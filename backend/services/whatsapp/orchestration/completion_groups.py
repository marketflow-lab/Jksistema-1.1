"""Extracted WhatsApp bridge component: completion_groups."""

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


def _dual_task_snapshot(task: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any]:
    group_results = pending.get("group_results") if isinstance(pending.get("group_results"), dict) else {}
    verified_partial: list[str] = [
        str(item or "").strip()[:1200]
        for item in list(pending.get("verified_facts") or [])
        if str(item or "").strip()
    ]
    for result in group_results.values():
        if not isinstance(result, dict):
            continue
        for fact in list(result.get("verified_facts") or []):
            text = str(fact or "").strip()
            if text and text not in verified_partial:
                verified_partial.append(text[:1200])
    return {
        "job_id": str(pending.get("job_group_id") or task.get("task_id") or ""),
        "job_title": str(pending.get("job_title") or pending.get("request_text") or "")[:500],
        "request_text": str(pending.get("request_text") or "")[:1200],
        "status": str(task.get("status") or ""),
        "verified_partial": verified_partial[:10],
        "subtasks_total": len(list(pending.get("subtasks") or [])) or 1,
        "subtasks_completed": len(list(pending.get("collected_task_ids") or [])),
        "recent_conversation_messages": [
            str(item or "")[:500]
            for item in list(pending.get("conversation_tick_messages") or [])[-5:]
            if str(item or "").strip()
        ],
    }

def _maybe_send_dual_conversation_tick(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task: dict[str, Any],
) -> bool:
    if str(task.get("status") or "") not in {"queued", "running"}:
        return False
    settings = _whatsapp_dual_agent_settings(config)
    notice_count = max(0, int(pending.get("wait_notice_count") or 0))
    interval = int(
        settings.get("wait_message_after_seconds")
        if notice_count == 0
        else settings.get("wait_message_repeat_seconds")
        if notice_count == 1
        else settings.get("wait_message_steady_seconds")
    )
    now_epoch = time.time()
    last_notice = float(pending.get("last_wait_notice_at_epoch") or 0)
    reference = last_notice or float(pending.get("created_at_epoch") or now_epoch)
    if now_epoch - reference < interval:
        return False
    refreshed_tasks = _pending_codex_tasks(pending)
    if refreshed_tasks and all(
        str(item.get("status") or "") in {"completed", "partial", "failed", "canceled"}
        for item in refreshed_tasks
    ):
        return False
    tick_index = notice_count + 1
    if notice_count == 0:
        progress_text = "Ainda estou consultando as fontes necessárias. Assim que concluir, envio o resultado aqui."
    elif notice_count == 1:
        progress_text = "A consulta continua em andamento. Ainda não há um resultado final confirmado."
    else:
        progress_text = "Continuo processando sua solicitação. Avisarei assim que houver um resultado confirmado."
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{pending.get('job_group_id') or task.get('task_id')}:conversation:{tick_index}",
            "event_type": "task_conversation",
            "severity": "info",
            "text": progress_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        return False
    pending.update(
        {
            "tick_index": tick_index,
            "wait_notice_count": notice_count + 1,
            "last_wait_notice_at": _now(),
            "last_wait_notice_at_epoch": now_epoch,
            "handoff_status": "worker_running",
            "delivery_state": f"conversation_tick_{delivery}",
            "conversation_tick_messages": (
                list(pending.get("conversation_tick_messages") or [])
                + [progress_text]
            )[-5:],
        }
    )
    _save_pending(state, message_id, pending)
    _update_pending_codex_tasks(
        pending,
        handoff_status="worker_running",
        last_conversation_tick_at=_now(),
        delivery_state=f"conversation_tick_{delivery}",
    )
    return True

def _dual_worker_result_is_meaningful(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "")
    if _dual_retry_is_auth_error(
        str(result.get("summary") or "") + " " + " ".join(str(item or "") for item in list(result.get("missing") or []))
    ):
        return True
    if status == "blocked" and list(result.get("questions") or []):
        return True
    if status not in {"completed", "partial"}:
        return False
    return bool(list(result.get("verified_facts") or []) or str(result.get("summary") or "").strip())

