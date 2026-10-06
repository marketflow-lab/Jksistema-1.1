"""History/consolidation against temporary SQLite databases, never desktop data."""

import ast
import asyncio
import json
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
    _normalizar_permissoes_exigidas,
    _permissao_exigida_por_rota,
    _permissoes_autorizam_rota,
)
from backend.services.pesquisa_mercado import PesquisaMercadoStore


@pytest.fixture
def store(tmp_path):
    return PesquisaMercadoStore(tmp_path / "info")


def category(key="MLB1574", name="Ferramentas"):
    return {"key": key, "nome": name, "url": "https://www.mercadolivre.com.br/c/ferramentas"}


def seller(number="123", key=None):
    return {"key": key or "id:" + number, "id": number, "nome": "Loja " + number, "perfilUrl": "https://lista.mercadolivre.com.br/_CustId_" + number}


def ad(number=1, monthly=10, sales=100, owner="123", **extras):
    return {"id": "MLB" + str(number), "url": f"https://produto.mercadolivre.com.br/MLB-{number}-produto-_JM", "titulo": f"Produto {number}", "vendas": sales, "vendasFonte": "avantpro_anuncio", "media_vendas_mensal": monthly, "seller_id": owner, **extras}


def publish(store, job, ads, vendor=None, client="cliente-a", user="alice"):
    vendor = vendor or seller()
    return store.publish(job["id"], vendor["key"], client, user, vendor, ads)


def start_next(store, job, selected=None):
    store.checkpoint(job["id"], "cliente-a", "alice", status="completed")
    return store.start("cliente-a", "alice", category=selected)


def test_history_restores_summaries_scope_filters_and_stable_pages_without_checkpoint(store):
    ids = []
    for index in range(29):
        job = store.start("cliente-a", "Alice", category=category(name="Ferramentas elétricas"))
        ids.append(job["id"])
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"phase": "seller", "large": "x" * 10000}, status="cancelled")
    restored = PesquisaMercadoStore(store.info_root)
    page = restored.history("cliente-a", "alice", q="ELÉTRICAS", status="cancelled", scope_category="MLB1574")
    assert page["total"] == 29 and len(page["items"]) == 25
    second = restored.history("cliente-a", "alice", offset=25, revision=page["owner_revision"])
    assert len(second["items"]) == 4
    assert {row["id"] for row in page["items"] + second["items"]} == set(ids)
    assert all("checkpoint" not in row and row["scope_category"] == category(name="Ferramentas elétricas") for row in page["items"])
    summary = restored.job_summary(ids[0], "cliente-a", "alice")
    assert "checkpoint" not in summary and summary["scope_category"] == category(name="Ferramentas elétricas")
    with pytest.raises(HTTPException) as denied:
        restored.job_summary(ids[0], "cliente-a", "bob")
    assert denied.value.status_code == 404
    assert restored.history("cliente-a", "bob")["total"] == 0
    assert restored.history("cliente-b", "alice")["total"] == 0
    assert restored.consolidated_categories("cliente-a", "alice")["items"][0]["researches"] == 29


