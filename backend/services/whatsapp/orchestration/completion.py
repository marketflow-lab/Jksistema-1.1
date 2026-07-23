"""Extracted WhatsApp bridge component: completion."""

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
from backend.services.whatsapp import delivery as whatsapp_delivery
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import marketplace_listing_delivery as whatsapp_marketplace_listing
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

def _load_terminal_dual_worker(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> tuple[str, dict[str, Any], float]:
    task_id = str(pending.get("task_id") or "")
    task = codex_console._codex_load_task(task_id) if task_id else None
    if not isinstance(task, dict):
        return "", {}, 0.0
    status = str(task.get("status") or "")
    if status not in {"completed", "partial", "failed", "canceled"}:
        _maybe_send_dual_conversation_tick(config, state, message_id, pending, task)
        return "", {}, 0.0
    sol_started = task.get("sol_started_at") or task.get("started_at") or ""
    completed_at = task.get("completed_at") or _now()
    _record_message_timing(message_id, sol_started_at=sol_started, completed_at=completed_at)
    sol_started_epoch = _timing_epoch(sol_started)
    completed_epoch = _timing_epoch(completed_at)
    if sol_started_epoch and completed_epoch:
        _record_latency("sol_duration", completed_epoch - sol_started_epoch)
    return task_id, task, completed_epoch

def _dual_worker_requeue_manager(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task_id: str,
    worker_result: dict[str, Any],
) -> None:
    _dual_preserve_worker_result(pending, worker_result)
    codex_console._codex_update_task(
        task_id,
        worker_result=worker_result,
        handoff_status="manager_data_requested",
        delivery_state="manager_recollecting",
    )
    holder = {
        "logical_subtask_id": str(pending.get("logical_subtask_id") or pending.get("subtask_id") or "main"),
        "subtask_id": str(pending.get("subtask_id") or "main"),
        "title": str(pending.get("title") or pending.get("job_title") or "Analise complementar"),
        "prompt": str(pending.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or ""),
        "requires_web": bool(pending.get("requires_web", True)),
        "current_attempt": max(1, int(pending.get("current_attempt") or 1)),
        "attempt_task_ids": list(pending.get("attempt_task_ids") or [task_id]),
    }
    requests = [item for item in list(worker_result.get("data_requests") or []) if isinstance(item, dict)]
    _function_manager_requeue_from_sol(config, state, message_id, pending, requests, [holder])

def _notify_dual_worker_awaiting_input(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    worker_result: dict[str, Any],
) -> None:
    questions = [
        str(item or "").strip()
        for item in list(worker_result.get("questions") or pending.get("pending_questions") or [])
        if str(item or "").strip()
    ]
    prompt = "Preciso desta informação para continuar: " + (
        questions[0] if questions else "informe os dados que faltam no pedido."
    )
    delivery_result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{pending.get('job_group_id') or message_id}:awaiting-input",
            "event_type": "task_partial",
            "severity": "info",
            "text": prompt[:3500],
        },
    )
    pending["awaiting_notified"] = str(delivery_result.get("status") or "") in {
        "sent", "queued", "duplicate", "waiting_free_window",
    }
    pending["delivery_state"] = f"awaiting_input_{str(delivery_result.get('status') or 'failed')}"
    _save_pending(state, message_id, pending)

def _handle_incomplete_dual_worker(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    worker_result: dict[str, Any],
    disposition: str,
) -> bool:
    handled = [str(item or "") for item in list(pending.get("handled_attempt_task_ids") or []) if str(item or "")]
    if task_id not in handled:
        _dual_schedule_retry(pending, pending, task, worker_result, key=f"{pending.get('job_group_id') or message_id}:main")
        handled.append(task_id)
        pending["handled_attempt_task_ids"] = handled[-50:]
        _save_pending(state, message_id, pending)
        if str(pending.get("job_state") or "") == "partial":
            return _terminate_pending_partial(
                config,
                state,
                message_id,
                pending,
                reason=str(pending.get("terminal_reason") or "resultado_parcial"),
            )
        if disposition == "awaiting_input":
            _notify_dual_worker_awaiting_input(config, state, message_id, pending, worker_result)
            return False
    _maybe_send_dual_conversation_tick(
        config,
        state,
        message_id,
        pending,
        {"task_id": str(pending.get("job_group_id") or task_id), "status": "queued"},
    )
    return False

