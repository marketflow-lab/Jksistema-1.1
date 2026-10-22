from __future__ import annotations

import ast
import asyncio
import inspect
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


def seller(number="123", category="Ferramentas"):
    return {"key": f"id:{number}", "id": number, "nome": f"Loja {number}", "perfilUrl": f"https://lista.mercadolivre.com.br/_CustId_{number}", "categoria": category}


def ad(number=1, monthly=10, sales=100, position=1, owner="123", **overrides):
    return {
        "id": f"MLB{number}", "url": f"https://produto.mercadolivre.com.br/MLB-{number}-produto-_JM",
        "titulo": f"Produto {number}", "preco": 99, "imagem": "https://http2.mlstatic.com/D_NQ_NP.webp",
        "vendas": sales, "vendasFonte": "avantpro_anuncio", "data_criacao": "2026-01-01",
        "media_vendas_mensal": monthly, "posicao": position, "seller_id": owner,
        **overrides,
    }


def scope_category(key="MLB1574", name="Ferramentas", url="https://www.mercadolivre.com.br/c/ferramentas"):
    return {"key": key, "nome": name, "url": url}


@pytest.mark.parametrize("category", [
    scope_category(),
    scope_category("MLB1234", "Ferramentas eletricas", "https://lista.mercadolivre.com.br/ferramentas/ferramentas-eletricas"),
    scope_category("leaf", "Brocas", "https://www.mercadolivre.com.br/categorias/ferramentas/brocas"),
])
def test_selected_category_or_subcategory_seeds_only_the_selected_scope(store, category):
    job = store.start("cliente-a", "alice", category=category)
    checkpoint = job["checkpoint"]
    assert checkpoint == {"version": 1, "scope": {"category": category}, "categories": [category], "categoryIndex": 0, "discoveryInitialized": True, "phase": "categories"}
    assert store.get(job["id"], "cliente-a", "alice")["checkpoint"]["scope"] == {"category": category}
    assert store.latest("cliente-a", "alice")["checkpoint"]["scope"]["category"]["nome"] == category["nome"]


def test_category_metadata_is_normalized_without_tracking_or_fragments(store):
    category = scope_category("  root  ", "  Ferramentas   eletricas  ", "https://www.mercadolivre.com.br/c/ferramentas/?utm_source=menu&category_id=MLB1574#origin")
    normalized = {"key": "root", "nome": "Ferramentas eletricas", "url": "https://www.mercadolivre.com.br/c/ferramentas/?category_id=MLB1574"}
    assert store.start("cliente-a", "alice", category=category)["checkpoint"]["scope"] == {"category": normalized}


@pytest.mark.parametrize("host,path,name", [
    ("imoveis.mercadolivre.com.br", "apartamentos", "Apartamentos"),
    ("imoveis.mercadolivre.com.br", "casas", "Casas"),
    ("servicos.mercadolivre.com.br", "academia-esportes", "Academia e Esportes"),
    ("servicos.mercadolivre.com.br", "educacao", "Educacao"),
])
def test_specialized_children_observed_in_official_category_index_are_valid_scopes(store, host, path, name):
    url = f"https://{host}/{path}/"
    category = scope_category(url, name, url)
    job = store.start("cliente-a", "alice", category=category)
    normalized = {**category, "url": url}
    assert job["checkpoint"]["scope"] == {"category": normalized}
    assert job["checkpoint"]["categories"] == [normalized]
    restarted = PesquisaMercadoStore(store.info_root)
    resumed = restarted.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"phase": "seller"}, status="paused")
    assert resumed["checkpoint"]["scope"] == {"category": normalized}


@pytest.mark.parametrize("host", ["imoveis.mercadolivre.com.br", "servicos.mercadolivre.com.br"])
@pytest.mark.parametrize("path", [
    "/", "//", "///", "/p/MLB123", "/up/MLBU123", "/MLB-123-produto-_JM", "/loja/vendedor",
    "/perfil/vendedor", "/_CustId_123", "/casas?seller_id=123", "/casas?wid=MLB123",
    "/casas?pdp_filters=item_id%3AMLB123", "/casas?pdp_filters=seller_id%3A123", "/casas?access_token=invalid",
])
def test_specialized_category_hosts_keep_product_seller_root_and_secret_rejections(store, host, path):
    with pytest.raises(HTTPException) as error:
        store.start("cliente-a", "alice", category=scope_category(url=f"https://{host}{path}"))
    assert error.value.status_code == 422
    assert not store.info_root.exists()


@pytest.mark.parametrize("url", [
    "https://carros.mercadolivre.com.br/carros-caminhonetes/",
    "https://motos.mercadolivre.com.br/motos/",
    "https://imoveis.mercadolivre.com.br.example.test/casas/",
    "https://outro.mercadolivre.com.br/casas/",
    "https://usuario:senha@imoveis.mercadolivre.com.br/casas/",
    "https://servicos.mercadolivre.com.br:443/educacao/",
])
def test_category_host_expansion_is_specific_and_keeps_credentials_and_ports_blocked(store, url):
    with pytest.raises(HTTPException) as error:
        store.start("cliente-a", "alice", category=scope_category(url=url))
    assert error.value.status_code == 422
    assert not store.info_root.exists()


@pytest.mark.parametrize("path", ["/", "//", "///"])
def test_listing_category_root_with_repeated_slashes_is_rejected_before_database_write(store, path):
    with pytest.raises(HTTPException) as error:
        store.start("cliente-a", "alice", category=scope_category(url=f"https://lista.mercadolivre.com.br{path}"))
    assert error.value.status_code == 422
    assert not store.info_root.exists()


@pytest.mark.parametrize("url", [
    "http://www.mercadolivre.com.br/c/ferramentas", "https://example.test/c/ferramentas",
    "https://mercadolivre.com.br.example.test/c/ferramentas", "https://www.mercadolivre.com.br:443/c/ferramentas",
    "https://www.mercadolivre.com.br:8443/c/ferramentas", "https://usuario:senha@www.mercadolivre.com.br/c/ferramentas",
    "https://produto.mercadolivre.com.br/MLB-123-produto-_JM", "https://www.mercadolivre.com.br/produto/p/MLB123",
    "https://lista.mercadolivre.com.br/produto/p/MLB123", "https://www.mercadolivre.com.br/up/MLBU123",
    "https://lista.mercadolivre.com.br/MLB-123-produto-_JM", "https://www.mercadolivre.com.br/loja/vendedor",
    "https://lista.mercadolivre.com.br/loja/vendedor", "https://lista.mercadolivre.com.br/perfil/vendedor",
    "https://lista.mercadolivre.com.br/stores/vendedor", "https://lista.mercadolivre.com.br/_CustId_123",
    "https://lista.mercadolivre.com.br/ferramentas_SellerId_123", "https://lista.mercadolivre.com.br/ferramentas?seller_id=123",
    "https://lista.mercadolivre.com.br/ferramentas?seller_id=", "https://lista.mercadolivre.com.br/ferramentas?cust_id=123",
    "https://lista.mercadolivre.com.br/ferramentas?pdp_filters=seller_id%3A123", "https://www.mercadolivre.com.br/",
    "https://lista.mercadolivre.com.br/", "https://www.mercadolivre.com.br/ofertas",
    "https://lista.mercadolivre.com.br/ferramentas?access_token=invalid", "https://lista.mercadolivre.com.br/ferramentas?password=invalid",
    "https://lista.mercadolivre.com.br/ferramentas?wid=MLB123", "https://lista.mercadolivre.com.br/ferramentas?item_id=MLB123",
    "https://lista.mercadolivre.com.br/ferramentas?pdp_filters=item_id%3AMLB123",
])
def test_invalid_scope_url_has_no_sqlite_or_directory_side_effect(store, url):
    with pytest.raises(HTTPException) as error:
        store.start("cliente-a", "alice", category=scope_category(url=url))
    assert error.value.status_code == 422
    assert not store.info_root.exists()


