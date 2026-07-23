"""Shared Black Jhon conversation identity, memory, and delivery continuity."""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from backend.services import codex_console, codex_whatsapp_agents
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp.composition import BridgeDependencies, bind_component_namespace


_SHARED_TURN_LOCKS_GUARD = threading.RLock()
_SHARED_TURN_LOCKS: dict[str, threading.RLock] = {}
# Direct imports used by focused tests and diagnostic probes do not pass
# through the compatibility facade. The facade replaces these fallbacks with
# the process-wide locks before normal runtime invocations.
BRIDGE_STATE_LOCK = threading.RLock()
DUAL_AGENT_STATE_LOCK = threading.RLock()


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
    """Align replay memory when a control action rewrites the model reply."""

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


_IMPLEMENTATIONS = {
    name: globals()[name]
    for name in (
        "_shared_turn_lock",
        "_shared_authorization_fingerprint",
        "_dual_conversation_record",
        "_ensure_shared_conversation_record",
        "_shared_continuity_for_session",
        "_dual_confirm_conversation_context",
        "_dual_append_conversation_turn",
        "_dual_recent_conversation_context",
        "_dual_remember_conversation_turn",
        "_save_dual_conversation_record",
        "_sidebar_current_turn_context",
        "_shared_sidebar_conversation_turn",
        "_shared_sidebar_worker_result",
        "_record_shared_delivered_exchange",
        "_discard_unconfirmed_assistant_reply",
        "_replace_provisional_assistant_reply",
        "_rotate_shared_conversation",
        "_reset_shared_conversation_for_session",
        "_record_dual_user_message",
    )
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


__all__ = ["bind_bridge_dependencies", "_IMPLEMENTATIONS"]
