"""Common imports and runtime glue for Vendas endpoint modules."""

from __future__ import annotations

import asyncio
import calendar
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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

import pandas as pd

from backend.services.cadastro_compatibilidade import resolver_loja_ativa_para_leitura

from .compositions import load_composition_catalog
from .dependencies import get_tenant_path, get_vendas_dependencies, logger
from .errors import VendasDomainError as HTTPException
from .legacy_duplicates import without_legacy_sale_duplicates
from .legacy import (
    _carregar_mapeamento_lojas_virtuais_cliente,
    _deve_excluir_venda_ebazar,
    _listar_bancos_vendas_tenant,
    _migrar_arquivo_legado_para_tenant,
    _normalizar_sku_estoque,
    _sql_filtro_loja_notas_entrada,
    _sql_filtro_loja_vendas,
    _sql_filtro_unidade_com_mapa,
    _sql_filtro_unidade_devolucao,
    _vendas_series_estoque_historico,
)
from .performance import (
    append_indexable_date_filter,
    cache_vendas_response,
    cached_file_value,
    cached_table_columns,
    legacy_store_conflicts,
    mark_vendas_read_incomplete,
    select_compatible_column,
    open_vendas_readonly,
    vendas_source_paths,
    vendas_file_signature,
)

def _grafico_cache_paths(arguments: dict) -> list[str]:
    client_id = arguments["client_id"]
    db_paths = _listar_bancos_vendas_tenant(client_id, arguments.get("loja"))
    return vendas_source_paths(client_id, db_paths, include_stock=True, include_compositions=True)


def _calcular_skus_pareto_80(rows: list[tuple]) -> tuple[list[str], dict[str, Any]]:
    def texto_regra(value: Any) -> str:
        texto = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(char for char in texto if not unicodedata.combining(char)).strip().upper()

    chaves_faturadas = {
        (
            texto_regra(row[6] if len(row) > 6 else ""),
            str(row[12] if len(row) > 12 and row[12] is not None else "").strip(),
            str(row[3] if len(row) > 3 and row[3] is not None else "").strip().upper(),
        )
        for row in rows
        if len(row) > 12
        and str(row[12] or "").strip()
        and "FATURAD" in texto_regra(row[11] if len(row) > 11 else "")
    }
    faturamento_por_sku: dict[str, Decimal] = {}
    for row in rows:
        sku_norm = str(row[3] if len(row) > 3 and row[3] is not None else "").strip().upper()
        situacao_norm = texto_regra(row[11] if len(row) > 11 else "")
        nota_fiscal_id = str(row[12] if len(row) > 12 and row[12] is not None else "").strip()
        chave_fatura = (
            texto_regra(row[6] if len(row) > 6 else ""),
            nota_fiscal_id,
            sku_norm,
        )
        if (
            not sku_norm
            or texto_regra(sku_norm) == "ESTORNO DE CREDITO ICMS"
            or "CANCEL" in situacao_norm
            or (nota_fiscal_id and chave_fatura in chaves_faturadas and "FATURAD" not in situacao_norm)
        ):
            continue
        try:
            valor = Decimal(str(row[5] if len(row) > 5 and row[5] is not None else 0))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not valor.is_finite():
            continue
        faturamento_por_sku[sku_norm] = faturamento_por_sku.get(sku_norm, Decimal("0")) + valor

    ordenados = sorted(
        faturamento_por_sku.items(),
        key=lambda item: (-item[1], item[0]),
    )
    faturamento_total = sum((valor for _sku, valor in ordenados), Decimal("0"))
    if faturamento_total <= 0:
        return [], {
            "pareto_skus_total": 0,
            "pareto_participacao": 0.0,
            "pareto_sku_corte": None,
        }

    alvo = faturamento_total * Decimal("0.80")
    acumulado = Decimal("0")
    skus_pareto: list[str] = []
    for sku_item, valor in ordenados:
        skus_pareto.append(sku_item)
        acumulado += valor
        if acumulado >= alvo:
            break

    return skus_pareto, {
        "pareto_skus_total": len(skus_pareto),
        "pareto_participacao": float(acumulado / faturamento_total),
        "pareto_sku_corte": skus_pareto[-1] if skus_pareto else None,
    }


