"""Sala de Reuniao router definitions.

The endpoint implementations are still in backend_api.py while the Daily
integration helpers are untangled. This module owns the route table so the
monolith no longer registers the Sala de Reuniao routes directly.
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


LEGACY_SALA_REUNIAO_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/sala-reuniao/status", "sala_reuniao_status"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/salas", "sala_reuniao_criar_sala"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/reunioes-ativas", "sala_reuniao_reunioes_ativas"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/salas-ativas", "sala_reuniao_salas_ativas_alias"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/salas", "sala_reuniao_salas_listar"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/reunioes-ativas/encerrar-local", "sala_reuniao_encerrar_local"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/reunioes-ativas/encerrar", "sala_reuniao_encerrar"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/reunioes-ativas/encerrar-todas", "sala_reuniao_encerrar_todas"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/salas/encerrar-local", "sala_reuniao_encerrar_local_alias"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/uso-mensal", "sala_reuniao_uso_mensal"),
    LegacyRouteSpec("POST", "/api/sala-reuniao/uso-mensal/adicionar", "sala_reuniao_uso_mensal_adicionar"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/gravacoes", "sala_reuniao_gravacoes"),
    LegacyRouteSpec("GET", "/api/sala-reuniao/transcricoes", "sala_reuniao_transcricoes"),
)


router = APIRouter(tags=["sala-reuniao"])


def create_sala_reuniao_router(legacy_module: ModuleType) -> APIRouter:
    sala_reuniao_router = APIRouter(tags=["sala-reuniao"])

    for spec in LEGACY_SALA_REUNIAO_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        sala_reuniao_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return sala_reuniao_router
