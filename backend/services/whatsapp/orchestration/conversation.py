"""Extracted WhatsApp bridge component: conversation."""

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


def _whatsapp_is_job_status_probe(text: str) -> bool:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    if not normalized:
        return False
    if re.fullmatch(r"[?.!]+", normalized):
        return True
    return bool(
        len(normalized) <= 120
        and re.fullmatch(
            r"(?:e ai|e dai|terminou|acabou|concluiu|alguma novidade|como estamos|como esta|ja terminou|deu certo)[?.! ]*",
            normalized,
        )
    )

def _whatsapp_is_task_complement(text: str) -> bool:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").strip().lower()
    if not normalized or len(normalized) > 600:
        return False
    if re.match(
        r"^(na verdade|corrija|corrige|adicione|inclua|considere|use |troque|mude|refaca|quero dizer|quando eu disse|"
        r"complementando|mais um detalhe|a loja e|o sku e|o pedido e|e tambem|tambem |sobre isso|nesse caso|"
        r"e so\b|so\b|somente\b|apenas\b|continue\b|pode continuar\b|sem\b|nao inclua\b|nao precisa\b)",
        normalized,
    ):
        return True
    return bool(
        len(normalized) <= 220
        and re.search(r"\b(isso|isto|essa|esse|dessa|desse|nela|nele|anterior|mesmo|mesma|mesma loja|mesmo pedido|mais detalhes?)\b", normalized)
    )