@pytest.mark.parametrize("category", [
    {}, {"key": "root", "nome": "Ferramentas"}, {**scope_category(), "seller_id": "123"},
    scope_category(key=""), scope_category(key=" " * 4), scope_category(key="x" * 501),
    scope_category(name="x" * 401), scope_category(name="Ferramentas\nEletricas"),
    scope_category(key=123), scope_category(url=123), {"key": "root", "nome": "Ferramentas", "url": None},
])
def test_invalid_scope_shape_and_bounds_do_not_create_a_job(store, category):
    with pytest.raises(HTTPException) as error:
        store.start("cliente-a", "alice", category=category)
    assert error.value.status_code == 422
    assert not store.info_root.exists()


def test_active_job_keeps_its_original_scope_even_when_start_receives_another_category(store):
    first = store.start("cliente-a", "alice", category=scope_category())
    other = scope_category("MLB5678", "Autopecas", "https://www.mercadolivre.com.br/c/acessorios-para-veiculos")
    assert store.start("cliente-a", "alice", category=other) == first
    paused = store.checkpoint(first["id"], "cliente-a", "alice", status="paused")
    assert store.start("cliente-a", "alice", category=other) == paused
    assert store.start("cliente-a", "bob", category=other)["checkpoint"]["scope"] == {"category": other}
    assert store.start("cliente-b", "alice", category=other)["checkpoint"]["scope"] == {"category": other}


def test_scope_survives_deep_checkpoint_restart_and_resume_without_being_resupplied(store):
    root = scope_category()
    child = scope_category("leaf", "Brocas", "https://lista.mercadolivre.com.br/ferramentas/brocas")
    job = store.start("cliente-a", "alice", category=root)
    cursor = {"version": 1, "categories": [root, child], "categoryIndex": 1, "currentCategory": child, "page": {"url": child["url"], "index": 5}, "phase": "seller", "seenSellerKeys": ["id:123"], "discoveryInitialized": True}
    paused = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=cursor, status="paused")
    assert paused["checkpoint"]["scope"] == {"category": root}
    restarted = PesquisaMercadoStore(store.info_root)
    assert restarted.get(job["id"], "cliente-a", "alice") == paused
    resumed = restarted.checkpoint(job["id"], "cliente-a", "alice", checkpoint={**cursor, "phase": "discovery"}, status="running")
    assert resumed["checkpoint"]["scope"] == {"category": root}
    assert resumed["checkpoint"]["page"]["index"] == 5
    assert resumed["checkpoint"]["categories"] == [root, child]


@pytest.mark.parametrize("incoming_scope", [None, {}, {"category": scope_category("leaf", "Brocas", "https://lista.mercadolivre.com.br/ferramentas/brocas")}])
def test_scoped_job_rejects_scope_replacement_atomically(store, incoming_scope):
    job = store.start("cliente-a", "alice", category=scope_category())
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"scope": incoming_scope, "discoveredSeed": {"key": "MLB123", "status": "failed"}}, counters={"pages": 99})
    assert error.value.status_code == 422
    assert store.get(job["id"], "cliente-a", "alice") == job
    assert store.discovery(job["id"], "MLB123", "cliente-a", "alice") == {"seen": False}


def test_legacy_unscoped_job_stays_compatible_and_cannot_acquire_a_new_scope(store):
    job = store.start("cliente-a", "alice")
    assert job["checkpoint"] == {}
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"scope": {"category": scope_category()}})
    assert error.value.status_code == 422
    assert store.get(job["id"], "cliente-a", "alice") == job
    updated = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"scope": None, "phase": "categories"})
    assert updated["checkpoint"] == {"phase": "categories"}


def publish(store, job, ads, number="123", category="Ferramentas", client="cliente-a", username="alice"):
    return store.publish(job["id"], f"id:{number}", client, username, seller(number, category), ads)


def test_start_is_atomic_and_only_one_active_per_authenticated_owner(store):
    barrier = threading.Barrier(8)

    def start(_):
        barrier.wait()
        return store.start("cliente-a", "Alice")["id"]

    with ThreadPoolExecutor(max_workers=8) as workers:
        ids = list(workers.map(start, range(8)))
    assert len(set(ids)) == 1
    assert store.start("cliente-a", "bob")["id"] != ids[0]
    assert store.start("cliente-b", "alice")["id"] != ids[0]
    store.checkpoint(ids[0], "cliente-a", "alice", status="paused")
    assert store.start("cliente-a", "alice")["id"] == ids[0]
    store.checkpoint(ids[0], "cliente-a", "alice", status="cancelled")
    assert store.start("cliente-a", "alice")["id"] != ids[0]


@pytest.mark.parametrize("method", ["get", "ranking", "sellers", "categories", "checkpoint"])
@pytest.mark.parametrize("client,user", [("cliente-b", "alice"), ("cliente-a", "bob")])
def test_all_job_operations_are_isolated_by_tenant_and_username(store, method, client, user):
    job = store.start("cliente-a", "alice")
    with pytest.raises(HTTPException) as error:
        getattr(store, method)(job["id"], client, user)
    assert error.value.status_code == 404


def test_unknown_id_and_latest_do_not_fall_back_to_another_owner(store):
    job = store.start("cliente-a", "alice")
    assert store.latest("cliente-a", "alice")["id"] == job["id"]
    assert store.latest("cliente-a", "bob") is None
    assert store.latest("cliente-b", "alice") is None
    with pytest.raises(HTTPException) as error:
        store.get("missing", "cliente-a", "alice")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        publish(store, job, [ad()], client="cliente-a", username="bob")


def test_discovery_history_is_atomic_idempotent_and_isolated_after_restart(store):
    job = store.start("cliente-a", "alice")
    assert store.discovery(job["id"], "id:MLB1", "cliente-a", "alice") == {"seen": False}
    checkpoint = {"page": {"index": 2}, "discoveredSeed": {"key": "id:MLB1", "id": "MLB1", "url": ad()["url"], "category_key": "cat:1", "status": "consulted"}}
    updated = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint)
    assert updated["checkpoint"] == {"page": {"index": 2}}
    restarted = PesquisaMercadoStore(store.info_root)
    assert restarted.discovery(job["id"], "id:MLB1", "cliente-a", "alice") == {"seen": True}
    restarted.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint)
    with sqlite3.connect(store._path("cliente-a")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_discovery").fetchone()[0] == 1
    for tenant, username in (("cliente-b", "alice"), ("cliente-a", "bob")):
        with pytest.raises(HTTPException) as error:
            restarted.discovery(job["id"], "id:MLB1", tenant, username)
        assert error.value.status_code == 404
    # An invalid counter prevents both cursor and history publication.
    with pytest.raises(HTTPException):
        restarted.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"discoveredSeed": {"key": "id:MLB2", "status": "failed"}}, counters={"pages": -1})
    assert restarted.discovery(job["id"], "id:MLB2", "cliente-a", "alice") == {"seen": False}
    with pytest.raises(HTTPException):
        restarted.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"discoveredSeed": {"key": "id:MLB2", "status": "waiting_login"}})