def _dual_worker_result_is_auth_error(result: dict[str, Any]) -> bool:
    return _dual_retry_is_auth_error(
        str(result.get("summary") or "")
        + " "
        + " ".join(str(item or "") for item in list(result.get("missing") or []))
    )

def _maybe_send_dual_auth_notice(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    if pending.get("auth_notice_pending") is not True or pending.get("auth_notice_sent") is True:
        return False
    reason = str(pending.get("last_retry_reason") or "integracao desconectada (HTTP 401/403)").strip()
    worker_result = {
        "status": "partial",
        "summary": "Uma fonte exige reconexao ou nova autenticacao. A solicitacao continua pendente e sera repetida automaticamente.",
        "verified_facts": list(pending.get("verified_facts") or []),
        "sources": list(pending.get("verified_sources") or []),
        "confidence": "low",
        "missing": [reason[:1000]],
        "questions": [],
    }
    text = (
        "Não consegui acessar uma das integrações porque a autenticação está inválida ou expirada. "
        "Reconecte a conta em Integrações e depois peça para tentar novamente."
    )
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{pending.get('job_group_id') or message_id}:auth-required",
            "event_type": "task_partial",
            "severity": "medium",
            "text": text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        return False
    pending.update(
        {
            "auth_notice_sent": True,
            "auth_notice_pending": False,
            "auth_notice_sent_at": _now(),
            "last_conversation_at": _now(),
            "last_conversation_at_epoch": time.time(),
            "delivery_state": f"auth_notice_{delivery}",
        }
    )
    _save_pending(state, message_id, pending)
    return True

def _aggregate_dual_group_results(
    pending: dict[str, Any],
    *,
    task_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    results = pending.get("group_results") if isinstance(pending.get("group_results"), dict) else {}
    selected_ids = task_ids if task_ids is not None else list(results)
    summaries: list[str] = []
    facts: list[str] = [
        str(item or "").strip()[:2000]
        for item in list(pending.get("verified_facts") or [])
        if str(item or "").strip()
    ]
    sources: list[str] = [
        str(item or "").strip()[:1000]
        for item in list(pending.get("verified_sources") or [])
        if str(item or "").strip()
    ]
    missing: list[str] = []
    questions: list[str] = []
    statuses: list[str] = []
    confidences: list[str] = []
    subtask_details: list[dict[str, Any]] = []
    title_by_task = {
        str(item.get("task_id") or ""): str(item.get("title") or "").strip()
        for item in list(pending.get("subtasks") or [])
        if isinstance(item, dict)
    }

    def append_unique(target: list[str], values: Any, limit: int) -> None:
        for value in list(values or []):
            text = str(value or "").strip()
            if text and text not in target:
                target.append(text[:limit])

    for task_id in selected_ids:
        result = results.get(task_id)
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "failed")
        statuses.append(status)
        confidence = str(result.get("confidence") or "unknown")
        confidences.append(confidence)
        summary = str(result.get("summary") or "").strip()
        title = title_by_task.get(task_id, "")
        if summary:
            summaries.append(f"{title}: {summary}" if title else summary)
        append_unique(facts, result.get("verified_facts"), 2000)
        append_unique(sources, result.get("sources"), 1000)
        append_unique(missing, result.get("missing"), 1000)
        append_unique(questions, result.get("questions"), 1000)
        subtask_details.append(
            {
                "task_id": task_id,
                "title": title,
                "status": status,
                "summary": summary[:6000],
                "confidence": confidence,
            }
        )
    if statuses and all(status == "completed" for status in statuses):
        aggregate_status = "completed"
    elif any(status in {"completed", "partial"} for status in statuses):
        aggregate_status = "partial"
    elif any(status == "blocked" for status in statuses):
        aggregate_status = "blocked"
    else:
        aggregate_status = "failed"
    confidence_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    confidence = min(confidences, key=lambda item: confidence_rank.get(item, 0)) if confidences else "unknown"
    return {
        "status": aggregate_status,
        "summary": "\n".join(summaries)[:12000],
        "verified_facts": facts[:30],
        "sources": sources[:30],
        "confidence": confidence,
        "missing": missing[:20],
        "questions": questions[:10],
        "subtasks": subtask_details,
        "coverage": {
            "completed": len(statuses),
            "total": len(list(pending.get("subtasks") or [])),
        },
    }

def _function_manager_requeue_from_sol(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    requests: list[dict[str, Any]],
    holders: list[dict[str, Any]],
) -> bool:
    clean_requests = [item for item in requests if isinstance(item, dict) and str(item.get("need") or "").strip()][:6]
    if not clean_requests:
        return False
    retry_subtasks: list[dict[str, Any]] = []
    for index, holder in enumerate(holders[:6], start=1):
        logical_id = str(holder.get("logical_subtask_id") or holder.get("subtask_id") or f"manager-sol-{index}")
        retry_subtasks.append(
            {
                "logical_subtask_id": logical_id,
                "title": str(holder.get("title") or pending.get("job_title") or "Analise complementar")[:180],
                "prompt": str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or "")[:12000],
                "requires_web": bool(holder.get("requires_web", True)),
                "current_attempt": max(1, int(holder.get("current_attempt") or 1)) + 1,
                "attempt_task_ids": list(holder.get("attempt_task_ids") or []),
            }
        )
    pending.update(
        {
            "kind": "dual_function_manager",
            "task_id": "",
            "subtasks": [],
            "manager_data_requests": clean_requests,
            "manager_state": "queued",
            "job_state": "manager_queued",
            "manager_next_retry_at_epoch": 0,
            "manager_revision": max(0, int(pending.get("manager_revision") or 0)) + 1,
            "sol_subtasks": retry_subtasks,
            "handoff_status": "sol_requested_manager_data",
            "delivery_state": "manager_recollecting",
            "last_sol_data_request_at": _now(),
        }
    )
    _save_pending(state, message_id, pending)
    _update_pending_codex_tasks(
        pending,
        handoff_status="manager_data_requested",
        delivery_state="manager_recollecting",
    )
    _submit_function_manager_job(config, state, message_id)
    return True

