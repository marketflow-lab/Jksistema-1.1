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

from .dispatch import _assistant_execute_dispatcher
from .margin_analysis import _assistant_product_costs_and_margin_raw
from .references import _assistant_bling_message_with_refs
from .routing import _assistant_mercado_livre_full_stock_raw, _assistant_standard_result
from .runtime import _assistant_call_ia_tool, _assistant_texto_norm, _assistant_tool_call_signature_error
from .sales_returns import _assistant_raw_records, _assistant_sales_returns_query, _assistant_sqlite_sales_by_period, _assistant_sr_as_return_rate, _assistant_sr_as_returns_by_period, _assistant_sr_as_sales_by_period, _assistant_sr_as_sales_quantity
from .settings import BLING_REPORT_LIMIT, DEFAULT_RANKING_LIMIT

def _assistant_registry_context(
    client_id: str, tool_id: str, message: str, screen_context: Any, plan: dict[str, Any],
    existing_registry_results: Optional[list[dict[str, Any]]],
) -> dict[str, Any]:
    warnings: list[str] = []
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    data_inicio = str(plan.get("data_inicio") or "")
    data_fim = str(plan.get("data_fim") or "")
    loja = str(plan.get("loja") or "") or None
    sku = str(plan.get("sku") or "") or None
    materialized_refs = [
        value
        for value in (
            f"SKU {sku}" if sku else "",
            str(plan.get("item_id") or ""),
            f"loja {loja}" if loja else "",
        )
        if value
    ]
    materialized_message = " ".join(materialized_refs).strip() or message
    separar_por_loja = bool(plan.get("separar_por_loja"))
    incluir_registros = bool(plan.get("incluir_registros", True))
    prev = plan.get("periodo_anterior") if isinstance(plan.get("periodo_anterior"), dict) else {}
    mode = str(plan.get("mode") or "")
    report_mode = mode in {"daily", "report"}
    default_limit = 20_000 if tool_id == "mercado_livre_orders" and report_mode else (
        BLING_REPORT_LIMIT if report_mode else DEFAULT_RANKING_LIMIT
    )
    try:
        limit_safe = int(plan.get("limite") or plan.get("limit") or default_limit)
    except Exception:
        limit_safe = default_limit
    limit_safe = max(1, min(limit_safe, 20_000 if tool_id == "mercado_livre_orders" and report_mode else (500 if report_mode else 200)))

    return {"client_id": client_id, "tool_id": tool_id, "message": message,
        "screen_context": screen_context, "plan": plan,
        "existing_registry_results": existing_registry_results,
        "warnings": warnings, "raw_results": raw_results, "registry_results": registry_results,
        "data_inicio": data_inicio, "data_fim": data_fim, "loja": loja, "sku": sku,
        "materialized_message": materialized_message, "separar_por_loja": separar_por_loja,
        "incluir_registros": incluir_registros, "prev": prev, "report_mode": report_mode,
        "limit_safe": limit_safe}


