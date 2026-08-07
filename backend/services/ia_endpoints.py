"""Public IA endpoint implementations."""

from __future__ import annotations

import asyncio
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError

from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services import codex_turn_context
from backend.modules.perguntas_pos_venda.ai import api as perguntas_agent_api
from backend.modules.perguntas_pos_venda.ai.validation import ML_PERGUNTAS_IA_V2_MODO
from backend.services.ia_common import *
from backend.services.ia_conversas import (
    _ia_conversas_atualizar_thread_codex,
    _ia_conversas_carregar,
    _ia_conversas_contexto_conversa,
    _ia_conversas_deletar,
    _ia_conversas_listar,
    _ia_conversas_salvar,
    _normalizar_conversa_id_ia,
    _normalizar_modo_conversa_ia,
    _normalizar_surface_conversa_ia,
)
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *

logger = None


def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
    if peers:
        target_globals.update(peers)
    return runtime


def configure_ia_endpoints_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


async def ia_secrets_status(client_id: str = Depends(get_tenant_id)):
    """Retorna apenas o estado do cofre local, sem expor valores sensiveis."""
    return {
        "success": True,
        "client_id": client_id,
        "provisioning_configured": bool(_ia_secrets_provisioning_url("/download")),
        "local_store": _secure_secrets_status(),
    }


async def ia_secrets_provisionar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _payload_sessao_por_authorization(authorization)
    if str(sessao.get("client_id") or "").strip() != str(client_id or "").strip():
        raise HTTPException(status_code=403, detail="Sessao invalida para este cliente.")
    _ia_secrets_validar_sessao_ativa(sessao)
    return await asyncio.to_thread(_ia_secrets_provisionar_cofre_local, sessao)


def ia_agent_perguntas_query(payload: IAAgentQueryRequest, request: Request):
    perguntas_agent_api.authorize_request(request)
    metodo = str(payload.classMethod or "query").strip() or "query"
    if metodo not in {"query", "run"}:
        raise HTTPException(status_code=400, detail="Metodo do agente nao suportado.")
    agent_input = perguntas_agent_api.parse_request_input(payload)
    client_id = str(
        agent_input.get("tenant_id")
        or agent_input.get("client_id")
        or request.headers.get("x-client-id")
        or ""
    ).strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Informe tenant_id no input do agente.")
    task = str(agent_input.get("task") or "").strip()
    if task and task not in {"mercado_livre_question_draft", "mercado_livre_question_draft_v2"}:
        raise HTTPException(status_code=400, detail="Tarefa do agente nao suportada neste endpoint.")
    try:
        generated = perguntas_agent_api.generate_response(client_id, agent_input)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "output": {
            "success": True,
            "resposta": generated.answer,
            "answer": generated.answer,
            "model": generated.model,
            "tool_results": [],
            "diagnostico_ia": generated.diagnostics,
            "modo_ia": ML_PERGUNTAS_IA_V2_MODO,
            "read_only": True,
        }
    }


def _ia_chat_selection_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        return dict(dumped) if isinstance(dumped, dict) else {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    raw = getattr(value, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) else {}


_IA_CHAT_SELECTION_MAX_CALLS = 6
_IA_CHAT_SELECTION_RESULT_MAX_CHARS = 3200
_IA_CHAT_SELECTION_CONTEXT_KEYS = (
    "data_selection",
    "codex_data_selection",
    "data_selection_plan",
)
_IA_CHAT_SELECTION_SENSITIVE_KEY_RE = re.compile(
    r"(?:tenant|client[_-]?id|cliente[_-]?id|authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|secret|password|senha|cookie|permissions?|username|user[_-]?id|"
    r"phone|telefone|buyer|cpf|cnpj|path|caminho)",
    re.I,
)
_IA_CHAT_SELECTION_STORE_KEYS = ("loja", "store", "conta", "store_name")
_IA_SIDEBAR_SURFACE = "sidebar_chat"
_IA_SIDEBAR_MODE = "quick_chat"
_IA_SIDEBAR_SCHEMA_VERSION = "sidebar-turn-v2"
_IA_SIDEBAR_PROMPT_VERSION = "sidebar-codex-2026-07-20-v2"
_IA_SIDEBAR_CONTEXT_MAX_CHARS = 12000
_IA_SIDEBAR_GENERAL_BUSINESS_RE = re.compile(
    r"\b(?:lojas?|contas?|sku|mlb|anuncios?|mercado\s*livre|bling|estoques?|vendas?|pedidos?|"
    r"devolucoes?|faturamentos?|produtos?|fornecedores?|compras?|relatorios?|margens?|precos?|full)\b",
    re.I,
)
_IA_SIDEBAR_CONTINUATION_RE = re.compile(
    r"\b(?:isso|isto|esse|essa|esses|essas|ele|ela|deles|delas|continue|continuar|anterior)\b",
    re.I,
)


def _ia_sidebar_mode_context(payload: IAChatRequest) -> tuple[str, str, str]:
    context = payload.context if isinstance(payload.context, dict) else {}
    surface = _normalizar_surface_conversa_ia(context.get("surface") or "")
    raw_mode = context.get("conversation_mode") or context.get("mode") or ""
    mode = _normalizar_modo_conversa_ia(
        raw_mode or (_IA_SIDEBAR_MODE if surface == _IA_SIDEBAR_SURFACE else "")
    )
    conversation_id = _normalizar_conversa_id_ia(
        context.get("conversation_id") or payload.conversa_id or ""
    )
    return surface, mode, conversation_id


def _ia_sidebar_conversation_decision(
    previous: Any,
    *,
    client_id: str,
    username: str,
    conversation_mode: str,
    conversation_id: str,
    screen_snapshot: dict[str, Any],
) -> codex_turn_context.ConversationDecisionV2:
    return codex_turn_context.decide_conversation(
        previous,
        surface=_IA_SIDEBAR_SURFACE,
        client_id=client_id,
        store=screen_snapshot.get("store") or "",
        user=username,
        subject=f"{conversation_mode or _IA_SIDEBAR_MODE}:{conversation_id}",
        prompt_contract={"version": _IA_SIDEBAR_PROMPT_VERSION},
        schema_contract={
            "version": _IA_SIDEBAR_SCHEMA_VERSION,
            "shared_contract_hash": codex_turn_context.CONTRACT_HASH,
        },
        scope={
            "conversation_mode": conversation_mode or _IA_SIDEBAR_MODE,
            "store_mode": screen_snapshot.get("store_mode") or "none",
            "store": screen_snapshot.get("store") or "",
            "multi_store": bool(screen_snapshot.get("multi_store")),
        },
    )


def _ia_sidebar_compact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.replace("\x00", " ")).strip()[:600]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_ia_sidebar_compact_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:80]: _ia_sidebar_compact_value(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
            if str(key or "").strip()
        }
    return str(value)[:300]


