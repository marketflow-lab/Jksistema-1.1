import csv
import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.modules.perguntas_pos_venda.endpoints import training
from backend.services import cadastro_compatibilidade
from backend.services import cadastro_fotos
from backend.services import cadastro_lojas_produtos
from backend.services import ia_treinamento_ppv
from backend.services import integracoes
from backend.services import medias_compras_common
from backend.services import medias_compras_excel
from backend.services import medias_compras_fiscal
from backend.services import medias_compras_listas
from backend.schemas import ListaPedidoAddSkuRequest, ListaPedidoUpdateRequest


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _configure(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    tenant = info_root / "000002"
    tenant.mkdir(parents=True)
    stores = [
        {"store_id": "store-a", "nome": "Loja A", "integracoes": {}},
        {"store_id": "store-b", "nome": "Loja B", "integracoes": {}},
    ]

    def tenant_path(client_id):
        assert client_id == "000002"
        return str(tenant)

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info_root), raising=False)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client_id: stores)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: stores,
    )
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )

    refs = {}
    rows = []
    for store_id, conteudo in (("store-a", b"foto-a"), ("store-b", b"foto-b")):
        segmento = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
        ref = f"cadastro_fotos/lojas/{segmento}/001.png"
        photo = tenant / ref
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(conteudo)
        refs[store_id] = ref
        rows.append(
            {
                "store_id": store_id,
                "sku": "001",
                "sku_normalizado": "001",
                "nome": "Produto",
                "foto": ref,
                "row_version": "1",
                "updated_at_utc": "2026-09-02T12:00:00Z",
                "deleted_at_utc": "",
            }
        )
    _write_csv(tenant / "cadastro_produtos_lojas.csv", rows)
    return tenant, stores, refs


def test_contexto_de_leitura_resolve_loja_unica_e_falha_fechado_em_ambiguidade(
    monkeypatch,
    tmp_path,
):
    _tenant, stores, refs = _configure(monkeypatch, tmp_path)

    loja_a = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "000002",
        "loja a",
    )
    global_seguro = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "000002",
        "__todas",
    )

    assert loja_a["store_id"] == "store-a"
    assert loja_a["produtos"][0]["foto"] == refs["store-a"]
    assert global_seguro["produtos"][0]["foto"] == ""

    stores[1]["nome"] = "LOJA A"
    ambiguo = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "000002",
        "Loja A",
    )
    exato = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "000002",
        "store-a",
    )
    assert ambiguo["scope"] == "unresolved"
    assert ambiguo["produtos"] == []
    assert exato["store_id"] == "store-a"


def test_contexto_de_leitura_resolve_nome_historico_unico(monkeypatch, tmp_path):
    _tenant, stores, refs = _configure(monkeypatch, tmp_path)
    stores[0]["nome"] = "Loja A Renomeada"
    stores[0]["nomes_anteriores"] = ["Loja A"]

    legado = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        "000002",
        "Loja A",
    )

    assert legado["scope"] == "store"
    assert legado["store_id"] == "store-a"
    assert legado["produtos"][0]["foto"] == refs["store-a"]


def test_store_id_e_autoridade_e_nome_homonimo_nao_e_escolhido(
    monkeypatch, tmp_path
):
    _tenant, stores, _refs = _configure(monkeypatch, tmp_path)
    stores[0]["nomes_anteriores"] = ["Nome Compartilhado"]
    stores[1]["nome"] = "Nome Compartilhado"

    ambiguo = cadastro_compatibilidade.resolver_loja_ativa_para_leitura(
        "000002", "Nome Compartilhado"
    )
    exato = cadastro_compatibilidade.resolver_loja_ativa_para_leitura(
        "000002", "Nome Compartilhado", "store-a"
    )
    assert ambiguo["loja_resolvida"] is False
    assert exato == {
        "store_id": "store-a",
        "loja": "Loja A",
        "loja_resolvida": True,
        "scope": "store",
    }


