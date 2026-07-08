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

async def listar_vendas(
    client_id: str = Depends(vendas_context.get_tenant_id),
    data_inicio: str = None,
    data_fim: str = None,
    sku: str = None,
    loja: str = None,
    unidade_negocio: str = None,
    resolver_nf: bool = False,
    incluir_ebazar: bool = False,
):
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return []

    erros_bancos: list[str] = []
    bancos_processados = 0

    # Caminho rapido para o frontend (resolver_nf=false): consolida todos os bancos do tenant.
    if not resolver_nf:
        dados = []
        mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)

        for alvo in db_paths:
            if not (alvo and os.path.exists(alvo)):
                continue

            conn = None
            try:
                conn = sqlite3.connect(alvo)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                # Migração leve para bases antigas
                cols = [row[1] for row in cur.execute("PRAGMA table_info(vendas)").fetchall()]
                if "devolucao" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN devolucao INTEGER DEFAULT 0")
                if "numero_nf" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN numero_nf TEXT")
                if "comprador" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN comprador TEXT")
                if "unidade_negocio" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN unidade_negocio TEXT")
                if "nota_fiscal_id" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN nota_fiscal_id TEXT")
                if "loja_id" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN loja_id TEXT")
                if "unidade_id" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN unidade_id TEXT")
                if "intermediador_nome" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_nome TEXT")
                if "intermediador_cnpj" not in cols:
                    cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_cnpj TEXT")
                    conn.commit()

                cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_data ON vendas(data)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_sku_data ON vendas(sku, data)")

                query = "SELECT id_unico, data, loja_conta, canal, numero, situacao, devolucao, sku, produto, quantidade, valor, numero_nf, comprador, unidade_negocio, nota_fiscal_id, loja_id, unidade_id, intermediador_nome, intermediador_cnpj FROM vendas"
                params = []
                conditions = ["coalesce(devolucao, 0) = 0"]

                if data_inicio and data_fim:
                    conditions.append("date(data) BETWEEN ? AND ?")
                    params.extend([data_inicio, data_fim])
                elif data_inicio:
                    conditions.append("date(data) >= ?")
                    params.append(data_inicio)
                elif data_fim:
                    conditions.append("date(data) <= ?")
                    params.append(data_fim)

                if sku:
                    conditions.append("sku = ?")
                    params.append(sku)

                if loja and loja != "__todas":
                    filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                    if filtro_loja_sql:
                        conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                        params.extend(filtro_loja_params)

                if unidade_negocio and unidade_negocio != "__todos":
                    filtro_unidade_sql, filtro_unidade_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_lojas_cliente)
                    if filtro_unidade_sql:
                        conditions.append(filtro_unidade_sql.replace(" AND ", "", 1))
                        params.extend(filtro_unidade_params)

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY date(data) DESC"
                rows = cur.execute(query, params).fetchall()

                for r in rows:
                    item = dict(r)
                    if (not incluir_ebazar) and _deve_excluir_venda_ebazar(item.get("devolucao"), item.get("comprador"), item.get("canal")):
                        continue
                    loja_virtual_resolvida = _resolver_nome_loja_virtual(
                        mapa_lojas_cliente,
                        loja_id=item.get("loja_id"),
                        unidade_id=item.get("unidade_id"),
                        nome_oficial=item.get("unidade_negocio"),
                        intermediador_nome=item.get("intermediador_nome"),
                        intermediador_cnpj=item.get("intermediador_cnpj"),
                        canal=item.get("canal"),
                    )
                    loja_virtual_resolvida = _normalizar_unidade_negocio_ml(
                        loja_virtual_resolvida or item.get("unidade_negocio"),
                        item.get("canal"),
                        prefer_full=False,
                    )
                    if loja_virtual_resolvida:
                        item["unidade_negocio"] = loja_virtual_resolvida
                    # Mantém id_unico até após a deduplicação para uso na chave de dedup
                    dados.append(item)

                bancos_processados += 1
            except Exception as e:
                logger.warning("[VENDAS] Falha ao ler banco de vendas %s: %s", alvo, e)
                erros_bancos.append(f"{alvo}: {str(e)}")
            finally:
                if conn:
                    conn.close()

        if not bancos_processados:
            erro_texto = "Nenhum banco de vendas válido para leitura do período atual."
            if erros_bancos:
                logger.warning("[VENDAS] Falha no carregamento sem resultados: %s", "; ".join(erros_bancos[:3]))
                erro_texto += f" Detalhes: {', '.join(erros_bancos[:3])}"
            logger.info("[VENDAS] Retornando lista vazia para evitar quebra na UI.")
            return []

        dados = _deduplicar_vendas_consolidadas(dados)
        # Remove id_unico após dedup (campo interno, não deve ser exposto ao frontend)
        for item in dados:
            item.pop("id_unico", None)
        dados.sort(key=lambda x: str(x.get("data") or ""), reverse=True)
        return dados

    # Fluxo legado para resolver NF em lote (mantido para compatibilidade)
    dados = []
    for alvo in db_paths:
        if not (alvo and os.path.exists(alvo)):
            continue

        conn = None
        try:
            conn = sqlite3.connect(alvo)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Migração leve para bases antigas
            cols = [row[1] for row in cur.execute("PRAGMA table_info(vendas)").fetchall()]
            if "devolucao" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN devolucao INTEGER DEFAULT 0")
            if "numero_nf" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN numero_nf TEXT")
            if "comprador" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN comprador TEXT")
            if "unidade_negocio" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN unidade_negocio TEXT")
            if "nota_fiscal_id" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN nota_fiscal_id TEXT")
            if "loja_id" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN loja_id TEXT")
            if "unidade_id" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN unidade_id TEXT")
            if "intermediador_nome" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_nome TEXT")
            if "intermediador_cnpj" not in cols:
                cur.execute("ALTER TABLE vendas ADD COLUMN intermediador_cnpj TEXT")
                conn.commit()

            # Índices para acelerar filtros por período e SKU.
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_data ON vendas(data)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vendas_sku_data ON vendas(sku, data)")

            query = "SELECT id_unico, data, loja_conta, canal, numero, situacao, devolucao, sku, produto, quantidade, valor, numero_nf, comprador, unidade_negocio, nota_fiscal_id, loja_id, unidade_id, intermediador_nome, intermediador_cnpj FROM vendas"
            params = []
            conditions = ["coalesce(devolucao, 0) = 0"]
            if data_inicio and data_fim:
                conditions.append("date(data) BETWEEN ? AND ?")
                params.extend([data_inicio, data_fim])
            elif data_inicio:
                conditions.append("date(data) >= ?")
                params.append(data_inicio)
            elif data_fim:
                conditions.append("date(data) <= ?")
                params.append(data_fim)

            if sku:
                conditions.append("sku = ?")
                params.append(sku)

            if loja and loja != "__todas":
                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                if filtro_loja_sql:
                    conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                    params.extend(filtro_loja_params)

            if unidade_negocio and unidade_negocio != "__todos":
                unidade_norm = (unidade_negocio or "").strip().lower()
                if unidade_norm == "__ml_loja_full":
                    conditions.append("lower(unidade_negocio) IN (?, ?, ?, ?)")
                    params.extend([
                        "mercado livre - loja",
                        "mercado livre - full",
                        "unidade jkpeças lta",
                        "filial fulfillment 1"
                    ])
                else:
                    conditions.append("lower(unidade_negocio) = ?")
                    params.append(unidade_norm)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY date(data) DESC"
            rows = cur.execute(query, params).fetchall()
            for r in rows:
                item = dict(r)
                if (not incluir_ebazar) and _deve_excluir_venda_ebazar(item.get("devolucao"), item.get("comprador"), item.get("canal")):
                    continue
                dados.append(item)

            atualizacoes_nf = []
            atualizacoes_loja = []
            tokens_loja = {}
            cache_nf = {}
            mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)

            max_resolucoes_nf = 2000 if sku else 1200
            resolucoes_nf = 0

            for item in dados:
                id_unico = item.get("id_unico")

                loja_virtual_resolvida = _resolver_nome_loja_virtual(
                    mapa_lojas_cliente,
                    loja_id=item.get("loja_id"),
                    unidade_id=item.get("unidade_id"),
                    nome_oficial=item.get("unidade_negocio"),
                    intermediador_nome=item.get("intermediador_nome"),
                    intermediador_cnpj=item.get("intermediador_cnpj"),
                    canal=item.get("canal"),
                )
                loja_virtual_resolvida = _normalizar_unidade_negocio_ml(
                    loja_virtual_resolvida or item.get("unidade_negocio"),
                    item.get("canal"),
                    prefer_full=False,
                )
                if loja_virtual_resolvida and loja_virtual_resolvida != (item.get("unidade_negocio") or ""):
                    item["unidade_negocio"] = loja_virtual_resolvida
                    if id_unico:
                        atualizacoes_loja.append((loja_virtual_resolvida, id_unico))

                precisa_nf = bool(
                    resolver_nf
                    and not item.get("numero_nf")
                    and item.get("nota_fiscal_id")
                    and resolucoes_nf < max_resolucoes_nf
                )
                if not precisa_nf:
                    continue

                loja_conta = item.get("loja_conta")
                nota_fiscal_id = str(item.get("nota_fiscal_id") or "").strip()
                cache_key = (loja_conta, nota_fiscal_id)
                if cache_key in cache_nf:
                    numero_nf = cache_nf[cache_key]
                else:
                    estado = tokens_loja.get(loja_conta)
                    if estado is None:
                        loja_cfg = buscar_loja(client_id, loja_conta) or {}
                        bling_cfg = (loja_cfg.get("integracoes") or {}).get("bling") or {}
                        estado = {
                            "access_token": bling_cfg.get("access_token"),
                            "refresh_token": bling_cfg.get("refresh_token"),
                            "id": bling_cfg.get("id"),
                            "secret": bling_cfg.get("secret"),
                        }
                        tokens_loja[loja_conta] = estado

                    numero_nf = ""
                    access_token = estado.get("access_token")
                    if access_token:
                        resolucoes_nf += 1
                        numero_nf, status_nf = _bling_obter_numero_nf(access_token, nota_fiscal_id)
                        if status_nf == 401 and estado.get("refresh_token") and estado.get("id") and estado.get("secret"):
                            novos = _bling_refresh_token(estado["id"], estado["secret"], estado["refresh_token"])
                            estado["access_token"] = novos.get("access_token")
                            estado["refresh_token"] = novos.get("refresh_token", estado.get("refresh_token"))
                            atualizar_api_loja(client_id, loja_conta, "bling", {
                                "id": estado["id"],
                                "secret": estado["secret"],
                                "access_token": estado["access_token"],
                                "refresh_token": estado["refresh_token"],
                                "connected": True,
                                "updated_at": str(time.time())
                            })
                            numero_nf, status_nf = _bling_obter_numero_nf(estado["access_token"], nota_fiscal_id)
                        if status_nf != 200:
                            numero_nf = ""
                    cache_nf[cache_key] = numero_nf

                if numero_nf:
                    item["numero_nf"] = numero_nf
                    if id_unico:
                        atualizacoes_nf.append((numero_nf, id_unico))

            if atualizacoes_loja:
                cur.executemany("UPDATE vendas SET unidade_negocio = ? WHERE id_unico = ?", atualizacoes_loja)
            if atualizacoes_nf:
                cur.executemany("UPDATE vendas SET numero_nf = ? WHERE id_unico = ?", atualizacoes_nf)
            if atualizacoes_loja or atualizacoes_nf:
                conn.commit()

            for item in dados:
                item.pop("id_unico", None)

            bancos_processados += 1
            break
        except Exception as e:
            logger.warning("[VENDAS] Falha ao resolver NF no banco de vendas %s: %s", alvo, e)
            erros_bancos.append(f"{alvo}: {str(e)}")
        finally:
            if conn:
                conn.close()

    if not bancos_processados:
        erro_texto = "Nenhum banco de vendas válido para leitura do período atual."
        if erros_bancos:
            logger.warning("[VENDAS] Falha ao resolver NF sem resultados: %s", "; ".join(erros_bancos[:3]))
            erro_texto += f" Detalhes: {', '.join(erros_bancos[:3])}"
        logger.info("[VENDAS] Retornando lista vazia para resolver_nf=true para evitar quebra na UI.")
        return []

    return dados

