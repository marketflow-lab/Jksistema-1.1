"""Contracts, privacy guards and aggregate budgets for deep product research."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from backend.services.vin_transient import (
    contains_vin_like_identifier,
    is_valid_vin,
    sanitize_vin_like_text,
)

PRODUCT_EVIDENCE_POLICY = "jk_product_evidence_v2"
PUBLIC_RESEARCH_POLICY = "jk_black_jhon_research_v2"

PUBLIC_RESEARCH_MAX_QUERIES = 12
PUBLIC_RESEARCH_MAX_PAGES = 50
PUBLIC_RESEARCH_MAX_DOMAINS = 10
PUBLIC_RESEARCH_MAX_PAGES_PER_DOMAIN = 5
PUBLIC_RESEARCH_MAX_READERS = 4
PUBLIC_RESEARCH_MAX_SECONDS = 300.0
PUBLIC_RESEARCH_INITIAL_MAX_QUERIES = 8
PUBLIC_RESEARCH_INITIAL_MAX_PAGES = 35
PUBLIC_RESEARCH_INITIAL_MAX_SECONDS = 225.0
PUBLIC_RESEARCH_GAP_MAX_QUERIES = 4
PUBLIC_RESEARCH_GAP_MAX_PAGES = 15
PUBLIC_RESEARCH_GAP_MAX_SECONDS = 75.0
PUBLIC_RESEARCH_MAX_PASSAGES = 32
PUBLIC_RESEARCH_MAX_PASSAGE_CHARS = 1200
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


def bounded_research_request_timeouts(
    deadline_monotonic: float | None, *, maximum: float = 40.0,
) -> tuple[float, float] | None:
    """Partition a request budget so connect plus read fits the phase deadline."""

    budget = maximum
    if deadline_monotonic is not None:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0.55:
            return None
        budget = min(maximum, remaining - 0.05)
    connect = min(5.0, max(0.25, budget * 0.20))
    return connect, max(0.25, budget - connect)


@contextmanager
def closing_research_response(
    response: object, *, deadline_monotonic: float | None = None,
):
    """Close responses normally and force-close a blocked stream at its deadline."""

    close = getattr(response, "close", None)

    def safe_close() -> None:
        if callable(close):
            try:
                close()
            except Exception:
                pass

    timer = None
    if callable(close) and deadline_monotonic is not None:
        delay = max(0.0, deadline_monotonic - time.monotonic())
        timer = threading.Timer(delay, safe_close)
        timer.daemon = True
        timer.start()
    try:
        yield response
    finally:
        if timer is not None:
            timer.cancel()
        safe_close()

from .deep_research_sanitization import (
    _COMMON_TWO_LABEL_PUBLIC_SUFFIXES,
    _CPF_CNPJ_RE,
    _CURATED_TECHNICAL_DISTRIBUTOR_DOMAINS,
    _CURATED_TECHNICAL_INDEPENDENT_DOMAINS,
    _DISTRIBUTOR_HOST_PARTS,
    _EMAIL_RE,
    _IDENTITY_STOPWORDS,
    _MISSING_FACT_VALUES,
    _OEM_LABEL_METADATA,
    _PHONE_BR_COMPACT_RE,
    _PHONE_INTL_RE,
    _PHONE_LABEL_RE,
    _PHONE_NANP_RE,
    _PHONE_RE,
    _PROTECTED_VIN_MARKER,
    _VIN_ANYWHERE_RE,
    _VIN_GROUPED_ANYWHERE_RE,
    _VIN_LABELLED_TAIL_RE,
    _label_metadata_code,
    _missing_fact_value,
    _percent_decode_fixed,
    _plain,
    _safe_text,
    safe_agent_product_research_evidence,
    safe_agent_research_metadata,
    safe_agent_research_metrics,
    sanitize_public_research_text,
    technical_assertion_occurrence_is_positive,
)


def safe_agent_research_passages(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project bounded technical excerpts; full page bodies remain process-local."""

    safe: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    allowed_types = {"code_context", "section", "table"}
    for raw in values:
        if len(safe) >= PUBLIC_RESEARCH_MAX_PASSAGES or not isinstance(raw, Mapping):
            break
        passage_type = re.sub(
            r"[^a-z_]", "", safe_agent_research_metadata(raw.get("passage_type"), 32).lower()
        )
        if passage_type not in allowed_types:
            continue
        text = re.sub(
            r"\s+",
            " ",
            sanitize_public_research_text(raw.get("text"), PUBLIC_RESEARCH_MAX_PASSAGE_CHARS),
        ).strip()[:PUBLIC_RESEARCH_MAX_PASSAGE_CHARS]
        if not text or contains_vin_like_identifier(text) or "_PROTEGIDO]" in text.upper():
            continue
        url = safe_agent_research_metadata(raw.get("url"), 800)
        if url and not canonical_research_url(url):
            url = ""
        section_ref = re.sub(
            r"\s+", " ", sanitize_public_research_text(raw.get("section_ref"), 180)
        ).strip()[:180]
        codes: list[str] = []
        for value in (raw.get("codes") or [])[:12]:
            code = re.sub(
                r"[^A-Za-z0-9./-]", "", sanitize_public_research_text(value, 48)
            ).strip("./-")[:48]
            if code and not contains_vin_like_identifier(code) and code not in codes:
                codes.append(code)
        signature = (passage_type, url, text.casefold())
        if signature in seen:
            continue
        seen.add(signature)
        safe.append({
            "passage_type": passage_type,
            "text": text,
            "section_ref": section_ref,
            "codes": codes,
            "url": url,
            "title": safe_agent_research_metadata(raw.get("title"), 240),
            "query_type": safe_agent_research_metadata(raw.get("query_type"), 80),
            "source_type": safe_agent_research_metadata(raw.get("source_type"), 40),
        })
    return safe


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