def test_listas_medias_filtram_por_store_id_e_omitem_legado_ambiguo(
    monkeypatch, tmp_path
):
    _tenant, stores, _refs = _configure(monkeypatch, tmp_path)
    stores[1]["nome"] = "Loja A"
    listas = [
        {"id": "a", "loja": "Loja A", "store_id": "store-a", "itens": []},
        {"id": "b", "loja": "Loja A", "store_id": "store-b", "itens": []},
        {"id": "legada", "loja": "Loja A", "itens": []},
    ]
    monkeypatch.setattr(
        medias_compras_listas,
        "_carregar_listas_pedidos",
        lambda _client_id: listas,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_construir_mapa_m3_sku",
        lambda _client_id: {},
    )

    resposta = asyncio.run(
        medias_compras_listas.api_medias_compras_listas_pedidos(
            loja="Loja A",
            store_id="store-a",
            client_id="000002",
        )
    )
    assert [item["id"] for item in resposta["listas"]] == ["a"]


def test_ppv_propaga_store_id_exato_e_rejeita_nome_homonimo(
    monkeypatch, tmp_path
):
    _tenant, stores, _refs = _configure(monkeypatch, tmp_path)
    stores[1]["nome"] = "Loja A"
    escopos = []
    monkeypatch.setattr(
        training,
        "_ia_treinamento_ppv_listar_skus",
        lambda _client_id, escopo: escopos.append(escopo) or [],
    )

    resposta = training.ml_ia_treinamento_listar_skus(
        loja="Loja A",
        store_id="store-a",
        client_id="000002",
    )
    assert resposta["store_id"] == "store-a"
    assert escopos == ["store-a"]

    with pytest.raises(HTTPException) as exc_info:
        training.ml_ia_treinamento_listar_skus(
            loja="Loja A",
            client_id="000002",
        )
    assert getattr(exc_info.value, "status_code", None) == 409


def test_detalhe_de_lista_nao_apaga_foto_se_loja_legada_nao_resolver(
    monkeypatch,
    tmp_path,
):
    _tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    listas = [
        {
            "id": "lista-legada",
            "nome_lista": "Legada",
            "loja": "Loja que nao existe",
            "itens": [{"SKU": "001", "Foto": refs["store-a"]}],
        }
    ]
    salvas = []

    monkeypatch.setattr(
        medias_compras_listas,
        "_carregar_listas_pedidos",
        lambda _client_id: listas,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_salvar_listas_pedidos",
        lambda _client_id, novas: salvas.append(json.loads(json.dumps(novas))),
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_limpar_cache_lista_pedido",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_recalcular_frete_internacional_itens_lista",
        lambda _client_id, itens, **_kwargs: [
            {**dict(item), "Foto": "", "Frete internacional": 1}
            for item in itens
        ],
    )

    resposta = asyncio.run(
        medias_compras_listas.api_medias_compras_lista_pedido_detalhe(
            "lista-legada",
            client_id="000002",
        )
    )

    assert resposta["lista"]["itens"][0]["Foto"] == ""
    assert salvas[0][0]["itens"][0]["Foto"] == refs["store-a"]
    assert salvas[0][0]["itens"][0]["Frete internacional"] == 1


def test_detalhe_de_lista_legada_fixa_store_id_apos_renomear_loja(
    monkeypatch,
    tmp_path,
):
    _tenant, stores, refs = _configure(monkeypatch, tmp_path)
    stores[0]["nome"] = "Loja A Renomeada"
    stores[0]["nomes_anteriores"] = ["Loja A"]
    listas = [
        {
            "id": "lista-renomeada",
            "nome_lista": "Renomeada",
            "loja": "Loja A",
            "itens": [{"SKU": "001", "Foto": refs["store-a"]}],
        }
    ]
    salvas = []

    monkeypatch.setattr(
        medias_compras_listas,
        "_carregar_listas_pedidos",
        lambda _client_id: listas,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_salvar_listas_pedidos",
        lambda _client_id, novas: salvas.append(json.loads(json.dumps(novas))),
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_limpar_cache_lista_pedido",
        lambda *_args, **_kwargs: None,
    )

    def recalcular(_client_id, itens, *, loja=""):
        assert loja == "store-a"
        return [dict(item) for item in itens]

    monkeypatch.setattr(
        medias_compras_listas,
        "_recalcular_frete_internacional_itens_lista",
        recalcular,
    )

    resposta = asyncio.run(
        medias_compras_listas.api_medias_compras_lista_pedido_detalhe(
            "lista-renomeada",
            client_id="000002",
        )
    )

    assert resposta["lista"]["store_id"] == "store-a"
    assert salvas[0][0]["store_id"] == "store-a"
    assert salvas[0][0]["itens"][0]["Foto"] == refs["store-a"]


