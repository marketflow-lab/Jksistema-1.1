"""Internal slice for ia_tools."""

from __future__ import annotations

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
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *


def configure_ia_tools_vendas_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_ia_tools_vendas_runtime()


def _ia_stock_chart_number(value: Any, default: float = 0.0) -> float:
    """Convert an internal stock metric without copying arbitrary source data."""

    if value is None or isinstance(value, bool):
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _ia_stale_stock_chart_data(result: dict[str, Any]) -> dict[str, Any]:
    """Build the complete, PII-free contract used by stock-age visuals."""

    source = result if isinstance(result, dict) else {}
    raw_items = source.get("itens") if isinstance(source.get("itens"), list) else []
    stale_items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        quantity = _ia_stock_chart_number(item.get("saldo_loja"))
        days_raw = item.get("dias_sem_vender")
        days = None
        if days_raw is not None:
            try:
                days = max(0, int(float(days_raw)))
            except (TypeError, ValueError):
                days = None
        never_sold = str(item.get("status") or "").strip().lower() == "nunca_vendeu"
        if quantity <= 0 or (not never_sold and (days is None or days < 30)):
            continue
        if never_sold:
            band_key, band_label, band_order = "never_sold", "Nunca vendeu", 0
        elif days is not None and days >= 180:
            band_key, band_label, band_order = "days_180_plus", "180 dias ou mais", 1
        elif days is not None and days >= 90:
            band_key, band_label, band_order = "days_90_179", "90 a 179 dias", 2
        else:
            band_key, band_label, band_order = "days_30_89", "30 a 89 dias", 3

        capital_known = item.get("custo_cadastrado") is True and item.get("valor_custo_estoque_loja") is not None
        capital_value = (
            round(_ia_stock_chart_number(item.get("valor_custo_estoque_loja")), 2)
            if capital_known
            else None
        )
        stale_items.append(
            {
                "sku": str(item.get("sku") or "").strip(),
                "title": str(item.get("produto") or item.get("nome") or "").strip(),
                "store": str(item.get("loja") or source.get("loja") or "").strip(),
                "quantity": round(quantity, 3),
                "days_without_sale": days,
                "last_sale": str(item.get("ultima_venda") or "").strip(),
                "age_band": band_key,
                "age_band_label": band_label,
                "age_band_order": band_order,
                "capital_known": capital_known,
                "capital_value": capital_value,
                "ranking_metric": "known_capital" if capital_known else "quantity",
                "ranking_value": capital_value if capital_known else round(quantity, 3),
                "value_type": "currency" if capital_known else "quantity",
            }
        )

    band_definitions = (
        ("never_sold", "Nunca vendeu"),
        ("days_180_plus", "180 dias ou mais"),
        ("days_90_179", "90 a 179 dias"),
        ("days_30_89", "30 a 89 dias"),
    )
    age_bands = []
    for key, label in band_definitions:
        matches = [item for item in stale_items if item.get("age_band") == key]
        age_bands.append(
            {
                "key": key,
                "label": label,
                "skus": len(matches),
                "quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in matches), 3),
            }
        )

    ranking = sorted(
        stale_items,
        key=lambda item: (
            0 if item.get("capital_known") else 1,
            -_ia_stock_chart_number(item.get("ranking_value")),
            int(item.get("age_band_order") or 0),
            str(item.get("sku") or ""),
        ),
    )
    total_evaluated = max(0, int(_ia_stock_chart_number(source.get("total_skus_avaliados"))))
    total_returned = max(0, int(_ia_stock_chart_number(source.get("total_skus_retornados"), len(raw_items))))
    truncated = bool(source.get("resultado_truncado")) or total_returned < total_evaluated
    known = [item for item in stale_items if item.get("capital_known")]
    totals = {
        "stale_skus": len(stale_items),
        "stale_quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in stale_items), 3),
        "capital_known": round(sum(_ia_stock_chart_number(item.get("capital_value")) for item in known), 2),
        "skus_with_known_cost": len(known),
        "skus_without_known_cost": len(stale_items) - len(known),
    }
    return {
        "schema": "jk.stock.stale_inventory.v1",
        "kind": "stale_inventory",
        "currency_id": "BRL",
        "store": str(source.get("loja") or "").strip(),
        "reference_date": str(source.get("data_referencia") or "").strip(),
        "metrics": [
            {"key": "quantity", "type": "number"},
            {"key": "days_without_sale", "type": "integer", "nullable": True},
            {"key": "capital_value", "type": "currency", "nullable": True},
        ],
        "totals": totals,
        "age_bands": age_bands,
        "ranking": ranking,
        "ranking_basis": "known_capital_then_quantity",
        "coverage_complete": not truncated,
        "partial": truncated,
        "coverage": {
            "evaluated_skus": total_evaluated,
            "returned_skus": total_returned,
            "stale_skus_returned": len(stale_items),
            "truncated": truncated,
        },
        "pii_included": False,
        "read_only": True,
    }


def _ia_stockout_chart_data(result: dict[str, Any]) -> dict[str, Any]:
    """Build the complete, PII-free contract used by stockout visuals."""

    source = result if isinstance(result, dict) else {}
    raw_items = source.get("itens") if isinstance(source.get("itens"), list) else []
    if not raw_items and source.get("sku"):
        raw_items = [source]
    risk_order = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3, "sem_consumo": 4}
    risk_labels = {
        "critico": "Crítico",
        "alto": "Alto",
        "medio": "Médio",
        "baixo": "Baixo",
        "sem_consumo": "Sem consumo",
    }
    ranking: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        risk = str(item.get("risco_ruptura") or "sem_consumo").strip().lower()
        if risk not in risk_order:
            risk = "sem_consumo"
        days_raw = item.get("dias_ate_ruptura")
        days = None if days_raw is None else round(max(0.0, _ia_stock_chart_number(days_raw)), 2)
        quantity = round(_ia_stock_chart_number(item.get("saldo_total")), 3)
        daily_demand = round(_ia_stock_chart_number(item.get("media_venda_dia")), 4)
        urgency_score = round(
            (len(risk_order) - risk_order[risk]) * 1_000_000
            + max(0.0, 100_000.0 - (days if days is not None else 100_000.0))
            + daily_demand,
            4,
        )
        ranking.append(
            {
                "sku": str(item.get("sku") or "").strip(),
                "title": str(item.get("nome") or item.get("produto") or "").strip(),
                "store": str(item.get("loja") or source.get("loja") or "").strip(),
                "risk": risk,
                "risk_label": risk_labels[risk],
                "risk_order": risk_order[risk],
                "quantity": quantity,
                "store_quantity": round(_ia_stock_chart_number(item.get("saldo_loja")), 3),
                "full_quantity": round(_ia_stock_chart_number(item.get("saldo_full")), 3),
                "sold_in_window": round(_ia_stock_chart_number(item.get("quantidade_vendida_janela")), 3),
                "daily_demand": daily_demand,
                "days_to_stockout": days,
                "stockout_date": str(item.get("data_prevista_ruptura") or "").strip(),
                "urgency_score": urgency_score,
            }
        )
    ranking.sort(
        key=lambda item: (
            int(item.get("risk_order") or 0),
            float(item.get("days_to_stockout")) if item.get("days_to_stockout") is not None else float("inf"),
            -_ia_stock_chart_number(item.get("daily_demand")),
            str(item.get("sku") or ""),
        )
    )
    for position, item in enumerate(ranking, start=1):
        item["rank"] = position

    risk_bands = []
    for key in ("critico", "alto", "medio", "baixo", "sem_consumo"):
        matches = [item for item in ranking if item.get("risk") == key]
        risk_bands.append(
            {
                "key": key,
                "label": risk_labels[key],
                "skus": len(matches),
                "quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in matches), 3),
            }
        )
    total_analyzed = max(0, int(_ia_stock_chart_number(source.get("total_skus_analisados"), len(ranking))))
    truncated = len(ranking) < total_analyzed
    at_risk = [item for item in ranking if item.get("risk") in {"critico", "alto", "medio"}]
    return {
        "schema": "jk.stock.stockout_forecast.v1",
        "kind": "stockout_forecast",
        "store": str(source.get("loja") or "").strip(),
        "reference_date": str(source.get("data_referencia") or "").strip(),
        "lookback_days": max(0, int(_ia_stock_chart_number(source.get("janela_dias")))),
        "metrics": [
            {"key": "quantity", "type": "number"},
            {"key": "daily_demand", "type": "number"},
            {"key": "days_to_stockout", "type": "number", "nullable": True},
        ],
        "totals": {
            "analyzed_skus": total_analyzed,
            "returned_skus": len(ranking),
            "at_risk_skus": len(at_risk),
            "critical_skus": sum(1 for item in ranking if item.get("risk") == "critico"),
            "high_risk_skus": sum(1 for item in ranking if item.get("risk") == "alto"),
            "stock_quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in ranking), 3),
            "daily_demand": round(sum(_ia_stock_chart_number(item.get("daily_demand")) for item in ranking), 4),
        },
        "risk_bands": risk_bands,
        "ranking": ranking,
        "ranking_basis": "risk_then_days_to_stockout",
        "coverage_complete": not truncated,
        "partial": truncated,
        "coverage": {
            "analyzed_skus": total_analyzed,
            "returned_skus": len(ranking),
            "truncated": truncated,
        },
        "pii_included": False,
        "read_only": True,
    }


