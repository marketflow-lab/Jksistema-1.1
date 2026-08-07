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
from backend.services.sqlite_coordination import (
    configure_sqlite_connection,
    sqlite_lock_for_path,
    sqlite_locks_for_paths,
)

from .dependencies import get_tenant_path, logger
from .errors import VendasDomainError as HTTPException
from .legacy import (
    _bling_obter_numero_nf,
    _bling_renovar_token_loja,
    _carregar_mapeamento_lojas_virtuais_cliente,
    _classificar_unidade_virtual_devolucao,
    _deduplicar_vendas_consolidadas,
    _deve_excluir_venda_ebazar,
    _listar_bancos_vendas_tenant,
    _normalizar_unidade_negocio_ml,
    _resolver_nome_loja_virtual,
    _sql_filtro_loja_notas_entrada,
    _sql_filtro_loja_vendas,
    _sql_filtro_unidade_com_mapa,
    _sql_filtro_unidade_devolucao,
    buscar_loja,
)
from .performance import (
    append_indexable_date_filter,
    cache_vendas_response,
    cached_table_columns,
    invalidate_vendas_cache,
    open_vendas_readonly,
    select_compatible_column,
    vendas_source_paths,
)

def listar_vendas(
    client_id: str,
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
                conn = open_vendas_readonly(alvo, row_factory=sqlite3.Row)
                cur = conn.cursor()
                cols = cached_table_columns(alvo, "vendas", conn)
                select_specs = (
                    ("id_unico", "''"), ("data", "''"), ("loja_conta", "''"),
                    ("canal", "''"), ("numero", "''"), ("situacao", "''"),
                    ("devolucao", "0"), ("sku", "''"), ("produto", "''"),
                    ("quantidade", "0"), ("valor", "0"), ("numero_nf", "''"),
                    ("comprador", "''"), ("unidade_negocio", "''"),
                    ("nota_fiscal_id", "''"), ("loja_id", "''"), ("unidade_id", "''"),
                    ("intermediador_nome", "''"), ("intermediador_cnpj", "''"),
                )
                query = "SELECT " + ", ".join(
                    select_compatible_column(cols, name, default) for name, default in select_specs
                ) + " FROM vendas"
                params = []
                conditions = ["coalesce(devolucao, 0) = 0"] if "devolucao" in cols else []
                append_indexable_date_filter(conditions, params, "data", data_inicio, data_fim)

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
            conn = open_vendas_readonly(alvo, row_factory=sqlite3.Row)
            cur = conn.cursor()
            cols = cached_table_columns(alvo, "vendas", conn)
            select_specs = (
                ("id_unico", "''"), ("data", "''"), ("loja_conta", "''"),
                ("canal", "''"), ("numero", "''"), ("situacao", "''"),
                ("devolucao", "0"), ("sku", "''"), ("produto", "''"),
                ("quantidade", "0"), ("valor", "0"), ("numero_nf", "''"),
                ("comprador", "''"), ("unidade_negocio", "''"),
                ("nota_fiscal_id", "''"), ("loja_id", "''"), ("unidade_id", "''"),
                ("intermediador_nome", "''"), ("intermediador_cnpj", "''"),
            )
            query = "SELECT " + ", ".join(
                select_compatible_column(cols, name, default) for name, default in select_specs
            ) + " FROM vendas"
            params = []
            conditions = ["coalesce(devolucao, 0) = 0"] if "devolucao" in cols else []
            append_indexable_date_filter(conditions, params, "data", data_inicio, data_fim)

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
                            renovado = _bling_renovar_token_loja(client_id, loja_conta, estado)
                            estado.update({
                                "access_token": renovado.get("access_token"),
                                "refresh_token": renovado.get("refresh_token"),
                                "id": renovado.get("id") or estado.get("id"),
                                "secret": renovado.get("secret") or estado.get("secret"),
                            })
                            numero_nf, status_nf = _bling_obter_numero_nf(estado["access_token"], nota_fiscal_id)
                        if status_nf != 200:
                            numero_nf = ""
                    cache_nf[cache_key] = numero_nf

                if numero_nf:
                    item["numero_nf"] = numero_nf
                    if id_unico:
                        atualizacoes_nf.append((numero_nf, id_unico))

            if atualizacoes_loja or atualizacoes_nf:
                conn.close()
                conn = None
                with sqlite_lock_for_path(alvo):
                    write_conn = sqlite3.connect(alvo, timeout=15)
                    try:
                        configure_sqlite_connection(write_conn)
                        write_conn.execute("BEGIN IMMEDIATE")
                        write_cur = write_conn.cursor()
                        if atualizacoes_loja:
                            write_cur.executemany(
                                "UPDATE vendas SET unidade_negocio = ? WHERE id_unico = ?",
                                atualizacoes_loja,
                            )
                        if atualizacoes_nf:
                            write_cur.executemany(
                                "UPDATE vendas SET numero_nf = ? WHERE id_unico = ?",
                                atualizacoes_nf,
                            )
                        write_conn.commit()
                    except BaseException:
                        if write_conn.in_transaction:
                            write_conn.rollback()
                        raise
                    finally:
                        write_conn.close()
                invalidate_vendas_cache(client_id)

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
    return select_compatible_column(cols, nome, default)


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


def _vendas_resumo_cache_paths(arguments: dict) -> list[str]:
    client_id = arguments["client_id"]
    db_paths = _listar_bancos_vendas_tenant(client_id, arguments.get("loja"))
    return vendas_source_paths(client_id, db_paths)


@cache_vendas_response("resumo", _vendas_resumo_cache_paths)
def resumo_vendas(
    client_id: str,
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
            conn = open_vendas_readonly(alvo, row_factory=sqlite3.Row)
            cur = conn.cursor()
            tabela_vendas = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vendas'"
            ).fetchone()
            if not tabela_vendas:
                continue

            cols = cached_table_columns(alvo, "vendas", conn)
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
            conditions = ["coalesce(devolucao, 0) = 0"] if "devolucao" in cols else []
            params: list = []
            append_indexable_date_filter(conditions, params, "data", data_inicio, data_fim)

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

            # Preserve the historical first-record choice used for product/unit
            # labels while still allowing the date index to filter candidates.
            query += " ORDER BY rowid"

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
            conn = open_vendas_readonly(alvo, row_factory=sqlite3.Row)
            cur = conn.cursor()
            tabela_itens = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='notas_entrada_itens'"
            ).fetchone()
            if not tabela_itens:
                continue

            cols_itens = cached_table_columns(alvo, "notas_entrada_itens", conn)
            if "devolucao" not in cols_itens:
                continue
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

