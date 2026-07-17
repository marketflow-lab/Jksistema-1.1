"""Shared Sync machine-to-machine helpers."""

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


def configure_shared_sync_machine_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_machine_state_scope(scope: str) -> str:
    return f"machine-sync:{scope}"

def _shared_sync_machine_remote_meta(sessao: dict, scope: str) -> Optional[dict]:
    return _shared_sync_remote_meta_by_id(_shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope))

def _shared_sync_machine_push_scope(sessao: dict, scope: str, machine_id: str = "", skip_if_remote_hash_matches: bool = False) -> dict:
    bundle_id = _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope)
    return _shared_sync_push_scope(
        sessao.get("client_id"),
        scope,
        sessao,
        machine_id,
        bundle_id=bundle_id,
        extra_meta={
            "visibility": "machine-sync",
            "owner_client_id": _shared_sync_normalizar_client_id(sessao.get("client_id")),
            "owner_username": _shared_sync_normalizar_username(sessao.get("username")),
        },
        user_only=True,
        state_scope=_shared_sync_machine_state_scope(scope),
        skip_if_remote_hash_matches=skip_if_remote_hash_matches,
    )

def _shared_sync_machine_pull_scope(sessao: dict, scope: str, *, force: bool = False) -> dict:
    bundle_id = _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope)
    meta = _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    state_scope = _shared_sync_machine_state_scope(scope)
    # A importacao manual vem depois de uma previa confirmada pelo usuario e
    # precisa reaplicar o snapshot. O estado historico pode dizer que o hash ja
    # foi recebido mesmo quando o arquivo local foi removido, substituido ou
    # gravado em outra copia do app. O skip continua valido apenas para rotinas
    # automaticas/idempotentes que nao foram explicitamente solicitadas.
    if not force and _shared_sync_pull_already_current(
        sessao.get("client_id"), sessao.get("username") or "", state_scope, meta,
    ):
        return _shared_sync_pull_skip_payload(scope, meta)
    bundle, meta = _shared_sync_obter_bundle_por_id(bundle_id, meta)
    scope_config = {"share_between_users": bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped"))}
    result = _shared_sync_aplicar_pacote(sessao.get("client_id"), scope, bundle, sessao.get("username") or "", scope_config)
    _shared_sync_state_update(sessao.get("client_id"), sessao.get("username") or "", state_scope, meta, "pull")
    return {
        "scope": scope,
        "success": True,
        "direction": "pull",
        "file_count": result.get("file_count") or 0,
        "stores_count": int(result.get("stores_count") or 0),
        "backup_dir": result.get("backup_dir") or "",
        "snapshot_hash": meta.get("snapshot_hash") or "",
        "remote_updated_at": meta.get("updated_at") or "",
        "remote_updated_by": meta.get("updated_by") or "",
        "remote_machine_id": meta.get("machine_id") or "",
    }

def _shared_sync_machine_status_payload(sessao: dict, machine_id: str = "") -> dict:
    config = _shared_sync_machine_config_read(sessao)
    permitidos = set(_shared_sync_machine_allowed_scopes(sessao))
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    scopes = {}
    for scope in SHARED_SYNC_SCOPES:
        meta = _shared_sync_machine_remote_meta(sessao, scope) or {}
        state_key = _shared_sync_machine_state_scope(scope)
        scopes[scope] = {
            **_shared_sync_scope_public(scope),
            "allowed": scope in permitidos,
            "selected": scope in (config.get("scopes") or []),
            "state": state_scopes.get(state_key) or {},
            "remote": {
                "exists": bool(meta),
                "updated_at": meta.get("updated_at") or "",
                "updated_by": meta.get("updated_by") or "",
                "machine_id": meta.get("machine_id") or "",
                "file_count": meta.get("file_count") or 0,
                "stores_count": meta.get("stores_count") or 0,
                "bundle_bytes": meta.get("bundle_bytes") or 0,
                "chunk_count": meta.get("chunk_count") or 0,
                "warnings": meta.get("warnings") or [],
            },
        }
    maquinas = _machine_presence_list(sessao.get("username"), sessao.get("client_id"))
    maquinas = _machine_presence_mark_current(maquinas, machine_id or "")
    return {
        "success": True,
        "backend": "firebase" if _firebase_deve_usar() else "local",
        "current_user": {
            "username": sessao.get("username"),
            "client_id": sessao.get("client_id"),
        },
        "config": config,
        "scopes": scopes,
        "machines": maquinas[:20],
        "online_count": len([m for m in maquinas if m.get("online")]),
    }

def _shared_sync_machine_auto_run(sessao: dict, machine_id: str = "", requested: Optional[list[str]] = None) -> dict:
    config = _shared_sync_machine_config_read(sessao)
    if not config.get("enabled"):
        return {"success": True, "direction": "machine-auto", "results": [], "skipped": [{"reason": "disabled"}]}
    scopes = _shared_sync_machine_resolver_scopes(sessao, requested, require_enabled=True)
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    results = []
    skipped = []
    for scope in scopes:
        state_key = _shared_sync_machine_state_scope(scope)
        remote = _shared_sync_machine_remote_meta(sessao, scope) or {}
        remote_hash = str(remote.get("snapshot_hash") or "")
        state_hash = str(((state_scopes.get(state_key) or {}).get("snapshot_hash")) or "")
        remote_machine = str(remote.get("machine_id") or "").strip()
        current_machine = str(machine_id or "").strip()

        if config.get("auto_pull", True) and remote and remote_hash and state_hash != remote_hash and remote_machine != current_machine:
            results.append(_shared_sync_machine_pull_scope(sessao, scope))
            remote = _shared_sync_machine_remote_meta(sessao, scope) or remote
            remote_hash = str(remote.get("snapshot_hash") or remote_hash)

        if not config.get("auto_push", True):
            skipped.append({"scope": scope, "reason": "auto_push_disabled"})
            continue

        entries, _warnings = _shared_sync_coletar_arquivos(sessao.get("client_id"), scope, username=sessao.get("username"), user_only=True)
        local_hash = _shared_sync_snapshot_hash(entries)
        if remote_hash and local_hash == remote_hash:
            skipped.append({"scope": scope, "reason": "already_current", "snapshot_hash": remote_hash})
            continue
        results.append(_shared_sync_machine_push_scope(sessao, scope, machine_id, skip_if_remote_hash_matches=True))
    return {"success": True, "direction": "machine-auto", "results": results, "skipped": skipped}

def _shared_sync_status_payload(client_id: str, config: Optional[dict] = None) -> dict:
    cfg = config or _shared_sync_config_read(client_id)
    scopes = {}
    for scope in SHARED_SYNC_SCOPES:
        meta = _shared_sync_remote_meta(client_id, scope) or {}
        scopes[scope] = {
            **_shared_sync_scope_public(scope),
            "config": ((cfg.get("scopes") or {}).get(scope) or _shared_sync_normalizar_scope_config(scope, {})),
            "remote": {
                "exists": bool(meta),
                "updated_at": meta.get("updated_at") or "",
                "updated_by": meta.get("updated_by") or "",
                "machine_id": meta.get("machine_id") or "",
                "file_count": meta.get("file_count") or 0,
                "bundle_bytes": meta.get("bundle_bytes") or 0,
                "chunk_count": meta.get("chunk_count") or 0,
                "warnings": meta.get("warnings") or [],
            },
        }
    return {
        "success": True,
        "backend": "firebase" if _firebase_deve_usar() else "local",
        "client_id": str(client_id or "default").strip() or "default",
        "config": cfg,
        "scopes": scopes,
    }

configure_shared_sync_machine_runtime()

__all__ = [
    "configure_shared_sync_machine_runtime",
    "_shared_sync_machine_state_scope",
    "_shared_sync_machine_remote_meta",
    "_shared_sync_machine_push_scope",
    "_shared_sync_machine_pull_scope",
    "_shared_sync_machine_status_payload",
    "_shared_sync_machine_auto_run",
    "_shared_sync_status_payload",
]