def _dual_worker_final_response(
    config: dict[str, Any],
    state: dict[str, Any],
    pending: dict[str, Any],
    task: dict[str, Any],
    worker_result: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    try:
        decision = _run_conversation_agent(
            config,
            state,
            str(pending.get("conversation_id") or ""),
            event_type="worker_result",
            user_message=str(pending.get("request_text") or ""),
            active_job=_dual_task_snapshot(task, pending),
            worker_result=worker_result,
            ai_behavior=str(pending.get("phone_ai_behavior") or ""),
            tick_index=int(pending.get("tick_index") or 0),
            client_id=str(pending.get("client_id") or ""),
        )
        return decision, str(decision.get("reply_text") or "").strip()
    except Exception as exc:
        RUNTIME_STATE["conversation_fallback_last_error"] = str(exc)[:500]
        return {}, _worker_result_fallback_text(worker_result, pending)

def _dual_worker_report_response(
    config: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    final_text: str,
) -> str:
    report_request = pending.get("request_text") or task.get("prompt") or ""
    if not whatsapp_report_files.report_requested(report_request):
        return final_text
    artifact_results = _whatsapp_deliver_report_artifacts(
        config,
        message_id,
        task.get("whatsapp_artifacts"),
        pending.get("client_id") or task.get("client_id") or "default",
        max_images=4,
    )
    final_text = "\n\n".join(
        item for item in (
            final_text,
            _whatsapp_report_metadata_text(
                report_request,
                pending.get("query_policy") or task.get("query_policy"),
                task.get("tool_results_summary") or [],
            ),
            whatsapp_report_files.report_offer_text(report_request),
        ) if item
    ).strip()
    if task.get("whatsapp_artifacts") and not all(item.get("success") for item in artifact_results):
        final_text += "\n\nUm ou mais arquivos nao puderam ser anexados; o resumo em texto foi preservado."
    if task.get("whatsapp_artifacts"):
        codex_console._codex_update_task(task_id, whatsapp_artifacts=[])
    return final_text

def _deliver_dual_worker_final(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    worker_result: dict[str, Any],
    decision: dict[str, Any],
    final_text: str,
    completed_epoch: float,
) -> bool:
    event_type = "task_completed" if worker_result.get("status") in {"completed", "partial"} else "task_failed"
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{task_id}:final:{hashlib.sha256(final_text.encode('utf-8')).hexdigest()[:16]}",
            "event_type": event_type,
            "severity": "info" if worker_result.get("status") == "completed" else "medium",
            "text": final_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["delivery_state"] = f"final_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        codex_console._codex_update_task(task_id, delivery_state=pending["delivery_state"])
        return False
    delivery_confirmed = whatsapp_delivery.delivery_receipt_confirmed(result)
    if not delivery_confirmed:
        pending["delivery_state"] = "final_delivery_pending"
        _save_pending(state, message_id, pending)
        codex_console._codex_update_task(
            task_id,
            handoff_status="delivery_pending",
            delivery_state=pending["delivery_state"],
            conversation_agent_thread_id="",
        )
        _discard_unconfirmed_assistant_reply(
            state,
            str(pending.get("conversation_id") or ""),
            final_text,
        )
        return False
    codex_console._codex_update_task(
        task_id,
        handoff_status="delivered_by_conversation_agent",
        delivery_state=delivery,
        conversation_agent_thread_id=str(decision.get("thread_id") or "")[:200],
        user_facing_response=final_text[:12000],
    )
    sent_epoch = time.time()
    _record_message_timing(message_id, sent_at=_now())
    if completed_epoch:
        _record_latency("completed_to_sent", sent_epoch - completed_epoch)
    if delivery_confirmed:
        try:
            _record_shared_delivered_exchange(
                client_id=str(pending.get("client_id") or task.get("client_id") or ""),
                username=str(pending.get("username") or task.get("created_by") or ""),
                phone=str(pending.get("wa_id") or ""),
                subject_id=str(pending.get("subject_id") or ""),
                prompt=str(pending.get("request_text") or task.get("prompt") or ""),
                response=final_text,
                event_id=f"{message_id}:final",
            )
        except Exception:
            pass
    _whatsapp_update_query_context_from_task(state, pending, task)
    status = "completed" if str(worker_result.get("status") or "") == "completed" else "partial"
    reason = "" if status == "completed" else "resultado_parcial"
    _remove_pending(state, message_id, status=status, reason=reason)
    return True

def _complete_standard_task_artifacts(
    config: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task: dict[str, Any],
    response: str,
) -> str:
    task_id = str(pending.get("task_id") or "")
    client_id = pending.get("client_id") or task.get("client_id") or config.get("client_id")
    chart_results = _whatsapp_deliver_report_artifacts(config, message_id, task.get("whatsapp_artifacts"), client_id, max_images=4)
    charts_sent = sum(1 for item in chart_results if item.get("success") and item.get("artifact_type") == "report_chart")
    report_files_sent = sum(1 for item in chart_results if item.get("success"))
    image_results = list(chart_results)
    request_text = pending.get("request_text") or task.get("prompt")
    remaining_images = max(0, WHATSAPP_MAX_OUTBOUND_IMAGES - charts_sent)
    if remaining_images > 0:
        listing_bundle = task.get("whatsapp_listing_bundle") if isinstance(task.get("whatsapp_listing_bundle"), dict) else {}
        listing_results = _whatsapp_deliver_marketplace_listing_images(
            config, message_id, listing_bundle, request_text, max_images=remaining_images,
        ) if listing_bundle else []
        if listing_results:
            image_results.extend(listing_results)
            response = _whatsapp_strip_image_references(response)
            sent_listing_images = sum(1 for item in listing_results if item.get("success"))
            if sent_listing_images < len(listing_results):
                response += f"\n\nFotos: enviei {sent_listing_images} de {len(listing_results)} imagem(ns); as demais ficaram indisponiveis nesta tentativa."
        elif listing_bundle and whatsapp_marketplace_listing.pictures_requested(request_text):
            response = _whatsapp_strip_image_references(response)
            response += "\n\nFotos: a API do Mercado Livre nao retornou uma imagem oficial utilizavel para este anuncio."
        else:
            response, product_results = _whatsapp_deliver_requested_images(
                config, message_id, response, request_text, client_id, max_images=remaining_images,
            )
            image_results.extend(product_results)
    elif _whatsapp_image_requested(request_text):
        response = _whatsapp_strip_image_references(response)
    if task.get("whatsapp_chart_expected") is True and charts_sent == 0:
        response = (response.rstrip() + "\n\n_O relatório em texto está completo. O gráfico visual ficou indisponível nesta execução; "
                    "a proteção de custo zero não permitiu usar uma alternativa paga._").strip()
    if task.get("whatsapp_artifacts"):
        codex_console._codex_update_task(
            task_id,
            whatsapp_artifacts=[],
            whatsapp_chart_status="sent" if report_files_sent else "send_failed",
            whatsapp_chart_error="" if report_files_sent else str(task.get("whatsapp_chart_error") or "report_artifact_not_sent")[:500],
        )
    if image_results:
        RUNTIME_STATE["last_outbound_images"] = image_results[-WHATSAPP_MAX_OUTBOUND_IMAGES:]
    report_request = pending.get("request_text") or task.get("prompt") or ""
    if whatsapp_report_files.report_requested(report_request):
        response = "\n\n".join(
            item for item in (
                response,
                _whatsapp_report_metadata_text(
                    report_request,
                    pending.get("query_policy") or task.get("query_policy"),
                    task.get("tool_results_summary") or [],
                ),
                whatsapp_report_files.report_offer_text(report_request),
            ) if item
        ).strip()
    return response

def _deliver_standard_task_result(
    config: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task_id: str,
    status: str,
    parts: list[str],
    allow_full_history: bool,
) -> None:
    if pending.get("awaiting_notified"):
        _post_proactive(
            config,
            {
                "subject_id": str(pending.get("subject_id") or ""),
                "fingerprint": f"task:{task_id}:{status}",
                "event_type": "task_completed" if status in {"completed", "partial"} else "task_failed",
                "severity": "medium" if status == "partial" else ("high" if status != "completed" else "info"),
                "text": parts[0],
                "text_parts": parts,
                "allow_full_history": allow_full_history,
            },
        )
        return
    _post_message_result(
        config,
        message_id,
        {
            "status": "completed" if status in {"completed", "partial"} else "failed",
            "task_id": task_id,
            "response": parts[0],
            "response_parts": parts,
            "allow_full_history": allow_full_history,
        },
    )


def _complete_dual_worker_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    task_id, task, completed_epoch = _load_terminal_dual_worker(config, state, message_id, pending)
    if not task_id:
        return False
    worker_result = codex_whatsapp_agents.normalize_worker_result(task)
    disposition = _dual_worker_disposition(task, worker_result)
    if disposition == "manager_request":
        _dual_worker_requeue_manager(config, state, message_id, pending, task_id, worker_result)
        return False
    if disposition != "completed":
        return _handle_incomplete_dual_worker(
            config, state, message_id, pending, task_id, task, worker_result, disposition,
        )
    _dual_preserve_worker_result(pending, worker_result)
    codex_console._codex_update_task(
        task_id,
        worker_result=worker_result,
        handoff_status="result_ready",
        delivery_state="conversation_agent_finalizing",
    )
    decision, final_text = _dual_worker_final_response(config, state, pending, task, worker_result)
    final_text = _dual_worker_report_response(config, message_id, pending, task_id, task, final_text)
    return _deliver_dual_worker_final(
        config, state, message_id, pending, task_id, task, worker_result,
        decision, final_text, completed_epoch,
    )

def _complete_pending(config: dict[str, Any], state: dict[str, Any], message_id: str, pending: dict[str, Any]) -> bool:
    kind = str(pending.get("kind") or "task")
    if kind == "action_proposal":
        return _complete_action_pending(config, state, message_id, pending)
    if kind == "dual_job_group":
        return _complete_dual_job_group_pending(config, state, message_id, pending)
    if kind == "dual_worker":
        return _complete_dual_worker_pending(config, state, message_id, pending)
    if kind == "dual_function_manager":
        manager_state = str(pending.get("manager_state") or "queued")
        if manager_state in {"running", "queued", "waiting_retry", "partial"}:
            task = {
                "task_id": str(pending.get("job_group_id") or message_id),
                "status": "running" if manager_state == "running" else "queued",
            }
            _maybe_send_dual_conversation_tick(config, state, message_id, pending, task)
        return False
    task_id = str(pending.get("task_id") or "")
    task = codex_console._codex_load_task(task_id) if task_id else None
    if not task:
        return False
    status = str(task.get("status") or "")
    if status == "awaiting_approval" and not pending.get("awaiting_notified"):
        if task.get("whatsapp_full_access") is True:
            _notify_pending_approval(config, state, message_id, pending)
        else:
            response = (
                "Este pedido exige aprovação no aplicativo JK Sistema porque o vínculo não possui o modo móvel full. "
                "Abra a tarefa no Black John e informe o módulo ou caminho permitido."
            )
            parts = _whatsapp_response_parts(response, _whatsapp_result_title("", "awaiting_approval"))
            _post_message_result(
                config, message_id,
                {"status": "awaiting_approval", "task_id": task_id, "response": parts[0], "response_parts": parts},
            )
            pending["awaiting_notified"] = True
            _save_pending(state, message_id, pending)
        return False
    if status not in {"completed", "partial", "failed", "canceled"}:
        return False
    response = str(task.get("final_response") or task.get("error") or "Black John concluiu sem resposta final.")
    allow_full_history = WHATSAPP_EXACT_ORDER_HISTORY_MARKER in response
    title_status = "completed" if status in {"completed", "partial"} else "failed"
    if status in {"completed", "partial"}:
        response = _complete_standard_task_artifacts(config, message_id, pending, task, response)
    parts = _whatsapp_response_parts(
        response,
        _whatsapp_result_title(pending.get("request_text") or task.get("prompt"), title_status),
    )
    _deliver_standard_task_result(config, message_id, pending, task_id, status, parts, allow_full_history)
    _whatsapp_update_query_context_from_task(state, pending, task)
    _remove_pending(state, message_id)
    return True

_COMPONENT_FUNCTIONS = frozenset((
    '_complete_dual_worker_pending',
    '_complete_pending'
))
_IMPLEMENTATIONS = {
    '_complete_dual_worker_pending': _complete_dual_worker_pending,
    '_complete_pending': _complete_pending
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
