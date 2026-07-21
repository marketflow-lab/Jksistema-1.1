from __future__ import annotations

from backend.services.codex_model_router import (
    BASELINE_MODEL,
    ModelRouter,
    ModelRoutingPolicyV1,
)


def test_disabled_policy_keeps_gpt55_even_when_gpt56_is_requested():
    decision = ModelRouter().decide(
        category="technical",
        requested_model="codex:gpt-5.6-sol",
        rollout_key="trace-1",
    )

    assert decision.effective_model == BASELINE_MODEL
    assert decision.reason_code == "policy_disabled"
    assert decision.rerouted is True
    assert decision.requested_model == "gpt-5.6-sol"


def test_full_rollout_selects_variant_only_for_each_promoted_category():
    policy = ModelRoutingPolicyV1.rollout(
        policy_version="pilot-7",
        categories={
            "conversation": 100,
            "report": 100,
            "technical": 100,
        },
    )
    router = ModelRouter(policy)

    assert router.decide(
        category="conversation", requested_model=BASELINE_MODEL, rollout_key="a"
    ).effective_model == "gpt-5.6-luna"
    assert router.decide(
        category="report", requested_model=BASELINE_MODEL, rollout_key="b"
    ).effective_model == "gpt-5.6-terra"
    assert router.decide(
        category="technical", requested_model=BASELINE_MODEL, rollout_key="c"
    ).effective_model == "gpt-5.6-sol"

    not_promoted = router.decide(
        category="fitment", requested_model="gpt-5.6-sol", rollout_key="d"
    )
    assert not_promoted.effective_model == BASELINE_MODEL
    assert not_promoted.reason_code == "category_not_promoted"


def test_rollout_bucket_is_deterministic_and_runtime_fallback_is_gpt55():
    audits = []
    policy = ModelRoutingPolicyV1.rollout(
        policy_version="pilot-50",
        categories={"data_selection": 50},
    )
    router = ModelRouter(policy, audit_hook=audits.append)

    first = router.decide(
        category="data-selection",
        requested_model=BASELINE_MODEL,
        rollout_key="stable-trace",
    )
    second = router.decide(
        category="data_selection",
        requested_model=BASELINE_MODEL,
        rollout_key="stable-trace",
    )
    assert first.rollout_bucket == second.rollout_bucket
    assert first.effective_model == second.effective_model

    fallback = router.fallback(first, failed_model=first.effective_model, reason_code="timeout")
    assert fallback.effective_model == BASELINE_MODEL
    assert fallback.reroute_reason == "timeout"
    assert audits[-1]["effective_model"] == BASELINE_MODEL


def test_unavailable_candidate_never_switches_to_another_gpt56_variant():
    policy = ModelRoutingPolicyV1.rollout(
        policy_version="pilot",
        categories={"technical": 100},
    )
    decision = ModelRouter(policy).decide(
        category="technical",
        requested_model=BASELINE_MODEL,
        rollout_key="trace",
        available_models={BASELINE_MODEL, "gpt-5.6-terra"},
    )
    assert decision.effective_model == BASELINE_MODEL
    assert decision.reason_code == "candidate_unavailable"
