from __future__ import annotations

import copy
import datetime as dt
import io
import json
import logging
import sqlite3
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

from backend.core import AppPaths
from backend.modules.vendas import dependencies, sync_automation, sync_engine
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.state import (
    SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS,
    SYNC_META, SYNC_PROGRESS, SYNC_THREAD_CONTEXT,
)
from backend.modules.vendas.sync_queue import SyncQueue
from backend.modules.vendas.sync_period import _ensure_sync_schema
from backend.schemas import VendasSyncRequest
from backend.services import bling, bling_vendas, integracoes


TENANT = "history-tenant"
START, END = "2024-10-01", "2026-10-21"
URL = "https://api.bling.com.br/Api/v3/pedidos/vendas"
OLD_ERROR = "A integracao esta indisponivel. A sincronizacao tentara novamente."


class HistoryLimitedBlingAdapter(BaseAdapter):
    """Model the observed 400 for a multi-year sales query, without networking."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def send(self, request, **_kwargs):
        parsed = urlsplit(request.url)
        assert request.method == "GET"
        assert (parsed.scheme, parsed.netloc, parsed.path) == (
            "https", "api.bling.com.br", "/Api/v3/pedidos/vendas",
        )
        query = parse_qs(parsed.query)
        status = 200
        if "dataInicial" in query and "dataFinal" in query:
            first = dt.date.fromisoformat(query["dataInicial"][0])
            last = dt.date.fromisoformat(query["dataFinal"][0])
            if (last - first).days > 366:
                status = 400
        payload = {"data": []} if status == 200 else {"error": {"type": "BAD_REQUEST"}}
        self.calls.append({"query": query, "status": status})
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response.headers["Content-Type"] = "application/json"
        # Use the actual streamed-response path in the adaptive GET helper,
        # rather than the SimpleNamespace doubles that bypass body consumption.
        response.raw = HTTPResponse(
            body=io.BytesIO(json.dumps(payload).encode()),
            status=status, preload_content=False,
            headers=response.headers, request_url=request.url,
        )
        return response

    def close(self):
        pass


def plans(queue):
    with sqlite3.connect(queue.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY store_id")]


@pytest.fixture
def history_environment(tmp_path, monkeypatch):
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info",
                           logger=logging.getLogger("preflight-history"))
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=paths.logger, legacy=dependencies.LegacyBlingVendasAdapter(),
    ))
    for name in ("_RUNNING", "_AUTH", "_CHECKING", "_CHECK_AUTH", "_CENTRAL_CHECK_AUTH", "_STORE_CHECK_AUTH"):
        monkeypatch.setattr(sync_automation, name, {})
    monkeypatch.setattr(sync_automation, "_STOP", threading.Event())
    monkeypatch.setattr(sync_automation, "start", lambda: None)
    monkeypatch.setattr(sync_automation, "current", lambda *_: None)
    monkeypatch.setattr(bling_vendas, "get_tenant_path", paths.tenant_path)
    monkeypatch.setattr(bling, "_BLING_ACCOUNT_NEXT_CALL", {})
    monkeypatch.setattr(bling, "BLING_ACCOUNT_MIN_INTERVAL_SECONDS", 0)
    adapter, session = HistoryLimitedBlingAdapter(), requests.Session()
    session.trust_env = False
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    monkeypatch.setattr(bling, "BLING_SESSION", session)

    def unexpected_oauth(*_args, **_kwargs):
        pytest.fail("valid probes must not renew tokens, and invalidated accounts require user authorization")

    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", unexpected_oauth)
    monkeypatch.setattr(bling_vendas, "_bling_marcar_oauth_invalido", unexpected_oauth)
    stores, requests_by_store = {}, {}
    for index in range(11):
        store_id = f"history-store-{index:02}"
        cfg = {
            "id": "fixture-app", "secret": "fixture-secret",
            "access_token": "fixture-access-" + store_id,
            "refresh_token": "fixture-refresh-" + store_id,
            "oauth_connection_id": "fixture-connection-" + store_id,
            "empresa_id": str(1000 + index),
            "empresa_oauth_connection_id": "fixture-connection-" + store_id,
            "connected": True, "oauth_invalid": False, "status": "conectado",
        }
        if index >= 9:
            cfg.update(oauth_invalid=True, status="reautenticacao_necessaria", connected=False)
        stores[store_id] = {"store_id": store_id, "nome": f"Loja {index:02}", "integracoes": {"bling": cfg}}
        requests_by_store[store_id] = VendasSyncRequest(
            loja=stores[store_id]["nome"], store_id=store_id,
            data_inicio=START, data_fim=END,
        )

    def authorized_store(client_id, name, store_id=None):
        assert client_id == TENANT
        assert store_id in stores
        assert name == stores[store_id]["nome"]
        return copy.deepcopy(stores[store_id])

    monkeypatch.setattr(integracoes, "buscar_loja_snapshot", authorized_store)
    values = [SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS]
    previous = [dict(value) for value in values]
    for value in values:
        value.clear()
    queue = SyncQueue(TENANT)
    for req in requests_by_store.values():
        job = queue.enqueue(req.store_id, req.loja, START, END, automatico=True)
        claimed = queue.claim("history-seed")
        assert claimed["id"] == job["id"]
        # A day acknowledgement represents an actual commercial commit. Keep
        # that database so the worker's recovery guard can verify the evidence.
        database = bling_vendas._get_vendas_db_path(TENANT, req.loja)
        with sqlite3.connect(database) as conn:
            _ensure_sync_schema(conn.cursor())
            conn.executemany("""INSERT INTO vendas
                (id_unico, data, store_id, loja_conta, sku, quantidade, valor)
                VALUES (?, ?, ?, ?, 'fixture-sku', 1, 10)""",
                [(f"fixture-{index}", START, req.store_id, req.loja) for index in range(3)])
        queue.complete_day(job["id"], "history-seed", START, {
            "vendas": 3,
            "checkpoint": {"documentos_salvos": 3, "ultimo_tipo": "pedido", "ultimo_id": "fixture-123"},
        })
        queue.fail_job(job["id"], "history-seed", OLD_ERROR, retryable=False)
    queue.pause_all()
    release = threading.Event()
    try:
        yield SimpleNamespace(queue=queue, requests=requests_by_store, adapter=adapter,
                              release=release, valid=list(requests_by_store)[:9], invalid=list(requests_by_store)[9:])
    finally:
        sync_automation._STOP.set()
        queue.pause_all()
        release.set()
        for value in list(sync_automation._RUNNING.values()) + list(sync_automation._CHECKING.values()):
            value["thread"].join(timeout=5)
            assert not value["thread"].is_alive(), "fixture workers must stop"
        session.close()
        for value, snapshot in zip(values, previous):
            value.clear()
            value.update(snapshot)
        for name in ("job_id", "lease_check", "queue_job", "queue_owner", "loja"):
            if hasattr(SYNC_THREAD_CONTEXT, name):
                delattr(SYNC_THREAD_CONTEXT, name)


def test_real_preflight_does_not_apply_the_historical_collection_window(history_environment):
    state = history_environment
    req = state.requests[state.valid[0]]
    # Establish the transport's failure mode using the same real GET helper.
    rejected = bling._bling_get_with_adaptive_limit(
        URL, headers={"Authorization": "Bearer fixture-access"},
        params={"pagina": 1, "limite": 1, "dataInicial": START, "dataFinal": END}, max_attempts=1,
    )
    assert rejected.status_code == 400
    sync_engine._verificar_conexao_bling(req, TENANT)
    assert state.adapter.calls[-1] == {"query": {"pagina": ["1"], "limite": ["1"]}, "status": 200}
    assert (req.data_inicio, req.data_fim) == (START, END)


def test_historical_eleven_store_batch_acknowledges_nine_before_any_collection(history_environment, monkeypatch):
    state = history_environment
    for req in state.requests.values():
        state.queue.enqueue(req.store_id, req.loja, START, END, verify_connections=True)
    saved_jobs, saved_plans = state.queue.list_jobs(), plans(state.queue)
    assert state.queue.claim("must-wait-for-all-checks") is None
    grant = state.queue.claim_connection_check("history-checker")
    assert len(grant["stores"]) == 11
    assert all((store["data_inicio"], store["data_fim"]) == (START, END) for store in grant["stores"])
    # The coordinator, store authorization, provider preflight, refresh guard,
    # adaptive GET and streamed JSON consumption are all production functions.
    sync_automation._run_connection_check(TENANT, "history-checker", grant)

    assert len(state.adapter.calls) == 9, "invalidated accounts must not send a provider request"
    assert all(call == {"query": {"pagina": ["1"], "limite": ["1"]}, "status": 200}
               for call in state.adapter.calls), "a connection probe must not inherit a multi-year filter"
    assert all(not state.queue.connection_alerts(store) for store in state.valid)
    assert all(state.queue.connection_alerts(store) for store in state.invalid)
    assert state.queue.list_jobs() == saved_jobs, "checks must preserve status, errors, days and checkpoints"
    assert plans(state.queue) == saved_plans
    assert not state.queue.connection_check_pending()

    entered, observed, failures, lock = threading.Event(), [], [], threading.Lock()

    async def observe_collection(req, client_id, *, day_budget=None):
        try:
            assert client_id == TENANT
            assert len(state.adapter.calls) == 9
            assert all(not state.queue.connection_alerts(store) for store in state.valid)
            assert all(state.queue.connection_alerts(store) for store in state.invalid)
            assert plans(state.queue) == saved_plans
            with lock:
                observed.append(req.store_id)
                if len(observed) == 4:
                    entered.set()
            assert state.release.wait(5)
            sync_engine._verificar_cancelamento(client_id)
        except Exception as exc:
            # Expected teardown cancellation follows pause_all. Keep assertion
            # failures visible to the test instead of only to the worker's retry.
            if not isinstance(exc, VendasDomainError):
                failures.append(exc)
                entered.set()
            raise

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", observe_collection)
    started = sync_automation.dispatch(TENANT)
    assert len(started) == 4
    assert entered.wait(5)
    assert not failures
    assert len(observed) == 4 and set(observed).issubset(state.valid)
    assert len(state.adapter.calls) == 9, "claim must transfer the acknowledgement rather than re-probe"
    assert all(state.queue.get_job(job_id)["dias_concluidos"] == [START] for job_id in started)
