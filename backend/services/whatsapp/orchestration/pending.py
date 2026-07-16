"""Extracted WhatsApp bridge component: pending."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp.contracts import (
    _QuestionResearchPending,
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _job_deadline_seconds(request_text: Any, *, requires_web: bool = False) -> int:
    text = _whatsapp_text_key(request_text)
    if re.search(r"\b(relatorio|planilha|excel|xlsx|pdf|grafico|imagem do relatorio|dashboard)\b", text):
        return WHATSAPP_REPORT_DEADLINE_SECONDS
    if re.search(r"\b(pergunta|perguntas|pos venda|comprador|mercado livre|mercadolivre)\b", text) and (
        requires_web or re.search(r"\b(respost|compatib|aplica|serve|encaixa)\b", text)
    ):
        return WHATSAPP_ML_RESEARCH_DEADLINE_SECONDS
    return WHATSAPP_JOB_DEADLINE_DEFAULT

def _ensure_job_contract(pending: dict[str, Any]) -> dict[str, Any]:
    now_epoch = time.time()
    created_epoch = float(pending.get("created_at_epoch") or now_epoch)
    pending.setdefault("created_at_epoch", created_epoch)
    deadline_seconds = int(
        pending.get("deadline_seconds")
        or _job_deadline_seconds(
            pending.get("request_text") or pending.get("job_prompt") or "",
            requires_web=bool(pending.get("requires_web")),
        )
    )
    deadline_seconds = max(30, min(deadline_seconds, WHATSAPP_REPORT_DEADLINE_SECONDS))
    pending.setdefault("deadline_seconds", deadline_seconds)
    pending.setdefault("deadline_at_epoch", created_epoch + deadline_seconds)
    pending["retry_policy"] = "bounded"
    pending["max_retry_attempts"] = WHATSAPP_MAX_RETRY_ATTEMPTS
    pending.setdefault("wait_notice_count", 0)
    pending.setdefault("last_wait_notice_at_epoch", 0)
    return pending

def _save_pending(state: dict[str, Any], message_id: str, value: dict[str, Any]) -> None:
    value.setdefault("message_id", str(message_id))
    _ensure_job_contract(value)
    with BRIDGE_STATE_LOCK:
        pending = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        pending[message_id] = value
        state["pending_messages"] = pending
        _save_state(state)

def _archive_pending(
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    status: str,
    reason: str = "",
) -> None:
    terminal = status if status in {"partial", "completed", "failed", "canceled"} else "completed"
    archived = dict(pending or {})
    archived.update(
        {
            "job_state": terminal,
            "terminal_reason": str(reason or archived.get("terminal_reason") or "")[:1000],
            "terminal_at": _now(),
            "terminal_at_epoch": time.time(),
        }
    )
    history = state.get("assistant_job_history") if isinstance(state.get("assistant_job_history"), dict) else {}
    history[str(message_id)] = archived
    if len(history) > 500:
        ordered = sorted(
            history.items(),
            key=lambda pair: float((pair[1] or {}).get("terminal_at_epoch") or 0),
        )[-500:]
        history = dict(ordered)
    state["assistant_job_history"] = history
    try:
        _bridge_store().audit(
            "assistant_job_terminal",
            message_id=str(message_id),
            subject_id=str(pending.get("subject_id") or ""),
            details={"status": terminal, "reason": str(reason or "")[:500]},
        )
    except Exception:
        pass

def _remove_pending(
    state: dict[str, Any],
    message_id: str,
    *,
    status: str = "completed",
    reason: str = "",
) -> None:
    with BRIDGE_STATE_LOCK:
        pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
        removed = pending_messages.pop(message_id, None)
        if isinstance(removed, dict):
            _archive_pending(state, message_id, removed, status=status, reason=reason)
        state["pending_messages"] = pending_messages
        _save_state(state)

def _pending_partial_text(pending: dict[str, Any], reason: Any = "") -> str:
    facts = [str(item or "").strip() for item in list(pending.get("verified_facts") or []) if str(item or "").strip()]
    sources = [str(item or "").strip() for item in list(pending.get("verified_sources") or []) if str(item or "").strip()]
    lines: list[str] = []
    if facts:
        lines.append("Consegui confirmar até aqui:")
        lines.extend(f"- {item[:700]}" for item in facts[:5])
    else:
        lines.append("Não consegui obter uma confirmação suficiente para concluir esta solicitação.")
    clean_reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if clean_reason:
        lines.append(f"Limitação encontrada: {clean_reason[:500]}")
    if sources:
        lines.append("Fontes consultadas: " + "; ".join(item[:250] for item in sources[:5]) + ".")
    lines.append("Posso tentar novamente ou continuar se você ajustar o pedido.")
    return "\n\n".join(lines)[:3500]

def _worker_result_fallback_text(result: dict[str, Any], pending: Optional[dict[str, Any]] = None) -> str:
    value = result if isinstance(result, dict) else {}
    summary = re.sub(r"\s+", " ", str(value.get("summary") or "")).strip()
    facts = [str(item or "").strip() for item in list(value.get("verified_facts") or []) if str(item or "").strip()]
    sources = [str(item or "").strip() for item in list(value.get("sources") or []) if str(item or "").strip()]
    missing = [str(item or "").strip() for item in list(value.get("missing") or []) if str(item or "").strip()]
    lines: list[str] = []
    if summary:
        lines.append(summary[:1200])
    if facts:
        lines.extend(f"- {item[:650]}" for item in facts[:5])
    if sources:
        lines.append("Fontes: " + "; ".join(item[:220] for item in sources[:5]) + ".")
    if value.get("evidence_sufficient") is not True or str(value.get("status") or "") != "completed":
        limitation = missing[0] if missing else "a cobertura disponível não foi suficiente para confirmar tudo"
        lines.append(f"Limitação: {limitation[:500]}.")
    if not lines:
        return _pending_partial_text(pending or {}, "A resposta estruturada da IA veio vazia.")
    return "\n\n".join(lines)[:3500]

def _terminate_pending_partial(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    reason: str,
) -> bool:
    if pending.get("terminal_delivery_started"):
        return False
    pending.update(
        {
            "job_state": "partial",
            "terminal_reason": str(reason or "limite_operacional")[:1000],
            "terminal_delivery_started": True,
            "delivery_state": "partial_finalizing",
        }
    )
    _save_pending(state, message_id, pending)
    text = _pending_partial_text(pending, reason)
    result = _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"job:{pending.get('job_group_id') or message_id}:partial-terminal",
            "event_type": "task_partial",
            "severity": "medium",
            "text": text,
        },
    )
    delivery = str(result.get("status") or "")
    if delivery not in {"sent", "queued", "duplicate", "waiting_free_window"}:
        pending["terminal_delivery_started"] = False
        pending["delivery_state"] = f"partial_delivery_{delivery or 'failed'}"
        _save_pending(state, message_id, pending)
        return False
    _update_pending_codex_tasks(
        pending,
        handoff_status="partial_terminal",
        delivery_state=delivery,
        user_facing_response=text[:12000],
    )
    _record_message_timing(message_id, completed_at=_now(), sent_at=_now(), terminal_status="partial")
    _remove_pending(state, message_id, status="partial", reason=reason)
    return True

def _ensure_pending_approval(pending: dict[str, Any], *, renew: bool = False) -> tuple[str, float]:
    now = time.time()
    code = str(pending.get("approval_code") or "").strip().upper()
    expires_at = float(pending.get("approval_expires_at") or 0)
    if renew or not code or expires_at <= now:
        code = _approval_code()
        expires_at = now + APPROVAL_CODE_TTL_SECONDS
        pending.update(
            {
                "approval_code": code,
                "approval_expires_at": expires_at,
                "approval_used": False,
                "approval_issued_at": now,
            }
        )
    return code, expires_at

def _approval_notice(pending: dict[str, Any], *, renewed: bool = False) -> str:
    code, _ = _ensure_pending_approval(pending, renew=renewed)
    request_text = _whatsapp_clean_markdown(pending.get("request_text") or "Solicitação enviada pelo WhatsApp.")
    request_text = request_text[:700].rstrip()
    action_summary = _whatsapp_clean_markdown(pending.get("action_summary") or "")
    risk = _whatsapp_clean_markdown(pending.get("risk") or "")
    intro = "O código anterior expirou. Gere sua decisão com o novo código abaixo." if renewed else "Revise o pedido antes de autorizar a execução."
    details = [intro, f"*Pedido:* {request_text}"]
    if action_summary:
        details.append(f"*Execução reconhecida:* {action_summary[:700]}")
    if risk:
        details.append(f"*Nível de risco:* {risk[:120]}")
    details.extend(
        [
            "",
            "*Para executar:*",
            f"`APROVAR {code}`",
            "",
            "*Para rejeitar:*",
            f"`NEGAR {code}`",
            "",
            "_Código de uso único, válido por 10 minutos e aceito somente neste mesmo número._",
        ]
    )
    return "\n".join(details)

def _notify_pending_approval(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    *,
    renewed: bool = False,
) -> None:
    response = _approval_notice(pending, renewed=renewed)
    parts = _whatsapp_response_parts(response, _whatsapp_result_title("", "awaiting_approval"))
    _post_message_result(
        config,
        message_id,
        {
            "status": "awaiting_approval",
            "task_id": str(pending.get("task_id") or pending.get("proposal_id") or ""),
            "response": parts[0],
            "response_parts": parts,
        },
    )
    pending["awaiting_notified"] = True
    _save_pending(state, message_id, pending)

def _action_result_text(run: dict[str, Any]) -> str:
    action = run.get("action") if isinstance(run.get("action"), dict) else {}
    lines = [
        f"*Ação:* {str(action.get('label') or action.get('id') or 'comando do sistema')}",
        f"*Status:* {str(run.get('live_status') or run.get('status') or 'concluído')}",
    ]
    error = str(run.get("error") or "").strip()
    if error:
        lines.append(f"*Motivo:* {error[:1200]}")

    labels = {
        "status": "Status",
        "message": "Mensagem",
        "mensagem": "Mensagem",
        "resposta": "Resposta",
        "total": "Total",
        "processed": "Processados",
        "processados": "Processados",
        "created": "Criados",
        "updated": "Atualizados",
        "loja": "Loja",
        "data_inicio": "Início",
        "data_fim": "Fim",
        "external_mutation": "Alteração externa",
    }
    blocked_fragments = ("token", "secret", "authorization", "credential", "senha", "password")
    collected: list[tuple[str, str]] = []

    def collect(value: Any, prefix: str = "", depth: int = 0) -> None:
        if len(collected) >= 24 or depth > 2:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key or "")
                if any(fragment in key_text.lower() for fragment in blocked_fragments) or key_text.lower() in {"logs", "traceback"}:
                    continue
                collect(item, key_text if not prefix else f"{prefix}.{key_text}", depth + 1)
            return
        if isinstance(value, list):
            if value and all(not isinstance(item, (dict, list)) for item in value[:10]):
                collected.append((prefix, ", ".join(str(item) for item in value[:10])[:600]))
            elif value:
                collected.append((prefix, f"{len(value)} item(ns)"))
            return
        if value not in (None, ""):
            collected.append((prefix, str(value)[:700]))

    collect(run.get("result"))
    if collected:
        lines.append("")
        lines.append("*Detalhes:* ")
        for key, value in collected:
            leaf = key.rsplit(".", 1)[-1]
            label = labels.get(leaf, leaf.replace("_", " ").strip().capitalize() or "Resultado")
            lines.append(f"• *{label}:* {value}")
    return "\n".join(lines)

def _complete_action_pending(config: dict[str, Any], state: dict[str, Any], message_id: str, pending: dict[str, Any]) -> bool:
    run_id = str(pending.get("run_id") or "").strip()
    if not run_id:
        if not pending.get("awaiting_notified"):
            _notify_pending_approval(config, state, message_id, pending)
        return False
    try:
        run = (codex_actions.get_run(run_id, client_id=str(pending.get("client_id") or "")) or {}).get("run") or {}
    except HTTPException:
        return False
    status = str(run.get("status") or "")
    if status not in {"completed", "partial", "failed", "canceled"}:
        return False
    response = _action_result_text(run)
    title_status = "completed" if status in {"completed", "partial"} else "failed"
    parts = _whatsapp_response_parts(response, _whatsapp_result_title(pending.get("request_text"), title_status))
    _post_proactive(
        config,
        {
            "subject_id": str(pending.get("subject_id") or ""),
            "fingerprint": f"action:{run_id}:{status}",
            "event_type": "task_completed" if status in {"completed", "partial"} else "task_failed",
            "severity": "medium" if status == "partial" else ("info" if status == "completed" else "high"),
            "text": parts[0],
            "text_parts": parts,
        },
    )
    _remove_pending(
        state,
        message_id,
        status="completed" if status == "completed" else ("partial" if status == "partial" else status),
        reason="" if status == "completed" else f"acao_{status}",
    )
    return True


_COMPONENT_FUNCTIONS = frozenset((
    '_job_deadline_seconds',
    '_ensure_job_contract',
    '_save_pending',
    '_archive_pending',
    '_remove_pending',
    '_pending_partial_text',
    '_worker_result_fallback_text',
    '_terminate_pending_partial',
    '_ensure_pending_approval',
    '_approval_notice',
    '_notify_pending_approval',
    '_action_result_text',
    '_complete_action_pending'
))
_IMPLEMENTATIONS = {
    '_job_deadline_seconds': _job_deadline_seconds,
    '_ensure_job_contract': _ensure_job_contract,
    '_save_pending': _save_pending,
    '_archive_pending': _archive_pending,
    '_remove_pending': _remove_pending,
    '_pending_partial_text': _pending_partial_text,
    '_worker_result_fallback_text': _worker_result_fallback_text,
    '_terminate_pending_partial': _terminate_pending_partial,
    '_ensure_pending_approval': _ensure_pending_approval,
    '_approval_notice': _approval_notice,
    '_notify_pending_approval': _notify_pending_approval,
    '_action_result_text': _action_result_text,
    '_complete_action_pending': _complete_action_pending
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
