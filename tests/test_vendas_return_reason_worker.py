from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from backend.modules.vendas import return_reason_queue as queue
from backend.modules.vendas import return_reason_worker as worker
from backend.modules.vendas import return_reason_actions as actions


ORDER = "2000000000001"


def result(status="identified", source="mercado_livre", **changes):
    value = {"result_status": status, "reason_id": "PDD1", "reason_description": "Produto diferente",
             "reason_source": source, "order_id": ORDER, "retryable": False}
    value.update(changes)
    return value


def job(**changes):
    value = {"store_id": "store-a", "loja": "Nome antigo", "nota_id": "101", "sku": "200",
             "references": {}}
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def isolated_worker(monkeypatch):
    monkeypatch.setattr(worker, "_STOP", threading.Event())
    monkeypatch.setattr(worker, "_RUNNING", {})
    monkeypatch.setattr(worker, "_AUTH", {})
    monkeypatch.setattr(worker, "_BULK_AUTH", {})
    monkeypatch.setattr(worker, "_BULK_TURN", {})
    monkeypatch.setattr(worker, "_BULK_THREAD", None)
    monkeypatch.setattr(worker, "_BULK_WAKE", threading.Event())
    monkeypatch.setattr(worker, "_WAKE_TENANTS", set())
    monkeypatch.setattr(actions, "pending_operations", lambda tenant: [])
    monkeypatch.setattr(worker, "SYNC_CANCEL_FLAGS", {})
    monkeypatch.setattr(worker, "with_request_context", lambda function: function)


@pytest.fixture
def providers(monkeypatch):
    state = SimpleNamespace(ml_calls=[], bling_calls=[], stores=[],
                            ml_result=result(), bling_result=result("not_linked", "bling", order_id=""))

    def store(tenant, name, *, store_id):
        state.stores.append((tenant, name, store_id))
        return {"store_id": store_id, "nome": "Nome atual"}

    def ml(tenant, store_id, name, pending):
        state.ml_calls.append((tenant, store_id, name, deepcopy(pending)))
        return deepcopy(state.ml_result)

    def bling(tenant, store_id, name, pending):
        state.bling_calls.append((tenant, store_id, name, deepcopy(pending)))
        return deepcopy(state.bling_result)

    monkeypatch.setattr(worker, "buscar_loja", store)
    monkeypatch.setattr(worker, "resolve_reason", ml)
    monkeypatch.setattr(worker, "resolve_reference", bling)
    return state


def test_captured_order_goes_to_ml_first_and_current_store_name_is_used(providers):
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER}))
    assert actual == providers.ml_result
    assert len(providers.ml_calls) == 1 and providers.bling_calls == []
    assert providers.ml_calls[0][:3] == ("tenant-a", "store-a", "Nome atual")
    assert providers.ml_calls[0][3]["order_id"] == ORDER
    assert providers.stores == [("tenant-a", "Nome antigo", "store-a")]


def test_persisted_result_identifiers_do_not_become_new_documentary_evidence(providers):
    providers.bling_result = result("not_linked", "bling", order_id=ORDER)
    worker.resolve_job("tenant-a", job(
        order_id="999", claim_id="991", return_id="992", seller_id="993", site_id="MLM",
    ))
    assert providers.bling_calls[0][3].get("order_id") in (None, "")
    linked = providers.ml_calls[0][3]
    assert linked["order_id"] == ORDER
    assert all(key not in linked for key in ("claim_id", "return_id", "seller_id", "site_id"))


def test_only_identifiers_from_references_can_constrain_ml_match(providers):
    worker.resolve_job("tenant-a", job(
        claim_id="old-claim", seller_id="old-seller", site_id="MLM",
        references={"order_id": ORDER, "claim_id": "901", "return_id": "801"},
    ))
    linked = providers.ml_calls[0][3]
    assert (linked["claim_id"], linked["return_id"]) == ("901", "801")
    assert "seller_id" not in linked and "site_id" not in linked


