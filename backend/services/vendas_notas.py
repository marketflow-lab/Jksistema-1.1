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
from fastapi import Depends, HTTPException

from backend.schemas import VendasQuery, VendasSyncRequest
from backend.services import vendas_context
from backend.services.runtime_bridge import bind_runtime_globals

_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "SYNC_CANCEL_FLAGS",
    "SYNC_PROGRESS",
    "SYNC_LOGS",
    "SYNC_META",
    "SYNC_DAY_CONTEXT",
    "SYNC_MAX_ACTIVE_VENDAS",
    "SYNC_ACTIVE_LOCK",
    "SYNC_STATE_LOCK",
    "SYNC_THREAD_CONTEXT",
    "SYNC_ACTIVE",
)


def _sync_context_names() -> None:
    for name in vendas_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(vendas_context, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(vendas_context, name)


def _configure_runtime_globals(runtime_module=None):
    runtime = vendas_context.configure_vendas_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime

async def listar_notas_entrada(client_id: str = Depends(vendas_context.get_tenant_id), data_inicio: str = None, data_fim: str = None, agrupado: bool = False, unidade_negocio: str = None, loja: str = None):
    db_path = _get_notas_entrada_db(client_id, loja)
    if not (db_path and os.path.exists(db_path)):
        return [] if not agrupado else {}

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cols_itens = [row[1] for row in cur.execute("PRAGMA table_info(notas_entrada_itens)").fetchall()]
        if "origem_codigo" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN origem_codigo TEXT")
        if "loja_conta" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN loja_conta TEXT")
        if "unidade_negocio" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio TEXT")
        if "unidade_negocio_virtual" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio_virtual TEXT")
        if "finalidade_operacao" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN finalidade_operacao TEXT")
        conn.commit()

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
            if data_inicio and data_fim:
                query += " AND date(data_emissao) BETWEEN ? AND ?"
                params.extend([data_inicio, data_fim])
            elif data_inicio:
                query += " AND date(data_emissao) >= ?"
                params.append(data_inicio)
            elif data_fim:
                query += " AND date(data_emissao) <= ?"
                params.append(data_fim)
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
            if data_inicio and data_fim:
                conditions.append("date(data_emissao) BETWEEN ? AND ?")
                params.extend([data_inicio, data_fim])
            elif data_inicio:
                conditions.append("date(data_emissao) >= ?")
                params.append(data_inicio)
            elif data_fim:
                conditions.append("date(data_emissao) <= ?")
                params.append(data_fim)
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

async def listar_itens_devolucoes(client_id: str = Depends(vendas_context.get_tenant_id), data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    """Retorna todos os itens de devoluÃ§Ãµes (para cÃ¡lculo de totais)"""
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return []

    try:
        resultado = []

        for db_path in db_paths:
            if not (db_path and os.path.exists(db_path)):
                continue

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()

                cols_itens = [row[1] for row in cur.execute("PRAGMA table_info(notas_entrada_itens)").fetchall()]
                if "origem_codigo" not in cols_itens:
                    cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN origem_codigo TEXT")
                if "loja_conta" not in cols_itens:
                    cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN loja_conta TEXT")
                if "unidade_negocio" not in cols_itens:
                    cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio TEXT")
                if "unidade_negocio_virtual" not in cols_itens:
                    cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio_virtual TEXT")
                if "finalidade_operacao" not in cols_itens:
                    cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN finalidade_operacao TEXT")
                conn.commit()

                query = "SELECT numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''"
                params = []

                if data_inicio and data_fim:
                    query += " AND date(data_emissao) BETWEEN ? AND ?"
                    params.extend([data_inicio, data_fim])
                elif data_inicio:
                    query += " AND date(data_emissao) >= ?"
                    params.append(data_inicio)
                elif data_fim:
                    query += " AND date(data_emissao) <= ?"
                    params.append(data_fim)
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

async def listar_notas_entrada_por_sku(sku: str, client_id: str = Depends(vendas_context.get_tenant_id), data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    db_path = _get_notas_entrada_db(client_id, loja)
    if not (db_path and os.path.exists(db_path)):
        return []

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cols_itens = [row[1] for row in cur.execute("PRAGMA table_info(notas_entrada_itens)").fetchall()]
        if "origem_codigo" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN origem_codigo TEXT")
        if "loja_conta" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN loja_conta TEXT")
        if "unidade_negocio" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio TEXT")
        if "unidade_negocio_virtual" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN unidade_negocio_virtual TEXT")
        if "finalidade_operacao" not in cols_itens:
            cur.execute("ALTER TABLE notas_entrada_itens ADD COLUMN finalidade_operacao TEXT")
        conn.commit()

        # Ãndice para acelerar busca de devoluÃ§Ãµes por SKU e perÃ­odo.
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notas_itens_sku_data_dev ON notas_entrada_itens(sku, data_emissao, devolucao)")

        query = "SELECT numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE sku = ? AND sku != '' AND devolucao = 1"
        params = [sku]
        
        if data_inicio and data_fim:
            query += " AND date(data_emissao) BETWEEN ? AND ?"
            params.extend([data_inicio, data_fim])
        elif data_inicio:
            query += " AND date(data_emissao) >= ?"
            params.append(data_inicio)
        elif data_fim:
            query += " AND date(data_emissao) <= ?"
            params.append(data_fim)
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


def configure_vendas_notas_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_vendas_notas_runtime()

__all__ = [
    "configure_vendas_notas_runtime",
    "listar_notas_entrada",
    "listar_itens_devolucoes",
    "listar_notas_entrada_por_sku",
]
