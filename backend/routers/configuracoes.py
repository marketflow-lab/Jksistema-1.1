"""Configuracoes and Drive Sync router definitions.

The endpoint implementations are still in backend_api.py while configuration
and Google Drive sync helpers are untangled. This module owns the route table
so the monolith no longer registers these routes directly.
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


LEGACY_CONFIGURACOES_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/drive-sync/status", "drive_sync_status"),
    LegacyRouteSpec("POST", "/api/drive-sync/backup", "drive_sync_backup"),
    LegacyRouteSpec("POST", "/api/drive-sync/restore-latest", "drive_sync_restore_latest"),
    LegacyRouteSpec("POST", "/api/drive-sync/backup-if-changed", "drive_sync_backup_if_changed"),
    LegacyRouteSpec("POST", "/api/drive-sync/auto", "drive_sync_auto"),
    LegacyRouteSpec("GET", "/api/configuracoes", "obter_configuracoes_globais"),
    LegacyRouteSpec("PUT", "/api/configuracoes", "atualizar_configuracoes_globais"),
)


router = APIRouter(tags=["configuracoes"])


def create_configuracoes_router(legacy_module: ModuleType) -> APIRouter:
    configuracoes_router = APIRouter(tags=["configuracoes"])

    for spec in LEGACY_CONFIGURACOES_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        configuracoes_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return configuracoes_router
