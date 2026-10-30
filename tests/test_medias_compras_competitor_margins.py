import asyncio
import copy
import threading

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas.medias_compras import (
    ListaPedidoSkuAnaliseConcorrentesRequest,
    ListaPedidoUpdateRequest,
)
from backend.services import medias_compras_listas as listas_service
from backend.services.promocoes_core_analise import _calcular_margem_liquida_ml


def _configurar_estado(monkeypatch):
    estado = {
        "tenant-a": [
            {
                "id": "lista-1",
                "nome_lista": "Pedido A",
                "status": "Analisando orçamento",
                "loja": "Loja A",
                "itens": [{"SKU": "001", "Quantidade": 10}],
            }
        ],
        "tenant-b": [
            {
                "id": "lista-1",
                "nome_lista": "Pedido B",
                "status": "Analisando orçamento",
                "itens": [{"SKU": "001", "Quantidade": 20}],
            }
        ],
    }

    monkeypatch.setattr(
        listas_service,
        "_carregar_listas_pedidos",
        lambda client_id: copy.deepcopy(estado.get(client_id, [])),
    )

    def salvar(client_id, listas):
        estado[client_id] = copy.deepcopy(listas)

    monkeypatch.setattr(listas_service, "_salvar_listas_pedidos", salvar)
    monkeypatch.setattr(listas_service, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listas_service, "_recalcular_frete_internacional_itens_lista", lambda _client_id, itens, **_kwargs: itens)
    monkeypatch.setattr(listas_service, "_resumo_lista_pedido", lambda lista, *_args, **_kwargs: {"id": lista["id"]})
    monkeypatch.setattr(listas_service, "_normalizar_sku_mes", lambda sku: str(sku or "").strip().upper())
    return estado


def test_endpoint_calcula_e_persiste_margens_sem_misturar_tenants(monkeypatch):
    estado = _configurar_estado(monkeypatch)
    chamadas = []

    def calcular(client_id, loja, sku, precos, anuncios):
        chamadas.append((client_id, loja, sku, precos, anuncios))
        return {
            "concorrente_1": {
                "preco_venda": 100,
                "custo_unitario": 60,
                "imposto_valor": 10,
                "tarifa_ml": 15,
                "frete_ml": 8,
                "valor_liquido": 7,
                "margem_percentual": 7,
                "financeiro_exato": True,
            },
            "concorrente_2": {
                "preco_venda": 50,
                "item_id_loja": "MLB222",
                "margem_percentual": None,
                "financeiro_exato": False,
                "motivo_indisponivel": "Frete exato não disponível.",
            },
        }

    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", calcular)

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAnaliseConcorrentesRequest(
                custo_unitario=999,
                precos_concorrentes={
                    "concorrente_1": 100,
                    "concorrente_2": 50,
                },
                anuncios_loja={
                    "concorrente_1": "MLB111",
                    "concorrente_2": "MLB222",
                },
            ),
            client_id="tenant-a",
        )
    )

    analise = resultado["analise_concorrentes"]
    assert analise["custo_unitario"] == 60
    assert analise["fonte_custo"] == "cadastro_sku_loja"
    assert analise["metodo"] == "analise_promocoes_ml_liquida"
    assert analise["margens"]["concorrente_1"]["margem_percentual"] == 7
    assert analise["margens"]["concorrente_2"]["margem_percentual"] is None
    assert chamadas == [
        (
            "tenant-a",
            "Loja A",
            "001",
            {"concorrente_1": 100.0, "concorrente_2": 50.0},
            {"concorrente_1": "MLB111", "concorrente_2": "MLB222"},
        )
    ]
    assert analise["atualizado_em"]
    assert estado["tenant-a"][0]["itens"][0]["analise_concorrentes"] == analise
    assert "analise_concorrentes" not in estado["tenant-b"][0]["itens"][0]


