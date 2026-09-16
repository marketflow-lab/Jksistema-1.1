from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.modules.perguntas_pos_venda.endpoints import store_config
from backend.services import central_accounts_client, integracoes
from backend.services import store_listing_service as listing
from backend.services import store_public_snapshot as projection
from backend.services import store_snapshot_transactions as transactions
from backend.services.path_coordination import path_lock_for


def _store(suffix):
    return {"nome": "Loja " + suffix.upper(), "store_id": "store-" + suffix,
            "integracoes": {"mercadolivre": {"user_id": "seller-" + suffix,
                "site_id": "MLB", "access_token": "fixture-access", "refresh_token": "fixture-refresh",
                "app_id": "fixture-app", "client_secret": "fixture-secret"}}}


@pytest.fixture
def tenants(tmp_path, monkeypatch):
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: None)
    monkeypatch.setattr(listing, "_ensure_initialization", lambda *_args: True)
    for suffix in ("a", "b"):
        tenant = tmp_path / ("tenant-" + suffix)
        tenant.mkdir()
        rows = [_store(suffix)]
        (tenant / "lojas_config.json").write_text(json.dumps(rows), encoding="utf-8")
        projection.write_snapshot(tenant, projection.build_snapshot(rows))
    return tmp_path


def test_store_cards_do_not_wait_for_catalog_held_by_another_thread_and_real_writer(tenants, monkeypatch):
    entered, finished = threading.Event(), threading.Event()
    failures = []
    original = integracoes._integracoes_bloquear_catalogo_e_transicao_fotos
    @contextmanager
    def announced(client, tenant, **kwargs):
        entered.set()  # Caller already holds the tenant's store transaction mutex.
        with original(client, tenant, **kwargs):
            yield
    monkeypatch.setattr(integracoes, "_integracoes_bloquear_catalogo_e_transicao_fotos", announced)
    updated = _store("a")
    updated["nome"] = "Loja atualizada"
    def writer():
        try:
            integracoes.salvar_lojas("tenant-a", [updated])
        except Exception as exc:
            failures.append(exc)
        finally:
            finished.set()
    with path_lock_for(tenants / "tenant-a" / "cadastro_produtos_lojas.csv"):
        worker = threading.Thread(target=writer, daemon=True)
        worker.start()
        assert entered.wait(2)
        for suffix in ("a", "b"):
            start = time.perf_counter()
            cards = listing.read_store_cards("tenant-" + suffix)
            assert time.perf_counter() - start < 0.5
            assert cards["lojas"][0]["store_id"] == "store-" + suffix
            start = time.perf_counter()
            canonical = integracoes.carregar_lojas_snapshot("tenant-" + suffix)
            assert time.perf_counter() - start < 0.5
            assert canonical[0]["integracoes"]["mercadolivre"]["access_token"] == "fixture-access"
        assert not finished.is_set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert not failures
    assert listing.read_store_cards("tenant-a")["lojas"][0]["nome"] == "Loja atualizada"


def test_credential_snapshot_rejects_unpublished_identity_and_accepts_same_identity_refresh(tenants):
    tenant = tenants / "tenant-a"
    current = _store("a")
    current["integracoes"]["mercadolivre"]["access_token"] = "refreshed-access"
    (tenant / "lojas_config.json").write_text(json.dumps([current]), encoding="utf-8")

    refreshed = integracoes.carregar_lojas_snapshot("tenant-a")
    assert refreshed[0]["integracoes"]["mercadolivre"]["access_token"] == "refreshed-access"

    changed = copy.deepcopy(current)
    changed["integracoes"]["mercadolivre"]["user_id"] = "different-seller"
    (tenant / "lojas_config.json").write_text(json.dumps([changed]), encoding="utf-8")
    assert integracoes.carregar_lojas_snapshot("tenant-a") == []

    changed_site = copy.deepcopy(current)
    changed_site["integracoes"]["mercadolivre"]["site_id"] = "MLA"
    (tenant / "lojas_config.json").write_text(json.dumps([changed_site]), encoding="utf-8")
    assert integracoes.carregar_lojas_snapshot("tenant-a") == []

    (tenant / "lojas_config.json").write_text(json.dumps([changed]), encoding="utf-8")
    projection.write_snapshot(tenant, projection.build_snapshot([changed]))
    assert integracoes.carregar_lojas_snapshot("tenant-a")[0]["integracoes"]["mercadolivre"]["user_id"] == "different-seller"


