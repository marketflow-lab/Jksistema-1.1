"""Extracted WhatsApp bridge component: question_tokens."""

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
from backend.services import perguntas_pos_venda_codex
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


def _question_approval_allowed(permissions: dict[str, Any]) -> bool:
    return bool(
        isinstance(permissions, dict)
        and (permissions.get("full") is True or permissions.get("perguntas_pos_venda") is True)
    )

def _question_approval_product_identity(approval: dict[str, Any]) -> tuple[str, str]:
    approval = approval if isinstance(approval, dict) else {}
    conversation = approval.get("conversa") if isinstance(approval.get("conversa"), dict) else {}
    items = approval.get("items") if isinstance(approval.get("items"), list) else conversation.get("items")
    items = [item for item in (items or []) if isinstance(item, dict)]
    first_item = items[0] if items else {}

    sku = str(
        approval.get("sku")
        or approval.get("item_sku")
        or first_item.get("sku")
        or first_item.get("seller_sku")
        or "nao informado"
    ).strip()[:80]
    link_candidates = (
        approval.get("permalink"),
        approval.get("item_permalink"),
        approval.get("product_link"),
        approval.get("link"),
        approval.get("url"),
        first_item.get("permalink"),
        first_item.get("item_permalink"),
        first_item.get("link"),
        first_item.get("url"),
    )
    link = ""
    for candidate in link_candidates:
        value = str(candidate or "").strip()
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            link = value[:300]
            break
    item_id = str(approval.get("item_id") or first_item.get("id") or first_item.get("item_id") or "").strip()
    normalized_item_id = re.sub(r"[^A-Za-z0-9]", "", item_id).upper()
    if not link and re.fullmatch(r"MLB\d{6,}", normalized_item_id):
        link = f"https://produto.mercadolivre.com.br/{normalized_item_id}"
    return sku or "nao informado", link or "indisponivel"

def _question_approval_body(approval: dict[str, Any], response_override: str = "") -> str:
    store = str(approval.get("loja") or "Loja nao informada").strip()
    product = str(approval.get("titulo") or approval.get("sku") or approval.get("item_id") or "Produto nao informado").strip()
    question = str(approval.get("pergunta") or "Pergunta nao informada").strip()
    suggested = str(response_override or approval.get("resposta_sugerida") or "").strip()
    sku, product_link = _question_approval_product_identity(approval)
    prefix = f"Loja: {store[:80]}\nProduto: {product[:100]}\n\nPergunta:\n{question[:240]}\n\nSugestao do Black Jhon:\n"
    footer = f"\n\nSKU: {sku}\nLink do produto:\n{product_link}"
    available = max(0, 1024 - len(prefix) - len(footer))
    return (prefix + suggested[:available].rstrip() + footer).strip()

def _question_approval_token(
    state: dict[str, Any],
    *,
    approval: dict[str, Any],
    subject_id: str,
    client_id: str,
    username: str,
    force_new: bool = False,
    user_guidance: str = "",
) -> tuple[str, dict[str, Any]]:
    approval_id = str(approval.get("id") or "").strip()
    question_id = str(approval.get("question_id") or approval.get("pergunta_id") or approval_id).strip()
    store = str(approval.get("loja") or "").strip()
    suggested_response = str(approval.get("resposta_sugerida") or "").strip()[:1200]
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    now = time.time()
    for token, item in list(tokens.items()):
        if not isinstance(item, dict) or now - float(item.get("created_at") or 0) > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
            tokens.pop(token, None)
            continue
        if (
            not force_new
            and item.get("used") is not True
            and str(item.get("approval_id") or "") == approval_id
            and str(item.get("subject_id") or "") == subject_id
            and str(item.get("client_id") or "") == client_id
            and str(item.get("username") or "").strip().lower() == username.strip().lower()
            and (not str(item.get("store") or "").strip() or str(item.get("store") or "").strip() == store)
            and (
                not str(item.get("question_id") or "").strip()
                or str(item.get("question_id") or "").strip() == question_id
            )
        ):
            if not str(item.get("suggested_response") or "").strip() and suggested_response:
                item["suggested_response"] = suggested_response
                item["draft_hash"] = hashlib.sha256(suggested_response.encode("utf-8")).hexdigest()
                item["draft_created_at"] = _now()
            item["store"] = store
            item["question_id"] = question_id
            state["question_approval_tokens"] = tokens
            return str(token), item
    token = _approval_code()
    while token in tokens:
        token = _approval_code()
    item = {
        "approval_id": approval_id,
        "subject_id": subject_id,
        "client_id": client_id,
        "username": username.strip().lower(),
        "store": store,
        "question_id": question_id,
        "created_at": now,
        "used": False,
        "suggested_response": suggested_response,
        "draft_hash": hashlib.sha256(suggested_response.encode("utf-8")).hexdigest() if suggested_response else "",
        "draft_created_at": _now(),
    }
    guidance = str(user_guidance or "").strip()[:1200]
    if guidance:
        item["user_guidance"] = guidance
    tokens[token] = item
    state["question_approval_tokens"] = tokens
    return token, item

