"""DLP API that emits safe category-only findings."""

from __future__ import annotations

from typing import Any

from backend.modules.context_hub.dlp_core import _dlp_categories
from backend.modules.context_hub.findings import _finding


def scan_dlp(content: object, *, source_ref: str = "") -> list[dict[str, Any]]:
    """Return category-only blockers; never return a match or excerpt."""

    text = str(content or "")
    return [
        _finding("dlp_blocked", category=category, source_ref=source_ref)
        for category in sorted(_dlp_categories(text))
    ]
