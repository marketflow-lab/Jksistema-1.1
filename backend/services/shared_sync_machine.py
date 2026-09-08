"""Shared Sync machine-to-machine helpers."""

from __future__ import annotations

import base64
import io
import json
import logging
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


logger = logging.getLogger(__name__)


def configure_shared_sync_machine_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_machine_receipt_identity(sessao: dict, machine_id: str) -> str:
    import hashlib
    actual = str(sessao.get("machine_id") or "").strip()
    if not actual or actual != str(machine_id or "").strip():
        raise HTTPException(403, detail="A confirmacao exige a maquina da sessao autenticada.")
    return hashlib.sha256(actual.encode("utf-8")).hexdigest()


def _shared_sync_machine_receipt(sessao: dict, scope: str, machine_id: str, meta: dict,
                                 *, publish: bool = False, conflicts: int = 0) -> dict:
    """Per-snapshot, per-device acknowledgement. Contains no credentials or user data."""
    from backend.services.shared_sync_config import _firebase_shared_sync_collection_name
    from backend.services.shared_sync_remote import _shared_sync_firestore_required
    try:
        member = _shared_sync_machine_receipt_identity(sessao, machine_id)
        pointer = _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope)
        fingerprint = str(meta.get("snapshot_hash") or "")
        snapshot = str(meta.get("snapshot_id") or meta.get("id") or pointer)
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("invalid_receipt_snapshot")
        db = _shared_sync_firestore_required()
        receipts = db.collection(_firebase_shared_sync_collection_name()).document(pointer).collection("receipts")
        if publish:
            receipts.document(member).set({"protocol": 1, "snapshot_hash": fingerprint,
                "snapshot_id": snapshot, "applied_at": _shared_sync_now_iso(),
                "connection_conflicts": max(0, int(conflicts)), "connection_verification": "not_performed"}, timeout=5, retry=None)
            return {"state": "confirmed", "protocol": 1}
        matched = []
        for item in receipts.limit(100).stream(timeout=5, retry=None):
            value = item.to_dict() or {}
            if item.id != member and value.get("protocol") == 1 and value.get("snapshot_hash") == fingerprint and value.get("snapshot_id") == snapshot:
                matched.append({"machine_ref": item.id, "applied_at": str(value.get("applied_at") or ""),
                                "connection_conflicts": int(value.get("connection_conflicts") or 0)})
        return {"state": "received" if matched else "awaiting_receipt", "machines": matched, "protocol": 1}
    except Exception:
        # An acknowledgement outage must not roll back applied data or secrets.
        return {"state": "confirmation_pending" if publish else "unavailable", "protocol": 1}


def _shared_sync_machine_local_stamp(sessao: dict, scope: str) -> str:
    """Detect replaced/deleted local files even if the remote snapshot is unchanged."""
    import hashlib
    try:
        root = get_tenant_path(sessao.get("client_id"))
        entries = []
        for parent, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and not os.path.islink(os.path.join(parent, d))]
            for name in files:
                path = os.path.join(parent, name)
                rel = os.path.relpath(path, root).replace("\\", "/")
                if _shared_sync_scope_match(scope, rel) and not os.path.islink(path):
                    stat = os.stat(path)
                    entries.append((rel, stat.st_size, stat.st_mtime_ns))
        return hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest()
    except (OSError, NameError):
        return ""


def _shared_sync_machine_state_scope(scope: str) -> str:
    return f"machine-sync:{scope}"

def _shared_sync_machine_remote_meta(sessao: dict, scope: str, *, strict: bool = False) -> Optional[dict]:
    return _shared_sync_remote_meta_by_id(
        _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope),
        strict=strict,
    )

def _shared_sync_machine_push_scope(
    sessao: dict,
    scope: str,
    machine_id: str = "",
    skip_if_remote_hash_matches: bool = False,
    expected_snapshot_hash: str = "",
) -> dict:
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
        expected_snapshot_hash=expected_snapshot_hash,
        key_context={"sessao": sessao, "machine_id": machine_id},
    )

def _shared_sync_machine_pull_scope(
    sessao: dict,
    scope: str,
    *,
    force: bool = False,
    machine_id: str = "",
    expected_snapshot_id: str = "",
    expected_remote_fingerprint: str = "",
    expected_bundle_hash: str = "",
) -> dict:
    with _shared_sync_pull_lock(
        "destination",
        sessao.get("client_id"),
        scope,
    ):
        return _shared_sync_machine_pull_scope_serialized(
            sessao,
            scope,
            force=force,
            machine_id=machine_id,
            expected_snapshot_id=expected_snapshot_id,
            expected_remote_fingerprint=expected_remote_fingerprint,
            expected_bundle_hash=expected_bundle_hash,
        )


