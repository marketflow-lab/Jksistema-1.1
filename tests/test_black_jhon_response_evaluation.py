"""Offline contract evaluation for Black Jhon WhatsApp responses.

This suite does not call a model, the network, the installed runtime, or any
production module.  It validates deterministic response contracts and provides
an injectable runner for evaluating future candidate dictionaries.  A passing
result is not a claim about live-model accuracy.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "black_jhon_response_cases.jsonl"
OFFLINE_CONTRACT = "black-jhon-whatsapp-response-eval.offline.v1"
LIVE_MODEL_ACCURACY_CLAIMED = False
NETWORK_ACCESS_REQUIRED = False

CATEGORY_QUOTAS = {
    "context_pronouns": 6,
    "store_switch_isolation": 6,
    "sku_mlb": 6,
    "reports": 6,
    "active_tasks": 6,
    "partial_coverage_evidence": 6,
    "casual_conversation": 6,
    "thread_prompt": 6,
    "fallback": 6,
    "rag_relaxed": 6,
}

TOP_LEVEL_KEYS = {"id", "category", "input", "evidence", "expected", "offline_candidate"}
EVIDENCE_KEYS = {"status", "sources", "facts", "numbers"}
EXPECTED_KEYS = {
    "route",
    "store",
    "subject",
    "tools",
    "contains",
    "excludes",
    "identifier_numbers",
    "factual",
    "partial",
    "rag_relaxed",
}
CANDIDATE_KEYS = {"route", "store", "subject", "tools", "text", "partial", "rag_relaxed"}
EVIDENCE_STATUSES = {"complete", "partial", "none", "not_required"}
CASE_ID_RE = re.compile(r"^[a-z]+-\d{2}$")
NUMBER_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:R\$\s*)?\d+(?:\.\d{3})*(?:,\d+)?(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def load_cases() -> list[dict[str, Any]]:
    """Load the local JSONL collection without importing application code."""

    raw_lines = FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    if any(not line.strip() for line in raw_lines):
        raise AssertionError("the JSONL fixture must not contain blank lines")
    return [json.loads(line) for line in raw_lines]


def _all_strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _normal_number(value: str) -> str:
    return re.sub(r"\s+", "", value.lower()).replace(".", "")


def _number_literals(text: str) -> set[str]:
    return {_normal_number(match.group(0)) for match in NUMBER_LITERAL_RE.finditer(text)}


def validate_case_structure(case: Mapping[str, Any]) -> list[str]:
    """Return deterministic schema errors for one evaluation case."""

    errors: list[str] = []
    case_id = str(case.get("id") or "<missing-id>")

    if set(case) != TOP_LEVEL_KEYS:
        errors.append(f"{case_id}: top-level keys differ from the offline schema")
        return errors
    if not CASE_ID_RE.fullmatch(case_id):
        errors.append(f"{case_id}: invalid case id")
    if case["category"] not in CATEGORY_QUOTAS:
        errors.append(f"{case_id}: unknown category {case['category']!r}")

    input_data = case["input"]
    if not isinstance(input_data, dict) or not isinstance(input_data.get("message"), str):
        errors.append(f"{case_id}: input.message must be a string")
    elif not input_data["message"].strip():
        errors.append(f"{case_id}: input.message must not be empty")
    if not isinstance(input_data, dict) or input_data.get("active_store") is not None and not isinstance(
        input_data.get("active_store"), str
    ):
        errors.append(f"{case_id}: input.active_store must be a string or null")
    if not isinstance(input_data, dict) or not _all_strings(input_data.get("known_stores", [])):
        errors.append(f"{case_id}: input.known_stores must contain non-empty strings")
    if not isinstance(input_data, dict) or not _all_strings(input_data.get("history", [])):
        errors.append(f"{case_id}: input.history must contain non-empty strings")

    evidence = case["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS:
        errors.append(f"{case_id}: evidence keys differ from the offline schema")
    else:
        if evidence["status"] not in EVIDENCE_STATUSES:
            errors.append(f"{case_id}: invalid evidence status")
        for field in ("sources", "facts", "numbers"):
            if not _all_strings(evidence[field]):
                errors.append(f"{case_id}: evidence.{field} must contain non-empty strings")

    expected = case["expected"]
    if not isinstance(expected, dict) or set(expected) != EXPECTED_KEYS:
        errors.append(f"{case_id}: expected keys differ from the offline schema")
    else:
        for field in ("route",):
            if not isinstance(expected[field], str) or not expected[field]:
                errors.append(f"{case_id}: expected.{field} must be a non-empty string")
        for field in ("tools", "contains", "excludes", "identifier_numbers"):
            if not _all_strings(expected[field]):
                errors.append(f"{case_id}: expected.{field} must contain non-empty strings")
        for field in ("factual", "partial", "rag_relaxed"):
            if not isinstance(expected[field], bool):
                errors.append(f"{case_id}: expected.{field} must be boolean")
        for field in ("store", "subject"):
            if expected[field] is not None and not isinstance(expected[field], str):
                errors.append(f"{case_id}: expected.{field} must be a string or null")

        status = evidence.get("status") if isinstance(evidence, dict) else None
        if expected.get("factual") and status in {"partial", "none"} and not expected.get("partial"):
            errors.append(f"{case_id}: incomplete factual evidence must expect partial disclosure")
        if expected.get("factual") and status == "complete" and expected.get("partial"):
            errors.append(f"{case_id}: complete factual evidence cannot expect partial disclosure")
        if not expected.get("factual") and status != "not_required":
            errors.append(f"{case_id}: non-factual cases must mark evidence not_required")

    candidate = case["offline_candidate"]
    if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_KEYS:
        errors.append(f"{case_id}: offline_candidate keys differ from the offline schema")

    return errors


def evaluate_case(case: Mapping[str, Any], candidate: Mapping[str, Any] | None = None) -> list[str]:
    """Evaluate a supplied candidate using only local, deterministic checks."""

    errors = validate_case_structure(case)
    case_id = str(case.get("id") or "<missing-id>")
    if errors:
        return errors

    expected = case["expected"]
    evidence = case["evidence"]
    actual = candidate if candidate is not None else case["offline_candidate"]
    if not isinstance(actual, Mapping):
        return [f"{case_id}: candidate must be a mapping"]

    missing = CANDIDATE_KEYS.difference(actual)
    if missing:
        return [f"{case_id}: candidate is missing {sorted(missing)}"]

    for field in ("route", "store", "subject", "tools", "partial", "rag_relaxed"):
        if actual[field] != expected[field]:
            errors.append(
                f"{case_id}: candidate {field}={actual[field]!r}, expected {expected[field]!r}"
            )

    text = actual.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append(f"{case_id}: candidate text must be a non-empty string")
        return errors
    folded_text = text.casefold()

    for snippet in expected["contains"]:
        if snippet.casefold() not in folded_text:
            errors.append(f"{case_id}: required text is absent: {snippet!r}")
    for snippet in expected["excludes"]:
        if snippet.casefold() in folded_text:
            errors.append(f"{case_id}: forbidden text is present: {snippet!r}")

    if case["category"] == "store_switch_isolation":
        selected_store = expected["store"]
        for known_store in case["input"]["known_stores"]:
            if known_store != selected_store and known_store.casefold() in folded_text:
                errors.append(f"{case_id}: response leaked another store: {known_store!r}")

    allowed_numbers = {
        _normal_number(value)
        for value in [*evidence["numbers"], *expected["identifier_numbers"]]
    }
    unexpected_numbers = sorted(_number_literals(text).difference(allowed_numbers))
    if unexpected_numbers:
        errors.append(f"{case_id}: numeric claims lack evidence: {unexpected_numbers}")

    if expected["factual"] and evidence["status"] in {"partial", "none"} and not actual["partial"]:
        errors.append(f"{case_id}: incomplete evidence was presented as complete")

    return errors


def run_offline_evaluation(
    candidates_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the fixture contract; optional candidates make the runner injectable."""

    failures: dict[str, list[str]] = {}
    cases = load_cases()
    supplied = candidates_by_id or {}
    for case in cases:
        errors = evaluate_case(case, supplied.get(case["id"]))
        if errors:
            failures[case["id"]] = errors
    passed = len(cases) - len(failures)
    return {
        "contract": OFFLINE_CONTRACT,
        "offline": True,
        "network_used": NETWORK_ACCESS_REQUIRED,
        "live_model_accuracy_claimed": LIVE_MODEL_ACCURACY_CLAIMED,
        "total": len(cases),
        "passed": passed,
        "failed": len(failures),
        "contract_pass_rate": passed / len(cases) if cases else 0.0,
        "failures": failures,
    }


