"""Atomic persistence for operator revisions of customer-reply proposals."""

from __future__ import annotations

from typing import Any

from .common import _connection, _json_loads, _lock_for, codex_assistant_state_db_path
from .customer_replies import _customer_reply_prepare_job, _customer_reply_upsert
from .customer_reply_state import (
    _CUSTOMER_REPLY_TRANSIENT,
    _CUSTOMER_REPLY_TRANSIENT_LOCK,
    _customer_reply_cache_key,
    _customer_reply_cleanup_rows,
    _customer_reply_transient_merge,
    _customer_reply_transient_put,
    _manual_safe_job_payload,
)
from .schema import _ensure_state_schema


PROPOSAL_CAS_APPLIED = "_proposal_cas_applied"


def customer_reply_proposal_save_cas(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    expected_proposal_version: int,
    expected_proposal_hash: str,
) -> dict[str, Any]:
    """Replace a proposal iff its durable version and hash still match."""

    data, job_id, now = _customer_reply_prepare_job(client_id, payload)
    if str(data.get("queue_origin") or "") == "manual":
        data = _manual_safe_job_payload(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    expired_job_ids: list[str] = []
    with _lock_for(db_path):
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            expired_job_ids = _customer_reply_cleanup_rows(conn)
            row = conn.execute(
                "SELECT status, cancel_requested, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            existing = _json_loads(row["payload_json"] if row else "", {})
            if not isinstance(existing, dict):
                existing = {}
            matches = bool(
                row
                and not bool(row["cancel_requested"])
                and str(row["status"] or "") == "completed"
                and max(1, int(existing.get("proposal_version") or 1))
                == int(expected_proposal_version)
                and str(existing.get("proposal_hash") or "")
                == str(expected_proposal_hash)
            )
            if not matches:
                current = _customer_reply_transient_merge(db_path, existing, job_id)
                return {**current, PROPOSAL_CAS_APPLIED: False}
            transient = _customer_reply_upsert(conn, db_path, data, job_id, now)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        for expired_job_id in expired_job_ids:
            _CUSTOMER_REPLY_TRANSIENT.pop(
                _customer_reply_cache_key(db_path, expired_job_id), None
            )
    _customer_reply_transient_put(db_path, data, transient)
    return {**data, PROPOSAL_CAS_APPLIED: True}


__all__ = ["PROPOSAL_CAS_APPLIED", "customer_reply_proposal_save_cas"]
