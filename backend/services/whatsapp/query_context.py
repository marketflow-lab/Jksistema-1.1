"""Extracted WhatsApp bridge component: query_context."""

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
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents, whatsapp_report_files, whatsapp_report_visuals, whatsapp_voice
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _whatsapp_materialized_product_identifiers(value: Any) -> tuple[str, str]:
    """Validate product identifiers already materialized by an agent/context."""

    raw = value if isinstance(value, dict) else {}
    entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    sku = str(raw.get("sku") or entities.get("sku") or "").strip()[:100]
    if (
        sku.casefold() in {"a", "ao", "da", "de", "do", "e", "em", "na", "no", "para"}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", sku)
    ):
        sku = ""
    item_id = re.sub(
        r"[^A-Z0-9]", "", str(raw.get("item_id") or raw.get("mlb") or entities.get("mlb") or "").upper()
    )[:60]
    if not re.fullmatch(r"MLB\d{6,}", item_id):
        item_id = ""
    return sku, item_id


def _whatsapp_inherit_query_store_context(
    value: Any,
    direct_policy: dict[str, Any],
    state: dict[str, Any],
    conversation_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Herda loja apenas de uma consulta recente da mesma conversa telefônica."""
    if _whatsapp_mutation_intent(value):
        return {}
    if direct_policy.get("store_mode") == "all" or len(direct_policy.get("store_matches") or []) == 1:
        return {}
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    previous = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not previous:
        return {}
    context_age = time.time() - float(previous.get("updated_at") or 0)
    if context_age < 0 or context_age > WHATSAPP_QUERY_CONTEXT_TTL_SECONDS:
        return {}
    if not _whatsapp_implicit_store_followup(
        value,
        context_age=context_age,
        has_direct_policy=bool(direct_policy),
    ):
        return {}

    domains = [
        str(item or "").strip()
        for item in (direct_policy.get("domains") or previous.get("domains") or [])
        if str(item or "").strip() in {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    ]
    if not domains:
        return {}
    providers = [
        str(item or "").strip()
        for item in (direct_policy.get("providers") or previous.get("providers") or [])
        if str(item or "").strip()
    ]
    authorized_stores = _whatsapp_authorized_api_stores(
        session.get("client_id"),
        session.get("permissions"),
        domains,
        providers,
    )
    previous_mode = str(previous.get("store_mode") or "single").strip() or "single"
    previous_store = str(previous.get("store") or "").strip()
    previous_stores = [
        str(item or "").strip()
        for item in (previous.get("stores") or [])
        if str(item or "").strip()
    ]

    policy = dict(direct_policy) if direct_policy else {
        "mode": "query_only",
        "read_only": True,
        "deny_approval": True,
        "report_mode": bool(previous.get("report_mode")),
        "limit": max(1, min(int(previous.get("limit") or 20), 20000 if previous.get("report_mode") else 100)),
    }
    policy.update({
        "domains": domains,
        "providers": providers,
        "store_required": True,
        "authorized_stores": authorized_stores,
        "base_request": str(
            value
            if direct_policy
            else previous.get("base_request") or value or ""
        ).strip()[:2000],
        "continuation_request": str(value or "").strip()[:1000],
        "inherited": True,
        "inherited_store_context": True,
    })
    # Product identity comes only from the agent-materialized policy or the
    # previously materialized conversation context, never from free-form text.
    current_sku, current_item_id = _whatsapp_materialized_product_identifiers(policy)
    inherited_sku, inherited_item_id = _whatsapp_materialized_product_identifiers(previous)
    policy["sku"] = current_sku or inherited_sku
    policy["item_id"] = current_item_id or inherited_item_id
    if (policy.get("sku") and not current_sku) or (policy.get("item_id") and not current_item_id):
        policy["inherited_product_context"] = True
        previous_request = str(previous.get("base_request") or "").strip()
        continuation = str(value or "").strip()
        policy["context_request"] = "\n".join(
            part for part in (previous_request, f"Continuacao: {continuation}" if continuation else "") if part
        )[:3000]
    if not isinstance(policy.get("source_policy"), dict) or not policy.get("source_policy"):
        policy["source_policy"] = (
            dict(previous.get("source_policy") or {})
            if isinstance(previous.get("source_policy"), dict)
            else {}
        )

    if previous_mode == "all":
        scoped_stores = [store for store in previous_stores if store in authorized_stores]
        policy.update({
            "store": "",
            "store_mode": "all" if scoped_stores else "single",
            "stores": scoped_stores,
            "store_matches": [],
        })
        if not scoped_stores:
            policy["continuation_invalid_store"] = True
        return policy

    valid_store = previous_store if previous_store in authorized_stores else ""
    policy.update({
        "store": valid_store,
        "store_mode": "single",
        "stores": [],
        "store_matches": [valid_store] if valid_store else [],
    })
    if not valid_store:
        policy["continuation_invalid_store"] = True
    return policy

def _continuation_base_policy(
    domains: list[str],
    store_required: bool,
    store: str,
    store_mode: str,
    scoped_stores: list[str],
    authorized_stores: list[str],
    providers: list[str],
    source_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": "query_only",
        "domains": domains,
        "read_only": True,
        "deny_approval": True,
        "store_required": store_required,
        "store": store if store_mode != "all" else "",
        "store_mode": store_mode,
        "stores": scoped_stores if store_mode == "all" else [],
        "authorized_stores": authorized_stores,
        "store_matches": [store] if store_mode != "all" and store else [],
        "providers": providers,
        "source_policy": source_policy,
    }


def _contextual_report_continuation(
    value: Any,
    previous: dict[str, Any],
    base: dict[str, Any],
    store: str,
    store_mode: str,
    scoped_stores: list[str],
) -> dict[str, Any]:
    period_start, period_end = _whatsapp_contextual_report_period(value)
    if period_start and period_end:
        scope_label = ", ".join(scoped_stores) if store_mode == "all" else store
        base_request = (
            "Relatorio completo de vendas pelo Mercado Livre"
            + (f" da loja {scope_label}" if scope_label else "")
            + f", no periodo de {period_start} a {period_end}."
        )
    else:
        base_request = str(previous.get("base_request") or value or "").strip()[:2000]
    result = {
        **base,
        "inherited": True,
        "contextual_report": True,
        "offset": 0,
        "provider_offsets": {},
        "limit": 20000,
        "report_mode": True,
        "base_request": base_request,
        "fresh": True,
        "bypass_cache": True,
    }
    if period_start and period_end:
        result.update({"data_inicio": period_start, "data_fim": period_end})
    return result


def _pagination_continuation(previous: dict[str, Any], base: dict[str, Any], report_mode: bool) -> dict[str, Any]:
    limit = max(1, min(int(previous.get("limit") or 20), 20000 if report_mode else 100))
    provider_offsets = {
        str(key): max(0, int(value))
        for key, value in (previous.get("provider_offsets") or {}).items()
        if str(key or "").strip() and value is not None
    } if isinstance(previous.get("provider_offsets"), dict) else {}
    if previous.get("pagination_complete") is True and not provider_offsets:
        return {**base, "pagination": "complete", "no_more_results": True}
    previous_offset = max(0, int(previous.get("offset") or 0))
    materialized_sku, materialized_item_id = _whatsapp_materialized_product_identifiers(previous)
    return {
        **base,
        "pagination": "next",
        "inherited": True,
        "offset": min(provider_offsets.values()) if provider_offsets else previous_offset + limit,
        "provider_offsets": provider_offsets,
        "limit": limit,
        "report_mode": report_mode,
        "base_request": str(previous.get("base_request") or "")[:2000],
        "fresh": bool(previous.get("fresh")),
        "bypass_cache": bool(previous.get("bypass_cache")),
        "sku": materialized_sku,
        "item_id": materialized_item_id,
        "context_request": str(previous.get("base_request") or "").strip()[:2000],
    }


def _whatsapp_query_continuation_policy(
    value: Any,
    state: dict[str, Any],
    conversation_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    contextual_report = _whatsapp_contextual_report_request(value)
    if not _whatsapp_pagination_request(value) and not contextual_report:
        return {}
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    previous = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not previous or time.time() - float(previous.get("updated_at") or 0) > WHATSAPP_QUERY_CONTEXT_TTL_SECONDS:
        return {}
    domains = [
        str(item or "").strip() for item in (previous.get("domains") or [])
        if str(item or "").strip() in {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    ]
    if not domains or (contextual_report and not (bool(previous.get("report_mode")) or "vendas" in domains)):
        return {}
    store = str(previous.get("store") or "").strip()
    store_mode = str(previous.get("store_mode") or "single").strip() or "single"
    store_required = bool(previous.get("store_required"))
    previous_stores = [str(item or "").strip() for item in (previous.get("stores") or []) if str(item or "").strip()]
    providers = [str(item or "").strip() for item in (previous.get("providers") or []) if str(item or "").strip()]
    source_policy = dict(previous.get("source_policy") or {}) if isinstance(previous.get("source_policy"), dict) else {}
    if contextual_report:
        providers = ["mercado_livre"]
        source_policy.update({"required_tools": ["mercado_livre_orders"], "preferred_providers": ["mercado_livre"], "force_refresh": True})
        source_policy["forbidden_tools"] = [
            str(item or "").strip() for item in (source_policy.get("forbidden_tools") or [])
            if str(item or "").strip() and str(item or "").strip() != "mercado_livre_orders"
        ]
    authorized_stores = _whatsapp_authorized_api_stores(
        session.get("client_id"), session.get("permissions"), domains, providers,
    ) if store_required else []
    scoped_stores = [item for item in previous_stores if item in authorized_stores]
    invalid_store = store_required and (
        (store_mode == "all" and not scoped_stores) or (store_mode != "all" and store not in authorized_stores)
    )
    if invalid_store:
        return {
            **_continuation_base_policy(domains, True, "", "single", [], authorized_stores, providers, source_policy),
            "continuation_invalid_store": True,
        }
    base = _continuation_base_policy(
        domains, store_required, store, store_mode, scoped_stores, authorized_stores, providers, source_policy,
    )
    report_mode = bool(previous.get("report_mode") or contextual_report)
    if contextual_report:
        return _contextual_report_continuation(value, previous, base, store, store_mode, scoped_stores)
    return _pagination_continuation(previous, base, report_mode)

def _whatsapp_remember_query_context(
    state: dict[str, Any],
    conversation_id: str,
    request_text: str,
    policy: dict[str, Any],
) -> None:
    if policy.get("mode") != "query_only":
        return
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    base_request = str(policy.get("base_request") or request_text or "").strip()[:2000]
    materialized_sku, materialized_item_id = _whatsapp_materialized_product_identifiers(policy)
    contexts[conversation_id] = {
        "domains": list(policy.get("domains") or []),
        "store_required": bool(policy.get("store_required")),
        "store": str(policy.get("store") or "").strip(),
        "store_mode": str(policy.get("store_mode") or "single").strip() or "single",
        "stores": [
            str(item or "").strip()
            for item in (policy.get("stores") or [])
            if str(item or "").strip()
        ],
        "providers": list(policy.get("providers") or []),
        "source_policy": dict(policy.get("source_policy") or {}) if isinstance(policy.get("source_policy"), dict) else {},
        "base_request": base_request,
        "sku": materialized_sku,
        "item_id": materialized_item_id,
        "offset": max(0, int(policy.get("offset") or 0)),
        "provider_offsets": dict(policy.get("provider_offsets") or {}) if isinstance(policy.get("provider_offsets"), dict) else {},
        "pagination_complete": False,
        "report_mode": bool(policy.get("report_mode")),
        "limit": max(1, min(int(policy.get("limit") or 20), 20000 if policy.get("report_mode") else 100)),
        "fresh": bool(policy.get("fresh")),
        "bypass_cache": bool(policy.get("bypass_cache")),
        "updated_at": time.time(),
    }
    state["query_contexts"] = contexts

def _whatsapp_update_query_context_from_task(
    state: dict[str, Any],
    pending: dict[str, Any],
    task: dict[str, Any],
) -> None:
    conversation_id = str(pending.get("conversation_id") or task.get("conversation_id") or "").strip()
    if not conversation_id:
        return
    contexts = state.get("query_contexts") if isinstance(state.get("query_contexts"), dict) else {}
    context = contexts.get(conversation_id) if isinstance(contexts.get(conversation_id), dict) else {}
    if not context:
        return
    materialized_sku = ""
    materialized_item_id = ""
    for source in (
        pending.get("manager_plan"),
        pending.get("data_selection_plan"),
        task.get("data_selection"),
    ):
        candidate_sku, candidate_item_id = _whatsapp_materialized_product_identifiers(source)
        materialized_sku = materialized_sku or candidate_sku
        materialized_item_id = materialized_item_id or candidate_item_id
    identity_changed = False
    if materialized_sku and context.get("sku") != materialized_sku:
        context["sku"] = materialized_sku
        identity_changed = True
    if materialized_item_id and context.get("item_id") != materialized_item_id:
        context["item_id"] = materialized_item_id
        identity_changed = True
    provider_offsets: dict[str, int] = {}
    saw_paging = False
    for item in task.get("tool_results_summary") if isinstance(task.get("tool_results_summary"), list) else []:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("tool_id") or "").strip()
        if tool_id not in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_listing"}:
            continue
        paging = item.get("paging") if isinstance(item.get("paging"), dict) else {}
        if not paging:
            continue
        saw_paging = True
        next_offset = paging.get("next_offset")
        if paging.get("has_more") is True and next_offset is not None:
            try:
                provider_offsets[tool_id] = max(0, int(next_offset))
            except (TypeError, ValueError):
                continue
    if not saw_paging and not identity_changed:
        return
    if saw_paging:
        context["provider_offsets"] = provider_offsets
        context["pagination_complete"] = not bool(provider_offsets)
        if provider_offsets:
            context["offset"] = min(provider_offsets.values())
    context["updated_at"] = time.time()
    contexts[conversation_id] = context
    state["query_contexts"] = contexts

def _whatsapp_query_only_block_text(domains: list[str]) -> str:
    labels = []
    if "vendas" in domains:
        labels.append("vendas")
    if "anuncios_ml" in domains:
        labels.append("anuncios do Mercado Livre")
    if "estoque" in domains:
        labels.append("estoque")
    if "mercado_full" in domains:
        labels.append("estoque Full do Mercado Livre")
    scope = " e ".join(labels) or "este modulo"
    return (
        f"Pelo WhatsApp, *{scope}* funciona somente para consultas. "
        "Nao posso sincronizar, alterar, pausar, publicar, responder, aprovar ou executar outra acao mutavel nesse dominio. "
        "Nenhum codigo de aprovacao foi criado. Voce pode pedir a leitura dos dados informando a loja."
    )

def _whatsapp_store_required_text(policy: dict[str, Any]) -> str:
    stores = [str(item) for item in (policy.get("authorized_stores") or []) if str(item or "").strip()]
    matches = [str(item) for item in (policy.get("store_matches") or []) if str(item or "").strip()]
    if matches:
        intro = "Encontrei mais de uma loja no pedido. Escolha qual deseja consultar."
    else:
        intro = "Qual loja voce quer consultar?"
    if not stores:
        return (
            "Nao encontrei nenhuma loja disponivel para este usuario. "
            "Confira as permissoes e o cadastro das lojas no JK Sistema."
        )
    options = "\n".join(f"- *{store}*" for store in stores[:20])
    return f"{intro}\n\n*Lojas disponiveis:*\n{options}\n\nVoce tambem pode pedir `todas as lojas` para receber o resultado separado por loja."

def _whatsapp_plain_inline(value: Any) -> str:
    return whatsapp_formatting._whatsapp_plain_inline(value)

def _whatsapp_field(line: Any) -> tuple[str, str]:
    return whatsapp_formatting._whatsapp_field(line)

def _whatsapp_heading_value(line: Any) -> str:
    return whatsapp_formatting._whatsapp_heading_value(line)

def _whatsapp_report_section_kind(heading: Any) -> str:
    return whatsapp_formatting._whatsapp_report_section_kind(heading)

def _whatsapp_report_sections(value: Any) -> dict[str, list[str]]:
    return whatsapp_formatting._whatsapp_report_sections(value)

def _whatsapp_metric_label(value: Any) -> str:
    return whatsapp_formatting._whatsapp_metric_label(value)

def _whatsapp_number(value: Any) -> str:
    return whatsapp_formatting._whatsapp_number(value)

def _whatsapp_money(value: Any) -> str:
    return whatsapp_formatting._whatsapp_money(value)

def _whatsapp_report_date(value: Any) -> str:
    return whatsapp_formatting._whatsapp_report_date(value)

def _whatsapp_daily_ml_sales_report(
    tool_results: list[dict[str, Any]],
    query_policy: dict[str, Any],
    request_text: Any,
) -> str:
    return whatsapp_formatting._whatsapp_daily_ml_sales_report(tool_results, query_policy, request_text)

def _whatsapp_report_summary(lines: list[str]) -> str:
    return whatsapp_formatting._whatsapp_report_summary(lines)

def _whatsapp_ranking_items(lines: list[str]) -> list[dict[str, Any]]:
    return whatsapp_formatting._whatsapp_ranking_items(lines)

def _whatsapp_rank_marker(index: int) -> str:
    return whatsapp_formatting._whatsapp_rank_marker(index)

def _whatsapp_report_ranking_parts(lines: list[str]) -> list[str]:
    return whatsapp_formatting._whatsapp_report_ranking_parts(lines)

def _whatsapp_report_text_section(title: str, lines: list[str]) -> list[str]:
    return whatsapp_formatting._whatsapp_report_text_section(title, lines)

def _whatsapp_report_response_parts(value: Any, title: str) -> list[str]:
    return whatsapp_formatting._whatsapp_report_response_parts(value, title)

def _whatsapp_split_body(
    value: Any,
    limit: int = WHATSAPP_PART_BODY_CHARS,
    max_parts: Optional[int] = WHATSAPP_MAX_PARTS,
) -> list[str]:
    return whatsapp_formatting._whatsapp_split_body(value, limit, max_parts)

def _whatsapp_operational_title(title: Any) -> bool:
    return whatsapp_formatting._whatsapp_operational_title(title)

def _whatsapp_response_parts(value: Any, title: str) -> list[str]:
    return whatsapp_formatting._whatsapp_response_parts(value, title)

def _whatsapp_result_title(prompt: Any, status: str = "completed") -> str:
    return whatsapp_formatting._whatsapp_result_title(prompt, status)

def _whatsapp_store_selection_token(value: Any) -> str:
    match = STORE_SELECTION_COMMAND_RE.fullmatch(str(value or "").strip())
    return str(match.group(1) or "").upper() if match else ""

def _whatsapp_consume_store_selection(
    state: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    session: dict[str, Any],
    conversation_id: str,
) -> Optional[dict[str, Any]]:
    selections = state.get("store_selection_tokens") if isinstance(state.get("store_selection_tokens"), dict) else {}
    item = selections.get(str(token or "").upper())
    if not isinstance(item, dict) or item.get("used") is True:
        return None
    if time.time() - float(item.get("created_at") or 0) > STORE_SELECTION_TOKEN_TTL_SECONDS:
        return None
    if str(item.get("subject_id") or "") != str(subject_id or ""):
        return None
    if str(item.get("client_id") or "") != str(session.get("client_id") or ""):
        return None
    if str(item.get("username") or "").strip().lower() != str(session.get("username") or "").strip().lower():
        return None
    if str(item.get("conversation_id") or "") != str(conversation_id or ""):
        return None
    item["used"] = True
    item["used_at"] = time.time()
    selections[str(token or "").upper()] = item
    state["store_selection_tokens"] = selections
    _save_state(state)
    return dict(item)

def _whatsapp_send_store_selection(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    conversation_id: str,
    request_text: str,
    stores: list[str],
    *,
    allow_all: bool = True,
) -> bool:
    message_id = str(message.get("message_id") or "").strip()
    subject_id = str(message.get("subject_id") or "").strip()
    all_available = list(dict.fromkeys(str(store or "").strip() for store in stores if str(store or "").strip()))
    available = all_available[:9 if allow_all else 10]
    if not message_id or not subject_id or not available:
        return False
    now = time.time()
    selections = state.get("store_selection_tokens") if isinstance(state.get("store_selection_tokens"), dict) else {}
    selections = {
        str(key): value
        for key, value in selections.items()
        if isinstance(value, dict)
        and value.get("used") is not True
        and now - float(value.get("created_at") or 0) <= STORE_SELECTION_TOKEN_TTL_SECONDS
    }
    options: list[dict[str, str]] = []
    if allow_all and len(all_available) > 1:
        token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        while token in selections:
            token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        selections[token] = {
            "subject_id": subject_id,
            "client_id": str(session.get("client_id") or ""),
            "username": str(session.get("username") or "").strip().lower(),
            "conversation_id": conversation_id,
            "request_text": str(request_text or "")[:12000],
            "store": "",
            "stores": all_available[:20],
            "store_mode": "all",
            "created_at": now,
            "used": False,
        }
        options.append({
            "id": f"store_select:{token}",
            "title": "Todas as lojas",
            "description": "Consultar cada loja separadamente",
        })
    for store in available:
        token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        while token in selections:
            token = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
        selections[token] = {
            "subject_id": subject_id,
            "client_id": str(session.get("client_id") or ""),
            "username": str(session.get("username") or "").strip().lower(),
            "conversation_id": conversation_id,
            "request_text": str(request_text or "")[:12000],
            "store": store,
            "store_mode": "single",
            "created_at": now,
            "used": False,
        }
        options.append({"id": f"store_select:{token}", "title": store, "description": "Consultar esta loja"})
    state["store_selection_tokens"] = selections
    _save_state(state)
    try:
        result = _post_interactive_store_selection(
            config,
            subject_id=subject_id,
            fingerprint="store-selection:" + hashlib.sha256(
                f"{subject_id}|{message_id}".encode("utf-8")
            ).hexdigest(),
            options=options,
        )
        if result.get("success") is True or str(result.get("status") or "") in {"sent", "duplicate"}:
            _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
            return True
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"store_selection: {str(exc)[:800]}"
    return False


_COMPONENT_FUNCTIONS = frozenset((
    '_whatsapp_inherit_query_store_context',
    '_whatsapp_query_continuation_policy',
    '_whatsapp_remember_query_context',
    '_whatsapp_update_query_context_from_task',
    '_whatsapp_query_only_block_text',
    '_whatsapp_store_required_text',
    '_whatsapp_plain_inline',
    '_whatsapp_field',
    '_whatsapp_heading_value',
    '_whatsapp_report_section_kind',
    '_whatsapp_report_sections',
    '_whatsapp_metric_label',
    '_whatsapp_number',
    '_whatsapp_money',
    '_whatsapp_report_date',
    '_whatsapp_daily_ml_sales_report',
    '_whatsapp_report_summary',
    '_whatsapp_ranking_items',
    '_whatsapp_rank_marker',
    '_whatsapp_report_ranking_parts',
    '_whatsapp_report_text_section',
    '_whatsapp_report_response_parts',
    '_whatsapp_split_body',
    '_whatsapp_operational_title',
    '_whatsapp_response_parts',
    '_whatsapp_result_title',
    '_whatsapp_store_selection_token',
    '_whatsapp_consume_store_selection',
    '_whatsapp_send_store_selection'
))
_IMPLEMENTATIONS = {
    '_whatsapp_inherit_query_store_context': _whatsapp_inherit_query_store_context,
    '_whatsapp_query_continuation_policy': _whatsapp_query_continuation_policy,
    '_whatsapp_remember_query_context': _whatsapp_remember_query_context,
    '_whatsapp_update_query_context_from_task': _whatsapp_update_query_context_from_task,
    '_whatsapp_query_only_block_text': _whatsapp_query_only_block_text,
    '_whatsapp_store_required_text': _whatsapp_store_required_text,
    '_whatsapp_plain_inline': _whatsapp_plain_inline,
    '_whatsapp_field': _whatsapp_field,
    '_whatsapp_heading_value': _whatsapp_heading_value,
    '_whatsapp_report_section_kind': _whatsapp_report_section_kind,
    '_whatsapp_report_sections': _whatsapp_report_sections,
    '_whatsapp_metric_label': _whatsapp_metric_label,
    '_whatsapp_number': _whatsapp_number,
    '_whatsapp_money': _whatsapp_money,
    '_whatsapp_report_date': _whatsapp_report_date,
    '_whatsapp_daily_ml_sales_report': _whatsapp_daily_ml_sales_report,
    '_whatsapp_report_summary': _whatsapp_report_summary,
    '_whatsapp_ranking_items': _whatsapp_ranking_items,
    '_whatsapp_rank_marker': _whatsapp_rank_marker,
    '_whatsapp_report_ranking_parts': _whatsapp_report_ranking_parts,
    '_whatsapp_report_text_section': _whatsapp_report_text_section,
    '_whatsapp_report_response_parts': _whatsapp_report_response_parts,
    '_whatsapp_split_body': _whatsapp_split_body,
    '_whatsapp_operational_title': _whatsapp_operational_title,
    '_whatsapp_response_parts': _whatsapp_response_parts,
    '_whatsapp_result_title': _whatsapp_result_title,
    '_whatsapp_store_selection_token': _whatsapp_store_selection_token,
    '_whatsapp_consume_store_selection': _whatsapp_consume_store_selection,
    '_whatsapp_send_store_selection': _whatsapp_send_store_selection
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
