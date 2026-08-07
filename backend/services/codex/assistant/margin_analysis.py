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

from .analysis_stock import _assistant_collect_sales_rank_rows, _assistant_collect_stale_stock_rows, _assistant_collect_stockout_rows
from .runtime import _assistant_texto_norm
from .settings import DEFAULT_RANKING_LIMIT
from .utils import _assistant_float, _assistant_function_name, _assistant_money, _assistant_qty

def _assistant_load_margin_cost_maps(client_id: str, loja: str) -> tuple[dict, dict, list[str]]:
    warnings: list[str] = []
    try:
        from backend.services.promocoes_core_custos import (
            _carregar_custos_impostos_cadastro_por_sku_loja,
        )

        custos, impostos = _carregar_custos_impostos_cadastro_por_sku_loja(str(client_id or "default"), str(loja or ""))
        return custos if isinstance(custos, dict) else {}, impostos if isinstance(impostos, dict) else {}, warnings
    except Exception as exc:
        warnings.append(f"Nao consegui carregar custos/impostos do cadastro: {str(exc)[:220]}")
        return {}, {}, warnings


def _assistant_resolve_margin_cost_tax(custos_por_sku: dict, impostos_por_sku: dict, sku: str) -> tuple[float | None, float | None]:
    try:
        from backend.services.promocoes_core_custos import _resolver_custo_por_sku, _resolver_imposto_rate_por_sku

        custo = _resolver_custo_por_sku(custos_por_sku or {}, sku)
        imposto = _resolver_imposto_rate_por_sku(impostos_por_sku or {}, sku)
        return (
            float(custo) if custo is not None else None,
            float(imposto) if imposto is not None else None,
        )
    except Exception:
        return None, None


