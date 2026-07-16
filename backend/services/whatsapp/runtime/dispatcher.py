"""Extracted WhatsApp bridge component: dispatcher."""

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


def _timing_epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text) / (1000.0 if float(text) > 10_000_000_000 else 1.0)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _record_latency(stage: str, seconds: float) -> None:
    if stage not in PHONE_LATENCY_SAMPLES or seconds < 0 or seconds > 24 * 3600:
        return
    with PHONE_DISPATCH_LOCK:
        PHONE_LATENCY_SAMPLES[stage].append(float(seconds))

def _record_message_timing(message_id: str, **values: Any) -> None:
    if not message_id:
        return
    try:
        state = _load_state()
        with BRIDGE_STATE_LOCK:
            timings = state.get("bridge_message_timings") if isinstance(state.get("bridge_message_timings"), dict) else {}
            item = timings.get(message_id) if isinstance(timings.get(message_id), dict) else {}
            item = dict(item)
            for key, value in values.items():
                if value not in (None, ""):
                    item[str(key)[:60]] = value
            timings[message_id] = item
            if len(timings) > 500:
                ordered = sorted(
                    timings.items(),
                    key=lambda pair: str((pair[1] or {}).get("claimed_at") or ""),
                )[-500:]
                timings = dict(ordered)
            state["bridge_message_timings"] = timings
            _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["timing_last_error"] = str(exc)[:500]

def _dispatch_phone_key(config: dict[str, Any], *, message: Optional[dict[str, Any]] = None, pending: Optional[dict[str, Any]] = None) -> str:
    raw = ""
    if isinstance(message, dict):
        raw = _message_phone(config, message) or str(message.get("subject_id") or "")
        identity = "|".join(
            (
                str(message.get("client_id") or config.get("client_id") or ""),
                str(message.get("username") or config.get("username") or "").lower(),
                raw,
            )
        )
    else:
        value = pending if isinstance(pending, dict) else {}
        raw = str(value.get("wa_id") or value.get("subject_id") or value.get("conversation_id") or "")
        identity = "|".join(
            (
                str(value.get("client_id") or config.get("client_id") or ""),
                str(value.get("username") or config.get("username") or "").lower(),
                raw,
            )
        )
    if not raw:
        identity += "|unknown"
    return hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()

def _configure_phone_dispatcher(worker_count: int) -> None:
    global PHONE_DISPATCH_EXECUTOR, PHONE_DISPATCH_EXECUTOR_WORKERS, PHONE_DISPATCH_ACCEPTING
    workers = _normalize_capacity(worker_count, WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT, 1, 8)
    previous: Optional[concurrent.futures.ThreadPoolExecutor] = None
    with PHONE_DISPATCH_LOCK:
        if not PHONE_DISPATCH_ACTIVE and not PHONE_DISPATCH_EVENT_IDS:
            BRIDGE_RUNTIME.phone_dispatch_inflight = int(PHONE_DISPATCH_INFLIGHT or 0)
        PHONE_DISPATCH_ACCEPTING = True
        if PHONE_DISPATCH_EXECUTOR is not None and PHONE_DISPATCH_EXECUTOR_WORKERS == workers:
            return
        replacement = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="jk-whatsapp-phone",
        )
        previous = PHONE_DISPATCH_EXECUTOR
        PHONE_DISPATCH_EXECUTOR = replacement
        PHONE_DISPATCH_EXECUTOR_WORKERS = workers
        if previous is not None:
            PHONE_DISPATCH_OLD_EXECUTORS.append(previous)
    if previous is not None:
        previous.shutdown(wait=False, cancel_futures=False)

def _phone_dispatch_capacity() -> int:
    with PHONE_DISPATCH_LOCK:
        queued = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        return max(0, WHATSAPP_LOCAL_QUEUE_CAPACITY - queued - BRIDGE_RUNTIME.phone_dispatch_inflight)

def _phone_dispatch_done(future: concurrent.futures.Future[Any]) -> None:
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_FUTURES.discard(future)

def _enqueue_phone_event(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    kind: str,
    event_id: str,
    message: Optional[dict[str, Any]] = None,
    pending: Optional[dict[str, Any]] = None,
    message_id: str = "",
) -> bool:
    priority = {"inbound": 0, "worker_result": 1, "waiting_tick": 2}.get(kind, 2)
    if kind == "inbound":
        inbound_text = str((message or {}).get("text_body") or "").strip()
        if QUESTION_APPROVAL_COMMAND_RE.match(inbound_text) or re.search(
            r"\b(corrija|corrigir|aprove|aprovar|negar|negue|gerar outra resposta)\b",
            _whatsapp_text_key(inbound_text),
        ):
            priority = -2
        elif whatsapp_report_files.report_requested(inbound_text):
            priority = 1
    key = _dispatch_phone_key(config, message=message, pending=pending)
    dedupe_id = f"{kind}:{event_id}"
    tick_id = f"tick:{str((pending or {}).get('task_id') or event_id)}" if kind == "waiting_tick" else ""
    with PHONE_DISPATCH_LOCK:
        if not PHONE_DISPATCH_ACCEPTING:
            return False
        if dedupe_id in PHONE_DISPATCH_EVENT_IDS or (tick_id and tick_id in PHONE_DISPATCH_TICK_IDS):
            return True
        queued = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        if queued + BRIDGE_RUNTIME.phone_dispatch_inflight >= WHATSAPP_LOCAL_QUEUE_CAPACITY:
            return False
        if PHONE_DISPATCH_EXECUTOR is None:
            _configure_phone_dispatcher(_whatsapp_dual_agent_settings(config)["conversation_worker_count"])
        event = {
            "kind": kind,
            "event_id": event_id,
            "dedupe_id": dedupe_id,
            "tick_id": tick_id,
            "config": dict(config),
            "state": state,
            "message": dict(message or {}),
            "pending": dict(pending or {}),
            "message_id": str(message_id or (message or {}).get("message_id") or ""),
            "claimed_epoch": time.time(),
        }
        queue = PHONE_DISPATCH_QUEUES.setdefault(key, [])
        heapq.heappush(queue, (priority, next(PHONE_DISPATCH_SEQUENCE), event))
        PHONE_DISPATCH_EVENT_IDS.add(dedupe_id)
        if tick_id:
            PHONE_DISPATCH_TICK_IDS.add(tick_id)
        if key not in PHONE_DISPATCH_ACTIVE:
            PHONE_DISPATCH_ACTIVE.add(key)
            assert PHONE_DISPATCH_EXECUTOR is not None
            future = PHONE_DISPATCH_EXECUTOR.submit(_drain_phone_events, key)
            PHONE_DISPATCH_FUTURES.add(future)
            future.add_done_callback(_phone_dispatch_done)
    return True

