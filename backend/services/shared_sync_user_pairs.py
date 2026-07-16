"""Shared Sync pair package preparation, pair pull/push, and propagation helpers."""

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


def configure_shared_sync_user_pairs_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_push_pair_scope(source_sessao: dict, target: dict, scope: str, machine_id: str = "", link_id: str = "", invite_id: str = "") -> dict:
    item_ref = {
        "source_client_id": source_sessao.get("client_id"),
        "source_username": source_sessao.get("username"),
        "source_is_admin": _shared_sync_session_is_admin(source_sessao),
        "target_client_id": target.get("client_id"),
        "target_username": target.get("username"),
    }
    if not _shared_sync_scope_permitido_entre_clientes(item_ref, scope, source_sessao):
        raise HTTPException(status_code=400, detail="Lojas e integracoes nao podem ser compartilhadas entre clientes diferentes.")
    link_ref = {
        "id": str(link_id or _shared_sync_link_id_for_pair({
            "source_client_id": source_sessao.get("client_id"),
            "source_username": source_sessao.get("username"),
            "target_client_id": target.get("client_id"),
            "target_username": target.get("username"),
        })),
        "source_client_id": source_sessao.get("client_id"),
        "source_username": source_sessao.get("username"),
        "target_client_id": target.get("client_id"),
        "target_username": target.get("username"),
    }
    direction_key = "source_to_target"
    bundle_id = _shared_sync_pair_doc_id(
        source_sessao.get("client_id"),
        source_sessao.get("username"),
        target.get("client_id"),
        target.get("username"),
        scope,
    )
    known_keys = None if scope == "lojas_integracoes" else _shared_sync_user_share_known_keys(
        source_sessao.get("client_id"),
        source_sessao.get("username") or "",
        link_ref["id"],
        scope,
    )
    result = _shared_sync_push_scope(
        source_sessao.get("client_id"),
        scope,
        source_sessao,
        machine_id,
        bundle_id=bundle_id,
        extra_meta={
            "visibility": "user-share",
            "source_client_id": _shared_sync_normalizar_client_id(source_sessao.get("client_id")),
            "source_username": _shared_sync_normalizar_username(source_sessao.get("username")),
            "target_client_id": _shared_sync_normalizar_client_id(target.get("client_id")),
            "target_username": _shared_sync_normalizar_username(target.get("username")),
            "invite_id": str(invite_id or ""),
            "link_id": str(link_id or ""),
        },
        user_only=True,
        state_scope=_shared_sync_user_share_state_scope(link_ref["id"], direction_key, scope),
        known_keys=known_keys,
        allow_empty_delta=True,
        sanitize_user_share_oauth=False,
        skip_if_remote_hash_matches=(scope == "lojas_integracoes"),
    )
    _shared_sync_user_share_add_known_keys(source_sessao.get("client_id"), source_sessao.get("username") or "", link_ref["id"], scope, result.get("item_keys") or [])
    return result

def _shared_sync_exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return str(exc or "").strip() or "Erro ao preparar dados."

def _shared_sync_scope_label(scope: str) -> str:
    return (SHARED_SYNC_SCOPES.get(scope) or {}).get("label") or str(scope or "")

def _shared_sync_save_invite_prepare_progress(
    invite_id: str,
    *,
    status: str = "preparing",
    done: int = 0,
    total: int = 0,
    current_scope: str = "",
    message: str = "",
) -> None:
    try:
        invite = _shared_sync_get_invite(invite_id)
    except Exception:
        return
    if _shared_sync_invite_prepare_should_stop(invite):
        return
    total_int = max(0, int(total or 0))
    done_int = max(0, min(total_int or done, int(done or 0)))
    final_status = str(status or "preparing").strip().lower()
    if final_status in {"ready", "partial", "failed"}:
        progress = 100
    elif total_int:
        progress = int(round((done_int / total_int) * 100))
        if done_int < total_int and (current_scope or message):
            progress = max(1, progress)
    else:
        progress = 0
    progress = max(0, min(100, progress))
    agora = _shared_sync_now_iso()
    invite["prepare_status"] = final_status
    invite["prepare_done"] = done_int
    invite["prepare_total"] = total_int
    invite["prepare_progress"] = progress
    invite["prepare_current_scope"] = str(current_scope or "")
    invite["prepare_current_label"] = _shared_sync_scope_label(current_scope) if current_scope else ""
    invite["prepare_message"] = str(message or "").strip()
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    if final_status == "preparing" and not invite.get("prepare_started_at"):
        invite["prepare_started_at"] = agora
    if final_status in {"ready", "partial", "failed"}:
        invite["prepare_finished_at"] = agora
    _shared_sync_save_invite(invite)