def test_latest_pending_observation_wins_before_evidence_and_any_origin_scope_filters(store):
    old = store.start("cliente-a", "alice", category=category())
    publish(store, old, [ad(monthly=90, sales=900)])
    new = start_next(store, old, category("MLB5672", "Acessórios para Veículos"))
    publish(store, new, [ad(monthly=None, sales=950, vendasFonte="pagina_anuncio")])
    rows = store.consolidated_ranking("cliente-a", "alice", scope_category="MLB1574")["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["source_job_id"] == new["id"] and row["rankable"] is False and row["rank"] is None
    assert row["vendas"] == 950 and row["research_count"] == 2
    assert row["scope_category"]["key"] == "MLB5672"
    assert store.consolidated_ranking("cliente-a", "alice", evidence="rankable", scope_category="MLB1574")["total"] == 0
    summary = store.consolidated_summary("cliente-a", "alice")
    assert (summary["researches"], summary["observations"], summary["unique_ads"], summary["rankable"], summary["pending"]) == (2, 2, 1, 0, 1)
    origins = store.consolidated_origins("cliente-a", "alice", ad_key=row["ad_key"], seller_key=row["seller_key"])
    assert origins["total"] == 2 and [item["source_job_id"] for item in origins["items"]] == [new["id"], old["id"]]
    assert origins["items"][1]["vendas"] == 900


def test_catalog_identity_fallback_missing_avant_and_seller_aliases_remain_correct(store):
    first = store.start("cliente-a", "alice")
    catalog_url = "https://www.mercadolivre.com.br/produto/p/MLB999"
    catalog = ad(id="MLB999", url=catalog_url, catalogo=True, avant_seller_id="123")
    publish(store, first, [catalog])
    # Same numeric seller under a profile alias must count once for this research.
    publish(store, first, [{**catalog, "media_vendas_mensal": 20}], seller(key="perfil:alias"))
    other_vendor = seller("456")
    publish(store, first, [{**catalog, "seller_id": "456", "avant_seller_id": "456"}], other_vendor)
    second = start_next(store, first)
    publish(store, second, [{**catalog, "media_vendas_mensal": 30}])
    rows = store.consolidated_ranking("cliente-a", "alice")["items"]
    assert len(rows) == 2 and all(row["id"] == "" and row["ad_key"] == "url:" + catalog_url for row in rows)
    mine = next(row for row in rows if row["seller_key"] == "id:123")
    assert mine["media_vendas_mensal"] == 30 and mine["observations"] == mine["research_count"] == 2
    origins = store.consolidated_origins("cliente-a", "alice", ad_key=mine["ad_key"], seller_key="id:123")
    assert origins["total"] == 2 and origins["items"][1]["media_vendas_mensal"] == 20
    bad = ad(8, catalogo=True, avant_seller_id="456")
    publish(store, second, [catalog, bad, ad(9, vendasFonte="pagina_anuncio", media_vendas_mensal=None)])
    assert store.consolidated_ranking("cliente-a", "alice", evidence="pending")["total"] == 2
    assert store.consolidated_sellers("cliente-a", "alice")["total"] == 2


def test_consolidated_endpoints_are_isolated_even_when_ids_and_sellers_match(store):
    for tenant, user, monthly in (("cliente-a", "alice", 10), ("cliente-a", "bob", 999), ("cliente-b", "alice", 888)):
        job = store.start(tenant, user, category=category())
        publish(store, job, [ad(monthly=monthly)], client=tenant, user=user)
    assert store.consolidated_ranking("cliente-a", "alice")["items"][0]["media_vendas_mensal"] == 10
    assert store.consolidated_summary("cliente-a", "alice")["researches"] == 1
    assert store.consolidated_categories("cliente-a", "alice")["items"][0]["researches"] == 1
    assert store.consolidated_origins("cliente-a", "alice", ad_key="id:MLB1", seller_key="id:123")["total"] == 1
    assert store.consolidated_origins("cliente-a", "outsider", ad_key="id:MLB1", seller_key="id:123")["items"] == []


def test_revision_changes_only_for_persisted_changes_and_all_reads_reject_stale_snapshot(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad()])
    revision = store.history("cliente-a", "alice")["owner_revision"]
    assert store.start("cliente-a", "alice")["id"] == job["id"]
    publish(store, job, [ad()])
    assert store.history("cliente-a", "alice")["owner_revision"] == revision
    publish(store, job, [ad(monthly=11)])
    methods = ["history", "consolidated_summary", "consolidated_ranking", "consolidated_sellers", "consolidated_categories", "consolidated_origins"]
    for method in methods:
        kwargs = {"ad_key": "id:MLB1", "seller_key": "id:123"} if method.endswith("origins") else {}
        with pytest.raises(HTTPException) as error:
            getattr(store, method)("cliente-a", "alice", revision=revision, **kwargs)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "research_revision_changed"


