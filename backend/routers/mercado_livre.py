"""Mercado Livre router definitions.

The endpoint implementations are still in backend_api.py while Mercado Livre
helpers are untangled. This module owns the route table so new Mercado Livre
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


LEGACY_MERCADO_LIVRE_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/api/mercadolivre/cache/invalidar", "ml_invalidar_cache"),
    LegacyRouteSpec("GET", "/api/mercadolivre/anuncios", "ml_listar_anuncios"),
    LegacyRouteSpec("GET", "/api/mercadolivre/anuncios/{item_id}/visitas", "ml_historico_visitas_anuncio"),
    LegacyRouteSpec("GET", "/api/mercadolivre/anuncios/{item_id}", "ml_buscar_anuncio"),
    LegacyRouteSpec("GET", "/api/mercadolivre/promocoes", "mercadolivre_listar_promocoes_ativas"),
    LegacyRouteSpec("GET", "/api/mercadolivre/promocoes/contagens", "mercadolivre_listar_promocoes_contagens"),
)


router = APIRouter(tags=["mercado-livre"])


def create_mercado_livre_router(legacy_module: ModuleType) -> APIRouter:
    mercado_livre_router = APIRouter(tags=["mercado-livre"])

    for spec in LEGACY_MERCADO_LIVRE_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        mercado_livre_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return mercado_livre_router
