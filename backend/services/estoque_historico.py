"""Estoque historical snapshots and chart helpers."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
from fastapi import Depends, HTTPException

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoquePreferenciasColunasRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_context
from backend.services.runtime_bridge import bind_runtime_globals


def _sync_context_names() -> None:
    for name in estoque_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(estoque_context, name)


def configure_estoque_historico_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_historico_runtime()

def _estoque_historico_db_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "estoque_historico.db")

def _garantir_tabela_historico_estoque(client_id: str) -> None:
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_historico (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ref TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                nome_bling TEXT,
                situacao_bling TEXT,
                ncm_bling TEXT,
                saldo_loja REAL NOT NULL DEFAULT 0,
                saldo_full REAL NOT NULL DEFAULT 0,
                saldo_total REAL NOT NULL DEFAULT 0,
                UNIQUE (data_ref, loja_sync, sku)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_hist_data_loja ON estoque_historico (data_ref, loja_sync)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_hist_sku ON estoque_historico (sku)"
        )
        conn.commit()
    finally:
        conn.close()

def _registrar_snapshot_historico_estoque(client_id: str, loja_sync: str, registros: list[dict]) -> int:
    _garantir_tabela_historico_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        agora_iso = datetime.now().isoformat(timespec="seconds")
        cur = conn.cursor()
        total = 0

        for r in (registros or []):
            sku = str((r or {}).get("sku") or "").strip()
            if not sku:
                continue

            saldo_loja = float((r or {}).get("saldo_loja") or 0)
            saldo_full = float((r or {}).get("saldo_full") or 0)
            saldo_total = saldo_loja + saldo_full

            cur.execute(
                """
                INSERT INTO estoque_historico (
                    data_ref, recorded_at, loja_sync, sku,
                    id_bling, nome_bling, situacao_bling, ncm_bling,
                    saldo_loja, saldo_full, saldo_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_ref, loja_sync, sku) DO UPDATE SET
                    recorded_at=excluded.recorded_at,
                    id_bling=excluded.id_bling,
                    nome_bling=excluded.nome_bling,
                    situacao_bling=excluded.situacao_bling,
                    ncm_bling=excluded.ncm_bling,
                    saldo_loja=excluded.saldo_loja,
                    saldo_full=excluded.saldo_full,
                    saldo_total=excluded.saldo_total
                """,
                (
                    hoje,
                    agora_iso,
                    str(loja_sync or "").strip(),
                    sku,
                    str((r or {}).get("id_bling") or "").strip(),
                    str((r or {}).get("nome_bling") or "").strip(),
                    str((r or {}).get("situacao_bling") or "").strip(),
                    str((r or {}).get("ncm_bling") or "").strip(),
                    saldo_loja,
                    saldo_full,
                    saldo_total,
                ),
            )
            total += 1

        conn.commit()
        return total
    finally:
        conn.close()

def _normalizar_sku_estoque(v: Any) -> str:
    return str(v or "").strip().upper()

def _label_mes_estoque(chave: str) -> str:
    ano, mes = chave.split("-")
    meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
    return f"{meses[int(mes)-1]}/{ano}"

def _chave_intervalo_estoque(data_ref: datetime, intervalo: str) -> tuple[str, str]:
    if intervalo == "dia":
        chave = data_ref.strftime("%Y-%m-%d")
        return chave, chave
    if intervalo == "semana":
        inicio_semana = data_ref - timedelta(days=data_ref.weekday())
        chave = inicio_semana.strftime("%Y-%m-%d")
        return chave, inicio_semana.strftime("%d/%m/%Y")
    if intervalo == "mes":
        chave = data_ref.strftime("%Y-%m")
        return chave, _label_mes_estoque(chave)
    raise HTTPException(status_code=400, detail="intervalo invalido. Use dia, semana ou mes.")

def _inicio_periodo_estoque(
    periodo: str,
    data_inicio: str | None,
    data_fim_ref: datetime,
    client_id: str,
    loja: str,
) -> datetime:
    if data_inicio:
        try:
            return datetime.fromisoformat(data_inicio)
        except ValueError:
            raise HTTPException(status_code=400, detail="data_inicio invalida. Use YYYY-MM-DD.")

    periodo_norm = str(periodo or "3m").strip().lower()
    if periodo_norm == "3m":
        return data_fim_ref - timedelta(days=90)
    if periodo_norm == "6m":
        return data_fim_ref - timedelta(days=180)
    if periodo_norm == "1a":
        return data_fim_ref - timedelta(days=365)
    if periodo_norm == "2a":
        return data_fim_ref - timedelta(days=730)
    if periodo_norm == "max":
        db_hist = _estoque_historico_db_path(client_id)
        if not os.path.exists(db_hist):
            return data_fim_ref - timedelta(days=90)
        conn = sqlite3.connect(db_hist)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT MIN(date(data_ref))
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                """,
                (str(loja or "").strip(),),
            )
            row = cur.fetchone()
            if row and row[0]:
                return datetime.fromisoformat(str(row[0]))
            return data_fim_ref - timedelta(days=90)
        finally:
            conn.close()

    return data_fim_ref - timedelta(days=90)

