"""Context Hub retrieval component."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import (
    Any,
    Mapping,
    Optional,
    Sequence,
)


from backend.services.compatibility_coverage import (
    COMPATIBILITY_COVERAGE_VERSION,
    bind_compatibility_coverage,
)

from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    _CONTEXT_AUTHORITY_TRUTH_CLASSES,
)

from backend.modules.context_hub.documents import (
    _active_generation_id,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.product_evidence_attestation import (
    product_evidence_generation_matches_completed_projection,
)

from backend.modules.context_hub.retrieval_filters import (
    _closed_context_filters,
    _finalize_context_retrieval_v3,
    _fts_match_query,
    _matching_identifiers,
    _rows_matching_required_identifiers,
    _search_identifiers,
    _search_query_terms,
    _search_score,
    _significant_search_terms,
)

from backend.modules.context_hub.runtime import (
    _sha256_text,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _compatibility_coverage_from_document(value: Any) -> dict[str, Any]:
    for line in str(value or "").splitlines():
        if not line.startswith("CompatibilityCoverageV1: "):
            continue
        try:
            decoded = json.loads(line.split(": ", 1)[1])
        except (json.JSONDecodeError, IndexError, TypeError):
            return {}
        if isinstance(decoded, dict) and decoded.get("schema_version") == COMPATIBILITY_COVERAGE_VERSION:
            return decoded
        return {}
    return {}


def _search_result_from_row(
    row: sqlite3.Row,
    *,
    active_id: str,
    query_terms: Sequence[str],
    score: float,
    strategy: str,
    reason: str,
) -> dict[str, Any]:
    try:
        refs = json.loads(str(row["source_refs_json"] or "[]"))
    except json.JSONDecodeError:
        refs = []
    reference = str(refs[0]) if isinstance(refs, list) and refs else str(row["relative_path"] or "")
    result = {
        "doc_id": row["doc_id"],
        "title": row["title"],
        "chunk_id": row["chunk_id"],
        "snippet": _search_snippet(str(row["content"] or ""), query_terms),
        "score": round(float(score), 6),
        "reference": reference,
        "truth_class": row["truth_class"],
        "sensitivity": row["sensitivity"],
        "source_version": row["source_version"],
        "source_hash": row["source_hash"],
        "content_hash": row["content_hash"],
        "generation_id": active_id,
        "version": row["source_version"],
        "hash": row["source_hash"],
        "generation": active_id,
        "type": row["kind"],
        "module": row["module"],
        "surface": row["surface"],
        "store_ref": row["store_ref"],
        "tags": [item for item in str(row["tags_text"] or "").splitlines() if item],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "selection_strategy": strategy,
        "selection_reason": reason,
    }
    raw_coverage = _compatibility_coverage_from_document(row["document_content"])
    coverage = bind_compatibility_coverage(
        raw_coverage,
        source_hash=str(row["source_hash"] or ""),
        generation_id=active_id,
        truth_class=str(row["truth_class"] or ""),
        doc_id=str(row["doc_id"] or ""),
    )
    if coverage:
        result["compatibility_coverage"] = coverage
    return result


def _diverse_search_results(candidates: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Deduplicate snippets, then take one result per document before extras."""

    strategy_priority = {
        "exact_identifier": 4,
        "bm25_strict": 3,
        "lexical_strict": 3,
        "bm25_relaxed": 2,
        "lexical_relaxed": 2,
        "lexical_browse": 1,
    }
    ordered = sorted(
        candidates,
        key=lambda item: (
            -strategy_priority.get(str(item.get("selection_strategy") or ""), 0),
            -float(item.get("score") or 0.0),
            str(item.get("doc_id") or ""),
            str(item.get("chunk_id") or ""),
        ),
    )
    by_document: dict[str, list[dict[str, Any]]] = {}
    seen_chunks: set[tuple[str, str]] = set()
    seen_snippets: set[str] = set()
    for item in ordered:
        doc_id = str(item.get("doc_id") or "")
        chunk_id = str(item.get("chunk_id") or "")
        chunk_key = (doc_id, chunk_id)
        snippet_key = _sha256_text(re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip().casefold())
        if chunk_key in seen_chunks or snippet_key in seen_snippets:
            continue
        seen_chunks.add(chunk_key)
        seen_snippets.add(snippet_key)
        by_document.setdefault(doc_id, []).append(item)

    results: list[dict[str, Any]] = []
    depth = 0
    while len(results) < limit:
        added = False
        for document_rows in by_document.values():
            if depth < len(document_rows):
                results.append(document_rows[depth])
                added = True
                if len(results) >= limit:
                    break
        if not added:
            break
        depth += 1
    return results


