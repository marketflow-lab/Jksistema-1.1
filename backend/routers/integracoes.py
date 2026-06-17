"""Integracoes router definitions.

The endpoint implementations are still in backend_api.py while Integracoes
helpers are untangled. This module owns the route table so new Integracoes
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


LEGACY_INTEGRACOES_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/lojas", "get_lojas"),
    LegacyRouteSpec("GET", "/api/lojas/{nome_loja}", "get_loja"),
    LegacyRouteSpec("POST", "/api/lojas", "create_loja"),
    LegacyRouteSpec("DELETE", "/api/lojas/{nome_loja}", "delete_loja"),
    LegacyRouteSpec("POST", "/api/integracoes/{loja_nome}/turbo", "save_turbo_token"),
    LegacyRouteSpec("DELETE", "/api/integracoes/{loja_nome}/{servico_nome}", "disconnect_integracao"),
    LegacyRouteSpec("POST", "/api/integracoes/temp-auth", "save_temp_auth_endpoint"),
    LegacyRouteSpec("POST", "/api/integracoes/bling/start", "start_bling_auth"),
    LegacyRouteSpec("POST", "/api/integracoes/mercadolivre/start", "start_mercadolivre_auth"),
    LegacyRouteSpec("GET", "/auth/callback", "integracoes_auth_callback"),
)


router = APIRouter(tags=["integracoes"])


def create_integracoes_router(legacy_module: ModuleType) -> APIRouter:
    integracoes_router = APIRouter(tags=["integracoes"])

    for spec in LEGACY_INTEGRACOES_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        integracoes_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return integracoes_router