def test_endpoint_limpa_margens_quando_analise_nao_tem_precos(monkeypatch):
    estado = _configurar_estado(monkeypatch)
    monkeypatch.setattr(
        listas_service,
        "_calcular_margens_concorrentes_promocoes",
        lambda *_args, **_kwargs: pytest.fail("Calculadora não deveria ser chamada sem preços."),
    )
    estado["tenant-a"][0]["itens"][0]["analise_concorrentes"] = {
        "custo_unitario": 10,
        "margens": {"concorrente_1": {"preco_venda": 20, "margem_percentual": 50}},
        "atualizado_em": "2026-08-16T10:00:00",
    }

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAnaliseConcorrentesRequest(),
            client_id="tenant-a",
        )
    )

    assert resultado["analise_concorrentes"]["custo_unitario"] is None
    assert resultado["analise_concorrentes"]["margens"] == {}


@pytest.mark.parametrize(
    ("payload_req", "mensagem"),
    [
        (
            ListaPedidoSkuAnaliseConcorrentesRequest(
                custo_unitario=-1,
                precos_concorrentes={"concorrente_1": 100},
            ),
            "Custo unitario invalido",
        ),
        (
            ListaPedidoSkuAnaliseConcorrentesRequest(
                custo_unitario=10,
                precos_concorrentes={"concorrente_6": 100},
            ),
            "Concorrente invalido",
        ),
        (
            ListaPedidoSkuAnaliseConcorrentesRequest(
                custo_unitario=10,
                precos_concorrentes={"concorrente_1": 0},
            ),
            "Preco de concorrente invalido",
        ),
        (
            ListaPedidoSkuAnaliseConcorrentesRequest(
                custo_unitario=10,
                precos_concorrentes={"concorrente_1": 100},
                anuncios_loja={"concorrente_1": "anuncio-invalido"},
            ),
            "Anuncio da loja invalido",
        ),
    ],
)
def test_endpoint_rejeita_dados_invalidos(monkeypatch, payload_req, mensagem):
    _configurar_estado(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
                "lista-1",
                "001",
                payload_req,
                client_id="tenant-a",
            )
        )

    assert exc_info.value.status_code == 400
    assert mensagem in exc_info.value.detail


class _RespostaMlFake:
    status_code = 200

    def __init__(self, seller_id="123"):
        self._seller_id = seller_id

    def json(self):
        return {
            "id": "MLB111",
            "seller_id": self._seller_id,
            "category_id": "MLB1",
            "listing_type_id": "gold_special",
            "condition": "new",
            "shipping": {
                "mode": "me2",
                "logistic_type": "cross_docking",
                "free_shipping": True,
            },
        }


def _dependencias_financeiras_fake(*, seller_cfg="123", seller_item="123", frete_exato=True):
    return {
        "carregar_lojas": lambda _client_id: [],
        "obter_cfg_ml": lambda _client_id, _loja: {"user_id": seller_cfg, "access_token": "token-teste"},
        "ml_api_request": lambda _client_id, _loja, cfg, _method, _url, **_kwargs: (
            _RespostaMlFake(seller_item),
            cfg,
        ),
        "ml_contexto_frete_item": lambda _item, preco: {"item_price": preco},
        "ml_obter_frete_detalhado": lambda _client_id, _loja, cfg, _item_id, _shipping, **_kwargs: (
            {
                "shipping_cost": 8,
                "shipping_exact_for_price": frete_exato,
                "shipping_price_context": 100,
                "shipping_cost_retry_source": "shipping_options/contexto",
            },
            cfg,
        ),
        "ml_obter_taxas_anuncio": lambda _client_id, _loja, cfg, _item: (
            {
                "ad_cost": 15,
                "ad_cost_exact_for_price": True,
                "ad_cost_price_context": 100,
                "ad_cost_source": "sites/MLB/listing_prices",
            },
            cfg,
        ),
        "carregar_custos_impostos": lambda _client_id, _loja: ({"001": 60}, {"001": 0.10}),
        "resolver_custo": lambda custos, sku: custos.get(sku),
        "resolver_imposto": lambda impostos, sku: impostos.get(sku),
        "calcular_margem": _calcular_margem_liquida_ml,
    }


