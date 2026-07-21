"""Server-controlled rollout and durable idempotency for the internal JK MCP.

This module deliberately does not read a client/model supplied switch.  A
signed backend context must carry ``enabled=true`` and an explicit mode; absent
that context the MCP remains off.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional


ROLLOUT_MODES = {
    "off",
    "shadow",
    "pilot",
    "whatsapp_5",
    "whatsapp_25",
    "whatsapp_50",
    "read_only_100",
    "enabled",
}
ROLLOUT_PERCENT_BY_MODE = {
    "off": 0,
    "shadow": 0,
    "pilot": 0,
    "whatsapp_5": 5,
    "whatsapp_25": 25,
    "whatsapp_50": 50,
    "read_only_100": 100,
    "enabled": 100,
}
PUBLIC_ROLLOUT_SEQUENCE = (
    "off",
    "shadow",
    "pilot",
    "whatsapp_5",
    "whatsapp_25",
    "whatsapp_50",
    "read_only_100",
)
ROLLOUT_ADVANCE_GATES = {
    "pilot": {"shadow_decisions": 200, "tasks": 0, "days": 0},
    "whatsapp_5": {"shadow_decisions": 0, "tasks": 50, "days": 3},
    "whatsapp_25": {"shadow_decisions": 0, "tasks": 50, "days": 7},
    "whatsapp_50": {"shadow_decisions": 0, "tasks": 100, "days": 7},
    "read_only_100": {"shadow_decisions": 0, "tasks": 200, "days": 14},
}
PILOT_READ_ONLY_TOOLS = frozenset({"product_data", "sales_ranking", "bling_stock_balances"})
SANITIZATION_VERSION = "mcp-v2-summary-1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _safe_metric_id(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"mcp_metric_{field}_required")
    if len(raw) <= 160 and all(char.isalnum() or char in {"-", "_", ".", ":"} for char in raw):
        return raw
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def resolve_policy(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    enabled = raw.get("enabled") is True
    mode = str(raw.get("mode") or "off").strip().lower()
    if not enabled or mode not in ROLLOUT_MODES:
        mode = "off"
    allowed = {
        str(item or "").strip()
        for item in list(raw.get("allowed_tools") or [])
        if str(item or "").strip()
    }
    if mode == "pilot":
        allowed = allowed.intersection(PILOT_READ_ONLY_TOOLS) if allowed else set(PILOT_READ_ONLY_TOOLS)
    return {
        "enabled": mode != "off",
        "mode": mode,
        "allowed_tools": sorted(allowed),
        "cohort_percent": ROLLOUT_PERCENT_BY_MODE[mode],
        "baseline_p95_ms": max(0.0, float(raw.get("baseline_p95_ms") or 0)),
    }


def in_cohort(policy: dict[str, Any], *, subject: str, secret: str) -> bool:
    percent = int(policy.get("cohort_percent") or 0)
    if percent >= 100:
        return True
    if percent <= 0 or not subject or not secret:
        return False
    digest = hmac.new(secret.encode("utf-8"), subject.encode("utf-8"), hashlib.sha256).digest()
    return int.from_bytes(digest[:4], "big") % 100 < percent


def execution_decision(policy: dict[str, Any], *, tool_id: str) -> tuple[bool, str]:
    mode = str(policy.get("mode") or "off")
    if mode == "off":
        return False, "mcp_rollout_off"
    if mode == "shadow":
        return False, "mcp_shadow_no_execution"
    allowed = set(policy.get("allowed_tools") or [])
    if allowed and str(tool_id or "") not in allowed:
        return False, "mcp_tool_outside_rollout"
    return True, "mcp_execution_allowed"


def fallback_allowed(*, external_call_count: int) -> bool:
    """Protocol fallback is legal only before the first external MCP call."""

    return max(0, int(external_call_count or 0)) == 0


def rollback_reason(metrics: Any) -> str:
    value = metrics if isinstance(metrics, dict) else {}
    for flag in ("scope_violation", "plan_violation", "tool_violation", "dlp_leak", "duplicate_call", "idempotency_mismatch"):
        if value.get(flag) is True:
            return flag
    tasks = max(0, int(value.get("tasks") or 0))
    protocol_errors = max(0, int(value.get("protocol_errors") or 0))
    if tasks >= 20 and protocol_errors / tasks > 0.02:
        return "protocol_error_rate"
    baseline = max(0.0, float(value.get("baseline_p95_ms") or 0))
    observed = max(0.0, float(value.get("observed_p95_ms") or 0))
    if baseline and observed > 2 * baseline:
        return "p95_above_two_times_baseline"
    return ""


def _safe_replay_result(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    allowed = {
        "success", "tool_id", "status", "error_code", "retryable",
        "dados_suficientes", "coverage_complete", "total", "count", "source",
    }
    safe: dict[str, Any] = {
        str(key): item
        for key, item in raw.items()
        if str(key) in allowed and isinstance(item, (str, int, float, bool, type(None)))
    }
    if raw.get("success") is True:
        # Raw rows are deliberately not durable.  A process restart therefore
        # returns an explicit partial state instead of pretending the summary
        # contains enough evidence to answer the user.
        safe.update(
            {
                "success": False,
                "dados_suficientes": False,
                "coverage_complete": False,
                "error_code": "mcp_persisted_result_summary_only",
                "retryable": False,
            }
        )
    safe["sanitization_version"] = SANITIZATION_VERSION
    safe["persisted_summary_only"] = True
    return safe


class MCPIdempotencyStore:
    """SQLite/WAL ledger that never stores tool arguments or raw result rows."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_call_idempotency (
                    call_key TEXT PRIMARY KEY,
                    plan_hash TEXT NOT NULL,
                    call_index INTEGER NOT NULL,
                    tool_id TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    lease_expires_at_epoch REAL NOT NULL DEFAULT 0,
                    lease_owner_token TEXT NOT NULL DEFAULT '',
                    updated_at_epoch REAL NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(mcp_call_idempotency)").fetchall()
            }
            if "lease_expires_at_epoch" not in columns:
                connection.execute(
                    "ALTER TABLE mcp_call_idempotency "
                    "ADD COLUMN lease_expires_at_epoch REAL NOT NULL DEFAULT 0"
                )
            if "lease_owner_token" not in columns:
                connection.execute(
                    "ALTER TABLE mcp_call_idempotency "
                    "ADD COLUMN lease_owner_token TEXT NOT NULL DEFAULT ''"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def begin(
        self,
        *,
        call_key: str,
        plan_hash: str,
        call_index: int,
        tool_id: str,
        arguments_hash: str,
        lease_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Claim a call lease or return its current durable state.

        A successful claim always returns an explicit ``claim_token``.  The
        executor must present that exact token to :meth:`finish`; keeping the
        token outside the store object is the fencing boundary that prevents a
        stale executor from completing a lease reclaimed by another process.
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT plan_hash,call_index,tool_id,arguments_hash,state,result_summary_json,"
                "lease_expires_at_epoch,lease_owner_token "
                "FROM mcp_call_idempotency WHERE call_key=?",
                (call_key,),
            ).fetchone()
            if row:
                if (
                    str(row["plan_hash"]) != plan_hash
                    or int(row["call_index"]) != int(call_index)
                    or str(row["tool_id"]) != tool_id
                    or str(row["arguments_hash"]) != arguments_hash
                ):
                    raise RuntimeError("mcp_idempotency_mismatch")
                try:
                    summary = json.loads(str(row["result_summary_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    summary = {}
                now = time.time()
                if (
                    str(row["state"]) == "running"
                    and float(row["lease_expires_at_epoch"] or 0.0) <= now
                ):
                    lease_token = uuid.uuid4().hex
                    cursor = connection.execute(
                        "UPDATE mcp_call_idempotency SET lease_expires_at_epoch=?,"
                        "lease_owner_token=?,updated_at_epoch=? "
                        "WHERE call_key=? AND state='running' "
                        "AND lease_owner_token=? AND lease_expires_at_epoch<=?",
                        (
                            now + max(1.0, float(lease_seconds or 1.0)),
                            lease_token,
                            now,
                            call_key,
                            str(row["lease_owner_token"] or ""),
                            now,
                        ),
                    )
                    connection.commit()
                    if cursor.rowcount != 1:
                        return {"state": "running", "result": {}, "claim_token": ""}
                    return {
                        "state": "claimed",
                        "result": {},
                        "claim_token": lease_token,
                        "reclaimed": True,
                    }
                connection.commit()
                return {
                    "state": str(row["state"]),
                    "result": summary if isinstance(summary, dict) else {},
                    "claim_token": "",
                }
            now = time.time()
            lease_token = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO mcp_call_idempotency("
                "call_key,plan_hash,call_index,tool_id,arguments_hash,state,"
                "lease_expires_at_epoch,lease_owner_token,updated_at_epoch) "
                "VALUES(?,?,?,?,?,'running',?,?,?)",
                (
                    call_key,
                    plan_hash,
                    int(call_index),
                    tool_id,
                    arguments_hash,
                    now + max(1.0, float(lease_seconds or 1.0)),
                    lease_token,
                    now,
                ),
            )
            connection.commit()
        return {
            "state": "claimed",
            "result": {},
            "claim_token": lease_token,
            "reclaimed": False,
        }

    def finish(self, *, call_key: str, claim_token: str, result: dict[str, Any]) -> bool:
        state = "completed" if result.get("success") is True else "failed"
        lease_token = str(claim_token or "").strip()
        if not lease_token:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE mcp_call_idempotency SET state=?,result_summary_json=?,"
                "lease_expires_at_epoch=0,lease_owner_token='',updated_at_epoch=? "
                "WHERE call_key=? AND state='running' AND lease_owner_token=?",
                (
                    state,
                    _json(_safe_replay_result(result)),
                    time.time(),
                    call_key,
                    lease_token,
                ),
            )
            connection.commit()
        return cursor.rowcount == 1


class MCPRolloutPolicyStore:
    """Transactional current policy plus mandatory, content-free audit."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_rollout_current (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    policy_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at_epoch REAL NOT NULL,
                    updated_by_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_rollout_audit (
                    event_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    policy_hash TEXT NOT NULL,
                    actor_hash TEXT NOT NULL,
                    created_at_epoch REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_rollout_metrics (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL DEFAULT '',
                    execution_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    protocol_error INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL NOT NULL DEFAULT 0,
                    created_at_epoch REAL NOT NULL
                );
                """
            )
            metric_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(mcp_rollout_metrics)").fetchall()
            }
            for name, definition in (
                ("task_id", "TEXT NOT NULL DEFAULT ''"),
                ("execution_id", "TEXT NOT NULL DEFAULT ''"),
                ("error_code", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in metric_columns:
                    connection.execute(
                        f"ALTER TABLE mcp_rollout_metrics ADD COLUMN {name} {definition}"
                    )
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_mcp_rollout_metrics_created
                    ON mcp_rollout_metrics(created_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_mcp_rollout_metrics_task_execution
                    ON mcp_rollout_metrics(mode, task_id, execution_id, created_at_epoch);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @staticmethod
    def _actor_hash(actor: str) -> str:
        from backend.services.codex_ai_telemetry import secure_hmac_identifier

        return secure_hmac_identifier(actor or "unknown", namespace="mcp_rollout_actor")

    def get(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT policy_json,version,updated_at_epoch FROM mcp_rollout_current WHERE singleton=1"
            ).fetchone()
        if not row:
            return {**resolve_policy({}), "version": 0, "updated_at_epoch": 0.0}
        try:
            raw = json.loads(str(row["policy_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        return {
            **resolve_policy(raw),
            "version": int(row["version"] or 0),
            "updated_at_epoch": float(row["updated_at_epoch"] or 0.0),
        }

    def put(self, value: Any, *, actor: str) -> dict[str, Any]:
        policy = resolve_policy(value)
        now = time.time()
        actor_hash = self._actor_hash(actor)
        encoded = _json(policy)
        policy_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version FROM mcp_rollout_current WHERE singleton=1"
            ).fetchone()
            version = int(row["version"] or 0) + 1 if row else 1
            event_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO mcp_rollout_audit(event_id,version,mode,policy_hash,actor_hash,created_at_epoch) "
                "VALUES(?,?,?,?,?,?)",
                (event_id, version, policy["mode"], policy_hash, actor_hash, now),
            )
            connection.execute(
                "INSERT INTO mcp_rollout_current(singleton,policy_json,version,updated_at_epoch,updated_by_hash) "
                "VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                "policy_json=excluded.policy_json,version=excluded.version,"
                "updated_at_epoch=excluded.updated_at_epoch,updated_by_hash=excluded.updated_by_hash",
                (encoded, version, now, actor_hash),
            )
            connection.commit()
        return {**policy, "version": version, "updated_at_epoch": now}

    def advance(self, value: Any, *, actor: str, observations: Any = None) -> dict[str, Any]:
        """Apply a manual stage transition only after its minimum evidence gate."""

        requested = resolve_policy(value)
        current = self.get()
        target = str(requested.get("mode") or "off")
        current_mode = str(current.get("mode") or "off")
        if target not in PUBLIC_ROLLOUT_SEQUENCE:
            raise ValueError("mcp_rollout_mode_not_public")
        current_index = PUBLIC_ROLLOUT_SEQUENCE.index(current_mode) if current_mode in PUBLIC_ROLLOUT_SEQUENCE else 0
        target_index = PUBLIC_ROLLOUT_SEQUENCE.index(target)
        if target_index > current_index + 1:
            raise ValueError("mcp_rollout_stage_skip_forbidden")
        if target_index == current_index + 1:
            metrics = observations if isinstance(observations, dict) else {}
            gate = ROLLOUT_ADVANCE_GATES.get(target, {})
            for field, minimum in gate.items():
                try:
                    actual = float(metrics.get(field) or 0)
                except (TypeError, ValueError):
                    actual = 0.0
                if actual < minimum:
                    raise ValueError(f"mcp_rollout_gate_not_met:{field}:{minimum}")
            if rollback_reason(metrics):
                raise ValueError("mcp_rollout_safety_gate_failed")
        return self.put(requested, actor=actor)

    def evaluate_and_rollback(self, metrics: Any, *, actor: str = "automatic") -> dict[str, Any]:
        reason = rollback_reason(metrics)
        if not reason:
            return {"rolled_back": False, "reason": "", "rollout": self.get()}
        rollout = self.put({"enabled": False, "mode": "off"}, actor=f"{actor}:{reason}")
        return {"rolled_back": True, "reason": reason, "rollout": rollout}

    def record_metric(
        self,
        *,
        task_id: str,
        execution_id: str,
        event_id: str = "",
        status: str,
        duration_ms: float = 0.0,
        protocol_error: bool = False,
        error_code: str = "",
    ) -> dict[str, Any]:
        """Persist one content-free event and return task-aggregated metrics."""

        current = self.get()
        now = time.time()
        safe_task_id = _safe_metric_id(task_id, field="task_id")
        safe_execution_id = _safe_metric_id(execution_id, field="execution_id")
        raw_event_id = str(event_id or uuid.uuid4().hex)
        safe_event_id = hashlib.sha256(
            f"{safe_task_id}\0{safe_execution_id}\0{raw_event_id}".encode("utf-8")
        ).hexdigest()
        safe_status = str(status or "unknown").strip().lower()[:40] or "unknown"
        safe_error_code = str(error_code or "").strip().lower()[:100]
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO mcp_rollout_metrics("
                "event_id,task_id,execution_id,mode,status,error_code,"
                "protocol_error,duration_ms,created_at_epoch) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    safe_event_id,
                    safe_task_id,
                    safe_execution_id,
                    str(current.get("mode") or "off")[:40],
                    safe_status,
                    safe_error_code,
                    1 if protocol_error else 0,
                    max(0.0, float(duration_ms or 0.0)),
                    now,
                ),
            )
            connection.commit()
        return self.current_metrics(current=current)

    def current_metrics(self, *, current: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Aggregate the active rollout window once per logical task.

        A task may contain several tool calls, JSON-RPC messages or retried MCP
        executions.  The rollback denominator is therefore the number of
        distinct task IDs, while protocol errors count tasks affected by at
        least one real protocol failure.  Latency is the sum of event durations
        for each task, then p95 across tasks.
        """

        policy = current if isinstance(current, dict) else self.get()
        since = max(0.0, float(policy.get("updated_at_epoch") or 0.0))
        mode = str(policy.get("mode") or "off")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id,task_id,execution_id,status,protocol_error,duration_ms "
                "FROM mcp_rollout_metrics WHERE mode=? AND created_at_epoch>=?",
                (mode, since),
            ).fetchall()
        tasks: dict[str, dict[str, Any]] = {}
        executions: set[tuple[str, str]] = set()
        protocol_error_events = 0
        for row in rows:
            # Rows from a pre-migration database have blank IDs. Keep them
            # isolated by event instead of collapsing unrelated legacy tasks.
            event_id = str(row["event_id"] or "")
            task_id = str(row["task_id"] or "") or f"legacy-task:{event_id}"
            execution_id = str(row["execution_id"] or "") or f"legacy-execution:{event_id}"
            executions.add((task_id, execution_id))
            item = tasks.setdefault(task_id, {"duration_ms": 0.0, "protocol_error": False, "statuses": set()})
            item["duration_ms"] += max(0.0, float(row["duration_ms"] or 0.0))
            item["statuses"].add(str(row["status"] or ""))
            if int(row["protocol_error"] or 0):
                item["protocol_error"] = True
                protocol_error_events += 1
        durations = sorted(float(item["duration_ms"]) for item in tasks.values())
        p95 = 0.0
        if durations:
            rank = max(0, min(len(durations) - 1, math.ceil(len(durations) * 0.95) - 1))
            p95 = durations[rank]
        shadow_decisions = sum(
            1
            for item in tasks.values()
            if {"shadow_match", "shadow_divergence"}.intersection(item["statuses"])
        ) if mode == "shadow" else 0
        return {
            "tasks": len(tasks),
            "executions": len(executions),
            "events": len(rows),
            "shadow_decisions": shadow_decisions,
            "shadow_matches": sum(
                1 for item in tasks.values() if "shadow_match" in item["statuses"]
            ) if mode == "shadow" else 0,
            "shadow_divergences": sum(
                1 for item in tasks.values() if "shadow_divergence" in item["statuses"]
            ) if mode == "shadow" else 0,
            "days": max(0.0, (time.time() - since) / 86_400.0) if since else 0.0,
            "protocol_errors": sum(1 for item in tasks.values() if item["protocol_error"]),
            "protocol_error_events": protocol_error_events,
            "observed_p95_ms": p95,
            "baseline_p95_ms": max(0.0, float(policy.get("baseline_p95_ms") or 0.0)),
        }

    def record_metric_and_evaluate(
        self,
        *,
        task_id: str,
        execution_id: str,
        event_id: str = "",
        status: str,
        duration_ms: float = 0.0,
        protocol_error: bool = False,
        error_code: str = "",
    ) -> dict[str, Any]:
        metrics = self.record_metric(
            task_id=task_id,
            execution_id=execution_id,
            event_id=event_id,
            status=status,
            duration_ms=duration_ms,
            protocol_error=protocol_error,
            error_code=error_code,
        )
        return self.evaluate_and_rollback(metrics, actor="automatic_metrics")


__all__ = [
    "MCPIdempotencyStore",
    "MCPRolloutPolicyStore",
    "PILOT_READ_ONLY_TOOLS",
    "PUBLIC_ROLLOUT_SEQUENCE",
    "ROLLOUT_ADVANCE_GATES",
    "ROLLOUT_MODES",
    "ROLLOUT_PERCENT_BY_MODE",
    "execution_decision",
    "fallback_allowed",
    "in_cohort",
    "resolve_policy",
    "rollback_reason",
]
