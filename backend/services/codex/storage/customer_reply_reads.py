"""Customer-reply snapshot queries and transient draft availability."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from .common import (
    SCHEMA_VERSION,
    _connection,
    _json_loads,
    _lock_for,
    _readonly_state_db_path,
    _safe_id,
    codex_assistant_state_db_path,
)
from .customer_reply_state import (
    _CUSTOMER_REPLY_SEALED_RESULT_FIELD,
    _CUSTOMER_REPLY_TRANSIENT,
    _CUSTOMER_REPLY_TRANSIENT_LOCK,
    _customer_reply_cache_key,
    _customer_reply_transient_merge,
)
from .schema import _ensure_state_schema


def codex_assistant_customer_reply_jobs_readonly(
    info_base: str,
    client_id: str,
    job_ids: list[str],
    *,
    include_drafts: bool = True,
) -> dict[str, dict[str, Any]]:
    """Read requested jobs in one tenant snapshot without schema or lifecycle writes."""

    ids = list(dict.fromkeys(
        str(value).strip() for value in job_ids
        if isinstance(value, str) and value.strip()
        and _safe_id(value.strip(), "") == value.strip()
    ))
    if not ids:
        return {}
    try:
        db_path = _readonly_state_db_path(info_base, client_id)
        uri = Path(db_path).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    except (OSError, ValueError, sqlite3.Error):
        return {}
    rows: list[sqlite3.Row] = []
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        version = connection.execute(
            "SELECT value FROM assistant_meta WHERE key='schema_version'"
        ).fetchone()
        if version is None or str(version["value"] or "") != SCHEMA_VERSION:
            return {}
        for offset in range(0, len(ids), 256):
            chunk = ids[offset:offset + 256]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(connection.execute(
                "SELECT job_id, status, agent_state, payload_json "
                f"FROM assistant_customer_reply_jobs WHERE job_id IN ({placeholders})",
                chunk,
            ).fetchall())
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _json_loads(row["payload_json"], None)
        job_id = str(row["job_id"] or "")
        if (
            not isinstance(payload, dict)
            or str(payload.get("client_id") or "") != str(client_id).strip()
            or str(payload.get("job_id") or "") != job_id
            or str(payload.get("status") or "") != str(row["status"] or "")
            or str(payload.get("agent_state") or "") != str(row["agent_state"] or "")
        ):
            continue
        if include_drafts:
            payload = _customer_reply_transient_merge(db_path, payload, job_id)
        else:
            payload = dict(payload)
            payload.pop(_CUSTOMER_REPLY_SEALED_RESULT_FIELD, None)
        result[job_id] = payload
    return result


def codex_assistant_customer_reply_job_has_transient(
    info_base: str, client_id: str, job_id: str
) -> bool:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    key = _customer_reply_cache_key(db_path, job_id)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        cached = _CUSTOMER_REPLY_TRANSIENT.get(key)
        if cached and cached[0] > time.time():
            return True
        _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
    with _lock_for(db_path):
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                "SELECT payload_json FROM assistant_customer_reply_jobs WHERE job_id = ? LIMIT 1",
                (_safe_id(job_id, ""),),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    merged = (
        _customer_reply_transient_merge(db_path, payload, _safe_id(job_id, ""))
        if isinstance(payload, dict)
        else {}
    )
    result = merged.get("result") if isinstance(merged, dict) else {}
    return bool(isinstance(result, dict) and str(result.get("resposta") or "").strip())
