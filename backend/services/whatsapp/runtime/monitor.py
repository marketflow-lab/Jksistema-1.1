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
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents, whatsapp_report_files, whatsapp_report_visuals, whatsapp_voice
from backend.services.codex.console import paths as console_paths
from backend.services.codex.console import queueing as console_queueing
from backend.services.codex.console import tasks as console_tasks
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)
from backend.services.whatsapp.orchestration.retry_coordinator import _pending_is_terminal_or_finalizing

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS

_PENDING_PROGRESS_STALL_SECONDS = 300.0


def _timestamp_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def _pending_progress_epoch(pending: dict[str, Any]) -> float:
    epochs: list[float] = []
    for field in ("last_progress_at_epoch", "manager_progress_at_epoch", "created_at_epoch"):
        try:
            epochs.append(float(pending.get(field) or 0))
        except (TypeError, ValueError):
            continue
    for field in (
        "manager_started_at", "manager_planned_at", "last_retry_scheduled_at",
        "restart_recovered_at", "last_progress_at",
    ):
        epochs.append(_timestamp_epoch(pending.get(field)))
    holders = (
        [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
        if str(pending.get("kind") or "") == "dual_job_group"
        else [pending]
    )
    for holder in holders:
        for field in ("last_progress_at", "retry_started_at", "last_attempt_at"):
            epochs.append(_timestamp_epoch(holder.get(field)))
    for task in _pending_codex_tasks(pending):
        for field in ("last_progress_at", "updated_at", "started_at", "completed_at"):
            epochs.append(_timestamp_epoch(task.get(field)))
    return max(epochs or [0])


def _expire_stalled_pending(
    state: dict[str, Any], message_id: str, pending: dict[str, Any], *, now_epoch: Optional[float] = None,
) -> bool:
    if _pending_is_terminal_or_finalizing(pending):
        return False
    manager_state = str(pending.get("manager_state") or "").strip().lower()
    kind = str(pending.get("kind") or "")
    holders = (
        [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
        if kind == "dual_job_group"
        else [pending]
    )
    waiting_holders = [
        holder for holder in holders
        if str(holder.get("state") or "").strip().lower() in {"waiting_retry", "retry_starting"}
    ]
    running_holders = [
        holder for holder in holders
        if str(
            holder.get("state")
            or (pending.get("job_state") if kind == "dual_worker" else "")
            or ""
        ).strip().lower() in {"queued", "running", "cancel_requested"}
    ]
    watches_manager = kind == "dual_function_manager" and manager_state in {"running", "queued", "waiting_retry"}
    watches_workers = kind in {"dual_worker", "dual_job_group"} and bool(running_holders or waiting_holders)
    if not watches_manager and not watches_workers:
        return False
    current_epoch = time.time() if now_epoch is None else float(now_epoch)
    progress_epoch = _pending_progress_epoch(pending)
    if progress_epoch <= 0 or current_epoch - progress_epoch < _PENDING_PROGRESS_STALL_SECONDS:
        return False
    reason = "watchdog_sem_progresso"
    if watches_manager:
        pending.update(
            {
                "manager_state": "partial",
                "manager_next_retry_at_epoch": 0,
                "manager_retry_reason": reason,
                "manager_revision": max(0, int(pending.get("manager_revision") or 0)) + 1,
            }
        )
    for holder in waiting_holders:
        holder.update({"state": "partial", "next_retry_at_epoch": 0, "retry_reason": reason})
    for holder in running_holders:
        holder.update({"state": "partial", "next_retry_at_epoch": 0, "retry_reason": reason})
    pending.update(
        {
            "job_state": "partial",
            "terminal_reason": reason,
            "progress_watchdog_triggered_at": _now(),
            "progress_watchdog_triggered_at_epoch": current_epoch,
        }
    )
    _save_pending(state, message_id, pending)
    return True


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
    if _pending_is_terminal_or_finalizing(pending):
        if (
            str(pending.get("job_state") or "").strip().lower() == "partial"
            or str(pending.get("manager_state") or "").strip().lower() == "partial"
            or pending.get("terminal_delivery_started") is True
            or str(pending.get("delivery_state") or "").strip().lower() == "partial_finalizing"
        ):
            _terminate_pending_partial(
                config,
                state,
                message_id,
                pending,
                reason=str(pending.get("terminal_reason") or "resultado_parcial"),
            )
        return True
    _expire_stalled_pending(state, message_id, pending)
    if _pending_is_terminal_or_finalizing(pending):
        _terminate_pending_partial(
            config,
            state,
            message_id,
            pending,
            reason=str(pending.get("terminal_reason") or "resultado_parcial"),
        )
        return True
    changed = _dual_migrate_pending_v7(pending)
    if changed:
        _save_pending(state, message_id, pending)
    if _pending_is_terminal_or_finalizing(pending):
        if (
            str(pending.get("job_state") or "").strip().lower() == "partial"
            or str(pending.get("manager_state") or "").strip().lower() == "partial"
            or pending.get("terminal_delivery_started") is True
            or str(pending.get("delivery_state") or "").strip().lower() == "partial_finalizing"
        ):
            _terminate_pending_partial(
                config,
                state,
                message_id,
                pending,
                reason=str(pending.get("terminal_reason") or "resultado_parcial"),
            )
        return True
    # O Black Jhon nao encerra mais consultas por tempo total. O monitor segue
    # cuidando de progresso, retries e estados terminais reais.
    return False

def _retry_dual_pending_interrupts(pending: dict[str, Any]) -> int:
    interrupted = 0
    for task_id in _pending_task_ids(pending):
        task = console_tasks.load(task_id) or {}
        if str(task.get("status") or "") != "cancel_requested":
            continue
        if console_queueing.interrupt_active_turn(task_id):
            interrupted += 1
            console_tasks.update(
                task_id,
                interrupt_requested=True,
                interrupt_requested_at=_now(),
            )
    return interrupted


def _monitor_function_manager_item(
    config: dict[str, Any], state: dict[str, Any], message_id: str, item: dict[str, Any],
) -> bool:
    if str(item.get("kind") or "") != "dual_function_manager":
        return False
    manager_state = str(item.get("manager_state") or "queued")
    if _expire_dual_pending_if_due(config, state, message_id, item):
        return True
    if manager_state == "partial" or str(item.get("job_state") or "") == "partial":
        _terminate_pending_partial(
            config, state, message_id, item,
            reason=str(item.get("terminal_reason") or item.get("manager_retry_reason") or "resultado_parcial"),
        )
        return True
    due = float(item.get("manager_next_retry_at_epoch") or 0) <= time.time()
    if manager_state == "queued" or (manager_state == "waiting_retry" and due):
        item.update({"manager_state": "queued", "job_state": "manager_queued"})
        _save_pending(state, message_id, item)
        _submit_function_manager_job(config, state, message_id)
    if manager_state in {"running", "queued", "partial", "waiting_retry"}:
        synthetic = {
            "task_id": str(item.get("job_group_id") or message_id),
            "status": "running" if manager_state == "running" else "queued",
        }
        if _dual_waiting_tick_due(config, state, item, synthetic):
            _enqueue_phone_event(
                config, state, kind="waiting_tick", event_id=str(item.get("job_group_id") or message_id),
                pending=item, message_id=message_id,
            )
    return True


def _monitor_dual_group(
    config: dict[str, Any], state: dict[str, Any], message_id: str, item: dict[str, Any],
) -> None:
    tasks = _pending_codex_tasks(item)
    if not tasks:
        synthetic = {"task_id": str(item.get("job_group_id") or message_id), "status": "queued"}
        if _dual_waiting_tick_due(config, state, item, synthetic):
            _enqueue_phone_event(
                config, state, kind="waiting_tick", event_id=str(item.get("job_group_id") or message_id),
                pending=item, message_id=message_id,
            )
        return
    statuses = {str(task.get("status") or "") for task in tasks}
    if statuses & {"completed", "partial", "failed", "canceled"}:
        signature = hashlib.sha256(
            "|".join(sorted(f"{task.get('task_id')}:{task.get('status')}" for task in tasks)).encode("utf-8")
        ).hexdigest()[:16]
        _enqueue_phone_event(
            config, state, kind="worker_result", event_id=f"{item.get('job_group_id')}:{signature}",
            pending=item, message_id=message_id,
        )
        return
    synthetic = {
        "task_id": str(item.get("job_group_id") or message_id),
        "status": "running" if "running" in statuses else "queued",
    }
    if _dual_waiting_tick_due(config, state, item, synthetic):
        _enqueue_phone_event(
            config, state, kind="waiting_tick", event_id=str(item.get("job_group_id") or message_id),
            pending=item, message_id=message_id,
        )


def _monitor_dual_worker(
    config: dict[str, Any], state: dict[str, Any], message_id: str, item: dict[str, Any],
) -> None:
    task_id = str(item.get("task_id") or "")
    task = console_tasks.load(task_id) if task_id else None
    if not isinstance(task, dict):
        return
    status = str(task.get("status") or "")
    if status in {"completed", "partial", "failed", "canceled"}:
        _enqueue_phone_event(
            config, state, kind="worker_result", event_id=f"{task_id}:{status}",
            pending=item, message_id=message_id,
        )
    elif _dual_waiting_tick_due(config, state, item, task):
        _enqueue_phone_event(
            config, state, kind="waiting_tick", event_id=task_id, pending=item, message_id=message_id,
        )


def _monitor_pending_item(
    config: dict[str, Any], state: dict[str, Any], message_id: str, item: dict[str, Any],
) -> None:
    if _monitor_function_manager_item(config, state, message_id, item):
        return
    kind = str(item.get("kind") or "")
    if kind in {"dual_worker", "dual_job_group"}:
        if _expire_dual_pending_if_due(config, state, message_id, item):
            return
        _dual_retry_pending_due(config, state, message_id, item)
        _retry_dual_pending_interrupts(item)
        _maybe_send_dual_auth_notice(config, state, message_id, item)
    if _whatsapp_ai_settings(config).get("provider") == "codex":
        _start_progress_pulse(config, message_id, str(item.get("task_id") or ""))
    if kind not in {"dual_worker", "dual_job_group"}:
        _complete_pending(config, state, message_id, item)
    elif kind == "dual_job_group":
        _monitor_dual_group(config, state, message_id, item)
    else:
        _monitor_dual_worker(config, state, message_id, item)


def _monitor_pending(config: dict[str, Any], state: dict[str, Any]) -> None:
    with BRIDGE_STATE_LOCK:
        pending = dict(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, item in list(pending.items())[:100]:
        if not isinstance(item, dict):
            continue
        try:
            _monitor_pending_item(config, state, str(message_id), item)
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
        paths = sorted(Path(console_paths.info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]
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
