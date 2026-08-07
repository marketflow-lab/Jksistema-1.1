"""Codex console agent loop component."""

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


def _codex_agent_unique_extend(target: list[Any], values: Any) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        if value not in target:
            target.append(value)


def _codex_agent_update_trace(task_id: str, trace: dict[str, Any], **updates: Any) -> None:
    _codex_update_live(
        task_id,
        agent_steps=list(trace.get("agent_steps") or [])[-80:],
        tool_calls=list(trace.get("tool_calls") or [])[-80:],
        tool_results_summary=list(trace.get("tool_results_summary") or [])[-80:],
        sources=list(trace.get("sources") or [])[-80:],
        warnings=list(trace.get("warnings") or [])[-80:],
        **updates,
    )


def _codex_agent_source_policy_for_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    return dict(source_policy)


def _codex_agent_source_policy_error(
    tool_id: str,
    source_policy: dict[str, Any],
    previous_results: list[dict[str, Any]],
) -> str:
    if not source_policy:
        return ""
    tool_id = str(tool_id or "").strip()
    required = [str(item or "").strip() for item in (source_policy.get("required_tools") or []) if str(item or "").strip()]
    forbidden = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    if tool_id in forbidden:
        return "Fonte bloqueada pela politica do WhatsApp para este tipo de dado."
    if source_policy.get("full_exclusive") is True and tool_id not in set(required) | {"integrations_status"}:
        return "Estoque Full aceita exclusivamente a ferramenta de inventario Full da API do Mercado Livre."
    attempted = {
        str(item.get("tool_id") or "").strip()
        for item in previous_results
        if isinstance(item, dict) and str(item.get("tool_id") or "").strip()
    }
    pending_required = [item for item in required if item not in attempted]
    if pending_required and tool_id not in pending_required:
        return "Antes de qualquer fallback, consulte as fontes obrigatorias: " + ", ".join(pending_required)
    return ""


