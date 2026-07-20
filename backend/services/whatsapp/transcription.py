"""Extracted WhatsApp bridge component: transcription."""

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
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
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

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS

AUDIO_MESSAGE_MAX_BYTES = 16 * 1024 * 1024
AUDIO_MESSAGE_MAX_DURATION_SECONDS = 600
AUDIO_TRANSCRIPTION_MAX_WAITING = 4
AUDIO_TRANSCRIPTION_QUEUE_WAIT_SECONDS = 90
_AUDIO_TRANSCRIPTION_CAPACITY = threading.BoundedSemaphore(1 + AUDIO_TRANSCRIPTION_MAX_WAITING)
_AUDIO_TRANSCRIPTION_EXECUTION = threading.Lock()
_AUDIO_TRANSCRIPTION_STATE_LOCK = threading.Lock()
_AUDIO_TRANSCRIPTION_STATE: dict[str, Any] = {
    "active": 0,
    "waiting": 0,
    "accepted": 0,
    "completed": 0,
    "failed": 0,
    "rejected_busy": 0,
    "last_error_code": "",
    "last_latency_ms": 0,
    "telemetry": {},
}

_AUDIO_SAFE_ERROR_CODES = frozenset(
    {
        "audio_corrupt",
        "audio_cleanup_failed",
        "audio_empty",
        "audio_missing",
        "audio_size_limit",
        "child_failed",
        "dependency_missing",
        "duration_limit",
        "low_confidence",
        "model_invalid",
        "no_speech",
        "preflight_failed",
        "queue_timeout",
        "resource_busy",
        "runner_invalid",
        "transcription_timeout",
    }
)


def _safe_audio_error_code(value: Any, fallback: str = "child_failed") -> str:
    code = str(value or "").strip().lower().replace("-", "_")
    return code if code in _AUDIO_SAFE_ERROR_CODES else fallback


def _audio_failure(code: Any) -> dict[str, Any]:
    safe = _safe_audio_error_code(code)
    return {"success": False, "error_code": safe, "error": safe, "local_only": True}


def _audio_mime_from_path(audio_path: Path) -> str:
    return {
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".aac": "audio/aac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".amr": "audio/amr",
    }.get(audio_path.suffix.lower(), "unknown")


def _audio_size_bucket(size_bytes: int) -> str:
    if size_bytes < 0:
        return "unknown"
    if size_bytes < 1024 * 1024:
        return "lt_1mb"
    if size_bytes < 4 * 1024 * 1024:
        return "1_to_4mb"
    if size_bytes <= AUDIO_MESSAGE_MAX_BYTES:
        return "4_to_16mb"
    return "gt_16mb"


def _audio_duration_bucket(duration_seconds: float) -> str:
    if duration_seconds < 0:
        return "unknown"
    if duration_seconds < 30:
        return "lt_30s"
    if duration_seconds < 120:
        return "30_to_120s"
    if duration_seconds <= AUDIO_MESSAGE_MAX_DURATION_SECONDS:
        return "120_to_600s"
    return "gt_600s"


def _record_audio_stage(
    stage: str,
    *,
    audio_path: Optional[Path] = None,
    size_bytes: int = -1,
    duration_seconds: float = -1,
    success: Optional[bool] = None,
    error_code: str = "",
    latency_ms: int = 0,
) -> None:
    safe_stage = stage if stage in {"received", "transcription_started", "transcription_succeeded", "transcription_failed"} else "transcription_failed"
    safe_mime = _audio_mime_from_path(audio_path) if audio_path is not None else "unknown"
    version = str(os.getenv("JK_APP_VERSION") or "unknown").strip()[:40]
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,40}", version):
        version = "unknown"
    safe_error = _safe_audio_error_code(error_code, "") if error_code else ""
    event = {
        "stage": safe_stage,
        "mime": safe_mime,
        "size_bucket": _audio_size_bucket(int(size_bytes)),
        "duration_bucket": _audio_duration_bucket(float(duration_seconds)),
        "runtime_version": version,
        "latency_ms": max(0, min(int(latency_ms or 0), 3_600_000)),
        "success": success if isinstance(success, bool) else None,
        "error_code": safe_error,
    }
    with _AUDIO_TRANSCRIPTION_STATE_LOCK:
        telemetry = _AUDIO_TRANSCRIPTION_STATE.setdefault("telemetry", {})
        stage_counts = telemetry.setdefault("stage_counts", {})
        error_counts = telemetry.setdefault("error_counts", {})
        stage_counts[safe_stage] = int(stage_counts.get(safe_stage) or 0) + 1
        if safe_error:
            error_counts[safe_error] = int(error_counts.get(safe_error) or 0) + 1
        telemetry["last"] = event