def test_resolvedores_de_compras_e_excel_nao_cruzam_fotos_de_loja(
    monkeypatch,
    tmp_path,
):
    tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(medias_compras_excel, "get_tenant_path", lambda _client_id: str(tenant))
    monkeypatch.setattr(medias_compras_excel, "PASTA_INFO", "", raising=False)

    assert medias_compras_common._resolver_foto_cadastro_sku(
        "000002",
        "001",
        "",
        "store-a",
    ) == refs["store-a"]
    assert medias_compras_common._resolver_foto_cadastro_sku(
        "000002",
        "001",
        refs["store-b"],
        "store-a",
    ) == ""
    assert medias_compras_excel._resolver_caminho_foto_cadastro_seguro(
        "000002",
        refs["store-a"],
        "001",
        "store-a",
    ) == str(tenant / refs["store-a"])
    assert medias_compras_excel._resolver_caminho_foto_cadastro_seguro(
        "000002",
        refs["store-b"],
        "001",
        "store-a",
    ) is None


def test_treinamento_sem_loja_usa_projecao_global_segura(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_migrar_arquivo_legado_para_tenant",
        lambda *_args: "",
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "ARQUIVO_DB_CADASTRO_PRODUTOS",
        "cadastro_produtos.csv",
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_cadastro_limpar_nome",
        lambda texto: texto,
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_normalizar_sku_mes",
        lambda sku: str(sku or "").strip().zfill(3),
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "logger",
        logging.getLogger("test-cadastro-photo-store-consumers"),
    )

    produtos = ia_treinamento_ppv._ia_treinamento_ppv_listar_skus("000002")
    produto = ia_treinamento_ppv._ia_treinamento_ppv_produto_por_sku(
        "000002",
        "001",
    )

    assert len(produtos) == 1
    assert produtos[0]["sku"] == "001"
    assert produtos[0]["foto"] == ""
    assert produto["sku"] == "001"
    assert "foto" not in produto


def test_treinamento_com_loja_usa_foto_do_escopo_exato(monkeypatch, tmp_path):
    _tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_migrar_arquivo_legado_para_tenant",
        lambda *_args: "",
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "ARQUIVO_DB_CADASTRO_PRODUTOS",
        "cadastro_produtos.csv",
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_cadastro_limpar_nome",
        lambda texto: texto,
        raising=False,
    )
    monkeypatch.setattr(
        ia_treinamento_ppv,
        "_normalizar_sku_mes",
        lambda sku: str(sku or "").strip().zfill(3),
        raising=False,
    )

    produtos = ia_treinamento_ppv._ia_treinamento_ppv_listar_skus(
        "000002",
        "Loja B",
    )
    produto = ia_treinamento_ppv._ia_treinamento_ppv_produto_por_sku(
        "000002",
        "001",
        "store-b",
    )

    assert produtos[0]["foto"] == refs["store-b"]
    assert produto["foto"] == refs["store-b"]


def test_compilado_nao_reintroduz_alias_de_foto_de_outra_loja(
    monkeypatch,
    tmp_path,
):
    _tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    registro = {
        "store_id": "store-a",
        "sku": "001",
        "sku_normalizado": "001",
        "foto": "",
        "row_version": "1",
        "updated_at_utc": "2026-09-02T12:00:00Z",
        "deleted_at_utc": "",
    }
    contexto = {
        "client_id": "000002",
        "fotos": {},
        "compilado": {
            "001": {
                "foto": refs["store-b"],
                "image_url": refs["store-b"],
            }
        },
        "custos": {},
    }

    produto = cadastro_lojas_produtos._enriquecer_produto(
        registro,
        {"store_id": "store-a", "nome": "Loja A"},
        contexto,
    )

    assert produto.get("foto", "") == ""
    assert produto.get("image_url", "") == ""