def _collect_dual_group_tasks(
    pending: dict[str, Any],
    subtasks: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], set[str], bool, list[dict[str, Any]], list[dict[str, Any]]]:
    terminal = {"completed", "partial", "failed", "canceled"}
    results = dict(pending.get("group_results") or {}) if isinstance(pending.get("group_results"), dict) else {}
    collected = {str(item or "") for item in list(pending.get("collected_task_ids") or [])}
    tasks: dict[str, dict[str, Any]] = {}
    changed = False
    manager_requests: list[dict[str, Any]] = []
    manager_holders: list[dict[str, Any]] = []
    for item in subtasks:
        task_id = str(item.get("task_id") or "")
        task = codex_console._codex_load_task(task_id) if task_id else None
        if not isinstance(task, dict):
            continue
        tasks[task_id] = task
        if str(task.get("status") or "") not in terminal or task_id in collected:
            continue
        worker_result = codex_whatsapp_agents.normalize_worker_result(task)
        results[task_id] = worker_result
        collected.add(task_id)
        changed = True
        _dual_preserve_worker_result(pending, worker_result)
        disposition = _dual_worker_disposition(task, worker_result)
        if disposition == "completed":
            item.update(
                {
                    "state": "completed",
                    "next_retry_at_epoch": 0,
                    "last_attempt_at": str(task.get("completed_at") or _now()),
                    "last_progress_at": str(task.get("last_progress_at") or task.get("completed_at") or _now()),
                }
            )
        elif disposition == "manager_request":
            item.update({"state": "manager_waiting", "next_retry_at_epoch": 0, "last_attempt_at": str(task.get("completed_at") or _now())})
            manager_requests.extend(value for value in list(worker_result.get("data_requests") or []) if isinstance(value, dict))
            manager_holders.append(dict(item))
        else:
            _dual_schedule_retry(
                pending,
                item,
                task,
                worker_result,
                key=f"{pending.get('job_group_id')}:{item.get('logical_subtask_id') or item.get('subtask_id')}",
            )
        handoff = "group_result_ready" if disposition == "completed" else "manager_data_requested" if disposition == "manager_request" else str(item.get("state") or "waiting_retry")
        delivery = "group_aggregating" if disposition == "completed" else "manager_recollecting" if disposition == "manager_request" else str(item.get("state") or "waiting_retry")
        codex_console._codex_update_task(task_id, worker_result=worker_result, handoff_status=handoff, delivery_state=delivery)
    return tasks, results, collected, changed, manager_requests, manager_holders

