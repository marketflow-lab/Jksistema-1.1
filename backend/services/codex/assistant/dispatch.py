"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage, codex_turn_context
from backend.services.favoritos_margem import margem_calcular_anuncio, margem_formatar_moeda, margem_parse_float
from backend.services.whatsapp import intent as whatsapp_intent

from .runtime import _assistant_call_ia_tool, _assistant_context_page, _assistant_periodo_padrao, _assistant_texto_norm

def _assistant_direct_tools(client_id: str, mode: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    inicio_30, fim = _assistant_periodo_padrao(30)
    inicio_90, fim_90 = _assistant_periodo_padrao(90)
    fim_date = date.today()
    atual_inicio = (fim_date - timedelta(days=29)).isoformat()
    atual_fim = fim_date.isoformat()
    anterior_fim_date = fim_date - timedelta(days=30)
    anterior_inicio = (anterior_fim_date - timedelta(days=29)).isoformat()
    anterior_fim = anterior_fim_date.isoformat()
    estoque_limite = 100 if mode in {"daily", "report"} else 50
    parado_limite = 500 if mode in {"daily", "report"} else 60

    candidates = [
        _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, None),
        _assistant_call_ia_tool("_ia_tool_get_sales_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_returns_by_period", client_id, inicio_30, fim, None, 10),
        _assistant_call_ia_tool("_ia_tool_get_stockout_forecast", client_id, "previsao ruptura estoque", None, None, 30, estoque_limite),
        _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, None, parado_limite, True, False, True),
    ]
    if mode in {"daily", "report"}:
        candidates.extend(
            [
                _assistant_call_ia_tool("_ia_tool_get_sales_quantity_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_returns_quantity_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_return_rate_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_profit_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_detect_sales_anomalies", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_avg_ticket_by_period", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_sales_timeseries", client_id, inicio_90, fim_90, None),
                _assistant_call_ia_tool("_ia_tool_get_period_comparison", client_id, anterior_inicio, anterior_fim, atual_inicio, atual_fim, None),
                _assistant_call_ia_tool("_ia_tool_get_mercado_livre_listing", client_id, "listar anuncios ativos mercado livre", None, None, 20),
            ]
        )
    for item in candidates:
        if isinstance(item, dict):
            results.append(item)
    return results


def _assistant_prompt_queries(message: str, mode: str) -> list[str]:
    text = _assistant_texto_norm(message)
    queries = []
    if str(message or "").strip():
        queries.append(str(message or "").strip())
    wants_broad = bool(re.search(r"\b(analise|analisar|relatorio|relatorio|oportunidade|melhoria|melhorias|vendas|estoque|devolucao|devolucoes|ruptura|margem|lucro)\b", text))
    wants_external = any(word in text for word in ("mercado livre", "mercadolivre", "bling", "anuncio", "integracao", "integracoes"))
    if wants_external:
        queries.append("status das integracoes Mercado Livre e Bling; consultar Mercado Livre ou Bling quando houver SKU, item ou produto informado.")
    if wants_broad or mode in {"daily", "report"}:
        queries.append(
            "analise especialista de vendas, estoque, devolucoes, margem, lucro, anomalias, ruptura de estoque "
            "e produtos com estoque sem venda nos ultimos 30 dias."
        )
    if mode == "proactive":
        queries.append(
            "verificar alertas operacionais leves: integracoes desconectadas, anomalias de vendas, devolucoes, "
            "previsao de ruptura e produtos com estoque sem venda."
        )
    return queries[:4]


def _assistant_execute_dispatcher(client_id: str, message: str, screen_context: Any) -> tuple[list[dict[str, Any]], str, list[str]]:
    warnings: list[str] = []
    try:
        from backend.schemas.ia import IAChatRequest
        from backend.services import ia as ia_service

        contexto = dict(screen_context) if isinstance(screen_context, dict) else {}
        page = _assistant_context_page(contexto)
        payload = IAChatRequest(
            message=str(message or ""),
            page=page,
            modulo=page,
            context=contexto,
            history=[],
        )
        tool_results = ia_service._ia_chat_executar_funcoes(payload, client_id)
        payload.tool_results = tool_results
        tool_context = ia_service._ia_chat_contexto_funcoes(payload, client_id) if tool_results else ""
        return list(tool_results or []), str(tool_context or ""), warnings
    except Exception as exc:
        warnings.append(str(exc)[:300])
        return [], "", warnings


def _assistant_dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    clean: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        key_raw = json.dumps(
            {
                "function": item.get("function"),
                "arguments": item.get("arguments"),
                "result": item.get("result"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )[:5000]
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean
