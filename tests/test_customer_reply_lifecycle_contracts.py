from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace

import openai_codex

from backend.schemas.ia import IAChatRequest
from backend.services import codex_console, ia_providers
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints


def _completed_job(
    job_id: str,
    *,
    task_type: str,
    event_subject_key: str,
    conversation_subject_key: str,
) -> dict:
    return {
        "job_id": job_id,
        "task_type": task_type,
        "store": "Loja A",
        "event_subject_key": event_subject_key,
        "subject_key": conversation_subject_key,
        "status": "completed",
        "agent_state": "aguardando_aprovacao",
        "result": {
            "resposta": f"Resposta tardia {job_id}",
            "requires_approval": True,
            "publish_attempted": False,
            "data_sufficient": True,
            "contexto": {},
        },
    }


def _patch_latest_job(monkeypatch, job: dict) -> list[dict]:
    calls: list[dict] = []

    def latest(*_args, **kwargs):
        calls.append(dict(kwargs))
        return dict(job)

    monkeypatch.setattr(orchestrator, "_runtime_info_base", lambda: "runtime-test")
    monkeypatch.setattr(orchestrator, "_queue_position", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        orchestrator.codex_assistant_storage,
        "codex_assistant_customer_reply_job_latest",
        latest,
    )
    return calls


def test_wait_job_does_not_treat_waiting_retry_as_terminal(monkeypatch):
    waiting = {"job_id": "job-retry", "status": "waiting_retry"}
    completed = {"job_id": "job-retry", "status": "completed"}
    monkeypatch.setattr(orchestrator, "get_job", lambda *_args: next(states))
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _seconds: None)
    states = iter((waiting, completed))

    result = orchestrator.wait_job("tenant", "job-retry", timeout=1)

    assert result["status"] == "completed"


def test_late_public_question_job_reconciles_once_and_only_for_exact_event(monkeypatch):
    event_key = "QUESTION-1"
    conversation_key = "item:MLB-1|buyer:BUYER-1"
    job = _completed_job(
        "job-question-late",
        task_type="public_question",
        event_subject_key=event_key,
        conversation_subject_key=conversation_key,
    )
    calls = _patch_latest_job(monkeypatch, job)
    request = {
        "pergunta": {
            "id": event_key,
            "item_id": "MLB-1",
            "from": {"id": "BUYER-1"},
        }
    }

    candidate, already_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key=event_key,
        request=request,
        approvals=[],
    )
    repeated, repeated_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key=event_key,
        request=request,
        approvals=[{"status": "rejected", "proposal_id": "job-question-late"}],
    )
    wrong_event, wrong_event_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="QUESTION-2",
        request={
            "pergunta": {
                "id": "QUESTION-2",
                "item_id": "MLB-1",
                "from": {"id": "BUYER-1"},
            }
        },
        approvals=[],
    )

    assert candidate and candidate["job_id"] == "job-question-late"
    assert already_reconciled is False
    answer, context = endpoints._customer_reply_job_draft(candidate)
    assert answer == "Resposta tardia job-question-late"
    assert context["codex_job_id"] == "job-question-late"
    assert repeated and repeated["job_id"] == "job-question-late"
    assert repeated_reconciled is True
    assert wrong_event is None
    assert wrong_event_reconciled is False
    assert {call["subject_key"] for call in calls} == {conversation_key}


def test_late_post_sale_job_reconciles_once_and_only_for_exact_message(monkeypatch):
    event_key = "pos_venda:PACK-1:MESSAGE-1"
    conversation_key = "pack:PACK-1"
    job = _completed_job(
        "job-post-sale-late",
        task_type="post_sale",
        event_subject_key=event_key,
        conversation_subject_key=conversation_key,
    )
    calls = _patch_latest_job(monkeypatch, job)
    request = {
        "pack_id": "PACK-1",
        "order_id": "ORDER-1",
        "buyer_id": "BUYER-1",
        "last_message_id": "MESSAGE-1",
        "last_message_text": "Ainda nao recebi.",
    }

    candidate, already_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="post_sale",
        store="Loja A",
        subject_key=event_key,
        request=request,
        approvals=[],
    )
    repeated, repeated_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="post_sale",
        store="Loja A",
        subject_key=event_key,
        request=request,
        approvals=[{"status": "sent", "codex_job_id": "job-post-sale-late"}],
    )
    wrong_message, wrong_message_reconciled = endpoints._customer_reply_late_reconciliation_candidate(
        client_id="tenant",
        task_type="post_sale",
        store="Loja A",
        subject_key="pos_venda:PACK-1:MESSAGE-2",
        request={**request, "last_message_id": "MESSAGE-2"},
        approvals=[],
    )

    assert candidate and candidate["job_id"] == "job-post-sale-late"
    assert already_reconciled is False
    result = endpoints._customer_reply_post_sale_job_result(candidate)
    assert result["resposta"] == "Resposta tardia job-post-sale-late"
    assert result["codex_job_id"] == "job-post-sale-late"
    assert repeated and repeated["job_id"] == "job-post-sale-late"
    assert repeated_reconciled is True
    assert wrong_message is None
    assert wrong_message_reconciled is False
    assert {call["subject_key"] for call in calls} == {conversation_key}


def test_codex_resume_omits_ephemeral_and_fallback_start_keeps_it(monkeypatch):
    captured: dict = {}

    class Thread:
        id = "thread-fallback"

        def run(self, _prompt: str, **_kwargs):
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                final_response="Resposta de teste",
            )

    class Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_resume(self, thread_id: str, **kwargs):
            captured["resume_id"] = thread_id
            captured["resume_kwargs"] = kwargs
            raise RuntimeError("thread antiga indisponivel")

        def thread_start(self, **kwargs):
            captured["start_kwargs"] = kwargs
            return Thread()

    with ExitStack() as stack:
        stack.enter_context(monkeypatch.context())
        monkeypatch.setattr(codex_console, "_codex_enabled", lambda: True)
        monkeypatch.setattr(codex_console, "_codex_sdk_installed", lambda: True)
        monkeypatch.setattr(codex_console, "_codex_auth_detected", lambda: True)
        monkeypatch.setattr(codex_console, "_codex_runtime_bin", lambda: "codex")
        monkeypatch.setattr(codex_console, "_codex_sdk_env", lambda: {})
        monkeypatch.setattr(codex_console, "_codex_nonfull_config_overrides", lambda: {})
        monkeypatch.setattr(codex_console, "_codex_readonly_cwd_for_session", lambda *_args: ".")
        monkeypatch.setattr(openai_codex, "Codex", Codex)
        monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)
        response, thread_id = ia_providers._chamar_codex_chat_com_thread(
            IAChatRequest(
                message="Responda ao comprador",
                model="codex:gpt-5.5",
                context={"modulo": "perguntas_pos_venda", "ia_finalidade": "pos_venda"},
            ),
            "tenant",
            thread_id="thread-existente",
            persist_thread=True,
            reasoning_effort="medium",
        )

    assert response == "Resposta de teste"
    assert thread_id == "thread-fallback"
    assert captured["resume_id"] == "thread-existente"
    assert "ephemeral" not in captured["resume_kwargs"]
    assert captured["start_kwargs"]["ephemeral"] is False
