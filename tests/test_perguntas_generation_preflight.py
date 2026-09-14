"""Regression: browser context and singleton fallback are not loading evidence."""
from types import SimpleNamespace
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import manual_questions as manual, questions_v2
from backend.modules.perguntas_pos_venda.endpoints.questions_loading_support import Scope
from backend.schemas import PerguntasGerarRespostaRequest, MLQuestionsV2ProcessRequest
from backend.services import perguntas_generation_preflight as preflight
from backend.services import perguntas_loading_cache
from backend.services import perguntas_pos_venda_codex as codex_service
from backend.services import codex_assistant_storage


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
            "components": {key: {"state": "ready"} for key in ("question", "history", "item")},
            "cache_revision": perguntas_loading_cache.revision("tenant-a", "store-a")}


@pytest.fixture(autouse=True)
def clean_generation_sessions():
    preflight._SESSIONS.clear()
    with perguntas_loading_cache._LOCK:
        perguntas_loading_cache._ENTRIES.clear()
        perguntas_loading_cache._REVISIONS.clear()
    yield
    preflight._SESSIONS.clear()


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


def test_store_display_name_is_not_an_authorization_dimension():
    data = context()
    data["scope"]["name"] = "Nome anterior"
    assert preflight.validate_context(data, scope(), "23")["question"]["id"] == "23"


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


