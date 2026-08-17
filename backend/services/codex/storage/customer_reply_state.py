"""Transient and durable customer-reply state helpers."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from .common import _safe_id


_CUSTOMER_REPLY_TRANSIENT_LOCK = threading.RLock()


_CUSTOMER_REPLY_TRANSIENT: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


_CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS = 24 * 60 * 60


_CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS = 15 * 60


_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS = 7


# Customer prompts, buyer history, generated answers, evidence, queries, sources,
# tool output and operator guidance are intentionally absent. This table is a
# durable scheduler/lease journal, not a conversation or proposal store.
_CUSTOMER_REPLY_DURABLE_FIELDS = frozenset({
    "job_id", "profile", "task_type", "subject_key", "event_subject_key",
    "question_id", "item_id", "store", "client_id", "channel", "status",
    "agent_state", "current_step", "idempotency_key", "request_hash",
    "thread_id", "thread_reused", "thread_restart_reason", "prompt_version",
    "prompt_hash", "schema_version", "conversation_id", "previous_job_id",
    "plan_id", "proposal_id", "proposal_version", "proposal_hash", "action_id",
    "cancel_requested", "request_generation", "attempt_count",
    "operational_failure_count", "retry_count", "retry_policy",
    "queue_origin", "queue_priority", "queue_policy_version",
    "evidence_attempt_count", "evidence_attempt_limit",
    "operational_failure_limit", "total_attempt_limit",
    "first_started_at_epoch", "execution_deadline_epoch",
    "next_retry_at_epoch", "next_retry_delay_seconds", "deadline_seconds",
    "deadline_at_epoch", "deadline_reached", "completed_with_partial",
    "blocked_without_draft", "contract_quarantined", "lease_owner",
    "lease_expires_ts", "lease_generation", "heartbeat_at", "retry_ready_at",
    "last_attempt_completed_at", "created_at", "updated_at", "completed_at",
    "data_sufficient", "publish_attempted", "requires_approval",
    "completion_reason", "draft_source", "review_required", "draft_expired", "retry_kind",
})


_CUSTOMER_REPLY_SCOPE_ID_FIELDS = frozenset({
    "question_id", "item_id", "buyer_id", "pack_id", "order_id",
})


def _customer_reply_cache_key(db_path: str, job_id: str) -> tuple[str, str]:
    return (os.path.abspath(db_path), _safe_id(job_id, ""))


def _customer_reply_durable_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {key: value for key, value in source.items() if key in _CUSTOMER_REPLY_DURABLE_FIELDS}
    scope = source.get("scope_verifiers") if isinstance(source.get("scope_verifiers"), dict) else {}
    durable_scope = {
        key: str(value or "")[:160]
        for key, value in scope.items()
        if key in _CUSTOMER_REPLY_SCOPE_ID_FIELDS and str(value or "").strip()
    }
    if durable_scope:
        durable["scope_verifiers"] = durable_scope
    result = source.get("result") if isinstance(source.get("result"), dict) else {}
    for key in (
        "proposal_id", "proposal_version", "proposal_hash", "data_sufficient",
        "publish_attempted", "requires_approval", "completed_with_partial",
        "blocked_without_draft", "draft_source",
    ):
        if key in result and key not in durable:
            durable[key] = result.get(key)
    steps = source.get("agent_steps") if isinstance(source.get("agent_steps"), list) else []
    if steps:
        durable["agent_steps"] = [
            {
                key: item.get(key)
                for key in ("state", "step", "at")
                if key in item
            }
            for item in steps[-60:]
            if isinstance(item, dict)
        ]
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    if verification:
        durable["verification"] = {
            key: verification.get(key)
            for key in ("status", "confirmed", "verified_at")
            if key in verification
        }
    transient = {key: value for key, value in source.items() if key not in durable}
    return durable, transient


def _customer_reply_plan_durable_payload(payload: Any) -> dict[str, Any]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {
        key: source.get(key)
        for key in (
            "plan_id", "task_id", "conversation_id", "conversation_generation",
            "channel", "agent_state", "current_step", "idempotency_key",
            "created_at", "updated_at",
        )
        if key in source
    }
    durable["required_input"] = []
    durable["guidance_applied"] = []
    durable["steps"] = [
        {
            key: step.get(key)
            for key in ("step_id", "status", "started_at", "completed_at")
            if key in step
        }
        for step in (source.get("steps") or [])
        if isinstance(step, dict)
    ]
    durable["state_history"] = [
        {
            key: state.get(key)
            for key in ("state", "step", "at")
            if key in state
        }
        for state in (source.get("state_history") or [])[-100:]
        if isinstance(state, dict)
    ]
    proposal = source.get("proposal") if isinstance(source.get("proposal"), dict) else {}
    durable["proposal"] = {
        key: proposal.get(key)
        for key in (
            "proposal_id", "version", "proposal_hash", "action_id",
            "channels_allowed", "requires_confirmation",
        )
        if key in proposal
    }
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    durable["verification"] = {
        key: verification.get(key)
        for key in ("status", "confirmed", "verified_at")
        if key in verification
    }
    return durable


def _customer_reply_transient_put(db_path: str, payload: dict[str, Any], transient: dict[str, Any]) -> None:
    job_id = _safe_id(payload.get("job_id"), "")
    if not job_id:
        return
    key = _customer_reply_cache_key(db_path, job_id)
    status = str(payload.get("status") or "")
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        if status == "cancelled":
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return
        previous = _CUSTOMER_REPLY_TRANSIENT.get(key)
        merged = dict(previous[1]) if previous and previous[0] > time.time() else {}
        merged.update(transient)
        if not merged:
            return
        ttl = (
            _CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS
            if status == "completed"
            else _CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS
        )
        _CUSTOMER_REPLY_TRANSIENT[key] = (time.time() + ttl, merged)


def _customer_reply_transient_merge(db_path: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    key = _customer_reply_cache_key(db_path, str(payload.get("job_id") or ""))
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        cached = _CUSTOMER_REPLY_TRANSIENT.get(key)
        if not cached:
            return payload
        if cached[0] <= time.time():
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return payload
        return {**payload, **cached[1]}


def _customer_reply_cleanup_rows(conn: sqlite3.Connection) -> list[str]:
    expired = [
        str(row["job_id"] or "")
        for row in conn.execute(
            "SELECT job_id FROM assistant_customer_reply_jobs "
            "WHERE status IN ('completed', 'cancelled') "
            "AND julianday(updated_at) < julianday('now', ?)",
            (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
        ).fetchall()
    ]
    conn.execute(
        "DELETE FROM assistant_customer_reply_jobs "
        "WHERE status IN ('completed', 'cancelled') "
        "AND julianday(updated_at) < julianday('now', ?)",
        (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
    )
    return expired