@pytest.fixture(scope="module")
def cases() -> list[dict[str, Any]]:
    return load_cases()


def test_fixture_has_exactly_sixty_cases_and_balanced_categories(cases: list[dict[str, Any]]) -> None:
    assert len(cases) == 60
    assert Counter(case["category"] for case in cases) == Counter(CATEGORY_QUOTAS)
    assert len({case["id"] for case in cases}) == 60


def test_fixture_schema_is_valid_and_explicitly_offline(cases: list[dict[str, Any]]) -> None:
    errors = [error for case in cases for error in validate_case_structure(case)]
    assert errors == []
    assert OFFLINE_CONTRACT.endswith(".offline.v1")
    assert NETWORK_ACCESS_REQUIRED is False
    assert LIVE_MODEL_ACCURACY_CLAIMED is False


def test_required_behavioral_axes_are_represented(cases: list[dict[str, Any]]) -> None:
    by_category = {
        category: [case for case in cases if case["category"] == category]
        for category in CATEGORY_QUOTAS
    }

    assert any(case["expected"]["subject"] is None for case in by_category["context_pronouns"])
    assert any(
        case["input"]["active_store"] != case["expected"]["store"]
        for case in by_category["store_switch_isolation"]
    )
    assert {case["expected"]["route"] for case in by_category["sku_mlb"]} >= {
        "sku_mlb_lookup",
        "mlb_lookup",
        "full_stock_lookup",
    }
    assert {case["evidence"]["status"] for case in by_category["reports"]} >= {"complete", "partial"}
    assert {case["expected"]["route"] for case in by_category["active_tasks"]} >= {
        "active_tasks",
        "active_task_continue",
        "mutation_blocked",
    }
    assert {case["evidence"]["status"] for case in by_category["partial_coverage_evidence"]} == {
        "complete",
        "partial",
        "none",
    }
    assert all(case["expected"]["tools"] == [] for case in by_category["casual_conversation"])
    assert any("untrusted_context" in case["input"] for case in by_category["thread_prompt"])
    assert any("other_thread_context" in case["input"] for case in by_category["thread_prompt"])
    assert any(case["expected"]["route"] == "prompt_protection" for case in by_category["thread_prompt"])
    assert all(len(case["expected"]["tools"]) >= 2 for case in by_category["fallback"])
    assert all(case["expected"]["rag_relaxed"] for case in by_category["rag_relaxed"])
    assert {case["expected"]["factual"] for case in by_category["rag_relaxed"]} == {False, True}


