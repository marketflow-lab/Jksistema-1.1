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

async def grafico_vendas(
    periodo: str = "3m",
    intervalo: str = "dia",
    sku: str = None,
    data_inicio: str = None,
    data_fim: str = None,
    loja: str = None,
    unidade_negocio: str = None,
    mostrar_estoque_geral: bool = False,
    mostrar_estoque_sku: bool = False,
    client_id: str = Depends(vendas_context.get_tenant_id)
):
    """
    Endpoint para gerar dados de gráfico de vendas
    periodo: 3m, 6m, 1a, 2a, max
    intervalo: dia, semana, mes
    sku: filtro opcional por SKU
    """
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return {
            "labels": [],
            "valores_vendas": [],
            "quantidades_vendas": [],
            "valores_devolucoes": [],
            "quantidades_devolucoes": [],
            "estoque_geral": [],
            "estoque_sku": [],
            "estoque_meta": {
                "success": True,
                "requested": bool(mostrar_estoque_geral or mostrar_estoque_sku),
                "lojas": 0,
                "sku": _normalizar_sku_estoque(sku) if sku else None,
                "detail": "Sem bancos de vendas para o filtro informado.",
            },
        }

    try:
        hoje = datetime.now()
        data_inicio_dt = None
        data_fim_str = None

        if data_inicio:
            try:
                data_inicio_dt = datetime.fromisoformat(data_inicio)
            except ValueError:
                raise HTTPException(status_code=400, detail="data_inicio invalida. Use YYYY-MM-DD.")

        if data_fim:
            try:
                datetime.fromisoformat(data_fim)
                data_fim_str = data_fim
            except ValueError:
                raise HTTPException(status_code=400, detail="data_fim invalida. Use YYYY-MM-DD.")

        # Se nenhuma data for informada explicitamente, mantém a lógica por período.
        if data_inicio_dt is None:
            if periodo == "3m":
                data_inicio_dt = hoje - timedelta(days=90)
            elif periodo == "6m":
                data_inicio_dt = hoje - timedelta(days=180)
            elif periodo == "1a":
                data_inicio_dt = hoje - timedelta(days=365)
            elif periodo == "2a":
                data_inicio_dt = hoje - timedelta(days=730)
            elif periodo == "max":
                menor_data = None
                for path in db_paths:
                    if not os.path.exists(path):
                        continue
                    conn_tmp = sqlite3.connect(path)
                    try:
                        cur_tmp = conn_tmp.cursor()
                        cur_tmp.execute("SELECT MIN(date(data)) FROM vendas")
                        row = cur_tmp.fetchone()
                        if row and row[0]:
                            data_tmp = datetime.fromisoformat(str(row[0]))
                            if menor_data is None or data_tmp < menor_data:
                                menor_data = data_tmp
                    finally:
                        conn_tmp.close()
                data_inicio_dt = menor_data or (hoje - timedelta(days=90))
            else:
                data_inicio_dt = hoje - timedelta(days=90)

        query = """
            SELECT id_unico, data, devolucao, sku, quantidade, valor, loja_conta, unidade_negocio, numero, comprador, canal
            FROM vendas
            WHERE date(data) >= ?
              AND coalesce(devolucao, 0) = 0
        """
        params = [data_inicio_dt.strftime("%Y-%m-%d")]
        if data_fim_str:
            query += " AND date(data) <= ?"
            params.append(data_fim_str)
        if sku:
            query += " AND sku = ?"
            params.append(sku)
        if loja and loja != "__todas":
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
            if filtro_loja_sql:
                query += filtro_loja_sql
                params.extend(filtro_loja_params)
        if unidade_negocio and unidade_negocio != "__todos":
            mapa_lojas_grafico = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
            filtro_unidade_sql, filtro_unidade_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_lojas_grafico)
            if filtro_unidade_sql:
                query += filtro_unidade_sql
                params.extend(filtro_unidade_params)
        query += " ORDER BY data"
        rows = []
        for path in db_paths:
            if not os.path.exists(path):
                continue
            conn_db = sqlite3.connect(path)
            try:
                cur_db = conn_db.cursor()
                cur_db.execute(query, params)
                rows.extend(cur_db.fetchall())
            finally:
                conn_db.close()
        
        # Evita contabilizar a mesma venda duas vezes ao consolidar bancos do tenant.
        rows_unicos = []
        vistos = set()
        for row in rows:
            if _deve_excluir_venda_ebazar(row[2], row[9], row[10]):
                continue
            id_unico = str(row[0] or "").strip()
            if id_unico:
                chave = ("id_unico", id_unico)
            else:
                chave = (
                    "fallback",
                    str(row[1] or "")[:10],
                    str(row[6] or "").strip().lower(),
                    str(row[8] or "").strip(),
                    str(row[3] or "").strip().upper(),
                    float(row[4] or 0),
                    float(row[5] or 0),
                    str(row[9] or "").strip().lower(),
                )
            if chave in vistos:
                continue
            vistos.add(chave)
            rows_unicos.append(row)

        dados_agrupados = {}

        def _chave_label_intervalo(data_str):
            if intervalo == "dia":
                chave = data_str[:10]
                label = chave
            elif intervalo == "semana":
                dt = datetime.fromisoformat(data_str[:10])
                inicio_semana = dt - timedelta(days=dt.weekday())
                chave = inicio_semana.strftime("%Y-%m-%d")
                label = inicio_semana.strftime("%d/%m/%Y")
            elif intervalo == "mes":
                chave = data_str[:7]
                mes, ano = data_str[5:7], data_str[:4]
                meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
                label = f"{meses[int(mes)-1]}/{ano}"
            else:
                chave = data_str[:10]
                label = chave
            return chave, label

        def _label_por_chave(chave: str) -> str:
            if intervalo == "dia":
                return chave
            if intervalo == "semana":
                dt = datetime.fromisoformat(chave)
                return dt.strftime("%d/%m/%Y")
            if intervalo == "mes":
                ano, mes = chave.split("-")
                meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
                return f"{meses[int(mes)-1]}/{ano}"
            return chave

        def _chaves_periodo_completo(data_inicio_ref: datetime, data_fim_ref: datetime):
            chaves = []
            if intervalo == "dia":
                atual = data_inicio_ref.date()
                fim = data_fim_ref.date()
                while atual <= fim:
                    chaves.append(atual.strftime("%Y-%m-%d"))
                    atual += timedelta(days=1)
                return chaves

            if intervalo == "semana":
                atual = (data_inicio_ref - timedelta(days=data_inicio_ref.weekday())).date()
                fim = (data_fim_ref - timedelta(days=data_fim_ref.weekday())).date()
                while atual <= fim:
                    chaves.append(atual.strftime("%Y-%m-%d"))
                    atual += timedelta(days=7)
                return chaves

            if intervalo == "mes":
                ano_mes_ini = datetime(data_inicio_ref.year, data_inicio_ref.month, 1)
                ano_mes_fim = datetime(data_fim_ref.year, data_fim_ref.month, 1)
                atual = ano_mes_ini
                while atual <= ano_mes_fim:
                    chaves.append(atual.strftime("%Y-%m"))
                    if atual.month == 12:
                        atual = datetime(atual.year + 1, 1, 1)
                    else:
                        atual = datetime(atual.year, atual.month + 1, 1)
                return chaves

            return chaves

        for row in rows_unicos:
            data_str = row[1]
            qtd = row[4]
            valor = row[5]
            
            chave, label = _chave_label_intervalo(data_str)
            
            if chave not in dados_agrupados:
                dados_agrupados[chave] = {
                    "label": label,
                    "vendas_valor": 0,
                    "vendas_qtd": 0,
                    "devol_valor": 0,
                    "devol_qtd": 0
                }
            
            dados_agrupados[chave]["vendas_valor"] += valor or 0
            dados_agrupados[chave]["vendas_qtd"] += qtd or 0

        # Adicionar devoluções a partir de notas de entrada (consolidando bancos por loja)
        for db_path_nf in db_paths:
            if not os.path.exists(db_path_nf):
                continue
            conn_nf = sqlite3.connect(db_path_nf)
            try:
                cur_nf = conn_nf.cursor()
                query_nf = """
                    SELECT data_emissao, quantidade, valor_total
                    FROM notas_entrada_itens
                    WHERE devolucao = 1 AND date(data_emissao) >= ?
                """
                params_nf = [data_inicio_dt.strftime("%Y-%m-%d")]
                if data_fim_str:
                    query_nf += " AND date(data_emissao) <= ?"
                    params_nf.append(data_fim_str)
                if sku:
                    query_nf += " AND sku = ?"
                    params_nf.append(sku)
                filtro_nf_sql, filtro_nf_params = _sql_filtro_unidade_devolucao(unidade_negocio)
                if filtro_nf_sql:
                    query_nf += filtro_nf_sql
                    params_nf.extend(filtro_nf_params)
                filtro_loja_nf_sql, filtro_loja_nf_params = _sql_filtro_loja_notas_entrada(cur_nf, loja)
                if filtro_loja_nf_sql:
                    query_nf += filtro_loja_nf_sql
                    params_nf.extend(filtro_loja_nf_params)
                query_nf += " ORDER BY data_emissao"
                cur_nf.execute(query_nf, params_nf)
                rows_nf = cur_nf.fetchall()

                for row in rows_nf:
                    data_str = row[0]
                    qtd = row[1]
                    valor = row[2]
                    chave, label = _chave_label_intervalo(data_str)

                    if chave not in dados_agrupados:
                        dados_agrupados[chave] = {
                            "label": label,
                            "vendas_valor": 0,
                            "vendas_qtd": 0,
                            "devol_valor": 0,
                            "devol_qtd": 0
                        }

                    dados_agrupados[chave]["devol_valor"] += valor or 0
                    dados_agrupados[chave]["devol_qtd"] += qtd or 0
            except sqlite3.OperationalError:
                # Alguns bancos podem não ter tabela de notas ainda.
                pass
            finally:
                conn_nf.close()
        
        labels = []
        valores_vendas = []
        quantidades_vendas = []
        valores_devolucoes = []
        quantidades_devolucoes = []

        data_fim_dt = datetime.fromisoformat(data_fim_str) if data_fim_str else hoje
        chaves_periodo = _chaves_periodo_completo(data_inicio_dt, data_fim_dt)

        for chave in chaves_periodo:
            dados = dados_agrupados.get(chave, {
                "label": _label_por_chave(chave),
                "vendas_valor": 0,
                "vendas_qtd": 0,
                "devol_valor": 0,
                "devol_qtd": 0,
            })
            labels.append(dados["label"])
            valores_vendas.append(dados["vendas_valor"])
            quantidades_vendas.append(dados["vendas_qtd"])
            valores_devolucoes.append(dados["devol_valor"])
            quantidades_devolucoes.append(dados["devol_qtd"])

        estoque_series = _vendas_series_estoque_historico(
            client_id=client_id,
            loja=loja,
            intervalo=intervalo,
            data_inicio_ref=data_inicio_dt,
            data_fim_ref=data_fim_dt,
            chaves_periodo=chaves_periodo,
            mostrar_estoque_geral=mostrar_estoque_geral,
            mostrar_estoque_sku=mostrar_estoque_sku,
            sku=sku,
        )
        
        return {
            "labels": labels,
            "valores_vendas": valores_vendas,
            "quantidades_vendas": quantidades_vendas,
            "valores_devolucoes": valores_devolucoes,
            "quantidades_devolucoes": quantidades_devolucoes,
            **estoque_series,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao gerar gráfico de vendas: {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar gráfico de vendas.")

async def skus_sem_venda(
    loja: str = None,
    unidade_negocio: str = None,
    client_id: str = Depends(vendas_context.get_tenant_id)
):
    """
    Retorna SKUs que não têm vendas há 30, 60 e 90 dias e que possuem estoque.
    Agrupa por períodos: ultimos_30_dias, ultimos_60_dias, ultimos_90_dias
    """
    try:
        # Carregar estoque compilado
        arquivo_cliente = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
        estoque_data = {}
        
        if arquivo_cliente and os.path.exists(arquivo_cliente):
            try:
                df_estoque = pd.read_csv(arquivo_cliente).fillna("")
                # Colunas que podem conter quantidade de estoque
                colunas_estoque = ["saldo_loja", "saldo_full", "quantidade_estoque", "saldo_estoque", "estoque", "quantidade", "saldo"]
                coluna_estoque_usada = None
                for col in colunas_estoque:
                    if col in df_estoque.columns:
                        coluna_estoque_usada = col
                        break
                
                if coluna_estoque_usada:
                    for idx, row in df_estoque.iterrows():
                        sku = str(row.get("sku", "")).strip().upper()
                        if sku:
                            try:
                                qtd = float(row.get(coluna_estoque_usada, 0) or 0)
                                estoque_data[sku] = {"quantidade": qtd, "produto": str(row.get("nome_bling", row.get("produto", row.get("nome", "-")))).strip()}
                            except:
                                pass
            except Exception as e:
                logger.warning(f"Erro ao ler estoque compilado: {e}")
        
        # Se não encontrou dados de estoque compilado, retorna vazio
        if not estoque_data:
            return {
                "ultimos_30_dias": [],
                "ultimos_60_dias": [],
                "ultimos_90_dias": [],
                "total_skus_sem_estoque": 0
            }
        
        # Carregar bancos de vendas
        db_paths = _listar_bancos_vendas_tenant(client_id, loja)
        hoje = datetime.now()
        dias_7 = (hoje - timedelta(days=7)).strftime("%Y-%m-%d")
        dias_15 = (hoje - timedelta(days=15)).strftime("%Y-%m-%d")
        dias_30 = (hoje - timedelta(days=30)).strftime("%Y-%m-%d")
        dias_60 = (hoje - timedelta(days=60)).strftime("%Y-%m-%d")
        dias_90 = (hoje - timedelta(days=90)).strftime("%Y-%m-%d")
        
        # Mapa de última data de venda por SKU
        ultima_venda_por_sku = {}
        
        for db_path in db_paths:
            if not os.path.exists(db_path):
                continue
            
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                query = """
                    SELECT UPPER(trim(coalesce(sku,''))), MAX(date(data))
                    FROM vendas
                    WHERE coalesce(devolucao, 0) = 0 AND trim(coalesce(sku,'')) != ''
                    GROUP BY UPPER(trim(coalesce(sku,'')))
                """
                params = []
                
                if loja and loja != "__todas":
                    filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                    if filtro_loja_sql:
                        query_insert = "SELECT UPPER(trim(coalesce(sku,''))), MAX(date(data)) FROM vendas WHERE coalesce(devolucao, 0) = 0 AND trim(coalesce(sku,'')) != ''"
                        query_insert += filtro_loja_sql
                        query_insert += " GROUP BY UPPER(trim(coalesce(sku,'')))"
                        query = query_insert
                        params.extend(filtro_loja_params)
                
                if unidade_negocio and unidade_negocio != "__todos":
                    mapa_unidade = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
                    filtro_un_sql, filtro_un_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_unidade)
                    if filtro_un_sql and unidade_negocio:
                        if "WHERE" in query:
                            query += filtro_un_sql
                        else:
                            query += " WHERE coalesce(devolucao, 0) = 0 AND trim(coalesce(sku,'')) != ''" + filtro_un_sql
                        params.extend(filtro_un_params)
                
                cur.execute(query, params)
                rows = cur.fetchall()
                
                for row in rows:
                    sku = row[0]
                    data_venda = row[1]
                    # Manter a venda mais recente por SKU
                    if sku not in ultima_venda_por_sku or (data_venda and data_venda > ultima_venda_por_sku[sku]):
                        ultima_venda_por_sku[sku] = data_venda
            except Exception as e:
                logger.warning(f"Erro ao consultar vendas: {e}")
            finally:
                conn.close()
        
        # Categorizar SKUs por período sem venda
        resultado = {
            "ultimos_7_dias": [],
            "ultimos_15_dias": [],
            "ultimos_30_dias": [],
            "ultimos_60_dias": [],
            "ultimos_90_dias": [],
            "total_skus_com_estoque": 0
        }
        
        for sku, estoque_info in estoque_data.items():
            qtd_estoque = estoque_info.get("quantidade", 0)
            # Só incluir SKUs com estoque
            if qtd_estoque <= 0:
                continue
            
            resultado["total_skus_com_estoque"] += 1
            
            ultima_venda = ultima_venda_por_sku.get(sku)
            dias_sem_vender = None if ultima_venda is None else (hoje - datetime.fromisoformat(ultima_venda)).days
            item_base = {
                "sku": sku,
                "produto": estoque_info.get("produto", "-"),
                "estoque": qtd_estoque,
                "ultima_venda": ultima_venda or "Nunca vendido",
                "dias_sem_vender": dias_sem_vender
            }
            
            # Se nunca foi vendido ou última venda foi há 90+ dias
            if ultima_venda is None or ultima_venda <= dias_90:
                resultado["ultimos_90_dias"].append(item_base)
            # Última venda entre 60-90 dias
            elif ultima_venda <= dias_60:
                resultado["ultimos_60_dias"].append(item_base)
            # Última venda entre 30-60 dias
            elif ultima_venda <= dias_30:
                resultado["ultimos_30_dias"].append(item_base)
            # Última venda entre 15-30 dias
            elif ultima_venda <= dias_15:
                resultado["ultimos_15_dias"].append(item_base)
            # Última venda entre 7-15 dias
            elif ultima_venda <= dias_7:
                resultado["ultimos_7_dias"].append(item_base)
        
        # Ordenar por quantidade de estoque (descendente)
        resultado["ultimos_7_dias"].sort(key=lambda x: x["estoque"], reverse=True)
        resultado["ultimos_15_dias"].sort(key=lambda x: x["estoque"], reverse=True)
        resultado["ultimos_30_dias"].sort(key=lambda x: x["estoque"], reverse=True)
        resultado["ultimos_60_dias"].sort(key=lambda x: x["estoque"], reverse=True)
        resultado["ultimos_90_dias"].sort(key=lambda x: x["dias_sem_vender"] if x["dias_sem_vender"] else 999999, reverse=True)
        
        return resultado
    except Exception as e:
        logger.exception(f"Erro ao buscar SKUs sem venda: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar SKUs sem venda.")



def configure_vendas_grafico_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_vendas_grafico_runtime()

__all__ = [
    "configure_vendas_grafico_runtime",
    "grafico_vendas",
    "skus_sem_venda",
]
