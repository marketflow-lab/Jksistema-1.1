"""Regression: browser context and singleton fallback are not loading evidence."""
from types import SimpleNamespace
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import manual_questions as manual, questions_v2
from backend.modules.perguntas_pos_venda.endpoints.questions_loading_support import Scope
from backend.schemas import PerguntasGerarRespostaRequest, MLQuestionsV2ProcessRequest
from backend.services import perguntas_generation_preflight as preflight
from backend.services import perguntas_pos_venda_codex as codex_service


def scope():
    return Scope("tenant-a", "store-a", "operator", "17", "MLB", "Loja")


def request():
    req = Request({"type": "http", "headers": []})
    req.state.username, req.state.client_id = "operator", "tenant-a"
    return req


def context():
    return {"scope": vars(scope()), "question": {"id": "23", "item_id": "MLB12",
            "text": "Pergunta canonica", "buyer_question_chat": [{"text": "Canonico"}]},
            "item": {"id": "MLB12", "seller_id": 17, "site_id": "MLB"},
            "components": {key: {"state": "ready"} for key in ("question", "history", "item")}}


@pytest.mark.parametrize("state", ["unavailable", "access_blocked", "stale", "loading", None])
def test_singleton_history_is_not_successful_history(state):
    data = context()
    data["components"]["history"] = {"state": state}
    data["question"]["buyer_question_history"] = [data["question"].copy()]
    with pytest.raises(HTTPException) as caught:
        preflight.validate_context(data, scope(), "23")
    assert caught.value.headers["X-JK-Error-Code"] == "generation_context_unavailable"


def test_buyer_failure_and_truncated_success_allow_generation():
    data = context()
    data["components"]["buyer"] = {"state": "access_blocked"}
    data["history_truncated"] = True
    assert preflight.validate_context(data, scope(), "23")["history_truncated"] is True


@pytest.mark.parametrize("field,value", [("client_id", "tenant-b"), ("username", "other"),
    ("store_id", "store-b"), ("seller_id", "18"), ("site_id", "MLA")])
def test_rejects_cross_identity_context(field, value):
    data = context()
    data["scope"][field] = value
    with pytest.raises(HTTPException):
        preflight.validate_context(data, scope(), "23")


@pytest.mark.parametrize("codex", [True, False])
@pytest.mark.parametrize("surface", ["manual", "v2"])
def test_direct_endpoint_cannot_bypass_failed_history_with_browser_flags(monkeypatch, codex, surface):
    monkeypatch.setattr(manual, "_manual_generation_scope", lambda *_args: scope())
    monkeypatch.setattr(questions_v2, "resolve_scope", lambda *_args: scope())
    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "enabled", lambda: codex)
    def blocked(*_args, **_kwargs):
        raise HTTPException(409, {"code": "generation_context_unavailable"})
    monkeypatch.setattr(preflight, "load_context", blocked)
    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "create_job",
                        lambda **_kwargs: pytest.fail("AI job was created without history"))
    monkeypatch.setattr(manual, "_perguntas_ia_gerar_resposta",
                        lambda *_args: pytest.fail("AI was invoked without history"))
    payload = PerguntasGerarRespostaRequest(loja="Loja", store_id="store-a", pergunta={
        "id": "23", "item_id": "MLB12", "text": "browser text",
        "history_loaded": True, "buyer_question_history": [{"id": "23"}]})
    with pytest.raises(HTTPException) as caught:
        if surface == "manual":
            manual.ml_perguntas_gerar_resposta_manual(payload, request(), "tenant-a")
        else:
            questions_v2.ml_questions_v2_process(request(), "23",
                MLQuestionsV2ProcessRequest(loja="Loja", store_id="store-a", pergunta=payload.pergunta), "tenant-a")
    assert caught.value.detail["code"] == "generation_context_unavailable"


def job_for(handle):
    return {"client_id": "tenant-a", "store_id": "store-a", "created_by": "operator",
            "store": "Loja", "seller_id": "17", "site_id": "MLB", "question_id": "23",
            "queue_origin": "manual", "request": {"_generation_session": handle}}


def session_request():
    req = request()
    req.state.auth_payload = {"exp": time.time() + 3600}
    return req


def test_parallel_sessions_of_same_operator_keep_distinct_credentials():
    credential = contextvars.ContextVar("test_credential", default="ambient-admin")
    token = credential.set("restricted-session-a")
    first = preflight.remember_session(session_request(), scope())
    credential.set("restricted-session-b")
    second = preflight.remember_session(session_request(), scope())
    credential.reset(token)
    assert first != second
    assert preflight.run_with_session(job_for(first), credential.get) == "restricted-session-a"
    assert preflight.run_with_session(job_for(second), credential.get) == "restricted-session-b"
    assert credential.get() == "ambient-admin"


def test_duplicate_clicks_in_same_session_reuse_context_handle():
    req = session_request()
    with ThreadPoolExecutor(max_workers=4) as pool:
        handles = list(pool.map(lambda _index: preflight.remember_session(req, scope(), "23"), range(12)))
    assert len(set(handles)) == 1
    assert preflight.remember_session(req, scope(), "24") != handles[0]


@pytest.mark.parametrize("field", ["created_by", "client_id", "seller_id", "site_id", "store_id"])
def test_worker_rejects_reconnected_store_or_changed_identity(field):
    handle = preflight.remember_session(session_request(), scope())
    job = job_for(handle)
    job[field] = "changed"
    with pytest.raises(preflight.GenerationContextUnavailable):
        preflight.run_with_session(job, lambda: pytest.fail("cross-identity execution"))


