"""Extracted WhatsApp bridge component: artifacts."""

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


def _mask_phone(value: Any) -> str:
    return whatsapp_message.mask_phone(value)

def _normalize_registered_phone(value: Any) -> str:
    return whatsapp_message.normalize_registered_phone(value)

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


_COMPONENT_FUNCTIONS = frozenset((
    '_mask_phone',
    '_normalize_registered_phone',
    '_whatsapp_table_blocks_to_mobile',
    '_whatsapp_clean_markdown',
    '_whatsapp_image_requested',
    '_whatsapp_image_references',
    '_whatsapp_path_within',
    '_whatsapp_image_roots',
    '_whatsapp_resolve_image_reference',
    '_whatsapp_sku_candidates',
    '_whatsapp_normalized_sku',
    '_whatsapp_image_matches_skus',
    '_whatsapp_find_image_by_sku',
    '_whatsapp_image_mime',
    '_whatsapp_prepare_outbound_image',
    '_whatsapp_strip_image_references',
    '_whatsapp_outbound_image_caption',
    '_whatsapp_deliver_requested_images',
    '_whatsapp_report_chart_path',
    '_whatsapp_report_document_path',
    '_whatsapp_deliver_report_artifacts'
))
_IMPLEMENTATIONS = {
    '_mask_phone': _mask_phone,
    '_normalize_registered_phone': _normalize_registered_phone,
    '_whatsapp_table_blocks_to_mobile': _whatsapp_table_blocks_to_mobile,
    '_whatsapp_clean_markdown': _whatsapp_clean_markdown,
    '_whatsapp_image_requested': _whatsapp_image_requested,
    '_whatsapp_image_references': _whatsapp_image_references,
    '_whatsapp_path_within': _whatsapp_path_within,
    '_whatsapp_image_roots': _whatsapp_image_roots,
    '_whatsapp_resolve_image_reference': _whatsapp_resolve_image_reference,
    '_whatsapp_sku_candidates': _whatsapp_sku_candidates,
    '_whatsapp_normalized_sku': _whatsapp_normalized_sku,
    '_whatsapp_image_matches_skus': _whatsapp_image_matches_skus,
    '_whatsapp_find_image_by_sku': _whatsapp_find_image_by_sku,
    '_whatsapp_image_mime': _whatsapp_image_mime,
    '_whatsapp_prepare_outbound_image': _whatsapp_prepare_outbound_image,
    '_whatsapp_strip_image_references': _whatsapp_strip_image_references,
    '_whatsapp_outbound_image_caption': _whatsapp_outbound_image_caption,
    '_whatsapp_deliver_requested_images': _whatsapp_deliver_requested_images,
    '_whatsapp_report_chart_path': _whatsapp_report_chart_path,
    '_whatsapp_report_document_path': _whatsapp_report_document_path,
    '_whatsapp_deliver_report_artifacts': _whatsapp_deliver_report_artifacts
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
