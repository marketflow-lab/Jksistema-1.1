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

from .runtime import _assistant_texto_norm
from .utils import _assistant_first_list, _assistant_float, _assistant_function_name, _assistant_money, _assistant_qty, _assistant_risk_label

def _assistant_stockout_store_metrics(item: dict[str, Any], data_referencia: Any = "") -> dict[str, Any]:
    saldo = _assistant_float(item.get("saldo_loja"))
    media = _assistant_float(item.get("media_venda_dia"))
    dias = None
    data_prevista = ""
    risco = "sem_consumo"
    if media > 0:
        dias = saldo / media if saldo > 0 else 0.0
        try:
            ref = date.fromisoformat(str(data_referencia or "")[:10]) if data_referencia else date.today()
        except Exception:
            ref = date.today()
        data_prevista = (ref + timedelta(days=max(0, int(math.ceil(dias))))).isoformat()
        if dias <= 7:
            risco = "critico"
        elif dias <= 15:
            risco = "alto"
        elif dias <= 30:
            risco = "medio"
        else:
            risco = "baixo"
    return {
        "saldo_considerado": saldo,
        "media_venda_dia": media,
        "dias_ate_ruptura": dias,
        "data_prevista_ruptura": data_prevista,
        "risco_ruptura": risco,
    }


def _assistant_stockout_reason(item: dict[str, Any], janela_dias: Any = 30) -> str:
    saldo = _assistant_float(item.get("saldo_considerado", item.get("saldo_loja")))
    media = _assistant_float(item.get("media_venda_dia"))
    vendida = _assistant_float(item.get("quantidade_vendida_janela"))
    dias = item.get("dias_ate_ruptura_considerado", item.get("dias_ate_ruptura"))
    risco = _assistant_texto_norm(item.get("risco_relatorio", item.get("risco_ruptura")))
    janela = int(_assistant_float(janela_dias, 30) or 30)
    if media <= 0:
        return f"Saldo de loja de {_assistant_qty(saldo)} un., mas sem venda na janela de {janela} dias; nao e ruptura imediata, e sim alerta de giro."
    if saldo <= 0:
        return f"Produto ja esta sem saldo de loja e vendeu {_assistant_qty(vendida)} un. nos ultimos {janela} dias."
    cobertura = _assistant_qty(dias if dias is not None else 0, 1)
    base = (
        f"Saldo de loja {_assistant_qty(saldo)} un. cobre cerca de {cobertura} dia(s), "
        f"considerando media de {_assistant_qty(media, 2)} un./dia e {_assistant_qty(vendida)} un. vendidas nos ultimos {janela} dias."
    )
    if risco in {"critico", "alto"}:
        return base + " O estoque Full foi ignorado nesta analise; a cobertura de loja esta abaixo do minimo operacional."
    return base


def _assistant_stockout_action(item: dict[str, Any]) -> str:
    saldo_loja = _assistant_float(item.get("saldo_considerado", item.get("saldo_loja")))
    media = _assistant_float(item.get("media_venda_dia"))
    dias = item.get("dias_ate_ruptura_considerado", item.get("dias_ate_ruptura"))
    if media <= 0:
        return "Revisar anuncio/preco antes de comprar mais; se houver saldo alto, criar acao de giro ou kit."
    if _assistant_float(dias, 9999) <= 7:
        return "Reposicao imediata para estoque de loja, conferir compra em aberto e evitar escalar campanha antes de recompor saldo local."
    if saldo_loja <= 0:
        return "Repor estoque de loja antes de manter venda ativa; sem saldo local, o risco comercial e imediato."
    return "Planejar reposicao, revisar curva de venda e proteger campanha dos SKUs com maior giro."