def test_worker_restart_and_expired_session_fail_closed():
    with pytest.raises(preflight.GenerationContextUnavailable):
        preflight.run_with_session(job_for("missing-after-restart"), lambda: None)
    req = request()
    req.state.auth_payload = {"exp": time.time() - 1}
    with pytest.raises(preflight.GenerationContextUnavailable):
        preflight.remember_session(req, scope())


def test_worker_does_not_accept_legacy_singleton_fallback(monkeypatch):
    handle = preflight.remember_session(session_request(), scope())
    monkeypatch.setattr(codex_service, "_RUNTIME", SimpleNamespace())
    monkeypatch.setattr(preflight, "load_job_context", lambda *_args: (_ for _ in ()).throw(
        preflight._blocked("history")))
    job = job_for(handle)
    job["request"]["pergunta"] = {"id": "23", "text": "Forged", "buyer_question_history": [{"id": "23"}]}
    with pytest.raises(preflight.GenerationContextUnavailable):
        codex_service._load_question_context(job)


def test_loading_failure_cannot_turn_into_neutral_draft(monkeypatch):
    monkeypatch.setattr(codex_service, "_runtime_info_base", lambda: "unused")
    monkeypatch.setattr(codex_service.codex_assistant_storage,
        "codex_assistant_customer_reply_job_save", lambda _base, _tenant, job, **_kwargs: job)
    job = job_for("expired")
    job["last_partial_result"] = {"resposta": "Old draft"}
    result = codex_service._complete_generation_blocked(job)
    assert result["blocked_without_draft"] is True
    assert result["result"]["resposta"] == ""
    assert not result["requires_approval"]


def test_previous_contract_proposal_is_not_reusable():
    current = {"prompt_version": codex_service.PROMPT_VERSION,
               "schema_version": codex_service.SCHEMA_VERSION,
               "queue_policy_version": codex_service.QUEUE_POLICY_VERSION,
               "prompt_hash": codex_service.PROMPT_HASH}
    assert codex_service._job_contract_current(current)
    current["prompt_hash"] = "pre-loading-preflight-contract"
    assert not codex_service._job_contract_current(current)


@pytest.mark.parametrize("revocation", ["none", "inactive", "expired", "tenant", "permission"])
def test_worker_rechecks_current_user_before_canonical_read(monkeypatch, revocation):
    from backend.services import central_accounts_client
    monkeypatch.setattr(central_accounts_client, "current", lambda *_args: None)
    handle = preflight.remember_session(session_request(), scope(), "23")
    called = []
    def canonical(*_args, **_kwargs):
        called.append(True)
        return context()
    monkeypatch.setattr(preflight.questions_loading, "canonical_context", canonical)
    runtime = SimpleNamespace(
        _obter_usuario_sql=lambda _username: {"client_id": "other" if revocation == "tenant" else "tenant-a"},
        _login_usuario_ativo=lambda _user: revocation != "inactive",
        _login_validade_ok=lambda _user: (revocation != "expired", ""),
        _carregar_permissoes_usuario=lambda *_args: {"perguntas_pos_venda": revocation != "permission"},
        _permissoes_autorizam_rota=lambda permissions, module: permissions.get(module) is True,
    )
    if revocation == "none":
        assert preflight.load_job_context(job_for(handle), runtime)["question"]["text"] == "Pergunta canonica"
        assert called == [True]
    else:
        with pytest.raises(preflight.GenerationContextUnavailable):
            preflight.load_job_context(job_for(handle), runtime)
        assert called == []


def test_canonical_timeout_is_typed_so_worker_cannot_generate_fallback(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise HTTPException(504, "Tempo esgotado")
    monkeypatch.setattr(preflight.questions_loading, "canonical_context", timeout)
    with pytest.raises(preflight.GenerationContextUnavailable) as caught:
        preflight.load_context(request(), scope(), "23")
    assert caught.value.status_code == 504


def test_revocation_after_preflight_remains_typed_and_blocks_fallback():
    handle = preflight.remember_session(session_request(), scope(), "23")
    def revoked():
        raise HTTPException(403, "Access revoked during enrichment")
    with pytest.raises(preflight.GenerationContextUnavailable):
        preflight.run_with_session(job_for(handle), revoked)


@pytest.mark.parametrize("change", ["none", "session", "question"])
def test_active_job_reuse_requires_same_session_and_canonical_question(tmp_path, monkeypatch, change):
    monkeypatch.setattr(codex_service, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(codex_service, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(codex_service, "_schedule", lambda _job: True)
    monkeypatch.setattr(codex_service, "_resolve_job_store_identity",
                        lambda *_args, **_kwargs: {"store_id": "store-a", "seller_id": "17", "site_id": "MLB"})
    monkeypatch.setattr(codex_service.codex_agent_runtime, "resolve_guidance", lambda *_args, **_kwargs: [])
    payload = {"pergunta": context()["question"], "_generation_session": "opaque-execution-a"}
    arguments = dict(client_id="tenant-a", task_type="question", store="Loja", store_id="store-a",
                     seller_id="17", site_id="MLB", subject_key="23", created_by="operator")
    first = codex_service.create_job(request=payload, **arguments)
    second_payload = {**payload, "pergunta": dict(payload["pergunta"])}
    if change == "session":
        second_payload["_generation_session"] = "opaque-execution-b"
    elif change == "question":
        second_payload["pergunta"]["text"] = "Canonical question changed"
    second = codex_service.create_job(request=second_payload, **arguments)
    assert (first["job_id"] == second["job_id"]) is (change == "none")