def _filtrar_vendas_lojas_ativas(rows: list[tuple], client_id: str, loja: str | None) -> list[tuple]:
    if loja and loja != "__todas":
        return rows
    try:
        caminho = os.path.join(get_tenant_path(client_id), "lojas_config.json")
        with open(caminho, "r", encoding="utf-8-sig") as arquivo:
            payload = json.load(arquivo)
    except FileNotFoundError:
        return rows
    except (OSError, ValueError, TypeError, RuntimeError):
        mark_vendas_read_incomplete()
        return rows
    if not isinstance(payload, list):
        mark_vendas_read_incomplete()
        return rows

    def chave_loja(value: Any) -> str:
        texto = unicodedata.normalize("NFKD", str(value or ""))
        return "".join(char for char in texto if not unicodedata.combining(char)).strip().casefold()

    lojas_ativas = {
        chave_loja(item.get("nome"))
        for item in payload
        if isinstance(item, dict) and chave_loja(item.get("nome"))
    }
    if not lojas_ativas:
        return rows
    return [row for row in rows if len(row) > 6 and chave_loja(row[6]) in lojas_ativas]


def _calcular_previsao_mes_atual(rows: list[tuple], hoje: datetime) -> dict[str, Any]:
    mes_atual = hoje.strftime("%Y-%m")
    data_referencia = hoje.strftime("%Y-%m-%d")
    valor_realizado = Decimal("0")
    for row in rows:
        data_venda = str(row[1] or "")[:10] if len(row) > 1 else ""
        if len(row) <= 5 or data_venda[:7] != mes_atual or data_venda > data_referencia:
            continue
        try:
            valor = Decimal(str(row[5] if row[5] is not None else 0))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if valor.is_finite():
            valor_realizado += valor

    dias_decorridos = hoje.day
    dias_no_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    ritmo_diario = valor_realizado / Decimal(dias_decorridos)
    valor_projetado = ritmo_diario * Decimal(dias_no_mes)
    centavos = Decimal("0.01")

    return {
        "mes": mes_atual,
        "data_referencia": data_referencia,
        "valor_realizado": float(valor_realizado.quantize(centavos, rounding=ROUND_HALF_UP)),
        "valor_projetado": float(valor_projetado.quantize(centavos, rounding=ROUND_HALF_UP)),
        "ritmo_diario": float(ritmo_diario.quantize(centavos, rounding=ROUND_HALF_UP)),
        "dias_decorridos": dias_decorridos,
        "dias_no_mes": dias_no_mes,
        "metodo": "media_diaria_linear",
    }