def _assistant_collect_stockout_rows(context: dict[str, Any], only_risky: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(item: Any, janela_dias: Any = 30, loja: str = "", data_referencia: Any = "") -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("seller_sku") or "").strip().upper()
        if not sku:
            return
        metrics = _assistant_stockout_store_metrics(item, data_referencia)
        risco_norm = _assistant_texto_norm(metrics.get("risco_ruptura"))
        if only_risky and risco_norm not in {"critico", "alto"}:
            return
        key = f"{sku}|{loja}|{metrics.get('data_prevista_ruptura') or ''}"
        if key in seen:
            return
        seen.add(key)
        enriched = {
            **item,
            "saldo_considerado": metrics.get("saldo_considerado"),
            "dias_ate_ruptura_considerado": metrics.get("dias_ate_ruptura"),
            "risco_relatorio": metrics.get("risco_ruptura"),
            "data_prevista_ruptura_considerada": metrics.get("data_prevista_ruptura"),
        }
        row = {
            "sku": sku,
            "produto": str(item.get("produto") or item.get("nome") or item.get("title") or "").strip(),
            "risco": _assistant_risk_label(metrics.get("risco_ruptura")),
            "saldo_loja": _assistant_qty(metrics.get("saldo_considerado")),
            "saldo_considerado": _assistant_qty(metrics.get("saldo_considerado")),
            "vendido_janela": _assistant_qty(item.get("quantidade_vendida_janela")),
            "media_dia": _assistant_qty(item.get("media_venda_dia"), 2),
            "dias_ate_ruptura": _assistant_qty(metrics.get("dias_ate_ruptura"), 1) if metrics.get("dias_ate_ruptura") is not None else "-",
            "ruptura_prevista": str(metrics.get("data_prevista_ruptura") or "-"),
            "motivo": _assistant_stockout_reason(enriched, janela_dias),
            "acao_recomendada": _assistant_stockout_action(enriched),
        }
        if loja:
            row["loja"] = loja
        rows.append(row)

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_stockout_forecast":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        janela = result.get("janela_dias") or (raw.get("arguments") or {}).get("janela_dias") or 30
        data_ref = result.get("data_referencia") or ""
        loja = str(result.get("loja") or (raw.get("arguments") or {}).get("loja") or "").strip()
        items = result.get("itens") if isinstance(result.get("itens"), list) else []
        if not items and result.get("sku"):
            items = [result]
        for item in items:
            add_item(item, janela, loja, data_ref)

    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "stockout_forecast":
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        janela = summary.get("janela_dias") or (item.get("arguments") or {}).get("janela_dias") or 30
        data_ref = summary.get("data_referencia") or ""
        loja = str(item.get("loja") or summary.get("loja") or "").strip()
        for row in item.get("rows") or []:
            add_item(row, janela, loja, data_ref)

    risk_weight = {"Critico": 0, "Alto": 1, "Medio": 2, "Baixo": 3, "Sem consumo recente": 4}
    rows.sort(
        key=lambda row: (
            risk_weight.get(str(row.get("risco") or ""), 9),
            _assistant_float(str(row.get("dias_ate_ruptura") or "9999").replace(",", "."), 9999),
            -_assistant_float(str(row.get("media_dia") or "0").replace(",", "."), 0),
        )
    )
    return rows[:limit]


