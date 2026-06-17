"""Vendas router definitions.

The endpoint implementations are still in backend_api.py while Vendas helpers
are untangled. This module owns the route table so new Vendas routes can move
here without keeping registration in the monolith.
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


LEGACY_VENDAS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/vendas", "listar_vendas"),
    LegacyRouteSpec("POST", "/api/vendas/limpar-tudo", "limpar_todos_bancos_vendas"),
    LegacyRouteSpec("GET", "/api/vendas/todas", "listar_vendas_todas"),
    LegacyRouteSpec("GET", "/api/vendas/grafico", "grafico_vendas"),
    LegacyRouteSpec("GET", "/api/vendas/skus-sem-venda", "skus_sem_venda"),
    LegacyRouteSpec("GET", "/api/vendas/limites", "limites_vendas"),
    LegacyRouteSpec("POST", "/api/vendas/sync/cancel", "cancelar_sincronizacao_vendas"),
    LegacyRouteSpec("GET", "/api/vendas/sync/progress", "progresso_sincronizacao_vendas"),
    LegacyRouteSpec("POST", "/api/vendas/sync", "sincronizar_vendas"),
    LegacyRouteSpec("GET", "/api/notas-entrada", "listar_notas_entrada"),
    LegacyRouteSpec("GET", "/api/notas-entrada/itens", "listar_itens_devolucoes"),
    LegacyRouteSpec("GET", "/api/notas-entrada/sku/{sku}", "listar_notas_entrada_por_sku"),
    LegacyRouteSpec("GET", "/api/notas-entrada/debug/devolucoes-por-loja", "debug_devolucoes_por_loja"),
    LegacyRouteSpec("GET", "/api/unidades-negocios", "listar_unidades_negocios"),
    LegacyRouteSpec("POST", "/api/unidades-negocios/atualizar", "atualizar_unidade_negocio"),
    LegacyRouteSpec("POST", "/api/unidades-negocios/mapeamento", "salvar_mapeamento_unidades"),
    LegacyRouteSpec("GET", "/api/debug/notas-entrada-itens", "debug_notas_entrada_itens"),
)


router = APIRouter(tags=["vendas"])


def create_vendas_router(legacy_module: ModuleType) -> APIRouter:
    vendas_router = APIRouter(tags=["vendas"])

    for spec in LEGACY_VENDAS_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        vendas_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return vendas_router
