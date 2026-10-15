"""Promotion list identity and discovery capability regressions."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import mercadolivre_cache as cache
from backend.services import mercadolivre_promocoes as promotions


@pytest.fixture
def runtime(monkeypatch):
    cfg = {"user_id": "101", "site_id": "MLB"}
    calls = []
    entries = [{"id": "P-SYNTHETIC", "type": "SMART", "status": "started", "name": "Oferta"}]
    status = [200]

    def request(client_id, store, current_cfg, method, url, **kwargs):
        calls.append((client_id, store, dict(current_cfg), method, url))
        return SimpleNamespace(status_code=status[0], json=lambda: {"results": entries}), dict(current_cfg)

    monkeypatch.setattr(cache, "_ml_cache", {})
    monkeypatch.setattr(promotions, "_ctx", lambda: SimpleNamespace(
        logger=SimpleNamespace(warning=lambda *args: None),
        obter_cfg_ml=lambda client, store: dict(cfg),
        ml_api_request_com_retry=request,
        normalizar_texto=lambda value: value.lower(),
        ml_parse_error_detail=lambda *args: "Erro de consulta",
    ))
    return cfg, calls, entries, status


def test_list_preserves_campaigns_and_advertises_discovery(runtime):
    cfg, calls, entries, _ = runtime
    result = promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert result["seller_id"] == "101"
    assert result["site_id"] == "MLB"
    assert result["discovery_available"] is True
    assert result["campaigns"][0]["id"] == entries[0]["id"]
    assert result["campaigns"][0]["selection_group"] == "mercado_livre"
    assert result["total"] == 1
    assert calls[0][3] == "GET"
    promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("user_id", "202"), ("site_id", "MLA")])
def test_reconnected_store_never_reuses_previous_identity_cache(runtime, field, value):
    cfg, calls, entries, _ = runtime
    original = promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    cfg[field] = value
    entries[:] = [{"id": "P-OTHER", "type": "PRICE_MATCHING", "status": "started", "name": "Outra oferta"}]
    current = promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert len(calls) == 2
    assert current["campaigns"][0]["id"] == "P-OTHER"
    assert original["campaigns"][0]["id"] == "P-SYNTHETIC"
    assert current["seller_id"] == cfg["user_id"]
    assert current["site_id"] == cfg["site_id"]


def test_previous_seller_stale_cache_is_not_used_on_error(runtime):
    cfg, _, _, status = runtime
    promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    cfg["user_id"] = "202"
    status[0] = 503
    with pytest.raises(HTTPException) as error:
        promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert error.value.status_code == 503


def test_store_invalidation_removes_all_identity_generations(runtime):
    cfg, calls, _, _ = runtime
    promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    cfg["user_id"] = "202"
    promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert cache.invalidar_cache_mercado_livre("tenant-a", "store-a")["entradas_removidas"] == 2
    promotions.listar_promocoes_ativas_mercado_livre("tenant-a", "store-a")
    assert len(calls) == 3


def test_campaign_counts_cache_follows_seller_and_site(runtime, monkeypatch):
    cfg, _, entries, _ = runtime
    counts_calls = []

    def count(client, store, current_cfg, campaign, kind, status):
        counts_calls.append((current_cfg["user_id"], current_cfg["site_id"], status))
        return int(current_cfg["user_id"]), current_cfg

    monkeypatch.setattr(promotions, "_ml_contar_itens_promocao_status", count)
    first = promotions.listar_promocoes_contagens_mercado_livre("tenant-a", "store-a")
    cfg["user_id"] = "202"
    second = promotions.listar_promocoes_contagens_mercado_livre("tenant-a", "store-a")
    cfg["site_id"] = "MLA"
    third = promotions.listar_promocoes_contagens_mercado_livre("tenant-a", "store-a")
    assert first["counts"]["P-SYNTHETIC"]["active_count"] == 101
    assert second["counts"]["P-SYNTHETIC"]["active_count"] == 202
    assert third["counts"]["P-SYNTHETIC"]["active_count"] == 202
    assert counts_calls == [("101", "MLB", "active"), ("101", "MLB", "candidate"),
                            ("202", "MLB", "active"), ("202", "MLB", "candidate"),
                            ("202", "MLA", "active"), ("202", "MLA", "candidate")]
