"""Warm Codex conversation lane for the Black Jhon WhatsApp bridge.

Only Codex produces semantic decisions and user-facing free text here.  The
bridge remains responsible for authentication, persistence, queueing and hard
security rules.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from typing import Any, Optional

from backend.services.whatsapp import black_jhon_prompting


CONVERSATION_ACTIONS = (
    "reply",
    "delegate",
    "steer",
    "queue",
    "cancel_job",
    "request_information",
    "wait",
)

_BASE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "reply_text",
        "job_title",
        "job_prompt",
        "related_job_id",
        "needs_user_input",
        "requires_web",
        "resolved_context",
        "subtasks",
    ],
    "properties": {
        "action": {"type": "string", "enum": list(CONVERSATION_ACTIONS)},
        "reply_text": {"type": "string", "maxLength": 3500},
        "job_title": {"type": "string", "maxLength": 180},
        "job_prompt": {"type": "string", "maxLength": 12000},
        "related_job_id": {"type": "string", "maxLength": 100},
        "needs_user_input": {"type": "boolean"},
        "requires_web": {"type": "boolean"},
        "resolved_context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["store", "store_mode", "sku", "mlb", "period", "applied_fields", "clear_fields"],
            "properties": {
                "store": {"type": "string", "maxLength": 200},
                "store_mode": {"type": "string", "enum": ["none", "single", "all"]},
                "sku": {"type": "string", "maxLength": 100},
                "mlb": {"type": "string", "maxLength": 60},
                "period": {"type": "string", "maxLength": 160},
                "applied_fields": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string", "enum": ["store", "store_mode", "sku", "mlb", "period"]},
                },
                "clear_fields": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string", "enum": ["store", "sku", "mlb", "period"]},
                },
            },
        },
        "subtasks": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "prompt", "requires_web", "reasoning_effort"],
                "properties": {
                    "title": {"type": "string", "maxLength": 180},
                    "prompt": {"type": "string", "maxLength": 12000},
                    "requires_web": {"type": "boolean"},
                    "reasoning_effort": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "xhigh"],
                    },
                },
            },
        },
    },
}

CONVERSATION_DECISION_V2_SCHEMA = black_jhon_prompting.conversation_decision_v2_schema(_BASE_DECISION_SCHEMA)
CONVERSATION_DECISION_V3_SCHEMA = black_jhon_prompting.conversation_decision_v3_schema(_BASE_DECISION_SCHEMA)
# The active output contract is V3. ``normalize_decision`` still accepts and
# normalizes legacy V1/V2 payloads when no V3 contract was requested.
DECISION_SCHEMA = CONVERSATION_DECISION_V3_SCHEMA
EVIDENCE_ENVELOPE_V2_SCHEMA = black_jhon_prompting.EVIDENCE_ENVELOPE_V2_SCHEMA
RETRIEVAL_RESULT_V2_SCHEMA = black_jhon_prompting.RETRIEVAL_RESULT_V2_SCHEMA

_DECISION_CONTRACT_ENV = "JK_BLACK_JHON_DECISION_CONTRACT"


def decision_contract_mode(value: Any = None) -> str:
    """Resolve the server-owned decision contract with a safe V2 fallback."""

    raw_value = os.getenv(_DECISION_CONTRACT_ENV) if value is None else value
    if raw_value is None or not str(raw_value).strip():
        return "v3"
    normalized = str(raw_value).strip().casefold()
    if normalized in {"v3", "3", black_jhon_prompting.CONVERSATION_DECISION_V3.casefold()}:
        return "v3"
    if normalized in {
        "v2",
        "2",
        "legacy",
        "off",
        "false",
        "0",
        black_jhon_prompting.CONVERSATION_DECISION_V2.casefold(),
    }:
        return "v2"
    return "v2"


def _decision_runtime_contract(value: Any = None) -> dict[str, Any]:
    mode = decision_contract_mode(value)
    if mode == "v3":
        return {
            "mode": mode,
            "schema": CONVERSATION_DECISION_V3_SCHEMA,
            "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V3,
            "prompt_contract": black_jhon_prompting.prompt_contract_v3_diagnostics(),
        }
    return {
        "mode": "v2",
        "schema": CONVERSATION_DECISION_V2_SCHEMA,
        "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V2,
        "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
    }

WORKER_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "verified_facts", "sources", "confidence", "missing", "questions"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked", "failed"]},
        "summary": {"type": "string"},
        "verified_facts": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "evidence_sufficient": {"type": "boolean"},
        "coverage_complete": {"type": "boolean"},
        "missing": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "data_requests": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["need", "fields", "reason"],
                "properties": {
                    "need": {"type": "string", "maxLength": 500},
                    "fields": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "reason": {"type": "string", "maxLength": 1000},
                },
            },
        },
    },
}


FUNCTION_MANAGER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "store",
        "store_mode",
        "sku",
        "item_id",
        "requested_fields",
        "tool_calls",
        "requires_sol",
        "requires_web",
        "missing_user_fields",
        "reason",
    ],
    "properties": {
        "intent": {"type": "string", "maxLength": 120},
        "store": {"type": "string", "maxLength": 180},
        "store_mode": {"type": "string", "enum": ["single", "all", "none"]},
        "sku": {"type": "string", "maxLength": 100},
        "item_id": {"type": "string", "maxLength": 60},
        "requested_fields": {"type": "array", "maxItems": 30, "items": {"type": "string"}},
        "tool_calls": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool_id", "arguments", "required", "reason"],
                "properties": {
                    "tool_id": {"type": "string", "maxLength": 100},
                    "arguments": {"type": "string", "maxLength": 4000},
                    "required": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 500},
                },
            },
        },
        "requires_sol": {"type": "boolean"},
        "requires_web": {"type": "boolean"},
        "missing_user_fields": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
        "reason": {"type": "string", "maxLength": 1200},
    },
}


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


_RESPONSE_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+"
)


def _strip_response_emojis(value: Any, limit: int = 3500) -> str:
    text = _RESPONSE_EMOJI_RE.sub("", str(value or "").replace("\x00", ""))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    return text.strip()[:limit]


def _compact_whatsapp_reply(value: Any, limit: int) -> str:
    """Limit a reply at a natural boundary instead of leaking a long dump."""

    text = _strip_response_emojis(value, 3500)
    if len(text) <= limit:
        return text
    candidate = text[: max(1, limit)].rstrip()
    boundaries = [candidate.rfind(marker) for marker in ("\n\n", "\n", ". ", "? ", "! ", "; ")]
    boundary = max(boundaries)
    if boundary >= max(80, int(limit * 0.55)):
        candidate = candidate[: boundary + (1 if candidate[boundary:boundary + 1] in ".?!" else 0)]
    else:
        word_boundary = candidate.rfind(" ")
        if word_boundary >= max(40, int(limit * 0.7)):
            candidate = candidate[:word_boundary]
    return candidate.rstrip(" ,;:-") + "..."


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = _clean_text(value, 30000)
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_context_operations(value: Any) -> list[dict[str, str]]:
    fields = {"store", "store_mode", "sku", "mlb", "period"}
    operations = {"keep", "set", "clear"}
    sources = {"current_turn", "conversation_memory", "quoted_context"}
    confidences = {"high", "medium", "low"}
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in list(value or [])[:10]:
        if not isinstance(item, dict):
            continue
        field = _clean_text(item.get("field"), 20).lower()
        operation = _clean_text(item.get("operation"), 20).lower()
        if field not in fields or operation not in operations or field in seen:
            continue
        raw_value = _clean_text(item.get("value"), 200)
        if operation == "set" and not raw_value:
            continue
        source = _clean_text(item.get("source"), 30).lower()
        confidence = _clean_text(item.get("confidence"), 20).lower()
        result.append(
            {
                "field": field,
                "operation": operation,
                "value": raw_value if operation == "set" else "",
                "source": source if source in sources else (
                    "conversation_memory" if operation == "keep" else "current_turn"
                ),
                "confidence": confidence if confidence in confidences else "medium",
            }
        )
        seen.add(field)
    return result


def _legacy_context_operations(raw_context: dict[str, Any]) -> list[dict[str, str]]:
    operations: list[dict[str, str]] = []
    cleared = {
        str(item or "").strip().lower()
        for item in list(raw_context.get("clear_fields") or [])[:5]
    }
    for field in ("store", "store_mode", "sku", "mlb", "period"):
        if field in cleared or (field == "store_mode" and "store" in cleared):
            operations.append(
                {
                    "field": field,
                    "operation": "clear",
                    "value": "",
                    "source": "current_turn",
                    "confidence": "medium",
                }
            )
            continue
        applied = {
            str(item or "").strip().lower()
            for item in list(raw_context.get("applied_fields") or [])[:5]
        }
        if field not in applied:
            continue
        content = _clean_text(raw_context.get(field), 200)
        operations.append(
            {
                "field": field,
                "operation": "set" if content else "keep",
                "value": content,
                "source": "current_turn" if content else "conversation_memory",
                "confidence": "medium",
            }
        )
    return operations[:10]


def normalize_decision(
    value: Any,
    *,
    event_type: str,
    contract_version: Any = None,
) -> dict[str, Any]:
    parsed = parse_json_object(value)
    action = _clean_text(parsed.get("action"), 40).lower()
    if action not in CONVERSATION_ACTIONS:
        raise RuntimeError("conversation_agent_invalid_action")
    action_limits = {
        "waiting_tick": 320,
        "worker_partial": 900,
        "worker_result": 3500,
    }
    reply_limit = action_limits.get(event_type, 900)
    if action == "request_information":
        reply_limit = min(reply_limit, 420)
    elif action in {"delegate", "queue", "steer", "cancel_job"}:
        reply_limit = min(reply_limit, 600)
    reply = _compact_whatsapp_reply(parsed.get("reply_text"), reply_limit)
    if not reply and not (event_type == "waiting_tick" and action == "wait"):
        raise RuntimeError("conversation_agent_empty_reply")
    if event_type == "waiting_tick" and action not in {"reply", "request_information", "wait"}:
        raise RuntimeError("conversation_agent_invalid_event_action")
    if event_type in {"worker_result", "worker_partial"} and action not in {"reply", "request_information"}:
        raise RuntimeError("conversation_agent_invalid_event_action")
    if event_type == "user_message" and action == "wait":
        raise RuntimeError("conversation_agent_invalid_event_action")
    raw_task = parsed.get("task") if isinstance(parsed.get("task"), dict) else {}
    job_title = _clean_text(raw_task.get("title") or parsed.get("job_title"), 180)
    job_prompt = _clean_text(raw_task.get("prompt") or parsed.get("job_prompt"), 12000)
    requires_web = bool(raw_task.get("requires_web")) if "requires_web" in raw_task else bool(parsed.get("requires_web"))
    task_reasoning = _clean_text(raw_task.get("reasoning_effort"), 20).lower()
    if task_reasoning not in {"low", "medium", "high", "xhigh"}:
        task_reasoning = "low"
    if action in {"delegate", "queue"} and not job_prompt:
        raise RuntimeError("conversation_agent_empty_job_prompt")
    subtasks: list[dict[str, Any]] = []
    for item in list(parsed.get("subtasks") or [])[:6]:
        if not isinstance(item, dict):
            continue
        prompt = _clean_text(item.get("prompt"), 12000)
        if not prompt:
            continue
        subtasks.append(
            {
                "title": _clean_text(item.get("title"), 180) or _clean_text(parsed.get("job_title"), 180),
                "prompt": prompt,
                "requires_web": bool(item.get("requires_web")),
                "reasoning_effort": "low",
            }
        )
    raw_context = parsed.get("resolved_context") if isinstance(parsed.get("resolved_context"), dict) else {}
    raw_mlb = re.sub(r"[^A-Za-z0-9]", "", _clean_text(raw_context.get("mlb"), 60)).upper()
    applied_fields = [
        str(item or "").strip().lower()
        for item in list(raw_context.get("applied_fields") or [])[:5]
        if str(item or "").strip().lower() in {"store", "store_mode", "sku", "mlb", "period"}
    ]
    resolved_context = {
        "store": _clean_text(raw_context.get("store"), 200),
        "store_mode": (
            str(raw_context.get("store_mode") or "none").strip().lower()
            if str(raw_context.get("store_mode") or "none").strip().lower() in {"none", "single", "all"}
            else "none"
        ),
        "sku": _clean_text(raw_context.get("sku"), 100),
        "mlb": raw_mlb if re.fullmatch(r"MLB\d{6,}", raw_mlb) else "",
        "period": _clean_text(raw_context.get("period"), 160),
        "applied_fields": applied_fields,
        "clear_fields": [
            str(item or "").strip().lower()
            for item in list(raw_context.get("clear_fields") or [])[:5]
            if str(item or "").strip().lower() in {"store", "sku", "mlb", "period"}
        ],
        "provided_fields": list(applied_fields),
    }
    source_schema = _clean_text(parsed.get("schema_version"), 120)
    context_operations = _normalize_context_operations(parsed.get("context_operations"))
    if not context_operations and source_schema != black_jhon_prompting.CONVERSATION_DECISION_V3:
        context_operations = _legacy_context_operations(raw_context)
    missing_fields = [
        _clean_text(item, 200)
        for item in list(parsed.get("missing_fields") or [])[:10]
        if _clean_text(item, 200)
    ]
    response_mode = _clean_text(parsed.get("response_mode"), 40).lower()
    allowed_response_modes = {
        "direct_reply",
        "task_delegation",
        "clarification",
        "status_update",
        "control",
        "silent_wait",
    }
    if response_mode not in allowed_response_modes:
        response_mode = {
            "reply": "direct_reply",
            "delegate": "task_delegation",
            "queue": "task_delegation",
            "steer": "task_delegation",
            "request_information": "clarification",
            "cancel_job": "control",
            "wait": "silent_wait",
        }.get(action, "status_update")
    confidence = _clean_text(parsed.get("confidence"), 20).lower()
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    intent = _clean_text(parsed.get("intent"), 120) or {
        "reply": "conversation_reply",
        "delegate": "task_delegation",
        "queue": "task_queue",
        "steer": "task_update",
        "request_information": "clarification",
        "cancel_job": "task_cancel",
        "wait": "status_wait",
    }.get(action, "conversation")
    intent_kind = _clean_text(parsed.get("intent_kind"), 40).lower()
    if intent_kind not in {"conversation", "query", "control", "mutation_candidate"}:
        intent_kind = "control" if action == "cancel_job" else (
            "query" if action in {"delegate", "queue", "steer", "request_information"} else "conversation"
        )
    relation_to_active_job = _clean_text(parsed.get("relation_to_active_job"), 40).lower()
    if relation_to_active_job not in {"none", "status", "followup", "correction", "cancel", "new_parallel"}:
        relation_to_active_job = "none"
    answer_basis = _clean_text(parsed.get("answer_basis"), 40).lower()
    if answer_basis not in {"conversation_only", "active_job", "verified_evidence", "clarification", "unavailable"}:
        answer_basis = (
            "verified_evidence" if event_type in {"worker_result", "worker_partial"}
            else "clarification" if action == "request_information"
            else "conversation_only"
        )
    data_requirement = _clean_text(parsed.get("data_requirement"), 20).lower()
    if data_requirement not in {"none", "optional", "required"}:
        data_requirement = "required" if action in {"delegate", "queue", "steer", "request_information"} else "none"
    if action == "reply" and data_requirement == "required":
        raise RuntimeError("conversation_agent_reply_requires_data")
    task = {
        "title": job_title,
        "prompt": job_prompt,
        "requires_web": requires_web,
        "reasoning_effort": task_reasoning,
    }
    normalized_v2 = {
        "schema_version": black_jhon_prompting.CONVERSATION_DECISION_V2,
        "intent_id": _clean_text(parsed.get("intent_id"), 100),
        "intent": intent,
        "intent_kind": intent_kind,
        "relation_to_active_job": relation_to_active_job,
        "answer_basis": answer_basis,
        "data_requirement": data_requirement,
        "context_operations": context_operations,
        "response_mode": response_mode,
        "missing_fields": missing_fields,
        "confidence": confidence,
        "task": task,
        "action": action,
        "reply_text": reply,
        "job_title": job_title,
        "job_prompt": job_prompt,
        "related_job_id": _clean_text(parsed.get("related_job_id"), 100),
        "needs_user_input": bool(parsed.get("needs_user_input")) or bool(missing_fields),
        "requires_web": requires_web,
        "resolved_context": resolved_context,
        "subtasks": subtasks,
    }
    selected_mode = (
        decision_contract_mode(contract_version)
        if contract_version is not None
        else (
            "v3"
            if str(parsed.get("schema_version") or "").strip()
            == black_jhon_prompting.CONVERSATION_DECISION_V3
            else "v2"
        )
    )
    if selected_mode != "v3":
        return normalized_v2

    v3_source = {
        **normalized_v2,
        "schema_version": parsed.get("schema_version") or black_jhon_prompting.CONVERSATION_DECISION_V2,
    }
    for key in ("intent_id", "intent_path", "entities", "scope", "risk", "ambiguities"):
        if key in parsed:
            v3_source[key] = parsed[key]
    return black_jhon_prompting.normalize_conversation_decision_v3(v3_source)


def normalize_worker_result(task: dict[str, Any]) -> dict[str, Any]:
    raw_response = _clean_text(task.get("final_response"), 20000)
    parsed = parse_json_object(raw_response)
    allowed_statuses = {"completed", "partial", "blocked", "failed"}
    if parsed and str(parsed.get("status") or "") in allowed_statuses:
        raw_records = [item for item in list(parsed.get("records") or [])[:40] if isinstance(item, dict)]
        raw_facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else parsed.get("verified_facts")
        facts = [_clean_text(item, 2000) for item in list(raw_facts or [])[:30] if _clean_text(item, 2000)]
        if not facts:
            facts = [_clean_text(item.get("value"), 2000) for item in raw_records if _clean_text(item.get("value"), 2000)][:30]
        sources = [_clean_text(item, 1000) for item in list(parsed.get("sources") or [])[:30] if _clean_text(item, 1000)]
        confidence = str(parsed.get("confidence") or "unknown") if str(parsed.get("confidence") or "") in {"high", "medium", "low", "unknown"} else "unknown"
        raw_missing = parsed.get("gaps") if isinstance(parsed.get("gaps"), list) else parsed.get("missing")
        missing = [_clean_text(item, 1000) for item in list(raw_missing or [])[:20] if _clean_text(item, 1000)]
        inferred_sufficient = bool(
            str(parsed.get("status")) == "completed"
            and facts
            and sources
            and confidence in {"high", "medium"}
            and not missing
        )
        return black_jhon_prompting.normalize_evidence_envelope_v2({
            "status": str(parsed.get("status")),
            "summary": _clean_text(parsed.get("summary"), 12000),
            "records": raw_records,
            "facts": facts,
            "sources": sources,
            "confidence": confidence,
            "evidence_sufficient": bool(parsed.get("evidence_sufficient")) if "evidence_sufficient" in parsed else inferred_sufficient,
            "coverage_complete": bool(parsed.get("coverage_complete")) if "coverage_complete" in parsed else False,
            "gaps": missing,
            "questions": [_clean_text(item, 1000) for item in list(parsed.get("questions") or [])[:10] if _clean_text(item, 1000)],
            "data_requests": [
                {
                    "need": _clean_text(item.get("need"), 500),
                    "fields": [_clean_text(value, 200) for value in list(item.get("fields") or [])[:20] if _clean_text(value, 200)],
                    "reason": _clean_text(item.get("reason"), 1000),
                }
                for item in list(parsed.get("data_requests") or [])[:6]
                if isinstance(item, dict) and _clean_text(item.get("need"), 500)
            ],
            "schema_version": parsed.get("schema_version") or "legacy-v1",
        })
    status = str(task.get("status") or "failed")
    mapped_status = status if status in allowed_statuses else ("partial" if status == "canceled" else "failed")
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    summaries = [
        item
        for item in list(task.get("tool_results_summary") or [])
        if isinstance(item, dict)
    ]
    validations = [
        item.get("tool_validation")
        for item in summaries
        if isinstance(item.get("tool_validation"), dict)
    ]
    confirmed_by_tools = bool(
        verification.get("confirmed") is True
        or (
            mapped_status == "completed"
            and validations
            and any(item.get("dados_suficientes") is True for item in validations)
        )
    )
    sources = [_clean_text(item, 1000) for item in list(task.get("sources") or [])[:30] if _clean_text(item, 1000)]
    for item in summaries:
        validation = item.get("tool_validation") if isinstance(item.get("tool_validation"), dict) else {}
        if validation.get("dados_suficientes") is not True:
            continue
        source = _clean_text(
            item.get("source_label") or item.get("source") or item.get("tool_label") or item.get("tool_id"),
            1000,
        )
        if source and source not in sources:
            sources.append(source)
    if confirmed_by_tools and not sources:
        sources.append("validacao consolidada das ferramentas autorizadas")
    missing = [] if confirmed_by_tools else ([_clean_text(task.get("error"), 1000)] if task.get("error") else [])
    fallback_facts = [raw_response] if raw_response else []
    if confirmed_by_tools and not fallback_facts:
        for item in summaries:
            validation = item.get("tool_validation") if isinstance(item.get("tool_validation"), dict) else {}
            if validation.get("dados_suficientes") is not True:
                continue
            payload = {
                key: item.get(key)
                for key in ("tool_id", "records", "summary", "data", "result")
                if item.get(key) not in (None, "", [], {})
            }
            if payload:
                fallback_facts.append(_clean_text(json.dumps(payload, ensure_ascii=False, default=str), 2000))
    return black_jhon_prompting.normalize_evidence_envelope_v2({
        "status": mapped_status,
        "summary": raw_response or _clean_text(task.get("error"), 4000),
        "facts": fallback_facts[:30],
        "sources": sources,
        "confidence": "high" if confirmed_by_tools else ("low" if mapped_status != "completed" else "medium"),
        "evidence_sufficient": confirmed_by_tools,
        "coverage_complete": bool(verification.get("coverage_complete") is True or confirmed_by_tools),
        "gaps": missing,
        "questions": [],
        "data_requests": [],
    })


def normalize_manager_plan(value: Any, *, max_calls: int = 6) -> dict[str, Any]:
    parsed = parse_json_object(value)
    if not parsed:
        raise RuntimeError("function_manager_invalid_json")
    store_mode = _clean_text(parsed.get("store_mode"), 20).lower()
    if store_mode not in {"single", "all", "none"}:
        store_mode = "single" if _clean_text(parsed.get("store"), 180) else "none"
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(parsed.get("tool_calls") or [])[: max(1, min(6, int(max_calls or 6)))]:
        if not isinstance(item, dict):
            continue
        tool_id = _clean_text(item.get("tool_id"), 100)
        raw_arguments = item.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        else:
            arguments = parse_json_object(raw_arguments)
        signature = json.dumps({"tool_id": tool_id, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)
        if not tool_id or signature in seen:
            continue
        seen.add(signature)
        calls.append(
            {
                "tool_id": tool_id,
                "arguments": arguments,
                "required": item.get("required") is not False,
                "reason": _clean_text(item.get("reason"), 500),
            }
        )
    return {
        "intent": _clean_text(parsed.get("intent"), 120) or "generic_data_query",
        "store": _clean_text(parsed.get("store"), 180),
        "store_mode": store_mode,
        "sku": _clean_text(parsed.get("sku"), 100),
        "item_id": _clean_text(parsed.get("item_id"), 60),
        "requested_fields": [_clean_text(item, 200) for item in list(parsed.get("requested_fields") or [])[:30] if _clean_text(item, 200)],
        "tool_calls": calls,
        "requires_sol": parsed.get("requires_sol") is True,
        "requires_web": parsed.get("requires_web") is True,
        "missing_user_fields": [_clean_text(item, 300) for item in list(parsed.get("missing_user_fields") or [])[:10] if _clean_text(item, 300)],
        "reason": _clean_text(parsed.get("reason"), 1200),
    }


def worker_output_instruction() -> str:
    return (
        black_jhon_prompting.prompt_contract_header("task_agent")
        + black_jhon_prompting.WORKER_OUTPUT_INSTRUCTIONS
    )


def _manager_prompt(
    *,
    request_text: str,
    job_prompt: str,
    query_policy: Optional[dict[str, Any]],
    tool_catalog: list[dict[str, Any]],
    previous_evidence: Optional[dict[str, Any]],
    data_requests: Optional[list[dict[str, Any]]],
) -> str:
    normalized_evidence = (
        black_jhon_prompting.normalize_evidence_envelope_v2(previous_evidence)
        if isinstance(previous_evidence, dict) and previous_evidence
        else {}
    )
    context = {
        "request_text": _clean_text(request_text, 12000),
        "job_prompt": _clean_text(job_prompt, 12000),
        "query_policy": query_policy if isinstance(query_policy, dict) else {},
        "allowed_tools": list(tool_catalog or [])[:80],
        "previous_evidence": normalized_evidence,
        "task_data_requests": list(data_requests or [])[:6],
    }
    return (
        black_jhon_prompting.prompt_contract_header("function_planner")
        + black_jhon_prompting.MANAGER_PROMPT_INSTRUCTIONS
        + "\n\n"
        + black_jhon_prompting.bounded_context_json(context)
    )


def _decision_prompt(
    *,
    event_type: str,
    user_message: str,
    active_job: Optional[dict[str, Any]],
    worker_result: Optional[dict[str, Any]],
    conversation_context: Optional[list[dict[str, Any]]],
    conversation_state: Optional[dict[str, Any]] = None,
    ai_behavior: str,
    tick_index: int,
    contract_version: Any = None,
    quoted_context: Optional[dict[str, Any]] = None,
    include_bootstrap: bool = True,
    origin_channel: str = "whatsapp",
) -> str:
    contract = _decision_runtime_contract("v2" if contract_version is None else contract_version)
    active = active_job if isinstance(active_job, dict) else {}
    result = (
        black_jhon_prompting.normalize_evidence_envelope_v2(worker_result)
        if isinstance(worker_result, dict) and worker_result
        else {}
    )
    context = {
        "channel": "app" if str(origin_channel or "").strip().lower() == "app" else "whatsapp",
        "event_type": event_type,
        "user_message": _clean_text(user_message, 12000),
        "quoted_context": {
            "message_id": _clean_text((quoted_context or {}).get("message_id"), 200),
            "text": _clean_text((quoted_context or {}).get("text"), 3500),
            "source": "whatsapp_reply",
        } if isinstance(quoted_context, dict) and (
            _clean_text(quoted_context.get("message_id"), 200)
            or _clean_text(quoted_context.get("text"), 3500)
        ) else {},
        "active_job": {
            "job_id": _clean_text(active.get("job_id") or active.get("task_id"), 100),
            "title": _clean_text(active.get("job_title") or active.get("request_text"), 500),
            "status": _clean_text(active.get("status"), 60),
            "verified_partial": [_clean_text(item, 1200) for item in list(active.get("verified_partial") or [])[:10]],
            "recent_conversation_messages": [
                _clean_text(item, 500) for item in list(active.get("recent_conversation_messages") or [])[-5:]
            ],
        } if active else {},
        "worker_result": result,
        "conversation_context": [
            {
                "role": _clean_text(item.get("role"), 20),
                "text": _clean_text(item.get("text"), 900),
            }
            for item in list(conversation_context or [])[-10:]
            if isinstance(item, dict) and _clean_text(item.get("text"), 900)
        ] if include_bootstrap else [],
        "conversation_state": {
            "store": _clean_text((conversation_state or {}).get("store"), 200),
            "store_mode": _clean_text((conversation_state or {}).get("store_mode"), 20) or "none",
            "sku": _clean_text((conversation_state or {}).get("sku"), 100),
            "mlb": _clean_text((conversation_state or {}).get("mlb"), 60),
            "period": _clean_text((conversation_state or {}).get("period"), 160),
            "revision": max(0, int((conversation_state or {}).get("revision") or 0)),
            "field_sources": {
                _clean_text(key, 20): _clean_text(value, 40)
                for key, value in dict((conversation_state or {}).get("field_sources") or {}).items()
                if _clean_text(key, 20) in {"store", "sku", "mlb", "period"}
            },
            "confirmed_fields": [
                _clean_text(item, 20)
                for item in list((conversation_state or {}).get("confirmed_fields") or [])[:5]
                if _clean_text(item, 20) in {"store", "sku", "mlb", "period"}
            ],
            "authorized_stores": [
                _clean_text(item, 200)
                for item in list((conversation_state or {}).get("authorized_stores") or [])[:50]
                if _clean_text(item, 200)
            ],
        },
        "waiting_turn_index": max(0, int(tick_index or 0)),
        "phone_behavior": _clean_text(ai_behavior, 2000),
    }
    if not include_bootstrap:
        return (
            "Turno incremental da conversa ja inicializada. Preserve as instrucoes e o contexto da thread; "
            "aplique apenas o evento e o estado atual abaixo.\n\n"
            + black_jhon_prompting.bounded_context_json(context)
        )
    return (
        (
            black_jhon_prompting.prompt_contract_v3_header("conversation_decision")
            if contract["mode"] == "v3"
            else black_jhon_prompting.prompt_contract_header("conversation_decision")
        )
        + (
            black_jhon_prompting.DECISION_V3_PROMPT_INSTRUCTIONS
            if contract["mode"] == "v3"
            else black_jhon_prompting.DECISION_PROMPT_INSTRUCTIONS
        )
        + "\n\n"
        + black_jhon_prompting.bounded_context_json(context)
    )


class WarmConversationRuntime:
    """Own one warm Codex app-server for the WhatsApp conversation lane."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._codex: Any = None
        self._available_models: list[str] = []
        self._last_error = ""
        self._started_at = ""
        self._last_used_at = ""
        self._effective_model = ""

    def _start_locked(self) -> Any:
        if self._codex is not None:
            return self._codex
        from backend.services import codex_console
        from openai_codex import Codex, CodexConfig

        codex_console._codex_apply_sdk_protocol_compat()
        runtime_bin = codex_console._codex_runtime_require_ready()
        client = Codex(
            CodexConfig(
                codex_bin=runtime_bin,
                env=codex_console._codex_sdk_env(),
                cwd=str(codex_console._codex_base_dir()),
                config_overrides=codex_console._codex_nonfull_config_overrides(fast_mode=True),
            )
        )
        client.__enter__()
        self._codex = client
        self._started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._last_error = ""
        try:
            response = client.models(include_hidden=False)
            data = getattr(response, "data", None) or getattr(response, "models", None) or []
            models: list[str] = []
            for item in data:
                value = getattr(item, "id", None) or getattr(item, "model", None) or (item.get("id") if isinstance(item, dict) else "")
                if value:
                    models.append(str(value))
            self._available_models = list(dict.fromkeys(models))
        except Exception:
            self._available_models = []
        return client

    def _close_locked(self) -> None:
        client, self._codex = self._codex, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def resolve_model(self, requested: str) -> str:
        value = _clean_text(requested, 100).removeprefix("codex:")
        with self._lock:
            self._start_locked()
            if not self._available_models or value in self._available_models:
                return value
            for fallback in ("gpt-5.6-terra", "gpt-5.5", "gpt-5.4"):
                if fallback in self._available_models:
                    return fallback
        raise RuntimeError(f"codex_model_unavailable:{value}")

    def run(
        self,
        *,
        thread_id: str,
        model: str,
        reasoning_effort: str,
        event_type: str,
        user_message: str,
        active_job: Optional[dict[str, Any]] = None,
        worker_result: Optional[dict[str, Any]] = None,
        conversation_context: Optional[list[dict[str, Any]]] = None,
        conversation_state: Optional[dict[str, Any]] = None,
        quoted_context: Optional[dict[str, Any]] = None,
        ai_behavior: str = "",
        tick_index: int = 0,
        speed: str = "fast",
        service_tier: str = "priority",
        client_id: str = "",
        telemetry_trace_id: str = "",
        store_id: str = "",
        origin_channel: str = "whatsapp",
    ) -> dict[str, Any]:
        from backend.services import codex_console
        from openai_codex.generated.v2_all import ReasoningSummary

        decision_contract = _decision_runtime_contract()
        context_chars = 0
        effective_speed = codex_console._codex_normalizar_speed(speed)
        effective_service_tier = codex_console._codex_normalizar_service_tier(
            service_tier,
            effective_speed,
        )
        tenant = _clean_text(client_id, 80) or "default"
        trace_id = _clean_text(telemetry_trace_id, 200) or uuid.uuid4().hex
        responder_span_id = f"{trace_id}:responder"
        telemetry_started = time.perf_counter()
        telemetry = None
        telemetry_finished = False
        effective_model_for_telemetry = _clean_text(model, 100).removeprefix("codex:")
        if client_id:
            try:
                telemetry = codex_console._codex_ai_telemetry_instance()
                telemetry.schedule_retention(tenant)
                telemetry.start_trace(
                    tenant,
                    trace_id=trace_id,
                    surface=("app" if str(origin_channel or "").strip().lower() == "app" else "whatsapp"),
                    category="conversation",
                    requested_model=model,
                    store_id=store_id,
                    expected_spans=("responder",),
                )
                telemetry.start_span(
                    tenant,
                    trace_id=trace_id,
                    span_id=responder_span_id,
                    stage="responder",
                )
            except Exception:
                telemetry = None

        def finish_telemetry(status: str, *, error_code: str = "") -> None:
            nonlocal telemetry_finished
            if telemetry is None or telemetry_finished:
                return
            telemetry_finished = True
            duration_ms = (time.perf_counter() - telemetry_started) * 1000
            try:
                telemetry.finish_span(
                    tenant,
                    trace_id=trace_id,
                    span_id=responder_span_id,
                    stage="responder",
                    status=status,
                    duration_ms=duration_ms,
                    error_code=error_code,
                )
                telemetry.record_event(
                    tenant,
                    event_id=f"{trace_id}:responder",
                    trace_id=trace_id,
                    span_id=responder_span_id,
                    event_type="inference",
                    status=status,
                    requested_model=model,
                    effective_model=effective_model_for_telemetry,
                    provider="openai_codex",
                    provider_path="black_jhon_shared_conversation_agent",
                    model_rerouted=(
                        effective_model_for_telemetry
                        != _clean_text(model, 100).removeprefix("codex:")
                    ),
                    duration_ms=duration_ms,
                    store_id=store_id,
                    dimensions={
                        "surface": (
                            "app" if str(origin_channel or "").strip().lower() == "app" else "whatsapp"
                        ),
                        "category": "conversation",
                    },
                    error_code=error_code,
                )
                telemetry.finish_trace(
                    tenant,
                    trace_id=trace_id,
                    status=status,
                    effective_model=effective_model_for_telemetry,
                    provider="openai_codex",
                    duration_ms=duration_ms,
                    error_code=error_code,
                )
            except Exception:
                return
        last_error: Optional[Exception] = None
        for attempt in range(2):
            with self._lock:
                try:
                    client = self._start_locked()
                    effective_model = self.resolve_model(model)
                    effective_model_for_telemetry = effective_model
                    kwargs = {
                        "cwd": str(codex_console._codex_base_dir()),
                        "model": effective_model,
                        "approval_mode": codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        "developer_instructions": black_jhon_prompting.CONVERSATION_DEVELOPER_INSTRUCTIONS,
                        "service_tier": effective_service_tier,
                    }
                    thread_reused = False
                    thread_reset_reason = "new_conversation" if not thread_id else ""
                    if thread_id:
                        try:
                            resume_kwargs = {
                                key: value
                                for key, value in kwargs.items()
                                if key != "developer_instructions"
                            }
                            thread = client.thread_resume(thread_id, **resume_kwargs)
                            thread_reused = True
                        except Exception:
                            thread = client.thread_start(**kwargs)
                            thread_reset_reason = "thread_resume_failed"
                    else:
                        thread = client.thread_start(**kwargs)
                    prompt = _decision_prompt(
                        event_type=event_type,
                        user_message=user_message,
                        active_job=active_job,
                        worker_result=worker_result,
                        conversation_context=conversation_context,
                        conversation_state=conversation_state,
                        quoted_context=quoted_context,
                        ai_behavior=ai_behavior,
                        tick_index=tick_index,
                        contract_version=decision_contract["mode"],
                        include_bootstrap=not thread_reused,
                        origin_channel=origin_channel,
                    )
                    context_chars = len(prompt.rsplit("\n\n", 1)[-1])
                    result = thread.run(
                        prompt,
                        model=effective_model,
                        effort=codex_console._codex_reasoning_effort_enum(reasoning_effort),
                        approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        output_schema=decision_contract["schema"],
                        summary=ReasoningSummary.model_validate("none"),
                        service_tier=effective_service_tier,
                    )
                    decision = normalize_decision(
                        getattr(result, "final_response", ""),
                        event_type=event_type,
                        contract_version=decision_contract["mode"],
                    )
                    decision.update(
                        {
                            "thread_id": _clean_text(getattr(thread, "id", ""), 200),
                            "requested_model": _clean_text(model, 100).removeprefix("codex:"),
                            "effective_model": effective_model,
                            "reasoning_effort": reasoning_effort,
                            "speed": effective_speed,
                            "service_tier": effective_service_tier or "",
                            "response_provider": "codex",
                            "thread_reused": thread_reused,
                            "thread_reset_reason": thread_reset_reason,
                            "context_chars": context_chars,
                            "prompt_contract": decision_contract["prompt_contract"],
                            "decision_contract_mode": decision_contract["mode"],
                            "conversation_prompt_contract": (
                                black_jhon_prompting.conversation_prompt_contract_diagnostics()
                            ),
                        }
                    )
                    self._effective_model = effective_model
                    self._last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._last_error = ""
                    finish_telemetry("completed")
                    return decision
                except Exception as exc:
                    last_error = exc
                    self._last_error = str(exc)[:1000]
                    self._close_locked()
            if attempt == 0:
                time.sleep(0.2)
        finish_telemetry("failed", error_code=type(last_error).__name__ if last_error else "RuntimeError")
        raise RuntimeError(f"conversation_agent_failed:{last_error}")

    def run_manager(
        self,
        *,
        thread_id: str,
        model: str,
        reasoning_effort: str,
        request_text: str,
        job_prompt: str,
        query_policy: Optional[dict[str, Any]],
        tool_catalog: list[dict[str, Any]],
        previous_evidence: Optional[dict[str, Any]] = None,
        data_requests: Optional[list[dict[str, Any]]] = None,
        max_calls: int = 6,
        speed: str = "fast",
        service_tier: str = "priority",
    ) -> dict[str, Any]:
        from backend.services import codex_console
        from openai_codex.generated.v2_all import ReasoningSummary

        prompt = _manager_prompt(
            request_text=request_text,
            job_prompt=job_prompt,
            query_policy=query_policy,
            tool_catalog=tool_catalog,
            previous_evidence=previous_evidence,
            data_requests=data_requests,
        )
        effective_speed = codex_console._codex_normalizar_speed(speed)
        effective_service_tier = codex_console._codex_normalizar_service_tier(service_tier, effective_speed)
        last_error: Optional[Exception] = None
        for attempt in range(2):
            with self._lock:
                try:
                    client = self._start_locked()
                    effective_model = self.resolve_model(model)
                    kwargs = {
                        "cwd": str(codex_console._codex_base_dir()),
                        "model": effective_model,
                        "approval_mode": codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        "developer_instructions": black_jhon_prompting.FUNCTION_MANAGER_DEVELOPER_INSTRUCTIONS,
                        "service_tier": effective_service_tier,
                    }
                    if thread_id:
                        try:
                            thread = client.thread_resume(thread_id, **kwargs)
                        except Exception:
                            thread = client.thread_start(**kwargs)
                    else:
                        thread = client.thread_start(**kwargs)
                    result = thread.run(
                        prompt,
                        model=effective_model,
                        effort=codex_console._codex_reasoning_effort_enum(reasoning_effort),
                        approval_mode=codex_console._codex_approval_mode_enum("read_only", "read_only"),
                        output_schema=FUNCTION_MANAGER_PLAN_SCHEMA,
                        summary=ReasoningSummary.model_validate("none"),
                        service_tier=effective_service_tier,
                    )
                    plan = normalize_manager_plan(getattr(result, "final_response", ""), max_calls=max_calls)
                    plan.update(
                        {
                            "thread_id": _clean_text(getattr(thread, "id", ""), 200),
                            "requested_model": _clean_text(model, 100).removeprefix("codex:"),
                            "effective_model": effective_model,
                            "reasoning_effort": reasoning_effort,
                            "speed": effective_speed,
                            "service_tier": effective_service_tier or "",
                            "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
                        }
                    )
                    self._effective_model = effective_model
                    self._last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._last_error = ""
                    return plan
                except Exception as exc:
                    last_error = exc
                    self._last_error = str(exc)[:1000]
                    self._close_locked()
            if attempt == 0:
                time.sleep(0.2)
        raise RuntimeError(f"function_manager_failed:{last_error}")

    def warm(self, conversation_model: str, task_model: str) -> dict[str, Any]:
        with self._lock:
            decision_contract = _decision_runtime_contract()
            conversation_effective = self.resolve_model(conversation_model)
            task_effective = self.resolve_model(task_model)
            self._effective_model = conversation_effective
            return {
                "ready": True,
                "conversation_effective_model": conversation_effective,
                "task_effective_model": task_effective,
                "available_models": list(self._available_models),
                "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
                "decision_prompt_contract": decision_contract["prompt_contract"],
                "decision_contract_mode": decision_contract["mode"],
            }

    def diagnostics(self) -> dict[str, Any]:
        decision_contract = _decision_runtime_contract()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {
                "ready": self._codex is not None and not self._last_error,
                "started_at": self._started_at,
                "last_used_at": self._last_used_at,
                "last_error": self._last_error,
                "effective_model": self._effective_model,
                "available_models": list(self._available_models),
                "busy": True,
                "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
                "decision_prompt_contract": decision_contract["prompt_contract"],
                "decision_contract_mode": decision_contract["mode"],
            }
        try:
            return {
                "ready": self._codex is not None and not self._last_error,
                "started_at": self._started_at,
                "last_used_at": self._last_used_at,
                "last_error": self._last_error,
                "effective_model": self._effective_model,
                "available_models": list(self._available_models),
                "busy": False,
                "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
                "decision_prompt_contract": decision_contract["prompt_contract"],
                "decision_contract_mode": decision_contract["mode"],
            }
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._close_locked()


