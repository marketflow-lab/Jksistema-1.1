"""Extracted WhatsApp bridge component: api_endpoints."""

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


def whatsapp_bridge_update_config(
    payload: WhatsappBridgeConfigRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    if payload.worker_url is not None:
        config["worker_url"] = _normalize_worker_url(payload.worker_url)
    if payload.bridge_token is not None and str(payload.bridge_token).strip():
        config["bridge_token"] = str(payload.bridge_token).strip()
    if payload.business_phone is not None:
        config["business_phone"] = str(payload.business_phone).strip()[:40]
    if payload.ai_model is not None:
        config["ai_model"] = _normalize_ai_model(payload.ai_model)
    if payload.codex_reasoning_effort is not None:
        config["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(payload.codex_reasoning_effort)
    if payload.codex_reasoning_policy is not None:
        config["codex_reasoning_policy"] = _normalize_codex_reasoning_policy(payload.codex_reasoning_policy)
    if payload.codex_reasoning_max is not None:
        config["codex_reasoning_max"] = _normalize_codex_reasoning_effort(payload.codex_reasoning_max)
    if payload.progress_interval_seconds is not None:
        config["progress_interval_seconds"] = _normalize_progress_interval(payload.progress_interval_seconds)
    if payload.progress_explain_wait is not None:
        config["progress_explain_wait"] = bool(payload.progress_explain_wait)
    if payload.agent_architecture is not None:
        config["agent_architecture"] = _normalize_agent_architecture(payload.agent_architecture)
    if payload.conversation_agent_model is not None:
        config["conversation_agent_model"] = _normalize_codex_agent_model(
            payload.conversation_agent_model, WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT
        )
    if payload.conversation_agent_reasoning is not None:
        config["conversation_agent_reasoning"] = _normalize_codex_reasoning_effort(payload.conversation_agent_reasoning)
    if payload.task_agent_model is not None:
        config["task_agent_model"] = _normalize_codex_agent_model(
            payload.task_agent_model, WHATSAPP_TASK_AGENT_MODEL_DEFAULT
        )
        config["ai_model"] = f"codex:{config['task_agent_model']}"
    if payload.task_agent_reasoning is not None:
        config["task_agent_reasoning"] = WHATSAPP_TASK_AGENT_REASONING_DEFAULT
    if payload.conversation_interval_seconds is not None:
        config["conversation_interval_seconds"] = _normalize_conversation_interval(payload.conversation_interval_seconds)
    if payload.wait_message_after_seconds is not None:
        config["wait_message_after_seconds"] = _normalize_capacity(
            payload.wait_message_after_seconds, WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT, 5, 60
        )
    if payload.wait_message_repeat_seconds is not None:
        config["wait_message_repeat_seconds"] = _normalize_capacity(
            payload.wait_message_repeat_seconds, WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT, 15, 180
        )
    if payload.wait_message_steady_seconds is not None:
        config["wait_message_steady_seconds"] = _normalize_capacity(
            payload.wait_message_steady_seconds, WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT, 30, 300
        )
    if payload.partial_delivery_debounce_seconds is not None:
        config["partial_delivery_debounce_seconds"] = _normalize_capacity(
            payload.partial_delivery_debounce_seconds, WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT, 1, 10
        )
    if payload.job_deadline_seconds is not None:
        config["job_deadline_seconds"] = _normalize_capacity(
            payload.job_deadline_seconds,
            WHATSAPP_JOB_DEADLINE_DEFAULT,
            30,
            WHATSAPP_REPORT_DEADLINE_SECONDS,
        )
    config["retry_policy"] = "bounded"
    if payload.max_subtasks_per_job is not None:
        config["max_subtasks_per_job"] = _normalize_capacity(
            payload.max_subtasks_per_job, WHATSAPP_MAX_SUBTASKS_DEFAULT, 1, 6
        )
    if payload.progress_messages_enabled is not None:
        config["progress_messages_enabled"] = bool(payload.progress_messages_enabled)
    if payload.conversation_worker_count is not None:
        config["conversation_worker_count"] = _normalize_capacity(
            payload.conversation_worker_count, WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT, 1, 8
        )
    if payload.conversation_runtime_pool_size is not None:
        config["conversation_runtime_pool_size"] = _normalize_capacity(
            payload.conversation_runtime_pool_size,
            WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        )
    if payload.max_active_task_agents_global is not None:
        config["max_active_task_agents_global"] = _normalize_capacity(
            payload.max_active_task_agents_global,
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT,
            1,
            12,
        )
    if payload.preserve_order_per_phone is False:
        raise HTTPException(status_code=400, detail="A ordem FIFO por telefone deve permanecer habilitada.")
    config["preserve_order_per_phone"] = True
    if payload.function_manager_enabled is not None:
        config["function_manager_enabled"] = bool(payload.function_manager_enabled)
    if payload.function_manager_required_before_sol is not None:
        config["function_manager_required_before_sol"] = bool(payload.function_manager_required_before_sol)
    if payload.function_manager_worker_count is not None:
        config["function_manager_worker_count"] = _normalize_capacity(
            payload.function_manager_worker_count,
            WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT,
            1,
            8,
        )
    if payload.function_manager_runtime_pool_size is not None:
        config["function_manager_runtime_pool_size"] = _normalize_capacity(
            payload.function_manager_runtime_pool_size,
            WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        )
    if config.get("agent_architecture") == "dual_codex":
        # No fluxo dual v8 nao existe atalho direto para o Sol.
        config["function_manager_enabled"] = True
        config["function_manager_required_before_sol"] = True
    if payload.voice_model is not None:
        config["voice_model"] = _normalize_voice_model(payload.voice_model, whatsapp_voice.VOICE_MODEL_DEFAULT)
    if payload.voice_transcription_model is not None:
        config["voice_transcription_model"] = _normalize_voice_model(
            payload.voice_transcription_model, whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT
        )
    if payload.voice_name is not None:
        config["voice_name"] = _normalize_voice_name(payload.voice_name)
    if payload.voice_language is not None and str(payload.voice_language or "").strip().lower() not in {"pt-br", "pt_br", "pt"}:
        raise HTTPException(status_code=400, detail="A primeira versao das ligacoes usa portugues brasileiro.")
    if payload.voice_max_call_minutes is not None:
        config["voice_max_call_minutes"] = _normalize_voice_int(payload.voice_max_call_minutes, 30, 5, 60)
    if payload.voice_silence_timeout_seconds is not None:
        config["voice_silence_timeout_seconds"] = _normalize_voice_int(payload.voice_silence_timeout_seconds, 90, 30, 300)
    if payload.voice_long_task_offer_seconds is not None:
        config["voice_long_task_offer_seconds"] = _normalize_voice_int(payload.voice_long_task_offer_seconds, 90, 30, 300)
    if payload.voice_max_concurrent_calls is not None:
        config["voice_max_concurrent_calls"] = _normalize_voice_int(payload.voice_max_concurrent_calls, 3, 1, 10)
    if payload.voice_progress_interval_seconds is not None:
        config["voice_progress_interval_seconds"] = _normalize_voice_int(payload.voice_progress_interval_seconds, 8, 8, 30)
    if payload.max_active_task_agents_per_conversation is not None:
        config["max_active_task_agents_per_conversation"] = _normalize_capacity(
            payload.max_active_task_agents_per_conversation,
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT,
            1,
            6,
        )
    if payload.orchestration_mode is not None and str(payload.orchestration_mode or "") != WHATSAPP_ORCHESTRATION_MODE_DEFAULT:
        raise HTTPException(status_code=400, detail="Modo de orquestracao do WhatsApp invalido.")
    if payload.active_task_policy is not None and str(payload.active_task_policy or "") != "steer_or_queue":
        raise HTTPException(status_code=400, detail="Politica de tarefa ativa invalida.")
    config["client_id"] = str(session.get("client_id") or "")
    config["username"] = str(session.get("username") or "").strip().lower()
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    if payload.enabled is not None:
        if payload.enabled:
            worker = _worker_health(config)
            whisper = _whisper_status()
            codex = codex_console._codex_status_payload()
            policy_valid = bool((worker.get("zero_cost") or {}).get("policy_valid"))
            meta_ready = bool((worker.get("meta") or {}).get("configured"))
            machine_bindings = [
                item
                for item in (worker.get("bindings") or [])
                if isinstance(item, dict)
                and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
            ]
            missing = []
            if not worker.get("success"):
                missing.append("Worker")
            if not policy_valid:
                missing.append("politica de custo zero")
            if not meta_ready:
                missing.append("Meta")
            if not machine_bindings:
                missing.append("numero pessoal vinculado")
            if not whisper.get("ready"):
                missing.append("Whisper Small")
            if _whatsapp_ai_settings(config)["provider"] == "codex" and not codex.get("ready"):
                missing.append("Joao")
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail="Ativacao bloqueada: " + ", ".join(missing) + " ainda nao esta pronto.",
                )
            config["enabled"] = True
            config.update({"pairing_pending": False, "pairing_expires_at": 0})
        else:
            config["enabled"] = False
            config.update({"pairing_pending": False, "pairing_expires_at": 0})
    config = _save_config(config)
    if config.get("enabled") and config.get("agent_architecture") == "dual_codex":
        settings = _whatsapp_dual_agent_settings(config)
        try:
            _configure_phone_dispatcher(settings["conversation_worker_count"])
            codex_console._codex_configure_dual_sol_limit(
                settings["max_active_task_agents_global"],
                settings["max_active_task_agents_per_conversation"],
            )
            codex_whatsapp_agents.CONVERSATION_RUNTIME.warm(
                settings["conversation_agent_model"],
                settings["task_agent_model"],
                pool_size=settings["conversation_runtime_pool_size"],
            )
            codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.warm(
                settings["conversation_agent_model"],
                settings["task_agent_model"],
                pool_size=settings["function_manager_runtime_pool_size"],
            )
            RUNTIME_STATE["dual_agent_last_error"] = ""
        except Exception as exc:
            RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]
            raise HTTPException(status_code=409, detail=f"Agentes Codex indisponiveis: {exc}")
    return _public_status(config)

