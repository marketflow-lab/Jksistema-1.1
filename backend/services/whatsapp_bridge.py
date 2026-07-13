"""Local zero-cost WhatsApp bridge for Black Jhon.

The public webhook lives at Cloudflare. This module only polls the authenticated
bridge endpoints, keeps media and transcription local, and creates tasks through
the same internal Codex task core used by the existing HTTP API.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo

import requests
from fastapi import Header, HTTPException, Request
from pydantic import BaseModel

from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services import admin_usuarios_common, codex_actions, codex_console, whatsapp_report_visuals


ZERO_COST_POLICY_VALID_UNTIL = "2026-09-30T23:59:59Z"
POLL_SECONDS = 3
CLAIM_LIMIT = 5
TYPING_REFRESH_SECONDS = 20
TYPING_MAX_SECONDS = 10 * 60
TYPING_MAX_CONSECUTIVE_ERRORS = 3
TRANSCRIPTION_TIMEOUT_SECONDS = 600
WHISPER_MODEL_EXPECTED_BYTES = 488_000_000
WHATSAPP_GATEWAY_PROTOCOL_VERSION = 1
SUPPORTED_IMAGE_MIMES = {"image/jpeg": 5 * 1024 * 1024, "image/png": 5 * 1024 * 1024}
SUPPORTED_AUDIO_MIMES = {
    "audio/aac": 16 * 1024 * 1024,
    "audio/mp4": 16 * 1024 * 1024,
    "audio/mpeg": 16 * 1024 * 1024,
    "audio/amr": 16 * 1024 * 1024,
    "audio/ogg": 16 * 1024 * 1024,
}
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
APPROVAL_CODE_TTL_SECONDS = 10 * 60
WHATSAPP_PART_BODY_CHARS = 2600
WHATSAPP_ADHOC_MESSAGE_CHARS = 3500
WHATSAPP_MAX_PARTS = 8
WHATSAPP_EXACT_ORDER_HISTORY_MARKER = "<!-- JK_EXACT_ORDER_FULL_HISTORY -->"
WHATSAPP_REPORT_MAX_PARTS = 30
WHATSAPP_REPORT_BODY_CHARS = 2100
WHATSAPP_REPORT_RANKING_ITEMS_PER_PART = 8
WHATSAPP_QUERY_CONTEXT_TTL_SECONDS = 24 * 3600
WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS = 30 * 60
WHATSAPP_MAX_OUTBOUND_IMAGES = 3
WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES = 5 * 1024 * 1024
WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS = 40_000_000
WHATSAPP_WEEKLY_REPORT_START_HOUR = 8
WHATSAPP_IMAGE_MARKDOWN_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(?:\s+['\"][^)]*['\"])?\s*\)", re.IGNORECASE)
APPROVAL_COMMAND_RE = re.compile(
    r"^(APROVAR|CONFIRMAR|NEGAR|REJEITAR|CANCELAR)\s+([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_COMMAND_RE = re.compile(
    r"^ppv_(approve|reject|regenerate|suggest):([A-Z2-9]{8})$",
    re.IGNORECASE,
)
QUESTION_APPROVAL_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
STORE_SELECTION_COMMAND_RE = re.compile(r"^store_select:([A-Z2-9]{8})$", re.IGNORECASE)
STORE_SELECTION_TOKEN_TTL_SECONDS = 10 * 60
WHATSAPP_AI_DEFAULT_MODEL = "codex:gpt-5.5"
WHATSAPP_CODEX_REASONING_DEFAULT = "xhigh"
WHATSAPP_CODEX_REASONING_OPTIONS = ("low", "medium", "high", "xhigh")


class WhatsappBridgeConfigRequest(BaseModel):
    worker_url: Optional[str] = None
    bridge_token: Optional[str] = None
    business_phone: Optional[str] = None
    ai_model: Optional[str] = None
    codex_reasoning_effort: Optional[str] = None
    enabled: Optional[bool] = None


class WhatsappTemplatesRequest(BaseModel):
    create_missing: bool = False


class WhatsappPairingCodeRequest(BaseModel):
    username: Optional[str] = None
    client_id: Optional[str] = None


class WhatsappBindingRevokeRequest(BaseModel):
    subject_id: Optional[str] = None
    revoke_all: bool = False
    username: Optional[str] = None
    client_id: Optional[str] = None


class WhatsappPhoneSettingsRequest(BaseModel):
    subject_id: str
    username: str
    client_id: str
    label: Optional[str] = None
    send_ml_question_suggestions: bool = True
    send_weekly_report: bool = False
    send_monthly_report: bool = False
    ai_behavior: Optional[str] = None


class WhatsappPhoneRegistrationRequest(BaseModel):
    username: str
    client_id: str
    name: str
    phone_number: str
    send_ml_question_suggestions: bool = True
    send_weekly_report: bool = False
    send_monthly_report: bool = False
    welcome_message: Optional[str] = None
    send_welcome_message: bool = False


class WhatsappAdhocMessageRequest(BaseModel):
    phone_number: str
    message: str


CONFIG_LOCK = threading.RLock()
BRIDGE_STOP_EVENT = threading.Event()
BRIDGE_THREAD: Optional[threading.Thread] = None
DOWNLOAD_THREAD: Optional[threading.Thread] = None
ALERT_THREAD: Optional[threading.Thread] = None
TYPING_PULSES_LOCK = threading.RLock()
TYPING_PULSES: dict[str, threading.Event] = {}
RUNTIME_STATE: dict[str, Any] = {
    "running": False,
    "last_error": "",
    "last_processing_at": "",
    "last_worker_ok_at": "",
    "typing_last_error": "",
    "typing_last_sent_at": "",
    "whisper_download_status": "idle",
    "whisper_download_error": "",
    "whisper_download_started_at": "",
}
MODEL_VALIDATION_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


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
    from backend.services import ia_providers

    raw = str(value or WHATSAPP_AI_DEFAULT_MODEL).strip()
    if len(raw) > 100 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", raw):
        raise HTTPException(status_code=400, detail="Modelo de IA do WhatsApp invalido.")
    return ia_providers._normalizar_ia_modelo_padrao(raw)


def _normalize_codex_reasoning_effort(value: Any) -> str:
    effort = str(value or WHATSAPP_CODEX_REASONING_DEFAULT).strip().lower()
    if effort not in WHATSAPP_CODEX_REASONING_OPTIONS:
        raise HTTPException(status_code=400, detail="Padrao de inteligencia do Codex invalido.")
    return effort


def _whatsapp_ai_settings(config: Optional[dict[str, Any]] = None) -> dict[str, str]:
    source = config if isinstance(config, dict) else _load_config()
    model = _normalize_ai_model(source.get("ai_model"))
    reasoning = _normalize_codex_reasoning_effort(source.get("codex_reasoning_effort"))
    if model.startswith("codex:"):
        provider = "codex"
    elif model.startswith("vertex:"):
        provider = "vertex"
    elif model.startswith("gemini:"):
        provider = "gemini"
    elif model.startswith("deepseek-"):
        provider = "deepseek"
    else:
        provider = "openai"
    return {"model": model, "provider": provider, "codex_reasoning_effort": reasoning}


def _default_phone_notification_settings() -> dict[str, Any]:
    return {
        "label": "",
        "send_ml_question_suggestions": True,
        "send_weekly_report": False,
        "send_monthly_report": False,
        "ai_behavior": "",
    }


def _normalize_phone_ai_behavior(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()[:2000]


def _normalize_phone_notification_settings(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    result = _default_phone_notification_settings()
    result.update(
        {
            "label": re.sub(r"\s+", " ", str(source.get("label") or "")).strip()[:60],
            "send_ml_question_suggestions": source.get("send_ml_question_suggestions") is not False,
            "send_weekly_report": source.get("send_weekly_report") is True,
            "send_monthly_report": source.get("send_monthly_report") is True,
            "ai_behavior": _normalize_phone_ai_behavior(source.get("ai_behavior")),
        }
    )
    return result


def _phone_notification_settings(
    config: dict[str, Any],
    subject_id: Any,
    *,
    client_id: Any = "",
    username: Any = "",
) -> dict[str, Any]:
    subject = str(subject_id or "").strip()
    settings_by_phone = config.get("phone_notification_settings")
    stored = settings_by_phone.get(subject) if subject and isinstance(settings_by_phone, dict) else {}
    result = _normalize_phone_notification_settings(stored)
    result.update(
        {
            "subject_id": subject,
            "client_id": str(client_id or (stored.get("client_id") if isinstance(stored, dict) else "") or "").strip(),
            "username": str(username or (stored.get("username") if isinstance(stored, dict) else "") or "").strip().lower(),
        }
    )
    return result


def _default_config() -> dict[str, Any]:
    return {
        "version": 3,
        "worker_url": "",
        "bridge_token": "",
        "business_phone": "",
        "ai_model": WHATSAPP_AI_DEFAULT_MODEL,
        "codex_reasoning_effort": WHATSAPP_CODEX_REASONING_DEFAULT,
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
        result["enabled"] = bool(result.get("enabled"))
        result["pairing_pending"] = bool(result.get("pairing_pending"))
        result["machine_id"] = str(result.get("machine_id") or _host_machine_id())
        result["ai_model"] = _normalize_ai_model(result.get("ai_model"))
        result["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(result.get("codex_reasoning_effort"))
        if not isinstance(result.get("phone_notification_settings"), dict):
            result["phone_notification_settings"] = {}
        return result


def _save_config(config: dict[str, Any]) -> dict[str, Any]:
    with CONFIG_LOCK:
        value = _default_config()
        value.update(dict(config or {}))
        value["ai_model"] = _normalize_ai_model(value.get("ai_model"))
        value["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(value.get("codex_reasoning_effort"))
        if not isinstance(value.get("phone_notification_settings"), dict):
            value["phone_notification_settings"] = {}
        value["updated_at"] = _now()
        _json_write(_config_path(), value)
        return value


def _load_state() -> dict[str, Any]:
    with CONFIG_LOCK:
        value = _json_read(_state_path(), {})
        return value if isinstance(value, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    with CONFIG_LOCK:
        _json_write(_state_path(), state)


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
    lines = str(text or "").splitlines()
    output: list[str] = []
    index = 0
    separator_re = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

    def cells(line: str) -> list[str]:
        return [item.strip() for item in line.strip().strip("|").split("|")]

    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index] and separator_re.match(lines[index + 1] or ""):
            headers = cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(cells(lines[index]))
                index += 1
            for row in rows:
                fields = []
                for pos, value in enumerate(row):
                    if not value:
                        continue
                    label = headers[pos] if pos < len(headers) and headers[pos] else f"Campo {pos + 1}"
                    fields.append(f"*{label}:* {value}")
                if fields:
                    output.append("• " + "\n  ".join(fields))
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _whatsapp_clean_markdown(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = text.replace(WHATSAPP_EXACT_ORDER_HISTORY_MARKER, "").strip()
    text = _whatsapp_table_blocks_to_mobile(text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+(.+?)\s*$", r"*\1*", text)
    text = re.sub(r"\*\*([^\n*]+?)\*\*", r"*\1*", text)
    text = re.sub(r"__([^\n_]+?)__", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    text = re.sub(r"(?m)^\s*[-+]\s+", "• ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _whatsapp_image_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    if not re.search(r"\b(foto|fotos|imagem|imagens)\b", text):
        return False
    explicit_send = re.search(r"\b(manda|mandar|envia|enviar|mostra|mostrar|quero|preciso|consigo|consegue|pode)\b", text)
    product_context = re.search(r"\b(sku|produto|cadastro|anuncio)\b", text)
    return bool(explicit_send or product_context)


def _whatsapp_image_references(value: Any) -> list[tuple[str, str]]:
    text = str(value or "")
    references: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in WHATSAPP_IMAGE_MARKDOWN_RE.finditer(text):
        alt = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
        ref = str(match.group(2) or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            references.append((alt, ref))
    for match in re.finditer(r"(?:/api/cadastro/foto-(?:arquivo/)?|cadastro_fotos/)[^\s)>'\"]+", text, flags=re.IGNORECASE):
        ref = str(match.group(0) or "").rstrip(".,;:")
        if ref and ref not in seen:
            seen.add(ref)
            references.append(("", ref))
    return references[:WHATSAPP_MAX_OUTBOUND_IMAGES]


def _whatsapp_path_within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


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
    text = str(value or "")
    result: list[str] = []
    for match in re.finditer(r"\bSKU\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{0,40})", text, flags=re.IGNORECASE):
        sku = str(match.group(1) or "").strip().rstrip(".,;:")
        if sku and sku.upper() not in {item.upper() for item in result}:
            result.append(sku)
    return result[:10]


def _whatsapp_normalized_sku(value: Any, *, strip_numeric_zeroes: bool = False) -> str:
    text = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if strip_numeric_zeroes:
        text = re.sub(r"\d+", lambda match: str(int(match.group(0))), text)
    return text


def _whatsapp_image_matches_skus(path: Path, skus: list[str]) -> bool:
    stem = _whatsapp_normalized_sku(path.stem)
    stem_compact = _whatsapp_normalized_sku(path.stem, strip_numeric_zeroes=True)
    for sku in skus:
        exact = _whatsapp_normalized_sku(sku)
        compact = _whatsapp_normalized_sku(sku, strip_numeric_zeroes=True)
        if stem == exact or (compact and stem_compact == compact):
            return True
    return False


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
    try:
        with path.open("rb") as handle:
            signature = handle.read(12)
    except OSError:
        return ""
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if signature.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
        return "image/webp"
    if signature.startswith(b"BM"):
        return "image/bmp"
    return ""


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
    text = WHATSAPP_IMAGE_MARKDOWN_RE.sub("", str(value or ""))
    text = re.sub(r"(?:/api/cadastro/foto-(?:arquivo/)?|cadastro_fotos/)[^\s)>'\"]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _whatsapp_outbound_image_caption(response: Any, alt: Any = "") -> str:
    clean = _whatsapp_strip_image_references(response)
    for line in clean.splitlines():
        candidate = re.sub(r"^[#*•\s]+|[*_`]+$", "", line).strip()
        if candidate and not _whatsapp_report_section_kind(candidate):
            return candidate[:1024]
    return (str(alt or "Imagem solicitada ao Black Jhon").strip() or "Imagem solicitada ao Black Jhon")[:1024]


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


def _whatsapp_deliver_report_artifacts(
    config: dict[str, Any],
    message_id: str,
    artifacts: Any,
    client_id: Any,
    max_images: int = 2,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    limit = max(0, min(int(max_images or 0), 2, WHATSAPP_MAX_OUTBOUND_IMAGES))
    for artifact in [item for item in (artifacts or []) if isinstance(item, dict)][:limit]:
        path = _whatsapp_report_chart_path(artifact, client_id)
        if not path:
            results.append({"success": False, "error": "report_chart_invalid_or_expired"})
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
                artifact_type="report_chart",
            )
            results.append(
                {
                    "success": bool(result.get("success")),
                    "status": result.get("status"),
                    "artifact_type": "report_chart",
                    "kind": artifact.get("kind"),
                    "filename": path.name,
                    "error": result.get("error"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "success": False,
                    "artifact_type": "report_chart",
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
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return results


def _whatsapp_text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def _whatsapp_daily_sales_report_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    )


def _whatsapp_sales_report_requested(value: Any) -> bool:
    text = _whatsapp_text_key(value)
    return bool(
        _whatsapp_daily_sales_report_requested(value)
        or (
            re.search(r"\b(relatorio|resumo|analise)\b", text)
            and re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|mercado livre|mercadolivre)\b", text)
        )
    )


def _whatsapp_query_only_domains(value: Any) -> list[str]:
    """Classifica os dominios que nunca podem sofrer mutacao pelo WhatsApp."""
    text = _whatsapp_text_key(value)
    domains: list[str] = []
    if _whatsapp_daily_sales_report_requested(value) or re.search(
        r"\b(venda|vendas|vendido|vendidos|faturamento|pedidos?|devolucao|devolucoes|sync de vendas|sincronizacao de vendas)\b",
        text,
    ):
        domains.append("vendas")
    if re.search(r"\b(mercado livre|mercadolivre|anuncio|anuncios|mlb\d+)\b", text):
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
    text = str(value or "").strip()
    text = re.sub(r"^\*+|\*+$", "", text).strip()
    text = re.sub(r"^`+|`+$", "", text).strip()
    return re.sub(r"\s+", " ", text)


def _whatsapp_field(line: Any) -> tuple[str, str]:
    text = str(line or "").strip()
    text = re.sub(r"^(?:[•*-]|\d+[.)])\s*", "", text).strip()
    match = re.match(r"^\*([^*:\n]{1,60}?):\*\s*(.+?)\s*$", text)
    if not match:
        match = re.match(r"^([^:\n]{1,60}?):\s*(.+?)\s*$", text)
    if not match:
        return "", ""
    return _whatsapp_plain_inline(match.group(1)), _whatsapp_plain_inline(match.group(2))


def _whatsapp_heading_value(line: Any) -> str:
    text = str(line or "").strip()
    match = re.match(r"^\*([^*\n]{2,90})\*$", text)
    if match:
        return _whatsapp_plain_inline(match.group(1)).rstrip(":").strip()
    if len(text) <= 70 and not re.search(r"[.!?]$", text):
        key = _whatsapp_text_key(text.rstrip(":"))
        known = (
            "dados principais",
            "indicadores",
            "resumo",
            "resumo executivo",
            "skus mais vendidos",
            "produtos mais vendidos",
            "mais vendidos",
            "ranking",
            "analise",
            "insights",
            "conclusao",
            "proximas acoes",
            "recomendacoes",
            "fontes",
            "fontes e cobertura",
            "onde consultou",
            "qualidade dos dados",
            "avisos",
        )
        if key in known:
            return text.rstrip(":").strip()
    return ""


def _whatsapp_report_section_kind(heading: Any) -> str:
    key = _whatsapp_text_key(heading)
    if not key:
        return ""
    if key.startswith("relatorio"):
        return "report_title"
    if any(token in key for token in ("mais vendido", "skus vendidos", "sku vendidos", "ranking", "top sku", "top produto")):
        return "ranking"
    if any(token in key for token in ("fonte", "onde consult", "cobertura", "qualidade dos dados", "aviso")):
        return "sources"
    if any(token in key for token in ("analise", "insight", "conclusao", "proxima acao", "proximas acoes", "recomend")):
        return "analysis"
    if any(token in key for token in ("dados principais", "indicadores", "resumo", "visao geral")):
        return "summary"
    return ""


def _whatsapp_report_sections(value: Any) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"summary": [], "ranking": [], "analysis": [], "sources": []}
    current = "summary"
    for raw_line in _whatsapp_clean_markdown(value).splitlines():
        line = raw_line.rstrip()
        field_label, field_value = _whatsapp_field(line)
        field_kind = _whatsapp_report_section_kind(field_label)
        if field_value and field_kind in {"analysis", "sources"}:
            current = field_kind
            # Preserve o rótulo: ele informa se a cobertura foi completa ou
            # parcial. Removê-lo deixava apenas "todas as páginas..." e podia
            # tornar ambígua a situação do relatório no WhatsApp.
            sections[current].append(f"{field_label}: {field_value}")
            continue
        heading = _whatsapp_heading_value(line)
        kind = _whatsapp_report_section_kind(heading)
        if kind == "report_title":
            if heading:
                sections["summary"].append(f"*{heading}*")
            current = "summary"
            continue
        if kind in sections:
            current = kind
            continue
        sections[current].append(line)
    for key, lines in sections.items():
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        sections[key] = lines
    return sections


def _whatsapp_metric_label(value: Any) -> str:
    label = _whatsapp_plain_inline(value)
    key = _whatsapp_text_key(label)
    aliases = (
        (("quantidade vendida", "itens vendidos", "unidades vendidas"), "Itens vendidos"),
        (("faturamento bruto", "faturamento total", "faturamento"), "Faturamento"),
        (("resultado apos devolucoes", "receita liquida", "resultado liquido"), "Receita líquida"),
        (("valor devolvido", "valor de devolucoes"), "Valor devolvido"),
        (("ticket medio",), "Ticket médio"),
        (("devolucoes", "itens devolvidos"), "Devoluções"),
        (("quantidade de pedidos", "pedidos"), "Pedidos"),
    )
    for names, compact in aliases:
        if key in names:
            return compact
    return label


def _whatsapp_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _whatsapp_money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return "R$ " + f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _whatsapp_report_date(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else raw


def _whatsapp_daily_ml_sales_report(
    tool_results: list[dict[str, Any]],
    query_policy: dict[str, Any],
    request_text: Any,
) -> str:
    """Monta o relatorio diario diretamente do agregado da API do Mercado Livre."""
    if not _whatsapp_sales_report_requested(request_text):
        return ""
    # Com varias lojas, preserve os blocos separados gerados pelo agente; usar
    # apenas o primeiro resultado esconderia as demais contas selecionadas.
    if str(query_policy.get("store_mode") or "") == "all":
        return ""
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    if "mercado_livre_orders" not in (source_policy.get("required_tools") or []):
        return ""
    result = next(
        (
            item for item in tool_results
            if isinstance(item, dict) and str(item.get("tool_id") or "") == "mercado_livre_orders"
        ),
        None,
    )
    if not isinstance(result, dict) or result.get("success") is not True:
        return ""

    summaries = [item for item in (result.get("summary") or []) if isinstance(item, dict)]
    primary = next(
        (item for item in summaries if str(item.get("tool_id") or "") == "mercado_livre_orders"),
        summaries[0] if summaries else {},
    )
    data = primary.get("summary") if isinstance(primary.get("summary"), dict) else {}
    if data.get("api_consulted") is False or data.get("error"):
        return ""
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
    rows = [row for row in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(row, dict)]
    rows = [
        row for row in rows
        if str(
            row.get("sku")
            or row.get("seller_sku")
            or row.get("item_id")
            or row.get("mlb")
            or row.get("id")
            or row.get("title")
            or ""
        ).strip()
    ]

    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    requested_period = period.get("requested") if isinstance(period.get("requested"), dict) else {}
    start = _whatsapp_report_date(requested_period.get("from") or (primary.get("periodo") or {}).get("data_inicio"))
    end = _whatsapp_report_date(requested_period.get("to") or (primary.get("periodo") or {}).get("data_fim"))
    period_label = start if start and start == end else f"{start} a {end}".strip(" a")
    store = str(query_policy.get("store") or primary.get("loja") or data.get("store") or "").strip()

    lines = [f"# Relatório de vendas — {store or 'Mercado Livre'}"]
    if period_label:
        lines.append(f"Período: {period_label}")
    if store:
        lines.append(f"Loja: {store}")
    lines.extend([
        "",
        "## Dados principais",
        f"- **Pedidos:** {_whatsapp_number(totals.get('orders'))}",
        f"- **Itens vendidos:** {_whatsapp_number(totals.get('items_quantity'))}",
        f"- **Valor bruto:** {_whatsapp_money(totals.get('gross_amount'))}",
        f"- **Valor pago:** {_whatsapp_money(totals.get('paid_amount'))}",
    ])
    if totals.get("refund_amount") is None:
        lines.append("- **Estornos:** indisponível")
        lines.append("- **Valor líquido:** indisponível")
    else:
        lines.append(f"- **Estornos:** {_whatsapp_money(totals.get('refund_amount'))}")
        lines.append(f"- **Valor líquido:** {_whatsapp_money(totals.get('net_amount'))}")

    lines.extend(["", "## SKUs vendidos"])
    if rows:
        for row in rows:
            sku = _whatsapp_plain_inline(
                row.get("sku")
                or row.get("seller_sku")
                or row.get("item_id")
                or row.get("mlb")
                or row.get("id")
                or "não informado"
            )
            title = _whatsapp_plain_inline(row.get("title") or row.get("produto") or row.get("nome") or "Produto sem título")
            quantity = float(row.get("quantity") or 0)
            gross = float(row.get("gross_amount") or 0)
            unit = gross / quantity if quantity > 0 else 0.0
            lines.extend([
                f"- **SKU:** {sku}",
                f"  **Produto:** {title}",
                f"  **Qtd.:** {_whatsapp_number(quantity)}",
                f"  **Valor unitário médio:** {_whatsapp_money(unit)}",
                f"  **Total vendido:** {_whatsapp_money(gross)}",
            ])
    else:
        lines.append("Nenhum SKU vendido no período consultado.")

    coverage_complete = (
        data.get("coverage_complete") is not False
        and not bool(data.get("truncated"))
        and not bool(paging.get("has_more"))
    )
    pages = int(paging.get("pages_fetched") or 0)
    scanned_orders = int(paging.get("scanned") or paging.get("returned") or totals.get("orders") or 0)
    considered_orders = int(totals.get("orders") or paging.get("returned") or 0)
    lines.extend([
        "",
        "## Fontes e cobertura",
        (
            f"Consulta direta à API do Mercado Livre da loja {store or 'selecionada'}, "
            f"no período {period_label or 'informado'}, com {pages} página(s), "
            f"{scanned_orders} pedido(s) verificado(s), {considered_orders} pedido(s) considerado(s) "
            f"e {len(rows)} SKU(s) consolidado(s)."
        ),
    ])
    if coverage_complete:
        lines.append("Cobertura completa para o período informado.")
    else:
        lines.append("A API indicou mais registros além da cobertura recebida; o relatório está incompleto e não estimou valores.")
    return "\n".join(lines).strip()


def _whatsapp_report_summary(lines: list[str]) -> str:
    report_name = ""
    metadata: list[tuple[str, str]] = []
    metrics: list[tuple[str, str]] = []
    notes: list[str] = []
    metadata_keys = {"periodo", "conta", "loja", "filtro", "filtros"}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        heading = _whatsapp_heading_value(stripped)
        if heading and _whatsapp_report_section_kind(heading) == "report_title":
            report_name = re.sub(r"(?i)^relat[oó]rio\s+(?:de\s+)?", "", heading).strip(" —-") or heading
            if report_name:
                report_name = report_name[:1].upper() + report_name[1:]
            continue
        label, value = _whatsapp_field(stripped)
        if label and value:
            key = _whatsapp_text_key(label)
            if key in metadata_keys:
                metadata.append((key, value))
            else:
                metrics.append((_whatsapp_metric_label(label), value))
            continue
        notes.append(stripped)

    output: list[str] = []
    if report_name:
        output.append(f"*{report_name}*")
    icons = {"periodo": "🗓", "conta": "🏪", "loja": "🏪", "filtro": "🔎", "filtros": "🔎"}
    for key, value in metadata:
        output.append(f"{icons.get(key, '•')} {value}")
    if metrics:
        output.append("\n*📌 INDICADORES*")
        output.extend(f"*{label}:* {value}" for label, value in metrics)
    if notes:
        output.append("\n".join(notes))
    return "\n".join(output).strip()


def _whatsapp_ranking_items(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        label, value = _whatsapp_field(stripped)
        key = _whatsapp_text_key(label)
        if label and value and key in {"sku", "codigo", "codigo sku", "produto sku"}:
            if current:
                items.append(current)
            current = {"sku": value, "product": "", "quantity": "", "unit_value": "", "value": "", "extra": []}
            continue
        if current is None:
            current = {"sku": "", "product": "", "quantity": "", "unit_value": "", "value": "", "extra": []}
        if label and value:
            if key in {"produto", "descricao", "nome", "titulo"}:
                current["product"] = value
            elif key in {"qtd", "qtde", "quantidade", "unidades", "quantidade vendida"}:
                current["quantity"] = value
            elif key in {"valor unitario", "valor unitario medio", "preco unitario", "preco medio"}:
                current["unit_value"] = value
            elif key in {"valor", "valor vendido", "total vendido", "faturamento", "total", "receita"}:
                current["value"] = value
            else:
                current["extra"].append(f"{label}: {value}")
        else:
            current["extra"].append(stripped)
    if current:
        items.append(current)
    return [item for item in items if any(item.get(field) for field in ("sku", "product", "quantity", "value", "extra"))]


def _whatsapp_rank_marker(index: int) -> str:
    markers = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
    return markers[index - 1] if 1 <= index <= len(markers) else f"{index}."


def _whatsapp_report_ranking_parts(lines: list[str]) -> list[str]:
    items = _whatsapp_ranking_items(lines)
    if not items:
        fallback = "\n".join(lines).strip()
        return [f"*📦 SKUs VENDIDOS*\n\n{fallback}"] if fallback else []

    cards: list[str] = []
    for index, item in enumerate(items, 1):
        sku = _whatsapp_plain_inline(item.get("sku") or "não informado")
        product = _whatsapp_plain_inline(item.get("product"))
        card = [f"{_whatsapp_rank_marker(index)} *SKU {sku}*"]
        if product:
            card.append(product)
        details = []
        if item.get("quantity"):
            details.append(f"Qtd. {_whatsapp_plain_inline(item['quantity'])}")
        if item.get("unit_value"):
            details.append(f"Unit. {_whatsapp_plain_inline(item['unit_value'])}")
        if item.get("value"):
            details.append(f"Total {_whatsapp_plain_inline(item['value'])}")
        if details:
            card.append("`" + "  |  ".join(details) + "`")
        card.extend(_whatsapp_plain_inline(value) for value in item.get("extra") or [] if _whatsapp_plain_inline(value))
        cards.append("\n".join(card))

    parts: list[str] = []
    current: list[str] = []
    for card in cards:
        candidate = "\n\n".join(current + [card])
        if current and (
            len(current) >= WHATSAPP_REPORT_RANKING_ITEMS_PER_PART
            or len(candidate) > WHATSAPP_REPORT_BODY_CHARS - 80
        ):
            parts.append("*📦 SKUs VENDIDOS*\n\n" + "\n\n".join(current))
            current = [card]
        else:
            current.append(card)
    if current:
        parts.append("*📦 SKUs VENDIDOS*\n\n" + "\n\n".join(current))
    return parts


def _whatsapp_report_text_section(title: str, lines: list[str]) -> list[str]:
    body = "\n".join(lines).strip()
    if not body:
        return []
    return [f"*{title}*\n\n{part}" for part in _whatsapp_split_body(body, WHATSAPP_REPORT_BODY_CHARS - 80)]


def _whatsapp_report_response_parts(value: Any, title: str) -> list[str]:
    sections = _whatsapp_report_sections(value)
    semantic_parts: list[tuple[str, str]] = []
    summary = _whatsapp_report_summary(sections["summary"])
    if summary:
        semantic_parts.append(("Resumo", summary))
    semantic_parts.extend(("SKUs vendidos", part) for part in _whatsapp_report_ranking_parts(sections["ranking"]))
    semantic_parts.extend(("Análise", part) for part in _whatsapp_report_text_section("🔎 ANÁLISE", sections["analysis"]))
    semantic_parts.extend(("Fontes", part) for part in _whatsapp_report_text_section("🧾 FONTES E COBERTURA", sections["sources"]))
    if not semantic_parts:
        semantic_parts = [("Resumo", part) for part in _whatsapp_split_body(value, WHATSAPP_REPORT_BODY_CHARS)]
    if len(semantic_parts) > WHATSAPP_REPORT_MAX_PARTS:
        semantic_parts = semantic_parts[:WHATSAPP_REPORT_MAX_PARTS]
        label, last = semantic_parts[-1]
        semantic_parts[-1] = (label, last[: WHATSAPP_REPORT_BODY_CHARS - 100].rstrip() + "\n\n_Conteúdo adicional disponível no histórico do Black Jhon._")

    total = len(semantic_parts)
    result: list[str] = []
    for index, (label, body) in enumerate(semantic_parts, 1):
        counter = f" • {index} de {total}" if total > 1 else ""
        heading = "*📊 Relatório*\n" if index == 1 else ""
        result.append(
            f"{heading}"
            f"_{label}{counter}_\n"
            f"{body}"
        )
    return result


def _whatsapp_split_body(
    value: Any,
    limit: int = WHATSAPP_PART_BODY_CHARS,
    max_parts: Optional[int] = WHATSAPP_MAX_PARTS,
) -> list[str]:
    text = _whatsapp_clean_markdown(value)
    if not text:
        return []
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= limit:
            units.append(paragraph)
            continue
        current_lines: list[str] = []
        current_size = 0
        for line in paragraph.splitlines() or [paragraph]:
            line = line.strip()
            while len(line) > limit:
                if current_lines:
                    units.append("\n".join(current_lines))
                    current_lines, current_size = [], 0
                cut = line.rfind(" ", 0, limit)
                cut = cut if cut >= max(200, limit // 2) else limit
                units.append(line[:cut].rstrip())
                line = line[cut:].lstrip()
            projected = current_size + (1 if current_lines else 0) + len(line)
            if current_lines and projected > limit:
                units.append("\n".join(current_lines))
                current_lines, current_size = [], 0
            if line:
                current_lines.append(line)
                current_size += (1 if current_size else 0) + len(line)
        if current_lines:
            units.append("\n".join(current_lines))

    parts: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}\n\n{unit}"
        if current and len(candidate) > limit:
            parts.append(current)
            current = unit
        else:
            current = candidate
    if current:
        parts.append(current)
    if max_parts is not None and max_parts > 0 and len(parts) > max_parts:
        kept = parts[:max_parts]
        kept[-1] = kept[-1][: max(1, limit - 110)].rstrip() + "\n\n_Conteudo adicional disponivel no historico do Black Jhon._"
        parts = kept
    return parts


def _whatsapp_operational_title(title: Any) -> bool:
    key = _whatsapp_text_key(title)
    return bool(
        re.search(
            r"\b(confirmacao|aprovar|aprovacao|autorizada|negada|rejeitada|bloqueado|acesso negado|"
            r"nao concluido|erro|falha|codigo|decisao|expirada|seguranca|aguardando janela|somente consulta)\b",
            key,
        )
    )


def _whatsapp_response_parts(value: Any, title: str) -> list[str]:
    if WHATSAPP_EXACT_ORDER_HISTORY_MARKER in str(value or ""):
        body_parts = _whatsapp_split_body(value, WHATSAPP_PART_BODY_CHARS, max_parts=None) or ["Sem detalhes adicionais."]
        total = len(body_parts)
        return [
            f"_Parte {index} de {total}_\n\n{body}".strip() if total > 1 else body
            for index, body in enumerate(body_parts, 1)
        ]
    if "relatorio" in _whatsapp_text_key(title):
        return _whatsapp_report_response_parts(value, title)
    body_parts = _whatsapp_split_body(value) or ["Sem detalhes adicionais."]
    total = len(body_parts)
    result: list[str] = []
    show_title = _whatsapp_operational_title(title)
    for index, body in enumerate(body_parts, 1):
        prefix = f"*{title}*\n\n" if show_title and index == 1 else ""
        counter = f"_Parte {index} de {total}_\n\n" if total > 1 else ""
        result.append(f"{prefix}{counter}{body}".strip())
    return result


def _whatsapp_result_title(prompt: Any, status: str = "completed") -> str:
    prompt_text = str(prompt or "").lower()
    if status == "completed" and re.search(r"\b(relat[oó]rio|an[aá]lise|diagn[oó]stico)\b", prompt_text):
        return "📊 BLACK JHON — RELATÓRIO"
    if status == "completed":
        return "✅ BLACK JHON — RESULTADO"
    if status == "awaiting_approval":
        return "🔐 BLACK JHON — CONFIRMAÇÃO"
    return "⚠️ BLACK JHON — NÃO CONCLUÍDO"


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
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (parsed.scheme != "https" and not (local and parsed.scheme == "http")):
        raise HTTPException(status_code=400, detail="Use uma URL HTTPS do Worker (HTTP e aceito apenas em localhost).")
    return text


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
    token = str(config.get("bridge_token") or "").strip()
    return {"authorization": f"Bearer {token}", "accept": "application/json"}


def _gateway_request(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    stream: bool = False,
) -> requests.Response:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    if not worker_url or not token:
        raise RuntimeError("worker_url_or_bridge_token_missing")
    response = requests.request(
        method.upper(),
        worker_url + path,
        headers=_gateway_headers(config),
        json=payload,
        timeout=timeout,
        stream=stream,
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"gateway_http_{response.status_code}: {detail}")
    return response


def _gateway_json(config: dict[str, Any], method: str, path: str, payload: Optional[dict[str, Any]] = None, timeout: int = 15) -> dict[str, Any]:
    response = _gateway_request(config, method, path, payload=payload, timeout=timeout)
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value


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
    name = os.path.basename(unquote(str(value or ""))).strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-")
    return (name or fallback)[:160]


def _media_extension(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "audio/aac": ".aac",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/amr": ".amr",
        "audio/ogg": ".ogg",
    }.get(mime, mimetypes.guess_extension(mime) or ".bin")


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
            "Use secoes apenas quando elas realmente ajudarem em relatorios ou respostas longas; emojis somente com moderacao."
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
    body = {"machine_id": config.get("machine_id"), **payload}
    result = _gateway_json(config, "POST", f"/bridge/messages/{message_id}/result", body, timeout=20)
    _stop_typing_pulse(message_id)
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
            "buttons": [
                {"id": f"ppv_approve:{token}", "title": "Aprovar"},
                {"id": f"ppv_reject:{token}", "title": "Negar"},
                {"id": f"ppv_regenerate:{token}", "title": "Gerar nova resposta"},
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
) -> tuple[str, dict[str, Any]]:
    approval_id = str(approval.get("id") or "").strip()
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    now = time.time()
    for token, item in list(tokens.items()):
        if not isinstance(item, dict) or now - float(item.get("created_at") or 0) > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
            tokens.pop(token, None)
            continue
        if str(item.get("approval_id") or "") == approval_id and str(item.get("subject_id") or "") == subject_id:
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
    }
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
                if notifications.get(notification_key):
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
                    notifications[notification_key] = {"sent_at": _now(), "approval_id": approval_id, "subject_id": subject_id}
                    _question_set_active_thread(
                        state,
                        approval_id=approval_id,
                        token=token,
                        subject_id=subject_id,
                        client_id=client_id,
                        username=username,
                    )
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


def _save_pending(state: dict[str, Any], message_id: str, value: dict[str, Any]) -> None:
    pending = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    pending[message_id] = value
    state["pending_messages"] = pending
    _save_state(state)


def _remove_pending(state: dict[str, Any], message_id: str) -> None:
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    pending_messages.pop(message_id, None)
    state["pending_messages"] = pending_messages
    _save_state(state)


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
        run = (codex_actions.get_run(run_id) or {}).get("run") or {}
    except HTTPException:
        return False
    status = str(run.get("status") or "")
    if status not in {"completed", "failed", "canceled"}:
        return False
    response = _action_result_text(run)
    title_status = "completed" if status == "completed" else "failed"
    parts = _whatsapp_response_parts(response, _whatsapp_result_title(pending.get("request_text"), title_status))
    _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"action:{run_id}:{status}",
            "event_type": "task_completed" if status == "completed" else "task_failed",
            "severity": "info" if status == "completed" else "high",
            "text": parts[0],
            "text_parts": parts,
        },
    )
    _remove_pending(state, message_id)
    return True


def _complete_pending(config: dict[str, Any], state: dict[str, Any], message_id: str, pending: dict[str, Any]) -> bool:
    if str(pending.get("kind") or "task") == "action_proposal":
        return _complete_action_pending(config, state, message_id, pending)
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
                "Abra a tarefa no Black Jhon e informe o módulo ou caminho permitido."
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
    if status not in {"completed", "failed", "canceled"}:
        return False
    response = str(task.get("final_response") or task.get("error") or "Black Jhon concluiu sem resposta final.")
    allow_full_history = WHATSAPP_EXACT_ORDER_HISTORY_MARKER in response
    title_status = "completed" if status == "completed" else "failed"
    if status == "completed":
        client_id = pending.get("client_id") or task.get("client_id") or config.get("client_id")
        chart_results = _whatsapp_deliver_report_artifacts(
            config,
            message_id,
            task.get("whatsapp_artifacts"),
            client_id,
            max_images=2,
        )
        charts_sent = sum(1 for item in chart_results if item.get("success"))
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
        if task.get("whatsapp_chart_expected") is True:
            codex_console._codex_update_task(
                task_id,
                whatsapp_artifacts=[],
                whatsapp_chart_status="sent" if charts_sent else "send_failed",
                whatsapp_chart_error="" if charts_sent else str(task.get("whatsapp_chart_error") or "chart_not_sent")[:500],
            )
        if image_results:
            RUNTIME_STATE["last_outbound_images"] = image_results[-WHATSAPP_MAX_OUTBOUND_IMAGES:]
    parts = _whatsapp_response_parts(response, _whatsapp_result_title(pending.get("request_text") or task.get("prompt"), title_status))
    if pending.get("awaiting_notified"):
        event_type = "task_completed" if status == "completed" else "task_failed"
        _post_proactive(
            config,
            {
                "subject_id": str(pending.get("subject_id") or ""),
                "fingerprint": f"task:{task_id}:{status}",
                "event_type": event_type,
                "severity": "high" if status != "completed" else "info",
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
                "status": "completed" if status == "completed" else "failed",
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
        "title": "WhatsApp — Black Jhon",
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
    matched_spec = None
    try:
        specs = codex_actions._discover_route_specs()
        matched_spec = codex_actions._select_action(action_reference, specs)
    except Exception:
        matched_spec = None
    protected_action_domains = _whatsapp_action_spec_query_only_domains(matched_spec)
    if not protected_action_domains:
        protected_action_domains = _whatsapp_protected_mutation_domains(action_reference)
    if protected_action_domains:
        followups.pop(conversation_id, None)
        state["action_followups"] = followups
        _save_state(state)
        _post_command_reply(
            config,
            str(message.get("message_id") or ""),
            _whatsapp_query_only_block_text(protected_action_domains),
            "BLACK JHON — SOMENTE CONSULTA",
        )
        return True
    if not followup and not codex_console._codex_prompt_pede_alteracao(request_text):
        return False
    history = [{"role": "user", "text": str(followup.get("message") or "")}] if followup else []
    result = codex_actions.create_proposal(
        client_id=str(session.get("client_id") or "default"),
        username=str(session.get("username") or ""),
        message=request_text,
        screen_context=screen_context,
        history=history,
        conversation_id=conversation_id,
        conversation_generation=1,
    )
    if not isinstance(result, dict) or result.get("matched") is not True:
        if followup:
            followups.pop(conversation_id, None)
            state["action_followups"] = followups
            _save_state(state)
        return False
    result_action = result.get("action") if isinstance(result.get("action"), dict) else {}
    result_proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    result_proposal_action = result_proposal.get("action") if isinstance(result_proposal.get("action"), dict) else {}
    protected_action_domains = list(
        dict.fromkeys(
            _whatsapp_action_spec_query_only_domains(result_action)
            + _whatsapp_action_spec_query_only_domains(result_proposal_action)
            + _whatsapp_action_spec_query_only_domains(result_proposal)
        )
    )
    if protected_action_domains:
        followups.pop(conversation_id, None)
        state["action_followups"] = followups
        _save_state(state)
        _post_command_reply(
            config,
            str(message.get("message_id") or ""),
            _whatsapp_query_only_block_text(protected_action_domains),
            "BLACK JHON — SOMENTE CONSULTA",
        )
        return True
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
        parts = _whatsapp_response_parts(response, "🧩 BLACK JHON — DADOS NECESSÁRIOS")
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
            "mas ainda está marcada como somente proposta no Black Jhon e não possui executor seguro. "
            "Ela não será executada pelo WhatsApp."
        )
        parts = _whatsapp_response_parts(response, "🛡️ BLACK JHON — EXECUÇÃO INDISPONÍVEL")
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
        "awaiting_notified": True,
        "trusted_bound_number": True,
    }
    approved = codex_actions.approve_proposal(
        str(proposal.get("proposal_id") or ""),
        username=str(session.get("username") or ""),
        client_id=str(session.get("client_id") or ""),
        authorization=None,
    )
    run = approved.get("run") if isinstance(approved, dict) and isinstance(approved.get("run"), dict) else {}
    pending["run_id"] = str(run.get("run_id") or "")
    _save_pending(state, message_id, pending)
    parts = _whatsapp_response_parts(
        "Certo, vou cuidar disso agora e te aviso aqui quando terminar.",
        "BLACK JHON - EM ANDAMENTO",
    )
    _post_message_result(
        config,
        message_id,
        {"status": "completed", "response": parts[0], "response_parts": parts},
    )
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
            ),
            client_id,
        )
    response = str((generated or {}).get("resposta") or (generated or {}).get("answer") or "").strip()[:1200]
    if not response:
        raise RuntimeError("generated_answer_empty")
    approval["resposta_sugerida"] = response
    approval["regenerated_at"] = _now()
    approval["regenerated_via"] = "whatsapp"
    if str(guidance or "").strip():
        approval["whatsapp_user_guidance"] = str(guidance or "").strip()[:1200]
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    return response


def _question_natural_action(value: Any) -> str:
    text = _whatsapp_text_key(value)
    if not text:
        return ""
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
        return "approve"
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
    if not action or not _question_approval_allowed(session.get("permissions") or {}):
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

        if action == "suggest":
            guidance = _question_suggestion_guidance(message.get("text_body"))
            regenerated = _regenerate_question_approval_response(
                approval,
                approvals,
                client_id,
                guidance=guidance,
            )
            token_item["suggested_response"] = regenerated
            token_item["user_guidance"] = guidance
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

        response_override = str(token_item.get("suggested_response") or "").strip() or None
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
                and str(item.get("status") or "pending") == "pending"
            ),
            None,
        )
        if not approval:
            token_item["used"] = True
            _save_state(state)
            _post_command_reply(config, message_id, "A pergunta ja foi resolvida por outro fluxo e nenhuma acao foi repetida.", "BLACK JHON - JA RESOLVIDA")
            return True
        if action in {"regenerate", "suggest"}:
            regenerated = _regenerate_question_approval_response(approval, approvals, client_id)
            token_item["suggested_response"] = regenerated
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
        request_payload = PerguntasAprovacaoRequest(
            approval_id=approval_id,
            resposta=str(token_item.get("suggested_response") or "").strip() or None,
        )
        if action == "approve":
            result = ppv_endpoints.ml_perguntas_aprovacoes_aprovar(request_payload, client_id)
            resolved = result.get("approval") if isinstance(result, dict) and isinstance(result.get("approval"), dict) else {}
            if str(resolved.get("status") or "") not in {"sent", "sent_approved", "sent_manual", "sent_manual_pos_venda"}:
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
    if not session.get("is_full"):
        _post_command_reply(
            config,
            message_id,
            "Este vínculo não possui mais permissão `full` no JK Sistema. Nenhuma execução foi liberada.",
            "🛡️ BLACK JHON — ACESSO NEGADO",
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
            "⚠️ BLACK JHON — CÓDIGO INVÁLIDO",
        )
        return True
    protected_domains = _whatsapp_protected_mutation_domains(
        "\n".join(
            str(item or "")
            for item in (
                pending.get("request_text"),
                pending.get("action_summary"),
                pending.get("action_id"),
            )
        )
    )
    if protected_domains:
        _remove_pending(state, original_message_id)
        _post_command_reply(
            config,
            message_id,
            _whatsapp_query_only_block_text(protected_domains),
            "BLACK JHON — SOMENTE CONSULTA",
        )
        return True
    if float(pending.get("approval_expires_at") or 0) < time.time():
        renewed_text = _approval_notice(pending, renewed=True)
        _save_pending(state, original_message_id, pending)
        _post_command_reply(config, message_id, renewed_text, "🔐 BLACK JHON — NOVO CÓDIGO")
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
                "Confirmação aceita. O Black Jhon iniciou somente o pedido associado a este código e enviará o resultado nesta conversa.",
                "✅ BLACK JHON — EXECUÇÃO AUTORIZADA",
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
            _remove_pending(state, original_message_id)
            _post_command_reply(
                config,
                message_id,
                "Pedido rejeitado. Nenhuma execução foi iniciada para este código.",
                "🚫 BLACK JHON — PEDIDO REJEITADO",
            )
    except HTTPException as exc:
        detail = str(exc.detail or "Não foi possível aplicar esta decisão.")
        _post_command_reply(config, message_id, detail, "⚠️ BLACK JHON — DECISÃO NÃO APLICADA")
    except Exception:
        _post_command_reply(
            config,
            message_id,
            "Não foi possível aplicar a decisão agora. O pedido continua bloqueado e nada foi executado.",
            "⚠️ BLACK JHON — DECISÃO NÃO APLICADA",
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
            page="WhatsApp - Black Jhon",
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
        chart_outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
            base_info_dir=codex_console._codex_base_info_dir(),
            client_id=task.get("client_id") or "default",
            task_id=task_id,
            prompt=task.get("prompt") or "",
            tool_results=list(payload.tool_results or []),
            query_policy=query_policy,
            max_images=2,
        )
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
            whatsapp_artifacts=list(chart_outcome.get("artifacts") or [])[:2],
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
    channel_metadata = {
        **dict(channel_metadata or {}),
        "ai_model": settings["model"],
        "ai_provider": settings["provider"],
        "codex_reasoning_effort": settings["codex_reasoning_effort"],
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
        reasoning_effort=settings["codex_reasoning_effort"],
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
        config["subject_id"] = subject
        config["personal_phone"] = str(message.get("wa_id") or "")
        _save_config(config)
    session = _reload_bound_session(config, message)
    phone_settings = _phone_notification_settings(
        config,
        subject,
        client_id=session.get("client_id"),
        username=session.get("username"),
    )
    phone_ai_behavior = _normalize_phone_ai_behavior(phone_settings.get("ai_behavior"))
    if _handle_question_approval_command(config, state, message, session):
        return
    if _handle_question_natural_language(config, state, message, session):
        return
    if _handle_approval_command(config, state, message, session):
        return
    phone = _message_phone(config, message)
    if not phone:
        raise RuntimeError("whatsapp_phone_identity_missing")
    conversation_id = _conversation_id(config, message)
    media: Optional[dict[str, Any]] = None
    transcription: Optional[dict[str, Any]] = None
    if message.get("media_id") or message.get("media_object_key"):
        media = _download_media(config, message, conversation_id)
        if str(media.get("mime_type") or "") in SUPPORTED_AUDIO_MIMES:
            path = (_base_dir() / str(media.get("path") or "")).resolve()
            transcription = _transcribe_audio(path)
    mobile_full_access = bool(session.get("is_full") and (session.get("permissions") or {}).get("full") is True)
    request_text = _message_request_text(message, transcription)
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
                "BLACK JHON - ESCOLHA EXPIRADA",
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
    general_answer = _whatsapp_general_answer_request(request_text, session)
    protected_mutation_domains = [] if general_answer else _whatsapp_protected_mutation_domains(request_text)
    if protected_mutation_domains:
        _post_command_reply(
            config,
            message_id,
            _whatsapp_query_only_block_text(protected_mutation_domains),
            "BLACK JHON — SOMENTE CONSULTA",
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
            "BLACK JHON — REPITA A CONSULTA",
        )
        return
    if query_policy.get("no_more_results") is True:
        _post_command_reply(
            config,
            message_id,
            "A consulta anterior ja chegou ao fim dos resultados retornados pelas APIs. Para iniciar outra busca, envie novamente os filtros e a loja.",
            "BLACK JHON — FIM DOS RESULTADOS",
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
        _post_command_reply(config, message_id, _whatsapp_store_required_text(fallback_policy), "BLACK JHON — INFORME A LOJA")
        return
    screen_context = _mobile_screen_context(message_id, subject, media, transcription, query_policy)
    if mobile_full_access and not general_answer and not query_policy and not _whatsapp_readonly_inquiry(request_text) and _try_create_action_pending(
        config,
        state,
        message,
        session,
        conversation_id,
        request_text,
        screen_context,
    ):
        return
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
        },
    )
    task = result.get("task") if isinstance(result, dict) else {}
    trusted_auto_approved = False
    if mobile_full_access and str((task or {}).get("status") or "") == "awaiting_approval":
        approved_result = codex_console.codex_aprovar_tarefa_para_sessao(
            str((task or {}).get("task_id") or ""),
            session,
            codex_console.CodexTaskApprovalRequest(screen_context=screen_context),
            approval_source="whatsapp",
            subject_id=subject,
        )
        task = approved_result.get("task") if isinstance(approved_result, dict) else task
        trusted_auto_approved = True
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
        "awaiting_notified": trusted_auto_approved,
        "trusted_bound_number": trusted_auto_approved,
    }
    _save_pending(state, message_id, pending)
    if trusted_auto_approved:
        parts = _whatsapp_response_parts(
            "Certo, vou fazer isso agora e te aviso aqui quando concluir.",
            "BLACK JHON - EM ANDAMENTO",
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
    _complete_pending(config, state, message_id, pending)


def _monitor_pending(config: dict[str, Any], state: dict[str, Any]) -> None:
    pending = dict(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else {}
    for message_id, item in list(pending.items())[:100]:
        if isinstance(item, dict):
            try:
                _complete_pending(config, state, str(message_id), item)
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
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
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
    if not config.get("enabled"):
        config, activation_status = _activate_completed_pairing(config)
        if not config.get("enabled"):
            return {"success": True, "status": activation_status, "claimed": 0}
    state = _load_state()
    if time.time() - float(state.get("report_chart_cleanup_at") or 0) >= 3600:
        state["report_chart_cleanup_removed"] = whatsapp_report_visuals.cleanup_stale_chart_files(_info_dir())
        state["report_chart_cleanup_at"] = time.time()
        _save_state(state)
    _monitor_pending(config, state)
    response = _gateway_json(
        config,
        "POST",
        "/bridge/claim",
        {"machine_id": config.get("machine_id"), "limit": CLAIM_LIMIT},
        timeout=20,
    )
    messages = response.get("messages") if isinstance(response.get("messages"), list) else []
    if messages:
        _flush_pending_weekly_visuals(config, state)
    processed = 0
    for raw in messages[:CLAIM_LIMIT]:
        if not isinstance(raw, dict):
            continue
        message_id = str(raw.get("message_id") or "")
        try:
            _process_message(config, state, raw)
            processed += 1
        except Exception as exc:
            detail = str(exc)[:1000]
            RUNTIME_STATE["last_error"] = detail
            try:
                _post_message_result(config, message_id, {"status": "failed", "error": detail})
            except Exception:
                pass
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
    BRIDGE_THREAD = threading.Thread(target=_bridge_loop, name="jk-whatsapp-bridge", daemon=True)
    BRIDGE_THREAD.start()


def _public_status(config: dict[str, Any], worker: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    whisper = _whisper_status()
    codex = codex_console._codex_status_payload()
    ai_settings = _whatsapp_ai_settings(config)
    worker = worker if isinstance(worker, dict) else _worker_health(config) if config.get("worker_url") and config.get("bridge_token") else {"success": False, "worker": False, "error": "nao_configurado"}
    state = _load_state()
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
    return {
        "success": True,
        "enabled": bool(config.get("enabled")),
        "configured": bool(config.get("worker_url") and config.get("bridge_token")),
        "worker_url": str(config.get("worker_url") or ""),
        "bridge_token_configured": bool(config.get("bridge_token")),
        "business_phone": str(config.get("business_phone") or ""),
        "ai_model": ai_settings["model"],
        "ai_provider": ai_settings["provider"],
        "codex_reasoning_effort": ai_settings["codex_reasoning_effort"],
        "codex_reasoning_options": list(WHATSAPP_CODEX_REASONING_OPTIONS),
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
        "whisper": whisper,
        "joao": {"ready": bool(codex.get("ready")), "enabled": bool(codex.get("enabled")), "message": codex.get("message")},
        "dependencies": {
            "codex": str(codex.get("runtime_status") or "dependency_missing"),
            "whisper": "ready" if whisper.get("ready") else "dependency_missing",
            "gateway": "ready" if worker.get("protocol_compatible") else "incompatible",
        },
        "runtime": {
            "running": bool(RUNTIME_STATE.get("running")),
            "last_error": str(RUNTIME_STATE.get("last_error") or ""),
            "last_processing_at": str(RUNTIME_STATE.get("last_processing_at") or ""),
            "last_worker_ok_at": str(RUNTIME_STATE.get("last_worker_ok_at") or ""),
            "typing_active": len(TYPING_PULSES),
            "typing_refresh_seconds": TYPING_REFRESH_SECONDS,
            "typing_max_seconds": TYPING_MAX_SECONDS,
            "typing_last_sent_at": str(RUNTIME_STATE.get("typing_last_sent_at") or ""),
            "typing_last_error": str(RUNTIME_STATE.get("typing_last_error") or ""),
            "bridge_heartbeat_at": str(state.get("bridge_heartbeat_at") or ""),
            "bridge_last_error": str(state.get("bridge_last_error") or ""),
            "pending_local_tasks": len(state.get("pending_messages") or {}) if isinstance(state.get("pending_messages"), dict) else 0,
            "pending_weekly_visuals": len(state.get("pending_weekly_visuals") or {}) if isinstance(state.get("pending_weekly_visuals"), dict) else 0,
            "last_weekly_visual_status": str(state.get("last_weekly_visual_status") or ""),
            "last_weekly_visual_sent_at": str(state.get("last_weekly_visual_sent_at") or ""),
            "poll_seconds": POLL_SECONDS,
            "claim_limit": CLAIM_LIMIT,
        },
        "zero_cost": {
            "policy_valid_until": ZERO_COST_POLICY_VALID_UNTIL,
            "fail_closed": True,
            "free_window_minutes": 1410,
            "templates_outside_window": False,
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
        }
    )
    updated.update({"subject_id": subject_id, "client_id": client_id, "username": username, "updated_at": _now()})
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
]
