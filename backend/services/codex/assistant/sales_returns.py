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

from .references import _assistant_normalize_sku, _assistant_sales_db_candidates, _assistant_sales_db_candidates_query, _assistant_sales_returns_context_text, _assistant_sr_add_sku, _assistant_sr_empty_store, _assistant_sr_finalize_sku_rows, _assistant_store_label
from .settings import DEFAULT_RANKING_LIMIT
from .utils import _assistant_float, _assistant_result_count
def _assistant_sr_read_sales(cur: Any, ctx: dict[str, Any], store: str, store_summary: dict[str, Any]) -> None:
    data_inicio, data_fim = ctx["data_inicio"], ctx["data_fim"]
    sku_ref, incluir_registros = ctx["sku_ref"], ctx["incluir_registros"]
    vendas_records, sku_aggregates = ctx["vendas_records"], ctx["sku_aggregates"]
    pedidos_total_keys = ctx["pedidos_total_keys"]
    sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
    sales_params: list[Any] = [data_inicio, data_fim]
    if sku_ref:
        sales_params.append(sku_ref)
    sales_rows = cur.execute(
        f"""
        SELECT
            id_unico,
            substr(coalesce(data, ''), 1, 10) AS data,
            loja_conta,
            canal,
            numero,
            situacao,
            sku,
            produto,
            quantidade,
            valor,
            numero_nf,
            comprador,
            unidade_negocio,
            loja_id,
            unidade_id,
            intermediador_nome,
            intermediador_cnpj
        FROM vendas
        WHERE substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
          AND coalesce(devolucao, 0) = 0
          AND lower(coalesce(situacao, '')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
          {sku_sql}
        ORDER BY date(data) DESC, numero DESC, sku
        """,
        sales_params,
    ).fetchall()
    for row in sales_rows or []:
        sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
        pedido_ref = str(row["numero"] or row["id_unico"] or "").strip()
        pedido_key = f"{store}|{pedido_ref or row['id_unico'] or len(vendas_records)}"
        pedido_store_key = pedido_key
        pedidos_total_keys.add(pedido_key)
        qtd = _assistant_float(row["quantidade"])
        valor = _assistant_float(row["valor"])
        store_summary["vendas_quantidade"] += qtd
        store_summary["vendas_valor"] += valor
        store_summary["vendas_registros"] += 1
        _assistant_sr_add_sku(sku_aggregates, sku_row, row["produto"], store, "venda", qtd, valor, pedido_store_key)
        if incluir_registros:
            vendas_records.append(
                {
                    "loja": store,
                    "data": str(row["data"] or ""),
                    "id_unico": str(row["id_unico"] or ""),
                    "numero": str(row["numero"] or ""),
                    "numero_nf": str(row["numero_nf"] or ""),
                    "sku": sku_row,
                    "produto": str(row["produto"] or ""),
                    "quantidade": qtd,
                    "valor": valor,
                    "situacao": str(row["situacao"] or ""),
                    "canal": str(row["canal"] or ""),
                    "unidade_negocio": str(row["unidade_negocio"] or ""),
                    "loja_conta": str(row["loja_conta"] or ""),
                }
            )

    store_summary["vendas_pedidos"] = len({
        str(row["numero"] or row["id_unico"] or idx)
        for idx, row in enumerate(sales_rows or [])
    })

