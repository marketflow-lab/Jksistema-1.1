"""Cadastro router definitions.

The endpoint implementations are still in backend_api.py while Cadastro helpers
are untangled. This module owns the route table so new Cadastro routes can move
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


LEGACY_CADASTRO_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("POST", "/sync-ncm/iniciar", "iniciar_sync_ncm_cadastro"),
    LegacyRouteSpec("GET", "/sync-ncm/progresso/{job_id}", "progresso_sync_ncm_cadastro"),
    LegacyRouteSpec("GET", "/produtos", "listar_produtos_cadastro"),
    LegacyRouteSpec("POST", "/produtos", "salvar_produto_cadastro"),
    LegacyRouteSpec("POST", "/importar-colunas", "importar_colunas_cadastro_por_sku"),
    LegacyRouteSpec("GET", "/colunas", "listar_colunas_cadastro"),
    LegacyRouteSpec("GET", "/produto/{sku}", "obter_produto_cadastro"),
    LegacyRouteSpec("PUT", "/produto/{sku}", "atualizar_produto_cadastro_completo"),
    LegacyRouteSpec("POST", "/produto", "incluir_produto_cadastro_completo"),
    LegacyRouteSpec("POST", "/foto/upload", "upload_foto_cadastro"),
    LegacyRouteSpec("POST", "/foto-upload", "upload_foto_cadastro"),
    LegacyRouteSpec("GET", "/produto", "obter_produto_cadastro_query"),
    LegacyRouteSpec("PUT", "/produto", "atualizar_produto_cadastro_completo_query"),
    LegacyRouteSpec("GET", "/foto/{client_id}/{filename:path}", "servir_foto_cadastro"),
    LegacyRouteSpec("GET", "/foto-arquivo/{filename:path}", "servir_foto_cadastro_por_arquivo"),
)


router = APIRouter(prefix="/api/cadastro", tags=["cadastro"])


def create_cadastro_router(legacy_module: ModuleType) -> APIRouter:
    cadastro_router = APIRouter(prefix="/api/cadastro", tags=["cadastro"])

    for spec in LEGACY_CADASTRO_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        cadastro_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return cadastro_router
