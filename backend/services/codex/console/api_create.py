"""Codex console api create component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)
from .contracts import CodexTaskRequest
from .task_creation import _codex_create_task_for_session


def codex_status(request: Request, authorization: Optional[str] = Header(default=None)):
    sessao = _codex_require_authenticated(request, authorization)
    return _codex_status_for_session(sessao)


async def codex_upload_attachments(
    request: Request,
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(default=""),
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    uploads = list(files or [])
    if not uploads:
        raise HTTPException(status_code=400, detail="Envie ao menos um arquivo.")
    if len(uploads) > CODEX_ATTACHMENT_MAX_COUNT:
        raise HTTPException(status_code=400, detail=f"Limite de {CODEX_ATTACHMENT_MAX_COUNT} arquivos por envio.")

    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "user")
    # O campo continua aceito para clientes antigos, mas anexos sempre pertencem
    # a conversa app canonica do usuario autenticado.
    del conversation_id
    state = _codex_load_or_create_conversation_state(client_id, username, channel="app")
    conv_id = str(
        state.get("conversation_id")
        or _codex_universal_conversation_id(client_id, username)
    )
    saved: list[dict[str, Any]] = []
    saved_paths: list[Path] = []
    total_bytes = 0
    try:
        _codex_cleanup_old_attachments()
        target_dir = _codex_attachment_dir(client_id, username, conv_id)
        for upload in uploads:
            original_name = _codex_safe_filename(upload.filename or "arquivo")
            content = await upload.read()
            size = len(content or b"")
            if size <= 0:
                raise HTTPException(status_code=400, detail=f"Arquivo vazio: {original_name}")
            if size > CODEX_ATTACHMENT_MAX_BYTES:
                raise HTTPException(status_code=413, detail=f"Arquivo acima de 25 MB: {original_name}")
            total_bytes += size
            if total_bytes > CODEX_ATTACHMENT_TOTAL_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Limite total de 100 MB por envio excedido.")

            file_id = uuid.uuid4().hex
            target = (target_dir / f"{file_id}_{original_name}").resolve()
            base = _codex_attachments_base_dir().resolve()
            try:
                common = os.path.commonpath([str(base), str(target)])
            except Exception:
                common = ""
            if common != str(base):
                raise HTTPException(status_code=400, detail="Nome de arquivo invalido.")
            target.write_bytes(content)
            saved_paths.append(target)
            saved.append(
                _codex_attachment_public_payload(
                    target,
                    original_name,
                    upload.content_type or "application/octet-stream",
                    size,
                )
            )
    except HTTPException:
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise
    except Exception as exc:
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Falha ao salvar anexo: {exc}") from exc

    return {
        "success": True,
        "attachments": saved,
        "conversation_id": conv_id,
        "conversation_generation": int(visible_state.get("generation") or 1),
    }





def codex_criar_tarefa(
    payload: CodexTaskRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_criar_tarefa_para_sessao(payload, sessao)














def codex_criar_tarefa_para_sessao(
    payload: CodexTaskRequest,
    sessao: dict[str, Any],
    *,
    origin: str = "app",
    channel_metadata: Optional[dict[str, Any]] = None,
    trusted_model_config: bool = False,
):
    return _codex_create_task_for_session(
        payload,
        sessao,
        origin=origin,
        channel_metadata=channel_metadata,
        trusted_model_config=trusted_model_config,
    )


def codex_listar_tarefas(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
    summary: bool = False,
    channel: str = "",
):
    sessao = _codex_require_authenticated(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    max_items = max(1, min(100, int(limit or 20)))
    channel_filter = str(channel or "").strip().lower()
    if channel_filter not in {"", "app", "whatsapp", "unified"}:
        raise HTTPException(status_code=400, detail="Canal de conversa invalido.")
    if bool(sessao.get("is_full")):
        _codex_backfill_assistant_report_tasks(
            client_id,
            str(sessao.get("username") or ""),
            max_items,
        )
    tasks: list[dict[str, Any]] = []
    try:
        paths = sorted(
            Path(_codex_info_dir()).glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        paths = []

    deleted_ids = _codex_deleted_conversation_ids(client_id, username)
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                task = json.load(fh)
            if isinstance(task, dict):
                if str(task.get("client_id") or "default") != client_id:
                    continue
                if _codex_task_conversation_keys(task) & deleted_ids:
                    continue
                created_by = str(task.get("created_by") or "").strip().lower()
                if not created_by or created_by != username:
                    continue
                if channel_filter == "unified":
                    if _codex_task_channel(task) != "app":
                        continue
                elif channel_filter and _codex_task_channel(task) != channel_filter:
                    continue
                tasks.append(_codex_task_summary(task) if summary else _codex_public_task(task))
                if len(tasks) >= max_items:
                    break
        except Exception:
            continue
    return {"success": True, "tasks": tasks}





def codex_registrar_interacao_whatsapp_externa(*, client_id: str, username: str, phone: str, prompt: str, response: str, model: str='black-jhon-voice', source: str='whatsapp_call', call_id: str='', duration_seconds: int=0, sources: Optional[list[Any]]=None, shared_continuity: bool=False, delivery_confirmed: bool=False, subject_id: str='', message_type: str='voice_call') -> dict[str, Any]:
    """Compatibility stub for the removed WhatsApp-to-Sidebar history bridge."""

    del client_id, username, phone, prompt, response, model, source, call_id
    del duration_seconds, sources, shared_continuity, delivery_confirmed, subject_id, message_type
    raise RuntimeError("whatsapp_sidebar_continuity_removed")
__codex_dependencies__ = ['CODEX_ATTACHMENT_MAX_BYTES', 'CODEX_ATTACHMENT_MAX_COUNT', 'CODEX_ATTACHMENT_TOTAL_MAX_BYTES', 'CODEX_SIDEBAR_TASK_PROMPT_VERSION', 'CODEX_SIDEBAR_TASK_SCHEMA_VERSION', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_codex_agent_guidance_context', '_codex_agent_is_report_request', '_codex_agent_mode_enabled', '_codex_ai_telemetry_instance', '_codex_attachment_dir', '_codex_attachment_public_payload', '_codex_attachments_base_dir', '_codex_backfill_assistant_report_tasks', '_codex_base_dir', '_codex_base_info_dir', '_codex_build_scope', '_codex_clean_text', '_codex_cleanup_old_attachments', '_codex_context_stats', '_codex_deadline_at', '_codex_decide_model', '_codex_deleted_conversation_ids', '_codex_development_requires_desktop_detail', '_codex_enabled', '_codex_hmac_identifier', '_codex_info_dir', '_codex_load_or_create_conversation_state', '_codex_load_or_create_shared_conversation_state', '_codex_load_task', '_codex_log', '_codex_normalizar_model', '_codex_normalizar_reasoning_effort', '_codex_normalizar_sandbox', '_codex_normalizar_screen_context', '_codex_normalizar_service_tier', '_codex_normalizar_speed', '_codex_normalize_phone', '_codex_now', '_codex_persist_task', '_codex_prompt_pede_alteracao', '_codex_prompt_pede_desenvolvimento', '_codex_public_task', '_codex_readonly_cwd_for_session', '_codex_require_authenticated', '_codex_require_full_admin', '_codex_resolve_attachment_ids', '_codex_resolve_new_conversation_id', '_codex_resolve_readonly_references', '_codex_safe_filename', '_codex_save_conversation_state', '_codex_sdk_installed', '_codex_shared_continuity_for_session', '_codex_start_thread', '_codex_status_for_session', '_codex_task_channel', '_codex_task_conversation_keys', '_codex_task_stored_conversation_id', '_codex_task_summary', '_codex_universal_conversation_id', '_codex_update_conversation_memory', '_codex_whatsapp_query_policy']

__codex_exports__ = ['codex_status', 'codex_upload_attachments', 'codex_criar_tarefa', 'codex_listar_tarefas', 'codex_registrar_interacao_whatsapp_externa']
