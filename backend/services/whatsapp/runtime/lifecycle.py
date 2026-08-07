"""Extracted WhatsApp bridge component: lifecycle."""

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
from backend.services.whatsapp import audio_processing as whatsapp_audio_processing
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
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents, whatsapp_report_files, whatsapp_report_visuals, whatsapp_voice
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


def _run_audio_cleanup_janitor(*, force: bool = False) -> dict[str, int]:
    now_epoch = time.time()
    last_run = float(RUNTIME_STATE.get("audio_cleanup_janitor_at_epoch") or 0)
    if not force and now_epoch - last_run < 3600:
        previous = RUNTIME_STATE.get("audio_cleanup_janitor_result")
        return dict(previous) if isinstance(previous, dict) else {}
    try:
        result = whatsapp_audio_processing.cleanup_stale_audio_files(
            Path(_base_dir()),
            older_than_seconds=15 * 60,
            now_epoch=now_epoch,
        )
        sanitized = {
            "scanned": max(0, int(result.get("scanned") or 0)),
            "removed": max(0, int(result.get("removed") or 0)),
            "queued": max(0, int(result.get("queued") or 0)),
            "failed": max(0, int(result.get("failed") or 0)),
        }
        RUNTIME_STATE["audio_cleanup_janitor_result"] = sanitized
        RUNTIME_STATE["audio_cleanup_janitor_error"] = ""
        return sanitized
    except Exception:
        RUNTIME_STATE["audio_cleanup_janitor_error"] = "audio_cleanup_failed"
        return {"scanned": 0, "removed": 0, "queued": 0, "failed": 1}
    finally:
        RUNTIME_STATE["audio_cleanup_janitor_at_epoch"] = now_epoch


def _activate_completed_pairing(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if config.get("enabled"):
        return config, "enabled"
    if not config.get("pairing_pending"):
        return config, "disabled"
    if int(config.get("pairing_expires_at") or 0) < int(time.time()):
        config.update({"pairing_pending": False, "pairing_expires_at": 0})
        return _save_config(config), "pairing_expired"
    worker = _worker_health(config)
    machine_bindings = [
        item
        for item in (worker.get("bindings") or [])
        if isinstance(item, dict)
        and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
    ]
    if not machine_bindings:
        return config, "pairing_waiting"
    prerequisites_ready = bool(
        worker.get("success")
        and (worker.get("zero_cost") or {}).get("policy_valid")
        and (worker.get("meta") or {}).get("configured")
        and _whisper_status().get("ready")
        and console_telemetry.status_payload().get("ready")
    )
    if not prerequisites_ready:
        return config, "pairing_waiting_health"
    config.update({"enabled": True, "pairing_pending": False, "pairing_expires_at": 0})
    return _save_config(config), "pairing_activated"

def whatsapp_bridge_poll_once() -> dict[str, Any]:
    config = _load_config()
    _run_audio_cleanup_janitor()
    try:
        whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    except Exception as exc:
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    if not config.get("enabled"):
        config, activation_status = _activate_completed_pairing(config)
        if not config.get("enabled"):
            return {"success": True, "status": activation_status, "claimed": 0}
    state = _load_state()
    if time.time() - float(state.get("report_chart_cleanup_at") or 0) >= 3600:
        state["report_chart_cleanup_removed"] = whatsapp_report_visuals.cleanup_stale_chart_files(_info_dir())
        state["report_file_cleanup_removed"] = whatsapp_report_files.cleanup_stale_files(_info_dir())
        state["report_chart_cleanup_at"] = time.time()
        _save_state(state)
    lease_renewal_now = time.time()
    lease_renewal_due = (
        lease_renewal_now - float(state.get("gateway_lease_renewed_at_epoch") or 0)
        >= 4 * 60
    )
    pending_messages = (
        state.get("pending_messages")
        if isinstance(state.get("pending_messages"), dict)
        else {}
    )
    active_message_ids = (
        [
            str(message_id)
            for message_id, pending in list(pending_messages.items())[:100]
            if str(message_id or "").strip() and isinstance(pending, dict)
        ]
        if lease_renewal_due
        else []
    )
    heartbeat = _gateway_json(
        config,
        "POST",
        "/bridge/heartbeat",
        {
            "machine_id": config.get("machine_id"),
            "client_id": config.get("client_id"),
            "username": config.get("username"),
            "app_version": str(config.get("app_version") or "bridge-v9"),
            "active_message_ids": active_message_ids,
        },
        timeout=12,
    )
    if active_message_ids and heartbeat.get("success") is True:
        state["gateway_lease_renewed_at_epoch"] = lease_renewal_now
        state["gateway_lease_renewed_count"] = int(heartbeat.get("renewed_leases") or 0)
    state["gateway_heartbeat_at"] = _now()
    state["gateway_heartbeat_status"] = str(heartbeat.get("status") or "")
    _save_state(state)
    settings = _whatsapp_dual_agent_settings(config)
    _configure_phone_dispatcher(settings["conversation_worker_count"])
    console_queueing.configure_dual_limit(
        settings["max_active_task_agents_global"],
        settings["max_active_task_agents_per_conversation"],
    )
    capacity = _phone_dispatch_capacity()
    if capacity <= 0:
        _forward_task_transitions(config, state)
        _forward_question_approvals(config, state)
        RUNTIME_STATE["last_processing_at"] = _now()
        return {"success": True, "status": "local_queue_full", "claimed": 0, "processed": 0}
    claim_limit = min(CLAIM_LIMIT, capacity)
    response = _gateway_json(
        config,
        "POST",
        "/bridge/claim",
        {"machine_id": config.get("machine_id"), "limit": claim_limit},
        timeout=20,
    )
    messages = response.get("messages") if isinstance(response.get("messages"), list) else []
    if messages:
        _flush_pending_weekly_visuals(config, state)
    processed = 0
    for raw in messages[:claim_limit]:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("message_id") or "")
        if _enqueue_phone_event(
            config,
            state,
            kind="inbound",
            event_id=message_id,
            message=raw,
            message_id=message_id,
        ):
            processed += 1
        else:
            try:
                _post_message_result(config, message_id, {"status": "retry", "error": "local_queue_full"})
            except Exception:
                pass
    _monitor_pending(config, state)
    _forward_task_transitions(config, state)
    _forward_question_approvals(config, state)
    _start_operational_alert_scan(config)
    _start_phone_notification_report_scan(config)
    RUNTIME_STATE["last_processing_at"] = _now()
    return {"success": True, "status": "processed", "claimed": len(messages), "processed": processed}

