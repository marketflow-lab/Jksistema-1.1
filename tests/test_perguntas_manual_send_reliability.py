"""Synthetic send failures: no runtime setup, tenant data, or network access."""
import json
import logging

import pytest
from fastapi import HTTPException
from requests import Response

from backend.modules.perguntas_pos_venda.endpoints import manual_questions as api
from backend.schemas.perguntas_pos_venda import PerguntasEnviarRespostaRequest


@pytest.fixture
def sending(monkeypatch):
    calls = []
    config_calls = []
    cfg = {"_store_id_context": "Store-A"}

    def config(tenant, name, *, store_id, require_name_match):
        assert require_name_match is True
        config_calls.append((tenant, name, store_id))
        return cfg

    def send(*args):
        calls.append(args)
        return {"status": "ACTIVE"}, cfg

    monkeypatch.setattr(api, "obter_cfg_ml_snapshot", config)
    monkeypatch.setattr(api, "_obter_cfg_ml", lambda *a, **k: pytest.fail("canonical store maintenance must not run"))
    monkeypatch.setattr(api, "_perguntas_ia_enviar_resposta_ml", send)
    monkeypatch.setattr(api, "_perguntas_ia_resolver_aprovacoes_pendentes", lambda *a, **k: [])
    monkeypatch.setattr(api, "_perguntas_ia_memoria_registrar_resposta_aprovada", lambda *a, **k: True)
    monkeypatch.setattr(api, "_perguntas_ia_state_carregar", lambda *a: {})
    monkeypatch.setattr(api, "_perguntas_ia_marcar_processada", lambda *a: None)
    monkeypatch.setattr(api, "_perguntas_ia_state_salvar", lambda *a: None)
    monkeypatch.setattr(api, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: {
        "job": {"store_id": "Store-A"}, "proposal_version": 1, "proposal_hash": "hash",
    })
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "mark_verified", lambda **k: None)
    request = PerguntasEnviarRespostaRequest(
        loja="Fixture store", store_id="Store-A", question_id="question-fixture",
        resposta="Approved fixture answer.", proposal_id="proposal-fixture",
    )
    return request, calls, config_calls


@pytest.mark.parametrize("failure,expected", [
    ("verification", "local_verification_pending"),
    ("approvals", "local_approvals_pending"),
    ("state", "local_state_pending"),
])
def test_remote_confirmation_survives_local_bookkeeping_failure(monkeypatch, sending, failure, expected):
    request, sends, config_calls = sending

    def unavailable(*a, **k):
        raise RuntimeError("synthetic persistence outage")

    targets = {
        "verification": (api.perguntas_pos_venda_codex, "mark_verified"),
        "approvals": (api, "_perguntas_ia_resolver_aprovacoes_pendentes"),
        "state": (api, "_perguntas_ia_state_salvar"),
    }
    monkeypatch.setattr(*targets[failure], unavailable)
    result = api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert result["success"] is True
    assert result["warnings"] == [expected]
    assert len(sends) == 1
    assert config_calls == [("tenant-fixture", "Fixture store", "Store-A")]


@pytest.mark.parametrize("detail", ["remote denied", "remote denied | Item must be active later"])
def test_remote_failure_never_retries_post_or_masks_original_error(monkeypatch, sending, detail):
    request, sends, _ = sending
    rejected = HTTPException(status_code=403, detail=detail)

    def send(*args):
        sends.append(args)
        raise rejected

    def verification(**kwargs):
        raise RuntimeError("synthetic local failure")

    monkeypatch.setattr(api, "_perguntas_ia_enviar_resposta_ml", send)
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "mark_verified", verification)
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value is rejected
    assert len(sends) == 1


