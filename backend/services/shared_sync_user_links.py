"""Shared Sync user link direction, public status, and status aggregation helpers."""

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


def configure_shared_sync_user_links_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_session_is_source(sessao: dict, item: dict) -> bool:
    return (
        _shared_sync_normalizar_username(sessao.get("username")) == _shared_sync_normalizar_username(item.get("source_username"))
        and _shared_sync_normalizar_client_id(sessao.get("client_id")) == _shared_sync_normalizar_client_id(item.get("source_client_id"))
    )

def _shared_sync_session_is_target(sessao: dict, item: dict) -> bool:
    return (
        _shared_sync_normalizar_username(sessao.get("username")) == _shared_sync_normalizar_username(item.get("target_username"))
        and _shared_sync_normalizar_client_id(sessao.get("client_id")) == _shared_sync_normalizar_client_id(item.get("target_client_id"))
    )

def _shared_sync_pair_key(source_client_id: str, source_username: str, target_client_id: str, target_username: str) -> tuple[str, str, str, str]:
    return (
        _shared_sync_normalizar_client_id(source_client_id),
        _shared_sync_normalizar_username(source_username),
        _shared_sync_normalizar_client_id(target_client_id),
        _shared_sync_normalizar_username(target_username),
    )

def _shared_sync_item_pair_key(item: dict) -> tuple[str, str, str, str]:
    return _shared_sync_pair_key(
        item.get("source_client_id"),
        item.get("source_username"),
        item.get("target_client_id"),
        item.get("target_username"),
    )

def _shared_sync_link_id_for_pair(item: dict) -> str:
    return _shared_sync_safe_doc_id("shared-sync-link", *_shared_sync_item_pair_key(item))

def _shared_sync_link_direction_parts(link: dict, direction_key: str) -> tuple[str, str, str, str]:
    if direction_key == "target_to_source":
        return (
            _shared_sync_normalizar_client_id(link.get("target_client_id")),
            _shared_sync_normalizar_username(link.get("target_username")),
            _shared_sync_normalizar_client_id(link.get("source_client_id")),
            _shared_sync_normalizar_username(link.get("source_username")),
        )
    return (
        _shared_sync_normalizar_client_id(link.get("source_client_id")),
        _shared_sync_normalizar_username(link.get("source_username")),
        _shared_sync_normalizar_client_id(link.get("target_client_id")),
        _shared_sync_normalizar_username(link.get("target_username")),
    )

def _shared_sync_link_direction_for_session(sessao: dict, link: dict) -> str:
    if _shared_sync_session_is_source(sessao, link):
        return "source_to_target"
    if _shared_sync_session_is_target(sessao, link):
        return "target_to_source"
    raise HTTPException(status_code=403, detail="Voce nao participa deste compartilhamento.")

def _shared_sync_link_reverse_direction(direction_key: str) -> str:
    return "source_to_target" if direction_key == "target_to_source" else "target_to_source"

def _shared_sync_lojas_integracoes_cross_client_volta_bloqueada(link: dict, scope: str, direction_key: str) -> bool:
    if scope != "lojas_integracoes" or direction_key == "source_to_target":
        return False
    source_client = _shared_sync_normalizar_client_id((link or {}).get("source_client_id"))
    target_client = _shared_sync_normalizar_client_id((link or {}).get("target_client_id"))
    return bool(source_client and target_client and source_client != target_client)

def _shared_sync_link_bundle_id_for_direction(link: dict, scope: str, direction_key: str) -> str:
    directional = link.get("directional_bundles") if isinstance(link.get("directional_bundles"), dict) else {}
    direction_map = directional.get(direction_key) if isinstance(directional.get(direction_key), dict) else {}
    if direction_map.get(scope):
        return str(direction_map.get(scope))
    if direction_key == "source_to_target":
        bundles = link.get("bundles") if isinstance(link.get("bundles"), dict) else {}
        if bundles.get(scope):
            return str(bundles.get(scope))
    from_client, from_username, to_client, to_username = _shared_sync_link_direction_parts(link, direction_key)
    return _shared_sync_pair_doc_id(from_client, from_username, to_client, to_username, scope)