def test_pending_combines_missing_avant_and_failed_seeds_without_double_counting(store):
    job = store.start("cliente-a", "alice")
    assert publish(store, job, [ad(monthly=None)])["counters"]["pending"] == 1
    checkpoint = {"discoveredSeed": {"key": "MLB999", "status": "failed"}, "incompleteCategories": ["Ferramentas"]}
    failed = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint, counters={"pending": 999})
    assert failed["counters"]["pending"] == 2
    repeated = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint)
    assert repeated["counters"]["pending"] == 2
    # Seller publications preserve existing discovery failures when recounting.
    updated = publish(store, job, [ad(monthly=None, titulo="Produto atualizado"), ad(2)])
    assert updated["counters"]["pending"] == 2
    assert updated["counters"]["ads"] == 2
    assert updated["counters"]["sellers"] == 1
    consulted = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"discoveredSeed": {"key": "MLB999", "status": "consulted"}}, counters={"pending": 999, "ads": 999, "sellers": 999})
    assert consulted["counters"]["pending"] == 1
    assert consulted["counters"]["ads"] == 2
    assert consulted["counters"]["sellers"] == 1
    other = store.start("cliente-a", "bob")
    assert other["counters"]["pending"] == 0
    assert publish(store, job, [ad(), ad(2)])["counters"]["pending"] == 0


def test_checkpoint_persistence_login_pause_resume_and_terminal_cancel(store):
    job = store.start("cliente-a", "alice")
    cursor = {"version": 1, "categories": [{"key": "1", "nome": "Ferramentas"}], "categoryIndex": 0, "page": {"url": "https://lista.mercadolivre.com.br/", "index": 7}, "seenSellerKeys": ["id:123"], "phase": "seller"}
    paused = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=cursor, status="waiting_login", counters={"pages": 3, "categories": 1}, message="Avant solicita login.")
    restarted = PesquisaMercadoStore(store.info_root)
    assert restarted.get(job["id"], "cliente-a", "alice") == paused
    assert restarted.get(job["id"], "cliente-a", "alice")["checkpoint"] == cursor
    restarted.checkpoint(job["id"], "cliente-a", "alice", status="running")
    cancelled = restarted.checkpoint(job["id"], "cliente-a", "alice", status="cancelled")
    assert restarted.checkpoint(job["id"], "cliente-a", "alice", status="running", checkpoint={}) == cancelled
    assert restarted.checkpoint(job["id"], "cliente-a", "alice", status="cancelled") == cancelled
    with pytest.raises(HTTPException) as error:
        publish(restarted, job, [ad()])
    assert error.value.status_code == 409


def test_failed_job_can_resume_unless_a_new_job_is_active(store):
    job = store.start("cliente-a", "alice")
    store.checkpoint(job["id"], "cliente-a", "alice", status="failed")
    resumed = store.checkpoint(job["id"], "cliente-a", "alice", status="running")
    assert resumed["status"] == "running"
    store.checkpoint(job["id"], "cliente-a", "alice", status="failed")
    store.start("cliente-a", "alice")
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", status="running")
    assert error.value.status_code == 409


def recovery_job(store):
    root = scope_category("MLB100", "Pecas", "https://lista.mercadolivre.com.br/ciclismo/pecas/")
    done = scope_category("MLB101", "Freios", root["url"] + "freios/")
    failed = scope_category("MLB102", "Pastilhas", root["url"] + "pastilhas/")
    job = store.start("cliente-a", "alice", category=root)
    publish(store, job, [ad(categoria=done["nome"], categoria_key=done["key"])])
    checkpoint = {**job["checkpoint"], "categories": [root, done, failed], "categoryIndex": 3,
                  "currentCategory": failed, "page": None, "seenSellerKeys": ["id:123"],
                  "incompleteCategories": [{**failed, "motivo": "Categoria indisponivel."}],
                  "officialTaxonomy": {"updatedAt": "2026-10-22T12:00:00Z", "ancestry": [root],
                                       "children": {root["key"]: [done, failed], done["key"]: [], failed["key"]: []}}}
    for key, category, status in [("consulted-seed", failed, "consulted"), ("failed-a", failed, "failed"),
                                   ("failed-b", failed, "failed"), ("outside-failure", done, "failed")]:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={**checkpoint, "discoveredSeed": {
            "key": key, "id": "MLB9", "url": ad(9)["url"], "category_key": category["key"], "status": status}})
    return store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint,
                            status="completed_partial", counters={"categories": 3, "pages": 1})


def test_recover_incomplete_preserves_scope_publications_discovery_and_original_cursor(store):
    job = recovery_job(store)
    ranking = store.ranking(job["id"], "cliente-a", "alice")["items"]
    sellers = store.sellers(job["id"], "cliente-a", "alice")
    recovered = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    assert recovered["id"] == job["id"]
    assert recovered["status"] == "running"
    assert recovered["generation"] == 1
    assert recovered["revision"] == job["revision"] + 1
    assert recovered["counters"] == job["counters"]
    assert recovered["checkpoint"]["recovery"] == {"version": 1, "generation": 1, "categoryKeys": ["MLB102"], "index": 0}
    for key in ("scope", "categories", "categoryIndex", "officialTaxonomy", "seenSellerKeys", "incompleteCategories"):
        assert recovered["checkpoint"][key] == job["checkpoint"][key]
    assert store.ranking(job["id"], "cliente-a", "alice")["items"] == ranking
    assert store.sellers(job["id"], "cliente-a", "alice") == sellers
    assert store.discovery(job["id"], "consulted-seed", "cliente-a", "alice", include_status=True) == {"seen": True, "status": "consulted"}
    assert store.discovery(job["id"], "failed-a", "cliente-a", "alice") == {"seen": True}
    restarted = PesquisaMercadoStore(store.info_root)
    assert restarted.get(job["id"], "cliente-a", "alice") == recovered


def test_failed_total_discovery_can_recover_its_pending_categories(store):
    job = recovery_job(store)
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET status='failed',counters=json_set(counters,'$.pages',0) WHERE id=?", (job["id"],))
    recovered = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    assert recovered["status"] == "running"
    assert recovered["checkpoint"]["recovery"]["categoryKeys"] == ["MLB102"]


@pytest.mark.parametrize("pending", ["Pastilhas", {"nome": "Pastilhas", "motivo": "Categoria indisponivel."}])
def test_recover_incomplete_accepts_unique_legacy_category_without_detailed_cause(store, pending):
    job = recovery_job(store)
    # Simulate the stored legacy representation, before terminal write guards apply.
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET checkpoint=json_set(checkpoint,'$.incompleteCategories',json(?)) WHERE id=?",
                     (pesquisa_mercado._json([pending], 10000), job["id"]))
    recovered = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    assert recovered["checkpoint"]["recovery"]["categoryKeys"] == ["MLB102"]


def test_recovery_rejects_ambiguous_legacy_identity_without_mutating_job(store):
    job = recovery_job(store)
    checkpoint = job["checkpoint"]
    checkpoint["categories"].append(scope_category("MLB103", "Pastilhas", "https://lista.mercadolivre.com.br/ciclismo/outras-pastilhas/"))
    checkpoint["incompleteCategories"] = ["Pastilhas"]
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET checkpoint=? WHERE id=?", (pesquisa_mercado._json(checkpoint, 100000), job["id"]))
    before = store.get(job["id"], "cliente-a", "alice")
    with pytest.raises(HTTPException) as error:
        store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    assert error.value.status_code == 409
    assert store.get(job["id"], "cliente-a", "alice") == before