def _bridge_loop() -> None:
    RUNTIME_STATE["running"] = True
    while not BRIDGE_STOP_EVENT.is_set():
        try:
            result = whatsapp_bridge_poll_once()
            heartbeat_state = _load_state()
            heartbeat_state["bridge_heartbeat_at"] = _now()
            heartbeat_state.pop("bridge_last_error", None)
            heartbeat_state["last_poll_summary"] = {
                "status": str(result.get("status") or ""),
                "claimed": int(result.get("claimed") or 0),
                "processed": int(result.get("processed") or 0),
            }
            _save_state(heartbeat_state)
        except Exception as exc:
            RUNTIME_STATE["last_error"] = str(exc)[:1000]
            heartbeat_state = _load_state()
            heartbeat_state["bridge_heartbeat_at"] = _now()
            heartbeat_state["bridge_last_error"] = str(exc)[:1000]
            _save_state(heartbeat_state)
        BRIDGE_STOP_EVENT.wait(POLL_SECONDS)
    RUNTIME_STATE["running"] = False

def whatsapp_bridge_iniciar_background() -> None:
    global BRIDGE_THREAD
    if BRIDGE_THREAD and BRIDGE_THREAD.is_alive():
        return
    BRIDGE_STOP_EVENT.clear()
    whatsapp_audio_processing.start_audio_cleanup_worker()
    _run_audio_cleanup_janitor(force=True)
    # Persiste a configuracao atual e materializa somente os runtimes Codex ativos.
    config = _save_config(_load_config())
    settings = _whatsapp_dual_agent_settings(config)
    recovered_retries = _recover_dual_pending_after_restart(_load_state())
    RUNTIME_STATE["dual_agent_retries_recovered"] = recovered_retries
    _configure_phone_dispatcher(settings["conversation_worker_count"])
    console_queueing.configure_dual_limit(
        settings["max_active_task_agents_global"],
        settings["max_active_task_agents_per_conversation"],
    )
    BRIDGE_THREAD = threading.Thread(target=_bridge_loop, name="jk-whatsapp-bridge", daemon=True)
    BRIDGE_THREAD.start()
    if config.get("enabled") and _whatsapp_dual_agent_settings(config).get("agent_architecture") == "dual_codex":
        def warm_dual_runtime() -> None:
            try:
                settings = _whatsapp_dual_agent_settings(config)
                codex_whatsapp_agents.CONVERSATION_RUNTIME.warm(
                    settings["conversation_agent_model"],
                    settings["task_agent_model"],
                    pool_size=settings["conversation_runtime_pool_size"],
                )
                codex_whatsapp_agents.DATA_SELECTION_RUNTIME.warm(
                    settings["conversation_agent_model"],
                    settings["task_agent_model"],
                    pool_size=settings["data_selection_runtime_pool_size"],
                )
                RUNTIME_STATE["dual_agent_last_error"] = ""
            except Exception as exc:
                RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]

        threading.Thread(target=warm_dual_runtime, name="jk-whatsapp-codex-warm", daemon=True).start()

