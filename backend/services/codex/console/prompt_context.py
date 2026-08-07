"""Codex console prompt context component."""

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


def _codex_whatsapp_query_policy(origin: Any, channel_metadata: Any) -> dict[str, Any]:
    if str(origin or "").strip().lower() != "whatsapp" or not isinstance(channel_metadata, dict):
        return {}
    raw = channel_metadata.get("query_policy")
    if not isinstance(raw, dict) or str(raw.get("mode") or "").strip().lower() != "query_only":
        return {}
    allowed_domains = {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    domains = [
        str(item or "").strip().lower()
        for item in (raw.get("domains") or [])
        if str(item or "").strip().lower() in allowed_domains
    ]
    if not domains:
        return {}
    policy: dict[str, Any] = {
        "mode": "query_only",
        "domains": list(dict.fromkeys(domains)),
        "read_only": True,
        "deny_approval": True,
        "store_required": bool(raw.get("store_required")),
        "store": _codex_clean_text(str(raw.get("store") or ""), 160),
    }
    raw_store_mode = str(raw.get("store_mode") or "").strip().lower()
    raw_stores = [
        _codex_clean_text(str(item or ""), 160)
        for item in (raw.get("stores") or [])
        if str(item or "").strip()
    ]
    raw_stores = list(dict.fromkeys(item for item in raw_stores if item))[:20]
    if raw_store_mode == "all" and raw_stores:
        policy["store_mode"] = "all"
        policy["stores"] = raw_stores
    source_raw = raw.get("source_policy") if isinstance(raw.get("source_policy"), dict) else {}
    allowed_source_tools = {
        "bling_stock_balances",
        "mercado_livre_listing",
        "mercado_livre_orders",
        "mercado_livre_returns",
        "mercado_livre_full_stock",
    }
    required_tools = [
        str(item or "").strip()
        for item in (source_raw.get("required_tools") or [])
        if str(item or "").strip() in allowed_source_tools
    ]
    forbidden_tools = [
        str(item or "").strip()
        for item in (source_raw.get("forbidden_tools") or [])
        if str(item or "").strip() in {
            "bling_stock_balances", "bling_deposits", "stock_data", "product_data",
            "bling_sales_orders", "mercado_livre_listing", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_full_stock",
        }
    ]
    if required_tools:
        policy["source_policy"] = {
            "version": _codex_clean_text(str(source_raw.get("version") or ""), 80),
            "intent": _codex_clean_text(str(source_raw.get("intent") or ""), 240),
            "required_tools": list(dict.fromkeys(required_tools)),
            "forbidden_tools": list(dict.fromkeys(forbidden_tools)),
            "preferred_providers": [
                str(item or "").strip()
                for item in (source_raw.get("preferred_providers") or [])
                if str(item or "").strip() in {"bling", "mercado_livre"}
            ],
            "force_refresh": bool(source_raw.get("force_refresh")),
            "include_listing_details": bool(source_raw.get("include_listing_details")),
            "sum_requested": bool(source_raw.get("sum_requested")),
            "full_exclusive": bool(source_raw.get("full_exclusive")),
            "full_stock_provider": "mercado_livre_api_only",
            "bling_stock_scope": "exclude_full",
            "aggregation_policy": _codex_clean_text(str(source_raw.get("aggregation_policy") or ""), 120),
        }
    if raw.get("base_request"):
        policy["base_request"] = _codex_clean_text(str(raw.get("base_request") or ""), 2000)
    if str(raw.get("pagination") or "") == "next":
        policy["pagination"] = "next"
    if raw.get("inherited") is True:
        policy["inherited"] = True
        policy["base_request"] = _codex_clean_text(str(raw.get("base_request") or ""), 2000)
    if raw.get("fresh") is True or raw.get("bypass_cache") is True:
        policy["fresh"] = True
        policy["bypass_cache"] = True
    if raw.get("report_mode") is True and "mercado_livre_orders" in required_tools:
        policy["report_mode"] = True
    try:
        if raw.get("offset") is not None:
            policy["offset"] = max(0, min(int(raw.get("offset")), 100000))
    except Exception:
        pass
    try:
        if raw.get("limit") is not None:
            maximum = 20000 if policy.get("report_mode") is True else 100
            policy["limit"] = max(1, min(int(raw.get("limit")), maximum))
    except Exception:
        pass
    return policy


def _codex_task_whatsapp_query_only(task: Any) -> bool:
    if not isinstance(task, dict) or str(task.get("origin") or "").strip().lower() != "whatsapp":
        return False
    if task.get("whatsapp_query_only") is True:
        return True
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return bool(_codex_whatsapp_query_policy("whatsapp", metadata))


def _codex_clean_text(value: Optional[str], limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _codex_texto_sem_acentos(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _codex_prompt_pede_alteracao(prompt: str) -> bool:
    text = _codex_texto_sem_acentos(prompt)
    if re.search(r"\b(responda|responder|envie|enviar|mande|mandar|aprove|aprovar)\b", text) and re.search(
        r"\b(pergunta|pos venda|pos-venda|resposta|mensagem (?:ao|para o) comprador|mercado livre|mercadolivre)\b",
        text,
    ):
        return True
    if re.search(r"\b(execute|executar|rode|rodar|cancele|cancelar|pause|pausar|publique|publicar)\b", text):
        return True
    patterns = (
        r"\b(altere|alterar|alteracao|ajuste|ajustar|corrija|corrigir|correcao)\b",
        r"\b(implemente|implementar|crie|criar|adicione|adicionar|inclua|incluir)\b",
        r"\b(edite|editar|modifique|modificar|troque|trocar|substitua|substituir)\b",
        r"\b(remova|remover|apague|apagar|delete|deletar|exclua|excluir)\b",
        r"\b(salve|salvar|grave|gravar|atualize|atualizar|sincronize|sincronizar)\b",
        r"\b(instale|instalar|publique|publicar|gere arquivo|gerar arquivo)\b",
        r"\b(change|edit|fix|implement|create|add|remove|delete|update|save|write|modify|patch)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _codex_normalizar_screen_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    allowed_keys = {
        "title",
        "url",
        "url_completa",
        "pathname",
        "modulo_atual",
        "periodo",
        "filtros",
        "cards",
        "table_headers",
        "table_rows",
        "table",
        "listas",
        "visible_text",
        "controls",
        "selection",
        "viewport",
        "store",
        "store_mode",
        "multi_store",
    }

    def trim(obj: Any, depth: int = 0) -> Any:
        if depth > 4:
            return ""
        if isinstance(obj, str):
            return obj.strip()[:4000]
        if isinstance(obj, (int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, list):
            return [trim(item, depth + 1) for item in obj[:60]]
        if isinstance(obj, dict):
            clean: dict[str, Any] = {}
            for key, item in list(obj.items())[:80]:
                clean[str(key)[:80]] = trim(item, depth + 1)
            return clean
        return str(obj)[:1000]

    screen_context = {
        key: trim(value.get(key))
        for key in allowed_keys
        if key in value and value.get(key) not in (None, "", [], {})
    }
    compacted = codex_turn_context.compact_json_structural(
        screen_context,
        max_bytes=18000,
        priority_paths=(
            "modulo_atual",
            "pathname",
            "title",
            "selection",
            "periodo",
            "filtros",
            "table_headers",
            "table_rows",
            "cards",
            "controls",
            "visible_text",
        ),
    )
    return dict(compacted.value) if isinstance(compacted.value, dict) else {}





def _codex_screen_context_json(screen_context: Any, limit: int = 18000) -> str:
    if not isinstance(screen_context, dict) or not screen_context:
        return ""
    return codex_turn_context.bounded_json(
        screen_context,
        max_bytes=max(2, int(limit or 0)),
        priority_paths=(
            "modulo_atual",
            "pathname",
            "selection",
            "periodo",
            "filtros",
            "table_headers",
            "table_rows",
            "cards",
            "controls",
            "visible_text",
        ),
    )





def _codex_app_data_context(
    prompt: str,
    screen_context: Any,
    client_id: str,
    permissions: Any = None,
) -> dict[str, Any]:
    if not _codex_bool_env("JK_CODEX_APP_DATA_CONTEXT_ENABLED", True):
        return {}
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("full") is not True:
        return {
            "enabled": True,
            "permission_filtered": True,
            "tool_results_count": 0,
            "tool_context": "",
        }
    mensagem = str(prompt or "").strip()
    tenant = str(client_id or "").strip()
    if not mensagem or not tenant:
        return {}
    try:
        from backend.services.codex.assistant import answers as assistant_answers

        return assistant_answers.codex_assistant_collect_context(
            mensagem,
            screen_context if isinstance(screen_context, dict) else {},
            tenant,
            mode="chat",
        )
    except Exception:
        pass
    try:
        from backend.schemas.ia import IAChatRequest
        from backend.services import ia as ia_service

        contexto = screen_context if isinstance(screen_context, dict) else {}
        page = (
            str(contexto.get("modulo_atual") or "").strip()
            or str(contexto.get("pathname") or "").strip().strip("/").split("/", 1)[0]
            or str(contexto.get("title") or "").strip()
            or "codex"
        )
        payload = IAChatRequest(
            message=mensagem,
            page=page,
            modulo=page,
            context=dict(contexto),
            history=payload.history if isinstance(payload.history, list) else [],
        )
        tool_results = ia_service._ia_chat_executar_funcoes(payload, tenant)
        if not tool_results:
            return {"enabled": True, "tool_results_count": 0, "tool_context": ""}
        payload.tool_results = tool_results
        tool_context = ia_service._ia_chat_contexto_funcoes(payload, tenant)
        return {
            "enabled": True,
            "tool_results_count": len(tool_results),
            "tool_context": str(tool_context or "")[:24000],
            "tool_results_preview": codex_turn_context.bounded_json(
                tool_results,
                max_bytes=24000,
                priority_paths=("field", "value", "source", "coverage", "status", "reference"),
            ),
        }
    except Exception as exc:
        return {
            "enabled": True,
            "tool_results_count": 0,
            "tool_context": "",
            "error": str(exc)[:600],
        }





def _codex_estimar_tokens(text: str) -> int:
    return max(0, ceil(len(str(text or "")) / 4))


def _codex_context_stats(prompt: str, screen_context: Any, app_data_context: Any = None) -> dict[str, Any]:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    if isinstance(app_data_context, dict):
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "")
    run_prompt = _codex_prompt_com_contexto_tela(prompt, screen_context, app_data_context)
    visible_text = ""
    controls_count = 0
    table_rows_count = 0
    filtros_count = 0
    listas_count = 0
    app_tool_results_count = 0
    if isinstance(screen_context, dict):
        visible_text = str(screen_context.get("visible_text") or "")
        controls_count = len(screen_context.get("controls") or []) if isinstance(screen_context.get("controls"), list) else 0
        table_rows_count = len(screen_context.get("table_rows") or []) if isinstance(screen_context.get("table_rows"), list) else 0
        filtros_count = len(screen_context.get("filtros") or []) if isinstance(screen_context.get("filtros"), list) else 0
        listas_count = len(screen_context.get("listas") or []) if isinstance(screen_context.get("listas"), list) else 0
    if isinstance(app_data_context, dict):
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    return {
        "prompt_chars": len(str(prompt or "")),
        "screen_context_chars": len(context_json),
        "screen_context_bytes": len(context_json.encode("utf-8")) if context_json else 0,
        "app_data_context_chars": len(app_data_text),
        "app_data_context_bytes": len(app_data_text.encode("utf-8")) if app_data_text else 0,
        "app_tool_results_count": app_tool_results_count,
        "visible_text_chars": len(visible_text),
        "controls_count": controls_count,
        "table_rows_count": table_rows_count,
        "filtros_count": filtros_count,
        "listas_count": listas_count,
        "run_prompt_chars": len(run_prompt),
        "estimated_input_tokens": _codex_estimar_tokens(run_prompt),
        "estimated_context_tokens": _codex_estimar_tokens(context_json),
        "estimated_app_data_tokens": _codex_estimar_tokens(app_data_text),
    }


def _codex_prompt_com_contexto_tela(prompt: str, screen_context: Any, app_data_context: Any = None) -> str:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    app_data_error = ""
    app_tool_results_count = 0
    app_data_enabled = False
    if isinstance(app_data_context, dict):
        app_data_enabled = bool(app_data_context.get("enabled"))
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "").strip()
        app_data_error = str(app_data_context.get("error") or "").strip()
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    parts: list[str] = []
    if context_json:
        parts.append(
            "Contexto da tela atual do JK Sistema, capturado no momento em que o usuario enviou a mensagem:\n"
            f"{context_json}\n\n"
            "Use esse contexto como a tela que o usuario esta vendo agora. "
            "Se o usuario mencionar 'a pergunta', 'essa pergunta', 'a tela', 'isso' ou algo semelhante, "
            "responda usando os dados visiveis nesse contexto. "
            "Quando houver varias perguntas visiveis, priorize a linha selecionada; se nao houver selecao, priorize a primeira pergunta nao respondida ou a primeira pergunta visivel."
        )
    if app_data_text:
        parts.append(
            "Resultados de consultas internas read-only do JK Sistema, executadas pelo backend com dados reais antes do Codex responder:\n"
            f"{app_data_text}\n\n"
            "Para perguntas de negocio, vendas, estoque, produtos, Mercado Livre, Bling, devolucoes, integracoes ou relatorios, "
            "priorize estes resultados estruturados em vez de inferir pela tela. "
            "Se os resultados nao cobrirem a pergunta, diga exatamente qual dado esta faltando."
        )
    elif app_data_enabled and app_tool_results_count == 0:
        parts.append(
            "As ferramentas internas de leitura do JK Sistema nao retornaram resultados estruturados para esta mensagem. "
            "Use o contexto da tela e, se necessario, explique quais dados faltam para uma resposta exata."
        )
    if app_data_error:
        parts.append(f"Aviso: houve falha ao preparar consultas internas read-only: {app_data_error}")
    if not parts:
        return prompt
    return (
        "\n\n".join(parts)
        + "\n\n"
        "Mensagem do usuario:\n"
        f"{prompt}"
    )


def _codex_context_stats_from_prompt(
    prompt: str,
    run_prompt: str,
    screen_context: Any,
    app_data_context: Any = None,
    conversation_context: Any = None,
) -> dict[str, Any]:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    app_tool_results_count = 0
    if isinstance(app_data_context, dict):
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "")
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    visible_text = ""
    controls_count = 0
    table_rows_count = 0
    filtros_count = 0
    listas_count = 0
    if isinstance(screen_context, dict):
        visible_text = str(screen_context.get("visible_text") or "")
        controls_count = len(screen_context.get("controls") or []) if isinstance(screen_context.get("controls"), list) else 0
        table_rows_count = len(screen_context.get("table_rows") or []) if isinstance(screen_context.get("table_rows"), list) else 0
        filtros_count = len(screen_context.get("filtros") or []) if isinstance(screen_context.get("filtros"), list) else 0
        listas_count = len(screen_context.get("listas") or []) if isinstance(screen_context.get("listas"), list) else 0
    estimated = _codex_estimar_tokens(run_prompt)
    history_chars = 0
    estimated_history_tokens = 0
    conversation_id = ""
    conversation_compacted = False
    if isinstance(conversation_context, dict):
        history_chars = int(conversation_context.get("history_chars") or 0)
        estimated_history_tokens = int(conversation_context.get("estimated_history_tokens") or 0)
        conversation_id = str(conversation_context.get("conversation_id") or "")
        conversation_compacted = bool(conversation_context.get("compacted"))
    return {
        "prompt_chars": len(str(prompt or "")),
        "screen_context_chars": len(context_json),
        "screen_context_bytes": len(context_json.encode("utf-8")) if context_json else 0,
        "app_data_context_chars": len(app_data_text),
        "app_data_context_bytes": len(app_data_text.encode("utf-8")) if app_data_text else 0,
        "app_tool_results_count": app_tool_results_count,
        "visible_text_chars": len(visible_text),
        "controls_count": controls_count,
        "table_rows_count": table_rows_count,
        "filtros_count": filtros_count,
        "listas_count": listas_count,
        "run_prompt_chars": len(run_prompt),
        "estimated_input_tokens": estimated,
        "estimated_context_tokens": _codex_estimar_tokens(context_json),
        "estimated_app_data_tokens": _codex_estimar_tokens(app_data_text),
        "history_chars": history_chars,
        "estimated_history_tokens": estimated_history_tokens,
        "conversation_id": conversation_id,
        "conversation_compacted": conversation_compacted,
        "agent_mode": True,
        "context_soft_limit": CODEX_AGENT_INPUT_TOKEN_SOFT_LIMIT,
        "context_target": CODEX_AGENT_INPUT_TOKEN_TARGET,
    }


def _codex_agent_json(value: Any, limit: int = 60000, indent: Optional[int] = 2) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=indent)
    if len(text.encode("utf-8")) <= limit:
        return text
    return codex_turn_context.bounded_json(
        value,
        max_bytes=max(2, int(limit or 0)),
        priority_paths=(
            "request",
            "user_message",
            "scope",
            "selection",
            "facts",
            "sources",
            "gaps",
            "missing",
            "records",
            "history",
        ),
    )