def _shared_sync_link_set_bundle_for_direction(link: dict, scope: str, direction_key: str, bundle_id: str) -> None:
    directional = link.get("directional_bundles") if isinstance(link.get("directional_bundles"), dict) else {}
    direction_map = directional.get(direction_key) if isinstance(directional.get(direction_key), dict) else {}
    direction_map[scope] = str(bundle_id or "")
    directional[direction_key] = direction_map
    link["directional_bundles"] = directional
    if direction_key == "source_to_target":
        bundles = link.get("bundles") if isinstance(link.get("bundles"), dict) else {}
        bundles[scope] = str(bundle_id or "")
        link["bundles"] = bundles

def _shared_sync_item_timestamp(item: dict) -> int:
    for key in ("updated_ts", "created_ts", "responded_ts"):
        try:
            value = int((item or {}).get(key) or 0)
            if value:
                return value
        except Exception:
            pass
    return 0

def _shared_sync_unir_scopes(*listas) -> list[str]:
    saida = []
    for lista in listas:
        for scope in lista or []:
            scope_norm = str(scope or "").strip()
            if scope_norm in SHARED_SYNC_SCOPES and scope_norm not in saida:
                saida.append(scope_norm)
    return saida

def _shared_sync_mesmo_par(item: dict, source_user: dict, target_user: dict) -> bool:
    return _shared_sync_item_pair_key(item) == _shared_sync_pair_key(
        source_user.get("client_id"),
        source_user.get("username"),
        target_user.get("client_id"),
        target_user.get("username"),
    )

def _shared_sync_find_pending_invite(source_user: dict, target_user: dict) -> Optional[dict]:
    candidatos = [
        item for item in _shared_sync_invites_all()
        if str(item.get("status") or "pending") == "pending"
        and _shared_sync_mesmo_par(item, source_user, target_user)
    ]
    if not candidatos:
        return None
    candidatos.sort(key=_shared_sync_item_timestamp, reverse=True)
    return candidatos[0]

def _shared_sync_find_active_link(source_user: dict, target_user: dict) -> Optional[dict]:
    candidatos = [
        item for item in _shared_sync_links_all()
        if bool(item.get("active", True))
        and _shared_sync_mesmo_par(item, source_user, target_user)
    ]
    if not candidatos:
        return None
    candidatos.sort(key=_shared_sync_item_timestamp, reverse=True)
    return candidatos[0]

def _shared_sync_merge_link_items(existing: dict, incoming: dict) -> dict:
    merged = dict(existing or {})
    incoming = dict(incoming or {})
    merged["id"] = merged.get("id") or incoming.get("id") or _shared_sync_link_id_for_pair(incoming or merged)
    merged["invite_id"] = merged.get("invite_id") or incoming.get("invite_id") or ""
    for key in ("source_username", "source_client_id", "source_name", "target_username", "target_client_id", "target_name", "created_at", "created_ts"):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming.get(key)
    merged["active"] = bool(incoming.get("active", merged.get("active", True)))
    merged["source_is_admin"] = bool(merged.get("source_is_admin") or incoming.get("source_is_admin"))
    merged["source_keep_synced"] = bool(merged.get("source_keep_synced") or incoming.get("source_keep_synced"))
    merged["target_keep_synced"] = bool(merged.get("target_keep_synced") or incoming.get("target_keep_synced"))
    merged["scopes"] = _shared_sync_unir_scopes(merged.get("scopes"), incoming.get("scopes"))
    bundles = {}
    if isinstance(merged.get("bundles"), dict):
        bundles.update(merged.get("bundles") or {})
    if isinstance(incoming.get("bundles"), dict):
        bundles.update(incoming.get("bundles") or {})
    merged["bundles"] = bundles
    directional = {}
    if isinstance(merged.get("directional_bundles"), dict):
        directional.update(_shared_sync_json_clone(merged.get("directional_bundles") or {}))
    if isinstance(incoming.get("directional_bundles"), dict):
        for direction_key, value in (incoming.get("directional_bundles") or {}).items():
            current = directional.get(direction_key) if isinstance(directional.get(direction_key), dict) else {}
            if isinstance(value, dict):
                current.update(value)
            directional[direction_key] = current
    merged["directional_bundles"] = directional
    if _shared_sync_item_timestamp(incoming) >= _shared_sync_item_timestamp(merged):
        merged["updated_at"] = incoming.get("updated_at") or merged.get("updated_at") or _shared_sync_now_iso()
        merged["updated_ts"] = int(incoming.get("updated_ts") or merged.get("updated_ts") or time.time())
    return merged

