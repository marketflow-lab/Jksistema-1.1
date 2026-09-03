"""Deterministic, read-only Obsidian projection for structured product evidence.

The operational product-evidence tables remain authoritative.  This module only
collects a canonical snapshot from an existing SQLite connection and renders an
in-memory projection suitable for a later Context Hub generation.  It never
writes to SQLite, the vault, a generation directory, or the publication state.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from itertools import combinations
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.paths import _normalize_client_id
from backend.modules.context_hub.product_evidence_activation import (
    _claim_source_expiry,
    _support_for_claim,
    _source_supports_policy,
)
from backend.modules.context_hub.product_evidence_model import (
    PRODUCT_EVIDENCE_POLICY,
    PRODUCT_EVIDENCE_SCOPES,
    _SOURCE_AUTHORITIES,
    _canonicalize_url,
    _independence_origin,
    _iso,
    _normalize_field_name,
    _normalize_hash,
    _normalize_source_type,
    _normalize_text,
    _parse_timestamp,
    _registrable_domain,
    _reject_sensitive,
    _source_fingerprint,
)
from backend.modules.context_hub.product_evidence_rendering import (
    PRODUCT_EVIDENCE_EDITORIAL_ROOT,
    PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
    _assert_dlp_safe,
    render_product_evidence_editorial,
)
from backend.modules.context_hub.runtime import (
    _json_canonical,
    _sha256_text,
)


_PRODUCT_EVIDENCE_TABLES = frozenset(
    {
        "product_evidence_batches",
        "product_evidence_sources",
        "product_evidence_claims",
        "product_evidence_claim_sources",
    }
)


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description or ()]
    result: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        if isinstance(row, sqlite3.Row):
            result.append({column: row[column] for column in columns})
        else:
            result.append(dict(zip(columns, row)))
    return result


def _tables_available(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'product_evidence_%'"
    ).fetchall()
    names = {str(row[0]) for row in rows}
    return _PRODUCT_EVIDENCE_TABLES.issubset(names)


def _safe_short_text(value: object, *, label: str, maximum: int = 256) -> str:
    normalized = _normalize_text(value, label=label, maximum=maximum)
    _reject_sensitive(normalized, label=label)
    return normalized


def _normalized_timestamp(value: object, *, label: str) -> str:
    raw = _normalize_text(value, label=label, maximum=64)
    return _iso(_parse_timestamp(raw))


def _normalized_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id"):
        value = _normalize_text(
            row.get(key), label=key, maximum=180, required=key not in {"item_id", "variation_id"}
        )
        _reject_sensitive(value, label=key)
        values.append(value)
    return tuple(values)


def _normalized_source(row: Mapping[str, Any]) -> dict[str, str] | None:
    if row.get("source_id") is None:
        return None
    canonical_url, domain = _canonicalize_url(row.get("canonical_url"))
    source_type = _normalize_source_type(row.get("source_type"))
    authority = _SOURCE_AUTHORITIES[source_type]
    stored_authority = str(row.get("authority") or "").strip().lower()
    if stored_authority and stored_authority != authority:
        raise ContextHubValidationError("Autoridade divergente na evidencia editorial de produto.")
    section_ref = _normalize_text(
        row.get("section_ref"), label="Secao", maximum=160, required=False
    )
    _reject_sensitive(section_ref, label="Secao")
    return {
        "url": canonical_url,
        "domain": domain,
        "source_type": source_type,
        "authority": authority,
        "origin_key": _safe_short_text(
            row.get("origin_key") or domain, label="Origem", maximum=160
        ).casefold(),
        "section_ref": section_ref,
        "collected_at": _normalized_timestamp(row.get("source_collected_at"), label="Coleta"),
        "valid_until": _normalized_timestamp(row.get("source_valid_until"), label="Validade da fonte"),
        "content_hash": _normalize_hash(row.get("content_hash"), label="Hash de conteudo"),
        "copy_fingerprint": _normalize_hash(
            row.get("copy_fingerprint"), label="Fingerprint de copia", required=False
        ),
    }


def _snapshot_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _tables_available(connection):
        return []
    cursor = connection.execute(
        """
        SELECT b.store_ref, b.seller_id, b.site_id, b.sku, b.item_id, b.variation_id,
               c.claim_id, c.field_name, c.scope, c.normalized_value,
               c.normalized_key, c.unit, c.state, c.activation_policy,
               c.conflict_group, c.valid_from, c.valid_until,
               s.source_id, s.canonical_url, s.domain, s.source_type, s.authority,
               s.origin_key, s.section_ref, s.collected_at AS source_collected_at,
               s.valid_until AS source_valid_until, s.content_hash, s.copy_fingerprint
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
          LEFT JOIN product_evidence_claim_sources cs ON cs.claim_id=c.claim_id
          LEFT JOIN product_evidence_sources s ON s.source_id=cs.source_id
         WHERE b.status='completed' AND c.state!='rejected'
         ORDER BY b.store_ref, b.seller_id, b.site_id, b.sku, b.item_id,
                  b.variation_id, c.field_name, c.scope, c.normalized_key,
                  c.unit, c.state, s.canonical_url, s.content_hash
        """
    )
    return _rows_as_dicts(cursor)


def _fact_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    field_name = _normalize_field_name(row.get("field_name"))
    scope = str(row.get("scope") or "").strip().lower()
    if scope not in PRODUCT_EVIDENCE_SCOPES:
        raise ContextHubValidationError("Escopo invalido na evidencia editorial de produto.")
    normalized_key = _safe_short_text(
        row.get("normalized_key"), label="Chave normalizada", maximum=256
    )
    unit = _normalize_text(row.get("unit"), label="Unidade", maximum=16, required=False)
    _reject_sensitive(unit, label="Unidade")
    return field_name, scope, normalized_key, unit


def _new_fact(row: Mapping[str, Any], key: tuple[str, str, str, str]) -> dict[str, Any]:
    value = _safe_short_text(row.get("normalized_value"), label="Valor", maximum=256)
    return {
        "field_name": key[0],
        "scope": key[1],
        "value": value,
        "normalized_key": key[2],
        "unit": key[3],
        "sources": {},
    }


def _merge_fact_row(fact: dict[str, Any], row: Mapping[str, Any]) -> None:
    value = _safe_short_text(row.get("normalized_value"), label="Valor", maximum=256)
    if value != fact["value"]:
        raise ContextHubValidationError("Afirmacoes equivalentes divergiram na projecao editorial.")
    source = _normalized_source(row)
    if source is not None:
        source_key = (
            source["url"], source["source_type"], source["content_hash"],
            source["copy_fingerprint"], source["valid_until"], source["origin_key"],
        )
        fact["sources"][source_key] = source


def _policy_sources_at(
    fact: Mapping[str, Any], sources: Sequence[Mapping[str, Any]], policy: str, as_of: Any,
) -> tuple[list[Mapping[str, Any]], str]:
    active: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    eligible = [
        source for source in sources
        if _source_supports_policy(str(source["source_type"]), policy)
        and _parse_timestamp(source["collected_at"]) <= as_of < _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), source
        )
    ]
    for source in sorted(
        eligible,
        key=lambda item: _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), item
        ),
        reverse=True,
    ):
        fingerprint = _source_fingerprint(source)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        active.append(source)
    if policy != "two_independent_technical_v1":
        return active, min(str(source["collected_at"]) for source in active)
    pairs = [
        pair for pair in combinations(active, 2)
        if _independence_origin(pair[0]["origin_key"], pair[0]["domain"])
        != _independence_origin(pair[1]["origin_key"], pair[1]["domain"])
        and _registrable_domain(pair[0]["domain"]) != _registrable_domain(pair[1]["domain"])
    ]
    if not pairs:
        raise ContextHubValidationError("Suporte tecnico independente divergente no snapshot.")
    start = min(max(str(left["collected_at"]), str(right["collected_at"])) for left, right in pairs)
    return active, start


def _candidate_sources_at(
    fact: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    as_of: Any,
) -> list[Mapping[str, Any]]:
    """Return current, deduplicated sources without granting factual authority."""

    active: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for source in sorted(
        sources,
        key=lambda item: _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), item
        ),
        reverse=True,
    ):
        expiry = _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), source
        )
        if not (_parse_timestamp(source["collected_at"]) <= as_of < expiry):
            continue
        fingerprint = _source_fingerprint(source)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        active.append(source)
    return active


def _finish_fact(fact: dict[str, Any], as_of: Any) -> dict[str, Any] | None:
    sources = sorted(
        fact["sources"].values(),
        key=lambda source: (
            source["domain"], source["url"], source["source_type"],
            source["content_hash"], source["valid_until"],
        ),
    )
    if not sources:
        return None
    current = _support_for_claim(fact["field_name"], fact["scope"], sources, as_of)
    history = []
    for event in sorted({_parse_timestamp(source["collected_at"]) for source in sources}):
        result = _support_for_claim(fact["field_name"], fact["scope"], sources, event)
        if result[0]:
            history.append((event, result))
    if current[0]:
        supporting_sources, valid_from = _policy_sources_at(fact, sources, current[1], as_of)
        state, policy, valid_until = "verified", current[1], _iso(current[2])
    elif current[3]:
        supporting_sources = _candidate_sources_at(fact, sources, as_of)
        if not supporting_sources:
            return None
        valid_from = min(str(source["collected_at"]) for source in supporting_sources)
        valid_until = _iso(
            max(
                _claim_source_expiry(
                    str(fact["field_name"]), str(fact["scope"]), source
                )
                for source in supporting_sources
            )
        )
        state, policy = "candidate", current[1] or "candidate_only_v1"
    elif history:
        supporting_sources = []
        valid_from = _iso(min(item[0] for item in history))
        valid_until = _iso(max(item[1][2] for item in history if item[1][2] is not None))
        state, policy = "expired", history[-1][1][1]
    else:
        return None
    return {
        "field_name": fact["field_name"],
        "scope": fact["scope"],
        "value": fact["value"],
        "normalized_key": fact["normalized_key"],
        "unit": fact["unit"],
        "state": state,
        "activation_policy": policy,
        "conflict_group": "",
        "valid_from": valid_from,
        "valid_until": valid_until,
        "sources": sources,
        "supporting_sources": supporting_sources,
    }


def _identity_digest(client_id: str, identity: Sequence[str]) -> str:
    return _sha256_text(_json_canonical([client_id, *identity]))


def collect_product_evidence_editorial_snapshot(
    connection: sqlite3.Connection,
    *,
    client_id: object,
    as_of: object,
) -> dict[str, Any]:
    """Read and canonicalize exportable evidence without changing the database."""

    normalized_client = _normalize_client_id(client_id)
    captured_datetime = _parse_timestamp(as_of)
    captured_at = _iso(captured_datetime)
    identities: dict[tuple[str, ...], dict[tuple[str, str, str, str], dict[str, Any]]] = defaultdict(dict)
    for row in _snapshot_rows(connection):
        identity = _normalized_identity(row)
        key = _fact_key(row)
        fact = identities[identity].get(key)
        if fact is None:
            fact = _new_fact(row, key)
            identities[identity][key] = fact
        _merge_fact_row(fact, row)

    editorial_identities: list[dict[str, Any]] = []
    for identity in sorted(identities):
        facts = [
            finished for key in sorted(identities[identity])
            if (finished := _finish_fact(identities[identity][key], captured_datetime)) is not None
        ]
        by_field: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for fact in facts:
            if fact["state"] == "verified":
                by_field[(fact["field_name"], fact["scope"])].append(fact)
        for (field_name, scope), active in by_field.items():
            if len(active) < 2:
                continue
            group = _sha256_text(_json_canonical([*identity, field_name, scope]))[:24]
            for fact in active:
                fact.update(state="conflict", activation_policy="conflict_v1", conflict_group=group)
        if not facts:
            continue
        editorial_identities.append(
            {
                "identity_key": _identity_digest(normalized_client, identity),
                "store_ref": identity[0],
                "seller_id": identity[1],
                "site_id": identity[2],
                "sku": identity[3],
                "item_id": identity[4],
                "variation_id": identity[5],
                "facts": facts,
            }
        )
    verified_identities = [
        identity | {
            "facts": [
                fact for fact in identity["facts"] if fact["state"] != "candidate"
            ]
        }
        for identity in editorial_identities
        if any(fact["state"] != "candidate" for fact in identity["facts"])
    ]
    transitions = sorted(
        _iso(_claim_source_expiry(fact["field_name"], fact["scope"], source))
        for identity in verified_identities
        for fact in identity["facts"]
        for source in fact["supporting_sources"]
        if _claim_source_expiry(fact["field_name"], fact["scope"], source) > captured_datetime
    )
    projection_transitions = sorted(
        _iso(_claim_source_expiry(fact["field_name"], fact["scope"], source))
        for identity in editorial_identities
        for fact in identity["facts"]
        for source in fact["supporting_sources"]
        if _claim_source_expiry(fact["field_name"], fact["scope"], source) > captured_datetime
    )
    payload = {
        "schema_version": PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
        "policy_version": PRODUCT_EVIDENCE_POLICY,
        "client_id": normalized_client,
        "identities": verified_identities,
    }
    projection_payload = payload | {"editorial_identities": editorial_identities}
    _assert_dlp_safe(
        _json_canonical(projection_payload),
        source_ref="product_evidence_editorial_snapshot",
    )
    counts = defaultdict(int)
    for identity in editorial_identities:
        for fact in identity["facts"]:
            counts[str(fact["state"])] += 1
    return payload | {
        "editorial_identities": editorial_identities,
        "snapshot_hash": _sha256_text(_json_canonical(payload)),
        "projection_hash": _sha256_text(_json_canonical(projection_payload)),
        "captured_at": captured_at,
        "next_transition_at": transitions[0] if transitions else "",
        "projection_next_transition_at": (
            projection_transitions[0] if projection_transitions else ""
        ),
        "stats": {
            "identities": len(editorial_identities),
            "facts": sum(counts.values()),
            "candidate": counts["candidate"],
            "verified": counts["verified"],
            "conflict": counts["conflict"],
            "expired": counts["expired"],
        },
    }


__all__ = [
    "PRODUCT_EVIDENCE_EDITORIAL_ROOT",
    "PRODUCT_EVIDENCE_EDITORIAL_SCHEMA",
    "collect_product_evidence_editorial_snapshot",
    "render_product_evidence_editorial",
]
