"""Extracted WhatsApp bridge component: intent_runtime."""

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
from backend.services import admin_usuarios_common, codex_actions, codex_whatsapp_agents
from backend.services.codex.console import security as console_security
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _whatsapp_text_key(value: Any) -> str:
    return whatsapp_formatting._whatsapp_text_key(value)

def _whatsapp_daily_sales_report_requested(value: Any) -> bool:
    return whatsapp_formatting._whatsapp_daily_sales_report_requested(value)

def _whatsapp_sales_report_requested(value: Any) -> bool:
    return whatsapp_formatting._whatsapp_sales_report_requested(value)

def _whatsapp_query_only_domains(value: Any) -> list[str]:
    return whatsapp_intent.query_only_domains(value)

def _whatsapp_source_policy(value: Any) -> dict[str, Any]:
    try:
        from backend.services.codex.assistant import routing as assistant_routing

        policy = assistant_routing.source_policy(str(value or ""))
    except Exception:
        policy = {}
    return dict(policy) if isinstance(policy, dict) else {}

def _whatsapp_readonly_inquiry(value: Any) -> bool:
    return whatsapp_intent.readonly_inquiry(value)

def _whatsapp_general_answer_request(value: Any, session: Optional[dict[str, Any]] = None) -> bool:
    """Distingue conversa geral de consultas/acoes sobre o JK Sistema."""
    text = _whatsapp_text_key(value)
    if not text:
        return False

    internal_anchor = bool(
        re.search(
            r"\b(jk sistema|sistema jk|black jhon|black john|neste sistema|no sistema|do sistema|"
            r"modulo|tela|configuracoes|favoritos|medias e compras|fila interna|funcao interna|"
            r"tarefa interna|cadastro interno|banco de dados interno)\b",
            text,
        )
    )
    if internal_anchor:
        return False

    stores: list[str] = []
    try:
        stores = _whatsapp_session_stores(session or {})
    except Exception:
        stores = []
    if stores and _whatsapp_exact_store_matches(str(value or ""), stores):
        return False

    technical_identifier = bool(
        re.search(r"\b(?:sku|mlb|pack|order|pedido|venda)\s*[:#-]?\s*[a-z0-9][a-z0-9._-]*\b", text)
        or re.search(r"\b\d{10,20}\b", text)
    )
    if technical_identifier:
        return False

    business_subject = bool(
        re.search(
            r"\b(estoque|saldo|venda|vendas|pedido|pedidos|anuncio|anuncios|mercado livre|bling|"
            r"full|loja|lojas|sku|comprador|cliente|devolucao|devolucoes|reclamacao|reclamacoes|"
            r"pergunta|perguntas|pos venda|relatorio|importacao|fornecedor|preco|margem|faturamento)\b",
            text,
        )
    )
    owned_or_current_data = bool(
        re.search(
            r"\b(meu|meus|minha|minhas|nosso|nossos|nossa|nossas|da loja|das lojas|cadastrado|"
            r"cadastrada|atual|agora|hoje|ontem|ultima|ultimas|ultimo|ultimos|pendente|pendentes)\b",
            text,
        )
    )
    operational_request = bool(
        re.search(
            r"\b(consulte|consultar|verifique|verificar|liste|listar|mostre|mostrar|informe|informar|"
            r"busque|buscar|atualize|atualizar|sincronize|sincronizar|altere|alterar|remova|remover|"
            r"responda|responder|envie|enviar|publique|publicar|cancele|cancelar)\b",
            text,
        )
    )
    if business_subject and (owned_or_current_data or operational_request):
        return False

    general_marker = bool(
        re.search(
            r"\b(o que e|como funciona|como posso|como fazer|explique|qual a diferenca|para que serve|"
            r"por que|porque|quem e|onde fica|me de uma dica|me de ideias|escreva|redija|traduza|"
            r"corrija (?:a|esta|essa|este|esse|frase|texto)|resuma (?:o|a|este|esta|esse|essa|texto))\b",
            text,
        )
    )
    if general_marker:
        return True

    # Sem qualquer assunto operacional conhecido, a mensagem e conversa geral
    # (saudacao, conhecimento, escrita, calculo ou pergunta cotidiana).
    return not business_subject

