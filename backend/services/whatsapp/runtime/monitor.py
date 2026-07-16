"""Extracted WhatsApp bridge component: monitor."""

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


def _dual_waiting_tick_due(config: dict[str, Any], state: dict[str, Any], pending: dict[str, Any], task: dict[str, Any]) -> bool:
    if str(task.get("status") or "") not in {"queued", "running"}:
        return False
    settings = _whatsapp_dual_agent_settings(config)
    notice_count = int(pending.get("wait_notice_count") or 0)
    interval = int(
        settings.get("wait_message_after_seconds")
        if notice_count == 0
        else settings.get("wait_message_repeat_seconds")
        if notice_count == 1
        else settings.get("wait_message_steady_seconds")
    )
    reference = float(pending.get("last_wait_notice_at_epoch") or pending.get("created_at_epoch") or time.time())
    return time.time() - reference >= interval

def _expire_dual_pending_if_due(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    changed = _dual_migrate_pending_v7(pending)
    if changed:
        _save_pending(state, message_id, pending)
    if str(pending.get("job_state") or "") == "partial":
        return _terminate_pending_partial(
            config,
            state,
            message_id,
            pending,
            reason=str(pending.get("terminal_reason") or "resultado_parcial"),
        )
    deadline = float(pending.get("deadline_at_epoch") or 0)
    if not deadline or time.time() < deadline:
        return False
    for task_id in _pending_task_ids(pending):
        try:
            task = codex_console._codex_load_task(task_id) or {}
            if str(task.get("status") or "") not in {"queued", "running", "cancel_requested"}:
                continue
            codex_console._codex_interrupt_active_turn(task_id)
            codex_console._codex_update_task(
                task_id,
                status="canceled",
                cancel_source="whatsapp_deadline",
                canceled_at=_now(),
                handoff_status="partial_terminal",
                delivery_state="deadline_exceeded",
            )
        except Exception:
            continue
    pending["deadline_exceeded_at"] = _now()
    return _terminate_pending_partial(
        config,
        state,
        message_id,
        pending,
        reason="O tempo máximo desta consulta foi atingido.",
    )

def _retry_dual_pending_interrupts(pending: dict[str, Any]) -> int:
    interrupted = 0
    for task_id in _pending_task_ids(pending):
        task = codex_console._codex_load_task(task_id) or {}
        if str(task.get("status") or "") != "cancel_requested":
            continue
        if codex_console._codex_interrupt_active_turn(task_id):
            interrupted += 1
            codex_console._codex_update_task(
                task_id,
                interrupt_requested=True,
                interrupt_requested_at=_now(),
            )
    return interrupted

def _monitor_pending(config: dict[str, Any], state: dict[str, Any]) -> None:
    with BRIDGE_STATE_LOCK:
        pending = dict(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, item in list(pending.items())[:100]:
        if not isinstance(item, dict):
            continue
        try:
            kind = str(item.get("kind") or "")
            if kind == "dual_function_manager":
                manager_state = str(item.get("manager_state") or "queued")
                if _expire_dual_pending_if_due(config, state, str(message_id), item):
                    continue
                if manager_state == "partial" or str(item.get("job_state") or "") == "partial":
                    _terminate_pending_partial(
                        config,
                        state,
                        str(message_id),
                        item,
                        reason=str(item.get("terminal_reason") or item.get("manager_retry_reason") or "resultado_parcial"),
                    )
                    continue
                due = float(item.get("manager_next_retry_at_epoch") or 0) <= time.time()
                if manager_state == "queued" or (manager_state == "waiting_retry" and due):
                    item.update({"manager_state": "queued", "job_state": "manager_queued"})
                    _save_pending(state, str(message_id), item)
                    _submit_function_manager_job(config, state, str(message_id))
                if manager_state in {"running", "queued", "partial", "waiting_retry"}:
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": "running" if manager_state == "running" else "queued",
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                continue
            if str(item.get("kind") or "") in {"dual_worker", "dual_job_group"}:
                if _expire_dual_pending_if_due(config, state, str(message_id), item):
                    continue
                _dual_retry_pending_due(config, state, str(message_id), item)
                _retry_dual_pending_interrupts(item)
                _maybe_send_dual_auth_notice(config, state, str(message_id), item)
            if _whatsapp_ai_settings(config).get("provider") == "codex":
                _start_progress_pulse(config, str(message_id), str(item.get("task_id") or ""))
            kind = str(item.get("kind") or "")
            if kind not in {"dual_worker", "dual_job_group"}:
                _complete_pending(config, state, str(message_id), item)
                continue
            if kind == "dual_job_group":
                tasks = _pending_codex_tasks(item)
                if not tasks:
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": "queued",
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                    continue
                statuses = {str(task.get("status") or "") for task in tasks}
                terminal = {"completed", "partial", "failed", "canceled"}
                if statuses & terminal:
                    signature = hashlib.sha256(
                        "|".join(
                            sorted(f"{task.get('task_id')}:{task.get('status')}" for task in tasks)
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    _enqueue_phone_event(
                        config,
                        state,
                        kind="worker_result",
                        event_id=f"{item.get('job_group_id')}:{signature}",
                        pending=item,
                        message_id=str(message_id),
                    )
                else:
                    synthetic_status = "running" if "running" in statuses else "queued"
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": synthetic_status,
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                continue
            task_id = str(item.get("task_id") or "")
            task = codex_console._codex_load_task(task_id) if task_id else None
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "")
            if status in {"completed", "partial", "failed", "canceled"}:
                _enqueue_phone_event(
                    config,
                    state,
                    kind="worker_result",
                    event_id=f"{task_id}:{status}",
                    pending=item,
                    message_id=str(message_id),
                )
            elif _dual_waiting_tick_due(config, state, item, task):
                _enqueue_phone_event(
                    config,
                    state,
                    kind="waiting_tick",
                    event_id=task_id,
                    pending=item,
                    message_id=str(message_id),
                )
        except Exception as exc:
            RUNTIME_STATE["last_error"] = str(exc)[:1000]

def _forward_task_transitions(config: dict[str, Any], state: dict[str, Any]) -> None:
    """Atualiza o espelho de estados sem atravessar tarefas entre canais.

    Respostas de tarefas WhatsApp sao entregues por ``_complete_pending`` ao
    ``message_id``/telefone que as originou. Tarefas do aplicativo pertencem
    exclusivamente ao sidebar e nunca podem virar notificacao proativa.
    """
    statuses = state.get("task_statuses") if isinstance(state.get("task_statuses"), dict) else {}
    try:
        paths = sorted(Path(codex_console._codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]
    except Exception:
        paths = []
    for path in paths:
        task = _json_read(path, {})
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(config.get("client_id") or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != str(config.get("username") or "").strip().lower():
            continue
        task_id = str(task.get("task_id") or path.stem)
        status = str(task.get("status") or "")
        statuses[task_id] = status
    state["task_statuses"] = dict(list(statuses.items())[-1000:])
    _save_state(state)


_COMPONENT_FUNCTIONS = frozenset((
    '_dual_waiting_tick_due',
    '_expire_dual_pending_if_due',
    '_retry_dual_pending_interrupts',
    '_monitor_pending',
    '_forward_task_transitions'
))
_IMPLEMENTATIONS = {
    '_dual_waiting_tick_due': _dual_waiting_tick_due,
    '_expire_dual_pending_if_due': _expire_dual_pending_if_due,
    '_retry_dual_pending_interrupts': _retry_dual_pending_interrupts,
    '_monitor_pending': _monitor_pending,
    '_forward_task_transitions': _forward_task_transitions
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
