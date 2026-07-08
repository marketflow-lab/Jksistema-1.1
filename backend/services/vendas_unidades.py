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

async def listar_unidades_negocios(loja: str = None, sku: str = None, data_inicio: str = None, data_fim: str = None, client_id: str = Depends(vendas_context.get_tenant_id)):
    """Lista as unidades de negócio e seus nomes configurados"""
    try:
        # Buscar IDs únicos em todos os bancos de vendas do tenant
        db_paths = _listar_bancos_vendas_tenant(client_id, loja)
        unidades_encontradas = {}  # Dict para armazenar nome -> uid
        unidade_ids_encontradas = set()  # IDs encontrados mesmo quando nome está vazio
        mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)

        # Banco dedicado à loja específica — não precisamos filtrar por loja_conta nele
        db_loja_dedicado = _get_vendas_db_path(client_id, loja) if loja and str(loja).strip() not in ("", "__todas") else None

        for db_path in db_paths:
            if not os.path.exists(db_path):
                continue
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                # Aplica filtro de loja_conta apenas nos bancos NÃO dedicados à loja
                # (o banco dedicado já contém só dados daquela loja por definição)
                loja_clause = ""
                loja_params: list = []
                if loja and str(loja).strip() and str(loja).strip() not in ("__todas", "") \
                        and db_path != db_loja_dedicado:
                    filtro_loja_sql, filtro_loja_p = _sql_filtro_loja_vendas(str(loja).strip())
                    if filtro_loja_sql:
                        loja_clause = filtro_loja_sql  # já inclui " AND ..."
                        loja_params = filtro_loja_p

                if sku:
                    params_q: list = [str(sku).strip()]
                    date_clause = ""
                    if data_inicio:
                        date_clause += " AND date(data) >= ?"
                        params_q.append(data_inicio)
                    if data_fim:
                        date_clause += " AND date(data) <= ?"
                        params_q.append(data_fim)
                    params_q.extend(loja_params)
                    rows = cur.execute(
                        f"""
                        SELECT DISTINCT
                            trim(coalesce(unidade_id, '')) AS unidade_id,
                            trim(coalesce(unidade_negocio, '')) AS unidade_negocio,
                            trim(coalesce(loja_id, '')) AS loja_id,
                            trim(coalesce(intermediador_nome, '')) AS intermediador_nome,
                            trim(coalesce(intermediador_cnpj, '')) AS intermediador_cnpj,
                            trim(coalesce(canal, '')) AS canal
                        FROM vendas
                        WHERE (coalesce(trim(unidade_id), '') != ''
                           OR coalesce(trim(unidade_negocio), '') != ''
                           OR coalesce(trim(canal), '') != ''
                           OR coalesce(trim(intermediador_nome), '') != '')
                          AND trim(coalesce(sku, '')) = ?
                          AND coalesce(devolucao, 0) = 0
                          {date_clause}
                          {loja_clause}
                        """,
                        params_q
                    ).fetchall()
                else:
                    params_q = list(loja_params)
                    rows = cur.execute(
                        f"""
                        SELECT DISTINCT
                            trim(coalesce(unidade_id, '')) AS unidade_id,
                            trim(coalesce(unidade_negocio, '')) AS unidade_negocio,
                            trim(coalesce(loja_id, '')) AS loja_id,
                            trim(coalesce(intermediador_nome, '')) AS intermediador_nome,
                            trim(coalesce(intermediador_cnpj, '')) AS intermediador_cnpj,
                            trim(coalesce(canal, '')) AS canal
                        FROM vendas
                        WHERE (coalesce(trim(unidade_id), '') != ''
                           OR coalesce(trim(unidade_negocio), '') != ''
                           OR coalesce(trim(canal), '') != ''
                           OR coalesce(trim(intermediador_nome), '') != '')
                          {loja_clause}
                        """,
                        params_q
                    ).fetchall()
                for row in rows:
                    if not row:
                        continue
                    uid = str(row[0] or "").strip()
                    nome = str(row[1] or "").strip()
                    loja_id = str(row[2] or "").strip()
                    intermediador_nome = str(row[3] or "").strip()
                    intermediador_cnpj = str(row[4] or "").strip()
                    canal = str(row[5] or "").strip()
                    if uid:
                        unidade_ids_encontradas.add(uid)
                    if nome:
                        unidades_encontradas[nome] = uid

                    # Resolve nome virtual com mesma prioridade usada na carga de vendas.
                    nome_resolvido = _resolver_nome_loja_virtual(
                        mapa_lojas_cliente,
                        loja_id=loja_id,
                        unidade_id=uid,
                        nome_oficial=nome,
                        intermediador_nome=intermediador_nome,
                        intermediador_cnpj=intermediador_cnpj,
                        canal=canal,
                    )
                    nome_resolvido = _normalizar_unidade_negocio_ml(nome_resolvido, canal, prefer_full=False)
                    if nome_resolvido:
                        unidades_encontradas[nome_resolvido] = uid
            finally:
                conn.close()
        
        # Carregar mapeamento atual
        mapeamento = _carregar_mapeamento_unidades()
        
        # Construir lista de unidades com nomes (compatível com vendas.html)
        unidades = []
        for nome_db in sorted(unidades_encontradas.keys()):
            uid = unidades_encontradas.get(nome_db) or ""
            nome_map = str((mapeamento or {}).get(uid) or "").strip() if uid else ""
            nome_final = nome_map or nome_db
            if not nome_final:
                continue
            unidades.append({
                "nome": nome_final,
                "editavel": True
            })

        # Garante inclusão de unidades cujo nome no banco está vazio, mas há mapeamento por unidade_id.
        nomes_existentes_norm = {_normalizar_texto(str(item.get("nome") or "")) for item in unidades}
        for uid in sorted(unidade_ids_encontradas):
            nome_map = str((mapeamento or {}).get(uid) or "").strip()
            if not nome_map:
                continue
            nome_norm = _normalizar_texto(nome_map)
            if nome_norm in nomes_existentes_norm:
                continue
            unidades.append({
                "nome": nome_map,
                "editavel": True
            })
            nomes_existentes_norm.add(nome_norm)
        
        return {
            "unidades": sorted(unidades, key=lambda x: x["nome"]),
            "mapeamento": mapeamento
        }
    except Exception as e:
        return {"error": str(e), "unidades": [], "mapeamento": {}}

