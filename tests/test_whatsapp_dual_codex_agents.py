from __future__ import annotations

import json
import time

from backend.services import codex_console, codex_whatsapp_agents, whatsapp_bridge


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


def test_dual_defaults_use_luna_and_sol_without_legacy_progress():
    settings = whatsapp_bridge._whatsapp_dual_agent_settings(whatsapp_bridge._default_config())
    assert settings == {
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
        "wait_message_after_seconds": 15,
        "wait_message_repeat_seconds": 30,
        "partial_delivery_debounce_seconds": 2,
        "job_deadline_seconds": 120,
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
        "function_manager_enabled": True,
        "function_manager_required_before_sol": True,
        "function_manager_worker_count": 4,
        "function_manager_runtime_pool_size": 4,
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
    assert config["version"] == 9
    assert config["task_agent_reasoning"] == "low"
    assert config["job_deadline_seconds"] == 120
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
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_dual_delegate_query_policy", lambda *_args: {})
    monkeypatch.setattr(whatsapp_bridge, "_create_dual_worker_task", lambda *_args, **_kwargs: {"task_id": "task-sol", "status": "queued"})
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, _mid, payload: sent.append(payload) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, dict(pending))))
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_remember_query_context", lambda *_args: None)
    submitted = []
    monkeypatch.setattr(whatsapp_bridge, "_submit_function_manager_job", lambda *_args: submitted.append(True) or True)

    whatsapp_bridge._process_dual_codex_message(
        _config(),
        {},
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
    monkeypatch.setattr(whatsapp_bridge, "_dual_delegate_query_policy", lambda *_args: {})

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
    monkeypatch.setattr(codex_console, "codex_complementar_tarefa_para_sessao", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no steer")))
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
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: raw_worker)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda task_id, **values: updates.append((task_id, values)))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_run_conversation_agent",
        lambda *_args, **kwargs: {
            "action": "reply",
            "reply_text": "Confirmei 4 unidades disponíveis no Bling.",
            "thread_id": "thread-luna",
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or {"status": "queued"})
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
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda task_id: tasks.get(task_id))
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda _cfg, payload: proactive.append(payload) or {"status": "sent"})
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


def test_expired_job_deadline_cancels_running_tasks_and_delivers_partial(monkeypatch):
    statuses = {"task-1": "running", "task-2": "queued"}
    canceled = []
    updates = []
    saved = []
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda task_id: {"task_id": task_id, "status": statuses[task_id]})
    monkeypatch.setattr(codex_console, "_codex_interrupt_active_turn", lambda task_id: canceled.append(task_id) or True)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda task_id, **values: updates.append((task_id, values)))
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, mid, value: saved.append((mid, dict(value))))
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda *_args, **_kwargs: {"success": True, "status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_remove_pending", lambda *_args, **_kwargs: None)
    pending = {
        "task_id": "task-1", "kind": "dual_job_group", "deadline_at_epoch": time.time() - 1,
        "username": "admin", "client_id": "cliente",
        "subtasks": [{"task_id": "task-1"}, {"task_id": "task-2"}],
    }
    assert whatsapp_bridge._expire_dual_pending_if_due({}, {}, "wamid-deadline", pending) is True
    assert canceled == ["task-1", "task-2"]
    canceled_updates = [values for _, values in updates if "status" in values]
    assert len(canceled_updates) == 2
    assert all(values["status"] == "canceled" for values in canceled_updates)
    assert pending["job_state"] == "partial"
    assert pending["retry_policy"] == "bounded"


def test_waiting_tick_uses_deterministic_text_at_fifteen_seconds(monkeypatch):
    task = {"task_id": "task-sol", "status": "running"}
    proactive = []
    saved = []
    monkeypatch.setattr(whatsapp_bridge, "_run_conversation_agent", lambda *_args, **_kwargs: pytest.fail("wait notices must not call AI"))
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: task)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda *_args, **_kwargs: None)
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
    legacy = codex_console._codex_canonical_conversation_id("cliente", "admin", channel="whatsapp", phone="5511999999999")
    conversation = codex_console._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="5511999999999", lane="conversation"
    )
    worker = codex_console._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="5511999999999", lane="worker"
    )
    assert legacy.startswith("wa_")
    assert conversation.startswith("wa_conversation_")
    assert worker.startswith("wa_worker_")
    assert len({legacy, conversation, worker}) == 3


