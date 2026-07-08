"""Shared Sync Firestore upload, metadata, and bundle download helpers."""

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


def configure_shared_sync_remote_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_firestore_required():
    db = _firebase_db()
    if db is None:
        detalhe = FIREBASE_AUTH_LAST_ERROR or "Firebase nao configurado para sincronizacao compartilhada."
        raise HTTPException(status_code=503, detail=detalhe)
    return db

def _shared_sync_delete_chunks(db, bundle_id: str) -> None:
    try:
        coll = db.collection(_firebase_shared_sync_chunks_collection_name())
        for snap in coll.where("bundle_id", "==", bundle_id).stream():
            snap.reference.delete()
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao limpar chunks antigos: %s", exc)

def _shared_sync_push_scope(
    client_id: str,
    scope: str,
    sessao: dict,
    machine_id: str = "",
    bundle_id: Optional[str] = None,
    extra_meta: Optional[dict] = None,
    user_only: bool = False,
    state_scope: Optional[str] = None,
    known_keys: Optional[set[str]] = None,
    allow_empty_delta: bool = False,
    sanitize_user_share_oauth: bool = False,
    skip_if_remote_hash_matches: bool = False,
) -> dict:
    db = _shared_sync_firestore_required()
    bundle, manifest, warnings = _shared_sync_montar_pacote(
        client_id,
        scope,
        sessao.get("username"),
        machine_id,
        user_only=user_only,
        known_keys=known_keys,
        sanitize_user_share_oauth=sanitize_user_share_oauth,
    )
    bundle_id = str(bundle_id or _shared_sync_doc_id(client_id, scope)).strip()
    if skip_if_remote_hash_matches:
        remote_meta = _shared_sync_remote_meta_by_id(bundle_id) or {}
        remote_hash = str(remote_meta.get("snapshot_hash") or "")
        local_hash = str(manifest.get("snapshot_hash") or "")
        if remote_hash and local_hash and remote_hash == local_hash:
            return {
                "scope": scope,
                "success": True,
                "direction": "push",
                "id": bundle_id,
                "skipped": True,
                "reason": "already_current",
                "file_count": manifest.get("file_count") or 0,
                "item_count": manifest.get("item_count") or 0,
                "item_keys": manifest.get("item_keys") or [],
                "chunk_count": int(remote_meta.get("chunk_count") or 0),
                "bundle_bytes": int(remote_meta.get("bundle_bytes") or 0),
                "snapshot_hash": local_hash,
                "warnings": warnings,
                "updated_at": remote_meta.get("updated_at") or _shared_sync_now_iso(),
            }
    if known_keys is not None and not manifest.get("item_count") and not allow_empty_delta:
        return {
            "scope": scope,
            "success": True,
            "direction": "push",
            "id": bundle_id,
            "skipped": True,
            "reason": "already_shared",
            "file_count": 0,
            "item_count": 0,
            "item_keys": [],
            "chunk_count": 0,
            "bundle_bytes": 0,
            "snapshot_hash": manifest.get("snapshot_hash") or "",
            "warnings": warnings,
            "updated_at": _shared_sync_now_iso(),
        }
    if scope == "lojas_integracoes" and known_keys is None:
        _shared_sync_validar_push_lojas_integracoes(bundle_id, bundle)
    bundle_b64 = base64.b64encode(bundle).decode("ascii")
    chunks = [bundle_b64[i:i + SHARED_SYNC_CHUNK_CHARS] for i in range(0, len(bundle_b64), SHARED_SYNC_CHUNK_CHARS)] or [""]
    _shared_sync_delete_chunks(db, bundle_id)
    chunks_coll = db.collection(_firebase_shared_sync_chunks_collection_name())
    for idx, chunk in enumerate(chunks):
        chunks_coll.document(f"{bundle_id}_{idx:05d}").set({
            "bundle_id": bundle_id,
            "client_id": str(client_id or "default").strip() or "default",
            "scope": scope,
            "index": idx,
            "data": chunk,
            "updated_at": manifest["created_at"],
        })
    meta = {
        "id": bundle_id,
        "client_id": str(client_id or "default").strip() or "default",
        "scope": scope,
        "updated_at": manifest["created_at"],
        "updated_ts": int(time.time()),
        "updated_by": sessao.get("username") or "",
        "machine_id": str(machine_id or "").strip(),
        "file_count": manifest.get("file_count") or 0,
        "delta": bool(manifest.get("delta")),
        "item_count": manifest.get("item_count") or 0,
        "bundle_bytes": len(bundle),
        "bundle_b64_chars": len(bundle_b64),
        "chunk_count": len(chunks),
        "snapshot_hash": manifest.get("snapshot_hash") or "",
        "files": (manifest.get("files") or [])[:250],
        "warnings": warnings,
    }
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    db.collection(_firebase_shared_sync_collection_name()).document(bundle_id).set(meta, merge=False)
    _shared_sync_state_update(client_id, sessao.get("username") or "", state_scope or scope, meta, "push")
    return {
        "scope": scope,
        "success": True,
        "direction": "push",
        "id": bundle_id,
        "file_count": meta["file_count"],
        "item_count": meta["item_count"],
        "item_keys": list(manifest.get("item_keys") or []),
        "delta": meta["delta"],
        "chunk_count": meta["chunk_count"],
        "bundle_bytes": meta["bundle_bytes"],
        "snapshot_hash": meta["snapshot_hash"],
        "warnings": warnings,
        "updated_at": meta["updated_at"],
    }

