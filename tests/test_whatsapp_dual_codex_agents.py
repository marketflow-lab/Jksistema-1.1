from __future__ import annotations

import json
import time

import pytest

from backend.services import codex_console, codex_whatsapp_agents, whatsapp_bridge
from backend.services.codex.assistant import execution as assistant_execution
from backend.services.whatsapp import black_jhon_prompting, conversation_context, marketplace_listing_delivery
from backend.services.whatsapp.orchestration import conversation as whatsapp_conversation
from backend.services.whatsapp.orchestration import function_manager as whatsapp_function_manager
from backend.services.whatsapp.orchestration import manager_results as whatsapp_manager_results
from backend.services.codex.console import agent_loop as console_agent_loop
from backend.services.codex.console import attachments as console_attachments
from backend.services.codex.console import scope as console_scope
from backend.services.codex.console import task_store as console_task_store
from backend.services.codex.console import tasks as console_tasks
from backend.services.codex.console import queueing as console_queueing


def _evidence(status: str, reason: str = "") -> dict:
    conclusive = status in {"complete", "confirmed_zero"}
    return {"schema": "jk.codex.evidence.v1", "status": status,
        "claim_scope": "full" if conclusive else "observed_only" if status == "partial" else "none",
        "coverage_complete": conclusive, "confidence": "high" if conclusive else "medium" if status == "partial" else "low",
        "freshness": "live", "retryable": status == "unavailable", "reason": reason or status,
        "missing_fields": [] if conclusive else ["decisive_evidence"], "sources": [],
        "attempted_fallbacks": [], "next_sources": []}


def _config() -> dict:
    return {
        "agent_architecture": "dual_codex",
        "conversation_agent_model": "gpt-5.6-luna",
        "conversation_agent_reasoning": "low",
        "task_agent_model": "gpt-5.6-sol",
        "task_agent_reasoning": "low",
        "conversation_agent_speed": "fast",
        "conversation_agent_service_tier": "priority",
        "task_agent_speed": "fast",
        "task_agent_service_tier": "priority",
        "conversation_interval_seconds": 30,
        "progress_messages_enabled": False,
        "progress_explain_wait": False,
        "worker_url": "https://worker.example",
        "bridge_token": "token",
        "machine_id": "machine-1",
    }


def _session() -> dict:
    return {"username": "admin", "client_id": "cliente", "permissions": {"full": True}, "is_full": True}


def _confirmed_delivery(parts: int = 1) -> dict:
    return {
        "status": "sent",
        "delivery_receipt": {
            "schema_version": "jk.whatsapp.delivery-receipt.v1",
            "state": "sent",
            "confirmed": True,
            "terminal": True,
            "parts_total": parts,
            "parts_sent": parts,
            "parts_pending": 0,
            "parts_failed": 0,
        },
    }


def test_dual_defaults_use_luna_and_sol_without_legacy_progress():
    settings = whatsapp_bridge._whatsapp_dual_agent_settings(whatsapp_bridge._default_config())
    assert settings == {
        "agent_architecture": "dual_codex",
        "response_provider_policy": "codex_only",
        "conversation_agent_model": "gpt-5.6-luna",
        "conversation_agent_reasoning": "low",
        "task_agent_model": "gpt-5.6-sol",
        "task_agent_reasoning": "low",
        "conversation_agent_speed": "fast",
        "conversation_agent_service_tier": "priority",
        "task_agent_speed": "fast",
        "task_agent_service_tier": "priority",
        "conversation_interval_seconds": 30,
        "wait_message_after_seconds": 15,
        "wait_message_repeat_seconds": 30,
        "partial_delivery_debounce_seconds": 2,
        "deadline_enabled": False,
        "job_deadline_seconds": 0,
        "retry_policy": "bounded",
        "max_retry_attempts": 3,
        "wait_message_steady_seconds": 60,
        "max_subtasks_per_job": 6,
        "progress_messages_enabled": False,
        "max_active_task_agents_per_conversation": 6,
        "conversation_worker_count": 4,
        "conversation_runtime_pool_size": 4,
        "max_active_task_agents_global": 12,
        "preserve_order_per_phone": True,
        "data_selection_enabled": True,
        "data_selection_required_before_sol": True,
        "data_selection_worker_count": 4,
        "data_selection_runtime_pool_size": 4,
        "function_manager_enabled": True,
        "function_manager_required_before_sol": True,
        "context_hub_enabled": True,
        "function_manager_worker_count": 4,
        "function_manager_runtime_pool_size": 4,
        "function_manager_legacy_fields_ignored": True,
    }
    assert whatsapp_bridge._start_progress_pulse(_config(), "wamid-1", "task-1") is False


def test_version_five_dual_config_is_migrated_to_parallel_capacity(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_json_read",
        lambda *_args, **_kwargs: {
            "version": 5,
            "agent_architecture": "dual_codex",
            "max_active_task_agents_per_conversation": 1,
            "max_active_task_agents_global": 2,
        },
    )
    config = whatsapp_bridge._load_config()
    assert config["version"] == 12
    assert config["response_provider_policy"] == "codex_only"
    assert config["task_agent_reasoning"] == "low"
    assert config["deadline_enabled"] is False
    assert config["job_deadline_seconds"] == 0
    assert config["retry_policy"] == "bounded"
    assert config["max_active_task_agents_per_conversation"] == 6
    assert config["max_active_task_agents_global"] == 12


def test_conversation_reply_does_not_create_worker(monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "reply",
            "reply_text": "Bom dia! Como posso ajudar?",
            "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("worker must not start")))

    handled = whatsapp_bridge._process_dual_codex_message(
        _config(),
        {},
        {"message_id": "wamid-1", "text_body": "Bom dia"},
        session=_session(),
        conversation_id="wa-conversation",
        message_id="wamid-1",
        subject="subject-1",
        phone="5511999999999",
        request_text="Bom dia",
        media=None,
        transcription=None,
        phone_ai_behavior="",
    )
    assert handled is True
    assert sent == [{"status": "completed", "response": "Bom dia! Como posso ajudar?"}]


def test_delegate_queues_function_manager_before_any_sol_worker(monkeypatch):
    sent = []
    saved = []
    state = {
        "dual_agent_conversations": {
            "wa-conversation": {
                "resolved_context": {
                    "store_mode": "single",
                    "store": "JK Peças",
                    "sku": "200",
                    "revision": 1,
                    "updated_at_epoch": time.time(),
                    "expires_at_epoch": time.time() + 3600,
                }
            }
        }
    }
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "delegate",
            "reply_text": "Vou confirmar isso para você. Enquanto isso, pode continuar falando comigo.",
                "job_title": "Consultar estoque",
                "job_prompt": "Consulte o estoque do SKU 200 na JK Peças.",
                "thread_id": "thread-luna",
                "resolved_context": {
                    "store_mode": "single", "store": "JK Peças", "sku": "200",
                    "mlb": "", "period": "", "applied_fields": ["store", "store_mode", "sku"],
                },
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", lambda *_args, **_kwargs: {"task_id": "task-sol", "status": "queued"})
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, dict(pending))))
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_remember_query_context", lambda *_args: None)
    submitted = []
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: submitted.append(True) or True)

    whatsapp_bridge._process_dual_codex_message(
        _config(),
        state,
        {"message_id": "wamid-2", "text_body": "Veja o estoque"},
        session=_session(),
        conversation_id="wa-conversation",
        message_id="wamid-2",
        subject="subject-1",
        phone="5511999999999",
        request_text="Veja o estoque",
        media=None,
        transcription=None,
        phone_ai_behavior="",
    )
    assert saved[0][1]["kind"] == "dual_function_manager"
    assert saved[0][1]["task_id"] == ""
    assert saved[0][1]["job_state"] == "manager_queued"
    assert saved[0][1]["conversation_anchors"]["resolved_context"]["store"] == "JK Peças"
    assert saved[0][1]["conversation_anchors"]["resolved_context"]["sku"] == "200"
    assert submitted == [True]
    assert sent[0]["response"].startswith("Vou confirmar")
    assert sent[0]["task_id"] == ""


def test_delegate_can_create_independent_sol_siblings_in_one_job_group(monkeypatch):
    created = []
    saved = []
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "delegate",
            "reply_text": "Vou consultar as fontes em paralelo.",
            "job_title": "Pesquisa tecnica",
            "job_prompt": "Confirme a informacao.",
            "thread_id": "thread-luna",
            "subtasks": [
                {"title": "Fabricante", "prompt": "Consulte o fabricante.", "requires_web": True, "reasoning_effort": "medium"},
                {"title": "Cadastro", "prompt": "Consulte o cadastro.", "requires_web": False, "reasoning_effort": "low"},
            ],
        },
    )

    def create(*_args, **kwargs):
        created.append(kwargs)
        return {"task_id": f"task-{len(created)}", "status": "queued"}

    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, mid, pending: saved.append((mid, dict(pending))))
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_remember_query_context", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: True)

    assert whatsapp_bridge._process_dual_codex_message(
        _config(), {}, {"message_id": "wamid-group", "text_body": "Pesquise"},
        session=_session(), conversation_id="wa-conversation", message_id="wamid-group",
        subject="subject-1", phone="5511999999999", request_text="Pesquise",
        media=None, transcription=None, phone_ai_behavior="",
    ) is True
    assert len(created) == 0
    manager_pending = saved[-1][1]
    assert manager_pending["kind"] == "dual_function_manager"
    assert len(manager_pending["sol_subtasks"]) == 2
    manager_pending["manager_plan"] = {"requires_sol": True, "requires_web": True}
    manager_pending["manager_evidence"] = {
        "status": "partial", "summary": "Cadastro coletado", "verified_facts": ["fato"],
        "sources": ["cadastro"], "confidence": "medium", "validations": [], "missing": [], "failures": [],
    }
    manager_pending["manager_tool_catalog"] = []
    assert whatsapp_bridge._function_manager_start_sol(_config(), {}, "wamid-group", manager_pending) is True
    assert len(created) == 2
    assert created[0]["job_group_id"] == created[1]["job_group_id"]
    assert {item["requires_web"] for item in created} == {True, False}
    assert saved[-1][1]["kind"] == "dual_job_group"
    assert len(saved[-1][1]["subtasks"]) == 2
    assert sent[0]["subtask_count"] == 0


