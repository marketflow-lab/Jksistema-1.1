"""Assistant report persistence and legacy metadata migration."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .common import (
    _connection,
    _file_sha256,
    _json_dumps,
    _json_loads,
    _lock_for,
    _meta_set,
    _now_iso,
    _read_json_file,
    _safe_id,
    _sha_text,
    codex_assistant_client_dir,
    codex_assistant_state_db_path,
)
from .schema import _ensure_state_schema


def _report_row_to_payload(row: sqlite3.Row | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    payload = _json_loads(row["payload_json"], None)
    return payload if isinstance(payload, dict) else None


def _report_upsert_conn(conn: sqlite3.Connection, report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    report_id = _safe_id(report.get("report_id"), "")
    if not report_id:
        return {}
    payload = dict(report)
    payload["report_id"] = report_id
    payload_json = _json_dumps(payload)
    formats_json = _json_dumps(payload.get("formats") if isinstance(payload.get("formats"), dict) else {})
    downloads_json = _json_dumps(payload.get("downloads") if isinstance(payload.get("downloads"), dict) else {})
    conn.execute(
        """
        INSERT OR REPLACE INTO assistant_reports(
            report_id, title, prompt, created_at, thread_id, conversation_id,
            formats_json, downloads_json, chat_text, payload_json, payload_hash, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            str(payload.get("title") or ""),
            str(payload.get("prompt") or ""),
            str(payload.get("created_at") or ""),
            str(payload.get("thread_id") or ""),
            str(payload.get("conversation_id") or ""),
            formats_json,
            downloads_json,
            str(payload.get("chat_text") or ""),
            payload_json,
            _sha_text(payload_json),
            _now_iso(),
        ),
    )
    return payload


def _legacy_report_metadata_path(info_base: str, client_id: str, report_id: str) -> str:
    return os.path.join(
        codex_assistant_client_dir(info_base, client_id),
        "reports",
        _safe_id(report_id, "report"),
        "metadata.json",
    )


def _report_import_legacy_file_conn(
    conn: sqlite3.Connection,
    metadata_path: str,
    report_id_fallback: str,
) -> Optional[dict[str, Any]]:
    data = _read_json_file(metadata_path, None)
    if not isinstance(data, dict):
        return None
    report_id = str(data.get("report_id") or report_id_fallback or "").strip()
    if not report_id:
        return None
    data["report_id"] = _safe_id(report_id, "report")
    payload = _report_upsert_conn(conn, data)
    if payload:
        try:
            _meta_set(conn, f"legacy_report_sha256:{payload['report_id']}", _file_sha256(metadata_path))
        except Exception:
            pass
    return payload or None


def codex_assistant_reports_import_legacy(
    info_base: str,
    client_id: str,
    limit_scan: int = 300,
) -> int:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    reports_dir = Path(codex_assistant_client_dir(info_base, client_id)) / "reports"
    lock = _lock_for(db_path)
    imported = 0
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if reports_dir.exists():
                try:
                    report_dirs = sorted(
                        [item for item in reports_dir.iterdir() if item.is_dir()],
                        key=lambda item: item.stat().st_mtime,
                        reverse=True,
                    )[: max(1, int(limit_scan or 300))]
                except Exception:
                    report_dirs = []
                for report_dir in report_dirs:
                    report_id = _safe_id(report_dir.name, "")
                    if not report_id:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM assistant_reports WHERE report_id = ?",
                        (report_id,),
                    ).fetchone()
                    if exists:
                        continue
                    metadata_path = report_dir / "metadata.json"
                    if not metadata_path.exists():
                        continue
                    if _report_import_legacy_file_conn(conn, str(metadata_path), report_id):
                        imported += 1
            _meta_set(conn, "legacy_reports_imported_at", _now_iso())
            _meta_set(conn, "legacy_reports_imported_count", imported)
    return imported


def codex_assistant_report_save(info_base: str, client_id: str, report: dict[str, Any]) -> dict[str, Any]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            return _report_upsert_conn(conn, report)


def codex_assistant_report_get(info_base: str, client_id: str, report_id: str) -> Optional[dict[str, Any]]:
    safe_report_id = _safe_id(report_id, "")
    if not safe_report_id:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_reports WHERE report_id = ?",
                (safe_report_id,),
            ).fetchone()
            payload = _report_row_to_payload(row)
            if payload is not None:
                return payload
            metadata_path = _legacy_report_metadata_path(info_base, client_id, safe_report_id)
            payload = _report_import_legacy_file_conn(conn, metadata_path, safe_report_id)
            return payload


def codex_assistant_reports_list(
    info_base: str,
    client_id: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    codex_assistant_reports_import_legacy(info_base, client_id, max(100, int(limit or 30) * 4))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                """
                SELECT payload_json FROM assistant_reports
                ORDER BY COALESCE(created_at, updated_at) DESC, updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit or 30)),),
            ).fetchall()
            payloads: list[dict[str, Any]] = []
            for row in rows:
                payload = _report_row_to_payload(row)
                if isinstance(payload, dict):
                    payloads.append(payload)
            return payloads