def test_without_captured_order_resolves_bling_before_ml(providers):
    providers.bling_result = result("not_linked", "bling", order_id=ORDER)
    actual = worker.resolve_job("tenant-a", job())
    assert actual["reason_source"] == "mercado_livre"
    assert len(providers.bling_calls) == len(providers.ml_calls) == 1
    assert providers.bling_calls[0][:3] == ("tenant-a", "store-a", "Nome atual")


def test_unlinked_bling_document_never_triggers_ml_by_sku_or_old_order(providers):
    actual = worker.resolve_job("tenant-a", job(order_id=ORDER))
    assert actual == providers.bling_result
    assert providers.ml_calls == [] and len(providers.bling_calls) == 1


@pytest.mark.parametrize("captured", [False, True])
def test_ml_reason_has_priority_and_explicit_bling_text_is_fallback(providers, captured):
    providers.bling_result = result(source="bling", reason_id="explicit_text")
    references = {"order_id": ORDER} if captured else {}
    assert worker.resolve_job("tenant-a", job(references=references))["reason_source"] == "mercado_livre"
    providers.ml_calls.clear()
    providers.bling_calls.clear()
    providers.ml_result = result("not_informed", reason_id="", reason_description="")
    assert worker.resolve_job("tenant-a", job(references=references))["reason_source"] == "bling"
    assert len(providers.ml_calls) == 1, "A mesma referência não deve consultar o ML duas vezes."


def test_transient_ml_failure_preserves_retry_without_extra_bling_calls(providers):
    providers.ml_result = result("unavailable", retryable=True, error_code="rate_limited")
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER}))
    assert actual["retryable"] and providers.bling_calls == []


@pytest.mark.parametrize("captured", [False, True])
@pytest.mark.parametrize("status,code", [("ambiguous", "multiple_returns"),
                                         ("unavailable", "claim_scope_mismatch")])
def test_conflicting_ml_evidence_is_not_hidden_by_bling_fallback(providers, captured, status, code):
    providers.ml_result = result(status, error_code=code)
    providers.bling_result = result(source="bling", reason_id="explicit_text")
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER} if captured else {}))
    assert actual["result_status"] == status and actual["error_code"] == code


def test_cancellation_between_providers_preserves_the_pending_job(providers, monkeypatch):
    def cancel_after_ml(*_args):
        worker.SYNC_CANCEL_FLAGS["tenant-a"] = True
        return result("not_informed")
    monkeypatch.setattr(worker, "resolve_reason", cancel_after_ml)
    try:
        actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER}))
        assert actual["error_code"] == "worker_stopped"
        assert providers.bling_calls == []
    finally:
        worker.SYNC_CANCEL_FLAGS.pop("tenant-a", None)


def test_identity_mismatch_stops_both_providers(providers, monkeypatch):
    monkeypatch.setattr(worker, "buscar_loja", lambda *args, **kwargs: {"store_id": "other", "nome": "Outra"})
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER}))
    assert actual["error_code"] == "store_identity_mismatch"
    assert providers.ml_calls == providers.bling_calls == []


@pytest.mark.parametrize("stop_kind", ["shutdown", "sync_cancel"])
def test_stop_or_sync_cancellation_prevents_resolution_and_dispatch(providers, monkeypatch, stop_kind):
    if stop_kind == "shutdown": worker.stop()
    else: worker.SYNC_CANCEL_FLAGS["tenant-a"] = True
    monkeypatch.setattr(worker, "_due_stores", lambda tenant: pytest.fail("Canceled task was dispatched"))
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER}))
    worker.dispatch("tenant-a")
    assert actual["result_status"] == "unavailable" and actual["retryable"]
    assert providers.ml_calls == providers.bling_calls == [] and worker._RUNNING == {}


@pytest.fixture
def fake_threads(monkeypatch):
    threads = []

    class Thread:
        def __init__(self, target, args, name, daemon):
            self.target, self.args, self.name, self.daemon = target, args, name, daemon
            self.alive = False
            threads.append(self)

        def is_alive(self):
            return self.alive

        def start(self):
            self.alive = True

    monkeypatch.setattr(worker.threading, "Thread", Thread)
    monkeypatch.setattr(worker, "_due_stores", lambda tenant: [(tenant + ".db", "store-a"), (tenant + ".db", "store-b")])
    for tenant in ("tenant-a", "tenant-b", "tenant-c"):
        worker.remember_authorization(tenant, "store-a")
        worker.remember_authorization(tenant, "store-b")
    return threads


