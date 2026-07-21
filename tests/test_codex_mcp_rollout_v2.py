from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time

import pytest

from backend.services import codex_console, codex_mcp_rollout, jk_codex_mcp_server


def test_rollout_is_off_without_signed_server_configuration():
    policy = codex_mcp_rollout.resolve_policy({"mode": "enabled"})
    assert policy["mode"] == "off"
    assert codex_mcp_rollout.execution_decision(policy, tool_id="product_data") == (
        False,
        "mcp_rollout_off",
    )


def test_pilot_is_limited_and_protocol_fallback_stops_after_external_call():
    policy = codex_mcp_rollout.resolve_policy(
        {"enabled": True, "mode": "pilot", "allowed_tools": ["product_data", "danger"]}
    )
    assert policy["allowed_tools"] == ["product_data"]
    assert codex_mcp_rollout.execution_decision(policy, tool_id="product_data")[0] is True
    assert codex_mcp_rollout.execution_decision(policy, tool_id="danger")[0] is False
    assert codex_mcp_rollout.fallback_allowed(external_call_count=0) is True
    assert codex_mcp_rollout.fallback_allowed(external_call_count=1) is False


def test_rollback_thresholds_are_fail_closed():
    assert codex_mcp_rollout.rollback_reason({"scope_violation": True}) == "scope_violation"
    assert codex_mcp_rollout.rollback_reason({"tasks": 20, "protocol_errors": 1}) == "protocol_error_rate"
    assert codex_mcp_rollout.rollback_reason({"baseline_p95_ms": 100, "observed_p95_ms": 201}) == "p95_above_two_times_baseline"


def test_idempotency_ledger_persists_only_sanitized_summary(tmp_path):
    path = tmp_path / "idempotency.sqlite3"
    store = codex_mcp_rollout.MCPIdempotencyStore(path)
    claim = store.begin(
        call_key="call-a", plan_hash="plan-a", call_index=0,
        tool_id="product_data", arguments_hash="args-a",
    )
    assert claim["state"] == "claimed"
    assert claim["claim_token"]
    assert store.finish(
        call_key="call-a",
        claim_token=claim["claim_token"],
        result={"success": True, "tool_id": "product_data", "rows": [{"email": "secret@example.com"}]},
    ) is True
    replay = store.begin(
        call_key="call-a", plan_hash="plan-a", call_index=0,
        tool_id="product_data", arguments_hash="args-a",
    )
    assert replay and replay["state"] == "completed"
    assert replay["result"]["persisted_summary_only"] is True
    assert "secret@example.com" not in path.read_bytes().decode("latin-1", "ignore")


def test_expired_running_lease_can_be_reclaimed_after_process_failure(tmp_path):
    path = tmp_path / "idempotency-lease.sqlite3"
    store = codex_mcp_rollout.MCPIdempotencyStore(path)
    args = dict(
        call_key="call-lease",
        plan_hash="plan-a",
        call_index=0,
        tool_id="product_data",
        arguments_hash="args-a",
    )
    first_claim = store.begin(**args, lease_seconds=60)
    assert first_claim["state"] == "claimed"
    assert store.begin(**args, lease_seconds=60)["state"] == "running"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE mcp_call_idempotency SET lease_expires_at_epoch=0 WHERE call_key=?",
            ("call-lease",),
        )
        connection.commit()
    second_claim = store.begin(**args, lease_seconds=60)
    assert second_claim["state"] == "claimed"
    assert second_claim["reclaimed"] is True
    assert second_claim["claim_token"] != first_claim["claim_token"]
    assert store.finish(
        call_key=args["call_key"],
        claim_token=first_claim["claim_token"],
        result={"success": True, "tool_id": "product_data"},
    ) is False
    assert store.finish(
        call_key=args["call_key"],
        claim_token=second_claim["claim_token"],
        result={"success": True, "tool_id": "product_data"},
    ) is True


