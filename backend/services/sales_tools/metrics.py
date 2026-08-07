from __future__ import annotations

import logging
import math
import os
import sqlite3
from typing import Optional

from .parsing import _ia_tool_float
from .repository import (
    _ia_devolucoes_db_resumo,
    _ia_vendas_db_top_skus,
    _ia_vendas_timeseries_raw,
)
from .runtime import (
    _ia_carregar_produtos_tool_df,
    _ia_normalizar_periodo_chat,
    _listar_bancos_vendas_tenant,
    _sql_filtro_loja_vendas,
)

logger = logging.getLogger(__name__)


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
        coverage_sufficient = bool(faturamento_total > 0 and cobertura_faturamento >= 0.95)
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
                "lucro_estimado": lucro_parcial if coverage_sufficient else None,
                "margem_percentual_estimada": margem_parcial if coverage_sufficient else None,
                "lucro_estimado_parcial": lucro_parcial if faturamento_com_custo > 0 else None,
                "margem_percentual_parcial": margem_parcial,
                "cobertura_faturamento_percentual": cobertura_faturamento * 100.0,
                "coverage_sufficient": coverage_sufficient,
                "status_margem": "disponivel" if coverage_sufficient else "indisponivel_dados_insuficientes",
                "skus_considerados": len(agregados),
                "skus_com_custo": cobertura_sku_com_custo,
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao calcular lucro por período: {exc}")
        return None


__all__ = [
    "_ia_tool_get_sales_by_period",
    "_ia_tool_get_sales_quantity_by_period",
    "_ia_metricas_periodo_raw",
    "_ia_tool_get_avg_ticket_by_period",
    "_ia_tool_get_return_rate_by_period",
    "_ia_tool_get_period_comparison",
    "_ia_tool_get_sales_timeseries",
    "_ia_tool_detect_sales_anomalies",
    "_ia_tool_get_profit_by_period",
]
