"""Shared Sync package application and pull helpers."""

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


def _shared_sync_validate_sqlite_payload(rel: str, data: bytes) -> None:
    if not (data or b"").startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}")
    fd, tmp_path = tempfile.mkstemp(prefix="shared_sync_validate_", suffix=".db")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as file:
            file.write(data or b"")
        conn = sqlite3.connect(tmp_path, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        try:
            _shared_sync_sqlite_configure(conn)
            _shared_sync_sqlite_quick_check(conn, rel)
        finally:
            conn.close()
    except HTTPException:
        raise
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _shared_sync_read_validated_bundle(bundle: bytes, scope: str) -> tuple[dict, list[tuple[str, bytes]]]:
    max_file = _shared_sync_max_file_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            try:
                manifest_info = zf.getinfo("manifest.json")
            except KeyError as exc:
                raise HTTPException(status_code=502, detail="Backup remoto sem manifest.json.") from exc
            if manifest_info.file_size > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Manifesto do Shared Sync maior que o limite permitido.")
            try:
                manifest = json.loads(zf.read(manifest_info).decode("utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Manifesto do Shared Sync invalido.") from exc
            if not isinstance(manifest, dict):
                raise HTTPException(status_code=502, detail="Manifesto do Shared Sync invalido.")
            schema = manifest.get("schema")
            if schema not in {None, 1, 2}:
                raise HTTPException(status_code=400, detail="Versao do pacote Shared Sync nao suportada.")
            if str(manifest.get("scope") or "") != scope:
                raise HTTPException(status_code=400, detail="Backup remoto pertence a outro escopo.")
            files = manifest.get("files") or []
            if not isinstance(files, list):
                raise HTTPException(status_code=502, detail="Lista de arquivos invalida no manifesto.")
            if schema == 2 and int(manifest.get("file_count") or 0) != len(files):
                raise HTTPException(status_code=502, detail="Contagem de arquivos divergente no manifesto.")

            fontes: list[tuple[str, bytes]] = []
            seen_rels = set()
            archive_names = zf.namelist()
            for item in files:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=502, detail="Entrada invalida no manifesto do Shared Sync.")
                rel = _shared_sync_relativo_seguro(item.get("relative_path"))
                rel_key = rel.lower()
                if rel_key in seen_rels:
                    raise HTTPException(status_code=502, detail=f"Arquivo duplicado no manifesto: {rel}")
                seen_rels.add(rel_key)
                if _shared_sync_path_permanently_excluded(rel):
                    raise HTTPException(status_code=400, detail="Arquivo permanentemente excluido do Shared Sync.")
                legacy_state = scope == "vendas" and rel_key == "vendas_sync_state.json"
                if not legacy_state and not _shared_sync_scope_match(scope, rel):
                    raise HTTPException(status_code=400, detail=f"Arquivo fora do escopo: {rel}")
                if _shared_sync_transient_filename(os.path.basename(rel)):
                    raise HTTPException(status_code=400, detail=f"Arquivo temporario nao permitido no pacote: {rel}")

                member = "files/" + rel
                if archive_names.count(member) != 1:
                    raise HTTPException(status_code=502, detail=f"Backup remoto sem arquivo unico esperado: {rel}")
                member_info = zf.getinfo(member)
                try:
                    expected_size = int(item.get("size"))
                except (TypeError, ValueError):
                    expected_size = -1
                if schema == 2 and expected_size < 0:
                    raise HTTPException(status_code=502, detail=f"Tamanho ausente no manifesto: {rel}")
                if expected_size > max_file or member_info.file_size > max_file:
                    raise HTTPException(status_code=413, detail=f"Arquivo maior que o limite de sincronizacao: {rel}")
                data = zf.read(member_info)
                if expected_size >= 0 and len(data) != expected_size:
                    raise HTTPException(status_code=502, detail=f"Tamanho divergente no pacote: {rel}")
                expected_hash = str(item.get("sha256") or "").strip().lower()
                if schema == 2 and not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                    raise HTTPException(status_code=502, detail=f"Hash ausente ou invalido no manifesto: {rel}")
                if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
                    raise HTTPException(status_code=502, detail=f"Hash divergente no pacote: {rel}")
                if rel_key.endswith((".db", ".sqlite", ".sqlite3")):
                    _shared_sync_validate_sqlite_payload(rel, data)
                if not legacy_state:
                    fontes.append((rel, data))
            return manifest, fontes
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Pacote Shared Sync invalido.") from exc


def _shared_sync_atomic_write(target_abs: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target_abs)}.sharedsync_",
        suffix=".tmp",
        dir=os.path.dirname(target_abs),
    )
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data or b"")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, target_abs)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _shared_sync_aplicar_pacote(
    client_id: str,
    scope: str,
    bundle: bytes,
    username: str = "",
    scope_config: Optional[dict] = None,
) -> dict:
    tenant_path = get_tenant_path(client_id)
    tenant_abs = os.path.abspath(tenant_path)
    backup_dir = os.path.join(
        tenant_abs,
        "_shared_sync_backups",
        f"{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
    )
    escritos = []
    lojas_aplicadas: Optional[int] = None
    user_scoped_fontes: list[tuple[str, bytes]] = []
    user_share_fontes: list[tuple[str, bytes]] = []
    legacy_favoritos_fontes: list[tuple[str, bytes]] = []
    user_share = bool((scope_config or {}).get("user_share"))
    share_between_users = bool((scope_config or {}).get("share_between_users")) and bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped"))
    _manifest, fontes = _shared_sync_read_validated_bundle(bundle, scope)

    if scope == "vendas":
        vendas_dbs = [(rel, data) for rel, data in fontes if _shared_sync_vendas_history_db(rel)]
        if vendas_dbs:
            merged = _shared_sync_apply_vendas_dbs_add_only(vendas_dbs, tenant_abs, backup_dir)
            escritos.extend(merged.get("files") or [])
        fontes = [(rel, data) for rel, data in fontes if not _shared_sync_vendas_history_db(rel)]

    for rel, data in fontes:
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
            lojas_aplicadas = len(lojas)
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
        _shared_sync_atomic_write(target_abs, data)
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
    if scope == "lojas_integracoes" and lojas_aplicadas is None:
        raise HTTPException(
            status_code=502,
            detail="O snapshot remoto de Lojas e integracoes nao contem lojas_config.json.",
        )
    return {
        "file_count": len(escritos),
        "files": escritos[:250],
        "backup_dir": backup_dir if escritos else "",
        "stores_count": lojas_aplicadas if lojas_aplicadas is not None else 0,
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
    "_shared_sync_validate_sqlite_payload",
    "_shared_sync_read_validated_bundle",
    "_shared_sync_atomic_write",
    "_shared_sync_aplicar_pacote",
    "_shared_sync_pull_scope",
]