def test_status_probe_never_creates_or_steers_another_worker(monkeypatch):
    sent = []
    monkeypatch.setattr(
        whatsapp_bridge,
        "_dual_active_job_snapshot",
        lambda *_args, **_kwargs: (
            "original", {"task_id": "task-active"}, {"task_id": "task-active", "status": "running"},
            {"job_id": "task-active", "status": "running"},
        ),
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "delegate", "reply_text": "Ainda estou consultando as fontes.",
            "job_prompt": "Crie outra tarefa", "job_title": "Outra", "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no worker")))
    monkeypatch.setattr(console_tasks, "steer", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no steer")))
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"})
    assert whatsapp_bridge._process_dual_codex_message(
        _config(), {}, {"message_id": "probe", "text_body": "?"},
        session=_session(), conversation_id="wa-conversation", message_id="probe",
        subject="subject-1", phone="5511999999999", request_text="?",
        media=None, transcription=None, phone_ai_behavior="",
    ) is True
    assert sent[0]["response"] == "Ainda estou consultando as fontes."


def test_worker_result_is_rewritten_by_luna_before_proactive_delivery(monkeypatch):
    raw_worker = {
        "task_id": "task-sol",
        "status": "completed",
        "final_response": json.dumps({
            "status": "completed",
            "summary": "Saldo confirmado em 4 unidades.",
            "verified_facts": ["Saldo: 4"],
            "sources": ["Bling"],
            "confidence": "high",
            "missing": [],
            "questions": [],
        }),
    }
    proactive = []
    updates = []
    removed = []
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: raw_worker)
    monkeypatch.setattr(console_tasks, "update", lambda task_id, **values: updates.append((task_id, values)))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **kwargs: {
            "action": "reply",
            "reply_text": "Confirmei 4 unidades disponíveis no Bling.",
            "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or _confirmed_delivery())
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_update_query_context_from_task", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda _state, message_id, **_kwargs: removed.append(message_id))

    completed = whatsapp_bridge._complete_dual_worker_pending(
        _config(),
        {},
        "wamid-2",
        {
            "task_id": "task-sol",
            "conversation_id": "wa-conversation",
            "subject_id": "subject-1",
            "request_text": "Veja o estoque",
            "job_title": "Consultar estoque",
        },
    )
    assert completed is True
    assert proactive[0]["text"] == "Confirmei 4 unidades disponíveis no Bling."
    assert raw_worker["final_response"] not in proactive[0]["text"]
    assert removed == ["wamid-2"]
    assert updates[-1][1]["handoff_status"] == "delivered_by_conversation_agent"


def test_job_group_buffers_partial_and_delivers_one_final_answer(monkeypatch):
    tasks = {
        "task-1": {
            "task_id": "task-1", "status": "completed",
            "final_response": json.dumps({
                "status": "completed", "summary": "Fabricante confirmou A.",
                "verified_facts": ["Fato A"], "sources": ["Fabricante"],
                "confidence": "high", "missing": [], "questions": [],
            }),
        },
        "task-2": {"task_id": "task-2", "status": "running"},
    }
    proactive = []
    removed = []
    state = {}
    monkeypatch.setattr(console_tasks, "load", lambda task_id: tasks.get(task_id))
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **kwargs: {
            "action": "reply",
            "reply_text": "Já confirmei o fato A; a outra fonte continua em consulta."
            if kwargs["event_type"] == "worker_partial"
            else "Concluí: fatos A e B confirmados.",
            "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or _confirmed_delivery())
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_update_query_context_from_task", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda _state, mid, **_kwargs: removed.append(mid))
    monkeypatch.setattr(whatsapp_bridge, "_maybe_send_dual_conversation_tick", lambda *_args, **_kwargs: False)

    pending = {
        "task_id": "task-1", "kind": "dual_job_group", "job_group_id": "group-1",
        "conversation_id": "wa-conversation", "subject_id": "subject-1", "request_text": "Pesquise",
        "subtasks": [
            {"task_id": "task-1", "title": "Fonte A"},
            {"task_id": "task-2", "title": "Fonte B"},
        ],
        "group_results": {}, "collected_task_ids": [], "delivered_task_ids": [],
        "partial_pending_since_epoch": time.time() - 3,
    }
    assert whatsapp_bridge._complete_dual_job_group_pending(_config(), state, "wamid-group", pending) is False
    assert proactive == []
    assert removed == []

    tasks["task-2"] = {
        "task_id": "task-2", "status": "completed",
        "final_response": json.dumps({
            "status": "completed", "summary": "Fonte B confirmou B.",
            "verified_facts": ["Fato B"], "sources": ["Fonte B"],
            "confidence": "high", "missing": [], "questions": [],
        }),
    }
    assert whatsapp_bridge._complete_dual_job_group_pending(_config(), state, "wamid-group", pending) is True
    assert [item["event_type"] for item in proactive] == ["task_completed"]
    assert proactive[-1]["text"] == "Concluí: fatos A e B confirmados."
    assert removed == ["wamid-group"]


def test_expired_legacy_job_deadline_is_cleared_without_canceling_tasks(monkeypatch):
    statuses = {"task-1": "running", "task-2": "queued"}
    canceled = []
    updates = []
    saved = []
    monkeypatch.setattr(console_tasks, "load", lambda task_id: {"task_id": task_id, "status": statuses[task_id]})
    monkeypatch.setattr(console_queueing, "interrupt_active_turn", lambda task_id: canceled.append(task_id) or True)
    monkeypatch.setattr(console_tasks, "update", lambda task_id, **values: updates.append((task_id, values)))
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, mid, value: saved.append((mid, dict(value))))
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda *_args, **_kwargs: {"success": True, "status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda *_args, **_kwargs: None)
    pending = {
        "task_id": "task-1", "kind": "dual_job_group", "deadline_at_epoch": time.time() - 1,
        "username": "admin", "client_id": "cliente",
        "subtasks": [{"task_id": "task-1"}, {"task_id": "task-2"}],
    }
    assert whatsapp_bridge._expire_dual_pending_if_due({}, {}, "wamid-deadline", pending) is False
    assert canceled == []
    canceled_updates = [values for _, values in updates if "status" in values]
    assert canceled_updates == []
    assert pending["deadline_enabled"] is False
    assert pending["deadline_seconds"] == 0
    assert pending["deadline_at_epoch"] == 0
    assert pending["retry_policy"] == "bounded"


def test_waiting_tick_uses_deterministic_text_at_fifteen_seconds(monkeypatch):
    task = {"task_id": "task-sol", "status": "running"}
    proactive = []
    saved = []
    monkeypatch.setattr(whatsapp_bridge, "_run_conversation_agent", lambda *_args, **_kwargs: pytest.fail("wait notices must not call AI"))
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: task)
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or {"status": "queued"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, dict(pending))))

    pending = {
        "task_id": "task-sol",
        "conversation_id": "wa-conversation",
        "subject_id": "subject-1",
        "request_text": "Faça uma pesquisa demorada",
        "created_at_epoch": time.time() - 16,
        "tick_index": 0,
    }
    assert whatsapp_bridge._maybe_send_dual_conversation_tick(_config(), {}, "wamid-3", pending, task) is True
    assert proactive[0]["event_type"] == "task_conversation"
    assert proactive[0]["text"] == "Ainda estou consultando as fontes necessárias. Assim que concluir, envio o resultado aqui."
    assert saved[0][1]["tick_index"] == 1


def test_codex_conversation_lanes_are_distinct_and_legacy_identity_is_stable():
    legacy = console_attachments._codex_canonical_conversation_id("cliente", "admin", channel="whatsapp", phone="5511999999999")
    conversation = console_attachments._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="5511999999999", lane="conversation"
    )
    worker = console_attachments._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="5511999999999", lane="worker"
    )
    assert legacy.startswith("wa_")
    assert conversation.startswith("wa_conversation_")
    assert worker.startswith("wa_worker_")
    assert len({legacy, conversation, worker}) == 3


def test_new_codex_reasoning_aliases_are_accepted_by_beta_sdk_compatibility():
    normalized = console_agent_loop._codex_sdk_response_compat({
        "supportedReasoningEfforts": [
            {"reasoningEffort": "max"},
            {"reasoningEffort": "ultra"},
        ]
    })
    assert normalized["supportedReasoningEfforts"] == [
        {"reasoningEffort": "xhigh"},
        {"reasoningEffort": "xhigh"},
    ]


def test_worker_result_fallback_never_invents_verified_facts():
    result = codex_whatsapp_agents.normalize_worker_result(
        {"status": "failed", "final_response": "", "error": "HTTP 403", "sources": []}
    )
    assert result["status"] == "failed"
    assert result["verified_facts"] == []
    assert result["confidence"] == "low"
    assert result["missing"] == ["HTTP 403"]


def test_retry_backoff_is_bounded_and_authentication_is_not_retried():
    assert [whatsapp_bridge._dual_retry_delay_seconds(i, "HTTP 429", "job-a") for i in range(1, 4)] == [2, 5, 15]
    assert whatsapp_bridge._dual_retry_classification("HTTP 429") == ("rate_limited", True)
    assert whatsapp_bridge._dual_retry_classification("HTTP 403 token expirado") == ("authentication", False)


