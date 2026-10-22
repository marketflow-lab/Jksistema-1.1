from __future__ import annotations

import copy
import logging
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import dependencies, sync_automation, sync_engine
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.sync_queue import SyncQueue
from backend.schemas import VendasSyncRequest
from backend.services import bling_vendas


URL = "https://api.bling.com.br/Api/v3/pedidos/vendas"


@pytest.fixture
def context(tmp_path, monkeypatch):
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logging.getLogger("preflight-test"))
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(paths=paths, logger=paths.logger))
    cfg = {"id": "app-a", "secret": "fixture-secret", "access_token": "fixture-access",
           "refresh_token": "fixture-refresh", "oauth_connection_id": "connection-a", "connected": True}
    state = {"cfg": cfg, "responses": [], "calls": [], "refreshes": [], "marked": []}
    req = VendasSyncRequest(loja="Mesmo nome", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-02")

    def store(client, name, *, store_id):
        assert (client, name, store_id) == ("tenant-a", req.loja, req.store_id)
        return {"nome": name, "store_id": store_id, "integracoes": {"bling": copy.deepcopy(state["cfg"])}}

    def get(url, **kwargs):
        assert url == URL
        state["calls"].append(kwargs)
        response = state["responses"].pop(0)
        return response() if callable(response) else response

    def renew(client, name, cfg, *, store_id, **_kwargs):
        state["refreshes"].append((client, store_id))
        state["cfg"] = {**cfg, "access_token": "fixture-renewed", "updated_at": "new-version"}
        return copy.deepcopy(state["cfg"])

    monkeypatch.setattr(sync_engine, "buscar_loja", store)
    monkeypatch.setattr(sync_engine, "_bling_get_with_adaptive_limit", get)
    monkeypatch.setattr(sync_engine, "_bling_executar_com_refresh", bling_vendas._bling_executar_com_refresh)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", renew)
    monkeypatch.setattr(bling_vendas, "_bling_marcar_oauth_invalido", lambda *args, **kwargs: state["marked"].append(kwargs))
    monkeypatch.setattr(sync_engine, "_verificar_cancelamento", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_set_progresso", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_sync_unregister_active", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_listar_bancos_vendas_tenant", lambda *_: [])
    monkeypatch.setattr(sync_automation, "wake", lambda: None)
    yield req, state


def response(status=200, body=None, *, url=URL, headers=None):
    def json():
        if isinstance(body, Exception):
            raise body
        return body
    return SimpleNamespace(status_code=status, json=json, url=url, headers=headers or {})


def pending(req):
    queue = SyncQueue("tenant-a")
    job = queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim)
    queue.claim("first")
    queue.complete_day(job["id"], "first", req.data_inicio, {"vendas": 3})
    queue.fail_job(job["id"], "first", "A integração está indisponível. A sincronização tentará novamente.")
    queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim)
    job = queue.claim("current")
    return queue, job


def test_minimal_get_confirms_empty_account_without_collecting(context):
    req, state = context
    state["responses"] = [response(body={"data": []})]
    sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert len(state["calls"]) == 1
    assert state["calls"][0]["params"] == {"pagina": 1, "limite": 1}
    assert state["calls"][0]["max_attempts"] == 1
    assert not state["refreshes"] and not state["marked"]


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, [], ValueError("invalid JSON")])
def test_incomplete_200_never_confirms_connection(context, body):
    req, state = context
    state["responses"] = [response(body=body)]
    with pytest.raises(VendasDomainError) as error:
        sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert error.value.status_code == 502
    assert not state["refreshes"] and not state["marked"]


@pytest.mark.parametrize("status", [400, 403, 429, 500, 502, 503])
def test_provider_failure_preserves_pending_work_and_warning(context, status):
    req, state = context
    queue, job = pending(req)
    before = queue.get_job(job["id"])
    state["responses"] = [response(status, headers={"Retry-After": "7"})]
    with pytest.raises(VendasDomainError):
        sync_engine._verificar_alertas_antes_da_coleta(req, "tenant-a", queue, job["id"], "current")
    assert queue.get_job(job["id"]) == before
    assert queue.summary()["lojas"][0]["ultimo_erro"] == before["last_error"]
    assert not state["refreshes"] and not state["marked"]


def test_timeout_does_not_acknowledge_warning(context):
    req, state = context
    queue, job = pending(req)
    state["responses"] = [None]
    with pytest.raises(VendasDomainError, match="confirmar"):
        sync_engine._verificar_alertas_antes_da_coleta(req, "tenant-a", queue, job["id"], "current")
    assert queue.summary()["lojas"][0]["ultimo_erro"]


@pytest.mark.parametrize("flags", [{"oauth_invalid": True}, {"status": "reautenticacao_necessaria"}])
def test_invalidated_connection_requires_reconnect_without_get_or_refresh(context, flags):
    req, state = context
    state["cfg"].update(flags)
    with pytest.raises(VendasDomainError) as error:
        sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert error.value.code == "bling_reconnect_required"
    assert not state["calls"] and not state["refreshes"] and not state["marked"]


