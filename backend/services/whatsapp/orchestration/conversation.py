"""Extracted WhatsApp bridge component: conversation."""
from __future__ import annotations
import json
import re
import threading
import time
import uuid
from typing import Any, Optional
from backend.services.whatsapp import black_jhon_prompting
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import response_fallback as whatsapp_response_fallback
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import delivery as whatsapp_delivery
from backend.services import (
    admin_usuarios_common,
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

_SHARED_TURN_LOCKS_GUARD = threading.RLock()
_SHARED_TURN_LOCKS: dict[str, threading.RLock] = {}


def _shared_turn_lock(conversation_id: str) -> threading.RLock:
    key = str(conversation_id or "").strip()
    with _SHARED_TURN_LOCKS_GUARD:
        return _SHARED_TURN_LOCKS.setdefault(key, threading.RLock())


def _shared_authorization_fingerprint(
    session: dict[str, Any], authorized_stores: Optional[list[str]] = None,
) -> str:
    permissions = session.get("permissions") if isinstance(session.get("permissions"), dict) else {}
    payload = {
        "client_id": str(session.get("client_id") or "").strip(),
        "username": str(session.get("username") or "").strip().casefold(),
        "is_full": session.get("is_full") is True,
        "permissions": sorted(
            str(key)
            for key, value in permissions.items()
            if value is True
            and str(key).strip()
            and str(key) != "context_hub_read_full"
        ),
        "stores": sorted(str(item).strip() for item in list(authorized_stores or []) if str(item).strip()),
    }
    return codex_console._codex_hmac_identifier(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        namespace="shared_authorization",
    )[-32:]


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


def _ensure_shared_conversation_record(
    config: dict[str, Any],
    state: dict[str, Any],
    session: dict[str, Any],
    *,
    phone: str,
    subject_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Migrate the primary phone's legacy responder state into the shared identity."""

    if not str(conversation_id or "").startswith("bj_"):
        return _dual_conversation_record(state, conversation_id)
    client_id = str(session.get("client_id") or "").strip()
    username = str(session.get("username") or "").strip().casefold()
    primary = whatsapp_settings.primary_phone_setting(
        config,
        client_id=client_id,
        username=username,
        subject_id=subject_id,
    )
    if not primary:
        raise RuntimeError("shared_conversation_primary_binding_required")
    legacy_id = (
        codex_console._codex_canonical_conversation_id(
            client_id,
            username,
            channel="whatsapp",
            phone=phone,
        )
        if phone
        else ""
    )
    subject_fingerprint = codex_console._codex_hmac_identifier(
        subject_id,
        namespace="whatsapp_subject",
    )[-24:]
    with DUAL_AGENT_STATE_LOCK, BRIDGE_STATE_LOCK:
        conversations = (
            state.get("dual_agent_conversations")
            if isinstance(state.get("dual_agent_conversations"), dict)
            else {}
        )
        current = conversations.get(conversation_id)
        if not isinstance(current, dict):
            legacy = conversations.get(legacy_id) if legacy_id else None
            current = dict(legacy) if isinstance(legacy, dict) else {}
        previous_subject = str(current.get("primary_subject_fingerprint") or "")
        if previous_subject and previous_subject != subject_fingerprint:
            current.update(
                {
                    "thread_id": "",
                    "authorization_fingerprint": "",
                    "recent_turns": [],
                    "resolved_context": {},
                    "context_revision": 0,
                    "thread_reused": False,
                    "thread_reset_reason": "primary_subject_changed",
                    "continuity_generation": max(1, int(current.get("continuity_generation") or 1)) + 1,
                }
            )
        current.update(
            {
                "conversation_id": conversation_id,
                "conversation_queue_key": f"{conversation_id}:conversation",
                "worker_queue_key": f"{conversation_id}:worker",
                "shared_continuity": True,
                "shared_client_id": client_id,
                "shared_username": username,
                "primary_subject_fingerprint": subject_fingerprint,
            }
        )
        if not list(current.get("recent_turns") or []):
            app_state = codex_console._codex_load_or_create_conversation_state(
                client_id,
                username,
                channel="app",
            )
            for item in list(app_state.get("recent_messages") or [])[-12:]:
                if not isinstance(item, dict):
                    continue
                whatsapp_conversation_context.append_turn(
                    current,
                    role=str(item.get("role") or "user"),
                    text=item.get("text") or item.get("content") or "",
                    event_type="legacy_app_history",
                    at=_now(),
                )
        conversations[conversation_id] = current
        state["dual_agent_conversations"] = conversations
        _save_state(state)
        return dict(current)


def _shared_continuity_for_session(session: dict[str, Any]) -> dict[str, Any]:
    """Resolve shared continuity only for an active, exact primary binding."""

    client_id = str(session.get("client_id") or "").strip()
    username = str(session.get("username") or "").strip().casefold()
    if not client_id or not username:
        return {}
    config = _load_config()
    cached_primary = whatsapp_settings.primary_phone_setting(
        config,
        client_id=client_id,
        username=username,
    )
    primary = cached_primary
    try:
        worker = _worker_health(config)
    except Exception:
        worker = {}
    capabilities = {
        str(item or "").strip()
        for item in list(worker.get("gateway_capabilities") or [])
        if str(item or "").strip()
    } if isinstance(worker, dict) else set()
    if worker.get("success") is True and "primary_binding_v1" in capabilities:
        binding = whatsapp_settings.primary_phone_binding(
            config,
            worker.get("bindings") or [],
            client_id=client_id,
            username=username,
            machine_id=str(session.get("machine_id") or config.get("machine_id") or ""),
        )
        if binding:
            primary = {
                **dict(binding.get("notification_settings") or {}),
                "subject_id": str(binding.get("subject_id") or ""),
                "phone_number": str(binding.get("phone_number") or ""),
                "is_primary": True,
            }
            existing_settings = dict(config.get("phone_notification_settings") or {})
            settings_by_phone = dict(existing_settings)
            subject_id = str(primary.get("subject_id") or "")
            cached = dict(settings_by_phone.get(subject_id) or {})
            cached.update(
                {
                    "subject_id": subject_id,
                    "client_id": client_id,
                    "username": username,
                    "phone_number": str(primary.get("phone_number") or cached.get("phone_number") or ""),
                    "is_primary": True,
                }
            )
            settings_by_phone[subject_id] = cached
            selected_settings = whatsapp_settings.select_primary_phone_setting(
                settings_by_phone,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
                enabled=True,
            )
            if selected_settings != existing_settings:
                config["phone_notification_settings"] = selected_settings
                _save_config(config)
        else:
            if cached_primary:
                settings_by_phone = whatsapp_settings.select_primary_phone_setting(
                    config.get("phone_notification_settings"),
                    subject_id=str(cached_primary.get("subject_id") or ""),
                    client_id=client_id,
                    username=username,
                    enabled=False,
                )
                if settings_by_phone != config.get("phone_notification_settings"):
                    config["phone_notification_settings"] = settings_by_phone
                    _save_config(config)
            primary = {}
    if not primary:
        return {}
    subject_id = str(primary.get("subject_id") or "").strip()
    phone = re.sub(r"\D+", "", str(primary.get("phone_number") or ""))
    if not subject_id:
        return {}
    return {
        "conversation_id": codex_console._codex_shared_conversation_id(client_id, username),
        "client_id": client_id,
        "username": username,
        "subject_id": subject_id,
        "phone": phone,
    }


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
    return codex_console._codex_hmac_identifier(
        json.dumps(runtime_contract.get("schema") or {}, ensure_ascii=False, sort_keys=True, default=str),
        namespace="conversation_schema",
    )[-40:]


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
        initial_thread_reset_reason = ""
        previous_authorization = str(record.get("authorization_fingerprint") or "")
        if previous_authorization and authorization_fingerprint and (
            previous_authorization != authorization_fingerprint
        ):
            thread_id = ""
            initial_thread_reset_reason = "authorization_scope_changed"
            record["recent_turns"] = []
            record["resolved_context"] = {}
            record["context_revision"] = 0
            if event_type == "worker_result":
                record.update(
                    {
                        "thread_id": "",
                        "thread_reused": False,
                        "thread_reset_reason": initial_thread_reset_reason,
                        "authorization_fingerprint": authorization_fingerprint,
                        "last_activity_at_epoch": time.time(),
                    }
                )
                _save_dual_conversation_record(state, conversation_id, record)
                return {
                    "action": "reply",
                    "reply_text": (
                        "Seu acesso mudou durante a consulta. Descartei o resultado anterior; "
                        "envie a solicitacao novamente para eu consultar com o acesso atual."
                    ),
                    "response_provider": "authorization_guard",
                    "thread_id": "",
                    "thread_reused": False,
                    "thread_reset_reason": initial_thread_reset_reason,
                    "authorization_stale_result": True,
                }
        if thread_id and (
            str(record.get("conversation_prompt_version") or record.get("prompt_version") or "")
            != str(prompt_contract.get("version") or "")
            or str(record.get("conversation_prompt_hash") or record.get("prompt_hash") or "")
            != str(prompt_contract.get("hash") or "")
            or str(record.get("conversation_schema_fingerprint") or "") != schema_fingerprint
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
            origin_channel=origin_channel,
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
                "conversation_schema_fingerprint": schema_fingerprint,
                "schema_version": str(
                    runtime_contract.get("schema_version") or ""
                )[:120],
                "decision_contract_mode": str(runtime_contract.get("mode") or "v2")[:20],
                "context_chars": max(0, int(decision.get("context_chars") or 0)),
                "last_event_type": event_type,
                "last_activity_at_epoch": time.time(),
                "last_conversation_at": _now(),
                "authorization_fingerprint": str(
                    authorization_fingerprint or record.get("authorization_fingerprint") or ""
                )[:64],
                "last_origin_channel": (
                    "app" if str(origin_channel or "").strip().lower() == "app" else "whatsapp"
                ),
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


def _run_conversation_agent(
    config: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    # A mesma thread pode receber Sidebar e WhatsApp quase simultaneamente.
    # Serializar capture -> run -> persist evita dois forks a partir do mesmo turno.
    with _shared_turn_lock(conversation_id):
        return _run_conversation_agent_unlocked(
            config,
            state,
            conversation_id,
            **kwargs,
        )


def _sidebar_current_turn_context(
    screen_context: Any,
    authorized_stores: list[str],
) -> dict[str, Any]:
    context = screen_context if isinstance(screen_context, dict) else {}
    selection = context.get("selection") if isinstance(context.get("selection"), dict) else {}
    filters = (
        context.get("filtros")
        if isinstance(context.get("filtros"), dict)
        else context.get("filters")
        if isinstance(context.get("filters"), dict)
        else {}
    )
    requested_store = next(
        (
            str(value).strip()
            for value in (
                selection.get("loja"), selection.get("store"),
                filters.get("loja"), filters.get("store"),
                context.get("loja"), context.get("store"),
            )
            if str(value or "").strip()
        ),
        "",
    )
    if requested_store:
        matches = whatsapp_intent.exact_store_matches(requested_store, authorized_stores)
        if len(matches) == 1:
            return {"store": matches[0], "store_mode": "single"}
        return {"store": "", "store_mode": "none", "invalid_store": True}
    raw_mode = str(context.get("store_mode") or "").strip().lower()
    if raw_mode == "all" and authorized_stores:
        return {"store": "", "store_mode": "all"}
    if raw_mode == "none":
        return {"store": "", "store_mode": "none"}
    return {}


def _shared_sidebar_conversation_turn(
    session: dict[str, Any],
    user_message: str,
    screen_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    continuity = _shared_continuity_for_session(session)
    if not continuity:
        return {}
    config = _load_config()
    state = _load_state()
    conversation_id = str(continuity.get("conversation_id") or "")
    authorized_stores = _whatsapp_session_stores(session)
    authorization_fingerprint = _shared_authorization_fingerprint(session, authorized_stores)
    current_turn_context = _sidebar_current_turn_context(screen_context, authorized_stores)
    setting = whatsapp_settings.primary_phone_setting(
        config,
        client_id=continuity.get("client_id"),
        username=continuity.get("username"),
        subject_id=continuity.get("subject_id"),
    )
    with _shared_turn_lock(conversation_id):
        _ensure_shared_conversation_record(
            config,
            state,
            session,
            phone=str(continuity.get("phone") or ""),
            subject_id=str(continuity.get("subject_id") or ""),
            conversation_id=conversation_id,
        )
        _record_dual_user_message(state, conversation_id, user_message)
        decision = _run_conversation_agent(
            config,
            state,
            conversation_id,
            event_type="user_message",
            user_message=user_message,
            ai_behavior=str(setting.get("ai_behavior") or ""),
            authorized_stores=authorized_stores,
            client_id=str(continuity.get("client_id") or ""),
            origin_channel="app",
            authorization_fingerprint=authorization_fingerprint,
            current_turn_context=current_turn_context,
        )
    return {
        "conversation_id": conversation_id,
        "subject_id": str(continuity.get("subject_id") or ""),
        "decision": decision,
        "authorization_fingerprint": authorization_fingerprint,
    }


def _shared_sidebar_worker_result(
    task: dict[str, Any],
    final_response: str,
    agent_trace: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    session = {
        "client_id": str(task.get("client_id") or ""),
        "username": str(task.get("created_by") or ""),
        "permissions": dict(task.get("permissions") or {}),
        "is_full": bool((task.get("permissions") or {}).get("full") is True),
    }
    continuity = _shared_continuity_for_session(session)
    if not continuity or str(task.get("conversation_id") or "") != str(continuity.get("conversation_id") or ""):
        return {}
    config = _load_config()
    state = _load_state()
    _ensure_shared_conversation_record(
        config,
        state,
        session,
        phone=str(continuity.get("phone") or ""),
        subject_id=str(continuity.get("subject_id") or ""),
        conversation_id=str(continuity.get("conversation_id") or ""),
    )
    normalized_task = dict(task)
    normalized_task["final_response"] = str(final_response or "")
    if isinstance(agent_trace, dict):
        normalized_task.update(
            {
                "tool_results_summary": list(agent_trace.get("tool_results_summary") or []),
                "sources": list(agent_trace.get("sources") or []),
                "warnings": list(agent_trace.get("warnings") or []),
            }
        )
    worker_result = codex_whatsapp_agents.normalize_worker_result(normalized_task)
    authorized_stores = _whatsapp_session_stores(session)
    decision = _run_conversation_agent(
        config,
        state,
        str(continuity.get("conversation_id") or ""),
        event_type="worker_result",
        user_message="",
        worker_result=worker_result,
        authorized_stores=authorized_stores,
        client_id=str(continuity.get("client_id") or ""),
        origin_channel="app",
        authorization_fingerprint=_shared_authorization_fingerprint(session, authorized_stores),
    )
    return decision


def _record_shared_delivered_exchange(
    *,
    client_id: str,
    username: str,
    phone: str,
    subject_id: str,
    prompt: str,
    response: str,
    event_id: str,
) -> dict[str, Any]:
    config = _load_config()
    primary = whatsapp_settings.primary_phone_setting(
        config,
        client_id=client_id,
        username=username,
        subject_id=subject_id,
    )
    if not primary:
        return {}
    prompt_text, _prompt_categories = whatsapp_conversation_context.sanitize_turn_text(prompt)
    response_text, _response_categories = whatsapp_conversation_context.sanitize_turn_text(response)
    if not prompt_text or not response_text:
        return {}
    return codex_console.codex_registrar_interacao_whatsapp_externa(
        client_id=client_id,
        username=username,
        phone=phone,
        prompt=prompt_text,
        response=response_text,
        model="black-jhon-conversation",
        source="whatsapp_message",
        call_id=event_id,
        shared_continuity=True,
        delivery_confirmed=True,
        subject_id=subject_id,
        message_type="text",
    )


def _discard_unconfirmed_assistant_reply(
    state: dict[str, Any],
    conversation_id: str,
    reply_text: str,
) -> None:
    sanitized, _categories = whatsapp_conversation_context.sanitize_turn_text(reply_text)
    with _shared_turn_lock(conversation_id), DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        turns = whatsapp_conversation_context.sanitize_turns(record.get("recent_turns"))
        if turns and turns[-1].get("role") == "assistant" and turns[-1].get("text") == sanitized:
            turns.pop()
        record.update(
            {
                "recent_turns": turns,
                "thread_id": "",
                "thread_reused": False,
                "thread_reset_reason": "delivery_unconfirmed",
            }
        )
        _save_dual_conversation_record(state, conversation_id, record)


def _replace_provisional_assistant_reply(
    state: dict[str, Any],
    conversation_id: str,
    provisional_reply: str,
    delivered_reply: str,
) -> None:
    """Keep local replay aligned when a control action rewrites the model reply.

    The native Codex thread already contains the provisional decision, so it must
    be rotated. The next turn rebuilds from the small confirmed turn window.
    """
    provisional_text, _provisional_categories = whatsapp_conversation_context.sanitize_turn_text(
        provisional_reply
    )
    delivered_text, _delivered_categories = whatsapp_conversation_context.sanitize_turn_text(
        delivered_reply
    )
    with _shared_turn_lock(conversation_id), DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        turns = whatsapp_conversation_context.sanitize_turns(record.get("recent_turns"))
        if (
            provisional_text
            and turns
            and turns[-1].get("role") == "assistant"
            and turns[-1].get("text") == provisional_text
        ):
            turns.pop()
        record["recent_turns"] = turns
        if delivered_text:
            _dual_append_conversation_turn(
                record,
                role="assistant",
                text=delivered_text,
                event_type="control_delivery",
            )
        record.update(
            {
                "thread_id": "",
                "thread_reused": False,
                "thread_reset_reason": "control_reply_rewritten",
            }
        )
        _save_dual_conversation_record(state, conversation_id, record)


def _rotate_shared_conversation(
    config: dict[str, Any],
    *,
    client_id: str,
    username: str,
    reason: str,
    clear_memory: bool = False,
) -> dict[str, Any]:
    del config
    conversation_id = codex_console._codex_shared_conversation_id(client_id, username)
    state = _load_state()
    with _shared_turn_lock(conversation_id), DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record.update(
            {
                "thread_id": "",
                "authorization_fingerprint": "",
                "thread_reused": False,
                "thread_reset_reason": str(reason or "shared_continuity_rotated")[:120],
                "continuity_generation": max(1, int(record.get("continuity_generation") or 1)) + 1,
                "last_activity_at_epoch": time.time(),
            }
        )
        if clear_memory:
            record["recent_turns"] = []
            record["resolved_context"] = {}
            record["context_revision"] = 0
        _save_dual_conversation_record(state, conversation_id, record)
        return dict(record)


def _reset_shared_conversation_for_session(session: dict[str, Any]) -> dict[str, Any]:
    continuity = _shared_continuity_for_session(session)
    if not continuity:
        return {}
    return _rotate_shared_conversation(
        _load_config(),
        client_id=str(continuity.get("client_id") or ""),
        username=str(continuity.get("username") or ""),
        reason="manual_reset",
        clear_memory=True,
    )

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
        result = codex_console.codex_cancelar_tarefa_para_sessao(
            task_id,
            session,
            cancel_source="whatsapp_conversation_agent",
        )
        result_task = result.get("task") if isinstance(result, dict) and isinstance(result.get("task"), dict) else {}
        task_status = str(result_task.get("status") or "")
        outcomes.append(task_status)
        codex_console._codex_update_task(
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
        delivery = _post_message_result(config, message_id, {"status": "completed", "task_id": active_task_id, "response": reply_text})
        return True, delivery
    outcomes: list[bool] = []
    for task_id in _pending_task_ids(active_pending):
        task = active_task if task_id == active_task_id else (codex_console._codex_load_task(task_id) or {})
        if str(task.get("status") or "") not in {"queued", "running"}:
            continue
        steer = codex_console.codex_complementar_tarefa_para_sessao(
            task_id, prompt, session,
            request_id=f"{message_id}:{task_id}", subject_id=subject, wa_id=phone,
        )
        outcomes.append(steer.get("accepted") is True)
    accepted = bool(outcomes) and all(outcomes)
    if accepted:
        delivery = _post_message_result(config, message_id, {"status": "completed", "task_id": active_task_id, "response": reply_text})
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
    active_task_id = str(active_task.get("task_id") or "").strip() if active_task else ""
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
            _replace_provisional_assistant_reply(
                state,
                conversation_id,
                provisional_reply,
                reply_text,
            )
            _record_shared_delivered_exchange(
                client_id=str(session.get("client_id") or ""),
                username=str(session.get("username") or ""),
                phone=phone,
                subject_id=subject,
                prompt=request_text,
                response=reply_text,
                event_id=message_id,
            )
        else:
            _discard_unconfirmed_assistant_reply(state, conversation_id, provisional_reply)
        return True, action
    if action == "steer" and active_task:
        accepted, delivery = _steer_dual_active_job(
            config, state, session, decision, active_message_id, active_pending,
            active_task, request_text, message_id, subject, phone, reply_text,
        )
        if accepted:
            if whatsapp_delivery.delivery_receipt_confirmed(delivery):
                _record_shared_delivered_exchange(
                    client_id=str(session.get("client_id") or ""),
                    username=str(session.get("username") or ""),
                    phone=phone,
                    subject_id=subject,
                    prompt=request_text,
                    response=reply_text,
                    event_id=message_id,
                )
            else:
                _discard_unconfirmed_assistant_reply(state, conversation_id, reply_text)
            return True, action
        return False, "queue"
    if action == "steer":
        return False, "delegate"
    if action in {"reply", "request_information"}:
        delivery = _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        if whatsapp_delivery.delivery_receipt_confirmed(delivery):
            try:
                _record_shared_delivered_exchange(
                    client_id=str(session.get("client_id") or ""),
                    username=str(session.get("username") or ""),
                    phone=phone,
                    subject_id=subject,
                    prompt=request_text,
                    response=reply_text,
                    event_id=message_id,
                )
            except Exception:
                pass
        else:
            _discard_unconfirmed_assistant_reply(
                state,
                conversation_id,
                reply_text,
            )
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
    _ensure_shared_conversation_record(
        config,
        state,
        session,
        phone=phone,
        subject_id=subject,
        conversation_id=conversation_id,
    )
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
    '_shared_continuity_for_session',
    '_ensure_shared_conversation_record',
    '_shared_sidebar_conversation_turn',
    '_shared_sidebar_worker_result',
    '_record_shared_delivered_exchange',
    '_discard_unconfirmed_assistant_reply',
    '_replace_provisional_assistant_reply',
    '_rotate_shared_conversation',
    '_reset_shared_conversation_for_session',
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
    '_shared_continuity_for_session': _shared_continuity_for_session,
    '_ensure_shared_conversation_record': _ensure_shared_conversation_record,
    '_shared_sidebar_conversation_turn': _shared_sidebar_conversation_turn,
    '_shared_sidebar_worker_result': _shared_sidebar_worker_result,
    '_record_shared_delivered_exchange': _record_shared_delivered_exchange,
    '_discard_unconfirmed_assistant_reply': _discard_unconfirmed_assistant_reply,
    '_replace_provisional_assistant_reply': _replace_provisional_assistant_reply,
    '_rotate_shared_conversation': _rotate_shared_conversation,
    '_reset_shared_conversation_for_session': _reset_shared_conversation_for_session,
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
