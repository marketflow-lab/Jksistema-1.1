"""Shared Sync router definitions.

The endpoint implementations are still in backend_api.py while the sync
helpers are untangled. This module owns the route table so the monolith no
longer registers Shared Sync routes directly.
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


LEGACY_SHARED_SYNC_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/shared-sync/users", "shared_sync_listar_usuarios_destino"),
    LegacyRouteSpec("GET", "/api/shared-sync/user-shares", "shared_sync_user_shares_status"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/invite", "shared_sync_user_shares_invite"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/invites/{invite_id}/accept", "shared_sync_user_shares_accept"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/invites/{invite_id}/reject", "shared_sync_user_shares_reject"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/invites/{invite_id}/cancel", "shared_sync_user_shares_cancel"),
    LegacyRouteSpec("DELETE", "/api/shared-sync/user-shares/invites/{invite_id}", "shared_sync_user_shares_delete"),
    LegacyRouteSpec("PUT", "/api/shared-sync/user-shares/links/{link_id}", "shared_sync_user_shares_link_update"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/links/{link_id}/push", "shared_sync_user_shares_link_push"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/links/{link_id}/pull", "shared_sync_user_shares_link_pull"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/auto-push", "shared_sync_user_shares_auto_push"),
    LegacyRouteSpec("POST", "/api/shared-sync/user-shares/auto", "shared_sync_user_shares_auto"),
    LegacyRouteSpec("GET", "/api/shared-sync/machine-sync", "shared_sync_machine_status"),
    LegacyRouteSpec("PUT", "/api/shared-sync/machine-sync", "shared_sync_machine_salvar_config"),
    LegacyRouteSpec("POST", "/api/shared-sync/machine-sync/push", "shared_sync_machine_push"),
    LegacyRouteSpec("POST", "/api/shared-sync/machine-sync/pull", "shared_sync_machine_pull"),
    LegacyRouteSpec("POST", "/api/shared-sync/machine-sync/auto", "shared_sync_machine_auto"),
    LegacyRouteSpec("GET", "/api/admin/shared-sync/config", "admin_shared_sync_config"),
    LegacyRouteSpec("PUT", "/api/admin/shared-sync/config", "admin_shared_sync_salvar_config"),
    LegacyRouteSpec("GET", "/api/shared-sync/status", "shared_sync_status"),
    LegacyRouteSpec("POST", "/api/shared-sync/auto-pull", "shared_sync_auto_pull"),
    LegacyRouteSpec("POST", "/api/shared-sync/push", "shared_sync_push"),
    LegacyRouteSpec("POST", "/api/shared-sync/pull", "shared_sync_pull"),
)


router = APIRouter(tags=["shared-sync"])


def create_shared_sync_router(legacy_module: ModuleType) -> APIRouter:
    shared_sync_router = APIRouter(tags=["shared-sync"])

    for spec in LEGACY_SHARED_SYNC_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        shared_sync_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return shared_sync_router
