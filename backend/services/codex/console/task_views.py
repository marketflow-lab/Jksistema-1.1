"""Codex console task views component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)

_CODEX_PUBLIC_CONTEXT_STAT_FIELDS = (
    "prompt_chars",
    "screen_context_chars",
    "screen_context_bytes",
    "app_data_context_chars",
    "app_data_context_bytes",
    "app_tool_results_count",
    "visible_text_chars",
    "controls_count",
    "table_rows_count",
    "filtros_count",
    "listas_count",
    "run_prompt_chars",
    "estimated_input_tokens",
    "estimated_tokens",
    "estimated_context_tokens",
    "estimated_app_data_tokens",
    "history_chars",
    "estimated_history_tokens",
    "context_soft_limit",
    "context_target",
)


def _codex_observability_from_task(task: dict[str, Any]) -> dict[str, Any]:
    tool_calls = [item for item in (task.get("tool_calls") or []) if isinstance(item, dict)]
    result_summaries = [item for item in (task.get("tool_results_summary") or []) if isinstance(item, dict)]
    last_call = tool_calls[-1] if tool_calls else {}
    last_result = result_summaries[-1] if result_summaries else {}
    evidence = last_result.get("evidence") if isinstance(last_result.get("evidence"), dict) else {}
    raw_sources = (
        last_result.get("sources_human")
        if isinstance(last_result.get("sources_human"), list)
        else last_result.get("sources")
        if isinstance(last_result.get("sources"), list)
        else []
    )
    sources: list[str] = []
    source_label = str(last_result.get("source_label") or "").strip()
    if source_label:
        sources.append(source_label)
    for source in raw_sources:
        if isinstance(source, dict):
            text = str(source.get("source_label") or source.get("function_label") or source.get("label") or source.get("source") or source.get("function") or source.get("tool_id") or "").strip()
        else:
            text = str(source or "").strip()
        if text and text not in sources:
            sources.append(text)
    if not sources:
        for source in task.get("sources") or []:
            text = str((source.get("source_label") or source.get("source")) if isinstance(source, dict) else source or "").strip()
            if text and text not in sources:
                sources.append(text)
    warnings = [str(item or "").strip() for item in (last_result.get("warnings") or task.get("warnings") or []) if str(item or "").strip()]
    failures = []
    empty_reason = str(last_result.get("empty_reason") or "").strip()
    if empty_reason:
        failures.append(empty_reason)
    failures.extend(warnings[:4])
    next_fallbacks = [
        str(item.get("label") or item.get("tool_id") or "").strip()
        for item in list(evidence.get("next_sources") or [])
        if isinstance(item, dict) and str(item.get("label") or item.get("tool_id") or "").strip()
    ]
    return {
        "current_status": str(task.get("live_status") or task.get("status") or "").strip(),
        "last_tool": str(last_result.get("tool_label") or last_result.get("tool_id") or last_call.get("tool_id") or "").strip(),
        "last_tool_module": str(last_result.get("module") or "").strip(),
        "source": sources[0] if sources else "",
        "sources": sources[:8],
        "records": int(last_result.get("records") or 0) if last_result else 0,
        "evidence_status": str(evidence.get("status") or "").strip(),
        "claim_scope": str(evidence.get("claim_scope") or "").strip(),
        "coverage_complete": evidence.get("coverage_complete") is True,
        "confidence": str(evidence.get("confidence") or "").strip(),
        "failures": failures[:6],
        "next_fallbacks": [str(item or "").strip() for item in next_fallbacks[:8] if str(item or "").strip()],
        "tool_calls_count": len(tool_calls),
        "tool_results_count": len(result_summaries),
        "updated_at": _codex_now(),
    }





def _codex_agent_guidance_context(prompt: str, screen_context: Any) -> dict[str, str]:
    context = screen_context if isinstance(screen_context, dict) else {}
    selection = context.get("selection") if isinstance(context.get("selection"), dict) else {}
    filters = (
        context.get("filtros")
        if isinstance(context.get("filtros"), dict)
        else context.get("filters")
        if isinstance(context.get("filters"), dict)
        else {}
    )

    def first(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text[:240]
        return ""

    corpus = "\n".join(
        str(item or "")
        for item in (
            prompt,
            context.get("visible_text"),
            context.get("title"),
        )
    )
    sku_match = re.search(r"\bSKU\s*[:#-]?\s*([A-Za-z0-9._/-]{1,80})\b", corpus, flags=re.I)
    return {
        "module": first(context.get("modulo_atual"), context.get("module"), selection.get("module")),
        "store": first(
            selection.get("loja"),
            selection.get("store"),
            filters.get("loja"),
            filters.get("store"),
            context.get("loja"),
            context.get("store"),
        ),
        "supplier": first(selection.get("fornecedor"), selection.get("supplier"), filters.get("fornecedor")),
        "sku": first(selection.get("sku"), filters.get("sku"), sku_match.group(1) if sku_match else ""),
    }





def _codex_sync_plan_fields(task: dict[str, Any], plan: Any) -> None:
    if not isinstance(plan, dict):
        return
    task.update(
        {
            "plan_id": str(plan.get("plan_id") or task.get("plan_id") or ""),
            "agent_state": str(plan.get("agent_state") or task.get("agent_state") or "entendendo"),
            "steps": list(plan.get("steps") or []),
            "current_step": str(plan.get("current_step") or ""),
            "required_input": list(plan.get("required_input") or []),
            "proposal": plan.get("proposal") if isinstance(plan.get("proposal"), dict) else {},
            "guidance_applied": list(plan.get("guidance_applied") or []),
            "verification": plan.get("verification") if isinstance(plan.get("verification"), dict) else {},
            "idempotency_key": str(plan.get("idempotency_key") or task.get("idempotency_key") or ""),
        }
    )


def _codex_transition_task_plan(
    task_id: str,
    state: str,
    *,
    current_step: str = "",
    step_status: str = "",
    required_input: Optional[list[Any]] = None,
    proposal: Optional[dict[str, Any]] = None,
    verification: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not isinstance(task, dict) or not task.get("plan_id"):
        return {}
    plan = codex_agent_runtime.transition_plan(
        _codex_base_info_dir(),
        str(task.get("client_id") or "default"),
        str(task.get("plan_id") or ""),
        state,
        current_step=current_step,
        step_status=step_status,
        required_input=required_input,
        proposal=proposal,
        verification=verification,
        details=details,
    )
    updates: dict[str, Any] = {}
    _codex_sync_plan_fields(updates, plan)
    if updates:
        _codex_update_task(task_id, **updates)
    return plan


def _codex_public_task(task: dict[str, Any]) -> dict[str, Any]:
    conversation = _codex_public_conversation_metadata(task)
    return {'task_id': task.get('task_id'), 'status': task.get('status'), 'execution_plane': CODEX_EXECUTION_PLANE, 'development_write_enabled': CODEX_DEVELOPMENT_WRITE_ENABLED, 'development_error_code': CODEX_DEVELOPMENT_ERROR_CODE, 'sandbox': task.get('sandbox'), 'cwd': '', 'thread_reused': bool(task.get('thread_reused')), 'thread_restart_reasons': list(task.get('thread_restart_reasons') or []), 'thread_prompt_version': task.get('thread_prompt_version') or '', 'thread_schema_version': task.get('thread_schema_version') or '', 'thread_prompt_fingerprint': task.get('thread_prompt_fingerprint') or '', 'thread_schema_fingerprint': task.get('thread_schema_fingerprint') or '', 'thread_scope_fingerprint': task.get('thread_scope_fingerprint') or '', 'shared_context_contract_version': codex_turn_context.CONTRACT_VERSION, 'shared_context_contract_hash': codex_turn_context.CONTRACT_HASH, 'conversation_id': conversation.get('conversation_id') or task.get('conversation_id') or task.get('task_id'), 'conversation_generation': int(conversation.get('conversation_generation') or 1), 'conversation_state': conversation.get('conversation_state') or 'archived', 'channel': conversation.get('channel') or 'app', 'queue_position': _codex_task_queue_position(task), 'prompt': task.get('prompt'), 'mutable_intent': bool(task.get('mutable_intent')), 'model': task.get('model'), 'requested_model': task.get('requested_model') or task.get('model'), 'effective_model': task.get('effective_model') or task.get('model'), 'model_category': task.get('model_category') or 'general', 'model_policy_version': task.get('model_policy_version') or 'disabled', 'model_reason_code': task.get('model_reason_code') or 'baseline_default', 'approval_mode': task.get('approval_mode'), 'reasoning_effort': task.get('reasoning_effort'), 'reasoning_level': task.get('reasoning_level') or task.get('reasoning_effort') or '', 'reasoning_policy': task.get('reasoning_policy') or 'fixed', 'reasoning_max': task.get('reasoning_max') or task.get('reasoning_effort') or '', 'orchestration_profile': task.get('orchestration_profile') or 'default', 'agent_role': task.get('agent_role') or '', 'agent_lane': task.get('agent_lane') or '', 'parent_job_id': task.get('parent_job_id') or '', 'job_group_id': task.get('job_group_id') or '', 'subtask_id': task.get('subtask_id') or '', 'logical_subtask_id': task.get('logical_subtask_id') or '', 'current_attempt': int(task.get('current_attempt') or 1), 'attempt_task_ids': [str(item or '')[:100] for item in list(task.get('attempt_task_ids') or [])[-50:]], 'retry_count': int(task.get('retry_count') or 0), 'retry_reason': str(task.get('retry_reason') or '')[:1000], 'next_retry_at_epoch': float(task.get('next_retry_at_epoch') or 0), 'handoff_status': task.get('handoff_status') or '', 'last_conversation_tick_at': task.get('last_conversation_tick_at') or '', 'delivery_state': task.get('delivery_state') or '', 'sol_queue_wait_started_at': task.get('sol_queue_wait_started_at') or '', 'sol_started_at': task.get('sol_started_at') or '', 'restart_recovery_count': int(task.get('restart_recovery_count') or 0), 'runtime_retry_count': int(task.get('runtime_retry_count') or 0), 'runtime_retry_after_seconds': int(task.get('runtime_retry_after_seconds') or 0), 'tool_protocol': task.get('tool_protocol') or 'typed_catalog_text_v1', 'mcp_migration': _codex_public_mcp_migration(task.get('mcp_migration')), 'speed': task.get('speed'), 'service_tier': task.get('service_tier'), 'goal': task.get('goal') or '', 'planning_mode': bool(task.get('planning_mode')), 'attachments': list(task.get('attachments') or []), 'reference_paths': [], 'paths': [], 'scope': _codex_public_scope(task.get('scope')), 'scope_changed_files': [], 'scope_violations': [], 'screen_context': {}, 'history': [], 'context_stats': _codex_public_context_stats(task.get('context_stats')), 'app_data_context': {}, 'conversation_summary': {}, 'conversation_compaction': {}, 'agent_mode': bool(task.get('agent_mode')), 'plan_id': task.get('plan_id') or '', 'agent_state': task.get('agent_state') or ('concluido' if task.get('status') == 'completed' else 'entendendo'), 'steps': list(task.get('steps') or []), 'current_step': task.get('current_step') or '', 'required_input': _codex_public_required_input(task.get('required_input')), 'proposal': _codex_public_proposal(task.get('proposal')), 'action_run': _codex_public_action_run(task.get('action_run')), 'guidance_applied': [], 'verification': _codex_public_verification(task.get('verification')), 'idempotency_key': task.get('idempotency_key') or '', 'agent_steps': _codex_public_status_events(task.get('agent_steps')), 'tool_calls': _codex_public_tool_summaries(task.get('tool_calls')), 'tool_results_summary': _codex_public_tool_summaries(task.get('tool_results_summary')), 'sources': _codex_public_sources(task.get('sources')), 'warnings': [_codex_sanitize_log_text(item, 300) for item in list(task.get('warnings') or [])[:30]], 'observability': _codex_public_observability(_codex_observability_from_task(task)), 'live_status': _codex_sanitize_log_text(task.get('live_status') or '', 240), 'live_answer': task.get('live_answer') or '', 'reasoning_summary': '', 'live_plan': '', 'token_usage': task.get('token_usage') if isinstance(task.get('token_usage'), dict) else {}, 'turn_id': task.get('turn_id') or '', 'active_turn_id': task.get('active_turn_id') or '', 'can_steer': bool(task.get('can_steer')), 'wait_reason': task.get('wait_reason') or '', 'progress_events': _codex_public_status_events(task.get('progress_events')), 'last_progress_at': task.get('last_progress_at') or '', 'deadline_enabled': bool(task.get('deadline_enabled') is not False and str(task.get('origin') or '').strip().lower() != 'whatsapp'), 'deadline_at': '' if task.get('deadline_enabled') is False or str(task.get('origin') or '').strip().lower() == 'whatsapp' else task.get('deadline_at') or '', 'deadline_seconds': 0 if task.get('deadline_enabled') is False or str(task.get('origin') or '').strip().lower() == 'whatsapp' else int(task.get('deadline_seconds') or 0), 'steer_events': _codex_public_steer_events(task.get('steer_events')), 'final_response': task.get('final_response') or '', 'error': _codex_sanitize_log_text(task.get('error') or '', 500), 'error_code': str(task.get('error_code') or '')[:100], 'message_kind': task.get('message_kind') or '', 'memory_excluded': bool(task.get('memory_excluded')), 'report_id': task.get('report_id') or '', 'report_formats': list(task.get('report_formats') or []), 'logs': [], 'created_at': task.get('created_at'), 'started_at': task.get('started_at'), 'completed_at': task.get('completed_at'), 'created_by': task.get('created_by'), 'client_id': task.get('client_id'), 'origin': task.get('origin') or 'app', 'channel_message_id': '', 'channel_metadata': {}, 'external_safe_mode': bool(task.get('external_safe_mode')), 'whatsapp_full_access': bool(task.get('whatsapp_full_access')), 'whatsapp_query_only': bool(task.get('whatsapp_query_only')), 'query_policy': _codex_public_query_policy(task.get('query_policy')), 'access_mode': task.get('access_mode') or ('full' if task.get('sandbox') != 'read_only' else 'read_only'), 'approval_required': bool(task.get('approval_required')), 'approved': bool(task.get('approved'))}




def _codex_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    conversation = _codex_public_conversation_metadata(task)
    prompt = str(task.get("prompt") or "")
    response = str(
        task.get("final_response")
        or task.get("live_answer")
        or task.get("error")
        or ""
    )
    return {
        "task_id": task.get("task_id"),
        "client_id": task.get("client_id"),
        "created_by": task.get("created_by"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "model": task.get("model"),
        "conversation_id": conversation.get("conversation_id") or task.get("conversation_id") or task.get("task_id"),
        "conversation_generation": int(conversation.get("conversation_generation") or 1),
        "conversation_state": conversation.get("conversation_state") or "archived",
        "channel": conversation.get("channel") or "app",
        "queue_position": _codex_task_queue_position(task),
        "prompt_preview": prompt[:240],
        "response_preview": response[:600],
        "message_kind": task.get("message_kind") or "",
        "agent_role": task.get("agent_role") or "",
        "parent_job_id": task.get("parent_job_id") or "",
        "delivery_state": task.get("delivery_state") or "",
        "plan_id": task.get("plan_id") or "",
        "agent_state": task.get("agent_state") or "",
        "current_step": task.get("current_step") or "",
        "required_input": _codex_public_required_input(task.get("required_input")),
        "proposal_id": str((task.get("proposal") or {}).get("proposal_id") or "") if isinstance(task.get("proposal"), dict) else "",
        "verification": _codex_public_verification(task.get("verification")),
        "memory_excluded": bool(task.get("memory_excluded")),
        "report_id": task.get("report_id") or "",
        "report_formats": list(task.get("report_formats") or []),
        "context_stats": _codex_public_context_stats(task.get("context_stats")),
        "observability": _codex_public_observability(_codex_observability_from_task(task)),
        "mcp_migration": _codex_public_mcp_migration(task.get("mcp_migration")),
        "token_usage": task.get("token_usage") if isinstance(task.get("token_usage"), dict) else {},
    }





def codex_register_report_history(
    *,
    client_id: str,
    username: str,
    prompt: str,
    report: dict[str, Any],
    thread_id: str = "",
    conversation_id: str = "",
    screen_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report_id = str((report or {}).get("report_id") or "").strip()
    if not report_id:
        return {}
    state = _codex_load_or_create_conversation_state(
        str(client_id or "default"),
        str(username or "").strip().lower(),
        channel="app",
    )
    canonical_conversation_id = str(state.get("conversation_id") or "")
    task_id = report_id
    now = _codex_now()
    created_at = str((report or {}).get("created_at") or now)
    chat_text = str((report or {}).get("chat_text") or "").strip()
    title = str((report or {}).get("title") or f"Relatorio {BLACK_JHON_DISPLAY_NAME}").strip()
    formats = []
    if isinstance((report or {}).get("chat_download_formats"), list):
        formats = [str(fmt or "").strip().lower() for fmt in report.get("chat_download_formats") or [] if str(fmt or "").strip()]
    elif isinstance((report or {}).get("formats"), dict):
        formats = [fmt for fmt, enabled in (report.get("formats") or {}).items() if enabled]
    task = {
        "task_id": task_id,
        "status": "completed",
        "sandbox": "read_only",
        "cwd": _codex_base_dir(),
        "thread_id": "",
        "conversation_id": canonical_conversation_id,
        "conversation_generation": int(state.get("generation") or 1),
        "prompt": str(prompt or title).strip() or title,
        "model": "codex-assistant-report",
        "approval_mode": "read_only",
        "reasoning_effort": "",
        "speed": "",
        "service_tier": "",
        "goal": "",
        "planning_mode": False,
        "paths": [],
        "screen_context": screen_context if isinstance(screen_context, dict) else {},
        "history": [],
        "context_stats": {},
        "app_data_context": {},
        "conversation_summary": {},
        "conversation_compaction": {},
        "agent_mode": False,
        "agent_steps": list((report or {}).get("status_steps") or []),
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": list((report or {}).get("sources") or []),
        "warnings": list((report or {}).get("warnings") or []),
        "live_status": "",
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
        "token_usage": {},
        "turn_id": "",
        "final_response": chat_text or title,
        "error": "",
        "message_kind": "report",
        "memory_excluded": True,
        "report_id": report_id,
        "report_formats": formats,
        "logs": [{"at": now, "text": f"Relatorio {BLACK_JHON_DISPLAY_NAME} gerado e persistido no historico.", "kind": "report"}],
        "created_at": created_at,
        "started_at": created_at,
        "completed_at": created_at,
        "created_by": str(username or ""),
        "client_id": str(client_id or "default"),
        "origin": "app",
        "channel_metadata": {},
        "approval_required": False,
        "approved": True,
    }
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    return _codex_public_task(task)

def _codex_public_action_run(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if not item:
        return {}
    return {
        "run_id": str(item.get("run_id") or item.get("action_run_id") or "")[:120],
        "action_id": str(item.get("action_id") or "")[:120],
        "status": str(item.get("status") or "")[:40],
        "error_code": str(item.get("error_code") or "")[:100],
        "created_at": str(item.get("created_at") or "")[:40],
        "completed_at": str(item.get("completed_at") or "")[:40],
        "verification": _codex_public_verification(item.get("verification")),
    }



def _codex_public_context_stats(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    public: dict[str, Any] = {}
    for key in _CODEX_PUBLIC_CONTEXT_STAT_FIELDS:
        if key not in item:
            continue
        try:
            public[key] = max(0, min(int(item.get(key) or 0), 2_000_000_000))
        except (TypeError, ValueError):
            public[key] = 0
    for key in ("conversation_compacted", "agent_mode"):
        if key in item:
            public[key] = item.get(key) is True
    return public



def _codex_public_conversation_metadata(task: dict[str, Any]) -> dict[str, Any]:
    # Legacy tasks sometimes used the technical Codex thread as conversation
    # identity. Public projections must never derive an identifier from it.
    public_task = dict(task or {})
    public_task["thread_id"] = ""
    return _codex_task_conversation_metadata(public_task)



def _codex_public_mcp_migration(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if not item:
        return {}
    public: dict[str, Any] = {
        "target": "jk_system_mcp" if item.get("target") == "jk_system_mcp" else "",
        "native_enabled": item.get("native_enabled") is True,
        "native_active": item.get("native_active") is True,
        "legacy_parser_fallback": item.get("legacy_parser_fallback") is True,
        "fallback_used": item.get("fallback_used") is True,
    }
    if isinstance(item.get("shadow_observed"), bool):
        public["shadow_observed"] = item.get("shadow_observed") is True
    if isinstance(item.get("external_call_executed"), bool):
        public["external_call_executed"] = item.get("external_call_executed") is True
    shadow_status = str(item.get("shadow_status") or "").strip().lower()
    if shadow_status in {"prepared", "match", "divergence", "not_eligible", "invalid", "metrics_unavailable", "stale_rollout"}:
        public["shadow_status"] = shadow_status
    for field in ("shadow_plan_tools_count", "shadow_legacy_tools_count"):
        if field not in item:
            continue
        try:
            public[field] = max(0, min(int(item.get(field) or 0), 100))
        except (TypeError, ValueError):
            public[field] = 0
    rollout_mode = str(item.get("rollout_mode") or "").strip().lower()
    if rollout_mode in codex_mcp_rollout.ROLLOUT_MODES:
        public["rollout_mode"] = rollout_mode
    try:
        public["rollout_version"] = max(0, int(item.get("rollout_version") or 0))
    except (TypeError, ValueError):
        public["rollout_version"] = 0
    disabled_reason = str(item.get("disabled_reason") or "").strip()
    if disabled_reason in {"rollout_or_plan_not_eligible", "data_selection_cutover"}:
        public["disabled_reason"] = disabled_reason
    fallback_boundary = str(item.get("fallback_boundary") or "").strip()
    if fallback_boundary in {"before_first_external_call", "before_first_external_call_only"}:
        public["fallback_boundary"] = fallback_boundary
    fallback_error_code = str(item.get("fallback_error_code") or "").strip()
    if item.get("fallback_error") not in (None, ""):
        fallback_error_code = "MCP_RUNTIME_UNAVAILABLE"
    if fallback_error_code == "MCP_RUNTIME_UNAVAILABLE":
        public["fallback_error_code"] = fallback_error_code
    return public



def _codex_public_observability(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    try:
        records = max(0, min(int(item.get("records") or 0), 2_000_000_000))
    except (TypeError, ValueError):
        records = 0
    failures = [
        _codex_sanitize_log_text(entry, 240)
        for entry in list(item.get("failures") or [])[:6]
    ]
    next_fallbacks = [
        _codex_sanitize_log_text(entry, 180)
        for entry in list(item.get("next_fallbacks") or [])[:8]
    ]
    sources = [
        _codex_sanitize_log_text(entry, 180)
        for entry in list(item.get("sources") or [])[:8]
    ]
    source = _codex_sanitize_log_text(item.get("source") or "", 180)
    return {
        "current_status": _codex_sanitize_log_text(item.get("current_status") or "", 160),
        "last_tool": _codex_sanitize_log_text(item.get("last_tool") or "", 120),
        "last_tool_module": _codex_public_stable_code(item.get("last_tool_module"), 100),
        "source": source,
        "sources": [entry for entry in sources if entry],
        "records": records,
        "confidence": _codex_public_stable_code(item.get("confidence"), 40),
        "evidence_status": _codex_public_stable_code(item.get("evidence_status"), 40),
        "claim_scope": _codex_public_stable_code(item.get("claim_scope"), 40),
        "coverage_complete": item.get("coverage_complete") is True,
        "failures": [entry for entry in failures if entry],
        "next_fallbacks": [entry for entry in next_fallbacks if entry],
        "tool_calls_count": max(0, min(int(item.get("tool_calls_count") or 0), 100_000)),
        "tool_results_count": max(0, min(int(item.get("tool_results_count") or 0), 100_000)),
        "updated_at": str(item.get("updated_at") or "")[:40],
    }



def _codex_public_proposal(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if not item:
        return {}
    try:
        version = max(0, int(item.get("version") or 0))
    except (TypeError, ValueError):
        version = 0
    return {
        "proposal_id": str(item.get("proposal_id") or "")[:120],
        "version": version,
        "proposal_hash": str(item.get("proposal_hash") or "")[:128],
        "action_id": str(item.get("action_id") or item.get("capability_id") or "")[:120],
        "status": str(item.get("status") or "awaiting_approval")[:40],
        "risk": str(item.get("risk") or "")[:40],
        "summary": _codex_sanitize_log_text(item.get("summary") or "", 240),
    }



def _codex_public_query_policy(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if not item:
        return {}
    allowed_domains = {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    return {
        "mode": str(item.get("mode") or "")[:40],
        "domains": [
            str(domain) for domain in list(item.get("domains") or [])[:10]
            if str(domain) in allowed_domains
        ],
        "read_only": item.get("read_only") is True,
        "deny_approval": item.get("deny_approval") is True,
        "store_required": item.get("store_required") is True,
        "store_mode": str(item.get("store_mode") or "")[:40],
    }



def _codex_public_required_input(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or [])[:20]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "field": _codex_public_stable_code(item.get("field") or item.get("id"), 100),
                "label": _codex_sanitize_log_text(item.get("label") or "", 160),
                "message": _codex_sanitize_log_text(item.get("message") or item.get("question") or "", 240),
                "required": item.get("required") is not False,
            }
        )
    return rows



def _codex_public_scope(value: Any) -> dict[str, Any]:
    scope = value if isinstance(value, dict) else {}
    return {
        "sandbox": "read_only",
        "enforced": True,
        "modules": [str(item or "")[:80] for item in list(scope.get("modules") or [])[:30]],
        "reason": str(scope.get("reason") or "internal_read_only")[:100],
    }



def _codex_public_sources(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in list(value or [])[:50]:
        if isinstance(item, dict):
            source_id = str(item.get("citation_id") or item.get("source_id") or item.get("id") or "")[:120]
            source_type = str(item.get("type") or item.get("source") or "internal")[:80]
            title = _codex_sanitize_log_text(item.get("title") or item.get("label") or "", 180)
            url = str(item.get("url") or "").strip()
            if url and not re.match(r"^https://", url, re.IGNORECASE):
                url = ""
        else:
            text = _codex_sanitize_log_text(item, 180)
            if not text:
                continue
            source_id, source_type, title, url = "", "internal", text, ""
        rows.append({"source_id": source_id, "type": source_type, "title": title, "url": url[:500]})
    return rows



def _codex_public_stable_code(value: Any, limit: int = 100) -> str:
    text = str(value or "").strip()
    maximum = max(1, min(int(limit or 1), 160))
    if not text or len(text) > maximum:
        return ""
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", text) else ""



def _codex_public_status_events(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or [])[-120:]:
        if not isinstance(item, dict):
            continue
        try:
            cycle = max(0, int(item.get("cycle") or 0))
        except (TypeError, ValueError):
            cycle = 0
        rows.append(
            {
                "cycle": cycle,
                "kind": str(item.get("kind") or "status")[:40],
                "status": _codex_sanitize_log_text(item.get("status") or item.get("text") or "", 180),
                "tool_id": str(item.get("tool_id") or "")[:100],
                "at": str(item.get("at") or "")[:40],
            }
        )
    return rows



def _codex_public_steer_events(value: Any) -> list[dict[str, str]]:
    """Expose steer lifecycle metadata without projecting user messages."""

    rows: list[dict[str, str]] = []
    for item in list(value or [])[-20:]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "request_id": _codex_public_stable_code(item.get("request_id"), 120),
                "mode": _codex_public_stable_code(item.get("mode"), 40),
                "created_at": _codex_public_stable_code(item.get("created_at"), 40),
            }
        )
    return rows



def _codex_public_tool_summaries(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or [])[:80]:
        if not isinstance(item, dict):
            continue
        try:
            count = max(0, int(item.get("count") or item.get("total") or 0))
        except (TypeError, ValueError):
            count = 0
        rows.append(
            {
                "tool_id": str(item.get("tool_id") or item.get("name") or "")[:100],
                "status": str(item.get("status") or "")[:40],
                "count": count,
            }
        )
    return rows



def _codex_public_verification(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if not item:
        return {}
    return {
        "status": _codex_public_stable_code(item.get("status"), 40),
        "confirmed": item.get("confirmed") is True,
        "error_code": _codex_public_stable_code(item.get("error_code"), 100),
    }
__codex_dependencies__ = ['BLACK_JHON_DISPLAY_NAME', 'CODEX_DEVELOPMENT_ERROR_CODE', 'CODEX_DEVELOPMENT_WRITE_ENABLED', 'CODEX_EXECUTION_PLANE', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_codex_base_dir', '_codex_base_info_dir', '_codex_load_or_create_conversation_state', '_codex_load_task', '_codex_now', '_codex_persist_task', '_codex_sanitize_log_text', '_codex_task_conversation_metadata', '_codex_task_queue_position', '_codex_update_task']

__codex_exports__ = ['_codex_observability_from_task', '_codex_agent_guidance_context', '_codex_sync_plan_fields', '_codex_transition_task_plan', '_codex_public_task', '_codex_task_summary', 'codex_register_report_history', '_codex_public_action_run', '_codex_public_context_stats', '_codex_public_conversation_metadata', '_codex_public_mcp_migration', '_codex_public_observability', '_codex_public_proposal', '_codex_public_query_policy', '_codex_public_required_input', '_codex_public_scope', '_codex_public_sources', '_codex_public_stable_code', '_codex_public_status_events', '_codex_public_steer_events', '_codex_public_tool_summaries', '_codex_public_verification']
