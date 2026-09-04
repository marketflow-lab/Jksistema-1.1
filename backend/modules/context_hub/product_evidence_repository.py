"""Transactional repository for structured, tenant-scoped product evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.locking import (
    _exclusive_product_evidence_file_lock,
    _product_evidence_thread_lock,
)
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence_activation import (
    _batch_identity,
    _recalculate_identity_field,
    _require_collecting_batch,
    _source_supports_policy,
)
from backend.modules.context_hub.product_evidence_model import (
    PRODUCT_EVIDENCE_POLICY,
    PRODUCT_EVIDENCE_SCOPES,
    _SOURCE_AUTHORITIES,
    _SOURCE_TTL,
    _canonicalize_url,
    _iso,
    _normalize_field_name,
    _normalize_hash,
    _normalize_identifier,
    _normalize_opaque_id,
    _normalize_source_type,
    _normalize_text,
    _parse_timestamp,
    _reject_sensitive,
    _source_fingerprint,
    normalize_product_evidence_value,
)
from backend.modules.context_hub.runtime import _new_id, _runtime_config
from backend.modules.context_hub.storage import _connect


def _normalized_identity(
    store_ref: object,
    seller_id: object,
    site_id: object,
    sku: object,
    item_id: object,
    variation_id: object,
) -> tuple[str, str, str, str, str, str]:
    return (
        _normalize_identifier(store_ref, label="Loja"),
        _normalize_identifier(seller_id, label="Seller"),
        _normalize_identifier(site_id, label="Site"),
        _normalize_identifier(sku, label="SKU"),
        _normalize_identifier(item_id, label="Item", required=False),
        _normalize_identifier(variation_id, label="Variacao", required=False),
    )


def create_product_evidence_batch(
    client_id: object,
    *,
    store_ref: object,
    seller_id: object,
    site_id: object,
    sku: object,
    item_id: object = "",
    variation_id: object = "",
    started_at: Optional[object] = None,
    info_root: Optional[object] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    identity = _normalized_identity(
        store_ref, seller_id, site_id, sku, item_id, variation_id
    )
    batch_id = _new_id()
    started = _iso(_parse_timestamp(started_at))
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO product_evidence_batches(
                    batch_id, store_ref, seller_id, site_id, sku, item_id, variation_id,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'collecting', ?)
                """,
                (batch_id, *identity, started),
            )
            connection.commit()
    return {"batch_id": batch_id, "status": "collecting", "policy": PRODUCT_EVIDENCE_POLICY}


