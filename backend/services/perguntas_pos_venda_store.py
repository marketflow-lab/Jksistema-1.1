"""Persistent tenant-local store for Mercado Livre post-sale conversations.

The collector is intentionally kept outside this module.  This file owns only
the durable, idempotent SQLite contract used by the HTTP endpoint, counters and
background synchronization jobs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


DB_FILENAME = "perguntas_pos_venda_cache.db"
_SQLITE_BUSY_TIMEOUT_MS = 15_000
ML_MAX_VALID_ORDER_OFFSET = 9_999

_LOCKS_GUARD = threading.RLock()
_LOCKS_BY_PATH: dict[str, threading.RLock] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _database_path(tenant_path: str | os.PathLike[str]) -> str:
    raw = os.fspath(tenant_path) if tenant_path is not None else ""
    if not str(raw).strip():
        raise ValueError("tenant_path nao informado")
    return os.path.abspath(os.path.join(raw, DB_FILENAME))


def _lock_for_path(path: str) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(path))
    with _LOCKS_GUARD:
        lock = _LOCKS_BY_PATH.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS_BY_PATH[key] = lock
        return lock


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            store_key TEXT NOT NULL,
            store_name TEXT NOT NULL,
            seller_id TEXT NOT NULL,
            conversation_key TEXT NOT NULL,
            pack_id TEXT NOT NULL DEFAULT '',
            order_id TEXT NOT NULL DEFAULT '',
            date_created TEXT NOT NULL DEFAULT '',
            date_closed TEXT NOT NULL DEFAULT '',
            last_message_date TEXT NOT NULL DEFAULT '',
            last_message_id TEXT NOT NULL DEFAULT '',
            activity_epoch REAL NOT NULL DEFAULT 0,
            unread INTEGER NOT NULL DEFAULT 0,
            unread_count INTEGER NOT NULL DEFAULT 0,
            search_text TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (store_key, seller_id, conversation_key)
        );

        CREATE INDEX IF NOT EXISTS idx_ppv_conversations_activity
            ON conversations (store_key, seller_id, activity_epoch DESC, conversation_key);
        CREATE INDEX IF NOT EXISTS idx_ppv_conversations_unread
            ON conversations (store_key, seller_id, unread, activity_epoch DESC);

        CREATE TABLE IF NOT EXISTS sync_state (
            store_key TEXT NOT NULL,
            store_name TEXT NOT NULL,
            seller_id TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle',
            coverage_days INTEGER NOT NULL DEFAULT 0,
            cursor INTEGER NOT NULL DEFAULT 0,
            conversations_saved INTEGER NOT NULL DEFAULT 0,
            orders_scanned INTEGER NOT NULL DEFAULT 0,
            orders_total INTEGER NOT NULL DEFAULT 0,
            bootstrap_complete INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (store_key, seller_id)
        );
        """
    )


