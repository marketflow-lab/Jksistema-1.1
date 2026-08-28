"""Contracts, privacy guards and aggregate budgets for deep product research."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

PRODUCT_EVIDENCE_POLICY = "jk_product_evidence_v1"
PUBLIC_RESEARCH_POLICY = "jk_public_product_research_v1"

PUBLIC_RESEARCH_MAX_QUERIES = 12
PUBLIC_RESEARCH_MAX_PAGES = 50
PUBLIC_RESEARCH_MAX_DOMAINS = 10
PUBLIC_RESEARCH_MAX_PAGES_PER_DOMAIN = 5
PUBLIC_RESEARCH_MAX_READERS = 4
PUBLIC_RESEARCH_MAX_SECONDS = 300.0
PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES = 600_000
PUBLIC_RESEARCH_STREAM_CHUNK_BYTES = 16 * 1024

_SESSION_TTL_SECONDS = 10 * 60.0
_SESSION_LOCK = threading.RLock()
_SESSIONS: dict[str, "ResearchSessionV1"] = {}

_MARKETPLACE_HOSTS = (
    "mercadolivre.",
    "mercadolibre.",
    "amazon.",
    "shopee.",
    "aliexpress.",
    "magazineluiza.",
    "ebay.",
)
_FORUM_HOST_PARTS = ("forum.", "forums.", "reddit.", "club.", "comunidade.")
_BLOG_PATH_PARTS = ("/blog/", "/noticias/", "/news/")
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
            if sum(character.isdigit() for character in candidate) >= 2
            else candidate
        )

    def replace_grouped(match: re.Match[str]) -> str:
        candidate = match.group(1)
        compact = re.sub(r"[ -]+", "", candidate)
        return (
            _PROTECTED_VIN_MARKER
            if len(compact) == 17
            and sum(character.isdigit() for character in compact) >= 2
            else candidate
        )

    sanitized = _VIN_ANYWHERE_RE.sub(replace_compact, source)
    sanitized = _VIN_GROUPED_ANYWHERE_RE.sub(replace_grouped, sanitized)
    sanitized = _EMAIL_RE.sub("[EMAIL_PROTEGIDO]", sanitized)
    sanitized = _CPF_CNPJ_RE.sub("[DOCUMENTO_PROTEGIDO]", sanitized)
    sanitized = _PHONE_LABEL_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_INTL_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_NANP_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    sanitized = _PHONE_BR_COMPACT_RE.sub("[TELEFONE_PROTEGIDO]", sanitized)
    return sanitized[: max(0, int(maximum))]


def sanitize_public_research_item(value: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(value)
    for field, maximum in (("title", 600), ("snippet", 4000), ("description", 4000)):
        if field in safe:
            safe[field] = sanitize_public_research_text(safe.get(field), maximum)
    return safe


def _safe_page_text(value: object, maximum: int = 600_000) -> str:
    source = unicodedata.normalize(
        "NFKC",
        sanitize_public_research_text(value, maximum),
    )
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", source)
    return source.strip()[:maximum]


def read_limited_decompressed_response(response: object) -> str:
    """Read a requests response incrementally within the public byte budget."""

    iter_content = getattr(response, "iter_content", None)
    if callable(iter_content):
        chunks = iter_content(
            chunk_size=PUBLIC_RESEARCH_STREAM_CHUNK_BYTES,
            decode_unicode=False,
        )
    else:
        # Small in-process doubles may expose only text; requests.Response always
        # takes the streaming branch above.
        chunks = [str(getattr(response, "text", "") or "").encode("utf-8")]
    payload = bytearray()
    for chunk in chunks:
        if not chunk:
            continue
        raw = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        remaining = PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES - len(payload)
        if remaining <= 0:
            break
        payload.extend(raw[:remaining])
        if len(raw) >= remaining:
            break
    encoding = str(getattr(response, "encoding", "") or "utf-8")
    try:
        return bytes(payload).decode(encoding, errors="replace")
    except LookupError:
        return bytes(payload).decode("utf-8", errors="replace")


def _safe_public_query(value: object) -> str:
    raw = str(value or "")
    if len(raw) > 4096:
        return ""
    inspected, decoding_complete = _percent_decode_fixed(raw)
    query = _safe_text(inspected, 260)
    compact_identifiers = [
        match.group(1).upper()
        for match in _VIN_ANYWHERE_RE.finditer(inspected)
        if sum(character.isdigit() for character in match.group(1)) >= 2
    ]
    grouped_identifiers = [
        re.sub(r"[ -]+", "", match.group(1)).upper()
        for match in _VIN_GROUPED_ANYWHERE_RE.finditer(inspected)
        if len(re.sub(r"[ -]+", "", match.group(1))) == 17
        and sum(character.isdigit() for character in match.group(1)) >= 2
    ]
    labelled_identifiers: list[str] = []
    for match in _VIN_LABELLED_TAIL_RE.finditer(inspected):
        chunks = re.findall(r"[A-Z0-9]+", match.group(1), flags=re.IGNORECASE)
        for start in range(len(chunks)):
            joined = ""
            for chunk in chunks[start : start + 24]:
                joined += chunk
                if len(joined) == 17 and sum(character.isdigit() for character in joined) >= 2:
                    labelled_identifiers.append(joined.upper())
                    break
                if len(joined) > 17:
                    break
    if (
        not query
        or not decoding_complete
        or re.search(r"\b(?:chassi|chassis|vin)\b", inspected, flags=re.IGNORECASE)
        or compact_identifiers
        or grouped_identifiers
        or labelled_identifiers
        or _EMAIL_RE.search(inspected)
        or _CPF_CNPJ_RE.search(inspected)
        or _PHONE_LABEL_RE.search(inspected)
        or _PHONE_INTL_RE.search(inspected)
        or _PHONE_NANP_RE.search(inspected)
        or _PHONE_RE.search(inspected)
        or _PHONE_BR_COMPACT_RE.search(inspected)
        or "[CHASSI_PROTEGIDO]" in inspected.upper()
    ):
        return ""
    return query


def _domain(url: object) -> str:
    try:
        return str(urlsplit(str(url or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _registrable_domain(host: object) -> str:
    labels = [label for label in str(host or "").casefold().strip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    compound_suffix = ".".join(labels[-2:])
    if compound_suffix in _COMMON_TWO_LABEL_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def canonical_research_url(url: object) -> str:
    """Return a fragment-free public URL while preserving functional parameters."""

    raw_url = str(url or "").strip()
    if not raw_url or len(raw_url) > 2048:
        return ""
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError:
        return ""
    host = str(parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return ""
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    if port is not None and not default_port:
        return ""
    if not _safe_public_query(host):
        return ""
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    decoded_path, path_decoding_complete = _percent_decode_fixed(parsed.path)
    if not path_decoding_complete:
        return ""
    if decoded_path and not _safe_public_query(decoded_path):
        return ""
    decoded_fragment, fragment_decoding_complete = _percent_decode_fixed(parsed.fragment)
    if not fragment_decoding_complete:
        return ""
    if decoded_fragment and not _safe_public_query(decoded_fragment):
        return ""
    safe_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in {"fbclid", "gclid", "msclkid"}:
            continue
        if lowered in {
            "token",
            "access_token",
            "api_key",
            "key",
            "auth",
            "authorization",
            "secret",
            "signature",
            "sig",
            "session",
        } or lowered.startswith(("x-amz-", "x-goog-")):
            return ""
        if lowered in {"vin", "chassi", "chassis", "cpf", "cnpj", "email", "phone", "telefone"}:
            return ""
        decoded_key, key_decoding_complete = _percent_decode_fixed(key)
        if not key_decoding_complete:
            return ""
        if not _safe_public_query(decoded_key):
            return ""
        decoded_value, value_decoding_complete = _percent_decode_fixed(value)
        if not value_decoding_complete:
            return ""
        if re.search(r"(?i)(?:https?:)?//", decoded_value):
            # A server-side reader could otherwise follow a nested redirect
            # before the application can validate the destination hop.
            return ""
        if not _safe_public_query(decoded_value) and decoded_value:
            return ""
        safe_query.append((key, value))
    safe_query.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path or "/",
            urlencode(safe_query, doseq=True),
            "",
        )
    )


@dataclass
class ResearchSessionV1:
    key: str
    started_monotonic: float
    expires_monotonic: float
    queries_used: set[str] = field(default_factory=set)
    pages_used: int = 0
    domains: dict[str, int] = field(default_factory=dict)
    page_urls: set[str] = field(default_factory=set)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def deadline_monotonic(self) -> float:
        return self.started_monotonic + PUBLIC_RESEARCH_MAX_SECONDS

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def reserve_queries(self, queries: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        accepted: list[dict[str, str]] = []
        with self.lock:
            if self.remaining_seconds() <= 0:
                return []
            for query in queries:
                text = _safe_public_query(query.get("query"))
                kind = _safe_text(query.get("type") or "web", 80)
                normalized = _plain(text)
                if not normalized or normalized in self.queries_used:
                    continue
                if len(self.queries_used) >= PUBLIC_RESEARCH_MAX_QUERIES:
                    break
                self.queries_used.add(normalized)
                accepted.append({"query": text, "type": kind})
        return accepted

    def reserve_page(self, url: object) -> bool:
        host = _registrable_domain(_domain(url))
        canonical = canonical_research_url(url)
        with self.lock:
            if (
                self.remaining_seconds() <= 0
                or not host
                or not canonical
                or canonical in self.page_urls
                or self.pages_used >= PUBLIC_RESEARCH_MAX_PAGES
            ):
                return False
            if host not in self.domains and len(self.domains) >= PUBLIC_RESEARCH_MAX_DOMAINS:
                return False
            if self.domains.get(host, 0) >= PUBLIC_RESEARCH_MAX_PAGES_PER_DOMAIN:
                return False
            self.pages_used += 1
            self.domains[host] = self.domains.get(host, 0) + 1
            self.page_urls.add(canonical)
            return True


def research_session(agent_input: Mapping[str, Any]) -> ResearchSessionV1:
    """Return a tenant/store-isolated process-local aggregate research budget."""

    now = time.monotonic()
    job_id = _safe_text(agent_input.get("_codex_job_id"), 128)
    identity = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    if job_id:
        key_material = "\x1f".join(
            _safe_text(value, 160)
            for value in (
                agent_input.get("tenant_id"),
                agent_input.get("store"),
                identity.get("seller_id"),
                identity.get("site_id"),
                job_id,
            )
        )
        key = hashlib.sha256(key_material.encode("utf-8", errors="ignore")).hexdigest()
    else:
        key = f"local-{threading.get_ident()}-{time.monotonic_ns()}"
    with _SESSION_LOCK:
        expired = [name for name, item in _SESSIONS.items() if item.expires_monotonic <= now]
        for name in expired:
            _SESSIONS.pop(name, None)
        session = _SESSIONS.get(key)
        if session is None:
            session = ResearchSessionV1(
                key=key,
                started_monotonic=now,
                expires_monotonic=now + _SESSION_TTL_SECONDS,
            )
            for prior in agent_input.get("research_history") or []:
                if not isinstance(prior, Mapping):
                    continue
                for query in prior.get("queries") or []:
                    normalized = _plain(_safe_public_query(query))
                    if normalized:
                        session.queries_used.add(normalized)
            _SESSIONS[key] = session
        else:
            session.expires_monotonic = now + _SESSION_TTL_SECONDS
        return session


@dataclass(frozen=True)
class TechnicalClaimV1:
    field_name: str
    scope: str
    value: str
    unit: str = ""

    @property
    def signature(self) -> tuple[str, str, str, str]:
        return (self.field_name, self.scope, _plain(self.value), self.unit.casefold())


@dataclass(frozen=True)
class ResearchDocumentV1:
    url: str
    title: str
    query_type: str
    query: str
    text: str
    content_hash: str
    source_type: str
    origin_key: str
    copy_fingerprint: str = ""
