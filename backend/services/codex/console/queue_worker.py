"""Codex console queue worker component."""

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


def _codex_task_conversation_gate_key(task: dict[str, Any]) -> str:
    identity = "|".join(
        (
            str(task.get("client_id") or "default").strip().lower(),
            str(task.get("created_by") or "user").strip().lower(),
            _codex_task_channel(task),
            _codex_task_stored_conversation_id(task),
            str(int(task.get("conversation_generation") or 1)),
        )
    )
    return hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()


def _codex_task_queue_key(task: dict[str, Any]) -> str:
    base_key = _codex_task_conversation_gate_key(task)
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    group_id = str(metadata.get("job_group_id") or task.get("job_group_id") or "").strip()
    subtask_id = str(metadata.get("subtask_id") or task.get("subtask_id") or "").strip()
    if _codex_is_whatsapp_dual_worker(task) and group_id and subtask_id:
        return hashlib.sha256(
            f"{base_key}|{group_id}|{subtask_id}".encode("utf-8", "ignore")
        ).hexdigest()
    return base_key


def _codex_task_queue_position(task: dict[str, Any]) -> int:
    status = str(task.get("status") or "")
    if status != "queued":
        return 0
    key = _codex_task_queue_key(task)
    with CODEX_TASKS_LOCK:
        queued = [
            item
            for item in CODEX_TASKS.values()
            if isinstance(item, dict)
            and str(item.get("status") or "") == "queued"
            and _codex_task_queue_key(item) == key
        ]
    queued.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")))
    task_id = str(task.get("task_id") or "")
    for index, item in enumerate(queued, start=1):
        if str(item.get("task_id") or "") == task_id:
            return index
    return 0


def _codex_next_queued_task_id(queue_key: str) -> str:
    with CODEX_TASKS_LOCK:
        queued = [
            item
            for item in CODEX_TASKS.values()
            if isinstance(item, dict)
            and str(item.get("status") or "") == "queued"
            and _codex_task_queue_key(item) == queue_key
        ]
    queued.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")))
    return str(queued[0].get("task_id") or "") if queued else ""


def _codex_run_conversation_queue(queue_key: str) -> None:
    try:
        while True:
            task_id = _codex_next_queued_task_id(queue_key)
            if not task_id:
                return
            task = _codex_load_task(task_id)
            dual_worker = isinstance(task, dict) and _codex_is_whatsapp_dual_worker(task)
            acquired_dual_slot = False
            dual_gate_key = _codex_task_conversation_gate_key(task) if isinstance(task, dict) else ""
            if dual_worker:
                _codex_update_task(task_id, sol_queue_wait_started_at=_codex_now(), wait_reason="global_sol_capacity")
                acquired_dual_slot = CODEX_DUAL_SOL_GATE.acquire(task_id, dual_gate_key)
                if not acquired_dual_slot:
                    continue
                _codex_update_task(task_id, sol_started_at=_codex_now(), wait_reason="")
            try:
                _codex_run_worker(task_id)
            finally:
                if acquired_dual_slot:
                    CODEX_DUAL_SOL_GATE.release(dual_gate_key)
    finally:
        with CODEX_QUEUE_LOCK:
            CODEX_ACTIVE_QUEUES.discard(queue_key)
        # Fecha a corrida entre a ultima leitura vazia e a remocao da fila ativa.
        next_task_id = _codex_next_queued_task_id(queue_key)
        if next_task_id:
            _codex_start_thread(next_task_id)


def _codex_thread_resume_failure(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and re.search(
            r"(thread).*(not found|expired|invalid|does not exist|nao existe|expirad|inval)",
            text,
        )
    )


def _codex_transient_runtime_failure(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and re.search(
            r"(app[- ]server|broken pipe|connection (?:closed|refused|reset)|temporar(?:y|ily) unavailable|"
            r"timed? out|timeout|rate limit|too many requests|runtime.*(?:indispon|unavailable)|"
            r"codex.*(?:indispon|unavailable|not ready)|process.*(?:closed|exited))",
            text,
        )
    )




















def _codex_start_thread(task_id: str) -> None:
    task = _codex_load_task(task_id)
    if not task or str(task.get("status") or "") != "queued":
        return
    queue_key = _codex_task_queue_key(task)
    with CODEX_QUEUE_LOCK:
        if queue_key in CODEX_ACTIVE_QUEUES:
            return
        CODEX_ACTIVE_QUEUES.add(queue_key)
    worker = threading.Thread(target=_codex_run_conversation_queue, args=(queue_key,), daemon=True)
    worker.start()


