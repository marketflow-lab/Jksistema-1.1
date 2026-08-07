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


SCHEMA_VERSION = "5"
STATE_ROW_SCHEDULER = "scheduler_state"

_LOCKS_LOCK = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}

_CUSTOMER_REPLY_TRANSIENT_LOCK = threading.RLock()
_CUSTOMER_REPLY_TRANSIENT: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS = 24 * 60 * 60
_CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS = 15 * 60
_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS = 7

# Customer prompts, buyer history, generated answers, evidence, queries, sources,
# tool output and operator guidance are intentionally absent.  This table is a
# durable scheduler/lease journal, not a conversation or proposal store.
_CUSTOMER_REPLY_DURABLE_FIELDS = frozenset({
    "job_id", "profile", "task_type", "subject_key", "event_subject_key",
    "question_id", "item_id", "store", "client_id", "channel", "status",
    "agent_state", "current_step", "idempotency_key", "request_hash",
    "thread_id", "thread_reused", "thread_restart_reason", "prompt_version",
    "prompt_hash", "schema_version", "conversation_id", "previous_job_id",
    "plan_id", "proposal_id", "proposal_version", "proposal_hash", "action_id",
    "cancel_requested", "request_generation", "attempt_count",
    "operational_failure_count", "retry_count", "retry_policy",
    "queue_origin", "queue_priority", "queue_policy_version",
    "evidence_attempt_count", "evidence_attempt_limit",
    "operational_failure_limit", "total_attempt_limit",
    "first_started_at_epoch", "execution_deadline_epoch",
    "next_retry_at_epoch", "next_retry_delay_seconds", "deadline_seconds",
    "deadline_at_epoch", "deadline_reached", "completed_with_partial",
    "blocked_without_draft", "contract_quarantined", "lease_owner",
    "lease_expires_ts", "lease_generation", "heartbeat_at", "retry_ready_at",
    "last_attempt_completed_at", "created_at", "updated_at", "completed_at",
    "data_sufficient", "publish_attempted", "requires_approval",
    "completion_reason", "review_required", "draft_expired", "retry_kind",
})
_CUSTOMER_REPLY_SCOPE_ID_FIELDS = frozenset({
    "question_id", "item_id", "buyer_id", "pack_id", "order_id",
})


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


def _customer_reply_cache_key(db_path: str, job_id: str) -> tuple[str, str]:
    return (os.path.abspath(db_path), _safe_id(job_id, ""))


def _customer_reply_durable_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {key: value for key, value in source.items() if key in _CUSTOMER_REPLY_DURABLE_FIELDS}
    scope = source.get("scope_verifiers") if isinstance(source.get("scope_verifiers"), dict) else {}
    durable_scope = {
        key: str(value or "")[:160]
        for key, value in scope.items()
        if key in _CUSTOMER_REPLY_SCOPE_ID_FIELDS and str(value or "").strip()
    }
    if durable_scope:
        durable["scope_verifiers"] = durable_scope
    result = source.get("result") if isinstance(source.get("result"), dict) else {}
    for key in (
        "proposal_id", "proposal_version", "proposal_hash", "data_sufficient",
        "publish_attempted", "requires_approval", "completed_with_partial",
        "blocked_without_draft",
    ):
        if key in result and key not in durable:
            durable[key] = result.get(key)
    steps = source.get("agent_steps") if isinstance(source.get("agent_steps"), list) else []
    if steps:
        durable["agent_steps"] = [
            {
                key: item.get(key)
                for key in ("state", "step", "at")
                if key in item
            }
            for item in steps[-60:]
            if isinstance(item, dict)
        ]
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    if verification:
        durable["verification"] = {
            key: verification.get(key)
            for key in ("status", "confirmed", "verified_at")
            if key in verification
        }
    transient = {key: value for key, value in source.items() if key not in durable}
    return durable, transient


