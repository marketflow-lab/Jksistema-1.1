import asyncio

import pytest

from backend.services import medias_compras_common as common
from backend.services import medias_compras_visao as visao


def _lista(lista_id, nome, status, loja, itens):
    return {
        "id": lista_id,
        "nome_lista": nome,
        "status": status,
        "loja": loja,
        "itens": itens,
    }


def test_importacao_entra_no_transito_e_soma_quantidade_por_lista(monkeypatch):
    listas = [
        _lista(
            "lista-a",
            "Importacao A",
            "Analisando orçamento",
            "JK Pecas",
            [
                {"SKU": "001", "Quantidade": 2},
                {"SKU": "001", "Quantidade": 3},
            ],
        ),
        _lista(
            "lista-b",
            "Importacao B",
            "Analisando orçamento",
            "JK Pecas",
            [{"SKU": "001", "Quantidade": 4}],
        ),
    ]
    monkeypatch.setattr(common, "_carregar_listas_pedidos", lambda _client_id: listas)

    detalhes = common._mapa_estoque_em_transito_detalhado_por_sku("tenant-a", "JK Pecas")
    totais = common._mapa_estoque_em_transito_por_sku("tenant-a", "JK Pecas")

    assert detalhes["001"]["total"] == pytest.approx(9)
    assert detalhes["001"]["listas"] == [
        {
            "lista_id": "lista-a",
            "nome_lista": "Importacao A",
            "quantidade": 5,
        },
        {
            "lista_id": "lista-b",
            "nome_lista": "Importacao B",
            "quantidade": 4,
        },
    ]
    assert totais == {"001": pytest.approx(9)}


def test_transito_respeita_loja_e_descarta_status_fora_do_fluxo(monkeypatch):
    listas = [
        _lista("selecionada", "Loja selecionada", "Analisando orçamento", "JK Pecas", [{"SKU": "10", "Quantidade": 2}]),
        _lista("geral", "Todas as lojas", "Em produção", "__todas", [{"SKU": "10", "Quantidade": 3}]),
        _lista("outra", "Outra loja", "Em trânsito", "Deckas", [{"SKU": "10", "Quantidade": 100}]),
        _lista("recebida", "Ja recebida", "Recebido", "JK Pecas", [{"SKU": "10", "Quantidade": 200}]),
        _lista("cancelada", "Cancelada", "Pedido cancelado", "JK Pecas", [{"SKU": "10", "Quantidade": 300}]),
    ]
    monkeypatch.setattr(common, "_carregar_listas_pedidos", lambda _client_id: listas)

    detalhes = common._mapa_estoque_em_transito_detalhado_por_sku("tenant-a", "JK Pecas")

    assert detalhes["10"]["total"] == pytest.approx(2)
    assert [item["nome_lista"] for item in detalhes["10"]["listas"]] == ["Loja selecionada"]


def test_transito_todas_as_lojas_agrega_listas_de_todas_as_lojas(monkeypatch):
    listas = [
        _lista("jk", "Lista JK", "Analisando orçamento", "JK Pecas", [{"SKU": "10", "Quantidade": 2}]),
        _lista("geral", "Lista geral", "Em produção", "__todas", [{"SKU": "10", "Quantidade": 3}]),
        _lista("deckas", "Lista Deckas", "Em trânsito", "Deckas", [{"SKU": "10", "Quantidade": 4}]),
    ]
    monkeypatch.setattr(common, "_carregar_listas_pedidos", lambda _client_id: listas)

    detalhes = common._mapa_estoque_em_transito_detalhado_por_sku("tenant-a", "__todas")

    assert detalhes["10"]["total"] == pytest.approx(9)
    assert [item["nome_lista"] for item in detalhes["10"]["listas"]] == [
        "Lista JK",
        "Lista geral",
        "Lista Deckas",
    ]


def test_visao_expoe_total_e_composicao_do_transito(monkeypatch):
    monkeypatch.setattr(visao, "_listar_bancos_vendas_tenant", lambda *_args: [], raising=False)
    monkeypatch.setattr(visao, "_migrar_arquivo_legado_para_tenant", lambda *_args: "", raising=False)
    monkeypatch.setattr(visao, "_cadastro_mapa_fotos_locais", lambda *_args: {}, raising=False)
    monkeypatch.setattr(visao, "_cadastro_resolver_foto_local", lambda *_args: "", raising=False)
    monkeypatch.setattr(visao, "ARQUIVO_DB_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "ARQUIVO_DB_CADASTRO_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "_mapa_ultima_venda_por_sku", lambda *_args: {})
    monkeypatch.setattr(
        visao,
        "_mapa_estoque_em_transito_detalhado_por_sku",
        lambda *_args: {
            "001": {
                "total": 9,
                "listas": [
                    {"lista_id": "a", "nome_lista": "Importacao A", "quantidade": 5},
                    {"lista_id": "b", "nome_lista": "Importacao B", "quantidade": 4},
                ],
            }
        },
    )

    resposta = asyncio.run(visao.api_medias_compras_visao(meses=3, loja="JK Pecas", client_id="tenant-a"))

    assert len(resposta["itens"]) == 1
    item = resposta["itens"][0]
    assert item["sku"] == "001"
    assert item["estoque_em_transito"] == pytest.approx(9)
    assert item["estoque_em_transito_listas"] == [
        {"lista_id": "a", "nome_lista": "Importacao A", "quantidade": 5.0},
        {"lista_id": "b", "nome_lista": "Importacao B", "quantidade": 4.0},
    ]
    assert resposta["resumo"]["estoque_total_transito"] == pytest.approx(9)
