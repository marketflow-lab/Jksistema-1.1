"""Shared Sync user identity, local/Firebase docs, and invite/link storage helpers."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import Depends, Header, HTTPException

from backend.schemas import (
    SharedSyncConfigRequest,
    SharedSyncMachineConfigRequest,
    SharedSyncRunRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserLinkRunRequest,
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_user_identity_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_normalizar_username(valor: str) -> str:
    return str(valor or "").strip().lower()

def _shared_sync_normalizar_client_id(valor: str) -> str:
    return str(valor or "default").strip() or "default"

def _shared_sync_resolver_scopes_usuario(requested: Optional[list[str]]) -> list[str]:
    scopes = [str(scope or "").strip() for scope in (requested or []) if str(scope or "").strip()]
    if not scopes:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um tipo de dado para compartilhar.")
    saida = []
    for scope in scopes:
        if scope not in SHARED_SYNC_SCOPES:
            raise HTTPException(status_code=400, detail=f"Tipo de dado invalido: {scope}")
        if scope not in saida:
            saida.append(scope)
    return saida

def _shared_sync_usuario_publico(usuario: dict) -> dict:
    email = str(usuario.get("email") or "").strip().lower()
    normalizar_email = globals().get("_normalizar_email")
    if callable(normalizar_email):
        try:
            email = normalizar_email(email)
        except Exception:
            pass
    return {
        "username": _shared_sync_normalizar_username(usuario.get("username")),
        "name": str(usuario.get("name") or usuario.get("username") or "").strip(),
        "email": email,
        "client_id": _shared_sync_normalizar_client_id(usuario.get("client_id")),
        "active": bool(usuario.get("active", True)),
    }

def _shared_sync_resolver_usuario_destino(target_username: str, target_client_id: Optional[str] = None) -> dict:
    alvo = _shared_sync_normalizar_username(target_username)
    alvo_client = str(target_client_id or "").strip()
    if not alvo and not alvo_client:
        raise HTTPException(status_code=400, detail="Informe o usuario ou codigo do cliente de destino.")

    usuarios = [_shared_sync_usuario_publico(item) for item in _listar_usuarios_admin_sql()]
    candidatos = []
    for usuario in usuarios:
        username = _shared_sync_normalizar_username(usuario.get("username"))
        client_id = _shared_sync_normalizar_client_id(usuario.get("client_id"))
        if alvo_client and client_id != alvo_client:
            continue
        if alvo and username != alvo and client_id != alvo:
            continue
        candidatos.append(usuario)

    if not candidatos and alvo:
        for usuario in usuarios:
            client_id = _shared_sync_normalizar_client_id(usuario.get("client_id"))
            if client_id == alvo:
                candidatos.append(usuario)

    if not candidatos:
        raise HTTPException(status_code=404, detail="Usuario de destino nao encontrado.")

    candidatos.sort(key=lambda item: (
        0 if _shared_sync_normalizar_username(item.get("username")) == alvo else 1,
        0 if _shared_sync_normalizar_client_id(item.get("client_id")) == (alvo_client or alvo) else 1,
        _shared_sync_normalizar_username(item.get("username")),
    ))
    destino = candidatos[0]
    if not destino.get("active"):
        raise HTTPException(status_code=400, detail="Usuario de destino esta inativo.")
    return destino

def _shared_sync_json_list_read(path: str) -> list[dict]:
    with SHARED_SYNC_USER_LINKS_LOCK:
        try:
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("[SHARED-SYNC] Falha ao ler %s: %s", path, exc)
            return []

def _shared_sync_json_list_write(path: str, data: list[dict]) -> None:
    with SHARED_SYNC_USER_LINKS_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([item for item in (data or []) if isinstance(item, dict)], f, ensure_ascii=False, indent=2)

def _shared_sync_invites_local_read() -> list[dict]:
    return _shared_sync_json_list_read(ARQUIVO_SHARED_SYNC_USER_INVITES)

def _shared_sync_invites_local_write(data: list[dict]) -> None:
    _shared_sync_json_list_write(ARQUIVO_SHARED_SYNC_USER_INVITES, data)

def _shared_sync_links_local_read() -> list[dict]:
    return _shared_sync_json_list_read(ARQUIVO_SHARED_SYNC_USER_LINKS)

def _shared_sync_links_local_write(data: list[dict]) -> None:
    _shared_sync_json_list_write(ARQUIVO_SHARED_SYNC_USER_LINKS, data)

def _shared_sync_save_doc(collection_name: str, local_path: str, item: dict) -> dict:
    item = dict(item or {})
    local = _shared_sync_json_list_read(local_path)
    found = False
    for idx, atual in enumerate(local):
        if str(atual.get("id") or "") == str(item.get("id") or ""):
            local[idx] = item
            found = True
            break
    if not found:
        local.append(item)
    _shared_sync_json_list_write(local_path, local)
    _shared_sync_docs_cache_invalidate(collection_name, local_path)

    db = _firebase_db() if _firebase_deve_usar() else None
    if db is not None and item.get("id"):
        try:
            db.collection(collection_name).document(str(item["id"])).set(item, merge=True, timeout=8)
            _shared_sync_docs_cache_invalidate(collection_name, local_path)
            item["storage"] = "firebase"
            return item
        except Exception as exc:
            logger.warning("[SHARED-SYNC] Falha ao salvar documento no Firebase: %s", exc)
    item["storage"] = "local"
    return item

def _shared_sync_delete_doc(collection_name: str, local_path: str, doc_id: str) -> dict:
    doc_id = str(doc_id or "").strip()
    if not doc_id:
        return {"local_deleted": False, "firebase_deleted": False}

    local = _shared_sync_json_list_read(local_path)
    filtrados = [item for item in local if str((item or {}).get("id") or "") != doc_id]
    local_deleted = len(filtrados) != len(local)
    if local_deleted:
        _shared_sync_json_list_write(local_path, filtrados)
        _shared_sync_docs_cache_invalidate(collection_name, local_path)

    firebase_deleted = False
    firebase_error = ""
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is not None:
        try:
            db.collection(collection_name).document(doc_id).delete(timeout=8)
            _shared_sync_docs_cache_invalidate(collection_name, local_path)
            firebase_deleted = True
        except Exception as exc:
            firebase_error = _shared_sync_exception_message(exc)
            logger.warning("[SHARED-SYNC] Falha ao excluir documento no Firebase: %s", exc)
    return {
        "local_deleted": local_deleted,
        "firebase_deleted": firebase_deleted,
        "firebase_error": firebase_error,
    }

def _shared_sync_all_docs(collection_name: str, local_path: str) -> list[dict]:
    docs = []
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is not None:
        cached = _shared_sync_docs_cache_get(collection_name, local_path)
        if cached is not None:
            docs.extend(cached)
        else:
            remote_docs = []
            try:
                for snap in db.collection(collection_name).stream(timeout=8):
                    data = snap.to_dict() or {}
                    data["id"] = data.get("id") or snap.id
                    remote_docs.append(data)
                _shared_sync_docs_cache_set(collection_name, local_path, remote_docs)
                docs.extend(remote_docs)
            except Exception as exc:
                logger.warning("[SHARED-SYNC] Falha ao listar documentos no Firebase: %s", exc)
                fallback = _shared_sync_docs_cache_get(collection_name, local_path, allow_expired=True)
                if fallback is not None:
                    docs.extend(fallback)
    docs.extend(_shared_sync_json_list_read(local_path))
    por_id = {}
    for item in docs:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("id") or "").strip()
        if not doc_id:
            continue
        atual = por_id.get(doc_id)
        if not atual or int(item.get("updated_ts") or item.get("created_ts") or 0) >= int(atual.get("updated_ts") or atual.get("created_ts") or 0):
            por_id[doc_id] = item
    return list(por_id.values())

def _shared_sync_invites_all() -> list[dict]:
    return _shared_sync_all_docs(_firebase_shared_sync_user_invites_collection_name(), ARQUIVO_SHARED_SYNC_USER_INVITES)

def _shared_sync_links_all() -> list[dict]:
    return _shared_sync_all_docs(_firebase_shared_sync_user_links_collection_name(), ARQUIVO_SHARED_SYNC_USER_LINKS)

def _shared_sync_save_invite(item: dict) -> dict:
    return _shared_sync_save_doc(_firebase_shared_sync_user_invites_collection_name(), ARQUIVO_SHARED_SYNC_USER_INVITES, item)

def _shared_sync_delete_invite_doc(invite_id: str) -> dict:
    return _shared_sync_delete_doc(_firebase_shared_sync_user_invites_collection_name(), ARQUIVO_SHARED_SYNC_USER_INVITES, invite_id)

def _shared_sync_save_link(item: dict) -> dict:
    return _shared_sync_save_doc(_firebase_shared_sync_user_links_collection_name(), ARQUIVO_SHARED_SYNC_USER_LINKS, item)

def _shared_sync_delete_link_doc(link_id: str) -> dict:
    return _shared_sync_delete_doc(_firebase_shared_sync_user_links_collection_name(), ARQUIVO_SHARED_SYNC_USER_LINKS, link_id)

def _shared_sync_get_invite(invite_id: str) -> dict:
    invite_id = str(invite_id or "").strip()
    for item in _shared_sync_invites_all():
        if str(item.get("id") or "") == invite_id:
            return _shared_sync_marcar_admin_origem(item)
    raise HTTPException(status_code=404, detail="Convite de compartilhamento nao encontrado.")

def _shared_sync_get_link(link_id: str) -> dict:
    link_id = str(link_id or "").strip()
    for item in _shared_sync_links_all():
        if str(item.get("id") or "") == link_id:
            return _shared_sync_marcar_admin_origem(item)
    raise HTTPException(status_code=404, detail="Vinculo de compartilhamento nao encontrado.")

configure_shared_sync_user_identity_runtime()

__all__ = [
    "configure_shared_sync_user_identity_runtime",
    "_shared_sync_normalizar_username",
    "_shared_sync_normalizar_client_id",
    "_shared_sync_resolver_scopes_usuario",
    "_shared_sync_usuario_publico",
    "_shared_sync_resolver_usuario_destino",
    "_shared_sync_json_list_read",
    "_shared_sync_json_list_write",
    "_shared_sync_invites_local_read",
    "_shared_sync_invites_local_write",
    "_shared_sync_links_local_read",
    "_shared_sync_links_local_write",
    "_shared_sync_save_doc",
    "_shared_sync_delete_doc",
    "_shared_sync_all_docs",
    "_shared_sync_invites_all",
    "_shared_sync_links_all",
    "_shared_sync_save_invite",
    "_shared_sync_delete_invite_doc",
    "_shared_sync_save_link",
    "_shared_sync_delete_link_doc",
    "_shared_sync_get_invite",
    "_shared_sync_get_link",
]
