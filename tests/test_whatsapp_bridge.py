from __future__ import annotations

import base64
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.services import admin_usuarios_common, codex_actions, codex_assistant, codex_capabilities, codex_console, perguntas_pos_venda_codex, perguntas_pos_venda_endpoints, perguntas_pos_venda_state, whatsapp_bridge, whatsapp_transcribe


_REAL_MESSAGE_PHONE = whatsapp_bridge._message_phone


@pytest.fixture(autouse=True)
def _default_phone_for_legacy_bridge_scenarios(monkeypatch):
    """Cenarios antigos focam outras regras; mensagens reais sempre trazem wa_id."""
    monkeypatch.setattr(
        whatsapp_bridge,
        "_message_phone",
        lambda config, message: _REAL_MESSAGE_PHONE(config, message) or "5511999999999",
    )


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})


@pytest.fixture()
def codex_runtime(tmp_path, monkeypatch):
    monkeypatch.setitem(codex_console.__dict__, "BASE_DIR", str(tmp_path))
    monkeypatch.setitem(codex_console.__dict__, "PASTA_INFO", str(tmp_path / "info"))
    monkeypatch.setattr(codex_console, "_codex_enabled", lambda: True)
    monkeypatch.setattr(codex_console, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(codex_console, "_codex_start_thread", lambda _task_id: None)
    codex_console.CODEX_TASKS.clear()
    yield tmp_path
    codex_console.CODEX_TASKS.clear()


def _full_session():
    return {"username": "admin", "client_id": "cliente", "permissions": {"full": True}, "is_full": True}


def test_worker_url_requires_https_outside_localhost():
    assert whatsapp_bridge._normalize_worker_url("https://bridge.example.workers.dev/") == "https://bridge.example.workers.dev"
    assert whatsapp_bridge._normalize_worker_url("http://127.0.0.1:8787/") == "http://127.0.0.1:8787"
    with pytest.raises(HTTPException):
        whatsapp_bridge._normalize_worker_url("http://bridge.example.com")


def test_whatsapp_ai_defaults_and_validation():
    defaults = whatsapp_bridge._default_config()
    assert defaults["ai_model"] == "codex:gpt-5.5"
    assert defaults["codex_reasoning_effort"] == "xhigh"
    assert defaults["codex_reasoning_policy"] == "adaptive"
    assert defaults["codex_reasoning_max"] == "xhigh"
    assert defaults["progress_interval_seconds"] == 8
    assert defaults["agent_architecture"] == "dual_codex"
    assert defaults["conversation_agent_model"] == "gpt-5.6-luna"
    assert defaults["conversation_agent_reasoning"] == "low"
    assert defaults["task_agent_model"] == "gpt-5.6-sol"
    assert defaults["task_agent_reasoning"] == "low"
    assert defaults["conversation_interval_seconds"] == 30
    assert defaults["progress_messages_enabled"] is False

    settings = whatsapp_bridge._whatsapp_ai_settings(
        {"ai_model": "vertex:gemini-2.5-flash", "codex_reasoning_effort": "medium"}
    )
    assert settings == {
        "model": "vertex:gemini-2.5-flash",
        "provider": "vertex",
        "codex_reasoning_effort": "medium",
        "codex_reasoning_policy": "adaptive",
        "codex_reasoning_max": "medium",
    }

    with pytest.raises(HTTPException):
        whatsapp_bridge._whatsapp_ai_settings(
            {"ai_model": "codex:gpt-5.5", "codex_reasoning_effort": "ilimitada"}
        )


def test_whatsapp_adaptive_reasoning_uses_complexity_and_cap():
    settings = {
        "codex_reasoning_effort": "xhigh",
        "codex_reasoning_policy": "adaptive",
        "codex_reasoning_max": "xhigh",
    }
    assert whatsapp_bridge._whatsapp_adaptive_reasoning_level(settings, "Oi, tudo bem?", {}) == "low"
    assert whatsapp_bridge._whatsapp_adaptive_reasoning_level(settings, "Qual o estoque do SKU 10?", {}) == "medium"
    assert whatsapp_bridge._whatsapp_adaptive_reasoning_level(
        settings,
        "Faça um relatório comparando todas as lojas",
        {"store_mode": "all", "domains": ["vendas", "estoque"]},
    ) == "high"
    assert whatsapp_bridge._whatsapp_adaptive_reasoning_level(settings, "Investigue profundamente os dados conflitantes", {}) == "xhigh"
    capped = {**settings, "codex_reasoning_max": "high"}
    assert whatsapp_bridge._whatsapp_adaptive_reasoning_level(capped, "Investigue profundamente os dados conflitantes", {}) == "high"


def test_whatsapp_progress_message_explains_real_wait_without_internal_tool_names(monkeypatch):
    monkeypatch.setattr(codex_console, "_codex_task_queue_position", lambda _task: 2)
    stage, text = whatsapp_bridge._progress_message(
        {"status": "queued", "progress_events": [], "live_status": "Tarefa criada."},
        1,
        8,
    )
    assert stage == "fila"
    assert "Posição atual na fila: 2" in text
    assert "local_database_query" not in text

    stage, text = whatsapp_bridge._progress_message(
        {
            "status": "running",
            "wait_reason": "api_query",
            "live_status": "solicitando ferramenta: mercado_livre_orders",
            "progress_events": [{"stage": "consultando"}],
        },
        2,
        16,
    )
    assert stage == "consultando"
    assert "APIs necessárias" in text
    assert "mercado_livre_orders" not in text

    stage, text = whatsapp_bridge._progress_message(
        {"status": "queued", "wait_reason": "retry_backoff", "live_status": "nova tentativa"},
        3,
        24,
    )
    assert stage == "retentativa"
    assert "nova tentativa automatica" in text


def test_codex_transient_runtime_failure_is_narrow():
    assert codex_console._codex_transient_runtime_failure("Codex app-server temporarily unavailable") is True
    assert codex_console._codex_transient_runtime_failure("connection reset by peer") is True
    assert codex_console._codex_transient_runtime_failure("Acesso negado para esta loja") is False


def test_whatsapp_complement_classifier_is_conservative():
    assert whatsapp_bridge._whatsapp_is_task_complement("Na verdade use a loja JK Peças") is True
    assert whatsapp_bridge._whatsapp_is_task_complement("Inclua também as devoluções") is True
    assert whatsapp_bridge._whatsapp_is_task_complement("Qual foi a última venda da loja Deckas?") is False


def test_codex_queued_task_accepts_idempotent_whatsapp_complement(codex_runtime):
    session = _full_session()
    created = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Consulte a venda", request_id="wamid.base"),
        session,
        origin="whatsapp",
        channel_metadata={
            "message_id": "wamid.base",
            "subject_id": "subject-1",
            "wa_id": "5511999999999",
            "admin_configured_ai": True,
        },
    )
    task_id = created["task"]["task_id"]
    result = codex_console.codex_complementar_tarefa_para_sessao(
        task_id,
        "Na verdade use a loja JK Peças",
        session,
        request_id="wamid.followup",
        subject_id="subject-1",
        wa_id="+55 (11) 99999-9999",
    )
    assert result["accepted"] is True
    assert result["mode"] == "queued_prompt"
    stored = codex_console._codex_load_task(task_id)
    assert "Na verdade use a loja JK Peças" in stored["prompt"]
    replay = codex_console.codex_complementar_tarefa_para_sessao(
        task_id,
        "Na verdade use a loja JK Peças",
        session,
        request_id="wamid.followup",
        subject_id="subject-1",
        wa_id="5511999999999",
    )
    assert replay["idempotent_replay"] is True


def test_codex_running_task_steers_only_the_same_phone(codex_runtime):
    session = _full_session()
    created = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Consulte as vendas", request_id="wamid.running"),
        session,
        origin="whatsapp",
        channel_metadata={
            "message_id": "wamid.running",
            "subject_id": "subject-1",
            "wa_id": "5511999999999",
            "admin_configured_ai": True,
        },
    )
    task_id = created["task"]["task_id"]

    class FakeTurn:
        id = "turn-active-1"

        def __init__(self):
            self.messages = []

        def steer(self, message):
            self.messages.append(message)

    turn = FakeTurn()
    codex_console._codex_update_task(task_id, status="running", agent_state="consultando")
    with codex_console.CODEX_ACTIVE_TURNS_LOCK:
        codex_console.CODEX_ACTIVE_TURNS[task_id] = turn
    try:
        result = codex_console.codex_complementar_tarefa_para_sessao(
            task_id,
            "Inclua tambem as devolucoes",
            session,
            request_id="wamid.running-followup",
            subject_id="subject-1",
            wa_id="5511999999999",
        )
        assert result["mode"] == "turn_steer"
        assert turn.messages == ["Inclua tambem as devolucoes"]
        with pytest.raises(HTTPException, match="telefone"):
            codex_console.codex_complementar_tarefa_para_sessao(
                task_id,
                "Inclua outra loja",
                session,
                request_id="wamid.other-phone",
                subject_id="subject-1",
                wa_id="5511888888888",
            )
    finally:
        with codex_console.CODEX_ACTIVE_TURNS_LOCK:
            codex_console.CODEX_ACTIVE_TURNS.pop(task_id, None)


def test_phone_notification_defaults_preserve_questions_and_opt_in_reports():
    defaults = whatsapp_bridge._default_config()
    assert defaults["version"] == 9
    assert defaults["phone_notification_settings"] == {}
    assert whatsapp_bridge._phone_notification_settings({}, "subject-1") == {
        "subject_id": "subject-1",
        "client_id": "",
        "username": "",
        "label": "",
        "send_ml_question_suggestions": True,
        "send_weekly_report": False,
        "send_monthly_report": False,
        "ai_behavior": "",
        "allow_voice_calls": False,
        }


def test_selected_codex_ai_passes_admin_model_and_reasoning(monkeypatch):
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update(
            payload=payload,
            session=session,
            origin=origin,
            channel_metadata=channel_metadata,
        )
        return {"success": True, "task": {"task_id": "task-codex"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    result = whatsapp_bridge._create_selected_ai_task(
        {"ai_model": "codex:gpt-5.6-terra", "codex_reasoning_effort": "high"},
        prompt="Analise as vendas",
        session=_full_session(),
        conversation_id="wa-conversation",
        paths=[],
        screen_context={"page": "WhatsApp"},
        safe_read_only=True,
        mobile_full_access=True,
        channel_metadata={"message_id": "wamid.1"},
    )

    assert result["task"]["task_id"] == "task-codex"
    assert created["origin"] == "whatsapp"
    assert created["payload"].model == "gpt-5.6-terra"
    assert created["payload"].reasoning_effort == "high"
    assert created["payload"].speed == "fast"
    assert created["payload"].service_tier == "priority"
    assert created["payload"].sandbox == "read_only"
    assert created["channel_metadata"]["admin_configured_ai"] is True
    assert created["channel_metadata"]["ai_model"] == "codex:gpt-5.6-terra"
    assert created["channel_metadata"]["codex_speed"] == "fast"
    assert created["channel_metadata"]["codex_service_tier"] == "priority"
    assert created["channel_metadata"]["orchestration_profile"] == "whatsapp_full_agent"


def test_selected_codex_ai_preserves_authenticated_dual_worker_profile(monkeypatch):
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update(
            payload=payload,
            session=session,
            origin=origin,
            channel_metadata=channel_metadata,
        )
        return {"success": True, "task": {"task_id": "task-sol"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    result = whatsapp_bridge._create_selected_ai_task(
        {
            "ai_model": "codex:gpt-5.6-sol",
            "codex_reasoning_effort": "xhigh",
            "codex_reasoning_policy": "fixed",
            "codex_reasoning_max": "xhigh",
        },
        prompt="Consulte a previsao do tempo",
        session=_full_session(),
        conversation_id="wa-worker",
        paths=[],
        screen_context={"page": "WhatsApp"},
        safe_read_only=True,
        mobile_full_access=False,
        channel_metadata={
            "message_id": "wamid.weather:worker",
            "request_text": "Consulte a previsao do tempo",
            "agent_role": "task",
            "agent_lane": "worker",
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "allow_web_search": True,
        },
    )

    assert result["task"]["task_id"] == "task-sol"
    assert created["payload"].sandbox == "read_only"
    assert created["payload"].speed == "fast"
    assert created["payload"].service_tier == "priority"
    assert created["channel_metadata"]["orchestration_profile"] == "whatsapp_dual_codex_worker"
    assert created["channel_metadata"]["agent_role"] == "task"
    assert created["channel_metadata"]["agent_lane"] == "worker"
    assert created["channel_metadata"]["allow_web_search"] is True


def test_selected_non_codex_ai_uses_read_only_provider_task(monkeypatch):
    created = {}

    def fake_provider(**kwargs):
        created.update(kwargs)
        return {"success": True, "task": {"task_id": "task-provider"}}

    monkeypatch.setattr(whatsapp_bridge, "_create_provider_task", fake_provider)
    monkeypatch.setattr(
        codex_console,
        "codex_criar_tarefa_para_sessao",
        lambda *_args, **_kwargs: pytest.fail("Codex must not run for a Vertex selection"),
    )
    result = whatsapp_bridge._create_selected_ai_task(
        {"ai_model": "vertex:gemini-2.5-pro", "codex_reasoning_effort": "xhigh"},
        prompt="Consulte o estoque",
        session=_full_session(),
        conversation_id="wa-conversation",
        paths=[],
        screen_context={"page": "WhatsApp"},
        safe_read_only=False,
        mobile_full_access=True,
        channel_metadata={"message_id": "wamid.2"},
    )

    assert result["task"]["task_id"] == "task-provider"
    assert created["model"] == "vertex:gemini-2.5-pro"
    assert created["channel_metadata"]["ai_provider"] == "vertex"
    assert created["channel_metadata"]["admin_configured_ai"] is True


def test_whatsapp_admin_model_is_preserved_for_read_only_user(codex_runtime):
    session = {
        "username": "operador",
        "client_id": "cliente",
        "permissions": {"vendas": True},
        "is_full": False,
    }
    result = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Consulte as vendas sem alterar dados",
            model="gpt-5.6-terra",
            reasoning_effort="high",
            speed="fast",
            service_tier="priority",
            sandbox="full_access",
            approval_mode="full_access",
        ),
        session,
        origin="whatsapp",
        channel_metadata={"admin_configured_ai": True, "wa_id": "5511999999999"},
    )

    task = result["task"]
    assert task["model"] == "gpt-5.6-terra"
    assert task["reasoning_effort"] == "high"
    assert task["speed"] == "fast"
    assert task["service_tier"] == "priority"
    assert task["sandbox"] == "read_only"
    assert task["approval_mode"] == "read_only"


def test_daily_report_endpoint_is_disabled_even_when_forced(monkeypatch):
    monkeypatch.setattr(codex_assistant, "_assistant_require_full_admin", lambda *_args, **_kwargs: {"client_id": "cliente", "username": "admin"})
    monkeypatch.setattr(codex_assistant, "_assistant_scheduler_state", lambda _client: {"last_weekly_key": "2026-W28"})
    monkeypatch.setattr(codex_assistant, "_assistant_collect_data", lambda *_args, **_kwargs: pytest.fail("daily collection must stay disabled"))

    result = codex_assistant.codex_assistant_daily_analysis_run(
        codex_assistant.CodexAssistantRunRequest(force=True, compact=False),
        _request(),
        None,
    )

    assert result["status"] == "disabled_weekly_only"
    assert result["report"] is None
    assert result["due"] is False


def test_question_approval_button_is_scoped_and_uses_existing_approval(monkeypatch):
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
            }
        }
    }
    approval = {"id": "approval-1", "status": "pending", "loja": "JK Pecas", "resposta_sugerida": "Resposta IA"}
    calls = []
    replies = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_aprovacoes_aprovar",
        lambda req, client: calls.append((req, client)) or {"success": True, "approval": {**approval, "status": "sent"}},
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    session = {
        "client_id": "cliente",
        "username": "operador",
        "permissions": {"perguntas_pos_venda": True},
        "is_full": False,
    }
    consumed = whatsapp_bridge._handle_question_approval_command(
        {},
        state,
        {"message_id": "wamid.button", "subject_id": "subject-1", "text_body": "ppv_approve:ABCDEFGH"},
        session,
    )

    assert consumed is True
    assert len(calls) == 1
    assert calls[0][0].approval_id == "approval-1"
    assert calls[0][1] == "cliente"
    assert state["question_approval_tokens"]["ABCDEFGH"]["used"] is True
    assert "ENVIADA" in replies[-1][0]

    wrong_number = dict(state)
    wrong_number["question_approval_tokens"] = {"ABCDEFGH": {**state["question_approval_tokens"]["ABCDEFGH"], "used": False}}
    calls.clear()
    whatsapp_bridge._handle_question_approval_command(
        {},
        wrong_number,
        {"message_id": "wamid.wrong", "subject_id": "subject-2", "text_body": "ppv_approve:ABCDEFGH"},
        session,
    )
    assert calls == []