def _ia_sidebar_screen_context_v2(context: Any, message: str = "") -> dict[str, Any]:
    """Keep a typed snapshot; raw HTML, table bodies and broad DOM text never pass."""

    source = context if isinstance(context, dict) else {}
    nested = source.get("screen_context") if isinstance(source.get("screen_context"), dict) else source
    route = nested.get("route") if isinstance(nested.get("route"), dict) else {}
    period = nested.get("period") if isinstance(nested.get("period"), dict) else {}
    selection = nested.get("selection") if isinstance(nested.get("selection"), dict) else {}
    entity_refs = nested.get("entity_refs") if isinstance(nested.get("entity_refs"), dict) else {}
    filters = nested.get("filters") if isinstance(nested.get("filters"), list) else []
    metrics = nested.get("metrics") if isinstance(nested.get("metrics"), list) else []
    snapshot = {
        "schema_version": _IA_SIDEBAR_SCHEMA_VERSION,
        "surface": _IA_SIDEBAR_SURFACE,
        "captured_at": str(nested.get("captured_at") or "")[:40],
        "route": _ia_sidebar_compact_value({
            "title": route.get("title") or nested.get("title"),
            "pathname": route.get("pathname") or nested.get("pathname") or nested.get("url"),
            "module": route.get("module") or nested.get("modulo_atual"),
        }),
        "period": _ia_sidebar_compact_value(period),
        "filters": _ia_sidebar_compact_value(filters),
        "metrics": _ia_sidebar_compact_value(metrics),
        "selection": _ia_sidebar_compact_value(selection),
        "entity_refs": _ia_sidebar_compact_value(entity_refs),
        "store_mode": str(nested.get("store_mode") or "none").strip().lower(),
        "multi_store": nested.get("multi_store") is True,
    }
    store = str(nested.get("store") or entity_refs.get("store") or "").strip()[:180]
    if snapshot["store_mode"] == "all" and snapshot["multi_store"]:
        snapshot["store"] = ""
    elif snapshot["store_mode"] == "single" and store:
        snapshot["store"] = store
        snapshot["multi_store"] = False
    else:
        snapshot["store_mode"] = "none"
        snapshot["multi_store"] = False
        snapshot["store"] = ""
    for key in ("sku", "item_id"):
        value = str(nested.get(key) or entity_refs.get(key) or "").strip()[:100]
        if value:
            snapshot[key] = value
    general = not _IA_SIDEBAR_GENERAL_BUSINESS_RE.search(str(message or "")) and not _IA_SIDEBAR_CONTINUATION_RE.search(str(message or ""))
    snapshot["context_kind"] = "general" if general else "operational"
    if general:
        snapshot["store_mode"] = "none"
        snapshot["multi_store"] = False
        snapshot["store"] = ""
        snapshot["entity_refs"] = {
            key: value
            for key, value in dict(snapshot.get("entity_refs") or {}).items()
            if key not in _IA_CHAT_SELECTION_STORE_KEYS
        }
    compacted = codex_turn_context.compact_json_structural(
        snapshot,
        max_bytes=_IA_SIDEBAR_CONTEXT_MAX_CHARS,
        priority_paths=(
            "schema_version",
            "surface",
            "route",
            "period",
            "entity_refs",
            "store_mode",
            "multi_store",
            "store",
            "context_kind",
            "selection",
            "filters",
            "metrics",
        ),
    )
    return dict(compacted.value) if isinstance(compacted.value, dict) else {}


def _ia_sidebar_provider_settings(requested_model: str) -> tuple[str, str, str]:
    try:
        config = _carregar_configuracoes_globais()
    except Exception:
        config = {}
    policy = str(config.get("ia_sidebar_response_provider_policy") or "codex_only").strip().lower()
    if policy not in {"codex_only", "codex_then_configured_fallback"}:
        policy = "codex_only"
    configured_chat = _normalizar_ia_modelo_padrao(config.get("ia_modelo_chat") or "codex:gpt-5.5")
    codex_model = configured_chat if _modelo_eh_codex(configured_chat) else "codex:gpt-5.5"
    requested = _normalizar_ia_modelo_padrao(requested_model)
    fallback_model = str(config.get("ia_sidebar_fallback_model") or "").strip()
    if not fallback_model and requested and not _modelo_eh_codex(requested):
        # Legacy model selection remains readable only as an optional fallback.
        fallback_model = requested
    fallback_model = _normalizar_ia_modelo_padrao(fallback_model) if fallback_model else ""
    if _modelo_eh_codex(fallback_model):
        fallback_model = ""
    return policy, codex_model, fallback_model


def _ia_sidebar_call_fallback(payload: IAChatRequest, client_id: str, model: str) -> tuple[str, str]:
    payload.model = model
    if _modelo_eh_vertex_ai(model):
        return _chamar_vertex_ai_chat(payload, client_id), f"vertex:{_vertex_modelo_nome_curto(model) or _vertex_ai_modelo_padrao()}"
    if _modelo_eh_gemini_api(model):
        return _chamar_gemini_chat(payload, client_id), f"gemini:{_gemini_nome_curto(model) or 'gemini-2.5-flash'}"
    if model.startswith("deepseek-"):
        return _chamar_deepseek_chat(payload, client_id), model
    return _chamar_openai_responses(payload, client_id), model


def _ia_sidebar_codex_operational_failure(exc: BaseException) -> bool:
    if isinstance(exc, HTTPException):
        return int(getattr(exc, "status_code", 0) or 0) >= 500
    text = f"{type(exc).__name__}: {exc}".casefold()
    return bool(
        re.search(
            r"(?:operational|temporar|unavailable|indispon|timeout|timed out|rate limit|"
            r"connection|broken pipe|runtime|thread.*(?:expired|not found|invalid))",
            text,
        )
    )


def _ia_chat_selection_store_key(value: Any) -> str:
    ascii_value = "".join(
        char
        for char in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(char)
    ).casefold()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def _ia_chat_selection_context(payload: IAChatRequest) -> dict[str, Any]:
    context = payload.context if isinstance(payload.context, dict) else {}
    clean = dict(context)
    for key in _IA_CHAT_SELECTION_CONTEXT_KEYS:
        clean.pop(key, None)
    return clean


def _ia_chat_selection_authorized_stores(client_id: str) -> tuple[list[str], bool]:
    """Load the tenant store boundary from the server, never from chat context."""

    stores: list[str] = []
    seen: set[str] = set()
    try:
        from backend.services import integracoes

        configured_stores = integracoes.carregar_lojas(str(client_id or "").strip())
    except Exception as exc:
        if logger is not None:
            logger.warning("[IA DATA SELECTION] Lojas autorizadas indisponiveis: %s", type(exc).__name__)
        return [], False
    for raw in list(configured_stores or [])[:100]:
        if not isinstance(raw, dict):
            continue
        store = str(raw.get("nome") or raw.get("name") or "").strip()[:180]
        normalized = store.casefold()
        if not store or normalized in seen:
            continue
        stores.append(store)
        seen.add(normalized)
        if len(stores) >= 50:
            break
    return stores, True


def _ia_chat_selection_anchors(context: dict[str, Any], authorized_stores: list[str]) -> dict[str, Any]:
    selection = context.get("selection") if isinstance(context.get("selection"), dict) else {}
    query_policy = selection.get("query_policy") if isinstance(selection.get("query_policy"), dict) else {}
    if not query_policy and isinstance(context.get("query_policy"), dict):
        query_policy = context.get("query_policy")
    sources = [query_policy, selection, context]

    def first(*keys: str) -> Any:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in keys:
                value = source.get(key)
                if value not in (None, "", [], {}):
                    return value
        return ""

    anchors: dict[str, Any] = {}
    store = str(first("store", "loja", "conta") or "").strip()[:180]
    store_key = _ia_chat_selection_store_key(store)
    authorized_by_key = {_ia_chat_selection_store_key(item): item for item in authorized_stores}
    if store_key and store_key in authorized_by_key:
        anchors["store"] = authorized_by_key[store_key]
    store_mode = str(first("store_mode", "modo_loja") or "").strip().lower()
    multi_store = first("multi_store") is True
    if store_mode == "all" and multi_store:
        anchors["store_mode"] = "all"
        anchors["multi_store"] = True
        anchors.pop("store", None)
    elif store_mode == "single" and anchors.get("store"):
        anchors["store_mode"] = "single"
        anchors["multi_store"] = False
    else:
        anchors["store_mode"] = "none"
        anchors["multi_store"] = False
        anchors.pop("store", None)
    sku = str(first("sku", "seller_sku", "codigo") or "").strip()[:100]
    if sku:
        anchors["sku"] = sku
    item_id = str(first("item_id", "mlb", "id_anuncio") or "").strip().upper()[:60]
    if item_id:
        anchors["item_id"] = item_id
    period = first("period", "periodo")
    if isinstance(period, dict):
        anchors["period"] = {
            key: str(period.get(key) or "").strip()[:30]
            for key in ("start", "end", "data_inicio", "data_fim")
            if str(period.get(key) or "").strip()
        }
    return anchors