def _update_dual_group_job_state(pending: dict[str, Any], subtasks: list[dict[str, Any]]) -> bool:
    holder_states = {str(item.get("state") or "") for item in subtasks}
    all_terminal = bool(subtasks) and holder_states.issubset({"completed", "partial", "canceled"})
    if all_terminal:
        pending["job_state"] = "completed" if holder_states == {"completed"} else "partial"
    elif any(str(item.get("state") or "") == "running" for item in subtasks):
        pending["job_state"] = "running"
    elif any(str(item.get("state") or "") == "awaiting_input" for item in subtasks):
        pending["job_state"] = "awaiting_input"
    else:
        pending["job_state"] = "waiting_retry"
    return all_terminal

def _dual_group_meaningful_results(
    pending: dict[str, Any],
    results: dict[str, Any],
    collected: set[str],
) -> tuple[list[str], bool]:
    delivered = {str(item or "") for item in list(pending.get("delivered_task_ids") or [])}
    delivered_before_filter = set(delivered)
    meaningful: list[str] = []
    for task_id in collected:
        result = results.get(task_id) or {}
        if task_id in delivered or not _dual_worker_result_is_meaningful(result):
            continue
        if pending.get("auth_notice_sent") is True and _dual_worker_result_is_auth_error(result):
            delivered.add(task_id)
            continue
        meaningful.append(task_id)
    changed = delivered != delivered_before_filter
    if changed:
        pending["delivered_task_ids"] = sorted(delivered)
    if meaningful and not pending.get("partial_pending_since_epoch"):
        pending["partial_pending_since_epoch"] = time.time()
        changed = True
    return meaningful, changed

def _buffer_dual_group_partial(
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    meaningful: list[str],
) -> None:
    delivered = {str(item or "") for item in list(pending.get("delivered_task_ids") or [])}
    pending["delivered_task_ids"] = sorted(delivered.union(meaningful))
    pending["partial_pending_since_epoch"] = 0
    pending["delivery_state"] = "partial_evidence_buffered"
    _save_pending(state, message_id, pending)

