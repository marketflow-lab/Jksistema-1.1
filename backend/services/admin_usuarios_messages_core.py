"""Internal helpers for admin usuarios messages core."""

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


def configure_admin_usuarios_messages_core_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_messages_core_runtime()

def _admin_messages_local_read() -> list[dict]:
    with ADMIN_MESSAGES_LOCK:
        try:
            if not os.path.exists(ARQUIVO_ADMIN_MESSAGES):
                return []
            with open(ARQUIVO_ADMIN_MESSAGES, "r", encoding="utf-8") as arquivo:
                data = json.load(arquivo)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("[ADMIN MSG] Falha ao ler mensagens locais: %s", exc)
            return []

def _admin_messages_local_write(messages: list[dict]) -> None:
    with ADMIN_MESSAGES_LOCK:
        try:
            os.makedirs(os.path.dirname(ARQUIVO_ADMIN_MESSAGES), exist_ok=True)
            itens = [m for m in (messages or []) if isinstance(m, dict)]
            itens.sort(key=lambda item: int(float(item.get("created_ts") or 0)), reverse=True)
            itens = itens[:1000]
            tmp = ARQUIVO_ADMIN_MESSAGES + ".tmp"
            with open(tmp, "w", encoding="utf-8") as arquivo:
                json.dump(itens, arquivo, ensure_ascii=False, indent=2)
            os.replace(tmp, ARQUIVO_ADMIN_MESSAGES)
        except Exception as exc:
            logger.warning("[ADMIN MSG] Falha ao salvar mensagens locais: %s", exc)

def _admin_message_public(message: dict) -> dict:
    item = message if isinstance(message, dict) else {}
    return {
        "id": str(item.get("id") or ""),
        "username": str(item.get("username") or "").strip().lower(),
        "client_id": str(item.get("client_id") or "default").strip() or "default",
        "title": str(item.get("title") or "Mensagem do administrador").strip()[:120],
        "message": str(item.get("message") or "").strip(),
        "sender": str(item.get("sender") or "admin").strip(),
        "created_at": str(item.get("created_at") or ""),
        "created_ts": int(float(item.get("created_ts") or 0)),
        "read_at": str(item.get("read_at") or ""),
        "read_ts": int(float(item.get("read_ts") or 0)),
        "storage": str(item.get("storage") or ""),
    }

def _admin_message_for_user(message: dict, username: str, client_id: str, unread_only: bool = True) -> bool:
    item = _admin_message_public(message)
    if not item.get("id") or item.get("username") != str(username or "").strip().lower():
        return False
    msg_client = str(item.get("client_id") or "default").strip() or "default"
    session_client = str(client_id or "default").strip() or "default"
    if msg_client != session_client:
        return False
    if unread_only and item.get("read_at"):
        return False
    return True

def _admin_messages_firebase_list(username: str, client_id: str, unread_only: bool = True) -> list[dict]:
    if not _firebase_live_features_ativas():
        return []
    try:
        db = _firebase_db()
        if db is None:
            return []
        coll = db.collection(_firebase_admin_messages_collection_name())
        docs = coll.where("username", "==", str(username or "").strip().lower()).stream()
        mensagens = []
        for snap in docs:
            data = snap.to_dict() or {}
            if not data.get("id"):
                data["id"] = snap.id
            if _admin_message_for_user(data, username, client_id, unread_only=unread_only):
                mensagens.append(_admin_message_public(data))
        return mensagens
    except Exception as exc:
        logger.warning("[ADMIN MSG] Falha ao listar mensagens no Firebase: %s", exc)
        return []

def _admin_messages_for_user(username: str, client_id: str, unread_only: bool = True) -> list[dict]:
    mensagens = []
    mensagens.extend(_admin_messages_firebase_list(username, client_id, unread_only=unread_only))
    mensagens.extend(
        _admin_message_public(item)
        for item in _admin_messages_local_read()
        if _admin_message_for_user(item, username, client_id, unread_only=unread_only)
    )
    por_id = {}
    for item in mensagens:
        mid = str(item.get("id") or "").strip()
        if mid:
            por_id[mid] = item
    resultado = list(por_id.values())
    resultado.sort(key=lambda item: int(float(item.get("created_ts") or 0)), reverse=True)
    return resultado