def _shared_sync_invite_prepare_should_stop(invite: dict) -> bool:
    status = str((invite or {}).get("status") or "").strip().lower()
    prepare_status = str((invite or {}).get("prepare_status") or "").strip().lower()
    return status in {"cancelled", "canceled", "deleted"} or prepare_status in {"cancelled", "canceled", "deleted"}

def _shared_sync_invite_prepare_stopped(invite_id: str) -> bool:
    try:
        invite = _shared_sync_get_invite(invite_id)
    except Exception:
        return True
    return _shared_sync_invite_prepare_should_stop(invite)

def _shared_sync_mark_invite_prepare_failed(invite_id: str, exc: Exception) -> None:
    try:
        invite = _shared_sync_get_invite(invite_id)
    except Exception:
        return
    if _shared_sync_invite_prepare_should_stop(invite):
        return
    message = _shared_sync_exception_message(exc)
    agora = _shared_sync_now_iso()
    invite["status"] = "failed"
    invite["prepare_status"] = "failed"
    invite["prepare_progress"] = 100
    invite["prepare_message"] = message
    invite["prepare_finished_at"] = agora
    invite["prepare_errors"] = [{
        "scope": str(invite.get("prepare_current_scope") or ""),
        "label": str(invite.get("prepare_current_label") or "Preparação"),
        "message": message,
    }]
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    _shared_sync_save_invite(invite)

def _shared_sync_prepare_invite_packages(invite_id: str, source_sessao: dict, destino: dict, scopes: list[str], machine_id: str = "") -> None:
    try:
        _shared_sync_prepare_invite_packages_impl(invite_id, source_sessao, destino, scopes, machine_id)
    except Exception as exc:
        logger.exception("[SHARED-SYNC] Erro inesperado ao preparar convite %s: %s", invite_id, exc)
        _shared_sync_mark_invite_prepare_failed(invite_id, exc)

def _shared_sync_compactar_resultado_preparo(result: dict) -> dict:
    result = result if isinstance(result, dict) else {}
    compacto = {
        "scope": result.get("scope") or "",
        "success": bool(result.get("success")),
        "direction": result.get("direction") or "",
        "id": result.get("id") or "",
        "file_count": int(result.get("file_count") or 0),
        "item_count": int(result.get("item_count") or 0),
        "delta": bool(result.get("delta")),
        "chunk_count": int(result.get("chunk_count") or 0),
        "bundle_bytes": int(result.get("bundle_bytes") or 0),
        "snapshot_hash": result.get("snapshot_hash") or "",
        "updated_at": result.get("updated_at") or "",
    }
    if result.get("skipped"):
        compacto["skipped"] = True
        compacto["reason"] = result.get("reason") or ""
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    if warnings:
        compacto["warnings"] = warnings[:10]
    return compacto

def _shared_sync_prepare_invite_packages_impl(invite_id: str, source_sessao: dict, destino: dict, scopes: list[str], machine_id: str = "") -> None:
    bundles: dict[str, str] = {}
    results: list[dict] = []
    errors: list[dict] = []
    scopes = [scope for scope in (scopes or []) if scope in SHARED_SYNC_SCOPES]
    total = len(scopes)
    _shared_sync_save_invite_prepare_progress(
        invite_id,
        status="preparing",
        done=0,
        total=total,
        message="Iniciando preparação dos dados.",
    )
    link_id = _shared_sync_link_id_for_pair({
        "source_client_id": source_sessao.get("client_id"),
        "source_username": source_sessao.get("username"),
        "target_client_id": destino.get("client_id"),
        "target_username": destino.get("username"),
    })
    for index, scope in enumerate(scopes, start=1):
        if _shared_sync_invite_prepare_stopped(invite_id):
            return
        label = _shared_sync_scope_label(scope)
        _shared_sync_save_invite_prepare_progress(
            invite_id,
            status="preparing",
            done=index - 1,
            total=total,
            current_scope=scope,
            message=f"Preparando {label}.",
        )
        try:
            result = _shared_sync_push_pair_scope(source_sessao, destino, scope, machine_id, link_id=link_id, invite_id=invite_id)
            bundles[scope] = result.get("id") or _shared_sync_pair_doc_id(
                source_sessao.get("client_id"),
                source_sessao.get("username"),
                destino.get("client_id"),
                destino.get("username"),
                scope,
            )
            results.append(_shared_sync_compactar_resultado_preparo(result))
            _shared_sync_save_invite_prepare_progress(
                invite_id,
                status="preparing",
                done=index,
                total=total,
                current_scope=scope,
                message=f"{label} preparado.",
            )
        except Exception as exc:
            if _shared_sync_invite_prepare_stopped(invite_id):
                return
            logger.warning("[SHARED-SYNC] Falha ao preparar escopo %s do convite %s: %s", scope, invite_id, exc)
            errors.append({
                "scope": scope,
                "label": label,
                "message": _shared_sync_exception_message(exc),
            })
            _shared_sync_save_invite_prepare_progress(
                invite_id,
                status="preparing",
                done=index,
                total=total,
                current_scope=scope,
                message=f"{label} falhou; seguindo para o próximo dado.",
            )

    try:
        invite = _shared_sync_get_invite(invite_id)
    except Exception:
        return
    if _shared_sync_invite_prepare_should_stop(invite):
        return

    agora = _shared_sync_now_iso()
    invite["bundles"] = bundles
    invite["directional_bundles"] = {"source_to_target": dict(bundles)}
    invite["prepared_results"] = results
    invite["prepare_errors"] = errors
    invite["prepare_done"] = total
    invite["prepare_total"] = total
    invite["prepare_progress"] = 100
    invite["prepare_current_scope"] = ""
    invite["prepare_current_label"] = ""
    invite["prepare_finished_at"] = agora
    invite["updated_at"] = agora
    invite["updated_ts"] = int(time.time())
    if bundles:
        invite["scopes"] = [scope for scope in scopes if scope in bundles]
        invite["prepare_status"] = "partial" if errors else "ready"
        invite["prepare_message"] = "Preparação parcial. Alguns dados falharam." if errors else "Dados preparados. Aguardando aceite."
    else:
        invite["status"] = "failed"
        invite["prepare_status"] = "failed"
        invite["scopes"] = scopes
        invite["prepare_message"] = "Nenhum dado foi preparado. Confira as falhas."
    _shared_sync_save_invite(invite)

