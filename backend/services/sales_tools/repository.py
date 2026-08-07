from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
from typing import Optional

from .runtime import (
    _ia_normalizar_periodo_chat,
    _listar_bancos_vendas_tenant,
    _sql_filtro_loja_notas_entrada,
    _sql_filtro_loja_vendas,
    carregar_lojas,
)

logger = logging.getLogger(__name__)


def _returns_database_summary(
    db_path: str,
    data_inicio: str,
    data_fim: str,
    loja_filtro: Optional[str],
    sku_ref: str,
) -> Optional[dict]:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        exists = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notas_entrada_itens'"
        ).fetchone()
        if not exists:
            return None
        store_sql, store_params = _sql_filtro_loja_notas_entrada(cur, loja_filtro)
        sku_sql = " AND UPPER(TRIM(COALESCE(sku, ''))) = ?" if sku_ref else ""
        params = [data_inicio, data_fim, *store_params]
        if sku_ref:
            params.append(sku_ref)
        total = cur.execute(
            f"""
            SELECT SUM(COALESCE(quantidade, 0)) AS qtd_total,
                   SUM(COALESCE(valor_total, 0)) AS valor_total,
                   MAX(date(data_emissao)) AS ultima_data,
                   MAX(COALESCE(descricao, '')) AS produto
            FROM notas_entrada_itens
            WHERE devolucao = 1 AND date(data_emissao) BETWEEN ? AND ?
              {store_sql} {sku_sql}
            """,
            params,
        ).fetchone()
        top_rows = []
        if not sku_ref:
            top_rows = cur.execute(
                f"""
                SELECT UPPER(TRIM(COALESCE(sku, ''))) AS sku,
                       MAX(COALESCE(descricao, '')) AS produto,
                       SUM(COALESCE(quantidade, 0)) AS qtd,
                       SUM(COALESCE(valor_total, 0)) AS valor
                FROM notas_entrada_itens
                WHERE devolucao = 1 AND date(data_emissao) BETWEEN ? AND ?
                  {store_sql} AND TRIM(COALESCE(sku, '')) != ''
                GROUP BY UPPER(TRIM(COALESCE(sku, '')))
                """,
                [data_inicio, data_fim, *store_params],
            ).fetchall()
        return {"total": total, "top_rows": top_rows}
    finally:
        conn.close()



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
        databases = _listar_bancos_vendas_tenant(client_id, loja_filtro)
        if not databases:
            return None

        total_quantity = 0.0
        total_value = 0.0
        latest_return = ""
        product_name = ""
        top_skus: dict[str, dict] = {}
        consulted = 0
        for db_path in databases:
            if not os.path.exists(db_path):
                continue
            try:
                summary = _returns_database_summary(
                    db_path, data_inicio, data_fim, loja_filtro, sku_ref
                )
                if not summary:
                    continue
                row = summary["total"]
                if row:
                    total_quantity += float(row["qtd_total"] or 0)
                    total_value += float(row["valor_total"] or 0)
                    row_date = str(row["ultima_data"] or "").strip()
                    latest_return = max(latest_return, row_date)
                    row_product = str(row["produto"] or "").strip()
                    product_name = product_name or row_product
                for top_row in summary["top_rows"] or []:
                    item_sku = str(top_row["sku"] or "").strip().upper()
                    if not item_sku:
                        continue
                    current_item = top_skus.setdefault(item_sku, {
                        "sku": item_sku,
                        "produto": str(top_row["produto"] or "").strip(),
                        "quantidade_devolvida": 0.0,
                        "valor_devolvido": 0.0,
                    })
                    current_item["quantidade_devolvida"] += float(top_row["qtd"] or 0)
                    current_item["valor_devolvido"] += float(top_row["valor"] or 0)
                consulted += 1
            except Exception as exc:
                logger.warning(f"[IA TOOLS] Erro ao consultar devolucoes em {db_path}: {exc}")

        ranking = sorted(
            top_skus.values(),
            key=lambda item: (
                -float(item.get("valor_devolvido") or 0),
                -float(item.get("quantidade_devolvida") or 0),
                str(item.get("sku") or ""),
            ),
        )
        return {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja_filtro or "",
            "sku": sku_ref,
            "produto": product_name,
            "quantidade_devolvida_total": total_quantity,
            "valor_devolvido_total": total_value,
            "ultima_devolucao": latest_return,
            "top_skus": ranking[: max(0, int(limite_top_skus or 0))],
            "bancos_consultados": consulted,
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao resumir devolucoes: {exc}")
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


__all__ = [
    "_ia_devolucoes_db_resumo",
    "_ia_obter_data_referencia_vendas",
    "_ia_vendas_timeseries_raw",
    "_ia_vendas_db_consulta_sku_vendas_devolucoes",
    "_ia_vendas_db_consulta_sku",
    "_ia_extrair_item_ids_ml",
    "_ia_lojas_ml_conectadas",
    "_ia_vendas_db_texto",
    "_ia_vendas_db_top_skus",
]