def test_rollout_advance_enforces_order_minimums_and_automatic_rollback(tmp_path):
    store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "rollout-gated.sqlite3")
    assert store.advance({"enabled": True, "mode": "shadow"}, actor="admin")["mode"] == "shadow"
    with pytest.raises(ValueError, match="gate_not_met"):
        store.advance({"enabled": True, "mode": "pilot"}, actor="admin", observations={"shadow_decisions": 199})
    pilot = store.advance(
        {"enabled": True, "mode": "pilot"},
        actor="admin",
        observations={"shadow_decisions": 200},
    )
    assert pilot["mode"] == "pilot"
    rollback = store.evaluate_and_rollback({"scope_violation": True})
    assert rollback["rolled_back"] is True
    assert rollback["rollout"]["mode"] == "off"


def test_real_metric_window_triggers_automatic_protocol_and_latency_rollback(tmp_path):
    protocol_store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "protocol.sqlite3")
    protocol_store.put({"enabled": True, "mode": "enabled"}, actor="admin")
    result = {}
    for index in range(20):
        result = protocol_store.record_metric_and_evaluate(
            task_id=f"task-{index}",
            execution_id=f"execution-{index}",
            event_id=f"protocol-{index}",
            status="failed" if index == 19 else "completed",
            duration_ms=50,
            protocol_error=index == 19,
        )
    assert result["rolled_back"] is True
    assert result["reason"] == "protocol_error_rate"
    assert protocol_store.get()["mode"] == "off"

    latency_store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "latency.sqlite3")
    latency_store.put(
        {"enabled": True, "mode": "enabled", "baseline_p95_ms": 100},
        actor="admin",
    )
    latency = latency_store.record_metric_and_evaluate(
        task_id="latency-task",
        execution_id="latency-execution",
        event_id="latency-1",
        status="completed",
        duration_ms=201,
    )
    assert latency["rolled_back"] is True
    assert latency["reason"] == "p95_above_two_times_baseline"


def test_metrics_aggregate_calls_and_retries_once_per_task(tmp_path):
    store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "task-metrics.sqlite3")
    store.put({"enabled": True, "mode": "enabled"}, actor="admin")
    store.record_metric(
        task_id="task-a",
        execution_id="execution-a1",
        event_id="a-call-1",
        status="completed",
        duration_ms=40,
    )
    store.record_metric(
        task_id="task-a",
        execution_id="execution-a1",
        event_id="a-call-2",
        status="protocol_error",
        duration_ms=10,
        protocol_error=True,
        error_code="jsonrpc_method_not_found",
    )
    store.record_metric(
        task_id="task-a",
        execution_id="execution-a2",
        event_id="a-retry",
        status="completed",
        duration_ms=20,
    )
    store.record_metric(
        task_id="task-b",
        execution_id="execution-b1",
        # The same caller-local event ID in another task must not deduplicate
        # the second task's metric.
        event_id="a-call-1",
        status="completed",
        duration_ms=30,
    )

    metrics = store.current_metrics()
    assert metrics["tasks"] == 2
    assert metrics["executions"] == 3
    assert metrics["events"] == 4
    assert metrics["protocol_errors"] == 1
    assert metrics["protocol_error_events"] == 1
    assert metrics["observed_p95_ms"] == 70


