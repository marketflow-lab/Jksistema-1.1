"""Activation and conflict rules for tenant-scoped product evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.product_evidence_model import (
    _OFFICIAL_STRONG_SOURCE_TYPES,
    _TECHNICAL_SOURCE_TYPES,
    _independence_origin,
    _iso,
    _parse_timestamp,
    _registrable_domain,
    _source_fingerprint,
)
from backend.modules.context_hub.runtime import _sha256_text


_ADVISORY_FIELD_PREFIXES = ("context.", "refutation.")


def _batch_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return tuple(
        str(row[key])
        for key in ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id")
    )  # type: ignore[return-value]


def _fetch_batch(connection: sqlite3.Connection, batch_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM product_evidence_batches WHERE batch_id=?", (batch_id,)
    ).fetchone()
    if row is None:
        raise ContextHubValidationError("Lote de evidencias de produto inexistente.")
    return row


def _require_collecting_batch(connection: sqlite3.Connection, batch_id: str) -> sqlite3.Row:
    row = _fetch_batch(connection, batch_id)
    if str(row["status"]) != "collecting":
        raise ContextHubValidationError("Lote de evidencias de produto ja foi encerrado.")
    return row


def _is_strong_claim(field_name: str, scope: str) -> bool:
    if scope == "application":
        return True
    parts = set(field_name.split("."))
    return bool(
        parts
        & {
            "compatibility",
            "compatibilidade",
            "fitment",
            "application",
            "aplicacao",
            "originality",
            "originalidade",
            "genuine",
            "genuino",
            "original_standard",
            "padrao_original",
            "equivalent_original",
            "equivalente_original",
        }
    )


def _claim_source_expiry(
    field_name: str,
    scope: str,
    source: Mapping[str, Any],
) -> datetime:
    source_expiry = _parse_timestamp(source["valid_until"])
    collected = _parse_timestamp(source["collected_at"])
    if scope == "application" or set(field_name.split(".")) & {
        "compatibility",
        "compatibilidade",
        "fitment",
        "application",
        "aplicacao",
    }:
        return min(source_expiry, collected + timedelta(days=90))
    return source_expiry


def _support_for_claim(
    field_name: str,
    scope: str,
    sources: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> tuple[bool, str, Optional[datetime], bool]:
    active: list[tuple[Mapping[str, Any], datetime]] = []
    for source in sources:
        expiry = _claim_source_expiry(field_name, scope, source)
        collected = _parse_timestamp(source["collected_at"])
        if collected <= as_of < expiry:
            active.append((source, expiry))
    if not active:
        return False, "", None, False
    if field_name.startswith(_ADVISORY_FIELD_PREFIXES):
        return (
            False,
            "advisory_candidate_v1",
            max(expiry for _source, expiry in active),
            True,
        )
    strong = _is_strong_claim(field_name, scope)
    official_expiries = [
        expiry
        for source, expiry in active
        if str(source["source_type"]) in _OFFICIAL_STRONG_SOURCE_TYPES
    ]
    if official_expiries:
        return (
            True,
            "strong_official_only_v1" if strong else "official_single_v1",
            max(official_expiries),
            True,
        )
    if strong:
        return False, "strong_official_only_v1", max(expiry for _source, expiry in active), True
    technical: dict[tuple[str, str], datetime] = {}
    seen_fingerprints: set[str] = set()
    for source, expiry in sorted(active, key=lambda item: item[1], reverse=True):
        if str(source["source_type"]) not in _TECHNICAL_SOURCE_TYPES:
            continue
        fingerprint = _source_fingerprint(source)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        key = (
            _independence_origin(source["origin_key"], source["domain"]),
            _registrable_domain(source["domain"]),
        )
        technical[key] = max(technical.get(key, expiry), expiry)
    origins = {key[0] for key in technical}
    domains = {key[1] for key in technical}
    if len(origins) >= 2 and len(domains) >= 2:
        expiries = sorted(technical.values(), reverse=True)
        return True, "two_independent_technical_v1", expiries[1], True
    return False, "", max(expiry for _source, expiry in active), True


def _source_supports_policy(source_type: str, policy: str) -> bool:
    if policy in {"strong_official_only_v1", "official_single_v1"}:
        return source_type in _OFFICIAL_STRONG_SOURCE_TYPES
    if policy == "two_independent_technical_v1":
        return source_type in _TECHNICAL_SOURCE_TYPES
    return False


def _load_identity_claims(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    field_name: str,
    scope: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT c.*, s.source_id, s.source_type, s.authority, s.origin_key,
               s.domain, s.content_hash, s.copy_fingerprint, s.canonical_url, s.section_ref,
               s.collected_at AS source_collected_at, s.valid_until AS source_valid_until
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
         LEFT JOIN product_evidence_claim_sources cs ON cs.claim_id=c.claim_id
         LEFT JOIN product_evidence_sources s ON s.source_id=cs.source_id
         WHERE b.store_ref=? AND b.seller_id=? AND b.site_id=? AND b.sku=?
           AND b.item_id=? AND b.variation_id=? AND b.status='completed'
           AND c.field_name=? AND c.scope=?
        """,
        (*identity, field_name, scope),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        claim_id = str(row["claim_id"])
        claim = grouped.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                "state": str(row["state"]),
                "activation_policy": str(row["activation_policy"]),
                "conflict_group": str(row["conflict_group"]),
                "valid_until": str(row["valid_until"] or ""),
                "normalized_key": str(row["normalized_key"]),
                "unit": str(row["unit"]),
                "sources": [],
            },
        )
        if row["source_id"] is not None:
            claim["sources"].append(
                {
                    "source_id": str(row["source_id"]),
                    "source_type": str(row["source_type"]),
                    "authority": str(row["authority"]),
                    "origin_key": str(row["origin_key"]),
                    "domain": str(row["domain"]),
                    "content_hash": str(row["content_hash"]),
                    "copy_fingerprint": str(row["copy_fingerprint"] or ""),
                    "canonical_url": str(row["canonical_url"]),
                    "section_ref": str(row["section_ref"]),
                    "collected_at": str(row["source_collected_at"]),
                    "valid_until": str(row["source_valid_until"]),
                }
            )
    return list(grouped.values())


