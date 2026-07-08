"""Medias Compras router definitions."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import medias_compras


router = APIRouter(tags=["medias-compras"])


def create_medias_compras_router() -> APIRouter:
    medias_router = APIRouter(tags=["medias-compras"])

    medias_router.add_api_route(
        "/api/medias-compras/calcular",
        medias_compras.api_medias_compras_calcular,
        methods=["POST"],
        name="api_medias_compras_calcular",
    )
    medias_router.add_api_route(
        "/api/medias-compras/visao",
        medias_compras.api_medias_compras_visao,
        methods=["GET"],
        name="api_medias_compras_visao",
    )
    medias_router.add_api_route(
        "/api/medias-compras/gerar-lista-compra",
        medias_compras.api_medias_compras_gerar_lista_compra,
        methods=["POST"],
        name="api_medias_compras_gerar_lista_compra",
    )
    medias_router.add_api_route(
        "/api/medias-compras/gerar-lista-compra",
        medias_compras.api_medias_compras_gerar_lista_compra_get,
        methods=["GET"],
        name="api_medias_compras_gerar_lista_compra_get",
    )
    medias_router.add_api_route(
        "/api/medias-compras/gerar-lista-sugestao",
        medias_compras.api_medias_compras_gerar_lista_sugestao,
        methods=["GET"],
        name="api_medias_compras_gerar_lista_sugestao",
    )
    medias_router.add_api_route(
        "/api/medias-compras/produtos-sem-venda",
        medias_compras.api_medias_compras_produtos_sem_venda,
        methods=["GET"],
        name="api_medias_compras_produtos_sem_venda",
    )
    medias_router.add_api_route(
        "/api/medias-compras/preferencias-skus-ocultos",
        medias_compras.api_medias_compras_skus_ocultos_get,
        methods=["GET"],
        name="api_medias_compras_skus_ocultos_get",
    )
    medias_router.add_api_route(
        "/api/medias-compras/preferencias-skus-ocultos",
        medias_compras.api_medias_compras_skus_ocultos_put,
        methods=["PUT"],
        name="api_medias_compras_skus_ocultos_put",
    )
    medias_router.add_api_route(
        "/api/medias-compras/download/{file_id}",
        medias_compras.api_medias_compras_download,
        methods=["GET"],
        name="api_medias_compras_download",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos",
        medias_compras.api_medias_compras_listas_pedidos,
        methods=["GET"],
        name="api_medias_compras_listas_pedidos",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/preferencias-colunas",
        medias_compras.api_medias_compras_preferencias_colunas_get,
        methods=["GET"],
        name="api_medias_compras_preferencias_colunas_get",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/preferencias-colunas",
        medias_compras.api_medias_compras_preferencias_colunas_put,
        methods=["PUT"],
        name="api_medias_compras_preferencias_colunas_put",
    )
    medias_router.add_api_route(
        "/api/medias-compras/concorrentes-links",
        medias_compras.api_medias_compras_concorrentes_links,
        methods=["GET"],
        name="api_medias_compras_concorrentes_links",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/importar-excel",
        medias_compras.api_medias_compras_lista_pedido_importar_excel_nova_lista,
        methods=["POST"],
        name="api_medias_compras_lista_pedido_importar_excel_nova_lista",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}",
        medias_compras.api_medias_compras_lista_pedido_detalhe,
        methods=["GET"],
        name="api_medias_compras_lista_pedido_detalhe",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}",
        medias_compras.api_medias_compras_lista_pedido_editar,
        methods=["PUT"],
        name="api_medias_compras_lista_pedido_editar",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}/adicionar-sku",
        medias_compras.api_medias_compras_lista_pedido_adicionar_sku,
        methods=["POST"],
        name="api_medias_compras_lista_pedido_adicionar_sku",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}/status",
        medias_compras.api_medias_compras_lista_pedido_atualizar_status,
        methods=["PATCH"],
        name="api_medias_compras_lista_pedido_atualizar_status",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}",
        medias_compras.api_medias_compras_lista_pedido_excluir,
        methods=["DELETE"],
        name="api_medias_compras_lista_pedido_excluir",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}/download",
        medias_compras.api_medias_compras_lista_pedido_download,
        methods=["GET"],
        name="api_medias_compras_lista_pedido_download",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}/gerar-download",
        medias_compras.api_medias_compras_lista_pedido_gerar_download,
        methods=["POST"],
        name="api_medias_compras_lista_pedido_gerar_download",
    )
    medias_router.add_api_route(
        "/api/medias-compras/listas-pedidos/{lista_id}/importar-excel-precos",
        medias_compras.api_medias_compras_lista_pedido_importar_excel_precos,
        methods=["POST"],
        name="api_medias_compras_lista_pedido_importar_excel_precos",
    )

    return medias_router


__all__ = ["create_medias_compras_router"]