def test_question_approval_sends_the_exact_draft_bound_to_the_selected_card(monkeypatch):
    state = {
        "question_approval_tokens": {
            "OLDTOKEN": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time() - 10,
                "used": False,
                "suggested_response": "Resposta escolhida no cartao antigo.",
            },
            "NEWTOKEN": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
                "suggested_response": "Outra resposta mostrada depois.",
            },
        }
    }
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "resposta_sugerida": "Outra resposta mostrada depois.",
    }
    calls = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_aprovacoes_aprovar",
        lambda req, client: calls.append((req, client)) or {"success": True, "approval": {**approval, "status": "sent"}},
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda *_args, **_kwargs: None)

    handled = whatsapp_bridge._handle_question_approval_command(
        {},
        state,
        {"message_id": "wamid.old-card", "subject_id": "subject-1", "text_body": "ppv_approve:OLDTOKEN"},
        {"client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}},
    )

    assert handled is True
    assert len(calls) == 1
    assert calls[0][0].resposta == "Resposta escolhida no cartao antigo."
    assert state["question_approval_tokens"]["OLDTOKEN"]["used"] is True
    assert state["question_approval_tokens"]["NEWTOKEN"]["used"] is False


def test_question_suggestion_body_ends_with_sku_and_product_link():
    link = "https://produto.mercadolivre.com.br/MLB-1234567890-produto-_JM"
    body = whatsapp_bridge._question_approval_body(
        {
            "loja": "JK Pecas",
            "titulo": "Produto de teste",
            "pergunta": "Serve no meu veiculo?",
            "resposta_sugerida": "Sim, conforme as medidas descritas no anuncio.",
            "sku": "254-1",
            "item_id": "MLB1234567890",
            "permalink": link,
        }
    )

    assert len(body) <= 1024
    assert "Sugestao do Black Jhon:\nSim, conforme" in body
    assert "\n\nSKU: 254-1\nLink do produto:\n" in body
    assert body.endswith(link)


def test_question_suggestion_body_builds_product_link_for_legacy_approval():
    body = whatsapp_bridge._question_approval_body(
        {
            "loja": "Deckas",
            "titulo": "Produto legado",
            "pergunta": "Qual a medida?",
            "resposta_sugerida": "A medida e 68 mm.",
            "sku": "D9496-32-O",
            "item_id": "MLB4147423197",
        }
    )

    assert "SKU: D9496-32-O" in body
    assert body.endswith("https://produto.mercadolivre.com.br/MLB4147423197")


def test_question_regenerate_creates_new_ai_answer_and_keeps_confirmation_buttons(monkeypatch):
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
            }
        }
    }
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "titulo": "Produto",
        "pergunta": "Serve no modelo X?",
        "resposta_sugerida": "Resposta original",
    }
    interactive = []
    replies = []
    completed = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, mid, payload: completed.append((mid, payload)))
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: interactive.append(kwargs) or {"success": True, "status": "sent"})
    monkeypatch.setattr(
        whatsapp_bridge,
        "_regenerate_question_approval_response",
        lambda approval_item, _approvals, _client, *, guidance="": approval_item.update({"resposta_sugerida": "Nova resposta da IA."}) or "Nova resposta da IA.",
    )
    session = {"client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}}

    assert whatsapp_bridge._handle_question_approval_command(
        {}, state, {"message_id": "wamid.regenerate", "subject_id": "subject-1", "text_body": "ppv_regenerate:ABCDEFGH"}, session
    ) is True
    assert state["question_approval_tokens"]["ABCDEFGH"]["used"] is False
    new_tokens = [token for token in state["question_approval_tokens"] if token != "ABCDEFGH"]
    assert len(new_tokens) == 1
    assert state["question_approval_tokens"][new_tokens[0]]["suggested_response"] == "Nova resposta da IA."
    assert interactive and "Nova resposta da IA." in interactive[0]["body"]
    assert interactive[0]["token"] == new_tokens[0]
    assert completed == [("wamid.regenerate", {"status": "completed", "response_parts": []})]
    assert replies == []


def test_pending_question_approval_is_notified_once_to_authorized_binding(monkeypatch):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "titulo": "Produto",
        "pergunta": "Tem garantia?",
        "resposta_sugerida": "Sim, possui garantia.",
    }
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_worker_health", lambda _cfg: {"bindings": [{
        "machine_id": "machine-1", "client_id": "cliente", "username": "operador", "subject_id": "subject-1"
    }]})
    monkeypatch.setattr(admin_usuarios_common, "_carregar_permissoes_usuario", lambda _user, _client: {"perguntas_pos_venda": True})
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_loja_configs_carregar", lambda _client: {
        "JK Pecas": {"notificar_whatsapp_aprovacoes": True}
    })
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: sent.append(kwargs) or {"success": True, "status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    state = {}

    whatsapp_bridge._forward_question_approvals({"machine_id": "machine-1"}, state)
    whatsapp_bridge._forward_question_approvals({"machine_id": "machine-1"}, state)

    assert len(sent) == 1
    assert sent[0]["subject_id"] == "subject-1"
    assert len(state["question_approval_tokens"]) == 1
    assert len(state["question_approval_notifications"]) == 1


def test_phone_can_disable_mercado_livre_question_suggestions(monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_worker_health", lambda _cfg: {"bindings": [{
        "machine_id": "machine-1", "client_id": "cliente", "username": "operador", "subject_id": "subject-1"
    }]})
    monkeypatch.setattr(admin_usuarios_common, "_carregar_permissoes_usuario", lambda _user, _client: {"perguntas_pos_venda": True})
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda *_args, **_kwargs: sent.append(True))

    whatsapp_bridge._forward_question_approvals(
        {
            "machine_id": "machine-1",
            "phone_notification_settings": {
                "subject-1": {"send_ml_question_suggestions": False},
            },
        },
        {},
    )

    assert sent == []


def test_whatsapp_forwards_only_one_question_until_active_is_answered(monkeypatch):
    approvals = [
        {
            "id": "approval-1",
            "status": "pending",
            "loja": "JK Pecas",
            "titulo": "Produto 1",
            "pergunta": "Pergunta 1?",
            "resposta_sugerida": "Resposta 1.",
        },
        {
            "id": "approval-2",
            "status": "pending",
            "loja": "JK Pecas",
            "titulo": "Produto 2",
            "pergunta": "Pergunta 2?",
            "resposta_sugerida": "Resposta 2.",
        },
    ]
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_worker_health", lambda _cfg: {"bindings": [{
        "machine_id": "machine-1", "client_id": "cliente", "username": "operador", "subject_id": "subject-1"
    }]})
    monkeypatch.setattr(admin_usuarios_common, "_carregar_permissoes_usuario", lambda _user, _client: {"perguntas_pos_venda": True})
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_loja_configs_carregar", lambda _client: {
        "JK Pecas": {"notificar_whatsapp_aprovacoes": True}
    })
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: approvals)
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: sent.append(kwargs) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    state = {}

    whatsapp_bridge._forward_question_approvals({"machine_id": "machine-1"}, state)
    whatsapp_bridge._forward_question_approvals({"machine_id": "machine-1"}, state)

    assert len(sent) == 1
    assert "Pergunta 1?" in sent[0]["body"]
    active = next(iter(state["question_active_threads"].values()))
    assert active["approval_id"] == "approval-1"

    approvals[0]["status"] = "sent"
    whatsapp_bridge._forward_question_approvals({"machine_id": "machine-1"}, state)

    assert len(sent) == 2
    assert "Pergunta 2?" in sent[1]["body"]
    active = next(iter(state["question_active_threads"].values()))
    assert active["approval_id"] == "approval-2"


def test_whatsapp_recognizes_user_guidance_and_regenerates_same_active_question(monkeypatch):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "titulo": "Peca estriada",
        "pergunta": "Ela e estriada dos dois lados?",
        "resposta_sugerida": "Resposta original.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
            }
        }
    }
    regenerated = []
    interactive = []
    replies = []
    completed = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(
        whatsapp_bridge,
        "_regenerate_question_approval_response",
        lambda item, _approvals, _client, *, guidance="": regenerated.append(guidance) or item.update({"resposta_sugerida": "Sim, e estriada dos dois lados."}) or "Sim, e estriada dos dois lados.",
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: interactive.append(kwargs) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, mid, payload: completed.append((mid, payload)))
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    session = {"client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}}

    handled = whatsapp_bridge._handle_question_natural_language(
        {},
        state,
        {
            "message_id": "wamid.guidance",
            "subject_id": "subject-1",
            "text_body": "Estou confirmando que e estriado dos dois lados. Gere outra sugestao de resposta respondendo isso",
        },
        session,
    )

    assert handled is True
    assert regenerated == ["Estou confirmando que e estriado dos dois lados. Gere outra sugestao de resposta respondendo isso"]
    assert "Sim, e estriada dos dois lados." in interactive[0]["body"]
    assert state["question_approval_tokens"]["ABCDEFGH"]["used"] is False
    assert interactive[0]["token"] != "ABCDEFGH"
    assert state["question_approval_tokens"][interactive[0]["token"]]["suggested_response"] == "Sim, e estriada dos dois lados."
    assert completed == [("wamid.guidance", {"status": "completed", "response_parts": []})]
    assert replies == []


def test_question_regenerate_button_preserves_the_latest_user_guidance(monkeypatch):
    guidance = "Refaca a resposta dizendo apenas que nao temos esse produto e agradecendo."
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
                "suggested_response": "No momento nao temos esse produto. Agradecemos o contato.",
                "user_guidance": guidance,
            }
        }
    }
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "Uai Mineirinho",
        "pergunta": "Tem a versao de 10 estrias?",
        "resposta_sugerida": "No momento nao temos esse produto. Agradecemos o contato.",
        "whatsapp_user_guidance": guidance,
    }
    captured_guidance = []
    interactive = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])

    def regenerate(item, _approvals, _client, *, guidance=""):
        captured_guidance.append(guidance)
        item["resposta_sugerida"] = "Infelizmente nao temos esse produto no momento. Agradecemos o contato."
        return item["resposta_sugerida"]

    monkeypatch.setattr(whatsapp_bridge, "_regenerate_question_approval_response", regenerate)
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: interactive.append(kwargs) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)

    assert whatsapp_bridge._handle_question_approval_command(
        {},
        state,
        {"message_id": "wamid.regenerate-guided", "subject_id": "subject-1", "text_body": "ppv_regenerate:ABCDEFGH"},
        {"client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}},
    ) is True

    assert captured_guidance == [guidance]
    assert interactive[0]["token"] != "ABCDEFGH"
    assert state["question_approval_tokens"]["ABCDEFGH"]["suggested_response"].startswith("No momento")
    new_item = state["question_approval_tokens"][interactive[0]["token"]]
    assert new_item["user_guidance"] == guidance
    assert new_item["suggested_response"].startswith("Infelizmente")


@pytest.mark.parametrize(
    "instruction",
    [
        "Gostei da resposta porem remova a parte que pede ele para confirmar com o mecânico",
        "Repita a resposta anterior a essa sua e so remova a parte do mecânico",
        "Refaça a resposta falando que não vendemos e agradecendo",
    ],
)
def test_natural_edit_instruction_never_falls_into_generic_chat_and_reopens_buttons(monkeypatch, instruction):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "Uai Mineirinho",
        "titulo": "Produto",
        "pergunta": "Serve no veículo informado?",
        "resposta_sugerida": "Serve. Confirme com seu mecânico.\n\nEquipe Uai Mineirinho agradece o seu contato.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
            }
        }
    }
    interactive = []
    completed = []
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: {
        "client_id": "cliente",
        "username": "operador",
        "permissions": {"perguntas_pos_venda": True},
        "is_full": False,
    })
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: interactive.append(kwargs) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, mid, payload: completed.append((mid, payload)))
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", lambda *_args, **_kwargs: pytest.fail("must not create generic chat task"))

    def record_regenerate(item, _approvals, _client, *, guidance=""):
        guidance_capture.append(guidance)
        item["resposta_sugerida"] = "Serve.\n\nEquipe Uai Mineirinho agradece o seu contato."
        return item["resposta_sugerida"]

    guidance_capture = []
    monkeypatch.setattr(whatsapp_bridge, "_regenerate_question_approval_response", record_regenerate)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        state,
        {
            "message_id": "wamid.edit",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": instruction,
            "message_type": "text",
        },
    )

    assert guidance_capture == [instruction]
    assert interactive and "Serve." in interactive[0]["body"]
    assert interactive[0]["token"] != "ABCDEFGH"
    assert state["question_approval_tokens"][interactive[0]["token"]]["user_guidance"] == instruction
    assert completed == [("wamid.edit", {"status": "completed", "response_parts": []})]


def test_audio_guidance_is_transcribed_before_question_revision_routing(monkeypatch):
    transcript = "Reformule a resposta, diga apenas que nao temos esse produto e agradeca."
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "Uai Mineirinho",
        "titulo": "Roda livre automatica",
        "pergunta": "Tem a versao para Willys?",
        "resposta_sugerida": "Resposta tecnica anterior.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
                "suggested_response": "Resposta tecnica anterior.",
            }
        }
    }
    captured_guidance = []
    interactive = []
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: {
        "client_id": "cliente",
        "username": "operador",
        "permissions": {"perguntas_pos_venda": True},
        "is_full": False,
    })
    monkeypatch.setattr(whatsapp_bridge, "_download_media", lambda *_args: {"mime_type": "audio/ogg", "path": "audio-teste.ogg"})
    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", lambda _path: {"success": True, "text": transcript})
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])

    def regenerate(item, _approvals, _client, *, guidance=""):
        captured_guidance.append(guidance)
        item["resposta_sugerida"] = "No momento nao temos esse produto. Agradecemos o contato."
        return item["resposta_sugerida"]

    monkeypatch.setattr(whatsapp_bridge, "_regenerate_question_approval_response", regenerate)
    monkeypatch.setattr(whatsapp_bridge, "_post_interactive_approval", lambda _cfg, **kwargs: interactive.append(kwargs) or {"status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", lambda *_args, **_kwargs: pytest.fail("audio guidance must not create a generic task"))

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1", "personal_phone": "5511999999999"},
        state,
        {
            "message_id": "wamid.audio-guidance",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "wa_id": "5511999999999",
            "message_type": "audio",
            "media_id": "media-1",
            "media_mime": "audio/ogg",
        },
    )

    assert captured_guidance == [transcript]
    assert len(interactive) == 1
    assert "No momento nao temos esse produto" in interactive[0]["body"]
    assert interactive[0]["token"] != "ABCDEFGH"


def test_natural_question_action_accepts_short_confirmation_after_revision():
    assert whatsapp_bridge._question_natural_action("Responda isso") == "confirm_approval"
    assert whatsapp_bridge._question_natural_action("Resposta isso") == "confirm_approval"


def test_explicit_user_answer_is_kept_exactly_and_saved_without_calling_ai(monkeypatch):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "question_id": "question-1",
        "pergunta": "Serve no modelo X?",
        "resposta_sugerida": "Resposta anterior.",
    }
    approvals = [approval]
    saved = []
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_gerar_resposta_manual",
        lambda *_args, **_kwargs: pytest.fail("explicit user answer must not be rewritten by AI"),
    )
    monkeypatch.setattr(
        perguntas_pos_venda_state,
        "_perguntas_ia_aprovacoes_salvar",
        lambda client_id, values: saved.append((client_id, values)),
    )

    response = whatsapp_bridge._regenerate_question_approval_response(
        approval,
        approvals,
        "cliente",
        guidance="Use esta resposta: Olá! Sim, serve no modelo informado.",
    )

    assert response == "Olá! Sim, serve no modelo informado."
    assert approval["resposta_sugerida"] == response
    assert saved and saved[-1][0] == "cliente"


