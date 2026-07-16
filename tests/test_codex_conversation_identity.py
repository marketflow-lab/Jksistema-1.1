from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.services import codex_console, whatsapp_bridge


def _request(path: str = "/api/codex/tasks") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.fixture()
def conversation_runtime(tmp_path, monkeypatch):
    monkeypatch.setitem(codex_console.__dict__, "BASE_DIR", str(tmp_path))
    monkeypatch.setitem(codex_console.__dict__, "PASTA_INFO", str(tmp_path / "info"))
    monkeypatch.setattr(codex_console, "_codex_enabled", lambda: True)
    monkeypatch.setattr(codex_console, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(codex_console, "_codex_start_thread", lambda _task_id: None)
    codex_console.CODEX_TASKS.clear()
    codex_console.CODEX_ACTIVE_QUEUES.clear()
    yield tmp_path
    codex_console.CODEX_TASKS.clear()
    codex_console.CODEX_ACTIVE_QUEUES.clear()


def _session(username: str = "admin", client_id: str = "cliente", full: bool = True):
    return {
        "username": username,
        "client_id": client_id,
        "permissions": {"full": full, "vendas": True},
        "is_full": full,
    }


def test_app_identity_ignores_client_conversation_and_thread_ids(conversation_runtime):
    first = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Primeira pergunta",
            conversation_id="cliente-tentou-criar-uma",
            thread_id="thread-forjada-1",
        ),
        _session(),
    )["task"]
    second = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(
            prompt="Segunda pergunta",
            conversation_id="outra-conversa-forjada",
            thread_id="thread-forjada-2",
        ),
        _session(),
    )["task"]

    assert first["conversation_id"] == second["conversation_id"]
    assert first["conversation_id"].startswith("app_")
    assert first["thread_id"] == second["thread_id"] == ""
    assert first["conversation_generation"] == second["conversation_generation"] == 1


def test_whatsapp_identity_is_one_conversation_per_normalized_phone(conversation_runtime):
    def create(phone: str, subject: str = "subject"):
        return codex_console.codex_criar_tarefa_para_sessao(
            codex_console.CodexTaskRequest(prompt="Consulta de estoque", conversation_id="ignorar"),
            _session(),
            origin="whatsapp",
            channel_metadata={"wa_id": phone, "subject_id": subject},
        )["task"]

    formatted = create("+55 (11) 99999-0000", "subject-a")
    digits = create("5511999990000", "subject-b")
    other = create("5511888880000", "subject-a")

    assert formatted["conversation_id"] == digits["conversation_id"]
    assert formatted["conversation_id"] != other["conversation_id"]
    assert formatted["conversation_id"].startswith("wa_")
    assert formatted["channel"] == "whatsapp"


def test_whatsapp_identity_unifies_brazilian_mobile_with_or_without_ninth_digit(conversation_runtime):
    with_ninth_digit = codex_console._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="5537999995515"
    )
    meta_variant = codex_console._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone="553799995515"
    )

    assert with_ninth_digit == meta_variant


def test_same_phone_is_isolated_between_users_and_clients(conversation_runtime):
    phone = "5511999990000"
    base = codex_console._codex_canonical_conversation_id("cliente", "admin", channel="whatsapp", phone=phone)
    other_user = codex_console._codex_canonical_conversation_id("cliente", "operador", channel="whatsapp", phone=phone)
    other_client = codex_console._codex_canonical_conversation_id("outro", "admin", channel="whatsapp", phone=phone)

    assert len({base, other_user, other_client}) == 3


def test_whatsapp_missing_phone_never_uses_unknown_bucket(conversation_runtime):
    with pytest.raises(HTTPException) as exc:
        codex_console.codex_criar_tarefa_para_sessao(
            codex_console.CodexTaskRequest(prompt="Consulta"),
            _session(),
            origin="whatsapp",
            channel_metadata={"subject_id": "subject-only"},
        )
    assert exc.value.status_code == 409

    with pytest.raises(RuntimeError, match="whatsapp_phone_identity_missing"):
        whatsapp_bridge._conversation_id(
            {"client_id": "cliente", "username": "admin", "subject_id": "subject-only"},
            {"subject_id": "subject-only"},
        )