def test_completed_result_requires_evidence_and_full_coverage_for_absence():
    task = {"task_id": "task", "status": "completed"}
    empty = {
        "status": "completed", "summary": "Consulta encerrada.", "verified_facts": [], "sources": [],
        "confidence": "unknown", "evidence_sufficient": False, "coverage_complete": False,
        "missing": [], "questions": [],
    }
    assert whatsapp_bridge._dual_worker_disposition(task, empty) == "waiting_retry"
    absence = {
        **empty,
        "summary": "Nenhum registro foi encontrado.",
        "verified_facts": ["Nenhum registro encontrado para o filtro"],
        "sources": ["API oficial"],
        "confidence": "high",
        "evidence_sufficient": True,
    }
    assert whatsapp_bridge._dual_worker_disposition(task, absence) == "waiting_retry"
    absence["coverage_complete"] = True
    assert whatsapp_bridge._dual_worker_disposition(task, absence) == "completed"


def test_non_json_sol_result_uses_confirmed_evidence_and_stops_retry():
    task = {
        "task_id": "task-confirmed",
        "status": "completed",
        "final_response": "SKU 001 confirmado como produto cadastrado.",
        "verification": {"status": "confirmed", "confirmed": True, "coverage_complete": True},
        "tool_results_summary": [
            {
                "tool_id": "product_data",
                "source_label": "cadastro do JK Sistema",
                "records": 1,
                "status": "complete", "claim_scope": "full", "evidence": _evidence("complete", "SKU localizado"),
            },
            {
                "tool_id": "fallback_optional",
                "status": "insufficient", "claim_scope": "none", "evidence": _evidence("insufficient", "sem retorno"),
            },
        ],
    }
    result = codex_whatsapp_agents.normalize_worker_result(task)
    assert result["evidence_sufficient"] is True
    assert result["confidence"] == "high"
    assert "cadastro do JK Sistema" in result["sources"]
    assert whatsapp_bridge._dual_worker_disposition(task, result) == "completed"


def test_server_preserves_agent_selected_sku_sources_without_adding_calls():
    catalog = [
        {"id": "mercado_livre_listing"},
        {"id": "product_data"},
        {"id": "product_image"},
        {"id": "mercado_livre_orders"},
    ]
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "001", "mlb": "", "order_id": "", "period": "",
                "store_ref": "Uai Mineirinho", "store_mode": "single",
            },
            "tool_calls": [
                {"tool_id": "mercado_livre_listing", "arguments": {}, "required": True, "reason": "anuncio", "depends_on": []},
                {"tool_id": "product_data", "arguments": {}, "required": False, "reason": "cadastro", "depends_on": []},
                {"tool_id": "product_image", "arguments": {}, "required": False, "reason": "imagem", "depends_on": []},
            ],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="Informacoes do SKU 001 no Mercado Livre",
        query_policy={"authorized_stores": ["Uai Mineirinho"]},
        catalog=catalog,
        max_calls=6,
    )
    ids = [item["tool_id"] for item in plan["tool_calls"]]
    assert ids == ["mercado_livre_listing", "product_data", "product_image"]
    assert plan["sku"] == "001"
    assert plan["requires_sol"] is False
    assert all(item["arguments"]["loja"] == "Uai Mineirinho" for item in plan["tool_calls"])


def test_deterministic_route_is_disabled_and_agent_mlb_scope_is_applied():
    assert not hasattr(whatsapp_bridge, "_deterministic_direct_query_plan")
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "", "mlb": "MLB123456789", "order_id": "", "period": "",
                "store_ref": "JK Pecas", "store_mode": "single",
            },
            "tool_calls": [{
                "tool_id": "mercado_livre_listing",
                "arguments": {"incluir_detalhes": True},
                "required": True,
                "reason": "anuncio selecionado",
                "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="Mande link, descricao e fotos do MLB-123456789 na JK Pecas",
        query_policy={"authorized_stores": ["JK Pecas"]},
        catalog=[{"id": "mercado_livre_listing"}, {"id": "mercado_livre_orders"}],
        max_calls=6,
    )

    assert plan["item_id"] == "MLB123456789"
    assert [item["tool_id"] for item in plan["tool_calls"]] == ["mercado_livre_listing"]
    assert plan["tool_calls"][0]["arguments"]["loja"] == "JK Pecas"
    assert plan["tool_calls"][0]["arguments"]["incluir_detalhes"] is True


def test_manager_routes_latest_sale_only_to_fresh_mercado_livre_order_api():
    catalog = [
        {"id": "mercado_livre_orders"},
        {"id": "sales_ranking"},
        {"id": "local_database_query"},
        {"id": "local_csv_query"},
    ]
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "", "mlb": "", "order_id": "", "period": "",
                "store_ref": "JK Pecas", "store_mode": "single",
            },
            "tool_calls": [
                {"tool_id": "mercado_livre_orders", "arguments": {
                    "force_refresh": True, "limite": 1, "incluir_detalhes": True,
                }, "required": True, "depends_on": []},
            ],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="Última venda\n\nLoja selecionada: JK Pecas",
        query_policy={"authorized_stores": ["JK Pecas"]},
        catalog=catalog,
        max_calls=6,
    )

    assert [item["tool_id"] for item in plan["tool_calls"]] == ["mercado_livre_orders"]
    assert plan["tool_calls"][0]["arguments"] == {
        "force_refresh": True,
        "limite": 1,
        "incluir_detalhes": True,
        "loja": "JK Pecas",
        "message": "Última venda\n\nLoja selecionada: JK Pecas",
    }


def test_manager_routes_specific_sale_id_to_direct_mercado_livre_lookup():
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "", "mlb": "", "order_id": "2000017389080442", "period": "",
                "store_ref": "JK Pecas", "store_mode": "single",
            },
            "tool_calls": [{
                "tool_id": "mercado_livre_orders",
                "arguments": {"id_pedido": "2000017389080442", "limite": 1, "incluir_detalhes": True},
                "required": True, "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="Consulte a venda 2000017389080442",
        query_policy={"authorized_stores": ["JK Pecas"]},
        catalog=[{"id": "mercado_livre_orders"}, {"id": "sales_returns_query"}],
        max_calls=6,
    )

    call = plan["tool_calls"][0]
    assert [item["tool_id"] for item in plan["tool_calls"]] == ["mercado_livre_orders"]
    assert call["arguments"]["id_pedido"] == "2000017389080442"
    assert call["arguments"]["limite"] == 1
    assert call["arguments"]["incluir_detalhes"] is True


def test_manager_does_not_treat_the_sale_number_of_latest_return_as_latest_sale():
    request = "Qual foi o número da venda e a data da última devolução na JK Pecas?"
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "", "mlb": "", "order_id": "", "period": "",
                "store_ref": "JK Pecas", "store_mode": "single",
            },
            "tool_calls": [{
                "tool_id": "mercado_livre_returns", "arguments": {}, "required": True, "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text=request,
        query_policy={
            "authorized_stores": ["JK Pecas"],
        },
        catalog=[
            {"id": "mercado_livre_orders"},
            {"id": "mercado_livre_returns"},
            {"id": "sales_ranking"},
            {"id": "returns_summary"},
        ],
        max_calls=6,
    )

    assert [item["tool_id"] for item in plan["tool_calls"]] == ["mercado_livre_returns"]


@pytest.mark.parametrize(
    "query",
    [
        "Mostre o pedido da ultima semana",
        "Qual foi o valor da venda na ultima semana?",
        "Mostre o pedido do ultimo mes",
    ],
)
def test_manager_does_not_treat_period_sales_as_latest_sale(query):
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "", "mlb": "", "order_id": "", "period": query,
                "store_ref": "JK Pecas", "store_mode": "single",
            },
            "tool_calls": [{
                "tool_id": "mercado_livre_orders", "arguments": {"limite": 20},
                "required": True, "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text=query,
        query_policy={"authorized_stores": ["JK Pecas"]},
        catalog=[
            {"id": "mercado_livre_orders"},
            {"id": "sales_returns_query"},
            {"id": "sales_summary"},
        ],
        max_calls=6,
    )

    assert all(
        not (
            item["tool_id"] == "mercado_livre_orders"
            and item.get("arguments", {}).get("limite") == 1
        )
        for item in plan["tool_calls"]
    )


def test_manager_treats_order_id_as_return_reference_when_return_is_requested():
    for query in (
        "Qual foi a ultima devolucao do pedido 2000017389080442?",
        "Mostre o reembolso do pedido 2000017389080442",
    ):
        plan = whatsapp_bridge._function_manager_enforce_plan(
            {
                "action": "collect",
                "entities": {
                    "sku": "", "mlb": "", "order_id": "2000017389080442", "period": "",
                    "store_ref": "JK Pecas", "store_mode": "single",
                },
                "tool_calls": [{
                    "tool_id": "mercado_livre_returns",
                    "arguments": {"id_pedido": "2000017389080442"},
                    "required": True, "depends_on": [],
                }],
                "context_hub": {"mode": "not_applicable"},
            },
            request_text=query,
            query_policy={
                "authorized_stores": ["JK Pecas"],
            },
            catalog=[
                {"id": "mercado_livre_orders"},
                {"id": "mercado_livre_returns"},
                {"id": "sales_ranking"},
                {"id": "returns_summary"},
            ],
            max_calls=6,
        )

        assert [item["tool_id"] for item in plan["tool_calls"]] == ["mercado_livre_returns"]


def test_manager_routes_explicit_stock_in_bling_ml_system_priority_order():
    catalog = [
        {"id": "bling_stock_balances"},
        {"id": "mercado_livre_listing"},
        {"id": "product_data"},
        {"id": "product_image"},
    ]
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "001", "mlb": "", "order_id": "", "period": "",
                "store_ref": "", "store_mode": "all",
            },
            "tool_calls": [
                {"tool_id": "bling_stock_balances", "arguments": {}, "required": True, "depends_on": []},
                {"tool_id": "mercado_livre_listing", "arguments": {}, "required": False, "depends_on": [0]},
            ],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="Quanto temos de estoque do SKU 001 em todas as lojas?",
        query_policy={
            "authorized_stores": ["JK Pecas", "Uai Mineirinho", "Carlos Jose", "Deckas"],
        },
        catalog=catalog,
        max_calls=6,
    )

    assert [item["tool_id"] for item in plan["tool_calls"]] == [
        "bling_stock_balances",
        "mercado_livre_listing",
    ]
    assert plan["tool_calls"][0]["required"] is True
    assert plan["tool_calls"][1]["depends_on"] == [0]


