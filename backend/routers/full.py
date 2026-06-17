"""Full router definitions.

The endpoint implementations are still in backend_api.py while Full helpers are
untangled. This module owns the route table so new Full routes can move here
without keeping registration in the monolith.
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


LEGACY_FULL_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/full/estoque", "listar_estoque_full"),
    LegacyRouteSpec("GET", "/api/full/lojas-mercadolivre", "listar_lojas_full_mercadolivre"),
    LegacyRouteSpec("GET", "/api/full/anuncios", "listar_anuncios_full_mercadolivre"),
    LegacyRouteSpec("GET", "/api/full/calendario-comercial", "calendario_comercial_full"),
    LegacyRouteSpec("GET", "/api/full/envios-transito", "listar_full_envios_transito"),
    LegacyRouteSpec("POST", "/api/full/envios-transito/upload", "upload_full_envios_transito"),
    LegacyRouteSpec("POST", "/api/full/envios-transito/manual", "criar_full_envio_transito_manual"),
    LegacyRouteSpec("PATCH", "/api/full/envios-transito/{envio_id}", "atualizar_full_envio_transito"),
    LegacyRouteSpec("DELETE", "/api/full/envios-transito/{envio_id}", "excluir_full_envio_transito"),
    LegacyRouteSpec("GET", "/api/full/envios-transito/{envio_id}/pdf", "baixar_pdf_full_envio_transito"),
)


router = APIRouter(tags=["full"])


def create_full_router(legacy_module: ModuleType) -> APIRouter:
    full_router = APIRouter(tags=["full"])

    for spec in LEGACY_FULL_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        full_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return full_router
