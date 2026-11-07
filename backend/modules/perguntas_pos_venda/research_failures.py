"""Bounded research diagnostics shared by generation, storage and HTTP."""

from collections.abc import Mapping


# Identity-only marker set by the manual question handler, never by JSON input.
MANUAL_RESEARCH_RECOVERY = object()

RESEARCH_FAILURE_SOURCES = frozenset({
    "technical_web", "product_identity_web", "listing", "internal_catalog",
    "bling", "context_hub", "same_store_listing", "research",
})
_RESEARCH_FAILURE_REASONS = frozenset({"timeout", "unavailable", "error", "no_results"})


def safe_research_failures(value: object) -> list[dict[str, str]]:
    """Keep only stable source/reason codes, never a query or tool output."""
    if not isinstance(value, list):
        return []
    failures: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        source, reason = entry.get("source"), entry.get("reason")
        if not isinstance(source, str) or source not in RESEARCH_FAILURE_SOURCES:
            continue
        if not isinstance(reason, str) or reason not in _RESEARCH_FAILURE_REASONS:
            continue
        failure = {"source": source, "reason": reason}
        if failure not in failures:
            failures.append(failure)
    return failures
