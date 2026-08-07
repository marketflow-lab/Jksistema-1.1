"""Telemetry, routing and MCP shadow policy for Codex Console."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import base64
import uuid
from pathlib import Path
from typing import Any, Optional

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
from . import state as console_state


def _codex_agent_deadline_seconds(task: dict[str, Any], report_mode: bool) -> Optional[int]:
    """Return the total task deadline, or None for an explicitly unbounded task."""
    if task.get("deadline_enabled") is False or str(task.get("origin") or "").strip().lower() == "whatsapp":
        return None
    fallback = 600 if report_mode else 180
    return max(30, min(int(task.get("deadline_seconds") or fallback), 600))


def _codex_ai_telemetry_instance() -> codex_ai_telemetry.CodexAITelemetry:
    expected_root = Path(_codex_base_info_dir()).resolve()
    state = console_state.CONSOLE_STATE
    with state.telemetry_lock:
        telemetry = state.telemetry
        if telemetry is not None and telemetry.info_root != expected_root:
            telemetry.close()
            telemetry = None
        if telemetry is None:
            telemetry = codex_ai_telemetry.CodexAITelemetry(expected_root)
        state.telemetry = telemetry
        return telemetry


def _codex_decide_model(
    *,
    prompt: str,
    requested_model: str,
    rollout_key: str,
    channel_metadata: Any = None,
) -> codex_model_router.ModelDecisionV1:
    router = codex_model_router.ModelRouter(_codex_model_routing_policy())
    return router.decide(
        category=_codex_model_category(prompt, channel_metadata),
        requested_model=requested_model,
        rollout_key=rollout_key,
        available_models={codex_model_router.BASELINE_MODEL, *codex_model_router.GPT_56_VARIANTS},
    )


def _codex_hmac_identifier(value: Any, *, namespace: str) -> str:
    if not str(value or ""):
        return ""
    return codex_ai_telemetry.secure_hmac_identifier(value, namespace=namespace)


def _codex_mcp_shadow_call_fingerprint(tool_id: Any, arguments: Any) -> str:
    payload = {
        "tool_id": str(tool_id or "").strip(),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _codex_mcp_shadow_finish(probe: Any, agent_trace: Any) -> dict[str, Any]:
    """Compare the legacy calls with the validated Shadow plan, content-free."""

    state = probe if isinstance(probe, dict) else {}
    status = str(state.get("status") or "")
    if status != "prepared":
        return {
            "shadow_observed": False,
            "shadow_status": "not_eligible" if status == "not_eligible" else "invalid",
            "external_call_executed": False,
        } if status else {}
    trace = agent_trace if isinstance(agent_trace, dict) else {}
    actual_calls = [
        _codex_mcp_shadow_call_fingerprint(item.get("tool_id"), item.get("args"))
        for item in list(trace.get("tool_calls") or [])
        if isinstance(item, dict) and str(item.get("protocol") or "legacy") != "mcp"
    ]
    expected = list(state.get("expected_calls") or [])
    expected_calls = [str(item.get("fingerprint") or "") for item in expected if isinstance(item, dict)]
    required_calls = {
        str(item.get("fingerprint") or "")
        for item in expected
        if isinstance(item, dict) and item.get("required") is True
    }
    sequence_valid = actual_calls == expected_calls[: len(actual_calls)]
    required_covered = required_calls.issubset(set(actual_calls))
    matched = sequence_valid and required_covered
    try:
        path_text = str(state.get("rollout_db_path") or "").strip()
        if not path_text:
            raise RuntimeError("shadow_rollout_store_missing")
        store = codex_mcp_rollout.MCPRolloutPolicyStore(Path(path_text).resolve())
        current_policy = store.get()
        if (
            str(current_policy.get("mode") or "off") != "shadow"
            or int(current_policy.get("version") or 0) != int(state.get("rollout_version") or 0)
        ):
            return {
                "shadow_observed": False,
                "shadow_status": "stale_rollout",
                "shadow_plan_tools_count": len(expected_calls),
                "shadow_legacy_tools_count": len(actual_calls),
                "external_call_executed": False,
            }
        store.record_metric(
            task_id=str(state.get("task_id") or "shadow-task"),
            execution_id=str(state.get("execution_id") or uuid.uuid4().hex),
            event_id=f"shadow-comparison-v1:{int(state.get('rollout_version') or 0)}",
            status="shadow_match" if matched else "shadow_divergence",
            error_code="" if matched else "shadow_decision_divergence",
        )
    except Exception:
        return {
            "shadow_observed": False,
            "shadow_status": "metrics_unavailable",
            "shadow_plan_tools_count": len(expected_calls),
            "shadow_legacy_tools_count": len(actual_calls),
            "external_call_executed": False,
        }
    return {
        "shadow_observed": True,
        "shadow_status": "match" if matched else "divergence",
        "shadow_plan_tools_count": len(expected_calls),
        "shadow_legacy_tools_count": len(actual_calls),
        "external_call_executed": False,
    }


def _codex_mcp_shadow_prepare(
    task: dict[str, Any],
    screen_context: Any,
    rollout_policy: Any,
) -> dict[str, Any]:
    """Materialize and validate a Shadow plan without starting MCP or calling tools."""

    policy = rollout_policy if isinstance(rollout_policy, dict) else {}
    if str(policy.get("mode") or "off") != "shadow":
        return {}
    try:
        thread_config = _codex_native_mcp_thread_config(task, screen_context)
        server = ((thread_config.get("mcp_servers") or {}).get("jk_system") or {})
        env = server.get("env") if isinstance(server.get("env"), dict) else {}
        encoded = str(env.get("JK_CODEX_MCP_CONTEXT_B64") or "").strip()
        signature = str(env.get("JK_CODEX_MCP_CONTEXT_SIGNATURE") or "").strip().lower()
        secret = str(env.get("JK_CODEX_MCP_CONTEXT_SECRET") or "")
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not encoded or not signature or not secret or not hmac.compare_digest(signature, expected_signature):
            raise RuntimeError("signed_context_invalid")
        padded = encoded + "=" * (-len(encoded) % 4)
        context = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(context, dict):
            raise RuntimeError("signed_context_invalid")
        task_client = _codex_safe_id(str(task.get("client_id") or "default"))
        context_client = _codex_safe_id(str(context.get("client_id") or "default"))
        expected_rollout_path = (
            Path(_codex_base_info_dir()) / task_client / "codex_ai" / "mcp_rollout.sqlite3"
        ).resolve()
        context_rollout_path = Path(str(context.get("rollout_policy_db_path") or "")).resolve()
        if context_client != task_client or context_rollout_path != expected_rollout_path:
            raise RuntimeError("mcp_rollout_store_scope_invalid")
        from backend.services import jk_codex_mcp_server

        plan = jk_codex_mcp_server.validate_plan_v2(context)
        expected_calls = [
            {
                "fingerprint": _codex_mcp_shadow_call_fingerprint(call.get("tool_id"), call.get("arguments")),
                "required": call.get("required") is not False,
            }
            for call in list(plan.get("calls") or [])
            if isinstance(call, dict)
        ]
        if not expected_calls:
            raise RuntimeError("mcp_plan_materialization_incomplete")
        return {
            "status": "prepared",
            "task_id": str(context.get("task_id") or task.get("task_id") or ""),
            "execution_id": str(context.get("execution_id") or uuid.uuid4().hex),
            "rollout_db_path": str(expected_rollout_path),
            "rollout_version": max(0, int(policy.get("version") or 0)),
            "expected_calls": expected_calls,
        }
    except RuntimeError as exc:
        code = str(exc)
        if code in {"mcp_plan_materialization_incomplete", "mcp_plan_store_refs_required"}:
            return {"status": "not_eligible", "reason": "plan_or_store_scope_incomplete"}
        return {"status": "invalid", "reason": "shadow_plan_validation_failed"}
    except Exception:
        return {"status": "not_eligible", "reason": "plan_or_store_scope_incomplete"}


def _codex_model_category(prompt: str, channel_metadata: Any = None) -> str:
    text = _codex_texto_sem_acentos(prompt)
    metadata = channel_metadata if isinstance(channel_metadata, dict) else {}
    lane = str(metadata.get("agent_lane") or metadata.get("agent_role") or "").lower()
    if "public" in lane or re.search(r"\b(pergunta publica|pos-venda|pos venda|comprador)\b", text):
        return "public_question"
    if re.search(r"\b(compatibilidade|serve|aplica|encaixa|codigo da peca|veiculo|motor)\b", text):
        return "fitment"
    if re.search(r"\b(relatorio|ranking|comparacao|compare|consolidado)\b", text):
        return "report"
    if re.search(r"\b(context hub|obsidian|rag|fonte documental)\b", text):
        return "context_synthesis"
    if re.search(r"\b(estoque|venda|pedido|anuncio|mercado livre|bling|devolucao)\b", text):
        return "commerce_query"
    return "general"


def _codex_model_routing_policy() -> codex_model_router.ModelRoutingPolicyV1:
    raw = str(os.getenv("JK_CODEX_MODEL_ROLLOUT_JSON") or "").strip()
    if not raw:
        return codex_model_router.ModelRoutingPolicyV1.disabled()
    try:
        payload = json.loads(raw)
        categories = payload.get("categories") if isinstance(payload, dict) else {}
        if not isinstance(categories, dict):
            raise ValueError("categories_invalid")
        return codex_model_router.ModelRoutingPolicyV1.rollout(
            policy_version=str(payload.get("policy_version") or "environment-v1"),
            categories={str(key): float(value) for key, value in categories.items()},
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return codex_model_router.ModelRoutingPolicyV1.disabled("invalid-policy-fallback")
__codex_dependencies__ = ['_codex_base_info_dir', '_codex_native_mcp_thread_config', '_codex_safe_id', '_codex_texto_sem_acentos']

__codex_exports__ = ['_codex_agent_deadline_seconds', '_codex_ai_telemetry_instance', '_codex_decide_model', '_codex_hmac_identifier', '_codex_mcp_shadow_call_fingerprint', '_codex_mcp_shadow_finish', '_codex_mcp_shadow_prepare', '_codex_model_category', '_codex_model_routing_policy']