def test_concurrent_publication_cannot_mix_revision_and_rows_in_one_response(store, monkeypatch):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad()])
    revision = store.history("cliente-a", "alice")["owner_revision"]
    snapshot_read, writer_done = threading.Event(), threading.Event()
    original = PesquisaMercadoStore._owner_revision

    def read_revision(conn, tenant, user, expected=None):
        value = original(conn, tenant, user, expected)
        snapshot_read.set()
        assert writer_done.wait(5)
        return value

    def writer():
        assert snapshot_read.wait(5)
        publish(store, job, [ad(), ad(2)])
        writer_done.set()

    monkeypatch.setattr(PesquisaMercadoStore, "_owner_revision", staticmethod(read_revision))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        result = store.consolidated_ranking("cliente-a", "alice", revision=revision)
        future.result(timeout=5)
    assert result["owner_revision"] == revision and result["total"] == len(result["items"]) == 1


def test_legacy_migration_projects_scope_without_fabricating_collection_dates(tmp_path):
    root = tmp_path / "legacy-info"
    directory = root / "cliente-a"
    directory.mkdir(parents=True)
    normalized_seller = {**seller(), "categoria": "Ferramentas"}
    key, normalized_ad = pesquisa_mercado._normalize_ad(ad(), normalized_seller, 0)
    normalized_ad.update(ordem_coleta=1, posicao_original=1)
    with sqlite3.connect(directory / "pesquisa_mercado.db") as conn:
        conn.executescript("""
            CREATE TABLE pm_jobs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,username TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,checkpoint TEXT NOT NULL,counters TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_order INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE pm_sellers(job_id TEXT NOT NULL,seller_key TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(job_id,seller_key));
            CREATE TABLE pm_ads(job_id TEXT NOT NULL,seller_key TEXT NOT NULL,ad_key TEXT NOT NULL,payload TEXT NOT NULL,category TEXT NOT NULL,search_text TEXT NOT NULL,rankable INTEGER NOT NULL,monthly REAL,sales REAL,original_position INTEGER NOT NULL,collection_order INTEGER NOT NULL,ranking_key TEXT NOT NULL,PRIMARY KEY(job_id,seller_key,ad_key));
        """)
        conn.execute("INSERT INTO pm_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("old", "cliente-a", "alice", "completed", 8, json.dumps({"scope": {"category": category()}, "padding": "x" * 1000000}), json.dumps({name: 0 for name in pesquisa_mercado.COUNTER_KEYS}), "", "2025-01-01T00:00:00.000+00:00", "2099-01-01T00:00:00.000+00:00", 2))
        conn.execute("INSERT INTO pm_sellers VALUES(?,?,?,?)", ("old", "id:123", json.dumps(normalized_seller), "legacy"))
        conn.execute("INSERT INTO pm_ads VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("old", "id:123", key, json.dumps(normalized_ad), "Ferramentas", "produto 1 loja 123 mlb1", 1, 10, 100, 1, 1, pesquisa_mercado._ranking_key(normalized_ad)))
    store = PesquisaMercadoStore(root)
    history = store.history("cliente-a", "alice")
    assert history["items"][0]["scope_category"] == category()
    legacy = store.consolidated_ranking("cliente-a", "alice")["items"][0]
    assert legacy["date_approximate"] and legacy["observed_at"].startswith("2025-")
    assert PesquisaMercadoStore(root).history("cliente-a", "alice")["owner_revision"] == history["owner_revision"]
    new = store.start("cliente-a", "alice")
    publish(store, new, [ad(monthly=20)])
    latest = store.consolidated_ranking("cliente-a", "alice")["items"][0]
    assert latest["source_job_id"] == new["id"] and latest["date_approximate"] is False
    store.checkpoint(new["id"], "cliente-a", "alice", status="paused")
    assert store.consolidated_ranking("cliente-a", "alice")["items"][0]["observed_at"] == latest["observed_at"]
    origins = store.consolidated_origins("cliente-a", "alice", ad_key=key, seller_key="id:123")
    assert origins["items"][1]["date_approximate"] is True


def test_large_database_decodes_only_the_requested_payload_page(store, monkeypatch):
    # Thirty thousand observations; SQLite performs dedup/sort, Python decodes 100.
    for research in range(6):
        job = store.start("cliente-a", "alice")
        first = (research % 3) * 5000
        publish(store, job, [ad(first + index, monthly=index % 100) for index in range(5000)])
        store.checkpoint(job["id"], "cliente-a", "alice", status="completed")
    real_loads, payload_decodes = pesquisa_mercado.json.loads, []

    def counted_loads(value, *args, **kwargs):
        if isinstance(value, str) and '"titulo"' in value and '"ordem_coleta"' in value:
            payload_decodes.append(value)
        return real_loads(value, *args, **kwargs)

    monkeypatch.setattr(pesquisa_mercado.json, "loads", counted_loads)
    page = store.consolidated_ranking("cliente-a", "alice", limit=100)
    assert page["total"] == 15000 and len(page["items"]) == len(payload_decodes) == 100
    assert all(row["research_count"] == 2 for row in page["items"])
    with store._connection("cliente-a") as conn:
        sql, args = store._consolidated_query("cliente-a", "alice")
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql + "SELECT payload FROM selected LIMIT 100", args)]
    assert any("SEARCH" in row and "INDEX" in row for row in plan)


