"""Pure helpers for agent-owned evidence replanning and plan reuse."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def call_signature(call: Any) -> str:
    """Identify a materialized read-only call without conversational filler."""

    value = call if isinstance(call, dict) else {}
    arguments = value.get("arguments") if isinstance(value.get("arguments"), dict) else {}
    semantic_arguments = {
        str(key): item
        for key, item in arguments.items()
        if str(key) not in {"message", "mode", "force_refresh", "limite", "limit"}
    }
    encoded = json.dumps(
        {"tool_id": str(value.get("tool_id") or "").strip(), "arguments": semantic_arguments},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def context_revision(conversation_anchors: Any) -> int:
    anchors = conversation_anchors if isinstance(conversation_anchors, dict) else {}
    resolved = anchors.get("resolved_context") if isinstance(anchors.get("resolved_context"), dict) else {}
    try:
        return max(0, int(resolved.get("revision") or 0))
    except (TypeError, ValueError):
        return 0


def gap_key(pending: dict[str, Any]) -> str:
    requests = list(pending.get("manager_data_requests") or [])[:12]
    if not requests:
        return "initial"
    encoded = json.dumps(requests, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def register_plan_attempt(pending: dict[str, Any]) -> None:
    """Allow one replan per data gap; tool retries reuse the stored plan."""

    current_gap = gap_key(pending)
    if (
        isinstance(pending.get("data_selection_raw_plan"), dict)
        and pending.get("data_selection_raw_plan")
        and str(pending.get("data_selection_gap_key") or "") == current_gap
    ):
        return
    attempt_counts = {
        str(key): max(0, int(value or 0))
        for key, value in dict(pending.get("data_selection_attempts_by_gap") or {}).items()
        if re.fullmatch(r"(?:initial|[a-f0-9]{64})", str(key or ""))
    }
    next_attempt = attempt_counts.get(current_gap, 0) + 1
    if next_attempt > 2:
        raise RuntimeError("data_selection_replan_limit")
    attempt_counts[current_gap] = next_attempt
    pending["data_selection_attempts_by_gap"] = dict(list(attempt_counts.items())[-12:])


def apply_retry_state(
    pending: dict[str, Any], *, reason: str, count: int, error_class: str,
    retryable: bool, max_attempts: int, delay_seconds: float, now_epoch: float,
) -> None:
    common = {
        "manager_retry_count": count,
        "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
        "retry_policy": "bounded",
    }
    if not retryable or count > max_attempts:
        pending.update({
            **common,
            "manager_state": "partial", "data_selection_state": "partial", "job_state": "partial",
            "manager_next_retry_at_epoch": 0, "next_retry_at_epoch": 0,
            "terminal_reason": str(reason or "limite_de_tentativas_atingido")[:1000],
            "terminal_error_class": error_class,
        })
        return
    next_epoch = now_epoch + max(0.0, float(delay_seconds or 0))
    pending.update({
        **common,
        "manager_state": "waiting_retry", "data_selection_state": "waiting_retry", "job_state": "waiting_retry",
        "manager_next_retry_at_epoch": next_epoch, "next_retry_at_epoch": next_epoch,
    })


def reusable_raw_plan(pending: dict[str, Any], *, gap_key: str, revision: int) -> dict[str, Any]:
    try:
        stored_revision = max(0, int(pending.get("data_selection_context_revision") or 0))
    except (TypeError, ValueError):
        stored_revision = -1
    value = pending.get("data_selection_raw_plan")
    if (
        isinstance(value, dict)
        and str(pending.get("data_selection_gap_key") or "") == gap_key
        and stored_revision == revision
    ):
        return value
    return {}


def prepare_evidence_replan(pending: dict[str, Any], evidence: dict[str, Any]) -> bool:
    """Record one insufficient-evidence gap for a different agent plan."""

    if max(0, int(pending.get("manager_evidence_replan_count") or 0)) >= 1:
        return False
    validations = [
        item
        for item in list(evidence.get("validations") or [])[:12]
        if isinstance(item, dict)
        and item.get("required") is True
        and item.get("dados_suficientes") is not True
    ]
    if not validations or any(
        str(item.get("error_class") or "").strip().lower()
        in {"authentication", "permission", "invalid_input"}
        for item in validations
    ):
        return False
    previous_plan = (
        pending.get("data_selection_plan")
        if isinstance(pending.get("data_selection_plan"), dict)
        else pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    )
    attempted_calls = [
        call for call in list(previous_plan.get("tool_calls") or [])[:12]
        if isinstance(call, dict) and str(call.get("tool_id") or "").strip()
    ]
    pending["manager_evidence_replan_count"] = 1
    pending["manager_data_requests"] = [
        {
            "kind": "alternate_source_or_refined_arguments",
            "tool_id": str(item.get("tool_id") or "")[:100],
            "reason": str(item.get("motivo") or "insufficient_evidence")[:500],
        }
        for item in validations
    ]
    pending["manager_previously_attempted_tools"] = list(
        dict.fromkeys(str(call.get("tool_id") or "") for call in attempted_calls)
    )
    pending["manager_previously_attempted_call_signatures"] = [
        call_signature(call) for call in attempted_calls
    ]
    pending.pop("data_selection_raw_plan", None)
    pending.pop("data_selection_gap_key", None)
    return True


def reject_repeated_calls(plan: dict[str, Any], blocked_values: Any) -> None:
    blocked = {
        str(item or "").strip()
        for item in list(blocked_values or [])[:12]
        if str(item or "").strip()
    }
    if not blocked:
        return
    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)]
    plan["tool_calls"] = [item for item in calls if call_signature(item) not in blocked]
    if calls and not plan["tool_calls"] and str(plan.get("action") or "") == "collect":
        raise RuntimeError("data_selection_repeated_plan")


__all__ = [
    "call_signature",
    "context_revision",
    "apply_retry_state",
    "gap_key",
    "prepare_evidence_replan",
    "register_plan_attempt",
    "reject_repeated_calls",
    "reusable_raw_plan",
]