def test_new_codex_reasoning_aliases_are_accepted_by_beta_sdk_compatibility():
    normalized = codex_console._codex_sdk_response_compat({
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


def test_non_json_sol_result_uses_confirmed_tool_validation_and_stops_retry():
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
                "tool_validation": {"dados_suficientes": True, "motivo": "SKU localizado"},
            },
            {
                "tool_id": "fallback_optional",
                "tool_validation": {"dados_suficientes": False, "motivo": "sem retorno"},
            },
        ],
    }
    result = codex_whatsapp_agents.normalize_worker_result(task)
    assert result["evidence_sufficient"] is True
    assert result["confidence"] == "high"
    assert "cadastro do JK Sistema" in result["sources"]
    assert whatsapp_bridge._dual_worker_disposition(task, result) == "completed"


def test_manager_routes_sku_information_without_scanning_orders():
    catalog = [
        {"id": "mercado_livre_listing"},
        {"id": "product_data"},
        {"id": "product_image"},
        {"id": "mercado_livre_orders"},
    ]
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "intent": "sku_information",
            "tool_calls": [
                {"tool_id": "mercado_livre_orders", "arguments": {}, "required": True, "reason": "expansao indevida"}
            ],
            "requires_sol": True,
            "requires_web": False,
        },
        request_text="Informacoes do SKU 001 no Mercado Livre",
        query_policy={"store": "Uai Mineirinho", "store_mode": "single"},
        catalog=catalog,
        max_calls=6,
    )
    ids = [item["tool_id"] for item in plan["tool_calls"]]
    assert ids == ["mercado_livre_listing", "product_data", "product_image"]
    assert plan["sku"] == "001"
    assert plan["requires_sol"] is False
    assert all(item["arguments"]["loja"] == "Uai Mineirinho" for item in plan["tool_calls"])


def test_manager_routes_explicit_stock_in_bling_ml_system_priority_order():
    catalog = [
        {"id": "bling_stock_balances"},
        {"id": "mercado_livre_listing"},
        {"id": "product_data"},
        {"id": "product_image"},
    ]
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {"tool_calls": [], "requires_sol": False, "requires_web": False},
        request_text="Quanto temos de estoque do SKU 001 em todas as lojas?",
        query_policy={
            "store_mode": "all",
            "stores": ["JK Pecas", "Uai Mineirinho", "Carlos Jose", "Deckas"],
            "source_policy": {"required_tools": ["bling_stock_balances"], "force_refresh": True},
        },
        catalog=catalog,
        max_calls=6,
    )

    assert [item["tool_id"] for item in plan["tool_calls"]] == [
        "bling_stock_balances",
        "mercado_livre_listing",
    ]
    assert all(item["required"] is False for item in plan["tool_calls"])
    assert plan["manager_guard"]["explicit_stock"] is True
    assert plan["manager_guard"]["listing_first"] is False


def test_simple_stock_query_keeps_the_three_source_chain_in_deterministic_route(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_function_manager_catalog",
        lambda *_args: [
            {"id": "bling_stock_balances"},
            {"id": "mercado_livre_listing"},
            {"id": "stock_data"},
        ],
    )
    plan = whatsapp_bridge._deterministic_direct_query_plan(
        "estoque do SKU 001 na Uai Mineirinho",
        {
            "mode": "query_only",
            "domains": ["estoque"],
            "store": "Uai Mineirinho",
            "store_mode": "single",
            "source_policy": {"required_tools": ["bling_stock_balances"], "force_refresh": True},
        },
        {"full": True},
    )

    assert [item["tool_id"] for item in plan["tool_calls"]] == [
        "bling_stock_balances", "mercado_livre_listing", "stock_data",
    ]
    assert plan["sku"] == "001"