def _assistant_sr_read_note_returns(cur: Any, ctx: dict[str, Any], store: str, store_summary: dict[str, Any]) -> None:
    data_inicio, data_fim = ctx["data_inicio"], ctx["data_fim"]
    sku_ref, incluir_registros = ctx["sku_ref"], ctx["incluir_registros"]
    devol_records, sku_aggregates = ctx["devol_records"], ctx["sku_aggregates"]
    sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
    notes_table_exists = True
    if notes_table_exists:
        store_summary["devolucoes_fonte"] = "notas_entrada_itens"
        returns_params: list[Any] = [data_inicio, data_fim]
        if sku_ref:
            returns_params.append(sku_ref)
        returns_rows = cur.execute(
            f"""
            SELECT
                numero_nota,
                origem_codigo,
                substr(coalesce(data_emissao, ''), 1, 10) AS data,
                sku,
                descricao,
                quantidade,
                valor_unitario,
                valor_total,
                fornecedor,
                unidade_negocio_virtual,
                unidade_negocio,
                natureza_operacao,
                finalidade_operacao,
                loja_conta
            FROM notas_entrada_itens
            WHERE devolucao = 1
              AND date(data_emissao) BETWEEN ? AND ?
              AND trim(coalesce(sku, '')) != ''
              {sku_sql}
            ORDER BY date(data_emissao) DESC, numero_nota DESC, sku
            """,
            returns_params,
        ).fetchall()
        for row in returns_rows or []:
            sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
            qtd = _assistant_float(row["quantidade"])
            valor = _assistant_float(row["valor_total"])
            store_summary["devolucoes_quantidade"] += qtd
            store_summary["devolucoes_valor"] += valor
            store_summary["devolucoes_registros"] += 1
            _assistant_sr_add_sku(sku_aggregates, sku_row, row["descricao"], store, "devolucao", qtd, valor)
            if incluir_registros:
                devol_records.append(
                    {
                        "loja": store,
                        "data": str(row["data"] or ""),
                        "numero_nota": str(row["numero_nota"] or ""),
                        "origem_codigo": str(row["origem_codigo"] or ""),
                        "sku": sku_row,
                        "produto": str(row["descricao"] or ""),
                        "quantidade": qtd,
                        "valor_unitario": _assistant_float(row["valor_unitario"]),
                        "valor_total": valor,
                        "fornecedor": str(row["fornecedor"] or ""),
                        "unidade_negocio_virtual": str(row["unidade_negocio_virtual"] or ""),
                        "unidade_negocio": str(row["unidade_negocio"] or ""),
                        "natureza_operacao": str(row["natureza_operacao"] or ""),
                        "finalidade_operacao": str(row["finalidade_operacao"] or ""),
                        "loja_conta": str(row["loja_conta"] or ""),
                        "fonte": "notas_entrada_itens",
                    }
                )

def _assistant_sr_read_fallback_returns(cur: Any, ctx: dict[str, Any], store: str, store_summary: dict[str, Any], db_path: str) -> None:
    data_inicio, data_fim = ctx["data_inicio"], ctx["data_fim"]
    sku_ref, incluir_registros = ctx["sku_ref"], ctx["incluir_registros"]
    devol_records, sku_aggregates = ctx["devol_records"], ctx["sku_aggregates"]
    warnings = ctx["warnings"]
    sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
    warnings.append(f"{os.path.basename(db_path)}: notas_entrada_itens ausente; usei fallback vendas.devolucao=1.")
    returns_params = [data_inicio, data_fim]
    if sku_ref:
        returns_params.append(sku_ref)
    fallback_rows = cur.execute(
        f"""
        SELECT
            id_unico,
            substr(coalesce(data, ''), 1, 10) AS data,
            loja_conta,
            canal,
            numero,
            situacao,
            sku,
            produto,
            quantidade,
            valor,
            numero_nf,
            comprador,
            unidade_negocio
        FROM vendas
        WHERE substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
          AND coalesce(devolucao, 0) = 1
          {sku_sql}
        ORDER BY date(data) DESC, numero DESC, sku
        """,
        returns_params,
    ).fetchall()
    for row in fallback_rows or []:
        sku_row = _assistant_normalize_sku(row["sku"]) or "SEM SKU"
        qtd = _assistant_float(row["quantidade"])
        valor = _assistant_float(row["valor"])
        store_summary["devolucoes_quantidade"] += qtd
        store_summary["devolucoes_valor"] += valor
        store_summary["devolucoes_registros"] += 1
        _assistant_sr_add_sku(sku_aggregates, sku_row, row["produto"], store, "devolucao", qtd, valor)
        if incluir_registros:
            devol_records.append(
                {
                    "loja": store,
                    "data": str(row["data"] or ""),
                    "id_unico": str(row["id_unico"] or ""),
                    "numero": str(row["numero"] or ""),
                    "numero_nf": str(row["numero_nf"] or ""),
                    "sku": sku_row,
                    "produto": str(row["produto"] or ""),
                    "quantidade": qtd,
                    "valor_total": valor,
                    "situacao": str(row["situacao"] or ""),
                    "canal": str(row["canal"] or ""),
                    "unidade_negocio": str(row["unidade_negocio"] or ""),
                    "loja_conta": str(row["loja_conta"] or ""),
                    "fonte": "vendas.devolucao",
                }
            )