def test_research_revision_keeps_old_draft_pending_instead_of_saving_fallback(monkeypatch):
    approval = {
        "id": "approval-research",
        "status": "pending",
        "loja": "JK Pecas",
        "question_id": "question-evoque",
        "pergunta": "Serve na Evoque 2017 gasolina e quantos bar de pressao?",
        "resposta_sugerida": "Resposta anterior que ainda sera revisada.",
        "sku": "254-1",
    }
    approvals = [approval]
    saved = []
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_gerar_resposta_manual",
        lambda request, _client: {
            "job_id": "job-research-loop",
            "status": "queued",
            "queued": True,
            "data_sufficient": False,
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        perguntas_pos_venda_state,
        "_perguntas_ia_aprovacoes_salvar",
        lambda client_id, values: saved.append((client_id, values)),
    )

    with pytest.raises(whatsapp_bridge._QuestionResearchPending) as pending:
        whatsapp_bridge._regenerate_question_approval_response(
            approval,
            approvals,
            "cliente",
            guidance="Pesquise a aplicacao e a pressao correta em fontes tecnicas.",
        )

    assert pending.value.job_id == "job-research-loop"
    assert approval["resposta_sugerida"] == "Resposta anterior que ainda sera revisada."
    assert approval["research_status"] == "queued"
    assert approval["research_delivery_state"] == "waiting_evidence"
    assert approval["data_sufficient"] is False
    assert saved and saved[-1][0] == "cliente"


def test_completed_research_is_delivered_once_as_new_approval_card(monkeypatch):
    approval = {
        "id": "approval-research",
        "status": "pending",
        "loja": "JK Pecas",
        "question_id": "question-evoque",
        "pergunta": "Serve na Evoque 2017 gasolina e quantos bar de pressao?",
        "resposta_sugerida": "Resposta antiga.",
        "research_job_id": "job-research-loop",
        "research_delivery_state": "waiting_evidence",
        "whatsapp_user_guidance": "Pesquise a aplicacao e a pressao.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-research",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": True,
            }
        }
    }
    delivered = []
    saved_approvals = []
    monkeypatch.setattr(
        perguntas_pos_venda_codex,
        "get_job",
        lambda *_args, **_kwargs: {
            "job_id": "job-research-loop",
            "status": "completed",
            "data_sufficient": True,
            "proposal_version": 2,
            "proposal_hash": "hash-final",
            "result": {
                "resposta": "Essa bomba e compativel com a Evoque 2017 gasolina e trabalha a 3 bar.",
                "data_sufficient": True,
                "proposal_version": 2,
                "proposal_hash": "hash-final",
            },
        },
    )
    monkeypatch.setattr(
        perguntas_pos_venda_state,
        "_perguntas_ia_aprovacoes_salvar",
        lambda _client, values: saved_approvals.append(values),
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_interactive_approval",
        lambda _cfg, **kwargs: delivered.append(kwargs) or {"status": "sent"},
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)

    assert whatsapp_bridge._deliver_completed_question_research(
        {},
        state,
        [approval],
        approval,
        client_id="cliente",
        subject_id="subject-1",
        username="operador",
    ) is True

    assert len(delivered) == 1
    assert "3 bar" in delivered[0]["body"]
    assert approval["research_delivery_state"] == "delivered"
    assert approval["research_delivered_job_id"] == "job-research-loop"
    assert approval["data_sufficient"] is True
    assert state["question_approval_tokens"]["ABCDEFGH"]["decision"] == "superseded_by_verified_research"
    assert saved_approvals
    assert whatsapp_bridge._deliver_completed_question_research(
        {}, state, [approval], approval, client_id="cliente", subject_id="subject-1", username="operador"
    ) is False


def test_active_legacy_incomplete_approval_resumes_research(monkeypatch):
    approval = {
        "id": "approval-legacy",
        "status": "pending",
        "codex_job_id": "job-legacy",
        "data_sufficient": False,
    }
    resumed = []
    saved = []
    monkeypatch.setattr(
        perguntas_pos_venda_codex,
        "get_job",
        lambda *_args, **_kwargs: {
            "job_id": "job-legacy",
            "status": "completed",
            "data_sufficient": False,
            "result": {"data_sufficient": False},
        },
    )
    monkeypatch.setattr(
        perguntas_pos_venda_codex,
        "resume_incomplete_job",
        lambda *args, **kwargs: resumed.append((args, kwargs)) or {
            "job_id": "job-legacy",
            "status": "waiting_retry",
            "data_sufficient": False,
            "retry_count": 1,
        },
    )
    monkeypatch.setattr(
        perguntas_pos_venda_state,
        "_perguntas_ia_aprovacoes_salvar",
        lambda _client, values: saved.append(values),
    )

    assert whatsapp_bridge._deliver_completed_question_research(
        {}, {}, [approval], approval, client_id="cliente", subject_id="subject-1", username="operador"
    ) is False
    assert resumed
    assert approval["research_delivery_state"] == "waiting_evidence"
    assert approval["research_status"] == "waiting_retry"
    assert saved


def test_rejecting_suggestion_keeps_same_question_active(monkeypatch):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "pergunta": "Pergunta ainda sem resposta?",
        "resposta_sugerida": "Sugestao a revisar.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
            }
        }
    }
    saved = []
    replies = []
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_salvar", lambda _client, values: saved.append(values))
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    session = {"client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}}

    assert whatsapp_bridge._handle_question_natural_language(
        {},
        state,
        {"message_id": "wamid.reject", "subject_id": "subject-1", "text_body": "Nao responda, rejeite essa sugestao"},
        session,
    ) is True

    assert approval["status"] == "pending"
    assert saved and saved[-1][0]["id"] == "approval-1"
    assert state["question_approval_tokens"]["ABCDEFGH"]["used"] is False
    assert next(iter(state["question_active_threads"].values()))["approval_id"] == "approval-1"
    assert "continua ativa" in replies[-1][1]


def test_natural_approval_reopens_tokenized_confirmation_without_sending(monkeypatch):
    approval = {
        "id": "approval-1",
        "status": "pending",
        "loja": "JK Pecas",
        "pergunta": "Ela e estriada dos dois lados?",
        "resposta_sugerida": "Sim, e estriada dos dois lados.",
    }
    state = {
        "question_approval_tokens": {
            "ABCDEFGH": {
                "approval_id": "approval-1",
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "operador",
                "created_at": whatsapp_bridge.time.time(),
                "used": False,
                "suggested_response": "Sim, e estriada dos dois lados.",
            }
        }
    }
    calls = []
    replies = []
    interactive = []
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: {
        "client_id": "cliente", "username": "operador", "permissions": {"perguntas_pos_venda": True}, "is_full": False
    })
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", lambda _client: [approval])
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_aprovacoes_aprovar",
        lambda req, client: calls.append((req, client)) or {"success": True, "approval": {**approval, "status": "sent"}},
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_interactive_approval",
        lambda _cfg, **payload: interactive.append(payload) or {"success": True, "status": "sent"},
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: {"success": True, "status": "sent"})
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", lambda *_args, **_kwargs: pytest.fail("must not create generic task"))

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        state,
        {
            "message_id": "wamid.approve-natural",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Perfeito. responda a pergunta",
            "message_type": "text",
        },
    )

    assert calls == []
    assert interactive[0]["token"] == "ABCDEFGH"
    assert state["question_approval_tokens"]["ABCDEFGH"]["used"] is False
    assert replies == []


def test_whatsapp_task_is_read_only_or_waits_for_local_approval(codex_runtime):
    query = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Qual e o estoque atual?", conversation_id="wa:cliente:admin:5511"),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"message_id": "wamid.1", "wa_id": "5511999999999"},
    )["task"]
    assert query["origin"] == "whatsapp"
    assert query["channel_message_id"] == "wamid.1"
    assert query["external_safe_mode"] is True
    assert query["sandbox"] == "read_only"
    assert query["status"] == "queued"

    mutation = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Altere o modulo de configuracoes"),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"message_id": "wamid.2", "wa_id": "5511999999999"},
    )["task"]
    assert mutation["sandbox"] == "workspace_write"
    assert mutation["status"] == "awaiting_input"
    assert mutation["approved"] is False
    assert mutation["required_input"]


def test_whatsapp_full_mobile_uses_full_catalog_but_mutations_require_app(codex_runtime, monkeypatch):
    monkeypatch.setattr(codex_actions.threading, "Thread", lambda *args, **kwargs: type("NoStart", (), {"start": lambda self: None})())
    query = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Consulte as vendas dos ultimos 30 dias",
            sandbox="workspace_write",
            approval_mode="request",
        ),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"message_id": "wamid.mobile.query", "subject_id": "subject-1", "wa_id": "5511999999999", "mobile_full_access": True},
    )["task"]
    assert query["external_safe_mode"] is False
    assert query["whatsapp_full_access"] is True
    assert query["sandbox"] == "read_only"
    assert query["status"] == "queued"

    mutation = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Sincronize as vendas da loja JK Pecas de 01/07/2026 a 10/07/2026",
            sandbox="workspace_write",
            approval_mode="request",
        ),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"message_id": "wamid.mobile.write", "subject_id": "subject-1", "wa_id": "5511999999999", "mobile_full_access": True},
    )["task"]
    assert mutation["status"] == "awaiting_approval"
    assert mutation["external_safe_mode"] is True
    assert mutation["whatsapp_full_access"] is False
    with pytest.raises(HTTPException) as mobile_approval:
        codex_console.codex_aprovar_tarefa_para_sessao(
            mutation["task_id"],
            _full_session(),
            approval_source="whatsapp",
            subject_id="subject-1",
        )
    assert mobile_approval.value.status_code == 403
    assert "aplicativo" in str(mobile_approval.value.detail).lower()


def test_whatsapp_query_only_metadata_forces_full_user_to_read_only(codex_runtime):
    task = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Atualize as vendas da loja JK Pecas",
            sandbox="full_access",
            approval_mode="full_access",
        ),
        _full_session(),
        origin="whatsapp",
        channel_metadata={
            "message_id": "wamid.query-only",
            "subject_id": "subject-1",
            "wa_id": "5511999999999",
            "mobile_full_access": True,
            "query_policy": {
                "mode": "query_only",
                "domains": ["vendas"],
                "read_only": True,
                "deny_approval": True,
                "store_required": True,
                "store": "JK Pecas",
            },
        },
    )["task"]

    assert task["whatsapp_query_only"] is True
    assert task["query_policy"]["domains"] == ["vendas"]
    assert task["sandbox"] == "read_only"
    assert task["approval_mode"] == "read_only"
    assert task["access_mode"] == "query_only"
    assert task["status"] == "queued"
    assert task["approval_required"] is False
    with pytest.raises(HTTPException) as exc:
        codex_console.codex_aprovar_tarefa_para_sessao(
            task["task_id"],
            _full_session(),
            approval_source="whatsapp",
            subject_id="subject-1",
        )
    assert exc.value.status_code == 403


def test_nonfull_whatsapp_mutation_remains_read_only(codex_runtime):
    session = {"username": "vendas", "client_id": "cliente", "permissions": {"vendas": True}, "is_full": False}
    task = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Atualize as vendas"),
        session,
        origin="whatsapp",
        channel_metadata={"message_id": "wamid.nonfull", "subject_id": "subject", "wa_id": "5511999999999"},
    )["task"]
    assert task["sandbox"] == "read_only"
    assert task["status"] == "queued"
    assert task["whatsapp_full_access"] is False


def test_whatsapp_sensitive_request_waits_for_missing_data_before_app_approval(codex_runtime, monkeypatch):
    task = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Corrija o modulo de configuracoes"),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"wa_id": "5511999999999"},
    )["task"]
    assert task["status"] == "awaiting_input"
    assert task["required_input"]
    assert task["agent_state"] == "aguardando_dados"


def test_external_catalog_omits_action_tools(monkeypatch):
    monkeypatch.setattr(
        "backend.services.codex_assistant._assistant_tools_public",
        lambda _permissions: [
            {"id": "sales_summary", "read_only": True},
            {"id": "program_action_match", "read_only": True},
            {"id": "operational_dispatcher", "read_only": True},
            {"id": "danger", "read_only": False},
        ],
    )
    ids = {item["id"] for item in codex_console._codex_agent_tool_catalog({"full": True}, read_only_only=True)}
    assert ids == {"sales_summary"}


def test_capability_catalog_filters_mutations(monkeypatch):
    monkeypatch.setattr(
        codex_capabilities,
        "build_capabilities",
        lambda _client: [
            {"id": "read", "module": "vendas", "read_only": True, "mutating": False, "requires_approval": False},
            {"id": "write", "module": "vendas", "read_only": False, "mutating": True, "requires_approval": True},
        ],
    )
    result = codex_capabilities.compact_capability_catalog(client_id="cliente", read_only_only=True)
    assert result["total_capabilities"] == 1
    assert result["modules"][0]["capabilities"][0]["id"] == "read"


def test_audio_over_ten_minutes_is_rejected_before_model_load(tmp_path, monkeypatch):
    audio = tmp_path / "long.ogg"
    audio.write_bytes(b"not-used")
    monkeypatch.setattr(whatsapp_transcribe, "_audio_duration", lambda _path: 601.0)
    monkeypatch.setattr(whatsapp_transcribe, "_load_model", lambda _path: pytest.fail("model must not load"))
    with pytest.raises(RuntimeError, match="audio_exceeds_ten_minutes"):
        whatsapp_transcribe._transcribe(tmp_path / "model", audio)


def test_transcription_uses_portuguese_vad_then_detection_fallback(tmp_path, monkeypatch):
    class Segment:
        def __init__(self, text, logprob):
            self.text = text
            self.avg_logprob = logprob

    class Info:
        duration = 2.0
        language = "pt"
        language_probability = 0.95

    calls = []

    class Model:
        def transcribe(self, _path, **kwargs):
            calls.append(kwargs)
            if kwargs.get("language") == "pt":
                return iter([]), Info()
            return iter([Segment(" teste em portugues ", -0.1)]), Info()

    monkeypatch.setattr(whatsapp_transcribe, "_audio_duration", lambda _path: 2.0)
    monkeypatch.setattr(whatsapp_transcribe, "_verify_manifest", lambda _path: None)
    monkeypatch.setattr(whatsapp_transcribe, "_load_model", lambda _path: Model())
    monkeypatch.setattr(whatsapp_transcribe, "_write_manifest", lambda _path: {})
    result = whatsapp_transcribe._transcribe(tmp_path / "model", tmp_path / "short.ogg")
    assert result["success"] is True
    assert result["text"] == "teste em portugues"
    assert result["used_detection_fallback"] is True
    assert calls[0]["language"] == "pt"
    assert calls[0]["vad_filter"] is True
    assert calls[1]["language"] is None


def test_prompt_preserves_media_and_local_transcription_context():
    prompt = whatsapp_bridge._message_prompt(
        {"message_id": "wamid.3", "text_body": "Analise esta imagem"},
        {"path": ".codex-remote-attachments/a.jpg", "mime_type": "image/jpeg", "size": 123},
        {"success": True, "text": "audio em portugues", "duration_seconds": 3.2, "confidence": 0.9, "language": "pt"},
        ai_behavior="Responda de forma objetiva e sempre informe a loja consultada.",
    )
    assert "Origem: WhatsApp" in prompt
    assert ".codex-remote-attachments/a.jpg" in prompt
    assert "transcrito localmente" in prompt
    assert "nenhuma API externa" in prompt
    assert "Instrucoes administrativas especificas para atender este numero" in prompt
    assert "Responda de forma objetiva e sempre informe a loja consultada." in prompt
    assert "nao ampliam permissoes" in prompt


def _write_test_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )


def test_whatsapp_product_photo_request_is_explicit_and_resolved_per_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path / "info")
    client_a = tmp_path / "info" / "000001" / "cadastro_fotos" / "001.png"
    client_b = tmp_path / "info" / "000002" / "cadastro_fotos" / "001.png"
    _write_test_png(client_a)
    _write_test_png(client_b)

    assert whatsapp_bridge._whatsapp_image_requested("Consegue me mandar a foto do SKU 001?") is True
    assert whatsapp_bridge._whatsapp_image_requested("Analise esta imagem que anexei") is False
    assert whatsapp_bridge._whatsapp_resolve_image_reference("/api/cadastro/foto-arquivo/001.png", "000002") == client_b.resolve()
    assert whatsapp_bridge._whatsapp_resolve_image_reference(str(client_a), "000002") is None
    assert whatsapp_bridge._whatsapp_resolve_image_reference("../../000001/cadastro_fotos/001.png", "000002") is None
    assert whatsapp_bridge._whatsapp_find_image_by_sku("manda a foto do SKU 001", "", "000002") == client_b.resolve()


