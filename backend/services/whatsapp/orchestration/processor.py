"""Extracted WhatsApp bridge component: processor."""

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


def _provider_tool_summary(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in tool_results[:50]:
        if not isinstance(item, dict):
            continue
        summary = {
            key: item.get(key)
            for key in ("tool_id", "function", "status", "source", "message", "paging", "warnings")
            if item.get(key) not in (None, "", [], {})
        }
        if summary:
            summaries.append(summary)
    return summaries

def _provider_task_attachments(paths: list[str]) -> list[IAChatAttachment]:
    attachments: list[IAChatAttachment] = []
    for raw in paths[:4]:
        try:
            path = Path(str(raw or ""))
            if not path.is_absolute():
                path = (_base_dir() / path).resolve()
            if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
                continue
            mime = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").lower()
            if not mime.startswith("image/"):
                continue
            attachments.append(
                IAChatAttachment(
                    name=path.name,
                    mime_type=mime,
                    data_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
                )
            )
        except Exception:
            continue
    return attachments

def _whatsapp_execute_source_policy_tools(task: dict[str, Any], query_policy: dict[str, Any]) -> list[dict[str, Any]]:
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    required_tools = [str(item or "").strip() for item in (source_policy.get("required_tools") or []) if str(item or "").strip()]
    forbidden_tools = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    if not required_tools:
        return []
    from backend.services import codex_assistant

    stores = [str(item or "").strip() for item in (query_policy.get("stores") or []) if str(item or "").strip()]
    if not stores and str(query_policy.get("store") or "").strip():
        stores = [str(query_policy.get("store") or "").strip()]
    if not stores:
        stores = [""]
    message = str(query_policy.get("base_request") or task.get("prompt") or "").strip()
    report_mode = bool(query_policy.get("report_mode"))
    results: list[dict[str, Any]] = []
    for store in stores:
        for tool_id in required_tools:
            if tool_id in forbidden_tools:
                continue
            requested_limit = int(query_policy.get("limit") or (10000 if tool_id == "mercado_livre_full_stock" else 50))
            if report_mode and tool_id == "mercado_livre_orders":
                requested_limit = max(requested_limit, 20000)
            args = {
                "message": message,
                "loja": store,
                "limite": requested_limit,
                "offset": int(query_policy.get("offset") or 0),
                "mode": "report" if report_mode else "chat",
                "force_refresh": bool(query_policy.get("bypass_cache") or source_policy.get("force_refresh", True)),
                "incluir_detalhes": bool(source_policy.get("include_listing_details")),
            }
            if report_mode and tool_id == "mercado_livre_orders":
                args["max_paginas"] = 400
                if str(query_policy.get("data_inicio") or "").strip():
                    args["data_inicio"] = str(query_policy.get("data_inicio")).strip()
                if str(query_policy.get("data_fim") or "").strip():
                    args["data_fim"] = str(query_policy.get("data_fim")).strip()
            result = codex_assistant.codex_assistant_execute_tool_call(
                client_id=str(task.get("client_id") or "default"),
                tool_id=tool_id,
                args=args,
                screen_context=task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {},
                previous_results=results,
                permissions=task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                audit_user=str(task.get("created_by") or "whatsapp"),
                query_deadline=time.monotonic() + (300 if report_mode and tool_id == "mercado_livre_orders" else 60),
            )
            results.append(result)
    return results

def _whatsapp_provider_task_worker(task_id: str) -> None:
    task = codex_console._codex_load_task(task_id)
    if not task:
        return
    try:
        from backend.services import ia as ia_service

        codex_console._codex_update_task(
            task_id,
            status="running",
            started_at=codex_console._codex_now(),
            live_status="IA do WhatsApp esta processando.",
            error="",
        )
        model = _normalize_ai_model(task.get("model"))
        context = dict(task.get("screen_context") or {}) if isinstance(task.get("screen_context"), dict) else {}
        query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
        context.update({
            "origem": "whatsapp",
            "modo_rapido_sidebar": False,
            "restricao_whatsapp": "Somente consultas e respostas em texto; nenhuma mutacao externa e permitida.",
            "query_policy": query_policy,
        })
        if query_policy.get("store"):
            context["loja"] = str(query_policy.get("store"))
        payload = IAChatRequest(
            message=str(task.get("prompt") or ""),
            page="WhatsApp - Black John",
            context=context,
            attachments=_provider_task_attachments(list(task.get("paths") or [])),
            model=model,
            tool_results=[],
            fallback_read_only=True,
        )
        try:
            source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
            if source_policy:
                payload.tool_results = _whatsapp_execute_source_policy_tools(task, query_policy)
            else:
                payload.tool_results = ia_service._ia_chat_executar_funcoes(payload, str(task.get("client_id") or "default"))
        except Exception as exc:
            payload.tool_results = []
            codex_console._codex_log(task, f"Consultas auxiliares indisponiveis: {exc}", "warning")

        api_report = _whatsapp_daily_ml_sales_report(
            list(payload.tool_results or []),
            query_policy,
            task.get("prompt"),
        )
        if api_report:
            response = api_report
            model_used = "mercado_livre:api"
        elif ia_service._modelo_eh_vertex_ai(model):
            response = ia_service._chamar_vertex_ai_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"vertex:{ia_service._vertex_modelo_nome_curto(model)}"
        elif ia_service._modelo_eh_gemini_api(model):
            response = ia_service._chamar_gemini_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"gemini:{ia_service._gemini_nome_curto(model)}"
        elif model.startswith("deepseek-"):
            response = ia_service._chamar_deepseek_chat(payload, str(task.get("client_id") or "default"))
            model_used = model
        else:
            response = ia_service._chamar_openai_responses(payload, str(task.get("client_id") or "default"))
            model_used = model
        response = str(response or "").strip()
        if not response:
            raise RuntimeError("A IA selecionada concluiu sem resposta.")
        requested_formats = whatsapp_report_files.requested_report_formats(task.get("prompt") or "")
        chart_outcome = (
            whatsapp_report_visuals.generate_task_chart_artifacts(
                base_info_dir=codex_console._codex_base_info_dir(),
                client_id=task.get("client_id") or "default",
                task_id=task_id,
                prompt=task.get("prompt") or "",
                tool_results=list(payload.tool_results or []),
                query_policy=query_policy,
                max_images=2,
            )
            if "png" in requested_formats
            else {"expected": False, "status": "not_requested", "artifacts": []}
        )
        document_outcome = whatsapp_report_files.generate_report_documents(
            base_info_dir=codex_console._codex_base_info_dir(),
            client_id=task.get("client_id") or "default",
            task_id=task_id,
            prompt=task.get("prompt") or "",
            tool_results=list(payload.tool_results or []),
            query_policy=query_policy,
            formats=requested_formats,
        )
        report_artifacts = [
            *list(chart_outcome.get("artifacts") or []),
            *list(document_outcome.get("artifacts") or []),
        ][:4]
        summaries = _provider_tool_summary(list(payload.tool_results or []))
        codex_console._codex_update_task(
            task_id,
            status="completed",
            completed_at=codex_console._codex_now(),
            final_response=response,
            model=model_used,
            live_status="IA do WhatsApp concluiu.",
            tool_results_summary=summaries,
            sources=list(dict.fromkeys(str(item.get("source") or "") for item in summaries if item.get("source"))),
            whatsapp_artifacts=report_artifacts,
            whatsapp_chart_expected=bool(chart_outcome.get("expected")),
            whatsapp_chart_status=str(chart_outcome.get("status") or "")[:80],
            whatsapp_chart_error=str(chart_outcome.get("error") or "")[:500],
            error="",
        )
    except Exception as exc:
        detail = str(getattr(exc, "detail", "") or exc or "Falha na IA selecionada.")[:2000]
        codex_console._codex_update_task(
            task_id,
            status="failed",
            completed_at=codex_console._codex_now(),
            final_response="",
            live_status="IA do WhatsApp falhou.",
            error=detail,
        )

def _create_provider_task(
    *,
    model: str,
    prompt: str,
    session: dict[str, Any],
    conversation_id: str,
    paths: list[str],
    screen_context: dict[str, Any],
    channel_metadata: dict[str, Any],
) -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    query_policy = channel_metadata.get("query_policy") if isinstance(channel_metadata.get("query_policy"), dict) else {}
    task = {
        "task_id": task_id,
        "status": "queued",
        "sandbox": "read_only",
        "cwd": "",
        "thread_id": "",
        "conversation_id": conversation_id,
        "prompt": prompt,
        "model": model,
        "approval_mode": "read_only",
        "reasoning_effort": "",
        "speed": "standard",
        "service_tier": "",
        "goal": "",
        "planning_mode": False,
        "paths": list(paths or []),
        "scope": {},
        "scope_violations": [],
        "screen_context": dict(screen_context or {}),
        "history": [],
        "context_stats": {},
        "conversation_summary": {},
        "conversation_compaction": {},
        "agent_mode": False,
        "agent_steps": [],
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": [],
        "warnings": [],
        "live_status": "Tarefa de IA criada.",
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
        "token_usage": {},
        "turn_id": "",
        "mutable_intent": False,
        "final_response": "",
        "error": "",
        "logs": [],
        "created_at": codex_console._codex_now(),
        "started_at": "",
        "completed_at": "",
        "created_by": str(session.get("username") or "user"),
        "client_id": str(session.get("client_id") or "default"),
        "origin": "whatsapp",
        "channel_message_id": str(channel_metadata.get("message_id") or "")[:200],
        "channel_metadata": dict(channel_metadata or {}),
        "external_safe_mode": True,
        "whatsapp_full_access": False,
        "whatsapp_query_only": query_policy.get("mode") == "query_only",
        "query_policy": query_policy,
        "access_mode": "query_only" if query_policy.get("mode") == "query_only" else "read_only",
        "permissions": {
            str(key): value is True
            for key, value in (session.get("permissions") or {}).items()
            if str(key or "").strip()
        },
        "approval_required": False,
        "approved": True,
        "provider_task": True,
    }
    with codex_console.CODEX_TASKS_LOCK:
        codex_console.CODEX_TASKS[task_id] = task
        codex_console._codex_persist_task(task)
    codex_console._codex_log(task, f"Tarefa WhatsApp criada com {model} em modo somente leitura.")
    threading.Thread(
        target=_whatsapp_provider_task_worker,
        args=(task_id,),
        name=f"jk-whatsapp-ia-{task_id[:8]}",
        daemon=True,
    ).start()
    return {"success": True, "task": codex_console._codex_public_task(task)}

def _whatsapp_adaptive_reasoning_level(
    settings: dict[str, Any],
    request_text: str,
    query_policy: Optional[dict[str, Any]] = None,
) -> str:
    configured = _normalize_codex_reasoning_effort(settings.get("codex_reasoning_effort"))
    policy = _normalize_codex_reasoning_policy(settings.get("codex_reasoning_policy"))
    maximum = _normalize_codex_reasoning_effort(settings.get("codex_reasoning_max") or configured)
    if policy == "fixed":
        return configured
    ranks = {"low": 0, "medium": 1, "high": 2, "xhigh": 3}
    normalized = unicodedata.normalize("NFKD", str(request_text or "")).encode("ascii", "ignore").decode("ascii").lower()
    query_policy = query_policy if isinstance(query_policy, dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    domains = [str(item) for item in (query_policy.get("domains") or [])]
    providers = [str(item) for item in (source_policy.get("providers") or [])]
    level = "low"
    if query_policy or re.search(r"\b(estoque|pedido|venda|anuncio|devolucao|reclamacao|sku|produto)\b", normalized):
        level = "medium"
    if (
        query_policy.get("store_mode") == "all"
        or len(domains) > 1
        or len(providers) > 1
        or re.search(r"\b(relatorio|compare|comparacao|analise|periodo|todas as lojas|historico completo)\b", normalized)
    ):
        level = "high"
    if re.search(
        r"\b(fontes divergentes|dados conflitantes|estrategia|planeje|plano completo|risco|simule|cenario|corrija a falha|investigue profundamente)\b",
        normalized,
    ):
        level = "xhigh"
    if ranks[level] > ranks[maximum]:
        level = maximum
    return level

def _create_selected_ai_task(
    config: dict[str, Any],
    *,
    prompt: str,
    session: dict[str, Any],
    conversation_id: str,
    paths: list[str],
    screen_context: dict[str, Any],
    safe_read_only: bool,
    mobile_full_access: bool,
    channel_metadata: dict[str, Any],
) -> dict[str, Any]:
    settings = _whatsapp_ai_settings(config)
    incoming_metadata = dict(channel_metadata or {})
    query_policy = (
        incoming_metadata.get("query_policy")
        if isinstance(incoming_metadata.get("query_policy"), dict)
        else {}
    )
    request_text = str(incoming_metadata.get("request_text") or prompt or "")
    reasoning_level = _whatsapp_adaptive_reasoning_level(settings, request_text, query_policy)
    report_mode = codex_console._codex_agent_is_report_request(request_text)
    requested_profile = str(incoming_metadata.get("orchestration_profile") or "").strip()
    requested_role = str(incoming_metadata.get("agent_role") or "").strip().lower()
    requested_lane = str(incoming_metadata.get("agent_lane") or "").strip().lower()
    is_dual_codex_worker = bool(
        settings["provider"] == "codex"
        and safe_read_only
        and requested_profile == "whatsapp_dual_codex_worker"
        and requested_role == "task"
        and requested_lane == "worker"
    )
    default_deadline = _job_deadline_seconds(
        request_text,
        requires_web=bool(incoming_metadata.get("allow_web_search") or incoming_metadata.get("web_search_requested")),
    )
    try:
        requested_deadline = int(incoming_metadata.get("deadline_seconds") or default_deadline)
    except (TypeError, ValueError):
        requested_deadline = default_deadline
    channel_metadata = {
        **incoming_metadata,
        "ai_model": settings["model"],
        "ai_provider": settings["provider"],
        "codex_reasoning_effort": reasoning_level,
        "reasoning_level": reasoning_level,
        "reasoning_policy": settings["codex_reasoning_policy"],
        "reasoning_max": settings["codex_reasoning_max"],
        "codex_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "codex_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "orchestration_profile": (
            "whatsapp_dual_codex_worker" if is_dual_codex_worker else "whatsapp_full_agent"
        ),
        "deadline_seconds": max(30, min(requested_deadline, 600)),
        "admin_configured_ai": True,
    }
    if settings["provider"] != "codex":
        return _create_provider_task(
            model=settings["model"],
            prompt=prompt,
            session=session,
            conversation_id=conversation_id,
            paths=paths,
            screen_context=screen_context,
            channel_metadata=channel_metadata,
        )
    codex_model = settings["model"].split(":", 1)[1]
    payload = codex_console.CodexTaskRequest(
        prompt=prompt,
        sandbox="read_only" if safe_read_only else ("workspace_write" if mobile_full_access else "read_only"),
        approval_mode="read_only" if safe_read_only else ("request" if mobile_full_access else "read_only"),
        conversation_id=conversation_id,
        paths=paths,
        screen_context=screen_context,
        model=codex_model,
        reasoning_effort=reasoning_level,
        speed=WHATSAPP_CODEX_SPEED_DEFAULT,
        service_tier=WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        request_id=str(channel_metadata.get("message_id") or uuid.uuid4().hex),
    )
    return codex_console.codex_criar_tarefa_para_sessao(
        payload,
        session,
        origin="whatsapp",
        channel_metadata=channel_metadata,
    )

def _prepare_inbound_message(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
) -> Optional[dict[str, Any]]:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return None
    if str(message.get("machine_id") or "") != str(config.get("machine_id") or ""):
        raise RuntimeError("message_not_owned_by_this_machine")
    _start_typing_pulse(config, message_id)
    existing = _pending_task_for_message(state, message_id)
    if existing:
        _complete_pending(config, state, message_id, existing)
        return {"handled": True}
    subject = str(message.get("subject_id") or "").strip()
    if subject and subject != str(config.get("subject_id") or ""):
        config["subject_id"] = subject
        config["personal_phone"] = str(message.get("wa_id") or "")
    session = _reload_bound_session(config, message)
    settings = _phone_notification_settings(
        config, subject, client_id=session.get("client_id"), username=session.get("username"),
    )
    phone_ai_behavior = _normalize_phone_ai_behavior(settings.get("ai_behavior"))
    phone = ""
    conversation_id = ""
    media: Optional[dict[str, Any]] = None
    transcription: Optional[dict[str, Any]] = None
    if message.get("media_id") or message.get("media_object_key"):
        phone = _message_phone(config, message)
        if not phone:
            raise RuntimeError("whatsapp_phone_identity_missing")
        conversation_id = _conversation_id(config, message)
        media = _download_media(config, message, conversation_id)
        if str(media.get("mime_type") or "") in SUPPORTED_AUDIO_MIMES:
            transcription = _transcribe_audio((_base_dir() / str(media.get("path") or "")).resolve())
    request_text = _message_request_text(message, transcription)
    action_message = {**message, "text_body": request_text}
    if transcription and transcription.get("success") and request_text:
        action_message["message_type"] = "text"
    return {
        "handled": False, "message_id": message_id, "subject": subject, "session": session,
        "phone_ai_behavior": phone_ai_behavior, "phone": phone, "conversation_id": conversation_id,
        "media": media, "transcription": transcription, "request_text": request_text,
        "message": action_message,
    }

def _handle_inbound_commands(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    return bool(
        _handle_question_approval_command(config, state, message, session)
        or _handle_question_natural_language(config, state, message, session)
        or _handle_approval_command(config, state, message, session)
    )

def _apply_store_selection(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    message_id: str,
    subject: str,
) -> tuple[dict[str, Any], str, bool]:
    token = _whatsapp_store_selection_token(request_text)
    if not token:
        return message, request_text, False
    selection = _whatsapp_consume_store_selection(
        state, token, subject_id=subject, session=session, conversation_id=conversation_id,
    )
    if not selection:
        _post_command_reply(
            config, message_id,
            "Essa escolha de loja expirou ou ja foi utilizada. Envie novamente a sua pergunta para escolher outra vez.",
            "BLACK JOHN - ESCOLHA EXPIRADA",
        )
        return message, request_text, True
    original = str(selection.get("request_text") or "").strip()
    if str(selection.get("store_mode") or "single") == "all":
        stores = [str(store or "").strip() for store in selection.get("stores") or [] if str(store or "").strip()]
        request_text = (
            f"{original}\n\nSelecao confirmada: todas as lojas. "
            f"Consulte separadamente estas lojas: {', '.join(stores)}. Nao some nem misture os totais entre lojas."
        ).strip()
    else:
        request_text = f"{original}\n\nLoja selecionada: {str(selection.get('store') or '').strip()}".strip()
    return {**message, "text_body": request_text, "message_type": "text"}, request_text, False

def _try_steer_standard_task(
    config: dict[str, Any],
    state: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    message_id: str,
    subject: str,
    phone: str,
    request_text: str,
) -> bool:
    if (
        _whatsapp_ai_settings(config).get("provider") != "codex"
        or str(config.get("active_task_policy") or "steer_or_queue") != "steer_or_queue"
        or not _whatsapp_is_task_complement(request_text)
    ):
        return False
    _, active_pending, active_task = _active_pending_for_conversation(
        state, conversation_id, exclude_message_id=message_id,
    )
    if not active_task:
        return False
    result = codex_console.codex_complementar_tarefa_para_sessao(
        str(active_task.get("task_id") or ""), request_text, session,
        request_id=message_id, subject_id=subject, wa_id=phone,
    )
    if result.get("accepted") is not True:
        return False
    active_request = str(active_pending.get("request_text") or "").strip()
    response = "Incluí esta informação na consulta em andamento."
    if active_request:
        response += f" Pedido em análise: {active_request[:220]}"
    _post_message_result(
        config, message_id,
        {"status": "completed", "task_id": str(active_task.get("task_id") or ""), "response": response},
    )
    return True

def _standard_query_candidates(
    request_text: str,
    session: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
) -> tuple[dict[str, Any], bool, bool, bool]:
    general_answer = _whatsapp_general_answer_request(request_text, session)
    pagination = _whatsapp_pagination_request(request_text)
    contextual_report = _whatsapp_contextual_report_request(request_text)
    direct = {} if pagination or general_answer else _whatsapp_query_policy(request_text, session)
    inherited = {} if pagination or contextual_report else _whatsapp_inherit_query_store_context(
        request_text, direct, state, conversation_id, session,
    )
    direct_resolved = bool(
        direct and (
            not direct.get("store_required") or direct.get("store_mode") == "all"
            or len(direct.get("store_matches") or []) == 1
        )
    )
    if pagination:
        policy = _whatsapp_query_continuation_policy(request_text, state, conversation_id, session)
    elif direct_resolved:
        policy = direct
    elif contextual_report:
        policy = _whatsapp_query_continuation_policy(request_text, state, conversation_id, session) or direct
    elif inherited:
        policy = inherited
    else:
        policy = direct
    general_answer = bool(general_answer and not policy)
    if not general_answer and not policy and not _whatsapp_mutation_intent(request_text):
        policy = _whatsapp_store_scope_policy(request_text, session)
    return policy, general_answer, pagination, contextual_report

def _validate_standard_query_policy(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    message_id: str,
    request_text: str,
    policy: dict[str, Any],
    pagination: bool,
    contextual_report: bool,
) -> bool:
    if (pagination or contextual_report) and not policy:
        _post_command_reply(
            config, message_id,
            "Nao encontrei uma consulta anterior recente nesta conversa. Repita o pedido informando os filtros e, para API, a loja exata.",
            "BLACK JOHN — REPITA A CONSULTA",
        )
        return False
    if policy.get("no_more_results") is True:
        _post_command_reply(
            config, message_id,
            "A consulta anterior ja chegou ao fim dos resultados retornados pelas APIs. Para iniciar outra busca, envie novamente os filtros e a loja.",
            "BLACK JOHN — FIM DOS RESULTADOS",
        )
        return False
    missing_store = bool(
        policy.get("store_required") and policy.get("store_mode") != "all"
        and len(policy.get("store_matches") or []) != 1
    )
    mutation_stores = _whatsapp_session_stores(session) if not policy else []
    mutation_missing_store = bool(
        not policy and _whatsapp_mutation_intent(request_text) and _whatsapp_store_scoped_request(request_text)
        and len(_whatsapp_exact_store_matches(request_text, mutation_stores)) != 1
    )
    if not missing_store and not mutation_missing_store:
        return True
    stores = list(policy.get("authorized_stores") or []) if missing_store else mutation_stores
    if _whatsapp_send_store_selection(
        config, state, message, session, conversation_id, request_text, stores, allow_all=missing_store,
    ):
        return False
    fallback = policy or {"authorized_stores": stores, "store_matches": _whatsapp_exact_store_matches(request_text, stores)}
    _post_command_reply(config, message_id, _whatsapp_store_required_text(fallback), "BLACK JOHN — INFORME A LOJA")
    return False

def _standard_task_pending(
    session: dict[str, Any],
    task: dict[str, Any],
    proposal: dict[str, Any],
    conversation_id: str,
    subject: str,
    phone: str,
    request_text: str,
    mobile_full_access: bool,
    query_policy: dict[str, Any],
    general_answer: bool,
    app_confirmation_only: bool,
) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or ""), "kind": "task", "conversation_id": conversation_id,
        "subject_id": subject, "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(),
        "request_text": request_text or "Pedido com anexo recebido pelo WhatsApp.",
        "mobile_full_access": mobile_full_access, "query_policy": query_policy, "general_answer": general_answer,
        "created_at": _now(), "awaiting_notified": app_confirmation_only,
        "trusted_bound_number": mobile_full_access, "proposal_id": str(proposal.get("proposal_id") or ""),
        "proposal_version": int(proposal.get("version") or 1), "proposal_hash": str(proposal.get("proposal_hash") or ""),
        "action_summary": str(proposal.get("summary") or proposal.get("title") or ""),
        "risk": str(proposal.get("risk") or ""), "wa_id": phone,
    }

def _launch_standard_ai_task(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    message_id: str,
    subject: str,
    phone: str,
    request_text: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
    mobile_full_access: bool,
    query_policy: dict[str, Any],
    general_answer: bool,
) -> None:
    result = _create_selected_ai_task(
        config,
        prompt=_message_prompt(
            message, media, transcription, mobile_full_access=mobile_full_access,
            query_policy=query_policy, ai_behavior=phone_ai_behavior, general_answer=general_answer,
        ),
        session=session, conversation_id=conversation_id,
        paths=[str(media.get("path"))] if media else [],
        screen_context=_mobile_screen_context(message_id, subject, media, transcription, query_policy),
        safe_read_only=bool(general_answer or query_policy.get("mode") == "query_only" or _whatsapp_readonly_inquiry(request_text)),
        mobile_full_access=mobile_full_access,
        channel_metadata={
            "message_id": message_id, "subject_id": subject, "wa_id": phone,
            "message_type": str(message.get("message_type") or "text"), "media": media or {},
            "transcription": transcription or {}, "received_at": message.get("received_at"),
            "mobile_full_access": mobile_full_access, "query_policy": query_policy,
            "phone_ai_behavior": phone_ai_behavior, "general_answer": general_answer, "request_text": request_text,
        },
    )
    task = result.get("task") if isinstance(result, dict) else {}
    proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    app_only = bool(proposal and (
        proposal.get("requires_app_confirmation") is True or "whatsapp" not in list(proposal.get("channels_allowed") or [])
    ))
    _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
    pending = _standard_task_pending(
        session, task, proposal, conversation_id, subject, phone, request_text,
        mobile_full_access, query_policy, general_answer, app_only,
    )
    _save_pending(state, message_id, pending)
    if _whatsapp_ai_settings(config).get("provider") == "codex":
        _start_progress_pulse(config, message_id, str(task.get("task_id") or ""))
    if app_only:
        parts = _whatsapp_response_parts(
            "A proposta foi preparada, mas esta ação só pode ser confirmada no aplicativo JK Sistema. "
            f"Identificador: `{str(proposal.get('proposal_id') or task.get('task_id') or '')}`.",
            "BLACK JHON - CONFIRME NO APLICATIVO",
        )
        _post_message_result(
            config, message_id,
            {"status": "completed", "task_id": str(task.get("task_id") or ""), "response": parts[0], "response_parts": parts},
        )
        _remove_pending(state, message_id)
        return
    _complete_pending(config, state, message_id, pending)

def _process_message(config: dict[str, Any], state: dict[str, Any], message: dict[str, Any]) -> None:
    context = _prepare_inbound_message(config, state, message)
    if context is None or context.get("handled") is True:
        return
    message_id = context["message_id"]
    subject = context["subject"]
    session = context["session"]
    phone_ai_behavior = context["phone_ai_behavior"]
    phone = context["phone"]
    conversation_id = context["conversation_id"]
    media = context["media"]
    transcription = context["transcription"]
    request_text = context["request_text"]
    message = context["message"]
    if _handle_inbound_commands(config, state, message, session):
        return
    if not phone:
        phone = _message_phone(config, message)
    if not phone:
        raise RuntimeError("whatsapp_phone_identity_missing")
    if not conversation_id:
        conversation_id = _conversation_id(config, message)
    mobile_full_access = bool(session.get("is_full") and (session.get("permissions") or {}).get("full") is True)
    message, request_text, selection_handled = _apply_store_selection(
        config, state, message, session, conversation_id,
        request_text, message_id, subject,
    )
    if selection_handled:
        return
    if str(config.get("agent_architecture") or "").strip().lower() == "dual_codex":
        _process_dual_codex_message(
            config, state, message, session=session, conversation_id=conversation_id,
            message_id=message_id, subject=subject, phone=phone, request_text=request_text,
            media=media, transcription=transcription, phone_ai_behavior=phone_ai_behavior,
        )
        return
    if _try_steer_standard_task(
        config, state, session, conversation_id, message_id,
        subject, phone, request_text,
    ):
        return
    initial_general = _whatsapp_general_answer_request(request_text, session)
    protected = [] if initial_general else _whatsapp_protected_mutation_domains(request_text)
    if protected and not mobile_full_access:
        _post_command_reply(
            config, message_id, _whatsapp_query_only_block_text(protected),
            "BLACK JOHN — SOMENTE CONSULTA",
        )
        return
    query_policy, general_answer, pagination, contextual_report = _standard_query_candidates(
        request_text, session, state, conversation_id,
    )
    if not _validate_standard_query_policy(
        config, state, message, session, conversation_id, message_id,
        request_text, query_policy, pagination, contextual_report,
    ):
        return
    _launch_standard_ai_task(
        config, state, message, session, conversation_id, message_id,
        subject, phone, request_text, media, transcription, phone_ai_behavior,
        mobile_full_access, query_policy, general_answer,
    )

_COMPONENT_FUNCTIONS = frozenset((
    '_provider_tool_summary',
    '_provider_task_attachments',
    '_whatsapp_execute_source_policy_tools',
    '_whatsapp_provider_task_worker',
    '_create_provider_task',
    '_whatsapp_adaptive_reasoning_level',
    '_create_selected_ai_task',
    '_process_message'
))
_IMPLEMENTATIONS = {
    '_provider_tool_summary': _provider_tool_summary,
    '_provider_task_attachments': _provider_task_attachments,
    '_whatsapp_execute_source_policy_tools': _whatsapp_execute_source_policy_tools,
    '_whatsapp_provider_task_worker': _whatsapp_provider_task_worker,
    '_create_provider_task': _create_provider_task,
    '_whatsapp_adaptive_reasoning_level': _whatsapp_adaptive_reasoning_level,
    '_create_selected_ai_task': _create_selected_ai_task,
    '_process_message': _process_message
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
