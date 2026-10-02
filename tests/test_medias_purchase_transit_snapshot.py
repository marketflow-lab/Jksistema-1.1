from __future__ import annotations

import asyncio
import copy
import json

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import ListaCompraRequest
from backend.services import cadastro_compatibilidade
from backend.services import medias_compras_common as common
from backend.services import medias_compras_sugestoes as suggestions


@pytest.fixture
def transit_env(tmp_path, monkeypatch):
    tenant_path = lambda client: str(tmp_path / client)
    monkeypatch.setattr(common, "get_tenant_path", tenant_path)
    monkeypatch.setattr(suggestions, "get_tenant_path", tenant_path)
    rows = [
        {"id": "transit", "loja": "JK Pecas", "store_id": "store-jk", "status": "Em trânsito",
         "itens": [{"SKU": "001", "Quantidade": 5}]},
        {"id": "other", "loja": "Deckas", "store_id": "store-other", "status": "Em trânsito",
         "itens": [{"SKU": "001", "Quantidade": 100}]},
    ]
    common._salvar_listas_pedidos("tenant-a", rows)
    (tmp_path / "tenant-a/cadastro_fornecedores.json").write_text(
        json.dumps({"fornecedores": [{"id": "supplier-synthetic", "nome_empresa": "Synthetic supplier"}]}),
        encoding="utf-8",
    )
    stock = tmp_path / "tenant-a/products.csv"
    stock.write_text("sku,saldo_loja,loja_sync\n001,0,JK Pecas\n", encoding="utf-8")
    monkeypatch.setattr(suggestions, "_resolver_escopo_loja_medias", lambda *_a, **_kw: {
        "loja": "JK Pecas", "store_id": "store-jk", "scope": "store",
    })
    monkeypatch.setattr(suggestions, "ARQUIVO_DB_PRODUTOS", "products.csv", raising=False)
    monkeypatch.setattr(suggestions, "ARQUIVO_DB_CADASTRO_PRODUTOS", "cadastro_produtos.csv", raising=False)
    monkeypatch.setattr(suggestions, "_listar_bancos_vendas_tenant", lambda *_a: [], raising=False)
    monkeypatch.setattr(suggestions, "_migrar_arquivo_legado_para_tenant", lambda _c, name, _target: str(stock) if name == "produtos_compilado.csv" else "", raising=False)
    monkeypatch.setattr(suggestions, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
    monkeypatch.setattr(suggestions, "_salvar_listas_pedidos", common._salvar_listas_pedidos)
    monkeypatch.setattr(suggestions, "_mapa_estoque_em_transito_por_sku", common._mapa_estoque_em_transito_por_sku)
    monkeypatch.setattr(suggestions, "_gerar_excel_lista_pedido_bytes", lambda *_a, **_kw: b"synthetic-xlsx")
    monkeypatch.setattr(suggestions, "_salvar_bytes_cache_lista_pedido", lambda *_a, **_kw: None)
    monkeypatch.setattr(suggestions, "TEMP_FILES_STORAGE", {})
    monkeypatch.setattr(suggestions, "TEMP_FILES_META", {})
    monkeypatch.setattr(cadastro_compatibilidade, "mesclar_produtos_legados_com_contexto_loja", lambda *_a, **_kw: {
        "produtos": [], "store_id": "store-jk", "scope": "store", "loja_resolvida": True,
    })
    return rows


def _request():
    return ListaCompraRequest(
        opcao="media_6m", nome_lista="Synthetic purchase", loja="JK Pecas",
        store_id="store-jk", fornecedor_id="supplier-synthetic", periodo_meses=6,
    )


def test_transit_helpers_use_supplied_snapshot_without_rereading_or_mixing_store(transit_env, monkeypatch):
    snapshot = common._carregar_listas_pedidos("tenant-a")

    def forbidden(*args):
        pytest.fail("A supplied snapshot must be the only list input.")

    monkeypatch.setattr(common, "_carregar_listas_pedidos", forbidden)
    assert common._mapa_estoque_em_transito_por_sku("tenant-a", "JK Pecas", snapshot=snapshot) == {"001": 5}
    assert common._mapa_estoque_em_transito_por_sku("tenant-a", "Deckas", snapshot=snapshot) == {"001": 100}
    assert common._mapa_estoque_em_transito_por_sku("tenant-a", snapshot=[]) == {}
    with pytest.raises(common._ListaPedidoConflict):
        common._mapa_estoque_em_transito_por_sku("tenant-b", "JK Pecas", snapshot=snapshot)


@pytest.mark.parametrize("race", ["quantity", "status", "deletion"])
def test_purchase_recalculates_from_fresh_transit_after_concurrent_change(transit_env, monkeypatch, race):
    positions, calculations = [], []

    def calculate(**kwargs):
        positions.append(kwargs["estoque_em_transito"])
        return {"compra_sugerida": 30 - kwargs["estoque_em_transito"]}

    def normalize(client, items, **kwargs):
        calculations.append(1)
        if len(calculations) == 1:
            current = common._carregar_listas_pedidos(client)
            if race == "quantity":
                current[0]["itens"][0]["Quantidade"] = 9
            elif race == "status":
                current[0]["status"] = "Recebido"
            else:
                current.pop(0)
            common._salvar_listas_pedidos(client, current)
        return copy.deepcopy(items)

    monkeypatch.setattr(suggestions, "calcular_reposicao_periodo", calculate)
    monkeypatch.setattr(suggestions, "_recalcular_frete_internacional_itens_lista", normalize)
    result = asyncio.run(suggestions.api_medias_compras_gerar_lista_compra(_request(), client_id="tenant-a"))
    assert positions == [5, 9 if race == "quantity" else 0]
    assert len(calculations) == 2
    current = common._carregar_listas_pedidos("tenant-a")
    added = next(row for row in current if row["id"] == result["lista_id"])
    assert added["fornecedor_id"] == result["fornecedor_id"] == "supplier-synthetic"
    assert added["supplier"] == result["supplier"] == "Synthetic supplier"
    assert added["itens"][0]["Quantidade"] == (21 if race == "quantity" else 30)
    assert next(row for row in current if row["id"] == "other")["itens"][0]["Quantidade"] == 100


def test_purchase_returns_409_after_second_transit_conflict_without_appending(transit_env, monkeypatch):
    calculations, positions = [], []

    def calculate(**kwargs):
        positions.append(kwargs["estoque_em_transito"])
        return {"compra_sugerida": 30 - kwargs["estoque_em_transito"]}

    def normalize(client, items, **kwargs):
        calculations.append(1)
        current = common._carregar_listas_pedidos(client)
        current[0]["itens"][0]["Quantidade"] += 4
        common._salvar_listas_pedidos(client, current)
        return copy.deepcopy(items)

    monkeypatch.setattr(suggestions, "calcular_reposicao_periodo", calculate)
    monkeypatch.setattr(suggestions, "_recalcular_frete_internacional_itens_lista", normalize)
    with pytest.raises(HTTPException) as error:
        asyncio.run(suggestions.api_medias_compras_gerar_lista_compra(_request(), client_id="tenant-a"))
    assert error.value.status_code == 409 and len(calculations) == 2
    assert positions == [5, 9]
    current = common._carregar_listas_pedidos("tenant-a")
    assert [row["id"] for row in current] == ["transit", "other"]
    assert current[0]["itens"][0]["Quantidade"] == 13
    assert not suggestions.TEMP_FILES_STORAGE and not suggestions.TEMP_FILES_META
