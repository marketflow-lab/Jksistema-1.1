"""Context Hub retrieval filters component."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from typing import (
    Any,
    Mapping,
    Optional,
    Sequence,
)



from backend.modules.context_hub.contracts import (
    CONTEXT_RETRIEVAL_AUTHORITY_POLICY,
    CONTEXT_RETRIEVAL_FILTER_KEYS,
    CONTEXT_RETRIEVAL_V3,
    ContextHubValidationError,
    _CONTEXT_AUTHORITY_TRUTH_CLASSES,
)


def _search_score(query_terms: Sequence[str], title: str, content: str, doc_id: str) -> float:
    title_lower = title.lower()
    content_lower = content.lower()
    id_lower = doc_id.lower()
    if not query_terms:
        return 1.0
    score = 0.0
    for term in query_terms:
        score += title_lower.count(term) * 5.0
        score += id_lower.count(term) * 4.0
        score += min(content_lower.count(term), 20) * 1.0
    return score


_SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "ao",
        "aos",
        "as",
        "com",
        "como",
        "da",
        "das",
        "de",
        "do",
        "dos",
        "e",
        "em",
        "entre",
        "esse",
        "esta",
        "este",
        "isso",
        "na",
        "nas",
        "no",
        "nos",
        "o",
        "os",
        "ou",
        "para",
        "por",
        "que",
        "qual",
        "sem",
        "um",
        "uma",
    }
)


def _search_query_terms(value: object) -> list[str]:
    terms = [
        term.casefold()
        for term in re.findall(r"[\w:/.-]+", str(value or ""), re.UNICODE)
        if len(term) >= 2
    ]
    return list(dict.fromkeys(terms))[:20]


def _significant_search_terms(query_terms: Sequence[str]) -> list[str]:
    significant = [
        term
        for term in query_terms
        if term not in _SEARCH_STOPWORDS
        and (len(term) >= 3 or any(character.isdigit() for character in term))
    ]
    return significant[:12]


def _search_identifiers(query: str, filters: Mapping[str, Any]) -> list[str]:
    """Extract stable IDs, MLBs and explicit/identifier-like SKUs in order."""

    candidates: list[str] = []
    ids_value = filters.get("ids") or filters.get("entity_ids") or []
    if isinstance(ids_value, list):
        candidates.extend(str(value or "").strip() for value in ids_value)
    candidates.extend(
        str(filters.get(key) or "").strip()
        for key in ("sku", "mlb", "item_id")
    )
    candidates.extend(re.findall(r"\bjk:[A-Za-z0-9:_./-]{1,236}", query, re.IGNORECASE))
    candidates.extend(
        "MLB" + match
        for match in re.findall(r"\bMLB[\s:#-]*(\d{6,})\b", query, re.IGNORECASE)
    )
    candidates.extend(
        match
        for match in re.findall(
            r"\bSKU[\s:#-]+([A-Za-z0-9][A-Za-z0-9._/-]{0,99})\b",
            query,
            re.IGNORECASE,
        )
    )
    candidates.extend(
        term
        for term in _search_query_terms(query)
        if len(term) >= 4
        and any(character.isdigit() for character in term)
        and any(character.isalpha() for character in term)
    )
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value:
            continue
        mlb = re.fullmatch(r"MLB[\s:#-]*(\d{6,})", value, re.IGNORECASE)
        value = "MLB" + mlb.group(1) if mlb else value
        marker = value.casefold()
        if marker not in seen:
            seen.add(marker)
            normalized.append(value)
    return normalized[:20]


def _matching_identifiers(
    row: Mapping[str, Any],
    identifiers: Sequence[str],
    *,
    include_document_content: bool = False,
) -> list[str]:
    searchable_keys = ["doc_id", "title", "content"]
    if include_document_content:
        searchable_keys.extend(("entity_id", "document_content"))
    searchable = "\n".join(
        str(row[key] or "")
        for key in searchable_keys
        if key in row.keys()
    ).casefold()
    matched: list[str] = []
    for identifier in identifiers:
        needle = identifier.casefold()
        if not needle:
            continue
        pattern = rf"(?<![\w]){re.escape(needle)}(?![\w])"
        if re.search(pattern, searchable, re.UNICODE):
            matched.append(identifier)
    return matched


def _rows_matching_required_identifiers(
    rows: Sequence[sqlite3.Row],
    identifiers: Sequence[str],
) -> list[sqlite3.Row]:
    if not identifiers:
        return list(rows)
    required = {str(item or "").casefold() for item in identifiers if str(item or "").strip()}
    return [
        row
        for row in rows
        if required.issubset({
            item.casefold()
            for item in _matching_identifiers(
                row,
                identifiers,
                include_document_content=True,
            )
        })
    ]


def _fts_match_query(terms: Sequence[str], operator: str) -> str:
    safe_operator = " OR " if operator == "OR" else " AND "
    return safe_operator.join('"' + term.replace('"', '""') + '"' for term in terms)


def _closed_context_filters(filters: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    raw = dict(filters or {}) if isinstance(filters, Mapping) else {}
    unknown = sorted(str(key) for key in raw if str(key) not in CONTEXT_RETRIEVAL_FILTER_KEYS)
    if unknown:
        raise ContextHubValidationError("Filtro de busca do Context Hub nao permitido: " + ", ".join(unknown[:5]))
    authority = str(raw.get("authority") or "").strip().casefold()
    if authority and authority not in _CONTEXT_AUTHORITY_TRUTH_CLASSES:
        raise ContextHubValidationError("Autoridade de busca do Context Hub invalida.")
    validity = str(raw.get("validity") or "").strip().casefold()
    if validity and validity not in {"active_generation", "unverified"}:
        raise ContextHubValidationError("Validade de busca do Context Hub invalida.")
    valid_at = str(raw.get("valid_at") or "").strip()
    if valid_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?", valid_at):
        raise ContextHubValidationError("Data de validade do Context Hub invalida.")
    for list_key in ("ids", "entity_ids", "document_types", "tags"):
        if list_key in raw and not isinstance(raw.get(list_key), list):
            raise ContextHubValidationError(f"Filtro {list_key} do Context Hub deve ser uma lista.")
    return raw


def _context_authority(truth_class: Any) -> str:
    normalized = str(truth_class or "").strip().casefold()
    for authority, truth_classes in _CONTEXT_AUTHORITY_TRUTH_CLASSES.items():
        if normalized in truth_classes:
            return authority
    return "unverified"


def _context_validity(truth_class: Any) -> str:
    return "unverified" if _context_authority(truth_class) == "unverified" else "active_generation"


def _context_citation_id(row: Mapping[str, Any]) -> str:
    material = "|".join(
        str(row.get(key) or "")
        for key in ("generation_id", "doc_id", "chunk_id", "source_hash")
    )
    return "ctx-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _conflict_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _conflict_claim(value: Any) -> tuple[set[str], int]:
    text = _conflict_text(value)
    identifiers = {
        item.upper()
        for item in re.findall(r"\b(?:MLB\d{6,}|[A-Z0-9][A-Z0-9._/-]*\d[A-Z0-9._/-]{2,})\b", str(value or ""), re.I)
    }
    negative = bool(
        re.search(r"\b(?:nao|nunca)\s+(?:e\s+)?(?:compativel|permitido|serve|suporta)\b|\bincompativel\b", text)
    )
    positive = bool(re.search(r"\b(?:compativel|permitido|serve|suporta)\b", text)) and not negative
    return identifiers, -1 if negative else 1 if positive else 0


def _finalize_context_retrieval_v3(
    payload: Mapping[str, Any],
    *,
    filters: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Attach bounded provenance, authority and conflict metadata to search results."""

    result = dict(payload)
    rows = [dict(item) for item in list(result.get("results") or []) if isinstance(item, Mapping)]

    def safe_score(item: Mapping[str, Any]) -> float:
        try:
            return max(0.0, float(item.get("score") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    maximum_score = max((safe_score(item) for item in rows), default=0.0)
    for item in rows:
        raw_score = safe_score(item)
        item["normalized_score"] = round(raw_score / maximum_score, 6) if maximum_score > 0 else 0.0
        item["authority"] = _context_authority(item.get("truth_class"))
        item["validity"] = _context_validity(item.get("truth_class"))
        item["citation_id"] = _context_citation_id(item)
        item["conflict"] = False
        item["conflict_with"] = []
        item["operational_data_source"] = False
    claims = [_conflict_claim(item.get("snippet")) for item in rows]
    for left_index, (left_ids, left_polarity) in enumerate(claims):
        if not left_ids or left_polarity == 0:
            continue
        for right_index in range(left_index + 1, len(rows)):
            right_ids, right_polarity = claims[right_index]
            if not left_ids.intersection(right_ids) or right_polarity == 0 or left_polarity == right_polarity:
                continue
            left = rows[left_index]
            right = rows[right_index]
            left["conflict"] = right["conflict"] = True
            left["conflict_with"].append(right["citation_id"])
            right["conflict_with"].append(left["citation_id"])
    conflict_detected = any(item["conflict"] for item in rows)
    applied_filters = {
        str(key): value
        for key, value in dict(filters or {}).items()
        if value not in (None, "", [], {}) and str(key) in CONTEXT_RETRIEVAL_FILTER_KEYS
    }
    gaps: list[str] = []
    if not result.get("generation_id"):
        gaps.append("no_active_generation")
    elif not rows:
        gaps.append("no_context_match")
    if conflict_detected:
        gaps.append("context_conflict")
    result.update(
        {
            "schema_version": CONTEXT_RETRIEVAL_V3,
            "authority_policy": CONTEXT_RETRIEVAL_AUTHORITY_POLICY,
            "results": rows,
            "citations": [
                {
                    key: item.get(key)
                    for key in (
                        "citation_id", "doc_id", "chunk_id", "reference", "snippet", "generation_id",
                        "source_version", "truth_class", "authority", "validity", "normalized_score",
                        "conflict", "conflict_with",
                    )
                }
                for item in rows
            ],
            "count": len(rows),
            "gaps": gaps,
            "coverage_complete": bool(rows) and not conflict_detected,
            "conflict_detected": conflict_detected,
            "filters_applied": applied_filters,
            "valid_at": str(dict(filters or {}).get("valid_at") or ""),
            "operational_data_source": False,
            "embeddings_enabled": False,
        }
    )
    return result
