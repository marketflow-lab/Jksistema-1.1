from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from backend.services import codex_console, codex_whatsapp_agents
from backend.services.whatsapp import black_jhon_prompting
from backend.services.codex.console import agent_loop as console_agent_loop
from backend.services.codex.console import runtime as console_runtime
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import paths as console_paths


def _decision_payload(**overrides):
    payload = {
        "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V2,
        "intent": "conversation_reply",
        "response_mode": "direct_reply",
        "missing_fields": [],
        "confidence": "high",
        "task": {
            "title": "",
            "prompt": "",
            "requires_web": False,
            "reasoning_effort": "low",
        },
        "action": "reply",
        "reply_text": "Tudo certo.",
        "job_title": "",
        "job_prompt": "",
        "related_job_id": "",
        "needs_user_input": False,
        "requires_web": False,
        "resolved_context": {
            "store": "",
            "store_mode": "none",
            "sku": "",
            "mlb": "",
            "period": "",
            "applied_fields": [],
            "clear_fields": [],
        },
        "subtasks": [],
    }
    payload.update(overrides)
    return payload


def test_server_gate_defaults_to_v3_and_fails_closed_to_v2(monkeypatch) -> None:
    monkeypatch.delenv("JK_BLACK_JHON_DECISION_CONTRACT", raising=False)

    active = codex_whatsapp_agents._decision_runtime_contract()
    fallback = codex_whatsapp_agents._decision_runtime_contract("v2")

    assert codex_whatsapp_agents.decision_contract_mode() == "v3"
    assert active["schema"] is codex_whatsapp_agents.CONVERSATION_DECISION_V3_SCHEMA
    assert active["prompt_contract"] == black_jhon_prompting.prompt_contract_v3_diagnostics()
    assert fallback["schema"] is codex_whatsapp_agents.CONVERSATION_DECISION_V2_SCHEMA
    assert fallback["prompt_contract"] == black_jhon_prompting.prompt_contract_diagnostics()
    assert codex_whatsapp_agents.decision_contract_mode("v2") == "v2"
    assert codex_whatsapp_agents.decision_contract_mode("valor-invalido") == "v2"


def test_runtime_v3_normalizer_enriches_v2_without_breaking_legacy_callers() -> None:
    payload = _decision_payload(
        intent="sku_stock",
        action="delegate",
        reply_text="Vou consultar.",
        job_title="Consultar estoque",
        job_prompt="Consulte o SKU 001.",
        requires_web=False,
        task={
            "title": "Consultar estoque",
            "prompt": "Consulte o SKU 001.",
            "requires_web": False,
            "reasoning_effort": "low",
        },
        resolved_context={
            "store": "JK Pecas",
            "store_mode": "single",
            "sku": "001",
            "mlb": "",
            "period": "hoje",
            "applied_fields": ["store", "sku", "period"],
            "clear_fields": [],
        },
    )

    legacy = codex_whatsapp_agents.normalize_decision(payload, event_type="user_message")
    active = codex_whatsapp_agents.normalize_decision(
        payload,
        event_type="user_message",
        contract_version="v3",
    )

    assert legacy["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert active["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert active["source_schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert active["intent_id"] == "commerce.stock"
    assert active["scope"]["tenant_source"] == "server"
    assert active["scope"]["store_refs"] == ["JK Pecas"]
    assert active["job_prompt"] == legacy["job_prompt"]


def test_native_v3_mutation_is_review_only_and_never_authorized() -> None:
    payload = _decision_payload(
        schema_version=black_jhon_prompting.CONVERSATION_DECISION_V3,
        intent_id="system.mutation_request",
        intent_path=["system", "mutation_request"],
        entities=[],
        scope={
            "tenant_source": "server",
            "store_mode": "none",
            "store_refs": [],
            "period": "",
            "subject": "alterar anuncio",
            "module": "mercado_livre",
        },
        risk={
            "operation_class": "mutation_request",
            "level": "high",
            "requires_human_review": False,
            "reason_codes": ["user_requested_write"],
        },
        ambiguities=[],
    )

    normalized = codex_whatsapp_agents.normalize_decision(payload, event_type="user_message")

    assert normalized["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert normalized["risk"]["operation_class"] == "mutation_request"
    assert normalized["risk"]["requires_human_review"] is True
    assert normalized["scope"]["tenant_source"] == "server"


def test_v3_prompt_has_one_unambiguous_contract() -> None:
    prompt = codex_whatsapp_agents._decision_prompt(
        event_type="user_message",
        user_message="Consulte o estoque do SKU 001.",
        active_job=None,
        worker_result=None,
        conversation_context=None,
        ai_behavior="",
        tick_index=0,
        contract_version="v3",
    )

    assert black_jhon_prompting.PROMPT_CONTRACT_V3_VERSION in prompt
    assert "Retorne somente ConversationDecisionV3." in prompt
    assert "Retorne somente ConversationDecisionV2." not in prompt
    assert "nunca executa ferramentas" in black_jhon_prompting.DECISION_V3_PROMPT_INSTRUCTIONS
    assert "nao possui ferramentas" in black_jhon_prompting.CONVERSATION_DEVELOPER_INSTRUCTIONS


def test_warm_runtime_uses_v3_schema_in_read_only_mode_without_tools(monkeypatch) -> None:
    calls = {}

    class FakeThread:
        id = "thread-v3"

        def run(self, prompt, **kwargs):
            calls["prompt"] = prompt
            calls["run"] = kwargs
            return SimpleNamespace(final_response=json.dumps(_decision_payload()))

    class FakeClient:
        def thread_start(self, **kwargs):
            calls["start"] = kwargs
            return FakeThread()

    monkeypatch.delenv("JK_BLACK_JHON_DECISION_CONTRACT", raising=False)
    monkeypatch.setattr(console_paths, "base_dir", lambda: Path("."))
    monkeypatch.setattr(console_execution, "normalize_speed", lambda value: value)
    monkeypatch.setattr(console_execution, "normalize_service_tier", lambda value, _speed: value)
    monkeypatch.setattr(console_execution, "approval_mode", lambda value, _fallback: value)
    monkeypatch.setattr(console_execution, "reasoning_effort", lambda value: value)
    runtime = codex_whatsapp_agents.WarmConversationRuntime()
    runtime._codex = FakeClient()
    runtime._available_models = ["gpt-5.6-terra"]

    result = runtime.run(
        thread_id="",
        model="gpt-5.6-terra",
        reasoning_effort="low",
        event_type="user_message",
        user_message="Oi",
    )

    assert calls["start"]["approval_mode"] == "read_only"
    assert calls["run"]["approval_mode"] == "read_only"
    assert "tools" not in calls["start"]
    assert "tools" not in calls["run"]
    assert calls["run"]["output_schema"] is codex_whatsapp_agents.CONVERSATION_DECISION_V3_SCHEMA
    assert black_jhon_prompting.PROMPT_CONTRACT_V3_VERSION in calls["prompt"]
    assert result["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert result["decision_contract_mode"] == "v3"
