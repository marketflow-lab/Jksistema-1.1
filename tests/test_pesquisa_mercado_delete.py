"""Research deletion is owner-scoped, atomic, and changes consolidated origins."""

import ast
import asyncio
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

from backend.routers.pesquisa_mercado import create_pesquisa_mercado_router
from backend.services import favoritos_endpoints, pesquisa_mercado
from backend.services.admin_usuarios_common import (
    _normalizar_permissoes_exigidas, _permissao_exigida_por_rota,
    _permissoes_autorizam_rota,
)
from backend.services.pesquisa_mercado import PesquisaMercadoStore


@pytest.fixture
def store(tmp_path):
    return PesquisaMercadoStore(tmp_path / "info")


def vendor(name="Loja 123"):
    return {"key": "id:123", "id": "123", "nome": name,
            "perfilUrl": "https://lista.mercadolivre.com.br/_CustId_123"}


def ad(number=1, monthly=10):
    return {"id": f"MLB{number}", "url": f"https://produto.mercadolivre.com.br/MLB-{number}-produto-_JM",
            "titulo": f"Produto {number}", "vendas": monthly * 10,
            "vendasFonte": "avantpro_anuncio", "media_vendas_mensal": monthly, "seller_id": "123"}


def make_job(store, *, status="completed", tenant="cliente-a", user="alice", monthly=10, name="Loja 123"):
    job = store.start(tenant, user, category={"key": "ferramentas", "nome": "Ferramentas",
                                          "url": "https://www.mercadolivre.com.br/c/ferramentas"})
    store.publish(job["id"], "id:123", tenant, user, vendor(name), [ad(monthly=monthly)])
    for number, outcome in ((1, "consulted"), (2, "failed")):
        store.checkpoint(job["id"], tenant, user, checkpoint={"discoveredSeed": {
            "key": f"id:MLB{number}", "category_key": "ferramentas", "status": outcome,
            "id": f"MLB{number}", "url": ad(number)["url"]}})
    return store.checkpoint(job["id"], tenant, user, status=status)