@pytest.mark.parametrize("surface", ["manual", "v2"])
def test_synchronous_generation_exposes_context_hub_status(monkeypatch, surface):
    from backend.modules.perguntas_pos_venda.ai import catalog_context

    canonical = context()
    canonical["context_status"] = {"source": "fresh_cache", "age_seconds": 2, "warning": None}
    monkeypatch.setattr(manual, "_manual_generation_scope", lambda *_args: scope())
    monkeypatch.setattr(questions_v2, "resolve_scope", lambda *_args: scope())
    monkeypatch.setattr(preflight, "load_context", lambda *_args: canonical)
    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "enabled", lambda: False)
    monkeypatch.setattr(manual, "_obter_cfg_ml", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(questions_v2, "_obter_cfg_ml", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(manual, "_ml_extrair_sku", lambda _item: "001")
    monkeypatch.setattr(questions_v2, "_ml_extrair_sku", lambda _item: "001")
    monkeypatch.setattr(manual, "bind_official_listing_catalog_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(catalog_context, "bind_official_listing_catalog_identity",
                        lambda *_args, **_kwargs: None)
    generated_context = {"context_hub": {"result": {
        "unavailable": True, "reason_code": "training_index_initializing",
        "retryable": True, "retry_after": 3, "warning": "Context Hub indisponivel.",
    }}}
    monkeypatch.setattr(manual, "_perguntas_ia_gerar_resposta",
                        lambda *_args: ("Rascunho", {}, generated_context.copy()))
    monkeypatch.setattr(questions_v2, "_perguntas_ia_gerar_resposta",
                        lambda *_args: ("Rascunho", {}, generated_context.copy()))

    if surface == "manual":
        result = manual.ml_perguntas_gerar_resposta_manual(
            PerguntasGerarRespostaRequest(
                loja="Loja", store_id="store-a",
                pergunta={"id": "23", "item_id": "MLB12", "text": "Pergunta"},
            ),
            request(),
            "tenant-a",
        )
        nested = result["contexto"]
    else:
        result = questions_v2.ml_questions_v2_process(
            request(), "23",
            MLQuestionsV2ProcessRequest(
                loja="Loja", store_id="store-a",
                pergunta={"id": "23", "item_id": "MLB12", "text": "Pergunta"},
            ),
            "tenant-a",
        )
        nested = result["context"]

    assert result["context_hub_status"] == nested["context_hub_status"]
    assert result["context_hub_status"] == {
        "state": "unavailable", "component": "context_hub",
        "reason": "training_index_initializing", "retryable": True,
        "retry_after": 3, "warning": "Context Hub indisponivel.",
    }
    assert result["warnings"] == ["Context Hub indisponivel."]


@pytest.mark.parametrize("surface", ["manual", "v2"])
def test_manual_job_persists_only_the_opaque_context_reference(monkeypatch, surface):
    captured = {}
    monkeypatch.setattr(manual, "_manual_generation_scope", lambda *_args: scope())
    monkeypatch.setattr(questions_v2, "resolve_scope", lambda *_args: scope())
    monkeypatch.setattr(preflight, "load_context", lambda *_args: context())
    monkeypatch.setattr(preflight, "remember_session", lambda *_args: "opaque-context")
    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "enabled", lambda: True)

    def create_job(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "create_job", create_job)
    browser_question = {
        "id": "23", "item_id": "MLB12", "text": "Texto do navegador",
        "buyer_question_chat": [{"text": "Historico que nao deve persistir"}],
    }
    if surface == "manual":
        manual.ml_perguntas_gerar_resposta_manual(
            PerguntasGerarRespostaRequest(
                loja="Nome antigo", store_id="store-a", pergunta=browser_question, **{"async": True},
            ),
            request(),
            "tenant-a",
        )
    else:
        monkeypatch.setattr(questions_v2, "_customer_reply_wait_or_raise", lambda *_args: {
            "job_id": "job-1", "status": "completed", "result": {"resposta": "ok", "contexto": {}},
        })
        questions_v2.ml_questions_v2_process(
            request(), "23",
            MLQuestionsV2ProcessRequest(
                loja="Nome antigo", store_id="store-a", pergunta=browser_question,
                item={"id": "MLB12", "title": "Item do navegador"},
            ),
            "tenant-a",
        )
    persisted = captured["request"]
    assert persisted["_generation_session"] == "opaque-context"
    assert persisted["item_id"] == "MLB12"
    assert "pergunta" not in persisted
    assert "item" not in persisted
    assert "buyer_question_chat" not in str(persisted)


def job_for(handle):
    return {"client_id": "tenant-a", "store_id": "store-a", "created_by": "operator",
            "store": "Loja", "seller_id": "17", "site_id": "MLB", "question_id": "23",
            "queue_origin": "manual", "request": {"_generation_session": handle}}


def session_request():
    req = current_local_session_request()
    req.state.auth_payload["exp"] = time.time() + 3600
    return req


def current_local_session_request():
    req = request()
    req.state.auth_payload = {
        "sub": "operator", "client_id": "tenant-a", "machine_id": "machine-a",
    }
    return req


def test_current_local_token_without_exp_gets_bounded_generation_session():
    before = time.time()
    handle = preflight.remember_session(current_local_session_request(), scope(), "23", context())
    after = time.time()

    remembered = preflight._SESSIONS[handle]
    assert before + preflight._SESSION_TTL_SECONDS <= remembered.expiry
    assert remembered.expiry <= after + preflight._SESSION_TTL_SECONDS
    assert preflight.run_with_session(job_for(handle), lambda: "authorized") == "authorized"


def test_manual_generation_with_current_local_token_reaches_job_creation(monkeypatch):
    captured = {}
    monkeypatch.setattr(manual, "_manual_generation_scope", lambda *_args: scope())
    monkeypatch.setattr(preflight, "load_context", lambda *_args: context())
    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "enabled", lambda: True)

    def create_job(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-current-token", "status": "queued"}

    monkeypatch.setattr(manual.perguntas_pos_venda_codex, "create_job", create_job)
    result = manual.ml_perguntas_gerar_resposta_manual(
        PerguntasGerarRespostaRequest(
            loja="Loja", store_id="store-a",
            pergunta={"id": "23", "item_id": "MLB12", "text": "Pergunta"},
            **{"async": True},
        ),
        current_local_session_request(),
        "tenant-a",
    )

    handle = captured["request"]["_generation_session"]
    assert result == {"job_id": "job-current-token", "status": "queued"}
    assert handle in preflight._SESSIONS


def test_token_expiry_still_limits_internal_generation_session():
    req = current_local_session_request()
    token_expiry = time.time() + 30
    req.state.auth_payload["exp"] = token_expiry
    handle = preflight.remember_session(req, scope(), "23", context())
    assert preflight._SESSIONS[handle].expiry == token_expiry


@pytest.mark.parametrize("invalid", [None, "", "invalid", float("nan"), float("inf"), float("-inf")])
def test_invalid_token_expiry_fails_closed(invalid):
    req = current_local_session_request()
    req.state.auth_payload["exp"] = invalid
    with pytest.raises(preflight.GenerationContextUnavailable) as caught:
        preflight.remember_session(req, scope(), "23", context())
    assert caught.value.headers["X-JK-Error-Component"] == "session"
    assert caught.value.headers["X-JK-Error-Reason"] == "session_expiry_invalid"


@pytest.mark.parametrize("claims", [
    {},
    {"sub": "other", "client_id": "tenant-a"},
    {"sub": "operator", "client_id": "tenant-b"},
])
def test_missing_or_foreign_auth_claims_fail_closed(claims):
    req = request()
    req.state.auth_payload = claims
    with pytest.raises(preflight.GenerationContextUnavailable) as caught:
        preflight.remember_session(req, scope(), "23", context())
    assert caught.value.headers["X-JK-Error-Component"] == "session"
    assert caught.value.headers["X-JK-Error-Reason"] == "session_identity_invalid"


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


def test_snapshot_age_metadata_does_not_break_duplicate_click_deduplication():
    req = session_request()
    first_context = context()
    first_context["context_status"] = {
        "source": "fresh_cache", "age_seconds": 1, "warning": None,
    }
    second_context = context()
    second_context["context_status"] = {
        "source": "fresh_cache", "age_seconds": 12, "warning": None,
    }
    first = preflight.remember_session(req, scope(), "23", first_context)
    second = preflight.remember_session(req, scope(), "23", second_context)
    assert first == second


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
    req = current_local_session_request()
    req.state.auth_payload["exp"] = time.time() - 1
    with pytest.raises(preflight.GenerationContextUnavailable):
        preflight.remember_session(req, scope())


def test_generation_sessions_are_bounded_and_store_purge_removes_snapshots():
    req = session_request()
    handles = [preflight.remember_session(req, scope(), str(index), {"marker": index})
               for index in range(129)]
    assert len(preflight._SESSIONS) == 128
    assert handles[0] not in preflight._SESSIONS
    preflight.forget_store_sessions("tenant-a", "store-a")
    assert preflight._SESSIONS == {}


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


def test_worker_generation_error_metadata_survives_completed_job(monkeypatch):
    monkeypatch.setattr(codex_service, "_runtime_info_base", lambda: "unused")
    monkeypatch.setattr(codex_service.codex_assistant_storage,
        "codex_assistant_customer_reply_job_save", lambda _base, _tenant, job, **_kwargs: job)
    error = preflight._blocked(
        "history", reason="component_timeout", status=504, retryable=True, retry_after=12,
    )
    result = codex_service._complete_generation_blocked(job_for("expired"), error)
    expected = {"error_code": "generation_context_unavailable", "error_component": "history",
                "error_reason": "component_timeout", "retryable": True, "retry_after": 12}
    assert {key: result[key] for key in expected} == expected
    assert {key: result["result"][key] for key in expected} == expected


def test_manual_volatile_job_keeps_only_safe_context_metadata(tmp_path):
    raw_history = "CANARY_BUYER_HISTORY"
    raw_obsidian = "CANARY_OBSIDIAN_DOCUMENT"
    payload = {
        "job_id": "manual-safe-job", "client_id": "tenant-a", "status": "waiting_retry",
        "queue_origin": "manual", "request": {
            "_generation_session": "opaque", "question_text": "Pergunta",
            "pergunta": {"buyer_question_chat": [{"text": raw_history}]},
            "item": {"description": raw_obsidian}, "access_token": "credential-canary",
        },
        "result": {"resposta": "Rascunho preservado", "contexto": {
            "buyer_question_chat": [{"text": raw_history}],
            "context_hub": {"result": {"document": raw_obsidian}},
            "verified_product_evidence": {"content": raw_obsidian},
            "context_status": {"source": "fresh_cache", "age_seconds": 2, "warning": None},
            "context_hub_status": {"state": "ready", "component": "context_hub", "retryable": False},
            "evidence_envelope": {"sources": ["obsidian://sku"], "records": [
                {"field": "sku", "value": raw_obsidian, "source": "obsidian://sku", "coverage": "confirmed"}
            ]},
        }},
        "last_partial_result": {"resposta": "Parcial", "contexto": {
            "historico_comprador": [{"text": raw_history}], "product_research_evidence": raw_obsidian,
        }},
        "research_history": [{"queries": [raw_history], "sources": ["obsidian://sku"],
                              "answer": "Parcial", "attempt": 1}],
    }
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", payload,
    )
    serialized = json.dumps(saved, ensure_ascii=False)
    assert raw_history not in serialized
    assert raw_obsidian not in serialized
    assert "credential-canary" not in serialized
    assert saved["request"] == {"_generation_session": "opaque", "question_text": "Pergunta"}
    safe_context = saved["result"]["contexto"]
    assert safe_context["context_status"]["source"] == "fresh_cache"
    assert safe_context["context_hub_status"]["state"] == "ready"
    assert safe_context["evidence_envelope"]["sources"] == ["obsidian://sku"]
    assert "value" not in safe_context["evidence_envelope"]["records"][0]
    assert "queries" not in saved["research_history"][0]


