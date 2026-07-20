"""Small, side-effect-free helpers for WhatsApp AI provider tasks."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from backend.schemas import IAChatAttachment


def tool_summary(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def task_attachments(paths: list[str], base_dir: Path) -> list[IAChatAttachment]:
    attachments: list[IAChatAttachment] = []
    for raw in paths[:4]:
        try:
            path = Path(str(raw or ""))
            if not path.is_absolute():
                path = (base_dir / path).resolve()
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
