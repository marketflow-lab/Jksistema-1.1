import asyncio
import copy

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import (
    ListaPedidoSkuAprovacaoRequest,
    ListaPedidoUpdateRequest,
)
from backend.routers.medias_compras import create_medias_compras_router
from backend.services import medias_compras_listas as listas_service


def _configurar_estado(monkeypatch, itens):
    estado = {
        "listas": [
            {
                "id": "lista-1",
                "nome_lista": "Pedido teste",
                "status": "Analisando orçamento",
                "itens": copy.deepcopy(itens),
            }
        ]
    }

    monkeypatch.setattr(
        listas_service,
        "_carregar_listas_pedidos",
        lambda _client_id: copy.deepcopy(estado["listas"]),
    )

    def salvar(_client_id, listas):
        estado["listas"] = copy.deepcopy(listas)

    monkeypatch.setattr(listas_service, "_salvar_listas_pedidos", salvar)
    monkeypatch.setattr(listas_service, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listas_service, "_recalcular_frete_internacional_itens_lista", lambda _client_id, itens: itens)
    monkeypatch.setattr(listas_service, "_resumo_lista_pedido", lambda lista, *_args: {"id": lista["id"]})
    monkeypatch.setattr(listas_service, "_normalizar_sku_mes", lambda sku: str(sku or "").strip().upper())
    return estado


def test_update_rejeita_exclusao_de_sku_aprovado(monkeypatch):
    _configurar_estado(
        monkeypatch,
        [
            {"SKU": "001", "Quantidade": 10, "compra_aprovada": True, "compra_aprovada_em": "2026-08-15T10:00:00"},
            {"SKU": "002", "Quantidade": 5},
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_editar(
                "lista-1",
                ListaPedidoUpdateRequest(itens=[{"SKU": "002", "Quantidade": 5}]),
                client_id="tenant-test",
            )
        )

    assert exc_info.value.status_code == 409
    assert "001" in exc_info.value.detail
    assert "Desfaca a aprovacao dentro do SKU" in exc_info.value.detail


def test_update_preserva_aprovacao_e_data_originais(monkeypatch):
    estado = _configurar_estado(
        monkeypatch,
        [{"SKU": "001", "Quantidade": 10, "compra_aprovada": True, "compra_aprovada_em": "2026-08-15T10:00:00"}],
    )

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_editar(
            "lista-1",
            ListaPedidoUpdateRequest(
                itens=[
                    {"SKU": "001", "Quantidade": 20, "compra_aprovada": True, "compra_aprovada_em": "alterada"}
                ]
            ),
            client_id="tenant-test",
        )
    )

    item = resultado["lista"]["itens"][0]
    assert item["Quantidade"] == 20
    assert item["compra_aprovada"] is True
    assert item["compra_aprovada_em"] == "2026-08-15T10:00:00"
    assert estado["listas"][0]["itens"][0] == item


def test_update_generico_nao_pode_desfazer_aprovacao(monkeypatch):
    _configurar_estado(
        monkeypatch,
        [{"SKU": "001", "Quantidade": 10, "compra_aprovada": True, "compra_aprovada_em": "2026-08-15T10:00:00"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_editar(
                "lista-1",
                ListaPedidoUpdateRequest(
                    itens=[{"SKU": "001", "Quantidade": 10, "compra_aprovada": False}]
                ),
                client_id="tenant-test",
            )
        )

    assert exc_info.value.status_code == 409
    assert "so pode ser desfeita dentro do SKU" in exc_info.value.detail


def test_endpoint_aprova_e_depois_desfaz_dentro_do_sku(monkeypatch):
    estado = _configurar_estado(monkeypatch, [{"SKU": "001", "Quantidade": 10}])

    aprovado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_aprovacao_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAprovacaoRequest(aprovada=True),
            client_id="tenant-test",
        )
    )
    assert aprovado["item"]["compra_aprovada"] is True
    assert aprovado["item"]["compra_aprovada_em"]
    assert estado["listas"][0]["itens"][0]["compra_aprovada"] is True

    desfeito = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_atualizar_aprovacao_sku(
            "lista-1",
            "001",
            ListaPedidoSkuAprovacaoRequest(aprovada=False),
            client_id="tenant-test",
        )
    )
    assert desfeito["item"]["compra_aprovada"] is False
    assert "compra_aprovada_em" not in desfeito["item"]
    assert estado["listas"][0]["itens"][0]["compra_aprovada"] is False


def test_router_expoe_endpoint_dedicado_de_aprovacao():
    rota = next(
        (
            rota
            for rota in create_medias_compras_router().routes
            if rota.path == "/api/medias-compras/listas-pedidos/{lista_id}/skus/{sku}/aprovacao"
        ),
        None,
    )

    assert rota is not None
    assert rota.methods == {"PATCH"}
