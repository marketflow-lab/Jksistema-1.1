"""Codex console task store component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import logging
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

logger = logging.getLogger(__name__)


def _codex_assistant_reports_dir(client_id: str) -> Path:
    return Path(_codex_base_info_dir()) / _codex_safe_id(client_id) / "codex_assistant" / "reports"


def _codex_backfill_assistant_report_tasks(client_id: str, username: str = "", limit: int = 30) -> None:
    max_reports = max(1, min(100, int(limit or 30)))
    username_norm = str(username or "").strip().lower()

    def backfill_report(report: dict[str, Any], fallback_report_id: str = "") -> None:
        if not isinstance(report, dict):
            return
        report_owner = str(
            report.get("created_by")
            or report.get("username")
            or report.get("owner")
            or ""
        ).strip().lower()
        # Relatorio legado sem dono nao pode ser apropriado pelo primeiro
        # usuario que abrir o historico.
        if not report_owner or report_owner != username_norm:
            return
        report_id = str(report.get("report_id") or fallback_report_id).strip()
        conversation_id = str(report.get("conversation_id") or report.get("thread_id") or report_id).strip()
        if _codex_is_conversation_deleted(client_id, username, conversation_id) or _codex_is_conversation_deleted(client_id, username, report_id):
            return
        if not report_id or os.path.exists(_codex_task_path(report_id)):
            return
        report["report_id"] = report_id
        codex_register_report_history(
            client_id=client_id,
            username=report_owner,
            prompt=str(report.get("prompt") or report.get("title") or f"Relatorio {BLACK_JHON_DISPLAY_NAME}"),
            report=report,
            thread_id=str(report.get("thread_id") or ""),
            conversation_id=conversation_id,
            screen_context=report.get("screen_context") if isinstance(report.get("screen_context"), dict) else {},
        )

    try:
        reports = codex_assistant_storage.codex_assistant_reports_list(_codex_base_info_dir(), client_id, max_reports)
        for report in reports:
            backfill_report(report)
    except Exception:
        pass

    reports_dir = _codex_assistant_reports_dir(client_id)
    if not reports_dir.exists() or not reports_dir.is_dir():
        return
    try:
        report_dirs = sorted(
            [item for item in reports_dir.iterdir() if item.is_dir()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:max_reports]
    except Exception:
        return

    for report_dir in report_dirs:
        metadata_path = report_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            with metadata_path.open("r", encoding="utf-8") as fh:
                report = json.load(fh)
            if not isinstance(report, dict):
                continue
            backfill_report(report, report_dir.name)
        except Exception:
            continue


def _codex_persist_task(task: dict[str, Any]) -> None:
    try:
        payload = _codex_public_task(task)
        # Metadados internos necessarios para retomar uma tarefa apos reinicio.
        payload["permissions"] = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
        payload["trusted_model_config"] = bool(task.get("trusted_model_config"))
        payload["thread_resume_retried"] = bool(task.get("thread_resume_retried"))
        # Artefatos visuais do WhatsApp sao privados: precisam sobreviver a um
        # reinicio para a ponte concluir o envio, mas nunca saem em
        # ``_codex_public_task`` nem chegam ao frontend.
        payload["whatsapp_artifacts"] = [
            dict(item)
            for item in (task.get("whatsapp_artifacts") or [])
            if isinstance(item, dict)
        ][:4]
        payload["whatsapp_chart_expected"] = bool(task.get("whatsapp_chart_expected"))
        payload["whatsapp_chart_status"] = str(task.get("whatsapp_chart_status") or "")[:80]
        payload["whatsapp_chart_error"] = str(task.get("whatsapp_chart_error") or "")[:500]
        with open(_codex_task_path(task.get("task_id")), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        try:
            logger.warning("[CODEX CONSOLE] Falha ao persistir tarefa: %s", exc)
        except Exception:
            pass


def _codex_load_task(task_id: str) -> Optional[dict[str, Any]]:
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
    if task:
        return task
    path = _codex_task_path(task_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            task = json.load(fh)
        if isinstance(task, dict):
            with CODEX_TASKS_LOCK:
                CODEX_TASKS[task_id] = task
            return task
    except Exception:
        return None
    return None


def _codex_owned_persisted_tasks(client_id: str, username: str) -> list[dict[str, Any]]:
    username_norm = str(username or "").strip().lower()
    tasks: dict[str, dict[str, Any]] = {}
    try:
        paths = list(Path(_codex_info_dir()).glob("*.json"))
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
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if task_id:
            tasks[task_id] = task
    with CODEX_TASKS_LOCK:
        for task_id, task in CODEX_TASKS.items():
            if (
                isinstance(task, dict)
                and str(task.get("client_id") or "") == str(client_id or "")
                and str(task.get("created_by") or "").strip().lower() == username_norm
            ):
                tasks[str(task_id)] = task
    return list(tasks.values())


def _codex_whatsapp_phone_display(phone: str) -> str:
    digits = _codex_normalize_phone(phone)
    if not digits:
        return "Telefone indisponivel"
    if digits.startswith("55") and len(digits) == 13:
        return f"+55 ({digits[2:4]}) {digits[4:9]}-{digits[9:]}"
    if digits.startswith("55") and len(digits) == 12:
        return f"+55 ({digits[2:4]}) {digits[4:8]}-{digits[8:]}"
    return f"+{digits}"


def _codex_timestamp_from_seconds(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _codex_registered_whatsapp_bindings(sessao: dict[str, Any]) -> list[dict[str, Any]]:
    """Consulta os vinculos ativos para exibir tambem telefones ainda sem tarefa."""
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    if not username:
        return []
    try:
        # Importacao local evita o ciclo: whatsapp_bridge usa este modulo para
        # criar e acompanhar as tarefas recebidas do gateway.
        from backend.services import whatsapp_bridge

        config = whatsapp_bridge._load_config()
        worker = whatsapp_bridge._worker_health(config)
        raw_bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    except Exception:
        return []

    bindings: list[dict[str, Any]] = []
    for item in raw_bindings:
        if not isinstance(item, dict):
            continue
        binding_client_id = str(item.get("client_id") or "")
        binding_username = str(item.get("username") or "").strip().lower()
        if binding_client_id != client_id or binding_username != username:
            continue
        phone = _codex_normalize_phone(item.get("phone_number") or item.get("wa_id"))
        if not phone:
            continue
        subject_id = str(item.get("subject_id") or "").strip()
        try:
            settings = whatsapp_bridge._phone_notification_settings(
                config,
                subject_id,
                client_id=binding_client_id,
                username=binding_username,
            )
        except Exception:
            settings = {}
        bindings.append(
            {
                "conversation_id": _codex_canonical_conversation_id(
                    client_id,
                    username,
                    channel="whatsapp",
                    phone=phone,
                ),
                "phone": phone,
                "label": str(settings.get("label") or "").strip()[:60],
                "registered_at": _codex_timestamp_from_seconds(item.get("created_at")),
                "last_inbound_at": _codex_timestamp_from_seconds(item.get("last_inbound_at")),
            }
        )
    return bindings


def _codex_whatsapp_history_records(sessao: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna somente tarefas WhatsApp pertencentes ao usuario autenticado."""
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    deleted_ids = _codex_deleted_conversation_ids(client_id, username)
    records: list[dict[str, Any]] = []
    for task in _codex_owned_persisted_tasks(client_id, username):
        if _codex_task_channel(task) != "whatsapp":
            continue
        if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
            continue
        phone = _codex_task_phone(task)
        if not phone:
            # Nunca reunir historico sem telefone em um balde compartilhado.
            continue
        conversation_id = _codex_canonical_conversation_id(
            client_id,
            username,
            channel="whatsapp",
            phone=phone,
        )
        if conversation_id in deleted_ids or (_codex_task_conversation_keys(task) & deleted_ids):
            continue
        records.append(
            {
                "task": task,
                "conversation_id": conversation_id,
                "phone": phone,
                "activity_at": str(
                    task.get("completed_at")
                    or task.get("started_at")
                    or task.get("created_at")
                    or ""
                ),
            }
        )
    records.sort(key=lambda item: str(item.get("activity_at") or ""), reverse=True)
    return records