def test_inclusao_manual_em_lista_busca_foto_da_loja_selecionada(
    monkeypatch,
    tmp_path,
):
    _tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    listas = [
        {
            "id": "lista-1",
            "nome_lista": "Pedido",
            "loja": "Loja A",
            "itens": [],
        }
    ]

    async def produto_global(**_kwargs):
        return {
            "produto": {
                "sku": "001",
                "nome": "Produto global obsoleto",
                "foto": refs["store-b"],
            }
        }

    monkeypatch.setattr(
        medias_compras_listas,
        "_carregar_listas_pedidos",
        lambda _client_id: listas,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_salvar_listas_pedidos",
        lambda _client_id, novas: listas.__setitem__(slice(None), novas),
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_limpar_cache_lista_pedido",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "obter_produto_cadastro_query",
        produto_global,
        raising=False,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_recalcular_frete_internacional_itens_lista",
        lambda _client_id, itens, **_kwargs: itens,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_resumo_lista_pedido",
        lambda lista, *_args: {"id": lista["id"]},
    )

    resultado = asyncio.run(
        medias_compras_listas.api_medias_compras_lista_pedido_adicionar_sku(
            "lista-1",
            ListaPedidoAddSkuRequest(
                sku="001",
                quantidade=2,
                valor_unitario=10,
            ),
            client_id="000002",
        )
    )

    assert resultado["acao"] == "adicionado"
    assert listas[0]["itens"][0]["Foto"] == refs["store-a"]


def test_troca_de_loja_reidrata_foto_no_mesmo_commit(monkeypatch, tmp_path):
    _tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    listas = [
        {
            "id": "lista-1",
            "nome_lista": "Pedido",
            "loja": "Loja A",
            "store_id": "store-a",
            "itens": [{"SKU": "001", "Foto": "https://cdn.example/loja-a.png"}],
        }
    ]

    monkeypatch.setattr(
        medias_compras_listas,
        "_carregar_listas_pedidos",
        lambda _client_id: listas,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_salvar_listas_pedidos",
        lambda _client_id, novas: listas.__setitem__(slice(None), novas),
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "_limpar_cache_lista_pedido",
        lambda *_args, **_kwargs: None,
    )

    def recalcular(_client_id, itens, *, loja=""):
        assert loja == "store-b"
        return [{**dict(item), "Foto": refs["store-b"]} for item in itens]

    monkeypatch.setattr(
        medias_compras_listas,
        "_recalcular_frete_internacional_itens_lista",
        recalcular,
    )

    resposta = asyncio.run(
        medias_compras_listas.api_medias_compras_lista_pedido_editar(
            "lista-1",
            ListaPedidoUpdateRequest(loja="Loja B"),
            client_id="000002",
        )
    )

    assert listas[0]["store_id"] == "store-b"
    assert listas[0]["itens"][0]["Foto"] == refs["store-b"]
    assert resposta["lista"]["itens"][0]["Foto"] == refs["store-b"]


def test_fiscal_prioriza_foto_contextual_e_preserva_metadados_globais(
    monkeypatch,
    tmp_path,
):
    tenant, _stores, refs = _configure(monkeypatch, tmp_path)
    cadastro_global = tenant / "cadastro_produtos.csv"
    _write_csv(
        cadastro_global,
        [
            {
                "sku": "001",
                "foto": "https://cdn.global.test/foto-loja-a.png",
                "ncm": "85365090",
                "oem": "OEM-001",
                "link": "https://fornecedor.test/produto-001",
            }
        ],
    )
    df_global = medias_compras_fiscal.pd.read_csv(
        cadastro_global,
        dtype=str,
    ).fillna("")
    monkeypatch.setattr(
        medias_compras_fiscal,
        "_carregar_cadastro_para_impostos",
        lambda _client_id: (df_global, str(cadastro_global)),
    )
    monkeypatch.setattr(
        medias_compras_fiscal,
        "_caminhos_cadastros_meta",
        lambda _client_id: [str(cadastro_global)],
    )
    monkeypatch.setattr(
        medias_compras_fiscal,
        "_indice_ncm_referencia_aliquotas",
        lambda _client_id: {},
    )
    monkeypatch.setattr(
        medias_compras_fiscal,
        "_indice_ncm_referencia_por_ncm",
        lambda _client_id: {},
    )

    itens = medias_compras_fiscal._recalcular_frete_internacional_itens_lista(
        "000002",
        [
            {
                "SKU": "001",
                "Foto": "https://cdn.global.test/foto-obsoleta.png",
                "Quantidade": 1,
                "Valor unidade": 10,
            }
        ],
        loja="Loja B",
    )

    assert itens[0]["Foto"] == refs["store-b"]
    assert itens[0]["NCM"] == "85365090"
    assert itens[0]["OEM"] == "OEM-001"
    assert itens[0]["Link"] == "https://fornecedor.test/produto-001"
