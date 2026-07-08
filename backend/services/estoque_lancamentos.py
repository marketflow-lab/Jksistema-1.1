"""Bling stock movement synchronization for Estoque."""

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


def configure_estoque_lancamentos_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_lancamentos_runtime()

from backend.services.estoque_common import (
    _criar_progresso,
    _estoque_lanc_log,
    _set_estoque_lanc_progresso,
)
from backend.services.estoque_historico import (
    _chave_intervalo_estoque,
    _estoque_historico_db_path,
    _garantir_tabela_historico_estoque,
    _inicio_periodo_estoque,
    _normalizar_sku_estoque,
)

def _bling_listar_lotes_produto(access_token: str, produto_id: str) -> tuple[list[dict], int]:
    pid = str(produto_id or "").strip()
    if not pid:
        return [], 200
    headers = {"Authorization": f"Bearer {access_token}"}
    # Endpoint correto para listagem de lotes ÃƒÂ© /produtos/lotes com filtro por produto.
    url = "https://api.bling.com.br/Api/v3/produtos/lotes"
    lotes: list[dict] = []
    for pagina in range(1, 1000):
        resp = BLING_SESSION.get(
            url,
            headers=headers,
            params={"pagina": pagina, "limite": 100, "idsProdutos[]": pid},
            timeout=20,
        )
        if resp.status_code == 401:
            return [], 401
        if resp.status_code == 403:
            try:
                payload = resp.json() or {}
                err_type = str(((payload.get("error") or {}).get("type") or "")).strip().lower()
                if err_type == "insufficient_scope":
                    return [], 403
            except Exception:
                pass
        if resp.status_code in (400, 404):
            # Alguns produtos nÃ£o possuem lotes vinculados ou foram removidos no Bling.
            # Nesses casos, tratamos como ausÃƒÂªncia de lanÃƒÂ§amentos para nÃ£o quebrar o lote.
            try:
                payload = resp.json() or {}
                err = ((payload.get("error") or {}).get("type") or "").strip().upper()
                msg = str((payload.get("error") or {}).get("message") or "")
                desc = str((payload.get("error") or {}).get("description") or "")
            except Exception:
                err, msg, desc = "", "", ""
            texto_resp = str(resp.text or "")
            if (
                err == "RESOURCE_NOT_FOUND"
                or "RESOURCE_NOT_FOUND" in texto_resp
                or "NÃƒÂ£o encontrado" in msg
                or "NÃƒÂ£o encontrado" in desc
                or "Nao encontrado" in msg
                or "Nao encontrado" in desc
                or "nÃ£o foi encontrado" in texto_resp.lower()
                or "nao foi encontrado" in texto_resp.lower()
            ):
                return [], 200
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Erro ao listar lotes do produto {pid}: {resp.text}")
        data = resp.json().get("data", [])
        if not data:
            break
        lotes.extend(data)
    return lotes, 200

def _bling_listar_lancamentos_lote(access_token: str, lote_id: str) -> tuple[list[dict], int]:
    lid = str(lote_id or "").strip()
    if not lid:
        return [], 200
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://api.bling.com.br/Api/v3/produtos/lotes/{lid}/lancamentos"
    lancamentos: list[dict] = []
    for pagina in range(1, 1000):
        resp = BLING_SESSION.get(url, headers=headers, params={"pagina": pagina, "limite": 100}, timeout=20)
        if resp.status_code == 401:
            return [], 401
        if resp.status_code == 403:
            try:
                payload = resp.json() or {}
                err_type = str(((payload.get("error") or {}).get("type") or "")).strip().lower()
                if err_type == "insufficient_scope":
                    return [], 403
            except Exception:
                pass
        if resp.status_code in (400, 404):
            try:
                payload = resp.json() or {}
                err = ((payload.get("error") or {}).get("type") or "").strip().upper()
                msg = str((payload.get("error") or {}).get("message") or "")
                desc = str((payload.get("error") or {}).get("description") or "")
            except Exception:
                err, msg, desc = "", "", ""
            texto_resp = str(resp.text or "")
            if (
                err == "RESOURCE_NOT_FOUND"
                or "RESOURCE_NOT_FOUND" in texto_resp
                or "NÃƒÂ£o encontrado" in msg
                or "NÃƒÂ£o encontrado" in desc
                or "Nao encontrado" in msg
                or "Nao encontrado" in desc
                or "nÃ£o foi encontrado" in texto_resp.lower()
                or "nao foi encontrado" in texto_resp.lower()
            ):
                return [], 200
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Erro ao listar lanÃƒÂ§amentos do lote {lid}: {resp.text}")
        data = resp.json().get("data", [])
        if not data:
            break
        lancamentos.extend(data)
    return lancamentos, 200