def _assistant_registry_aliases(ctx: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(ctx[key] for key in (
        "client_id", "tool_id", "message", "screen_context", "plan", "existing_registry_results",
        "warnings", "raw_results", "registry_results", "data_inicio", "data_fim", "loja", "sku",
        "materialized_message", "separar_por_loja", "incluir_registros", "prev", "report_mode", "limit_safe",
    ))


def _assistant_registry_sales(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    if tool_id not in {"sales_returns_query", "sales_ranking", "sales_summary", "returns_summary", "return_rate",
            "profit_summary", "product_costs_and_margin", "avg_ticket", "period_comparison", "sales_timeseries",
            "sales_anomalies", "stockout_forecast", "stale_stock", "integrations_status"}:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "sales_returns_query":
        raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
    elif tool_id == "sales_ranking":
        raw = _assistant_call_ia_tool("_ia_tool_get_sales_by_period", client_id, data_inicio, data_fim, loja, limit_safe)
        if _assistant_raw_records(raw) == 0:
            sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
            fallback_raw = _assistant_sr_as_sales_by_period(sr_raw, limit_safe) or _assistant_sqlite_sales_by_period(client_id, data_inicio, data_fim, loja, limit_safe)
            if isinstance(fallback_raw, dict):
                fallback_records = _assistant_raw_records(fallback_raw)
                if fallback_records > 0 or not isinstance(raw, dict):
                    raw = fallback_raw
                    warnings.append(
                        "sales_ranking: usei sales_returns_query/SQLite direto dos bancos vendas_historico*.db "
                        "porque a ferramenta principal retornou vazio."
                    )
    elif tool_id == "sales_summary":
        raw = _assistant_call_ia_tool("_ia_tool_get_sales_quantity_by_period", client_id, data_inicio, data_fim, loja)
        if _assistant_raw_records(raw) == 0:
            sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
            raw = _assistant_sr_as_sales_quantity(sr_raw)
            if isinstance(raw, dict) and _assistant_raw_records(raw) > 0:
                warnings.append(
                    "sales_summary: usei sales_returns_query direto dos bancos vendas_historico*.db "
                    "porque a ferramenta principal retornou vazio."
                )
    elif tool_id == "returns_summary":
        raw = _assistant_call_ia_tool("_ia_tool_get_returns_by_period", client_id, data_inicio, data_fim, loja, 50)
        if _assistant_raw_records(raw) == 0:
            sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
            fallback_raw = _assistant_sr_as_returns_by_period(sr_raw, 50)
            if isinstance(fallback_raw, dict) and (_assistant_raw_records(fallback_raw) > 0 or not isinstance(raw, dict)):
                raw = fallback_raw
                warnings.append(
                    "returns_summary: usei sales_returns_query com notas_entrada_itens/vendas.devolucao "
                    "porque a ferramenta principal retornou vazio."
                )
    elif tool_id == "return_rate":
        raw = _assistant_call_ia_tool("_ia_tool_get_return_rate_by_period", client_id, data_inicio, data_fim, loja)
        if _assistant_raw_records(raw) == 0:
            sr_raw = _assistant_sales_returns_query(client_id, data_inicio, data_fim, loja, sku, separar_por_loja, incluir_registros)
            fallback_raw = _assistant_sr_as_return_rate(sr_raw)
            if isinstance(fallback_raw, dict):
                raw = fallback_raw
                warnings.append("return_rate: calculei a taxa pela sales_returns_query.")
    elif tool_id == "profit_summary":
        raw = _assistant_call_ia_tool("_ia_tool_get_profit_by_period", client_id, data_inicio, data_fim, loja)
    elif tool_id == "product_costs_and_margin":
        raw = _assistant_product_costs_and_margin_raw(client_id, plan, existing_registry_results)
    elif tool_id == "avg_ticket":
        raw = _assistant_call_ia_tool("_ia_tool_get_avg_ticket_by_period", client_id, data_inicio, data_fim, loja)
    elif tool_id == "period_comparison":
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_period_comparison",
            client_id,
            str(prev.get("data_inicio") or ""),
            str(prev.get("data_fim") or ""),
            data_inicio,
            data_fim,
            loja,
        )
    elif tool_id == "sales_timeseries":
        raw = _assistant_call_ia_tool("_ia_tool_get_sales_timeseries", client_id, data_inicio, data_fim, loja)
    elif tool_id == "sales_anomalies":
        raw = _assistant_call_ia_tool("_ia_tool_detect_sales_anomalies", client_id, data_inicio, data_fim, loja)
    elif tool_id == "stockout_forecast":
        raw = _assistant_call_ia_tool("_ia_tool_get_stockout_forecast", client_id, message, None, loja, 30, 100)
    elif tool_id == "stale_stock":
        raw = _assistant_call_ia_tool("_ia_tool_get_days_without_sale_top", client_id, loja, limit_safe, True, False, True)
    elif tool_id == "integrations_status":
        raw = _assistant_call_ia_tool("_ia_tool_get_integrations_status", client_id, loja)
    return True, raw


def _assistant_registry_marketplace_catalog(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    if tool_id not in {"mercado_livre_resource_query", "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions"}:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "mercado_livre_resource_query":
        from backend.services import mercado_livre_query_service

        raw = mercado_livre_query_service.execute_mercado_livre_query(
            client_id=client_id,
            message=message,
            loja=loja,
            resource_id=str(plan.get("resource_id") or ""),
            params=plan.get("resource_params") if isinstance(plan.get("resource_params"), dict) else {},
            limit=limit_safe,
            query_deadline=plan.get("query_deadline"),
        )
    elif tool_id == "mercado_livre_listing":
        ml_message = message if re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|sku|anuncio|anuncios|listar)\b", _assistant_texto_norm(message)) else "listar anuncios ativos mercado livre"
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_mercado_livre_listing",
            client_id,
            ml_message,
            loja=loja,
            produto_tool=None,
            limite=limit_safe,
            incluir_descricao=bool(plan.get("incluir_detalhes")),
            status=str(plan.get("status") or "active"),
            sku=str(plan.get("sku") or ""),
            item_id=str(plan.get("item_id") or ""),
            offset=int(plan.get("offset") or 0),
            incluir_detalhes=bool(plan.get("incluir_detalhes")),
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
            incluir_comercial=bool(plan.get("incluir_comercial")),
        )
        if _assistant_tool_call_signature_error(raw):
            # Runtime anterior: mantenha a listagem basica enquanto a nova
            # assinatura ainda nao estiver presente no espelho instalado.
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_listing",
                client_id,
                ml_message,
                loja,
                None,
                limit_safe,
                bool(plan.get("incluir_detalhes")),
            )
    elif tool_id == "mercado_livre_visits":
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_mercado_livre_visits",
            client_id,
            message,
            loja=loja,
            item_id=str(plan.get("item_id") or ""),
            dias=int(plan.get("dias") or 30),
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
        )
    elif tool_id == "mercado_livre_promotions":
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_mercado_livre_promotions",
            client_id,
            message,
            loja=loja,
            status=str(plan.get("status") or ""),
            promotion_id=str(plan.get("promotion_id") or ""),
            incluir_contagens=bool(plan.get("incluir_contagens")),
            limite=limit_safe,
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
        )
    return True, raw