def _ia_tool_get_days_without_sale(client_id: str, mensagem: str, produto_tool: Optional[dict] = None, loja: Optional[str] = None) -> Optional[dict]:
    try:
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        ultima_venda = ""
        primeira_venda = ""
        produto_nome = ""

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT
                        MIN(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN data END) AS primeira_venda,
                        MAX(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN data END) AS ultima_venda,
                        MAX(produto) AS produto
                    FROM vendas
                    WHERE UPPER(TRIM(COALESCE(sku, ''))) = ?
                      {sql_loja}
                    """,
                    [sku, *params_loja],
                ).fetchone()
                conn.close()
                if row:
                    prod = str(row["produto"] or "").strip()
                    if prod and not produto_nome:
                        produto_nome = prod
                    p = str(row["primeira_venda"] or "").strip()
                    u = str(row["ultima_venda"] or "").strip()
                    if p and (not primeira_venda or p < primeira_venda):
                        primeira_venda = p
                    if u and (not ultima_venda or u > ultima_venda):
                        ultima_venda = u
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar dias sem venda para SKU {sku} em {db_path}: {exc}")
                continue

        data_ref = _ia_obter_data_referencia_vendas(client_id, loja_filtro) or dt.date.today()
        status = "nunca_vendeu"
        dias_sem_vender = None
        if ultima_venda:
            try:
                ultima_venda_data = str(ultima_venda).strip().replace("T", " ").split(" ", 1)[0]
                dias_sem_vender = max(0, (data_ref - dt.date.fromisoformat(ultima_venda_data)).days)
                ultima_venda = ultima_venda_data
                status = "sem_venda_recente" if dias_sem_vender > 0 else "vendeu_no_dia"
            except Exception:
                dias_sem_vender = None
                status = "sem_dado"

        return {
            "function": "get_days_without_sale",
            "arguments": {"sku": sku, "loja": loja_filtro or ""},
            "result": {
                "sku": sku,
                "produto": produto_nome,
                "loja": loja_filtro or "",
                "data_referencia": data_ref.isoformat(),
                "primeira_venda": primeira_venda,
                "ultima_venda": ultima_venda,
                "dias_sem_vender": dias_sem_vender,
                "status": status,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao calcular dias sem venda: {exc}")
        return None


def _ia_tool_get_days_without_sale_top(
    client_id: str,
    loja: Optional[str] = None,
    limite: int = 20,
    apenas_com_estoque: bool = False,
    apenas_ja_vendidos: bool = False,
    apenas_saldo_loja: bool = False,
) -> Optional[dict]:
    try:
        limite = max(1, min(int(limite or 20), 500))
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None

        df = _ia_carregar_produtos_tool_df(client_id)
        if df is None or df.empty:
            return None

        mapa_produtos: dict[str, str] = {}
        mapa_saldos: dict[str, float] = {}
        mapa_saldos_loja: dict[str, float] = {}
        mapa_saldos_full: dict[str, float] = {}
        mapa_custos: dict[str, float] = {}
        mapa_precos: dict[str, float] = {}
        mapa_custo_origem: dict[str, str] = {}
        mapa_preco_origem: dict[str, str] = {}
        custos_conhecidos: set[str] = set()
        precos_conhecidos: set[str] = set()
        for _, row in df.iterrows():
            sku = str(row.get("sku_norm") or "").strip().upper()
            if not sku:
                continue
            if not any(ch.isalnum() for ch in sku):
                continue
            if sku not in mapa_produtos:
                mapa_produtos[sku] = str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip()
            saldo_total = _ia_tool_float(row.get("saldo_total"))
            saldo_loja = _ia_tool_float(row.get("saldo_loja"))
            saldo_full = _ia_tool_float(row.get("saldo_full"))
            if saldo_total <= 0:
                saldo_total = saldo_loja + saldo_full
            mapa_saldos[sku] = max(mapa_saldos.get(sku, 0.0), float(saldo_total or 0.0))
            mapa_saldos_loja[sku] = max(mapa_saldos_loja.get(sku, 0.0), float(saldo_loja or 0.0))
            mapa_saldos_full[sku] = max(mapa_saldos_full.get(sku, 0.0), float(saldo_full or 0.0))
            for coluna in ("custo", "custo_y", "custo_x"):
                raw_custo = str(row.get(coluna) or "").strip()
                if raw_custo:
                    mapa_custos[sku] = _ia_tool_float(raw_custo)
                    custos_conhecidos.add(sku)
                    mapa_custo_origem[sku] = "cadastro geral"
                    break
            for coluna in ("preco", "preco_y", "preco_x"):
                raw_preco = str(row.get(coluna) or "").strip()
                if raw_preco:
                    mapa_precos[sku] = _ia_tool_float(raw_preco)
                    precos_conhecidos.add(sku)
                    mapa_preco_origem[sku] = "cadastro geral"
                    break

        # O cadastro principal pode estar incompleto. Aproveita o cadastro de
        # custos por loja e, no consolidado, usa a media dos valores conhecidos
        # sem transformar campo ausente em zero.
        custos_reader = globals().get("_cadastro_ler_custos_lojas")
        if callable(custos_reader):
            try:
                df_custos = custos_reader(client_id)
                custos_por_sku: dict[str, list[float]] = {}
                precos_por_sku: dict[str, list[float]] = {}
                loja_key_target = _normalizar_texto(loja_filtro or "")
                for _, cost_row in (df_custos.iterrows() if df_custos is not None and not df_custos.empty else []):
                    cost_sku = str(cost_row.get("sku") or "").strip().upper()
                    if not cost_sku:
                        continue
                    if loja_key_target and _normalizar_texto(cost_row.get("loja_sync") or "") != loja_key_target:
                        continue
                    sku_variants = [cost_sku]
                    variant_resolver = globals().get("_sku_lookup_variantes")
                    if callable(variant_resolver):
                        try:
                            sku_variants = list(dict.fromkeys([cost_sku, *variant_resolver(cost_sku)]))
                        except Exception:
                            sku_variants = [cost_sku]
                    matched_sku = next((variant for variant in sku_variants if variant in mapa_produtos), cost_sku)
                    raw_custo = str(cost_row.get("custo") or "").strip()
                    raw_preco = str(cost_row.get("preco") or "").strip()
                    if raw_custo:
                        custos_por_sku.setdefault(matched_sku, []).append(_ia_tool_float(raw_custo))
                    if raw_preco:
                        precos_por_sku.setdefault(matched_sku, []).append(_ia_tool_float(raw_preco))
                for sku, values in custos_por_sku.items():
                    if not values:
                        continue
                    mapa_custos[sku] = sum(values) / len(values)
                    custos_conhecidos.add(sku)
                    mapa_custo_origem[sku] = "cadastro da loja" if loja_filtro else "media do cadastro por loja"
                for sku, values in precos_por_sku.items():
                    if not values:
                        continue
                    mapa_precos[sku] = sum(values) / len(values)
                    precos_conhecidos.add(sku)
                    mapa_preco_origem[sku] = "cadastro da loja" if loja_filtro else "media do cadastro por loja"
            except Exception as exc:
                if logger is not None:
                    logger.warning(f"[IA TOOLS] Falha ao complementar custo por loja no estoque parado: {exc}")

        if not mapa_produtos:
            return None

        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        ultimas_vendas: dict[str, str] = {}

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                           MAX(data) AS ultima_venda
                    FROM vendas
                    WHERE COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [*params_loja],
                ).fetchall()
                conn.close()
                for row in rows or []:
                    sku = str(row["sku"] or "").strip().upper()
                    data_ref = str(row["ultima_venda"] or "").strip().replace("T", " ").split(" ", 1)[0]
                    if not sku or not data_ref:
                        continue
                    anterior = ultimas_vendas.get(sku)
                    if (anterior is None) or (data_ref > anterior):
                        ultimas_vendas[sku] = data_ref
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao buscar últimas vendas em {db_path}: {exc}")
                continue

        data_referencia = _ia_obter_data_referencia_vendas(client_id, loja_filtro) or dt.date.today()
        itens = []
        for sku, nome in mapa_produtos.items():
            saldo_total = float(mapa_saldos.get(sku) or 0.0)
            saldo_loja = float(mapa_saldos_loja.get(sku) or 0.0)
            saldo_full = float(mapa_saldos_full.get(sku) or 0.0)
            custo_conhecido = sku in custos_conhecidos
            preco_conhecido = sku in precos_conhecidos
            custo_unitario = float(mapa_custos.get(sku) or 0.0)
            preco_unitario = float(mapa_precos.get(sku) or 0.0)
            valores_cadastro = {
                "custo_cadastrado": custo_conhecido,
                "custo_unitario": custo_unitario if custo_conhecido else None,
                "custo_origem": mapa_custo_origem.get(sku, "") if custo_conhecido else "",
                "valor_custo_estoque_loja": round(saldo_loja * custo_unitario, 2) if custo_conhecido else None,
                "preco_cadastrado": preco_conhecido,
                "preco_unitario": preco_unitario if preco_conhecido else None,
                "preco_origem": mapa_preco_origem.get(sku, "") if preco_conhecido else "",
                "valor_venda_estoque_loja": round(saldo_loja * preco_unitario, 2) if preco_conhecido else None,
            }
            if apenas_com_estoque and saldo_total <= 0:
                continue
            if apenas_saldo_loja and saldo_loja <= 0:
                continue
            ultima = str(ultimas_vendas.get(sku) or "").strip()
            if not ultima:
                if apenas_ja_vendidos:
                    continue
                itens.append({
                    "sku": sku,
                    "produto": nome,
                    "saldo_loja": saldo_loja,
                    "saldo_full": saldo_full,
                    "saldo_total": saldo_total,
                    "ultima_venda": "",
                    "dias_sem_vender": None,
                    "status": "nunca_vendeu",
                    **valores_cadastro,
                })
                continue
            try:
                dias = max(0, (data_referencia - dt.date.fromisoformat(ultima)).days)
                itens.append({
                    "sku": sku,
                    "produto": nome,
                    "saldo_loja": saldo_loja,
                    "saldo_full": saldo_full,
                    "saldo_total": saldo_total,
                    "ultima_venda": ultima,
                    "dias_sem_vender": dias,
                    "status": "sem_venda_recente" if dias > 0 else "vendeu_no_dia",
                    **valores_cadastro,
                })
            except Exception:
                itens.append({
                    "sku": sku,
                    "produto": nome,
                    "saldo_loja": saldo_loja,
                    "saldo_full": saldo_full,
                    "saldo_total": saldo_total,
                    "ultima_venda": ultima,
                    "dias_sem_vender": None,
                    "status": "sem_dado",
                    **valores_cadastro,
                })

        if not itens:
            return None

        # SKUs sem histórico vêm primeiro; depois maior quantidade de dias sem vender.
        itens_ordenados = sorted(
            itens,
            key=lambda x: (
                0 if x.get("dias_sem_vender") is None else 1,
                -(int(x.get("dias_sem_vender") or 0)),
                str(x.get("sku") or ""),
            ),
        )
        itens_parados_loja = [
            item for item in itens_ordenados
            if float(item.get("saldo_loja") or 0) > 0
            and (item.get("dias_sem_vender") is None or int(item.get("dias_sem_vender") or 0) >= 30)
        ]
        custos_cobertos = [item for item in itens_parados_loja if item.get("custo_cadastrado") is True]
        resumo_parado = {
            "total_skus": len(itens_parados_loja),
            "total_unidades_loja": round(sum(float(item.get("saldo_loja") or 0) for item in itens_parados_loja), 3),
            "nunca_venderam": sum(1 for item in itens_parados_loja if item.get("status") == "nunca_vendeu"),
            "dias_180_mais": sum(1 for item in itens_parados_loja if item.get("dias_sem_vender") is not None and int(item.get("dias_sem_vender") or 0) >= 180),
            "dias_90_179": sum(1 for item in itens_parados_loja if item.get("dias_sem_vender") is not None and 90 <= int(item.get("dias_sem_vender") or 0) < 180),
            "dias_30_89": sum(1 for item in itens_parados_loja if item.get("dias_sem_vender") is not None and 30 <= int(item.get("dias_sem_vender") or 0) < 90),
            "custos_cobertos": len(custos_cobertos),
            "capital_custo_conhecido": round(sum(float(item.get("valor_custo_estoque_loja") or 0) for item in custos_cobertos), 2),
        }

        result = {
            "loja": loja_filtro or "",
            "data_referencia": data_referencia.isoformat(),
            "total_skus_avaliados": len(itens_ordenados),
            "total_skus_retornados": min(len(itens_ordenados), limite),
            "resultado_truncado": len(itens_ordenados) > limite,
            "resumo_estoque_parado": resumo_parado,
            "itens": itens_ordenados[:limite],
        }
        result["chart_data"] = _ia_stale_stock_chart_data(result)
        return {
            "function": "get_days_without_sale_top",
            "arguments": {
                "loja": loja_filtro or "",
                "limite": limite,
                "apenas_com_estoque": bool(apenas_com_estoque),
                "apenas_ja_vendidos": bool(apenas_ja_vendidos),
                "apenas_saldo_loja": bool(apenas_saldo_loja),
            },
            "result": result,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao listar SKUs sem venda: {exc}")
        return None


def _ia_tool_float(value: Any) -> float:
    try:
        texto = str(value or "").strip().replace("R$", "").replace(" ", "")
        if not texto:
            return 0.0
        if "," in texto and "." in texto:
            texto = texto.replace(".", "").replace(",", ".")
        elif "," in texto:
            texto = texto.replace(",", ".")
        return float(texto)
    except Exception:
        return 0.0


def _ia_tool_resolver_sku(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> str:
    ref = _ia_extrair_referencia_produto_mensagem(mensagem)
    sku = str(ref.get("sku") or "").strip().upper()
    if sku:
        return sku
    if produto_tool:
        return str(((produto_tool.get("result") or {}).get("canonical_sku") or "")).strip().upper()
    return ""


def _ia_tool_get_stock_data(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> Optional[dict]:
    base_produto = produto_tool or _ia_tool_get_product_data(client_id, mensagem)
    if not base_produto:
        return None
    resultado = base_produto.get("result") or {}
    matches = resultado.get("matches") or []
    if not matches:
        return {
            "function": "get_stock_data",
            "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or ""},
            "result": {"found": False, "items": []},
        }

    sku = str(resultado.get("canonical_sku") or matches[0].get("sku") or "").strip().upper()
    saldo_loja_total = sum(_ia_tool_float(item.get("saldo_loja")) for item in matches)
    saldo_full_total = sum(_ia_tool_float(item.get("saldo_full")) for item in matches)
    return {
        "function": "get_stock_data",
        "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or sku},
        "result": {
            "found": True,
            "sku": sku,
            "nome": str(matches[0].get("nome") or "").strip(),
            "saldo_loja_total": saldo_loja_total,
            "saldo_full_total": saldo_full_total,
            "saldo_total": saldo_loja_total + saldo_full_total,
            "linhas_origem": len(matches),
        },
    }


def _ia_tool_get_sales_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None, limite: int = 5) -> Optional[dict]:
    try:
        if not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        total_qtd = 0.0
        total_valor = 0.0
        total_pedidos = 0
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT SUM(quantidade) AS qtd, SUM(valor) AS valor, COUNT(DISTINCT numero) AS pedidos
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchone()
                if row:
                    total_qtd += float(row["qtd"] or 0)
                    total_valor += float(row["valor"] or 0)
                    total_pedidos += int(row["pedidos"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao resumir vendas em {db_path}: {exc}")
                continue
        top_skus = _ia_vendas_db_top_skus(client_id, data_inicio, data_fim, loja_filtro, limite=limite)
        period_label = f"{data_inicio} a {data_fim}"
        return {
            "function": "get_sales_by_period",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja_filtro or ""},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "quantidade_total": total_qtd,
                "valor_total": total_valor,
                "pedidos_total": total_pedidos,
                "top_skus": top_skus,
                "chart_data": {
                    "schema": "jk.sales.period_summary.v1",
                    "analysis_type": "sales",
                    "title": "Analise visual de vendas",
                    "source": "Historico de vendas do JK Sistema",
                    "period_start": data_inicio,
                    "period_end": data_fim,
                    "store": loja_filtro or "Todas as lojas",
                    "coverage_complete": True,
                    "pii_included": False,
                    "kpis": {
                        "orders": total_pedidos,
                        "items": total_qtd,
                        "gross": total_valor,
                    },
                    "series": [
                        {
                            "label": period_label,
                            "orders": total_pedidos,
                            "items": total_qtd,
                            "gross": total_valor,
                        }
                    ],
                    "ranking": [
                        {
                            "sku": item.get("sku") or "",
                            "title": item.get("nome") or "Produto",
                            "quantity": item.get("qtd") or 0,
                            "gross": item.get("valor") or 0,
                        }
                        for item in top_skus
                        if isinstance(item, dict)
                    ],
                },
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter vendas por período: {exc}")
        return None


def _ia_tool_get_sales_quantity_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        if not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        quantidade_total = 0.0
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT SUM(quantidade) AS qtd
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchone()
                if row:
                    quantidade_total += float(row["qtd"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao sumarizar quantidade de vendas em {db_path}: {exc}")
                continue

        return {
            "function": "get_sales_quantity_by_period",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja_filtro or ""},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "quantidade_vendida_total": quantidade_total,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter quantidade de vendas por período: {exc}")
        return None


def _ia_extrair_mes_ano_mensagem(mensagem: str, contexto: Optional[dict] = None) -> Optional[str]:
    raw = str(mensagem or "")
    texto = _normalizar_texto(raw)

    m1 = re.search(r"\b(20\d{2})[/-](0?[1-9]|1[0-2])\b", raw)
    if m1:
        ano = int(m1.group(1))
        mes = int(m1.group(2))
        return f"{ano:04d}-{mes:02d}"

    m2 = re.search(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", raw)
    if m2:
        mes = int(m2.group(1))
        ano = int(m2.group(2))
        return f"{ano:04d}-{mes:02d}"

    meses = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }

    m3 = re.search(r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})?\b", texto)
    if m3:
        mes = int(meses.get(m3.group(1)) or 0)
        ano_txt = str(m3.group(2) or "").strip()
        if ano_txt:
            ano = int(ano_txt)
            return f"{ano:04d}-{mes:02d}"

        ctx = contexto if isinstance(contexto, dict) else {}
        ini_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or ""))
        fim_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or ""))
        ano_ref = None
        if ini_ctx:
            ano_ref = int(ini_ctx[:4])
        elif fim_ctx:
            ano_ref = int(fim_ctx[:4])
        else:
            ano_ref = dt.date.today().year
        return f"{int(ano_ref):04d}-{mes:02d}"

    m4 = re.search(r"\bMES\s*(0?[1-9]|1[0-2])(?:\s*DE\s*(20\d{2}))?\b", texto)
    if m4:
        mes = int(m4.group(1))
        ano_txt = str(m4.group(2) or "").strip()
        ano = int(ano_txt) if ano_txt else dt.date.today().year
        return f"{ano:04d}-{mes:02d}"

    return None


def _ia_periodo_mensal_padrao(client_id: str, loja: Optional[str] = None, meses: int = 12) -> tuple[str, str]:
    data_ref = _ia_obter_data_referencia_vendas(client_id, loja) or dt.date.today()
    ini_mes = _ia_subtrair_meses(dt.date(data_ref.year, data_ref.month, 1), max(0, int(meses or 12) - 1))
    fim_mes = _ia_ultimo_dia_mes(data_ref.year, data_ref.month)
    return ini_mes.isoformat(), fim_mes.isoformat()


def _ia_tool_get_sales_by_month_period(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str] = None,
    limite_meses: int = 24,
) -> Optional[dict]:
    try:
        data_inicio, data_fim = _ia_normalizar_periodo_chat(data_inicio, data_fim)
        if not data_inicio or not data_fim:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        meses: dict[str, dict] = {}

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT
                        SUBSTR(data, 1, 7) AS mes_ano,
                        SUM(COALESCE(quantidade, 0)) AS qtd,
                        SUM(COALESCE(valor, 0)) AS valor,
                        COUNT(DISTINCT numero) AS pedidos
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY SUBSTR(data, 1, 7)
                    ORDER BY SUBSTR(data, 1, 7)
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchall()
                for row in rows or []:
                    mes_ano = str(row["mes_ano"] or "").strip()
                    if not re.match(r"^\d{4}-\d{2}$", mes_ano):
                        continue
                    atual = meses.setdefault(mes_ano, {
                        "mes_ano": mes_ano,
                        "quantidade_vendida": 0.0,
                        "valor_vendido": 0.0,
                        "pedidos_total": 0,
                    })
                    atual["quantidade_vendida"] += float(row["qtd"] or 0)
                    atual["valor_vendido"] += float(row["valor"] or 0)
                    atual["pedidos_total"] += int(row["pedidos"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consolidar vendas mensais em {db_path}: {exc}")
                continue

        lista = sorted(meses.values(), key=lambda x: str(x.get("mes_ano") or ""))
        if not lista:
            return None

        limite_meses = max(1, min(int(limite_meses or 24), 60))
        lista = lista[-limite_meses:]
        mes_campeao_valor = max(lista, key=lambda x: float(x.get("valor_vendido") or 0))
        mes_campeao_qtd = max(lista, key=lambda x: float(x.get("quantidade_vendida") or 0))

        return {
            "function": "get_sales_by_month_period",
            "arguments": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "limite_meses": limite_meses,
            },
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "mes_campeao_valor": mes_campeao_valor,
                "mes_campeao_quantidade": mes_campeao_qtd,
                "meses": lista,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter vendas por mês: {exc}")
        return None


def _ia_tool_get_top_skus_sales_by_month(
    client_id: str,
    mes_ano: str,
    loja: Optional[str] = None,
    limite: int = 5,
) -> Optional[dict]:
    try:
        ini, fim = _ia_periodo_do_mes(mes_ano)
        if not ini or not fim:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        limite = max(1, min(int(limite or 5), 500))

        skus: dict[str, dict] = {}
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT
                        UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                        MAX(COALESCE(produto, '')) AS produto,
                        SUM(COALESCE(quantidade, 0)) AS qtd,
                        SUM(COALESCE(valor, 0)) AS valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                      AND TRIM(COALESCE(sku, '')) != ''
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [ini, fim, *params_loja],
                ).fetchall()
                for row in rows or []:
                    sku = str(row["sku"] or "").strip().upper()
                    if not sku:
                        continue
                    atual = skus.setdefault(sku, {
                        "sku": sku,
                        "produto": str(row["produto"] or "").strip(),
                        "quantidade_vendida": 0.0,
                        "valor_vendido": 0.0,
                    })
                    atual["quantidade_vendida"] += float(row["qtd"] or 0)
                    atual["valor_vendido"] += float(row["valor"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao obter top SKUs vendidos do mês em {db_path}: {exc}")
                continue

        itens = sorted(
            skus.values(),
            key=lambda x: (-float(x.get("quantidade_vendida") or 0), -float(x.get("valor_vendido") or 0), str(x.get("sku") or "")),
        )
        if not itens:
            return None

        return {
            "function": "get_top_skus_sales_by_month",
            "arguments": {
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": loja_filtro or "",
                "limite": limite,
            },
            "result": {
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": loja_filtro or "",
                "top_skus": itens[:limite],
                "sku_campeao": itens[0],
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter top SKUs vendidos no mês: {exc}")
        return None


def _ia_tool_get_top_skus_returns_by_month(
    client_id: str,
    mes_ano: str,
    loja: Optional[str] = None,
    limite: int = 5,
) -> Optional[dict]:
    try:
        ini, fim = _ia_periodo_do_mes(mes_ano)
        if not ini or not fim:
            return None

        limite = max(1, min(int(limite or 5), 500))
        resumo = _ia_devolucoes_db_resumo(client_id, ini, fim, loja, sku=None, limite_top_skus=limite)
        if not resumo:
            return None

        itens = resumo.get("top_skus") or []
        return {
            "function": "get_top_skus_returns_by_month",
            "arguments": {
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": resumo.get("loja") or "",
                "limite": limite,
            },
            "result": {
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": resumo.get("loja") or "",
                "top_skus": itens,
                "sku_campeao": (itens[0] if itens else {}),
                "quantidade_devolvida_total": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido_total": float(resumo.get("valor_devolvido_total") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter top SKUs devolvidos no mês: {exc}")
        return None


def _ia_tool_get_month_sales_returns_details(
    client_id: str,
    mes_ano: str,
    loja: Optional[str] = None,
    limite_skus: int = 200,
) -> Optional[dict]:
    try:
        ini, fim = _ia_periodo_do_mes(mes_ano)
        if not ini or not fim:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        limite_skus = max(1, min(int(limite_skus or 200), 500))
        vendas = _ia_tool_get_top_skus_sales_by_month(client_id, mes_ano, loja_filtro, limite=limite_skus)
        devolucoes = _ia_tool_get_top_skus_returns_by_month(client_id, mes_ano, loja_filtro, limite=limite_skus)
        vendas_result = (vendas or {}).get("result") or {}
        devolucoes_result = (devolucoes or {}).get("result") or {}

        vendas_por_sku = {
            str(item.get("sku") or "").strip().upper(): item
            for item in (vendas_result.get("top_skus") or [])
            if str(item.get("sku") or "").strip()
        }
        devolucoes_por_sku = {
            str(item.get("sku") or "").strip().upper(): item
            for item in (devolucoes_result.get("top_skus") or [])
            if str(item.get("sku") or "").strip()
        }
        todos_skus = sorted(set(vendas_por_sku) | set(devolucoes_por_sku))
        cadastro = _ia_produtos_info_por_sku(client_id, todos_skus)

        itens = []
        for sku in todos_skus:
            venda = vendas_por_sku.get(sku) or {}
            dev = devolucoes_por_sku.get(sku) or {}
            cad = cadastro.get(sku) or {}
            produto = (
                str(venda.get("produto") or "").strip()
                or str(dev.get("produto") or "").strip()
                or str(cad.get("produto_cadastro") or "").strip()
                or "-"
            )
            qtd_vendida = float(venda.get("quantidade_vendida") or 0)
            valor_vendido = float(venda.get("valor_vendido") or 0)
            qtd_devolvida = float(dev.get("quantidade_devolvida") or 0)
            valor_devolvido = float(dev.get("valor_devolvido") or 0)
            itens.append({
                "sku": sku,
                "produto": produto,
                "quantidade_vendida": qtd_vendida,
                "valor_vendido": valor_vendido,
                "quantidade_devolvida": qtd_devolvida,
                "valor_devolvido": valor_devolvido,
                "quantidade_liquida": qtd_vendida - qtd_devolvida,
                "valor_liquido": valor_vendido - valor_devolvido,
                "imagem_url": cad.get("imagem_url") or "",
                "categoria": cad.get("categoria") or "",
                "marca": cad.get("marca") or "",
            })

        itens.sort(key=lambda x: (-float(x.get("quantidade_vendida") or 0), -float(x.get("valor_vendido") or 0), str(x.get("sku") or "")))
        total_vendido = sum(float(i.get("quantidade_vendida") or 0) for i in itens)
        valor_vendido_total = sum(float(i.get("valor_vendido") or 0) for i in itens)
        total_devolvido = sum(float(i.get("quantidade_devolvida") or 0) for i in itens)
        valor_devolvido_total = sum(float(i.get("valor_devolvido") or 0) for i in itens)

        return {
            "function": "get_month_sales_returns_details",
            "arguments": {"mes_ano": mes_ano, "data_inicio": ini, "data_fim": fim, "loja": loja_filtro or "", "limite_skus": limite_skus},
            "result": {
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": loja_filtro or "",
                "totais": {
                    "quantidade_vendida": total_vendido,
                    "valor_vendido": valor_vendido_total,
                    "quantidade_devolvida": total_devolvido,
                    "valor_devolvido": valor_devolvido_total,
                    "quantidade_liquida": total_vendido - total_devolvido,
                    "valor_liquido": valor_vendido_total - valor_devolvido_total,
                    "total_skus": len(itens),
                },
                "sku_mais_vendido": vendas_result.get("sku_campeao") or {},
                "sku_mais_devolvido": devolucoes_result.get("sku_campeao") or {},
                "itens": itens[:limite_skus],
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao consolidar vendas e devolucoes do mes: {exc}")
        return None


def _ia_tool_get_sku_sales_by_month(
    client_id: str,
    sku: str,
    mes_ano: str,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        sku_ref = str(sku or "").strip().upper()
        ini, fim = _ia_periodo_do_mes(mes_ano)
        if not sku_ref or not ini or not fim:
            return None
        dados = _ia_vendas_db_consulta_sku_vendas_devolucoes(client_id, ini, fim, sku_ref, loja)
        if not dados:
            dados = {
                "sku": sku_ref,
                "produto": "",
                "data_inicio": ini,
                "data_fim": fim,
                "loja": loja or "",
                "quantidade_vendida": 0.0,
                "valor_vendido": 0.0,
                "quantidade_devolvida": 0.0,
                "valor_devolvido": 0.0,
                "ultima_venda": "",
                "ultima_devolucao": "",
            }
        qtd_vendida = float(dados.get("quantidade_vendida") or 0)
        valor_vendido = float(dados.get("valor_vendido") or 0)
        qtd_devolvida = float(dados.get("quantidade_devolvida") or 0)
        valor_devolvido = float(dados.get("valor_devolvido") or 0)
        return {
            "function": "get_sku_sales_by_month",
            "arguments": {"sku": sku_ref, "mes_ano": mes_ano, "data_inicio": ini, "data_fim": fim, "loja": loja or ""},
            "result": {
                "sku": sku_ref,
                "produto": dados.get("produto") or "",
                "mes_ano": mes_ano,
                "data_inicio": ini,
                "data_fim": fim,
                "loja": dados.get("loja") or loja or "",
                "quantidade_vendida": qtd_vendida,
                "valor_vendido": valor_vendido,
                "quantidade_devolvida": qtd_devolvida,
                "valor_devolvido": valor_devolvido,
                "quantidade_liquida": qtd_vendida - qtd_devolvida,
                "valor_liquido": valor_vendido - valor_devolvido,
                "ultima_venda": dados.get("ultima_venda") or dados.get("ultima_data") or "",
                "ultima_devolucao": dados.get("ultima_devolucao") or "",
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao consultar SKU por mes: {exc}")
        return None


def _ia_extrair_meses_ano_mensagem(mensagem: str, contexto: Optional[dict] = None) -> list[str]:
    raw = str(mensagem or "")
    texto = _normalizar_texto(raw)
    meses_map = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }
    encontrados: list[str] = []

    for ano, mes in re.findall(r"\b(20\d{2})[/-](0?[1-9]|1[0-2])\b", raw):
        encontrados.append(f"{int(ano):04d}-{int(mes):02d}")
    for mes, ano in re.findall(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", raw):
        encontrados.append(f"{int(ano):04d}-{int(mes):02d}")

    ano_ref = None
    ctx = contexto if isinstance(contexto, dict) else {}
    ini_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or ""))
    fim_ctx = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or ""))
    if ini_ctx:
        ano_ref = int(ini_ctx[:4])
    elif fim_ctx:
        ano_ref = int(fim_ctx[:4])
    else:
        ano_ref = dt.date.today().year

    padrao_mes = r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})?\b"
    for match in re.finditer(padrao_mes, texto):
        nome_mes = match.group(1)
        mes_num = meses_map.get(nome_mes)
        if not mes_num:
            continue
        ano = int(match.group(2) or ano_ref)
        encontrados.append(f"{ano:04d}-{mes_num:02d}")

    return list(dict.fromkeys(encontrados))


def _ia_tool_compare_sku_sales_months(
    client_id: str,
    sku: str,
    meses_ano: list[str],
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        sku_ref = str(sku or "").strip().upper()
        meses = [m for m in (meses_ano or []) if re.match(r"^\d{4}-\d{2}$", str(m or ""))]
        meses = list(dict.fromkeys(meses))[:12]
        if not sku_ref or len(meses) < 2:
            return None

        itens = []
        for mes in meses:
            item = _ia_tool_get_sku_sales_by_month(client_id, sku_ref, mes, loja)
            if item:
                itens.append((item.get("result") or {}))
        if len(itens) < 2:
            return None

        base = itens[0]
        ultimo = itens[-1]
        qtd_base = float(base.get("quantidade_vendida") or 0)
        qtd_ult = float(ultimo.get("quantidade_vendida") or 0)
        valor_base = float(base.get("valor_vendido") or 0)
        valor_ult = float(ultimo.get("valor_vendido") or 0)

        def pct(novo, antigo):
            if float(antigo or 0) == 0:
                return None
            return ((float(novo or 0) - float(antigo or 0)) / float(antigo or 0)) * 100.0

        return {
            "function": "compare_sku_sales_months",
            "arguments": {"sku": sku_ref, "meses_ano": meses, "loja": loja or ""},
            "result": {
                "sku": sku_ref,
                "loja": loja or "",
                "meses": itens,
                "comparativo_primeiro_ultimo": {
                    "mes_base": base.get("mes_ano") or "",
                    "mes_comparado": ultimo.get("mes_ano") or "",
                    "variacao_quantidade": qtd_ult - qtd_base,
                    "variacao_quantidade_percentual": pct(qtd_ult, qtd_base),
                    "variacao_valor": valor_ult - valor_base,
                    "variacao_valor_percentual": pct(valor_ult, valor_base),
                },
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao comparar SKU entre meses: {exc}")
        return None


def _ia_devolucoes_db_resumo(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str] = None,
    sku: Optional[str] = None,
    limite_top_skus: int = 10,
) -> Optional[dict]:
    try:
        data_inicio, data_fim = _ia_normalizar_periodo_chat(data_inicio, data_fim)
        if not data_inicio or not data_fim:
            return None

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        sku_ref = str(sku or "").strip().upper()
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        total_qtd = 0.0
        total_valor = 0.0
        ultima_devolucao = ""
        produto_sku = ""
        top_skus: dict[str, dict] = {}
        bancos_consultados = 0

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                existe = cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='notas_entrada_itens'"
                ).fetchone()
                if not existe:
                    conn.close()
                    continue

                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_notas_entrada(cur, loja_filtro)

                sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
                params_total = [data_inicio, data_fim]
                params_total.extend(filtro_loja_params)
                if sku_ref:
                    params_total.append(sku_ref)

                row = cur.execute(
                    f"""
                    SELECT
                        SUM(COALESCE(quantidade, 0)) AS qtd_total,
                        SUM(COALESCE(valor_total, 0)) AS valor_total,
                        MAX(date(data_emissao)) AS ultima_data,
                        MAX(COALESCE(descricao, '')) AS produto
                    FROM notas_entrada_itens
                    WHERE devolucao = 1
                      AND date(data_emissao) BETWEEN ? AND ?
                      {filtro_loja_sql}
                      {sku_sql}
                    """,
                    params_total,
                ).fetchone()

                if row:
                    total_qtd += float(row["qtd_total"] or 0)
                    total_valor += float(row["valor_total"] or 0)
                    data_row = str(row["ultima_data"] or "").strip()
                    if data_row and data_row > ultima_devolucao:
                        ultima_devolucao = data_row
                    prod_row = str(row["produto"] or "").strip()
                    if prod_row and not produto_sku:
                        produto_sku = prod_row

                if not sku_ref:
                    rows_top = cur.execute(
                        f"""
                        SELECT
                            UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                            MAX(COALESCE(descricao, '')) AS produto,
                            SUM(COALESCE(quantidade, 0)) AS qtd,
                            SUM(COALESCE(valor_total, 0)) AS valor
                        FROM notas_entrada_itens
                        WHERE devolucao = 1
                          AND date(data_emissao) BETWEEN ? AND ?
                          {filtro_loja_sql}
                          AND TRIM(COALESCE(sku, '')) != ''
                        GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                        """,
                        [data_inicio, data_fim, *filtro_loja_params],
                    ).fetchall()

                    for r in rows_top or []:
                        sku_item = str(r["sku"] or "").strip().upper()
                        if not sku_item:
                            continue
                        atual = top_skus.setdefault(sku_item, {
                            "sku": sku_item,
                            "produto": str(r["produto"] or "").strip(),
                            "quantidade_devolvida": 0.0,
                            "valor_devolvido": 0.0,
                        })
                        atual["quantidade_devolvida"] += float(r["qtd"] or 0)
                        atual["valor_devolvido"] += float(r["valor"] or 0)

                bancos_consultados += 1
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar devoluções em {db_path}: {exc}")
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                continue

        lista_top = sorted(
            top_skus.values(),
            key=lambda x: (-float(x.get("valor_devolvido") or 0), -float(x.get("quantidade_devolvida") or 0), str(x.get("sku") or "")),
        )

        return {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja_filtro or "",
            "sku": sku_ref,
            "produto": produto_sku,
            "quantidade_devolvida_total": total_qtd,
            "valor_devolvido_total": total_valor,
            "ultima_devolucao": ultima_devolucao,
            "top_skus": lista_top[: max(0, int(limite_top_skus or 0))],
            "bancos_consultados": bancos_consultados,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao resumir devoluções: {exc}")
        return None


def _ia_tool_get_returns_quantity_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja, sku=None, limite_top_skus=0)
        if not resumo:
            return None

        return {
            "function": "get_returns_quantity_by_period",
            "arguments": {"data_inicio": resumo.get("data_inicio") or "", "data_fim": resumo.get("data_fim") or "", "loja": resumo.get("loja") or ""},
            "result": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida_total": float(resumo.get("quantidade_devolvida_total") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter quantidade de devoluções por período: {exc}")
        return None


def _ia_tool_get_returns_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None, limite: int = 10) -> Optional[dict]:
    try:
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja, sku=None, limite_top_skus=max(1, min(int(limite or 10), 50)))
        if not resumo:
            return None
        return {
            "function": "get_returns_by_period",
            "arguments": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "limite": max(1, min(int(limite or 10), 50)),
            },
            "result": {
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida_total": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido_total": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "top_skus": resumo.get("top_skus") or [],
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter resumo de devoluções por período: {exc}")
        return None


def _ia_obter_data_referencia_vendas(client_id: str, loja: Optional[str] = None) -> Optional[dt.date]:
    try:
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        maior = None
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT MAX(data) AS data_max
                    FROM vendas
                    WHERE LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    """,
                    [*params_loja],
                ).fetchone()
                conn.close()
                data_txt = str((row or {}).get("data_max") or "").strip()
                if not data_txt:
                    continue
                data_ref = dt.date.fromisoformat(data_txt)
                if maior is None or data_ref > maior:
                    maior = data_ref
            except Exception:
                continue
        return maior
    except Exception:
        return None


