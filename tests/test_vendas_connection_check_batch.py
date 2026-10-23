from __future__ import annotations

import contextvars
import logging
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import dependencies, progress, sync_automation, sync_engine
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.state import (
    SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS,
    SYNC_META, SYNC_PROGRESS, SYNC_THREAD_CONTEXT,
)
from backend.modules.vendas.sync_queue import SyncQueue
from backend.modules.vendas.sync_period import _ensure_sync_schema
from backend.schemas import VendasSyncRequest
from backend.services import bling_vendas


@pytest.fixture
def environment(tmp_path, monkeypatch):
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logging.getLogger("batch-test"))
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(paths=paths, logger=paths.logger))
    for name in ("_RUNNING", "_AUTH", "_CHECKING", "_CHECK_AUTH", "_CENTRAL_CHECK_AUTH", "_STORE_CHECK_AUTH"):
        monkeypatch.setattr(sync_automation, name, {})
    monkeypatch.setattr(sync_automation, "_STOP", threading.Event())
    monkeypatch.setattr(sync_automation, "start", lambda: None)
    monkeypatch.setattr(sync_automation, "current", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_resolve_store", lambda req, _client: req)
    monkeypatch.setattr(bling_vendas, "get_tenant_path", paths.tenant_path)
    monkeypatch.setattr(sync_engine, "_get_vendas_db_path", bling_vendas._get_vendas_db_path)
    monkeypatch.setattr(sync_engine, "_listar_bancos_vendas_tenant", bling_vendas._listar_bancos_vendas_tenant)
    values = [SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS]
    previous = [dict(value) for value in values]
    for value in values:
        value.clear()
    queue = SyncQueue("tenant-a")
    requests = []
    for number in range(11):
        req = VendasSyncRequest(loja=f"Loja {number:02}", store_id=f"store-{number:02}",
                               data_inicio="2026-10-01", data_fim="2026-10-02", automatico=True)
        job = queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim, automatico=True)
        queue.claim("seed")
        database = bling_vendas._get_vendas_db_path("tenant-a", req.loja)
        with sqlite3.connect(database) as conn:
            _ensure_sync_schema(conn.cursor())
            conn.executemany("""INSERT INTO vendas
                (id_unico, data, store_id, loja_conta, sku, quantidade, valor)
                VALUES (?, ?, ?, ?, 'fixture-sku', 1, 10)""",
                [(f"fixture-{index}", req.data_inicio, req.store_id, req.loja) for index in range(2)])
        queue.complete_day(job["id"], "seed", req.data_inicio, {"vendas": 2})
        queue.fail_job(job["id"], "seed", "Aviso antigo preservado.", retryable=False)
        requests.append(req)
    def store(client, name, *, store_id):
        assert client == "tenant-a"
        req = next(req for req in requests if req.store_id == store_id)
        assert name == req.loja
        return {"nome": name, "store_id": store_id, "integracoes": {"bling": {
            "id": "fixture-app", "secret": "fixture-secret", "connected": True,
            "access_token": "fixture-access-" + store_id,
            "oauth_connection_id": "fixture-connection-" + store_id,
        }}}
    monkeypatch.setattr(sync_engine, "buscar_loja", store)
    queue.pause_all()
    release = threading.Event()
    state = SimpleNamespace(queue=queue, requests=requests, events=[], release=release)
    try:
        yield state
    finally:
        release.set()
        sync_automation._STOP.set()
        for value in list(sync_automation._CHECKING.values()) + list(sync_automation._RUNNING.values()):
            value["thread"].join(timeout=5)
            assert not value["thread"].is_alive(), "test worker must terminate"
        for value, snapshot in zip(values, previous):
            value.clear()
            value.update(snapshot)
        for name in ("job_id", "lease_check", "queue_job", "queue_owner", "loja"):
            if hasattr(SYNC_THREAD_CONTEXT, name):
                delattr(SYNC_THREAD_CONTEXT, name)


def request_check(state, *, requires_session=False):
    req = state.requests[0]
    return state.queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim,
                               automatico=True, requires_session=requires_session, verify_connections=True)


def finish_checks(state):
    for _ in range(5):
        if not state.queue.connection_check_pending():
            return
        sync_automation.dispatch("tenant-a")
        running = sync_automation._CHECKING.get("tenant-a")
        assert running is not None
        running["thread"].join(timeout=5)
        assert not running["thread"].is_alive()
    pytest.fail("connection phase never released the collection queue")