def test_calculadora_reutiliza_formula_liquida_de_promocoes(monkeypatch):
    monkeypatch.setattr(
        listas_service,
        "_dependencias_margens_concorrentes",
        lambda: _dependencias_financeiras_fake(),
    )

    margens = listas_service._calcular_margens_concorrentes_promocoes(
        "tenant-a",
        "Loja A",
        "001",
        {"concorrente_1": 100},
        {"concorrente_1": "MLB111"},
    )

    assert margens["concorrente_1"] == {
        "preco_venda": 100.0,
        "item_id_loja": "MLB111",
        "loja": "Loja A",
        "custo_unitario": 60.0,
        "imposto_percentual": 10.0,
        "imposto_valor": 10.0,
        "tarifa_ml": 15.0,
        "frete_ml": 8.0,
        "valor_liquido": 7.0,
        "margem_percentual": 7.0,
        "financeiro_exato": True,
        "tarifa_fonte": "sites/MLB/listing_prices",
        "frete_fonte": "shipping_options/contexto",
    }


def test_calculadora_nao_usa_custo_da_importacao_quando_cadastro_esta_vazio(monkeypatch):
    deps = _dependencias_financeiras_fake()
    deps["carregar_custos_impostos"] = lambda _client_id, _loja: ({}, {"001": 0.10})
    deps["ml_obter_taxas_anuncio"] = lambda *_args, **_kwargs: pytest.fail(
        "Tarifa não deve ser consultada sem custo cadastrado."
    )
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)

    dados = listas_service._calcular_margens_concorrentes_promocoes(
        "tenant-a",
        "Loja A",
        "001",
        {"concorrente_1": 100},
        {"concorrente_1": "MLB111"},
    )["concorrente_1"]

    assert dados["margem_percentual"] is None
    assert "Custo do SKU não encontrado no cadastro" in dados["motivo_indisponivel"]


def test_calculadora_nao_estima_margem_sem_frete_exato(monkeypatch):
    monkeypatch.setattr(
        listas_service,
        "_dependencias_margens_concorrentes",
        lambda: _dependencias_financeiras_fake(frete_exato=False),
    )

    dados = listas_service._calcular_margens_concorrentes_promocoes(
        "tenant-a",
        "Loja A",
        "001",
        {"concorrente_1": 100},
        {"concorrente_1": "MLB111"},
    )["concorrente_1"]

    assert dados["margem_percentual"] is None
    assert dados["financeiro_exato"] is False
    assert "Frete exato" in dados["motivo_indisponivel"]


def test_calculadora_rejeita_anuncio_de_outra_loja(monkeypatch):
    deps = _dependencias_financeiras_fake(seller_cfg="999", seller_item="123")
    deps["ml_obter_taxas_anuncio"] = lambda *_args, **_kwargs: pytest.fail(
        "Tarifa não pode ser consultada para anúncio de outra loja."
    )
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)

    dados = listas_service._calcular_margens_concorrentes_promocoes(
        "tenant-a",
        "Loja A",
        "001",
        {"concorrente_1": 100},
        {"concorrente_1": "MLB111"},
    )["concorrente_1"]

    assert dados["margem_percentual"] is None
    assert "não pertence à loja" in dados["motivo_indisponivel"]


