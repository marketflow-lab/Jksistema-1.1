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
    finalize_research_evidence_result,
    make_research_document,
    persist_research_documents,
    research_session,
)
from .queries import _ia_agent_perguntas_relaxar_query_web
from .runtime import _normalizar_texto
from .deep_research_contracts import (
    PUBLIC_RESEARCH_GAP_MAX_PAGES,
    PUBLIC_RESEARCH_GAP_MAX_SECONDS,
    PUBLIC_RESEARCH_INITIAL_MAX_PAGES,
    PUBLIC_RESEARCH_INITIAL_MAX_SECONDS,
    sanitize_public_research_text,
    technical_assertion_occurrence_is_positive,
)
from .deep_research_fingerprints import research_document_rank
from .official_source_registry import is_reviewed_official_domain, official_domains_for_target_identity


from .deep_research_target import (
    _COMPATIBILITY_CRITICAL_QUERY_TYPES,
    _TARGET_SOURCE_AUTHORITIES,
    DeepResearchCallbacks,
    ResearchEntry,
    ResearchGroup,
    _render_research_context,
    _target_identity_from_input,
    _target_identity_is_specific,
    _target_identity_pattern,
    _target_interface_claims,
    _target_scoped_text,
    _target_source_authority,
    _verified_target_evidence,
)


def reserve_research_queries(
    agent_input: dict,
    queries: list[dict],
) -> tuple[object, list[dict[str, str]], dict[str, list[str]]]:
    """Reserve original queries in planner/gap/fallback order.

    Relaxed variants are deliberately absent here: the collector may reserve
    one only after the provider returned zero results for its original query.
    """

    session = research_session(agent_input)
    originals: list[dict[str, str]] = []
    variants_by_original: dict[str, list[str]] = {}
    phase_order = {"plan": 0, "gap": 1, "initial": 2, "identity": 2, "fallback": 3}
    ordered = sorted(
        enumerate(queries[:24]),
        key=lambda pair: (
            phase_order.get(str((pair[1] or {}).get("research_phase") or "fallback").lower(), 3)
            if isinstance(pair[1], dict)
            else 3,
            pair[0],
        ),
    )
    for _index, query in ordered:
        if not isinstance(query, dict):
            continue
        original = str(query.get("query") or "").strip()[:260]
        query_type = str(query.get("type") or "web").strip()[:80]
        research_phase = str(query.get("research_phase") or "fallback").strip().lower()[:16]
        if research_phase not in phase_order:
            research_phase = "fallback"
        if not original:
            continue
        variants_by_original[original] = [original]
        originals.append({
            "query": original,
            "type": query_type,
            "research_phase": research_phase,
        })
    accepted = session.reserve_queries(originals)
    accepted_texts = {str(value.get("query") or "") for value in accepted}
    variants_by_original = {
        original: [original]
        for original in variants_by_original
        if original in accepted_texts
    }
    return session, accepted, variants_by_original