def test_legacy_nonmanual_volatile_job_context_is_unchanged(tmp_path):
    canary = "LEGACY_CONTEXT_CANARY"
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", {
            "job_id": "legacy-context-job", "client_id": "tenant-a", "status": "running",
            "queue_origin": "automation", "result": {"contexto": {"legacy": canary}},
        },
    )
    assert saved["result"]["contexto"]["legacy"] == canary


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
    handle = preflight.remember_session(session_request(), scope(), "23", context())
    called = []
    def canonical(*_args, **_kwargs):
        called.append(True)
        return context()
    monkeypatch.setattr(preflight.questions_loading, "canonical_context", canonical)
    monkeypatch.setattr(preflight, "resolve_scope", lambda *_args, **_kwargs: scope())
    runtime = SimpleNamespace(
        _obter_usuario_sql=lambda _username: {"client_id": "other" if revocation == "tenant" else "tenant-a"},
        _login_usuario_ativo=lambda _user: revocation != "inactive",
        _login_validade_ok=lambda _user: (revocation != "expired", ""),
        _carregar_permissoes_usuario=lambda *_args: {"perguntas_pos_venda": revocation != "permission"},
        _permissoes_autorizam_rota=lambda permissions, module: permissions.get(module) is True,
    )
    if revocation == "none":
        assert preflight.load_job_context(job_for(handle), runtime)["question"]["text"] == "Pergunta canonica"
        assert called == []
    else:
        with pytest.raises(preflight.GenerationContextUnavailable):
            preflight.load_job_context(job_for(handle), runtime)
        assert called == []