def test_dispatch_respects_two_global_slots_one_per_tenant_and_releases_finished_slots(fake_threads):
    worker.dispatch("tenant-a")
    worker.dispatch("tenant-a")
    worker.dispatch("tenant-b")
    worker.dispatch("tenant-c")
    assert len(fake_threads) == 2
    assert set(worker._RUNNING) == {("tenant-a", "store-a"), ("tenant-b", "store-a")}
    fake_threads[0].alive = False
    worker.dispatch("tenant-c")
    assert len(fake_threads) == 3
    assert set(worker._RUNNING) == {("tenant-b", "store-a"), ("tenant-c", "store-a")}
    assert all(thread.daemon and thread.name == "vendas-motivos" for thread in fake_threads)


def test_dispatch_uses_captured_authorization_context(fake_threads, monkeypatch):
    captured = []
    def wrap(function):
        def bound(*args):
            return function(*args)
        captured.append(bound)
        return bound
    monkeypatch.setattr(worker, "with_request_context", wrap)
    worker.remember_authorization("tenant-a", "store-a")
    worker.dispatch("tenant-a")
    assert fake_threads[0].target is captured[0] and len(captured) == 1


def test_failed_thread_start_releases_registered_slot(fake_threads, monkeypatch):
    def fail(self):
        raise RuntimeError("synthetic thread startup failure")
    monkeypatch.setattr(worker.threading.Thread, "start", fail)
    try:
        worker.dispatch("tenant-a")
    except RuntimeError:
        pass
    assert worker._RUNNING == {}


def _queue_db(path, rows=(), *, schema="valid"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        if schema == "none":
            connection.execute("CREATE TABLE commercial (id TEXT)")
        elif schema == "invalid":
            connection.execute("CREATE TABLE devolucao_motivos (store_id TEXT)")
        else:
            queue.ensure_schema(connection)
            connection.executemany("""INSERT INTO devolucao_motivos
                (store_id,item_id_unico,nota_id,loja,sku,reference_json,fingerprint,status,
                 next_attempt_at,lease_until,created_at,updated_at)
                VALUES (?,?, '101','Loja','200','{}','fingerprint',?,?,?,0,0)""", rows)
    return path


def test_due_stores_reads_only_owned_regular_databases_with_valid_queue_schema(tmp_path, monkeypatch):
    tenant = tmp_path / "tenant-a"
    valid = _queue_db(tenant / "vendas_historico.db", [
        ("pending", "p", "pending", 1, None), ("retry", "r", "retry", 1, None),
        ("refresh", "d", "done", 1, None), ("expired", "e", "processing", None, 99),
        ("future", "f", "retry", 200, None), ("leased", "l", "processing", None, 200),
        ("obsolete", "o", "obsolete", 1, None),
    ])
    outside = _queue_db(tmp_path / "tenant-b" / "vendas_historico.db", [("foreign", "x", "pending", 1, None)])
    no_table = _queue_db(tenant / "vendas_historico_sem_fila.db", schema="none")
    invalid = _queue_db(tenant / "vendas_historico_parcial.db", schema="invalid")
    symbolic = _queue_db(tenant / "vendas_historico_link.db", [("link", "x", "pending", 1, None)])
    junction = _queue_db(tenant / "vendas_historico_junction.db", [("junction", "x", "pending", 1, None)])
    monkeypatch.setattr(worker, "get_tenant_path", lambda client: str(tenant))
    monkeypatch.setattr(worker, "_listar_bancos_vendas_tenant", lambda client: [
        str(outside), str(symbolic), str(junction), str(no_table), str(invalid), str(valid), str(tenant / "missing.db"),
    ])
    original_symlink = Path.is_symlink
    original_junction = Path.is_junction
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == symbolic or original_symlink(path))
    monkeypatch.setattr(Path, "is_junction", lambda path: path == junction or original_junction(path))
    monkeypatch.setattr(worker.time, "time", lambda: 100)
    assert worker._due_stores("tenant-a") == [(str(valid), store) for store in ("expired", "pending", "refresh", "retry")]


