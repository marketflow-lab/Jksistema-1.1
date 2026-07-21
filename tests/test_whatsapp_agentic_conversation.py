import json

import pytest

from backend.services import codex_whatsapp_agents
from backend.services import whatsapp_bridge  # noqa: F401 - binds extracted components
from backend.services.whatsapp import black_jhon_prompting
from backend.services.whatsapp import conversation_context
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp.orchestration import processor


def _v3_decision(**overrides):
    payload = {
        "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V3,
        "intent_id": "intent-1",
        "intent": "conversation_reply",
        "intent_kind": "conversation",
        "relation_to_active_job": "none",
        "answer_basis": "conversation_only",
        "data_requirement": "none",
        "context_operations": [],
        "response_mode": "direct_reply",
        "missing_fields": [],
        "confidence": "high",
        "task": {"title": "", "prompt": "", "requires_web": False, "reasoning_effort": "low"},
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


def test_v3_schema_is_active_and_v2_normalization_remains_compatible():
    assert codex_whatsapp_agents.DECISION_SCHEMA["properties"]["schema_version"]["enum"] == [
        black_jhon_prompting.CONVERSATION_DECISION_V3
    ]
    assert {
        "intent_kind",
        "relation_to_active_job",
        "answer_basis",
        "data_requirement",
        "context_operations",
    } <= set(codex_whatsapp_agents.DECISION_SCHEMA["required"])

    legacy = codex_whatsapp_agents.normalize_decision(
        {
            "action": "reply",
            "reply_text": "Oi.",
            "job_title": "",
            "job_prompt": "",
            "related_job_id": "",
            "needs_user_input": False,
            "requires_web": False,
            "resolved_context": {},
            "subtasks": [],
        },
        event_type="user_message",
    )
    assert legacy["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert legacy["data_requirement"] == "none"


def test_reply_cannot_claim_that_required_data_is_still_needed():
    with pytest.raises(RuntimeError, match="conversation_agent_reply_requires_data"):
        codex_whatsapp_agents.normalize_decision(
            _v3_decision(data_requirement="required"),
            event_type="user_message",
        )

    delegated = codex_whatsapp_agents.normalize_decision(
        _v3_decision(
            action="delegate",
            response_mode="task_delegation",
            intent_kind="query",
            data_requirement="required",
            reply_text="Vou consultar.",
            job_title="Consultar estoque",
            job_prompt="Consulte o estoque do SKU 001.",
            task={
                "title": "Consultar estoque",
                "prompt": "Consulte o estoque do SKU 001.",
                "requires_web": False,
                "reasoning_effort": "low",
            },
        ),
        event_type="user_message",
    )
    assert delegated["action"] == "delegate"
    assert delegated["data_requirement"] == "required"


def test_context_operations_materialize_canonical_effective_context_and_revision():
    record = {}
    first_operations = [
        {
            "field": "store",
            "operation": "set",
            "value": "JK Pecas",
            "source": "current_turn",
            "confidence": "high",
        },
        {
            "field": "sku",
            "operation": "set",
            "value": "001",
            "source": "current_turn",
            "confidence": "high",
        },
    ]
    first_memory = conversation_context.apply_context_operations(
        record,
        first_operations,
        authorized_stores=["JK Pecas", "Uai Mineirinho"],
        source="test",
        now_epoch=100,
    )
    first = conversation_context.effective_context(first_operations, first_memory)
    assert (first["store"], first["sku"], first["revision"]) == ("JK Pecas", "001", 1)

    second_operations = [
        {
            "field": "store",
            "operation": "set",
            "value": "Uai Mineirinho",
            "source": "current_turn",
            "confidence": "high",
        },
        {
            "field": "sku",
            "operation": "keep",
            "value": "",
            "source": "conversation_memory",
            "confidence": "high",
        },
    ]
    second_memory = conversation_context.apply_context_operations(
        record,
        second_operations,
        authorized_stores=["JK Pecas", "Uai Mineirinho"],
        source="test",
        now_epoch=200,
    )
    second = conversation_context.effective_context(second_operations, second_memory)
    assert (second["store"], second["sku"], second["revision"]) == ("Uai Mineirinho", "001", 2)
    assert {"store", "store_mode", "sku"} <= set(second["applied_fields"])


def test_unauthorized_store_operation_cannot_leak_previous_store():
    record = {}
    conversation_context.apply_context_operations(
        record,
        [{"field": "store", "operation": "set", "value": "JK Pecas", "source": "current_turn", "confidence": "high"}],
        authorized_stores=["JK Pecas"],
        source="test",
    )
    operations = [
        {"field": "store", "operation": "set", "value": "Loja Invasora", "source": "current_turn", "confidence": "high"}
    ]
    memory = conversation_context.apply_context_operations(
        record, operations, authorized_stores=["JK Pecas"], source="test",
    )
    effective = conversation_context.effective_context(operations, memory)
    assert effective["store_mode"] == "none"
    assert effective["store"] == ""
    assert "store" not in effective["confirmed_fields"]


def test_quoted_context_stays_structured_until_both_prompts():
    message = {
        "message_id": "wamid.current",
        "text_body": "E o mesmo SKU nessa loja?",
        "quoted_message_id": "wamid.previous",
        "quoted_text": "SKU 001 na Uai Mineirinho",
    }
    quoted = whatsapp_message.message_quoted_context(message)
    assert quoted == {
        "message_id": "wamid.previous",
        "text": "SKU 001 na Uai Mineirinho",
        "source": "whatsapp_reply",
    }

    decision_prompt = codex_whatsapp_agents._decision_prompt(
        event_type="user_message",
        user_message=message["text_body"],
        active_job=None,
        worker_result=None,
        conversation_context=None,
        conversation_state={"revision": 3},
        quoted_context=quoted,
        ai_behavior="",
        tick_index=0,
    )
    decision_context = json.loads(decision_prompt.rsplit("\n\n", 1)[1])
    assert decision_context["quoted_context"] == quoted
    assert decision_context["conversation_state"]["revision"] == 3

    worker_prompt = whatsapp_message.message_prompt(message, None, None)
    assert json.dumps({"quoted_context": quoted}, ensure_ascii=False, separators=(",", ":")) in worker_prompt


def test_processor_forwards_structured_quoted_context(monkeypatch):
    captured = {}
    quoted = {"message_id": "wamid.previous", "text": "SKU 001", "source": "whatsapp_reply"}
    inbound = {"message_id": "wamid.current", "machine_id": "machine", "text_body": "E esse?"}
    monkeypatch.setattr(
        processor,
        "_prepare_inbound_message",
        lambda *_args: {
            "handled": False,
            "message_id": "wamid.current",
            "subject": "subject",
            "session": {"client_id": "000002", "username": "user", "permissions": {}},
            "phone_ai_behavior": "",
            "phone": "5511999999999",
            "conversation_id": "conversation",
            "media": None,
            "transcription": None,
            "request_text": "E esse?",
            "quoted_context": quoted,
            "message": inbound,
        },
    )
    monkeypatch.setattr(processor, "_handle_inbound_commands", lambda *_args: False)
    monkeypatch.setattr(
        processor,
        "_apply_store_selection",
        lambda _config, _state, message, _session, _conversation_id, request_text, *_args: (
            message,
            request_text,
            False,
        ),
    )
    monkeypatch.setattr(
        processor,
        "_process_dual_codex_message",
        lambda *_args, **kwargs: captured.update(kwargs),
        raising=False,
    )

    processor._IMPLEMENTATIONS["_process_message"]({"machine_id": "machine"}, {}, inbound)

    assert captured["quoted_context"] == quoted