def _vendas_resumo_num(valor):
    try:
        if valor is None or valor == "":
            return 0.0
        return float(valor)
    except Exception:
        return 0.0


def _vendas_resumo_col(cols: set[str], nome: str, default: str = "''") -> str:
    if nome in cols:
        return nome
    return f"{default} AS {nome}"


def _vendas_resumo_nova_linha(sku: str, produto: str = "") -> dict:
    return {
        "sku": sku or "N/D",
        "produto": produto or "-",
        "pedidos": 0,
        "itens": 0.0,
        "devolucoes": 0,
        "itensDevolucao": 0.0,
        "valorDevolucao": 0.0,
        "valor": 0.0,
        "quantidade": 0.0,
        "loja_conta": "",
        "unidade_negocio": "",
        "__resumo": True,
    }


async def resumo_vendas(
    client_id: str = Depends(vendas_context.get_tenant_id),
    data_inicio: str = None,
    data_fim: str = None,
    sku: str = None,
    loja: str = None,
    unidade_negocio: str = None,
    incluir_ebazar: bool = False,
):
    inicio_tempo = time.time()
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return {
            "success": True,
            "rows": [],
            "totals": {
                "pedidos": 0,
                "itens": 0,
                "valor": 0,
                "devolucoes": 0,
                "itensDevolucao": 0,
                "valorDevolucao": 0,
                "itensLiquidos": 0,
                "valorLiquido": 0,
            },
            "meta": {
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "loja": loja or "__todas",
                "unidade_negocio": unidade_negocio or "__todos",
                "dias_com_dados": [],
                "unidades": [],
                "source": "summary",
                "duration_ms": 0,
            },
        }

    mapa_lojas_cliente = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
    unidade_sql = ""
    unidade_params: list = []
    if unidade_negocio and unidade_negocio != "__todos":
        unidade_sql, unidade_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_lojas_cliente)

    vendas_brutas: list[dict] = []
    dias_com_dados: set[str] = set()
    unidades: set[str] = set()
    bancos_processados = 0

    for alvo in db_paths:
        if not (alvo and os.path.exists(alvo)):
            continue
        conn = None
        try:
            conn = sqlite3.connect(alvo)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tabela_vendas = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vendas'"
            ).fetchone()
            if not tabela_vendas:
                continue

            cols = {row[1] for row in cur.execute("PRAGMA table_info(vendas)").fetchall()}
            select_cols = [
                _vendas_resumo_col(cols, "id_unico"),
                _vendas_resumo_col(cols, "data"),
                _vendas_resumo_col(cols, "loja_conta"),
                _vendas_resumo_col(cols, "canal"),
                _vendas_resumo_col(cols, "numero"),
                _vendas_resumo_col(cols, "devolucao", "0"),
                _vendas_resumo_col(cols, "sku"),
                _vendas_resumo_col(cols, "produto"),
                _vendas_resumo_col(cols, "quantidade", "0"),
                _vendas_resumo_col(cols, "valor", "0"),
                _vendas_resumo_col(cols, "comprador"),
                _vendas_resumo_col(cols, "unidade_negocio"),
                _vendas_resumo_col(cols, "loja_id"),
                _vendas_resumo_col(cols, "unidade_id"),
                _vendas_resumo_col(cols, "intermediador_nome"),
                _vendas_resumo_col(cols, "intermediador_cnpj"),
            ]
            query = "SELECT " + ", ".join(select_cols) + " FROM vendas"
            conditions = ["coalesce(devolucao, 0) = 0"]
            params: list = []

            if data_inicio and data_fim:
                conditions.append("date(data) BETWEEN ? AND ?")
                params.extend([data_inicio, data_fim])
            elif data_inicio:
                conditions.append("date(data) >= ?")
                params.append(data_inicio)
            elif data_fim:
                conditions.append("date(data) <= ?")
                params.append(data_fim)

            if sku:
                conditions.append("sku = ?")
                params.append(sku)

            if loja and loja != "__todas":
                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                if filtro_loja_sql:
                    conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                    params.extend(filtro_loja_params)

            if unidade_sql:
                conditions.append(unidade_sql.replace(" AND ", "", 1))
                params.extend(unidade_params)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            rows = cur.execute(query, params).fetchall()
            for row in rows:
                item = dict(row)
                if (not incluir_ebazar) and _deve_excluir_venda_ebazar(
                    item.get("devolucao"),
                    item.get("comprador"),
                    item.get("canal"),
                ):
                    continue

                unidade_resolvida = _resolver_nome_loja_virtual(
                    mapa_lojas_cliente,
                    loja_id=item.get("loja_id"),
                    unidade_id=item.get("unidade_id"),
                    nome_oficial=item.get("unidade_negocio"),
                    intermediador_nome=item.get("intermediador_nome"),
                    intermediador_cnpj=item.get("intermediador_cnpj"),
                    canal=item.get("canal"),
                )
                unidade_resolvida = _normalizar_unidade_negocio_ml(
                    unidade_resolvida or item.get("unidade_negocio"),
                    item.get("canal"),
                    prefer_full=False,
                )
                if unidade_resolvida:
                    item["unidade_negocio"] = unidade_resolvida
                    unidades.add(str(unidade_resolvida))

                data_item = str(item.get("data") or "")[:10]
                if data_item:
                    dias_com_dados.add(data_item)
                vendas_brutas.append(item)

            bancos_processados += 1
        except Exception as e:
            logger.warning("[VENDAS] Falha ao gerar resumo no banco %s: %s", alvo, e)
        finally:
            if conn:
                conn.close()

    vendas_deduplicadas = _deduplicar_vendas_consolidadas(vendas_brutas)
    resumo_por_sku: dict[str, dict] = {}
    totals = {
        "pedidos": 0,
        "itens": 0.0,
        "valor": 0.0,
        "devolucoes": 0,
        "itensDevolucao": 0.0,
        "valorDevolucao": 0.0,
    }

    for item in vendas_deduplicadas:
        chave_sku = str(item.get("sku") or "N/D").strip() or "N/D"
        produto = str(item.get("produto") or "").strip() or "-"
        linha = resumo_por_sku.get(chave_sku)
        if linha is None:
            linha = _vendas_resumo_nova_linha(chave_sku, produto)
            resumo_por_sku[chave_sku] = linha
        if not linha.get("produto") or linha.get("produto") == "-":
            linha["produto"] = produto
        qtd = _vendas_resumo_num(item.get("quantidade"))
        valor = _vendas_resumo_num(item.get("valor"))
        linha["pedidos"] += 1
        linha["itens"] += qtd
        linha["quantidade"] += qtd
        linha["valor"] += valor
        if not linha.get("loja_conta"):
            linha["loja_conta"] = str(item.get("loja_conta") or "")
        if not linha.get("unidade_negocio"):
            linha["unidade_negocio"] = str(item.get("unidade_negocio") or "")
        totals["pedidos"] += 1
        totals["itens"] += qtd
        totals["valor"] += valor

    devolucoes_vistas: set[str] = set()
    for alvo in db_paths:
        if not (alvo and os.path.exists(alvo)):
            continue
        conn = None
        try:
            conn = sqlite3.connect(alvo)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            tabela_itens = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='notas_entrada_itens'"
            ).fetchone()
            if not tabela_itens:
                continue

            cols_itens = {row[1] for row in cur.execute("PRAGMA table_info(notas_entrada_itens)").fetchall()}
            select_itens = [
                _vendas_resumo_col(cols_itens, "id_unico"),
                _vendas_resumo_col(cols_itens, "data_emissao"),
                _vendas_resumo_col(cols_itens, "sku"),
                _vendas_resumo_col(cols_itens, "descricao"),
                _vendas_resumo_col(cols_itens, "quantidade", "0"),
                _vendas_resumo_col(cols_itens, "valor_total", "0"),
                _vendas_resumo_col(cols_itens, "loja_conta"),
                _vendas_resumo_col(cols_itens, "unidade_negocio"),
                _vendas_resumo_col(cols_itens, "unidade_negocio_virtual"),
                _vendas_resumo_col(cols_itens, "natureza_operacao"),
            ]
            query = (
                "SELECT "
                + ", ".join(select_itens)
                + " FROM notas_entrada_itens WHERE devolucao = 1 AND sku IS NOT NULL AND sku != ''"
            )
            params: list = []
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

            for row in cur.execute(query, params).fetchall():
                dev = dict(row)
                chave_dev = str(dev.get("id_unico") or "").strip()
                if chave_dev:
                    if chave_dev in devolucoes_vistas:
                        continue
                    devolucoes_vistas.add(chave_dev)
                data_dev = str(dev.get("data_emissao") or "")[:10]
                if data_dev:
                    dias_com_dados.add(data_dev)
                unidade_dev = _classificar_unidade_virtual_devolucao(
                    dev.get("unidade_negocio_virtual"),
                    dev.get("natureza_operacao"),
                    dev.get("loja_conta"),
                )
                if unidade_dev:
                    unidades.add(str(unidade_dev))
                chave_sku = str(dev.get("sku") or "N/D").strip() or "N/D"
                linha = resumo_por_sku.get(chave_sku)
                if linha is None:
                    linha = _vendas_resumo_nova_linha(chave_sku, str(dev.get("descricao") or ""))
                    resumo_por_sku[chave_sku] = linha
                if not linha.get("produto") or linha.get("produto") == "-":
                    linha["produto"] = str(dev.get("descricao") or "-")
                qtd_dev = _vendas_resumo_num(dev.get("quantidade"))
                valor_dev = _vendas_resumo_num(dev.get("valor_total"))
                linha["devolucoes"] += 1
                linha["itensDevolucao"] += qtd_dev
                linha["valorDevolucao"] += valor_dev
                totals["devolucoes"] += 1
                totals["itensDevolucao"] += qtd_dev
                totals["valorDevolucao"] += valor_dev
        except Exception as e:
            logger.warning("[VENDAS] Falha ao somar devolucoes no resumo %s: %s", alvo, e)
        finally:
            if conn:
                conn.close()

    totals["itensLiquidos"] = totals["itens"] - totals["itensDevolucao"]
    totals["valorLiquido"] = totals["valor"] - totals["valorDevolucao"]

    rows = list(resumo_por_sku.values())
    for item in rows:
        for campo in ("itens", "quantidade", "valor", "itensDevolucao", "valorDevolucao"):
            item[campo] = round(_vendas_resumo_num(item.get(campo)), 2)
    rows.sort(
        key=lambda item: (
            _vendas_resumo_num(item.get("valor")) or _vendas_resumo_num(item.get("valorDevolucao")),
            _vendas_resumo_num(item.get("itens")) or _vendas_resumo_num(item.get("itensDevolucao")),
        ),
        reverse=True,
    )

    return {
        "success": True,
        "rows": rows,
        "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in totals.items()},
        "meta": {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "loja": loja or "__todas",
            "unidade_negocio": unidade_negocio or "__todos",
            "dias_com_dados": sorted(dias_com_dados),
            "unidades": sorted(unidades),
            "bancos_processados": bancos_processados,
            "registros_vendas": len(vendas_deduplicadas),
            "registros_devolucoes": len(devolucoes_vistas),
            "source": "summary",
            "duration_ms": int((time.time() - inicio_tempo) * 1000),
        },
    }