def _prefetch_reserved_queries(
    client_id: str,
    accepted: list[dict[str, str]],
    variants_by_original: dict[str, list[str]],
    *,
    session: object,
    search: Callable[..., list[dict]],
    callbacks: DeepResearchCallbacks,
    deadline_monotonic: float,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]], int]:
    """Search phases serially and relax only an original with zero provider hits."""

    phase_order = ("plan", "gap", "identity", "initial", "fallback")
    prefetch: dict[str, list[dict[str, Any]]] = {}
    all_accepted = list(accepted)
    relaxed_count = 0
    for phase in phase_order:
        phase_queries = [
            value for value in accepted
            if str(value.get("research_phase") or "initial").strip().lower() == phase
        ]
        if not phase_queries or time.monotonic() >= deadline_monotonic:
            continue
        originals = [str(value.get("query") or "") for value in phase_queries]
        original_results = callbacks.prefetch_web(
            client_id,
            originals,
            search,
            deadline_monotonic=deadline_monotonic,
        )
        if isinstance(original_results, dict):
            prefetch.update(original_results)
        relaxed_requests: list[dict[str, str]] = []
        relaxed_owners: dict[str, str] = {}
        for value in phase_queries:
            original = str(value.get("query") or "")
            if prefetch.get(original):
                continue
            relaxed = _ia_agent_perguntas_relaxar_query_web(original)[:260]
            if not relaxed or _normalizar_texto(relaxed) == _normalizar_texto(original):
                continue
            relaxed_requests.append({
                "query": relaxed,
                "type": str(value.get("type") or "web"),
                "research_phase": phase,
            })
            relaxed_owners[relaxed] = original
        reserve = getattr(session, "reserve_queries", None)
        newly_accepted = reserve(relaxed_requests) if callable(reserve) else []
        if not newly_accepted:
            continue
        relaxed_texts = [str(value.get("query") or "") for value in newly_accepted]
        relaxed_results = callbacks.prefetch_web(
            client_id,
            relaxed_texts,
            search,
            deadline_monotonic=deadline_monotonic,
        )
        if isinstance(relaxed_results, dict):
            prefetch.update(relaxed_results)
        for value in newly_accepted:
            relaxed = str(value.get("query") or "")
            original = relaxed_owners.get(relaxed)
            if not original:
                continue
            variants_by_original.setdefault(original, [original]).append(relaxed)
            all_accepted.append(value)
            relaxed_count += 1
    return prefetch, all_accepted, relaxed_count


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
    phase_order = {"plan": 0, "gap": 1, "identity": 2, "initial": 2, "fallback": 3}
    ordered_queries = sorted(
        enumerate(queries[:24]),
        key=lambda pair: (
            phase_order.get(str((pair[1] or {}).get("research_phase") or "initial").lower(), 2)
            if isinstance(pair[1], dict)
            else 3,
            pair[0],
        ),
    )
    for _index, query in ordered_queries:
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
    research_phase: str,
    page_count: int,
    page_limit: int,
) -> tuple[list[ResearchEntry], int]:
    selected: list[ResearchEntry] = []
    for item, url in items:
        if page_count >= page_limit:
            break
        try:
            reserved = session.reserve_page(url, research_phase=research_phase)
        except TypeError:
            reserved = session.reserve_page(url)
        if not reserved:
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
    research_phase: str,
    page_count: int,
    page_limit: int,
) -> tuple[list[ResearchEntry], int]:
    linked: list[ResearchEntry] = []
    for item, url, query, query_type in selected:
        key = canonical_research_url(url) or str(url or "").lower()
        for linked_url in discover_links(url, str(readings.get(key) or "")):
            discovered_urls.add(linked_url)
            if page_count >= page_limit:
                continue
            try:
                reserved = session.reserve_page(linked_url, research_phase=research_phase)
            except TypeError:
                reserved = session.reserve_page(linked_url)
            if not reserved:
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