def test_worker_failure_keeps_committed_lease_for_later_recovery(tmp_path, monkeypatch, caplog):
    path = _queue_db(tmp_path / "vendas_historico.db", [("store-a", "item-a", "pending", 1, None)])
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notas_entrada_itens (store_id TEXT,id_unico TEXT,devolucao INTEGER)")
        connection.execute("INSERT INTO notas_entrada_itens VALUES ('store-a','item-a',1)")

    def crash_after_lease(db_path, store_id, resolver, limit, *, legacy_owner=None):
        assert limit == 5
        with sqlite3.connect(db_path) as connection:
            assert queue.claim(connection, store_id, now=100, lease_seconds=10)
        raise RuntimeError("private provider payload must not be logged")

    monkeypatch.setattr(worker, "process_due", crash_after_lease)
    worker._process_store("tenant-a", str(path), "store-a")
    with sqlite3.connect(path) as connection:
        status, lease, until = connection.execute("SELECT status,lease_token,lease_until FROM devolucao_motivos").fetchone()
        assert status == "processing" and lease and until == 110
        assert queue.claim(connection, "store-a", now=109) is None
        recovered = queue.claim(connection, "store-a", now=110)
        assert recovered and recovered["lease_token"] != lease
    assert caplog.text == ""


@pytest.mark.parametrize("captured", [False, True])
def test_oauth_failure_is_not_presented_as_bling_reason(providers, captured):
    providers.ml_result = result("reconnect_required", reason_id=None, reason_description=None)
    providers.bling_result = result(source="bling")
    actual = worker.resolve_job("tenant-a", job(references={"order_id": ORDER} if captured else {}))
    assert actual["result_status"] == "reconnect_required"
    if captured:
        assert providers.bling_calls == []


def test_restart_without_captured_authorization_never_falls_back_to_local_context(fake_threads, monkeypatch):
    worker._AUTH.clear()
    monkeypatch.setattr(actions, "pending_operations", lambda tenant: [("scope", True)])
    monkeypatch.setattr(worker, "_due_stores", lambda tenant: pytest.fail("Unauthenticated queue discovery"))
    for bulk_only in (True, False):
        worker.dispatch("tenant-a", bulk_only=bulk_only)
    assert fake_threads == [] and worker._RUNNING == {}


def test_historical_dispatch_uses_captured_context_and_does_not_dispatch_unrelated_queue(fake_threads, monkeypatch):
    monkeypatch.setattr(actions, "pending_operations", lambda tenant: [("scope", True)])
    monkeypatch.setattr(worker, "_due_stores", lambda tenant: pytest.fail("Bulk loop entered automatic queue"))
    worker.remember_bulk_authorization("tenant-a", "scope")
    worker.dispatch("tenant-a", bulk_only=True)
    assert len(fake_threads) == 1
    assert fake_threads[0].target is worker._BULK_AUTH[("tenant-a", "scope")]
    assert fake_threads[0].args == ("tenant-a", "scope")


def test_expired_captured_central_context_never_reaches_bulk_resolver(fake_threads, monkeypatch):
    from backend.services import central_accounts_client as central
    from backend.modules.vendas.errors import VendasDomainError
    monkeypatch.setattr(worker, "with_request_context", central.with_request_context)
    monkeypatch.setattr(actions, "pending_operations", lambda tenant: [("scope", True)])
    marker = SimpleNamespace(tenant="tenant-a", expires_at=1)
    seen = []
    def advance(tenant, scope, resolver):
        captured = central.current(tenant)
        seen.append(captured)
        assert captured is marker
        raise VendasDomainError(401, "Sessão expirada")
    monkeypatch.setattr(actions, "advance_search", advance)
    monkeypatch.setattr(worker, "resolve_job", lambda *args: pytest.fail("Expired context reached provider"))
    token = central._current.set(marker)
    try:
        worker.remember_bulk_authorization("tenant-a", "scope")
    finally:
        central._current.reset(token)
    worker.dispatch("tenant-a", bulk_only=True)
    fake_threads[0].target(*fake_threads[0].args)
    assert seen == [marker]
