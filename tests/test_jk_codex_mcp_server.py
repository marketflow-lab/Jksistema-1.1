from __future__ import annotations

import base64
import json
import os
import subprocess

from backend.services import codex_mcp_rollout, integracoes, jk_codex_mcp_server
from backend.services.codex.console import agent_prompt as console_agent_prompt
from backend.services.codex.console import runtime as console_runtime


def _start_server(monkeypatch):
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda _client_id: [{
            "nome": "JK Pecas",
            "integracoes": {"mercadolivre": {"user_id": "123", "site_id": "MLB"}},
        }],
    )
    screen_context = {
        "selection": {
            "query_policy": {
                "authorized_stores": ["JK Pecas"],
                "source_policy": {"required_tools": ["mercado_livre_orders"]},
            }
        }
    }
    task = {
        "task_id": "task-mcp-test",
        "conversation_id": "conversation-mcp-test",
        "origin": "whatsapp",
        "client_id": "cliente",
        "created_by": "admin",
        "permissions": {"full": True},
        "data_selection_trust_marker": console_agent_prompt._CODEX_AGENT_DATA_SELECTION_TRUST_MARKER,
        "data_selection": {
            "schema_version": "1.0",
            "action": "collect",
            "tool_calls": [{
                "tool_id": "mercado_livre_orders",
                "arguments": {"loja": "JK Pecas", "message": "ultima venda"},
                "depends_on": [],
                "required": True,
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        "deadline_at": console_runtime._codex_deadline_at(180),
        "channel_metadata": {
            "wa_id": "5511999999999",
            "query_policy": {
                "authorized_stores": ["JK Pecas"],
                "source_policy": {"required_tools": ["mercado_livre_orders"]},
            },
        },
    }
    config = console_runtime._codex_native_mcp_thread_config(task, screen_context)
    definition = config["mcp_servers"]["jk_system"]
    env = os.environ.copy()
    env.update(definition["env"])
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [definition["command"], *definition["args"]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _send(process, payload):
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def test_signed_mcp_server_lists_only_selected_read_only_catalog_and_enforces_store_scope(monkeypatch):
    process = _start_server(monkeypatch)
    try:
        initialized = _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            },
        )
        assert initialized["result"]["serverInfo"]["name"] == "jk-system-readonly"

        listed = _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = listed["result"]["tools"]
        assert tools
        assert all(item["annotations"]["readOnlyHint"] is True for item in tools)
        assert {item["name"] for item in tools} == {"mercado_livre_orders"}

        blocked = _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "mercado_livre_orders", "arguments": {"loja": "Deckas", "message": "ultima venda"}},
            },
        )
        result = blocked["result"]
        assert result["isError"] is True
        assert "fora do escopo autorizado" in result["content"][0]["text"]
    finally:
        process.terminate()
        process.wait(timeout=10)


def _decode_signed_context(config: dict) -> dict:
    encoded = config["mcp_servers"]["jk_system"]["env"]["JK_CODEX_MCP_CONTEXT_B64"]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8"))


def test_unbounded_whatsapp_mcp_context_has_independent_reissuable_security_ttl(monkeypatch):
    monkeypatch.setattr(
        integracoes,
        "carregar_lojas",
        lambda _client_id: [{
            "nome": "JK Pecas",
            "integracoes": {"mercadolivre": {"user_id": "123", "site_id": "MLB"}},
        }],
    )
    issued_times = iter((1_000, 1_200))
    monkeypatch.setattr(console_runtime.time, "time", lambda: next(issued_times))
    task = {
        "task_id": "task-mcp-unbounded",
        "conversation_id": "conversation-mcp-unbounded",
        "origin": "whatsapp",
        "client_id": "cliente",
        "created_by": "admin",
        "permissions": {"full": True},
        "deadline_enabled": False,
        "deadline_seconds": 0,
        "deadline_at": "",
        "data_selection_trust_marker": console_agent_prompt._CODEX_AGENT_DATA_SELECTION_TRUST_MARKER,
        "data_selection": {
            "schema_version": "1.0",
            "action": "collect",
            "tool_calls": [{
                "tool_id": "mercado_livre_orders",
                "arguments": {"loja": "JK Pecas", "message": "ultima venda"},
                "depends_on": [],
                "required": True,
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        "channel_metadata": {"wa_id": "redacted"},
    }

    first = _decode_signed_context(console_runtime._codex_native_mcp_thread_config(task, {}))
    second = _decode_signed_context(console_runtime._codex_native_mcp_thread_config(task, {}))

    assert first["deadline_at_epoch"] == 0
    assert first["tool_timeout_seconds"] == 60
    assert first["expires_at"] - first["issued_at"] == 15 * 60
    assert second["issued_at"] > first["issued_at"]
    assert second["expires_at"] > first["expires_at"]


def test_mcp_tool_timeout_is_per_call_and_not_the_expired_global_task_deadline(monkeypatch):
    captured: dict = {}

    class Assistant:
        @staticmethod
        def execute_tool_call(**kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "tool_id": kwargs["tool_id"],
                "records": 1,
                "evidence": {
                    "schema": "jk.codex.evidence.v1",
                    "status": "complete",
                    "claim_scope": "full",
                    "coverage_complete": True,
                    "confidence": "high",
                    "freshness": "live",
                    "retryable": False,
                    "reason": "registro confirmado",
                    "missing_fields": [],
                    "sources": [],
                    "attempted_fallbacks": [],
                    "next_sources": [],
                },
            }

    class Console:
        @staticmethod
        def source_policy_error(*_args, **_kwargs):
            return ""

    server = object.__new__(jk_codex_mcp_server.JKCodexMCP)
    server.context = {
        "client_id": "cliente",
        "username": "admin",
        "tool_timeout_seconds": 60,
        "deadline_at_epoch": 1,
    }
    server.permissions = {"full": True}
    server.screen_context = {}
    server.source_policy = {}
    server.allowed_tools = {"mercado_livre_orders"}
    server.authorized_stores = set()
    server.store_scope_valid = False
    server.previous_results = []
    server.call_cache = {}
    server.result_path = None
    server.assistant_execution = Assistant()
    server.console_execution = Console()
    server.codex_mcp_rollout = codex_mcp_rollout
    plan_context = {
        "client_id": "cliente",
        "permissions": server.permissions,
        "source_policy": server.source_policy,
    }
    server.plan = jk_codex_mcp_server.build_plan_v2(
        plan_context,
        calls=[{
            "tool_id": "mercado_livre_orders",
            "arguments": {"message": "ultima venda"},
            "depends_on": [],
            "required": True,
        }],
        stores=[{
            "store_id": "store-1",
            "name": "JK Pecas",
            "seller_id": "123",
            "site_id": "MLB",
        }],
    )
    server.plan_calls = list(server.plan["calls"])
    server.completed_indexes = set()
    server.successful_indexes = set()
    server.external_call_count = 0
    server.rollout_policy = {
        "enabled": True,
        "mode": "read_only_100",
        "allowed_tools": ["mercado_livre_orders"],
        "cohort_percent": 100,
        "baseline_p95_ms": 0,
    }
    server.rollout_store = None
    server.idempotency = None
    server.tools = lambda: [{"name": "mercado_livre_orders"}]
    monkeypatch.setattr(jk_codex_mcp_server.time, "monotonic", lambda: 5_000.0)

    result = server.call_tool("mercado_livre_orders", {"message": "ultima venda"})

    assert result["isError"] is False
    assert captured["query_deadline"] == 5_060.0