def _codex_whatsapp_completed_exchange(task: dict[str, Any]) -> bool:
    return bool(
        str(task.get("status") or "") == "completed"
        and str(task.get("prompt") or "").strip()
        and str(task.get("final_response") or "").strip()
        and str(task.get("message_kind") or "") != "report"
        and task.get("memory_excluded") is not True
    )


def _codex_whatsapp_history_message_preview(task: dict[str, Any]) -> dict[str, Any]:
    prompt = str(task.get("prompt") or "").strip()
    response = str(task.get("final_response") or "").strip()
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    interaction_type = "call" if str(metadata.get("interaction_type") or metadata.get("source") or "").lower() in {"call", "whatsapp_call"} else "message"
    prompt_limit = 4000
    response_limit = 12000
    return {
        "task_id": str(task.get("task_id") or ""),
        "status": str(task.get("status") or ""),
        "model": str(task.get("model") or ""),
        "created_at": str(task.get("created_at") or ""),
        "completed_at": str(task.get("completed_at") or ""),
        "prompt": prompt[:prompt_limit],
        "prompt_truncated": len(prompt) > prompt_limit,
        "response": response[:response_limit],
        "response_truncated": len(response) > response_limit,
        "interaction_type": interaction_type,
        "call_id": str(metadata.get("call_id") or "")[:200],
        "duration_seconds": max(0, int(metadata.get("duration_seconds") or 0)),
    }