def test_whatsapp_requested_product_photo_is_sent_as_media_and_internal_link_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path / "info")
    image = tmp_path / "info" / "000002" / "cadastro_fotos" / "001.png"
    wrong_image = tmp_path / "info" / "000002" / "cadastro_fotos" / "002.png"
    _write_test_png(image)
    _write_test_png(wrong_image)
    calls = []

    def fake_post(config, message_id, path, mime_type, caption, filename):
        calls.append(
            {
                "config": config,
                "message_id": message_id,
                "exists": path.is_file(),
                "mime_type": mime_type,
                "caption": caption,
                "filename": filename,
            }
        )
        return {"success": True, "status": "sent", "meta_message_id": "wamid.out.1"}

    monkeypatch.setattr(whatsapp_bridge, "_post_outbound_image", fake_post)
    response = (
        "SKU 001 - Cebolao do Radiador Sensor Temperatura\n\n"
        "![SKU 002](/api/cadastro/foto-arquivo/002.png)\n\n"
        "![SKU 001](/api/cadastro/foto-arquivo/001.png)\n\n"
        "Fonte: imagem e cadastro de produtos do JK Sistema."
    )
    clean, results = whatsapp_bridge._whatsapp_deliver_requested_images(
        {"machine_id": "machine"}, "wamid.in.1", response, "Consegue me mandar a foto do SKU 001?", "000002"
    )

    assert len(calls) == 1
    assert calls[0]["message_id"] == "wamid.in.1"
    assert calls[0]["filename"].startswith("001")
    assert calls[0]["exists"] is True
    assert calls[0]["mime_type"] in {"image/jpeg", "image/png"}
    assert calls[0]["caption"].startswith("SKU 001")
    assert results[0]["success"] is True
    assert "![" not in clean
    assert "/api/cadastro/" not in clean
    assert "Fonte:" in clean


def test_whatsapp_image_failure_returns_clean_text_without_broken_internal_url(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path / "info")
    image = tmp_path / "info" / "000002" / "cadastro_fotos" / "001.png"
    _write_test_png(image)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_outbound_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("meta_media_upload_failed")),
    )
    response = "SKU 001\n\n![SKU 001](/api/cadastro/foto-arquivo/001.png)"
    clean, results = whatsapp_bridge._whatsapp_deliver_requested_images(
        {}, "wamid.in.2", response, "Envie a imagem do SKU 001", "000002"
    )

    assert results[0]["success"] is False
    assert "/api/cadastro/" not in clean
    assert "link quebrado" in clean


def test_whatsapp_image_markdown_does_not_send_without_explicit_request(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path / "info")
    image = tmp_path / "info" / "000002" / "cadastro_fotos" / "001.png"
    _write_test_png(image)
    monkeypatch.setattr(whatsapp_bridge, "_post_outbound_image", lambda *_args, **_kwargs: pytest.fail("must not send"))
    response = "Veja no cadastro: ![SKU 001](/api/cadastro/foto-arquivo/001.png)"
    clean, results = whatsapp_bridge._whatsapp_deliver_requested_images(
        {}, "wamid.in.3", response, "Qual e o estoque do SKU 001?", "000002"
    )

    assert clean == response
    assert results == []


def test_whatsapp_formatter_converts_markdown_tables_and_splits_long_reports():
    report = (
        "# Relatorio de vendas\n\n"
        "| Loja | Total |\n| --- | --- |\n| JK Pecas | 120 |\n\n"
        + "**Analise:** "
        + ("resultado detalhado " * 420)
    )
    parts = whatsapp_bridge._whatsapp_response_parts(report, "📊 BLACK JOHN — RELATÓRIO")
    assert 2 <= len(parts) <= whatsapp_bridge.WHATSAPP_MAX_PARTS
    assert all(len(part) < 3500 for part in parts)
    assert sum("📊 Relatório" in part for part in parts) == 1
    assert all("JK Sistema • Black John" not in part for part in parts)
    assert "🏪 JK Pecas" in "\n".join(parts)
    assert "**Analise:**" not in "\n".join(parts)


def test_whatsapp_sales_report_is_split_into_mobile_semantic_cards():
    report = """# Relatório de vendas — JK Peças
Período: 01/06/2026 a 30/06/2026
Conta: JK Peças

## Dados principais
- **Quantidade vendida:** 3.078 unidades
- **Faturamento bruto:** R$ 399.255,92
- **Pedidos:** 2.658
- **Ticket médio:** R$ 150,21
- **Devoluções:** 250 unidades
- **Valor devolvido:** R$ 41.455,01
- **Resultado após devoluções:** R$ 357.800,91

## SKUs mais vendidos
- **SKU:** 124-1
  **Produto:** Aparelho Anti Latido Ultrassônico Apito Adestramento Cães
  **Qtd.:** 201
  **Valor:** R$ 12.765,25
- **SKU:** 422
  **Produto:** Disco Enxada Rotativa Roçadeira Capina Grama Universal
  **Qtd.:** 177
  **Valor:** R$ 6.637,18
- **SKU:** 392-1
  **Produto:** Amortecedor Tampa da Caçamba Traseira Gun125 Direito
  **Qtd.:** 150
  **Valor:** R$ 22.056,42

## Análise
- O faturamento permaneceu concentrado nos produtos líderes.
- As devoluções reduziram o resultado do período.

## Fontes e cobertura
Histórico de vendas do JK Sistema, conta JK Peças, 2.658 pedidos no período.
"""
    parts = whatsapp_bridge._whatsapp_response_parts(report, "📊 BLACK JOHN — RELATÓRIO")
    joined = "\n\n".join(parts)

    assert len(parts) == 4
    assert "_Resumo • 1 de 4_" in parts[0]
    assert "*📌 INDICADORES*" in parts[0]
    assert "*Itens vendidos:* 3.078 unidades" in parts[0]
    assert "Faturamento" in parts[0]
    assert "```" not in parts[0]
    assert "_SKUs vendidos • 2 de 4_" in parts[1]
    assert "*📦 SKUs VENDIDOS*" in parts[1]
    assert "1️⃣ *SKU 124-1*" in parts[1]
    assert "\n\n2️⃣ *SKU 422*" in parts[1]
    assert "`Qtd. 201  |  Total R$ 12.765,25`" in parts[1]
    assert "_Análise • 3 de 4_" in parts[2]
    assert "_Fontes • 4 de 4_" in parts[3]
    assert "Histórico de vendas" in parts[3]
    assert "_JK Sistema • Black John_" not in joined
    assert "• *SKU:*" not in joined
    assert all(len(part) < 3500 for part in parts)


def test_whatsapp_numbered_sku_ranking_keeps_quantity_and_total_per_sku():
    report = """# Relatorio

Loja: **JK Pecas**
Periodo: **07/07/2026 a 13/07/2026**

## Mais vendidos

**1. SKU 008**
Base Para Antena De Carros
Quantidade: **19**
Valor: **R$ 951,55**

**2. SKU 448**
Console Do Teto Com Porta Oculos Ranger 2013 2014 2015 Ford
Quantidade: **14**
Valor: **R$ 2.928,01**

**3. SKU 214**
Comutador De Ignicao 931102d000
Quantidade: **11**
Valor: **R$ 877,62**

## Fontes e cobertura
Historico de vendas do JK Sistema.
"""

    joined = "\n\n".join(
        whatsapp_bridge._whatsapp_response_parts(report, "BLACK JHON - RELATORIO")
    )

    assert "*SKU nao informado*" not in joined
    assert "*SKU 008*\nBase Para Antena De Carros\n`Qtd. 19  |  Total R$ 951,55`" in joined
    assert "*SKU 448*\nConsole Do Teto Com Porta Oculos Ranger 2013 2014 2015 Ford\n`Qtd. 14  |  Total R$ 2.928,01`" in joined
    assert "*SKU 214*\nComutador De Ignicao 931102d000\n`Qtd. 11  |  Total R$ 877,62`" in joined
    assert joined.count("*SKU ") == 3


def test_whatsapp_report_keeps_full_metric_labels_and_product_names_without_ellipsis():
    product = "Produto " + ("com nome completo e detalhado " * 8).strip()
    report = f"""# Relatório de vendas

## Dados principais
- **Pedidos pagos analisados:** 96
- **SKUs vendidos no retorno completo da API:** 48
- **Ticket médio por pedido pago:** R$ 163,00

## SKUs vendidos
- **SKU:** SKU-001
  **Produto:** {product}
  **Qtd.:** 5
  **Valor unitário médio:** R$ 149,90
  **Total vendido:** R$ 749,50
"""

    joined = "\n".join(whatsapp_bridge._whatsapp_response_parts(report, "📊 BLACK JOHN — RELATÓRIO"))

    assert "*Pedidos pagos analisados:* 96" in joined
    assert "*SKUs vendidos no retorno completo da API:* 48" in joined
    assert "*Ticket médio por pedido pago:* R$ 163,00" in joined
    assert product in joined
    assert "..." not in joined
    assert "…" not in joined


def test_whatsapp_simple_reply_is_conversational_without_forced_branding():
    parts = whatsapp_bridge._whatsapp_response_parts(
        "Encontrei 12 pedidos. Quer que eu detalhe os três mais recentes?",
        "✅ BLACK JOHN — RESULTADO",
    )

    assert parts == ["Encontrei 12 pedidos. Quer que eu detalhe os três mais recentes?"]
    assert "BLACK JOHN" not in parts[0]
    assert "JK Sistema" not in parts[0]


def test_exact_order_history_is_sent_in_all_numbered_parts_without_eight_part_cap():
    paragraphs = [f"Mensagem literal {index}: " + ("x" * 1200) for index in range(20)]
    response = whatsapp_bridge.WHATSAPP_EXACT_ORDER_HISTORY_MARKER + "\n" + "\n\n".join(paragraphs)

    parts = whatsapp_bridge._whatsapp_response_parts(response, "📊 BLACK JOHN — RELATÓRIO")
    joined = "\n".join(parts)

    assert len(parts) > 8
    assert all(len(part) <= whatsapp_bridge.WHATSAPP_PART_BODY_CHARS + 80 for part in parts)
    assert all(f"Mensagem literal {index}:" in joined for index in range(20))
    assert whatsapp_bridge.WHATSAPP_EXACT_ORDER_HISTORY_MARKER not in joined
    assert "Conteudo adicional disponivel" not in joined
    assert parts[0].startswith(f"_Parte 1 de {len(parts)}_")
    assert parts[-1].startswith(f"_Parte {len(parts)} de {len(parts)}_")


def test_whatsapp_security_reply_keeps_context_title_without_signature():
    parts = whatsapp_bridge._whatsapp_response_parts(
        "Esse código expirou e nada foi executado.",
        "⚠️ BLACK JOHN — DECISÃO INVÁLIDA",
    )

    assert parts[0].startswith("*⚠️ BLACK JOHN — DECISÃO INVÁLIDA*")
    assert "JK Sistema • Black John" not in parts[0]


def test_whatsapp_agent_prompt_requests_compact_mobile_report_format(monkeypatch):
    monkeypatch.setattr(codex_console, "_codex_agent_tool_catalog", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(codex_console, "_codex_agent_capability_catalog", lambda *_args, **_kwargs: {})
    prompt = codex_console._codex_agent_initial_prompt(
        "Faça um relatório de vendas",
        {},
        {},
        "cliente",
        {"full": True},
        "read_only",
        "gpt-5",
        "medium",
        "normal",
        "request",
        whatsapp_full_access=True,
    )
    assert "Formato de relatorio para WhatsApp" in prompt
    assert "sem tabelas Markdown" in prompt
    assert "relatorios completos, liste todos os SKUs" in prompt
    assert "nome completo do produto" in prompt
    assert "nao coloque titulo em respostas simples" in prompt.lower()
    assert "nao assine ao final" in prompt.lower()


def test_whatsapp_approval_code_is_one_time_and_bound_to_subject(codex_runtime, monkeypatch):
    monkeypatch.setattr(codex_actions.threading, "Thread", lambda *args, **kwargs: type("NoStart", (), {"start": lambda self: None})())
    task = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Responda a pergunta do Mercado Livre da loja JK Pecas question_id:123. Resposta: Sim, serve.",
            sandbox="workspace_write",
            approval_mode="request",
        ),
        _full_session(),
        origin="whatsapp",
        channel_metadata={"message_id": "origin", "subject_id": "subject-1", "wa_id": "5511999999999", "mobile_full_access": True},
    )["task"]
    state = {
        "pending_messages": {
            "origin": {
                "kind": "task",
                "task_id": task["task_id"],
                "subject_id": "subject-1",
                "username": "admin",
                "client_id": "cliente",
                "request_text": "Responder pergunta 123",
                "approval_code": "ABCD2345",
                "approval_expires_at": __import__("time").time() + 600,
                "approval_used": False,
                "awaiting_notified": True,
            }
        }
    }
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _config, message_id, payload: sent.append((message_id, payload)) or {"success": True})

    assert whatsapp_bridge._handle_approval_command(
        {},
        state,
        {"message_id": "approval", "subject_id": "subject-1", "text_body": "APROVAR ABCD2345"},
        _full_session(),
    ) is True
    approved = codex_console._codex_load_task(task["task_id"])
    assert approved["status"] == "awaiting_approval"
    assert state["pending_messages"]["origin"]["approval_used"] is False
    assert sent[-1][0] == "approval"
    assert "aplicativo" in sent[-1][1]["response"].lower()


def test_sales_mobile_action_requires_one_time_approval_code(monkeypatch):
    proposal = {
        "proposal_id": "proposal-1",
        "summary": "Sincronizar vendas da JK Pecas entre 2026-07-01 e 2026-07-10",
        "risk": "external_write",
        "can_execute": True,
        "channels_allowed": ["app", "whatsapp"],
        "action": {"label": "Sincronizar vendas"},
    }
    proposal_calls = []
    monkeypatch.setattr(
        codex_actions,
        "create_proposal",
        lambda **_kwargs: proposal_calls.append(True) or {"success": True, "matched": True, "proposal": proposal},
    )
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _config, message_id, payload: sent.append((message_id, payload)) or {"success": True})
    state = {}
    handled = whatsapp_bridge._try_create_action_pending(
        {},
        state,
        {"message_id": "wamid.action", "subject_id": "subject-1", "wa_id": "5511999999999"},
        _full_session(),
        "wa-cliente-admin-subject",
        "Sincronize vendas da JK Pecas de 2026-07-01 a 2026-07-10",
        {"title": "WhatsApp"},
    )
    assert handled is True
    assert proposal_calls == [True]
    assert not state.get("pending_messages")
    assert "aplicativo" in sent[-1][1]["response"].lower()


def test_stock_sync_from_whatsapp_requires_one_time_approval_code(monkeypatch):
    proposal = {
        "proposal_id": "proposal-stock-1",
        "summary": "Atualizar estoque da JK Pecas pela Bling",
        "risk": "external_write",
        "can_execute": True,
        "channels_allowed": ["app", "whatsapp"],
        "action": {"id": "estoque.sync_bling_to_jk", "module": "estoque", "label": "Atualizar estoque"},
    }
    monkeypatch.setattr(codex_actions, "create_proposal", lambda **_kwargs: {"success": True, "matched": True, "proposal": proposal})
    monkeypatch.setattr(
        codex_actions,
        "approve_proposal",
        lambda *_args, **_kwargs: {"success": True, "run": {"run_id": "run-stock-1", "status": "queued"}},
    )
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _config, message_id, payload: sent.append((message_id, payload)) or {"success": True})
    state = {}

    handled = whatsapp_bridge._try_create_action_pending(
        {},
        state,
        {"message_id": "wamid.stock", "subject_id": "subject-1", "wa_id": "5511999999999"},
        _full_session(),
        "wa-cliente-admin-subject",
        "Atualize o estoque da loja JK Pecas",
        {"title": "WhatsApp"},
    )

    assert handled is True
    assert not state.get("pending_messages")
    assert sent[-1][1]["status"] == "completed"
    assert "aplicativo" in sent[-1][1]["response"].lower()