def _shared_sync_session_is_admin(sessao: Optional[dict]) -> bool:
    if not isinstance(sessao, dict):
        return False
    permissoes = sessao.get("permissions") if isinstance(sessao.get("permissions"), dict) else {}
    if bool(sessao.get("is_admin") or permissoes.get("full") is True or permissoes.get("admin_usuarios") is True):
        return True
    return _shared_sync_usuario_identity_is_admin(sessao.get("username"), sessao.get("client_id"))

def _shared_sync_permissoes_indicam_admin(permissoes: Optional[dict]) -> bool:
    perms = _normalizar_permissoes(permissoes or {})
    return bool(perms.get("full") is True or perms.get("admin_usuarios") is True)

def _shared_sync_usuario_identity_is_admin(username: str, client_id: str = "") -> bool:
    username_norm = _shared_sync_normalizar_username(username)
    client_norm = _shared_sync_normalizar_client_id(client_id)
    if not username_norm:
        return False
    if username_norm in {"admin", "administrador"}:
        return True
    try:
        for usuario in _listar_usuarios_admin_sql(client_id=client_norm or None):
            if _shared_sync_normalizar_username(usuario.get("username")) != username_norm:
                continue
            usuario_client = _shared_sync_normalizar_client_id(usuario.get("client_id"))
            if client_norm and usuario_client and usuario_client != client_norm:
                continue
            if _shared_sync_permissoes_indicam_admin(usuario.get("permissions") if isinstance(usuario.get("permissions"), dict) else {}):
                return True
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao conferir admin do usuario %s/%s: %s", username_norm, client_norm, exc)
    return False

def _shared_sync_marcar_admin_origem(item: dict) -> dict:
    if not isinstance(item, dict):
        return item
    if not bool(item.get("source_is_admin")) and _shared_sync_usuario_identity_is_admin(item.get("source_username"), item.get("source_client_id")):
        item = dict(item)
        item["source_is_admin"] = True
    return item

def _shared_sync_source_is_admin(item: dict, sessao: Optional[dict] = None) -> bool:
    if _shared_sync_session_is_admin(sessao):
        return True
    item = item or {}
    if bool(item.get("source_is_admin")):
        return True
    source_username = _shared_sync_normalizar_username(item.get("source_username"))
    source_client = _shared_sync_normalizar_client_id(item.get("source_client_id"))
    return _shared_sync_usuario_identity_is_admin(source_username, source_client)

def _shared_sync_scope_permitido_entre_clientes(item: dict, scope: str, sessao: Optional[dict] = None) -> bool:
    if scope != "lojas_integracoes":
        return True
    if _shared_sync_source_is_admin(item, sessao):
        return True
    source_client = _shared_sync_normalizar_client_id((item or {}).get("source_client_id"))
    target_client = _shared_sync_normalizar_client_id((item or {}).get("target_client_id"))
    return bool(source_client and target_client and source_client == target_client)

def _shared_sync_filtrar_scopes_entre_clientes(scopes: list[str], item: dict, sessao: Optional[dict] = None) -> list[str]:
    saida = []
    for scope in scopes or []:
        if scope not in SHARED_SYNC_SCOPES:
            continue
        if not _shared_sync_scope_permitido_entre_clientes(item, scope, sessao):
            continue
        if scope not in saida:
            saida.append(scope)
    return saida

def _shared_sync_invite_public(item: dict, sessao: Optional[dict] = None) -> dict:
    scopes = _shared_sync_filtrar_scopes_entre_clientes(item.get("scopes") or [], item, sessao)
    prepare_errors = item.get("prepare_errors") if isinstance(item.get("prepare_errors"), list) else []
    try:
        prepare_progress = int(float(item.get("prepare_progress") or 0))
    except Exception:
        prepare_progress = 0
    prepare_progress = max(0, min(100, prepare_progress))
    try:
        prepare_done = int(float(item.get("prepare_done") or 0))
    except Exception:
        prepare_done = 0
    try:
        prepare_total = int(float(item.get("prepare_total") or 0))
    except Exception:
        prepare_total = 0
    return {
        "id": item.get("id"),
        "status": item.get("status") or "pending",
        "source_username": item.get("source_username") or "",
        "source_client_id": item.get("source_client_id") or "",
        "source_name": item.get("source_name") or "",
        "target_username": item.get("target_username") or "",
        "target_client_id": item.get("target_client_id") or "",
        "target_name": item.get("target_name") or "",
        "scopes": scopes,
        "scope_labels": [SHARED_SYNC_SCOPES[scope].get("label") or scope for scope in scopes],
        "message": item.get("message") or "",
        "source_keep_synced": bool(item.get("source_keep_synced")),
        "target_keep_synced": bool(item.get("target_keep_synced")),
        "prepare_status": item.get("prepare_status") or "ready",
        "prepare_progress": prepare_progress,
        "prepare_done": max(0, prepare_done),
        "prepare_total": max(0, prepare_total),
        "prepare_current_scope": item.get("prepare_current_scope") or "",
        "prepare_current_label": item.get("prepare_current_label") or "",
        "prepare_message": item.get("prepare_message") or "",
        "prepare_started_at": item.get("prepare_started_at") or "",
        "prepare_finished_at": item.get("prepare_finished_at") or "",
        "prepare_errors": prepare_errors[:10],
        "created_at": item.get("created_at") or "",
        "responded_at": item.get("responded_at") or "",
        "direction": (
            "sent" if sessao and _shared_sync_session_is_source(sessao, item)
            else "received" if sessao and _shared_sync_session_is_target(sessao, item)
            else ""
        ),
    }