def _assistant_registry_marketplace_orders(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    if tool_id not in {"mercado_livre_orders", "mercado_livre_returns", "mercado_livre_full_stock"}:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "mercado_livre_orders":
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_mercado_livre_orders",
            client_id,
            message,
            loja=loja,
            data_inicio=data_inicio,
            data_fim=data_fim,
            status=str(plan.get("status") or "paid,partially_refunded"),
            sku=str(plan.get("sku") or ""),
            item_id=str(plan.get("item_id") or ""),
            id_pedido=str(plan.get("id_pedido") or ""),
            offset=int(plan.get("offset") or 0),
            limite=limit_safe,
            incluir_detalhes=bool(plan.get("incluir_detalhes")),
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
            modo_relatorio=report_mode,
            max_paginas=int(plan.get("max_paginas") or (400 if report_mode else 2)),
        )
        if _assistant_tool_call_signature_error(raw):
            raw = _assistant_call_ia_tool(
                "_ia_tool_get_mercado_livre_orders",
                client_id,
                message,
                loja=loja,
                data_inicio=data_inicio,
                data_fim=data_fim,
                status=str(plan.get("status") or "paid,partially_refunded"),
                sku=str(plan.get("sku") or ""),
                item_id=str(plan.get("item_id") or ""),
                id_pedido=str(plan.get("id_pedido") or ""),
                offset=int(plan.get("offset") or 0),
                limite=min(limit_safe, 100),
                incluir_detalhes=bool(plan.get("incluir_detalhes")),
                force_refresh=bool(plan.get("force_refresh")),
                query_deadline=plan.get("query_deadline"),
            )
        if raw is None:
            raw = {
                "function": "get_mercado_livre_orders",
                "arguments": {
                    "loja": loja or "",
                    "data_inicio": data_inicio,
                    "data_fim": data_fim,
                    "status": str(plan.get("status") or "paid,partially_refunded"),
                    "sku": str(plan.get("sku") or ""),
                    "item_id": str(plan.get("item_id") or ""),
                    "id_pedido": str(plan.get("id_pedido") or ""),
                    "offset": int(plan.get("offset") or 0),
                    "limite": limit_safe,
                },
                "result": {
                    "error": "Consulta de pedidos Mercado Livre indisponivel neste runtime.",
                    "pedidos": [],
                    "read_only": True,
                },
            }
    elif tool_id == "mercado_livre_returns":
        raw = _assistant_call_ia_tool(
            "_ia_tool_get_mercado_livre_returns",
            client_id,
            message,
            loja=loja,
            data_inicio=data_inicio,
            data_fim=data_fim,
            limite=limit_safe,
            offset=int(plan.get("offset") or 0),
            sku=str(plan.get("sku") or ""),
            item_id=str(plan.get("item_id") or ""),
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
        )
        if raw is None:
            raw = {
                "function": "get_mercado_livre_returns",
                "arguments": {
                    "loja": loja or "",
                    "data_inicio": data_inicio,
                    "data_fim": data_fim,
                    "offset": int(plan.get("offset") or 0),
                    "limite": limit_safe,
                    "sku": str(plan.get("sku") or ""),
                    "item_id": str(plan.get("item_id") or ""),
                },
                "result": {
                    "error": "Consulta de devolucoes Mercado Livre indisponivel neste runtime.",
                    "devolucoes": [],
                    "read_only": True,
                },
            }
    elif tool_id == "mercado_livre_full_stock":
        raw = _assistant_mercado_livre_full_stock_raw(client_id, plan)
    return True, raw


