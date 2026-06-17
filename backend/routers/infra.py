"""Technical infrastructure router definitions.

These endpoints keep the local app shell and update checks alive. The
implementations still live in backend_api.py while infrastructure helpers are
untangled from the legacy monolith.
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


LEGACY_INFRA_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/health", "health"),
    LegacyRouteSpec("GET", "/api/app-update/check", "api_app_update_check"),
    LegacyRouteSpec("GET", "/api/mobile-update/check", "api_mobile_update_check"),
)


router = APIRouter(tags=["infra"])


def create_infra_router(legacy_module: ModuleType) -> APIRouter:
    infra_router = APIRouter(tags=["infra"])

    for spec in LEGACY_INFRA_ROUTES:
        endpoint = getattr(legacy_module, spec.endpoint_name)
        infra_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return infra_router