def _assistant_sr_process_candidate(ctx: dict[str, Any], db_path: str, store_name: str) -> None:
    store = _assistant_store_label(store_name)
    store_summary = _assistant_sr_empty_store(store, db_path)
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=8)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        tables = {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()}
        if "vendas" not in tables:
            ctx["warnings"].append(f"{os.path.basename(db_path)}: tabela vendas ausente.")
            return
        _assistant_sr_read_sales(cur, ctx, store, store_summary)
        if "notas_entrada_itens" in tables:
            _assistant_sr_read_note_returns(cur, ctx, store, store_summary)
        else:
            _assistant_sr_read_fallback_returns(cur, ctx, store, store_summary, db_path)
        ctx["por_loja"].append(store_summary)
        totals = ctx["totals"]
        totals["quantidade_vendida_total"] += float(store_summary["vendas_quantidade"] or 0)
        totals["valor_vendido_total"] += float(store_summary["vendas_valor"] or 0)
        totals["quantidade_devolvida_total"] += float(store_summary["devolucoes_quantidade"] or 0)
        totals["valor_devolvido_total"] += float(store_summary["devolucoes_valor"] or 0)
    except Exception as exc:
        ctx["warnings"].append(f"{os.path.basename(db_path)}: {str(exc)[:220]}")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

def _assistant_sr_finalize_query(ctx: dict[str, Any]) -> dict[str, Any]:
    totals, por_loja = ctx["totals"], ctx["por_loja"]
    vendas_records, devol_records = ctx["vendas_records"], ctx["devol_records"]
    incluir_registros = ctx["incluir_registros"]
    totals["pedidos_total"] = len(ctx["pedidos_total_keys"])
    totals["vendas_registros_total"] = len(vendas_records) if incluir_registros else sum(int(row.get("vendas_registros") or 0) for row in por_loja)
    totals["devolucoes_registros_total"] = len(devol_records) if incluir_registros else sum(int(row.get("devolucoes_registros") or 0) for row in por_loja)
    totals["total_registros"] = int(totals["vendas_registros_total"] or 0) + int(totals["devolucoes_registros_total"] or 0)
    vendas_qtd, vendas_valor = float(totals["quantidade_vendida_total"] or 0), float(totals["valor_vendido_total"] or 0)
    devol_qtd, devol_valor = float(totals["quantidade_devolvida_total"] or 0), float(totals["valor_devolvido_total"] or 0)
    totals["taxa_devolucao_quantidade_percentual"] = (devol_qtd / vendas_qtd * 100.0) if vendas_qtd > 0 else 0.0
    totals["taxa_devolucao_valor_percentual"] = (devol_valor / vendas_valor * 100.0) if vendas_valor > 0 else 0.0
    result = {
        "data_inicio": ctx["data_inicio"], "data_fim": ctx["data_fim"],
        "loja": ctx["loja_filtro"] or "", "sku": ctx["sku_ref"],
        "separar_por_loja": ctx["separar_por_loja"], "incluir_registros": incluir_registros,
        "totais": totals, "por_loja": por_loja if ctx["separar_por_loja"] or not ctx["loja_filtro"] else por_loja[:1],
        "por_sku": _assistant_sr_finalize_sku_rows(ctx["sku_aggregates"]),
        "vendas": vendas_records if incluir_registros else [],
        "devolucoes": devol_records if incluir_registros else [],
        "source_paths": ctx["checked"], "warnings": ctx["warnings"][:20],
        "fallback_used": any("fallback" in warning.lower() for warning in ctx["warnings"]),
        "total_registros": totals["total_registros"],
    }
    result["context_text"] = _assistant_sales_returns_context_text(result)
    return {"function": "sales_returns_query", "arguments": {
        "data_inicio": ctx["data_inicio"], "data_fim": ctx["data_fim"],
        "loja": ctx["loja_filtro"] or "", "sku": ctx["sku_ref"],
        "separar_por_loja": ctx["separar_por_loja"], "incluir_registros": incluir_registros,
    }, "result": result}

