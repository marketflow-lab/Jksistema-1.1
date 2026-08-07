import pytest

from backend.services import whatsapp_bridge
from backend.services.whatsapp import retry_policy


def _insufficient_evidence() -> dict:
    return {"schema": "jk.codex.evidence.v1", "status": "insufficient", "claim_scope": "none",
        "coverage_complete": False, "confidence": "low", "freshness": "live", "retryable": False,
        "reason": "sem evidencia decisiva", "missing_fields": ["decisive_evidence"], "sources": [],
        "attempted_fallbacks": [], "next_sources": []}


@pytest.mark.parametrize(
    ("reason", "classification"),
    [
        ("HTTP 403 token expirado", ("authentication", False)),
        ("permissão negada", ("permission", False)),
        ("campo obrigatório ausente", ("invalid_input", False)),
        ("operação read-only", ("unsupported", False)),
        ("HTTP 429 too many requests", ("rate_limited", True)),
        ("HTTP 504 timeout", ("transient_dependency", True)),
        ("cobertura incompleta", ("insufficient_evidence", False)),
        ("resultado sem conclusão", ("incomplete_result", False)),
    ],
)
def test_retry_classification_component_matches_facade(reason: str, classification: tuple[str, bool]) -> None:
    assert retry_policy.retry_classification(reason) == whatsapp_bridge._dual_retry_classification(reason) == classification


def test_retry_reason_and_bounded_delays_match_facade() -> None:
    task = {"error": " timeout "}
    result = {"summary": "fonte temporaria", "missing": ["API Bling", "API ML"]}
    assert retry_policy.retry_reason_text(task, result) == whatsapp_bridge._dual_retry_reason_text(task, result)
    assert [retry_policy.retry_delay_seconds(index, "", "job") for index in range(1, 6)] == [2, 5, 15, 15, 15]
    assert [whatsapp_bridge._dual_retry_delay_seconds(index, "", "job") for index in range(1, 6)] == [2, 5, 15, 15, 15]


def test_unique_evidence_is_trimmed_deduplicated_and_limited() -> None:
    expected = ["A", "B", "C"]
    assert retry_policy.append_unique([" A ", "B"], ["B", "C", "D"], limit=3, item_limit=10) == expected
    assert whatsapp_bridge._dual_append_unique([" A ", "B"], ["B", "C", "D"], limit=3, item_limit=10) == expected

    pending = {"verified_facts": ["Saldo 10"], "verified_sources": ["Bling"]}
    result = {
        "verified_facts": ["Saldo 10", "Full 2"],
        "sources": ["Bling", "Mercado Livre"],
    }
    retry_policy.preserve_worker_result(pending, result)
    assert pending == {
        "verified_facts": ["Saldo 10", "Full 2"],
        "verified_sources": ["Bling", "Mercado Livre"],
    }


@pytest.mark.parametrize(
    ("task", "result", "expected"),
    [
        ({"status": "canceled", "cancel_source": "whatsapp"}, {}, "canceled"),
        ({}, {"data_requests": [{"tool": "stock"}]}, "manager_request"),
        ({}, {"status": "blocked", "questions": ["Qual loja?"]}, "awaiting_input"),
        ({"status": "failed"}, {"status": "failed"}, "waiting_retry"),
        (
            {},
            {
                "status": "completed",
                "verified_facts": ["Saldo 10"],
                "sources": ["Bling"],
                "confidence": "high",
                "evidence_sufficient": True,
                "coverage_complete": True,
            },
            "completed",
        ),
        (
            {},
            {
                "status": "completed",
                "summary": "Nenhum registro encontrado",
                "verified_facts": ["Zero registros"],
                "sources": ["Bling"],
                "confidence": "high",
                "evidence_sufficient": True,
                "coverage_complete": False,
            },
            "waiting_retry",
        ),
        (
            {"tool_results_summary": [{"evidence": _insufficient_evidence()}]},
            {"status": "completed"},
            "waiting_retry",
        ),
    ],
)
def test_worker_disposition_component_matches_facade(task: dict, result: dict, expected: str) -> None:
    assert retry_policy.worker_disposition(task, result) == whatsapp_bridge._dual_worker_disposition(task, result) == expected
