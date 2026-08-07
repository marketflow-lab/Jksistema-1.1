from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
from typing import Optional

from .repository import (
    _ia_devolucoes_db_resumo,
    _ia_obter_data_referencia_vendas,
    _ia_vendas_db_consulta_sku_vendas_devolucoes,
)
from .runtime import (
    _ia_normalizar_periodo_chat,
    _ia_periodo_do_mes,
    _ia_produtos_info_por_sku,
    _ia_subtrair_meses,
    _ia_ultimo_dia_mes,
    _listar_bancos_vendas_tenant,
    _sql_filtro_loja_vendas,
)

logger = logging.getLogger(__name__)


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


__all__ = [
    "_ia_periodo_mensal_padrao",
    "_ia_tool_get_sales_by_month_period",
    "_ia_tool_get_top_skus_sales_by_month",
    "_ia_tool_get_top_skus_returns_by_month",
    "_ia_tool_get_month_sales_returns_details",
    "_ia_tool_get_sku_sales_by_month",
    "_ia_tool_compare_sku_sales_months",
]
