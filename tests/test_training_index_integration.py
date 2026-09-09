"""Read-path acceptance tests: real persistence, HTTP auth and contended writers."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.modules.context_hub import api as hub
from backend.modules.context_hub import training_read_index as index
from backend.modules.context_hub.store_sku_contracts import STORE_SKU_PUBLIC_SURFACE


def scope(tenant="tenant-a", store="store-a", seller="100"):
    return {"tenant_scope": "tenant:" + tenant, "store_ref": store,
            "store_name": "Mesma loja", "seller_id": seller, "site_id": "MLB",
            "surface": STORE_SKU_PUBLIC_SURFACE}


def publish(info, identity=None, *, revision="revision-old", count=2, notes=2):
    identity = identity or scope()
    products = {f"{i:05}": {"sku": f"{i:05}", "catalog_found": True,
                            "catalog_document": {"name": f"Produto {i}"},
                            "characteristics": [], "evidence": []} for i in range(count)}
    editor = {"guidance": {"general": {}},
              "editorial": {"revision": revision, "skus": {
                  f"{i:05}": {"status": "draft", "source_body": f"Nota {i}"} for i in range(notes)}},
              "sku_guidance": {f"{i:05}": {"notas": revision + f":{i}"} for i in range(notes)}}
    meta = index.publish_generation(identity["tenant_scope"].split(":", 1)[1], identity,
                                    editor, products, info_root=info)
    return meta


@pytest.fixture
def info(tmp_path):
    root = tmp_path / "info"
    root.mkdir()
    hub.stop_all_context_hub_watchers()
    hub.configure_context_hub(base_dir=tmp_path, info_root=root, surface="test")
    yield root
    hub.stop_all_context_hub_watchers()


@pytest.fixture
def authenticated_api(info, monkeypatch):
    from backend.modules.perguntas_pos_venda.endpoints import security
    from backend.modules.context_hub import training_index_worker
    from backend.routers.perguntas_pos_venda import create_perguntas_pos_venda_router
    from backend.services import central_accounts_client as central

    def session(tenant, store, seller):
        stores = [{"nome": "Mesma loja", "store_id": store, "integracoes": {
            "mercadolivre": {"central": True, "connected": True,
                             "user_id": seller, "site_id": "MLB"}}}]
        return SimpleNamespace(tenant=tenant, username="synthetic-user", expires_at=time.time() + 600,
                               public_stores=lambda: copy.deepcopy(stores))

    sessions = {"session-a": session("tenant-a", "store-a", "100"),
                "session-b": session("tenant-a", "store-b", "200"),
                "session-c": session("tenant-b", "store-a", "100")}

    async def authenticate(request, authorization):
        token = (authorization or "").removeprefix("Bearer ")
        selected = sessions.get(token)
        if not selected:
            raise HTTPException(401, "Authentication required")
        central._current.set(selected)
        request.state.client_id = selected.tenant
        request.state.username = selected.username
        return selected.tenant

    monkeypatch.setattr(security, "runtime_dependency", lambda name: authenticate)
    # Queue scheduling is tested with the worker separately. This suite exercises
    # the actual route, authorization projection and SQLite query on its thread.
    enqueued = []
    real_enqueue = training_index_worker.request_refresh
    monkeypatch.setattr(training_index_worker, "request_refresh",
                        lambda *args, **kwargs: enqueued.append((args, kwargs)))
    app = FastAPI()
    app.include_router(create_perguntas_pos_venda_router())
    with TestClient(app) as client:
        client.real_enqueue = real_enqueue
        yield client, enqueued, sessions


URL = "/api/mercadolivre/ia-treinamento/ficha"


def get(client, *, token="session-a", store="store-a", sku="00001"):
    return client.get(URL, params={"store_id": store, "sku": sku},
                      headers={"Authorization": "Bearer " + token} if token else {})


def test_http_checks_current_session_before_index_access(info, authenticated_api, monkeypatch):
    client, queued, sessions = authenticated_api
    for identity in (scope(), scope(store="store-b", seller="200"), scope("tenant-b")):
        publish(info, identity, revision=identity["tenant_scope"] + identity["store_ref"])
    original = index.read_ficha
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(index, "read_ficha", read)
    assert get(client, token="").status_code == 401
    assert get(client, token="invalid").status_code == 401
    assert get(client, store="store-b").status_code == 403
    assert get(client, store="Mesma loja").status_code == 403
    assert get(client, store="STORE-A").status_code == 403
    assert not calls and not queued
    for token, store, expected in (("session-a", "store-a", "tenant:tenant-astore-a"),
                                   ("session-b", "store-b", "tenant:tenant-astore-b"),
                                   ("session-c", "store-a", "tenant:tenant-bstore-a")):
        response = get(client, token=token, store=store)
        assert response.status_code == 200, response.text
        assert response.json()["notas_sku"]["00001"] == expected + ":1"
    sessions["session-a"].expires_at = time.time() - 1
    assert get(client).status_code == 401
    assert len(calls) == len(queued) == 3


def test_exact_sku_and_seller_are_not_coerced_or_reused(info, authenticated_api):
    client, _, sessions = authenticated_api
    publish(info)
    assert get(client).json()["sku_known"] is True
    missing = get(client, sku="1").json()
    assert missing["sku_known"] is False and missing["notas_sku"] == {"1": ""}
    # Reconnecting the same store to a different seller cannot reuse its old projection.
    rows = sessions["session-a"].public_stores()
    rows[0]["integracoes"]["mercadolivre"]["user_id"] = "999"
    sessions["session-a"].public_stores = lambda: rows
    assert get(client).status_code == 503


def test_cold_read_reports_preparation_without_creating_index(info, authenticated_api):
    client, queued, _ = authenticated_api
    response = get(client)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.json()["detail"]["code"] == "training_index_initializing"
    assert len(queued) == 1
    assert not index.index_path("tenant-a", info_root=info).exists()


def test_refresh_is_authorized_queued_and_keeps_published_generation(info, authenticated_api):
    client, queued, _ = authenticated_api
    old = publish(info)
    endpoint = URL + "/atualizar"
    body = {"store_id": "store-a", "sku": "00001"}
    assert client.post(endpoint, json=body).status_code == 401
    headers = {"Authorization": "Bearer session-a"}
    assert client.post(endpoint, headers=headers, json={**body, "store_id": "store-b"}).status_code == 403
    for forged in ({"client_id": "tenant-b"}, {"seller_id": "200"}, {"surface": "private"}):
        assert client.post(endpoint, headers=headers, json={**body, **forged}).status_code == 422
    assert not queued
    response = client.post(endpoint, headers=headers, json=body)
    assert response.status_code == 202, response.text
    assert response.json()["store_id"] == "store-a" and response.json()["sku"] == "00001"
    assert len(queued) == 1 and queued[0][1]["immediate"] is True
    assert queued[0][0][0] == "tenant-a" and queued[0][0][1] == scope()
    assert index.index_status("tenant-a", scope(), info_root=info)["generation"] == old["generation"]


def test_central_session_never_resolves_private_legacy_profile_by_display_name(info, authenticated_api):
    client, _, _ = authenticated_api
    publish(info)
    legacy = info / "tenant-a" / "ia_treinamento_perguntas_pos_venda.json"
    legacy.write_text(json.dumps({"por_loja": {"Mesma loja": {
        "loja": "Mesma loja", "orientacoes_pos_venda": "Orientacao de outra loja homonima"}}}), encoding="utf-8")
    response = client.get(URL.removesuffix("/ficha"), params={"store_id": "store-a"},
                          headers={"Authorization": "Bearer session-a"})
    assert response.status_code == 200, response.text
    assert response.json()["orientacoes_pos_venda"] == ""
    assert "Orientacao de outra loja homonima" not in response.text


def test_legacy_profile_with_contradictory_store_identity_is_not_displayed(info, authenticated_api):
    client, _, _ = authenticated_api
    publish(info)
    legacy = info / "tenant-a" / "ia_treinamento_perguntas_pos_venda.json"
    legacy.write_text(json.dumps({"por_loja": {"store_id:store-a": {
        "store_id": "store-b", "orientacoes_pos_venda": "Nao exibir"}}}), encoding="utf-8")
    response = client.get(URL.removesuffix("/ficha"), params={"store_id": "store-a"},
                          headers={"Authorization": "Bearer session-a"})
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "training_profile_identity_conflict"
    assert "Nao exibir" not in response.text


def test_authenticated_ficha_p95_below_500ms_with_10000_notes_30000_products(
        info, authenticated_api, monkeypatch):
    from backend.modules.context_hub import catalog_product_repository, obsidian_store_sku_index, store_sku_editor
    from backend.services import integracoes
    client, _, _ = authenticated_api
    notes = info / "tenant-a" / "ContextVault" / "80_Curadoria" / "scale-fixture"
    notes.mkdir(parents=True)
    for i in range(10000):
        (notes / f"{i:05}.md").write_text(f"Nota sintetica SKU {i:05}\n", encoding="utf-8")
    publish(info, count=30000, notes=10000)

    def forbidden(*args, **kwargs):
        raise AssertionError("The indexed HTTP read touched a canonical loader or scanned the vault")

    monkeypatch.setattr(Path, "rglob", forbidden)
    monkeypatch.setattr(catalog_product_repository, "load_catalog_product", forbidden)
    monkeypatch.setattr(obsidian_store_sku_index, "load_store_sku_index_entry", forbidden)
    monkeypatch.setattr(store_sku_editor, "load_store_guidance_editor", forbidden)
    monkeypatch.setattr(integracoes, "carregar_lojas", forbidden)
    samples = []
    for i in range(50):
        sku = f"{(i * 601) % 30000:05}"
        start = time.perf_counter()
        response = get(client, sku=sku)
        samples.append(time.perf_counter() - start)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["sku"] == sku and payload["sku_known"]
        assert list(payload["editorial"]["skus"]) == [sku]
        assert len(response.content) < 10000
    p95 = sorted(samples)[47]
    print(f"indexed_authenticated_ficha requests=50 notes=10000 products=30000 p95_ms={p95 * 1000:.2f}")
    assert p95 < .5


def _hold_writers(info, ready, release):
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.locking import _exclusive_file_lock
    from backend.services.store_coordination import store_lock, coordinated_path_lock
    paths = _tenant_paths("tenant-a", info_root=Path(info))
    with ExitStack() as stack:
        stack.enter_context(store_lock(paths.tenant_dir))
        stack.enter_context(coordinated_path_lock(paths.tenant_dir / "cadastro.csv"))
        stack.enter_context(_exclusive_file_lock(paths))
        connection = sqlite3.connect(index.index_path("tenant-a", info_root=Path(info)))
        stack.callback(connection.close)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE training_scopes SET revision='uncommitted'")
        ready.set()
        if not release.wait(15):
            raise AssertionError("test writer release timed out")
        connection.rollback()


@pytest.mark.parametrize("writer_kind", ["thread", "process"])
def test_ready_http_read_ignores_contended_business_and_publication_locks(
        info, authenticated_api, writer_kind):
    client, _, _ = authenticated_api
    meta = publish(info)
    ctx = multiprocessing.get_context("spawn") if writer_kind == "process" else threading
    ready, release = ctx.Event(), ctx.Event()
    cls = ctx.Process if writer_kind == "process" else threading.Thread
    writer = cls(target=_hold_writers, args=(str(info), ready, release))
    writer.start()
    try:
        assert ready.wait(10), "writer failed to acquire test locks"
        started = time.perf_counter()
        response = get(client)
        elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        assert response.json()["snapshot"]["generation"] == meta["generation"]
        assert response.json()["expected_revision"] == "revision-old"
        assert elapsed < .5
    finally:
        release.set()
        writer.join(10)
        if writer_kind == "process" and writer.is_alive():
            writer.terminate()
            writer.join(5)
    assert not writer.is_alive()
    if writer_kind == "process":
        assert writer.exitcode == 0


def test_ready_http_read_enqueues_real_worker_while_hub_writer_is_blocked(info, authenticated_api, monkeypatch):
    from backend.modules.context_hub import training_index_worker as worker
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.locking import _exclusive_file_lock
    client, _, _ = authenticated_api
    # Initialize the canonical skeleton outside the measured HTTP request.
    hub.bootstrap_context_hub("tenant-a", info_root=info)
    old = publish(info)
    paths = _tenant_paths("tenant-a", info_root=info)
    ready, release, worker_attempted = threading.Event(), threading.Event(), threading.Event()
    holder_errors = []

    def hold_hub():
        try:
            with _exclusive_file_lock(paths):
                ready.set()
                assert release.wait(30)
        except BaseException as exc:
            holder_errors.append(exc)

    @contextmanager
    def observe_worker_lock(target, **kwargs):
        if target.lock_path == paths.lock_path:
            worker_attempted.set()
        with _exclusive_file_lock(target, **kwargs):
            yield

    monkeypatch.setattr(worker, "request_refresh", client.real_enqueue)
    monkeypatch.setattr(worker, "_exclusive_file_lock", observe_worker_lock)
    holder = threading.Thread(target=hold_hub)
    holder.start()
    try:
        assert ready.wait(5)
        started = time.perf_counter()
        response = get(client)
        elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        assert response.json()["snapshot"]["generation"] == old["generation"]
        assert elapsed < .5
        assert worker_attempted.wait(5), "real worker did not reach the contended Hub source lock"
        assert not release.is_set()
    finally:
        release.set()
        holder.join(5)
        worker.stop_workers(timeout=10)
    assert not holder.is_alive()
    assert holder_errors == []
    with worker._GUARD:
        assert not worker._JOBS


def _read_after_restart(info, result):
    try:
        payload = index.read_ficha("tenant-a", scope(), "00001", info_root=Path(info))
        result.put((payload["snapshot"]["generation"], payload["notas_sku"]["00001"]))
    except Exception as exc:
        result.put(("error", type(exc).__name__))


def _crash_during_publication(info):
    """Exit after SKU rows change, before the scope pointer can be committed."""
    import os
    from contextlib import contextmanager
    original = index._db

    class CrashConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, key):
            return getattr(self.connection, key)

        def executemany(self, *args, **kwargs):
            self.connection.executemany(*args, **kwargs)
            os._exit(17)

    @contextmanager
    def connection(*args, **kwargs):
        with original(*args, **kwargs) as current:
            yield CrashConnection(current) if kwargs.get("write") else current

    index._db = connection
    publish(Path(info), revision="uncommitted-revision")


def test_process_crash_between_rows_and_generation_preserves_previous_snapshot(info):
    old = publish(info)
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_crash_during_publication, args=(str(info),))
    child.start()
    try:
        child.join(15)
        assert child.exitcode == 17
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
    restored = index.read_ficha("tenant-a", scope(), "00001", info_root=info)
    assert restored["snapshot"]["generation"] == old["generation"]
    assert restored["notas_sku"] == {"00001": "revision-old:1"}
    new = publish(info, revision="recovered")
    assert new["generation"] != old["generation"]


def test_failed_sql_publication_preserves_generation_after_process_restart(info):
    old = publish(info)
    db_path = index.index_path("tenant-a", info_root=info)
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TRIGGER fail_scope_publish BEFORE INSERT ON training_scopes "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic publication failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        publish(info, revision="revision-new")
    # A fresh process rules out accidentally serving only an in-memory old value.
    ctx = multiprocessing.get_context("spawn")
    result = ctx.Queue()
    child = ctx.Process(target=_read_after_restart, args=(str(info), result))
    child.start()
    try:
        assert result.get(timeout=10) == (old["generation"], "revision-old:1")
    finally:
        child.join(10)
        if child.is_alive():
            child.terminate()
            child.join(5)
        result.close()
    assert child.exitcode == 0
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER fail_scope_publish")
    new = publish(info, revision="revision-new")
    assert new["generation"] != old["generation"]
    assert index.read_ficha("tenant-a", scope(), "00001", info_root=info)["notas_sku"] == {"00001": "revision-new:1"}
