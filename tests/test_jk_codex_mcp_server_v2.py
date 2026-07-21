from __future__ import annotations

import sqlite3
import sys
import types
import uuid

from backend.services import codex_assistant, codex_console, codex_mcp_rollout
from backend.services import jk_codex_mcp_server as mcp_server


def _context(tmp_path, *, rollout_mode="enabled"):
    base = {
        "client_id": "tenant-a",
        "permissions": {"full": True},
        "source_policy": {},
        "allowed_tools": ["product_data"],
    }
    arguments = {"loja": "JK Pecas", "sku": "001"}
    plan = mcp_server.build_plan_v2(
        base,
        calls=[{"tool_id": "product_data", "arguments": arguments, "depends_on": [], "required": True}],
        stores=[{"store_id": "store-a", "name": "JK Pecas", "seller_id": "123", "site_id": "MLB"}],
    )
    return {
        **base,
        "protocol": "mcp_v2",
        "task_id": "task-mcp-v2",
        "execution_id": f"execution-{uuid.uuid4().hex}",
        "plan": plan,
        "authorized_stores": ["JK Pecas"],
        "store_scope_valid": True,
        "rollout": {"enabled": True, "mode": rollout_mode, "allowed_tools": ["product_data"]},
        "rollout_policy_db_path": str(tmp_path / "rollout.sqlite3"),
        "idempotency_db_path": str(tmp_path / "mcp.sqlite3"),
    }


def _server(monkeypatch, tmp_path, *, rollout_mode="enabled"):
    monkeypatch.setitem(sys.modules, "backend_api", types.SimpleNamespace())
    monkeypatch.setattr(codex_assistant, "configure_codex_assistant_runtime", lambda *_args: None)
    monkeypatch.setattr(
        codex_console,
        "_codex_agent_tool_catalog",
        lambda *_args, **_kwargs: [{"id": "product_data", "description": "Consulta", "external": False}],
    )
    monkeypatch.setattr(codex_console, "_codex_agent_source_policy_error", lambda *_args, **_kwargs: "")
    context = _context(tmp_path, rollout_mode=rollout_mode)
    codex_mcp_rollout.MCPRolloutPolicyStore(
        tmp_path / "rollout.sqlite3"
    ).put(context["rollout"], actor="test")
    return mcp_server.JKCodexMCP(context)


def test_plan_hash_schema_and_stable_store_scope_are_required(tmp_path):
    context = _context(tmp_path)
    assert mcp_server.validate_plan_v2(context)["calls"][0]["arguments"]["sku"] == "001"
    context["plan"]["calls"][0]["arguments"]["sku"] = "002"
    try:
        mcp_server.validate_plan_v2(context)
    except RuntimeError as exc:
        assert str(exc) == "mcp_plan_hash_invalid"
    else:
        raise AssertionError("tampered plan accepted")


def test_tools_use_closed_exact_schema_and_dispatch_materialized_arguments(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **kwargs: calls.append(kwargs) or {"success": True, "tool_id": kwargs["tool_id"], "rows": [{"saldo": 7}]},
    )
    server = _server(monkeypatch, tmp_path)
    schema = server.tools()[0]["inputSchema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["sku"]["const"] == "001"

    mismatch = server.call_tool("product_data", {"loja": "JK Pecas", "sku": "002"})
    assert mismatch["isError"] is True
    assert calls == []

    # A plan violation disables this MCP process immediately.  A new signed
    # execution is required before a valid call can proceed.
    server = _server(monkeypatch, tmp_path)
    result = server.call_tool("product_data", {"loja": "JK Pecas", "sku": "001"})
    assert result["isError"] is False
    assert calls[0]["args"] == {"loja": "JK Pecas", "sku": "001"}
    replay = server.call_tool("product_data", {"loja": "JK Pecas", "sku": "001"})
    assert "idempotent_replay" in replay["content"][0]["text"]


def test_shadow_validates_without_external_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("shadow executed tool")),
    )
    server = _server(monkeypatch, tmp_path, rollout_mode="shadow")
    result = server.call_tool("product_data", {"loja": "JK Pecas", "sku": "001"})
    assert result["isError"] is False
    assert "shadow_validated" in result["content"][0]["text"]


def test_idempotency_survives_server_restart_without_reexecuting(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **kwargs: calls.append(kwargs) or {"success": True, "tool_id": kwargs["tool_id"], "rows": [{"private": "not-persisted"}]},
    )
    first = _server(monkeypatch, tmp_path)
    first.call_tool("product_data", {"loja": "JK Pecas", "sku": "001"})
    second = _server(monkeypatch, tmp_path)
    replay = second.call_tool("product_data", {"loja": "JK Pecas", "sku": "001"})
    assert len(calls) == 1
    assert replay["isError"] is True
    assert "mcp_persisted_result_summary_only" in replay["content"][0]["text"]
    assert "persisted_summary_only" in replay["content"][0]["text"]
    assert "not-persisted" not in (tmp_path / "mcp.sqlite3").read_bytes().decode("latin-1", "ignore")


def test_jsonrpc_and_unknown_method_errors_are_real_protocol_metrics(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)

    invalid = mcp_server._dispatch_message(
        server,
        {"jsonrpc": "1.0", "id": "invalid-1", "method": "ping"},
    )
    missing = mcp_server._dispatch_message(
        server,
        {"jsonrpc": "2.0", "id": "missing-1", "method": "unknown/method"},
    )

    assert invalid and invalid["error"]["code"] == -32600
    assert missing and missing["error"]["code"] == -32601
    metrics = server.rollout_store.current_metrics()
    assert metrics["tasks"] == 1
    assert metrics["executions"] == 1
    assert metrics["protocol_errors"] == 1
    assert metrics["protocol_error_events"] == 2
    with sqlite3.connect(tmp_path / "rollout.sqlite3") as connection:
        codes = {
            row[0]
            for row in connection.execute(
                "SELECT error_code FROM mcp_rollout_metrics WHERE protocol_error=1"
            ).fetchall()
        }
    assert codes == {"jsonrpc_invalid_request", "jsonrpc_method_not_found"}