def _codex_update_task(task_id: str, **updates: Any) -> dict[str, Any]:
    if "error" in updates:
        updates["error"] = _codex_sanitize_log_text(updates.get("error"), 500)
    terminal_transition = False
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
        if not task:
            raise KeyError(task_id)
        previous_status = str(task.get("status") or "")
        task.update(updates)
        _codex_persist_task(task)
        terminal_transition = (
            str(task.get("status") or "") in {"completed", "partial", "failed", "canceled"}
            and previous_status not in {"completed", "partial", "failed", "canceled"}
        )
        result = task
    if terminal_transition:
        _codex_telemetry_finish_task(result)
    return result





def _codex_log(task: dict[str, Any], text: str, kind: str = "status") -> None:
    del text
    logs = task.setdefault("logs", [])
    kind_safe = str(kind or "status")[:40]
    logs.append(
        {
            "at": _codex_now(),
            "text": "Aviso tecnico registrado." if kind_safe in {"warning", "error"} else "Evento tecnico registrado.",
            "kind": kind_safe,
        }
    )
    task["logs"] = logs[-240:]
    _codex_persist_task(task)





def _codex_normalizar_sandbox(value: str) -> str:
    sandbox = str(value or "read_only").strip().lower()
    if sandbox not in CODEX_SANDBOXES:
        raise HTTPException(
            status_code=400,
            detail=_codex_public_error("CODEX_SANDBOX_INVALID", "Sandbox invalido para o assistente interno."),
        )
    if sandbox not in CODEX_INTERNAL_ALLOWED_SANDBOXES:
        raise HTTPException(status_code=409, detail=_codex_development_requires_desktop_detail())
    return sandbox



def _codex_public_error(
    error_code: str,
    message: str,
    *,
    retryable: bool = False,
    trace_id: str = "",
) -> dict[str, Any]:
    return {
        "error_code": str(error_code or "CODEX_ERROR")[:100],
        "trace_id": str(trace_id or uuid.uuid4().hex)[:100],
        "retryable": bool(retryable),
        "message": _codex_clean_text(message, 500),
    }



def _codex_sanitize_log_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    text = re.sub(r"(?i)\b(bearer|api[_ -]?key|authorization|token|secret|password)\b\s*[:=]?\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\r\n\t]+", "[PATH_REDACTED]", text)
    text = re.sub(r"(?<!:)\/(?:[^\s/]+\/){2,}[^\s]+", "[PATH_REDACTED]", text)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL_REDACTED]", text)
    text = re.sub(r"(?<!\d)(?:\d[ .()\/-]?){10,14}(?!\d)", "[IDENTIFIER_REDACTED]", text)
    text = re.sub(
        r"(?i)\b(?:rua|avenida|av\.?|travessa|alameda|rodovia|pra[cç]a)\s+[^,;\r\n]{2,100}(?:,\s*\d{1,6})?",
        "[ADDRESS_REDACTED]",
        text,
    )
    text = re.sub(r"(?is)<jk_tool_calls>.*?</jk_tool_calls>", "[TOOL_PAYLOAD_REDACTED]", text)
    text = re.sub(r"(?is)\b(?:arguments?|result|payload)\s*[:=]\s*[\[{].*", "[TOOL_PAYLOAD_REDACTED]", text)
    text = re.sub(r"(?is)traceback \(most recent call last\):.*", "[STACK_TRACE_REDACTED]", text)
    return text[: max(1, int(limit or 1))]



def _codex_task_duration_ms(task: dict[str, Any]) -> int:
    try:
        start = datetime.fromisoformat(str(task.get("started_at") or task.get("created_at") or "").replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(task.get("completed_at") or _codex_now()).replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1000))
    except (TypeError, ValueError):
        return 0



