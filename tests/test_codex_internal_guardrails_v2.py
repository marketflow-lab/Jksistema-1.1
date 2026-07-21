from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.routers.codex_console import create_codex_console_router
from backend.services import codex_console, codex_mcp_rollout


def test_internal_sandbox_rejects_development_write() -> None:
    assert codex_console._codex_normalizar_sandbox("read_only") == "read_only"
    for sandbox in ("workspace_write", "full_access"):
        with pytest.raises(HTTPException) as error:
            codex_console._codex_normalizar_sandbox(sandbox)
        assert error.value.status_code == 409
        assert error.value.detail["error_code"] == "DEVELOPMENT_REQUIRES_CODEX_DESKTOP"


def test_development_intent_is_separate_from_typed_commercial_action() -> None:
    assert codex_console._codex_prompt_pede_desenvolvimento(
        "Implemente esta rota no backend e edite o arquivo Python"
    ) is True
    assert codex_console._codex_prompt_pede_desenvolvimento(
        "Pause o anuncio MLB123 na Loja Alfa"
    ) is False
    assert codex_console._codex_prompt_pede_alteracao("Pause o anuncio MLB123") is True


def test_public_task_hides_internal_context_paths_and_logs() -> None:
    public = codex_console._codex_public_task(
        {
            "task_id": "task-a",
            "status": "completed",
            "sandbox": "read_only",
            "cwd": r"C:\secret\readonly-root",
            "prompt": "Consulta",
            "final_response": "Resposta",
            "paths": [r"C:\secret\customer.txt"],
            "reference_paths": [r"C:\secret\customer.txt"],
            "logs": [{"text": "Bearer secret"}],
            "screen_context": {"email": "secret@example.com"},
            "channel_metadata": {"wa_id": "5511999999999"},
            "tool_calls": [{"tool_id": "product_data", "arguments": {"cpf": "123"}}],
            "tool_results_summary": [{"tool_id": "product_data", "status": "ok", "rows": [{"cpf": "123"}]}],
        }
    )
    assert public["execution_plane"] == "in_app_operations"
    assert public["development_write_enabled"] is False
    assert public["cwd"] == ""
    assert public["paths"] == []
    assert public["reference_paths"] == []
    assert public["logs"] == []
    assert public["screen_context"] == {}
    assert public["channel_metadata"] == {}
    assert "arguments" not in public["tool_calls"][0]
    assert "rows" not in public["tool_results_summary"][0]


def test_public_task_projects_nested_state_without_sensitive_payloads() -> None:
    canary = "CPF 123.456.789-00 token=segredo Rua das Flores, 123 secret@example.com C:\\secret\\x.json"
    public = codex_console._codex_public_task(
        {
            "task_id": "task-sensitive",
            "status": "awaiting_approval",
            "proposal": {"proposal_id": "p1", "params": {"cpf": canary}, "summary": canary},
            "action_run": {"run_id": "r1", "result": {"email": canary}, "status": "queued"},
            "verification": {"status": "pending", "raw": canary},
            "progress_events": [{"status": canary, "payload": {"token": canary}}],
            "steer_events": [{"message": canary}],
            "query_policy": {"mode": "query_only", "domains": ["vendas"], "secret": canary},
            "conversation_compaction": {"summary": canary},
            "reasoning_summary": canary,
            "live_plan": canary,
            "channel_message_id": canary,
        }
    )
    encoded = str(public)
    for forbidden in ("123.456.789-00", "segredo", "das Flores", "secret@example.com", "x.json"):
        assert forbidden not in encoded
    assert public["proposal"]["proposal_id"] == "p1"
    assert public["action_run"] == {
        "run_id": "r1",
        "action_id": "",
        "status": "queued",
        "error_code": "",
        "created_at": "",
        "completed_at": "",
        "verification": {},
    }