def test_worker_receives_detached_frozen_snapshot_without_ml_refetch(monkeypatch):
    frozen = context()
    handle = preflight.remember_session(session_request(), scope(), "23", frozen)
    frozen["question"]["text"] = "Mutacao externa"
    monkeypatch.setattr(preflight, "resolve_scope", lambda *_args, **_kwargs: scope())
    monkeypatch.setattr(preflight.questions_loading, "canonical_context",
                        lambda *_args, **_kwargs: pytest.fail("worker refetched Mercado Livre"))
    from backend.services import central_accounts_client
    monkeypatch.setattr(central_accounts_client, "current", lambda *_args: None)
    runtime = SimpleNamespace(
        _obter_usuario_sql=lambda _username: {"client_id": "tenant-a"},
        _login_usuario_ativo=lambda _user: True,
        _login_validade_ok=lambda _user: (True, ""),
        _carregar_permissoes_usuario=lambda *_args: {"perguntas_pos_venda": True},
        _permissoes_autorizam_rota=lambda permissions, module: permissions.get(module) is True,
    )
    first = preflight.load_job_context(job_for(handle), runtime)
    first["question"]["text"] = "Mutacao do worker"
    second = preflight.load_job_context(job_for(handle), runtime)
    assert second["question"]["text"] == "Pergunta canonica"


def test_worker_revalidates_missing_local_site_from_authorized_identity_cache(monkeypatch):
    from backend.services import central_accounts_client
    blank_site = Scope("tenant-a", "store-a", "operator", "17", "", "Loja renomeada")
    perguntas_loading_cache.read(
        blank_site.key("identity"), lambda: {"site_id": "MLB"}, ttl=3600, stale_seconds=0,
    )
    canonical = context()
    handle = preflight.remember_session(session_request(), scope(), "23", canonical)
    monkeypatch.setattr(central_accounts_client, "current", lambda *_args: None)
    monkeypatch.setattr(preflight, "resolve_scope", lambda *_args, **_kwargs: blank_site)
    runtime = SimpleNamespace(
        _obter_usuario_sql=lambda _username: {"client_id": "tenant-a"},
        _login_usuario_ativo=lambda _user: True,
        _login_validade_ok=lambda _user: (True, ""),
        _carregar_permissoes_usuario=lambda *_args: {"perguntas_pos_venda": True},
        _permissoes_autorizam_rota=lambda permissions, module: permissions.get(module) is True,
    )
    assert preflight.load_job_context(job_for(handle), runtime)["scope"]["site_id"] == "MLB"


@pytest.mark.parametrize("invalidate", ["normal", "revocation"])
def test_store_cache_invalidation_purges_frozen_generation_sessions(invalidate):
    handle = preflight.remember_session(session_request(), scope(), "23", context())
    assert handle in preflight._SESSIONS
    if invalidate == "normal":
        perguntas_loading_cache.invalidate_store("tenant-a", "store-a")
    else:
        perguntas_loading_cache._discard_scope(("tenant-a", "store-a"))
    assert handle not in preflight._SESSIONS


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
