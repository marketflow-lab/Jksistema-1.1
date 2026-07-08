"""API operations for Configuracoes and Drive Sync routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from backend.schemas.configuracoes import ConfiguracoesGlobaisRequest
from backend.services import configuracoes_drive_sync


def drive_sync_status(config: Any, authorization: Optional[str] = None) -> dict:
    ctx = configuracoes_drive_sync._drive_sync_contexto_usuario(authorization)
    token_info = configuracoes_drive_sync._google_drive_token_linkado(ctx)
    if not token_info:
        return {
            "success": True,
            "linked": False,
            "message": "Drive ainda nao vinculado. Entre pelo Google para ativar o backup automatico.",
        }
    backups = configuracoes_drive_sync._drive_sync_listar_backups(ctx, limit=1)
    return {
        "success": True,
        "linked": True,
        "latest": backups[0] if backups else None,
        "state": configuracoes_drive_sync._drive_sync_carregar_estado(ctx.get("client_id"), ctx.get("username")),
    }


def drive_sync_backup(config: Any, authorization: Optional[str] = None) -> dict:
    ctx = configuracoes_drive_sync._drive_sync_contexto_usuario(authorization)
    result = configuracoes_drive_sync._drive_sync_upload_backup(ctx)
    return {
        "success": True,
        "action": "uploaded",
        "message": "Backup salvo no Google Drive.",
        "file": result.get("file"),
        "file_count": (result.get("manifest") or {}).get("file_count"),
    }


def drive_sync_restore_latest(config: Any, authorization: Optional[str] = None) -> dict:
    ctx = configuracoes_drive_sync._drive_sync_contexto_usuario(authorization)
    result = configuracoes_drive_sync._drive_sync_restaurar_ultimo(ctx)
    if not result.get("restored"):
        return {"success": True, "action": "none", "message": result.get("message"), "latest": None}
    manifest = result.get("manifest") or {}
    return {
        "success": True,
        "action": "restored",
        "message": "Backup do Google Drive restaurado nesta maquina.",
        "latest": result.get("latest"),
        "file_count": manifest.get("restored_file_count") or manifest.get("file_count"),
    }


def drive_sync_backup_if_changed(config: Any, authorization: Optional[str] = None) -> dict:
    ctx = configuracoes_drive_sync._drive_sync_contexto_usuario(authorization)
    token_info = configuracoes_drive_sync._google_drive_token_linkado(ctx)
    if not token_info:
        return {
            "success": True,
            "linked": False,
            "action": "not_linked",
            "message": "Drive ainda nao vinculado. Entre pelo Google para ativar o backup automatico.",
        }

    client_id = ctx.get("client_id")
    username = ctx.get("username")
    estado = configuracoes_drive_sync._drive_sync_carregar_estado(client_id, username)
    latest_list = configuracoes_drive_sync._drive_sync_listar_backups(ctx, limit=1)
    latest = latest_list[0] if latest_list else None
    if latest and latest.get("id") != estado.get("last_remote_file_id"):
        return {
            "success": True,
            "linked": True,
            "action": "remote_changed",
            "message": "Existe backup remoto mais novo. Abra o dashboard para restaurar antes de enviar novos dados.",
            "latest": latest,
        }

    local_hash, local_count = configuracoes_drive_sync._drive_sync_local_snapshot(client_id)
    if not local_count:
        return {
            "success": True,
            "linked": True,
            "action": "empty",
            "message": "Nenhum dado local encontrado para enviar ao Drive.",
        }
    if local_hash == estado.get("last_snapshot_hash"):
        return {
            "success": True,
            "linked": True,
            "action": "synced",
            "message": "Backup do Drive ja esta atualizado.",
            "file_count": local_count,
        }

    result = configuracoes_drive_sync._drive_sync_upload_backup(ctx)
    return {
        "success": True,
        "linked": True,
        "action": "uploaded",
        "message": "Backup local enviado ao Google Drive.",
        "file": result.get("file"),
        "file_count": (result.get("manifest") or {}).get("file_count"),
    }


def drive_sync_auto(config: Any, authorization: Optional[str] = None) -> dict:
    ctx = configuracoes_drive_sync._drive_sync_contexto_usuario(authorization)
    token_info = configuracoes_drive_sync._google_drive_token_linkado(ctx)
    if not token_info:
        return {
            "success": True,
            "linked": False,
            "action": "not_linked",
            "message": "Drive ainda nao vinculado. Entre pelo Google para ativar o backup automatico.",
        }

    client_id = ctx.get("client_id")
    username = ctx.get("username")
    estado = configuracoes_drive_sync._drive_sync_carregar_estado(client_id, username)
    local_hash, local_count = configuracoes_drive_sync._drive_sync_local_snapshot(client_id)
    latest_list = configuracoes_drive_sync._drive_sync_listar_backups(ctx, limit=1)
    latest = latest_list[0] if latest_list else None

    if latest and latest.get("id") != estado.get("last_remote_file_id"):
        local_igual_ao_ultimo = bool(estado.get("last_snapshot_hash")) and local_hash == estado.get("last_snapshot_hash")
        if local_count == 0 or local_igual_ao_ultimo or not estado.get("last_remote_file_id"):
            result = configuracoes_drive_sync._drive_sync_restaurar_ultimo(ctx)
            manifest = result.get("manifest") or {}
            return {
                "success": True,
                "linked": True,
                "action": "restored",
                "message": "Dados restaurados do Google Drive.",
                "latest": result.get("latest"),
                "file_count": manifest.get("restored_file_count") or manifest.get("file_count"),
            }
        return {
            "success": True,
            "linked": True,
            "action": "conflict",
            "message": "Ha dados locais e um backup remoto diferente. Use Sincronizar Drive para enviar o estado local ou restaurar manualmente.",
            "latest": latest,
        }

    if local_count and local_hash != estado.get("last_snapshot_hash"):
        result = configuracoes_drive_sync._drive_sync_upload_backup(ctx)
        return {
            "success": True,
            "linked": True,
            "action": "uploaded",
            "message": "Backup local enviado ao Google Drive.",
            "file": result.get("file"),
            "file_count": (result.get("manifest") or {}).get("file_count"),
        }

    return {
        "success": True,
        "linked": True,
        "action": "synced",
        "message": "Dados locais ja estao sincronizados com o Google Drive.",
        "latest": latest,
        "file_count": local_count,
    }


def obter_configuracoes_globais(config: Any) -> dict:
    return config.carregar_configuracoes_globais()


def atualizar_configuracoes_globais(config: Any, req: ConfiguracoesGlobaisRequest) -> dict:
    valor = int(req.auto_sync_estoque_janela_minutos)
    if valor < 1 or valor > 1440:
        raise HTTPException(status_code=400, detail="A janela deve estar entre 1 e 1440 minutos.")

    atuais = config.carregar_configuracoes_globais()
    atuais["auto_sync_estoque_janela_minutos"] = valor
    modelos_ia_explicitos = any(
        valor_modelo is not None
        for valor_modelo in (
            req.ia_modelo_padrao,
            req.ia_modelo_perguntas,
            req.ia_modelo_pos_venda,
            req.ia_modelo_chat,
            req.ia_modelo_favoritos,
        )
    )
    atuais["ia_modelo_padrao"] = config.normalizar_ia_modelo_padrao(req.ia_modelo_padrao or atuais.get("ia_modelo_padrao"))
    atuais["ia_modelo_perguntas"] = config.normalizar_ia_modelo_padrao(
        req.ia_modelo_perguntas or atuais.get("ia_modelo_perguntas") or atuais["ia_modelo_padrao"]
    )
    atuais["ia_modelo_pos_venda"] = config.normalizar_ia_modelo_padrao(
        req.ia_modelo_pos_venda or atuais.get("ia_modelo_pos_venda") or atuais["ia_modelo_padrao"]
    )
    atuais["ia_modelo_chat"] = config.normalizar_ia_modelo_padrao(
        req.ia_modelo_chat or atuais.get("ia_modelo_chat") or atuais["ia_modelo_padrao"]
    )
    atuais["ia_modelo_favoritos"] = config.normalizar_ia_modelo_padrao(
        req.ia_modelo_favoritos or atuais.get("ia_modelo_favoritos") or atuais["ia_modelo_padrao"]
    )
    for campo, valor_modo in (
        ("ia_modo_padrao", req.ia_modo_padrao),
        ("ia_modo_perguntas", req.ia_modo_perguntas),
        ("ia_modo_pos_venda", req.ia_modo_pos_venda),
        ("ia_modo_chat", req.ia_modo_chat),
        ("ia_modo_favoritos", req.ia_modo_favoritos),
    ):
        if valor_modo is not None:
            atuais[campo] = config.normalizar_ia_modo(valor_modo)
        else:
            atuais[campo] = config.normalizar_ia_modo(atuais.get(campo))
    if req.ia_vertex_project_id is not None:
        atuais["ia_vertex_project_id"] = str(req.ia_vertex_project_id or "").strip()
    if req.ia_vertex_location is not None:
        atuais["ia_vertex_location"] = str(req.ia_vertex_location or "global").strip() or "global"
    if req.ia_vertex_model is not None:
        modelo_vertex_curto = config.vertex_modelo_nome_curto(req.ia_vertex_model) or "gemini-2.5-flash"
        atuais["ia_vertex_model"] = modelo_vertex_curto
        if not modelos_ia_explicitos:
            modelo_vertex = f"vertex:{modelo_vertex_curto}"
            atuais["ia_modelo_padrao"] = modelo_vertex
            atuais["ia_modelo_perguntas"] = modelo_vertex
            atuais["ia_modelo_pos_venda"] = modelo_vertex
            atuais["ia_modelo_chat"] = modelo_vertex
            atuais["ia_modelo_favoritos"] = modelo_vertex
    if req.ia_vertex_service_account_email is not None:
        atuais["ia_vertex_service_account_email"] = str(req.ia_vertex_service_account_email or "").strip()
    if req.ia_agent_resource_name is not None:
        atuais["ia_agent_resource_name"] = str(req.ia_agent_resource_name or "").strip()
    if req.ia_agent_endpoint_url is not None:
        atuais["ia_agent_endpoint_url"] = str(req.ia_agent_endpoint_url or "").strip()
    chaves_ia_alteradas = any(
        valor is not None and str(valor or "").strip()
        for valor in (
            req.ia_openai_api_key,
            req.ia_deepseek_api_key,
            req.ia_gemini_api_key,
            req.ia_agent_api_key,
        )
    ) or any(
        bool(valor)
        for valor in (
            req.ia_openai_api_key_limpar,
            req.ia_deepseek_api_key_limpar,
            req.ia_gemini_api_key_limpar,
            req.ia_agent_api_key_limpar,
        )
    )
    config.salvar_ia_provider_api_key("openai", req.ia_openai_api_key, limpar=bool(req.ia_openai_api_key_limpar))
    config.salvar_ia_provider_api_key("deepseek", req.ia_deepseek_api_key, limpar=bool(req.ia_deepseek_api_key_limpar))
    config.salvar_ia_provider_api_key("gemini", req.ia_gemini_api_key, limpar=bool(req.ia_gemini_api_key_limpar))
    config.salvar_vertex_agent_api_key(req.ia_agent_api_key, limpar=bool(req.ia_agent_api_key_limpar))
    if req.ia_favoritos_usar_imagem is not None:
        atuais["ia_favoritos_usar_imagem"] = bool(req.ia_favoritos_usar_imagem)
    else:
        atuais["ia_favoritos_usar_imagem"] = bool(atuais.get("ia_favoritos_usar_imagem"))
    for campo, valor in (
        ("ia_openai_ativa", req.ia_openai_ativa),
        ("ia_deepseek_ativa", req.ia_deepseek_ativa),
        ("ia_gemini_ativa", req.ia_gemini_ativa),
        ("ia_vertex_ativa", req.ia_vertex_ativa),
    ):
        if valor is not None:
            atuais[campo] = bool(valor)
        else:
            atuais[campo] = bool(atuais.get(campo, True))
    publicacao_chaves = None
    if chaves_ia_alteradas:
        publicacao_chaves = config.ia_secrets_publicar_no_provisionador_se_configurado()
    config.salvar_configuracoes_globais(atuais)
    resposta = config.carregar_configuracoes_globais()
    retorno = {"success": True, "configuracoes": resposta}
    if publicacao_chaves is not None:
        retorno["ia_secrets_publication"] = publicacao_chaves
    return retorno
