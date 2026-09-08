from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.services import mercadolivre_http as http
from backend.services import perguntas_loading_transport as budget


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(budget, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_legacy_timeouts_unchanged_and_nested_budget_cannot_extend_deadline(clock):
    assert budget.request_timeout((3.05, 90)) == (3.05, 90)
    with budget.read_budget(15):
        clock[0] += 4
        with budget.read_budget(30):
            timeout = budget.request_timeout(35)
            assert timeout.total == 11
            assert timeout.connect_timeout == 11
        assert budget.remaining() == 11
    assert budget.remaining() is None


def test_deadline_blocks_followup_request_and_restores_context(clock, monkeypatch):
    calls = []
    response = requests.Response()
    response.status_code = 200

    def request(*args, **kwargs):
        calls.append(kwargs["timeout"].total)
        clock[0] += 9
        return response

    monkeypatch.setattr(http, "_ml_http_obter_session", lambda *a: SimpleNamespace(request=request))
    with pytest.raises(requests.Timeout):
        with budget.read_budget(15):
            http._ml_http_request("t", "store", "token", "GET", "https://api.mercadolibre.com/questions/search")
            http._ml_http_request("t", "store", "token", "GET", "https://api.mercadolibre.com/questions/search")
    assert calls == [15, 6]
    assert budget.remaining() is None


def test_oauth_refresh_and_retry_share_read_budget(clock, monkeypatch):
    from backend.services import mercadolivre_legacy_api as api

    seen = []

    def request(method, url, **kwargs):
        seen.append((method, kwargs["timeout"].total))
        clock[0] += 4
        response = requests.Response()
        response.status_code = 401 if len(seen) == 1 else 200
        response._content = json.dumps({"access_token": "refreshed", "refresh_token": "refresh"}).encode()
        return response

    monkeypatch.setattr(http, "_ml_http_obter_session", lambda *a: SimpleNamespace(request=request))
    monkeypatch.setattr(api, "_ml_http_request", http._ml_http_request, raising=False)
    monkeypatch.setattr(api, "_ml_http_invalidar_session", lambda *a: None, raising=False)
    monkeypatch.setattr(api, "_ml_atualizar_api_loja_exata", lambda *a: None)
    monkeypatch.setattr(api, "_env_bool", lambda *a: True, raising=False)
    monkeypatch.setattr(api, "logger", logging.getLogger("test"), raising=False)
    cfg = {"access_token": "old", "refresh_token": "refresh", "app_id": "app", "client_secret": "secret", "_store_id_context": "store"}
    with budget.read_budget(15):
        result, cfg = api._ml_api_request("tenant", "store", cfg, "GET", "https://api.mercadolibre.com/questions/search")
    assert result.status_code == 200
    assert seen == [("GET", 15), ("POST", 11), ("GET", 7)]


def test_central_read_respects_same_budget(clock):
    from backend.services.central_accounts_client import CentralClient
    timeouts = []

    def request(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        return response

    client = CentralClient.__new__(CentralClient)
    client.expires_at = float("inf")
    client._credential = "session"
    client.machine = "fixture"
    client._origin = "https://central.example"
    client._transport = SimpleNamespace(request=request)
    with budget.read_budget(15):
        clock[0] += 5
        assert client.call("POST", "/stores/store/request", {"method": "GET"}) == {}
    assert timeouts[0].total == 10
    assert timeouts[0].connect_timeout == 3.05
    client.call("POST", "/stores/store/request", {"method": "GET"})
    assert timeouts[1] == (3.05, 35)


def test_oauth_timeout_is_transient_for_screen_reads(monkeypatch):
    from backend.services import mercadolivre_legacy_api as api

    def timeout(*a, **k):
        raise requests.Timeout()

    monkeypatch.setattr(api, "_ml_http_request", timeout, raising=False)
    monkeypatch.setattr(api, "_env_bool", lambda *a: True, raising=False)
    cfg = {"access_token": "old", "refresh_token": "refresh", "app_id": "app", "client_secret": "secret"}
    with pytest.raises(HTTPException) as error:
        with budget.read_budget():
            api._ml_refresh_token("tenant", "store", cfg)
    assert error.value.status_code == 504


@pytest.mark.parametrize("cache_fails", [False, True])
def test_confirmed_publication_invalidates_exact_store_without_resending(monkeypatch, cache_fails):
    from backend.services import perguntas_pos_venda_perguntas_ml as questions

    posted = []
    invalidated = []

    def invalidate(tenant, store):
        invalidated.append((tenant, store))
        if cache_fails:
            raise RuntimeError("cache unavailable")

    monkeypatch.setitem(sys.modules, "backend.services.perguntas_loading_cache", SimpleNamespace(invalidate_store=invalidate))
    response = requests.Response()
    response.status_code = 201
    response._content = b'{"status":"ACTIVE"}'

    def send(tenant, name, cfg, method, url, **kwargs):
        posted.append(method)
        return response, cfg

    monkeypatch.setattr(questions, "_ml_api_request", send, raising=False)
    result, _ = questions._perguntas_ia_enviar_resposta_ml("t", "display", {"_store_id_context": "StoreExact"}, "q", "reply")
    assert result["status"] == "ACTIVE"
    assert posted == ["POST"]
    assert invalidated == [("t", "StoreExact")]


def test_rejected_publication_does_not_invalidate_cache(monkeypatch):
    from backend.services import perguntas_pos_venda_perguntas_ml as questions

    invalidated = []
    monkeypatch.setitem(sys.modules, "backend.services.perguntas_loading_cache", SimpleNamespace(invalidate_store=lambda *a: invalidated.append(a)))
    response = requests.Response()
    response.status_code = 403
    response._content = b'{}'
    monkeypatch.setattr(questions, "_ml_api_request", lambda *a, **k: (response, {"_store_id_context": "s"}), raising=False)
    monkeypatch.setattr(questions, "_ml_parse_error_detail", lambda *a: "denied", raising=False)
    with pytest.raises(HTTPException) as error:
        questions._perguntas_ia_enviar_resposta_ml("t", "display", {}, "q", "reply")
    assert error.value.status_code == 403
    assert invalidated == []


def test_lightweight_question_keeps_nested_variation_until_item_arrives(monkeypatch):
    from backend.services import perguntas_pos_venda_perguntas_ml as questions

    monkeypatch.setattr(questions, "_ml_perguntas_foto_item", lambda item: "", raising=False)
    monkeypatch.setattr(questions, "_ml_perguntas_nome_comprador", lambda *a: "", raising=False)
    question = {"id": "1", "item_id": "MLB1", "variation": {"id": 234}}
    result = questions._ml_perguntas_normalizar(question, {}, {})
    assert result["variation_id"] == "234"
    assert result["item_sku"] == ""
