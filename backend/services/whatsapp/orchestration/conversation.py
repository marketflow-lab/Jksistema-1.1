"""Extracted WhatsApp bridge component: conversation."""
from __future__ import annotations
import re
import time
import uuid
from typing import Any, Optional
from backend.services.whatsapp import black_jhon_prompting
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import response_fallback as whatsapp_response_fallback
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services import (
    codex_console,
    codex_whatsapp_agents,
)
from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)
from backend.services.whatsapp.orchestration.retry_coordinator import _pending_is_terminal_or_finalizing
from backend.services.whatsapp.data_selection_enforcement import _is_live_question_queue_request
WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS
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


_dual_conversation_context_snapshot = whatsapp_conversation_context.snapshot
_dual_apply_resolved_context = whatsapp_conversation_context.apply_resolved_context


def _dual_confirm_conversation_context(
    state: dict[str, Any], conversation_id: str, resolved: dict[str, Any], *,
    authorized_stores: Optional[list[str]] = None, source: str = "interactive_selection",
    pin_next_turn: bool = False,
) -> dict[str, Any]:
    if not conversation_id:
        return {}
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        snapshot = _dual_apply_resolved_context(
            record, resolved, authorized_stores=authorized_stores, source=source,
        )
        if pin_next_turn:
            record["round_context_pin"] = dict(
                store=str(snapshot.get("store") or ""), store_mode=str(snapshot.get("store_mode") or "none"),
                created_at_epoch=time.time(),
            )
        _save_dual_conversation_record(state, conversation_id, record)
        return snapshot


def _dual_append_conversation_turn(
    record: dict[str, Any],
    *,
    role: str,
    text: Any,
    event_type: str = "",
) -> dict[str, Any]:
    """Persist a small, PII-minimized dialogue window independent of Codex threads."""
    return whatsapp_conversation_context.append_turn(
        record, role=role, text=text, event_type=event_type, at=_now(),
    )
