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


_COMPONENT_FUNCTIONS = frozenset((
    '_directory_size',
    '_whisper_status',
    '_whisper_runner',
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
