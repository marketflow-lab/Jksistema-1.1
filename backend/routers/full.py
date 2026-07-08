"""Full router definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter

from backend.services import full_api


@dataclass(frozen=True)
class FullRouterConfig:
    get_tenant_id: Callable
    listar_estoque: Callable[[str], object]
    logger: object | None = None


def create_full_router(config: FullRouterConfig) -> APIRouter:
    full_api.configure_full_api_context(
        get_tenant_id_fn=config.get_tenant_id,
        listar_estoque_fn=config.listar_estoque,
        logger_ref=config.logger,
    )

    router = APIRouter(tags=["full"])
    router.add_api_route("/api/full/estoque", full_api.listar_estoque_full, methods=["GET"], name="listar_estoque_full")
    router.add_api_route("/api/full/lojas-mercadolivre", full_api.listar_lojas_full_mercadolivre, methods=["GET"], name="listar_lojas_full_mercadolivre")
    router.add_api_route("/api/full/anuncios", full_api.listar_anuncios_full_mercadolivre, methods=["GET"], name="listar_anuncios_full_mercadolivre")
    router.add_api_route("/api/full/calendario-comercial", full_api.calendario_comercial_full, methods=["GET"], name="calendario_comercial_full")
    router.add_api_route("/api/full/envios-transito", full_api.listar_full_envios_transito, methods=["GET"], name="listar_full_envios_transito")
    router.add_api_route("/api/full/envios-transito/upload", full_api.upload_full_envios_transito, methods=["POST"], name="upload_full_envios_transito")
    router.add_api_route("/api/full/envios-transito/manual", full_api.criar_full_envio_transito_manual, methods=["POST"], name="criar_full_envio_transito_manual")
    router.add_api_route("/api/full/envios-transito/{envio_id}", full_api.atualizar_full_envio_transito, methods=["PATCH"], name="atualizar_full_envio_transito")
    router.add_api_route("/api/full/envios-transito/{envio_id}", full_api.excluir_full_envio_transito, methods=["DELETE"], name="excluir_full_envio_transito")
    router.add_api_route("/api/full/envios-transito/{envio_id}/pdf", full_api.baixar_pdf_full_envio_transito, methods=["GET"], name="baixar_pdf_full_envio_transito")
    return router


__all__ = ["FullRouterConfig", "create_full_router"]
