"""Renovacao router definitions.

The endpoint implementations are still in backend_api.py while renewal
promotion helpers are untangled. This module owns the route table so the
monolith no longer registers these routes directly.
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


LEGACY_RENOVACAO_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/renovacao/campanhas-usuario", "renovacao_listar_campanhas_usuario"),
    LegacyRouteSpec("POST", "/api/renovacao/criar-proximo-mes", "renovacao_criar_proximo_mes"),
    LegacyRouteSpec("PUT", "/api/renovacao/campanha-periodo", "renovacao_alterar_periodo_campanha"),
    LegacyRouteSpec("DELETE", "/api/renovacao/campanha", "renovacao_deletar_campanha"),
    LegacyRouteSpec("POST", "/api/renovacao/sincronizar-promocao", "renovacao_sincronizar_promocao"),
    LegacyRouteSpec("POST", "/api/renovacao/sincronizar-promocao/iniciar", "renovacao_sincronizar_promocao_iniciar"),
    LegacyRouteSpec("GET", "/api/renovacao/sincronizar-promocao/progresso/{job_id}", "renovacao_sincronizar_promocao_progresso"),
    LegacyRouteSpec("GET", "/api/renovacao/agendamento", "renovacao_agendamento_get"),
    LegacyRouteSpec("PUT", "/api/renovacao/agendamento", "renovacao_agendamento_put"),
    LegacyRouteSpec("POST", "/api/renovacao/analisar", "renovacao_analisar_endpoint"),
    LegacyRouteSpec("POST", "/api/renovacao/exportar", "renovacao_exportar_endpoint"),
)


router = APIRouter(tags=["renovacao"])


def create_renovacao_router(legacy_module: ModuleType) -> APIRouter:
    renovacao_router = APIRouter(tags=["renovacao"])

    for spec in LEGACY_RENOVACAO_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        renovacao_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return renovacao_router
