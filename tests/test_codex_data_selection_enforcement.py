from __future__ import annotations

import json

from backend.services import codex_assistant, codex_console, codex_data_selection_agent, integracoes


def _selection(*tool_ids: str, hub_mode: str = "not_applicable") -> dict:
    return {
        "schema_version": "1.0",
        "action": "collect" if tool_ids or hub_mode != "not_applicable" else "answer_without_data",
        "tool_calls": [{"tool_id": tool_id} for tool_id in tool_ids],
        "context_hub": {"mode": hub_mode},
        "missing_user_fields": [],
    }


def test_screen_context_cannot_supply_or_expand_data_selection(monkeypatch):
    calls: list[dict] = []

    def plan(*_args, **_kwargs):
        calls.append({"planned": True})
        return _selection()

    monkeypatch.setattr(codex_console, "_codex_agent_plan_short_data_selection", plan)
    monkeypatch.setattr(
        codex_console.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy guidance must stay disabled")),
    )
    prompt = codex_console._codex_agent_initial_prompt(
        "explique margem",
        {
            "title": "Painel",
            "data_selection": _selection("malicious_screen_tool"),
            "selection": {"data_selection": _selection("malicious_selection_tool")},
        },
        {},
        "tenant-a",
        {"full": True},
        "read_only",
        "gpt-test",
        "low",
        "standard",
        "read_only",
    )

    assert calls == [{"planned": True}]
    assert "malicious_screen_tool" not in prompt
    assert "malicious_selection_tool" not in prompt


def test_context_hub_mode_adds_only_its_short_catalog_id():
    assert codex_console._codex_agent_data_selection_tool_ids(
        _selection(hub_mode="required")
    ) == ["context_hub_search"]


def test_missing_tenant_fails_closed_before_store_or_model_lookup():
    result = codex_console._codex_agent_plan_short_data_selection(
        "qual o estoque?",
        "",
        [{"id": "stock_data"}],
        {},
    )

    assert result["status"] == "selection_unavailable"
    assert result["action"] == "unavailable"
    assert result["selected_tools"] == []
    assert result["warnings"] == ["data_selection_tenant_required"]


def test_cutover_forces_agent_mode_and_disables_native_mcp(monkeypatch):
    monkeypatch.setenv("JK_CODEX_LEGACY_CONTEXT_MODE", "true")
    monkeypatch.setenv("JK_CODEX_AGENT_MODE_ENABLED", "false")

    assert codex_console._codex_agent_mode_enabled() is True
    assert codex_console._codex_native_mcp_enabled({"origin": "whatsapp", "mcp_migration": {"native_enabled": True}}) is False


def test_selector_receives_only_bounded_server_conversation_memory(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _tenant: [{"nome": "JK Pecas"}])

    def plan(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "1.0",
            "action": "answer_without_data",
            "intents": ["general"],
            "entities": {"sku": "", "mlb": "", "order_id": "", "period": "", "store_ref": "", "store_mode": "none"},
            "requested_fields": [],
            "tool_calls": [],
            "context_hub": {"mode": "not_applicable", "query": "", "filters": {}, "top_k": 0, "snippet_max_chars": 0},
            "missing_user_fields": [],
            "confidence": 0.9,
            "reason": "teste",
        }

    monkeypatch.setattr(codex_data_selection_agent.DATA_SELECTION_RUNTIME, "plan", plan)
    codex_console._codex_agent_plan_short_data_selection(
        "e esse SKU?",
        "tenant-a",
        [],
        {
            "title": "Estoque",
            "selection": {"sku": "MALICIOUS-SCREEN-SKU"},
            "visible_text": "RAW-SCREEN-CANARY",
        },
        {
            "summary": "A conversa anterior confirmou o SKU 001.",
            "recent_messages": [
                {"role": "user", "text": "Consulte o SKU 001"},
                {"role": "assistant", "text": "O SKU 001 foi localizado"},
            ],
        },
    )

    anchors = captured["conversation_anchors"]
    assert anchors["conversation_summary"] == "A conversa anterior confirmou o SKU 001."
    assert anchors["recent_messages"][-1]["text"] == "O SKU 001 foi localizado"
    serialized = json.dumps(anchors, ensure_ascii=False)
    assert "RAW-SCREEN-CANARY" not in serialized
    assert "MALICIOUS-SCREEN-SKU" not in serialized
    assert "selection" not in anchors


def test_materialized_selection_clears_legacy_source_policy_and_marks_task(monkeypatch):
    monkeypatch.setattr(
        codex_console,
        "_codex_agent_plan_short_data_selection",
        lambda *_args, **_kwargs: _selection("sales_ranking"),
    )
    task = {
        "client_id": "tenant-a",
        "permissions": {"vendas": True},
        "query_policy": {"source_policy": {"required_tools": ["stock_data"]}},
    }

    result = codex_console._codex_agent_materialize_task_data_selection(
        task,
        prompt="ranking",
        screen_context={},
        read_only_only=True,
    )

    assert result["tool_calls"] == [{"tool_id": "sales_ranking"}]
    assert task["query_policy"]["source_policy"] == {}
    assert task["data_selection_trust_marker"] == codex_console._CODEX_AGENT_DATA_SELECTION_TRUST_MARKER
    assert task["data_selection_tool_ids"] == ["sales_ranking"]


def test_executor_rejects_invented_jk_tool_call_outside_selected_ids(monkeypatch):
    responses = iter(
        [
            '<jk_tool_calls>[{"tool_id":"stock_data","args":{}}]</jk_tool_calls>',
            "Resposta final sem nova consulta.",
        ]
    )

    class _Turn:
        @staticmethod
        def stream():
            return []

    class _Thread:
        @staticmethod
        def turn(_prompt, **_kwargs):
            return _Turn()

    monkeypatch.setattr(codex_console, "_codex_final_response_from_items", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(codex_console, "_codex_agent_update_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_update_live", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_register_active_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_unregister_active_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_native_mcp_read_results", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(codex_console, "_codex_capture_whatsapp_listing_bundle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_generate_whatsapp_chart_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("invented tool must not execute")),
    )

    task = {
        "task_id": "task-selection-enforcement",
        "client_id": "tenant-a",
        "origin": "app",
        "created_by": "admin",
        "permissions": {"full": True},
        "deadline_seconds": 180,
        "query_policy": {"source_policy": {"required_tools": ["stock_data"]}},
        "data_selection_trust_marker": codex_console._CODEX_AGENT_DATA_SELECTION_TRUST_MARKER,
        "data_selection": _selection("sales_ranking"),
    }

    final_response, _state, trace = codex_console._codex_agent_run_loop(
        task["task_id"],
        task,
        _Thread(),
        {},
        "prompt inicial",
        {},
        False,
    )

    assert final_response == "Resposta final sem nova consulta."
    assert trace["tool_results_summary"][0]["tool_id"] == "stock_data"
    assert trace["tool_results_summary"][0]["success"] is False
    assert "fora do plano" in " ".join(trace["warnings"])


