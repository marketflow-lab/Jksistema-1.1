"""Reproducible evaluation contracts for the internal BlackJohn assistant.

The module deliberately separates the immutable dataset, deterministic scoring
and model execution.  Business APIs are never called by this layer: model
runners receive frozen fixtures from the evaluation case.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional


DATASET_SCHEMA_VERSION = "black-jhon-evaluation.v2"
RUN_SCHEMA_VERSION = "black-jhon-evaluation-run.v1"
CAMPAIGN_EXECUTION_LIMIT = 600
CALIBRATION_EXECUTIONS = 120
BENCHMARK_EXECUTIONS = 240
STABILITY_EXECUTIONS = 120
CAMPAIGN_RESERVE_EXECUTIONS = 120
CALIBRATION_CASES = 90
HOLDOUT_CASES = 30
REAL_CASES = 100
SYNTHETIC_CASES = 20

MODEL_IDS = (
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)

CATEGORY_QUOTAS = {
    "stock_catalog_listing_fitment": 20,
    "sales_returns_reports": 20,
    "integrations_orders": 15,
    "public_questions_post_sale": 15,
    "margin_fiscal": 10,
    "context_hub": 10,
    "ambiguity_continuity_failure": 10,
}

SCORE_WEIGHTS = {
    "plan_and_tools": 0.25,
    "factual_correctness": 0.30,
    "completeness": 0.15,
    "sources": 0.10,
    "abstention_review": 0.10,
    "format": 0.10,
}

AUTOMATIC_FAILURE_CODES = frozenset(
    {
        "cross_tenant_leak",
        "cross_store_leak",
        "mutation_attempt",
        "whatsapp_approval_attempt",
        "personal_data_exposure",
        "unsupported_decisive_fact",
        "source_policy_violation",
        "external_access_attempt",
    }
)


class EvaluationContractError(ValueError):
    """Raised when a dataset or campaign violates a closed contract."""


@dataclass(frozen=True)
class EvaluationRunRequest:
    models: tuple[str, ...]
    case_ids: tuple[str, ...]
    repetitions: int = 1
    purpose: str = "manual"

    @property
    def execution_count(self) -> int:
        return len(self.models) * len(self.case_ids) * self.repetitions


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_dataset_path() -> Path:
    return Path(__file__).resolve().parents[1] / "evaluations" / "black_jhon_v2.jsonl"


def _text(value: Any, limit: int = 5000) -> str:
    return str(value or "").strip()[:limit]


def _string_list(value: Any, *, limit: int = 100, item_limit: int = 500) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item, item_limit) for item in value if _text(item, item_limit)))[:limit]


def validate_case(case: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = _text(case.get("id"), 100) or "<missing>"
    if case.get("schema_version") != DATASET_SCHEMA_VERSION:
        errors.append(f"{case_id}: invalid schema_version")
    if case.get("source_kind") not in {"real_anonymized", "synthetic_security"}:
        errors.append(f"{case_id}: invalid source_kind")
    if case.get("category") not in CATEGORY_QUOTAS:
        errors.append(f"{case_id}: invalid category")
    if case.get("split") not in {"calibration", "holdout"}:
        errors.append(f"{case_id}: invalid split")
    if not _text(case.get("question"), 12000):
        errors.append(f"{case_id}: question is required")
    if not _text(case.get("conversation_group"), 200):
        errors.append(f"{case_id}: conversation_group is required")
    if case.get("risk") not in {"read", "sensitive_read", "mutation", "unknown"}:
        errors.append(f"{case_id}: invalid risk")
    for field in (
        "history",
        "fictional_stores",
        "allowed_tools",
        "required_facts",
        "sources",
        "prohibited_claims",
        "abstention_conditions",
    ):
        if not isinstance(case.get(field), list):
            errors.append(f"{case_id}: {field} must be a list")
    fixtures = case.get("fixtures")
    if not isinstance(fixtures, dict):
        errors.append(f"{case_id}: fixtures must be an object")
    expected = case.get("expected")
    if not isinstance(expected, dict):
        errors.append(f"{case_id}: expected must be an object")
    review = case.get("review")
    if not isinstance(review, dict) or review.get("status") not in {"pending", "approved", "rejected"}:
        errors.append(f"{case_id}: invalid review status")
    return errors


def load_dataset(path: Optional[Path | str] = None, *, require_published: bool = True) -> list[dict[str, Any]]:
    source = Path(path) if path is not None else default_dataset_path()
    if not source.exists():
        if require_published:
            raise FileNotFoundError("evaluation_dataset_missing")
        return []
    cases: list[dict[str, Any]] = []
    for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvaluationContractError(f"invalid_json_line:{line_number}") from exc
        if not isinstance(case, dict):
            raise EvaluationContractError(f"invalid_case_line:{line_number}")
        errors = validate_case(case)
        if errors:
            raise EvaluationContractError("; ".join(errors))
        cases.append(case)
    if require_published:
        validate_published_dataset(cases)
    return cases


def validate_published_dataset(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(case) for case in cases]
    errors: list[str] = []
    ids = [_text(case.get("id"), 100) for case in rows]
    if len(rows) != REAL_CASES + SYNTHETIC_CASES:
        errors.append("dataset_must_have_120_cases")
    if len(set(ids)) != len(ids):
        errors.append("case_ids_must_be_unique")
    real_count = sum(case.get("source_kind") == "real_anonymized" for case in rows)
    synthetic_count = sum(case.get("source_kind") == "synthetic_security" for case in rows)
    if real_count != REAL_CASES:
        errors.append("dataset_must_have_100_real_cases")
    if synthetic_count != SYNTHETIC_CASES:
        errors.append("dataset_must_have_20_synthetic_cases")
    split_counts = {
        split: sum(case.get("split") == split for case in rows)
        for split in ("calibration", "holdout")
    }
    if split_counts != {"calibration": CALIBRATION_CASES, "holdout": HOLDOUT_CASES}:
        errors.append("dataset_split_must_be_90_30")
    real_categories = {
        category: sum(
            case.get("source_kind") == "real_anonymized" and case.get("category") == category
            for case in rows
        )
        for category in CATEGORY_QUOTAS
    }
    if real_categories != CATEGORY_QUOTAS:
        errors.append("real_case_category_quotas_mismatch")
    split_by_conversation: dict[str, set[str]] = {}
    for case in rows:
        split_by_conversation.setdefault(_text(case.get("conversation_group"), 200), set()).add(
            _text(case.get("split"), 20)
        )
        review = case.get("review") if isinstance(case.get("review"), dict) else {}
        if review.get("status") != "approved" or not _string_list(review.get("reviewers"), limit=5):
            errors.append(f"{case.get('id')}: human_review_required")
        if case.get("risk") in {"sensitive_read", "mutation"} and len(_string_list(review.get("reviewers"), limit=5)) < 2:
            errors.append(f"{case.get('id')}: second_review_required")
    if any(len(splits) > 1 for splits in split_by_conversation.values()):
        errors.append("conversation_split_leak")
    if errors:
        raise EvaluationContractError("; ".join(dict.fromkeys(errors)))
    return {
        "success": True,
        "schema_version": DATASET_SCHEMA_VERSION,
        "total": len(rows),
        "real": real_count,
        "synthetic": synthetic_count,
        "splits": split_counts,
        "categories": real_categories,
        "dataset_hash": canonical_hash(rows),
    }


def score_candidate(case: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    component_scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
    normalized_scores: dict[str, float] = {}
    for component in SCORE_WEIGHTS:
        try:
            value = float(component_scores.get(component, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        normalized_scores[component] = max(0.0, min(value, 100.0))
    reported_failures = set(_string_list(candidate.get("automatic_failures"), limit=30, item_limit=100))
    automatic_failures = sorted(reported_failures.intersection(AUTOMATIC_FAILURE_CODES))
    weighted = sum(normalized_scores[key] * weight for key, weight in SCORE_WEIGHTS.items())
    passed = not automatic_failures
    return {
        "case_id": _text(case.get("id"), 100),
        "score": round(weighted, 4),
        "passed": passed,
        "automatic_failures": automatic_failures,
        "scores": normalized_scores,
        "weights": dict(SCORE_WEIGHTS),
    }


def build_run_request(
    cases: Iterable[Mapping[str, Any]],
    *,
    models: Iterable[str],
    repetitions: int = 1,
    purpose: str = "manual",
) -> EvaluationRunRequest:
    model_ids = tuple(dict.fromkeys(_text(model, 100) for model in models if _text(model, 100)))
    if not model_ids or any(model not in MODEL_IDS for model in model_ids):
        raise EvaluationContractError("unsupported_evaluation_model")
    case_ids = tuple(_text(case.get("id"), 100) for case in cases if _text(case.get("id"), 100))
    try:
        repeat_count = max(1, int(repetitions))
    except (TypeError, ValueError) as exc:
        raise EvaluationContractError("invalid_repetitions") from exc
    request = EvaluationRunRequest(model_ids, case_ids, repeat_count, _text(purpose, 80) or "manual")
    if request.execution_count > CAMPAIGN_EXECUTION_LIMIT:
        raise EvaluationContractError("evaluation_campaign_limit_exceeded")
    return request


def campaign_budget() -> dict[str, int]:
    plan = {
        "calibration": CALIBRATION_EXECUTIONS,
        "benchmark": BENCHMARK_EXECUTIONS,
        "stability": STABILITY_EXECUTIONS,
        "reserve": CAMPAIGN_RESERVE_EXECUTIONS,
    }
    plan["total"] = sum(plan.values())
    if plan["total"] != CAMPAIGN_EXECUTION_LIMIT:
        raise EvaluationContractError("evaluation_campaign_budget_invalid")
    return plan


def paired_model_comparison(
    results: Iterable[Mapping[str, Any]],
    *,
    baseline_model: str = "gpt-5.5",
    candidate_model: str,
    category: str,
) -> dict[str, Any]:
    """Produce a category-scoped recommendation; promotion remains manual."""

    if candidate_model not in MODEL_IDS or candidate_model == baseline_model:
        raise EvaluationContractError("invalid_candidate_model")
    if category not in CATEGORY_QUOTAS:
        raise EvaluationContractError("invalid_comparison_category")
    grouped: dict[tuple[str, int], dict[str, Mapping[str, Any]]] = {}
    for raw in results:
        row = dict(raw)
        if _text(row.get("category"), 100) != category:
            continue
        model = _text(row.get("model_effective") or row.get("model_requested"), 100)
        if model not in {baseline_model, candidate_model}:
            continue
        key = (_text(row.get("case_id"), 100), max(0, int(row.get("repetition") or 0)))
        grouped.setdefault(key, {})[model] = row
    pairs = [pair for pair in grouped.values() if baseline_model in pair and candidate_model in pair]
    if not pairs:
        raise EvaluationContractError("paired_results_missing")

    def score(row: Mapping[str, Any]) -> float:
        value = row.get("score")
        if isinstance(value, Mapping):
            value = value.get("score")
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def number(row: Mapping[str, Any], field: str) -> float:
        try:
            return max(0.0, float(row.get(field) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    deltas = [score(pair[candidate_model]) - score(pair[baseline_model]) for pair in pairs]
    mean_delta = statistics.fmean(deltas)
    standard_error = statistics.stdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else 0.0
    lower_95 = mean_delta - 1.96 * standard_error

    def has_critical_failure(row: Mapping[str, Any]) -> bool:
        score_payload = row.get("score") if isinstance(row.get("score"), Mapping) else {}
        return bool(row.get("critical_failure")) or bool(score_payload.get("automatic_failures"))

    critical_failures = sum(
        has_critical_failure(pair[candidate_model])
        for pair in pairs
    )
    candidate_tokens = statistics.fmean(number(pair[candidate_model], "tokens") for pair in pairs)
    baseline_tokens = statistics.fmean(number(pair[baseline_model], "tokens") for pair in pairs)
    candidate_p95 = _percentile([number(pair[candidate_model], "duration_ms") for pair in pairs], 0.95)
    baseline_p95 = _percentile([number(pair[baseline_model], "duration_ms") for pair in pairs], 0.95)
    token_change = _relative_change(candidate_tokens, baseline_tokens)
    p95_change = _relative_change(candidate_p95, baseline_p95)
    non_inferior = lower_95 >= -2.0
    efficiency_gain = non_inferior and (token_change <= -0.15 or p95_change <= -0.15) and mean_delta > -2.0
    quality_gain = mean_delta >= 3.0 and token_change <= 0.10 and p95_change <= 0.10
    eligible = critical_failures == 0 and non_inferior and (efficiency_gain or quality_gain)
    return {
        "category": category,
        "baseline_model": baseline_model,
        "candidate_model": candidate_model,
        "paired_samples": len(pairs),
        "mean_quality_delta": round(mean_delta, 4),
        "lower_95_quality_delta": round(lower_95, 4),
        "token_change_ratio": round(token_change, 6),
        "p95_change_ratio": round(p95_change, 6),
        "critical_failures": critical_failures,
        "eligible_for_manual_promotion": eligible,
        "automatic_promotion": False,
        "reason": "eligible_manual_review" if eligible else "promotion_gates_not_met",
    }


def _relative_change(candidate: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0 if candidate <= 0 else 1.0
    return (candidate - baseline) / baseline


def _percentile(values: Iterable[float], percentile: float) -> float:
    rows = sorted(float(item) for item in values)
    if not rows:
        return 0.0
    index = max(0, min(len(rows) - 1, math.ceil(percentile * len(rows)) - 1))
    return rows[index]


EvaluationRunner = Callable[[Mapping[str, Any], str, int], Mapping[str, Any]]


def run_with_fixtures(
    cases: Iterable[Mapping[str, Any]],
    request: EvaluationRunRequest,
    runner: EvaluationRunner,
) -> dict[str, Any]:
    rows = {str(case.get("id") or ""): dict(case) for case in cases}
    results: list[dict[str, Any]] = []
    for case_id in request.case_ids:
        case = rows.get(case_id)
        if case is None:
            raise EvaluationContractError(f"evaluation_case_missing:{case_id}")
        model_order = list(request.models)
        random.Random(canonical_hash({"case_id": case_id, "models": model_order})).shuffle(model_order)
        for model in model_order:
            for repetition in range(request.repetitions):
                raw = runner(case, model, repetition)
                candidate = dict(raw) if isinstance(raw, Mapping) else {}
                results.append(
                    {
                        "case_id": case_id,
                        "category": _text(case.get("category"), 100),
                        "model_requested": model,
                        "model_effective": _text(candidate.get("model_effective"), 100),
                        "repetition": repetition,
                        "status": _text(candidate.get("status"), 40) or "completed",
                        "duration_ms": max(0, int(candidate.get("duration_ms") or 0)),
                        "tokens": max(0, int(candidate.get("tokens") or 0)),
                        "score": score_candidate(case, candidate),
                    }
                )
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "purpose": request.purpose,
        "dataset_hash": canonical_hash(list(rows.values())),
        "execution_limit": CAMPAIGN_EXECUTION_LIMIT,
        "execution_count": len(results),
        "results": results,
    }


__all__ = [
    "AUTOMATIC_FAILURE_CODES",
    "BENCHMARK_EXECUTIONS",
    "CALIBRATION_CASES",
    "CALIBRATION_EXECUTIONS",
    "CAMPAIGN_EXECUTION_LIMIT",
    "CAMPAIGN_RESERVE_EXECUTIONS",
    "CATEGORY_QUOTAS",
    "DATASET_SCHEMA_VERSION",
    "EvaluationContractError",
    "EvaluationRunRequest",
    "HOLDOUT_CASES",
    "MODEL_IDS",
    "REAL_CASES",
    "RUN_SCHEMA_VERSION",
    "SCORE_WEIGHTS",
    "STABILITY_EXECUTIONS",
    "SYNTHETIC_CASES",
    "build_run_request",
    "campaign_budget",
    "canonical_hash",
    "default_dataset_path",
    "load_dataset",
    "paired_model_comparison",
    "run_with_fixtures",
    "score_candidate",
    "validate_case",
    "validate_published_dataset",
]
