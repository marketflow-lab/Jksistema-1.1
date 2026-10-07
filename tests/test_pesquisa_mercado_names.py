"""Name recovery proves identity while leaving historical observations intact."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.routers.pesquisa_mercado import create_pesquisa_mercado_router
from backend.services import favoritos_endpoints, pesquisa_mercado
from backend.services.pesquisa_mercado import PesquisaMercadoStore


@pytest.fixture
def store(tmp_path):
    return PesquisaMercadoStore(tmp_path / "info")


def seller(name="123", **extras):
    return {"key": "id:123", "id": "123", "nome": name,
            "perfilUrl": "https://lista.mercadolivre.com.br/_CustId_123", **extras}


def ad(number=1, **extras):
    return {"id": f"MLB{number}", "url": f"https://produto.mercadolivre.com.br/MLB-{number}-produto-_JM",
            "titulo": f"Produto {number}", "vendas": 100, "vendasFonte": "avantpro_anuncio",
            "media_vendas_mensal": 10, "seller_id": "123", "data_criacao": "2026-01-01", **extras}


def update(name="Loja recuperada", **extras):
    return {"seller_key": "id:123", "nome": name, "seller_id": "123",
            "seller_perfil_url": "https://lista.mercadolivre.com.br/_CustId_123",
            "seller_name_kind": "seller", "source": "perfil", **extras}


def publish(store, job, vendor=None, ads=None):
    return store.publish(job["id"], "id:123", "cliente-a", "alice", vendor or seller(), ads or [ad()])


def snapshot(store, job):
    with store._connection("cliente-a") as conn:
        return {"job": dict(conn.execute("SELECT * FROM pm_jobs WHERE id=?", (job["id"],)).fetchone()),
                "seller": dict(conn.execute("SELECT * FROM pm_sellers WHERE job_id=?", (job["id"],)).fetchone()),
                "ads": [dict(row) for row in conn.execute("SELECT * FROM pm_ads WHERE job_id=? ORDER BY ad_key", (job["id"],))]}


@pytest.mark.parametrize("name", ["", "123", "id:123", "Ir para a loja oficial", "Todos os anúncios", "Vendedor não identificado", "Nome pendente — vendedor 123"])
def test_navigation_labels_and_identifiers_remain_pending(store, name):
    job = store.start("cliente-a", "alice")
    publish(store, job, seller(name))
    item = store.sellers(job["id"], "cliente-a", "alice")["items"][0]
    assert item["nome"] == "Nome pendente — vendedor 123" and item["name_pending"] is True
    assert store.ranking(job["id"], "cliente-a", "alice")["items"][0]["vendedor"] == item["nome"]


def test_avant_name_requires_consistent_seller_identity_and_preserves_brand(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, seller("Ir para a loja oficial"), [ad(vendedor="Conta Avant")])
    stored = store.sellers(job["id"], "cliente-a", "alice")["items"][0]
    assert (stored["nome"], stored["seller_name_kind"], stored["seller_name_source"]) == ("Conta Avant", "seller", "avant")
    publish(store, job, seller("Marca Oficial", seller_name_kind="store_brand"), [ad(vendedor="Conta Avant")])
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"] == "Marca Oficial"
    publish(store, job, seller("Conta Avant"), [ad(vendedor="Conta Avant", media_vendas_mensal=11)])
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"] == "Marca Oficial"


@pytest.mark.parametrize("extras", [
    {"seller_id": "999"}, {"avant_seller_id": "999"},
    {"seller_perfil_url": "https://lista.mercadolivre.com.br/_CustId_999"},
    {"vendasFonte": "pagina_anuncio"}, {"seller_confirmed": False},
])
def test_unconfirmed_or_non_avant_ad_cannot_supply_store_name(store, extras):
    job = store.start("cliente-a", "alice")
    publish(store, job, ads=[ad(vendedor="Outra loja", **extras)])
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"].startswith("Nome pendente")


def test_conflicting_avant_names_do_not_pick_arbitrary_ad(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, ads=[ad(vendedor="Loja A"), ad(2, vendedor="Loja B")])
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"].startswith("Nome pendente")


@pytest.mark.parametrize("status", ["paused", "completed", "completed_partial", "failed", "cancelled"])
def test_recovery_preserves_metrics_dates_order_digest_and_is_idempotent(store, status):
    job = store.start("cliente-a", "alice")
    publish(store, job, ads=[ad(), ad(2, media_vendas_mensal=None)])
    store.checkpoint(job["id"], "cliente-a", "alice", status=status)
    before = snapshot(store, job)
    owner_revision = store.history("cliente-a", "alice")["owner_revision"]
    result = store.update_names(job["id"], "cliente-a", "alice", [update()])
    assert result["updated"] == 1 and result["pending"] == 0
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["name_pending"] is False
    after = snapshot(store, job)
    assert after["job"] == {**before["job"], "revision": before["job"]["revision"] + 1}
    assert {key: value for key, value in after["seller"].items() if key != "payload"} == {key: value for key, value in before["seller"].items() if key != "payload"}
    for old, new in zip(before["ads"], after["ads"]):
        assert {key: value for key, value in new.items() if key not in {"payload", "search_text"}} == {key: value for key, value in old.items() if key not in {"payload", "search_text"}}
        assert json.loads(new["payload"]) == {**json.loads(old["payload"]), "vendedor": "Loja recuperada"}
        assert "loja recuperada" in new["search_text"]
    assert store.history("cliente-a", "alice")["owner_revision"] == owner_revision + 1
    assert store.update_names(job["id"], "cliente-a", "alice", [update()])["updated"] == 0
    assert snapshot(store, job) == after
    assert store.ranking(job["id"], "cliente-a", "alice", q="RECUPERADA")["total"] == 2


def test_replaying_stale_publication_cannot_erase_recovered_name(store):
    job = store.start("cliente-a", "alice")
    publish(store, job)
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    recovered = store.update_names(job["id"], "cliente-a", "alice", [update()])
    before = snapshot(store, job)
    assert publish(store, job)["revision"] == recovered["job"]["revision"]
    assert snapshot(store, job) == before
    publish(store, job, seller("Nome antigo"), [ad(media_vendas_mensal=12)])
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"] == "Loja recuperada"
    assert store.ranking(job["id"], "cliente-a", "alice")["items"][0]["media_vendas_mensal"] == 12


@pytest.mark.parametrize("extras", [
    {"seller_id": "999"}, {"seller_perfil_url": "https://lista.mercadolivre.com.br/_CustId_999"},
    {"seller_id": "", "seller_perfil_url": ""}, {"seller_perfil_url": "https://example.org/perfil/loja"},
    {"seller_perfil_url": "https://lista.mercadolivre.com.br/_CustId_123?seller_id=999"},
    {"nome": "Ir para a loja oficial"}, {"source": "pagina"}, {"seller_name_kind": "unknown"}, {"metrics": 123},
])
def test_invalid_identity_names_and_extra_fields_roll_back_entire_batch(store, extras):
    job = store.start("cliente-a", "alice")
    publish(store, job)
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    before = snapshot(store, job)
    with pytest.raises(HTTPException) as error:
        store.update_names(job["id"], "cliente-a", "alice", [update(**extras)])
    assert error.value.status_code == 422
    assert snapshot(store, job) == before


def test_late_bad_update_rolls_back_prior_seller_in_same_batch(store):
    job = store.start("cliente-a", "alice")
    publish(store, job)
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    before = snapshot(store, job)
    with pytest.raises(HTTPException) as error:
        store.update_names(job["id"], "cliente-a", "alice", [update(), update(seller_key="id:missing")])
    assert error.value.status_code == 404 and snapshot(store, job) == before


def test_recovery_owner_job_scope_and_running_guard(store):
    job = store.start("cliente-a", "alice")
    publish(store, job)
    for status in ("running", "waiting_login"):
        store.checkpoint(job["id"], "cliente-a", "alice", status=status)
        with pytest.raises(HTTPException) as error:
            store.update_names(job["id"], "cliente-a", "alice", [update()])
        assert error.value.status_code == 409
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    for tenant, user, job_id in (("cliente-b", "alice", job["id"]), ("cliente-a", "bob", job["id"]), ("cliente-a", "alice", "missing")):
        with pytest.raises(HTTPException) as error:
            store.update_names(job_id, tenant, user, [update()])
        assert error.value.status_code == 404


def test_brand_recovery_does_not_overwrite_brand_with_avant_nickname(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, seller("Marca Oficial", seller_name_kind="store_brand"))
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    assert store.update_names(job["id"], "cliente-a", "alice", [update("Conta Avant", source="avant")])["updated"] == 0
    assert store.sellers(job["id"], "cliente-a", "alice")["items"][0]["nome"] == "Marca Oficial"


def test_consolidated_names_follow_latest_valid_identity_not_lexicographic_max(store):
    first = store.start("cliente-a", "alice")
    publish(store, first, seller("Z Loja antiga"))
    store.checkpoint(first["id"], "cliente-a", "alice", status="completed")
    second = store.start("cliente-a", "alice")
    publish(store, second, seller("A Loja atual"), [ad(2)])
    names = store.consolidated_sellers("cliente-a", "alice")["items"]
    assert names == [{"seller_key": "id:123", "vendedor": "A Loja atual", "ads": 2, "pending": 0}]
    store.checkpoint(second["id"], "cliente-a", "alice", status="completed")
    third = store.start("cliente-a", "alice")
    publish(store, third, ads=[ad(3)])
    assert {item["vendedor"] for item in store.consolidated_ranking("cliente-a", "alice")["items"]} == {"A Loja atual"}
    store.update_names(first["id"], "cliente-a", "alice", [update("Nome recuperado recente")])
    assert store.consolidated_sellers("cliente-a", "alice")["items"][0]["vendedor"] == "Nome recuperado recente"
    assert store.consolidated_ranking("cliente-a", "alice", q="RECUPERADO")["total"] == 3
    assert store.ranking(third["id"], "cliente-a", "alice")["items"][0]["vendedor"].startswith("Nome pendente")


def test_concurrent_paused_publication_and_name_recovery_are_atomic(store):
    job = store.start("cliente-a", "alice")
    publish(store, job)
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    barrier = threading.Barrier(2)

    def collect():
        barrier.wait(timeout=5)
        publish(store, job, ads=[ad(), ad(2)])

    def recover():
        barrier.wait(timeout=5)
        return store.update_names(job["id"], "cliente-a", "alice", [update()])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(collect), pool.submit(recover)]
        for future in futures:
            future.result(timeout=10)
    ranking = store.ranking(job["id"], "cliente-a", "alice")
    assert ranking["total"] == 2 and {row["vendedor"] for row in ranking["items"]} == {"Loja recuperada"}
    assert store.get(job["id"], "cliente-a", "alice")["counters"]["ads"] == 2


def test_http_names_request_is_closed_bounded_and_binds_authenticated_username(store, monkeypatch):
    monkeypatch.setattr(pesquisa_mercado, "_STORE", store)
    app = FastAPI()

    async def tenant(request: Request):
        request.state.username = "alice"
        return "cliente-a"

    app.dependency_overrides[favoritos_endpoints.get_tenant_id] = tenant
    app.include_router(create_pesquisa_mercado_router())
    job = store.start("cliente-a", "alice")
    publish(store, job)
    store.checkpoint(job["id"], "cliente-a", "alice", status="paused")
    base = f"/api/pesquisa-mercado/jobs/{job['id']}/nomes"
    with TestClient(app) as client:
        response = client.put(base, headers={"X-Username": "bob"}, json={"updates": [update()]})
        assert response.status_code == 200, response.text
        assert response.json()["updated"] == 1
        invalid = [{"updates": []}, {"updates": [update()] * 101}, {"updates": [update(extra="bad")]},
                   {"updates": [update(seller_id=123)]}, {"updates": [update()], "status": "running"},
                   {"updates": [update(source="page")]}, {"updates": [update(seller_name_kind="other")]}]
        for body in invalid:
            assert client.put(base, json=body).status_code == 422


def test_pending_ad_label_uses_researched_seller_even_when_ad_seller_diverges(store):
    job = store.start("cliente-a", "alice")
    publish(store, job, ads=[ad(seller_id="999")])
    item = store.ranking(job["id"], "cliente-a", "alice")["items"][0]
    assert item["vendedor"] == "Nome pendente — vendedor 123"
    assert item["rankable"] is False
