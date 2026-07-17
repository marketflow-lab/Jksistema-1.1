"""Shared Sync package application and pull helpers."""

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


def configure_shared_sync_apply_scope_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_favoritos_historico_legacy_json_username(rel: str) -> str:
    base = os.path.basename(str(rel or "")).lower()
    match = re.match(r"^favoritos_historico_(.+)\.json$", base)
    if not match:
        return ""
    return re.sub(r"[^a-z0-9_-]+", "_", match.group(1).strip()) or ""


def _shared_sync_aplicar_pacote(
    client_id: str,
    scope: str,
    bundle: bytes,
    username: str = "",
    scope_config: Optional[dict] = None,
) -> dict:
    tenant_path = get_tenant_path(client_id)
    tenant_abs = os.path.abspath(tenant_path)
    backup_dir = os.path.join(tenant_abs, "_shared_sync_backups", f"{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    escritos = []
    user_scoped_fontes: list[tuple[str, bytes]] = []
    user_share_fontes: list[tuple[str, bytes]] = []
    legacy_favoritos_fontes: list[tuple[str, bytes]] = []
    user_share = bool((scope_config or {}).get("user_share"))
    share_between_users = bool((scope_config or {}).get("share_between_users")) and bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped"))
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
        manifest_raw = zf.read("manifest.json")
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if str(manifest.get("scope") or "") != scope:
            raise HTTPException(status_code=400, detail="Backup remoto pertence a outro escopo.")
        for item in manifest.get("files") or []:
            rel = _shared_sync_relativo_seguro((item or {}).get("relative_path"))
            if _shared_sync_path_permanently_excluded(rel):
                raise HTTPException(status_code=400, detail="Arquivo permanentemente excluido do Shared Sync.")
            if not _shared_sync_scope_match(scope, rel):
                raise HTTPException(status_code=400, detail=f"Arquivo fora do escopo: {rel}")
            member = "files/" + rel
            try:
                data = zf.read(member)
            except KeyError:
                raise HTTPException(status_code=502, detail=f"Backup remoto sem arquivo esperado: {rel}")
            if scope == "lojas_integracoes" and rel == "lojas_config.json":
                # A operacao manual confirmada e autoritativa. Toda escrita passa
                # pelo lock, backup imediato, validacao e os.replace de Integracoes.
                try:
                    lojas = json.loads(data.decode("utf-8-sig"))
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=f"lojas_config.json remoto invalido: {exc}")
                if not isinstance(lojas, list):
                    raise HTTPException(status_code=502, detail="lojas_config.json remoto nao contem uma lista.")
                target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                from backend.services.integracoes import salvar_lojas
                salvar_lojas(client_id, lojas, permitir_reducao_confirmada=True)
                escritos.append(rel)
                continue
            if (
                scope == "favoritos_historico"
                and not user_share
                and not share_between_users
                and _shared_sync_favoritos_historico_legacy_json_username(rel)
            ):
                legacy_favoritos_fontes.append((rel, data))
                continue
            if user_share:
                user_share_fontes.append((rel, data))
                continue
            if share_between_users:
                user_scoped_fontes.append((rel, data))
                continue
            target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            os.makedirs(os.path.dirname(target_abs), exist_ok=True)
            with open(target_abs, "wb") as f:
                f.write(data)
            escritos.append(rel)
    for rel, data in legacy_favoritos_fontes:
        destino_username = _shared_sync_favoritos_historico_legacy_json_username(rel) or username
        target_rel = _shared_sync_target_rel_usuario(scope, destino_username, rel)
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, target_rel)
        _shared_sync_backup_target(tenant_abs, backup_dir, target_rel, target_abs)
        _shared_sync_merge_historico_usuario(client_id, destino_username, [(rel, data)])
        escritos.append(target_rel)
    if user_share:
        resultado_share = _shared_sync_aplicar_user_share_add_only(client_id, scope, username, user_share_fontes, tenant_abs, backup_dir)
        escritos.extend(resultado_share.get("files") or [])
    if share_between_users:
        resultado_share = _shared_sync_aplicar_user_scoped_share(client_id, scope, username, user_scoped_fontes, tenant_abs, backup_dir)
        escritos.extend(resultado_share.get("files") or [])
    if escritos:
        _shared_sync_prune_local_backups(tenant_abs)
    return {
        "file_count": len(escritos),
        "files": escritos[:250],
        "backup_dir": backup_dir if escritos else "",
    }

def _shared_sync_pull_scope(client_id: str, scope: str, sessao: dict, machine_id: str = "", scope_config: Optional[dict] = None) -> dict:
    bundle_id = _shared_sync_doc_id(client_id, scope)
    meta = _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    if _shared_sync_pull_already_current(client_id, sessao.get("username") or "", scope, meta):
        return _shared_sync_pull_skip_payload(scope, meta)
    bundle, meta = _shared_sync_obter_bundle_por_id(bundle_id, meta)
    result = _shared_sync_aplicar_pacote(client_id, scope, bundle, sessao.get("username") or "", scope_config)
    _shared_sync_state_update(client_id, sessao.get("username") or "", scope, meta, "pull")
    return {
        "scope": scope,
        "success": True,
        "direction": "pull",
        "file_count": result.get("file_count") or 0,
        "backup_dir": result.get("backup_dir") or "",
        "snapshot_hash": meta.get("snapshot_hash") or "",
        "remote_updated_at": meta.get("updated_at") or "",
        "remote_updated_by": meta.get("updated_by") or "",
        "remote_machine_id": meta.get("machine_id") or "",
    }

configure_shared_sync_apply_scope_runtime()

__all__ = [
    "configure_shared_sync_apply_scope_runtime",
    "_shared_sync_favoritos_historico_legacy_json_username",
    "_shared_sync_aplicar_pacote",
    "_shared_sync_pull_scope",
]
