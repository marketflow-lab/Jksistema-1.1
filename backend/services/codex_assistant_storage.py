"""SQLite persistence for Codex assistant volatile cache and report state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = "1"
STATE_ROW_SCHEDULER = "scheduler_state"

_LOCKS_LOCK = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}


def _safe_id(value: Any, fallback: str = "default") -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe or fallback


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_loads(raw: Any, fallback: Any) -> Any:
    try:
        if not isinstance(raw, str) or not raw:
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback


def _sha_text(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _read_json_file(path: str, fallback: Any) -> Any:
    try:
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return fallback


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _info_base(info_base: str) -> str:
    base = str(info_base or "info").strip() or "info"
    if not os.path.isabs(base):
        base = os.path.abspath(base)
    os.makedirs(base, exist_ok=True)
    return base


def codex_assistant_client_dir(info_base: str, client_id: str) -> str:
    path = os.path.join(_info_base(info_base), _safe_id(client_id), "codex_assistant")
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "cache"), exist_ok=True)
    os.makedirs(os.path.join(path, "reports"), exist_ok=True)
    return path


def codex_assistant_cache_db_path(info_base: str, client_id: str) -> str:
    cache_dir = os.path.join(codex_assistant_client_dir(info_base, client_id), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "cache.db")


def codex_assistant_state_db_path(info_base: str, client_id: str) -> str:
    return os.path.join(codex_assistant_client_dir(info_base, client_id), "codex_assistant.db")


def _lock_for(path: str) -> threading.RLock:
    key = os.path.abspath(path)
    with _LOCKS_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _connection(path: str):
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM assistant_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO assistant_meta(key, value, updated_at)
        VALUES (?, ?, ?)
        """,
        (key, str(value), _now_iso()),
    )


def _ensure_meta_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _meta_set(conn, "schema_version", SCHEMA_VERSION)


def _ensure_cache_schema(conn: sqlite3.Connection) -> None:
    _ensure_meta_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_cache_entries (
            key TEXT PRIMARY KEY,
            created_ts REAL NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            last_accessed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_cache_created_ts "
        "ON assistant_cache_entries(created_ts)"
    )


