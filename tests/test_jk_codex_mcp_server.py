from __future__ import annotations

import json
import os
import subprocess

from backend.services import codex_console


def _start_server():
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
        "deadline_at": codex_console._codex_deadline_at(180),
        "channel_metadata": {
            "wa_id": "5511999999999",
            "query_policy": {
                "authorized_stores": ["JK Pecas"],
                "source_policy": {"required_tools": ["mercado_livre_orders"]},
            },
        },
    }
    config = codex_console._codex_native_mcp_thread_config(task, screen_context)
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


def test_signed_mcp_server_lists_only_read_only_catalog_and_enforces_store_scope():
    process = _start_server()
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
        assert "mercado_livre_orders" in {item["name"] for item in tools}

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
