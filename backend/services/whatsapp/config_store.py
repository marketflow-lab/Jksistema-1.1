"""Extracted WhatsApp bridge component: config_store."""

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
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents
from backend.services.codex.console import paths as console_paths
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _base_dir() -> Path:
    return Path(console_paths.base_dir()).resolve()

def _info_dir() -> Path:
    path = Path(console_paths.base_info_dir()).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path

def _config_path() -> Path:
    return _info_dir() / "whatsapp_bridge.json"

def _state_path() -> Path:
    return _info_dir() / "whatsapp_bridge_state.json"

def _store_path() -> Path:
    return _info_dir() / "whatsapp_bridge.db"

def _bridge_store() -> WhatsappBridgeStore:
    global BRIDGE_STORE
    path = _store_path()
    if BRIDGE_STORE is None or BRIDGE_STORE.path != path:
        BRIDGE_STORE = WhatsappBridgeStore(path, _state_path())
    return BRIDGE_STORE

def _download_model_dir() -> Path:
    return _info_dir() / "ai_models" / "faster-whisper-small"

def _bundled_model_dir() -> Path:
    return _base_dir() / "black_jhon_runtime" / "faster-whisper-small"

def _model_manifest_path(model_dir: Optional[Path] = None) -> Path:
    return (model_dir or _model_dir()) / "model-manifest.json"

def _json_read(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default

def _validate_model_dir(path: Path) -> dict[str, Any]:
    model_dir = Path(path).resolve()
    manifest_path = model_dir / "model-manifest.json"
    try:
        manifest_mtime = manifest_path.stat().st_mtime_ns
    except OSError:
        return {"valid": False, "error": "manifest_missing", "files": 0, "bytes": 0}

    cache_key = str(model_dir).lower()
    cached = MODEL_VALIDATION_CACHE.get(cache_key)
    if cached and cached[0] == manifest_mtime:
        return dict(cached[1])

    manifest = _json_read(manifest_path, {})
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not entries:
        result = {"valid": False, "error": "manifest_invalid", "files": 0, "bytes": 0}
        MODEL_VALIDATION_CACHE[cache_key] = (manifest_mtime, result)
        return dict(result)

    total = 0
    try:
        for entry in entries:
            rel = str(entry.get("path") or "").replace("\\", "/").strip("/")
            expected_size = int(entry.get("size") or -1)
            expected_sha = str(entry.get("sha256") or "").strip().lower()
            if not rel or expected_size < 0 or not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
                raise RuntimeError("manifest_entry_invalid")
            candidate = (model_dir / rel).resolve()
            if os.path.commonpath([str(model_dir), str(candidate)]) != str(model_dir):
                raise RuntimeError("manifest_path_outside_model")
            if not candidate.is_file() or candidate.stat().st_size != expected_size:
                raise RuntimeError(f"model_file_invalid:{rel}")
            digest = hashlib.sha256()
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected_sha:
                raise RuntimeError(f"model_hash_invalid:{rel}")
            total += expected_size
    except Exception as exc:
        result = {"valid": False, "error": str(exc)[:300], "files": 0, "bytes": total}
        MODEL_VALIDATION_CACHE[cache_key] = (manifest_mtime, result)
        return dict(result)

    result = {"valid": True, "error": "", "files": len(entries), "bytes": total}
    MODEL_VALIDATION_CACHE[cache_key] = (manifest_mtime, result)
    return dict(result)

def _model_dir() -> Path:
    downloaded = _download_model_dir()
    if _validate_model_dir(downloaded).get("valid"):
        return downloaded
    bundled = _bundled_model_dir()
    if _validate_model_dir(bundled).get("valid"):
        return bundled
    return downloaded

def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent), suffix=".tmp") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    os.replace(temporary, path)

def _host_machine_id() -> str:
    raw = f"{socket.gethostname()}|{uuid.getnode()}".encode("utf-8", "ignore")
    return "wa-" + hashlib.sha256(raw).hexdigest()[:24]

def _normalize_ai_model(value: Any) -> str:
    return whatsapp_settings.normalize_ai_model(value)

