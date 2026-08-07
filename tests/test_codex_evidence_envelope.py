from __future__ import annotations

import pytest

from backend.services.codex.assistant.evidence import (
    EVIDENCE_SCHEMA,
    classify_evidence,
    denied_evidence,
    failed_evidence,
    mark_dlp_partial,
    mark_recent_cache,
)


def _registry(
    tool_id: str, *, records: int = 0, coverage: bool = False, error: str = "",
    has_more: bool = False, dlp_removed: int = 0,
) -> dict:
    summary = {"coverage_complete": coverage}
    if error:
        summary["error"] = error
    if has_more:
        summary["paging"] = {"has_more": True}
    if dlp_removed:
        summary["dlp_blocked_count"] = dlp_removed
    return {"tool_id": tool_id, "records": records, "source": f"source:{tool_id}", "summary": summary}


def _classify(tool_id: str, registry_results: list[dict], warnings: list[str] | None = None) -> dict:
    return classify_evidence(
        tool_id=tool_id,
        records=sum(int(item.get("records") or 0) for item in registry_results),
        sources=[str(item.get("source") or "") for item in registry_results],
        warnings=warnings or [], empty_reasons=[], next_fallbacks=["source_discovery"],
        registry_results=registry_results,
    )


@pytest.mark.parametrize(
    ("name", "evidence", "status", "scope", "complete"),
    [
        ("complete", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", records=2, coverage=True)]), "complete", "full", True),
        ("confirmed_zero", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", coverage=True)]), "confirmed_zero", "full", True),
        ("partial_store", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", records=1, coverage=True), _registry("mercado_livre_listing", error="API indisponivel")]), "partial", "observed_only", False),
        ("pagination", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", records=1, coverage=True, has_more=True)]), "partial", "observed_only", False),
        ("inconclusive_empty", _classify("stock_data", [_registry("stock_data")]), "insufficient", "none", False),
        ("authentication", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", error="HTTP 401 token expirado")]), "unavailable", "none", False),
        ("timeout", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", error="timeout")]), "unavailable", "none", False),
        ("dlp", _classify("mercado_livre_listing", [_registry("mercado_livre_listing", records=1, coverage=True, dlp_removed=1)]), "partial", "observed_only", False),
    ],
)
def test_evidence_classification_matrix(name: str, evidence: dict, status: str, scope: str, complete: bool) -> None:
    assert name
    assert evidence["schema"] == EVIDENCE_SCHEMA
    assert evidence["status"] == status
    assert evidence["claim_scope"] == scope
    assert evidence["coverage_complete"] is complete


def test_timeout_is_retryable_but_authentication_is_not() -> None:
    timeout = _classify("mercado_livre_listing", [_registry("mercado_livre_listing", error="timeout")])
    auth = _classify("mercado_livre_listing", [_registry("mercado_livre_listing", error="HTTP 401 token expirado")])
    assert timeout["retryable"] is True
    assert auth["retryable"] is False


def test_local_history_cannot_promote_inconclusive_primary_source() -> None:
    evidence = _classify("mercado_livre_orders", [
        _registry("mercado_livre_orders"),
        _registry("sales_returns_query", records=4, coverage=True),
    ])
    assert evidence["status"] == "insufficient"
    assert evidence["claim_scope"] == "none"
    assert evidence["attempted_fallbacks"][0]["tool_id"] == "sales_returns_query"


def test_cache_dlp_denied_and_failed_envelopes_remain_non_conclusive() -> None:
    complete = _classify("mercado_livre_listing", [_registry("mercado_livre_listing", records=1, coverage=True)])
    cached = mark_recent_cache(complete)
    dlp = mark_dlp_partial(complete, 1)
    assert cached["status"] == "partial" and cached["freshness"] == "recent_cache"
    assert cached["claim_scope"] == "observed_only" and cached["coverage_complete"] is False
    assert dlp["status"] == "partial" and "dlp_filtered_rows" in dlp["missing_fields"]
    assert denied_evidence("blocked")["status"] == "denied"
    assert failed_evidence("invalid contract")["status"] == "failed"