def _assistant_collect_stale_stock_rows(context: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_item(item: Any, loja: str = "") -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("seller_sku") or item.get("SKU") or "").strip().upper()
        if not sku:
            return
        saldo_loja = item.get("saldo_loja", item.get("saldo_total", item.get("saldo", 0)))
        saldo_num = _assistant_float(saldo_loja)
        if saldo_num <= 0:
            return
        key = f"{sku}|{loja}"
        if key in seen:
            return
        seen.add(key)
        dias = item.get("dias_sem_vender")
        if dias is None:
            dias = item.get("dias_sem_venda", item.get("dias", ""))
        produto = str(item.get("produto") or item.get("nome") or item.get("title") or "").strip()
        ultima_venda = str(item.get("ultima_venda") or item.get("data_ultima_venda") or item.get("last_sale_date") or "-").strip() or "-"
        status = _assistant_texto_norm(item.get("status"))
        nunca_vendeu = status == "nunca_vendeu" or (not str(dias if dias is not None else "").strip() and ultima_venda == "-")
        dias_informado = str(dias if dias is not None else "").strip() not in {"", "-"}
        dias_num = max(0, int(_assistant_float(dias))) if dias_informado else None
        if not nunca_vendeu and dias_num is not None and dias_num < 30:
            return

        if nunca_vendeu:
            situacao = "Nunca vendeu"
            prioridade = "1 - Imediata"
            motivo = "SKU possui saldo de loja, mas nao tem historico de venda localizado."
            acao = "Validar cadastro e anuncio, bloquear nova compra e decidir entre lancamento, kit, transferencia ou liquidacao."
        elif dias_num is None:
            situacao = "Data da ultima venda indisponivel"
            prioridade = "2 - Alta"
            motivo = "SKU possui saldo de loja, mas a data da ultima venda nao pôde ser confirmada."
            acao = "Conferir a sincronizacao do historico de vendas antes de comprar mais ou iniciar liquidacao."
        elif dias_num >= 180:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "1 - Imediata"
            motivo = "SKU esta ha pelo menos 180 dias sem venda e continua ocupando estoque de loja."
            acao = "Bloquear nova compra, revisar anuncio e preco e preparar liquidacao, kit ou transferencia de canal."
        elif dias_num >= 90:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "2 - Alta"
            motivo = "SKU esta entre 90 e 179 dias sem venda, indicando perda relevante de giro."
            acao = "Revisar preco, titulo, foto, frete e concorrencia; testar kit ou promocao com prazo definido."
        elif dias_num >= 60:
            situacao = f"{dias_num} dias sem venda"
            prioridade = "3 - Atencao"
            motivo = "SKU esta entre 60 e 89 dias sem venda e precisa de intervencao antes de virar estoque cronico."
            acao = "Revisar anuncio e exposicao, testar ajuste comercial e acompanhar o giro nas proximas duas semanas."
        else:
            situacao = f"{dias_num or 0} dias sem venda"
            prioridade = "4 - Monitorar"
            motivo = "SKU possui saldo de loja e esta ha pelo menos 30 dias sem venda."
            acao = "Monitorar o giro, revisar a oferta e evitar recomprar ate ocorrer nova venda."

        saldo_full = item.get("saldo_full", 0)
        custo_cadastrado = item.get("custo_cadastrado") is True or (
            item.get("custo_cadastrado") is None and item.get("custo_unitario") not in (None, "")
        )
        custo_unitario = _assistant_float(item.get("custo_unitario")) if custo_cadastrado else None
        custo_origem = str(item.get("custo_origem") or "cadastro").strip() if custo_cadastrado else ""
        capital_custo = item.get("valor_custo_estoque_loja")
        if capital_custo is None and custo_cadastrado:
            capital_custo = saldo_num * float(custo_unitario or 0)
        impacto = (
            f"{_assistant_qty(saldo_num)} un. paradas; {_assistant_money(capital_custo)} pelo custo cadastrado ({custo_origem})."
            if custo_cadastrado
            else f"{_assistant_qty(saldo_num)} un. paradas; custo nao cadastrado para estimar o capital."
        )
        row = {
            "sku": sku,
            "produto": produto,
            "saldo_loja": _assistant_qty(saldo_loja),
            "saldo_full": _assistant_qty(saldo_full),
            "dias_sem_vender": _assistant_qty(dias_num, 0) if dias_num is not None else "-",
            "ultima_venda": ultima_venda,
            "situacao": situacao,
            "prioridade": prioridade,
            "impacto_financeiro": impacto,
            "custo_unitario": _assistant_money(custo_unitario) if custo_cadastrado else "nao cadastrado",
            "custo_origem": custo_origem or "indisponivel",
            "capital_custo": _assistant_money(capital_custo) if custo_cadastrado else "indisponivel",
            "motivo": motivo,
            "acao_recomendada": acao,
        }
        if loja:
            row["loja"] = loja
        rows.append(row)

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_days_without_sale_top":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        loja = str(result.get("loja") or (raw.get("arguments") or {}).get("loja") or "").strip()
        for item in _assistant_first_list(result, "itens", "items", "rows"):
            add_item(item, loja)

    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "stale_stock":
            continue
        loja = str(item.get("loja") or (item.get("arguments") or {}).get("loja") or "").strip()
        for row in item.get("rows") or []:
            add_item(row, loja)

    rows.sort(
        key=lambda row: (
            int(str(row.get("prioridade") or "9").split(" ", 1)[0]) if str(row.get("prioridade") or "").split(" ", 1)[0].isdigit() else 9,
            0 if str(row.get("situacao") or "") == "Nunca vendeu" else 1,
            -_assistant_float(str(row.get("dias_sem_vender") or "0").replace(",", "."), 0),
            -_assistant_float(str(row.get("saldo_loja") or "0").replace(",", "."), 0),
            str(row.get("sku") or ""),
        )
    )
    return rows[:limit]


def _assistant_collect_sales_rank_rows(context: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(item: Any) -> None:
        if not isinstance(item, dict):
            return
        sku = str(item.get("sku") or item.get("SKU") or item.get("seller_sku") or "").strip().upper()
        if not sku:
            return
        if sku in seen:
            return
        seen.add(sku)
        qtd = item.get("quantidade_vendida") or item.get("quantidade") or item.get("qtd") or item.get("quantidade_total") or 0
        valor = item.get("valor_vendido") or item.get("valor_total") or item.get("valor") or 0
        qtd_num = margem_parse_float(qtd) or 0.0
        valor_num = margem_parse_float(valor) or 0.0
        preco_unitario = (valor_num / qtd_num) if qtd_num > 0 else 0.0
        pedidos = item.get("pedidos") or item.get("numero_pedidos") or item.get("pedidos_total") or "-"
        rows.append(
            {
                "sku": sku,
                "produto": str(item.get("produto") or item.get("nome") or item.get("title") or "").strip(),
                "quantidade_vendida": _assistant_qty(qtd),
                "valor_vendido": _assistant_money(valor),
                "quantidade_num": qtd_num,
                "valor_num": valor_num,
                "preco_unitario_num": preco_unitario,
                "pedidos": pedidos,
                "analise": "SKU relevante para proteger estoque, margem, anuncio e reposicao no periodo consultado.",
            }
        )

    for raw in context.get("tool_results") or []:
        if not isinstance(raw, dict) or _assistant_function_name(raw) != "get_sales_by_period":
            continue
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        for item in _assistant_first_list(result, "top_skus", "por_sku", "items", "itens"):
            add_row(item)
    for item in context.get("registry_results") or []:
        if not isinstance(item, dict) or item.get("tool_id") != "sales_ranking":
            continue
        for row in item.get("rows") or []:
            add_row(row)
    return rows[:limit]