def _normalize_codex_reasoning_effort(value: Any) -> str:
    return whatsapp_settings.normalize_codex_reasoning_effort(value)

def _normalize_codex_reasoning_policy(value: Any) -> str:
    return whatsapp_settings.normalize_codex_reasoning_policy(value)

def _normalize_codex_agent_model(value: Any, default: str) -> str:
    return whatsapp_settings.normalize_codex_agent_model(value, default)

def _normalize_agent_architecture(value: Any) -> str:
    return whatsapp_settings.normalize_agent_architecture(value)

def _normalize_conversation_interval(value: Any) -> int:
    return whatsapp_settings.normalize_conversation_interval(value)

def _normalize_capacity(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    return whatsapp_settings.normalize_capacity(value, fallback, minimum, maximum)

def _whatsapp_dual_agent_settings(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    source = config if isinstance(config, dict) else _load_config()
    return whatsapp_settings.dual_agent_settings(
        source,
        max_retry_attempts=WHATSAPP_MAX_RETRY_ATTEMPTS,
    )

def _normalize_progress_interval(value: Any) -> int:
    return whatsapp_settings.normalize_progress_interval(value)

def _whatsapp_ai_settings(config: Optional[dict[str, Any]] = None) -> dict[str, str]:
    source = config if isinstance(config, dict) else _load_config()
    return whatsapp_settings.ai_settings(source)

def _default_phone_notification_settings() -> dict[str, Any]:
    return whatsapp_settings.default_phone_notification_settings()

def _normalize_phone_ai_behavior(value: Any) -> str:
    return whatsapp_settings.normalize_phone_ai_behavior(value)

def _normalize_phone_notification_settings(value: Any) -> dict[str, Any]:
    return whatsapp_settings.normalize_phone_notification_settings(value)

def _phone_notification_settings(
    config: dict[str, Any],
    subject_id: Any,
    *,
    client_id: Any = "",
    username: Any = "",
) -> dict[str, Any]:
    return whatsapp_settings.phone_notification_settings(
        config,
        subject_id,
        client_id=client_id,
        username=username,
    )

def _default_config() -> dict[str, Any]:
    return {
        "version": 13,
        "worker_url": "",
        "bridge_token": "",
        "business_phone": "",
        "ai_model": WHATSAPP_AI_DEFAULT_MODEL,
        "codex_reasoning_effort": WHATSAPP_CODEX_REASONING_DEFAULT,
        "codex_reasoning_policy": WHATSAPP_CODEX_REASONING_POLICY_DEFAULT,
        "codex_reasoning_max": "xhigh",
        "orchestration_mode": WHATSAPP_ORCHESTRATION_MODE_DEFAULT,
        "progress_interval_seconds": WHATSAPP_PROGRESS_INTERVAL_DEFAULT,
        "progress_explain_wait": True,
        "active_task_policy": "steer_or_queue",
        "agent_architecture": WHATSAPP_AGENT_ARCHITECTURE_DEFAULT,
        "response_provider_policy": whatsapp_settings.WHATSAPP_RESPONSE_PROVIDER_POLICY_DEFAULT,
        "conversation_agent_model": WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT,
        "conversation_agent_reasoning": WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT,
        "task_agent_model": WHATSAPP_TASK_AGENT_MODEL_DEFAULT,
        "task_agent_reasoning": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
        "conversation_agent_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "conversation_agent_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "task_agent_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "task_agent_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "conversation_interval_seconds": WHATSAPP_CONVERSATION_INTERVAL_DEFAULT,
        "wait_message_after_seconds": WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT,
        "wait_message_repeat_seconds": WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT,
        "wait_message_steady_seconds": WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT,
        "partial_delivery_debounce_seconds": WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT,
        "job_deadline_seconds": WHATSAPP_JOB_DEADLINE_DEFAULT,
        "deadline_enabled": False,
        "retry_policy": "bounded",
        "max_subtasks_per_job": WHATSAPP_MAX_SUBTASKS_DEFAULT,
        "progress_messages_enabled": False,
        "max_active_task_agents_per_conversation": WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT,
        "conversation_worker_count": WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT,
        "conversation_runtime_pool_size": WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT,
        "max_active_task_agents_global": WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT,
        "preserve_order_per_phone": True,
        "function_manager_enabled": WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT,
        "function_manager_required_before_sol": WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT,
        "context_hub_enabled": True,
        "context_hub_enabled_default": True,
        "context_hub_enabled_by_client": {},
        "function_manager_worker_count": WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT,
        "function_manager_runtime_pool_size": WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT,
        "enabled": False,
        "pairing_pending": False,
        "pairing_expires_at": 0,
        "machine_id": _host_machine_id(),
        "client_id": "",
        "username": "",
        "subject_id": "",
        "personal_phone": "",
        "phone_notification_settings": {},
        "zero_cost_policy_valid_until": ZERO_COST_POLICY_VALID_UNTIL,
        "updated_at": "",
    }

def _load_config() -> dict[str, Any]:
    with CONFIG_LOCK:
        result = _default_config()
        stored = _json_read(_config_path(), {})
        stored_config = stored if isinstance(stored, dict) else {}
        if stored_config:
            result.update(stored_config)
            try:
                stored_version = int(stored_config.get("version") or 0)
            except (TypeError, ValueError):
                stored_version = 0
            if stored_version < 9 and str(result.get("agent_architecture") or "").strip().lower() == "dual_codex":
                # A versao 9 mantem a arquitetura dual e normaliza tentativas,
                # capacidade e avisos de espera.
                result.update(
                    {
                        "version": 9,
                        "wait_message_after_seconds": WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT,
                        "wait_message_repeat_seconds": WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT,
                        "wait_message_steady_seconds": WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT,
                        "partial_delivery_debounce_seconds": WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT,
                        "job_deadline_seconds": WHATSAPP_JOB_DEADLINE_DEFAULT,
                        "retry_policy": "bounded",
                        "task_agent_reasoning": "low",
                        "max_subtasks_per_job": WHATSAPP_MAX_SUBTASKS_DEFAULT,
                        "max_active_task_agents_per_conversation": WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT,
                        "max_active_task_agents_global": WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT,
                        "function_manager_enabled": True,
                        "function_manager_required_before_sol": True,
                        "function_manager_worker_count": WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT,
                        "function_manager_runtime_pool_size": WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT,
                    }
                )
            if stored_version < 10:
                result.update(
                    {
                        "version": 10,
                        "response_provider_policy": whatsapp_settings.WHATSAPP_RESPONSE_PROVIDER_POLICY_DEFAULT,
                    }
                )
            if stored_version < 11:
                result.update(
                    {
                        "version": 11,
                        "job_deadline_seconds": 0,
                        "deadline_enabled": False,
                    }
                )
            if stored_version < 12:
                result.update(
                    {
                        "version": 12,
                        "phone_notification_settings": whatsapp_settings.migrate_legacy_primary_phone_labels(
                            result.get("phone_notification_settings")
                        ),
                    }
                )
        result["enabled"] = bool(result.get("enabled"))
        result["version"] = 13
        result["job_deadline_seconds"] = 0
        result["deadline_enabled"] = False
        result["pairing_pending"] = bool(result.get("pairing_pending"))
        if "context_hub_enabled_default" not in stored_config:
            result["context_hub_enabled_default"] = stored_config.get("context_hub_enabled") is not False
        else:
            result["context_hub_enabled_default"] = stored_config.get("context_hub_enabled_default") is not False
        result["context_hub_enabled_by_client"] = whatsapp_settings.normalize_context_hub_enabled_by_client(
            result.get("context_hub_enabled_by_client")
        )
        result["client_id"] = str(result.get("client_id") or "").strip()
        result["context_hub_enabled"] = whatsapp_settings.context_hub_enabled_for_client(
            result, result.get("client_id")
        )
        result["machine_id"] = str(result.get("machine_id") or _host_machine_id())
        result["ai_model"] = _normalize_ai_model(result.get("ai_model"))
        result["response_provider_policy"] = whatsapp_settings.normalize_response_provider_policy(
            result.get("response_provider_policy")
        )
        result["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(result.get("codex_reasoning_effort"))
        result["codex_reasoning_policy"] = _normalize_codex_reasoning_policy(result.get("codex_reasoning_policy"))
        result["codex_reasoning_max"] = _normalize_codex_reasoning_effort(result.get("codex_reasoning_max"))
        result["orchestration_mode"] = WHATSAPP_ORCHESTRATION_MODE_DEFAULT
        result["progress_interval_seconds"] = _normalize_progress_interval(result.get("progress_interval_seconds"))
        result["progress_explain_wait"] = result.get("progress_explain_wait") is not False
        result["active_task_policy"] = "steer_or_queue"
        dual = _whatsapp_dual_agent_settings(result)
        result.update(dual)
        if dual["agent_architecture"] == "dual_codex":
            result["progress_explain_wait"] = False
        if not isinstance(result.get("phone_notification_settings"), dict):
            result["phone_notification_settings"] = {}
        for key in tuple(result):
            if str(key).startswith("voice_"):
                result.pop(key, None)
        return result

def _save_config(config: dict[str, Any]) -> dict[str, Any]:
    with CONFIG_LOCK:
        source = dict(config or {})
        override = source.pop("_context_hub_enabled_override", None)
        stored = _json_read(_config_path(), {})
        stored_config = stored if isinstance(stored, dict) else {}
        if "context_hub_enabled_default" in stored_config:
            context_hub_default = stored_config.get("context_hub_enabled_default") is not False
        elif "context_hub_enabled" in stored_config:
            context_hub_default = stored_config.get("context_hub_enabled") is not False
        else:
            context_hub_default = whatsapp_settings.context_hub_enabled_default(source)
        if "context_hub_enabled_by_client" in stored_config:
            context_hub_overrides = whatsapp_settings.normalize_context_hub_enabled_by_client(
                stored_config.get("context_hub_enabled_by_client")
            )
        else:
            context_hub_overrides = whatsapp_settings.normalize_context_hub_enabled_by_client(
                source.get("context_hub_enabled_by_client")
            )
        if isinstance(override, dict):
            override_client_id = whatsapp_settings.normalize_context_hub_client_id(
                override.get("client_id")
            )
            if override_client_id and isinstance(override.get("enabled"), bool):
                context_hub_overrides[override_client_id] = override["enabled"]
        value = _default_config()
        value.update(source)
        value["version"] = 13
        value["context_hub_enabled_default"] = context_hub_default
        value["context_hub_enabled_by_client"] = context_hub_overrides
        value["client_id"] = str(value.get("client_id") or "").strip()
        value["context_hub_enabled"] = whatsapp_settings.context_hub_enabled_for_client(
            value, value.get("client_id")
        )
        value["ai_model"] = _normalize_ai_model(value.get("ai_model"))
        value["response_provider_policy"] = whatsapp_settings.normalize_response_provider_policy(
            value.get("response_provider_policy")
        )
        value["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(value.get("codex_reasoning_effort"))
        value["codex_reasoning_policy"] = _normalize_codex_reasoning_policy(value.get("codex_reasoning_policy"))
        value["codex_reasoning_max"] = _normalize_codex_reasoning_effort(value.get("codex_reasoning_max"))
        value["orchestration_mode"] = WHATSAPP_ORCHESTRATION_MODE_DEFAULT
        value["progress_interval_seconds"] = _normalize_progress_interval(value.get("progress_interval_seconds"))
        value["progress_explain_wait"] = value.get("progress_explain_wait") is not False
        value["active_task_policy"] = "steer_or_queue"
        dual = _whatsapp_dual_agent_settings(value)
        value.update(dual)
        if dual["agent_architecture"] == "dual_codex":
            value["progress_explain_wait"] = False
        if not isinstance(value.get("phone_notification_settings"), dict):
            value["phone_notification_settings"] = {}
        for key in tuple(value):
            if str(key).startswith("voice_"):
                value.pop(key, None)
        value["updated_at"] = _now()
        persisted_value = {
            key: item
            for key, item in value.items()
            if not str(key).startswith("function_manager_")
        }
        _json_write(_config_path(), persisted_value)
        return value

def _load_state() -> dict[str, Any]:
    global BRIDGE_SHARED_STATE
    with BRIDGE_STATE_LOCK:
        if BRIDGE_SHARED_STATE is None:
            try:
                value = _bridge_store().load_state()
            except Exception as exc:
                RUNTIME_STATE["state_store_last_error"] = str(exc)[:500]
                value = _json_read(_state_path(), {})
            BRIDGE_SHARED_STATE = value if isinstance(value, dict) else {}
        return BRIDGE_SHARED_STATE

def _save_state(state: dict[str, Any]) -> None:
    global BRIDGE_SHARED_STATE
    with BRIDGE_STATE_LOCK:
        if BRIDGE_SHARED_STATE is None:
            BRIDGE_SHARED_STATE = state
        elif state is not BRIDGE_SHARED_STATE:
            BRIDGE_SHARED_STATE.update(dict(state or {}))
        try:
            _bridge_store().save_state(BRIDGE_SHARED_STATE)
            RUNTIME_STATE.pop("state_store_last_error", None)
        except Exception as exc:
            # A falha do SQLite nao pode apagar o estado em memoria. O JSON
            # legado fica apenas como contingencia de escrita e diagnostico.
            RUNTIME_STATE["state_store_last_error"] = str(exc)[:500]
            _json_write(_state_path(), BRIDGE_SHARED_STATE)


_COMPONENT_FUNCTIONS = frozenset((
    '_now',
    '_base_dir',
    '_info_dir',
    '_config_path',
    '_state_path',
    '_store_path',
    '_bridge_store',
    '_download_model_dir',
    '_bundled_model_dir',
    '_model_manifest_path',
    '_json_read',
    '_validate_model_dir',
    '_model_dir',
    '_json_write',
    '_host_machine_id',
    '_normalize_ai_model',
    '_normalize_codex_reasoning_effort',
    '_normalize_codex_reasoning_policy',
    '_normalize_codex_agent_model',
    '_normalize_agent_architecture',
    '_normalize_conversation_interval',
    '_normalize_capacity',
    '_whatsapp_dual_agent_settings',
    '_normalize_progress_interval',
    '_whatsapp_ai_settings',
    '_default_phone_notification_settings',
    '_normalize_phone_ai_behavior',
    '_normalize_phone_notification_settings',
    '_phone_notification_settings',
    '_default_config',
    '_load_config',
    '_save_config',
    '_load_state',
    '_save_state'
))
_IMPLEMENTATIONS = {
    '_now': _now,
    '_base_dir': _base_dir,
    '_info_dir': _info_dir,
    '_config_path': _config_path,
    '_state_path': _state_path,
    '_store_path': _store_path,
    '_bridge_store': _bridge_store,
    '_download_model_dir': _download_model_dir,
    '_bundled_model_dir': _bundled_model_dir,
    '_model_manifest_path': _model_manifest_path,
    '_json_read': _json_read,
    '_validate_model_dir': _validate_model_dir,
    '_model_dir': _model_dir,
    '_json_write': _json_write,
    '_host_machine_id': _host_machine_id,
    '_normalize_ai_model': _normalize_ai_model,
    '_normalize_codex_reasoning_effort': _normalize_codex_reasoning_effort,
    '_normalize_codex_reasoning_policy': _normalize_codex_reasoning_policy,
    '_normalize_codex_agent_model': _normalize_codex_agent_model,
    '_normalize_agent_architecture': _normalize_agent_architecture,
    '_normalize_conversation_interval': _normalize_conversation_interval,
    '_normalize_capacity': _normalize_capacity,
    '_whatsapp_dual_agent_settings': _whatsapp_dual_agent_settings,
    '_normalize_progress_interval': _normalize_progress_interval,
    '_whatsapp_ai_settings': _whatsapp_ai_settings,
    '_default_phone_notification_settings': _default_phone_notification_settings,
    '_normalize_phone_ai_behavior': _normalize_phone_ai_behavior,
    '_normalize_phone_notification_settings': _normalize_phone_notification_settings,
    '_phone_notification_settings': _phone_notification_settings,
    '_default_config': _default_config,
    '_load_config': _load_config,
    '_save_config': _save_config,
    '_load_state': _load_state,
    '_save_state': _save_state
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