def _codex_telemetry_finish_task(task: dict[str, Any]) -> None:
    try:
        telemetry = _codex_ai_telemetry_instance()
        client_id = str(task.get("client_id") or "default")
        trace_id = str(task.get("trace_id") or task.get("task_id") or "")
        status = str(task.get("status") or "unknown")
        duration_ms = _codex_task_duration_ms(task)
        telemetry.finish_span(
            client_id,
            trace_id=trace_id,
            span_id=f"{trace_id}:finalization",
            stage="finalization",
            status="completed" if status in {"completed", "partial"} else status,
            duration_ms=0,
            error_code=str(task.get("error_code") or ""),
        )
        telemetry.record_event(
            client_id,
            event_id=f"{task.get('task_id')}:terminal",
            trace_id=trace_id,
            event_type="inference",
            status=status,
            requested_model=task.get("requested_model") or task.get("model"),
            effective_model=task.get("effective_model") or task.get("model"),
            provider=task.get("provider") or "openai_codex",
            provider_path=task.get("provider_path") or "codex_internal",
            model_rerouted=bool(task.get("model_rerouted")),
            duration_ms=duration_ms,
            input_tokens=(task.get("token_usage") or {}).get("input_tokens", 0),
            output_tokens=(task.get("token_usage") or {}).get("output_tokens", 0),
            cached_tokens=(task.get("token_usage") or {}).get("cached_input_tokens", 0),
            tool_codes=[
                item.get("tool_id") or item.get("name")
                for item in list(task.get("tool_calls") or [])
                if isinstance(item, dict)
            ],
            context_generation=task.get("conversation_generation"),
            error_code=task.get("error_code") or ("TASK_FAILED" if status == "failed" else ""),
            user_id=task.get("created_by"),
            store_id=(task.get("query_policy") or {}).get("store", ""),
            dimensions={
                "surface": task.get("origin") or "app",
                "category": task.get("model_category") or "general",
                "prompt_version": task.get("thread_prompt_version") or CODEX_SIDEBAR_TASK_PROMPT_VERSION,
                "tool_schema_version": task.get("thread_schema_version") or CODEX_SIDEBAR_TASK_SCHEMA_VERSION,
                "model_policy_version": task.get("model_policy_version") or "disabled",
                "model_reason_code": task.get("model_reason_code") or "baseline_default",
            },
        )
        telemetry.finish_trace(
            client_id,
            trace_id=trace_id,
            status=status,
            effective_model=task.get("effective_model") or task.get("model"),
            provider=task.get("provider") or "openai_codex",
            duration_ms=duration_ms,
            error_code=task.get("error_code") or ("TASK_FAILED" if status == "failed" else ""),
        )
    except Exception:
        return
__codex_dependencies__ = ['BLACK_JHON_DISPLAY_NAME', 'CODEX_INTERNAL_ALLOWED_SANDBOXES', 'CODEX_SANDBOXES', 'CODEX_SIDEBAR_TASK_PROMPT_VERSION', 'CODEX_SIDEBAR_TASK_SCHEMA_VERSION', 'CODEX_TASKS', 'CODEX_TASKS_LOCK', '_codex_ai_telemetry_instance', '_codex_base_info_dir', '_codex_canonical_conversation_id', '_codex_clean_text', '_codex_deleted_conversation_ids', '_codex_development_requires_desktop_detail', '_codex_info_dir', '_codex_is_conversation_deleted', '_codex_normalize_phone', '_codex_now', '_codex_public_task', '_codex_safe_id', '_codex_task_channel', '_codex_task_conversation_keys', '_codex_task_path', '_codex_task_phone', 'codex_register_report_history']

__codex_exports__ = ['_codex_assistant_reports_dir', '_codex_backfill_assistant_report_tasks', '_codex_persist_task', '_codex_load_task', '_codex_owned_persisted_tasks', '_codex_whatsapp_phone_display', '_codex_timestamp_from_seconds', '_codex_registered_whatsapp_bindings', '_codex_whatsapp_history_records', '_codex_whatsapp_completed_exchange', '_codex_whatsapp_history_message_preview', '_codex_update_task', '_codex_log', '_codex_normalizar_sandbox', '_codex_public_error', '_codex_sanitize_log_text', '_codex_task_duration_ms', '_codex_telemetry_finish_task']