def _crawl_reserved_groups(
    queries: list[dict], accepted: list[dict[str, str]],
    variants: dict[str, list[str]], groups: list[ResearchGroup],
    discovered_urls: set[str], agent_input: dict, session: object,
    budget_phase: str, deadline: float, page_limit: int,
    callbacks: DeepResearchCallbacks,
) -> tuple[list[ResearchDocumentV1], dict[str, str], dict[str, int], str]:
    documents: list[ResearchDocumentV1] = []
    document_hashes: set[str] = set()
    fact_signatures: set[tuple[str, str, str, str]] = set()
    preloaded: dict[str, str] = {}
    read_totals = {key: 0 for key in _READ_METRIC_KEYS}
    page_count = no_new_fact_rounds = 0
    stop_reason = "no_new_facts"
    query_phase_by_text = {
        str(value.get("query") or ""): str(value.get("research_phase") or budget_phase).lower()
        for value in accepted
    }
    priority_query_texts = {
        variant
        for original, query_variants in variants.items()
        if next(
            (
                str(query.get("research_phase") or "").lower()
                for query in queries
                if isinstance(query, dict) and str(query.get("query") or "") == original
            ),
            "",
        ) in {"plan", "gap"}
        for variant in query_variants
    }
    for group_index, (used_query, query_type, items, _listings) in enumerate(groups):
        if time.monotonic() >= deadline:
            stop_reason = "deadline"
            break
        group_phase = (
            "gap"
            if query_phase_by_text.get(used_query) == "gap" or budget_phase == "gap"
            else "initial"
        )
        selected, page_count = _select_pages(
            session, items, used_query, query_type,
            research_phase=group_phase,
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
                research_phase=group_phase,
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
        document_count_before = len(documents)
        new_facts = _append_documents(
            selected, readings, agent_input, documents, document_hashes, fact_signatures,
        )
        new_documents = max(0, len(documents) - document_count_before)
        if new_facts or new_documents:
            no_new_fact_rounds = 0
        elif batch_pages_read:
            no_new_fact_rounds += 1
        compatibility_query_pending = any(str(group[1] or "") in _COMPATIBILITY_CRITICAL_QUERY_TYPES
                                          for group in groups[group_index + 1 :])
        priority_query_pending = any(
            str(group[0] or "") in priority_query_texts
            for group in groups[group_index + 1 :]
        )
        if not compatibility_query_pending and not priority_query_pending and apparent_coverage_complete(
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
        if no_new_fact_rounds >= 2 and not compatibility_query_pending and not priority_query_pending:
            stop_reason = "no_new_facts"
            break
        if page_count >= page_limit or session.pages_used >= PUBLIC_RESEARCH_MAX_PAGES:
            stop_reason = "page_limit"
            break
    return documents, preloaded, read_totals, stop_reason


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
    budget_phase = "gap" if str(phase or "").strip().lower() == "gap" else "initial"
    page_limit = (
        PUBLIC_RESEARCH_GAP_MAX_PAGES
        if budget_phase == "gap"
        else PUBLIC_RESEARCH_INITIAL_MAX_PAGES
    )
    phase_seconds = (
        PUBLIC_RESEARCH_GAP_MAX_SECONDS
        if budget_phase == "gap"
        else PUBLIC_RESEARCH_INITIAL_MAX_SECONDS
    )
    phase_deadline = getattr(session, "phase_deadline_monotonic", None)
    deadline = (
        phase_deadline(budget_phase)
        if callable(phase_deadline)
        else min(session.deadline_monotonic, started + phase_seconds)
    )
    accepted_texts = [str(value.get("query") or "") for value in accepted]
    if accepted_texts and session.remaining_seconds() > 0:
        prefetch, accepted, relaxed_query_count = _prefetch_reserved_queries(
            client_id,
            accepted,
            variants,
            session=session,
            search=search,
            callbacks=callbacks,
            deadline_monotonic=deadline,
        )
    else:
        prefetch, relaxed_query_count = {}, 0
    accepted_texts = [str(value.get("query") or "") for value in accepted]
    groups, discovered_urls = _collect_groups(
        queries,
        variants,
        prefetch,
        callbacks.select_items,
    )
    documents, preloaded, read_totals, stop_reason = _crawl_reserved_groups(
        queries, accepted, variants, groups, discovered_urls, agent_input, session,
        budget_phase, deadline, page_limit, callbacks,
    )
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
        "queries_used": len(accepted_texts),
        "relaxed_queries_used": relaxed_query_count,
        "research_phase": budget_phase,
    }
    metrics["pages_read"] = min(metrics["pages_read"], len(discovered_urls))
    verified_target = _verified_target_evidence(agent_input, documents)
    return finalize_research_evidence_result(
        client_id, agent_input, documents, lines, metrics, verified_target,
        persist=persist_research_documents,
    )