def _codex_agent_screen_summary(screen_context: Any) -> dict[str, Any]:
    if not isinstance(screen_context, dict) or not screen_context:
        return {}

    def trim(value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return str(value)[:160]
        if isinstance(value, str):
            return value.strip()[:1600]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [trim(item, depth + 1) for item in value[:20]]
        if isinstance(value, dict):
            return {str(key)[:80]: trim(item, depth + 1) for key, item in list(value.items())[:40]}
        return str(value)[:600]

    summary = {
        "title": screen_context.get("title") or "",
        "pathname": screen_context.get("pathname") or "",
        "modulo_atual": screen_context.get("modulo_atual") or "",
        "periodo": trim(screen_context.get("periodo")),
        "filtros": trim(screen_context.get("filtros")),
        "selection": trim(screen_context.get("selection")),
        "cards": trim(screen_context.get("cards")),
        "table_headers": trim(screen_context.get("table_headers")),
        "table_rows_preview": trim(screen_context.get("table_rows")),
        "controls_preview": trim(screen_context.get("controls")),
        "visible_text_preview": trim(screen_context.get("visible_text")),
    }
    summary = {key: value for key, value in summary.items() if value not in (None, "", [], {})}
    raw = json.dumps(summary, ensure_ascii=False, default=str)
    if len(raw) <= CODEX_AGENT_SCREEN_CONTEXT_LIMIT:
        return summary
    return {
        "truncated": True,
        "title": summary.get("title") or "",
        "pathname": summary.get("pathname") or "",
        "modulo_atual": summary.get("modulo_atual") or "",
        "preview": raw[:CODEX_AGENT_SCREEN_CONTEXT_LIMIT],
    }


def _codex_agent_source_policy_from_screen(screen_context: Any) -> dict[str, Any]:
    if not isinstance(screen_context, dict):
        return {}
    selection = screen_context.get("selection") if isinstance(screen_context.get("selection"), dict) else {}
    query_policy = selection.get("query_policy") if isinstance(selection.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    return dict(source_policy)


def _codex_agent_tool_catalog(
    permissions: Any = None,
    *,
    read_only_only: bool = False,
    source_policy: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    try:
        from backend.services.codex.assistant import catalog as assistant_catalog

        tools = assistant_catalog.public_tools(permissions)
    except Exception:
        tools = []
    source_policy = source_policy if isinstance(source_policy, dict) else {}
    forbidden_tools = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    compact: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if read_only_only and (
            tool.get("read_only") is not True
            or str(tool.get("id") or "") in {"program_action_match", "operational_dispatcher"}
        ):
            continue
        if str(tool.get("id") or "") in forbidden_tools:
            continue
        compact.append(
            {
                "id": tool.get("id") or "",
                "module": tool.get("module") or "",
                "description": str(tool.get("description") or "")[:260],
                "input_schema": tool.get("input_schema") or {},
                "external": bool(tool.get("external")),
                "fallbacks": list(tool.get("fallbacks") or [])[:6],
                "read_only": bool(tool.get("read_only", True)),
            }
        )
    return compact





def _codex_agent_capability_catalog(
    client_id: str = "",
    permissions: Any = None,
    *,
    read_only_only: bool = False,
) -> dict[str, Any]:
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("full") is not True:
        return {
            "version": "permission-filtered",
            "total_capabilities": 0,
            "modules": [],
            "capabilities": [],
        }
    try:
        return codex_capabilities.compact_capability_catalog(
            client_id=str(client_id or ""),
            limit=80,
            read_only_only=read_only_only,
        )
    except Exception as exc:
        return {
            "version": "unavailable",
            "total_capabilities": 0,
            "modules": [],
            "error": str(exc),
        }


def _codex_agent_is_report_request(prompt: str) -> bool:
    text = _codex_texto_sem_acentos(prompt)
    return bool(re.search(r"\b(relatorio|analise completa|diagnostico|ultimos?\s+\d+\s+dias?)\b", text))

def _codex_prompt_pede_desenvolvimento(prompt: str) -> bool:
    """Distingue alteracao de codigo de uma acao comercial tipada do app."""

    text = _codex_texto_sem_acentos(prompt)
    development_objects = (
        r"\b(codigo|codebase|repositorio|repository|branch|worktree|commit|pull request|pr)\b",
        r"\b(arquivo|file|pasta|diretorio|source|fonte)\b.*\b(py|python|js|javascript|ts|html|css|toml|yaml|json|md)\b",
        r"\b(terminal|shell|powershell|cmd|bash|comando)\b",
        r"\b(endpoint|rota|router|backend|frontend|fastapi|electron|modulo)\b",
        r"\b(teste|testes|pytest|npm|build|instalador|release)\b",
    )
    development_actions = (
        r"\b(altere|alterar|ajuste|ajustar|corrija|corrigir|implemente|implementar)\b",
        r"\b(crie|criar|adicione|adicionar|edite|editar|modifique|modificar)\b",
        r"\b(remova|remover|apague|apagar|delete|deletar|refatore|refatorar)\b",
        r"\b(execute|executar|rode|rodar|instale|instalar|publique|publicar)\b",
        r"\b(change|edit|fix|implement|create|add|remove|delete|update|write|patch|refactor)\b",
    )
    return any(re.search(pattern, text) for pattern in development_objects) and any(
        re.search(pattern, text) for pattern in development_actions
    )
__codex_dependencies__ = ['CODEX_AGENT_INPUT_TOKEN_SOFT_LIMIT', 'CODEX_AGENT_INPUT_TOKEN_TARGET', 'CODEX_AGENT_SCREEN_CONTEXT_LIMIT', '_codex_bool_env']

__codex_exports__ = ['_codex_whatsapp_query_policy', '_codex_task_whatsapp_query_only', '_codex_clean_text', '_codex_texto_sem_acentos', '_codex_prompt_pede_alteracao', '_codex_normalizar_screen_context', '_codex_screen_context_json', '_codex_app_data_context', '_codex_estimar_tokens', '_codex_context_stats', '_codex_prompt_com_contexto_tela', '_codex_context_stats_from_prompt', '_codex_agent_json', '_codex_agent_screen_summary', '_codex_agent_source_policy_from_screen', '_codex_agent_tool_catalog', '_codex_agent_capability_catalog', '_codex_agent_is_report_request', '_codex_prompt_pede_desenvolvimento']
