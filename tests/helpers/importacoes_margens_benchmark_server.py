"""Loopback-only benchmark fixture: real import margins/commits, synthetic providers.

Run through scripts/benchmark_importacoes_margens_browser.js. All data lives in a
temporary directory. External network calls are rejected rather than mocked by
the browser, so the production HTTP handlers and financial workers are measured.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import requests
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import blocking_workers as workers
from backend.services import importacoes_base_custos as base
from backend.services import importacoes_financeiro as financeiro
from backend.services import importacoes_sku_anuncios as anuncios
from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as listas
from backend.services.promocoes_core_analise import _calcular_margem_liquida_ml


CLIENT = "benchmark"
STORE = "store-benchmark"
SELLER = "111"
SHOP = "Loja sintética"
REVISION = "a" * 64
FISCAL = {"convenio_mg_st_minas": "Não", "convenio_mg_st_paraiba": "Não", "convenio_mg_mva": ""}
ORIGIN = ContextVar("benchmark_request_origin", default="interactive")
TEMPDIR = tempfile.TemporaryDirectory(prefix="jk-margens-benchmark-")
atexit.register(TEMPDIR.cleanup)
TENANT = Path(TEMPDIR.name) / CLIENT
TENANT.mkdir()
STATE = {"scenario": "uniforme", "sku_count": 12, "provider_limit": 4, "calls": Counter(),
         "active": Counter(), "maximum": Counter(), "completed": Counter(), "http_errors": []}
LOCK = threading.Lock()
PROVIDER = threading.Semaphore(4)
SOURCE_DELAY = {"planilha": .025, "anuncios": .040}
PROVIDER_DELAY = {"item": .045, "fee": .055, "shipping": .055, "promotion": .035}


def record_delay(kind, *, item_id="", delay=None):
    """One configured provider budget shared by list and interactive work."""
    with PROVIDER:
        with LOCK:
            STATE["calls"][kind] += 1
            STATE["active"]["provider"] += 1
            STATE["maximum"]["provider"] = max(STATE["maximum"]["provider"], STATE["active"]["provider"])
        try:
            extra = 1.0 if STATE["scenario"] == "sku_lento" and kind == "item" and item_id == "MLB1000000011" else 0
            time.sleep((PROVIDER_DELAY.get(kind, .02) if delay is None else delay) + extra)
        finally:
            with LOCK:
                STATE["active"]["provider"] -= 1


def ads_for(sku):
    number = int(sku)
    rows = []
    for index in range(1, 3):
        item_id = f"MLB{1000000000 + number * 10 + index}"
        variation = str(900000 + number) if index == 2 else None
        price = 80 + number % 5 * 3 + (index - 1) * 15
        rows.append({"chave": f"concorrente_{index}", "item_id": item_id,
                     "url": "https://produto.mercadolivre.com.br/" + item_id.replace("MLB", "MLB-")
                            + (f"?variation={variation}" if variation else ""),
                     "preco": price, "status": "active", "seller_id": SELLER, "site_id": "MLB",
                     "sku": sku, "variation_id": variation, "variation_ids": [variation] if variation else []})
    return rows


def fixture_item(sku):
    return {"SKU": sku, "Quantidade": 10, "Valor unidade": 2 + int(sku) % 4 * .25,
            "Valor total": 10 * (2 + int(sku) % 4 * .25), "M3": .05, "M3 individual": .005,
            "Cubagem status": "historico", "II": 10, "IPI": 5, "PIS": 2, "COFINS": 8,
            "ST Minas": "Não", "ST Paraíba": "Não"}


def initial_list():
    return {"id": "A", "nome_lista": "Benchmark de margens", "status": "Recebido", "loja": SHOP,
            "store_id": STORE, "exchange_rate": 5, "Valor por M³": 12,
            "itens": [fixture_item(f"{index:03}") for index in range(1, STATE["sku_count"] + 1)] + [fixture_item("900")]}


def read_list():
    return common._carregar_listas_pedidos(CLIENT)[0]


def visible_list():
    data = deepcopy(read_list())
    data["itens"] = [row for row in data["itens"] if row["SKU"] != "900"]
    return data


def base_data():
    order = visible_list()
    return {"success": True, "tipo": "importacoes_base_custos", "versao": 1, "revisao": REVISION,
            "lista": {key: order[key] for key in ("id", "nome_lista", "loja", "store_id", "exchange_rate", "Valor por M³")},
            "revisoes_sku": {row["SKU"]: REVISION for row in read_list()["itens"]},
            "itens": [{**row, "fiscal": FISCAL, "fiscal_pronto": True} for row in order["itens"]],
            "rateio": {"total_usd": sum(row["Valor total"] for row in order["itens"]),
                       "total_m3": .05 * len(order["itens"]), "m3_completo": True, "valor_por_m3": 12}}


def sources(sku, only_own=False):
    rows = ads_for(sku)
    return {"success": True, "fontes_versao": 3, "consultado_em_ms": int(time.time() * 1000),
            "anuncios_loja_fonte": "loja_lista_sku", "anuncios_loja_store_id": STORE,
            "anuncios_loja_seller_id": SELLER, "anuncios_loja_site_id": "MLB", "anuncios_versao": 2,
            "anuncios_loja": rows, "anuncios_loja_variacoes": {row["chave"]: row["variation_id"] for row in rows},
            "anuncios_loja_status": {row["chave"]: row["status"] for row in rows},
            "concorrentes": {} if only_own else {row["chave"]: row["url"] for row in rows},
            "concorrentes_valores": {} if only_own else {row["chave"]: row["preco"] - 5 for row in rows},
            "concorrentes_mlb": {row["chave"]: row["item_id"] for row in rows},
            "concorrentes_preco": {row["chave"]: row["preco"] for row in rows}}


def identity(client, name, store_id=None):
    assert (client, name, store_id) == (CLIENT, SHOP, STORE)
    return {"_store_id_context": STORE, "user_id": SELLER, "site_id": "MLB", "access_token": "synthetic"}


def stores(client):
    assert client == CLIENT
    return [{"nome": SHOP, "store_id": STORE, "integracoes": {"mercadolivre": {
        "user_id": SELLER, "site_id": "MLB", "access_token": "synthetic", "connected": True}}}]


class ProviderResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


def provider_item(client, name, cfg, method, url, **kwargs):
    assert (client, name, method) == (CLIENT, SHOP, "GET")
    item_id = url.rsplit("/", 1)[-1]
    if "/seller-promotions/" in url:
        record_delay("promotion", item_id=item_id)
        return ProviderResponse([]), cfg
    number = (int(item_id[3:]) - 1000000000) // 10
    sku = f"{number:03}"
    row = next(row for row in ads_for(sku) if row["item_id"] == item_id)
    record_delay("item", item_id=item_id)
    payload = {"id": item_id, "seller_id": SELLER, "site_id": "MLB", "status": "active", "currency_id": "BRL",
               "price": row["preco"], "seller_custom_field": sku, "shipping": {"mode": "me2", "dimensions": "20x15x6,500"},
               "category_id": "MLB123", "listing_type_id": "gold_special", "condition": "new", "variations": []}
    if row["variation_id"]:
        payload["variations"] = [
            {"id": int(row["variation_id"]), "price": row["preco"], "seller_custom_field": sku,
             "shipping": {"dimensions": "20x15x6,500"}},
            {"id": int(row["variation_id"]) + 10000, "price": row["preco"] + 20, "seller_custom_field": "foreign"},
        ]
    return ProviderResponse(payload), cfg


def fee(client, name, cfg, item):
    record_delay("fee", item_id=item["id"])
    return {"ad_cost": item["price"] * .15, "ad_cost_exact_for_price": True,
            "ad_cost_price_context": item["price"], "ad_cost_source": "sites/MLB/listing_prices"}, cfg


def shipping(client, name, cfg, item_id, raw, **kwargs):
    record_delay("shipping", item_id=item_id)
    return {"shipping_cost": 8, "shipping_exact_for_price": True,
            "shipping_price_context": kwargs["contexto_frete"]["item_price"],
            "shipping_cost_retry_source": "users/shipping_options/free/contexto"}, cfg


def verified_ads(client, order, sku, *, cache_scope):
    assert client == CLIENT and order["store_id"] == STORE and cache_scope
    return {"store_id": STORE, "loja": SHOP, "seller_id": SELLER, "site_id": "MLB",
            "consultado_em_ms": int(time.time() * 1000), "anuncios": ads_for(sku), "anuncios_versao": 2,
            "limite": None, "truncado": False}


def verify_revision(client, lista_id, sku, revision, *, lista=None):
    assert client == CLIENT and lista_id == "A" and revision == REVISION
    assert any(row["SKU"] == sku for row in (lista or read_list())["itens"])


def no_network(*args, **kwargs):
    raise RuntimeError("External network forbidden by the synthetic benchmark")


common.get_tenant_path = lambda client: str(TENANT) if client == CLIENT else (_ for _ in ()).throw(HTTPException(403))
listas.get_tenant_path = common.get_tenant_path
base.exigir_revisao_custo = verify_revision
anuncios.buscar_anuncios_sku_da_lista = verified_ads
requests.sessions.Session.request = no_network
listas._dependencias_margens_concorrentes = lambda: {
    "carregar_lojas": stores, "obter_cfg_ml": identity, "ml_api_request": provider_item,
    "ml_obter_taxas_anuncio": fee, "ml_obter_frete_detalhado": shipping,
    "ml_contexto_frete_item": lambda item, price: {"item_price": price},
    "carregar_custos_impostos": lambda client, name, store, sku: ({sku: 10}, {sku: .10}),
    "resolver_custo": lambda costs, sku: costs.get(sku), "resolver_imposto": lambda taxes, sku: taxes.get(sku),
    "calcular_margem": _calcular_margem_liquida_ml,
}
_calculate = listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync


def metered_calculate(*args, **kwargs):
    lane = ORIGIN.get()
    with LOCK:
        STATE["active"][lane] += 1
        STATE["maximum"][lane] = max(STATE["maximum"][lane], STATE["active"][lane])
    try:
        value = _calculate(*args, **kwargs)
        with LOCK:
            STATE["completed"][lane] += 1
        return value
    finally:
        with LOCK:
            STATE["active"][lane] -= 1


listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync = metered_calculate
common._salvar_listas_pedidos(CLIENT, [initial_list()])
app = FastAPI()


def require_auth(request: Request):
    if request.headers.get("authorization") != "Bearer synthetic":
        raise HTTPException(401)
    return CLIENT


app.dependency_overrides[common.get_tenant_id] = require_auth


@app.middleware("http")
async def request_metrics(request, call_next):
    token = ORIGIN.set("list" if request.headers.get("X-JK-Importacoes-Origem") == "lista" else "interactive")
    try:
        response = await call_next(request)
        if response.status_code >= 400 and request.url.path.startswith("/api/"):
            STATE["http_errors"].append({"route": request.url.path, "status": response.status_code})
        return response
    finally:
        ORIGIN.reset(token)


@app.get("/__benchmark/health")
def health():
    return {"ready": True}


@app.post("/__benchmark/shutdown")
def shutdown(client=Depends(require_auth)):
    app.state.server.should_exit = True
    return {"success": True}


@app.post("/__benchmark/reset")
def reset(data: dict, client=Depends(require_auth)):
    global PROVIDER
    assert not any(STATE["active"].values())
    STATE.update(scenario=data.get("scenario", "uniforme"), sku_count=int(data.get("sku_count", 12)),
                 provider_limit=int(data.get("provider_limit", 4)), calls=Counter(), active=Counter(),
                 maximum=Counter(), completed=Counter(), http_errors=[])
    PROVIDER = threading.Semaphore(STATE["provider_limit"])
    financeiro._DOCUMENTOS = financeiro.CacheConsultas(1024)
    common._salvar_listas_pedidos(CLIENT, [initial_list()])
    return {"success": True}


@app.get("/__benchmark/metrics")
def metrics(client=Depends(require_auth)):
    with LOCK:
        data = deepcopy(STATE)
    analyses = {row["SKU"]: row.get("analise_simulacao_importacao_sku") for row in read_list()["itens"]}
    data["saved"] = {sku: analysis for sku, analysis in analyses.items() if analysis}
    return data


@app.get("/api/medias-compras/listas-pedidos/A")
def order(client=Depends(require_auth)):
    return {"lista": visible_list()}


@app.get("/api/medias-compras/listas-pedidos/A/base-custos")
def cost_basis(somente_revisao: bool = False, client=Depends(require_auth)):
    result = base_data()
    if somente_revisao:
        del result["itens"], result["rateio"]
    return result


@app.get("/api/medias-compras/listas-pedidos/A/skus/{sku}")
def detail(sku: str, client=Depends(require_auth)):
    order = read_list()
    row = next(row for row in order["itens"] if row["SKU"] == sku)
    return {"lista": {key: order[key] for key in ("id", "nome_lista", "loja", "store_id", "exchange_rate", "Valor por M³")},
            "item": row, "cadastro": {"sku": sku, "store_id": STORE, "custo": 10}, "fiscal": FISCAL,
            "fiscal_pronto": True, "rateio": base_data()["rateio"], "revisao": REVISION,
            "revisao_fontes_custo": REVISION, "anuncios_identidade": {"seller_id": SELLER, "site_id": "MLB", "fontes_versao": 3}}


@app.get("/api/medias-compras/concorrentes-links")
async def offers(sku: str, somente_proprios: bool = False, client=Depends(require_auth)):
    def source(kind):
        time.sleep(SOURCE_DELAY[kind])
        return None
    queries = [workers.run_import_offer_source("anuncios", source, "anuncios")]
    if not somente_proprios:
        queries.append(workers.run_import_offer_source("planilha", source, "planilha"))
    await asyncio.gather(*queries)
    return sources(sku, somente_proprios)


@app.get("/api/cadastro/lojas/{store}/produtos")
def catalog(store: str, view: str = "", somente_revisao: bool = False, client=Depends(require_auth)):
    assert store == STORE
    if somente_revisao:
        return {"store_id": STORE, "revisao": "benchmark-cost-v1"}
    return Response(json.dumps([{ "store_id": STORE, "sku": row["SKU"], "custo": 10} for row in read_list()["itens"]]),
                    media_type="application/json", headers={"X-JK-Custos-Revisao": "benchmark-cost-v1"})


@app.get("/api/medias-compras/listas-pedidos/A/landed-cost")
def landed(client=Depends(require_auth)):
    return {"analysis": {}}


@app.get("/api/vendas/grafico")
def sales(client=Depends(require_auth)):
    return {"valores_vendas": [], "valores_devolucoes": []}


@app.get("/api/cadastro/{rest:path}")
def other_catalog(rest: str, client=Depends(require_auth)):
    return []


app.include_router(create_medias_compras_router())


@app.get("/auth.js")
def authentication_script():
    return Response('window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});',
                    media_type="application/javascript")


@app.get("/{asset:path}")
def static_asset(asset: str):
    path = (ROOT / "static" / asset).resolve()
    if not path.is_relative_to(ROOT / "static") or not path.is_file():
        return Response("", media_type="text/plain")
    return FileResponse(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, access_log=False, log_level="error"))
    app.state.server = server
    server.run()