def test_real_auth_boundary_and_http_contracts_cover_all_history_routes(store, monkeypatch):
    from backend.services import central_accounts_client

    monkeypatch.setattr(central_accounts_client, "bind_request", lambda *_: None)
    monkeypatch.setattr(central_accounts_client, "assert_manual_path", lambda *_: None)
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)
    permissions = {"alice": {"favoritos": True}, "bob": {"vendas": True}}
    namespace = {
        "Request": Request, "Header": Header, "Optional": Optional, "HTTPException": HTTPException,
        "asyncio": asyncio, "JWTError": ValueError,
        "decodificar_access_token": lambda token: {"sub": token, "client_id": "cliente-a"},
        "_permissao_exigida_por_rota": _permissao_exigida_por_rota,
        "_normalizar_permissoes_exigidas": _normalizar_permissoes_exigidas,
        "_permissoes_autorizam_rota": _permissoes_autorizam_rota,
        "_carregar_permissoes_usuario": lambda user, _: permissions[user],
        "_machine_presence_auto_touch": lambda *_: None,
        "logger": SimpleNamespace(warning=lambda *_: None),
    }
    # Compile only the real dependency's AST, avoiding backend_api import/startup.
    source = (Path(__file__).resolve().parents[1] / "backend_api.py").read_text(encoding="utf-8-sig")
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_tenant_id")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "backend_auth_boundary", "exec"), namespace)
    monkeypatch.setattr(favoritos_endpoints, "_TENANT_DEPENDENCY", namespace["get_tenant_id"])
    app = FastAPI()
    app.include_router(create_pesquisa_mercado_router())
    routes = ["/jobs", "/consolidado/resumo", "/consolidado/ranking", "/consolidado/vendedores", "/consolidado/categorias", "/consolidado/origens?ad_key=id:MLB1&seller_key=id:123"]
    with TestClient(app) as client:
        allowed = {"Authorization": "Bearer alice", "X-Username": "bob"}
        denied = {"Authorization": "Bearer bob", "X-Username": "alice"}
        for route in routes + ["/jobs/missing/resumo"]:
            assert client.get("/api/pesquisa-mercado" + route).status_code == 401
            assert client.get("/api/pesquisa-mercado" + route, headers=denied).status_code == 403
        job = client.post("/api/pesquisa-mercado/jobs", headers=allowed, json={"category": category()}).json()["job"]
        publish(store, job, [ad()])
        for route in routes + [f"/jobs/{job['id']}/resumo"]:
            response = client.get("/api/pesquisa-mercado" + route, headers=allowed)
            assert response.status_code == 200, response.text
            if route.endswith("/resumo") and route.startswith("/jobs/"):
                assert "checkpoint" not in response.json()["job"]
            else:
                assert "owner_revision" in response.json()
        revision = client.get("/api/pesquisa-mercado/jobs", headers=allowed).json()["owner_revision"]
        publish(store, job, [ad(monthly=11)])
        assert client.get(f"/api/pesquisa-mercado/consolidado/ranking?revision={revision}", headers=allowed).status_code == 409
        for suffix in ("/jobs?limit=101", "/jobs?status=queued", "/consolidado/ranking?offset=-1", "/consolidado/ranking?evidence=unknown", "/consolidado/origens", "/consolidado/categorias?revision=-1"):
            assert client.get("/api/pesquisa-mercado" + suffix, headers=allowed).status_code == 422
