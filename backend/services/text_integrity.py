"""Strict UTF-8 and mojibake validation for server-owned IA sources.

These helpers never attempt to repair arbitrary user text. Code, prompts and
configuration owners must fix a rejected source explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Collection


REPLACEMENT_CHARACTER = "\ufffd"
MOJIBAKE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:\u00c3[\u0080-\u00bf\u0192]|\u00c2[\u0080-\u00bf]|\u00e2[\u0080-\u00bf\u20ac]|\u00f0\u0178)"),
    re.compile(r"(?:\u00c3\u0192|\u00c3\u00a2)"),
)


class TextIntegrityError(ValueError):
    """Raised when a server-owned source violates the text contract."""


def decode_utf8_strict(value: Any, *, source_ref: str = "") -> str:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("utf8_source_must_be_bytes")
    try:
        return bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        label = str(source_ref or "ia_source")[:200]
        raise TextIntegrityError(f"invalid_utf8:{label}") from exc


def read_text_utf8_strict(path: str | Path) -> str:
    source = Path(path)
    return decode_utf8_strict(source.read_bytes(), source_ref=str(source))


def integrity_findings(value: Any, *, legacy_patterns: Collection[str] = ()) -> list[str]:
    text = str(value or "")
    allowed = {str(item) for item in legacy_patterns}
    findings: list[str] = []
    if REPLACEMENT_CHARACTER in text and REPLACEMENT_CHARACTER not in allowed:
        findings.append("replacement_character")
    scan_text = text
    for item in sorted(allowed, key=len, reverse=True):
        if item:
            scan_text = scan_text.replace(item, "")
    if any(pattern.search(scan_text) for pattern in MOJIBAKE_PATTERNS):
        findings.append("high_confidence_mojibake")
    return findings


def require_clean_text(
    value: Any,
    *,
    source_ref: str = "",
    legacy_patterns: Collection[str] = (),
) -> str:
    text = str(value or "")
    findings = integrity_findings(text, legacy_patterns=legacy_patterns)
    if findings:
        label = str(source_ref or "ia_source")[:200]
        raise TextIntegrityError(f"text_integrity_failed:{label}:{','.join(findings)}")
    return text


__all__ = [
    "MOJIBAKE_PATTERNS",
    "REPLACEMENT_CHARACTER",
    "TextIntegrityError",
    "decode_utf8_strict",
    "integrity_findings",
    "read_text_utf8_strict",
    "require_clean_text",
]
