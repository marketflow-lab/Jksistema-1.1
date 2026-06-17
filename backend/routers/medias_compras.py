"""Medias Compras router definitions.

The endpoint implementations are still in backend_api.py while Medias Compras
helpers are untangled. This module owns the route table so new Medias Compras
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


LEGACY_MEDIAS_COMPRAS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/api/medias-compras/calcular", "api_medias_compras_calcular"),
    LegacyRouteSpec("GET", "/api/medias-compras/visao", "api_medias_compras_visao"),
    LegacyRouteSpec("POST", "/api/medias-compras/gerar-lista-compra", "api_medias_compras_gerar_lista_compra"),
    LegacyRouteSpec("GET", "/api/medias-compras/gerar-lista-compra", "api_medias_compras_gerar_lista_compra_get"),
    LegacyRouteSpec("GET", "/api/medias-compras/gerar-lista-sugestao", "api_medias_compras_gerar_lista_sugestao"),
    LegacyRouteSpec("GET", "/api/medias-compras/produtos-sem-venda", "api_medias_compras_produtos_sem_venda"),
    LegacyRouteSpec("GET", "/api/medias-compras/preferencias-skus-ocultos", "api_medias_compras_skus_ocultos_get"),
    LegacyRouteSpec("PUT", "/api/medias-compras/preferencias-skus-ocultos", "api_medias_compras_skus_ocultos_put"),
    LegacyRouteSpec("GET", "/api/medias-compras/download/{file_id}", "api_medias_compras_download"),
)


router = APIRouter(tags=["medias-compras"])


def create_medias_compras_router(legacy_module: ModuleType) -> APIRouter:
    medias_compras_router = APIRouter(tags=["medias-compras"])

    for spec in LEGACY_MEDIAS_COMPRAS_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        medias_compras_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return medias_compras_router