def _assistant_collect_ml_listing_refs(context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    refs: dict[str, list[dict[str, Any]]] = {}

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        sku = str(
            item.get("sku")
            or item.get("SKU")
            or item.get("seller_sku")
            or item.get("seller_custom_field")
            or item.get("sku_display")
            or ""
        ).strip().upper()
        if not sku:
            return
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        commercial = details.get("commercial") if isinstance(details.get("commercial"), dict) else {}
        price = commercial.get("price") if isinstance(commercial.get("price"), dict) else {}
        fees = commercial.get("fees") if isinstance(commercial.get("fees"), dict) else {}
        shipping = commercial.get("shipping") if isinstance(commercial.get("shipping"), dict) else {}
        normalized = {
            **item,
            "price": price.get("amount") if price.get("amount") is not None else item.get("price"),
            "original_price": price.get("regular_amount") if price.get("regular_amount") is not None else item.get("original_price"),
            "sale_fee_amount": fees.get("sale_fee_amount"),
            "shipping_seller_cost": shipping.get("seller_cost"),
            "free_shipping": shipping.get("free_shipping"),
            "commercial_collected": bool(commercial),
        }
        identity = (
            str(normalized.get("loja") or normalized.get("store") or ""),
            str(normalized.get("id") or normalized.get("item_id") or ""),
            str(normalized.get("variation_id") or ((normalized.get("match") or {}).get("variation_id") if isinstance(normalized.get("match"), dict) else "")),
        )
        current = refs.setdefault(sku, [])
        if not any(
            (
                str(value.get("loja") or value.get("store") or ""),
                str(value.get("id") or value.get("item_id") or ""),
                str(value.get("variation_id") or ((value.get("match") or {}).get("variation_id") if isinstance(value.get("match"), dict) else "")),
            ) == identity
            for value in current
        ):
            current.append(normalized)

    def lists_from_result(result: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for key in ("rows", "items", "itens", "matches", "anuncios", "produtos", "results"):
            current = result.get(key)
            if isinstance(current, list):
                values.extend(current)
        return values

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict):
            continue
        if _assistant_function_name(raw) != "get_mercado_livre_listing":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        for item in lists_from_result(result):
            add(item)
    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "mercado_livre_listing":
            continue
        for row in item.get("rows") or []:
            add(row)
    return refs


def _assistant_margin_status_label(margem: dict[str, Any]) -> str:
    faltando = [str(item or "").strip() for item in margem.get("faltando_margem") or [] if str(item or "").strip()]
    if not faltando:
        return "completa"
    if "custo" in faltando:
        return "custo ausente"
    return "margem incompleta: " + ", ".join(faltando)


def _assistant_collect_margin_rows(context: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    client_id = str(context.get("client_id") or "").strip()
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    if not client_id:
        client_id = str(tool_plan.get("client_id") or "default").strip() or "default"
    loja = str(tool_plan.get("loja") or "").strip()
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=max(limit, 120))
    if not sales_rows:
        return []
    custos_por_sku, impostos_por_sku, warnings = _assistant_load_margin_cost_maps(client_id, loja)
    cost_maps_by_store: dict[str, tuple[dict, dict]] = {
        _assistant_texto_norm(loja): (custos_por_sku, impostos_por_sku)
    }
    if warnings:
        context.setdefault("warnings", []).extend(warnings)
    ml_refs = _assistant_collect_ml_listing_refs(context)
    rows: list[dict[str, Any]] = []
    for row in sales_rows[:limit]:
        sku = str(row.get("sku") or "").strip().upper()
        if not sku:
            continue
        qtd_num = margem_parse_float(row.get("quantidade_num")) or margem_parse_float(row.get("quantidade_vendida")) or 0.0
        valor_num = margem_parse_float(row.get("valor_num")) or margem_parse_float(row.get("valor_vendido")) or 0.0
        preco_unitario = margem_parse_float(row.get("preco_unitario_num"))
        if (preco_unitario is None or preco_unitario <= 0) and qtd_num > 0:
            preco_unitario = valor_num / qtd_num
        ml_candidates = [dict(item) for item in ml_refs.get(sku) or []]
        if not ml_candidates:
            ml_candidates = [{}]
        multiple_ads = len(ml_candidates) > 1
        for ml_ref in ml_candidates:
            candidate_store = str(ml_ref.get("loja") or ml_ref.get("store") or row.get("loja") or row.get("store") or loja).strip()
            store_key = _assistant_texto_norm(candidate_store)
            if store_key not in cost_maps_by_store:
                store_costs, store_taxes, store_warnings = _assistant_load_margin_cost_maps(client_id, candidate_store)
                cost_maps_by_store[store_key] = (store_costs, store_taxes)
                if store_warnings:
                    context.setdefault("warnings", []).extend(store_warnings)
            candidate_costs, candidate_taxes = cost_maps_by_store[store_key]
            candidate_cost, candidate_tax_rate = _assistant_resolve_margin_cost_tax(candidate_costs, candidate_taxes, sku)
            # Um preco atual de anuncio nunca substitui silenciosamente o preco
            # historico vendido. Sem MLB exato, o preco medio permanece apenas
            # como estimativa por SKU.
            has_listing = bool(ml_ref)
            anuncio_ref = dict(ml_ref)
            if not anuncio_ref:
                anuncio_ref["price"] = preco_unitario
            margem = margem_calcular_anuncio(
                anuncio_ref,
                sku_hint=sku,
                custo=candidate_cost,
                imposto_rate=candidate_tax_rate,
                require_shipping=has_listing,
            )
            custo_total = round(float(candidate_cost) * float(qtd_num), 2) if candidate_cost is not None and qtd_num and not multiple_ads else None
            lucro_unitario = margem_parse_float(margem.get("valor_liquido")) if margem.get("margem_completa") else None
            lucro_total = round(float(lucro_unitario) * float(qtd_num), 2) if lucro_unitario is not None and qtd_num and not multiple_ads and not has_listing else None
            status_label = _assistant_margin_status_label(margem)
            if multiple_ads:
                status_label += "; resultado historico nao rateado entre MLBs"
            rows.append(
                {
                "SKU": sku,
                "Loja": candidate_store,
                "MLB": str(ml_ref.get("id") or ml_ref.get("item_id") or ""),
                "Variacao": str(ml_ref.get("variation_id") or ((ml_ref.get("match") or {}).get("variation_id") if isinstance(ml_ref.get("match"), dict) else "")),
                "Produto": str(row.get("produto") or "").strip(),
                "Qtd vendida": _assistant_qty(qtd_num) if not multiple_ads else "",
                "Valor vendido": _assistant_money(valor_num) if not multiple_ads else "",
                "Custo unitario": margem_formatar_moeda(candidate_cost) if candidate_cost is not None else "",
                "Custo total": margem_formatar_moeda(custo_total) if custo_total is not None else "",
                "Imposto": f"{_assistant_float(margem.get('imposto_percentual')):.2f}%".replace(".", ",") if margem.get("imposto_percentual") is not None else "",
                "Frete": margem.get("frete_ml_text") or "",
                "Tarifa": margem.get("tarifa_ml_text") or "",
                "Contribuicao unitaria atual": margem.get("valor_liquido_text") or "",
                "Lucro estimado": margem_formatar_moeda(lucro_total) if lucro_total is not None else "",
                "Margem %": margem.get("margem_text") or "",
                "Status da margem": status_label,
                "_valor_num": valor_num if not multiple_ads and not has_listing else 0.0,
                "_qtd_num": qtd_num if not multiple_ads else 0.0,
                "_custo_total_num": float(custo_total) if custo_total is not None else None,
                "_lucro_num": float(lucro_total) if lucro_total is not None else None,
                "_margem_completa": bool(margem.get("margem_completa") and lucro_total is not None),
                "_current_listing_margin_complete": bool(margem.get("margem_completa") and has_listing),
                }
            )
    return rows


def _assistant_report_row_sku(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip().upper()
        if value:
            return value
    return ""


def _assistant_index_by_sku(rows: list[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = _assistant_report_row_sku(row, *keys)
        if sku and sku not in indexed:
            indexed[sku] = row
    return indexed


def _assistant_parse_percent_text(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    return _assistant_float(raw)


def _assistant_diagnostic_priority(score: int) -> str:
    if score <= 1:
        return "1 - Imediata"
    if score == 2:
        return "2 - Alta"
    if score == 3:
        return "3 - Media"
    return "4 - Monitorar"


def _assistant_diagnostic_stock_signal(stockout: dict[str, Any], state: dict[str, Any]) -> None:
    risk = _assistant_texto_norm(str(stockout.get("risco") or "").strip())
    state["stockout_risk"] = risk
    if not stockout:
        return
    state["fontes"].append("ruptura_estoque")
    if str(stockout.get("motivo") or "").strip():
        state["motivos"].append(str(stockout.get("motivo") or "").strip())
    if str(stockout.get("acao_recomendada") or "").strip():
        state["acoes"].append(str(stockout.get("acao_recomendada") or "").strip())
    if risk in {"critico", "alto"}:
        state["risco"] = "Critico" if risk == "critico" else "Alto"
        score = 1 if risk == "critico" else 2
        state["risk_score"] = min(state["risk_score"], score)
        state["priority_score"] = min(state["priority_score"], score)
        state["responsavel"] = "Compras/Estoque"
    elif risk == "medio":
        state["risco"] = "Medio"
        state["risk_score"] = min(state["risk_score"], 3)
        state["priority_score"] = min(state["priority_score"], 3)


def _assistant_diagnostic_margin_signal(
    margin: dict[str, Any], sale: dict[str, Any], state: dict[str, Any],
) -> tuple[str, Optional[float], Any, float, float]:
    status = str(margin.get("Status da margem") or "").strip().lower()
    margin_pct = _assistant_parse_percent_text(margin.get("Margem %"))
    profit = margin.get("_lucro_num")
    value = _assistant_float(margin.get("_valor_num") if margin else sale.get("valor_num"))
    quantity = _assistant_float(margin.get("_qtd_num") if margin else sale.get("quantidade_num"))
    if not margin:
        return status, margin_pct, profit, value, quantity
    state["fontes"].append("margem_cadastro_favoritos")
    owner = state["responsavel"]
    if "custo ausente" in status:
        state["motivos"].append("Custo ausente no cadastro; a margem e o lucro do SKU nao ficam confiaveis.")
        state["acoes"].append("Cadastrar custo por loja ou custo geral antes de decidir reposicao, preco ou campanha.")
        state["risco"] = "Alto" if state["risk_score"] > 2 else state["risco"]
        state["risk_score"] = min(state["risk_score"], 2)
        state["priority_score"] = min(state["priority_score"], 2)
        state["responsavel"] = "Cadastro/Custos" if owner == "Comercial/Estoque" else owner if "Cadastro/Custos" in owner else f"{owner} + Cadastro/Custos"
    elif status and status != "completa":
        state["motivos"].append(f"Margem incompleta: {status}.")
        state["acoes"].append("Completar frete, tarifa, imposto e custo para fechar o diagnostico financeiro.")
        state["risco"] = "Medio" if state["risk_score"] > 3 else state["risco"]
        state["risk_score"] = min(state["risk_score"], 3)
        state["priority_score"] = min(state["priority_score"], 3)
        state["responsavel"] = "Cadastro/Custos" if owner == "Comercial/Estoque" else owner if "Cadastro/Custos" in owner else f"{owner} + Cadastro/Custos"
    elif margin_pct is not None and margin_pct < 5:
        state["motivos"].append(f"Margem estimada baixa ({margin.get('Margem %')}); SKU pode estar vendendo com lucro apertado.")
        state["acoes"].append("Revisar preco, custo, tarifa e frete antes de escalar campanha ou reposicao.")
        state["risco"] = "Alto" if margin_pct < 0 else ("Medio" if state["risk_score"] > 3 else state["risco"])
        score = 2 if margin_pct < 0 else 3
        state["risk_score"] = min(state["risk_score"], score)
        state["priority_score"] = min(state["priority_score"], score)
        state["responsavel"] = "Comercial/Precificacao" if owner == "Comercial/Estoque" else f"{owner} + Precificacao"
    return status, margin_pct, profit, value, quantity


def _assistant_diagnostic_stale_sales_signal(stale: dict[str, Any], sale: dict[str, Any], state: dict[str, Any]) -> None:
    if stale:
        state["fontes"].append("estoque_parado")
        if str(stale.get("motivo") or "").strip():
            state["motivos"].append(str(stale.get("motivo") or "").strip())
        if str(stale.get("acao_recomendada") or "").strip():
            state["acoes"].append(str(stale.get("acao_recomendada") or "").strip())
        if state["risk_score"] > 3:
            state.update(risco="Medio", risk_score=3)
        state["priority_score"] = min(state["priority_score"], 3)
        if state["responsavel"] == "Comercial/Estoque":
            state["responsavel"] = "Comercial/Anuncios"
    if sale:
        state["fontes"].append("ranking_vendas")
        if not state["motivos"]:
            state["motivos"].append("SKU esta entre os mais vendidos; precisa ser acompanhado por cobertura, margem e reposicao.")
        if not state["acoes"]:
            state["acoes"].append("Monitorar giro, margem e estoque de loja para manter venda sem ruptura.")


def _assistant_specialist_sku_row(
    sku: str, sale: dict[str, Any], margin: dict[str, Any], stockout: dict[str, Any], stale: dict[str, Any],
) -> dict[str, Any]:
    produto = (str(stockout.get("produto") or "").strip() or str(sale.get("produto") or "").strip()
        or str(margin.get("Produto") or "").strip() or str(stale.get("produto") or "").strip())
    state = {"motivos": [], "acoes": [], "fontes": [], "priority_score": 4, "risk_score": 4,
        "risco": "Monitorar", "responsavel": "Comercial/Estoque", "stockout_risk": ""}
    _assistant_diagnostic_stock_signal(stockout, state)
    status, _, profit, value, quantity = _assistant_diagnostic_margin_signal(margin, sale, state)
    _assistant_diagnostic_stale_sales_signal(stale, sale, state)
    impact: list[str] = []
    if stockout and state["stockout_risk"] in {"critico", "alto"}:
        daily = _assistant_float(str(stockout.get("media_dia") or "0").replace(",", "."))
        unit_price = (value / quantity) if quantity > 0 and value > 0 else 0.0
        if daily * unit_price * 7 > 0:
            impact.append(f"Venda semanal em risco: {_assistant_money(daily * unit_price * 7)}")
    if value > 0:
        impact.append(f"Valor vendido no periodo: {_assistant_money(value)}")
    if profit is not None:
        impact.append(f"Lucro estimado: {_assistant_money(profit)}")
    elif "custo ausente" in status and value > 0:
        impact.append(f"Valor sem margem confiavel: {_assistant_money(value)}")
    return {"SKU": sku, "Produto": produto, "Motivo": " ".join(dict.fromkeys(state["motivos"]))[:900],
        "Risco": state["risco"], "Impacto financeiro": "; ".join(impact) or "Impacto financeiro nao calculavel com os dados atuais.",
        "Margem": str(margin.get("Margem %") or margin.get("Status da margem") or "nao calculada"),
        "Custo ausente": "Sim" if "custo ausente" in status else "Nao",
        "Acao recomendada": " ".join(dict.fromkeys(state["acoes"]))[:900],
        "Prioridade": _assistant_diagnostic_priority(state["priority_score"]), "Responsavel": state["responsavel"],
        "Fonte": ", ".join(dict.fromkeys(state["fontes"])), "_risk_score": state["risk_score"],
        "_priority_score": state["priority_score"], "_valor_num": value}


def _assistant_specialist_sku_diagnostics(context: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    existing = context.get("specialist_sku_diagnostics")
    if isinstance(existing, list) and existing:
        return existing[:limit]
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=max(limit, 200))
    margin_rows = _assistant_collect_margin_rows(context, limit=max(limit, 200))
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=False, limit=max(limit, 200))
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=max(limit, 200))
    indexes = (_assistant_index_by_sku(sales_rows, "sku", "SKU"), _assistant_index_by_sku(margin_rows, "SKU", "sku"),
        _assistant_index_by_sku(stockout_rows, "sku", "SKU"), _assistant_index_by_sku(stale_rows, "sku", "SKU"))
    ordered_skus: list[str] = []
    for source in (stockout_rows, margin_rows, stale_rows, sales_rows):
        for row in source:
            sku = _assistant_report_row_sku(row, "sku", "SKU")
            if sku and sku not in ordered_skus:
                ordered_skus.append(sku)
    diagnostics = [_assistant_specialist_sku_row(sku, *(index.get(sku, {}) for index in indexes)) for sku in ordered_skus]
    diagnostics.sort(key=lambda row: (int(row.get("_priority_score") or 9), int(row.get("_risk_score") or 9),
        -_assistant_float(row.get("_valor_num")), str(row.get("SKU") or "")))
    clean_rows = [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in diagnostics[:limit]]
    context["specialist_sku_diagnostics"] = clean_rows
    return clean_rows



def _assistant_product_costs_and_margin_raw(
    client_id: str,
    plan: dict[str, Any],
    existing_registry_results: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    context = {
        "client_id": client_id,
        "tool_plan": {**(plan or {}), "client_id": client_id},
        "registry_results": list(existing_registry_results or []),
        "tool_results": [],
        "warnings": [],
    }
    limit = int(plan.get("limite") or DEFAULT_RANKING_LIMIT or 50)
    rows = _assistant_collect_margin_rows(context, limit=max(1, min(limit, 500)))
    completos = [row for row in rows if row.get("_margem_completa")]
    valor_completo = sum(_assistant_float(row.get("_valor_num")) for row in completos)
    lucro = sum(_assistant_float(row.get("_lucro_num")) for row in completos)
    margem_pct = (lucro / valor_completo * 100.0) if valor_completo > 0 else 0.0
    sem_custo = sum(1 for row in rows if str(row.get("Status da margem") or "") == "custo ausente")
    return {
        "function": "codex_product_costs_and_margin",
        "arguments": {
            "data_inicio": plan.get("data_inicio") or "",
            "data_fim": plan.get("data_fim") or "",
            "loja": plan.get("loja") or "",
            "limite": limit,
        },
        "result": {
            "rows": [{key: value for key, value in row.items() if not str(key).startswith("_")} for row in rows],
            "total_registros": len(rows),
            "skus_com_margem_completa": len(completos),
            "skus_sem_custo": sem_custo,
            "lucro_estimado_completo": lucro,
            "margem_percentual_completa": margem_pct,
            "warnings": context.get("warnings") or [],
        },
    }