@contextmanager
def _connection(
    tenant_path: str | os.PathLike[str],
    *,
    writable: bool = False,
) -> Iterator[sqlite3.Connection]:
    path = _database_path(tenant_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _lock_for_path(path):
        conn = sqlite3.connect(path, timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        try:
            _configure_connection(conn)
            _initialize_schema(conn)
            conn.commit()
            yield conn
            if writable:
                conn.commit()
        except Exception:
            if writable:
                conn.rollback()
            raise
        finally:
            conn.close()


def _store_identity(loja: Any, seller_id: Any) -> tuple[str, str, str]:
    store_name = re.sub(r"\s+", " ", str(loja or "").strip())
    seller = str(seller_id or "").strip()
    if not store_name:
        raise ValueError("loja nao informada")
    if not seller:
        raise ValueError("seller_id nao informado")
    normalized = unicodedata.normalize("NFKD", store_name).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    store_key = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not store_key:
        raise ValueError("loja invalida")
    return store_key, store_name, seller


def _as_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "sim",
        "unread",
        "nao_lida",
        "nao-lida",
    }


def _parse_epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _conversation_activity(conversation: dict[str, Any]) -> tuple[str, float]:
    for field in ("last_message_date", "date_created", "date_closed"):
        value = str(conversation.get(field) or "").strip()
        if value:
            return value, _parse_epoch(value)
    return "", 0.0


def _conversation_unread(conversation: dict[str, Any]) -> tuple[bool, int]:
    unread_count = _as_non_negative_int(
        conversation.get("unread_count") or conversation.get("mensagens_nao_lidas")
    )
    unread = unread_count > 0 or any(
        _as_bool(conversation.get(field))
        for field in ("unread", "is_unread", "nao_lida", "has_unread", "has_unread_messages")
    )
    return unread, max(unread_count, 1 if unread else 0)


def _search_normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return "".join(char for char in text if char.isalnum())


def _conversation_search_text(conversation: dict[str, Any]) -> str:
    values: list[Any] = [
        conversation.get("pack_id"),
        conversation.get("order_id"),
        conversation.get("buyer_id"),
        conversation.get("buyer_name"),
        conversation.get("buyer_nickname"),
        conversation.get("item_title"),
        conversation.get("last_message_text"),
        conversation.get("claim_id"),
        conversation.get("claim_reason_id"),
        conversation.get("claim_reason_name"),
        conversation.get("claim_reason_detail"),
    ]
    for item in conversation.get("items") or []:
        if isinstance(item, dict):
            values.extend((item.get("id"), item.get("sku"), item.get("title")))
    return _search_normalize(" ".join(str(value or "") for value in values))


def _conversation_key(conversation: dict[str, Any]) -> tuple[str, str, str]:
    pack_id = str(conversation.get("pack_id") or "").strip()
    order_id = str(conversation.get("order_id") or "").strip()
    return pack_id or order_id, pack_id, order_id


def upsert_conversations(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    conversations: Sequence[dict[str, Any]],
) -> int:
    """Insert or refresh normalized conversations without allowing stale overwrite."""

    store_key, store_name, seller = _store_identity(loja, seller_id)
    saved = 0
    cached_at = _utc_now_iso()
    with _connection(tenant_path, writable=True) as conn:
        for conversation in conversations or ():
            if not isinstance(conversation, dict):
                continue
            conversation_key, pack_id, order_id = _conversation_key(conversation)
            if not conversation_key:
                continue
            _activity_text, activity_epoch = _conversation_activity(conversation)
            unread, unread_count = _conversation_unread(conversation)
            payload_json = json.dumps(
                conversation,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            conn.execute(
                """
                INSERT INTO conversations (
                    store_key, store_name, seller_id, conversation_key,
                    pack_id, order_id, date_created, date_closed,
                    last_message_date, last_message_id, activity_epoch,
                    unread, unread_count, search_text, payload_json, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_key, seller_id, conversation_key) DO UPDATE SET
                    store_name=excluded.store_name,
                    pack_id=CASE WHEN excluded.pack_id <> '' THEN excluded.pack_id ELSE conversations.pack_id END,
                    order_id=CASE WHEN excluded.order_id <> '' THEN excluded.order_id ELSE conversations.order_id END,
                    date_created=excluded.date_created,
                    date_closed=excluded.date_closed,
                    last_message_date=excluded.last_message_date,
                    last_message_id=excluded.last_message_id,
                    activity_epoch=excluded.activity_epoch,
                    unread=excluded.unread,
                    unread_count=excluded.unread_count,
                    search_text=excluded.search_text,
                    payload_json=excluded.payload_json,
                    cached_at=excluded.cached_at
                WHERE conversations.activity_epoch <= excluded.activity_epoch
                   OR conversations.activity_epoch <= 0
                """,
                (
                    store_key,
                    store_name,
                    seller,
                    conversation_key,
                    pack_id,
                    order_id,
                    str(conversation.get("date_created") or "").strip(),
                    str(conversation.get("date_closed") or "").strip(),
                    str(conversation.get("last_message_date") or "").strip(),
                    str(conversation.get("last_message_id") or "").strip(),
                    activity_epoch,
                    int(unread),
                    unread_count,
                    _conversation_search_text(conversation),
                    payload_json,
                    cached_at,
                ),
            )
            saved += 1
    return saved


def _conversation_filters(
    store_key: str,
    seller_id: str,
    days: int,
    search: str,
    unread_only: bool,
) -> tuple[str, list[Any]]:
    coverage_days = max(1, _as_non_negative_int(days) or 1)
    cutoff = (_utc_now() - timedelta(days=coverage_days)).timestamp()
    clauses = ["store_key = ?", "seller_id = ?", "activity_epoch >= ?"]
    params: list[Any] = [store_key, seller_id, cutoff]
    normalized_search = _search_normalize(search)
    if normalized_search:
        clauses.append("search_text LIKE ?")
        params.append(f"%{normalized_search}%")
    if unread_only:
        clauses.append("unread = 1")
    return " AND ".join(clauses), params


def list_conversations(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    days: int,
    offset: int,
    limit: int,
    search: str = "",
    unread_only: bool = False,
) -> dict[str, Any]:
    store_key, _store_name, seller = _store_identity(loja, seller_id)
    page_offset = _as_non_negative_int(offset)
    page_limit = max(1, min(_as_non_negative_int(limit) or 20, 500))
    where_sql, params = _conversation_filters(store_key, seller, days, search, unread_only)
    with _connection(tenant_path) as conn:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM conversations WHERE {where_sql}",
                params,
            ).fetchone()[0]
            or 0
        )
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM conversations
            WHERE {where_sql}
            ORDER BY activity_epoch DESC, conversation_key DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_limit, page_offset],
        ).fetchall()
    conversations: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            conversations.append(payload)
    consumed = page_offset + len(conversations)
    has_next = consumed < total
    return {
        "conversations": conversations,
        "total": total,
        "next_offset": consumed if has_next else None,
        "has_next": has_next,
    }