def _ia_tool_get_stockout_forecast(
    client_id: str,
    mensagem: str,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
    lookback_days: int = 30,
    limite: int = 100,
) -> Optional[dict]:
    try:
        lookback_days = max(7, min(int(lookback_days or 30), 180))
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None

        df_prod = _ia_carregar_produtos_tool_df(client_id)
        if df_prod is None or df_prod.empty:
            return None

        df_valid = df_prod.copy()
        df_valid["sku_norm"] = df_valid["sku_norm"].astype(str).str.strip().str.upper()
        df_valid = df_valid[df_valid["sku_norm"] != ""]
        if df_valid.empty:
            return None

        # Agrega saldo por SKU para evitar linhas duplicadas por origem.
        mapa_estoque = {}
        for _, row in df_valid.iterrows():
            sku = str(row.get("sku_norm") or "").strip().upper()
            if not sku:
                continue
            atual = mapa_estoque.setdefault(sku, {
                "sku": sku,
                "nome": str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
                "saldo_loja": 0.0,
                "saldo_full": 0.0,
            })
            atual["saldo_loja"] += _ia_tool_float(row.get("saldo_loja"))
            atual["saldo_full"] += _ia_tool_float(row.get("saldo_full"))

        data_ref = _ia_obter_data_referencia_vendas(client_id, loja_filtro) or dt.date.today()
        data_inicio = (data_ref - dt.timedelta(days=lookback_days - 1)).isoformat()
        data_fim = data_ref.isoformat()

        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        vendas_por_sku: dict[str, float] = {}
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                           SUM(COALESCE(quantidade, 0)) AS qtd
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchall()
                conn.close()
                for row in rows or []:
                    sku = str(row["sku"] or "").strip().upper()
                    if not sku:
                        continue
                    vendas_por_sku[sku] = float(vendas_por_sku.get(sku) or 0) + float(row["qtd"] or 0)
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao calcular consumo por SKU em {db_path}: {exc}")
                continue

        sku_especifico = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if sku_especifico and sku_especifico not in mapa_estoque:
            sku_especifico = ""
        skus_alvo = [sku_especifico] if sku_especifico else list(mapa_estoque.keys())
        previsoes = []

        for sku in skus_alvo:
            item = mapa_estoque.get(sku)
            if not item:
                continue
            saldo_total = float(item.get("saldo_loja") or 0) + float(item.get("saldo_full") or 0)
            qtd_vendida = float(vendas_por_sku.get(sku) or 0)
            media_dia = qtd_vendida / float(lookback_days)

            dias_ate_ruptura = None
            data_ruptura = ""
            risco = "sem_consumo"

            if media_dia > 0:
                dias_ate_ruptura = saldo_total / media_dia if saldo_total > 0 else 0.0
                dias_arr = max(0, int(math.ceil(dias_ate_ruptura)))
                data_ruptura = (data_ref + dt.timedelta(days=dias_arr)).isoformat()
                if dias_ate_ruptura <= 7:
                    risco = "critico"
                elif dias_ate_ruptura <= 15:
                    risco = "alto"
                elif dias_ate_ruptura <= 30:
                    risco = "medio"
                else:
                    risco = "baixo"

            previsoes.append({
                "sku": sku,
                "nome": item.get("nome") or "",
                "saldo_loja": float(item.get("saldo_loja") or 0),
                "saldo_full": float(item.get("saldo_full") or 0),
                "saldo_total": saldo_total,
                "quantidade_vendida_janela": qtd_vendida,
                "media_venda_dia": media_dia,
                "dias_ate_ruptura": dias_ate_ruptura,
                "data_prevista_ruptura": data_ruptura,
                "risco_ruptura": risco,
            })

        if not previsoes:
            return None

        previsoes_ordenadas = sorted(
            previsoes,
            key=lambda x: (
                float(x.get("dias_ate_ruptura") if x.get("dias_ate_ruptura") is not None else 10**9),
                -float(x.get("media_venda_dia") or 0),
            ),
        )

        if sku_especifico:
            retorno = previsoes_ordenadas[0]
        else:
            retorno = {
                "data_referencia": data_ref.isoformat(),
                "janela_dias": lookback_days,
                "loja": loja_filtro or "",
                "itens": previsoes_ordenadas[:max(1, limite)],
                "total_skus_analisados": len(previsoes_ordenadas),
            }

        retorno["chart_data"] = _ia_stockout_chart_data(retorno)
        return {
            "function": "get_stockout_forecast",
            "arguments": {
                "sku": sku_especifico or "",
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "janela_dias": lookback_days,
                "loja": loja_filtro or "",
            },
            "result": retorno,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao prever ruptura de estoque: {exc}")
        return None


def _ia_metricas_periodo_raw(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        data_inicio, data_fim = _ia_normalizar_periodo_chat(data_inicio, data_fim)
        if not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)

        vendas_qtd = 0.0
        vendas_valor = 0.0
        pedidos_total = 0
        devol_qtd = 0.0
        devol_valor = 0.0

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN COALESCE(quantidade, 0) ELSE 0 END) AS vendas_qtd,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN COALESCE(valor, 0) ELSE 0 END) AS vendas_valor,
                        COUNT(DISTINCT CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN numero END) AS pedidos,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(quantidade, 0) ELSE 0 END) AS devol_qtd,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(valor, 0) ELSE 0 END) AS devol_valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      {sql_loja}
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchone()
                if row:
                    vendas_qtd += float(row["vendas_qtd"] or 0)
                    vendas_valor += float(row["vendas_valor"] or 0)
                    pedidos_total += int(row["pedidos"] or 0)
                    devol_qtd += float(row["devol_qtd"] or 0)
                    devol_valor += float(row["devol_valor"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao calcular métricas do período em {db_path}: {exc}")
                continue

        # Devoluções oficiais vêm de notas_entrada_itens; usa esse resumo para evitar subnotificação.
        resumo_devolucoes = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja_filtro, sku=None, limite_top_skus=0)
        if resumo_devolucoes:
            devol_qtd = float(resumo_devolucoes.get("quantidade_devolvida_total") or 0)
            devol_valor = float(resumo_devolucoes.get("valor_devolvido_total") or 0)

        ticket_medio = (vendas_valor / pedidos_total) if pedidos_total > 0 else 0.0
        taxa_devolucao_qtd = (devol_qtd / vendas_qtd * 100.0) if vendas_qtd > 0 else 0.0
        taxa_devolucao_valor = (devol_valor / vendas_valor * 100.0) if vendas_valor > 0 else 0.0

        return {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja_filtro or "",
            "quantidade_vendida_total": vendas_qtd,
            "valor_vendido_total": vendas_valor,
            "pedidos_total": pedidos_total,
            "quantidade_devolvida_total": devol_qtd,
            "valor_devolvido_total": devol_valor,
            "ticket_medio": ticket_medio,
            "taxa_devolucao_quantidade_percentual": taxa_devolucao_qtd,
            "taxa_devolucao_valor_percentual": taxa_devolucao_valor,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao calcular métricas do período: {exc}")
        return None


def _ia_tool_get_avg_ticket_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    metricas = _ia_metricas_periodo_raw(client_id, data_inicio, data_fim, loja)
    if not metricas:
        return None
    return {
        "function": "get_avg_ticket_by_period",
        "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": metricas.get("loja") or ""},
        "result": {
            "data_inicio": metricas.get("data_inicio"),
            "data_fim": metricas.get("data_fim"),
            "loja": metricas.get("loja") or "",
            "ticket_medio": float(metricas.get("ticket_medio") or 0),
            "pedidos_total": int(metricas.get("pedidos_total") or 0),
            "valor_vendido_total": float(metricas.get("valor_vendido_total") or 0),
        },
    }


def _ia_tool_get_return_rate_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    metricas = _ia_metricas_periodo_raw(client_id, data_inicio, data_fim, loja)
    if not metricas:
        return None
    return {
        "function": "get_return_rate_by_period",
        "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": metricas.get("loja") or ""},
        "result": {
            "data_inicio": metricas.get("data_inicio"),
            "data_fim": metricas.get("data_fim"),
            "loja": metricas.get("loja") or "",
            "quantidade_vendida_total": float(metricas.get("quantidade_vendida_total") or 0),
            "quantidade_devolvida_total": float(metricas.get("quantidade_devolvida_total") or 0),
            "valor_vendido_total": float(metricas.get("valor_vendido_total") or 0),
            "valor_devolvido_total": float(metricas.get("valor_devolvido_total") or 0),
            "taxa_devolucao_quantidade_percentual": float(metricas.get("taxa_devolucao_quantidade_percentual") or 0),
            "taxa_devolucao_valor_percentual": float(metricas.get("taxa_devolucao_valor_percentual") or 0),
        },
    }


