"""Task creation workflow isolated from HTTP transport."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import HTTPException

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_assistant_storage,
    codex_turn_context,
)
from .contracts import CodexTaskRequest


def _validated_prompt(payload: CodexTaskRequest) -> str:
    _codex_cleanup_old_attachments()
    if not _codex_enabled():
        raise HTTPException(
            status_code=503,
            detail="Codex Console desabilitado. Defina JK_CODEX_CONSOLE_ENABLED=true.",
        )
    if not _codex_sdk_installed():
        raise HTTPException(
            status_code=503,
            detail="Dependencia openai-codex nao instalada no runtime Python.",
        )
    prompt = str(payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Informe uma mensagem para o Codex.")
    if _codex_prompt_pede_desenvolvimento(prompt):
        raise HTTPException(
            status_code=409, detail=_codex_development_requires_desktop_detail()
        )
    return prompt


def _base_context(
    payload: CodexTaskRequest,
    sessao: dict[str, Any],
    origin: str,
    channel_metadata: Optional[dict[str, Any]],
    trusted_model_config: bool,
) -> dict[str, Any]:
    prompt = _validated_prompt(payload)
    is_full = bool(sessao.get("is_full"))
    origin = "whatsapp" if str(origin or "").strip().lower() == "whatsapp" else "app"
    metadata = dict(channel_metadata or {}) if isinstance(channel_metadata, dict) else {}
    trusted = bool(
        origin == "whatsapp"
        and (trusted_model_config or metadata.get("admin_configured_ai") is True)
    )
    query_policy = _codex_whatsapp_query_policy(origin, metadata)
    whatsapp_query_only = bool(query_policy)
    external_safe_mode = origin == "whatsapp"
    sandbox = _codex_normalizar_sandbox(payload.sandbox)
    model = _codex_normalizar_model(payload.model)
    reasoning_effort = _codex_normalizar_reasoning_effort(payload.reasoning_effort)
    speed = _codex_normalizar_speed(payload.speed)
    service_tier = _codex_normalizar_service_tier(payload.service_tier, speed)
    if whatsapp_query_only or not is_full:
        sandbox = "read_only"
    if not is_full and not trusted:
        model = _codex_normalizar_model(None)
        reasoning_effort = _codex_normalizar_reasoning_effort(None)
        speed = _codex_normalizar_speed(None)
        service_tier = _codex_normalizar_service_tier(None, speed)
    shared = _codex_shared_continuity_for_session(sessao) if origin == "app" else {}
    conversation_id = str(shared.get("conversation_id") or "")
    if not conversation_id:
        conversation_id = _codex_resolve_new_conversation_id(
            sessao,
            payload.conversation_id,
            origin=origin,
            channel_metadata=metadata,
        )
    if shared:
        metadata["shared_continuity"] = True
    conversation_state = _codex_load_or_create_conversation_state(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
        channel=origin,
        phone=metadata.get("wa_id") if origin == "whatsapp" else "",
        lane=metadata.get("agent_lane") or metadata.get("agent_role"),
    )
    visible_state = (
        _codex_load_or_create_shared_conversation_state(
            str(sessao.get("client_id") or "default"),
            str(sessao.get("username") or "user"),
        )
        if shared
        else conversation_state
    )
    generation = int(visible_state.get("generation") or 1)
    decision = _codex_decide_model(
        prompt=prompt,
        requested_model=model,
        rollout_key=f"{sessao.get('client_id')}:{conversation_id}",
        channel_metadata=metadata,
    )
    cwd = _codex_readonly_cwd_for_session(sessao, conversation_id)
    attachment_ids, attachment_paths = _codex_resolve_attachment_ids(
        payload.attachments, sessao, conversation_id
    )
    reference_paths = _codex_resolve_readonly_references(
        list(payload.reference_paths or []) + list(payload.paths or []),
        sessao,
        conversation_id,
    )
    paths = list(dict.fromkeys([*attachment_paths, *reference_paths]))
    goal = _codex_clean_text(payload.goal, 1200) if is_full else ""
    screen = _codex_normalizar_screen_context(payload.screen_context)
    mutable = _codex_prompt_pede_alteracao(prompt)
    if origin == "whatsapp" and mutable:
        metadata["requires_app_confirmation"] = True
    sandbox = "read_only"
    scope = _codex_build_scope(
        prompt=prompt, sandbox=sandbox, paths=paths, screen_context=screen, cwd=cwd
    )
    return {
        "prompt": prompt,
        "is_full": is_full,
        "origin": origin,
        "channel_metadata": metadata,
        "trusted_model_config": trusted,
        "query_policy": query_policy,
        "whatsapp_query_only": whatsapp_query_only,
        "whatsapp_full_access": False,
        "external_safe_mode": external_safe_mode,
        "sandbox": sandbox,
        "model": decision.effective_model,
        "requested_model": model,
        "model_decision": decision,
        "reasoning_effort": reasoning_effort,
        "speed": speed,
        "service_tier": service_tier,
        "approval_profile": "read_only",
        "shared_continuity": shared,
        "conversation_id": conversation_id,
        "conversation_state": conversation_state,
        "conversation_generation": generation,
        "cwd": cwd,
        "attachment_ids": attachment_ids,
        "reference_paths": reference_paths,
        "paths": paths,
        "goal": goal,
        "screen_context": screen,
        "context_stats": _codex_context_stats(prompt, screen),
        "history": [],
        "mutable_intent": mutable,
        "guidance_applied": [],
        "scope": scope,
    }


def _thread_context(ctx: dict[str, Any], sessao: dict[str, Any]) -> None:
    ctx.update(thread_decision=None, task_thread_id="", thread_scope={})
    if not ctx["is_full"]:
        return
    screen_identity = _codex_agent_guidance_context("", ctx["screen_context"])
    store_scope = str(screen_identity.get("store") or "").strip()
    screen = ctx["screen_context"]
    thread_scope = {
        "store": store_scope,
        "store_mode": str(screen.get("store_mode") or "none").strip().lower(),
        "multi_store": screen.get("multi_store") is True,
        "sandbox": ctx["sandbox"],
        "modules": list(ctx["scope"].get("modules") or []),
        "explicit_paths": list(ctx["scope"].get("explicit_paths") or []),
        "agent_lane": str(
            ctx["channel_metadata"].get("agent_lane")
            or ctx["channel_metadata"].get("agent_role")
            or ""
        ),
    }
    decision = _codex_thread_decision(ctx, sessao, store_scope, thread_scope)
    task_thread_id = (
        str(ctx["conversation_state"].get("latest_thread_id") or "").strip()
        if decision.reuse_thread
        else ""
    )
    ctx.update(
        store_scope=store_scope,
        thread_scope=thread_scope,
        thread_decision=decision,
        task_thread_id=task_thread_id,
    )


def _codex_thread_decision(
    ctx: dict[str, Any],
    sessao: dict[str, Any],
    store_scope: str,
    thread_scope: dict[str, Any],
) -> Any:
    state = ctx["conversation_state"]
    return codex_turn_context.decide_conversation(
        {
            "thread_id": state.get("latest_thread_id") or "",
            "prompt_fingerprint": state.get("thread_prompt_fingerprint") or "",
            "schema_fingerprint": state.get("thread_schema_fingerprint") or "",
            "scope_fingerprint": state.get("thread_scope_fingerprint") or "",
            "conversation_key": state.get("thread_conversation_key") or "",
            "updated_at": state.get("updated_at") or "",
        },
        surface="codex_sidebar_task",
        client_id=str(sessao.get("client_id") or "default"),
        store=store_scope,
        user=str(sessao.get("username") or "user"),
        subject=(
            f"{ctx['origin']}:{ctx['conversation_id']}:generation:"
            f"{ctx['conversation_generation']}"
        ),
        prompt_contract={"version": CODEX_SIDEBAR_TASK_PROMPT_VERSION},
        schema_contract={
            "version": CODEX_SIDEBAR_TASK_SCHEMA_VERSION,
            "shared_contract_hash": codex_turn_context.CONTRACT_HASH,
        },
        scope=thread_scope,
    )


def _plan_context(
    ctx: dict[str, Any], payload: CodexTaskRequest, sessao: dict[str, Any]
) -> Optional[dict[str, Any]]:
    metadata = ctx["channel_metadata"]
    request_id = str(
        payload.request_id
        or metadata.get("message_id")
        or metadata.get("request_id")
        or ""
    ).strip()
    idempotency_key = codex_agent_runtime.make_idempotency_key(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or "user"),
        channel=ctx["origin"],
        conversation_id=ctx["conversation_id"],
        conversation_generation=ctx["conversation_generation"],
        request_id=request_id,
        message=ctx["prompt"],
    )
    existing_plan = codex_assistant_storage.codex_assistant_agent_plan_get(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        idempotency_key=idempotency_key,
    )
    if isinstance(existing_plan, dict) and existing_plan.get("task_id"):
        existing_task = _codex_load_task(str(existing_plan.get("task_id") or ""))
        if (
            isinstance(existing_task, dict)
            and str(existing_task.get("client_id") or "")
            == str(sessao.get("client_id") or "")
            and str(existing_task.get("created_by") or "").strip().lower()
            == str(sessao.get("username") or "").strip().lower()
        ):
            return {
                "success": True,
                "task": _codex_public_task(existing_task),
                "idempotent_replay": True,
            }
    task_id = uuid.uuid4().hex
    plan = codex_agent_runtime.create_plan(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        task_id=task_id,
        conversation_id=ctx["conversation_id"],
        conversation_generation=ctx["conversation_generation"],
        username=str(sessao.get("username") or ""),
        channel=ctx["origin"],
        message=ctx["prompt"],
        mutable=bool(ctx["mutable_intent"]),
        idempotency_key=idempotency_key,
        guidance_applied=ctx["guidance_applied"],
    )
    operational: dict[str, Any] = {}
    approval_required = False
    if ctx["is_full"] and ctx["mutable_intent"] and not ctx["whatsapp_query_only"]:
        operational = codex_actions.create_proposal(
            client_id=str(sessao.get("client_id") or "default"),
            username=str(sessao.get("username") or ""),
            message=ctx["prompt"],
            screen_context=ctx["screen_context"],
            history=[],
            conversation_id=ctx["conversation_id"],
            conversation_generation=ctx["conversation_generation"],
            plan_id=str(plan.get("plan_id") or ""),
            task_id=task_id,
            channel=ctx["origin"],
            wa_id_hash=(
                _codex_hmac_identifier(
                    metadata.get("wa_id"), namespace="whatsapp_phone"
                )
                if ctx["origin"] == "whatsapp"
                else ""
            ),
            idempotency_key=idempotency_key,
        )
        if operational.get("matched") is True:
            approval_required = not bool(operational.get("needs_input"))
    ctx.update(
        request_id=request_id,
        idempotency_key=idempotency_key,
        task_id=task_id,
        plan=plan,
        operational_action=operational,
        approval_required=approval_required,
    )
    return None


def _resolve_initial_state(ctx: dict[str, Any], sessao: dict[str, Any]) -> None:
    shared_intake: dict[str, Any] = {}
    shared_decision: dict[str, Any] = {}
    if ctx["shared_continuity"] and not ctx["approval_required"]:
        try:
            from backend.services import whatsapp_bridge

            shared_intake = whatsapp_bridge._shared_sidebar_conversation_turn(
                sessao, ctx["prompt"], ctx["screen_context"]
            )
            shared_decision = (
                shared_intake.get("decision")
                if isinstance(shared_intake.get("decision"), dict)
                else {}
            )
        except Exception:
            shared_intake = {}
            shared_decision = {}
    task_status = "awaiting_approval" if ctx["approval_required"] else "queued"
    required_input: list[Any] = []
    proposal: dict[str, Any] = {}
    initial_response = ""
    operational = ctx["operational_action"]
    if operational.get("matched") is True:
        required_input = list(operational.get("missing_params") or [])
        proposal = (
            operational.get("proposal")
            if isinstance(operational.get("proposal"), dict)
            else {}
        )
        if required_input:
            task_status = "awaiting_input"
            initial_response = str(
                operational.get("message")
                or "Preciso de mais dados antes de preparar a acao."
            )
        elif proposal:
            task_status = "awaiting_approval"
            initial_response = str(
                proposal.get("summary") or "Revise e confirme a acao proposta."
            )
    shared_action = str(shared_decision.get("action") or "").strip().lower()
    shared_reply = str(shared_decision.get("reply_text") or "").strip()
    if (
        shared_action in {"reply", "request_information"}
        and shared_reply
        and not required_input
        and not proposal
    ):
        task_status = "completed"
        initial_response = shared_reply
    if required_input:
        ctx["plan"] = codex_agent_runtime.transition_plan(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            str(ctx["plan"].get("plan_id") or ""),
            "aguardando_dados",
            current_step="preparar",
            required_input=required_input,
            details={"missing_params": required_input},
        )
    elif proposal:
        refreshed = codex_assistant_storage.codex_assistant_agent_plan_get(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            str(ctx["plan"].get("plan_id") or ""),
        )
        if isinstance(refreshed, dict):
            ctx["plan"] = refreshed
    ctx.update(
        shared_intake=shared_intake,
        shared_decision=shared_decision,
        shared_action=shared_action,
        task_status=task_status,
        required_input=required_input,
        proposal=proposal,
        initial_response=initial_response,
    )


def _deadline_context(ctx: dict[str, Any], sessao: dict[str, Any]) -> None:
    metadata = ctx["channel_metadata"]
    enabled = bool(
        str(ctx["origin"] or "").strip().lower() != "whatsapp"
        and metadata.get("deadline_enabled") is not False
    )
    default = 600 if _codex_agent_is_report_request(ctx["prompt"]) else 180
    if enabled:
        try:
            seconds = int(metadata.get("deadline_seconds") or default)
        except (TypeError, ValueError):
            seconds = default
        seconds = max(30, min(seconds, 600))
    else:
        seconds = 0
    decision = ctx["thread_decision"]
    if (
        ctx["is_full"]
        and decision is not None
        and ctx["thread_scope"].get("sandbox") != ctx["sandbox"]
    ):
        ctx["thread_scope"]["sandbox"] = ctx["sandbox"]
        decision = _codex_thread_decision(
            ctx, sessao, ctx["store_scope"], ctx["thread_scope"]
        )
        ctx["task_thread_id"] = (
            str(ctx["conversation_state"].get("latest_thread_id") or "").strip()
            if decision.reuse_thread
            else ""
        )
    ctx.update(
        deadline_enabled=enabled,
        deadline_seconds=seconds,
        thread_decision=decision,
    )


def _task_record(
    ctx: dict[str, Any], payload: CodexTaskRequest, sessao: dict[str, Any]
) -> dict[str, Any]:
    decision = ctx["thread_decision"]
    plan = ctx["plan"]
    metadata = ctx["channel_metadata"]
    trace_id = uuid.uuid4().hex
    task = {
        "task_id": ctx["task_id"], "trace_id": trace_id, "status": ctx["task_status"], "sandbox": ctx["sandbox"], "cwd": ctx["cwd"], "thread_id": ctx["task_thread_id"], "thread_reused": bool(decision and decision.reuse_thread), "thread_restart_reasons": list(decision.restart_reasons) if decision else [], "thread_prompt_fingerprint": decision.prompt_fingerprint if decision else "", "thread_schema_fingerprint": decision.schema_fingerprint if decision else "", "thread_scope_fingerprint": decision.scope_fingerprint if decision else "", "thread_conversation_key": decision.conversation_key if decision else "", "thread_prompt_version": CODEX_SIDEBAR_TASK_PROMPT_VERSION if ctx["is_full"] else "", "thread_schema_version": CODEX_SIDEBAR_TASK_SCHEMA_VERSION if ctx["is_full"] else "", "conversation_id": ctx["conversation_id"], "conversation_generation": ctx["conversation_generation"], "shared_responder": bool(ctx["shared_continuity"]), "shared_responder_action": ctx["shared_action"][:40], "shared_authorization_fingerprint": str(ctx["shared_intake"].get("authorization_fingerprint") or "")[:64],
        "prompt": ctx["prompt"], "model": ctx["model"], "requested_model": ctx["requested_model"], "effective_model": ctx["model"], "model_category": ctx["model_decision"].category, "model_policy_version": ctx["model_decision"].policy_version, "model_reason_code": ctx["model_decision"].reason_code, "model_rerouted": ctx["model_decision"].rerouted, "approval_mode": ctx["approval_profile"], "reasoning_effort": ctx["reasoning_effort"], "reasoning_level": str(metadata.get("reasoning_level") or ctx["reasoning_effort"]), "reasoning_policy": str(metadata.get("reasoning_policy") or "fixed")[:40], "reasoning_max": str(metadata.get("reasoning_max") or ctx["reasoning_effort"])[:20], "orchestration_profile": str(metadata.get("orchestration_profile") or "default")[:80], "agent_role": str(metadata.get("agent_role") or "")[:40], "agent_lane": str(metadata.get("agent_lane") or metadata.get("agent_role") or "")[:40], "parent_job_id": str(metadata.get("parent_job_id") or "")[:100], "job_group_id": str(metadata.get("job_group_id") or "")[:100], "subtask_id": str(metadata.get("subtask_id") or "")[:100], "logical_subtask_id": str(metadata.get("logical_subtask_id") or metadata.get("subtask_id") or "")[:100], "current_attempt": max(1, int(metadata.get("attempt") or 1)), "attempt_task_ids": [], "retry_count": 0, "retry_reason": "", "next_retry_at_epoch": 0, "handoff_status": str(metadata.get("handoff_status") or "")[:60], "last_conversation_tick_at": str(metadata.get("last_conversation_tick_at") or "")[:40], "delivery_state": str(metadata.get("delivery_state") or "pending")[:40],
        "tool_protocol": "typed_catalog_text_v1", "mcp_migration": {"target": "jk_system_mcp", "native_enabled": False, "native_active": False, "legacy_parser_fallback": True, "disabled_reason": "data_selection_cutover"}, "speed": ctx["speed"], "service_tier": ctx["service_tier"] or "", "goal": ctx["goal"], "planning_mode": bool(payload.planning_mode) if ctx["is_full"] else False, "attachments": ctx["attachment_ids"], "reference_paths": ctx["reference_paths"], "paths": ctx["paths"], "scope": ctx["scope"], "scope_violations": [], "screen_context": ctx["screen_context"], "history": ctx["history"], "context_stats": ctx["context_stats"], "conversation_summary": {}, "conversation_compaction": {}, "agent_mode": _codex_agent_mode_enabled(), "plan_id": str(plan.get("plan_id") or ""), "agent_state": str(plan.get("agent_state") or ("aguardando_dados" if ctx["required_input"] else "aguardando_aprovacao" if ctx["proposal"] else "entendendo")), "steps": list(plan.get("steps") or []), "current_step": str(plan.get("current_step") or ("aprovar" if ctx["proposal"] else "preparar" if ctx["required_input"] else "entender")), "required_input": ctx["required_input"], "proposal": ctx["proposal"], "guidance_applied": ctx["guidance_applied"], "verification": {}, "idempotency_key": ctx["idempotency_key"],
        "agent_steps": [], "tool_calls": [], "tool_results_summary": [], "sources": [], "warnings": [], "live_status": "Codex concluiu." if ctx["task_status"] == "completed" else "Tarefa criada.", "live_answer": "", "reasoning_summary": "", "live_plan": "", "token_usage": {}, "turn_id": "", "active_turn_id": "", "can_steer": False, "wait_reason": "queue" if ctx["task_status"] == "queued" else "" if ctx["task_status"] == "completed" else ctx["task_status"], "progress_events": [], "last_progress_at": "", "deadline_enabled": ctx["deadline_enabled"], "deadline_seconds": ctx["deadline_seconds"], "deadline_at": _codex_deadline_at(ctx["deadline_seconds"]) if ctx["deadline_enabled"] else "", "steer_events": [], "mutable_intent": ctx["mutable_intent"], "final_response": ctx["initial_response"], "error": "", "message_kind": "conversation" if ctx["shared_continuity"] else "", "memory_excluded": False, "logs": [], "created_at": _codex_now(), "started_at": _codex_now() if ctx["task_status"] == "completed" else "", "completed_at": _codex_now() if ctx["task_status"] == "completed" else "",
        "created_by": sessao["username"], "client_id": sessao["client_id"], "origin": ctx["origin"], "channel_message_id": str(metadata.get("message_id") or "")[:200], "channel_metadata": metadata, "external_safe_mode": ctx["external_safe_mode"], "whatsapp_full_access": ctx["whatsapp_full_access"], "whatsapp_query_only": ctx["whatsapp_query_only"], "query_policy": ctx["query_policy"], "trusted_model_config": ctx["trusted_model_config"], "access_mode": "query_only" if ctx["whatsapp_query_only"] else "read_only", "permissions": {str(key): value is True for key, value in (sessao.get("permissions") or {}).items() if str(key or "").strip()}, "approval_required": ctx["approval_required"], "approved": not bool(ctx["approval_required"] or ctx["required_input"] or ctx["proposal"]),
    }
    ctx["trace_id"] = trace_id
    return task


def _store_and_start(
    ctx: dict[str, Any], task: dict[str, Any], sessao: dict[str, Any]
) -> dict[str, Any]:
    task_id = ctx["task_id"]
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    try:
        telemetry = _codex_ai_telemetry_instance()
        telemetry.schedule_retention(str(sessao.get("client_id") or "default"))
        telemetry.start_trace(
            str(sessao.get("client_id") or "default"),
            trace_id=ctx["trace_id"],
            surface=ctx["origin"],
            category=ctx["model_decision"].category,
            requested_model=ctx["requested_model"],
            user_id=sessao.get("username"),
            store_id=ctx["query_policy"].get("store", ""),
            expected_spans=("intake", "selection", "provider", "finalization"),
        )
        telemetry.finish_span(
            str(sessao.get("client_id") or "default"),
            trace_id=ctx["trace_id"],
            span_id=f"{ctx['trace_id']}:intake",
            stage="intake",
            status="completed",
            duration_ms=0,
        )
    except Exception:
        pass
    decision = ctx["thread_decision"]
    if ctx["is_full"] and decision is not None:
        _codex_save_conversation_state(
            task,
            latest_thread_id=ctx["task_thread_id"],
            thread_prompt_fingerprint=decision.prompt_fingerprint,
            thread_schema_fingerprint=decision.schema_fingerprint,
            thread_scope_fingerprint=decision.scope_fingerprint,
            thread_conversation_key=decision.conversation_key,
            thread_restart_reason=",".join(decision.restart_reasons),
        )
    _codex_log(task, "Tarefa criada.")
    if not ctx["is_full"]:
        _codex_log(
            task,
            "Acesso do usuario limitado pelo servidor a leitura e aos modulos autorizados.",
        )
    if ctx["whatsapp_query_only"]:
        _codex_log(
            task,
            "Politica WhatsApp query_only aplicada a vendas/anuncios; aprovacao e mutacoes desabilitadas.",
        )
    if ctx["approval_required"]:
        _codex_log(task, "Aguardando confirmacao para executar com permissao mutavel.")
        if ctx["external_safe_mode"]:
            _codex_log(
                task,
                "Aprovacao pelo WhatsApp e proibida; informe modulo ou caminhos no aplicativo.",
            )
    elif ctx["task_status"] == "queued":
        _codex_start_thread(task_id)
    elif ctx["task_status"] == "completed":
        try:
            _codex_update_conversation_memory(task_id)
        except Exception:
            pass
    return {"success": True, "task": _codex_public_task(task)}


def _codex_create_task_for_session(
    payload: CodexTaskRequest,
    sessao: dict[str, Any],
    *,
    origin: str = "app",
    channel_metadata: Optional[dict[str, Any]] = None,
    trusted_model_config: bool = False,
) -> dict[str, Any]:
    ctx = _base_context(
        payload, sessao, origin, channel_metadata, trusted_model_config
    )
    _thread_context(ctx, sessao)
    replay = _plan_context(ctx, payload, sessao)
    if replay is not None:
        return replay
    _resolve_initial_state(ctx, sessao)
    _deadline_context(ctx, sessao)
    return _store_and_start(ctx, _task_record(ctx, payload, sessao), sessao)


__codex_dependencies__ = [
    "CODEX_SIDEBAR_TASK_PROMPT_VERSION",
    "CODEX_SIDEBAR_TASK_SCHEMA_VERSION",
    "CODEX_TASKS",
    "CODEX_TASKS_LOCK",
    "_codex_agent_guidance_context",
    "_codex_agent_is_report_request",
    "_codex_agent_mode_enabled",
    "_codex_ai_telemetry_instance",
    "_codex_base_info_dir",
    "_codex_build_scope",
    "_codex_clean_text",
    "_codex_cleanup_old_attachments",
    "_codex_context_stats",
    "_codex_deadline_at",
    "_codex_decide_model",
    "_codex_development_requires_desktop_detail",
    "_codex_enabled",
    "_codex_hmac_identifier",
    "_codex_load_or_create_conversation_state",
    "_codex_load_or_create_shared_conversation_state",
    "_codex_load_task",
    "_codex_log",
    "_codex_normalizar_model",
    "_codex_normalizar_reasoning_effort",
    "_codex_normalizar_sandbox",
    "_codex_normalizar_screen_context",
    "_codex_normalizar_service_tier",
    "_codex_normalizar_speed",
    "_codex_now",
    "_codex_persist_task",
    "_codex_prompt_pede_alteracao",
    "_codex_prompt_pede_desenvolvimento",
    "_codex_public_task",
    "_codex_readonly_cwd_for_session",
    "_codex_resolve_attachment_ids",
    "_codex_resolve_new_conversation_id",
    "_codex_resolve_readonly_references",
    "_codex_save_conversation_state",
    "_codex_sdk_installed",
    "_codex_shared_continuity_for_session",
    "_codex_start_thread",
    "_codex_update_conversation_memory",
    "_codex_whatsapp_query_policy",
]

__codex_exports__ = ["_codex_create_task_for_session"]