def _configure_whatsapp_process_test(monkeypatch, stores):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_authorized_api_stores", lambda *_args: list(stores))
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)


def test_query_only_classifier_keeps_report_delivery_and_fresh_api_read_only():
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Envie um relatorio de vendas") == []
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Atualize agora as vendas via API da loja JK Pecas") == []
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Sincronize as vendas da loja JK Pecas") == ["vendas"]
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Atualize o preco do anuncio MLB1234567890") == ["anuncios_ml"]
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Envie o relatorio e depois pause o anuncio MLB1234567890") == ["anuncios_ml"]
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("Envie esta resposta para a pergunta do Mercado Livre") == []
    assert whatsapp_bridge._whatsapp_query_policy(
        "Envie esta resposta para a pergunta do Mercado Livre",
        _full_session(),
    ) == {}
    assert whatsapp_bridge._whatsapp_readonly_inquiry("Tem pergunta para responder?") is True
    assert whatsapp_bridge._whatsapp_readonly_inquiry("Qual o saldo do SKU 001?") is True
    assert whatsapp_bridge._whatsapp_readonly_inquiry("Me diga as informacoes do anuncio MLB1234567890") is True
    assert whatsapp_bridge._whatsapp_readonly_inquiry("Responda a pergunta do comprador") is False


def test_general_answer_classifier_separates_conversation_from_system_operations(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_session_stores", lambda _session: ["JK Pecas", "Deckas"])

    assert whatsapp_bridge._whatsapp_general_answer_request("Qual e a capital do Brasil?", _full_session()) is True
    assert whatsapp_bridge._whatsapp_general_answer_request("Corrija esta frase: nos vai amanha.", _full_session()) is True
    assert whatsapp_bridge._whatsapp_general_answer_request("O que e estoque de seguranca?", _full_session()) is True
    assert whatsapp_bridge._whatsapp_general_answer_request("Como funciona o Mercado Livre?", _full_session()) is True

    assert whatsapp_bridge._whatsapp_general_answer_request("Qual o estoque atual do SKU 001?", _full_session()) is False
    assert whatsapp_bridge._whatsapp_general_answer_request("Mostre minhas vendas de hoje", _full_session()) is False
    assert whatsapp_bridge._whatsapp_general_answer_request("Execute a funcao interna solicitada", _full_session()) is False
    assert whatsapp_bridge._whatsapp_general_answer_request("Consulte a loja JK Pecas", _full_session()) is False


def test_general_question_answers_directly_without_progress_confirmation(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_session_stores", lambda _session: ["JK Pecas"])
    monkeypatch.setattr(
        whatsapp_bridge,
        "_try_create_action_pending",
        lambda *_args, **_kwargs: pytest.fail("general question must not enter the action proposal flow"),
    )
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update({"payload": payload, "session": session, "origin": origin, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-general-answer", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(
        codex_console,
        "codex_aprovar_tarefa_para_sessao",
        lambda *_args, **_kwargs: pytest.fail("general question must not request or receive approval"),
    )
    saved = []
    sent = []
    monkeypatch.setattr(
        whatsapp_bridge,
        "_save_pending",
        lambda _state, message_id, pending: saved.append((message_id, pending.copy())),
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_message_result",
        lambda _cfg, message_id, payload: sent.append((message_id, payload)) or {"success": True},
    )
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.general-answer",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Corrija esta frase: nos vai amanha.",
        },
    )

    assert created["payload"].sandbox == "read_only"
    assert created["payload"].approval_mode == "read_only"
    assert "Responda diretamente" in created["payload"].prompt
    assert "nao diga que vai fazer depois" in created["payload"].prompt
    assert created["metadata"]["general_answer"] is True
    assert saved[0][1]["general_answer"] is True
    assert sent == []


def test_post_sale_mercado_livre_action_keeps_normal_approval_flow(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_load_store_configs", lambda *_args: [{"nome": "JK Pecas"}])
    monkeypatch.setattr(whatsapp_bridge, "_try_create_action_pending", lambda *_args, **_kwargs: pytest.fail("browser-side proposal flow must not be used"))
    created = []
    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", lambda payload, *_args, **_kwargs: created.append(payload) or {
        "success": True,
        "task": {
            "task_id": "task-post-sale",
            "status": "awaiting_approval",
            "proposal": {"proposal_id": "proposal-post-sale", "channels_allowed": ["app", "whatsapp"]},
        },
    })
    saved = []
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, pending.copy())))
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.post-sale",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Envie esta resposta para a pergunta do Mercado Livre da loja JK Pecas",
        },
    )

    assert len(created) == 1
    assert created[0].request_id == "wamid.post-sale"
    assert saved[0][1]["proposal_id"] == "proposal-post-sale"


def test_question_queue_inquiry_from_bound_number_is_read_only_without_confirmation(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_load_store_configs", lambda *_args: [{"nome": "JK Pecas"}])
    monkeypatch.setattr(
        whatsapp_bridge,
        "_try_create_action_pending",
        lambda *_args, **_kwargs: pytest.fail("read-only inquiry must not enter action confirmation"),
    )
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update({"payload": payload, "session": session, "origin": origin, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-question-inquiry", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.question-inquiry",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Tem pergunta para responder na loja JK Pecas?",
        },
    )

    assert created["payload"].sandbox == "read_only"
    assert created["payload"].approval_mode == "read_only"
    assert created["origin"] == "whatsapp"


def test_bound_full_number_never_auto_approves_fallback_task(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_try_create_action_pending", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        codex_console,
        "codex_criar_tarefa_para_sessao",
        lambda *_args, **_kwargs: {"success": True, "task": {"task_id": "task-write", "status": "awaiting_approval"}},
    )
    approvals = []
    monkeypatch.setattr(
        codex_console,
        "codex_aprovar_tarefa_para_sessao",
        lambda task_id, _session, _payload, **kwargs: approvals.append((task_id, kwargs)) or {
            "success": True,
            "task": {"task_id": task_id, "status": "queued"},
        },
    )
    saved = []
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, pending.copy())))
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda _cfg, message_id, payload: sent.append((message_id, payload)) or {"success": True})
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.write",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Execute a funcao interna solicitada",
        },
    )

    assert approvals == []
    assert saved[0][1]["trusted_bound_number"] is True
    assert saved[0][1]["awaiting_notified"] is False
    assert sent == []


def test_exact_store_match_prefers_longest_specific_name():
    assert whatsapp_bridge._whatsapp_exact_store_matches(
        "Consulte os anuncios da loja JK Pecas",
        ["JK", "JK Pecas", "Deckas"],
    ) == ["JK Pecas"]
    assert whatsapp_bridge._whatsapp_exact_store_matches(
        "Compare as lojas JK Pecas e Deckas",
        ["JK", "JK Pecas", "Deckas"],
    ) == ["JK Pecas", "Deckas"]


def test_generic_stock_query_requires_store_or_keeps_all_stores_separate(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [{"nome": "JK Pecas"}, {"nome": "Deckas"}],
    )
    missing = whatsapp_bridge._whatsapp_store_scope_policy("Qual o saldo do SKU 001?", _full_session())
    assert missing["store_required"] is True
    assert missing["store_mode"] == "single"
    assert missing["store_matches"] == []
    assert missing["authorized_stores"] == ["JK Pecas", "Deckas"]

    all_stores = whatsapp_bridge._whatsapp_store_scope_policy(
        "Qual o saldo do SKU 001 em todas as lojas, separado por loja?",
        _full_session(),
    )
    assert all_stores["store_mode"] == "all"
    assert all_stores["stores"] == ["JK Pecas", "Deckas"]


def test_whatsapp_source_policy_enforces_bling_ml_and_full_routes(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [{
            "nome": "JK Pecas",
            "integracoes": {
                "bling": {"access_token": "bling-token"},
                "mercadolivre": {"access_token": "ml-token"},
            },
        }],
    )
    session = _full_session()

    stock = whatsapp_bridge._whatsapp_query_policy("Qual o estoque atual do SKU 001 na JK Pecas?", session)
    assert stock["domains"] == ["estoque"]
    assert stock["providers"] == ["bling"]
    assert stock["source_policy"]["required_tools"] == ["bling_stock_balances"]
    assert stock["bypass_cache"] is True

    sales = whatsapp_bridge._whatsapp_query_policy("Mostre pedidos e vendas da JK Pecas", session)
    assert sales["providers"] == ["mercado_livre"]
    assert sales["source_policy"]["required_tools"] == ["mercado_livre_orders"]

    listing = whatsapp_bridge._whatsapp_query_policy("Qual a descricao do anuncio MLB123456789 da JK Pecas?", session)
    assert listing["source_policy"]["required_tools"] == ["mercado_livre_listing"]
    assert listing["source_policy"]["include_listing_details"] is True

    full = whatsapp_bridge._whatsapp_query_policy("Some o estoque Full dos SKUs na JK Pecas", session)
    assert full["providers"] == ["mercado_livre"]
    assert full["source_policy"]["required_tools"] == ["mercado_livre_full_stock"]
    assert "bling_stock_balances" in full["source_policy"]["forbidden_tools"]

    combined = whatsapp_bridge._whatsapp_query_policy("Some o estoque da loja + Full na JK Pecas", session)
    assert combined["providers"] == ["bling", "mercado_livre"]
    assert combined["source_policy"]["required_tools"] == ["bling_stock_balances", "mercado_livre_full_stock"]


def test_implicit_latest_sku_on_ml_does_not_inherit_previous_stock_route(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [{
            "nome": "JK Pecas",
            "integracoes": {
                "bling": {"access_token": "bling-token"},
                "mercadolivre": {"access_token": "ml-token"},
            },
        }],
    )
    message = "E a ultima do SKU 200 na JK Pecas. Busque pelo ML"
    direct = whatsapp_bridge._whatsapp_query_policy(message, _full_session())
    state = {
        "query_contexts": {
            "conversation": {
                "domains": ["estoque"],
                "providers": ["bling"],
                "store": "JK Pecas",
                "store_mode": "single",
                "source_policy": {
                    "intent": "current_store_stock",
                    "required_tools": ["bling_stock_balances"],
                },
                "base_request": "Saldo dele pelo Mercado Livre",
                "updated_at": __import__("time").time(),
            }
        }
    }
    inherited = whatsapp_bridge._whatsapp_inherit_query_store_context(
        message,
        direct,
        state,
        "conversation",
        _full_session(),
    )

    assert direct["domains"] == ["vendas", "anuncios_ml"]
    assert direct["providers"] == ["mercado_livre"]
    assert direct["source_policy"]["required_tools"] == ["mercado_livre_orders"]
    assert direct["base_request"] == message
    assert inherited == {}


def test_daily_report_always_routes_to_complete_mercado_livre_orders(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [{
            "nome": "JK Pecas",
            "integracoes": {"mercadolivre": {"access_token": "ml-token"}},
        }],
    )

    policy = whatsapp_bridge._whatsapp_query_policy("Relatorio do dia da JK Pecas", _full_session())

    assert policy["domains"] == ["vendas"]
    assert policy["providers"] == ["mercado_livre"]
    assert policy["source_policy"]["required_tools"] == ["mercado_livre_orders"]
    assert policy["report_mode"] is True
    assert policy["limit"] == 20000
    assert policy["store"] == "JK Pecas"

    sanitized = codex_console._codex_whatsapp_query_policy(
        "whatsapp",
        {"query_policy": policy},
    )
    assert sanitized["report_mode"] is True
    assert sanitized["limit"] == 20000


@pytest.mark.parametrize(
    "message,has_month_period",
    [
        ("Faça a análise desse mês na mesma loja", True),
        ("deste mês", True),
        ("mesma loja", False),
    ],
)
def test_contextual_sales_report_inherits_recent_store_and_forces_complete_fresh_query(
    monkeypatch,
    message,
    has_month_period,
):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_authorized_api_stores",
        lambda *_args, **_kwargs: ["Uai Mineirinho"],
    )
    previous = {
        "domains": ["vendas"],
        "store_required": True,
        "store": "Uai Mineirinho",
        "store_mode": "single",
        "providers": ["mercado_livre"],
        "source_policy": {
            "required_tools": ["mercado_livre_orders"],
            "preferred_providers": ["mercado_livre"],
            "force_refresh": False,
        },
        "base_request": "Relatorio de vendas de ontem da Uai Mineirinho",
        "report_mode": True,
        "limit": 20,
        "updated_at": __import__("time").time(),
    }

    policy = whatsapp_bridge._whatsapp_query_continuation_policy(
        message,
        {"query_contexts": {"wa:conversation": previous}},
        "wa:conversation",
        _full_session(),
    )

    assert policy["store"] == "Uai Mineirinho"
    assert policy["store_matches"] == ["Uai Mineirinho"]
    assert policy["domains"] == ["vendas"]
    assert policy["providers"] == ["mercado_livre"]
    assert policy["source_policy"]["required_tools"] == ["mercado_livre_orders"]
    assert policy["source_policy"]["force_refresh"] is True
    assert policy["report_mode"] is True
    assert policy["limit"] == 20000
    assert policy["offset"] == 0
    assert policy["fresh"] is True
    assert policy["bypass_cache"] is True
    if has_month_period:
        start, end = whatsapp_bridge._whatsapp_contextual_report_period(message)
        assert policy["data_inicio"] == start
        assert policy["data_fim"] == end
        assert start.endswith("-01")
        assert f"{start} a {end}" in policy["base_request"]
    else:
        assert "data_inicio" not in policy
        assert policy["base_request"] == previous["base_request"]


def test_contextual_sales_report_does_not_inherit_expired_or_unauthorized_store(monkeypatch):
    previous = {
        "domains": ["vendas"],
        "store_required": True,
        "store": "Uai Mineirinho",
        "providers": ["mercado_livre"],
        "source_policy": {"required_tools": ["mercado_livre_orders"]},
        "report_mode": True,
        "updated_at": __import__("time").time() - (24 * 3600 + 1),
    }
    state = {"query_contexts": {"wa:conversation": previous}}
    assert whatsapp_bridge._whatsapp_query_continuation_policy(
        "deste mês", state, "wa:conversation", _full_session()
    ) == {}

    previous["updated_at"] = __import__("time").time()
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_authorized_api_stores", lambda *_args, **_kwargs: [])
    policy = whatsapp_bridge._whatsapp_query_continuation_policy(
        "deste mês", state, "wa:conversation", _full_session()
    )
    assert policy["continuation_invalid_store"] is True
    assert policy["store"] == ""


def test_specific_followup_inherits_recent_store_from_same_phone_conversation(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_authorized_api_stores",
        lambda *_args, **_kwargs: ["JK Pecas", "Deckas"],
    )
    conversation_id = "wa:phone:5511999999999"
    previous = {
        "domains": ["vendas"],
        "store_required": True,
        "store": "JK Pecas",
        "store_mode": "single",
        "providers": ["mercado_livre"],
        "source_policy": {"required_tools": ["mercado_livre_returns"]},
        "base_request": "Mostre as cinco devolucoes mais recentes da JK Pecas",
        "report_mode": False,
        "limit": 5,
        "updated_at": __import__("time").time(),
    }
    message = "agora me informe o motivo de cada devolução"
    direct = whatsapp_bridge._whatsapp_query_policy(message, _full_session())

    policy = whatsapp_bridge._whatsapp_inherit_query_store_context(
        message,
        direct,
        {"query_contexts": {conversation_id: previous}},
        conversation_id,
        _full_session(),
    )

    assert policy["store"] == "JK Pecas"
    assert policy["store_matches"] == ["JK Pecas"]
    assert policy["store_mode"] == "single"
    assert policy["domains"] == ["vendas"]
    assert policy["providers"] == ["mercado_livre"]
    assert policy["source_policy"]["required_tools"] == ["mercado_livre_returns"]
    assert policy["inherited_store_context"] is True
    assert policy["base_request"] == message

    explicit_message = "agora detalhe as devoluções da Deckas"
    explicit = whatsapp_bridge._whatsapp_query_policy(explicit_message, _full_session())
    assert explicit["store"] == "Deckas"
    assert whatsapp_bridge._whatsapp_inherit_query_store_context(
        explicit_message,
        explicit,
        {"query_contexts": {conversation_id: previous}},
        conversation_id,
        _full_session(),
    ) == {}


def test_generic_more_details_inherits_domains_and_store_but_mutation_does_not(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_authorized_api_stores",
        lambda *_args, **_kwargs: ["JK Pecas"],
    )
    conversation_id = "wa:phone:5511999999999"
    previous = {
        "domains": ["vendas"],
        "store_required": True,
        "store": "JK Pecas",
        "store_mode": "single",
        "providers": ["mercado_livre"],
        "source_policy": {"required_tools": ["mercado_livre_orders"]},
        "limit": 20,
        "updated_at": __import__("time").time(),
    }
    state = {"query_contexts": {conversation_id: previous}}

    inherited = whatsapp_bridge._whatsapp_inherit_query_store_context(
        "me dê mais detalhes",
        {},
        state,
        conversation_id,
        _full_session(),
    )
    mutation = whatsapp_bridge._whatsapp_inherit_query_store_context(
        "agora altere o preço do anúncio",
        {},
        state,
        conversation_id,
        _full_session(),
    )

    assert inherited["store"] == "JK Pecas"
    assert inherited["domains"] == ["vendas"]
    assert inherited["source_policy"]["required_tools"] == ["mercado_livre_orders"]
    assert mutation == {}


def test_implicit_store_context_expires_and_rejects_unauthorized_previous_store(monkeypatch):
    conversation_id = "wa:phone:5511999999999"
    previous = {
        "domains": ["vendas"],
        "store_required": True,
        "store": "JK Pecas",
        "store_mode": "single",
        "providers": ["mercado_livre"],
        "updated_at": __import__("time").time() - whatsapp_bridge.WHATSAPP_QUERY_CONTEXT_TTL_SECONDS - 1,
    }
    state = {"query_contexts": {conversation_id: previous}}
    direct = {"mode": "query_only", "domains": ["vendas"], "store_required": True, "providers": ["mercado_livre"]}
    assert whatsapp_bridge._whatsapp_inherit_query_store_context(
        "agora detalhe as devoluções", direct, state, conversation_id, _full_session()
    ) == {}

    previous["updated_at"] = __import__("time").time()
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_authorized_api_stores", lambda *_args, **_kwargs: ["Deckas"])
    invalid = whatsapp_bridge._whatsapp_inherit_query_store_context(
        "agora detalhe as devoluções", direct, state, conversation_id, _full_session()
    )
    assert invalid["store"] == ""
    assert invalid["store_matches"] == []
    assert invalid["continuation_invalid_store"] is True


def test_daily_ml_report_lists_quantity_unit_value_and_total_for_every_sku():
    policy = {
        "store": "JK Pecas",
        "report_mode": True,
        "source_policy": {"required_tools": ["mercado_livre_orders"]},
    }
    tool_results = [{
        "success": True,
        "tool_id": "mercado_livre_orders",
        "all_rows": [
            {"sku": "422", "title": "Enxada Rotativa", "quantity": 2, "gross_amount": 120.0},
            {"sku": "190", "title": "Reservatorio", "quantity": 1, "gross_amount": 50.9},
            {"sku": "", "item_id": "MLB123", "title": "Item sem seller SKU", "quantity": 3, "gross_amount": 75.0},
        ],
        "summary": [{
            "tool_id": "mercado_livre_orders",
            "loja": "JK Pecas",
            "periodo": {"data_inicio": "2026-07-13", "data_fim": "2026-07-13"},
            "summary": {
                "api_consulted": True,
                "store": "JK Pecas",
                "period": {"requested": {"from": "2026-07-13T00:00:00-03:00", "to": "2026-07-13T23:59:59-03:00"}},
                "totals": {
                    "orders": 6,
                    "items_quantity": 6,
                    "gross_amount": 245.9,
                    "paid_amount": 245.9,
                    "refund_amount": 0,
                    "net_amount": 245.9,
                },
                "paging": {"returned": 6, "scanned": 8, "pages_fetched": 2, "has_more": False},
                "coverage_complete": True,
                "truncated": False,
            },
        }],
    }]

    report = whatsapp_bridge._whatsapp_daily_ml_sales_report(
        tool_results,
        policy,
        "Relatorio do dia da JK Pecas",
    )
    parts = whatsapp_bridge._whatsapp_response_parts(report, "📊 BLACK JOHN — RELATÓRIO")
    joined = "\n\n".join(parts)

    assert "SKU 422" in joined
    assert "SKU 190" in joined
    assert "SKU MLB123" in joined
    assert "Qtd. 2" in joined
    assert "Unit. R$ 60,00" in joined
    assert "Total R$ 120,00" in joined
    assert "3 SKU(s) consolidado(s)" in joined
    assert "2 página(s)" in joined
    assert "8 pedido(s) verificado(s)" in joined
    assert "6 pedido(s) considerado(s)" in joined
    assert "Consulta direta à API do Mercado Livre" in joined


def test_codex_whatsapp_complete_ml_report_preserves_every_sku_and_paging():
    task = {
        "origin": "whatsapp",
        "query_policy": {
            "store": "Uai Mineirinho",
            "store_mode": "single",
            "report_mode": True,
            "source_policy": {"required_tools": ["mercado_livre_orders"]},
        },
    }
    rows = [
        {
            "sku": f"SKU-{index:03d}",
            "title": f"Produto {index}",
            "quantity": index,
            "gross_amount": index * 10.5,
        }
        for index in range(1, 49)
    ]
    total_value = sum(index * 10.5 for index in range(1, 49))
    results = [{
        "success": True,
        "tool_id": "mercado_livre_orders",
        "all_rows": rows,
        "summary": [{
            "tool_id": "mercado_livre_orders",
            "loja": "Uai Mineirinho",
            "periodo": {"data_inicio": "2026-07-01", "data_fim": "2026-07-13"},
            "summary": {
                "api_consulted": True,
                "store": "Uai Mineirinho",
                "period": {"requested": {"from": "2026-07-01", "to": "2026-07-13"}},
                "totals": {
                    "orders": 206,
                    "items_quantity": sum(range(1, 49)),
                    "gross_amount": total_value,
                    "paid_amount": total_value,
                    "refund_amount": 0,
                    "net_amount": total_value,
                },
                "paging": {
                    "pages_fetched": 5,
                    "scanned": 206,
                    "returned": 206,
                    "has_more": False,
                },
                "coverage_complete": True,
                "truncated": False,
            },
        }],
    }]

    report = codex_console._codex_whatsapp_complete_ml_report(task, results)
    parts = whatsapp_bridge._whatsapp_response_parts(report, "BLACK JHON — RELATÓRIO")
    joined = "\n\n".join(parts)

    assert "SKU-001" in joined
    assert "SKU-048" in joined
    assert "Qtd. 48" in joined
    assert "Total R$ 504,00" in joined
    assert "5 página(s)" in joined
    assert "206 pedido(s) verificado(s)" in joined
    assert "206 pedido(s) considerado(s)" in joined
    assert "48 SKU(s) consolidado(s)" in joined
    assert "Cobertura completa" in joined
    sku_counts = [part.count("*SKU ") for part in parts]
    assert sum(sku_counts) == 48
    assert max(sku_counts) <= 8
    assert "..." not in joined
    assert len(parts) <= whatsapp_bridge.WHATSAPP_REPORT_MAX_PARTS


def test_daily_ml_source_execution_requests_report_mode_and_full_day_coverage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **kwargs: calls.append(kwargs) or {
            "success": True,
            "tool_id": kwargs["tool_id"],
            "records": 1,
        },
    )
    task = {
        "client_id": "cliente",
        "created_by": "admin",
        "permissions": {"full": True},
        "prompt": "Relatorio do dia da JK Pecas",
        "screen_context": {},
    }
    policy = {
        "store": "JK Pecas",
        "base_request": "Relatorio do dia da JK Pecas",
        "report_mode": True,
        "limit": 20000,
        "source_policy": codex_assistant._assistant_source_routing_policy("Relatorio do dia da JK Pecas"),
    }

    whatsapp_bridge._whatsapp_execute_source_policy_tools(task, policy)

    assert [call["tool_id"] for call in calls] == ["mercado_livre_orders"]
    assert calls[0]["args"]["mode"] == "report"
    assert calls[0]["args"]["limite"] == 20000
    assert calls[0]["args"]["max_paginas"] == 400
    assert calls[0]["args"]["force_refresh"] is True


def test_contextual_month_report_forwards_explicit_period_and_bypasses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **kwargs: calls.append(kwargs) or {"success": True, "tool_id": kwargs["tool_id"]},
    )
    task = {
        "client_id": "cliente",
        "created_by": "admin",
        "permissions": {"full": True},
        "prompt": "Faça a análise desse mês na mesma loja",
        "screen_context": {},
    }
    policy = {
        "store": "Uai Mineirinho",
        "base_request": "Relatorio completo de vendas pelo Mercado Livre da loja Uai Mineirinho, no periodo de 2026-07-01 a 2026-07-13.",
        "data_inicio": "2026-07-01",
        "data_fim": "2026-07-13",
        "report_mode": True,
        "limit": 20000,
        "bypass_cache": True,
        "source_policy": {
            "required_tools": ["mercado_livre_orders"],
            "force_refresh": False,
        },
    }

    whatsapp_bridge._whatsapp_execute_source_policy_tools(task, policy)

    assert len(calls) == 1
    assert calls[0]["args"]["data_inicio"] == "2026-07-01"
    assert calls[0]["args"]["data_fim"] == "2026-07-13"
    assert calls[0]["args"]["max_paginas"] == 400
    assert calls[0]["args"]["force_refresh"] is True


def test_codex_whatsapp_policy_blocks_bling_for_full_stock():
    raw = {
        "mode": "query_only",
        "domains": ["estoque", "mercado_full"],
        "source_policy": codex_assistant._assistant_source_routing_policy("estoque Full do SKU 001"),
    }
    policy = codex_console._codex_whatsapp_query_policy("whatsapp", {"query_policy": raw})
    source_policy = policy["source_policy"]
    assert source_policy["required_tools"] == ["mercado_livre_full_stock"]
    assert codex_console._codex_agent_source_policy_error("bling_stock_balances", source_policy, [])
    assert codex_console._codex_agent_source_policy_error("stock_data", source_policy, [])
    assert codex_console._codex_agent_source_policy_error("mercado_livre_full_stock", source_policy, []) == ""


def test_provider_worker_source_policy_executes_only_required_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **kwargs: calls.append(kwargs) or {"success": True, "tool_id": kwargs["tool_id"], "records": 1},
    )
    task = {
        "client_id": "cliente",
        "created_by": "admin",
        "permissions": {"full": True},
        "prompt": "Some o estoque da loja + Full",
        "screen_context": {},
    }
    policy = {
        "store": "JK Pecas",
        "base_request": "Some o estoque da loja + Full",
        "source_policy": codex_assistant._assistant_source_routing_policy("Some o estoque da loja + Full"),
    }
    results = whatsapp_bridge._whatsapp_execute_source_policy_tools(task, policy)

    assert [call["tool_id"] for call in calls] == ["bling_stock_balances", "mercado_livre_full_stock"]
    assert all(call["args"]["force_refresh"] is True for call in calls)
    assert len(results) == 2