def test_simple_stock_query_has_no_deterministic_source_chain():
    assert not hasattr(whatsapp_bridge, "_deterministic_direct_query_plan")


def test_stock_executor_runs_only_the_agent_plan_in_call_order(monkeypatch):
    calls: list[tuple[str, str]] = []

    def execute_tool_call(*, tool_id, args, **_kwargs):
        store = str(args.get("loja") or "")
        calls.append((store, tool_id))
        if tool_id == "bling_stock_balances" and store == "Bling OK":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 1,
                "summary": [{
                    "tool_id": tool_id,
                    "loja": store,
                    "summary": {
                        "stock_scope": "bling_non_full_only",
                        "full_provider": "mercado_livre_api_only",
                        "chart_data": {
                            "stores": [store],
                            "totals": {"skus": 1, "store_available": 7},
                            "ranking": [{"sku": "001", "quantity": 7, "quantity_reliable": True}],
                            "coverage_complete": True,
                        },
                        "partial": False,
                    },
                }],
                "evidence": _evidence("complete"),
            }
        if tool_id == "bling_stock_balances":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 0,
                "summary": [],
                "evidence": _evidence("unavailable", "Bling indisponivel"),
            }
        if tool_id == "mercado_livre_listing" and store == "ML OK":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 1,
                "top_rows": [{"id": "MLB123", "seller_sku": "001", "available_quantity": 5}],
                "evidence": _evidence("complete"),
            }
        if tool_id == "mercado_livre_listing":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 0,
                "top_rows": [],
                "evidence": _evidence("confirmed_zero"),
            }
        return {
            "success": True,
            "tool_id": "stock_data",
            "records": 1,
            "summary": [{
                "tool_id": "stock_data",
                "summary": {
                    "found": True,
                    "sku": "001",
                    "saldo_loja_total": 11,
                    "saldo_full_total": 2,
                    "saldo_total": 13,
                },
            }],
            "evidence": _evidence("complete"),
        }

    monkeypatch.setattr(assistant_execution, "execute_tool_call", execute_tool_call)
    plan = {
        "tool_calls": [
            {"tool_id": "bling_stock_balances", "arguments": {}, "required": False},
            {"tool_id": "mercado_livre_listing", "arguments": {}, "required": False},
            {"tool_id": "stock_data", "arguments": {}, "required": False},
        ],
        "sku": "001",
    }
    policy = {"store_mode": "all", "stores": ["Bling OK", "ML OK", "Local only"]}
    results = whatsapp_bridge._function_manager_execute_tools(
        {"client_id": "tenant", "session_permissions": {"full": True}},
        plan,
        policy,
    )

    for store in ("Bling OK", "ML OK", "Local only"):
        assert [tool for called_store, tool in calls if called_store == store] == [
            "bling_stock_balances", "mercado_livre_listing", "stock_data",
        ]
    assert len(results) == 9


def test_query_context_can_inform_agent_but_server_uses_plan_entities(monkeypatch):
    state: dict = {}
    original = {
        "mode": "query_only",
        "domains": ["estoque"],
        "store_required": True,
        "store": "Uai Mineirinho",
        "store_mode": "single",
        "stores": [],
        "providers": ["bling"],
        "source_policy": {"required_tools": ["bling_stock_balances"]},
        "base_request": "estoque do SKU 001 na Uai Mineirinho",
        "sku": "001",
    }
    whatsapp_bridge._whatsapp_remember_query_context(state, "conversation-1", original["base_request"], original)
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_authorized_api_stores", lambda *_args: ["Uai Mineirinho"])
    followup = whatsapp_bridge._whatsapp_inherit_query_store_context(
        "e no Mercado Livre?",
        {
            "mode": "query_only", "domains": ["anuncios_ml"], "providers": ["mercado_livre"],
            "store_required": True, "store": "", "store_mode": "single", "store_matches": [],
            "source_policy": {},
        },
        state,
        "conversation-1",
        {"client_id": "tenant", "permissions": {"full": True}},
    )

    assert state["query_contexts"]["conversation-1"]["sku"] == "001"
    assert followup["sku"] == "001"
    assert followup["inherited_product_context"] is True
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "action": "collect",
            "entities": {
                "sku": "001", "mlb": "", "order_id": "", "period": "",
                "store_ref": "Uai Mineirinho", "store_mode": "single",
            },
            "tool_calls": [{
                "tool_id": "mercado_livre_listing", "arguments": {}, "required": True, "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable"},
        },
        request_text="e no Mercado Livre?",
        query_policy={"authorized_stores": ["Uai Mineirinho"]},
        catalog=[{"id": "mercado_livre_listing"}],
        max_calls=3,
    )
    assert plan["sku"] == "001"
    assert plan["tool_calls"][0]["arguments"]["sku"] == "001"


def test_followup_preposition_is_never_interpreted_as_sku() -> None:
    message = "É o mesmo SKU na UAI Mineirinho?"

    assert whatsapp_bridge._function_manager_extract_identifiers(message)[0] == ""

    state: dict = {}
    whatsapp_bridge._whatsapp_remember_query_context(
        state,
        "conversation-no-sku",
        message,
        {
            "mode": "query_only",
            "domains": ["estoque"],
            "store": "Uai Mineirinho",
            "store_mode": "single",
            "providers": ["bling"],
        },
    )
    assert state["query_contexts"]["conversation-no-sku"]["sku"] == ""


def test_agent_materialized_preposition_is_rejected_before_tool_execution() -> None:
    raw = {
        "action": "collect",
        "entities": {
            "sku": "na",
            "mlb": "",
            "order_id": "",
            "period": "",
            "store_ref": "Uai Mineirinho",
            "store_mode": "single",
        },
    }

    sanitized = whatsapp_function_manager._function_manager_sanitize_materialized_entities(raw)

    assert sanitized["entities"]["sku"] == ""


def test_manager_result_uses_only_materialized_sku() -> None:
    message = "É o mesmo SKU na UAI Mineirinho?"

    assert whatsapp_manager_results._materialized_stock_sku({"request_text": message}) == ""
    assert whatsapp_manager_results._materialized_stock_sku({
        "request_text": message,
        "manager_plan": {"sku": "001"},
    }) == "001"


def test_query_context_learns_sku_from_agent_plan_not_request_text() -> None:
    state: dict = {}
    whatsapp_bridge._whatsapp_remember_query_context(
        state,
        "conversation-agent-sku",
        "É o mesmo SKU na UAI Mineirinho?",
        {
            "mode": "query_only",
            "domains": ["estoque"],
            "store": "Uai Mineirinho",
            "store_mode": "single",
            "providers": ["bling"],
        },
    )

    whatsapp_bridge._whatsapp_update_query_context_from_task(
        state,
        {
            "conversation_id": "conversation-agent-sku",
            "manager_plan": {"entities": {"sku": "001"}, "sku": "001"},
        },
        {},
    )

    assert state["query_contexts"]["conversation-agent-sku"]["sku"] == "001"


def test_conversation_agent_receives_durable_context_even_without_thread(monkeypatch):
    captured = []
    state = {
        "dual_agent_conversations": {
            "conversation-1": {
                "conversation_id": "conversation-1",
                "thread_id": "",
                "recent_turns": [
                    {"role": "user", "text": "Consulte o SKU 001 na Uai Mineirinho"},
                    {"role": "assistant", "text": "O saldo confirmado foi 7."},
                    {"role": "user", "text": "e no Mercado Livre?"},
                ],
            }
        }
    }
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args, **_kwargs: None)

    def run(**kwargs):
        captured.append(kwargs)
        return {"action": "reply", "reply_text": "No Mercado Livre sao 5.", "thread_id": "thread-new"}

    monkeypatch.setattr(codex_whatsapp_agents.CONVERSATION_RUNTIME, "run", run)
    decision = whatsapp_bridge._run_conversation_agent(
        _config(), state, "conversation-1", event_type="user_message", user_message="e no Mercado Livre?",
    )

    assert decision["reply_text"] == "No Mercado Livre sao 5."
    assert captured[0]["conversation_context"] == [
        {"role": "user", "text": "Consulte o SKU 001 na Uai Mineirinho"},
        {"role": "assistant", "text": "O saldo confirmado foi 7."},
    ]
    assert state["dual_agent_conversations"]["conversation-1"]["thread_id"] == "thread-new"
    assert state["dual_agent_conversations"]["conversation-1"]["recent_turns"][-1]["role"] == "assistant"


