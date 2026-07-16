"""Extracted WhatsApp bridge component: api_status."""

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
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
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
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _latency_diagnostics() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with PHONE_DISPATCH_LOCK:
        snapshots = {key: list(values) for key, values in PHONE_LATENCY_SAMPLES.items()}
    for stage, values in snapshots.items():
        ordered = sorted(float(value) for value in values)
        if not ordered:
            result[stage] = {"samples": 0, "average_ms": 0, "p95_ms": 0}
            continue
        p95_index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95) + 0.999999) - 1))
        result[stage] = {
            "samples": len(ordered),
            "average_ms": round((sum(ordered) / len(ordered)) * 1000, 1),
            "p95_ms": round(ordered[p95_index] * 1000, 1),
        }
    return result

def _phone_dispatch_diagnostics() -> dict[str, Any]:
    with PHONE_DISPATCH_LOCK:
        waiting = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        futures = list(PHONE_DISPATCH_FUTURES)
        return {
            "queue_capacity": WHATSAPP_LOCAL_QUEUE_CAPACITY,
            "messages_waiting": waiting,
            "capacity_available": max(0, WHATSAPP_LOCAL_QUEUE_CAPACITY - waiting - PHONE_DISPATCH_INFLIGHT),
            "active_phones": len(PHONE_DISPATCH_ACTIVE),
            "worker_count": PHONE_DISPATCH_EXECUTOR_WORKERS,
            "workers_busy": sum(1 for future in futures if future.running()),
            "accepting": PHONE_DISPATCH_ACCEPTING,
            "coalesced_ticks": len(PHONE_DISPATCH_TICK_IDS),
            "latency": _latency_diagnostics(),
        }