def _shared_sync_start_invite_prepare_thread(invite_id: str, source_sessao: dict, destino: dict, scopes: list[str], machine_id: str = "") -> None:
    # Convites v1 nao preparam mais pacotes em segundo plano. O endpoint antigo
    # agora cria um vinculo direto e o pacote so nasce apos previa + Enviar agora.
    return None

def _shared_sync_pull_pair_scope(target_sessao: dict, link: dict, scope: str) -> dict:
    if not _shared_sync_scope_permitido_entre_clientes(link, scope, target_sessao):
        raise HTTPException(status_code=400, detail="Lojas e integracoes nao podem ser importadas entre clientes diferentes.")
    my_direction = _shared_sync_link_direction_for_session(target_sessao, link)
    receive_direction = _shared_sync_link_reverse_direction(my_direction)
    if _shared_sync_lojas_integracoes_cross_client_volta_bloqueada(link, scope, receive_direction):
        raise HTTPException(status_code=400, detail="Lojas e integracoes nao podem voltar do cliente compartilhado para o cliente de origem.")
    bundle_id = _shared_sync_link_bundle_id_for_direction(link, scope, receive_direction)
    meta = _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    state_scope = _shared_sync_user_share_state_scope(link.get("id"), receive_direction, scope)
    if _shared_sync_pull_already_current(target_sessao.get("client_id"), target_sessao.get("username") or "", state_scope, meta):
        return _shared_sync_pull_skip_payload(
            scope,
            meta,
            extra={"sync_direction": receive_direction, "link_id": link.get("id"), "item_count": int(meta.get("item_count") or 0)},
        )
    bundle, meta = _shared_sync_obter_bundle_por_id(bundle_id, meta)
    manifest = _shared_sync_manifest_from_bundle(bundle)
    scope_config = {"user_share": True, "share_between_users": True}
    result = _shared_sync_aplicar_pacote(target_sessao.get("client_id"), scope, bundle, target_sessao.get("username") or "", scope_config)
    _shared_sync_state_update(target_sessao.get("client_id"), target_sessao.get("username") or "", state_scope, meta, "pull")
    _shared_sync_user_share_add_known_keys(
        target_sessao.get("client_id"),
        target_sessao.get("username") or "",
        link.get("id"),
        scope,
        manifest.get("item_keys") or [],
    )
    return {
        "scope": scope,
        "success": True,
        "direction": "pull",
        "sync_direction": receive_direction,
        "link_id": link.get("id"),
        "file_count": result.get("file_count") or 0,
        "item_count": manifest.get("item_count") or meta.get("item_count") or 0,
        "backup_dir": result.get("backup_dir") or "",
        "snapshot_hash": meta.get("snapshot_hash") or "",
        "remote_updated_at": meta.get("updated_at") or "",
        "remote_updated_by": meta.get("updated_by") or "",
    }