async def limpar_todos_bancos_vendas(client_id: str = Depends(vendas_context.get_tenant_id)):
    """Apaga todos os bancos de vendas do tenant (legado + segregados por loja)."""
    tenant_path = get_tenant_path(client_id)
    if not os.path.exists(tenant_path):
        return {"success": True, "arquivos_removidos": []}

    removidos = []
    for nome in os.listdir(tenant_path):
        if not (nome.startswith("vendas_historico") and nome.endswith(".db")):
            continue
        base = os.path.join(tenant_path, nome)
        candidatos = [base, f"{base}-wal", f"{base}-shm"]
        for arq in candidatos:
            if not os.path.exists(arq):
                continue
            try:
                os.remove(arq)
                removidos.append(os.path.basename(arq))
            except Exception:
                logger.exception(f"Falha ao remover arquivo de vendas: {arq}")

    return {
        "success": True,
        "arquivos_removidos": sorted(removidos),
        "total_removidos": len(removidos)
    }

async def listar_vendas_todas(client_id: str = Depends(vendas_context.get_tenant_id)):
    db_path = _get_vendas_db(client_id)
    if not os.path.exists(db_path):
        return {"success": False, "data": []}
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM vendas ORDER BY data DESC")
        rows = cur.fetchall()
        vendas = [dict(row) for row in rows]
        return {"success": True, "data": vendas}
    finally:
        conn.close()