def _codex_agent_order_calls_by_source_policy(
    calls: list[dict[str, Any]],
    source_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run required sources in their deterministic policy order."""

    required = [
        str(item or "").strip()
        for item in (source_policy.get("required_tools") or [])
        if str(item or "").strip()
    ]
    if not required:
        return list(calls or [])
    rank = {tool_id: index for index, tool_id in enumerate(required)}
    indexed = list(enumerate(calls or []))
    indexed.sort(
        key=lambda pair: (
            0 if str((pair[1] or {}).get("tool_id") or "") in rank else 1,
            rank.get(str((pair[1] or {}).get("tool_id") or ""), len(rank)),
            pair[0],
        )
    )
    return [call for _index, call in indexed]




























def _codex_model_dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", by_alias=False)
        except TypeError:
            return value.model_dump()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_codex_model_dump(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _codex_model_dump(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _codex_token_usage_dict(value: Any) -> dict[str, Any]:
    dumped = _codex_model_dump(value)
    return dumped if isinstance(dumped, dict) else {}


def _codex_final_response_from_items(items: list[Any], fallback: str = "") -> str:
    last_text = ""
    for item in reversed(items):
        root = getattr(item, "root", item)
        text = str(getattr(root, "text", "") or "").strip()
        if not text:
            continue
        phase = getattr(root, "phase", None)
        phase_value = str(getattr(phase, "value", phase) or "")
        if phase_value == "final_answer":
            return text
        if not last_text:
            last_text = text
    return last_text or str(fallback or "").strip()


def _codex_item_label(item: Any) -> str:
    root = getattr(item, "root", item)
    item_type = str(getattr(root, "type", "") or root.__class__.__name__)
    labels = {
        "agent_message": "Gerando resposta",
        "reasoning": "Raciocinando",
        "command_execution": "Executando comando",
        "file_change": "Preparando alteracao de arquivo",
        "plan": "Atualizando plano",
        "mcp_tool_call": "Usando ferramenta",
        "dynamic_tool_call": "Usando ferramenta",
        "web_search": "Pesquisando",
        "collab_agent_tool_call": "Usando agente auxiliar",
    }
    return labels.get(item_type, item_type or "Etapa")


def _codex_plan_text(plan: Any, explanation: Optional[str] = None) -> str:
    parts: list[str] = []
    if explanation:
        parts.append(str(explanation).strip())
    if isinstance(plan, list):
        for idx, step in enumerate(plan[:12], 1):
            dumped = _codex_model_dump(step)
            if isinstance(dumped, dict):
                text = dumped.get("step") or dumped.get("text") or dumped.get("title") or dumped.get("description") or str(dumped)
                status = dumped.get("status") or ""
                parts.append(f"{idx}. {text}" + (f" [{status}]" if status else ""))
            else:
                parts.append(f"{idx}. {dumped}")
    return "\n".join(part for part in parts if str(part).strip())[:4000]


def _codex_update_live(task_id: str, **updates: Any) -> dict[str, Any]:
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
        if not task:
            raise KeyError(task_id)
        task.update(updates)
        _codex_persist_task(task)
        return task


def _codex_register_active_turn(task_id: str, turn: Any) -> None:
    turn_id = str(getattr(turn, "id", "") or "").strip()
    with CODEX_ACTIVE_TURNS_LOCK:
        CODEX_ACTIVE_TURNS[task_id] = turn
    _codex_update_live(task_id, turn_id=turn_id, active_turn_id=turn_id, can_steer=bool(turn_id))


def _codex_unregister_active_turn(task_id: str, turn: Any = None) -> None:
    with CODEX_ACTIVE_TURNS_LOCK:
        current = CODEX_ACTIVE_TURNS.get(task_id)
        if turn is None or current is turn:
            CODEX_ACTIVE_TURNS.pop(task_id, None)
    try:
        _codex_update_live(task_id, active_turn_id="", can_steer=False)
    except KeyError:
        pass


def _codex_interrupt_active_turn(task_id: str) -> bool:
    with CODEX_ACTIVE_TURNS_LOCK:
        turn = CODEX_ACTIVE_TURNS.get(str(task_id or ""))
    if turn is None:
        return False
    try:
        turn.interrupt()
        return True
    except Exception:
        return False


def _codex_stream_update(
    task_id: str,
    state: dict[str, Any],
    now: float,
    force: bool = False,
    **updates: Any,
) -> None:
    last = float(state.get("last_update_at") or 0)
    if not force and now - last < 0.75:
        state.setdefault("pending_updates", {}).update(updates)
        return
    pending = state.pop("pending_updates", {})
    pending.update(updates)
    if pending:
        _codex_update_live(task_id, **pending)
    state["last_update_at"] = now


def _codex_process_stream_content_event(
    task_id: str,
    task: dict[str, Any],
    method: str,
    payload: Any,
    state: dict[str, Any],
    now: float,
) -> bool:
    update = lambda force=False, **values: _codex_stream_update(task_id, state, now, force, **values)
    if method == "turn/started":
        _codex_log(task, "Turno iniciado no Codex.", "event")
        update(True, live_status="Turno iniciado no Codex.", wait_reason="codex_turn")
    elif method == "item/started":
        label = _codex_item_label(getattr(payload, "item", None))
        _codex_log(task, f"{label}...", "event")
        update(True, live_status=f"{label}...")
    elif method == "item/completed":
        item = getattr(payload, "item", None)
        if item is not None:
            state.setdefault("items", []).append(item)
        label = _codex_item_label(item)
        _codex_log(task, f"{label} concluido.", "event")
        update(live_status=f"{label} concluido.")
    elif method == "item/agentMessage/delta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["live_answer"] = (state.get("live_answer") or "") + delta
            update(live_status="Gerando resposta...", live_answer=state["live_answer"][-8000:])
    elif method == "item/reasoning/summaryPartAdded":
        _codex_log(task, "Codex iniciou um resumo de raciocinio.", "reasoning")
        update(live_status="Raciocinando com resumo disponivel...", wait_reason="data_analysis")
    elif method == "item/reasoning/summaryTextDelta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["reasoning_summary"] = (state.get("reasoning_summary") or "") + delta
            update(live_status="Raciocinando...", wait_reason="data_analysis", reasoning_summary=state["reasoning_summary"][-8000:])
    elif method == "item/reasoning/textDelta":
        update(live_status="Raciocinando...")
    elif method == "item/plan/delta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["live_plan"] = (state.get("live_plan") or "") + delta
            update(live_status="Atualizando plano...", live_plan=state["live_plan"][-6000:])
    elif method == "turn/plan/updated":
        plan_text = _codex_plan_text(getattr(payload, "plan", None), getattr(payload, "explanation", None))
        if plan_text:
            _codex_log(task, "Plano atualizado.", "plan")
            update(True, live_status="Plano atualizado.", live_plan=plan_text)
    else:
        return False
    return True


def _codex_process_stream_runtime_event(
    task_id: str,
    task: dict[str, Any],
    method: str,
    payload: Any,
    state: dict[str, Any],
    now: float,
) -> None:
    update = lambda force=False, **values: _codex_stream_update(task_id, state, now, force, **values)
    if method in {"item/commandExecution/outputDelta", "command/exec/outputDelta"}:
        text = str(getattr(payload, "delta", "") or "").replace("\r", "").strip()
        if text:
            _codex_log(task, "Saida de comando: " + text[:300], "command")
        update(live_status="Executando comando...")
    elif method == "item/fileChange/patchUpdated":
        changes = getattr(payload, "changes", None) or []
        _codex_log(task, f"Patch atualizado ({len(changes)} alteracao/alteracoes).", "file")
        update(True, live_status="Preparando alteracao de arquivo...")
    elif method == "item/mcpToolCall/progress":
        message = str(getattr(payload, "message", "") or "").strip()
        if message:
            _codex_log(task, message, "tool")
            update(live_status=message[:240], wait_reason="api_query")
    elif method == "model/rerouted":
        message = f"Modelo redirecionado: {getattr(payload, 'from_model', '')} -> {getattr(payload, 'to_model', '')}".strip()
        _codex_log(task, message, "event")
        update(True, live_status=message)
    elif method == "thread/tokenUsage/updated":
        usage = _codex_token_usage_dict(getattr(payload, "token_usage", None))
        if usage:
            state["token_usage"] = usage
            update(token_usage=usage)
    elif method == "turn/completed":
        state["completed_turn"] = getattr(payload, "turn", None)
        usage = _codex_token_usage_dict(getattr(payload, "token_usage", None))
        if usage:
            state["token_usage"] = usage
        update(True, live_status="Codex concluiu o turno.")
    elif method in {"error", "warning"}:
        fallback = "Erro no stream do Codex." if method == "error" else "Aviso do Codex."
        message = str(getattr(payload, "message", "") or getattr(payload, "error", "") or fallback)
        _codex_log(task, message, method)
        update(method == "error", live_status=message[:240])


def _codex_process_stream_event(
    task_id: str,
    task: dict[str, Any],
    event: Any,
    state: dict[str, Any],
) -> None:
    method = str(getattr(event, "method", "") or "")
    payload = getattr(event, "payload", None)
    now = time.time()
    if _codex_process_stream_content_event(task_id, task, method, payload, state, now):
        return
    _codex_process_stream_runtime_event(task_id, task, method, payload, state, now)


def _codex_normalizar_model(value: Optional[str]) -> str:
    model = str(value or os.getenv("JK_CODEX_MODEL") or CODEX_DEFAULT_MODEL).strip()
    if not model:
        return CODEX_DEFAULT_MODEL
    if len(model) > 80 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", model):
        raise HTTPException(status_code=400, detail="Modelo Codex invalido.")
    return model


def _codex_normalizar_reasoning_effort(value: Optional[str]) -> str:
    raw = str(value or os.getenv("JK_CODEX_REASONING_EFFORT") or "xhigh").strip().lower()
    aliases = {
        "nenhum": "none",
        "none": "none",
        "minimo": "minimal",
        "minimal": "minimal",
        "baixa": "low",
        "baixo": "low",
        "low": "low",
        "media": "medium",
        "medio": "medium",
        "medium": "medium",
        "alta": "high",
        "alto": "high",
        "high": "high",
        "altissimo": "xhigh",
        "altissima": "xhigh",
        "xhigh": "xhigh",
    }
    effort = aliases.get(raw, raw)
    if effort not in CODEX_REASONING_EFFORTS:
        raise HTTPException(status_code=400, detail="Nivel de raciocinio Codex invalido.")
    return effort


def _codex_reasoning_effort_enum(value: Optional[str]):
    effort = _codex_normalizar_reasoning_effort(value)
    from openai_codex.generated.v2_all import ReasoningEffort

    return getattr(ReasoningEffort, effort)


def _codex_sdk_response_compat(value: Any) -> Any:
    """Keep the beta Python SDK compatible with newer Codex protocol values."""
    if isinstance(value, dict):
        return {
            key: (
                "xhigh"
                if key == "reasoningEffort" and item in {"max", "ultra"}
                else _codex_sdk_response_compat(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_codex_sdk_response_compat(item) for item in value]
    return value


def _codex_apply_sdk_protocol_compat() -> None:
    from openai_codex.client import CodexClient
    from openai_codex.errors import CodexError

    if getattr(CodexClient.request, "_jk_protocol_compat", False):
        return

    def request_compat(self, method, params, *, response_model):
        result = self._request_raw(method, params)
        if not isinstance(result, dict):
            raise CodexError(f"{method} response must be a JSON object")
        return response_model.model_validate(_codex_sdk_response_compat(result))

    request_compat._jk_protocol_compat = True
    CodexClient.request = request_compat


def _codex_normalizar_speed(value: Optional[str]) -> str:
    raw = str(value or os.getenv("JK_CODEX_SPEED") or "standard").strip().lower()
    aliases = {
        "padrao": "standard",
        "standard": "standard",
        "default": "standard",
        "rapido": "fast",
        "fast": "fast",
    }
    speed = aliases.get(raw, raw)
    if speed not in CODEX_SPEEDS:
        raise HTTPException(status_code=400, detail="Velocidade Codex invalida.")
    return speed


def _codex_normalizar_service_tier(value: Optional[str], speed: str) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if raw in {"", "standard", "default", "none"}:
        # O catalogo do app-server identifica o modo Fast pelo tier interno
        # ``priority`` (nome exibido: Fast). A flag ``features.fast_mode`` e
        # habilitada separadamente no runtime que executa a tarefa.
        return "priority" if speed == "fast" else None
    if raw in {"priority", "auto", "flex"}:
        return raw
    raise HTTPException(status_code=400, detail="Service tier Codex invalido.")


def _codex_normalizar_approval_profile(value: Optional[str], sandbox: str) -> str:
    raw = str(value or "").strip().lower()
    if sandbox == "full_access":
        return "full_access"
    if sandbox == "read_only":
        return "read_only"
    aliases = {
        "request": "request",
        "ask": "request",
        "solicitar": "request",
        "solicitar_aprovacao": "request",
        "auto": "auto",
        "auto_review": "auto",
        "aprovar_por_mim": "auto",
        "full": "full_access",
        "full_access": "full_access",
        "acesso_completo": "full_access",
        "deny_all": "read_only",
        "read_only": "read_only",
    }
    return aliases.get(raw, "request")


def _codex_approval_mode_enum(approval_profile: str, sandbox: str):
    from openai_codex import ApprovalMode

    if sandbox == "read_only" or approval_profile == "read_only":
        return ApprovalMode.deny_all
    return ApprovalMode.auto_review

def _codex_capture_whatsapp_listing_bundle(task_id: str, task: dict[str, Any], result: Any) -> None:
    """Persist a bounded official-listing contract for channel delivery."""

    if str(task.get("origin") or "") != "whatsapp" or not isinstance(result, dict):
        return
    if str(result.get("tool_id") or "") != "mercado_livre_listing":
        return
    try:
        from backend.services.whatsapp import marketplace_listing_delivery

        bundle = marketplace_listing_delivery.build_listing_bundle([result])
    except Exception:
        return
    listings = [item for item in list(bundle.get("listings") or []) if isinstance(item, dict)][:20]
    if not listings:
        return
    for listing in listings:
        listing["pictures"] = [
            item for item in list(listing.get("pictures") or []) if isinstance(item, dict)
        ][:5]
        listing["description"] = str(listing.get("description") or "")[:1500]
    bundle["listings"] = listings
    bundle["listing_count"] = len(listings)
    bundle["picture_count"] = sum(len(item.get("pictures") or []) for item in listings)
    task["whatsapp_listing_bundle"] = bundle
    _codex_update_task(task_id, whatsapp_listing_bundle=bundle)
__codex_dependencies__ = ['CODEX_ACTIVE_TURNS', 'CODEX_ACTIVE_TURNS_LOCK', 'CODEX_AGENT_MAX_CYCLES', 'CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE', 'CODEX_AGENT_REPORT_MAX_CYCLES', 'CODEX_DEFAULT_MODEL', 'CODEX_REASONING_EFFORTS', 'CODEX_SPEEDS', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_codex_agent_authorize_planned_call', '_codex_agent_data_selection_from_task', '_codex_agent_data_selection_tool_ids', '_codex_agent_deadline_seconds', '_codex_agent_extract_tool_calls', '_codex_agent_planned_calls', '_codex_agent_results_prompt', '_codex_agent_tool_catalog', '_codex_agent_tool_status', '_codex_exact_ml_order_response', '_codex_generate_whatsapp_chart_artifacts', '_codex_int_env', '_codex_load_task', '_codex_log', '_codex_native_mcp_read_results', '_codex_now', '_codex_persist_task', '_codex_task_whatsapp_query_only', '_codex_texto_sem_acentos', '_codex_update_task', '_codex_whatsapp_bling_stock_response', '_codex_whatsapp_complete_ml_report', '_codex_whatsapp_prepare_agent_tool_call', '_codex_whatsapp_user_request_text']

__codex_exports__ = ['_codex_agent_unique_extend', '_codex_agent_update_trace', '_codex_agent_source_policy_for_task', '_codex_agent_source_policy_error', '_codex_agent_order_calls_by_source_policy', '_codex_model_dump', '_codex_token_usage_dict', '_codex_final_response_from_items', '_codex_item_label', '_codex_plan_text', '_codex_update_live', '_codex_register_active_turn', '_codex_unregister_active_turn', '_codex_interrupt_active_turn', '_codex_process_stream_event', '_codex_normalizar_model', '_codex_normalizar_reasoning_effort', '_codex_reasoning_effort_enum', '_codex_sdk_response_compat', '_codex_apply_sdk_protocol_compat', '_codex_normalizar_speed', '_codex_normalizar_service_tier', '_codex_normalizar_approval_profile', '_codex_approval_mode_enum', '_codex_capture_whatsapp_listing_bundle']
