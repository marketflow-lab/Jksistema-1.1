"""Bounded public-web crawler used by Mercado Livre question research."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from .deep_research import (
    PUBLIC_RESEARCH_MAX_PAGES,
    PUBLIC_RESEARCH_MAX_READERS,
    ResearchDocumentV1,
    apparent_coverage_complete,
    canonical_research_url,
    extract_technical_claims,
    make_research_document,
    persist_research_documents,
    research_session,
)
from .queries import _ia_agent_perguntas_relaxar_query_web
from .runtime import _normalizar_texto
from .deep_research_contracts import (
    sanitize_public_research_text,
    technical_assertion_occurrence_is_positive,
)
from .deep_research_fingerprints import research_document_rank
from .official_source_registry import is_reviewed_official_domain, official_domains_for_target_identity


ResearchEntry = tuple[dict, str, str, str]
ResearchGroup = tuple[str, str, list, list[dict]]

_TARGET_SOURCE_AUTHORITIES = {
    "official_manufacturer", "official_oem", "technical_distributor", "technical_independent",
}
_TARGET_OFFICIAL_AUTHORITIES = {"official_manufacturer", "official_oem"}
_TARGET_TECHNICAL_AUTHORITIES = {"technical_distributor", "technical_independent"}
_COMPATIBILITY_CRITICAL_QUERY_TYPES = {
    "target_interface_official", "target_interface_technical", "product_interface_technical",
    "interface_equivalence",
}


@dataclass(frozen=True)
class DeepResearchCallbacks:
    """Source-layer operations kept injectable to preserve the legacy facade."""

    reserve_queries: Callable[..., tuple[object, list[dict[str, str]], dict[str, list[str]]]]
    prefetch_web: Callable[..., dict[str, list[dict[str, Any]]]]
    select_items: Callable[[dict, dict, set[str]], tuple[str, str, list]]
    read_batch: Callable[..., tuple[dict[str, str], dict[str, int], bool]]
    discover_links: Callable[[str, str], list[str]]
    render_results: Callable[[list, str, str, dict], list[str]]
    render_listings: Callable[[list[dict]], list[str]]


def reserve_research_queries(
    agent_input: dict,
    queries: list[dict],
) -> tuple[object, list[dict[str, str]], dict[str, list[str]]]:
    """Reserve every actual provider query, including relaxed variants."""

    session = research_session(agent_input)
    expanded: list[dict[str, str]] = []
    variants_by_original: dict[str, list[str]] = {}
    for query in queries[:12]:
        if not isinstance(query, dict):
            continue
        original = str(query.get("query") or "").strip()[:260]
        query_type = str(query.get("type") or "web").strip()[:80]
        if not original:
            continue
        variants = [original]
        relaxed = _ia_agent_perguntas_relaxar_query_web(original)
        if relaxed and _normalizar_texto(relaxed) != _normalizar_texto(original):
            variants.append(relaxed[:260])
        variants_by_original[original] = variants
        expanded.extend({"query": value, "type": query_type} for value in variants)
    accepted = session.reserve_queries(expanded)
    accepted_texts = {str(value.get("query") or "") for value in accepted}
    variants_by_original = {
        original: [variant for variant in variants if variant in accepted_texts]
        for original, variants in variants_by_original.items()
        if any(variant in accepted_texts for variant in variants)
    }
    return session, accepted, variants_by_original


def read_deep_batch(
    entries: list[ResearchEntry],
    *,
    deadline_monotonic: float,
    reader: Callable[..., str],
) -> tuple[dict[str, str], dict[str, int], bool]:
    """Read at most four pages concurrently and abandon late results."""

    if not entries:
        return {}, {
            "pages_attempted": 0,
            "pages_read": 0,
            "read_failures": 0,
            "read_timeouts": 0,
            "retry_successes": 0,
        }, False
    queue: Queue = Queue()
    lock = Lock()
    results: dict[str, str] = {}
    metrics = {
        "pages_attempted": 0,
        "pages_read": 0,
        "read_failures": 0,
        "read_timeouts": 0,
        "retry_successes": 0,
    }
    for entry in entries:
        queue.put_nowait(entry)

    def worker() -> None:
        while time.monotonic() < deadline_monotonic:
            try:
                _item, url, query, _query_type = queue.get_nowait()
            except Empty:
                return
            diagnostics: dict[str, Any] = {}
            with lock:
                metrics["pages_attempted"] += 1
            try:
                content = reader(
                    url,
                    query,
                    deep=True,
                    deadline_monotonic=deadline_monotonic,
                    diagnostics=diagnostics,
                )
            except Exception:
                content = ""
            with lock:
                key = canonical_research_url(url) or str(url or "").lower()
                rendered = str(content or "")
                if rendered:
                    results[key] = rendered
                    metrics["pages_read"] += 1
                else:
                    metrics["read_failures"] += 1
                if diagnostics.get("read_timeout") is True:
                    metrics["read_timeouts"] += 1
                if diagnostics.get("retry_success") is True:
                    metrics["retry_successes"] += 1
            queue.task_done()

    workers: list[Thread] = []
    for index in range(min(PUBLIC_RESEARCH_MAX_READERS, len(entries))):
        thread = Thread(
            target=worker,
            name=f"ml-questions-deep-read-{index + 1}",
            daemon=True,
        )
        thread.start()
        workers.append(thread)
    for thread in workers:
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        if remaining <= 0:
            break
        thread.join(timeout=remaining)
    deadline_exhausted = any(thread.is_alive() for thread in workers) or not queue.empty()
    return results, metrics, deadline_exhausted


def discover_same_domain_technical_links(
    page_url: str,
    page_text: str,
    *,
    sanitize_url: Callable[[str], str],
) -> list[str]:
    """Discover one same-controlled-host hop to technical documents."""

    source = str(page_text or "")[:600_000]
    if not source:
        return []
    candidates = re.findall(r"\[[^\]]{0,200}\]\(([^)\s]+)\)", source)
    candidates.extend(re.findall(r"https?://[^\s<>()\]]+", source, flags=re.IGNORECASE))
    source_host = str(urlparse(page_url).hostname or "").lower().strip(".")
    source_owner = source_host[4:] if source_host.startswith("www.") else source_host
    technical_markers = (
        "manual", "catalog", "catalogue", "datasheet", "data-sheet",
        "ficha-tecnica", "technical", "specification", "service",
        "document", "download", ".pdf",
    )
    links: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = urljoin(page_url, str(candidate or "").strip(" '\"<>.,"))
        safe = sanitize_url(resolved)
        if not safe or safe == page_url:
            continue
        parsed = urlparse(safe)
        candidate_host = str(parsed.hostname or "").lower().strip(".")
        candidate_owner = candidate_host[4:] if candidate_host.startswith("www.") else candidate_host
        same_controlled_host = (
            bool(source_owner)
            and bool(candidate_owner)
            and (
                candidate_owner == source_owner
                or candidate_owner.endswith(f".{source_owner}")
                or source_owner.endswith(f".{candidate_owner}")
            )
        )
        if not same_controlled_host:
            continue
        coordinate = f"{parsed.path}?{parsed.query}".lower()
        if not any(marker in coordinate for marker in technical_markers):
            continue
        canonical = canonical_research_url(safe)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        links.append(canonical)
        if len(links) >= 12:
            break
    return links


def _collect_groups(
    queries: list[dict],
    variants_by_original: dict[str, list[str]],
    prefetch: dict,
    select_items: Callable[[dict, dict, set[str]], tuple[str, str, list]],
) -> tuple[list[ResearchGroup], set[str]]:
    groups: list[ResearchGroup] = []
    discovered_urls: set[str] = set()
    seen_urls: set[str] = set()
    for query in queries[:12]:
        if not isinstance(query, dict):
            continue
        original = str(query.get("query") or "").strip()[:260]
        if not original or original not in variants_by_original:
            continue
        used_query, query_type, items = select_items(query, prefetch, seen_urls)
        for _item, url in items:
            discovered_urls.add(canonical_research_url(url) or str(url or "").lower())
        groups.append((used_query or original, query_type, items, []))
    return groups, discovered_urls


def _select_pages(
    session: object,
    items: list,
    query: str,
    query_type: str,
    *,
    page_count: int,
    page_limit: int,
) -> tuple[list[ResearchEntry], int]:
    selected: list[ResearchEntry] = []
    for item, url in items:
        if page_count >= page_limit:
            break
        if not session.reserve_page(url):
            continue
        selected.append((item, url, query, query_type))
        page_count += 1
    return selected, page_count


def _select_linked_pages(
    session: object,
    selected: list[ResearchEntry],
    readings: dict[str, str],
    discover_links: Callable[[str, str], list[str]],
    discovered_urls: set[str],
    *,
    page_count: int,
    page_limit: int,
) -> tuple[list[ResearchEntry], int]:
    linked: list[ResearchEntry] = []
    for item, url, query, query_type in selected:
        key = canonical_research_url(url) or str(url or "").lower()
        for linked_url in discover_links(url, str(readings.get(key) or "")):
            discovered_urls.add(linked_url)
            if page_count >= page_limit or not session.reserve_page(linked_url):
                continue
            linked.append((item, linked_url, query, f"{query_type}_linked_technical"))
            page_count += 1
    return linked, page_count


def _append_documents(
    entries: list[ResearchEntry],
    readings: dict[str, str],
    agent_input: dict,
    documents: list[ResearchDocumentV1],
    document_hashes: set[str],
    fact_signatures: set[tuple[str, str, str, str]],
) -> int:
    new_facts = 0
    for item, url, query, query_type in entries:
        key = canonical_research_url(url) or str(url or "").lower()
        content = str(readings.get(key) or "")
        if not content:
            continue
        document = make_research_document(
            item=item,
            url=url,
            query=query,
            query_type=query_type,
            text=content,
            agent_input=agent_input,
        )
        if document is None:
            continue
        duplicate_index = next(
            (
                index for index, current in enumerate(documents)
                if current.content_hash == document.content_hash
            ),
            None,
        )
        if duplicate_index is not None:
            current = documents[duplicate_index]
            if research_document_rank(document) <= research_document_rank(current):
                continue
            documents[duplicate_index] = document
        else:
            document_hashes.add(document.content_hash)
            documents.append(document)
        if str(document.query_type or "").startswith("target_"):
            target_identity = _target_identity_from_input(agent_input)
            target_pattern = _target_identity_pattern(target_identity)
            target_text = ""
            if target_identity and target_pattern is not None and _target_identity_is_specific(target_identity):
                target_text = _target_scoped_text(document, target_pattern)
            target_authority = _target_source_authority(document, target_identity) if target_text else ""
            if target_authority in _TARGET_SOURCE_AUTHORITIES:
                for field_name, value, unit in _target_interface_claims(target_text):
                    signature = (
                        "target:" + _normalizar_texto(target_identity).casefold(),
                        field_name,
                        value.casefold(),
                        unit.casefold(),
                    )
                    if signature not in fact_signatures:
                        fact_signatures.add(signature)
                        new_facts += 1
        if not bool(getattr(document, "claim_eligible", True)):
            continue
        for claim in extract_technical_claims(document.text):
            if claim.signature not in fact_signatures:
                fact_signatures.add(claim.signature)
                new_facts += 1
    return new_facts


def _target_identity_from_input(agent_input: dict) -> str:
    intent = agent_input.get("intent") if isinstance(agent_input.get("intent"), dict) else {}
    compatibility = (
        intent.get("compatibilidade")
        if isinstance(intent.get("compatibilidade"), dict)
        else {}
    )
    value = compatibility.get("target_item") or compatibility.get("target_vehicle") or ""
    safe = sanitize_public_research_text(value, 300).strip()
    return "" if "PROTEGIDO]" in safe else safe


def _target_identity_pattern(target_identity: str) -> re.Pattern | None:
    normalized = _normalizar_texto(target_identity).casefold()
    parts = re.findall(r"[a-z]+|\d+", normalized)
    if len("".join(parts)) < 4:
        return None
    separator = r"[\s._/\-]*"
    return re.compile(
        r"(?<![a-z0-9])" + separator.join(re.escape(part) for part in parts) + r"(?![a-z0-9])",
        flags=re.IGNORECASE,
    )


def _target_identity_is_specific(target_identity: str) -> bool:
    """Reject brand/model-only targets before they can activate interface facts."""

    normalized = _normalizar_texto(target_identity).casefold()
    tokens = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", normalized)
    numeric = [token for token in tokens if re.fullmatch(r"\d+(?:[.,]\d+)?", token)]
    non_year = [
        token for token in numeric
        if not (token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2099)
    ]
    if not non_year:
        return False
    if re.search(r"\b(?=[a-z0-9-]{4,}\b)(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*\b", normalized):
        return True
    if len(non_year) >= 2 or any(len(re.sub(r"\D", "", token)) >= 5 for token in non_year):
        return True
    for index, token in enumerate(tokens[:-1]):
        if token.isalpha() and 1 < len(token) <= 4 and tokens[index + 1] in non_year:
            return True
    return bool(re.search(r"\b\d+[.,]\d+\b", normalized))


def _target_scoped_text(
    document: ResearchDocumentV1, pattern: re.Pattern,
) -> str:
    matches = lambda value: bool(pattern.search(_normalizar_texto(value).casefold()))
    if not matches(document.text):
        return ""
    lines = document.text.splitlines() or [document.text]
    interface_line = re.compile(
        r"\b(?:bcd|pcd|bolt|furacao|fixacao|montagem|mounting|simetr|assimetr|symmetr|asymmetr|"
        r"bracos?|arms?|furos?|holes?|parafusos?|conector|connector)\b",
        flags=re.IGNORECASE,
    )
    model_code = re.compile(
        r"\b(?=[A-Z0-9-]{4,30}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d{3})"
        r"[A-Z0-9]+(?:-[A-Z0-9]+)*\b",
        flags=re.IGNORECASE,
    )
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if not matches(line):
            continue
        target_heading = re.match(r"^\s*(#{1,6})\s+", line)
        selected = [line.strip()]
        for candidate in lines[index + 1 : index + 13]:
            normalized = _normalizar_texto(candidate)
            heading = re.match(r"^\s*(#{1,6})\s+", candidate)
            if re.match(r"^\s*(?:-{3,}|={3,})\s*$", candidate):
                break
            if model_code.search(normalized) and not matches(candidate):
                break
            if heading and target_heading and len(heading.group(1)) <= len(target_heading.group(1)):
                break
            if interface_line.search(normalized):
                selected.append(candidate.strip())
        blocks.append("\n".join(value for value in selected if value))
    return "\n".join(dict.fromkeys(value for value in blocks if value))[:12_000]


def _target_source_authority(
    document: ResearchDocumentV1, target_identity: str,
) -> str:
    authority = str(document.source_type or "").strip().lower()
    host = str(urlparse(document.url).hostname or "").strip(".").lower()
    target_roots = official_domains_for_target_identity(target_identity)
    if any(host == root or host.endswith("." + root) for root in target_roots):
        return authority if authority in _TARGET_OFFICIAL_AUTHORITIES else "official_manufacturer"
    if authority in _TARGET_OFFICIAL_AUTHORITIES or is_reviewed_official_domain(host):
        return "technical_independent"
    if authority in _TARGET_TECHNICAL_AUTHORITIES:
        return authority
    return ""


def _target_interface_claims(text: str) -> list[tuple[str, str, str]]:
    normalized = _normalizar_texto(text).casefold()
    claims: set[tuple[str, str, str]] = set()
    symmetry_values: set[str] = set()
    for value, pattern in (
        ("asymmetric", re.compile(r"\b(?:assimetr(?:ico|ica)|asymmetr(?:ic|ical))\b")),
        ("symmetric", re.compile(r"\b(?:simetr(?:ico|ica)|symmetr(?:ic|ical))\b")),
    ):
        if any(
            technical_assertion_occurrence_is_positive(
                normalized, match.start(), match.end(),
            )
            for match in pattern.finditer(normalized)
        ):
            symmetry_values.add(value)
    if len(symmetry_values) == 1:
        claims.add(("interface.symmetry", next(iter(symmetry_values)), ""))
    bolt_pattern = re.compile(
        r"\b(?:bcd|pcd)(?:\s*/\s*(?:bcd|pcd))?\b\s*[:=\-]?\s*"
        r"(?:\d{1,2}\s*[x×]\s*)?(\d{2,4}(?:[.,]\d+)?(?:\s*/\s*\d{2,4}(?:[.,]\d+)?)?)\s*mm\b",
        flags=re.IGNORECASE,
    )
    for match in bolt_pattern.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        value = re.sub(r"\s+", "", match.group(1)).replace(",", ".")
        claims.add(("interface.bolt_pattern", value, "mm"))
    geometry = re.compile(
        r"\b(\d{1,2})\s*(bracos?|arms?|furos?|holes?|parafusos?|bolts?)\b",
        flags=re.IGNORECASE,
    )
    for match in geometry.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        kind = "arm" if match.group(2).startswith(("braco", "arm")) else "hole"
        claims.add(("interface.fixation_geometry", f"{int(match.group(1))}-{kind}", ""))
    connector = re.compile(
        r"\b(?:tipo\s+de\s+conector|connector\s+type|conector|connector)\s*[:=\-]\s*"
        r"([a-z0-9][a-z0-9+._ /\-]{1,40})",
        flags=re.IGNORECASE,
    )
    for match in connector.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        value = re.split(r"[;|,.]", match.group(1), maxsplit=1)[0].strip()
        if value:
            claims.add(("interface.connector_type", value[:40], ""))
    return sorted(claims)


def _target_claim_copy_fingerprint(
    text: str,
    pattern: re.Pattern,
    target_identity: str,
    claim: tuple[str, str, str],
) -> str:
    """Fingerprint only the assertion that supports one target claim."""

    assertions: set[str] = set()
    for segment in re.split(r"[\r\n|]+|(?<=[.!?;])\s+", str(text or "")):
        if claim not in _target_interface_claims(segment):
            continue
        normalized = re.sub(
            r"\s+", " ", _normalizar_texto(re.sub(r"https?://\S+", " ", segment)).casefold(),
        ).strip(" .,:;|-")
        identity_match = pattern.search(normalized)
        if identity_match:
            normalized = normalized[identity_match.start():]
        else:
            anchors = {
                "interface.symmetry": r"\b(?:fixacao|montagem|simetr|assimetr|symmetr|asymmetr)",
                "interface.bolt_pattern": r"\b(?:bcd|pcd|bolt|furacao)",
                "interface.fixation_geometry": r"\b(?:fixacao|montagem|furacao|\d+\s*(?:bracos?|arms?|furos?|holes?))",
                "interface.connector_type": r"\b(?:tipo\s+de\s+conector|connector\s+type|conector|connector)",
            }
            anchor_match = re.search(anchors.get(claim[0], r"$^"), normalized)
            if anchor_match:
                normalized = normalized[anchor_match.start():]
        if normalized:
            assertions.add(normalized)
    payload = "\n".join([
        "target-claim-copy-v1",
        _normalizar_texto(target_identity).casefold(),
        "|".join(claim),
        *sorted(assertions),
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _verified_target_evidence(
    agent_input: dict, documents: list[ResearchDocumentV1],
) -> list[dict[str, Any]]:
    target_identity = _target_identity_from_input(agent_input)
    pattern = _target_identity_pattern(target_identity)
    if not target_identity or pattern is None or not _target_identity_is_specific(target_identity):
        return []
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for document in documents:
        if not str(document.query_type or "").startswith("target_"):
            continue
        scoped_text = _target_scoped_text(document, pattern)
        authority = _target_source_authority(document, target_identity) if scoped_text else ""
        if authority not in _TARGET_SOURCE_AUTHORITIES:
            continue
        for claim in _target_interface_claims(scoped_text):
            source = {
                "authority": authority,
                "url": document.url,
                "origin_key": document.origin_key,
                "copy_fingerprint": _target_claim_copy_fingerprint(
                    scoped_text, pattern, target_identity, claim,
                ),
            }
            values = grouped.setdefault(claim, [])
            if not any(value["url"] == source["url"] for value in values):
                values.append(source)
    verified: list[dict[str, Any]] = []
    for (field_name, value, unit), sources in sorted(grouped.items()):
        official = [source for source in sources if source["authority"] in _TARGET_OFFICIAL_AUTHORITIES]
        technical = [source for source in sources if source["authority"] in _TARGET_TECHNICAL_AUTHORITIES]
        technical_origins = {source["origin_key"] for source in technical if source["origin_key"]}
        technical_copies = {source["copy_fingerprint"] for source in technical if source["copy_fingerprint"]}
        if not official and not (len(technical_origins) >= 2 and len(technical_copies) >= 2):
            continue
        verified.append({
            "scope": "target",
            "target_identity": target_identity,
            "field_name": field_name,
            "value": value,
            "unit": unit,
            "activation_policy": "official_or_two_independent_sources",
            "sources": [*official, *technical][:8],
        })
    conflicting_fields = {
        field_name
        for field_name in {value["field_name"] for value in verified}
        if len({
            (value["value"].casefold(), value["unit"].casefold())
            for value in verified if value["field_name"] == field_name
        }) > 1
    }
    return [value for value in verified if value["field_name"] not in conflicting_fields]


def _research_result(
    context: str, verified: list[dict], verified_target: list[dict], metrics: dict,
) -> dict[str, Any]:
    return {
        "context": context[:12_000],
        "verified_product_evidence": verified,
        "verified_target_evidence": verified_target,
        "research_metrics": metrics,
    }


def _render_research_context(
    groups: list[ResearchGroup],
    preloaded: dict[str, str],
    pages_read: int,
    callbacks: DeepResearchCallbacks,
) -> list[str]:
    state = {
        "tentadas": pages_read,
        "confirmada": False,
        "preloaded": preloaded,
        "disable_live_reads": True,
    }
    lines: list[str] = []
    for used_query, query_type, items, listings in groups:
        if not items and not listings:
            continue
        lines.append(f"Busca {len(lines) + 1} ({query_type}): {used_query}")
        lines.extend(callbacks.render_results(items[:6], used_query, query_type, state))
        lines.extend(callbacks.render_listings(listings))
        if sum(len(value) for value in lines) >= 12_000:
            break
    return lines


def _verified_evidence_lines(verified: list[dict]) -> list[str]:
    if not verified:
        return []
    return [
        "Evidencias tecnicas ativadas pelo dossie (somente fatos verified):",
        *[
            "- {field} [{scope}]: {value}{unit} ({policy})".format(
                field=value.get("field_name") or "campo",
                scope=value.get("scope") or "product",
                value=value.get("value") or "",
                unit=(" " + str(value.get("unit") or "")).rstrip(),
                policy=value.get("activation_policy") or "",
            )
            for value in verified[:60]
        ],
    ]


_READ_METRIC_KEYS = (
    "pages_attempted", "pages_read", "read_failures", "read_timeouts", "retry_successes",
)


def _accumulate_read_metrics(
    totals: dict[str, int], *batches: dict[str, int],
) -> int:
    batch_pages_read = 0
    for batch in batches:
        for key in _READ_METRIC_KEYS:
            value = int(batch.get(key) or 0)
            totals[key] += value
            if key == "pages_read":
                batch_pages_read += value
    return batch_pages_read


def collect_deep_research_context(
    client_id: str, queries: list[dict], agent_input: dict,
    *,
    phase: str,
    search: Callable[..., list[dict]],
    callbacks: DeepResearchCallbacks,
) -> dict[str, Any]:
    """Collect a bounded dossier while keeping page bodies process-local."""

    started = time.monotonic()
    session, accepted, variants = callbacks.reserve_queries(agent_input, queries)
    page_limit = 10 if phase == "identity" else 40
    phase_seconds = 55.0 if phase == "identity" else 230.0
    deadline = min(session.deadline_monotonic, started + phase_seconds)
    accepted_texts = [str(value.get("query") or "") for value in accepted]
    prefetch = (
        callbacks.prefetch_web(
            client_id,
            accepted_texts,
            search,
            deadline_monotonic=deadline,
        )
        if accepted_texts and session.remaining_seconds() > 0
        else {}
    )
    groups, discovered_urls = _collect_groups(
        queries,
        variants,
        prefetch,
        callbacks.select_items,
    )
    documents: list[ResearchDocumentV1] = []
    document_hashes: set[str] = set()
    fact_signatures: set[tuple[str, str, str, str]] = set()
    preloaded: dict[str, str] = {}
    read_totals = {key: 0 for key in _READ_METRIC_KEYS}
    page_count = no_new_fact_rounds = 0
    stop_reason = "no_new_facts"
    for group_index, (used_query, query_type, items, _listings) in enumerate(groups):
        if time.monotonic() >= deadline:
            stop_reason = "deadline"
            break
        selected, page_count = _select_pages(
            session, items, used_query, query_type,
            page_count=page_count, page_limit=page_limit,
        )
        readings, read_metrics, deadline_exhausted = callbacks.read_batch(
            selected,
            deadline_monotonic=deadline,
        )
        linked: list[ResearchEntry] = []
        if not deadline_exhausted and time.monotonic() < deadline:
            linked, page_count = _select_linked_pages(
                session, selected, readings, callbacks.discover_links, discovered_urls,
                page_count=page_count, page_limit=page_limit,
            )
        linked_readings, linked_metrics, linked_deadline_exhausted = callbacks.read_batch(
            linked,
            deadline_monotonic=deadline,
        )
        readings.update(linked_readings)
        selected.extend(linked)
        batch_pages_read = _accumulate_read_metrics(
            read_totals, read_metrics, linked_metrics,
        )
        preloaded.update(
            {
                key: sanitize_public_research_text(value)
                for key, value in readings.items()
            }
        )
        new_facts = _append_documents(
            selected, readings, agent_input, documents, document_hashes, fact_signatures,
        )
        if new_facts:
            no_new_fact_rounds = 0
        elif batch_pages_read:
            no_new_fact_rounds += 1
        compatibility_query_pending = any(str(group[1] or "") in _COMPATIBILITY_CRITICAL_QUERY_TYPES
                                          for group in groups[group_index + 1 :])
        if not compatibility_query_pending and apparent_coverage_complete(
            agent_input, documents,
        ):
            stop_reason = "coverage_complete"
            break
        if (
            (deadline_exhausted or linked_deadline_exhausted)
            and time.monotonic() >= deadline
        ):
            stop_reason = "deadline"
            break
        if no_new_fact_rounds >= 2 and not compatibility_query_pending:
            stop_reason = "no_new_facts"
            break
        if page_count >= page_limit or session.pages_used >= PUBLIC_RESEARCH_MAX_PAGES:
            stop_reason = "page_limit"
            break
    if time.monotonic() >= deadline:
        stop_reason = "deadline"
    elif read_totals["pages_attempted"] and not read_totals["pages_read"]:
        stop_reason = "provider_unavailable"
    elif not accepted_texts and len(session.queries_used) >= 12:
        stop_reason = "page_limit"
    elif not groups and accepted_texts:
        stop_reason = "provider_unavailable"
    lines = _render_research_context(groups, preloaded, read_totals["pages_read"], callbacks)
    metrics = {
        "pages_discovered": len(discovered_urls),
        **read_totals,
        "duration_ms": int(max(0.0, time.monotonic() - started) * 1000),
        "stop_reason": stop_reason,
        "coverage_complete": stop_reason == "coverage_complete",
    }
    metrics["pages_read"] = min(metrics["pages_read"], len(discovered_urls))
    verified_target = _verified_target_evidence(agent_input, documents)
    verified, metrics = persist_research_documents(
        client_id, agent_input, documents, metrics=metrics,
    )
    context = "\n\n".join([*_verified_evidence_lines(verified), *lines])
    return _research_result(context, verified, verified_target, metrics)
