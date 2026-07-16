"""Extracted WhatsApp bridge component: actions."""

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


def _message_request_text(message: dict[str, Any], transcription: Optional[dict[str, Any]] = None) -> str:
    return whatsapp_message.message_request_text(message, transcription)

def _mobile_screen_context(
    message_id: str,
    subject: str,
    media: Optional[dict[str, Any]],
    transcription: Optional[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "title": "WhatsApp — Black John",
        "pathname": "/whatsapp",
        "modulo_atual": "black_jhon_mobile",
        "selection": {
            "origin": "whatsapp",
            "message_id": message_id,
            "subject_id": subject,
            "mobile_full_access": True,
            "media": media or {},
            "transcription": transcription or {},
            "query_policy": dict(query_policy or {}) if isinstance(query_policy, dict) else {},
        },
    }

def _try_create_action_pending(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    screen_context: dict[str, Any],
) -> bool:
    if not session.get("is_full") or not request_text:
        return False
    if _whatsapp_readonly_inquiry(request_text):
        return False
    followups = state.get("action_followups") if isinstance(state.get("action_followups"), dict) else {}
    followup = followups.get(conversation_id) if isinstance(followups.get(conversation_id), dict) else {}
    if followup and time.time() - float(followup.get("created_at") or 0) > 3600:
        followups.pop(conversation_id, None)
        followup = {}
    action_reference = "\n".join(
        item for item in (str(followup.get("message") or "").strip(), str(request_text or "").strip()) if item
    )
    if not followup and not codex_console._codex_prompt_pede_alteracao(request_text):
        return False
    history = [{"role": "user", "text": str(followup.get("message") or "")}] if followup else []
    raw_wa_id = str(message.get("wa_id") or "").strip()
    try:
        normalized_wa_id = _normalize_registered_phone(raw_wa_id) if raw_wa_id else ""
    except HTTPException:
        normalized_wa_id = ""
    result = codex_actions.create_proposal(
        client_id=str(session.get("client_id") or "default"),
        username=str(session.get("username") or ""),
        message=request_text,
        screen_context=screen_context,
        history=history,
        conversation_id=conversation_id,
        conversation_generation=1,
        channel="whatsapp",
        wa_id_hash=hashlib.sha256(normalized_wa_id.encode("utf-8")).hexdigest() if normalized_wa_id else "",
        idempotency_key=f"wa:{str(message.get('message_id') or '').strip()}",
    )
    if not isinstance(result, dict) or result.get("matched") is not True:
        if followup:
            followups.pop(conversation_id, None)
            state["action_followups"] = followups
            _save_state(state)
        return False
    message_id = str(message.get("message_id") or "")
    subject = str(message.get("subject_id") or "")
    if result.get("needs_input"):
        missing = [str(item) for item in (result.get("missing_params") or []) if str(item or "").strip()]
        followups[conversation_id] = {"message": request_text, "created_at": time.time(), "missing_params": missing}
        state["action_followups"] = followups
        _save_state(state)
        response = (
            "Reconheci o comando, mas preciso destas informações antes de preparar a confirmação:\n\n"
            + "\n".join(f"• *{item.replace('_', ' ').capitalize()}*" for item in missing)
            + "\n\nEnvie os dados faltantes em uma nova mensagem."
        )
        parts = _whatsapp_response_parts(response, "🧩 BLACK JOHN — DADOS NECESSÁRIOS")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        return True
    followups.pop(conversation_id, None)
    state["action_followups"] = followups
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    if not proposal:
        _save_state(state)
        return False
    if proposal.get("can_execute") is False:
        response = (
            f"A função *{str((proposal.get('action') or {}).get('label') or proposal.get('action_id') or 'solicitada')}* foi reconhecida, "
            "mas ainda está marcada como somente proposta no Black John e não possui executor seguro. "
            "Ela não será executada pelo WhatsApp."
        )
        parts = _whatsapp_response_parts(response, "🛡️ BLACK JOHN — EXECUÇÃO INDISPONÍVEL")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        _save_state(state)
        return True
    pending = {
        "kind": "action_proposal",
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "subject_id": subject,
        "username": str(session.get("username") or "").strip().lower(),
        "client_id": str(session.get("client_id") or "").strip(),
        "request_text": request_text,
        "action_summary": str(proposal.get("summary") or proposal.get("title") or ""),
        "risk": str(proposal.get("risk") or ""),
        "created_at": _now(),
        "awaiting_notified": False,
        "trusted_bound_number": True,
        "proposal_version": int(proposal.get("version") or 1),
        "proposal_hash": str(proposal.get("proposal_hash") or ""),
        "wa_id": normalized_wa_id,
    }
    # Mutacoes do agente geral sempre exigem confirmacao no aplicativo. A
    # excecao de Perguntas e Pos-venda usa o fluxo especializado anterior.
    if proposal:
        _save_state(state)
        response = (
            f"Preparei a proposta *{str(proposal.get('title') or proposal.get('action_id') or 'solicitada')}*. "
            "Por segurança, esta ação precisa ser revisada e confirmada no aplicativo JK Sistema. "
            f"Identificador: `{str(proposal.get('proposal_id') or '')}`."
        )
        parts = _whatsapp_response_parts(response, "BLACK JHON - CONFIRME NO APLICATIVO")
        _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})
        return True
    _save_pending(state, message_id, pending)
    _notify_pending_approval(config, state, message_id, pending)
    return True