def _ensure_state_schema(conn: sqlite3.Connection) -> None:
    _ensure_meta_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_scheduler_state (
            name TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_reports (
            report_id TEXT PRIMARY KEY,
            title TEXT,
            prompt TEXT,
            created_at TEXT,
            thread_id TEXT,
            conversation_id TEXT,
            formats_json TEXT,
            downloads_json TEXT,
            chat_text TEXT,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_reports_created_at "
        "ON assistant_reports(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_reports_conversation "
        "ON assistant_reports(conversation_id, thread_id)"
    )


def _cache_insert_conn(conn: sqlite3.Connection, key: str, created_ts: float, payload: dict[str, Any]) -> None:
    payload_json = _json_dumps(payload)
    conn.execute(
        """
        INSERT OR REPLACE INTO assistant_cache_entries(
            key, created_ts, payload_json, payload_hash, size_bytes, last_accessed_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(key),
            float(created_ts),
            payload_json,
            _sha_text(payload_json),
            len(payload_json.encode("utf-8", "replace")),
            _now_iso(),
        ),
    )


def _legacy_cache_file(info_base: str, client_id: str, key: str) -> str:
    return os.path.join(codex_assistant_client_dir(info_base, client_id), "cache", f"{_safe_id(key, 'cache')}.json")


def _cache_import_legacy_conn(
    conn: sqlite3.Connection,
    info_base: str,
    client_id: str,
    ttl_seconds: int,
) -> None:
    ttl = max(1, int(ttl_seconds or 1))
    try:
        imported_ttl = int(_meta_get(conn, "legacy_cache_imported_ttl_seconds") or 0)
    except Exception:
        imported_ttl = 0
    if _meta_get(conn, "legacy_cache_imported_at") and imported_ttl >= ttl:
        return
    cache_dir = os.path.join(codex_assistant_client_dir(info_base, client_id), "cache")
    now = time.time()
    imported = 0
    for item in Path(cache_dir).glob("*.json"):
        data = _read_json_file(str(item), None)
        if not isinstance(data, dict):
            continue
        created = float(data.get("_created_ts") or 0)
        payload = data.get("payload")
        if created <= 0 or now - created > ttl or not isinstance(payload, dict):
            continue
        _cache_insert_conn(conn, item.stem, created, payload)
        imported += 1
    _meta_set(conn, "legacy_cache_imported_at", _now_iso())
    _meta_set(conn, "legacy_cache_imported_count", imported)
    _meta_set(conn, "legacy_cache_imported_ttl_seconds", ttl)


def _cache_try_import_single_legacy(
    conn: sqlite3.Connection,
    info_base: str,
    client_id: str,
    key: str,
    ttl_seconds: int,
) -> None:
    path = _legacy_cache_file(info_base, client_id, key)
    data = _read_json_file(path, None)
    if not isinstance(data, dict):
        return
    created = float(data.get("_created_ts") or 0)
    payload = data.get("payload")
    if created <= 0 or time.time() - created > max(1, int(ttl_seconds or 1)) or not isinstance(payload, dict):
        return
    _cache_insert_conn(conn, key, created, payload)


def codex_assistant_cache_get(
    info_base: str,
    client_id: str,
    key: str,
    ttl_seconds: int,
) -> Optional[dict[str, Any]]:
    db_path = codex_assistant_cache_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_cache_schema(conn)
            _cache_import_legacy_conn(conn, info_base, client_id, ttl_seconds)
            row = conn.execute(
                "SELECT created_ts, payload_json FROM assistant_cache_entries WHERE key = ?",
                (str(key),),
            ).fetchone()
            if row is None:
                _cache_try_import_single_legacy(conn, info_base, client_id, key, ttl_seconds)
                row = conn.execute(
                    "SELECT created_ts, payload_json FROM assistant_cache_entries WHERE key = ?",
                    (str(key),),
                ).fetchone()
            if row is None:
                return None
            created = float(row["created_ts"] or 0)
            if created <= 0 or time.time() - created > max(1, int(ttl_seconds or 1)):
                conn.execute("DELETE FROM assistant_cache_entries WHERE key = ?", (str(key),))
                return None
            payload = _json_loads(row["payload_json"], None)
            if not isinstance(payload, dict):
                conn.execute("DELETE FROM assistant_cache_entries WHERE key = ?", (str(key),))
                return None
            conn.execute(
                "UPDATE assistant_cache_entries SET last_accessed_at = ? WHERE key = ?",
                (_now_iso(), str(key)),
            )
            return payload


def codex_assistant_cache_set(
    info_base: str,
    client_id: str,
    key: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    if not isinstance(payload, dict):
        return
    db_path = codex_assistant_cache_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_cache_schema(conn)
            _cache_import_legacy_conn(conn, info_base, client_id, ttl_seconds)
            _cache_insert_conn(conn, str(key), time.time(), payload)
            cutoff = time.time() - max(1, int(ttl_seconds or 1))
            conn.execute("DELETE FROM assistant_cache_entries WHERE created_ts < ?", (cutoff,))


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


def _scheduler_legacy_path(info_base: str, client_id: str) -> str:
    return os.path.join(codex_assistant_client_dir(info_base, client_id), "scheduler_state.json")


def _scheduler_light_payload_conn(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    report = data.get("last_daily_report")
    if isinstance(report, dict):
        saved = _report_upsert_conn(conn, report)
        report_id = str(saved.get("report_id") or report.get("report_id") or "").strip()
        if report_id:
            data["last_daily_report_id"] = report_id
            data.pop("last_daily_report", None)
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
    if not isinstance(data.get("last_daily_report"), dict):
        report_id = str(data.get("last_daily_report_id") or "").strip()
        if report_id:
            report = codex_assistant_report_get(info_base, client_id, report_id)
            if isinstance(report, dict):
                data["last_daily_report"] = report
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