def _question_thread_key(client_id: Any, subject_id: Any, username: Any) -> str:
    identity = "|".join(
        (
            str(client_id or "").strip(),
            str(subject_id or "").strip(),
            str(username or "").strip().lower(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _question_token_scope_matches(
    item: Any,
    *,
    subject_id: str,
    client_id: str,
    username: str,
) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("used") is not True
        and str(item.get("subject_id") or "") == str(subject_id or "")
        and str(item.get("client_id") or "") == str(client_id or "")
        and str(item.get("username") or "").strip().lower()
        == str(username or "").strip().lower()
    )


def _question_token_approval_matches(item: Any, approval: Any) -> bool:
    if not isinstance(item, dict) or not isinstance(approval, dict):
        return False
    approval_id = str(approval.get("id") or "").strip()
    question_id = str(approval.get("question_id") or approval.get("pergunta_id") or approval_id).strip()
    store = str(approval.get("loja") or "").strip()
    return bool(
        approval_id
        and str(item.get("approval_id") or "").strip() == approval_id
        and (not str(item.get("store") or "").strip() or str(item.get("store") or "").strip() == store)
        and (
            not str(item.get("question_id") or "").strip()
            or str(item.get("question_id") or "").strip() == question_id
        )
    )


def _question_card_context(approval: Any, token_item: Any = None) -> dict[str, Any]:
    """Build the bounded, non-capability context shown in the proactive card."""

    source = approval if isinstance(approval, dict) else {}
    token = token_item if isinstance(token_item, dict) else {}
    draft = (
        str(token.get("suggested_response") or "").strip()
        or str(source.get("resposta_sugerida") or "").strip()
    )[:1200]
    approval_id = str(source.get("id") or token.get("approval_id") or "").strip()[:120]
    question_id = str(source.get("question_id") or source.get("pergunta_id") or approval_id).strip()[:120]
    return {
        "schema_version": "jk.whatsapp.ml-question-draft-context.v1",
        "kind": "mercado_livre_public_question_draft",
        "approval_id": approval_id,
        "question_id": question_id,
        "status": str(source.get("status") or "pending").strip()[:40],
        "store": str(source.get("loja") or "").strip()[:200],
        "item_id": str(source.get("item_id") or "").strip()[:80],
        "sku": str(source.get("sku") or source.get("item_sku") or "").strip()[:100],
        "title": str(source.get("titulo") or "").strip()[:300],
        "question": str(source.get("pergunta") or "").strip()[:1200],
        "draft": draft,
        "draft_hash": hashlib.sha256(draft.encode("utf-8")).hexdigest() if draft else "",
        "free_text_actions": ["request_revision"],
        "approval_requires_typed_token": True,
        "free_text_can_send": False,
    }

def _question_set_active_thread(
    state: dict[str, Any],
    *,
    approval_id: str,
    token: str,
    subject_id: str,
    client_id: str,
    username: str,
    card_context: Optional[dict[str, Any]] = None,
) -> None:
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    key = _question_thread_key(client_id, subject_id, username)
    previous = threads.get(key) if isinstance(threads.get(key), dict) else {}
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    token_item = tokens.get(str(token or "").strip().upper())
    outbound_message_id = (
        str(token_item.get("outbound_message_id") or "").strip()[:200]
        if isinstance(token_item, dict)
        else ""
    )
    if not outbound_message_id and str(previous.get("token") or "").strip().upper() == str(token or "").strip().upper():
        outbound_message_id = str(previous.get("outbound_message_id") or "").strip()[:200]
    threads[key] = {
        "approval_id": str(approval_id or "").strip(),
        "token": str(token or "").strip().upper(),
        "subject_id": str(subject_id or "").strip(),
        "client_id": str(client_id or "").strip(),
        "username": str(username or "").strip().lower(),
        "activated_at": time.time(),
        "outbound_message_id": outbound_message_id,
        "card_context": (
            dict(card_context)
            if isinstance(card_context, dict) and card_context
            else dict(previous.get("card_context") or {})
        ),
    }
    state["question_active_threads"] = threads

def _question_clear_active_thread(
    state: dict[str, Any],
    *,
    subject_id: str,
    client_id: str,
    username: str,
) -> None:
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    threads.pop(_question_thread_key(client_id, subject_id, username), None)
    state["question_active_threads"] = threads


def _question_approval_job_id(approval: dict[str, Any]) -> str:
    return str(
        approval.get("proposal_id")
        or approval.get("codex_job_id")
        or approval.get("research_job_id")
        or ""
    ).strip()


def _question_approval_is_current(
    approval: dict[str, Any],
    *,
    client_id: str,
    require_current_contract: bool,
) -> bool:
    if str(approval.get("status") or "pending").strip().lower() not in {"pending", "sending"}:
        return False
    return bool(
        not require_current_contract
        or perguntas_pos_venda_codex.job_contract_current(
            client_id,
            _question_approval_job_id(approval),
        )
    )


def _question_quoted_approval(
    state: dict[str, Any],
    by_id: dict[str, list[dict[str, Any]]],
    tokens: dict[str, Any],
    *,
    quoted_message_id: str,
    subject_id: str,
    client_id: str,
    username: str,
    require_current_contract: bool,
) -> tuple[Optional[dict[str, Any]], str, Optional[dict[str, Any]]]:
    quoted_tokens = [
        (str(token).upper(), item)
        for token, item in tokens.items()
        if _question_token_scope_matches(
            item, subject_id=subject_id, client_id=client_id, username=username,
        )
        and str(item.get("outbound_message_id") or "").strip() == quoted_message_id
    ]
    if len(quoted_tokens) != 1:
        return None, "", None
    quoted_token, quoted_item = quoted_tokens[0]
    matching_approvals = [
        approval
        for approval in by_id.get(str(quoted_item.get("approval_id") or "").strip(), [])
        if _question_token_approval_matches(quoted_item, approval)
        and _question_approval_is_current(
            approval,
            client_id=client_id,
            require_current_contract=require_current_contract,
        )
    ]
    if len(matching_approvals) != 1:
        return None, "", None
    approval = matching_approvals[0]
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=quoted_token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, quoted_item),
    )
    return approval, quoted_token, quoted_item