def _ia_tool_get_period_comparison(
    client_id: str,
    data_inicio_a: str,
    data_fim_a: str,
    data_inicio_b: str,
    data_fim_b: str,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        # O contrato visual e os percentuais usam sempre a ordem cronologica.
        # Assim, mesmo que o agente envie A=atual e B=anterior, o resultado
        # representa de forma consistente anterior -> atual.
        if str(data_inicio_a or "") > str(data_inicio_b or ""):
            data_inicio_a, data_inicio_b = data_inicio_b, data_inicio_a
            data_fim_a, data_fim_b = data_fim_b, data_fim_a
        pa = _ia_metricas_periodo_raw(client_id, data_inicio_a, data_fim_a, loja)
        pb = _ia_metricas_periodo_raw(client_id, data_inicio_b, data_fim_b, loja)
        if not pa or not pb:
            return None

        def _delta_pct(valor_a: float, valor_b: float) -> float:
            if abs(valor_a) < 1e-9:
                return 0.0 if abs(valor_b) < 1e-9 else 100.0
            return ((valor_b - valor_a) / valor_a) * 100.0

        qtd_a = float(pa.get("quantidade_vendida_total") or 0)
        qtd_b = float(pb.get("quantidade_vendida_total") or 0)
        valor_a = float(pa.get("valor_vendido_total") or 0)
        valor_b = float(pb.get("valor_vendido_total") or 0)
        pedidos_a = int(pa.get("pedidos_total") or 0)
        pedidos_b = int(pb.get("pedidos_total") or 0)
        label_a = f"{data_inicio_a} a {data_fim_a}"
        label_b = f"{data_inicio_b} a {data_fim_b}"
        chart_data = {
            "schema": "jk.sales.period_comparison.v1",
            "analysis_type": "sales_period_comparison",
            "title": "Comparacao de vendas entre periodos",
            "source": "Historico de vendas do JK Sistema",
            "period_start": data_inicio_a,
            "period_end": data_fim_b,
            "store": pa.get("loja") or loja or "Todas as lojas",
            "coverage_complete": True,
            "pii_included": False,
            "kpis": {
                "Periodos comparados": 2,
                "Faturamento mais recente": valor_b,
                "Pedidos mais recentes": pedidos_b,
                "Itens mais recentes": qtd_b,
            },
            "series": [
                {
                    "label": label_a,
                    "orders": pedidos_a,
                    "items": qtd_a,
                    "gross": valor_a,
                },
                {
                    "label": label_b,
                    "orders": pedidos_b,
                    "items": qtd_b,
                    "gross": valor_b,
                },
            ],
            "ranking": [],
        }

        return {
            "function": "get_period_comparison",
            "arguments": {
                "data_inicio_a": data_inicio_a,
                "data_fim_a": data_fim_a,
                "data_inicio_b": data_inicio_b,
                "data_fim_b": data_fim_b,
                "loja": pa.get("loja") or "",
            },
            "result": {
                "periodo_a": {
                    "data_inicio": data_inicio_a,
                    "data_fim": data_fim_a,
                    "quantidade_vendida_total": qtd_a,
                    "valor_vendido_total": valor_a,
                    "pedidos_total": pedidos_a,
                },
                "periodo_b": {
                    "data_inicio": data_inicio_b,
                    "data_fim": data_fim_b,
                    "quantidade_vendida_total": qtd_b,
                    "valor_vendido_total": valor_b,
                    "pedidos_total": pedidos_b,
                },
                "comparativo": {
                    "variacao_quantidade": qtd_b - qtd_a,
                    "variacao_quantidade_percentual": _delta_pct(qtd_a, qtd_b),
                    "variacao_faturamento": valor_b - valor_a,
                    "variacao_faturamento_percentual": _delta_pct(valor_a, valor_b),
                    "variacao_pedidos": pedidos_b - pedidos_a,
                    "variacao_pedidos_percentual": _delta_pct(float(pedidos_a), float(pedidos_b)),
                },
                "chart_data": chart_data,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao comparar períodos: {exc}")
        return None


def _ia_vendas_timeseries_raw(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> list[dict]:
    try:
        if not data_inicio or not data_fim:
            return []
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return []

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        serie: dict[str, dict] = {}

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT
                        data,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN COALESCE(quantidade, 0) ELSE 0 END) AS vendas_qtd,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0
                                  AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                                 THEN COALESCE(valor, 0) ELSE 0 END) AS vendas_valor,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(quantidade, 0) ELSE 0 END) AS devol_qtd,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(valor, 0) ELSE 0 END) AS devol_valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      {sql_loja}
                    GROUP BY data
                    ORDER BY data
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchall()
                for row in rows or []:
                    data_ref = str(row["data"] or "").strip()
                    if not data_ref:
                        continue
                    atual = serie.setdefault(data_ref, {
                        "data": data_ref,
                        "quantidade_vendida": 0.0,
                        "valor_vendido": 0.0,
                        "quantidade_devolvida": 0.0,
                        "valor_devolvido": 0.0,
                    })
                    atual["quantidade_vendida"] += float(row["vendas_qtd"] or 0)
                    atual["valor_vendido"] += float(row["vendas_valor"] or 0)
                    atual["quantidade_devolvida"] += float(row["devol_qtd"] or 0)
                    atual["valor_devolvido"] += float(row["devol_valor"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao montar série temporal em {db_path}: {exc}")
                continue

        return sorted(serie.values(), key=lambda item: item.get("data") or "")
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao montar série temporal: {exc}")
        return []


def _ia_tool_get_sales_timeseries(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    pontos = _ia_vendas_timeseries_raw(client_id, data_inicio, data_fim, loja)
    if not pontos:
        return None
    loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else ""
    total_items = sum(float(item.get("quantidade_vendida") or 0) for item in pontos)
    total_gross = sum(float(item.get("valor_vendido") or 0) for item in pontos)
    total_refunds = sum(float(item.get("valor_devolvido") or 0) for item in pontos)
    return {
        "function": "get_sales_timeseries",
        "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja_filtro},
        "result": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja_filtro,
            "pontos": pontos,
            "chart_data": {
                "schema": "jk.sales.timeseries.v1",
                "analysis_type": "sales",
                "title": "Evolucao das vendas",
                "source": "Historico de vendas do JK Sistema",
                "period_start": data_inicio,
                "period_end": data_fim,
                "store": loja_filtro or "Todas as lojas",
                "coverage_complete": True,
                "pii_included": False,
                "kpis": {
                    "items": total_items,
                    "gross": total_gross,
                    "refunds": total_refunds,
                    "net": total_gross - total_refunds,
                },
                "series": [
                    {
                        "date": item.get("data") or "",
                        "items": item.get("quantidade_vendida") or 0,
                        "gross": item.get("valor_vendido") or 0,
                        "refunds": item.get("valor_devolvido") or 0,
                        "net": float(item.get("valor_vendido") or 0) - float(item.get("valor_devolvido") or 0),
                    }
                    for item in pontos
                    if isinstance(item, dict)
                ],
                "ranking": [],
            },
        },
    }


def _ia_tool_detect_sales_anomalies(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        pontos = _ia_vendas_timeseries_raw(client_id, data_inicio, data_fim, loja)
        if len(pontos) < 7:
            return None

        valores = [float(p.get("valor_vendido") or 0) for p in pontos]
        media = sum(valores) / len(valores) if valores else 0.0
        variancia = sum((v - media) ** 2 for v in valores) / len(valores) if valores else 0.0
        desvio = math.sqrt(variancia)
        limiar_alto = media + 2 * desvio
        limiar_baixo = max(0.0, media - 2 * desvio)

        alertas = []
        for p in pontos:
            valor = float(p.get("valor_vendido") or 0)
            data_ref = str(p.get("data") or "")
            if valor > limiar_alto:
                alertas.append({"data": data_ref, "tipo": "pico", "valor_vendido": valor})
            elif valor < limiar_baixo:
                alertas.append({"data": data_ref, "tipo": "queda", "valor_vendido": valor})

        return {
            "function": "detect_sales_anomalies",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or ""},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja or "",
                "media_valor_diario": media,
                "desvio_padrao_valor_diario": desvio,
                "limiar_superior": limiar_alto,
                "limiar_inferior": limiar_baixo,
                "alertas": alertas[:20],
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao detectar anomalias de vendas: {exc}")
        return None


def _ia_tool_get_profit_by_period(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        if not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        df_prod = _ia_carregar_produtos_tool_df(client_id)
        custo_por_sku: dict[str, float] = {}
        imposto_por_sku: dict[str, float] = {}
        if df_prod is not None and not df_prod.empty:
            for _, row in df_prod.iterrows():
                sku = str(row.get("sku_norm") or "").strip().upper()
                if not sku:
                    continue
                custo_raw = row.get("custo")
                imposto_raw = row.get("imposto")
                custo_presente = custo_raw is not None and str(custo_raw).strip().lower() not in {"", "nan", "none", "null"}
                imposto_presente = imposto_raw is not None and str(imposto_raw).strip().lower() not in {"", "nan", "none", "null"}
                if sku not in custo_por_sku and custo_presente:
                    custo_por_sku[sku] = _ia_tool_float(custo_raw)
                if sku not in imposto_por_sku and imposto_presente:
                    imposto_por_sku[sku] = _ia_tool_float(imposto_raw)

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        agregados: dict[str, dict] = {}
        faturamento_total = 0.0

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                           MAX(produto) AS produto,
                           SUM(COALESCE(quantidade, 0)) AS qtd,
                           SUM(COALESCE(valor, 0)) AS valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchall()

                for row in rows or []:
                    sku = str(row["sku"] or "").strip().upper()
                    if not sku:
                        continue
                    atual = agregados.setdefault(sku, {
                        "sku": sku,
                        "produto": str(row["produto"] or "").strip(),
                        "qtd": 0.0,
                        "valor": 0.0,
                    })
                    atual["qtd"] += float(row["qtd"] or 0)
                    atual["valor"] += float(row["valor"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao calcular lucro por SKU em {db_path}: {exc}")
                continue

        custo_total_estimado = 0.0
        imposto_total_estimado = 0.0
        faturamento_com_custo = 0.0
        cobertura_sku_com_custo = 0
        for sku, item in agregados.items():
            qtd = float(item.get("qtd") or 0)
            valor = float(item.get("valor") or 0)
            faturamento_total += valor
            custo_unit = float(custo_por_sku.get(sku) or 0)
            imposto_pct = float(imposto_por_sku.get(sku) or 0)
            if sku in custo_por_sku and sku in imposto_por_sku:
                cobertura_sku_com_custo += 1
                faturamento_com_custo += valor
                custo_total_estimado += qtd * custo_unit
                imposto_total_estimado += valor * (imposto_pct / 100.0)

        cobertura_faturamento = (faturamento_com_custo / faturamento_total) if faturamento_total > 0 else 0.0
        dados_suficientes = bool(faturamento_total > 0 and cobertura_faturamento >= 0.95)
        lucro_parcial = faturamento_com_custo - custo_total_estimado - imposto_total_estimado
        margem_parcial = (lucro_parcial / faturamento_com_custo * 100.0) if faturamento_com_custo > 0 else None

        return {
            "function": "get_profit_by_period",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja_filtro or ""},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "faturamento_total": faturamento_total,
                "faturamento_com_custo": faturamento_com_custo,
                "custo_total_estimado": custo_total_estimado,
                "imposto_total_estimado": imposto_total_estimado,
                "lucro_estimado": lucro_parcial if dados_suficientes else None,
                "margem_percentual_estimada": margem_parcial if dados_suficientes else None,
                "lucro_estimado_parcial": lucro_parcial if faturamento_com_custo > 0 else None,
                "margem_percentual_parcial": margem_parcial,
                "cobertura_faturamento_percentual": cobertura_faturamento * 100.0,
                "dados_suficientes": dados_suficientes,
                "status_margem": "disponivel" if dados_suficientes else "indisponivel_dados_insuficientes",
                "skus_considerados": len(agregados),
                "skus_com_custo": cobertura_sku_com_custo,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao calcular lucro por período: {exc}")
        return None


def _ia_chat_scope_notice(client_id: str, loja: Optional[str]) -> Optional[dict]:
    if loja and str(loja).strip() not in ("", "__todas", "Todas as lojas"):
        return None
    try:
        tenant_path = get_tenant_path(client_id)
        lojas = []
        if os.path.exists(tenant_path):
            for nome in sorted(os.listdir(tenant_path)):
                if nome.startswith("vendas_historico_") and nome.endswith(".db") and ".backup_" not in nome:
                    slug = nome[len("vendas_historico_"):-3]
                    if slug:
                        lojas.append(slug.replace("_", " "))
        if not lojas:
            return None
        return {
            "function": "store_scope_notice",
            "arguments": {"loja": ""},
            "result": {
                "scope": "all_stores",
                "lojas_disponiveis": lojas,
                "message": "Loja não informada; consulta consolidada em todas as lojas. Se quiser, pergunte ao usuário qual loja específica deseja analisar.",
            },
        }
    except Exception:
        return None


def _ia_tool_get_returns_data(
    client_id: str,
    mensagem: str,
    contexto: Optional[dict] = None,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        ctx = contexto if isinstance(contexto, dict) else {}
        data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, ctx)
        loja_msg = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, ctx)
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else loja_msg
        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja_filtro, sku=sku or None, limite_top_skus=10)
        if not resumo:
            return None

        return {
            "function": "get_returns_data",
            "arguments": {
                "sku": sku or "",
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
            },
            "result": {
                "sku": sku or "",
                "produto": resumo.get("produto") or "",
                "quantidade_devolvida": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "top_skus": resumo.get("top_skus") or [],
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter devoluções: {exc}")
        return None


def _ia_tool_get_returns_by_sku_period(
    client_id: str,
    mensagem: str,
    contexto: Optional[dict] = None,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
) -> Optional[dict]:
    try:
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku:
            return None

        ctx = contexto if isinstance(contexto, dict) else {}
        data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, ctx)
        loja_msg = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, ctx)
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else loja_msg

        resumo = _ia_devolucoes_db_resumo(client_id, data_inicio, data_fim, loja_filtro, sku=sku, limite_top_skus=0)
        if not resumo:
            return None

        return {
            "function": "get_returns_by_sku_period",
            "arguments": {
                "sku": sku,
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
            },
            "result": {
                "sku": sku,
                "produto": resumo.get("produto") or "",
                "data_inicio": resumo.get("data_inicio") or "",
                "data_fim": resumo.get("data_fim") or "",
                "loja": resumo.get("loja") or "",
                "quantidade_devolvida": float(resumo.get("quantidade_devolvida_total") or 0),
                "valor_devolvido": float(resumo.get("valor_devolvido_total") or 0),
                "ultima_devolucao": resumo.get("ultima_devolucao") or "",
                "bancos_consultados": int(resumo.get("bancos_consultados") or 0),
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter devoluções por SKU/período: {exc}")
        return None


def _ia_vendas_db_consulta_sku_vendas_devolucoes(client_id: str, data_inicio: str, data_fim: str, sku: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        sku_ref = str(sku or "").strip().upper()
        if not sku_ref or not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        agregado = {
            "sku": sku_ref,
            "produto": "",
            "quantidade_vendida": 0.0,
            "valor_vendido": 0.0,
            "quantidade_devolvida": 0.0,
            "valor_devolvido": 0.0,
            "ultima_venda": "",
            "ultima_devolucao": "",
            "bancos_consultados": 0,
        }
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = cur.execute(
                    f"""
                    SELECT
                        MAX(produto) AS produto,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0 THEN COALESCE(quantidade, 0) ELSE 0 END) AS qtd_vendida,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 0 THEN COALESCE(valor, 0) ELSE 0 END) AS valor_vendido,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(quantidade, 0) ELSE 0 END) AS qtd_devolvida,
                        SUM(CASE WHEN COALESCE(devolucao, 0) = 1 THEN COALESCE(valor, 0) ELSE 0 END) AS valor_devolvido,
                        MAX(CASE WHEN COALESCE(devolucao, 0) = 0 THEN data END) AS ultima_venda,
                        MAX(CASE WHEN COALESCE(devolucao, 0) = 1 THEN data END) AS ultima_devolucao
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND UPPER(TRIM(COALESCE(sku, ''))) = ?
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    """,
                    [data_inicio, data_fim, sku_ref, *params_loja],
                ).fetchone()
                if row:
                    agregado["produto"] = agregado["produto"] or str(row["produto"] or "").strip()
                    agregado["quantidade_vendida"] += float(row["qtd_vendida"] or 0)
                    agregado["valor_vendido"] += float(row["valor_vendido"] or 0)
                    agregado["quantidade_devolvida"] += float(row["qtd_devolvida"] or 0)
                    agregado["valor_devolvido"] += float(row["valor_devolvido"] or 0)
                    venda_data = str(row["ultima_venda"] or "").strip()
                    dev_data = str(row["ultima_devolucao"] or "").strip()
                    if venda_data and venda_data > str(agregado["ultima_venda"] or ""):
                        agregado["ultima_venda"] = venda_data
                    if dev_data and dev_data > str(agregado["ultima_devolucao"] or ""):
                        agregado["ultima_devolucao"] = dev_data
                agregado["bancos_consultados"] += 1
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar vendas/devoluções SKU {sku_ref} em {db_path}: {exc}")
                continue

        if agregado["quantidade_vendida"] <= 0 and agregado["quantidade_devolvida"] <= 0:
            return None
        return agregado
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha na consulta de vendas+devoluções por SKU: {exc}")
        return None


def _ia_vendas_db_consulta_sku(client_id: str, data_inicio: str, data_fim: str, sku: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        sku_ref = str(sku or "").strip().upper()
        if not sku_ref or not data_inicio or not data_fim:
            return None
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        agregado = {
            "sku": sku_ref,
            "produto": "",
            "quantidade": 0.0,
            "valor": 0.0,
            "ultima_data": "",
            "bancos_consultados": 0,
        }
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT sku, produto,
                           SUM(quantidade) AS qtd,
                           SUM(valor) AS valor,
                           MAX(data) AS ultima_data
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND UPPER(TRIM(COALESCE(sku, ''))) = ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY sku, produto
                    """,
                    [data_inicio, data_fim, sku_ref, *params_loja],
                )
                for row in cur.fetchall() or []:
                    agregado["produto"] = agregado["produto"] or str(row["produto"] or "").strip()
                    agregado["quantidade"] += float(row["qtd"] or 0)
                    agregado["valor"] += float(row["valor"] or 0)
                    data_row = str(row["ultima_data"] or "").strip()
                    if data_row and data_row > str(agregado["ultima_data"] or ""):
                        agregado["ultima_data"] = data_row
                agregado["bancos_consultados"] += 1
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar SKU {sku_ref} em {db_path}: {exc}")
                continue

        if agregado["quantidade"] <= 0:
            return None
        return agregado
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha na consulta de vendas por SKU: {exc}")
        return None


def _ia_extrair_item_ids_ml(*valores) -> list[str]:
    ids = []
    vistos = set()
    for valor in valores:
        texto = str(valor or "").upper()
        for match in re.findall(r"MLB[\s_-]*\d+", texto):
            item_id = re.sub(r"[\s_-]+", "", match)
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                ids.append(item_id)
    return ids


def _ia_lojas_ml_conectadas(client_id: str) -> list[str]:
    lojas = []
    for loja in carregar_lojas(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = str(loja.get("nome") or "").strip()
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        if nome and isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip():
            lojas.append(nome)
    return lojas


def _ia_vendas_db_texto(client_id: str, data_inicio: str, data_fim: str, loja: str = None) -> str:
    """Consulta os bancos SQLite de vendas do tenant e retorna texto compacto com todos os SKUs vendidos no período."""
    try:
        if not data_inicio or not data_fim:
            return ""
        import re as _re
        if not _re.match(r"\d{4}-\d{2}-\d{2}", data_inicio) or not _re.match(r"\d{4}-\d{2}-\d{2}", data_fim):
            return ""

        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return ""

        skus: dict = {}
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
                cur.execute(
                    f"""
                    SELECT sku, produto, SUM(quantidade) AS qtd, SUM(valor) AS valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY sku
                                        """,
                                        [data_inicio, data_fim, *params_loja],
                )
                for row in cur.fetchall():
                    sku = str(row["sku"] or "").strip()
                    if not sku:
                        continue
                    qtd = float(row["qtd"] or 0)
                    valor = float(row["valor"] or 0)
                    if qtd <= 0:
                        continue
                    nome = str(row["produto"] or "").strip()[:80]
                    if sku in skus:
                        skus[sku]["qtd"] += qtd
                        skus[sku]["valor"] += valor
                    else:
                        skus[sku] = {"sku": sku, "nome": nome, "qtd": qtd, "valor": valor}
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA] Erro ao consultar {db_path}: {exc}")
                continue

        if not skus:
            return ""

        ordenados = sorted(skus.values(), key=lambda x: x["qtd"], reverse=True)[:500]
        linhas = [f"{s['sku']} | {s['nome']} | {int(s['qtd'])}un | R${s['valor']:.2f}" for s in ordenados]
        return (
            f"SKUs vendidos no período {data_inicio} a {data_fim} (SKU | produto | qtd | valor):\n"
            + "\n".join(linhas)
        )
    except Exception as exc:
        logger.warning(f"[IA] Falha ao montar vendas DB para IA: {exc}")
        return ""


def _ia_extrair_periodo_mensagem_vendas(mensagem: str, contexto: Optional[dict] = None) -> tuple[str, str]:
    mensagem_raw = str(mensagem or "")
    texto = _normalizar_texto(mensagem_raw)

    padrao_data = r"(\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
    intervalo = re.search(rf"\bDE\s+{padrao_data}\s+(?:A|ATE|ATÉ|\-)\s+{padrao_data}\b", texto)
    if intervalo:
        d1 = _ia_parse_data_iso_flex(intervalo.group(1))
        d2 = _ia_parse_data_iso_flex(intervalo.group(2))
        if d1 and d2:
            return (d1, d2) if d1 <= d2 else (d2, d1)

    datas = re.findall(padrao_data, mensagem_raw)
    if len(datas) >= 2:
        d1 = _ia_parse_data_iso_flex(datas[0])
        d2 = _ia_parse_data_iso_flex(datas[1])
        if d1 and d2:
            return (d1, d2) if d1 <= d2 else (d2, d1)
    elif len(datas) == 1:
        d = _ia_parse_data_iso_flex(datas[0])
        if d:
            return d, d

    meses = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }
    match = re.search(r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*(20\d{2})\b", texto)
    mes = ano = None
    if match:
        mes = meses.get(match.group(1))
        ano = int(match.group(2))
    if mes is None or ano is None:
        match_num = re.search(r"\b(0?[1-9]|1[0-2])[/-](20\d{2})\b", texto)
        if match_num:
            mes = int(match_num.group(1))
            ano = int(match_num.group(2))

    if mes and ano:
        data_ini = dt.date(ano, mes, 1)
        prox = dt.date(ano + 1, 1, 1) if mes == 12 else dt.date(ano, mes + 1, 1)
        data_fim = prox - dt.timedelta(days=1)
        return data_ini.isoformat(), data_fim.isoformat()

    ctx = contexto if isinstance(contexto, dict) else {}
    data_inicio = _ia_normalizar_data_iso_chat(str(ctx.get("data_inicio") or "").strip())
    data_fim = _ia_normalizar_data_iso_chat(str(ctx.get("data_fim") or "").strip())
    return _ia_normalizar_periodo_chat(data_inicio, data_fim)


def _ia_extrair_periodos_comparacao(mensagem: str, contexto: Optional[dict] = None) -> Optional[tuple[tuple[str, str], tuple[str, str]]]:
    mensagem_raw = str(mensagem or "")
    texto = _normalizar_texto(mensagem_raw)
    if not texto:
        return None

    def _normalizar_ano(valor: Optional[str], ano_fallback: Optional[int] = None) -> int:
        if valor is None or str(valor).strip() == "":
            return int(ano_fallback or dt.date.today().year)
        ano_txt = str(valor).strip()
        ano_int = int(ano_txt)
        if len(ano_txt) <= 2:
            return 2000 + ano_int
        return ano_int

    # Comparativos relativos por trimestre
    if "TRIMESTRE" in texto and any(k in texto for k in ("ANTERIOR", "PASSADO")):
        hoje = dt.date.today()
        trimestre_atual = ((hoje.month - 1) // 3) + 1
        ano_atual = hoje.year

        trimestre_anterior = trimestre_atual - 1
        ano_anterior = ano_atual
        if trimestre_anterior == 0:
            trimestre_anterior = 4
            ano_anterior -= 1

        ini_atual_mes = (trimestre_atual - 1) * 3 + 1
        ini_ant_mes = (trimestre_anterior - 1) * 3 + 1

        ini_atual = dt.date(ano_atual, ini_atual_mes, 1)
        fim_atual = dt.date(ano_atual, ini_atual_mes + 3, 1) - dt.timedelta(days=1) if trimestre_atual < 4 else dt.date(ano_atual, 12, 31)

        ini_ant = dt.date(ano_anterior, ini_ant_mes, 1)
        fim_ant = dt.date(ano_anterior, ini_ant_mes + 3, 1) - dt.timedelta(days=1) if trimestre_anterior < 4 else dt.date(ano_anterior, 12, 31)

        return (ini_ant.isoformat(), fim_ant.isoformat()), (ini_atual.isoformat(), fim_atual.isoformat())

    padrao_data = r"(\d{4}[/-]\d{2}[/-]\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
    datas = re.findall(padrao_data, mensagem_raw)
    if len(datas) >= 4:
        a1 = _ia_parse_data_iso_flex(datas[0]); a2 = _ia_parse_data_iso_flex(datas[1])
        b1 = _ia_parse_data_iso_flex(datas[2]); b2 = _ia_parse_data_iso_flex(datas[3])
        if a1 and a2 and b1 and b2:
            pa = (a1, a2) if a1 <= a2 else (a2, a1)
            pb = (b1, b2) if b1 <= b2 else (b2, b1)
            return pa, pb

    meses = {
        "JAN": 1, "JANEIRO": 1,
        "FEV": 2, "FEVEREIRO": 2,
        "MAR": 3, "MARCO": 3,
        "ABR": 4, "ABRIL": 4,
        "MAI": 5, "MAIO": 5,
        "JUN": 6, "JUNHO": 6,
        "JUL": 7, "JULHO": 7,
        "AGO": 8, "AGOSTO": 8,
        "SET": 9, "SETEMBRO": 9,
        "OUT": 10, "OUTUBRO": 10,
        "NOV": 11, "NOVEMBRO": 11,
        "DEZ": 12, "DEZEMBRO": 12,
    }
    matches_mes = list(re.finditer(
        r"\b(JANEIRO|JAN|FEVEREIRO|FEV|MARCO|MAR|ABRIL|ABR|MAIO|MAI|JUNHO|JUN|JULHO|JUL|AGOSTO|AGO|SETEMBRO|SET|OUTUBRO|OUT|NOVEMBRO|NOV|DEZEMBRO|DEZ)\s*(?:DE|/|-)?\s*((?:20)?\d{2})?\b",
        texto,
    ))
    if len(matches_mes) >= 2:
        ano_padrao = dt.date.today().year
        m1 = matches_mes[0]
        m2 = matches_mes[1]
        mes1 = meses.get(m1.group(1))
        mes2 = meses.get(m2.group(1))
        ano1 = _normalizar_ano(m1.group(2), _normalizar_ano(m2.group(2), ano_padrao))
        ano2 = _normalizar_ano(m2.group(2), ano1)
        if mes1 and mes2:
            ini1 = dt.date(ano1, mes1, 1)
            prox1 = dt.date(ano1 + 1, 1, 1) if mes1 == 12 else dt.date(ano1, mes1 + 1, 1)
            fim1 = prox1 - dt.timedelta(days=1)

            ini2 = dt.date(ano2, mes2, 1)
            prox2 = dt.date(ano2 + 1, 1, 1) if mes2 == 12 else dt.date(ano2, mes2 + 1, 1)
            fim2 = prox2 - dt.timedelta(days=1)
            return (ini1.isoformat(), fim1.isoformat()), (ini2.isoformat(), fim2.isoformat())

    # MM/AA ou MM/AAAA (ex.: 04/25 vs 05/25)
    pares_mes = re.findall(r"\b(0?[1-9]|1[0-2])\s*/\s*((?:20)?\d{2})\b", texto)
    if len(pares_mes) >= 2:
        (mes1_txt, ano1_txt), (mes2_txt, ano2_txt) = pares_mes[0], pares_mes[1]
        mes1 = int(mes1_txt)
        mes2 = int(mes2_txt)
        ano1 = _normalizar_ano(ano1_txt)
        ano2 = _normalizar_ano(ano2_txt, ano1)

        ini1 = dt.date(ano1, mes1, 1)
        prox1 = dt.date(ano1 + 1, 1, 1) if mes1 == 12 else dt.date(ano1, mes1 + 1, 1)
        fim1 = prox1 - dt.timedelta(days=1)

        ini2 = dt.date(ano2, mes2, 1)
        prox2 = dt.date(ano2 + 1, 1, 1) if mes2 == 12 else dt.date(ano2, mes2 + 1, 1)
        fim2 = prox2 - dt.timedelta(days=1)
        return (ini1.isoformat(), fim1.isoformat()), (ini2.isoformat(), fim2.isoformat())

    ctx = contexto if isinstance(contexto, dict) else {}
    a_ini = str(ctx.get("data_inicio_a") or "").strip()
    a_fim = str(ctx.get("data_fim_a") or "").strip()
    b_ini = str(ctx.get("data_inicio_b") or "").strip()
    b_fim = str(ctx.get("data_fim_b") or "").strip()
    if a_ini and a_fim and b_ini and b_fim:
        return (a_ini, a_fim), (b_ini, b_fim)

    atual_ini, atual_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, contexto)
    if atual_ini and atual_fim:
        try:
            d_ini = dt.date.fromisoformat(atual_ini)
            d_fim = dt.date.fromisoformat(atual_fim)
            if d_fim >= d_ini:
                dias = (d_fim - d_ini).days + 1
                ant_fim = d_ini - dt.timedelta(days=1)
                ant_ini = ant_fim - dt.timedelta(days=dias - 1)
                return (ant_ini.isoformat(), ant_fim.isoformat()), (d_ini.isoformat(), d_fim.isoformat())
        except Exception:
            return None
    return None


def _ia_resolver_loja_mensagem_vendas(client_id: str, mensagem: str, contexto: Optional[dict] = None) -> Optional[str]:
    texto = _normalizar_texto(mensagem or "")
    tenant_path = get_tenant_path(client_id)
    candidatos: list[tuple[str, str]] = []

    if os.path.exists(tenant_path):
        for nome in sorted(os.listdir(tenant_path)):
            if not nome.startswith("vendas_historico_") or not nome.endswith(".db") or ".backup_" in nome:
                continue
            slug = nome[len("vendas_historico_"):-3]
            if not slug:
                continue
            candidatos.append((slug.replace("_", " "), _normalizar_texto(slug.replace("_", " "))))

    for nome_loja, nome_norm in candidatos:
        if nome_norm and nome_norm in texto:
            return nome_loja

    ctx = contexto if isinstance(contexto, dict) else {}
    loja_ctx = str(ctx.get("loja") or "").strip()
    if loja_ctx and loja_ctx not in ("__todas", "Todas as lojas"):
        return loja_ctx
    return None


def _ia_vendas_db_top_skus(client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None, limite: int = 5) -> list[dict]:
    try:
        if not data_inicio or not data_fim:
            return []
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return []

        skus: dict[str, dict] = {}
        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT sku, produto, SUM(quantidade) AS qtd, SUM(valor) AS valor
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY sku
                    ORDER BY SUM(quantidade) DESC, SUM(valor) DESC
                    """,
                    [data_inicio, data_fim, *params_loja],
                )
                for row in cur.fetchall() or []:
                    sku = str(row["sku"] or "").strip()
                    if not sku:
                        continue
                    atual = skus.setdefault(sku, {
                        "sku": sku,
                        "nome": str(row["produto"] or "").strip()[:120],
                        "qtd": 0.0,
                        "valor": 0.0,
                    })
                    atual["qtd"] += float(row["qtd"] or 0)
                    atual["valor"] += float(row["valor"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA] Erro ao consultar top SKUs em {db_path}: {exc}")
                continue

        return sorted(skus.values(), key=lambda item: (float(item.get("qtd") or 0), float(item.get("valor") or 0)), reverse=True)[:limite]
    except Exception as exc:
        logger.warning(f"[IA] Falha ao consultar top SKUs: {exc}")
        return []


def _ia_tool_get_sales_by_virtual_store_period(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str] = None,
    limite: int = 10,
) -> Optional[dict]:
    try:
        data_inicio, data_fim = _ia_normalizar_periodo_chat(data_inicio, data_fim)
        if not data_inicio or not data_fim:
            return None

        limite = max(1, min(int(limite or 10), 50))
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        agregados: dict[str, dict] = {}
        expr_loja_virtual = (
            "COALESCE(NULLIF(TRIM(unidade_negocio), ''), NULLIF(TRIM(canal), ''), "
            "NULLIF(TRIM(loja_conta), ''), 'Balcao/Painel')"
        )

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT
                        {expr_loja_virtual} AS loja_virtual,
                        SUM(COALESCE(quantidade, 0)) AS qtd,
                        SUM(COALESCE(valor, 0)) AS valor,
                        COUNT(DISTINCT numero) AS pedidos
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY {expr_loja_virtual}
                    """,
                    [data_inicio, data_fim, *params_loja],
                ).fetchall()

                for row in rows or []:
                    loja_virtual = str(row["loja_virtual"] or "").strip() or "Balcao/Painel"
                    atual = agregados.setdefault(loja_virtual, {
                        "loja_virtual": loja_virtual,
                        "quantidade_vendida": 0.0,
                        "valor_vendido": 0.0,
                        "pedidos_total": 0,
                    })
                    atual["quantidade_vendida"] += float(row["qtd"] or 0)
                    atual["valor_vendido"] += float(row["valor"] or 0)
                    atual["pedidos_total"] += int(row["pedidos"] or 0)
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao resumir vendas por loja virtual em {db_path}: {exc}")
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                continue

        itens = sorted(
            agregados.values(),
            key=lambda x: (
                -float(x.get("valor_vendido") or 0),
                -float(x.get("quantidade_vendida") or 0),
                str(x.get("loja_virtual") or ""),
            ),
        )

        return {
            "function": "get_sales_by_virtual_store_period",
            "arguments": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "limite": limite,
            },
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "total_lojas_virtuais": len(itens),
                "itens": itens[:limite],
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter vendas por loja virtual: {exc}")
        return None


def _ia_tool_get_sales_by_sku_virtual_store(
    client_id: str,
    mensagem: str,
    contexto: Optional[dict] = None,
    produto_tool: Optional[dict] = None,
    loja: Optional[str] = None,
    limite: int = 10,
) -> Optional[dict]:
    try:
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku:
            return None

        ctx = contexto if isinstance(contexto, dict) else {}
        data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, ctx)
        data_inicio, data_fim = _ia_normalizar_periodo_chat(data_inicio, data_fim)
        if not data_inicio or not data_fim:
            return None

        limite = max(1, min(int(limite or 10), 50))
        loja_ctx = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, ctx)
        loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else loja_ctx
        dbs = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not dbs:
            return None

        sql_loja, params_loja = _sql_filtro_loja_vendas(loja_filtro)
        agregados: dict[str, dict] = {}
        expr_loja_virtual = (
            "COALESCE(NULLIF(TRIM(unidade_negocio), ''), NULLIF(TRIM(canal), ''), "
            "NULLIF(TRIM(loja_conta), ''), 'Balcao/Painel')"
        )

        for db_path in dbs:
            if not os.path.exists(db_path):
                continue
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                rows = cur.execute(
                    f"""
                    SELECT
                        {expr_loja_virtual} AS loja_virtual,
                        MAX(COALESCE(produto, '')) AS produto,
                        SUM(COALESCE(quantidade, 0)) AS qtd,
                        SUM(COALESCE(valor, 0)) AS valor,
                        COUNT(DISTINCT numero) AS pedidos,
                        MAX(data) AS ultima_venda
                    FROM vendas
                    WHERE data BETWEEN ? AND ?
                      AND UPPER(TRIM(COALESCE(sku, ''))) = ?
                      AND COALESCE(devolucao, 0) = 0
                      AND LOWER(COALESCE(situacao,'')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
                      {sql_loja}
                    GROUP BY {expr_loja_virtual}
                    """,
                    [data_inicio, data_fim, sku, *params_loja],
                ).fetchall()

                for row in rows or []:
                    loja_virtual = str(row["loja_virtual"] or "").strip() or "Balcao/Painel"
                    atual = agregados.setdefault(loja_virtual, {
                        "loja_virtual": loja_virtual,
                        "sku": sku,
                        "produto": str(row["produto"] or "").strip(),
                        "quantidade_vendida": 0.0,
                        "valor_vendido": 0.0,
                        "pedidos_total": 0,
                        "ultima_venda": "",
                    })
                    atual["quantidade_vendida"] += float(row["qtd"] or 0)
                    atual["valor_vendido"] += float(row["valor"] or 0)
                    atual["pedidos_total"] += int(row["pedidos"] or 0)
                    data_row = str(row["ultima_venda"] or "").strip()
                    if data_row and data_row > str(atual.get("ultima_venda") or ""):
                        atual["ultima_venda"] = data_row
                conn.close()
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar SKU por loja virtual em {db_path}: {exc}")
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                continue

        itens = sorted(
            agregados.values(),
            key=lambda x: (
                -float(x.get("valor_vendido") or 0),
                -float(x.get("quantidade_vendida") or 0),
                str(x.get("loja_virtual") or ""),
            ),
        )

        return {
            "function": "get_sales_by_sku_virtual_store",
            "arguments": {
                "sku": sku,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "limite": limite,
            },
            "result": {
                "sku": sku,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja_filtro or "",
                "total_lojas_virtuais": len(itens),
                "itens": itens[:limite],
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter vendas por SKU e loja virtual: {exc}")
        return None


def _ia_vendas_contexto_exato(mensagem: str, client_id: str, contexto: Optional[dict] = None) -> str:
    texto = _normalizar_texto(mensagem or "")
    if "SKU" not in texto and "PRODUTO" not in texto:
        return ""
    if not any(chave in texto for chave in ("MAIS VENDEU", "MAIS VENDIDO", "LIDER", "TOP")):
        return ""

    data_inicio, data_fim = _ia_extrair_periodo_mensagem_vendas(mensagem, contexto)
    if not data_inicio or not data_fim:
        return ""
    loja = _ia_resolver_loja_mensagem_vendas(client_id, mensagem, contexto)
    top_skus = _ia_vendas_db_top_skus(client_id, data_inicio, data_fim, loja, limite=5)
    if not top_skus:
        return ""

    lider = top_skus[0]
    top_linhas = [
        f"{item['sku']} | {item['nome']} | {int(float(item['qtd']))}un | R${float(item['valor']):.2f}"
        for item in top_skus
    ]
    loja_txt = loja or "Todas as lojas"
    return (
        "Consulta exata para a pergunta do usuario sobre vendas:\n"
        f"Periodo consultado: {data_inicio} a {data_fim}\n"
        f"Loja considerada: {loja_txt}\n"
        f"SKU lider: {lider['sku']} | {lider['nome']} | {int(float(lider['qtd']))} unidades | R${float(lider['valor']):.2f}\n"
        "Top SKUs no periodo:\n" + "\n".join(top_linhas)
    )

PEER_EXPORTS = ['_ia_tool_get_days_without_sale', '_ia_tool_get_days_without_sale_top', '_ia_tool_float', '_ia_tool_resolver_sku', '_ia_tool_get_stock_data', '_ia_tool_get_sales_by_period', '_ia_tool_get_sales_quantity_by_period', '_ia_extrair_mes_ano_mensagem', '_ia_periodo_mensal_padrao', '_ia_tool_get_sales_by_month_period', '_ia_tool_get_top_skus_sales_by_month', '_ia_tool_get_top_skus_returns_by_month', '_ia_tool_get_month_sales_returns_details', '_ia_tool_get_sku_sales_by_month', '_ia_extrair_meses_ano_mensagem', '_ia_tool_compare_sku_sales_months', '_ia_devolucoes_db_resumo', '_ia_tool_get_returns_quantity_by_period', '_ia_tool_get_returns_by_period', '_ia_obter_data_referencia_vendas', '_ia_tool_get_stockout_forecast', '_ia_metricas_periodo_raw', '_ia_tool_get_avg_ticket_by_period', '_ia_tool_get_return_rate_by_period', '_ia_tool_get_period_comparison', '_ia_vendas_timeseries_raw', '_ia_tool_get_sales_timeseries', '_ia_tool_detect_sales_anomalies', '_ia_tool_get_profit_by_period', '_ia_chat_scope_notice', '_ia_tool_get_returns_data', '_ia_tool_get_returns_by_sku_period', '_ia_vendas_db_consulta_sku_vendas_devolucoes', '_ia_vendas_db_consulta_sku', '_ia_extrair_item_ids_ml', '_ia_lojas_ml_conectadas', '_ia_vendas_db_texto', '_ia_extrair_periodo_mensagem_vendas', '_ia_extrair_periodos_comparacao', '_ia_resolver_loja_mensagem_vendas', '_ia_vendas_db_top_skus', '_ia_tool_get_sales_by_virtual_store_period', '_ia_tool_get_sales_by_sku_virtual_store', '_ia_vendas_contexto_exato']
__all__ = PEER_EXPORTS + ["configure_ia_tools_vendas_runtime"]

configure_ia_tools_vendas_runtime()