@pytest.mark.parametrize("failure,detail,site_id", [
    pytest.param(None, "Item must be active", "MLB", id="paused-out-of-stock"),
    pytest.param("lookup", "remote denied | Item must be active", None, id="lookup-unavailable"),
    pytest.param("question_seller", "Item must be active", "MLB", id="other-question-seller"),
    pytest.param("item_seller", "Item must be active", "MLB", id="other-item-seller"),
    pytest.param("item_site", "Item must be active", "MLB", id="other-item-site"),
])
def test_inactive_item_rejection_reports_only_verified_reason(monkeypatch, sending, failure, detail, site_id):
    request, sends, _ = sending
    request.question_id = "1234567890"
    request.item_id = "MLB999999"
    request.pergunta = {"item_id": request.item_id}
    seller_id = "123456"
    item_id = "MLB1234567"
    store = {"_store_id_context": "Store-A", "user_id": seller_id, "access_token": "old-fixture-token"}
    if site_id:
        store["site_id"] = site_id
    current_store = {**store, "access_token": "current-fixture-token"}
    config_calls = []
    lookups = []

    def config(tenant, name, *, store_id, require_name_match, seller_id=None, site_id=None):
        config_calls.append((tenant, name, store_id, require_name_match, seller_id, site_id))
        return current_store if seller_id is not None else store

    def send(*args):
        sends.append(args)
        raise HTTPException(status_code=400, detail=detail)

    def lookup(tenant, name, cfg, method, url, **kwargs):
        lookups.append((tenant, name, cfg, method, url, kwargs))
        response = Response()
        response.status_code = 200
        if url.endswith(f"/questions/{request.question_id}"):
            response.status_code = 503 if failure == "lookup" else 200
            payload = {"id": int(request.question_id), "item_id": item_id,
                       "seller_id": "654321" if failure == "question_seller" else seller_id}
        else:
            payload = {"id": item_id, "status": "paused", "sub_status": ["out_of_stock"],
                       "seller_id": "654321" if failure == "item_seller" else seller_id,
                       "site_id": "MLA" if failure == "item_site" else "MLB"}
        response._content = json.dumps(payload).encode("utf-8")
        return response, cfg

    monkeypatch.setattr(api, "obter_cfg_ml_snapshot", config)
    monkeypatch.setattr(api, "_perguntas_ia_enviar_resposta_ml", send)
    monkeypatch.setattr(api, "_ml_api_request", lookup)

    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")

    expected_message = (
        "Anúncio pausado por falta de estoque no Mercado Livre." if failure is None
        else "O Mercado Livre só permite responder quando o anúncio está ativo."
    )
    assert error.value.status_code == 400
    assert error.value.detail == {"code": "ml_item_inactive", "message": expected_message}
    assert len(sends) == 1
    assert config_calls == [
        ("tenant-fixture", "Fixture store", "Store-A", True, None, None),
        ("tenant-fixture", "Fixture store", "Store-A", True, seller_id, site_id),
    ]
    urls = [f"https://api.mercadolibre.com/questions/{request.question_id}"]
    if failure not in {"lookup", "question_seller"}:
        urls.append(f"https://api.mercadolibre.com/items/{item_id}")
    assert [call[4] for call in lookups] == urls
    assert str(lookups[0][5]["params"]["api_version"]) == "4"
    for tenant, name, cfg, method, _, options in lookups:
        assert (tenant, name, cfg, method) == ("tenant-fixture", "Fixture store", current_store, "GET")
        assert options["retry_unauthorized"] is False


def test_store_contention_fails_before_proposal_approval_and_post(monkeypatch, sending):
    request, sends, _ = sending

    def busy(*a, **k):
        raise HTTPException(status_code=409, detail={"code": "stores_busy"})

    monkeypatch.setattr(api, "obter_cfg_ml_snapshot", busy)
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: pytest.fail("no approval on configuration failure"))
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value.detail["code"] == "stores_busy"
    assert sends == []


def test_proposal_from_another_exact_store_cannot_send(monkeypatch, sending):
    request, sends, _ = sending
    monkeypatch.setattr(api.perguntas_pos_venda_codex, "approve_or_refresh_proposal", lambda **k: {"job": {"store_id": "Store-B"}})
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert error.value.status_code == 403
    assert sends == []


def test_legacy_request_without_store_id_remains_accepted(sending):
    request, sends, config_calls = sending
    request.store_id = ""
    request.proposal_id = ""
    result = api.ml_perguntas_responder_manual(request, "tenant-fixture")
    assert result["success"] is True
    assert len(sends) == 1
    assert config_calls[0][2] == ""
