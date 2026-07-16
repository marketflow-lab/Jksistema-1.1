"""Local zero-cost WhatsApp bridge for Joao Pretinho.

The public webhook lives at Cloudflare. This module only polls the authenticated
bridge endpoints, keeps media and transcription local, and creates tasks through
the same internal Codex task core used by the existing HTTP API.
"""

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo

import requests
from fastapi import Header, HTTPException, Request

from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import settings as whatsapp_settings
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
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore


ZERO_COST_POLICY_VALID_UNTIL = "2026-09-30T23:59:59Z"
POLL_SECONDS = 3
CLAIM_LIMIT = 5
TYPING_REFRESH_SECONDS = 20
TYPING_MAX_SECONDS = 10 * 60
TYPING_MAX_CONSECUTIVE_ERRORS = 3
TRANSCRIPTION_TIMEOUT_SECONDS = 600
WHISPER_MODEL_EXPECTED_BYTES = 488_000_000
WHATSAPP_GATEWAY_PROTOCOL_VERSION = 1
SUPPORTED_IMAGE_MIMES = whatsapp_media.SUPPORTED_IMAGE_MIMES
SUPPORTED_AUDIO_MIMES = whatsapp_media.SUPPORTED_AUDIO_MIMES
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
APPROVAL_CODE_TTL_SECONDS = 10 * 60
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_ADHOC_MESSAGE_CHARS = 3500
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS
WHATSAPP_EXACT_ORDER_HISTORY_MARKER = whatsapp_formatting.WHATSAPP_EXACT_ORDER_HISTORY_MARKER
WHATSAPP_REPORT_MAX_PARTS = whatsapp_formatting.WHATSAPP_REPORT_MAX_PARTS
WHATSAPP_REPORT_BODY_CHARS = whatsapp_formatting.WHATSAPP_REPORT_BODY_CHARS
WHATSAPP_REPORT_RANKING_ITEMS_PER_PART = whatsapp_formatting.WHATSAPP_REPORT_RANKING_ITEMS_PER_PART
WHATSAPP_QUERY_CONTEXT_TTL_SECONDS = 24 * 3600
WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS = 30 * 60
WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES = whatsapp_media.WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES
WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES = whatsapp_media.WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES
WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS = whatsapp_media.WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS
WHATSAPP_WEEKLY_REPORT_START_HOUR = 8
WHATSAPP_IMAGE_MARKDOWN_RE = whatsapp_media.WHATSAPP_IMAGE_MARKDOWN_RE
APPROVAL_COMMAND_RE = re.compile(
    r"^(APROVAR|CONFIRMAR|NEGAR|REJEITAR|CANCELAR)\s+([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_COMMAND_RE = re.compile(
    r"^ppv_(approve|correct|reject|regenerate|suggest):([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
STORE_SELECTION_COMMAND_RE = re.compile(r"^store_select:([A-Z2-9]{8})$", re.IGNORECASE)
STORE_SELECTION_TOKEN_TTL_SECONDS = 10 * 60
WHATSAPP_AI_DEFAULT_MODEL = whatsapp_settings.WHATSAPP_AI_DEFAULT_MODEL
WHATSAPP_CODEX_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_REASONING_DEFAULT
WHATSAPP_CODEX_REASONING_OPTIONS = whatsapp_settings.WHATSAPP_CODEX_REASONING_OPTIONS
WHATSAPP_CODEX_REASONING_POLICY_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_REASONING_POLICY_DEFAULT
WHATSAPP_CODEX_REASONING_POLICIES = whatsapp_settings.WHATSAPP_CODEX_REASONING_POLICIES
WHATSAPP_ORCHESTRATION_MODE_DEFAULT = whatsapp_settings.WHATSAPP_ORCHESTRATION_MODE_DEFAULT
WHATSAPP_PROGRESS_INTERVAL_DEFAULT = whatsapp_settings.WHATSAPP_PROGRESS_INTERVAL_DEFAULT
WHATSAPP_AGENT_ARCHITECTURE_DEFAULT = whatsapp_settings.WHATSAPP_AGENT_ARCHITECTURE_DEFAULT
WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT
WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_AGENT_REASONING_DEFAULT
WHATSAPP_TASK_AGENT_MODEL_DEFAULT = whatsapp_settings.WHATSAPP_TASK_AGENT_MODEL_DEFAULT
WHATSAPP_TASK_AGENT_REASONING_DEFAULT = whatsapp_settings.WHATSAPP_TASK_AGENT_REASONING_DEFAULT
WHATSAPP_CODEX_SPEED_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_SPEED_DEFAULT
WHATSAPP_CODEX_SERVICE_TIER_DEFAULT = whatsapp_settings.WHATSAPP_CODEX_SERVICE_TIER_DEFAULT
WHATSAPP_CONVERSATION_INTERVAL_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_INTERVAL_DEFAULT
WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT
WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT
WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT = whatsapp_settings.WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT
WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT = whatsapp_settings.WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT
WHATSAPP_JOB_DEADLINE_DEFAULT = whatsapp_settings.WHATSAPP_JOB_DEADLINE_DEFAULT
WHATSAPP_ML_RESEARCH_DEADLINE_SECONDS = 5 * 60
WHATSAPP_REPORT_DEADLINE_SECONDS = 10 * 60
WHATSAPP_MAX_SUBTASKS_DEFAULT = whatsapp_settings.WHATSAPP_MAX_SUBTASKS_DEFAULT
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT = whatsapp_settings.WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT
WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT
WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT = whatsapp_settings.WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT
WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_ENABLED_DEFAULT
WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_REQUIRED_DEFAULT
WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT
WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT = whatsapp_settings.WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT
WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT = whatsapp_settings.WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT
WHATSAPP_RETRY_DELAYS_SECONDS = (2, 5, 15)
WHATSAPP_MAX_RETRY_ATTEMPTS = 3
WHATSAPP_LOCAL_QUEUE_CAPACITY = 32
DUAL_AGENT_STATE_LOCK = threading.RLock()
BRIDGE_STATE_LOCK = threading.RLock()
BRIDGE_SHARED_STATE: Optional[dict[str, Any]] = None
BRIDGE_STORE: Optional[WhatsappBridgeStore] = None


CONFIG_LOCK = threading.RLock()
BRIDGE_STOP_EVENT = threading.Event()
BRIDGE_THREAD: Optional[threading.Thread] = None
DOWNLOAD_THREAD: Optional[threading.Thread] = None
ALERT_THREAD: Optional[threading.Thread] = None
TYPING_PULSES_LOCK = threading.RLock()
TYPING_PULSES: dict[str, threading.Event] = {}
PROGRESS_PULSES_LOCK = threading.RLock()
PROGRESS_PULSES: dict[str, threading.Event] = {}
RUNTIME_STATE: dict[str, Any] = {
    "running": False,
    "last_error": "",
    "last_processing_at": "",
    "last_worker_ok_at": "",
    "typing_last_error": "",
    "typing_last_sent_at": "",
    "progress_last_error": "",
    "progress_last_sent_at": "",
    "whisper_download_status": "idle",
    "whisper_download_error": "",
    "whisper_download_started_at": "",
}
MODEL_VALIDATION_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}
PHONE_DISPATCH_LOCK = threading.RLock()
PHONE_DISPATCH_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
PHONE_DISPATCH_EXECUTOR_WORKERS = 0
PHONE_DISPATCH_OLD_EXECUTORS: list[concurrent.futures.ThreadPoolExecutor] = []
PHONE_DISPATCH_QUEUES: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
PHONE_DISPATCH_ACTIVE: set[str] = set()
PHONE_DISPATCH_INFLIGHT = 0
PHONE_DISPATCH_EVENT_IDS: set[str] = set()
PHONE_DISPATCH_TICK_IDS: set[str] = set()
PHONE_DISPATCH_FUTURES: set[concurrent.futures.Future[Any]] = set()
PHONE_DISPATCH_SEQUENCE = itertools.count()
PHONE_DISPATCH_ACCEPTING = True
FUNCTION_MANAGER_LOCK = threading.RLock()
FUNCTION_MANAGER_EXECUTOR: Optional[concurrent.futures.ThreadPoolExecutor] = None
FUNCTION_MANAGER_EXECUTOR_WORKERS = 0
WEB_FALLBACK_LOCK = threading.RLock()
WEB_FALLBACK_CIRCUIT = {"failures": 0, "opened_until_epoch": 0.0, "last_error": ""}
FUNCTION_MANAGER_FUTURES: dict[str, concurrent.futures.Future[Any]] = {}
PHONE_LATENCY_SAMPLES: dict[str, deque[float]] = {
    "gateway_to_luna": deque(maxlen=500),
    "claimed_to_luna": deque(maxlen=500),
    "luna_duration": deque(maxlen=500),
    "sol_duration": deque(maxlen=500),
    "manager_duration": deque(maxlen=500),
    "manager_tools_duration": deque(maxlen=500),
    "completed_to_sent": deque(maxlen=500),
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_dir() -> Path:
    return Path(codex_console._codex_base_dir()).resolve()


def _info_dir() -> Path:
    path = Path(codex_console._codex_base_info_dir()).resolve()
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
        report_deadline_seconds=WHATSAPP_REPORT_DEADLINE_SECONDS,
        max_retry_attempts=WHATSAPP_MAX_RETRY_ATTEMPTS,
    )


def _normalize_progress_interval(value: Any) -> int:
    return whatsapp_settings.normalize_progress_interval(value)


def _normalize_voice_model(value: Any, fallback: str) -> str:
    return whatsapp_settings.normalize_voice_model(value, fallback)


def _normalize_voice_name(value: Any) -> str:
    return whatsapp_settings.normalize_voice_name(value)


def _normalize_voice_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    return whatsapp_settings.normalize_voice_int(value, fallback, minimum, maximum)


def _normalize_voice_config(config: dict[str, Any]) -> dict[str, Any]:
    return whatsapp_settings.normalize_voice_config(config)


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
        "version": 9,
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
        "function_manager_worker_count": WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT,
        "function_manager_runtime_pool_size": WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT,
        "voice_enabled": False,
        "voice_model": whatsapp_voice.VOICE_MODEL_DEFAULT,
        "voice_transcription_model": whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT,
        "voice_name": whatsapp_voice.VOICE_NAME_DEFAULT,
        "voice_language": "pt-BR",
        "voice_max_call_minutes": 30,
        "voice_silence_timeout_seconds": 90,
        "voice_long_task_offer_seconds": 90,
        "voice_max_concurrent_calls": 3,
        "voice_progress_interval_seconds": 8,
        "voice_transcript_retention": "transcript_only",
        "voice_store_audio": False,
        "voice_read_only": True,
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
        if isinstance(stored, dict):
            result.update(stored)
            try:
                stored_version = int(stored.get("version") or 0)
            except (TypeError, ValueError):
                stored_version = 0
            if stored_version < 9 and str(result.get("agent_architecture") or "").strip().lower() == "dual_codex":
                # A versao 9 mantem a arquitetura dual, mas torna prazos,
                # tentativas e avisos de espera deterministicos e limitados.
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
        result["enabled"] = bool(result.get("enabled"))
        result["version"] = 9
        result["pairing_pending"] = bool(result.get("pairing_pending"))
        result["machine_id"] = str(result.get("machine_id") or _host_machine_id())
        result["ai_model"] = _normalize_ai_model(result.get("ai_model"))
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
        result.update(_normalize_voice_config(result))
        return result


def _save_config(config: dict[str, Any]) -> dict[str, Any]:
    with CONFIG_LOCK:
        value = _default_config()
        value.update(dict(config or {}))
        value["version"] = 9
        value["ai_model"] = _normalize_ai_model(value.get("ai_model"))
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
        value.update(_normalize_voice_config(value))
        value["updated_at"] = _now()
        _json_write(_config_path(), value)
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


def _mask_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) < 5:
        return ""
    return f"+{digits[:2]} **** *** {digits[-4:]}"


def _normalize_registered_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in {10, 11}:
        digits = "55" + digits
    if len(digits) < 10 or len(digits) > 15 or len(set(digits)) == 1:
        raise HTTPException(status_code=400, detail="Informe um numero de WhatsApp valido, com DDD.")
    return digits


def _whatsapp_table_blocks_to_mobile(text: str) -> str:
    return whatsapp_formatting._whatsapp_table_blocks_to_mobile(text)


def _whatsapp_clean_markdown(value: Any) -> str:
    return whatsapp_formatting._whatsapp_clean_markdown(value)


def _whatsapp_image_requested(value: Any) -> bool:
    return whatsapp_media.image_requested(value)


def _whatsapp_image_references(value: Any) -> list[tuple[str, str]]:
    return whatsapp_media.image_references(value, max_images=WHATSAPP_MAX_OUTBOUND_IMAGES)


def _whatsapp_path_within(path: Path, roots: list[Path]) -> bool:
    return whatsapp_media.path_within(path, roots)


def _whatsapp_image_roots(client_id: Any) -> list[Path]:
    safe_client = codex_console._codex_safe_id(str(client_id or ""), "default")
    # Outbound WhatsApp images are deliberately restricted to the product-photo
    # directory of the authenticated tenant. A model-produced path must never
    # become a generic local-file exfiltration primitive.
    return [(_info_dir() / safe_client / "cadastro_fotos").resolve()]


def _whatsapp_resolve_image_reference(reference: Any, client_id: Any) -> Optional[Path]:
    raw = unquote(str(reference or "").strip().strip("<>\"'"))
    if not raw or re.match(r"^https?://", raw, re.IGNORECASE):
        return None
    raw_path = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    if ".." in Path(raw_path).parts:
        return None
    roots = _whatsapp_image_roots(client_id)
    tenant_photos = roots[0]
    candidates: list[Path] = []
    if raw_path.lower().startswith("/api/cadastro/foto-arquivo/"):
        candidates.append(tenant_photos / Path(raw_path).name)
    elif raw_path.lower().startswith("/api/cadastro/foto/"):
        candidates.append(tenant_photos / Path(raw_path).name)
    elif raw_path.lower().startswith("cadastro_fotos/"):
        candidates.append(tenant_photos / Path(raw_path).name)
    else:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            candidates.append(tenant_photos / candidate.name)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and _whatsapp_path_within(resolved, roots):
                return resolved
        except OSError:
            continue
    return None


def _whatsapp_sku_candidates(value: Any) -> list[str]:
    return whatsapp_media.sku_candidates(value)


def _whatsapp_normalized_sku(value: Any, *, strip_numeric_zeroes: bool = False) -> str:
    return whatsapp_media.normalized_sku(value, strip_numeric_zeroes=strip_numeric_zeroes)


def _whatsapp_image_matches_skus(path: Path, skus: list[str]) -> bool:
    return whatsapp_media.image_matches_skus(path, skus)


def _whatsapp_find_image_by_sku(request_text: Any, response: Any, client_id: Any) -> Optional[Path]:
    tenant_photos = _whatsapp_image_roots(client_id)[0]
    if not tenant_photos.is_dir():
        return None

    skus = _whatsapp_sku_candidates(f"{request_text}\n{response}")
    files = [item for item in tenant_photos.iterdir() if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}]
    for sku in skus:
        for item in files:
            if _whatsapp_image_matches_skus(item, [sku]):
                return item.resolve()
    return None


def _whatsapp_image_mime(path: Path) -> str:
    return whatsapp_media.image_mime(path)


def _whatsapp_prepare_outbound_image(path: Path) -> Optional[dict[str, Any]]:
    mime = _whatsapp_image_mime(path)
    size = path.stat().st_size if path.is_file() else 0
    try:
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS
        with Image.open(path) as source:
            if source.width * source.height > WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS:
                return None
            source.load()
            image = ImageOps.exif_transpose(source)
            if getattr(image, "is_animated", False):
                image.seek(0)
            if mime == "image/png":
                image.thumbnail((2048, 2048))
                handle = tempfile.NamedTemporaryFile(prefix="jk-wa-outbound-", suffix=".png", delete=False)
                temporary = Path(handle.name)
                handle.close()
                image.save(temporary, format="PNG", optimize=True)
                converted_size = temporary.stat().st_size
                if 0 < converted_size <= WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
                    return {
                        "path": temporary,
                        "mime_type": "image/png",
                        "size": converted_size,
                        "cleanup": True,
                        "filename": f"{path.stem}.png",
                    }
                temporary.unlink(missing_ok=True)
            if image.mode in {"RGBA", "LA"}:
                canvas = Image.new("RGB", image.size, "white")
                alpha = image.getchannel("A") if "A" in image.getbands() else None
                canvas.paste(image.convert("RGB"), mask=alpha)
                image = canvas
            else:
                image = image.convert("RGB")
            image.thumbnail((2048, 2048))
            handle = tempfile.NamedTemporaryFile(prefix="jk-wa-outbound-", suffix=".jpg", delete=False)
            temporary = Path(handle.name)
            handle.close()
            for quality in (88, 78, 68, 58):
                image.save(temporary, format="JPEG", quality=quality, optimize=True)
                if temporary.stat().st_size <= WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
                    break
            converted_size = temporary.stat().st_size
            if not 0 < converted_size <= WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
                temporary.unlink(missing_ok=True)
                return None
            return {"path": temporary, "mime_type": "image/jpeg", "size": converted_size, "cleanup": True, "filename": f"{path.stem}.jpg"}
    except Exception:
        # Pillow is optional in a few development installs. The Worker still
        # revalidates MIME, size and magic bytes before an upload is accepted.
        if mime in {"image/jpeg", "image/png"} and 0 < size <= WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
            return {"path": path, "mime_type": mime, "size": size, "cleanup": False, "filename": path.name}
        return None


def _whatsapp_strip_image_references(value: Any) -> str:
    return whatsapp_media.strip_image_references(value)


def _whatsapp_outbound_image_caption(response: Any, alt: Any = "") -> str:
    return whatsapp_media.outbound_image_caption(response, alt)


def _whatsapp_deliver_requested_images(
    config: dict[str, Any],
    message_id: str,
    response: Any,
    request_text: Any,
    client_id: Any,
    max_images: int = WHATSAPP_MAX_OUTBOUND_IMAGES,
) -> tuple[str, list[dict[str, Any]]]:
    original = str(response or "")
    if not _whatsapp_image_requested(request_text):
        return original, []
    references = _whatsapp_image_references(original)
    requested_skus = _whatsapp_sku_candidates(request_text)
    candidates: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()
    for alt, reference in references:
        resolved = _whatsapp_resolve_image_reference(reference, client_id)
        if resolved and requested_skus and not _whatsapp_image_matches_skus(resolved, requested_skus):
            continue
        if resolved and str(resolved).lower() not in seen_paths:
            seen_paths.add(str(resolved).lower())
            candidates.append((alt, resolved))
    if not candidates:
        fallback = _whatsapp_find_image_by_sku(request_text, original, client_id)
        if fallback:
            candidates.append(("", fallback))

    results: list[dict[str, Any]] = []
    max_images = max(0, min(int(max_images or 0), WHATSAPP_MAX_OUTBOUND_IMAGES))
    for alt, path in candidates[:max_images]:
        prepared = _whatsapp_prepare_outbound_image(path)
        if not prepared:
            results.append({"success": False, "error": "image_prepare_failed", "filename": path.name})
            continue
        try:
            result = _post_outbound_image(
                config,
                message_id,
                Path(prepared["path"]),
                str(prepared["mime_type"]),
                _whatsapp_outbound_image_caption(original, alt),
                str(prepared.get("filename") or path.name),
            )
            results.append({"success": bool(result.get("success")), "status": result.get("status"), "filename": path.name, "error": result.get("error")})
        except Exception as exc:
            results.append({"success": False, "error": str(exc)[:500], "filename": path.name})
        finally:
            if prepared.get("cleanup"):
                try:
                    Path(prepared["path"]).unlink(missing_ok=True)
                except OSError:
                    pass

    clean_response = _whatsapp_strip_image_references(original)
    sent = sum(1 for item in results if item.get("success"))
    if sent:
        return clean_response or ("Imagem enviada conforme solicitado." if sent == 1 else f"{sent} imagens enviadas conforme solicitado."), results
    fallback_text = "Não consegui anexar a imagem no WhatsApp agora. A referência interna foi removida para não enviar um link quebrado."
    return f"{clean_response}\n\n{fallback_text}".strip(), results or [{"success": False, "error": "image_not_found"}]


def _whatsapp_report_chart_path(artifact: dict[str, Any], client_id: Any) -> Optional[Path]:
    try:
        root = whatsapp_report_visuals.chart_output_dir(_info_dir(), client_id).resolve()
        path = Path(str(artifact.get("path") or "")).resolve()
        path.relative_to(root)
        if not path.is_file() or path.suffix.lower() != ".png":
            return None
        expires_at = int(artifact.get("expires_at") or 0)
        if expires_at and expires_at < int(time.time()):
            path.unlink(missing_ok=True)
            return None
        size = path.stat().st_size
        if size <= 0 or size > WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if str(artifact.get("sha256") or "").strip().lower() != digest:
            return None
        if _whatsapp_image_mime(path) != "image/png":
            return None
        return path
    except (OSError, ValueError, TypeError):
        return None


def _whatsapp_report_document_path(artifact: dict[str, Any], client_id: Any) -> Optional[Path]:
    try:
        root = whatsapp_report_files.output_dir(_info_dir(), client_id).resolve()
        path = Path(str(artifact.get("path") or "")).resolve()
        path.relative_to(root)
        kind = str(artifact.get("kind") or "").strip().lower()
        expected_suffix = ".pdf" if kind == "pdf" else ".xlsx" if kind == "xlsx" else ""
        expected_mime = whatsapp_report_files.FORMAT_MIMES.get(kind, "")
        if not expected_suffix or not path.is_file() or path.suffix.lower() != expected_suffix:
            return None
        expires_at = int(artifact.get("expires_at") or 0)
        if expires_at and expires_at < int(time.time()):
            path.unlink(missing_ok=True)
            return None
        size = path.stat().st_size
        if size <= 0 or size > WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES:
            return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if str(artifact.get("sha256") or "").strip().lower() != digest:
            return None
        if str(artifact.get("mime_type") or "").strip().lower() != expected_mime:
            return None
        return path
    except (OSError, ValueError, TypeError):
        return None


def _whatsapp_deliver_report_artifacts(
    config: dict[str, Any],
    message_id: str,
    artifacts: Any,
    client_id: Any,
    max_images: int = 4,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    limit = max(0, min(int(max_images or 0), 4))
    for artifact in [item for item in (artifacts or []) if isinstance(item, dict)][:limit]:
        artifact_type = str(artifact.get("artifact_type") or "report_chart").strip().lower()
        is_document = artifact_type in {"report_pdf", "report_xlsx"}
        path = (
            _whatsapp_report_document_path(artifact, client_id)
            if is_document
            else _whatsapp_report_chart_path(artifact, client_id)
        )
        if not path:
            results.append({"success": False, "artifact_type": artifact_type, "error": "report_artifact_invalid_or_expired"})
            continue
        if is_document:
            try:
                result = _post_outbound_document(
                    config,
                    message_id,
                    path,
                    str(artifact.get("mime_type") or ""),
                    "",
                    str(artifact.get("filename") or path.name),
                    artifact_type=artifact_type,
                )
                results.append(
                    {
                        "success": bool(result.get("success")),
                        "status": result.get("status"),
                        "artifact_type": artifact_type,
                        "kind": artifact.get("kind"),
                        "filename": path.name,
                        "error": result.get("error"),
                    }
                )
            except Exception as exc:
                results.append({"success": False, "artifact_type": artifact_type, "filename": path.name, "error": str(exc)[:500]})
            continue
        prepared = _whatsapp_prepare_outbound_image(path)
        if not prepared:
            results.append({"success": False, "error": "report_chart_prepare_failed", "filename": path.name})
            path.unlink(missing_ok=True)
            continue
        try:
            result = _post_outbound_image(
                config,
                message_id,
                Path(prepared["path"]),
                str(prepared["mime_type"]),
                "",
                str(prepared.get("filename") or path.name),
                artifact_type=artifact_type,
            )
            results.append(
                {
                    "success": bool(result.get("success")),
                    "status": result.get("status"),
                    "artifact_type": artifact_type,
                    "kind": artifact.get("kind"),
                    "filename": path.name,
                    "error": result.get("error"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "success": False,
                    "artifact_type": artifact_type,
                    "kind": artifact.get("kind"),
                    "filename": path.name,
                    "error": str(exc)[:500],
                }
            )
        finally:
            if prepared.get("cleanup"):
                try:
                    Path(prepared["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
    return results


def _whatsapp_text_key(value: Any) -> str:
    return whatsapp_formatting._whatsapp_text_key(value)


def _whatsapp_daily_sales_report_requested(value: Any) -> bool:
    return whatsapp_formatting._whatsapp_daily_sales_report_requested(value)


def _whatsapp_sales_report_requested(value: Any) -> bool:
    return whatsapp_formatting._whatsapp_sales_report_requested(value)


def _whatsapp_query_only_domains(value: Any) -> list[str]:
    """Classifica os dominios que nunca podem sofrer mutacao pelo WhatsApp."""
    text = _whatsapp_text_key(value)
    domains: list[str] = []
    implicit_latest_ml_sale = bool(
        re.search(r"\b(ultima|ultimo|mais recente)\b", text)
        and re.search(r"\bsku\s*[a-z0-9._/-]+\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|ml)\b", text)
        and not re.search(r"\b(devolucao|devolucoes|reembolso|estorno)\b", text)
    )
    if implicit_latest_ml_sale or _whatsapp_daily_sales_report_requested(value) or re.search(
        r"\b(venda|vendas|vendido|vendidos|faturamento|pedidos?|devolucao|devolucoes|sync de vendas|sincronizacao de vendas)\b",
        text,
    ):
        domains.append("vendas")
    if re.search(r"\b(mercado livre|mercadolivre|ml|anuncio|anuncios|mlb\d+)\b", text):
        domains.append("anuncios_ml")
    if re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", text):
        domains.append("estoque")
        if re.search(r"\b(full|fulfillment|mercado envios)\b", text):
            domains.append("mercado_full")
    return domains


def _whatsapp_source_policy(value: Any) -> dict[str, Any]:
    try:
        from backend.services import codex_assistant

        policy = codex_assistant._assistant_source_routing_policy(str(value or ""))
    except Exception:
        policy = {}
    return dict(policy) if isinstance(policy, dict) else {}


def _whatsapp_readonly_inquiry(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    if not text:
        return False
    explicit_mutation = bool(
        re.search(
            r"\b(responda|envie a resposta|mande a resposta|publique|pause|ative|desative|altere|mude|"
            r"sincronize|cancele|remova|exclua|aprove|rejeite)\b",
            text,
        )
    )
    if explicit_mutation:
        return False
    subject = bool(
        re.search(
            r"\b(saldo|estoque|anuncio|anuncios|informacao|informacoes|detalhe|detalhes|pergunta|perguntas|"
            r"pendencia|pendencias|fila|venda|vendas|pedido|pedidos|preco|status|relatorio|dados)\b",
            text,
        )
    )
    inquiry = bool(
        re.search(r"\b(tem|ha|existe|existem|chegou|chegaram|qual|quais|quanto|quantos|quantas)\b", text)
        or re.search(r"\b(me diga|mostre|consulte|verifique|liste|informe|quero saber|pode ver|consegue ver)\b", text)
    )
    return bool(subject and inquiry)


def _whatsapp_general_answer_request(value: Any, session: Optional[dict[str, Any]] = None) -> bool:
    """Distingue conversa geral de consultas/acoes sobre o JK Sistema."""
    text = _whatsapp_text_key(value)
    if not text:
        return False

    internal_anchor = bool(
        re.search(
            r"\b(jk sistema|sistema jk|black jhon|black john|neste sistema|no sistema|do sistema|"
            r"modulo|tela|configuracoes|favoritos|medias e compras|fila interna|funcao interna|"
            r"tarefa interna|cadastro interno|banco de dados interno)\b",
            text,
        )
    )
    if internal_anchor:
        return False

    stores: list[str] = []
    try:
        stores = _whatsapp_session_stores(session or {})
    except Exception:
        stores = []
    if stores and _whatsapp_exact_store_matches(str(value or ""), stores):
        return False

    technical_identifier = bool(
        re.search(r"\b(?:sku|mlb|pack|order|pedido|venda)\s*[:#-]?\s*[a-z0-9][a-z0-9._-]*\b", text)
        or re.search(r"\b\d{10,20}\b", text)
    )
    if technical_identifier:
        return False

    business_subject = bool(
        re.search(
            r"\b(estoque|saldo|venda|vendas|pedido|pedidos|anuncio|anuncios|mercado livre|bling|"
            r"full|loja|lojas|sku|comprador|cliente|devolucao|devolucoes|reclamacao|reclamacoes|"
            r"pergunta|perguntas|pos venda|relatorio|importacao|fornecedor|preco|margem|faturamento)\b",
            text,
        )
    )
    owned_or_current_data = bool(
        re.search(
            r"\b(meu|meus|minha|minhas|nosso|nossos|nossa|nossas|da loja|das lojas|cadastrado|"
            r"cadastrada|atual|agora|hoje|ontem|ultima|ultimas|ultimo|ultimos|pendente|pendentes)\b",
            text,
        )
    )
    operational_request = bool(
        re.search(
            r"\b(consulte|consultar|verifique|verificar|liste|listar|mostre|mostrar|informe|informar|"
            r"busque|buscar|atualize|atualizar|sincronize|sincronizar|altere|alterar|remova|remover|"
            r"responda|responder|envie|enviar|publique|publicar|cancele|cancelar)\b",
            text,
        )
    )
    if business_subject and (owned_or_current_data or operational_request):
        return False

    general_marker = bool(
        re.search(
            r"\b(o que e|como funciona|como posso|como fazer|explique|qual a diferenca|para que serve|"
            r"por que|porque|quem e|onde fica|me de uma dica|me de ideias|escreva|redija|traduza|"
            r"corrija (?:a|esta|essa|este|esse|frase|texto)|resuma (?:o|a|este|esta|esse|essa|texto))\b",
            text,
        )
    )
    if general_marker:
        return True

    # Sem qualquer assunto operacional conhecido, a mensagem e conversa geral
    # (saudacao, conhecimento, escrita, calculo ou pergunta cotidiana).
    return not business_subject


def _whatsapp_mutation_intent(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    if _whatsapp_readonly_inquiry(value):
        return False
    send_mutation = bool(
        re.search(r"\b(envie|enviar|mande|mandar)\b", text)
        and re.search(r"\b(resposta|mensagem|pergunta|aprovacao|publicacao)\b", text)
    )
    strong_mutation = send_mutation or bool(
        re.search(
            r"\b(pause|pausar|ative|ativar|desative|desativar|publique|publicar|responda|responder|"
            r"aprove|aprovar|cancele|cancelar|mude|mudar|troque|trocar|remova|remover|exclua|excluir|"
            r"delete|deletar|sincronize|sincronizar|altere|alterar|corrija|corrigir)\b",
            text,
        )
    )
    # Verbos como "envie" descrevem frequentemente a entrega de uma consulta,
    # e "atualize agora ... via API" pede dados frescos, nao uma sincronizacao.
    query_delivery = bool(
        re.search(r"\b(envie|enviar|mande|mandar|mostre|mostrar|passe|passar|gere|gerar)\b", text)
        and re.search(r"\b(relatorio|resumo|consulta|dados|informacoes|resultados|lista|listagem)\b", text)
    )
    fresh_api_query = bool(
        re.search(r"\b(atualize|atualizar)\b", text)
        and re.search(r"\b(api|dados|consulta|relatorio|resumo|listagem|resultados)\b", text)
        and not re.search(r"\b(preco|estoque|titulo|status|situacao|quantidade|saldo|resposta|mensagem)\b", text)
    )
    if (query_delivery or fresh_api_query) and not strong_mutation:
        return False
    if strong_mutation:
        return True
    if codex_console._codex_prompt_pede_alteracao(str(value or "")):
        return True
    return bool(
        re.search(
            r"\b(pause|pausar|ative|ativar|desative|desativar|publique|publicar|responda|responder|"
            r"aprove|aprovar|cancele|cancelar|mude|mudar|troque|trocar|remova|remover|exclua|excluir|"
            r"delete|deletar|sincronize|sincronizar|atualize|atualizar|altere|alterar|corrija|corrigir)\b",
            text,
        )
    )


def _whatsapp_post_sale_action(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        re.search(r"\b(pergunta|perguntas|pos venda|conversa|resposta ao cliente|mensagem ao cliente)\b", text)
        and re.search(r"\b(responda|responder|envie|enviar|mande|mandar|aprove|aprovar)\b", text)
        and not re.search(r"\b(anuncio|anuncios|preco|estoque|titulo|status do anuncio|pausar|publicar)\b", text)
    )


def _whatsapp_protected_mutation_domains(value: Any) -> list[str]:
    domains = _whatsapp_query_only_domains(value)
    if _whatsapp_post_sale_action(value):
        domains = [domain for domain in domains if domain != "anuncios_ml"]
    return domains if domains and _whatsapp_mutation_intent(value) else []


def _whatsapp_action_spec_query_only_domains(spec: Any) -> list[str]:
    if spec is None:
        return []
    if isinstance(spec, dict):
        getter = lambda key, default="": spec.get(key, default)
    else:
        getter = lambda key, default="": getattr(spec, key, default)
    text = _whatsapp_text_key(
        " ".join(
            str(item or "")
            for item in (
                getter("id") or getter("action_id"),
                getter("module"),
                getter("label"),
                getter("status_kind"),
                " ".join(getter("side_effects", ()) or ()),
            )
        )
    )
    domains: list[str] = []
    if re.search(r"\b(vendas?|vendas sync|vendas cancel|sincronizar vendas)\b", text):
        domains.append("vendas")
    module_name = str(getter("module") or "").strip().lower()
    action_id = str(getter("id") or getter("action_id") or "").strip().lower()
    if module_name == "anuncios_ml" or action_id.startswith("ml.anuncio") or re.search(r"\b(anuncio|anuncios|mercado_livre)\b", text):
        domains.append("anuncios_ml")
    return list(dict.fromkeys(domains))


def _whatsapp_load_store_configs(client_id: Any) -> list[dict[str, Any]]:
    tenant = str(client_id or "default").strip() or "default"
    try:
        from backend.services.integracoes import carregar_lojas

        return [item for item in (carregar_lojas(tenant) or []) if isinstance(item, dict)]
    except Exception:
        path = (_info_dir() / tenant / "lojas_config.json").resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _whatsapp_authorized_api_stores(
    client_id: Any,
    permissions: Any,
    domains: list[str],
    providers: Optional[list[str]] = None,
) -> list[str]:
    permissions = permissions if isinstance(permissions, dict) else {}
    is_full = permissions.get("full") is True
    providers = [str(item or "").strip() for item in (providers or []) if str(item or "").strip()]
    if "vendas" in domains and not (is_full or permissions.get("vendas") is True):
        return []
    if "anuncios_ml" in domains and not (is_full or permissions.get("anuncios_ml") is True):
        return []
    if "estoque" in domains and not (is_full or permissions.get("estoque") is True):
        return []
    if "mercado_full" in domains and not (is_full or permissions.get("mercado_full") is True):
        return []
    if "bling" in providers and not (is_full or permissions.get("integracao") is True):
        return []
    if "mercado_livre" in providers and "vendas" in domains and not (is_full or permissions.get("anuncios_ml") is True):
        return []

    stores: list[str] = []
    for store in _whatsapp_load_store_configs(client_id):
        name = str(store.get("nome") or "").strip()
        integrations = store.get("integracoes") if isinstance(store.get("integracoes"), dict) else {}
        ml = integrations.get("mercadolivre") if isinstance(integrations, dict) else {}
        bling = integrations.get("bling") if isinstance(integrations, dict) else {}
        ml_connected = isinstance(ml, dict) and bool(str(ml.get("access_token") or "").strip())
        bling_connected = isinstance(bling, dict) and bool(str(bling.get("access_token") or "").strip())
        if not name:
            continue
        if ("anuncios_ml" in domains or "mercado_full" in domains or "mercado_livre" in providers) and not ml_connected:
            continue
        if "bling" in providers and not bling_connected:
            continue
        if "vendas" in domains and not providers and not (bling_connected or ml_connected):
            continue
        if name not in stores:
            stores.append(name)
    return stores


def _whatsapp_exact_store_matches(value: Any, stores: list[str]) -> list[str]:
    text = _whatsapp_text_key(value)
    raw_matches: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for store in stores:
        key = _whatsapp_text_key(store)
        if (
            key
            and key not in seen_keys
            and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text)
        ):
            seen_keys.add(key)
            raw_matches.append((store, key))
    # "JK Pecas" tambem contem "JK". Nesse caso existe uma unica mencao,
    # portanto prevalece o nome cadastrado mais especifico/mais longo.
    return [
        store
        for store, key in raw_matches
        if not any(key != other_key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", other_key) for _, other_key in raw_matches)
    ]


def _whatsapp_all_stores_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        re.search(
            r"\b(todas as lojas|todas lojas|todas as contas|cada loja|cada conta|por loja|por conta|"
            r"loja a loja|conta a conta|separad[oa]s? por loja|compare as lojas|comparar as lojas|visao geral)\b",
            text,
        )
    )


def _whatsapp_store_scoped_request(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    if not text:
        return False
    return bool(
        re.search(
            r"\b(venda|vendas|faturamento|pedido|pedidos|devolucao|devolucoes|estoque|saldo|sku|produto|produtos|"
            r"anuncio|anuncios|mercado livre|mercadolivre|preco|precos|margem|lucro|relatorio|relatorios|"
            r"pergunta|perguntas|pos venda|pos-venda|comprador|compradores|promocao|promocoes)\b",
            text,
        )
    )


def _whatsapp_session_stores(session: dict[str, Any]) -> list[str]:
    stores: list[str] = []
    for item in _whatsapp_load_store_configs(session.get("client_id")):
        name = str(item.get("nome") or "").strip()
        if name and name not in stores:
            stores.append(name)
    return stores


def _whatsapp_store_scope_policy(value: Any, session: dict[str, Any]) -> dict[str, Any]:
    if not _whatsapp_store_scoped_request(value):
        return {}
    stores = _whatsapp_session_stores(session)
    matches = _whatsapp_exact_store_matches(value, stores)
    all_stores = _whatsapp_all_stores_requested(value) or (
        len(matches) > 1 and bool(re.search(r"\b(compare|comparar|comparacao|entre)\b", _whatsapp_text_key(value)))
    )
    policy: dict[str, Any] = {
        "mode": "store_scope",
        "read_only": True,
        "store_required": True,
        "authorized_stores": stores,
        "store_matches": matches,
        "store": matches[0] if len(matches) == 1 else "",
        "store_mode": "all" if all_stores else "single",
    }
    if all_stores:
        policy["stores"] = matches if len(matches) > 1 else stores
    return policy


def _whatsapp_api_query_requires_store(value: Any, domains: list[str]) -> bool:
    del value
    return bool(set(domains) & {"vendas", "anuncios_ml", "estoque", "mercado_full"})


def _whatsapp_requested_api_providers(value: Any, domains: list[str]) -> list[str]:
    source_policy = _whatsapp_source_policy(value)
    preferred = [
        str(item or "").strip()
        for item in (source_policy.get("preferred_providers") or [])
        if str(item or "").strip() in {"bling", "mercado_livre"}
    ]
    if preferred:
        return list(dict.fromkeys(preferred))
    text = _whatsapp_text_key(value)
    providers: list[str] = []
    if "bling" in text:
        providers.append("bling")
    if "anuncios_ml" in domains or re.search(r"\b(mercado livre|mercadolivre|mlb\d+)\b", text):
        providers.append("mercado_livre")
    if "vendas" in domains and re.search(r"\b(api|apis|via api)\b", text) and not providers:
        providers.extend(["bling", "mercado_livre"])
    return list(dict.fromkeys(providers))


def _whatsapp_query_policy(value: Any, session: dict[str, Any]) -> dict[str, Any]:
    domains = _whatsapp_query_only_domains(value)
    if _whatsapp_post_sale_action(value):
        domains = [domain for domain in domains if domain != "anuncios_ml"]
    if not domains:
        return {}
    policy: dict[str, Any] = {
        "mode": "query_only",
        "domains": domains,
        "read_only": True,
        "deny_approval": True,
        "store_required": _whatsapp_api_query_requires_store(value, domains),
        "store": "",
        "base_request": str(value or "").strip()[:2000],
    }
    policy["providers"] = _whatsapp_requested_api_providers(value, domains)
    source_policy = _whatsapp_source_policy(value)
    if source_policy:
        policy["source_policy"] = source_policy
        policy["fresh"] = bool(source_policy.get("force_refresh"))
        policy["bypass_cache"] = bool(source_policy.get("force_refresh"))
    text = _whatsapp_text_key(value)
    limit_match = re.search(r"\b(?:limite|limit|pagina de)\s*(\d{1,5})\b", text)
    sales_report = _whatsapp_sales_report_requested(value)
    policy["report_mode"] = sales_report
    policy["limit"] = (
        max(1, min(int(limit_match.group(1)), 20000 if sales_report else 100))
        if limit_match
        else 20000
        if sales_report
        else 20
    )
    offset_match = re.search(r"\b(?:offset|a partir de)\s*(\d{1,6})\b", text)
    if offset_match:
        policy["offset"] = max(0, min(int(offset_match.group(1)), 100000))
    elif re.search(r"\b(proximos|proximas|mais resultados|pagina seguinte)\b", text):
        policy["pagination"] = "next"
    if (
        re.search(r"\b(atualize agora|sem cache|direto da api|dados mais recentes|dados atualizados)\b", text)
        or ("api" in text and re.search(r"\b(atualize|atualizar)\b", text))
    ):
        policy["fresh"] = True
        policy["bypass_cache"] = True

    if policy["store_required"]:
        stores = _whatsapp_authorized_api_stores(
            session.get("client_id"),
            session.get("permissions"),
            domains,
            policy.get("providers"),
        )
        matches = _whatsapp_exact_store_matches(value, stores)
        policy["authorized_stores"] = stores
        policy["store_matches"] = matches
        all_stores = _whatsapp_all_stores_requested(value) or (
            len(matches) > 1 and bool(re.search(r"\b(compare|comparar|comparacao|entre)\b", text))
        )
        policy["store_mode"] = "all" if all_stores else "single"
        if all_stores:
            policy["stores"] = matches if len(matches) > 1 else stores
        elif len(matches) == 1:
            policy["store"] = matches[0]
    return policy


def _whatsapp_pagination_request(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(re.fullmatch(r"(?:os |as )?(?:proximos|proximas|mais resultados|pagina seguinte|continuar|continue)", text))


def _whatsapp_contextual_report_request(value: Any) -> bool:
    """Reconhece uma nova rodada de relatorio que depende do contexto recente."""
    text = _whatsapp_text_key(value)
    if not text:
        return False
    relative_period = bool(
        re.search(r"\b(?:deste|desse|este|nesse|no) mes\b", text)
        or re.search(r"\bmes atual\b", text)
    )
    same_store = bool(re.search(r"\b(?:na |da |pela )?mesma (?:loja|conta)\b", text))
    report_language = bool(re.search(r"\b(relatorio|resumo|analise|vendas?|pedidos?|faturamento)\b", text))
    return bool(relative_period or (same_store and report_language) or text in {"mesma loja", "mesma conta"})


def _whatsapp_contextual_report_period(value: Any) -> tuple[str, str]:
    text = _whatsapp_text_key(value)
    if not (
        re.search(r"\b(?:deste|desse|este|nesse|no) mes\b", text)
        or re.search(r"\bmes atual\b", text)
    ):
        return "", ""
    try:
        current = datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        current = datetime.now().astimezone()
    return current.replace(day=1).date().isoformat(), current.date().isoformat()


def _whatsapp_implicit_store_followup(value: Any, *, context_age: float, has_direct_policy: bool) -> bool:
    text = _whatsapp_text_key(value)
    if not text or _whatsapp_all_stores_requested(value):
        return False
    strong_reference = bool(
        re.search(r"^(?:agora|entao|e\s|tambem|continue|continuando)\b", text)
        or re.search(
            r"\b(?:dess[ae]s?|dest[ae]s?|del[ae]s?|sobre isso|sobre eles|sobre elas|"
            r"mais detalhes?|detalhe melhor|motivo de cada|cada (?:um|uma|pedido|venda|devolucao|reclamacao)|"
            r"mesma loja|mesma conta)\b",
            text,
        )
    )
    if strong_reference:
        return True
    word_count = len(re.findall(r"[a-z0-9]+", text))
    return bool(
        has_direct_policy
        and context_age <= WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS
        and word_count <= 16
    )


def _whatsapp_inherit_query_store_context(
    value: Any,
    direct_policy: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Herda loja apenas de uma consulta recente da mesma conversa telefônica."""
    if _whatsapp_mutation_intent(value):
        return {}
    if direct_policy.get("store_mode") == "all" or len(direct_policy.get("store_matches") or []) == 1:
        return {}
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    previous = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not previous:
        return {}
    context_age = time.time() - float(previous.get("updated_at") or 0)
    if context_age < 0 or context_age > WHATSAPP_QUERY_CONTEXT_TTL_SECONDS:
        return {}
    if not _whatsapp_implicit_store_followup(
        value,
        context_age=context_age,
        has_direct_policy=bool(direct_policy),
    ):
        return {}

    domains = [
        str(item or "").strip()
        for item in (direct_policy.get("domains") or previous.get("domains") or [])
        if str(item or "").strip() in {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    ]
    if not domains:
        return {}
    providers = [
        str(item or "").strip()
        for item in (direct_policy.get("providers") or previous.get("providers") or [])
        if str(item or "").strip()
    ]
    authorized_stores = _whatsapp_authorized_api_stores(
        session.get("client_id"),
        session.get("permissions"),
        domains,
        providers,
    )
    previous_mode = str(previous.get("store_mode") or "single").strip() or "single"
    previous_store = str(previous.get("store") or "").strip()
    previous_stores = [
        str(item or "").strip()
        for item in (previous.get("stores") or [])
        if str(item or "").strip()
    ]

    policy = dict(direct_policy) if direct_policy else {
        "mode": "query_only",
        "read_only": True,
        "deny_approval": True,
        "report_mode": bool(previous.get("report_mode")),
        "limit": max(1, min(int(previous.get("limit") or 20), 20000 if previous.get("report_mode") else 100)),
    }
    policy.update({
        "domains": domains,
        "providers": providers,
        "store_required": True,
        "authorized_stores": authorized_stores,
        "base_request": str(
            value
            if direct_policy
            else previous.get("base_request") or value or ""
        ).strip()[:2000],
        "continuation_request": str(value or "").strip()[:1000],
        "inherited": True,
        "inherited_store_context": True,
    })
    current_sku, current_item_id = _function_manager_extract_identifiers(value)
    inherited_sku = str(previous.get("sku") or "").strip()
    inherited_item_id = str(previous.get("item_id") or "").strip().upper()
    policy["sku"] = str(policy.get("sku") or current_sku or inherited_sku).strip()[:100]
    policy["item_id"] = str(policy.get("item_id") or current_item_id or inherited_item_id).strip().upper()[:60]
    if (policy.get("sku") and not current_sku) or (policy.get("item_id") and not current_item_id):
        policy["inherited_product_context"] = True
        previous_request = str(previous.get("base_request") or "").strip()
        continuation = str(value or "").strip()
        policy["context_request"] = "\n".join(
            part for part in (previous_request, f"Continuacao: {continuation}" if continuation else "") if part
        )[:3000]
    if not isinstance(policy.get("source_policy"), dict) or not policy.get("source_policy"):
        policy["source_policy"] = (
            dict(previous.get("source_policy") or {})
            if isinstance(previous.get("source_policy"), dict)
            else {}
        )

    if previous_mode == "all":
        scoped_stores = [store for store in previous_stores if store in authorized_stores]
        policy.update({
            "store": "",
            "store_mode": "all" if scoped_stores else "single",
            "stores": scoped_stores,
            "store_matches": [],
        })
        if not scoped_stores:
            policy["continuation_invalid_store"] = True
        return policy

    valid_store = previous_store if previous_store in authorized_stores else ""
    policy.update({
        "store": valid_store,
        "store_mode": "single",
        "stores": [],
        "store_matches": [valid_store] if valid_store else [],
    })
    if not valid_store:
        policy["continuation_invalid_store"] = True
    return policy


def _whatsapp_query_continuation_policy(
    value: Any,
    state: dict[str, Any],
    conversation_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    pagination_request = _whatsapp_pagination_request(value)
    contextual_report = _whatsapp_contextual_report_request(value)
    if not pagination_request and not contextual_report:
        return {}
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    previous = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not previous or time.time() - float(previous.get("updated_at") or 0) > WHATSAPP_QUERY_CONTEXT_TTL_SECONDS:
        return {}
    domains = [
        str(item or "").strip()
        for item in (previous.get("domains") or [])
        if str(item or "").strip() in {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    ]
    if not domains:
        return {}
    if contextual_report and not (bool(previous.get("report_mode")) or "vendas" in domains):
        return {}
    store = str(previous.get("store") or "").strip()
    store_mode = str(previous.get("store_mode") or "single").strip() or "single"
    previous_stores = [
        str(item or "").strip()
        for item in (previous.get("stores") or [])
        if str(item or "").strip()
    ]
    store_required = bool(previous.get("store_required"))
    providers = [str(item or "").strip() for item in (previous.get("providers") or []) if str(item or "").strip()]
    source_policy = dict(previous.get("source_policy") or {}) if isinstance(previous.get("source_policy"), dict) else {}
    if contextual_report:
        providers = ["mercado_livre"]
        source_policy["required_tools"] = ["mercado_livre_orders"]
        source_policy["preferred_providers"] = ["mercado_livre"]
        source_policy["force_refresh"] = True
        source_policy["forbidden_tools"] = [
            str(item or "").strip()
            for item in (source_policy.get("forbidden_tools") or [])
            if str(item or "").strip() and str(item or "").strip() != "mercado_livre_orders"
        ]
    authorized_stores = _whatsapp_authorized_api_stores(
        session.get("client_id"),
        session.get("permissions"),
        domains,
        providers,
    ) if store_required else []
    scoped_stores = [item for item in previous_stores if item in authorized_stores]
    invalid_store = bool(
        store_required
        and (
            (store_mode == "all" and not scoped_stores)
            or (store_mode != "all" and store not in authorized_stores)
        )
    )
    if invalid_store:
        return {
            "mode": "query_only",
            "domains": domains,
            "read_only": True,
            "deny_approval": True,
            "store_required": True,
            "store": "",
            "authorized_stores": authorized_stores,
            "store_matches": [],
            "providers": providers,
            "source_policy": source_policy,
            "continuation_invalid_store": True,
        }
    report_mode = bool(previous.get("report_mode") or contextual_report)
    limit = max(1, min(int(previous.get("limit") or 20), 20000 if report_mode else 100))
    if contextual_report:
        period_start, period_end = _whatsapp_contextual_report_period(value)
        if period_start and period_end:
            scope_label = ", ".join(scoped_stores) if store_mode == "all" else store
            base_request = (
                "Relatorio completo de vendas pelo Mercado Livre"
                + (f" da loja {scope_label}" if scope_label else "")
                + f", no periodo de {period_start} a {period_end}."
            )
        else:
            base_request = str(previous.get("base_request") or value or "").strip()[:2000]
        result = {
            "mode": "query_only",
            "domains": domains,
            "read_only": True,
            "deny_approval": True,
            "store_required": store_required,
            "store": store if store_mode != "all" else "",
            "store_mode": store_mode,
            "authorized_stores": authorized_stores,
            "store_matches": [store] if store_mode != "all" and store else [],
            "providers": providers,
            "source_policy": source_policy,
            "inherited": True,
            "contextual_report": True,
            "offset": 0,
            "provider_offsets": {},
            "limit": 20000,
            "report_mode": True,
            "base_request": base_request,
            "fresh": True,
            "bypass_cache": True,
        }
        if store_mode == "all":
            result["stores"] = scoped_stores
        if period_start and period_end:
            result["data_inicio"] = period_start
            result["data_fim"] = period_end
        return result
    previous_offset = max(0, int(previous.get("offset") or 0))
    provider_offsets = {
        str(key): max(0, int(value))
        for key, value in (previous.get("provider_offsets") or {}).items()
        if str(key or "").strip() and value is not None
    } if isinstance(previous.get("provider_offsets"), dict) else {}
    if previous.get("pagination_complete") is True and not provider_offsets:
        return {
            "mode": "query_only",
            "domains": domains,
            "read_only": True,
            "deny_approval": True,
            "store_required": store_required,
            "store": store,
            "store_mode": store_mode,
            "stores": scoped_stores if store_mode == "all" else [],
            "authorized_stores": authorized_stores,
            "store_matches": [store] if store else [],
            "providers": providers,
            "source_policy": source_policy,
            "pagination": "complete",
            "no_more_results": True,
        }
    next_offset = min(provider_offsets.values()) if provider_offsets else previous_offset + limit
    return {
        "mode": "query_only",
        "domains": domains,
        "read_only": True,
        "deny_approval": True,
        "store_required": store_required,
        "store": store,
        "store_mode": store_mode,
        "stores": scoped_stores if store_mode == "all" else [],
        "authorized_stores": authorized_stores,
        "store_matches": [store] if store else [],
        "providers": providers,
        "source_policy": source_policy,
        "pagination": "next",
        "inherited": True,
        "offset": next_offset,
        "provider_offsets": provider_offsets,
        "limit": limit,
        "report_mode": report_mode,
        "base_request": str(previous.get("base_request") or "")[:2000],
        "fresh": bool(previous.get("fresh")),
        "bypass_cache": bool(previous.get("bypass_cache")),
        "sku": str(previous.get("sku") or "").strip()[:100],
        "item_id": str(previous.get("item_id") or "").strip().upper()[:60],
        "context_request": str(previous.get("base_request") or "").strip()[:2000],
    }


def _whatsapp_remember_query_context(
    state: dict[str, Any],
    conversation_id: str,
    request_text: str,
    policy: dict[str, Any],
) -> None:
    if policy.get("mode") != "query_only":
        return
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    base_request = str(policy.get("base_request") or request_text or "").strip()[:2000]
    extracted_sku, extracted_item_id = _function_manager_extract_identifiers(
        policy.get("context_request") or request_text or base_request
    )
    contexts[conversation_id] = {
        "domains": list(policy.get("domains") or []),
        "store_required": bool(policy.get("store_required")),
        "store": str(policy.get("store") or "").strip(),
        "store_mode": str(policy.get("store_mode") or "single").strip() or "single",
        "stores": [
            str(item or "").strip()
            for item in (policy.get("stores") or [])
            if str(item or "").strip()
        ],
        "providers": list(policy.get("providers") or []),
        "source_policy": dict(policy.get("source_policy") or {}) if isinstance(policy.get("source_policy"), dict) else {},
        "base_request": base_request,
        "sku": str(policy.get("sku") or extracted_sku or "").strip()[:100],
        "item_id": str(policy.get("item_id") or extracted_item_id or "").strip().upper()[:60],
        "offset": max(0, int(policy.get("offset") or 0)),
        "provider_offsets": dict(policy.get("provider_offsets") or {}) if isinstance(policy.get("provider_offsets"), dict) else {},
        "pagination_complete": False,
        "report_mode": bool(policy.get("report_mode")),
        "limit": max(1, min(int(policy.get("limit") or 20), 20000 if policy.get("report_mode") else 100)),
        "fresh": bool(policy.get("fresh")),
        "bypass_cache": bool(policy.get("bypass_cache")),
        "updated_at": time.time(),
    }
    state["query_contexts"] = contexts


def _whatsapp_update_query_context_from_task(
    state: dict[str, Any],
    pending: dict[str, Any],
    task: dict[str, Any],
) -> None:
    conversation_id = str(pending.get("conversation_id") or task.get("conversation_id") or "").strip()
    if not conversation_id:
        return
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    context = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not context:
        return
    provider_offsets: dict[str, int] = {}
    saw_paging = False
    for item in task.get("tool_results_summary") if isinstance(task.get("tool_results_summary"), list) else []:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("tool_id") or "").strip()
        if tool_id not in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_listing"}:
            continue
        paging = item.get("paging") if isinstance(item.get("paging"), dict) else {}
        if not paging:
            continue
        saw_paging = True
        next_offset = paging.get("next_offset")
        if paging.get("has_more") is True and next_offset is not None:
            try:
                provider_offsets[tool_id] = max(0, int(next_offset))
            except (TypeError, ValueError):
                continue
    if not saw_paging:
        return
    context["provider_offsets"] = provider_offsets
    context["pagination_complete"] = not bool(provider_offsets)
    if provider_offsets:
        context["offset"] = min(provider_offsets.values())
    context["updated_at"] = time.time()
    contexts[conversation_id] = context
    state["query_contexts"] = contexts


def _whatsapp_query_only_block_text(domains: list[str]) -> str:
    labels = []
    if "vendas" in domains:
        labels.append("vendas")
    if "anuncios_ml" in domains:
        labels.append("anuncios do Mercado Livre")
    if "estoque" in domains:
        labels.append("estoque")
    if "mercado_full" in domains:
        labels.append("estoque Full do Mercado Livre")
    scope = " e ".join(labels) or "este modulo"
    return (
        f"Pelo WhatsApp, *{scope}* funciona somente para consultas. "
        "Nao posso sincronizar, alterar, pausar, publicar, responder, aprovar ou executar outra acao mutavel nesse dominio. "
        "Nenhum codigo de aprovacao foi criado. Voce pode pedir a leitura dos dados informando a loja."
    )


def _whatsapp_store_required_text(policy: dict[str, Any]) -> str:
    stores = [str(item) for item in (policy.get("authorized_stores") or []) if str(item or "").strip()]
    matches = [str(item) for item in (policy.get("store_matches") or []) if str(item or "").strip()]
    if matches:
        intro = "Encontrei mais de uma loja no pedido. Escolha qual deseja consultar."
    else:
        intro = "Qual loja voce quer consultar?"
    if not stores:
        return (
            "Nao encontrei nenhuma loja disponivel para este usuario. "
            "Confira as permissoes e o cadastro das lojas no JK Sistema."
        )
    options = "\n".join(f"- *{store}*" for store in stores[:20])
    return f"{intro}\n\n*Lojas disponiveis:*\n{options}\n\nVoce tambem pode pedir `todas as lojas` para receber o resultado separado por loja."


def _whatsapp_plain_inline(value: Any) -> str:
    return whatsapp_formatting._whatsapp_plain_inline(value)


def _whatsapp_field(line: Any) -> tuple[str, str]:
    return whatsapp_formatting._whatsapp_field(line)


def _whatsapp_heading_value(line: Any) -> str:
    return whatsapp_formatting._whatsapp_heading_value(line)


def _whatsapp_report_section_kind(heading: Any) -> str:
    return whatsapp_formatting._whatsapp_report_section_kind(heading)


def _whatsapp_report_sections(value: Any) -> dict[str, list[str]]:
    return whatsapp_formatting._whatsapp_report_sections(value)


def _whatsapp_metric_label(value: Any) -> str:
    return whatsapp_formatting._whatsapp_metric_label(value)


def _whatsapp_number(value: Any) -> str:
    return whatsapp_formatting._whatsapp_number(value)


def _whatsapp_money(value: Any) -> str:
    return whatsapp_formatting._whatsapp_money(value)


def _whatsapp_report_date(value: Any) -> str:
    return whatsapp_formatting._whatsapp_report_date(value)


def _whatsapp_daily_ml_sales_report(
    tool_results: list[dict[str, Any]],
    query_policy: dict[str, Any],
    request_text: Any,
) -> str:
    return whatsapp_formatting._whatsapp_daily_ml_sales_report(tool_results, query_policy, request_text)


def _whatsapp_report_summary(lines: list[str]) -> str:
    return whatsapp_formatting._whatsapp_report_summary(lines)


def _whatsapp_ranking_items(lines: list[str]) -> list[dict[str, Any]]:
    return whatsapp_formatting._whatsapp_ranking_items(lines)


def _whatsapp_rank_marker(index: int) -> str:
    return whatsapp_formatting._whatsapp_rank_marker(index)


def _whatsapp_report_ranking_parts(lines: list[str]) -> list[str]:
    return whatsapp_formatting._whatsapp_report_ranking_parts(lines)


def _whatsapp_report_text_section(title: str, lines: list[str]) -> list[str]:
    return whatsapp_formatting._whatsapp_report_text_section(title, lines)


def _whatsapp_report_response_parts(value: Any, title: str) -> list[str]:
    return whatsapp_formatting._whatsapp_report_response_parts(value, title)


def _whatsapp_split_body(
    value: Any,
    limit: int = WHATSAPP_PART_BODY_CHARS,
    max_parts: Optional[int] = WHATSAPP_MAX_PARTS,
) -> list[str]:
    return whatsapp_formatting._whatsapp_split_body(value, limit, max_parts)


def _whatsapp_operational_title(title: Any) -> bool:
    return whatsapp_formatting._whatsapp_operational_title(title)


def _whatsapp_response_parts(value: Any, title: str) -> list[str]:
    return whatsapp_formatting._whatsapp_response_parts(value, title)


def _whatsapp_result_title(prompt: Any, status: str = "completed") -> str:
    return whatsapp_formatting._whatsapp_result_title(prompt, status)


def _approval_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))


def _approval_command(value: Any) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = APPROVAL_COMMAND_RE.match(normalized)
    if not match:
        return "", ""
    verb = match.group(1).upper()
    action = "approve" if verb in {"APROVAR", "CONFIRMAR"} else "reject"
    return action, match.group(2).upper()


def _normalize_worker_url(value: Any) -> str:
    return whatsapp_gateway.normalize_worker_url(value)


def _require_full(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    session = codex_console._codex_require_full_admin(request, authorization)
    try:
        raw = codex_console._codex_payload_sessao(authorization)
    except Exception:
        raw = {}
    session["machine_id"] = str(raw.get("machine_id") or _host_machine_id())
    return session


def _binding_target(session: dict[str, Any], username: Any = None, client_id: Any = None) -> tuple[str, str]:
    session_username = str(session.get("username") or "").strip().lower()
    session_client_id = str(session.get("client_id") or "").strip()
    target_username = str(username or session_username).strip().lower()
    target_client_id = str(client_id or session_client_id).strip()
    if not target_username or not target_client_id:
        raise HTTPException(status_code=400, detail="Selecione um usuario valido para o pareamento.")
    if target_username != session_username or target_client_id != session_client_id:
        try:
            admin_usuarios_common._carregar_permissoes_usuario(target_username, target_client_id)
        except HTTPException as exc:
            raise HTTPException(status_code=404, detail="Usuario selecionado nao foi encontrado.") from exc
    return target_username, target_client_id


def _gateway_headers(config: dict[str, Any]) -> dict[str, str]:
    return whatsapp_gateway.gateway_headers(config)


def _gateway_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    stream: bool = False,
) -> requests.Response:
    return whatsapp_gateway.gateway_request(
        config,
        method,
        path,
        payload=payload,
        timeout=timeout,
        stream=stream,
    )


def _gateway_json(config: dict[str, Any], method: str, path: str, payload: Optional[dict[str, Any]] = None, timeout: int = 15) -> dict[str, Any]:
    return whatsapp_gateway.gateway_json(config, method, path, payload=payload, timeout=timeout)


def _worker_health(config: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    try:
        result = _gateway_json(config, "GET", "/bridge/status", timeout=12)
        result["latency_ms"] = int((time.time() - started) * 1000)
        try:
            actual_protocol = int(result.get("gateway_protocol_version") or 0)
        except (TypeError, ValueError):
            actual_protocol = 0
        result["expected_gateway_protocol_version"] = WHATSAPP_GATEWAY_PROTOCOL_VERSION
        result["protocol_compatible"] = actual_protocol == WHATSAPP_GATEWAY_PROTOCOL_VERSION
        if not result["protocol_compatible"]:
            result.update(
                {
                    "success": False,
                    "worker": False,
                    "error": (
                        "gateway_protocol_incompatible: "
                        f"expected={WHATSAPP_GATEWAY_PROTOCOL_VERSION}, actual={actual_protocol}"
                    ),
                }
            )
            RUNTIME_STATE["last_error"] = str(result["error"])
            return result
        RUNTIME_STATE["last_worker_ok_at"] = _now()
        RUNTIME_STATE["last_error"] = ""
        return result
    except Exception as exc:
        return {"success": False, "worker": False, "error": str(exc)[:800]}


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*") if path.exists() else []:
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _whisper_status() -> dict[str, Any]:
    installed = importlib.util.find_spec("faster_whisper") is not None
    model_dir = _model_dir()
    validation = _validate_model_dir(model_dir)
    total_bytes = int(validation.get("bytes") or _directory_size(model_dir))
    ready = bool(installed and validation.get("valid"))
    model_source = "bundled" if model_dir == _bundled_model_dir() else "downloaded"
    progress = 100 if ready else min(99, int(total_bytes * 100 / WHISPER_MODEL_EXPECTED_BYTES))
    return {
        "dependency_installed": installed,
        "model": "small",
        "model_dir": str(model_dir),
        "model_source": model_source,
        "integrity": "verified_sha256" if validation.get("valid") else "invalid",
        "integrity_error": str(validation.get("error") or ""),
        "ready": ready,
        "download_status": RUNTIME_STATE.get("whisper_download_status") or "idle",
        "download_error": RUNTIME_STATE.get("whisper_download_error") or "",
        "downloaded_bytes": total_bytes,
        "expected_bytes": WHISPER_MODEL_EXPECTED_BYTES,
        "progress_percent": progress,
        "manifest_files": int(validation.get("files") or 0),
        "engine": "faster-whisper==1.2.1",
        "device": "cpu",
        "compute_type": "int8",
        "local_only": True,
    }


def _whisper_runner() -> Path:
    return Path(__file__).with_name("whatsapp_transcribe.py").resolve()


def _download_whisper_worker() -> None:
    RUNTIME_STATE.update(
        {
            "whisper_download_status": "downloading",
            "whisper_download_error": "",
            "whisper_download_started_at": _now(),
        }
    )
    output = _info_dir() / "whatsapp_whisper_download_result.json"
    try:
        process = subprocess.run(
            [
                sys.executable,
                str(_whisper_runner()),
                "--model-dir",
                str(_download_model_dir()),
                "--output",
                str(output),
                "--download-only",
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        result = _json_read(output, {})
        if process.returncode != 0 or not result.get("success"):
            raise RuntimeError(str(result.get("error") or process.stderr or "whisper_download_failed")[:1000])
        RUNTIME_STATE["whisper_download_status"] = "ready"
    except Exception as exc:
        RUNTIME_STATE["whisper_download_status"] = "failed"
        RUNTIME_STATE["whisper_download_error"] = str(exc)[:1000]


def whatsapp_bridge_download_whisper(request: Request, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    global DOWNLOAD_THREAD
    _require_full(request, authorization)
    if importlib.util.find_spec("faster_whisper") is None:
        raise HTTPException(status_code=503, detail="Instale faster-whisper==1.2.1 no Python do JK Sistema antes de baixar o modelo.")
    if DOWNLOAD_THREAD and DOWNLOAD_THREAD.is_alive():
        return {"success": True, "started": False, "whisper": _whisper_status()}
    DOWNLOAD_THREAD = threading.Thread(target=_download_whisper_worker, name="jk-whatsapp-whisper-download", daemon=True)
    DOWNLOAD_THREAD.start()
    return {"success": True, "started": True, "whisper": _whisper_status()}


def _transcribe_audio(audio_path: Path) -> dict[str, Any]:
    if not _whisper_status().get("ready"):
        return {"success": False, "error": "Whisper Small local indisponivel."}
    descriptor, output_name = tempfile.mkstemp(prefix="jk-wa-transcript-", suffix=".json", dir=str(_info_dir()))
    os.close(descriptor)
    output = Path(output_name)
    try:
        process = subprocess.run(
            [
                sys.executable,
                str(_whisper_runner()),
                "--model-dir",
                str(_model_dir()),
                "--audio",
                str(audio_path),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
            check=False,
        )
        result = _json_read(output, {})
        if process.returncode != 0 and not result.get("error"):
            result = {"success": False, "error": (process.stderr or "transcription_failed")[:1000]}
        return result if isinstance(result, dict) else {"success": False, "error": "transcription_invalid_result"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Transcricao excedeu o limite de dez minutos."}
    except Exception as exc:
        return {"success": False, "error": str(exc)[:1000]}
    finally:
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass


def _safe_filename(value: Any, fallback: str) -> str:
    return whatsapp_media.safe_filename(value, fallback)


def _media_extension(mime: str) -> str:
    return whatsapp_media.media_extension(mime)


def _download_media(config: dict[str, Any], message: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    message_id = str(message.get("message_id") or "").strip()
    declared = str(message.get("media_mime") or "").split(";", 1)[0].strip().lower()
    limits = {**SUPPORTED_IMAGE_MIMES, **SUPPORTED_AUDIO_MIMES}
    if declared not in limits:
        raise RuntimeError(f"media_type_not_allowed:{declared or 'unknown'}")
    expected = int(message.get("media_size") or 0)
    if expected and expected > limits[declared]:
        raise RuntimeError("media_size_limit")
    response: Optional[requests.Response] = None
    # Workers KV is eventually consistent across regions. A webhook write can
    # take a few seconds to become visible to the bridge request from Brazil.
    # Retry only the temporary media-not-found response; all other failures
    # remain fail-closed.
    for attempt in range(6):
        try:
            response = _gateway_request(config, "GET", f"/bridge/media/{message_id}", timeout=60, stream=True)
            break
        except RuntimeError as exc:
            if "gateway_http_404" not in str(exc) or attempt >= 5:
                raise
            time.sleep(10)
    if response is None:
        raise RuntimeError("media_download_unavailable")
    actual_mime = str(response.headers.get("content-type") or declared).split(";", 1)[0].strip().lower()
    if actual_mime not in limits:
        raise RuntimeError(f"media_type_not_allowed:{actual_mime or 'unknown'}")
    client_id = str(message.get("client_id") or config.get("client_id") or "default")
    username = str(message.get("username") or config.get("username") or "user")
    target_dir = codex_console._codex_attachment_dir(client_id, username, conversation_id)
    original = _safe_filename(response.headers.get("x-jk-filename"), f"whatsapp{_media_extension(actual_mime)}")
    if not os.path.splitext(original)[1]:
        original += _media_extension(actual_mime)
    target = target_dir / f"{codex_console._codex_safe_id(message_id, 'wa')}_{original}"
    temporary = target.with_suffix(target.suffix + ".part")
    total = 0
    try:
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(128 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limits[actual_mime]:
                    raise RuntimeError("media_size_limit")
                handle.write(chunk)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
        response.close()
    return codex_console._codex_attachment_public_payload(target, original, actual_mime, total)


def _message_phone(config: dict[str, Any], message: dict[str, Any]) -> str:
    phone = codex_console._codex_normalize_phone(
        message.get("wa_id") or message.get("phone") or message.get("phone_number")
    )
    if phone:
        return phone
    subject = str(message.get("subject_id") or "").strip()
    if subject and subject == str(config.get("subject_id") or "").strip():
        return codex_console._codex_normalize_phone(config.get("personal_phone"))
    return ""


def _conversation_id(config: dict[str, Any], message: dict[str, Any]) -> str:
    phone = _message_phone(config, message)
    if not phone:
        raise RuntimeError("whatsapp_phone_identity_missing")
    return codex_console._codex_canonical_conversation_id(
        str(message.get("client_id") or config.get("client_id") or "default"),
        str(message.get("username") or config.get("username") or "user"),
        channel="whatsapp",
        phone=phone,
    )


def _message_prompt(
    message: dict[str, Any],
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    *,
    mobile_full_access: bool = False,
    query_policy: Optional[dict[str, Any]] = None,
    ai_behavior: str = "",
    general_answer: bool = False,
) -> str:
    parts = [
        "[Origem: WhatsApp vinculado ao JK Sistema]",
        f"Mensagem externa: {str(message.get('message_id') or '')}",
        (
            "O remetente esta vinculado a um usuario full. Consultas usam o catalogo completo; "
            "qualquer execucao mutavel so e iniciada depois da confirmacao externa por codigo unico no mesmo numero."
            if mobile_full_access
            else "O remetente nao possui modo movel full; mantenha a tarefa estritamente read-only."
        ),
        (
            "Estilo da resposta no WhatsApp: converse como um colega prestativo, natural e descontraido. "
            "Va direto ao ponto, varie a abertura conforme o contexto e use frases simples. "
            "Nao crie titulo para toda resposta, nao repita o nome Black Jhon e nao assine no final. "
            "Use secoes apenas quando elas realmente ajudarem em relatorios ou respostas longas; nao use emojis."
        ),
    ]
    phone_ai_behavior = _normalize_phone_ai_behavior(ai_behavior)
    if phone_ai_behavior:
        parts.append(
            "Instrucoes administrativas especificas para atender este numero:\n"
            + phone_ai_behavior
            + "\nSiga estas orientacoes de tom, formato e atendimento. Elas nao ampliam permissoes, nao autorizam mutacoes e nao substituem as regras obrigatorias de seguranca, fontes e escopo."
        )
    if general_answer:
        parts.append(
            "Esta e uma conversa geral, sem consulta nem acao no JK Sistema. "
            "Responda diretamente ao usuario na resposta final. Nao envie confirmacao de recebimento, "
            "nao diga que vai fazer depois e nao prometa avisar quando concluir."
        )
    query_policy = query_policy if isinstance(query_policy, dict) else {}
    store_mode = str(query_policy.get("store_mode") or "").strip()
    store = str(query_policy.get("store") or "").strip()
    scoped_stores = [str(item or "").strip() for item in (query_policy.get("stores") or []) if str(item or "").strip()]
    if store_mode == "all" and scoped_stores:
        parts.append(
            "Escopo obrigatorio por loja: consulte cada uma destas lojas separadamente: "
            + ", ".join(scoped_stores)
            + ". Nunca some nem misture os totais. Responda com um bloco identificado para cada loja e informe falhas individualmente."
        )
    elif store:
        parts.append(f"Escopo obrigatorio: considere exclusivamente a loja exata {store}.")
    if query_policy.get("mode") == "query_only":
        domains = ", ".join(str(item) for item in (query_policy.get("domains") or []))
        parts.append(
            "Politica obrigatoria deste pedido: query_only. "
            f"Dominios protegidos: {domains or 'vendas/anuncios_ml'}. "
            "Use somente ferramentas read-only; nao crie proposta, nao solicite aprovacao e nao execute mutacao."
            + (f" Consulte exclusivamente a loja exata: {store}." if store else "")
            + (
                " O usuario pediu dados atualizados diretamente da API: ignore resultado em cache quando a ferramenta oferecer essa opcao."
                if query_policy.get("bypass_cache") is True
                else ""
            )
            + (
                " Continue a consulta anterior preservando seus filtros: "
                f"{str(query_policy.get('base_request') or '')[:2000]}. "
                f"Use offset {int(query_policy.get('offset') or 0)} e limite {int(query_policy.get('limit') or 20)}."
                + (
                    " Offsets exatos retornados por fonte: "
                    + ", ".join(
                        f"{tool_id}={int(next_offset)}"
                        for tool_id, next_offset in (query_policy.get("provider_offsets") or {}).items()
                    )
                    + ". Continue somente as fontes listadas."
                    if isinstance(query_policy.get("provider_offsets"), dict) and query_policy.get("provider_offsets")
                    else ""
                )
                if query_policy.get("inherited") is True
                else ""
            )
        )
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    if source_policy:
        parts.append(
            "Politica obrigatoria de fontes deste pedido:\n"
            "- Estoque atual de loja: consultar primeiro o saldo atual diretamente na API da Bling, excluindo qualquer deposito Full.\n"
            "- Descricao de anuncios, pedidos e vendas: consultar primeiro a API do Mercado Livre.\n"
            "- Estoque Full: usar exclusivamente inventories/{inventory_id}/stock/fulfillment da API do Mercado Livre.\n"
            "- Nunca consultar, inferir ou somar estoque Full vindo da Bling, de cadastro local ou de cache local.\n"
            "- Quando a soma combinar loja e Full, somar somente o saldo de loja confirmado pela Bling com o Full confirmado pelo Mercado Livre; "
            "se uma das APIs falhar, nao completar o valor por suposicao.\n"
            f"Roteamento calculado pelo servidor: {json.dumps(source_policy, ensure_ascii=False, default=str)[:3000]}"
        )
    body = str(message.get("text_body") or "").strip()
    if body:
        parts.append("Texto recebido:\n" + body[:12000])
        if _whatsapp_image_requested(body):
            parts.append(
                "O usuario pediu explicitamente uma foto de produto. Consulte somente a foto do cadastro do cliente vinculado, "
                "identifique o SKU correto e, se a fonte retornar uma referencia interna /api/cadastro/foto-arquivo/, "
                "preserve essa referencia na resposta para a ponte anexar o arquivo real. Nao invente URL nem caminho."
            )
    if media:
        parts.append(
            "Anexo local recebido pelo WhatsApp:\n"
            f"- caminho: {media.get('path')}\n"
            f"- MIME: {media.get('mime_type')}\n"
            f"- tamanho: {media.get('size')} bytes\n"
            f"- wamid: {message.get('message_id')}"
        )
    if transcription:
        if transcription.get("success"):
            text = str(transcription.get("text") or "")
            suffix = "\n[transcricao limitada a 12000 caracteres]" if len(text) > 12000 else ""
            parts.append(
                "Conteudo originado de audio e transcrito localmente (nenhuma API externa):\n"
                f"{text[:12000]}{suffix}\n"
                f"Duracao: {transcription.get('duration_seconds')} s | confianca: {transcription.get('confidence')} | idioma: {transcription.get('language')}"
            )
        else:
            parts.append(
                "Nao foi possivel transcrever o audio localmente. Preserve o anexo na auditoria e informe isso claramente na resposta. "
                f"Motivo: {str(transcription.get('error') or 'falha desconhecida')[:500]}"
            )
    if not body and not media:
        parts.append("A mensagem nao continha texto ou midia suportada.")
    return "\n\n".join(parts)


def _post_typing_indicator(config: dict[str, Any], message_id: str) -> dict[str, Any]:
    response = _gateway_request(
        config,
        "POST",
        f"/bridge/messages/{message_id}/typing",
        payload={"machine_id": config.get("machine_id")},
        timeout=15,
    )
    try:
        try:
            payload = response.json()
        except Exception:
            payload = {"error": response.text[:500]}
        if response.status_code in {403, 404, 409, 429} and isinstance(payload, dict):
            return payload
        if response.status_code >= 400:
            raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
        return payload if isinstance(payload, dict) else {"success": False, "status": "invalid_gateway_json"}
    finally:
        response.close()


def _post_message_progress(
    config: dict[str, Any],
    message_id: str,
    *,
    task_id: str,
    sequence: int,
    stage: str,
    text: str,
    fingerprint: str,
) -> dict[str, Any]:
    return _gateway_json(
        config,
        "POST",
        f"/bridge/messages/{message_id}/progress",
        {
            "machine_id": config.get("machine_id"),
            "task_id": task_id,
            "sequence": int(sequence),
            "stage": str(stage or "processando")[:80],
            "text": str(text or "")[:1200],
            "fingerprint": str(fingerprint or "")[:160],
        },
        timeout=20,
    )


def _progress_stage(task: dict[str, Any]) -> tuple[str, str]:
    status = str(task.get("status") or "").strip().lower()
    reason = str(task.get("wait_reason") or "").strip().lower()
    agent_state = str(task.get("agent_state") or "").strip().lower()
    live = unicodedata.normalize("NFKD", str(task.get("live_status") or "")).encode("ascii", "ignore").decode("ascii").lower()
    if reason == "retry_backoff":
        return "retentativa", "O Codex ficou temporariamente indisponivel e a tarefa foi preservada para uma nova tentativa automatica."
    if status == "queued":
        return "fila", "A solicitação continua na fila da conversa para não misturar respostas."
    if reason == "restart_recovery":
        return "retomando", "Estou reconstruindo a consulta após a reinicialização, sem reaproveitar uma resposta incompleta."
    if reason == "api_query" or any(word in live for word in ("ferramenta", "api", "mercado livre", "bling", "consult")):
        return "consultando", "Ainda estou aguardando ou percorrendo os dados das APIs necessárias."
    if reason in {"data_validation", "data_analysis"} or any(word in live for word in ("validando", "fallback", "insuficiente", "raciocin")):
        return "validando", "Estou comparando os resultados porque ainda preciso confirmar a suficiência e a consistência dos dados."
    if "gerando" in live or agent_state in {"validando", "verificando"}:
        return "preparando_resposta", "As consultas terminaram e estou organizando a resposta final sem repetir ou completar dados por suposição."
    return "analisando", "A análise ainda está em andamento e o Codex não concluiu uma resposta segura."


def _progress_message(task: dict[str, Any], sequence: int, elapsed_seconds: int) -> tuple[str, str]:
    stage, explanation = _progress_stage(task)
    previous = [item for item in (task.get("progress_events") or []) if isinstance(item, dict)]
    previous_stage = str(previous[-1].get("stage") or "") if previous else ""
    prefix = "Avancei para a próxima etapa." if previous_stage and previous_stage != stage else "Ainda estou trabalhando nesta solicitação."
    queue_position = int(task.get("queue_position") or codex_console._codex_task_queue_position(task) or 0)
    queue_note = f" Posição atual na fila: {queue_position}." if stage == "fila" and queue_position else ""
    text = f"*Andamento ({elapsed_seconds}s):* {prefix} {explanation}{queue_note}"
    return stage, text[:1200]


def _record_progress_event(task_id: str, event: dict[str, Any]) -> None:
    task = codex_console._codex_load_task(task_id)
    if not isinstance(task, dict):
        return
    events = [item for item in (task.get("progress_events") or []) if isinstance(item, dict)]
    codex_console._codex_update_task(
        task_id,
        progress_events=(events + [event])[-120:],
        last_progress_at=str(event.get("created_at") or _now()),
    )


def _progress_pulse_worker(
    config: dict[str, Any],
    message_id: str,
    task_id: str,
    stop_event: threading.Event,
) -> None:
    interval = _normalize_progress_interval(config.get("progress_interval_seconds"))
    started_at = time.monotonic()
    try:
        while not stop_event.wait(interval) and not BRIDGE_STOP_EVENT.is_set():
            task = codex_console._codex_load_task(task_id)
            if not isinstance(task, dict):
                break
            status = str(task.get("status") or "")
            if status in {"completed", "partial", "failed", "canceled", "awaiting_approval", "awaiting_input"}:
                break
            sequence = len([item for item in (task.get("progress_events") or []) if isinstance(item, dict)]) + 1
            elapsed = max(interval, int(time.monotonic() - started_at))
            stage, progress_text = _progress_message(task, sequence, elapsed)
            fingerprint = hashlib.sha256(f"{message_id}|{task_id}|{sequence}".encode("utf-8")).hexdigest()
            event = {
                "sequence": sequence,
                "stage": stage,
                "text": progress_text,
                "fingerprint": fingerprint,
                "created_at": _now(),
                "delivery": "pending",
            }
            try:
                result = _post_message_progress(
                    config,
                    message_id,
                    task_id=task_id,
                    sequence=sequence,
                    stage=stage,
                    text=progress_text,
                    fingerprint=fingerprint,
                )
                event["delivery"] = str(result.get("status") or ("sent" if result.get("success") else "failed"))
                RUNTIME_STATE["progress_last_error"] = ""
                RUNTIME_STATE["progress_last_sent_at"] = _now()
            except Exception as exc:
                event["delivery"] = "failed"
                event["error"] = str(exc)[:500]
                RUNTIME_STATE["progress_last_error"] = str(exc)[:800]
            _record_progress_event(task_id, event)
            if elapsed >= TYPING_MAX_SECONDS:
                break
    finally:
        with PROGRESS_PULSES_LOCK:
            if PROGRESS_PULSES.get(message_id) is stop_event:
                PROGRESS_PULSES.pop(message_id, None)


def _start_progress_pulse(config: dict[str, Any], message_id: str, task_id: str) -> bool:
    if (
        config.get("progress_messages_enabled") is False
        or config.get("progress_explain_wait") is False
        or not message_id
        or not task_id
        or not config.get("worker_url")
        or not config.get("bridge_token")
        or not config.get("machine_id")
        or _whatsapp_ai_settings(config).get("provider") != "codex"
    ):
        return False
    with PROGRESS_PULSES_LOCK:
        existing = PROGRESS_PULSES.get(message_id)
        if existing is not None and not existing.is_set():
            return False
        stop_event = threading.Event()
        PROGRESS_PULSES[message_id] = stop_event
    threading.Thread(
        target=_progress_pulse_worker,
        args=(dict(config), message_id, task_id, stop_event),
        name=f"jk-whatsapp-progress-{hashlib.sha256(message_id.encode('utf-8')).hexdigest()[:8]}",
        daemon=True,
    ).start()
    return True


def _stop_progress_pulse(message_id: str) -> None:
    with PROGRESS_PULSES_LOCK:
        stop_event = PROGRESS_PULSES.get(str(message_id or "").strip())
    if stop_event is not None:
        stop_event.set()


def _typing_pulse_worker(config: dict[str, Any], message_id: str, stop_event: threading.Event) -> None:
    started_at = time.monotonic()
    consecutive_errors = 0
    terminal_statuses = {
        "not_active",
        "daily_limit",
        "policy_recheck_required",
        "waiting_free_window",
        "message_not_owned",
        "binding_machine_mismatch",
    }
    try:
        while time.monotonic() - started_at < TYPING_MAX_SECONDS and not stop_event.is_set():
            try:
                result = _post_typing_indicator(config, message_id)
                status = str(result.get("status") or result.get("error") or "").strip().lower()
                if status in terminal_statuses:
                    break
                if status in {"sent", "too_soon"}:
                    consecutive_errors = 0
                    RUNTIME_STATE["typing_last_error"] = ""
                    if status == "sent":
                        RUNTIME_STATE["typing_last_sent_at"] = _now()
                else:
                    consecutive_errors += 1
            except Exception as exc:
                consecutive_errors += 1
                RUNTIME_STATE["typing_last_error"] = str(exc)[:800]
            if consecutive_errors >= TYPING_MAX_CONSECUTIVE_ERRORS:
                break
            if stop_event.wait(TYPING_REFRESH_SECONDS) or BRIDGE_STOP_EVENT.is_set():
                break
    finally:
        with TYPING_PULSES_LOCK:
            if TYPING_PULSES.get(message_id) is stop_event:
                TYPING_PULSES.pop(message_id, None)


def _start_typing_pulse(config: dict[str, Any], message_id: str) -> bool:
    message_key = str(message_id or "").strip()
    if not message_key or not config.get("worker_url") or not config.get("bridge_token") or not config.get("machine_id"):
        return False
    with TYPING_PULSES_LOCK:
        existing = TYPING_PULSES.get(message_key)
        if existing is not None and not existing.is_set():
            return False
        stop_event = threading.Event()
        TYPING_PULSES[message_key] = stop_event
    threading.Thread(
        target=_typing_pulse_worker,
        args=(dict(config), message_key, stop_event),
        name=f"jk-whatsapp-typing-{hashlib.sha256(message_key.encode('utf-8')).hexdigest()[:8]}",
        daemon=True,
    ).start()
    return True


def _stop_typing_pulse(message_id: str) -> None:
    with TYPING_PULSES_LOCK:
        stop_event = TYPING_PULSES.get(str(message_id or "").strip())
    if stop_event is not None:
        stop_event.set()


def _post_message_result(config: dict[str, Any], message_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _stop_progress_pulse(message_id)
    body = {"machine_id": config.get("machine_id"), **payload}
    result = _gateway_json(config, "POST", f"/bridge/messages/{message_id}/result", body, timeout=20)
    _stop_typing_pulse(message_id)
    _record_message_timing(message_id, sent_at=_now())
    return result


def _post_outbound_image(
    config: dict[str, Any],
    message_id: str,
    path: Path,
    mime_type: str,
    caption: str,
    filename: str,
    artifact_type: str = "product_photo",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not token or not machine_id:
        raise RuntimeError("worker_url_bridge_token_or_machine_missing")
    if not path.is_file():
        raise RuntimeError("outbound_image_missing")
    artifact_type = str(artifact_type or "").strip().lower()
    if artifact_type not in {"product_photo", "report_chart"}:
        raise RuntimeError("outbound_image_artifact_not_allowed")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    with path.open("rb") as source:
        response = requests.post(
            worker_url + f"/bridge/messages/{quote(str(message_id), safe='')}/image",
            headers=_gateway_headers(config),
            data={
                "machine_id": machine_id,
                "caption": str(caption or "")[:1024],
                "artifact_type": artifact_type,
                "sha256": digest.hexdigest(),
            },
            files={"file": (_safe_filename(filename, "black-john-image.jpg"), source, mime_type)},
            timeout=90,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value


def _post_outbound_document(
    config: dict[str, Any],
    message_id: str,
    path: Path,
    mime_type: str,
    caption: str,
    filename: str,
    artifact_type: str,
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not token or not machine_id:
        raise RuntimeError("worker_url_bridge_token_or_machine_missing")
    if not path.is_file() or path.stat().st_size > WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES:
        raise RuntimeError("outbound_document_missing_or_too_large")
    expected = {
        "report_pdf": "application/pdf",
        "report_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if expected.get(artifact_type) != str(mime_type or "").strip().lower():
        raise RuntimeError("outbound_document_artifact_or_mime_not_allowed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("rb") as source:
        response = requests.post(
            worker_url + f"/bridge/messages/{quote(str(message_id), safe='')}/document",
            headers=_gateway_headers(config),
            data={
                "machine_id": machine_id,
                "caption": str(caption or "")[:1024],
                "artifact_type": artifact_type,
                "sha256": digest,
            },
            files={"file": (_safe_filename(filename, path.name), source, mime_type)},
            timeout=120,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value


def _post_proactive_image(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    path: Path,
    caption: str,
    filename: str,
    event_type: str = "weekly_report",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    subject_id = str(subject_id or config.get("subject_id") or "").strip()
    fingerprint = str(fingerprint or "").strip()[:160]
    if not worker_url or not token or not machine_id or not subject_id:
        raise RuntimeError("worker_url_bridge_token_machine_or_subject_missing")
    if not re.fullmatch(r"[A-Za-z0-9:_-]{16,160}", fingerprint):
        raise RuntimeError("invalid_proactive_image_fingerprint")
    if not path.is_file():
        raise RuntimeError("outbound_image_missing")
    safe_event_type = str(event_type or "weekly_report").strip().lower()
    if safe_event_type not in {"weekly_report", "monthly_report"}:
        raise RuntimeError("invalid_proactive_image_event_type")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    with path.open("rb") as source:
        response = requests.post(
            worker_url + "/bridge/proactive/image",
            headers=_gateway_headers(config),
            data={
                "subject_id": subject_id,
                "machine_id": machine_id,
                "fingerprint": fingerprint,
                "event_type": safe_event_type,
                "artifact_type": "report_chart",
                "caption": str(caption or "")[:1024],
                "sha256": digest.hexdigest(),
            },
            files={"file": (_safe_filename(filename, "black-jhon-weekly-report.png"), source, "image/png")},
            timeout=90,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code == 409 and isinstance(value, dict):
        return {"success": False, **value}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value


def _post_proactive_document(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    path: Path,
    caption: str,
    filename: str,
    artifact_type: str,
    mime_type: str,
    event_type: str = "weekly_report",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    machine_id = str(config.get("machine_id") or "").strip()
    subject_id = str(subject_id or config.get("subject_id") or "").strip()
    if not worker_url or not config.get("bridge_token") or not machine_id or not subject_id:
        raise RuntimeError("worker_url_bridge_token_machine_or_subject_missing")
    if not path.is_file() or path.stat().st_size > WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES:
        raise RuntimeError("outbound_document_missing_or_too_large")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("rb") as source:
        response = requests.post(
            worker_url + "/bridge/proactive/document",
            headers=_gateway_headers(config),
            data={
                "subject_id": subject_id,
                "machine_id": machine_id,
                "fingerprint": str(fingerprint or "")[:160],
                "event_type": str(event_type or "weekly_report")[:80],
                "artifact_type": artifact_type,
                "caption": str(caption or "")[:1024],
                "sha256": digest,
            },
            files={"file": (_safe_filename(filename, path.name), source, mime_type)},
            timeout=120,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code == 409 and isinstance(value, dict):
        return {"success": False, **value}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    return value if isinstance(value, dict) else {"success": False, "status": "invalid_gateway_json"}


def _post_proactive(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload or {})
    subject = str(body.pop("subject_id", "") or config.get("subject_id") or "").strip()
    if not subject:
        return {"success": False, "status": "no_paired_subject"}
    return _gateway_json(config, "POST", "/bridge/proactive", {"subject_id": subject, **body}, timeout=20)


def _post_interactive_approval(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    token: str,
    body: str,
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    bridge_token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not bridge_token or not machine_id:
        return {"success": False, "status": "bridge_not_configured"}
    response = requests.post(
        worker_url + "/bridge/interactive",
        headers={**_gateway_headers(config), "content-type": "application/json"},
        json={
            "subject_id": str(subject_id or "").strip(),
            "machine_id": machine_id,
            "fingerprint": str(fingerprint or "")[:160],
            "event_type": "question_approval",
            "header": "Black Jhon - resposta sugerida",
            "body": str(body or "")[:1024],
            "footer": "Somente o numero vinculado pode decidir",
            "button_label": "Revisar resposta",
            "options": [
                {"id": f"ppv_approve:{token}", "title": "Aprovar e enviar", "description": "Enviar exatamente este rascunho"},
                {"id": f"ppv_correct:{token}", "title": "Corrigir", "description": "Orientar uma correcao no mesmo rascunho"},
                {"id": f"ppv_regenerate:{token}", "title": "Gerar outra resposta", "description": "Criar uma nova versao para revisar"},
                {"id": f"ppv_reject:{token}", "title": "Negar", "description": "Nao enviar e manter a pergunta pendente"},
            ],
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text[:500]}
    if response.status_code == 409 and isinstance(payload, dict):
        return {"success": False, "status": str(payload.get("status") or payload.get("error") or "blocked")}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
    return payload if isinstance(payload, dict) else {"success": False, "status": "invalid_gateway_json"}


def _post_interactive_store_selection(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    options: list[dict[str, str]],
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    bridge_token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not bridge_token or not machine_id:
        return {"success": False, "status": "bridge_not_configured"}
    response = requests.post(
        worker_url + "/bridge/interactive",
        headers={**_gateway_headers(config), "content-type": "application/json"},
        json={
            "subject_id": str(subject_id or "").strip(),
            "machine_id": machine_id,
            "fingerprint": str(fingerprint or "")[:160],
            "event_type": "store_selection",
            "header": "Escolha a loja",
            "body": "Selecione uma loja ou consulte todas separadamente.",
            "footer": "Os totais de lojas diferentes nunca serao misturados.",
            "button_label": "Ver lojas",
            "options": list(options or [])[:10],
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text[:500]}
    if response.status_code == 409 and isinstance(payload, dict):
        return {"success": False, "status": str(payload.get("status") or payload.get("error") or "blocked")}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
    return payload if isinstance(payload, dict) else {"success": False, "status": "invalid_gateway_json"}


def _whatsapp_store_selection_token(value: Any) -> str:
    match = STORE_SELECTION_COMMAND_RE.fullmatch(str(value or "").strip())
    return str(match.group(1) or "").upper() if match else ""


def _whatsapp_consume_store_selection(
    state: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    session: dict[str, Any],
    conversation_id: str,
) -> Optional[dict[str, Any]]:
    selections = state.get("store_selection_tokens") if isinstance(state.get("store_selection_tokens"), dict) else {}
    item = selections.get(str(token or "").upper())
    if not isinstance(item, dict) or item.get("used") is True:
        return None
    if time.time() - float(item.get("created_at") or 0) > STORE_SELECTION_TOKEN_TTL_SECONDS:
        return None
    if str(item.get("subject_id") or "") != str(subject_id or ""):
        return None
    if str(item.get("client_id") or "") != str(session.get("client_id") or ""):
        return None
    if str(item.get("username") or "").strip().lower() != str(session.get("username") or "").strip().lower():
        return None
    if str(item.get("conversation_id") or "") != str(conversation_id or ""):
        return None
    item["used"] = True
    item["used_at"] = time.time()
    selections[str(token or "").upper()] = item
    state["store_selection_tokens"] = selections
    _save_state(state)
    return dict(item)


def _whatsapp_send_store_selection(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    stores: list[str],
    *,
    allow_all: bool = True,
) -> bool:
    message_id = str(message.get("message_id") or "").strip()
    subject_id = str(message.get("subject_id") or "").strip()
    all_available = list(dict.fromkeys(str(store or "").strip() for store in stores if str(store or "").strip()))
    available = all_available[:9 if allow_all else 10]
    if not message_id or not subject_id or not available:
        return False
    now = time.time()
    selections = state.get("store_selection_tokens") if isinstance(state.get("store_selection_tokens"), dict) else {}
    selections = {
        str(key): value
        for key, value in selections.items()
        if isinstance(value, dict)
        and value.get("used") is not True
        and now - float(value.get("created_at") or 0) <= STORE_SELECTION_TOKEN_TTL_SECONDS
    }
    options: list[dict[str, str]] = []
    if allow_all and len(all_available) > 1:
        token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        while token in selections:
            token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        selections[token] = {
            "subject_id": subject_id,
            "client_id": str(session.get("client_id") or ""),
            "username": str(session.get("username") or "").strip().lower(),
            "conversation_id": conversation_id,
            "request_text": str(request_text or "")[:12000],
            "store": "",
            "stores": all_available[:20],
            "store_mode": "all",
            "created_at": now,
            "used": False,
        }
        options.append({
            "id": f"store_select:{token}",
            "title": "Todas as lojas",
            "description": "Consultar cada loja separadamente",
        })
    for store in available:
        token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        while token in selections:
            token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        selections[token] = {
            "subject_id": subject_id,
            "client_id": str(session.get("client_id") or ""),
            "username": str(session.get("username") or "").strip().lower(),
            "conversation_id": conversation_id,
            "request_text": str(request_text or "")[:12000],
            "store": store,
            "store_mode": "single",
            "created_at": now,
            "used": False,
        }
        options.append({"id": f"store_select:{token}", "title": store, "description": "Consultar esta loja"})
    state["store_selection_tokens"] = selections
    _save_state(state)
    try:
        result = _post_interactive_store_selection(
            config,
            subject_id=subject_id,
            fingerprint="store-selection:" + hashlib.sha256(
                f"{subject_id}|{message_id}".encode("utf-8")
            ).hexdigest(),
            options=options,
        )
        if result.get("success") is True or str(result.get("status") or "") in {"sent", "duplicate"}:
            _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
            return True
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"store_selection: {str(exc)[:800]}"
    return False


def _question_approval_allowed(permissions: dict[str, Any]) -> bool:
    return bool(
        isinstance(permissions, dict)
        and (permissions.get("full") is True or permissions.get("perguntas_pos_venda") is True)
    )


def _question_approval_product_identity(approval: dict[str, Any]) -> tuple[str, str]:
    approval = approval if isinstance(approval, dict) else {}
    conversation = approval.get("conversa") if isinstance(approval.get("conversa"), dict) else {}
    items = approval.get("items") if isinstance(approval.get("items"), list) else conversation.get("items")
    items = [item for item in (items or []) if isinstance(item, dict)]
    first_item = items[0] if items else {}

    sku = str(
        approval.get("sku")
        or approval.get("item_sku")
        or first_item.get("sku")
        or first_item.get("seller_sku")
        or "nao informado"
    ).strip()[:80]
    link_candidates = (
        approval.get("permalink"),
        approval.get("item_permalink"),
        approval.get("product_link"),
        approval.get("link"),
        approval.get("url"),
        first_item.get("permalink"),
        first_item.get("item_permalink"),
        first_item.get("link"),
        first_item.get("url"),
    )
    link = ""
    for candidate in link_candidates:
        value = str(candidate or "").strip()
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            link = value[:300]
            break
    item_id = str(approval.get("item_id") or first_item.get("id") or first_item.get("item_id") or "").strip()
    normalized_item_id = re.sub(r"[^A-Za-z0-9]", "", item_id).upper()
    if not link and re.fullmatch(r"MLB\d{6,}", normalized_item_id):
        link = f"https://produto.mercadolivre.com.br/{normalized_item_id}"
    return sku or "nao informado", link or "indisponivel"


def _question_approval_body(approval: dict[str, Any], response_override: str = "") -> str:
    store = str(approval.get("loja") or "Loja nao informada").strip()
    product = str(approval.get("titulo") or approval.get("sku") or approval.get("item_id") or "Produto nao informado").strip()
    question = str(approval.get("pergunta") or "Pergunta nao informada").strip()
    suggested = str(response_override or approval.get("resposta_sugerida") or "").strip()
    sku, product_link = _question_approval_product_identity(approval)
    prefix = f"Loja: {store[:80]}\nProduto: {product[:100]}\n\nPergunta:\n{question[:240]}\n\nSugestao do Black Jhon:\n"
    footer = f"\n\nSKU: {sku}\nLink do produto:\n{product_link}"
    available = max(0, 1024 - len(prefix) - len(footer))
    return (prefix + suggested[:available].rstrip() + footer).strip()


def _question_approval_token(
    state: dict[str, Any],
    *,
    approval: dict[str, Any],
    subject_id: str,
    client_id: str,
    username: str,
    force_new: bool = False,
    user_guidance: str = "",
) -> tuple[str, dict[str, Any]]:
    approval_id = str(approval.get("id") or "").strip()
    suggested_response = str(approval.get("resposta_sugerida") or "").strip()[:1200]
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    now = time.time()
    for token, item in list(tokens.items()):
        if not isinstance(item, dict) or now - float(item.get("created_at") or 0) > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
            tokens.pop(token, None)
            continue
        if (
            not force_new
            and item.get("used") is not True
            and str(item.get("approval_id") or "") == approval_id
            and str(item.get("subject_id") or "") == subject_id
        ):
            if not str(item.get("suggested_response") or "").strip() and suggested_response:
                item["suggested_response"] = suggested_response
                item["draft_hash"] = hashlib.sha256(suggested_response.encode("utf-8")).hexdigest()
                item["draft_created_at"] = _now()
            state["question_approval_tokens"] = tokens
            return str(token), item
    token = _approval_code()
    while token in tokens:
        token = _approval_code()
    item = {
        "approval_id": approval_id,
        "subject_id": subject_id,
        "client_id": client_id,
        "username": username.strip().lower(),
        "created_at": now,
        "used": False,
        "suggested_response": suggested_response,
        "draft_hash": hashlib.sha256(suggested_response.encode("utf-8")).hexdigest() if suggested_response else "",
        "draft_created_at": _now(),
    }
    guidance = str(user_guidance or "").strip()[:1200]
    if guidance:
        item["user_guidance"] = guidance
    tokens[token] = item
    state["question_approval_tokens"] = tokens
    return token, item


def _question_thread_key(client_id: Any, subject_id: Any, username: Any) -> str:
    identity = "|".join(
        (
            str(client_id or "").strip(),
            str(subject_id or "").strip(),
            str(username or "").strip().lower(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _question_set_active_thread(
    state: dict[str, Any],
    *,
    approval_id: str,
    token: str,
    subject_id: str,
    client_id: str,
    username: str,
) -> None:
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    key = _question_thread_key(client_id, subject_id, username)
    threads[key] = {
        "approval_id": str(approval_id or "").strip(),
        "token": str(token or "").strip().upper(),
        "subject_id": str(subject_id or "").strip(),
        "client_id": str(client_id or "").strip(),
        "username": str(username or "").strip().lower(),
        "activated_at": time.time(),
    }
    state["question_active_threads"] = threads


def _question_clear_active_thread(
    state: dict[str, Any],
    *,
    subject_id: str,
    client_id: str,
    username: str,
) -> None:
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    threads.pop(_question_thread_key(client_id, subject_id, username), None)
    state["question_active_threads"] = threads


def _question_active_approval(
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    *,
    subject_id: str,
    client_id: str,
    username: str,
) -> tuple[Optional[dict[str, Any]], str, Optional[dict[str, Any]]]:
    by_id = {
        str(item.get("id") or "").strip(): item
        for item in approvals
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    thread = threads.get(_question_thread_key(client_id, subject_id, username))
    if isinstance(thread, dict):
        approval = by_id.get(str(thread.get("approval_id") or "").strip())
        if approval is not None:
            active_token = str(thread.get("token") or "").strip().upper()
            tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
            token_item = tokens.get(active_token)
            return approval, active_token, token_item if isinstance(token_item, dict) else thread

    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    candidates = []
    for token, item in tokens.items():
        if not isinstance(item, dict) or item.get("used") is True:
            continue
        if str(item.get("subject_id") or "") != str(subject_id or ""):
            continue
        if str(item.get("client_id") or "") != str(client_id or ""):
            continue
        if str(item.get("username") or "").strip().lower() != str(username or "").strip().lower():
            continue
        approval = by_id.get(str(item.get("approval_id") or "").strip())
        if approval is None or str(approval.get("status") or "pending") != "pending":
            continue
        candidates.append((float(item.get("created_at") or 0), str(token).upper(), item, approval))
    if not candidates:
        return None, "", None
    _created_at, token, token_item, approval = max(candidates, key=lambda value: value[0])
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
    )
    return approval, token, token_item


def _deliver_completed_question_research(
    config: dict[str, Any],
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    username: str,
) -> bool:
    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    delivery_state = str(approval.get("research_delivery_state") or "")
    if not delivery_state and approval.get("data_sufficient") is False and job_id:
        approval.update(
            {
                "research_job_id": job_id,
                "research_delivery_state": "waiting_evidence",
                "research_status": "legacy_incomplete",
            }
        )
        delivery_state = "waiting_evidence"
    if not job_id or delivery_state not in {
        "waiting_evidence",
        "ready",
    }:
        return False
    if str(approval.get("research_delivered_job_id") or "") == job_id:
        return False
    try:
        from backend.services import perguntas_pos_venda_codex as ppv_codex
        from backend.services import perguntas_pos_venda_state as ppv_state

        job = ppv_codex.get_job(client_id, job_id)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_research_status: {str(exc)[:800]}"
        return False
    if not isinstance(job, dict):
        return False
    job_status = str(job.get("status") or "").strip().lower()
    if job_status == "completed" and job.get("data_sufficient") is False:
        try:
            job = ppv_codex.resume_incomplete_job(
                client_id,
                job_id,
                reason="retomada_de_resposta_ativa_sem_evidencia_suficiente",
            ) or job
            job_status = str(job.get("status") or "").strip().lower()
            approval["research_delivery_state"] = "waiting_evidence"
            approval["research_resumed_at"] = _now()
            ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        except Exception as exc:
            RUNTIME_STATE["last_error"] = f"question_research_resume: {str(exc)[:800]}"
            return False
    approval["research_status"] = job_status
    approval["research_retry_count"] = max(0, int(job.get("retry_count") or 0))
    approval["research_next_retry_at_epoch"] = float(job.get("next_retry_at_epoch") or 0.0)
    if job_status != "completed" or job.get("data_sufficient") is not True:
        if job_status == "cancelled":
            approval["research_delivery_state"] = "cancelled"
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    response = str(result.get("resposta") or "").strip()[:1200]
    if not response:
        return False

    approval.update(
        {
            "resposta_sugerida": response,
            "regenerated_at": _now(),
            "regenerated_via": "whatsapp_research_loop",
            "research_status": "completed",
            "research_delivery_state": "ready",
            "data_sufficient": True,
            "proposal_id": job_id,
            "codex_job_id": job_id,
            "proposal_version": int(result.get("proposal_version") or job.get("proposal_version") or 1),
            "proposal_hash": str(result.get("proposal_hash") or job.get("proposal_hash") or ""),
            "warnings": list(result.get("warnings") or job.get("warnings") or [])[:8],
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)

    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    approval_id = str(approval.get("id") or "").strip()
    for item in tokens.values():
        if isinstance(item, dict) and str(item.get("approval_id") or "") == approval_id:
            item.update({"used": True, "decision": "superseded_by_verified_research"})
    token, _token_item = _question_approval_token(
        state,
        approval=approval,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        force_new=True,
        user_guidance=str(approval.get("whatsapp_user_guidance") or ""),
    )
    delivery = _post_interactive_approval(
        config,
        subject_id=subject_id,
        fingerprint=f"ppv-research:{client_id}:{subject_id}:{job_id}:{result.get('proposal_hash') or ''}",
        token=token,
        body=_question_approval_body(approval, response),
    )
    if str(delivery.get("status") or "") not in {"sent", "duplicate"}:
        return False
    approval.update(
        {
            "research_delivery_state": "delivered",
            "research_delivered_job_id": job_id,
            "research_delivered_at": _now(),
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    _question_set_active_thread(
        state,
        approval_id=approval_id,
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
    )
    _save_state(state)
    return True


def _forward_question_approvals(config: dict[str, Any], state: dict[str, Any]) -> None:
    try:
        from backend.services import perguntas_pos_venda_state as ppv_state

        worker = _worker_health(config)
        bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
        eligible_bindings = []
        for binding in bindings:
            if not isinstance(binding, dict) or str(binding.get("machine_id") or "") != str(config.get("machine_id") or ""):
                continue
            client_id = str(binding.get("client_id") or "").strip()
            username = str(binding.get("username") or "").strip().lower()
            subject_id = str(binding.get("subject_id") or "").strip()
            if not client_id or not username or not subject_id:
                continue
            permissions = admin_usuarios_common._carregar_permissoes_usuario(username, client_id)
            phone_settings = _phone_notification_settings(
                config,
                subject_id,
                client_id=client_id,
                username=username,
            )
            if _question_approval_allowed(permissions) and phone_settings["send_ml_question_suggestions"]:
                eligible_bindings.append((binding, client_id, username, subject_id))
        if not eligible_bindings:
            return
        notifications = state.get("question_approval_notifications") if isinstance(state.get("question_approval_notifications"), dict) else {}
        for _binding, client_id, username, subject_id in eligible_bindings:
            configs = ppv_state._perguntas_loja_configs_carregar(client_id)
            approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
            active_approval, active_token, _active_item = _question_active_approval(
                state,
                approvals,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
            )
            if active_approval is not None and str(active_approval.get("status") or "pending") == "pending":
                _deliver_completed_question_research(
                    config,
                    state,
                    approvals,
                    active_approval,
                    client_id=client_id,
                    subject_id=subject_id,
                    username=username,
                )
                # Uma pergunta ja esta nas maos do operador. Nao envie outra
                # enquanto ela continuar pendente.
                continue
            if active_approval is not None or active_token:
                _question_clear_active_thread(
                    state,
                    subject_id=subject_id,
                    client_id=client_id,
                    username=username,
                )

            approval = next(
                (
                    item
                    for item in approvals
                    if isinstance(item, dict)
                    and str(item.get("status") or "pending") == "pending"
                    and str(item.get("id") or "").strip()
                    and str(item.get("resposta_sugerida") or "").strip()
                    and ppv_state._perguntas_loja_config_normalizar(
                        ppv_state._perguntas_loja_config_obter(configs, str(item.get("loja") or "").strip())
                    ).get("notificar_whatsapp_aprovacoes") is True
                ),
                None,
            )
            if approval is not None:
                store = str(approval.get("loja") or "").strip()
                approval_id = str(approval.get("id") or "").strip()
                draft = str(approval.get("resposta_sugerida") or "").strip()
                draft_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()[:16]
                notification_key = hashlib.sha256(f"{client_id}|{subject_id}|{approval_id}|{draft_hash}".encode("utf-8")).hexdigest()
                token, _token_item = _question_approval_token(
                    state,
                    approval=approval,
                    subject_id=subject_id,
                    client_id=client_id,
                    username=username,
                )
                existing_notification = notifications.get(notification_key)
                if isinstance(existing_notification, dict) and (
                    existing_notification.get("interactive_sent") is True
                    or bool(existing_notification.get("sent_at"))
                ):
                    _question_set_active_thread(
                        state,
                        approval_id=approval_id,
                        token=token,
                        subject_id=subject_id,
                        client_id=client_id,
                        username=username,
                    )
                    continue
                result = _post_interactive_approval(
                    config,
                    subject_id=subject_id,
                    fingerprint=f"ppv:{notification_key}",
                    token=token,
                    body=_question_approval_body(approval),
                )
                if str(result.get("status") or "") in {"sent", "duplicate"}:
                    notifications[notification_key] = {
                        "sent_at": _now(),
                        "interactive_sent": True,
                        "status": "sent",
                        "approval_id": approval_id,
                        "subject_id": subject_id,
                    }
                    _question_set_active_thread(
                        state,
                        approval_id=approval_id,
                        token=token,
                        subject_id=subject_id,
                        client_id=client_id,
                        username=username,
                    )
                else:
                    blocked_status = str(result.get("status") or result.get("error") or "interactive_blocked")
                    notification = _post_proactive(
                        config,
                        {
                            "subject_id": subject_id,
                            "fingerprint": f"ppv-template:{notification_key}",
                            "event_type": "task_awaiting_approval",
                            "severity": "medium",
                            "text": f"Nova pergunta de comprador aguardando revisao na loja {store or 'nao informada'}.",
                            "template_name": "jk_black_jhon_nova_pergunta",
                            "template_params": [store or "Loja nao informada"],
                        },
                    )
                    notification_status = str(notification.get("status") or notification.get("error") or blocked_status)
                    notifications[notification_key] = {
                        "approval_id": approval_id,
                        "subject_id": subject_id,
                        "status": notification_status,
                        "interactive_status": blocked_status,
                        "blocked": notification_status in {"template_not_approved", "waiting_free_window", "policy_recheck_required"},
                        "updated_at": _now(),
                    }
                    try:
                        _bridge_store().record_notification(
                            f"ppv:{notification_key}",
                            event_type="mercado_livre_question",
                            status=notification_status,
                            reason=blocked_status,
                            details={"approval_id": approval_id, "store": store},
                        )
                    except Exception:
                        pass
        state["question_approval_notifications"] = notifications
        _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_approval_notification: {str(exc)[:800]}"


def _reload_bound_session(config: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    username = str(message.get("username") or config.get("username") or "").strip().lower()
    client_id = str(message.get("client_id") or config.get("client_id") or "").strip()
    if not username or not client_id:
        raise RuntimeError("binding_identity_missing")
    permissions = admin_usuarios_common._carregar_permissoes_usuario(username, client_id)
    return {"username": username, "client_id": client_id, "permissions": permissions, "is_full": permissions.get("full") is True}


def _pending_task_for_message(state: dict[str, Any], message_id: str) -> Optional[dict[str, Any]]:
    pending = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    item = pending.get(message_id)
    return item if isinstance(item, dict) else None


def _active_pending_for_conversation(
    state: dict[str, Any],
    conversation_id: str,
    *,
    exclude_message_id: str = "",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for pending_message_id, pending in pending_messages.items():
        if str(pending_message_id or "") == str(exclude_message_id or "") or not isinstance(pending, dict):
            continue
        if str(pending.get("conversation_id") or "") != str(conversation_id or ""):
            continue
        tasks = _pending_codex_tasks(pending)
        active = [task for task in tasks if str(task.get("status") or "") in {"queued", "running", "cancel_requested"}]
        pending_state = str(pending.get("job_state") or "")
        manager_active = str(pending.get("kind") or "") == "dual_function_manager" and pending_state in {
            "manager_queued", "manager_running", "waiting_retry", "awaiting_input", "retry_starting",
        }
        if not active and pending_state not in {"waiting_retry", "awaiting_input", "retry_starting"} and not manager_active:
            continue
        if str(pending.get("kind") or "") in {"dual_job_group", "dual_function_manager"} or not active:
            task = {
                "task_id": str(pending.get("job_group_id") or pending_message_id),
                "status": (
                    "queued"
                    if pending_state in {"manager_queued", "waiting_retry", "retry_starting"}
                    else "running" if pending_state == "manager_running" else pending_state or "running"
                ),
                "child_tasks": active,
            }
        else:
            task = active[0]
        candidates.append((str(pending_message_id), pending, task))
    candidates.sort(key=lambda item: str(item[1].get("created_at") or ""), reverse=True)
    return candidates[0] if candidates else ("", {}, {})


def _pending_task_ids(pending: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in list(pending.get("subtasks") or []):
        if isinstance(item, dict):
            task_id = str(item.get("task_id") or "").strip()
            if task_id and task_id not in values:
                values.append(task_id)
    if str(pending.get("kind") or "") == "dual_job_group":
        # Em grupos, cada holder aponta para a tentativa Sol corrente. O campo
        # superior existe apenas por compatibilidade e pode conter uma tentativa
        # antiga depois de um retry.
        return values
    task_id = str(pending.get("task_id") or "").strip()
    if task_id and task_id not in values:
        values.append(task_id)
    return values


def _pending_codex_tasks(pending: dict[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_id in _pending_task_ids(pending):
        task = codex_console._codex_load_task(task_id)
        if isinstance(task, dict):
            tasks.append(task)
    return tasks


def _update_pending_codex_tasks(pending: dict[str, Any], **changes: Any) -> None:
    for task_id in _pending_task_ids(pending):
        try:
            codex_console._codex_update_task(task_id, **changes)
        except Exception:
            continue


def _dual_retry_reason_text(task: dict[str, Any], result: dict[str, Any]) -> str:
    parts = [
        str(task.get("error") or ""),
        str(result.get("summary") or ""),
        " ".join(str(item or "") for item in list(result.get("missing") or [])),
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:2000]


def _dual_retry_is_auth_error(reason: Any) -> bool:
    text = unicodedata.normalize("NFKD", str(reason or "")).encode("ascii", "ignore").decode("ascii").lower()
    return bool(
        re.search(
            r"(?:\b401\b|\b403\b|unauthori[sz]ed|forbidden|token.*expir|autentic|credencial|reconect)",
            text,
        )
    )


def _dual_retry_classification(reason: Any) -> tuple[str, bool]:
    text = unicodedata.normalize("NFKD", str(reason or "")).encode("ascii", "ignore").decode("ascii").lower()
    if _dual_retry_is_auth_error(text):
        return "authentication", False
    if re.search(r"\b(permission|permissao|acesso negado|not allowed|nao autorizado)\b", text):
        return "permission", False
    if re.search(r"\b(invalid|invalido|entrada incompleta|missing input|campo obrigatorio|store_required)\b", text):
        return "invalid_input", False
    if re.search(r"\b(unsupported|nao suportad|somente consulta|read.?only|executor seguro)\b", text):
        return "unsupported", False
    if re.search(r"\b(429|rate.?limit|too many requests)\b", text):
        return "rate_limited", True
    if re.search(r"\b(timeout|timed out|connection|conexao|temporar\w*|remote.*closed|502|503|504|5\d\d)\b", text):
        return "transient_dependency", True
    if re.search(r"\b(evidencia|evidence|cobertura|coverage|resultado incompleto|dados insuficientes|fonte indisponivel)\b", text):
        return "insufficient_evidence", False
    return "incomplete_result", False


def _dual_retry_delay_seconds(retry_count: int, reason: Any, key: Any = "") -> int:
    index = max(0, min(int(retry_count or 1) - 1, len(WHATSAPP_RETRY_DELAYS_SECONDS) - 1))
    return int(WHATSAPP_RETRY_DELAYS_SECONDS[index])


def _local_web_fallback_context(query: str, client_id: str) -> dict[str, Any]:
    now_epoch = time.time()
    with WEB_FALLBACK_LOCK:
        if float(WEB_FALLBACK_CIRCUIT.get("opened_until_epoch") or 0) > now_epoch:
            return {
                "success": False,
                "data": [],
                "sources": [],
                "dados_suficientes": False,
                "coverage_complete": False,
                "error_class": "circuit_open",
                "retryable": True,
            }
    try:
        from backend.services import ia_web

        results = ia_web._ia_web_buscar_cached(
            str(query or "")[:2000],
            client_id=str(client_id or "default"),
            max_results=5,
            fast=True,
        )
        clean = [item for item in list(results or []) if isinstance(item, dict) and str(item.get("url") or "").strip()]
        if not clean:
            raise RuntimeError("local_web_fallback_empty")
        with WEB_FALLBACK_LOCK:
            WEB_FALLBACK_CIRCUIT.update({"failures": 0, "opened_until_epoch": 0.0, "last_error": ""})
        return {
            "success": True,
            "data": clean,
            "sources": [str(item.get("url") or "")[:600] for item in clean],
            "dados_suficientes": True,
            # Resultados de busca sao pistas para o agente de tarefa, nao
            # prova conclusiva de cobertura integral.
            "coverage_complete": False,
            "error_class": "",
            "retryable": False,
        }
    except Exception as exc:
        with WEB_FALLBACK_LOCK:
            failures = int(WEB_FALLBACK_CIRCUIT.get("failures") or 0) + 1
            WEB_FALLBACK_CIRCUIT.update(
                {
                    "failures": failures,
                    "opened_until_epoch": time.time() + 60 if failures >= 3 else 0.0,
                    "last_error": str(exc)[:500],
                }
            )
        return {
            "success": False,
            "data": [],
            "sources": [],
            "dados_suficientes": False,
            "coverage_complete": False,
            "error_class": "transient_dependency",
            "retryable": True,
        }


def _dual_append_unique(target: list[str], values: Any, *, limit: int, item_limit: int) -> list[str]:
    output = [str(item or "").strip()[:item_limit] for item in list(target or []) if str(item or "").strip()]
    for value in list(values or []):
        text = str(value or "").strip()[:item_limit]
        if text and text not in output:
            output.append(text)
    return output[:limit]


def _dual_preserve_worker_result(pending: dict[str, Any], result: dict[str, Any]) -> None:
    pending["verified_facts"] = _dual_append_unique(
        list(pending.get("verified_facts") or []),
        result.get("verified_facts"),
        limit=30,
        item_limit=2000,
    )
    pending["verified_sources"] = _dual_append_unique(
        list(pending.get("verified_sources") or []),
        result.get("sources"),
        limit=30,
        item_limit=1000,
    )


def _dual_worker_disposition(task: dict[str, Any], result: dict[str, Any]) -> str:
    task_status = str(task.get("status") or "")
    if task_status == "canceled" and str(task.get("cancel_source") or "") in {
        "whatsapp",
        "whatsapp_conversation_agent",
    }:
        return "canceled"
    result_status = str(result.get("status") or "failed")
    if list(result.get("data_requests") or []):
        return "manager_request"
    if result_status == "blocked" and list(result.get("questions") or []):
        return "awaiting_input"
    if task_status in {"partial", "failed", "canceled"}:
        return "waiting_retry"
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    if verification.get("confirmed") is False or str(verification.get("status") or "") == "partial":
        return "waiting_retry"
    validations = [
        item.get("tool_validation")
        for item in list(task.get("tool_results_summary") or [])
        if isinstance(item, dict) and isinstance(item.get("tool_validation"), dict)
    ]
    consolidated_sufficient = bool(
        verification.get("confirmed") is True
        or any(item.get("dados_suficientes") is True for item in validations)
    )
    if validations and not consolidated_sufficient:
        return "waiting_retry"
    if result_status == "completed":
        facts = [str(item or "").strip() for item in list(result.get("verified_facts") or []) if str(item or "").strip()]
        sources = [str(item or "").strip() for item in list(result.get("sources") or []) if str(item or "").strip()]
        confidence = str(result.get("confidence") or "unknown")
        if result.get("evidence_sufficient") is not True or not facts or not sources or confidence not in {"high", "medium"}:
            return "waiting_retry"
        absence_text = unicodedata.normalize(
            "NFKD",
            " ".join([str(result.get("summary") or ""), *facts]),
        ).encode("ascii", "ignore").decode("ascii").lower()
        confirms_absence = bool(
            re.search(
                r"\b(?:nenhum|nenhuma|nao (?:foi )?encontrad[oa]s?|zero registros?|sem registros?|inexistente|ausencia confirmada)\b",
                absence_text,
            )
        )
        if confirms_absence and result.get("coverage_complete") is not True:
            return "waiting_retry"
        return "completed"
    return "waiting_retry"


def _dual_schedule_retry(
    pending: dict[str, Any],
    holder: dict[str, Any],
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    key: str,
) -> None:
    _dual_preserve_worker_result(pending, result)
    task_id = str(task.get("task_id") or holder.get("task_id") or "").strip()
    history = [str(item or "").strip() for item in list(holder.get("attempt_task_ids") or []) if str(item or "").strip()]
    if task_id and task_id not in history:
        history.append(task_id)
    disposition = _dual_worker_disposition(task, result)
    holder["attempt_task_ids"] = history[-50:]
    holder["last_attempt_task_id"] = task_id
    holder["last_attempt_at"] = str(task.get("completed_at") or _now())
    holder["last_progress_at"] = str(task.get("last_progress_at") or task.get("completed_at") or _now())
    if disposition == "awaiting_input":
        holder["state"] = "awaiting_input"
        holder["next_retry_at_epoch"] = 0
        holder["retry_reason"] = "awaiting_user_input"
        pending["job_state"] = "awaiting_input"
        pending["pending_questions"] = _dual_append_unique(
            list(pending.get("pending_questions") or []),
            result.get("questions"),
            limit=10,
            item_limit=1000,
        )
        return
    if disposition == "canceled":
        holder["state"] = "canceled"
        pending["job_state"] = "canceled"
        return
    retry_count = max(0, int(holder.get("retry_count") or 0)) + 1
    reason = _dual_retry_reason_text(task, result) or "resultado_incompleto"
    error_class, retryable = _dual_retry_classification(reason)
    if retryable and bool(holder.get("requires_web")) and not holder.get("local_web_fallback_attempted"):
        fallback = _local_web_fallback_context(
            str(pending.get("request_text") or holder.get("prompt") or ""),
            str(pending.get("client_id") or "default"),
        )
        holder["local_web_fallback_attempted"] = True
        holder["local_web_fallback"] = fallback
        if fallback.get("success") is True:
            fallback_lines = [
                f"- {str(item.get('title') or '')[:200]} | {str(item.get('url') or '')[:600]} | {str(item.get('snippet') or '')[:500]}"
                for item in list(fallback.get("data") or [])[:5]
                if isinstance(item, dict)
            ]
            if fallback_lines:
                holder["prompt"] = (
                    str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or "")
                    + "\n\nFallback local de busca (validar nas paginas antes de concluir):\n"
                    + "\n".join(fallback_lines)
                )[:12000]
            pending["verified_sources"] = _dual_append_unique(
                list(pending.get("verified_sources") or []),
                fallback.get("sources"),
                limit=30,
                item_limit=1000,
            )
    try:
        _bridge_store().record_attempt(
            str(pending.get("message_id") or pending.get("job_group_id") or task_id),
            retry_count,
            "running" if retryable else "partial",
            error_class=error_class,
            retryable=retryable,
            details={"task_id": task_id, "reason": reason[:500]},
        )
    except Exception:
        pass
    if not retryable or retry_count > WHATSAPP_MAX_RETRY_ATTEMPTS:
        holder.update(
            {
                "state": "partial",
                "retry_count": retry_count,
                "retry_reason": reason[:1000],
                "retryable": False,
                "error_class": error_class,
                "next_retry_at_epoch": 0,
            }
        )
        pending.update(
            {
                "job_state": "partial",
                "retry_policy": "bounded",
                "terminal_reason": reason[:1000],
                "terminal_error_class": error_class,
            }
        )
        return
    delay = _dual_retry_delay_seconds(retry_count, reason, key)
    holder.update(
        {
            "state": "waiting_retry",
            "retry_count": retry_count,
            "retry_reason": reason[:1000],
            "next_retry_at_epoch": time.time() + delay,
            "next_retry_delay_seconds": delay,
            "auth_retry": False,
            "retryable": True,
            "error_class": error_class,
        }
    )
    pending.update(
        {
            "job_state": "waiting_retry",
            "retry_policy": "bounded",
            "last_retry_reason": reason[:1000],
            "last_retry_scheduled_at": _now(),
        }
    )
    if holder.get("auth_retry"):
        pending["auth_retry_active"] = True
        if pending.get("auth_notice_sent") is not True:
            pending["auth_notice_pending"] = True
    if task_id:
        codex_console._codex_update_task(
            task_id,
            handoff_status="retry_scheduled",
            delivery_state="waiting_retry",
            retry_count=retry_count,
            retry_reason=reason[:1000],
            next_retry_at_epoch=holder["next_retry_at_epoch"],
        )


def _dual_migrate_pending_v7(pending: dict[str, Any]) -> bool:
    changed = False
    before_deadline = float(pending.get("deadline_at_epoch") or 0)
    before_policy = str(pending.get("retry_policy") or "")
    _ensure_job_contract(pending)
    if not before_deadline or before_policy != "bounded":
        changed = True
    pending.setdefault("verified_facts", [])
    pending.setdefault("verified_sources", [])
    kind = str(pending.get("kind") or "")
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    for holder in holders:
        holder.setdefault("retry_count", 0)
        holder.setdefault("attempt_task_ids", [str(holder.get("task_id") or "")] if holder.get("task_id") else [])
        expected_retry_count = max(0, int(holder.get("current_attempt") or 1) - 1)
        if int(holder.get("retry_count") or 0) < expected_retry_count:
            holder["retry_count"] = expected_retry_count
            changed = True
        state = str(holder.get("state") or "")
        task_id = str(holder.get("task_id") or "")
        task = codex_console._codex_load_task(task_id) if task_id else None
        status = str((task or {}).get("status") or "")
        canceled_by_deadline = bool(
            status == "canceled"
            and str((task or {}).get("cancel_source") or "") == "whatsapp_deadline"
        )
        if canceled_by_deadline:
            holder["state"] = "partial"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = "limite_total_atingido"
            pending["job_state"] = "partial"
            changed = True
            continue
        if int(holder.get("retry_count") or 0) > WHATSAPP_MAX_RETRY_ATTEMPTS and state in {
            "waiting_retry",
            "retry_starting",
            "running",
            "waiting_result",
        }:
            holder["state"] = "partial"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = str(holder.get("retry_reason") or "limite_de_tentativas_atingido")[:1000]
            pending["job_state"] = "partial"
            changed = True
            continue
        if state in {"running", "waiting_result"} and status in {"", "awaiting_approval"}:
            # Sol do WhatsApp e sempre read-only e pre-aprovado. Uma tentativa
            # antiga sem arquivo ou presa em aprovacao nao pode bloquear o job;
            # ela vira uma nova tentativa, sem autorizar nem cancelar a antiga.
            holder["state"] = "waiting_retry"
            holder["next_retry_at_epoch"] = 0
            holder["retry_reason"] = "stale_readonly_attempt"
            changed = True
            continue
        if state == "retry_starting":
            holder["state"] = "waiting_retry"
            holder["next_retry_at_epoch"] = 0
            changed = True
            continue
        if state:
            continue
        if status in {"queued", "running", "cancel_requested"}:
            holder["state"] = "running"
        else:
            holder["state"] = "waiting_result" if task_id else "waiting_retry"
            if not task_id:
                holder["next_retry_at_epoch"] = 0
        changed = True
    holder_states = {str(holder.get("state") or "") for holder in holders}
    if "partial" in holder_states and not ({"running", "waiting_retry", "awaiting_input"} & holder_states):
        pending["job_state"] = "partial"
        changed = True
    elif "running" not in holder_states and "awaiting_input" not in holder_states and "waiting_retry" in holder_states:
        pending["job_state"] = "waiting_retry"
        changed = True
    elif not str(pending.get("job_state") or ""):
        pending["job_state"] = "running"
        changed = True
    return changed


def _recover_dual_pending_after_restart(state: dict[str, Any]) -> int:
    """Reativa retries persistidos sem encerrar ou duplicar tarefas em curso."""

    recovered = 0
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, pending in list(pending_messages.items()):
        if not isinstance(pending, dict) or str(pending.get("kind") or "") not in {"dual_worker", "dual_job_group", "dual_function_manager"}:
            continue
        if str(pending.get("kind") or "") == "dual_function_manager":
            if str(pending.get("manager_state") or "") in {"running", "queued", "partial", "waiting_retry"}:
                pending.update(
                    {
                        "manager_state": "queued",
                        "job_state": "manager_queued",
                        "manager_next_retry_at_epoch": 0,
                        "restart_recovered_at": _now(),
                    }
                )
                _save_pending(state, str(message_id), pending)
                recovered += 1
            continue
        changed = _dual_migrate_pending_v7(pending)
        holders = (
            [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
            if str(pending.get("kind") or "") == "dual_job_group"
            else [pending]
        )
        for holder in holders:
            if str(holder.get("state") or "") == "waiting_retry":
                holder["next_retry_at_epoch"] = 0
                changed = True
                recovered += 1
        if changed:
            pending["restart_recovered_at"] = _now()
            _save_pending(state, str(message_id), pending)
    return recovered


def _whatsapp_is_job_status_probe(text: str) -> bool:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    if not normalized:
        return False
    if re.fullmatch(r"[?.!]+", normalized):
        return True
    return bool(
        len(normalized) <= 120
        and re.fullmatch(
            r"(?:e ai|e dai|terminou|acabou|concluiu|alguma novidade|como estamos|como esta|ja terminou|deu certo)[?.! ]*",
            normalized,
        )
    )


def _whatsapp_is_task_complement(text: str) -> bool:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").strip().lower()
    if not normalized or len(normalized) > 600:
        return False
    if re.match(
        r"^(na verdade|corrija|corrige|adicione|inclua|considere|use |troque|mude|refaca|quero dizer|quando eu disse|"
        r"complementando|mais um detalhe|a loja e|o sku e|o pedido e|e tambem|tambem |sobre isso|nesse caso|"
        r"e so\b|so\b|somente\b|apenas\b|continue\b|pode continuar\b|sem\b|nao inclua\b|nao precisa\b)",
        normalized,
    ):
        return True
    return bool(
        len(normalized) <= 220
        and re.search(r"\b(isso|isto|essa|esse|dessa|desse|nela|nele|anterior|mesmo|mesma|mesma loja|mesmo pedido|mais detalhes?)\b", normalized)
    )


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


def _dual_append_conversation_turn(
    record: dict[str, Any],
    *,
    role: str,
    text: Any,
    event_type: str = "",
) -> dict[str, Any]:
    """Persist a small, PII-minimized dialogue window independent of Codex threads."""

    content = re.sub(r"\s+", " ", str(text or "").replace("\x00", " ")).strip()[:1200]
    normalized_role = "assistant" if str(role or "").strip().lower() == "assistant" else "user"
    if not content:
        return record
    turns = [
        {
            "role": "assistant" if str(item.get("role") or "") == "assistant" else "user",
            "text": str(item.get("text") or "").strip()[:1200],
            "event_type": str(item.get("event_type") or "")[:40],
            "at": str(item.get("at") or "")[:40],
        }
        for item in list(record.get("recent_turns") or [])[-15:]
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if turns and turns[-1]["role"] == normalized_role and turns[-1]["text"] == content:
        return record
    turns.append({"role": normalized_role, "text": content, "event_type": str(event_type or "")[:40], "at": _now()})
    record["recent_turns"] = turns[-16:]
    return record


def _dual_recent_conversation_context(
    record: dict[str, Any],
    *,
    current_user_message: str = "",
) -> list[dict[str, str]]:
    turns = [
        {"role": str(item.get("role") or "user"), "text": str(item.get("text") or "").strip()[:900]}
        for item in list(record.get("recent_turns") or [])[-12:]
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    current = re.sub(r"\s+", " ", str(current_user_message or "")).strip()[:900]
    if turns and current and turns[-1]["role"] == "user" and turns[-1]["text"] == current:
        turns.pop()
    return turns[-10:]


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
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    message_id, pending, task = _active_pending_for_conversation(
        state,
        conversation_id,
        exclude_message_id=exclude_message_id,
    )
    if task and str(task.get("status") or "") != "running":
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        for candidate_message_id, candidate_pending in pending_messages.items():
            if not isinstance(candidate_pending, dict) or str(candidate_pending.get("kind") or "") != "dual_worker":
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
) -> dict[str, Any]:
    settings = _whatsapp_dual_agent_settings(config)
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        thread_id = str(record.get("thread_id") or "")
        conversation_context = _dual_recent_conversation_context(
            record,
            current_user_message=user_message if event_type == "user_message" else "",
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
            ai_behavior=ai_behavior,
            tick_index=tick_index,
        )
        return value if isinstance(value, dict) else {}

    def valid(value: dict[str, Any]) -> bool:
        action = str(value.get("action") or "").strip()
        if action not in {"reply", "request_information", "delegate", "queue", "steer", "cancel_job", "wait"}:
            return False
        if action in {"reply", "request_information", "steer", "cancel_job"}:
            return bool(str(value.get("reply_text") or "").strip())
        if action in {"delegate", "queue"}:
            return bool(str(value.get("job_prompt") or user_message or "").strip())
        return True

    first_error = ""
    try:
        decision = invoke(thread_id)
    except Exception as exc:
        decision = {}
        first_error = str(exc)[:500]
    if not valid(decision):
        try:
            # Uma unica nova tentativa em thread limpa evita carregar uma
            # resposta estruturada vazia ou invalida para a rodada seguinte.
            decision = invoke("")
        except Exception as exc:
            decision = {}
            first_error = first_error or str(exc)[:500]
    if not valid(decision):
        RUNTIME_STATE["conversation_fallback_last_error"] = first_error or "conversation_agent_empty_or_invalid_reply"
        decision = {
            "action": "reply",
            "reply_text": (
                "Nao consegui identificar com seguranca o que voce quer consultar. "
                "Pode reformular em uma frase, dizendo apenas o resultado que precisa?"
            ),
            "thread_id": "",
            "fallback_terminal": True,
        }
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record.update(
            {
                "thread_id": str(decision.get("thread_id") or record.get("thread_id") or "")[:200],
                "requested_model": str(decision.get("requested_model") or "")[:100],
                "effective_model": str(decision.get("effective_model") or "")[:100],
                "reasoning_effort": str(decision.get("reasoning_effort") or "")[:20],
                "speed": str(decision.get("speed") or "")[:20],
                "service_tier": str(decision.get("service_tier") or "")[:40],
                "last_event_type": event_type,
                "last_activity_at_epoch": time.time(),
                "last_conversation_at": _now(),
            }
        )
        reply_text = str(decision.get("reply_text") or "").strip()
        if reply_text:
            _dual_append_conversation_turn(record, role="assistant", text=reply_text, event_type=event_type)
        _save_dual_conversation_record(state, conversation_id, record)
    return decision


def _deterministic_direct_query_plan(
    request_text: str,
    query_policy: dict[str, Any],
    permissions: Any,
) -> dict[str, Any]:
    """Return a one-source read-only plan that can bypass both planning LLMs."""

    if not query_policy:
        return {}
    catalog = _function_manager_catalog(permissions)
    stock_request = bool(
        "estoque" in list(query_policy.get("domains") or [])
        or re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", _whatsapp_text_key(request_text))
    )
    plan = _function_manager_enforce_plan(
        {"tool_calls": [], "requires_sol": False, "requires_web": False},
        request_text=request_text,
        query_policy=query_policy,
        catalog=catalog,
        max_calls=3 if stock_request else 1,
    )
    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)]
    stock_chain = [str(item.get("tool_id") or "") for item in calls]
    valid_stock_chain = bool(
        stock_request
        and stock_chain
        and stock_chain == [
            tool_id
            for tool_id in ("bling_stock_balances", "mercado_livre_listing", "stock_data")
            if tool_id in stock_chain
        ]
    )
    if (len(calls) != 1 and not valid_stock_chain) or plan.get("requires_sol") or plan.get("requires_web"):
        return {}
    return plan


def _dual_delegate_query_policy(
    request_text: str,
    session: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    direct = _whatsapp_query_policy(request_text, session)
    store_scoped = _whatsapp_store_scoped_request(request_text)
    # O contexto de loja so pode ser herdado por uma tarefa que continue sendo
    # comercial. Frases naturais do Luna como "agora pesquise a previsao do
    # tempo" nao podem reaproveitar a ultima loja consultada.
    if not direct and not store_scoped:
        return {}
    inherited = _whatsapp_inherit_query_store_context(request_text, direct, state, conversation_id, session)
    policy = direct or inherited
    if not policy and store_scoped:
        policy = _whatsapp_store_scope_policy(request_text, session)
    return policy if isinstance(policy, dict) else {}


def _function_manager_catalog(permissions: Any) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    catalog: list[dict[str, Any]] = []
    for item in codex_assistant._assistant_tools_public(permissions):
        if not isinstance(item, dict) or item.get("read_only") is not True:
            continue
        tool_id = str(item.get("id") or "").strip()
        if not tool_id:
            continue
        catalog.append(
            {
                "id": tool_id,
                "description": str(item.get("description") or "")[:500],
                "external": item.get("external") is True,
                "output_fields": [str(value or "")[:100] for value in list(item.get("output_fields") or [])[:30]],
                "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
            }
        )
    return catalog[:100]


def _function_manager_extract_identifiers(value: Any) -> tuple[str, str]:
    text = str(value or "")
    sku_match = re.search(r"\bsku\s*[:#-]?\s*([a-z0-9][a-z0-9._/-]{0,99})\b", text, re.IGNORECASE)
    item_match = re.search(r"\b(MLB\d{6,})\b", text, re.IGNORECASE)
    return (
        str(sku_match.group(1) if sku_match else "").strip(),
        str(item_match.group(1) if item_match else "").upper(),
    )


def _function_manager_enforce_plan(
    plan: dict[str, Any],
    *,
    request_text: str,
    query_policy: dict[str, Any],
    catalog: list[dict[str, Any]],
    max_calls: int,
) -> dict[str, Any]:
    result = dict(plan or {})
    text = _whatsapp_text_key(request_text)
    context_request = str(query_policy.get("context_request") or request_text or "").strip()
    allowed = {str(item.get("id") or "") for item in catalog if isinstance(item, dict)}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    forbidden = {str(item or "") for item in list(source_policy.get("forbidden_tools") or []) if str(item or "")}
    explicit_sales = bool(re.search(r"\b(venda|vendas|vendido|vendidos|pedido|pedidos|faturamento|ranking)\b", text))
    explicit_returns = bool(re.search(r"\b(devolucao|devolucoes|reembolso|reembolsos|estorno|estornos)\b", text))
    explicit_stock = bool(
        re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque|full|fulfillment)\b", text)
        or "estoque" in list(query_policy.get("domains") or [])
        or "mercado_full" in list(query_policy.get("domains") or [])
    )
    explicit_image = bool(re.search(r"\b(foto|fotos|imagem|imagens)\b", text))
    mentions_ml = bool(re.search(r"\b(mercado livre|mercadolivre|mlb\d+)\b", text))
    product_query = bool(re.search(r"\b(sku\s*[a-z0-9._/-]+|produto|anuncio|informacao|informacoes|detalhe|detalhes)\b", text))
    technical_web = bool(re.search(r"\b(serve|funciona|encaixa|compativel|compatibilidade|aplica|aplicacao|manual|oem|fabricante)\b", text))
    sku, item_id = _function_manager_extract_identifiers(context_request)
    result["sku"] = str(result.get("sku") or query_policy.get("sku") or sku)[:100]
    result["item_id"] = str(result.get("item_id") or query_policy.get("item_id") or item_id)[:60]
    exact_store = str(query_policy.get("store") or "").strip()
    if exact_store:
        result["store"] = exact_store
        result["store_mode"] = "single"
    elif str(query_policy.get("store_mode") or "") == "all":
        result["store"] = ""
        result["store_mode"] = "all"

    heavy_sales_tools = {
        "mercado_livre_orders", "bling_sales_orders", "sales_returns_query", "sales_ranking", "sales_summary",
        "sales_timeseries", "avg_ticket", "period_comparison", "sales_anomalies",
    }
    return_tools = {"mercado_livre_returns", "returns_summary", "return_rate"}
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_call(tool_id: str, arguments: Optional[dict[str, Any]] = None, required: bool = True, reason: str = "") -> None:
        if tool_id not in allowed or tool_id in forbidden:
            return
        if tool_id in heavy_sales_tools and not explicit_sales:
            return
        if tool_id in return_tools and not explicit_returns:
            return
        if tool_id in {"bling_stock_balances", "mercado_livre_full_stock", "stock_data"} and not explicit_stock:
            return
        args = dict(arguments or {})
        if exact_store:
            args["loja"] = exact_store
        if result.get("sku"):
            args.setdefault("sku", result["sku"])
        if result.get("item_id"):
            args.setdefault("item_id", result["item_id"])
        args.setdefault("message", context_request[:4000])
        signature = json.dumps({"tool_id": tool_id, "arguments": args}, ensure_ascii=False, sort_keys=True, default=str)
        if signature in seen or len(calls) >= max(1, min(6, int(max_calls or 6))):
            return
        seen.add(signature)
        calls.append({"tool_id": tool_id, "arguments": args, "required": bool(required), "reason": str(reason or "")[:500]})

    for item in list(result.get("tool_calls") or []):
        if isinstance(item, dict):
            add_call(
                str(item.get("tool_id") or ""),
                item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                item.get("required") is not False,
                str(item.get("reason") or ""),
            )

    for required_tool in list(source_policy.get("required_tools") or []):
        add_call(
            str(required_tool or ""),
            {"force_refresh": source_policy.get("force_refresh") is not False},
            True,
            "fonte determinada pela politica de roteamento",
        )

    # Guardas deterministicas impedem que uma expansao textual do Luna converta
    # uma consulta de cadastro em varredura completa de pedidos.
    if product_query and not explicit_sales and not explicit_returns and not explicit_stock:
        add_call(
            "mercado_livre_listing",
            {"incluir_detalhes": True, "force_refresh": True},
            mentions_ml,
            "dados do anuncio por SKU",
        )
    if product_query and not explicit_sales and not explicit_returns and not explicit_stock:
        add_call("product_data", {}, False, "cadastro do produto")
        add_call("product_image", {}, False, "imagem cadastrada do produto")
    if explicit_image:
        add_call("product_image", {}, True, "imagem solicitada")
    if explicit_stock:
        if re.search(r"\b(full|fulfillment)\b", text):
            add_call("mercado_livre_full_stock", {"force_refresh": True}, True, "estoque Full solicitado")
        else:
            add_call("bling_stock_balances", {"force_refresh": True}, False, "prioridade 1: saldo atual na Bling")
            add_call("mercado_livre_listing", {"force_refresh": True}, False, "prioridade 2: saldo dos anuncios no Mercado Livre")
            add_call("stock_data", {"force_refresh": True}, False, "prioridade 3: saldo interno do JK Sistema")
            stock_order = {"bling_stock_balances": 1, "mercado_livre_listing": 2, "stock_data": 3}
            stock_calls = [item for item in calls if str(item.get("tool_id") or "") in stock_order]
            other_calls = [item for item in calls if str(item.get("tool_id") or "") not in stock_order]
            for item in stock_calls:
                item["required"] = False
                item["stock_priority"] = stock_order[str(item.get("tool_id") or "")]
            calls = sorted(stock_calls, key=lambda item: int(item.get("stock_priority") or 99)) + other_calls
    if explicit_sales:
        add_call("mercado_livre_orders" if mentions_ml else "sales_ranking", {"force_refresh": True}, True, "vendas solicitadas")
    if explicit_returns:
        add_call("mercado_livre_returns" if mentions_ml else "returns_summary", {"force_refresh": True}, True, "devolucoes solicitadas")

    result["tool_calls"] = calls
    # Consultas internas simples sao respondidas pelo Luna Conversa com o
    # pacote validado, sem pagar uma segunda rodada Sol. Se nenhuma funcao
    # interna resolver a solicitacao, a decisao de pesquisa externa do
    # gerenciador e preservada. Compatibilidade sempre exige analise tecnica.
    result["requires_web"] = bool(technical_web or (result.get("requires_web") and not calls))
    result["requires_sol"] = bool(result["requires_web"] or (result.get("requires_sol") and not calls))
    result["manager_guard"] = {
        "explicit_sales": explicit_sales,
        "explicit_returns": explicit_returns,
        "explicit_stock": explicit_stock,
        "listing_first": product_query and not explicit_sales and not explicit_returns and not explicit_stock,
    }
    return result


def _stock_balance_contract(result: Any) -> dict[str, Any]:
    """Extract only a store-scoped, reliable Bling balance from a tool wrapper."""

    source = result if isinstance(result, dict) else {}
    stored = source.get("stock_balance")
    if isinstance(stored, dict):
        return dict(stored)

    candidates: list[dict[str, Any]] = []
    summary = source.get("summary")
    if isinstance(summary, list):
        candidates.extend(item for item in summary if isinstance(item, dict))
    elif isinstance(summary, dict) and str(source.get("tool_id") or "") == "bling_stock_balances":
        candidates.append(source)
    for row in list(source.get("top_rows") or []):
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "tool_id": "bling_stock_balances",
                "loja": row.get("loja") or row.get("store"),
                "summary": {
                    "chart_data": {
                        "totals": {
                            "skus": 1,
                            "store_available": row.get("saldo_loja_total"),
                            "gross_returned": row.get("saldo_bruto_retornado"),
                        },
                        "ranking": [{
                            "sku": row.get("sku"),
                            "title": row.get("produto") or row.get("title"),
                            "quantity": row.get("saldo_loja_total"),
                            "quantity_reliable": row.get("cobertura_depositos_completa") is not False,
                        }],
                        "coverage_complete": row.get("cobertura_depositos_completa") is not False,
                    },
                    "partial": row.get("cobertura_depositos_completa") is False,
                },
            }
        )

    direct = next(
        (item for item in candidates if str(item.get("tool_id") or "") == "bling_stock_balances"),
        {},
    )
    payload = direct.get("summary") if isinstance(direct.get("summary"), dict) else {}
    chart = payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
    totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else {}
    ranking = [item for item in list(chart.get("ranking") or []) if isinstance(item, dict)]
    first = ranking[0] if ranking else {}
    quantity = totals.get("store_available")
    numeric_quantity = isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
    quantity_reliable = not ranking or first.get("quantity_reliable") is True
    confirmed = bool(
        numeric_quantity
        and int(totals.get("skus") or 0) > 0
        and chart.get("coverage_complete") is True
        and payload.get("partial") is not True
        and quantity_reliable
    )
    stores = [str(item or "").strip() for item in list(chart.get("stores") or []) if str(item or "").strip()]
    validation = source.get("tool_validation") if isinstance(source.get("tool_validation"), dict) else {}
    warnings = [str(item or "").strip() for item in list(validation.get("warnings") or []) if str(item or "").strip()]
    warning_key = " ".join(_whatsapp_text_key(item) for item in warnings)
    auth_failed = bool(
        re.search(r"\b(token.*expir\w*|http 401|nao autoriz\w*|autentic\w*|credencial\w*)\b", warning_key)
    )
    fallback_identified = any(
        str(item.get("tool_id") or "") in {"stock_data", "product_data"}
        and isinstance(item.get("summary"), dict)
        and item.get("summary", {}).get("found") is True
        for item in candidates
    )
    if confirmed:
        reason = "Saldo numerico por loja confirmado diretamente na Bling."
        error_class = ""
    elif auth_failed:
        reason = "A autenticacao da Bling desta loja expirou; o saldo nao foi confirmado."
        error_class = "authentication"
    else:
        reason = "A consulta nao retornou saldo numerico confiavel para esta loja."
        error_class = "insufficient_evidence"
    return {
        "confirmed": confirmed,
        "store": str(direct.get("loja") or direct.get("store") or (stores[0] if stores else "")).strip(),
        "sku": str(first.get("sku") or source.get("sku") or "").strip(),
        "title": str(first.get("title") or first.get("produto") or "").strip(),
        "store_available": float(quantity) if numeric_quantity else None,
        "scope": str(payload.get("stock_scope") or "bling_non_full_only"),
        "full_excluded": str(payload.get("full_provider") or "") == "mercado_livre_api_only",
        "fallback_identified": fallback_identified,
        "auth_failed": auth_failed,
        "reason": reason,
        "error_class": error_class,
        "retryable": False,
    }


def _normalize_tool_result_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {"success": False, "error": "resultado_invalido"}
    validation = source.get("tool_validation") if isinstance(source.get("tool_validation"), dict) else {}
    error = str(source.get("error") or source.get("failure") or source.get("empty_reason") or "").strip()
    error_class, retryable = _dual_retry_classification(error) if error else ("", False)
    data = source.get("data")
    if data is None:
        for key in ("result", "rows", "all_rows", "summary"):
            if source.get(key) not in (None, ""):
                data = source.get(key)
                break
    record_count = source.get("records")
    if not isinstance(record_count, (int, float)):
        if isinstance(data, list):
            record_count = len(data)
        elif isinstance(data, dict):
            rows = data.get("rows") or data.get("records") or data.get("items")
            record_count = len(rows) if isinstance(rows, list) else (1 if data else 0)
        else:
            record_count = 0
    explicit_sufficient = validation.get("dados_suficientes")
    if explicit_sufficient is None:
        explicit_sufficient = source.get("dados_suficientes")
    dados_suficientes = bool(
        explicit_sufficient is True
        or (
            explicit_sufficient is None
            and source.get("success") is True
            and int(record_count or 0) > 0
            and not error
        )
    )
    paging = source.get("paging") if isinstance(source.get("paging"), dict) else {}
    coverage_complete = bool(
        dados_suficientes
        and source.get("coverage_complete") is not False
        and source.get("partial_response") is not True
        and source.get("truncated") is not True
        and paging.get("has_more") is not True
    )
    raw_sources = source.get("sources") if isinstance(source.get("sources"), list) else []
    raw_human_sources = source.get("sources_human") if isinstance(source.get("sources_human"), list) else []
    sources: list[str] = []
    for value in [source.get("source_label"), source.get("source"), *raw_sources, *raw_human_sources]:
        text = str(value or "").strip()
        if text and text not in sources:
            sources.append(text[:500])
    return {
        "success": bool(source.get("success") is True and not error),
        "data": data,
        "sources": sources,
        "dados_suficientes": dados_suficientes,
        "coverage_complete": coverage_complete,
        "error_class": error_class or ("insufficient_evidence" if not dados_suficientes else ""),
        "retryable": bool(retryable),
        "records": int(record_count or 0),
    }


def _function_manager_compact_result(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {"success": False, "error": "resultado_invalido"}
    compact: dict[str, Any] = {}
    for key in (
        "tool_id", "tool_label", "module", "success", "records", "source", "source_label", "sources",
        "sources_human", "warnings", "empty_reason", "tool_validation", "confidence", "paging", "generated_at",
        "error", "failure", "message", "summary", "data", "rows", "result", "top_rows",
    ):
        if key not in source:
            continue
        value = source.get(key)
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) > 12000:
            if isinstance(value, list):
                value = value[:20]
            elif isinstance(value, dict):
                value = {str(k): v for k, v in list(value.items())[:30]}
            serialized = json.dumps(value, ensure_ascii=False, default=str)[:12000]
            try:
                value = json.loads(serialized)
            except Exception:
                value = serialized
        compact[key] = value
    compact.update(_normalize_tool_result_contract(source))
    if str(source.get("tool_id") or "") == "bling_stock_balances":
        stock_balance = _stock_balance_contract(source)
        compact["stock_balance"] = stock_balance
        compact["dados_suficientes"] = stock_balance.get("confirmed") is True
        compact["coverage_complete"] = stock_balance.get("confirmed") is True
        compact["error_class"] = str(stock_balance.get("error_class") or "")
        compact["retryable"] = stock_balance.get("retryable") is True
    return compact


def _marketplace_listing_stock_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {}
    rows = [item for item in list(source.get("top_rows") or []) if isinstance(item, dict)]
    confirmed_rows: list[dict[str, Any]] = []
    for row in rows:
        quantity = row.get("available_quantity")
        if quantity is None:
            quantity = row.get("variation_available_quantity")
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
            continue
        confirmed_rows.append(
            {
                "item_id": str(row.get("id") or row.get("item_id") or "").strip(),
                "sku": str(row.get("seller_sku") or row.get("sku") or "").strip(),
                "title": str(row.get("title") or row.get("titulo") or "").strip(),
                "available_quantity": float(quantity),
            }
        )
    confirmed = bool(
        source.get("dados_suficientes") is True
        and source.get("coverage_complete") is True
        and confirmed_rows
    )
    return {
        "confirmed": confirmed,
        "rows": confirmed_rows[:10],
        "reason": (
            "Saldo por anuncio confirmado na API do Mercado Livre."
            if confirmed
            else "O Mercado Livre nao retornou saldo numerico de anuncio com cobertura suficiente."
        ),
    }


def _local_stock_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {}
    stored = source.get("local_stock")
    if isinstance(stored, dict):
        return dict(stored)
    payload: dict[str, Any] = {}
    for item in list(source.get("summary") or []):
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != "stock_data":
            continue
        candidate = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        if candidate:
            payload = candidate
            break
    has_numeric_contract = bool(
        payload.get("found") is True
        and str(payload.get("sku") or "").strip()
        and all(
            isinstance(payload.get(key), (int, float)) and not isinstance(payload.get(key), bool)
            for key in ("saldo_loja_total", "saldo_full_total", "saldo_total")
        )
    )
    confirmed = bool(source.get("dados_suficientes") is True and has_numeric_contract)
    return {
        "confirmed": confirmed,
        "sku": str(payload.get("sku") or "").strip(),
        "title": str(payload.get("nome") or "").strip(),
        "store_available": payload.get("saldo_loja_total") if has_numeric_contract else None,
        "full_available": payload.get("saldo_full_total") if has_numeric_contract else None,
        "total_available": payload.get("saldo_total") if has_numeric_contract else None,
        "reason": (
            "Saldo agregado confirmado no cadastro interno do JK Sistema."
            if confirmed
            else "O JK Sistema nao retornou um contrato numerico de estoque para o SKU."
        ),
    }


def _stock_tool_result_confirmed(result: dict[str, Any]) -> bool:
    tool_id = str(result.get("tool_id") or "")
    if tool_id == "bling_stock_balances":
        return _stock_balance_contract(result).get("confirmed") is True
    if tool_id == "mercado_livre_listing":
        return _marketplace_listing_stock_contract(result).get("confirmed") is True
    if tool_id == "stock_data":
        return _local_stock_contract(result).get("confirmed") is True
    return False


def _function_manager_execute_tools(
    pending: dict[str, Any],
    plan: dict[str, Any],
    query_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)][:6]
    stores = [str(item or "").strip() for item in list(query_policy.get("stores") or []) if str(item or "").strip()]
    if not stores and str(query_policy.get("store") or "").strip():
        stores = [str(query_policy.get("store") or "").strip()]
    stores = stores or [""]
    expanded: list[tuple[int, dict[str, Any], str]] = []
    for index, call in enumerate(calls):
        for store in stores:
            expanded.append((index, call, store))
    if not expanded:
        return []

    def execute(entry: tuple[int, dict[str, Any], str]) -> tuple[int, dict[str, Any]]:
        index, call, store = entry
        args = dict(call.get("arguments") or {}) if isinstance(call.get("arguments"), dict) else {}
        if store:
            args["loja"] = store
        args.setdefault("mode", "chat")
        args.setdefault("limite", 20)
        args.setdefault("force_refresh", True)
        try:
            raw = codex_assistant.codex_assistant_execute_tool_call(
                client_id=str(pending.get("client_id") or "default"),
                tool_id=str(call.get("tool_id") or ""),
                args=args,
                screen_context=pending.get("screen_context") if isinstance(pending.get("screen_context"), dict) else {},
                previous_results=[],
                permissions=pending.get("session_permissions") if isinstance(pending.get("session_permissions"), dict) else {},
                audit_user=str(pending.get("username") or "whatsapp"),
                query_deadline=time.monotonic() + 60,
            )
            value = _function_manager_compact_result(raw)
        except Exception as exc:
            value = {
                "tool_id": str(call.get("tool_id") or ""),
                "success": False,
                "error": str(exc)[:1000],
                "tool_validation": {"dados_suficientes": False, "motivo": str(exc)[:1000]},
            }
        value.setdefault("tool_id", str(call.get("tool_id") or ""))
        value["manager_call_index"] = index
        value["manager_required"] = call.get("required") is not False
        value["manager_store"] = store
        return index, value

    started = time.monotonic()
    manager_guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    staged_stock = bool(
        manager_guard.get("explicit_stock") is True
        and not any(str(item.get("tool_id") or "") == "mercado_livre_full_stock" for item in calls)
    )
    if staged_stock:
        priority = {"bling_stock_balances": 1, "mercado_livre_listing": 2, "stock_data": 3}
        stock_calls = sorted(
            [item for item in calls if str(item.get("tool_id") or "") in priority],
            key=lambda item: priority[str(item.get("tool_id") or "")],
        )

        def execute_store(store_entry: tuple[int, str]) -> tuple[int, list[dict[str, Any]]]:
            store_index, store = store_entry
            store_results: list[dict[str, Any]] = []
            for call in stock_calls:
                _index, value = execute((priority[str(call.get("tool_id") or "")] - 1, call, store))
                value["manager_required"] = False
                store_results.append(value)
                confirmed = _stock_tool_result_confirmed(value)
                if str(value.get("tool_id") or "") == "stock_data" and confirmed:
                    value["local_stock"] = _local_stock_contract(value)
                    if store:
                        # The current local cadastro is an aggregate and is not
                        # evidence of a balance for a named commercial account.
                        value["dados_suficientes"] = False
                        value["coverage_complete"] = False
                        value["error_class"] = "scope_incomplete"
                        value["stock_supporting_only"] = True
                if confirmed:
                    break
            if store_results:
                # One conclusive source is required for every requested store.
                # Earlier failed priorities remain diagnostic, not blockers.
                store_results[-1]["manager_required"] = True
            return store_index, store_results

        grouped: list[tuple[int, list[dict[str, Any]]]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(6, len(stores)),
            thread_name_prefix="jk-wa-stock-priority",
        ) as executor:
            futures = [executor.submit(execute_store, item) for item in enumerate(stores)]
            for future in concurrent.futures.as_completed(futures):
                grouped.append(future.result())
        grouped.sort(key=lambda item: item[0])
        _record_latency("manager_tools_duration", time.monotonic() - started)
        return [result for _store_index, store_results in grouped for result in store_results]

    output: list[tuple[int, dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(expanded)), thread_name_prefix="jk-wa-manager-tools") as executor:
        futures = [executor.submit(execute, item) for item in expanded]
        for future in concurrent.futures.as_completed(futures):
            output.append(future.result())
    _record_latency("manager_tools_duration", time.monotonic() - started)
    output.sort(key=lambda item: item[0])
    return [item[1] for item in output]


def _function_manager_evidence(plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    facts: list[str] = []
    sources: list[str] = []
    validations: list[dict[str, Any]] = []
    failures: list[str] = []
    required_ok = True
    sufficient_count = 0
    for result in results:
        validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
        sufficient = (
            result.get("dados_suficientes") is True
            if "dados_suficientes" in result
            else validation.get("dados_suficientes") is True
        )
        if sufficient:
            sufficient_count += 1
        if result.get("manager_required") is True and not sufficient:
            required_ok = False
        stock_balance = result.get("stock_balance") if isinstance(result.get("stock_balance"), dict) else {}
        validations.append(
            {
                "tool_id": str(result.get("tool_id") or ""),
                "required": result.get("manager_required") is True,
                "dados_suficientes": sufficient,
                "coverage_complete": result.get("coverage_complete") is True,
                "error_class": str(result.get("error_class") or "")[:100],
                "retryable": result.get("retryable") is True,
                "store": str(result.get("manager_store") or "")[:160],
                "motivo": str(stock_balance.get("reason") or validation.get("motivo") or result.get("empty_reason") or result.get("error") or "")[:1000],
            }
        )
        source_values = [
            result.get("source_label"),
            result.get("source"),
            str(result.get("tool_id") or "") if sufficient else "",
            *(result.get("sources_human") or [] if isinstance(result.get("sources_human"), list) else []),
        ]
        for value in source_values:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text[:1000])
        if result.get("error") or result.get("success") is False:
            failures.append(str(result.get("error") or result.get("empty_reason") or "falha_na_consulta")[:1000])
        payload = {key: result.get(key) for key in ("tool_id", "records", "summary", "data", "rows", "result") if result.get(key) not in (None, "", [], {})}
        if payload:
            facts.append(json.dumps(payload, ensure_ascii=False, default=str)[:6000])
    has_required = any(item.get("required") is True for item in validations)
    sufficient = bool(results and required_ok and (has_required or sufficient_count > 0))
    return {
        "status": "completed" if sufficient else "partial",
        "summary": "Dados internos coletados pelo Luna Gerenciador." if results else "Nenhuma funcao interna aplicavel retornou dados.",
        "verified_facts": facts[:30],
        "sources": sources[:30],
        "confidence": "high" if sufficient else ("medium" if sufficient_count else "low"),
        "evidence_sufficient": sufficient,
        "answerable": sufficient_count > 0,
        "coverage_complete": sufficient,
        "missing": [item["motivo"] for item in validations if item.get("required") and not item.get("dados_suficientes") and item.get("motivo")][:20],
        "questions": [],
        "data_requests": [],
        "validations": validations,
        "failures": failures[:20],
        "tool_results": results,
        "plan": plan,
    }


def _function_manager_merge_evidence(previous: Any, current: dict[str, Any]) -> dict[str, Any]:
    old = previous if isinstance(previous, dict) else {}
    merged = dict(current or {})
    for target, limit, item_limit in (
        ("verified_facts", 30, 6000),
        ("sources", 30, 1000),
        ("missing", 20, 1000),
        ("failures", 20, 1000),
    ):
        merged[target] = _dual_append_unique(
            list(old.get(target) or []),
            merged.get(target),
            limit=limit,
            item_limit=item_limit,
        )
    old_validations = [item for item in list(old.get("validations") or []) if isinstance(item, dict)]
    new_validations = [item for item in list(merged.get("validations") or []) if isinstance(item, dict)]
    by_signature: dict[str, dict[str, Any]] = {}
    for item in [*old_validations, *new_validations]:
        signature = json.dumps(
            {"tool_id": item.get("tool_id"), "required": item.get("required")},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        # Um fallback vazio posterior nunca apaga uma confirmacao anterior.
        prior = by_signature.get(signature)
        if prior and prior.get("dados_suficientes") is True and item.get("dados_suficientes") is not True:
            continue
        by_signature[signature] = dict(item)
    merged["validations"] = list(by_signature.values())[:30]
    if old.get("evidence_sufficient") is True:
        merged["evidence_sufficient"] = True
        merged["coverage_complete"] = bool(old.get("coverage_complete") or merged.get("coverage_complete"))
        merged["status"] = "completed"
        if str(merged.get("confidence") or "") not in {"high", "medium"}:
            merged["confidence"] = str(old.get("confidence") or "high")
    merged["tool_results"] = [
        *[item for item in list(old.get("tool_results") or []) if isinstance(item, dict)],
        *[item for item in list(current.get("tool_results") or []) if isinstance(item, dict)],
    ][-30:]
    return merged


def _configure_function_manager_executor(worker_count: int) -> None:
    global FUNCTION_MANAGER_EXECUTOR, FUNCTION_MANAGER_EXECUTOR_WORKERS
    workers = max(1, min(8, int(worker_count or 4)))
    with FUNCTION_MANAGER_LOCK:
        if FUNCTION_MANAGER_EXECUTOR is not None and FUNCTION_MANAGER_EXECUTOR_WORKERS == workers:
            return
        previous = FUNCTION_MANAGER_EXECUTOR
        FUNCTION_MANAGER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="jk-whatsapp-function-manager",
        )
        FUNCTION_MANAGER_EXECUTOR_WORKERS = workers
    if previous is not None:
        previous.shutdown(wait=False, cancel_futures=False)


def _record_function_manager_diagnostic(
    state: dict[str, Any],
    pending: dict[str, Any],
    *,
    status: str,
    reason: str,
) -> None:
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    evidence = pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {}
    entry = {
        "job_id": str(pending.get("job_group_id") or "")[:100],
        "agent_role": "function_manager",
        "status": str(status or "")[:80],
        "reason": str(reason or plan.get("reason") or "")[:1000],
        "effective_model": str(pending.get("manager_effective_model") or "")[:100],
        "reasoning_effort": str(pending.get("manager_reasoning_effort") or "")[:20],
        "speed": str(pending.get("manager_speed") or "")[:20],
        "service_tier": str(pending.get("manager_service_tier") or "")[:40],
        "planning_duration_ms": int(pending.get("manager_planning_duration_ms") or 0),
        "tools_duration_ms": int(pending.get("manager_tools_duration_ms") or 0),
        "total_duration_ms": int(pending.get("manager_total_duration_ms") or 0),
        "tool_ids": [
            str(item.get("tool_id") or "")[:100]
            for item in list(plan.get("tool_calls") or [])[:6]
            if isinstance(item, dict) and str(item.get("tool_id") or "")
        ],
        "validations": [
            {
                "tool_id": str(item.get("tool_id") or "")[:100],
                "required": item.get("required") is True,
                "dados_suficientes": item.get("dados_suficientes") is True,
            }
            for item in list(evidence.get("validations") or [])[:12]
            if isinstance(item, dict)
        ],
        "requires_sol": plan.get("requires_sol") is True,
        "requires_web": plan.get("requires_web") is True,
        "retry_count": int(pending.get("manager_retry_count") or 0),
        "recorded_at": _now(),
    }
    with DUAL_AGENT_STATE_LOCK, BRIDGE_STATE_LOCK:
        history = [item for item in list(state.get("function_manager_diagnostics") or []) if isinstance(item, dict)]
        history.append(entry)
        state["function_manager_diagnostics"] = history[-100:]
        _save_state(state)


def _create_dual_worker_task(
    config: dict[str, Any],
    *,
    message: dict[str, Any],
    message_id: str,
    subject: str,
    phone: str,
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    job_prompt: str,
    job_title: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    phone_ai_behavior: str,
    query_policy: dict[str, Any],
    job_group_id: str = "",
    subtask_id: str = "",
    subtask_index: int = 1,
    subtask_total: int = 1,
    requires_web: bool = True,
    reasoning_effort: str = "",
    logical_subtask_id: str = "",
    attempt: int = 1,
) -> dict[str, Any]:
    settings = _whatsapp_dual_agent_settings(config)
    effective_task_model = codex_whatsapp_agents.CONVERSATION_RUNTIME.resolve_model(settings["task_agent_model"])
    effective_reasoning = settings["task_agent_reasoning"]
    group_id = str(job_group_id or message_id).strip()
    child_id = str(subtask_id or "main").strip()
    logical_id = str(logical_subtask_id or child_id).strip()
    attempt_number = max(1, int(attempt or 1))
    worker_config = {
        **dict(config),
        "ai_model": f"codex:{effective_task_model}",
        "codex_reasoning_effort": effective_reasoning,
        "codex_reasoning_policy": "fixed",
        "codex_reasoning_max": effective_reasoning,
    }
    worker_message = {**message, "text_body": job_prompt or request_text, "message_type": "text"}
    worker_prompt = codex_whatsapp_agents.worker_output_instruction() + _message_prompt(
        worker_message,
        media,
        transcription,
        mobile_full_access=False,
        query_policy=query_policy,
        ai_behavior=phone_ai_behavior,
        general_answer=False,
    )
    screen_context = _mobile_screen_context(message_id, subject, media, transcription, query_policy)
    result = _create_selected_ai_task(
        worker_config,
        prompt=worker_prompt,
        session=session,
        conversation_id=conversation_id,
        paths=[str(media.get("path"))] if media else [],
        screen_context=screen_context,
        safe_read_only=True,
        mobile_full_access=False,
        channel_metadata={
            "message_id": f"{message_id}:worker:{child_id}",
            "parent_job_id": group_id,
            "job_group_id": group_id,
            "subtask_id": child_id,
            "logical_subtask_id": logical_id,
            "attempt": attempt_number,
            "subtask_index": max(1, int(subtask_index or 1)),
            "subtask_total": max(1, int(subtask_total or 1)),
            "subject_id": subject,
            "wa_id": phone,
            "message_type": str(message.get("message_type") or "text"),
            "media": media or {},
            "transcription": transcription or {},
            "received_at": message.get("received_at"),
            "mobile_full_access": False,
            "query_policy": query_policy,
            "phone_ai_behavior": phone_ai_behavior,
            "general_answer": False,
            "request_text": job_prompt or request_text,
            "agent_role": "task",
            "agent_lane": "worker",
            "handoff_status": "delegated",
            "delivery_state": "worker_running",
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "allow_web_search": bool(requires_web),
            "web_search_requested": bool(requires_web),
            "max_active_task_agents_global": settings["max_active_task_agents_global"],
            "max_active_task_agents_per_conversation": settings["max_active_task_agents_per_conversation"],
            "retry_policy": "bounded",
            "job_deadline_seconds": _job_deadline_seconds(request_text, requires_web=bool(requires_web)),
        },
    )
    task = result.get("task") if isinstance(result, dict) else {}
    if not isinstance(task, dict) or not str(task.get("task_id") or ""):
        raise RuntimeError("task_agent_creation_failed")
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    if (
        str(metadata.get("orchestration_profile") or "") != "whatsapp_dual_codex_worker"
        or str(metadata.get("agent_role") or "") != "task"
        or str(metadata.get("agent_lane") or "") != "worker"
    ):
        metadata = {
            **metadata,
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "agent_role": "task",
            "agent_lane": "worker",
            "allow_web_search": bool(requires_web),
            "web_search_requested": bool(requires_web),
            "job_group_id": group_id,
            "subtask_id": child_id,
            "logical_subtask_id": logical_id,
            "attempt": attempt_number,
        }
        codex_console._codex_update_task(
            str(task.get("task_id") or ""),
            orchestration_profile="whatsapp_dual_codex_worker",
            agent_role="task",
            agent_lane="worker",
            parent_job_id=group_id,
            job_group_id=group_id,
            subtask_id=child_id,
            channel_metadata=metadata,
        )
        task = codex_console._codex_load_task(str(task.get("task_id") or "")) or {**task, "channel_metadata": metadata}
    task_id = str(task.get("task_id") or "")
    codex_console._codex_update_task(
        task_id,
        job_group_id=group_id,
        subtask_id=child_id,
        logical_subtask_id=logical_id,
        current_attempt=attempt_number,
        attempt_task_ids=[task_id],
        retry_count=max(0, attempt_number - 1),
    )
    task = codex_console._codex_load_task(task_id) or task
    return task


def _function_manager_worker_query_policy(pending: dict[str, Any]) -> dict[str, Any]:
    base_policy = pending.get("manager_query_policy") if isinstance(pending.get("manager_query_policy"), dict) else pending.get("query_policy")
    policy = dict(base_policy or {}) if isinstance(base_policy, dict) else {}
    source = dict(policy.get("source_policy") or {}) if isinstance(policy.get("source_policy"), dict) else {}
    catalog_ids = [str(item.get("id") or "") for item in list(pending.get("manager_tool_catalog") or []) if isinstance(item, dict)]
    source.update(
        {
            "required_tools": [],
            "forbidden_tools": list(dict.fromkeys([*list(source.get("forbidden_tools") or []), *catalog_ids])),
            "function_manager_completed": True,
            "internal_tools_reserved_for_manager": True,
        }
    )
    policy["source_policy"] = source
    return policy


def _function_manager_start_sol(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    evidence = pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {}
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    settings = _whatsapp_dual_agent_settings(config)
    proposed = [item for item in list(pending.get("sol_subtasks") or []) if isinstance(item, dict)][: settings["max_subtasks_per_job"]]
    if not proposed:
        proposed = [
            {
                "title": str(pending.get("job_title") or pending.get("request_text") or "Pesquisa")[:180],
                "prompt": str(pending.get("job_prompt") or pending.get("request_text") or "")[:12000],
                "requires_web": bool(plan.get("requires_web")),
            }
        ]
    session = {
        "username": str(pending.get("username") or "").strip().lower(),
        "client_id": str(pending.get("client_id") or "").strip(),
        "permissions": dict(pending.get("session_permissions") or {}),
        "is_full": bool(pending.get("session_is_full")),
    }
    message = {
        "message_id": message_id,
        "subject_id": str(pending.get("subject_id") or ""),
        "wa_id": str(pending.get("wa_id") or ""),
        "message_type": "text",
        "text_body": str(pending.get("request_text") or ""),
        "received_at": pending.get("created_at") or _now(),
    }
    worker_policy = _function_manager_worker_query_policy(pending)
    packet = json.dumps(
        {
            "manager_plan": plan,
            "evidence": {key: evidence.get(key) for key in ("summary", "verified_facts", "sources", "confidence", "validations", "missing", "failures")},
        },
        ensure_ascii=False,
        default=str,
    )[:24000]
    group_id = str(pending.get("job_group_id") or f"wa-{uuid.uuid4().hex[:20]}")
    created: list[dict[str, Any]] = []
    for index, subtask in enumerate(proposed, start=1):
        logical_id = str(subtask.get("logical_subtask_id") or subtask.get("subtask_id") or f"s{index}-{uuid.uuid4().hex[:8]}")
        attempt = max(1, int(subtask.get("current_attempt") or 1))
        child_id = logical_id if attempt <= 1 else f"{logical_id}-a{attempt}"
        base_prompt = str(subtask.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or "").strip()
        worker_prompt = (
            base_prompt
            + "\n\nPACOTE INTERNO JA COLETADO PELO LUNA GERENCIADOR:\n"
            + packet
            + "\n\nUse esse pacote como fonte interna. Nao repita ferramentas internas do JK Sistema. "
            "Pesquise na internet quando autorizado. Se ainda faltar dado interno, retorne data_requests no JSON final."
        )[:36000]
        item = {
            "subtask_id": child_id,
            "logical_subtask_id": logical_id,
            "title": str(subtask.get("title") or pending.get("job_title") or "Pesquisa")[:180],
            "prompt": base_prompt[:12000],
            "requires_web": (
                bool(subtask.get("requires_web"))
                if "requires_web" in subtask
                else bool(plan.get("requires_web"))
            ),
            "reasoning_effort": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            "task_id": "",
            "current_attempt": attempt,
            "attempt_task_ids": list(subtask.get("attempt_task_ids") or []),
            "retry_count": max(0, attempt - 1),
            "next_retry_at_epoch": 0,
            "state": "running",
        }
        try:
            task = _create_dual_worker_task(
                config,
                message=message,
                message_id=message_id,
                subject=str(pending.get("subject_id") or ""),
                phone=str(pending.get("wa_id") or ""),
                session=session,
                conversation_id=str(pending.get("conversation_id") or ""),
                request_text=str(pending.get("request_text") or ""),
                job_prompt=worker_prompt,
                job_title=item["title"],
                media=pending.get("media") if isinstance(pending.get("media"), dict) and pending.get("media") else None,
                transcription=pending.get("transcription") if isinstance(pending.get("transcription"), dict) and pending.get("transcription") else None,
                phone_ai_behavior=str(pending.get("phone_ai_behavior") or ""),
                query_policy=worker_policy,
                job_group_id=group_id,
                subtask_id=child_id,
                logical_subtask_id=logical_id,
                attempt=attempt,
                subtask_index=index,
                subtask_total=len(proposed),
                requires_web=item["requires_web"],
                reasoning_effort=WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            )
            task_id = str(task.get("task_id") or "")
            item["task_id"] = task_id
            item["attempt_task_ids"] = list(dict.fromkeys([*item["attempt_task_ids"], task_id])) if task_id else item["attempt_task_ids"]
        except Exception as exc:
            reason = str(exc)[:1000]
            error_class, retryable = _dual_retry_classification(reason)
            delay = _dual_retry_delay_seconds(1, reason, f"{group_id}:{logical_id}:create") if retryable else 0
            item.update(
                {
                    "state": "waiting_retry" if retryable else "partial",
                    "retry_count": 1,
                    "retry_reason": reason,
                    "next_retry_at_epoch": time.time() + delay if retryable else 0,
                    "next_retry_delay_seconds": delay,
                    "auth_retry": False,
                    "retryable": retryable,
                    "error_class": error_class,
                }
            )
        created.append(item)
    first_id = next((str(item.get("task_id") or "") for item in created if item.get("task_id")), "")
    is_group = len(created) > 1 or not first_id
    first = created[0] if created else {}
    pending.update(
        {
            "task_id": first_id,
            "kind": "dual_job_group" if is_group else "dual_worker",
            "subtasks": created if is_group else [],
            "group_results": {},
            "collected_task_ids": [],
            "delivered_task_ids": [],
            "job_state": "running" if first_id else "waiting_retry",
            "handoff_status": "worker_running",
            "delivery_state": "manager_evidence_delivered_to_sol",
            "query_policy": worker_policy,
            "manager_completed_before_sol": True,
            "manager_completed_at": _now(),
            "retry_count": int(first.get("retry_count") or 0) if not is_group else 0,
            "next_retry_at_epoch": float(first.get("next_retry_at_epoch") or 0) if not is_group else 0,
            "retry_reason": str(first.get("retry_reason") or "") if not is_group else "",
            "attempt_task_ids": list(first.get("attempt_task_ids") or []) if not is_group else [],
            "current_attempt": int(first.get("current_attempt") or 1) if not is_group else 1,
            "subtask_id": str(first.get("subtask_id") or "main") if not is_group else "",
            "logical_subtask_id": str(first.get("logical_subtask_id") or "main") if not is_group else "",
            "prompt": str(first.get("prompt") or pending.get("job_prompt") or "")[:12000] if not is_group else "",
            "title": str(first.get("title") or pending.get("job_title") or "")[:180] if not is_group else "",
            "requires_web": bool(first.get("requires_web", True)) if not is_group else bool(plan.get("requires_web")),
        }
    )
    _save_pending(state, message_id, pending)
    if first_id:
        _record_message_timing(message_id, sol_started_at=_now())
    return bool(first_id)


def _function_manager_deliver_direct(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    evidence = pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {}
    if isinstance(pending.get("deterministic_plan"), dict) and pending.get("deterministic_plan"):
        decision = {}
        final_text = _deterministic_tool_result_text(evidence, pending)
    else:
        try:
            decision = _run_conversation_agent(
                config,
                state,
                str(pending.get("conversation_id") or ""),
                event_type="worker_result",
                user_message=str(pending.get("request_text") or ""),
                active_job={"job_id": str(pending.get("job_group_id") or message_id), "status": "completed", "job_title": str(pending.get("job_title") or "")},
                worker_result=evidence,
                ai_behavior=str(pending.get("phone_ai_behavior") or ""),
                tick_index=int(pending.get("tick_index") or 0),
            )
            final_text = str(decision.get("reply_text") or "").strip()
        except Exception as exc:
            decision = {}
            final_text = _worker_result_fallback_text(evidence, pending)
            RUNTIME_STATE["conversation_fallback_last_error"] = str(exc)[:500]
    request_text = str(pending.get("request_text") or "")
    report_results: list[dict[str, Any]] = []
    if whatsapp_report_files.report_requested(request_text):
        tool_results = [item for item in list(evidence.get("tool_results") or []) if isinstance(item, dict)]
        formats = whatsapp_report_files.requested_report_formats(request_text)
        artifacts: list[dict[str, Any]] = []
        if "png" in formats:
            chart_outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
                base_info_dir=_info_dir(),
                client_id=pending.get("client_id") or "default",
                task_id=pending.get("job_group_id") or message_id,
                prompt=request_text,
                tool_results=tool_results,
                query_policy=pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {},
                max_images=2,
            )
            artifacts.extend(list(chart_outcome.get("artifacts") or []))
        documents = whatsapp_report_files.generate_report_documents(
            base_info_dir=_info_dir(),
            client_id=pending.get("client_id") or "default",
            task_id=pending.get("job_group_id") or message_id,
            prompt=request_text,
            tool_results=tool_results,
            query_policy=pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {},
            formats=formats,
        )
        artifacts.extend(list(documents.get("artifacts") or []))
        if artifacts:
            report_results = _whatsapp_deliver_report_artifacts(
                config,
                message_id,
                artifacts,
                pending.get("client_id") or "default",
                max_images=4,
            )
        metadata_text = _whatsapp_report_metadata_text(
            request_text,
            pending.get("query_policy"),
            tool_results,
        )
        offer = whatsapp_report_files.report_offer_text(request_text)
        final_text = "\n\n".join(item for item in (final_text, metadata_text, offer) if item).strip()
        if artifacts and not all(item.get("success") for item in report_results):
            final_text += "\n\nUm ou mais arquivos nao puderam ser anexados nesta tentativa; o resumo em texto foi preservado."
    delivery = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"manager:{pending.get('job_group_id') or message_id}:{hashlib.sha256(final_text.encode('utf-8')).hexdigest()[:16]}",
            "event_type": "task_completed",
            "severity": "info",
            "text": final_text,
        },
    )
    if str(delivery.get("status") or "") not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["delivery_state"] = f"manager_final_{str(delivery.get('status') or 'failed')}"
        _save_pending(state, message_id, pending)
        return False
    if isinstance(pending.get("deterministic_plan"), dict) and pending.get("deterministic_plan"):
        _dual_remember_conversation_turn(
            state,
            str(pending.get("conversation_id") or ""),
            role="assistant",
            text=final_text,
            event_type="worker_result",
        )
    _record_message_timing(message_id, completed_at=_now(), sent_at=_now())
    coverage_complete = evidence.get("coverage_complete") is True
    _remove_pending(
        state,
        message_id,
        status="completed" if coverage_complete else "partial",
        reason="" if coverage_complete else "cobertura_incompleta",
    )
    return True


def _format_stock_quantity(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "indisponivel"
    if number.is_integer():
        return str(int(number))
    return (f"{number:.3f}").rstrip("0").rstrip(".").replace(".", ",")


def _deterministic_stock_result_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    results = [
        item
        for item in list(evidence.get("tool_results") or [])
        if isinstance(item, dict)
        and str(item.get("tool_id") or "") in {"bling_stock_balances", "mercado_livre_listing", "stock_data"}
    ]
    if not results:
        return ""
    policy = (
        pending.get("manager_query_policy")
        if isinstance(pending.get("manager_query_policy"), dict)
        else pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {}
    )
    requested_stores = [str(item or "").strip() for item in list(policy.get("stores") or []) if str(item or "").strip()]
    if not requested_stores and str(policy.get("store") or "").strip():
        requested_stores = [str(policy.get("store") or "").strip()]
    by_store: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for result in results:
        details = _stock_balance_contract(result) if str(result.get("tool_id") or "") == "bling_stock_balances" else {}
        store = str(result.get("manager_store") or details.get("store") or "Loja consultada").strip()
        key = _whatsapp_text_key(store)
        if key not in by_store:
            by_store[key] = (store, [])
        by_store[key][1].append(result)
    ordered: list[tuple[str, list[dict[str, Any]]]] = []
    used: set[str] = set()
    for store in requested_stores:
        key = _whatsapp_text_key(store)
        value = by_store.get(key)
        if value:
            ordered.append((store, value[1]))
            used.add(key)
    ordered.extend(value for key, value in by_store.items() if key not in used)

    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    sku = str(plan.get("sku") or _function_manager_extract_identifiers(pending.get("request_text"))[0] or "").strip()
    lines: list[str] = []
    confirmed_any = False
    partial_any = False
    for store, store_results in ordered:
        lines.append(f"*{store}*")
        bling_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "bling_stock_balances"),
            {},
        )
        ml_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "mercado_livre_listing"),
            {},
        )
        local_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "stock_data"),
            {},
        )
        bling = _stock_balance_contract(bling_result)
        marketplace = _marketplace_listing_stock_contract(ml_result)
        local = _local_stock_contract(local_result)
        row_sku = str(bling.get("sku") or local.get("sku") or sku or "SKU consultado")
        if bling.get("confirmed") is True:
            confirmed_any = True
            quantity = _format_stock_quantity(bling.get("store_available"))
            lines.append(f"SKU {row_sku}: *{quantity} unidade(s)* no estoque da loja, confirmado diretamente na Bling.")
            if bling.get("full_excluded") is True:
                lines.append("Estoque Full/Fulfillment nao esta incluido nesse saldo.")
        elif marketplace.get("confirmed") is True:
            confirmed_any = True
            lines.append("A Bling nao confirmou o saldo; usei o Mercado Livre como segunda fonte.")
            for row in list(marketplace.get("rows") or [])[:4]:
                label = str(row.get("item_id") or row.get("sku") or row_sku or "anuncio")
                quantity = _format_stock_quantity(row.get("available_quantity"))
                lines.append(f"{label}: *{quantity} unidade(s) disponivel(is)* no anuncio.")
        elif local.get("confirmed") is True:
            partial_any = True
            quantity = _format_stock_quantity(local.get("store_available"))
            lines.append(
                f"Bling e Mercado Livre nao confirmaram o saldo desta loja. O JK Sistema registra *{quantity} "
                "unidade(s)* de saldo interno agregado para o SKU."
            )
            lines.append("Esse cadastro interno nao separa com seguranca o saldo por conta/loja; por isso a cobertura permanece parcial.")
        else:
            partial_any = True
            if bling.get("auth_failed") is True:
                lines.append(f"SKU {row_sku}: saldo nao confirmado. A conexao da Bling desta loja esta expirada e precisa ser refeita.")
            else:
                lines.append(f"SKU {row_sku}: saldo nao confirmado nas fontes disponiveis.")
            if bling.get("fallback_identified") is True:
                lines.append("O SKU foi localizado no cadastro interno, mas esse retorno nao comprova o saldo desta loja.")
        lines.append("")
    attempted_tools = {str(item.get("tool_id") or "") for item in results}
    attempted_sources = [
        label
        for tool_id, label in (
            ("bling_stock_balances", "Bling"),
            ("mercado_livre_listing", "Mercado Livre"),
            ("stock_data", "JK Sistema"),
        )
        if tool_id in attempted_tools
    ]
    if attempted_sources:
        lines.append("Fontes consultadas, em ordem de prioridade: " + "; ".join(attempted_sources) + ".")
    if partial_any:
        lines.append("Cobertura parcial: lojas sem saldo confiavel foram mantidas como nao confirmadas, nunca como estoque zero.")
    return "\n".join(lines).strip()[:3500]


def _deterministic_tool_result_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    if guard.get("explicit_stock") is True:
        stock_text = _deterministic_stock_result_text(evidence, pending)
        if stock_text:
            return stock_text
    results = [item for item in list(evidence.get("tool_results") or []) if isinstance(item, dict)]
    by_store: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_store.setdefault(str(result.get("manager_store") or "").strip(), []).append(result)
    lines: list[str] = []
    for store, items in by_store.items():
        if len(by_store) > 1 or store:
            lines.append(f"*{store or 'Escopo consolidado'}*")
        for result in items:
            label = str(result.get("tool_label") or result.get("tool_id") or "Fonte consultada").replace("_", " ").strip()
            summary = result.get("summary")
            message = re.sub(r"\s+", " ", str(result.get("message") or "")).strip()
            if result.get("dados_suficientes") is not True:
                validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
                reason = str(result.get("empty_reason") or result.get("error") or validation.get("motivo") or "dados insuficientes")
                lines.append(f"{label}: dados insuficientes para confirmar a resposta.")
                lines.append(f"Limitacao: {reason[:500]}.")
                continue
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip()[:1600])
            elif message:
                lines.append(message[:1600])
            else:
                data = result.get("data")
                if isinstance(data, dict):
                    scalar = [
                        f"{str(key).replace('_', ' ')}: {value}"
                        for key, value in data.items()
                        if isinstance(value, (str, int, float, bool)) and str(value).strip()
                    ][:10]
                    lines.append((f"{label}: " + "; ".join(scalar))[:1800] if scalar else f"{label}: dados estruturados confirmados.")
                elif isinstance(data, list):
                    count = int(result.get("records") or len(data))
                    lines.append(f"{label}: {count} registro(s) confirmado(s).")
                else:
                    lines.append(f"{label}: consulta concluida.")
            if result.get("coverage_complete") is not True:
                reason = str(result.get("empty_reason") or result.get("error") or "cobertura parcial")
                lines.append(f"Limitacao: {reason[:500]}.")
        if store:
            lines.append("")
    sources = [str(item or "").strip() for item in list(evidence.get("sources") or []) if str(item or "").strip()]
    if sources:
        lines.append("Fontes: " + "; ".join(sources[:8]) + ".")
    if not lines:
        return _worker_result_fallback_text(evidence, pending)
    return "\n".join(lines).strip()[:3500]


def _whatsapp_report_metadata_text(request_text: Any, query_policy: Any, tool_results: Any) -> str:
    if not whatsapp_report_files.report_requested(request_text):
        return ""
    dataset = whatsapp_report_files.build_report_dataset(query_policy, tool_results)
    limitations = "; ".join(dataset.get("limitations") or []) or "nenhuma informada"
    return (
        f"Periodo: {dataset.get('period_start')} a {dataset.get('period_end')}\n"
        f"Lojas: {', '.join(dataset.get('stores') or []) or 'nao informadas'}\n"
        f"Fontes: {'; '.join(dataset.get('sources') or []) or 'nao informadas'}\n"
        f"Registros: {int(dataset.get('record_count') or 0)}\n"
        f"Cobertura: {'completa' if dataset.get('coverage_complete') else 'incompleta'}\n"
        f"Limitacoes: {limitations}"
    )[:1800]


def _function_manager_retry(pending: dict[str, Any], reason: str) -> None:
    count = max(0, int(pending.get("manager_retry_count") or 0)) + 1
    error_class, retryable = _dual_retry_classification(reason)
    if not retryable or count > WHATSAPP_MAX_RETRY_ATTEMPTS:
        pending.update(
            {
                "manager_state": "partial",
                "job_state": "partial",
                "manager_retry_count": count,
                "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
                "manager_next_retry_at_epoch": 0,
                "next_retry_at_epoch": 0,
                "retry_policy": "bounded",
                "terminal_reason": str(reason or "limite_de_tentativas_atingido")[:1000],
                "terminal_error_class": error_class,
            }
        )
        return
    delay = _dual_retry_delay_seconds(count, reason, str(pending.get("job_group_id") or "manager"))
    pending.update(
        {
            "manager_state": "waiting_retry",
            "job_state": "waiting_retry",
            "manager_retry_count": count,
            "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
            "manager_next_retry_at_epoch": time.time() + delay,
            "next_retry_at_epoch": time.time() + delay,
            "retry_policy": "bounded",
        }
    )


def _function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> None:
    started = time.monotonic()
    try:
        with BRIDGE_STATE_LOCK:
            current = (state.get("pending_messages") or {}).get(message_id) if isinstance(state.get("pending_messages"), dict) else None
            pending = dict(current) if isinstance(current, dict) else {}
        if not pending or str(pending.get("kind") or "") != "dual_function_manager":
            return
        run_revision = max(0, int(pending.get("manager_revision") or 0))
        pending.update({"manager_state": "running", "job_state": "manager_running", "manager_started_at": _now()})
        _save_pending(state, message_id, pending)
        settings = _whatsapp_dual_agent_settings(config)
        catalog = _function_manager_catalog(pending.get("session_permissions"))
        pending["manager_tool_catalog"] = catalog
        manager_policy = (
            pending.get("manager_query_policy")
            if isinstance(pending.get("manager_query_policy"), dict)
            else pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {}
        )
        planning_started = time.monotonic()
        deterministic_plan = pending.get("deterministic_plan") if isinstance(pending.get("deterministic_plan"), dict) else {}
        if deterministic_plan:
            raw_plan = dict(deterministic_plan)
            raw_plan.update(
                {
                    "thread_id": "",
                    "effective_model": "deterministic-router",
                    "reasoning_effort": "none",
                    "speed": "direct",
                    "service_tier": "local",
                }
            )
        else:
            raw_plan = codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.run_manager(
                thread_id=str(pending.get("function_manager_thread_id") or ""),
                model=settings["conversation_agent_model"],
                reasoning_effort=settings["conversation_agent_reasoning"],
                speed=settings["conversation_agent_speed"],
                service_tier=settings["conversation_agent_service_tier"],
                request_text=str(pending.get("request_text") or ""),
                job_prompt=str(pending.get("job_prompt") or pending.get("request_text") or ""),
                query_policy=manager_policy,
                tool_catalog=catalog,
                previous_evidence=pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {},
                data_requests=pending.get("manager_data_requests") if isinstance(pending.get("manager_data_requests"), list) else [],
                max_calls=settings["max_subtasks_per_job"],
            )
        planning_duration_ms = int(round((time.monotonic() - planning_started) * 1000))
        plan = _function_manager_enforce_plan(
            raw_plan,
            request_text=str(pending.get("request_text") or ""),
            query_policy=manager_policy,
            catalog=catalog,
            max_calls=settings["max_subtasks_per_job"],
        )
        with BRIDGE_STATE_LOCK:
            latest = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(latest, dict) or int(latest.get("manager_revision") or 0) != run_revision:
            return
        pending.update(
            {
                "function_manager_thread_id": str(raw_plan.get("thread_id") or "")[:200],
                "manager_effective_model": str(raw_plan.get("effective_model") or "")[:100],
                "manager_reasoning_effort": str(raw_plan.get("reasoning_effort") or "")[:20],
                "manager_speed": str(raw_plan.get("speed") or "")[:20],
                "manager_service_tier": str(raw_plan.get("service_tier") or "")[:40],
                "manager_plan": plan,
                "manager_planned_at": _now(),
                "manager_planning_duration_ms": planning_duration_ms,
            }
        )
        if list(plan.get("missing_user_fields") or []):
            evidence = {
                "status": "blocked",
                "summary": "Faltam dados do usuario para executar as consultas internas.",
                "verified_facts": [],
                "sources": [],
                "confidence": "low",
                "evidence_sufficient": False,
                "coverage_complete": False,
                "missing": list(plan.get("missing_user_fields") or []),
                "questions": list(plan.get("missing_user_fields") or [])[:2],
                "data_requests": [],
            }
            pending.update({"manager_evidence": evidence, "manager_state": "awaiting_input", "job_state": "awaiting_input"})
            pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
            _save_pending(state, message_id, pending)
            _record_function_manager_diagnostic(
                state,
                pending,
                status="awaiting_input",
                reason="missing_user_fields",
            )
            questions = [str(item or "").strip() for item in list(evidence.get("questions") or []) if str(item or "").strip()]
            prompt = "Preciso desta informação para continuar: " + (questions[0] if questions else "informe os dados que faltam no pedido.")
            delivery = _post_proactive(
                config,
                {
                    "subject_id": str(pending.get("subject_id") or ""),
                    "fingerprint": f"manager:{pending.get('job_group_id')}:input",
                    "event_type": "task_partial",
                    "severity": "info",
                    "text": prompt[:3500],
                },
            )
            pending["awaiting_notified"] = str(delivery.get("status") or "") in {
                "sent",
                "queued",
                "duplicate",
                "waiting_free_window",
            }
            pending["delivery_state"] = f"awaiting_input_{str(delivery.get('status') or 'failed')}"
            _save_pending(state, message_id, pending)
            return
        tools_started = time.monotonic()
        results = _function_manager_execute_tools(pending, plan, manager_policy)
        pending["manager_tools_duration_ms"] = int(round((time.monotonic() - tools_started) * 1000))
        evidence = _function_manager_merge_evidence(
            pending.get("manager_evidence"),
            _function_manager_evidence(plan, results),
        )
        with BRIDGE_STATE_LOCK:
            latest = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(latest, dict) or int(latest.get("manager_revision") or 0) != run_revision:
            return
        pending.update(
            {
                "manager_evidence": evidence,
                "manager_state": "completed" if evidence.get("evidence_sufficient") else "partial",
                "manager_completed_at": _now(),
                "verified_facts": list(evidence.get("verified_facts") or []),
                "verified_sources": list(evidence.get("sources") or []),
                "manager_total_duration_ms": int(round((time.monotonic() - started) * 1000)),
            }
        )
        _save_pending(state, message_id, pending)
        with BRIDGE_STATE_LOCK:
            still_pending = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(still_pending, dict) or still_pending.get("cancel_requested") is True:
            return
        required_retryable = any(
            isinstance(item, dict)
            and item.get("required") is True
            and item.get("dados_suficientes") is not True
            and item.get("retryable") is True
            for item in list(evidence.get("validations") or [])
        )
        deterministic_terminal = bool(
            pending.get("deterministic_plan")
            and results
            and not required_retryable
            and not plan.get("requires_sol")
            and not plan.get("requires_web")
        )
        if (
            evidence.get("evidence_sufficient") is True
            or deterministic_terminal
        ) and not plan.get("requires_sol") and not plan.get("requires_web"):
            _record_function_manager_diagnostic(
                state,
                pending,
                status="completed_without_sol" if evidence.get("evidence_sufficient") is True else "partial_without_sol",
                reason="internal_evidence_sufficient" if evidence.get("evidence_sufficient") is True else "non_retryable_partial_evidence",
            )
            _function_manager_deliver_direct(config, state, message_id, pending)
            return
        if plan.get("requires_sol") or plan.get("requires_web"):
            _record_function_manager_diagnostic(
                state,
                pending,
                status="handed_to_sol",
                reason="technical_or_external_analysis_required",
            )
            if _function_manager_start_sol(config, state, message_id, pending):
                return
            _function_manager_retry(pending, "sol_creation_failed")
            _save_pending(state, message_id, pending)
            return
        _function_manager_retry(pending, "evidencia_interna_insuficiente")
        _record_function_manager_diagnostic(
            state,
            pending,
            status="waiting_retry",
            reason="evidencia_interna_insuficiente",
        )
        _save_pending(state, message_id, pending)
    except Exception as exc:
        with BRIDGE_STATE_LOCK:
            current = (state.get("pending_messages") or {}).get(message_id) if isinstance(state.get("pending_messages"), dict) else None
            pending = dict(current) if isinstance(current, dict) else {}
        if pending:
            _function_manager_retry(pending, str(exc)[:1000])
            pending["manager_last_error"] = str(exc)[:1000]
            pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
            _record_function_manager_diagnostic(
                state,
                pending,
                status="waiting_retry",
                reason=str(exc)[:1000],
            )
            _save_pending(state, message_id, pending)
        RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]
    finally:
        _record_latency("manager_duration", time.monotonic() - started)


def _function_manager_future_done(message_id: str, future: concurrent.futures.Future[Any]) -> None:
    with FUNCTION_MANAGER_LOCK:
        if FUNCTION_MANAGER_FUTURES.get(message_id) is future:
            FUNCTION_MANAGER_FUTURES.pop(message_id, None)


def _submit_function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> bool:
    settings = _whatsapp_dual_agent_settings(config)
    _configure_function_manager_executor(settings["function_manager_worker_count"])
    with FUNCTION_MANAGER_LOCK:
        current = FUNCTION_MANAGER_FUTURES.get(message_id)
        if current is not None and not current.done():
            return False
        assert FUNCTION_MANAGER_EXECUTOR is not None
        future = FUNCTION_MANAGER_EXECUTOR.submit(_function_manager_job, dict(config), state, message_id)
        FUNCTION_MANAGER_FUTURES[message_id] = future
        future.add_done_callback(lambda completed, mid=message_id: _function_manager_future_done(mid, completed))
    return True


def _dual_retry_pending_due(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> int:
    changed = _dual_migrate_pending_v7(pending)
    now_epoch = time.time()
    kind = str(pending.get("kind") or "")
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    created = 0
    for index, holder in enumerate(holders, start=1):
        if str(holder.get("state") or "") != "waiting_retry":
            continue
        if float(holder.get("next_retry_at_epoch") or 0) > now_epoch:
            continue
        holder["state"] = "retry_starting"
        holder["retry_started_at"] = _now()
        changed = True
        _save_pending(state, message_id, pending)
        old_task_id = str(holder.get("task_id") or "")
        logical_id = str(holder.get("logical_subtask_id") or holder.get("subtask_id") or ("main" if kind != "dual_job_group" else f"s{index}"))
        attempt = max(1, int(holder.get("current_attempt") or len(list(holder.get("attempt_task_ids") or [])) or 1)) + 1
        child_id = f"{logical_id}-a{attempt}"
        session = {
            "username": str(pending.get("username") or "").strip().lower(),
            "client_id": str(pending.get("client_id") or "").strip(),
            "permissions": dict(pending.get("session_permissions") or {}),
            "is_full": bool(pending.get("session_is_full")),
        }
        message = {
            "message_id": message_id,
            "subject_id": str(pending.get("subject_id") or ""),
            "wa_id": str(pending.get("wa_id") or ""),
            "message_type": "text",
            "text_body": str(pending.get("request_text") or ""),
            "received_at": pending.get("created_at") or _now(),
        }
        try:
            task = _create_dual_worker_task(
                config,
                message=message,
                message_id=message_id,
                subject=str(pending.get("subject_id") or ""),
                phone=str(pending.get("wa_id") or ""),
                session=session,
                conversation_id=str(pending.get("conversation_id") or ""),
                request_text=str(pending.get("request_text") or ""),
                job_prompt=str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or ""),
                job_title=str(holder.get("title") or pending.get("job_title") or "")[:180],
                media=pending.get("media") if isinstance(pending.get("media"), dict) and pending.get("media") else None,
                transcription=(
                    pending.get("transcription")
                    if isinstance(pending.get("transcription"), dict) and pending.get("transcription")
                    else None
                ),
                phone_ai_behavior=str(pending.get("phone_ai_behavior") or ""),
                query_policy=dict(pending.get("query_policy") or {}),
                job_group_id=str(pending.get("job_group_id") or message_id),
                subtask_id=child_id,
                logical_subtask_id=logical_id,
                attempt=attempt,
                subtask_index=index,
                subtask_total=max(1, len(holders)),
                requires_web=bool(holder.get("requires_web", True)),
                reasoning_effort=WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            )
        except Exception as exc:
            failure = {
                "status": "failed",
                "summary": "",
                "verified_facts": [],
                "sources": [],
                "missing": [str(exc)[:1000]],
                "questions": [],
            }
            _dual_schedule_retry(
                pending,
                holder,
                {"task_id": old_task_id, "status": "failed", "error": str(exc)[:1000], "completed_at": _now()},
                failure,
                key=f"{pending.get('job_group_id')}:{logical_id}:create",
            )
            pending["last_retry_creation_error"] = str(exc)[:1000]
            changed = True
            continue
        task_id = str(task.get("task_id") or "")
        history = [str(item or "") for item in list(holder.get("attempt_task_ids") or []) if str(item or "")]
        if old_task_id and old_task_id not in history:
            history.append(old_task_id)
        if task_id and task_id not in history:
            history.append(task_id)
        holder.update(
            {
                "task_id": task_id,
                "logical_subtask_id": logical_id,
                "state": "running",
                "current_attempt": attempt,
                "retry_count": max(int(holder.get("retry_count") or 0), attempt - 1),
                "attempt_task_ids": history[-50:],
                "next_retry_at_epoch": 0,
                "next_retry_delay_seconds": 0,
                "retry_started_at": "",
                "last_attempt_started_at": _now(),
            }
        )
        if kind != "dual_job_group":
            pending["task_id"] = task_id
        else:
            results = pending.get("group_results") if isinstance(pending.get("group_results"), dict) else {}
            results.pop(old_task_id, None)
            pending["group_results"] = results
            pending["collected_task_ids"] = [
                str(item or "")
                for item in list(pending.get("collected_task_ids") or [])
                if str(item or "") != old_task_id
            ]
        pending.update(
            {
                "task_id": str(pending.get("task_id") or task_id),
                "job_state": "running",
                "handoff_status": "worker_retry_running",
                "delivery_state": "worker_retry_running",
                "last_retry_started_at": _now(),
            }
        )
        codex_console._codex_update_task(
            task_id,
            retry_root_job_id=str(pending.get("job_group_id") or message_id),
            retry_count=int(holder.get("retry_count") or 0),
            current_attempt=attempt,
            attempt_task_ids=history[-50:],
        )
        created += 1
        changed = True
    if changed:
        _save_pending(state, message_id, pending)
    return created


def _whatsapp_is_retry_command(value: Any) -> bool:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return bool(re.search(r"\b(tente|tentar|continue|continuar|retome|retomar|de novo|novamente)\b", text))


def _resume_dual_pending_with_message(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    user_message: str,
) -> int:
    kind = str(pending.get("kind") or "")
    if kind == "dual_function_manager":
        original = str(pending.get("job_prompt") or pending.get("request_text") or "").strip()
        pending.update(
            {
                "job_prompt": (original + "\n\nInformacao adicional do usuario: " + str(user_message or "").strip())[-12000:],
                "manager_state": "queued",
                "job_state": "manager_queued",
                "manager_next_retry_at_epoch": 0,
                "manager_data_requests": [],
                "manager_revision": max(0, int(pending.get("manager_revision") or 0)) + 1,
                "pending_questions": [],
                "last_user_resume_at": _now(),
                "last_conversation_at": _now(),
                "last_conversation_at_epoch": time.time(),
            }
        )
        _save_pending(state, message_id, pending)
        _submit_function_manager_job(config, state, message_id)
        return 1
    holders = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)] if kind == "dual_job_group" else [pending]
    resumed = 0
    for holder in holders:
        if str(holder.get("state") or "") not in {"waiting_retry", "awaiting_input", "retry_starting"}:
            continue
        if str(holder.get("state") or "") == "awaiting_input" and str(user_message or "").strip():
            original = str(holder.get("prompt") or pending.get("job_prompt") or "").strip()
            holder["prompt"] = (original + "\n\nInformacao adicional do usuario: " + str(user_message).strip())[-12000:]
        holder.update(
            {
                "state": "waiting_retry",
                "next_retry_at_epoch": 0,
                "retry_reason": "user_resume",
                "auth_retry": False,
            }
        )
        resumed += 1
    if resumed:
        pending.update(
            {
                "job_state": "waiting_retry",
                "pending_questions": [],
                "last_user_resume_at": _now(),
                "last_conversation_at": _now(),
                "last_conversation_at_epoch": time.time(),
            }
        )
        _save_pending(state, message_id, pending)
        _dual_retry_pending_due(config, state, message_id, pending)
    return resumed


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
) -> bool:
    active_message_id, active_pending, active_task, active_snapshot = _dual_active_job_snapshot(
        state,
        conversation_id,
        exclude_message_id=message_id,
    )
    with DUAL_AGENT_STATE_LOCK:
        record = _dual_conversation_record(state, conversation_id)
        record["last_inbound_at"] = _now()
        record["last_inbound_at_epoch"] = time.time()
        _dual_append_conversation_turn(record, role="user", text=request_text, event_type="user_message")
        _save_dual_conversation_record(state, conversation_id, record)
    precomputed_query_policy: dict[str, Any] = {}
    deterministic_plan: dict[str, Any] = {}
    if not active_task:
        precomputed_query_policy = _dual_delegate_query_policy(request_text, session, state, conversation_id)
        missing_store = bool(
            precomputed_query_policy.get("store_required")
            and precomputed_query_policy.get("store_mode") != "all"
            and len(precomputed_query_policy.get("store_matches") or []) != 1
        )
        if missing_store:
            stores = list(precomputed_query_policy.get("authorized_stores") or [])
            if _whatsapp_send_store_selection(
                config,
                state,
                message,
                session,
                conversation_id,
                request_text,
                stores,
                allow_all=True,
            ):
                return True
            raise RuntimeError("store_selection_delivery_failed")
        deterministic_plan = _deterministic_direct_query_plan(
            request_text,
            precomputed_query_policy,
            session.get("permissions"),
        )
    if deterministic_plan:
        tool_call = (deterministic_plan.get("tool_calls") or [{}])[0]
        decision = {
            "action": "delegate",
            "reply_text": "Vou consultar os dados confirmados e retorno assim que a fonte responder.",
            "job_prompt": request_text,
            "job_title": str(tool_call.get("reason") or "Consulta direta")[:180],
            "subtasks": [],
            "requires_web": False,
            "thread_id": "",
            "deterministic_route": True,
        }
    else:
        decision = _run_conversation_agent(
            config,
            state,
            conversation_id,
            event_type="user_message",
            user_message=request_text,
            active_job=active_snapshot,
            ai_behavior=phone_ai_behavior,
        )
    action = str(decision.get("action") or "")
    reply_text = str(decision.get("reply_text") or "").strip()
    related_job_id = str(decision.get("related_job_id") or "").strip()
    active_task_id = str(active_task.get("task_id") or "").strip() if active_task else ""
    if active_task and _whatsapp_is_job_status_probe(request_text):
        # Perguntas de estado pertencem ao Luna. Elas nunca abrem, alteram ou
        # enfileiram outro trabalho Sol, mesmo que uma decisao imperfeita tente
        # delega-las.
        action = "reply"
    if active_task and action in {"delegate", "queue"} and (
        _whatsapp_is_task_complement(request_text)
        or (related_job_id and related_job_id == active_task_id)
    ):
        # Uma correcao/complemento nunca abre outro Sol. Mesmo que o Luna use
        # delegate/queue por engano, o bridge preserva a tarefa ativa.
        action = "steer"
    elif active_task and action == "delegate":
        # Com um Sol ativo, uma pesquisa realmente independente deve aguardar
        # na mesma fila em vez de parecer outra execucao concorrente.
        action = "queue"
    if action == "cancel_job":
        if active_task:
            for task_id in _pending_task_ids(active_pending):
                codex_console.codex_cancelar_tarefa_para_sessao(
                    task_id,
                    session,
                    cancel_source="whatsapp_conversation_agent",
                )
                codex_console._codex_update_task(
                    task_id,
                    handoff_status="canceled_by_conversation_agent",
                    delivery_state="canceled",
                )
            if active_message_id:
                _remove_pending(state, active_message_id, status="canceled", reason="cancelado_pelo_usuario")
        _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        return True
    active_job_state = str(active_pending.get("job_state") or "") if active_pending else ""
    should_resume_waiting = bool(
        active_pending
        and not _whatsapp_is_job_status_probe(request_text)
        and (
            active_job_state == "awaiting_input"
            or (active_job_state in {"waiting_retry", "retry_starting"} and _whatsapp_is_retry_command(request_text))
        )
    )
    if should_resume_waiting and active_message_id:
        resumed = _resume_dual_pending_with_message(
            config,
            state,
            active_message_id,
            active_pending,
            request_text,
        )
        if resumed:
            _post_message_result(
                config,
                message_id,
                {"status": "completed", "task_id": active_task_id, "response": reply_text},
            )
            return True
    if action == "steer" and active_task:
        if str(active_pending.get("kind") or "") == "dual_function_manager" and active_message_id:
            _resume_dual_pending_with_message(
                config,
                state,
                active_message_id,
                active_pending,
                str(decision.get("job_prompt") or request_text),
            )
            _post_message_result(
                config,
                message_id,
                {"status": "completed", "task_id": active_task_id, "response": reply_text},
            )
            return True
        accepted = False
        for task_id in _pending_task_ids(active_pending):
            task = (
                active_task
                if task_id == str(active_task.get("task_id") or "")
                else (codex_console._codex_load_task(task_id) or {})
            )
            if str(task.get("status") or "") not in {"queued", "running"}:
                continue
            steer = codex_console.codex_complementar_tarefa_para_sessao(
                task_id,
                str(decision.get("job_prompt") or request_text),
                session,
                request_id=f"{message_id}:{task_id}",
                subject_id=subject,
                wa_id=phone,
            )
            accepted = steer.get("accepted") is True or accepted
        if accepted:
            _post_message_result(
                config,
                message_id,
                {"status": "completed", "task_id": active_task_id, "response": reply_text},
            )
            return True
        action = "queue"
    elif action == "steer":
        action = "delegate"
    if action in {"reply", "request_information"}:
        _post_message_result(config, message_id, {"status": "completed", "response": reply_text})
        return True
    if action not in {"delegate", "queue"}:
        raise RuntimeError("conversation_agent_unhandled_action")

    job_prompt = str(decision.get("job_prompt") or request_text).strip()
    job_title = str(decision.get("job_title") or request_text).strip()[:180]
    # O texto original governa o escopo. O detalhamento produzido pelo Luna
    # Conversa nao pode acrescentar vendas, pedidos ou estoque que o usuario
    # nao solicitou.
    query_policy = precomputed_query_policy or _dual_delegate_query_policy(request_text, session, state, conversation_id)
    if not query_policy:
        query_policy = _dual_delegate_query_policy(job_prompt, session, state, conversation_id)
    missing_store = bool(
        query_policy.get("store_required")
        and query_policy.get("store_mode") != "all"
        and len(query_policy.get("store_matches") or []) != 1
    )
    if missing_store:
        stores = list(query_policy.get("authorized_stores") or [])
        if _whatsapp_send_store_selection(
            config,
            state,
            message,
            session,
            conversation_id,
            request_text,
            stores,
            allow_all=True,
        ):
            return True
        raise RuntimeError("store_selection_delivery_failed")

    settings = _whatsapp_dual_agent_settings(config)
    if settings.get("function_manager_enabled") and settings.get("function_manager_required_before_sol"):
        job_group_id = f"wa-{uuid.uuid4().hex[:20]}"
        now_epoch = time.time()
        pending = {
            "task_id": "",
            "kind": "dual_function_manager",
            "conversation_id": conversation_id,
            "conversation_agent_thread_id": str(decision.get("thread_id") or ""),
            "function_manager_thread_id": "",
            "parent_job_id": job_group_id,
            "job_group_id": job_group_id,
            "job_title": job_title,
            "subject_id": subject,
            "username": str(session.get("username") or "").strip().lower(),
            "client_id": str(session.get("client_id") or "").strip(),
            "request_text": request_text,
            "job_prompt": job_prompt,
            "query_policy": query_policy,
            "manager_query_policy": query_policy,
            "deterministic_plan": deterministic_plan,
            "phone_ai_behavior": phone_ai_behavior,
            "media": media or {},
            "transcription": transcription or {},
            "screen_context": _mobile_screen_context(message_id, subject, media, transcription, query_policy),
            "session_permissions": {
                str(key): value is True
                for key, value in dict(session.get("permissions") or {}).items()
                if str(key or "").strip()
            },
            "session_is_full": bool(session.get("is_full")),
            "created_at": _now(),
            "created_at_epoch": now_epoch,
            "job_state": "manager_queued",
            "manager_state": "queued",
            "manager_retry_count": 0,
            "manager_revision": 0,
            "manager_next_retry_at_epoch": 0,
            "retry_policy": "bounded",
            "sol_subtasks": list(decision.get("subtasks") or [])[: settings["max_subtasks_per_job"]],
            "verified_facts": [],
            "verified_sources": [],
            "last_conversation_at": _now(),
            "last_conversation_at_epoch": now_epoch,
            "tick_index": 0,
            "wait_notice_count": 0,
            "awaiting_notified": True,
            "handoff_status": "manager_queued",
            "delivery_state": "initial_reply_sent",
            "wa_id": phone,
        }
        _save_pending(state, message_id, pending)
        _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
        _post_message_result(
            config,
            message_id,
            {
                "status": "completed",
                "task_id": "",
                "job_group_id": job_group_id,
                "subtask_count": 0,
                "response": reply_text,
            },
        )
        _submit_function_manager_job(config, state, message_id)
        return True
    proposed_subtasks = list(decision.get("subtasks") or [])[: settings["max_subtasks_per_job"]]
    if len(proposed_subtasks) <= 1:
        proposed_subtasks = [
            proposed_subtasks[0]
            if proposed_subtasks
            else {
                "title": job_title,
                "prompt": job_prompt,
                "requires_web": bool(decision.get("requires_web")),
                "reasoning_effort": settings["task_agent_reasoning"],
            }
        ]
    job_group_id = f"wa-{uuid.uuid4().hex[:20]}"
    created_subtasks: list[dict[str, Any]] = []
    for index, subtask in enumerate(proposed_subtasks, start=1):
        subtask_id = f"s{index}-{uuid.uuid4().hex[:8]}"
        subtask_prompt = str(subtask.get("prompt") or job_prompt).strip()
        item = {
            "subtask_id": subtask_id,
            "logical_subtask_id": subtask_id,
            "title": str(subtask.get("title") or job_title).strip()[:180],
            "prompt": subtask_prompt[:12000],
            "requires_web": bool(subtask.get("requires_web")),
            "reasoning_effort": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            "task_id": "",
            "current_attempt": 1,
            "attempt_task_ids": [],
            "retry_count": 0,
            "next_retry_at_epoch": 0,
            "state": "running",
        }
        try:
            task = _create_dual_worker_task(
                config,
                message=message,
                message_id=message_id,
                subject=subject,
                phone=phone,
                session=session,
                conversation_id=conversation_id,
                request_text=request_text,
                job_prompt=subtask_prompt,
                job_title=str(subtask.get("title") or job_title).strip()[:180],
                media=media,
                transcription=transcription,
                phone_ai_behavior=phone_ai_behavior,
                query_policy=query_policy,
                job_group_id=job_group_id,
                subtask_id=subtask_id,
                logical_subtask_id=subtask_id,
                attempt=1,
                subtask_index=index,
                subtask_total=len(proposed_subtasks),
                requires_web=bool(subtask.get("requires_web")),
                reasoning_effort=WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
            )
            task_id = str(task.get("task_id") or "")
            item.update({"task_id": task_id, "attempt_task_ids": [task_id] if task_id else []})
        except Exception as exc:
            reason = str(exc)[:1000]
            error_class, retryable = _dual_retry_classification(reason)
            delay = _dual_retry_delay_seconds(1, reason, f"{job_group_id}:{subtask_id}:create") if retryable else 0
            item.update(
                {
                    "state": "waiting_retry" if retryable else "partial",
                    "retry_count": 1,
                    "retry_reason": reason,
                    "next_retry_at_epoch": time.time() + delay if retryable else 0,
                    "next_retry_delay_seconds": delay,
                    "auth_retry": False,
                    "retryable": retryable,
                    "error_class": error_class,
                }
            )
        created_subtasks.append(item)
    first_task_id = next((str(item.get("task_id") or "") for item in created_subtasks if item.get("task_id")), "")
    is_group = len(created_subtasks) > 1 or not first_task_id
    creation_terminal = bool(created_subtasks) and all(
        str(item.get("state") or "") in {"partial", "canceled"} for item in created_subtasks
    )
    initial_auth_retry = any(item.get("auth_retry") is True for item in created_subtasks)
    now_epoch = time.time()
    first_item = created_subtasks[0] if created_subtasks else {}
    pending = {
        "task_id": first_task_id,
        "kind": "dual_job_group" if is_group else "dual_worker",
        "conversation_id": conversation_id,
        "conversation_agent_thread_id": str(decision.get("thread_id") or ""),
        "parent_job_id": job_group_id,
        "job_group_id": job_group_id,
        "subtasks": created_subtasks if is_group else [],
        "group_results": {},
        "collected_task_ids": [],
        "delivered_task_ids": [],
        "results_revision": 0,
        "job_title": job_title,
        "subject_id": subject,
        "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(),
        "request_text": request_text,
        "job_prompt": job_prompt,
        "query_policy": query_policy,
        "phone_ai_behavior": phone_ai_behavior,
        "media": media or {},
        "transcription": transcription or {},
        "session_permissions": {
            str(key): value is True
            for key, value in dict(session.get("permissions") or {}).items()
            if str(key or "").strip()
        },
        "session_is_full": bool(session.get("is_full")),
        "created_at": _now(),
        "created_at_epoch": now_epoch,
        "job_state": "running" if first_task_id else ("partial" if creation_terminal else "waiting_retry"),
        "retry_policy": "bounded",
        "retry_count": int(first_item.get("retry_count") or 0) if not is_group else 0,
        "next_retry_at_epoch": float(first_item.get("next_retry_at_epoch") or 0) if not is_group else 0,
        "retry_reason": str(first_item.get("retry_reason") or "") if not is_group else "",
        "attempt_task_ids": list(first_item.get("attempt_task_ids") or []) if not is_group else [],
        "current_attempt": int(first_item.get("current_attempt") or 1) if not is_group else 1,
        "subtask_id": str(first_item.get("subtask_id") or "main") if not is_group else "",
        "logical_subtask_id": str(first_item.get("logical_subtask_id") or "main") if not is_group else "",
        "prompt": str(first_item.get("prompt") or job_prompt)[:12000] if not is_group else "",
        "title": str(first_item.get("title") or job_title)[:180] if not is_group else "",
        "requires_web": bool(first_item.get("requires_web", True)) if not is_group else True,
        "reasoning_effort": WHATSAPP_TASK_AGENT_REASONING_DEFAULT,
        "verified_facts": [],
        "verified_sources": [],
        "auth_retry_active": initial_auth_retry,
        "auth_notice_pending": initial_auth_retry,
        "auth_notice_sent": False,
        "last_conversation_at": _now(),
        "last_conversation_at_epoch": now_epoch,
        "tick_index": 0,
        "wait_notice_count": 0,
        "awaiting_notified": True,
        "handoff_status": "worker_running",
        "delivery_state": "initial_reply_sent",
        "wa_id": phone,
    }
    _save_pending(state, message_id, pending)
    _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
    _post_message_result(
        config,
        message_id,
        {
            "status": "completed",
            "task_id": first_task_id,
            "job_group_id": job_group_id,
            "subtask_count": len(created_subtasks),
            "response": reply_text,
        },
    )
    if creation_terminal:
        _terminate_pending_partial(
            config,
            state,
            message_id,
            pending,
            reason=str(first_item.get("retry_reason") or "nao_foi_possivel_iniciar_a_consulta"),
        )
    return True


def _job_deadline_seconds(request_text: Any, *, requires_web: bool = False) -> int:
    text = _whatsapp_text_key(request_text)
    if re.search(r"\b(relatorio|planilha|excel|xlsx|pdf|grafico|imagem do relatorio|dashboard)\b", text):
        return WHATSAPP_REPORT_DEADLINE_SECONDS
    if re.search(r"\b(pergunta|perguntas|pos venda|comprador|mercado livre|mercadolivre)\b", text) and (
        requires_web or re.search(r"\b(respost|compatib|aplica|serve|encaixa)\b", text)
    ):
        return WHATSAPP_ML_RESEARCH_DEADLINE_SECONDS
    return WHATSAPP_JOB_DEADLINE_DEFAULT


def _ensure_job_contract(pending: dict[str, Any]) -> dict[str, Any]:
    now_epoch = time.time()
    created_epoch = float(pending.get("created_at_epoch") or now_epoch)
    pending.setdefault("created_at_epoch", created_epoch)
    deadline_seconds = int(
        pending.get("deadline_seconds")
        or _job_deadline_seconds(
            pending.get("request_text") or pending.get("job_prompt") or "",
            requires_web=bool(pending.get("requires_web")),
        )
    )
    deadline_seconds = max(30, min(deadline_seconds, WHATSAPP_REPORT_DEADLINE_SECONDS))
    pending.setdefault("deadline_seconds", deadline_seconds)
    pending.setdefault("deadline_at_epoch", created_epoch + deadline_seconds)
    pending["retry_policy"] = "bounded"
    pending["max_retry_attempts"] = WHATSAPP_MAX_RETRY_ATTEMPTS
    pending.setdefault("wait_notice_count", 0)
    pending.setdefault("last_wait_notice_at_epoch", 0)
    return pending


def _save_pending(state: dict[str, Any], message_id: str, value: dict[str, Any]) -> None:
    value.setdefault("message_id", str(message_id))
    _ensure_job_contract(value)
    with BRIDGE_STATE_LOCK:
        pending = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        pending[message_id] = value
        state["pending_messages"] = pending
        _save_state(state)


def _archive_pending(
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    status: str,
    reason: str = "",
) -> None:
    terminal = status if status in {"partial", "completed", "failed", "canceled"} else "completed"
    archived = dict(pending or {})
    archived.update(
        {
            "job_state": terminal,
            "terminal_reason": str(reason or archived.get("terminal_reason") or "")[:1000],
            "terminal_at": _now(),
            "terminal_at_epoch": time.time(),
        }
    )
    history = state.get("assistant_job_history") if isinstance(state.get("assistant_job_history"), dict) else {}
    history[str(message_id)] = archived
    if len(history) > 500:
        ordered = sorted(
            history.items(),
            key=lambda pair: float((pair[1] or {}).get("terminal_at_epoch") or 0),
        )[-500:]
        history = dict(ordered)
    state["assistant_job_history"] = history
    try:
        _bridge_store().audit(
            "assistant_job_terminal",
            message_id=str(message_id),
            subject_id=str(pending.get("subject_id") or ""),
            details={"status": terminal, "reason": str(reason or "")[:500]},
        )
    except Exception:
        pass


def _remove_pending(
    state: dict[str, Any],
    message_id: str,
    *,
    status: str = "completed",
    reason: str = "",
) -> None:
    with BRIDGE_STATE_LOCK:
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        removed = pending_messages.pop(message_id, None)
        if isinstance(removed, dict):
            _archive_pending(state, message_id, removed, status=status, reason=reason)
        state["pending_messages"] = pending_messages
        _save_state(state)


def _pending_partial_text(pending: dict[str, Any], reason: Any = "") -> str:
    facts = [str(item or "").strip() for item in list(pending.get("verified_facts") or []) if str(item or "").strip()]
    sources = [str(item or "").strip() for item in list(pending.get("verified_sources") or []) if str(item or "").strip()]
    lines: list[str] = []
    if facts:
        lines.append("Consegui confirmar até aqui:")
        lines.extend(f"- {item[:700]}" for item in facts[:5])
    else:
        lines.append("Não consegui obter uma confirmação suficiente para concluir esta solicitação.")
    clean_reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if clean_reason:
        lines.append(f"Limitação encontrada: {clean_reason[:500]}")
    if sources:
        lines.append("Fontes consultadas: " + "; ".join(item[:250] for item in sources[:5]) + ".")
    lines.append("Posso tentar novamente ou continuar se você ajustar o pedido.")
    return "\n\n".join(lines)[:3500]


def _worker_result_fallback_text(result: dict[str, Any], pending: Optional[dict[str, Any]] = None) -> str:
    value = result if isinstance(result, dict) else {}
    summary = re.sub(r"\s+", " ", str(value.get("summary") or "")).strip()
    facts = [str(item or "").strip() for item in list(value.get("verified_facts") or []) if str(item or "").strip()]
    sources = [str(item or "").strip() for item in list(value.get("sources") or []) if str(item or "").strip()]
    missing = [str(item or "").strip() for item in list(value.get("missing") or []) if str(item or "").strip()]
    lines: list[str] = []
    if summary:
        lines.append(summary[:1200])
    if facts:
        lines.extend(f"- {item[:650]}" for item in facts[:5])
    if sources:
        lines.append("Fontes: " + "; ".join(item[:220] for item in sources[:5]) + ".")
    if value.get("evidence_sufficient") is not True or str(value.get("status") or "") != "completed":
        limitation = missing[0] if missing else "a cobertura disponível não foi suficiente para confirmar tudo"
        lines.append(f"Limitação: {limitation[:500]}.")
    if not lines:
        return _pending_partial_text(pending or {}, "A resposta estruturada da IA veio vazia.")
    return "\n\n".join(lines)[:3500]


def _terminate_pending_partial(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    reason: str,
) -> bool:
    if pending.get("terminal_delivery_started"):
        return False
    pending.update(
        {
            "job_state": "partial",
            "terminal_reason": str(reason or "limite_operacional")[:1000],
            "terminal_delivery_started": True,
            "delivery_state": "partial_finalizing",
        }
    )
    _save_pending(state, message_id, pending)
    text = _pending_partial_text(pending, reason)
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"job:{pending.get('job_group_id') or message_id}:partial-terminal",
            "event_type": "task_partial",
            "severity": "medium",
            "text": text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["terminal_delivery_started"] = False
        pending["delivery_state"] = f"partial_delivery_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        return False
    _update_pending_codex_tasks(
        pending,
        handoff_status="partial_terminal",
        delivery_state=delivery,
        user_facing_response=text[:12000],
    )
    _record_message_timing(message_id, completed_at=_now(), sent_at=_now(), terminal_status="partial")
    _remove_pending(state, message_id, status="partial", reason=reason)
    return True


def _ensure_pending_approval(pending: dict[str, Any], *, renew: bool = False) -> tuple[str, float]:
    now = time.time()
    code = str(pending.get("approval_code") or "").strip().upper()
    expires_at = float(pending.get("approval_expires_at") or 0)
    if renew or not code or expires_at <= now:
        code = _approval_code()
        expires_at = now + APPROVAL_CODE_TTL_SECONDS
        pending.update(
            {
                "approval_code": code,
                "approval_expires_at": expires_at,
                "approval_used": False,
                "approval_issued_at": now,
            }
        )
    return code, expires_at


def _approval_notice(pending: dict[str, Any], *, renewed: bool = False) -> str:
    code, _ = _ensure_pending_approval(pending, renew=renewed)
    request_text = _whatsapp_clean_markdown(pending.get("request_text") or "Solicitação enviada pelo WhatsApp.")
    request_text = request_text[:700].rstrip()
    action_summary = _whatsapp_clean_markdown(pending.get("action_summary") or "")
    risk = _whatsapp_clean_markdown(pending.get("risk") or "")
    intro = "O código anterior expirou. Gere sua decisão com o novo código abaixo." if renewed else "Revise o pedido antes de autorizar a execução."
    details = [intro, f"*Pedido:* {request_text}"]
    if action_summary:
        details.append(f"*Execução reconhecida:* {action_summary[:700]}")
    if risk:
        details.append(f"*Nível de risco:* {risk[:120]}")
    details.extend(
        [
            "",
            "*Para executar:*",
            f"`APROVAR {code}`",
            "",
            "*Para rejeitar:*",
            f"`NEGAR {code}`",
            "",
            "_Código de uso único, válido por 10 minutos e aceito somente neste mesmo número._",
        ]
    )
    return "\n".join(details)


def _notify_pending_approval(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    renewed: bool = False,
) -> None:
    response = _approval_notice(pending, renewed=renewed)
    parts = _whatsapp_response_parts(response, _whatsapp_result_title("", "awaiting_approval"))
    _post_message_result(
        config,
        message_id,
        {
            "status": "awaiting_approval",
            "task_id": str(pending.get("task_id") or pending.get("proposal_id") or ""),
            "response": parts[0],
            "response_parts": parts,
        },
    )
    pending["awaiting_notified"] = True
    _save_pending(state, message_id, pending)


def _action_result_text(run: dict[str, Any]) -> str:
    action = run.get("action") if isinstance(run.get("action"), dict) else {}
    lines = [
        f"*Ação:* {str(action.get('label') or action.get('id') or 'comando do sistema')}",
        f"*Status:* {str(run.get('live_status') or run.get('status') or 'concluído')}",
    ]
    error = str(run.get("error") or "").strip()
    if error:
        lines.append(f"*Motivo:* {error[:1200]}")

    labels = {
        "status": "Status",
        "message": "Mensagem",
        "mensagem": "Mensagem",
        "resposta": "Resposta",
        "total": "Total",
        "processed": "Processados",
        "processados": "Processados",
        "created": "Criados",
        "updated": "Atualizados",
        "loja": "Loja",
        "data_inicio": "Início",
        "data_fim": "Fim",
        "external_mutation": "Alteração externa",
    }
    blocked_fragments = ("token", "secret", "authorization", "credential", "senha", "password")
    collected: list[tuple[str, str]] = []

    def collect(value: Any, prefix: str = "", depth: int = 0) -> None:
        if len(collected) >= 24 or depth > 2:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key or "")
                if any(fragment in key_text.lower() for fragment in blocked_fragments) or key_text.lower() in {"logs", "traceback"}:
                    continue
                collect(item, key_text if not prefix else f"{prefix}.{key_text}", depth + 1)
            return
        if isinstance(value, list):
            if value and all(not isinstance(item, (dict, list)) for item in value[:10]):
                collected.append((prefix, ", ".join(str(item) for item in value[:10])[:600]))
            elif value:
                collected.append((prefix, f"{len(value)} item(ns)"))
            return
        if value not in (None, ""):
            collected.append((prefix, str(value)[:700]))

    collect(run.get("result"))
    if collected:
        lines.append("")
        lines.append("*Detalhes:* ")
        for key, value in collected:
            leaf = key.rsplit(".", 1)[-1]
            label = labels.get(leaf, leaf.replace("_", " ").strip().capitalize() or "Resultado")
            lines.append(f"• *{label}:* {value}")
    return "\n".join(lines)


def _complete_action_pending(config: dict[str, Any], state: dict[str, Any], message_id: str, pending: dict[str, Any]) -> bool:
    run_id = str(pending.get("run_id") or "").strip()
    if not run_id:
        if not pending.get("awaiting_notified"):
            _notify_pending_approval(config, state, message_id, pending)
        return False
    try:
        run = (codex_actions.get_run(run_id, client_id=str(pending.get("client_id") or "")) or {}).get("run") or {}
    except HTTPException:
        return False
    status = str(run.get("status") or "")
    if status not in {"completed", "partial", "failed", "canceled"}:
        return False
    response = _action_result_text(run)
    title_status = "completed" if status in {"completed", "partial"} else "failed"
    parts = _whatsapp_response_parts(response, _whatsapp_result_title(pending.get("request_text"), title_status))
    _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"action:{run_id}:{status}",
            "event_type": "task_completed" if status in {"completed", "partial"} else "task_failed",
            "severity": "medium" if status == "partial" else ("info" if status == "completed" else "high"),
            "text": parts[0],
            "text_parts": parts,
        },
    )
    _remove_pending(
        state,
        message_id,
        status="completed" if status == "completed" else ("partial" if status == "partial" else status),
        reason="" if status == "completed" else f"acao_{status}",
    )
    return True


def _dual_task_snapshot(task: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "job_id": str(pending.get("job_group_id") or task.get("task_id") or ""),
        "job_title": str(pending.get("job_title") or pending.get("request_text") or "")[:500],
        "request_text": str(pending.get("request_text") or "")[:1200],
        "status": str(task.get("status") or ""),
        "verified_partial": verified_partial[:10],
        "subtasks_total": len(list(pending.get("subtasks") or [])) or 1,
        "subtasks_completed": len(list(pending.get("collected_task_ids") or [])),
        "recent_conversation_messages": [
            str(item or "")[:500]
            for item in list(pending.get("conversation_tick_messages") or [])[-5:]
            if str(item or "").strip()
        ],
    }


def _maybe_send_dual_conversation_tick(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    task: dict[str, Any],
) -> bool:
    if str(task.get("status") or "") not in {"queued", "running"}:
        return False
    settings = _whatsapp_dual_agent_settings(config)
    notice_count = max(0, int(pending.get("wait_notice_count") or 0))
    interval = int(
        settings.get("wait_message_after_seconds")
        if notice_count == 0
        else settings.get("wait_message_repeat_seconds")
        if notice_count == 1
        else settings.get("wait_message_steady_seconds")
    )
    now_epoch = time.time()
    last_notice = float(pending.get("last_wait_notice_at_epoch") or 0)
    reference = last_notice or float(pending.get("created_at_epoch") or now_epoch)
    if now_epoch - reference < interval:
        return False
    refreshed_tasks = _pending_codex_tasks(pending)
    if refreshed_tasks and all(
        str(item.get("status") or "") in {"completed", "partial", "failed", "canceled"}
        for item in refreshed_tasks
    ):
        return False
    tick_index = notice_count + 1
    if notice_count == 0:
        progress_text = "Ainda estou consultando as fontes necessárias. Assim que concluir, envio o resultado aqui."
    elif notice_count == 1:
        progress_text = "A consulta continua em andamento. Ainda não há um resultado final confirmado."
    else:
        progress_text = "Continuo processando sua solicitação. Avisarei assim que houver um resultado confirmado."
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{pending.get('job_group_id') or task.get('task_id')}:conversation:{tick_index}",
            "event_type": "task_conversation",
            "severity": "info",
            "text": progress_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        return False
    pending.update(
        {
            "tick_index": tick_index,
            "wait_notice_count": notice_count + 1,
            "last_wait_notice_at": _now(),
            "last_wait_notice_at_epoch": now_epoch,
            "handoff_status": "worker_running",
            "delivery_state": f"conversation_tick_{delivery}",
            "conversation_tick_messages": (
                list(pending.get("conversation_tick_messages") or [])
                + [progress_text]
            )[-5:],
        }
    )
    _save_pending(state, message_id, pending)
    _update_pending_codex_tasks(
        pending,
        handoff_status="worker_running",
        last_conversation_tick_at=_now(),
        delivery_state=f"conversation_tick_{delivery}",
    )
    return True


def _dual_worker_result_is_meaningful(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "")
    if _dual_retry_is_auth_error(
        str(result.get("summary") or "") + " " + " ".join(str(item or "") for item in list(result.get("missing") or []))
    ):
        return True
    if status == "blocked" and list(result.get("questions") or []):
        return True
    if status not in {"completed", "partial"}:
        return False
    return bool(list(result.get("verified_facts") or []) or str(result.get("summary") or "").strip())


def _dual_worker_result_is_auth_error(result: dict[str, Any]) -> bool:
    return _dual_retry_is_auth_error(
        str(result.get("summary") or "")
        + " "
        + " ".join(str(item or "") for item in list(result.get("missing") or []))
    )


def _maybe_send_dual_auth_notice(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    if pending.get("auth_notice_pending") is not True or pending.get("auth_notice_sent") is True:
        return False
    reason = str(pending.get("last_retry_reason") or "integracao desconectada (HTTP 401/403)").strip()
    worker_result = {
        "status": "partial",
        "summary": "Uma fonte exige reconexao ou nova autenticacao. A solicitacao continua pendente e sera repetida automaticamente.",
        "verified_facts": list(pending.get("verified_facts") or []),
        "sources": list(pending.get("verified_sources") or []),
        "confidence": "low",
        "missing": [reason[:1000]],
        "questions": [],
    }
    text = (
        "Não consegui acessar uma das integrações porque a autenticação está inválida ou expirada. "
        "Reconecte a conta em Integrações e depois peça para tentar novamente."
    )
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{pending.get('job_group_id') or message_id}:auth-required",
            "event_type": "task_partial",
            "severity": "medium",
            "text": text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        return False
    pending.update(
        {
            "auth_notice_sent": True,
            "auth_notice_pending": False,
            "auth_notice_sent_at": _now(),
            "last_conversation_at": _now(),
            "last_conversation_at_epoch": time.time(),
            "delivery_state": f"auth_notice_{delivery}",
        }
    )
    _save_pending(state, message_id, pending)
    return True


def _aggregate_dual_group_results(
    pending: dict[str, Any],
    *,
    task_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    results = pending.get("group_results") if isinstance(pending.get("group_results"), dict) else {}
    selected_ids = task_ids if task_ids is not None else list(results)
    summaries: list[str] = []
    facts: list[str] = [
        str(item or "").strip()[:2000]
        for item in list(pending.get("verified_facts") or [])
        if str(item or "").strip()
    ]
    sources: list[str] = [
        str(item or "").strip()[:1000]
        for item in list(pending.get("verified_sources") or [])
        if str(item or "").strip()
    ]
    missing: list[str] = []
    questions: list[str] = []
    statuses: list[str] = []
    confidences: list[str] = []
    subtask_details: list[dict[str, Any]] = []
    title_by_task = {
        str(item.get("task_id") or ""): str(item.get("title") or "").strip()
        for item in list(pending.get("subtasks") or [])
        if isinstance(item, dict)
    }

    def append_unique(target: list[str], values: Any, limit: int) -> None:
        for value in list(values or []):
            text = str(value or "").strip()
            if text and text not in target:
                target.append(text[:limit])

    for task_id in selected_ids:
        result = results.get(task_id)
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "failed")
        statuses.append(status)
        confidence = str(result.get("confidence") or "unknown")
        confidences.append(confidence)
        summary = str(result.get("summary") or "").strip()
        title = title_by_task.get(task_id, "")
        if summary:
            summaries.append(f"{title}: {summary}" if title else summary)
        append_unique(facts, result.get("verified_facts"), 2000)
        append_unique(sources, result.get("sources"), 1000)
        append_unique(missing, result.get("missing"), 1000)
        append_unique(questions, result.get("questions"), 1000)
        subtask_details.append(
            {
                "task_id": task_id,
                "title": title,
                "status": status,
                "summary": summary[:6000],
                "confidence": confidence,
            }
        )
    if statuses and all(status == "completed" for status in statuses):
        aggregate_status = "completed"
    elif any(status in {"completed", "partial"} for status in statuses):
        aggregate_status = "partial"
    elif any(status == "blocked" for status in statuses):
        aggregate_status = "blocked"
    else:
        aggregate_status = "failed"
    confidence_rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    confidence = min(confidences, key=lambda item: confidence_rank.get(item, 0)) if confidences else "unknown"
    return {
        "status": aggregate_status,
        "summary": "\n".join(summaries)[:12000],
        "verified_facts": facts[:30],
        "sources": sources[:30],
        "confidence": confidence,
        "missing": missing[:20],
        "questions": questions[:10],
        "subtasks": subtask_details,
        "coverage": {
            "completed": len(statuses),
            "total": len(list(pending.get("subtasks") or [])),
        },
    }


def _function_manager_requeue_from_sol(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    requests: list[dict[str, Any]],
    holders: list[dict[str, Any]],
) -> bool:
    clean_requests = [item for item in requests if isinstance(item, dict) and str(item.get("need") or "").strip()][:6]
    if not clean_requests:
        return False
    retry_subtasks: list[dict[str, Any]] = []
    for index, holder in enumerate(holders[:6], start=1):
        logical_id = str(holder.get("logical_subtask_id") or holder.get("subtask_id") or f"manager-sol-{index}")
        retry_subtasks.append(
            {
                "logical_subtask_id": logical_id,
                "title": str(holder.get("title") or pending.get("job_title") or "Analise complementar")[:180],
                "prompt": str(holder.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or "")[:12000],
                "requires_web": bool(holder.get("requires_web", True)),
                "current_attempt": max(1, int(holder.get("current_attempt") or 1)) + 1,
                "attempt_task_ids": list(holder.get("attempt_task_ids") or []),
            }
        )
    pending.update(
        {
            "kind": "dual_function_manager",
            "task_id": "",
            "subtasks": [],
            "manager_data_requests": clean_requests,
            "manager_state": "queued",
            "job_state": "manager_queued",
            "manager_next_retry_at_epoch": 0,
            "manager_revision": max(0, int(pending.get("manager_revision") or 0)) + 1,
            "sol_subtasks": retry_subtasks,
            "handoff_status": "sol_requested_manager_data",
            "delivery_state": "manager_recollecting",
            "last_sol_data_request_at": _now(),
        }
    )
    _save_pending(state, message_id, pending)
    _update_pending_codex_tasks(
        pending,
        handoff_status="manager_data_requested",
        delivery_state="manager_recollecting",
    )
    _submit_function_manager_job(config, state, message_id)
    return True


def _complete_dual_job_group_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    terminal = {"completed", "partial", "failed", "canceled"}
    subtasks = [item for item in list(pending.get("subtasks") or []) if isinstance(item, dict)]
    if not subtasks:
        return False
    results = dict(pending.get("group_results") or {}) if isinstance(pending.get("group_results"), dict) else {}
    collected = set(str(item or "") for item in list(pending.get("collected_task_ids") or []))
    tasks: dict[str, dict[str, Any]] = {}
    changed = False
    manager_requests: list[dict[str, Any]] = []
    manager_holders: list[dict[str, Any]] = []
    for item in subtasks:
        task_id = str(item.get("task_id") or "")
        task = codex_console._codex_load_task(task_id) if task_id else None
        if not isinstance(task, dict):
            continue
        tasks[task_id] = task
        if str(task.get("status") or "") in terminal and task_id not in collected:
            worker_result = codex_whatsapp_agents.normalize_worker_result(task)
            results[task_id] = worker_result
            collected.add(task_id)
            changed = True
            _dual_preserve_worker_result(pending, worker_result)
            disposition = _dual_worker_disposition(task, worker_result)
            if disposition == "completed":
                item.update(
                    {
                        "state": "completed",
                        "next_retry_at_epoch": 0,
                        "last_attempt_at": str(task.get("completed_at") or _now()),
                        "last_progress_at": str(task.get("last_progress_at") or task.get("completed_at") or _now()),
                    }
                )
            elif disposition == "manager_request":
                item.update(
                    {
                        "state": "manager_waiting",
                        "next_retry_at_epoch": 0,
                        "last_attempt_at": str(task.get("completed_at") or _now()),
                    }
                )
                manager_requests.extend(
                    item for item in list(worker_result.get("data_requests") or []) if isinstance(item, dict)
                )
                manager_holders.append(dict(item))
            else:
                _dual_schedule_retry(
                    pending,
                    item,
                    task,
                    worker_result,
                    key=f"{pending.get('job_group_id')}:{item.get('logical_subtask_id') or item.get('subtask_id')}",
                )
            codex_console._codex_update_task(
                task_id,
                worker_result=worker_result,
                handoff_status=(
                    "group_result_ready"
                    if disposition == "completed"
                    else "manager_data_requested" if disposition == "manager_request" else str(item.get("state") or "waiting_retry")
                ),
                delivery_state=(
                    "group_aggregating"
                    if disposition == "completed"
                    else "manager_recollecting" if disposition == "manager_request" else str(item.get("state") or "waiting_retry")
                ),
            )
    pending["group_results"] = results
    pending["collected_task_ids"] = sorted(collected)
    if manager_requests:
        pending["prior_group_results"] = results
        _function_manager_requeue_from_sol(
            config,
            state,
            message_id,
            pending,
            manager_requests,
            manager_holders,
        )
        return False
    holder_states = {str(item.get("state") or "") for item in subtasks}
    all_terminal = bool(subtasks) and holder_states.issubset({"completed", "partial", "canceled"})
    if all_terminal:
        pending["job_state"] = "completed" if holder_states == {"completed"} else "partial"
    elif any(str(item.get("state") or "") == "running" for item in subtasks):
        pending["job_state"] = "running"
    elif any(str(item.get("state") or "") == "awaiting_input" for item in subtasks):
        pending["job_state"] = "awaiting_input"
    else:
        pending["job_state"] = "waiting_retry"
    delivered = set(str(item or "") for item in list(pending.get("delivered_task_ids") or []))
    delivered_before_filter = set(delivered)
    new_meaningful = []
    for task_id in collected:
        result = results.get(task_id) or {}
        if task_id in delivered or not _dual_worker_result_is_meaningful(result):
            continue
        if pending.get("auth_notice_sent") is True and _dual_worker_result_is_auth_error(result):
            # A pesquisa permanece pendente, mas a mesma desconexao nao deve
            # gerar um novo aviso a cada tentativa de quinze minutos.
            delivered.add(task_id)
            continue
        new_meaningful.append(task_id)
    if delivered != delivered_before_filter:
        pending["delivered_task_ids"] = sorted(delivered)
        changed = True
    now_epoch = time.time()
    if new_meaningful and not pending.get("partial_pending_since_epoch"):
        pending["partial_pending_since_epoch"] = now_epoch
        changed = True
    if changed:
        _save_pending(state, message_id, pending)

    if not all_terminal and new_meaningful:
        # Resultados parciais permanecem anexados ao job, mas os avisos de
        # espera sao fixos. Nenhum agente conversa sozinho durante a espera.
        pending["delivered_task_ids"] = sorted(delivered.union(new_meaningful))
        pending["partial_pending_since_epoch"] = 0
        pending["delivery_state"] = "partial_evidence_buffered"
        _save_pending(state, message_id, pending)

    if not all_terminal:
        synthetic_status = "running" if any(str(task.get("status") or "") == "running" for task in tasks.values()) else "queued"
        _maybe_send_dual_conversation_tick(
            config,
            state,
            message_id,
            pending,
            {"task_id": str(pending.get("job_group_id") or message_id), "status": synthetic_status},
        )
        return False

    final_result = _aggregate_dual_group_results(pending)
    synthetic = {"task_id": str(pending.get("job_group_id") or message_id), "status": "completed"}
    try:
        decision = _run_conversation_agent(
            config,
            state,
            str(pending.get("conversation_id") or ""),
            event_type="worker_result",
            user_message=str(pending.get("request_text") or ""),
            active_job=_dual_task_snapshot(synthetic, pending),
            worker_result=final_result,
            ai_behavior=str(pending.get("phone_ai_behavior") or ""),
            tick_index=int(pending.get("tick_index") or 0),
        )
        final_text = str(decision.get("reply_text") or "").strip()
    except Exception as exc:
        decision = {}
        final_text = _worker_result_fallback_text(final_result, pending)
        RUNTIME_STATE["conversation_fallback_last_error"] = str(exc)[:500]
    report_request = str(pending.get("request_text") or "")
    if whatsapp_report_files.report_requested(report_request):
        artifacts = [
            artifact
            for task in tasks.values()
            for artifact in list(task.get("whatsapp_artifacts") or [])
            if isinstance(artifact, dict)
        ][:4]
        artifact_results = _whatsapp_deliver_report_artifacts(
            config,
            message_id,
            artifacts,
            pending.get("client_id") or "default",
            max_images=4,
        ) if artifacts else []
        summaries = [
            summary
            for task in tasks.values()
            for summary in list(task.get("tool_results_summary") or [])
            if isinstance(summary, dict)
        ]
        final_text = "\n\n".join(
            item
            for item in (
                final_text,
                _whatsapp_report_metadata_text(report_request, pending.get("query_policy"), summaries),
                whatsapp_report_files.report_offer_text(report_request),
            )
            if item
        ).strip()
        if artifacts and not all(item.get("success") for item in artifact_results):
            final_text += "\n\nUm ou mais arquivos nao puderam ser anexados; o resumo em texto foi preservado."
        for task_id in tasks:
            codex_console._codex_update_task(task_id, whatsapp_artifacts=[])
    group_id = str(pending.get("job_group_id") or message_id)
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{group_id}:final:{hashlib.sha256(final_text.encode('utf-8')).hexdigest()[:16]}",
            "event_type": "task_completed" if final_result.get("status") in {"completed", "partial"} else "task_failed",
            "severity": "info" if final_result.get("status") == "completed" else "medium",
            "text": final_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["delivery_state"] = f"final_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        return False
    _update_pending_codex_tasks(
        pending,
        handoff_status="delivered_by_conversation_agent",
        delivery_state=delivery,
        conversation_agent_thread_id=str(decision.get("thread_id") or "")[:200],
        user_facing_response=final_text[:12000],
    )
    representative = next(iter(tasks.values()), {})
    _whatsapp_update_query_context_from_task(state, pending, representative)
    _record_message_timing(message_id, completed_at=_now(), sent_at=_now())
    _remove_pending(
        state,
        message_id,
        status="completed" if str(final_result.get("status") or "") == "completed" else "partial",
        reason="" if str(final_result.get("status") or "") == "completed" else "resultado_parcial",
    )
    return True


def _complete_dual_worker_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    task_id = str(pending.get("task_id") or "")
    task = codex_console._codex_load_task(task_id) if task_id else None
    if not isinstance(task, dict):
        return False
    status = str(task.get("status") or "")
    if status not in {"completed", "partial", "failed", "canceled"}:
        _maybe_send_dual_conversation_tick(config, state, message_id, pending, task)
        return False
    sol_started = task.get("sol_started_at") or task.get("started_at") or ""
    completed_at = task.get("completed_at") or _now()
    _record_message_timing(
        message_id,
        sol_started_at=sol_started,
        completed_at=completed_at,
    )
    sol_started_epoch = _timing_epoch(sol_started)
    completed_epoch = _timing_epoch(completed_at)
    if sol_started_epoch and completed_epoch:
        _record_latency("sol_duration", completed_epoch - sol_started_epoch)
    worker_result = codex_whatsapp_agents.normalize_worker_result(task)
    disposition = _dual_worker_disposition(task, worker_result)
    if disposition == "manager_request":
        _dual_preserve_worker_result(pending, worker_result)
        codex_console._codex_update_task(
            task_id,
            worker_result=worker_result,
            handoff_status="manager_data_requested",
            delivery_state="manager_recollecting",
        )
        holder = {
            "logical_subtask_id": str(pending.get("logical_subtask_id") or pending.get("subtask_id") or "main"),
            "subtask_id": str(pending.get("subtask_id") or "main"),
            "title": str(pending.get("title") or pending.get("job_title") or "Analise complementar"),
            "prompt": str(pending.get("prompt") or pending.get("job_prompt") or pending.get("request_text") or ""),
            "requires_web": bool(pending.get("requires_web", True)),
            "current_attempt": max(1, int(pending.get("current_attempt") or 1)),
            "attempt_task_ids": list(pending.get("attempt_task_ids") or [task_id]),
        }
        _function_manager_requeue_from_sol(
            config,
            state,
            message_id,
            pending,
            [item for item in list(worker_result.get("data_requests") or []) if isinstance(item, dict)],
            [holder],
        )
        return False
    if disposition != "completed":
        handled = [str(item or "") for item in list(pending.get("handled_attempt_task_ids") or []) if str(item or "")]
        if task_id not in handled:
            _dual_schedule_retry(
                pending,
                pending,
                task,
                worker_result,
                key=f"{pending.get('job_group_id') or message_id}:main",
            )
            handled.append(task_id)
            pending["handled_attempt_task_ids"] = handled[-50:]
            _save_pending(state, message_id, pending)
            if str(pending.get("job_state") or "") == "partial":
                return _terminate_pending_partial(
                    config,
                    state,
                    message_id,
                    pending,
                    reason=str(pending.get("terminal_reason") or "resultado_parcial"),
                )
            if disposition == "awaiting_input":
                questions = [
                    str(item or "").strip()
                    for item in list(worker_result.get("questions") or pending.get("pending_questions") or [])
                    if str(item or "").strip()
                ]
                prompt = "Preciso desta informação para continuar: " + (
                    questions[0] if questions else "informe os dados que faltam no pedido."
                )
                delivery_result = _post_proactive(
                    config,
                    {
                        "subject_id": str(pending.get("subject_id") or ""),
                        "fingerprint": f"dual:{pending.get('job_group_id') or message_id}:awaiting-input",
                        "event_type": "task_partial",
                        "severity": "info",
                        "text": prompt[:3500],
                    },
                )
                pending["awaiting_notified"] = str(delivery_result.get("status") or "") in {
                    "sent",
                    "queued",
                    "duplicate",
                    "waiting_free_window",
                }
                pending["delivery_state"] = f"awaiting_input_{str(delivery_result.get('status') or 'failed')}"
                _save_pending(state, message_id, pending)
                return False
        _maybe_send_dual_conversation_tick(
            config,
            state,
            message_id,
            pending,
            {"task_id": str(pending.get("job_group_id") or task_id), "status": "queued"},
        )
        return False
    _dual_preserve_worker_result(pending, worker_result)
    conversation_id = str(pending.get("conversation_id") or "")
    codex_console._codex_update_task(
        task_id,
        worker_result=worker_result,
        handoff_status="result_ready",
        delivery_state="conversation_agent_finalizing",
    )
    try:
        decision = _run_conversation_agent(
            config,
            state,
            conversation_id,
            event_type="worker_result",
            user_message=str(pending.get("request_text") or ""),
            active_job=_dual_task_snapshot(task, pending),
            worker_result=worker_result,
            ai_behavior=str(pending.get("phone_ai_behavior") or ""),
            tick_index=int(pending.get("tick_index") or 0),
        )
        final_text = str(decision.get("reply_text") or "").strip()
    except Exception as exc:
        decision = {}
        final_text = _worker_result_fallback_text(worker_result, pending)
        RUNTIME_STATE["conversation_fallback_last_error"] = str(exc)[:500]
    report_request = pending.get("request_text") or task.get("prompt") or ""
    if whatsapp_report_files.report_requested(report_request):
        artifact_results = _whatsapp_deliver_report_artifacts(
            config,
            message_id,
            task.get("whatsapp_artifacts"),
            pending.get("client_id") or task.get("client_id") or "default",
            max_images=4,
        )
        final_text = "\n\n".join(
            item
            for item in (
                final_text,
                _whatsapp_report_metadata_text(
                    report_request,
                    pending.get("query_policy") or task.get("query_policy"),
                    task.get("tool_results_summary") or [],
                ),
                whatsapp_report_files.report_offer_text(report_request),
            )
            if item
        ).strip()
        if task.get("whatsapp_artifacts") and not all(item.get("success") for item in artifact_results):
            final_text += "\n\nUm ou mais arquivos nao puderam ser anexados; o resumo em texto foi preservado."
        if task.get("whatsapp_artifacts"):
            codex_console._codex_update_task(task_id, whatsapp_artifacts=[])
    event_type = "task_completed" if worker_result.get("status") in {"completed", "partial"} else "task_failed"
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"dual:{task_id}:final:{hashlib.sha256(final_text.encode('utf-8')).hexdigest()[:16]}",
            "event_type": event_type,
            "severity": "info" if worker_result.get("status") == "completed" else "medium",
            "text": final_text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["delivery_state"] = f"final_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        codex_console._codex_update_task(task_id, delivery_state=pending["delivery_state"])
        return False
    codex_console._codex_update_task(
        task_id,
        handoff_status="delivered_by_conversation_agent",
        delivery_state=delivery,
        conversation_agent_thread_id=str(decision.get("thread_id") or "")[:200],
        user_facing_response=final_text[:12000],
    )
    sent_epoch = time.time()
    _record_message_timing(message_id, sent_at=_now())
    if completed_epoch:
        _record_latency("completed_to_sent", sent_epoch - completed_epoch)
    _whatsapp_update_query_context_from_task(state, pending, task)
    _remove_pending(
        state,
        message_id,
        status="completed" if str(worker_result.get("status") or "") == "completed" else "partial",
        reason="" if str(worker_result.get("status") or "") == "completed" else "resultado_parcial",
    )
    return True


def _complete_pending(config: dict[str, Any], state: dict[str, Any], message_id: str, pending: dict[str, Any]) -> bool:
    if str(pending.get("kind") or "task") == "action_proposal":
        return _complete_action_pending(config, state, message_id, pending)
    if str(pending.get("kind") or "task") == "dual_job_group":
        return _complete_dual_job_group_pending(config, state, message_id, pending)
    if str(pending.get("kind") or "task") == "dual_worker":
        return _complete_dual_worker_pending(config, state, message_id, pending)
    if str(pending.get("kind") or "task") == "dual_function_manager":
        manager_state = str(pending.get("manager_state") or "queued")
        if manager_state in {"running", "queued", "waiting_retry", "partial"}:
            _maybe_send_dual_conversation_tick(
                config,
                state,
                message_id,
                pending,
                {
                    "task_id": str(pending.get("job_group_id") or message_id),
                    "status": "running" if manager_state == "running" else "queued",
                },
            )
        return False
    task_id = str(pending.get("task_id") or "")
    task = codex_console._codex_load_task(task_id) if task_id else None
    if not task:
        return False
    status = str(task.get("status") or "")
    if status == "awaiting_approval" and not pending.get("awaiting_notified"):
        if task.get("whatsapp_full_access") is True:
            _notify_pending_approval(config, state, message_id, pending)
        else:
            response = (
                "Este pedido exige aprovação no aplicativo JK Sistema porque o vínculo não possui o modo móvel full. "
                "Abra a tarefa no Black John e informe o módulo ou caminho permitido."
            )
            parts = _whatsapp_response_parts(response, _whatsapp_result_title("", "awaiting_approval"))
            _post_message_result(
                config,
                message_id,
                {"status": "awaiting_approval", "task_id": task_id, "response": parts[0], "response_parts": parts},
            )
            pending["awaiting_notified"] = True
            _save_pending(state, message_id, pending)
        return False
    if status not in {"completed", "partial", "failed", "canceled"}:
        return False
    response = str(task.get("final_response") or task.get("error") or "Black John concluiu sem resposta final.")
    allow_full_history = WHATSAPP_EXACT_ORDER_HISTORY_MARKER in response
    title_status = "completed" if status in {"completed", "partial"} else "failed"
    if status in {"completed", "partial"}:
        client_id = pending.get("client_id") or task.get("client_id") or config.get("client_id")
        chart_results = _whatsapp_deliver_report_artifacts(
            config,
            message_id,
            task.get("whatsapp_artifacts"),
            client_id,
            max_images=4,
        )
        charts_sent = sum(1 for item in chart_results if item.get("success") and item.get("artifact_type") == "report_chart")
        report_files_sent = sum(1 for item in chart_results if item.get("success"))
        image_results = list(chart_results)
        remaining_images = max(0, WHATSAPP_MAX_OUTBOUND_IMAGES - charts_sent)
        request_text = pending.get("request_text") or task.get("prompt")
        if remaining_images > 0:
            response, product_results = _whatsapp_deliver_requested_images(
                config,
                message_id,
                response,
                request_text,
                client_id,
                max_images=remaining_images,
            )
            image_results.extend(product_results)
        elif _whatsapp_image_requested(request_text):
            response = _whatsapp_strip_image_references(response)
        if task.get("whatsapp_chart_expected") is True and charts_sent == 0:
            response = (
                response.rstrip()
                + "\n\n_O relatório em texto está completo. O gráfico visual ficou indisponível nesta execução; "
                "a proteção de custo zero não permitiu usar uma alternativa paga._"
            ).strip()
        if task.get("whatsapp_artifacts"):
            codex_console._codex_update_task(
                task_id,
                whatsapp_artifacts=[],
                whatsapp_chart_status="sent" if report_files_sent else "send_failed",
                whatsapp_chart_error="" if report_files_sent else str(task.get("whatsapp_chart_error") or "report_artifact_not_sent")[:500],
            )
        if image_results:
            RUNTIME_STATE["last_outbound_images"] = image_results[-WHATSAPP_MAX_OUTBOUND_IMAGES:]
        report_request = pending.get("request_text") or task.get("prompt") or ""
        if whatsapp_report_files.report_requested(report_request):
            response = "\n\n".join(
                item
                for item in (
                    response,
                    _whatsapp_report_metadata_text(
                        report_request,
                        pending.get("query_policy") or task.get("query_policy"),
                        task.get("tool_results_summary") or [],
                    ),
                    whatsapp_report_files.report_offer_text(report_request),
                )
                if item
            ).strip()
    parts = _whatsapp_response_parts(response, _whatsapp_result_title(pending.get("request_text") or task.get("prompt"), title_status))
    if pending.get("awaiting_notified"):
        event_type = "task_completed" if status in {"completed", "partial"} else "task_failed"
        _post_proactive(
            config,
            {
                "subject_id": str(pending.get("subject_id") or ""),
                "fingerprint": f"task:{task_id}:{status}",
                "event_type": event_type,
                "severity": "medium" if status == "partial" else ("high" if status != "completed" else "info"),
                "text": parts[0],
                "text_parts": parts,
                "allow_full_history": allow_full_history,
            },
        )
    else:
        _post_message_result(
            config,
            message_id,
            {
                "status": "completed" if status in {"completed", "partial"} else "failed",
                "task_id": task_id,
                "response": parts[0],
                "response_parts": parts,
                "allow_full_history": allow_full_history,
            },
        )
    _whatsapp_update_query_context_from_task(state, pending, task)
    _remove_pending(state, message_id)
    return True


def _message_request_text(message: dict[str, Any], transcription: Optional[dict[str, Any]] = None) -> str:
    parts: list[str] = []
    body = str(message.get("text_body") or "").strip()
    if body:
        parts.append(body)
    if isinstance(transcription, dict) and transcription.get("success") and str(transcription.get("text") or "").strip():
        parts.append(str(transcription.get("text") or "").strip())
    return "\n\n".join(parts).strip()[:12000]


def _mobile_screen_context(
    message_id: str,
    subject: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "title": "WhatsApp — Black John",
        "pathname": "/whatsapp",
        "modulo_atual": "black_jhon_mobile",
        "selection": {
            "origin": "whatsapp",
            "message_id": message_id,
            "subject_id": subject,
            "mobile_full_access": True,
            "media": media or {},
            "transcription": transcription or {},
            "query_policy": dict(query_policy or {}) if isinstance(query_policy, dict) else {},
        },
    }


def _try_create_action_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    screen_context: dict[str, Any],
) -> bool:
    if not session.get("is_full") or not request_text:
        return False
    if _whatsapp_readonly_inquiry(request_text):
        return False
    followups = state.get("action_followups") if isinstance(state.get("action_followups"), dict) else {}
    followup = followups.get(conversation_id) if isinstance(followups.get(conversation_id), dict) else {}
    if followup and time.time() - float(followup.get("created_at") or 0) > 3600:
        followups.pop(conversation_id, None)
        followup = {}
    action_reference = "\n".join(
        item for item in (str(followup.get("message") or "").strip(), str(request_text or "").strip()) if item
    )
    if not followup and not codex_console._codex_prompt_pede_alteracao(request_text):
        return False
    history = [{"role": "user", "text": str(followup.get("message") or "")}] if followup else []
    raw_wa_id = str(message.get("wa_id") or "").strip()
    try:
        normalized_wa_id = _normalize_registered_phone(raw_wa_id) if raw_wa_id else ""
    except HTTPException:
        normalized_wa_id = ""
    result = codex_actions.create_proposal(
        client_id=str(session.get("client_id") or "default"),
        username=str(session.get("username") or ""),
        message=request_text,
        screen_context=screen_context,
        history=history,
        conversation_id=conversation_id,
        conversation_generation=1,
        channel="whatsapp",
        wa_id_hash=hashlib.sha256(normalized_wa_id.encode("utf-8")).hexdigest() if normalized_wa_id else "",
        idempotency_key=f"wa:{str(message.get('message_id') or '').strip()}",
    )
    if not isinstance(result, dict) or result.get("matched") is not True:
        if followup:
            followups.pop(conversation_id, None)
            state["action_followups"] = followups
            _save_state(state)
        return False
    message_id = str(message.get("message_id") or "")
    subject = str(message.get("subject_id") or "")
    if result.get("needs_input"):
        missing = [str(item) for item in (result.get("missing_params") or []) if str(item or "").strip()]
        followups[conversation_id] = {"message": request_text, "created_at": time.time(), "missing_params": missing}
        state["action_followups"] = followups
        _save_state(state)
        response = (
            "Reconheci o comando, mas preciso destas informações antes de preparar a confirmação:\n\n"
            + "\n".join(f"• *{item.replace('_', ' ').capitalize()}*" for item in missing)
            + "\n\nEnvie os dados faltantes em uma nova mensagem."
        )
        parts = _whatsapp_response_parts(response, "🧩 BLACK JOHN — DADOS NECESSÁRIOS")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        return True
    followups.pop(conversation_id, None)
    state["action_followups"] = followups
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    if not proposal:
        _save_state(state)
        return False
    if proposal.get("can_execute") is False:
        response = (
            f"A função *{str((proposal.get('action') or {}).get('label') or proposal.get('action_id') or 'solicitada')}* foi reconhecida, "
            "mas ainda está marcada como somente proposta no Black John e não possui executor seguro. "
            "Ela não será executada pelo WhatsApp."
        )
        parts = _whatsapp_response_parts(response, "🛡️ BLACK JOHN — EXECUÇÃO INDISPONÍVEL")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        _save_state(state)
        return True
    pending = {
        "kind": "action_proposal",
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "subject_id": subject,
        "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(),
        "request_text": request_text,
        "action_summary": str(proposal.get("summary") or proposal.get("title") or ""),
        "risk": str(proposal.get("risk") or ""),
        "created_at": _now(),
        "awaiting_notified": False,
        "trusted_bound_number": True,
        "proposal_version": int(proposal.get("version") or 1),
        "proposal_hash": str(proposal.get("proposal_hash") or ""),
        "wa_id": normalized_wa_id,
    }
    # Mutacoes do agente geral sempre exigem confirmacao no aplicativo. A
    # excecao de Perguntas e Pos-venda usa o fluxo especializado anterior.
    if proposal:
        _save_state(state)
        response = (
            f"Preparei a proposta *{str(proposal.get('title') or proposal.get('action_id') or 'solicitada')}*. "
            "Por segurança, esta ação precisa ser revisada e confirmada no aplicativo JK Sistema. "
            f"Identificador: `{str(proposal.get('proposal_id') or '')}`."
        )
        parts = _whatsapp_response_parts(response, "BLACK JHON - CONFIRME NO APLICATIVO")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        return True
    _save_pending(state, message_id, pending)
    _notify_pending_approval(config, state, message_id, pending)
    return True


def _find_pending_approval(
    state: dict[str, Any],
    *,
    subject_id: str,
    username: str,
    client_id: str,
    code: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    for original_message_id, pending in pending_messages.items():
        if not isinstance(pending, dict) or pending.get("approval_used") is True:
            continue
        if str(pending.get("subject_id") or "") != subject_id:
            continue
        if str(pending.get("username") or "").strip().lower() != username.strip().lower():
            continue
        if str(pending.get("client_id") or "").strip() != client_id.strip():
            continue
        expected = str(pending.get("approval_code") or "").strip().upper()
        if expected and secrets.compare_digest(expected, str(code or "").strip().upper()):
            return str(original_message_id), pending
    return "", None


def _post_command_reply(config: dict[str, Any], message_id: str, text: str, title: str) -> None:
    parts = _whatsapp_response_parts(text, title)
    _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})


def _question_approval_command(value: Any) -> tuple[str, str]:
    match = QUESTION_APPROVAL_COMMAND_RE.match(str(value or "").strip())
    if not match:
        return "", ""
    return str(match.group(1) or "").lower(), str(match.group(2) or "").upper()


def _question_approval_lookup(
    state: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    session: dict[str, Any],
) -> Optional[dict[str, Any]]:
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    item = tokens.get(str(token or "").upper())
    if not isinstance(item, dict) or item.get("used") is True:
        return None
    if time.time() - float(item.get("created_at") or 0) > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
        return None
    if str(item.get("subject_id") or "") != str(subject_id or ""):
        return None
    if str(item.get("client_id") or "") != str(session.get("client_id") or ""):
        return None
    if str(item.get("username") or "").strip().lower() != str(session.get("username") or "").strip().lower():
        return None
    return item


def _question_bind_token_draft(token_item: dict[str, Any], approval: dict[str, Any]) -> str:
    response = (
        str(token_item.get("suggested_response") or "").strip()
        or str(approval.get("resposta_sugerida") or "").strip()
    )[:1200]
    if response and not str(token_item.get("suggested_response") or "").strip():
        token_item["suggested_response"] = response
        token_item["draft_hash"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
        token_item["draft_created_at"] = _now()
    elif response and len(str(token_item.get("draft_hash") or "")) != 64:
        token_item["draft_hash"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
    return response


def _question_validate_approval_send(
    token_item: dict[str, Any],
    approval: dict[str, Any],
    *,
    client_id: str,
) -> tuple[str, str]:
    if str(approval.get("status") or "pending") != "pending":
        raise RuntimeError("question_already_resolved")
    approval_id = str(approval.get("id") or "").strip()
    question_id = str(approval.get("question_id") or approval.get("pergunta_id") or approval_id).strip()
    store = str(approval.get("loja") or "").strip()
    if not client_id or not approval_id or not question_id or not store:
        raise RuntimeError("question_approval_scope_incomplete")
    draft = _question_bind_token_draft(token_item, approval)
    if not draft:
        raise RuntimeError("question_approval_draft_empty")
    draft_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(str(token_item.get("draft_hash") or ""), draft_hash):
        raise RuntimeError("question_approval_draft_hash_mismatch")
    idempotency_key = hashlib.sha256(
        f"{client_id}|{store}|{question_id}|{draft_hash}".encode("utf-8")
    ).hexdigest()
    existing = str(token_item.get("idempotency_key") or "")
    if existing and not secrets.compare_digest(existing, idempotency_key):
        raise RuntimeError("question_approval_idempotency_mismatch")
    token_item["idempotency_key"] = idempotency_key
    token_item["question_id"] = question_id
    token_item["store"] = store
    return draft, idempotency_key


def _regenerate_question_approval_response(
    approval: dict[str, Any],
    approvals: list[dict[str, Any]],
    client_id: str,
    *,
    guidance: str = "",
) -> str:
    from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest, PosVendaGerarRespostaRequest
    from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
    from backend.services import perguntas_pos_venda_state as ppv_state

    approval_type = str(approval.get("tipo") or approval.get("approval_type") or "").strip().lower()
    store = str(approval.get("loja") or "").strip()
    exact_response = _question_explicit_response(guidance)
    if exact_response:
        generated = {"resposta": exact_response}
    elif approval_type == "pos_venda":
        generated = ppv_endpoints.ml_pos_venda_gerar_resposta_conversa(
            PosVendaGerarRespostaRequest(
                loja=store,
                pack_id=approval.get("pack_id") or "",
                order_id=approval.get("order_id") or "",
                buyer_id=approval.get("buyer_id") or "",
                max_chars=int(approval.get("max_chars") or 350),
                resposta_atual=str(approval.get("resposta_sugerida") or "").strip(),
                orientacao_usuario=str(guidance or "").strip()[:1200],
                async_mode=True,
            ),
            client_id,
        )
    else:
        question = {
            "id": str(approval.get("question_id") or "").strip(),
            "text": str(approval.get("pergunta") or "").strip(),
            "item_id": str(approval.get("item_id") or "").strip(),
            "item_title": str(approval.get("titulo") or "").strip(),
            "item_sku": str(approval.get("sku") or "").strip(),
            "buyer_question_chat": approval.get("mensagens") or [],
        }
        generated = ppv_endpoints.ml_perguntas_gerar_resposta_manual(
            PerguntasGerarRespostaRequest(
                loja=store,
                pergunta=question,
                resposta_atual=str(approval.get("resposta_sugerida") or "").strip(),
                orientacao_usuario=str(guidance or "").strip()[:1200],
                async_mode=True,
            ),
            client_id,
        )
    generated_result = generated.get("result") if isinstance((generated or {}).get("result"), dict) else {}
    generated_job_id = str((generated or {}).get("job_id") or generated_result.get("proposal_id") or "").strip()
    generated_status = str((generated or {}).get("status") or "completed").strip().lower()
    if generated_job_id:
        approval["codex_job_id"] = generated_job_id
        approval["proposal_id"] = generated_job_id
        approval["proposal_version"] = int(
            generated_result.get("proposal_version") or (generated or {}).get("proposal_version") or 1
        )
        approval["proposal_hash"] = str(
            generated_result.get("proposal_hash") or (generated or {}).get("proposal_hash") or ""
        )
        approval["data_sufficient"] = bool(
            generated_result.get("data_sufficient")
            if "data_sufficient" in generated_result
            else (generated or {}).get("data_sufficient")
        )
        approval["warnings"] = list(generated_result.get("warnings") or (generated or {}).get("warnings") or [])[:8]
    if str(guidance or "").strip():
        approval["whatsapp_user_guidance"] = str(guidance or "").strip()[:1200]
    if generated_job_id and (
        generated_status != "completed"
        or generated_result.get("data_sufficient") is False
        or ("data_sufficient" in generated and generated.get("data_sufficient") is False)
    ):
        approval.update(
            {
                "research_status": generated_status or "queued",
                "research_job_id": generated_job_id,
                "research_started_at": _now(),
                "research_delivery_state": "waiting_evidence",
                "data_sufficient": False,
            }
        )
        ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        raise _QuestionResearchPending(generated_job_id, generated_status)

    response = str((generated or {}).get("resposta") or (generated or {}).get("answer") or generated_result.get("resposta") or "").strip()[:1200]
    if not response:
        raise RuntimeError("generated_answer_empty")
    approval["resposta_sugerida"] = response
    approval["regenerated_at"] = _now()
    approval["regenerated_via"] = "whatsapp"
    approval["research_status"] = "completed" if generated_job_id else "not_required"
    approval["research_delivery_state"] = "ready"
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    return response


def _question_natural_action(value: Any) -> str:
    text = _whatsapp_text_key(value)
    if not text:
        return ""
    if re.search(r"\b(cancele|cancelar|pare|parar|interrompa|interromper)\b.{0,40}\b(pesquisa|busca|consulta)\b", text):
        return "cancel_research"
    if re.search(r"\b(nao responda|nao envie|negue|negar|rejeite|rejeitar|descarte|ignorar esta pergunta)\b", text):
        return "reject"
    if (
        re.search(r"\b(gere|gerar|crie|criar|faca|fazer)\b.{0,50}\b(outra|nova)?\s*(sugestao|resposta)\b", text)
        or re.search(r"\b(responda assim|pode responder assim|sugestao de resposta|minha sugestao|use esta resposta|use essa resposta)\b", text)
        or re.search(r"\b(diga|informe|confirme|considere|esclareca)\s+que\b", text)
        or re.search(r"\b(falando|dizendo|informando|esclarecendo)\s+que\b", text)
        or re.search(
            r"\b(remova|retire|exclua|apague|mantenha|preserve|repita|refaca|refaz|refazer|"
            r"reescreva|reformule|melhore|melhorar|ajuste|altere|mude|troque|substitua|"
            r"corrija|inclua|acrescente|adicione|deixe)\b",
            text,
        )
    ):
        return "suggest"
    if (
        re.search(r"\b(perfeito|correto|certo|ok|sim|aprovado)\b.{0,50}\b(responda|responder|envie|enviar|mande|mandar|aprove|aprovar)\b", text)
        or re.fullmatch(r"(?:pode\s+)?(?:responda|responder|envie|enviar|mande|mandar|aprove|aprovar)(?:\s+(?:a|essa|esta))?\s*(?:pergunta|resposta)?", text)
        or re.search(r"\b(pode enviar|pode responder|responda a pergunta|envie a resposta|mande a resposta)\b", text)
        or re.fullmatch(r"(?:responda|resposta|envie|mande)\s+(?:isso|essa|esta)(?:\s+resposta)?", text)
    ):
        return "confirm_approval"
    return ""


def _question_suggestion_guidance(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
    return raw


def _question_explicit_response(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
    if not raw:
        return ""
    explicit = re.search(
        r"(?:responda assim|pode responder assim|sugest(?:ao|ão)(?: de resposta)?|"
        r"minha sugest(?:ao|ão)|use (?:esta|essa) resposta)\s*[:\-]\s*(.+)$",
        raw,
        flags=re.I,
    )
    return str(explicit.group(1) or "").strip()[:1200] if explicit else ""


def _handle_question_natural_language(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action = _question_natural_action(message.get("text_body"))
    if not _question_approval_allowed(session.get("permissions") or {}):
        return False
    subject_id = str(message.get("subject_id") or "").strip()
    client_id = str(session.get("client_id") or "").strip()
    username = str(session.get("username") or "").strip().lower()
    message_id = str(message.get("message_id") or "").strip()
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    active_thread = threads.get(_question_thread_key(client_id, subject_id, username))
    has_active_token = any(
        isinstance(item, dict)
        and item.get("used") is not True
        and str(item.get("subject_id") or "") == subject_id
        and str(item.get("client_id") or "") == client_id
        and str(item.get("username") or "").strip().lower() == username
        for item in tokens.values()
    )
    if not action:
        awaiting_correction = any(
            isinstance(item, dict)
            and item.get("used") is not True
            and item.get("awaiting_correction") is True
            and str(item.get("subject_id") or "") == subject_id
            and str(item.get("client_id") or "") == client_id
            and str(item.get("username") or "").strip().lower() == username
            for item in tokens.values()
        )
        action = "suggest" if awaiting_correction and str(message.get("text_body") or "").strip() else ""
    if not action:
        return False
    # Verbos como "corrija" tambem aparecem em conversas comuns. So trate a
    # frase como revisao do Mercado Livre quando este mesmo numero realmente
    # tiver uma pergunta interativa ativa.
    if not isinstance(active_thread, dict) and not has_active_token:
        return False
    try:
        from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest
        from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
        from backend.services import perguntas_pos_venda_state as ppv_state

        approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
        approval, token, token_item = _question_active_approval(
            state,
            approvals,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
        if approval is None or str(approval.get("status") or "pending") != "pending":
            return False
        if not token:
            token, token_item = _question_approval_token(
                state,
                approval=approval,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
            )
        if not isinstance(token_item, dict):
            tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
            token_item = tokens.get(token) if isinstance(tokens.get(token), dict) else {}
        _question_bind_token_draft(token_item, approval)

        if action == "confirm_approval":
            result = _post_interactive_approval(
                config,
                subject_id=subject_id,
                fingerprint="ppv-natural-confirm:" + hashlib.sha256(
                    f"{subject_id}|{token}|{message_id}".encode("utf-8")
                ).hexdigest(),
                token=token,
                body=_question_approval_body(approval, _question_bind_token_draft(token_item, approval)),
            )
            _save_state(state)
            if str(result.get("status") or "") in {"sent", "duplicate"}:
                _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
            else:
                _post_command_reply(
                    config,
                    message_id,
                    "Para enviar, use somente a opcao tokenizada Aprovar e enviar. Nada foi enviado ao comprador.",
                    "BLACK JHON - CONFIRMACAO OBRIGATORIA",
                )
            return True

        if action == "cancel_research":
            from backend.services import perguntas_pos_venda_codex as ppv_codex

            research_job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
            if research_job_id:
                ppv_codex.cancel_job(client_id, research_job_id)
            approval.update(
                {
                    "research_status": "cancelled",
                    "research_delivery_state": "cancelled",
                    "research_cancelled_at": _now(),
                }
            )
            ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
            _save_state(state)
            _post_command_reply(
                config,
                message_id,
                "Pesquisa cancelada. Nenhuma resposta foi enviada ao comprador e a pergunta continua disponivel para uma nova orientacao.",
                "BLACK JHON - PESQUISA CANCELADA",
            )
            return True

        if action == "suggest":
            guidance = _question_suggestion_guidance(message.get("text_body"))
            token_item["awaiting_correction"] = False
            regenerated = _regenerate_question_approval_response(
                approval,
                approvals,
                client_id,
                guidance=guidance,
            )
            # Cada cartao precisa de um token proprio. Assim, o botao Aprovar
            # sempre envia o texto exibido naquele cartao, mesmo que existam
            # outras versoes da mesma pergunta na conversa.
            token, token_item = _question_approval_token(
                state,
                approval=approval,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
                force_new=True,
                user_guidance=guidance,
            )
            token_item["regenerated_at"] = _now()
            _question_set_active_thread(
                state,
                approval_id=str(approval.get("id") or ""),
                token=token,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
            )
            result = _post_interactive_approval(
                config,
                subject_id=subject_id,
                fingerprint="ppv-natural-suggest:" + hashlib.sha256(
                    f"{subject_id}|{token}|{message_id}|{regenerated}".encode("utf-8")
                ).hexdigest(),
                token=token,
                body=_question_approval_body(approval, regenerated),
            )
            _save_state(state)
            if str(result.get("status") or "") in {"sent", "duplicate"}:
                _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
            else:
                _post_command_reply(
                    config,
                    message_id,
                    "A nova resposta ficou salva, mas os botoes de decisao nao puderam ser abertos. Peça para gerar novamente.",
                    "BLACK JHON - BOTOES INDISPONIVEIS",
                )
            return True

        response_override = _question_bind_token_draft(token_item, approval) or None
        request_payload = PerguntasAprovacaoRequest(
            approval_id=str(approval.get("id") or ""),
            resposta=response_override,
        )
        if action == "approve":
            result = ppv_endpoints.ml_perguntas_aprovacoes_aprovar(request_payload, client_id)
            resolved = result.get("approval") if isinstance(result, dict) and isinstance(result.get("approval"), dict) else approval
            if str(resolved.get("status") or "sent") not in {"sent", "sent_approved", "sent_manual", "sent_manual_pos_venda"}:
                raise RuntimeError("question_answer_not_confirmed")
            token_item.update({"used": True, "decision": "approve", "decided_at": _now()})
            _question_clear_active_thread(
                state,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
            )
            _save_state(state)
            _post_command_reply(
                config,
                message_id,
                "Resposta enviada ao comprador. A proxima pergunta so sera apresentada depois desta confirmacao.",
                "BLACK JHON - RESPOSTA ENVIADA",
            )
            return True

        approval["whatsapp_suggestion_rejected_at"] = _now()
        approval["whatsapp_suggestion_rejected_by"] = username
        ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        token_item.update({"used": False, "decision": "suggestion_rejected", "decided_at": _now()})
        _question_set_active_thread(
            state,
            approval_id=str(approval.get("id") or ""),
            token=token,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
        _save_state(state)
        _post_command_reply(
            config,
            message_id,
            "Sugestao rejeitada. Nenhuma resposta foi enviada e a pergunta continua ativa. Envie sua orientacao ou solicite outra sugestao.",
            "BLACK JHON - SUGESTAO REJEITADA",
        )
        return True
    except _QuestionResearchPending as pending:
        if isinstance(token_item, dict):
            token_item.update(
                {
                    "used": True,
                    "decision": "research_superseded",
                    "research_job_id": pending.job_id,
                    "research_started_at": _now(),
                }
            )
        _save_state(state)
        _post_command_reply(
            config,
            message_id,
            "Continuo pesquisando em novas fontes ate obter evidencia tecnica suficiente. "
            "Assim que a verificacao estiver concluida, envio a nova sugestao para sua aprovacao. "
            "Nenhuma resposta foi enviada ao comprador.",
            "BLACK JHON - PESQUISA EM ANDAMENTO",
        )
        return True
    except HTTPException as exc:
        _post_command_reply(
            config,
            message_id,
            str(exc.detail or "Nao foi possivel processar esta resposta."),
            "BLACK JHON - RESPOSTA NAO ENVIADA",
        )
        return True
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_natural_action: {str(exc)[:800]}"
        _post_command_reply(
            config,
            message_id,
            "Nao foi possivel aplicar essa instrucao agora. A pergunta atual continua pendente e nenhuma resposta foi enviada.",
            "BLACK JHON - PERGUNTA AINDA PENDENTE",
        )
        return True


def _handle_question_approval_command(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action, token = _question_approval_command(message.get("text_body"))
    if not action:
        return False
    message_id = str(message.get("message_id") or "")
    subject_id = str(message.get("subject_id") or "").strip()
    if not _question_approval_allowed(session.get("permissions") or {}):
        _post_command_reply(config, message_id, "Este usuario nao possui permissao para Perguntas e pos-venda.", "BLACK JHON - ACESSO NEGADO")
        return True
    token_item = _question_approval_lookup(state, token, subject_id=subject_id, session=session)
    if not token_item:
        _post_command_reply(config, message_id, "Esta decisao expirou, ja foi usada ou pertence a outro numero.", "BLACK JHON - DECISAO INVALIDA")
        return True
    client_id = str(session.get("client_id") or "")
    approval_id = str(token_item.get("approval_id") or "")
    try:
        from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest
        from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
        from backend.services import perguntas_pos_venda_state as ppv_state

        approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
        approval = next(
            (
                item for item in approvals
                if isinstance(item, dict)
                and str(item.get("id") or "") == approval_id
                and str(item.get("status") or "pending") in {"pending", "sending"}
            ),
            None,
        )
        if not approval:
            token_item["used"] = True
            _save_state(state)
            _post_command_reply(config, message_id, "A pergunta ja foi resolvida por outro fluxo e nenhuma acao foi repetida.", "BLACK JHON - JA RESOLVIDA")
            return True
        _question_bind_token_draft(token_item, approval)
        if action == "correct":
            token_item.update(
                {
                    "awaiting_correction": True,
                    "correction_requested_at": _now(),
                    "correction_message_id": message_id,
                }
            )
            _question_set_active_thread(
                state,
                approval_id=approval_id,
                token=token,
                subject_id=subject_id,
                client_id=client_id,
                username=str(session.get("username") or ""),
            )
            _save_state(state)
            _post_command_reply(
                config,
                message_id,
                "Envie agora a orientacao para corrigir esta mesma resposta. Depois apresentarei uma nova confirmacao; nada foi enviado ao comprador.",
                "BLACK JHON - INFORME A CORRECAO",
            )
            return True
        if action in {"regenerate", "suggest"}:
            guidance = str(
                token_item.get("user_guidance")
                or approval.get("whatsapp_user_guidance")
                or ""
            ).strip()[:1200]
            regenerated = _regenerate_question_approval_response(
                approval,
                approvals,
                client_id,
                guidance=guidance,
            )
            token, token_item = _question_approval_token(
                state,
                approval=approval,
                subject_id=subject_id,
                client_id=client_id,
                username=str(session.get("username") or ""),
                force_new=True,
                user_guidance=guidance,
            )
            token_item["regenerated_at"] = _now()
            fingerprint = "ppv-regen:" + hashlib.sha256(
                f"{subject_id}|{token}|{message_id}|{regenerated}".encode("utf-8")
            ).hexdigest()
            result = _post_interactive_approval(
                config,
                subject_id=subject_id,
                fingerprint=fingerprint,
                token=token,
                body=_question_approval_body(approval, regenerated),
            )
            _save_state(state)
            if str(result.get("status") or "") in {"sent", "duplicate"}:
                _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
            else:
                _post_command_reply(
                    config,
                    message_id,
                    "A nova resposta ficou salva, mas os botoes de decisao nao puderam ser abertos. Peça para gerar novamente.",
                    "BLACK JHON - BOTOES INDISPONIVEIS",
                )
            return True
        if action == "approve":
            draft, idempotency_key = _question_validate_approval_send(
                token_item,
                approval,
                client_id=client_id,
            )
            request_payload = PerguntasAprovacaoRequest(
                approval_id=approval_id,
                resposta=draft,
                idempotency_key=idempotency_key,
            )
            _bridge_store().audit(
                "mercado_livre_answer_approval_requested",
                message_id=message_id,
                subject_id=subject_id,
                details={
                    "client_id": client_id,
                    "store": str(approval.get("loja") or ""),
                    "question_id": str(token_item.get("question_id") or ""),
                    "draft_hash": str(token_item.get("draft_hash") or ""),
                    "idempotency_key": idempotency_key,
                },
            )
            result = ppv_endpoints.ml_perguntas_aprovacoes_aprovar(request_payload, client_id)
            resolved = result.get("approval") if isinstance(result, dict) and isinstance(result.get("approval"), dict) else {}
            api_status = str(resolved.get("status") or "")
            if api_status == "answered_elsewhere":
                token_item.update({"used": True, "decision": "answered_elsewhere", "decided_at": _now()})
                _question_clear_active_thread(
                    state,
                    subject_id=subject_id,
                    client_id=client_id,
                    username=str(session.get("username") or ""),
                )
                _save_state(state)
                _post_command_reply(
                    config,
                    message_id,
                    "A pergunta ja foi respondida por outro fluxo e nenhuma resposta foi repetida.",
                    "BLACK JHON - JA RESOLVIDA",
                )
                return True
            if api_status not in {"sent", "sent_reconciled", "sent_approved", "sent_manual", "sent_manual_pos_venda"}:
                raise RuntimeError("question_answer_not_confirmed")
            response_text = "Resposta aprovada e enviada ao comprador pelo Mercado Livre."
            response_title = "BLACK JHON - RESPOSTA ENVIADA"
            token_item.update({"used": True, "decision": action, "decided_at": _now()})
            _question_clear_active_thread(
                state,
                subject_id=subject_id,
                client_id=client_id,
                username=str(session.get("username") or ""),
            )
            _bridge_store().audit(
                "mercado_livre_answer_sent",
                message_id=message_id,
                subject_id=subject_id,
                details={
                    "client_id": client_id,
                    "store": str(approval.get("loja") or ""),
                    "question_id": str(token_item.get("question_id") or ""),
                    "draft_hash": str(token_item.get("draft_hash") or ""),
                    "idempotency_key": idempotency_key,
                    "api_status": api_status,
                },
            )
        else:
            approval["whatsapp_suggestion_rejected_at"] = _now()
            approval["whatsapp_suggestion_rejected_by"] = str(session.get("username") or "").strip().lower()
            ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
            token_item.update({"used": False, "decision": "suggestion_rejected", "decided_at": _now()})
            _question_set_active_thread(
                state,
                approval_id=approval_id,
                token=token,
                subject_id=subject_id,
                client_id=client_id,
                username=str(session.get("username") or ""),
            )
            response_text = (
                "Sugestao negada. Nenhuma resposta foi enviada ao comprador e esta pergunta continua ativa. "
                "Envie sua orientacao ou escolha Gerar nova resposta."
            )
            response_title = "BLACK JHON - SUGESTAO NEGADA"
        _save_state(state)
        _post_command_reply(config, message_id, response_text, response_title)
    except _QuestionResearchPending as pending:
        if isinstance(token_item, dict):
            token_item.update(
                {
                    "used": True,
                    "decision": "research_superseded",
                    "research_job_id": pending.job_id,
                    "research_started_at": _now(),
                }
            )
        _save_state(state)
        _post_command_reply(
            config,
            message_id,
            "Continuo pesquisando em novas fontes ate obter evidencia tecnica suficiente. "
            "Quando concluir, envio a nova sugestao para sua aprovacao. Nenhuma resposta foi enviada ao comprador.",
            "BLACK JHON - PESQUISA EM ANDAMENTO",
        )
    except HTTPException as exc:
        _post_command_reply(config, message_id, str(exc.detail or "Nao foi possivel aplicar esta decisao."), "BLACK JHON - DECISAO NAO APLICADA")
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_approval_action: {str(exc)[:800]}"
        _post_command_reply(config, message_id, "Nao foi possivel aplicar a decisao agora. Nada foi enviado ao comprador.", "BLACK JHON - DECISAO NAO APLICADA")
    return True


def _handle_approval_command(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action, code = _approval_command(message.get("text_body"))
    if not action:
        return False
    message_id = str(message.get("message_id") or "")
    subject = str(message.get("subject_id") or "").strip()
    _post_command_reply(
        config,
        message_id,
        "A aprovacao desta tarefa nunca pode ocorrer pelo WhatsApp. Abra o JK Sistema para revisar e aprovar. "
        "A unica mutacao permitida aqui e a resposta ao comprador do Mercado Livre pelo botao tokenizado Aprovar e enviar.",
        "BLACK JHON - APROVACAO SOMENTE NO APLICATIVO",
    )
    return True
    if not session.get("is_full"):
        _post_command_reply(
            config,
            message_id,
            "Este vínculo não possui mais permissão `full` no JK Sistema. Nenhuma execução foi liberada.",
            "🛡️ BLACK JOHN — ACESSO NEGADO",
        )
        return True
    original_message_id, pending = _find_pending_approval(
        state,
        subject_id=subject,
        username=str(session.get("username") or ""),
        client_id=str(session.get("client_id") or ""),
        code=code,
    )
    if not pending:
        _post_command_reply(
            config,
            message_id,
            "O código não existe, já foi usado ou pertence a outro número/usuário. Nada foi executado.",
            "⚠️ BLACK JOHN — CÓDIGO INVÁLIDO",
        )
        return True
    if float(pending.get("approval_expires_at") or 0) < time.time():
        renewed_text = _approval_notice(pending, renewed=True)
        _save_pending(state, original_message_id, pending)
        _post_command_reply(config, message_id, renewed_text, "🔐 BLACK JOHN — NOVO CÓDIGO")
        return True
    try:
        kind = str(pending.get("kind") or "task")
        if action == "approve":
            if kind == "action_proposal":
                result = codex_actions.approve_proposal(
                    str(pending.get("proposal_id") or ""),
                    username=str(session.get("username") or ""),
                    client_id=str(session.get("client_id") or ""),
                    authorization=None,
                    source="whatsapp",
                    wa_id=str(pending.get("wa_id") or ""),
                    proposal_version=int(pending.get("proposal_version") or 1),
                    proposal_hash=str(pending.get("proposal_hash") or ""),
                )
                run = result.get("run") if isinstance(result, dict) and isinstance(result.get("run"), dict) else {}
                pending["run_id"] = str(run.get("run_id") or "")
            else:
                codex_console.codex_aprovar_tarefa_para_sessao(
                    str(pending.get("task_id") or ""),
                    session,
                    codex_console.CodexTaskApprovalRequest(
                        screen_context={
                            "title": "WhatsApp — confirmação móvel",
                            "pathname": "/whatsapp",
                            "modulo_atual": "black_jhon_mobile",
                            "selection": {"subject_id": subject, "approval_code_used": True},
                        }
                    ),
                    approval_source="whatsapp",
                    subject_id=subject,
                )
            pending.update({"approval_used": True, "approved_at": _now(), "approved_by": str(session.get("username") or "")})
            _save_pending(state, original_message_id, pending)
            _post_command_reply(
                config,
                message_id,
                "Confirmação aceita. O Black John iniciou somente o pedido associado a este código e enviará o resultado nesta conversa.",
                "✅ BLACK JOHN — EXECUÇÃO AUTORIZADA",
            )
        else:
            if kind == "action_proposal":
                codex_actions.reject_proposal(
                    str(pending.get("proposal_id") or ""),
                    username=str(session.get("username") or ""),
                    client_id=str(session.get("client_id") or ""),
                    source="whatsapp",
                )
            else:
                codex_console.codex_cancelar_tarefa_para_sessao(
                    str(pending.get("task_id") or ""),
                    session,
                    cancel_source="whatsapp",
                    subject_id=subject,
                )
            _remove_pending(state, original_message_id, status="canceled", reason="cancelado_pelo_usuario")
            _post_command_reply(
                config,
                message_id,
                "Pedido rejeitado. Nenhuma execução foi iniciada para este código.",
                "🚫 BLACK JOHN — PEDIDO REJEITADO",
            )
    except HTTPException as exc:
        detail = str(exc.detail or "Não foi possível aplicar esta decisão.")
        _post_command_reply(config, message_id, detail, "⚠️ BLACK JOHN — DECISÃO NÃO APLICADA")
    except Exception:
        _post_command_reply(
            config,
            message_id,
            "Não foi possível aplicar a decisão agora. O pedido continua bloqueado e nada foi executado.",
            "⚠️ BLACK JOHN — DECISÃO NÃO APLICADA",
        )
    return True


def _provider_tool_summary(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in tool_results[:50]:
        if not isinstance(item, dict):
            continue
        summary = {
            key: item.get(key)
            for key in ("tool_id", "function", "status", "source", "message", "paging", "warnings")
            if item.get(key) not in (None, "", [], {})
        }
        if summary:
            summaries.append(summary)
    return summaries


def _provider_task_attachments(paths: list[str]) -> list[IAChatAttachment]:
    attachments: list[IAChatAttachment] = []
    for raw in paths[:4]:
        try:
            path = Path(str(raw or ""))
            if not path.is_absolute():
                path = (_base_dir() / path).resolve()
            if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
                continue
            mime = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").lower()
            if not mime.startswith("image/"):
                continue
            attachments.append(
                IAChatAttachment(
                    name=path.name,
                    mime_type=mime,
                    data_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
                )
            )
        except Exception:
            continue
    return attachments


def _whatsapp_execute_source_policy_tools(task: dict[str, Any], query_policy: dict[str, Any]) -> list[dict[str, Any]]:
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    required_tools = [str(item or "").strip() for item in (source_policy.get("required_tools") or []) if str(item or "").strip()]
    forbidden_tools = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    if not required_tools:
        return []
    from backend.services import codex_assistant

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
            result = codex_assistant.codex_assistant_execute_tool_call(
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


def _whatsapp_provider_task_worker(task_id: str) -> None:
    task = codex_console._codex_load_task(task_id)
    if not task:
        return
    try:
        from backend.services import ia as ia_service

        codex_console._codex_update_task(
            task_id,
            status="running",
            started_at=codex_console._codex_now(),
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
            codex_console._codex_log(task, f"Consultas auxiliares indisponiveis: {exc}", "warning")

        api_report = _whatsapp_daily_ml_sales_report(
            list(payload.tool_results or []),
            query_policy,
            task.get("prompt"),
        )
        if api_report:
            response = api_report
            model_used = "mercado_livre:api"
        elif ia_service._modelo_eh_vertex_ai(model):
            response = ia_service._chamar_vertex_ai_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"vertex:{ia_service._vertex_modelo_nome_curto(model)}"
        elif ia_service._modelo_eh_gemini_api(model):
            response = ia_service._chamar_gemini_chat(payload, str(task.get("client_id") or "default"))
            model_used = f"gemini:{ia_service._gemini_nome_curto(model)}"
        elif model.startswith("deepseek-"):
            response = ia_service._chamar_deepseek_chat(payload, str(task.get("client_id") or "default"))
            model_used = model
        else:
            response = ia_service._chamar_openai_responses(payload, str(task.get("client_id") or "default"))
            model_used = model
        response = str(response or "").strip()
        if not response:
            raise RuntimeError("A IA selecionada concluiu sem resposta.")
        requested_formats = whatsapp_report_files.requested_report_formats(task.get("prompt") or "")
        chart_outcome = (
            whatsapp_report_visuals.generate_task_chart_artifacts(
                base_info_dir=codex_console._codex_base_info_dir(),
                client_id=task.get("client_id") or "default",
                task_id=task_id,
                prompt=task.get("prompt") or "",
                tool_results=list(payload.tool_results or []),
                query_policy=query_policy,
                max_images=2,
            )
            if "png" in requested_formats
            else {"expected": False, "status": "not_requested", "artifacts": []}
        )
        document_outcome = whatsapp_report_files.generate_report_documents(
            base_info_dir=codex_console._codex_base_info_dir(),
            client_id=task.get("client_id") or "default",
            task_id=task_id,
            prompt=task.get("prompt") or "",
            tool_results=list(payload.tool_results or []),
            query_policy=query_policy,
            formats=requested_formats,
        )
        report_artifacts = [
            *list(chart_outcome.get("artifacts") or []),
            *list(document_outcome.get("artifacts") or []),
        ][:4]
        summaries = _provider_tool_summary(list(payload.tool_results or []))
        codex_console._codex_update_task(
            task_id,
            status="completed",
            completed_at=codex_console._codex_now(),
            final_response=response,
            model=model_used,
            live_status="IA do WhatsApp concluiu.",
            tool_results_summary=summaries,
            sources=list(dict.fromkeys(str(item.get("source") or "") for item in summaries if item.get("source"))),
            whatsapp_artifacts=report_artifacts,
            whatsapp_chart_expected=bool(chart_outcome.get("expected")),
            whatsapp_chart_status=str(chart_outcome.get("status") or "")[:80],
            whatsapp_chart_error=str(chart_outcome.get("error") or "")[:500],
            error="",
        )
    except Exception as exc:
        detail = str(getattr(exc, "detail", "") or exc or "Falha na IA selecionada.")[:2000]
        codex_console._codex_update_task(
            task_id,
            status="failed",
            completed_at=codex_console._codex_now(),
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
        "created_at": codex_console._codex_now(),
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
    with codex_console.CODEX_TASKS_LOCK:
        codex_console.CODEX_TASKS[task_id] = task
        codex_console._codex_persist_task(task)
    codex_console._codex_log(task, f"Tarefa WhatsApp criada com {model} em modo somente leitura.")
    threading.Thread(
        target=_whatsapp_provider_task_worker,
        args=(task_id,),
        name=f"jk-whatsapp-ia-{task_id[:8]}",
        daemon=True,
    ).start()
    return {"success": True, "task": codex_console._codex_public_task(task)}


def _whatsapp_adaptive_reasoning_level(
    settings: dict[str, Any],
    request_text: str,
    query_policy: Optional[dict[str, Any]] = None,
) -> str:
    configured = _normalize_codex_reasoning_effort(settings.get("codex_reasoning_effort"))
    policy = _normalize_codex_reasoning_policy(settings.get("codex_reasoning_policy"))
    maximum = _normalize_codex_reasoning_effort(settings.get("codex_reasoning_max") or configured)
    if policy == "fixed":
        return configured
    ranks = {"low": 0, "medium": 1, "high": 2, "xhigh": 3}
    normalized = unicodedata.normalize("NFKD", str(request_text or "")).encode("ascii", "ignore").decode("ascii").lower()
    query_policy = query_policy if isinstance(query_policy, dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    domains = [str(item) for item in (query_policy.get("domains") or [])]
    providers = [str(item) for item in (source_policy.get("providers") or [])]
    level = "low"
    if query_policy or re.search(r"\b(estoque|pedido|venda|anuncio|devolucao|reclamacao|sku|produto)\b", normalized):
        level = "medium"
    if (
        query_policy.get("store_mode") == "all"
        or len(domains) > 1
        or len(providers) > 1
        or re.search(r"\b(relatorio|compare|comparacao|analise|periodo|todas as lojas|historico completo)\b", normalized)
    ):
        level = "high"
    if re.search(
        r"\b(fontes divergentes|dados conflitantes|estrategia|planeje|plano completo|risco|simule|cenario|corrija a falha|investigue profundamente)\b",
        normalized,
    ):
        level = "xhigh"
    if ranks[level] > ranks[maximum]:
        level = maximum
    return level


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
    incoming_metadata = dict(channel_metadata or {})
    query_policy = (
        incoming_metadata.get("query_policy")
        if isinstance(incoming_metadata.get("query_policy"), dict)
        else {}
    )
    request_text = str(incoming_metadata.get("request_text") or prompt or "")
    reasoning_level = _whatsapp_adaptive_reasoning_level(settings, request_text, query_policy)
    report_mode = codex_console._codex_agent_is_report_request(request_text)
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
    default_deadline = _job_deadline_seconds(
        request_text,
        requires_web=bool(incoming_metadata.get("allow_web_search") or incoming_metadata.get("web_search_requested")),
    )
    try:
        requested_deadline = int(incoming_metadata.get("deadline_seconds") or default_deadline)
    except (TypeError, ValueError):
        requested_deadline = default_deadline
    channel_metadata = {
        **incoming_metadata,
        "ai_model": settings["model"],
        "ai_provider": settings["provider"],
        "codex_reasoning_effort": reasoning_level,
        "reasoning_level": reasoning_level,
        "reasoning_policy": settings["codex_reasoning_policy"],
        "reasoning_max": settings["codex_reasoning_max"],
        "codex_speed": WHATSAPP_CODEX_SPEED_DEFAULT,
        "codex_service_tier": WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        "orchestration_profile": (
            "whatsapp_dual_codex_worker" if is_dual_codex_worker else "whatsapp_full_agent"
        ),
        "deadline_seconds": max(30, min(requested_deadline, 600)),
        "admin_configured_ai": True,
    }
    if settings["provider"] != "codex":
        return _create_provider_task(
            model=settings["model"],
            prompt=prompt,
            session=session,
            conversation_id=conversation_id,
            paths=paths,
            screen_context=screen_context,
            channel_metadata=channel_metadata,
        )
    codex_model = settings["model"].split(":", 1)[1]
    payload = codex_console.CodexTaskRequest(
        prompt=prompt,
        sandbox="read_only" if safe_read_only else ("workspace_write" if mobile_full_access else "read_only"),
        approval_mode="read_only" if safe_read_only else ("request" if mobile_full_access else "read_only"),
        conversation_id=conversation_id,
        paths=paths,
        screen_context=screen_context,
        model=codex_model,
        reasoning_effort=reasoning_level,
        speed=WHATSAPP_CODEX_SPEED_DEFAULT,
        service_tier=WHATSAPP_CODEX_SERVICE_TIER_DEFAULT,
        request_id=str(channel_metadata.get("message_id") or uuid.uuid4().hex),
    )
    return codex_console.codex_criar_tarefa_para_sessao(
        payload,
        session,
        origin="whatsapp",
        channel_metadata=channel_metadata,
    )


def _process_message(config: dict[str, Any], state: dict[str, Any], message: dict[str, Any]) -> None:
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return
    if str(message.get("machine_id") or "") != str(config.get("machine_id") or ""):
        raise RuntimeError("message_not_owned_by_this_machine")
    _start_typing_pulse(config, message_id)
    existing = _pending_task_for_message(state, message_id)
    if existing:
        _complete_pending(config, state, message_id, existing)
        return
    subject = str(message.get("subject_id") or "").strip()
    if subject and subject != str(config.get("subject_id") or ""):
        # The dispatcher gives each phone an isolated config snapshot.  Do not
        # persist the last phone globally, otherwise concurrent conversations
        # can overwrite each other's binding identity.
        config["subject_id"] = subject
        config["personal_phone"] = str(message.get("wa_id") or "")
    session = _reload_bound_session(config, message)
    phone_settings = _phone_notification_settings(
        config,
        subject,
        client_id=session.get("client_id"),
        username=session.get("username"),
    )
    phone_ai_behavior = _normalize_phone_ai_behavior(phone_settings.get("ai_behavior"))
    phone = ""
    conversation_id = ""
    media: Optional[dict[str, Any]] = None
    transcription: Optional[dict[str, Any]] = None
    if message.get("media_id") or message.get("media_object_key"):
        phone = _message_phone(config, message)
        if not phone:
            raise RuntimeError("whatsapp_phone_identity_missing")
        conversation_id = _conversation_id(config, message)
        media = _download_media(config, message, conversation_id)
        if str(media.get("mime_type") or "") in SUPPORTED_AUDIO_MIMES:
            path = (_base_dir() / str(media.get("path") or "")).resolve()
            transcription = _transcribe_audio(path)
    request_text = _message_request_text(message, transcription)
    action_message = {**message, "text_body": request_text}
    if transcription and transcription.get("success") and request_text:
        action_message["message_type"] = "text"

    # Audios precisam ser transcritos antes de interpretar os botoes e as
    # orientacoes da pergunta ativa. Caso contrario, a fala cai na conversa
    # geral e deixa de atualizar o rascunho que sera aprovado no Mercado Livre.
    if _handle_question_approval_command(config, state, action_message, session):
        return
    if _handle_question_natural_language(config, state, action_message, session):
        return
    if _handle_approval_command(config, state, action_message, session):
        return
    message = action_message
    if not phone:
        phone = _message_phone(config, message)
    if not phone:
        raise RuntimeError("whatsapp_phone_identity_missing")
    if not conversation_id:
        conversation_id = _conversation_id(config, message)
    mobile_full_access = bool(session.get("is_full") and (session.get("permissions") or {}).get("full") is True)
    selection_token = _whatsapp_store_selection_token(request_text)
    if selection_token:
        selection = _whatsapp_consume_store_selection(
            state,
            selection_token,
            subject_id=subject,
            session=session,
            conversation_id=conversation_id,
        )
        if not selection:
            _post_command_reply(
                config,
                message_id,
                "Essa escolha de loja expirou ou ja foi utilizada. Envie novamente a sua pergunta para escolher outra vez.",
                "BLACK JOHN - ESCOLHA EXPIRADA",
            )
            return
        if str(selection.get("store_mode") or "single") == "all":
            selected_stores = [
                str(store or "").strip()
                for store in (selection.get("stores") or [])
                if str(store or "").strip()
            ]
            request_text = (
                f"{str(selection.get('request_text') or '').strip()}\n\n"
                "Selecao confirmada: todas as lojas. "
                f"Consulte separadamente estas lojas: {', '.join(selected_stores)}. "
                "Nao some nem misture os totais entre lojas."
            ).strip()
        else:
            request_text = (
                f"{str(selection.get('request_text') or '').strip()}\n\n"
                f"Loja selecionada: {str(selection.get('store') or '').strip()}"
            ).strip()
        message = {**message, "text_body": request_text, "message_type": "text"}
    if str(config.get("agent_architecture") or "").strip().lower() == "dual_codex":
        _process_dual_codex_message(
            config,
            state,
            message,
            session=session,
            conversation_id=conversation_id,
            message_id=message_id,
            subject=subject,
            phone=phone,
            request_text=request_text,
            media=media,
            transcription=transcription,
            phone_ai_behavior=phone_ai_behavior,
        )
        return
    if (
        _whatsapp_ai_settings(config).get("provider") == "codex"
        and str(config.get("active_task_policy") or "steer_or_queue") == "steer_or_queue"
        and _whatsapp_is_task_complement(request_text)
    ):
        _, active_pending, active_task = _active_pending_for_conversation(
            state,
            conversation_id,
            exclude_message_id=message_id,
        )
        if active_task:
            steer_result = codex_console.codex_complementar_tarefa_para_sessao(
                str(active_task.get("task_id") or ""),
                request_text,
                session,
                request_id=message_id,
                subject_id=subject,
                wa_id=phone,
            )
            if steer_result.get("accepted") is True:
                active_request = str(active_pending.get("request_text") or "").strip()
                response = "Incluí esta informação na consulta em andamento."
                if active_request:
                    response += f" Pedido em análise: {active_request[:220]}"
                _post_message_result(
                    config,
                    message_id,
                    {
                        "status": "completed",
                        "task_id": str(active_task.get("task_id") or ""),
                        "response": response,
                    },
                )
                return

    general_answer = _whatsapp_general_answer_request(request_text, session)
    protected_mutation_domains = [] if general_answer else _whatsapp_protected_mutation_domains(request_text)
    if protected_mutation_domains and not mobile_full_access:
        _post_command_reply(
            config,
            message_id,
            _whatsapp_query_only_block_text(protected_mutation_domains),
            "BLACK JOHN — SOMENTE CONSULTA",
        )
        return
    pagination_request = _whatsapp_pagination_request(request_text)
    contextual_report_request = _whatsapp_contextual_report_request(request_text)
    direct_query_policy = {} if pagination_request or general_answer else _whatsapp_query_policy(request_text, session)
    inherited_store_policy = (
        {}
        if pagination_request or contextual_report_request
        else _whatsapp_inherit_query_store_context(
            request_text,
            direct_query_policy,
            state,
            conversation_id,
            session,
        )
    )
    direct_scope_resolved = bool(
        direct_query_policy
        and (
            not direct_query_policy.get("store_required")
            or direct_query_policy.get("store_mode") == "all"
            or len(direct_query_policy.get("store_matches") or []) == 1
        )
    )
    if pagination_request:
        query_policy = _whatsapp_query_continuation_policy(request_text, state, conversation_id, session)
    elif direct_scope_resolved:
        query_policy = direct_query_policy
    elif contextual_report_request:
        query_policy = (
            _whatsapp_query_continuation_policy(request_text, state, conversation_id, session)
            or direct_query_policy
        )
    elif inherited_store_policy:
        query_policy = inherited_store_policy
    else:
        query_policy = direct_query_policy
    # Uma pergunta curta como "e o motivo?" pode depender da consulta anterior.
    # Nesse caso, o contexto operacional confirmado prevalece sobre a heuristica
    # de conversa geral.
    general_answer = bool(general_answer and not query_policy)
    if not general_answer and not query_policy and not _whatsapp_mutation_intent(request_text):
        query_policy = _whatsapp_store_scope_policy(request_text, session)
    if (pagination_request or contextual_report_request) and not query_policy:
        _post_command_reply(
            config,
            message_id,
            "Nao encontrei uma consulta anterior recente nesta conversa. Repita o pedido informando os filtros e, para API, a loja exata.",
            "BLACK JOHN — REPITA A CONSULTA",
        )
        return
    if query_policy.get("no_more_results") is True:
        _post_command_reply(
            config,
            message_id,
            "A consulta anterior ja chegou ao fim dos resultados retornados pelas APIs. Para iniciar outra busca, envie novamente os filtros e a loja.",
            "BLACK JOHN — FIM DOS RESULTADOS",
        )
        return
    missing_store = bool(
        query_policy.get("store_required")
        and query_policy.get("store_mode") != "all"
        and len(query_policy.get("store_matches") or []) != 1
    )
    mutation_stores = _whatsapp_session_stores(session) if not query_policy else []
    mutation_missing_store = bool(
        not query_policy
        and _whatsapp_mutation_intent(request_text)
        and _whatsapp_store_scoped_request(request_text)
        and len(_whatsapp_exact_store_matches(request_text, mutation_stores)) != 1
    )
    if missing_store or mutation_missing_store:
        stores = list(query_policy.get("authorized_stores") or []) if missing_store else mutation_stores
        if _whatsapp_send_store_selection(
            config,
            state,
            message,
            session,
            conversation_id,
            request_text,
            stores,
            allow_all=missing_store,
        ):
            return
        fallback_policy = query_policy or {
            "authorized_stores": stores,
            "store_matches": _whatsapp_exact_store_matches(request_text, stores),
        }
        _post_command_reply(config, message_id, _whatsapp_store_required_text(fallback_policy), "BLACK JOHN — INFORME A LOJA")
        return
    screen_context = _mobile_screen_context(message_id, subject, media, transcription, query_policy)
    prompt = _message_prompt(
        message,
        media,
        transcription,
        mobile_full_access=mobile_full_access,
        query_policy=query_policy,
        ai_behavior=phone_ai_behavior,
        general_answer=general_answer,
    )
    paths = [str(media.get("path"))] if media else []
    query_only = query_policy.get("mode") == "query_only"
    readonly_inquiry = _whatsapp_readonly_inquiry(request_text)
    safe_read_only = bool(general_answer or query_only or readonly_inquiry)
    result = _create_selected_ai_task(
        config,
        prompt=prompt,
        session=session,
        conversation_id=conversation_id,
        paths=paths,
        screen_context=screen_context,
        safe_read_only=safe_read_only,
        mobile_full_access=mobile_full_access,
        channel_metadata={
            "message_id": message_id,
            "subject_id": subject,
            "wa_id": phone,
            "message_type": str(message.get("message_type") or "text"),
            "media": media or {},
            "transcription": transcription or {},
            "received_at": message.get("received_at"),
            "mobile_full_access": mobile_full_access,
            "query_policy": query_policy,
            "phone_ai_behavior": phone_ai_behavior,
            "general_answer": general_answer,
            "request_text": request_text,
        },
    )
    task = result.get("task") if isinstance(result, dict) else {}
    task_proposal = task.get("proposal") if isinstance((task or {}).get("proposal"), dict) else {}
    app_confirmation_only = bool(
        task_proposal
        and (
            task_proposal.get("requires_app_confirmation") is True
            or "whatsapp" not in list(task_proposal.get("channels_allowed") or [])
        )
    )
    _whatsapp_remember_query_context(state, conversation_id, request_text, query_policy)
    pending = {
        "task_id": str((task or {}).get("task_id") or ""),
        "kind": "task",
        "conversation_id": conversation_id,
        "subject_id": subject,
        "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(),
        "request_text": request_text or "Pedido com anexo recebido pelo WhatsApp.",
        "mobile_full_access": mobile_full_access,
        "query_policy": query_policy,
        "general_answer": general_answer,
        "created_at": _now(),
        "awaiting_notified": app_confirmation_only,
        "trusted_bound_number": mobile_full_access,
        "proposal_id": str(task_proposal.get("proposal_id") or ""),
        "proposal_version": int(task_proposal.get("version") or 1),
        "proposal_hash": str(task_proposal.get("proposal_hash") or ""),
        "action_summary": str(task_proposal.get("summary") or task_proposal.get("title") or ""),
        "risk": str(task_proposal.get("risk") or ""),
        "wa_id": phone,
    }
    _save_pending(state, message_id, pending)
    if _whatsapp_ai_settings(config).get("provider") == "codex":
        _start_progress_pulse(config, message_id, str((task or {}).get("task_id") or ""))
    if app_confirmation_only:
        parts = _whatsapp_response_parts(
            (
                "A proposta foi preparada, mas esta ação só pode ser confirmada no aplicativo JK Sistema. "
                f"Identificador: `{str(task_proposal.get('proposal_id') or (task or {}).get('task_id') or '')}`."
            ),
            "BLACK JHON - CONFIRME NO APLICATIVO",
        )
        _post_message_result(
            config,
            message_id,
            {
                "status": "completed",
                "task_id": str((task or {}).get("task_id") or ""),
                "response": parts[0],
                "response_parts": parts,
            },
        )
        _remove_pending(state, message_id)
        return
    _complete_pending(config, state, message_id, pending)


def _timing_epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text) / (1000.0 if float(text) > 10_000_000_000 else 1.0)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _record_latency(stage: str, seconds: float) -> None:
    if stage not in PHONE_LATENCY_SAMPLES or seconds < 0 or seconds > 24 * 3600:
        return
    with PHONE_DISPATCH_LOCK:
        PHONE_LATENCY_SAMPLES[stage].append(float(seconds))


def _record_message_timing(message_id: str, **values: Any) -> None:
    if not message_id:
        return
    try:
        state = _load_state()
        with BRIDGE_STATE_LOCK:
            timings = state.get("bridge_message_timings") if isinstance(state.get("bridge_message_timings"), dict) else {}
            item = timings.get(message_id) if isinstance(timings.get(message_id), dict) else {}
            item = dict(item)
            for key, value in values.items():
                if value not in (None, ""):
                    item[str(key)[:60]] = value
            timings[message_id] = item
            if len(timings) > 500:
                ordered = sorted(
                    timings.items(),
                    key=lambda pair: str((pair[1] or {}).get("claimed_at") or ""),
                )[-500:]
                timings = dict(ordered)
            state["bridge_message_timings"] = timings
            _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["timing_last_error"] = str(exc)[:500]


def _dispatch_phone_key(config: dict[str, Any], *, message: Optional[dict[str, Any]] = None, pending: Optional[dict[str, Any]] = None) -> str:
    raw = ""
    if isinstance(message, dict):
        raw = _message_phone(config, message) or str(message.get("subject_id") or "")
        identity = "|".join(
            (
                str(message.get("client_id") or config.get("client_id") or ""),
                str(message.get("username") or config.get("username") or "").lower(),
                raw,
            )
        )
    else:
        value = pending if isinstance(pending, dict) else {}
        raw = str(value.get("wa_id") or value.get("subject_id") or value.get("conversation_id") or "")
        identity = "|".join(
            (
                str(value.get("client_id") or config.get("client_id") or ""),
                str(value.get("username") or config.get("username") or "").lower(),
                raw,
            )
        )
    if not raw:
        identity += "|unknown"
    return hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()


def _configure_phone_dispatcher(worker_count: int) -> None:
    global PHONE_DISPATCH_EXECUTOR, PHONE_DISPATCH_EXECUTOR_WORKERS, PHONE_DISPATCH_ACCEPTING
    workers = _normalize_capacity(worker_count, WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT, 1, 8)
    previous: Optional[concurrent.futures.ThreadPoolExecutor] = None
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_ACCEPTING = True
        if PHONE_DISPATCH_EXECUTOR is not None and PHONE_DISPATCH_EXECUTOR_WORKERS == workers:
            return
        replacement = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="jk-whatsapp-phone",
        )
        previous = PHONE_DISPATCH_EXECUTOR
        PHONE_DISPATCH_EXECUTOR = replacement
        PHONE_DISPATCH_EXECUTOR_WORKERS = workers
        if previous is not None:
            PHONE_DISPATCH_OLD_EXECUTORS.append(previous)
    if previous is not None:
        previous.shutdown(wait=False, cancel_futures=False)


def _phone_dispatch_capacity() -> int:
    with PHONE_DISPATCH_LOCK:
        queued = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        return max(0, WHATSAPP_LOCAL_QUEUE_CAPACITY - queued - PHONE_DISPATCH_INFLIGHT)


def _phone_dispatch_done(future: concurrent.futures.Future[Any]) -> None:
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_FUTURES.discard(future)


def _enqueue_phone_event(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    kind: str,
    event_id: str,
    message: Optional[dict[str, Any]] = None,
    pending: Optional[dict[str, Any]] = None,
    message_id: str = "",
) -> bool:
    priority = {"inbound": 0, "worker_result": 1, "waiting_tick": 2}.get(kind, 2)
    if kind == "inbound":
        inbound_text = str((message or {}).get("text_body") or "").strip()
        if QUESTION_APPROVAL_COMMAND_RE.match(inbound_text) or re.search(
            r"\b(corrija|corrigir|aprove|aprovar|negar|negue|gerar outra resposta)\b",
            _whatsapp_text_key(inbound_text),
        ):
            priority = -2
        elif whatsapp_report_files.report_requested(inbound_text):
            priority = 1
    key = _dispatch_phone_key(config, message=message, pending=pending)
    dedupe_id = f"{kind}:{event_id}"
    tick_id = f"tick:{str((pending or {}).get('task_id') or event_id)}" if kind == "waiting_tick" else ""
    with PHONE_DISPATCH_LOCK:
        if not PHONE_DISPATCH_ACCEPTING:
            return False
        if dedupe_id in PHONE_DISPATCH_EVENT_IDS or (tick_id and tick_id in PHONE_DISPATCH_TICK_IDS):
            return True
        queued = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        if queued + PHONE_DISPATCH_INFLIGHT >= WHATSAPP_LOCAL_QUEUE_CAPACITY:
            return False
        if PHONE_DISPATCH_EXECUTOR is None:
            _configure_phone_dispatcher(_whatsapp_dual_agent_settings(config)["conversation_worker_count"])
        event = {
            "kind": kind,
            "event_id": event_id,
            "dedupe_id": dedupe_id,
            "tick_id": tick_id,
            "config": dict(config),
            "state": state,
            "message": dict(message or {}),
            "pending": dict(pending or {}),
            "message_id": str(message_id or (message or {}).get("message_id") or ""),
            "claimed_epoch": time.time(),
        }
        queue = PHONE_DISPATCH_QUEUES.setdefault(key, [])
        heapq.heappush(queue, (priority, next(PHONE_DISPATCH_SEQUENCE), event))
        PHONE_DISPATCH_EVENT_IDS.add(dedupe_id)
        if tick_id:
            PHONE_DISPATCH_TICK_IDS.add(tick_id)
        if key not in PHONE_DISPATCH_ACTIVE:
            PHONE_DISPATCH_ACTIVE.add(key)
            assert PHONE_DISPATCH_EXECUTOR is not None
            future = PHONE_DISPATCH_EXECUTOR.submit(_drain_phone_events, key)
            PHONE_DISPATCH_FUTURES.add(future)
            future.add_done_callback(_phone_dispatch_done)
    return True


def _finish_phone_event(event: dict[str, Any]) -> None:
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_EVENT_IDS.discard(str(event.get("dedupe_id") or ""))
        tick_id = str(event.get("tick_id") or "")
        if tick_id:
            PHONE_DISPATCH_TICK_IDS.discard(tick_id)


def _process_phone_event(event: dict[str, Any]) -> None:
    kind = str(event.get("kind") or "")
    config = event.get("config") if isinstance(event.get("config"), dict) else {}
    state = event.get("state") if isinstance(event.get("state"), dict) else _load_state()
    message_id = str(event.get("message_id") or "")
    if kind == "inbound":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        started = time.time()
        received_epoch = _timing_epoch(message.get("received_at"))
        claimed_epoch = float(event.get("claimed_epoch") or started)
        _record_message_timing(
            message_id,
            received_at=message.get("received_at") or "",
            claimed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(claimed_epoch)),
            luna_started_at=_now(),
        )
        if received_epoch:
            _record_latency("gateway_to_luna", started - received_epoch)
        _record_latency("claimed_to_luna", started - claimed_epoch)
        try:
            _process_message(config, state, message)
            _record_message_timing(message_id, luna_completed_at=_now())
        except Exception as exc:
            detail = str(exc)[:1000]
            RUNTIME_STATE["last_error"] = detail
            _record_message_timing(message_id, luna_completed_at=_now(), error=detail)
            try:
                error_class, retryable = _dual_retry_classification(detail)
                status = "retry" if retryable and str(config.get("agent_architecture") or "").lower() == "dual_codex" else "failed"
                _post_message_result(
                    config,
                    message_id,
                    {
                        "status": status,
                        "error": detail,
                        "response": "" if status == "retry" else (
                            "Nao consegui concluir esta solicitacao com seguranca. "
                            f"Motivo: {error_class}. Nenhuma alteracao foi executada."
                        ),
                    },
                )
            except Exception:
                pass
        finally:
            _record_latency("luna_duration", time.time() - started)
        return

    with BRIDGE_STATE_LOCK:
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        current = pending_messages.get(message_id)
        current = dict(current) if isinstance(current, dict) else {}
    if not current:
        return
    try:
        _complete_pending(config, state, message_id, current)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = str(exc)[:1000]


def _drain_phone_events(phone_key: str) -> None:
    global PHONE_DISPATCH_INFLIGHT
    while True:
        with PHONE_DISPATCH_LOCK:
            queue = PHONE_DISPATCH_QUEUES.get(phone_key) or []
            if not queue:
                PHONE_DISPATCH_QUEUES.pop(phone_key, None)
                PHONE_DISPATCH_ACTIVE.discard(phone_key)
                return
            _, _, event = heapq.heappop(queue)
            PHONE_DISPATCH_INFLIGHT += 1
        try:
            _process_phone_event(event)
        finally:
            _finish_phone_event(event)
            with PHONE_DISPATCH_LOCK:
                PHONE_DISPATCH_INFLIGHT = max(0, PHONE_DISPATCH_INFLIGHT - 1)


def _dual_waiting_tick_due(config: dict[str, Any], state: dict[str, Any], pending: dict[str, Any], task: dict[str, Any]) -> bool:
    if str(task.get("status") or "") not in {"queued", "running"}:
        return False
    settings = _whatsapp_dual_agent_settings(config)
    notice_count = int(pending.get("wait_notice_count") or 0)
    interval = int(
        settings.get("wait_message_after_seconds")
        if notice_count == 0
        else settings.get("wait_message_repeat_seconds")
        if notice_count == 1
        else settings.get("wait_message_steady_seconds")
    )
    reference = float(pending.get("last_wait_notice_at_epoch") or pending.get("created_at_epoch") or time.time())
    return time.time() - reference >= interval


def _expire_dual_pending_if_due(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
) -> bool:
    changed = _dual_migrate_pending_v7(pending)
    if changed:
        _save_pending(state, message_id, pending)
    if str(pending.get("job_state") or "") == "partial":
        return _terminate_pending_partial(
            config,
            state,
            message_id,
            pending,
            reason=str(pending.get("terminal_reason") or "resultado_parcial"),
        )
    deadline = float(pending.get("deadline_at_epoch") or 0)
    if not deadline or time.time() < deadline:
        return False
    for task_id in _pending_task_ids(pending):
        try:
            task = codex_console._codex_load_task(task_id) or {}
            if str(task.get("status") or "") not in {"queued", "running", "cancel_requested"}:
                continue
            codex_console._codex_interrupt_active_turn(task_id)
            codex_console._codex_update_task(
                task_id,
                status="canceled",
                cancel_source="whatsapp_deadline",
                canceled_at=_now(),
                handoff_status="partial_terminal",
                delivery_state="deadline_exceeded",
            )
        except Exception:
            continue
    pending["deadline_exceeded_at"] = _now()
    return _terminate_pending_partial(
        config,
        state,
        message_id,
        pending,
        reason="O tempo máximo desta consulta foi atingido.",
    )


def _retry_dual_pending_interrupts(pending: dict[str, Any]) -> int:
    interrupted = 0
    for task_id in _pending_task_ids(pending):
        task = codex_console._codex_load_task(task_id) or {}
        if str(task.get("status") or "") != "cancel_requested":
            continue
        if codex_console._codex_interrupt_active_turn(task_id):
            interrupted += 1
            codex_console._codex_update_task(
                task_id,
                interrupt_requested=True,
                interrupt_requested_at=_now(),
            )
    return interrupted


def _monitor_pending(config: dict[str, Any], state: dict[str, Any]) -> None:
    with BRIDGE_STATE_LOCK:
        pending = dict(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, item in list(pending.items())[:100]:
        if not isinstance(item, dict):
            continue
        try:
            kind = str(item.get("kind") or "")
            if kind == "dual_function_manager":
                manager_state = str(item.get("manager_state") or "queued")
                if _expire_dual_pending_if_due(config, state, str(message_id), item):
                    continue
                if manager_state == "partial" or str(item.get("job_state") or "") == "partial":
                    _terminate_pending_partial(
                        config,
                        state,
                        str(message_id),
                        item,
                        reason=str(item.get("terminal_reason") or item.get("manager_retry_reason") or "resultado_parcial"),
                    )
                    continue
                due = float(item.get("manager_next_retry_at_epoch") or 0) <= time.time()
                if manager_state == "queued" or (manager_state == "waiting_retry" and due):
                    item.update({"manager_state": "queued", "job_state": "manager_queued"})
                    _save_pending(state, str(message_id), item)
                    _submit_function_manager_job(config, state, str(message_id))
                if manager_state in {"running", "queued", "partial", "waiting_retry"}:
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": "running" if manager_state == "running" else "queued",
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                continue
            if str(item.get("kind") or "") in {"dual_worker", "dual_job_group"}:
                if _expire_dual_pending_if_due(config, state, str(message_id), item):
                    continue
                _dual_retry_pending_due(config, state, str(message_id), item)
                _retry_dual_pending_interrupts(item)
                _maybe_send_dual_auth_notice(config, state, str(message_id), item)
            if _whatsapp_ai_settings(config).get("provider") == "codex":
                _start_progress_pulse(config, str(message_id), str(item.get("task_id") or ""))
            kind = str(item.get("kind") or "")
            if kind not in {"dual_worker", "dual_job_group"}:
                _complete_pending(config, state, str(message_id), item)
                continue
            if kind == "dual_job_group":
                tasks = _pending_codex_tasks(item)
                if not tasks:
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": "queued",
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                    continue
                statuses = {str(task.get("status") or "") for task in tasks}
                terminal = {"completed", "partial", "failed", "canceled"}
                if statuses & terminal:
                    signature = hashlib.sha256(
                        "|".join(
                            sorted(f"{task.get('task_id')}:{task.get('status')}" for task in tasks)
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    _enqueue_phone_event(
                        config,
                        state,
                        kind="worker_result",
                        event_id=f"{item.get('job_group_id')}:{signature}",
                        pending=item,
                        message_id=str(message_id),
                    )
                else:
                    synthetic_status = "running" if "running" in statuses else "queued"
                    synthetic = {
                        "task_id": str(item.get("job_group_id") or message_id),
                        "status": synthetic_status,
                    }
                    if _dual_waiting_tick_due(config, state, item, synthetic):
                        _enqueue_phone_event(
                            config,
                            state,
                            kind="waiting_tick",
                            event_id=str(item.get("job_group_id") or message_id),
                            pending=item,
                            message_id=str(message_id),
                        )
                continue
            task_id = str(item.get("task_id") or "")
            task = codex_console._codex_load_task(task_id) if task_id else None
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "")
            if status in {"completed", "partial", "failed", "canceled"}:
                _enqueue_phone_event(
                    config,
                    state,
                    kind="worker_result",
                    event_id=f"{task_id}:{status}",
                    pending=item,
                    message_id=str(message_id),
                )
            elif _dual_waiting_tick_due(config, state, item, task):
                _enqueue_phone_event(
                    config,
                    state,
                    kind="waiting_tick",
                    event_id=task_id,
                    pending=item,
                    message_id=str(message_id),
                )
        except Exception as exc:
            RUNTIME_STATE["last_error"] = str(exc)[:1000]


def _forward_task_transitions(config: dict[str, Any], state: dict[str, Any]) -> None:
    """Atualiza o espelho de estados sem atravessar tarefas entre canais.

    Respostas de tarefas WhatsApp sao entregues por ``_complete_pending`` ao
    ``message_id``/telefone que as originou. Tarefas do aplicativo pertencem
    exclusivamente ao sidebar e nunca podem virar notificacao proativa.
    """
    statuses = state.get("task_statuses") if isinstance(state.get("task_statuses"), dict) else {}
    try:
        paths = sorted(Path(codex_console._codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]
    except Exception:
        paths = []
    for path in paths:
        task = _json_read(path, {})
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(config.get("client_id") or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != str(config.get("username") or "").strip().lower():
            continue
        task_id = str(task.get("task_id") or path.stem)
        status = str(task.get("status") or "")
        statuses[task_id] = status
    state["task_statuses"] = dict(list(statuses.items())[-1000:])
    _save_state(state)


def _whatsapp_week_key(now: Optional[datetime] = None) -> str:
    current = now or datetime.now()
    iso = current.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _whatsapp_month_key(now: Optional[datetime] = None) -> str:
    current = now or datetime.now()
    return current.strftime("%Y-%m")


def _send_weekly_visual(
    config: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    week_key: str,
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    outcome = whatsapp_report_visuals.generate_weekly_chart_artifacts(
        base_info_dir=_info_dir(),
        client_id=client_id,
        week_key=week_key,
        chart_data=chart_data,
    )
    artifacts = [item for item in (outcome.get("artifacts") or []) if isinstance(item, dict)]
    if not artifacts:
        return {"success": False, "status": outcome.get("status") or "generation_empty", "error": outcome.get("error")}
    artifact = artifacts[0]
    path = _whatsapp_report_chart_path(artifact, client_id)
    if not path:
        return {"success": False, "status": "invalid_artifact", "error": "report_chart_invalid_or_expired"}
    try:
        return _post_proactive_image(
            config,
            subject_id=subject_id,
            fingerprint=f"weekly-report:{client_id}:{week_key}",
            path=path,
            caption="",
            filename=path.name,
        )
    except Exception as exc:
        return {"success": False, "status": "send_failed", "error": str(exc)[:500]}
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _remember_pending_weekly_visual(
    state: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    week_key: str,
    chart_data: dict[str, Any],
    last_status: str,
) -> None:
    pending = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
    fingerprint = f"weekly-report:{client_id}:{week_key}"
    pending[fingerprint] = {
        "client_id": str(client_id or "default"),
        "subject_id": str(subject_id or ""),
        "week_key": str(week_key or ""),
        "chart_data": dict(chart_data or {}),
        "created_at": time.time(),
        "expires_at": time.time() + 8 * 24 * 60 * 60,
        "last_status": str(last_status or "waiting_free_window")[:80],
        "last_attempt_at": time.time(),
    }
    # A semana nova substitui especificacoes antigas do mesmo cliente.
    state["pending_weekly_visuals"] = {
        key: value
        for key, value in pending.items()
        if isinstance(value, dict)
        and (key == fingerprint or str(value.get("client_id") or "") != str(client_id or "default"))
    }


def _flush_pending_weekly_visuals(config: dict[str, Any], state: dict[str, Any]) -> None:
    pending = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
    if not pending:
        return
    now = time.time()
    changed = False
    for fingerprint, item in list(pending.items()):
        if not isinstance(item, dict) or float(item.get("expires_at") or 0) <= now:
            pending.pop(fingerprint, None)
            changed = True
            continue
        if now - float(item.get("last_attempt_at") or 0) < 60:
            continue
        result = _send_weekly_visual(
            config,
            client_id=str(item.get("client_id") or config.get("client_id") or "default"),
            subject_id=str(item.get("subject_id") or config.get("subject_id") or ""),
            week_key=str(item.get("week_key") or ""),
            chart_data=dict(item.get("chart_data") or {}),
        )
        changed = True
        if result.get("success") or result.get("duplicate"):
            pending.pop(fingerprint, None)
            state["last_weekly_visual_sent_at"] = _now()
            state["last_weekly_visual_status"] = str(result.get("status") or "sent")[:80]
        else:
            item["last_attempt_at"] = now
            item["last_status"] = str(result.get("status") or result.get("error") or "send_failed")[:80]
            pending[fingerprint] = item
    state["pending_weekly_visuals"] = pending
    if changed:
        _save_state(state)


def _whatsapp_compact_alert_detail(value: Any, limit: int = 520) -> str:
    text = _whatsapp_clean_markdown(value)
    text = re.sub(r"(?im)^\s*(?:fonte|consultado em|atualizado em)\s*:.*$", "", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -•")
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    cut = cut if cut >= max(120, limit // 2) else limit - 1
    return text[:cut].rstrip(" ,;:-") + "…"


def _whatsapp_weekly_operational_parts(suggestions: list[dict[str, Any]], week_key: str) -> list[str]:
    important = [item for item in suggestions if isinstance(item, dict)][:6]
    critical_count = sum(1 for item in important if str(item.get("severity") or "").lower() == "critical")
    high_count = sum(1 for item in important if str(item.get("severity") or "").lower() in {"high", "warning"})
    lines = [
        "📅 *Resumo semanal*",
        f"_Semana {week_key.split('-W')[-1]}_",
        "",
        "*Panorama*",
        f"• {len(important)} ponto(s) importante(s)",
        f"• {critical_count} crítico(s) e {high_count} de atenção",
    ]
    for index, item in enumerate(important, 1):
        title = re.sub(r"\s+", " ", str(item.get("title") or "Ponto de atenção")).strip()[:120]
        detail = _whatsapp_compact_alert_detail(item.get("detail") or "")
        marker = "🚨" if str(item.get("severity") or "").lower() == "critical" else "⚠️"
        lines.extend(["", f"{marker} *{index}. {title}*", detail or "Confira os detalhes no JK Sistema."])
    lines.extend(
        [
            "",
            "*Próximo passo*",
            "Comece pelos itens críticos e abra o JK Sistema para consultar a lista completa e os dados detalhados.",
            "",
            "_Fonte: dados operacionais consolidados do JK Sistema nesta semana._",
        ]
    )
    parts = _whatsapp_split_body("\n".join(lines), WHATSAPP_REPORT_BODY_CHARS)
    return parts[:4]


def _forward_operational_alerts(config: dict[str, Any], week_key: str) -> None:
    try:
        from backend.services import codex_assistant

        client_id = str(config.get("client_id") or "default")
        with codex_assistant.ASSISTANT_LOCK:
            context = codex_assistant._assistant_collect_data(
                client_id,
                "resumo operacional semanal para WhatsApp",
                {},
                mode="report",
                force_refresh=True,
            )
            suggestions = codex_assistant._assistant_save_suggestions(client_id, context.get("suggestions") or [])
        important = [
            item
            for item in suggestions
            if isinstance(item, dict) and str(item.get("severity") or "").lower() in {"warning", "high", "critical"}
        ][:6]
        state = _load_state()
        if important:
            severity = "critical" if any(str(item.get("severity") or "").lower() == "critical" for item in important) else "high"
            parts = _whatsapp_weekly_operational_parts(important, week_key)
            text = parts[0]
            chart_data = whatsapp_report_visuals.build_weekly_chart_data(important, week_key)
            visual_result = _send_weekly_visual(
                config,
                client_id=client_id,
                subject_id=str(config.get("subject_id") or ""),
                week_key=week_key,
                chart_data=chart_data,
            )
            state["last_weekly_visual_status"] = str(
                visual_result.get("status") or visual_result.get("error") or "unknown"
            )[:80]
            if visual_result.get("success") or visual_result.get("duplicate"):
                state["last_weekly_visual_sent_at"] = _now()
                pending_visuals = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
                pending_visuals.pop(f"weekly-report:{client_id}:{week_key}", None)
                state["pending_weekly_visuals"] = pending_visuals
            else:
                _remember_pending_weekly_visual(
                    state,
                    client_id=client_id,
                    subject_id=str(config.get("subject_id") or ""),
                    week_key=week_key,
                    chart_data=chart_data,
                    last_status=str(visual_result.get("status") or visual_result.get("error") or "waiting_free_window"),
                )
            _post_proactive(
                config,
                {
                    "fingerprint": f"ops-week:{client_id}:{week_key}",
                    "event_type": "operational_alert",
                    "severity": severity,
                    "text": text,
                    "text_parts": parts,
                },
            )
        state["last_weekly_operational_key"] = week_key
        state["last_weekly_operational_at"] = _now()
        _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = str(exc)[:1000]


def _start_operational_alert_scan(config: dict[str, Any]) -> None:
    global ALERT_THREAD
    if isinstance(config.get("phone_notification_settings"), dict) and config.get("phone_notification_settings"):
        return
    state = _load_state()
    current = datetime.now()
    week_key = _whatsapp_week_key(current)
    if not str(state.get("last_weekly_operational_key") or ""):
        state["last_weekly_operational_key"] = week_key
        state["weekly_operational_initialized_at"] = _now()
        _save_state(state)
        return
    if str(state.get("last_weekly_operational_key") or "") == week_key:
        return
    if current.weekday() == 0 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR:
        return
    if ALERT_THREAD and ALERT_THREAD.is_alive():
        return
    ALERT_THREAD = threading.Thread(
        target=_forward_operational_alerts,
        args=(dict(config), week_key),
        name="jk-whatsapp-weekly-report",
        daemon=True,
    )
    ALERT_THREAD.start()


def _scheduled_report_period(kind: str, current: datetime) -> dict[str, str]:
    if kind == "monthly":
        current_month_start = current.replace(day=1)
        end = current_month_start - timedelta(days=1)
        start = end.replace(day=1)
        return {
            "key": _whatsapp_month_key(current),
            "title": "Relatório mensal",
            "event_type": "monthly_report",
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        }
    end = current - timedelta(days=1)
    start = end - timedelta(days=6)
    return {
        "key": _whatsapp_week_key(current),
        "title": "Relatório semanal",
        "event_type": "weekly_report",
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
    }


def _scheduled_report_parts(
    client_id: str,
    kind: str,
    current: datetime,
) -> tuple[dict[str, str], list[str], dict[str, Any]]:
    from backend.services import codex_assistant

    period = _scheduled_report_period(kind, current)
    prompt = (
        f"Gerar {period['title'].lower()} de vendas e operações do período de "
        f"{period['start']} a {period['end']}, usando todas as lojas deste cliente e separando os dados por loja. "
        "Incluir resumo executivo, indicadores de vendas e pedidos, ranking de SKUs, devoluções, ticket médio, "
        "comparação entre lojas, alertas, próximos passos, fontes e avisos de dados incompletos."
    )
    with codex_assistant.ASSISTANT_LOCK:
        context = codex_assistant._assistant_collect_data(
            client_id,
            prompt,
            {
                "periodo": {"data_inicio": period["start"], "data_fim": period["end"]},
                "data_inicio": period["start"],
                "data_fim": period["end"],
            },
            mode="report",
            force_refresh=True,
        )
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    answer = codex_assistant._assistant_report_chat_text(period["title"], context, suggestions)
    # O formatador móvel recebe um único título controlado pelo agendamento;
    # elimina-se apenas o primeiro H1 produzido pelo relatório-base.
    answer = re.sub(r"^\s*#\s+[^\n]+\n*", "", str(answer or ""), count=1).strip()
    start_label = _whatsapp_report_date(period["start"])
    end_label = _whatsapp_report_date(period["end"])
    report_text = (
        f"# {period['title']}\n"
        f"Período: {start_label} a {end_label}\n"
        "Conta: todas as lojas vinculadas\n\n"
        f"{answer}"
    ).strip()
    parts = _whatsapp_response_parts(report_text, "📊 BLACK JHON — RELATÓRIO")
    fallback = (
        f"*📊 {period['title']}*\n"
        f"_Período: {start_label} a {end_label}_\n\n"
        "Não foram encontrados dados suficientes para gerar o relatório."
    )
    chart_data = whatsapp_report_visuals.build_scheduled_report_chart_data(context, period, kind)
    return period, parts or [fallback], chart_data


def _send_scheduled_report_visuals(
    config: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    kind: str,
    period: dict[str, str],
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    outcome = whatsapp_report_visuals.generate_scheduled_report_chart_artifacts(
        base_info_dir=_info_dir(),
        client_id=client_id,
        period=period,
        kind=kind,
        chart_data=chart_data,
        max_images=2,
    )
    artifacts = [item for item in (outcome.get("artifacts") or []) if isinstance(item, dict)]
    if not artifacts:
        return {
            "success": False,
            "status": outcome.get("status") or "generation_empty",
            "error": outcome.get("error"),
            "results": [],
        }
    results: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts, 1):
        path = _whatsapp_report_chart_path(artifact, client_id)
        if not path:
            results.append({"success": False, "status": "invalid_artifact"})
            continue
        try:
            result = _post_proactive_image(
                config,
                subject_id=subject_id,
                fingerprint=f"scheduled-chart:{kind}:{subject_id}:{period['key']}:{index}",
                path=path,
                caption="",
                filename=path.name,
                event_type=period["event_type"],
            )
            results.append(dict(result or {}))
        except Exception as exc:
            results.append({"success": False, "status": "send_failed", "error": str(exc)[:500]})
    return {
        "success": any(item.get("success") is True for item in results),
        "status": "sent" if any(item.get("success") is True for item in results) else "send_failed",
        "results": results,
    }


def _scheduled_report_targets(config: dict[str, Any], worker: dict[str, Any], current: datetime) -> list[dict[str, str]]:
    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    deliveries = deliveries if isinstance(deliveries, dict) else {}
    machine_id = str(config.get("machine_id") or "")
    weekly_ready = not (current.weekday() == 0 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR)
    monthly_ready = not (current.day == 1 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR)
    targets: list[dict[str, str]] = []
    for binding in (worker.get("bindings") or []):
        if not isinstance(binding, dict) or str(binding.get("machine_id") or "") != machine_id:
            continue
        subject_id = str(binding.get("subject_id") or "").strip()
        client_id = str(binding.get("client_id") or "").strip()
        username = str(binding.get("username") or "").strip().lower()
        if not subject_id or not client_id or not username:
            continue
        settings = _phone_notification_settings(config, subject_id, client_id=client_id, username=username)
        delivered = deliveries.get(subject_id)
        delivered = delivered if isinstance(delivered, dict) else {}
        weekly_key = _whatsapp_week_key(current)
        monthly_key = _whatsapp_month_key(current)
        if settings["send_weekly_report"] and weekly_ready and str(delivered.get("weekly") or "") != weekly_key:
            targets.append({"subject_id": subject_id, "client_id": client_id, "username": username, "kind": "weekly", "key": weekly_key})
        if settings["send_monthly_report"] and monthly_ready and str(delivered.get("monthly") or "") != monthly_key:
            targets.append({"subject_id": subject_id, "client_id": client_id, "username": username, "kind": "monthly", "key": monthly_key})
    return targets


def _scheduled_report_result_accepted(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").strip().lower()
    return result.get("success") is True and status not in {"ignored_low_severity", "rate_limited", "binding_missing"}


def _forward_scheduled_reports(config: dict[str, Any], targets: list[dict[str, str]], current: datetime) -> None:
    try:
        rendered: dict[tuple[str, str], tuple[dict[str, str], list[str], dict[str, Any]]] = {}
        for target in targets:
            client_id = str(target.get("client_id") or "default")
            kind = str(target.get("kind") or "weekly")
            cache_key = (client_id, kind)
            if cache_key not in rendered:
                rendered[cache_key] = _scheduled_report_parts(client_id, kind, current)
            period, parts, chart_data = rendered[cache_key]
            subject_id = str(target.get("subject_id") or "")
            result = _post_proactive(
                config,
                {
                    "subject_id": subject_id,
                    "fingerprint": f"scheduled:{kind}:{subject_id}:{period['key']}",
                    "event_type": period["event_type"],
                    "severity": "high",
                    "text": parts[0],
                    "text_parts": parts,
                },
            )
            if not _scheduled_report_result_accepted(result):
                continue
            visual_result = _send_scheduled_report_visuals(
                config,
                client_id=client_id,
                subject_id=subject_id,
                kind=kind,
                period=period,
                chart_data=chart_data,
            )
            state = _load_state()
            deliveries = state.get("scheduled_report_deliveries")
            deliveries = deliveries if isinstance(deliveries, dict) else {}
            subject_deliveries = deliveries.get(subject_id)
            subject_deliveries = subject_deliveries if isinstance(subject_deliveries, dict) else {}
            subject_deliveries[kind] = period["key"]
            subject_deliveries[f"{kind}_sent_at"] = _now()
            subject_deliveries[f"{kind}_chart_status"] = str(visual_result.get("status") or "")[:80]
            subject_deliveries[f"{kind}_chart_count"] = sum(
                1 for item in (visual_result.get("results") or []) if isinstance(item, dict) and item.get("success") is True
            )
            deliveries[subject_id] = subject_deliveries
            state["scheduled_report_deliveries"] = deliveries
            _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"scheduled_whatsapp_report: {str(exc)[:900]}"


def _start_phone_notification_report_scan(config: dict[str, Any]) -> None:
    global ALERT_THREAD
    if ALERT_THREAD and ALERT_THREAD.is_alive():
        return
    worker = _worker_health(config)
    current = datetime.now()
    targets = _scheduled_report_targets(config, worker, current)
    if not targets:
        return
    ALERT_THREAD = threading.Thread(
        target=_forward_scheduled_reports,
        args=(dict(config), targets, current),
        name="jk-whatsapp-scheduled-reports",
        daemon=True,
    )
    ALERT_THREAD.start()


def _activate_completed_pairing(config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if config.get("enabled"):
        return config, "enabled"
    if not config.get("pairing_pending"):
        return config, "disabled"
    if int(config.get("pairing_expires_at") or 0) < int(time.time()):
        config.update({"pairing_pending": False, "pairing_expires_at": 0})
        return _save_config(config), "pairing_expired"
    worker = _worker_health(config)
    machine_bindings = [
        item
        for item in (worker.get("bindings") or [])
        if isinstance(item, dict)
        and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
    ]
    if not machine_bindings:
        return config, "pairing_waiting"
    prerequisites_ready = bool(
        worker.get("success")
        and (worker.get("zero_cost") or {}).get("policy_valid")
        and (worker.get("meta") or {}).get("configured")
        and _whisper_status().get("ready")
        and codex_console._codex_status_payload().get("ready")
    )
    if not prerequisites_ready:
        return config, "pairing_waiting_health"
    config.update({"enabled": True, "pairing_pending": False, "pairing_expires_at": 0})
    return _save_config(config), "pairing_activated"


def whatsapp_bridge_poll_once() -> dict[str, Any]:
    config = _load_config()
    try:
        whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    except Exception as exc:
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    if not config.get("enabled"):
        config, activation_status = _activate_completed_pairing(config)
        if not config.get("enabled"):
            return {"success": True, "status": activation_status, "claimed": 0}
    state = _load_state()
    if time.time() - float(state.get("report_chart_cleanup_at") or 0) >= 3600:
        state["report_chart_cleanup_removed"] = whatsapp_report_visuals.cleanup_stale_chart_files(_info_dir())
        state["report_file_cleanup_removed"] = whatsapp_report_files.cleanup_stale_files(_info_dir())
        state["report_chart_cleanup_at"] = time.time()
        _save_state(state)
    heartbeat = _gateway_json(
        config,
        "POST",
        "/bridge/heartbeat",
        {
            "machine_id": config.get("machine_id"),
            "client_id": config.get("client_id"),
            "username": config.get("username"),
            "app_version": str(config.get("app_version") or "bridge-v9"),
        },
        timeout=12,
    )
    state["gateway_heartbeat_at"] = _now()
    state["gateway_heartbeat_status"] = str(heartbeat.get("status") or "")
    _save_state(state)
    settings = _whatsapp_dual_agent_settings(config)
    _configure_phone_dispatcher(settings["conversation_worker_count"])
    codex_console._codex_configure_dual_sol_limit(
        settings["max_active_task_agents_global"],
        settings["max_active_task_agents_per_conversation"],
    )
    capacity = _phone_dispatch_capacity()
    if capacity <= 0:
        _forward_task_transitions(config, state)
        _forward_question_approvals(config, state)
        RUNTIME_STATE["last_processing_at"] = _now()
        return {"success": True, "status": "local_queue_full", "claimed": 0, "processed": 0}
    claim_limit = min(CLAIM_LIMIT, capacity)
    response = _gateway_json(
        config,
        "POST",
        "/bridge/claim",
        {"machine_id": config.get("machine_id"), "limit": claim_limit},
        timeout=20,
    )
    messages = response.get("messages") if isinstance(response.get("messages"), list) else []
    if messages:
        _flush_pending_weekly_visuals(config, state)
    processed = 0
    for raw in messages[:claim_limit]:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("message_id") or "")
        if _enqueue_phone_event(
            config,
            state,
            kind="inbound",
            event_id=message_id,
            message=raw,
            message_id=message_id,
        ):
            processed += 1
        else:
            try:
                _post_message_result(config, message_id, {"status": "retry", "error": "local_queue_full"})
            except Exception:
                pass
    _monitor_pending(config, state)
    _forward_task_transitions(config, state)
    _forward_question_approvals(config, state)
    _start_operational_alert_scan(config)
    _start_phone_notification_report_scan(config)
    RUNTIME_STATE["last_processing_at"] = _now()
    return {"success": True, "status": "processed", "claimed": len(messages), "processed": processed}


def _bridge_loop() -> None:
    RUNTIME_STATE["running"] = True
    while not BRIDGE_STOP_EVENT.is_set():
        try:
            result = whatsapp_bridge_poll_once()
            heartbeat_state = _load_state()
            heartbeat_state["bridge_heartbeat_at"] = _now()
            heartbeat_state.pop("bridge_last_error", None)
            heartbeat_state["last_poll_summary"] = {
                "status": str(result.get("status") or ""),
                "claimed": int(result.get("claimed") or 0),
                "processed": int(result.get("processed") or 0),
            }
            _save_state(heartbeat_state)
        except Exception as exc:
            RUNTIME_STATE["last_error"] = str(exc)[:1000]
            heartbeat_state = _load_state()
            heartbeat_state["bridge_heartbeat_at"] = _now()
            heartbeat_state["bridge_last_error"] = str(exc)[:1000]
            _save_state(heartbeat_state)
        BRIDGE_STOP_EVENT.wait(POLL_SECONDS)
    RUNTIME_STATE["running"] = False


def whatsapp_bridge_iniciar_background() -> None:
    global BRIDGE_THREAD
    if BRIDGE_THREAD and BRIDGE_THREAD.is_alive():
        return
    BRIDGE_STOP_EVENT.clear()
    # Persiste a normalizacao v8 no primeiro inicio, incluindo o gerenciador.
    config = _save_config(_load_config())
    settings = _whatsapp_dual_agent_settings(config)
    recovered_retries = _recover_dual_pending_after_restart(_load_state())
    RUNTIME_STATE["dual_agent_retries_recovered"] = recovered_retries
    _configure_phone_dispatcher(settings["conversation_worker_count"])
    codex_console._codex_configure_dual_sol_limit(
        settings["max_active_task_agents_global"],
        settings["max_active_task_agents_per_conversation"],
    )
    BRIDGE_THREAD = threading.Thread(target=_bridge_loop, name="jk-whatsapp-bridge", daemon=True)
    BRIDGE_THREAD.start()
    if config.get("enabled") and _whatsapp_dual_agent_settings(config).get("agent_architecture") == "dual_codex":
        def warm_dual_runtime() -> None:
            try:
                settings = _whatsapp_dual_agent_settings(config)
                codex_whatsapp_agents.CONVERSATION_RUNTIME.warm(
                    settings["conversation_agent_model"],
                    settings["task_agent_model"],
                    pool_size=settings["conversation_runtime_pool_size"],
                )
                codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.warm(
                    settings["conversation_agent_model"],
                    settings["task_agent_model"],
                    pool_size=settings["function_manager_runtime_pool_size"],
                )
                RUNTIME_STATE["dual_agent_last_error"] = ""
            except Exception as exc:
                RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]

        threading.Thread(target=warm_dual_runtime, name="jk-whatsapp-codex-warm", daemon=True).start()


def whatsapp_bridge_parar_background() -> None:
    global PHONE_DISPATCH_ACCEPTING, PHONE_DISPATCH_EXECUTOR, PHONE_DISPATCH_EXECUTOR_WORKERS
    global FUNCTION_MANAGER_EXECUTOR, FUNCTION_MANAGER_EXECUTOR_WORKERS
    BRIDGE_STOP_EVENT.set()
    with PHONE_DISPATCH_LOCK:
        PHONE_DISPATCH_ACCEPTING = False
        futures = list(PHONE_DISPATCH_FUTURES)
    deadline = time.monotonic() + 10.0
    for future in futures:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            future.result(timeout=remaining)
        except (concurrent.futures.TimeoutError, Exception):
            pass
    queued_events: list[dict[str, Any]] = []
    with PHONE_DISPATCH_LOCK:
        for queue in PHONE_DISPATCH_QUEUES.values():
            queued_events.extend(item[2] for item in queue)
        PHONE_DISPATCH_QUEUES.clear()
        executor = PHONE_DISPATCH_EXECUTOR
        old_executors = list(PHONE_DISPATCH_OLD_EXECUTORS)
        PHONE_DISPATCH_OLD_EXECUTORS.clear()
        PHONE_DISPATCH_EXECUTOR = None
        PHONE_DISPATCH_EXECUTOR_WORKERS = 0
    for event in queued_events:
        try:
            if str(event.get("kind") or "") == "inbound":
                _post_message_result(
                    event.get("config") if isinstance(event.get("config"), dict) else _load_config(),
                    str(event.get("message_id") or ""),
                    {"status": "retry", "error": "backend_shutdown"},
                )
        except Exception:
            pass
        finally:
            _finish_phone_event(event)
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=False)
    for old in old_executors:
        old.shutdown(wait=False, cancel_futures=False)
    with FUNCTION_MANAGER_LOCK:
        manager_executor = FUNCTION_MANAGER_EXECUTOR
        FUNCTION_MANAGER_EXECUTOR = None
        FUNCTION_MANAGER_EXECUTOR_WORKERS = 0
        FUNCTION_MANAGER_FUTURES.clear()
    if manager_executor is not None:
        manager_executor.shutdown(wait=False, cancel_futures=False)
    try:
        _save_state(_load_state())
    finally:
        codex_whatsapp_agents.CONVERSATION_RUNTIME.close()
        codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.close()
    RUNTIME_STATE["running"] = False


def _latency_diagnostics() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with PHONE_DISPATCH_LOCK:
        snapshots = {key: list(values) for key, values in PHONE_LATENCY_SAMPLES.items()}
    for stage, values in snapshots.items():
        ordered = sorted(float(value) for value in values)
        if not ordered:
            result[stage] = {"samples": 0, "average_ms": 0, "p95_ms": 0}
            continue
        p95_index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95) + 0.999999) - 1))
        result[stage] = {
            "samples": len(ordered),
            "average_ms": round((sum(ordered) / len(ordered)) * 1000, 1),
            "p95_ms": round(ordered[p95_index] * 1000, 1),
        }
    return result


def _phone_dispatch_diagnostics() -> dict[str, Any]:
    with PHONE_DISPATCH_LOCK:
        waiting = sum(len(items) for items in PHONE_DISPATCH_QUEUES.values())
        futures = list(PHONE_DISPATCH_FUTURES)
        return {
            "queue_capacity": WHATSAPP_LOCAL_QUEUE_CAPACITY,
            "messages_waiting": waiting,
            "capacity_available": max(0, WHATSAPP_LOCAL_QUEUE_CAPACITY - waiting - PHONE_DISPATCH_INFLIGHT),
            "active_phones": len(PHONE_DISPATCH_ACTIVE),
            "worker_count": PHONE_DISPATCH_EXECUTOR_WORKERS,
            "workers_busy": sum(1 for future in futures if future.running()),
            "accepting": PHONE_DISPATCH_ACCEPTING,
            "coalesced_ticks": len(PHONE_DISPATCH_TICK_IDS),
            "latency": _latency_diagnostics(),
        }


def _public_status(config: dict[str, Any], worker: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    whisper = _whisper_status()
    codex = codex_console._codex_status_payload()
    ai_settings = _whatsapp_ai_settings(config)
    dual_settings = _whatsapp_dual_agent_settings(config)
    dual_runtime = codex_whatsapp_agents.CONVERSATION_RUNTIME.diagnostics()
    function_manager_runtime = codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.diagnostics()
    dispatcher = _phone_dispatch_diagnostics()
    sol_capacity = codex_console._codex_dual_sol_diagnostics()
    worker = worker if isinstance(worker, dict) else _worker_health(config) if config.get("worker_url") and config.get("bridge_token") else {"success": False, "worker": False, "error": "nao_configurado"}
    voice_local = whatsapp_voice.VOICE_RUNTIME.diagnostics()
    if config.get("worker_url") and config.get("bridge_token"):
        try:
            voice_gateway = _gateway_json(config, "GET", "/bridge/voice/status", timeout=12)
        except Exception as exc:
            voice_gateway = {"success": False, "configured": False, "error": str(exc)[:500]}
    else:
        voice_gateway = {"success": False, "configured": False, "error": "nao_configurado"}
    gateway_openai = voice_gateway.get("openai") if isinstance(voice_gateway.get("openai"), dict) else {}
    gateway_fingerprint = str(gateway_openai.get("key_fingerprint") or "")
    local_fingerprint = str(voice_local.get("api_key_fingerprint") or "")
    voice_gateway_public = {
        key: value
        for key, value in voice_gateway.items()
        if key not in {"calls", "heartbeats"}
    }
    if isinstance(voice_gateway_public.get("openai"), dict):
        voice_gateway_public["openai"] = {
            key: value
            for key, value in voice_gateway_public["openai"].items()
            if key != "key_fingerprint"
        }
    current_client_id = str(config.get("client_id") or "")
    current_username = str(config.get("username") or "").strip().lower()
    voice_recent_calls = [
        item
        for item in list(voice_gateway.get("calls") or [])
        if isinstance(item, dict)
        and str(item.get("client_id") or "") == current_client_id
        and str(item.get("username") or "").strip().lower() == current_username
    ][:20]
    state = _load_state()
    try:
        persistence = _bridge_store().diagnostics()
    except Exception as exc:
        persistence = {"backend": "fallback_json", "error": str(exc)[:500]}
    raw_bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    personal_numbers = []
    for item in raw_bindings:
        if not isinstance(item, dict):
            continue
        phone_number = re.sub(r"\D", "", str(item.get("phone_number") or ""))
        suffix = (phone_number or re.sub(r"\D", "", str(item.get("phone_suffix") or "")))[-4:]
        binding_username = str(item.get("username") or "").strip().lower()
        binding_client_id = str(item.get("client_id") or "").strip()
        try:
            binding_permissions = admin_usuarios_common._carregar_permissoes_usuario(binding_username, binding_client_id)
            binding_full_access = binding_permissions.get("full") is True
        except Exception:
            binding_full_access = False
        subject_id = str(item.get("subject_id") or "").strip()
        notification_settings = _phone_notification_settings(
            config,
            subject_id,
            client_id=binding_client_id,
            username=binding_username,
        )
        personal_numbers.append(
            {
                "subject_id": subject_id,
                "phone_number": phone_number,
                "phone_masked": f"•••• {suffix}" if suffix else "número vinculado",
                "client_id": binding_client_id,
                "username": binding_username,
                "full_access": binding_full_access,
                "access_label": "Acesso total com confirmação pelo WhatsApp" if binding_full_access else "Somente consultas autorizadas",
                "last_inbound_at": int(item.get("last_inbound_at") or 0),
                "created_at": int(item.get("created_at") or 0),
                "this_machine": str(item.get("machine_id") or "") == str(config.get("machine_id") or ""),
                "notification_settings": notification_settings,
            }
        )
    binding_limit = max(1, min(3, int(worker.get("binding_limit_per_user") or 3)))
    conversation_records = [
        item
        for item in (state.get("dual_agent_conversations") or {}).values()
        if isinstance(item, dict)
    ] if isinstance(state.get("dual_agent_conversations"), dict) else []
    conversation_context_status = {
        "conversations": len(conversation_records),
        "with_persisted_context": sum(1 for item in conversation_records if list(item.get("recent_turns") or [])),
        "persisted_turns": sum(len(list(item.get("recent_turns") or [])) for item in conversation_records),
        "max_turns_per_conversation": 16,
    }
    return {
        "success": True,
        "config_version": int(config.get("version") or 9),
        "enabled": bool(config.get("enabled")),
        "configured": bool(config.get("worker_url") and config.get("bridge_token")),
        "worker_url": str(config.get("worker_url") or ""),
        "bridge_token_configured": bool(config.get("bridge_token")),
        "business_phone": str(config.get("business_phone") or ""),
        "ai_model": ai_settings["model"],
        "ai_provider": ai_settings["provider"],
        "codex_reasoning_effort": ai_settings["codex_reasoning_effort"],
        "codex_reasoning_policy": ai_settings["codex_reasoning_policy"],
        "codex_reasoning_max": ai_settings["codex_reasoning_max"],
        "codex_reasoning_options": list(WHATSAPP_CODEX_REASONING_OPTIONS),
        "codex_reasoning_policies": list(WHATSAPP_CODEX_REASONING_POLICIES),
        "orchestration_mode": WHATSAPP_ORCHESTRATION_MODE_DEFAULT,
        "progress_interval_seconds": _normalize_progress_interval(config.get("progress_interval_seconds")),
        "progress_explain_wait": config.get("progress_explain_wait") is not False,
        "active_task_policy": "steer_or_queue",
        **dual_settings,
        "dual_agent_runtime": dual_runtime,
        "function_manager_runtime": function_manager_runtime,
        "function_manager_recent": [
            item
            for item in list(state.get("function_manager_diagnostics") or [])[-10:]
            if isinstance(item, dict)
        ],
        "conversation_context": conversation_context_status,
        "conversation_dispatcher": dispatcher,
        "task_agent_capacity": sol_capacity,
        "personal_phone_masked": personal_numbers[0]["phone_masked"] if personal_numbers else _mask_phone(config.get("personal_phone")),
        "personal_numbers": personal_numbers,
        "binding_limit": binding_limit,
        "binding_slots_remaining": max(0, binding_limit - sum(
            1
            for item in personal_numbers
            if item["client_id"] == str(config.get("client_id") or "")
            and item["username"] == str(config.get("username") or "").strip().lower()
        )),
        "paired": bool(personal_numbers),
        "current_user": {
            "client_id": str(config.get("client_id") or ""),
            "username": str(config.get("username") or "").strip().lower(),
        },
        "machine_id": str(config.get("machine_id") or ""),
        "client_id": str(config.get("client_id") or ""),
        "username": str(config.get("username") or ""),
        "worker": worker,
        "persistence": persistence,
        "whisper": whisper,
        "joao": {"ready": bool(codex.get("ready")), "enabled": bool(codex.get("enabled")), "message": codex.get("message")},
        "voice": {
            "enabled": config.get("voice_enabled") is True,
            "ready": bool(
                config.get("voice_enabled") is True
                and voice_local.get("ready")
                and voice_gateway.get("configured")
                and gateway_fingerprint == local_fingerprint
            ),
            "read_only": True,
            "transcript_retention": "transcript_only",
            "model": str(config.get("voice_model") or whatsapp_voice.VOICE_MODEL_DEFAULT),
            "transcription_model": str(config.get("voice_transcription_model") or whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT),
            "name": str(config.get("voice_name") or whatsapp_voice.VOICE_NAME_DEFAULT),
            "language": "pt-BR",
            "max_call_minutes": int(config.get("voice_max_call_minutes") or 30),
            "silence_timeout_seconds": int(config.get("voice_silence_timeout_seconds") or 90),
            "long_task_offer_seconds": int(config.get("voice_long_task_offer_seconds") or 90),
            "max_concurrent_calls": int(config.get("voice_max_concurrent_calls") or 3),
            "progress_interval_seconds": int(config.get("voice_progress_interval_seconds") or 8),
            "api_key_configured": bool(voice_local.get("api_key_configured")),
            "key_match": bool(
                gateway_fingerprint
                and gateway_fingerprint == local_fingerprint
            ),
            "local": {key: value for key, value in voice_local.items() if key != "api_key_fingerprint"},
            "gateway": voice_gateway_public,
            "recent_calls": voice_recent_calls,
        },
        "runtime": {
            "running": bool(RUNTIME_STATE.get("running")),
            "last_error": str(RUNTIME_STATE.get("last_error") or ""),
            "last_processing_at": str(RUNTIME_STATE.get("last_processing_at") or ""),
            "last_worker_ok_at": str(RUNTIME_STATE.get("last_worker_ok_at") or ""),
            "typing_active": len(TYPING_PULSES),
            "progress_active": len(PROGRESS_PULSES),
            "typing_refresh_seconds": TYPING_REFRESH_SECONDS,
            "typing_max_seconds": TYPING_MAX_SECONDS,
            "typing_last_sent_at": str(RUNTIME_STATE.get("typing_last_sent_at") or ""),
            "typing_last_error": str(RUNTIME_STATE.get("typing_last_error") or ""),
            "progress_last_sent_at": str(RUNTIME_STATE.get("progress_last_sent_at") or ""),
            "progress_last_error": str(RUNTIME_STATE.get("progress_last_error") or ""),
            "dual_agent_last_error": str(RUNTIME_STATE.get("dual_agent_last_error") or dual_runtime.get("last_error") or ""),
            "dual_agent_retries_recovered": int(RUNTIME_STATE.get("dual_agent_retries_recovered") or 0),
            "dual_agent_conversations": len(state.get("dual_agent_conversations") or {}) if isinstance(state.get("dual_agent_conversations"), dict) else 0,
            "conversation_context": conversation_context_status,
            "bridge_heartbeat_at": str(state.get("bridge_heartbeat_at") or ""),
            "bridge_last_error": str(state.get("bridge_last_error") or ""),
            "pending_local_tasks": len(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else 0,
            "pending_weekly_visuals": len(state.get("pending_weekly_visuals") or {}) if isinstance(state.get("pending_weekly_visuals"), dict) else 0,
            "last_weekly_visual_status": str(state.get("last_weekly_visual_status") or ""),
            "last_weekly_visual_sent_at": str(state.get("last_weekly_visual_sent_at") or ""),
            "poll_seconds": POLL_SECONDS,
            "claim_limit": CLAIM_LIMIT,
            "conversation_dispatcher": dispatcher,
            "task_agent_capacity": sol_capacity,
            "state_store_last_error": str(RUNTIME_STATE.get("state_store_last_error") or ""),
            "web_fallback_circuit": dict(WEB_FALLBACK_CIRCUIT),
        },
        "zero_cost": {
            "policy_valid_until": ZERO_COST_POLICY_VALID_UNTIL,
            "fail_closed": True,
            "free_window_minutes": 1410,
            "templates_outside_window": int((worker.get("counts") or {}).get("templates") or 0) > 0,
            "template_blockers": [
                {"name": str(item.get("name") or ""), "status": str(item.get("status") or "")}
                for item in list(worker.get("template_blockers") or [])
                if isinstance(item, dict)
            ],
        },
    }


def whatsapp_bridge_status(request: Request, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    identity = {
        "client_id": str(session.get("client_id") or ""),
        "username": str(session.get("username") or "").strip().lower(),
        "machine_id": str(session.get("machine_id") or config.get("machine_id") or _host_machine_id()),
    }
    if any(str(config.get(key) or "") != value for key, value in identity.items()):
        config.update(identity)
        _save_config(config)
    return _public_status(config)


def whatsapp_bridge_update_config(
    payload: WhatsappBridgeConfigRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    if payload.worker_url is not None:
        config["worker_url"] = _normalize_worker_url(payload.worker_url)
    if payload.bridge_token is not None and str(payload.bridge_token).strip():
        config["bridge_token"] = str(payload.bridge_token).strip()
    if payload.business_phone is not None:
        config["business_phone"] = str(payload.business_phone).strip()[:40]
    if payload.ai_model is not None:
        config["ai_model"] = _normalize_ai_model(payload.ai_model)
    if payload.codex_reasoning_effort is not None:
        config["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(payload.codex_reasoning_effort)
    if payload.codex_reasoning_policy is not None:
        config["codex_reasoning_policy"] = _normalize_codex_reasoning_policy(payload.codex_reasoning_policy)
    if payload.codex_reasoning_max is not None:
        config["codex_reasoning_max"] = _normalize_codex_reasoning_effort(payload.codex_reasoning_max)
    if payload.progress_interval_seconds is not None:
        config["progress_interval_seconds"] = _normalize_progress_interval(payload.progress_interval_seconds)
    if payload.progress_explain_wait is not None:
        config["progress_explain_wait"] = bool(payload.progress_explain_wait)
    if payload.agent_architecture is not None:
        config["agent_architecture"] = _normalize_agent_architecture(payload.agent_architecture)
    if payload.conversation_agent_model is not None:
        config["conversation_agent_model"] = _normalize_codex_agent_model(
            payload.conversation_agent_model, WHATSAPP_CONVERSATION_AGENT_MODEL_DEFAULT
        )
    if payload.conversation_agent_reasoning is not None:
        config["conversation_agent_reasoning"] = _normalize_codex_reasoning_effort(payload.conversation_agent_reasoning)
    if payload.task_agent_model is not None:
        config["task_agent_model"] = _normalize_codex_agent_model(
            payload.task_agent_model, WHATSAPP_TASK_AGENT_MODEL_DEFAULT
        )
        config["ai_model"] = f"codex:{config['task_agent_model']}"
    if payload.task_agent_reasoning is not None:
        config["task_agent_reasoning"] = WHATSAPP_TASK_AGENT_REASONING_DEFAULT
    if payload.conversation_interval_seconds is not None:
        config["conversation_interval_seconds"] = _normalize_conversation_interval(payload.conversation_interval_seconds)
    if payload.wait_message_after_seconds is not None:
        config["wait_message_after_seconds"] = _normalize_capacity(
            payload.wait_message_after_seconds, WHATSAPP_WAIT_MESSAGE_AFTER_DEFAULT, 5, 60
        )
    if payload.wait_message_repeat_seconds is not None:
        config["wait_message_repeat_seconds"] = _normalize_capacity(
            payload.wait_message_repeat_seconds, WHATSAPP_WAIT_MESSAGE_REPEAT_DEFAULT, 15, 180
        )
    if payload.wait_message_steady_seconds is not None:
        config["wait_message_steady_seconds"] = _normalize_capacity(
            payload.wait_message_steady_seconds, WHATSAPP_WAIT_MESSAGE_STEADY_DEFAULT, 30, 300
        )
    if payload.partial_delivery_debounce_seconds is not None:
        config["partial_delivery_debounce_seconds"] = _normalize_capacity(
            payload.partial_delivery_debounce_seconds, WHATSAPP_PARTIAL_DEBOUNCE_DEFAULT, 1, 10
        )
    if payload.job_deadline_seconds is not None:
        config["job_deadline_seconds"] = _normalize_capacity(
            payload.job_deadline_seconds,
            WHATSAPP_JOB_DEADLINE_DEFAULT,
            30,
            WHATSAPP_REPORT_DEADLINE_SECONDS,
        )
    config["retry_policy"] = "bounded"
    if payload.max_subtasks_per_job is not None:
        config["max_subtasks_per_job"] = _normalize_capacity(
            payload.max_subtasks_per_job, WHATSAPP_MAX_SUBTASKS_DEFAULT, 1, 6
        )
    if payload.progress_messages_enabled is not None:
        config["progress_messages_enabled"] = bool(payload.progress_messages_enabled)
    if payload.conversation_worker_count is not None:
        config["conversation_worker_count"] = _normalize_capacity(
            payload.conversation_worker_count, WHATSAPP_CONVERSATION_WORKER_COUNT_DEFAULT, 1, 8
        )
    if payload.conversation_runtime_pool_size is not None:
        config["conversation_runtime_pool_size"] = _normalize_capacity(
            payload.conversation_runtime_pool_size,
            WHATSAPP_CONVERSATION_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        )
    if payload.max_active_task_agents_global is not None:
        config["max_active_task_agents_global"] = _normalize_capacity(
            payload.max_active_task_agents_global,
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_GLOBAL_DEFAULT,
            1,
            12,
        )
    if payload.preserve_order_per_phone is False:
        raise HTTPException(status_code=400, detail="A ordem FIFO por telefone deve permanecer habilitada.")
    config["preserve_order_per_phone"] = True
    if payload.function_manager_enabled is not None:
        config["function_manager_enabled"] = bool(payload.function_manager_enabled)
    if payload.function_manager_required_before_sol is not None:
        config["function_manager_required_before_sol"] = bool(payload.function_manager_required_before_sol)
    if payload.function_manager_worker_count is not None:
        config["function_manager_worker_count"] = _normalize_capacity(
            payload.function_manager_worker_count,
            WHATSAPP_FUNCTION_MANAGER_WORKER_COUNT_DEFAULT,
            1,
            8,
        )
    if payload.function_manager_runtime_pool_size is not None:
        config["function_manager_runtime_pool_size"] = _normalize_capacity(
            payload.function_manager_runtime_pool_size,
            WHATSAPP_FUNCTION_MANAGER_RUNTIME_POOL_SIZE_DEFAULT,
            1,
            8,
        )
    if config.get("agent_architecture") == "dual_codex":
        # No fluxo dual v8 nao existe atalho direto para o Sol.
        config["function_manager_enabled"] = True
        config["function_manager_required_before_sol"] = True
    if payload.voice_model is not None:
        config["voice_model"] = _normalize_voice_model(payload.voice_model, whatsapp_voice.VOICE_MODEL_DEFAULT)
    if payload.voice_transcription_model is not None:
        config["voice_transcription_model"] = _normalize_voice_model(
            payload.voice_transcription_model, whatsapp_voice.VOICE_TRANSCRIPTION_MODEL_DEFAULT
        )
    if payload.voice_name is not None:
        config["voice_name"] = _normalize_voice_name(payload.voice_name)
    if payload.voice_language is not None and str(payload.voice_language or "").strip().lower() not in {"pt-br", "pt_br", "pt"}:
        raise HTTPException(status_code=400, detail="A primeira versao das ligacoes usa portugues brasileiro.")
    if payload.voice_max_call_minutes is not None:
        config["voice_max_call_minutes"] = _normalize_voice_int(payload.voice_max_call_minutes, 30, 5, 60)
    if payload.voice_silence_timeout_seconds is not None:
        config["voice_silence_timeout_seconds"] = _normalize_voice_int(payload.voice_silence_timeout_seconds, 90, 30, 300)
    if payload.voice_long_task_offer_seconds is not None:
        config["voice_long_task_offer_seconds"] = _normalize_voice_int(payload.voice_long_task_offer_seconds, 90, 30, 300)
    if payload.voice_max_concurrent_calls is not None:
        config["voice_max_concurrent_calls"] = _normalize_voice_int(payload.voice_max_concurrent_calls, 3, 1, 10)
    if payload.voice_progress_interval_seconds is not None:
        config["voice_progress_interval_seconds"] = _normalize_voice_int(payload.voice_progress_interval_seconds, 8, 8, 30)
    if payload.max_active_task_agents_per_conversation is not None:
        config["max_active_task_agents_per_conversation"] = _normalize_capacity(
            payload.max_active_task_agents_per_conversation,
            WHATSAPP_MAX_ACTIVE_TASK_AGENTS_DEFAULT,
            1,
            6,
        )
    if payload.orchestration_mode is not None and str(payload.orchestration_mode or "") != WHATSAPP_ORCHESTRATION_MODE_DEFAULT:
        raise HTTPException(status_code=400, detail="Modo de orquestracao do WhatsApp invalido.")
    if payload.active_task_policy is not None and str(payload.active_task_policy or "") != "steer_or_queue":
        raise HTTPException(status_code=400, detail="Politica de tarefa ativa invalida.")
    config["client_id"] = str(session.get("client_id") or "")
    config["username"] = str(session.get("username") or "").strip().lower()
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    if payload.enabled is not None:
        if payload.enabled:
            worker = _worker_health(config)
            whisper = _whisper_status()
            codex = codex_console._codex_status_payload()
            policy_valid = bool((worker.get("zero_cost") or {}).get("policy_valid"))
            meta_ready = bool((worker.get("meta") or {}).get("configured"))
            machine_bindings = [
                item
                for item in (worker.get("bindings") or [])
                if isinstance(item, dict)
                and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
            ]
            missing = []
            if not worker.get("success"):
                missing.append("Worker")
            if not policy_valid:
                missing.append("politica de custo zero")
            if not meta_ready:
                missing.append("Meta")
            if not machine_bindings:
                missing.append("numero pessoal vinculado")
            if not whisper.get("ready"):
                missing.append("Whisper Small")
            if _whatsapp_ai_settings(config)["provider"] == "codex" and not codex.get("ready"):
                missing.append("Joao")
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail="Ativacao bloqueada: " + ", ".join(missing) + " ainda nao esta pronto.",
                )
            config["enabled"] = True
            config.update({"pairing_pending": False, "pairing_expires_at": 0})
        else:
            config["enabled"] = False
            config.update({"pairing_pending": False, "pairing_expires_at": 0})
    config = _save_config(config)
    if config.get("enabled") and config.get("agent_architecture") == "dual_codex":
        settings = _whatsapp_dual_agent_settings(config)
        try:
            _configure_phone_dispatcher(settings["conversation_worker_count"])
            codex_console._codex_configure_dual_sol_limit(
                settings["max_active_task_agents_global"],
                settings["max_active_task_agents_per_conversation"],
            )
            codex_whatsapp_agents.CONVERSATION_RUNTIME.warm(
                settings["conversation_agent_model"],
                settings["task_agent_model"],
                pool_size=settings["conversation_runtime_pool_size"],
            )
            codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.warm(
                settings["conversation_agent_model"],
                settings["task_agent_model"],
                pool_size=settings["function_manager_runtime_pool_size"],
            )
            RUNTIME_STATE["dual_agent_last_error"] = ""
        except Exception as exc:
            RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]
            raise HTTPException(status_code=409, detail=f"Agentes Codex indisponiveis: {exc}")
    return _public_status(config)


def whatsapp_bridge_update_phone_settings(
    payload: WhatsappPhoneSettingsRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    subject_id = str(payload.subject_id or "").strip()
    username = str(payload.username or "").strip().lower()
    client_id = str(payload.client_id or "").strip()
    if not subject_id or not username or not client_id:
        raise HTTPException(status_code=400, detail="Telefone, usuario e cliente sao obrigatorios.")

    worker = _worker_health(config)
    binding = next(
        (
            item
            for item in (worker.get("bindings") or [])
            if isinstance(item, dict)
            and str(item.get("subject_id") or "").strip() == subject_id
            and str(item.get("username") or "").strip().lower() == username
            and str(item.get("client_id") or "").strip() == client_id
        ),
        None,
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="Numero vinculado nao encontrado para este usuario.")
    if str(binding.get("machine_id") or "") != str(config.get("machine_id") or ""):
        raise HTTPException(status_code=409, detail="Este numero esta vinculado em outra maquina. Configure-o na maquina correspondente.")

    previous = _phone_notification_settings(
        config,
        subject_id,
        client_id=client_id,
        username=username,
    )
    updated = _normalize_phone_notification_settings(
        {
            "label": payload.label,
            "send_ml_question_suggestions": payload.send_ml_question_suggestions,
            "send_weekly_report": payload.send_weekly_report,
            "send_monthly_report": payload.send_monthly_report,
            "ai_behavior": previous.get("ai_behavior") if payload.ai_behavior is None else payload.ai_behavior,
            "allow_voice_calls": payload.allow_voice_calls,
        }
    )
    updated.update({"subject_id": subject_id, "client_id": client_id, "username": username, "updated_at": _now()})
    try:
        _gateway_json(
            config,
            "POST",
            "/bridge/voice/phones/settings",
            {
                "subject_id": subject_id,
                "machine_id": str(config.get("machine_id") or ""),
                "allow_voice_calls": updated["allow_voice_calls"],
            },
            timeout=15,
        )
    except Exception as exc:
        # Instalações antigas ainda não possuem a rota de voz. O salvamento
        # continua compatível apenas quando voz nunca esteve autorizada.
        if updated["allow_voice_calls"] or previous.get("allow_voice_calls") is True:
            raise HTTPException(status_code=409, detail="O gateway de ligações não confirmou a permissão deste telefone.") from exc
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    settings_by_phone = config.get("phone_notification_settings")
    if not isinstance(settings_by_phone, dict):
        settings_by_phone = {}
    settings_by_phone[subject_id] = updated
    config["phone_notification_settings"] = settings_by_phone
    config = _save_config(config)

    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if not isinstance(deliveries, dict):
        deliveries = {}
    subject_deliveries = deliveries.get(subject_id)
    if not isinstance(subject_deliveries, dict):
        subject_deliveries = {}
    current = datetime.now()
    if updated["send_weekly_report"] and not previous["send_weekly_report"]:
        subject_deliveries["weekly"] = _whatsapp_week_key(current)
    if updated["send_monthly_report"] and not previous["send_monthly_report"]:
        subject_deliveries["monthly"] = _whatsapp_month_key(current)
    deliveries[subject_id] = subject_deliveries
    state["scheduled_report_deliveries"] = deliveries
    _save_state(state)
    return _public_status(config, worker)


def whatsapp_bridge_register_phone(
    payload: WhatsappPhoneRegistrationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    label = re.sub(r"\s+", " ", str(payload.name or "")).strip()[:60]
    if not label:
        raise HTTPException(status_code=400, detail="Informe um nome para identificar o telefone.")
    phone_number = _normalize_registered_phone(payload.phone_number)
    welcome_message = str(payload.welcome_message or "").replace("\r\n", "\n").strip()
    if len(welcome_message) > 1000:
        raise HTTPException(status_code=400, detail="A mensagem de boas-vindas deve ter no maximo 1000 caracteres.")
    if payload.send_welcome_message and not welcome_message:
        raise HTTPException(status_code=400, detail="Escreva a mensagem de boas-vindas antes de solicitar o envio.")
    config = _load_config()
    machine_id = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    config["machine_id"] = machine_id
    try:
        result = _gateway_json(
            config,
            "POST",
            "/bridge/bindings/register",
            {
                "phone_number": phone_number,
                "client_id": target_client_id,
                "username": target_username,
                "machine_id": machine_id,
            },
            timeout=15,
        )
    except RuntimeError as exc:
        error = str(exc)
        if "binding_limit_reached" in error:
            raise HTTPException(status_code=409, detail="Este usuario ja possui o limite de 3 telefones.") from exc
        if "binding_already_registered" in error:
            raise HTTPException(status_code=409, detail="Este telefone ja esta cadastrado para outro usuario.") from exc
        if "invalid_phone_number" in error:
            raise HTTPException(status_code=400, detail="Informe um numero de WhatsApp valido, com DDD.") from exc
        raise

    binding = result.get("binding") if isinstance(result.get("binding"), dict) else {}
    subject_id = str(result.get("subject_id") or binding.get("subject_id") or phone_number).strip()
    previous = _phone_notification_settings(
        config,
        subject_id,
        client_id=target_client_id,
        username=target_username,
    )
    updated = _normalize_phone_notification_settings(
        {
            "label": label,
            "send_ml_question_suggestions": payload.send_ml_question_suggestions,
            "send_weekly_report": payload.send_weekly_report,
            "send_monthly_report": payload.send_monthly_report,
            "allow_voice_calls": payload.allow_voice_calls,
        }
    )
    updated.update(
        {
            "subject_id": subject_id,
            "client_id": target_client_id,
            "username": target_username,
            "phone_number": phone_number,
            "updated_at": _now(),
        }
    )
    settings_by_phone = config.get("phone_notification_settings")
    if not isinstance(settings_by_phone, dict):
        settings_by_phone = {}
    settings_by_phone[subject_id] = updated
    try:
        _gateway_json(
            config,
            "POST",
            "/bridge/voice/phones/settings",
            {
                "subject_id": subject_id,
                "machine_id": machine_id,
                "allow_voice_calls": updated["allow_voice_calls"],
            },
            timeout=15,
        )
    except Exception as exc:
        if updated["allow_voice_calls"]:
            raise HTTPException(status_code=409, detail="O gateway de ligações não confirmou a permissão deste telefone.") from exc
        RUNTIME_STATE["voice_last_error"] = str(exc)[:1000]
    config["phone_notification_settings"] = settings_by_phone
    config = _save_config(config)

    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if not isinstance(deliveries, dict):
        deliveries = {}
    subject_deliveries = deliveries.get(subject_id)
    if not isinstance(subject_deliveries, dict):
        subject_deliveries = {}
    current = datetime.now()
    if updated["send_weekly_report"] and not previous["send_weekly_report"]:
        subject_deliveries["weekly"] = _whatsapp_week_key(current)
    if updated["send_monthly_report"] and not previous["send_monthly_report"]:
        subject_deliveries["monthly"] = _whatsapp_month_key(current)
    deliveries[subject_id] = subject_deliveries
    state["scheduled_report_deliveries"] = deliveries
    _save_state(state)

    welcome_result: dict[str, Any] = {"requested": False, "success": True, "status": "not_requested"}
    if payload.send_welcome_message:
        try:
            sent = _gateway_json(
                config,
                "POST",
                "/bridge/welcome",
                {
                    "subject_id": subject_id,
                    "machine_id": machine_id,
                    "text": welcome_message,
                },
                timeout=20,
            )
            welcome_result = {"requested": True, **sent}
        except Exception as exc:
            welcome_result = {
                "requested": True,
                "success": False,
                "status": "failed",
                "error": str(exc)[:500],
            }

    worker = _worker_health(config)
    return {
        "success": True,
        "created": result.get("created") is not False,
        "subject_id": subject_id,
        "phone_number": phone_number,
        "welcome_message": welcome_result,
        "status": _public_status(config, worker),
    }


def whatsapp_bridge_send_adhoc_message(
    payload: WhatsappAdhocMessageRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    phone_number = _normalize_registered_phone(payload.phone_number)
    message = str(payload.message or "").replace("\r\n", "\n").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Escreva a mensagem que deseja enviar.")
    if len(message) > WHATSAPP_ADHOC_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail=f"A mensagem deve ter no maximo {WHATSAPP_ADHOC_MESSAGE_CHARS} caracteres.")
    config = _load_config()
    machine_id = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    delivery = _gateway_json(
        config,
        "POST",
        "/bridge/messages/send",
        {
            "phone_number": phone_number,
            "machine_id": machine_id,
            "text": message,
        },
        timeout=20,
    )
    return {
        "success": True,
        "phone_number": phone_number,
        "delivery": delivery,
    }


def whatsapp_bridge_test(request: Request, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    worker = _worker_health(config)
    return {"success": bool(worker.get("success")), "status": _public_status(config, worker)}


def whatsapp_bridge_voice_preflight(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    result = whatsapp_voice.VOICE_RUNTIME.preflight(config, sys.modules[__name__], check_openai=True)
    return {"success": True, "ready": result.pop("success", False), **result}


def whatsapp_bridge_voice_enable(
    payload: WhatsappVoiceToggleRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Confirme explicitamente a habilitacao das ligacoes.")
    config = _load_config()
    preflight = whatsapp_voice.VOICE_RUNTIME.preflight(config, sys.modules[__name__], check_openai=True)
    if preflight.get("success") is not True:
        raise HTTPException(
            status_code=409,
            detail="Ligacoes ainda nao estao prontas. Verifique chave OpenAI, webhook, projeto, SIP, migracao D1 e gateway.",
        )
    config["voice_enabled"] = True
    config = _save_config(config)
    whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    return {"success": True, "voice": _public_status(config).get("voice"), "preflight": preflight}


def whatsapp_bridge_voice_disable(
    payload: WhatsappVoiceToggleRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    if payload.confirmed is not True:
        raise HTTPException(status_code=400, detail="Confirme explicitamente a desativacao das ligacoes.")
    config = _load_config()
    config["voice_enabled"] = False
    config = _save_config(config)
    whatsapp_voice.VOICE_RUNTIME.tick(config, sys.modules[__name__])
    return {"success": True, "voice": _public_status(config).get("voice")}


def whatsapp_bridge_voice_calls(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    result = _gateway_json(config, "GET", "/bridge/voice/status", timeout=15)
    client_id = str(session.get("client_id") or "")
    username = str(session.get("username") or "").strip().lower()
    calls = [
        item
        for item in list(result.get("calls") or [])
        if isinstance(item, dict)
        and str(item.get("client_id") or "") == client_id
        and str(item.get("username") or "").strip().lower() == username
    ]
    return {"success": True, "calls": calls, "counts": result.get("counts") or {}}


def whatsapp_bridge_pairing_code(
    payload: WhatsappPairingCodeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    result = _gateway_json(
        config,
        "POST",
        "/bridge/pairing-codes",
        {
            "code": code,
            "client_id": target_client_id,
            "username": target_username,
            "machine_id": config["machine_id"],
        },
        timeout=15,
    )
    config.update({
        "pairing_pending": True,
        "pairing_expires_at": int(result.get("expires_at") or (time.time() + 600)),
    })
    _save_config(config)
    return {
        "success": True,
        "code": code,
        "expires_at": result.get("expires_at"),
        "limit": int(result.get("limit") or 3),
        "active_bindings": int(result.get("active_bindings") or 0),
        "remaining_slots": int(result.get("remaining_slots") or 0),
        "username": target_username,
        "client_id": target_client_id,
        "instruction": f"Envie VINCULAR {code} para o numero empresarial.",
    }


def whatsapp_bridge_revoke(
    payload: WhatsappBindingRevokeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    session = _require_full(request, authorization)
    config = _load_config()
    target_username, target_client_id = _binding_target(session, payload.username, payload.client_id)
    config["machine_id"] = str(session.get("machine_id") or config.get("machine_id") or _host_machine_id())
    subject_id = str(payload.subject_id or "").strip()
    result = _gateway_json(
        config,
        "POST",
        "/bridge/bindings/revoke",
        {
            "client_id": target_client_id,
            "username": target_username,
            "subject_id": subject_id,
            "revoke_all": bool(payload.revoke_all),
        },
        timeout=15,
    )
    if subject_id and subject_id == str(config.get("subject_id") or ""):
        config.update({"subject_id": "", "personal_phone": ""})
    settings_by_phone = config.get("phone_notification_settings")
    if isinstance(settings_by_phone, dict):
        if subject_id:
            settings_by_phone.pop(subject_id, None)
        elif payload.revoke_all:
            settings_by_phone = {
                key: value
                for key, value in settings_by_phone.items()
                if not (
                    isinstance(value, dict)
                    and str(value.get("username") or "").strip().lower() == target_username
                    and str(value.get("client_id") or "").strip() == target_client_id
                )
            }
        config["phone_notification_settings"] = settings_by_phone
    worker = _worker_health(config)
    machine_bindings = [
        item
        for item in (worker.get("bindings") or [])
        if isinstance(item, dict)
        and str(item.get("machine_id") or "") == str(config.get("machine_id") or "")
    ]
    if not machine_bindings:
        config.update({"subject_id": "", "personal_phone": "", "enabled": False})
    _save_config(config)
    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    if isinstance(deliveries, dict):
        active_subjects = {
            str(item.get("subject_id") or "").strip()
            for item in (worker.get("bindings") or [])
            if isinstance(item, dict) and str(item.get("subject_id") or "").strip()
        }
        state["scheduled_report_deliveries"] = {
            key: value for key, value in deliveries.items() if key in active_subjects
        }
        _save_state(state)
    return {
        "success": True,
        "revoked": int(result.get("revoked") or 0),
        "remaining": int(result.get("remaining") or 0),
        "status": _public_status(config, worker),
    }


def whatsapp_bridge_sync_templates(
    payload: WhatsappTemplatesRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _require_full(request, authorization)
    config = _load_config()
    return _gateway_json(config, "POST", "/bridge/templates/sync", {"create_missing": bool(payload.create_missing)}, timeout=45)


__all__ = [
    "WhatsappAdhocMessageRequest",
    "WhatsappBridgeConfigRequest",
    "WhatsappBindingRevokeRequest",
    "WhatsappPairingCodeRequest",
    "WhatsappPhoneRegistrationRequest",
    "WhatsappPhoneSettingsRequest",
    "WhatsappTemplatesRequest",
    "WhatsappVoiceToggleRequest",
    "whatsapp_bridge_download_whisper",
    "whatsapp_bridge_iniciar_background",
    "whatsapp_bridge_pairing_code",
    "whatsapp_bridge_poll_once",
    "whatsapp_bridge_register_phone",
    "whatsapp_bridge_revoke",
    "whatsapp_bridge_send_adhoc_message",
    "whatsapp_bridge_status",
    "whatsapp_bridge_sync_templates",
    "whatsapp_bridge_test",
    "whatsapp_bridge_update_config",
    "whatsapp_bridge_update_phone_settings",
    "whatsapp_bridge_voice_calls",
    "whatsapp_bridge_voice_disable",
    "whatsapp_bridge_voice_enable",
    "whatsapp_bridge_voice_preflight",
]
