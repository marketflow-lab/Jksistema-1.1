"""Action proposals, runs, approvals, and agent audit persistence."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from .common import (
    _connection,
    _json_dumps,
    _json_loads,
    _lock_for,
    _now_iso,
    _safe_id,
    codex_assistant_state_db_path,
)
from .schema import _ensure_state_schema


def codex_assistant_action_proposal_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    proposal_id = _safe_id(data.get("proposal_id"), "")
    if not proposal_id:
        raise ValueError("proposal_id obrigatorio")
    now = _now_iso()
    data.setdefault("created_at", now)
    data["updated_at"] = now
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT INTO assistant_action_proposals(
                    proposal_id, action_id, status, version, proposal_hash,
                    conversation_id, created_by, expires_at, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    action_id=excluded.action_id,
                    status=excluded.status,
                    version=excluded.version,
                    proposal_hash=excluded.proposal_hash,
                    conversation_id=excluded.conversation_id,
                    created_by=excluded.created_by,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    proposal_id,
                    str(data.get("action_id") or ""),
                    str(data.get("status") or "awaiting_approval"),
                    max(1, int(data.get("version") or 1)),
                    str(data.get("proposal_hash") or ""),
                    str(data.get("conversation_id") or ""),
                    str(data.get("created_by") or ""),
                    str(data.get("expires_at") or ""),
                    str(data.get("created_at") or now),
                    now,
                    raw,
                ),
            )
    return data


def codex_assistant_action_proposal_get(
    info_base: str,
    client_id: str,
    proposal_id: str,
) -> Optional[dict[str, Any]]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_action_proposals WHERE proposal_id = ?",
                (_safe_id(proposal_id, ""),),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return payload if isinstance(payload, dict) else None


def codex_assistant_action_proposal_list(
    info_base: str,
    client_id: str,
    *,
    status: str = "",
    created_by: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if str(status or "").strip():
        clauses.append("status = ?")
        params.append(str(status).strip())
    if str(created_by or "").strip():
        clauses.append("LOWER(created_by) = LOWER(?)")
        params.append(str(created_by).strip())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    safe_limit = max(1, min(int(limit or 100), 500))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT payload_json FROM assistant_action_proposals"
                + where
                + " ORDER BY updated_at DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
    return [item for row in rows if isinstance((item := _json_loads(row["payload_json"], {})), dict)]


def codex_assistant_action_run_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    run_id = _safe_id(data.get("run_id"), "")
    if not run_id:
        raise ValueError("run_id obrigatorio")
    now = _now_iso()
    data.setdefault("created_at", now)
    data["updated_at"] = now
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT INTO assistant_action_runs(
                    run_id, proposal_id, status, idempotency_key, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    proposal_id=excluded.proposal_id,
                    status=excluded.status,
                    idempotency_key=excluded.idempotency_key,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    run_id,
                    str(data.get("proposal_id") or ""),
                    str(data.get("status") or "queued"),
                    str(data.get("idempotency_key") or ""),
                    str(data.get("created_at") or now),
                    now,
                    raw,
                ),
            )
    return data


def codex_assistant_action_run_get(
    info_base: str,
    client_id: str,
    run_id: str = "",
    *,
    idempotency_key: str = "",
) -> Optional[dict[str, Any]]:
    if not run_id and not idempotency_key:
        return None
    column = "run_id" if run_id else "idempotency_key"
    value = _safe_id(run_id, "") if run_id else str(idempotency_key)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                f"SELECT payload_json FROM assistant_action_runs WHERE {column} = ? LIMIT 1",
                (value,),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return payload if isinstance(payload, dict) else None


def codex_assistant_action_approval_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    approval_id = _safe_id(data.get("approval_id"), "") or hashlib.sha256(
        f"{time.time_ns()}|{client_id}|{data.get('proposal_id')}".encode("utf-8")
    ).hexdigest()[:32]
    data["approval_id"] = approval_id
    data.setdefault("created_at", _now_iso())
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO assistant_action_approvals(
                    approval_id, proposal_id, proposal_version, proposal_hash, status,
                    source, actor, wa_id_hash, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    str(data.get("proposal_id") or ""),
                    max(1, int(data.get("proposal_version") or 1)),
                    str(data.get("proposal_hash") or ""),
                    str(data.get("status") or "approved"),
                    str(data.get("source") or "app"),
                    str(data.get("actor") or ""),
                    str(data.get("wa_id_hash") or ""),
                    str(data.get("created_at") or _now_iso()),
                    raw,
                ),
            )
    return data


def codex_assistant_agent_audit_add(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    data.setdefault("created_at", _now_iso())
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            cursor = conn.execute(
                """
                INSERT INTO assistant_agent_audit(
                    event_type, entity_type, entity_id, actor, channel, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(data.get("event_type") or "event"),
                    str(data.get("entity_type") or ""),
                    str(data.get("entity_id") or ""),
                    str(data.get("actor") or ""),
                    str(data.get("channel") or ""),
                    str(data.get("created_at") or _now_iso()),
                    raw,
                ),
            )
            data["audit_id"] = int(cursor.lastrowid or 0)
    return data


def codex_assistant_agent_audit_list(
    info_base: str,
    client_id: str,
    *,
    entity_type: str = "",
    entity_id: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(str(entity_type))
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(str(entity_id))
    params.append(max(1, min(2000, int(limit or 200))))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT audit_id, payload_json FROM assistant_agent_audit WHERE "
                + " AND ".join(clauses)
                + " ORDER BY audit_id DESC LIMIT ?",
                params,
            ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_loads(row["payload_json"], {})
        if isinstance(payload, dict):
            payload["audit_id"] = int(row["audit_id"] or 0)
            result.append(payload)
    return result
