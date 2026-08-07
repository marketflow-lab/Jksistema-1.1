from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

from .parsing import (
    _ia_extrair_periodo_mensagem_vendas,
    _ia_resolver_loja_mensagem_vendas,
    _ia_tool_resolver_sku,
)
from .runtime import (
    _ia_normalizar_periodo_chat,
    _listar_bancos_vendas_tenant,
    _sql_filtro_loja_vendas,
)

logger = logging.getLogger(__name__)


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


__all__ = [
    "_ia_tool_get_sales_by_virtual_store_period",
    "_ia_tool_get_sales_by_sku_virtual_store",
]
