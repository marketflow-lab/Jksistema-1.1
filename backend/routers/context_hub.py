"""Administrative Context Hub router definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from backend.modules.context_hub import api as context_hub_api
from backend.services import context_hub_endpoints


def create_context_hub_router(
    *,
    base_dir: Optional[str | Path] = None,
    info_root: Optional[str | Path] = None,
    surface: Optional[str] = None,
) -> APIRouter:
    recovery_info_root: Optional[str | Path] = None
    if base_dir is not None or info_root is not None or surface is not None:
        configured = context_hub_api.configure_context_hub(
            base_dir=base_dir,
            info_root=info_root,
            surface=surface,
        )
        recovery_info_root = configured.info_root

    router = APIRouter(tags=["context-hub"])
    recovery_lifecycle = {"started": False}

    def recover_product_evidence_outboxes() -> None:
        if recovery_lifecycle["started"]:
            return
        recovery_lifecycle["started"] = True
        context_hub_api.recover_pending_product_evidence_syncs(
            info_root=recovery_info_root
        )
        from backend.modules.context_hub.catalog_product_sync import start_catalog_sync_workers
        start_catalog_sync_workers(info_root=recovery_info_root)

    def stop_product_evidence_workers() -> None:
        if not recovery_lifecycle["started"]:
            return
        recovery_lifecycle["started"] = False
        context_hub_api.stop_all_product_evidence_sync_workers()
        from backend.modules.context_hub.catalog_product_sync import stop_catalog_sync_workers
        stop_catalog_sync_workers()

    router.add_event_handler("startup", recover_product_evidence_outboxes)
    router.add_event_handler("shutdown", stop_product_evidence_workers)
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
    router.add_api_route(
        "/api/admin/context-hub/curation/notes",
        context_hub_endpoints.context_hub_curated_notes,
        methods=["GET"],
        name="context_hub_curated_notes",
    )
    router.add_api_route(
        "/api/admin/context-hub/curation/notes",
        context_hub_endpoints.context_hub_curated_note_create,
        methods=["POST"],
        name="context_hub_curated_note_create",
    )
    for action, endpoint in (
        ("validate", context_hub_endpoints.context_hub_curated_note_validate),
        ("review", context_hub_endpoints.context_hub_curated_note_review),
        ("approve", context_hub_endpoints.context_hub_curated_note_approve),
        ("reject", context_hub_endpoints.context_hub_curated_note_reject),
    ):
        router.add_api_route(
            f"/api/admin/context-hub/curation/notes/{{note_id}}/{action}",
            endpoint,
            methods=["POST"],
            name=f"context_hub_curated_note_{action}",
        )
    router.add_api_route(
        "/api/admin/context-hub/curation/publish",
        context_hub_endpoints.context_hub_curated_publish,
        methods=["POST"],
        name="context_hub_curated_publish",
    )
    router.add_api_route(
        "/api/admin/context-hub/store-sku/publish",
        context_hub_endpoints.context_hub_store_sku_publish,
        methods=["POST"],
        name="context_hub_store_sku_publish",
    )
    router.add_api_route(
        "/api/admin/context-hub/store-sku/generations/{generation_id}/rollback",
        context_hub_endpoints.context_hub_store_sku_rollback,
        methods=["POST"],
        name="context_hub_store_sku_rollback",
    )
    router.add_api_route(
        "/api/admin/context-hub/curation/backups",
        context_hub_endpoints.context_hub_curated_backups,
        methods=["GET"],
        name="context_hub_curated_backups",
    )
    router.add_api_route(
        "/api/admin/context-hub/curation/backups",
        context_hub_endpoints.context_hub_curated_backup_create,
        methods=["POST"],
        name="context_hub_curated_backup_create",
    )
    router.add_api_route(
        "/api/admin/context-hub/curation/backups/{backup_id}/restore",
        context_hub_endpoints.context_hub_curated_backup_restore,
        methods=["POST"],
        name="context_hub_curated_backup_restore",
    )
    return router


__all__ = ["create_context_hub_router"]