def _ia_chat_selection_compact_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "[limite de profundidade]"
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for raw_key, child in list(value.items())[:35]:
            key = str(raw_key or "").strip()[:100]
            if not key or _IA_CHAT_SELECTION_SENSITIVE_KEY_RE.search(key):
                continue
            compact[key] = _ia_chat_selection_compact_value(child, depth=depth + 1)
        return compact
    if isinstance(value, (list, tuple)):
        return [_ia_chat_selection_compact_value(item, depth=depth + 1) for item in list(value)[:8]]
    if isinstance(value, str):
        return value.replace("\x00", "").strip()[:1200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value).replace("\x00", "").strip()[:600]


def _ia_chat_selection_compact_result(tool_id: str, result: Any) -> dict[str, Any]:
    raw = dict(result) if isinstance(result, dict) else {"success": False, "error": str(result or "")}
    raw.setdefault("tool_id", tool_id)
    if tool_id == "context_hub_search":
        try:
            from backend.services.whatsapp import tool_results as whatsapp_tool_results

            rows = whatsapp_tool_results._context_hub_safe_rows(raw)
            generation = whatsapp_tool_results._context_hub_generation(raw, rows)
            return {
                "tool_id": tool_id,
                "success": raw.get("success") is True,
                "records": len(rows),
                "rows": rows[:6],
                "generation": generation,
                "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {},
            }
        except Exception:
            return {
                "tool_id": tool_id,
                "success": False,
                "records": 0,
                "error_code": "context_hub_compaction_failed",
            }
    sanitized = _ia_chat_selection_compact_value(raw)
    sanitized = sanitized if isinstance(sanitized, dict) else {"tool_id": tool_id, "success": False}
    preferred_fields = (
        "tool_id", "success", "records", "source_label", "source", "loja", "store", "sku", "item_id",
        "summary", "totals", "rows", "items", "data", "por_loja", "sources_human", "sources",
        "evidence", "empty_reason", "error_code", "error",
    )
    compact: dict[str, Any] = {}
    omitted = False
    for key in preferred_fields:
        if key not in sanitized:
            continue
        value = sanitized[key]
        candidate = {**compact, key: value}
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded) <= _IA_CHAT_SELECTION_RESULT_MAX_CHARS:
            compact = candidate
            continue
        omitted = True
        if isinstance(value, list):
            accepted: list[Any] = []
            for item in value:
                item_candidate = {**compact, key: [*accepted, item]}
                if len(json.dumps(item_candidate, ensure_ascii=False, separators=(",", ":"), default=str)) > _IA_CHAT_SELECTION_RESULT_MAX_CHARS:
                    break
                accepted.append(item)
            if accepted:
                compact[key] = accepted
        elif isinstance(value, dict):
            accepted_map: dict[str, Any] = {}
            for child_key, child_value in value.items():
                item_candidate = {**compact, key: {**accepted_map, child_key: child_value}}
                if len(json.dumps(item_candidate, ensure_ascii=False, separators=(",", ":"), default=str)) > _IA_CHAT_SELECTION_RESULT_MAX_CHARS:
                    break
                accepted_map[child_key] = child_value
            if accepted_map:
                compact[key] = accepted_map
    compact.setdefault("tool_id", tool_id)
    compact.setdefault("success", raw.get("success") is True)
    if omitted:
        marker_candidate = {**compact, "evidence_omitted_by_budget": True}
        if len(json.dumps(marker_candidate, ensure_ascii=False, separators=(",", ":"), default=str)) <= _IA_CHAT_SELECTION_RESULT_MAX_CHARS:
            compact = marker_candidate
    return compact


def _ia_chat_selection_safe_arguments(arguments: Any) -> Optional[dict[str, Any]]:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            return None
    if not isinstance(arguments, dict):
        return None
    compact = _ia_chat_selection_compact_value(arguments if isinstance(arguments, dict) else {})
    return compact if isinstance(compact, dict) else None