def test_fifo_runs_same_conversation_sequentially(conversation_runtime, monkeypatch):
    conversation_id = codex_console._codex_canonical_conversation_id("cliente", "admin", channel="app")
    order: list[str] = []
    running = {"value": False}

    for index in (1, 2):
        task_id = f"task-{index}"
        codex_console.CODEX_TASKS[task_id] = {
            "task_id": task_id,
            "status": "queued",
            "conversation_id": conversation_id,
            "conversation_generation": 1,
            "client_id": "cliente",
            "created_by": "admin",
            "origin": "app",
            "created_at": f"2026-07-13T10:00:0{index}Z",
        }

    def fake_worker(task_id: str):
        assert running["value"] is False
        running["value"] = True
        order.append(task_id)
        codex_console.CODEX_TASKS[task_id]["status"] = "completed"
        running["value"] = False

    monkeypatch.setattr(codex_console, "_codex_run_worker", fake_worker)
    queue_key = codex_console._codex_task_queue_key(codex_console.CODEX_TASKS["task-1"])
    codex_console._codex_run_conversation_queue(queue_key)

    assert order == ["task-1", "task-2"]


def test_reset_keeps_id_and_archives_previous_generation(conversation_runtime):
    session = _session()
    created = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt="Contexto anterior"),
        session,
    )["task"]
    task = codex_console.CODEX_TASKS[created["task_id"]]
    task.update({"status": "completed", "final_response": "Resposta anterior"})
    codex_console._codex_persist_task(task)

    with patch.object(codex_console, "_codex_require_authenticated", return_value=session):
        result = codex_console.codex_reset_current_conversation(
            codex_console.CodexConversationResetRequest(confirm=True),
            _request("/api/codex/conversations/current/reset"),
            "Bearer token",
        )

    assert result["conversation"]["conversation_id"] == created["conversation_id"]
    assert result["conversation"]["generation"] == 2
    public_old = codex_console._codex_public_task(task)
    assert public_old["conversation_state"] == "archived"


def test_reports_are_visible_but_excluded_from_conversation_memory(conversation_runtime):
    report = codex_console.codex_register_report_history(
        client_id="cliente",
        username="admin",
        prompt="Relatorio diario",
        report={"report_id": "report-1", "chat_text": "Resumo automatico"},
        conversation_id="forjada",
        thread_id="forjada",
    )
    task = codex_console.CODEX_TASKS[report["task_id"]]

    assert report["conversation_id"].startswith("app_")
    assert report["memory_excluded"] is True
    assert codex_console._codex_task_history_messages(task) == []