def _whatsapp_mutation_intent(value: Any) -> bool:
    return whatsapp_intent.mutation_intent(
        value,
        mutation_detector=console_security.prompt_requests_mutation,
    )

def _whatsapp_post_sale_action(value: Any) -> bool:
    return whatsapp_intent.post_sale_action(value)

def _whatsapp_protected_mutation_domains(value: Any) -> list[str]:
    return whatsapp_intent.protected_mutation_domains(
        value,
        mutation_detector=console_security.prompt_requests_mutation,
    )

def _whatsapp_action_spec_query_only_domains(spec: Any) -> list[str]:
    return whatsapp_intent.action_spec_query_only_domains(spec)

def _whatsapp_load_store_configs(client_id: Any) -> list[dict[str, Any]]:
    tenant = str(client_id or "default").strip() or "default"
    try:
        from backend.services.integracoes import carregar_lojas

        return [item for item in (carregar_lojas(tenant) or []) if isinstance(item, dict)]
    except Exception:
        path = (_info_dir() / tenant / "lojas_config.json").resolve()
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

def _whatsapp_authorized_api_stores(
    client_id: Any,
    permissions: Any,
    domains: list[str],
    providers: Optional[list[str]] = None,
) -> list[str]:
    permissions = permissions if isinstance(permissions, dict) else {}
    is_full = permissions.get("full") is True
    providers = [str(item or "").strip() for item in (providers or []) if str(item or "").strip()]
    if "vendas" in domains and not (is_full or permissions.get("vendas") is True):
        return []
    if "anuncios_ml" in domains and not (is_full or permissions.get("anuncios_ml") is True):
        return []
    if "estoque" in domains and not (is_full or permissions.get("estoque") is True):
        return []
    if "mercado_full" in domains and not (is_full or permissions.get("mercado_full") is True):
        return []
    if "bling" in providers and not (is_full or permissions.get("integracao") is True):
        return []
    if "mercado_livre" in providers and "vendas" in domains and not (is_full or permissions.get("anuncios_ml") is True):
        return []

    stores: list[str] = []
    for store in _whatsapp_load_store_configs(client_id):
        name = str(store.get("nome") or "").strip()
        integrations = store.get("integracoes") if isinstance(store.get("integracoes"), dict) else {}
        ml = integrations.get("mercadolivre") if isinstance(integrations, dict) else {}
        bling = integrations.get("bling") if isinstance(integrations, dict) else {}
        ml_connected = isinstance(ml, dict) and bool(str(ml.get("access_token") or "").strip())
        bling_connected = isinstance(bling, dict) and bool(str(bling.get("access_token") or "").strip())
        if not name:
            continue
        if ("anuncios_ml" in domains or "mercado_full" in domains or "mercado_livre" in providers) and not ml_connected:
            continue
        if "bling" in providers and not bling_connected:
            continue
        if "vendas" in domains and not providers and not (bling_connected or ml_connected):
            continue
        if name not in stores:
            stores.append(name)
    return stores

def _whatsapp_exact_store_matches(value: Any, stores: list[str]) -> list[str]:
    return whatsapp_intent.exact_store_matches(value, stores)

def _whatsapp_all_stores_requested(value: Any) -> bool:
    return whatsapp_intent.all_stores_requested(value)

def _whatsapp_store_scoped_request(value: Any) -> bool:
    return whatsapp_intent.store_scoped_request(value)

def _whatsapp_session_stores(session: dict[str, Any]) -> list[str]:
    stores: list[str] = []
    for item in _whatsapp_load_store_configs(session.get("client_id")):
        name = str(item.get("nome") or "").strip()
        if name and name not in stores:
            stores.append(name)
    return stores