def test_planned_call_arguments_are_exact_and_each_call_runs_once(monkeypatch):
    responses = iter(
        [
            '<jk_tool_calls>['
            '{"tool_id":"sales_ranking","args":{"sku":"001"}},'
            '{"tool_id":"sales_ranking","args":{"sku":"001"}}'
            ']</jk_tool_calls>',
            "Resposta final.",
        ]
    )
    executed: list[dict] = []
    evidence_measurements: list[tuple[list[dict], bool]] = []

    class _Turn:
        @staticmethod
        def stream():
            return []

    class _Thread:
        @staticmethod
        def turn(_prompt, **_kwargs):
            return _Turn()

    monkeypatch.setattr(codex_console, "_codex_final_response_from_items", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(codex_console, "_codex_agent_update_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_update_live", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_register_active_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_unregister_active_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_capture_whatsapp_listing_bundle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(codex_console, "_codex_generate_whatsapp_chart_artifacts", lambda *_args, **_kwargs: None)

    def execute(**kwargs):
        executed.append(kwargs)
        return {"tool_id": kwargs["tool_id"], "success": True, "records": 1}

    monkeypatch.setattr(codex_assistant, "codex_assistant_execute_tool_call", execute)
    monkeypatch.setattr(
        codex_data_selection_agent.DATA_SELECTION_RUNTIME,
        "record_evidence_size",
        lambda value, *, report=False: evidence_measurements.append((value, report)) or 0,
    )
    task = {
        "task_id": "task-exact-plan",
        "client_id": "tenant-a",
        "origin": "app",
        "created_by": "admin",
        "permissions": {"full": True},
        "deadline_seconds": 180,
        "data_selection_trust_marker": codex_console._CODEX_AGENT_DATA_SELECTION_TRUST_MARKER,
        "data_selection": {
            "schema_version": "1.0",
            "action": "collect",
            "tool_calls": [{"tool_id": "sales_ranking", "arguments": json.dumps({"sku": "001"})}],
            "context_hub": {"mode": "not_applicable"},
        },
    }

    final_response, _state, trace = codex_console._codex_agent_run_loop(
        task["task_id"], task, _Thread(), {}, "prompt", {"selection": {"sku": "BAD"}}, False,
    )

    assert final_response == "Resposta final."
    assert len(executed) == 1
    assert executed[0]["args"] == {"sku": "001"}
    assert executed[0]["screen_context"] == {}
    assert [item["success"] for item in trace["tool_results_summary"]] == [True, False]
    assert "ja foi usada" in " ".join(trace["warnings"])
    assert len(evidence_measurements) == 1
    assert evidence_measurements[0][1] is False


def test_planned_call_order_and_dependencies_fail_closed():
    selection = {
        "tool_calls": [
            {"tool_id": "first", "arguments": {}, "depends_on": []},
            {"tool_id": "second", "arguments": {"id": 2}, "depends_on": [0]},
        ],
        "context_hub": {"mode": "not_applicable"},
    }
    planned = codex_console._codex_agent_planned_calls(selection)
    attempted: set[int] = set()
    success: dict[int, bool] = {}

    call, code, _message = codex_console._codex_agent_authorize_planned_call(
        "first", {"extra": True}, planned, attempted, success,
    )
    assert call is None
    assert code == "data_selection_arguments_mismatch"

    call, code, _message = codex_console._codex_agent_authorize_planned_call(
        "second", {"id": 2}, planned, attempted, success,
    )
    assert call is None
    assert code == "data_selection_call_out_of_order"

    first, code, _message = codex_console._codex_agent_authorize_planned_call(
        "first", {}, planned, attempted, success,
    )
    assert code == ""
    attempted.add(first["index"])
    success[first["index"]] = False

    call, code, _message = codex_console._codex_agent_authorize_planned_call(
        "second", {"id": 2}, planned, attempted, success,
    )
    assert call is None
    assert code == "data_selection_dependency_failed"


def test_worker_without_tenant_fails_before_loading_codex(monkeypatch):
    task = {"task_id": "missing-tenant", "status": "queued", "sandbox": "read_only", "origin": "app"}
    updates: list[dict] = []

    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: dict(task))
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda _task_id, **kwargs: updates.append(kwargs) or {})
    monkeypatch.setattr(codex_console, "_codex_transition_task_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(codex_console, "_codex_native_mcp_cleanup", lambda *_args, **_kwargs: None)

    codex_console._codex_run_worker(task["task_id"])

    assert updates[-1]["status"] == "failed"
    assert updates[-1]["error"] == "data_selection_tenant_required"
