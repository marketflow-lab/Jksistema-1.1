from __future__ import annotations

import pytest

from backend.services.codex_runtime_performance import (
    CacheEnvelope,
    CachePolicy,
    DeadlineBudget,
    DeadlineExceeded,
    PerformanceWindow,
    RuntimePerformanceTrace,
    build_scoped_cache_key,
)


def test_deadline_budget_children_never_extend_parent():
    now = [100.0]

    def clock():
        return now[0]

    budget = DeadlineBudget.from_timeout(10, clock=clock)
    now[0] = 104.0
    child = budget.child(20, label="tools")

    assert child.deadline_monotonic == budget.deadline_monotonic
    assert budget.timeout_for_call(3) == pytest.approx(3)

    now[0] = 110.0
    assert budget.expired() is True
    with pytest.raises(DeadlineExceeded, match="deadline_exceeded"):
        child.checkpoint("tools")


def test_cache_key_and_envelope_never_expose_scope_values():
    cache_key = build_scoped_cache_key(
        b"k" * 32,
        namespace="readonly_tool",
        client_id="000002",
        store_id="Loja Secreta",
        user_id="usuario@example.com",
        source_version="catalog-v2",
        inputs={"sku": "ABC-123", "page": 1},
    )

    assert cache_key.startswith("hmac-sha256:")
    assert "000002" not in cache_key
    assert "Loja" not in cache_key
    assert "ABC-123" not in cache_key

    policy = CachePolicy("readonly_tool", ttl_seconds=10, stale_if_error_seconds=5)
    envelope = CacheEnvelope.build(
        key_hash=cache_key,
        policy=policy,
        created_monotonic=100,
        now_monotonic=112,
        found=True,
        source_version="catalog-v2",
    )
    payload = envelope.as_telemetry()

    assert envelope.state == "stale"
    assert payload["cache_age_ms"] == 12_000
    assert payload["source_version_hash"] != "catalog-v2"


def test_performance_trace_exports_only_numeric_allowed_stages_and_percentiles():
    now = [10.0]

    def clock():
        return now[0]

    trace = RuntimePerformanceTrace(clock=clock)
    with trace.measure("selection"):
        now[0] += 0.250
    trace.record_duration("tools", 500)
    provider_started = now[0]
    now[0] += 0.125
    trace.mark_ttft(provider_started)

    snapshot = trace.snapshot()
    assert snapshot["stage_duration_ms"] == {
        "selection": 250.0,
        "tools": 500.0,
        "provider_ttft": 125.0,
    }

    window = PerformanceWindow(max_samples=3)
    window.add(snapshot)
    window.add({"stage_duration_ms": {"selection": 1000}})
    summary = window.summary()
    assert summary["selection"]["count"] == 2
    assert summary["selection"]["p50_ms"] == 250.0
    assert summary["selection"]["p95_ms"] == 1000.0

    with pytest.raises(ValueError, match="runtime_stage_invalid"):
        trace.record_duration("prompt_content", 1)