def _whatsapp_store_scope_policy(value: Any, session: dict[str, Any]) -> dict[str, Any]:
    if not _whatsapp_store_scoped_request(value):
        return {}
    stores = _whatsapp_session_stores(session)
    matches = _whatsapp_exact_store_matches(value, stores)
    all_stores = _whatsapp_all_stores_requested(value) or (
        len(matches) > 1 and bool(re.search(r"\b(compare|comparar|comparacao|entre)\b", _whatsapp_text_key(value)))
    )
    policy: dict[str, Any] = {
        "mode": "store_scope",
        "read_only": True,
        "store_required": True,
        "authorized_stores": stores,
        "store_matches": matches,
        "store": matches[0] if len(matches) == 1 else "",
        "store_mode": "all" if all_stores else "single",
    }
    if all_stores:
        policy["stores"] = matches if len(matches) > 1 else stores
    return policy

def _whatsapp_api_query_requires_store(value: Any, domains: list[str]) -> bool:
    return whatsapp_intent.api_query_requires_store(value, domains)

def _whatsapp_requested_api_providers(value: Any, domains: list[str]) -> list[str]:
    source_policy = _whatsapp_source_policy(value)
    preferred = [
        str(item or "").strip()
        for item in (source_policy.get("preferred_providers") or [])
        if str(item or "").strip() in {"bling", "mercado_livre"}
    ]
    if preferred:
        return list(dict.fromkeys(preferred))
    text = _whatsapp_text_key(value)
    providers: list[str] = []
    if "bling" in text:
        providers.append("bling")
    if "anuncios_ml" in domains or re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+)\b", text):
        providers.append("mercado_livre")
    if "vendas" in domains and re.search(r"\b(api|apis|via api)\b", text) and not providers:
        providers.extend(["bling", "mercado_livre"])
    return list(dict.fromkeys(providers))

def _whatsapp_query_policy(value: Any, session: dict[str, Any]) -> dict[str, Any]:
    domains = _whatsapp_query_only_domains(value)
    if _whatsapp_post_sale_action(value):
        domains = [domain for domain in domains if domain != "anuncios_ml"]
    if not domains:
        return {}
    policy: dict[str, Any] = {
        "mode": "query_only",
        "domains": domains,
        "read_only": True,
        "deny_approval": True,
        "store_required": _whatsapp_api_query_requires_store(value, domains),
        "store": "",
        "base_request": str(value or "").strip()[:2000],
    }
    policy["providers"] = _whatsapp_requested_api_providers(value, domains)
    source_policy = _whatsapp_source_policy(value)
    if source_policy:
        policy["source_policy"] = source_policy
        policy["fresh"] = bool(source_policy.get("force_refresh"))
        policy["bypass_cache"] = bool(source_policy.get("force_refresh"))
    text = _whatsapp_text_key(value)
    limit_match = re.search(r"\b(?:limite|limit|pagina de)\s*(\d{1,5})\b", text)
    sales_report = _whatsapp_sales_report_requested(value)
    policy["report_mode"] = sales_report
    policy["limit"] = (
        max(1, min(int(limit_match.group(1)), 20000 if sales_report else 100))
        if limit_match
        else 20000
        if sales_report
        else 20
    )
    offset_match = re.search(r"\b(?:offset|a partir de)\s*(\d{1,6})\b", text)
    if offset_match:
        policy["offset"] = max(0, min(int(offset_match.group(1)), 100000))
    elif re.search(r"\b(proximos|proximas|mais resultados|pagina seguinte)\b", text):
        policy["pagination"] = "next"
    if (
        re.search(r"\b(atualize agora|sem cache|direto da api|dados mais recentes|dados atualizados)\b", text)
        or ("api" in text and re.search(r"\b(atualize|atualizar)\b", text))
    ):
        policy["fresh"] = True
        policy["bypass_cache"] = True

    if policy["store_required"]:
        stores = _whatsapp_authorized_api_stores(
            session.get("client_id"),
            session.get("permissions"),
            domains,
            policy.get("providers"),
        )
        matches = _whatsapp_exact_store_matches(value, stores)
        policy["authorized_stores"] = stores
        policy["store_matches"] = matches
        all_stores = _whatsapp_all_stores_requested(value) or (
            len(matches) > 1 and bool(re.search(r"\b(compare|comparar|comparacao|entre)\b", text))
        )
        policy["store_mode"] = "all" if all_stores else "single"
        if all_stores:
            policy["stores"] = matches if len(matches) > 1 else stores
        elif len(matches) == 1:
            policy["store"] = matches[0]
    return policy