def test_recovery_is_atomic_and_rejects_stale_revision_other_owner_and_active_job(store):
    job = recovery_job(store)
    for tenant, username in (("cliente-b", "alice"), ("cliente-a", "bob")):
        with pytest.raises(HTTPException) as error:
            store.recover_incomplete(job["id"], tenant, username, expected_revision=job["revision"])
        assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"] - 1)
    assert error.value.status_code == 409
    barrier = threading.Barrier(2)

    def recover(_):
        barrier.wait()
        try:
            return store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])["generation"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(recover, range(2))) == [1, 409]
    active = store.get(job["id"], "cliente-a", "alice")
    store.checkpoint(job["id"], "cliente-a", "alice", status="completed_partial", generation=1)
    latest = store.get(job["id"], "cliente-a", "alice")
    other = store.start("cliente-a", "alice")
    with pytest.raises(HTTPException) as error:
        store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=latest["revision"])
    assert error.value.status_code == 409
    assert store.get(job["id"], "cliente-a", "alice")["generation"] == active["generation"]
    assert store.get(other["id"], "cliente-a", "alice")["status"] == "running"


def test_recovery_generation_rejects_late_checkpoint_publication_and_queue_mutation(store):
    job = recovery_job(store)
    recovered = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    for generation in (None, 0, 2):
        with pytest.raises(HTTPException) as error:
            store.checkpoint(job["id"], "cliente-a", "alice", status="completed", checkpoint={}, generation=generation)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            store.publish(job["id"], "id:123", "cliente-a", "alice", seller(), [ad(titulo="Late replacement")], generation=generation)
        assert error.value.status_code == 409
    altered = {**recovered["checkpoint"], "recovery": {**recovered["checkpoint"]["recovery"], "categoryKeys": ["MLB101"]}}
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=altered, generation=1)
    assert error.value.status_code == 422
    boolean_generation = {**recovered["checkpoint"], "recovery": {**recovered["checkpoint"]["recovery"], "generation": True}}
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=boolean_generation, generation=1)
    assert error.value.status_code == 422
    assert store.get(job["id"], "cliente-a", "alice") == recovered
    store.checkpoint(job["id"], "cliente-a", "alice", status="completed_partial", generation=1)
    closed = store.get(job["id"], "cliente-a", "alice")
    second = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=closed["revision"])
    assert second["generation"] == 2
    with pytest.raises(HTTPException):
        store.checkpoint(job["id"], "cliente-a", "alice", status="completed", generation=1)


def test_recovery_and_new_start_cannot_create_two_active_jobs(store):
    job = recovery_job(store)
    barrier = threading.Barrier(2)

    def run(action):
        barrier.wait()
        try:
            if action == "start":
                return store.start("cliente-a", "alice")["id"]
            return store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])["id"]
        except HTTPException as error:
            assert error.status_code == 409
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(run, ("start", "recover")))
    with store._connection("cliente-a") as conn:
        active = conn.execute("SELECT id FROM pm_jobs WHERE tenant='cliente-a' AND username='alice' AND status IN ('running','paused','waiting_login')").fetchall()
    assert len(active) == 1
    assert {result for result in outcomes if result} == {active[0]["id"]}


def test_failed_seed_recovery_preserves_consulted_and_keyset_pagination_while_resolving(store):
    job = recovery_job(store)
    recovered = store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    first = store.failed_seeds(job["id"], "cliente-a", "alice", category_key="MLB102", limit=1)
    assert first == {"items": [{"key": "failed-a", "id": "MLB9", "url": ad(9)["url"], "category_key": "MLB102"}], "total": 2, "generation": 1}
    checkpoint = {**recovered["checkpoint"], "discoveredSeed": {**first["items"][0], "status": "consulted"}}
    updated = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint, generation=1)
    second = store.failed_seeds(job["id"], "cliente-a", "alice", category_key="MLB102", after_key=first["items"][0]["key"], limit=1)
    assert [item["key"] for item in second["items"]] == ["failed-b"]
    assert second["total"] == 1
    assert updated["counters"]["pending"] == job["counters"]["pending"] - 1
    assert store.discovery(job["id"], "failed-a", "cliente-a", "alice", include_status=True) == {"seen": True, "status": "consulted"}
    assert store.discovery(job["id"], "outside-failure", "cliente-a", "alice", include_status=True)["status"] == "failed"
    with pytest.raises(HTTPException):
        store.failed_seeds(job["id"], "cliente-a", "alice", category_key="MLB101")


def test_http_recovery_has_closed_request_generation_and_owner_checks(client, store):
    job = recovery_job(store)
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    headers = {"Authorization": "Bearer cliente-a:alice"}
    for body in ({}, {"expected_revision": True}, {"expected_revision": str(job["revision"])}, {"expected_revision": job["revision"], "categoryKeys": ["outside"]}):
        assert client.post(base + "/recover-incomplete", json=body, headers=headers).status_code == 422
    assert client.post(base + "/recover-incomplete", json={"expected_revision": job["revision"]}, headers={"Authorization": "Bearer cliente-a:bob"}).status_code == 404
    recovered = client.post(base + "/recover-incomplete", json={"expected_revision": job["revision"]}, headers=headers)
    assert recovered.status_code == 200
    assert recovered.json()["job"]["generation"] == 1
    assert client.patch(base + "/checkpoint", json={"status": "paused"}, headers=headers).status_code == 409
    assert client.patch(base + "/checkpoint", json={"status": "paused", "generation": True}, headers=headers).status_code == 422
    assert client.patch(base + "/checkpoint", json={"status": "paused", "generation": 1}, headers=headers).status_code == 200
    assert client.get(base + "/failed-seeds?category_key=MLB102&limit=1", headers=headers).json()["total"] == 2
    assert client.get(base + "/failed-seeds?category_key=MLB102", headers={"Authorization": "Bearer cliente-b:alice"}).status_code == 404
    assert client.get(base + "/descoberta?seed_key=failed-a", headers=headers).json() == {"seen": True}
    assert client.get(base + "/descoberta?seed_key=failed-a&include_status=true", headers=headers).json() == {"seen": True, "status": "failed"}


def test_recovery_diagnostic_is_compact_closed_and_owner_scoped(client, store):
    job = recovery_job(store)
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET checkpoint=json_set(checkpoint,'$.private_cursor',?,'$.incompleteCategories[0].code',?,'$.incompleteCategories[0].stage',?,'$.incompleteCategories[0].dependency_key',?) WHERE id=?",
                     ("x" * 100000, "category_link_missing", "navigation", "MLB100", job["id"]))
    headers = {"Authorization": "Bearer cliente-a:alice"}
    endpoint = f"/api/pesquisa-mercado/jobs/{job['id']}/diagnostico"
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"scopePath", "incompleteCategories", "incompleteCategoriesTotal", "salesRecoveryPending", "recovery"}
    assert body["incompleteCategoriesTotal"] == 1
    assert body["salesRecoveryPending"] is False
    assert body["scopePath"] == [job["checkpoint"]["scope"]["category"]]
    assert body["recovery"] is None
    assert body["incompleteCategories"] == [{"key": "MLB102", "nome": "Pastilhas", "code": "category_link_missing", "motivo": "O link oficial desta categoria nao foi localizado.", "stage": "navigation", "dependency_key": "MLB100"}]
    assert len(response.content) < 3000
    assert client.get(endpoint, headers={"Authorization": "Bearer cliente-a:bob"}).status_code == 404
    store.recover_incomplete(job["id"], "cliente-a", "alice", expected_revision=job["revision"])
    assert client.get(endpoint, headers=headers).json()["recovery"] == {"generation": 1, "index": 0, "total": 1}


