"""Presence and online-user endpoints for admin usuarios."""

from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Header, Request

from backend.schemas import MachinePresenceHeartbeatRequest
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_presence_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


def firebase_realtime_presence_session(
    request: Request,
    machine_id: Optional[str] = "",
    app_version: Optional[str] = "",
    authorization: Optional[str] = Header(default=None),
):
    sessao = _payload_sessao_por_authorization(authorization)
    machine_final = _authenticated_session_machine_id(sessao, machine_id)
    app_version_ok = _validar_versao_minima_app_ou_426(app_version)
    if not _firebase_deve_usar():
        return {
            "success": True,
            "enabled": False,
            "reason": "firebase_disabled",
            "message": "Firebase nao esta ativo para presenca em tempo real.",
        }
    if firebase_auth is None:
        return {
            "success": True,
            "enabled": False,
            "reason": "firebase_auth_unavailable",
            "message": "firebase-admin auth nao esta disponivel neste ambiente.",
        }

    project_id = _firebase_project_id()
    database_url = _firebase_realtime_database_url()
    api_key = _firebase_web_api_key()
    if not project_id or not database_url or not api_key:
        return {
            "success": True,
            "enabled": False,
            "reason": "missing_web_config",
            "message": "Configure FIREBASE_WEB_API_KEY e FIREBASE_DATABASE_URL para ativar a presenca pelo Realtime Database.",
            "project_id": project_id,
            "database_url_configured": bool(database_url),
            "api_key_configured": bool(api_key),
        }

    app_fb = _firebase_app()
    if app_fb is None:
        return {
            "success": True,
            "enabled": False,
            "reason": "firebase_unavailable",
            "message": FIREBASE_AUTH_LAST_ERROR or "Firebase indisponivel.",
        }

    username = str(sessao.get("username") or "").strip().lower()
    client_norm = str(sessao.get("client_id") or "default").strip() or "default"
    _resolved_machine, meta = _montar_machine_id_login(request, machine_final)
    client_key = _firebase_presence_client_key(client_norm)
    user_key = _firebase_presence_user_key(username)
    machine_key = _firebase_presence_machine_key_hash(machine_final)
    uid = "jkpres_" + hashlib.sha256(f"{client_norm}|{username}|{machine_key}".encode("utf-8")).hexdigest()[:48]
    claims = {
        "jk_client_id": client_norm,
        "jk_client_key": client_key,
        "jk_username": username,
        "jk_user_key": user_key,
        "jk_machine_key": machine_key,
        "jk_app_version": app_version_ok,
        "jk_app_ok": True,
        "jk_admin": True,
    }
    try:
        token = firebase_auth.create_custom_token(uid, claims, app=app_fb)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
    except Exception as exc:
        logger.warning("[FIREBASE-RTDB] Falha ao gerar token de presenca: %s", exc)
        return {
            "success": True,
            "enabled": False,
            "reason": "custom_token_failed",
            "message": f"Nao foi possivel gerar token Firebase: {exc}",
        }

    config = {
        "apiKey": api_key,
        "authDomain": _firebase_web_auth_domain(),
        "databaseURL": database_url,
        "projectId": project_id,
    }
    app_id = _firebase_web_app_id()
    if app_id:
        config["appId"] = app_id

    root = f"{_firebase_presence_root_path()}/clients/{client_key}"
    return {
        "success": True,
        "enabled": True,
        "backend": "firebase-rtdb",
        "firebaseConfig": config,
        "customToken": token,
        "rootPath": root,
        "clientKey": client_key,
        "userKey": user_key,
        "machineKey": machine_key,
        "username": username,
        "client_id": client_norm,
        "app_version": app_version_ok,
        "admin": bool(claims["jk_admin"]),
        "machine": {
            "machine_id": machine_final,
            "label": _machine_presence_label(machine_final),
            "host_name": meta.get("host_name"),
            "ip_address": meta.get("ip_address"),
        },
        "online_timeout_seconds": _machine_presence_timeout_seconds(),
    }

def _montar_payload_usuarios_online(
    usuarios: list[dict],
    *,
    include_admin_fields: bool = True,
    include_machine_details: bool = True,
    use_remote_presence: bool = True,
) -> dict:
    resultados = []
    total_online = 0
    maquinas_online_unicas = set()
    client_ids = sorted({
        str(usuario.get("client_id") or "default").strip() or "default"
        for usuario in usuarios
        if isinstance(usuario, dict)
    })
    registros_presenca = []
    if use_remote_presence:
        for cid in client_ids:
            registros_presenca.extend(_machine_presence_list_firebase_client(cid))
    registros_presenca.extend(list(_machine_presence_local_read().values()))

    for usuario in usuarios:
        if not isinstance(usuario, dict):
            continue
        username = str(usuario.get("username") or "").strip().lower()
        if not username:
            continue
        user_client_id = str(usuario.get("client_id") or "default").strip() or "default"
        maquinas = _machine_presence_list_from_records(username, user_client_id, registros_presenca)
        maquinas_online = [item for item in maquinas if item.get("online")]
        if maquinas_online:
            total_online += 1
            for maquina in maquinas_online:
                machine_key = _machine_presence_machine_key(maquina.get("machine_id") or "")
                if machine_key:
                    maquinas_online_unicas.add(machine_key)

        ultima = None
        for item in maquinas:
            if ultima is None or int(item.get("last_seen_ts") or 0) > int(ultima.get("last_seen_ts") or 0):
                ultima = item

        item_user = {
            "username": username,
            "name": usuario.get("name") or username,
            "client_id": user_client_id,
            "empresa": _normalizar_empresa(usuario.get("empresa") or usuario.get("company") or usuario.get("company_name")),
            "active": bool(usuario.get("active", True)),
            "online": bool(maquinas_online),
            "online_count": len(maquinas_online),
            "last_seen_at": (ultima or {}).get("last_seen_at") or "",
            "seconds_since_seen": (ultima or {}).get("seconds_since_seen"),
        }
        if include_admin_fields:
            item_user.update({
                "email": _normalizar_email(usuario.get("email")),
                "permissions": _normalizar_permissoes(usuario.get("permissions") or {}),
            })
        if include_machine_details:
            item_user.update({
                "machines": maquinas_online[:10],
                "all_recent_machines": maquinas[:20],
            })
        resultados.append(item_user)

    resultados.sort(key=lambda item: (not bool(item.get("online")), str(item.get("username") or "")))
    return {
        "success": True,
        "users": resultados,
        "online_users": total_online,
        "online_machines": len(maquinas_online_unicas),
        "online_timeout_seconds": _machine_presence_timeout_seconds(),
        "backend": "firebase" if _firebase_deve_usar() else "local",
    }

