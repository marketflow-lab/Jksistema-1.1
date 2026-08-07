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

from .catalog import _assistant_tool_meta
from .runtime import _assistant_texto_norm
_ASSISTANT_SOURCE_LABELS: dict[str, str] = {
    "sales_returns_query": "vendas e devolucoes do JK Sistema",
    "sales_ranking": "historico de vendas do JK Sistema",
    "sales_summary": "resumo de vendas do JK Sistema",
    "returns_summary": "devolucoes do JK Sistema",
    "stockout_forecast": "analise de ruptura de estoque",
    "stale_stock": "estoque parado do JK Sistema",
    "product_data": "cadastro de produtos do JK Sistema",
    "product_registry": "cadastro detalhado de produtos",
    "stock_data": "estoque interno do JK Sistema",
    "product_margin": "cadastro de custo, imposto e margem",
    "product_costs_and_margin": "cadastro de custo, imposto e margem",
    "product_image": "imagens de produto",
    "source_discovery": "lista de arquivos e bases disponiveis",
    "local_database_query": "bancos locais do JK Sistema",
    "local_csv_query": "planilhas e cadastros locais",
    "local_cache_query": "caches locais do JK Sistema",
    "sync_logs_query": "logs e estado de sincronizacao",
    "integrations_status": "status das integracoes cadastradas",
    "mercado_livre_readonly": "dados do Mercado Livre",
    "mercado_livre_listing": "anuncios do Mercado Livre",
    "mercado_livre_resource_query": "catalogo e consultas oficiais do Mercado Livre",
    "mercado_livre_visits": "visitas de anuncio do Mercado Livre",
    "mercado_livre_promotions": "promocoes do Mercado Livre",
    "mercado_livre_post_sale_detail": "conversa de pos-venda do Mercado Livre",
    "mercado_livre_orders": "pedidos e vendas do Mercado Livre",
    "mercado_livre_returns": "devolucoes da API do Mercado Livre",
    "mercado_livre_full_stock": "estoque Full atual da API do Mercado Livre",
    "questions_post_sale_query": "perguntas e pos-venda do Mercado Livre",
    "fiscal_local_query": "cadastro fiscal local",
    "bling_status": "status da integracao Bling",
    "bling_products": "produtos cadastrados na Bling",
    "bling_product": "produtos cadastrados na Bling",
    "bling_positive_stock_sku_count": "contagem de SKUs com estoque na Bling",
    "bling_stock_balances": "saldos de estoque na Bling",
    "bling_deposits": "depositos cadastrados na Bling",
    "bling_sales_orders": "pedidos de venda da Bling",
    "bling_sales_order_detail": "detalhes de pedido da Bling",
    "bling_fiscal_nfe": "notas fiscais na Bling",
    "bling_fiscal_nfe_detail": "detalhes de nota fiscal na Bling",
    "bling_fiscal_product": "dados fiscais do produto na Bling",
    "bling_operation_natures": "naturezas de operacao da Bling",
    "bling_lots": "lotes e validade na Bling",
    "bling_lot_movements": "movimentacoes de lote na Bling",
    "bling_finance_summary": "resumo financeiro da Bling",
    "bling_resource_query": "consulta read-only na Bling",
    "program_functions_catalog": "catalogo de funcoes do JK Sistema",
    "program_action_match": "catalogo de acoes aprovaveis",
    "capability_resolve": "catalogo de capacidades do Black Jhon",
    "operational_memory_query": "memoria operacional do Black Jhon",
    "context_hub_search": "Context Hub tecnico do JK Sistema",
    "operational_dispatcher": "leitores operacionais do JK Sistema",
    "get_integrations_status": "status das integracoes cadastradas",
    "get_stockout_forecast": "analise de ruptura de estoque",
    "get_days_without_sale_top": "estoque parado do JK Sistema",
    "detect_sales_anomalies": "analise de anomalias de vendas",
    "get_returns_by_period": "devolucoes do JK Sistema",
    "get_sales_by_period": "historico de vendas do JK Sistema",
    "_ia_tool_get_sales_by_period": "historico de vendas do JK Sistema",
    "_ia_tool_get_sales_quantity_by_period": "resumo de vendas do JK Sistema",
    "_ia_tool_get_returns_by_period": "devolucoes do JK Sistema",
    "_ia_tool_get_product_data": "cadastro de produtos do JK Sistema",
    "_ia_tool_get_product_registry_info": "cadastro detalhado de produtos",
    "_ia_tool_get_stock_data": "estoque interno do JK Sistema",
    "_ia_tool_get_product_margin": "cadastro de custo, imposto e margem",
    "_ia_tool_get_product_image": "imagens de produto",
    "_ia_tool_get_integrations_status": "status das integracoes cadastradas",
    "_ia_tool_get_stockout_forecast": "analise de ruptura de estoque",
    "_ia_tool_get_days_without_sale_top": "estoque parado do JK Sistema",
}