def test_all_reference_candidates_pass_the_offline_contract(cases: list[dict[str, Any]]) -> None:
    failures = {case["id"]: evaluate_case(case) for case in cases}
    assert {case_id: errors for case_id, errors in failures.items() if errors} == {}


def test_store_isolation_rejects_metadata_switch_and_cross_store_text(
    cases: list[dict[str, Any]],
) -> None:
    case = next(case for case in cases if case["id"] == "store-01")
    candidate = copy.deepcopy(case["offline_candidate"])
    candidate["store"] = "JK Pecas"
    candidate["text"] += " A JK Pecas tambem foi somada."

    errors = evaluate_case(case, candidate)

    assert any("candidate store" in error for error in errors)
    assert any("leaked another store" in error for error in errors)


def test_evidence_gating_rejects_unverified_numbers_and_false_zero(
    cases: list[dict[str, Any]],
) -> None:
    no_evidence = next(case for case in cases if case["id"] == "evidence-01")
    invented = copy.deepcopy(no_evidence["offline_candidate"])
    invented["text"] += " Mesmo assim, vou usar 37 unidades."
    assert any("numeric claims lack evidence" in error for error in evaluate_case(no_evidence, invented))

    incomplete_empty = next(case for case in cases if case["id"] == "evidence-03")
    false_zero = copy.deepcopy(incomplete_empty["offline_candidate"])
    false_zero["text"] = "Nenhum pedido foi encontrado."
    errors = evaluate_case(incomplete_empty, false_zero)
    assert any("required text is absent" in error or "forbidden text is present" in error for error in errors)


def test_injectable_runner_reports_contract_failures_without_model_claims(
    cases: list[dict[str, Any]],
) -> None:
    baseline = run_offline_evaluation()
    assert baseline == {
        "contract": OFFLINE_CONTRACT,
        "offline": True,
        "network_used": False,
        "live_model_accuracy_claimed": False,
        "total": 60,
        "passed": 60,
        "failed": 0,
        "contract_pass_rate": 1.0,
        "failures": {},
    }

    broken = copy.deepcopy(cases[0]["offline_candidate"])
    broken["route"] = "conversation"
    result = run_offline_evaluation({cases[0]["id"]: broken})
    assert result["total"] == 60
    assert result["passed"] == 59
    assert result["failed"] == 1
    assert result["contract_pass_rate"] == pytest.approx(59 / 60)
    assert list(result["failures"]) == [cases[0]["id"]]
    assert result["live_model_accuracy_claimed"] is False


if __name__ == "__main__":
    summary = run_offline_evaluation()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(1 if summary["failed"] else 0)
