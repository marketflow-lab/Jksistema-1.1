"""Agent plan and guidance persistence."""

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


def codex_assistant_agent_plan_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    plan_id = _safe_id(data.get("plan_id"), "")
    if not plan_id:
        plan_id = hashlib.sha256(
            f"{time.time_ns()}|{client_id}|{data.get('task_id')}|{data.get('conversation_id')}".encode("utf-8")
        ).hexdigest()[:32]
    now = _now_iso()
    data["plan_id"] = plan_id
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
                INSERT INTO assistant_agent_plans(
                    plan_id, task_id, conversation_id, conversation_generation, status,
                    idempotency_key, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    conversation_id=excluded.conversation_id,
                    conversation_generation=excluded.conversation_generation,
                    status=excluded.status,
                    idempotency_key=excluded.idempotency_key,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    plan_id,
                    str(data.get("task_id") or ""),
                    str(data.get("conversation_id") or ""),
                    max(1, int(data.get("conversation_generation") or 1)),
                    str(data.get("agent_state") or data.get("status") or "entendendo"),
                    str(data.get("idempotency_key") or ""),
                    str(data.get("created_at") or now),
                    now,
                    raw,
                ),
            )
    return data


def codex_assistant_agent_plan_get(
    info_base: str,
    client_id: str,
    plan_id: str = "",
    *,
    task_id: str = "",
    idempotency_key: str = "",
) -> Optional[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if plan_id:
        clauses.append("plan_id = ?")
        params.append(_safe_id(plan_id, ""))
    elif task_id:
        clauses.append("task_id = ?")
        params.append(str(task_id))
    elif idempotency_key:
        clauses.append("idempotency_key = ?")
        params.append(str(idempotency_key))
    else:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_agent_plans WHERE " + " AND ".join(clauses) + " LIMIT 1",
                params,
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return payload if isinstance(payload, dict) else None


def codex_assistant_agent_guidance_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    updated_by: str = "",
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    scope_type = str(data.get("scope_type") or "global").strip().lower()
    scope_key = str(data.get("scope_key") or "").strip()
    guidance_id = _safe_id(data.get("guidance_id"), "")
    if not guidance_id:
        guidance_id = _safe_id(f"{scope_type}_{scope_key or 'default'}", "global_default")
    now = _now_iso()
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT MAX(version) AS version FROM assistant_agent_guidance WHERE guidance_id = ?",
                (guidance_id,),
            ).fetchone()
            latest = int(row["version"] or 0) if row else 0
            requested = int(data.get("version") or 0)
            version = max(latest + 1, requested if requested > latest else latest + 1)
            data.update(
                {
                    "guidance_id": guidance_id,
                    "version": version,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "active": data.get("active") is not False,
                    "updated_at": now,
                    "updated_by": str(updated_by or data.get("updated_by") or ""),
                }
            )
            data.setdefault("created_at", now)
            raw = _json_dumps(data)
            conn.execute(
                """
                INSERT INTO assistant_agent_guidance(
                    guidance_id, version, scope_type, scope_key, active,
                    created_at, updated_at, updated_by, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guidance_id,
                    version,
                    scope_type,
                    scope_key,
                    1 if data["active"] else 0,
                    str(data.get("created_at") or now),
                    now,
                    data["updated_by"],
                    raw,
                ),
            )
    return data


def codex_assistant_agent_guidance_list(
    info_base: str,
    client_id: str,
    *,
    active_only: bool = False,
    latest_only: bool = True,
) -> list[dict[str, Any]]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            where = "WHERE g.active = 1" if active_only else ""
            if latest_only:
                rows = conn.execute(
                    """
                    SELECT g.payload_json
                    FROM assistant_agent_guidance g
                    JOIN (
                        SELECT guidance_id, MAX(version) AS version
                        FROM assistant_agent_guidance
                        GROUP BY guidance_id
                    ) latest
                      ON latest.guidance_id = g.guidance_id AND latest.version = g.version
                    """
                    + where
                    + " ORDER BY g.scope_type, g.scope_key, g.guidance_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload_json FROM assistant_agent_guidance g "
                    + where
                    + " ORDER BY g.guidance_id, g.version DESC"
                ).fetchall()
    return [item for row in rows if isinstance((item := _json_loads(row["payload_json"], {})), dict)]
