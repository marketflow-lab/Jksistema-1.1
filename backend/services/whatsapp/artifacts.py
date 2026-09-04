"""Extracted WhatsApp bridge component: artifacts."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import ipaddress
import importlib.util
import itertools
import json
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
from urllib.parse import quote, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import marketplace_listing_delivery as whatsapp_marketplace_listing
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
from backend.services.codex.console import attachments_api as console_attachments
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
    safe_client = console_attachments.safe_id(str(client_id or ""), "default")
    # Outbound WhatsApp images are deliberately restricted to the product-photo
    # directory of the authenticated tenant. A model-produced path must never
    # become a generic local-file exfiltration primitive.
    return [(_info_dir() / safe_client / "cadastro_fotos").resolve()]


def _whatsapp_store_photo_segment_active(
    path: Path,
    tenant_photos: Path,
    client_id: Any,
) -> bool:
    """Reject orphan store folders even when they are physically in the tenant."""

    try:
        relative = path.relative_to(tenant_photos)
    except ValueError:
        return False
    parts = relative.parts
    if not parts or str(parts[0]).casefold() != "lojas":
        return True
    if len(parts) != 3:
        return False
    from backend.services.cadastro_fotos import _cadastro_store_id_por_segmento_foto

    return bool(
        _cadastro_store_id_por_segmento_foto(
            str(client_id or ""),
            str(parts[1] or ""),
        )
    )

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
        relativo = raw_path[len("/api/cadastro/foto-arquivo/"):].lstrip("/")
        if relativo.lower().startswith("cadastro_fotos/"):
            relativo = relativo.split("/", 1)[1]
        partes = relativo.split("/")
        if partes and partes[0].lower() == "lojas":
            if len(partes) == 3:
                candidates.append(tenant_photos.joinpath(*partes))
        elif len(partes) == 1 and partes[0]:
            candidates.append(tenant_photos / partes[0])
    elif raw_path.lower().startswith("/api/cadastro/foto/"):
        relativo = raw_path[len("/api/cadastro/foto/"):].lstrip("/")
        partes = relativo.split("/")
        safe_client = console_attachments.safe_id(str(client_id or ""), "default")
        client_referencia = (
            console_attachments.safe_id(partes[0], "default") if partes else ""
        )
        if client_referencia != safe_client:
            return None
        caminho_tenant = partes[1:]
        if (
            len(caminho_tenant) == 3
            and caminho_tenant[0].lower() == "lojas"
        ):
            candidates.append(tenant_photos.joinpath(*caminho_tenant))
        elif len(caminho_tenant) == 1 and caminho_tenant[0]:
            # Contrato legado: /foto/<client_id>/<basename>.
            candidates.append(tenant_photos / caminho_tenant[0])
    elif raw_path.lower().startswith("cadastro_fotos/"):
        relativo = raw_path.split("/", 1)[1]
        partes = relativo.split("/")
        if partes and partes[0].lower() == "lojas":
            if len(partes) == 3:
                candidates.append(tenant_photos.joinpath(*partes))
        elif len(partes) == 1 and partes[0]:
            candidates.append(tenant_photos / partes[0])
    else:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            candidates.append(candidate)
        elif len(candidate.parts) == 3 and candidate.parts[0].lower() == "lojas":
            candidates.append(tenant_photos.joinpath(*candidate.parts))
        elif len(candidate.parts) == 1 and candidate.name:
            candidates.append(tenant_photos / candidate.name)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if (
                resolved.is_file()
                and _whatsapp_path_within(resolved, roots)
                and _whatsapp_store_photo_segment_active(
                    resolved,
                    tenant_photos,
                    client_id,
                )
            ):
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


def _whatsapp_marketplace_image_url_allowed(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
        port = parsed.port
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower().rstrip(".")
    return bool(
        parsed.scheme.lower() == "https"
        and host
        and (host == "mlstatic.com" or host.endswith(".mlstatic.com"))
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
    )


def _whatsapp_marketplace_image_host_is_public(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = str(parsed.hostname or "").strip()
    if not host:
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not addresses:
        return False
    for entry in addresses:
        try:
            address = ipaddress.ip_address(str(entry[4][0]).split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            return False
        if not address.is_global:
            return False
    return True


def _whatsapp_download_marketplace_image(value: Any) -> Optional[Path]:
    current_url = str(value or "").strip()
    temporary: Optional[Path] = None
    response = None
    try:
        for _redirect in range(4):
            if not _whatsapp_marketplace_image_url_allowed(current_url):
                raise RuntimeError("marketplace_image_host_not_allowed")
            if not _whatsapp_marketplace_image_host_is_public(current_url):
                raise RuntimeError("marketplace_image_host_not_public")
            response = requests.get(
                current_url,
                stream=True,
                allow_redirects=False,
                timeout=(5, 20),
                headers={"Accept": "image/jpeg,image/png,image/webp", "User-Agent": "JK-Sistema-WhatsApp/1.0"},
            )
            if int(response.status_code or 0) in {301, 302, 303, 307, 308}:
                location = str(response.headers.get("Location") or "").strip()
                response.close()
                response = None
                if not location:
                    raise RuntimeError("marketplace_image_redirect_missing")
                current_url = urljoin(current_url, location)
                continue
            if int(response.status_code or 0) != 200:
                raise RuntimeError(f"marketplace_image_http_{int(response.status_code or 0)}")
            content_length = int(str(response.headers.get("Content-Length") or "0") or "0")
            if content_length > WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
                raise RuntimeError("marketplace_image_too_large")
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            suffix = ".png" if content_type == "image/png" else ".webp" if content_type == "image/webp" else ".jpg"
            handle = tempfile.NamedTemporaryFile(prefix="jk-wa-ml-", suffix=suffix, delete=False)
            temporary = Path(handle.name)
            downloaded = 0
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES:
                        raise RuntimeError("marketplace_image_too_large")
                    handle.write(chunk)
            finally:
                handle.close()
            if downloaded <= 0 or _whatsapp_image_mime(temporary) not in {"image/jpeg", "image/png", "image/webp"}:
                raise RuntimeError("marketplace_image_invalid")
            return temporary
        raise RuntimeError("marketplace_image_redirect_limit")
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        return None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _whatsapp_deliver_marketplace_listing_images(
    config: dict[str, Any],
    message_id: str,
    listing_bundle: Any,
    request_text: Any,
    max_images: int = WHATSAPP_MAX_OUTBOUND_IMAGES,
) -> list[dict[str, Any]]:
    if not whatsapp_marketplace_listing.pictures_requested(request_text):
        return []
    candidates = whatsapp_marketplace_listing.image_candidates(listing_bundle, max_images=max_images)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        source_url = str(candidate.get("url") or "").strip()
        path = _whatsapp_download_marketplace_image(source_url)
        item_id = str(candidate.get("item_id") or "MLB").strip()
        sequence = int(candidate.get("sequence") or len(results) + 1)
        sequence_total = int(candidate.get("sequence_total") or len(candidates))
        if path is None:
            results.append({
                "success": False,
                "error": "marketplace_image_download_failed",
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
            })
            continue
        prepared = None
        try:
            prepared = _whatsapp_prepare_outbound_image(path)
            if not prepared:
                raise RuntimeError("marketplace_image_prepare_failed")
            store = str(candidate.get("store") or "Loja").strip()
            caption = f"{store} • {item_id} • Foto {sequence}/{sequence_total}"[:1024]
            result = _post_outbound_image(
                config,
                message_id,
                Path(prepared["path"]),
                str(prepared["mime_type"]),
                caption,
                str(prepared.get("filename") or f"{item_id}-{sequence}.jpg"),
                artifact_type="product_photo",
            )
            results.append({
                "success": bool(result.get("success")),
                "status": result.get("status"),
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
                "error": result.get("error"),
            })
        except Exception as exc:
            results.append({
                "success": False,
                "error": str(exc)[:500],
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
            })
        finally:
            if prepared and prepared.get("cleanup"):
                try:
                    Path(prepared["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return results


def _whatsapp_deliver_marketplace_listing_images_proactive(
    config: dict[str, Any],
    *,
    subject_id: str,
    listing_bundle: Any,
    request_text: Any,
    fingerprint_seed: str,
    max_images: int = WHATSAPP_MAX_OUTBOUND_IMAGES,
) -> list[dict[str, Any]]:
    """Deliver official listing photos without requiring an inbound message."""

    subject_id = str(subject_id or "").strip()
    seed = str(fingerprint_seed or "").strip()
    if not subject_id or not seed or not whatsapp_marketplace_listing.pictures_requested(request_text):
        return []
    candidates = whatsapp_marketplace_listing.image_candidates(listing_bundle, max_images=max_images)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        source_url = str(candidate.get("url") or "").strip()
        path = _whatsapp_download_marketplace_image(source_url)
        item_id = str(candidate.get("item_id") or "MLB").strip()
        sequence = int(candidate.get("sequence") or len(results) + 1)
        sequence_total = int(candidate.get("sequence_total") or len(candidates))
        fingerprint = "whatsapp-product:" + hashlib.sha256(
            f"{seed}\n{subject_id}\n{item_id}\n{sequence}\n{source_url}".encode("utf-8")
        ).hexdigest()[:64]
        if path is None:
            results.append({
                "success": False,
                "error": "marketplace_image_download_failed",
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
            })
            continue
        prepared = None
        try:
            prepared = _whatsapp_prepare_outbound_image(path)
            if not prepared:
                raise RuntimeError("marketplace_image_prepare_failed")
            store = str(candidate.get("store") or "Loja").strip()
            caption = f"{store} - {item_id} - Foto {sequence}/{sequence_total}"[:1024]
            result = _post_proactive_image(
                config,
                subject_id=subject_id,
                fingerprint=fingerprint,
                path=Path(prepared["path"]),
                caption=caption,
                filename=str(prepared.get("filename") or f"{item_id}-{sequence}.jpg"),
                event_type="task_completed",
                artifact_type="product_photo",
                mime_type=str(prepared.get("mime_type") or "image/jpeg"),
            )
            results.append({
                "success": bool(result.get("success")),
                "status": result.get("status"),
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
                "error": result.get("error"),
            })
        except Exception as exc:
            results.append({
                "success": False,
                "error": str(exc)[:500],
                "artifact_type": "product_photo",
                "item_id": item_id,
                "sequence": sequence,
            })
        finally:
            if prepared and prepared.get("cleanup"):
                try:
                    Path(prepared["path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                path.unlink(missing_ok=True)
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
    '_whatsapp_marketplace_image_url_allowed',
    '_whatsapp_download_marketplace_image',
    '_whatsapp_deliver_marketplace_listing_images',
    '_whatsapp_deliver_marketplace_listing_images_proactive'
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
    '_whatsapp_marketplace_image_url_allowed': _whatsapp_marketplace_image_url_allowed,
    '_whatsapp_download_marketplace_image': _whatsapp_download_marketplace_image,
    '_whatsapp_deliver_marketplace_listing_images': _whatsapp_deliver_marketplace_listing_images,
    '_whatsapp_deliver_marketplace_listing_images_proactive': _whatsapp_deliver_marketplace_listing_images_proactive
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