def _finish_phone_event(event: dict[str, Any]) -> None:
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_EVENT_IDS.discard(str(event.get("dedupe_id") or ""))
        tick_id = str(event.get("tick_id") or "")
        if tick_id:
            PHONE_DISPATCH_TICK_IDS.discard(tick_id)

def _process_phone_event(event: dict[str, Any]) -> None:
    kind = str(event.get("kind") or "")
    config = event.get("config") if isinstance(event.get("config"), dict) else {}
    state = event.get("state") if isinstance(event.get("state"), dict) else _load_state()
    message_id = str(event.get("message_id") or "")
    if kind == "inbound":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        started = time.time()
        received_epoch = _timing_epoch(message.get("received_at"))
        claimed_epoch = float(event.get("claimed_epoch") or started)
        _record_message_timing(
            message_id,
            received_at=message.get("received_at") or "",
            claimed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(claimed_epoch)),
            luna_started_at=_now(),
        )
        if received_epoch:
            _record_latency("gateway_to_luna", started - received_epoch)
        _record_latency("claimed_to_luna", started - claimed_epoch)
        try:
            _process_message(config, state, message)
            _record_message_timing(message_id, luna_completed_at=_now())
        except Exception as exc:
            detail = str(exc)[:1000]
            RUNTIME_STATE["last_error"] = detail
            _record_message_timing(message_id, luna_completed_at=_now(), error=detail)
            try:
                error_class, retryable = _dual_retry_classification(detail)
                status = "retry" if retryable and str(config.get("agent_architecture") or "").lower() == "dual_codex" else "failed"
                _post_message_result(
                    config,
                    message_id,
                    {
                        "status": status,
                        "error": detail,
                        "response": "" if status == "retry" else (
                            "Nao consegui concluir esta solicitacao com seguranca. "
                            f"Motivo: {error_class}. Nenhuma alteracao foi executada."
                        ),
                    },
                )
            except Exception:
                pass
        finally:
            _record_latency("luna_duration", time.time() - started)
        return

    with BRIDGE_STATE_LOCK:
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        current = pending_messages.get(message_id)
        current = dict(current) if isinstance(current, dict) else {}
    if not current:
        return
    try:
        _complete_pending(config, state, message_id, current)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = str(exc)[:1000]

def _drain_phone_events(phone_key: str) -> None:
    global PHONE_DISPATCH_INFLIGHT
    while True:
        with PHONE_DISPATCH_LOCK:
            queue = PHONE_DISPATCH_QUEUES.get(phone_key) or []
            if not queue:
                PHONE_DISPATCH_QUEUES.pop(phone_key, None)
                PHONE_DISPATCH_ACTIVE.discard(phone_key)
                return
            _, _, event = heapq.heappop(queue)
            BRIDGE_RUNTIME.phone_dispatch_inflight += 1
            PHONE_DISPATCH_INFLIGHT = BRIDGE_RUNTIME.phone_dispatch_inflight
        try:
            _process_phone_event(event)
        finally:
            _finish_phone_event(event)
            with PHONE_DISPATCH_LOCK:
                BRIDGE_RUNTIME.phone_dispatch_inflight = max(
                    0,
                    BRIDGE_RUNTIME.phone_dispatch_inflight - 1,
                )
                PHONE_DISPATCH_INFLIGHT = BRIDGE_RUNTIME.phone_dispatch_inflight


_COMPONENT_FUNCTIONS = frozenset((
    '_timing_epoch',
    '_record_latency',
    '_record_message_timing',
    '_dispatch_phone_key',
    '_configure_phone_dispatcher',
    '_phone_dispatch_capacity',
    '_phone_dispatch_done',
    '_enqueue_phone_event',
    '_finish_phone_event',
    '_process_phone_event',
    '_drain_phone_events'
))
_IMPLEMENTATIONS = {
    '_timing_epoch': _timing_epoch,
    '_record_latency': _record_latency,
    '_record_message_timing': _record_message_timing,
    '_dispatch_phone_key': _dispatch_phone_key,
    '_configure_phone_dispatcher': _configure_phone_dispatcher,
    '_phone_dispatch_capacity': _phone_dispatch_capacity,
    '_phone_dispatch_done': _phone_dispatch_done,
    '_enqueue_phone_event': _enqueue_phone_event,
    '_finish_phone_event': _finish_phone_event,
    '_process_phone_event': _process_phone_event,
    '_drain_phone_events': _drain_phone_events
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