def _find_pending_approval(
    state: dict[str, Any],
    *,
    subject_id: str,
    username: str,
    client_id: str,
    code: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    pending_messages = state.get("pending_messages") if isinstance(state.get("pending_messages"), dict) else {}
    for original_message_id, pending in pending_messages.items():
        if not isinstance(pending, dict) or pending.get("approval_used") is True:
            continue
        if str(pending.get("subject_id") or "") != subject_id:
            continue
        if str(pending.get("username") or "").strip().lower() != username.strip().lower():
            continue
        if str(pending.get("client_id") or "").strip() != client_id.strip():
            continue
        expected = str(pending.get("approval_code") or "").strip().upper()
        if expected and secrets.compare_digest(expected, str(code or "").strip().upper()):
            return str(original_message_id), pending
    return "", None

def _post_command_reply(config: dict[str, Any], message_id: str, text: str, title: str) -> None:
    parts = _whatsapp_response_parts(text, title)
    _post_message_result(config, message_id, {"status": "completed", "response": parts[0], "response_parts": parts})

def _handle_approval_command(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action, code = _approval_command(message.get("text_body"))
    if not action:
        return False
    message_id = str(message.get("message_id") or "")
    subject = str(message.get("subject_id") or "").strip()
    _post_command_reply(
        config,
        message_id,
        "A aprovacao desta tarefa nunca pode ocorrer pelo WhatsApp. Abra o JK Sistema para revisar e aprovar. "
        "A unica mutacao permitida aqui e a resposta ao comprador do Mercado Livre pelo botao tokenizado Aprovar e enviar.",
        "BLACK JHON - APROVACAO SOMENTE NO APLICATIVO",
    )
    return True
    if not session.get("is_full"):
        _post_command_reply(
            config,
            message_id,
            "Este vínculo não possui mais permissão `full` no JK Sistema. Nenhuma execução foi liberada.",
            "🛡️ BLACK JOHN — ACESSO NEGADO",
        )
        return True
    original_message_id, pending = _find_pending_approval(
        state,
        subject_id=subject,
        username=str(session.get("username") or ""),
        client_id=str(session.get("client_id") or ""),
        code=code,
    )
    if not pending:
        _post_command_reply(
            config,
            message_id,
            "O código não existe, já foi usado ou pertence a outro número/usuário. Nada foi executado.",
            "⚠️ BLACK JOHN — CÓDIGO INVÁLIDO",
        )
        return True
    if float(pending.get("approval_expires_at") or 0) < time.time():
        renewed_text = _approval_notice(pending, renewed=True)
        _save_pending(state, original_message_id, pending)
        _post_command_reply(config, message_id, renewed_text, "🔐 BLACK JOHN — NOVO CÓDIGO")
        return True
    try:
        kind = str(pending.get("kind") or "task")
        if action == "approve":
            if kind == "action_proposal":
                result = codex_actions.approve_proposal(
                    str(pending.get("proposal_id") or ""),
                    username=str(session.get("username") or ""),
                    client_id=str(session.get("client_id") or ""),
                    authorization=None,
                    source="whatsapp",
                    wa_id=str(pending.get("wa_id") or ""),
                    proposal_version=int(pending.get("proposal_version") or 1),
                    proposal_hash=str(pending.get("proposal_hash") or ""),
                )
                run = result.get("run") if isinstance(result, dict) and isinstance(result.get("run"), dict) else {}
                pending["run_id"] = str(run.get("run_id") or "")
            else:
                codex_console.codex_aprovar_tarefa_para_sessao(
                    str(pending.get("task_id") or ""),
                    session,
                    codex_console.CodexTaskApprovalRequest(
                        screen_context={
                            "title": "WhatsApp — confirmação móvel",
                            "pathname": "/whatsapp",
                            "modulo_atual": "black_jhon_mobile",
                            "selection": {"subject_id": subject, "approval_code_used": True},
                        }
                    ),
                    approval_source="whatsapp",
                    subject_id=subject,
                )
            pending.update({"approval_used": True, "approved_at": _now(), "approved_by": str(session.get("username") or "")})
            _save_pending(state, original_message_id, pending)
            _post_command_reply(
                config,
                message_id,
                "Confirmação aceita. O Black John iniciou somente o pedido associado a este código e enviará o resultado nesta conversa.",
                "✅ BLACK JOHN — EXECUÇÃO AUTORIZADA",
            )
        else:
            if kind == "action_proposal":
                codex_actions.reject_proposal(
                    str(pending.get("proposal_id") or ""),
                    username=str(session.get("username") or ""),
                    client_id=str(session.get("client_id") or ""),
                    source="whatsapp",
                )
            else:
                codex_console.codex_cancelar_tarefa_para_sessao(
                    str(pending.get("task_id") or ""),
                    session,
                    cancel_source="whatsapp",
                    subject_id=subject,
                )
            _remove_pending(state, original_message_id, status="canceled", reason="cancelado_pelo_usuario")
            _post_command_reply(
                config,
                message_id,
                "Pedido rejeitado. Nenhuma execução foi iniciada para este código.",
                "🚫 BLACK JOHN — PEDIDO REJEITADO",
            )
    except HTTPException as exc:
        detail = str(exc.detail or "Não foi possível aplicar esta decisão.")
        _post_command_reply(config, message_id, detail, "⚠️ BLACK JOHN — DECISÃO NÃO APLICADA")
    except Exception:
        _post_command_reply(
            config,
            message_id,
            "Não foi possível aplicar a decisão agora. O pedido continua bloqueado e nada foi executado.",
            "⚠️ BLACK JOHN — DECISÃO NÃO APLICADA",
        )
    return True


_COMPONENT_FUNCTIONS = frozenset((
    '_message_request_text',
    '_mobile_screen_context',
    '_try_create_action_pending',
    '_find_pending_approval',
    '_post_command_reply',
    '_handle_approval_command'
))
_IMPLEMENTATIONS = {
    '_message_request_text': _message_request_text,
    '_mobile_screen_context': _mobile_screen_context,
    '_try_create_action_pending': _try_create_action_pending,
    '_find_pending_approval': _find_pending_approval,
    '_post_command_reply': _post_command_reply,
    '_handle_approval_command': _handle_approval_command
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
