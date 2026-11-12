"""Network reuse, fail-closed updates and per-SKU commits with synthetic data."""

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
from threading import Barrier, Event

import pytest
import requests
from fastapi import HTTPException

from backend.schemas.medias_compras import ListaPedidoSkuAnaliseConcorrentesRequest
from backend.services import importacoes_base_custos as base
from backend.services import importacoes_financeiro as financeiro
from backend.services import medias_compras_listas as service
from backend.services import mercadolivre_legacy_items as items
from backend.services import mercadolivre_legacy_pricing as pricing
from backend.services import blocking_workers as workers
from test_importacoes_sku_simulacao import _setup
from test_importacoes_anuncios_variacoes_margens import _ad, _verified
from test_importacoes_sku_anuncios import env as anuncios_env


class _Response:
    def __init__(self, data, status=200):
        self.status_code, self.data = status, data

    def json(self):
        return deepcopy(self.data)


def _request(*, sku="001", own=True, cost=14.2, count=1, competitor_price=100, signature="b"):
    keys = [f"concorrente_{number}" for number in range(1, count + 1)]
    return ListaPedidoSkuAnaliseConcorrentesRequest(
        fonte_custo="simulacao_importacao_sku", custo_unitario=cost,
        revisao_fontes_custo="a" * 64, assinatura_calculo=signature * 64,
        precos_concorrentes={key: competitor_price for key in keys[:5]} if competitor_price else {},
        precos_anuncios_loja={key: 100 for key in keys} if own else None,
        anuncios_loja={key: f"MLB{number}" for number, key in enumerate(keys, 1)},
    )


def _call(req, *, sku="001", preview=False):
    return service._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync(
        "lista-1", sku, req, client_id="tenant-a", somente_previa=preview,
    )


