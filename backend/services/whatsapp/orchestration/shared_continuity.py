"""WhatsApp conversation memory isolated from the Sidebar conversation."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from backend.services.codex.console import security as console_security
from backend.services.whatsapp import conversation_context as whatsapp_conversation_context
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
    return console_security.hmac_identifier(
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
    """Compatibility shim: shared Sidebar identity no longer exists."""

    del config, session, phone, subject_id
    return _dual_conversation_record(state, conversation_id)


def _shared_continuity_for_session(session: dict[str, Any]) -> dict[str, Any]:
    """Keep WhatsApp continuity isolated from the Sidebar conversation."""

    return {}


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


def _shared_sidebar_conversation_turn(
    session: dict[str, Any],
    user_message: str,
    screen_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compatibility shim for callers predating channel isolation."""

    del session, user_message, screen_context
    return {}


def _shared_sidebar_worker_result(
    task: dict[str, Any],
    final_response: str,
    agent_trace: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compatibility shim for callers predating channel isolation."""

    del task, final_response, agent_trace
    return {}


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
    """Compatibility shim; WhatsApp exchanges are never copied to Sidebar."""

    del client_id, username, phone, subject_id, prompt, response, event_id
    return {}


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
    """Compatibility shim for the removed shared Sidebar conversation."""

    del config, client_id, username, reason, clear_memory
    return {}


def _reset_shared_conversation_for_session(session: dict[str, Any]) -> dict[str, Any]:
    """Compatibility shim for the removed shared Sidebar conversation."""

    del session
    return {}


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