def test_recovery_diagnostic_caps_legacy_items_and_reports_the_full_total(store):
    job = recovery_job(store)
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET checkpoint=json_set(checkpoint,'$.incompleteCategories',json(?)) WHERE id=?",
                     (pesquisa_mercado._json(["Pastilhas"] * 1005, 100000), job["id"]))
    diagnostic = store.diagnostic(job["id"], "cliente-a", "alice")
    assert diagnostic["incompleteCategoriesTotal"] == 1005
    assert len(diagnostic["incompleteCategories"]) == 1000
    assert {item["code"] for item in diagnostic["incompleteCategories"]} == {"legacy_unknown"}
    assert {item["motivo"] for item in diagnostic["incompleteCategories"]} == {"Motivo anterior desconhecido."}


def test_diagnostic_exposes_metrics_only_pending_and_allows_another_recovery(client, store):
    job = recovery_job(store)
    with store._connection("cliente-a", write=True) as conn:
        conn.execute("UPDATE pm_jobs SET checkpoint=json_set(checkpoint,'$.incompleteCategories',json('[]'),'$.salesRecoveryPending',json('true')) WHERE id=?", (job["id"],))
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    headers = {"Authorization": "Bearer cliente-a:alice"}
    diagnostic = client.get(base + "/diagnostico", headers=headers).json()
    assert diagnostic["salesRecoveryPending"] is True
    assert diagnostic["incompleteCategoriesTotal"] == 0
    assert diagnostic["incompleteCategories"] == []
    recovered = client.post(base + "/recover-incomplete", json={"expected_revision": job["revision"]}, headers=headers)
    assert recovered.status_code == 200
    assert recovered.json()["job"]["checkpoint"]["recovery"] == {"version": 1, "generation": 1, "categoryKeys": [], "index": 0}


def test_idempotent_atomic_seller_publication_and_authoritative_counters(store):
    job = store.start("cliente-a", "alice")
    first = publish(store, job, [ad(), ad(), ad(2, monthly=None)])
    duplicate = publish(store, job, [ad(), ad(), ad(2, monthly=None)])
    assert first == duplicate
    assert first["counters"] == {"categories": 0, "pages": 0, "sellers": 1, "ads": 2, "pending": 1}
    # Retry with refreshed metrics updates one seller instead of duplicating ads.
    updated = publish(store, job, [ad(monthly=20), ad(3, monthly=5)])
    assert updated["counters"]["ads"] == 2
    assert updated["counters"]["sellers"] == 1
    ranking = store.ranking(job["id"], "cliente-a", "alice")["items"]
    assert ranking[0]["id"] == "MLB1"
    assert ranking[0]["ordem_coleta"] == 1
    assert ranking[1]["id"] == "MLB3"
    assert ranking[1]["ordem_coleta"] == 3
    checkpoint = store.checkpoint(job["id"], "cliente-a", "alice", counters={"ads": 9999, "sellers": 9999, "pending": 9999})
    assert checkpoint["counters"]["ads"] == 2


def test_seller_lookup_recovers_publication_before_cursor_checkpoint(store):
    job = store.start("cliente-a", "alice")
    published = publish(store, job, [ad()])
    assert published["checkpoint"] == {}
    # Simulate interruption between the successful PUT and the runner checkpoint.
    restarted = PesquisaMercadoStore(store.info_root)
    items = restarted.sellers(job["id"], "cliente-a", "alice", seller_key="id:123")["items"]
    assert len(items) == 1
    assert items[0]["seller_key"] == "id:123"
    assert items[0]["ads"] == 1
    assert restarted.get(job["id"], "cliente-a", "alice")["checkpoint"].get("seenSellerKeys", []) == []


def test_seller_lookup_is_exact_and_supports_profile_keys(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad()])
    key = "perfil:https://www.mercadolivre.com.br/perfil/" + "loja" * 90
    profile_seller = {"key": key, "nome": "Loja Perfil", "perfilUrl": key[7:]}
    store.publish(job["id"], key, "cliente-a", "alice", profile_seller, [ad(2, owner="", seller_perfil_url=key[7:])])
    assert len(store.sellers(job["id"], "cliente-a", "alice")["items"]) == 2
    assert store.sellers(job["id"], "cliente-a", "alice", seller_key="ID:123")["items"] == []
    assert store.sellers(job["id"], "cliente-a", "alice", seller_key="missing")["items"] == []
    assert store.sellers(job["id"], "cliente-a", "alice", seller_key=key)["items"][0]["seller_key"] == key
    for tenant, username in (("cliente-b", "alice"), ("cliente-a", "bob")):
        with pytest.raises(HTTPException) as error:
            store.sellers(job["id"], tenant, username, seller_key=key)
        assert error.value.status_code == 404


def test_publication_rolls_back_if_insert_fails(store):
    job = store.start("cliente-a", "alice")
    old = publish(store, job, [ad()])
    path = store._path("cliente-a")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER test_abort BEFORE INSERT ON pm_ads WHEN NEW.ad_key='id:MLB2' BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(HTTPException) as error:
        publish(store, job, [ad(monthly=500), ad(2)])
    assert error.value.status_code == 503
    assert store.get(job["id"], "cliente-a", "alice") == old
    result = store.ranking(job["id"], "cliente-a", "alice")
    assert result["total"] == 1
    assert result["items"][0]["media_vendas_mensal"] == 10


def test_ranking_uses_supplied_shared_average_then_sales_position_collection(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(1, monthly=10, sales=1000), ad(2, monthly=20, sales=1), ad(3, monthly=20, sales=5, position=3), ad(4, monthly=20, sales=5, position=1), ad(5, monthly=20, sales=5, position=1), ad(6, monthly=999, vendasFonte="mercadolivre")])
    result = store.ranking(job["id"], "cliente-a", "alice")
    assert [row["id"] for row in result["items"]] == ["MLB4", "MLB5", "MLB3", "MLB2", "MLB1", "MLB6"]
    assert [row["rank"] for row in result["items"]] == [1, 2, 3, 4, 5, None]
    assert result["items"][3]["media_vendas_mensal"] == 20
    assert result["items"][3]["data_criacao"] == "2026-01-01"


def test_ranking_preserves_favoritos_tolerance_and_unknown_position(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(1, monthly=20.00005, sales=10, position=1), ad(2, monthly=20, sales=100, position=2), ad(3, monthly=20, sales=100, position=0)])
    rows = store.ranking(job["id"], "cliente-a", "alice")["items"]
    assert [row["id"] for row in rows] == ["MLB2", "MLB3", "MLB1"]


def test_avant_provenance_and_seller_specific_catalog_metrics(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [
        ad(1), ad(2, owner="999"), ad(3, owner=""), ad(4, vendasFonte="mercadolivre"),
        ad(5, catalogo=True), ad(6, catalogo=True, avant_seller_id="123"),
        ad(7, catalogo=True, avant_seller_id="999"), ad(8, monthly=None),
        ad(9, monthly=0, sales=0, data_criacao=""), ad(10, rankable=False),
    ])
    rows = {row["id"]: row for row in store.ranking(job["id"], "cliente-a", "alice")["items"]}
    assert {key for key, row in rows.items() if row["rankable"]} == {"MLB1", "MLB6", "MLB9"}
    assert "vendedor" in rows["MLB2"]["erro"].lower()
    assert "catalogo" in rows["MLB5"]["erro"].lower()
    assert "Avant" in rows["MLB4"]["erro"]
    assert rows["MLB9"]["rank"] is not None