def whatsapp_bridge_parar_background() -> None:
    global PHONE_DISPATCH_ACCEPTING, PHONE_DISPATCH_EXECUTOR, PHONE_DISPATCH_EXECUTOR_WORKERS
    global FUNCTION_MANAGER_EXECUTOR, FUNCTION_MANAGER_EXECUTOR_WORKERS
    BRIDGE_STOP_EVENT.set()
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_ACCEPTING = False
        futures = list(PHONE_DISPATCH_FUTURES)
    deadline = time.monotonic() + 10.0
    for future in futures:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            future.result(timeout=remaining)
        except (concurrent.futures.TimeoutError, Exception):
            pass
    queued_events: list[dict[str, Any]] = []
    with PHONE_DISPATCH_LOCK:
        for queue in PHONE_DISPATCH_QUEUES.values():
            queued_events.extend(item[2] for item in queue)
        PHONE_DISPATCH_QUEUES.clear()
        executor = PHONE_DISPATCH_EXECUTOR
        old_executors = list(PHONE_DISPATCH_OLD_EXECUTORS)
        PHONE_DISPATCH_OLD_EXECUTORS.clear()
        PHONE_DISPATCH_EXECUTOR = None
        PHONE_DISPATCH_EXECUTOR_WORKERS = 0
    for event in queued_events:
        try:
            if str(event.get("kind") or "") == "inbound":
                _post_message_result(
                    event.get("config") if isinstance(event.get("config"), dict) else _load_config(),
                    str(event.get("message_id") or ""),
                    {"status": "retry", "error": "backend_shutdown"},
                )
        except Exception:
            pass
        finally:
            _finish_phone_event(event)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=False)
    for old in old_executors:
        old.shutdown(wait=False, cancel_futures=False)
    with FUNCTION_MANAGER_LOCK:
        manager_executor = FUNCTION_MANAGER_EXECUTOR
        FUNCTION_MANAGER_EXECUTOR = None
        FUNCTION_MANAGER_EXECUTOR_WORKERS = 0
        FUNCTION_MANAGER_FUTURES.clear()
    if manager_executor is not None:
        manager_executor.shutdown(wait=False, cancel_futures=False)
    try:
        _save_state(_load_state())
    finally:
        codex_whatsapp_agents.CONVERSATION_RUNTIME.close()
        codex_whatsapp_agents.DATA_SELECTION_RUNTIME.close()
    RUNTIME_STATE["running"] = False


_COMPONENT_FUNCTIONS = frozenset((
    '_run_audio_cleanup_janitor',
    '_activate_completed_pairing',
    'whatsapp_bridge_poll_once',
    '_bridge_loop',
    'whatsapp_bridge_iniciar_background',
    'whatsapp_bridge_parar_background'
))
_IMPLEMENTATIONS = {
    '_run_audio_cleanup_janitor': _run_audio_cleanup_janitor,
    '_activate_completed_pairing': _activate_completed_pairing,
    'whatsapp_bridge_poll_once': whatsapp_bridge_poll_once,
    '_bridge_loop': _bridge_loop,
    'whatsapp_bridge_iniciar_background': whatsapp_bridge_iniciar_background,
    'whatsapp_bridge_parar_background': whatsapp_bridge_parar_background
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