def test_calculadora_infere_loja_unica_em_lista_antiga_sem_loja(monkeypatch):
    deps = _dependencias_financeiras_fake()
    deps["carregar_lojas"] = lambda _client_id: [
        {"nome": "Loja A", "integracoes": {"mercadolivre": {"access_token": "a"}}},
        {"nome": "Loja B", "integracoes": {"mercadolivre": {"access_token": "b"}}},
    ]
    deps["obter_cfg_ml"] = lambda _client_id, loja: {
        "user_id": "123" if loja == "Loja A" else "999",
        "access_token": "token-teste",
    }
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)

    dados = listas_service._calcular_margens_concorrentes_promocoes(
        "tenant-a",
        "__todas",
        "001",
        {"concorrente_1": 100},
        {"concorrente_1": "MLB111"},
    )["concorrente_1"]

    assert dados["loja"] == "Loja A"
    assert dados["margem_percentual"] == 7.0


def test_update_generico_preserva_analise_gravada(monkeypatch):
    estado = _configurar_estado(monkeypatch)
    analise_original = {
        "custo_unitario": 60,
        "margens": {"concorrente_1": {"preco_venda": 100, "margem_percentual": 40}},
        "margens_anuncios_loja": {"concorrente_1": {"preco_venda": 120, "margem_percentual": 45}},
        "atualizado_em": "2026-08-17T10:00:00",
    }
    estado["tenant-a"][0]["itens"][0]["analise_concorrentes"] = copy.deepcopy(analise_original)

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_editar(
            "lista-1",
            ListaPedidoUpdateRequest(
                itens=[
                    {
                        "SKU": "001",
                        "Quantidade": 15,
                        "analise_concorrentes": {"margens": {}},
                    }
                ]
            ),
            client_id="tenant-a",
        )
    )

    item = resultado["lista"]["itens"][0]
    assert item["Quantidade"] == 15
    assert item["analise_concorrentes"] == analise_original


def test_router_expoe_endpoint_dedicado_de_analise():
    rota = next(
        (
            rota
            for rota in create_medias_compras_router().routes
            if rota.path
            == "/api/medias-compras/listas-pedidos/{lista_id}/skus/{sku}/analise-concorrentes"
        ),
        None,
    )

    assert rota is not None
    assert rota.methods == {"PATCH"}


def test_endpoint_em_lote_carrega_planilha_uma_vez_e_isola_lista(monkeypatch):
    estado = _configurar_estado(monkeypatch)
    estado["tenant-a"][0]["itens"].append({"SKU": "002", "Quantidade": 5})
    chamadas = []
    header = ["SKU"] + [f"coluna-{indice}" for indice in range(1, 22)]
    linha_001 = ["001"] + [""] * 21
    linha_001[2] = "https://produto.mercadolivre.com.br/MLB-11111111"
    linha_001[4] = "https://produto.mercadolivre.com.br/MLB-99999991"
    linha_001[5] = "R$ 100,00"
    linha_002 = ["002"] + [""] * 21
    linha_002[2] = "MLB22222222"
    linha_002[4] = "MLB99999992"
    linha_002[5] = "R$ 200,00"

    def carregar_planilha():
        chamadas.append("planilha")
        return [header, linha_001, linha_002]

    monkeypatch.setattr(listas_service, "_carregar_linhas_planilha_concorrentes", carregar_planilha)
    monkeypatch.setattr(
        listas_service.medias_common, "_resolver_escopo_loja_medias",
        lambda client_id, loja, store_id: {"loja": "Loja A", "store_id": "store-a", "scope": "store"},
    )

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_concorrentes_links_lote(
            "lista-1",
            client_id="tenant-a",
        )
    )

    assert chamadas == ["planilha"]
    assert resultado["total"] == 2
    assert set(resultado["resultados"]) == {"001", "002"}
    assert resultado["resultados"]["001"]["concorrentes_valores"]["concorrente_1"] == "R$ 100,00"
    assert resultado["resultados"]["002"]["concorrentes_mlb"]["concorrente_1"] == "MLB22222222"


