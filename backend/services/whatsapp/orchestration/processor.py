"""Extracted WhatsApp bridge component: processor."""

from __future__ import annotations
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import audio_processing as whatsapp_audio_processing
from backend.services.whatsapp import transcription as whatsapp_transcription
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import marketplace_listing_delivery as whatsapp_marketplace_listing
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import provider_processing as whatsapp_provider_processing
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.codex.console import contracts as console_contracts
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import security as console_security
from backend.services.codex.console import tasks as console_tasks

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _provider_task_attachments(paths: list[str]) -> list[IAChatAttachment]:
    return whatsapp_provider_processing.task_attachments(paths, _base_dir())


_provider_tool_summary = whatsapp_provider_processing.tool_summary

def _whatsapp_execute_source_policy_tools(task: dict[str, Any], query_policy: dict[str, Any]) -> list[dict[str, Any]]:
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    required_tools = [str(item or "").strip() for item in (source_policy.get("required_tools") or []) if str(item or "").strip()]
    forbidden_tools = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    if not required_tools:
        return []
    from backend.services.codex.assistant import execution as assistant_execution

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
            result = assistant_execution.execute_tool_call(
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


def _provider_direct_api_result(
    tool_results: list[dict[str, Any]],
    query_policy: dict[str, Any],
    prompt: Any,
) -> tuple[str, str, dict[str, Any]]:
    listing_bundle = whatsapp_marketplace_listing.build_listing_bundle(tool_results)
    api_report = _whatsapp_daily_ml_sales_report(tool_results, query_policy, prompt)
    if api_report:
        return api_report, "mercado_livre:api", listing_bundle
    listing_response = whatsapp_marketplace_listing.format_listing_bundle(listing_bundle, prompt or "")
    if listing_response:
        return listing_response, "mercado_livre:api", listing_bundle
    return "", "", listing_bundle


def _whatsapp_provider_task_worker(task_id: str) -> None:
    task = console_tasks.load(task_id)
    if not task:
        return
    try:
        from backend.services import ia as ia_service

        console_tasks.update(
            task_id,
            status="running",
            started_at=console_tasks.now(),
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
            console_tasks.log(task, f"Consultas auxiliares indisponiveis: {exc}", "warning")

        response, model_used, listing_bundle = _provider_direct_api_result(
            list(payload.tool_results or []),
            query_policy,
            task.get("prompt"),
        )
        if not response and ia_service._modelo_eh_vertex_ai(model):
            response = ia_service._chamar_vertex_ai_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"vertex:{ia_service._vertex_modelo_nome_curto(model)}"
        elif not response and ia_service._modelo_eh_gemini_api(model):
            response = ia_service._chamar_gemini_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"gemini:{ia_service._gemini_nome_curto(model)}"
        elif not response and model.startswith("deepseek-"):
            response = ia_service._chamar_deepseek_chat(payload, str(task.get("client_id") or "default"))
            model_used = model
        elif not response:
            response = ia_service._chamar_openai_responses(payload, str(task.get("client_id") or "default"))
            model_used = model
        response = str(response or "").strip()
        if not response:
            raise RuntimeError("A IA selecionada concluiu sem resposta.")
        summaries = _provider_tool_summary(list(payload.tool_results or []))
        console_tasks.update(
            task_id,
            status="completed",
            completed_at=console_tasks.now(),
            final_response=response,
            model=model_used,
            live_status="IA do WhatsApp concluiu.",
            tool_results_summary=summaries,
            sources=list(dict.fromkeys(str(item.get("source") or "") for item in summaries if item.get("source"))),
            whatsapp_listing_bundle=listing_bundle if listing_bundle.get("listings") else {},
            error="",
        )
    except Exception as exc:
        detail = str(getattr(exc, "detail", "") or exc or "Falha na IA selecionada.")[:2000]
        console_tasks.update(
            task_id,
            status="failed",
            completed_at=console_tasks.now(),
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
        "created_at": console_tasks.now(),
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
    with console_tasks.records_lock:
        console_tasks.records[task_id] = task
        console_tasks.persist(task)
    console_tasks.log(task, f"Tarefa WhatsApp criada com {model} em modo somente leitura.")
    threading.Thread(
        target=_whatsapp_provider_task_worker,
        args=(task_id,),
        name=f"jk-whatsapp-ia-{task_id[:8]}",
        daemon=True,
    ).start()
    return {"success": True, "task": console_tasks.public(task)}

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
    dual_settings = _whatsapp_dual_agent_settings(config)
    incoming_metadata = dict(channel_metadata or {})
    query_policy = (
        incoming_metadata.get("query_policy")
        if isinstance(incoming_metadata.get("query_policy"), dict)
        else {}
    )
    request_text = str(incoming_metadata.get("request_text") or prompt or "")
    reasoning_level = str(dual_settings.get("task_agent_reasoning") or "low")
    report_mode = console_execution.is_report_request(request_text)
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
    channel_metadata = {
        **incoming_metadata,
        "ai_model": f"codex:{dual_settings['task_agent_model']}",
        "ai_provider": "codex",
        "fallback_model": settings["fallback_model"],
        "fallback_provider": settings["fallback_provider"],
        "response_provider_policy": settings["response_provider_policy"],
        "codex_reasoning_effort": reasoning_level,
        "reasoning_level": reasoning_level,
        "reasoning_policy": settings["codex_reasoning_policy"],
        "reasoning_max": settings["codex_reasoning_max"],
        "codex_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "codex_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "orchestration_profile": (
            "whatsapp_dual_codex_worker" if is_dual_codex_worker else "whatsapp_full_agent"
        ),
        "deadline_enabled": False,
        "deadline_seconds": 0,
        "admin_configured_ai": True,
    }
    codex_model = str(dual_settings["task_agent_model"])
    payload = console_contracts.CodexTaskRequest(
        prompt=prompt,
        # O WhatsApp nunca eleva o sandbox do assistente interno. Acoes
        # operacionais seguem exclusivamente o fluxo tipado aprovado no app.
        sandbox="read_only",
        approval_mode="read_only",
        conversation_id=conversation_id,
        paths=paths,
        screen_context=screen_context,
        model=codex_model,
        reasoning_effort=reasoning_level,
        speed=WHATSAPP_CODEX_SPEED_DEFAULT,
        service_tier=WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        request_id=str(channel_metadata.get("message_id") or uuid.uuid4().hex),
    )
    return console_tasks.create(
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
        downloaded = _download_media(config, message, conversation_id)
        if not isinstance(downloaded, whatsapp_transcription.DownloadedMedia):
            raise RuntimeError("media_download_contract_invalid")
        media = dict(downloaded.public_payload)
        media.pop("path", None)
        media.pop("local_path", None)
        if str(media.get("mime_type") or "") in SUPPORTED_AUDIO_MIMES:
            attachment_root = _base_dir() / ".codex-remote-attachments"
            audio_path: Optional[Path] = None
            audio_deleted = False
            with whatsapp_audio_processing.audio_telemetry_scope(
                client_id=session.get("client_id") or config.get("client_id") or "default",
                trace_id=message_id,
                user_id=session.get("username") or "",
                surface="whatsapp",
            ):
                try:
                    audio_path = whatsapp_audio_processing.validate_inbound_audio_path(
                        downloaded.local_path,
                        attachment_root,
                    )
                    transcription = whatsapp_audio_processing.transcribe_audio_with_retry(
                        _transcribe_audio,
                        audio_path,
                        total_attempts=2,
                    )
                except (OSError, ValueError):
                    transcription = whatsapp_audio_processing.transcription_failure("child_failed")
                finally:
                    if audio_path is not None:
                        audio_deleted = whatsapp_audio_processing.delete_inbound_audio(audio_path, attachment_root)
                        if not audio_deleted:
                            whatsapp_audio_processing.queue_inbound_audio_cleanup(audio_path, attachment_root)
            if not audio_deleted:
                transcription = whatsapp_audio_processing.transcription_failure("audio_cleanup_failed")
            media = None
    request_text = _message_request_text(message, transcription)
    quoted_context = whatsapp_message.message_quoted_context(message)
    action_message = {**message, "text_body": request_text}
    if transcription and transcription.get("success") and request_text:
        action_message["message_type"] = "text"
        # Discard the raw transcription envelope before returning the prepared
        # context. Only the canonical user message continues through the
        # existing conversation-history policy.
        transcription = {"success": True, "local_only": True}
    return {
        "handled": False, "message_id": message_id, "subject": subject, "session": session,
        "phone_ai_behavior": phone_ai_behavior, "phone": phone, "conversation_id": conversation_id,
        "media": media, "transcription": transcription, "request_text": request_text,
        "quoted_context": quoted_context, "message": action_message,
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


def _question_only_policy_reply(request_text: str) -> str:
    reason = whatsapp_intent.question_only_block_reason(
        request_text,
        mutation_detector=console_security.prompt_requests_mutation,
    )
    if not reason:
        return ""
    if reason == "report_or_file":
        return (
            "No WhatsApp, o Black Jhon responde somente perguntas e nao gera relatorios, "
            "planilhas, graficos ou arquivos. Faca uma pergunta objetiva sobre o dado que deseja consultar."
        )
    if reason in {"operation", "content_creation"}:
        return (
            "No WhatsApp, o Black Jhon responde somente perguntas. Operacoes e criacao de conteudo "
            "devem ser feitas no JK Sistema. A excecao e a resposta de pergunta do Mercado Livre: "
            "voce pode pedir uma alteracao na sugestao recebida e aprovar o envio por aqui."
        )
    return (
        "No WhatsApp, o Black Jhon responde somente perguntas. Envie uma pergunta objetiva. "
        "As sugestoes de resposta das perguntas do Mercado Livre tambem podem ser revisadas e aprovadas por aqui."
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
    store_mode = str(selection.get("store_mode") or "single")
    selected_store = str(selection.get("store") or "").strip()
    _dual_confirm_conversation_context(
        state, conversation_id,
        {"store_mode": store_mode, "store": selected_store, "clear_fields": ["store"] if store_mode == "all" else []},
        authorized_stores=_whatsapp_session_stores(session), source="interactive_selection", pin_next_turn=True,
    )
    if store_mode == "all":
        stores = [str(store or "").strip() for store in selection.get("stores") or [] if str(store or "").strip()]
        request_text = (
            f"{original}\n\nSelecao confirmada: todas as lojas. "
            f"Consulte separadamente estas lojas: {', '.join(stores)}. Nao some nem misture os totais entre lojas."
        ).strip()
    else:
        request_text = f"{original}\n\nLoja selecionada: {selected_store}".strip()
    return {**message, "text_body": request_text, "message_type": "text"}, request_text, False

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
    quoted_context = context.get("quoted_context") if isinstance(context.get("quoted_context"), dict) else {}
    message = context["message"]
    if isinstance(transcription, dict) and (
        transcription.get("success") is not True or not str(request_text or "").strip()
    ):
        error_code = transcription.get("error_code") or (
            "no_speech" if transcription.get("success") is True else "child_failed"
        )
        _post_command_reply(
            config,
            message_id,
            whatsapp_audio_processing.transcription_reply(error_code),
            "BLACK JHON - AUDIO NAO PROCESSADO",
        )
        return
    if isinstance(transcription, dict) and transcription.get("success") is True:
        # The transcript is already the canonical request text.  Do not pass
        # audio metadata or a transcription object to any AI provider.
        transcription = None
        media = None
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
    policy_reply = _question_only_policy_reply(request_text)
    if policy_reply:
        _post_command_reply(
            config,
            message_id,
            policy_reply,
            "BLACK JHON - SOMENTE PERGUNTAS",
        )
        return
    # Direct cutover: every free-form message goes through the Codex
    # conversation agent and the Codex data-selection agent.
    _process_dual_codex_message(
        config, state, message, session=session, conversation_id=conversation_id,
        message_id=message_id, subject=subject, phone=phone, request_text=request_text,
        media=media, transcription=transcription, phone_ai_behavior=phone_ai_behavior,
        quoted_context=quoted_context,
    )

_COMPONENT_FUNCTIONS = frozenset((
    '_provider_tool_summary',
    '_provider_task_attachments',
    '_whatsapp_execute_source_policy_tools',
    '_whatsapp_provider_task_worker',
    '_create_provider_task',
    '_create_selected_ai_task',
    '_process_message'
))
_IMPLEMENTATIONS = {
    '_provider_tool_summary': _provider_tool_summary,
    '_provider_task_attachments': _provider_task_attachments,
    '_whatsapp_execute_source_policy_tools': _whatsapp_execute_source_policy_tools,
    '_whatsapp_provider_task_worker': _whatsapp_provider_task_worker,
    '_create_provider_task': _create_provider_task,
    '_create_selected_ai_task': _create_selected_ai_task,
    '_process_message': _process_message
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
