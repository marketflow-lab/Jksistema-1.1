"""Transient store publication never means disconnected, absent, or another store."""
import pytest
from fastapi import HTTPException

from backend.services import cadastro_importacao_catalogos, cadastro_sync_ncm, estoque_sync
from backend.services.marketplace_tools import integrations, runtime as marketplace_runtime
from backend.services.sales_tools import repository as sales_repository


def _updating(provider="bling"):
    return {"store_id": "store-a", "nome": "Store A", "integracoes": {},
            "_unavailable_providers": [provider]}


def _assert_unavailable(call):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "stores_snapshot_credentials_unavailable"
    assert caught.value.headers["Retry-After"] == "2"


@pytest.mark.parametrize("provider", ["bling", "mercadolivre"])
def test_marketplace_explicit_updating_store_never_falls_back_to_other_stores(monkeypatch, provider):
    monkeypatch.setattr(marketplace_runtime, "find_store", lambda *_a: _updating(provider))
    monkeypatch.setattr(marketplace_runtime, "ml_connected_stores", lambda *_a: pytest.fail("other store fallback"))
    monkeypatch.setattr(marketplace_runtime, "load_stores", lambda *_a: pytest.fail("other store fallback"))
    _assert_unavailable(lambda: integrations.connected_stores("tenant", provider, "Store A"))


def test_marketplace_explicit_missing_store_never_falls_back(monkeypatch):
    monkeypatch.setattr(marketplace_runtime, "find_store", lambda *_a: None)
    monkeypatch.setattr(marketplace_runtime, "ml_connected_stores", lambda *_a: ["Store B"])
    with pytest.raises(HTTPException) as caught:
        integrations.connected_stores("tenant", "mercadolivre", "Store A")
    assert caught.value.status_code == 404


def test_marketplace_exact_ready_store_is_independent_of_other_store(monkeypatch):
    ready = {"store_id": "store-b", "nome": "Store B", "integracoes": {
        "bling": {"access_token": "synthetic-token"}}}
    monkeypatch.setattr(marketplace_runtime, "find_store", lambda *_a: ready)
    monkeypatch.setattr(marketplace_runtime, "load_stores", lambda *_a: [_updating(), ready])
    assert integrations.connected_stores("tenant", "bling", "Store B") == ["Store B"]


def test_marketplace_does_not_report_temporary_provider_as_disconnected(monkeypatch):
    monkeypatch.setattr(marketplace_runtime, "load_stores", lambda *_a: [_updating()])
    monkeypatch.setattr(marketplace_runtime, "find_store", lambda *_a: _updating())
    _assert_unavailable(lambda: integrations.bling_connected_stores("tenant"))
    _assert_unavailable(lambda: integrations.get_bling_config("tenant", "Store A"))
    _assert_unavailable(lambda: integrations.get_status("tenant"))


def test_ml_store_enumeration_does_not_silently_drop_updating_store(monkeypatch):
    monkeypatch.setattr(sales_repository, "carregar_lojas", lambda *_a: [_updating("mercadolivre")])
    _assert_unavailable(lambda: sales_repository._ia_lojas_ml_conectadas("tenant"))


@pytest.mark.parametrize("provider,source", [("bling", "bling"), ("mercadolivre", "mercadolivre")])
def test_catalog_import_retains_temporary_error_instead_of_not_configured(monkeypatch, provider, source):
    monkeypatch.setattr(cadastro_importacao_catalogos, "resolver_loja_cadastro",
                        lambda *_a: {"store_id": "store-a", "nome": "Store A"})
    monkeypatch.setattr(cadastro_importacao_catalogos.integracoes, "ler_lojas",
                        lambda *_a: [_updating(provider)])
    _assert_unavailable(lambda: cadastro_importacao_catalogos._configuracao_fonte("tenant", "store-a", source))


def test_stock_snapshot_and_ncm_keep_provider_unavailability(monkeypatch):
    monkeypatch.setattr(estoque_sync, "carregar_lojas", lambda *_a: [_updating()])
    monkeypatch.setattr(cadastro_sync_ncm, "carregar_lojas", lambda *_a: [_updating()])
    _assert_unavailable(lambda: estoque_sync._snapshot_lojas_estoque("tenant"))
    _assert_unavailable(lambda: estoque_sync._resolver_loja_estoque("tenant", store_id="store-a"))
    _assert_unavailable(lambda: cadastro_sync_ncm._resolver_loja_sync_ncm("tenant", "store-a"))
