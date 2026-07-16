"""Common imports and runtime glue for Vendas endpoint modules."""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import functools
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from .errors import VendasDomainError as HTTPException
from .legacy import (
    _classificar_unidade_virtual_devolucao,
    _get_vendas_db_path,
    _listar_bancos_vendas_tenant,
    _sql_filtro_loja_notas_entrada,
    _sql_filtro_unidade_devolucao,
)
from .performance import append_indexable_date_filter, open_vendas_readonly


def listar_notas_entrada(client_id: str, data_inicio: str = None, data_fim: str = None, agrupado: bool = False, unidade_negocio: str = None, loja: str = None):
    db_path = _get_vendas_db_path(client_id, loja)
    if not (db_path and os.path.exists(db_path)):
        return [] if not agrupado else {}

    conn = None
    try:
        conn = open_vendas_readonly(db_path, row_factory=sqlite3.Row)
        cur = conn.cursor()

        if agrupado:
            # Retorna agrupado por SKU com contagem de devoluÃ§Ãµes
            query = """
            SELECT
                sku,
                descricao,
                COUNT(*) as total_devolucoes,
                SUM(valor_total) as valor_total,
                GROUP_CONCAT(DISTINCT fornecedor) as fornecedores
            FROM notas_entrada_itens
            WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''
            """
            params = []
            date_conditions: list[str] = []
            append_indexable_date_filter(date_conditions, params, "data_emissao", data_inicio, data_fim)
            if date_conditions:
                query += " AND " + " AND ".join(date_conditions)
            filtro_sql, filtro_params = _sql_filtro_unidade_devolucao(unidade_negocio)
            if filtro_sql:
                query += filtro_sql
                params.extend(filtro_params)
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_notas_entrada(cur, loja)
            if filtro_loja_sql:
                query += filtro_loja_sql
                params.extend(filtro_loja_params)

            query += " GROUP BY sku ORDER BY total_devolucoes DESC"
            rows = cur.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        else:
            # Retorna lista de itens individuais de devoluÃ§Ãµes
            query = "SELECT numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens"
            params = []
            conditions = ["devolucao = 1"]
            append_indexable_date_filter(conditions, params, "data_emissao", data_inicio, data_fim)
            filtro_sql, filtro_params = _sql_filtro_unidade_devolucao(unidade_negocio)
            if filtro_sql:
                conditions.append(filtro_sql.replace(" AND ", "", 1))
                params.extend(filtro_params)
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_notas_entrada(cur, loja)
            if filtro_loja_sql:
                conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                params.extend(filtro_loja_params)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY date(data_emissao) DESC"
            rows = cur.execute(query, params).fetchall()
            dados = [dict(r) for r in rows]
            for item in dados:
                item["unidade_negocio_virtual"] = _classificar_unidade_virtual_devolucao(
                    item.get("unidade_negocio_virtual"),
                    item.get("natureza_operacao"),
                    item.get("loja_conta"),
                )
            return dados
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler notas de entrada: {str(e)}")
    finally:
        if conn:
            conn.close()

def listar_itens_devolucoes(client_id: str, data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    """Retorna todos os itens de devoluÃ§Ãµes (para cÃ¡lculo de totais)"""
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return []

    try:
        resultado = []

        for db_path in db_paths:
            if not (db_path and os.path.exists(db_path)):
                continue

            conn = open_vendas_readonly(db_path, row_factory=sqlite3.Row)
            try:
                cur = conn.cursor()

                query = "SELECT numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''"
                params = []

                date_conditions: list[str] = []
                append_indexable_date_filter(date_conditions, params, "data_emissao", data_inicio, data_fim)
                if date_conditions:
                    query += " AND " + " AND ".join(date_conditions)
                filtro_sql, filtro_params = _sql_filtro_unidade_devolucao(unidade_negocio)
                if filtro_sql:
                    query += filtro_sql
                    params.extend(filtro_params)
                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_notas_entrada(cur, loja)
                if filtro_loja_sql:
                    query += filtro_loja_sql
                    params.extend(filtro_loja_params)

                query += " ORDER BY date(data_emissao) DESC"
                rows = cur.execute(query, params).fetchall()
                for r in rows:
                    item = dict(r)
                    item["unidade_negocio_virtual"] = _classificar_unidade_virtual_devolucao(
                        item.get("unidade_negocio_virtual"),
                        item.get("natureza_operacao"),
                        item.get("loja_conta"),
                    )
                    resultado.append(item)
            except sqlite3.OperationalError:
                # Alguns bancos podem nÃ£o ter tabela de notas ainda.
                continue
            finally:
                conn.close()

        resultado.sort(key=lambda r: str(r.get("data_emissao") or ""), reverse=True)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler itens de devoluÃ§Ãµes: {str(e)}")

def listar_notas_entrada_por_sku(sku: str, client_id: str, data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    db_path = _get_vendas_db_path(client_id, loja)
    if not (db_path and os.path.exists(db_path)):
        return []

    conn = None
    try:
        conn = open_vendas_readonly(db_path, row_factory=sqlite3.Row)
        cur = conn.cursor()

        query = "SELECT numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE sku = ? AND sku != '' AND devolucao = 1"
        params = [sku]

        date_conditions: list[str] = []
        append_indexable_date_filter(date_conditions, params, "data_emissao", data_inicio, data_fim)
        if date_conditions:
            query += " AND " + " AND ".join(date_conditions)
        filtro_sql, filtro_params = _sql_filtro_unidade_devolucao(unidade_negocio)
        if filtro_sql:
            query += filtro_sql
            params.extend(filtro_params)
        filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_notas_entrada(cur, loja)
        if filtro_loja_sql:
            query += filtro_loja_sql
            params.extend(filtro_loja_params)

        query += " ORDER BY date(data_emissao) DESC"
        rows = cur.execute(query, params).fetchall()
        dados = [dict(r) for r in rows]
        for item in dados:
            item["unidade_negocio_virtual"] = _classificar_unidade_virtual_devolucao(
                item.get("unidade_negocio_virtual"),
                item.get("natureza_operacao"),
                item.get("loja_conta"),
            )
        return dados
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler notas de entrada: {str(e)}")
    finally:
        if conn:
            conn.close()


__all__ = [
    "listar_notas_entrada",
    "listar_itens_devolucoes",
    "listar_notas_entrada_por_sku",
]
