"""SKU import simulations use their displayed cost and the import list's store."""

import copy
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas.medias_compras import ListaPedidoSkuAnaliseConcorrentesRequest
from backend.services import medias_compras_listas as service
from test_importacoes_ad_owner import _Harness
from test_medias_compras_competitor_margins import _configurar_estado


def _setup(monkeypatch):
    state = _configurar_estado(monkeypatch)
    order = state["tenant-a"][0]
    order.update(loja="Carlos", store_id="store-carlos")
    order["itens"][0]["analise_concorrentes"] = {
        "custo_unitario": 90, "fonte_custo": "cadastro_sku_loja", "margens": {"saved": "catalog-result"},
    }
    harness = _Harness()
    harness.statuses = {"Carlos": 200, "JK": 200}
    harness.payload = {"seller": "111"}
    harness.deps = harness.dependencies()
    original = harness.deps["ml_api_request"]

    def request(*args, **kwargs):
        response, cfg = original(*args, **kwargs)
        payload = response.json()
        payload["seller_custom_field"] = "001"
        response._content = json.dumps(payload).encode()
        return response, cfg

    harness.deps["ml_api_request"] = request
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: harness.deps)
    return state, harness


def _request(monkeypatch, state, *, cost=14.2, **fields):
    payload = {"fonte_custo": "simulacao_importacao_sku", "custo_unitario": cost,
               "precos_concorrentes": {"concorrente_1": 100}, "precos_anuncios_loja": {"concorrente_1": 100},
               "anuncios_loja": {"concorrente_1": "MLB111"}}
    payload.update(fields)
    request = ListaPedidoSkuAnaliseConcorrentesRequest(**payload)
    return service._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync(
        "lista-1", "001", request, client_id="tenant-a",
    )


def test_explicit_sku_simulation_uses_displayed_cost_and_keeps_catalog_analysis(monkeypatch):
    state, harness = _setup(monkeypatch)
    original = copy.deepcopy(state["tenant-a"][0]["itens"][0]["analise_concorrentes"])
    result = _request(monkeypatch, state)
    analysis = result["analise_concorrentes"]
    assert analysis["custo_unitario"] == 14.2
    assert analysis["fonte_custo"] == "simulacao_importacao_sku"
    for matrix in ("margens", "margens_anuncios_loja"):
        assert analysis[matrix]["concorrente_1"]["custo_unitario"] == 14.2
        assert analysis[matrix]["concorrente_1"]["margem_percentual"] == 52.8
        assert analysis[matrix]["concorrente_1"]["store_id"] == "store-carlos"
    saved = state["tenant-a"][0]["itens"][0]
    assert saved["analise_simulacao_importacao_sku"] == analysis
    assert saved["analise_concorrentes"] == original
    assert not any(call[0] == "item" and call[1] == "JK" for call in harness.calls)


def test_schema_defaults_to_catalog_and_rejects_unknown_cost_source():
    assert ListaPedidoSkuAnaliseConcorrentesRequest(custo_unitario=14.2).fonte_custo == "cadastro_sku_loja"
    with pytest.raises(ValidationError):
        ListaPedidoSkuAnaliseConcorrentesRequest(fonte_custo="unknown-source", custo_unitario=14.2)


def test_legacy_request_keeps_catalog_cost_and_preserves_saved_simulation(monkeypatch):
    state, harness = _setup(monkeypatch)
    simulation = {"custo_unitario": 14.2, "fonte_custo": "simulacao_importacao_sku", "margens": {"saved": "scenario"}}
    state["tenant-a"][0]["itens"][0]["analise_simulacao_importacao_sku"] = copy.deepcopy(simulation)
    result = _request(monkeypatch, state, fonte_custo="cadastro_sku_loja")
    analysis = result["analise_concorrentes"]
    assert analysis["fonte_custo"] == "cadastro_sku_loja"
    assert analysis["custo_unitario"] == 90
    assert analysis["margens"]["concorrente_1"]["margem_percentual"] == -23
    saved = state["tenant-a"][0]["itens"][0]
    assert saved["analise_concorrentes"] == analysis
    assert saved["analise_simulacao_importacao_sku"] == simulation