def _http_client(monkeypatch, configs=None):
    monkeypatch.setattr(store_config, "_perguntas_loja_configs_carregar", lambda _client: configs or {})
    monkeypatch.setattr(store_config, "_integracoes_nome_normalizado", lambda name: str(name).casefold().strip())
    monkeypatch.setattr(store_config, "_perguntas_loja_config_obter", lambda data, name: data.get(name, {}))
    monkeypatch.setattr(store_config, "_perguntas_loja_config_normalizar", lambda value: value or {})
    app = FastAPI()
    app.get("/api/mercadolivre/perguntas/lojas")(store_config.ml_perguntas_listar_lojas)
    app.dependency_overrides[store_config.get_tenant_id] = lambda: "tenant-a"
    return TestClient(app)


def test_get_preserves_public_contract_and_reports_pending_generation(tenants, monkeypatch):
    tenant = tenants / "tenant-a"
    journal = tenant / "_stores_publication"
    journal.mkdir()
    (journal / "transaction.json").write_text("{}", encoding="utf-8")
    with _http_client(monkeypatch, {"Loja A": {"intervalo_minutos": 5}}) as client:
        response = client.get("/api/mercadolivre/perguntas/lojas")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["snapshot"]["status"] == "updating"
    assert body["lojas"][0]["config_perguntas"] == {"intervalo_minutos": 5}
    assert "fixture-access" not in response.text


@pytest.mark.parametrize("initializing,code", [(True, "stores_snapshot_initializing"),
                                               (False, "stores_snapshot_unavailable")])
def test_get_returns_503_without_valid_snapshot_not_an_empty_store_list(tenants, monkeypatch, initializing, code):
    projection.snapshot_path(tenants / "tenant-a").unlink()
    monkeypatch.setattr(listing, "_ensure_initialization", lambda *_args: initializing)
    with _http_client(monkeypatch) as client:
        response = client.get("/api/mercadolivre/perguntas/lojas")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    assert response.json()["detail"]["code"] == code
    assert "lojas" not in response.json()


def test_central_uses_current_session_stores_without_reading_tenant_projection(tenants, monkeypatch):
    selected = [_store("b")]
    selected[0]["integracoes"]["mercadolivre"] = {
        "central": True, "connected": True, "user_id": "seller-b", "site_id": "MLB"}
    session = SimpleNamespace(expires_at=time.time() + 60, public_stores=lambda: copy.deepcopy(selected))
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: session)
    monkeypatch.setattr(listing, "_tenant", lambda *_args: pytest.fail("Central must not read a tenant-wide projection"))
    first = listing.read_store_cards("tenant-a")
    assert [row["store_id"] for row in first["lojas"]] == ["store-b"]
    selected.clear()
    assert listing.read_store_cards("tenant-a")["lojas"] == []
    session.expires_at = time.time() - 1
    with pytest.raises(Exception) as caught:
        listing.read_store_cards("tenant-a")
    assert caught.value.status_code == 401

def test_central_get_does_not_append_configuration_from_unauthorized_stores(tenants, monkeypatch):
    public = _store("b")
    public["integracoes"]["mercadolivre"] = {
        "central": True, "connected": True, "user_id": "seller-b", "site_id": "MLB"}
    session = SimpleNamespace(expires_at=time.time() + 60, public_stores=lambda: [public])
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: session)
    with _http_client(monkeypatch, {"Loja A": {"intervalo_minutos": 99}}) as client:
        response = client.get("/api/mercadolivre/perguntas/lojas")
    assert response.status_code == 200
    assert [row["store_id"] for row in response.json()["lojas"]] == ["store-b"]
    assert "Loja A" not in response.text


@pytest.mark.parametrize("existing_stores", [True, False])
def test_cold_get_schedules_one_initializer_then_serves_valid_generation(tmp_path, monkeypatch, existing_stores):
    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    if existing_stores:
        (tenant / "lojas_config.json").write_text(json.dumps([_store("a")]), encoding="utf-8")
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "ARQUIVO_LOJAS", "")
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: None)
    monkeypatch.setattr(listing, "_JOBS", set())
    monkeypatch.setattr(listing, "_FAILURES", {})
    queued = []
    monkeypatch.setattr(listing, "_EXECUTOR", SimpleNamespace(submit=lambda fn, *args: queued.append((fn, args))))
    with _http_client(monkeypatch) as client:
        for _ in range(2):
            response = client.get("/api/mercadolivre/perguntas/lojas")
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "stores_snapshot_initializing"
        assert len(queued) == 1
        assert not projection.snapshot_path(tenant).exists()
        worker, args = queued.pop()
        worker(*args)
        response = client.get("/api/mercadolivre/perguntas/lojas")
        assert response.status_code == 200, response.text
        assert response.json()["snapshot"]["status"] == "ready"
        assert len(response.json()["lojas"]) == (1 if existing_stores else 0)
        assert not queued
