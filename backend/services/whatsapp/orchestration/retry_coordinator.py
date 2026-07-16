"""Extracted WhatsApp bridge component: retry_coordinator."""

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


def _reload_bound_session(config: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    username = str(message.get("username") or config.get("username") or "").strip().lower()
    client_id = str(message.get("client_id") or config.get("client_id") or "").strip()
    if not username or not client_id:
        raise RuntimeError("binding_identity_missing")
    permissions = admin_usuarios_common._carregar_permissoes_usuario(username, client_id)
    return {"username": username, "client_id": client_id, "permissions": permissions, "is_full": permissions.get("full") is True}

def _pending_task_for_message(state: dict[str, Any], message_id: str) -> Optional[dict[str, Any]]:
    pending = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    item = pending.get(message_id)
    return item if isinstance(item, dict) else None

def _active_pending_for_conversation(
    state: dict[str, Any],
    conversation_id: str,
    *,
    exclude_message_id: str = "",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for pending_message_id, pending in pending_messages.items():
        if str(pending_message_id or "") == str(exclude_message_id or "") or not isinstance(pending, dict):
            continue
        if str(pending.get("conversation_id") or "") != str(conversation_id or ""):
            continue
        tasks = _pending_codex_tasks(pending)
        active = [task for task in tasks if str(task.get("status") or "") in {"queued", "running", "cancel_requested"}]
        pending_state = str(pending.get("job_state") or "")
        manager_active = str(pending.get("kind") or "") == "dual_function_manager" and pending_state in {
            "manager_queued", "manager_running", "waiting_retry", "awaiting_input", "retry_starting",
        }
        if not active and pending_state not in {"waiting_retry", "awaiting_input", "retry_starting"} and not manager_active:
            continue
        if str(pending.get("kind") or "") in {"dual_job_group", "dual_function_manager"} or not active:
            task = {
                "task_id": str(pending.get("job_group_id") or pending_message_id),
                "status": (
                    "queued"
                    if pending_state in {"manager_queued", "waiting_retry", "retry_starting"}
                    else "running" if pending_state == "manager_running" else pending_state or "running"
                ),
                "child_tasks": active,
            }
        else:
            task = active[0]
        candidates.append((str(pending_message_id), pending, task))
    candidates.sort(key=lambda item: str(item[1].get("created_at") or ""), reverse=True)
    return candidates[0] if candidates else ("", {}, {})

def _pending_task_ids(pending: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in list(pending.get("subtasks") or []):
        if isinstance(item, dict):
            task_id = str(item.get("task_id") or "").strip()
            if task_id and task_id not in values:
                values.append(task_id)
    if str(pending.get("kind") or "") == "dual_job_group":
        # Em grupos, cada holder aponta para a tentativa Sol corrente. O campo
        # superior existe apenas por compatibilidade e pode conter uma tentativa
        # antiga depois de um retry.
        return values
    task_id = str(pending.get("task_id") or "").strip()
    if task_id and task_id not in values:
        values.append(task_id)
    return values

def _pending_codex_tasks(pending: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_id in _pending_task_ids(pending):
        task = codex_console._codex_load_task(task_id)
        if isinstance(task, dict):
            tasks.append(task)
    return tasks

def _update_pending_codex_tasks(pending: dict[str, Any], **changes: Any) -> None:
    for task_id in _pending_task_ids(pending):
        try:
            codex_console._codex_update_task(task_id, **changes)
        except Exception:
            continue

def _dual_retry_reason_text(task: dict[str, Any], result: dict[str, Any]) -> str:
    return whatsapp_retry_policy.retry_reason_text(task, result)

def _dual_retry_is_auth_error(reason: Any) -> bool:
    return whatsapp_retry_policy.retry_is_auth_error(reason)

def _dual_retry_classification(reason: Any) -> tuple[str, bool]:
    return whatsapp_retry_policy.retry_classification(reason)

def _dual_retry_delay_seconds(retry_count: int, reason: Any, key: Any = "") -> int:
    return whatsapp_retry_policy.retry_delay_seconds(
        retry_count,
        reason,
        key,
        delays=WHATSAPP_RETRY_DELAYS_SECONDS,
    )

def _local_web_fallback_context(query: str, client_id: str) -> dict[str, Any]:
    now_epoch = time.time()
    with WEB_FALLBACK_LOCK:
        if float(WEB_FALLBACK_CIRCUIT.get("opened_until_epoch") or 0) > now_epoch:
            return {
                "success": False,
                "data": [],
                "sources": [],
                "dados_suficientes": False,
                "coverage_complete": False,
                "error_class": "circuit_open",
                "retryable": True,
            }
    try:
        from backend.services import ia_web

        results = ia_web._ia_web_buscar_cached(
            str(query or "")[:2000],
            client_id=str(client_id or "default"),
            max_results=5,
            fast=True,
        )
        clean = [item for item in list(results or []) if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if not clean:
            raise RuntimeError("local_web_fallback_empty")
        with WEB_FALLBACK_LOCK:
            WEB_FALLBACK_CIRCUIT.update({"failures": 0, "opened_until_epoch": 0.0, "last_error": ""})
        return {
            "success": True,
            "data": clean,
            "sources": [str(item.get("url") or "")[:600] for item in clean],
            "dados_suficientes": True,
            # Resultados de busca sao pistas para o agente de tarefa, nao
            # prova conclusiva de cobertura integral.
            "coverage_complete": False,
            "error_class": "",
            "retryable": False,
        }
    except Exception as exc:
        with WEB_FALLBACK_LOCK:
            failures = int(WEB_FALLBACK_CIRCUIT.get("failures") or 0) + 1
            WEB_FALLBACK_CIRCUIT.update(
                {
                    "failures": failures,
                    "opened_until_epoch": time.time() + 60 if failures >= 3 else 0.0,
                    "last_error": str(exc)[:500],
                }
            )
        return {
            "success": False,
            "data": [],
            "sources": [],
            "dados_suficientes": False,
            "coverage_complete": False,
            "error_class": "transient_dependency",
            "retryable": True,
        }

def _dual_append_unique(target: list[str], values: Any, *, limit: int, item_limit: int) -> list[str]:
    return whatsapp_retry_policy.append_unique(target, values, limit=limit, item_limit=item_limit)

def _dual_preserve_worker_result(pending: dict[str, Any], result: dict[str, Any]) -> None:
    whatsapp_retry_policy.preserve_worker_result(pending, result)

def _dual_worker_disposition(task: dict[str, Any], result: dict[str, Any]) -> str:
    return whatsapp_retry_policy.worker_disposition(task, result)

def _dual_schedule_retry(
    pending: dict[str, Any],
    holder: dict[str, Any],
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    key: str,
) -> None:
    _dual_preserve_worker_result(pending, result)
    task_id = str(task.get("task_id") or holder.get("task_id") or "").strip()
    history = [str(item or "").strip() for item in list(holder.get("attempt_task_ids") or []) if str(item or "").strip()]
    if task_id and task_id not in history:
        history.append(task_id)
    disposition = _dual_worker_disposition(task, result)
    holder["attempt_task_ids"] = history[-50:]
    holder["last_attempt_task_id"] = task_id
    holder["last_attempt_at"] = str(task.get("completed_at") or _now())
    holder["last_progress_at"] = str(task.get("last_progress_at") or task.get("completed_at") or _now())
    if disposition == "awaiting_input":
        holder["state"] = "awaiting_input"
        holder["next_retry_at_epoch"] = 0
        holder["retry_reason"] = "awaiting_user_input"
        pending["job_state"] = "awaiting_input"
        pending["pending_questions"] = _dual_append_unique(
            list(pending.get("pending_questions") or []),
            result.get("questions"),
            limit=10,
            item_limit=1000,
        )
        return
    if disposition == "canceled":
        holder["state"] = "canceled"
        pending["job_state"] = "canceled"
        return
    retry_count = max(0, int(holder.get("retry_count") or 0)) + 1
    reason = _dual_retry_reason_text(task, result) or "resultado_incompleto"
    error_class, retryable = _dual_retry_classification(reason)
    if retryable and bool(holder.get("requires_web")) and not holder.get("local_web_fallback_attempted"):
        fallback = _local_web_fallback_context(
            str(pending.get("request_text") or holder.get("prompt") or ""),
            str(pending.get("client_id") or "default"),
        )
        holder["local_web_fallback_attempted"] = True
        holder["local_web_fallback"] = fallback
        if fallback.get("success") is True:
            fallback_lines = [
                f"- {str(item.get('title') or '')[:200]} | {str(item.get('url') or '')[:600]} | {str(item.get('snippet') or '')[:500]}"
                for item in list(fallback.get("data") or [])[:5]
                if isinstance(item, dict)
            ]
            if fallback_lines:
                holder["prompt"] = (
                    str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or "")
                    + "\n\nFallback local de busca (validar nas paginas antes de concluir):\n"
                    + "\n".join(fallback_lines)
                )[:12000]
            pending["verified_sources"] = _dual_append_unique(
                list(pending.get("verified_sources") or []),
                fallback.get("sources"),
                limit=30,
                item_limit=1000,
            )
    try:
        _bridge_store().record_attempt(
            str(pending.get("message_id") or pending.get("job_group_id") or task_id),
            retry_count,
            "running" if retryable else "partial",
            error_class=error_class,
            retryable=retryable,
            details={"task_id": task_id, "reason": reason[:500]},
        )
    except Exception:
        pass
    if not retryable or retry_count > WHATSAPP_MAX_RETRY_ATTEMPTS:
        holder.update(
            {
                "state": "partial",
                "retry_count": retry_count,
                "retry_reason": reason[:1000],
                "retryable": False,
                "error_class": error_class,
                "next_retry_at_epoch": 0,
            }
        )
        pending.update(
            {
                "job_state": "partial",
                "retry_policy": "bounded",
                "terminal_reason": reason[:1000],
                "terminal_error_class": error_class,
            }
        )
        return
    delay = _dual_retry_delay_seconds(retry_count, reason, key)
    holder.update(
        {
            "state": "waiting_retry",
            "retry_count": retry_count,
            "retry_reason": reason[:1000],
            "next_retry_at_epoch": time.time() + delay,
            "next_retry_delay_seconds": delay,
            "auth_retry": False,
            "retryable": True,
            "error_class": error_class,
        }
    )
    pending.update(
        {
            "job_state": "waiting_retry",
            "retry_policy": "bounded",
            "last_retry_reason": reason[:1000],
            "last_retry_scheduled_at": _now(),
        }
    )
    if holder.get("auth_retry"):
        pending["auth_retry_active"] = True
        if pending.get("auth_notice_sent") is not True:
            pending["auth_notice_pending"] = True
    if task_id:
        codex_console._codex_update_task(
            task_id,
            handoff_status="retry_scheduled",
            delivery_state="waiting_retry",
            retry_count=retry_count,
            retry_reason=reason[:1000],
            next_retry_at_epoch=holder["next_retry_at_epoch"],
        )

def _dual_migrate_pending_v7(pending: dict[str, Any]) -> bool:
    changed = False
    before_deadline = float(pending.get("deadline_at_epoch") or 0)
    before_policy = str(pending.get("retry_policy") or "")
    _ensure_job_contract(pending)
    if not before_deadline or before_policy != "bounded":
        changed = True
    pending.setdefault("verified_facts", [])
    pending.setdefault("verified_sources", [])
    kind = str(pending.get("kind") or "")
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    for holder in holders:
        holder.setdefault("retry_count", 0)
        holder.setdefault("attempt_task_ids", [str(holder.get("task_id") or "")] if holder.get("task_id") else [])
        expected_retry_count = max(0, int(holder.get("current_attempt") or 1) - 1)
        if int(holder.get("retry_count") or 0) < expected_retry_count:
            holder["retry_count"] = expected_retry_count
            changed = True
        state = str(holder.get("state") or "")
        task_id = str(holder.get("task_id") or "")
        task = codex_console._codex_load_task(task_id) if task_id else None
        status = str((task or {}).get("status") or "")
        canceled_by_deadline = bool(
            status == "canceled"
            and str((task or {}).get("cancel_source") or "") == "whatsapp_deadline"
        )
        if canceled_by_deadline:
            holder["state"] = "partial"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = "limite_total_atingido"
            pending["job_state"] = "partial"
            changed = True
            continue
        if int(holder.get("retry_count") or 0) > WHATSAPP_MAX_RETRY_ATTEMPTS and state in {
            "waiting_retry",
            "retry_starting",
            "running",
            "waiting_result",
        }:
            holder["state"] = "partial"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = str(holder.get("retry_reason") or "limite_de_tentativas_atingido")[:1000]
            pending["job_state"] = "partial"
            changed = True
            continue
        if state in {"running", "waiting_result"} and status in {"", "awaiting_approval"}:
            # Sol do WhatsApp e sempre read-only e pre-aprovado. Uma tentativa
            # antiga sem arquivo ou presa em aprovacao nao pode bloquear o job;
            # ela vira uma nova tentativa, sem autorizar nem cancelar a antiga.
            holder["state"] = "waiting_retry"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = "stale_readonly_attempt"
            changed = True
            continue
        if state == "retry_starting":
            holder["state"] = "waiting_retry"
            holder["next_retry_at_epoch"] = 0
            changed = True
            continue
        if state:
            continue
        if status in {"queued", "running", "cancel_requested"}:
            holder["state"] = "running"
        else:
            holder["state"] = "waiting_result" if task_id else "waiting_retry"
            if not task_id:
                holder["next_retry_at_epoch"] = 0
        changed = True
    holder_states = {str(holder.get("state") or "") for holder in holders}
    if "partial" in holder_states and not ({"running", "waiting_retry", "awaiting_input"} & holder_states):
        pending["job_state"] = "partial"
        changed = True
    elif "running" not in holder_states and "awaiting_input" not in holder_states and "waiting_retry" in holder_states:
        pending["job_state"] = "waiting_retry"
        changed = True
    elif not str(pending.get("job_state") or ""):
        pending["job_state"] = "running"
        changed = True
    return changed

def _recover_dual_pending_after_restart(state: dict[str, Any]) -> int:
    """Reativa retries persistidos sem encerrar ou duplicar tarefas em curso."""

    recovered = 0
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, pending in list(pending_messages.items()):
        if not isinstance(pending, dict) or str(pending.get("kind") or "") not in {"dual_worker", "dual_job_group", "dual_function_manager"}:
            continue
        if str(pending.get("kind") or "") == "dual_function_manager":
            if str(pending.get("manager_state") or "") in {"running", "queued", "partial", "waiting_retry"}:
                pending.update(
                    {
                        "manager_state": "queued",
                        "job_state": "manager_queued",
                        "manager_next_retry_at_epoch": 0,
                        "restart_recovered_at": _now(),
                    }
                )
                _save_pending(state, str(message_id), pending)
                recovered += 1
            continue
        changed = _dual_migrate_pending_v7(pending)
        holders = (
            [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
            if str(pending.get("kind") or "") == "dual_job_group"
            else [pending]
        )
        for holder in holders:
            if str(holder.get("state") or "") == "waiting_retry":
                holder["next_retry_at_epoch"] = 0
                changed = True
                recovered += 1
        if changed:
            pending["restart_recovered_at"] = _now()
            _save_pending(state, str(message_id), pending)
    return recovered

def _dual_retry_pending_due(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> int:
    changed = _dual_migrate_pending_v7(pending)
    now_epoch = time.time()
    kind = str(pending.get("kind") or "")
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    created = 0
    for index, holder in enumerate(holders, start=1):
        if str(holder.get("state") or "") != "waiting_retry":
            continue
        if float(holder.get("next_retry_at_epoch") or 0) > now_epoch:
            continue
        holder["state"] = "retry_starting"
        holder["retry_started_at"] = _now()
        changed = True
        _save_pending(state, message_id, pending)
        old_task_id = str(holder.get("task_id") or "")
        logical_id = str(holder.get("logical_subtask_id") or holder.get("subtask_id") or ("main" if kind != "dual_job_group" else f"s{index}"))
        attempt = max(1, int(holder.get("current_attempt") or len(list(holder.get("attempt_task_ids") or [])) or 1)) + 1
        child_id = f"{logical_id}-a{attempt}"
        session = {
            "username": str(pending.get("username") or "").strip().lower(),
            "client_id": str(pending.get("client_id") or "").strip(),
            "permissions": dict(pending.get("session_permissions") or {}),
            "is_full": bool(pending.get("session_is_full")),
        }
        message = {
            "message_id": message_id,
            "subject_id": str(pending.get("subject_id") or ""),
            "wa_id": str(pending.get("wa_id") or ""),
            "message_type": "text",
            "text_body": str(pending.get("request_text") or ""),
            "received_at": pending.get("created_at") or _now(),
        }
        try:
            task = _create_dual_worker_task(
                config,
                message=message,
                message_id=message_id,
                subject=str(pending.get("subject_id") or ""),
                phone=str(pending.get("wa_id") or ""),
                session=session,
                conversation_id=str(pending.get("conversation_id") or ""),
                request_text=str(pending.get("request_text") or ""),
                job_prompt=str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or ""),
                job_title=str(holder.get("title") or pending.get("job_title") or "")[:180],
                media=pending.get("media") if isinstance(pending.get("media"), dict) and pending.get("media") else None,
                transcription=(
                    pending.get("transcription")
                    if isinstance(pending.get("transcription"), dict) and pending.get("transcription")
                    else None
                ),
                phone_ai_behavior=str(pending.get("phone_ai_behavior") or ""),
                query_policy=dict(pending.get("query_policy") or {}),
                job_group_id=str(pending.get("job_group_id") or message_id),
                subtask_id=child_id,
                logical_subtask_id=logical_id,
                attempt=attempt,
                subtask_index=index,
                subtask_total=max(1, len(holders)),
                requires_web=bool(holder.get("requires_web", True)),
                reasoning_effort=WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            )
        except Exception as exc:
            failure = {
                "status": "failed",
                "summary": "",
                "verified_facts": [],
                "sources": [],
                "missing": [str(exc)[:1000]],
                "questions": [],
            }
            _dual_schedule_retry(
                pending,
                holder,
                {"task_id": old_task_id, "status": "failed", "error": str(exc)[:1000], "completed_at": _now()},
                failure,
                key=f"{pending.get('job_group_id')}:{logical_id}:create",
            )
            pending["last_retry_creation_error"] = str(exc)[:1000]
            changed = True
            continue
        task_id = str(task.get("task_id") or "")
        history = [str(item or "") for item in list(holder.get("attempt_task_ids") or []) if str(item or "")]
        if old_task_id and old_task_id not in history:
            history.append(old_task_id)
        if task_id and task_id not in history:
            history.append(task_id)
        holder.update(
            {
                "task_id": task_id,
                "logical_subtask_id": logical_id,
                "state": "running",
                "current_attempt": attempt,
                "retry_count": max(int(holder.get("retry_count") or 0), attempt - 1),
                "attempt_task_ids": history[-50:],
                "next_retry_at_epoch": 0,
                "next_retry_delay_seconds": 0,
                "retry_started_at": "",
                "last_attempt_started_at": _now(),
            }
        )
        if kind != "dual_job_group":
            pending["task_id"] = task_id
        else:
            results = pending.get("group_results") if isinstance(pending.get("group_results"), dict) else {}
            results.pop(old_task_id, None)
            pending["group_results"] = results
            pending["collected_task_ids"] = [
                str(item or "")
                for item in list(pending.get("collected_task_ids") or [])
                if str(item or "") != old_task_id
            ]
        pending.update(
            {
                "task_id": str(pending.get("task_id") or task_id),
                "job_state": "running",
                "handoff_status": "worker_retry_running",
                "delivery_state": "worker_retry_running",
                "last_retry_started_at": _now(),
            }
        )
        codex_console._codex_update_task(
            task_id,
            retry_root_job_id=str(pending.get("job_group_id") or message_id),
            retry_count=int(holder.get("retry_count") or 0),
            current_attempt=attempt,
            attempt_task_ids=history[-50:],
        )
        created += 1
        changed = True
    if changed:
        _save_pending(state, message_id, pending)
    return created

def _whatsapp_is_retry_command(value: Any) -> bool:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return bool(re.search(r"\b(tente|tentar|continue|continuar|retome|retomar|de novo|novamente)\b", text))

def _resume_dual_pending_with_message(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    user_message: str,
) -> int:
    kind = str(pending.get("kind") or "")
    if kind == "dual_function_manager":
        original = str(pending.get("job_prompt") or pending.get("request_text") or "").strip()
        pending.update(
            {
                "job_prompt": (original + "\n\nInformacao adicional do usuario: " + str(user_message or "").strip())[-12000:],
                "manager_state": "queued",
                "job_state": "manager_queued",
                "manager_next_retry_at_epoch": 0,
                "manager_data_requests": [],
                "manager_revision": max(0, int(pending.get("manager_revision") or 0)) + 1,
                "pending_questions": [],
                "last_user_resume_at": _now(),
                "last_conversation_at": _now(),
                "last_conversation_at_epoch": time.time(),
            }
        )
        _save_pending(state, message_id, pending)
        _submit_function_manager_job(config, state, message_id)
        return 1
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    resumed = 0
    for holder in holders:
        if str(holder.get("state") or "") not in {"waiting_retry", "awaiting_input", "retry_starting"}:
            continue
        if str(holder.get("state") or "") == "awaiting_input" and str(user_message or "").strip():
            original = str(holder.get("prompt") or pending.get("job_prompt") or "").strip()
            holder["prompt"] = (original + "\n\nInformacao adicional do usuario: " + str(user_message).strip())[-12000:]
        holder.update(
            {
                "state": "waiting_retry",
                "next_retry_at_epoch": 0,
                "retry_reason": "user_resume",
                "auth_retry": False,
            }
        )
        resumed += 1
    if resumed:
        pending.update(
            {
                "job_state": "waiting_retry",
                "pending_questions": [],
                "last_user_resume_at": _now(),
                "last_conversation_at": _now(),
                "last_conversation_at_epoch": time.time(),
            }
        )
        _save_pending(state, message_id, pending)
        _dual_retry_pending_due(config, state, message_id, pending)
    return resumed


_COMPONENT_FUNCTIONS = frozenset((
    '_reload_bound_session',
    '_pending_task_for_message',
    '_active_pending_for_conversation',
    '_pending_task_ids',
    '_pending_codex_tasks',
    '_update_pending_codex_tasks',
    '_dual_retry_reason_text',
    '_dual_retry_is_auth_error',
    '_dual_retry_classification',
    '_dual_retry_delay_seconds',
    '_local_web_fallback_context',
    '_dual_append_unique',
    '_dual_preserve_worker_result',
    '_dual_worker_disposition',
    '_dual_schedule_retry',
    '_dual_migrate_pending_v7',
    '_recover_dual_pending_after_restart',
    '_dual_retry_pending_due',
    '_whatsapp_is_retry_command',
    '_resume_dual_pending_with_message'
))
_IMPLEMENTATIONS = {
    '_reload_bound_session': _reload_bound_session,
    '_pending_task_for_message': _pending_task_for_message,
    '_active_pending_for_conversation': _active_pending_for_conversation,
    '_pending_task_ids': _pending_task_ids,
    '_pending_codex_tasks': _pending_codex_tasks,
    '_update_pending_codex_tasks': _update_pending_codex_tasks,
    '_dual_retry_reason_text': _dual_retry_reason_text,
    '_dual_retry_is_auth_error': _dual_retry_is_auth_error,
    '_dual_retry_classification': _dual_retry_classification,
    '_dual_retry_delay_seconds': _dual_retry_delay_seconds,
    '_local_web_fallback_context': _local_web_fallback_context,
    '_dual_append_unique': _dual_append_unique,
    '_dual_preserve_worker_result': _dual_preserve_worker_result,
    '_dual_worker_disposition': _dual_worker_disposition,
    '_dual_schedule_retry': _dual_schedule_retry,
    '_dual_migrate_pending_v7': _dual_migrate_pending_v7,
    '_recover_dual_pending_after_restart': _recover_dual_pending_after_restart,
    '_dual_retry_pending_due': _dual_retry_pending_due,
    '_whatsapp_is_retry_command': _whatsapp_is_retry_command,
    '_resume_dual_pending_with_message': _resume_dual_pending_with_message
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