def _admin_messages_save(message: dict) -> dict:
    item = _admin_message_public(message)
    if not item.get("id"):
        item["id"] = uuid.uuid4().hex
    item["storage"] = "local"

    local = _admin_messages_local_read()
    local = [m for m in local if str((m or {}).get("id") or "") != item["id"]]
    local.append(item)
    _admin_messages_local_write(local)
    if not item.get("read_at"):
        _user_status_apply_delta(
            item.get("username"),
            item.get("client_id"),
            admin_delta=1,
            last_message_at=item.get("created_at") or "",
            last_message_ts=int(item.get("created_ts") or 0),
        )

    if _firebase_live_features_ativas():
        try:
            db = _firebase_db()
            if db is not None:
                db.collection(_firebase_admin_messages_collection_name()).document(item["id"]).set(item, merge=True)
                item["storage"] = "firebase+local"
        except Exception as exc:
            logger.warning("[ADMIN MSG] Falha ao salvar mensagem no Firebase: %s", exc)
    return item

def _admin_messages_mark_read(message_id: str, username: str, client_id: str) -> Optional[dict]:
    mid = str(message_id or "").strip()
    if not mid:
        return None
    agora_ts = int(time.time())
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    atualizado = None
    marcou_lida = False

    local = _admin_messages_local_read()
    for item in local:
        if str((item or {}).get("id") or "") == mid and _admin_message_for_user(item, username, client_id, unread_only=False):
            marcou_lida = not bool(item.get("read_at"))
            item["read_at"] = agora
            item["read_ts"] = agora_ts
            atualizado = _admin_message_public(item)
            break
    if atualizado:
        _admin_messages_local_write(local)

    if _firebase_live_features_ativas():
        try:
            db = _firebase_db()
            if db is not None:
                ref = db.collection(_firebase_admin_messages_collection_name()).document(mid)
                snap = ref.get()
                data = snap.to_dict() if snap.exists else None
                if data and _admin_message_for_user(data, username, client_id, unread_only=False):
                    if not data.get("read_at"):
                        marcou_lida = True
                    ref.set({"read_at": agora, "read_ts": agora_ts}, merge=True)
                    data.update({"read_at": agora, "read_ts": agora_ts})
                    atualizado = _admin_message_public(data)
        except Exception as exc:
            logger.warning("[ADMIN MSG] Falha ao marcar mensagem no Firebase: %s", exc)

    if marcou_lida:
        _user_status_apply_delta(username, client_id, admin_delta=-1)
    return atualizado

def _sse_event(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"

def _admin_messages_stream(username: str, client_id: str):
    eventos: "queue.Queue[tuple[str, Any]]" = queue.Queue()
    unsubscribe = None
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"

    def on_snapshot(snapshots, _changes, _read_time):
        docs = snapshots if isinstance(snapshots, (list, tuple)) else [snapshots]
        for doc in docs:
            if doc is None or not getattr(doc, "exists", False):
                continue
            data = doc.to_dict() or {}
            data["id"] = data.get("id") or doc.id
            status = _user_status_public(data, username_norm, client_norm)
            if int(status.get("admin_unread_count") or 0) <= 0:
                continue
            for mensagem in _admin_messages_for_user(username_norm, client_norm, unread_only=True)[:10]:
                eventos.put(("admin-message", mensagem))

    try:
        if not _firebase_live_features_ativas():
            eventos.put(("fallback", {"reason": "firebase_disabled"}))
        else:
            db = _firebase_db()
            if db is None:
                eventos.put(("fallback", {"reason": FIREBASE_AUTH_LAST_ERROR or "firebase_unavailable"}))
            else:
                status_ref = db.collection(_firebase_user_status_collection_name()).document(_user_status_doc_id(username_norm, client_norm))
                unsubscribe = status_ref.on_snapshot(on_snapshot)
                eventos.put(("ready", {"backend": "firebase-status"}))
    except Exception as exc:
        logger.warning("[ADMIN MSG] Stream Firebase indisponivel: %s", exc)
        eventos.put(("fallback", {"reason": "stream_unavailable"}))

    try:
        yield "retry: 15000\n\n"
        while True:
            try:
                event, data = eventos.get(timeout=25)
                yield _sse_event(event, data)
                if event == "fallback":
                    break
            except queue.Empty:
                yield _sse_event("ping", {"ts": int(time.time())})
    finally:
        if unsubscribe is not None:
            try:
                if callable(unsubscribe):
                    unsubscribe()
                elif hasattr(unsubscribe, "unsubscribe"):
                    unsubscribe.unsubscribe()
            except Exception:
                pass


__all__ = [
    "configure_admin_usuarios_messages_core_runtime",
    "_admin_messages_local_read",
    "_admin_messages_local_write",
    "_admin_message_public",
    "_admin_message_for_user",
    "_admin_messages_firebase_list",
    "_admin_messages_for_user",
    "_admin_messages_save",
    "_admin_messages_mark_read",
    "_sse_event",
    "_admin_messages_stream",
]
