"""Compatibility aggregator for Black Jhon conversation orchestration."""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from backend.services.codex.console import tasks as console_tasks
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
from backend.services.whatsapp import delivery as whatsapp_delivery
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)
from backend.services.whatsapp.orchestration import conversation_agent
from backend.services.whatsapp.orchestration import shared_continuity
from backend.services.whatsapp.orchestration.conversation_agent import (
    _dual_active_job_snapshot as _dual_active_job_snapshot_impl,
    _enforce_dual_decision_effect,
    _materialize_agent_decision_context,
    _run_conversation_agent,
)
from backend.services.whatsapp.orchestration.shared_continuity import (
    _discard_unconfirmed_assistant_reply,
    _dual_append_conversation_turn,
    _dual_confirm_conversation_context,
    _dual_conversation_record,
    _dual_recent_conversation_context,
    _dual_remember_conversation_turn,
    _ensure_shared_conversation_record,
    _record_dual_user_message,
    _record_shared_delivered_exchange,
    _replace_provisional_assistant_reply,
    _reset_shared_conversation_for_session,
    _rotate_shared_conversation,
    _save_dual_conversation_record,
    _shared_authorization_fingerprint,
    _shared_continuity_for_session,
    _shared_sidebar_conversation_turn,
    _shared_sidebar_worker_result,
    _shared_turn_lock,
)


def _dual_active_job_snapshot(*args: Any, **kwargs: Any):
    """Keep direct-import monkeypatches compatible with the extracted agent."""

    active_pending = globals().get("_active_pending_for_conversation")
    if callable(active_pending):
        conversation_agent._active_pending_for_conversation = active_pending
    return _dual_active_job_snapshot_impl(*args, **kwargs)


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
    authorized_stores = _whatsapp_session_stores(session)
    return _run_conversation_agent(
        config, state, conversation_id,
        event_type="user_message", user_message=request_text,
        active_job=active_snapshot, ai_behavior=phone_ai_behavior,
        authorized_stores=authorized_stores,
        client_id=str(session.get("client_id") or ""),
        quoted_context=(quoted_context or whatsapp_message.message_quoted_context(message)),
        origin_channel="whatsapp",
        authorization_fingerprint=_shared_authorization_fingerprint(session, authorized_stores),
    )


def _cancel_dual_active_job(
    state: dict[str, Any],
    session: dict[str, Any],
    active_message_id: str,
    active_pending: dict[str, Any],
) -> dict[str, Any]:
    outcomes: list[str] = []
    for task_id in _pending_task_ids(active_pending):
        result = console_tasks.cancel(
            task_id,
            session,
            cancel_source="whatsapp_conversation_agent",
        )
        result_task = result.get("task") if isinstance(result, dict) and isinstance(result.get("task"), dict) else {}
        task_status = str(result_task.get("status") or "")
        outcomes.append(task_status)
        console_tasks.update(
            task_id,
            handoff_status=(
                "cancel_requested_by_conversation_agent"
                if task_status == "cancel_requested"
                else "canceled_by_conversation_agent"
            ),
            delivery_state=task_status or "cancel_requested",
        )
    pending_cancel = any(status == "cancel_requested" for status in outcomes)
    if active_message_id and pending_cancel:
        active_pending["job_state"] = "cancel_requested"
        active_pending["delivery_state"] = "cancel_requested"
        _save_pending(state, active_message_id, active_pending)
    elif active_message_id:
        _remove_pending(state, active_message_id, status="canceled", reason="cancelado_pelo_usuario")
    return {"pending": pending_cancel, "outcomes": outcomes}


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
) -> tuple[bool, dict[str, Any]]:
    active_task_id = str(active_task.get("task_id") or "").strip()
    prompt = str(decision.get("job_prompt") or request_text)
    if str(active_pending.get("kind") or "") == "dual_function_manager" and active_message_id:
        resumed = _resume_dual_pending_with_message(
            config, state, active_message_id, active_pending, prompt,
            request_context=decision.get("resolved_context"),
        )
        if not resumed:
            return False, {}
        delivery = _post_message_result(
            config, message_id,
            {"status": "completed", "task_id": active_task_id, "response": reply_text},
        )
        return True, delivery
    outcomes: list[bool] = []
    for task_id in _pending_task_ids(active_pending):
        task = active_task if task_id == active_task_id else (console_tasks.load(task_id) or {})
        if str(task.get("status") or "") not in {"queued", "running"}:
            continue
        steer = console_tasks.steer(
            task_id, prompt, session,
            request_id=f"{message_id}:{task_id}", subject_id=subject, wa_id=phone,
        )
        outcomes.append(steer.get("accepted") is True)
    accepted = bool(outcomes) and all(outcomes)
    if accepted:
        delivery = _post_message_result(
            config, message_id,
            {"status": "completed", "task_id": active_task_id, "response": reply_text},
        )
        return True, delivery
    return False, {}


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
    conversation_id: str,
) -> tuple[bool, str]:
    if action == "cancel_job":
        provisional_reply = reply_text
        cancel_effect = {"pending": False, "outcomes": []}
        if active_task:
            cancel_effect = _cancel_dual_active_job(state, session, active_message_id, active_pending)
        reply_text = (
            "Solicitei o cancelamento da tarefa em execucao. Avisarei quando ela encerrar."
            if cancel_effect.get("pending") is True
            else "A tarefa foi cancelada."
        )
        delivery = _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        if whatsapp_delivery.delivery_receipt_confirmed(delivery):
            _replace_provisional_assistant_reply(state, conversation_id, provisional_reply, reply_text)
        else:
            _discard_unconfirmed_assistant_reply(state, conversation_id, provisional_reply)
        return True, action
    if action == "steer" and active_task:
        accepted, delivery = _steer_dual_active_job(
            config, state, session, decision, active_message_id, active_pending,
            active_task, request_text, message_id, subject, phone, reply_text,
        )
        if accepted:
            if not whatsapp_delivery.delivery_receipt_confirmed(delivery):
                _discard_unconfirmed_assistant_reply(state, conversation_id, reply_text)
            return True, action
        return False, "queue"
    if action == "steer":
        return False, "delegate"
    if action in {"reply", "request_information"}:
        delivery = _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        if not whatsapp_delivery.delivery_receipt_confirmed(delivery):
            _discard_unconfirmed_assistant_reply(state, conversation_id, reply_text)
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
        "session_permissions": {
            str(key): value is True
            for key, value in dict(session.get("permissions") or {}).items()
            if str(key or "").strip() and str(key) != "context_hub_read_full"
        },
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
        "session_permissions": {
            str(key): value is True
            for key, value in dict(session.get("permissions") or {}).items()
            if str(key or "").strip() and str(key) != "context_hub_read_full"
        },
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


