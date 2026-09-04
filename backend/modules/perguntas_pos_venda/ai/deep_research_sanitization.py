"""VIN/PII-safe projections used by public product research."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from backend.services.vin_transient import (
    contains_vin_like_identifier,
    is_valid_vin,
    sanitize_vin_like_text,
)


_DISTRIBUTOR_HOST_PARTS = (
    "catalog",
    "distrib",
    "autopec",
    "autopart",
    "parts",
    "pecas",
)
_CURATED_TECHNICAL_DISTRIBUTOR_DOMAINS = frozenset(
    {
        "autodoc.eu",
        "bike-components.de",
        "bike-discount.de",
        "digikey.com",
        "grainger.com",
        "mister-auto.com",
        "mouser.com",
        "oscaro.com",
        "rockauto.com",
        "rs-online.com",
        "summitracing.com",
    }
)
_CURATED_TECHNICAL_INDEPENDENT_DOMAINS = frozenset(
    {
        "allaboutcircuits.com",
        "engineeringtoolbox.com",
        "repairpal.com",
        "underhoodservice.com",
    }
)
_IDENTITY_STOPWORDS = frozenset(
    {
        "para",
        "com",
        "sem",
        "produto",
        "peca",
        "peça",
        "novo",
        "nova",
        "original",
        "modelo",
        "universal",
        "kit",
        "unidade",
        "mercado",
        "livre",
    }
)
_OEM_LABEL_METADATA = frozenset({"63q", "level", "oper", "serial"})
_MISSING_FACT_VALUES = frozenset(
    {
        "unknown",
        "unavailable",
        "notavailable",
        "notinformed",
        "naoinformado",
        "naodisponivel",
        "naoaplicavel",
        "semcodigo",
        "nenhum",
        "null",
    }
)
_COMMON_TWO_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        "co.jp",
        "co.nz",
        "co.uk",
        "com.ar",
        "com.au",
        "com.br",
        "com.cn",
        "com.mx",
        "net.br",
        "org.br",
        "org.uk",
    }
)
_VIN_ANYWHERE_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{17})(?![A-Z0-9])", re.IGNORECASE)
_VIN_LABELLED_TAIL_RE = re.compile(
    r"\b(?:chassi|chassis|vin)\b([^.!?;\r\n]{0,96})",
    re.IGNORECASE,
)
_VIN_GROUPED_ANYWHERE_RE = re.compile(
    r"(?<![A-Z0-9])((?:[A-Z0-9]{1,5}[ -]+){2,20}[A-Z0-9]{1,5})(?![A-Z0-9])",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_CPF_CNPJ_RE = re.compile(r"(?<!\d)(?:\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-\s]?\d{2}|\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\s-]?\d{4}[-\s]?\d{2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?55[\s.-]?)?(?:\(\d{2}\)|\d{2}[\s.-])"
    r"[\s.-]?(?:9\d{4}|\d{4})[\s.-]?\d{4}(?!\d)"
)
_PHONE_BR_COMPACT_RE = re.compile(
    r"(?<!\d)55[1-9]\d(?:9\d{8}|\d{8})(?!\d)"
)
_PHONE_LABEL_RE = re.compile(
    r"\b(?:telefone|tel|whatsapp|celular|fone|phone|contact|contacto|"
    r"call|mobile|m[oó]vil|tel[eé]fono|ligue)\s*[:=\-]?\s*"
    r"(?:\+?\d[\d\s().-]{7,}\d)",
    re.IGNORECASE,
)
_PHONE_INTL_RE = re.compile(
    r"(?<![\w+])\+\d{1,3}(?:[\s().-]*\d){7,14}(?!\d)"
)
_PHONE_NANP_RE = re.compile(
    r"(?<!\d)(?:\([2-9]\d{2}\)|[2-9]\d{2}[\s.-])"
    r"[\s.-]?[2-9]\d{2}[\s.-]\d{4}(?!\d)"
)
_PROTECTED_VIN_MARKER = "[CHASSI_PROTEGIDO]"


def _plain(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


_NEGATED_TECHNICAL_PREFIX = re.compile(
    r"(?:"
    r"\b(?:nunca|jamais|never|sem|without)\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\bnao\s+(?:possui|tem|usa|utiliza|inclui|e|aceita|suporta|adota|emprega|"
    r"oferece|dispoe|equipa|fornece|apresenta)\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\b(?:nao|not)\s+compativel\s+com\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\bnot\s+compatible\s+with\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\b(?:does\s+not|doesn't|isn't|can't|cannot)\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\b(?:incompativel\s+com|incompatible\s+with)\b(?:\s+[a-z0-9_./'-]+){0,8}|"
    r"\b(?:nao|not)\b"
    r")\s*$"
)
_NEGATED_TECHNICAL_INSIDE = re.compile(
    r"(?:[:=\-]\s*|\b)(?:nao|sem|not|without|never|nunca|jamais)\b"
)
_NEGATED_TECHNICAL_SUFFIX = re.compile(
    r"\s*[,:\-]?\s*(?:"
    r"nao\s+(?:suportad[oa]s?|disponivel|compativel|incluid[oa]s?|utilizad[oa]s?|se\s+aplica)|"
    r"nao\s+e\s+compativel|is\s+not\s+compatible|sem\s+suporte|lacks\s+support|"
    r"nunca|jamais|incompativel|incompatible|unsupported|unavailable|"
    r"not\s+(?:supported|available|compatible|included)|"
    r"isn't\s+(?:supported|available|compatible)|can't\s+be\s+used"
    r")\b"
)


def technical_assertion_occurrence_is_positive(text: str, start: int, end: int) -> bool:
    """Return false when a technical value is denied in its own clause."""

    source = str(text or "").casefold()
    bounded_start = max(0, min(int(start), len(source)))
    bounded_end = max(bounded_start, min(int(end), len(source)))
    delimiters = ".;\n|"
    clause_start = max(source.rfind(delimiter, 0, bounded_start) for delimiter in delimiters) + 1
    following = [
        position for delimiter in delimiters
        if (position := source.find(delimiter, bounded_end)) >= 0
    ]
    clause_end = min(following) if following else len(source)
    prefix = source[clause_start:bounded_start][-160:]
    matched = source[bounded_start:bounded_end]
    suffix = source[bounded_end:clause_end][:120]
    return not bool(
        _NEGATED_TECHNICAL_PREFIX.search(prefix)
        or _NEGATED_TECHNICAL_INSIDE.search(matched)
        or _NEGATED_TECHNICAL_SUFFIX.match(suffix)
    )


def _label_metadata_code(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", _plain(value))
    return normalized in _OEM_LABEL_METADATA or any(
        normalized.startswith(prefix)
        for prefix in ("level", "oper", "serial")
    )


def _missing_fact_value(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", _plain(value))
    return not normalized or normalized in _MISSING_FACT_VALUES


def _safe_text(value: object, maximum: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _percent_decode_fixed(value: object, *, rounds: int = 8) -> tuple[str, bool]:
    """Decode bounded nesting; callers must reject/redact an unfinished value."""

    current = str(value or "")
    for _attempt in range(max(1, int(rounds))):
        decoded = unquote(current)
        if decoded == current:
            return current, True
        current = decoded
    return current, unquote(current) == current


def sanitize_public_research_text(value: object, maximum: int = 600_000) -> str:
    """Remove VIN and contact/identity PII from untrusted web text."""

    source, decoding_complete = _percent_decode_fixed(value)
    if not decoding_complete:
        return "[CONTEUDO_CODIFICADO_PROTEGIDO]"[: max(0, int(maximum))]

    def replace_compact(match: re.Match[str]) -> str:
        candidate = match.group(1)
        return (
            _PROTECTED_VIN_MARKER
            if is_valid_vin(candidate)
            or sum(character.isdigit() for character in candidate) >= 2
            else candidate
        )

    def replace_grouped(match: re.Match[str]) -> str:
        candidate = match.group(1)
        compact = re.sub(r"[ -]+", "", candidate)
        return (
            _PROTECTED_VIN_MARKER
            if len(compact) == 17
            and (
                is_valid_vin(compact)
                or sum(character.isdigit() for character in compact) >= 2
            )
            else candidate
        )

    sanitized = sanitize_vin_like_text(source)
    sanitized = _VIN_ANYWHERE_RE.sub(replace_compact, sanitized)
    sanitized = _VIN_GROUPED_ANYWHERE_RE.sub(replace_grouped, sanitized)
    sanitized = _EMAIL_RE.sub("[EMAIL_PROTEGIDO]", sanitized)
    sanitized = _CPF_CNPJ_RE.sub("[DOCUMENTO_PROTEGIDO]", sanitized)
    sanitized = _PHONE_LABEL_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_INTL_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_NANP_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_BR_COMPACT_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    return sanitized[: max(0, int(maximum))]


def safe_agent_research_metadata(value: object, maximum: int) -> str:
    safe = re.sub(r"\s+", " ", sanitize_public_research_text(value, maximum)).strip()[:maximum]
    if "_PROTEGIDO]" in safe.upper() or contains_vin_like_identifier(safe):
        return ""
    return safe

def safe_agent_research_metrics(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    safe: dict[str, Any] = {}
    for key, metric in list(raw.items())[:64]:
        raw_key = str(key or "")
        if contains_vin_like_identifier(raw_key):
            continue
        safe_key = re.sub(
            r"[^a-z0-9_.-]", "", safe_agent_research_metadata(raw_key, 64).lower()
        )[:64]
        if not safe_key:
            continue
        if isinstance(metric, (bool, int, float)) or metric is None:
            safe[safe_key] = metric
            continue
        safe_value = safe_agent_research_metadata(metric, 120)
        if safe_value:
            safe[safe_key] = safe_value
    return safe


def safe_agent_product_research_evidence(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the final VIN/PII-safe projection before compiled facts enter a prompt."""

    safe: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if index >= 160:
            break
        if not isinstance(value, Mapping):
            continue
        if contains_vin_like_identifier(value.get("value")):
            continue
        normalized_value = re.sub(
            r"\s+", " ", sanitize_public_research_text(value.get("value"), 300)
        ).strip()[:300]
        if not normalized_value or contains_vin_like_identifier(normalized_value):
            continue
        sources: list[dict[str, str]] = []
        for source in (value.get("sources") or [])[:8]:
            if not isinstance(source, Mapping):
                continue
            safe_url = sanitize_public_research_text(source.get("url"), 700)
            if "_PROTEGIDO]" in safe_url.upper():
                safe_url = ""
            projection = {
                "source_type": sanitize_public_research_text(source.get("source_type"), 40),
                "authority": sanitize_public_research_text(source.get("authority"), 40),
                "url": safe_url,
                "domain": sanitize_public_research_text(source.get("domain"), 200),
                "section_ref": re.sub(
                    r"\s+", " ", sanitize_public_research_text(source.get("section_ref"), 160)
                ).strip()[:160],
                "collected_at": sanitize_public_research_text(
                    source.get("collected_at"), 40
                ),
                "valid_until": sanitize_public_research_text(
                    source.get("valid_until"), 40
                ),
            }
            if any(contains_vin_like_identifier(part) for part in projection.values()):
                continue
            sources.append(projection)
        safe.append({
            "field_name": re.sub(
                r"[^a-z0-9_.]", "",
                sanitize_public_research_text(value.get("field_name"), 96).lower(),
            )[:96],
            "scope": sanitize_public_research_text(value.get("scope"), 24),
            "value": normalized_value,
            "unit": sanitize_public_research_text(value.get("unit"), 16),
            "state": sanitize_public_research_text(
                value.get("state") or "candidate", 24
            ),
            "activation_policy": sanitize_public_research_text(
                value.get("activation_policy"), 64
            ),
            "conflict_group": sanitize_public_research_text(
                value.get("conflict_group"), 40
            ),
            "valid_from": sanitize_public_research_text(value.get("valid_from"), 40),
            "valid_until": sanitize_public_research_text(value.get("valid_until"), 40),
            "sources": sources,
            "source_authorities": [
                safe_authority
                for authority in (value.get("source_authorities") or [])[:8]
                if (
                    safe_authority := sanitize_public_research_text(authority, 40)
                )
                and "_PROTEGIDO]" not in safe_authority.upper()
                and not contains_vin_like_identifier(safe_authority)
            ],
        })
    return safe
