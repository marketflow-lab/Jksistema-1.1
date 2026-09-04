"""Bounded, ephemeral PDF-page inputs for Black Jhon technical vision.

This module deliberately returns in-memory ``IAChatAttachment`` values only.
It never writes a research document, page image, URL body or temporary path to
the Context Hub.  The Codex provider transport owns the short-lived local file
needed by ``LocalImageInput`` and removes it after the turn.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from backend.schemas.ia import IAChatAttachment
from backend.services.transport_security import requests_tls_verify

from .deep_research_contracts import (
    bounded_research_request_timeouts,
    canonical_research_url,
    closing_research_response,
)
from .provider_transport import fetch_research_response
from .research_url_security import _perguntas_ia_v2_url_fonte_tecnica_segura


PRODUCT_DOCUMENT_VISION_POLICY = "jk_product_document_vision_v1"
MAX_DOCUMENT_VISION_IMAGES = 8
MAX_DOCUMENT_VISION_PDF_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_VISION_PAGES = 250
MAX_DOCUMENT_VISION_IMAGE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_VISION_PAGE_POINTS = 14_400
MAX_DOCUMENT_VISION_IMAGE_DIMENSION = 6_000
MAX_DOCUMENT_VISION_IMAGE_PIXELS = 12_000_000
_DEFAULT_DOCUMENT_VISION_SECONDS = 45.0


@dataclass(frozen=True, slots=True)
class DocumentVisionBatchV1:
    """Transient page images plus non-sensitive aggregate diagnostics."""

    attachments: tuple[IAChatAttachment, ...]
    page_refs: tuple[dict[str, object], ...]
    documents_attempted: int
    documents_processed: int
    documents_unprocessed: int
    stop_reason: str
    policy: str = PRODUCT_DOCUMENT_VISION_POLICY

    def diagnostics(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "documents_attempted": self.documents_attempted,
            "documents_processed": self.documents_processed,
            "documents_unprocessed": self.documents_unprocessed,
            "images_created": len(self.attachments),
            "stop_reason": self.stop_reason,
        }


def _plain(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _safe_focus_terms(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _plain(value)
        for token in re.findall(r"[a-z0-9][a-z0-9./_-]{2,}", normalized):
            if token in {
                "para", "com", "sem", "uma", "esse", "essa", "este", "esta",
                "produto", "peca", "serve", "servir", "manual", "catalogo",
            }:
                continue
            if token not in seen:
                seen.add(token)
                result.append(token[:80])
            if len(result) >= 40:
                return tuple(result)
    return tuple(result)


def technical_focus_terms(
    question: object,
    *,
    listing_title: object = "",
    identifiers: Sequence[object] = (),
) -> tuple[str, ...]:
    """Return bounded technical terms; VIN-shaped values are never retained."""

    from backend.services.vin_transient import sanitize_vin_like_text

    values = [
        sanitize_vin_like_text(str(question or "")),
        sanitize_vin_like_text(str(listing_title or "")),
        *[sanitize_vin_like_text(str(value or "")) for value in identifiers[:24]],
    ]
    terms = _safe_focus_terms(values)
    # Defense in depth: a 17-character VIN-like token is not a useful page
    # selector and must never cross into a model-visible page reference.
    return tuple(
        value for value in terms
        if not re.fullmatch(r"[a-hj-npr-z0-9]{17}", value, flags=re.IGNORECASE)
    )


def _iter_research_source_candidates(value: object, *, depth: int = 0):
    if depth > 6:
        return
    if isinstance(value, Mapping):
        if any(value.get(key) for key in ("url", "source_url", "href")):
            yield value
        for item in list(value.values())[:160]:
            if isinstance(item, (Mapping, list, tuple)):
                yield from _iter_research_source_candidates(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in list(value)[:160]:
            if isinstance(item, (Mapping, list, tuple)):
                yield from _iter_research_source_candidates(item, depth=depth + 1)
            elif isinstance(item, str):
                yield item


def _candidate_is_pdf(candidate: object, url: str) -> bool:
    metadata = candidate if isinstance(candidate, Mapping) else {}
    try:
        parsed = urlparse(url)
        path = str(parsed.path or "").casefold()
        query = str(parsed.query or "").casefold()
    except Exception:
        return False
    if path.endswith(".pdf") or ".pdf" in query:
        return True
    media_type = " ".join(
        str(metadata.get(key) or "")
        for key in ("content_type", "mime_type", "media_type", "format", "file_type")
    ).casefold()
    if "application/pdf" in media_type or re.search(r"(?:^|[\s/._-])pdf(?:$|[\s/._-])", media_type):
        return True
    descriptor = " ".join(
        str(metadata.get(key) or "")
        for key in ("source_type", "passage_type", "document_type", "title", "name", "filename")
    ).casefold()
    descriptor = re.sub(r"[_/.-]+", " ", descriptor)
    if re.search(r"\b(pdf|manual|catalog(?:o|ue)?|datasheet|ficha tecnica|technical document)\b", descriptor):
        return True
    return bool(re.search(r"/(?:download|document|documents|manual|catalog)(?:/|$)", path))


def _pdf_source_urls(research: object) -> list[str]:
    data = research.get("result") if isinstance(research, Mapping) else {}
    data = data if isinstance(data, Mapping) else {}
    candidates: list[object] = list(data.get("research_sources") or [])
    for evidence_key in (
        "product_research_evidence", "verified_product_evidence", "verified_target_evidence",
    ):
        for evidence in list(data.get(evidence_key) or [])[:160]:
            if not isinstance(evidence, Mapping):
                continue
            candidates.extend(evidence.get("sources") or [])
    candidates.extend(_iter_research_source_candidates(data) or [])
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = (
            candidate.get("url") or candidate.get("source_url") or candidate.get("href")
            if isinstance(candidate, Mapping) else candidate
        )
        url = canonical_research_url(str(value or ""))
        if not url or url in seen:
            continue
        if not _candidate_is_pdf(candidate, url):
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= 10:
            break
    return urls


def _response_bytes(response: object, maximum: int) -> bytes:
    buffered = getattr(response, "content", None)
    if isinstance(buffered, (bytes, bytearray)):
        return bytes(buffered[: maximum + 1])
    iterator = getattr(response, "iter_content", None)
    if not callable(iterator):
        return b""
    payload = bytearray()
    for chunk in iterator(chunk_size=64 * 1024):
        if not chunk:
            continue
        payload.extend(bytes(chunk))
        if len(payload) > maximum:
            break
    return bytes(payload)


def fetch_public_pdf_for_vision(
    url: str,
    *,
    deadline_monotonic: float,
) -> bytes:
    """Fetch one public PDF with fixed SSRF, redirect, deadline and size gates."""

    safe_url = canonical_research_url(url)
    if not safe_url or not _perguntas_ia_v2_url_fonte_tecnica_segura(
        safe_url, resolve_dns=True,
    ):
        return b""
    timeouts = bounded_research_request_timeouts(deadline_monotonic)
    if timeouts is None:
        return b""
    with closing_research_response(fetch_research_response(
        safe_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; JKSistema/1.0; +https://jksistema.local)",
            "Accept": "application/pdf",
        },
        timeout=timeouts,
        verify=requests_tls_verify(),
        allow_redirects=False,
        stream=True,
        deadline_monotonic=deadline_monotonic,
        maximum_bytes=MAX_DOCUMENT_VISION_PDF_BYTES + 1,
    ), deadline_monotonic=deadline_monotonic) as response:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            return b""
        payload = _response_bytes(response, MAX_DOCUMENT_VISION_PDF_BYTES)
    if (
        len(payload) > MAX_DOCUMENT_VISION_PDF_BYTES
        or not payload.lstrip().startswith(b"%PDF-")
    ):
        return b""
    return payload


def _page_score(text: str, terms: Sequence[str], page_number: int) -> tuple[int, int]:
    normalized = _plain(text)
    exact_hits = sum(1 for term in terms if term and term in normalized)
    code_hits = sum(
        1 for term in terms
        if any(char.isdigit() for char in term) and term in normalized
    )
    relational_hits = sum(
        marker in normalized
        for marker in (
            "installed", "instalado", "mounted", "montado", "carcaca", "housing",
            "thermostat", "termostatica", "replaces", "substitui", "superseded",
            "diagram", "diagrama", "illustration", "aplicacao", "application",
        )
    )
    # Earlier pages are a stable tie breaker, never the principal signal.
    return ((code_hits * 12) + (relational_hits * 5) + exact_hits, -page_number)


def document_vision_page_key(value: Mapping[str, object]) -> str:
    """Return a stable, non-sensitive key for one rendered source page."""

    url = canonical_research_url(str(value.get("source_url") or ""))
    content_hash = str(value.get("content_hash") or "").strip().lower()
    try:
        page = max(0, int(value.get("page") or 0))
    except (TypeError, ValueError):
        page = 0
    if not url or not page or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        return ""
    return hashlib.sha256(
        f"{url}\n{page}\n{content_hash}".encode("utf-8", errors="ignore")
    ).hexdigest()


def _safe_page_render_scale(page: object, preferred: float) -> float | None:
    """Bound Pixmap allocation before asking PyMuPDF to render a page."""

    try:
        rect = getattr(page, "rect")
        width = float(rect.width)
        height = float(rect.height)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
        or width > MAX_DOCUMENT_VISION_PAGE_POINTS
        or height > MAX_DOCUMENT_VISION_PAGE_POINTS
    ):
        return None
    scale = min(
        max(0.0, float(preferred)),
        MAX_DOCUMENT_VISION_IMAGE_DIMENSION / width,
        MAX_DOCUMENT_VISION_IMAGE_DIMENSION / height,
        math.sqrt(MAX_DOCUMENT_VISION_IMAGE_PIXELS / (width * height)),
    )
    if not math.isfinite(scale) or scale <= 0:
        return None
    pixel_width = max(1, math.ceil(width * scale))
    pixel_height = max(1, math.ceil(height * scale))
    if (
        pixel_width > MAX_DOCUMENT_VISION_IMAGE_DIMENSION
        or pixel_height > MAX_DOCUMENT_VISION_IMAGE_DIMENSION
        or pixel_width * pixel_height > MAX_DOCUMENT_VISION_IMAGE_PIXELS
    ):
        return None
    return scale


def _distributed_scanned_page_order(
    scored: Sequence[tuple[tuple[int, int], int, bool]],
    sample_size: int,
) -> list[tuple[tuple[int, int], int, bool]]:
    """Prioritize a deterministic first-to-last sample for scans without text."""

    count = len(scored)
    if count <= 1:
        return list(scored)
    target = max(1, min(int(sample_size), count))
    if target == 1:
        primary_indexes = [0]
    else:
        primary_indexes = sorted({
            round(position * (count - 1) / (target - 1))
            for position in range(target)
        })
    primary = [scored[index] for index in primary_indexes]
    selected = set(primary_indexes)
    return [*primary, *[
        value for index, value in enumerate(scored) if index not in selected
    ]]


def _render_relevant_pdf_pages(
    pdf_bytes: bytes,
    *,
    source_url: str,
    focus_terms: Sequence[str],
    maximum_images: int = MAX_DOCUMENT_VISION_IMAGES,
    deadline_monotonic: float | None = None,
    excluded_page_keys: Iterable[str] = (),
) -> tuple[list[IAChatAttachment], list[dict[str, object]], bool]:
    """Render the most relevant textual or scanned/diagram PDF pages in memory."""

    image_limit = max(0, min(int(maximum_images), MAX_DOCUMENT_VISION_IMAGES))
    if image_limit <= 0:
        return [], [], bool(pdf_bytes)
    if (
        not isinstance(pdf_bytes, (bytes, bytearray))
        or len(pdf_bytes) > MAX_DOCUMENT_VISION_PDF_BYTES
        or not bytes(pdf_bytes).lstrip().startswith(b"%PDF-")
    ):
        return [], [], False
    try:
        import fitz

        document = fitz.open(stream=bytes(pdf_bytes), filetype="pdf")
    except Exception:
        return [], [], False
    try:
        if bool(getattr(document, "needs_pass", False)):
            return [], [], False
        page_count = min(len(document), MAX_DOCUMENT_VISION_PAGES)
        if page_count <= 0:
            return [], [], False
        terms = _safe_focus_terms(focus_terms)
        scored: list[tuple[tuple[int, int], int, bool]] = []
        for index in range(page_count):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            try:
                text = str(document[index].get_text("text") or "")[:120_000]
            except Exception:
                text = ""
            scored.append((_page_score(text, terms, index + 1), index, bool(text.strip())))
        positive = [value for value in scored if value[0][0] > 0]
        if positive:
            selected = sorted(positive, reverse=True)
        else:
            # A scanned diagram has no text layer.  Bounded first-page rendering
            # lets Sol inspect it without interpreting extraction failure as fact
            # absence.
            selected = _distributed_scanned_page_order(scored, image_limit)
        content_hash = hashlib.sha256(bytes(pdf_bytes)).hexdigest()
        canonical_url = canonical_research_url(source_url)
        digest = hashlib.sha256(canonical_url.encode(
            "utf-8", errors="ignore",
        )).hexdigest()[:16]
        excluded = {
            str(value or "").strip().lower()
            for value in excluded_page_keys
            if str(value or "").strip()
        }
        attachments: list[IAChatAttachment] = []
        refs: list[dict[str, object]] = []
        truncated = False
        for _score, index, had_text_layer in selected:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            page_ref = {
                "source_url": canonical_url,
                "page": index + 1,
                "content_hash": content_hash,
            }
            if document_vision_page_key(page_ref) in excluded:
                continue
            if len(attachments) >= image_limit:
                truncated = True
                break
            try:
                page = document[index]
                scale = _safe_page_render_scale(page, 1.5)
                if scale is None:
                    continue
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                png = bytes(pixmap.tobytes("png"))
                if len(png) > MAX_DOCUMENT_VISION_IMAGE_BYTES and scale > 1.0:
                    fallback_scale = _safe_page_render_scale(page, 1.0)
                    if fallback_scale is None:
                        continue
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(fallback_scale, fallback_scale), alpha=False,
                    )
                    png = bytes(pixmap.tobytes("png"))
                if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_DOCUMENT_VISION_IMAGE_BYTES:
                    continue
                name = f"research-{digest}-p{index + 1}.png"
                attachments.append(IAChatAttachment(
                    name=name,
                    mime_type="image/png",
                    data_base64=base64.b64encode(png).decode("ascii"),
                ))
                refs.append({
                    "attachment_name": name,
                    "source_url": canonical_url,
                    "page": index + 1,
                    "text_layer": had_text_layer,
                    "content_hash": content_hash,
                })
            except Exception:
                continue
        return attachments, refs, truncated
    finally:
        document.close()


def render_relevant_pdf_pages(
    pdf_bytes: bytes,
    *,
    source_url: str,
    focus_terms: Sequence[str],
    maximum_images: int = MAX_DOCUMENT_VISION_IMAGES,
    deadline_monotonic: float | None = None,
    excluded_page_keys: Iterable[str] = (),
) -> tuple[list[IAChatAttachment], list[dict[str, object]]]:
    """Compatibility facade for bounded, in-memory page rendering."""

    attachments, refs, _truncated = _render_relevant_pdf_pages(
        pdf_bytes,
        source_url=source_url,
        focus_terms=focus_terms,
        maximum_images=maximum_images,
        deadline_monotonic=deadline_monotonic,
        excluded_page_keys=excluded_page_keys,
    )
    return attachments, refs


def collect_document_vision_batch(
    research: object,
    *,
    focus_terms: Sequence[str],
    fetch_pdf: Callable[..., bytes] = fetch_public_pdf_for_vision,
    maximum_images: int = MAX_DOCUMENT_VISION_IMAGES,
    deadline_monotonic: float | None = None,
    excluded_page_keys: Iterable[str] = (),
) -> DocumentVisionBatchV1:
    """Fetch/render public research PDFs while retaining only transient images."""

    urls = _pdf_source_urls(research)
    image_limit = max(0, min(int(maximum_images), MAX_DOCUMENT_VISION_IMAGES))
    deadline = float(deadline_monotonic or (time.monotonic() + _DEFAULT_DOCUMENT_VISION_SECONDS))
    attachments: list[IAChatAttachment] = []
    page_refs: list[dict[str, object]] = []
    attempted = 0
    processed = 0
    unprocessed = 0
    stop_reason = "no_pdf_sources" if not urls else "completed"
    if image_limit <= 0:
        stop_reason = "image_limit"
    excluded = {
        str(value or "").strip().lower()
        for value in excluded_page_keys
        if str(value or "").strip()
    }
    for url_index, url in enumerate(urls):
        if len(attachments) >= image_limit:
            stop_reason = "image_limit"
            break
        if time.monotonic() >= deadline:
            stop_reason = "deadline"
            break
        attempted += 1
        try:
            payload = fetch_pdf(url, deadline_monotonic=deadline)
        except Exception:
            payload = b""
        if not payload:
            unprocessed += 1
            continue
        rendered, refs, pages_truncated = _render_relevant_pdf_pages(
            payload,
            source_url=url,
            focus_terms=focus_terms,
            maximum_images=image_limit - len(attachments),
            deadline_monotonic=deadline,
            excluded_page_keys=excluded,
        )
        if not rendered:
            unprocessed += 1
            continue
        processed += 1
        attachments.extend(rendered)
        page_refs.extend(refs)
        excluded.update(
            key for key in (document_vision_page_key(value) for value in refs) if key
        )
        if len(attachments) >= image_limit and (
            pages_truncated or url_index < len(urls) - 1
        ):
            stop_reason = "image_limit"
            break
    return DocumentVisionBatchV1(
        attachments=tuple(attachments[:image_limit]),
        page_refs=tuple(page_refs[:image_limit]),
        documents_attempted=attempted,
        documents_processed=processed,
        documents_unprocessed=unprocessed,
        stop_reason=stop_reason,
    )


__all__ = [
    "DocumentVisionBatchV1",
    "MAX_DOCUMENT_VISION_IMAGES",
    "MAX_DOCUMENT_VISION_IMAGE_DIMENSION",
    "MAX_DOCUMENT_VISION_IMAGE_PIXELS",
    "MAX_DOCUMENT_VISION_PAGE_POINTS",
    "PRODUCT_DOCUMENT_VISION_POLICY",
    "collect_document_vision_batch",
    "document_vision_page_key",
    "fetch_public_pdf_for_vision",
    "render_relevant_pdf_pages",
    "technical_focus_terms",
]