def test_direct_monthly_avant_average_waits_for_total_sales(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(1, monthly=50, sales=None, vendasFonte="", media_mensal_fonte="avantpro_media", data_criacao=""), ad(2, monthly=50, sales=0)])
    rows = store.ranking(job["id"], "cliente-a", "alice")["items"]
    assert [row["id"] for row in rows] == ["MLB2", "MLB1"]
    assert rows[0]["rankable"] is True
    assert rows[1]["rankable"] is False
    assert rows[1]["rank"] is None
    assert rows[1]["vendas"] is None
    assert rows[1]["media_vendas_mensal"] == 50
    assert "Quantidade de vendas" in rows[1]["erro"]


def test_catalog_warning_keeps_missing_metrics_visible(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(catalogo=True, monthly=None), ad(2, catalogo=True, monthly=10, sales=None, vendasFonte="", media_mensal_fonte="avantpro_media")])
    rows = {row["id"]: row for row in store.ranking(job["id"], "cliente-a", "alice")["items"]}
    assert "catalogo" in rows["MLB1"]["erro"]
    assert "Media mensal" in rows["MLB1"]["erro"]
    assert "catalogo" in rows["MLB2"]["erro"]
    assert "Quantidade de vendas" in rows["MLB2"]["erro"]
    assert all(not row["rankable"] for row in rows.values())


def test_complete_metrics_require_avant_total_source_and_preserve_monthly_alias(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [
        ad(1, sales=100, vendasFonte="mercadolivre", media_mensal_fonte="avantpro_media"),
        ad(2, monthly=0, sales=0, media_vendas_mensal_fonte="calculado_vendas_dias_avantpro"),
    ])
    rows = {row["id"]: row for row in store.ranking(job["id"], "cliente-a", "alice")["items"]}
    assert rows["MLB1"]["rankable"] is False
    assert "Fonte da quantidade" in rows["MLB1"]["erro"]
    assert rows["MLB2"]["rankable"] is True
    assert rows["MLB2"]["vendas"] == rows["MLB2"]["media_vendas_mensal"] == 0
    assert rows["MLB2"]["media_mensal_fonte"] == "calculado_vendas_dias_avantpro"


def test_partial_observation_becomes_ranked_after_missing_total_is_collected(store):
    job = store.start("cliente-a", "alice")
    first = publish(store, job, [ad(monthly=10, sales=None, media_mensal_fonte="avantpro_media")])
    assert first["counters"]["pending"] == 1
    completed = publish(store, job, [ad(monthly=10, sales=100, media_mensal_fonte="avantpro_media")])
    assert completed["counters"]["pending"] == 0
    row = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert row["rankable"] is True
    assert row["vendas"] == 100
    assert row["media_vendas_mensal"] == 10


@pytest.mark.parametrize("monthly", [None, 10])
@pytest.mark.parametrize("proof", [{"avant_seller_id": "999"}, {"avant_seller_perfil_url": "https://example.test/perfil/LOJA"}])
def test_explicit_avant_seller_conflict_blocks_even_non_catalog_metrics(store, monthly, proof):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(monthly=monthly, **proof)])
    row = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert row["rankable"] is False
    assert "vendedor nas metricas" in row["erro"]
    if monthly is None:
        assert "Media mensal" in row["erro"]


def test_partial_metric_warning_preserves_observed_identity_failure(store):
    job = store.start("cliente-a", "alice")
    observed = "Nao foi possivel confirmar que o anuncio pertence ao vendedor pesquisado."
    publish(store, job, [ad(monthly=None, rankable=False, erro=observed)])
    row = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert row["rankable"] is False
    assert observed in row["erro"]
    assert "Media mensal" in row["erro"]


def test_user_product_url_does_not_imply_aggregate_catalog_sales(store):
    job = store.start("cliente-a", "alice")
    url = "https://www.mercadolivre.com.br/produto/up/MLBU123456?wid=MLB654321"
    publish(store, job, [ad(id="MLBU123456", url=url, catalogo=False)])
    row = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert row["id"] == "MLB654321"
    assert row["catalogo"] is False
    assert row["rankable"] is True


@pytest.mark.parametrize("query,expected_id", [
    ("", ""), ("?wid=MLB123456", "MLB123456"),
    ("?item_id=MLB654321", "MLB654321"),
    ("?pdp_filters=item_id%3AMLB111222", "MLB111222"),
])
def test_catalog_url_uses_listing_identity_only_when_observed(store, query, expected_id):
    job = store.start("cliente-a", "alice")
    url = "https://www.mercadolivre.com.br/produto/p/MLB999000" + query
    publish(store, job, [ad(id="", url=url, catalogo=True, avant_seller_id="123")])
    result = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert result["id"] == expected_id
    assert result["url"] == url


def test_catalog_id_cannot_be_promoted_to_listing_id_from_path_or_raw_id(store):
    job = store.start("cliente-a", "alice")
    url = "https://www.mercadolivre.com.br/produto/p/MLB999000"
    publish(store, job, [ad(id="MLB999000", url=url, catalogo=True, avant_seller_id="123")])
    assert store.ranking(job["id"], "cliente-a", "alice")["items"][0]["id"] == ""


def test_classic_listing_path_remains_a_valid_identity(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(id="", url="https://produto.mercadolivre.com.br/MLB-123456-produto-_JM")])
    assert store.ranking(job["id"], "cliente-a", "alice")["items"][0]["id"] == "MLB123456"


def test_canonical_profile_identity_and_unknown_seller(store):
    job = store.start("cliente-a", "alice")
    profile_seller = {"key": "profile:minha-loja", "nome": "Loja", "perfilUrl": "https://www.mercadolivre.com.br/perfil/MINHA+LOJA"}
    store.publish(job["id"], profile_seller["key"], "cliente-a", "alice", profile_seller, [ad(1, owner="", seller_perfil_url="https://www.mercadolivre.com.br/perfil/minha+loja/"), ad(2, owner="")])
    rows = store.ranking(job["id"], "cliente-a", "alice")["items"]
    assert rows[0]["rankable"] is True
    assert rows[1]["rankable"] is False
    unknown_seller = {"key": "unknown:loja", "nome": "Loja"}
    store.publish(job["id"], unknown_seller["key"], "cliente-a", "alice", unknown_seller, [ad(3)])
    rows = store.ranking(job["id"], "cliente-a", "alice", seller_key="unknown:loja")["items"]
    assert rows[0]["rankable"] is False


def test_all_first_page_ads_survive_pagination_filters_and_seller_rankings(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad(number, monthly=number) for number in range(1, 154)])
    publish(store, job, [ad(999, owner="456", monthly=1000)], number="456", category="Autopecas")
    first = store.ranking(job["id"], "cliente-a", "alice", limit=100)
    second = store.ranking(job["id"], "cliente-a", "alice", offset=100, limit=100)
    assert first["total"] == second["total"] == 154
    assert len(first["items"]) == 100
    assert len(second["items"]) == 54
    assert second["items"][0]["rank"] == 101
    assert store.ranking(job["id"], "cliente-a", "alice", category="Ferramentas")["total"] == 153
    individual = store.ranking(job["id"], "cliente-a", "alice", seller_key="id:456")["items"]
    assert len(individual) == 1 and individual[0]["rank"] == 1
    assert store.ranking(job["id"], "cliente-a", "alice", q="Produto 999")["total"] == 1
    assert store.ranking(job["id"], "cliente-a", "alice", q="%_")["total"] == 0
    sellers = store.sellers(job["id"], "cliente-a", "alice")["items"]
    assert [item["ads"] for item in sellers] == [153, 1]
    store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"incompleteCategories": ["Autopecas"]})
    assert store.categories(job["id"], "cliente-a", "alice")["items"] == [{"categoria": "Autopecas", "status": "incomplete"}, {"categoria": "Ferramentas", "status": "completed"}]