def _ia_chat_selection_permission_catalog(
    client_id: str,
    username: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    if not str(username or "").strip():
        return {}, [], False
    try:
        from backend.services import admin_usuarios_common
        from backend.services.codex.assistant import catalog as assistant_catalog

        permissions = admin_usuarios_common._carregar_permissoes_usuario(username, client_id)
        catalog = []
        for raw_item in assistant_catalog.public_tools(permissions):
            if (
                not isinstance(raw_item, dict)
                or raw_item.get("read_only") is not True
                or not str(raw_item.get("id") or "").strip()
            ):
                continue
            item = dict(raw_item)
            input_schema = dict(item.get("input_schema") or {}) if isinstance(item.get("input_schema"), dict) else {}
            if "properties" not in input_schema:
                input_schema["properties"] = {
                    str(key): {"description": str(description or "")[:300]}
                    for key, description in list(input_schema.items())[:40]
                    if str(key or "").strip()
                }
            item["input_schema"] = input_schema
            catalog.append(item)
        return dict(permissions or {}), catalog, True
    except Exception as exc:
        if logger is not None:
            logger.warning("[IA DATA SELECTION] Catalogo de permissoes indisponivel: %s", type(exc).__name__)
        return {}, [], False


def _ia_chat_selection_unavailable(reason: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return {
        "schema_version": 1,
        "status": "selection_unavailable",
        "action": "unavailable",
        "coverage_complete": False,
        "missing": ["data_selection"],
        "warnings": [str(reason or "data_selection_unavailable")[:100]],
    }, []


def _ia_chat_execute_selected_calls(
    *,
    client_id: str,
    username: str,
    message: str,
    plan: dict[str, Any],
    catalog: list[dict[str, Any]],
    permissions: dict[str, Any],
    authorized_stores: list[str],
    conversation_anchors: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        from backend.services.codex.assistant import execution as assistant_execution
    except Exception:
        return [], ["executor_read_only_indisponivel"]
    allowed_by_id = {
        str(item.get("id") or "").strip(): item
        for item in catalog
        if isinstance(item, dict) and item.get("read_only") is True and str(item.get("id") or "").strip()
    }
    authorized_by_key = {_ia_chat_selection_store_key(item): item for item in authorized_stores}
    entities = plan.get("entities") if isinstance(plan.get("entities"), dict) else {}
    scoped_store = str(entities.get("store_ref") or "").strip()
    scoped_store_key = _ia_chat_selection_store_key(scoped_store)
    if scoped_store_key in authorized_by_key:
        scoped_store = authorized_by_key[scoped_store_key]
    else:
        scoped_store = ""
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    previous_results: list[dict[str, Any]] = []
    planned_calls = [
        dict(item)
        for item in list(plan.get("tool_calls") or [])[:_IA_CHAT_SELECTION_MAX_CALLS]
        if isinstance(item, dict)
    ]
    hub = plan.get("context_hub") if isinstance(plan.get("context_hub"), dict) else {}
    hub_mode = str(hub.get("mode") or "not_applicable")
    if hub_mode in {"optional", "required"}:
        hub_already_planned = any(
            str(item.get("tool_id") or "") == "context_hub_search" for item in planned_calls
        )
        if (
            "context_hub_search" in allowed_by_id
            and not hub_already_planned
            and len(planned_calls) < _IA_CHAT_SELECTION_MAX_CALLS
        ):
            hub_filters = hub.get("filters") if isinstance(hub.get("filters"), dict) else {}
            hub_arguments: dict[str, Any] = {
                "query": str(hub.get("query") or "")[:1000],
                "limit": max(1, min(6, int(hub.get("top_k") or 6))),
            }
            for filter_key, argument_key in (
                ("module", "module"),
                ("source_type", "source_type"),
                ("surface", "environment"),
                ("ids", "ids"),
            ):
                filter_value = hub_filters.get(filter_key)
                if filter_value not in (None, "", [], {}):
                    hub_arguments[argument_key] = filter_value
            planned_calls.append({
                "tool_id": "context_hub_search",
                "arguments": hub_arguments,
                "required": hub_mode == "required",
                "reason": "Context Hub selecionado explicitamente pelo agente de dados.",
                "depends_on": [],
            })
        elif "context_hub_search" not in allowed_by_id:
            warnings.append("context_hub_selecionado_sem_ferramenta_autorizada")
        elif not hub_already_planned:
            warnings.append("context_hub_descartado_por_limite_de_chamadas")
    call_success_by_index: dict[int, bool] = {}
    for call_index, raw_call in enumerate(planned_calls[:_IA_CHAT_SELECTION_MAX_CALLS]):
        if not isinstance(raw_call, dict):
            warnings.append("chamada_invalida_descartada")
            call_success_by_index[call_index] = False
            continue
        tool_id = str(raw_call.get("tool_id") or "").strip()[:100]
        if tool_id not in allowed_by_id:
            warnings.append("ferramenta_fora_da_permissao_descartada")
            call_success_by_index[call_index] = False
            continue
        dependencies = [
            item
            for item in list(raw_call.get("depends_on") or [])[:_IA_CHAT_SELECTION_MAX_CALLS]
            if isinstance(item, int) and 0 <= item < call_index
        ]
        failed_required_dependency = any(
            planned_calls[dependency].get("required") is True
            and call_success_by_index.get(dependency) is not True
            for dependency in dependencies
            if isinstance(planned_calls[dependency], dict)
        )
        if failed_required_dependency:
            warnings.append("chamada_descartada_por_dependencia_obrigatoria")
            results.append({
                "tool_id": tool_id,
                "success": False,
                "records": 0,
                "error_code": "selected_tool_dependency_failed",
                "error": "Consulta nao executada porque uma dependencia obrigatoria falhou.",
                "required": raw_call.get("required") is True,
            })
            call_success_by_index[call_index] = False
            continue
        arguments = _ia_chat_selection_safe_arguments(raw_call.get("arguments"))
        if arguments is None:
            warnings.append("argumentos_invalidos_descartados")
            results.append({
                "tool_id": tool_id,
                "success": False,
                "records": 0,
                "error_code": "selected_tool_arguments_invalid",
                "error": "Consulta nao executada porque os argumentos planejados sao invalidos.",
                "required": raw_call.get("required") is True,
            })
            call_success_by_index[call_index] = False
            continue
        rejected_store = False
        has_store = False
        for key in _IA_CHAT_SELECTION_STORE_KEYS:
            if key not in arguments or not str(arguments.get(key) or "").strip():
                continue
            has_store = True
            requested = _ia_chat_selection_store_key(arguments.get(key))
            if authorized_by_key and requested not in authorized_by_key:
                rejected_store = True
                break
            if requested in authorized_by_key:
                arguments[key] = authorized_by_key[requested]
            elif not authorized_by_key:
                arguments.pop(key, None)
        if rejected_store:
            warnings.append("loja_fora_do_escopo_descartada")
            results.append({
                "tool_id": tool_id,
                "success": False,
                "records": 0,
                "error_code": "selection_store_scope_rejected",
                "error": "Consulta descartada porque a loja nao pertence ao escopo autorizado.",
            })
            call_success_by_index[call_index] = False
            continue
        input_schema = allowed_by_id[tool_id].get("input_schema")
        schema_properties = input_schema.get("properties") if isinstance(input_schema, dict) and isinstance(input_schema.get("properties"), dict) else {}
        supports_store = isinstance(input_schema, dict) and any(
            key in input_schema or key in schema_properties for key in _IA_CHAT_SELECTION_STORE_KEYS
        )
        if not has_store and supports_store and scoped_store:
            arguments["loja"] = scoped_store
        arguments.setdefault("message", str(message or "").strip()[:4000])
        signature = f"{tool_id}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)}"
        if signature in seen:
            warnings.append("chamada_duplicada_descartada")
            call_success_by_index[call_index] = False
            continue
        seen.add(signature)
        try:
            raw_result = assistant_execution.execute_tool_call(
                client_id=str(client_id or "").strip(),
                tool_id=tool_id,
                args=arguments,
                screen_context=conversation_anchors,
                previous_results=previous_results,
                permissions=permissions,
                audit_user=str(username or "").strip(),
            )
        except Exception as exc:
            raw_result = {
                "tool_id": tool_id,
                "success": False,
                "records": 0,
                "error_code": "selected_tool_execution_failed",
                "error": type(exc).__name__,
            }
        compact_result = _ia_chat_selection_compact_result(tool_id, raw_result)
        compact_result["required"] = raw_call.get("required") is True
        results.append(compact_result)
        previous_results.append(compact_result)
        call_success_by_index[call_index] = compact_result.get("success") is True
    return results, warnings


def _ia_chat_prepare_data_selection(
    payload: IAChatRequest,
    client_id: str,
    request: Request,
    username: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Prepare compact, read-only evidence for a system chat request.

    Missing or incompatible selector builds fail closed with an explicit
    unavailable envelope; no provider or implicit screen/RAG/stock context is used.
    """

    if not str(client_id or "").strip():
        return _ia_chat_selection_unavailable("data_selection_tenant_required")

    context = _ia_chat_selection_context(payload)
    if not username:
        username = str(getattr(getattr(request, "state", None), "username", "") or "").strip()
    permissions, catalog, permission_context_ready = _ia_chat_selection_permission_catalog(client_id, username)
    if not permission_context_ready:
        return _ia_chat_selection_unavailable("permission_context_unavailable")
    authorized_stores, store_context_ready = _ia_chat_selection_authorized_stores(client_id)
    anchors = _ia_chat_selection_anchors(context, authorized_stores)
    recent_turns = _ia_chat_resumo_historico(payload.history, limite=6)
    if recent_turns:
        anchors["recent_turns"] = recent_turns
    try:
        from backend.services import codex_data_selection_agent

        runtime = getattr(codex_data_selection_agent, "DATA_SELECTION_RUNTIME", None)
        planner = getattr(runtime, "plan", None)
        if not callable(planner):
            return _ia_chat_selection_unavailable("data_selection_runtime_unavailable")
        raw = planner(
            request_text=str(payload.message or "").strip()[:12000],
            job_prompt=str(payload.message or "").strip()[:12000],
            surface=(
                _IA_SIDEBAR_SURFACE
                if str(context.get("surface") or "") == _IA_SIDEBAR_SURFACE
                else "legacy_ia"
            ),
            allowed_tools=catalog,
            authorized_stores=authorized_stores,
            conversation_anchors=anchors,
            previous_evidence=None,
            data_gap=None,
            model="gpt-5.6-luna",
            reasoning_effort="low",
            max_calls=_IA_CHAT_SELECTION_MAX_CALLS,
        )
    except Exception as exc:
        if logger is not None:
            logger.warning("[IA DATA SELECTION] Seletor indisponivel: %s", type(exc).__name__)
        return _ia_chat_selection_unavailable("data_selection_planning_failed")
    plan = _ia_chat_selection_mapping(raw)
    action = str(plan.get("action") or "").strip()
    if not plan or action not in {"answer_without_data", "clarify", "collect", "mutation_candidate"}:
        return _ia_chat_selection_unavailable("data_selection_invalid_plan")
    if action == "collect" and not store_context_ready:
        return _ia_chat_selection_unavailable("authorized_store_context_unavailable")
    missing = [str(item or "")[:120] for item in list(plan.get("missing_user_fields") or [])[:10] if str(item or "").strip()]
    if action == "collect" and not missing:
        tool_results, execution_warnings = _ia_chat_execute_selected_calls(
            client_id=client_id,
            username=username,
            message=str(payload.message or ""),
            plan=plan,
            catalog=catalog,
            permissions=permissions,
            authorized_stores=authorized_stores,
            conversation_anchors=anchors,
        )
    else:
        tool_results, execution_warnings = [], []
    calls = [item for item in list(plan.get("tool_calls") or [])[:_IA_CHAT_SELECTION_MAX_CALLS] if isinstance(item, dict)]
    hub = plan.get("context_hub") if isinstance(plan.get("context_hub"), dict) else {}
    if str(hub.get("mode") or "") in {"optional", "required"} and not any(
        str(item.get("tool_id") or "") == "context_hub_search" for item in calls
    ) and len(calls) < _IA_CHAT_SELECTION_MAX_CALLS:
        calls.append({
            "tool_id": "context_hub_search",
            "required": str(hub.get("mode") or "") == "required",
            "reason": "Context Hub selecionado explicitamente pelo agente de dados.",
            "depends_on": [],
        })
    required_ids = {str(item.get("tool_id") or "") for item in calls if item.get("required") is True}
    successful_ids = {
        str(item.get("tool_id") or "")
        for item in tool_results
        if item.get("success") is True
    }
    hub_required = str(hub.get("mode") or "") == "required"
    coverage_complete = (
        not missing
        and required_ids.issubset(successful_ids)
        and (not hub_required or "context_hub_search" in successful_ids)
    )
    plan_warnings = [str(item or "")[:300] for item in list(plan.get("warnings") or [])[:10] if str(item or "").strip()]
    envelope = {
        "schema_version": str(plan.get("schema_version") or "")[:100] or 1,
        "status": (
            "awaiting_input" if action == "clarify" or missing
            else "mutation_candidate" if action == "mutation_candidate"
            else "no_data_needed" if action == "answer_without_data"
            else "executed"
        ),
        "action": action,
        "intents": [str(item or "")[:120] for item in list(plan.get("intents") or [])[:8] if str(item or "").strip()],
        "entities": _ia_chat_selection_compact_value(plan.get("entities") or {}),
        "requested_fields": [
            str(item or "")[:120]
            for item in list(plan.get("requested_fields") or [])[:20]
            if str(item or "").strip()
        ],
        "plan": {
            "tool_calls": [
                {
                    "tool_id": str(item.get("tool_id") or "")[:100],
                    "required": item.get("required") is True,
                    "reason": str(item.get("reason") or "")[:500],
                    "depends_on": [
                        dep if isinstance(dep, int) else str(dep or "")[:100]
                        for dep in list(item.get("depends_on") or [])[:6]
                    ],
                }
                for item in calls
            ],
            "context_hub": _ia_chat_selection_compact_value(hub),
            "reason": str(plan.get("reason") or "")[:1200],
            "confidence": plan.get("confidence") if isinstance(plan.get("confidence"), (int, float)) else 0,
        },
        "selected_tools": [str(item.get("tool_id") or "")[:100] for item in calls],
        "evidence": tool_results,
        "coverage_complete": coverage_complete,
        "missing": missing,
        "warnings": list(dict.fromkeys([*plan_warnings, *execution_warnings]))[:12],
    }
    try:
        codex_data_selection_agent.DATA_SELECTION_RUNTIME.record_evidence_size(
            envelope,
            report=any(
                "report" in str(item or "").casefold() or "relatorio" in str(item or "").casefold()
                for item in [*list(plan.get("intents") or []), *list(plan.get("requested_fields") or [])]
            ),
        )
    except Exception:
        pass
    return envelope, tool_results


def ia_chat(payload: IAChatRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    perf_t0 = time.perf_counter()
    contexto = payload.context if isinstance(payload.context, dict) else {}
    mensagem_original = str(payload.message or "").strip()
    surface, conversation_mode, conversation_id = _ia_sidebar_mode_context(payload)
    sidebar_chat = surface == _IA_SIDEBAR_SURFACE
    if sidebar_chat and not conversation_id:
        raise HTTPException(status_code=400, detail="Informe conversation_id para a conversa rapida da barra lateral.")
    screen_snapshot: dict[str, Any] = {}
    conversation_memory: dict[str, Any] = {}
    if sidebar_chat:
        screen_snapshot = _ia_sidebar_screen_context_v2(contexto, mensagem_original)
        contexto = {
            "surface": _IA_SIDEBAR_SURFACE,
            "conversation_mode": conversation_mode or _IA_SIDEBAR_MODE,
            "conversation_id": conversation_id,
            "screen_context": screen_snapshot,
            "store_mode": screen_snapshot.get("store_mode") or "none",
            "multi_store": bool(screen_snapshot.get("multi_store")),
            "store": screen_snapshot.get("store") or "",
            "sku": screen_snapshot.get("sku") or "",
            "item_id": screen_snapshot.get("item_id") or "",
            "period": screen_snapshot.get("period") or {},
            "modo_rapido_sidebar": True,
            "prompt_contract": {
                "prompt_version": _IA_SIDEBAR_PROMPT_VERSION,
                "prompt_hash": hashlib.sha256(_IA_SIDEBAR_PROMPT_VERSION.encode("utf-8")).hexdigest(),
                "schema_version": _IA_SIDEBAR_SCHEMA_VERSION,
            },
        }
        payload.context = contexto
    fallback_read_only = bool(payload.fallback_read_only)
    modo_rapido_sidebar = fallback_read_only or bool(isinstance(contexto, dict) and contexto.get("modo_rapido_sidebar")) or _ia_chat_eh_pedido_rapido_sidebar(
        mensagem_original,
        contexto if isinstance(contexto, dict) else {},
        payload.attachments or [],
    )
    username = _extrair_username_do_request(request)
    nome_usuario = _ia_nome_usuario(client_id, username) if username else ""
    if sidebar_chat and username:
        conversation_memory = _ia_conversas_contexto_conversa(
            client_id,
            username,
            conversation_id,
            modo=conversation_mode or _IA_SIDEBAR_MODE,
            surface=_IA_SIDEBAR_SURFACE,
            limite_mensagens=10,
        )
        server_history = list(conversation_memory.get("recent_messages") or [])[-10:]
        summary = str(conversation_memory.get("summary") or "").strip()
        payload.history = server_history[-10:]
        if screen_snapshot.get("context_kind") == "general":
            payload.message = (
                "A mensagem atual e de contexto geral. Nao herde loja, conta ou escopo comercial de mensagens "
                "anteriores; use-os somente se o usuario os citar explicitamente agora.\n\n"
                f"Mensagem do usuario: {mensagem_original}"
            )
        elif summary:
            payload.message = (
                "Resumo compactado exclusivamente da conversa atual:\n"
                f"{summary[:4000]}\n\n"
                f"Mensagem do usuario: {mensagem_original}"
            )
    conversation_decision = None
    if sidebar_chat:
        conversation_decision = _ia_sidebar_conversation_decision(
            {
                "thread_id": conversation_memory.get("codex_thread_id") or "",
                "prompt_fingerprint": conversation_memory.get("prompt_fingerprint") or "",
                "schema_fingerprint": conversation_memory.get("schema_fingerprint") or "",
                "scope_fingerprint": conversation_memory.get("scope_fingerprint") or "",
                "updated_at": conversation_memory.get("updated_at") or "",
            },
            client_id=client_id,
            username=username,
            conversation_mode=conversation_mode,
            conversation_id=conversation_id,
            screen_snapshot=screen_snapshot,
        )
        if "scope_changed" in conversation_decision.restart_reasons:
            payload.history = []
            if screen_snapshot.get("context_kind") == "general":
                payload.message = (
                    "A mensagem atual e de contexto geral. Nao herde loja, conta ou escopo comercial de mensagens "
                    "anteriores; use-os somente se o usuario os citar explicitamente agora.\n\n"
                    f"Mensagem do usuario: {mensagem_original}"
                )
            else:
                payload.message = mensagem_original
    if username or nome_usuario:
        contexto = dict(contexto)
        contexto["usuario_atual"] = {
            "username": username,
            "nome": nome_usuario or username,
        }
        contexto["nome_usuario"] = nome_usuario or username
        contexto["preferencias_resposta_usuario"] = (
            f"Chame o usuario pelo nome '{nome_usuario or username}' quando for natural, "
            "sem repetir o nome em toda frase. Use apenas a conversa atual para manter continuidade."
        )
        payload.context = contexto

    resumo_historico = _ia_chat_resumo_historico(payload.history, limite=10)
    if resumo_historico:
        contexto = dict(contexto)
        contexto["historico_recente"] = resumo_historico
        payload.context = contexto
    if modo_rapido_sidebar:
        contexto = dict(contexto)
        contexto["modo_rapido_sidebar"] = True
        if fallback_read_only:
            contexto["fallback_read_only"] = True
            contexto["restricao_fallback"] = (
                "Fallback estritamente de leitura: responda apenas em texto; nao execute ferramentas, "
                "nao gere arquivos ou imagens e nao proponha como concluida nenhuma alteracao externa."
            )
        payload.context = contexto

    if fallback_read_only:
        payload.message = (
            "MODO FALLBACK ESTRITAMENTE DE LEITURA. Responda somente em texto. "
            "Nao execute nem alegue ter executado alteracoes, ferramentas, envios, arquivos ou imagens.\n\n"
            f"Pedido do usuario: {mensagem_original}"
        )

    perf_tools_t0 = time.perf_counter()
    selection_envelope: dict[str, Any] = {}
    if fallback_read_only:
        payload.tool_results = []
    else:
        try:
            selection_envelope, selected_results = _ia_chat_prepare_data_selection(
                payload,
                client_id,
                request,
                username=username,
            )
            payload.tool_results = selected_results
            contexto = dict(payload.context or {}) if isinstance(payload.context, dict) else {}
            for selection_key in _IA_CHAT_SELECTION_CONTEXT_KEYS:
                contexto.pop(selection_key, None)
            if selection_envelope:
                contexto["data_selection"] = selection_envelope
            payload.context = contexto
        except Exception as exc:
            logger.warning("[IA DATA SELECTION] Falha ao preparar evidencias: %s", type(exc).__name__)
            payload.tool_results = []
            selection_envelope, _unused_results = _ia_chat_selection_unavailable(
                "data_selection_preparation_failed"
            )
            contexto = dict(payload.context or {}) if isinstance(payload.context, dict) else {}
            for selection_key in _IA_CHAT_SELECTION_CONTEXT_KEYS:
                contexto.pop(selection_key, None)
            contexto["data_selection"] = selection_envelope
            payload.context = contexto
    perf_tools = time.perf_counter() - perf_tools_t0
    modelo_chat_padrao = _ia_modelo_chat_configurado()
    if _usuario_pode_escolher_modelo_chat(request, client_id):
        model_req = str(payload.model or "").strip() or modelo_chat_padrao
    else:
        model_req = modelo_chat_padrao
    model_req = _normalizar_ia_modelo_padrao(model_req)
    provider_policy = "legacy_provider_selection"
    codex_model = model_req
    fallback_model = ""
    if sidebar_chat:
        provider_policy, codex_model, fallback_model = _ia_sidebar_provider_settings(model_req)
        payload.model = codex_model
    else:
        payload.model = model_req
    # Image generation has its own explicit UI/API workflow.  Chat answers must
    # not bypass data selection through the legacy SKU/image helper.
    resposta_imagem = None
    selection_status = str(selection_envelope.get("status") or "")
    selection_direct_response = ""
    if selection_status == "selection_unavailable":
        selection_direct_response = (
            "Nao consegui validar com seguranca quais dados devem ser consultados agora. "
            "Nenhuma fonte foi executada. Tente novamente em instantes."
        )
    elif selection_status == "awaiting_input":
        missing_fields = [
            str(item or "").strip()
            for item in list(selection_envelope.get("missing") or [])[:2]
            if str(item or "").strip()
        ]
        selection_direct_response = (
            "Preciso desta informacao para continuar: " + "; ".join(missing_fields)
            if missing_fields
            else "Preciso de mais detalhes para escolher a consulta correta."
        )
    perf_provider_t0 = time.perf_counter()
    provider_attempts: list[dict[str, Any]] = []
    stored_thread_id = str(conversation_memory.get("codex_thread_id") or "") if sidebar_chat else ""
    thread_id_before = (
        stored_thread_id
        if conversation_decision is not None and conversation_decision.reuse_thread
        else ""
    )
    thread_id_after = thread_id_before
    thread_reset_reason = (
        ",".join(conversation_decision.restart_reasons)
        if conversation_decision is not None and conversation_decision.restart_required
        else ""
    )
    finalization_reason = ""
    if selection_direct_response:
        resposta = selection_direct_response
        model_usado = "data-selection"
        finalization_reason = selection_status
    elif resposta_imagem:
        resposta = resposta_imagem
        model_usado = (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()
        finalization_reason = "image_workflow"
    elif sidebar_chat:
        resposta = ""
        model_usado = f"codex:{_codex_modelo_nome_curto(codex_model)}"
        codex_runner = globals().get("_chamar_codex_chat_com_thread")
        if not callable(codex_runner):
            codex_runner = None
        for attempt in (1, 2):
            try:
                if codex_runner is None:
                    resposta = _chamar_codex_chat(payload, client_id)
                    thread_id_after = ""
                else:
                    resposta, thread_id_after = codex_runner(
                        payload,
                        client_id,
                        thread_id=thread_id_before if attempt == 1 else "",
                        persist_thread=True,
                        conversation_key=(
                            conversation_decision.conversation_key
                            if conversation_decision is not None
                            else f"{client_id}:{username}:{conversation_mode}:{conversation_id}"
                        ),
                    )
                provider_attempts.append({"provider": "codex", "attempt": attempt, "success": True})
                finalization_reason = "codex_completed"
                break
            except Exception as exc:
                operational_failure = _ia_sidebar_codex_operational_failure(exc)
                provider_attempts.append({
                    "provider": "codex",
                    "attempt": attempt,
                    "success": False,
                    "error_type": type(exc).__name__,
                    "operational": operational_failure,
                })
                if attempt == 1 and operational_failure:
                    thread_reset_reason = "codex_operational_failure"
                if not operational_failure:
                    break
        codex_operational_failures = sum(
            1
            for item in provider_attempts
            if item.get("provider") == "codex"
            and item.get("success") is False
            and item.get("operational") is True
        )
        if (
            not resposta
            and provider_policy == "codex_then_configured_fallback"
            and fallback_model
            and codex_operational_failures >= 2
        ):
            try:
                resposta, model_usado = _ia_sidebar_call_fallback(payload, client_id, fallback_model)
                provider_attempts.append({"provider": "configured_fallback", "attempt": 1, "success": True})
                finalization_reason = "configured_fallback_after_two_codex_failures"
            except Exception as exc:
                provider_attempts.append({
                    "provider": "configured_fallback",
                    "attempt": 1,
                    "success": False,
                    "error_type": type(exc).__name__,
                })
        if not resposta:
            resposta = "O servico de IA esta temporariamente indisponivel. Tente novamente em instantes."
            model_usado = "codex:unavailable"
            finalization_reason = "all_configured_providers_failed"
    elif _modelo_eh_codex(model_req):
        resposta = _chamar_codex_chat(payload, client_id)
        model_usado = f"codex:{_codex_modelo_nome_curto(model_req)}"
        finalization_reason = "legacy_codex_completed"
    elif _modelo_eh_vertex_ai(model_req):
        resposta = _chamar_vertex_ai_chat(payload, client_id)
        model_usado = f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
        finalization_reason = "legacy_provider_completed"
    elif _modelo_eh_gemini_api(model_req):
        resposta = _chamar_gemini_chat(payload, client_id)
        model_usado = f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
        finalization_reason = "legacy_provider_completed"
    elif model_req.startswith("deepseek-"):
        resposta = _chamar_deepseek_chat(payload, client_id)
        model_usado = model_req
        finalization_reason = "legacy_provider_completed"
    else:
        resposta = _chamar_openai_responses(payload, client_id)
        model_usado = model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()
        finalization_reason = "legacy_provider_completed"
    perf_provider = time.perf_counter() - perf_provider_t0

    conversa_id = conversation_id if sidebar_chat else str(payload.conversa_id or "").strip()
    modulo = str(payload.modulo or payload.page or "assistente").strip() or "assistente"
    mensagens_conversa = payload.conversa_mensagens if isinstance(payload.conversa_mensagens, list) else None
    if conversa_id and mensagens_conversa is not None:
        try:
            mensagens_norm = []
            for msg in mensagens_conversa:
                role = str((msg or {}).get("role") or "user").strip().lower()
                if role not in ("user", "assistant"):
                    role = "user"
                text = str((msg or {}).get("text") or (msg or {}).get("content") or "").strip()
                if not text:
                    continue
                mensagens_norm.append({"role": role, "text": text[:50000]})

            if not mensagens_norm or mensagens_norm[-1].get("role") != "assistant":
                mensagens_norm.append({"role": "assistant", "text": str(resposta or "")[:50000]})

            titulo = "Conversa"
            for msg in mensagens_norm:
                if msg.get("role") == "user" and str(msg.get("text") or "").strip():
                    titulo = str(msg.get("text") or "").strip()[:60]
                    break

            _ia_conversas_salvar(
                client_id,
                username,
                modulo,
                conversa_id,
                titulo,
                mensagens_norm[-80:],
                modo=conversation_mode if sidebar_chat else "legacy",
                surface=_IA_SIDEBAR_SURFACE if sidebar_chat else "legacy_ia",
                codex_thread_id=thread_id_after if sidebar_chat else "",
                prompt_fingerprint=(
                    conversation_decision.prompt_fingerprint
                    if conversation_decision is not None
                    else ""
                ),
                schema_fingerprint=(
                    conversation_decision.schema_fingerprint
                    if conversation_decision is not None
                    else ""
                ),
                scope_fingerprint=(
                    conversation_decision.scope_fingerprint
                    if conversation_decision is not None
                    else ""
                ),
            )
        except Exception as exc:
            logger.warning(f"[IA] Falha ao salvar conversa no /api/ia/chat: {exc}")

    if sidebar_chat and thread_id_after and thread_id_after != thread_id_before:
        _ia_conversas_atualizar_thread_codex(
            client_id,
            username,
            conversa_id,
            modo=conversation_mode,
            thread_id=thread_id_after,
        )

    perf_total = time.perf_counter() - perf_t0
    if perf_total >= 2.5:
        logger.info(
            "[IA CHAT PERF] model=%s fast=%s tools=%.2fs provider=%.2fs total=%.2fs tools_count=%s page=%s",
            model_usado,
            modo_rapido_sidebar,
            perf_tools,
            perf_provider,
            perf_total,
            len(payload.tool_results or []),
            str(payload.page or "")[:80],
        )

    evidence = list(selection_envelope.get("evidence") or []) if isinstance(selection_envelope, dict) else []
    sources = list(dict.fromkeys(
        str(source or "")[:300]
        for item in evidence
        if isinstance(item, dict)
        for source in list(item.get("sources_human") or item.get("sources") or [])[:8]
        if str(source or "").strip()
    ))[:20]
    context_payload = payload.context if isinstance(payload.context, dict) else {}
    context_size = len(json.dumps(
        {
            "message": str(payload.message or ""),
            "context": context_payload,
            "history": list(payload.history or [])[-12:],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ))
    diagnostics = {
        "surface": _IA_SIDEBAR_SURFACE if sidebar_chat else str(payload.page or "legacy_ia"),
        "conversation_mode": conversation_mode if sidebar_chat else "legacy",
        "conversation_id": conversa_id,
        "prompt_version": _IA_SIDEBAR_PROMPT_VERSION if sidebar_chat else "legacy",
        "prompt_hash": (
            hashlib.sha256(_IA_SIDEBAR_PROMPT_VERSION.encode("utf-8")).hexdigest()
            if sidebar_chat
            else ""
        ),
        "schema_version": _IA_SIDEBAR_SCHEMA_VERSION if sidebar_chat else 1,
        "shared_contract_version": (
            codex_turn_context.CONTRACT_VERSION if sidebar_chat else ""
        ),
        "shared_contract_hash": (
            codex_turn_context.CONTRACT_HASH if sidebar_chat else ""
        ),
        "context_chars": context_size,
        "coverage_complete": selection_envelope.get("coverage_complete") if selection_envelope else None,
        "sources": sources,
        "provider_policy": provider_policy,
        "provider_attempts": provider_attempts,
        "thread_action": (
            conversation_decision.thread_action
            if conversation_decision is not None
            else "legacy"
        ),
        "thread_reused": bool(conversation_decision and conversation_decision.reuse_thread),
        "thread_reset_reason": thread_reset_reason,
        "finalization_reason": finalization_reason,
        "latency_ms": round(perf_total * 1000),
    }
    return {
        "success": True,
        "model": model_usado,
        "resposta": resposta,
        "tool_results": payload.tool_results or [],
        "diagnostico_ia": diagnostics,
    }


async def ia_listar_modelos(request: Request, client_id: str = Depends(get_tenant_id)):
    pode_escolher_modelo = _usuario_pode_escolher_modelo_chat(request, client_id)
    return {
        "success": True,
        "pode_escolher_modelo_chat": pode_escolher_modelo,
        "codex": _listar_modelos_codex_configuraveis() if pode_escolher_modelo else [],
        "openai": [
            {"name": "gpt-5.4-nano", "display_name": "Nano"},
            {"name": "gpt-5.4-mini", "display_name": "Mini"},
            {"name": "gpt-5.4", "display_name": "GPT-5.4"},
            {"name": "gpt-5.5", "display_name": "GPT-5.5"},
        ] if pode_escolher_modelo else [],
        "deepseek": [
            {"name": "deepseek-v4-flash", "display_name": "DeepSeek V4 Flash"},
            {"name": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro"},
        ] if pode_escolher_modelo else [],
        "gemini": _listar_modelos_gemini_api() if pode_escolher_modelo else [],
        "vertex": _listar_modelos_vertex_ai() if pode_escolher_modelo else [],
        "defaults": {
            "sistema": _ia_modelo_padrao_configurado(),
            "perguntas": _ia_modelo_perguntas_configurado(),
            "pos_venda": _ia_modelo_pos_venda_configurado(),
            "chat": _ia_modelo_chat_configurado(),
            "favoritos": _ia_modelo_favoritos_configurado(),
            "favoritos_usar_imagem": _ia_favoritos_usar_imagem_configurado(),
            "openai_ativa": _ia_provedor_ativo("openai"),
            "deepseek_ativa": _ia_provedor_ativo("deepseek"),
            "gemini_ativa": _ia_provedor_ativo("gemini"),
            "vertex_ativa": _ia_provedor_ativo("vertex"),
            "openai": (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip(),
            "deepseek": "deepseek-v4-flash",
            "gemini": "gemini:gemini-2.5-flash",
            "vertex": f"vertex:{_vertex_ai_modelo_padrao()}",
            "codex": "codex:gpt-5.5",
            "vertex_project_id": _vertex_ai_project_id_configurado(),
            "vertex_location": _vertex_ai_location(),
            "vertex_service_account_email": _vertex_ai_service_account_email(),
            "agent_api_key_configurada": bool(_vertex_ai_agent_api_key()),
        },
    }


async def ia_salvar_conversa(payload: IASalvarConversaRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    """Salva uma conversa no servidor."""
    username = _extrair_username_do_request(request)
    sidebar_chat = str(payload.modulo or "").strip().lower() == "global"
    sucesso = _ia_conversas_salvar(
        client_id,
        username,
        payload.modulo,
        payload.conversa_id,
        payload.titulo,
        payload.mensagens,
        modo=_IA_SIDEBAR_MODE if sidebar_chat else "legacy",
        surface=_IA_SIDEBAR_SURFACE if sidebar_chat else "legacy_ia",
    )
    return {
        "success": sucesso,
        "conversa_id": payload.conversa_id,
    }


async def ia_listar_conversas(request: Request, modulo: str = None, client_id: str = Depends(get_tenant_id)):
    """Lista conversas salvas do cliente, opcionalmente filtradas por mÃƒÂ³dulo."""
    username = _extrair_username_do_request(request)
    conversas = _ia_conversas_listar(
        client_id,
        username,
        modulo,
        modo=_IA_SIDEBAR_MODE,
        surface=_IA_SIDEBAR_SURFACE,
    )
    if not conversas:
        conversas = _ia_conversas_listar(client_id, username, modulo, modo="legacy")
    return {
        "success": True,
        "conversas": conversas,
    }


async def ia_carregar_conversa(conversa_id: str, request: Request, client_id: str = Depends(get_tenant_id)):
    """Carrega uma conversa completa com suas mensagens."""
    username = _extrair_username_do_request(request)
    conversa = _ia_conversas_carregar(
        client_id,
        conversa_id,
        username,
        modo=_IA_SIDEBAR_MODE,
        surface=_IA_SIDEBAR_SURFACE,
    )
    if not conversa:
        conversa = _ia_conversas_carregar(client_id, conversa_id, username, modo="legacy")
    if not conversa:
        raise HTTPException(status_code=404, detail="Conversa nÃ£o encontrada")
    return {
        "success": True,
        "conversa": conversa,
    }


async def ia_deletar_conversa(conversa_id: str, request: Request, client_id: str = Depends(get_tenant_id)):
    """Deleta uma conversa e suas mensagens."""
    username = _extrair_username_do_request(request)
    sucesso = _ia_conversas_deletar(client_id, conversa_id, username, modo=_IA_SIDEBAR_MODE)
    if not sucesso:
        sucesso = _ia_conversas_deletar(client_id, conversa_id, username, modo="legacy")
    return {
        "success": sucesso,
        "conversa_id": conversa_id,
    }


async def ia_rag_status(client_id: str = Depends(get_tenant_id)):
    cfg = _ia_rag_config()
    status = {
        **cfg,
        "client_id": client_id,
        "ollama_ok": False,
        "postgres_ok": False,
        "pgvector_ok": False,
    }

    if cfg.get("backend") == "local":
        status.update(_ia_rag_local_status(client_id))
    else:
        try:
            requests.get(f"{cfg['ollama_base_url']}/api/tags", timeout=5).raise_for_status()
            status["ollama_ok"] = True
        except Exception as exc:
            status["ollama_error"] = type(exc).__name__

    try:
        if cfg.get("backend") == "postgres" and psycopg is not None and _ia_rag_pg_dsn():
            with _ia_rag_conectar() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    status["postgres_ok"] = True
                    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS ok")
                    row = cur.fetchone() or {}
                    status["pgvector_ok"] = bool(row.get("ok"))
    except Exception as exc:
        status["postgres_error"] = type(exc).__name__

    with IA_RAG_REINDEX_LOCK:
        status["reindex_active"] = bool(IA_RAG_REINDEX_ACTIVE.get(client_id))
        status["reindex_status"] = dict(IA_RAG_REINDEX_META.get(client_id) or {})

    return status


def _ia_rag_require_full_admin(request: Request, authorization: Optional[str], client_id: str) -> dict:
    from backend.services.codex.console.security import require_full_admin

    sessao = require_full_admin(request, authorization)
    if str(sessao.get("client_id") or "").strip() != str(client_id or "").strip():
        raise HTTPException(status_code=403, detail="Sessao sem acesso ao tenant solicitado.")
    return sessao


async def ia_rag_indexar(
    payload: IARagIndexRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _ia_rag_require_full_admin(request, authorization, client_id)
    if not _ia_rag_legacy_write_enabled():
        raise HTTPException(status_code=403, detail="A gravacao no RAG legado esta desativada.")
    if not _ia_rag_ativo():
        raise HTTPException(
            status_code=503,
            detail="RAG nao configurado. Defina IA_RAG_ENABLED=true e IA_VECTOR_DATABASE_URL no .env."
        )
    try:
        inseridos = _ia_rag_indexar_documentos(client_id, payload.documents or [])
    except PermissionError:
        raise HTTPException(status_code=403, detail="A gravacao no RAG legado esta desativada.")
    except Exception as exc:
        logger.exception(f"[IA RAG] Falha ao indexar documentos: {exc}")
        raise HTTPException(status_code=502, detail="Falha ao indexar documentos no RAG legado.")
    return {"success": True, "inseridos": inseridos}


async def ia_rag_reindexar(
    request: Request,
    payload: IARagReindexRequest = IARagReindexRequest(),
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    _ia_rag_require_full_admin(request, authorization, client_id)
    if not _ia_rag_legacy_write_enabled():
        raise HTTPException(status_code=403, detail="A gravacao no RAG legado esta desativada.")
    if not _ia_rag_ativo():
        raise HTTPException(
            status_code=503,
            detail="RAG nao configurado. Defina IA_RAG_ENABLED=true e IA_VECTOR_DATABASE_URL no .env."
        )

    try:
        info = _ia_rag_iniciar_reindex_async(client_id, force=payload.force)
        return {
            "success": True,
            "background": True,
            **info,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[IA RAG] Falha ao iniciar reindex em background: {exc}")
        raise HTTPException(status_code=502, detail="Falha ao iniciar reindexacao do RAG legado.")


async def servir_imagem_ia(filename: str):
    nome_original = str(filename or "").replace("\\", "/").strip("/")
    nome_seguro = os.path.basename(nome_original)
    if not nome_seguro:
        raise HTTPException(status_code=404, detail="Arquivo de imagem invÃƒÆ’Ã‚Â¡lido")

    candidatos = []
    try:
        for pasta in os.listdir(PASTA_INFO):
            tenant_dir = os.path.join(PASTA_INFO, pasta)
            if os.path.isdir(tenant_dir):
                candidatos.append(os.path.join(tenant_dir, "ia_imagens", nome_seguro))
    except Exception:
        pass

    caminho_arquivo = next((p for p in candidatos if os.path.exists(p)), None)
    if not caminho_arquivo:
        raise HTTPException(status_code=404, detail="Imagem gerada nÃƒÆ’Ã‚Â£o encontrada")

    response = FileResponse(caminho_arquivo, media_type="image/png")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

configure_ia_endpoints_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
    )
]
