"""Conversation-agent execution isolated from shared continuity and job control."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Optional

from backend.services import admin_usuarios_common, codex_whatsapp_agents
from backend.services.codex.console import security as console_security
from backend.services.codex.console import tasks as console_tasks
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
from backend.services.whatsapp import response_fallback as whatsapp_response_fallback
from backend.services.whatsapp.composition import BridgeDependencies, bind_component_namespace
from backend.services.whatsapp.data_selection_enforcement import _is_live_question_queue_request
from backend.services.whatsapp.orchestration.retry_coordinator import _pending_is_terminal_or_finalizing
from backend.services.whatsapp.orchestration.shared_continuity import (
    _dual_append_conversation_turn,
    _dual_apply_resolved_context,
    _dual_conversation_record,
    _dual_recent_conversation_context,
    _save_dual_conversation_record,
    _shared_authorization_fingerprint,
    _shared_turn_lock,
)


DUAL_AGENT_STATE_LOCK = threading.RLock()


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
            candidate_task = console_tasks.load(candidate_task_id) if candidate_task_id else None
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


def _current_shared_authorization(record: dict[str, Any]) -> tuple[list[str], str]:
    client_id = str(record.get("shared_client_id") or "").strip()
    username = str(record.get("shared_username") or "").strip().lower()
    if not client_id or not username:
        return [], ""
    permissions = dict(admin_usuarios_common._carregar_permissoes_usuario(username, client_id) or {})
    permissions.pop("context_hub_read_full", None)
    session = {
        "client_id": client_id,
        "username": username,
        "permissions": permissions,
        "is_full": permissions.get("full") is True,
    }
    stores = _whatsapp_session_stores(session)
    return stores, _shared_authorization_fingerprint(session, stores)


def _conversation_schema_fingerprint(runtime_contract: dict[str, Any]) -> str:
    return console_security.hmac_identifier(
        json.dumps(runtime_contract.get("schema") or {}, ensure_ascii=False, sort_keys=True, default=str),
        namespace="conversation_schema",
    )[-40:]


def _authorization_stale_decision(reason: str) -> dict[str, Any]:
    return {
        "action": "reply",
        "reply_text": (
            "Seu acesso mudou durante a consulta. Descartei o resultado anterior; "
            "envie a solicitacao novamente para eu consultar com o acesso atual."
        ),
        "response_provider": "authorization_guard",
        "thread_id": "",
        "thread_reused": False,
        "thread_reset_reason": reason,
        "authorization_stale_result": True,
    }


def _prepare_conversation_agent_run(
    state: dict[str, Any],
    conversation_id: str,
    *,
    event_type: str,
    user_message: str,
    runtime_contract: dict[str, Any],
    authorized_stores: Optional[list[str]],
    authorization_fingerprint: str,
    current_turn_context: Optional[dict[str, Any]],
) -> dict[str, Any]:
    prompt_contract = runtime_contract["prompt_contract"]
    schema_fingerprint = _conversation_schema_fingerprint(runtime_contract)
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        if str(conversation_id or "").startswith("bj_") and event_type != "user_message":
            current_stores, current_fingerprint = _current_shared_authorization(record)
            if current_fingerprint:
                authorized_stores = current_stores
                authorization_fingerprint = current_fingerprint
        thread_id = str(record.get("thread_id") or "")
        reset_reason = ""
        previous_authorization = str(record.get("authorization_fingerprint") or "")
        if previous_authorization and authorization_fingerprint and previous_authorization != authorization_fingerprint:
            thread_id = ""
            reset_reason = "authorization_scope_changed"
            record["recent_turns"] = []
            record["resolved_context"] = {}
            record["context_revision"] = 0
            if event_type == "worker_result":
                record.update({
                    "thread_id": "",
                    "thread_reused": False,
                    "thread_reset_reason": reset_reason,
                    "authorization_fingerprint": authorization_fingerprint,
                    "last_activity_at_epoch": time.time(),
                })
                _save_dual_conversation_record(state, conversation_id, record)
                return {"early_decision": _authorization_stale_decision(reset_reason)}
        if thread_id and (
            str(record.get("conversation_prompt_version") or record.get("prompt_version") or "")
            != str(prompt_contract.get("version") or "")
            or str(record.get("conversation_prompt_hash") or record.get("prompt_hash") or "")
            != str(prompt_contract.get("hash") or "")
            or str(record.get("conversation_schema_fingerprint") or "") != schema_fingerprint
        ):
            thread_id = ""
            reset_reason = "prompt_contract_changed"
        conversation_context = _dual_recent_conversation_context(
            record,
            current_user_message=user_message if event_type == "user_message" else "",
        )
        conversation_state = whatsapp_conversation_context.agent_state(
            record, authorized_stores=authorized_stores
        )
        turn_context = current_turn_context if isinstance(current_turn_context, dict) else {}
        if turn_context.get("store_mode") == "single" and str(turn_context.get("store") or ""):
            conversation_state = {
                **conversation_state,
                "store": str(turn_context.get("store") or ""),
                "store_mode": "single",
                "store_source": "sidebar_screen",
            }
        elif turn_context.get("store_mode") in {"none", "all"}:
            conversation_state = {
                **conversation_state,
                "store": "",
                "store_mode": str(turn_context.get("store_mode") or "none"),
                "store_source": "sidebar_screen",
            }
    return {
        "early_decision": None,
        "thread_id": thread_id,
        "reset_reason": reset_reason,
        "prompt_contract": prompt_contract,
        "schema_fingerprint": schema_fingerprint,
        "conversation_context": conversation_context,
        "conversation_state": conversation_state,
        "authorized_stores": authorized_stores,
        "authorization_fingerprint": authorization_fingerprint,
    }


def _invoke_conversation_runtime(
    candidate_thread_id: str,
    *,
    settings: dict[str, Any],
    event_type: str,
    user_message: str,
    active_job: Optional[dict[str, Any]],
    worker_result: Optional[dict[str, Any]],
    prepared: dict[str, Any],
    quoted_context: Optional[dict[str, Any]],
    ai_behavior: str,
    tick_index: int,
    client_id: str,
    telemetry_trace_id: str,
    origin_channel: str,
) -> dict[str, Any]:
    conversation_state = prepared["conversation_state"]
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
        conversation_context=prepared["conversation_context"],
        conversation_state=conversation_state,
        quoted_context=quoted_context,
        ai_behavior=ai_behavior,
        tick_index=tick_index,
        client_id=client_id,
        telemetry_trace_id=telemetry_trace_id,
        store_id=str(conversation_state.get("store") or ""),
        origin_channel=origin_channel,
    )
    return value if isinstance(value, dict) else {}


def _persist_conversation_agent_decision(
    state: dict[str, Any],
    conversation_id: str,
    decision: dict[str, Any],
    *,
    prepared: dict[str, Any],
    runtime_contract: dict[str, Any],
    codex_failure_count: int,
    event_type: str,
    quoted_context: Optional[dict[str, Any]],
    origin_channel: str,
) -> None:
    prompt_contract = prepared["prompt_contract"]
    authorization_fingerprint = prepared["authorization_fingerprint"]
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        clear_thread = bool(decision.get("fallback_terminal") or decision.get("fallback_after_codex_failures"))
        record.update({
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
            "conversation_schema_fingerprint": prepared["schema_fingerprint"],
            "schema_version": str(runtime_contract.get("schema_version") or "")[:120],
            "decision_contract_mode": str(runtime_contract.get("mode") or "v2")[:20],
            "context_chars": max(0, int(decision.get("context_chars") or 0)),
            "last_event_type": event_type,
            "last_activity_at_epoch": time.time(),
            "last_conversation_at": _now(),
            "authorization_fingerprint": str(
                authorization_fingerprint or record.get("authorization_fingerprint") or ""
            )[:64],
            "last_origin_channel": "app" if str(origin_channel or "").strip().lower() == "app" else "whatsapp",
        })
        reply_text = str(decision.get("reply_text") or "").strip()
        _materialize_agent_decision_context(
            record,
            decision,
            event_type=event_type,
            authorized_stores=prepared["authorized_stores"],
            quoted_context=quoted_context,
        )
        if reply_text:
            _dual_append_conversation_turn(record, role="assistant", text=reply_text, event_type=event_type)
        _save_dual_conversation_record(state, conversation_id, record)


def _run_conversation_agent_unlocked(
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
    origin_channel: str = "whatsapp",
    authorization_fingerprint: str = "",
    current_turn_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    settings = _whatsapp_dual_agent_settings(config)
    runtime_contract = codex_whatsapp_agents._decision_runtime_contract()
    prepared = _prepare_conversation_agent_run(
        state,
        conversation_id,
        event_type=event_type,
        user_message=user_message,
        runtime_contract=runtime_contract,
        authorized_stores=authorized_stores,
        authorization_fingerprint=authorization_fingerprint,
        current_turn_context=current_turn_context,
    )
    if prepared["early_decision"] is not None:
        return prepared["early_decision"]
    trace_id = uuid.uuid4().hex
    invoke = lambda thread_id: _invoke_conversation_runtime(
        thread_id,
        settings=settings,
        event_type=event_type,
        user_message=user_message,
        active_job=active_job,
        worker_result=worker_result,
        prepared=prepared,
        quoted_context=quoted_context,
        ai_behavior=ai_behavior,
        tick_index=tick_index,
        client_id=client_id,
        telemetry_trace_id=trace_id,
        origin_channel=origin_channel,
    )
    decision, failure_count, terminal_error = whatsapp_response_fallback.resolve_conversation_decision(
        invoke,
        initial_thread_id=prepared["thread_id"],
        config=config,
        client_id=client_id,
        event_type=event_type,
        user_message=user_message,
        worker_result=worker_result,
        conversation_state=prepared["conversation_state"],
    )
    decision = _enforce_dual_decision_effect(
        decision,
        event_type=event_type,
        active_job=active_job,
        user_message=user_message,
    )
    if terminal_error:
        RUNTIME_STATE["conversation_fallback_last_error"] = terminal_error
    if prepared["reset_reason"] and str(decision.get("response_provider") or "codex") == "codex":
        decision["thread_reused"] = False
        decision["thread_reset_reason"] = prepared["reset_reason"]
    _persist_conversation_agent_decision(
        state,
        conversation_id,
        decision,
        prepared=prepared,
        runtime_contract=runtime_contract,
        codex_failure_count=failure_count,
        event_type=event_type,
        quoted_context=quoted_context,
        origin_channel=origin_channel,
    )
    return decision


def _run_conversation_agent(
    config: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    with _shared_turn_lock(conversation_id):
        return _run_conversation_agent_unlocked(config, state, conversation_id, **kwargs)


_IMPLEMENTATIONS = {
    name: globals()[name]
    for name in (
        "_dual_append_conversation_turn",
        "_dual_conversation_record",
        "_dual_recent_conversation_context",
        "_save_dual_conversation_record",
        "_shared_authorization_fingerprint",
        "_dual_active_job_snapshot",
        "_dual_active_status_reply",
        "_enforce_dual_decision_effect",
        "_materialize_agent_decision_context",
        "_current_shared_authorization",
        "_conversation_schema_fingerprint",
        "_prepare_conversation_agent_run",
        "_invoke_conversation_runtime",
        "_persist_conversation_agent_decision",
        "_run_conversation_agent_unlocked",
        "_run_conversation_agent",
    )
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


__all__ = ["bind_bridge_dependencies", "_IMPLEMENTATIONS"]