def test_all_eleven_alerted_stores_are_checked_before_any_collection(environment, monkeypatch):
    state = environment
    last_check = threading.Event()
    collected = threading.Event()

    def verify(req, client):
        assert client == "tenant-a"
        state.events.append(("check", req.store_id))
        # This is a second queue instance, as used by a scheduler/other process.
        assert SyncQueue(client).claim("competing-collector") is None
        if req.store_id == state.requests[-1].store_id:
            last_check.set()
            assert state.release.wait(5)

    async def collect(req, client):
        state.events.append(("collect", req.store_id))
        assert {store for kind, store in state.events if kind == "check"} == {r.store_id for r in state.requests}
        assert not state.queue.connection_alerts(req.store_id), "claim must keep the confirmed warning dismissed"
        collected.set()
        job_id, owner = SYNC_THREAD_CONTEXT.job_id, SYNC_THREAD_CONTEXT.queue_owner
        state.queue.complete_day(job_id, owner, req.data_fim, {"vendas": 1})
        state.queue.complete_job(job_id, owner)

    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_impl", collect)
    results = [sync_engine.sincronizar_vendas(req, "tenant-a") for req in state.requests]
    assert all(result["accepted"] for result in results)
    assert last_check.wait(5), "the scan must reach store 11 without waiting for the first two collections"
    assert not collected.is_set()
    assert not any(job["status"] == "running" for job in state.queue.list_jobs())
    state.release.set()
    finish_checks(state)
    assert all(store["ultimo_erro"] is None for store in state.queue.summary()["lojas"])
    sync_automation.dispatch("tenant-a")
    assert collected.wait(5)
    first_collection = next(index for index, event in enumerate(state.events) if event[0] == "collect")
    assert len([event for event in state.events[:first_collection] if event[0] == "check"]) == 11


@pytest.mark.parametrize("failed_status", [401, 403, 429, 503])
def test_one_failure_does_not_skip_other_checks_or_change_pending_work(environment, monkeypatch, failed_status):
    state = environment
    request_check(state)
    before = state.queue.list_jobs()
    plans_before = state.queue.summary()["lojas"]
    def verify(req, client):
        state.events.append(("check", req.store_id))
        if req.store_id == state.requests[1].store_id:
            raise VendasDomainError(failed_status, "fixture provider failure")
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    finish_checks(state)
    assert len(state.events) == 11
    assert state.queue.list_jobs() == before
    stores_after = state.queue.summary()["lojas"]
    for before_store, after_store in zip(plans_before, stores_after):
        assert {key: value for key, value in before_store.items() if key not in {"ultimo_erro", "aviso_conexao_verificado"}} == {
            key: value for key, value in after_store.items() if key not in {"ultimo_erro", "aviso_conexao_verificado"}}
        if after_store["store_id"] == state.requests[1].store_id:
            assert after_store["ultimo_erro"] == "Aviso antigo preservado."
        else:
            assert after_store["ultimo_erro"] is None
    assert state.queue.claim("valid-store-collector") is not None


def test_pause_during_last_reply_keeps_confirmed_warnings_hidden_and_preserves_remaining(environment, monkeypatch):
    state = environment
    request_check(state)
    before = state.queue.list_jobs()
    def verify(req, _client):
        state.events.append(("check", req.store_id))
        if req.store_id == state.requests[-1].store_id:
            state.queue.pause_all()
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    grant = state.queue.claim_connection_check("check-owner")
    sync_automation._run_connection_check("tenant-a", "check-owner", grant)
    assert state.queue.claim("must-not-resume") is None
    assert all(job["status"] == "paused" for job in state.queue.list_jobs())
    assert state.queue.summary()["habilitadas"] == 0
    assert state.queue.connection_alerts(state.requests[-1].store_id)
    assert all(not state.queue.connection_alerts(req.store_id) for req in state.requests[:-1])
    for old, new in zip(before, state.queue.list_jobs()):
        assert old["resultados_dias"] == new["resultados_dias"]
        assert old["last_error"] == new["last_error"]