def test_calculo_financeiro_e_executado_fora_do_fluxo_principal(monkeypatch):
    _configurar_estado(monkeypatch)
    chamadas = []
    thread_principal = threading.get_ident()

    def calcular(*args):
        chamadas.append((threading.get_ident(), args))
        return {
            "concorrente_1": {
                "preco_venda": 100,
                "custo_unitario": 60,
                "margem_percentual": 7,
                "financeiro_exato": True,
            }
        }

    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", calcular)

    asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAnaliseConcorrentesRequest(
                precos_concorrentes={"concorrente_1": 100},
                anuncios_loja={"concorrente_1": "MLB111"},
            ),
            client_id="tenant-a",
        )
    )

    assert len(chamadas) == 1
    assert chamadas[0][0] != thread_principal
    assert chamadas[0][1] == (
        "tenant-a", "Loja A", "001",
        {"concorrente_1": 100.0}, {"concorrente_1": "MLB111"},
    )


def test_persistencia_da_margem_preserva_aprovacao_feita_durante_calculo(monkeypatch):
    estado = _configurar_estado(monkeypatch)

    def calcular(*_args):
        estado["tenant-a"][0]["itens"][0]["compra_aprovada"] = True
        estado["tenant-a"][0]["itens"][0]["compra_aprovada_em"] = "2026-08-17T12:00:00"
        return {
            "concorrente_1": {
                "preco_venda": 100,
                "custo_unitario": 60,
                "margem_percentual": 7,
                "financeiro_exato": True,
            }
        }

    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", calcular)

    asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAnaliseConcorrentesRequest(
                precos_concorrentes={"concorrente_1": 100},
                anuncios_loja={"concorrente_1": "MLB111"},
            ),
            client_id="tenant-a",
        )
    )

    item = estado["tenant-a"][0]["itens"][0]
    assert item["compra_aprovada"] is True
    assert item["compra_aprovada_em"] == "2026-08-17T12:00:00"
    assert item["analise_concorrentes"]["margens"]["concorrente_1"]["margem_percentual"] == 7


def test_router_expoe_endpoint_de_fontes_em_lote():
    rota = next(
        (
            rota
            for rota in create_medias_compras_router().routes
            if rota.path
            == "/api/medias-compras/listas-pedidos/{lista_id}/concorrentes-links"
        ),
        None,
    )

    assert rota is not None
    assert rota.methods == {"GET"}


def _dependencias_precos_variaveis(chamadas):
    deps = _dependencias_financeiras_fake()

    def item(client_id, loja, cfg, _method, _url, **_kwargs):
        chamadas.append(("item", client_id, loja))
        return _RespostaMlFake(), cfg

    def cadastro(client_id, loja):
        chamadas.append(("cadastro", client_id, loja))
        custo = 40 if client_id == "tenant-b" else 70 if loja == "Loja B" else 60
        return {"001": custo}, {"001": 0.1}

    def tarifa(_client_id, _loja, cfg, anuncio):
        preco = anuncio["price"]
        chamadas.append(("tarifa", preco))
        return {"ad_cost": preco * 0.15, "ad_cost_exact_for_price": True,
                "ad_cost_price_context": preco}, cfg

    def frete(_client_id, _loja, cfg, _item_id, _shipping, **kwargs):
        preco = kwargs["contexto_frete"]["item_price"]
        chamadas.append(("frete", preco))
        return {"shipping_cost": 8, "shipping_exact_for_price": True,
                "shipping_price_context": preco}, cfg

    deps.update(ml_api_request=item, carregar_custos_impostos=cadastro,
                ml_obter_taxas_anuncio=tarifa, ml_obter_frete_detalhado=frete)
    return deps


