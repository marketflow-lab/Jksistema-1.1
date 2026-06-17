"""Promocoes and Analise Promo router definitions.

The endpoint implementations are still in backend_api.py while promotion
helpers are untangled. This module owns the route table so the monolith no
longer registers these routes directly.
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


LEGACY_PROMOCOES_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/api/promo/analise-via-api", "analisar_promo_via_api"),
    LegacyRouteSpec("POST", "/api/promo/analise-via-api-arquivos", "analisar_promo_via_api_com_arquivos"),
    LegacyRouteSpec("POST", "/api/promo/analise-via-api/start", "iniciar_analise_promo_via_api"),
    LegacyRouteSpec("POST", "/api/promo/analise-via-api-arquivos/start", "iniciar_analise_promo_via_api_com_arquivos"),
    LegacyRouteSpec("GET", "/api/promo/analise-via-api-arquivos/progresso/{job_id}", "progresso_analise_promo_via_api_com_arquivos"),
    LegacyRouteSpec("POST", "/api/promo/analise-via-api-arquivos/cancelar/{job_id}", "cancelar_analise_promo_via_api_com_arquivos"),
    LegacyRouteSpec("GET", "/api/promo/automacao", "promo_automacao_obter"),
    LegacyRouteSpec("PUT", "/api/promo/automacao", "promo_automacao_salvar"),
    LegacyRouteSpec("POST", "/api/promo/aplicar-participacoes", "aplicar_participacoes_promocoes"),
    LegacyRouteSpec("POST", "/api/promo/aplicar-participacoes/start", "aplicar_participacoes_promocoes_start"),
    LegacyRouteSpec("GET", "/api/promo/aplicar-participacoes/jobs/{job_id}", "aplicar_participacoes_promocoes_job"),
    LegacyRouteSpec("GET", "/api/promo/analise", "analisar_promo_automatico"),
)


router = APIRouter(tags=["promocoes"])


def create_promocoes_router(legacy_module: ModuleType) -> APIRouter:
    promocoes_router = APIRouter(tags=["promocoes"])

    for spec in LEGACY_PROMOCOES_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        promocoes_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return promocoes_router