def test_explicit_zero_cost_is_not_replaced_by_catalog_cost(monkeypatch):
    state, _ = _setup(monkeypatch)
    result = _request(monkeypatch, state, cost=0)["analise_concorrentes"]
    assert result["custo_unitario"] == 0
    assert result["margens"]["concorrente_1"]["custo_unitario"] == 0
    assert result["margens"]["concorrente_1"]["margem_percentual"] == 67


@pytest.mark.parametrize("cost", [None, -1, float("nan"), float("inf"), -float("inf")])
def test_pending_or_invalid_simulation_cost_does_not_fallback_or_persist(monkeypatch, cost):
    state, harness = _setup(monkeypatch)
    original = copy.deepcopy(state)
    with pytest.raises(HTTPException) as error:
        _request(monkeypatch, state, cost=cost)
    assert error.value.status_code == 400
    assert harness.calls == []
    assert state == original


def test_simulation_needs_owner_tax_but_does_not_require_stored_catalog_cost(monkeypatch):
    state, harness = _setup(monkeypatch)
    harness.deps["carregar_custos_impostos"] = lambda *args: ({}, {"001": .1})
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["margens"]["concorrente_1"]["financeiro_exato"] is True
    assert result["margens"]["concorrente_1"]["custo_unitario"] == 14.2
    assert result["margens"]["concorrente_1"]["margem_percentual"] == 52.8


def test_missing_owner_tax_blocks_simulation_instead_of_estimating(monkeypatch):
    state, harness = _setup(monkeypatch)
    harness.deps["carregar_custos_impostos"] = lambda *args: ({"001": 90}, {})
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["custo_unitario"] == 14.2
    assert result["margens"]["concorrente_1"]["financeiro_exato"] is False
    assert "Imposto" in result["margens"]["concorrente_1"]["motivo_indisponivel"]
    assert not any(call[0] == "fee" for call in harness.calls)


def test_simulation_does_not_use_another_authorized_store_ad(monkeypatch):
    state, harness = _setup(monkeypatch)
    harness.payload = {"seller": "222"}
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["margens"]["concorrente_1"]["financeiro_exato"] is False
    assert not any(call[0] in {"cfg", "item"} and call[2 if call[0] == "cfg" else 1] == "JK" for call in harness.calls)
    assert not any(call[0] == "fee" for call in harness.calls)


def test_authoritative_list_store_id_wins_over_display_name_or_request_extra(monkeypatch):
    state, harness = _setup(monkeypatch)
    state["tenant-a"][0]["loja"] = "JK"
    result = _request(monkeypatch, state, store_id="store-jk")["analise_concorrentes"]
    assert result["store_id"] == "store-carlos"
    assert result["margens"]["concorrente_1"]["loja"] == "Carlos"
    assert result["margens"]["concorrente_1"]["store_id"] == "store-carlos"
    assert not any(call[0] == "cfg" and call[2] == "JK" for call in harness.calls)


def test_revoked_list_store_does_not_fallback_to_other_account(monkeypatch):
    state, harness = _setup(monkeypatch)
    harness.stores = [harness.stores[1]]
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["margens"]["concorrente_1"]["financeiro_exato"] is False
    assert not any(call[0] in {"cfg", "item", "costs", "fee"} for call in harness.calls)


@pytest.mark.parametrize("bad_sku", ["002", "001,002", "", None])
def test_same_account_item_must_also_confirm_the_requested_sku(monkeypatch, bad_sku):
    state, harness = _setup(monkeypatch)
    original = harness.deps["ml_api_request"]

    def request(*args, **kwargs):
        response, cfg = original(*args, **kwargs)
        payload = response.json()
        payload["seller_custom_field"] = bad_sku
        response._content = json.dumps(payload).encode()
        return response, cfg

    harness.deps["ml_api_request"] = request
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["margens"]["concorrente_1"]["financeiro_exato"] is False
    assert "SKU" in result["margens"]["concorrente_1"]["motivo_indisponivel"]
    assert not any(call[0] == "fee" for call in harness.calls)