def codex_console_recuperar_fila_background() -> dict[str, Any]:
    queued_ids: list[str] = []
    interrupted_ids: list[str] = []
    try:
        paths = sorted(Path(_codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime)
    except Exception:
        paths = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if not task_id:
            continue
        status = str(task.get("status") or "")
        if status in {"running", "cancel_requested"}:
            resumable_dual_worker = bool(
                status == "running"
                and _codex_is_whatsapp_dual_worker(task)
                and str(task.get("sandbox") or "read_only") == "read_only"
            )
            resumable_whatsapp_read = bool(
                status == "running"
                and str(task.get("origin") or "") == "whatsapp"
                and task.get("external_safe_mode") is True
                and (
                    resumable_dual_worker
                    or (not task.get("mutable_intent") and not task.get("proposal"))
                )
            )
            if resumable_whatsapp_read:
                task.update(
                    {
                        "status": "queued",
                        "started_at": "",
                        "completed_at": "",
                        "active_turn_id": "",
                        "can_steer": False,
                        "wait_reason": "restart_recovery",
                        "live_status": "Retomando a consulta apos a reinicializacao, sem reutilizar resposta incompleta.",
                        "error": "",
                        "restart_recovery_count": int(task.get("restart_recovery_count") or 0) + 1,
                    }
                )
                queued_ids.append(task_id)
            else:
                task.update(
                    {
                        "status": "failed",
                        "completed_at": _codex_now(),
                        "active_turn_id": "",
                        "can_steer": False,
                        "live_status": "Execucao interrompida pelo reinicio do aplicativo.",
                        "error": "Execucao interrompida pelo reinicio; nenhuma resposta foi adicionada ao contexto.",
                    }
                )
                interrupted_ids.append(task_id)
        elif status == "queued":
            queued_ids.append(task_id)
        else:
            continue
        with CODEX_TASKS_LOCK:
            CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    for task_id in queued_ids:
        _codex_start_thread(task_id)
    return {
        "success": True,
        "queued": len(queued_ids),
        "interrupted": len(interrupted_ids),
        "queued_task_ids": queued_ids,
        "interrupted_task_ids": interrupted_ids,
    }
__codex_dependencies__ = ['_codex_run_worker', 'CODEX_ACTIVE_QUEUES', 'CODEX_AGENT_TOOL_RESULT_LIMIT', 'CODEX_DUAL_SOL_GATE', 'CODEX_FULL_ACCESS_LOCK', 'CODEX_QUEUE_LOCK', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_CODEX_AGENT_DATA_SELECTION_TRUST_MARKER', '_codex_agent_data_selection_tool_ids', '_codex_agent_delta_prompt', '_codex_agent_initial_prompt', '_codex_agent_is_report_request', '_codex_agent_json', '_codex_agent_materialize_task_data_selection', '_codex_agent_mode_enabled', '_codex_agent_run_loop', '_codex_agent_screen_summary', '_codex_agent_tool_catalog', '_codex_ai_telemetry_instance', '_codex_app_data_context', '_codex_apply_sdk_protocol_compat', '_codex_approval_mode_enum', '_codex_base_info_dir', '_codex_build_scope', '_codex_clean_text', '_codex_context_stats', '_codex_context_stats_from_prompt', '_codex_conversation_id', '_codex_conversation_state_for_task', '_codex_dual_worker_web_search_enabled', '_codex_external_readonly_config_overrides', '_codex_final_response_from_items', '_codex_info_dir', '_codex_is_whatsapp_dual_worker', '_codex_load_task', '_codex_log', '_codex_mcp_shadow_finish', '_codex_mcp_shadow_prepare', '_codex_native_mcp_cleanup', '_codex_native_mcp_enabled', '_codex_native_mcp_thread_config', '_codex_nonfull_config_overrides', '_codex_normalizar_approval_profile', '_codex_normalizar_model', '_codex_normalizar_reasoning_effort', '_codex_normalizar_service_tier', '_codex_normalizar_speed', '_codex_now', '_codex_persist_task', '_codex_prepare_conversation_context', '_codex_process_stream_event', '_codex_prompt_com_contexto_tela', '_codex_readonly_cwd_for_session', '_codex_reasoning_effort_enum', '_codex_register_active_turn', '_codex_runtime_require_ready', '_codex_safe_id', '_codex_sandbox_enum', '_codex_save_conversation_state', '_codex_scope_instruction', '_codex_scope_violations', '_codex_sdk_env', '_codex_task_channel', '_codex_task_conversation_id', '_codex_task_stored_conversation_id', '_codex_task_whatsapp_query_only', '_codex_transition_task_plan', '_codex_unregister_active_turn', '_codex_update_conversation_memory', '_codex_update_live', '_codex_update_task', '_codex_web_readonly_config_overrides', '_codex_workspace_changed_files', '_codex_workspace_snapshot']

__codex_exports__ = ['_codex_task_conversation_gate_key', '_codex_task_queue_key', '_codex_task_queue_position', '_codex_next_queued_task_id', '_codex_run_conversation_queue', '_codex_thread_resume_failure', '_codex_transient_runtime_failure', '_codex_start_thread', 'codex_console_recuperar_fila_background']