async def atualizar_unidade_negocio(
    nome_antigo: str,
    nome_novo: str,
    client_id: str = Depends(vendas_context.get_tenant_id)
):
    """Atualiza o nome de uma unidade de negócio no banco de dados"""
    try:
        db_path = _get_vendas_db(client_id)
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="Banco de dados não encontrado")
        
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE vendas SET unidade_negocio = ? WHERE unidade_negocio = ?",
                (nome_novo, nome_antigo)
            )
            conn.commit()
            linhas_afetadas = cur.rowcount
            return {
                "success": True,
                "message": f"{linhas_afetadas} registros atualizados",
                "linhas_afetadas": linhas_afetadas
            }
        finally:
            conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def salvar_mapeamento_unidades(mapeamento: dict, client_id: str = Depends(vendas_context.get_tenant_id)):
    """Salva o mapeamento de IDs para nomes de unidades"""
    try:
        sucesso = _salvar_mapeamento_unidades(mapeamento)
        if sucesso:
            return {"success": True, "message": "Mapeamento salvo com sucesso"}
        else:
            raise HTTPException(status_code=500, detail="Erro ao salvar mapeamento")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



def configure_vendas_unidades_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_vendas_unidades_runtime()

__all__ = [
    "configure_vendas_unidades_runtime",
    "listar_unidades_negocios",
    "atualizar_unidade_negocio",
    "salvar_mapeamento_unidades",
]