def test_metric_schema_migrates_existing_rollout_database(tmp_path):
    path = tmp_path / "rollout-legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE mcp_rollout_metrics (
                event_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                protocol_error INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                created_at_epoch REAL NOT NULL
            )
            """
        )
    store = codex_mcp_rollout.MCPRolloutPolicyStore(path)
    store.put({"enabled": True, "mode": "enabled"}, actor="admin")
    store.record_metric(
        task_id="task-migrated",
        execution_id="execution-migrated",
        event_id="metric-migrated",
        status="completed",
        duration_ms=12,
    )
    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(mcp_rollout_metrics)").fetchall()
        }
    assert {"task_id", "execution_id", "error_code"}.issubset(columns)
    assert store.current_metrics()["tasks"] == 1


def test_shadow_observer_validates_and_compares_without_executing_mcp(monkeypatch, tmp_path):
    rollout_path = tmp_path / "tenant-shadow" / "codex_ai" / "mcp_rollout.sqlite3"
    idempotency_path = tmp_path / "shadow-idempotency.sqlite3"
    store = codex_mcp_rollout.MCPRolloutPolicyStore(rollout_path)
    policy = store.advance({"enabled": True, "mode": "shadow"}, actor="admin")
    arguments = {"loja": "Loja Alfa", "sku": "SKU-FICTICIO-001"}
    context = {
        "version": 2,
        "protocol": "mcp_v2",
        "task_id": "task-shadow-1",
        "execution_id": "execution-shadow-1",
        "client_id": "tenant-shadow",
        "permissions": {"full": False},
        "source_policy": {},
        "allowed_tools": ["product_data"],
        "rollout": policy,
        "rollout_policy_db_path": str(rollout_path),
        "idempotency_db_path": str(idempotency_path),
        "expires_at": int(time.time()) + 300,
    }
    context["plan"] = jk_codex_mcp_server.build_plan_v2(
        context,
        calls=[{"tool_id": "product_data", "arguments": arguments, "depends_on": [], "required": True}],
        stores=[{"store_id": "store-alpha", "name": "Loja Alfa", "seller_id": "1001", "site_id": "MLB"}],
    )
    encoded = base64.urlsafe_b64encode(
        json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = "shadow-secret-only-for-test"
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    monkeypatch.setattr(
        codex_console,
        "_codex_native_mcp_thread_config",
        lambda *_args, **_kwargs: {
            "mcp_servers": {
                "jk_system": {
                    "env": {
                        "JK_CODEX_MCP_CONTEXT_B64": encoded,
                        "JK_CODEX_MCP_CONTEXT_SIGNATURE": signature,
                        "JK_CODEX_MCP_CONTEXT_SECRET": secret,
                    }
                }
            }
        },
    )
    monkeypatch.setattr(codex_console, "_codex_base_info_dir", lambda: str(tmp_path))

    probe = codex_console._codex_mcp_shadow_prepare(
        {"task_id": "task-shadow-1", "client_id": "tenant-shadow"},
        {},
        policy,
    )
    assert probe["status"] == "prepared"
    result = codex_console._codex_mcp_shadow_finish(
        probe,
        {"tool_calls": [{"tool_id": "product_data", "args": arguments}]},
    )

    assert result == {
        "shadow_observed": True,
        "shadow_status": "match",
        "shadow_plan_tools_count": 1,
        "shadow_legacy_tools_count": 1,
        "external_call_executed": False,
    }
    metrics = store.current_metrics()
    assert metrics["shadow_decisions"] == 1
    assert metrics["shadow_matches"] == 1
    assert metrics["shadow_divergences"] == 0
    assert metrics["tasks"] == 1


def test_shadow_observer_rejects_rollout_database_from_another_tenant(monkeypatch, tmp_path):
    foreign_path = tmp_path / "tenant-beta" / "codex_ai" / "mcp_rollout.sqlite3"
    foreign_store = codex_mcp_rollout.MCPRolloutPolicyStore(foreign_path)
    policy = foreign_store.advance({"enabled": True, "mode": "shadow"}, actor="admin")
    context = {
        "version": 2,
        "protocol": "mcp_v2",
        "task_id": "task-shadow-scope",
        "execution_id": "execution-shadow-scope",
        "client_id": "tenant-alpha",
        "permissions": {},
        "source_policy": {},
        "allowed_tools": ["product_data"],
        "rollout": policy,
        "rollout_policy_db_path": str(foreign_path),
        "idempotency_db_path": str(tmp_path / "idempotency.sqlite3"),
        "expires_at": int(time.time()) + 300,
    }
    context["plan"] = jk_codex_mcp_server.build_plan_v2(
        context,
        calls=[{"tool_id": "product_data", "arguments": {}, "depends_on": [], "required": True}],
        stores=[{"store_id": "alpha", "name": "Loja Alfa", "seller_id": "1001", "site_id": "MLB"}],
    )
    encoded = base64.urlsafe_b64encode(
        json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = "shadow-scope-secret-for-test"
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    monkeypatch.setattr(
        codex_console,
        "_codex_native_mcp_thread_config",
        lambda *_args, **_kwargs: {
            "mcp_servers": {"jk_system": {"env": {
                "JK_CODEX_MCP_CONTEXT_B64": encoded,
                "JK_CODEX_MCP_CONTEXT_SIGNATURE": signature,
                "JK_CODEX_MCP_CONTEXT_SECRET": secret,
            }}}
        },
    )
    monkeypatch.setattr(codex_console, "_codex_base_info_dir", lambda: str(tmp_path))

    probe = codex_console._codex_mcp_shadow_prepare(
        {"task_id": "task-shadow-scope", "client_id": "tenant-alpha"},
        {},
        policy,
    )

    assert probe == {"status": "invalid", "reason": "shadow_plan_validation_failed"}
    assert foreign_store.current_metrics()["shadow_decisions"] == 0


def test_shadow_observer_records_divergence_without_tool_dispatch(tmp_path):
    rollout_path = tmp_path / "shadow-divergence.sqlite3"
    store = codex_mcp_rollout.MCPRolloutPolicyStore(rollout_path)
    policy = store.advance({"enabled": True, "mode": "shadow"}, actor="admin")
    probe = {
        "status": "prepared",
        "task_id": "task-shadow-divergence",
        "execution_id": "execution-shadow-divergence",
        "rollout_db_path": str(rollout_path),
        "rollout_version": policy["version"],
        "expected_calls": [
            {
                "fingerprint": codex_console._codex_mcp_shadow_call_fingerprint(
                    "product_data", {"loja": "Loja Alfa", "sku": "SKU-FICTICIO-001"}
                ),
                "required": True,
            }
        ],
    }

    result = codex_console._codex_mcp_shadow_finish(probe, {"tool_calls": []})

    assert result["shadow_status"] == "divergence"
    assert result["external_call_executed"] is False
    metrics = store.current_metrics()
    assert metrics["shadow_decisions"] == 1
    assert metrics["shadow_matches"] == 0
    assert metrics["shadow_divergences"] == 1


def test_shadow_gate_counts_only_comparison_events(tmp_path):
    store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "shadow-gate.sqlite3")
    store.advance({"enabled": True, "mode": "shadow"}, actor="admin")
    for index, status in enumerate(("protocol_error", "critical_violation", "shadow_validated")):
        store.record_metric(
            task_id=f"non-comparison-{index}",
            execution_id=f"execution-{index}",
            event_id=f"event-{index}",
            status=status,
            protocol_error=status == "protocol_error",
        )

    metrics = store.current_metrics()
    assert metrics["tasks"] == 3
    assert metrics["shadow_decisions"] == 0
    assert metrics["shadow_matches"] == 0
    assert metrics["shadow_divergences"] == 0


def test_shadow_observation_is_discarded_after_rollout_stage_changes(tmp_path):
    rollout_path = tmp_path / "shadow-stale.sqlite3"
    store = codex_mcp_rollout.MCPRolloutPolicyStore(rollout_path)
    shadow = store.advance({"enabled": True, "mode": "shadow"}, actor="admin")
    probe = {
        "status": "prepared",
        "task_id": "task-shadow-stale",
        "execution_id": "execution-shadow-stale",
        "rollout_db_path": str(rollout_path),
        "rollout_version": shadow["version"],
        "expected_calls": [],
    }
    store.advance(
        {"enabled": True, "mode": "pilot"},
        actor="admin",
        observations={"shadow_decisions": 200},
    )

    result = codex_console._codex_mcp_shadow_finish(probe, {"tool_calls": []})

    assert result["shadow_status"] == "stale_rollout"
    assert result["shadow_observed"] is False
    assert store.current_metrics()["events"] == 0
