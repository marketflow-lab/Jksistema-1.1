"""Import analyses retain every verified own ad and its exact SKU variation."""

import copy
import json

import pytest
from fastapi import HTTPException

from backend.services import importacoes_sku_anuncios as ads
from backend.services import medias_compras_listas as service
from test_importacoes_sku_simulacao import _request, _setup


def _ad(number, *, price=100, variation=None):
    return {"item_id": f"MLB{number}", "url": f"https://produto.mercadolivre.com.br/MLB-{number}-produto-_JM",
            "preco": price, "status": "active", "seller_id": "111", "site_id": "MLB", "sku": "001",
            "variation_id": variation, "variation_ids": [variation] if variation else []}


def _verified(monkeypatch, tmp_path, rows):
    def lookup(client, order, sku, *, cache_scope):
        assert (client, order["store_id"], sku) == ("tenant-a", "store-carlos", "001")
        assert cache_scope
        return {"store_id": "store-carlos", "loja": "Carlos", "seller_id": "111", "site_id": "MLB",
                "consultado_em_ms": 1234, "anuncios": copy.deepcopy(rows), "anuncios_versao": 2,
                "total_encontrado": len(rows), "limite": None, "truncado": False}

    monkeypatch.setattr(service, "get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(ads, "buscar_anuncios_sku_da_lista", lookup)


def test_source_payload_keeps_more_than_five_ads_and_variation_metadata():
    rows = [_ad(number, variation=str(1000 + number)) for number in range(1, 9)]
    rows[-1]["preco"] = None
    result = service._aplicar_anuncios_proprios({}, {
        "store_id": "store-carlos", "loja": "Carlos", "seller_id": "111", "site_id": "MLB",
        "consultado_em_ms": 1234, "anuncios": rows, "limite": None, "truncado": False,
        "anuncios_versao": 2,
    }, None)
    assert len(result["anuncios_loja"]) == 8
    assert result["anuncios_loja"][-1] == {**rows[-1], "chave": "concorrente_8"}
    assert result["concorrentes_mlb"]["concorrente_8"] == rows[-1]["url"]
    assert result["concorrentes_preco"]["concorrente_8"] == ""
    assert result["anuncios_loja_variacoes"]["concorrente_8"] == "1008"
    assert result["anuncios_versao"] == 2 and result["anuncios_loja_limite"] is None


def test_analysis_persists_all_verified_ads_including_unconfirmed_price(monkeypatch, tmp_path):
    state, harness = _setup(monkeypatch)
    rows = [_ad(number) for number in range(1, 9)]
    rows[-1]["preco"] = None
    rows[-1]["motivo_preco_indisponivel"] = "Preço da variação não confirmado."
    _verified(monkeypatch, tmp_path, rows)
    keys = [f"concorrente_{number}" for number in range(1, 9)]
    result = _request(monkeypatch, state,
                      anuncios_loja=dict(zip(keys, (row["item_id"] for row in rows))),
                      variacoes_anuncios_loja=dict.fromkeys(keys),
                      precos_concorrentes={}, precos_anuncios_loja=dict.fromkeys(keys, 999))
    analysis = result["analise_concorrentes"]
    assert len(analysis["anuncios_loja"]) == 8
    assert analysis["anuncios_versao"] == 2
    assert analysis["anuncios_loja"][-1]["preco"] is None
    assert len(analysis["margens_anuncios_loja"]) == 7
    assert all(row["preco_venda"] == 100 for row in analysis["margens_anuncios_loja"].values())
    assert all(row["variation_id"] is None for row in analysis["margens_anuncios_loja"].values())
    assert state["tenant-a"][0]["itens"][0]["analise_simulacao_importacao_sku"] == analysis
    assert not any(call[0] == "item" and "MLB8" in call[3] for call in harness.calls)


@pytest.mark.parametrize("bad_id", ["100", "999", None])
def test_analysis_rejects_changed_or_missing_variation_before_financial_calls(monkeypatch, tmp_path, bad_id):
    state, harness = _setup(monkeypatch)
    _verified(monkeypatch, tmp_path, [_ad(111, variation="200")])
    before = copy.deepcopy(state)
    with pytest.raises(HTTPException) as error:
        _request(monkeypatch, state, variacoes_anuncios_loja={"concorrente_1": bad_id})
    assert error.value.status_code == 409
    assert state == before and harness.calls == []


def test_margin_uses_only_selected_sku_variation_and_its_attributes(monkeypatch, tmp_path):
    from backend.services.mercadolivre_legacy_pricing import _ml_contexto_frete_item

    state, harness = _setup(monkeypatch)
    _verified(monkeypatch, tmp_path, [_ad(111, variation="200")])
    request = harness.deps["ml_api_request"]

    def with_variations(*args, **kwargs):
        response, cfg = request(*args, **kwargs)
        payload = response.json()
        payload["shipping"]["dimensions"] = "10x10x10,5000"
        payload["attributes"] = [{"id": "SELLER_PACKAGE_WEIGHT", "value_name": "10 kg"}]
        payload["variations"] = [
            {"id": 100, "seller_custom_field": "002", "price": 50,
             "attributes": [{"id": "SELLER_PACKAGE_WEIGHT", "value_name": "5 kg"}]},
            {"id": 200, "seller_custom_field": "001", "price": 100,
             "attributes": [{"id": "SELLER_PACKAGE_WEIGHT", "value_name": "2 kg"},
                            {"id": "SELLER_PACKAGE_LENGTH", "value_name": "20 cm"},
                            {"id": "SELLER_PACKAGE_WIDTH", "value_name": "15 cm"},
                            {"id": "SELLER_PACKAGE_HEIGHT", "value_name": "5 cm"}]},
        ]
        response._content = json.dumps(payload).encode()
        return response, cfg

    captured = []
    fee = harness.deps["ml_obter_taxas_anuncio"]

    def selected_fee(client, loja, cfg, item):
        captured.append(copy.deepcopy(item))
        return fee(client, loja, cfg, item)

    shipping = harness.deps["ml_obter_frete_detalhado"]
    shipping_contexts = []

    def selected_shipping(*args, **kwargs):
        shipping_contexts.append(copy.deepcopy(kwargs["contexto_frete"]))
        return shipping(*args, **kwargs)

    harness.deps.update(ml_api_request=with_variations, ml_obter_taxas_anuncio=selected_fee,
                        ml_contexto_frete_item=_ml_contexto_frete_item,
                        ml_obter_frete_detalhado=selected_shipping)
    result = _request(monkeypatch, state, variacoes_anuncios_loja={"concorrente_1": "200"})["analise_concorrentes"]
    assert result["margens_anuncios_loja"]["concorrente_1"]["variation_id"] == "200"
    assert result["margens"]["concorrente_1"]["variation_id"] == "200"
    assert len(captured) == 1, "Same variation and price may share fee/freight between both series."
    assert captured[0]["variation_id"] == "200"
    assert [variant["id"] for variant in captured[0]["variations"]] == [200]
    assert {attr["id"]: attr["value_name"] for attr in captured[0]["attributes"]}["SELLER_PACKAGE_WEIGHT"] == "2 kg"
    assert shipping_contexts[0]["dimensions"] == "20x15x5,2000"


def test_current_item_cannot_silently_switch_the_verified_variation_sku(monkeypatch, tmp_path):
    state, harness = _setup(monkeypatch)
    _verified(monkeypatch, tmp_path, [_ad(111, variation="200")])
    request = harness.deps["ml_api_request"]

    def changed_variation(*args, **kwargs):
        response, cfg = request(*args, **kwargs)
        payload = response.json()
        payload["variations"] = [{"id": 100, "seller_custom_field": "001", "price": 100},
                                 {"id": 200, "seller_custom_field": "002", "price": 100}]
        response._content = json.dumps(payload).encode()
        return response, cfg

    harness.deps["ml_api_request"] = changed_variation
    result = _request(monkeypatch, state, variacoes_anuncios_loja={"concorrente_1": "200"})["analise_concorrentes"]
    for matrix in ("margens", "margens_anuncios_loja"):
        assert result[matrix]["concorrente_1"]["financeiro_exato"] is False
        assert "variação" in result[matrix]["concorrente_1"]["motivo_indisponivel"].lower()
    assert not any(call[0] in {"costs", "fee", "shipping"} for call in harness.calls)


def test_new_catalog_analysis_keeps_the_list_store_when_an_ad_changes_owner(monkeypatch, tmp_path):
    state, harness = _setup(monkeypatch)
    _verified(monkeypatch, tmp_path, [_ad(111)])
    harness.payload = {"seller": "222"}
    result = _request(monkeypatch, state, fonte_custo="cadastro_sku_loja",
                      variacoes_anuncios_loja={"concorrente_1": None})["analise_concorrentes"]
    assert result["store_id"] == "store-carlos"
    assert result["margens_anuncios_loja"]["concorrente_1"]["financeiro_exato"] is False
    assert not any(call[0] == "item" and call[1] == "JK" for call in harness.calls)


@pytest.mark.parametrize("new_price", [100, 110, None])
def test_omitted_own_prices_reuse_only_the_same_verified_variant_price(monkeypatch, tmp_path, new_price):
    state, _ = _setup(monkeypatch)
    rows = [_ad(111)]
    _verified(monkeypatch, tmp_path, rows)
    first = _request(monkeypatch, state, variacoes_anuncios_loja={"concorrente_1": None})["analise_concorrentes"]
    rows[0]["preco"] = new_price
    refreshed = _request(monkeypatch, state, variacoes_anuncios_loja={"concorrente_1": None},
                         precos_anuncios_loja=None)["analise_concorrentes"]
    expected = first["margens_anuncios_loja"] if new_price == 100 else {}
    assert refreshed["margens_anuncios_loja"] == expected
    assert refreshed["anuncios_loja"][0]["preco"] == new_price


def test_financial_cache_isolated_by_variation_even_with_same_item_and_price(monkeypatch):
    _, harness = _setup(monkeypatch)
    request = harness.deps["ml_api_request"]

    def with_variations(*args, **kwargs):
        response, cfg = request(*args, **kwargs)
        payload = response.json()
        payload["variations"] = [{"id": 200, "seller_custom_field": "001", "price": 100},
                                 {"id": 300, "seller_custom_field": "001", "price": 100}]
        response._content = json.dumps(payload).encode()
        return response, cfg

    harness.deps["ml_api_request"] = with_variations
    context = {}
    for variation in ("200", "300"):
        result = service._calcular_margens_concorrentes_promocoes(
            "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
            contexto_consulta=context, custo_unitario_simulado=14.2, store_id_alvo="store-carlos",
            variacoes_anuncios_loja={"concorrente_1": variation},
        )
        assert result["concorrente_1"]["variation_id"] == variation
        assert result["concorrente_1"]["financeiro_exato"] is True
    assert sum(call[0] == "fee" for call in harness.calls) == 2
    assert sum(call[0] == "shipping" for call in harness.calls) == 2