def whatsapp_bridge_update_phone_settings(
    payload: WhatsappPhoneSettingsRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    subject_id = str(payload.subject_id or "").strip()
    username = str(payload.username or "").strip().lower()
    client_id = str(payload.client_id or "").strip()
    if not subject_id or not username or not client_id:
        raise HTTPException(status_code=400, detail="Telefone, usuario e cliente sao obrigatorios.")

    worker = _worker_health(config)
    binding = next(
        (
            item
            for item in (worker.get("bindings") or [])
            if isinstance(item, dict)
            and str(item.get("subject_id") or "").strip() == subject_id
            and str(item.get("username") or "").strip().lower() == username
            and str(item.get("client_id") or "").strip() == client_id
        ),
        None,
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="Numero vinculado nao encontrado para este usuario.")
    if str(binding.get("machine_id") or "") != str(config.get("machine_id") or ""):
        raise HTTPException(status_code=409, detail="Este numero esta vinculado em outra maquina. Configure-o na maquina correspondente.")

    previous = _phone_notification_settings(
        config,
        subject_id,
        client_id=client_id,
        username=username,
    )
    updated = _normalize_phone_notification_settings(
        {
            "label": payload.label,
            "send_ml_question_suggestions": payload.send_ml_question_suggestions,
            "send_weekly_report": payload.send_weekly_report,
            "send_monthly_report": payload.send_monthly_report,
            "ai_behavior": previous.get("ai_behavior") if payload.ai_behavior is None else payload.ai_behavior,
            "allow_voice_calls": payload.allow_voice_calls,
        }
    )
    updated.update({"subject_id": subject_id, "client_id": client_id, "username": username, "updated_at": _now()})
    try:
        _gateway_json(
            config,
            "POST",
            "/bridge/voice/phones/settings",
            {
                "subject_id": subject_id,
                "machine_id": str(config.get("machine_id") or ""),
                "allow_voice_calls": updated["allow_voice_calls"],
            },
            timeout=15,
        )
    except Exception as exc:
        # Instalações antigas ainda não possuem a rota de voz. O salvamento
        # continua compatível apenas quando voz nunca esteve autorizada.
        if updated["allow_voice_calls"] or previous.get("allow_voice_calls") is True:
            raise HTTPException(status_code=409, detail="O gateway de ligações não confirmou a permissão deste telefone.") from exc
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    settings_by_phone = config.get("phone_notification_settings")
    if not isinstance(settings_by_phone, dict):
        settings_by_phone = {}
    settings_by_phone[subject_id] = updated
    config["phone_notification_settings"] = settings_by_phone
    config = _save_config(config)

    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if not isinstance(deliveries, dict):
        deliveries = {}
    subject_deliveries = deliveries.get(subject_id)
    if not isinstance(subject_deliveries, dict):
        subject_deliveries = {}
    current = datetime.now()
    if updated["send_weekly_report"] and not previous["send_weekly_report"]:
        subject_deliveries["weekly"] = _whatsapp_week_key(current)
    if updated["send_monthly_report"] and not previous["send_monthly_report"]:
        subject_deliveries["monthly"] = _whatsapp_month_key(current)
    deliveries[subject_id] = subject_deliveries
    state["scheduled_report_deliveries"] = deliveries
    _save_state(state)
    return _public_status(config, worker)

def whatsapp_bridge_register_phone(
    payload: WhatsappPhoneRegistrationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    label = re.sub(r"\s+", " ", str(payload.name or "")).strip()[:60]
    if not label:
        raise HTTPException(status_code=400, detail="Informe um nome para identificar o telefone.")
    phone_number = _normalize_registered_phone(payload.phone_number)
    welcome_message = str(payload.welcome_message or "").replace("\r\n", "\n").strip()
    if len(welcome_message) > 1000:
        raise HTTPException(status_code=400, detail="A mensagem de boas-vindas deve ter no maximo 1000 caracteres.")
    if payload.send_welcome_message and not welcome_message:
        raise HTTPException(status_code=400, detail="Escreva a mensagem de boas-vindas antes de solicitar o envio.")
    config = _load_config()
    machine_id = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    config["machine_id"] = machine_id
    try:
        result = _gateway_json(
            config,
            "POST",
            "/bridge/bindings/register",
            {
                "phone_number": phone_number,
                "client_id": target_client_id,
                "username": target_username,
                "machine_id": machine_id,
            },
            timeout=15,
        )
    except RuntimeError as exc:
        error = str(exc)
        if "binding_limit_reached" in error:
            raise HTTPException(status_code=409, detail="Este usuario ja possui o limite de 3 telefones.") from exc
        if "binding_already_registered" in error:
            raise HTTPException(status_code=409, detail="Este telefone ja esta cadastrado para outro usuario.") from exc
        if "invalid_phone_number" in error:
            raise HTTPException(status_code=400, detail="Informe um numero de WhatsApp valido, com DDD.") from exc
        raise

    binding = result.get("binding") if isinstance(result.get("binding"), dict) else {}
    subject_id = str(result.get("subject_id") or binding.get("subject_id") or phone_number).strip()
    previous = _phone_notification_settings(
        config,
        subject_id,
        client_id=target_client_id,
        username=target_username,
    )
    updated = _normalize_phone_notification_settings(
        {
            "label": label,
            "send_ml_question_suggestions": payload.send_ml_question_suggestions,
            "send_weekly_report": payload.send_weekly_report,
            "send_monthly_report": payload.send_monthly_report,
            "allow_voice_calls": payload.allow_voice_calls,
        }
    )
    updated.update(
        {
            "subject_id": subject_id,
            "client_id": target_client_id,
            "username": target_username,
            "phone_number": phone_number,
            "updated_at": _now(),
        }
    )
    settings_by_phone = config.get("phone_notification_settings")
    if not isinstance(settings_by_phone, dict):
        settings_by_phone = {}
    settings_by_phone[subject_id] = updated
    try:
        _gateway_json(
            config,
            "POST",
            "/bridge/voice/phones/settings",
            {
                "subject_id": subject_id,
                "machine_id": machine_id,
                "allow_voice_calls": updated["allow_voice_calls"],
            },
            timeout=15,
        )
    except Exception as exc:
        if updated["allow_voice_calls"]:
            raise HTTPException(status_code=409, detail="O gateway de ligações não confirmou a permissão deste telefone.") from exc
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    config["phone_notification_settings"] = settings_by_phone
    config = _save_config(config)

    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if not isinstance(deliveries, dict):
        deliveries = {}
    subject_deliveries = deliveries.get(subject_id)
    if not isinstance(subject_deliveries, dict):
        subject_deliveries = {}
    current = datetime.now()
    if updated["send_weekly_report"] and not previous["send_weekly_report"]:
        subject_deliveries["weekly"] = _whatsapp_week_key(current)
    if updated["send_monthly_report"] and not previous["send_monthly_report"]:
        subject_deliveries["monthly"] = _whatsapp_month_key(current)
    deliveries[subject_id] = subject_deliveries
    state["scheduled_report_deliveries"] = deliveries
    _save_state(state)

    welcome_result: dict[str, Any] = {"requested": False, "success": True, "status": "not_requested"}
    if payload.send_welcome_message:
        try:
            sent = _gateway_json(
                config,
                "POST",
                "/bridge/welcome",
                {
                    "subject_id": subject_id,
                    "machine_id": machine_id,
                    "text": welcome_message,
                },
                timeout=20,
            )
            welcome_result = {"requested": True, **sent}
        except Exception as exc:
            welcome_result = {
                "requested": True,
                "success": False,
                "status": "failed",
                "error": str(exc)[:500],
            }

    worker = _worker_health(config)
    return {
        "success": True,
        "created": result.get("created") is not False,
        "subject_id": subject_id,
        "phone_number": phone_number,
        "welcome_message": welcome_result,
        "status": _public_status(config, worker),
    }

def whatsapp_bridge_send_adhoc_message(
    payload: WhatsappAdhocMessageRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    phone_number = _normalize_registered_phone(payload.phone_number)
    message = str(payload.message or "").replace("\r\n", "\n").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Escreva a mensagem que deseja enviar.")
    if len(message) > WHATSAPP_ADHOC_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail=f"A mensagem deve ter no maximo {WHATSAPP_ADHOC_MESSAGE_CHARS} caracteres.")
    config = _load_config()
    machine_id = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    delivery = _gateway_json(
        config,
        "POST",
        "/bridge/messages/send",
        {
            "phone_number": phone_number,
            "machine_id": machine_id,
            "text": message,
        },
        timeout=20,
    )
    return {
        "success": True,
        "phone_number": phone_number,
        "delivery": delivery,
    }

def whatsapp_bridge_test(request: Request, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    worker = _worker_health(config)
    return {"success": bool(worker.get("success")), "status": _public_status(config, worker)}

def whatsapp_bridge_voice_preflight(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    result = whatsapp_voice.VOICE_RUNTIME.preflight(config, sys.modules[__name__], check_openai=True)
    return {"success": True, "ready": result.pop("success", False), **result}

def whatsapp_bridge_voice_enable(
    payload: WhatsappVoiceToggleRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Confirme explicitamente a habilitacao das ligacoes.")
    config = _load_config()
    preflight = whatsapp_voice.VOICE_RUNTIME.preflight(config, sys.modules[__name__], check_openai=True)
    if preflight.get("success") is not True:
        raise HTTPException(
            status_code=409,
            detail="Ligacoes ainda nao estao prontas. Verifique chave OpenAI, webhook, projeto, SIP, migracao D1 e gateway.",
        )
    config["voice_enabled"] = True
    config = _save_config(config)
    whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    return {"success": True, "voice": _public_status(config).get("voice"), "preflight": preflight}

def whatsapp_bridge_voice_disable(
    payload: WhatsappVoiceToggleRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Confirme explicitamente a desativacao das ligacoes.")
    config = _load_config()
    config["voice_enabled"] = False
    config = _save_config(config)
    whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    return {"success": True, "voice": _public_status(config).get("voice")}

def whatsapp_bridge_voice_calls(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    result = _gateway_json(config, "GET", "/bridge/voice/status", timeout=15)
    client_id = str(session.get("client_id") or "")
    username = str(session.get("username") or "").strip().lower()
    calls = [
        item
        for item in list(result.get("calls") or [])
        if isinstance(item, dict)
        and str(item.get("client_id") or "") == client_id
        and str(item.get("username") or "").strip().lower() == username
    ]
    return {"success": True, "calls": calls, "counts": result.get("counts") or {}}

def whatsapp_bridge_pairing_code(
    payload: WhatsappPairingCodeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    result = _gateway_json(
        config,
        "POST",
        "/bridge/pairing-codes",
        {
            "code": code,
            "client_id": target_client_id,
            "username": target_username,
            "machine_id": config["machine_id"],
        },
        timeout=15,
    )
    config.update({
        "pairing_pending": True,
        "pairing_expires_at": int(result.get("expires_at") or (time.time() + 600)),
    })
    _save_config(config)
    return {
        "success": True,
        "code": code,
        "expires_at": result.get("expires_at"),
        "limit": int(result.get("limit") or 3),
        "active_bindings": int(result.get("active_bindings") or 0),
        "remaining_slots": int(result.get("remaining_slots") or 0),
        "username": target_username,
        "client_id": target_client_id,
        "instruction": f"Envie VINCULAR {code} para o numero empresarial.",
    }

def whatsapp_bridge_revoke(
    payload: WhatsappBindingRevokeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    subject_id = str(payload.subject_id or "").strip()
    result = _gateway_json(
        config,
        "POST",
        "/bridge/bindings/revoke",
        {
            "client_id": target_client_id,
            "username": target_username,
            "subject_id": subject_id,
            "revoke_all": bool(payload.revoke_all),
        },
        timeout=15,
    )
    if subject_id and subject_id == str(config.get("subject_id") or ""):
        config.update({"subject_id": "", "personal_phone": ""})
    settings_by_phone = config.get("phone_notification_settings")
    if isinstance(settings_by_phone, dict):
        if subject_id:
            settings_by_phone.pop(subject_id, None)
        elif payload.revoke_all:
            settings_by_phone = {
                key: value
                for key, value in settings_by_phone.items()
                if not (
                    isinstance(value, dict)
                    and str(value.get("username") or "").strip().lower() == target_username
                    and str(value.get("client_id") or "").strip() == target_client_id
                )
            }
        config["phone_notification_settings"] = settings_by_phone
    worker = _worker_health(config)
    machine_bindings = [
        item
        for item in (worker.get("bindings") or [])
        if isinstance(item, dict)
        and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
    ]
    if not machine_bindings:
        config.update({"subject_id": "", "personal_phone": "", "enabled": False})
    _save_config(config)
    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if isinstance(deliveries, dict):
        active_subjects = {
            str(item.get("subject_id") or "").strip()
            for item in (worker.get("bindings") or [])
            if isinstance(item, dict) and str(item.get("subject_id") or "").strip()
        }
        state["scheduled_report_deliveries"] = {
            key: value for key, value in deliveries.items() if key in active_subjects
        }
        _save_state(state)
    return {
        "success": True,
        "revoked": int(result.get("revoked") or 0),
        "remaining": int(result.get("remaining") or 0),
        "status": _public_status(config, worker),
    }

def whatsapp_bridge_sync_templates(
    payload: WhatsappTemplatesRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    return _gateway_json(config, "POST", "/bridge/templates/sync", {"create_missing": bool(payload.create_missing)}, timeout=45)


_COMPONENT_FUNCTIONS = frozenset((
    'whatsapp_bridge_update_config',
    'whatsapp_bridge_update_phone_settings',
    'whatsapp_bridge_register_phone',
    'whatsapp_bridge_send_adhoc_message',
    'whatsapp_bridge_test',
    'whatsapp_bridge_voice_preflight',
    'whatsapp_bridge_voice_enable',
    'whatsapp_bridge_voice_disable',
    'whatsapp_bridge_voice_calls',
    'whatsapp_bridge_pairing_code',
    'whatsapp_bridge_revoke',
    'whatsapp_bridge_sync_templates'
))
_IMPLEMENTATIONS = {
    'whatsapp_bridge_update_config': whatsapp_bridge_update_config,
    'whatsapp_bridge_update_phone_settings': whatsapp_bridge_update_phone_settings,
    'whatsapp_bridge_register_phone': whatsapp_bridge_register_phone,
    'whatsapp_bridge_send_adhoc_message': whatsapp_bridge_send_adhoc_message,
    'whatsapp_bridge_test': whatsapp_bridge_test,
    'whatsapp_bridge_voice_preflight': whatsapp_bridge_voice_preflight,
    'whatsapp_bridge_voice_enable': whatsapp_bridge_voice_enable,
    'whatsapp_bridge_voice_disable': whatsapp_bridge_voice_disable,
    'whatsapp_bridge_voice_calls': whatsapp_bridge_voice_calls,
    'whatsapp_bridge_pairing_code': whatsapp_bridge_pairing_code,
    'whatsapp_bridge_revoke': whatsapp_bridge_revoke,
    'whatsapp_bridge_sync_templates': whatsapp_bridge_sync_templates
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
