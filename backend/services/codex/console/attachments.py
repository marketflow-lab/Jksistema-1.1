"""Codex console attachments component."""

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


def _codex_safe_id(value: str, fallback: str = "default") -> str:
    safe_id = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe_id or fallback


def _codex_task_belongs_to_session(task: Any, sessao: dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    task_client = str(task.get("client_id") or "").strip()
    task_owner = str(task.get("created_by") or "").strip().lower()
    session_client = str(sessao.get("client_id") or "").strip()
    session_owner = str(sessao.get("username") or "").strip().lower()
    return bool(
        task_client
        and task_owner
        and session_client
        and session_owner
        and task_client == session_client
        and task_owner == session_owner
    )


def _codex_require_owned_task(task_id: str, sessao: dict[str, Any]) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not task or not _codex_task_belongs_to_session(task, sessao):
        # 404 evita revelar a existencia de tarefas de outro usuario/cliente.
        raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada.")
    return task


def _codex_safe_filename(value: str, fallback: str = "arquivo") -> str:
    name = os.path.basename(str(value or "").replace("\\", "/")).strip()
    if not name:
        name = fallback
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    if not name:
        name = fallback
    stem, ext = os.path.splitext(name[:180])
    stem = stem.strip(" .-_") or fallback
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:24]
    return (stem[:140] + ext)[:180]


def _codex_attachments_base_dir() -> Path:
    path = Path(_codex_base_dir()) / ".codex-remote-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_attachment_conversation_dir(client_id: str, username: str, conversation_id: str) -> Path:
    return (
        _codex_attachments_base_dir()
        / _codex_safe_id(client_id)
        / _codex_safe_id(username, "user")
        / _codex_safe_id(conversation_id)
    )


def _codex_attachment_dir(client_id: str, username: str, conversation_id: str) -> Path:
    date_part = time.strftime("%Y-%m-%d", time.localtime())
    path = _codex_attachment_conversation_dir(client_id, username, conversation_id) / date_part
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_cleanup_old_attachments() -> None:
    base = _codex_attachments_base_dir()
    cutoff = time.time() - CODEX_ATTACHMENT_TTL_SECONDS
    for path in list(base.rglob("*")):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            continue
    for path in sorted(base.rglob("*"), key=lambda item: len(str(item)), reverse=True):
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            continue


def _codex_attachment_public_payload(path: Path, original_name: str, mime_type: str, size: int) -> dict[str, Any]:
    return {
        "id": path.stem.split("_", 1)[0],
        "name": original_name,
        "mime_type": mime_type or "application/octet-stream",
        "size": int(size or 0),
    }





def _codex_conversation_id(thread_id: str = "", task_id: str = "") -> str:
    return _codex_safe_id(str(thread_id or "").strip() or str(task_id or "").strip() or uuid.uuid4().hex)


def _codex_normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) < 8 or len(digits) > 15:
        return ""
    return digits


def _codex_phone_identity(value: Any) -> str:
    """Normaliza a identidade sem separar a variante brasileira com nono digito."""
    digits = _codex_normalize_phone(value)
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        return digits[:4] + digits[5:]
    return digits


def _codex_canonical_conversation_id(
    client_id: str,
    username: str,
    *,
    channel: str = "app",
    phone: Any = "",
    lane: Any = "",
) -> str:
    channel_norm = "whatsapp" if str(channel or "").strip().lower() == "whatsapp" else "app"
    client_norm = str(client_id or "default").strip().lower() or "default"
    username_norm = str(username or "user").strip().lower() or "user"
    phone_norm = _codex_phone_identity(phone) if channel_norm == "whatsapp" else ""
    lane_norm = _codex_safe_id(str(lane or "").strip().lower(), "")[:40]
    if channel_norm == "whatsapp" and not phone_norm:
        raise HTTPException(
            status_code=409,
            detail="Nao foi possivel identificar com seguranca o telefone do WhatsApp.",
        )
    raw = (
        f"{client_norm}:{username_norm}:{channel_norm}:{phone_norm}:{lane_norm}"
        if lane_norm
        else f"{client_norm}:{username_norm}:{channel_norm}:{phone_norm}"
    )
    digest = _codex_hmac_identifier(raw, namespace="conversation")[-24:]
    prefix = "wa" if channel_norm == "whatsapp" else "app"
    return f"{prefix}_{lane_norm}_{digest}" if lane_norm else f"{prefix}_{digest}"





def _codex_universal_conversation_id(client_id: str, username: str) -> str:
    """Compatibilidade: a conversa universal antiga agora aponta para o canal app."""
    return _codex_canonical_conversation_id(client_id, username, channel="app")


def _codex_resolve_new_conversation_id(
    sessao: dict[str, Any],
    requested: Any = "",
    *,
    origin: str = "app",
    channel_metadata: Optional[dict[str, Any]] = None,
) -> str:
    # `requested` permanece no contrato para clientes antigos, mas nunca define
    # a identidade. Isso impede criar varias conversas trocando um ID no browser.
    del requested
    metadata = channel_metadata if isinstance(channel_metadata, dict) else {}
    channel = "whatsapp" if str(origin or "").strip().lower() == "whatsapp" else "app"
    return _codex_canonical_conversation_id(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
        channel=channel,
        phone=metadata.get("wa_id") or metadata.get("phone"),
        lane=metadata.get("agent_lane") or metadata.get("agent_role"),
    )

def _codex_shared_continuity_for_session(sessao: dict[str, Any]) -> dict[str, Any]:
    """WhatsApp and Sidebar always use independent conversation identities."""

    return {}



def _codex_shared_conversation_id(client_id: str, username: str) -> str:
    """Stable PII-free identity for the user's visible Black Jhon dialogue."""

    client_norm = str(client_id or "default").strip().lower() or "default"
    username_norm = str(username or "user").strip().lower() or "user"
    digest = _codex_hmac_identifier(
        f"{client_norm}:{username_norm}:black_jhon",
        namespace="shared_conversation",
    )[-24:]
    return f"bj_{digest}"
__codex_dependencies__ = ['CODEX_ATTACHMENT_TTL_SECONDS', '_codex_base_dir', '_codex_hmac_identifier', '_codex_load_task']

__codex_exports__ = ['_codex_safe_id', '_codex_task_belongs_to_session', '_codex_require_owned_task', '_codex_safe_filename', '_codex_attachments_base_dir', '_codex_attachment_conversation_dir', '_codex_attachment_dir', '_codex_cleanup_old_attachments', '_codex_attachment_public_payload', '_codex_conversation_id', '_codex_normalize_phone', '_codex_phone_identity', '_codex_canonical_conversation_id', '_codex_universal_conversation_id', '_codex_resolve_new_conversation_id', '_codex_shared_continuity_for_session', '_codex_shared_conversation_id']