def _public_status(config: dict[str, Any], worker: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    whisper = _whisper_status()
    codex = codex_console._codex_status_payload()
    ai_settings = _whatsapp_ai_settings(config)
    dual_settings = _whatsapp_dual_agent_settings(config)
    dual_runtime = codex_whatsapp_agents.CONVERSATION_RUNTIME.diagnostics()
    function_manager_runtime = codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.diagnostics()
    dispatcher = _phone_dispatch_diagnostics()
    sol_capacity = codex_console._codex_dual_sol_diagnostics()
    worker = worker if isinstance(worker, dict) else _worker_health(config) if config.get("worker_url") and config.get("bridge_token") else {"success": False, "worker": False, "error": "nao_configurado"}
    voice_local = whatsapp_voice.VOICE_RUNTIME.diagnostics()
    if config.get("worker_url") and config.get("bridge_token"):
        try:
            voice_gateway = _gateway_json(config, "GET", "/bridge/voice/status", timeout=12)
        except Exception as exc:
            voice_gateway = {"success": False, "configured": False, "error": str(exc)[:500]}
    else:
        voice_gateway = {"success": False, "configured": False, "error": "nao_configurado"}
    gateway_openai = voice_gateway.get("openai") if isinstance(voice_gateway.get("openai"), dict) else {}
    gateway_fingerprint = str(gateway_openai.get("key_fingerprint") or "")
    local_fingerprint = str(voice_local.get("api_key_fingerprint") or "")
    voice_gateway_public = {
        key: value
        for key, value in voice_gateway.items()
        if key not in {"calls", "heartbeats"}
    }
    if isinstance(voice_gateway_public.get("openai"), dict):
        voice_gateway_public["openai"] = {
            key: value
            for key, value in voice_gateway_public["openai"].items()
            if key != "key_fingerprint"
        }
    current_client_id = str(config.get("client_id") or "")
    current_username = str(config.get("username") or "").strip().lower()
    voice_recent_calls = [
        item
        for item in list(voice_gateway.get("calls") or [])
        if isinstance(item, dict)
        and str(item.get("client_id") or "") == current_client_id
        and str(item.get("username") or "").strip().lower() == current_username
    ][:20]
    state = _load_state()
    try:
        persistence = _bridge_store().diagnostics()
    except Exception as exc:
        persistence = {"backend": "fallback_json", "error": str(exc)[:500]}
    raw_bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    personal_numbers = []
    for item in raw_bindings:
        if not isinstance(item, dict):
            continue
        phone_number = re.sub(r"\D", "", str(item.get("phone_number") or ""))
        suffix = (phone_number or re.sub(r"\D", "", str(item.get("phone_suffix") or "")))[-4:]
        binding_username = str(item.get("username") or "").strip().lower()
        binding_client_id = str(item.get("client_id") or "").strip()
        try:
            binding_permissions = admin_usuarios_common._carregar_permissoes_usuario(binding_username, binding_client_id)
            binding_full_access = binding_permissions.get("full") is True
        except Exception:
            binding_full_access = False
        subject_id = str(item.get("subject_id") or "").strip()
        notification_settings = _phone_notification_settings(
            config,
            subject_id,
            client_id=binding_client_id,
            username=binding_username,
        )
        personal_numbers.append(
            {
                "subject_id": subject_id,
                "phone_number": phone_number,
                "phone_masked": f"•••• {suffix}" if suffix else "número vinculado",
                "client_id": binding_client_id,
                "username": binding_username,
                "full_access": binding_full_access,
                "access_label": "Acesso total com confirmação pelo WhatsApp" if binding_full_access else "Somente consultas autorizadas",
                "last_inbound_at": int(item.get("last_inbound_at") or 0),
                "created_at": int(item.get("created_at") or 0),
                "this_machine": str(item.get("machine_id") or "") == str(config.get("machine_id") or ""),
                "notification_settings": notification_settings,
            }
        )
    binding_limit = max(1, min(3, int(worker.get("binding_limit_per_user") or 3)))
    conversation_records = [
        item
        for item in (state.get("dual_agent_conversations") or {}).values()
        if isinstance(item, dict)
    ] if isinstance(state.get("dual_agent_conversations"), dict) else []
    conversation_context_status = {
        "conversations": len(conversation_records),
        "with_persisted_context": sum(1 for item in conversation_records if list(item.get("recent_turns") or [])),
        "persisted_turns": sum(len(list(item.get("recent_turns") or [])) for item in conversation_records),
        "max_turns_per_conversation": 16,
    }
    return {
        "success": True,
        "config_version": int(config.get("version") or 9),
        "enabled": bool(config.get("enabled")),
        "configured": bool(config.get("worker_url") and config.get("bridge_token")),
        "worker_url": str(config.get("worker_url") or ""),
        "bridge_token_configured": bool(config.get("bridge_token")),
        "business_phone": str(config.get("business_phone") or ""),
        "ai_model": ai_settings["model"],
        "ai_provider": ai_settings["provider"],
        "codex_reasoning_effort": ai_settings["codex_reasoning_effort"],
        "codex_reasoning_policy": ai_settings["codex_reasoning_policy"],
        "codex_reasoning_max": ai_settings["codex_reasoning_max"],
        "codex_reasoning_options": list(WHATSAPP_CODEX_REASONING_OPTIONS),
        "codex_reasoning_policies": list(WHATSAPP_CODEX_REASONING_POLICIES),
        "orchestration_mode": WHATSAPP_ORCHESTRATION_MODE_DEFAULT,
        "progress_interval_seconds": _normalize_progress_interval(config.get("progress_interval_seconds")),
        "progress_explain_wait": config.get("progress_explain_wait") is not False,
        "active_task_policy": "steer_or_queue",
        **dual_settings,
        "dual_agent_runtime": dual_runtime,
        "function_manager_runtime": function_manager_runtime,
        "function_manager_recent": [
            item
            for item in list(state.get("function_manager_diagnostics") or [])[-10:]
            if isinstance(item, dict)
        ],
        "conversation_context": conversation_context_status,
        "conversation_dispatcher": dispatcher,
        "task_agent_capacity": sol_capacity,
        "personal_phone_masked": personal_numbers[0]["phone_masked"] if personal_numbers else _mask_phone(config.get("personal_phone")),
        "personal_numbers": personal_numbers,
        "binding_limit": binding_limit,
        "binding_slots_remaining": max(0, binding_limit - sum(
            1
            for item in personal_numbers
            if item["client_id"] == str(config.get("client_id") or "")
            and item["username"] == str(config.get("username") or "").strip().lower()
        )),
        "paired": bool(personal_numbers),
        "current_user": {
            "client_id": str(config.get("client_id") or ""),
            "username": str(config.get("username") or "").strip().lower(),
        },
        "machine_id": str(config.get("machine_id") or ""),
        "client_id": str(config.get("client_id") or ""),
        "username": str(config.get("username") or ""),
        "worker": worker,
        "persistence": persistence,
        "whisper": whisper,
        "joao": {"ready": bool(codex.get("ready")), "enabled": bool(codex.get("enabled")), "message": codex.get("message")},
        "voice": {
            "enabled": config.get("voice_enabled") is True,
            "ready": bool(
                config.get("voice_enabled") is True
                and voice_local.get("ready")
                and voice_gateway.get("configured")
                and gateway_fingerprint == local_fingerprint
            ),
            "read_only": True,
            "transcript_retention": "transcript_only",
            "model": str(config.get("voice_model") or whatsapp_voice.VOICE_MODEL_DEFAULT),
            "transcription_model": str(config.get("voice_transcription_model") or whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT),
            "name": str(config.get("voice_name") or whatsapp_voice.VOICE_NAME_DEFAULT),
            "language": "pt-BR",
            "max_call_minutes": int(config.get("voice_max_call_minutes") or 30),
            "silence_timeout_seconds": int(config.get("voice_silence_timeout_seconds") or 90),
            "long_task_offer_seconds": int(config.get("voice_long_task_offer_seconds") or 90),
            "max_concurrent_calls": int(config.get("voice_max_concurrent_calls") or 3),
            "progress_interval_seconds": int(config.get("voice_progress_interval_seconds") or 8),
            "api_key_configured": bool(voice_local.get("api_key_configured")),
            "key_match": bool(
                gateway_fingerprint
                and gateway_fingerprint == local_fingerprint
            ),
            "local": {key: value for key, value in voice_local.items() if key != "api_key_fingerprint"},
            "gateway": voice_gateway_public,
            "recent_calls": voice_recent_calls,
        },
        "runtime": {
            "running": bool(RUNTIME_STATE.get("running")),
            "last_error": str(RUNTIME_STATE.get("last_error") or ""),
            "last_processing_at": str(RUNTIME_STATE.get("last_processing_at") or ""),
            "last_worker_ok_at": str(RUNTIME_STATE.get("last_worker_ok_at") or ""),
            "typing_active": len(TYPING_PULSES),
            "progress_active": len(PROGRESS_PULSES),
            "typing_refresh_seconds": TYPING_REFRESH_SECONDS,
            "typing_max_seconds": TYPING_MAX_SECONDS,
            "typing_last_sent_at": str(RUNTIME_STATE.get("typing_last_sent_at") or ""),
            "typing_last_error": str(RUNTIME_STATE.get("typing_last_error") or ""),
            "progress_last_sent_at": str(RUNTIME_STATE.get("progress_last_sent_at") or ""),
            "progress_last_error": str(RUNTIME_STATE.get("progress_last_error") or ""),
            "dual_agent_last_error": str(RUNTIME_STATE.get("dual_agent_last_error") or dual_runtime.get("last_error") or ""),
            "dual_agent_retries_recovered": int(RUNTIME_STATE.get("dual_agent_retries_recovered") or 0),
            "dual_agent_conversations": len(state.get("dual_agent_conversations") or {}) if isinstance(state.get("dual_agent_conversations"), dict) else 0,
            "conversation_context": conversation_context_status,
            "bridge_heartbeat_at": str(state.get("bridge_heartbeat_at") or ""),
            "bridge_last_error": str(state.get("bridge_last_error") or ""),
            "pending_local_tasks": len(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else 0,
            "pending_weekly_visuals": len(state.get("pending_weekly_visuals") or {}) if isinstance(state.get("pending_weekly_visuals"), dict) else 0,
            "last_weekly_visual_status": str(state.get("last_weekly_visual_status") or ""),
            "last_weekly_visual_sent_at": str(state.get("last_weekly_visual_sent_at") or ""),
            "poll_seconds": POLL_SECONDS,
            "claim_limit": CLAIM_LIMIT,
            "conversation_dispatcher": dispatcher,
            "task_agent_capacity": sol_capacity,
            "state_store_last_error": str(RUNTIME_STATE.get("state_store_last_error") or ""),
            "web_fallback_circuit": dict(WEB_FALLBACK_CIRCUIT),
        },
        "zero_cost": {
            "policy_valid_until": ZERO_COST_POLICY_VALID_UNTIL,
            "fail_closed": True,
            "free_window_minutes": 1410,
            "templates_outside_window": int((worker.get("counts") or {}).get("templates") or 0) > 0,
            "template_blockers": [
                {"name": str(item.get("name") or ""), "status": str(item.get("status") or "")}
                for item in list(worker.get("template_blockers") or [])
                if isinstance(item, dict)
            ],
        },
    }

def whatsapp_bridge_status(request: Request, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    identity = {
        "client_id": str(session.get("client_id") or ""),
        "username": str(session.get("username") or "").strip().lower(),
        "machine_id": str(session.get("machine_id") or config.get("machine_id") or _host_machine_id()),
    }
    if any(str(config.get(key) or "") != value for key, value in identity.items()):
        config.update(identity)
        _save_config(config)
    return _public_status(config)


_COMPONENT_FUNCTIONS = frozenset((
    '_latency_diagnostics',
    '_phone_dispatch_diagnostics',
    '_public_status',
    'whatsapp_bridge_status'
))
_IMPLEMENTATIONS = {
    '_latency_diagnostics': _latency_diagnostics,
    '_phone_dispatch_diagnostics': _phone_dispatch_diagnostics,
    '_public_status': _public_status,
    'whatsapp_bridge_status': whatsapp_bridge_status
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