def test_conversation_thread_restarts_when_prompt_contract_changes(monkeypatch):
    captured = []
    state = {
        "dual_agent_conversations": {
            "conversation-upgrade": {
                "conversation_id": "conversation-upgrade",
                "thread_id": "thread-old",
                "prompt_version": "black-jhon-whatsapp-prompts.v1",
                "prompt_hash": "old-hash",
            }
        }
    }
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args, **_kwargs: None)

    def run(**kwargs):
        captured.append(kwargs)
        return {
            "action": "reply",
            "reply_text": "Contexto retomado.",
            "thread_id": "thread-new",
            "thread_reused": False,
            "context_chars": 1200,
        }

    monkeypatch.setattr(codex_whatsapp_agents.CONVERSATION_RUNTIME, "run", run)
    whatsapp_bridge._run_conversation_agent(
        _config(), state, "conversation-upgrade",
        event_type="user_message", user_message="continue",
    )

    stored = state["dual_agent_conversations"]["conversation-upgrade"]
    assert captured[0]["thread_id"] == ""
    assert stored["thread_id"] == "thread-new"
    assert stored["thread_reset_reason"] == "prompt_contract_changed"
    v3_contract = black_jhon_prompting.prompt_contract_v3_diagnostics()
    assert stored["prompt_version"] == v3_contract["version"]
    assert stored["prompt_hash"] == v3_contract["hash"]
    assert stored["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V3
    assert stored["decision_contract_mode"] == "v3"
    assert stored["context_chars"] == 1200


def test_luna_persists_resolved_store_and_sku_for_the_next_turn(monkeypatch):
    captured = []
    state = {}
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args, **_kwargs: None)

    def run(**kwargs):
        captured.append(kwargs)
        if len(captured) == 1:
            return {
                "action": "delegate",
                "reply_text": "Vou consultar.",
                "job_title": "Estoque",
                "job_prompt": "Consulte o estoque do SKU 001 na JK Pecas.",
                "resolved_context": {
                    "store_mode": "single",
                    "store": "JK Pecas",
                    "sku": "001",
                },
                "thread_id": "thread-luna",
            }
        return {
            "action": "delegate",
            "reply_text": "Vou verificar na mesma loja.",
            "job_title": "Estoque",
            "job_prompt": "Consulte o estoque do SKU 001 na JK Pecas.",
            "resolved_context": {
                "store_mode": "single",
                "store": "JK Pecas",
                "sku": "001",
            },
            "thread_id": "thread-luna",
        }

    monkeypatch.setattr(codex_whatsapp_agents.CONVERSATION_RUNTIME, "run", run)
    whatsapp_bridge._run_conversation_agent(
        _config(), state, "conversation-context-a",
        event_type="user_message",
        user_message="Consulte o estoque do SKU 001 na JK Pecas",
        authorized_stores=["JK Pecas", "Deckas"],
    )
    decision = whatsapp_bridge._run_conversation_agent(
        _config(), state, "conversation-context-a",
        event_type="user_message",
        user_message="e na mesma loja?",
        authorized_stores=["JK Pecas", "Deckas"],
    )

    assert captured[1]["conversation_state"]["store"] == "JK Pecas"
    assert captured[1]["conversation_state"]["sku"] == "001"
    assert decision["resolved_context"]["store"] == "JK Pecas"
    assert decision["resolved_context"]["sku"] == "001"
    stored = state["dual_agent_conversations"]["conversation-context-a"]["resolved_context"]
    assert stored["revision"] == 1


def test_resolved_context_expires_rejects_store_and_clears_conflicting_identifier():
    record = {}
    first = conversation_context.apply_resolved_context(
        record,
        {"store_mode": "single", "store": "JK Pecas", "sku": "001", "mlb": "MLB123456789"},
        authorized_stores=["JK Pecas"],
        source="luna",
        now_epoch=100.0,
    )
    assert first["store"] == "JK Pecas"
    changed = conversation_context.apply_resolved_context(
        record,
        {"sku": "002"},
        authorized_stores=["JK Pecas"],
        source="luna",
        now_epoch=200.0,
    )
    assert changed["sku"] == "002"
    assert changed["mlb"] == ""
    rejected = conversation_context.apply_resolved_context(
        record,
        {"store": "Loja Invasora", "store_mode": "single"},
        authorized_stores=["JK Pecas"],
        source="luna",
        now_epoch=300.0,
    )
    assert rejected["store_mode"] == "none"
    assert rejected["store"] == ""
    expired = conversation_context.snapshot(
        record,
        now_epoch=300.0 + 30 * 24 * 60 * 60 + 1,
    )
    assert expired["confirmed_fields"] == []
    assert "resolved_context" not in record


def test_normalized_new_sku_clears_old_mlb_and_new_mlb_clears_old_sku():
    record = {}
    conversation_context.apply_resolved_context(
        record,
        {"sku": "001", "mlb": "MLB123456789"},
        source="fixture",
    )
    sku_decision = codex_whatsapp_agents.normalize_decision(
        {
            "action": "delegate", "reply_text": "Vou consultar.", "job_title": "Estoque",
            "job_prompt": "Consulte o SKU 002.", "related_job_id": "",
            "needs_user_input": False, "requires_web": False, "subtasks": [],
            "resolved_context": {
                "store": "", "store_mode": "none", "sku": "002", "mlb": "", "period": "",
                "applied_fields": ["sku"], "clear_fields": [],
            },
        },
        event_type="user_message",
    )
    updated = conversation_context.apply_resolved_context(
        record, sku_decision["resolved_context"], source="luna",
    )
    assert updated["sku"] == "002"
    assert updated["mlb"] == ""
    changed = conversation_context.apply_resolved_context(
        record,
        {"mlb": "MLB987654321", "provided_fields": ["mlb"]},
        source="luna",
    )
    assert changed["mlb"] == "MLB987654321"
    assert changed["sku"] == ""


def test_general_request_receives_no_commercial_request_context():
    record = {}
    memory = conversation_context.apply_resolved_context(
        record,
        {"store": "JK Pecas", "store_mode": "single", "sku": "001"},
        authorized_stores=["JK Pecas"], source="fixture",
    )
    request = conversation_context.request_context(
        {"store": "", "store_mode": "none", "sku": "", "mlb": "", "period": "", "applied_fields": []},
        memory,
    )
    assert request["confirmed_fields"] == []
    assert request["store_mode"] == "none"
    assert request["sku"] == ""
    assert conversation_context.snapshot(record)["store"] == "JK Pecas"


def test_interactive_store_pin_wins_only_for_the_next_round():
    proposed = {
        "store": "Deckas", "store_mode": "single", "sku": "001",
        "applied_fields": ["store", "store_mode", "sku"], "clear_fields": ["store"],
    }
    pinned = conversation_context.merge_round_pin(
        proposed,
        {"store": "JK Pecas", "store_mode": "single", "created_at_epoch": time.time()},
    )
    assert pinned["store"] == "JK Pecas"
    assert "store" not in pinned["clear_fields"]
    assert conversation_context.merge_round_pin(
        proposed, {"store": "JK Pecas", "store_mode": "single", "created_at_epoch": 1}
    )["store"] == "Deckas"


def test_user_answer_invalidates_clarify_plan_before_one_replan(monkeypatch):
    saved = []
    submitted = []
    pending = {
        "kind": "dual_function_manager", "request_text": "Qual o estoque?", "job_prompt": "Qual o estoque?",
        "data_selection_raw_plan": {"action": "clarify", "missing_user_fields": ["loja"]},
        "data_selection_gap_key": "initial", "manager_evidence": {"status": "blocked"},
        "conversation_anchors": {"recent_turns": [], "resolved_context": {}},
    }
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: saved.append(dict(_args[-1])))
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: submitted.append(True))
    resumed = whatsapp_bridge._resume_dual_pending_with_message(
        _config(), {}, "wamid-gap", pending, "JK Pecas",
        request_context={"store": "JK Pecas", "store_mode": "single", "applied_fields": ["store"]},
    )
    assert resumed == 1
    assert pending["data_selection_raw_plan"] == {}
    assert pending["data_selection_gap_key"] == ""
    assert pending["conversation_anchors"]["resolved_context"]["store"] == "JK Pecas"
    assert submitted == [True]


def test_resolved_context_is_isolated_by_conversation_and_all_stores_clears_single(monkeypatch):
    state = {}
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args, **_kwargs: None)
    whatsapp_bridge._dual_confirm_conversation_context(
        state,
        "tenant-a:user-a:phone-a",
        {"store_mode": "single", "store": "JK Pecas", "sku": "001"},
        authorized_stores=["JK Pecas", "Deckas"],
    )
    whatsapp_bridge._dual_confirm_conversation_context(
        state,
        "tenant-b:user-a:phone-a",
        {"store_mode": "single", "store": "Deckas", "sku": "900"},
        authorized_stores=["JK Pecas", "Deckas"],
    )
    whatsapp_bridge._dual_confirm_conversation_context(
        state,
        "tenant-a:user-a:phone-a",
        {"store_mode": "all", "clear_fields": ["store"]},
        authorized_stores=["JK Pecas", "Deckas"],
    )

    first = conversation_context.snapshot(
        state["dual_agent_conversations"]["tenant-a:user-a:phone-a"]
    )
    second = conversation_context.snapshot(
        state["dual_agent_conversations"]["tenant-b:user-a:phone-a"]
    )
    assert first["store_mode"] == "all"
    assert first["store"] == ""
    assert first["sku"] == "001"
    assert second["store_mode"] == "single"
    assert second["store"] == "Deckas"
    assert second["sku"] == "900"


