"""Persistence and verification primitives for evidence-generation attestations."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Mapping

from backend.modules.context_hub.contracts import (
    ContextHubConflictError,
    ContextHubValidationError,
)
from backend.modules.context_hub.product_evidence_editorial import (
    collect_product_evidence_editorial_snapshot,
)


def persist_product_evidence_attestation(
    connection: sqlite3.Connection,
    generation_id: str,
    attestation: Mapping[str, Any],
) -> None:
    revision = int(attestation.get("evidence_revision") or 0)
    snapshot_hash = str(attestation.get("snapshot_hash") or "").lower()
    policy_version = str(attestation.get("policy_version") or "").strip()
    captured_at = str(attestation.get("captured_at") or "").strip()
    next_transition_at = str(attestation.get("next_transition_at") or "").strip()
    if (
        revision < 0
        or not re.fullmatch(r"[a-f0-9]{64}", snapshot_hash)
        or not re.fullmatch(r"[a-z0-9_.-]{1,96}", policy_version)
        or not 10 <= len(captured_at) <= 40
        or len(next_transition_at) > 40
    ):
        raise ContextHubValidationError("Atestado de evidencia de produto invalido.")
    connection.execute(
        "INSERT INTO context_hub_generation_product_evidence("
        "generation_id, evidence_revision, snapshot_hash, policy_version, "
        "captured_at, next_transition_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            generation_id,
            revision,
            snapshot_hash,
            policy_version,
            captured_at,
            next_transition_at or None,
        ),
    )


def assert_product_evidence_attestation_current(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    client_id: str,
    as_of: str,
) -> None:
    attestation = connection.execute(
        "SELECT snapshot_hash, policy_version "
        "FROM context_hub_generation_product_evidence WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if attestation is None:
        raise ContextHubConflictError(
            "As evidencias de produto mudaram; reconstrua antes de publicar."
        )
    try:
        snapshot = collect_product_evidence_editorial_snapshot(
            connection,
            client_id=client_id,
            as_of=as_of,
        )
    except Exception as error:
        raise ContextHubConflictError(
            "As evidencias de produto nao puderam ser verificadas; reconstrua antes de publicar."
        ) from error
    if (
        str(attestation["snapshot_hash"] or "") != str(snapshot["snapshot_hash"])
        or str(attestation["policy_version"] or "") != str(snapshot["policy_version"])
    ):
        raise ContextHubConflictError(
            "As evidencias de produto mudaram; reconstrua antes de publicar."
        )


def align_product_evidence_outbox_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    completed_at: str,
) -> None:
    attestation = connection.execute(
        "SELECT next_transition_at FROM context_hub_generation_product_evidence "
        "WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if attestation is None:
        raise ContextHubConflictError(
            "Atestado das evidencias de produto indisponivel; reconstrua antes de publicar."
        )
    next_transition_at = str(attestation["next_transition_at"] or "")
    updated = connection.execute(
        "UPDATE context_hub_product_evidence_outbox "
        "SET completed_revision=requested_revision, completed_generation_id=?, "
        "not_before=?, attempt_count=0, last_success_at=?, last_error_code='', "
        "updated_at=? "
        "WHERE singleton_id=1",
        (generation_id, next_transition_at, completed_at, completed_at),
    )
    if updated.rowcount != 1:
        raise ContextHubConflictError(
            "Estado das evidencias de produto indisponivel; reconstrua antes de publicar."
        )


__all__ = [
    "align_product_evidence_outbox_generation",
    "assert_product_evidence_attestation_current",
    "persist_product_evidence_attestation",
]