def _search_snippet(content: str, query_terms: Sequence[str], *, maximum: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", content).strip()
    if len(normalized) <= maximum:
        return normalized
    lower = normalized.lower()
    positions = [lower.find(term) for term in query_terms if lower.find(term) >= 0]
    start = max(0, min(positions) - maximum // 3) if positions else 0
    end = min(len(normalized), start + maximum)
    snippet = normalized[start:end].strip()
    return ("..." if start else "") + snippet + ("..." if end < len(normalized) else "")


def _normalized_search(
    query: object,
    filters: Optional[Mapping[str, Any]],
    limit: int,
    product_evidence_identity: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    raw_filters = _closed_context_filters(filters)
    safe_query = str(query or "").strip()[:500]
    request_surface = str(
        raw_filters.get("request_surface") or raw_filters.get("consumer_surface") or ""
    ).strip().casefold()
    maximum_limit = 8 if request_surface in {"whatsapp", "black_jhon_whatsapp"} else 12
    safe_limit = max(1, min(int(limit or maximum_limit), maximum_limit))
    module_filter = str(raw_filters.get("module") or raw_filters.get("domain") or "").strip().lower()[:100]
    kind_filter = str(raw_filters.get("source_type") or raw_filters.get("kind") or "").strip().lower()[:100]
    surface_filter = str(raw_filters.get("environment") or raw_filters.get("surface") or "").strip().lower()[:100]
    truth_class_filter = str(raw_filters.get("truth_class") or "").strip().casefold()[:100]
    authority_filter = str(raw_filters.get("authority") or "").strip().casefold()
    sensitivity_filter = str(raw_filters.get("sensitivity") or "").strip().casefold()[:80]
    validity_filter = str(raw_filters.get("validity") or "").strip().casefold()
    store_filter = str(raw_filters.get("store_ref") or "").strip().casefold()[:180]
    tags_value = raw_filters.get("tags") or []
    tags_filter = sorted(
        {str(value).strip().casefold()[:80] for value in tags_value if str(value).strip()}
    ) if isinstance(tags_value, list) else []
    valid_at_filter = str(raw_filters.get("valid_at") or "").strip()[:40]
    document_types_value = raw_filters.get("document_types") or []
    document_types = sorted(
        {str(value).strip().casefold()[:80] for value in document_types_value if str(value).strip()}
    ) if isinstance(document_types_value, list) else []
    ids_value = raw_filters.get("ids") or raw_filters.get("entity_ids") or []
    ids = sorted({str(value).strip() for value in ids_value if str(value).strip()}) if isinstance(ids_value, list) else []
    query_terms = _search_query_terms(safe_query)
    significant_terms = _significant_search_terms(query_terms)
    identifiers = _search_identifiers(safe_query, raw_filters)
    required_identifiers = _search_identifiers(
        "",
        {"sku": raw_filters.get("sku"), "mlb": raw_filters.get("mlb")},
    )
    identity_keys = ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id")
    raw_identity = dict(product_evidence_identity or {}) if isinstance(product_evidence_identity, Mapping) else {}
    evidence_identity = {
        key: str(raw_identity.get(key) or "").strip()[:180]
        for key in identity_keys
    } if all(key in raw_identity for key in identity_keys) else {}
    if evidence_identity and not all(evidence_identity[key] for key in identity_keys[:-1]):
        evidence_identity = {}
    return {
        "filters": raw_filters,
        "query": safe_query,
        "limit": safe_limit,
        "module": module_filter,
        "kind": kind_filter,
        "surface": surface_filter,
        "truth_class": truth_class_filter,
        "authority": authority_filter,
        "sensitivity": sensitivity_filter,
        "validity": validity_filter,
        "store": store_filter,
        "tags": tags_filter,
        "valid_at": valid_at_filter,
        "document_types": document_types,
        "ids": ids,
        "query_terms": query_terms,
        "significant_terms": significant_terms,
        "identifiers": identifiers,
        "required_identifiers": required_identifiers,
        "product_evidence_identity": evidence_identity,
        "strict_match": _fts_match_query(query_terms, "AND"),
        "relaxed_match": _fts_match_query(significant_terms, "OR"),
        "retrieval_limit": min(max(safe_limit * 8, 32), 96),
    }


def _product_evidence_generation_current(
    connection: sqlite3.Connection,
    active_id: str,
) -> bool:
    return product_evidence_generation_matches_completed_projection(
        connection,
        active_id,
    )


def _base_search_query(active_id: str, search: Mapping[str, Any]) -> tuple[list[str], list[Any], str]:
    clauses = ["c.generation_id=?", "g.status='active'"]
    parameters: list[Any] = [active_id]

    def equal(column: str, key: str) -> None:
        if search[key]:
            clauses.append(f"{column}=?")
            parameters.append(search[key])

    if search["module"]:
        clauses.append("instr(lower(d.module), ?) > 0")
        parameters.append(search["module"])
    equal("lower(d.kind)", "kind")
    equal("lower(d.surface)", "surface")
    equal("lower(d.truth_class)", "truth_class")
    if search["authority"]:
        values = sorted(_CONTEXT_AUTHORITY_TRUTH_CLASSES[search["authority"]])
        clauses.append("lower(d.truth_class) IN (" + ",".join("?" for _ in values) + ")")
        parameters.extend(values)
    equal("lower(d.sensitivity)", "sensitivity")
    if search["validity"]:
        values = (
            sorted(set().union(*(value for key, value in _CONTEXT_AUTHORITY_TRUTH_CLASSES.items() if key != "unverified")))
            if search["validity"] == "active_generation"
            else sorted(_CONTEXT_AUTHORITY_TRUTH_CLASSES["unverified"])
        )
        clauses.append("lower(d.truth_class) IN (" + ",".join("?" for _ in values) + ")")
        parameters.extend(values)
    for key, column in (("document_types", "lower(d.kind)"), ("ids", "d.doc_id")):
        values = search[key]
        if values:
            clauses.append(column + " IN (" + ",".join("?" for _ in values) + ")")
            parameters.extend(values)
    equal("lower(d.store_ref)", "store")
    for tag in search["tags"]:
        clauses.append("instr(char(10) || lower(d.tags_text) || char(10), char(10) || ? || char(10)) > 0")
        parameters.append(tag)
    evidence_identity = search.get("product_evidence_identity") or {}
    if not bool(search.get("product_evidence_current")) or not evidence_identity:
        clauses.append("lower(d.kind) != 'product_evidence_fact'")
    else:
        clauses.append(
            "(lower(d.kind) != 'product_evidence_fact' OR ("
            "d.store_ref=? AND d.seller_id=? AND d.site_id=? "
            "AND d.sku=? AND d.item_id=? AND d.variation_id=?))"
        )
        parameters.extend(evidence_identity[key] for key in (
            "store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id"
        ))
    evidence_valid_at = search["valid_at"] or _utc_now()
    clauses.append(
        "(lower(d.kind) != 'product_evidence_fact' OR ("
        "d.valid_from != '' AND d.valid_to != '' "
        "AND d.valid_from <= ? AND d.valid_to > ?))"
    )
    parameters.extend((evidence_valid_at, evidence_valid_at))
    if search["valid_at"]:
        clauses.extend((
            "(d.valid_from != '' OR d.valid_to != '')",
            "(d.valid_from = '' OR d.valid_from <= ?)",
            "(d.valid_to = '' OR d.valid_to >= ?)",
        ))
        parameters.extend((search["valid_at"], search["valid_at"]))
    sql = f"""
        SELECT d.doc_id, d.entity_id, d.relative_path, d.title, d.kind, d.module, d.surface,
            d.truth_class, d.sensitivity, d.source_version, d.source_hash, d.content_hash,
            d.source_refs_json, d.store_ref, d.tags_text, d.valid_from, d.valid_to,
            d.content AS document_content, c.chunk_id, c.content, 0.0 AS rank_score
        FROM context_hub_chunks AS c
        JOIN context_hub_documents AS d ON d.generation_id=c.generation_id AND d.doc_id=c.doc_id
        JOIN context_hub_generations AS g ON g.generation_id=c.generation_id
        WHERE {' AND '.join(clauses)} ORDER BY d.doc_id, c.ordinal
    """
    return clauses, parameters, sql


@dataclass
class _SearchExecution:
    engine: str = "fts5_bm25"
    stages: list[str] = field(default_factory=list)
    relaxation_used: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    base_rows: Optional[Sequence[sqlite3.Row]] = None


def _append_exact_candidates(
    state: _SearchExecution,
    connection: sqlite3.Connection,
    base_sql: str,
    parameters: Sequence[Any],
    search: Mapping[str, Any],
    active_id: str,
) -> None:
    if not search["identifiers"]:
        return
    state.stages.append("exact_identifier")
    state.base_rows = _rows_matching_required_identifiers(
        connection.execute(base_sql, parameters).fetchall(),
        search["required_identifiers"],
    )
    for row in state.base_rows:
        matched = _matching_identifiers(row, search["identifiers"])
        if matched:
            state.candidates.append(_search_result_from_row(
                row,
                active_id=active_id,
                query_terms=[item.casefold() for item in matched],
                score=10_000.0 + (100.0 * len(matched)),
                strategy="exact_identifier",
                reason="identifier_exact_match:" + ",".join(matched[:3]),
            ))


def _fts_rows(
    connection: sqlite3.Connection,
    match_query: str,
    clauses: Sequence[str],
    parameters: Sequence[Any],
    retrieval_limit: int,
) -> Sequence[sqlite3.Row]:
    fts_clauses = [clause.replace("c.", "f.") for clause in clauses]
    return connection.execute(
        f"""
        SELECT d.doc_id, d.entity_id, d.relative_path, d.title, d.kind, d.module, d.surface,
            d.truth_class, d.sensitivity, d.source_version, d.source_hash, d.content_hash,
            d.source_refs_json, d.store_ref, d.tags_text, d.valid_from, d.valid_to,
            d.content AS document_content, f.chunk_id, f.content,
            -bm25(context_hub_chunks_fts, 0.0, 0.0, 0.0, 5.0, 1.0) AS rank_score
        FROM context_hub_chunks_fts AS f
        JOIN context_hub_documents AS d ON d.generation_id=f.generation_id AND d.doc_id=f.doc_id
        JOIN context_hub_generations AS g ON g.generation_id=f.generation_id
        WHERE context_hub_chunks_fts MATCH ? AND {' AND '.join(fts_clauses)}
        ORDER BY bm25(context_hub_chunks_fts, 0.0, 0.0, 0.0, 5.0, 1.0), f.doc_id, f.chunk_id
        LIMIT ?
        """,
        [match_query, *parameters, retrieval_limit],
    ).fetchall()


def _append_ranked_rows(
    state: _SearchExecution,
    rows: Sequence[sqlite3.Row],
    *,
    active_id: str,
    query_terms: Sequence[str],
    strategy: str,
    reason: str,
) -> None:
    for row in rows:
        state.candidates.append(_search_result_from_row(
            row,
            active_id=active_id,
            query_terms=query_terms,
            score=float(row["rank_score"] or 0.0),
            strategy=strategy,
            reason=reason,
        ))


def _search_fts(
    state: _SearchExecution,
    connection: sqlite3.Connection,
    clauses: Sequence[str],
    parameters: Sequence[Any],
    search: Mapping[str, Any],
    active_id: str,
) -> None:
    if not search["strict_match"]:
        raise sqlite3.OperationalError("empty_fts_query")
    indexed = int(connection.execute(
        "SELECT COUNT(*) FROM context_hub_chunks_fts WHERE generation_id=?", (active_id,)
    ).fetchone()[0])
    chunks = int(connection.execute(
        "SELECT COUNT(*) FROM context_hub_chunks WHERE generation_id=?", (active_id,)
    ).fetchone()[0])
    if chunks and not indexed:
        raise sqlite3.OperationalError("generation_not_indexed")
    state.stages.append("bm25_strict")
    rows = _fts_rows(connection, search["strict_match"], clauses, parameters, search["retrieval_limit"])
    rows = _rows_matching_required_identifiers(rows, search["required_identifiers"])
    _append_ranked_rows(
        state, rows, active_id=active_id, query_terms=search["query_terms"],
        strategy="bm25_strict", reason="all_query_terms_matched",
    )
    if state.candidates or not search["relaxed_match"]:
        return
    state.stages.append("bm25_relaxed")
    state.relaxation_used = True
    rows = _fts_rows(connection, search["relaxed_match"], clauses, parameters, search["retrieval_limit"])
    rows = _rows_matching_required_identifiers(rows, search["required_identifiers"])
    _append_ranked_rows(
        state, rows, active_id=active_id, query_terms=search["significant_terms"],
        strategy="bm25_relaxed", reason="significant_terms_after_zero_hit",
    )


def _lexical_result(
    row: sqlite3.Row,
    active_id: str,
    terms: Sequence[str],
    strategy: str,
    reason: str,
) -> dict[str, Any]:
    score = _search_score(terms, str(row["title"] or ""), str(row["content"] or ""), str(row["doc_id"] or ""))
    return _search_result_from_row(
        row, active_id=active_id, query_terms=terms, score=score,
        strategy=strategy, reason=reason,
    )


def _search_lexical(
    state: _SearchExecution,
    connection: sqlite3.Connection,
    base_sql: str,
    parameters: Sequence[Any],
    search: Mapping[str, Any],
    active_id: str,
) -> None:
    state.engine = "lexical_fallback"
    if state.base_rows is None:
        state.base_rows = _rows_matching_required_identifiers(
            connection.execute(base_sql, parameters).fetchall(), search["required_identifiers"]
        )
    state.candidates = [item for item in state.candidates if item.get("selection_strategy") == "exact_identifier"]
    if search["query_terms"]:
        state.stages = [stage for stage in state.stages if not stage.startswith("bm25_")]
        state.stages.append("lexical_strict")
        for row in state.base_rows:
            searchable = "\n".join((str(row["title"] or ""), str(row["content"] or ""), str(row["doc_id"] or ""))).casefold()
            if all(term in searchable for term in search["query_terms"]):
                state.candidates.append(_lexical_result(
                    row, active_id, search["query_terms"], "lexical_strict",
                    "all_query_terms_matched_without_fts5",
                ))
        if not state.candidates and search["significant_terms"]:
            state.stages.append("lexical_relaxed")
            state.relaxation_used = True
            for row in state.base_rows:
                searchable = "\n".join((str(row["title"] or ""), str(row["content"] or ""), str(row["doc_id"] or ""))).casefold()
                if any(term in searchable for term in search["significant_terms"]):
                    state.candidates.append(_lexical_result(
                        row, active_id, search["significant_terms"], "lexical_relaxed",
                        "significant_terms_after_zero_hit_without_fts5",
                    ))
    elif not search["identifiers"]:
        state.stages.append("lexical_browse")
        for row in state.base_rows[: search["retrieval_limit"]]:
            state.candidates.append(_search_result_from_row(
                row, active_id=active_id, query_terms=(), score=1.0,
                strategy="lexical_browse", reason="empty_query_filtered_browse",
            ))


def _empty_search_result(search: Mapping[str, Any]) -> dict[str, Any]:
    return _finalize_context_retrieval_v3({
        "success": True, "query": search["query"], "generation_id": None,
        "generation": None, "results": [], "count": 0,
        "search_engine": "fts5_bm25", "search_strategy": "none",
        "search_stages": [], "relaxation_used": False, "embeddings_enabled": False,
    }, filters=search["filters"])


def search_context(
    client_id: object,
    query: object,
    filters: Optional[Mapping[str, Any]] = None,
    limit: int = 12,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
    _product_evidence_identity: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Search an active generation by exact ID, strict BM25 and bounded relaxation."""

    search = _normalized_search(query, filters, limit, _product_evidence_identity)
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    state = _SearchExecution()
    with _connect(paths) as connection:
        connection.execute("BEGIN")
        active_id = _active_generation_id(connection)
        if not active_id:
            connection.commit()
            return _empty_search_result(search)
        search = dict(search)
        search["product_evidence_current"] = _product_evidence_generation_current(
            connection,
            active_id,
        )
        generation = connection.execute(
            "SELECT source_version FROM context_hub_generations WHERE generation_id=? AND status='active'",
            (active_id,),
        ).fetchone()
        clauses, parameters, base_sql = _base_search_query(active_id, search)
        _append_exact_candidates(state, connection, base_sql, parameters, search, active_id)
        try:
            _search_fts(state, connection, clauses, parameters, search, active_id)
        except sqlite3.OperationalError:
            _search_lexical(state, connection, base_sql, parameters, search, active_id)
        connection.commit()
    results = _diverse_search_results(state.candidates, search["limit"])
    result_strategies = list(
        dict.fromkeys(str(item.get("selection_strategy") or "") for item in results if item.get("selection_strategy"))
    )
    return _finalize_context_retrieval_v3({
        "success": True,
        "query": search["query"],
        "generation_id": active_id,
        "generation": active_id,
        "source_version": str(generation["source_version"] or "") if generation else "",
        "results": results,
        "count": len(results),
        "search_engine": state.engine,
        "search_strategy": "+".join(result_strategies) if result_strategies else "none",
        "search_stages": state.stages,
        "relaxation_used": state.relaxation_used,
        "embeddings_enabled": False,
    }, filters=search["filters"])
