"""Shared SQLite paths, serialization, locking, and metadata helpers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional


SCHEMA_VERSION = "5"


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