class WarmConversationRuntimePool:
    """Pool of isolated warm Codex app-servers for the conversation lane.

    A slot is checked out for one model turn and returned afterwards.  The
    caller is responsible for serializing turns that belong to the same phone;
    different phones can therefore use different app-server processes at the
    same time.
    """

    def __init__(self, default_size: int = 4) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._configure_lock = threading.Lock()
        self._slots: list[WarmConversationRuntime] = []
        self._available: deque[WarmConversationRuntime] = deque()
        self._busy: set[int] = set()
        self._target_size = max(1, min(8, int(default_size or 4)))
        self._closing = False
        self._last_error = ""

    def _checkout(self, timeout: float = 60.0) -> WarmConversationRuntime:
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        with self._condition:
            while not self._available and not self._closing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("conversation_runtime_pool_unavailable")
                self._condition.wait(min(remaining, 1.0))
            if self._closing:
                raise RuntimeError("conversation_runtime_pool_closed")
            slot = self._available.popleft()
            self._busy.add(id(slot))
            return slot

    def _release(self, slot: WarmConversationRuntime) -> None:
        close_slot = False
        with self._condition:
            self._busy.discard(id(slot))
            if self._closing or len(self._slots) > self._target_size:
                try:
                    self._slots.remove(slot)
                except ValueError:
                    pass
                close_slot = True
            elif slot in self._slots:
                self._available.append(slot)
            self._condition.notify_all()
        if close_slot:
            slot.close()

    def configure(self, size: int, conversation_model: str, task_model: str) -> dict[str, Any]:
        with self._configure_lock:
            return self._configure_locked(size, conversation_model, task_model)

    def _configure_locked(self, size: int, conversation_model: str, task_model: str) -> dict[str, Any]:
        target = max(1, min(8, int(size or 1)))
        with self._condition:
            if self._closing and not self._slots:
                self._closing = False
            if self._closing:
                raise RuntimeError("conversation_runtime_pool_closed")
            current = len(self._slots)

        # New slots are fully warmed before they become visible to workers.
        additions: list[WarmConversationRuntime] = []
        try:
            for _ in range(max(0, target - current)):
                slot = WarmConversationRuntime()
                slot.warm(conversation_model, task_model)
                additions.append(slot)
        except Exception as exc:
            for slot in additions:
                slot.close()
            self._last_error = str(exc)[:1000]
            raise

        removable: list[WarmConversationRuntime] = []
        with self._condition:
            self._target_size = target
            for slot in additions:
                self._slots.append(slot)
                self._available.append(slot)
            while len(self._slots) > target and self._available:
                slot = self._available.pop()
                try:
                    self._slots.remove(slot)
                except ValueError:
                    continue
                removable.append(slot)
            self._last_error = ""
            self._condition.notify_all()
        for slot in removable:
            slot.close()
        return self.diagnostics()

    def run(self, **kwargs: Any) -> dict[str, Any]:
        slot = self._checkout()
        try:
            return slot.run(**kwargs)
        finally:
            self._release(slot)

    def run_manager(self, **kwargs: Any) -> dict[str, Any]:
        slot = self._checkout()
        try:
            return slot.run_manager(**kwargs)
        finally:
            self._release(slot)

    def resolve_model(self, requested: str) -> str:
        slot = self._checkout()
        try:
            return slot.resolve_model(requested)
        finally:
            self._release(slot)

    def warm(
        self,
        conversation_model: str,
        task_model: str,
        pool_size: Optional[int] = None,
    ) -> dict[str, Any]:
        with self._condition:
            size = int(pool_size or self._target_size or 4)
        return self.configure(size, conversation_model, task_model)

    def diagnostics(self) -> dict[str, Any]:
        decision_contract = _decision_runtime_contract()
        with self._condition:
            slots = list(self._slots)
            busy_ids = set(self._busy)
            target = self._target_size
            last_error = self._last_error
        details: list[dict[str, Any]] = []
        healthy = 0
        for index, slot in enumerate(slots):
            diagnostic = slot.diagnostics()
            diagnostic = {
                **diagnostic,
                "slot": index + 1,
                "busy": id(slot) in busy_ids,
            }
            if diagnostic.get("ready"):
                healthy += 1
            details.append(diagnostic)
        return {
            "ready": bool(slots) and healthy == len(slots) and not last_error,
            "pool_size": len(slots),
            "target_pool_size": target,
            "busy": len(busy_ids),
            "available": max(0, len(slots) - len(busy_ids)),
            "healthy": healthy,
            "last_error": last_error,
            "slots": details,
            "prompt_contract": black_jhon_prompting.prompt_contract_diagnostics(),
            "decision_prompt_contract": decision_contract["prompt_contract"],
            "decision_contract_mode": decision_contract["mode"],
        }

    def close(self) -> None:
        with self._condition:
            self._closing = True
            slots = list(self._available)
            for slot in slots:
                try:
                    self._slots.remove(slot)
                except ValueError:
                    pass
            self._available.clear()
            self._condition.notify_all()
        for slot in slots:
            slot.close()


CONVERSATION_RUNTIME = WarmConversationRuntimePool(default_size=4)
from backend.services.codex_data_selection_agent import DATA_SELECTION_RUNTIME


__all__ = [
    "CONVERSATION_ACTIONS",
    "DECISION_SCHEMA",
    "CONVERSATION_DECISION_V2_SCHEMA",
    "CONVERSATION_DECISION_V3_SCHEMA",
    "WORKER_RESULT_SCHEMA",
    "EVIDENCE_ENVELOPE_V2_SCHEMA",
    "RETRIEVAL_RESULT_V2_SCHEMA",
    "FUNCTION_MANAGER_PLAN_SCHEMA",
    "CONVERSATION_RUNTIME",
    "DATA_SELECTION_RUNTIME",
    "WarmConversationRuntime",
    "WarmConversationRuntimePool",
    "normalize_decision",
    "normalize_worker_result",
    "normalize_manager_plan",
    "decision_contract_mode",
    "parse_json_object",
    "worker_output_instruction",
]