def _find_duplicate_source(
    connection: sqlite3.Connection,
    batch_id: str,
    canonical_url: str,
    content_hash: str,
    fingerprint: str,
) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM product_evidence_sources
         WHERE batch_id=? AND (
             canonical_url=? OR content_hash=?
             OR COALESCE(NULLIF(copy_fingerprint, ''), content_hash)=?
         )
         ORDER BY CASE WHEN canonical_url=? THEN 0
                       WHEN content_hash=? THEN 1 ELSE 2 END
         LIMIT 1
        """,
        (batch_id, canonical_url, content_hash, fingerprint, canonical_url, content_hash),
    ).fetchone()


def add_product_evidence_source(
    client_id: object,
    batch_id: object,
    *,
    url: object,
    source_type: object,
    content_hash: object,
    copy_fingerprint: Optional[object] = None,
    origin_key: object = "",
    section_ref: object = "",
    collected_at: Optional[object] = None,
    info_root: Optional[object] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    normalized_batch_id = _normalize_opaque_id(batch_id, label="Lote")
    normalized_url, domain = _canonicalize_url(url)
    normalized_type = _normalize_source_type(source_type)
    normalized_hash = _normalize_hash(content_hash, label="Hash de conteudo")
    normalized_fingerprint = _normalize_hash(
        copy_fingerprint, label="Fingerprint de copia", required=False
    )
    effective_fingerprint = normalized_fingerprint or normalized_hash
    normalized_origin = _normalize_identifier(
        origin_key or domain, label="Origem", maximum=160
    ).casefold()
    normalized_section = _normalize_text(
        section_ref, label="Secao", maximum=160, required=False
    )
    _reject_sensitive(normalized_section, label="Secao")
    collected = _parse_timestamp(collected_at)
    valid_until = collected + _SOURCE_TTL[normalized_type]
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_collecting_batch(connection, normalized_batch_id)
            existing = _find_duplicate_source(
                connection,
                normalized_batch_id,
                normalized_url,
                normalized_hash,
                effective_fingerprint,
            )
            if existing is not None:
                if (
                    str(existing["canonical_url"]) == normalized_url
                    and str(existing["content_hash"]) != normalized_hash
                ):
                    raise ContextHubValidationError(
                        "A mesma URL trouxe conteudos diferentes no mesmo lote."
                    )
                connection.commit()
                return {
                    "source_id": str(existing["source_id"]),
                    "canonical_url": str(existing["canonical_url"]),
                    "domain": str(existing["domain"]),
                    "authority": str(existing["authority"]),
                    "content_hash": str(existing["content_hash"]),
                    "copy_fingerprint": str(existing["copy_fingerprint"] or ""),
                    "deduplicated": True,
                }
            source_id = _new_id()
            authority = _SOURCE_AUTHORITIES[normalized_type]
            connection.execute(
                """
                INSERT INTO product_evidence_sources(
                    source_id, batch_id, canonical_url, domain, source_type, authority,
                    origin_key, content_hash, copy_fingerprint, section_ref,
                    collected_at, valid_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, normalized_batch_id, normalized_url, domain,
                    normalized_type, authority, normalized_origin, normalized_hash,
                    normalized_fingerprint, normalized_section, _iso(collected),
                    _iso(valid_until),
                ),
            )
            connection.commit()
    return {
        "source_id": source_id,
        "canonical_url": normalized_url,
        "domain": domain,
        "authority": authority,
        "content_hash": normalized_hash,
        "copy_fingerprint": normalized_fingerprint,
        "deduplicated": False,
    }


def _normalized_source_ids(source_ids: Iterable[object]) -> tuple[str, ...]:
    if isinstance(source_ids, (str, bytes)):
        raise ContextHubValidationError("Fontes devem ser informadas como uma colecao de IDs.")
    normalized = tuple(
        dict.fromkeys(
            _normalize_opaque_id(source_id, label="Fonte") for source_id in source_ids
        )
    )
    if not normalized:
        raise ContextHubValidationError("A afirmacao deve apontar pelo menos uma fonte.")
    return normalized


def _ensure_batch_sources(
    connection: sqlite3.Connection,
    batch_id: str,
    source_ids: tuple[str, ...],
) -> None:
    placeholders = ",".join("?" for _ in source_ids)
    sources = connection.execute(
        f"SELECT source_id, batch_id FROM product_evidence_sources "
        f"WHERE source_id IN ({placeholders})",
        source_ids,
    ).fetchall()
    if len(sources) != len(source_ids) or any(
        str(source["batch_id"]) != batch_id for source in sources
    ):
        raise ContextHubValidationError("Fonte ausente ou pertencente a outro lote.")


def _upsert_claim(
    connection: sqlite3.Connection,
    batch_id: str,
    field_name: str,
    scope: str,
    normalized: Any,
    evaluated_at: datetime,
) -> str:
    row = connection.execute(
        """
        SELECT claim_id FROM product_evidence_claims
         WHERE batch_id=? AND field_name=? AND scope=? AND normalized_key=? AND unit=?
        """,
        (batch_id, field_name, scope, normalized.key, normalized.unit),
    ).fetchone()
    if row is not None:
        return str(row["claim_id"])
    claim_id = _new_id()
    now = _iso(evaluated_at)
    connection.execute(
        """
        INSERT INTO product_evidence_claims(
            claim_id, batch_id, field_name, scope, normalized_value,
            normalized_key, unit, state, valid_from, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
        """,
        (
            claim_id, batch_id, field_name, scope, normalized.value,
            normalized.key, normalized.unit, now, now, now,
        ),
    )
    return claim_id