def _assistant_sales_returns_query(
    client_id: str, data_inicio: str, data_fim: str, loja: Optional[str] = None,
    sku: Optional[str] = None, separar_por_loja: bool = False, incluir_registros: bool = True,
) -> Optional[dict[str, Any]]:
    if not data_inicio or not data_fim:
        return None
    loja_filtro = loja if (loja and str(loja).strip() not in ("", "__todas", "Todas as lojas")) else None
    candidates = _assistant_sales_db_candidates_query(client_id, loja_filtro)
    warnings = [] if candidates else ["Nenhum banco vendas_historico*.db foi encontrado nas pastas de dados conhecidas."]
    ctx = {
        "data_inicio": data_inicio, "data_fim": data_fim, "loja_filtro": loja_filtro,
        "sku_ref": _assistant_normalize_sku(sku), "separar_por_loja": bool(separar_por_loja),
        "incluir_registros": bool(incluir_registros), "checked": [path for path, _ in candidates],
        "warnings": warnings, "vendas_records": [], "devol_records": [], "por_loja": [],
        "sku_aggregates": {}, "pedidos_total_keys": set(),
        "totals": {"quantidade_vendida_total": 0.0, "valor_vendido_total": 0.0, "pedidos_total": 0,
            "quantidade_devolvida_total": 0.0, "valor_devolvido_total": 0.0,
            "taxa_devolucao_quantidade_percentual": 0.0, "taxa_devolucao_valor_percentual": 0.0,
            "vendas_registros_total": 0, "devolucoes_registros_total": 0, "total_registros": 0},
    }
    for db_path, store_name in candidates:
        _assistant_sr_process_candidate(ctx, db_path, store_name)
    return _assistant_sr_finalize_query(ctx)

