"""Extracted WhatsApp bridge component: question_workflow."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
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
from backend.services import perguntas_pos_venda_codex
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
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
from backend.services.whatsapp.approvals import question_natural_language as _question_natural_language
from backend.services.whatsapp.approvals.question_tokens import (
    _question_card_context,
    _question_token_approval_matches,
    _question_token_scope_matches,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS

def _question_research_delivery_state(approval: dict[str, Any], job_id: str) -> str:
    delivery_state = str(approval.get("research_delivery_state") or "")
    if not delivery_state and approval.get("data_sufficient") is False and job_id:
        approval.update(
            {
                "research_job_id": job_id,
                "research_delivery_state": "waiting_evidence",
                "research_status": "legacy_incomplete",
            }
        )
        return "waiting_evidence"
    return delivery_state


def _deliver_completed_question_research(
    config: dict[str, Any],
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    username: str,
) -> bool:
    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    delivery_state = _question_research_delivery_state(approval, job_id)
    if not job_id or delivery_state not in {"waiting_evidence", "ready"}:
        return False
    if str(approval.get("research_delivered_job_id") or "") == job_id:
        return False
    try:
        from backend.services import perguntas_pos_venda_codex as ppv_codex
        from backend.services import perguntas_pos_venda_state as ppv_state

        job = ppv_codex.get_job(client_id, job_id)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_research_status: {str(exc)[:800]}"
        return False
    if not isinstance(job, dict):
        return False
    job_status = str(job.get("status") or "").strip().lower()
    if job_status == "completed" and job.get("data_sufficient") is False:
        try:
            job = ppv_codex.resume_incomplete_job(
                client_id,
                job_id,
                reason="retomada_de_resposta_ativa_sem_evidencia_suficiente",
            ) or job
            job_status = str(job.get("status") or "").strip().lower()
            approval["research_delivery_state"] = "waiting_evidence"
            approval["research_resumed_at"] = _now()
            ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        except Exception as exc:
            RUNTIME_STATE["last_error"] = f"question_research_resume: {str(exc)[:800]}"
            return False
    approval["research_status"] = job_status
    approval["research_retry_count"] = max(0, int(job.get("retry_count") or 0))
    approval["research_next_retry_at_epoch"] = float(job.get("next_retry_at_epoch") or 0.0)
    if job_status != "completed" or job.get("data_sufficient") is not True:
        if job_status == "cancelled":
            approval["research_delivery_state"] = "cancelled"
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    response = str(result.get("resposta") or "").strip()[:1200]
    if not response:
        return False

    approval.update(
        {
            "resposta_sugerida": response,
            "regenerated_at": _now(),
            "regenerated_via": "whatsapp_research_loop",
            "research_status": "completed",
            "research_delivery_state": "ready",
            "data_sufficient": True,
            "proposal_id": job_id,
            "codex_job_id": job_id,
            "proposal_version": int(result.get("proposal_version") or job.get("proposal_version") or 1),
            "proposal_hash": str(result.get("proposal_hash") or job.get("proposal_hash") or ""),
            "warnings": list(result.get("warnings") or job.get("warnings") or [])[:8],
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)

    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    approval_id = str(approval.get("id") or "").strip()
    for item in tokens.values():
        if isinstance(item, dict) and str(item.get("approval_id") or "") == approval_id:
            item.update({"used": True, "decision": "superseded_by_verified_research"})
    token, token_item = _question_approval_token(
        state,
        approval=approval,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        force_new=True,
        user_guidance=str(approval.get("whatsapp_user_guidance") or ""),
    )
    delivery = _post_interactive_approval(
        config,
        subject_id=subject_id,
        fingerprint=f"ppv-research:{client_id}:{subject_id}:{job_id}:{result.get('proposal_hash') or ''}",
        token=token,
        body=_question_approval_body(approval, response),
        state=state,
    )
    if str(delivery.get("status") or "") not in {"sent", "duplicate"}:
        return False
    approval.update(
        {
            "research_delivery_state": "delivered",
            "research_delivered_job_id": job_id,
            "research_delivered_at": _now(),
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    _question_set_active_thread(
        state,
        approval_id=approval_id,
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    _save_state(state)
    return True

def _eligible_question_bindings(config: dict[str, Any]) -> list[tuple[dict[str, Any], str, str, str]]:
    worker = _worker_health(config)
    bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    eligible: list[tuple[dict[str, Any], str, str, str]] = []
    for binding in bindings:
        if not isinstance(binding, dict) or str(binding.get("machine_id") or "") != str(config.get("machine_id") or ""):
            continue
        client_id = str(binding.get("client_id") or "").strip()
        username = str(binding.get("username") or "").strip().lower()
        subject_id = str(binding.get("subject_id") or "").strip()
        if not client_id or not username or not subject_id:
            continue
        permissions = admin_usuarios_common._carregar_permissoes_usuario(username, client_id)
        settings = _phone_notification_settings(config, subject_id, client_id=client_id, username=username)
        if _question_approval_allowed(permissions) and settings["send_ml_question_suggestions"]:
            eligible.append((binding, client_id, username, subject_id))
    return eligible

def _question_approval_marker(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _is_post_sale_question_approval(item: dict[str, Any]) -> bool:
    tipo = _question_approval_marker(item.get("tipo") or item.get("approval_type"))
    origens = (
        item.get("origem"),
        item.get("ia_origem"),
        item.get("ia_finalidade"),
    )
    return tipo == "pos_venda" or any(
        "pos_venda" in _question_approval_marker(origem)
        for origem in origens
    )

def _pending_question_approval(
    ppv_state: Any,
    configs: Any,
    approvals: list[dict[str, Any]],
    *,
    client_id: str = "",
) -> Optional[dict[str, Any]]:
    return next(
        (
            item for item in approvals
            if isinstance(item, dict)
            and not _is_post_sale_question_approval(item)
            and perguntas_pos_venda_codex.approval_job_current(client_id, str(item.get("proposal_id") or item.get("codex_job_id") or item.get("research_job_id") or ""))
            and str(item.get("status") or "pending") == "pending"
            and str(item.get("id") or "").strip()
            and str(item.get("resposta_sugerida") or "").strip()
            and ppv_state._perguntas_loja_config_normalizar(
                ppv_state._perguntas_loja_config_obter(configs, str(item.get("loja") or "").strip())
            ).get("notificar_whatsapp_aprovacoes") is True
        ),
        None,
    )


def _question_has_active_research(approval: dict[str, Any]) -> bool:
    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    if not job_id or str(approval.get("research_delivered_job_id") or "").strip() == job_id:
        return False
    return _question_research_delivery_state(approval, job_id) in {"waiting_evidence", "ready"}


def _question_active_token_invalid_reason(
    state: dict[str, Any],
    approval: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    client_id: str,
    username: str,
) -> str:
    if _is_post_sale_question_approval(approval):
        return "ineligible_post_sale"
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    token_item = tokens.get(str(token or "").strip().upper())
    if not isinstance(token_item, dict):
        return "missing_token"
    approval_id = str(approval.get("id") or "").strip()
    if (
        not _question_token_approval_matches(token_item, approval)
        or str(token_item.get("subject_id") or "").strip() != str(subject_id or "").strip()
        or str(token_item.get("client_id") or "").strip() != str(client_id or "").strip()
        or str(token_item.get("username") or "").strip().lower() != str(username or "").strip().lower()
    ):
        return "invalid_token_scope"
    try:
        token_age = time.time() - float(token_item.get("created_at") or 0)
    except (TypeError, ValueError):
        token_age = QUESTION_APPROVAL_TOKEN_TTL_SECONDS + 1
    if token_age > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
        return "expired_token"
    decision = _question_approval_marker(token_item.get("decision"))
    if token_item.get("used") is True or "superseded" in decision:
        if _question_has_active_research(approval):
            return ""
        return "consumed_token"
    return ""


def _question_invalidate_active_thread(
    state: dict[str, Any],
    *,
    notifications: dict[str, Any],
    approval_id: str,
    token: str,
    reason: str,
    subject_id: str,
    client_id: str,
    username: str,
) -> None:
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    expected_scope = (
        str(approval_id or "").strip(),
        str(subject_id or "").strip(),
        str(client_id or "").strip(),
        str(username or "").strip().lower(),
    )
    for candidate_token, token_item in tokens.items():
        if not isinstance(token_item, dict):
            continue
        item_scope = (
            str(token_item.get("approval_id") or "").strip(),
            str(token_item.get("subject_id") or "").strip(),
            str(token_item.get("client_id") or "").strip(),
            str(token_item.get("username") or "").strip().lower(),
        )
        if item_scope != expected_scope:
            continue
        if reason != "ineligible_post_sale" and str(candidate_token).strip().upper() != str(token or "").strip().upper():
            continue
        if token_item.get("used") is not True:
            token_item.update({"used": True, "decision": reason, "decided_at": _now()})
    if reason != "ineligible_post_sale":
        for notification_key, item in list(notifications.items()):
            if (
                isinstance(item, dict)
                and str(item.get("approval_id") or "").strip() == expected_scope[0]
                and str(item.get("subject_id") or "").strip() == expected_scope[1]
            ):
                notifications.pop(notification_key, None)
    _question_clear_active_thread(
        state,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
    )

def _record_blocked_question_notification(
    config: dict[str, Any],
    notifications: dict[str, Any],
    notification_key: str,
    approval_id: str,
    subject_id: str,
    store: str,
    blocked_status: str,
) -> None:
    store_label = store or "Loja nao informada"
    template_name = "jk_black_jhon_nova_pergunta"
    template_params = [store_label]
    try:
        worker = _worker_health(config)
        templates = worker.get("templates") if isinstance(worker.get("templates"), list) else []
        approved = {
            str(item.get("name") or "").strip()
            for item in templates
            if isinstance(item, dict)
            and str(item.get("category") or "").strip().upper() == "UTILITY"
            and str(item.get("status") or "").strip().upper() == "APPROVED"
        }
        if template_name not in approved and "jk_joao_aprovacao_pendente" in approved:
            template_name = "jk_joao_aprovacao_pendente"
            template_params = [f"nova pergunta de comprador na loja {store_label}"]
    except Exception:
        pass
    notification = _post_proactive(
        config,
        {
            "subject_id": subject_id,
            "fingerprint": f"ppv-template:{notification_key}",
            "event_type": "task_awaiting_approval",
            "severity": "medium",
            "text": f"Nova pergunta de comprador aguardando revisao na loja {store_label}.",
            "template_name": template_name,
            "template_params": template_params,
        },
    )
    notification_status = str(notification.get("status") or notification.get("error") or blocked_status)
    notifications[notification_key] = {
        "approval_id": approval_id,
        "subject_id": subject_id,
        "status": notification_status,
        "interactive_status": blocked_status,
        "template_name": template_name,
        "blocked": notification_status in {"template_not_approved", "waiting_free_window", "policy_recheck_required"},
        "updated_at": _now(),
    }
    try:
        _bridge_store().record_notification(
            f"ppv:{notification_key}",
            event_type="mercado_livre_question",
            status=notification_status,
            reason=blocked_status,
            details={"approval_id": approval_id, "store": store, "template_name": template_name},
        )
    except Exception:
        pass

def _forward_question_approval_for_binding(
    config: dict[str, Any],
    state: dict[str, Any],
    ppv_state: Any,
    notifications: dict[str, Any],
    client_id: str,
    username: str,
    subject_id: str,
) -> None:
    configs = ppv_state._perguntas_loja_configs_carregar(client_id)
    approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
    active, active_token, _item = _question_active_approval(
        state, approvals, subject_id=subject_id, client_id=client_id, username=username,
        require_current_contract=True,
    )
    active_status = str(active.get("status") or "pending").strip().lower() if active is not None else ""
    if active is not None and active_status == "sending":
        return
    if active is not None and active_status == "pending":
        invalid_reason = _question_active_token_invalid_reason(
            state,
            active,
            active_token,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
        if not invalid_reason:
            _deliver_completed_question_research(
                config, state, approvals, active,
                client_id=client_id, subject_id=subject_id, username=username,
            )
            return
        active_approval_id = str(active.get("id") or "").strip()
        active_research = _question_has_active_research(active)
        _question_invalidate_active_thread(
            state,
            notifications=notifications,
            approval_id=active_approval_id,
            token=active_token,
            reason=invalid_reason,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
        if active_research and invalid_reason != "ineligible_post_sale":
            rebound_token, _rebound_item = _question_approval_token(
                state,
                approval=active,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
                force_new=True,
            )
            _question_set_active_thread(
                state,
                approval_id=active_approval_id,
                token=rebound_token,
                subject_id=subject_id,
                client_id=client_id,
                username=username,
            )
            _deliver_completed_question_research(
                config,
                state,
                approvals,
                active,
                client_id=client_id,
                subject_id=subject_id,
                username=username,
            )
            return
    if active is not None or active_token:
        _question_clear_active_thread(state, subject_id=subject_id, client_id=client_id, username=username)
    else:
        threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
        thread_key = _question_thread_key(client_id, subject_id, username)
        if isinstance(threads.get(thread_key), dict):
            _question_clear_active_thread(state, subject_id=subject_id, client_id=client_id, username=username)
    approval = _pending_question_approval(ppv_state, configs, approvals, client_id=client_id)
    if approval is None:
        return
    store = str(approval.get("loja") or "").strip()
    approval_id = str(approval.get("id") or "").strip()
    draft = str(approval.get("resposta_sugerida") or "").strip()
    draft_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()[:16]
    notification_key = hashlib.sha256(f"{client_id}|{subject_id}|{approval_id}|{draft_hash}".encode("utf-8")).hexdigest()
    token, token_item = _question_approval_token(
        state, approval=approval, subject_id=subject_id, client_id=client_id, username=username,
    )
    existing = notifications.get(notification_key)
    if isinstance(existing, dict) and (existing.get("interactive_sent") is True or bool(existing.get("sent_at"))):
        _question_set_active_thread(
            state, approval_id=approval_id, token=token,
            subject_id=subject_id, client_id=client_id, username=username,
            card_context=_question_card_context(approval, token_item),
        )
        return
    result = _post_interactive_approval(
        config, subject_id=subject_id, fingerprint=f"ppv:{notification_key}:{token}",
        token=token, body=_question_approval_body(approval), state=state,
    )
    if str(result.get("status") or "") in {"sent", "duplicate"}:
        notifications[notification_key] = {
            "sent_at": _now(), "interactive_sent": True, "status": "sent",
            "approval_id": approval_id, "subject_id": subject_id,
        }
        _question_set_active_thread(
            state, approval_id=approval_id, token=token,
            subject_id=subject_id, client_id=client_id, username=username,
            card_context=_question_card_context(approval, token_item),
        )
        return
    blocked_status = str(result.get("status") or result.get("error") or "interactive_blocked")
    _record_blocked_question_notification(
        config, notifications, notification_key, approval_id, subject_id, store, blocked_status,
    )

def _forward_question_approvals(config: dict[str, Any], state: dict[str, Any]) -> None:
    try:
        from backend.services import perguntas_pos_venda_state as ppv_state

        eligible = _eligible_question_bindings(config)
        if not eligible:
            return
        notifications = state.get("question_approval_notifications") if isinstance(state.get("question_approval_notifications"), dict) else {}
        for _binding, client_id, username, subject_id in eligible:
            _forward_question_approval_for_binding(
                config, state, ppv_state, notifications, client_id, username, subject_id,
            )
        state["question_approval_notifications"] = notifications
        _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_approval_notification: {str(exc)[:800]}"

_question_agentic_free_text_action = _question_natural_language._question_agentic_free_text_action
_natural_question_request = _question_natural_language._natural_question_request
_natural_question_confirm = _question_natural_language._natural_question_confirm
_natural_question_cancel_research = _question_natural_language._natural_question_cancel_research
_natural_question_suggest = _question_natural_language._natural_question_suggest
_natural_question_decide = _question_natural_language._natural_question_decide

def _handle_question_natural_language(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    return _question_natural_language.invoke(
        "_handle_question_natural_language",
        config,
        state,
        message,
        session,
    )

def _question_command_correct(
    config: dict[str, Any],
    state: dict[str, Any],
    token_item: dict[str, Any],
    approval_id: str,
    token: str,
    subject_id: str,
    client_id: str,
    username: str,
    message_id: str,
) -> bool:
    token_item.update({"awaiting_correction": True, "correction_requested_at": _now(), "correction_message_id": message_id})
    _question_set_active_thread(
        state, approval_id=approval_id, token=token,
        subject_id=subject_id, client_id=client_id, username=username,
    )
    _save_state(state)
    _post_command_reply(
        config, message_id,
        "Envie agora a orientacao para corrigir esta mesma resposta. Depois apresentarei uma nova confirmacao; nada foi enviado ao comprador.",
        "BLACK JHON - INFORME A CORRECAO",
    )
    return True

def _question_command_regenerate(
    config: dict[str, Any],
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    token_item: dict[str, Any],
    subject_id: str,
    client_id: str,
    username: str,
    message_id: str,
) -> bool:
    guidance = str(token_item.get("user_guidance") or approval.get("whatsapp_user_guidance") or "").strip()[:1200]
    regenerated = _regenerate_question_approval_response(approval, approvals, client_id, guidance=guidance)
    token, token_item = _question_approval_token(
        state, approval=approval, subject_id=subject_id, client_id=client_id,
        username=username, force_new=True, user_guidance=guidance,
    )
    token_item["regenerated_at"] = _now()
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    fingerprint = "ppv-regen:" + hashlib.sha256(
        f"{subject_id}|{token}|{message_id}|{regenerated}".encode("utf-8")
    ).hexdigest()
    result = _post_interactive_approval(
        config, subject_id=subject_id, fingerprint=fingerprint,
        token=token, body=_question_approval_body(approval, regenerated), state=state,
    )
    _save_state(state)
    if str(result.get("status") or "") in {"sent", "duplicate"}:
        _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
    else:
        _post_command_reply(
            config, message_id,
            "A nova resposta ficou salva, mas os botoes de decisao nao puderam ser abertos. Peça para gerar novamente.",
            "BLACK JHON - BOTOES INDISPONIVEIS",
        )
    return True

def _question_command_approve(
    config: dict[str, Any],
    state: dict[str, Any],
    ppv_endpoints: Any,
    approval: dict[str, Any],
    token_item: dict[str, Any],
    approval_id: str,
    subject_id: str,
    client_id: str,
    username: str,
    message_id: str,
) -> bool:
    from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest

    draft, idempotency_key = _question_validate_approval_send(token_item, approval, client_id=client_id)
    payload = PerguntasAprovacaoRequest(
        approval_id=approval_id,
        store=str(token_item.get("store") or approval.get("loja") or ""),
        question_id=str(
            token_item.get("question_id")
            or approval.get("question_id")
            or approval.get("pergunta_id")
            or ""
        ),
        resposta=draft,
        idempotency_key=idempotency_key,
    )
    audit_details = {
        "client_id": client_id,
        "store": str(approval.get("loja") or ""),
        "question_id": str(token_item.get("question_id") or ""),
        "draft_hash": str(token_item.get("draft_hash") or ""),
        "idempotency_key": idempotency_key,
    }
    _bridge_store().audit(
        "mercado_livre_answer_approval_requested",
        message_id=message_id, subject_id=subject_id, details=audit_details,
    )
    result = ppv_endpoints.ml_perguntas_aprovacoes_aprovar(payload, client_id)
    resolved = result.get("approval") if isinstance(result, dict) and isinstance(result.get("approval"), dict) else {}
    api_status = str(resolved.get("status") or "")
    if api_status == "answered_elsewhere":
        token_item.update({"used": True, "decision": "answered_elsewhere", "decided_at": _now()})
        _question_clear_active_thread(state, subject_id=subject_id, client_id=client_id, username=username)
        _save_state(state)
        _post_command_reply(
            config, message_id,
            "A pergunta ja foi respondida por outro fluxo e nenhuma resposta foi repetida.",
            "BLACK JHON - JA RESOLVIDA",
        )
        return True
    if api_status not in {"sent", "sent_reconciled", "sent_approved", "sent_manual", "sent_manual_pos_venda"}:
        raise RuntimeError("question_answer_not_confirmed")
    token_item.update({"used": True, "decision": "approve", "decided_at": _now()})
    _question_clear_active_thread(state, subject_id=subject_id, client_id=client_id, username=username)
    _bridge_store().audit(
        "mercado_livre_answer_sent",
        message_id=message_id, subject_id=subject_id,
        details={**audit_details, "api_status": api_status},
    )
    _save_state(state)
    _post_command_reply(
        config, message_id,
        "Resposta aprovada e enviada ao comprador pelo Mercado Livre.",
        "BLACK JHON - RESPOSTA ENVIADA",
    )
    return True

def _question_command_reject(
    config: dict[str, Any],
    state: dict[str, Any],
    ppv_state: Any,
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    token_item: dict[str, Any],
    approval_id: str,
    token: str,
    subject_id: str,
    client_id: str,
    username: str,
    message_id: str,
) -> bool:
    approval["whatsapp_suggestion_rejected_at"] = _now()
    approval["whatsapp_suggestion_rejected_by"] = username
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    token_item.update({"used": False, "decision": "suggestion_rejected", "decided_at": _now()})
    _question_set_active_thread(
        state, approval_id=approval_id, token=token,
        subject_id=subject_id, client_id=client_id, username=username,
        card_context=_question_card_context(approval, token_item),
    )
    _save_state(state)
    _post_command_reply(
        config, message_id,
        "Sugestao negada. Nenhuma resposta foi enviada ao comprador e esta pergunta continua ativa. Envie sua orientacao ou escolha Gerar nova resposta.",
        "BLACK JHON - SUGESTAO NEGADA",
    )
    return True

def _handle_question_approval_command(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    action, token = _question_approval_command(message.get("text_body"))
    if not action:
        return False
    message_id = str(message.get("message_id") or "")
    subject_id = str(message.get("subject_id") or "").strip()
    if not _question_approval_allowed(session.get("permissions") or {}):
        _post_command_reply(config, message_id, "Este usuario nao possui permissao para Perguntas e pos-venda.", "BLACK JHON - ACESSO NEGADO")
        return True
    token_item = _question_approval_lookup(state, token, subject_id=subject_id, session=session)
    if not token_item:
        _post_command_reply(config, message_id, "Esta decisao expirou, ja foi usada ou pertence a outro numero.", "BLACK JHON - DECISAO INVALIDA")
        return True
    client_id = str(session.get("client_id") or "")
    username = str(session.get("username") or "")
    approval_id = str(token_item.get("approval_id") or "")
    try:
        from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
        from backend.services import perguntas_pos_venda_state as ppv_state

        approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
        matching_approvals = [
            item
            for item in approvals
            if isinstance(item, dict)
            and str(item.get("status") or "pending") in {"pending", "sending"}
            and _question_token_approval_matches(token_item, item)
        ]
        if len(matching_approvals) != 1:
            token_item["used"] = True
            _save_state(state)
            _post_command_reply(
                config,
                message_id,
                "Esta decisao nao identifica uma unica pergunta compativel. Nenhuma acao foi aplicada.",
                "BLACK JHON - DECISAO INVALIDA",
            )
            return True
        approval = matching_approvals[0]
        approval_id = str(approval.get("id") or "")
        _question_bind_token_draft(token_item, approval)
        if action == "correct":
            return _question_command_correct(
                config, state, token_item, approval_id, token, subject_id,
                client_id, username, message_id,
            )
        if action in {"regenerate", "suggest"}:
            return _question_command_regenerate(
                config, state, approvals, approval, token_item,
                subject_id, client_id, username, message_id,
            )
        if action == "approve":
            return _question_command_approve(
                config, state, ppv_endpoints, approval, token_item, approval_id,
                subject_id, client_id, username, message_id,
            )
        return _question_command_reject(
            config, state, ppv_state, approvals, approval, token_item,
            approval_id, token, subject_id, client_id, username, message_id,
        )
    except _QuestionResearchPending as pending:
        token_item.update({
            "used": True, "decision": "research_superseded",
            "research_job_id": pending.job_id, "research_started_at": _now(),
        })
        _save_state(state)
        _post_command_reply(
            config, message_id,
            "Continuo pesquisando em novas fontes ate obter evidencia tecnica suficiente. "
            "Quando concluir, envio a nova sugestao para sua aprovacao. Nenhuma resposta foi enviada ao comprador.",
            "BLACK JHON - PESQUISA EM ANDAMENTO",
        )
    except HTTPException as exc:
        _post_command_reply(config, message_id, str(exc.detail or "Nao foi possivel aplicar esta decisao."), "BLACK JHON - DECISAO NAO APLICADA")
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_approval_action: {str(exc)[:800]}"
        _post_command_reply(config, message_id, "Nao foi possivel aplicar a decisao agora. Nada foi enviado ao comprador.", "BLACK JHON - DECISAO NAO APLICADA")
    return True

_COMPONENT_FUNCTIONS = frozenset((
    '_deliver_completed_question_research',
    '_forward_question_approvals',
    '_handle_question_natural_language',
    '_handle_question_approval_command'
))
_IMPLEMENTATIONS = {
    '_deliver_completed_question_research': _deliver_completed_question_research,
    '_forward_question_approvals': _forward_question_approvals,
    '_handle_question_natural_language': _handle_question_natural_language,
    '_handle_question_approval_command': _handle_question_approval_command
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)
    _question_natural_language.bind_bridge_dependencies(dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