def test_public_task_and_summary_project_internal_dlp_fields() -> None:
    canary = (
        "CPF 123.456.789-00 token=segredo secret@example.com "
        r"C:\secret\payload.json"
    )
    task = {
        "task_id": "task-dlp-projection",
        "status": "completed",
        "thread_id": canary,
        "required_input": [
            {
                "field": canary,
                "label": canary,
                "message": canary,
                "private_payload": canary,
            }
        ],
        "verification": {
            "status": canary,
            "confirmed": False,
            "error_code": canary,
            "raw_evidence": canary,
        },
        "context_stats": {
            "prompt_chars": 42,
            "agent_mode": True,
            "conversation_id": canary,
            "raw_context": canary,
        },
        "tool_results_summary": [
            {
                "tool_id": "product_data",
                "source_label": canary,
                "warnings": [canary],
                "empty_reason": canary,
                "next_fallbacks": [canary],
            }
        ],
        "mcp_migration": {
            "target": "jk_system_mcp",
            "native_enabled": True,
            "native_active": False,
            "fallback_used": True,
            "fallback_error": canary,
            "legacy_parser_fallback": True,
        },
    }

    for projected in (
        codex_console._codex_public_task(task),
        codex_console._codex_task_summary(task),
    ):
        encoded = json.dumps(projected, ensure_ascii=False)
        assert "thread_id" not in projected
        for forbidden in (
            "123.456.789-00",
            "segredo",
            "secret@example.com",
            r"C:\secret\payload.json",
            "private_payload",
            "raw_evidence",
            "raw_context",
            "fallback_error\"",
        ):
            assert forbidden not in encoded
        assert projected["required_input"][0]["field"] == ""
        assert projected["required_input"][0]["required"] is True
        assert "[IDENTIFIER_REDACTED]" in projected["required_input"][0]["label"]
        assert "[PATH_REDACTED]" in projected["required_input"][0]["message"]
        assert projected["verification"] == {
            "status": "",
            "confirmed": False,
            "error_code": "",
        }
        assert projected["context_stats"] == {"prompt_chars": 42, "agent_mode": True}
        assert projected["mcp_migration"]["fallback_error_code"] == "MCP_RUNTIME_UNAVAILABLE"


def test_task_persistence_never_writes_raw_mcp_fallback_error(tmp_path: Path, monkeypatch) -> None:
    canary = "token=segredo secret@example.com " + r"C:\secret\mcp.json"
    task_path = tmp_path / "task.json"
    monkeypatch.setattr(codex_console, "_codex_task_path", lambda _task_id: str(task_path))

    codex_console._codex_persist_task(
        {
            "task_id": "task-mcp-fallback",
            "status": "failed",
            "prompt": "Consulta segura",
            "mcp_migration": {
                "target": "jk_system_mcp",
                "fallback_used": True,
                "fallback_error": canary,
                "legacy_parser_fallback": True,
            },
        }
    )

    persisted_text = task_path.read_text(encoding="utf-8")
    persisted = json.loads(persisted_text)
    assert canary not in persisted_text
    assert "fallback_error" not in persisted["mcp_migration"]
    assert persisted["mcp_migration"]["fallback_error_code"] == "MCP_RUNTIME_UNAVAILABLE"
    assert "thread_id" not in persisted


def test_public_shadow_status_is_content_free_and_confirms_no_external_call() -> None:
    projected = codex_console._codex_public_mcp_migration(
        {
            "target": "jk_system_mcp",
            "rollout_mode": "shadow",
            "rollout_version": 3,
            "native_enabled": False,
            "native_active": False,
            "legacy_parser_fallback": True,
            "shadow_observed": True,
            "shadow_status": "match",
            "shadow_plan_tools_count": 2,
            "shadow_legacy_tools_count": 2,
            "external_call_executed": False,
            "private_plan": {"arguments": {"cpf": "123.456.789-00"}},
        }
    )

    assert projected["rollout_mode"] == "shadow"
    assert projected["shadow_observed"] is True
    assert projected["shadow_status"] == "match"
    assert projected["shadow_plan_tools_count"] == 2
    assert projected["shadow_legacy_tools_count"] == 2
    assert projected["external_call_executed"] is False
    assert "private_plan" not in projected
    assert "123.456.789-00" not in str(projected)


def test_technical_logs_never_store_supplied_content() -> None:
    task = {"logs": []}
    codex_console._codex_log(task, "prompt token=secret@example.com arguments={'cpf':'123'}")
    assert task["logs"][0]["text"] == "Evento tecnico registrado."
    assert "secret" not in str(task["logs"])


def test_reference_roots_are_tenant_partitioned(tmp_path: Path, monkeypatch) -> None:
    shared = tmp_path / "shared"
    tenant_a = shared / "tenant-a"
    tenant_b = shared / "tenant-b"
    tenant_a.mkdir(parents=True)
    tenant_b.mkdir(parents=True)
    (tenant_a / "a.txt").write_text("a", encoding="utf-8")
    (tenant_b / "b.txt").write_text("b", encoding="utf-8")
    monkeypatch.setenv("JK_CODEX_REFERENCE_ROOTS", str(shared))
    roots = codex_console._codex_authorized_reference_roots(
        {"client_id": "tenant-a", "username": "user"}, "conversation-a"
    )
    assert tenant_a.resolve() in roots
    assert shared.resolve() not in roots
    assert tenant_b.resolve() not in roots


