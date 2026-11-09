from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError
from requests import Timeout

from backend.modules.perguntas_pos_venda.endpoints import question_moderation as moderation
from backend.schemas.perguntas_pos_venda import PerguntasModeracaoRequest
from backend.services import mercadolivre_legacy_api, perguntas_store_config


@pytest.fixture
def marketplace(monkeypatch):
    scope = {
        "store_id": "store-1",
        "nome": "Loja Teste",
        "integracoes": {"mercadolivre": {"user_id": "101", "site_id": "MLB", "access_token": "fake-token"}},
    }
    question = {"id": 1001, "seller_id": 101, "item_id": "MLB2001", "from": {"id": 301}}
    market = SimpleNamespace(
        question=question, read_status=200, write_status=204, write_timeout=False, calls=[], invalidations=[],
    )
    monkeypatch.setattr(
        moderation.support, "carregar_lojas_snapshot", lambda tenant: [scope] if tenant == "tenant-a" else [],
    )
    monkeypatch.setattr(
        moderation.support, "_ml_oauth_status", lambda cfg: {"conectado": bool(cfg.get("access_token"))},
    )
    monkeypatch.setattr(
        moderation.support, "_ml_cfg_com_store_id_context", lambda cfg, store: {**cfg, "_store_id_context": store},
    )

    def provider(tenant, shop, cfg, method, url, **kwargs):
        assert (tenant, shop, cfg["_store_id_context"]) == ("tenant-a", "Loja Teste", "store-1")
        market.calls.append((method, url, kwargs))
        if method == "GET":
            return SimpleNamespace(status_code=market.read_status, json=lambda: market.question), cfg
        if market.write_timeout:
            raise Timeout("resposta do provedor omitida")
        return SimpleNamespace(status_code=market.write_status), cfg

    monkeypatch.setattr(moderation.support, "_ml_api_request", provider)
    monkeypatch.setattr(moderation, "_ml_api_request", provider)
    monkeypatch.setattr(moderation, "invalidate_store", lambda *args: market.invalidations.append(args))
    return market


def _invoke(endpoint, *, tenant="tenant-a", store="store-1"):
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.username = "operator"
    request.state.client_id = "tenant-a"
    payload = PerguntasModeracaoRequest(store_id=store, question_id="1001")
    return endpoint(request, payload, tenant)


def test_excluir_only_deletes_selected_question_and_invalidates_its_store(marketplace):
    result = _invoke(moderation.ml_perguntas_excluir)

    assert result == {"success": True}
    assert [(method, url) for method, url, _ in marketplace.calls] == [
        ("GET", "https://api.mercadolibre.com/questions/1001"),
        ("DELETE", "https://api.mercadolibre.com/questions/1001"),
    ]
    assert marketplace.calls[0][2]["params"] == {"api_version": 4}
    assert marketplace.calls[1][2]["json"] is None
    assert marketplace.invalidations == [("tenant-a", "store-1")]


def test_bloquear_uses_question_author_in_confirmed_seller_question_blacklist(marketplace):
    marketplace.question["buyer_id"] = 999
    marketplace.write_status = 201
    result = _invoke(moderation.ml_perguntas_bloquear_comprador)

    assert result == {"success": True}
    assert [(method, url) for method, url, _ in marketplace.calls] == [
        ("GET", "https://api.mercadolibre.com/questions/1001"),
        ("POST", "https://api.mercadolibre.com/users/101/questions_blacklist"),
    ]
    assert marketplace.calls[1][2]["json"] == {"user_id": 301}
    assert marketplace.invalidations == []


@pytest.mark.parametrize(("tenant", "store", "status"), [("tenant-b", "store-1", 401), ("tenant-a", "store-2", 403)])
def test_unbound_tenant_or_store_cannot_even_read_question(marketplace, tenant, store, status):
    with pytest.raises(HTTPException) as error:
        _invoke(moderation.ml_perguntas_excluir, tenant=tenant, store=store)
    assert error.value.status_code == status
    assert marketplace.calls == []


@pytest.mark.parametrize(("fields", "status"), [
    ({"id": 1002}, 502), ({"seller_id": 102}, 403), ({"item_id": "MLA2001"}, 403),
    ({"item_id": None}, 502),
])
def test_exclusion_refuses_unverified_question_seller_or_site(marketplace, fields, status):
    marketplace.question.update(fields)
    with pytest.raises(HTTPException) as error:
        _invoke(moderation.ml_perguntas_excluir)
    assert error.value.status_code == status
    assert [method for method, _, _ in marketplace.calls] == ["GET"]
    assert marketplace.invalidations == []


@pytest.mark.parametrize("author", [None, {"id": 101}, {"id": "invalid"}])
def test_block_refuses_missing_or_invalid_question_author(marketplace, author):
    marketplace.question.update({"from": author, "buyer_id": 301})
    with pytest.raises(HTTPException, match="não confirmou o comprador"):
        _invoke(moderation.ml_perguntas_bloquear_comprador)
    assert [method for method, _, _ in marketplace.calls] == ["GET"]


def test_request_rejects_provider_identity_from_browser():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PerguntasModeracaoRequest.model_validate({"question_id": "1001", "store_id": "store-1", "buyer_id": 999})


def test_transport_refuses_connection_change_after_question_read(marketplace, monkeypatch):
    def changed_connection(tenant, shop, store_id, *, seller_id, site_id):
        assert (tenant, shop, store_id, seller_id, site_id) == ("tenant-a", "Loja Teste", "store-1", "101", "MLB")
        return {"user_id": "101", "site_id": "MLB", "oauth_connection_id": "new"}

    def unexpected_write(*_args, **_kwargs):
        pytest.fail("changed connection must not reach the provider")

    monkeypatch.setattr(perguntas_store_config, "obter_cfg_ml_snapshot", changed_connection)
    monkeypatch.setattr(mercadolivre_legacy_api, "_ml_http_request", unexpected_write, raising=False)
    monkeypatch.setattr(moderation, "_ml_api_request", mercadolivre_legacy_api._ml_api_request)

    with pytest.raises(HTTPException) as error:
        _invoke(moderation.ml_perguntas_excluir)
    assert error.value.status_code == 409
    assert [method for method, _, _ in marketplace.calls] == ["GET"]
    assert marketplace.invalidations == []


@pytest.mark.parametrize(("write_status", "timeout", "status"), [(403, False, 403), (502, False, 502), (204, True, 504)])
def test_provider_failure_does_not_invalidate_or_retry_delete(marketplace, write_status, timeout, status):
    marketplace.write_status = write_status
    marketplace.write_timeout = timeout
    with pytest.raises(HTTPException) as error:
        _invoke(moderation.ml_perguntas_excluir)
    assert error.value.status_code == status
    assert "resposta do provedor omitida" not in error.value.detail
    assert [method for method, _, _ in marketplace.calls] == ["GET", "DELETE"]
    assert marketplace.calls[1][2]["retry_unauthorized"] is False
    assert marketplace.invalidations == []