def test_simulation_cache_distinguishes_costs_and_catalog_mode(monkeypatch):
    _, harness = _setup(monkeypatch)
    context = {}
    values = []
    for cost in (14.2, 20, 0):
        result = service._calcular_margens_concorrentes_promocoes(
            "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
            contexto_consulta=context, custo_unitario_simulado=cost, store_id_alvo="store-carlos",
        )
        values.append(result["concorrente_1"]["margem_percentual"])
    catalog = service._calcular_margens_concorrentes_promocoes(
        "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"}, contexto_consulta=context,
    )
    assert values == [52.8, 47, 67]
    assert catalog["concorrente_1"]["margem_percentual"] == -23


@pytest.mark.parametrize("change_cost", [False, True])
def test_omitted_own_prices_reuse_only_the_same_saved_simulation_cost(monkeypatch, change_cost):
    state, _ = _setup(monkeypatch)
    first = _request(monkeypatch, state)["analise_concorrentes"]
    result = _request(monkeypatch, state, cost=20 if change_cost else 14.2,
                      precos_anuncios_loja=None)["analise_concorrentes"]
    assert result["margens_anuncios_loja"] == ({} if change_cost else first["margens_anuncios_loja"])
    assert state["tenant-a"][0]["itens"][0]["analise_concorrentes"]["fonte_custo"] == "cadastro_sku_loja"


def test_normal_item_edit_keeps_both_server_owned_analysis_namespaces():
    current = [{"SKU": "001", "analise_concorrentes": {"cost": 90},
                "analise_simulacao_importacao_sku": {"cost": 14.2}}]
    edited = service._proteger_itens_aprovados_lista(current, [{"SKU": "001", "Quantidade": 20}])
    assert edited[0]["analise_concorrentes"] == current[0]["analise_concorrentes"]
    assert edited[0]["analise_simulacao_importacao_sku"] == current[0]["analise_simulacao_importacao_sku"]
    assert edited[0]["analise_simulacao_importacao_sku"] is not current[0]["analise_simulacao_importacao_sku"]


def test_list_store_change_during_quote_does_not_persist_simulation(monkeypatch):
    state, harness = _setup(monkeypatch)
    original = harness.deps["ml_obter_taxas_anuncio"]

    def fee(*args, **kwargs):
        state["tenant-a"][0]["store_id"] = "store-jk"
        return original(*args, **kwargs)

    harness.deps["ml_obter_taxas_anuncio"] = fee
    with pytest.raises(HTTPException) as error:
        _request(monkeypatch, state)
    assert error.value.status_code == 409
    assert "analise_simulacao_importacao_sku" not in state["tenant-a"][0]["itens"][0]


def test_legacy_list_without_id_resolves_its_unique_authorized_store(monkeypatch):
    state, _ = _setup(monkeypatch)
    state["tenant-a"][0].pop("store_id")
    calls = []

    def scope(client, name, **kwargs):
        calls.append((client, name, kwargs))
        return {"store_id": "store-carlos", "loja": "Carlos", "scope": "store"}

    monkeypatch.setattr(service.medias_common, "_resolver_escopo_loja_medias", scope)
    result = _request(monkeypatch, state)["analise_concorrentes"]
    assert result["store_id"] == "store-carlos"
    assert calls == [("tenant-a", "Carlos", {"exigir_especifica": True})]


def test_ambiguous_legacy_store_is_rejected_before_any_marketplace_call(monkeypatch):
    state, harness = _setup(monkeypatch)
    state["tenant-a"][0].pop("store_id")

    def ambiguous(*args, **kwargs):
        raise HTTPException(409, "Ambiguous store")

    monkeypatch.setattr(service.medias_common, "_resolver_escopo_loja_medias", ambiguous)
    with pytest.raises(HTTPException) as error:
        _request(monkeypatch, state)
    assert error.value.status_code == 409
    assert harness.calls == []