def test_restart_requeues_pending_and_marks_interrupted_without_memory(conversation_runtime, monkeypatch):
    conversation_id = codex_console._codex_canonical_conversation_id("cliente", "admin", channel="app")
    base = {
        "conversation_id": conversation_id,
        "conversation_generation": 1,
        "client_id": "cliente",
        "created_by": "admin",
        "origin": "app",
        "created_at": "2026-07-13T10:00:00Z",
        "prompt": "Teste de retomada",
        "permissions": {"full": True},
    }
    for task_id, status in (("queued-restart", "queued"), ("running-restart", "running")):
        path = Path(codex_console._codex_task_path(task_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({**base, "task_id": task_id, "status": status}, ensure_ascii=False),
            encoding="utf-8",
        )
    started: list[str] = []
    monkeypatch.setattr(codex_console, "_codex_start_thread", lambda task_id: started.append(task_id))

    result = codex_console.codex_console_recuperar_fila_background()

    assert result["queued_task_ids"] == ["queued-restart"]
    assert result["interrupted_task_ids"] == ["running-restart"]
    assert started == ["queued-restart"]
    interrupted = codex_console.CODEX_TASKS["running-restart"]
    assert interrupted["status"] == "failed"
    assert codex_console._codex_task_history_messages(interrupted) == []


def _completed_whatsapp_task(session, phone: str, prompt: str, response: str):
    public = codex_console.codex_criar_tarefa_para_sessao(
        codex_console.CodexTaskRequest(prompt=prompt),
        session,
        origin="whatsapp",
        channel_metadata={"wa_id": phone, "subject_id": "subject-test"},
    )["task"]
    task = codex_console.CODEX_TASKS[public["task_id"]]
    task.update(
        {
            "status": "completed",
            "final_response": response,
            "completed_at": "2026-07-13T12:00:00Z",
        }
    )
    codex_console._codex_persist_task(task)
    return task


def test_whatsapp_history_groups_by_phone_and_isolates_authenticated_user(conversation_runtime):
    session = _session("admin", "cliente")
    first = _completed_whatsapp_task(session, "+55 (11) 99999-0000", "Primeira pergunta", "Primeira resposta")
    _completed_whatsapp_task(session, "5511999990000", "Segunda pergunta", "Segunda resposta")
    _completed_whatsapp_task(session, "5511888880000", "Outro telefone", "Outra resposta")
    _completed_whatsapp_task(_session("operador", "cliente"), "5511999990000", "Pergunta privada", "Resposta privada")

    with patch.object(codex_console, "_codex_require_authenticated", return_value=session):
        result = codex_console.codex_listar_conversas_whatsapp(
            _request("/api/codex/conversations/whatsapp"),
            "Bearer token",
            50,
            0,
        )

    assert result["total"] == 2
    grouped = {item["phone"]: item for item in result["conversations"]}
    assert grouped["5511999990000"]["exchange_count"] == 2
    assert grouped["5511999990000"]["phone_display"] == "+55 (11) 99999-0000"
    assert all("Pergunta privada" not in item["last_prompt_preview"] for item in result["conversations"])
    assert first["conversation_id"] == grouped["5511999990000"]["conversation_id"]


def test_whatsapp_history_returns_paginated_previews_and_explicit_full_text(conversation_runtime):
    session = _session("admin", "cliente")
    long_response = "R" * 14000
    task = _completed_whatsapp_task(session, "5511999990000", "Detalhe da venda", long_response)
    conversation_id = str(task["conversation_id"])

    with patch.object(codex_console, "_codex_require_authenticated", return_value=session):
        page = codex_console.codex_listar_mensagens_conversa_whatsapp(
            conversation_id,
            _request(f"/api/codex/conversations/whatsapp/{conversation_id}/messages"),
            "Bearer token",
            20,
            0,
        )
        full = codex_console.codex_obter_mensagem_conversa_whatsapp(
            conversation_id,
            str(task["task_id"]),
            _request(f"/api/codex/conversations/whatsapp/{conversation_id}/messages/{task['task_id']}"),
            "Bearer token",
        )

    assert page["total"] == 1
    assert len(page["messages"][0]["response"]) == 12000
    assert page["messages"][0]["response_truncated"] is True
    assert full["message"]["response"] == long_response

    other_session = _session("operador", "cliente")
    with patch.object(codex_console, "_codex_require_authenticated", return_value=other_session):
        with pytest.raises(HTTPException) as exc:
            codex_console.codex_listar_mensagens_conversa_whatsapp(
                conversation_id,
                _request(),
                "Bearer token",
            )
    assert exc.value.status_code == 404


def test_whatsapp_history_lists_registered_phones_before_first_exchange(conversation_runtime):
    session = _session("admin", "cliente")
    phone = "5537999999791"
    conversation_id = codex_console._codex_canonical_conversation_id(
        "cliente", "admin", channel="whatsapp", phone=phone
    )
    registered = [{
        "conversation_id": conversation_id,
        "phone": phone,
        "label": "Comercial",
        "registered_at": "2026-07-13T17:06:12Z",
        "last_inbound_at": "",
    }]

    with (
        patch.object(codex_console, "_codex_require_authenticated", return_value=session),
        patch.object(codex_console, "_codex_registered_whatsapp_bindings", return_value=registered),
    ):
        conversations = codex_console.codex_listar_conversas_whatsapp(
            _request("/api/codex/conversations/whatsapp"),
            "Bearer token",
            50,
            0,
        )
        messages = codex_console.codex_listar_mensagens_conversa_whatsapp(
            conversation_id,
            _request(f"/api/codex/conversations/whatsapp/{conversation_id}/messages"),
            "Bearer token",
            20,
            0,
        )

    assert conversations["total"] == 1
    item = conversations["conversations"][0]
    assert item["registered"] is True
    assert item["label"] == "Comercial"
    assert item["exchange_count"] == 0
    assert item["last_inbound_at"] == ""
    assert messages["total"] == 0
    assert messages["conversation"]["registered"] is True


def test_configuracoes_exposes_readonly_whatsapp_history_tab():
    html = Path("static/configuracoes.html").read_text(encoding="utf-8")
    script = Path("static/configuracoes-whatsapp-history.js").read_text(encoding="utf-8")

    assert 'data-tab="whatsapp-history"' in html
    assert 'id="waHistoryTimeline"' in html
    assert "/configuracoes-whatsapp-history.js?v=" in html
    assert "/api/codex/conversations/whatsapp?limit=100" in script
    assert "replaceChildren" in script
    assert "textContent" in script
    assert "method: 'POST'" not in script
    assert "method: 'DELETE'" not in script