async def limites_vendas(
    loja: str = None,
    unidade_negocio: str = None,
    client_id: str = Depends(vendas_context.get_tenant_id)
):
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return {"inicio": None, "fim": None}

    menor_data = None
    maior_data = None

    for db_path in db_paths:
        if not os.path.exists(db_path):
            continue
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            query = "SELECT MIN(date(data)), MAX(date(data)) FROM vendas WHERE 1=1"
            params = []

            if loja and loja != "__todas":
                filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                if filtro_loja_sql:
                    query += filtro_loja_sql
                    params.extend(filtro_loja_params)
            if unidade_negocio and unidade_negocio != "__todos":
                mapa_lojas_limites = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
                filtro_unidade_sql, filtro_unidade_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_lojas_limites)
                if filtro_unidade_sql:
                    query += filtro_unidade_sql
                    params.extend(filtro_unidade_params)

            cur.execute(query, params)
            row = cur.fetchone() or (None, None)
            inicio_db, fim_db = row[0], row[1]

            if inicio_db:
                if menor_data is None or str(inicio_db) < menor_data:
                    menor_data = str(inicio_db)
            if fim_db:
                if maior_data is None or str(fim_db) > maior_data:
                    maior_data = str(fim_db)
        finally:
            conn.close()

    return {
        "inicio": menor_data,
        "fim": maior_data,
    }



def configure_vendas_listagem_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_vendas_listagem_runtime()

__all__ = [
    "configure_vendas_listagem_runtime",
    "resumo_vendas",
    "listar_vendas",
    "limpar_todos_bancos_vendas",
    "listar_vendas_todas",
    "limites_vendas",
]