_ASSISTANT_SOURCE_FILE_LABELS: tuple[tuple[str, str], ...] = (
    ("estoque_historico", "historico de estoque do JK Sistema"),
    ("vendas_historico", "historico de vendas do JK Sistema"),
    ("cadastro_custos_lojas", "cadastro de custos por loja"),
    ("cadastro_produtos", "cadastro de produtos do JK Sistema"),
    ("lojas_config", "configuracao das lojas e integracoes"),
    ("perguntas", "perguntas e pos-venda do Mercado Livre"),
    ("pos_venda", "perguntas e pos-venda do Mercado Livre"),
    ("mercado_livre", "integracao Mercado Livre"),
    ("mercadolivre", "integracao Mercado Livre"),
    ("bling", "integracao Bling"),
    ("sync", "logs e estado de sincronizacao"),
    ("sincronizacao", "logs e estado de sincronizacao"),
    ("fiscal", "cadastro fiscal local"),
    ("imposto", "cadastro fiscal local"),
    ("favoritos", "favoritos e simulador do Mercado Livre"),
    ("full", "modulo Full"),
    ("cache", "caches locais do JK Sistema"),
)


def _assistant_human_tool_label(tool_id: Any) -> str:
    key = str(tool_id or "").strip()
    if not key:
        return "consulta do JK Sistema"
    if key in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[key]
    short = key.rsplit(".", 1)[-1]
    if short in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[short]
    meta = _assistant_tool_meta(short if short != key else key)
    description = str(meta.get("description") or "").strip()
    if description:
        return description[:120]
    return "consulta do JK Sistema"


def _assistant_human_source_label(source: Any, tool_id: Any = "") -> str:
    raw = str(source or "").strip()
    fallback = _assistant_human_tool_label(tool_id)
    if not raw:
        return fallback
    key = raw.rsplit(".", 1)[-1]
    if raw in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[raw]
    if key in _ASSISTANT_SOURCE_LABELS:
        return _ASSISTANT_SOURCE_LABELS[key]
    norm = _assistant_texto_norm(raw).replace("\\", "/")
    for needle, label in _ASSISTANT_SOURCE_FILE_LABELS:
        if needle in norm:
            return label
    if re.search(r"\b(select|sqlite|\.db)\b", norm):
        return "bancos locais do JK Sistema"
    if ".csv" in norm:
        return "planilhas e cadastros locais"
    if ".json" in norm:
        return "arquivos locais do JK Sistema"
    if re.match(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?$", raw, flags=re.I):
        return fallback
    return raw[:160]


def _assistant_human_source_list(sources: Any, tool_id: Any = "") -> list[str]:
    labels: list[str] = []
    if not isinstance(sources, list):
        sources = [sources] if sources else []
    for source in sources:
        if isinstance(source, dict):
            label = str(source.get("source_label") or source.get("function_label") or source.get("label") or "").strip()
            if not label:
                label = _assistant_human_source_label(
                    source.get("source") or source.get("function") or source.get("tool_id") or "",
                    source.get("tool_id") or tool_id,
                )
        else:
            label = _assistant_human_source_label(source, tool_id)
        if label and label not in labels:
            labels.append(label)
    return labels


def _assistant_human_fallback_list(tool_ids: Any) -> list[str]:
    labels: list[str] = []
    if not isinstance(tool_ids, list):
        return labels
    for tool_id in tool_ids:
        label = _assistant_human_tool_label(tool_id)
        if label and label not in labels:
            labels.append(label)
    return labels


def _assistant_source_display(src: Any) -> str:
    if isinstance(src, dict):
        return str(
            src.get("source_label")
            or src.get("function_label")
            or _assistant_human_source_label(src.get("source") or src.get("function") or src.get("tool_id") or "", src.get("tool_id") or "")
        ).strip()
    return _assistant_human_source_label(src)


def _assistant_humanize_source_text(value: Any) -> str:
    text = str(value if value is not None else "")
    if not text:
        return ""
    for key in sorted(_ASSISTANT_SOURCE_LABELS, key=len, reverse=True):
        label = _ASSISTANT_SOURCE_LABELS[key]
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", label, text)
    text = text.replace("ferramenta principal", "consulta principal")
    text = text.replace("fallbacks read-only", "consultas alternativas de leitura")
    text = text.replace("fallback read-only", "consulta alternativa de leitura")
    return text