def read_limited_decompressed_response(
    response: object, *, deadline_monotonic: float | None = None,
) -> str:
    """Read incrementally within both the byte budget and total phase deadline."""

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
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return ""
    for chunk in chunks:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return ""
        if not chunk:
            continue
        raw = chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)
        remaining = PUBLIC_RESEARCH_MAX_DECOMPRESSED_BYTES - len(payload)
        if remaining <= 0:
            break
        payload.extend(raw[:remaining])
        if len(raw) >= remaining:
            break
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        return ""
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
        if is_valid_vin(match.group(1))
        or sum(character.isdigit() for character in match.group(1)) >= 2
    ]
    grouped_identifiers = [
        re.sub(r"[ -]+", "", match.group(1)).upper()
        for match in _VIN_GROUPED_ANYWHERE_RE.finditer(inspected)
        if len(re.sub(r"[ -]+", "", match.group(1))) == 17
        and (
            is_valid_vin(re.sub(r"[ -]+", "", match.group(1)))
            or sum(character.isdigit() for character in match.group(1)) >= 2
        )
    ]
    labelled_identifiers: list[str] = []
    for match in _VIN_LABELLED_TAIL_RE.finditer(inspected):
        chunks = re.findall(r"[A-Z0-9]+", match.group(1), flags=re.IGNORECASE)
        for start in range(len(chunks)):
            joined = ""
            for chunk in chunks[start : start + 24]:
                joined += chunk
                if len(joined) == 17 and (
                    is_valid_vin(joined)
                    or sum(character.isdigit() for character in joined) >= 2
                ):
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
    query_phase_counts: dict[str, int] = field(default_factory=dict)
    page_phase_counts: dict[str, int] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def deadline_monotonic(self) -> float:
        return self.started_monotonic + PUBLIC_RESEARCH_MAX_SECONDS

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    @staticmethod
    def _budget_phase(value: object) -> str:
        return "gap" if str(value or "").strip().lower() == "gap" else "initial"

    def phase_deadline_monotonic(self, phase: object) -> float:
        bucket = self._budget_phase(phase)
        if bucket == "gap":
            return min(
                self.deadline_monotonic,
                time.monotonic() + PUBLIC_RESEARCH_GAP_MAX_SECONDS,
            )
        return min(
            self.deadline_monotonic,
            self.started_monotonic + PUBLIC_RESEARCH_INITIAL_MAX_SECONDS,
        )

    def reserve_queries(self, queries: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        accepted: list[dict[str, str]] = []
        with self.lock:
            if self.remaining_seconds() <= 0:
                return []
            for query in queries:
                text = _safe_public_query(query.get("query"))
                kind = _safe_text(query.get("type") or "web", 80)
                requested_phase = _safe_text(query.get("research_phase") or "initial", 16).lower()
                budget_phase = self._budget_phase(requested_phase)
                semantic_phase = (
                    requested_phase
                    if requested_phase in {"plan", "gap", "fallback", "identity", "initial"}
                    else budget_phase
                )
                normalized = _plain(text)
                if not normalized or normalized in self.queries_used:
                    continue
                if len(self.queries_used) >= PUBLIC_RESEARCH_MAX_QUERIES:
                    break
                phase_limit = (
                    PUBLIC_RESEARCH_GAP_MAX_QUERIES
                    if budget_phase == "gap"
                    else PUBLIC_RESEARCH_INITIAL_MAX_QUERIES
                )
                if self.query_phase_counts.get(budget_phase, 0) >= phase_limit:
                    continue
                self.queries_used.add(normalized)
                self.query_phase_counts[budget_phase] = self.query_phase_counts.get(budget_phase, 0) + 1
                accepted.append({
                    "query": text,
                    "type": kind,
                    "research_phase": semantic_phase,
                })
        return accepted

    def reserve_page(self, url: object, *, research_phase: object = "initial") -> bool:
        host = _registrable_domain(_domain(url))
        canonical = canonical_research_url(url)
        budget_phase = self._budget_phase(research_phase)
        with self.lock:
            phase_limit = (
                PUBLIC_RESEARCH_GAP_MAX_PAGES
                if budget_phase == "gap"
                else PUBLIC_RESEARCH_INITIAL_MAX_PAGES
            )
            if (
                self.remaining_seconds() <= 0
                or not host
                or not canonical
                or canonical in self.page_urls
                or self.pages_used >= PUBLIC_RESEARCH_MAX_PAGES
                or self.page_phase_counts.get(budget_phase, 0) >= phase_limit
            ):
                return False
            if host not in self.domains and len(self.domains) >= PUBLIC_RESEARCH_MAX_DOMAINS:
                return False
            if self.domains.get(host, 0) >= PUBLIC_RESEARCH_MAX_PAGES_PER_DOMAIN:
                return False
            self.pages_used += 1
            self.domains[host] = self.domains.get(host, 0) + 1
            self.page_urls.add(canonical)
            self.page_phase_counts[budget_phase] = self.page_phase_counts.get(budget_phase, 0) + 1
            return True


def research_session(agent_input: Mapping[str, Any]) -> ResearchSessionV1:
    """Return a tenant/store-isolated process-local aggregate research budget."""

    now = time.monotonic()
    job_id = _safe_text(agent_input.get("_codex_job_id"), 128)
    transient_key = re.sub(
        r"[^A-Za-z0-9_.-]", "", _safe_text(agent_input.get("_research_session_key"), 128)
    )[:128]
    identity = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    question = agent_input.get("question") if isinstance(agent_input.get("question"), Mapping) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    stable_question_key = "\x1f".join(
        _safe_text(value, 160)
        for value in (question.get("id"), question.get("item_id"), item.get("id"))
        if _safe_text(value, 160)
    )
    if job_id or transient_key or stable_question_key:
        key_material = "\x1f".join(
            _safe_text(value, 160)
            for value in (
                agent_input.get("tenant_id"),
                agent_input.get("store"),
                identity.get("seller_id"),
                identity.get("site_id"),
                job_id or transient_key or stable_question_key,
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
                        session.query_phase_counts["initial"] = (
                            session.query_phase_counts.get("initial", 0) + 1
                        )
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
class ResearchPassageV1:
    """Ephemeral bounded excerpt centered on a technical code or document structure."""

    passage_type: str
    text: str
    section_ref: str = ""
    codes: tuple[str, ...] = ()


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
    # Safe pages may be retained as source-only leads even when the advertised
    # product identity could not be proven.  Such leads must never contribute
    # claims, coverage or public facts.
    claim_eligible: bool = True
    passages: tuple[ResearchPassageV1, ...] = ()
    # Set only by the constructors after exact-product structural scoping. It
    # prevents a second scoping pass from discarding directed relations while
    # keeping ad-hoc/manual ResearchDocumentV1 instances fail-closed.
    claim_text_scoped: bool = False