def test_stock_wrapper_uses_numeric_bling_balance_and_never_turns_empty_data_into_zero_records():
    def raw_balance(store: str, quantity, *, token_expired: bool = False):
        ranking = [] if quantity is None else [{
            "sku": "001",
            "title": "Cebolao do Radiador",
            "quantity": quantity,
            "quantity_reliable": True,
        }]
        nested = [{
            "tool_id": "bling_stock_balances",
            "loja": store,
            "records": 0 if quantity is None else 1,
            "summary": {
                "stock_scope": "bling_non_full_only",
                "full_provider": "mercado_livre_api_only",
                "chart_data": {
                    "stores": [store],
                    "totals": {"skus": 0 if quantity is None else 1, "store_available": quantity},
                    "ranking": ranking,
                    "coverage_complete": quantity is not None,
                },
                "partial": quantity is None,
            },
        }]
        if quantity is None:
            nested.append({
                "tool_id": "stock_data",
                "loja": store,
                "records": 0,
                "summary": {
                    "found": True,
                    "sku": "001",
                    "saldo_loja_total": 320,
                    "saldo_full_total": 458,
                    "saldo_total": 778,
                },
            })
        return {
            "success": True,
            "tool_id": "bling_stock_balances",
            "tool_label": "saldos de estoque na Bling",
            "records": 0 if quantity is None else 1,
            "data": [],
            "summary": nested,
            "warnings": ["Token Bling expirado para esta loja."] if token_expired else [],
            "evidence": _evidence(
                "unavailable" if token_expired else "confirmed_zero" if quantity == 0 else "complete",
                "token expirado" if token_expired else "saldo confirmado",
            ),
        }

    uai = whatsapp_bridge._function_manager_compact_result(raw_balance("Uai Mineirinho", 57))
    carlos = whatsapp_bridge._function_manager_compact_result(raw_balance("Carlos Jose", 0))
    deckas = whatsapp_bridge._function_manager_compact_result(raw_balance("Deckas", None, token_expired=True))
    for store, item in (("Uai Mineirinho", uai), ("Carlos Jose", carlos), ("Deckas", deckas)):
        item.update({"manager_store": store, "manager_required": True})

    assert uai["evidence"]["status"] == "complete"
    assert uai["stock_balance"]["store_available"] == 57
    assert carlos["evidence"]["status"] == "confirmed_zero"
    assert carlos["stock_balance"]["store_available"] == 0
    assert deckas["evidence"]["status"] == "unavailable"
    assert deckas["error_class"] == "authentication"
    assert deckas["retryable"] is False

    plan = {
        "tool_calls": [{"tool_id": "bling_stock_balances", "required": True}],
        "sku": "001",
        "manager_guard": {"explicit_stock": True},
    }
    evidence = whatsapp_bridge._function_manager_evidence(plan, [uai, carlos, deckas])
    pending = {
        "request_text": "estoque do SKU 001 em todas as lojas",
        "manager_plan": plan,
        "manager_query_policy": {
            "store_mode": "all",
            "stores": ["Uai Mineirinho", "Carlos Jose", "Deckas"],
        },
    }
    response = whatsapp_bridge._deterministic_tool_result_text(evidence, pending)

    assert evidence["evidence_sufficient"] is False
    assert evidence["answerable"] is True
    assert "*Uai Mineirinho*" in response
    assert "*57 unidade(s)*" in response
    assert "*Carlos Jose*" in response
    assert "*0 unidade(s)*" in response
    assert "*Deckas*" in response
    assert "conexao da Bling desta loja esta expirada" in response
    assert "nao comprova o saldo desta loja" in response
    assert "Cobertura parcial" in response
    assert "0 registro(s) confirmado(s)" not in response
    assert "anuncios do Mercado Livre" not in response
    assert "778" not in response


def test_deterministic_listing_bundle_formats_api_fields_without_llm():
    result = {
        "success": True,
        "tool_id": "mercado_livre_listing",
        "tool_label": "anuncios do Mercado Livre",
        "records": 2,
        "top_rows": [
            {
                "loja": "JK Pecas",
                "id": "MLB111111111",
                "title": "Produto A",
                "status": "active",
                "seller_sku": "001",
                "currency_id": "BRL",
                "price": 49.9,
                "available_quantity": 7,
                "permalink": "https://produto.mercadolivre.com.br/MLB-111111111",
                "description": "Descricao oficial A",
                "pictures": [{"secure_url": "https://http2.mlstatic.com/A.jpg"}],
            },
            {
                "loja": "JK Pecas",
                "id": "MLB222222222",
                "title": "Produto B",
                "status": "paused",
                "seller_sku": "001",
                "currency_id": "BRL",
                "price": 59.9,
                "available_quantity": 0,
                "permalink": "https://produto.mercadolivre.com.br/MLB-222222222",
                "description": "Descricao oficial B",
                "pictures": [{"secure_url": "https://http2.mlstatic.com/B.jpg"}],
            },
        ],
        "evidence": _evidence("complete"),
        "manager_store": "JK Pecas",
        "manager_required": True,
    }
    plan = {"manager_guard": {"listing_first": True}, "tool_calls": [{"tool_id": "mercado_livre_listing"}]}
    evidence = whatsapp_bridge._function_manager_evidence(plan, [result])
    pending = {
        "request_text": "Mande links, descricoes, MLB e fotos do SKU 001 na JK Pecas",
        "manager_plan": plan,
    }

    response = whatsapp_bridge._deterministic_tool_result_text(evidence, pending)

    assert "*Anuncios do Mercado Livre — JK Pecas*" in response
    assert "MLB111111111" in response and "MLB222222222" in response
    assert "SKU: 001" in response
    assert "R$ 49,90" in response
    assert "Descricao oficial A" in response
    assert "https://produto.mercadolivre.com.br/MLB-222222222" in response
    assert "Fonte: API oficial do Mercado Livre" in response

    bundle = marketplace_listing_delivery.build_listing_bundle([result])
    mixed = "Mande a descricao do anuncio e diga se serve no Corolla"
    assert marketplace_listing_delivery.deterministic_response_requested(mixed) is False
    assert marketplace_listing_delivery.format_listing_bundle(bundle, mixed) == ""


def test_deterministic_latest_sale_response_shows_order_instead_of_aggregate_count():
    result = {
        "success": True,
        "tool_id": "mercado_livre_orders",
        "records": 1,
        "data": [{
            "order_id": "2000017389080442",
            "date_created": "2026-07-17T18:30:00.000-03:00",
            "status": "paid",
            "paid_amount": 79.9,
            "buyer_name": "Cliente Teste",
            "buyer_city": "Belo Horizonte",
            "items": [{"sku": "001", "title": "Produto de teste", "quantity": 1}],
        }],
        "evidence": _evidence("complete"),
        "manager_store": "JK Pecas",
        "manager_required": True,
        "sources": ["get_mercado_livre_orders"],
    }
    plan = {
        "tool_calls": [{"tool_id": "mercado_livre_orders", "required": True}],
        "manager_guard": {"explicit_sales": True, "sales_lookup_mode": "latest"},
    }
    evidence = whatsapp_bridge._function_manager_evidence(plan, [result])
    response = whatsapp_bridge._deterministic_tool_result_text(
        evidence,
        {"request_text": "Última venda", "manager_plan": plan},
    )

    assert "Última venda confirmada na API do Mercado Livre" in response
    assert "Pedido: 2000017389080442" in response
    assert "17/07/2026 às 18:30" in response
    assert "R$ 79,90" in response
    assert "Cliente Teste — Belo Horizonte" in response
    assert "26529" not in response
    assert "registro(s) confirmado(s)" not in response

    result["data"][0]["store"] = "Deckas"
    exact_plan = {
        "tool_calls": [{"tool_id": "mercado_livre_orders", "required": True}],
        "manager_guard": {"explicit_sales": True, "sales_lookup_mode": "exact"},
    }
    exact_response = whatsapp_bridge._deterministic_tool_result_text(
        whatsapp_bridge._function_manager_evidence(exact_plan, [result]),
        {"request_text": "Venda 2000017389080442", "manager_plan": exact_plan},
    )
    assert "*Deckas*" in exact_response
    assert "*JK Pecas*" not in exact_response


def test_listing_bundle_preserves_partial_coverage_from_standard_codex_shape():
    result = {
        "success": True,
        "tool_id": "mercado_livre_listing",
        "records": 1,
        "all_rows": [
            {
                "loja": "JK Pecas",
                "id": "MLB111111111",
                "title": "Produto A",
                "seller_sku": "001",
                "permalink": "https://produto.mercadolivre.com.br/MLB-111111111",
                "pictures": [{"secure_url": "https://http2.mlstatic.com/A.jpg"}],
            }
        ],
        "summary": [
            {
                "tool_id": "mercado_livre_listing",
                "summary": {
                    "found": True,
                    "partial_response": True,
                    "coverage_complete": False,
                    "paging": {"has_more": True},
                },
            }
        ],
        "evidence": _evidence("partial", "cobertura de lojas incompleta"),
    }

    bundle = marketplace_listing_delivery.build_listing_bundle([result])
    response = marketplace_listing_delivery.format_listing_bundle(
        bundle,
        "Mande o anuncio do SKU 001 na JK Pecas",
    )

    assert bundle["coverage_complete"] is False
    assert "Cobertura: parcial" in response


def test_internal_manager_evidence_answers_without_creating_sol(monkeypatch):
    message_id = "manager-direct"
    pending = {
        "kind": "dual_function_manager",
        "job_group_id": "job-manager",
        "job_title": "Informacoes do SKU 001",
        "request_text": "Informacoes do SKU 001 no Mercado Livre",
        "job_prompt": "Consulte o SKU 001.",
        "query_policy": {"authorized_stores": ["Uai Mineirinho"]},
        "manager_query_policy": {"authorized_stores": ["Uai Mineirinho"]},
        "manager_revision": 0,
        "session_permissions": {"full": True},
        "client_id": "cliente",
        "username": "admin",
        "conversation_id": "conversation-1",
        "subject_id": "subject-1",
    }
    state = {"pending_messages": {message_id: dict(pending)}}

    def save(_state, mid, value):
        _state.setdefault("pending_messages", {})[mid] = dict(value)

    monkeypatch.setattr(whatsapp_bridge, "_save_pending", save)
    monkeypatch.setattr(whatsapp_bridge, "_record_function_manager_diagnostic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_function_manager_catalog",
        lambda *_args: [{"id": "mercado_livre_listing"}, {"id": "product_data"}],
    )
    monkeypatch.setattr(
        codex_whatsapp_agents.DATA_SELECTION_RUNTIME,
        "plan",
        lambda **_kwargs: {
            "schema_version": "1.0", "action": "collect", "intents": ["sku_information"],
            "entities": {
                "sku": "001", "mlb": "", "order_id": "", "period": "",
                "store_ref": "Uai Mineirinho", "store_mode": "single",
            },
            "requested_fields": ["produto"],
            "tool_calls": [{
                "tool_id": "product_data", "arguments": "{}", "required": True,
                "reason": "cadastro", "depends_on": [],
            }],
            "context_hub": {"mode": "not_applicable", "query": "", "filters": {}, "top_k": 0, "snippet_max_chars": 0},
            "missing_user_fields": [], "confidence": 0.9, "reason": "consulta interna",
        },
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_function_manager_execute_tools",
        lambda *_args: [{
            "tool_id": "product_data", "manager_required": True, "records": 1,
            "data": {"sku": "001", "produto": "Cebolao Shadow Hornet"},
            "source_label": "cadastro do JK Sistema",
            "evidence": _evidence("complete", "localizado"),
        }],
    )
    delivered = []
    monkeypatch.setattr(whatsapp_bridge, "_function_manager_deliver_direct", lambda *_args: delivered.append(True) or True)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_function_manager_start_sol",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Sol nao deve ser criado")),
    )

    whatsapp_bridge._function_manager_job(_config(), state, message_id)

    final_pending = state["pending_messages"][message_id]
    assert delivered == [True]
    assert final_pending["manager_evidence"]["evidence_sufficient"] is True
    assert final_pending["data_selection_plan"]["manager_guard"]["data_selection_action"] == "collect"