def _shared_sync_link_sync_status_public(item: dict, sessao: Optional[dict], scopes: list[str]) -> dict:
    if not sessao:
        return {"scopes": {}, "pending_receive": 0}
    try:
        send_direction = _shared_sync_link_direction_for_session(sessao, item)
    except HTTPException:
        return {"scopes": {}, "pending_receive": 0}
    receive_direction = _shared_sync_link_reverse_direction(send_direction)
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    por_scope = {}
    pending_receive = 0
    last_sent_at = ""
    last_received_at = ""
    for scope in scopes:
        send_bundle = _shared_sync_link_bundle_id_for_direction(item, scope, send_direction)
        receive_bundle = _shared_sync_link_bundle_id_for_direction(item, scope, receive_direction)
        send_meta = _shared_sync_remote_meta_by_id(send_bundle) or {}
        receive_meta = _shared_sync_remote_meta_by_id(receive_bundle) or {}
        send_state_key = _shared_sync_user_share_state_scope(item.get("id"), send_direction, scope)
        receive_state_key = _shared_sync_user_share_state_scope(item.get("id"), receive_direction, scope)
        send_state = state_scopes.get(send_state_key) if isinstance(state_scopes.get(send_state_key), dict) else {}
        receive_state = state_scopes.get(receive_state_key) if isinstance(state_scopes.get(receive_state_key), dict) else {}
        remote_hash = str(receive_meta.get("snapshot_hash") or "")
        received_hash = str(receive_state.get("snapshot_hash") or "")
        has_pending = bool(receive_meta and remote_hash and remote_hash != received_hash)
        if has_pending:
            pending_receive += 1
        last_sent_at = max(last_sent_at, str(send_meta.get("updated_at") or ""))
        last_received_at = max(last_received_at, str(receive_state.get("synced_at") or ""))
        por_scope[scope] = {
            "label": (SHARED_SYNC_SCOPES.get(scope) or {}).get("label") or scope,
            "send_direction": send_direction,
            "receive_direction": receive_direction,
            "send": {
                "exists": bool(send_meta),
                "updated_at": send_meta.get("updated_at") or "",
                "updated_by": send_meta.get("updated_by") or "",
                "item_count": send_meta.get("item_count") or 0,
                "file_count": send_meta.get("file_count") or 0,
                "skipped": bool(send_state.get("skipped")),
                "state": send_state,
            },
            "receive": {
                "exists": bool(receive_meta),
                "updated_at": receive_meta.get("updated_at") or "",
                "updated_by": receive_meta.get("updated_by") or "",
                "item_count": receive_meta.get("item_count") or 0,
                "file_count": receive_meta.get("file_count") or 0,
                "pending": has_pending,
                "state": receive_state,
            },
            "known_count": _shared_sync_user_share_known_count(state, item.get("id"), scope),
        }
    return {
        "send_direction": send_direction,
        "receive_direction": receive_direction,
        "pending_receive": pending_receive,
        "last_sent_at": last_sent_at,
        "last_received_at": last_received_at,
        "scopes": por_scope,
    }

