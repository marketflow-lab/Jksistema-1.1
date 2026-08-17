"""SQLite schema creation and migrations for Codex assistant state."""

from __future__ import annotations

import sqlite3

from .common import (
    _ensure_meta_schema,
    _json_dumps,
    _json_loads,
    _meta_get,
    _meta_set,
    _safe_id,
)
from .customer_reply_state import (
    _customer_reply_durable_payload,
    _customer_reply_plan_durable_payload,
)


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

def _ensure_report_schema(conn: sqlite3.Connection) -> None:
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

def _ensure_management_schema(conn: sqlite3.Connection) -> None:
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

def _ensure_agent_plan_schema(conn: sqlite3.Connection) -> None:
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

def _ensure_customer_reply_schema(conn: sqlite3.Connection) -> None:
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

def _migrate_customer_reply_payloads(conn: sqlite3.Connection) -> None:
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

def _ensure_agent_guidance_schema(conn: sqlite3.Connection) -> None:
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

def _ensure_action_schema(conn: sqlite3.Connection) -> None:
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

def _ensure_state_schema(conn: sqlite3.Connection) -> None:
    _ensure_meta_schema(conn)
    _ensure_report_schema(conn)
    _ensure_management_schema(conn)
    _ensure_agent_plan_schema(conn)
    _ensure_customer_reply_schema(conn)
    _migrate_customer_reply_payloads(conn)
    _ensure_agent_guidance_schema(conn)
    _ensure_action_schema(conn)