def test_direct_listing_delivery_sends_official_photos_before_structured_text(monkeypatch):
    events = []
    listing_result = {
        "success": True,
        "tool_id": "mercado_livre_listing",
        "records": 1,
        "top_rows": [{
            "loja": "JK Pecas",
            "id": "MLB123456789",
            "title": "Produto oficial",
            "status": "active",
            "seller_sku": "001",
            "price": 49.9,
            "available_quantity": 3,
            "permalink": "https://produto.mercadolivre.com.br/MLB-123456789",
            "description": "Descricao oficial",
            "pictures": [{"secure_url": "https://http2.mlstatic.com/A.jpg"}],
        }],
        "evidence": _evidence("complete"),
        "manager_store": "JK Pecas",
        "manager_required": True,
    }
    plan = {
        "manager_guard": {"listing_first": True},
        "tool_calls": [{"tool_id": "mercado_livre_listing"}],
    }
    evidence = whatsapp_bridge._function_manager_evidence(plan, [listing_result])
    pending = {
        "manager_plan": plan,
        "manager_evidence": evidence,
        "request_text": "Mande link, descricao, MLB e fotos do SKU 001 na JK Pecas",
        "job_group_id": "job-listing",
        "conversation_id": "conversation-listing",
        "subject_id": "subject-listing",
        "client_id": "000002",
    }
    state = {"pending_messages": {"wamid-listing": dict(pending)}}

    def deliver_images(_config, message_id, bundle, request_text, max_images):
        events.append(("images", message_id, bundle["listings"][0]["item_id"], max_images, request_text))
        return [{"success": True, "status": "sent", "item_id": "MLB123456789"}]

    def post_text(_config, payload):
        events.append(("text", payload["text"]))
        return _confirmed_delivery()

    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_deliver_marketplace_listing_images", deliver_images)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "reply_text": (
                "Produto oficial\n\nMLB: MLB123456789\n\n"
                "Link: https://produto.mercadolivre.com.br/MLB-123456789"
            ),
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", post_text)
    monkeypatch.setattr(whatsapp_bridge, "_dual_remember_conversation_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_record_message_timing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda *_args, **_kwargs: None)

    assert whatsapp_bridge._function_manager_deliver_direct({}, state, "wamid-listing", pending) is True
    assert [item[0] for item in events] == ["images", "text"]
    assert events[0][2] == "MLB123456789"
    assert "MLB: MLB123456789" in events[1][1]
    assert "Link: https://produto.mercadolivre.com.br/MLB-123456789" in events[1][1]


def test_sol_data_request_returns_to_same_manager_job(monkeypatch):
    task = {
        "task_id": "sol-1",
        "status": "completed",
        "final_response": json.dumps({
            "status": "blocked", "summary": "Falta o codigo OEM interno.", "verified_facts": [],
            "sources": [], "confidence": "low", "evidence_sufficient": False,
            "coverage_complete": False, "missing": ["codigo OEM"], "questions": [],
            "data_requests": [{"need": "codigo OEM", "fields": ["codigo", "fabricante"], "reason": "validar aplicacao"}],
        }),
    }
    pending = {
        "kind": "dual_worker", "task_id": "sol-1", "job_group_id": "job-1",
        "conversation_id": "conversation-1", "subject_id": "subject-1", "request_text": "Serve?",
        "job_prompt": "Verifique a compatibilidade.", "prompt": "Verifique a compatibilidade.",
        "current_attempt": 1, "attempt_task_ids": ["sol-1"], "requires_web": True,
    }
    submitted = []
    monkeypatch.setattr(console_tasks, "load", lambda *_args: task)
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: submitted.append(True) or True)

    assert whatsapp_bridge._complete_dual_worker_pending(_config(), {}, "message-1", pending) is False
    assert pending["kind"] == "dual_function_manager"
    assert pending["manager_state"] == "queued"
    assert pending["manager_data_requests"][0]["need"] == "codigo OEM"
    assert pending["sol_subtasks"][0]["current_attempt"] == 2
    assert submitted == [True]


def test_incomplete_nontransient_result_finishes_as_partial(monkeypatch):
    task = {
        "task_id": "attempt-1",
        "status": "partial",
        "completed_at": "2026-07-15T10:00:00Z",
        "final_response": json.dumps(
            {
                "status": "partial",
                "summary": "A primeira fonte confirmou o codigo, mas falta cobertura completa.",
                "verified_facts": ["Codigo confirmado: ABC"],
                "sources": ["Fabricante"],
                "confidence": "medium",
                "missing": ["Segunda fonte indisponivel"],
                "questions": [],
            }
        ),
        "verification": {"status": "partial", "confirmed": False},
    }
    proactive = []
    removed = []
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: task)
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {"action": "reply", "reply_text": "Confirmei o codigo; continuo buscando a cobertura restante."},
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_maybe_send_dual_conversation_tick", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda _state, mid, **_kwargs: removed.append(mid))
    pending = {
        "task_id": "attempt-1",
        "kind": "dual_worker",
        "job_group_id": "job-1",
        "conversation_id": "conversation-1",
        "subject_id": "subject-1",
        "request_text": "Pesquise",
        "attempt_task_ids": ["attempt-1"],
        "current_attempt": 1,
    }
    assert whatsapp_bridge._complete_dual_worker_pending(_config(), {}, "message-1", pending) is True
    assert pending["job_state"] == "partial"
    assert pending["retry_policy"] == "bounded"
    assert pending["retry_count"] == 1
    assert pending["verified_facts"] == ["Codigo confirmado: ABC"]
    assert pending["next_retry_at_epoch"] == 0
    assert proactive[0]["event_type"] == "task_partial"
    assert removed == ["message-1"]


def test_failed_third_subtask_does_not_cancel_created_siblings(monkeypatch):
    created = []
    saved = []
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "delegate",
            "reply_text": "Vou pesquisar em paralelo.",
            "job_title": "Pesquisa",
            "job_prompt": "Pesquise.",
            "subtasks": [
                {"title": "A", "prompt": "Fonte A", "requires_web": True, "reasoning_effort": "xhigh"},
                {"title": "B", "prompt": "Fonte B", "requires_web": True, "reasoning_effort": "high"},
                {"title": "C", "prompt": "Fonte C", "requires_web": True, "reasoning_effort": "medium"},
            ],
        },
    )

    def create(*_args, **kwargs):
        created.append(kwargs)
        if len(created) == 3:
            raise RuntimeError("runtime temporarily unavailable")
        return {"task_id": f"task-{len(created)}", "status": "queued"}

    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, _mid, pending: saved.append(pending))
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_remember_query_context", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: True)
    whatsapp_bridge._process_dual_codex_message(
        _config(), {}, {"message_id": "message-group", "text_body": "Pesquise"},
        session=_session(), conversation_id="conversation-1", message_id="message-group",
        subject="subject-1", phone="5511999999999", request_text="Pesquise",
        media=None, transcription=None, phone_ai_behavior="",
    )
    pending = saved[-1]
    pending["manager_plan"] = {"requires_sol": True, "requires_web": True}
    pending["manager_evidence"] = {
        "status": "partial", "summary": "Dados coletados", "verified_facts": ["fato"],
        "sources": ["fonte"], "confidence": "medium", "validations": [], "missing": [], "failures": [],
    }
    pending["manager_tool_catalog"] = []
    whatsapp_bridge._function_manager_start_sol(_config(), {}, "message-group", pending)
    pending = saved[-1]
    assert [item["task_id"] for item in pending["subtasks"][:2]] == ["task-1", "task-2"]
    assert pending["subtasks"][2]["task_id"] == ""
    assert pending["subtasks"][2]["state"] == "waiting_retry"
    assert all(item["reasoning_effort"] == "low" for item in pending["subtasks"])


def test_restart_keeps_deadline_canceled_pending_terminal_partial(monkeypatch):
    task = {"task_id": "old-task", "status": "canceled", "cancel_source": "whatsapp_deadline"}
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: task)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    pending = {
        "kind": "dual_worker",
        "task_id": "old-task",
        "job_state": "canceled",
        "deadline_at_epoch": time.time() - 100,
    }
    state = {"pending_messages": {"message-old": pending}}
    assert whatsapp_bridge._recover_dual_pending_after_restart(state) == 0
    assert pending["state"] == "partial"
    assert pending["job_state"] == "partial"
    assert pending["next_retry_at_epoch"] == 0
    assert pending["deadline_enabled"] is False
    assert pending["deadline_at_epoch"] == 0


def test_restart_retries_stale_readonly_attempt_waiting_for_approval(monkeypatch):
    task = {"task_id": "stale-task", "status": "awaiting_approval", "origin": "whatsapp"}
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: task)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    pending = {
        "kind": "dual_worker",
        "task_id": "stale-task",
        "state": "waiting_result",
        "job_state": "running",
        "current_attempt": 2,
        "retry_count": 0,
    }
    state = {"pending_messages": {"message-stale": pending}}
    assert whatsapp_bridge._recover_dual_pending_after_restart(state) == 1
    assert pending["state"] == "waiting_retry"
    assert pending["job_state"] == "waiting_retry"
    assert pending["retry_reason"] == "stale_readonly_attempt"
    assert pending["next_retry_at_epoch"] == 0
    assert pending["retry_count"] == 1


