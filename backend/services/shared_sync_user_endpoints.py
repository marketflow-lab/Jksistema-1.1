"""Shared Sync user-share FastAPI endpoint handlers."""

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
    SharedSyncPreviewRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserLinkRunRequest,
    SharedSyncUserLinkCreateRequest,
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_user_endpoints_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def shared_sync_listar_usuarios_destino(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    atual_username = _shared_sync_normalizar_username(sessao.get("username"))
    atual_client = _shared_sync_normalizar_client_id(sessao.get("client_id"))
    usuarios = []
    for item in _listar_usuarios_admin_sql():
        usuario = _shared_sync_usuario_publico(item)
        if not usuario.get("active"):
            continue
        if (
            _shared_sync_normalizar_username(usuario.get("username")) == atual_username
            and _shared_sync_normalizar_client_id(usuario.get("client_id")) == atual_client
        ):
            continue
        usuarios.append(usuario)
    usuarios.sort(key=lambda item: (_shared_sync_normalizar_client_id(item.get("client_id")), _shared_sync_normalizar_username(item.get("username"))))
    return {
        "success": True,
        "users": usuarios,
        "current_user": {
            "username": atual_username,
            "client_id": atual_client,
        },
    }

def shared_sync_user_shares_status(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    return _shared_sync_user_shares_for_session(sessao)


def _shared_sync_participant_session(username: str, client_id: str) -> dict:
    username_norm = _shared_sync_normalizar_username(username)
    client_norm = _shared_sync_normalizar_client_id(client_id)
    permissions = _carregar_permissoes_usuario(username_norm, client_norm)
    return {
        "username": username_norm,
        "client_id": client_norm,
        "permissions": permissions,
        "is_admin": bool(permissions.get("full") is True or permissions.get("admin_usuarios") is True),
    }


def _shared_sync_validate_link_scopes(sessao: dict, other: dict, requested: Optional[list[str]]) -> list[str]:
    scopes = _shared_sync_resolver_scopes_usuario(requested)
    other_session = _shared_sync_participant_session(other.get("username"), other.get("client_id"))
    denied = [
        scope for scope in scopes
        if not _shared_sync_machine_scope_allowed(scope, sessao)
        or not _shared_sync_machine_scope_allowed(scope, other_session)
    ]
    if denied:
        labels = ", ".join((SHARED_SYNC_SCOPES.get(scope) or {}).get("label") or scope for scope in denied)
        raise HTTPException(status_code=403, detail=f"Origem e destino precisam das permissoes normais destes modulos: {labels}.")
    return scopes


def _shared_sync_create_direct_link(payload, sessao: dict) -> dict:
    destino = _shared_sync_resolver_usuario_destino(payload.target_username, payload.target_client_id)
    if (
        _shared_sync_normalizar_username(destino.get("username")) == _shared_sync_normalizar_username(sessao.get("username"))
        and _shared_sync_normalizar_client_id(destino.get("client_id")) == _shared_sync_normalizar_client_id(sessao.get("client_id"))
    ):
        raise HTTPException(status_code=400, detail="Escolha outro usuario para receber os dados.")
    scopes = _shared_sync_validate_link_scopes(sessao, destino, payload.scopes)
    source_user = _shared_sync_usuario_publico(sessao.get("usuario") or {})
    source_user.update({
        "username": _shared_sync_normalizar_username(sessao.get("username")),
        "client_id": _shared_sync_normalizar_client_id(sessao.get("client_id")),
    })
    base = {
        "source_username": source_user.get("username"),
        "source_client_id": source_user.get("client_id"),
        "source_name": source_user.get("name") or source_user.get("username"),
        "target_username": destino.get("username"),
        "target_client_id": destino.get("client_id"),
        "target_name": destino.get("name") or destino.get("username"),
    }
    link_id = _shared_sync_link_id_for_pair(base)
    now = _shared_sync_now_iso()
    link = {
        **base,
        "id": link_id,
        "active": True,
        "schema": 2,
        "scopes": scopes,
        "bundles": {},
        "directional_bundles": {},
        "source_keep_synced": False,
        "target_keep_synced": False,
        "message": str(getattr(payload, "message", "") or "").strip()[:1000],
        "created_at": now,
        "created_ts": int(time.time()),
        "updated_at": now,
        "updated_ts": int(time.time()),
    }
    duplicates = []
    for existing in _shared_sync_links_all():
        if _shared_sync_item_pair_key(existing) != _shared_sync_item_pair_key(link):
            continue
        link = _shared_sync_merge_link_items(existing, link)
        if str(existing.get("id") or "") != link_id:
            duplicates.append(str(existing.get("id") or ""))
    link["id"] = link_id
    link["active"] = True
    link["schema"] = 2
    link["scopes"] = scopes
    link["source_keep_synced"] = False
    link["target_keep_synced"] = False
    link["updated_at"] = now
    link["updated_ts"] = int(time.time())
    saved = _shared_sync_save_link(link)
    for duplicate_id in duplicates:
        if duplicate_id:
            _shared_sync_delete_link_doc(duplicate_id)
    return {
        "success": True,
        "message": "Vinculo ativo criado. Nenhum dado foi enviado; use Enviar agora.",
        "link": _shared_sync_link_public(saved, sessao),
        "invite": None,
        "results": [],
        "manual_only": True,
    }


def shared_sync_user_shares_link_create(
    payload: SharedSyncUserLinkCreateRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    return _shared_sync_create_direct_link(payload, sessao)


def _shared_sync_migrate_v2_records():
    """Remove convites pendentes e consolida vínculos ativos sem enviar dados."""
    pending_deleted = 0
    for invite in _shared_sync_invites_all():
        if str(invite.get("status") or "pending").strip().lower() != "pending":
            continue
        result = _shared_sync_delete_invite_doc(str(invite.get("id") or ""))
        if result.get("local_deleted") or result.get("firebase_deleted"):
            pending_deleted += 1

    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for link in _shared_sync_links_all():
        if not bool(link.get("active", True)):
            continue
        groups.setdefault(_shared_sync_item_pair_key(link), []).append(link)

    duplicates_deleted = 0
    links_migrated = 0
    for items in groups.values():
        items.sort(key=_shared_sync_item_timestamp)
        merged: dict = {}
        for item in items:
            merged = _shared_sync_merge_link_items(merged, item)
        canonical_id = _shared_sync_link_id_for_pair(merged)
        merged["id"] = canonical_id
        merged["active"] = True
        merged["schema"] = 2
        merged["source_keep_synced"] = False
        merged["target_keep_synced"] = False
        merged["updated_at"] = _shared_sync_now_iso()
        merged["updated_ts"] = int(time.time())
        _shared_sync_save_link(merged)
        links_migrated += 1
        for item in items:
            old_id = str(item.get("id") or "")
            if old_id and old_id != canonical_id:
                result = _shared_sync_delete_link_doc(old_id)
                if result.get("local_deleted") or result.get("firebase_deleted"):
                    duplicates_deleted += 1

    return {
        "success": True,
        "manual_only": True,
        "pending_invites_deleted": pending_deleted,
        "active_links_migrated": links_migrated,
        "duplicate_links_deleted": duplicates_deleted,
        "message": "Migração v2 concluída sem publicar ou importar dados.",
    }


def admin_shared_sync_migrate_v2(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _shared_sync_require_admin(authorization, client_id)
    return _shared_sync_migrate_v2_records()

def shared_sync_user_shares_invite(
    payload: SharedSyncUserInviteCreateRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    # Adaptador temporario: nao cria convite pendente, nao prepara pacote e nao
    # exige aceite. Clientes antigos recebem imediatamente o vinculo ativo.
    return _shared_sync_create_direct_link(payload, sessao)
    # Codigo v1 abaixo e mantido apenas para leitura de instalacoes antigas.
    scopes = _shared_sync_resolver_scopes_usuario(payload.scopes)
    destino = _shared_sync_resolver_usuario_destino(payload.target_username, payload.target_client_id)
    source_user = _shared_sync_usuario_publico(sessao.get("usuario") or {})
    source_user["username"] = _shared_sync_normalizar_username(sessao.get("username"))
    source_user["client_id"] = _shared_sync_normalizar_client_id(sessao.get("client_id"))
    if (
        _shared_sync_normalizar_username(destino.get("username")) == _shared_sync_normalizar_username(sessao.get("username"))
        and _shared_sync_normalizar_client_id(destino.get("client_id")) == _shared_sync_normalizar_client_id(sessao.get("client_id"))
    ):
        raise HTTPException(status_code=400, detail="Escolha outro usuario para receber os dados.")
    active_link = _shared_sync_find_active_link(source_user, destino)
    if active_link:
        active_link = _shared_sync_marcar_admin_origem(active_link)
        active_link["source_is_admin"] = bool(active_link.get("source_is_admin") or _shared_sync_session_is_admin(sessao))
        active_link["source_keep_synced"] = bool(active_link.get("source_keep_synced") or payload.keep_synced)
        active_link["scopes"] = _shared_sync_unir_scopes(active_link.get("scopes"), scopes)
        active_link["updated_at"] = _shared_sync_now_iso()
        active_link["updated_ts"] = int(time.time())
        results = []
        for scope in _shared_sync_filtrar_scopes_entre_clientes(scopes, active_link, sessao):
            try:
                results.append(_shared_sync_push_link_scope(sessao, active_link, scope, payload.machine_id or ""))
            except Exception as exc:
                results.append({
                    "scope": scope,
                    "success": False,
                    "direction": "push",
                    "error": _shared_sync_exception_message(exc),
                })
        active_link = _shared_sync_save_link(active_link)
        return {
            "success": True,
            "message": "Compartilhamento ativo atualizado. Dados selecionados enviados.",
            "invite": None,
            "link": _shared_sync_link_public(active_link, sessao),
            "results": results,
        }
    pending_invite = _shared_sync_find_pending_invite(source_user, destino)
    if pending_invite:
        agora = _shared_sync_now_iso()
        pending_invite["scopes"] = _shared_sync_unir_scopes(pending_invite.get("scopes"), scopes)
        if str(payload.message or "").strip():
            pending_invite["message"] = str(payload.message or "").strip()[:1000]
        pending_invite["source_is_admin"] = bool(pending_invite.get("source_is_admin") or _shared_sync_session_is_admin(sessao))
        pending_invite["source_keep_synced"] = bool(pending_invite.get("source_keep_synced") or payload.keep_synced)
        pending_invite["bundles"] = {}
        pending_invite["directional_bundles"] = {}
        pending_invite["prepared_results"] = []
        pending_invite["prepare_status"] = "preparing"
        pending_invite["prepare_progress"] = 0
        pending_invite["prepare_done"] = 0
        pending_invite["prepare_total"] = len(pending_invite.get("scopes") or scopes)
        pending_invite["prepare_current_scope"] = ""
        pending_invite["prepare_current_label"] = ""
        pending_invite["prepare_message"] = "Aguardando início da preparação."
        pending_invite["prepare_started_at"] = agora
        pending_invite["prepare_finished_at"] = ""
        pending_invite["prepare_errors"] = []
        pending_invite["updated_at"] = agora
        pending_invite["updated_ts"] = int(time.time())
        pending_invite["source_machine_id"] = str(payload.machine_id or pending_invite.get("source_machine_id") or "").strip()
        pending_invite = _shared_sync_save_invite(pending_invite)
        _shared_sync_start_invite_prepare_thread(pending_invite.get("id") or "", sessao, destino, pending_invite.get("scopes") or scopes, payload.machine_id or "")
        return {
            "success": True,
            "message": "Convite pendente atualizado. Preparando os dados novamente em segundo plano.",
            "invite": _shared_sync_invite_public(pending_invite, sessao),
            "results": [],
        }

    invite_id = uuid.uuid4().hex

    agora = _shared_sync_now_iso()
    invite = {
        "id": invite_id,
        "status": "pending",
        "source_username": source_user.get("username"),
        "source_client_id": source_user.get("client_id"),
        "source_name": source_user.get("name") or source_user.get("username"),
        "source_is_admin": _shared_sync_session_is_admin(sessao),
        "target_username": destino.get("username"),
        "target_client_id": destino.get("client_id"),
        "target_name": destino.get("name") or destino.get("username"),
        "scopes": scopes,
        "bundles": {},
        "message": str(payload.message or "").strip()[:1000],
        "source_keep_synced": bool(payload.keep_synced),
        "target_keep_synced": False,
        "prepare_status": "preparing",
        "prepare_progress": 0,
        "prepare_done": 0,
        "prepare_total": len(scopes),
        "prepare_current_scope": "",
        "prepare_current_label": "",
        "prepare_message": "Aguardando início da preparação.",
        "prepare_started_at": agora,
        "prepare_finished_at": "",
        "prepare_errors": [],
        "created_at": agora,
        "created_ts": int(time.time()),
        "updated_at": agora,
        "updated_ts": int(time.time()),
        "source_machine_id": str(payload.machine_id or "").strip(),
    }
    invite = _shared_sync_save_invite(invite)
    _shared_sync_start_invite_prepare_thread(invite_id, sessao, destino, scopes, payload.machine_id or "")
    firebase_ok = invite.get("storage") == "firebase"
    return {
        "success": True,
        "message": (
            "Convite enviado. Preparando os dados em segundo plano."
            if firebase_ok
            else "Convite salvo localmente, mas o Firebase nao confirmou o envio para outra maquina."
        ),
        "invite": _shared_sync_invite_public(invite, sessao),
        "results": [],
    }

def shared_sync_user_shares_accept(
    invite_id: str,
    payload: SharedSyncUserInviteActionRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    raise HTTPException(status_code=410, detail="Aceite preliminar descontinuado; o vinculo ja nasce ativo.")
    sessao = _shared_sync_session(authorization, client_id)
    invite = _shared_sync_get_invite(invite_id)
    if not _shared_sync_session_is_target(sessao, invite):
        raise HTTPException(status_code=403, detail="Apenas o usuario de destino pode aceitar este convite.")
    if str(invite.get("status") or "pending") != "pending":
        raise HTTPException(status_code=400, detail="Este convite ja foi respondido.")
    prepare_status = str(invite.get("prepare_status") or "ready").strip().lower()
    if prepare_status == "preparing":
        raise HTTPException(status_code=409, detail="Convite ainda esta preparando os dados. Tente novamente em alguns instantes.")
    if prepare_status == "failed":
        raise HTTPException(status_code=400, detail="A origem nao conseguiu preparar os dados deste convite.")

    scopes = _shared_sync_resolver_scopes_usuario(invite.get("scopes") or [])
    scopes = _shared_sync_filtrar_scopes_entre_clientes(scopes, invite, sessao)
    bundles = invite.get("bundles") if isinstance(invite.get("bundles"), dict) else {}
    scopes = [scope for scope in scopes if bundles.get(scope)]
    if not scopes:
        raise HTTPException(status_code=400, detail="Convite sem pacote de dados disponivel para importar.")
    link_id = _shared_sync_link_id_for_pair(invite)
    link = {
        "id": link_id,
        "invite_id": invite.get("id"),
        "active": True,
        "source_username": invite.get("source_username"),
        "source_client_id": invite.get("source_client_id"),
        "source_name": invite.get("source_name") or invite.get("source_username"),
        "source_is_admin": bool(invite.get("source_is_admin") or _shared_sync_source_is_admin(invite)),
        "target_username": invite.get("target_username"),
        "target_client_id": invite.get("target_client_id"),
        "target_name": invite.get("target_name") or invite.get("target_username"),
        "scopes": scopes,
        "bundles": invite.get("bundles") if isinstance(invite.get("bundles"), dict) else {},
        "directional_bundles": invite.get("directional_bundles") if isinstance(invite.get("directional_bundles"), dict) else {"source_to_target": invite.get("bundles") if isinstance(invite.get("bundles"), dict) else {}},
        "source_keep_synced": bool(invite.get("source_keep_synced")),
        "target_keep_synced": bool(payload.keep_synced),
        "created_at": _shared_sync_now_iso(),
        "created_ts": int(time.time()),
        "updated_at": _shared_sync_now_iso(),
        "updated_ts": int(time.time()),
    }
    for existing in _shared_sync_links_all():
        if str(existing.get("id") or "") == link_id or _shared_sync_item_pair_key(existing) == _shared_sync_item_pair_key(link):
            link = _shared_sync_merge_link_items(existing, link)
            link["id"] = link_id
            link["active"] = True
            link["updated_at"] = _shared_sync_now_iso()
            link["updated_ts"] = int(time.time())
            break

    results = []
    for scope in scopes:
        results.append(_shared_sync_pull_pair_scope(sessao, link, scope))

    agora = _shared_sync_now_iso()
    invite["status"] = "accepted"
    invite["target_keep_synced"] = bool(payload.keep_synced)
    invite["responded_at"] = agora
    invite["responded_ts"] = int(time.time())
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    _shared_sync_save_invite(invite)
    _shared_sync_save_link(link)
    return {
        "success": True,
        "message": "Convite aceito e dados importados.",
        "invite": _shared_sync_invite_public(invite, sessao),
        "link": _shared_sync_link_public(link, sessao),
        "results": results,
    }

def shared_sync_user_shares_reject(
    invite_id: str,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    raise HTTPException(status_code=410, detail="Rejeicao de convite descontinuada; pause ou remova o vinculo.")
    sessao = _shared_sync_session(authorization, client_id)
    invite = _shared_sync_get_invite(invite_id)
    if not _shared_sync_session_is_target(sessao, invite):
        raise HTTPException(status_code=403, detail="Apenas o usuario de destino pode recusar este convite.")
    if str(invite.get("status") or "pending") != "pending":
        raise HTTPException(status_code=400, detail="Este convite ja foi respondido.")
    agora = _shared_sync_now_iso()
    invite["status"] = "rejected"
    invite["responded_at"] = agora
    invite["responded_ts"] = int(time.time())
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    _shared_sync_save_invite(invite)
    return {"success": True, "message": "Convite recusado.", "invite": _shared_sync_invite_public(invite, sessao)}

def shared_sync_user_shares_cancel(
    invite_id: str,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    invite = _shared_sync_get_invite(invite_id)
    if not (_shared_sync_session_is_source(sessao, invite) or _shared_sync_session_is_target(sessao, invite)):
        raise HTTPException(status_code=403, detail="Voce nao participa deste convite.")
    status_atual = str(invite.get("status") or "pending").strip().lower()
    if status_atual in {"cancelled", "canceled"}:
        return {"success": True, "message": "Convite ja estava cancelado.", "invite": _shared_sync_invite_public(invite, sessao)}
    if status_atual != "pending":
        raise HTTPException(status_code=400, detail="Apenas convites pendentes podem ser cancelados.")
    agora = _shared_sync_now_iso()
    invite["status"] = "cancelled"
    invite["prepare_status"] = "cancelled"
    invite["prepare_progress"] = 100
    invite["prepare_current_scope"] = ""
    invite["prepare_current_label"] = ""
    invite["prepare_message"] = "Convite cancelado pelo usuario."
    invite["cancelled_by"] = sessao.get("username") or ""
    invite["cancelled_at"] = agora
    invite["responded_at"] = invite.get("responded_at") or agora
    invite["responded_ts"] = int(time.time())
    invite["prepare_finished_at"] = invite.get("prepare_finished_at") or agora
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    saved = _shared_sync_save_invite(invite)
    firebase_ok = saved.get("storage") == "firebase" or not _firebase_deve_usar()
    return {
        "success": True,
        "message": "Convite cancelado." if firebase_ok else "Convite cancelado localmente, mas o Firebase nao confirmou a atualizacao.",
        "invite": _shared_sync_invite_public(saved, sessao),
    }

def shared_sync_user_shares_delete(
    invite_id: str,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    invite = _shared_sync_get_invite(invite_id)
    if not (_shared_sync_session_is_source(sessao, invite) or _shared_sync_session_is_target(sessao, invite)):
        raise HTTPException(status_code=403, detail="Voce nao participa deste convite.")
    agora = _shared_sync_now_iso()
    tombstone = dict(invite or {})
    tombstone["status"] = "deleted"
    tombstone["prepare_status"] = "deleted"
    tombstone["prepare_progress"] = 100
    tombstone["prepare_message"] = "Convite excluido da lista."
    tombstone["prepare_current_scope"] = ""
    tombstone["prepare_current_label"] = ""
    tombstone["prepare_finished_at"] = tombstone.get("prepare_finished_at") or agora
    tombstone["deleted_by"] = sessao.get("username") or ""
    tombstone["deleted_at"] = agora
    tombstone["updated_at"] = agora
    tombstone["updated_ts"] = int(time.time())
    _shared_sync_save_invite(tombstone)
    resultado = _shared_sync_delete_invite_doc(invite_id)
    firebase_ok = bool(resultado.get("firebase_deleted")) or not _firebase_deve_usar()
    if not firebase_ok:
        _shared_sync_save_invite(tombstone)
    return {
        "success": True,
        "message": "Convite excluido da lista." if firebase_ok else "Convite excluido localmente, mas o Firebase nao confirmou a atualizacao.",
        "delete": resultado,
    }

def shared_sync_user_shares_link_update(
    link_id: str,
    payload: SharedSyncUserLinkUpdateRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    link = _shared_sync_get_link(link_id)
    is_source = _shared_sync_session_is_source(sessao, link)
    is_target = _shared_sync_session_is_target(sessao, link)
    if not (is_source or is_target):
        raise HTTPException(status_code=403, detail="Voce nao participa deste compartilhamento.")
    if payload.keep_synced is not None:
        if is_source:
            link["source_keep_synced"] = bool(payload.keep_synced)
        if is_target:
            link["target_keep_synced"] = bool(payload.keep_synced)
    if payload.active is not None and payload.active is False:
        link["active"] = False
        link["stopped_by"] = sessao.get("username")
        link["stopped_at"] = _shared_sync_now_iso()
    elif payload.active is not None and payload.active is True:
        link["active"] = True
    link["updated_at"] = _shared_sync_now_iso()
    link["updated_ts"] = int(time.time())
    _shared_sync_save_link(link)
    return {"success": True, "message": "Preferencias atualizadas.", "link": _shared_sync_link_public(link, sessao)}


def _shared_sync_link_bundle_ids(link: dict, sessao: dict, scopes: list[str], direction: str) -> dict[str, str]:
    my_direction = _shared_sync_link_direction_for_session(sessao, link)
    transfer_direction = my_direction if direction == "push" else _shared_sync_link_reverse_direction(my_direction)
    return {
        scope: _shared_sync_link_bundle_id_for_direction(link, scope, transfer_direction)
        for scope in scopes
    }


def _shared_sync_validate_existing_link_scopes(sessao: dict, link: dict, requested: Optional[list[str]]) -> list[str]:
    scopes = _shared_sync_resolver_scopes_usuario(requested or link.get("scopes") or [])
    if any(scope not in (link.get("scopes") or []) for scope in scopes):
        raise HTTPException(status_code=403, detail="A operacao pediu um modulo fora deste vinculo.")
    other = (
        {"username": link.get("target_username"), "client_id": link.get("target_client_id")}
        if _shared_sync_session_is_source(sessao, link)
        else {"username": link.get("source_username"), "client_id": link.get("source_client_id")}
    )
    return _shared_sync_validate_link_scopes(sessao, other, scopes)


def shared_sync_user_shares_link_preview(
    link_id: str,
    payload: SharedSyncPreviewRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    link = _shared_sync_get_link(link_id)
    if not bool(link.get("active", True)):
        raise HTTPException(status_code=400, detail="Compartilhamento pausado.")
    direction = _shared_sync_operation_direction(payload.direction)
    scopes = _shared_sync_validate_existing_link_scopes(sessao, link, payload.scopes)
    bundle_ids = _shared_sync_link_bundle_ids(link, sessao, scopes, direction)
    if direction == "pull":
        missing = [scope for scope, bundle_id in bundle_ids.items() if not _shared_sync_remote_meta_by_id(bundle_id)]
        if missing:
            raise HTTPException(status_code=404, detail="A outra parte ainda nao publicou snapshot para todos os modulos selecionados.")
    return _shared_sync_create_preview(
        sessao,
        kind="user-link",
        resource_id=link_id,
        direction=direction,
        scopes=scopes,
        bundle_ids=bundle_ids,
        machine_id=payload.machine_id or "",
    )

def shared_sync_user_shares_link_push(
    link_id: str,
    payload: SharedSyncUserLinkRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    link = _shared_sync_get_link(link_id)
    if not bool(link.get("active", True)):
        raise HTTPException(status_code=400, detail="Compartilhamento pausado.")
    _shared_sync_link_direction_for_session(sessao, link)
    scopes = _shared_sync_validate_existing_link_scopes(sessao, link, payload.scopes)
    bundle_ids = _shared_sync_link_bundle_ids(link, sessao, scopes, "push")
    operation = _shared_sync_require_operation(
        payload.operation_id, sessao, kind="user-link", resource_id=link_id,
        direction="push", scopes=scopes, bundle_ids=bundle_ids,
    )
    results = [_shared_sync_push_link_scope(sessao, link, scope, payload.machine_id or "") for scope in scopes]
    _shared_sync_audit(sessao, record=operation, results=results, link_id=link_id)
    return {"success": True, "direction": "push", "results": results, "link": _shared_sync_link_public(link, sessao)}

def shared_sync_user_shares_link_pull(
    link_id: str,
    payload: SharedSyncUserLinkRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    link = _shared_sync_get_link(link_id)
    if not bool(link.get("active", True)):
        raise HTTPException(status_code=400, detail="Compartilhamento pausado.")
    _shared_sync_link_direction_for_session(sessao, link)
    scopes = _shared_sync_validate_existing_link_scopes(sessao, link, payload.scopes)
    bundle_ids = _shared_sync_link_bundle_ids(link, sessao, scopes, "pull")
    operation = _shared_sync_require_operation(
        payload.operation_id, sessao, kind="user-link", resource_id=link_id,
        direction="pull", scopes=scopes, bundle_ids=bundle_ids,
    )
    results = [_shared_sync_pull_pair_scope(sessao, link, scope) for scope in scopes]
    _shared_sync_audit(sessao, record=operation, results=results, link_id=link_id)
    return {"success": True, "direction": "pull", "results": results, "link": _shared_sync_link_public(link, sessao)}

def shared_sync_user_shares_auto_push(
    payload: SharedSyncUserLinkRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    if not _shared_sync_auto_enabled():
        return _shared_sync_manual_only_payload("auto-push")
    limitado = _shared_sync_auto_rate_limit("user-shares-auto-push", sessao, payload.machine_id or "")
    if limitado:
        return {"success": True, "direction": "auto-push", "results": [], "skipped": [limitado]}
    requested = set(str(scope or "").strip() for scope in (payload.scopes or []) if str(scope or "").strip())
    results = []
    skipped = []
    for link in _shared_sync_links_all():
        try:
            _shared_sync_link_direction_for_session(sessao, link)
        except HTTPException:
            continue
        if not bool(link.get("active", True)):
            skipped.append({"link_id": link.get("id"), "reason": "inactive"})
            continue
        if not (link.get("source_keep_synced") and link.get("target_keep_synced")):
            skipped.append({"link_id": link.get("id"), "reason": "sync_disabled_by_participant"})
            continue
        for scope in link.get("scopes") or []:
            if scope not in SHARED_SYNC_SCOPES:
                continue
            if not _shared_sync_scope_permitido_entre_clientes(link, scope, sessao):
                skipped.append({"link_id": link.get("id"), "scope": scope, "reason": "cross_client_sensitive_scope"})
                continue
            if requested and scope not in requested:
                continue
            result = _shared_sync_push_link_scope(sessao, link, scope, payload.machine_id or "", skip_if_remote_hash_matches=True)
            if result.get("skipped"):
                skipped.append({"link_id": link.get("id"), "scope": scope, "reason": result.get("reason") or "already_shared"})
                continue
            results.append(result)
    return {"success": True, "direction": "auto-push", "results": results, "skipped": skipped}

def shared_sync_user_shares_auto(
    payload: SharedSyncUserLinkRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    if not _shared_sync_auto_enabled():
        return _shared_sync_manual_only_payload("user-share-auto")
    limitado = _shared_sync_auto_rate_limit("user-shares-auto", sessao, payload.machine_id or "")
    if limitado:
        return {"success": True, "direction": "user-share-auto", "results": [], "skipped": [limitado]}
    requested = set(str(scope or "").strip() for scope in (payload.scopes or []) if str(scope or "").strip())
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    results = []
    skipped = []
    for link in _shared_sync_links_all():
        try:
            my_direction = _shared_sync_link_direction_for_session(sessao, link)
        except HTTPException:
            continue
        if not bool(link.get("active", True)):
            skipped.append({"link_id": link.get("id"), "reason": "inactive"})
            continue
        if not (link.get("source_keep_synced") and link.get("target_keep_synced")):
            skipped.append({"link_id": link.get("id"), "reason": "sync_disabled_by_participant"})
            continue
        receive_direction = _shared_sync_link_reverse_direction(my_direction)
        for scope in link.get("scopes") or []:
            if scope not in SHARED_SYNC_SCOPES:
                continue
            if requested and scope not in requested:
                continue
            if not _shared_sync_scope_permitido_entre_clientes(link, scope, sessao):
                skipped.append({"link_id": link.get("id"), "scope": scope, "reason": "cross_client_sensitive_scope"})
                continue

            receive_bundle = _shared_sync_link_bundle_id_for_direction(link, scope, receive_direction)
            meta = _shared_sync_remote_meta_by_id(receive_bundle)
            if meta:
                remote_hash = str(meta.get("snapshot_hash") or "")
                state_scope = _shared_sync_user_share_state_scope(link.get("id"), receive_direction, scope)
                local_hash = str(((state_scopes.get(state_scope) or {}).get("snapshot_hash")) or "")
                if remote_hash and remote_hash != local_hash:
                    try:
                        results.append(_shared_sync_pull_pair_scope(sessao, link, scope))
                    except Exception as exc:
                        skipped.append({"link_id": link.get("id"), "scope": scope, "direction": "pull", "reason": _shared_sync_exception_message(exc)})
                else:
                    skipped.append({"link_id": link.get("id"), "scope": scope, "direction": "pull", "reason": "already_current"})
            else:
                skipped.append({"link_id": link.get("id"), "scope": scope, "direction": "pull", "reason": "no_remote"})

            try:
                result = _shared_sync_push_link_scope(sessao, link, scope, payload.machine_id or "", skip_if_remote_hash_matches=True)
                if result.get("skipped"):
                    skipped.append({"link_id": link.get("id"), "scope": scope, "direction": "push", "reason": result.get("reason") or "already_shared"})
                else:
                    results.append(result)
            except Exception as exc:
                skipped.append({"link_id": link.get("id"), "scope": scope, "direction": "push", "reason": _shared_sync_exception_message(exc)})
    return {"success": True, "direction": "user-share-auto", "results": results, "skipped": skipped}

configure_shared_sync_user_endpoints_runtime()

__all__ = [
    "configure_shared_sync_user_endpoints_runtime",
    "shared_sync_listar_usuarios_destino",
    "shared_sync_user_shares_status",
    "shared_sync_user_shares_link_create",
    "_shared_sync_migrate_v2_records",
    "admin_shared_sync_migrate_v2",
    "shared_sync_user_shares_invite",
    "shared_sync_user_shares_accept",
    "shared_sync_user_shares_reject",
    "shared_sync_user_shares_cancel",
    "shared_sync_user_shares_delete",
    "shared_sync_user_shares_link_update",
    "shared_sync_user_shares_link_preview",
    "shared_sync_user_shares_link_push",
    "shared_sync_user_shares_link_pull",
    "shared_sync_user_shares_auto_push",
    "shared_sync_user_shares_auto",
]