def _market_sheet(monkeypatch):
    row = ["001", "", "MLB777", "999", "https://example.test/market", "100"]
    monkeypatch.setattr(service, "_indice_concorrentes_lista", lambda client, order: ({"001": row}, 1))


def test_list_ad_query_requires_a_list_before_reading_market_sources(monkeypatch):
    monkeypatch.setattr(service, "_indice_concorrentes_lista", lambda *args: pytest.fail("Missing list must fail first"))
    with pytest.raises(HTTPException) as error:
        service._api_medias_compras_concorrentes_links_sync("001", client_id="tenant-a", anuncios_da_lista=True)
    assert error.value.status_code == 400


def test_default_links_query_preserves_sheet_ad_mapping(monkeypatch):
    from backend.services import importacoes_sku_anuncios as ads

    _setup(monkeypatch)
    _market_sheet(monkeypatch)
    monkeypatch.setattr(ads, "buscar_anuncios_sku_da_lista", lambda *args: pytest.fail("Legacy lookup must remain unchanged"))
    result = service._api_medias_compras_concorrentes_links_sync("001", client_id="tenant-a", lista_id="lista-1")
    assert result["concorrentes_mlb"]["concorrente_1"] == "MLB777"
    assert result["concorrentes_preco"]["concorrente_1"] == "999"
    assert "anuncios_loja_fonte" not in result


def test_opt_in_links_replace_only_own_ads_and_keep_competitor_comparisons(monkeypatch):
    from backend.services import importacoes_sku_anuncios as ads

    _setup(monkeypatch)
    _market_sheet(monkeypatch)
    legacy = service._api_medias_compras_concorrentes_links_sync("001", client_id="tenant-a", lista_id="lista-1")
    calls = []

    def lookup(client, order, sku):
        calls.append((client, order["store_id"], sku))
        return {"store_id": "store-carlos", "loja": "Carlos", "anuncios": [
            {"item_id": "MLB111", "preco": 30, "url": "https://produto.mercadolivre.com.br/MLB-111-peca"},
            {"item_id": "MLB222", "preco": 35, "url": "https://produto.mercadolivre.com.br/MLB-222-peca"},
        ], "total_encontrado": 6, "limite": 5, "truncado": True}

    monkeypatch.setattr(ads, "buscar_anuncios_sku_da_lista", lookup)
    result = service._api_medias_compras_concorrentes_links_sync(
        "001", client_id="tenant-a", lista_id="lista-1", anuncios_da_lista=True,
    )
    assert calls == [("tenant-a", "store-carlos", "001")]
    assert result["concorrentes"] == legacy["concorrentes"]
    assert result["concorrentes_valores"] == legacy["concorrentes_valores"]
    assert result["fontes_versao"] == legacy["fontes_versao"]
    assert result["concorrentes_mlb"] == {"concorrente_1": "https://produto.mercadolivre.com.br/MLB-111-peca",
                                           "concorrente_2": "https://produto.mercadolivre.com.br/MLB-222-peca",
                                           "concorrente_3": "", "concorrente_4": "", "concorrente_5": ""}
    assert result["concorrentes_preco"]["concorrente_1"] == 30
    assert result["concorrentes_preco"]["concorrente_3"] == ""
    assert result["anuncios_loja_store_id"] == "store-carlos"
    assert result["anuncios_loja_nome"] == "Carlos"
    assert result["anuncios_loja_fonte"] == "loja_lista_sku"
    assert result["anuncios_loja_total"] == 6 and result["anuncios_loja_truncado"] is True


def test_opt_in_lookup_failure_does_not_fallback_to_sheet_own_ads(monkeypatch):
    from backend.services import importacoes_sku_anuncios as ads

    _setup(monkeypatch)
    _market_sheet(monkeypatch)

    def denied(*args):
        raise HTTPException(401, "Reconnect this store")

    monkeypatch.setattr(ads, "buscar_anuncios_sku_da_lista", denied)
    with pytest.raises(HTTPException) as error:
        service._api_medias_compras_concorrentes_links_sync(
            "001", client_id="tenant-a", lista_id="lista-1", anuncios_da_lista=True,
        )
    assert error.value.status_code == 401