def _shared_sync_push_link_scope(source_sessao: dict, link: dict, scope: str, machine_id: str = "", skip_if_remote_hash_matches: bool = False) -> dict:
    direction_key = _shared_sync_link_direction_for_session(source_sessao, link)
    admin_origem_atualizado = False
    if direction_key == "source_to_target":
        link_marcado = _shared_sync_marcar_admin_origem(link)
        if _shared_sync_session_is_admin(source_sessao) and not bool(link_marcado.get("source_is_admin")):
            link_marcado = dict(link_marcado)
            link_marcado["source_is_admin"] = True
        admin_origem_atualizado = bool(link_marcado.get("source_is_admin")) and not bool((link or {}).get("source_is_admin"))
        link = link_marcado
    if not _shared_sync_scope_permitido_entre_clientes(link, scope, source_sessao):
        raise HTTPException(status_code=400, detail="Lojas e integracoes nao podem ser compartilhadas entre clientes diferentes.")
    if _shared_sync_lojas_integracoes_cross_client_volta_bloqueada(link, scope, direction_key):
        raise HTTPException(status_code=400, detail="Lojas e integracoes nao podem voltar do cliente compartilhado para o cliente de origem.")
    from_client, from_username, to_client, to_username = _shared_sync_link_direction_parts(link, direction_key)
    bundle_id = _shared_sync_link_bundle_id_for_direction(link, scope, direction_key)
    known_keys = None if scope == "lojas_integracoes" else _shared_sync_user_share_known_keys(
        source_sessao.get("client_id"),
        source_sessao.get("username") or "",
        link.get("id"),
        scope,
    )
    result = _shared_sync_push_scope(
        source_sessao.get("client_id"),
        scope,
        source_sessao,
        machine_id,
        bundle_id=bundle_id,
        extra_meta={
            "visibility": "user-share",
            "source_client_id": from_client,
            "source_username": from_username,
            "target_client_id": to_client,
            "target_username": to_username,
            "sync_direction": direction_key,
            "invite_id": link.get("invite_id") or "",
            "link_id": link.get("id") or "",
        },
        user_only=True,
        state_scope=_shared_sync_user_share_state_scope(link.get("id"), direction_key, scope),
        known_keys=known_keys,
        allow_empty_delta=False,
        sanitize_user_share_oauth=False,
        skip_if_remote_hash_matches=(skip_if_remote_hash_matches or scope == "lojas_integracoes"),
    )
    if result.get("skipped"):
        if admin_origem_atualizado:
            link["updated_at"] = _shared_sync_now_iso()
            link["updated_ts"] = int(time.time())
            _shared_sync_save_link(link)
        state = _shared_sync_state_read(source_sessao.get("client_id"), source_sessao.get("username") or "")
        scopes_state = state.setdefault("scopes", {})
        scopes_state[_shared_sync_user_share_state_scope(link.get("id"), direction_key, scope)] = {
            "direction": "push",
            "skipped": True,
            "reason": result.get("reason") or "already_shared",
            "synced_at": _shared_sync_now_iso(),
        }
        _shared_sync_state_write(source_sessao.get("client_id"), source_sessao.get("username") or "", state)
        return result
    _shared_sync_link_set_bundle_for_direction(link, scope, direction_key, result.get("id") or bundle_id)
    _shared_sync_user_share_add_known_keys(source_sessao.get("client_id"), source_sessao.get("username") or "", link.get("id"), scope, result.get("item_keys") or [])
    link["scopes"] = _shared_sync_unir_scopes(link.get("scopes"), [scope])
    link["updated_at"] = _shared_sync_now_iso()
    link["updated_ts"] = int(time.time())
    _shared_sync_save_link(link)
    return result

def _shared_sync_propagar_lojas_integracoes_cliente(client_id: str, machine_id: str = "") -> list[dict]:
    return []

configure_shared_sync_user_pairs_runtime()

__all__ = [
    "configure_shared_sync_user_pairs_runtime",
    "_shared_sync_push_pair_scope",
    "_shared_sync_exception_message",
    "_shared_sync_scope_label",
    "_shared_sync_save_invite_prepare_progress",
    "_shared_sync_invite_prepare_should_stop",
    "_shared_sync_invite_prepare_stopped",
    "_shared_sync_mark_invite_prepare_failed",
    "_shared_sync_prepare_invite_packages",
    "_shared_sync_compactar_resultado_preparo",
    "_shared_sync_prepare_invite_packages_impl",
    "_shared_sync_start_invite_prepare_thread",
    "_shared_sync_pull_pair_scope",
    "_shared_sync_push_link_scope",
    "_shared_sync_propagar_lojas_integracoes_cliente",
]
