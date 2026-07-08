"""Internal helpers for admin usuarios chat core."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import requests
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.schemas import LoginResponse
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_chat_core_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_chat_core_runtime()

def _user_chat_norm_username(username: str) -> str:
    return str(username or "").strip().lower()

def _user_chat_norm_client(client_id: str) -> str:
    return str(client_id or "default").strip() or "default"

def _user_chat_user_name(username: str) -> str:
    username_norm = _user_chat_norm_username(username)
    if not username_norm:
        return "Usuario"
    try:
        usuario = _obter_usuario_sql(username_norm)
        return str(usuario.get("name") or usuario.get("username") or username_norm).strip() or username_norm
    except Exception:
        return username_norm

def _user_chat_local_read() -> list[dict]:
    with USER_CHAT_MESSAGES_LOCK:
        try:
            if not os.path.exists(ARQUIVO_USER_CHAT_MESSAGES):
                return []
            with open(ARQUIVO_USER_CHAT_MESSAGES, "r", encoding="utf-8") as arquivo:
                data = json.load(arquivo)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao ler historico local: %s", exc)
            return []

def _user_chat_local_write(messages: list[dict]) -> None:
    with USER_CHAT_MESSAGES_LOCK:
        try:
            os.makedirs(os.path.dirname(ARQUIVO_USER_CHAT_MESSAGES), exist_ok=True)
            itens = [m for m in (messages or []) if isinstance(m, dict)]
            itens.sort(key=lambda item: int(float(item.get("created_ts") or 0)), reverse=True)
            itens = itens[:5000]
            tmp = ARQUIVO_USER_CHAT_MESSAGES + ".tmp"
            with open(tmp, "w", encoding="utf-8") as arquivo:
                json.dump(itens, arquivo, ensure_ascii=False, indent=2)
            os.replace(tmp, ARQUIVO_USER_CHAT_MESSAGES)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao salvar historico local: %s", exc)

def _user_chat_typing_firebase_enabled() -> bool:
    if not _firebase_live_features_ativas():
        return False
    return _env_config_bool(
        (
            "JK_FIREBASE_TYPING_ENABLED",
            "FIREBASE_TYPING_ENABLED",
            "JK_FIREBASE_CHAT_TYPING_ENABLED",
        ),
        default=False,
    )

def _user_chat_typing_key(sender_username: str, sender_client_id: str, recipient_username: str, recipient_client_id: str) -> str:
    raw = "__".join([
        _user_chat_norm_client(sender_client_id),
        _user_chat_norm_username(sender_username),
        _user_chat_norm_client(recipient_client_id),
        _user_chat_norm_username(recipient_username),
    ])
    return "".join(ch if (ch.isalnum() or ch in {"_", "-", "."}) else "_" for ch in raw)[:220]

def _user_chat_typing_public(item: dict) -> dict:
    data = item if isinstance(item, dict) else {}
    return {
        "id": str(data.get("id") or ""),
        "sender_username": _user_chat_norm_username(data.get("sender_username") or ""),
        "sender_client_id": _user_chat_norm_client(data.get("sender_client_id") or "default"),
        "recipient_username": _user_chat_norm_username(data.get("recipient_username") or ""),
        "recipient_client_id": _user_chat_norm_client(data.get("recipient_client_id") or "default"),
        "typing": bool(data.get("typing")),
        "updated_at": str(data.get("updated_at") or ""),
        "updated_ts": int(float(data.get("updated_ts") or 0)),
    }

def _user_chat_typing_local_read() -> list[dict]:
    with USER_CHAT_MESSAGES_LOCK:
        try:
            if not os.path.exists(ARQUIVO_USER_CHAT_TYPING):
                return []
            with open(ARQUIVO_USER_CHAT_TYPING, "r", encoding="utf-8") as arquivo:
                data = json.load(arquivo)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao ler digitacao local: %s", exc)
            return []

def _user_chat_typing_local_write(items: list[dict]) -> None:
    with USER_CHAT_MESSAGES_LOCK:
        try:
            agora_ts = int(time.time())
            filtrados = [
                _user_chat_typing_public(item)
                for item in (items or [])
                if isinstance(item, dict) and agora_ts - int(float(item.get("updated_ts") or 0)) <= 60
            ]
            os.makedirs(os.path.dirname(ARQUIVO_USER_CHAT_TYPING), exist_ok=True)
            tmp = ARQUIVO_USER_CHAT_TYPING + ".tmp"
            with open(tmp, "w", encoding="utf-8") as arquivo:
                json.dump(filtrados[-300:], arquivo, ensure_ascii=False, indent=2)
            os.replace(tmp, ARQUIVO_USER_CHAT_TYPING)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao salvar digitacao local: %s", exc)

def _user_chat_typing_save(sender_username: str, sender_client_id: str, recipient_username: str, recipient_client_id: str, typing: bool) -> dict:
    agora_ts = int(time.time())
    item = {
        "sender_username": _user_chat_norm_username(sender_username),
        "sender_client_id": _user_chat_norm_client(sender_client_id),
        "recipient_username": _user_chat_norm_username(recipient_username),
        "recipient_client_id": _user_chat_norm_client(recipient_client_id),
        "typing": bool(typing),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": agora_ts,
    }
    item["id"] = _user_chat_typing_key(
        item["sender_username"],
        item["sender_client_id"],
        item["recipient_username"],
        item["recipient_client_id"],
    )

    local = [raw for raw in _user_chat_typing_local_read() if str((raw or {}).get("id") or "") != item["id"]]
    local.append(item)
    _user_chat_typing_local_write(local)

    if _user_chat_typing_firebase_enabled():
        try:
            db = _firebase_db()
            if db is not None:
                db.collection(_firebase_user_chat_typing_collection_name()).document(item["id"]).set(item, merge=True)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao salvar digitacao no Firebase: %s", exc)
    return item

def _user_chat_typing_status(current_username: str, current_client_id: str, other_username: str, other_client_id: str) -> dict:
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)
    current_username = _user_chat_norm_username(current_username)
    current_client_id = _user_chat_norm_client(current_client_id)
    doc_id = _user_chat_typing_key(other_username, other_client_id, current_username, current_client_id)
    candidatos = []

    if _user_chat_typing_firebase_enabled():
        try:
            db = _firebase_db()
            if db is not None:
                snap = db.collection(_firebase_user_chat_typing_collection_name()).document(doc_id).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    data["id"] = data.get("id") or snap.id
                    candidatos.append(data)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao ler digitacao no Firebase: %s", exc)

    candidatos.extend(raw for raw in _user_chat_typing_local_read() if str((raw or {}).get("id") or "") == doc_id)
    if not candidatos:
        return {"typing": False, "updated_at": "", "updated_ts": 0}

    item = max((_user_chat_typing_public(raw) for raw in candidatos), key=lambda raw: int(raw.get("updated_ts") or 0))
    typing = bool(item.get("typing")) and (int(time.time()) - int(item.get("updated_ts") or 0) <= USER_CHAT_TYPING_TTL_SECONDS)
    return {
        "typing": typing,
        "updated_at": item.get("updated_at") or "",
        "updated_ts": int(item.get("updated_ts") or 0),
        "name": _user_chat_user_name(other_username),
    }

def _user_chat_attachments_normalizar(attachments: Any, *, validar_limites: bool = False) -> list[dict]:
    if not isinstance(attachments, list):
        return []
    normalizados = []
    total_bytes = 0
    for raw in attachments[:USER_CHAT_ATTACHMENT_MAX_COUNT]:
        if isinstance(raw, BaseModel):
            raw = raw.dict()
        if not isinstance(raw, dict):
            continue
        nome = os.path.basename(str(raw.get("name") or "arquivo").strip()) or "arquivo"
        mime = str(raw.get("mime_type") or raw.get("mime") or "application/octet-stream").strip() or "application/octet-stream"
        data_base64 = str(raw.get("data_base64") or "").strip()
        if not data_base64:
            continue
        try:
            tamanho = int(raw.get("size") or (len(data_base64) * 3 / 4))
        except Exception:
            tamanho = int(len(data_base64) * 3 / 4)
        if validar_limites and tamanho > USER_CHAT_ATTACHMENT_MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"Anexo muito grande: {nome}. Limite de 700 KB por arquivo.")
        total_bytes += max(0, tamanho)
        if validar_limites and total_bytes > USER_CHAT_ATTACHMENT_TOTAL_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Anexos muito grandes. Limite total de 900 KB por mensagem.")
        normalizados.append({
            "name": nome[:160],
            "mime_type": mime[:120],
            "data_base64": data_base64,
            "size": max(0, tamanho),
        })
    return normalizados

def _user_chat_attachment_summary(attachments: Any) -> str:
    anexos = _user_chat_attachments_normalizar(attachments)
    if not anexos:
        return ""
    if len(anexos) == 1:
        mime = str(anexos[0].get("mime_type") or "")
        if mime.startswith("image/"):
            return "Imagem"
        if mime.startswith("audio/"):
            return "Audio"
        return "Arquivo"
    return f"{len(anexos)} anexos"

def _user_chat_call_normalizar(call: Any) -> dict:
    if not isinstance(call, dict):
        return {}
    tipo = str(call.get("type") or call.get("tipo") or "daily_video").strip().lower()
    if tipo not in {"daily_video", "video", "video_call", "chamada_video"}:
        return {}
    room_url = str(call.get("room_url") or call.get("url") or "").strip()
    if not room_url:
        return {}
    try:
        parsed = urlparse(room_url)
        host = (parsed.hostname or "").strip().lower()
        if parsed.scheme != "https" or not (host == "daily.co" or host.endswith(".daily.co")):
            return {}
    except Exception:
        return {}
    host_url = str(call.get("host_url") or "").strip()
    if host_url:
        try:
            parsed_host = urlparse(host_url)
            host = (parsed_host.hostname or "").strip().lower()
            if parsed_host.scheme != "https" or not (host == "daily.co" or host.endswith(".daily.co")):
                host_url = ""
        except Exception:
            host_url = ""
    room_name = str(call.get("room_name") or call.get("name") or _sala_reuniao_room_name_from_url(room_url) or "").strip()
    return {
        "type": "daily_video",
        "room_name": room_name[:160],
        "room_url": room_url[:400],
        "host_url": host_url[:500],
        "started_at": str(call.get("started_at") or "")[:40],
        "expires_at": str(call.get("expires_at") or "")[:40],
        "created_by": _user_chat_norm_username(call.get("created_by") or ""),
        "created_by_name": str(call.get("created_by_name") or "")[:120],
        "invited_username": _user_chat_norm_username(call.get("invited_username") or ""),
        "invited_client_id": _user_chat_norm_client(call.get("invited_client_id") or "default"),
    }

def _user_chat_public(message: dict) -> dict:
    item = message if isinstance(message, dict) else {}
    return {
        "id": str(item.get("id") or ""),
        "sender_username": _user_chat_norm_username(item.get("sender_username") or ""),
        "sender_client_id": _user_chat_norm_client(item.get("sender_client_id") or "default"),
        "recipient_username": _user_chat_norm_username(item.get("recipient_username") or ""),
        "recipient_client_id": _user_chat_norm_client(item.get("recipient_client_id") or "default"),
        "message": str(item.get("message") or "").strip(),
        "created_at": str(item.get("created_at") or ""),
        "created_ts": int(float(item.get("created_ts") or 0)),
        "delivered_at": str(item.get("delivered_at") or ""),
        "delivered_ts": int(float(item.get("delivered_ts") or 0)),
        "read_at": str(item.get("read_at") or ""),
        "read_ts": int(float(item.get("read_ts") or 0)),
        "storage": str(item.get("storage") or ""),
        "attachments": _user_chat_attachments_normalizar(item.get("attachments")),
        "call": _user_chat_call_normalizar(item.get("call")),
    }

def _user_status_doc_id(username: str, client_id: str) -> str:
    raw = "__".join([
        _user_chat_norm_client(client_id),
        _user_chat_norm_username(username),
    ])
    return "".join(ch if (ch.isalnum() or ch in {"_", "-", "."}) else "_" for ch in raw)[:220]

def _user_status_cache_key(username: str, client_id: str) -> str:
    return f"user-status:{_user_chat_norm_client(client_id)}:{_user_chat_norm_username(username)}:v1"

def _user_status_empty(username: str, client_id: str) -> dict:
    username_norm = _user_chat_norm_username(username)
    client_norm = _user_chat_norm_client(client_id)
    return {
        "id": _user_status_doc_id(username_norm, client_norm),
        "username": username_norm,
        "client_id": client_norm,
        "unread_count": 0,
        "admin_unread_count": 0,
        "user_chat_unread_count": 0,
        "has_admin_message": False,
        "has_user_chat": False,
        "last_message_at": "",
        "last_message_ts": 0,
        "updated_at": "",
        "updated_ts": 0,
        "status_initialized": True,
    }

def _user_status_public(item: dict, username: str = "", client_id: str = "") -> dict:
    base = _user_status_empty(username or (item or {}).get("username"), client_id or (item or {}).get("client_id"))
    data = item if isinstance(item, dict) else {}
    admin_count = max(0, int(float(data.get("admin_unread_count") or 0)))
    chat_count = max(0, int(float(data.get("user_chat_unread_count") or 0)))
    unread_count = max(0, int(float(data.get("unread_count") or (admin_count + chat_count) or 0)))
    last_ts = int(float(data.get("last_message_ts") or 0))
    base.update({
        "id": str(data.get("id") or base["id"]),
        "username": _user_chat_norm_username(data.get("username") or base["username"]),
        "client_id": _user_chat_norm_client(data.get("client_id") or base["client_id"]),
        "unread_count": unread_count,
        "admin_unread_count": admin_count,
        "user_chat_unread_count": chat_count,
        "has_admin_message": bool(data.get("has_admin_message")) or admin_count > 0,
        "has_user_chat": bool(data.get("has_user_chat")) or chat_count > 0,
        "last_message_at": str(data.get("last_message_at") or ""),
        "last_message_ts": last_ts,
        "updated_at": str(data.get("updated_at") or ""),
        "updated_ts": int(float(data.get("updated_ts") or 0)),
        "status_initialized": bool(data.get("status_initialized", True)),
    })
    base["unread_count"] = max(base["unread_count"], base["admin_unread_count"] + base["user_chat_unread_count"])
    return base

def _user_status_local_read() -> dict:
    with USER_STATUS_LOCK:
        try:
            if not os.path.exists(ARQUIVO_USER_STATUS):
                return {}
            with open(ARQUIVO_USER_STATUS, "r", encoding="utf-8") as arquivo:
                data = json.load(arquivo)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("[USER STATUS] Falha ao ler resumo local: %s", exc)
            return {}

def _user_status_local_write(data: dict) -> None:
    with USER_STATUS_LOCK:
        try:
            os.makedirs(os.path.dirname(ARQUIVO_USER_STATUS), exist_ok=True)
            payload = data if isinstance(data, dict) else {}
            tmp = ARQUIVO_USER_STATUS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as arquivo:
                json.dump(payload, arquivo, ensure_ascii=False, indent=2)
            os.replace(tmp, ARQUIVO_USER_STATUS)
        except Exception as exc:
            logger.warning("[USER STATUS] Falha ao salvar resumo local: %s", exc)

def _user_status_local_get(username: str, client_id: str) -> Optional[dict]:
    doc_id = _user_status_doc_id(username, client_id)
    data = _user_status_local_read().get(doc_id)
    if not isinstance(data, dict):
        return None
    return _user_status_public(data, username, client_id)

def _user_status_local_save(status: dict) -> dict:
    item = _user_status_public(status, status.get("username"), status.get("client_id"))
    data = _user_status_local_read()
    data[item["id"]] = item
    _user_status_local_write(data)
    cache_item = dict(item)
    cache_item["_source"] = "local-cache"
    cache_item["_trusted"] = True
    _backend_cache_set(_user_status_cache_key(item.get("username"), item.get("client_id")), cache_item, ttl_seconds=30)
    return item

def _user_status_firebase_ref(username: str, client_id: str):
    if not _firebase_live_features_ativas():
        return None
    db = _firebase_db()
    if db is None:
        return None
    return db.collection(_firebase_user_status_collection_name()).document(_user_status_doc_id(username, client_id))

def _user_status_firebase_get(username: str, client_id: str) -> Optional[dict]:
    try:
        ref = _user_status_firebase_ref(username, client_id)
        if ref is None:
            return None
        snap = ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["id"] = data.get("id") or snap.id
        status = _user_status_public(data, username, client_id)
        status["_source"] = "firebase"
        _user_status_local_save(status)
        return status
    except Exception as exc:
        logger.warning("[USER STATUS] Falha ao ler resumo no Firebase: %s", exc)
        return None

def _user_status_firebase_set(status: dict) -> None:
    if not _firebase_live_features_ativas():
        return
    try:
        item = _user_status_public(status, status.get("username"), status.get("client_id"))
        ref = _user_status_firebase_ref(item["username"], item["client_id"])
        if ref is not None:
            ref.set(item, merge=True)
    except Exception as exc:
        logger.warning("[USER STATUS] Falha ao salvar resumo no Firebase: %s", exc)

def _user_status_firebase_delta(username: str, client_id: str, update: dict, admin_delta: int, chat_delta: int) -> None:
    if not _firebase_live_features_ativas():
        return
    try:
        ref = _user_status_firebase_ref(username, client_id)
        if ref is None:
            return
        payload = dict(update or {})
        total_delta = int(admin_delta or 0) + int(chat_delta or 0)
        if admin_delta:
            payload["admin_unread_count"] = firebase_firestore.Increment(int(admin_delta))
        if chat_delta:
            payload["user_chat_unread_count"] = firebase_firestore.Increment(int(chat_delta))
        if total_delta:
            payload["unread_count"] = firebase_firestore.Increment(total_delta)
        ref.set(payload, merge=True)
    except Exception as exc:
        logger.warning("[USER STATUS] Falha ao atualizar resumo no Firebase: %s", exc)

def _user_status_get(username: str, client_id: str) -> dict:
    cache_key = _user_status_cache_key(username, client_id)
    cached = _backend_cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    status = _user_status_firebase_get(username, client_id)
    if status is not None:
        status["_trusted"] = True
        _backend_cache_set(cache_key, status, ttl_seconds=60)
        return status
    local = _user_status_local_get(username, client_id)
    if local is not None:
        local["_source"] = "local"
        local["_trusted"] = not _firebase_live_features_ativas()
        _backend_cache_set(cache_key, local, ttl_seconds=30)
        return local
    rebuilt = _user_status_rebuild_local(username, client_id)
    rebuilt["_source"] = "rebuilt"
    rebuilt["_trusted"] = not _firebase_live_features_ativas()
    _user_status_local_save(rebuilt)
    return rebuilt

def _user_status_apply_delta(
    username: str,
    client_id: str,
    *,
    admin_delta: int = 0,
    chat_delta: int = 0,
    last_message_at: str = "",
    last_message_ts: int = 0,
) -> dict:
    current = _user_status_local_get(username, client_id) or _user_status_empty(username, client_id)
    admin_count = max(0, int(current.get("admin_unread_count") or 0) + int(admin_delta or 0))
    chat_count = max(0, int(current.get("user_chat_unread_count") or 0) + int(chat_delta or 0))
    now_ts = int(time.time())
    now_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if int(last_message_ts or 0) >= int(current.get("last_message_ts") or 0):
        current["last_message_ts"] = int(last_message_ts or 0)
        current["last_message_at"] = str(last_message_at or current.get("last_message_at") or "")
    current.update({
        "admin_unread_count": admin_count,
        "user_chat_unread_count": chat_count,
        "unread_count": admin_count + chat_count,
        "has_admin_message": admin_count > 0,
        "has_user_chat": chat_count > 0,
        "updated_at": now_at,
        "updated_ts": now_ts,
        "status_initialized": True,
    })
    saved = _user_status_local_save(current)
    remote_update = {
        "id": saved["id"],
        "username": saved["username"],
        "client_id": saved["client_id"],
        "has_admin_message": saved["has_admin_message"],
        "has_user_chat": saved["has_user_chat"],
        "last_message_at": saved["last_message_at"],
        "last_message_ts": saved["last_message_ts"],
        "updated_at": now_at,
        "updated_ts": now_ts,
        "status_initialized": True,
    }
    if (admin_delta or chat_delta) and int(admin_delta or 0) >= 0 and int(chat_delta or 0) >= 0:
        _user_status_firebase_delta(saved["username"], saved["client_id"], remote_update, int(admin_delta or 0), int(chat_delta or 0))
    else:
        remote_update.update({
            "admin_unread_count": saved["admin_unread_count"],
            "user_chat_unread_count": saved["user_chat_unread_count"],
            "unread_count": saved["unread_count"],
        })
        _user_status_firebase_set(remote_update)
    return saved

def _user_status_rebuild_local(username: str, client_id: str) -> dict:
    username_norm = _user_chat_norm_username(username)
    client_norm = _user_chat_norm_client(client_id)
    admin_unread = 0
    chat_unread = 0
    last_at = ""
    last_ts = 0

    def touch(ts: int, created_at: str) -> None:
        nonlocal last_ts, last_at
        ts_int = int(ts or 0)
        if ts_int >= last_ts:
            last_ts = ts_int
            last_at = str(created_at or last_at or "")

    for raw in _admin_messages_local_read():
        item = _admin_message_public(raw)
        if item.get("username") == username_norm and item.get("client_id") == client_norm:
            touch(item.get("created_ts") or 0, item.get("created_at") or "")
            if _admin_message_for_user(item, username_norm, client_norm, unread_only=True):
                admin_unread += 1

    for raw in _user_chat_local_read():
        item = _user_chat_public(raw)
        if (
            item.get("recipient_username") == username_norm
            and item.get("recipient_client_id") == client_norm
        ):
            touch(item.get("created_ts") or 0, item.get("created_at") or "")
            if not item.get("read_at"):
                chat_unread += 1
        elif (
            item.get("sender_username") == username_norm
            and item.get("sender_client_id") == client_norm
        ):
            touch(item.get("created_ts") or 0, item.get("created_at") or "")

    now_ts = int(time.time())
    status = _user_status_empty(username_norm, client_norm)
    status.update({
        "admin_unread_count": admin_unread,
        "user_chat_unread_count": chat_unread,
        "unread_count": admin_unread + chat_unread,
        "has_admin_message": admin_unread > 0,
        "has_user_chat": chat_unread > 0,
        "last_message_at": last_at,
        "last_message_ts": last_ts,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ts": now_ts,
    })
    return status

def _user_status_rebuild_and_save(username: str, client_id: str, sync_firebase: bool = True) -> dict:
    status = _user_status_rebuild_local(username, client_id)
    saved = _user_status_local_save(status)
    if sync_firebase:
        _user_status_firebase_set(saved)
    return saved

def _user_chat_is_between(message: dict, username: str, client_id: str, other_username: str, other_client_id: str) -> bool:
    item = _user_chat_public(message)
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)
    ida = (
        item.get("sender_username") == username
        and item.get("sender_client_id") == client_id
        and item.get("recipient_username") == other_username
        and item.get("recipient_client_id") == other_client_id
    )
    volta = (
        item.get("sender_username") == other_username
        and item.get("sender_client_id") == other_client_id
        and item.get("recipient_username") == username
        and item.get("recipient_client_id") == client_id
    )
    return ida or volta

def _user_chat_save(message: dict) -> dict:
    item = _user_chat_public(message)
    if not item.get("id"):
        item["id"] = uuid.uuid4().hex
    if not item.get("created_ts"):
        item["created_ts"] = int(time.time())
    if not item.get("created_at"):
        item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    item["storage"] = "local"

    local = _user_chat_local_read()
    local = [m for m in local if str((m or {}).get("id") or "") != item["id"]]
    local.append(item)
    _user_chat_local_write(local)
    if item.get("recipient_username") and not item.get("read_at"):
        _user_status_apply_delta(
            item.get("recipient_username"),
            item.get("recipient_client_id"),
            chat_delta=1,
            last_message_at=item.get("created_at") or "",
            last_message_ts=int(item.get("created_ts") or 0),
        )

    if _firebase_live_features_ativas():
        try:
            db = _firebase_db()
            if db is not None:
                firebase_item = dict(item)
                firebase_item["storage"] = "firebase"
                db.collection(_firebase_user_chat_collection_name()).document(item["id"]).set(firebase_item, merge=True)
                item["storage"] = "firebase+local"
                local = _user_chat_local_read()
                for local_item in local:
                    if str((local_item or {}).get("id") or "") == item["id"]:
                        local_item["storage"] = item["storage"]
                        break
                _user_chat_local_write(local)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao salvar mensagem no Firebase: %s", exc)
    return item

def _user_chat_int_ts(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except Exception:
        return 0

def _user_chat_firebase_for_user(username: str, client_id: str, after_ts: int = 0) -> list[dict]:
    if not _firebase_live_features_ativas():
        return []
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    after_ts = _user_chat_int_ts(after_ts)
    mensagens = []
    try:
        db = _firebase_db()
        if db is None:
            return []
        coll = db.collection(_firebase_user_chat_collection_name())
        for campo in ("sender_username", "recipient_username"):
            query = coll.where(campo, "==", username)
            if after_ts:
                query = query.where("created_ts", ">", after_ts)
            for snap in query.stream():
                data = snap.to_dict() or {}
                if not data.get("id"):
                    data["id"] = snap.id
                public = _user_chat_public(data)
                if after_ts and int(public.get("created_ts") or 0) <= after_ts:
                    continue
                if (
                    public.get("sender_username") == username
                    and public.get("sender_client_id") == client_id
                ) or (
                    public.get("recipient_username") == username
                    and public.get("recipient_client_id") == client_id
                ):
                    public["storage"] = "firebase"
                    mensagens.append(public)
    except Exception as exc:
        logger.warning("[USER CHAT] Falha ao listar mensagens no Firebase: %s", exc)
    return mensagens

def _user_chat_firebase_between(username: str, client_id: str, other_username: str, other_client_id: str, after_ts: int = 0) -> list[dict]:
    if not _firebase_live_features_ativas():
        return []
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)
    after_ts = _user_chat_int_ts(after_ts)
    mensagens = []
    try:
        db = _firebase_db()
        if db is None:
            return []
        coll = db.collection(_firebase_user_chat_collection_name())
        consultas = [
            coll.where("sender_username", "==", username).where("recipient_username", "==", other_username),
            coll.where("sender_username", "==", other_username).where("recipient_username", "==", username),
        ]
        if after_ts:
            consultas = [query.where("created_ts", ">", after_ts) for query in consultas]
        for query in consultas:
            stream = query.stream()
            for snap in stream:
                data = snap.to_dict() or {}
                if not data.get("id"):
                    data["id"] = snap.id
                public = _user_chat_public(data)
                if after_ts and int(public.get("created_ts") or 0) <= after_ts:
                    continue
                if _user_chat_is_between(public, username, client_id, other_username, other_client_id):
                    public["storage"] = "firebase"
                    mensagens.append(public)
    except Exception as exc:
        if after_ts:
            logger.warning("[USER CHAT] Falha ao listar conversa incremental no Firebase; usando sincronizacao completa: %s", exc)
            return _user_chat_firebase_between(username, client_id, other_username, other_client_id, 0)
        logger.warning("[USER CHAT] Falha ao listar conversa no Firebase; usando fallback por usuario: %s", exc)
        try:
            mensagens.extend(
                item for item in _user_chat_firebase_for_user(username, client_id)
                if _user_chat_is_between(item, username, client_id, other_username, other_client_id)
            )
        except Exception:
            pass
    return mensagens

def _user_chat_merge_messages(messages: list[dict]) -> list[dict]:
    por_id = {}
    for raw in messages or []:
        item = _user_chat_public(raw)
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        atual = por_id.get(mid)
        if not atual:
            por_id[mid] = item
            continue
        for campo in ("sender_username", "sender_client_id", "recipient_username", "recipient_client_id", "message", "created_at"):
            if item.get(campo) and not atual.get(campo):
                atual[campo] = item.get(campo)
        if item.get("attachments") and not atual.get("attachments"):
            atual["attachments"] = item.get("attachments")
        if item.get("call") and not atual.get("call"):
            atual["call"] = item.get("call")
        atual["created_ts"] = max(int(atual.get("created_ts") or 0), int(item.get("created_ts") or 0))
        if int(item.get("delivered_ts") or 0) > int(atual.get("delivered_ts") or 0):
            atual["delivered_at"] = item.get("delivered_at") or atual.get("delivered_at") or ""
            atual["delivered_ts"] = int(item.get("delivered_ts") or 0)
        if int(item.get("read_ts") or 0) > int(atual.get("read_ts") or 0):
            atual["read_at"] = item.get("read_at") or atual.get("read_at") or ""
            atual["read_ts"] = int(item.get("read_ts") or 0)
        storages = {s for s in str(atual.get("storage") or "").split("+") if s}
        storages.update(s for s in str(item.get("storage") or "").split("+") if s)
        atual["storage"] = "+".join(sorted(storages)) if storages else ""
    resultado = list(por_id.values())
    resultado.sort(key=lambda msg: int(msg.get("created_ts") or 0))
    return resultado

def _user_chat_all_for_user(username: str, client_id: str, include_firebase: bool = True) -> list[dict]:
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    mensagens = []
    if include_firebase:
        mensagens.extend(_user_chat_firebase_for_user(username, client_id))
    mensagens.extend(
        _user_chat_public(item)
        for item in _user_chat_local_read()
        if (
            (
                _user_chat_public(item).get("sender_username") == username
                and _user_chat_public(item).get("sender_client_id") == client_id
            )
            or (
                _user_chat_public(item).get("recipient_username") == username
                and _user_chat_public(item).get("recipient_client_id") == client_id
            )
        )
    )
    return _user_chat_merge_messages(mensagens)

def _user_chat_all_between(username: str, client_id: str, other_username: str, other_client_id: str) -> list[dict]:
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)
    mensagens = []
    mensagens.extend(_user_chat_firebase_between(username, client_id, other_username, other_client_id))
    mensagens.extend(
        _user_chat_public(item)
        for item in _user_chat_local_read()
        if _user_chat_is_between(item, username, client_id, other_username, other_client_id)
    )
    return _user_chat_merge_messages(mensagens)

def _user_chat_conversation_key(username: str, client_id: str, other_username: str, other_client_id: str) -> str:
    lados = sorted([
        f"{_user_chat_norm_client(client_id)}:{_user_chat_norm_username(username)}",
        f"{_user_chat_norm_client(other_client_id)}:{_user_chat_norm_username(other_username)}",
    ])
    return "__".join(lados)

def _user_chat_cache_remote_messages(messages: list[dict]) -> int:
    remotas = []
    for raw in messages or []:
        item = _user_chat_public(raw)
        if not item.get("id"):
            continue
        storages = {s for s in str(item.get("storage") or "").split("+") if s}
        storages.update({"firebase", "local"})
        item["storage"] = "+".join(sorted(storages))
        remotas.append(item)
    if not remotas:
        return 0
    local = _user_chat_local_read()
    _user_chat_local_write(_user_chat_merge_messages(local + remotas))
    return len(remotas)

def _user_chat_local_between(username: str, client_id: str, other_username: str, other_client_id: str) -> list[dict]:
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)
    mensagens = [
        _user_chat_public(item)
        for item in _user_chat_local_read()
        if _user_chat_is_between(item, username, client_id, other_username, other_client_id)
    ]
    return _user_chat_merge_messages(mensagens)

def _user_chat_latest_ts(messages: list[dict]) -> int:
    latest = 0
    for item in messages or []:
        latest = max(latest, _user_chat_int_ts((item or {}).get("created_ts")))
    return latest

def _user_chat_remote_sync_plan(
    username: str,
    client_id: str,
    other_username: str,
    other_client_id: str,
    local_latest_ts: int,
    force_remote: bool = False,
) -> tuple[bool, int, str]:
    local_latest_ts = _user_chat_int_ts(local_latest_ts)
    if force_remote:
        return True, 0, "forced"

    chave = _user_chat_conversation_key(username, client_id, other_username, other_client_id)
    checked_at = _user_chat_int_ts(USER_CHAT_REMOTE_HISTORY_CHECK_CACHE.get(chave))
    checked_recently = bool(checked_at and int(time.time()) - checked_at <= USER_CHAT_REMOTE_HISTORY_CHECK_TTL_SECONDS)

    status = _user_status_get(username, client_id)
    unread = max(0, int(status.get("user_chat_unread_count") or 0))
    status_latest_ts = _user_chat_int_ts(status.get("last_message_ts"))
    if unread:
        if local_latest_ts and status_latest_ts > local_latest_ts:
            return True, local_latest_ts, "unread_newer"
        return True, 0, "unread_full_check"
    if local_latest_ts and status_latest_ts > local_latest_ts:
        if checked_recently:
            return False, local_latest_ts, "status_newer_recently_checked"
        return True, local_latest_ts, "status_newer"

    if not local_latest_ts:
        if checked_recently:
            return False, 0, "empty_recently_checked"
        return True, 0, "initial_or_empty_local"

    return False, local_latest_ts, "local_current"

def _user_chat_history_cached(
    username: str,
    client_id: str,
    other_username: str,
    other_client_id: str,
    limit: int = 80,
    force_remote: bool = False,
) -> dict:
    limit = max(1, min(int(limit or 80), 200))
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id)

    local = _user_chat_local_between(username, client_id, other_username, other_client_id)
    local_latest_ts = _user_chat_latest_ts(local)
    remote_checked, after_ts, reason = _user_chat_remote_sync_plan(
        username,
        client_id,
        other_username,
        other_client_id,
        local_latest_ts,
        force_remote=force_remote,
    )
    imported = 0
    if remote_checked:
        remotas = _user_chat_firebase_between(username, client_id, other_username, other_client_id, after_ts=after_ts)
        imported = _user_chat_cache_remote_messages(remotas)
        chave = _user_chat_conversation_key(username, client_id, other_username, other_client_id)
        if imported:
            local = _user_chat_local_between(username, client_id, other_username, other_client_id)
            local_latest_ts = _user_chat_latest_ts(local)
        else:
            USER_CHAT_REMOTE_HISTORY_CHECK_CACHE[chave] = int(time.time())

    local.sort(key=lambda item: int(item.get("created_ts") or 0))
    return {
        "messages": local[-limit:],
        "history_source": "local+firebase" if imported else "local",
        "remote_checked": bool(remote_checked),
        "remote_reason": reason,
        "remote_after_ts": int(after_ts or 0),
        "remote_imported": int(imported or 0),
        "local_count": len(local),
        "local_latest_ts": int(local_latest_ts or 0),
    }

def _user_chat_update_incoming_status(
    username: str,
    client_id: str,
    other_username: str = "",
    other_client_id: str = "",
    *,
    mark_delivered: bool = True,
    mark_read: bool = False,
    sync_firebase: bool = True,
) -> None:
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    other_username = _user_chat_norm_username(other_username)
    other_client_id = _user_chat_norm_client(other_client_id) if other_client_id else ""
    agora_ts = int(time.time())
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def matches(public: dict) -> bool:
        if (
            public.get("recipient_username") != username
            or public.get("recipient_client_id") != client_id
        ):
            return False
        if other_username and public.get("sender_username") != other_username:
            return False
        if other_client_id and public.get("sender_client_id") != other_client_id:
            return False
        return True

    def build_updates(public: dict) -> dict:
        updates = {}
        if mark_delivered and not public.get("delivered_at"):
            updates["delivered_at"] = agora
            updates["delivered_ts"] = agora_ts
        if mark_read and not public.get("read_at"):
            updates["read_at"] = agora
            updates["read_ts"] = agora_ts
            if not public.get("delivered_at"):
                updates["delivered_at"] = agora
                updates["delivered_ts"] = agora_ts
        return updates

    alterou = False
    lidas_novas = 0
    lidas_ids: set[str] = set()
    local = _user_chat_local_read()
    for item in local:
        public = _user_chat_public(item)
        if not matches(public):
            continue
        updates = build_updates(public)
        if updates:
            mid = str(public.get("id") or "").strip()
            if mark_read and not public.get("read_at") and mid not in lidas_ids:
                lidas_novas += 1
                if mid:
                    lidas_ids.add(mid)
            item.update(updates)
            alterou = True
    if alterou:
        _user_chat_local_write(local)

    if sync_firebase and _firebase_live_features_ativas():
        try:
            db = _firebase_db()
            if db is not None:
                coll = db.collection(_firebase_user_chat_collection_name())
                for snap in coll.where("recipient_username", "==", username).stream():
                    data = snap.to_dict() or {}
                    if not data.get("id"):
                        data["id"] = snap.id
                    public = _user_chat_public(data)
                    if not matches(public):
                        continue
                    updates = build_updates(public)
                    if updates:
                        mid = str(public.get("id") or snap.id or "").strip()
                        if mark_read and not public.get("read_at") and mid not in lidas_ids:
                            lidas_novas += 1
                            if mid:
                                lidas_ids.add(mid)
                        coll.document(str(public.get("id") or snap.id)).set(updates, merge=True)
        except Exception as exc:
            logger.warning("[USER CHAT] Falha ao atualizar status no Firebase: %s", exc)

    if mark_read and lidas_novas:
        _user_status_apply_delta(username, client_id, chat_delta=-lidas_novas)

def _user_chat_mark_delivered_for_user(username: str, client_id: str, sync_firebase: bool = True) -> None:
    _user_chat_update_incoming_status(username, client_id, mark_delivered=True, mark_read=False, sync_firebase=sync_firebase)

def _user_chat_mark_read_between(
    username: str,
    client_id: str,
    other_username: str,
    other_client_id: str,
    sync_firebase: bool = True,
) -> None:
    _user_chat_update_incoming_status(
        username,
        client_id,
        other_username,
        other_client_id,
        mark_delivered=True,
        mark_read=True,
        sync_firebase=sync_firebase,
    )

def _user_chat_history(username: str, client_id: str, other_username: str, other_client_id: str, limit: int = 80) -> list[dict]:
    return _user_chat_history_cached(username, client_id, other_username, other_client_id, limit=limit).get("messages") or []

def _user_chat_unread_conversations(username: str, client_id: str, include_firebase: bool = True) -> list[dict]:
    username = _user_chat_norm_username(username)
    client_id = _user_chat_norm_client(client_id)
    grupos = {}
    for public in _user_chat_all_for_user(username, client_id, include_firebase=include_firebase):
        if (
            public.get("recipient_username") != username
            or public.get("recipient_client_id") != client_id
            or public.get("read_at")
        ):
            continue
        chave = (public.get("sender_username"), public.get("sender_client_id"))
        grupo = grupos.setdefault(chave, {"unread_count": 0, "last": None})
        grupo["unread_count"] += 1
        if not grupo["last"] or int(public.get("created_ts") or 0) > int((grupo["last"] or {}).get("created_ts") or 0):
            grupo["last"] = public

    conversas = []
    for (sender_username, sender_client_id), grupo in grupos.items():
        last = grupo.get("last") or {}
        conversas.append({
            "username": sender_username,
            "client_id": sender_client_id,
            "name": _user_chat_user_name(sender_username),
            "last_message_id": last.get("id") or "",
            "last_message": last.get("message") or ("Chamada de video Daily" if last.get("call") else "") or _user_chat_attachment_summary(last.get("attachments")) or "",
            "call": last.get("call") or {},
            "created_at": last.get("created_at") or "",
            "created_ts": int(last.get("created_ts") or 0),
            "unread_count": int(grupo.get("unread_count") or 0),
        })
    conversas.sort(key=lambda item: int(item.get("created_ts") or 0), reverse=True)
    return conversas

def _usuarios_podem_conversar_chat(origem: dict, destino: dict) -> bool:
    origem_empresa = _empresa_chat_key((origem or {}).get("empresa"))
    destino_empresa = _empresa_chat_key((destino or {}).get("empresa"))
    if origem_empresa and destino_empresa:
        return origem_empresa == destino_empresa
    origem_client = _user_chat_norm_client((origem or {}).get("client_id") or "default")
    destino_client = _user_chat_norm_client((destino or {}).get("client_id") or "default")
    return origem_client == destino_client

def _user_chat_usuarios_locais() -> dict:
    try:
        usuarios_sql, _headers_sql = _carregar_usuarios_sql(seed_if_empty=True)
    except Exception as exc:
        logger.warning("[USER-CHAT] Falha ao carregar usuarios locais para contatos: %s", exc)
        return {}
    if not isinstance(usuarios_sql, dict):
        return {}
    resultado = {}
    for username, usuario in usuarios_sql.items():
        if not isinstance(usuario, dict):
            continue
        username_norm = _user_chat_norm_username(username or usuario.get("username"))
        if not username_norm:
            continue
        item = dict(usuario)
        item["username"] = username_norm
        item["empresa"] = _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name"))
        item["client_id"] = _user_chat_norm_client(item.get("client_id") or "default")
        resultado[username_norm] = item
    return resultado

def _user_chat_mesclar_usuario_preferindo_principal(principal: Optional[dict], fallback: Optional[dict] = None) -> dict:
    resultado = dict(fallback or {}) if isinstance(fallback, dict) else {}
    if isinstance(principal, dict):
        for chave, valor in principal.items():
            if isinstance(valor, str):
                if valor.strip() or chave not in resultado:
                    resultado[chave] = valor
            elif valor is not None:
                resultado[chave] = valor
    username_norm = _user_chat_norm_username(resultado.get("username") or "")
    if username_norm:
        resultado["username"] = username_norm
    resultado["empresa"] = _normalizar_empresa(resultado.get("empresa") or resultado.get("company") or resultado.get("company_name"))
    resultado["client_id"] = _user_chat_norm_client(resultado.get("client_id") or "default")
    return resultado

def _user_chat_obter_usuario(username: str, usuarios_locais: Optional[dict] = None) -> dict:
    username_norm = _user_chat_norm_username(username)
    if not username_norm:
        return {}
    usuario_local = None
    if isinstance(usuarios_locais, dict):
        usuario_local = usuarios_locais.get(username_norm)
    try:
        usuario_principal = _obter_usuario_sql(username_norm)
        return _user_chat_mesclar_usuario_preferindo_principal(usuario_principal, usuario_local)
    except Exception as exc:
        logger.warning("[USER-CHAT] Falha ao obter usuario principal '%s'; usando cache local se existir: %s", username_norm, exc)
    if isinstance(usuario_local, dict):
        return _user_chat_mesclar_usuario_preferindo_principal(usuario_local)
    return {}

def _user_chat_encontrar_usuario_sessao(sessao: dict, usuarios: list[dict], usuarios_locais: Optional[dict] = None) -> dict:
    username_norm = _user_chat_norm_username((sessao or {}).get("username"))
    client_norm = _user_chat_norm_client((sessao or {}).get("client_id") or "default")
    usuario_local = usuarios_locais.get(username_norm) if isinstance(usuarios_locais, dict) else None
    candidato_principal = None
    for usuario in usuarios or []:
        if not isinstance(usuario, dict):
            continue
        if _user_chat_norm_username(usuario.get("username")) != username_norm:
            continue
        if _user_chat_norm_client(usuario.get("client_id") or "default") == client_norm:
            return _user_chat_mesclar_usuario_preferindo_principal(usuario, usuario_local)
        if candidato_principal is None:
            candidato_principal = dict(usuario)
    if candidato_principal and _empresa_chat_key(candidato_principal.get("empresa") or candidato_principal.get("company") or candidato_principal.get("company_name")):
        return _user_chat_mesclar_usuario_preferindo_principal(candidato_principal, usuario_local)
    try:
        usuario_principal = _obter_usuario_sql(username_norm)
        return _user_chat_mesclar_usuario_preferindo_principal(usuario_principal, usuario_local)
    except Exception as exc:
        logger.warning("[USER-CHAT] Falha ao resolver usuario da sessao '%s'; usando cache local se existir: %s", username_norm, exc)
    if isinstance(usuario_local, dict):
        return _user_chat_mesclar_usuario_preferindo_principal(usuario_local)
    return {}

def _user_chat_resolver_destino(sessao: dict, username: str, client_id: Optional[str] = None) -> tuple[dict, str]:
    destino_username = _user_chat_norm_username(username)
    if not destino_username:
        raise HTTPException(status_code=400, detail="Informe o usuario de destino.")
    usuarios_locais = _user_chat_usuarios_locais()
    origem = _user_chat_obter_usuario(sessao["username"], usuarios_locais)
    destino = _user_chat_obter_usuario(destino_username, usuarios_locais)
    if not _login_usuario_ativo(destino):
        raise HTTPException(status_code=403, detail="Usuario de destino inativo.")
    destino_real_client = _user_chat_norm_client(destino.get("client_id") or "default")
    destino_client_id = _user_chat_norm_client(client_id or destino_real_client or sessao["client_id"])
    if destino_real_client and destino_client_id != destino_real_client:
        raise HTTPException(status_code=404, detail="Usuario de destino nao encontrado neste cliente.")
    if not _usuarios_podem_conversar_chat(origem, destino):
        raise HTTPException(status_code=403, detail="Usuarios so podem conversar quando pertencem a mesma empresa.")
    return destino, destino_real_client or destino_client_id


__all__ = [
    "configure_admin_usuarios_chat_core_runtime",
    "_user_chat_norm_username",
    "_user_chat_norm_client",
    "_user_chat_user_name",
    "_user_chat_local_read",
    "_user_chat_local_write",
    "_user_chat_typing_firebase_enabled",
    "_user_chat_typing_key",
    "_user_chat_typing_public",
    "_user_chat_typing_local_read",
    "_user_chat_typing_local_write",
    "_user_chat_typing_save",
    "_user_chat_typing_status",
    "_user_chat_attachments_normalizar",
    "_user_chat_attachment_summary",
    "_user_chat_call_normalizar",
    "_user_chat_public",
    "_user_status_doc_id",
    "_user_status_cache_key",
    "_user_status_empty",
    "_user_status_public",
    "_user_status_local_read",
    "_user_status_local_write",
    "_user_status_local_get",
    "_user_status_local_save",
    "_user_status_firebase_ref",
    "_user_status_firebase_get",
    "_user_status_firebase_set",
    "_user_status_firebase_delta",
    "_user_status_get",
    "_user_status_apply_delta",
    "_user_status_rebuild_local",
    "_user_status_rebuild_and_save",
    "_user_chat_is_between",
    "_user_chat_save",
    "_user_chat_int_ts",
    "_user_chat_firebase_for_user",
    "_user_chat_firebase_between",
    "_user_chat_merge_messages",
    "_user_chat_all_for_user",
    "_user_chat_all_between",
    "_user_chat_conversation_key",
    "_user_chat_cache_remote_messages",
    "_user_chat_local_between",
    "_user_chat_latest_ts",
    "_user_chat_remote_sync_plan",
    "_user_chat_history_cached",
    "_user_chat_update_incoming_status",
    "_user_chat_mark_delivered_for_user",
    "_user_chat_mark_read_between",
    "_user_chat_history",
    "_user_chat_unread_conversations",
    "_usuarios_podem_conversar_chat",
    "_user_chat_usuarios_locais",
    "_user_chat_mesclar_usuario_preferindo_principal",
    "_user_chat_obter_usuario",
    "_user_chat_encontrar_usuario_sessao",
    "_user_chat_resolver_destino",
]
