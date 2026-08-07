"""Normalize Mercado Livre message attachments for agent input."""

from __future__ import annotations

import re
from urllib.parse import quote_plus


def _attachment_url(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    for key in (
        "url", "secure_url", "download_url", "file_url", "original_url",
        "thumbnail", "thumbnail_url", "preview_url", "image_url", "src",
    ):
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    for key in ("file", "image", "picture", "source", "content", "data"):
        child = item.get(key)
        if isinstance(child, dict):
            url = _attachment_url(child)
            if url:
                return url
    return ""


def _raw_attachments(message: dict) -> list:
    raw_items: list = []

    def add(value) -> None:
        if isinstance(value, list):
            raw_items.extend(item for item in value if item)
        elif isinstance(value, dict):
            found_list = False
            for key in ("attachments", "files", "images", "pictures"):
                children = value.get(key)
                if isinstance(children, list):
                    found_list = True
                    raw_items.extend(item for item in children if item)
            if not found_list:
                raw_items.append(value)
        elif isinstance(value, str) and value.strip():
            raw_items.append(value)

    for key in ("attachments", "message_attachments", "files", "images", "pictures"):
        add(message.get(key))
    return raw_items


def _normalize_attachment(raw, store: str) -> dict | None:
    if isinstance(raw, str):
        url = raw.strip()
        attachment_id = ""
        name = url.split("?")[0].rstrip("/").split("/")[-1] or "anexo"
        mime = ""
    elif isinstance(raw, dict):
        attachment_keys = (
            "url", "secure_url", "download_url", "file_url", "original_url",
            "thumbnail", "thumbnail_url", "preview_url", "image_url", "src",
            "filename", "file_name", "original_filename", "attachment_id", "file_id",
            "mime_type", "content_type", "media_type", "type",
        )
        if not any(raw.get(key) for key in attachment_keys) and str(raw.get("name") or "").strip().lower() in {"packs", "orders", "items", "order"}:
            return None
        attachment_id = str(raw.get("id") or raw.get("attachment_id") or raw.get("file_id") or raw.get("resource_id") or "").strip()
        url = _attachment_url(raw)
        name = str(raw.get("filename") or raw.get("file_name") or raw.get("original_filename") or raw.get("name") or raw.get("title") or attachment_id or "anexo").strip()
        mime = str(raw.get("mime_type") or raw.get("content_type") or raw.get("type") or raw.get("media_type") or "").strip()
    else:
        return None
    if not url and attachment_id:
        url = f"/api/mercadolivre/pos-venda/anexos/{quote_plus(attachment_id)}?loja={quote_plus(str(store or '').strip())}"
    key = url or attachment_id or name
    if not key:
        return None
    is_image = bool(re.search(r"image|foto|picture|jpg|jpeg|png|webp|gif", f"{mime} {name} {url}", re.I))
    return {"id": attachment_id, "name": name[:160], "mime_type": mime[:120], "url": url, "is_image": is_image}


def message_attachments(message: dict, store: str = "") -> list[dict]:
    attachments: list[dict] = []
    seen: set[str] = set()
    for raw in _raw_attachments(message):
        attachment = _normalize_attachment(raw, store)
        key = str((attachment or {}).get("url") or (attachment or {}).get("id") or (attachment or {}).get("name") or "")
        if not attachment or key in seen:
            continue
        seen.add(key)
        attachments.append(attachment)
    return attachments


__all__ = ["message_attachments"]