def _assistant_registry_bling_product(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    supported = tool_id in {"bling_product", "product_data", "product_registry", "stock_data", "product_margin", "product_image"}
    if not supported and not str(tool_id or "").startswith("bling_"):
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "bling_product":
        bling_message = _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
        raw = _assistant_call_ia_tool("_ia_tool_get_bling_product", client_id, bling_message, loja, None)
    elif str(tool_id or "").startswith("bling_"):
        from backend.services import codex_bling_tools

        bling_message = (
            message
            if tool_id == "bling_positive_stock_sku_count"
            else _assistant_bling_message_with_refs(message, plan, screen_context, existing_registry_results)
        )
        if tool_id == "bling_sales_order_detail" and str(plan.get("id_pedido") or ""):
            bling_message = f"pedido {plan.get('id_pedido')} {bling_message}".strip()
        raw = codex_bling_tools.execute_bling_tool(
            client_id=client_id,
            tool_id=tool_id,
            message=bling_message,
            loja=loja,
            data_inicio=data_inicio,
            data_fim=data_fim,
            limit=limit_safe,
            offset=int(plan.get("offset") or 0),
            status=str(plan.get("status") or ""),
            sku=str(plan.get("sku") or ""),
            item_id=str(plan.get("item_id") or ""),
            id_pedido=str(plan.get("id_pedido") or ""),
            force_refresh=bool(plan.get("force_refresh")),
            query_deadline=plan.get("query_deadline"),
        )
    elif tool_id == "product_data":
        raw = _assistant_call_ia_tool("_ia_tool_get_product_data", client_id, materialized_message, 10)
    elif tool_id == "product_registry":
        raw = _assistant_call_ia_tool("_ia_tool_get_product_registry_info", client_id, materialized_message, None, 10)
    elif tool_id == "stock_data":
        raw = _assistant_call_ia_tool("_ia_tool_get_stock_data", client_id, materialized_message, None)
    elif tool_id == "product_margin":
        raw = _assistant_call_ia_tool("_ia_tool_get_product_margin", client_id, materialized_message, None)
    elif tool_id == "product_image":
        raw = _assistant_call_ia_tool("_ia_tool_get_product_image", client_id, materialized_message, None)
    return True, raw


def _assistant_registry_memory_context(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    if tool_id not in {"operational_memory_query", "context_hub_search"}:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "operational_memory_query":
        from backend.services import codex_operational_memory

        result = codex_operational_memory.query_memory(
            client_id=client_id,
            message=message,
            category=str(plan.get("category_filter") or ""),
            limit=limit_safe,
        )
        raw = {
            "function": "codex_operational_memory.query_memory",
            "arguments": {
                "message": message,
                "category": str(plan.get("category_filter") or ""),
                "limit": limit_safe,
            },
            "result": result,
        }
    elif tool_id == "context_hub_search":
        from backend.modules.context_hub import retrieval as context_hub_retrieval

        filters = {
            "sku": str(plan.get("sku") or ""),
            "mlb": str(plan.get("item_id") or ""),
            "module": str(plan.get("module_filter") or ""),
            "ids": list(plan.get("context_ids") or [])[:50],
            "source_type": str(plan.get("source_type") or ""),
            "surface": str(plan.get("context_surface") or plan.get("environment_filter") or ""),
            "request_surface": str(plan.get("request_surface") or ""),
            "document_types": list(plan.get("document_types") or [])[:10],
            "store_ref": str(plan.get("context_store_ref") or ""),
            "tags": list(plan.get("context_tags") or [])[:12],
            "valid_at": str(plan.get("context_valid_at") or ""),
            "truth_class": str(plan.get("context_truth_class") or ""),
            "authority": str(plan.get("context_authority") or ""),
            "sensitivity": str(plan.get("context_sensitivity") or ""),
        }
        filters = {key: value for key, value in filters.items() if value not in ("", [], None)}
        result = context_hub_retrieval.search_context(
            client_id=client_id,
            query=message,
            filters=filters,
            limit=min(limit_safe, 12),
        )
        raw = {
            "function": "context_hub.search_context",
            "arguments": {
                "query": message,
                "filters": filters,
                "limit": min(limit_safe, 12),
            },
            "result": result,
        }
    return True, raw


def _assistant_registry_readonly_sources(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    supported = {"source_discovery", "local_database_query", "local_csv_query", "local_cache_query",
        "sync_logs_query", "mercado_livre_readonly", "questions_post_sale_query",
        "mercado_livre_post_sale_detail", "fiscal_local_query"}
    if tool_id not in supported:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id in {
        "source_discovery",
        "local_database_query",
        "local_csv_query",
        "local_cache_query",
        "sync_logs_query",
        "mercado_livre_readonly",
        "questions_post_sale_query",
        "mercado_livre_post_sale_detail",
        "fiscal_local_query",
    }:
        from backend.services import codex_readonly_sources

        query_deadline = plan.get("query_deadline")
        try:
            query_deadline_seconds = max(5, min(15, int(float(query_deadline) - time.monotonic())))
        except (TypeError, ValueError):
            query_deadline_seconds = 15
        result = codex_readonly_sources.execute_readonly_source_tool(
            client_id=client_id,
            tool_id=tool_id,
            message=message,
            loja=loja or "",
            data_inicio=data_inicio,
            data_fim=data_fim,
            limit=limit_safe,
            query_deadline_seconds=query_deadline_seconds,
            args={
                "source_id": str(plan.get("source_id") or ""),
                "module": str(plan.get("module_filter") or ""),
                "type": str(plan.get("source_type") or ""),
                "sql": str(plan.get("sql") or ""),
                "status": str(plan.get("status") or ""),
                "separar_por_loja": bool(plan.get("separar_por_loja")),
                "pack_id": str(plan.get("pack_id") or ""),
                "order_id": str(plan.get("id_pedido") or ""),
            },
        )
        raw = {
            "function": f"codex_readonly_sources.{tool_id}",
            "arguments": {
                "message": message,
                "loja": loja or "",
                "source_id": str(plan.get("source_id") or ""),
                "module": str(plan.get("module_filter") or ""),
                "type": str(plan.get("source_type") or ""),
                "limit": limit_safe,
                "sql": bool(plan.get("sql")),
            },
            "result": result,
        }
    return True, raw


def _assistant_registry_capabilities(ctx: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    (client_id, tool_id, message, screen_context, plan, existing_registry_results,
     warnings, raw_results, registry_results, data_inicio, data_fim, loja, sku,
     materialized_message, separar_por_loja, incluir_registros, prev, report_mode,
     limit_safe) = _assistant_registry_aliases(ctx)
    if tool_id not in {"program_functions_catalog", "capability_resolve", "program_action_match"}:
        return False, None
    raw: Optional[dict[str, Any]] = None
    if tool_id == "program_functions_catalog":
        from backend.services import codex_capabilities

        result = codex_capabilities.list_capabilities(
            client_id=client_id,
            query=message,
            module=str(plan.get("module_filter") or ""),
            category=str(plan.get("category_filter") or ""),
            limit=limit_safe,
        )
        raw = {
            "function": "codex_capabilities.list_capabilities",
            "arguments": {
                "query": message,
                "module": str(plan.get("module_filter") or ""),
                "category": str(plan.get("category_filter") or ""),
                "limit": limit_safe,
            },
            "result": result,
        }
    elif tool_id == "capability_resolve":
        from backend.services import codex_capabilities

        result = codex_capabilities.resolve_capability(
            client_id=client_id,
            message=message,
            capability_id=str(plan.get("capability_id") or ""),
            module=str(plan.get("module_filter") or ""),
            category=str(plan.get("category_filter") or ""),
            params=plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
            limit=min(limit_safe, 20),
        )
        raw = {
            "function": "codex_capabilities.resolve_capability",
            "arguments": {
                "message": message,
                "capability_id": str(plan.get("capability_id") or ""),
                "module": str(plan.get("module_filter") or ""),
                "category": str(plan.get("category_filter") or ""),
            },
            "result": result,
        }
    elif tool_id == "program_action_match":
        from backend.services import codex_actions

        result = codex_actions.match_action_dry_run(
            client_id=client_id,
            username="codex",
            message=message,
            action_id=str(plan.get("action_id") or ""),
            capability_id=str(plan.get("capability_id") or ""),
            params=plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
            screen_context=screen_context if isinstance(screen_context, dict) else {},
            history=plan.get("history") if isinstance(plan.get("history"), list) else [],
        )
        if isinstance(result, dict) and result.get("matched"):
            result["rows"] = [
                {
                    "action_id": ((result.get("action") or {}) if isinstance(result.get("action"), dict) else {}).get("id") or "",
                    "label": ((result.get("action") or {}) if isinstance(result.get("action"), dict) else {}).get("label") or "",
                    "params": result.get("params") or {},
                    "missing_params": result.get("missing_params") or [],
                    "proposal_preview": result.get("proposal_preview") if isinstance(result.get("proposal_preview"), dict) else {},
                    "requires_confirmation": True,
                }
            ]
        raw = {
            "function": "codex_actions.match_action_dry_run",
            "arguments": {
                "message": message,
                "action_id": str(plan.get("action_id") or ""),
                "capability_id": str(plan.get("capability_id") or ""),
                "params": plan.get("action_params") if isinstance(plan.get("action_params"), dict) else {},
            },
            "result": result,
        }
    return True, raw


def _assistant_execute_registry_tool(
    client_id: str, tool_id: str, message: str, screen_context: Any, plan: dict[str, Any],
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    ctx = _assistant_registry_context(client_id, tool_id, message, screen_context, plan, existing_registry_results)
    warnings, raw_results, registry_results = ctx["warnings"], ctx["raw_results"], ctx["registry_results"]
    try:
        raw: Optional[dict[str, Any]] = None
        handled = False
        for executor in (_assistant_registry_sales, _assistant_registry_marketplace_catalog,
                         _assistant_registry_marketplace_orders, _assistant_registry_bling_product,
                         _assistant_registry_memory_context, _assistant_registry_readonly_sources,
                         _assistant_registry_capabilities):
            handled, raw = executor(ctx)
            if handled:
                break
        if tool_id == "operational_dispatcher":
            dispatcher_results, _, dispatcher_warnings = _assistant_execute_dispatcher(client_id, message, screen_context)
            warnings.extend(dispatcher_warnings)
            raw_results.extend(dispatcher_results)
            registry_results.extend(_assistant_standard_result(tool_id, item, plan) for item in dispatcher_results)
            if not dispatcher_results:
                registry_results.append(_assistant_standard_result(tool_id, None, plan))
            return raw_results, registry_results, warnings
        if isinstance(raw, dict):
            raw_results.append(raw)
            raw_result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
            for warning in raw_result.get("warnings") if isinstance(raw_result.get("warnings"), list) else []:
                warning_text = str(warning or "").strip()
                if warning_text and warning_text not in warnings:
                    warnings.append(warning_text[:600])
        registry_results.append(_assistant_standard_result(tool_id, raw, plan))
    except Exception as exc:
        warnings.append(f"{tool_id}: {str(exc)[:300]}")
        registry_results.append(_assistant_standard_result(tool_id, {"function": tool_id, "result": {"error": str(exc)[:300]}}, plan))
    return raw_results, registry_results, warnings
