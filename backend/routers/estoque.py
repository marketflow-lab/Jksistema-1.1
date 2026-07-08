"""Estoque router definitions."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from backend.services import estoque as estoque_service


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_ESTOQUE_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/estoque", "listar_estoque"),
    LegacyRouteSpec("POST", "/api/estoque/lancamentos/sync", "sincronizar_lancamentos_estoque_api"),
    LegacyRouteSpec("POST", "/api/estoque/lancamentos/sync-lote", "sincronizar_lancamentos_estoque_lote_api"),
    LegacyRouteSpec("GET", "/api/estoque/lancamentos/sync-lote/progress", "progresso_sincronizacao_lancamentos_estoque"),
    LegacyRouteSpec("GET", "/api/estoque/serie-retroativa", "estoque_serie_retroativa"),
    LegacyRouteSpec("GET", "/api/estoque/sync/progress", "progresso_sincronizacao_estoque"),
    LegacyRouteSpec("GET", "/api/estoque/preferencias-colunas", "api_estoque_preferencias_colunas_get"),
    LegacyRouteSpec("PUT", "/api/estoque/preferencias-colunas", "api_estoque_preferencias_colunas_put"),
    LegacyRouteSpec("POST", "/api/estoque/sync", "sincronizar_estoque"),
)


router = APIRouter(tags=["estoque"])


def create_estoque_router() -> APIRouter:
    estoque_router = APIRouter(tags=["estoque"])

    for spec in LEGACY_ESTOQUE_ROUTES:
        endpoint = getattr(estoque_service, spec.endpoint_name)
        estoque_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return estoque_router
