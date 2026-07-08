"""Integracoes router definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter

from backend.services import integracoes_api


@dataclass(frozen=True)
class IntegracoesRouterConfig:
    get_tenant_id: Callable
    logger: object | None = None
    resolver_redirect_uri_publica: Callable[..., str] | None = None
    resolver_redirect_uri_bling: Callable[..., str] | None = None
    shared_sync_propagar_lojas_integracoes_cliente: Callable[[str, str], object] | None = None


def create_integracoes_router(config: IntegracoesRouterConfig) -> APIRouter:
    integracoes_api.configure_integracoes_api_context(
        get_tenant_id_fn=config.get_tenant_id,
        logger_ref=config.logger,
        resolver_redirect_uri_publica=config.resolver_redirect_uri_publica,
        resolver_redirect_uri_bling=config.resolver_redirect_uri_bling,
        shared_sync_propagar_lojas_integracoes_cliente=config.shared_sync_propagar_lojas_integracoes_cliente,
    )

    router = APIRouter(tags=["integracoes"])
    router.add_api_route("/api/lojas", integracoes_api.get_lojas, methods=["GET"], name="get_lojas")
    router.add_api_route("/api/lojas/{nome_loja}", integracoes_api.get_loja, methods=["GET"], name="get_loja")
    router.add_api_route("/api/lojas", integracoes_api.create_loja, methods=["POST"], name="create_loja")
    router.add_api_route("/api/lojas/{nome_loja}", integracoes_api.delete_loja, methods=["DELETE"], name="delete_loja")
    router.add_api_route("/api/integracoes/{loja_nome}/turbo", integracoes_api.save_turbo_token, methods=["POST"], name="save_turbo_token")
    router.add_api_route("/api/integracoes/{loja_nome}/{servico_nome}", integracoes_api.disconnect_integracao, methods=["DELETE"], name="disconnect_integracao")
    router.add_api_route("/api/integracoes/temp-auth", integracoes_api.save_temp_auth_endpoint, methods=["POST"], name="save_temp_auth_endpoint")
    router.add_api_route("/api/integracoes/bling/start", integracoes_api.start_bling_auth, methods=["POST"], name="start_bling_auth")
    router.add_api_route("/api/integracoes/mercadolivre/start", integracoes_api.start_mercadolivre_auth, methods=["POST"], name="start_mercadolivre_auth")
    router.add_api_route("/auth/callback", integracoes_api.integracoes_auth_callback, methods=["GET"], name="integracoes_auth_callback")
    return router


__all__ = ["IntegracoesRouterConfig", "create_integracoes_router"]