def _vendas_series_estoque_historico(
    client_id: str,
    loja: str | None,
    intervalo: str,
    data_inicio_ref: datetime,
    data_fim_ref: datetime,
    chaves_periodo: list[str],
    mostrar_estoque_geral: bool,
    mostrar_estoque_sku: bool,
    sku: str | None,
) -> dict:
    total_pontos = len(chaves_periodo or [])
    incluir_geral = bool(mostrar_estoque_geral)
    sku_norm = _normalizar_sku_estoque(sku) if sku else ""
    incluir_sku = bool(mostrar_estoque_sku and sku_norm)
    resultado = {
        "estoque_geral": [None] * total_pontos if incluir_geral else [],
        "estoque_sku": [None] * total_pontos if incluir_sku else [],
        "estoque_meta": {
            "success": True,
            "requested": bool(mostrar_estoque_geral or mostrar_estoque_sku),
            "lojas": 0,
            "sku": sku_norm or None,
            "detail": "",
        },
    }

    if not (incluir_geral or incluir_sku):
        if mostrar_estoque_sku and not sku_norm:
            resultado["estoque_meta"]["detail"] = "Informe um SKU para exibir estoque por SKU."
        return resultado

    db_hist = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_hist):
        resultado["estoque_meta"]["detail"] = "Histórico de estoque ainda não foi gerado."
        return resultado

    loja_txt = str(loja or "").strip()
    loja_especifica = bool(loja_txt and loja_txt != "__todas")
    data_fim_str = data_fim_ref.strftime("%Y-%m-%d")

    conn_hist = sqlite3.connect(db_hist)
    try:
        cur_hist = conn_hist.cursor()
        if loja_especifica:
            cur_hist.execute(
                """
                SELECT lower(trim(loja_sync)) AS loja_ref, MAX(date(data_ref)) AS data_base
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                  AND date(data_ref) <= date(?)
                GROUP BY lower(trim(loja_sync))
                """,
                (loja_txt, data_fim_str),
            )
        else:
            cur_hist.execute(
                """
                SELECT lower(trim(loja_sync)) AS loja_ref, MAX(date(data_ref)) AS data_base
                FROM estoque_historico
                WHERE trim(coalesce(loja_sync, '')) != ''
                  AND date(data_ref) <= date(?)
                GROUP BY lower(trim(loja_sync))
                """,
                (data_fim_str,),
            )
        lojas_base = [
            (str(row[0] or "").strip(), str(row[1] or "").strip())
            for row in cur_hist.fetchall()
            if str(row[0] or "").strip() and str(row[1] or "").strip()
        ]

        if not lojas_base:
            resultado["estoque_meta"]["detail"] = "Sem snapshot de estoque no período solicitado."
            return resultado

        _garantir_tabela_lancamentos_estoque(client_id)
        bucket_geral: dict[str, float] = {}
        bucket_sku: dict[str, float] = {}
        inicio_periodo_date = data_inicio_ref.date()
        fim_periodo_date = data_fim_ref.date()

        def somar_snapshot(loja_ref: str, data_base: str, apenas_sku: str = "") -> float | None:
            query = """
                SELECT SUM(coalesce(saldo_loja, 0))
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) = date(?)
            """
            params = [loja_ref, data_base]
            if apenas_sku:
                query += " AND upper(trim(sku)) = ?"
                params.append(apenas_sku)
            cur_hist.execute(query, params)
            row = cur_hist.fetchone()
            if not row or row[0] is None:
                return None
            return float(row[0] or 0)

        def carregar_movimentos(
            loja_ref: str,
            data_inicio_calc: str,
            data_base: str,
            apenas_sku: str = "",
        ) -> tuple[dict[str, float], dict[str, float]]:
            entradas: dict[str, float] = {}
            saidas: dict[str, float] = {}
            query = """
                SELECT date(data_ref) AS data_ref,
                       SUM(coalesce(entrada, 0)) AS entradas,
                       SUM(coalesce(saida, 0)) AS saidas
                FROM estoque_lancamentos
                WHERE lower(trim(loja_sync)) = ?
                  AND date(data_ref) >= date(?)
                  AND date(data_ref) <= date(?)
            """
            params = [loja_ref, data_inicio_calc, data_base]
            if apenas_sku:
                query += " AND upper(trim(sku)) = ?"
                params.append(apenas_sku)
            query += " GROUP BY date(data_ref)"
            cur_hist.execute(query, params)
            for data_ref, entradas_raw, saidas_raw in cur_hist.fetchall():
                data_ref_str = str(data_ref or "").strip()
                if not data_ref_str:
                    continue
                entradas[data_ref_str] = float(entradas_raw or 0)
                saidas[data_ref_str] = float(saidas_raw or 0)
            return entradas, saidas

        def acumular_buckets(
            destino: dict[str, float],
            loja_ref: str,
            valor_base: float | None,
            data_base: str,
            movimentos_sku: str = "",
        ) -> None:
            if valor_base is None:
                return
            data_base_dt = datetime.fromisoformat(data_base)
            data_inicio_calc = min(data_inicio_ref, data_base_dt).strftime("%Y-%m-%d")
            entradas, saidas = carregar_movimentos(
                loja_ref,
                data_inicio_calc,
                data_base,
                movimentos_sku,
            )
            saldo_atual = float(valor_base or 0)
            data_cursor = data_base_dt.date()
            limite_calc = datetime.fromisoformat(data_inicio_calc).date()
            buckets_loja: dict[str, float] = {}
            while data_cursor >= limite_calc:
                data_cursor_str = data_cursor.strftime("%Y-%m-%d")
                if inicio_periodo_date <= data_cursor <= fim_periodo_date:
                    chave_bucket, _label_bucket = _chave_intervalo_estoque(
                        datetime.combine(data_cursor, datetime.min.time()),
                        intervalo,
                    )
                    if chave_bucket not in buckets_loja:
                        buckets_loja[chave_bucket] = saldo_atual

                entradas_dia = float(entradas.get(data_cursor_str, 0) or 0)
                saidas_dia = float(saidas.get(data_cursor_str, 0) or 0)
                saldo_atual = saldo_atual - entradas_dia + saidas_dia
                data_cursor -= timedelta(days=1)

            for chave_bucket, valor_bucket in buckets_loja.items():
                destino[chave_bucket] = float(destino.get(chave_bucket, 0) or 0) + float(valor_bucket or 0)

        for loja_ref_atual, data_base in lojas_base:
            if incluir_geral:
                total_base = somar_snapshot(loja_ref_atual, data_base)
                acumular_buckets(bucket_geral, loja_ref_atual, total_base, data_base)
            if incluir_sku:
                total_sku_base = somar_snapshot(loja_ref_atual, data_base, sku_norm)
                acumular_buckets(bucket_sku, loja_ref_atual, total_sku_base, data_base, sku_norm)

        for idx, chave in enumerate(chaves_periodo or []):
            if incluir_geral and chave in bucket_geral:
                resultado["estoque_geral"][idx] = round(float(bucket_geral[chave] or 0), 2)
            if incluir_sku and chave in bucket_sku:
                resultado["estoque_sku"][idx] = round(float(bucket_sku[chave] or 0), 2)

        resultado["estoque_meta"].update({
            "lojas": len(lojas_base),
            "detail": "",
        })
        return resultado
    except sqlite3.OperationalError as exc:
        logger.warning(f"Falha ao consultar histórico de estoque para gráfico de vendas: {exc}")
        resultado["estoque_meta"].update({
            "success": False,
            "detail": "Não foi possível consultar o histórico de estoque.",
        })
        return resultado
    finally:
        conn_hist.close()


__all__ = [
    "configure_estoque_historico_runtime",
    "_estoque_historico_db_path",
    "_garantir_tabela_historico_estoque",
    "_registrar_snapshot_historico_estoque",
    "_normalizar_sku_estoque",
    "_label_mes_estoque",
    "_chave_intervalo_estoque",
    "_inicio_periodo_estoque",
    "_vendas_series_estoque_historico",
]
