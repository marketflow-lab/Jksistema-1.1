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
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp import context_hub_telemetry as whatsapp_context_hub_telemetry
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
from backend.services.codex.console import queueing as console_queueing
from backend.services.codex.console import telemetry as console_telemetry
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


def _voice_public_status(config: dict[str, Any]) -> dict[str, Any]:
    del config
    return {
        "enabled": False,
        "ready": False,
        "policy_blocked": True,
        "removed": True,
    }


def _personal_number_status(config: dict[str, Any], worker: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    personal_numbers: list[dict[str, Any]] = []
    raw_bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    for item in raw_bindings:
        if not isinstance(item, dict):
            continue
        phone_number = re.sub(r"\D", "", str(item.get("phone_number") or ""))
        suffix = (phone_number or re.sub(r"\D", "", str(item.get("phone_suffix") or "")))[-4:]
        username = str(item.get("username") or "").strip().lower()
        client_id = str(item.get("client_id") or "").strip()
        try:
            full_access = admin_usuarios_common._carregar_permissoes_usuario(username, client_id).get("full") is True
        except Exception:
            full_access = False
        subject_id = str(item.get("subject_id") or "").strip()
        notification_settings = _phone_notification_settings(
            config, subject_id, client_id=client_id, username=username,
        )
        personal_numbers.append({
            "subject_id": subject_id,
            "phone_number": phone_number,
            "phone_masked": f"•••• {suffix}" if suffix else "número vinculado",
            "client_id": client_id,
            "username": username,
            "full_access": full_access,
            "access_label": "Perguntas e respostas do Mercado Livre",
            "last_inbound_at": int(item.get("last_inbound_at") or 0),
            "created_at": int(item.get("created_at") or 0),
            "this_machine": str(item.get("machine_id") or "") == str(config.get("machine_id") or ""),
            "notification_settings": notification_settings,
        })
    return personal_numbers, max(1, min(3, int(worker.get("binding_limit_per_user") or 3)))


def _conversation_context_status(state: dict[str, Any]) -> dict[str, Any]:
    records = [
        item for item in (state.get("dual_agent_conversations") or {}).values() if isinstance(item, dict)
    ] if isinstance(state.get("dual_agent_conversations"), dict) else []
    latest = max(records, key=lambda item: float(item.get("last_activity_at_epoch") or 0), default={})
    return {
        "conversations": len(records),
        "with_persisted_context": sum(1 for item in records if list(item.get("recent_turns") or [])),
        "persisted_turns": sum(len(list(item.get("recent_turns") or [])) for item in records),
        "max_turns_per_conversation": 16,
        "references_ttl_days": 30,
        "threads_reused": sum(1 for item in records if item.get("thread_reused") is True),
        "threads_restarted": sum(1 for item in records if str(item.get("thread_reset_reason") or "")),
        "last_thread": {
            "reused": latest.get("thread_reused") is True,
            "reset_reason": str(latest.get("thread_reset_reason") or "")[:120],
            "prompt_version": str(latest.get("prompt_version") or "")[:120],
            "prompt_hash": str(latest.get("prompt_hash") or "")[:128],
            "schema_version": str(latest.get("schema_version") or "")[:120],
            "context_chars": max(0, int(latest.get("context_chars") or 0)),
            "response_provider": str(latest.get("response_provider") or "")[:40],
            "codex_failure_count": max(0, int(latest.get("codex_failure_count") or 0)),
        },
    }


def _runtime_public_status(
    state: dict[str, Any],
    dispatcher: dict[str, Any],
    sol_capacity: dict[str, Any],
    dual_runtime: dict[str, Any],
    conversation_context: dict[str, Any],
) -> dict[str, Any]:
    return {
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
        "conversation_context": conversation_context,
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
    }


def _zero_cost_public_status(worker: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_valid_until": ZERO_COST_POLICY_VALID_UNTIL,
        "fail_closed": True,
        "free_window_minutes": 1410,
        "templates_outside_window": int((worker.get("counts") or {}).get("templates") or 0) > 0,
        "template_blockers": [
            {"name": str(item.get("name") or ""), "status": str(item.get("status") or "")}
            for item in list(worker.get("template_blockers") or []) if isinstance(item, dict)
        ],
    }


def _diagnostic_int(value: Any, *, maximum: int = 2_147_483_647) -> int:
    try:
        return max(0, min(maximum, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _diagnostic_code(value: Any, maximum: int) -> str:
    text = str(value or "").strip()
    return text[:maximum] if re.fullmatch(rf"[A-Za-z0-9_.:+-]{{1,{maximum}}}", text) else ""


def _public_context_hub_diagnostic(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    documents: list[dict[str, str]] = []
    for raw in list(source.get("documents") or [])[:12]:
        if not isinstance(raw, dict):
            continue
        document: dict[str, str] = {}
        doc_id = str(raw.get("doc_id") or "").strip()
        if re.fullmatch(r"jk:[A-Za-z0-9:_./-]{1,236}", doc_id) or re.fullmatch(r"sha256:[a-f0-9]{64}", doc_id):
            document["doc_id"] = doc_id
        for key in ("chunk_id_hash", "source_hash"):
            digest = str(raw.get(key) or "").strip().lower()
            if re.fullmatch(r"[a-f0-9]{32,128}", digest):
                document[key] = digest
        if document:
            documents.append(document)
    query_hash = str(source.get("query_hash") or "").strip().lower()
    return {
        "query_hash": query_hash if re.fullmatch(r"[a-f0-9]{64}", query_hash) else "",
        "intent": _diagnostic_code(source.get("intent"), 80),
        "generation_id": _diagnostic_code(source.get("generation_id"), 160),
        "source_version": _diagnostic_code(source.get("source_version"), 120),
        "result_count": _diagnostic_int(source.get("result_count"), maximum=12),
        "documents": documents,
        "latency_ms": _diagnostic_int(source.get("latency_ms")),
        "status": _diagnostic_code(source.get("status"), 80),
    }


def _data_selection_recent_for_client(state: dict[str, Any], client_id: Any) -> list[dict[str, Any]]:
    safe_client_id = whatsapp_context_hub_telemetry.safe_internal_client_id(client_id)
    if not safe_client_id:
        return []
    raw_history = state.get("data_selection_diagnostics")
    if not isinstance(raw_history, list):
        raw_history = state.get("function_manager_diagnostics")
    filtered = [
        item for item in list(raw_history or [])
        if isinstance(item, dict)
        and whatsapp_context_hub_telemetry.safe_internal_client_id(item.get("client_id")) == safe_client_id
    ][-10:]
    public: list[dict[str, Any]] = []
    for item in filtered:
        entry: dict[str, Any] = {
            "job_id": _diagnostic_code(item.get("job_id"), 100),
            "agent_role": _diagnostic_code(item.get("agent_role"), 80),
            "status": _diagnostic_code(item.get("status"), 80),
            "reason": _diagnostic_code(item.get("reason"), 120),
            "effective_model": _diagnostic_code(item.get("effective_model"), 100),
            "reasoning_effort": _diagnostic_code(item.get("reasoning_effort"), 20),
            "speed": _diagnostic_code(item.get("speed"), 20),
            "service_tier": _diagnostic_code(item.get("service_tier"), 40),
            "planning_duration_ms": _diagnostic_int(item.get("planning_duration_ms")),
            "tools_duration_ms": _diagnostic_int(item.get("tools_duration_ms")),
            "total_duration_ms": _diagnostic_int(item.get("total_duration_ms")),
            "tool_ids": [
                safe for safe in (_diagnostic_code(tool_id, 100) for tool_id in list(item.get("tool_ids") or [])[:6])
                if safe
            ],
            "validations": [
                {
                    "tool_id": _diagnostic_code(validation.get("tool_id"), 100),
                    "required": validation.get("required") is True,
                    "evidence_status": _diagnostic_code(validation.get("status"), 40),
                    "claim_scope": _diagnostic_code(validation.get("claim_scope"), 40),
                }
                for validation in list(item.get("validations") or [])[:12]
                if isinstance(validation, dict) and _diagnostic_code(validation.get("tool_id"), 100)
            ],
            "requires_sol": item.get("requires_sol") is True,
            "requires_web": item.get("requires_web") is True,
            "retry_count": _diagnostic_int(item.get("retry_count"), maximum=100),
            "recorded_at": _diagnostic_code(item.get("recorded_at"), 80),
        }
        if isinstance(item.get("context_hub"), dict):
            entry["context_hub"] = _public_context_hub_diagnostic(item["context_hub"])
        public.append(entry)
    return public


def _function_manager_recent_for_client(state: dict[str, Any], client_id: Any) -> list[dict[str, Any]]:
    """Compatibilidade read-only; o Function Manager semantico nao executa mais."""

    return _data_selection_recent_for_client(state, client_id)


def _public_status(config: dict[str, Any], worker: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    whisper = _whisper_status()
    audio_messages = _audio_messages_status()
    codex = console_telemetry.status_payload()
    ai_settings = _whatsapp_ai_settings(config)
    dual_settings = _whatsapp_dual_agent_settings(config)
    dual_runtime = codex_whatsapp_agents.CONVERSATION_RUNTIME.diagnostics()
    data_selection_runtime = codex_whatsapp_agents.DATA_SELECTION_RUNTIME.diagnostics()
    dispatcher = _phone_dispatch_diagnostics()
    sol_capacity = console_queueing.diagnostics()
    worker = worker if isinstance(worker, dict) else _worker_health(config) if config.get("worker_url") and config.get("bridge_token") else {"success": False, "worker": False, "error": "nao_configurado"}
    voice_status = _voice_public_status(config)
    state = _load_state()
    try:
        persistence = _bridge_store().diagnostics()
    except Exception as exc:
        persistence = {"backend": "fallback_json", "error": str(exc)[:500]}
    personal_numbers, binding_limit = _personal_number_status(config, worker)
    conversation_context_status = _conversation_context_status(state)
    return {
        "success": True,
        "channel_mode": whatsapp_settings.WHATSAPP_CHANNEL_MODE,
        "conversation_scope": whatsapp_settings.WHATSAPP_CONVERSATION_SCOPE,
        "allowed_workflows": list(whatsapp_settings.WHATSAPP_ALLOWED_WORKFLOWS),
        "config_version": int(config.get("version") or 10),
        "enabled": bool(config.get("enabled")),
        "configured": bool(config.get("worker_url") and config.get("bridge_token")),
        "worker_url": str(config.get("worker_url") or ""),
        "bridge_token_configured": bool(config.get("bridge_token")),
        "business_phone": str(config.get("business_phone") or ""),
        "ai_model": ai_settings["model"],
        "ai_provider": ai_settings["provider"],
        "fallback_model": ai_settings["fallback_model"],
        "fallback_provider": ai_settings["fallback_provider"],
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
        "black_jhon_prompt_contract": dual_runtime.get("prompt_contract") or {},
        "data_selection_runtime": data_selection_runtime,
        "data_selection_recent": _data_selection_recent_for_client(state, config.get("client_id")),
        # Campos antigos permanecem consultaveis, mas deixam explicito que nao
        # representam um segundo agente semantico em execucao.
        "function_manager_runtime": {
            **data_selection_runtime,
            "legacy_ignored": True,
            "replaced_by": "data_selection",
        },
        "function_manager_recent": _data_selection_recent_for_client(state, config.get("client_id")),
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
        "audio_messages": audio_messages,
        "joao": {"ready": bool(codex.get("ready")), "enabled": bool(codex.get("enabled")), "message": codex.get("message")},
        "voice": voice_status,
        "runtime": _runtime_public_status(
            state, dispatcher, sol_capacity, dual_runtime, conversation_context_status,
        ),
        "zero_cost": _zero_cost_public_status(worker),
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
