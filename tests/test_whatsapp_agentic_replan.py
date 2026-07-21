import json

import pytest

from backend.services import codex_assistant
from backend.services import codex_data_selection_agent
from backend.services import whatsapp_bridge as _whatsapp_bridge  # binds extracted component dependencies
from backend.services.whatsapp.orchestration import function_manager


def _plan(*, tool_id: str, arguments: dict, store: str = "Uai Mineirinho") -> dict:
    return {
        "schema_version": "1.0",
        "action": "collect",
        "intents": ["stock_lookup"],
        "entities": {
            "sku": "001",
            "mlb": "",
            "order_id": "",
            "period": "",
            "store_ref": store,
            "store_mode": "single",
        },
        "requested_fields": ["stock"],
        "tool_calls": [{
            "tool_id": tool_id,
            "arguments": json.dumps(arguments),
            "required": True,
            "reason": "saldo atual da loja",
            "depends_on": [],
        }],
        "context_hub": {
            "mode": "not_applicable",
            "query": "",
            "filters": {},
            "top_k": 0,
            "snippet_max_chars": 0,
        },
        "missing_user_fields": [],
        "confidence": 0.95,
        "reason": "consulta solicitada",
    }


def _catalog(*tool_ids: str) -> list[dict]:
    return [{"id": tool_id, "read_only": True, "input_schema": {}} for tool_id in tool_ids]


def test_false_preposition_is_not_accepted_as_sku():
    assert codex_assistant._assistant_extract_sku_filter("E o mesmo SKU na Uai Mineirinho?") == ""
    assert codex_assistant._assistant_extract_sku_filter(
        "E o mesmo SKU na Uai Mineirinho? Contexto anterior: SKU 001."
    ) == "001"


def test_materialized_sku_precedes_untrusted_conversation_text():
    enriched = codex_assistant._assistant_bling_message_with_refs(
        "E o mesmo SKU na Uai Mineirinho?",
        {"sku": "001", "loja": "Uai Mineirinho"},
        None,
    )

    assert enriched.index("SKU 001") < enriched.index("Pedido original")
    assert "loja Uai Mineirinho" in enriched


def test_named_store_rejects_aggregate_stock_source_and_accepts_scoped_source():
    policy = {
        "authorized_stores": ["Uai Mineirinho"],
        "context_request": "Estoque do SKU 001 na Uai Mineirinho",
    }
    with pytest.raises(RuntimeError, match="data_selection_collect_without_authorized_source"):
        function_manager._function_manager_enforce_plan(
            _plan(tool_id="stock_data", arguments={"sku": "001"}),
            request_text=policy["context_request"],
            query_policy=dict(policy),
            catalog=_catalog("stock_data", "bling_stock_balances"),
            max_calls=6,
        )

    guarded = function_manager._function_manager_enforce_plan(
        _plan(tool_id="bling_stock_balances", arguments={"sku": "001"}),
        request_text=policy["context_request"],
        query_policy=dict(policy),
        catalog=_catalog("stock_data", "bling_stock_balances"),
        max_calls=6,
    )
    assert guarded["tool_calls"][0]["tool_id"] == "bling_stock_balances"
    assert guarded["tool_calls"][0]["arguments"]["loja"] == "Uai Mineirinho"


def test_tool_executor_receives_only_materialized_agent_context(monkeypatch):
    captured = []
    monkeypatch.setattr(
        function_manager, "_record_latency", lambda *_args, **_kwargs: None, raising=False
    )

    def execute(**kwargs):
        captured.append(kwargs)
        return {
            "success": True,
            "records": 1,
            "data": [{"sku": "001", "stock": 3}],
            "tool_validation": {"dados_suficientes": True, "motivo": "ok"},
        }

    monkeypatch.setattr(codex_assistant, "codex_assistant_execute_tool_call", execute)
    results = function_manager._function_manager_execute_tools(
        {
            "client_id": "tenant-a",
            "request_text": "E o mesmo SKU na Uai Mineirinho?",
            "session_permissions": {"full": True},
            "username": "admin",
        },
        {
            "tool_calls": [{
                "tool_id": "bling_stock_balances",
                "arguments": {"sku": "001", "loja": "Uai Mineirinho"},
                "required": True,
                "reason": "saldo",
                "depends_on": [],
            }],
            "store_mode": "single",
            "store": "Uai Mineirinho",
        },
        {"store_mode": "single", "store": "Uai Mineirinho"},
        {},
    )

    assert results[0]["dados_suficientes"] is True
    assert captured[0]["materialized_context"] is True
    assert captured[0]["args"]["sku"] == "001"
    assert captured[0]["args"]["loja"] == "Uai Mineirinho"


def test_insufficient_evidence_schedules_one_different_agent_replan(monkeypatch):
    monkeypatch.setattr(
        function_manager,
        "_dual_retry_classification",
        lambda _reason: ("incomplete_result", False),
        raising=False,
    )
    monkeypatch.setattr(
        function_manager, "_dual_retry_delay_seconds", lambda *_args: 0, raising=False
    )
    monkeypatch.setattr(function_manager, "WHATSAPP_MAX_RETRY_ATTEMPTS", 3, raising=False)
    pending = {
        "job_group_id": "job-a",
        "manager_plan": {
            "tool_calls": [{
                "tool_id": "stock_data",
                "arguments": {"sku": "001", "loja": "Uai Mineirinho"},
            }],
        },
        "data_selection_raw_plan": {"action": "collect"},
        "data_selection_gap_key": "initial",
    }
    evidence = {
        "validations": [{
            "tool_id": "stock_data",
            "required": True,
            "dados_suficientes": False,
            "motivo": "fonte sem escopo de loja",
            "error_class": "incomplete_result",
        }],
    }

    assert function_manager._function_manager_schedule_evidence_replan(pending, evidence) is True
    assert pending["manager_state"] == "waiting_retry"
    assert pending["manager_evidence_replan_count"] == 1
    assert pending["manager_previously_attempted_tools"] == ["stock_data"]
    assert len(pending["manager_previously_attempted_call_signatures"]) == 1
    assert "data_selection_raw_plan" not in pending
    assert function_manager._function_manager_schedule_evidence_replan(pending, evidence) is False


def test_replan_prompt_exposes_attempted_sources_without_raw_content():
    prompt = codex_data_selection_agent.build_planner_prompt(
        request_text="E o mesmo SKU na Uai Mineirinho?",
        job_prompt="Consultar saldo",
        surface="whatsapp",
        allowed_tools=_catalog("stock_data", "bling_stock_balances"),
        authorized_stores=["Uai Mineirinho"],
        conversation_anchors={
            "resolved_context": {"store": "Uai Mineirinho", "sku": "001", "revision": 2},
        },
        previous_evidence={"validations": [{"dados_suficientes": False}]},
        data_gap={
            "attempted_tools": ["stock_data"],
            "attempted_call_signatures": ["abc123"],
        },
    )

    assert '"attempted_tools":["stock_data"]' in prompt
    assert '"attempted_call_signatures":["abc123"]' in prompt
    assert "plano materialmente diferente" in prompt