def admin_listar_usuarios_online(
    authorization: Optional[str] = Header(default=None),
):
    acesso = _require_online_presence_access(authorization, "")
    permissoes = acesso.get("permissions") if isinstance(acesso.get("permissions"), dict) else {}
    pode_ver_campos_admin = bool(permissoes.get("full") is True or permissoes.get("admin_usuarios") is True)
    client_norm = _user_chat_norm_client(acesso.get("client_id") or "default")
    usuarios_todos = _listar_usuarios_admin_sql(client_id=None)
    usuario_atual = {}
    username_atual = _user_chat_norm_username(acesso.get("username") or "")
    for usuario in usuarios_todos or []:
        if _user_chat_norm_username((usuario or {}).get("username")) == username_atual:
            usuario_atual = dict(usuario or {})
            break
    if not usuario_atual:
        try:
            usuario_atual = _obter_usuario_sql(username_atual)
        except Exception:
            usuario_atual = {}

    empresa_atual = _normalizar_empresa(usuario_atual.get("empresa") or usuario_atual.get("company") or usuario_atual.get("company_name"))
    if not _empresa_chat_key(empresa_atual):
        try:
            usuario_principal = _obter_usuario_sql(username_atual)
            if _empresa_chat_key((usuario_principal or {}).get("empresa") or (usuario_principal or {}).get("company") or (usuario_principal or {}).get("company_name")):
                usuario_atual = _user_chat_mesclar_usuario_preferindo_principal(usuario_principal, usuario_atual)
                empresa_atual = _normalizar_empresa(usuario_atual.get("empresa") or usuario_atual.get("company") or usuario_atual.get("company_name"))
        except Exception as exc:
            logger.warning("[MACHINES] Falha ao atualizar empresa do usuario atual para presenca: %s", exc)
    empresa_key = _empresa_chat_key(empresa_atual)
    if empresa_key:
        usuarios = [
            dict(usuario)
            for usuario in (usuarios_todos or [])
            if isinstance(usuario, dict)
            and _empresa_chat_key(usuario.get("empresa") or usuario.get("company") or usuario.get("company_name")) == empresa_key
        ]
        presence_scope = "empresa"
    else:
        usuarios = [
            dict(usuario)
            for usuario in (usuarios_todos or [])
            if isinstance(usuario, dict)
            and _user_chat_norm_client(usuario.get("client_id") or "default") == client_norm
        ]
        presence_scope = "client_id"

    payload = _montar_payload_usuarios_online(
        usuarios,
        include_admin_fields=pode_ver_campos_admin,
        include_machine_details=True,
    )
    payload["current_user"] = username_atual
    payload["current_client_id"] = client_norm
    payload["empresa"] = empresa_atual
    payload["presence_scope"] = presence_scope
    return payload

def user_machine_heartbeat(
    payload: MachinePresenceHeartbeatRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _payload_sessao_por_authorization(authorization)
    machine_final = _authenticated_session_machine_id(sessao, payload.machine_id)
    record = _machine_presence_record(
        sessao["username"],
        sessao["client_id"],
        machine_final,
        request,
        payload.page or "",
        payload.app_version,
    )
    _machine_presence_save(record)
    return {
        "success": True,
        "machine": {
            "machine_id": record.get("machine_id"),
            "label": record.get("label"),
            "app_version": record.get("app_version"),
            "last_seen_at": record.get("last_seen_at"),
            "online_timeout_seconds": record.get("online_timeout_seconds"),
        },
    }

def user_machines_online(
    machine_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _payload_sessao_por_authorization(authorization)
    maquinas = _machine_presence_list(sessao["username"], sessao["client_id"])
    maquinas = _machine_presence_mark_current(maquinas, machine_id or "")
    online = [item for item in maquinas if item.get("online")]
    return {
        "success": True,
        "username": sessao["username"],
        "client_id": sessao["client_id"],
        "online_count": len(online),
        "machines": online,
        "all_recent_machines": maquinas[:20],
        "online_timeout_seconds": _machine_presence_timeout_seconds(),
        "backend": "firebase" if _firebase_deve_usar() else "local",
    }


configure_admin_usuarios_presence_runtime()

__all__ = [
    "configure_admin_usuarios_presence_runtime",
    "firebase_realtime_presence_session",
    "_montar_payload_usuarios_online",
    "admin_listar_usuarios_online",
    "user_machine_heartbeat",
    "user_machines_online",
]
