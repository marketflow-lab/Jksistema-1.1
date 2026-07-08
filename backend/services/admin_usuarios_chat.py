"""Internal user chat and admin-message endpoints."""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Header, HTTPException
from fastapi.responses import StreamingResponse

from backend.schemas import UserChatMessageRequest, UserChatTypingRequest
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.admin_usuarios_presence import _montar_payload_usuarios_online
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_chat_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


def user_chat_contacts(authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    usuarios_locais = _user_chat_usuarios_locais()
    usuarios_admin = _listar_usuarios_admin_sql(client_id=None)
    usuario_atual = _user_chat_encontrar_usuario_sessao(sessao, usuarios_admin, usuarios_locais)
    if not _login_usuario_ativo(usuario_atual):
        raise HTTPException(status_code=403, detail="Usuario inativo.")
    client_norm = str(sessao.get("client_id") or "default").strip() or "default"
    empresa_atual = _normalizar_empresa(usuario_atual.get("empresa"))
    if not _empresa_chat_key(empresa_atual):
        try:
            usuario_principal = _obter_usuario_sql(sessao["username"])
            if _empresa_chat_key((usuario_principal or {}).get("empresa") or (usuario_principal or {}).get("company") or (usuario_principal or {}).get("company_name")):
                usuario_atual = _user_chat_mesclar_usuario_preferindo_principal(usuario_principal, usuario_atual)
                empresa_atual = _normalizar_empresa(usuario_atual.get("empresa"))
        except Exception as exc:
            logger.warning("[USER-CHAT] Falha ao atualizar empresa do usuario atual para contatos: %s", exc)
    empresa_key = _empresa_chat_key(empresa_atual)
    cache_key = (
        f"chat-contacts-users:empresa:{hashlib.sha256(empresa_key.encode('utf-8')).hexdigest()[:24]}:v3"
        if empresa_key
        else f"chat-contacts-users:{client_norm}:v3"
    )
    usuarios = _backend_cache_get(cache_key)
    if not isinstance(usuarios, list):
        fonte_usuarios = [
            dict(usuario)
            for usuario in (usuarios_admin or [])
            if isinstance(usuario, dict)
        ]
        if not fonte_usuarios:
            fonte_usuarios = [
                dict(usuario)
                for usuario in usuarios_locais.values()
                if isinstance(usuario, dict)
            ]
        if empresa_key:
            usuarios = [
                dict(usuario)
                for usuario in fonte_usuarios
                if isinstance(usuario, dict)
                and bool(usuario.get("active", True))
                and _empresa_chat_key(usuario.get("empresa") or usuario.get("company") or usuario.get("company_name")) == empresa_key
            ]
        else:
            usuarios = [
                dict(usuario)
                for usuario in fonte_usuarios
                if isinstance(usuario, dict) and bool(usuario.get("active", True))
                and _user_chat_norm_client(usuario.get("client_id") or "default") == client_norm
            ]
        _backend_cache_set(cache_key, usuarios, ttl_seconds=90)
    payload = _montar_payload_usuarios_online(
        usuarios,
        include_admin_fields=False,
        include_machine_details=False,
        use_remote_presence=True,
    )
    payload["current_user"] = sessao["username"]
    payload["current_client_id"] = sessao["client_id"]
    payload["empresa"] = empresa_atual
    payload["chat_scope"] = "empresa" if empresa_key else "client_id"
    return payload

def user_chat_typing(payload: UserChatTypingRequest, authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    destino = _user_chat_norm_username(payload.username)
    if not destino:
        raise HTTPException(status_code=400, detail="Informe o usuario de destino.")
    _destino_usuario, destino_client_id = _user_chat_resolver_destino(sessao, destino, payload.client_id)
    if destino == sessao["username"] and destino_client_id == sessao["client_id"]:
        return {"success": True, "typing": False}
    item = _user_chat_typing_save(
        sessao["username"],
        sessao["client_id"],
        destino,
        destino_client_id,
        bool(payload.typing),
    )
    return {
        "success": True,
        "typing": bool(item.get("typing")),
        "updated_at": item.get("updated_at") or "",
        "updated_ts": int(item.get("updated_ts") or 0),
        "backend": "firebase" if _user_chat_typing_firebase_enabled() else "backend-cache",
    }

def user_chat_typing_get(
    username: str,
    client_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _payload_sessao_por_authorization(authorization)
    other_username = _user_chat_norm_username(username)
    if not other_username:
        raise HTTPException(status_code=400, detail="Informe o usuario da conversa.")
    _destino_usuario, other_client_id = _user_chat_resolver_destino(sessao, other_username, client_id)
    status = _user_chat_typing_status(sessao["username"], sessao["client_id"], other_username, other_client_id)
    return {
        "success": True,
        "typing": bool(status.get("typing")),
        "name": status.get("name") or _user_chat_user_name(other_username),
        "updated_at": status.get("updated_at") or "",
        "updated_ts": int(status.get("updated_ts") or 0),
        "backend": "firebase" if _user_chat_typing_firebase_enabled() else "backend-cache",
    }

def user_chat_unread(authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    _user_chat_mark_delivered_for_user(sessao["username"], sessao["client_id"], sync_firebase=False)
    status = _user_status_get(sessao["username"], sessao["client_id"])
    status_count = int(status.get("user_chat_unread_count") or 0)
    rebuilt_from_local = False
    conversas = (
        _user_chat_unread_conversations(sessao["username"], sessao["client_id"], include_firebase=True)
        if status_count > 0
        else []
    )
    if not bool(status.get("_trusted")):
        conversas_local = _user_chat_unread_conversations(sessao["username"], sessao["client_id"], include_firebase=False)
        local_count = sum(int(item.get("unread_count") or 0) for item in conversas_local)
        atualizado = _user_status_empty(sessao["username"], sessao["client_id"])
        atualizado.update({
            "admin_unread_count": int(status.get("admin_unread_count") or 0),
            "user_chat_unread_count": local_count,
            "unread_count": int(status.get("admin_unread_count") or 0) + local_count,
            "has_admin_message": int(status.get("admin_unread_count") or 0) > 0,
            "has_user_chat": local_count > 0,
            "last_message_at": status.get("last_message_at") or "",
            "last_message_ts": int(status.get("last_message_ts") or 0),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_ts": int(time.time()),
        })
        status = _user_status_local_save(atualizado)
        _user_status_firebase_set(status)
        status_count = local_count
        conversas = conversas_local
        rebuilt_from_local = True
    unread_count = max(0, status_count)
    return {
        "success": True,
        "conversations": conversas,
        "unread_count": unread_count,
        "summary": {
            "unread_count": int(status.get("unread_count") or 0),
            "user_chat_unread_count": status_count,
            "admin_unread_count": int(status.get("admin_unread_count") or 0),
            "last_message_at": status.get("last_message_at") or "",
            "last_message_ts": int(status.get("last_message_ts") or 0),
            "source": status.get("_source") or "",
            "history_loaded": False,
            "rebuilt_from_local": rebuilt_from_local,
        },
        "backend": "firebase" if _firebase_deve_usar() else "local",
    }

def user_chat_history(
    username: str,
    client_id: Optional[str] = None,
    limit: Optional[int] = 80,
    force_remote: Optional[bool] = False,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _payload_sessao_por_authorization(authorization)
    other_username = _user_chat_norm_username(username)
    if not other_username:
        raise HTTPException(status_code=400, detail="Informe o usuario da conversa.")
    _destino_usuario, other_client_id = _user_chat_resolver_destino(sessao, other_username, client_id)
    historico = _user_chat_history_cached(
        sessao["username"],
        sessao["client_id"],
        other_username,
        other_client_id,
        limit or 80,
        force_remote=bool(force_remote),
    )
    _user_chat_mark_read_between(
        sessao["username"],
        sessao["client_id"],
        other_username,
        other_client_id,
        sync_firebase=False,
    )
    limit_final = max(1, min(int(limit or 80), 200))
    mensagens = _user_chat_local_between(sessao["username"], sessao["client_id"], other_username, other_client_id)
    mensagens.sort(key=lambda item: int(item.get("created_ts") or 0))
    mensagens = mensagens[-limit_final:]
    return {
        "success": True,
        "current_user": sessao["username"],
        "current_client_id": sessao["client_id"],
        "other_user": other_username,
        "other_client_id": other_client_id,
        "other_name": _user_chat_user_name(other_username),
        "messages": mensagens,
        "history_source": historico.get("history_source") or "local",
        "remote_checked": bool(historico.get("remote_checked")),
        "remote_reason": historico.get("remote_reason") or "",
        "remote_after_ts": int(historico.get("remote_after_ts") or 0),
        "remote_imported": int(historico.get("remote_imported") or 0),
        "local_count": int(historico.get("local_count") or len(mensagens)),
        "local_latest_ts": int(historico.get("local_latest_ts") or 0),
    }

def user_chat_send(payload: UserChatMessageRequest, authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    destino = _user_chat_norm_username(payload.username)
    texto = str(payload.message or "").strip()
    anexos = _user_chat_attachments_normalizar(payload.attachments or [], validar_limites=True)
    call = _user_chat_call_normalizar(payload.call)
    if not destino:
        raise HTTPException(status_code=400, detail="Informe o usuario de destino.")
    if not texto and not anexos and not call:
        raise HTTPException(status_code=400, detail="Informe a mensagem ou anexe um arquivo.")
    if len(texto) > 2000:
        raise HTTPException(status_code=400, detail="A mensagem deve ter no maximo 2000 caracteres.")
    _destino_usuario, destino_client_id = _user_chat_resolver_destino(sessao, destino, payload.client_id)
    if destino == sessao["username"] and destino_client_id == sessao["client_id"]:
        raise HTTPException(status_code=400, detail="Nao e possivel enviar mensagem para voce mesmo.")

    agora_ts = int(time.time())
    mensagem = _user_chat_save({
        "id": uuid.uuid4().hex,
        "sender_username": sessao["username"],
        "sender_client_id": sessao["client_id"],
        "recipient_username": destino,
        "recipient_client_id": destino_client_id,
        "message": texto,
        "attachments": anexos,
        "call": call,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": agora_ts,
        "delivered_at": "",
        "delivered_ts": 0,
        "read_at": "",
        "read_ts": 0,
    })
    storage = str(mensagem.get("storage") or "")
    delivery_available = "firebase" in storage
    return {
        "success": True,
        "message": "Mensagem enviada." if delivery_available else "Mensagem salva localmente. Entrega remota indisponivel neste backend.",
        "chat_message": mensagem,
        "delivery_available": delivery_available,
        "backend": "firebase" if _firebase_deve_usar() else "local",
    }

def user_admin_messages(authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    status = _user_status_get(sessao["username"], sessao["client_id"])
    chat_unread_count = int(status.get("user_chat_unread_count") or 0)
    chat_conversations = (
        _user_chat_unread_conversations(sessao["username"], sessao["client_id"], include_firebase=True)
        if chat_unread_count > 0
        else []
    )
    if bool(status.get("_trusted")) and int(status.get("admin_unread_count") or 0) <= 0:
        return {
            "success": True,
            "messages": [],
            "chat_conversations": chat_conversations,
            "unread_count": 0,
            "summary": {
                "unread_count": int(status.get("unread_count") or 0),
                "admin_unread_count": int(status.get("admin_unread_count") or 0),
                "user_chat_unread_count": chat_unread_count,
                "last_message_at": status.get("last_message_at") or "",
                "last_message_ts": int(status.get("last_message_ts") or 0),
                "source": status.get("_source") or "",
            },
            "backend": "firebase-status" if _firebase_live_features_ativas() else "local",
        }
    mensagens = _admin_messages_for_user(sessao["username"], sessao["client_id"], unread_only=True)
    if not mensagens:
        status = _user_status_rebuild_and_save(sessao["username"], sessao["client_id"])
        chat_unread_count = int(status.get("user_chat_unread_count") or chat_unread_count)
        chat_conversations = (
            _user_chat_unread_conversations(sessao["username"], sessao["client_id"], include_firebase=True)
            if chat_unread_count > 0
            else []
        )
    else:
        latest = max(mensagens, key=lambda item: int(item.get("created_ts") or 0))
        atualizado = _user_status_empty(sessao["username"], sessao["client_id"])
        atualizado.update({
            "admin_unread_count": len(mensagens),
            "user_chat_unread_count": int(status.get("user_chat_unread_count") or 0),
            "unread_count": len(mensagens) + int(status.get("user_chat_unread_count") or 0),
            "has_admin_message": True,
            "has_user_chat": int(status.get("user_chat_unread_count") or 0) > 0,
            "last_message_at": latest.get("created_at") or status.get("last_message_at") or "",
            "last_message_ts": max(int(latest.get("created_ts") or 0), int(status.get("last_message_ts") or 0)),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "updated_ts": int(time.time()),
        })
        status = _user_status_local_save(atualizado)
        _user_status_firebase_set(status)
        chat_unread_count = int(status.get("user_chat_unread_count") or chat_unread_count)
    return {
        "success": True,
        "messages": mensagens[:10],
        "chat_conversations": chat_conversations,
        "unread_count": len(mensagens),
        "summary": {
            "unread_count": int(status.get("unread_count") or len(mensagens)),
            "admin_unread_count": int(status.get("admin_unread_count") or len(mensagens)),
            "user_chat_unread_count": chat_unread_count,
            "last_message_at": status.get("last_message_at") or "",
            "last_message_ts": int(status.get("last_message_ts") or 0),
            "source": status.get("_source") or "",
        },
        "backend": "firebase" if _firebase_deve_usar() else "local",
    }

def user_admin_messages_stream(token: str = ""):
    authorization = f"Bearer {str(token or '').strip()}"
    sessao = _payload_sessao_por_authorization(authorization)
    return StreamingResponse(
        _admin_messages_stream(sessao["username"], sessao["client_id"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )

def user_admin_message_read(message_id: str, authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    mensagem = _admin_messages_mark_read(message_id, sessao["username"], sessao["client_id"])
    return {
        "success": True,
        "message": "Mensagem marcada como lida.",
        "notification": mensagem,
    }


configure_admin_usuarios_chat_runtime()

__all__ = [
    "configure_admin_usuarios_chat_runtime",
    "user_chat_contacts",
    "user_chat_typing",
    "user_chat_typing_get",
    "user_chat_unread",
    "user_chat_history",
    "user_chat_send",
    "user_admin_messages",
    "user_admin_messages_stream",
    "user_admin_message_read",
]