def _question_latest_token_candidate(
    state: dict[str, Any],
    by_id: dict[str, list[dict[str, Any]]],
    tokens: dict[str, Any],
    *,
    subject_id: str,
    client_id: str,
    username: str,
    require_current_contract: bool,
) -> tuple[Optional[dict[str, Any]], str, Optional[dict[str, Any]]]:
    candidates = []
    for token, item in tokens.items():
        if not _question_token_scope_matches(
            item, subject_id=subject_id, client_id=client_id, username=username,
        ):
            continue
        matching_approvals = [
            approval
            for approval in by_id.get(str(item.get("approval_id") or "").strip(), [])
            if _question_token_approval_matches(item, approval)
            and str(approval.get("status") or "pending") == "pending"
            and _question_approval_is_current(
                approval,
                client_id=client_id,
                require_current_contract=require_current_contract,
            )
        ]
        if len(matching_approvals) == 1:
            candidates.append(
                (float(item.get("created_at") or 0), str(token).upper(), item, matching_approvals[0])
            )
    if not candidates:
        return None, "", None
    _created_at, token, token_item, approval = max(candidates, key=lambda value: value[0])
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    return approval, token, token_item


def _question_active_approval(
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    *,
    subject_id: str,
    client_id: str,
    username: str,
    quoted_message_id: str = "",
    require_current_contract: bool = False,
) -> tuple[Optional[dict[str, Any]], str, Optional[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for item in approvals:
        if not isinstance(item, dict):
            continue
        approval_id = str(item.get("id") or "").strip()
        if approval_id:
            by_id.setdefault(approval_id, []).append(item)
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    quoted_id = str(quoted_message_id or "").strip()[:200]
    if quoted_id:
        return _question_quoted_approval(
            state,
            by_id,
            tokens,
            quoted_message_id=quoted_id,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
            require_current_contract=require_current_contract,
        )
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    thread = threads.get(_question_thread_key(client_id, subject_id, username))
    if isinstance(thread, dict):
        if (
            str(thread.get("subject_id") or "") != str(subject_id or "")
            or str(thread.get("client_id") or "") != str(client_id or "")
            or str(thread.get("username") or "").strip().lower()
            != str(username or "").strip().lower()
        ):
            thread = None
    if isinstance(thread, dict):
        active_token = str(thread.get("token") or "").strip().upper()
        token_item = tokens.get(active_token)
        if _question_token_scope_matches(
            token_item, subject_id=subject_id, client_id=client_id, username=username,
        ):
            matching_approvals = [
                approval
                for approval in by_id.get(str(thread.get("approval_id") or "").strip(), [])
                if _question_token_approval_matches(token_item, approval)
                and _question_approval_is_current(
                    approval,
                    client_id=client_id,
                    require_current_contract=require_current_contract,
                )
            ]
            if len(matching_approvals) == 1:
                approval = matching_approvals[0]
                thread["card_context"] = _question_card_context(approval, token_item)
                state["question_active_threads"] = threads
                return approval, active_token, token_item

        # Legacy threads may point at a consumed, missing or stale-scope token.
        # The thread identity is still server-scoped. Recover only when its
        # approval id identifies exactly one approval, and any persisted card
        # context agrees with that approval's store/question identity.
        legacy_matches = [
            approval
            for approval in by_id.get(str(thread.get("approval_id") or "").strip(), [])
            if _question_approval_is_current(
                approval,
                client_id=client_id,
                require_current_contract=require_current_contract,
            )
        ]
        if len(legacy_matches) == 1:
            approval = legacy_matches[0]
            context = thread.get("card_context") if isinstance(thread.get("card_context"), dict) else {}
            expected_store = str(context.get("store") or "").strip()
            expected_question = str(context.get("question_id") or "").strip()
            approval_id = str(approval.get("id") or "").strip()
            approval_store = str(approval.get("loja") or "").strip()
            approval_question = str(
                approval.get("question_id") or approval.get("pergunta_id") or approval_id
            ).strip()
            if (
                (not expected_store or expected_store == approval_store)
                and (not expected_question or expected_question == approval_question)
            ):
                thread["card_context"] = _question_card_context(approval)
                state["question_active_threads"] = threads
                return approval, active_token, token_item if isinstance(token_item, dict) else None

        threads.pop(_question_thread_key(client_id, subject_id, username), None)
        state["question_active_threads"] = threads

    return _question_latest_token_candidate(
        state,
        by_id,
        tokens,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        require_current_contract=require_current_contract,
    )

def _question_approval_command(value: Any) -> tuple[str, str]:
    match = QUESTION_APPROVAL_COMMAND_RE.match(str(value or "").strip())
    if not match:
        return "", ""
    return str(match.group(1) or "").lower(), str(match.group(2) or "").upper()

def _question_approval_lookup(
    state: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    session: dict[str, Any],
) -> Optional[dict[str, Any]]:
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    item = tokens.get(str(token or "").upper())
    if not isinstance(item, dict) or item.get("used") is True:
        return None
    if time.time() - float(item.get("created_at") or 0) > QUESTION_APPROVAL_TOKEN_TTL_SECONDS:
        return None
    if str(item.get("subject_id") or "") != str(subject_id or ""):
        return None
    if str(item.get("client_id") or "") != str(session.get("client_id") or ""):
        return None
    if str(item.get("username") or "").strip().lower() != str(session.get("username") or "").strip().lower():
        return None
    return item

def _question_bind_token_draft(token_item: dict[str, Any], approval: dict[str, Any]) -> str:
    response = (
        str(token_item.get("suggested_response") or "").strip()
        or str(approval.get("resposta_sugerida") or "").strip()
    )[:1200]
    if response and not str(token_item.get("suggested_response") or "").strip():
        token_item["suggested_response"] = response
        token_item["draft_hash"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
        token_item["draft_created_at"] = _now()
    elif response and len(str(token_item.get("draft_hash") or "")) != 64:
        token_item["draft_hash"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
    return response

def _question_validate_approval_send(
    token_item: dict[str, Any],
    approval: dict[str, Any],
    *,
    client_id: str,
) -> tuple[str, str]:
    if str(approval.get("status") or "pending") != "pending":
        raise RuntimeError("question_already_resolved")
    approval_id = str(approval.get("id") or "").strip()
    question_id = str(approval.get("question_id") or approval.get("pergunta_id") or approval_id).strip()
    store = str(approval.get("loja") or "").strip()
    if not client_id or not approval_id or not question_id or not store:
        raise RuntimeError("question_approval_scope_incomplete")
    draft = _question_bind_token_draft(token_item, approval)
    if not draft:
        raise RuntimeError("question_approval_draft_empty")
    draft_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(str(token_item.get("draft_hash") or ""), draft_hash):
        raise RuntimeError("question_approval_draft_hash_mismatch")
    idempotency_key = hashlib.sha256(
        f"{client_id}|{store}|{question_id}|{draft_hash}".encode("utf-8")
    ).hexdigest()
    existing = str(token_item.get("idempotency_key") or "")
    if existing and not secrets.compare_digest(existing, idempotency_key):
        raise RuntimeError("question_approval_idempotency_mismatch")
    token_item["idempotency_key"] = idempotency_key
    if not str(token_item.get("question_id") or "").strip():
        token_item["question_id"] = question_id
    if not str(token_item.get("store") or "").strip():
        token_item["store"] = store
    return draft, idempotency_key

def _regenerate_question_approval_response(
    approval: dict[str, Any],
    approvals: list[dict[str, Any]],
    client_id: str,
    *,
    guidance: str = "",
) -> str:
    from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest, PosVendaGerarRespostaRequest
    from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
    from backend.services import perguntas_pos_venda_state as ppv_state

    approval_type = str(approval.get("tipo") or approval.get("approval_type") or "").strip().lower()
    store = str(approval.get("loja") or "").strip()
    exact_response = _question_explicit_response(guidance)
    if exact_response:
        generated = {"resposta": exact_response}
    elif approval_type == "pos_venda":
        generated = ppv_endpoints.ml_pos_venda_gerar_resposta_conversa(
            PosVendaGerarRespostaRequest(
                loja=store,
                pack_id=approval.get("pack_id") or "",
                order_id=approval.get("order_id") or "",
                buyer_id=approval.get("buyer_id") or "",
                max_chars=int(approval.get("max_chars") or 350),
                resposta_atual=str(approval.get("resposta_sugerida") or "").strip(),
                orientacao_usuario=str(guidance or "").strip()[:1200],
                async_mode=True,
            ),
            client_id,
        )
    else:
        question = {
            "id": str(approval.get("question_id") or "").strip(),
            "text": str(approval.get("pergunta") or "").strip(),
            "item_id": str(approval.get("item_id") or "").strip(),
            "item_title": str(approval.get("titulo") or "").strip(),
            "item_sku": str(approval.get("sku") or "").strip(),
            "buyer_question_chat": approval.get("mensagens") or [],
        }
        generated = ppv_endpoints.ml_perguntas_gerar_resposta_manual(
            PerguntasGerarRespostaRequest(
                loja=store,
                pergunta=question,
                resposta_atual=str(approval.get("resposta_sugerida") or "").strip(),
                orientacao_usuario=str(guidance or "").strip()[:1200],
                async_mode=True,
            ),
            client_id,
        )
    generated_result = generated.get("result") if isinstance((generated or {}).get("result"), dict) else {}
    generated_job_id = str((generated or {}).get("job_id") or generated_result.get("proposal_id") or "").strip()
    generated_status = str((generated or {}).get("status") or "completed").strip().lower()
    if generated_job_id:
        approval["codex_job_id"] = generated_job_id
        approval["proposal_id"] = generated_job_id
        approval["proposal_version"] = int(
            generated_result.get("proposal_version") or (generated or {}).get("proposal_version") or 1
        )
        approval["proposal_hash"] = str(
            generated_result.get("proposal_hash") or (generated or {}).get("proposal_hash") or ""
        )
        approval["data_sufficient"] = bool(
            generated_result.get("data_sufficient")
            if "data_sufficient" in generated_result
            else (generated or {}).get("data_sufficient")
        )
        approval["warnings"] = list(generated_result.get("warnings") or (generated or {}).get("warnings") or [])[:8]
    if str(guidance or "").strip():
        approval["whatsapp_user_guidance"] = str(guidance or "").strip()[:1200]
    if generated_job_id and (
        generated_status != "completed"
        or generated_result.get("data_sufficient") is False
        or ("data_sufficient" in generated and generated.get("data_sufficient") is False)
    ):
        approval.update(
            {
                "research_status": generated_status or "queued",
                "research_job_id": generated_job_id,
                "research_started_at": _now(),
                "research_delivery_state": "waiting_evidence",
                "data_sufficient": False,
            }
        )
        ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        raise _QuestionResearchPending(generated_job_id, generated_status)

    response = str((generated or {}).get("resposta") or (generated or {}).get("answer") or generated_result.get("resposta") or "").strip()[:1200]
    if not response:
        raise RuntimeError("generated_answer_empty")
    approval["resposta_sugerida"] = response
    approval["regenerated_at"] = _now()
    approval["regenerated_via"] = "whatsapp"
    approval["research_status"] = "completed" if generated_job_id else "not_required"
    approval["research_delivery_state"] = "ready"
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    return response

def _question_suggestion_guidance(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
    return raw

def _question_explicit_response(value: Any) -> str:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()[:1200]
    if not raw:
        return ""
    explicit = re.search(
        r"(?:responda assim|pode responder assim|sugest(?:ao|ão)(?: de resposta)?|"
        r"minha sugest(?:ao|ão)|use (?:esta|essa) resposta)\s*[:\-]\s*(.+)$",
        raw,
        flags=re.I,
    )
    return str(explicit.group(1) or "").strip()[:1200] if explicit else ""


_COMPONENT_FUNCTIONS = frozenset((
    '_question_approval_allowed',
    '_question_approval_product_identity',
    '_question_approval_body',
    '_question_approval_token',
    '_question_thread_key',
    '_question_token_scope_matches',
    '_question_card_context',
    '_question_set_active_thread',
    '_question_clear_active_thread',
    '_question_active_approval',
    '_question_approval_command',
    '_question_approval_lookup',
    '_question_bind_token_draft',
    '_question_validate_approval_send',
    '_regenerate_question_approval_response',
    '_question_suggestion_guidance',
    '_question_explicit_response'
))
_IMPLEMENTATIONS = {
    '_question_approval_allowed': _question_approval_allowed,
    '_question_approval_product_identity': _question_approval_product_identity,
    '_question_approval_body': _question_approval_body,
    '_question_approval_token': _question_approval_token,
    '_question_thread_key': _question_thread_key,
    '_question_token_scope_matches': _question_token_scope_matches,
    '_question_card_context': _question_card_context,
    '_question_set_active_thread': _question_set_active_thread,
    '_question_clear_active_thread': _question_clear_active_thread,
    '_question_active_approval': _question_active_approval,
    '_question_approval_command': _question_approval_command,
    '_question_approval_lookup': _question_approval_lookup,
    '_question_bind_token_draft': _question_bind_token_draft,
    '_question_validate_approval_send': _question_validate_approval_send,
    '_regenerate_question_approval_response': _regenerate_question_approval_response,
    '_question_suggestion_guidance': _question_suggestion_guidance,
    '_question_explicit_response': _question_explicit_response
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
