"""Pure DLP detection primitives without finding construction."""

from __future__ import annotations

import re
from typing import Any, Mapping


_DLP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("email", re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)),
    (
        "credential",
        re.compile(
            r"(?ix)\b(?:"
            r"access[_ -]?token|refresh[_ -]?token|oauth[_ -]?token|auth[_ -]?token|"
            r"token|client[_ -]?secret|api[_ -]?key|password|senha"
            r")\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?!redacted\b|redigido\b|masked\b|none\b|null\b|false\b|\*{3,}|<[^>]+>)"
            r"[^\s\"'<>]{6,}"
            r"|\b(?:authorization\b[\"']?\s*(?:=|:)\s*[\"']?)?"
            r"bearer\s+(?!redacted\b|redigido\b|masked\b|\*{3,}|<[^>]+>)"
            r"[A-Za-z0-9._~+/=-]{12,}"
            r"|\boauth\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?!redacted\b|redigido\b|masked\b|none\b|null\b|false\b|\*{3,}|<[^>]+>)"
            r"[^\s\"'<>]{6,}"
        ),
    ),
    ("buyer_data", re.compile(r"(?i)\b(?:comprador|buyer|recipient|destinatario)\b\s*(?:=|:)\s*[^\s#][^\n]{2,}")),
    (
        "address",
        re.compile(
            r"(?ix)\b(?:"
            r"(?:endereco|logradouro)\s*(?:=|:)\s*[^\s#][^\n]{3,}"
            r"|(?:rua|avenida|av\.)\s+[A-Z0-9À-ÿ .'-]{2,60}(?:,\s*|\s+)\d{1,6}\b"
            r"|cep\s*(?:=|:)?\s*\d{5}-?\d{3}\b"
            r")"
        ),
    ),
    (
        "phone",
        re.compile(
            r"(?<!\d)(?:(?:\+?55[\s.-]+)?\([1-9]\d\)[\s.-]*9?\d{4}[\s.-]+\d{4}"
            r"|(?:\+?55[\s.-]+)?[1-9]\d[\s.-]+9?\d{4}[\s.-]+\d{4}"
            r"|(?i:\b(?:telefone|phone|celular|whatsapp|fone)\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?:\+?55)?[1-9]\d9?\d{8}))(?!\d)"
        ),
    ),
)


def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for position in (9, 10):
        total = sum(int(digits[index]) * ((position + 1) - index) for index in range(position))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[position]):
            return False
    return True


def _valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for position, weights in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        total = sum(int(digits[index]) * weights[index] for index in range(position))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != int(digits[position]):
            return False
    return True


def _dlp_categories(content: str) -> set[str]:
    categories = {category for category, pattern in _DLP_PATTERNS if pattern.search(content)}
    document_numbers = re.findall(
        r"(?ix)(?:"
        r"\b(?:cpf|cnpj)\b[\"']?\s*(?:=|:)\s*[\"']?(\d{14}|\d{11})(?!\d)"
        r"|(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})(?!\d)"
        r")",
        content,
    )
    for compact, formatted in document_numbers:
        digits = re.sub(r"\D", "", compact or formatted)
        if _valid_cpf(digits):
            categories.add("cpf")
        elif _valid_cnpj(digits):
            categories.add("cnpj")
    return categories


def _dlp_document_text(metadata: Mapping[str, Any], body: str) -> str:
    surface = [str(body or "")]
    visited: set[int] = set()

    def append_value(value: Any, label: str = "") -> None:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for raw_key in sorted(value, key=lambda item: str(item)):
                key = str(raw_key)
                surface.append(key)
                append_value(value[raw_key], key)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            items = sorted(value, key=lambda item: str(item)) if isinstance(value, (set, frozenset)) else value
            for item in items:
                append_value(item, label)
            return
        text = str(value or "")
        surface.append(f"{label}: {text}" if label else text)

    append_value(metadata)
    return "\n".join(surface)