def _dual_group_final_response(
    config: dict[str, Any],
    state: dict[str, Any],
    pending: dict[str, Any],
    message_id: str,
    final_result: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    synthetic = {"task_id": str(pending.get("job_group_id") or message_id), "status": "completed"}
    try:
        decision = _run_conversation_agent(
            config,
            state,
            str(pending.get("conversation_id") or ""),
            event_type="worker_result",
            user_message=str(pending.get("request_text") or ""),
            active_job=_dual_task_snapshot(synthetic, pending),
            worker_result=final_result,
            ai_behavior=str(pending.get("phone_ai_behavior") or ""),
            tick_index=int(pending.get("tick_index") or 0),
            client_id=str(pending.get("client_id") or ""),
        )
        return decision, str(decision.get("reply_text") or "").strip()
    except Exception as exc:
        RUNTIME_STATE["conversation_fallback_last_error"] = str(exc)[:500]
        return {}, _worker_result_fallback_text(final_result, pending)

def _dual_group_report_response(
    config: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    final_text: str,
) -> str:
    report_request = str(pending.get("request_text") or "")
    if not whatsapp_report_files.report_requested(report_request):
        return final_text
    artifacts = [
        artifact for task in tasks.values() for artifact in list(task.get("whatsapp_artifacts") or [])
        if isinstance(artifact, dict)
    ][:4]
    artifact_results = _whatsapp_deliver_report_artifacts(
        config, message_id, artifacts, pending.get("client_id") or "default", max_images=4,
    ) if artifacts else []
    summaries = [
        summary for task in tasks.values() for summary in list(task.get("tool_results_summary") or [])
        if isinstance(summary, dict)
    ]
    final_text = "\n\n".join(
        item for item in (
            final_text,
            _whatsapp_report_metadata_text(report_request, pending.get("query_policy"), summaries),
            whatsapp_report_files.report_offer_text(report_request),
        ) if item
    ).strip()
    if artifacts and not all(item.get("success") for item in artifact_results):
        final_text += "\n\nUm ou mais arquivos nao puderam ser anexados; o resumo em texto foi preservado."
    for task_id in tasks:
        codex_console._codex_update_task(task_id, whatsapp_artifacts=[])
    return final_text

def _deliver_dual_group_final(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    final_result: dict[str, Any],
    decision: dict[str, Any],
    final_text: str,
) -> bool:
    group_id = str(pending.get("job_group_id") or message_id)
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{group_id}:final:{hashlib.sha256(final_text.encode('utf-8')).hexdigest()[:16]}",
            "event_type": "task_completed" if final_result.get("status") in {"completed", "partial"} else "task_failed",
            "severity": "info" if final_result.get("status") == "completed" else "medium",
            "text": final_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["delivery_state"] = f"final_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        return False
    _update_pending_codex_tasks(
        pending,
        handoff_status="delivered_by_conversation_agent",
        delivery_state=delivery,
        conversation_agent_thread_id=str(decision.get("thread_id") or "")[:200],
        user_facing_response=final_text[:12000],
    )
    _whatsapp_update_query_context_from_task(state, pending, next(iter(tasks.values()), {}))
    _record_message_timing(message_id, completed_at=_now(), sent_at=_now())
    status = "completed" if str(final_result.get("status") or "") == "completed" else "partial"
    _remove_pending(state, message_id, status=status, reason="" if status == "completed" else "resultado_parcial")
    return True

def _complete_dual_job_group_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    subtasks = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
    if not subtasks:
        return False
    tasks, results, collected, changed, manager_requests, manager_holders = _collect_dual_group_tasks(pending, subtasks)
    pending["group_results"] = results
    pending["collected_task_ids"] = sorted(collected)
    if manager_requests:
        pending["prior_group_results"] = results
        _function_manager_requeue_from_sol(
            config, state, message_id, pending, manager_requests, manager_holders,
        )
        return False
    all_terminal = _update_dual_group_job_state(pending, subtasks)
    meaningful, meaningful_changed = _dual_group_meaningful_results(pending, results, collected)
    if changed or meaningful_changed:
        _save_pending(state, message_id, pending)
    if not all_terminal and meaningful:
        _buffer_dual_group_partial(state, message_id, pending, meaningful)
    if not all_terminal:
        synthetic_status = "running" if any(str(task.get("status") or "") == "running" for task in tasks.values()) else "queued"
        _maybe_send_dual_conversation_tick(
            config,
            state,
            message_id,
            pending,
            {"task_id": str(pending.get("job_group_id") or message_id), "status": synthetic_status},
        )
        return False
    final_result = _aggregate_dual_group_results(pending)
    decision, final_text = _dual_group_final_response(config, state, pending, message_id, final_result)
    final_text = _dual_group_report_response(config, message_id, pending, tasks, final_text)
    return _deliver_dual_group_final(
        config, state, message_id, pending, tasks, final_result, decision, final_text,
    )

_COMPONENT_FUNCTIONS = frozenset((
    '_dual_task_snapshot',
    '_maybe_send_dual_conversation_tick',
    '_dual_worker_result_is_meaningful',
    '_dual_worker_result_is_auth_error',
    '_maybe_send_dual_auth_notice',
    '_aggregate_dual_group_results',
    '_function_manager_requeue_from_sol',
    '_complete_dual_job_group_pending'
))
_IMPLEMENTATIONS = {
    '_dual_task_snapshot': _dual_task_snapshot,
    '_maybe_send_dual_conversation_tick': _maybe_send_dual_conversation_tick,
    '_dual_worker_result_is_meaningful': _dual_worker_result_is_meaningful,
    '_dual_worker_result_is_auth_error': _dual_worker_result_is_auth_error,
    '_maybe_send_dual_auth_notice': _maybe_send_dual_auth_notice,
    '_aggregate_dual_group_results': _aggregate_dual_group_results,
    '_function_manager_requeue_from_sol': _function_manager_requeue_from_sol,
    '_complete_dual_job_group_pending': _complete_dual_job_group_pending
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