def _recalculate_identity_field(
    connection: sqlite3.Connection,
    identity: tuple[str, str, str, str, str, str],
    field_name: str,
    scope: str,
    *,
    as_of: datetime,
) -> None:
    claims = _load_identity_claims(connection, identity, field_name, scope)
    sources_by_value: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for claim in claims:
        if claim["state"] == "rejected":
            continue
        value_key = (claim["normalized_key"], claim["unit"])
        sources_by_value.setdefault(value_key, []).extend(claim["sources"])
    evaluated_values = {
        value_key: _support_for_claim(field_name, scope, sources, as_of)
        for value_key, sources in sources_by_value.items()
    }
    evaluated: dict[str, tuple[bool, str, Optional[datetime], bool]] = {}
    qualified_values: set[tuple[str, str]] = set()
    for claim in claims:
        if claim["state"] == "rejected":
            continue
        value_key = (claim["normalized_key"], claim["unit"])
        result = evaluated_values[value_key]
        evaluated[claim["claim_id"]] = result
        if result[0]:
            qualified_values.add(value_key)
    conflict = len(qualified_values) > 1
    conflict_group = (
        _sha256_text("|".join((*identity, field_name, scope)))[:24] if conflict else ""
    )
    updated_at = _iso(as_of)
    for claim in claims:
        if claim["state"] == "rejected":
            continue
        supported, policy, valid_until, has_active = evaluated[claim["claim_id"]]
        if supported and conflict:
            state, activation_policy = "conflict", "conflict_v1"
        elif supported:
            state, activation_policy = "verified", policy
        elif not has_active:
            state, activation_policy = "expired", ""
        else:
            state, activation_policy = "candidate", policy
        normalized_valid_until = _iso(valid_until) if valid_until is not None else ""
        normalized_conflict_group = conflict_group if supported else ""
        if (
            claim["state"] == state
            and claim["activation_policy"] == activation_policy
            and claim["conflict_group"] == normalized_conflict_group
            and claim["valid_until"] == normalized_valid_until
        ):
            continue
        connection.execute(
            """
            UPDATE product_evidence_claims
               SET state=?, activation_policy=?, conflict_group=?, valid_until=?, updated_at=?
             WHERE claim_id=?
            """,
            (
                state,
                activation_policy,
                normalized_conflict_group,
                normalized_valid_until or None,
                updated_at,
                claim["claim_id"],
            ),
        )


__all__ = [
    "_batch_identity",
    "_recalculate_identity_field",
    "_require_collecting_batch",
    "_source_supports_policy",
]