@pytest.fixture
def synthetic(monkeypatch, tmp_path):
    state, harness = _setup(monkeypatch)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_, **__: pytest.fail("Real network forbidden"))
    monkeypatch.setattr(service.medias_common, "get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(service, "get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(base, "exigir_revisao_custo", lambda *_, **__: None)
    monkeypatch.setattr(financeiro, "_DOCUMENTOS", financeiro.CacheConsultas(100))
    for name, value in {
        "_cache_get": items._cache_get, "_cache_set": items._cache_set,
        "ML_LISTING_FEE_CACHE": {}, "ML_LISTING_FEE_CACHE_TTL": 900,
        "ML_ITEM_SHIPPING_CACHE": {}, "ML_ITEM_SHIPPING_CACHE_TTL": 900,
        "_ml_nome_tipo_anuncio": lambda _: "Classico", "_to_float_safe": lambda value: float(value) if value is not None else None,
        "logger": logging.getLogger("test.importacoes.financeiro"),
    }.items():
        monkeypatch.setattr(pricing, name, value, raising=False)
    calls = Counter()
    options = {"failure": None, "stage": None, "shipping": 8}
    original = harness.deps["ml_api_request"]

    def response(stage, data):
        calls[stage] += 1
        if options["stage"] == stage:
            if options["failure"] == "timeout":
                raise requests.Timeout("Synthetic provider timeout")
            return _Response({}, options["failure"])
        return _Response(data)

    def item_or_promo(*args, **kwargs):
        if "/seller-promotions/" in args[4]:
            return response("promotion", []), args[2]
        actual, cfg = original(*args, **kwargs)
        data = actual.json()
        data.update(status="active", category_id="MLB123", listing_type_id="gold_special", condition="new",
                    shipping={"mode": "me2", "logistic_type": "cross_docking", "dimensions": "6x15x20,500", "free_shipping": True})
        return response("item", data), cfg

    def remote(client, loja, cfg, method, url, **kwargs):
        if "/listing_prices" in url:
            return response("fee", {"sale_fee_amount": kwargs["params"]["price"] * .15,
                                    "sale_fee_details": {"fixed_fee": 0, "percentage_fee": 15}}), cfg
        assert "/shipping_options" in url
        return response("shipping", {"coverage": {"all_country": {
            "seller_cost": options["shipping"], "list_cost": options["shipping"], "free_shipping_by_meli": True,
        }}}), cfg

    harness.deps.update(
        ml_api_request=item_or_promo, ml_contexto_frete_item=pricing._ml_contexto_frete_item,
        ml_obter_taxas_anuncio=lambda *args: pricing._ml_obter_taxas_anuncio(*args, request_fn=remote),
        ml_obter_frete_detalhado=lambda *args, **kwargs: pricing._ml_obter_frete_detalhado(*args, **kwargs, request_fn=remote),
    )
    return state, harness, calls, options


def test_eight_ads_reopen_cost_change_and_preview_reuse_network_sources(synthetic, monkeypatch, tmp_path):
    state, harness, calls, _ = synthetic
    _verified(monkeypatch, tmp_path, [_ad(number) for number in range(1, 9)])
    req = _request(count=8, competitor_price=110)
    req.variacoes_anuncios_loja = dict.fromkeys(req.anuncios_loja)
    first = _call(req)["analise_concorrentes"]
    assert calls == {"item": 8, "promotion": 8, "fee": 2, "shipping": 13}
    calls.clear()
    repeated = _call(req)["analise_concorrentes"]
    assert repeated["margens"] == first["margens"] and not calls
    changed = req.model_copy(update={"custo_unitario": 20, "assinatura_calculo": "c" * 64})
    updated = _call(changed)["analise_concorrentes"]
    assert updated["margens"]["concorrente_1"]["margem_percentual"] != first["margens"]["concorrente_1"]["margem_percentual"]
    assert not calls
    before = deepcopy(state)
    _call(changed.model_copy(update={"precos_concorrentes": {}}), preview=True)
    assert state == before
    _call(changed)
    assert not calls
    different_price = changed.model_copy(update={"precos_concorrentes": {key: 120 for key in list(req.anuncios_loja)[:5]}})
    _call(different_price)
    assert calls == {"fee": 1, "shipping": 5}


def test_exact_zero_shipping_is_cached(synthetic):
    _, _, calls, options = synthetic
    options["shipping"] = 0
    first = _call(_request())["analise_concorrentes"]
    assert first["margens"]["concorrente_1"]["frete_ml"] == 0
    assert calls["shipping"] == 1
    calls.clear()
    _call(_request(cost=25))
    assert not calls


@pytest.mark.parametrize("stage", ["item", "promotion", "fee", "shipping"])
@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_transient_error_stops_cascade_and_preserves_previous_analysis(synthetic, monkeypatch, stage, failure):
    state, _, calls, options = synthetic
    req = _request()
    _call(req)
    before = deepcopy(state)
    monkeypatch.setattr(financeiro, "_DOCUMENTOS", financeiro.CacheConsultas(100))
    pricing.ML_ITEM_SHIPPING_CACHE.clear()
    pricing.ML_LISTING_FEE_CACHE.clear()
    calls.clear()
    options.update(stage=stage, failure=failure)
    with pytest.raises(HTTPException) as error:
        _call(req)
    assert error.value.status_code == 503
    assert calls[stage] == 1 and state == before
    options.update(stage=None, failure=None)
    calls.clear()
    _call(req)
    assert calls[stage] == 1  # A transient error never becomes a cache entry.


def test_expired_documents_refresh_but_cost_change_does_not(synthetic, monkeypatch):
    from backend.services import importacoes_consultas_cache as cache

    _, _, calls, _ = synthetic
    now = [100.0]
    monkeypatch.setattr(cache, "monotonic", lambda: now[0])
    _call(_request())
    calls.clear()
    now[0] += 59
    _call(_request(cost=30))
    assert not calls
    now[0] += 2
    _call(_request(cost=30))
    assert calls == {"item": 1, "promotion": 1}


def test_tenant_root_is_part_of_all_financial_cache_keys(synthetic, monkeypatch, tmp_path):
    _, _, calls, _ = synthetic
    _call(_request())
    calls.clear()
    monkeypatch.setattr(service.medias_common, "get_tenant_path", lambda client: str(tmp_path / "other-root" / client))
    _call(_request())
    assert calls == {"item": 1, "promotion": 1, "fee": 1, "shipping": 1}


def test_document_cache_isolated_by_tenant_store_seller_site_and_root(monkeypatch):
    monkeypatch.setattr(financeiro, "_DOCUMENTOS", financeiro.CacheConsultas(100))
    identities = [("tenant-a", "root-a", "store-a", "seller-a", "MLB"),
                  ("tenant-b", "root-a", "store-a", "seller-a", "MLB"),
                  ("tenant-a", "root-b", "store-a", "seller-a", "MLB"),
                  ("tenant-a", "root-a", "store-b", "seller-a", "MLB"),
                  ("tenant-a", "root-a", "store-a", "seller-b", "MLB"),
                  ("tenant-a", "root-a", "store-a", "seller-a", "MLC")]
    calls = []
    for tenant, root, store, seller, site in identities * 2:
        cfg = {"_store_id_context": store, "user_id": seller, "site_id": site, "access_token": "synthetic"}

        def request(*_, **__):
            calls.append((tenant, root, store, seller, site))
            return _Response({"id": "MLB1", "seller_id": seller, "site_id": site}), cfg

        deps = {"carregar_lojas": lambda _: [{"nome": "Loja", "store_id": store, "integracoes": {
                    "mercadolivre": {"user_id": seller, "site_id": site, "connected": True}}}],
                "obter_cfg_ml": lambda *_, **__: cfg, "ml_api_request": request,
                "ml_obter_taxas_anuncio": None, "ml_obter_frete_detalhado": None}
        with financeiro.contexto_financeiro(tenant, root):
            wrapped = financeiro.dependencias_com_cache(deps)
            result, _ = wrapped["ml_api_request"](tenant, "Loja", cfg, "GET", "https://api.mercadolibre.com/items/MLB1")
            assert result.json()["seller_id"] == seller and result.json()["site_id"] == site
    assert calls == identities


def test_same_item_document_singleflight_and_reauthorization(synthetic):
    _, harness, calls, _ = synthetic
    entered, release = Event(), Event()
    original = harness.deps["ml_api_request"]

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    deps = {**harness.deps, "ml_api_request": blocked}
    cfg = harness.deps["obter_cfg_ml"]("tenant-a", "Carlos", store_id="store-carlos")

    def read():
        with financeiro.contexto_financeiro("tenant-a", "synthetic-root"):
            wrapped = financeiro.dependencias_com_cache(deps)
            return wrapped["ml_api_request"]("tenant-a", "Carlos", cfg, "GET", "https://api.mercadolibre.com/items/MLB1")[0].json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(read), pool.submit(read)
        assert entered.wait(3)
        release.set()
        assert one.result() == two.result()
    assert calls == {"item": 1}
    harness.stores[0]["integracoes"]["mercadolivre"]["connected"] = False
    with pytest.raises(HTTPException) as error:
        read()
    assert error.value.status_code == 409 and calls == {"item": 1}


def test_authorization_change_during_provider_wait_is_not_cached(synthetic):
    _, harness, calls, _ = synthetic
    original = harness.deps["ml_api_request"]

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        harness.stores[0]["integracoes"]["mercadolivre"]["connected"] = False
        return result

    cfg = harness.deps["obter_cfg_ml"]("tenant-a", "Carlos", store_id="store-carlos")
    with financeiro.contexto_financeiro("tenant-a", "synthetic-root"):
        wrapped = financeiro.dependencias_com_cache({**harness.deps, "ml_api_request": revoked})
        with pytest.raises(HTTPException):
            wrapped["ml_api_request"]("tenant-a", "Carlos", cfg, "GET", "https://api.mercadolibre.com/items/MLB1")
        assert not financeiro._DOCUMENTOS._dados


def test_source_item_snapshot_is_reused_privately_without_extending_ttl(anuncios_env, monkeypatch, tmp_path):
    from backend.services import importacoes_sku_anuncios as ads, importacoes_consultas_cache as cache

    state = anuncios_env
    now = [10.0]
    monkeypatch.setattr(cache, "monotonic", lambda: now[0])
    monkeypatch.setattr(ads, "_CACHE_ANUNCIOS", financeiro.CacheConsultas(100))
    scope = {"store_id": state["store"], "seller_id": state["seller"], "site_id": "MLB", "loja": state["name"]}
    deps = {
        "carregar_lojas": ads.integracoes.ler_lojas,
        "obter_cfg_ml": lambda *_, **__: ads._cfg_atual(state["client"], scope),
        "ml_api_request": lambda *_, **__: pytest.fail("Source snapshot must avoid a duplicate item request"),
        "ml_obter_taxas_anuncio": lambda *_, **__: None,
        "ml_obter_frete_detalhado": lambda *_, **__: None,
    }

    def read():
        with financeiro.contexto_financeiro(state["client"], tmp_path):
            result = ads.buscar_anuncios_sku_da_lista(state["client"],
                {"store_id": state["store"], "itens": [{"SKU": state["sku"]}]}, state["sku"], cache_scope=str(tmp_path))
            assert "_itens_financeiros" not in result
            wrapped = financeiro.dependencias_com_cache(deps)
            cfg = ads._cfg_atual(state["client"], scope)
            response, _ = wrapped["ml_api_request"](state["client"], state["name"], cfg, "GET",
                                                   "https://api.mercadolibre.com/items/" + state["ids"][0])
            assert response.json()["seller_id"] == state["seller"]

    read()
    original_calls = len(state["calls"])
    now[0] += 59
    read()
    assert len(state["calls"]) == original_calls
    now[0] += 2
    read()
    assert len(state["calls"]) == original_calls * 2
    with financeiro.contexto_financeiro(state["client"], tmp_path / "other-root"):
        with pytest.raises(HTTPException) as error:
            financeiro.usar_itens_verificados(state["client"], scope, {}, cache_scope=str(tmp_path))
        assert error.value.status_code == 409


@pytest.mark.parametrize("field,value", [
    ("variation_id", "22"), ("category_id", "MLB456"), ("condition", "used"),
    ("dimensions", "10x20x30,1000"), ("item_price", 105),
])
def test_shipping_quote_changes_with_actual_inputs(synthetic, field, value):
    _, harness, calls, _ = synthetic
    cfg = harness.deps["obter_cfg_ml"]("tenant-a", "Carlos", store_id="store-carlos")
    shipping = {"mode": "me2", "logistic_type": "cross_docking", "free_shipping": True}
    context = {**shipping, "item_price": 100, "category_id": "MLB123", "condition": "new", "variation_id": "11",
               "dimensions": "6x15x20,500", "listing_type_id": "gold_special"}
    with financeiro.contexto_financeiro("tenant-a", "synthetic-root"):
        for inputs in (context, context, {**context, field: value}):
            result, _ = harness.deps["ml_obter_frete_detalhado"](
                "tenant-a", "Carlos", cfg, "MLB1", shipping, contexto_frete=inputs, reconsultar_zero=True,
            )
            assert result["shipping_exact_for_price"]
    assert calls == {"shipping": 2}


@pytest.mark.parametrize("same_sku", [False, True])
def test_parallel_commits_merge_other_sku_but_reject_same_sku_overwrite(synthetic, monkeypatch, same_sku):
    state, _, _, _ = synthetic
    state["tenant-a"][0]["itens"].append({"SKU": "002", "Quantidade": 1})
    barrier = Barrier(2)
    computations = []

    def calculate(client, loja, sku, prices, ads, **kwargs):
        computations.append(sku)
        barrier.wait(timeout=3)
        return {"concorrente_1": {"custo_unitario": kwargs["custo_unitario_simulado"], "margem_percentual": 30}}

    monkeypatch.setattr(service, "_calcular_margens_concorrentes_promocoes", calculate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_call, _request(own=False, cost=cost, signature=sig), sku=sku)
                   for sku, cost, sig in [("001", 20, "b"), ("001" if same_sku else "002", 30, "c")]]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except HTTPException as error:
                results.append(error)
    assert len(computations) == 2  # No whole-list retry repeats provider work.
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(failures) == int(same_sku)
    if failures:
        assert failures[0].status_code == 409
    saved = [item.get("analise_simulacao_importacao_sku") for item in state["tenant-a"][0]["itens"]]
    assert sum(bool(item) for item in saved) == (1 if same_sku else 2)
    if not same_sku:
        assert [item["custo_unitario"] for item in saved] == [20, 30]


def test_two_import_analyses_leave_local_workers_available():
    async def scenario():
        entered = [Event(), Event()]
        release = Event()

        def work(index):
            entered[index].set()
            assert release.wait(3)
            return index

        tasks = [asyncio.create_task(workers.run_import_financial(work, index)) for index in range(2)]
        try:
            assert await asyncio.to_thread(entered[0].wait, 2)
            assert await asyncio.to_thread(entered[1].wait, 2)
            assert workers.heavy_worker_limiter().borrowed_tokens == 0
            assert await asyncio.wait_for(workers.run_heavy(lambda: "local-ready"), 1) == "local-ready"
        finally:
            release.set()
        assert await asyncio.gather(*tasks) == [0, 1]

    asyncio.run(scenario())