def test_mcp_rollout_store_is_manual_audited_and_reversible(tmp_path: Path) -> None:
    store = codex_mcp_rollout.MCPRolloutPolicyStore(tmp_path / "rollout.sqlite3")
    assert store.get()["mode"] == "off"
    pilot = store.put(
        {"enabled": True, "mode": "pilot", "allowed_tools": ["product_data", "danger"]},
        actor="admin-a",
    )
    assert pilot["mode"] == "pilot"
    assert pilot["allowed_tools"] == ["product_data"]
    disabled = store.put({"enabled": False, "mode": "off"}, actor="admin-a")
    assert disabled["mode"] == "off"
    assert disabled["version"] == 2


def test_admin_observability_and_feedback_routes_are_registered() -> None:
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in create_codex_console_router().routes}
    expected = {
        ("/api/admin/codex/telemetry/summary", ("GET",)),
        ("/api/admin/codex/telemetry/timeseries", ("GET",)),
        ("/api/admin/codex/evaluations/runs", ("GET",)),
        ("/api/admin/codex/evaluations/runs", ("POST",)),
        ("/api/admin/codex/mcp/rollout", ("GET",)),
        ("/api/admin/codex/mcp/rollout", ("PUT",)),
        ("/api/codex/tasks/{task_id}/feedback", ("POST",)),
    }
    assert expected.issubset(routes)


def test_mcp_admin_api_uses_server_metrics_instead_of_browser_observations(monkeypatch) -> None:
    captured = {}

    class FakeStore:
        def current_metrics(self):
            return {"tasks": 50, "days": 3, "shadow_decisions": 200, "protocol_errors": 0}

        def evaluate_and_rollback(self, metrics, **_kwargs):
            captured["rollback_metrics"] = dict(metrics)
            return {"rolled_back": False}

        def advance(self, _value, *, observations, **_kwargs):
            captured["advance_metrics"] = dict(observations)
            return {"enabled": True, "mode": "shadow"}

    monkeypatch.setattr(
        codex_console,
        "_codex_require_full_admin",
        lambda *_args, **_kwargs: {"client_id": "tenant-a", "username": "admin"},
    )
    monkeypatch.setattr(codex_console, "_codex_mcp_rollout_store", lambda _session: FakeStore())
    result = codex_console.codex_mcp_rollout_put(
        codex_console.CodexMCPRolloutRequest(
            mode="shadow",
            observations={"tasks": 999999, "days": 999999, "protocol_errors": 0},
        ),
        object(),
    )

    assert result["rollout"]["mode"] == "shadow"
    assert captured["rollback_metrics"]["tasks"] == 50
    assert captured["advance_metrics"]["days"] == 3


def test_admin_observability_frontend_is_content_free_and_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "configuracoes.html").read_text(encoding="utf-8")
    script = (root / "static" / "configuracoes-codex-observability.js").read_text(encoding="utf-8")
    assert 'data-tab="codex-observability"' in html
    assert "/configuracoes-codex-observability.js" in html
    for route in (
        "/api/admin/codex/telemetry/summary",
        "/api/admin/codex/telemetry/timeseries",
        "/api/admin/codex/evaluations/runs",
        "/api/admin/codex/mcp/rollout",
    ):
        assert route in script
    for filter_id in (
        "codexObsSurface",
        "codexObsModel",
        "codexObsProvider",
        "codexObsResult",
    ):
        assert f'id="{filter_id}"' in html
        assert filter_id in script
    for parameter in ("surface", "model", "provider", "status"):
        assert f"['{parameter}'," in script
    for forbidden in ("prompt", "final_response", "tool_results", "phone", "cpf", "email"):
        assert forbidden not in script.lower()


def test_admin_telemetry_api_forwards_safe_dimension_filters(monkeypatch) -> None:
    captured = {}

    class FakeTelemetry:
        def summary(self, client_id, **filters):
            captured["summary"] = (client_id, filters)
            return {"events": 0}

        def timeseries(self, client_id, **filters):
            captured["timeseries"] = (client_id, filters)
            return []

        def feedback_summary(self, _client_id):
            return {}

        def diagnostics(self):
            return {}

    monkeypatch.setattr(
        codex_console,
        "_codex_require_full_admin",
        lambda *_args, **_kwargs: {"client_id": "tenant-a", "username": "admin"},
    )
    monkeypatch.setattr(codex_console, "_codex_ai_telemetry_instance", lambda: FakeTelemetry())
    arguments = {
        "surface": "whatsapp",
        "model": "gpt-5.6-terra",
        "provider": "openai",
        "status": "completed",
    }
    codex_console.codex_telemetry_summary(object(), **arguments)
    codex_console.codex_telemetry_timeseries(object(), bucket="day", **arguments)

    assert captured["summary"][1] == {"from_at": "", "to_at": "", **arguments}
    assert captured["timeseries"][1] == {
        "from_at": "",
        "to_at": "",
        "bucket": "day",
        **arguments,
    }