def summary(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    days: int,
) -> dict[str, Any]:
    store_key, _store_name, seller = _store_identity(loja, seller_id)
    where_sql, params = _conversation_filters(store_key, seller, days, "", False)
    with _connection(tenant_path) as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN unread = 1 THEN 1 ELSE 0 END), 0) AS unread_total,
                COALESCE(MAX(last_message_date), '') AS last_message_date
            FROM conversations
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
    total = int(row["total"] or 0)
    unread_total = int(row["unread_total"] or 0)
    return {
        "days": max(1, _as_non_negative_int(days) or 1),
        "total": total,
        "conversations_total": total,
        "unread_total": unread_total,
        "conversations_unread_total": unread_total,
        "last_message_date": str(row["last_message_date"] or ""),
    }


def _default_state(store_name: str, seller_id: str) -> dict[str, Any]:
    return {
        "loja": store_name,
        "seller_id": seller_id,
        "mode": "",
        "status": "idle",
        "coverage_days": 0,
        "cursor": 0,
        "conversations_saved": 0,
        "orders_scanned": 0,
        "orders_total": 0,
        "bootstrap_complete": False,
        "started_at": "",
        "updated_at": "",
        "finished_at": "",
        "error": "",
    }


def _state_from_row(row: sqlite3.Row | None, store_name: str, seller_id: str) -> dict[str, Any]:
    if row is None:
        return _default_state(store_name, seller_id)
    return {
        "loja": str(row["store_name"] or store_name),
        "seller_id": str(row["seller_id"] or seller_id),
        "mode": str(row["mode"] or ""),
        "status": str(row["status"] or "idle"),
        "coverage_days": int(row["coverage_days"] or 0),
        "cursor": int(row["cursor"] or 0),
        "conversations_saved": int(row["conversations_saved"] or 0),
        "orders_scanned": int(row["orders_scanned"] or 0),
        "orders_total": int(row["orders_total"] or 0),
        "bootstrap_complete": bool(row["bootstrap_complete"]),
        "started_at": str(row["started_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "finished_at": str(row["finished_at"] or ""),
        "error": str(row["error"] or ""),
    }


def _get_state_conn(
    conn: sqlite3.Connection,
    store_key: str,
    store_name: str,
    seller_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sync_state WHERE store_key = ? AND seller_id = ?",
        (store_key, seller_id),
    ).fetchone()
    return _state_from_row(row, store_name, seller_id)


def _save_state_conn(
    conn: sqlite3.Connection,
    store_key: str,
    state: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (
            store_key, store_name, seller_id, mode, status, coverage_days,
            cursor, conversations_saved, orders_scanned, orders_total,
            bootstrap_complete, started_at, updated_at, finished_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(store_key, seller_id) DO UPDATE SET
            store_name=excluded.store_name,
            mode=excluded.mode,
            status=excluded.status,
            coverage_days=excluded.coverage_days,
            cursor=excluded.cursor,
            conversations_saved=excluded.conversations_saved,
            orders_scanned=excluded.orders_scanned,
            orders_total=excluded.orders_total,
            bootstrap_complete=excluded.bootstrap_complete,
            started_at=excluded.started_at,
            updated_at=excluded.updated_at,
            finished_at=excluded.finished_at,
            error=excluded.error
        """,
        (
            store_key,
            state["loja"],
            state["seller_id"],
            state["mode"],
            state["status"],
            state["coverage_days"],
            state["cursor"],
            state["conversations_saved"],
            state["orders_scanned"],
            state["orders_total"],
            int(bool(state["bootstrap_complete"])),
            state["started_at"],
            state["updated_at"],
            state["finished_at"],
            state["error"],
        ),
    )


def get_state(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
) -> dict[str, Any]:
    store_key, store_name, seller = _store_identity(loja, seller_id)
    with _connection(tenant_path) as conn:
        return _get_state_conn(conn, store_key, store_name, seller)


def begin_sync(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    cursor: int = 0,
) -> dict[str, Any]:
    store_key, store_name, seller = _store_identity(loja, seller_id)
    mode_value = str(mode or "").strip().lower() or "incremental"
    now = _utc_now_iso()
    with _connection(tenant_path, writable=True) as conn:
        current = _get_state_conn(conn, store_key, store_name, seller)
        resume = current["mode"] == mode_value and current["status"] in {"running", "failed"}
        state = {
            **current,
            "loja": store_name,
            "seller_id": seller,
            "mode": mode_value,
            "status": "running",
            "coverage_days": max(1, _as_non_negative_int(coverage_days) or 1),
            "cursor": max(current["cursor"], _as_non_negative_int(cursor)) if resume else _as_non_negative_int(cursor),
            "conversations_saved": current["conversations_saved"] if resume else 0,
            "orders_scanned": current["orders_scanned"] if resume else 0,
            "orders_total": current["orders_total"] if resume else 0,
            "started_at": current["started_at"] if resume and current["started_at"] else now,
            "updated_at": now,
            "finished_at": "",
            "error": "",
        }
        _save_state_conn(conn, store_key, state)
    return state


def update_sync_progress(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    cursor: int,
    conversations_saved: int,
    orders_scanned: int,
) -> dict[str, Any]:
    store_key, store_name, seller = _store_identity(loja, seller_id)
    with _connection(tenant_path, writable=True) as conn:
        state = _get_state_conn(conn, store_key, store_name, seller)
        state.update({
            "loja": store_name,
            "seller_id": seller,
            "status": "running",
            "cursor": max(state["cursor"], _as_non_negative_int(cursor)),
            "conversations_saved": max(
                state["conversations_saved"], _as_non_negative_int(conversations_saved)
            ),
            "orders_scanned": max(state["orders_scanned"], _as_non_negative_int(orders_scanned)),
            "updated_at": _utc_now_iso(),
            "finished_at": "",
            "error": "",
        })
        _save_state_conn(conn, store_key, state)
    return state


def finish_sync(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    bootstrap_complete: bool = False,
    cursor: int = 0,
    orders_total: int = 0,
) -> dict[str, Any]:
    store_key, store_name, seller = _store_identity(loja, seller_id)
    mode_value = str(mode or "").strip().lower() or "incremental"
    now = _utc_now_iso()
    with _connection(tenant_path, writable=True) as conn:
        state = _get_state_conn(conn, store_key, store_name, seller)
        same_mode = state["mode"] == mode_value
        state.update({
            "loja": store_name,
            "seller_id": seller,
            "mode": mode_value,
            "status": "completed",
            "coverage_days": max(1, _as_non_negative_int(coverage_days) or 1),
            "cursor": max(state["cursor"], _as_non_negative_int(cursor)) if same_mode else _as_non_negative_int(cursor),
            "orders_total": max(state["orders_total"], _as_non_negative_int(orders_total)) if same_mode else _as_non_negative_int(orders_total),
            "bootstrap_complete": bool(state["bootstrap_complete"] or bootstrap_complete),
            "updated_at": now,
            "finished_at": now,
            "error": "",
        })
        _save_state_conn(conn, store_key, state)
    return state


def fail_sync(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    error: Any,
    cursor: int = 0,
) -> dict[str, Any]:
    store_key, store_name, seller = _store_identity(loja, seller_id)
    with _connection(tenant_path, writable=True) as conn:
        state = _get_state_conn(conn, store_key, store_name, seller)
        state.update({
            "loja": store_name,
            "seller_id": seller,
            "status": "failed",
            "cursor": max(state["cursor"], _as_non_negative_int(cursor)),
            "updated_at": _utc_now_iso(),
            "error": re.sub(r"\s+", " ", str(error or "")).strip()[:2000],
        })
        _save_state_conn(conn, store_key, state)
    return state


def recover_interrupted_sync(
    tenant_path: str | os.PathLike[str],
    loja: str,
    seller_id: str,
    *,
    active: bool,
    invalid_cursor_at: int = ML_MAX_VALID_ORDER_OFFSET + 1,
) -> dict[str, Any]:
    """Make an orphaned sync terminal and recover an unusable bootstrap cursor.

    The in-process worker registry is authoritative while ``active`` is true.
    A persisted ``running`` row without a live worker is left behind by a
    stopped process and must not keep the automation waiting forever.  Mercado
    Livre also rejects order-search offsets at 10,000; resetting only the
    bootstrap progress lets the idempotent cache resume without deleting
    already collected conversations.
    """

    store_key, store_name, seller = _store_identity(loja, seller_id)
    cursor_limit = max(1, _as_non_negative_int(invalid_cursor_at) or 1)
    now = _utc_now_iso()
    with _connection(tenant_path, writable=True) as conn:
        state = _get_state_conn(conn, store_key, store_name, seller)
        changed = False
        reasons: list[str] = []

        if state["status"] == "running" and not active:
            state["status"] = "failed"
            state["finished_at"] = now
            reasons.append("Sincronizacao anterior interrompida antes da conclusao.")
            changed = True

        invalid_bootstrap_cursor = (
            state["mode"] == "bootstrap"
            and not state["bootstrap_complete"]
            and state["cursor"] >= cursor_limit
        )
        if invalid_bootstrap_cursor and not active:
            state["status"] = "failed"
            state["cursor"] = 0
            state["conversations_saved"] = 0
            state["orders_scanned"] = 0
            state["finished_at"] = now
            reasons.append(
                "Cursor do bootstrap fora da faixa aceita; coleta reiniciada de forma idempotente."
            )
            changed = True

        if changed:
            previous_error = re.sub(r"\s+", " ", str(state.get("error") or "")).strip()
            combined_error = " ".join((*reasons, previous_error)).strip()
            state.update({
                "loja": store_name,
                "seller_id": seller,
                "updated_at": now,
                "error": combined_error[:2000],
            })
            _save_state_conn(conn, store_key, state)
    return state


__all__ = [
    "DB_FILENAME",
    "ML_MAX_VALID_ORDER_OFFSET",
    "begin_sync",
    "fail_sync",
    "finish_sync",
    "get_state",
    "list_conversations",
    "recover_interrupted_sync",
    "summary",
    "update_sync_progress",
    "upsert_conversations",
]
