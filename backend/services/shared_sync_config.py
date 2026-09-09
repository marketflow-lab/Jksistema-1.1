"""Shared Sync configuration, session, and scope helpers."""

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


def configure_shared_sync_config_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _firebase_shared_sync_config_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_CONFIG_COLLECTION", "JK_FIREBASE_SHARED_SYNC_CONFIG_COLLECTION") or "jk_sistema_shared_sync_config"

def _firebase_shared_sync_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_COLLECTION", "JK_FIREBASE_SHARED_SYNC_COLLECTION") or "jk_sistema_shared_sync"

def _firebase_shared_sync_chunks_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_CHUNKS_COLLECTION", "JK_FIREBASE_SHARED_SYNC_CHUNKS_COLLECTION") or "jk_sistema_shared_sync_chunks"


def _firebase_shared_sync_keyrings_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_KEYRINGS_COLLECTION", "JK_FIREBASE_SHARED_SYNC_KEYRINGS_COLLECTION") or "jk_sistema_shared_sync_keyrings"

def _firebase_shared_sync_user_invites_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_USER_INVITES_COLLECTION", "JK_FIREBASE_SHARED_SYNC_USER_INVITES_COLLECTION") or "jk_sistema_shared_sync_user_invites"

def _firebase_shared_sync_user_links_collection_name() -> str:
    return _env_texto("FIREBASE_SHARED_SYNC_USER_LINKS_COLLECTION", "JK_FIREBASE_SHARED_SYNC_USER_LINKS_COLLECTION") or "jk_sistema_shared_sync_user_links"

def _shared_sync_auto_interval_seconds() -> int:
    try:
        valor = int(float(os.getenv("JK_SHARED_SYNC_AUTO_INTERVAL_S", "900") or 900))
    except Exception:
        valor = 900
    return max(600, min(valor, 3600))


def _shared_sync_machine_auto_interval_seconds() -> int:
    try:
        valor = int(float(os.getenv("JK_MACHINE_SHARED_SYNC_AUTO_INTERVAL_S", "120") or 120))
    except Exception:
        valor = 120
    return max(60, min(valor, 900))

def _shared_sync_auto_enabled() -> bool:
    # Contrato v2: a sincronizacao compartilhada nunca executa em background.
    # A variavel antiga e deliberadamente ignorada para impedir reativacao acidental.
    return False

def _shared_sync_manual_only_payload(direction: str) -> dict:
    return {
        "success": True,
        "direction": direction,
        "results": [],
        "skipped": [{
            "reason": "manual_only",
            "message": "Sincronizacao automatica desativada; use envio/recebimento manual.",
        }],
    }

def _shared_sync_auto_rate_limit(
    endpoint: str,
    sessao: dict,
    machine_id: str = "",
    interval_seconds: Optional[int] = None,
) -> Optional[dict]:
    intervalo = int(interval_seconds or _shared_sync_auto_interval_seconds())
    username = _shared_sync_normalizar_username((sessao or {}).get("username"))
    client_id = _shared_sync_normalizar_client_id((sessao or {}).get("client_id"))
    machine = str(machine_id or "").strip()[:120]
    key = "|".join([str(endpoint or "auto"), client_id, username, machine])
    agora = time.time()
    with SHARED_SYNC_AUTO_RATE_LIMIT_LOCK:
        anterior = float(SHARED_SYNC_AUTO_RATE_LIMIT.get(key) or 0)
        restante = intervalo - (agora - anterior)
        if anterior and restante > 0:
            return {
                "reason": "auto_interval",
                "interval_seconds": intervalo,
                "retry_after_seconds": int(restante) + 1,
            }
        SHARED_SYNC_AUTO_RATE_LIMIT[key] = agora
    return None

def _shared_sync_prune_local_backups(tenant_abs: str) -> None:
    keep = _shared_sync_local_backup_retention()
    if keep <= 0:
        return
    backup_root = os.path.abspath(os.path.join(tenant_abs, "_shared_sync_backups"))
    if not os.path.isdir(backup_root):
        return
    try:
        backups = []
        for entry in os.scandir(backup_root):
            if not entry.is_dir():
                continue
            try:
                backups.append((entry.stat().st_mtime, os.path.abspath(entry.path)))
            except OSError:
                continue
        backups.sort(reverse=True)
        for _mtime, path_abs in backups[keep:]:
            if path_abs.startswith(backup_root + os.sep):
                shutil.rmtree(path_abs, ignore_errors=True)
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao limpar backups locais antigos: %s", exc)