def _shared_sync_remote_meta(client_id: str, scope: str) -> Optional[dict]:
    return _shared_sync_remote_meta_by_id(_shared_sync_doc_id(client_id, scope))

def _shared_sync_remote_meta_by_id(bundle_id: str) -> Optional[dict]:
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is None:
        return None
    try:
        snap = db.collection(_firebase_shared_sync_collection_name()).document(str(bundle_id or "").strip()).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["id"] = data.get("id") or snap.id
        return data
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao ler metadados remotos: %s", exc)
        return None

def _shared_sync_obter_bundle_remoto(client_id: str, scope: str) -> tuple[bytes, dict]:
    return _shared_sync_obter_bundle_por_id(_shared_sync_doc_id(client_id, scope))

def _shared_sync_obter_bundle_por_id(bundle_id: str, meta: Optional[dict] = None) -> tuple[bytes, dict]:
    db = _shared_sync_firestore_required()
    meta = meta if isinstance(meta, dict) and meta else _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    bundle_id = str(meta.get("id") or bundle_id)
    chunk_count = int(meta.get("chunk_count") or 0)
    if chunk_count <= 0:
        raise HTTPException(status_code=404, detail="Backup remoto sem chunks.")
    chunks = []
    coll = db.collection(_firebase_shared_sync_chunks_collection_name())
    for idx in range(chunk_count):
        snap = coll.document(f"{bundle_id}_{idx:05d}").get()
        if not snap.exists:
            raise HTTPException(status_code=502, detail=f"Backup remoto incompleto: parte {idx + 1}/{chunk_count}.")
        data = snap.to_dict() or {}
        chunks.append(str(data.get("data") or ""))
    try:
        bundle = base64.b64decode("".join(chunks).encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backup remoto corrompido: {exc}")
    return bundle, meta

def _shared_sync_manifest_from_bundle(bundle: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            raw = zf.read("manifest.json")
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao ler manifest do pacote: %s", exc)
        return {}

configure_shared_sync_remote_runtime()

__all__ = [
    "configure_shared_sync_remote_runtime",
    "_shared_sync_firestore_required",
    "_shared_sync_delete_chunks",
    "_shared_sync_push_scope",
    "_shared_sync_remote_meta",
    "_shared_sync_remote_meta_by_id",
    "_shared_sync_obter_bundle_remoto",
    "_shared_sync_obter_bundle_por_id",
    "_shared_sync_manifest_from_bundle",
]
