"""Administrative Context Hub router definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from backend.services import context_hub, context_hub_endpoints


def create_context_hub_router(
    *,
    base_dir: Optional[str | Path] = None,
    info_root: Optional[str | Path] = None,
    surface: Optional[str] = None,
) -> APIRouter:
    if base_dir is not None or info_root is not None or surface is not None:
        context_hub.configure_context_hub(base_dir=base_dir, info_root=info_root, surface=surface)

    router = APIRouter(tags=["context-hub"])
    router.add_api_route(
        "/api/admin/context-hub/status",
        context_hub_endpoints.context_hub_status,
        methods=["GET"],
        name="context_hub_status",
    )
    router.add_api_route(
        "/api/admin/context-hub/settings",
        context_hub_endpoints.context_hub_settings_put,
        methods=["PUT"],
        name="context_hub_settings_put",
    )
    router.add_api_route(
        "/api/admin/context-hub/rebuild",
        context_hub_endpoints.context_hub_rebuild,
        methods=["POST"],
        name="context_hub_rebuild",
    )
    router.add_api_route(
        "/api/admin/context-hub/generations",
        context_hub_endpoints.context_hub_generations,
        methods=["GET"],
        name="context_hub_generations",
    )
    router.add_api_route(
        "/api/admin/context-hub/generations/{generation_id}",
        context_hub_endpoints.context_hub_generation_get,
        methods=["GET"],
        name="context_hub_generation_get",
    )
    router.add_api_route(
        "/api/admin/context-hub/generations/{generation_id}/publish",
        context_hub_endpoints.context_hub_generation_publish,
        methods=["POST"],
        name="context_hub_generation_publish",
    )
    router.add_api_route(
        "/api/admin/context-hub/generations/{generation_id}/rollback",
        context_hub_endpoints.context_hub_generation_rollback,
        methods=["POST"],
        name="context_hub_generation_rollback",
    )
    router.add_api_route(
        "/api/admin/context-hub/search",
        context_hub_endpoints.context_hub_search,
        methods=["POST"],
        name="context_hub_search",
    )
    return router


__all__ = ["create_context_hub_router"]
