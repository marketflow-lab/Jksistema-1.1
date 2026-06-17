"""Importacoes router definitions.

The endpoint implementations are still in backend_api.py while Importacoes
helpers are untangled. This module owns the route table so new Importacoes
routes can move here without keeping registration in the monolith.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from fastapi import APIRouter


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_IMPORTACOES_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/medias-compras/listas-pedidos", "api_medias_compras_listas_pedidos"),
    LegacyRouteSpec(
        "GET",
        "/api/medias-compras/listas-pedidos/preferencias-colunas",
        "api_medias_compras_preferencias_colunas_get",
    ),
    LegacyRouteSpec(
        "PUT",
        "/api/medias-compras/listas-pedidos/preferencias-colunas",
        "api_medias_compras_preferencias_colunas_put",
    ),
    LegacyRouteSpec("GET", "/api/medias-compras/concorrentes-links", "api_medias_compras_concorrentes_links"),
    LegacyRouteSpec(
        "POST",
        "/api/medias-compras/listas-pedidos/importar-excel",
        "api_medias_compras_lista_pedido_importar_excel_nova_lista",
    ),
    LegacyRouteSpec("GET", "/api/medias-compras/listas-pedidos/{lista_id}", "api_medias_compras_lista_pedido_detalhe"),
    LegacyRouteSpec("PUT", "/api/medias-compras/listas-pedidos/{lista_id}", "api_medias_compras_lista_pedido_editar"),
    LegacyRouteSpec(
        "POST",
        "/api/medias-compras/listas-pedidos/{lista_id}/adicionar-sku",
        "api_medias_compras_lista_pedido_adicionar_sku",
    ),
    LegacyRouteSpec(
        "PATCH",
        "/api/medias-compras/listas-pedidos/{lista_id}/status",
        "api_medias_compras_lista_pedido_atualizar_status",
    ),
    LegacyRouteSpec(
        "DELETE",
        "/api/medias-compras/listas-pedidos/{lista_id}",
        "api_medias_compras_lista_pedido_excluir",
    ),
    LegacyRouteSpec(
        "GET",
        "/api/medias-compras/listas-pedidos/{lista_id}/download",
        "api_medias_compras_lista_pedido_download",
    ),
    LegacyRouteSpec(
        "POST",
        "/api/medias-compras/listas-pedidos/{lista_id}/gerar-download",
        "api_medias_compras_lista_pedido_gerar_download",
    ),
    LegacyRouteSpec(
        "POST",
        "/api/medias-compras/listas-pedidos/{lista_id}/importar-excel-precos",
        "api_medias_compras_lista_pedido_importar_excel_precos",
    ),
)


router = APIRouter(tags=["importacoes"])


def create_importacoes_router(legacy_module: ModuleType) -> APIRouter:
    importacoes_router = APIRouter(tags=["importacoes"])

    for spec in LEGACY_IMPORTACOES_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        importacoes_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return importacoes_router
