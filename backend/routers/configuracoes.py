"""Configuracoes and Drive Sync API routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Header

from backend.schemas.configuracoes import ConfiguracoesGlobaisRequest
from backend.services import configuracoes_api


@dataclass(frozen=True)
class ConfiguracoesRouterConfig:
    get_tenant_id: Callable
    carregar_configuracoes_globais: Callable[[], dict]
    salvar_configuracoes_globais: Callable[[dict], None]
    normalizar_ia_modelo_padrao: Callable[[Any], str]
    normalizar_ia_modo: Callable[[Any], str]
    vertex_modelo_nome_curto: Callable[[Any], str]
    salvar_ia_provider_api_key: Callable[..., None]
    salvar_vertex_agent_api_key: Callable[..., None]
    ia_secrets_publicar_no_provisionador_se_configurado: Callable[[], Any]


def create_configuracoes_router(config: ConfiguracoesRouterConfig) -> APIRouter:
    router = APIRouter(tags=["configuracoes"])

    @router.get("/api/drive-sync/status", name="drive_sync_status")
    def drive_sync_status(authorization: Optional[str] = Header(default=None)):
        return configuracoes_api.drive_sync_status(config, authorization)

    @router.post("/api/drive-sync/backup", name="drive_sync_backup")
    def drive_sync_backup(authorization: Optional[str] = Header(default=None)):
        return configuracoes_api.drive_sync_backup(config, authorization)

    @router.post("/api/drive-sync/restore-latest", name="drive_sync_restore_latest")
    def drive_sync_restore_latest(authorization: Optional[str] = Header(default=None)):
        return configuracoes_api.drive_sync_restore_latest(config, authorization)

    @router.post("/api/drive-sync/backup-if-changed", name="drive_sync_backup_if_changed")
    def drive_sync_backup_if_changed(authorization: Optional[str] = Header(default=None)):
        return configuracoes_api.drive_sync_backup_if_changed(config, authorization)

    @router.post("/api/drive-sync/auto", name="drive_sync_auto")
    def drive_sync_auto(authorization: Optional[str] = Header(default=None)):
        return configuracoes_api.drive_sync_auto(config, authorization)

    @router.get("/api/configuracoes", name="obter_configuracoes_globais")
    async def obter_configuracoes_globais(_client_id: str = Depends(config.get_tenant_id)):
        return configuracoes_api.obter_configuracoes_globais(config)

    @router.put("/api/configuracoes", name="atualizar_configuracoes_globais")
    async def atualizar_configuracoes_globais(
        req: ConfiguracoesGlobaisRequest,
        _client_id: str = Depends(config.get_tenant_id),
    ):
        return configuracoes_api.atualizar_configuracoes_globais(config, req)

    return router
