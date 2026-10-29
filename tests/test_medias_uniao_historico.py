import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import (
    ListaPedidoAddSkuRequest, ListaPedidoSkuAnaliseConcorrentesRequest,
    ListaPedidoSkuAprovacaoRequest, ListaPedidoStatusRequest, ListaPedidoUpdateRequest,
)
from backend.services import medias_compras_common as common
from backend.services import medias_compras_importacao as imports
from backend.services import medias_compras_listas as lists


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "_arquivo_listas_pedidos", lambda client: str(tmp_path / client / "listas_pedidos.json"))
    original = {
        "id": "original", "nome_lista": "Original", "loja": "Loja A", "store_id": "store-a",
        "status": "Analisando orçamento", "unida_em_lista_id": "unificada",
        "unida_em_lista_nome": "Unificada", "unida_em": "2026-10-29T12:00:00",
        "itens": [{"SKU": "001", "Quantidade": 5, "Valor unidade": 4, "Valor total": 20,
                   "M3 individual": 0.1, "Estimed CBM": 0.5, "Link": "https://example.test/original"}],
    }
    unified = {"id": "unificada", "nome_lista": "Unificada", "loja": "Loja A", "status": "Lista gerada",
               "uniao_origens": [{"id": "original", "nome_lista": "Original"}],
               "itens": [{"SKU": "001", "Quantidade": 8}]}
    common._salvar_listas_pedidos("tenant-a", [original, unified, {"id": "outra", "itens": []}])
    return copy.deepcopy(original)


@pytest.mark.parametrize("action", ["item", "status", "remover_marca", "excluir", "excluir_destino"])
@pytest.mark.parametrize("snapshot", [True, False])
def test_historico_imutavel_no_commit_inclusive_escrita_legada(history, action, snapshot):
    before = common._carregar_listas_pedidos("tenant-a")
    proposed = common._carregar_listas_pedidos("tenant-a")
    if not snapshot:
        proposed = list(proposed)
    if action == "item":
        proposed[0]["itens"][0]["Quantidade"] = 999
    elif action == "status":
        proposed[0]["status"] = "Em trânsito"
    elif action == "remover_marca":
        proposed[0].pop("unida_em_lista_id")
    else:
        proposed.pop(0 if action == "excluir" else 1)
    with pytest.raises(HTTPException) as error:
        common._salvar_listas_pedidos("tenant-a", proposed)
    assert error.value.status_code == 409
    assert common._carregar_listas_pedidos("tenant-a") == before


def test_outras_listas_continuam_editaveis_e_destino_pode_ser_cancelado(history):
    first = common._carregar_listas_pedidos("tenant-a")
    second = common._carregar_listas_pedidos("tenant-a")
    first[1]["status"] = "Pedido cancelado"
    second[2]["nome_lista"] = "Outro pedido"
    common._salvar_listas_pedidos("tenant-a", first)
    common._salvar_listas_pedidos("tenant-a", second)
    current = common._carregar_listas_pedidos("tenant-a")
    assert current[0] == history
    assert current[1]["status"] == "Pedido cancelado"
    assert current[2]["nome_lista"] == "Outro pedido"


def test_historico_excluido_do_transito_e_destino_entra_uma_vez(history):
    assert common._mapa_estoque_em_transito_por_sku("tenant-a") == {}
    rows = common._carregar_listas_pedidos("tenant-a")
    rows[1]["status"] = "Analisando orçamento"
    common._salvar_listas_pedidos("tenant-a", rows)
    assert common._mapa_estoque_em_transito_por_sku("tenant-a") == {"001": 8}
    assert common._mapa_estoque_em_transito_detalhado_por_sku("tenant-a")["001"]["listas"] == [
        {"lista_id": "unificada", "nome_lista": "Unificada", "quantidade": 8},
    ]


@pytest.mark.parametrize("saved_only", [False, True])
def test_consulta_preserva_itens_e_cubagem_do_historico(history, monkeypatch, saved_only):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Historico unido nao deve fazer backfill nem reconsultar catalogo")
    monkeypatch.setattr(lists, "_salvar_listas_pedidos", forbidden)
    monkeypatch.setattr(lists, "_itens_salvos_lista_pedido_no_escopo", forbidden)
    monkeypatch.setattr(lists, "_recalcular_frete_internacional_itens_lista", forbidden)
    response = lists._api_medias_compras_lista_pedido_detalhe_sync(
        "original", client_id="tenant-a", somente_salvos=saved_only,
    )["lista"]
    assert response["itens"] == history["itens"]
    assert response["unida_em_lista_id"] == "unificada"
    assert response["pode_unir"] is False
    assert response["total_m3"] == pytest.approx(0.5)
    assert common._carregar_listas_pedidos("tenant-a")[0] == history
    assert common._resumo_lista_pedido(history, {"001": 999})["total_m3"] == pytest.approx(0.5)
    assert lists._lista_pedido_com_links_atuais(history, "tenant-a") == history


@pytest.mark.parametrize("operation", ["edit", "status", "approve", "analysis", "add", "delete", "import"])
def test_endpoints_recusam_mutacao_de_origem_antes_do_trabalho(history, operation, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Mutacao de historico nao pode chegar a persistencia ou catalogo")
    monkeypatch.setattr(lists, "_salvar_listas_pedidos", forbidden)
    monkeypatch.setattr(lists, "_recalcular_frete_internacional_itens_lista", forbidden)
    actions = {
        "edit": lambda: lists._api_medias_compras_lista_pedido_editar_sync("original", ListaPedidoUpdateRequest(nome_lista="Alterada"), client_id="tenant-a"),
        "status": lambda: lists._api_medias_compras_lista_pedido_atualizar_status_sync("original", ListaPedidoStatusRequest(status="Em trânsito"), client_id="tenant-a"),
        "approve": lambda: lists._api_medias_compras_lista_pedido_atualizar_aprovacao_sku_sync("original", "001", ListaPedidoSkuAprovacaoRequest(aprovada=True), client_id="tenant-a"),
        "analysis": lambda: lists._api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync("original", "001", ListaPedidoSkuAnaliseConcorrentesRequest(), client_id="tenant-a"),
        "add": lambda: lists._api_medias_compras_lista_pedido_adicionar_sku_sync("original", ListaPedidoAddSkuRequest(sku="002", quantidade=1, valor_unitario=1), client_id="tenant-a"),
        "delete": lambda: lists._api_medias_compras_lista_pedido_excluir_sync("original", SimpleNamespace(headers={}), client_id="tenant-a"),
        "import": lambda: imports._api_medias_compras_lista_pedido_importar_excel_precos_sync("original", b"invalid-xlsx", client_id="tenant-a"),
    }
    with pytest.raises(HTTPException) as error:
        actions[operation]()
    assert error.value.status_code == 409
    assert "consulta" in error.value.detail
    assert common._carregar_listas_pedidos("tenant-a")[0] == history