@pytest.mark.parametrize("status", ["paused", "waiting_login", "cancelled", "failed"])
@pytest.mark.parametrize("finished", [False, True])
def test_stopped_current_category_is_incomplete_only_while_cursor_is_open(store, status, finished):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad()])
    category = {"key": "cat:1", "nome": "Ferramentas", "url": "https://lista.mercadolivre.com.br/ferramentas"}
    checkpoint = {"categories": [category], "categoryIndex": 1 if finished else 0, "currentCategory": category, "page": None if finished else {"url": category["url"], "index": 3}, "incompleteCategories": []}
    stopped = store.checkpoint(job["id"], "cliente-a", "alice", checkpoint=checkpoint, status=status)
    assert store.categories(job["id"], "cliente-a", "alice")["items"] == [{"categoria": "Ferramentas", "status": "completed" if finished else "incomplete"}]
    # This label is derived for the response; it is not a permanent error or a pending count.
    assert store.get(job["id"], "cliente-a", "alice")["checkpoint"] == checkpoint
    assert stopped["counters"]["pending"] == 0
    if status != "cancelled":
        store.checkpoint(job["id"], "cliente-a", "alice", status="running")
        assert store.categories(job["id"], "cliente-a", "alice")["items"] == [{"categoria": "Ferramentas", "status": "completed"}]


def test_stopped_checkpoint_does_not_mark_the_previous_finished_category_incomplete(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, [ad()])
    previous = {"key": "cat:1", "nome": "Ferramentas"}
    next_category = {"key": "cat:2", "nome": "Autopecas"}
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused", checkpoint={"categories": [previous, next_category], "categoryIndex": 1, "currentCategory": previous, "page": None})
    assert store.categories(job["id"], "cliente-a", "alice")["items"] == [{"categoria": "Ferramentas", "status": "completed"}]


@pytest.mark.parametrize("bad", ["../escape", "..", "a/b", "a\\b", "C:foo", "", "."])
def test_tenant_path_traversal_is_rejected(store, bad):
    with pytest.raises(HTTPException) as error:
        store.start(bad, "alice")
    assert error.value.status_code == 401


