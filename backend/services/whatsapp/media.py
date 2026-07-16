"""Pure media parsing and validation helpers for the WhatsApp bridge."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from backend.services.whatsapp import formatting


SUPPORTED_IMAGE_MIMES = {"image/jpeg": 5 * 1024 * 1024, "image/png": 5 * 1024 * 1024}
SUPPORTED_AUDIO_MIMES = {
    "audio/aac": 16 * 1024 * 1024,
    "audio/mp4": 16 * 1024 * 1024,
    "audio/mpeg": 16 * 1024 * 1024,
    "audio/amr": 16 * 1024 * 1024,
    "audio/ogg": 16 * 1024 * 1024,
}
WHATSAPP_MAX_OUTBOUND_IMAGES = 3
WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES = 5 * 1024 * 1024
WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
WHATSAPP_OUTBOUND_IMAGE_MAX_PIXELS = 40_000_000
WHATSAPP_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(?:\s+['\"][^)]*['\"])?\s*\)",
    re.IGNORECASE,
)


def image_requested(value: Any) -> bool:
    text = formatting._whatsapp_text_key(value)
    if not re.search(r"\b(foto|fotos|imagem|imagens)\b", text):
        return False
    explicit_send = re.search(
        r"\b(manda|mandar|envia|enviar|mostra|mostrar|quero|preciso|consigo|consegue|pode)\b",
        text,
    )
    product_context = re.search(r"\b(sku|produto|cadastro|anuncio)\b", text)
    return bool(explicit_send or product_context)


def image_references(value: Any, *, max_images: int = WHATSAPP_MAX_OUTBOUND_IMAGES) -> list[tuple[str, str]]:
    text = str(value or "")
    references: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in WHATSAPP_IMAGE_MARKDOWN_RE.finditer(text):
        alt = re.sub(r"\s+", " ", str(match.group(1) or "")).strip()
        ref = str(match.group(2) or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            references.append((alt, ref))
    for match in re.finditer(
        r"(?:/api/cadastro/foto-(?:arquivo/)?|cadastro_fotos/)[^\s)>'\"]+",
        text,
        flags=re.IGNORECASE,
    ):
        ref = str(match.group(0) or "").rstrip(".,;:")
        if ref and ref not in seen:
            seen.add(ref)
            references.append(("", ref))
    return references[:max_images]


def path_within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def sku_candidates(value: Any) -> list[str]:
    text = str(value or "")
    result: list[str] = []
    for match in re.finditer(r"\bSKU\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{0,40})", text, flags=re.IGNORECASE):
        sku = str(match.group(1) or "").strip().rstrip(".,;:")
        if sku and sku.upper() not in {item.upper() for item in result}:
            result.append(sku)
    return result[:10]


def normalized_sku(value: Any, *, strip_numeric_zeroes: bool = False) -> str:
    text = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
    if strip_numeric_zeroes:
        text = re.sub(r"\d+", lambda match: str(int(match.group(0))), text)
    return text


def image_matches_skus(path: Path, skus: list[str]) -> bool:
    stem = normalized_sku(path.stem)
    stem_compact = normalized_sku(path.stem, strip_numeric_zeroes=True)
    for sku in skus:
        exact = normalized_sku(sku)
        compact = normalized_sku(sku, strip_numeric_zeroes=True)
        if stem == exact or (compact and stem_compact == compact):
            return True
    return False


def image_mime(path: Path) -> str:
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


def strip_image_references(value: Any) -> str:
    text = WHATSAPP_IMAGE_MARKDOWN_RE.sub("", str(value or ""))
    text = re.sub(
        r"(?:/api/cadastro/foto-(?:arquivo/)?|cadastro_fotos/)[^\s)>'\"]+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def outbound_image_caption(response: Any, alt: Any = "") -> str:
    clean = strip_image_references(response)
    for line in clean.splitlines():
        candidate = re.sub(r"^[#*•\s]+|[*_`]+$", "", line).strip()
        if candidate and not formatting._whatsapp_report_section_kind(candidate):
            return candidate[:1024]
    return (str(alt or "Imagem solicitada ao Black John").strip() or "Imagem solicitada ao Black John")[:1024]


def safe_filename(value: Any, fallback: str) -> str:
    name = os.path.basename(unquote(str(value or ""))).strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-")
    return (name or fallback)[:160]


def media_extension(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "audio/aac": ".aac",
        "audio/mp4": ".m4a",
        "audio/mpeg": ".mp3",
        "audio/amr": ".amr",
        "audio/ogg": ".ogg",
    }.get(mime, mimetypes.guess_extension(mime) or ".bin")
