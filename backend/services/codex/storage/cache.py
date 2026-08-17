"""Volatile assistant cache persistence and legacy migration."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .common import (
    _connection,
    _json_dumps,
    _json_loads,
    _lock_for,
    _meta_get,
    _meta_set,
    _now_iso,
    _read_json_file,
    _safe_id,
    _sha_text,
    codex_assistant_cache_db_path,
    codex_assistant_client_dir,
)
from .schema import _ensure_cache_schema


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
