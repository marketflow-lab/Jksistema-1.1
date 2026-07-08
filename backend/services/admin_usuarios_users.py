"""Admin user management endpoints."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Depends, Header, HTTPException

from backend.schemas import (
    AdminUserMaxMachinesRequest,
    AdminUserMessageRequest,
    AdminUserPasswordRequest,
    AdminUserPermissionsRequest,
    AdminUserStatusRequest,
    AdminUserUpsertRequest,
)
from backend.services.admin_usuarios_context import configure_admin_usuarios_context, get_tenant_id
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_users_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


def admin_status_controle_acesso(client_id: str = Depends(get_tenant_id)):
    firebase_file = _firebase_service_account_file()
    firebase_configurado = _firebase_tem_configuracao()
    firebase_ativo = _firebase_deve_usar()
    return {
        "success": True,
        "mode": _firebase_access_mode(),
        "active_backend": "firebase" if firebase_ativo else "local",
        "firebase_configured": bool(firebase_configurado),
        "firebase_required": bool(_firebase_access_obrigatorio()),
        "firebase_project_id": _firebase_project_id() if firebase_configurado else "",
        "firebase_users_collection": _firebase_users_collection_name(),
        "firebase_users_index_collection": _firebase_users_index_collection_name(),
        "firebase_audit_collection": _firebase_audit_collection_name(),
        "firebase_presence_collection": _firebase_presence_collection_name(),
        "firebase_admin_messages_collection": _firebase_admin_messages_collection_name(),
        "firebase_credential_loaded": bool(firebase_file and os.path.exists(firebase_file)),
        "last_error": FIREBASE_AUTH_LAST_ERROR,
    }

def admin_listar_usuarios(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    acesso = _require_full_admin_user_management(authorization, client_id)
    cache_key = f"admin-users:global:{acesso.get('username')}:v3"
    cached = _backend_cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    users, backend = _listar_usuarios_admin_sql(return_backend=True, client_id=None, prefer_index=False)
    payload = {"success": True, "users": users, "backend": backend, "cache_ttl_seconds": 60}
    _backend_cache_set(cache_key, payload, ttl_seconds=60)
    return payload

def admin_enviar_mensagem_usuario(
    payload: AdminUserMessageRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    username_norm = str(payload.username or "").strip().lower()
    texto = str(payload.message or "").strip()
    if not username_norm:
        raise HTTPException(status_code=400, detail="Informe o usuario de destino.")
    if not texto:
        raise HTTPException(status_code=400, detail="Informe a mensagem.")
    if len(texto) > 2000:
        raise HTTPException(status_code=400, detail="A mensagem deve ter no maximo 2000 caracteres.")

    usuario = _obter_usuario_sql(username_norm)
    client_destino = str(payload.client_id or usuario.get("client_id") or "default").strip() or "default"
    sender = "admin"
    try:
        sessao = _payload_sessao_por_authorization(authorization)
        sender = sessao.get("username") or sender
    except Exception:
        pass

    agora_ts = int(time.time())
    mensagem = _admin_messages_save({
        "id": uuid.uuid4().hex,
        "username": username_norm,
        "client_id": client_destino,
        "title": str(payload.title or "Mensagem do administrador").strip()[:120],
        "message": texto,
        "sender": sender,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": agora_ts,
        "read_at": "",
        "read_ts": 0,
    })
    return {
        "success": True,
        "message": "Mensagem enviada.",
        "notification": mensagem,
        "backend": mensagem.get("storage") or ("firebase" if _firebase_deve_usar() else "local"),
    }

def admin_salvar_usuario(
    payload: AdminUserUpsertRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _salvar_usuario_admin_sql(payload)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    backend = "firebase" if _firebase_deve_usar() else "local"
    return {
        "success": True,
        "message": "UsuÃ¡rio salvo com sucesso.",
        "backend": backend,
        "user": _resumo_usuario_admin(usuario),
    }

def admin_trocar_senha_usuario(
    username: str,
    payload: AdminUserPasswordRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _atualizar_senha_usuario_sql(username, payload.password)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    return {
        "success": True,
        "message": "Senha atualizada.",
        "user": _resumo_usuario_admin(usuario),
    }

def admin_resetar_dispositivos_usuario(
    username: str,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _resetar_maquinas_usuario_sql(username)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    return {
        "success": True,
        "message": "Dispositivos resetados.",
        "user": _resumo_usuario_admin(usuario),
    }

def admin_alterar_status_usuario(
    username: str,
    payload: AdminUserStatusRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _atualizar_status_usuario_sql(username, payload.active)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    return {
        "success": True,
        "message": "Status atualizado.",
        "user": _resumo_usuario_admin(usuario),
    }

def admin_alterar_permissoes_usuario(
    username: str,
    payload: AdminUserPermissionsRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _atualizar_permissoes_usuario_sql(username, payload.permissions)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    return {
        "success": True,
        "message": "PermissÃµes atualizadas.",
        "user": _resumo_usuario_admin(usuario),
    }

def admin_alterar_limite_dispositivos_usuario(
    username: str,
    payload: AdminUserMaxMachinesRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    usuario = _atualizar_max_machines_usuario_sql(username, payload.max_machines)
    _backend_cache_invalidate_user_views(usuario.get("client_id") or client_id)
    return {
        "success": True,
        "message": "Limite de dispositivos atualizado.",
        "user": _resumo_usuario_admin(usuario),
    }

def admin_excluir_usuario(
    username: str,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _require_full_admin_user_management(authorization, client_id)
    _remover_usuario_sql(username)
    _backend_cache_invalidate_user_views(client_id)
    return {"success": True, "message": "UsuÃ¡rio removido."}


configure_admin_usuarios_users_runtime()

__all__ = [
    "configure_admin_usuarios_users_runtime",
    "admin_status_controle_acesso",
    "admin_listar_usuarios",
    "admin_enviar_mensagem_usuario",
    "admin_salvar_usuario",
    "admin_trocar_senha_usuario",
    "admin_resetar_dispositivos_usuario",
    "admin_alterar_status_usuario",
    "admin_alterar_permissoes_usuario",
    "admin_alterar_limite_dispositivos_usuario",
    "admin_excluir_usuario",
]