def _extrair_entradas_saidas_lancamento(lanc: dict) -> tuple[float, float]:
    # Tenta campos explÃƒÂ­citos (quando disponÃƒÂ­veis no payload)
    entrada_campo = lanc.get("entrada")
    saida_campo = lanc.get("saida")
    if entrada_campo is not None or saida_campo is not None:
        entrada = abs(float(entrada_campo or 0))
        saida = abs(float(saida_campo or 0))
        return entrada, saida

    qtd_raw = float(lanc.get("quantidade") or 0)
    qtd_abs = abs(qtd_raw)

    tipo = str(lanc.get("tipoLancamento") or lanc.get("tipo") or "").strip().lower()
    if tipo in ("2", "saida", "saÃƒÂ­da", "venda"):
        return 0.0, qtd_abs
    if tipo in ("1", "entrada", "compra"):
        return qtd_abs, 0.0

    # Fallback por sinal da quantidade
    if qtd_raw < 0:
        return 0.0, qtd_abs
    return qtd_abs, 0.0

def _garantir_tabela_lancamentos_estoque(client_id: str) -> None:
    _garantir_tabela_historico_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_lancamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loja_sync TEXT NOT NULL,
                sku TEXT NOT NULL,
                id_bling TEXT,
                lote_id TEXT,
                data_ref TEXT NOT NULL,
                tipo_lancamento TEXT,
                quantidade REAL NOT NULL DEFAULT 0,
                entrada REAL NOT NULL DEFAULT 0,
                saida REAL NOT NULL DEFAULT 0,
                origem TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(loja_sync, sku, lote_id, data_ref, tipo_lancamento, quantidade, origem)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_estoque_lanc_data ON estoque_lancamentos (loja_sync, sku, data_ref)"
        )
        conn.commit()
    finally:
        conn.close()

def _resolver_id_bling_por_sku_snapshot(client_id: str, loja: str, sku: str, data_fim: str | None = None) -> str:
    db_hist = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_hist):
        return ""
    conn = sqlite3.connect(db_hist)
    try:
        cur = conn.cursor()
        sku_norm = _normalizar_sku_estoque(sku)
        if not sku_norm:
            return ""
        if data_fim:
            cur.execute(
                """
                SELECT id_bling
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                  AND upper(trim(sku)) = ?
                  AND date(data_ref) <= date(?)
                ORDER BY date(data_ref) DESC
                LIMIT 1
                """,
                (loja, sku_norm, data_fim),
            )
        else:
            cur.execute(
                """
                SELECT id_bling
                FROM estoque_historico
                WHERE lower(trim(loja_sync)) = lower(trim(?))
                  AND upper(trim(sku)) = ?
                ORDER BY date(data_ref) DESC
                LIMIT 1
                """,
                (loja, sku_norm),
            )
        row = cur.fetchone()
        return str(row[0] or "").strip() if row else ""
    finally:
        conn.close()

def _salvar_lancamentos_estoque(
    client_id: str,
    loja: str,
    sku: str,
    id_bling: str,
    lote_id: str,
    lancamentos: list[dict],
    data_inicio: str | None = None,
    data_fim: str | None = None,
) -> int:
    _garantir_tabela_lancamentos_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        agora = datetime.now().isoformat(timespec="seconds")
        total = 0
        for lanc in (lancamentos or []):
            data_ref = str((lanc or {}).get("data") or "")[:10]
            if not data_ref:
                continue
            if data_inicio and data_ref < data_inicio:
                continue
            if data_fim and data_ref > data_fim:
                continue
            tipo = str((lanc or {}).get("tipoLancamento") or (lanc or {}).get("tipo") or "").strip()
            qtd = float((lanc or {}).get("quantidade") or 0)
            entrada, saida = _extrair_entradas_saidas_lancamento(lanc or {})
            origem = str((lanc or {}).get("origem") or "").strip()
            cur.execute(
                """
                INSERT INTO estoque_lancamentos (
                    loja_sync, sku, id_bling, lote_id, data_ref,
                    tipo_lancamento, quantidade, entrada, saida,
                    origem, raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(loja_sync, sku, lote_id, data_ref, tipo_lancamento, quantidade, origem) DO UPDATE SET
                    entrada=excluded.entrada,
                    saida=excluded.saida,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(loja or "").strip(),
                    _normalizar_sku_estoque(sku),
                    str(id_bling or "").strip(),
                    str(lote_id or "").strip(),
                    data_ref,
                    tipo,
                    qtd,
                    float(entrada or 0),
                    float(saida or 0),
                    origem,
                    json.dumps(lanc or {}, ensure_ascii=False),
                    agora,
                ),
            )
            total += 1
        conn.commit()
        return total
    finally:
        conn.close()

def _agrupar_lancamentos_cache(
    client_id: str,
    loja: str,
    sku: str,
    data_inicio: str,
    data_fim: str,
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float], int]:
    _garantir_tabela_lancamentos_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT data_ref, SUM(coalesce(entrada, 0)) AS entradas, SUM(coalesce(saida, 0)) AS saidas
            FROM estoque_lancamentos
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND upper(trim(sku)) = ?
              AND date(data_ref) >= date(?)
              AND date(data_ref) <= date(?)
            GROUP BY data_ref
            ORDER BY data_ref
            """,
            (loja, _normalizar_sku_estoque(sku), data_inicio, data_fim),
        )
        entradas_map: dict[tuple[str, str], float] = {}
        saidas_map: dict[tuple[str, str], float] = {}
        total_rows = 0
        sku_norm = _normalizar_sku_estoque(sku)
        for data_ref, entradas, saidas in cur.fetchall():
            data_str = str(data_ref or "")
            entradas_map[(sku_norm, data_str)] = float(entradas or 0)
            saidas_map[(sku_norm, data_str)] = float(saidas or 0)
            total_rows += 1
        return entradas_map, saidas_map, total_rows
    finally:
        conn.close()

