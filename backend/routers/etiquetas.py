"""Etiquetas router definitions.

The endpoint implementations are still in backend_api.py while Etiquetas
helpers are untangled. This module owns the route table so new Etiquetas routes
can move here without keeping registration in the monolith.
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


LEGACY_ETIQUETAS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/api/etiquetas/impressao-editor", "gerar_impressao_editor_endpoint"),
    LegacyRouteSpec("POST", "/api/etiquetas/impressao-avulsa", "gerar_impressao_avulsa_endpoint"),
    LegacyRouteSpec("POST", "/api/etiquetas/qrcode-link", "gerar_qrcode_link_endpoint"),
    LegacyRouteSpec("POST", "/api/etiquetas/processar", "processar_etiquetas_endpoint"),
    LegacyRouteSpec("GET", "/api/etiquetas/download/{file_id}", "download_etiqueta"),
    LegacyRouteSpec("GET", "/api/etiquetas/preview/{file_id}", "preview_etiqueta"),
)


router = APIRouter(tags=["etiquetas"])


def create_etiquetas_router(legacy_module: ModuleType) -> APIRouter:
    etiquetas_router = APIRouter(tags=["etiquetas"])

    for spec in LEGACY_ETIQUETAS_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        etiquetas_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return etiquetas_router