def _whatsapp_pagination_request(value: Any) -> bool:
    return whatsapp_intent.pagination_request(value)

def _whatsapp_contextual_report_request(value: Any) -> bool:
    return whatsapp_intent.contextual_report_request(value)

def _whatsapp_contextual_report_period(value: Any) -> tuple[str, str]:
    try:
        current = datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        current = datetime.now().astimezone()
    return whatsapp_intent.contextual_report_period(value, current=current)

def _whatsapp_implicit_store_followup(value: Any, *, context_age: float, has_direct_policy: bool) -> bool:
    return whatsapp_intent.implicit_store_followup(
        value,
        context_age=context_age,
        has_direct_policy=has_direct_policy,
        recent_seconds=WHATSAPP_IMPLICIT_STORE_RECENT_SECONDS,
    )


_COMPONENT_FUNCTIONS = frozenset((
    '_whatsapp_text_key',
    '_whatsapp_daily_sales_report_requested',
    '_whatsapp_sales_report_requested',
    '_whatsapp_query_only_domains',
    '_whatsapp_source_policy',
    '_whatsapp_readonly_inquiry',
    '_whatsapp_general_answer_request',
    '_whatsapp_mutation_intent',
    '_whatsapp_post_sale_action',
    '_whatsapp_protected_mutation_domains',
    '_whatsapp_action_spec_query_only_domains',
    '_whatsapp_load_store_configs',
    '_whatsapp_authorized_api_stores',
    '_whatsapp_exact_store_matches',
    '_whatsapp_all_stores_requested',
    '_whatsapp_store_scoped_request',
    '_whatsapp_session_stores',
    '_whatsapp_store_scope_policy',
    '_whatsapp_api_query_requires_store',
    '_whatsapp_requested_api_providers',
    '_whatsapp_query_policy',
    '_whatsapp_pagination_request',
    '_whatsapp_contextual_report_request',
    '_whatsapp_contextual_report_period',
    '_whatsapp_implicit_store_followup'
))
_IMPLEMENTATIONS = {
    '_whatsapp_text_key': _whatsapp_text_key,
    '_whatsapp_daily_sales_report_requested': _whatsapp_daily_sales_report_requested,
    '_whatsapp_sales_report_requested': _whatsapp_sales_report_requested,
    '_whatsapp_query_only_domains': _whatsapp_query_only_domains,
    '_whatsapp_source_policy': _whatsapp_source_policy,
    '_whatsapp_readonly_inquiry': _whatsapp_readonly_inquiry,
    '_whatsapp_general_answer_request': _whatsapp_general_answer_request,
    '_whatsapp_mutation_intent': _whatsapp_mutation_intent,
    '_whatsapp_post_sale_action': _whatsapp_post_sale_action,
    '_whatsapp_protected_mutation_domains': _whatsapp_protected_mutation_domains,
    '_whatsapp_action_spec_query_only_domains': _whatsapp_action_spec_query_only_domains,
    '_whatsapp_load_store_configs': _whatsapp_load_store_configs,
    '_whatsapp_authorized_api_stores': _whatsapp_authorized_api_stores,
    '_whatsapp_exact_store_matches': _whatsapp_exact_store_matches,
    '_whatsapp_all_stores_requested': _whatsapp_all_stores_requested,
    '_whatsapp_store_scoped_request': _whatsapp_store_scoped_request,
    '_whatsapp_session_stores': _whatsapp_session_stores,
    '_whatsapp_store_scope_policy': _whatsapp_store_scope_policy,
    '_whatsapp_api_query_requires_store': _whatsapp_api_query_requires_store,
    '_whatsapp_requested_api_providers': _whatsapp_requested_api_providers,
    '_whatsapp_query_policy': _whatsapp_query_policy,
    '_whatsapp_pagination_request': _whatsapp_pagination_request,
    '_whatsapp_contextual_report_request': _whatsapp_contextual_report_request,
    '_whatsapp_contextual_report_period': _whatsapp_contextual_report_period,
    '_whatsapp_implicit_store_followup': _whatsapp_implicit_store_followup
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