@pytest.mark.parametrize(("preco_loja", "margem_loja", "consultas_financeiras"), [(100, 7, 1), (120, 18.33, 2)])
def test_endpoint_separa_margens_proprias_e_reutiliza_contexto(monkeypatch, preco_loja, margem_loja, consultas_financeiras):
    estado = _configurar_estado(monkeypatch)
    chamadas = []
    deps = _dependencias_precos_variaveis(chamadas)
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)

    resultado = asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
        "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(
            custo_unitario=999,
            precos_concorrentes={"concorrente_1": 100},
            precos_anuncios_loja={"concorrente_1": preco_loja},
            anuncios_loja={"concorrente_1": "MLB111"},
        ), client_id="tenant-a",
    ))

    analise = resultado["analise_concorrentes"]
    rival = analise["margens"]["concorrente_1"]
    propria = analise["margens_anuncios_loja"]["concorrente_1"]
    assert rival["margem_percentual"] == 7
    assert rival["preco_venda"] == 100
    assert propria["margem_percentual"] == margem_loja
    assert propria["preco_venda"] == preco_loja
    assert propria["item_id_loja"] == "MLB111"
    assert propria["financeiro_exato"] is True
    assert propria["custo_unitario"] == analise["custo_unitario"] == 60
    assert rival is not propria
    assert chamadas.count(("item", "tenant-a", "Loja A")) == 1
    assert chamadas.count(("cadastro", "tenant-a", "Loja A")) == 1
    assert sum(row[0] == "tarifa" for row in chamadas) == consultas_financeiras
    assert sum(row[0] == "frete" for row in chamadas) == consultas_financeiras
    assert estado["tenant-a"][0]["itens"][0]["analise_concorrentes"] == analise
    assert "analise_concorrentes" not in estado["tenant-b"][0]["itens"][0]


def test_contexto_financeiro_nao_e_reutilizado_entre_tenants_ou_lojas(monkeypatch):
    chamadas = []
    deps = _dependencias_precos_variaveis(chamadas)
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)
    contexto = {}
    margens = []
    for client_id, loja in [("tenant-a", "Loja A"), ("tenant-a", "Loja B"), ("tenant-b", "Loja A")]:
        resultado = listas_service._calcular_margens_concorrentes_promocoes(
            client_id, loja, "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
            contexto_consulta=contexto,
        )
        margens.append(resultado["concorrente_1"]["margem_percentual"])
    assert margens == [7, -3, 27]
    assert sum(row[0] == "item" for row in chamadas) == 3
    assert sum(row[0] == "cadastro" for row in chamadas) == 3


@pytest.mark.parametrize("precos", [{"concorrente_1": None}, {"concorrente_1": "sem preco"}])
def test_schema_rejeita_preco_proprio_nulo_ou_nao_numerico(precos):
    with pytest.raises(ValidationError):
        ListaPedidoSkuAnaliseConcorrentesRequest(precos_anuncios_loja=precos)


@pytest.mark.parametrize("precos", [
    {"concorrente_1": 0}, {"concorrente_1": -1},
    {"concorrente_1": float("nan")}, {"concorrente_1": float("inf")},
    {"concorrente_6": 100},
])
def test_endpoint_rejeita_preco_proprio_invalido_sem_calcular(monkeypatch, precos):
    estado = _configurar_estado(monkeypatch)
    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes",
                        lambda *_args, **_kwargs: pytest.fail("Preço inválido não deve consultar o ML."))
    with pytest.raises(HTTPException) as erro:
        asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(precos_anuncios_loja=precos),
            client_id="tenant-a",
        ))
    assert erro.value.status_code == 400
    assert "analise_concorrentes" not in estado["tenant-a"][0]["itens"][0]