def test_expired_local_token_uses_one_refresh_and_rechecks_same_store(context):
    req, state = context
    state["responses"] = [response(401), response(body={"data": []})]
    sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert state["refreshes"] == [("tenant-a", "store-a")]
    assert len(state["calls"]) == 2
    assert state["calls"][1]["headers"]["Authorization"] == "Bearer fixture-renewed"
    assert not state["marked"]


def test_repeated_401_marks_only_the_exact_local_store(context):
    req, state = context
    state["responses"] = [response(401), response(401)]
    with pytest.raises(VendasDomainError) as error:
        sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert error.value.code == "bling_reconnect_required"
    assert state["refreshes"] == [("tenant-a", "store-a")]
    assert state["marked"] == [{"store_id": "store-a"}]


@pytest.mark.parametrize("changed", [{}, {"access_token": "another-account"}, {"oauth_invalid": True},
                                    {"oauth_connection_id": "another-connection"}])
def test_config_change_during_get_discards_the_old_success(context, changed):
    req, state = context
    queue, job = pending(req)
    def raced():
        state["cfg"] = {} if not changed else {**state["cfg"], **changed}
        return response(body={"data": []})
    state["responses"] = [raced]
    with pytest.raises(VendasDomainError):
        sync_engine._verificar_alertas_antes_da_coleta(req, "tenant-a", queue, job["id"], "current")
    assert queue.summary()["lojas"][0]["ultimo_erro"]


@pytest.mark.parametrize("status", [200, 401, 403])
def test_central_connection_never_uses_local_refresh_or_invalidation(context, status):
    req, state = context
    state["cfg"] = {"central": True, "access_token": "opaque-fixture-reference"}
    state["responses"] = [response(status, {"data": []})]
    if status == 200:
        sync_engine._verificar_conexao_bling(req, "tenant-a")
    else:
        with pytest.raises(VendasDomainError):
            sync_engine._verificar_conexao_bling(req, "tenant-a")
    assert not state["refreshes"] and not state["marked"]


def test_redirected_response_cannot_confirm_a_different_origin(context):
    req, state = context
    state["responses"] = [response(body={"data": []}, url="https://example.invalid/Api/v3/pedidos/vendas")]
    with pytest.raises(VendasDomainError, match="Destino"):
        sync_engine._verificar_conexao_bling(req, "tenant-a")


def test_no_warning_does_not_add_a_provider_request(context):
    req, state = context
    queue = SyncQueue("tenant-a")
    job = queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim)
    queue.claim("current")
    sync_engine._verificar_alertas_antes_da_coleta(req, "tenant-a", queue, job["id"], "current")
    assert not state["calls"]


def test_worker_checks_before_collection_and_preserves_historical_diagnostics(context, monkeypatch):
    req, state = context
    queue, job = pending(req)
    state["responses"] = [response(body={"data": []})]
    collected = []
    async def collect(_req, _client):
        assert len(state["calls"]) == 1
        saved = queue.get_job(job["id"])
        assert saved["dias_concluidos"] == [req.data_inicio]
        assert saved["last_error"]
        assert queue.summary()["lojas"][0]["ultimo_erro"] is None
        collected.append(True)
        queue.complete_day(job["id"], "current", req.data_fim, {"vendas": 1})
        queue.complete_job(job["id"], "current")
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", collect)
    sync_engine._sincronizar_vendas_thread_worker(req, "tenant-a", job["id"], "current")
    assert collected == [True] and queue.get_job(job["id"])["status"] == "complete"


def test_new_collection_failure_is_visible_even_if_the_old_message_repeats(context, monkeypatch):
    req, state = context
    queue, job = pending(req)
    state["responses"] = [response(body={"data": []})]
    async def fail(_req, _client):
        assert queue.summary()["lojas"][0]["ultimo_erro"] is None
        raise VendasDomainError(503, "new provider outage")
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", fail)
    sync_engine._sincronizar_vendas_thread_worker(req, "tenant-a", job["id"], "current")
    assert queue.get_job(job["id"])["status"] == "retry"
    assert queue.get_job(job["id"])["dias_concluidos"] == [req.data_inicio]
    assert queue.summary()["lojas"][0]["ultimo_erro"]


def test_failed_preflight_never_starts_collection(context, monkeypatch):
    req, state = context
    queue, job = pending(req)
    state["responses"] = [response(403)]
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", lambda *_: pytest.fail("must verify connection first"))
    sync_engine._sincronizar_vendas_thread_worker(req, "tenant-a", job["id"], "current")
    saved = queue.get_job(job["id"])
    assert saved["status"] == "retry" and saved["dias_concluidos"] == [req.data_inicio]
    assert queue.summary()["lojas"][0]["ultimo_erro"]


def test_pause_while_provider_reply_is_in_flight_does_not_start_collection(context, monkeypatch):
    req, state = context
    queue, job = pending(req)
    def pause():
        queue.pause_all()
        return response(body={"data": []})
    state["responses"] = [pause]
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", lambda *_: pytest.fail("paused job cannot collect"))
    sync_engine._sincronizar_vendas_thread_worker(req, "tenant-a", job["id"], "current")
    saved = queue.get_job(job["id"])
    assert saved["status"] == "paused" and saved["dias_concluidos"] == [req.data_inicio]
    assert queue.summary()["lojas"][0]["ultimo_erro"]