def _shared_sync_scope_public(scope: str) -> dict:
    item = SHARED_SYNC_SCOPES.get(scope) or {}
    return {
        "key": scope,
        "label": item.get("label") or scope,
        "description": item.get("description") or "",
        "sensitive": bool(item.get("sensitive")),
        "user_scoped": bool(item.get("user_scoped")),
        "patterns": list(item.get("patterns") or []),
    }

def _shared_sync_normalizar_scope_config(scope: str, valor: Any = None) -> dict:
    payload = valor if isinstance(valor, dict) else {}
    allowed = payload.get("allowed_users")
    if not isinstance(allowed, list):
        allowed = []
    allowed_norm = []
    vistos = set()
    for item in allowed:
        username = str(item or "").strip().lower()
        if username and username not in vistos:
            vistos.add(username)
            allowed_norm.append(username)
    conflict = str(payload.get("conflict") or "latest_wins").strip().lower()
    if conflict not in {"latest_wins", "manual"}:
        conflict = "latest_wins"
    return {
        "enabled": bool(payload.get("enabled", False)),
        "allowed_users": allowed_norm,
        "auto_pull": bool(payload.get("auto_pull", True)),
        "auto_push": bool(payload.get("auto_push", False)),
        "share_between_users": bool(payload.get("share_between_users", False)),
        "conflict": conflict,
    }

def _shared_sync_config_default(client_id: str) -> dict:
    return {
        "client_id": str(client_id or "default").strip() or "default",
        "updated_at": "",
        "updated_by": "",
        "scopes": {
            scope: _shared_sync_normalizar_scope_config(scope, {})
            for scope in SHARED_SYNC_SCOPES
        },
    }

def _shared_sync_config_local_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "shared_sync_config.json")

def _shared_sync_config_local_read(client_id: str) -> Optional[dict]:
    path = _shared_sync_config_local_path(client_id)
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao ler config local: %s", exc)
        return None

def _shared_sync_config_local_write(client_id: str, config: dict) -> None:
    path = _shared_sync_config_local_path(client_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def _shared_sync_state_path(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"shared_sync_state_{_shared_sync_safe_filename(username)}.json")


_SHARED_SYNC_STATE_LOCK = threading.RLock()


def _shared_sync_state_read(client_id: str, username: str) -> dict:
    with _SHARED_SYNC_STATE_LOCK:
        path = _shared_sync_state_path(client_id, username)
        try:
            if not os.path.exists(path):
                return {"scopes": {}}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"scopes": {}}
        except Exception:
            return {"scopes": {}}