def _shared_sync_link_public(item: dict, sessao: Optional[dict] = None) -> dict:
    scopes = _shared_sync_filtrar_scopes_entre_clientes(item.get("scopes") or [], item, sessao)
    source = sessao and _shared_sync_session_is_source(sessao, item)
    target = sessao and _shared_sync_session_is_target(sessao, item)
    return {
        "id": item.get("id"),
        "invite_id": item.get("invite_id") or "",
        "active": bool(item.get("active", True)),
        "source_username": item.get("source_username") or "",
        "source_client_id": item.get("source_client_id") or "",
        "source_name": item.get("source_name") or "",
        "target_username": item.get("target_username") or "",
        "target_client_id": item.get("target_client_id") or "",
        "target_name": item.get("target_name") or "",
        "scopes": scopes,
        "scope_labels": [SHARED_SYNC_SCOPES[scope].get("label") or scope for scope in scopes],
        "source_keep_synced": bool(item.get("source_keep_synced")),
        "target_keep_synced": bool(item.get("target_keep_synced")),
        "auto_sync_enabled": bool(_shared_sync_auto_enabled() and item.get("active", True) and item.get("source_keep_synced") and item.get("target_keep_synced")),
        "manual_only": not _shared_sync_auto_enabled(),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "direction": "source" if source else "target" if target else "",
        "can_push": bool((source or target) and item.get("active", True)),
        "can_pull": bool((source or target) and item.get("active", True)),
        "sync_status": _shared_sync_link_sync_status_public(item, sessao, scopes),
    }

def _shared_sync_user_shares_for_session(sessao: dict) -> dict:
    invites_por_par = {}
    for item in _shared_sync_invites_all():
        item = _shared_sync_marcar_admin_origem(item)
        if str(item.get("status") or "").strip().lower() == "deleted":
            continue
        if _shared_sync_session_is_source(sessao, item) or _shared_sync_session_is_target(sessao, item):
            status = str(item.get("status") or "pending")
            key = (*_shared_sync_item_pair_key(item), status)
            atual = invites_por_par.get(key)
            if not atual or _shared_sync_item_timestamp(item) >= _shared_sync_item_timestamp(atual):
                invites_por_par[key] = item
    links_por_par = {}
    for item in _shared_sync_links_all():
        item = _shared_sync_marcar_admin_origem(item)
        if _shared_sync_session_is_source(sessao, item) or _shared_sync_session_is_target(sessao, item):
            key = _shared_sync_item_pair_key(item)
            atual = links_por_par.get(key)
            links_por_par[key] = _shared_sync_merge_link_items(atual, item) if atual else item
    invites = [_shared_sync_invite_public(item, sessao) for item in invites_por_par.values()]
    links = [_shared_sync_link_public(item, sessao) for item in links_por_par.values()]
    invites.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    links.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return {
        "success": True,
        "backend": "firebase" if _firebase_deve_usar() else "local",
        "scopes": {scope: _shared_sync_scope_public(scope) for scope in SHARED_SYNC_SCOPES},
        "invites": invites,
        "links": links,
    }

def _shared_sync_link_bundle_id(link: dict, scope: str) -> str:
    return _shared_sync_link_bundle_id_for_direction(link, scope, "source_to_target")

configure_shared_sync_user_links_runtime()

__all__ = [
    "configure_shared_sync_user_links_runtime",
    "_shared_sync_session_is_source",
    "_shared_sync_session_is_target",
    "_shared_sync_pair_key",
    "_shared_sync_item_pair_key",
    "_shared_sync_link_id_for_pair",
    "_shared_sync_link_direction_parts",
    "_shared_sync_link_direction_for_session",
    "_shared_sync_link_reverse_direction",
    "_shared_sync_lojas_integracoes_cross_client_volta_bloqueada",
    "_shared_sync_link_bundle_id_for_direction",
    "_shared_sync_link_set_bundle_for_direction",
    "_shared_sync_item_timestamp",
    "_shared_sync_unir_scopes",
    "_shared_sync_mesmo_par",
    "_shared_sync_find_pending_invite",
    "_shared_sync_find_active_link",
    "_shared_sync_merge_link_items",
    "_shared_sync_session_is_admin",
    "_shared_sync_permissoes_indicam_admin",
    "_shared_sync_usuario_identity_is_admin",
    "_shared_sync_marcar_admin_origem",
    "_shared_sync_source_is_admin",
    "_shared_sync_scope_permitido_entre_clientes",
    "_shared_sync_filtrar_scopes_entre_clientes",
    "_shared_sync_invite_public",
    "_shared_sync_link_sync_status_public",
    "_shared_sync_link_public",
    "_shared_sync_user_shares_for_session",
    "_shared_sync_link_bundle_id",
]
