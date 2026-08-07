"""Codex console conversation store component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)


def _codex_conversation_dir(client_id: str, username: str) -> Path:
    path = (
        Path(_codex_info_dir())
        / "conversations"
        / _codex_safe_id(client_id)
        / _codex_safe_id(username, "user")
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_conversation_path(client_id: str, username: str, conversation_id: str) -> Path:
    return _codex_conversation_dir(client_id, username) / f"{_codex_safe_id(conversation_id)}.json"


def _codex_task_channel(task: dict[str, Any]) -> str:
    return "whatsapp" if str(task.get("origin") or "").strip().lower() == "whatsapp" else "app"


def _codex_task_phone(task: dict[str, Any]) -> str:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return _codex_normalize_phone(metadata.get("wa_id") or metadata.get("phone"))


def _codex_task_lane(task: dict[str, Any]) -> str:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return _codex_safe_id(
        str(task.get("agent_lane") or metadata.get("agent_lane") or task.get("agent_role") or metadata.get("agent_role") or "").strip().lower(),
        "",
    )[:40]


def _codex_task_stored_conversation_id(task: dict[str, Any]) -> str:
    return _codex_safe_id(
        str(task.get("conversation_id") or task.get("thread_id") or task.get("task_id") or "").strip(),
        "",
    )


def _codex_find_latest_legacy_conversation(
    client_id: str,
    username: str,
    *,
    channel: str,
    phone: str = "",
    canonical_id: str,
) -> tuple[str, dict[str, Any]]:
    username_norm = str(username or "").strip().lower()
    try:
        paths = sorted(
            Path(_codex_info_dir()).glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        paths = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        if _codex_task_channel(task) != channel:
            continue
        if channel == "whatsapp" and _codex_task_phone(task) != phone:
            continue
        if str(task.get("status") or "") != "completed":
            continue
        if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
            continue
        if not str(task.get("prompt") or "").strip() or not str(task.get("final_response") or "").strip():
            continue
        legacy_id = _codex_task_stored_conversation_id(task)
        if legacy_id and legacy_id != canonical_id:
            return legacy_id, task
    return "", {}


def _codex_load_or_create_conversation_state(
    client_id: str,
    username: str,
    *,
    channel: str = "app",
    phone: Any = "",
    lane: Any = "",
) -> dict[str, Any]:
    channel_norm = "whatsapp" if str(channel or "").strip().lower() == "whatsapp" else "app"
    phone_norm = _codex_normalize_phone(phone) if channel_norm == "whatsapp" else ""
    lane_norm = _codex_safe_id(str(lane or "").strip().lower(), "")[:40]
    conversation_id = _codex_canonical_conversation_id(
        client_id,
        username,
        channel=channel_norm,
        phone=phone_norm,
        lane=lane_norm,
    )
    with CODEX_CONVERSATION_LOCK:
        stored = _codex_load_conversation_summary(client_id, username, conversation_id)
        if (
            str(stored.get("conversation_id") or "") == conversation_id
            and int(stored.get("generation") or 0) >= 1
        ):
            return stored

        legacy_id, legacy_task = ("", {})
        if not lane_norm:
            legacy_id, legacy_task = _codex_find_latest_legacy_conversation(
                client_id,
                username,
                channel=channel_norm,
                phone=phone_norm,
                canonical_id=conversation_id,
            )
        legacy_summary = _codex_load_conversation_summary(client_id, username, legacy_id) if legacy_id else {}
        now = _codex_now()
        payload = {
            "conversation_id": conversation_id,
            "client_id": str(client_id or "default"),
            "created_by": str(username or "").strip().lower(),
            "channel": channel_norm,
            "agent_lane": lane_norm,
            "phone_fingerprint": _codex_hmac_identifier(phone_norm, namespace="whatsapp_phone")[-24:] if phone_norm else "",
            "generation": 1,
            "state": "active",
            "summary": str(legacy_summary.get("summary") or ""),
            "recent_messages": list(legacy_summary.get("recent_messages") or []),
            "legacy_conversation_ids": [legacy_id] if legacy_id else [],
            "latest_thread_id": str(legacy_task.get("thread_id") or "") if legacy_task else "",
            "thread_prompt_fingerprint": "",
            "thread_schema_fingerprint": "",
            "thread_scope_fingerprint": "",
            "thread_conversation_key": "",
            "thread_restart_reason": "legacy_state_without_fingerprints" if legacy_task else "",
            "created_at": now,
            "updated_at": now,
            "reset_audit": [],
        }
        _codex_save_conversation_summary(client_id, username, conversation_id, payload)
        return payload





def _codex_conversation_state_for_task(task: dict[str, Any]) -> dict[str, Any]:
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    channel = _codex_task_channel(task)
    phone = _codex_task_phone(task)
    lane = _codex_task_lane(task)
    if not client_id or not username or (channel == "whatsapp" and not phone):
        return {}
    return _codex_load_or_create_conversation_state(
        client_id,
        username,
        channel=channel,
        phone=phone,
        lane=lane,
    )


def _codex_save_conversation_state(task: dict[str, Any], **updates: Any) -> dict[str, Any]:
    with CODEX_CONVERSATION_LOCK:
        state = _codex_conversation_state_for_task(task)
        if not state:
            return {}
        generation = int(task.get("conversation_generation") or 1)
        if generation != int(state.get("generation") or 1):
            return state
        state.update(updates)
        state["updated_at"] = _codex_now()
        _codex_save_conversation_summary(
            str(task.get("client_id") or "default"),
            str(task.get("created_by") or "").strip().lower(),
            str(state.get("conversation_id") or ""),
            state,
        )
        return state


def _codex_task_conversation_metadata(task: dict[str, Any]) -> dict[str, Any]:
    stored_id = _codex_task_stored_conversation_id(task)
    expected_shared_id = _codex_shared_conversation_id(
        str(task.get("client_id") or "default"),
        str(task.get("created_by") or "user"),
    )
    state = (
        _codex_load_or_create_shared_conversation_state(
            str(task.get("client_id") or "default"),
            str(task.get("created_by") or "user"),
        )
        if stored_id == expected_shared_id
        else _codex_conversation_state_for_task(task)
    )
    if not state:
        return {
            "conversation_id": stored_id,
            "conversation_generation": int(task.get("conversation_generation") or 1),
            "conversation_state": "archived",
            "channel": _codex_task_channel(task),
        }
    canonical_id = str(state.get("conversation_id") or stored_id)
    generation = int(task.get("conversation_generation") or 1)
    active_generation = int(state.get("generation") or 1)
    aliases = {
        _codex_safe_id(str(item or "").strip(), "")
        for item in (state.get("legacy_conversation_ids") or [])
        if str(item or "").strip()
    }
    selected = stored_id == canonical_id or (generation == 1 and stored_id in aliases)
    return {
        "conversation_id": canonical_id if selected else stored_id,
        "conversation_generation": generation,
        "conversation_state": "active" if selected and generation == active_generation else "archived",
        "channel": str(state.get("channel") or _codex_task_channel(task)),
    }





def _codex_deleted_conversations_path(client_id: str, username: str) -> Path:
    return _codex_conversation_dir(client_id, username) / "_deleted_conversations.json"


def _codex_load_deleted_conversations(client_id: str, username: str) -> dict[str, Any]:
    path = _codex_deleted_conversations_path(client_id, username)
    if not path.exists():
        return {"deleted": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            deleted = payload.get("deleted")
            if isinstance(deleted, dict):
                return {"deleted": deleted}
    except Exception:
        pass
    return {"deleted": {}}


def _codex_deleted_conversation_ids(client_id: str, username: str) -> set[str]:
    payload = _codex_load_deleted_conversations(client_id, username)
    deleted = payload.get("deleted") if isinstance(payload.get("deleted"), dict) else {}
    return {str(item or "").strip() for item in deleted.keys() if str(item or "").strip()}


def _codex_is_conversation_deleted(client_id: str, username: str, conversation_id: str) -> bool:
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    return bool(conv_id and conv_id in _codex_deleted_conversation_ids(client_id, username))


def _codex_mark_conversation_deleted(client_id: str, conversation_id: str, username: str = "", task_ids: Optional[list[str]] = None) -> None:
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not conv_id:
        return
    path = _codex_deleted_conversations_path(client_id, username)
    payload = _codex_load_deleted_conversations(client_id, username)
    deleted = payload.setdefault("deleted", {})
    if not isinstance(deleted, dict):
        deleted = {}
        payload["deleted"] = deleted
    deleted[conv_id] = {
        "conversation_id": conv_id,
        "deleted_at": _codex_now(),
        "deleted_by": str(username or "")[:120],
        "task_ids": [str(item or "")[:80] for item in (task_ids or []) if str(item or "").strip()][:500],
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def _codex_normalizar_history(value: Any, limit: int = 40) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            role = "assistant" if role in {"bot", "codex", "joao"} else "user"
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "role": role,
                "text": re.sub(r"\s+", " ", text)[:2400],
                "task_id": str(item.get("task_id") or "")[:80],
                "kind": str(item.get("kind") or item.get("message_kind") or "")[:40],
            }
        )
    return normalized


def _codex_load_conversation_summary(client_id: str, username: str, conversation_id: str) -> dict[str, Any]:
    path = _codex_conversation_path(client_id, username, conversation_id)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _codex_save_conversation_summary(client_id: str, username: str, conversation_id: str, payload: dict[str, Any]) -> None:
    path = _codex_conversation_path(client_id, username, conversation_id)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def _codex_task_conversation_id(task: dict[str, Any]) -> str:
    return _codex_conversation_id(str(task.get("conversation_id") or task.get("thread_id") or ""), str(task.get("task_id") or ""))


def _codex_task_conversation_keys(task: dict[str, Any]) -> set[str]:
    keys = {_codex_task_conversation_id(task)}
    conversation_id = _codex_safe_id(str(task.get("conversation_id") or "").strip(), "")
    thread_id = _codex_safe_id(str(task.get("thread_id") or "").strip(), "")
    task_id = _codex_safe_id(str(task.get("task_id") or "").strip(), "")
    if conversation_id:
        keys.add(conversation_id)
    if thread_id:
        keys.add(thread_id)
        keys.add(f"thread_{thread_id}")
    if task_id:
        keys.add(task_id)
        keys.add(f"task_{task_id}")
    return {item for item in keys if item}


def _codex_task_history_messages(task: dict[str, Any]) -> list[dict[str, str]]:
    if str(task.get("status") or "") != "completed":
        return []
    if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
        return []
    messages: list[dict[str, str]] = []
    prompt = str(task.get("prompt") or "").strip()
    if prompt:
        messages.append({"role": "user", "text": prompt[:2400], "task_id": str(task.get("task_id") or "")})
    final = str(task.get("final_response") or "").strip()
    if final:
        kind = str(task.get("message_kind") or "")
        messages.append({"role": "assistant", "text": final[:3600], "task_id": str(task.get("task_id") or ""), "kind": kind})
    return messages


def _codex_recent_persisted_messages(
    client_id: str,
    username: str,
    conversation_id: str,
    exclude_task_id: str = "",
    limit: int = 40,
    generation: int = 1,
    aliases: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    username_norm = str(username or "").strip().lower()
    if _codex_is_conversation_deleted(client_id, username_norm, conversation_id):
        return []
    try:
        paths = sorted(Path(_codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime)
    except Exception:
        paths = []
    alias_ids = {
        _codex_safe_id(str(item or "").strip(), "")
        for item in (aliases or [])
        if str(item or "").strip()
    }
    canonical_id = _codex_safe_id(conversation_id)
    for path in paths[-240:]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        if str(task.get("task_id") or "") == str(exclude_task_id or ""):
            continue
        stored_id = _codex_task_stored_conversation_id(task)
        task_generation = int(task.get("conversation_generation") or 1)
        canonical_match = stored_id == canonical_id and task_generation == int(generation or 1)
        legacy_match = int(generation or 1) == 1 and stored_id in alias_ids
        if not canonical_match and not legacy_match:
            continue
        messages.extend(_codex_task_history_messages(task))
    return messages[-limit:]


def _codex_conversation_keywords(messages: list[dict[str, str]]) -> dict[str, list[str]]:
    text = "\n".join(str(item.get("text") or "") for item in messages)
    skus = sorted(set(re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,24}\b", text.upper())))[:30]
    reports = sorted(set(re.findall(r"\bcodex_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}\b", text, flags=re.I)))[:20]
    lojas = []
    for match in re.findall(r"\b(JK\s*Pecas|JK\s*Peças|Mercado Livre|Bling|Full)\b", text, flags=re.I):
        label = re.sub(r"\s+", " ", match).strip()
        if label and label not in lojas:
            lojas.append(label)
    return {"skus": skus, "reports": reports, "lojas": lojas[:20]}


def _codex_compact_summary(existing_summary: str, older_messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    if existing_summary:
        stable_existing = _codex_durable_memory_text(
            existing_summary,
            CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT,
        )
        if stable_existing:
            lines.append(stable_existing)
    stable_messages: list[dict[str, str]] = []
    for item in older_messages[-60:]:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        stable_text = _codex_durable_memory_text(text, 520) if text else ""
        if stable_text:
            stable_messages.append({"role": str(item.get("role") or "assistant"), "text": stable_text})
    keywords = _codex_conversation_keywords(stable_messages)
    lines.append("Resumo operacional compactado da conversa atual:")
    if keywords.get("lojas"):
        lines.append("Lojas/contas citadas: " + ", ".join(keywords["lojas"]))
    if keywords.get("skus"):
        lines.append("SKUs/codigos citados: " + ", ".join(keywords["skus"][:20]))
    if keywords.get("reports"):
        lines.append("Relatorios citados: " + ", ".join(keywords["reports"][:12]))
    for item in stable_messages:
        role = "Usuario" if item.get("role") == "user" else BLACK_JHON_DISPLAY_NAME
        lines.append(f"- {role}: {item.get('text') or ''}")
    summary = "\n".join(line for line in lines if line).strip()
    if len(summary) > CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT:
        summary = summary[-CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT:]
    return summary





def _codex_prepare_conversation_context(task: dict[str, Any]) -> dict[str, Any]:
    client_id = str(task.get("client_id") or "").strip()
    if not client_id:
        return {}
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    stored = _codex_load_conversation_summary(client_id, username, conversation_id)
    summary = _codex_durable_memory_text(
        stored.get("summary") or "",
        CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT,
    )
    generation = int(task.get("conversation_generation") or stored.get("generation") or 1)
    aliases = list(stored.get("legacy_conversation_ids") or []) if generation == 1 else []
    # O browser nao e fonte de autoridade do contexto. A memoria vem apenas de
    # tarefas persistidas desta conversa/geracao no servidor.
    browser_history: list[dict[str, str]] = []
    persisted = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        60,
        generation,
        aliases,
    )
    recent_messages = (persisted + browser_history)[-max(4, CODEX_CONVERSATION_RECENT_MESSAGES):]
    raw_context = json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)
    estimated_before = _codex_estimar_tokens(raw_context)
    compacted = False
    if estimated_before > CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT:
        combined = persisted + browser_history
        keep = max(4, CODEX_CONVERSATION_RECENT_MESSAGES)
        older = combined[:-keep]
        recent_messages = combined[-keep:]
        summary = _codex_compact_summary(summary, older)
        compacted = True
        payload = {
            **stored,
            "conversation_id": conversation_id,
            "client_id": client_id,
            "created_by": username,
            "summary": summary,
            "compacted_until": _codex_now(),
            "summary_updated_at": _codex_now(),
            "estimated_tokens_before": estimated_before,
            "estimated_tokens_after": _codex_estimar_tokens(json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)),
        }
        _codex_save_conversation_summary(client_id, username, conversation_id, payload)
        stored = payload
    return {
        "conversation_id": conversation_id,
        "summary": summary,
        "recent_messages": recent_messages,
        "history_chars": len(raw_context),
        "estimated_history_tokens": estimated_before,
        "compacted": compacted,
        "compaction": {
            "compacted": compacted,
            "compacted_until": stored.get("compacted_until") or "",
            "summary_updated_at": stored.get("summary_updated_at") or "",
            "estimated_tokens_before": stored.get("estimated_tokens_before") or estimated_before,
            "estimated_tokens_after": stored.get("estimated_tokens_after") or estimated_before,
        },
    }





def _codex_update_conversation_memory(task_id: str) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not task:
        return {}
    client_id = str(task.get("client_id") or "").strip()
    if not client_id:
        return {}
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    stored = _codex_load_conversation_summary(client_id, username, conversation_id)
    summary = str(stored.get("summary") or "").strip()
    generation = int(task.get("conversation_generation") or stored.get("generation") or 1)
    aliases = list(stored.get("legacy_conversation_ids") or []) if generation == 1 else []
    messages = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        160,
        generation,
        aliases,
    )
    messages.extend(_codex_task_history_messages(task))
    keep = max(4, CODEX_CONVERSATION_RECENT_MESSAGES)
    raw = json.dumps({"summary": summary, "messages": messages}, ensure_ascii=False, default=str)
    estimated_before = _codex_estimar_tokens(raw)
    compacted = estimated_before > CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT or bool(summary)
    if compacted and len(messages) > keep:
        summary = _codex_compact_summary(summary, messages[:-keep])
    recent_messages = messages[-keep:]
    payload = {
        **stored,
        "conversation_id": conversation_id,
        "client_id": client_id,
        "created_by": username,
        "summary": summary,
        "recent_messages": recent_messages,
        "compacted_until": _codex_now() if compacted else str(stored.get("compacted_until") or ""),
        "summary_updated_at": _codex_now(),
        "estimated_tokens_before": estimated_before,
        "estimated_tokens_after": _codex_estimar_tokens(json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)),
    }
    _codex_save_conversation_summary(client_id, username, conversation_id, payload)
    return payload





def _codex_delete_conversation_memory_if_unused(task: dict[str, Any]) -> None:
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    current_task_id = str(task.get("task_id") or "")
    try:
        for path in Path(_codex_info_dir()).glob("*.json"):
            with path.open("r", encoding="utf-8") as fh:
                other = json.load(fh)
            if not isinstance(other, dict):
                continue
            if str(other.get("task_id") or "") == current_task_id:
                continue
            if (
                str(other.get("client_id") or "") == client_id
                and str(other.get("created_by") or "").strip().lower() == username
                and _codex_safe_id(conversation_id) in _codex_task_conversation_keys(other)
            ):
                return
        summary_path = _codex_conversation_path(client_id, username, conversation_id)
        if summary_path.exists():
            summary_path.unlink()
    except Exception:
        return

def _codex_durable_memory_text(value: Any, limit: int = 4000) -> str:
    sanitized = codex_turn_context.sanitize_durable_memory(
        {"content": str(value or "")},
        max_bytes=max(128, min(int(limit or 4000), CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT)),
    )
    payload = sanitized.value if isinstance(sanitized.value, dict) else {}
    return str(payload.get("content") or "").strip()[:limit]



def _codex_load_or_create_shared_conversation_state(
    client_id: str,
    username: str,
) -> dict[str, Any]:
    conversation_id = _codex_shared_conversation_id(client_id, username)
    with CODEX_CONVERSATION_LOCK:
        stored = _codex_load_conversation_summary(client_id, username, conversation_id)
        if (
            str(stored.get("conversation_id") or "") == conversation_id
            and int(stored.get("generation") or 0) >= 1
        ):
            return stored
        app_state = _codex_load_or_create_conversation_state(
            client_id,
            username,
            channel="app",
        )
        now = _codex_now()
        payload = {
            "conversation_id": conversation_id,
            "client_id": str(client_id or "default"),
            "created_by": str(username or "").strip().lower(),
            "channel": "shared",
            "generation": int(app_state.get("generation") or 1),
            "state": "active",
            "summary": str(app_state.get("summary") or ""),
            "recent_messages": list(app_state.get("recent_messages") or []),
            "legacy_conversation_ids": [str(app_state.get("conversation_id") or "")],
            "created_at": now,
            "updated_at": now,
            "reset_audit": [],
        }
        _codex_save_conversation_summary(client_id, username, conversation_id, payload)
        return payload
__codex_dependencies__ = ['BLACK_JHON_DISPLAY_NAME', 'CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT', 'CODEX_CONVERSATION_LOCK', 'CODEX_CONVERSATION_RECENT_MESSAGES', 'CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT', '_codex_canonical_conversation_id', '_codex_conversation_id', '_codex_estimar_tokens', '_codex_hmac_identifier', '_codex_info_dir', '_codex_load_task', '_codex_normalize_phone', '_codex_now', '_codex_safe_id', '_codex_shared_conversation_id']

__codex_exports__ = ['_codex_conversation_dir', '_codex_conversation_path', '_codex_task_channel', '_codex_task_phone', '_codex_task_lane', '_codex_task_stored_conversation_id', '_codex_find_latest_legacy_conversation', '_codex_load_or_create_conversation_state', '_codex_conversation_state_for_task', '_codex_save_conversation_state', '_codex_task_conversation_metadata', '_codex_deleted_conversations_path', '_codex_load_deleted_conversations', '_codex_deleted_conversation_ids', '_codex_is_conversation_deleted', '_codex_mark_conversation_deleted', '_codex_normalizar_history', '_codex_load_conversation_summary', '_codex_save_conversation_summary', '_codex_task_conversation_id', '_codex_task_conversation_keys', '_codex_task_history_messages', '_codex_recent_persisted_messages', '_codex_conversation_keywords', '_codex_compact_summary', '_codex_prepare_conversation_context', '_codex_update_conversation_memory', '_codex_delete_conversation_memory_if_unused', '_codex_durable_memory_text', '_codex_load_or_create_shared_conversation_state']