def test_start_returns_while_provider_is_waiting_and_new_requests_keep_barrier(environment, monkeypatch):
    state = environment
    entered = threading.Event()
    returned = threading.Event()
    def verify(req, _client):
        entered.set()
        assert state.release.wait(5)
        state.events.append(("check", req.store_id))
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    def submit():
        sync_engine.sincronizar_vendas(state.requests[0], "tenant-a")
        returned.set()
    submitter = threading.Thread(target=submit)
    submitter.start()
    try:
        assert entered.wait(5)
        assert returned.wait(2), "POST must not wait for Bling responses"
        sync_engine.sincronizar_vendas(state.requests[1], "tenant-a")
        assert SyncQueue("tenant-a").claim("other-process") is None
    finally:
        state.release.set()
        submitter.join(timeout=5)
    finish_checks(state)
    assert len(state.events) == 11


def test_connection_phase_carries_initiating_request_context(environment, monkeypatch):
    state = environment
    authorization = contextvars.ContextVar("test-authorization", default=None)
    def verify(req, client):
        assert authorization.get() == "initiating-session"
        assert client == "tenant-a"
        state.events.append(("check", req.store_id))
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    token = authorization.set("initiating-session")
    try:
        sync_automation.remember_authorization("tenant-a", state.requests[0].store_id)
    finally:
        authorization.reset(token)
    request_check(state)
    finish_checks(state)
    assert len(state.events) == 11
    assert all(store["ultimo_erro"] is None for store in state.queue.summary()["lojas"])


def test_missing_central_session_never_falls_back_to_local_credentials(environment, monkeypatch):
    state = environment
    req = state.requests[1]
    state.queue.enqueue(req.store_id, req.loja, req.data_inicio, req.data_fim,
                        automatico=True, requires_session=True)
    request_check(state)
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling",
                        lambda req, _client: state.events.append(("check", req.store_id)))
    finish_checks(state)
    assert len(state.events) == 10
    assert ("check", req.store_id) not in state.events
    assert state.queue.connection_alerts(req.store_id)


def test_denied_store_keeps_its_warning_without_skipping_other_accounts(environment, monkeypatch):
    state = environment
    denied = state.requests[4].store_id
    request_check(state)
    def authorize(req, client):
        assert client == "tenant-a"
        if req.store_id == denied:
            raise VendasDomainError(404, "fixture access revoked")
        return req
    monkeypatch.setattr(sync_engine, "_resolve_store", authorize)
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling",
                        lambda req, _client: state.events.append(("check", req.store_id)))
    finish_checks(state)
    assert len(state.events) == 10
    assert ("check", denied) not in state.events
    assert state.queue.connection_alerts(denied)


def test_foreign_tenant_cannot_use_another_tenants_grant(environment, monkeypatch):
    state = environment
    request_check(state)
    grant = state.queue.claim_connection_check("same-owner")
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling",
                        lambda *_: pytest.fail("a foreign grant must not call Bling"))
    sync_automation._run_connection_check("tenant-b", "same-owner", grant)
    assert state.queue.owns_connection_check("same-owner", grant["generation"])
    assert all(store["ultimo_erro"] for store in state.queue.summary()["lojas"])


def test_monitor_shows_connection_phase_without_claiming_a_collection(environment):
    state = environment
    request_check(state)
    SYNC_PROGRESS["tenant-a"] = {"etapa": "Finalizado", "percentual": 100, "concluido": True}
    payload = progress.progresso_sincronizacao_vendas("tenant-a")
    assert payload["automacao"]["verificando_conexoes"] is True
    assert payload["active"] is True
    assert payload["active_count"] == 0
    assert payload["progress"]["etapa"] == "Conexões"
    assert payload["progress"]["concluido"] is False
    assert all(job["status"] != "running" for job in state.queue.list_jobs())
    progress.cancelar_sincronizacao_vendas("tenant-a")
    paused = progress.progresso_sincronizacao_vendas("tenant-a")
    assert paused["automacao"]["verificando_conexoes"] is False
    assert paused["active"] is False
    assert paused["progress"]["concluido"] is False