def _shared_sync_machine_pull_scope_serialized(
    sessao: dict,
    scope: str,
    *,
    force: bool = False,
    machine_id: str = "",
    expected_snapshot_id: str = "",
    expected_remote_fingerprint: str = "",
    expected_bundle_hash: str = "",
) -> dict:
    bundle_id = _shared_sync_machine_doc_id(sessao.get("client_id"), sessao.get("username"), scope)
    meta = _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    initial_remote_fingerprint = _shared_sync_remote_fingerprint_from_meta(meta)
    current_snapshot_id = str(meta.get("snapshot_id") or meta.get("id") or "").strip()
    if expected_snapshot_id and current_snapshot_id != str(expected_snapshot_id).strip():
        raise HTTPException(
            status_code=409,
            detail="O snapshot remoto mudou depois da previa; confira novamente.",
        )
    if (
        expected_remote_fingerprint
        and _shared_sync_remote_fingerprint_from_meta(meta)
        != str(expected_remote_fingerprint)
    ):
        raise HTTPException(
            status_code=409,
            detail="O snapshot remoto mudou depois da previa; confira novamente.",
        )
    state_scope = _shared_sync_machine_state_scope(scope)
    # A importacao manual vem depois de uma previa confirmada pelo usuario e
    # precisa reaplicar o snapshot. O estado historico pode dizer que o hash ja
    # foi recebido mesmo quando o arquivo local foi removido, substituido ou
    # gravado em outra copia do app. O skip continua valido apenas para rotinas
    # automaticas/idempotentes que nao foram explicitamente solicitadas.
    already_current = _shared_sync_pull_already_current(
        sessao.get("client_id"), sessao.get("username") or "", state_scope, meta,
    )
    local_stamp = _shared_sync_machine_local_stamp(sessao, scope)
    local_verified = False
    saved_scope = {}
    if not force and already_current and local_stamp:
        saved_state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
        saved_scope = (saved_state.get("scopes") or {}).get(state_scope) or {}
        local_verified = local_stamp == saved_scope.get("local_content_stamp")
    if not force and already_current and (scope not in {"cadastro", "lojas_integracoes"} or local_verified):
        receipt = None
        if saved_scope.get("receipt_pending"):
            receipt = _shared_sync_machine_receipt(sessao, scope, machine_id, meta, publish=True,
                conflicts=int(saved_scope.get("connection_conflicts") or 0))
            _shared_sync_state_update(sessao.get("client_id"), sessao.get("username") or "", state_scope,
                {**meta, "local_content_stamp": local_stamp, "receipt_pending": receipt["state"] != "confirmed",
                 "connection_conflicts": int(saved_scope.get("connection_conflicts") or 0)}, "pull")
        return _shared_sync_pull_skip_payload(scope, meta, extra={"receipt": receipt} if receipt else None)
    if scope == "lojas_integracoes":
        remoto = _shared_sync_obter_bundle_remoto_para_guard(
            bundle_id,
            key_context={"sessao": sessao, "machine_id": machine_id},
        )
        if remoto is None:
            raise HTTPException(
                status_code=404,
                detail="Nenhum backup remoto encontrado para esse compartilhamento.",
            )
        bundle, meta = remoto
    else:
        bundle, meta = _shared_sync_obter_bundle_por_id(
            bundle_id,
            meta,
            key_context={"sessao": sessao, "machine_id": machine_id},
        )
    if _shared_sync_remote_fingerprint_from_meta(meta) != initial_remote_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=(
                "O snapshot remoto mudou durante a importacao; "
                "confira novamente."
            ),
        )
    if (
        expected_bundle_hash
        and _shared_sync_bytes_sha256(bundle) != str(expected_bundle_hash)
    ):
        raise HTTPException(
            status_code=409,
            detail="O conteúdo remoto mudou depois da previa; confira novamente.",
        )
    base_bundle = None
    if scope == "lojas_integracoes":
        immutable_current_snapshot_id = _shared_sync_immutable_snapshot_id(meta)
        base_snapshot_id = _shared_sync_state_snapshot_id(
            sessao.get("client_id"),
            sessao.get("username") or "",
            state_scope,
        )
        base_snapshot_hash = ""
        if immutable_current_snapshot_id and not base_snapshot_id:
            try:
                base_snapshot_hash = _shared_sync_state_snapshot_hash(
                    sessao.get("client_id"),
                    sessao.get("username") or "",
                    state_scope,
                )
            except Exception:
                # A compatibilidade antiga nunca reduz o fail-closed: se o
                # estado nao puder ser lido, nenhum hash recebe autoridade.
                base_snapshot_hash = ""
        # Um writer v1 pode recolocar o ponteiro legado depois de uma publicacao
        # v2. Nunca trate esse ponteiro mutavel como descendente da base v2:
        # sem base, o merge estrito bloqueia OAuth divergente em vez de escolher.
        if (
            immutable_current_snapshot_id
            and base_snapshot_id == immutable_current_snapshot_id
        ):
            base_bundle = bundle
        elif immutable_current_snapshot_id and base_snapshot_id:
            try:
                base_meta = _shared_sync_remote_meta_by_id(base_snapshot_id)
                if base_meta:
                    base_bundle, _ = _shared_sync_obter_bundle_por_id(
                        base_snapshot_id,
                        base_meta,
                        key_context={"sessao": sessao, "machine_id": machine_id},
                    )
            except Exception:
                # Sem base confiavel, o merge estrito bloqueia credenciais
                # divergentes em vez de decidir por relogio.
                base_bundle = None
        elif immutable_current_snapshot_id and base_snapshot_hash:
            current_hash = str(meta.get("snapshot_hash") or "").strip().lower()
            legacy_hash = str(base_snapshot_hash or "").strip().lower()
            if re.fullmatch(r"[a-f0-9]{64}", legacy_hash) and legacy_hash == current_hash:
                base_bundle = bundle
            else:
                recovered = _shared_sync_obter_base_causal_por_hash(
                    bundle_id,
                    legacy_hash,
                    expected_client_id=str(sessao.get("client_id") or ""),
                    expected_scope=scope,
                    key_context={"sessao": sessao, "machine_id": machine_id},
                )
                if recovered is not None:
                    base_bundle, _ = recovered
    scope_config = {
        "share_between_users": bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped")),
        "base_bundle": base_bundle,
        "strict_oauth_conflicts": scope == "lojas_integracoes",
        "preserve_local_connections": scope == "lojas_integracoes",
        # Uma copia entre maquinas da mesma conta pode preservar o modo legado
        # de fotos quando o produtor ainda nao executou a migracao por loja.
        # Pacotes com fotos ja escopadas continuam exigindo a configuracao.
        "allow_legacy_cadastro_bootstrap": scope == "cadastro",
    }
    result = _shared_sync_aplicar_pacote(sessao.get("client_id"), scope, bundle, sessao.get("username") or "", scope_config)
    receipt = _shared_sync_machine_receipt(sessao, scope, machine_id, meta, publish=True,
                                         conflicts=int(result.get("connection_conflicts") or 0))
    local_meta = {**meta, "local_content_stamp": _shared_sync_machine_local_stamp(sessao, scope),
                  "receipt_pending": receipt["state"] != "confirmed",
                  "connection_conflicts": int(result.get("connection_conflicts") or 0)}
    _shared_sync_state_update(sessao.get("client_id"), sessao.get("username") or "", state_scope, local_meta, "pull")
    return {
        "scope": scope,
        "success": True,
        "direction": "pull",
        "file_count": result.get("file_count") or 0,
        "stores_count": int(result.get("stores_count") or 0),
        "snapshot_stores_count": int(result.get("snapshot_stores_count") or 0),
        "connection_conflicts": int(result.get("connection_conflicts") or 0),
        "connections_preserved": bool(result.get("connections_preserved")),
        "connection_verification": "not_performed",
        "delivery_state": "applied",
        "receipt": receipt,

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
        meta = _shared_sync_machine_remote_meta(sessao, scope, strict=True) or {}
        state_key = _shared_sync_machine_state_scope(scope)
        scope_state = state_scopes.get(state_key) or {}
        remote_hash = str(meta.get("snapshot_hash") or "")
        state_hash = str(scope_state.get("snapshot_hash") or "")
        remote_snapshot_id = str(meta.get("snapshot_id") or "").strip()
        state_snapshot_id = str(scope_state.get("snapshot_id") or "").strip()
        remote_machine = str(meta.get("machine_id") or "").strip()
        current_machine = str(machine_id or "").strip()
        synced_at = str(scope_state.get("synced_at") or "")
        scopes[scope] = {
            **_shared_sync_scope_public(scope),
            "allowed": scope in permitidos,
            "selected": scope in (config.get("scopes") or []),
            "state": scope_state,
            "pending_receive": bool(
                meta
                and remote_hash
                and (
                    remote_hash != state_hash
                    or (
                        remote_snapshot_id
                        and remote_snapshot_id != state_snapshot_id
                    )
                )
                and remote_machine != current_machine
            ),
            "delivery": _shared_sync_machine_receipt(sessao, scope, machine_id, meta) if meta and scope in config.get("scopes", []) else {"state": "not_sent"},
            "synced_at": synced_at,
            "last_received_at": synced_at if str(scope_state.get("direction") or "") == "pull" else "",
            "remote": {
                "exists": bool(meta),
                "updated_at": meta.get("updated_at") or "",
                "updated_by": meta.get("updated_by") or "",
                "machine_id": meta.get("machine_id") or "",
                "snapshot_hash": remote_hash,
                "file_count": meta.get("file_count") or 0,
                "stores_count": meta.get("stores_count") or 0,
                "bundle_bytes": meta.get("bundle_bytes") or 0,
                "chunk_count": meta.get("chunk_count") or 0,
                "encryption_key_id": meta.get("encryption_key_id") or "",
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
        "keyring": _shared_sync_keyring_status(sessao, machine_id or ""),
        "machines": maquinas[:20],
        "online_count": len([m for m in maquinas if m.get("online")]),
    }

def _shared_sync_machine_auto_run(sessao: dict, machine_id: str = "", requested: Optional[list[str]] = None) -> dict:
    config = _shared_sync_machine_config_read(sessao)
    if not config.get("enabled"):
        return {"success": True, "direction": "machine-auto", "results": [], "skipped": [{"reason": "disabled"}]}
    scopes = _shared_sync_machine_resolver_scopes(sessao, requested, require_enabled=True)
    if "lojas_integracoes" in scopes:
        scopes = [
            "lojas_integracoes",
            *(scope for scope in scopes if scope != "lojas_integracoes"),
        ]
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    state_scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    results = []
    skipped = []
    for scope in scopes:
        if not config.get("auto_pull"):
            skipped.append({"scope": scope, "reason": "auto_pull_disabled"})
            continue
        state_key = _shared_sync_machine_state_scope(scope)
        remote = _shared_sync_machine_remote_meta(sessao, scope) or {}
        remote_hash = str(remote.get("snapshot_hash") or "")
        state_hash = str(((state_scopes.get(state_key) or {}).get("snapshot_hash")) or "")
        remote_machine = str(remote.get("machine_id") or "").strip()
        current_machine = str(machine_id or "").strip()

        if not remote:
            skipped.append({"scope": scope, "reason": "remote_missing"})
            continue
        if not remote_hash:
            skipped.append({"scope": scope, "reason": "remote_hash_missing"})
            continue
        state_scope = state_scopes.get(state_key) if isinstance(state_scopes.get(state_key), dict) else {}
        remote_snapshot_id = str(remote.get("snapshot_id") or "").strip()
        state_snapshot_id = str(state_scope.get("snapshot_id") or "").strip()
        if (
            state_hash == remote_hash
            and not state_scope.get("receipt_pending")
            and (scope not in {"cadastro", "lojas_integracoes"} or (state_scope.get("local_content_stamp") and state_scope.get("local_content_stamp") == _shared_sync_machine_local_stamp(sessao, scope)))
            and (not remote_snapshot_id or state_snapshot_id == remote_snapshot_id)
        ):
            skipped.append({"scope": scope, "reason": "already_current", "snapshot_hash": remote_hash})
            continue
        if remote_machine == current_machine:
            skipped.append({"scope": scope, "reason": "same_machine", "snapshot_hash": remote_hash})
            continue
        try:
            results.append(_shared_sync_machine_pull_scope(sessao, scope, machine_id=machine_id))
        except Exception as exc:
            from backend.services.shared_sync_machine_endpoints import _shared_sync_machine_pull_failure
            skipped.append(_shared_sync_machine_pull_failure(scope, exc))
    failures = [item for item in skipped if item.get("reason") == "pull_failed"]
    return {"success": not failures, "partial": bool(failures) and bool(results),
            "direction": "machine-auto", "results": results, "skipped": skipped}

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
