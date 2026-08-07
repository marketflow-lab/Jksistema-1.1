"""Context Hub findings component."""

from __future__ import annotations

import re
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)



from backend.modules.context_hub.contracts import (
    _VOLATILE_INVENTORY_KEYS,
)

from backend.modules.context_hub.dlp_core import (
    _dlp_categories,
)


def _finding(
    code: str,
    *,
    severity: str = "blocker",
    category: str = "validation",
    source_ref: str = "",
    count: int = 1,
) -> dict[str, Any]:
    # Never accept a message or match value here: findings are safe metadata only.
    payload: dict[str, Any] = {
        "code": re.sub(r"[^a-z0-9_.-]+", "_", str(code or "finding").lower())[:80],
        "severity": str(severity or "blocker").lower()[:20],
        "category": re.sub(r"[^a-z0-9_.-]+", "_", str(category or "validation").lower())[:80],
        "count": max(1, int(count or 1)),
    }
    if source_ref:
        safe_ref = str(source_ref).replace("\\", "/")
        # A source reference is useful for remediation, but it is untrusted
        # metadata too.  Never copy a filename/path containing PII or a secret
        # into a finding: findings are persisted and may also reach logs/UI.
        if (
            len(safe_ref) <= 300
            and not any(character in safe_ref for character in "\r\n\x00")
            and ".." not in Path(safe_ref).parts
            and not Path(safe_ref).is_absolute()
            and not _dlp_categories(safe_ref)
        ):
            payload["source_ref"] = safe_ref
    return payload


def _has_blocker(findings: Sequence[Mapping[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").lower() in {"blocker", "critical", "error"} for item in findings)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_INVENTORY_KEYS
        }
    if isinstance(value, (list, tuple)):
        normalized = [_strip_volatile(item) for item in value]
        if all(isinstance(item, Mapping) and item.get("id") for item in normalized):
            normalized.sort(key=lambda item: str(item.get("id")))
        return normalized
    if isinstance(value, Path):
        return value.as_posix()
    return value