def _customer_reply_plan_durable_payload(payload: Any) -> dict[str, Any]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {
        key: source.get(key)
        for key in (
            "plan_id", "task_id", "conversation_id", "conversation_generation",
            "channel", "agent_state", "current_step", "idempotency_key",
            "created_at", "updated_at",
        )
        if key in source
    }
    durable["required_input"] = []
    durable["guidance_applied"] = []
    durable["steps"] = [
        {
            key: step.get(key)
            for key in ("step_id", "status", "started_at", "completed_at")
            if key in step
        }
        for step in (source.get("steps") or [])
        if isinstance(step, dict)
    ]
    durable["state_history"] = [
        {
            key: state.get(key)
            for key in ("state", "step", "at")
            if key in state
        }
        for state in (source.get("state_history") or [])[-100:]
        if isinstance(state, dict)
    ]
    proposal = source.get("proposal") if isinstance(source.get("proposal"), dict) else {}
    durable["proposal"] = {
        key: proposal.get(key)
        for key in (
            "proposal_id", "version", "proposal_hash", "action_id",
            "channels_allowed", "requires_confirmation",
        )
        if key in proposal
    }
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    durable["verification"] = {
        key: verification.get(key)
        for key in ("status", "confirmed", "verified_at")
        if key in verification
    }
    return durable


def _customer_reply_transient_put(db_path: str, payload: dict[str, Any], transient: dict[str, Any]) -> None:
    job_id = _safe_id(payload.get("job_id"), "")
    if not job_id:
        return
    key = _customer_reply_cache_key(db_path, job_id)
    status = str(payload.get("status") or "")
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        if status == "cancelled":
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return
        previous = _CUSTOMER_REPLY_TRANSIENT.get(key)
        merged = dict(previous[1]) if previous and previous[0] > time.time() else {}
        merged.update(transient)
        if not merged:
            return
        ttl = (
            _CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS
            if status == "completed"
            else _CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS
        )
        _CUSTOMER_REPLY_TRANSIENT[key] = (time.time() + ttl, merged)


