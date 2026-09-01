"""Shared Sync snapshot hashing and bundle assembly helpers."""

from __future__ import annotations

import base64
import hashlib
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


def configure_shared_sync_bundle_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_snapshot_hash(entries: list[dict]) -> str:
    sha = hashlib.sha256()
    for item in entries:
        sha.update(str(item.get("relative_path") or "").encode("utf-8"))
        sha.update(b"\0")
        sha.update(str(item.get("size") or 0).encode("ascii"))
        sha.update(b"\0")
        sha.update(str(item.get("sha256") or "").encode("ascii"))
        sha.update(b"\n")
    return sha.hexdigest()


_SHARED_SYNC_TRANSIENT_OAUTH_KEYS = {
    "state", "code", "oauth_code", "authorization_code", "callback",
    "callback_url", "oauth_callback", "oauth_callback_url", "oauth_draft",
    "oauth_pending_state",
}


def _shared_sync_remove_transient_oauth(value):
    if isinstance(value, list):
        return [_shared_sync_remove_transient_oauth(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _shared_sync_remove_transient_oauth(item)
            for key, item in value.items()
            if str(key or "").strip().lower() not in _SHARED_SYNC_TRANSIENT_OAUTH_KEYS
        }
    return value


def _shared_sync_sanitize_transient_oauth_entries(scope: str, entries: list[dict]) -> list[dict]:
    if scope != "lojas_integracoes":
        return entries
    sanitized = []
    for item in entries:
        rel = str(item.get("relative_path") or "")
        if rel.lower() not in {"lojas_config.json", "integracoes.json"}:
            sanitized.append(item)
            continue
        data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
        try:
            payload = json.loads((data or b"").decode("utf-8-sig"))
            data = json.dumps(_shared_sync_remove_transient_oauth(payload), ensure_ascii=False, indent=2).encode("utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{rel} invalido para sincronizacao: {exc}")
        sanitized.append(_shared_sync_entry_from_bytes(rel, data, item.get("mtime") or time.time(), item.get("item_keys") or []))
    return sanitized

def _shared_sync_montar_pacote(
    client_id: str,
    scope: str,
    username: str,
    machine_id: str = "",
    user_only: bool = False,
    known_keys: Optional[set[str]] = None,
    sanitize_user_share_oauth: bool = False,
) -> tuple[bytes, dict, list[str]]:
    item_keys: list[str] = []
    if known_keys is None:
        entries, warnings = _shared_sync_coletar_arquivos(client_id, scope, username=username, user_only=user_only)
    else:
        entries, warnings, item_keys = _shared_sync_coletar_arquivos_delta(
            client_id,
            scope,
            username=username,
            user_only=user_only,
            known_keys=known_keys,
        )
    if sanitize_user_share_oauth and scope == "lojas_integracoes":
        sanitizadas = []
        for item in entries:
            rel = item.get("relative_path") or ""
            if rel.lower() != "lojas_config.json":
                sanitizadas.append(item)
                continue
            data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
            data = _shared_sync_sanitizar_lojas_integracoes_user_share_bytes(data or b"")
            sanitizadas.append(_shared_sync_entry_from_bytes(rel, data, item.get("mtime") or time.time(), item.get("item_keys") or []))
        entries = sanitizadas
    entries = _shared_sync_sanitize_transient_oauth_entries(scope, entries)
    manifest = {
        "schema": 2,
        "app": "JK Sistema",
        "scope": scope,
        "client_id": str(client_id or "default").strip() or "default",
        "created_at": _shared_sync_now_iso(),
        "created_by": str(username or "").strip().lower(),
        "machine_id": str(machine_id or "").strip(),
        "snapshot_hash": _shared_sync_snapshot_hash(entries),
        "file_count": len(entries),
        "delta": known_keys is not None,
        "item_count": len(item_keys),
        "item_keys": item_keys,
        "files": [
            {
                "relative_path": item["relative_path"],
                "size": item["size"],
                "mtime": item["mtime"],
                "sha256": item["sha256"],
                "item_count": len(item.get("item_keys") or []),
            }
            for item in entries
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for item in entries:
            data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
            zf.writestr("files/" + item["relative_path"], data or b"")
    bundle = buffer.getvalue()
    if len(bundle) > _shared_sync_max_bundle_bytes():
        raise HTTPException(
            status_code=413,
            detail="Pacote maior que o limite de sincronizacao. Reduza arquivos antigos ou aumente JK_SHARED_SYNC_MAX_BUNDLE_BYTES.",
        )
    return bundle, manifest, warnings

configure_shared_sync_bundle_runtime()

__all__ = [
    "configure_shared_sync_bundle_runtime",
    "_shared_sync_snapshot_hash",
    "_shared_sync_remove_transient_oauth",
    "_shared_sync_sanitize_transient_oauth_entries",
    "_shared_sync_montar_pacote",
]
