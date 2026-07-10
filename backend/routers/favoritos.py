"""Favoritos router definitions.

The endpoint implementations are still in backend_api.py while Favoritos
helpers are untangled. This module owns the route table so new Favoritos routes
can move here without keeping registration in the monolith.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from backend.services import favoritos_endpoints, favoritos_jobs


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_FAVORITOS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/skus", "favoritos_listar_skus"),
    LegacyRouteSpec("GET", "/skus/ocultos", "favoritos_skus_ocultos_get"),
    LegacyRouteSpec("PUT", "/skus/ocultos", "favoritos_skus_ocultos_put"),
    LegacyRouteSpec("GET", "/preferencias-vendedores-ignorados", "favoritos_vendedores_ignorados_get"),
    LegacyRouteSpec("PUT", "/preferencias-vendedores-ignorados", "favoritos_vendedores_ignorados_put"),
    LegacyRouteSpec("GET", "/preferencias-anuncios-ignorados", "favoritos_anuncios_ignorados_get"),
    LegacyRouteSpec("PUT", "/preferencias-anuncios-ignorados", "favoritos_anuncios_ignorados_put"),
    LegacyRouteSpec("GET", "/historico", "favoritos_historico_get"),
    LegacyRouteSpec("PUT", "/historico", "favoritos_historico_put"),
    LegacyRouteSpec("POST", "/historico/realtime-sync", "favoritos_historico_realtime_sync"),
    LegacyRouteSpec("GET", "/planilhas-lojas", "favoritos_planilhas_lojas_get"),
    LegacyRouteSpec("PUT", "/planilhas-lojas", "favoritos_planilhas_lojas_put"),
    LegacyRouteSpec("POST", "/planilhas-lojas/colar-historico", "favoritos_planilhas_colar_historico"),
    LegacyRouteSpec("GET", "/ml/skus-anuncios", "favoritos_ml_listar_skus_anuncios"),
    LegacyRouteSpec("GET", "/ml/promocoes", "favoritos_ml_listar_promocoes_ativas"),
    LegacyRouteSpec("POST", "/ml/validar-efetivacao", "favoritos_ml_validar_efetivacao"),
    LegacyRouteSpec("POST", "/ml/efetivar-promocao", "favoritos_ml_efetivar_promocao"),
    LegacyRouteSpec("GET", "/ml/anuncios-sku", "favoritos_ml_listar_anuncios_sku"),
    LegacyRouteSpec("PUT", "/skus/pesquisas", "favoritos_salvar_pesquisas_sku"),
    LegacyRouteSpec("POST", "/skus/pesquisas/ia", "favoritos_gerar_pesquisas_sku_ia"),
    LegacyRouteSpec("POST", "/ranking/filtrar-ia", "favoritos_filtrar_ranking_ia"),
    LegacyRouteSpec("GET", "/skus/descricao", "favoritos_buscar_descricao_sku"),
    LegacyRouteSpec("POST", "/skus/descricoes", "favoritos_buscar_descricoes_skus"),
    LegacyRouteSpec("POST", "/ml/primeira-pagina", "favoritos_ml_primeira_pagina"),
    LegacyRouteSpec("POST", "/ml/enriquecer-datas", "favoritos_ml_enriquecer_datas"),
    LegacyRouteSpec("POST", "/pesquisar", "favoritos_pesquisar"),
)

JOB_FAVORITOS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/jobs", "favoritos_jobs_start"),
    LegacyRouteSpec("GET", "/jobs/{job_id}/status", "favoritos_jobs_status"),
    LegacyRouteSpec("GET", "/jobs/{job_id}/proxima-coleta", "favoritos_jobs_proxima_coleta"),
    LegacyRouteSpec("POST", "/jobs/{job_id}/coleta-termo", "favoritos_jobs_coleta_termo"),
    LegacyRouteSpec("POST", "/jobs/{job_id}/pause", "favoritos_jobs_pause"),
    LegacyRouteSpec("POST", "/jobs/{job_id}/resume", "favoritos_jobs_resume"),
    LegacyRouteSpec("POST", "/jobs/{job_id}/cancel", "favoritos_jobs_cancel"),
)


router = APIRouter(prefix="/api/favoritos", tags=["favoritos"])


def create_favoritos_router() -> APIRouter:
    favoritos_router = APIRouter(prefix="/api/favoritos", tags=["favoritos"])

    for spec in LEGACY_FAVORITOS_ROUTES:
        endpoint = getattr(favoritos_endpoints, spec.endpoint_name)
        favoritos_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    for spec in JOB_FAVORITOS_ROUTES:
        endpoint = getattr(favoritos_jobs, spec.endpoint_name)
        favoritos_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return favoritos_router