def test_stock_executor_stops_at_first_confirmed_priority_and_keeps_local_as_support(monkeypatch):
    from backend.services import codex_assistant

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
                "tool_validation": {"dados_suficientes": True},
            }
        if tool_id == "bling_stock_balances":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 0,
                "summary": [],
                "tool_validation": {"dados_suficientes": False, "motivo": "Bling indisponivel"},
            }
        if tool_id == "mercado_livre_listing" and store == "ML OK":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 1,
                "top_rows": [{"id": "MLB123", "seller_sku": "001", "available_quantity": 5}],
                "dados_suficientes": True,
                "coverage_complete": True,
                "tool_validation": {"dados_suficientes": True},
            }
        if tool_id == "mercado_livre_listing":
            return {
                "success": True,
                "tool_id": tool_id,
                "records": 0,
                "top_rows": [],
                "dados_suficientes": True,
                "coverage_complete": True,
                "tool_validation": {"dados_suficientes": True},
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
            "dados_suficientes": True,
            "coverage_complete": True,
            "tool_validation": {"dados_suficientes": True},
        }

    monkeypatch.setattr(codex_assistant, "codex_assistant_execute_tool_call", execute_tool_call)
    plan = {
        "tool_calls": [
            {"tool_id": "bling_stock_balances", "arguments": {}, "required": False},
            {"tool_id": "mercado_livre_listing", "arguments": {}, "required": False},
            {"tool_id": "stock_data", "arguments": {}, "required": False},
        ],
        "manager_guard": {"explicit_stock": True},
        "sku": "001",
    }
    policy = {"store_mode": "all", "stores": ["Bling OK", "ML OK", "Local only"]}
    results = whatsapp_bridge._function_manager_execute_tools(
        {"client_id": "tenant", "session_permissions": {"full": True}},
        plan,
        policy,
    )

    assert [tool for store, tool in calls if store == "Bling OK"] == ["bling_stock_balances"]
    assert [tool for store, tool in calls if store == "ML OK"] == ["bling_stock_balances", "mercado_livre_listing"]
    assert [tool for store, tool in calls if store == "Local only"] == [
        "bling_stock_balances", "mercado_livre_listing", "stock_data",
    ]
    local = next(item for item in results if item["manager_store"] == "Local only" and item["tool_id"] == "stock_data")
    assert local["stock_supporting_only"] is True
    assert local["dados_suficientes"] is False
    assert local["manager_required"] is True


def test_query_context_persists_sku_and_reuses_it_in_followup(monkeypatch):
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
        {"tool_calls": [{"tool_id": "mercado_livre_listing", "arguments": {}, "required": True}]},
        request_text="e no Mercado Livre?",
        query_policy=followup,
        catalog=[{"id": "mercado_livre_listing"}],
        max_calls=3,
    )
    assert plan["sku"] == "001"
    assert plan["tool_calls"][0]["arguments"]["sku"] == "001"


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
            "tool_validation": {
                "dados_suficientes": True,
                "motivo": "fallback generico considerou o SKU localizado",
                "warnings": ["Token Bling expirado para esta loja."] if token_expired else [],
            },
        }

    uai = whatsapp_bridge._function_manager_compact_result(raw_balance("Uai Mineirinho", 57))
    carlos = whatsapp_bridge._function_manager_compact_result(raw_balance("Carlos Jose", 0))
    deckas = whatsapp_bridge._function_manager_compact_result(raw_balance("Deckas", None, token_expired=True))
    for store, item in (("Uai Mineirinho", uai), ("Carlos Jose", carlos), ("Deckas", deckas)):
        item.update({"manager_store": store, "manager_required": True})

    assert uai["dados_suficientes"] is True
    assert uai["stock_balance"]["store_available"] == 57
    assert carlos["dados_suficientes"] is True
    assert carlos["stock_balance"]["store_available"] == 0
    assert deckas["dados_suficientes"] is False
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