def persisted_job(store, job_id, tenant="cliente-a"):
    with store._connection(tenant) as conn:
        return {table: [dict(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE {'id' if table == 'pm_jobs' else 'job_id'}=? ORDER BY rowid", (job_id,))]
                for table in ("pm_jobs", "pm_sellers", "pm_ads", "pm_discovery")}


@pytest.mark.parametrize("status", ["completed", "completed_partial", "cancelled", "failed"])
def test_delete_closed_research_cascades_results_and_discovery(store, status):
    job = make_job(store, status=status)
    revision = store.history("cliente-a", "alice")["owner_revision"]
    before = persisted_job(store, job["id"])
    assert [len(before[table]) for table in ("pm_jobs", "pm_sellers", "pm_ads", "pm_discovery")] == [1, 1, 1, 2]
    assert store.delete(job["id"], "cliente-a", "alice") == {
        "deleted": True, "job_id": job["id"], "revision": revision + 1}
    assert all(not rows for rows in persisted_job(store, job["id"]).values())
    assert store.history("cliente-a", "alice") == {"items": [], "total": 0, "owner_revision": revision + 1}
    assert store.latest("cliente-a", "alice") is None
    assert store.consolidated_ranking("cliente-a", "alice")["items"] == []
    with store._connection("cliente-a") as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(HTTPException) as error:
        store.get(job["id"], "cliente-a", "alice")
    assert error.value.status_code == 404


@pytest.mark.parametrize("status", ["running", "paused", "waiting_login", "unknown"])
def test_active_or_unknown_research_cannot_be_deleted_before_cancellation(store, status):
    job = make_job(store, status=status if status != "unknown" else "paused")
    if status == "unknown":
        with store._connection("cliente-a", write=True) as conn:
            conn.execute("UPDATE pm_jobs SET status='unknown' WHERE id=?", (job["id"],))
    before = persisted_job(store, job["id"])
    revision = store.consolidated_summary("cliente-a", "alice")["owner_revision"]
    with pytest.raises(HTTPException) as error:
        store.delete(job["id"], "cliente-a", "alice")
    assert error.value.status_code == 409 and "Cancele" in error.value.detail
    assert persisted_job(store, job["id"]) == before
    assert store.consolidated_summary("cliente-a", "alice")["owner_revision"] == revision
    if status != "unknown":
        store.checkpoint(job["id"], "cliente-a", "alice", status="cancelled")
        assert store.delete(job["id"], "cliente-a", "alice")["deleted"] is True


def test_delete_isolated_by_tenant_username_and_job_and_preserves_other_jobs(store):
    first = make_job(store)
    latest = make_job(store, monthly=20)
    bob = make_job(store, user="bob", monthly=999)
    other_tenant = make_job(store, tenant="cliente-b", monthly=888)
    saved = {(tenant, job_id): persisted_job(store, job_id, tenant) for tenant, job_id in (
        ("cliente-a", first["id"]), ("cliente-a", latest["id"]),
        ("cliente-a", bob["id"]), ("cliente-b", other_tenant["id"]))}
    revisions = {(tenant, user): store.history(tenant, user)["owner_revision"] for tenant, user in (
        ("cliente-a", "alice"), ("cliente-a", "bob"), ("cliente-b", "alice"))}
    for job_id, tenant, user in ((latest["id"], "cliente-a", "bob"),
                                (latest["id"], "cliente-b", "alice"),
                                ("missing", "cliente-a", "alice")):
        with pytest.raises(HTTPException) as error:
            store.delete(job_id, tenant, user)
        assert error.value.status_code == 404
    assert all(persisted_job(store, job_id, tenant) == data for (tenant, job_id), data in saved.items())
    assert all(store.history(tenant, user)["owner_revision"] == revision for (tenant, user), revision in revisions.items())
    store.delete(latest["id"], "cliente-a", "alice")
    assert persisted_job(store, first["id"]) == saved[("cliente-a", first["id"])]
    assert persisted_job(store, bob["id"]) == saved[("cliente-a", bob["id"])]
    assert persisted_job(store, other_tenant["id"], "cliente-b") == saved[("cliente-b", other_tenant["id"])]
    assert store.history("cliente-a", "bob")["owner_revision"] == revisions[("cliente-a", "bob")]
    assert store.history("cliente-b", "alice")["owner_revision"] == revisions[("cliente-b", "alice")]


def test_deleting_latest_observation_restores_earlier_metrics_and_name(store):
    first = make_job(store, monthly=10, name="Loja anterior")
    latest = make_job(store, monthly=20, name="Loja atual")
    before = store.consolidated_ranking("cliente-a", "alice")["items"][0]
    assert (before["source_job_id"], before["media_vendas_mensal"], before["vendedor"], before["observations"]) == (latest["id"], 20, "Loja atual", 2)
    saved = persisted_job(store, first["id"])
    store.delete(latest["id"], "cliente-a", "alice")
    after = store.consolidated_ranking("cliente-a", "alice")["items"][0]
    assert (after["source_job_id"], after["media_vendas_mensal"], after["vendas"], after["vendedor"], after["observations"]) == (first["id"], 10, 100, "Loja anterior", 1)
    assert persisted_job(store, first["id"]) == saved
    assert store.latest("cliente-a", "alice")["id"] == first["id"]
    origins = store.consolidated_origins("cliente-a", "alice", ad_key="id:MLB1", seller_key="id:123")
    assert origins["total"] == 1 and origins["items"][0]["source_job_id"] == first["id"]
    assert store.consolidated_categories("cliente-a", "alice")["items"][0]["researches"] == 1
    assert store.consolidated_sellers("cliente-a", "alice")["items"][0]["vendedor"] == "Loja anterior"


def test_delete_invalidates_every_owner_snapshot_and_repeat_delete_is_404(store):
    first = make_job(store)
    latest = make_job(store, monthly=20)
    revision = store.history("cliente-a", "alice")["owner_revision"]
    result = store.delete(latest["id"], "cliente-a", "alice")
    assert result["revision"] == revision + 1
    for method in ("history", "consolidated_summary", "consolidated_ranking", "consolidated_sellers", "consolidated_categories", "consolidated_origins"):
        kwargs = {"ad_key": "id:MLB1", "seller_key": "id:123"} if method.endswith("origins") else {}
        with pytest.raises(HTTPException) as error:
            getattr(store, method)("cliente-a", "alice", revision=revision, **kwargs)
        assert error.value.status_code == 409 and error.value.detail["owner_revision"] == result["revision"]
    with pytest.raises(HTTPException) as error:
        store.delete(latest["id"], "cliente-a", "alice")
    assert error.value.status_code == 404
    assert store.history("cliente-a", "alice")["owner_revision"] == result["revision"]
    assert store.get(first["id"], "cliente-a", "alice")["id"] == first["id"]


def test_delete_rolls_back_cascade_if_revision_update_fails(store, monkeypatch):
    job = make_job(store)
    before = persisted_job(store, job["id"])
    revision = store.history("cliente-a", "alice")["owner_revision"]

    def fail_revision(*_):
        raise sqlite3.OperationalError("simulated revision write failure")

    monkeypatch.setattr(PesquisaMercadoStore, "_bump_owner_revision", staticmethod(fail_revision))
    with pytest.raises(HTTPException) as error:
        store.delete(job["id"], "cliente-a", "alice")
    assert error.value.status_code == 503
    assert persisted_job(store, job["id"]) == before
    assert store.history("cliente-a", "alice")["owner_revision"] == revision


def test_delayed_collector_writes_cannot_recreate_deleted_research(store):
    job = make_job(store, status="cancelled")
    revision = store.delete(job["id"], "cliente-a", "alice")["revision"]
    for write in (
        lambda: store.publish(job["id"], "id:123", "cliente-a", "alice", vendor(), [ad(2)]),
        lambda: store.checkpoint(job["id"], "cliente-a", "alice", status="running"),
    ):
        with pytest.raises(HTTPException) as error:
            write()
        assert error.value.status_code == 404
    assert all(not rows for rows in persisted_job(store, job["id"]).values())
    assert store.history("cliente-a", "alice")["owner_revision"] == revision


def test_reader_keeps_one_snapshot_while_concurrent_delete_commits(store, monkeypatch):
    first = make_job(store)
    latest = make_job(store, monthly=20)
    revision = store.history("cliente-a", "alice")["owner_revision"]
    snapshot_read, deleted = threading.Event(), threading.Event()
    original = PesquisaMercadoStore._owner_revision

    def read_revision(conn, tenant, user, expected=None):
        value = original(conn, tenant, user, expected)
        if expected == revision:
            snapshot_read.set()
            assert deleted.wait(5)
        return value

    def writer():
        assert snapshot_read.wait(5)
        store.delete(latest["id"], "cliente-a", "alice")
        deleted.set()

    monkeypatch.setattr(PesquisaMercadoStore, "_owner_revision", staticmethod(read_revision))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        result = store.consolidated_ranking("cliente-a", "alice", revision=revision)
        future.result(timeout=5)
    assert result["owner_revision"] == revision and result["items"][0]["source_job_id"] == latest["id"]
    assert result["items"][0]["observations"] == 2
    refreshed = store.consolidated_ranking("cliente-a", "alice")
    assert refreshed["owner_revision"] == revision + 1 and refreshed["items"][0]["source_job_id"] == first["id"]


def test_delete_http_enforces_production_auth_permissions_owner_and_response(store, monkeypatch):
    from backend.services import central_accounts_client

    monkeypatch.setattr(central_accounts_client, "bind_request", lambda *_: None)
    monkeypatch.setattr(central_accounts_client, "assert_manual_path", lambda *_: None)
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)
    permissions = {"alice": {"favoritos": True}, "bob": {"favoritos": True}, "denied": {"vendas": True}}
    namespace = {
        "Request": Request, "Header": Header, "Optional": Optional, "HTTPException": HTTPException,
        "asyncio": asyncio, "JWTError": ValueError,
        "decodificar_access_token": lambda token: {"sub": token.split(":", 1)[1], "client_id": token.split(":", 1)[0]},
        "_permissao_exigida_por_rota": _permissao_exigida_por_rota,
        "_normalizar_permissoes_exigidas": _normalizar_permissoes_exigidas,
        "_permissoes_autorizam_rota": _permissoes_autorizam_rota,
        "_carregar_permissoes_usuario": lambda user, _: permissions[user],
        "_machine_presence_auto_touch": lambda *_: None,
        "logger": SimpleNamespace(warning=lambda *_: None),
    }
    source = (Path(__file__).resolve().parents[1] / "backend_api.py").read_text(encoding="utf-8-sig")
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_tenant_id")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "backend_auth_boundary", "exec"), namespace)
    monkeypatch.setattr(favoritos_endpoints, "_TENANT_DEPENDENCY", namespace["get_tenant_id"])
    app = FastAPI()
    app.include_router(create_pesquisa_mercado_router())
    job = make_job(store)
    revision = store.history("cliente-a", "alice")["owner_revision"]
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    with TestClient(app) as client:
        assert client.delete(base).status_code == 401
        assert client.delete(base, headers={"Authorization": "Bearer cliente-a:denied", "X-Username": "alice"}).status_code == 403
        assert client.delete(base, headers={"Authorization": "Bearer cliente-a:bob", "X-Username": "alice"}).status_code == 404
        assert client.delete(base, headers={"Authorization": "Bearer cliente-b:alice"}).status_code == 404
        response = client.delete(base, headers={"Authorization": "Bearer cliente-a:alice", "X-Username": "bob"})
        assert response.status_code == 200, response.text
        assert response.json() == {"deleted": True, "job_id": job["id"], "revision": revision + 1}
        assert client.delete(base, headers={"Authorization": "Bearer cliente-a:alice"}).status_code == 404
        active = store.start("cliente-a", "alice")
        assert client.delete(f"/api/pesquisa-mercado/jobs/{active['id']}", headers={"Authorization": "Bearer cliente-a:alice"}).status_code == 409


def test_delete_removes_legacy_observations_without_foreign_key_cascade(store):
    job = make_job(store)
    # Old history schema fixtures are supported even without cascade declarations.
    path = store.info_root / "cliente-a" / "pesquisa_mercado.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in ("pm_ads", "pm_sellers"):
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            sql = sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE legacy_{table}")
            sql = sql.replace("REFERENCES pm_jobs(id) ON DELETE CASCADE", "")
            sql = re.sub(r",\s*FOREIGN KEY\(job_id,seller_key\) REFERENCES pm_sellers\(job_id,seller_key\) ON DELETE CASCADE", "", sql)
            conn.execute(sql)
            conn.execute(f"INSERT INTO legacy_{table} SELECT * FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE legacy_{table} RENAME TO {table}")
    assert store.delete(job["id"], "cliente-a", "alice")["deleted"] is True
    assert all(not rows for rows in persisted_job(store, job["id"]).values())