def _dual_recent_conversation_context(
    record: dict[str, Any],
    *,
    current_user_message: str = "",
) -> list[dict[str, str]]:
    return whatsapp_conversation_context.recent_turns(
        record, current_user_message=current_user_message,
    )
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
        whatsapp_conversation_context.expire_turn_memory(value)
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
    client_id: str = "",
    username: str = "",
    subject_id: str = "",
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    def matches_scope(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        expected = {
            "client_id": str(client_id or "").strip(),
            "username": str(username or "").strip().casefold(),
            "subject_id": str(subject_id or "").strip(),
        }
        actual = {
            "client_id": str(value.get("client_id") or "").strip(),
            "username": str(value.get("username") or "").strip().casefold(),
            "subject_id": str(value.get("subject_id") or "").strip(),
        }
        return all(not expected[key] or actual[key] == expected[key] for key in expected)

    scoped_state = state
    if any(str(item or "").strip() for item in (client_id, username, subject_id)):
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        scoped_state = {
            **state,
            "pending_messages": {
                key: value for key, value in pending_messages.items() if matches_scope(value)
            },
        }
    message_id, pending, task = _active_pending_for_conversation(
        scoped_state,
        conversation_id,
        exclude_message_id=exclude_message_id,
    )
    if task and str(task.get("status") or "") != "running":
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        for candidate_message_id, candidate_pending in pending_messages.items():
            if not isinstance(candidate_pending, dict) or str(candidate_pending.get("kind") or "") != "dual_worker":
                continue
            if not matches_scope(candidate_pending):
                continue
            if _pending_is_terminal_or_finalizing(candidate_pending):
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


def _dual_active_status_reply(active_job: dict[str, Any]) -> str:
    if not active_job:
        return "Nao ha nenhuma consulta ativa nesta conversa."
    status = str(active_job.get("status") or "").strip().lower()
    if status in {"queued", "manager_queued", "waiting_retry", "retry_starting"}:
        return "Sim. A consulta esta na fila e ainda nao foi concluida."
    return "Sim. A consulta esta em andamento e ainda nao foi concluida."


def _enforce_dual_decision_effect(
    decision: dict[str, Any],
    *,
    event_type: str,
    active_job: Optional[dict[str, Any]],
    user_message: str = "",
) -> dict[str, Any]:
    """Bind V3 semantics to effects before context or job state is changed."""

    result = dict(decision or {})
    if event_type != "user_message":
        return result
    relation = str(result.get("relation_to_active_job") or "").strip().lower()
    intent_id = str(result.get("intent_id") or "").strip().lower()
    action = str(result.get("action") or "").strip().lower()
    has_active = bool(active_job)
    if _is_live_question_queue_request(user_message):
        result.update({
            "intent_id": "mercado_livre.question",
            "intent_kind": "query",
            "action": "queue" if has_active else "delegate",
            "relation_to_active_job": "new_parallel" if has_active else "none",
            "answer_basis": "unavailable",
            "data_requirement": "required",
            "response_mode": "task_delegation",
            "context_operations": [
                {"field": "sku", "operation": "clear", "value": "", "source": "current_turn", "confidence": "high"},
                {"field": "mlb", "operation": "clear", "value": "", "source": "current_turn", "confidence": "high"},
            ],
            "resolved_context": {},
            "reply_text": "Vou verificar as perguntas em aberto para responder.",
            "job_title": "Perguntas em aberto do Mercado Livre",
            "job_prompt": str(user_message or "Tem perguntas?").strip()[:12000],
            "subtasks": [],
        })
        return result
    if relation == "status" or intent_id == "system.task_status":
        result.update({
            "action": "reply",
            "relation_to_active_job": "status",
            "answer_basis": "active_job" if has_active else "unavailable",
            "data_requirement": "none",
            "response_mode": "status_update",
            "context_operations": [],
            "resolved_context": {},
            "reply_text": _dual_active_status_reply(dict(active_job or {})),
        })
        return result
    if relation == "cancel" or intent_id == "system.task_control" and action == "cancel_job":
        if has_active:
            result.update({"action": "cancel_job", "data_requirement": "none", "context_operations": []})
        else:
            result.update({
                "action": "reply",
                "answer_basis": "unavailable",
                "data_requirement": "none",
                "context_operations": [],
                "resolved_context": {},
                "reply_text": "Nao ha nenhuma consulta ativa para cancelar nesta conversa.",
            })
        return result
    if relation == "new_parallel" and action in {"delegate", "queue", "steer"}:
        result["action"] = "queue"
    elif relation in {"followup", "correction"} and action in {"delegate", "queue", "steer"}:
        result["action"] = "steer" if has_active else "delegate"
    elif action == "steer":
        # Never alter an active job unless the semantic relation confirms it.
        result["action"] = "queue" if has_active else "delegate"
    elif action == "cancel_job":
        result.update({
            "action": "reply",
            "answer_basis": "unavailable",
            "data_requirement": "none",
            "context_operations": [],
            "reply_text": "Nao cancelei nenhuma consulta porque o pedido de cancelamento nao ficou confirmado.",
        })
    if not has_active and str(result.get("answer_basis") or "").strip().lower() == "active_job":
        result["answer_basis"] = "unavailable"
    return result


def _materialize_agent_decision_context(
    record: dict[str, Any], decision: dict[str, Any], *, event_type: str,
    authorized_stores: Optional[list[str]], quoted_context: Optional[dict[str, Any]],
) -> None:
    relation = str(decision.get("relation_to_active_job") or "").strip().lower()
    intent_id = str(decision.get("intent_id") or "").strip().lower()
    action = str(decision.get("action") or "").strip().lower()
    if event_type == "user_message" and (
        relation in {"status", "cancel"}
        or intent_id in {"system.task_status", "system.task_control"}
        or action == "cancel_job"
    ):
        resolved_context = whatsapp_conversation_context.request_context({}, {})
        decision["context_operations"] = []
        decision["resolved_context"] = resolved_context
        decision["effective_context"] = dict(resolved_context)
        decision["quoted_context"] = dict(quoted_context or {})
        return
    if event_type == "user_message":
        proposed_payload = dict(decision.get("resolved_context") or {})
        if "context_operations" in decision:
            proposed_payload["context_operations"] = list(decision.get("context_operations") or [])
        proposed_context = whatsapp_conversation_context.merge_round_pin(
            proposed_payload, record.pop("round_context_pin", None),
        )
        if "context_operations" in proposed_context:
            memory_context = whatsapp_conversation_context.apply_context_operations(
                record, proposed_context.get("context_operations"),
                authorized_stores=authorized_stores, source="codex_conversation",
            )
        else:
            memory_context = _dual_apply_resolved_context(
                record, proposed_context,
                authorized_stores=authorized_stores, source="codex_conversation",
            )
        resolved_context = whatsapp_conversation_context.request_context(
            proposed_context, memory_context,
        )
        decision["context_operations"] = list(resolved_context.get("context_operations") or [])
    else:
        resolved_context = whatsapp_conversation_context.request_context({}, {})
    decision["resolved_context"] = resolved_context
    decision["effective_context"] = dict(resolved_context)
    decision["quoted_context"] = dict(quoted_context or {})


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
    authorized_stores: Optional[list[str]] = None,
    client_id: str = "",
    quoted_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    settings = _whatsapp_dual_agent_settings(config)
    runtime_contract = codex_whatsapp_agents._decision_runtime_contract()
    prompt_contract = runtime_contract["prompt_contract"]
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        thread_id = str(record.get("thread_id") or "")
        initial_thread_reset_reason = ""
        if thread_id and (
            str(record.get("conversation_prompt_version") or record.get("prompt_version") or "")
            != str(prompt_contract.get("version") or "")
            or str(record.get("conversation_prompt_hash") or record.get("prompt_hash") or "")
            != str(prompt_contract.get("hash") or "")
        ):
            thread_id = ""
            initial_thread_reset_reason = "prompt_contract_changed"
        conversation_context = _dual_recent_conversation_context(
            record,
            current_user_message=user_message if event_type == "user_message" else "",
        )
        conversation_state = whatsapp_conversation_context.agent_state(
            record, authorized_stores=authorized_stores
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
            conversation_state=conversation_state,
            quoted_context=quoted_context,
            ai_behavior=ai_behavior,
            tick_index=tick_index,
            client_id=client_id,
            telemetry_trace_id=telemetry_trace_id,
            store_id=str(conversation_state.get("store") or ""),
        )
        return value if isinstance(value, dict) else {}

    telemetry_trace_id = uuid.uuid4().hex
    decision, codex_failure_count, terminal_error = whatsapp_response_fallback.resolve_conversation_decision(
        invoke,
        initial_thread_id=thread_id,
        config=config,
        client_id=client_id,
        event_type=event_type,
        user_message=user_message,
        worker_result=worker_result,
        conversation_state=conversation_state,
    )
    decision = _enforce_dual_decision_effect(
        decision,
        event_type=event_type,
        active_job=active_job,
        user_message=user_message,
    )
    if terminal_error:
        RUNTIME_STATE["conversation_fallback_last_error"] = terminal_error
    if initial_thread_reset_reason and str(decision.get("response_provider") or "codex") == "codex":
        decision["thread_reused"] = False
        decision["thread_reset_reason"] = initial_thread_reset_reason
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        clear_thread = bool(decision.get("fallback_terminal") or decision.get("fallback_after_codex_failures"))
        record.update(
            {
                "thread_id": "" if clear_thread else str(decision.get("thread_id") or record.get("thread_id") or "")[:200],
                "requested_model": str(decision.get("requested_model") or "")[:100],
                "effective_model": str(decision.get("effective_model") or "")[:100],
                "reasoning_effort": str(decision.get("reasoning_effort") or "")[:20],
                "speed": str(decision.get("speed") or "")[:20],
                "service_tier": str(decision.get("service_tier") or "")[:40],
                "response_provider": str(decision.get("response_provider") or "codex")[:40],
                "codex_failure_count": int(decision.get("codex_failure_count") or codex_failure_count),
                "thread_reused": decision.get("thread_reused") is True,
                "thread_reset_reason": str(decision.get("thread_reset_reason") or "")[:120],
                "prompt_version": str(prompt_contract.get("version") or "")[:120],
                "prompt_hash": str(prompt_contract.get("hash") or "")[:128],
                "conversation_prompt_version": str(prompt_contract.get("version") or "")[:120],
                "conversation_prompt_hash": str(prompt_contract.get("hash") or "")[:128],
                "schema_version": str(
                    runtime_contract.get("schema_version") or ""
                )[:120],
                "decision_contract_mode": str(runtime_contract.get("mode") or "v2")[:20],
                "context_chars": max(0, int(decision.get("context_chars") or 0)),
                "last_event_type": event_type,
                "last_activity_at_epoch": time.time(),
                "last_conversation_at": _now(),
            }
        )
        reply_text = str(decision.get("reply_text") or "").strip()
        _materialize_agent_decision_context(
            record, decision, event_type=event_type,
            authorized_stores=authorized_stores, quoted_context=quoted_context,
        )
        if reply_text:
            _dual_append_conversation_turn(record, role="assistant", text=reply_text, event_type=event_type)
        _save_dual_conversation_record(state, conversation_id, record)
    return decision

def _record_dual_user_message(state: dict[str, Any], conversation_id: str, request_text: str) -> None:
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record["last_inbound_at"] = _now()
        record["last_inbound_at_epoch"] = time.time()
        _dual_append_conversation_turn(record, role="user", text=request_text, event_type="user_message")
        _save_dual_conversation_record(state, conversation_id, record)


def _dual_server_data_policy(config: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Return authorization facts only; the selection agent owns semantics."""

    client_id = str(session.get("client_id") or "").strip()
    if not client_id:
        raise RuntimeError("data_selection_tenant_required")
    return {
        "read_only": True,
        "deny_approval": True,
        "authorized_stores": _whatsapp_session_stores(session),
        "context_hub_enabled": whatsapp_settings.context_hub_enabled_for_client(config, client_id),
    }

def _dual_agent_query_policy(
    config: dict[str, Any], session: dict[str, Any], resolved_context: Any,
) -> dict[str, Any]:
    """Bind the agent-selected context to server-owned authorization."""

    policy = _dual_server_data_policy(config, session)
    resolved = resolved_context if isinstance(resolved_context, dict) else {}
    authorized = list(policy.get("authorized_stores") or [])
    store_mode = str(resolved.get("store_mode") or "none").strip().lower()
    store = str(resolved.get("store") or "").strip()
    if store_mode == "single" and store in authorized:
        policy.update({"store_mode": "single", "store": store, "stores": [store]})
    elif store_mode == "all" and authorized:
        policy.update({"store_mode": "all", "store": "", "stores": authorized})
    else:
        policy.update({"store_mode": "none", "store": "", "stores": []})
    return policy

def _dual_initial_decision(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    active_snapshot: dict[str, Any],
    phone_ai_behavior: str,
    quoted_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return _run_conversation_agent(
        config, state, conversation_id,
        event_type="user_message", user_message=request_text,
        active_job=active_snapshot, ai_behavior=phone_ai_behavior,
        authorized_stores=_whatsapp_session_stores(session),
        client_id=str(session.get("client_id") or ""),
        quoted_context=(quoted_context or whatsapp_message.message_quoted_context(message)),
    )

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
        _resume_dual_pending_with_message(
            config, state, active_message_id, active_pending, prompt,
            request_context=decision.get("resolved_context"),
        )
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
    decision: dict[str, Any],
) -> Optional[dict[str, Any]]:
    del state, message, conversation_id, request_text, job_prompt
    return _dual_agent_query_policy(config, session, decision.get("resolved_context"))

def _queue_dual_function_manager(
    config: dict[str, Any],
    state: dict[str, Any],
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
    selection_enabled = settings.get("data_selection_enabled", settings.get("function_manager_enabled"))
    selection_required = settings.get(
        "data_selection_required_before_sol", settings.get("function_manager_required_before_sol")
    )
    if not selection_enabled or not selection_required:
        _post_message_result(
            config,
            message_id,
            {
                "status": "completed",
                "task_id": "",
                "response": (
                    "O seletor seguro de dados esta indisponivel agora. "
                    "Nenhuma fonte foi consultada; tente novamente em instantes."
                ),
            },
        )
        return True
    if not str(session.get("client_id") or "").strip():
        raise RuntimeError("data_selection_tenant_required")
    job_group_id = f"wa-{uuid.uuid4().hex[:20]}"
    now_epoch = time.time()
    conversation_anchors = {
        "recent_turns": _dual_recent_conversation_context(
            _dual_conversation_record(state, conversation_id),
            current_user_message=request_text,
        )[-6:],
        "resolved_context": dict(decision.get("resolved_context") or {}),
        "quoted_context": dict(decision.get("quoted_context") or {}),
    }
    pending = {
        "task_id": "", "kind": "dual_function_manager", "conversation_id": conversation_id,
        "conversation_agent_thread_id": str(decision.get("thread_id") or ""),
        "data_selection_thread_id": "", "function_manager_thread_id": "",
        "parent_job_id": job_group_id, "job_group_id": job_group_id, "job_title": job_title,
        "subject_id": subject, "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(), "request_text": request_text,
        "job_prompt": job_prompt, "query_policy": query_policy, "manager_query_policy": query_policy,
        "conversation_anchors": conversation_anchors,
        "data_selection_plan": {}, "phone_ai_behavior": phone_ai_behavior,
        "media": media or {}, "transcription": transcription or {},
        "screen_context": _mobile_screen_context(message_id, subject, media, transcription, query_policy),
        "session_permissions": {str(key): value is True for key, value in dict(session.get("permissions") or {}).items() if str(key or "").strip() and str(key) != "context_hub_read_full"},
        "binding_machine_id": str(session.get("machine_id") or config.get("machine_id") or "").strip(),
        "session_is_full": bool(session.get("is_full")), "created_at": _now(), "created_at_epoch": now_epoch,
        "job_state": "manager_queued", "manager_state": "queued", "data_selection_state": "queued",
        "manager_retry_count": 0,
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
        "session_permissions": {str(key): value is True for key, value in dict(session.get("permissions") or {}).items() if str(key or "").strip() and str(key) != "context_hub_read_full"},
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
    quoted_context: Optional[dict[str, Any]] = None,
) -> bool:
    active_message_id, active_pending, active_task, active_snapshot = _dual_active_job_snapshot(
        state,
        conversation_id,
        exclude_message_id=message_id,
        client_id=str(session.get("client_id") or ""),
        username=str(session.get("username") or ""),
        subject_id=subject,
    )
    _record_dual_user_message(state, conversation_id, request_text)
    decision = _dual_initial_decision(
        config, state, message, session, conversation_id, request_text,
        active_snapshot, phone_ai_behavior, quoted_context,
    )
    decision = _enforce_dual_decision_effect(
        decision,
        event_type="user_message",
        active_job=active_snapshot,
        user_message=request_text,
    )
    if decision.get("action") == "selection_sent":
        return True
    reply_text = str(decision.get("reply_text") or "").strip()
    action = str(decision.get("action") or "")
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
        request_text, job_prompt, decision,
    )
    if query_policy is None:
        return True
    query_policy = dict(query_policy or {})
    query_policy["context_hub_enabled"] = whatsapp_settings.context_hub_enabled_for_client(
        config, session.get("client_id")
    )
    if _queue_dual_function_manager(
        config, state, session, decision, query_policy,
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
    '_dual_conversation_record',
    '_dual_confirm_conversation_context',
    '_dual_append_conversation_turn',
    '_dual_recent_conversation_context',
    '_dual_remember_conversation_turn',
    '_save_dual_conversation_record',
    '_dual_active_job_snapshot',
    '_run_conversation_agent',
    '_dual_agent_query_policy',
    '_process_dual_codex_message'
))
_IMPLEMENTATIONS = {
    '_dual_conversation_record': _dual_conversation_record,
    '_dual_confirm_conversation_context': _dual_confirm_conversation_context,
    '_dual_append_conversation_turn': _dual_append_conversation_turn,
    '_dual_recent_conversation_context': _dual_recent_conversation_context,
    '_dual_remember_conversation_turn': _dual_remember_conversation_turn,
    '_save_dual_conversation_record': _save_dual_conversation_record,
    '_dual_active_job_snapshot': _dual_active_job_snapshot,
    '_run_conversation_agent': _run_conversation_agent,
    '_dual_agent_query_policy': _dual_agent_query_policy,
    '_process_dual_codex_message': _process_dual_codex_message
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