def _shared_sync_state_write(client_id: str, username: str, data: dict) -> None:
    with _SHARED_SYNC_STATE_LOCK:
        path = _shared_sync_state_path(client_id, username)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = data if isinstance(data, dict) else {"scopes": {}}
        payload["updated_at"] = _shared_sync_now_iso()
        fd, temp_path = tempfile.mkstemp(
            prefix=".shared_sync_state_",
            suffix=".tmp",
            dir=os.path.dirname(path),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

def _shared_sync_immutable_snapshot_id(meta: dict) -> str:
    """Retorna somente IDs de snapshots v2 imutaveis e criptografados."""
    if not isinstance(meta, dict):
        return ""
    try:
        schema = int(meta.get("schema") or 0)
    except (TypeError, ValueError):
        return ""
    if schema != 2 or not bool(meta.get("encrypted")):
        return ""
    return str(meta.get("snapshot_id") or "").strip()


def _shared_sync_state_update(client_id: str, username: str, scope: str, meta: dict, direction: str) -> None:
    with _SHARED_SYNC_STATE_LOCK:
        state = _shared_sync_state_read(client_id, username)
        scopes = state.setdefault("scopes", {})
        current = scopes.get(scope) if isinstance(scopes.get(scope), dict) else {}
        entry = dict(current)
        entry.pop("skipped", None)
        entry.pop("reason", None)
        entry.update({
            "snapshot_hash": str((meta or {}).get("snapshot_hash") or ""),
            "snapshot_id": _shared_sync_immutable_snapshot_id(meta),
            "remote_updated_at": str((meta or {}).get("updated_at") or ""),
            "remote_updated_by": str((meta or {}).get("updated_by") or ""),
            "direction": direction,
            "synced_at": _shared_sync_now_iso(),
        })
        if direction == "pull" and "local_content_stamp" in (meta or {}):
            entry["local_content_stamp"] = str(meta.get("local_content_stamp") or "")
        elif direction == "push":
            entry.pop("local_content_stamp", None)
        if direction == "pull":
            for key in ("receipt_pending", "connection_conflicts"):
                if key in (meta or {}):
                    entry[key] = meta[key]
        else:
            entry.pop("receipt_pending", None)
            entry.pop("connection_conflicts", None)
        scopes[scope] = entry
        _shared_sync_state_write(client_id, username, state)


def _shared_sync_state_mark_skipped(
    client_id: str,
    username: str,
    scope: str,
    *,
    direction: str,
    reason: str,
) -> None:
    """Registra um skip sem apagar a base causal ja confirmada do escopo."""
    with _SHARED_SYNC_STATE_LOCK:
        state = _shared_sync_state_read(client_id, username)
        scopes = state.setdefault("scopes", {})
        current = scopes.get(scope) if isinstance(scopes.get(scope), dict) else {}
        entry = dict(current)
        entry.update({
            "direction": str(direction or ""),
            "skipped": True,
            "reason": str(reason or "already_current"),
            "synced_at": _shared_sync_now_iso(),
        })
        scopes[scope] = entry
        _shared_sync_state_write(client_id, username, state)

def _shared_sync_state_snapshot_hash(client_id: str, username: str, scope: str) -> str:
    state = _shared_sync_state_read(client_id, username)
    scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    return str(((scopes.get(scope) or {}).get("snapshot_hash")) or "")


def _shared_sync_state_snapshot_id(client_id: str, username: str, scope: str) -> str:
    state = _shared_sync_state_read(client_id, username)
    scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
    return str(((scopes.get(scope) or {}).get("snapshot_id")) or "")

def _shared_sync_pull_already_current(client_id: str, username: str, scope: str, meta: dict) -> bool:
    remote_hash = str((meta or {}).get("snapshot_hash") or "")
    if not remote_hash:
        return False
    with _SHARED_SYNC_STATE_LOCK:
        state = _shared_sync_state_read(client_id, username)
        scopes = state.get("scopes") if isinstance(state.get("scopes"), dict) else {}
        current = scopes.get(scope) if isinstance(scopes.get(scope), dict) else {}
        local_hash = str(current.get("snapshot_hash") or "")
        if not local_hash or local_hash != remote_hash:
            return False

        # Um hash legado prova qual snapshot foi visto, mas nao prova que o merge
        # antigo aplicou o mesmo bloco OAuth. Force uma reaplicacao segura antes
        # de conceder autoridade causal ao snapshot_id v2.
        remote_snapshot_id = _shared_sync_immutable_snapshot_id(meta)
        current_snapshot_id = str(current.get("snapshot_id") or "").strip()
        if remote_snapshot_id and not current_snapshot_id:
            return False
        if remote_snapshot_id and current_snapshot_id != remote_snapshot_id:
            current = dict(current)
            current["snapshot_id"] = remote_snapshot_id
            current["remote_updated_at"] = str((meta or {}).get("updated_at") or "")
            current["remote_updated_by"] = str((meta or {}).get("updated_by") or "")
            scopes[scope] = current
            state["scopes"] = scopes
            _shared_sync_state_write(client_id, username, state)
        return True

def _shared_sync_pull_skip_payload(scope: str, meta: dict, *, direction: str = "pull", extra: Optional[dict] = None) -> dict:
    payload = {
        "scope": scope,
        "success": True,
        "direction": direction,
        "skipped": True,
        "reason": "already_current",
        "file_count": 0,
        "backup_dir": "",
        "snapshot_hash": str((meta or {}).get("snapshot_hash") or ""),
        "remote_updated_at": str((meta or {}).get("updated_at") or ""),
        "remote_updated_by": str((meta or {}).get("updated_by") or ""),
        "remote_machine_id": str((meta or {}).get("machine_id") or ""),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload

def _shared_sync_user_share_state_scope(link_id: str, direction_key: str, scope: str) -> str:
    return f"user-share:{str(link_id or '').strip()}:{str(direction_key or '').strip()}:{scope}"

def _shared_sync_user_share_known_keys(client_id: str, username: str, link_id: str, scope: str) -> set[str]:
    state = _shared_sync_state_read(client_id, username)
    shares = state.get("user_share_known") if isinstance(state.get("user_share_known"), dict) else {}
    link_state = shares.get(str(link_id or "")) if isinstance(shares.get(str(link_id or "")), dict) else {}
    scope_state = link_state.get(scope) if isinstance(link_state.get(scope), dict) else {}
    return {
        str(key or "").strip()
        for key in (scope_state.get("keys") or [])
        if str(key or "").strip()
    }

def _shared_sync_user_share_known_count(state: dict, link_id: str, scope: str) -> int:
    shares = state.get("user_share_known") if isinstance(state.get("user_share_known"), dict) else {}
    link_state = shares.get(str(link_id or "")) if isinstance(shares.get(str(link_id or "")), dict) else {}
    scope_state = link_state.get(scope) if isinstance(link_state.get(scope), dict) else {}
    try:
        return int(scope_state.get("count") or len(scope_state.get("keys") or []))
    except Exception:
        return 0

def _shared_sync_user_share_add_known_keys(
    client_id: str,
    username: str,
    link_id: str,
    scope: str,
    keys: list[str] | set[str] | tuple[str, ...],
) -> None:
    novos = {
        str(key or "").strip()
        for key in (keys or [])
        if str(key or "").strip()
    }
    if not novos:
        return
    with _SHARED_SYNC_STATE_LOCK:
        state = _shared_sync_state_read(client_id, username)
        shares = state.setdefault("user_share_known", {})
        link_state = shares.setdefault(str(link_id or ""), {})
        scope_state = link_state.setdefault(scope, {})
        atuais = {
            str(key or "").strip()
            for key in (scope_state.get("keys") or [])
            if str(key or "").strip()
        }
        atuais.update(novos)
        scope_state["keys"] = sorted(atuais)
        scope_state["count"] = len(atuais)
        scope_state["updated_at"] = _shared_sync_now_iso()
        _shared_sync_state_write(client_id, username, state)

def _shared_sync_config_normalizar(client_id: str, data: Optional[dict]) -> dict:
    config = _shared_sync_config_default(client_id)
    payload = data if isinstance(data, dict) else {}
    config["updated_at"] = str(payload.get("updated_at") or "")
    config["updated_by"] = str(payload.get("updated_by") or "")
    scopes_in = payload.get("scopes") if isinstance(payload.get("scopes"), dict) else {}
    for scope in SHARED_SYNC_SCOPES:
        config["scopes"][scope] = _shared_sync_normalizar_scope_config(scope, scopes_in.get(scope))
    return config

def _shared_sync_config_read(client_id: str) -> dict:
    client_norm = str(client_id or "default").strip() or "default"
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is not None:
        try:
            snap = db.collection(_firebase_shared_sync_config_collection_name()).document(_shared_sync_config_doc_id(client_norm)).get()
            if snap.exists:
                config = _shared_sync_config_normalizar(client_norm, snap.to_dict() or {})
                _shared_sync_config_local_write(client_norm, config)
                return config
        except Exception as exc:
            logger.warning("[SHARED-SYNC] Falha ao ler config no Firebase: %s", exc)
    return _shared_sync_config_normalizar(client_norm, _shared_sync_config_local_read(client_norm))

def _shared_sync_config_save(client_id: str, payload: dict, updated_by: str = "") -> dict:
    client_norm = str(client_id or "default").strip() or "default"
    atual = _shared_sync_config_read(client_norm)
    scopes_payload = payload.get("scopes") if isinstance(payload, dict) and isinstance(payload.get("scopes"), dict) else {}
    for scope in SHARED_SYNC_SCOPES:
        if scope in scopes_payload:
            atual["scopes"][scope] = _shared_sync_normalizar_scope_config(scope, scopes_payload.get(scope))
    atual["updated_at"] = _shared_sync_now_iso()
    atual["updated_by"] = str(updated_by or "").strip().lower()
    _shared_sync_config_local_write(client_norm, atual)
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is not None:
        try:
            db.collection(_firebase_shared_sync_config_collection_name()).document(_shared_sync_config_doc_id(client_norm)).set(atual, merge=True)
            atual["backend"] = "firebase"
            return atual
        except Exception as exc:
            logger.warning("[SHARED-SYNC] Falha ao salvar config no Firebase: %s", exc)
    atual["backend"] = "local"
    return atual

def _shared_sync_session(authorization: Optional[str], client_id: str) -> dict:
    sessao = _payload_sessao_por_authorization(authorization)
    username = str(sessao.get("username") or "").strip().lower()
    client_sessao = str(sessao.get("client_id") or "default").strip() or "default"
    client_norm = str(client_id or client_sessao or "default").strip() or "default"
    if client_sessao != client_norm:
        raise HTTPException(status_code=403, detail="Sessao invalida para esse cliente.")
    usuario = _obter_usuario_sql(username)
    permissoes = _carregar_permissoes_usuario(username, client_norm)
    return {
        "username": username,
        "client_id": client_norm,
        "machine_id": str(sessao.get("machine_id") or "").strip(),
        "usuario": usuario,
        "permissions": permissoes,
        "is_admin": bool(permissoes.get("full") is True or permissoes.get("admin_usuarios") is True),
    }

def _shared_sync_require_admin(authorization: Optional[str], client_id: str) -> dict:
    sessao = _shared_sync_session(authorization, client_id)
    if not sessao.get("is_admin"):
        raise HTTPException(status_code=403, detail="Apenas administradores podem alterar compartilhamento.")
    return sessao

def _shared_sync_user_allowed(config: dict, scope: str, sessao: dict) -> bool:
    scope_cfg = ((config or {}).get("scopes") or {}).get(scope) or {}
    if not scope_cfg.get("enabled"):
        return False
    if sessao.get("is_admin"):
        return True
    allowed = scope_cfg.get("allowed_users") or []
    if not allowed:
        return True
    return str(sessao.get("username") or "").strip().lower() in allowed

def _shared_sync_machine_scope_allowed(scope: str, sessao: dict) -> bool:
    if scope not in SHARED_SYNC_SCOPES:
        return False
    permissoes = sessao.get("permissions") if isinstance(sessao.get("permissions"), dict) else {}
    if sessao.get("is_admin") or permissoes.get("full") is True:
        return True
    mapa = {
        "cadastro": "cadastro",
        "lojas_integracoes": "integracao",
        "vendas": "vendas",
        "favoritos_historico": "favoritos",
        "sku_campos_pesquisa": "favoritos",
        "favoritos_planilhas": "favoritos",
        "anuncios_ml": "favoritos",
    }
    chave = mapa.get(scope)
    return bool(chave and permissoes.get(chave) is True)

def _shared_sync_machine_allowed_scopes(sessao: dict) -> list[str]:
    allowed = [scope for scope in SHARED_SYNC_SCOPES if _shared_sync_machine_scope_allowed(scope, sessao)]
    if "lojas_integracoes" in allowed:
        from backend.services.central_accounts_store_index import assert_legacy_sync_allowed
        try:
            assert_legacy_sync_allowed(sessao.get("client_id"))
        except HTTPException:
            allowed.remove("lojas_integracoes")
    return allowed

def _shared_sync_machine_config_normalizar(sessao: dict, payload: Optional[dict]) -> dict:
    data = payload if isinstance(payload, dict) else {}
    allowed = set(_shared_sync_machine_allowed_scopes(sessao))
    scopes_raw = data.get("scopes") if isinstance(data.get("scopes"), list) else []
    scopes = []
    for scope in scopes_raw:
        scope_norm = str(scope or "").strip()
        if scope_norm in allowed and scope_norm not in scopes:
            scopes.append(scope_norm)
    return {
        "enabled": bool(data.get("enabled", False)),
        "scopes": scopes,
        # Modo v3: preferências antigas não podem reativar transferências.
        # A leitura normaliza somente esta sessão e preserva o histórico.
        "auto_pull": False,
        "auto_push": False,
        "auto_pull_explicit": True,
        "mode_version": 3,
        "updated_at": str(data.get("updated_at") or ""),
    }

def _shared_sync_machine_config_read(sessao: dict) -> dict:
    state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
    return _shared_sync_machine_config_normalizar(sessao, state.get("machine_sync") if isinstance(state, dict) else None)

def _shared_sync_machine_config_save(sessao: dict, payload: dict) -> dict:
    explicit_payload = dict(payload) if isinstance(payload, dict) else {}
    explicit_payload["auto_pull_explicit"] = True
    explicit_payload["mode_version"] = 3
    config = _shared_sync_machine_config_normalizar(sessao, explicit_payload)
    config["updated_at"] = _shared_sync_now_iso()
    with _SHARED_SYNC_STATE_LOCK:
        state = _shared_sync_state_read(sessao.get("client_id"), sessao.get("username") or "")
        state["machine_sync"] = config
        _shared_sync_state_write(sessao.get("client_id"), sessao.get("username") or "", state)
    return config

def _shared_sync_machine_resolver_scopes(sessao: dict, requested: Optional[list[str]] = None, require_enabled: bool = True) -> list[str]:
    config = _shared_sync_machine_config_read(sessao)
    if require_enabled and not config.get("enabled"):
        raise HTTPException(status_code=400, detail="Sincronizacao entre minhas maquinas esta desativada.")
    permitidos = set(_shared_sync_machine_allowed_scopes(sessao))
    configurados = [scope for scope in (config.get("scopes") or []) if scope in permitidos]
    if requested:
        base = [str(scope or "").strip() for scope in requested if str(scope or "").strip()]
        scopes = [scope for scope in base if scope in permitidos and scope in configurados]
    else:
        scopes = configurados
    if not scopes:
        raise HTTPException(status_code=400, detail="Selecione pelo menos um dado permitido para sincronizar.")
    return scopes

def _shared_sync_resolver_scopes(config: dict, requested: Optional[list[str]], sessao: dict) -> list[str]:
    if requested:
        scopes = [str(scope or "").strip() for scope in requested]
    else:
        scopes = list(SHARED_SYNC_SCOPES.keys())
    saida = []
    for scope in scopes:
        if scope not in SHARED_SYNC_SCOPES:
            raise HTTPException(status_code=400, detail=f"Escopo de compartilhamento invalido: {scope}")
        if _shared_sync_user_allowed(config, scope, sessao):
            saida.append(scope)
    if not saida:
        raise HTTPException(status_code=403, detail="Nenhum escopo de dados compartilhados habilitado para este usuario.")
    return saida

configure_shared_sync_config_runtime()

__all__ = [
    "configure_shared_sync_config_runtime",
    "_firebase_shared_sync_config_collection_name",
    "_firebase_shared_sync_collection_name",
    "_firebase_shared_sync_chunks_collection_name",
    "_firebase_shared_sync_keyrings_collection_name",
    "_firebase_shared_sync_user_invites_collection_name",
    "_firebase_shared_sync_user_links_collection_name",
    "_shared_sync_auto_interval_seconds",
    "_shared_sync_machine_auto_interval_seconds",
    "_shared_sync_auto_enabled",
    "_shared_sync_manual_only_payload",
    "_shared_sync_auto_rate_limit",
    "_shared_sync_prune_local_backups",
    "_shared_sync_scope_public",
    "_shared_sync_normalizar_scope_config",
    "_shared_sync_config_default",
    "_shared_sync_config_local_path",
    "_shared_sync_config_local_read",
    "_shared_sync_config_local_write",
    "_shared_sync_state_path",
    "_shared_sync_state_read",
    "_shared_sync_state_write",
    "_shared_sync_state_update",
    "_shared_sync_state_snapshot_hash",
    "_shared_sync_state_snapshot_id",
    "_shared_sync_state_mark_skipped",
    "_shared_sync_pull_already_current",
    "_shared_sync_pull_skip_payload",
    "_shared_sync_user_share_state_scope",
    "_shared_sync_user_share_known_keys",
    "_shared_sync_user_share_known_count",
    "_shared_sync_user_share_add_known_keys",
    "_shared_sync_config_normalizar",
    "_shared_sync_config_read",
    "_shared_sync_config_save",
    "_shared_sync_session",
    "_shared_sync_require_admin",
    "_shared_sync_user_allowed",
    "_shared_sync_machine_scope_allowed",
    "_shared_sync_machine_allowed_scopes",
    "_shared_sync_machine_config_normalizar",
    "_shared_sync_machine_config_read",
    "_shared_sync_machine_config_save",
    "_shared_sync_machine_resolver_scopes",
    "_shared_sync_resolver_scopes",
]