def _contar_lancamentos_cache_periodo(
    client_id: str,
    loja: str,
    data_inicio: str,
    data_fim: str,
) -> tuple[int, int]:
    _garantir_tabela_lancamentos_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS total_rows, COUNT(DISTINCT upper(trim(sku))) AS total_skus
            FROM estoque_lancamentos
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND date(data_ref) >= date(?)
              AND date(data_ref) <= date(?)
            """,
            (loja, data_inicio, data_fim),
        )
        row = cur.fetchone() or (0, 0)
        return int(row[0] or 0), int(row[1] or 0)
    finally:
        conn.close()

def _limpar_lancamentos_nf_periodo(client_id: str, loja: str, data_inicio: str, data_fim: str) -> int:
    _garantir_tabela_lancamentos_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM estoque_lancamentos
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND date(data_ref) >= date(?)
              AND date(data_ref) <= date(?)
              AND upper(coalesce(origem, '')) LIKE 'NF_%'
            """,
            (loja, data_inicio, data_fim),
        )
        removidos = int(cur.rowcount or 0)
        conn.commit()
        return removidos
    finally:
        conn.close()

def _salvar_movimentos_nf_estoque(client_id: str, loja: str, movimentos: list[dict]) -> int:
    _garantir_tabela_lancamentos_estoque(client_id)
    db_path = _estoque_historico_db_path(client_id)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        agora = datetime.now().isoformat(timespec="seconds")
        payload = []
        for mv in (movimentos or []):
            sku_norm = _normalizar_sku_estoque(mv.get("sku"))
            if not sku_norm:
                continue
            data_ref = str(mv.get("data_ref") or "")[:10]
            if not data_ref:
                continue
            qtd = abs(float(mv.get("quantidade") or 0))
            entrada = abs(float(mv.get("entrada") or 0))
            saida = abs(float(mv.get("saida") or 0))
            origem = str(mv.get("origem") or "").strip()
            if not origem:
                continue
            payload.append(
                (
                    str(loja or "").strip(),
                    sku_norm,
                    str(mv.get("id_bling") or "").strip(),
                    "NF",
                    data_ref,
                    str(mv.get("tipo_lancamento") or "").strip(),
                    qtd,
                    entrada,
                    saida,
                    origem,
                    json.dumps(mv.get("raw") or {}, ensure_ascii=False),
                    agora,
                )
            )

        if not payload:
            return 0

        cur.executemany(
            """
            INSERT INTO estoque_lancamentos (
                loja_sync, sku, id_bling, lote_id, data_ref,
                tipo_lancamento, quantidade, entrada, saida,
                origem, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(loja_sync, sku, lote_id, data_ref, tipo_lancamento, quantidade, origem) DO UPDATE SET
                entrada=excluded.entrada,
                saida=excluded.saida,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()

def _sincronizar_lancamentos_estoque_sku_api(
    client_id: str,
    loja_nome: str,
    sku: str,
    data_inicio: str,
    data_fim: str,
) -> dict:
    loja_cfg = buscar_loja(client_id, loja_nome)
    if not loja_cfg:
        raise HTTPException(status_code=404, detail="Loja não encontrada para o cliente.")
    bling_cfg = (loja_cfg.get("integracoes") or {}).get("bling") or {}
    access_token = bling_cfg.get("access_token")
    cid = bling_cfg.get("id")
    sec = bling_cfg.get("secret")
    refresh_tok = bling_cfg.get("refresh_token")
    if not (access_token and cid and sec):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para buscar lançamentos.")

    id_bling_sku = _resolver_id_bling_por_sku_snapshot(client_id, loja_nome, sku, data_fim)
    if not id_bling_sku:
        raise HTTPException(status_code=404, detail="ID do produto no Bling não encontrado para o SKU informado.")

    lotes, status_lotes = _bling_listar_lotes_produto(access_token, id_bling_sku)
    if status_lotes == 401 and refresh_tok:
        novos = _bling_refresh_token(cid, sec, refresh_tok)
        access_token = novos.get("access_token")
        refresh_tok = novos.get("refresh_token", refresh_tok)
        atualizar_api_loja(client_id, loja_nome, "bling", {
            "id": cid,
            "secret": sec,
            "access_token": access_token,
            "refresh_token": refresh_tok,
            "connected": True,
            "updated_at": str(time.time()),
        })
        lotes, status_lotes = _bling_listar_lotes_produto(access_token, id_bling_sku)

    if status_lotes == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
    if status_lotes == 403:
        raise HTTPException(
            status_code=403,
            detail="Permissão insuficiente (insufficient_scope) no token Bling para consultar lotes/lançamentos de estoque.",
        )

    total_salvos = 0
    total_lancamentos = 0
    for lote in (lotes or []):
        lote_id = lote.get("idLote") or lote.get("id")
        lancs, status_lancs = _bling_listar_lancamentos_lote(access_token, str(lote_id or ""))
        if status_lancs == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado ao consultar lançamentos.")
        if status_lancs == 403:
            raise HTTPException(
                status_code=403,
                detail="Permissão insuficiente (insufficient_scope) no token Bling para consultar lançamentos de lote.",
            )
        total_lancamentos += len(lancs or [])
        total_salvos += _salvar_lancamentos_estoque(
            client_id=client_id,
            loja=loja_nome,
            sku=sku,
            id_bling=id_bling_sku,
            lote_id=str(lote_id or ""),
            lancamentos=lancs or [],
            data_inicio=data_inicio,
            data_fim=data_fim,
        )

    return {
        "sku": _normalizar_sku_estoque(sku),
        "id_bling": id_bling_sku,
        "lotes": len(lotes or []),
        "sem_lote": len(lotes or []) == 0,
        "lancamentos_recebidos": total_lancamentos,
        "lancamentos_sincronizados": total_salvos,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
    }

async def sincronizar_lancamentos_estoque_api(
    req: EstoqueLancamentosSyncRequest,
    client_id: str = Depends(get_tenant_id),
):
    loja_nome = str(req.loja or "").strip()
    sku = _normalizar_sku_estoque(req.sku)
    if not loja_nome or loja_nome == "__todas":
        raise HTTPException(status_code=400, detail="Informe uma loja específica.")
    if not sku:
        raise HTTPException(status_code=400, detail="Informe um SKU para sincronizar lançamentos.")

    data_fim = req.data_fim or datetime.now().strftime("%Y-%m-%d")
    if req.data_inicio:
        data_inicio = req.data_inicio
    else:
        data_inicio = (datetime.fromisoformat(data_fim) - timedelta(days=365)).strftime("%Y-%m-%d")

    result = _sincronizar_lancamentos_estoque_sku_api(
        client_id=client_id,
        loja_nome=loja_nome,
        sku=sku,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return {"success": True, **result}

async def _sincronizar_lancamentos_estoque_lote_impl(
    req: EstoqueLancamentosSyncLoteRequest,
    client_id: str,
) -> dict:
    loja_nome = str(req.loja or "").strip()
    if not loja_nome or loja_nome == "__todas":
        raise HTTPException(status_code=400, detail="Informe uma loja específica.")

    data_fim = req.data_fim or datetime.now().strftime("%Y-%m-%d")
    try:
        data_fim_ref = datetime.fromisoformat(data_fim)
    except ValueError:
        raise HTTPException(status_code=400, detail="data_fim invalida. Use YYYY-MM-DD.")

    if req.data_inicio:
        data_inicio = req.data_inicio
        try:
            _ = datetime.fromisoformat(data_inicio)
        except ValueError:
            raise HTTPException(status_code=400, detail="data_inicio invalida. Use YYYY-MM-DD.")
    else:
        data_inicio = (data_fim_ref - timedelta(days=365)).strftime("%Y-%m-%d")

    loja_cfg = buscar_loja(client_id, loja_nome)
    if not loja_cfg:
        raise HTTPException(status_code=404, detail="Loja não encontrada para o cliente.")

    bling_cfg = (loja_cfg.get("integracoes") or {}).get("bling") or {}
    access_token = bling_cfg.get("access_token")
    cid = bling_cfg.get("id")
    sec = bling_cfg.get("secret")
    refresh_tok = bling_cfg.get("refresh_token")

    if not (access_token and cid and sec):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para buscar notas fiscais.")

    data_fim_str = data_fim_ref.strftime("%Y-%m-%d")
    _estoque_lanc_log(client_id, f"[ESTOQUE][LANC] Iniciando sincronização via NF da loja {loja_nome} ({data_inicio} a {data_fim_str})")
    _set_estoque_lanc_progresso(
        client_id,
        _criar_progresso("Notas Entrada", 0, 1, 15, "Buscando notas fiscais de entrada..."),
    )

    natureza_map, status_nat = _bling_listar_naturezas(access_token)
    if status_nat == 401 and refresh_tok:
        novos = _bling_refresh_token(cid, sec, refresh_tok)
        access_token = novos.get("access_token")
        refresh_tok = novos.get("refresh_token", refresh_tok)
        atualizar_api_loja(client_id, loja_nome, "bling", {
            "id": cid,
            "secret": sec,
            "access_token": access_token,
            "refresh_token": refresh_tok,
            "connected": True,
            "updated_at": str(time.time()),
        })
        natureza_map, status_nat = _bling_listar_naturezas(access_token)
    if status_nat == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")

    def _progresso_nf(etapa: str, lote: int, total_lotes: int, percentual: int, mensagem: str):
        _set_estoque_lanc_progresso(
            client_id,
            _criar_progresso(etapa, lote, total_lotes, percentual, mensagem),
        )

    def _log_nf(mensagem: str):
        _estoque_lanc_log(client_id, mensagem)

    notas_entrada, notas_entrada_itens, status_entrada = _bling_listar_notas_entrada(
        access_token,
        data_inicio,
        data_fim_str,
        natureza_map or {},
        client_id,
        progress_callback=_progresso_nf,
        log_callback=_log_nf,
        progress_start=15,
        progress_end=44,
    )
    if status_entrada == 401 and refresh_tok:
        novos = _bling_refresh_token(cid, sec, refresh_tok)
        access_token = novos.get("access_token")
        refresh_tok = novos.get("refresh_token", refresh_tok)
        atualizar_api_loja(client_id, loja_nome, "bling", {
            "id": cid,
            "secret": sec,
            "access_token": access_token,
            "refresh_token": refresh_tok,
            "connected": True,
            "updated_at": str(time.time()),
        })
        notas_entrada, notas_entrada_itens, status_entrada = _bling_listar_notas_entrada(
            access_token,
            data_inicio,
            data_fim_str,
            natureza_map or {},
            client_id,
            progress_callback=_progresso_nf,
            log_callback=_log_nf,
            progress_start=15,
            progress_end=44,
        )
    if status_entrada == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado ao buscar notas de entrada.")

    _estoque_lanc_log(
        client_id,
        f"[ESTOQUE][LANC] Notas de entrada: {len(notas_entrada or [])} notas, {len(notas_entrada_itens or [])} itens",
    )
    _set_estoque_lanc_progresso(
        client_id,
        _criar_progresso("Notas Saída", 0, 1, 45, "Buscando notas fiscais de saída..."),
    )

    notas_saida_itens, status_saida = _bling_listar_vendas_fallback_nf_saida(
        access_token,
        data_inicio,
        data_fim_str,
        loja_nome,
        client_id,
        progress_callback=_progresso_nf,
        log_callback=_log_nf,
        progress_start=45,
        progress_end=74,
    )
    if status_saida == 401 and refresh_tok:
        novos = _bling_refresh_token(cid, sec, refresh_tok)
        access_token = novos.get("access_token")
        refresh_tok = novos.get("refresh_token", refresh_tok)
        atualizar_api_loja(client_id, loja_nome, "bling", {
            "id": cid,
            "secret": sec,
            "access_token": access_token,
            "refresh_token": refresh_tok,
            "connected": True,
            "updated_at": str(time.time()),
        })
        notas_saida_itens, status_saida = _bling_listar_vendas_fallback_nf_saida(
            access_token,
            data_inicio,
            data_fim_str,
            loja_nome,
            client_id,
            progress_callback=_progresso_nf,
            log_callback=_log_nf,
            progress_start=45,
            progress_end=74,
        )
    if status_saida == 401:
        raise HTTPException(status_code=401, detail="Token Bling expirado ao buscar notas de saída.")

    _estoque_lanc_log(client_id, f"[ESTOQUE][LANC] Itens de saída via NF: {len(notas_saida_itens or [])}")
    _set_estoque_lanc_progresso(
        client_id,
        _criar_progresso("Persistindo", 0, 1, 75, "Gravando movimentos de entrada e saída por SKU..."),
    )

    movimentos_nf: list[dict] = []
    for idx, item in enumerate(notas_entrada_itens or []):
        sku = _normalizar_sku_estoque(item.get("sku"))
        if not sku:
            continue
        data_ref = str(item.get("data_emissao") or "")[:10]
        if not data_ref:
            continue
        quantidade = abs(float(item.get("quantidade") or 0))
        if quantidade <= 0:
            continue
        id_nota = str(item.get("id_nota") or "")
        movimentos_nf.append(
            {
                "sku": sku,
                "data_ref": data_ref,
                "id_bling": id_nota,
                "tipo_lancamento": "NF_ENTRADA",
                "quantidade": quantidade,
                "entrada": quantidade,
                "saida": 0.0,
                "origem": f"NF_ENTRADA:{id_nota}:{idx}",
                "raw": {
                    "fonte": "nota_entrada",
                    "numero_nota": item.get("numero_nota"),
                    "id_nota": id_nota,
                    "sku": sku,
                    "quantidade": quantidade,
                },
            }
        )

    for idx, item in enumerate(notas_saida_itens or []):
        sku = _normalizar_sku_estoque(item.get("sku"))
        if not sku:
            continue
        data_ref = str(item.get("data") or "")[:10]
        if not data_ref:
            continue
        quantidade = abs(float(item.get("quantidade") or 0))
        if quantidade <= 0:
            continue
        id_nota = str(item.get("nota_fiscal_id") or "")
        numero_doc = str(item.get("numero_nf") or item.get("numero") or "")
        movimentos_nf.append(
            {
                "sku": sku,
                "data_ref": data_ref,
                "id_bling": id_nota,
                "tipo_lancamento": "NF_SAIDA",
                "quantidade": quantidade,
                "entrada": 0.0,
                "saida": quantidade,
                "origem": f"NF_SAIDA:{id_nota}:{numero_doc}:{idx}",
                "raw": {
                    "fonte": "nota_saida",
                    "numero": numero_doc,
                    "nota_fiscal_id": id_nota,
                    "sku": sku,
                    "quantidade": quantidade,
                },
            }
        )

    removidos = _limpar_lancamentos_nf_periodo(client_id, loja_nome, data_inicio, data_fim_str)
    salvos = _salvar_movimentos_nf_estoque(client_id, loja_nome, movimentos_nf)

    entradas_total = sum(float(mv.get("entrada") or 0) for mv in movimentos_nf)
    saidas_total = sum(float(mv.get("saida") or 0) for mv in movimentos_nf)
    skus_movimentados = sorted({str(mv.get("sku") or "") for mv in movimentos_nf if str(mv.get("sku") or "")})

    resultado = {
        "success": True,
        "loja": loja_nome,
        "data_inicio": data_inicio,
        "data_fim": data_fim_str,
        "fonte": "notas_entrada_saida",
        "notas_entrada_total": len(notas_entrada or []),
        "itens_entrada_total": len(notas_entrada_itens or []),
        "itens_saida_total": len(notas_saida_itens or []),
        "movimentos_gerados": len(movimentos_nf),
        "movimentos_removidos_periodo": removidos,
        "lancamentos_sincronizados": salvos,
        "total_skus_movimentados": len(skus_movimentados),
        "entradas_total": entradas_total,
        "saidas_total": saidas_total,
        "detalhes_falhas": [],
        "skus_sem_lote": 0,
    }

    _estoque_lanc_log(
        client_id,
        f"[ESTOQUE][LANC] Concluído via NF: skus={len(skus_movimentados)} movimentos={len(movimentos_nf)} salvos={salvos} entradas={entradas_total:.2f} saídas={saidas_total:.2f}",
    )
    _set_estoque_lanc_progresso(
        client_id,
        _criar_progresso(
            "Concluído",
            len(skus_movimentados),
            len(skus_movimentados),
            100,
            f"Sincronização por notas concluída: {salvos} movimentos gravados.",
        ),
    )
    ESTOQUE_LANC_SYNC_META[client_id] = {
        **(ESTOQUE_LANC_SYNC_META.get(client_id) or {}),
        "last_result": resultado,
    }
    return resultado

def _sincronizar_lancamentos_estoque_lote_thread_worker(req: "EstoqueLancamentosSyncLoteRequest", client_id: str):
    ESTOQUE_LANC_SYNC_ACTIVE[client_id] = True
    ESTOQUE_LANC_SYNC_LOGS[client_id] = []
    try:
        _set_estoque_lanc_progresso(
            client_id,
            _criar_progresso("Preparando", 0, 0, 5, "Preparando sincronização de lançamentos em lote..."),
        )
        asyncio.run(_sincronizar_lancamentos_estoque_lote_impl(req, client_id))
    except HTTPException as e:
        msg = str(e.detail or "Erro na sincronização de lançamentos")
        _estoque_lanc_log(client_id, f"[ESTOQUE][LANC] Erro: {msg}")
        _set_estoque_lanc_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, msg[:140]))
    except Exception as e:
        msg = f"Erro inesperado: {str(e)}"
        logger.exception(f"[ESTOQUE][LANC] Erro: {msg}")
        _set_estoque_lanc_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, msg[:140]))
    finally:
        ESTOQUE_LANC_SYNC_ACTIVE.pop(client_id, None)

async def sincronizar_lancamentos_estoque_lote_api(
    req: EstoqueLancamentosSyncLoteRequest,
    client_id: str = Depends(get_tenant_id),
):
    loja_nome = str(req.loja or "").strip()
    if not loja_nome or loja_nome == "__todas":
        raise HTTPException(status_code=400, detail="Informe uma loja específica.")

    if ESTOQUE_LANC_SYNC_ACTIVE.get(client_id):
        return {"started": False, "already_running": True, "message": "Sincronização de lançamentos já em andamento."}

    ESTOQUE_LANC_SYNC_META[client_id] = {
        "loja": loja_nome,
        "started_at": datetime.now().isoformat(),
        "last_result": None,
    }

    t = threading.Thread(
        target=_sincronizar_lancamentos_estoque_lote_thread_worker,
        args=(req, client_id),
        daemon=True,
    )
    t.start()
    return {"started": True, "message": "Sincronização de lançamentos iniciada em background."}

async def progresso_sincronizacao_lancamentos_estoque(client_id: str = Depends(get_tenant_id)):
    active = bool(ESTOQUE_LANC_SYNC_ACTIVE.get(client_id))
    progress = ESTOQUE_LANC_SYNC_PROGRESS.get(client_id)
    if progress is None and active:
        progress = _criar_progresso("Preparando", 0, 0, 0, "Iniciando sincronização de lançamentos...")
    return {
        "success": True,
        "progress": progress,
        "logs": ESTOQUE_LANC_SYNC_LOGS.get(client_id, []),
        "active": active,
        "sync_meta": ESTOQUE_LANC_SYNC_META.get(client_id),
    }

async def estoque_serie_retroativa(
    loja: str,
    periodo: str = "3m",
    intervalo: str = "dia",
    fonte: str = "lancamentos",
    sku: str = None,
    data_inicio: str = None,
    data_fim: str = None,
    client_id: str = Depends(get_tenant_id),
):
    loja_nome = str(loja or "").strip()
    if not loja_nome or loja_nome == "__todas":
        raise HTTPException(status_code=400, detail="Informe uma loja específica.")

    intervalo = str(intervalo or "dia").strip().lower()
    if intervalo not in ("dia", "semana", "mes"):
        raise HTTPException(status_code=400, detail="intervalo invalido. Use dia, semana ou mes.")
    fonte = str(fonte or "lancamentos").strip().lower()
    if fonte != "lancamentos":
        raise HTTPException(status_code=400, detail="fonte invalida. Use apenas lancamentos.")

    if data_fim:
        try:
            data_fim_ref = datetime.fromisoformat(data_fim)
        except ValueError:
            raise HTTPException(status_code=400, detail="data_fim invalida. Use YYYY-MM-DD.")
    else:
        data_fim_ref = datetime.now()

    data_inicio_ref = _inicio_periodo_estoque(periodo, data_inicio, data_fim_ref, client_id, loja_nome)
    if data_inicio_ref > data_fim_ref:
        raise HTTPException(status_code=400, detail="data_inicio não pode ser maior que data_fim.")

    db_hist = _estoque_historico_db_path(client_id)
    if not os.path.exists(db_hist):
        return {
            "success": True,
            "labels": [],
            "saldo_retroativo": [],
            "entradas": [],
            "saidas": [],
            "base_snapshot_data": None,
            "total_skus": 0,
            "detail": "Histórico de estoque ainda não foi gerado para esta loja.",
        }

    conn_hist = sqlite3.connect(db_hist)
    try:
        cur_hist = conn_hist.cursor()
        cur_hist.execute(
            """
            SELECT MAX(date(data_ref))
            FROM estoque_historico
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND date(data_ref) <= date(?)
            """,
            (loja_nome, data_fim_ref.strftime("%Y-%m-%d")),
        )
        row = cur_hist.fetchone()
        base_snapshot_data = str(row[0]) if row and row[0] else ""
        if not base_snapshot_data:
            return {
                "success": True,
                "labels": [],
                "saldo_retroativo": [],
                "entradas": [],
                "saidas": [],
                "base_snapshot_data": None,
                "total_skus": 0,
                "detail": "Sem snapshot de estoque para a loja no período solicitado.",
            }

        sku_filtro_norm = _normalizar_sku_estoque(sku) if sku else ""
        params_snapshot = [loja_nome, base_snapshot_data]
        sql_snapshot = (
            """
            SELECT sku, saldo_loja
            FROM estoque_historico
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND date(data_ref) = date(?)
            """
        )
        if sku_filtro_norm:
            sql_snapshot += " AND upper(trim(sku)) = ?"
            params_snapshot.append(sku_filtro_norm)

        cur_hist.execute(sql_snapshot, params_snapshot)
        snapshot_rows = cur_hist.fetchall()
    finally:
        conn_hist.close()

    if not snapshot_rows:
        return {
            "success": True,
            "labels": [],
            "saldo_retroativo": [],
            "entradas": [],
            "saidas": [],
            "base_snapshot_data": base_snapshot_data,
            "total_skus": 0,
            "detail": "Nenhum SKU encontrado no snapshot base para os filtros informados.",
        }

    snapshot_por_sku: dict[str, dict] = {}
    for sku_raw, saldo_total_raw in snapshot_rows:
        sku_norm = _normalizar_sku_estoque(sku_raw)
        if not sku_norm:
            continue
        try:
            saldo_total = float(saldo_total_raw or 0)
        except Exception:
            saldo_total = 0.0
        snapshot_por_sku[sku_norm] = {
            "sku": str(sku_raw or "").strip(),
            "saldo_base": saldo_total,
        }

    if not snapshot_por_sku:
        return {
            "success": True,
            "labels": [],
            "saldo_retroativo": [],
            "entradas": [],
            "saidas": [],
            "base_snapshot_data": base_snapshot_data,
            "total_skus": 0,
            "detail": "Snapshot base sem SKUs validos para cÃƒÂ¡lculo.",
        }

    data_base_ref = datetime.fromisoformat(base_snapshot_data)
    if data_inicio_ref > data_base_ref:
        data_inicio_ref = data_base_ref

    data_inicio_str = data_inicio_ref.strftime("%Y-%m-%d")
    data_base_str = data_base_ref.strftime("%Y-%m-%d")

    skus_calc = list(snapshot_por_sku.keys())
    entradas_map: dict[tuple[str, str], float] = {}
    saidas_map: dict[tuple[str, str], float] = {}
    _garantir_tabela_lancamentos_estoque(client_id)
    db_lanc = _estoque_historico_db_path(client_id)
    conn_lanc = sqlite3.connect(db_lanc)
    try:
        cur_lanc = conn_lanc.cursor()
        placeholders_sku = ", ".join(["?"] * len(skus_calc))
        query_lanc = f"""
            SELECT upper(trim(sku)) AS sku_ref,
                   date(data_ref) AS data_ref,
                   SUM(coalesce(entrada, 0)) AS entradas,
                   SUM(coalesce(saida, 0)) AS saidas
            FROM estoque_lancamentos
            WHERE lower(trim(loja_sync)) = lower(trim(?))
              AND date(data_ref) >= date(?)
              AND date(data_ref) <= date(?)
              AND upper(trim(sku)) IN ({placeholders_sku})
            GROUP BY upper(trim(sku)), date(data_ref)
        """
        params_lanc = [loja_nome, data_inicio_str, data_base_str] + skus_calc
        cur_lanc.execute(query_lanc, params_lanc)
        for sku_ref_row, data_ref_row, entradas_row, saidas_row in cur_lanc.fetchall():
            chave = (_normalizar_sku_estoque(sku_ref_row), str(data_ref_row or ""))
            entradas_map[chave] = float(entradas_row or 0)
            saidas_map[chave] = float(saidas_row or 0)
    finally:
        conn_lanc.close()

    datas = []
    d_atual = data_inicio_ref.date()
    d_fim = data_base_ref.date()
    while d_atual <= d_fim:
        datas.append(d_atual)
        d_atual += timedelta(days=1)

    saldos_por_sku_dia: dict[str, dict[str, float]] = {}
    for sku_norm, meta in snapshot_por_sku.items():
        saldo_dia: dict[str, float] = {}
        saldo_atual = float(meta.get("saldo_base") or 0)
        data_cursor = data_base_ref.date()
        saldo_dia[data_cursor.strftime("%Y-%m-%d")] = saldo_atual

        while data_cursor > data_inicio_ref.date():
            dia_str = data_cursor.strftime("%Y-%m-%d")
            entradas_dia = float(entradas_map.get((sku_norm, dia_str), 0) or 0)
            saidas_dia = float(saidas_map.get((sku_norm, dia_str), 0) or 0)
            saldo_anterior = saldo_atual - entradas_dia + saidas_dia

            data_ant = data_cursor - timedelta(days=1)
            saldo_dia[data_ant.strftime("%Y-%m-%d")] = saldo_anterior

            saldo_atual = saldo_anterior
            data_cursor = data_ant

        saldos_por_sku_dia[sku_norm] = saldo_dia

    buckets: dict[str, dict] = {}
    for d in datas:
        d_dt = datetime.combine(d, datetime.min.time())
        d_str = d.strftime("%Y-%m-%d")
        chave, label = _chave_intervalo_estoque(d_dt, intervalo)

        if chave not in buckets:
            buckets[chave] = {
                "label": label,
                "saldo": 0.0,
                "entradas": 0.0,
                "saidas": 0.0,
            }

        saldo_total_dia = 0.0
        entradas_total_dia = 0.0
        saidas_total_dia = 0.0
        for sku_norm in skus_calc:
            saldo_total_dia += float((saldos_por_sku_dia.get(sku_norm) or {}).get(d_str, 0) or 0)
            entradas_total_dia += float(entradas_map.get((sku_norm, d_str), 0) or 0)
            saidas_total_dia += float(saidas_map.get((sku_norm, d_str), 0) or 0)

        # Saldo do bucket representa o saldo do ÃƒÂºltimo dia daquele intervalo.
        buckets[chave]["saldo"] = saldo_total_dia
        buckets[chave]["entradas"] += entradas_total_dia
        buckets[chave]["saidas"] += saidas_total_dia

    labels = []
    saldo_retroativo = []
    entradas = []
    saidas = []
    for chave in sorted(buckets.keys()):
        item = buckets[chave]
        labels.append(item["label"])
        saldo_retroativo.append(item["saldo"])
        entradas.append(item["entradas"])
        saidas.append(item["saidas"])

    por_sku = []
    if sku_filtro_norm and sku_filtro_norm in saldos_por_sku_dia:
        saldo_sku = saldos_por_sku_dia.get(sku_filtro_norm, {})
        for d in datas:
            d_str = d.strftime("%Y-%m-%d")
            por_sku.append({
                "data": d_str,
                "saldo": float(saldo_sku.get(d_str, 0) or 0),
                "entradas": float(entradas_map.get((sku_filtro_norm, d_str), 0) or 0),
                "saidas": float(saidas_map.get((sku_filtro_norm, d_str), 0) or 0),
            })

    return {
        "success": True,
        "loja": loja_nome,
        "sku": sku_filtro_norm or None,
        "periodo": periodo,
        "intervalo": intervalo,
        "fonte": fonte,
        "data_inicio": data_inicio_ref.strftime("%Y-%m-%d"),
        "data_fim": data_base_str,
        "base_snapshot_data": base_snapshot_data,
        "total_skus": len(skus_calc),
        "labels": labels,
        "saldo_retroativo": saldo_retroativo,
        "entradas": entradas,
        "saidas": saidas,
        "series_por_sku": por_sku,
    }


__all__ = [
    "configure_estoque_lancamentos_runtime",
    "_bling_listar_lotes_produto",
    "_bling_listar_lancamentos_lote",
    "_extrair_entradas_saidas_lancamento",
    "_garantir_tabela_lancamentos_estoque",
    "_resolver_id_bling_por_sku_snapshot",
    "_salvar_lancamentos_estoque",
    "_agrupar_lancamentos_cache",
    "_contar_lancamentos_cache_periodo",
    "_limpar_lancamentos_nf_periodo",
    "_salvar_movimentos_nf_estoque",
    "_sincronizar_lancamentos_estoque_sku_api",
    "sincronizar_lancamentos_estoque_api",
    "_sincronizar_lancamentos_estoque_lote_impl",
    "_sincronizar_lancamentos_estoque_lote_thread_worker",
    "sincronizar_lancamentos_estoque_lote_api",
    "progresso_sincronizacao_lancamentos_estoque",
    "estoque_serie_retroativa",
]
