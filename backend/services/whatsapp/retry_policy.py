"""Bounded retry classification and worker-result disposition."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


WHATSAPP_RETRY_DELAYS_SECONDS = (2, 5, 15)
WHATSAPP_MAX_RETRY_ATTEMPTS = 3


def retry_reason_text(task: dict[str, Any], result: dict[str, Any]) -> str:
    parts = [
        str(task.get("error") or ""),
        str(result.get("summary") or ""),
        " ".join(str(item or "") for item in list(result.get("missing") or [])),
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:2000]


def retry_is_auth_error(reason: Any) -> bool:
    text = unicodedata.normalize("NFKD", str(reason or "")).encode("ascii", "ignore").decode("ascii").lower()
    return bool(
        re.search(
            r"(?:\b401\b|\b403\b|unauthori[sz]ed|forbidden|token.*expir|autentic|credencial|reconect)",
            text,
        )
    )


def retry_classification(reason: Any) -> tuple[str, bool]:
    text = unicodedata.normalize("NFKD", str(reason or "")).encode("ascii", "ignore").decode("ascii").lower()
    if retry_is_auth_error(text):
        return "authentication", False
    if re.search(r"\b(permission|permissao|acesso negado|not allowed|nao autorizado)\b", text):
        return "permission", False
    if re.search(r"\b(invalid|invalido|entrada incompleta|missing input|campo obrigatorio|store_required)\b", text):
        return "invalid_input", False
    if re.search(r"\b(unsupported|nao suportad|somente consulta|read.?only|executor seguro)\b", text):
        return "unsupported", False
    if re.search(r"\b(429|rate.?limit|too many requests)\b", text):
        return "rate_limited", True
    if re.search(r"\b(timeout|timed out|connection|conexao|temporar\w*|remote.*closed|502|503|504|5\d\d)\b", text):
        return "transient_dependency", True
    if re.search(r"\b(evidencia|evidence|cobertura|coverage|resultado incompleto|dados insuficientes|fonte indisponivel)\b", text):
        return "insufficient_evidence", False
    return "incomplete_result", False


def retry_delay_seconds(
    retry_count: int,
    reason: Any,
    key: Any = "",
    *,
    delays: tuple[int, ...] = WHATSAPP_RETRY_DELAYS_SECONDS,
) -> int:
    del reason, key
    index = max(0, min(int(retry_count or 1) - 1, len(delays) - 1))
    return int(delays[index])


def append_unique(target: list[str], values: Any, *, limit: int, item_limit: int) -> list[str]:
    output = [str(item or "").strip()[:item_limit] for item in list(target or []) if str(item or "").strip()]
    for value in list(values or []):
        text = str(value or "").strip()[:item_limit]
        if text and text not in output:
            output.append(text)
    return output[:limit]


def preserve_worker_result(pending: dict[str, Any], result: dict[str, Any]) -> None:
    pending["verified_facts"] = append_unique(
        list(pending.get("verified_facts") or []),
        result.get("verified_facts"),
        limit=30,
        item_limit=2000,
    )
    pending["verified_sources"] = append_unique(
        list(pending.get("verified_sources") or []),
        result.get("sources"),
        limit=30,
        item_limit=1000,
    )


def worker_disposition(task: dict[str, Any], result: dict[str, Any]) -> str:
    task_status = str(task.get("status") or "")
    if task_status == "canceled" and str(task.get("cancel_source") or "") in {
        "whatsapp",
        "whatsapp_conversation_agent",
    }:
        return "canceled"
    result_status = str(result.get("status") or "failed")
    if list(result.get("data_requests") or []):
        return "manager_request"
    if result_status == "blocked" and list(result.get("questions") or []):
        return "awaiting_input"
    if task_status in {"partial", "failed", "canceled"}:
        return "waiting_retry"
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    if verification.get("confirmed") is False or str(verification.get("status") or "") == "partial":
        return "waiting_retry"
    validations = [
        item.get("tool_validation")
        for item in list(task.get("tool_results_summary") or [])
        if isinstance(item, dict) and isinstance(item.get("tool_validation"), dict)
    ]
    consolidated_sufficient = bool(
        verification.get("confirmed") is True
        or any(item.get("dados_suficientes") is True for item in validations)
    )
    if validations and not consolidated_sufficient:
        return "waiting_retry"
    if result_status == "completed":
        facts = [str(item or "").strip() for item in list(result.get("verified_facts") or []) if str(item or "").strip()]
        sources = [str(item or "").strip() for item in list(result.get("sources") or []) if str(item or "").strip()]
        confidence = str(result.get("confidence") or "unknown")
        if result.get("evidence_sufficient") is not True or not facts or not sources or confidence not in {"high", "medium"}:
            return "waiting_retry"
        absence_text = unicodedata.normalize(
            "NFKD",
            " ".join([str(result.get("summary") or ""), *facts]),
        ).encode("ascii", "ignore").decode("ascii").lower()
        confirms_absence = bool(
            re.search(
                r"\b(?:nenhum|nenhuma|nao (?:foi )?encontrad[oa]s?|zero registros?|sem registros?|inexistente|ausencia confirmada)\b",
                absence_text,
            )
        )
        if confirms_absence and result.get("coverage_complete") is not True:
            return "waiting_retry"
        return "completed"
    return "waiting_retry"
