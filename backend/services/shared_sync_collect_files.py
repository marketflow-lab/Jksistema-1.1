"""Shared Sync file discovery and entry helpers."""

from __future__ import annotations

import base64
import fnmatch
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


def configure_shared_sync_collect_files_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_user_scoped_rels(scope: str, username: str) -> list[str]:
    slug = _favoritos_usuario_slug(username)
    if scope == "favoritos_historico":
        return [f"favoritos_historico_{slug}.db"]
    if scope == "sku_campos_pesquisa":
        return [f"favoritos_pesquisas_{slug}.json"]
    if scope == "anuncios_ml":
        return [
            f"favoritos_anuncios_ignorados_{slug}.json",
            f"favoritos_vendedores_ignorados_{slug}.json",
            f"favoritos_skus_ocultos_{slug}.json",
        ]
    return []

def _shared_sync_scope_match(scope: str, rel_path: str) -> bool:
    info = SHARED_SYNC_SCOPES.get(scope) or {}
    rel = _shared_sync_relativo_seguro(rel_path).lower()
    if _shared_sync_path_permanently_excluded(rel):
        return False
    for pattern in info.get("patterns") or []:
        pat = str(pattern or "").replace("\\", "/").strip().lower()
        if not pat:
            continue
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/") + "/"
            if rel.startswith(prefix):
                return True
        elif fnmatch.fnmatch(rel, pat):
            return True
    return False

def _shared_sync_sqlite_ext(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in {".db", ".sqlite", ".sqlite3"}

def _shared_sync_sqlite_backup_bytes(path: str) -> bytes:
    abs_path = os.path.abspath(str(path or ""))
    if not os.path.exists(abs_path):
        return b""

    def _backup() -> bytes:
        tmp_path = f"{abs_path}.sharedsync_{uuid.uuid4().hex}.tmp"
        src = None
        dst = None
        try:
            with _shared_sync_sqlite_lock_for_path(abs_path):
                timeout_s = max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000)
                src = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=timeout_s)
                _shared_sync_sqlite_configure(src)
                dst = sqlite3.connect(tmp_path, timeout=timeout_s)
                _shared_sync_sqlite_configure(dst)
                src.backup(dst)
                dst.close()
                src.close()
                dst = None
                src = None
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                if dst:
                    dst.close()
            except Exception:
                pass
            try:
                if src:
                    src.close()
            except Exception:
                pass
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    try:
        return _shared_sync_sqlite_retry_locked(_backup, os.path.basename(abs_path) or abs_path)
    except HTTPException:
        raise
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido para sincronizacao: {os.path.basename(abs_path)}") from exc

def _shared_sync_ler_arquivo_pacote(path: str) -> bytes:
    if _shared_sync_sqlite_ext(path) and os.path.exists(path):
        return _shared_sync_sqlite_backup_bytes(path)
    with open(path, "rb") as f:
        return f.read()

def _shared_sync_coletar_arquivos(client_id: str, scope: str, username: str = "", user_only: bool = False) -> tuple[list[dict], list[str]]:
    tenant_path = get_tenant_path(client_id)
    tenant_abs = os.path.abspath(tenant_path)
    max_file = _shared_sync_max_file_bytes()
    warnings = []
    entries: list[dict] = []
    if not os.path.exists(tenant_abs):
        return entries, warnings
    user_scoped_rels: set[str] = set()
    if user_only and bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped")):
        user_scoped_rels = set(_shared_sync_user_scoped_rels(scope, username))

    for root, dirs, files in os.walk(tenant_abs, followlinks=False):
        safe_dirs = []
        for directory in dirs:
            rel_dir = os.path.relpath(os.path.join(root, directory), tenant_abs).replace("\\", "/")
            if directory in {"__pycache__", "_drive_restore_backup", "_shared_sync_backups"} or directory.startswith("."):
                continue
            try:
                safe_dir = _shared_sync_resolve_tenant_path(tenant_abs, rel_dir)
                if not os.path.isdir(safe_dir):
                    continue
            except HTTPException:
                warnings.append(f"{rel_dir} ignorado: caminho inseguro para sincronizacao.")
                continue
            safe_dirs.append(directory)
        dirs[:] = safe_dirs
        for filename in files:
            lower = filename.lower()
            if lower.startswith(("drive_sync_state_", "shared_sync_config")):
                continue
            if lower.endswith((".tmp", ".log", ".bak")) or ".backup_" in lower:
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SHARED_SYNC_ALLOWED_EXTENSIONS:
                continue
            rel = os.path.relpath(os.path.join(root, filename), tenant_abs).replace("\\", "/")
            try:
                abs_path = _shared_sync_resolve_tenant_path(tenant_abs, rel)
            except HTTPException:
                warnings.append(f"{rel} ignorado: caminho inseguro para sincronizacao.")
                continue
            if _shared_sync_path_permanently_excluded(rel):
                continue
            if user_scoped_rels and rel not in user_scoped_rels:
                continue
            if not _shared_sync_scope_match(scope, rel):
                continue
            if (
                scope == "favoritos_historico"
                and lower.startswith("favoritos_historico_")
                and lower.endswith(".json")
            ):
                continue
            try:
                entry = {
                    "relative_path": _shared_sync_relativo_seguro(rel),
                    "abs_path": abs_path,
                    "mtime": os.path.getmtime(abs_path),
                }
                if _shared_sync_sqlite_ext(abs_path):
                    data = _shared_sync_ler_arquivo_pacote(abs_path)
                    size = len(data or b"")
                    entry.update({
                        "data": data,
                        "size": size,
                        "sha256": _shared_sync_bytes_sha256(data),
                    })
                else:
                    size = os.path.getsize(abs_path)
                    entry.update({
                        "size": size,
                        "sha256": _shared_sync_sha256_file(abs_path),
                    })
                if size > max_file:
                    warnings.append(f"{rel} ignorado: arquivo maior que o limite de sincronizacao.")
                    continue
                entries.append(entry)
            except HTTPException:
                raise
            except OSError:
                continue
            except Exception as exc:
                warnings.append(f"{rel} ignorado: {exc}")
    entries.sort(key=lambda item: item["relative_path"])
    return entries, warnings

def _shared_sync_entry_from_bytes(rel: str, data: bytes, mtime: float, item_keys: Optional[list[str]] = None) -> dict:
    payload = data or b""
    return {
        "relative_path": _shared_sync_relativo_seguro(rel),
        "data": payload,
        "size": len(payload),
        "mtime": mtime,
        "sha256": _shared_sync_bytes_sha256(payload),
        "item_keys": list(item_keys or []),
    }

configure_shared_sync_collect_files_runtime()

__all__ = [
    "configure_shared_sync_collect_files_runtime",
    "_shared_sync_user_scoped_rels",
    "_shared_sync_scope_match",
    "_shared_sync_sqlite_ext",
    "_shared_sync_sqlite_backup_bytes",
    "_shared_sync_ler_arquivo_pacote",
    "_shared_sync_coletar_arquivos",
    "_shared_sync_entry_from_bytes",
]