def _customer_reply_transient_merge(db_path: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    key = _customer_reply_cache_key(db_path, str(payload.get("job_id") or ""))
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        cached = _CUSTOMER_REPLY_TRANSIENT.get(key)
        if not cached:
            return payload
        if cached[0] <= time.time():
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return payload
        return {**payload, **cached[1]}


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
        return False


def _customer_reply_cleanup_rows(conn: sqlite3.Connection) -> list[str]:
    expired = [
        str(row["job_id"] or "")
        for row in conn.execute(
            "SELECT job_id FROM assistant_customer_reply_jobs "
            "WHERE status IN ('completed', 'cancelled') "
            "AND julianday(updated_at) < julianday('now', ?)",
            (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
        ).fetchall()
    ]
    conn.execute(
        "DELETE FROM assistant_customer_reply_jobs "
        "WHERE status IN ('completed', 'cancelled') "
        "AND julianday(updated_at) < julianday('now', ?)",
        (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
    )
    return expired


def codex_assistant_customer_reply_jobs_cleanup(info_base: str, client_id: str) -> int:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    expired: list[str] = []
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            expired = _customer_reply_cleanup_rows(conn)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        for job_id in expired:
            _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, job_id), None)
    return len(expired)


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_report_settings (
            name TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_financial_adjustments (
            adjustment_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            store TEXT,
            period_start TEXT,
            period_end TEXT,
            amount REAL,
            platform TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_adjustments_period "
        "ON assistant_financial_adjustments(kind, store, period_start, period_end)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_action_queue (
            action_id TEXT PRIMARY KEY,
            report_id TEXT,
            action_type TEXT NOT NULL,
            status TEXT NOT NULL,
            store TEXT,
            owner_username TEXT,
            owner_role TEXT,
            due_at TEXT,
            impact_brl REAL,
            confidence TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT,
            approved_by TEXT,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assistant_action_queue_status "
        "ON assistant_action_queue(status, owner_username, due_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_agent_plans (
            plan_id TEXT PRIMARY KEY,
            task_id TEXT,
            conversation_id TEXT,
            conversation_generation INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_plans_task "
        "ON assistant_agent_plans(task_id) WHERE task_id <> ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_plans_idempotency "
        "ON assistant_agent_plans(idempotency_key) WHERE idempotency_key <> ''"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_customer_reply_jobs (
            job_id TEXT PRIMARY KEY,
            profile TEXT NOT NULL,
            task_type TEXT NOT NULL,
            subject_key TEXT NOT NULL,
            store TEXT,
            status TEXT NOT NULL,
            agent_state TEXT NOT NULL,
            idempotency_key TEXT,
            thread_id TEXT,
            queue_origin TEXT NOT NULL DEFAULT 'legacy',
            queue_priority INTEGER NOT NULL DEFAULT 0,
            queue_policy_version TEXT NOT NULL DEFAULT '',
            lease_owner TEXT,
            lease_expires_ts REAL,
            lease_generation INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    customer_reply_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(assistant_customer_reply_jobs)").fetchall()
    }
    if "lease_generation" not in customer_reply_columns:
        conn.execute(
            "ALTER TABLE assistant_customer_reply_jobs "
            "ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0"
        )
    if "queue_origin" not in customer_reply_columns:
        conn.execute(
            "ALTER TABLE assistant_customer_reply_jobs "
            "ADD COLUMN queue_origin TEXT NOT NULL DEFAULT 'legacy'"
        )
    if "queue_priority" not in customer_reply_columns:
        conn.execute(
            "ALTER TABLE assistant_customer_reply_jobs "
            "ADD COLUMN queue_priority INTEGER NOT NULL DEFAULT 0"
        )
    if "queue_policy_version" not in customer_reply_columns:
        conn.execute(
            "ALTER TABLE assistant_customer_reply_jobs "
            "ADD COLUMN queue_policy_version TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_reply_jobs_queue "
        "ON assistant_customer_reply_jobs(status, lease_expires_ts, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_reply_jobs_priority_queue "
        "ON assistant_customer_reply_jobs(status, queue_priority DESC, created_at, job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_reply_jobs_subject "
        "ON assistant_customer_reply_jobs(profile, task_type, store, subject_key, created_at DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_reply_jobs_idempotency "
        "ON assistant_customer_reply_jobs(idempotency_key) WHERE idempotency_key <> ''"
    )
    migration_key = "customer_reply_payload_allowlist_v1"
    if _meta_get(conn, migration_key) != "1":
        linked_plan_ids: set[str] = set()
        legacy_rows = conn.execute(
            "SELECT job_id, profile, task_type, subject_key, store, status, agent_state, "
            "idempotency_key, thread_id, lease_owner, lease_expires_ts, lease_generation, "
            "cancel_requested, created_at, updated_at, payload_json "
            "FROM assistant_customer_reply_jobs"
        ).fetchall()
        for row in legacy_rows:
            source = _json_loads(row["payload_json"], {})
            if not isinstance(source, dict):
                source = {}
            plan_id = _safe_id(source.get("plan_id"), "")
            if plan_id:
                linked_plan_ids.add(plan_id)
            source.update(
                {
                    "job_id": str(row["job_id"] or ""),
                    "profile": str(row["profile"] or ""),
                    "task_type": str(row["task_type"] or ""),
                    "subject_key": str(row["subject_key"] or ""),
                    "store": str(row["store"] or ""),
                    "status": str(row["status"] or ""),
                    "agent_state": str(row["agent_state"] or ""),
                    "idempotency_key": str(row["idempotency_key"] or ""),
                    "thread_id": str(row["thread_id"] or ""),
                    "lease_owner": str(row["lease_owner"] or ""),
                    "lease_expires_ts": float(row["lease_expires_ts"] or 0.0),
                    "lease_generation": max(0, int(row["lease_generation"] or 0)),
                    "cancel_requested": bool(row["cancel_requested"]),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
            durable, _discarded = _customer_reply_durable_payload(source)
            conn.execute(
                "UPDATE assistant_customer_reply_jobs SET payload_json = ? WHERE job_id = ?",
                (_json_dumps(durable), str(row["job_id"] or "")),
            )
        for plan_id in linked_plan_ids:
            plan_row = conn.execute(
                "SELECT payload_json FROM assistant_agent_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if not plan_row:
                continue
            plan = _json_loads(plan_row["payload_json"], {})
            conn.execute(
                "UPDATE assistant_agent_plans SET payload_json = ? WHERE plan_id = ?",
                (_json_dumps(_customer_reply_plan_durable_payload(plan)), plan_id),
            )
        _meta_set(conn, migration_key, "1")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_agent_guidance (
            guidance_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            scope_type TEXT NOT NULL,
            scope_key TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(guidance_id, version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_guidance_scope "
        "ON assistant_agent_guidance(active, scope_type, scope_key, version DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_action_proposals (
            proposal_id TEXT PRIMARY KEY,
            action_id TEXT,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            proposal_hash TEXT NOT NULL,
            conversation_id TEXT,
            created_by TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_proposals_status "
        "ON assistant_action_proposals(status, created_by, updated_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_action_runs (
            run_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_action_runs_idempotency "
        "ON assistant_action_runs(idempotency_key) WHERE idempotency_key <> ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_runs_proposal "
        "ON assistant_action_runs(proposal_id, updated_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_action_approvals (
            approval_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            proposal_version INTEGER NOT NULL,
            proposal_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            actor TEXT,
            wa_id_hash TEXT,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_approvals_proposal "
        "ON assistant_action_approvals(proposal_id, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_agent_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            actor TEXT,
            channel TEXT,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_audit_entity "
        "ON assistant_agent_audit(entity_type, entity_id, created_at DESC)"
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


def codex_assistant_customer_reply_job_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    expected_lease_owner: str = "",
    expected_lease_generation: Optional[int] = None,
    max_origin_active: int = 0,
    max_origin_active_store: int = 0,
) -> dict[str, Any]:
    """Persist a Mercado Livre customer-reply orchestration job."""

    data = dict(payload or {}) if isinstance(payload, dict) else {}
    job_id = _safe_id(data.get("job_id"), "")
    if not job_id:
        job_id = hashlib.sha256(
            f"{time.time_ns()}|{client_id}|{data.get('task_type')}|{data.get('subject_key')}".encode("utf-8")
        ).hexdigest()[:32]
    now = _now_iso()
    data["job_id"] = job_id
    data.setdefault("profile", "mercado_livre_customer_reply")
    data.setdefault("status", "queued")
    data.setdefault("agent_state", "entendendo")
    data.setdefault("created_at", now)
    data.setdefault("lease_generation", 0)
    data["updated_at"] = now
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    transient: dict[str, Any] = {}
    expired_job_ids: list[str] = []
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            expired_job_ids = _customer_reply_cleanup_rows(conn)
            existing_row = conn.execute(
                "SELECT status, lease_owner, lease_generation, cancel_requested, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if (
                not existing_row
                and str(data.get("status") or "queued") in {"queued", "running", "waiting_retry"}
                and str(data.get("queue_origin") or "").strip()
                and (int(max_origin_active or 0) > 0 or int(max_origin_active_store or 0) > 0)
            ):
                origin = str(data.get("queue_origin") or "").strip()
                store = str(data.get("store") or "").strip()
                active_statuses = "('queued', 'running', 'waiting_retry')"
                if int(max_origin_active or 0) > 0:
                    total_row = conn.execute(
                        "SELECT COUNT(*) AS total FROM assistant_customer_reply_jobs "
                        f"WHERE status IN {active_statuses} AND cancel_requested = 0 AND queue_origin = ?",
                        (origin,),
                    ).fetchone()
                    if int(total_row["total"] or 0) >= int(max_origin_active):
                        return {
                            "job_id": job_id,
                            "status": "deferred",
                            "queue_admission_blocked": True,
                            "blocked_reason": "queue_backpressure",
                            "queue_origin": origin,
                        }
                if int(max_origin_active_store or 0) > 0:
                    store_row = conn.execute(
                        "SELECT COUNT(*) AS total FROM assistant_customer_reply_jobs "
                        f"WHERE status IN {active_statuses} AND cancel_requested = 0 "
                        "AND queue_origin = ? AND store = ?",
                        (origin, store),
                    ).fetchone()
                    if int(store_row["total"] or 0) >= int(max_origin_active_store):
                        return {
                            "job_id": job_id,
                            "status": "deferred",
                            "queue_admission_blocked": True,
                            "blocked_reason": "queue_backpressure",
                            "queue_origin": origin,
                        }
            if existing_row:
                existing = _json_loads(existing_row["payload_json"], {})
                if not isinstance(existing, dict):
                    existing = {}
                if bool(existing_row["cancel_requested"]) or str(existing_row["status"] or "") == "cancelled":
                    return _customer_reply_transient_merge(db_path, existing)
                existing_owner = str(existing_row["lease_owner"] or "")
                incoming_owner = str(data.get("lease_owner") or "")
                expected_owner = str(expected_lease_owner or "")
                existing_generation = max(0, int(existing_row["lease_generation"] or 0))
                incoming_generation = max(0, int(data.get("lease_generation") or 0))
                if expected_lease_generation is not None and int(expected_lease_generation) != existing_generation:
                    return _customer_reply_transient_merge(db_path, existing)
                if incoming_generation != existing_generation:
                    return _customer_reply_transient_merge(db_path, existing)
                if (
                    str(existing_row["status"] or "") == "running"
                    and existing_owner
                    and existing_owner not in {incoming_owner, expected_owner}
                ):
                    return _customer_reply_transient_merge(db_path, existing)
                data["lease_generation"] = existing_generation
            durable, transient = _customer_reply_durable_payload(data)
            raw = _json_dumps(durable)
            conn.execute(
                """
                INSERT INTO assistant_customer_reply_jobs(
                    job_id, profile, task_type, subject_key, store, status,
                    agent_state, idempotency_key, thread_id,
                    queue_origin, queue_priority, queue_policy_version, lease_owner,
                    lease_expires_ts, lease_generation, cancel_requested, created_at, updated_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    profile=excluded.profile,
                    task_type=excluded.task_type,
                    subject_key=excluded.subject_key,
                    store=excluded.store,
                    status=excluded.status,
                    agent_state=excluded.agent_state,
                    idempotency_key=excluded.idempotency_key,
                    thread_id=excluded.thread_id,
                    queue_origin=excluded.queue_origin,
                    queue_priority=excluded.queue_priority,
                    queue_policy_version=excluded.queue_policy_version,
                    lease_owner=excluded.lease_owner,
                    lease_expires_ts=excluded.lease_expires_ts,
                    lease_generation=excluded.lease_generation,
                    cancel_requested=excluded.cancel_requested,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    job_id,
                    str(durable.get("profile") or "mercado_livre_customer_reply"),
                    str(durable.get("task_type") or "question"),
                    str(durable.get("subject_key") or ""),
                    str(durable.get("store") or ""),
                    str(durable.get("status") or "queued"),
                    str(durable.get("agent_state") or "entendendo"),
                    str(durable.get("idempotency_key") or ""),
                    str(durable.get("thread_id") or ""),
                    str(durable.get("queue_origin") or "legacy"),
                    int(durable.get("queue_priority") or 0),
                    str(durable.get("queue_policy_version") or ""),
                    str(durable.get("lease_owner") or ""),
                    float(durable.get("lease_expires_ts") or 0.0),
                    max(0, int(durable.get("lease_generation") or 0)),
                    1 if durable.get("cancel_requested") else 0,
                    str(durable.get("created_at") or now),
                    now,
                    raw,
                ),
            )
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        for expired_job_id in expired_job_ids:
            _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, expired_job_id), None)
    _customer_reply_transient_put(db_path, data, transient)
    return data


def codex_assistant_customer_reply_job_get(
    info_base: str,
    client_id: str,
    job_id: str = "",
    *,
    idempotency_key: str = "",
) -> Optional[dict[str, Any]]:
    if job_id:
        clause, value = "job_id = ?", _safe_id(job_id, "")
    elif idempotency_key:
        clause, value = "idempotency_key = ?", str(idempotency_key)
    else:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                f"SELECT payload_json FROM assistant_customer_reply_jobs WHERE {clause} LIMIT 1",
                (value,),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return _customer_reply_transient_merge(db_path, payload) if isinstance(payload, dict) else None


def codex_assistant_customer_reply_job_latest(
    info_base: str,
    client_id: str,
    *,
    task_type: str,
    store: str,
    subject_key: str,
) -> Optional[dict[str, Any]]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                """
                SELECT payload_json FROM assistant_customer_reply_jobs
                WHERE profile = 'mercado_livre_customer_reply'
                  AND task_type = ? AND store = ? AND subject_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(task_type or ""), str(store or ""), str(subject_key or "")),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return _customer_reply_transient_merge(db_path, payload) if isinstance(payload, dict) else None


def codex_assistant_customer_reply_jobs_list(
    info_base: str,
    client_id: str,
    *,
    statuses: Optional[list[str]] = None,
    limit: int = 100,
    offset: int = 0,
    priority_order: bool = False,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = "SELECT payload_json FROM assistant_customer_reply_jobs"
    clean_statuses = [str(item or "").strip() for item in (statuses or []) if str(item or "").strip()]
    if clean_statuses:
        sql += " WHERE status IN (" + ",".join("?" for _ in clean_statuses) + ")"
        params.extend(clean_statuses)
    if priority_order:
        sql += " ORDER BY queue_priority DESC, created_at ASC, job_id ASC LIMIT ? OFFSET ?"
    else:
        sql += " ORDER BY created_at ASC, job_id ASC LIMIT ? OFFSET ?"
    params.append(max(1, min(int(limit or 100), 1000)))
    params.append(max(0, int(offset or 0)))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_loads(row["payload_json"], None)
        if isinstance(payload, dict):
            result.append(_customer_reply_transient_merge(db_path, payload))
    return result


def codex_assistant_customer_reply_queue_metrics(
    info_base: str,
    client_id: str,
    *,
    origin: str = "",
    store: str = "",
) -> dict[str, int]:
    """Return secret-free durable queue counters for one tenant database."""

    clauses = ["status IN ('queued', 'running', 'waiting_retry')", "cancel_requested = 0"]
    params: list[Any] = []
    if str(origin or "").strip():
        clauses.append("queue_origin = ?")
        params.append(str(origin).strip())
    if str(store or "").strip():
        clauses.append("store = ?")
        params.append(str(store).strip())
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM assistant_customer_reply_jobs "
                f"WHERE {' AND '.join(clauses)} GROUP BY status",
                params,
            ).fetchall()
    counts = {"queued": 0, "running": 0, "waiting_retry": 0}
    for row in rows:
        status = str(row["status"] or "")
        if status in counts:
            counts[status] = max(0, int(row["total"] or 0))
    counts["active"] = sum(counts.values())
    return counts


def codex_assistant_customer_reply_job_claim(
    info_base: str,
    client_id: str,
    job_id: str,
    *,
    owner: str,
    lease_seconds: float = 45.0,
    max_running: int = 0,
    enforce_priority: bool = False,
    queue_policy_version: str = "",
) -> Optional[dict[str, Any]]:
    """Atomically claim a queued job or a running job whose lease expired."""

    now_ts = time.time()
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, store, queue_priority, queue_policy_version, created_at, "
                "lease_owner, lease_expires_ts, lease_generation, cancel_requested, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (_safe_id(job_id, ""),),
            ).fetchone()
            if not row or bool(row["cancel_requested"]):
                return None
            status = str(row["status"] or "")
            lease_owner = str(row["lease_owner"] or "")
            lease_expires = float(row["lease_expires_ts"] or 0.0)
            if status not in {"queued", "running"}:
                return None
            if status == "running" and lease_expires > now_ts and lease_owner != str(owner or ""):
                return None
            expected_policy = str(queue_policy_version or "").strip()
            if expected_policy and str(row["queue_policy_version"] or "") != expected_policy:
                return None
            if max(0, int(max_running or 0)):
                running_total = conn.execute(
                    "SELECT COUNT(*) FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 "
                    "AND lease_expires_ts > ? AND job_id <> ?",
                    (now_ts, _safe_id(job_id, "")),
                ).fetchone()[0]
                if int(running_total or 0) >= max(1, int(max_running)):
                    return None
                same_store = conn.execute(
                    "SELECT 1 FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 "
                    "AND lease_expires_ts > ? AND store = ? AND job_id <> ? LIMIT 1",
                    (now_ts, str(row["store"] or ""), _safe_id(job_id, "")),
                ).fetchone()
                if same_store:
                    return None
            if enforce_priority:
                higher_priority = conn.execute(
                    "SELECT 1 FROM assistant_customer_reply_jobs "
                    "WHERE status = 'queued' AND cancel_requested = 0 AND job_id <> ? "
                    "AND (? = '' OR queue_policy_version = ?) "
                    "AND (queue_priority > ? OR (queue_priority = ? AND "
                    "(created_at < ? OR (created_at = ? AND job_id < ?)))) "
                    "AND store NOT IN ("
                    "SELECT store FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 AND lease_expires_ts > ?"
                    ") LIMIT 1",
                    (
                        _safe_id(job_id, ""), expected_policy, expected_policy,
                        int(row["queue_priority"] or 0), int(row["queue_priority"] or 0),
                        str(row["created_at"] or ""), str(row["created_at"] or ""),
                        _safe_id(job_id, ""), now_ts,
                    ),
                ).fetchone()
                if higher_priority:
                    return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            next_generation = max(
                0,
                int(row["lease_generation"] or data.get("lease_generation") or 0),
            ) + 1
            data.update(
                {
                    "status": "running",
                    "lease_owner": str(owner or ""),
                    "lease_expires_ts": now_ts + max(10.0, float(lease_seconds or 45.0)),
                    "lease_generation": next_generation,
                    "updated_at": _now_iso(),
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET status = 'running', lease_owner = ?, lease_expires_ts = ?,
                    lease_generation = ?, updated_at = ?, payload_json = ?
                WHERE job_id = ?
                """,
                (
                    data["lease_owner"],
                    data["lease_expires_ts"],
                    data["lease_generation"],
                    data["updated_at"],
                    _json_dumps(durable),
                    _safe_id(job_id, ""),
                ),
            )
    return data


def codex_assistant_customer_reply_job_heartbeat(
    info_base: str,
    client_id: str,
    job_id: str,
    *,
    owner: str,
    lease_generation: Optional[int] = None,
    lease_seconds: float = 45.0,
) -> Optional[dict[str, Any]]:
    """Atomically renew an active customer-reply job lease.

    The status and owner checks happen in the same database lock as the update,
    so a late heartbeat can never resurrect a job that already completed.
    """

    now = _now_iso()
    lease_expires = time.time() + max(10.0, float(lease_seconds or 45.0))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, lease_owner, lease_generation, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (_safe_id(job_id, ""),),
            ).fetchone()
            if not row or str(row["status"] or "") != "running":
                return None
            if str(row["lease_owner"] or "") != str(owner or ""):
                return None
            current_generation = max(0, int(row["lease_generation"] or 0))
            if current_generation > 0 and lease_generation is None:
                return None
            if lease_generation is not None and int(lease_generation) != current_generation:
                return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            data.update(
                {
                    "status": "running",
                    "lease_owner": str(owner or ""),
                    "lease_expires_ts": lease_expires,
                    "lease_generation": current_generation,
                    "heartbeat_at": now,
                    "updated_at": now,
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            updated = conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET lease_expires_ts = ?, updated_at = ?, payload_json = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ? AND lease_generation = ?
                """,
                (
                    lease_expires,
                    now,
                    _json_dumps(durable),
                    _safe_id(job_id, ""),
                    str(owner or ""),
                    current_generation,
                ),
            )
            if int(updated.rowcount or 0) != 1:
                return None
    return data


def codex_assistant_customer_reply_job_request_cancel(
    info_base: str,
    client_id: str,
    job_id: str,
) -> Optional[dict[str, Any]]:
    safe_job_id = _safe_id(job_id, "")
    if not safe_job_id:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, payload_json FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (safe_job_id,),
            ).fetchone()
            if not row:
                return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            if str(row["status"] or "") in {"completed", "cancelled"}:
                return data
            now = _now_iso()
            data.update(
                {
                    "job_id": safe_job_id,
                    "status": "cancelled",
                    "agent_state": "cancelado",
                    "current_step": "cancelar",
                    "cancel_requested": True,
                    "lease_owner": "",
                    "lease_expires_ts": 0.0,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET status = 'cancelled', agent_state = 'cancelado',
                    lease_owner = '', lease_expires_ts = 0,
                    cancel_requested = 1, updated_at = ?, payload_json = ?
                WHERE job_id = ?
                """,
                (now, _json_dumps(durable), safe_job_id),
            )
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, safe_job_id), None)
    return durable


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