@cache_vendas_response("grafico", _grafico_cache_paths, persistent=True)
def grafico_vendas(
    periodo: str = "3m",
    intervalo: str = "dia",
    sku: str = None,
    data_inicio: str = None,
    data_fim: str = None,
    loja: str = None,
    unidade_negocio: str = None,
    mostrar_estoque_geral: bool = False,
    mostrar_estoque_sku: bool = False,
    client_id: str = "",
    incluir_previsao_mes_atual: bool = False,
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
            "estoque_skus_com_saldo": [],
            "estoque_skus_pareto_com_saldo": [],
            "estoque_meta": {
                "success": True,
                "requested": bool(mostrar_estoque_geral or mostrar_estoque_sku),
                "lojas": 0,
                "sku": _normalizar_sku_estoque(sku) if sku else None,
                "pareto_skus_total": 0,
                "pareto_participacao": 0.0,
                "pareto_sku_corte": None,
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
                    conn_tmp = open_vendas_readonly(path)
                    try:
                        cur_tmp = conn_tmp.cursor()
                        cur_tmp.execute("SELECT MIN(data) FROM vendas")
                        row = cur_tmp.fetchone()
                        if row and row[0]:
                            data_tmp = datetime.fromisoformat(str(row[0])[:10])
                            if menor_data is None or data_tmp < menor_data:
                                menor_data = data_tmp
                    finally:
                        conn_tmp.close()
                data_inicio_dt = menor_data or (hoje - timedelta(days=90))
            else:
                data_inicio_dt = hoje - timedelta(days=90)

        catalog = load_composition_catalog(client_id)
        candidates = sorted({str(value).strip().upper() for value in catalog.candidates(sku, loja)}) if sku else []
        sale_fields = (
            "id_unico", "data", "devolucao", "sku", "quantidade", "valor",
            "loja_conta", "unidade_negocio", "numero", "comprador", "canal",
            "situacao", "nota_fiscal_id", "store_id",
            "numero_nf", "origem_tipo", "origem_id", "item_id",
        )
        conditions = ["coalesce(devolucao, 0) = 0"]
        params = []
        append_indexable_date_filter(
            conditions,
            params,
            "data",
            data_inicio_dt.strftime("%Y-%m-%d"),
            data_fim_str,
        )
        if sku:
            conditions.append("UPPER(trim(sku)) IN (" + ",".join("?" for _ in candidates) + ")")
            params.extend(candidates)
        if loja and loja != "__todas":
            filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
            if filtro_loja_sql:
                conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                params.extend(filtro_loja_params)
        if unidade_negocio and unidade_negocio != "__todos":
            mapa_lojas_grafico = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
            filtro_unidade_sql, filtro_unidade_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_lojas_grafico)
            if filtro_unidade_sql:
                conditions.append(filtro_unidade_sql.replace(" AND ", "", 1))
                params.extend(filtro_unidade_params)
        where_sql = " WHERE " + " AND ".join(conditions) + " ORDER BY data"
        rows = []
        for path in db_paths:
            if not os.path.exists(path):
                continue
            conn_db = open_vendas_readonly(path)
            try:
                cur_db = conn_db.cursor()
                columns = cached_table_columns(path, "vendas", conn_db)
                query = "SELECT " + ", ".join(
                    select_compatible_column(
                        columns, field, "0" if field in {"devolucao", "quantidade", "valor"} else "''",
                    ) for field in sale_fields
                ) + " FROM vendas" + where_sql
                cur_db.execute(query, params)
                rows.extend(cur_db.fetchall())
            finally:
                conn_db.close()

        # Keep the graph's original consolidation independent of runtime
        # adapters, then apply the shared legacy/document identity check.
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
                    "fallback", str(row[1] or "")[:10],
                    str(row[6] or "").strip().lower(), str(row[8] or "").strip(),
                    str(row[3] or "").strip().upper(), float(row[4] or 0),
                    float(row[5] or 0), str(row[9] or "").strip().lower(),
                )
            if chave in vistos:
                continue
            vistos.add(chave)
            rows_unicos.append(row)
        sales = [dict(zip(sale_fields, row)) for row in rows_unicos]
        consolidated = without_legacy_sale_duplicates(
            sales, blocked_store_names=legacy_store_conflicts(db_paths, sales),
        )
        rows_unicos = [
            tuple(item.get(field) for field in sale_fields) for item in consolidated
        ]

        if mostrar_estoque_geral and not sku:
            rows_pareto = _filtrar_vendas_lojas_ativas(rows_unicos, client_id, loja)
            pareto_skus, pareto_meta = _calcular_skus_pareto_80(rows_pareto)
        else:
            pareto_skus = []
            pareto_meta = {
                "pareto_skus_total": 0,
                "pareto_participacao": 0.0,
                "pareto_sku_corte": None,
            }

        # Project only after consolidating source rows; synthetic items keep no revenue.
        rows_unicos = [
            tuple(item.get(field) for field in sale_fields)
            for item in catalog.project_rows(
                [dict(zip(sale_fields, row)) for row in rows_unicos],
                target_sku=sku, store=loja,
            )
        ]
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
        returns_rows = []
        for db_path_nf in db_paths:
            if not os.path.exists(db_path_nf):
                continue
            conn_nf = open_vendas_readonly(db_path_nf)
            try:
                cur_nf = conn_nf.cursor()
                columns_nf = cached_table_columns(db_path_nf, "notas_entrada_itens", conn_nf)
                fields_nf = (
                    "data_emissao", "quantidade", "valor_total", "sku", "loja_conta",
                    "store_id", "id_unico", "numero_nota", "origem_codigo",
                )
                query_nf = "SELECT " + ", ".join(
                    select_compatible_column(columns_nf, field, "0" if field in {"quantidade", "valor_total"} else "''")
                    for field in fields_nf
                ) + " FROM notas_entrada_itens WHERE devolucao = 1"
                params_nf = []
                date_conditions_nf: list[str] = []
                append_indexable_date_filter(
                    date_conditions_nf,
                    params_nf,
                    "data_emissao",
                    data_inicio_dt.strftime("%Y-%m-%d"),
                    data_fim_str,
                )
                if date_conditions_nf:
                    query_nf += " AND " + " AND ".join(date_conditions_nf)
                if sku:
                    query_nf += " AND UPPER(trim(sku)) IN (" + ",".join("?" for _ in candidates) + ")"
                    params_nf.extend(candidates)
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
                returns_rows.extend(dict(zip(fields_nf, row)) for row in cur_nf.fetchall())
            except sqlite3.OperationalError as error:
                # Alguns bancos podem não ter tabela de notas ainda.
                if not ("no such table" in str(error).lower() and "notas_entrada_itens" in str(error).lower()):
                    mark_vendas_read_incomplete()
            finally:
                conn_nf.close()

        # A tenant may expose both a consolidated DB and its store mirror.
        unique_returns = {}
        for item in returns_rows:
            unique_id = str(item.get("id_unico") or "").strip()
            identity = str(item.get("store_id") or item.get("loja_conta") or "").strip().casefold()
            key = (identity, unique_id) if unique_id else tuple(
                str(item.get(field) or "") for field in fields_nf if field != "id_unico"
            )
            unique_returns.setdefault(key, item)
        for item in catalog.project_rows(list(unique_returns.values()), target_sku=sku, store=loja):
            chave, label = _chave_label_intervalo(item["data_emissao"])
            bucket = dados_agrupados.setdefault(chave, {
                "label": label, "vendas_valor": 0, "vendas_qtd": 0,
                "devol_valor": 0, "devol_qtd": 0,
            })
            bucket["devol_valor"] += item.get("valor_total") or 0
            bucket["devol_qtd"] += item.get("quantidade") or 0

        labels = []
        valores_vendas = []
        quantidades_vendas = []
        valores_devolucoes = []
        quantidades_devolucoes = []

        data_fim_dt = datetime.fromisoformat(data_fim_str) if data_fim_str else hoje
        previsao_mes_atual = None
        inicio_mes_atual = hoje.replace(day=1).date()
        if (
            incluir_previsao_mes_atual
            and data_inicio_dt.date() <= inicio_mes_atual
            and hoje.date() <= data_fim_dt.date()
        ):
            previsao_mes_atual = _calcular_previsao_mes_atual(rows_unicos, hoje)
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
            pareto_skus=pareto_skus,
        )
        estoque_series.setdefault("estoque_meta", {}).update(pareto_meta)

        resultado = {
            "labels": labels,
            "valores_vendas": valores_vendas,
            "quantidades_vendas": quantidades_vendas,
            "valores_devolucoes": valores_devolucoes,
            "quantidades_devolucoes": quantidades_devolucoes,
            **estoque_series,
        }
        if previsao_mes_atual is not None:
            resultado["previsao_mes_atual"] = previsao_mes_atual
        if catalog.metadata.get("incomplete"):
            mark_vendas_read_incomplete()
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao gerar gráfico de vendas: {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar gráfico de vendas.")

def _skus_sem_venda_cache_paths(arguments: dict) -> list[str]:
    client_id = arguments["client_id"]
    db_paths = _listar_bancos_vendas_tenant(client_id, arguments.get("loja"))
    paths = vendas_source_paths(client_id, db_paths, include_products=True, include_compositions=True)
    paths.append(os.path.join(get_tenant_path(client_id), "lojas_config.json"))
    return paths


def _carregar_lojas_estoque_skus(client_id: str) -> list[dict]:
    """Read the store identity snapshot without changing its legacy metadata."""
    try:
        path = os.path.join(get_tenant_path(client_id), "lojas_config.json")
        with open(path, encoding="utf-8-sig") as handle:
            lojas = json.load(handle)
    except (OSError, ValueError):
        return []
    return [loja for loja in lojas if isinstance(loja, dict)] if isinstance(lojas, list) else []


def _carregar_estoque_compilado_skus(
    path: str,
    *,
    client_id: str = "",
    loja: str | None = None,
    lojas: list[dict] | None = None,
) -> dict[str, dict]:
    lojas = lojas if lojas is not None else []
    loja_especifica = bool(str(loja or "").strip() and str(loja).strip() != "__todas")
    identidade = resolver_loja_ativa_para_leitura(client_id, loja, lojas=lojas) if loja_especifica else {}
    if loja_especifica and not identidade.get("loja_resolvida"):
        return {}

    # Preserve opaque store ids and SKUs, including any leading zeroes.
    df_estoque = pd.read_csv(path, dtype=str).fillna("")
    colunas_estoque = [
        "saldo_loja", "saldo_full", "quantidade_estoque", "saldo_estoque",
        "estoque", "quantidade", "saldo",
    ]
    coluna_estoque_usada = next((col for col in colunas_estoque if col in df_estoque.columns), None)
    if not coluna_estoque_usada:
        return {}

    por_loja_sku: dict[tuple[str, str], dict] = {}
    for _idx, row in df_estoque.iterrows():
        sku = str(row.get("sku", "")).strip().upper()
        if not sku:
            continue
        store_id = str(row.get("store_id", "")).strip()
        nome_loja = str(row.get("loja_sync", "") or row.get("loja", "")).strip()
        identidade_linha = resolver_loja_ativa_para_leitura(
            client_id, nome_loja, store_id, lojas=lojas,
        )
        if loja_especifica:
            # An explicit foreign id never falls back to a matching store name.
            if store_id:
                if store_id != identidade["store_id"]:
                    continue
            elif not identidade_linha.get("loja_resolvida") or identidade_linha["store_id"] != identidade["store_id"]:
                continue
        try:
            qtd = float(row.get(coluna_estoque_usada, 0) or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(qtd):
            continue
        chave_loja = store_id or identidade_linha.get("store_id") or nome_loja.casefold()
        por_loja_sku[(chave_loja, sku)] = {
            "quantidade": qtd,
            "produto": str(row.get("nome_bling", row.get("produto", row.get("nome", "-")))).strip(),
        }

    # Compiled balances are snapshots: deduplicate within a store, add across stores.
    estoque_data: dict[str, dict] = {}
    for (_chave_loja, sku), item in por_loja_sku.items():
        if sku not in estoque_data:
            estoque_data[sku] = dict(item)
        else:
            estoque_data[sku]["quantidade"] += item["quantidade"]
    return estoque_data


@cache_vendas_response("skus_sem_venda", _skus_sem_venda_cache_paths)
def skus_sem_venda(
    loja: str = None,
    unidade_negocio: str = None,
    client_id: str = ""
):
    """
    Retorna SKUs que não têm vendas há 30, 60 e 90 dias e que possuem estoque.
    Agrupa por períodos: ultimos_30_dias, ultimos_60_dias, ultimos_90_dias
    """
    try:
        # Carregar estoque compilado
        arquivo_cliente = _migrar_arquivo_legado_para_tenant(
            client_id,
            "produtos_compilado.csv",
            get_vendas_dependencies().paths.legacy_products_csv,
        )
        estoque_data = {}

        if arquivo_cliente and os.path.exists(arquivo_cliente):
            try:
                lojas_estoque = _carregar_lojas_estoque_skus(client_id)
                identidade_estoque = resolver_loja_ativa_para_leitura(client_id, loja, lojas=lojas_estoque)
                config_path = os.path.join(get_tenant_path(client_id), "lojas_config.json")
                namespace_estoque = json.dumps([
                    "produtos_compilado_skus", client_id, loja,
                    identidade_estoque.get("store_id", ""), vendas_file_signature(config_path),
                ])
                estoque_data = cached_file_value(
                    namespace_estoque,
                    arquivo_cliente,
                    lambda path: _carregar_estoque_compilado_skus(
                        path, client_id=client_id, loja=loja, lojas=lojas_estoque,
                    ),
                )
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
        catalog = load_composition_catalog(client_id)

        for db_path in db_paths:
            if not os.path.exists(db_path):
                continue

            conn = open_vendas_readonly(db_path)
            try:
                cur = conn.cursor()
                conditions = ["coalesce(devolucao, 0) = 0", "trim(coalesce(sku,'')) != ''"]
                params = []

                if loja and loja != "__todas":
                    filtro_loja_sql, filtro_loja_params = _sql_filtro_loja_vendas(loja)
                    if filtro_loja_sql:
                        conditions.append(filtro_loja_sql.replace(" AND ", "", 1))
                        params.extend(filtro_loja_params)

                if unidade_negocio and unidade_negocio != "__todos":
                    mapa_unidade = _carregar_mapeamento_lojas_virtuais_cliente(client_id)
                    filtro_un_sql, filtro_un_params = _sql_filtro_unidade_com_mapa(unidade_negocio, mapa_unidade)
                    if filtro_un_sql and unidade_negocio:
                        conditions.append(filtro_un_sql.replace(" AND ", "", 1))
                        params.extend(filtro_un_params)

                columns = cached_table_columns(db_path, "vendas", conn)
                store_column = select_compatible_column(columns, "store_id")
                query = (
                    "SELECT UPPER(trim(coalesce(sku,''))), MAX(data), loja_conta, " + store_column + " "
                    "FROM vendas WHERE "
                    + " AND ".join(conditions)
                    + " GROUP BY UPPER(trim(coalesce(sku,''))), loja_conta, store_id"
                )

                cur.execute(query, params)
                rows = cur.fetchall()

                for row in rows:
                    sku = row[0]
                    data_venda = str(row[1])[:10] if row[1] else None
                    # The parent remains commercially sold; its leaves also moved on this date.
                    component_skus = {sku}
                    component_skus.update(
                        component.sku for component in catalog.resolve(sku, row[3] or row[2] or loja)
                    )
                    for component_sku in component_skus:
                        normalized = str(component_sku).strip().upper()
                        if normalized not in ultima_venda_por_sku or (
                            data_venda and data_venda > (ultima_venda_por_sku[normalized] or "")
                        ):
                            ultima_venda_por_sku[normalized] = data_venda
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



__all__ = [
    "grafico_vendas",
    "skus_sem_venda",
]
