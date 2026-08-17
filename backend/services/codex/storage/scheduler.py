"""Assistant scheduler state persistence and report rehydration."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from .common import (
    STATE_ROW_SCHEDULER,
    _connection,
    _json_dumps,
    _json_loads,
    _lock_for,
    _meta_set,
    _now_iso,
    _read_json_file,
    _sha_text,
    codex_assistant_client_dir,
    codex_assistant_state_db_path,
)
from .reports import (
    _report_upsert_conn,
    codex_assistant_report_get,
)
from .schema import _ensure_state_schema


def _scheduler_legacy_path(info_base: str, client_id: str) -> str:
    return os.path.join(codex_assistant_client_dir(info_base, client_id), "scheduler_state.json")


def _scheduler_light_payload_conn(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    for prefix in ("daily", "weekly"):
        report_key = f"last_{prefix}_report"
        report = data.get(report_key)
        if isinstance(report, dict):
            saved = _report_upsert_conn(conn, report)
            report_id = str(saved.get("report_id") or report.get("report_id") or "").strip()
            if report_id:
                data[f"last_{prefix}_report_id"] = report_id
                data.pop(report_key, None)
    return data


def _scheduler_upsert_conn(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    data = _scheduler_light_payload_conn(conn, payload)
    payload_json = _json_dumps(data)
    conn.execute(
        """
        INSERT OR REPLACE INTO assistant_scheduler_state(
            name, payload_json, payload_hash, size_bytes, updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            STATE_ROW_SCHEDULER,
            payload_json,
            _sha_text(payload_json),
            len(payload_json.encode("utf-8", "replace")),
            _now_iso(),
        ),
    )
    return data


def _scheduler_import_legacy_conn(conn: sqlite3.Connection, info_base: str, client_id: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM assistant_scheduler_state WHERE name = ?",
        (STATE_ROW_SCHEDULER,),
    ).fetchone()
    if exists:
        return
    data = _read_json_file(_scheduler_legacy_path(info_base, client_id), {})
    if not isinstance(data, dict):
        data = {}
    _scheduler_upsert_conn(conn, data)
    _meta_set(conn, "legacy_scheduler_imported_at", _now_iso())


def _scheduler_rehydrate(info_base: str, client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    for prefix in ("daily", "weekly"):
        report_key = f"last_{prefix}_report"
        if not isinstance(data.get(report_key), dict):
            report_id = str(data.get(f"last_{prefix}_report_id") or "").strip()
            if report_id:
                report = codex_assistant_report_get(info_base, client_id, report_id)
                if isinstance(report, dict):
                    data[report_key] = report
    return data


def codex_assistant_scheduler_get(info_base: str, client_id: str) -> dict[str, Any]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            _scheduler_import_legacy_conn(conn, info_base, client_id)
            row = conn.execute(
                "SELECT payload_json FROM assistant_scheduler_state WHERE name = ?",
                (STATE_ROW_SCHEDULER,),
            ).fetchone()
            payload = _json_loads(row["payload_json"] if row else "", {})
    return _scheduler_rehydrate(info_base, client_id, payload if isinstance(payload, dict) else {})


def codex_assistant_scheduler_save(info_base: str, client_id: str, payload: dict[str, Any]) -> None:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            _scheduler_upsert_conn(conn, payload if isinstance(payload, dict) else {})