def test_authentication_notice_is_generated_by_luna_only_once(monkeypatch):
    proactive = []
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "reply",
            "reply_text": "A integracao precisa ser reconectada; mantive seu pedido pendente e vou retomar automaticamente.",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args, **_kwargs: None)
    pending = {
        "kind": "dual_worker",
        "job_group_id": "job-auth",
        "conversation_id": "conversation-1",
        "subject_id": "subject-1",
        "request_text": "Consulte o Mercado Livre",
        "auth_notice_pending": True,
        "auth_notice_sent": False,
        "last_retry_reason": "HTTP 401 token expirado",
    }
    assert whatsapp_bridge._maybe_send_dual_auth_notice(_config(), {}, "message-auth", pending) is True
    assert whatsapp_bridge._maybe_send_dual_auth_notice(_config(), {}, "message-auth", pending) is False
    assert len(proactive) == 1
    assert pending["auth_notice_sent"] is True


def test_agent_query_policy_does_not_inherit_unselected_store_context():
    policy = whatsapp_bridge._dual_agent_query_policy(_config(), _session(), {})
    assert policy["store_mode"] == "none"
    assert policy["store"] == ""
    assert policy["stores"] == []


def test_sol_web_profile_is_live_search_but_remains_restricted():
    overrides = set(console_scope._codex_web_readonly_config_overrides())
    assert "tools.web_search=true" in overrides
    assert 'web_search="live"' in overrides
    assert "tools.web_search=false" not in overrides
    assert 'web_search="disabled"' not in overrides
    assert "features.shell_tool=false" in overrides
    assert "features.apps=false" in overrides
    assert "features.computer_use=false" in overrides
    assert "features.browser_use=false" in overrides


def test_only_authenticated_readonly_sol_worker_enables_live_web_search():
    task = {
        "origin": "whatsapp",
        "orchestration_profile": "whatsapp_dual_codex_worker",
        "agent_role": "task",
        "agent_lane": "worker",
        "channel_metadata": {
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "agent_role": "task",
            "agent_lane": "worker",
            "allow_web_search": True,
        },
    }
    assert console_scope._codex_dual_worker_web_search_enabled(
        task,
        read_only_channel_mode=True,
        sandbox="read_only",
    ) is True

    for changed in (
        {"origin": "app"},
        {"agent_role": "conversation"},
        {"agent_lane": "conversation"},
        {"orchestration_profile": "whatsapp_full_agent"},
    ):
        blocked = {**task, **changed}
        blocked["channel_metadata"] = {**task["channel_metadata"], **changed}
        assert console_scope._codex_dual_worker_web_search_enabled(
            blocked,
            read_only_channel_mode=True,
            sandbox="read_only",
        ) is False

    no_permission = {**task, "channel_metadata": {**task["channel_metadata"], "allow_web_search": False}}
    assert console_scope._codex_dual_worker_web_search_enabled(
        no_permission,
        read_only_channel_mode=True,
        sandbox="read_only",
    ) is False
    assert console_scope._codex_dual_worker_web_search_enabled(
        task,
        read_only_channel_mode=True,
        sandbox="workspace_write",
    ) is False


def test_waiting_tick_is_deterministic_even_when_agent_would_stay_silent(monkeypatch):
    task = {"task_id": "task-sol", "status": "running"}
    proactive = []
    saved = []
    updates = []
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {"action": "wait", "reply_text": "", "thread_id": "thread-luna"},
    )
    monkeypatch.setattr(console_tasks, "update", lambda task_id, **values: updates.append((task_id, values)))
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda *_args, **_kwargs: proactive.append(True) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, dict(pending))))

    pending = {
        "task_id": "task-sol",
        "conversation_id": "wa-conversation",
        "subject_id": "subject-1",
        "request_text": "Veja a previsão do tempo",
        "created_at_epoch": time.time() - 46,
        "wait_notice_count": 1,
        "last_wait_notice_at_epoch": time.time() - 31,
        "tick_index": 1,
    }
    assert whatsapp_bridge._maybe_send_dual_conversation_tick(_config(), {}, "wamid-silent", pending, task) is True
    assert proactive == [True]
    assert saved[0][1]["tick_index"] == 2
    assert saved[0][1]["delivery_state"] == "conversation_tick_sent"
    assert updates[0][1]["delivery_state"] == "conversation_tick_sent"


def test_active_job_routing_follows_agent_decision_without_lexical_override(monkeypatch):
    sent = []
    steered = []
    queued = []
    active_task = {"task_id": "task-active", "status": "running"}
    monkeypatch.setattr(
        whatsapp_bridge,
        "_dual_active_job_snapshot",
        lambda *_args, **_kwargs: (
            "wamid-original",
            {"task_id": "task-active", "request_text": "Previsão do tempo"},
            active_task,
            {"job_id": "task-active", "status": "running", "request_text": "Previsão do tempo"},
        ),
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_dual_conversation_record", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **_kwargs: {
            "action": "delegate",
            "reply_text": "Certo, somente a previsão de hoje.",
            "job_title": "Ajustar previsão",
            "job_prompt": "Consulte somente a previsão de hoje em Leandro Ferreira-MG.",
            "related_job_id": "",
            "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(console_tasks, "steer",
        lambda task_id, prompt, *_args, **_kwargs: steered.append((task_id, prompt)) or {"accepted": True},
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_create_dual_worker_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create a second worker")),
    )
    monkeypatch.setattr(
        whatsapp_conversation,
        "_queue_dual_function_manager",
        lambda *_args, **_kwargs: queued.append(True) or True,
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_message_result",
        lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"},
    )

    assert whatsapp_bridge._process_dual_codex_message(
        _config(),
        {},
        {"message_id": "wamid-correction", "text_body": "é só a previsão mesmo"},
        session=_session(),
        conversation_id="wa-conversation",
        message_id="wamid-correction",
        subject="subject-1",
        phone="5511999999999",
        request_text="é só a previsão mesmo",
        media=None,
        transcription=None,
        phone_ai_behavior="",
    ) is True
    assert steered == []
    assert queued == [True]
    assert sent == []


def test_wait_action_is_valid_only_for_periodic_tick():
    decision = codex_whatsapp_agents.normalize_decision(
        {
            "action": "wait",
            "reply_text": "",
            "job_title": "",
            "job_prompt": "",
            "related_job_id": "",
            "needs_user_input": False,
        },
        event_type="waiting_tick",
    )
    assert decision["action"] == "wait"
    assert decision["reply_text"] == ""


def test_ambiguous_question_is_kept_to_one_short_objective_reply():
    verbose = (
        "Posso consultar estoque, vendas, anuncios, imagens, cadastro, pedidos e relatorios. "
        "Tambem posso pesquisar na internet e gerar PDF, Excel e PNG. "
    ) * 12
    decision = codex_whatsapp_agents.normalize_decision(
        {
            "action": "request_information",
            "reply_text": verbose,
            "job_title": "",
            "job_prompt": "",
            "related_job_id": "",
            "needs_user_input": True,
        },
        event_type="user_message",
    )

    assert len(decision["reply_text"]) <= 423
    assert decision["reply_text"].endswith("...")
    prompt = codex_whatsapp_agents._decision_prompt(
        event_type="user_message",
        user_message="e aquele?",
        active_job=None,
        worker_result=None,
        conversation_context=[{"role": "user", "text": "Consulte o SKU 001"}],
        conversation_state={
            "store_mode": "single",
            "store": "JK Pecas",
            "sku": "001",
            "authorized_stores": ["JK Pecas", "Deckas"],
            "confirmed_fields": ["store", "sku"],
        },
        ai_behavior="",
        tick_index=0,
    )
    assert "faca exatamente uma pergunta curta e objetiva" in prompt
    assert "nao amplie o escopo" in prompt
    assert '\"conversation_context\":[{\"role\":\"user\",\"text\":\"Consulte o SKU 001\"}]' in prompt
    assert '\"conversation_state\":{\"store\":\"JK Pecas\",\"store_mode\":\"single\",\"sku\":\"001\"' in prompt
    assert '\"authorized_stores\":[\"JK Pecas\",\"Deckas\"]' in prompt


def test_luna_decision_normalizes_structured_conversation_context():
    decision = codex_whatsapp_agents.normalize_decision(
        {
            "action": "delegate",
            "reply_text": "Vou consultar.",
            "job_title": "Estoque",
            "job_prompt": "Consulte o estoque do SKU 001 na JK Pecas.",
            "related_job_id": "",
            "needs_user_input": False,
            "requires_web": False,
            "subtasks": [],
            "resolved_context": {
                "store_mode": "single",
                "store": "JK Pecas",
                "sku": "001",
                "mlb": "mlb-123456789",
                "period": "hoje",
                "applied_fields": ["store", "store_mode", "sku", "mlb", "period"],
                "clear_fields": [],
            },
        },
        event_type="user_message",
    )

    assert decision["resolved_context"]["store"] == "JK Pecas"
    assert decision["resolved_context"]["sku"] == "001"
    assert decision["resolved_context"]["mlb"] == "MLB123456789"
    assert decision["resolved_context"]["period"] == "hoje"
    assert set(decision["resolved_context"]["provided_fields"]) >= {"store", "sku", "mlb", "period"}


def test_luna_response_emojis_are_removed_before_whatsapp_delivery():
    decision = codex_whatsapp_agents.normalize_decision(
        {
            "action": "reply",
            "reply_text": "Tudo certo, Chefe 😄 Vou consultar agora. ✅",
            "job_title": "",
            "job_prompt": "",
            "related_job_id": "",
            "needs_user_input": False,
        },
        event_type="user_message",
    )
    assert decision["reply_text"] == "Tudo certo, Chefe Vou consultar agora."
    assert "😄" not in decision["reply_text"]
    assert "✅" not in decision["reply_text"]