def _dual_conversation_record(state: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    with BRIDGE_STATE_LOCK:
        conversations = state.get("dual_agent_conversations") if isinstance(state.get("dual_agent_conversations"), dict) else {}
        record = conversations.get(conversation_id) if isinstance(conversations.get(conversation_id), dict) else {}
        record = dict(record)
        record.setdefault("conversation_id", conversation_id)
        record.setdefault("conversation_queue_key", f"{conversation_id}:conversation")
        record.setdefault("worker_queue_key", f"{conversation_id}:worker")
        conversations[conversation_id] = record
        state["dual_agent_conversations"] = conversations
        return record

def _dual_append_conversation_turn(
    record: dict[str, Any],
    *,
    role: str,
    text: Any,
    event_type: str = "",
) -> dict[str, Any]:
    """Persist a small, PII-minimized dialogue window independent of Codex threads."""

    content = re.sub(r"\s+", " ", str(text or "").replace("\x00", " ")).strip()[:1200]
    normalized_role = "assistant" if str(role or "").strip().lower() == "assistant" else "user"
    if not content:
        return record
    turns = [
        {
            "role": "assistant" if str(item.get("role") or "") == "assistant" else "user",
            "text": str(item.get("text") or "").strip()[:1200],
            "event_type": str(item.get("event_type") or "")[:40],
            "at": str(item.get("at") or "")[:40],
        }
        for item in list(record.get("recent_turns") or [])[-15:]
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if turns and turns[-1]["role"] == normalized_role and turns[-1]["text"] == content:
        return record
    turns.append({"role": normalized_role, "text": content, "event_type": str(event_type or "")[:40], "at": _now()})
    record["recent_turns"] = turns[-16:]
    return record

def _dual_recent_conversation_context(
    record: dict[str, Any],
    *,
    current_user_message: str = "",
) -> list[dict[str, str]]:
    turns = [
        {"role": str(item.get("role") or "user"), "text": str(item.get("text") or "").strip()[:900]}
        for item in list(record.get("recent_turns") or [])[-12:]
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    current = re.sub(r"\s+", " ", str(current_user_message or "")).strip()[:900]
    if turns and current and turns[-1]["role"] == "user" and turns[-1]["text"] == current:
        turns.pop()
    return turns[-10:]

def _dual_remember_conversation_turn(
    state: dict[str, Any],
    conversation_id: str,
    *,
    role: str,
    text: Any,
    event_type: str = "",
) -> None:
    if not conversation_id or not str(text or "").strip():
        return
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        _dual_append_conversation_turn(record, role=role, text=text, event_type=event_type)
        _save_dual_conversation_record(state, conversation_id, record)

def _save_dual_conversation_record(state: dict[str, Any], conversation_id: str, record: dict[str, Any]) -> None:
    with DUAL_AGENT_STATE_LOCK, BRIDGE_STATE_LOCK:
        conversations = state.get("dual_agent_conversations") if isinstance(state.get("dual_agent_conversations"), dict) else {}
        value = dict(record or {})
        value["conversation_id"] = conversation_id
        value["updated_at"] = _now()
        conversations[conversation_id] = value
        state["dual_agent_conversations"] = conversations
        _save_state(state)

def _dual_active_job_snapshot(
    state: dict[str, Any],
    conversation_id: str,
    *,
    exclude_message_id: str = "",
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    message_id, pending, task = _active_pending_for_conversation(
        state,
        conversation_id,
        exclude_message_id=exclude_message_id,
    )
    if task and str(task.get("status") or "") != "running":
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        for candidate_message_id, candidate_pending in pending_messages.items():
            if not isinstance(candidate_pending, dict) or str(candidate_pending.get("kind") or "") != "dual_worker":
                continue
            if str(candidate_pending.get("conversation_id") or "") != str(conversation_id or ""):
                continue
            candidate_task_id = str(candidate_pending.get("task_id") or "")
            candidate_task = codex_console._codex_load_task(candidate_task_id) if candidate_task_id else None
            if isinstance(candidate_task, dict) and str(candidate_task.get("status") or "") == "running":
                message_id, pending, task = str(candidate_message_id), candidate_pending, candidate_task
                break
    if not task:
        return "", {}, {}, {}
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
    snapshot = {
        "job_id": str(pending.get("job_group_id") or task.get("task_id") or ""),
        "job_title": str(pending.get("job_title") or pending.get("request_text") or "")[:500],
        "request_text": str(pending.get("request_text") or "")[:1200],
        "status": str(task.get("status") or ""),
        "verified_partial": verified_partial[:10],
        "subtasks_total": len(list(pending.get("subtasks") or [])) or 1,
        "subtasks_completed": len(list(pending.get("collected_task_ids") or [])),
    }
    return message_id, pending, task, snapshot

def _run_conversation_agent(
    config: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
    *,
    event_type: str,
    user_message: str,
    active_job: Optional[dict[str, Any]] = None,
    worker_result: Optional[dict[str, Any]] = None,
    ai_behavior: str = "",
    tick_index: int = 0,
) -> dict[str, Any]:
    settings = _whatsapp_dual_agent_settings(config)
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        thread_id = str(record.get("thread_id") or "")
        conversation_context = _dual_recent_conversation_context(
            record,
            current_user_message=user_message if event_type == "user_message" else "",
        )
    def invoke(candidate_thread_id: str) -> dict[str, Any]:
        value = codex_whatsapp_agents.CONVERSATION_RUNTIME.run(
            thread_id=candidate_thread_id,
            model=settings["conversation_agent_model"],
            reasoning_effort=settings["conversation_agent_reasoning"],
            speed=settings["conversation_agent_speed"],
            service_tier=settings["conversation_agent_service_tier"],
            event_type=event_type,
            user_message=user_message,
            active_job=active_job,
            worker_result=worker_result,
            conversation_context=conversation_context,
            ai_behavior=ai_behavior,
            tick_index=tick_index,
        )
        return value if isinstance(value, dict) else {}

    def valid(value: dict[str, Any]) -> bool:
        action = str(value.get("action") or "").strip()
        if action not in {"reply", "request_information", "delegate", "queue", "steer", "cancel_job", "wait"}:
            return False
        if action in {"reply", "request_information", "steer", "cancel_job"}:
            return bool(str(value.get("reply_text") or "").strip())
        if action in {"delegate", "queue"}:
            return bool(str(value.get("job_prompt") or user_message or "").strip())
        return True

    first_error = ""
    try:
        decision = invoke(thread_id)
    except Exception as exc:
        decision = {}
        first_error = str(exc)[:500]
    if not valid(decision):
        try:
            # Uma unica nova tentativa em thread limpa evita carregar uma
            # resposta estruturada vazia ou invalida para a rodada seguinte.
            decision = invoke("")
        except Exception as exc:
            decision = {}
            first_error = first_error or str(exc)[:500]
    if not valid(decision):
        RUNTIME_STATE["conversation_fallback_last_error"] = first_error or "conversation_agent_empty_or_invalid_reply"
        decision = {
            "action": "reply",
            "reply_text": (
                "Nao consegui identificar com seguranca o que voce quer consultar. "
                "Pode reformular em uma frase, dizendo apenas o resultado que precisa?"
            ),
            "thread_id": "",
            "fallback_terminal": True,
        }
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record.update(
            {
                "thread_id": str(decision.get("thread_id") or record.get("thread_id") or "")[:200],
                "requested_model": str(decision.get("requested_model") or "")[:100],
                "effective_model": str(decision.get("effective_model") or "")[:100],
                "reasoning_effort": str(decision.get("reasoning_effort") or "")[:20],
                "speed": str(decision.get("speed") or "")[:20],
                "service_tier": str(decision.get("service_tier") or "")[:40],
                "last_event_type": event_type,
                "last_activity_at_epoch": time.time(),
                "last_conversation_at": _now(),
            }
        )
        reply_text = str(decision.get("reply_text") or "").strip()
        if reply_text:
            _dual_append_conversation_turn(record, role="assistant", text=reply_text, event_type=event_type)
        _save_dual_conversation_record(state, conversation_id, record)
    return decision

def _deterministic_direct_query_plan(
    request_text: str,
    query_policy: dict[str, Any],
    permissions: Any,
) -> dict[str, Any]:
    """Return a one-source read-only plan that can bypass both planning LLMs."""

    if not query_policy:
        return {}
    catalog = _function_manager_catalog(permissions)
    stock_request = bool(
        "estoque" in list(query_policy.get("domains") or [])
        or re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", _whatsapp_text_key(request_text))
    )
    plan = _function_manager_enforce_plan(
        {"tool_calls": [], "requires_sol": False, "requires_web": False},
        request_text=request_text,
        query_policy=query_policy,
        catalog=catalog,
        max_calls=3 if stock_request else 1,
    )
    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)]
    stock_chain = [str(item.get("tool_id") or "") for item in calls]
    valid_stock_chain = bool(
        stock_request
        and stock_chain
        and stock_chain == [
            tool_id
            for tool_id in ("bling_stock_balances", "mercado_livre_listing", "stock_data")
            if tool_id in stock_chain
        ]
    )
    if (len(calls) != 1 and not valid_stock_chain) or plan.get("requires_sol") or plan.get("requires_web"):
        return {}
    return plan

def _dual_delegate_query_policy(
    request_text: str,
    session: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    direct = _whatsapp_query_policy(request_text, session)
    store_scoped = _whatsapp_store_scoped_request(request_text)
    # O contexto de loja so pode ser herdado por uma tarefa que continue sendo
    # comercial. Frases naturais do Luna como "agora pesquise a previsao do
    # tempo" nao podem reaproveitar a ultima loja consultada.
    if not direct and not store_scoped:
        return {}
    inherited = _whatsapp_inherit_query_store_context(request_text, direct, state, conversation_id, session)
    policy = direct or inherited
    if not policy and store_scoped:
        policy = _whatsapp_store_scope_policy(request_text, session)
    return policy if isinstance(policy, dict) else {}

def _record_dual_user_message(state: dict[str, Any], conversation_id: str, request_text: str) -> None:
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record["last_inbound_at"] = _now()
        record["last_inbound_at_epoch"] = time.time()
        _dual_append_conversation_turn(record, role="user", text=request_text, event_type="user_message")
        _save_dual_conversation_record(state, conversation_id, record)

def _dual_initial_decision(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    active_task: dict[str, Any],
    active_snapshot: dict[str, Any],
    phone_ai_behavior: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    query_policy: dict[str, Any] = {}
    deterministic_plan: dict[str, Any] = {}
    if not active_task:
        query_policy = _dual_delegate_query_policy(request_text, session, state, conversation_id)
        missing_store = bool(
            query_policy.get("store_required")
            and query_policy.get("store_mode") != "all"
            and len(query_policy.get("store_matches") or []) != 1
        )
        if missing_store:
            delivered = _whatsapp_send_store_selection(
                config, state, message, session, conversation_id, request_text,
                list(query_policy.get("authorized_stores") or []), allow_all=True,
            )
            if delivered:
                return {"action": "selection_sent"}, query_policy, deterministic_plan
            raise RuntimeError("store_selection_delivery_failed")
        deterministic_plan = _deterministic_direct_query_plan(request_text, query_policy, session.get("permissions"))
    if deterministic_plan:
        tool_call = (deterministic_plan.get("tool_calls") or [{}])[0]
        decision = {
            "action": "delegate",
            "reply_text": "Vou consultar os dados confirmados e retorno assim que a fonte responder.",
            "job_prompt": request_text,
            "job_title": str(tool_call.get("reason") or "Consulta direta")[:180],
            "subtasks": [],
            "requires_web": False,
            "thread_id": "",
            "deterministic_route": True,
        }
    else:
        decision = _run_conversation_agent(
            config, state, conversation_id,
            event_type="user_message", user_message=request_text,
            active_job=active_snapshot, ai_behavior=phone_ai_behavior,
        )
    return decision, query_policy, deterministic_plan

def _normalized_dual_action(
    decision: dict[str, Any],
    active_task: dict[str, Any],
    request_text: str,
) -> str:
    action = str(decision.get("action") or "")
    if not active_task:
        return action
    if _whatsapp_is_job_status_probe(request_text):
        return "reply"
    related_job_id = str(decision.get("related_job_id") or "").strip()
    active_task_id = str(active_task.get("task_id") or "").strip()
    if action in {"delegate", "queue"} and (
        _whatsapp_is_task_complement(request_text) or (related_job_id and related_job_id == active_task_id)
    ):
        return "steer"
    if action == "delegate":
        return "queue"
    return action

def _cancel_dual_active_job(
    state: dict[str, Any],
    session: dict[str, Any],
    active_message_id: str,
    active_pending: dict[str, Any],
) -> None:
    for task_id in _pending_task_ids(active_pending):
        codex_console.codex_cancelar_tarefa_para_sessao(task_id, session, cancel_source="whatsapp_conversation_agent")
        codex_console._codex_update_task(task_id, handoff_status="canceled_by_conversation_agent", delivery_state="canceled")
    if active_message_id:
        _remove_pending(state, active_message_id, status="canceled", reason="cancelado_pelo_usuario")

def _steer_dual_active_job(
    config: dict[str, Any],
    state: dict[str, Any],
    session: dict[str, Any],
    decision: dict[str, Any],
    active_message_id: str,
    active_pending: dict[str, Any],
    active_task: dict[str, Any],
    request_text: str,
    message_id: str,
    subject: str,
    phone: str,
    reply_text: str,
) -> bool:
    active_task_id = str(active_task.get("task_id") or "").strip()
    prompt = str(decision.get("job_prompt") or request_text)
    if str(active_pending.get("kind") or "") == "dual_function_manager" and active_message_id:
        _resume_dual_pending_with_message(config, state, active_message_id, active_pending, prompt)
        _post_message_result(config, message_id, {"status": "completed", "task_id": active_task_id, "response": reply_text})
        return True
    accepted = False
    for task_id in _pending_task_ids(active_pending):
        task = active_task if task_id == active_task_id else (codex_console._codex_load_task(task_id) or {})
        if str(task.get("status") or "") not in {"queued", "running"}:
            continue
        steer = codex_console.codex_complementar_tarefa_para_sessao(
            task_id, prompt, session,
            request_id=f"{message_id}:{task_id}", subject_id=subject, wa_id=phone,
        )
        accepted = steer.get("accepted") is True or accepted
    if accepted:
        _post_message_result(config, message_id, {"status": "completed", "task_id": active_task_id, "response": reply_text})
    return accepted

def _handle_dual_control_action(
    config: dict[str, Any],
    state: dict[str, Any],
    session: dict[str, Any],
    decision: dict[str, Any],
    action: str,
    active_message_id: str,
    active_pending: dict[str, Any],
    active_task: dict[str, Any],
    request_text: str,
    message_id: str,
    subject: str,
    phone: str,
    reply_text: str,
) -> tuple[bool, str]:
    active_task_id = str(active_task.get("task_id") or "").strip() if active_task else ""
    if action == "cancel_job":
        if active_task:
            _cancel_dual_active_job(state, session, active_message_id, active_pending)
        _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        return True, action
    active_job_state = str(active_pending.get("job_state") or "") if active_pending else ""
    should_resume = bool(
        active_pending and not _whatsapp_is_job_status_probe(request_text)
        and (active_job_state == "awaiting_input" or (
            active_job_state in {"waiting_retry", "retry_starting"} and _whatsapp_is_retry_command(request_text)
        ))
    )
    if should_resume and active_message_id and _resume_dual_pending_with_message(
        config, state, active_message_id, active_pending, request_text,
    ):
        _post_message_result(config, message_id, {"status": "completed", "task_id": active_task_id, "response": reply_text})
        return True, action
    if action == "steer" and active_task:
        if _steer_dual_active_job(
            config, state, session, decision, active_message_id, active_pending,
            active_task, request_text, message_id, subject, phone, reply_text,
        ):
            return True, action
        return False, "queue"
    if action == "steer":
        return False, "delegate"
    if action in {"reply", "request_information"}:
        _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        return True, action
    if action not in {"delegate", "queue"}:
        raise RuntimeError("conversation_agent_unhandled_action")
    return False, action

def _dual_resolve_job_policy(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    job_prompt: str,
    precomputed: dict[str, Any],
) -> Optional[dict[str, Any]]:
    policy = precomputed or _dual_delegate_query_policy(request_text, session, state, conversation_id)
    if not policy:
        policy = _dual_delegate_query_policy(job_prompt, session, state, conversation_id)
    missing_store = bool(
        policy.get("store_required") and policy.get("store_mode") != "all"
        and len(policy.get("store_matches") or []) != 1
    )
    if not missing_store:
        return policy
    if _whatsapp_send_store_selection(
        config, state, message, session, conversation_id, request_text,
        list(policy.get("authorized_stores") or []), allow_all=True,
    ):
        return None
    raise RuntimeError("store_selection_delivery_failed")

def _queue_dual_function_manager(
    config: dict[str, Any],
    state: dict[str, Any],
    session: dict[str, Any],
    decision: dict[str, Any],
    deterministic_plan: dict[str, Any],
    query_policy: dict[str, Any],
    message_id: str,
    subject: str,
    phone: str,
    conversation_id: str,
    request_text: str,
    job_prompt: str,
    job_title: str,
    reply_text: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
) -> bool:
    settings = _whatsapp_dual_agent_settings(config)
    if not settings.get("function_manager_enabled") or not settings.get("function_manager_required_before_sol"):
        return False
    job_group_id = f"wa-{uuid.uuid4().hex[:20]}"
    now_epoch = time.time()
    pending = {
        "task_id": "", "kind": "dual_function_manager", "conversation_id": conversation_id,
        "conversation_agent_thread_id": str(decision.get("thread_id") or ""), "function_manager_thread_id": "",
        "parent_job_id": job_group_id, "job_group_id": job_group_id, "job_title": job_title,
        "subject_id": subject, "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(), "request_text": request_text,
        "job_prompt": job_prompt, "query_policy": query_policy, "manager_query_policy": query_policy,
        "deterministic_plan": deterministic_plan, "phone_ai_behavior": phone_ai_behavior,
        "media": media or {}, "transcription": transcription or {},
        "screen_context": _mobile_screen_context(message_id, subject, media, transcription, query_policy),
        "session_permissions": {str(key): value is True for key, value in dict(session.get("permissions") or {}).items() if str(key or "").strip()},
        "session_is_full": bool(session.get("is_full")), "created_at": _now(), "created_at_epoch": now_epoch,
        "job_state": "manager_queued", "manager_state": "queued", "manager_retry_count": 0,
        "manager_revision": 0, "manager_next_retry_at_epoch": 0, "retry_policy": "bounded",
        "sol_subtasks": list(decision.get("subtasks") or [])[: settings["max_subtasks_per_job"]],
        "verified_facts": [], "verified_sources": [], "last_conversation_at": _now(),
        "last_conversation_at_epoch": now_epoch, "tick_index": 0, "wait_notice_count": 0,
        "awaiting_notified": True, "handoff_status": "manager_queued", "delivery_state": "initial_reply_sent",
        "wa_id": phone,
    }
    _save_pending(state, message_id, pending)
    _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
    _post_message_result(
        config, message_id,
        {"status": "completed", "task_id": "", "job_group_id": job_group_id, "subtask_count": 0, "response": reply_text},
    )
    _submit_function_manager_job(config, state, message_id)
    return True

def _create_dual_subtask(
    config: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    subtask: dict[str, Any],
    index: int,
    total: int,
    job_group_id: str,
    job_prompt: str,
    job_title: str,
    message_id: str,
    subject: str,
    phone: str,
    conversation_id: str,
    request_text: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
    query_policy: dict[str, Any],
) -> dict[str, Any]:
    subtask_id = f"s{index}-{uuid.uuid4().hex[:8]}"
    subtask_prompt = str(subtask.get("prompt") or job_prompt).strip()
    item = {
        "subtask_id": subtask_id, "logical_subtask_id": subtask_id,
        "title": str(subtask.get("title") or job_title).strip()[:180], "prompt": subtask_prompt[:12000],
        "requires_web": bool(subtask.get("requires_web")), "reasoning_effort": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
        "task_id": "", "current_attempt": 1, "attempt_task_ids": [], "retry_count": 0,
        "next_retry_at_epoch": 0, "state": "running",
    }
    try:
        task = _create_dual_worker_task(
            config, message=message, message_id=message_id, subject=subject, phone=phone, session=session,
            conversation_id=conversation_id, request_text=request_text, job_prompt=subtask_prompt,
            job_title=item["title"], media=media, transcription=transcription,
            phone_ai_behavior=phone_ai_behavior, query_policy=query_policy, job_group_id=job_group_id,
            subtask_id=subtask_id, logical_subtask_id=subtask_id, attempt=1,
            subtask_index=index, subtask_total=total, requires_web=bool(subtask.get("requires_web")),
            reasoning_effort=WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
        )
        task_id = str(task.get("task_id") or "")
        item.update({"task_id": task_id, "attempt_task_ids": [task_id] if task_id else []})
    except Exception as exc:
        reason = str(exc)[:1000]
        error_class, retryable = _dual_retry_classification(reason)
        delay = _dual_retry_delay_seconds(1, reason, f"{job_group_id}:{subtask_id}:create") if retryable else 0
        item.update({
            "state": "waiting_retry" if retryable else "partial", "retry_count": 1,
            "retry_reason": reason, "next_retry_at_epoch": time.time() + delay if retryable else 0,
            "next_retry_delay_seconds": delay, "auth_retry": False, "retryable": retryable, "error_class": error_class,
        })
    return item

def _build_dual_worker_pending(
    session: dict[str, Any],
    decision: dict[str, Any],
    created: list[dict[str, Any]],
    job_group_id: str,
    conversation_id: str,
    subject: str,
    phone: str,
    request_text: str,
    job_prompt: str,
    job_title: str,
    query_policy: dict[str, Any],
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
) -> tuple[dict[str, Any], bool, str]:
    first_task_id = next((str(item.get("task_id") or "") for item in created if item.get("task_id")), "")
    is_group = len(created) > 1 or not first_task_id
    terminal = bool(created) and all(str(item.get("state") or "") in {"partial", "canceled"} for item in created)
    initial_auth_retry = any(item.get("auth_retry") is True for item in created)
    now_epoch = time.time()
    first = created[0] if created else {}
    pending = {
        "task_id": first_task_id, "kind": "dual_job_group" if is_group else "dual_worker",
        "conversation_id": conversation_id, "conversation_agent_thread_id": str(decision.get("thread_id") or ""),
        "parent_job_id": job_group_id, "job_group_id": job_group_id, "subtasks": created if is_group else [],
        "group_results": {}, "collected_task_ids": [], "delivered_task_ids": [], "results_revision": 0,
        "job_title": job_title, "subject_id": subject, "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(), "request_text": request_text,
        "job_prompt": job_prompt, "query_policy": query_policy, "phone_ai_behavior": phone_ai_behavior,
        "media": media or {}, "transcription": transcription or {},
        "session_permissions": {str(key): value is True for key, value in dict(session.get("permissions") or {}).items() if str(key or "").strip()},
        "session_is_full": bool(session.get("is_full")), "created_at": _now(), "created_at_epoch": now_epoch,
        "job_state": "running" if first_task_id else ("partial" if terminal else "waiting_retry"), "retry_policy": "bounded",
        "retry_count": int(first.get("retry_count") or 0) if not is_group else 0,
        "next_retry_at_epoch": float(first.get("next_retry_at_epoch") or 0) if not is_group else 0,
        "retry_reason": str(first.get("retry_reason") or "") if not is_group else "",
        "attempt_task_ids": list(first.get("attempt_task_ids") or []) if not is_group else [],
        "current_attempt": int(first.get("current_attempt") or 1) if not is_group else 1,
        "subtask_id": str(first.get("subtask_id") or "main") if not is_group else "",
        "logical_subtask_id": str(first.get("logical_subtask_id") or "main") if not is_group else "",
        "prompt": str(first.get("prompt") or job_prompt)[:12000] if not is_group else "",
        "title": str(first.get("title") or job_title)[:180] if not is_group else "",
        "requires_web": bool(first.get("requires_web", True)) if not is_group else True,
        "reasoning_effort": WHATSAPP_TASK_AGENT_REASONING_DEFAULT, "verified_facts": [], "verified_sources": [],
        "auth_retry_active": initial_auth_retry, "auth_notice_pending": initial_auth_retry, "auth_notice_sent": False,
        "last_conversation_at": _now(), "last_conversation_at_epoch": now_epoch, "tick_index": 0,
        "wait_notice_count": 0, "awaiting_notified": True, "handoff_status": "worker_running",
        "delivery_state": "initial_reply_sent", "wa_id": phone,
    }
    return pending, terminal, first_task_id

def _queue_dual_workers(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    decision: dict[str, Any],
    query_policy: dict[str, Any],
    message_id: str,
    subject: str,
    phone: str,
    conversation_id: str,
    request_text: str,
    job_prompt: str,
    job_title: str,
    reply_text: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
) -> bool:
    settings = _whatsapp_dual_agent_settings(config)
    proposed = list(decision.get("subtasks") or [])[: settings["max_subtasks_per_job"]]
    if len(proposed) <= 1:
        proposed = [proposed[0] if proposed else {
            "title": job_title, "prompt": job_prompt,
            "requires_web": bool(decision.get("requires_web")), "reasoning_effort": settings["task_agent_reasoning"],
        }]
    job_group_id = f"wa-{uuid.uuid4().hex[:20]}"
    created = [
        _create_dual_subtask(
            config, message, session, subtask, index, len(proposed), job_group_id,
            job_prompt, job_title, message_id, subject, phone, conversation_id,
            request_text, media, transcription, phone_ai_behavior, query_policy,
        )
        for index, subtask in enumerate(proposed, start=1)
    ]
    pending, creation_terminal, first_task_id = _build_dual_worker_pending(
        session, decision, created, job_group_id, conversation_id, subject, phone,
        request_text, job_prompt, job_title, query_policy, media, transcription, phone_ai_behavior,
    )
    _save_pending(state, message_id, pending)
    _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
    _post_message_result(
        config, message_id,
        {"status": "completed", "task_id": first_task_id, "job_group_id": job_group_id,
         "subtask_count": len(created), "response": reply_text},
    )
    if creation_terminal:
        first = created[0] if created else {}
        _terminate_pending_partial(
            config, state, message_id, pending,
            reason=str(first.get("retry_reason") or "nao_foi_possivel_iniciar_a_consulta"),
        )
    return True

def _process_dual_codex_message(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    *,
    session: dict[str, Any],
    conversation_id: str,
    message_id: str,
    subject: str,
    phone: str,
    request_text: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
) -> bool:
    active_message_id, active_pending, active_task, active_snapshot = _dual_active_job_snapshot(
        state, conversation_id, exclude_message_id=message_id,
    )
    _record_dual_user_message(state, conversation_id, request_text)
    decision, precomputed_policy, deterministic_plan = _dual_initial_decision(
        config, state, message, session, conversation_id, request_text,
        active_task, active_snapshot, phone_ai_behavior,
    )
    if decision.get("action") == "selection_sent":
        return True
    reply_text = str(decision.get("reply_text") or "").strip()
    action = _normalized_dual_action(decision, active_task, request_text)
    handled, action = _handle_dual_control_action(
        config, state, session, decision, action, active_message_id, active_pending,
        active_task, request_text, message_id, subject, phone, reply_text,
    )
    if handled:
        return True
    job_prompt = str(decision.get("job_prompt") or request_text).strip()
    job_title = str(decision.get("job_title") or request_text).strip()[:180]
    query_policy = _dual_resolve_job_policy(
        config, state, message, session, conversation_id,
        request_text, job_prompt, precomputed_policy,
    )
    if query_policy is None:
        return True
    if _queue_dual_function_manager(
        config, state, session, decision, deterministic_plan, query_policy,
        message_id, subject, phone, conversation_id, request_text, job_prompt,
        job_title, reply_text, media, transcription, phone_ai_behavior,
    ):
        return True
    return _queue_dual_workers(
        config, state, message, session, decision, query_policy,
        message_id, subject, phone, conversation_id, request_text, job_prompt,
        job_title, reply_text, media, transcription, phone_ai_behavior,
    )

_COMPONENT_FUNCTIONS = frozenset((
    '_whatsapp_is_job_status_probe',
    '_whatsapp_is_task_complement',
    '_dual_conversation_record',
    '_dual_append_conversation_turn',
    '_dual_recent_conversation_context',
    '_dual_remember_conversation_turn',
    '_save_dual_conversation_record',
    '_dual_active_job_snapshot',
    '_run_conversation_agent',
    '_deterministic_direct_query_plan',
    '_dual_delegate_query_policy',
    '_process_dual_codex_message'
))
_IMPLEMENTATIONS = {
    '_whatsapp_is_job_status_probe': _whatsapp_is_job_status_probe,
    '_whatsapp_is_task_complement': _whatsapp_is_task_complement,
    '_dual_conversation_record': _dual_conversation_record,
    '_dual_append_conversation_turn': _dual_append_conversation_turn,
    '_dual_recent_conversation_context': _dual_recent_conversation_context,
    '_dual_remember_conversation_turn': _dual_remember_conversation_turn,
    '_save_dual_conversation_record': _save_dual_conversation_record,
    '_dual_active_job_snapshot': _dual_active_job_snapshot,
    '_run_conversation_agent': _run_conversation_agent,
    '_deterministic_direct_query_plan': _deterministic_direct_query_plan,
    '_dual_delegate_query_policy': _dual_delegate_query_policy,
    '_process_dual_codex_message': _process_dual_codex_message
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
