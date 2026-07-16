"""Durable SQLite storage for the local WhatsApp bridge.

The bridge still works with a dictionary in memory because a large amount of
the orchestration code mutates that object in place.  This module persists each
top-level bucket independently and mirrors the operationally important buckets
into queryable tables.  It replaces the former whole-file JSON write without
forcing the gateway/orchestrator to change all at once.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional


ASSISTANT_JOB_STATES = {
    "queued",
    "running",
    "awaiting_input",
    "partial",
    "completed",
    "failed",
    "canceled",
}
TERMINAL_JOB_STATES = {"partial", "completed", "failed", "canceled"}
SCHEMA_VERSION = 2


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _state_name(value: Any) -> str:
    state = str(value or "queued").strip().lower()
    return state if state in ASSISTANT_JOB_STATES else "queued"


def _pending_state(value: dict[str, Any]) -> str:
    state = str(value.get("job_state") or value.get("manager_state") or value.get("state") or "queued").strip().lower()
    aliases = {
        "manager_queued": "queued",
        "manager_running": "running",
        "waiting_result": "running",
        "retry_starting": "running",
        "waiting_retry": "running",
        "partial": "partial",
    }
    return _state_name(aliases.get(state, state))


def _subject_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:24]


def _safe_audit_value(value: Any) -> Any:
    sensitive = {
        "token", "bridge_token", "access_token", "refresh_token", "authorization",
        "credential", "credentials", "password", "secret", "api_key",
    }
    if isinstance(value, dict):
        return {
            str(key)[:100]: _safe_audit_value(item)
            for key, item in value.items()
            if str(key).strip().lower() not in sensitive
        }
    if isinstance(value, list):
        return [_safe_audit_value(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:4000]
    return value


class WhatsappBridgeStore:
    """Thread-safe SQLite/WAL store used by ``whatsapp_bridge``."""

    def __init__(self, path: Path, legacy_json_path: Optional[Path] = None) -> None:
        self.path = Path(path)
        self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS state_buckets (
                        bucket TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS assistant_jobs (
                        message_id TEXT PRIMARY KEY,
                        client_id TEXT NOT NULL DEFAULT '',
                        subject_hash TEXT NOT NULL DEFAULT '',
                        username TEXT NOT NULL DEFAULT '',
                        kind TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL,
                        stage TEXT NOT NULL DEFAULT '',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        deadline_at_epoch REAL NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT '',
                        updated_at_epoch REAL NOT NULL,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_assistant_jobs_state
                        ON assistant_jobs(state, updated_at_epoch);
                    CREATE TABLE IF NOT EXISTS messages (
                        message_id TEXT PRIMARY KEY,
                        subject_hash TEXT NOT NULL DEFAULT '',
                        state TEXT NOT NULL DEFAULT '',
                        stage TEXT NOT NULL DEFAULT '',
                        received_at_epoch REAL NOT NULL DEFAULT 0,
                        claimed_at_epoch REAL NOT NULL DEFAULT 0,
                        completed_at_epoch REAL NOT NULL DEFAULT 0,
                        sent_at_epoch REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_state
                        ON messages(state, updated_at_epoch);
                    CREATE TABLE IF NOT EXISTS job_attempts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_id TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        error_class TEXT NOT NULL DEFAULT '',
                        retryable INTEGER NOT NULL DEFAULT 0,
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at_epoch REAL NOT NULL,
                        UNIQUE(message_id, attempt_number, state)
                    );
                    CREATE TABLE IF NOT EXISTS approval_tokens (
                        token TEXT PRIMARY KEY,
                        approval_id TEXT NOT NULL DEFAULT '',
                        subject_hash TEXT NOT NULL DEFAULT '',
                        client_id TEXT NOT NULL DEFAULT '',
                        username TEXT NOT NULL DEFAULT '',
                        used INTEGER NOT NULL DEFAULT 0,
                        expires_at_epoch REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS artifacts (
                        fingerprint TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL DEFAULT '',
                        artifact_type TEXT NOT NULL DEFAULT '',
                        mime_type TEXT NOT NULL DEFAULT '',
                        byte_size INTEGER NOT NULL DEFAULT 0,
                        sha256 TEXT NOT NULL DEFAULT '',
                        path TEXT NOT NULL DEFAULT '',
                        expires_at_epoch REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS message_timings (
                        message_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL DEFAULT '',
                        error_class TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS notifications (
                        fingerprint TEXT PRIMARY KEY,
                        message_id TEXT NOT NULL DEFAULT '',
                        event_type TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at_epoch REAL NOT NULL,
                        updated_at_epoch REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_notifications_status
                        ON notifications(status, updated_at_epoch);
                    CREATE TABLE IF NOT EXISTS metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        metric TEXT NOT NULL,
                        value REAL NOT NULL,
                        labels_json TEXT NOT NULL DEFAULT '{}',
                        observed_at_epoch REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_metrics_name_time
                        ON metrics(metric, observed_at_epoch);
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        message_id TEXT NOT NULL DEFAULT '',
                        subject_hash TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at_epoch REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_audit_events_created
                        ON audit_events(created_at_epoch);
                    """
                )
                now = time.time()
                connection.execute(
                    "INSERT INTO metadata(key,value,updated_at_epoch) VALUES('schema_version',?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at_epoch=excluded.updated_at_epoch",
                    (str(SCHEMA_VERSION), now),
                )
            self._initialized = True
            self._import_legacy_once()

    def _metadata(self, key: str) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row["value"] or "") if row else ""

    def _set_metadata(self, connection: sqlite3.Connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO metadata(key,value,updated_at_epoch) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at_epoch=excluded.updated_at_epoch",
            (str(key), str(value), time.time()),
        )

    def _import_legacy_once(self) -> None:
        legacy = self.legacy_json_path
        if not legacy or not legacy.exists() or self._metadata("legacy_json_imported"):
            return
        try:
            value = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            value = {}
        if not isinstance(value, dict):
            value = {}
        backup = legacy.with_suffix(legacy.suffix + ".sqlite-migration.bak")
        try:
            if not backup.exists():
                shutil.copy2(legacy, backup)
        except Exception:
            pass
        self.save_state(value, migration=True)

    def load_state(self) -> dict[str, Any]:
        self.initialize()
        result: dict[str, Any] = {}
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT bucket,payload_json FROM state_buckets").fetchall()
        for row in rows:
            try:
                result[str(row["bucket"])] = json.loads(str(row["payload_json"] or "null"))
            except Exception:
                continue
        return result

    def save_state(self, state: dict[str, Any], *, migration: bool = False) -> None:
        self.initialize() if not self._initialized else None
        snapshot = dict(state or {})
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    str(row["bucket"])
                    for row in connection.execute("SELECT bucket FROM state_buckets").fetchall()
                }
                for bucket, value in snapshot.items():
                    connection.execute(
                        "INSERT INTO state_buckets(bucket,payload_json,updated_at_epoch) VALUES(?,?,?) "
                        "ON CONFLICT(bucket) DO UPDATE SET payload_json=excluded.payload_json,updated_at_epoch=excluded.updated_at_epoch",
                        (str(bucket), _json(value), now),
                    )
                for removed in existing.difference(str(key) for key in snapshot):
                    connection.execute("DELETE FROM state_buckets WHERE bucket=?", (removed,))
                self._sync_jobs(
                    connection,
                    _object(snapshot.get("pending_messages")),
                    _object(snapshot.get("assistant_job_history")),
                    now,
                )
                self._sync_approval_tokens(connection, _object(snapshot.get("question_approval_tokens")), now)
                self._sync_timings(connection, _object(snapshot.get("bridge_message_timings")), now)
                self._sync_messages(
                    connection,
                    _object(snapshot.get("bridge_message_timings")),
                    _object(snapshot.get("pending_messages")),
                    _object(snapshot.get("assistant_job_history")),
                    now,
                )
                self._sync_artifacts(connection, snapshot.get("artifacts"), now)
                self._set_metadata(connection, "last_snapshot_at_epoch", now)
                if migration:
                    self._set_metadata(connection, "legacy_json_imported", now)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _sync_jobs(
        self,
        connection: sqlite3.Connection,
        jobs: dict[str, Any],
        history: dict[str, Any],
        now: float,
    ) -> None:
        active_ids: set[str] = set()
        merged = dict(history)
        merged.update(jobs)
        for message_id, raw in merged.items():
            if not isinstance(raw, dict):
                continue
            key = str(message_id or "").strip()
            if not key:
                continue
            if message_id in jobs:
                active_ids.add(key)
            state = _pending_state(raw)
            attempts = max(int(raw.get("retry_count") or 0), int(raw.get("manager_retry_count") or 0))
            connection.execute(
                """
                INSERT INTO assistant_jobs(
                    message_id,client_id,subject_hash,username,kind,state,stage,attempts,
                    deadline_at_epoch,created_at,updated_at_epoch,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(message_id) DO UPDATE SET
                    client_id=excluded.client_id,subject_hash=excluded.subject_hash,username=excluded.username,
                    kind=excluded.kind,state=excluded.state,stage=excluded.stage,attempts=excluded.attempts,
                    deadline_at_epoch=excluded.deadline_at_epoch,created_at=excluded.created_at,
                    updated_at_epoch=excluded.updated_at_epoch,payload_json=excluded.payload_json
                """,
                (
                    key,
                    str(raw.get("client_id") or "")[:100],
                    _subject_hash(raw.get("subject_id")),
                    str(raw.get("username") or "")[:200],
                    str(raw.get("kind") or "")[:80],
                    state,
                    str(raw.get("delivery_state") or raw.get("handoff_status") or "")[:100],
                    attempts,
                    float(raw.get("deadline_at_epoch") or 0),
                    str(raw.get("created_at") or "")[:40],
                    now,
                    _json(raw),
                ),
            )
        if active_ids:
            placeholders = ",".join("?" for _ in active_ids)
            connection.execute(
                f"DELETE FROM assistant_jobs WHERE state NOT IN ('partial','completed','failed','canceled') AND message_id NOT IN ({placeholders})",
                tuple(active_ids),
            )
        else:
            connection.execute("DELETE FROM assistant_jobs WHERE state NOT IN ('partial','completed','failed','canceled')")

    def _sync_approval_tokens(self, connection: sqlite3.Connection, tokens: dict[str, Any], now: float) -> None:
        connection.execute("DELETE FROM approval_tokens")
        for token, raw in tokens.items():
            if not isinstance(raw, dict):
                continue
            created = float(raw.get("created_at_epoch") or raw.get("created_at") or 0)
            expires = float(raw.get("expires_at_epoch") or 0)
            if not expires and created:
                expires = created + (7 * 24 * 3600)
            connection.execute(
                "INSERT INTO approval_tokens(token,approval_id,subject_hash,client_id,username,used,expires_at_epoch,payload_json,updated_at_epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(token),
                    str(raw.get("approval_id") or "")[:200],
                    _subject_hash(raw.get("subject_id")),
                    str(raw.get("client_id") or "")[:100],
                    str(raw.get("username") or "")[:200],
                    1 if raw.get("used") is True else 0,
                    expires,
                    _json(raw),
                    now,
                ),
            )

    def _sync_timings(self, connection: sqlite3.Connection, timings: dict[str, Any], now: float) -> None:
        connection.execute("DELETE FROM message_timings")
        for message_id, raw in timings.items():
            if not isinstance(raw, dict):
                continue
            state = "sent" if raw.get("sent_at") else "completed" if raw.get("completed_at") else "running"
            error = str(raw.get("error") or "")
            error_class = error.split(":", 1)[0][:100] if error else ""
            connection.execute(
                "INSERT INTO message_timings(message_id,state,error_class,payload_json,updated_at_epoch) VALUES(?,?,?,?,?)",
                (str(message_id), state, error_class, _json(raw), now),
            )

    def _sync_messages(
        self,
        connection: sqlite3.Connection,
        timings: dict[str, Any],
        jobs: dict[str, Any],
        history: dict[str, Any],
        now: float,
    ) -> None:
        message_ids = {str(key) for key in (*timings.keys(), *jobs.keys(), *history.keys()) if str(key)}
        for message_id in message_ids:
            timing = _object(timings.get(message_id))
            job = _object(jobs.get(message_id) or history.get(message_id))
            state = _pending_state(job) if job else (
                "sent" if timing.get("sent_at") else "completed" if timing.get("completed_at") else "running"
            )
            connection.execute(
                """
                INSERT INTO messages(
                    message_id,subject_hash,state,stage,received_at_epoch,claimed_at_epoch,
                    completed_at_epoch,sent_at_epoch,payload_json,updated_at_epoch
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(message_id) DO UPDATE SET
                    subject_hash=excluded.subject_hash,state=excluded.state,stage=excluded.stage,
                    received_at_epoch=excluded.received_at_epoch,claimed_at_epoch=excluded.claimed_at_epoch,
                    completed_at_epoch=excluded.completed_at_epoch,sent_at_epoch=excluded.sent_at_epoch,
                    payload_json=excluded.payload_json,updated_at_epoch=excluded.updated_at_epoch
                """,
                (
                    message_id,
                    _subject_hash(job.get("subject_id")),
                    state,
                    str(job.get("delivery_state") or job.get("handoff_status") or "")[:100],
                    float(timing.get("received_at_epoch") or 0),
                    float(timing.get("claimed_at_epoch") or 0),
                    float(timing.get("completed_at_epoch") or job.get("terminal_at_epoch") or 0),
                    float(timing.get("sent_at_epoch") or 0),
                    _json({"timing": timing, "job": job}),
                    now,
                ),
            )

    def _sync_artifacts(self, connection: sqlite3.Connection, artifacts: Any, now: float) -> None:
        if not isinstance(artifacts, dict):
            return
        connection.execute("DELETE FROM artifacts")
        for fingerprint, raw in artifacts.items():
            if not isinstance(raw, dict):
                continue
            connection.execute(
                "INSERT INTO artifacts(fingerprint,message_id,artifact_type,mime_type,byte_size,sha256,path,expires_at_epoch,payload_json,updated_at_epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(fingerprint),
                    str(raw.get("message_id") or "")[:200],
                    str(raw.get("artifact_type") or "")[:80],
                    str(raw.get("mime_type") or "")[:160],
                    int(raw.get("byte_size") or raw.get("size") or 0),
                    str(raw.get("sha256") or "")[:128],
                    str(raw.get("path") or "")[:1000],
                    float(raw.get("expires_at_epoch") or raw.get("expires_at") or 0),
                    _json(raw),
                    now,
                ),
            )

    def record_attempt(
        self,
        message_id: str,
        attempt_number: int,
        state: str,
        *,
        error_class: str = "",
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.initialize()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO job_attempts(message_id,attempt_number,state,error_class,retryable,details_json,created_at_epoch) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    str(message_id),
                    max(0, int(attempt_number or 0)),
                    _state_name(state),
                    str(error_class or "")[:100],
                    1 if retryable else 0,
                    _json(details or {}),
                    time.time(),
                ),
            )

    def audit(
        self,
        event_type: str,
        *,
        message_id: str = "",
        subject_id: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.initialize()
        safe_details = _safe_audit_value(dict(details or {}))
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(event_type,message_id,subject_hash,details_json,created_at_epoch) VALUES(?,?,?,?,?)",
                (
                    str(event_type or "")[:100],
                    str(message_id or "")[:200],
                    _subject_hash(subject_id),
                    _json(safe_details),
                    time.time(),
                ),
            )

    def record_notification(
        self,
        fingerprint: str,
        *,
        message_id: str = "",
        event_type: str = "",
        status: str,
        reason: str = "",
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self.initialize()
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO notifications(fingerprint,message_id,event_type,status,reason,payload_json,created_at_epoch,updated_at_epoch) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET "
                "status=excluded.status,reason=excluded.reason,payload_json=excluded.payload_json,updated_at_epoch=excluded.updated_at_epoch",
                (
                    str(fingerprint)[:200], str(message_id)[:200], str(event_type)[:100],
                    str(status)[:100], str(reason)[:500], _json(_safe_audit_value(details or {})), now, now,
                ),
            )

    def record_metric(self, metric: str, value: float, *, labels: Optional[dict[str, Any]] = None) -> None:
        self.initialize()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO metrics(metric,value,labels_json,observed_at_epoch) VALUES(?,?,?,?)",
                (str(metric)[:100], float(value), _json(_safe_audit_value(labels or {})), time.time()),
            )
            connection.execute("DELETE FROM metrics WHERE observed_at_epoch<?", (time.time() - 30 * 86400,))

    def diagnostics(self) -> dict[str, Any]:
        self.initialize()
        with self._lock, self._connect() as connection:
            job_rows = connection.execute(
                "SELECT state,COUNT(*) AS total,MAX(updated_at_epoch) AS last_at FROM assistant_jobs GROUP BY state"
            ).fetchall()
            attempts = connection.execute("SELECT COUNT(*) AS total FROM job_attempts").fetchone()
            approvals = connection.execute(
                "SELECT SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS pending,COUNT(*) AS total FROM approval_tokens"
            ).fetchone()
            notifications = connection.execute(
                "SELECT status,COUNT(*) AS total FROM notifications GROUP BY status"
            ).fetchall()
            messages = connection.execute(
                "SELECT state,COUNT(*) AS total FROM messages GROUP BY state"
            ).fetchall()
            oldest = connection.execute(
                "SELECT MIN(updated_at_epoch) AS oldest FROM assistant_jobs WHERE state NOT IN ('partial','completed','failed','canceled')"
            ).fetchone()
            db_mode = connection.execute("PRAGMA journal_mode").fetchone()
        return {
            "backend": "sqlite",
            "path": str(self.path),
            "schema_version": SCHEMA_VERSION,
            "journal_mode": str(db_mode[0] if db_mode else ""),
            "jobs": {str(row["state"]): int(row["total"] or 0) for row in job_rows},
            "messages": {str(row["state"]): int(row["total"] or 0) for row in messages},
            "attempts": int((attempts or {"total": 0})["total"] or 0),
            "approvals": {
                "pending": int((approvals or {"pending": 0})["pending"] or 0),
                "total": int((approvals or {"total": 0})["total"] or 0),
            },
            "notifications": {str(row["status"]): int(row["total"] or 0) for row in notifications},
            "oldest_active_job_seconds": max(0, int(time.time() - float((oldest or {"oldest": 0})["oldest"] or time.time()))),
        }


__all__ = [
    "ASSISTANT_JOB_STATES",
    "TERMINAL_JOB_STATES",
    "WhatsappBridgeStore",
]