def test_thread_start_failure_leaves_barrier_recoverable_without_collecting(environment, monkeypatch):
    state = environment
    request_check(state)
    class BrokenThread:
        def __init__(self, **_kwargs): pass
        def start(self): raise RuntimeError("fixture thread failure")
    original = sync_automation.threading.Thread
    monkeypatch.setattr(sync_automation.threading, "Thread", BrokenThread)
    with pytest.raises(RuntimeError, match="thread failure"):
        sync_automation.dispatch("tenant-a")
    assert state.queue.connection_check_pending()
    assert state.queue.claim("must-not-collect") is None
    monkeypatch.setattr(sync_automation.threading, "Thread", original)
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", lambda *_: None)
    finish_checks(state)
    assert not state.queue.connection_check_pending()


@pytest.mark.parametrize("failure_boundary", ["probe", "acknowledgement"])
def test_unexpected_failure_abandons_phase_without_releasing_collection(environment, monkeypatch, failure_boundary):
    state = environment
    request_check(state)
    grant = state.queue.claim_connection_check("check-owner")
    def crash(*_args, **_kwargs):
        raise RuntimeError("fixture infrastructure failure")
    if failure_boundary == "probe":
        monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", crash)
    else:
        monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", lambda *_: None)
        monkeypatch.setattr(SyncQueue, "acknowledge_checked_connection", crash)
    with pytest.raises(RuntimeError, match="infrastructure failure"):
        sync_automation._run_connection_check("tenant-a", "check-owner", grant)
    assert state.queue.connection_check_pending()
    assert state.queue.claim("must-not-collect") is None
    assert all(store["ultimo_erro"] for store in state.queue.summary()["lojas"])


def test_central_batch_without_session_cannot_fall_back_to_historical_local_connections(environment, monkeypatch):
    state = environment
    request_check(state, requires_session=True)
    grant = state.queue.claim_connection_check("check-owner")
    assert grant["requires_session"] is True
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling",
                        lambda *_: pytest.fail("missing Central session must not try local credentials"))
    sync_automation._run_connection_check("tenant-a", "check-owner", grant)
    assert state.queue.connection_check_pending()
    assert state.queue.claim("must-wait-for-session") is None
    assert all(store["ultimo_erro"] for store in state.queue.summary()["lojas"])


@pytest.mark.parametrize("local_poll", [False, True])
def test_distinct_central_sessions_keep_each_stores_initiating_authorization(environment, monkeypatch, local_poll):
    state = environment
    session = contextvars.ContextVar("central-test-session", default=None)
    first, second = state.requests[:2]
    def client(name, store):
        return SimpleNamespace(name=name, stores=lambda: [{"store_id": store}])
    monkeypatch.setattr(sync_automation, "current", lambda *_: session.get())
    def resolve(req, _client):
        active = session.get()
        if not active or req.store_id not in {row["store_id"] for row in active.stores()}:
            raise VendasDomainError(403, "fixture store outside current session")
        return req
    def verify(req, _client):
        state.events.append((session.get().name, req.store_id))
    monkeypatch.setattr(sync_engine, "_resolve_store", resolve)
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    for name, req in [("session-a", first), ("session-b", second)]:
        token = session.set(client(name, req.store_id))
        try:
            sync_automation.remember_authorization("tenant-a", req.store_id)
        finally:
            session.reset(token)
    if local_poll:
        sync_automation.remember_authorization("tenant-a")
    request_check(state, requires_session=True)
    finish_checks(state)
    assert set(state.events) == {("session-a", first.store_id), ("session-b", second.store_id)}
    for req in [first, second]:
        assert not state.queue.connection_alerts(req.store_id)
    assert all(state.queue.connection_alerts(req.store_id) for req in state.requests[2:])


def test_stop_revokes_inflight_phase_without_losing_pending_work(environment, monkeypatch):
    state = environment
    request_check(state)
    before = state.queue.list_jobs()
    entered = threading.Event()
    def verify(*_args):
        entered.set()
        assert state.release.wait(5)
    monkeypatch.setattr(sync_engine, "_verificar_conexao_bling", verify)
    sync_automation.dispatch("tenant-a")
    assert entered.wait(5)
    sync_automation.stop()
    state.release.set()
    checking = sync_automation._CHECKING["tenant-a"]["thread"]
    checking.join(timeout=5)
    assert not checking.is_alive()
    assert state.queue.connection_check_pending()
    assert state.queue.claim("must-not-collect") is None
    assert state.queue.list_jobs() == before
