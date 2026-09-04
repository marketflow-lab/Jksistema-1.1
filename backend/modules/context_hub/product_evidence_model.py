"""Validation and normalization primitives for structured product evidence."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.runtime import _utc_now
from backend.services.vin_transient import contains_vin_like_identifier


PRODUCT_EVIDENCE_POLICY = "jk_product_evidence_v2"
PRODUCT_EVIDENCE_SCOPES = frozenset(
    {"product", "package", "kit", "variation", "application"}
)
PRODUCT_EVIDENCE_SOURCE_TYPES = frozenset(
    {
        "official_manufacturer",
        "official_oem",
        "official_listing",
        "technical_distributor",
        "technical_independent",
        "marketplace",
        "forum",
        "blog",
    }
)

_SOURCE_AUTHORITIES = {
    "official_manufacturer": "official",
    "official_oem": "official",
    "official_listing": "official",
    "technical_distributor": "technical",
    "technical_independent": "technical",
    "marketplace": "lead",
    "forum": "lead",
    "blog": "lead",
}
_SOURCE_TTL = {
    "official_manufacturer": timedelta(days=365),
    "official_oem": timedelta(days=365),
    "official_listing": timedelta(hours=24),
    "technical_distributor": timedelta(days=90),
    "technical_independent": timedelta(days=90),
    "marketplace": timedelta(hours=24),
    "forum": timedelta(hours=24),
    "blog": timedelta(hours=24),
}
_OFFICIAL_STRONG_SOURCE_TYPES = frozenset(
    {"official_manufacturer", "official_oem"}
)
_TECHNICAL_SOURCE_TYPES = frozenset(
    {"technical_distributor", "technical_independent"}
)

_FIELD_RE = re.compile(r"^[a-z][a-z0-9_.]{0,95}$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_CPF_CNPJ_RE = re.compile(
    r"(?<!\d)(?:\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-\s]?\d{2}|"
    r"\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\s-]?\d{4}[-\s]?\d{2})(?!\d)"
)
_LABELLED_PHONE_RE = re.compile(
    r"\b(?:telefone|phone|whatsapp|celular)\b[^\d]{0,12}\+?\d[\d ()-]{7,20}",
    re.IGNORECASE,
)
_NUMBER_WITH_UNIT_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+))\s*([A-Za-z]+)?\s*$"
)
_PERFORMANCE_NUMBER_WITH_UNIT_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+))\s*"
    r"(l\s*/\s*(?:h|hr|min)|lph|lpm|m3\s*/\s*h|bar|psi|kpa|mpa)\s*$",
    re.IGNORECASE,
)
_DIMENSIONS_RE = re.compile(
    r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*[xX×]\s*"
    r"([+-]?\d+(?:[.,]\d+)?)"
    r"(?:\s*[xX×]\s*([+-]?\d+(?:[.,]\d+)?))?\s*([A-Za-z]+)\s*$"
)

_FORBIDDEN_FIELD_PARTS = frozenset(
    {
        "vin", "chassis", "chassi", "buyer", "comprador", "question", "pergunta",
        "answer", "response", "resposta", "prompt", "history", "historico", "phone",
        "telefone", "email", "cpf", "cnpj", "address", "endereco", "raw", "html",
        "snippet", "commercial", "price", "preco", "inventory", "stock", "estoque",
        "promotion", "promocao", "shipping", "envio", "postagem", "delivery", "prazo",
    }
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "vin", "chassi", "chassis", "email", "phone", "telefone", "cpf", "cnpj",
        "buyer", "comprador", "price", "preco", "stock", "estoque", "promotion",
        "promocao", "shipping", "envio", "postagem", "delivery", "prazo", "token",
        "access_token", "api_key", "key", "auth", "authorization", "secret",
        "signature", "sig", "session",
    }
)
_SENSITIVE_QUERY_PREFIXES = ("x-amz-", "x-goog-")
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "msclkid"})
_COMMON_TWO_LABEL_PUBLIC_SUFFIXES = frozenset(
    {"co.jp", "co.uk", "com.ar", "com.au", "com.br", "com.mx", "net.br", "org.br"}
)
_UNIT_ALIASES = {
    "g": ("g", Decimal("1")),
    "grama": ("g", Decimal("1")),
    "gramas": ("g", Decimal("1")),
    "kg": ("g", Decimal("1000")),
    "kgs": ("g", Decimal("1000")),
    "mg": ("g", Decimal("0.001")),
    "mm": ("mm", Decimal("1")),
    "cm": ("mm", Decimal("10")),
    "m": ("mm", Decimal("1000")),
    "v": ("V", Decimal("1")),
    "mv": ("V", Decimal("0.001")),
    "kv": ("V", Decimal("1000")),
    "a": ("A", Decimal("1")),
    "ma": ("A", Decimal("0.001")),
    "w": ("W", Decimal("1")),
    "kw": ("W", Decimal("1000")),
    "hz": ("Hz", Decimal("1")),
    "khz": ("Hz", Decimal("1000")),
    "l/h": ("L/h", Decimal("1")),
    "l/hr": ("L/h", Decimal("1")),
    "lph": ("L/h", Decimal("1")),
    "l/min": ("L/h", Decimal("60")),
    "lpm": ("L/h", Decimal("60")),
    "m3/h": ("L/h", Decimal("1000")),
    "bar": ("kPa", Decimal("100")),
    "psi": ("kPa", Decimal("6.894757293168")),
    "kpa": ("kPa", Decimal("1")),
    "mpa": ("kPa", Decimal("1000")),
}


@dataclass(frozen=True)
class NormalizedEvidenceValue:
    value: str
    key: str
    unit: str


def _normalize_text(value: object, *, label: str, maximum: int, required: bool = True) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = " ".join(normalized.split()).strip()
    if required and not normalized:
        raise ContextHubValidationError(f"{label} e obrigatorio para a evidencia de produto.")
    if len(normalized) > maximum:
        raise ContextHubValidationError(f"{label} excede o limite da evidencia de produto.")
    if "\x00" in normalized:
        raise ContextHubValidationError(f"{label} contem caracteres invalidos.")
    return normalized


def _reject_sensitive(value: str, *, label: str) -> None:
    if (
        contains_vin_like_identifier(value)
        or _EMAIL_RE.search(value)
        or _CPF_CNPJ_RE.search(value)
        or _LABELLED_PHONE_RE.search(value)
    ):
        raise ContextHubValidationError(f"{label} contem dado pessoal proibido.")


def _normalize_identifier(
    value: object,
    *,
    label: str,
    required: bool = True,
    maximum: int = 128,
) -> str:
    normalized = _normalize_text(value, label=label, maximum=maximum, required=required)
    _reject_sensitive(normalized, label=label)
    return normalized


def _normalize_opaque_id(value: object, *, label: str) -> str:
    """Validate an internal identifier without interpreting random digits as PII."""

    normalized = _normalize_text(value, label=label, maximum=128)
    if not _OPAQUE_ID_RE.fullmatch(normalized):
        raise ContextHubValidationError(f"{label} invalido para a evidencia de produto.")
    return normalized


def _normalize_field_name(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not _FIELD_RE.fullmatch(normalized):
        raise ContextHubValidationError("Campo tecnico invalido para a evidencia de produto.")
    if set(re.split(r"[._]", normalized)) & _FORBIDDEN_FIELD_PARTS:
        raise ContextHubValidationError("Campo proibido para a evidencia de produto.")
    return normalized


def _normalize_hash(value: object, *, label: str, required: bool = True) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and not required:
        return ""
    if not _SHA256_RE.fullmatch(normalized):
        raise ContextHubValidationError(f"{label} invalido para a evidencia de produto.")
    return normalized


def _decimal_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _comparison_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ContextHubValidationError("Valor numerico invalido para a evidencia de produto.") from exc
    if parsed < 0:
        raise ContextHubValidationError("Medida negativa invalida para a evidencia de produto.")
    return parsed


def normalize_product_evidence_value(
    value: object,
    *,
    unit: Optional[object] = None,
) -> NormalizedEvidenceValue:
    """Normalize scalar measurements or short factual text without retaining raw text."""

    text = _normalize_text(value, label="Valor", maximum=256)
    _reject_sensitive(text, label="Valor")
    explicit_unit = _normalize_text(unit, label="Unidade", maximum=16, required=False)
    performance_match = _PERFORMANCE_NUMBER_WITH_UNIT_RE.fullmatch(text)
    if performance_match and not explicit_unit:
        raw_unit = re.sub(r"\s+", "", performance_match.group(2)).casefold()
        canonical_unit, factor = _UNIT_ALIASES[raw_unit]
        normalized = _decimal_text(_parse_decimal(performance_match.group(1)) * factor)
        return NormalizedEvidenceValue(normalized, normalized, canonical_unit)
    dimension_match = _DIMENSIONS_RE.fullmatch(text) if not explicit_unit else None
    if dimension_match:
        raw_unit = dimension_match.group(4).casefold()
        if raw_unit not in _UNIT_ALIASES or _UNIT_ALIASES[raw_unit][0] != "mm":
            raise ContextHubValidationError("Unidade de dimensao nao suportada.")
        canonical_unit, factor = _UNIT_ALIASES[raw_unit]
        dimensions = [
            _decimal_text(_parse_decimal(part) * factor)
            for part in dimension_match.groups()[:3]
            if part is not None
        ]
        normalized = "x".join(dimensions)
        return NormalizedEvidenceValue(normalized, normalized, canonical_unit)
    match = _NUMBER_WITH_UNIT_RE.fullmatch(text)
    embedded_unit = match.group(2) if match else ""
    if explicit_unit and embedded_unit:
        explicit_definition = _UNIT_ALIASES.get(explicit_unit.casefold())
        embedded_definition = _UNIT_ALIASES.get(embedded_unit.casefold())
        if explicit_definition is None or embedded_definition is None:
            raise ContextHubValidationError("Unidade nao suportada para a evidencia de produto.")
        if explicit_definition != embedded_definition:
            raise ContextHubValidationError("Unidades conflitantes para a evidencia de produto.")
    parsed_unit = embedded_unit or explicit_unit
    if parsed_unit:
        unit_key = parsed_unit.casefold()
        if unit_key not in _UNIT_ALIASES:
            raise ContextHubValidationError("Unidade nao suportada para a evidencia de produto.")
        if match is None:
            raise ContextHubValidationError("Valor e unidade nao formam uma medida valida.")
        canonical_unit, factor = _UNIT_ALIASES[unit_key]
        normalized = _decimal_text(_parse_decimal(match.group(1)) * factor)
        return NormalizedEvidenceValue(normalized, normalized, canonical_unit)
    if match and isinstance(value, (int, float, Decimal)):
        normalized = _decimal_text(_parse_decimal(match.group(1)))
        return NormalizedEvidenceValue(normalized, normalized, "")
    return NormalizedEvidenceValue(text, _comparison_key(text), "")


def _parse_timestamp(value: Optional[object]) -> datetime:
    raw = str(value or _utc_now()).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextHubValidationError("Data invalida para a evidencia de produto.") from exc
    if parsed.tzinfo is None:
        raise ContextHubValidationError("Data da evidencia deve conter fuso horario.")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _fully_unquote(value: str) -> str:
    decoded = value
    for _attempt in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _canonicalize_url(value: object) -> tuple[str, str]:
    raw = _normalize_text(value, label="URL publica", maximum=2048)
    _reject_sensitive(raw, label="URL publica")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ContextHubValidationError("URL publica invalida.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ContextHubValidationError("Somente URL publica HTTP ou HTTPS e permitida.")
    if parsed.username or parsed.password:
        raise ContextHubValidationError("URL publica nao pode conter credenciais.")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".local"):
        raise ContextHubValidationError("Host privado e proibido na evidencia de produto.")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ContextHubValidationError("Endereco privado e proibido na evidencia de produto.")
    _reject_sensitive(_fully_unquote(parsed.path), label="URL publica")
    query_items = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered in _SENSITIVE_QUERY_KEYS or lowered.startswith(_SENSITIVE_QUERY_PREFIXES):
            raise ContextHubValidationError("URL publica contem parametro sensivel proibido.")
        if lowered in _TRACKING_QUERY_KEYS or lowered.startswith(_TRACKING_QUERY_PREFIXES):
            continue
        _reject_sensitive(_fully_unquote(item), label="URL publica")
        query_items.append((key, item))
    query_items.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    canonical = urlunsplit(
        (parsed.scheme.lower(), netloc, parsed.path or "/", urlencode(query_items, doseq=True), "")
    )
    return canonical, host


def _normalize_source_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in PRODUCT_EVIDENCE_SOURCE_TYPES:
        raise ContextHubValidationError("Tipo de fonte invalido para a evidencia de produto.")
    return normalized


def _registrable_domain(value: object) -> str:
    normalized = str(value or "").strip().casefold().rstrip(".")
    labels = [label for label in normalized.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    suffix = ".".join(labels[-2:])
    if suffix in _COMMON_TWO_LABEL_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _independence_origin(origin_key: object, domain: object) -> str:
    normalized = str(origin_key or "").strip().casefold().rstrip(".")
    if "." in normalized and re.fullmatch(r"[a-z0-9.-]+", normalized):
        return _registrable_domain(normalized)
    return normalized or _registrable_domain(domain)


def _source_fingerprint(source: Mapping[str, Any]) -> str:
    """Prefer copy equivalence and fall back to the exact content digest."""

    try:
        copy_fingerprint = source["copy_fingerprint"]
    except (KeyError, IndexError):
        copy_fingerprint = ""
    try:
        content_hash = source["content_hash"]
    except (KeyError, IndexError):
        content_hash = ""
    return str(copy_fingerprint or content_hash or "")


__all__ = [
    "NormalizedEvidenceValue",
    "PRODUCT_EVIDENCE_POLICY",
    "PRODUCT_EVIDENCE_SCOPES",
    "PRODUCT_EVIDENCE_SOURCE_TYPES",
    "normalize_product_evidence_value",
]
