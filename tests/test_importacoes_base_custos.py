import asyncio
from copy import deepcopy

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas.medias_compras import ListaPedidoSkuAnaliseConcorrentesRequest
from backend.services import importacoes_base_custos as service
from backend.services import medias_compras_listas as listas


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    estado = {
        "id": "lista-a", "nome_lista": "Pedido", "status": "Analisando orçamento",
        "loja": "Loja A", "store_id": "loja-a", "exchange_rate": 5.35,
        "itens": [{"SKU": "001", "Quantidade": 4, "Valor unidade": 1.125, "Valor total": 4.5},
                  {"SKU": "002", "Quantidade": 2, "Valor unidade": 4, "Valor total": 8}],
    }
    chamadas = {"cadastro": 0, "fiscal": 0, "enriquecimento": 0}
    for tenant in ("tenant-a", "tenant-b"):
        (tmp_path / tenant).mkdir()
    monkeypatch.setattr(service.common, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    ler = lambda tenant: [deepcopy(estado)] if tenant == "tenant-a" else []
    monkeypatch.setattr(service.common, "_carregar_listas_pedidos", ler)
    monkeypatch.setattr(service.common, "_resolver_escopo_loja_medias", lambda tenant, loja, store, **_: {
        "scope": "store", "loja": "Loja A", "store_id": store,
    })
    monkeypatch.setattr(service, "extras_runtime", lambda _: [])
    monkeypatch.setattr(service, "_bases", service.ResultadoCache())

    def cadastro(tenant, loja, *, skus, _incluir_fotos):
        chamadas["cadastro"] += 1
        assert tenant == "tenant-a" and loja == "loja-a" and not _incluir_fotos
        assert sorted(skus) == ["001", "002"]
        return {"scope": "store", "store_id": loja, "produtos": [
            {"sku": "001", "store_id": loja, "m3 individual": .1},
            {"sku": "002", "store_id": loja, "m3 individual": .3},
        ]}

    def fiscal(tenant, *, filtro_sku):
        chamadas["fiscal"] += 1
        assert tenant == "tenant-a" and filtro_sku("001") and filtro_sku("002") and not filtro_sku("outro")
        return {"produtos": [{"sku": sku, "convenio_mg_st_minas": "SIM", "convenio_mg_mva": 40}
                              for sku in ("001", "002")]}

    def enriquecer(tenant, itens, **kwargs):
        chamadas["enriquecimento"] += 1
        assert kwargs["filtrar_cadastro_fiscal"] and not kwargs["incluir_fotos"]
        return [{**item, "II": 18, "IPI": 0, "PIS": 3.12, "COFINS": 14.37, "Foto": "não-projetar"}
                for item in itens]

    monkeypatch.setattr(service.cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja", cadastro)
    monkeypatch.setattr(service.sku_service.impostos_regras, "_montar_simulacao_impostos", fiscal)
    monkeypatch.setattr(service, "_enriquecer_itens_lista_pedido_com_impostos", enriquecer)
    return estado, chamadas, tmp_path


def test_lote_persistente_sem_recalculo_por_tempo_ordem_margem_ou_fotos(ambiente, monkeypatch):
    estado, chamadas, raiz = ambiente

    async def conferir():
        primeiro, simultaneo = await asyncio.gather(*[
            service.api_importacoes_base_custos("lista-a", "tenant-a") for _ in range(2)
        ])
        assert primeiro == simultaneo
        assert chamadas == {"cadastro": 1, "fiscal": 1, "enriquecimento": 1}
        assert primeiro["rateio"] == {"total_usd": 12.5, "total_m3": 1, "m3_completo": True, "valor_por_m3": 12.5}
        assert primeiro["itens"][0]["M3"] == .4
        assert "Foto" not in primeiro["itens"][0]
        assert primeiro["itens"][0]["fiscal"]["convenio_mg_st_minas"] == "SIM"
        assert primeiro["itens"][0]["II"] == 18
        meta = await service.api_importacoes_base_custos("lista-a", "tenant-a", somente_revisao=True)
        assert meta["revisao"] == primeiro["revisao"] and meta["revisoes_sku"] == primeiro["revisoes_sku"]
        assert "itens" not in meta and "rateio" not in meta

        estado["updated_at"] = "agora"
        estado["nome_lista"] = "Renomeada"
        estado["itens"][0].update(analise_simulacao_importacao_sku={"custo_unitario": 23.75},
                                 analise_concorrentes={"margens": {}}, compra_aprovada=True, Foto="nova")
        estado["itens"].reverse()
        (raiz / "tenant-a" / "vendas_historico_2026.db").write_bytes(b"vendas novas")
        (raiz / "tenant-a" / "cadastro_fotos").mkdir()
        (raiz / "tenant-a" / "cadastro_fotos" / "foto.png").write_bytes(b"foto nova")
        service._bases.clock = lambda: 120 + __import__("time").monotonic()
        assert await service.api_importacoes_base_custos("lista-a", "tenant-a") == primeiro
        assert chamadas["fiscal"] == 1

        # A new service instance exercises the durable JSON rather than memory.
        monkeypatch.setattr(service, "_bases", service.ResultadoCache())
        assert await service.api_importacoes_base_custos("lista-a", "tenant-a") == primeiro
        assert chamadas["fiscal"] == 1

        estado["exchange_rate"] = 5.4
        segundo = await service.api_importacoes_base_custos("lista-a", "tenant-a")
        assert segundo["revisao"] != primeiro["revisao"]
        assert chamadas["fiscal"] == 2
        (raiz / "tenant-a" / "impostos_regras.json").write_text("[]", encoding="utf-8")
        terceiro = await service.api_importacoes_base_custos("lista-a", "tenant-a")
        assert terceiro["revisoes_sku"]["001"] != segundo["revisoes_sku"]["001"]
        assert chamadas["fiscal"] == 3
        monkeypatch.setattr(service, "calculation_revision", lambda: "motor-fiscal-atualizado")
        nova_versao = await service.api_importacoes_base_custos("lista-a", "tenant-a", somente_revisao=True)
        assert nova_versao["revisao"] != terceiro["revisao"], "Uma atualização do motor invalida também o cache do navegador."

    asyncio.run(conferir())


def test_metadata_rota_isolada_sem_cadastro_ou_fiscal(ambiente):
    _, chamadas, _ = ambiente
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    tenant = ["tenant-a"]
    app.dependency_overrides[service.common.get_tenant_id] = lambda: tenant[0]
    client = TestClient(app)
    url = "/api/medias-compras/listas-pedidos/lista-a/base-custos"
    resposta = client.get(url, params={"somente_revisao": "true"})
    assert resposta.status_code == 200
    assert resposta.json()["lista"]["store_id"] == "loja-a"
    assert chamadas == {"cadastro": 0, "fiscal": 0, "enriquecimento": 0}
    tenant[0] = "tenant-b"
    assert client.get(url).status_code == 404


def test_falha_fiscal_nao_persiste_resultado_incompleto(ambiente, monkeypatch):
    _, chamadas, _ = ambiente
    original = service._fiscal_lote
    monkeypatch.setattr(service, "_fiscal_lote", lambda *_: (_ for _ in ()).throw(RuntimeError("temporario")))
    with pytest.raises(RuntimeError):
        asyncio.run(service.api_importacoes_base_custos("lista-a", "tenant-a"))
    monkeypatch.setattr(service, "_fiscal_lote", original)
    resultado = asyncio.run(service.api_importacoes_base_custos("lista-a", "tenant-a"))
    assert all(row["fiscal_pronto"] for row in resultado["itens"])
    assert chamadas["cadastro"] == 2


def test_fontes_alteradas_durante_calculo_sao_recalculadas(ambiente, monkeypatch):
    estado, chamadas, _ = ambiente
    original = service._fiscal_lote

    def alterar(*args):
        if chamadas["fiscal"] == 0:
            estado["exchange_rate"] = 6.0
        return original(*args)

    monkeypatch.setattr(service, "_fiscal_lote", alterar)
    resultado = asyncio.run(service.api_importacoes_base_custos("lista-a", "tenant-a"))
    assert resultado["lista"]["exchange_rate"] == 6.0
    assert chamadas["fiscal"] == 2
    assert resultado["revisao"] == service.metadados_base_custos(service.capturar_base_custos("tenant-a", "lista-a"))["revisao"]


def test_rateio_pendente_e_historico_nao_inventa_cubagem(ambiente):
    estado, _, _ = ambiente
    estado["status"] = "Recebido"
    estado["itens"][0]["M3"] = .2
    estado["itens"][1]["Cubagem status"] = "pendente"
    resultado = asyncio.run(service.api_importacoes_base_custos("lista-a", "tenant-a"))
    assert resultado["rateio"]["total_m3"] == .2
    assert not resultado["rateio"]["m3_completo"]
    assert resultado["rateio"]["valor_por_m3"] is None


def test_margem_rejeita_revisao_obsoleta_e_preserva_metadados(ambiente, monkeypatch):
    estado, _, raiz = ambiente
    monkeypatch.setattr(listas, "_carregar_listas_pedidos", lambda _: [deepcopy(estado)])
    salvas = []
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", lambda tenant, rows: salvas.append(deepcopy(rows)))
    monkeypatch.setattr(listas, "_limpar_cache_lista_pedido", lambda *_, **__: None)
    monkeypatch.setattr(listas, "_resumo_lista_pedido", lambda row, **_: {"id": row["id"]})
    monkeypatch.setattr(listas, "_calcular_margens_concorrentes_promocoes", lambda *_, **__: {
        "concorrente_1": {"margem_percentual": 30.97, "custo_unitario": 23.75},
    })
    revisao = service.metadados_base_custos(service.capturar_base_custos("tenant-a", "lista-a"))["revisoes_sku"]["001"]
    req = ListaPedidoSkuAnaliseConcorrentesRequest(
        fonte_custo="simulacao_importacao_sku", custo_unitario=23.75,
        revisao_fontes_custo=revisao, assinatura_calculo="a" * 64,
        precos_concorrentes={"concorrente_1": 85.89}, anuncios_loja={"concorrente_1": "MLB11111111"},
    )
    resultado = listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync(
        "lista-a", "001", req, "tenant-a",
    )
    assert resultado["analise_concorrentes"]["revisao_fontes_custo"] == revisao
    assert resultado["analise_concorrentes"]["assinatura_calculo"] == "a" * 64
    assert len(salvas) == 1
    anterior = deepcopy(resultado["analise_concorrentes"])
    estado["itens"][0]["analise_simulacao_importacao_sku"] = deepcopy(anterior)

    def falha_temporaria(*_, **__):
        raise TimeoutError("serviço financeiro temporariamente indisponível")

    monkeypatch.setattr(listas, "_calcular_margens_concorrentes_promocoes", falha_temporaria)
    with pytest.raises(HTTPException) as erro:
        listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync("lista-a", "001", req, "tenant-a")
    assert erro.value.status_code == 503 and "temporariamente" in erro.value.detail
    assert len(salvas) == 1
    assert estado["itens"][0]["analise_simulacao_importacao_sku"] == anterior

    def mudar_fiscal(*_, **__):
        (raiz / "tenant-a" / "impostos_regras.json").write_text("[]", encoding="utf-8")
        return {}

    monkeypatch.setattr(listas, "_calcular_margens_concorrentes_promocoes", mudar_fiscal)
    with pytest.raises(HTTPException) as erro:
        listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync("lista-a", "001", req, "tenant-a")
    assert erro.value.status_code == 409 and len(salvas) == 1
    with pytest.raises(HTTPException) as erro:
        listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync("lista-a", "001", req, "tenant-a")
    assert erro.value.status_code == 409 and len(salvas) == 1


@pytest.mark.parametrize("assinatura_anterior,revisao_atual,preservar", [
    ("a", True, True), ("b", True, False), ("a", False, False),
])
def test_margens_proprias_so_sao_preservadas_no_mesmo_calculo(
    ambiente, monkeypatch, assinatura_anterior, revisao_atual, preservar,
):
    estado, _, _ = ambiente
    revisao = service.metadados_base_custos(service.capturar_base_custos("tenant-a", "lista-a"))["revisoes_sku"]["001"]
    propria = {"concorrente_1": {"item_id_loja": "MLB11111111", "store_id": "loja-a", "margem_percentual": 35}}
    estado["itens"][0]["analise_simulacao_importacao_sku"] = {
        "fonte_custo": "simulacao_importacao_sku", "custo_unitario": 23.75, "store_id": "loja-a",
        "revisao_fontes_custo": revisao if revisao_atual else "f" * 64,
        "assinatura_calculo": assinatura_anterior * 64, "margens_anuncios_loja": propria,
    }
    monkeypatch.setattr(listas, "_carregar_listas_pedidos", lambda _: [deepcopy(estado)])
    salvas = []
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", lambda tenant, rows: salvas.append(deepcopy(rows)))
    monkeypatch.setattr(listas, "_limpar_cache_lista_pedido", lambda *_, **__: None)
    monkeypatch.setattr(listas, "_resumo_lista_pedido", lambda row, **_: {"id": row["id"]})
    monkeypatch.setattr(listas, "_calcular_margens_concorrentes_promocoes", lambda *_, **__: {})
    req = ListaPedidoSkuAnaliseConcorrentesRequest(
        fonte_custo="simulacao_importacao_sku", custo_unitario=23.75, revisao_fontes_custo=revisao,
        assinatura_calculo="a" * 64, precos_concorrentes={"concorrente_1": 85.89},
        anuncios_loja={"concorrente_1": "MLB11111111"},
    )
    resultado = listas._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync("lista-a", "001", req, "tenant-a")
    assert resultado["analise_concorrentes"]["margens_anuncios_loja"] == (propria if preservar else {})
    assert len(salvas) == 1