@pytest.mark.parametrize("falha", ["anuncio_ausente", "anuncio_outra_loja", "custo", "imposto", "tarifa", "frete", "preco_frete"])
def test_margem_propria_nao_e_estimada_sem_evidencia_exata(monkeypatch, falha):
    _configurar_estado(monkeypatch)
    deps = _dependencias_financeiras_fake(seller_item="999" if falha == "anuncio_outra_loja" else "123")
    if falha in {"custo", "imposto"}:
        deps["carregar_custos_impostos"] = lambda *_args: (
            {} if falha == "custo" else {"001": 60}, {} if falha == "imposto" else {"001": 0.1},
        )
    if falha == "tarifa":
        deps["ml_obter_taxas_anuncio"] = lambda _c, _l, cfg, _item: (
            {"ad_cost": 15, "ad_cost_exact_for_price": False, "ad_cost_price_context": 100}, cfg,
        )
    if falha in {"frete", "preco_frete"}:
        deps["ml_obter_frete_detalhado"] = lambda _c, _l, cfg, *_args, **_kwargs: (
            {"shipping_cost": 8, "shipping_exact_for_price": falha != "frete",
             "shipping_price_context": 90 if falha == "preco_frete" else 100}, cfg,
        )
    monkeypatch.setattr(listas_service, "_dependencias_margens_concorrentes", lambda: deps)
    resultado = asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
        "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(
            precos_anuncios_loja={"concorrente_1": 100},
            anuncios_loja={} if falha == "anuncio_ausente" else {"concorrente_1": "MLB111"},
        ), client_id="tenant-a",
    ))
    propria = resultado["analise_concorrentes"]["margens_anuncios_loja"]["concorrente_1"]
    assert propria["margem_percentual"] is None
    assert propria["financeiro_exato"] is False
    assert propria["motivo_indisponivel"]


@pytest.mark.parametrize("campos", [{}, {"precos_anuncios_loja": None}])
def test_cliente_antigo_preserva_margem_propria_mais_recente(monkeypatch, campos):
    estado = _configurar_estado(monkeypatch)
    item = estado["tenant-a"][0]["itens"][0]
    item["analise_concorrentes"] = {"margens_anuncios_loja": {"concorrente_1": {"preco_venda": 100}}}
    recente = {"concorrente_1": {"preco_venda": 120, "margem_percentual": 18.33, "financeiro_exato": True}}

    def calcular(*_args):
        item["analise_concorrentes"]["margens_anuncios_loja"] = copy.deepcopy(recente)
        item["compra_aprovada"] = True
        return {}

    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", calcular)
    resultado = asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
        "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(
            precos_concorrentes={"concorrente_1": 90}, **campos,
        ), client_id="tenant-a",
    ))
    assert resultado["analise_concorrentes"]["margens_anuncios_loja"] == recente
    assert estado["tenant-a"][0]["itens"][0]["compra_aprovada"] is True


def test_precos_proprios_vazios_limpam_somente_matriz_propria(monkeypatch):
    estado = _configurar_estado(monkeypatch)
    estado["tenant-a"][0]["itens"][0]["analise_concorrentes"] = {"margens_anuncios_loja": {"concorrente_1": {}}}
    rival = {"concorrente_1": {"margem_percentual": 7, "custo_unitario": 60}}
    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", lambda *_args, **_kwargs: rival)
    resultado = asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
        "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(
            precos_concorrentes={"concorrente_1": 100}, precos_anuncios_loja={},
        ), client_id="tenant-a",
    ))
    assert resultado["analise_concorrentes"]["margens"] == rival
    assert resultado["analise_concorrentes"]["margens_anuncios_loja"] == {}


@pytest.mark.parametrize("campo", ["loja", "store_id"])
def test_margem_propria_nao_grava_se_loja_mudou_durante_consulta(monkeypatch, campo):
    estado = _configurar_estado(monkeypatch)

    def calcular(*_args, **_kwargs):
        estado["tenant-a"][0][campo] = "outra-loja"
        return {}

    monkeypatch.setattr(listas_service, "_calcular_margens_concorrentes_promocoes", calcular)
    with pytest.raises(HTTPException) as erro:
        asyncio.run(listas_service.api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
            "lista-1", "001", ListaPedidoSkuAnaliseConcorrentesRequest(precos_anuncios_loja={"concorrente_1": 100}),
            client_id="tenant-a",
        ))
    assert erro.value.status_code == 409
    assert "analise_concorrentes" not in estado["tenant-a"][0]["itens"][0]