def _record_audio_outcome(
    *,
    success: bool,
    error_code: str = "",
    latency_ms: int = 0,
    audio_path: Optional[Path] = None,
    size_bytes: int = -1,
    duration_seconds: float = -1,
) -> None:
    with _AUDIO_TRANSCRIPTION_STATE_LOCK:
        key = "completed" if success else "failed"
        _AUDIO_TRANSCRIPTION_STATE[key] = int(_AUDIO_TRANSCRIPTION_STATE.get(key) or 0) + 1
        _AUDIO_TRANSCRIPTION_STATE["last_error_code"] = "" if success else _safe_audio_error_code(error_code)
        _AUDIO_TRANSCRIPTION_STATE["last_latency_ms"] = max(0, min(int(latency_ms or 0), 3_600_000))
    _record_audio_stage(
        "transcription_succeeded" if success else "transcription_failed",
        audio_path=audio_path,
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        success=success,
        error_code=error_code,
        latency_ms=latency_ms,
    )


def _runner_validation() -> dict[str, Any]:
    services_root = Path(__file__).resolve().parent.parent
    runner = _whisper_runner()
    try:
        runner.relative_to(services_root)
        stat = runner.lstat()
        valid = runner.is_file() and not runner.is_symlink() and stat.st_size > 0
    except (OSError, ValueError):
        valid = False
    return {"valid": bool(valid), "error": "" if valid else "runner_invalid"}


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
    av_installed = importlib.util.find_spec("av") is not None
    model_dir = _model_dir()
    validation = _validate_model_dir(model_dir)
    runner = _runner_validation()
    total_bytes = int(validation.get("bytes") or _directory_size(model_dir))
    ready = bool(installed and av_installed and validation.get("valid") and runner.get("valid"))
    model_source = "bundled" if model_dir == _bundled_model_dir() else "downloaded"
    progress = 100 if ready else min(99, int(total_bytes * 100 / WHISPER_MODEL_EXPECTED_BYTES))
    error_code = ""
    if not installed or not av_installed:
        error_code = "dependency_missing"
    elif not runner.get("valid"):
        error_code = "runner_invalid"
    elif not validation.get("valid"):
        error_code = "model_invalid"
    return {
        "dependency_installed": installed,
        "av_installed": av_installed,
        "runner_ready": bool(runner.get("valid")),
        "model": "small",
        "model_dir": str(model_dir),
        "model_source": model_source,
        "integrity": "verified_sha256" if validation.get("valid") else "invalid",
        "integrity_error": str(validation.get("error") or ""),
        "error_code": error_code,
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
    return (Path(__file__).resolve().parent.parent / "whatsapp_transcribe.py").resolve()


def _audio_messages_status() -> dict[str, Any]:
    whisper = _whisper_status()
    with _AUDIO_TRANSCRIPTION_STATE_LOCK:
        queue = dict(_AUDIO_TRANSCRIPTION_STATE)
        raw_telemetry = dict(_AUDIO_TRANSCRIPTION_STATE.get("telemetry") or {})
        telemetry = {
            "stage_counts": dict(raw_telemetry.get("stage_counts") or {}),
            "error_counts": dict(raw_telemetry.get("error_counts") or {}),
            "last": dict(raw_telemetry.get("last") or {}),
        }
    return {
        "ready": bool(whisper.get("ready")),
        "local_only": True,
        "voice_calls_independent": True,
        "engine": str(whisper.get("engine") or "faster-whisper==1.2.1"),
        "model": "small",
        "device": "cpu",
        "compute_type": "int8",
        "limits": {
            "max_bytes": AUDIO_MESSAGE_MAX_BYTES,
            "max_duration_seconds": AUDIO_MESSAGE_MAX_DURATION_SECONDS,
            "max_active": 1,
            "max_waiting": AUDIO_TRANSCRIPTION_MAX_WAITING,
        },
        "retention": {
            "raw_audio": "delete_immediately_after_terminal",
            "transcription_persisted": False,
        },
        "queue": {
            "active": int(queue.get("active") or 0),
            "waiting": int(queue.get("waiting") or 0),
            "accepted": int(queue.get("accepted") or 0),
            "completed": int(queue.get("completed") or 0),
            "failed": int(queue.get("failed") or 0),
            "rejected_busy": int(queue.get("rejected_busy") or 0),
        },
        "last_error_code": _safe_audio_error_code(queue.get("last_error_code"), "") if queue.get("last_error_code") else "",
        "last_latency_ms": max(0, min(int(queue.get("last_latency_ms") or 0), 3_600_000)),
        "telemetry": telemetry,
        "preflight": {
            "dependency_installed": bool(whisper.get("dependency_installed")),
            "av_installed": bool(whisper.get("av_installed")),
            "runner_ready": bool(whisper.get("runner_ready")),
            "model_integrity": str(whisper.get("integrity") or "invalid"),
            "error_code": _safe_audio_error_code(whisper.get("error_code"), "") if whisper.get("error_code") else "",
        },
    }


def _audio_messages_preflight() -> dict[str, Any]:
    status = _whisper_status()
    if not status.get("ready"):
        payload = _audio_messages_status()
        payload["preflight"] = {
            **dict(payload.get("preflight") or {}),
            "model_load": False,
            "error_code": _safe_audio_error_code(status.get("error_code"), "preflight_failed"),
            "latency_ms": 0,
        }
        return {"success": False, "audio_messages": payload}
    if not _AUDIO_TRANSCRIPTION_EXECUTION.acquire(blocking=False):
        payload = _audio_messages_status()
        payload["preflight"] = {
            **dict(payload.get("preflight") or {}),
            "model_load": False,
            "error_code": "resource_busy",
            "latency_ms": 0,
        }
        return {"success": False, "audio_messages": payload}
    output: Optional[Path] = None
    started = time.monotonic()
    try:
        descriptor, output_name = tempfile.mkstemp(prefix="jk-wa-preflight-", suffix=".json", dir=str(_info_dir()))
        os.close(descriptor)
        output = Path(output_name)
        process = subprocess.run(
            [
                sys.executable,
                str(_whisper_runner()),
                "--model-dir",
                str(_model_dir()),
                "--output",
                str(output),
                "--preflight",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        result = _json_read(output, {})
        success = bool(process.returncode == 0 and isinstance(result, dict) and result.get("success"))
        error_code = "" if success else _safe_audio_error_code(
            result.get("error_code") if isinstance(result, dict) else "", "preflight_failed"
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        payload = _audio_messages_status()
        payload["preflight"] = {
            **dict(payload.get("preflight") or {}),
            "model_load": success,
            "error_code": error_code,
            "latency_ms": max(0, min(latency_ms, 180_000)),
        }
        return {"success": success, "audio_messages": payload}
    except subprocess.TimeoutExpired:
        payload = _audio_messages_status()
        payload["preflight"] = {
            **dict(payload.get("preflight") or {}),
            "model_load": False,
            "error_code": "preflight_failed",
            "latency_ms": 180_000,
        }
        return {"success": False, "audio_messages": payload}
    except Exception:
        latency_ms = int((time.monotonic() - started) * 1000)
        payload = _audio_messages_status()
        payload["preflight"] = {
            **dict(payload.get("preflight") or {}),
            "model_load": False,
            "error_code": "preflight_failed",
            "latency_ms": max(0, min(latency_ms, 180_000)),
        }
        return {"success": False, "audio_messages": payload}
    finally:
        try:
            if output is not None:
                output.unlink(missing_ok=True)
        except OSError:
            pass
        _AUDIO_TRANSCRIPTION_EXECUTION.release()

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
            raise RuntimeError(_safe_audio_error_code(result.get("error_code"), "model_invalid"))
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
    _record_audio_stage("received", audio_path=audio_path)
    status = _whisper_status()
    if not status.get("ready"):
        code = str(status.get("error_code") or "model_invalid")
        _record_audio_outcome(success=False, error_code=code, audio_path=audio_path)
        return _audio_failure(code)
    try:
        size = audio_path.stat().st_size
    except OSError:
        _record_audio_outcome(success=False, error_code="audio_missing", audio_path=audio_path)
        return _audio_failure("audio_missing")
    if size <= 0:
        _record_audio_outcome(success=False, error_code="audio_empty", audio_path=audio_path, size_bytes=size)
        return _audio_failure("audio_empty")
    if size > AUDIO_MESSAGE_MAX_BYTES:
        _record_audio_outcome(success=False, error_code="audio_size_limit", audio_path=audio_path, size_bytes=size)
        return _audio_failure("audio_size_limit")
    if not _AUDIO_TRANSCRIPTION_CAPACITY.acquire(blocking=False):
        with _AUDIO_TRANSCRIPTION_STATE_LOCK:
            _AUDIO_TRANSCRIPTION_STATE["rejected_busy"] = int(_AUDIO_TRANSCRIPTION_STATE.get("rejected_busy") or 0) + 1
            _AUDIO_TRANSCRIPTION_STATE["last_error_code"] = "resource_busy"
        _record_audio_outcome(success=False, error_code="resource_busy", audio_path=audio_path, size_bytes=size)
        return _audio_failure("resource_busy")
    with _AUDIO_TRANSCRIPTION_STATE_LOCK:
        _AUDIO_TRANSCRIPTION_STATE["waiting"] = int(_AUDIO_TRANSCRIPTION_STATE.get("waiting") or 0) + 1
        _AUDIO_TRANSCRIPTION_STATE["accepted"] = int(_AUDIO_TRANSCRIPTION_STATE.get("accepted") or 0) + 1
    acquired_execution = False
    waiting_registered = True
    output: Optional[Path] = None
    started = time.monotonic()
    try:
        descriptor, output_name = tempfile.mkstemp(prefix="jk-wa-transcript-", suffix=".json", dir=str(_info_dir()))
        os.close(descriptor)
        output = Path(output_name)
        acquired_execution = _AUDIO_TRANSCRIPTION_EXECUTION.acquire(timeout=AUDIO_TRANSCRIPTION_QUEUE_WAIT_SECONDS)
        with _AUDIO_TRANSCRIPTION_STATE_LOCK:
            _AUDIO_TRANSCRIPTION_STATE["waiting"] = max(0, int(_AUDIO_TRANSCRIPTION_STATE.get("waiting") or 0) - 1)
            waiting_registered = False
            if acquired_execution:
                _AUDIO_TRANSCRIPTION_STATE["active"] = 1
        if not acquired_execution:
            result = _audio_failure("queue_timeout")
            _record_audio_outcome(success=False, error_code="queue_timeout", latency_ms=int((time.monotonic() - started) * 1000), audio_path=audio_path, size_bytes=size)
            return result
        _record_audio_stage("transcription_started", audio_path=audio_path, size_bytes=size)
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
        if not isinstance(result, dict):
            result = _audio_failure("child_failed")
        elif process.returncode != 0 or result.get("success") is not True:
            result = _audio_failure(result.get("error_code") or "child_failed")
        else:
            text = str(result.get("text") or "").strip()
            if not text:
                result = _audio_failure("no_speech")
            else:
                result = {
                    "success": True,
                    "text": text[:12000],
                    "duration_seconds": max(0.0, min(float(result.get("duration_seconds") or 0.0), 600.0)),
                    "confidence": result.get("confidence"),
                    "language": str(result.get("language") or "pt")[:16],
                    "language_probability": result.get("language_probability"),
                    "used_detection_fallback": result.get("used_detection_fallback") is True,
                    "local_only": True,
                }
        latency_ms = int((time.monotonic() - started) * 1000)
        _record_audio_outcome(
            success=result.get("success") is True,
            error_code=str(result.get("error_code") or ""),
            latency_ms=latency_ms,
            audio_path=audio_path,
            size_bytes=size,
            duration_seconds=float(result.get("duration_seconds") or -1),
        )
        return result
    except subprocess.TimeoutExpired:
        _record_audio_outcome(success=False, error_code="transcription_timeout", latency_ms=int((time.monotonic() - started) * 1000), audio_path=audio_path, size_bytes=size)
        return _audio_failure("transcription_timeout")
    except Exception:
        _record_audio_outcome(success=False, error_code="child_failed", latency_ms=int((time.monotonic() - started) * 1000), audio_path=audio_path, size_bytes=size)
        return _audio_failure("child_failed")
    finally:
        try:
            if output is not None:
                output.unlink(missing_ok=True)
        except OSError:
            pass
        if acquired_execution:
            with _AUDIO_TRANSCRIPTION_STATE_LOCK:
                _AUDIO_TRANSCRIPTION_STATE["active"] = 0
            _AUDIO_TRANSCRIPTION_EXECUTION.release()
        elif waiting_registered:
            with _AUDIO_TRANSCRIPTION_STATE_LOCK:
                _AUDIO_TRANSCRIPTION_STATE["waiting"] = max(0, int(_AUDIO_TRANSCRIPTION_STATE.get("waiting") or 0) - 1)
        _AUDIO_TRANSCRIPTION_CAPACITY.release()

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


_COMPONENT_FUNCTIONS = frozenset((
    '_directory_size',
    '_whisper_status',
    '_whisper_runner',
    '_audio_messages_status',
    '_audio_messages_preflight',
    '_download_whisper_worker',
    'whatsapp_bridge_download_whisper',
    '_transcribe_audio',
    '_safe_filename',
    '_media_extension',
    '_download_media'
))
_IMPLEMENTATIONS = {
    '_directory_size': _directory_size,
    '_whisper_status': _whisper_status,
    '_whisper_runner': _whisper_runner,
    '_audio_messages_status': _audio_messages_status,
    '_audio_messages_preflight': _audio_messages_preflight,
    '_download_whisper_worker': _download_whisper_worker,
    'whatsapp_bridge_download_whisper': whatsapp_bridge_download_whisper,
    '_transcribe_audio': _transcribe_audio,
    '_safe_filename': _safe_filename,
    '_media_extension': _media_extension,
    '_download_media': _download_media
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
