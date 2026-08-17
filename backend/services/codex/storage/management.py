"""Report settings, financial adjustments, and action queue storage."""

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


def codex_assistant_report_settings_get(info_base: str, client_id: str, name: str = "default") -> dict[str, Any]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_report_settings WHERE name = ?",
                (str(name or "default"),),
            ).fetchone()
            payload = _json_loads(row["payload_json"] if row else "", {})
            return payload if isinstance(payload, dict) else {}


def codex_assistant_report_settings_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    updated_by: str = "",
    name: str = "default",
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    data["updated_at"] = _now_iso()
    if updated_by:
        data["updated_by"] = str(updated_by)
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO assistant_report_settings(name, payload_json, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                """,
                (str(name or "default"), raw, data["updated_at"], str(updated_by or "")),
            )
    return data


def codex_assistant_financial_adjustment_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    created_by: str = "",
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    adjustment_id = _safe_id(data.get("adjustment_id"), "")
    if not adjustment_id:
        adjustment_id = hashlib.sha256(
            f"{time.time_ns()}|{client_id}|{data.get('kind')}|{data.get('store')}".encode("utf-8")
        ).hexdigest()[:24]
    data["adjustment_id"] = adjustment_id
    data.setdefault("created_at", _now_iso())
    data["created_by"] = str(created_by or data.get("created_by") or "")
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO assistant_financial_adjustments(
                    adjustment_id, kind, store, period_start, period_end, amount, platform,
                    note, created_at, created_by, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    adjustment_id,
                    str(data.get("kind") or "advertising"),
                    str(data.get("store") or ""),
                    str(data.get("period_start") or ""),
                    str(data.get("period_end") or ""),
                    float(data.get("amount") or 0),
                    str(data.get("platform") or ""),
                    str(data.get("note") or ""),
                    str(data.get("created_at") or ""),
                    data["created_by"],
                    raw,
                ),
            )
    return data


def codex_assistant_financial_adjustments_list(
    info_base: str,
    client_id: str,
    *,
    kind: str = "advertising",
    store: str = "",
    period_start: str = "",
    period_end: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses = ["kind = ?"]
    params: list[Any] = [str(kind or "advertising")]
    if store:
        clauses.append("LOWER(store) = LOWER(?)")
        params.append(str(store))
    if period_start:
        clauses.append("COALESCE(period_end, '') >= ?")
        params.append(str(period_start))
    if period_end:
        clauses.append("COALESCE(period_start, '') <= ?")
        params.append(str(period_end))
    params.append(max(1, min(5000, int(limit or 500))))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT payload_json FROM assistant_financial_adjustments WHERE "
                + " AND ".join(clauses)
                + " ORDER BY period_start DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
    return [item for row in rows if isinstance((item := _json_loads(row["payload_json"], {})), dict)]


def codex_assistant_financial_adjustment_delete(info_base: str, client_id: str, adjustment_id: str) -> bool:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            cursor = conn.execute(
                "DELETE FROM assistant_financial_adjustments WHERE adjustment_id = ?",
                (_safe_id(adjustment_id, ""),),
            )
            return bool(cursor.rowcount)


def codex_assistant_action_queue_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    action_id = _safe_id(data.get("action_id"), "")
    if not action_id:
        action_id = hashlib.sha256(
            f"{time.time_ns()}|{client_id}|{data.get('report_id')}|{data.get('action_type')}".encode("utf-8")
        ).hexdigest()[:24]
    now = _now_iso()
    data["action_id"] = action_id
    data.setdefault("created_at", now)
    data["updated_at"] = now
    data.setdefault("created_by", str(actor or ""))
    raw = _json_dumps(data)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO assistant_action_queue(
                    action_id, report_id, action_type, status, store, owner_username,
                    owner_role, due_at, impact_brl, confidence, created_at, updated_at,
                    created_by, approved_by, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    str(data.get("report_id") or ""),
                    str(data.get("action_type") or "review"),
                    str(data.get("status") or "queued"),
                    str(data.get("store") or ""),
                    str(data.get("owner_username") or ""),
                    str(data.get("owner_role") or ""),
                    str(data.get("due_at") or ""),
                    float(data["impact_brl"]) if data.get("impact_brl") is not None else None,
                    str(data.get("confidence") or ""),
                    str(data.get("created_at") or now),
                    now,
                    str(data.get("created_by") or actor or ""),
                    str(data.get("approved_by") or ""),
                    raw,
                ),
            )
    return data


def codex_assistant_action_queue_list(
    info_base: str,
    client_id: str,
    *,
    status: str = "",
    action_type: str = "",
    owner_username: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    for column, value in (("status", status), ("action_type", action_type), ("owner_username", owner_username)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(str(value))
    params.append(max(1, min(5000, int(limit or 500))))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT payload_json FROM assistant_action_queue WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
    return [item for row in rows if isinstance((item := _json_loads(row["payload_json"], {})), dict)]


def codex_assistant_action_queue_get(info_base: str, client_id: str, action_id: str) -> Optional[dict[str, Any]]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_action_queue WHERE action_id = ?",
                (_safe_id(action_id, ""),),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return payload if isinstance(payload, dict) else None
