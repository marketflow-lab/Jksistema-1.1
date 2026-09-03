"""Manual shared-sync previews, optimistic locks and secret-free audit records."""

from __future__ import annotations

import io
import json
import os
import threading
import time
import uuid
import zipfile
from typing import Optional

from fastapi import HTTPException

from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_bundle import _shared_sync_sanitize_transient_oauth_entries
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context


_OPERATIONS_LOCK = threading.RLock()
_OPERATIONS: dict[str, dict] = {}
_AUDIT_LOCK = threading.RLock()
_OPERATION_TTL_SECONDS = 600
_SECRET_KEYS = {
    "secret", "client_secret", "secret_key", "access_token", "refresh_token",
    "token", "api_key", "apikey", "password", "authorization",
}


def configure_shared_sync_operations_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_operation_direction(value: str) -> str:
    direction = str(value or "").strip().lower()
    if direction not in {"push", "pull"}:
        raise HTTPException(status_code=400, detail="Direcao invalida; use push ou pull.")
    return direction


def _shared_sync_local_fingerprint(sessao: dict, scopes: list[str]) -> tuple[dict, dict]:
    hashes: dict[str, str] = {}
    files: dict[str, list[dict]] = {}
    for scope in scopes:
        entries, _warnings = _shared_sync_coletar_arquivos(
            sessao.get("client_id"), scope, username=sessao.get("username"), user_only=True,
        )
        entries = _shared_sync_sanitize_transient_oauth_entries(scope, entries)
        hashes[scope] = _shared_sync_snapshot_hash(entries)
        files[scope] = [
            {
                "relative_path": str(item.get("relative_path") or ""),
                "size": int(item.get("size") or 0),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in entries
        ]
    return hashes, files


def _shared_sync_remote_fingerprint(bundle_ids: dict[str, str]) -> tuple[dict, dict]:
    hashes: dict[str, str] = {}
    metas: dict[str, dict] = {}
    for scope, bundle_id in bundle_ids.items():
        meta = _shared_sync_remote_meta_by_id(bundle_id) or {}
        hashes[scope] = _shared_sync_remote_fingerprint_from_meta(meta)
        metas[scope] = meta
    return hashes, metas


def _shared_sync_preview_file_counts(source_files: list[dict], target_files: list[dict]) -> dict:
    source = {str(item.get("relative_path") or ""): item for item in source_files or []}
    target = {str(item.get("relative_path") or ""): item for item in target_files or []}
    added = sum(1 for key in source if key not in target)
    changed = sum(
        1 for key, item in source.items()
        if key in target and str(item.get("sha256") or "") != str(target[key].get("sha256") or "")
    )
    deleted = sum(1 for key in target if key not in source)
    return {"inclusions": added, "changes": changed, "deletions": deleted}


def _shared_sync_count_sensitive(value) -> tuple[int, int, int]:
    credentials = 0
    disconnects = 0
    stores = sum(
        1
        for item in value
        if isinstance(item, dict)
        and (str(item.get("nome") or "").strip() or str(item.get("store_id") or "").strip())
    ) if isinstance(value, list) else 0

    def visitar(item) -> None:
        nonlocal credentials, disconnects
        if isinstance(item, list):
            for child in item:
                visitar(child)
            return
        if not isinstance(item, dict):
            return
        for key, child in item.items():
            key_norm = str(key or "").strip().lower()
            if key_norm in _SECRET_KEYS and child not in (None, "", False):
                credentials += 1
            if key_norm == "connected" and child is False:
                disconnects += 1
            visitar(child)

    visitar(value)
    return credentials, disconnects, stores


def _shared_sync_bundle_sensitive_counts(bundle: bytes) -> tuple[int, int, int]:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            lojas_raw = zf.read("files/lojas_config.json")
            try:
                legado_raw = zf.read("files/integracoes.json")
            except KeyError:
                legado_raw = b"{}"
        lojas = _shared_sync_count_sensitive(
            json.loads(lojas_raw.decode("utf-8-sig"))
        )
        legado = _shared_sync_count_sensitive(
            json.loads(legado_raw.decode("utf-8-sig"))
        )
        return lojas[0] + legado[0], lojas[1] + legado[1], lojas[2]
    except Exception:
        return 0, 0, 0


def _shared_sync_local_sensitive_counts(sessao: dict) -> tuple[int, int, int]:
    try:
        tenant_path = get_tenant_path(sessao.get("client_id"))
    except Exception:
        return 0, 0, 0
    lojas = (0, 0, 0)
    legado = (0, 0, 0)
    try:
        path = os.path.join(tenant_path, "lojas_config.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as handle:
                lojas = _shared_sync_count_sensitive(json.load(handle))
    except Exception:
        lojas = (0, 0, 0)
    try:
        legado_path = os.path.join(tenant_path, "integracoes.json")
        if os.path.exists(legado_path):
            with open(legado_path, "r", encoding="utf-8-sig") as handle:
                legado = _shared_sync_count_sensitive(json.load(handle))
    except Exception:
        legado = (0, 0, 0)
    return lojas[0] + legado[0], lojas[1] + legado[1], lojas[2]


def _shared_sync_create_preview(
    sessao: dict,
    *,
    kind: str,
    resource_id: str,
    direction: str,
    scopes: list[str],
    bundle_ids: dict[str, str],
    machine_id: str = "",
    key_context: Optional[dict] = None,
) -> dict:
    direction = _shared_sync_operation_direction(direction)
    local_hashes, local_files = _shared_sync_local_fingerprint(sessao, scopes)
    remote_hashes, remote_metas = _shared_sync_remote_fingerprint(bundle_ids)
    remote_bundle_hashes: dict[str, str] = {}
    totals = {"inclusions": 0, "changes": 0, "deletions": 0, "disconnects": 0, "credentials": 0, "stores": 0}
    per_scope = {}
    for scope in scopes:
        remote_files = list((remote_metas.get(scope) or {}).get("files") or [])
        if direction == "push":
            counts = _shared_sync_preview_file_counts(local_files.get(scope) or [], remote_files)
        else:
            counts = _shared_sync_preview_file_counts(remote_files, local_files.get(scope) or [])
        if scope == "lojas_integracoes":
            if direction == "push":
                cred, disc, stores = _shared_sync_local_sensitive_counts(sessao)
            else:
                meta = remote_metas.get(scope) or {}
                if meta:
                    remoto = _shared_sync_obter_bundle_remoto_para_guard(
                        bundle_ids[scope],
                        key_context=key_context,
                    )
                    if remoto is None:
                        raise HTTPException(
                            status_code=404,
                            detail="Snapshot remoto nao encontrado.",
                        )
                    bundle, _ = remoto
                    remote_bundle_hashes[scope] = _shared_sync_bytes_sha256(
                        bundle
                    )
                    cred, disc, stores = _shared_sync_bundle_sensitive_counts(bundle)
                else:
                    cred, disc, stores = 0, 0, 0
            counts.update({"credentials": cred, "disconnects": disc, "stores": stores})
        else:
            counts.update({"credentials": 0, "disconnects": 0, "stores": 0})
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
        per_scope[scope] = {
            **counts,
            "source_hash": local_hashes.get(scope) if direction == "push" else str((remote_metas.get(scope) or {}).get("snapshot_hash") or ""),
            "target_hash": str((remote_metas.get(scope) or {}).get("snapshot_hash") or "") if direction == "push" else local_hashes.get(scope),
            "remote_exists": bool(remote_metas.get(scope)),
        }
    now = int(time.time())
    operation_id = uuid.uuid4().hex
    record = {
        "id": operation_id,
        "kind": str(kind),
        "resource_id": str(resource_id or ""),
        "direction": direction,
        "scopes": list(scopes),
        "client_id": str(sessao.get("client_id") or ""),
        "username": str(sessao.get("username") or ""),
        "machine_id": str(machine_id or ""),
        "local_hashes": local_hashes,
        "remote_hashes": remote_hashes,
        "remote_bundle_hashes": remote_bundle_hashes,
        "remote_snapshot_ids": {
            scope: str(
                (remote_metas.get(scope) or {}).get("snapshot_id")
                or (remote_metas.get(scope) or {}).get("id")
                or ""
            )
            for scope in scopes
        },
        "created_ts": now,
        "expires_ts": now + _OPERATION_TTL_SECONDS,
        "used": False,
        "totals": totals,
    }
    with _OPERATIONS_LOCK:
        for key, value in list(_OPERATIONS.items()):
            if int(value.get("expires_ts") or 0) < now or value.get("used"):
                _OPERATIONS.pop(key, None)
        _OPERATIONS[operation_id] = record
    return {
        "success": True,
        "manual_only": True,
        "operation_id": operation_id,
        "expires_in_seconds": _OPERATION_TTL_SECONDS,
        "expires_at": record["expires_ts"],
        "direction": direction,
        "scopes": per_scope,
        "totals": totals,
    }


def _shared_sync_require_operation(
    operation_id: str,
    sessao: dict,
    *,
    kind: str,
    resource_id: str,
    direction: str,
    scopes: list[str],
    bundle_ids: dict[str, str],
) -> dict:
    operation_id = str(operation_id or "").strip()
    if not operation_id:
        raise HTTPException(status_code=409, detail="Faca a previa antes de confirmar esta sincronizacao.")
    now = int(time.time())
    with _OPERATIONS_LOCK:
        record = dict(_OPERATIONS.get(operation_id) or {})
        identity_ok = (
            record.get("kind") == str(kind)
            and record.get("resource_id") == str(resource_id or "")
            and record.get("direction") == _shared_sync_operation_direction(direction)
            and record.get("client_id") == str(sessao.get("client_id") or "")
            and record.get("username") == str(sessao.get("username") or "")
            and record.get("scopes") == list(scopes)
        )
        if not identity_ok or record.get("used") or int(record.get("expires_ts") or 0) < now:
            raise HTTPException(status_code=409, detail="Previa invalida ou expirada; confira os dados novamente.")
        # Reclama a operacao ainda dentro do lock. Uma segunda confirmacao
        # concorrente nao pode passar pelo mesmo operation_id.
        _OPERATIONS[operation_id]["used"] = True
    current_local, _ = _shared_sync_local_fingerprint(sessao, scopes)
    current_remote, _ = _shared_sync_remote_fingerprint(bundle_ids)
    if current_local != record.get("local_hashes") or current_remote != record.get("remote_hashes"):
        raise HTTPException(status_code=409, detail="Os dados mudaram depois da previa; confira novamente.")
    return record


def _shared_sync_audit(sessao: dict, *, record: dict, results: list[dict], link_id: str = "") -> None:
    payload = {
        "at": _shared_sync_now_iso(),
        "operation_id": record.get("id") or "",
        "username": str(sessao.get("username") or ""),
        "client_id": str(sessao.get("client_id") or ""),
        "machine_id": str(record.get("machine_id") or ""),
        "link_id": str(link_id or ""),
        "direction": str(record.get("direction") or ""),
        "scopes": list(record.get("scopes") or []),
        "counts": dict(record.get("totals") or {}),
        "snapshots": [
            {
                "scope": str(item.get("scope") or ""),
                "snapshot_hash": str(item.get("snapshot_hash") or ""),
                "success": item.get("success") is not False,
                "reason": str(item.get("reason") or ""),
                "status_code": int(item.get("status_code") or 0),
            }
            for item in (results or []) if isinstance(item, dict)
        ],
    }
    path = os.path.join(get_tenant_path(sessao.get("client_id")), "shared_sync_audit.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _AUDIT_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


configure_shared_sync_operations_runtime()

__all__ = [
    "configure_shared_sync_operations_runtime",
    "_shared_sync_operation_direction",
    "_shared_sync_create_preview",
    "_shared_sync_require_operation",
    "_shared_sync_audit",
]