def limpar_todos_bancos_vendas(client_id: str):
    """Apaga todos os bancos de vendas do tenant (legado + segregados por loja)."""
    tenant_path = get_tenant_path(client_id)
    if not os.path.exists(tenant_path):
        return {"success": True, "arquivos_removidos": []}

    removidos = []
    bases = [
        os.path.join(tenant_path, nome)
        for nome in os.listdir(tenant_path)
        if nome.startswith("vendas_historico") and nome.endswith(".db")
    ]
    with sqlite_locks_for_paths(bases):
        for base in bases:
            candidatos = [base, f"{base}-wal", f"{base}-shm"]
            for arq in candidatos:
                if not os.path.exists(arq):
                    continue
                try:
                    os.remove(arq)
                    removidos.append(os.path.basename(arq))
                except Exception:
                    logger.exception(f"Falha ao remover arquivo de vendas: {arq}")

    invalidate_vendas_cache(client_id)
    return {
        "success": True,
        "arquivos_removidos": sorted(removidos),
        "total_removidos": len(removidos)
    }

def listar_vendas_todas(client_id: str):
    db_path = os.path.join(get_tenant_path(client_id), "vendas_historico.db")
    if not os.path.exists(db_path):
        return {"success": False, "data": []}

    conn = open_vendas_readonly(db_path, row_factory=sqlite3.Row)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM vendas ORDER BY data DESC")
        rows = cur.fetchall()
        vendas = [dict(row) for row in rows]
        return {"success": True, "data": vendas}
    finally:
        conn.close()

def limites_vendas(
    loja: str = None,
    unidade_negocio: str = None,
    client_id: str = ""
):
    db_paths = _listar_bancos_vendas_tenant(client_id, loja)
    if not db_paths:
        return {"inicio": None, "fim": None}

    menor_data = None
    maior_data = None

    for db_path in db_paths:
        if not os.path.exists(db_path):
            continue
        conn = open_vendas_readonly(db_path)
        try:
            cur = conn.cursor()
            query = "SELECT MIN(data), MAX(data) FROM vendas WHERE 1=1"
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
            inicio_db = str(row[0])[:10] if row[0] else None
            fim_db = str(row[1])[:10] if row[1] else None

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



class VendasRepository:
    """Database/CSV boundary used by the Vendas application service."""

    def listar(self, **kwargs): return listar_vendas(**kwargs)
    def resumo(self, **kwargs): return resumo_vendas(**kwargs)
    def limpar(self, client_id: str): return limpar_todos_bancos_vendas(client_id)
    def listar_todas(self, client_id: str): return listar_vendas_todas(client_id)
    def limites(self, **kwargs): return limites_vendas(**kwargs)

    def relatorio_pareto(self, **kwargs):
        from .reports import generate_pareto_report
        return generate_pareto_report(**kwargs)

    def exportar_relatorio_pareto(self, **kwargs):
        from .reports import export_pareto_report
        return export_pareto_report(**kwargs)

    def relatorio_geral_skus(self, **kwargs):
        from .general_report import generate_general_sku_report
        return generate_general_sku_report(**kwargs)

    def exportar_relatorio_geral_skus(self, **kwargs):
        from .general_report import export_general_sku_report
        return export_general_sku_report(**kwargs)

    def grafico(self, **kwargs):
        from .analytics import grafico_vendas
        return grafico_vendas(**kwargs)

    def skus_sem_venda(self, **kwargs):
        from .analytics import skus_sem_venda
        return skus_sem_venda(**kwargs)

    def listar_notas(self, **kwargs):
        from .notes import listar_notas_entrada
        return listar_notas_entrada(**kwargs)

    def listar_devolucoes(self, **kwargs):
        from .notes import listar_itens_devolucoes
        return listar_itens_devolucoes(**kwargs)

    def listar_notas_por_sku(self, **kwargs):
        from .notes import listar_notas_entrada_por_sku
        return listar_notas_entrada_por_sku(**kwargs)

    def listar_unidades(self, **kwargs):
        from .units import listar_unidades_negocios
        return listar_unidades_negocios(**kwargs)

    def atualizar_unidade(self, **kwargs):
        from .units import atualizar_unidade_negocio
        return atualizar_unidade_negocio(**kwargs)

    def salvar_mapeamento(self, **kwargs):
        from .units import salvar_mapeamento_unidades
        return salvar_mapeamento_unidades(**kwargs)


__all__ = [
    "VendasRepository",
    "resumo_vendas",
    "listar_vendas",
    "limpar_todos_bancos_vendas",
    "listar_vendas_todas",
    "limites_vendas",
]