def test_internal_manager_evidence_answers_without_creating_sol(monkeypatch):
    message_id = "manager-direct"
    pending = {
        "kind": "dual_function_manager",
        "job_group_id": "job-manager",
        "job_title": "Informacoes do SKU 001",
        "request_text": "Informacoes do SKU 001 no Mercado Livre",
        "job_prompt": "Consulte o SKU 001.",
        "query_policy": {"store": "Uai Mineirinho", "store_mode": "single"},
        "manager_query_policy": {"store": "Uai Mineirinho", "store_mode": "single"},
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
        codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME,
        "run_manager",
        lambda **_kwargs: {
            "intent": "sku_information", "store": "Uai Mineirinho", "store_mode": "single",
            "sku": "001", "item_id": "", "requested_fields": ["produto"],
            "tool_calls": [{"tool_id": "product_data", "arguments": {}, "required": True, "reason": "cadastro"}],
            "requires_sol": False, "requires_web": False, "missing_user_fields": [], "reason": "consulta interna",
            "thread_id": "thread-manager", "effective_model": "gpt-5.6-luna", "reasoning_effort": "low",
            "speed": "fast", "service_tier": "priority",
        },
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_function_manager_execute_tools",
        lambda *_args: [{
            "tool_id": "product_data", "manager_required": True, "records": 1,
            "data": {"sku": "001", "produto": "Cebolao Shadow Hornet"},
            "source_label": "cadastro do JK Sistema",
            "tool_validation": {"dados_suficientes": True, "motivo": "localizado"},
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
    assert final_pending["manager_effective_model"] == "gpt-5.6-luna"


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
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda *_args: task)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: task)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(whatsapp_bridge, "_dual_delegate_query_policy", lambda *_args: {})

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
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: task)
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
    assert pending["deadline_at_epoch"] < time.time()


def test_restart_retries_stale_readonly_attempt_waiting_for_approval(monkeypatch):
    task = {"task_id": "stale-task", "status": "awaiting_approval", "origin": "whatsapp"}
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: task)
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


def test_general_research_never_inherits_a_previous_store_context():
    state = {
        "query_contexts": {
            "wa-conversation": {
                "updated_at": time.time(),
                "domains": ["vendas"],
                "providers": ["mercado_livre"],
                "store_mode": "single",
                "store": "JK Pecas",
            }
        }
    }
    policy = whatsapp_bridge._dual_delegate_query_policy(
        "Agora pesquise a previsao do tempo de hoje em Leandro Ferreira-MG.",
        _session(),
        state,
        "wa-conversation",
    )
    assert policy == {}


def test_sol_web_profile_is_live_search_but_remains_restricted():
    overrides = set(codex_console._codex_web_readonly_config_overrides())
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
    assert codex_console._codex_dual_worker_web_search_enabled(
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
        assert codex_console._codex_dual_worker_web_search_enabled(
            blocked,
            read_only_channel_mode=True,
            sandbox="read_only",
        ) is False

    no_permission = {**task, "channel_metadata": {**task["channel_metadata"], "allow_web_search": False}}
    assert codex_console._codex_dual_worker_web_search_enabled(
        no_permission,
        read_only_channel_mode=True,
        sandbox="read_only",
    ) is False
    assert codex_console._codex_dual_worker_web_search_enabled(
        task,
        read_only_channel_mode=True,
        sandbox="workspace_write",
    ) is False


def test_short_scope_correction_is_a_task_complement():
    assert whatsapp_bridge._whatsapp_is_task_complement("é só a previsão de hoje") is True
    assert whatsapp_bridge._whatsapp_is_task_complement("Somente a previsão, sem informações extras") is True


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
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda task_id, **values: updates.append((task_id, values)))
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


def test_scope_correction_is_steered_to_active_sol_even_if_luna_says_delegate(monkeypatch):
    sent = []
    steered = []
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
    monkeypatch.setattr(
        codex_console,
        "codex_complementar_tarefa_para_sessao",
        lambda task_id, prompt, *_args, **_kwargs: steered.append((task_id, prompt)) or {"accepted": True},
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_create_dual_worker_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create a second worker")),
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
    assert steered == [("task-active", "Consulte somente a previsão de hoje em Leandro Ferreira-MG.")]
    assert sent[0]["task_id"] == "task-active"


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
        ai_behavior="",
        tick_index=0,
    )
    assert "faca exatamente uma pergunta curta e objetiva" in prompt
    assert "nao amplie o escopo" in prompt
    assert '\"conversation_context\":[{\"role\":\"user\",\"text\":\"Consulte o SKU 001\"}]' in prompt


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