def _process_dual_codex_message_unlocked(
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
        conversation_id,
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


def _process_dual_codex_message(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    **kwargs: Any,
) -> bool:
    conversation_id = str(kwargs.get("conversation_id") or "")
    with _shared_turn_lock(conversation_id):
        return _process_dual_codex_message_unlocked(config, state, message, **kwargs)


_COMPONENT_FUNCTIONS = frozenset((
    "_shared_continuity_for_session",
    "_ensure_shared_conversation_record",
    "_shared_sidebar_conversation_turn",
    "_shared_sidebar_worker_result",
    "_record_shared_delivered_exchange",
    "_discard_unconfirmed_assistant_reply",
    "_replace_provisional_assistant_reply",
    "_rotate_shared_conversation",
    "_reset_shared_conversation_for_session",
    "_dual_conversation_record",
    "_dual_confirm_conversation_context",
    "_dual_append_conversation_turn",
    "_dual_recent_conversation_context",
    "_dual_remember_conversation_turn",
    "_save_dual_conversation_record",
    "_dual_active_job_snapshot",
    "_run_conversation_agent",
    "_dual_agent_query_policy",
    "_process_dual_codex_message",
))
_IMPLEMENTATIONS = {
    "_shared_continuity_for_session": _shared_continuity_for_session,
    "_ensure_shared_conversation_record": _ensure_shared_conversation_record,
    "_shared_sidebar_conversation_turn": _shared_sidebar_conversation_turn,
    "_shared_sidebar_worker_result": _shared_sidebar_worker_result,
    "_record_shared_delivered_exchange": _record_shared_delivered_exchange,
    "_discard_unconfirmed_assistant_reply": _discard_unconfirmed_assistant_reply,
    "_replace_provisional_assistant_reply": _replace_provisional_assistant_reply,
    "_rotate_shared_conversation": _rotate_shared_conversation,
    "_reset_shared_conversation_for_session": _reset_shared_conversation_for_session,
    "_dual_conversation_record": _dual_conversation_record,
    "_dual_confirm_conversation_context": _dual_confirm_conversation_context,
    "_dual_append_conversation_turn": _dual_append_conversation_turn,
    "_dual_recent_conversation_context": _dual_recent_conversation_context,
    "_dual_remember_conversation_turn": _dual_remember_conversation_turn,
    "_save_dual_conversation_record": _save_dual_conversation_record,
    "_dual_active_job_snapshot": _dual_active_job_snapshot,
    "_run_conversation_agent": _run_conversation_agent,
    "_dual_agent_query_policy": _dual_agent_query_policy,
    "_process_dual_codex_message": _process_dual_codex_message,
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    shared_continuity.bind_bridge_dependencies(dependencies)
    conversation_agent.bind_bridge_dependencies(dependencies)
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