def test_bounds_do_not_truncate_checkpoint_or_allow_nonfinite_metrics(store, monkeypatch):
    job = store.start("cliente-a", "alice")
    monkeypatch.setattr(pesquisa_mercado, "MAX_CHECKPOINT_BYTES", 100)
    with pytest.raises(HTTPException) as error:
        store.checkpoint(job["id"], "cliente-a", "alice", checkpoint={"seen": "x" * 200})
    assert error.value.status_code == 413
    assert store.get(job["id"], "cliente-a", "alice")["checkpoint"] == {}
    publish(store, job, [ad(monthly=float("nan")), ad(2, sales=float("inf"), monthly=float("inf")), ad(3, monthly=-1)])
    assert not any(row["rankable"] for row in store.ranking(job["id"], "cliente-a", "alice")["items"])


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)

    async def auth(request: Request, authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Login necessario.")
        client_id, username = authorization[7:].split(":", 1)
        if username == "sem-permissao":
            raise HTTPException(403, "Permissao favoritos necessaria.")
        request.state.username = username
        request.state.client_id = client_id
        return client_id

    monkeypatch.setattr(favoritos_endpoints, "_TENANT_DEPENDENCY", auth)
    app = FastAPI()
    app.include_router(create_pesquisa_mercado_router())
    return TestClient(app)


def test_http_contract_auth_statuses_and_strict_owner_requests(client):
    assert client.get("/api/pesquisa-mercado/jobs/latest").status_code == 401
    assert client.post("/api/pesquisa-mercado/jobs", json={}, headers={"Authorization": "Bearer cliente-a:sem-permissao"}).status_code == 403
    headers = {"Authorization": "Bearer cliente-a:alice", "X-Username": "bob"}
    assert client.get("/api/pesquisa-mercado/jobs/latest", headers=headers).json() == {"job": None}
    response = client.post("/api/pesquisa-mercado/jobs", json={}, headers=headers)
    assert response.status_code == 200
    job = response.json()["job"]
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    assert set(job) == {"id", "status", "revision", "checkpoint", "counters", "message", "created_at", "updated_at"}
    assert client.get(base, headers={"Authorization": "Bearer cliente-a:bob"}).status_code == 404
    assert client.post("/api/pesquisa-mercado/jobs", json={"client_id": "cliente-b"}, headers=headers).status_code == 422
    assert client.patch(base + "/checkpoint", json={"status": "auth_required"}, headers=headers).status_code == 422
    assert client.patch(base + "/checkpoint", json={"status": "waiting_login"}, headers=headers).json()["job"]["status"] == "waiting_login"
    result = client.put(base + "/vendedores/id:123", json={"seller": seller(), "anuncios": [ad()]}, headers=headers)
    assert result.status_code == 200
    assert set(client.get(base + "/ranking", headers=headers).json()) == {"items", "total", "revision"}
    assert client.get(base + "/ranking?limit=101", headers=headers).status_code == 422
    assert client.get(base + "/ranking?offset=-1", headers=headers).status_code == 422
    assert client.get(base + "/vendedores", headers=headers).json()["items"][0]["seller_key"] == "id:123"
    assert client.get(base + "/vendedores?seller_key=id:123", headers=headers).json()["items"][0]["seller_key"] == "id:123"
    assert client.get(base + "/vendedores?seller_key=missing", headers=headers).json() == {"items": []}
    assert client.get(base + "/vendedores?seller_key=" + "x" * 501, headers=headers).status_code == 422
    assert client.get(base + "/vendedores?seller_key=id:123", headers={"Authorization": "Bearer cliente-a:bob"}).status_code == 404
    assert client.get(base + "/categorias", headers=headers).json()["items"][0]["categoria"] == "Ferramentas"
    assert client.get(base + "/descoberta?seed_key=id:MLB1", headers=headers).json() == {"seen": False}


def test_http_category_schema_scope_and_ownership(client, store):
    headers = {"Authorization": "Bearer cliente-a:alice"}
    # Invalid forms fail before the persistence layer creates a database.
    for category in ({**scope_category(), "extra": True}, scope_category(key=123), scope_category(key="x" * 501), scope_category(url="https://lista.mercadolivre.com.br/_CustId_123")):
        assert client.post("/api/pesquisa-mercado/jobs", json={"category": category}, headers=headers).status_code == 422
        assert not store.info_root.exists()
    response = client.post("/api/pesquisa-mercado/jobs", json={"category": scope_category()}, headers=headers)
    assert response.status_code == 200
    job = response.json()["job"]
    assert job["checkpoint"]["scope"] == {"category": scope_category()}
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    assert client.get(base, headers={"Authorization": "Bearer cliente-a:bob"}).status_code == 404
    assert client.get(base, headers={"Authorization": "Bearer cliente-b:alice"}).status_code == 404
    assert client.patch(base + "/checkpoint", json={"checkpoint": {"phase": "seller"}}, headers=headers).json()["job"]["checkpoint"]["scope"] == {"category": scope_category()}
    assert client.patch(base + "/checkpoint", json={"checkpoint": {"scope": None}}, headers=headers).status_code == 422


@pytest.mark.parametrize("url", [
    "https://lista.mercadolivre.com.br/veiculos/caminhoes/",
    "https://lista.mercadolivre.com.br/veiculos/caminhoes",
    "https://lista.mercadolivre.com.br/veiculos/caminhoes/?category_id=MLB5828&brand=Ford&brand=Volvo&variant=",
    "https://lista.mercadolivre.com.br/veiculos/caminhoes/usados/volvo/",
    "https://imoveis.mercadolivre.com.br/apartamentos/",
])
def test_http_category_path_survives_start_restart_and_deep_resume(client, store, monkeypatch, url):
    headers = {"Authorization": "Bearer cliente-a:alice"}
    selected = scope_category("selected", "Categoria selecionada", url)
    response = client.post("/api/pesquisa-mercado/jobs", json={"category": selected}, headers=headers)
    assert response.status_code == 200
    job = response.json()["job"]
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    assert job["checkpoint"]["scope"] == {"category": selected}
    assert job["checkpoint"]["categories"] == [selected]

    path, separator, query = url.partition("?")
    prefix = path if path.endswith("/") else path + "/"
    suffix = separator + query
    child = scope_category("child", "Categoria filha", prefix + "filha/" + suffix)
    deep = scope_category("deep", "Categoria profunda", prefix + "filha/profunda/" + suffix)
    cursor = {
        "version": 1, "categories": [selected, child, deep], "categoryIndex": 2,
        "currentCategory": deep, "page": {"url": deep["url"], "index": 5},
        "phase": "seller", "discoveryInitialized": True, "seenSellerKeys": ["id:123"],
    }
    paused = client.patch(base + "/checkpoint", json={"checkpoint": cursor, "status": "paused"}, headers=headers)
    assert paused.status_code == 200
    expected = {**cursor, "scope": {"category": selected}}
    assert paused.json()["job"]["checkpoint"] == expected

    restarted = PesquisaMercadoStore(store.info_root)
    monkeypatch.setattr(pesquisa_mercado, "_STORE", restarted)
    restored = client.get(base, headers=headers)
    assert restored.status_code == 200
    assert restored.json()["job"]["checkpoint"] == expected
    assert restored.json()["job"]["status"] == "paused"
    for outsider in ["cliente-a:bob", "cliente-b:alice"]:
        other_headers = {"Authorization": "Bearer " + outsider}
        assert client.get(base, headers=other_headers).status_code == 404
        assert client.patch(base + "/checkpoint", json={"status": "running"}, headers=other_headers).status_code == 404
        assert client.get("/api/pesquisa-mercado/jobs/latest", headers=other_headers).json() == {"job": None}
    assert restarted.get(job["id"], "cliente-a", "alice")["checkpoint"] == expected

    resumed_cursor = {**cursor, "phase": "discovery"}
    resumed = client.patch(base + "/checkpoint", json={"checkpoint": resumed_cursor, "status": "running"}, headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["job"]["status"] == "running"
    expected_resumed = {**resumed_cursor, "scope": {"category": selected}}
    assert resumed.json()["job"]["checkpoint"] == expected_resumed
    assert PesquisaMercadoStore(store.info_root).get(job["id"], "cliente-a", "alice")["checkpoint"] == expected_resumed


@pytest.mark.parametrize("url,name", [
    ("https://imoveis.mercadolivre.com.br/apartamentos/", "Apartamentos"),
    ("https://servicos.mercadolivre.com.br/educacao/", "Educacao"),
])
def test_http_specialized_category_preserves_closed_schema_and_immutable_scope(client, store, url, name):
    headers = {"Authorization": "Bearer cliente-a:alice"}
    category = scope_category(url, name, url)
    assert client.post("/api/pesquisa-mercado/jobs", json={"category": {**category, "aliases": []}}, headers=headers).status_code == 422
    assert not store.info_root.exists()
    response = client.post("/api/pesquisa-mercado/jobs", json={"category": category}, headers=headers)
    assert response.status_code == 200
    job = response.json()["job"]
    base = f"/api/pesquisa-mercado/jobs/{job['id']}"
    expected_scope = {"category": {**category, "url": url}}
    assert job["checkpoint"]["scope"] == expected_scope
    assert client.patch(base + "/checkpoint", json={"checkpoint": {"scope": {"category": scope_category()}}}, headers=headers).status_code == 422
    assert client.get(base, headers=headers).json()["job"]["checkpoint"]["scope"] == expected_scope


def test_http_endpoints_use_stable_auth_dependency_and_sync_database_handlers():
    router = create_pesquisa_mercado_router()
    for route in router.routes:
        assert inspect.iscoroutinefunction(route.endpoint) is False
        assert favoritos_endpoints.get_tenant_id in [dependency.call for dependency in route.dependant.dependencies]
        for method in route.methods:
            assert _permissao_exigida_por_rota(route.path, method) == "favoritos"


def test_http_username_comes_only_from_verified_auth_state(monkeypatch, store):
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)

    async def auth_without_username(_request, _authorization=None):
        return "cliente-a"

    monkeypatch.setattr(favoritos_endpoints, "_TENANT_DEPENDENCY", auth_without_username)
    app = FastAPI()
    app.include_router(create_pesquisa_mercado_router())
    with TestClient(app) as client:
        response = client.post("/api/pesquisa-mercado/jobs", json={}, headers={"X-Username": "alice"})
    assert response.status_code == 401


def test_production_tenant_dependency_enforces_permissions_on_all_new_routes(monkeypatch, store):
    # Execute the real auth boundary without bootstrapping the desktop backend.
    from backend.services import central_accounts_client

    monkeypatch.setattr(central_accounts_client, "bind_request", lambda *_: None)
    monkeypatch.setattr(central_accounts_client, "assert_manual_path", lambda *_: None)
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)
    permissions = {"alice": {"favoritos": True}, "bob": {"vendas": True}}
    namespace = {
        "Request": Request, "Header": Header, "Optional": Optional,
        "HTTPException": HTTPException, "asyncio": asyncio, "JWTError": ValueError,
        "decodificar_access_token": lambda token: {"sub": token, "client_id": "cliente-a"},
        "_permissao_exigida_por_rota": _permissao_exigida_por_rota,
        "_normalizar_permissoes_exigidas": _normalizar_permissoes_exigidas,
        "_permissoes_autorizam_rota": _permissoes_autorizam_rota,
        "_carregar_permissoes_usuario": lambda username, _: permissions[username],
        "_machine_presence_auto_touch": lambda *_: None,
        "logger": SimpleNamespace(warning=lambda *_: None),
    }
    source = (Path(__file__).resolve().parents[1] / "backend_api.py").read_text(encoding="utf-8-sig")
    node = next(node for node in ast.parse(source).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_tenant_id")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "backend_auth_boundary", "exec"), namespace)
    monkeypatch.setattr(favoritos_endpoints, "_TENANT_DEPENDENCY", namespace["get_tenant_id"])
    app = FastAPI()
    app.include_router(create_pesquisa_mercado_router())
    with TestClient(app) as client:
        alice = {"Authorization": "Bearer alice", "X-Username": "bob"}
        denied = {"Authorization": "Bearer bob", "X-Username": "alice"}
        job = client.post("/api/pesquisa-mercado/jobs", json={}, headers=alice).json()["job"]
        base = f"/api/pesquisa-mercado/jobs/{job['id']}"
        requests = [
            ("POST", "/api/pesquisa-mercado/jobs", {}),
            ("GET", "/api/pesquisa-mercado/jobs/latest", None), ("GET", base, None),
            ("PATCH", base + "/checkpoint", {}),
            ("PUT", base + "/vendedores/id:123", {"seller": seller(), "anuncios": [ad()]}),
            ("GET", base + "/ranking", None), ("GET", base + "/vendedores", None),
            ("GET", base + "/categorias", None), ("GET", base + "/descoberta?seed_key=MLB1", None),
        ]
        for method, url, body in requests:
            assert client.request(method, url, json=body, headers=denied).status_code == 403
            assert client.request(method, url, json=body, headers=alice).status_code == 200
