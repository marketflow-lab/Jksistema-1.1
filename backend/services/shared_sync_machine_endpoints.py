"""Shared Sync machine, admin config, and direct sync endpoint handlers."""

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
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_machine_endpoints_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def shared_sync_machine_status(
    machine_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    return _shared_sync_machine_status_payload(sessao, machine_id or "")

def shared_sync_machine_salvar_config(
    payload: SharedSyncMachineConfigRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    config = _shared_sync_machine_config_save(sessao, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    return _shared_sync_machine_status_payload(sessao, "")


def _shared_sync_machine_bundle_ids(sessao: dict, scopes: list[str]) -> dict[str, str]:
    return {
        scope: _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope)
        for scope in scopes
    }


def shared_sync_machine_preview(
    payload: SharedSyncPreviewRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    direction = _shared_sync_operation_direction(payload.direction)
    scopes = _shared_sync_machine_resolver_scopes(sessao, payload.scopes, require_enabled=True)
    bundle_ids = _shared_sync_machine_bundle_ids(sessao, scopes)
    if direction == "pull":
        missing = [scope for scope, bundle_id in bundle_ids.items() if not _shared_sync_remote_meta_by_id(bundle_id)]
        if missing:
            raise HTTPException(status_code=404, detail="Ainda nao existe snapshot remoto para todos os modulos selecionados.")
    return _shared_sync_create_preview(
        sessao,
        kind="machine",
        resource_id="self",
        direction=direction,
        scopes=scopes,
        bundle_ids=bundle_ids,
        machine_id=payload.machine_id or "",
    )

def shared_sync_machine_push(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    scopes = _shared_sync_machine_resolver_scopes(sessao, payload.scopes, require_enabled=True)
    bundle_ids = _shared_sync_machine_bundle_ids(sessao, scopes)
    operation = _shared_sync_require_operation(
        payload.operation_id, sessao, kind="machine", resource_id="self",
        direction="push", scopes=scopes, bundle_ids=bundle_ids,
    )
    results = [_shared_sync_machine_push_scope(sessao, scope, payload.machine_id or "") for scope in scopes]
    _shared_sync_audit(sessao, record=operation, results=results)
    return {"success": True, "direction": "machine-push", "results": results}

def shared_sync_machine_pull(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    scopes = _shared_sync_machine_resolver_scopes(sessao, payload.scopes, require_enabled=True)
    bundle_ids = _shared_sync_machine_bundle_ids(sessao, scopes)
    operation = _shared_sync_require_operation(
        payload.operation_id, sessao, kind="machine", resource_id="self",
        direction="pull", scopes=scopes, bundle_ids=bundle_ids,
    )
    # O clique em "Importar agora" e uma ordem manual confirmada por previa.
    # Nao confie apenas no hash historico: o arquivo local pode ter sumido ou
    # divergido depois da ultima sincronizacao.
    results = [_shared_sync_machine_pull_scope(sessao, scope, force=True) for scope in scopes]
    _shared_sync_audit(sessao, record=operation, results=results)
    return {"success": True, "direction": "machine-pull", "results": results}

def shared_sync_machine_auto(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    if not _shared_sync_auto_enabled():
        return _shared_sync_manual_only_payload("machine-auto")
    limitado = _shared_sync_auto_rate_limit("machine-auto", sessao, payload.machine_id or "")
    if limitado:
        return {"success": True, "direction": "machine-auto", "results": [], "skipped": [limitado]}
    return _shared_sync_machine_auto_run(sessao, payload.machine_id or "", payload.scopes)

def admin_shared_sync_config(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _shared_sync_require_admin(authorization, client_id)
    return _shared_sync_status_payload(client_id)

def admin_shared_sync_salvar_config(
    payload: SharedSyncConfigRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_require_admin(authorization, client_id)
    config = _shared_sync_config_save(client_id, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), sessao.get("username"))
    return _shared_sync_status_payload(client_id, config)

def shared_sync_status(
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    payload = _shared_sync_status_payload(client_id)
    for scope, item in list((payload.get("scopes") or {}).items()):
        item["allowed"] = _shared_sync_user_allowed(payload.get("config") or {}, scope, sessao)
    return payload

def shared_sync_auto_pull(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _shared_sync_session(authorization, client_id)
    if not _shared_sync_auto_enabled():
        return _shared_sync_manual_only_payload("auto-pull")
    limitado = _shared_sync_auto_rate_limit("auto-pull", sessao, payload.machine_id or "")
    if limitado:
        return {"success": True, "direction": "auto-pull", "results": [], "skipped": [limitado]}
    config = _shared_sync_config_read(client_id)
    state = _shared_sync_state_read(client_id, sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    requested = set(str(scope or "").strip() for scope in (payload.scopes or []) if str(scope or "").strip())
    resultados = []
    ignorados = []
    for scope, scope_cfg in (config.get("scopes") or {}).items():
        if scope not in SHARED_SYNC_SCOPES:
            continue
        if requested and scope not in requested:
            continue
        if not scope_cfg.get("auto_pull", True):
            ignorados.append({"scope": scope, "reason": "auto_pull_disabled"})
            continue
        if not _shared_sync_user_allowed(config, scope, sessao):
            ignorados.append({"scope": scope, "reason": "not_allowed"})
            continue
        meta = _shared_sync_remote_meta(client_id, scope)
        if not meta:
            ignorados.append({"scope": scope, "reason": "no_remote"})
            continue
        remote_hash = str(meta.get("snapshot_hash") or "")
        local_hash = str(((state_scopes.get(scope) or {}).get("snapshot_hash")) or "")
        if remote_hash and local_hash == remote_hash:
            ignorados.append({"scope": scope, "reason": "already_current", "snapshot_hash": remote_hash})
            continue
        try:
            resultados.append(_shared_sync_pull_scope(client_id, scope, sessao, payload.machine_id or "", scope_cfg))
        except HTTPException as exc:
            ignorados.append({
                "scope": scope,
                "reason": "pull_failed",
                "status_code": exc.status_code,
                "detail": exc.detail,
            })

    for link in _shared_sync_links_all():
        try:
            my_direction = _shared_sync_link_direction_for_session(sessao, link)
        except HTTPException:
            continue
        if not bool(link.get("active", True)):
            ignorados.append({"link_id": link.get("id"), "reason": "user_share_inactive"})
            continue
        if not (link.get("source_keep_synced") and link.get("target_keep_synced")):
            ignorados.append({"link_id": link.get("id"), "reason": "user_share_sync_disabled_by_participant"})
            continue
        receive_direction = _shared_sync_link_reverse_direction(my_direction)
        for scope in link.get("scopes") or []:
            if scope not in SHARED_SYNC_SCOPES:
                continue
            if not _shared_sync_scope_permitido_entre_clientes(link, scope, sessao):
                ignorados.append({"link_id": link.get("id"), "scope": scope, "reason": "user_share_cross_client_sensitive_scope"})
                continue
            if requested and scope not in requested:
                continue
            bundle_id = _shared_sync_link_bundle_id_for_direction(link, scope, receive_direction)
            meta = _shared_sync_remote_meta_by_id(bundle_id)
            if not meta:
                ignorados.append({"link_id": link.get("id"), "scope": scope, "reason": "user_share_no_remote"})
                continue
            remote_hash = str(meta.get("snapshot_hash") or "")
            state_scope = _shared_sync_user_share_state_scope(link.get("id"), receive_direction, scope)
            local_hash = str(((state_scopes.get(state_scope) or {}).get("snapshot_hash")) or "")
            if remote_hash and local_hash == remote_hash:
                ignorados.append({"link_id": link.get("id"), "scope": scope, "reason": "already_current", "snapshot_hash": remote_hash})
                continue
            try:
                resultados.append(_shared_sync_pull_pair_scope(sessao, link, scope))
            except HTTPException as exc:
                ignorados.append({
                    "link_id": link.get("id"),
                    "scope": scope,
                    "reason": "user_share_pull_failed",
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                })
    return {
        "success": True,
        "direction": "auto-pull",
        "results": resultados,
        "skipped": ignorados,
    }

def shared_sync_push(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _shared_sync_session(authorization, client_id)
    return _shared_sync_manual_only_payload("legacy-push-use-machine-preview")

def shared_sync_pull(
    payload: SharedSyncRunRequest,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _shared_sync_session(authorization, client_id)
    return _shared_sync_manual_only_payload("legacy-pull-use-machine-preview")

configure_shared_sync_machine_endpoints_runtime()

__all__ = [
    "configure_shared_sync_machine_endpoints_runtime",
    "shared_sync_machine_status",
    "shared_sync_machine_salvar_config",
    "shared_sync_machine_preview",
    "shared_sync_machine_push",
    "shared_sync_machine_pull",
    "shared_sync_machine_auto",
    "admin_shared_sync_config",
    "admin_shared_sync_salvar_config",
    "shared_sync_status",
    "shared_sync_auto_pull",
    "shared_sync_push",
    "shared_sync_pull",
]