def _assistant_sr_raw_result(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result = raw.get("result")
    return result if isinstance(result, dict) else {}


def _assistant_sr_as_sales_by_period(raw: Optional[dict[str, Any]], limite: int = DEFAULT_RANKING_LIMIT) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    limit_safe = max(1, min(int(limite or DEFAULT_RANKING_LIMIT), 500))
    top_skus = [
        {
            "sku": item.get("sku") or "SEM SKU",
            "nome": item.get("produto") or "",
            "qtd": item.get("quantidade_vendida") or 0,
            "valor": item.get("valor_vendido") or 0,
            "pedidos": item.get("pedidos") or 0,
            "lojas": item.get("lojas") or [],
        }
        for item in (result.get("por_sku") or [])
        if isinstance(item, dict) and (_assistant_float(item.get("quantidade_vendida")) > 0 or _assistant_float(item.get("valor_vendido")) > 0)
    ]
    top_skus = sorted(top_skus, key=lambda item: (_assistant_float(item.get("qtd")), _assistant_float(item.get("valor"))), reverse=True)[:limit_safe]
    return {
        "function": "sales_returns_query_sales_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_total": totals.get("quantidade_vendida_total") or 0,
            "valor_total": totals.get("valor_vendido_total") or 0,
            "pedidos_total": totals.get("pedidos_total") or 0,
            "top_skus": top_skus,
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }
def _assistant_sr_as_sales_quantity(raw: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    return {
        "function": "sales_returns_query_sales_quantity_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_vendida_total": totals.get("quantidade_vendida_total") or 0,
            "quantidade_total": totals.get("quantidade_vendida_total") or 0,
            "valor_total": totals.get("valor_vendido_total") or 0,
            "pedidos_total": totals.get("pedidos_total") or 0,
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }

def _assistant_sr_as_returns_by_period(raw: Optional[dict[str, Any]], limite: int = 50) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    limit_safe = max(1, min(int(limite or 50), 500))
    top_skus = [
        {
            "sku": item.get("sku") or "SEM SKU",
            "produto": item.get("produto") or "",
            "quantidade_devolvida": item.get("quantidade_devolvida") or 0,
            "valor_devolvido": item.get("valor_devolvido") or 0,
            "lojas": item.get("lojas") or [],
        }
        for item in (result.get("por_sku") or [])
        if isinstance(item, dict) and (_assistant_float(item.get("quantidade_devolvida")) > 0 or _assistant_float(item.get("valor_devolvido")) > 0)
    ]
    top_skus = sorted(top_skus, key=lambda item: (_assistant_float(item.get("valor_devolvido")), _assistant_float(item.get("quantidade_devolvida"))), reverse=True)[:limit_safe]
    return {
        "function": "sales_returns_query_returns_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_devolvida_total": totals.get("quantidade_devolvida_total") or 0,
            "valor_devolvido_total": totals.get("valor_devolvido_total") or 0,
            "top_skus": top_skus,
            "devolucoes": result.get("devolucoes") or [],
            "source_paths": result.get("source_paths") or [],
            "fallback_used": True,
        },
    }


def _assistant_sr_as_return_rate(raw: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    result = _assistant_sr_raw_result(raw)
    if not result:
        return None
    totals = result.get("totais") if isinstance(result.get("totais"), dict) else {}
    return {
        "function": "sales_returns_query_return_rate_by_period",
        "arguments": raw.get("arguments") if isinstance(raw, dict) else {},
        "result": {
            "data_inicio": result.get("data_inicio") or "",
            "data_fim": result.get("data_fim") or "",
            "loja": result.get("loja") or "",
            "quantidade_vendida_total": totals.get("quantidade_vendida_total") or 0,
            "quantidade_devolvida_total": totals.get("quantidade_devolvida_total") or 0,
            "valor_vendido_total": totals.get("valor_vendido_total") or 0,
            "valor_devolvido_total": totals.get("valor_devolvido_total") or 0,
            "taxa_devolucao_quantidade_percentual": totals.get("taxa_devolucao_quantidade_percentual") or 0,
            "taxa_devolucao_valor_percentual": totals.get("taxa_devolucao_valor_percentual") or 0,
            "fallback_used": True,
        },
    }


def _assistant_raw_records(raw: Optional[dict[str, Any]]) -> int:
    if not isinstance(raw, dict):
        return 0
    result = raw.get("result")
    if not isinstance(result, dict):
        return 0
    records = _assistant_result_count(result)
    if records:
        return records
    scalar_keys = (
        "quantidade_total",
        "quantidade_vendida_total",
        "quantidade_devolvida_total",
        "valor_total",
        "valor_vendido_total",
        "valor_devolvido_total",
        "pedidos_total",
        "total_registros",
    )
    if any(result.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
        return 1
    return 0


def _assistant_sqlite_sales_totals(
    candidates: list[tuple[str, str]], data_inicio: str, data_fim: str, limite: int,
) -> tuple[dict[str, dict[str, Any]], float, float, int, list[str], int]:
    aggregates: dict[str, dict[str, Any]] = {}
    total_qtd = 0.0
    total_valor = 0.0
    total_pedidos = 0
    errors: list[str] = []
    limit_safe = max(1, min(int(limite or DEFAULT_RANKING_LIMIT), 500))
    where = """
        substr(coalesce(data, ''), 1, 10) BETWEEN ? AND ?
        AND coalesce(devolucao, 0) = 0
        AND lower(coalesce(situacao, '')) NOT IN ('cancelado','cancelada','cancelamento','cancelamento solicitado')
    """
    for db_path, store_name in candidates:
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tables = {str(row[0]) for row in cur.execute("select name from sqlite_master where type='table'").fetchall()}
            if "vendas" not in tables:
                errors.append(f"{db_path}: tabela vendas ausente")
                continue
            total = cur.execute(
                f"""SELECT coalesce(sum(coalesce(quantidade, 0)), 0) AS qtd,
                coalesce(sum(coalesce(valor, 0)), 0) AS valor,
                count(distinct coalesce(nullif(trim(numero), ''), id_unico, rowid)) AS pedidos
                FROM vendas WHERE {where}""", [data_inicio, data_fim],
            ).fetchone()
            if total:
                total_qtd += float(total["qtd"] or 0)
                total_valor += float(total["valor"] or 0)
                total_pedidos += int(total["pedidos"] or 0)
            rows = cur.execute(
                f"""SELECT coalesce(nullif(trim(sku), ''), 'SEM SKU') AS sku,
                max(coalesce(nullif(trim(produto), ''), '')) AS produto,
                coalesce(sum(coalesce(quantidade, 0)), 0) AS qtd,
                coalesce(sum(coalesce(valor, 0)), 0) AS valor,
                count(distinct coalesce(nullif(trim(numero), ''), id_unico, rowid)) AS pedidos
                FROM vendas WHERE {where}
                GROUP BY coalesce(nullif(trim(sku), ''), 'SEM SKU')
                ORDER BY qtd DESC, valor DESC LIMIT ?""", [data_inicio, data_fim, limit_safe],
            ).fetchall()
            for row in rows or []:
                sku = str(row["sku"] or "").strip() or "SEM SKU"
                item = aggregates.setdefault(sku, {
                    "sku": sku, "nome": str(row["produto"] or "").strip()[:160],
                    "qtd": 0.0, "valor": 0.0, "pedidos": 0, "loja": store_name,
                })
                if not item.get("nome") and row["produto"]:
                    item["nome"] = str(row["produto"] or "").strip()[:160]
                item["qtd"] += float(row["qtd"] or 0)
                item["valor"] += float(row["valor"] or 0)
                item["pedidos"] += int(row["pedidos"] or 0)
        except Exception as exc:
            errors.append(f"{db_path}: {str(exc)[:180]}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
    return aggregates, total_qtd, total_valor, total_pedidos, errors, limit_safe


def _assistant_sqlite_sales_by_period(
    client_id: str,
    data_inicio: str,
    data_fim: str,
    loja: Optional[str],
    limite: int = DEFAULT_RANKING_LIMIT,
) -> Optional[dict[str, Any]]:
    if not data_inicio or not data_fim:
        return None
    candidates = _assistant_sales_db_candidates(client_id, loja)
    checked = [path for path, _ in candidates]
    if not candidates:
        return {
            "function": "codex_sqlite_sales_by_period",
            "arguments": {"data_inicio": data_inicio, "data_fim": data_fim, "loja": loja or "", "limite": limite},
            "result": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja or "",
                "quantidade_total": 0,
                "valor_total": 0,
                "pedidos_total": 0,
                "top_skus": [],
                "source_paths": [],
                "fallback_used": True,
                "empty_reason": "Nenhum banco vendas_historico*.db foi encontrado nas pastas de dados conhecidas.",
            },
        }

    aggregates, total_qtd, total_valor, total_pedidos, errors, limit_safe = _assistant_sqlite_sales_totals(
        candidates, data_inicio, data_fim, limite,
    )

    top_skus = sorted(
        aggregates.values(),
        key=lambda item: (float(item.get("qtd") or 0), float(item.get("valor") or 0)),
        reverse=True,
    )[:limit_safe]
    return {
        "function": "codex_sqlite_sales_by_period",
        "arguments": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja or "",
            "limite": limit_safe,
            "source_paths": checked,
        },
        "result": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja or "",
            "quantidade_total": total_qtd,
            "valor_total": total_valor,
            "pedidos_total": total_pedidos,
            "top_skus": top_skus,
            "source_paths": checked,
            "source_errors": errors[:8],
            "fallback_used": True,
        },
    }
