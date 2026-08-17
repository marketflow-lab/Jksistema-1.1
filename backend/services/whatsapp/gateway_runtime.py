"""Extracted WhatsApp bridge component: gateway_runtime."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp.contracts import (
    _QuestionResearchPending,
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents
from backend.services.codex.console import security as console_security
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _approval_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))

def _approval_command(value: Any) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = APPROVAL_COMMAND_RE.match(normalized)
    if not match:
        return "", ""
    verb = match.group(1).upper()
    action = "approve" if verb in {"APROVAR", "CONFIRMAR"} else "reject"
    return action, match.group(2).upper()

def _normalize_worker_url(value: Any) -> str:
    return whatsapp_gateway.normalize_worker_url(value)

def _require_full(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    session = console_security.require_full_admin(request, authorization)
    try:
        raw = console_security.resolve_session(authorization)
    except Exception:
        raw = {}
    session["machine_id"] = str(raw.get("machine_id") or _host_machine_id())
    return session

def _binding_target(session: dict[str, Any], username: Any = None, client_id: Any = None) -> tuple[str, str]:
    session_username = str(session.get("username") or "").strip().lower()
    session_client_id = str(session.get("client_id") or "").strip()
    target_username = str(username or session_username).strip().lower()
    target_client_id = str(client_id or session_client_id).strip()
    if not target_username or not target_client_id:
        raise HTTPException(status_code=400, detail="Selecione um usuario valido para o pareamento.")
    if target_client_id != session_client_id:
        raise HTTPException(status_code=403, detail="Nao e permitido administrar telefones de outro cliente.")
    if target_username != session_username:
        try:
            admin_usuarios_common._carregar_permissoes_usuario(target_username, target_client_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="Usuario selecionado nao foi encontrado.") from exc
    return target_username, target_client_id

def _gateway_headers(config: dict[str, Any]) -> dict[str, str]:
    return whatsapp_gateway.gateway_headers(config)

def _gateway_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    stream: bool = False,
) -> requests.Response:
    return whatsapp_gateway.gateway_request(
        config,
        method,
        path,
        payload=payload,
        timeout=timeout,
        stream=stream,
    )

def _gateway_json(config: dict[str, Any], method: str, path: str, payload: Optional[dict[str, Any]] = None, timeout: int = 15) -> dict[str, Any]:
    return whatsapp_gateway.gateway_json(config, method, path, payload=payload, timeout=timeout)

def _worker_health(config: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    try:
        result = _gateway_json(config, "GET", "/bridge/status", timeout=12)
        result["latency_ms"] = int((time.time() - started) * 1000)
        try:
            actual_protocol = int(result.get("gateway_protocol_version") or 0)
        except (TypeError, ValueError):
            actual_protocol = 0
        result["expected_gateway_protocol_version"] = WHATSAPP_GATEWAY_PROTOCOL_VERSION
        result["protocol_compatible"] = actual_protocol == WHATSAPP_GATEWAY_PROTOCOL_VERSION
        if not result["protocol_compatible"]:
            result.update(
                {
                    "success": False,
                    "worker": False,
                    "error": (
                        "gateway_protocol_incompatible: "
                        f"expected={WHATSAPP_GATEWAY_PROTOCOL_VERSION}, actual={actual_protocol}"
                    ),
                }
            )
            RUNTIME_STATE["last_error"] = str(result["error"])
            return result
        RUNTIME_STATE["last_worker_ok_at"] = _now()
        RUNTIME_STATE["last_error"] = ""
        return result
    except Exception as exc:
        return {"success": False, "worker": False, "error": str(exc)[:800]}


_COMPONENT_FUNCTIONS = frozenset((
    '_approval_code',
    '_approval_command',
    '_normalize_worker_url',
    '_require_full',
    '_binding_target',
    '_gateway_headers',
    '_gateway_request',
    '_gateway_json',
    '_worker_health'
))
_IMPLEMENTATIONS = {
    '_approval_code': _approval_code,
    '_approval_command': _approval_command,
    '_normalize_worker_url': _normalize_worker_url,
    '_require_full': _require_full,
    '_binding_target': _binding_target,
    '_gateway_headers': _gateway_headers,
    '_gateway_request': _gateway_request,
    '_gateway_json': _gateway_json,
    '_worker_health': _worker_health
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