def add_product_evidence_claim(
    client_id: object,
    batch_id: object,
    *,
    field_name: object,
    scope: object,
    value: object,
    source_ids: Iterable[object],
    unit: Optional[object] = None,
    as_of: Optional[object] = None,
    info_root: Optional[object] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    normalized_batch_id = _normalize_opaque_id(batch_id, label="Lote")
    normalized_field = _normalize_field_name(field_name)
    normalized_scope = str(scope or "").strip().lower()
    if normalized_scope not in PRODUCT_EVIDENCE_SCOPES:
        raise ContextHubValidationError("Escopo invalido para a evidencia de produto.")
    normalized = normalize_product_evidence_value(value, unit=unit)
    normalized_sources = _normalized_source_ids(source_ids)
    evaluated_at = _parse_timestamp(as_of)
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            batch = _require_collecting_batch(connection, normalized_batch_id)
            _ensure_batch_sources(connection, normalized_batch_id, normalized_sources)
            claim_id = _upsert_claim(
                connection, normalized_batch_id, normalized_field,
                normalized_scope, normalized, evaluated_at,
            )
            connection.executemany(
                "INSERT OR IGNORE INTO product_evidence_claim_sources(claim_id, source_id) "
                "VALUES (?, ?)",
                ((claim_id, source_id) for source_id in normalized_sources),
            )
            _recalculate_identity_field(
                connection, _batch_identity(batch), normalized_field,
                normalized_scope, as_of=evaluated_at,
            )
            result = connection.execute(
                "SELECT * FROM product_evidence_claims WHERE claim_id=?", (claim_id,)
            ).fetchone()
            connection.commit()
    return {
        "claim_id": claim_id,
        "field_name": normalized_field,
        "scope": normalized_scope,
        "value": normalized.value,
        "unit": normalized.unit,
        "state": str(result["state"]),
        "activation_policy": str(result["activation_policy"]),
        "conflict_group": str(result["conflict_group"]),
        "valid_until": result["valid_until"],
    }


_STOP_REASONS = frozenset(
    {"coverage_complete", "no_new_facts", "page_limit", "deadline", "provider_unavailable", "failed"}
)


def _normalize_completion(
    stop_reason: object,
    pages_discovered: int,
    pages_read: int,
    fields_missing: int,
    finished_at: Optional[object],
) -> tuple[str, tuple[int, int, int], datetime]:
    reason = str(stop_reason or "").strip().lower()
    if reason not in _STOP_REASONS:
        raise ContextHubValidationError("Motivo de encerramento invalido.")
    metrics = (int(pages_discovered), int(pages_read), int(fields_missing))
    if any(metric < 0 for metric in metrics) or metrics[1] > metrics[0]:
        raise ContextHubValidationError("Metricas invalidas para o lote de evidencias.")
    return reason, metrics, _parse_timestamp(finished_at)


def _mark_batch_finished(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    failed: bool,
    finished: datetime,
) -> tuple[str, tuple[str, str, str, str, str, str]]:
    batch = _require_collecting_batch(connection, batch_id)
    identity = _batch_identity(batch)
    fields = connection.execute(
        "SELECT DISTINCT field_name, scope FROM product_evidence_claims WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    status = "failed" if failed else "completed"
    connection.execute(
        "UPDATE product_evidence_batches SET status=? WHERE batch_id=?",
        (status, batch_id),
    )
    if failed:
        connection.execute(
            """
            UPDATE product_evidence_claims
               SET state='candidate', activation_policy='', conflict_group='',
                   valid_until=NULL, updated_at=?
             WHERE batch_id=? AND state!='rejected'
            """,
            (_iso(finished), batch_id),
        )
    for field in fields:
        _recalculate_identity_field(
            connection, identity, str(field["field_name"]), str(field["scope"]),
            as_of=finished,
        )
    return status, identity


def _required_field_covered(required: str, active_fields: set[str]) -> bool:
    if required == "compatibility":
        return any(value.startswith("compatibility.") for value in active_fields)
    if required == "reference.oem_code":
        return any(value.startswith("reference.oem_code.") for value in active_fields)
    if required == "electrical.voltage":
        return bool(
            active_fields
            & {"electrical.voltage", "electrical.input_voltage", "electrical.output_voltage"}
        )
    if required in {"interface.connector", "kit.content"}:
        return any(value.startswith(required + ".") for value in active_fields)
    return required in active_fields


def _reconcile_expected_fields(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    *,
    expected_fields: Optional[Sequence[object]],
    requested_coverage: bool,
    failed: bool,
    reason: str,
    fallback_missing: int,
    finished: datetime,
) -> tuple[int, bool, str]:
    if expected_fields is None:
        return fallback_missing, bool(requested_coverage) and not failed, reason
    normalized_expected = {
        str(field or "").strip().lower()
        for field in expected_fields
        if str(field or "").strip()
    }
    rows = connection.execute(
        """
        SELECT DISTINCT c.field_name
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
         WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
           AND b.item_id=? AND b.variation_id=? AND b.status='completed'
           AND c.state='verified' AND c.valid_until>?
        """,
        (*identity, _iso(finished)),
    ).fetchall()
    active_fields = {str(row["field_name"] or "") for row in rows}
    missing = sum(
        1 for required in normalized_expected
        if not _required_field_covered(required, active_fields)
    )
    effective = bool(normalized_expected and not missing and requested_coverage and not failed)
    if effective:
        reason = "coverage_complete"
    elif reason == "coverage_complete":
        reason = "no_new_facts"
    return missing, effective, reason


def _persist_batch_metrics(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    status: str,
    coverage_complete: bool,
    reason: str,
    metrics: tuple[int, int, int],
    fields_missing: int,
    finished: datetime,
) -> tuple[int, int]:
    counts = connection.execute(
        """
        SELECT
            COUNT(DISTINCT CASE WHEN state='verified' THEN field_name || ':' || scope END),
            COUNT(DISTINCT CASE WHEN state='conflict' THEN conflict_group END)
          FROM product_evidence_claims WHERE batch_id=?
        """,
        (batch_id,),
    ).fetchone()
    connection.execute(
        """
        UPDATE product_evidence_batches
           SET status=?, coverage_complete=?, stop_reason=?, pages_discovered=?,
               pages_read=?, fields_confirmed=?, fields_missing=?, conflicts_count=?, finished_at=?
         WHERE batch_id=?
        """,
        (
            status, int(coverage_complete), reason, metrics[0], metrics[1],
            int(counts[0]), fields_missing, int(counts[1]), _iso(finished), batch_id,
        ),
    )
    return int(counts[0]), int(counts[1])


def complete_product_evidence_batch(
    client_id: object,
    batch_id: object,
    *,
    coverage_complete: bool,
    stop_reason: object,
    pages_discovered: int = 0,
    pages_read: int = 0,
    fields_missing: int = 0,
    expected_fields: Optional[Sequence[object]] = None,
    failed: bool = False,
    finished_at: Optional[object] = None,
    info_root: Optional[object] = None,
) -> dict[str, Any]:
    from backend.modules.context_hub.product_evidence_sync import (
        _mark_product_evidence_sync_pending,
    )

    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    normalized_batch_id = _normalize_opaque_id(batch_id, label="Lote")
    reason, metrics, finished = _normalize_completion(
        stop_reason, pages_discovered, pages_read, fields_missing, finished_at
    )
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            status, identity = _mark_batch_finished(
                connection, normalized_batch_id, failed=failed, finished=finished
            )
            effective_missing, effective_coverage, reason = _reconcile_expected_fields(
                connection, identity, expected_fields=expected_fields,
                requested_coverage=coverage_complete, failed=failed, reason=reason,
                fallback_missing=metrics[2], finished=finished,
            )
            confirmed, conflicts = _persist_batch_metrics(
                connection, normalized_batch_id, status=status,
                coverage_complete=effective_coverage, reason=reason, metrics=metrics,
                fields_missing=effective_missing, finished=finished,
            )
            _mark_product_evidence_sync_pending(connection)
            connection.commit()
    try:
        from backend.modules.context_hub.product_evidence_sync import (
            schedule_product_evidence_sync,
        )

        schedule_product_evidence_sync(paths.client_id, info_root=config.info_root)
    except Exception:
        # The durable outbox was committed with the batch.  A scheduler failure
        # must neither roll it back nor expose source/exception text.
        pass
    result = {
        "batch_id": normalized_batch_id,
        "status": status,
        "coverage_complete": effective_coverage,
        "fields_confirmed": confirmed,
        "conflicts_count": conflicts,
    }
    if expected_fields is not None:
        result.update({"fields_missing": effective_missing, "stop_reason": reason})
    return result


def _recalculate_identity(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    evaluated_at: datetime,
) -> None:
    fields = connection.execute(
        """
        SELECT DISTINCT c.field_name, c.scope
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
         WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
           AND b.item_id=? AND b.variation_id=? AND b.status='completed'
        """,
        identity,
    ).fetchall()
    for field in fields:
        _recalculate_identity_field(
            connection, identity, str(field["field_name"]), str(field["scope"]),
            as_of=evaluated_at,
        )


def _verified_claims(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    evaluated_at: datetime,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT c.*
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
         WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
           AND b.item_id=? AND b.variation_id=? AND b.status='completed'
           AND c.state='verified' AND c.valid_until>?
         ORDER BY c.field_name, c.scope, c.updated_at DESC
        """,
        (*identity, _iso(evaluated_at)),
    ).fetchall()


def _all_research_claims(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
) -> list[sqlite3.Row]:
    """Return the newest compiled value per exact signature, bounded for prompts.

    State and authority remain attached as provenance for the answer agent, but
    they are not an application-level permission gate.  Exact tenant/product
    scoping is still enforced by the repository identity.
    """

    return connection.execute(
        """
        WITH ranked AS (
            SELECT c.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.field_name, c.scope, c.normalized_key, c.unit, c.state
                       ORDER BY c.updated_at DESC, c.claim_id DESC
                   ) AS research_rank
              FROM product_evidence_claims c
              JOIN product_evidence_batches b ON b.batch_id=c.batch_id
             WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
               AND b.item_id=? AND b.variation_id=? AND b.status='completed'
        )
        SELECT * FROM ranked
         WHERE research_rank=1
         ORDER BY updated_at DESC, field_name, scope, claim_id
         LIMIT 160
        """,
        identity,
    ).fetchall()


def _claim_sources_by_signatures(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    signatures: Sequence[tuple[str, str, str, str, str]],
) -> dict[tuple[str, str, str, str, str], list[sqlite3.Row]]:
    selected = list(dict.fromkeys(signatures))[:160]
    if not selected:
        return {}
    placeholders = ",".join("(?,?,?,?,?)" for _value in selected)
    query = f"""
        WITH selected(field_name, scope, normalized_key, unit, state) AS (
            VALUES {placeholders}
        )
        SELECT linked.field_name, linked.scope, linked.normalized_key, linked.unit, linked.state,
               s.source_type, s.authority, s.canonical_url, s.domain,
               s.section_ref, s.collected_at, s.valid_until,
               s.content_hash, s.copy_fingerprint
          FROM product_evidence_sources s
          JOIN product_evidence_claim_sources cs ON cs.source_id=s.source_id
          JOIN product_evidence_claims linked ON linked.claim_id=cs.claim_id
          JOIN product_evidence_batches b ON b.batch_id=linked.batch_id
          JOIN selected ON selected.field_name=linked.field_name
                       AND selected.scope=linked.scope
                       AND selected.normalized_key=linked.normalized_key
                       AND selected.unit=linked.unit AND selected.state=linked.state
         WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
           AND b.item_id=? AND b.variation_id=? AND b.status='completed'
         ORDER BY linked.field_name, linked.scope, linked.normalized_key, linked.unit, linked.state,
                  s.collected_at DESC, s.authority, s.domain, s.canonical_url
        """
    parameters = tuple(part for signature in selected for part in signature) + identity
    rows = connection.execute(query, parameters).fetchall()
    grouped: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = {
        signature: [] for signature in selected
    }
    for row in rows:
        signature = tuple(str(row[field]) for field in (
            "field_name", "scope", "normalized_key", "unit", "state",
        ))
        grouped.setdefault(signature, []).append(row)
    return grouped


def _claim_sources(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    key: tuple[str, str, str, str],
    evaluated_at: datetime,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT s.source_type, s.authority, s.canonical_url, s.domain,
               s.section_ref, s.collected_at, s.valid_until,
               s.content_hash, s.copy_fingerprint
          FROM product_evidence_sources s
          JOIN product_evidence_claim_sources cs ON cs.source_id=s.source_id
          JOIN product_evidence_claims linked ON linked.claim_id=cs.claim_id
          JOIN product_evidence_batches linked_batch ON linked_batch.batch_id=linked.batch_id
         WHERE linked_batch.store_ref=? AND linked_batch.seller_id=?
           AND linked_batch.site_id=? AND linked_batch.sku=?
           AND linked_batch.item_id=? AND linked_batch.variation_id=?
           AND linked_batch.status='completed'
           AND linked.field_name=? AND linked.scope=?
           AND linked.normalized_key=? AND linked.unit=? AND s.valid_until>?
         ORDER BY s.authority, s.domain, s.canonical_url
        """,
        (*identity, key[0], key[1], key[2], key[3], _iso(evaluated_at)),
    ).fetchall()


def _unique_policy_sources(
    sources: Sequence[sqlite3.Row],
    activation_policy: str,
) -> list[sqlite3.Row]:
    unique: list[sqlite3.Row] = []
    seen_fingerprints: set[str] = set()
    for source in sources:
        if not _source_supports_policy(str(source["source_type"]), activation_policy):
            continue
        fingerprint = _source_fingerprint(source)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        unique.append(source)
    return unique


def _render_verified_claim(
    claim: sqlite3.Row,
    key: tuple[str, str, str, str],
    sources: Sequence[sqlite3.Row],
) -> dict[str, Any]:
    return {
        "field_name": key[0],
        "scope": key[1],
        "value": str(claim["normalized_value"]),
        "unit": key[3],
        "activation_policy": str(claim["activation_policy"]),
        "valid_until": str(claim["valid_until"]),
        "sources": [
            {
                "source_type": str(source["source_type"]),
                "authority": str(source["authority"]),
                "url": str(source["canonical_url"]),
                "domain": str(source["domain"]),
                "section_ref": str(source["section_ref"]),
                "collected_at": str(source["collected_at"]),
                "valid_until": str(source["valid_until"]),
            }
            for source in sources
        ],
    }


def list_verified_product_evidence(
    client_id: object,
    *,
    store_ref: object,
    seller_id: object,
    site_id: object,
    sku: object,
    item_id: object = "",
    variation_id: object = "",
    as_of: Optional[object] = None,
    info_root: Optional[object] = None,
) -> list[dict[str, Any]]:
    config = _runtime_config(info_root=info_root)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    identity = _normalized_identity(
        store_ref, seller_id, site_id, sku, item_id, variation_id
    )
    evaluated_at = _parse_timestamp(as_of)
    with _product_evidence_thread_lock(paths), _exclusive_product_evidence_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _recalculate_identity(connection, identity, evaluated_at)
            claims = _verified_claims(connection, identity, evaluated_at)
            results: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str, str]] = set()
            for claim in claims:
                key = (
                    str(claim["field_name"]), str(claim["scope"]),
                    str(claim["normalized_key"]), str(claim["unit"]),
                )
                if key in seen:
                    continue
                seen.add(key)
                sources = _claim_sources(connection, identity, key, evaluated_at)
                unique_sources = _unique_policy_sources(
                    sources, str(claim["activation_policy"])
                )
                results.append(_render_verified_claim(claim, key, unique_sources))
            connection.commit()
    return results


__all__ = [
    "add_product_evidence_claim",
    "add_product_evidence_source",
    "complete_product_evidence_batch",
    "create_product_evidence_batch",
    "list_verified_product_evidence",
]
