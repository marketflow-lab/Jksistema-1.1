import asyncio
from copy import deepcopy

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import importacoes_sku_detalhe as service


def _item(sku, qtd, valor):
    return {"SKU": sku, "Quantidade": qtd, "Valor unidade": valor, "Valor total": qtd * valor}


def _produto(sku, m3, loja="loja-a"):
    return {"sku": sku, "store_id": loja, "m3 individual": m3, "custo": 10.0, "nome": "Peça"}


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    estado = {
        "id": "lista-a", "nome_lista": "Pedido teste", "status": "Analisando orçamento",
        "loja": "Loja A", "store_id": "loja-a", "exchange_rate": 5.35,
        "itens": [_item("001", 4, 1.125), _item("002", 2, 4.0), _item("003", 0, 2.0)],
    }
    chamadas = []
    catalogo = {"001": _produto("001", 0.1), "002": _produto("002", 0.3), "003": _produto("003", 0.5)}

    def buscar(client_id, loja, *, skus):
        chamadas.append((client_id, loja, tuple(skus)))
        return {"scope": "store", "store_id": loja, "produtos": [catalogo[sku] for sku in skus if sku in catalogo]}

    monkeypatch.setattr(service.common, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(service.common, "_carregar_listas_pedidos",
                        lambda tenant: [deepcopy(estado)] if tenant == "tenant-a" else [])
    monkeypatch.setattr(service, "_rateios", service.ResultadoCache())
    monkeypatch.setattr(service.cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja", buscar)
    monkeypatch.setattr(service, "_enriquecer_itens_lista_pedido_com_impostos", lambda _client, itens, **_: itens)
    monkeypatch.setattr(service, "_impostos_sku", lambda _client, sku: {"sku": sku, "convenio_mg_st_minas": "SIM"})
    monkeypatch.setattr(service, "_identidade_anuncios", lambda *_: None)
    (tmp_path / "tenant-a").mkdir()
    (tmp_path / "tenant-b").mkdir()
    return estado, chamadas, catalogo, tmp_path


def test_detalhe_compacto_reusa_rateio_entre_skus_e_invalida_somente_entradas(ambiente):
    estado, chamadas, catalogo, tmp_path = ambiente

    def chamadas_lote():
        return [chamada for chamada in chamadas if len(chamada[2]) > 1]

    async def ler(sku):
        return await service.api_importacoes_sku_detalhe("lista-a", sku, "tenant-a")

    async def conferir():
        primeiro, segundo = await asyncio.gather(ler("001"), ler("002"))
        assert primeiro["item"]["SKU"] == "001" and segundo["item"]["SKU"] == "002"
        assert "itens" not in primeiro["lista"] and "por_sku" not in primeiro
        assert primeiro["cadastro"]["custo"] == 10.0
        assert primeiro["fiscal"] == {"sku": "001", "convenio_mg_st_minas": "SIM"}
        assert primeiro["item"]["M3"] == pytest.approx(0.4)
        assert primeiro["rateio"] == segundo["rateio"] == {
            "total_usd": 12.5, "total_m3": pytest.approx(1.0), "m3_completo": True,
            "valor_por_m3": pytest.approx(12.5),
        }
        assert chamadas_lote() == [("tenant-a", "loja-a", ("001", "002"))]

        estado["updated_at"] = "2026-11-04T10:00:00"
        estado["itens"][0]["analise_simulacao_importacao_sku"] = {"custo_unitario": 18}
        reaberto = await ler("001")
        assert reaberto["item"]["analise_simulacao_importacao_sku"]["custo_unitario"] == 18
        assert reaberto["revisao"] == primeiro["revisao"]
        assert len(chamadas_lote()) == 1

        estado["itens"][1]["Valor total"] = 12
        alterado = await ler("001")
        assert alterado["rateio"]["valor_por_m3"] == pytest.approx(16.5)
        assert alterado["revisao"] != primeiro["revisao"]
        assert len(chamadas_lote()) == 2

        catalogo["002"]["m3 individual"] = 0.8
        (tmp_path / "tenant-a" / "cadastro_produtos_lojas.csv").write_text("revisao nova", encoding="utf-8")
        recubado = await ler("002")
        assert recubado["rateio"]["total_m3"] == pytest.approx(2.0)
        assert recubado["item"]["M3"] == pytest.approx(1.6)
        assert len(chamadas_lote()) == 3

    asyncio.run(conferir())


def test_rota_compacta_salva_respeita_tenant_e_sku_exato(ambiente):
    _estado, chamadas, _catalogo, _tmp_path = ambiente
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    tenant = ["tenant-a"]
    app.dependency_overrides[service.common.get_tenant_id] = lambda: tenant[0]
    client = TestClient(app)
    base = "/api/medias-compras/listas-pedidos/lista-a/skus"

    resposta = client.get(f"{base}/001", params={"somente_salvos": "true"})
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["item"]["SKU"] == "001" and dados["cadastro"] is None
    assert not dados["fiscal_pronto"] and chamadas == []
    assert client.get(f"{base}/1", params={"somente_salvos": "true"}).status_code == 404
    tenant[0] = "tenant-b"
    assert client.get(f"{base}/001").status_code == 404


def test_validacao_leve_da_margem_confere_fontes_sem_reprocessar_cadastro(ambiente, monkeypatch):
    estado, chamadas, _catalogo, tmp_path = ambiente
    identidade = {"seller_id": "seller-a", "site_id": "MLB", "fontes_versao": "planilha-a"}
    monkeypatch.setattr(service, "_identidade_anuncios", lambda *_: dict(identidade))
    monkeypatch.setattr(service.common, "_resolver_escopo_loja_medias", lambda *_: {
        "scope": "store", "store_id": "loja-a", "loja": "Loja A",
    })

    async def conferir():
        completo = await service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a")
        selo = completo["revisao_fontes_margem"]
        chamadas.clear()
        estado["itens"][0]["analise_simulacao_importacao_sku"] = {"custo_unitario": 18}
        validado = await service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a", validar_margem=True)
        assert validado == {"revisao_fontes_margem": selo, "anuncios_identidade": identidade}
        assert chamadas == []

        identidade["seller_id"] = "seller-b"
        nova_conta = await service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a", validar_margem=True)
        assert nova_conta["anuncios_identidade"]["seller_id"] == "seller-b"

        (tmp_path / "tenant-a" / "impostos_regras.json").write_text("[]", encoding="utf-8")
        novo_fiscal = await service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a", validar_margem=True)
        assert novo_fiscal["revisao_fontes_margem"] != selo
        estado["exchange_rate"] = 5.0
        novo_custo = await service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a", validar_margem=True)
        assert novo_custo["revisao_fontes_margem"] != novo_fiscal["revisao_fontes_margem"]
        assert chamadas == []

    asyncio.run(conferir())


def test_rateio_historico_preserva_medida_e_cubagem_pendente(ambiente):
    estado, chamadas, _catalogo, _tmp_path = ambiente
    estado["status"] = "Recebido"
    estado["itens"][0]["M3"] = 0.2
    estado["itens"][1]["Cubagem status"] = "pendente"

    resultado = asyncio.run(service.api_importacoes_sku_detalhe("lista-a", "001", "tenant-a"))

    assert resultado["item"]["Cubagem status"] == "historico"
    assert resultado["item"]["M3"] == pytest.approx(0.2)
    assert resultado["rateio"]["total_m3"] == pytest.approx(0.2)
    assert resultado["rateio"]["valor_por_m3"] is None
    assert not resultado["rateio"]["m3_completo"]
    assert chamadas == [("tenant-a", "loja-a", ("001",))]


def test_fiscal_filtra_antes_de_classificar_e_mantem_sku_exato(monkeypatch, tmp_path):
    fiscal = service.impostos_regras
    (tmp_path / "cadastro_produtos.csv").touch()
    monkeypatch.setattr(fiscal, "get_tenant_path", lambda _: str(tmp_path))
    def migracao_indevida(*_):
        pytest.fail("A consulta fiscal por SKU não deve migrar o legado.")
    monkeypatch.setattr(fiscal, "_migrar_arquivo_legado_para_tenant", migracao_indevida, raising=False)
    monkeypatch.setattr(fiscal, "ler_csv", lambda *args, **kw: pd.DataFrame([
        {"sku": "A-1", "nome": "Alias", "ncm": "1"},
        {"sku": "A-JAN", "nome": "Exato", "ncm": "2"},
        {"sku": "Outro", "nome": "Não solicitado", "ncm": "3"},
    ]))
    monkeypatch.setattr(fiscal, "_carregar_regras_impostos", lambda _: [])
    monkeypatch.setattr(fiscal, "_indice_ncm_referencia_por_ncm", lambda _: {}, raising=False)
    monkeypatch.setattr(fiscal, "_carregar_convenio_mg_referencia", lambda _: [], raising=False)
    monkeypatch.setattr(fiscal, "_resumir_convenio_mg_para_sku",
                        lambda ncm, *_args: {"convenio_mg_mva": ncm}, raising=False)

    def classificar(df, _):
        assert df["sku"].tolist() == ["A-1", "A-JAN"]
        return df

    monkeypatch.setattr(fiscal, "_aplicar_classificacao_fiscal_fallback", classificar)
    assert service._impostos_sku("tenant-a", "A-JAN")["convenio_mg_mva"] == "2"