def test_store_selection_click_restores_original_request_and_selected_store(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [
            {"nome": "JK Pecas", "integracoes": {"bling": {"access_token": "bling-jk"}}},
            {"nome": "Deckas", "integracoes": {"bling": {"access_token": "bling-deckas"}}},
        ],
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)
    created = {}

    def fake_create(payload, _session, *, origin, channel_metadata):
        created.update({"payload": payload, "origin": origin, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-selected-store", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    config = {"machine_id": "machine-1", "subject_id": "subject-1", "client_id": "cliente", "username": "admin"}
    message = {
        "message_id": "wamid.store-click",
        "machine_id": "machine-1",
        "subject_id": "subject-1",
        "text_body": "store_select:ABCDEFGH",
        "message_type": "interactive",
    }
    conversation_id = whatsapp_bridge._conversation_id(config, message)
    state = {
        "store_selection_tokens": {
            "ABCDEFGH": {
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "admin",
                "conversation_id": conversation_id,
                "request_text": "Qual o saldo do SKU 001?",
                "store": "Deckas",
                "created_at": __import__("time").time(),
                "used": False,
            }
        }
    }

    whatsapp_bridge._process_message(config, state, message)

    policy = created["metadata"]["query_policy"]
    assert policy["mode"] == "query_only"
    assert policy["store"] == "Deckas"
    assert policy["source_policy"]["required_tools"] == ["bling_stock_balances"]
    assert "Qual o saldo do SKU 001?" in created["payload"].prompt
    assert "Loja selecionada: Deckas" in created["payload"].prompt
    assert state["store_selection_tokens"]["ABCDEFGH"]["used"] is True


def test_bling_store_prevalidation_requires_integration_permission(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [
            {"nome": "Uai Mineirinho", "integracoes": {"bling": {"access_token": "token"}}}
        ],
    )

    assert whatsapp_bridge._whatsapp_authorized_api_stores(
        "tenant",
        {"vendas": True, "integracao": False},
        ["vendas"],
        ["bling"],
    ) == []
    assert whatsapp_bridge._whatsapp_authorized_api_stores(
        "tenant",
        {"vendas": True, "integracao": True},
        ["vendas"],
        ["bling"],
    ) == ["Uai Mineirinho"]


def test_api_query_without_store_sends_selectable_authorized_options_without_creating_task(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas", "Deckas"])
    interactive = []
    completed = []
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_interactive_store_selection",
        lambda _config, **kwargs: interactive.append(kwargs) or {"success": True, "status": "sent"},
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_message_result",
        lambda _config, message_id, payload: completed.append((message_id, payload)) or {"success": True},
    )
    monkeypatch.setattr(
        codex_console,
        "codex_criar_tarefa_para_sessao",
        lambda *_args, **_kwargs: pytest.fail("task/API call must not be created without an exact store"),
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.no-store",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Consulte os anuncios ativos via API",
        },
    )

    assert len(interactive) == 1
    assert [item["title"] for item in interactive[0]["options"]] == ["Todas as lojas", "JK Pecas", "Deckas"]
    assert interactive[0]["options"][0]["description"] == "Consultar cada loja separadamente"
    assert all(item["id"].startswith("store_select:") for item in interactive[0]["options"])
    assert completed == [("wamid.no-store", {"status": "completed", "response_parts": []})]


def test_process_followup_reuses_recent_store_without_sending_selector(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas", "Deckas"])
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_interactive_store_selection",
        lambda *_args, **_kwargs: pytest.fail("a continuacao nao deve perguntar a loja novamente"),
    )
    created = {}

    def fake_create(payload, _session, *, origin, channel_metadata):
        created.update({"payload": payload, "origin": origin, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-followup-store", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    config = {"machine_id": "machine-1", "subject_id": "subject-1", "client_id": "cliente", "username": "admin"}
    message = {
        "message_id": "wamid.followup-store",
        "machine_id": "machine-1",
        "subject_id": "subject-1",
        "wa_id": "5511999999999",
        "text_body": "agora me informe o motivo de cada devolução",
    }
    conversation_id = whatsapp_bridge._conversation_id(config, message)
    state = {
        "query_contexts": {
            conversation_id: {
                "domains": ["vendas"],
                "store_required": True,
                "store": "JK Pecas",
                "store_mode": "single",
                "providers": ["mercado_livre"],
                "source_policy": {"required_tools": ["mercado_livre_returns"]},
                "base_request": "Mostre as cinco devolucoes mais recentes da JK Pecas",
                "limit": 5,
                "updated_at": __import__("time").time(),
            }
        }
    }

    whatsapp_bridge._process_message(config, state, message)

    policy = created["metadata"]["query_policy"]
    assert created["origin"] == "whatsapp"
    assert policy["store"] == "JK Pecas"
    assert policy["store_matches"] == ["JK Pecas"]
    assert policy["inherited_store_context"] is True
    assert policy["source_policy"]["required_tools"] == ["mercado_livre_returns"]
    assert "agora me informe o motivo de cada devolução" in created["payload"].prompt
    assert state["query_contexts"][conversation_id]["store"] == "JK Pecas"


def test_all_stores_selection_restores_request_with_separate_store_scope(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_reload_bound_session", lambda *_args: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_whatsapp_load_store_configs",
        lambda _client_id: [
            {"nome": "JK Pecas", "integracoes": {"mercadolivre": {"access_token": "ml-jk"}}},
            {"nome": "Deckas", "integracoes": {"mercadolivre": {"access_token": "ml-deckas"}}},
        ],
    )
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)
    created = {}

    def fake_create(payload, _session, *, origin, channel_metadata):
        created.update({"payload": payload, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-all-selected", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    config = {"machine_id": "machine-1", "subject_id": "subject-1", "client_id": "cliente", "username": "admin"}
    message = {
        "message_id": "wamid.store-all",
        "machine_id": "machine-1",
        "subject_id": "subject-1",
        "text_body": "store_select:ALLST234",
        "message_type": "interactive",
    }
    conversation_id = whatsapp_bridge._conversation_id(config, message)
    state = {
        "store_selection_tokens": {
            "ALLST234": {
                "subject_id": "subject-1",
                "client_id": "cliente",
                "username": "admin",
                "conversation_id": conversation_id,
                "request_text": "Mostre as vendas de hoje",
                "store": "",
                "stores": ["JK Pecas", "Deckas"],
                "store_mode": "all",
                "created_at": __import__("time").time(),
                "used": False,
            }
        }
    }

    whatsapp_bridge._process_message(config, state, message)

    policy = created["metadata"]["query_policy"]
    assert policy["store_mode"] == "all"
    assert policy["stores"] == ["JK Pecas", "Deckas"]
    assert policy["store"] == ""
    assert "todas as lojas" in created["payload"].prompt.lower()
    assert "Nao some nem misture os totais" in created["payload"].prompt

    sanitized = codex_console._codex_whatsapp_query_policy("whatsapp", created["metadata"])
    assert sanitized["store_mode"] == "all"
    assert sanitized["stores"] == ["JK Pecas", "Deckas"]


def test_mutable_store_selection_does_not_offer_all_stores(monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_interactive_store_selection",
        lambda _config, **kwargs: sent.append(kwargs) or {"success": True, "status": "sent"},
    )
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: {"success": True})

    assert whatsapp_bridge._whatsapp_send_store_selection(
        {"machine_id": "machine-1"},
        {},
        {"message_id": "wamid.mutation-store", "subject_id": "subject-1"},
        _full_session(),
        "wa:conversation",
        "Altere o cadastro desta loja",
        ["JK Pecas", "Deckas"],
        allow_all=False,
    ) is True
    assert [item["title"] for item in sent[0]["options"]] == ["JK Pecas", "Deckas"]


def test_api_query_comparing_multiple_stores_creates_separate_store_scope(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas", "Deckas"])
    created = {}

    def fake_create(payload, _session, *, origin, channel_metadata):
        created.update({"payload": payload, "origin": origin, "metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-all-stores", "status": "queued"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.ambiguous-store",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Compare os anuncios via API das lojas JK Pecas e Deckas",
        },
    )

    policy = created["metadata"]["query_policy"]
    assert policy["store_mode"] == "all"
    assert policy["stores"] == ["JK Pecas", "Deckas"]
    assert "Nunca some nem misture os totais" in created["payload"].prompt


def test_api_query_with_exact_store_creates_read_only_task(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas", "Deckas"])
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update(
            {
                "payload": payload,
                "session": session,
                "origin": origin,
                "channel_metadata": channel_metadata,
            }
        )
        return {"success": True, "task": {"task_id": "task-query-only"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.exact-store",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Consulte os anuncios ativos via API da loja JK Pecas",
        },
    )

    assert created["origin"] == "whatsapp"
    assert created["payload"].sandbox == "read_only"
    assert created["payload"].approval_mode == "read_only"
    assert created["payload"].model == "gpt-5.5"
    assert created["payload"].reasoning_effort == "medium"
    assert created["channel_metadata"]["reasoning_policy"] == "adaptive"
    assert created["channel_metadata"]["reasoning_max"] == "xhigh"
    policy = created["channel_metadata"]["query_policy"]
    assert policy["mode"] == "query_only"
    assert policy["domains"] == ["anuncios_ml"]
    assert policy["store"] == "JK Pecas"
    assert created["payload"].screen_context["selection"]["query_policy"]["store"] == "JK Pecas"


def test_fresh_sales_api_query_bypasses_mutation_flow_and_marks_no_cache(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas"])
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update({"payload": payload, "origin": origin, "channel_metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-fresh-sales"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(whatsapp_bridge, "_try_create_action_pending", lambda *_args, **_kwargs: pytest.fail("fresh API query is not a mutation"))
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        {},
        {
            "message_id": "wamid.fresh-sales",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Atualize agora as vendas via API da loja JK Pecas",
        },
    )

    policy = created["channel_metadata"]["query_policy"]
    assert policy["mode"] == "query_only"
    assert policy["domains"] == ["vendas"]
    assert policy["store"] == "JK Pecas"
    assert policy["fresh"] is True
    assert policy["bypass_cache"] is True
    assert created["payload"].sandbox == "read_only"
    assert "ignore resultado em cache" in created["payload"].prompt


def test_isolated_next_inherits_query_store_filters_and_advances_offset(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas"])
    created = {}

    def fake_create(payload, session, *, origin, channel_metadata):
        created.update({"payload": payload, "origin": origin, "channel_metadata": channel_metadata})
        return {"success": True, "task": {"task_id": "task-next-page"}}

    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", fake_create)
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)
    config = {"machine_id": "machine-1", "subject_id": "subject-1", "client_id": "cliente", "username": "admin"}
    message = {
        "message_id": "wamid.next",
        "machine_id": "machine-1",
        "subject_id": "subject-1",
        "text_body": "Proximos",
    }
    conversation_id = whatsapp_bridge._conversation_id(config, message)
    state = {
        "query_contexts": {
            conversation_id: {
                "domains": ["anuncios_ml"],
                "store_required": True,
                "store": "JK Pecas",
                "base_request": "Liste anuncios ativos da loja JK Pecas com status active",
                "offset": 20,
                "limit": 20,
                "updated_at": __import__("time").time(),
            }
        }
    }

    whatsapp_bridge._process_message(config, state, message)

    policy = created["channel_metadata"]["query_policy"]
    assert policy["mode"] == "query_only"
    assert policy["domains"] == ["anuncios_ml"]
    assert policy["store"] == "JK Pecas"
    assert policy["inherited"] is True
    assert policy["offset"] == 40
    assert policy["limit"] == 20
    assert "status active" in policy["base_request"]
    assert "Use offset 40 e limite 20" in created["payload"].prompt
    assert state["query_contexts"][conversation_id]["offset"] == 40


def test_next_uses_exact_provider_offset_returned_by_last_task(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_whatsapp_authorized_api_stores", lambda *_args: ["JK Pecas"])
    conversation_id = "wa-tenant-admin-subject"
    state = {
        "query_contexts": {
            conversation_id: {
                "domains": ["anuncios_ml"],
                "providers": ["mercado_livre"],
                "store_required": True,
                "store": "JK Pecas",
                "base_request": "Liste anuncios ativos da loja JK Pecas",
                "offset": 0,
                "limit": 20,
                "updated_at": __import__("time").time(),
            }
        }
    }
    whatsapp_bridge._whatsapp_update_query_context_from_task(
        state,
        {"conversation_id": conversation_id},
        {
            "tool_results_summary": [
                {
                    "tool_id": "mercado_livre_listing",
                    "paging": {"next_offset": 37, "has_more": True},
                }
            ]
        },
    )

    policy = whatsapp_bridge._whatsapp_query_continuation_policy(
        "Proximos",
        state,
        conversation_id,
        _full_session(),
    )

    assert policy["offset"] == 37
    assert policy["provider_offsets"] == {"mercado_livre_listing": 37}


def test_isolated_next_without_context_asks_to_repeat_without_task(monkeypatch):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas"])
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _config, _message_id, text, title: sent.append((title, text)))
    monkeypatch.setattr(
        codex_console,
        "codex_criar_tarefa_para_sessao",
        lambda *_args, **_kwargs: pytest.fail("next without context must not create a task"),
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1", "client_id": "cliente", "username": "admin"},
        {},
        {
            "message_id": "wamid.next-no-context",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": "Proximos",
        },
    )

    assert len(sent) == 1
    assert "REPITA A CONSULTA" in sent[0][0]
    assert "consulta anterior" in sent[0][1]


@pytest.mark.parametrize(
    "message_text",
    [
        "Sincronize as vendas da loja JK Pecas",
        "Pause o anuncio MLB1234567890 da loja JK Pecas",
    ],
)
def test_full_whatsapp_mutation_reaches_canonical_agent_task(monkeypatch, message_text):
    _configure_whatsapp_process_test(monkeypatch, ["JK Pecas"])
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _config, _message_id, text, title: sent.append((title, text)))
    created = []
    monkeypatch.setattr(codex_console, "codex_criar_tarefa_para_sessao", lambda payload, *_args, **_kwargs: created.append(payload) or {
        "success": True,
        "task": {"task_id": "task-canonical", "status": "queued"},
    })
    saved = []
    monkeypatch.setattr(whatsapp_bridge, "_save_pending", lambda _state, message_id, pending: saved.append((message_id, pending.copy())))
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: None)

    state = {}
    whatsapp_bridge._process_message(
        {"machine_id": "machine-1", "subject_id": "subject-1"},
        state,
        {
            "message_id": "wamid.blocked-write",
            "machine_id": "machine-1",
            "subject_id": "subject-1",
            "text_body": message_text,
        },
    )

    assert len(created) == 1
    assert created[0].request_id == "wamid.blocked-write"
    assert saved[0][1]["task_id"] == "task-canonical"
    assert sent == []


def test_status_lists_numbers_grouped_by_user_with_three_number_limit(monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_whisper_status", lambda: {"ready": True})
    monkeypatch.setattr(codex_console, "_codex_status_payload", lambda: {"ready": True, "enabled": True})
    monkeypatch.setattr(whatsapp_bridge, "_load_state", lambda: {})
    worker = {
        "success": True,
        "binding_limit_per_user": 3,
        "bindings": [
            {"subject_id": "one", "phone_number": "5537999991111", "phone_suffix": "1111", "client_id": "cliente", "username": "admin", "machine_id": "m1"},
            {"subject_id": "two", "phone_suffix": "2222", "client_id": "cliente", "username": "admin", "machine_id": "m2"},
            {"subject_id": "other", "phone_suffix": "9999", "client_id": "outro", "username": "admin", "machine_id": "m1"},
        ],
    }
    status = whatsapp_bridge._public_status(
        {
            "client_id": "cliente",
            "username": "admin",
            "machine_id": "m1",
            "worker_url": "https://example.workers.dev",
            "bridge_token": "token",
            "ai_model": "codex:gpt-5.6-sol",
            "codex_reasoning_effort": "high",
            "phone_notification_settings": {
                "one": {
                    "label": "Gerência",
                    "send_ml_question_suggestions": False,
                    "send_weekly_report": True,
                    "send_monthly_report": True,
                    "ai_behavior": "Seja direto e use linguagem simples.",
                }
            },
        },
        worker,
    )
    assert status["paired"] is True
    assert status["binding_limit"] == 3
    assert status["binding_slots_remaining"] == 1
    assert [item["subject_id"] for item in status["personal_numbers"]] == ["one", "two", "other"]
    assert status["personal_numbers"][0]["username"] == "admin"
    assert status["personal_numbers"][0]["client_id"] == "cliente"
    assert status["personal_numbers"][0]["this_machine"] is True
    assert status["personal_numbers"][0]["phone_number"] == "5537999991111"
    assert status["personal_numbers"][1]["this_machine"] is False
    assert status["personal_numbers"][0]["notification_settings"]["label"] == "Gerência"
    assert status["personal_numbers"][0]["notification_settings"]["send_ml_question_suggestions"] is False
    assert status["personal_numbers"][0]["notification_settings"]["send_weekly_report"] is True
    assert status["personal_numbers"][0]["notification_settings"]["ai_behavior"] == "Seja direto e use linguagem simples."
    assert status["personal_numbers"][1]["notification_settings"]["send_weekly_report"] is False
    assert status["ai_model"] == "codex:gpt-5.6-sol"
    assert status["ai_provider"] == "codex"
    assert status["codex_reasoning_effort"] == "high"
    assert status["codex_reasoning_options"] == ["low", "medium", "high", "xhigh"]


def test_binding_target_allows_admin_to_select_an_existing_user(monkeypatch):
    checked = {}
    monkeypatch.setattr(
        admin_usuarios_common,
        "_carregar_permissoes_usuario",
        lambda username, client_id: checked.update({"username": username, "client_id": client_id}) or {"full": False},
    )
    target = whatsapp_bridge._binding_target(_full_session(), "vendas", "cliente-vendas")
    assert target == ("vendas", "cliente-vendas")
    assert checked == {"username": "vendas", "client_id": "cliente-vendas"}


def test_admin_saves_independent_settings_for_a_linked_phone(monkeypatch):
    stored = {}
    state = {}
    config = {
        "machine_id": "machine-1",
        "phone_notification_settings": {},
    }
    monkeypatch.setattr(whatsapp_bridge, "_require_full", lambda *_args, **_kwargs: _full_session())
    monkeypatch.setattr(whatsapp_bridge, "_load_config", lambda: config)
    monkeypatch.setattr(whatsapp_bridge, "_worker_health", lambda _cfg: {"bindings": [{
        "subject_id": "subject-1",
        "username": "operador",
        "client_id": "cliente",
        "machine_id": "machine-1",
    }]})
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda value: stored.update(value) or value)
    monkeypatch.setattr(whatsapp_bridge, "_load_state", lambda: state)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda value: state.update(value))
    monkeypatch.setattr(whatsapp_bridge, "_public_status", lambda value, _worker=None: {"success": True, "config": value})

    result = whatsapp_bridge.whatsapp_bridge_update_phone_settings(
        whatsapp_bridge.WhatsappPhoneSettingsRequest(
            subject_id="subject-1",
            username="operador",
            client_id="cliente",
            label="  Gerência   comercial  ",
            send_ml_question_suggestions=False,
            send_weekly_report=True,
            send_monthly_report=True,
            ai_behavior="  Seja direto.  \n  Sempre informe a loja consultada.  ",
        ),
        _request(),
        None,
    )

    phone = stored["phone_notification_settings"]["subject-1"]
    assert phone["label"] == "Gerência comercial"
    assert phone["send_ml_question_suggestions"] is False
    assert phone["send_weekly_report"] is True
    assert phone["send_monthly_report"] is True
    assert phone["ai_behavior"] == "Seja direto.\nSempre informe a loja consultada."
    assert state["scheduled_report_deliveries"]["subject-1"]["weekly"]
    assert state["scheduled_report_deliveries"]["subject-1"]["monthly"]
    assert result["success"] is True


def test_admin_registers_phone_directly_without_confirmation_message(monkeypatch):
    stored = {}
    state = {}
    gateway_calls = []
    config = {
        "machine_id": "machine-1",
        "worker_url": "https://example.workers.dev",
        "bridge_token": "token",
        "phone_notification_settings": {},
    }

    def fake_gateway(_config, method, path, payload, timeout):
        gateway_calls.append({"method": method, "path": path, "payload": payload, "timeout": timeout})
        if path == "/bridge/welcome":
            return {"success": True, "status": "sent", "meta_message_id": "wamid.welcome"}
        return {
            "success": True,
            "created": True,
            "subject_id": "5537999993818",
            "binding": {"subject_id": "5537999993818", "phone_number": "5537999993818"},
        }

    monkeypatch.setattr(whatsapp_bridge, "_require_full", lambda *_args, **_kwargs: {**_full_session(), "machine_id": "machine-1"})
    monkeypatch.setattr(whatsapp_bridge, "_binding_target", lambda *_args, **_kwargs: ("operador", "cliente"))
    monkeypatch.setattr(whatsapp_bridge, "_load_config", lambda: config)
    monkeypatch.setattr(whatsapp_bridge, "_gateway_json", fake_gateway)
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda value: stored.update(value) or value)
    monkeypatch.setattr(whatsapp_bridge, "_load_state", lambda: state)
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda value: state.update(value))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_worker_health",
        lambda _value: {"success": True, "bindings": [{"subject_id": "5537999993818"}]},
    )
    monkeypatch.setattr(whatsapp_bridge, "_public_status", lambda value, _worker=None: {"success": True, "config": value})

    result = whatsapp_bridge.whatsapp_bridge_register_phone(
        whatsapp_bridge.WhatsappPhoneRegistrationRequest(
            username="operador",
            client_id="cliente",
            name="  Paraiba  ",
            phone_number="(37) 99999-3818",
            send_ml_question_suggestions=True,
            send_weekly_report=True,
            send_monthly_report=False,
            welcome_message="Olá! Seja bem-vindo ao JK Sistema.",
            send_welcome_message=True,
        ),
        _request(),
        None,
    )

    assert gateway_calls[0] == {
        "method": "POST",
        "path": "/bridge/bindings/register",
        "payload": {
            "phone_number": "5537999993818",
            "client_id": "cliente",
            "username": "operador",
            "machine_id": "machine-1",
        },
        "timeout": 15,
    }
    assert "message" not in gateway_calls[0]["payload"]
    assert "confirmation" not in gateway_calls[0]["payload"]
    assert gateway_calls[1] == {
        "method": "POST",
        "path": "/bridge/voice/phones/settings",
        "payload": {
            "subject_id": "5537999993818",
            "machine_id": "machine-1",
            "allow_voice_calls": False,
        },
        "timeout": 15,
    }
    assert gateway_calls[2] == {
        "method": "POST",
        "path": "/bridge/welcome",
        "payload": {
            "subject_id": "5537999993818",
            "machine_id": "machine-1",
            "text": "Olá! Seja bem-vindo ao JK Sistema.",
        },
        "timeout": 20,
    }
    saved = stored["phone_notification_settings"]["5537999993818"]
    assert saved["label"] == "Paraiba"
    assert saved["phone_number"] == "5537999993818"
    assert saved["send_weekly_report"] is True
    assert state["scheduled_report_deliveries"]["5537999993818"]["weekly"]
    assert result["success"] is True
    assert result["phone_number"] == "5537999993818"
    assert result["welcome_message"]["status"] == "sent"


def test_admin_sends_adhoc_message_to_any_normalized_phone(monkeypatch):
    captured = {}
    monkeypatch.setattr(whatsapp_bridge, "_require_full", lambda *_args, **_kwargs: {**_full_session(), "machine_id": "machine-1"})
    monkeypatch.setattr(
        whatsapp_bridge,
        "_load_config",
        lambda: {"worker_url": "https://example.workers.dev", "bridge_token": "token", "machine_id": "machine-1"},
    )

    def fake_gateway(_config, method, path, payload, timeout):
        captured.update({"method": method, "path": path, "payload": payload, "timeout": timeout})
        return {"success": True, "status": "waiting_free_window", "outbox_id": "out-1"}

    monkeypatch.setattr(whatsapp_bridge, "_gateway_json", fake_gateway)
    result = whatsapp_bridge.whatsapp_bridge_send_adhoc_message(
        whatsapp_bridge.WhatsappAdhocMessageRequest(
            phone_number="(37) 99999-3818",
            message="  Olá! Esta é uma mensagem avulsa.  ",
        ),
        _request(),
        None,
    )

    assert captured == {
        "method": "POST",
        "path": "/bridge/messages/send",
        "payload": {
            "phone_number": "5537999993818",
            "machine_id": "machine-1",
            "text": "Olá! Esta é uma mensagem avulsa.",
        },
        "timeout": 20,
    }
    assert result["success"] is True
    assert result["phone_number"] == "5537999993818"
    assert result["delivery"]["status"] == "waiting_free_window"


def test_completed_pairing_activates_bridge_when_health_is_ready(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_worker_health",
        lambda _config: {
            "success": True,
            "zero_cost": {"policy_valid": True},
            "meta": {"configured": True},
            "bindings": [{"machine_id": "machine-1"}],
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_whisper_status", lambda: {"ready": True})
    monkeypatch.setattr(codex_console, "_codex_status_payload", lambda: {"ready": True})
    monkeypatch.setattr(whatsapp_bridge, "_save_config", lambda config: dict(config))
    config, status = whatsapp_bridge._activate_completed_pairing(
        {"enabled": False, "pairing_pending": True, "pairing_expires_at": 4_000_000_000, "machine_id": "machine-1"}
    )
    assert status == "pairing_activated"
    assert config["enabled"] is True
    assert config["pairing_pending"] is False


def test_codex_sdk_compat_maps_new_max_effort_response():
    payload = {
        "reasoningEffort": "max",
        "thread": {"reasoningEffort": "max", "items": [{"reasoningEffort": "high"}]},
    }
    assert codex_console._codex_sdk_response_compat(payload) == {
        "reasoningEffort": "xhigh",
        "thread": {"reasoningEffort": "xhigh", "items": [{"reasoningEffort": "high"}]},
    }


def test_generic_transition_forwarder_skips_whatsapp_tasks(tmp_path, monkeypatch):
    task = {
        "task_id": "wa-task",
        "origin": "whatsapp",
        "client_id": "cliente",
        "created_by": "admin",
        "status": "completed",
        "final_response": "resposta",
    }
    (tmp_path / "wa-task.json").write_text(__import__("json").dumps(task), encoding="utf-8")
    monkeypatch.setattr(codex_console, "_codex_info_dir", lambda: str(tmp_path))
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda *_args, **_kwargs: sent.append(True))
    state = {"task_statuses": {"wa-task": "running"}}
    whatsapp_bridge._forward_task_transitions(
        {"subject_id": "subject", "client_id": "cliente", "username": "admin"},
        state,
    )
    assert sent == []
    assert state["task_statuses"]["wa-task"] == "completed"


def test_generic_transition_forwarder_never_sends_sidebar_tasks_to_whatsapp(tmp_path, monkeypatch):
    task = {
        "task_id": "app-task",
        "origin": "app",
        "client_id": "cliente",
        "created_by": "admin",
        "status": "completed",
        "final_response": "Esta resposta pertence somente ao sidebar.",
        "channel_metadata": {},
    }
    (tmp_path / "app-task.json").write_text(__import__("json").dumps(task), encoding="utf-8")
    monkeypatch.setattr(codex_console, "_codex_info_dir", lambda: str(tmp_path))
    sent = []
    monkeypatch.setattr(whatsapp_bridge, "_post_proactive", lambda *_args, **_kwargs: sent.append(True))
    state = {"task_statuses": {"app-task": "running"}}

    whatsapp_bridge._forward_task_transitions(
        {"subject_id": "subject", "client_id": "cliente", "username": "admin"},
        state,
    )

    assert sent == []
    assert state["task_statuses"]["app-task"] == "completed"


def test_proactive_can_target_the_originating_number(monkeypatch):
    captured = {}

    def fake_gateway(_config, method, path, payload, timeout):
        captured.update({"method": method, "path": path, "payload": payload, "timeout": timeout})
        return {"success": True}

    monkeypatch.setattr(whatsapp_bridge, "_gateway_json", fake_gateway)
    whatsapp_bridge._post_proactive(
        {"subject_id": "latest"},
        {"subject_id": "origin", "fingerprint": "task:1", "text": "feito"},
    )
    assert captured["payload"]["subject_id"] == "origin"
    assert captured["payload"]["fingerprint"] == "task:1"


def test_weekly_whatsapp_report_is_compact_and_has_no_old_raw_alert_dump():
    suggestions = [
        {
            "title": "Risco de ruptura",
            "severity": "critical",
            "detail": "17 SKUs com risco alto ou critico. Principais: 441, 151, 412-8, 507. Fonte: estoque parado do JK Sistema. 2026-07-12T10:57:43",
        },
        {
            "title": "Estoque parado com saldo",
            "severity": "high",
            "detail": "230 SKUs e 8640 unidades paradas. Capital estimado: R$ 340.076,59. Mais urgentes: " + ("SKU 001, " * 200),
        },
    ]

    parts = whatsapp_bridge._whatsapp_weekly_operational_parts(suggestions, "2026-W29")
    joined = "\n\n".join(parts)

    assert 1 <= len(parts) <= 4
    assert sum("📅 *Resumo semanal*" in part for part in parts) == 1
    assert "*Panorama*" in joined
    assert "*Próximo passo*" in joined
    assert "Resumo de alertas operacionais do Joao Pretinho" not in joined
    assert "2026-07-12T10:57:43" not in joined
    assert all(len(part) <= whatsapp_bridge.WHATSAPP_REPORT_BODY_CHARS for part in parts)


def test_scheduled_phone_reports_are_selected_independently(monkeypatch):
    monkeypatch.setattr(
        whatsapp_bridge,
        "_load_state",
        lambda: {"scheduled_report_deliveries": {"subject-1": {"weekly": "2026-W30", "monthly": "2026-07"}}},
    )
    config = {
        "machine_id": "machine-1",
        "phone_notification_settings": {
            "subject-1": {"send_weekly_report": True, "send_monthly_report": True},
            "subject-2": {"send_weekly_report": False, "send_monthly_report": True},
        },
    }
    worker = {"bindings": [
        {"machine_id": "machine-1", "client_id": "cliente", "username": "um", "subject_id": "subject-1"},
        {"machine_id": "machine-1", "client_id": "cliente", "username": "dois", "subject_id": "subject-2"},
        {"machine_id": "outra", "client_id": "cliente", "username": "tres", "subject_id": "subject-3"},
    ]}

    current = whatsapp_bridge.datetime(2026, 8, 3, 9, 0, 0)
    targets = whatsapp_bridge._scheduled_report_targets(config, worker, current)

    assert {(item["subject_id"], item["kind"]) for item in targets} == {
        ("subject-1", "weekly"),
        ("subject-1", "monthly"),
        ("subject-2", "monthly"),
    }
    monthly = whatsapp_bridge._scheduled_report_period("monthly", current)
    assert monthly["start"] == "2026-07-01"
    assert monthly["end"] == "2026-07-31"


def test_scheduled_report_is_formatted_as_mobile_cards_and_returns_chart_data(monkeypatch):
    context = {
        "suggestions": [{"title": "Revisar estoque", "severity": "warning", "category": "Estoque"}],
        "tool_results": [],
        "tool_plan": {},
        "sources": [],
        "management_analysis": {},
    }
    monkeypatch.setattr(codex_assistant, "_assistant_collect_data", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_report_chat_text",
        lambda *_args, **_kwargs: (
            "# Relatório semanal\n\n"
            "## Resumo executivo\n- Vendas estáveis no período.\n\n"
            "## Indicadores\n- Pedidos: 42\n- Ticket médio: R$ 135,00\n\n"
            "## Recomendações\n- Revisar os SKUs com menor cobertura.\n\n"
            "## Fontes e cobertura\n- JK Sistema: cobertura completa."
        ),
    )

    period, parts, chart_data = whatsapp_bridge._scheduled_report_parts(
        "cliente", "weekly", whatsapp_bridge.datetime(2026, 7, 13, 9, 0, 0)
    )
    joined = "\n\n".join(parts)

    assert period["start"] == "2026-07-06"
    assert period["end"] == "2026-07-12"
    assert joined.count("*📊 Relatório*") == 1
    assert "06/07/2026 a 12/07/2026" in joined
    assert "*📌 INDICADORES*" in joined
    assert "*🔎 ANÁLISE*" in joined
    assert "*🧾 FONTES E COBERTURA*" in joined
    assert chart_data["period_start"] == period["start"]
    assert chart_data["period_end"] == period["end"]
    assert all(len(part) <= whatsapp_bridge.WHATSAPP_REPORT_BODY_CHARS for part in parts)


def test_scheduled_monthly_charts_are_sent_without_whatsapp_footer(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path)
    calls = []

    def fake_post(*_args, **kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "sent"}

    monkeypatch.setattr(whatsapp_bridge, "_post_proactive_image", fake_post)
    period = {
        "key": "2026-07",
        "title": "Relatório mensal",
        "event_type": "monthly_report",
        "start": "2026-07-01",
        "end": "2026-07-31",
    }
    chart_data = {
        "analysis_type": "sales_report",
        "title": "Relatório mensal — vendas e operações",
        "source": "JK Sistema",
        "period_start": period["start"],
        "period_end": period["end"],
        "coverage_complete": True,
        "kpis": {"Pedidos": 12, "Faturamento": 1800},
        "series": [
            {"date": "2026-07-01", "orders": 5, "gross": 700},
            {"date": "2026-07-31", "orders": 7, "gross": 1100},
        ],
    }

    result = whatsapp_bridge._send_scheduled_report_visuals(
        {"machine_id": "machine"},
        client_id="cliente",
        subject_id="5537999993818",
        kind="monthly",
        period=period,
        chart_data=chart_data,
    )

    assert result["success"] is True
    assert calls
    assert all(item["caption"] == "" for item in calls)
    assert all(item["event_type"] == "monthly_report" for item in calls)


def test_typing_pulse_renews_and_stops_when_worker_marks_message_inactive(monkeypatch):
    calls = []
    replies = iter([{"success": True, "status": "sent"}, {"success": True, "status": "not_active"}])
    monkeypatch.setattr(whatsapp_bridge, "_post_typing_indicator", lambda *_args: calls.append(True) or next(replies))
    monkeypatch.setattr(whatsapp_bridge, "TYPING_REFRESH_SECONDS", 0)
    monkeypatch.setattr(whatsapp_bridge, "TYPING_MAX_SECONDS", 1)
    monkeypatch.setitem(whatsapp_bridge.RUNTIME_STATE, "typing_last_error", "old")

    whatsapp_bridge._typing_pulse_worker({}, "wamid.typing", threading.Event())

    assert calls == [True, True]
    assert whatsapp_bridge.RUNTIME_STATE["typing_last_error"] == ""
    assert whatsapp_bridge.RUNTIME_STATE["typing_last_sent_at"]


def test_typing_pulse_failure_is_silent_and_stops_after_three_errors(monkeypatch):
    calls = []

    def fail(*_args):
        calls.append(True)
        raise RuntimeError("typing unavailable")

    monkeypatch.setattr(whatsapp_bridge, "_post_typing_indicator", fail)
    monkeypatch.setattr(whatsapp_bridge, "TYPING_REFRESH_SECONDS", 0)
    monkeypatch.setattr(whatsapp_bridge, "TYPING_MAX_SECONDS", 1)
    monkeypatch.setitem(whatsapp_bridge.RUNTIME_STATE, "last_error", "business-flow-unchanged")
    monkeypatch.setitem(whatsapp_bridge.RUNTIME_STATE, "typing_last_error", "")

    whatsapp_bridge._typing_pulse_worker({}, "wamid.error", threading.Event())

    assert len(calls) == whatsapp_bridge.TYPING_MAX_CONSECUTIVE_ERRORS
    assert whatsapp_bridge.RUNTIME_STATE["last_error"] == "business-flow-unchanged"
    assert "typing unavailable" in whatsapp_bridge.RUNTIME_STATE["typing_last_error"]


def test_successful_message_result_stops_the_local_typing_pulse(monkeypatch):
    stop_event = threading.Event()
    with whatsapp_bridge.TYPING_PULSES_LOCK:
        whatsapp_bridge.TYPING_PULSES["wamid.done"] = stop_event
    monkeypatch.setattr(
        whatsapp_bridge,
        "_gateway_json",
        lambda *_args, **_kwargs: {"success": True, "status": "completed"},
    )
    try:
        result = whatsapp_bridge._post_message_result(
            {"machine_id": "machine"},
            "wamid.done",
            {"status": "completed", "response": "pronto"},
        )
        assert result["success"] is True
        assert stop_event.is_set()
    finally:
        with whatsapp_bridge.TYPING_PULSES_LOCK:
            whatsapp_bridge.TYPING_PULSES.pop("wamid.done", None)


def test_process_message_starts_typing_only_after_machine_ownership(monkeypatch):
    started = []
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda _config, message_id: started.append(message_id) or True)
    monkeypatch.setattr(whatsapp_bridge, "_pending_task_for_message", lambda *_args: {"task_id": "existing"})
    monkeypatch.setattr(whatsapp_bridge, "_complete_pending", lambda *_args: True)

    whatsapp_bridge._process_message(
        {"machine_id": "machine"},
        {},
        {"message_id": "wamid.owned", "machine_id": "machine"},
    )
    assert started == ["wamid.owned"]

    with pytest.raises(RuntimeError, match="message_not_owned_by_this_machine"):
        whatsapp_bridge._process_message(
            {"machine_id": "machine"},
            {},
            {"message_id": "wamid.other", "machine_id": "other"},
        )
    assert started == ["wamid.owned"]
