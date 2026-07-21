from __future__ import annotations

import copy

import pytest

from backend.services import codex_evaluations as evaluation


def _case(index: int, *, source: str = "real_anonymized", category: str = "stock_catalog_listing_fitment", split: str = "calibration"):
    reviewers = ["reviewer-a", "reviewer-b"]
    return {
        "schema_version": evaluation.DATASET_SCHEMA_VERSION,
        "id": f"case-{index:03d}",
        "source_kind": source,
        "category": category,
        "split": split,
        "conversation_group": f"conversation-{index:03d}",
        "question": "Consulte o SKU-FICTICIO na Loja Alfa.",
        "history": [],
        "risk": "read",
        "fictional_stores": ["Loja Alfa"],
        "allowed_tools": ["product_data"],
        "fixtures": {"product_data": {"success": True}},
        "expected": {"intent_id": "product.lookup"},
        "required_facts": [],
        "sources": ["fixture:product_data"],
        "prohibited_claims": [],
        "abstention_conditions": ["fixture_missing"],
        "review": {"status": "approved", "reviewers": reviewers},
    }


def _published_cases():
    rows = []
    index = 0
    for category, count in evaluation.CATEGORY_QUOTAS.items():
        for _ in range(count):
            index += 1
            rows.append(_case(index, category=category, split="calibration" if index <= 75 else "holdout"))
    for synthetic in range(20):
        index += 1
        rows.append(
            _case(
                index,
                source="synthetic_security",
                category="ambiguity_continuity_failure",
                split="calibration" if synthetic < 15 else "holdout",
            )
        )
    return rows


def test_published_dataset_contract_has_exact_counts_and_hash():
    summary = evaluation.validate_published_dataset(_published_cases())
    assert summary["total"] == 120
    assert summary["real"] == 100
    assert summary["synthetic"] == 20
    assert summary["splits"] == {"calibration": 90, "holdout": 30}
    assert len(summary["dataset_hash"]) == 64


def test_dataset_rejects_conversation_leak_and_missing_review():
    rows = _published_cases()
    rows[-1]["conversation_group"] = rows[0]["conversation_group"]
    rows[-1]["review"] = {"status": "pending", "reviewers": []}
    with pytest.raises(evaluation.EvaluationContractError) as error:
        evaluation.validate_published_dataset(rows)
    assert "human_review_required" in str(error.value)
    assert "conversation_split_leak" in str(error.value)


def test_scoring_weights_and_automatic_failure():
    case = _case(1)
    perfect = {"scores": {name: 100 for name in evaluation.SCORE_WEIGHTS}}
    assert evaluation.score_candidate(case, perfect)["score"] == 100
    leaked = copy.deepcopy(perfect)
    leaked["automatic_failures"] = ["cross_store_leak"]
    assert evaluation.score_candidate(case, leaked)["passed"] is False


def test_campaign_limit_and_injected_runner_are_deterministic():
    cases = _published_cases()[:30]
    request = evaluation.build_run_request(cases, models=evaluation.MODEL_IDS, repetitions=1)
    run = evaluation.run_with_fixtures(
        cases,
        request,
        lambda _case, model, _repetition: {
            "model_effective": model,
            "duration_ms": 10,
            "tokens": 20,
            "scores": {name: 100 for name in evaluation.SCORE_WEIGHTS},
        },
    )
    assert run["execution_count"] == 120
    assert all(item["score"]["passed"] for item in run["results"])

    with pytest.raises(evaluation.EvaluationContractError, match="campaign_limit"):
        evaluation.build_run_request(_published_cases(), models=evaluation.MODEL_IDS, repetitions=2)


def test_campaign_budget_is_exactly_six_hundred():
    assert evaluation.campaign_budget() == {
        "calibration": 120,
        "benchmark": 240,
        "stability": 120,
        "reserve": 120,
        "total": 600,
    }


def test_paired_comparison_only_recommends_manual_category_promotion():
    results = []
    for index in range(20):
        results.extend(
            [
                {
                    "case_id": f"case-{index}",
                    "category": "sales_returns_reports",
                    "model_effective": "gpt-5.5",
                    "score": {"score": 90, "automatic_failures": []},
                    "tokens": 1000,
                    "duration_ms": 1000,
                },
                {
                    "case_id": f"case-{index}",
                    "category": "sales_returns_reports",
                    "model_effective": "gpt-5.6-terra",
                    "score": {"score": 90, "automatic_failures": []},
                    "tokens": 800,
                    "duration_ms": 900,
                },
            ]
        )
    comparison = evaluation.paired_model_comparison(
        results,
        candidate_model="gpt-5.6-terra",
        category="sales_returns_reports",
    )
    assert comparison["eligible_for_manual_promotion"] is True
    assert comparison["automatic_promotion"] is False
    assert comparison["token_change_ratio"] == -0.2
