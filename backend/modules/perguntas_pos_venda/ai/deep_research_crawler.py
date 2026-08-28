"""Bounded public-web crawler used by Mercado Livre question research."""

from __future__ import annotations

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
from .deep_research_contracts import sanitize_public_research_text


ResearchEntry = tuple[dict, str, str, str]
ResearchGroup = tuple[str, str, list, list[dict]]


@dataclass(frozen=True)
class DeepResearchCallbacks:
    """Source-layer operations kept injectable to preserve the legacy facade."""

    reserve_queries: Callable[..., tuple[object, list[dict[str, str]], dict[str, list[str]]]]
    prefetch_web: Callable[..., dict[str, list[dict[str, Any]]]]
    select_items: Callable[[dict, dict, set[str]], tuple[str, str, list]]
    read_batch: Callable[..., tuple[dict[str, str], int, bool]]
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
) -> tuple[dict[str, str], int, bool]:
    """Read at most four pages concurrently and abandon late results."""

    if not entries:
        return {}, 0, False
    queue: Queue = Queue()
    lock = Lock()
    results: dict[str, str] = {}
    completed = 0
    for entry in entries:
        queue.put_nowait(entry)

    def worker() -> None:
        nonlocal completed
        while time.monotonic() < deadline_monotonic:
            try:
                _item, url, query, _query_type = queue.get_nowait()
            except Empty:
                return
            try:
                content = reader(url, query, deep=True)
            except Exception:
                content = ""
            with lock:
                key = canonical_research_url(url) or str(url or "").lower()
                results[key] = str(content or "")
                completed += 1
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
    timed_out = any(thread.is_alive() for thread in workers) or not queue.empty()
    return results, completed, timed_out


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
        if document is None or document.content_hash in document_hashes:
            continue
        document_hashes.add(document.content_hash)
        documents.append(document)
        for claim in extract_technical_claims(document.text):
            if claim.signature not in fact_signatures:
                fact_signatures.add(claim.signature)
                new_facts += 1
    return new_facts


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


def collect_deep_research_context(
    client_id: str,
    queries: list[dict],
    agent_input: dict,
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
    pages_read = page_count = no_new_fact_rounds = 0
    stop_reason = "no_new_facts"
    for used_query, query_type, items, _listings in groups:
        if time.monotonic() >= deadline:
            stop_reason = "deadline"
            break
        selected, page_count = _select_pages(
            session, items, used_query, query_type,
            page_count=page_count, page_limit=page_limit,
        )
        readings, completed, timed_out = callbacks.read_batch(
            selected,
            deadline_monotonic=deadline,
        )
        linked: list[ResearchEntry] = []
        if not timed_out and time.monotonic() < deadline:
            linked, page_count = _select_linked_pages(
                session, selected, readings, callbacks.discover_links, discovered_urls,
                page_count=page_count, page_limit=page_limit,
            )
        linked_readings, linked_completed, linked_timeout = callbacks.read_batch(
            linked,
            deadline_monotonic=deadline,
        )
        readings.update(linked_readings)
        selected.extend(linked)
        pages_read += completed + linked_completed
        preloaded.update(
            {
                key: sanitize_public_research_text(value)
                for key, value in readings.items()
            }
        )
        new_facts = _append_documents(
            selected, readings, agent_input, documents, document_hashes, fact_signatures,
        )
        no_new_fact_rounds = no_new_fact_rounds + 1 if new_facts == 0 else 0
        if apparent_coverage_complete(agent_input, documents):
            stop_reason = "coverage_complete"
            break
        if timed_out or linked_timeout:
            stop_reason = "deadline"
            break
        if no_new_fact_rounds >= 2:
            stop_reason = "no_new_facts"
            break
        if page_count >= page_limit or session.pages_used >= PUBLIC_RESEARCH_MAX_PAGES:
            stop_reason = "page_limit"
            break
    if time.monotonic() >= deadline:
        stop_reason = "deadline"
    elif not accepted_texts and len(session.queries_used) >= 12:
        stop_reason = "page_limit"
    elif not groups and accepted_texts:
        stop_reason = "provider_unavailable"
    lines = _render_research_context(groups, preloaded, pages_read, callbacks)
    metrics = {
        "pages_discovered": len(discovered_urls),
        "pages_read": min(pages_read, len(discovered_urls)),
        "duration_ms": int(max(0.0, time.monotonic() - started) * 1000),
        "stop_reason": stop_reason,
        "coverage_complete": stop_reason == "coverage_complete",
    }
    verified, metrics = persist_research_documents(
        client_id, agent_input, documents, metrics=metrics,
    )
    context = "\n\n".join([*_verified_evidence_lines(verified), *lines])
    return {
        "context": context[:12_000],
        "verified_product_evidence": verified,
        "research_metrics": metrics,
    }
