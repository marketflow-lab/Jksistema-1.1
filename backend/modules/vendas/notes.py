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
from .compositions import load_composition_catalog
from .performance import append_indexable_date_filter, cached_table_columns, open_vendas_readonly, select_compatible_column

from .return_reason_queue import RESULT_STATUSES, _legacy_matches


def _return_reason_metadata(row=None):
    """Expose only report fields, keeping queue progress separate from evidence."""
    result = {
        "status": "not_identified", "descricao": "", "codigo": "",
        "fonte": "", "status_coleta": "",
    }
    if row is None:
        return result
    result["status_coleta"] = row["status"]
    if row["result_status"] in RESULT_STATUSES:
        result["status"] = row["result_status"]
    if result["status"] == "identified":
        result["descricao"] = str(row["reason_description"] or "")
        result["codigo"] = str(row["reason_id"] or "")
        if not (result["descricao"].strip() or result["codigo"].strip()):
            # An invalid saved result cannot prove that the provider omitted it.
            result.update(status="not_identified", descricao="", codigo="")
    if row["reason_source"] in {"bling", "mercado_livre"}:
        result["fonte"] = row["reason_source"]
    return result



def _complete_return_reason_from_mirror(current, candidate):
    """Fill missing evidence without replacing the selected commercial row."""
    previous = current["motivo_devolucao"]
    incoming = candidate["motivo_devolucao"]
    if previous["status"] != "not_identified" or incoming["status"] != "identified":
        return
    # Pending evidence may reflect a changed documentary link. An older mirror
    # must not override that deliberate invalidation or an in-flight refresh.
    if previous["status_coleta"] in {"pending", "processing", "retry"}:
        return
    for field in ("store_id", "id_unico"):
        value = current.get(field)
        if not isinstance(value, str) or not value.strip() or candidate.get(field) != value:
            return
    # The commercial deduplication accepts legacy/casefolded owners. Enrichment
    # deliberately requires both exact persisted IDs instead of those fallbacks.
    current["motivo_devolucao"] = dict(incoming)


def _enrich_return_reasons(conn, db_path, rows, client_id, *, catalog=None):
    """Read saved evidence by exact store/item identity without provider calls."""
    for item in rows:
        item["motivo_devolucao"] = _return_reason_metadata()
    if not rows:
        return rows
    try:
        columns = cached_table_columns(db_path, "devolucao_motivos", conn)
        if not {"store_id", "item_id_unico", "status", "result_status"}.issubset(columns):
            return rows
        identities = {}
        for item in rows:
            item_id = str(item.get("id_unico") or "")
            if not item_id.strip():
                continue
            store_id = str(item.get("store_id") or "")
            if not store_id.strip() and item.get("loja_conta"):
                # Old rows may lack the stable ID. Reuse the local catalog's
                # unambiguous aliases and retired-name safeguards, never a
                # provider lookup or the requested filter as proof of ownership.
                if catalog is None:
                    catalog = load_composition_catalog(client_id)
                store_id = catalog._row_store(item, None)
            if store_id:
                identities.setdefault((store_id, item_id), []).append(item)
        by_store = {}
        for store_id, item_id in identities:
            by_store.setdefault(store_id, []).append(item_id)
        fields = ", ".join(select_compatible_column(columns, field) for field in (
            "item_id_unico", "status", "result_status", "reason_id",
            "reason_description", "reason_source", "source_kind",
            "source_fingerprint", "nota_id", "sku",
        ))
        evidence = {}
        for store_id, item_ids in by_store.items():
            # Stay below the variable limit of older SQLite builds.
            for offset in range(0, len(item_ids), 400):
                batch = item_ids[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT {fields} FROM devolucao_motivos WHERE store_id=? "
                    f"AND item_id_unico IN ({placeholders}) "
                    "AND status IN ('pending','processing','retry','done')"
                )
                for row in conn.execute(query, [store_id, *batch]).fetchall():
                    if row["source_kind"] == "legacy":
                        # Legacy ownership and the commercial source can change
                        # after collection has completed. Revalidate the saved
                        # proof against the current local row before reporting.
                        if catalog is None:
                            catalog = load_composition_catalog(client_id)
                        proof = dict(row)
                        proof["store_id"] = store_id
                        if not _legacy_matches(conn, proof, lambda source: catalog._row_store(source, None)):
                            continue
                    evidence[(store_id, row["item_id_unico"])] = _return_reason_metadata(row)
        for identity, reason in evidence.items():
            for item in identities.get(identity, []):
                item["motivo_devolucao"] = dict(reason)
    except sqlite3.DatabaseError:
        # Optional enrichment must not discard already-read commercial rows if
        # an old/temporarily unavailable metadata table cannot be queried.
        pass
    return rows


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
                columns = cached_table_columns(db_path, "notas_entrada_itens", conn)
                if not columns:
                    continue
                identity = ", ".join(select_compatible_column(columns, name) for name in ("id_unico", "store_id"))

                query = "SELECT " + identity + ", numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''"
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
                rows = [dict(row) for row in cur.execute(query, params).fetchall()]
                _enrich_return_reasons(conn, db_path, rows, client_id)
                for item in rows:
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
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return []

    try:
        catalog = load_composition_catalog(client_id)
        candidates = sorted({str(value).strip().upper() for value in catalog.candidates(sku, loja)}) or [str(sku).strip().upper()]
        unique_returns = {}
        identity_fields = (
            "data_emissao", "quantidade", "valor_total", "sku", "loja_conta",
            "store_id", "numero_nota", "origem_codigo",
        )
        for db_path in db_paths:
            if not (db_path and os.path.exists(db_path)):
                continue
            conn = open_vendas_readonly(db_path, row_factory=sqlite3.Row)
            try:
                cur = conn.cursor()
                columns = cached_table_columns(db_path, "notas_entrada_itens", conn)
                if not columns:
                    continue
                identity = ", ".join(select_compatible_column(columns, name, "''") for name in ("id_unico", "store_id"))
                query = "SELECT " + identity + ", numero_nota, origem_codigo, data_emissao, sku, descricao, quantidade, valor_unitario, valor_total, fornecedor, unidade_negocio_virtual, unidade_negocio, natureza_operacao, finalidade_operacao, loja_conta FROM notas_entrada_itens WHERE UPPER(trim(coalesce(sku,''))) IN (" + ",".join("?" for _ in candidates) + ") AND sku != '' AND devolucao = 1"
                params = list(candidates)

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
                rows = [dict(row) for row in cur.execute(query, params).fetchall()]
                _enrich_return_reasons(conn, db_path, rows, client_id, catalog=catalog)
                for item in rows:
                    unique_id = str(item.get("id_unico") or "").strip()
                    owner = str(item.get("store_id") or item.get("loja_conta") or "").strip().casefold()
                    key = (owner, unique_id) if unique_id else tuple(
                        str(item.get(field) or "") for field in identity_fields
                    )
                    current = unique_returns.setdefault(key, item)
                    if current is not item:
                        _complete_return_reason_from_mirror(current, item)
            finally:
                conn.close()

        dados = sorted(unique_returns.values(), key=lambda item: str(item.get("data_emissao") or ""), reverse=True)
        for item in dados:
            item["unidade_negocio_virtual"] = _classificar_unidade_virtual_devolucao(
                item.get("unidade_negocio_virtual"),
                item.get("natureza_operacao"),
                item.get("loja_conta"),
            )
        return catalog.project_rows(dados, target_sku=sku, store=loja)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler notas de entrada: {str(e)}")


__all__ = [
    "listar_notas_entrada",
    "listar_itens_devolucoes",
    "listar_notas_entrada_por_sku",
]
