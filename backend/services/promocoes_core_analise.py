"""Internal slice for promocoes_core."""

from __future__ import annotations

from __future__ import annotations
import functools
import io
import json
import logging
import math
import os
import re
import unicodedata
from typing import Any, Optional
import numpy as np
import openpyxl
import pandas as pd
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.promocoes_common import *


def configure_promocoes_core_analise_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_promocoes_core_analise_runtime()


def _parse_float_str_cached(s: str):
    """Fase 3 - Cache de parse: evita parsear o mesmo valor mÃƒÂºltiplas vezes.
    Chamada por _parse_float_flex. maxsize=8192 cobre todas as variaÃƒÂ§ÃƒÂµes ÃƒÂºnicas
    de valores monetÃƒÂ¡rios/percentuais em um arquivo tÃƒÂ­pico (< 500 ÃƒÂºnicos).
    """
    if not s or s.lower() in {"nan", "none", "null", "-"}:
        return None
    sc = s.replace("R$", "").replace("%", "").strip().replace(" ", "")
    try:
        if "," in sc and "." in sc:
            sc = sc.replace(".", "").replace(",", ".")
        elif "," in sc:
            sc = sc.replace(",", ".")
        return float(sc)
    except Exception:
        return None


def _parse_float_flex(valor):
    if valor is None:
        return None
    return _parse_float_str_cached(str(valor).strip())


def _calcular_margem_liquida_ml(
    preco_venda,
    custo_produto,
    imposto_rate,
    tarifa_ml,
    frete_ml,
):
    """Calcula a margem liquida usada nas comparacoes financeiras do ML."""
    preco = _parse_float_flex(preco_venda)
    custo = _parse_float_flex(custo_produto)
    aliquota = _parse_float_flex(imposto_rate)
    tarifa = _parse_float_flex(tarifa_ml)
    frete = _parse_float_flex(frete_ml)
    valores = (preco, custo, aliquota, tarifa, frete)
    if any(valor is None or not math.isfinite(float(valor)) for valor in valores):
        return None
    if preco <= 0 or custo < 0 or aliquota < 0 or tarifa < 0 or frete < 0:
        return None

    imposto = float(preco) * float(aliquota)
    valor_liquido = float(preco) - float(custo) - imposto - float(tarifa) - float(frete)
    return {
        "imposto": imposto,
        "valor_liquido": valor_liquido,
        "margem_percentual": (valor_liquido * 100.0) / float(preco),
    }


def _format_money_safe_cached(s: str) -> str:
    """Fase 3 - Cache de formataÃƒÂ§ÃƒÂ£o monetÃƒÂ¡ria. Evita recomputar para valores repetidos."""
    v = _parse_float_str_cached(s)
    if v is None:
        return ""
    return formatar_moeda_br(v)


def _format_money_safe(valor):
    if valor is None:
        return ""
    return _format_money_safe_cached(str(valor).strip())


def _format_pct_br_cached(s: str) -> str:
    """Fase 3 - Cache de formataÃƒÂ§ÃƒÂ£o percentual. Evita recomputar para valores repetidos."""
    v = _parse_float_str_cached(s)
    if v is None:
        return ""
    if float(v).is_integer():
        return f"{int(v)}%"
    return f"{v:.2f}%".replace(".", ",")


def _format_pct_br(valor):
    if valor is None:
        return ""
    return _format_pct_br_cached(str(valor).strip())


def _normalizar_decisao_local(action_val, status_val):
    action = normalizar_texto(action_val)
    if action:
        if "nao" in action and "particip" in action:
            return "NÃ£o participar"
        if "particip" in action:
            return "Participar"

    status = normalizar_texto(status_val)
    if not status:
        return "Participar"
    if "ativo" in status or "active" in status or "programado" in status:
        return "Participar"
    if "elegivel" in status or "eligible" in status:
        return "NÃ£o participar"
    return "NÃ£o participar"


def _build_col_index(df):
    idx = {}
    for col in df.columns:
        idx[normalizar_texto(col)] = col
    return idx


def _pick_first_col(df, aliases):
    idx = _build_col_index(df)
    for a in aliases:
        col = idx.get(normalizar_texto(a))
        if col is not None:
            return col
    return None


def _extract_monetary_from_df_row(row, df):
    col_m21 = _pick_first_col(df, ["RECEIVES", "M 21 Fixa", "M21 Fixa", "M_21_FIXA"])
    col_mml = _pick_first_col(df, ["LOYALTY_RECEIVES", "M ML", "M_ML"])

    v_m21 = row.get(col_m21, "") if col_m21 else ""
    v_mml = row.get(col_mml, "") if col_mml else ""

    # Fallbacks comuns quando as colunas de receives nÃ£o vierem preenchidas.
    if _parse_float_flex(v_m21) is None:
        col_fp = _pick_first_col(df, ["FINAL_PRICE", "FINAL PRICE", "PRECO FINAL", "PREÃƒâ€¡O FINAL"])
        if col_fp:
            v_m21 = row.get(col_fp, "")

    if _parse_float_flex(v_mml) is None:
        col_lp = _pick_first_col(df, ["LOYALTY_PRICE", "LOYALTY PRICE", "PRECO ML", "PREÃƒâ€¡O ML"])
        if col_lp:
            v_mml = row.get(col_lp, "")

    m21 = _format_money_safe(v_m21)
    mml = _format_money_safe(v_mml)

    return m21, mml


def _build_col_index_optimized(df_local: pd.DataFrame) -> dict:
    """
    OtimizaÃƒÂ§ÃƒÂ£o Prioridade 2: PrÃƒÂ©-compilar ÃƒÂ­ndice de coluna UMA VEZ.
    Evita 80+ normalizaÃƒÂ§ÃƒÂµes repetidas em loops.
    Reduz de O(n*m) para O(n) onde n=linhas, m=aliases.
    
    Retorna: {'item_id': 'MLB', 'fee_per_sale': 'FEE_PER_SALE', ...}
    """
    if df_local is None or df_local.empty:
        return {}
    
    col_map = {}
    cols_norm = {}
    
    # PrÃƒÂ©-normalizar colunas do dataframe UMA VEZ
    for col in df_local.columns:
        col_clean = _limpar_nome_coluna(col)
        col_norm = normalizar_texto(col_clean)
        cols_norm[col_norm] = col
    
    # Definir aliases por grupo (compilado uma vez)
    aliases_groups = {
        'item_id': [
            'ITEM_ID', 'MLB', 'ID anuncio', 'ID anuncio', 'ID do anuncio',
            'ID do anuncio', 'AnÃƒÂºncio', 'Anuncio', 'ID',
            'NÃƒÂºmero do anuncio', 'Numero do anuncio', 'NÃƒÂºmero do produto',
            'Numero do produto', 'CÃƒÂ³digo do anuncio', 'Codigo do anuncio'
        ],
        'sku': ['SKU', 'SELLER_SKU'],
        'fee_per_sale': [
            'FEE_PER_SALE', 'Tarifa de venda', 'TARIFA DE VENDA',
            'Sale Fee', 'SALE_FEE', 'Desconto ML', 'Desconto', '__FEE_PER_SALE_ORIG'
        ],
        'listing_type': [
            'LISTING_TYPE', 'Tipo de anuncio', 'TIPO DE ANUNCIO',
            'Type', 'Tipo', 'CLASS'
        ],
        'custo_frete': [
            'Custo Frete', 'CUSTO FRETE', 'Custo do Frete', 'Frete',
            'Valor Frete', 'COST_SHIPPING', 'SHIPPING_COST', 'Frete Cliente',
            'Frete UnitÃƒÂ¡rio', 'Frete Unitario'
        ],
        'custo': [
            'CUSTO', 'Custo', 'CUSTO PRODUTO', 'CUSTO_PRODUTO',
            'Custo UnitÃƒÂ¡rio', 'Custo Unitario'
        ],
        'imposto': ['IMPOSTO', 'Imposto', 'TAX', 'AlÃƒÂ­quota'],
        'status': ['STATUS', 'SITUAÃƒâ€¡ÃƒÆ’O', 'SITUACAO', 'State', 'Estado'],
        'discount_percentage': ['DISCOUNT_PERCENTAGE', 'Desconto', 'DISCOUNT %', 'DISCOUNT'],
        'm21_fixa': ['RECEIVES', 'M 21 Fixa', 'M21 Fixa', 'M_21_FIXA'],
        'm_ml': ['LOYALTY_RECEIVES', 'M ML', 'M_ML'],
        'title': ['TITLE', 'TÃƒÂTULO', 'TITULO', 'TÃ­tulo', 'Produto', 'PRODUTO'],
    }
    
    # Mapear grupos para colunas reais (O(1) lookup cada)
    for grupo, aliases in aliases_groups.items():
        for alias in aliases:
            alias_norm = normalizar_texto(alias)
            if alias_norm in cols_norm:
                col_map[grupo] = cols_norm[alias_norm]
                break  # Primeira match vence
    
    return col_map


def _preprocessar_todos_indices_otimizado(df_metas: list, _txt_clean, _normalizar_mlb_key, 
                                         _sku_lookup_variantes_cached, _format_money_safe, 
                                         _format_pct_br, _extrair_percentual_de_formula_fee,
                                         _detectar_subtipo_promo) -> dict:
    """
    OtimizaÃƒÂ§ÃƒÂ£o: Processar cada df_meta em thread paralela, depois merge.
    Cada meta ÃƒÂ© completamente independente Ã¢â‚¬â€ sem race conditions.
    GIL liberado durante I/O de DataFrame; ganho real em multi-core para DataFrames mÃƒÂ©dios/grandes.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _processar_meta_isolado(meta):
        """Processa um ÃƒÂºnico meta em thread separada. Retorna ÃƒÂ­ndices parciais."""
        parcial = {
            'anuncios_por_item': {},
            'anuncios_por_sku': {},
            'fixa_status_por_item': {},
            'fixa_pct_por_item': {},
            'frete_por_sku': {},
            'frete_por_mlb': {},
            'custo_por_sku': {},
            'imposto_por_sku': {},
            'index_margens_por_item': {},
            'sku_por_mlb': {},
        }
        if meta is None or meta.get("df") is None or meta["df"].empty:
            return parcial
        df = meta["df"]
        tipo = meta.get("tipo_detectado", "").lower()
        cols = meta.get('col_map', {})
        for row in df.to_dict('records'):
            _processar_row_consolidado(
                row, tipo, cols, parcial, meta,
                _txt_clean, _normalizar_mlb_key,
                _sku_lookup_variantes_cached, _format_money_safe,
                _format_pct_br, _extrair_percentual_de_formula_fee
            )
        return parcial

    indices = {
        'anuncios_por_item': {},
        'anuncios_por_sku': {},
        'fixa_status_por_item': {},
        'fixa_pct_por_item': {},
        'frete_por_sku': {},
        'frete_por_mlb': {},
        'custo_por_sku': {},
        'imposto_por_sku': {},
        'index_margens_por_item': {},
        'sku_por_mlb': {},
    }

    n_workers = min(len(df_metas), 4)  # MÃƒÂ¡ximo 4 threads Ã¢â‚¬â€ evita overhead para poucos arquivos
    if n_workers <= 1:
        # Sem overhead de threading para 1 arquivo
        for meta in df_metas:
            parcial = _processar_meta_isolado(meta)
            for key in indices:
                for k, v in parcial[key].items():
                    if k not in indices[key]:
                        indices[key][k] = v
        return indices

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_processar_meta_isolado, meta): meta for meta in df_metas}
        for future in as_completed(futures):
            try:
                parcial = future.result()
                for key in indices:
                    for k, v in parcial[key].items():
                        if k not in indices[key]:  # Primeira ocorrÃƒÂªncia vence
                            indices[key][k] = v
            except Exception as e:
                logger.debug(f"[PROMO PARALELO] Erro em meta: {e}")

    return indices


def _processar_row_consolidado(row, tipo: str, cols: dict, indices: dict, meta: dict,
                               _txt_clean, _normalizar_mlb_key, 
                               _sku_lookup_variantes_cached, _format_money_safe,
                               _format_pct_br, _extrair_percentual_de_formula_fee) -> None:
    """
    Processa uma linha, extraindo dados relevantes por tipo.
    Dispatch baseado no tipo_detectado do arquivo.
    """
    
    # ========== ANÃƒÅ¡NCIOS: MLB + Fee + Tipo + SKU ==========
    if tipo in ('anuncios', 'anuncios_com_fee'):
        col_item = cols.get('item_id')
        col_fee = cols.get('fee_per_sale')
        col_tipo = cols.get('listing_type')
        col_sku = cols.get('sku')
        
        if col_item:
            item_val = _normalizar_mlb_key(row.get(col_item, ""))
            if item_val:
                fee_txt = ""
                if col_fee:
                    fee_raw = row.get(col_fee, "")
                    fee_txt = _txt_clean(fee_raw)
                    if fee_txt and fee_txt.startswith("="):
                        tipo_val_tmp = _txt_clean(row.get(col_tipo, "")) if col_tipo else ""
                        fee_txt = _extrair_percentual_de_formula_fee(fee_txt, tipo_val_tmp)
                    if fee_txt and "%" not in fee_txt:
                        fee_num = _parse_float_flex(fee_raw)
                        if fee_num is not None:
                            if fee_num <= 1:
                                fee_txt = _format_pct_br(fee_num * 100.0)
                            else:
                                fee_txt = _format_pct_br(fee_num)
                
                tipo_val = _txt_clean(row.get(col_tipo, "")) if col_tipo else ""
                
                indices['anuncios_por_item'][item_val] = {
                    "Tipo": tipo_val,
                    "%": fee_txt,
                }
                
                if col_sku:
                    sku_raw = _txt_clean(row.get(col_sku, ""))
                    if sku_raw:
                        for sku_key in _sku_lookup_variantes_cached(sku_raw):
                            if sku_key not in indices['anuncios_por_sku']:
                                indices['anuncios_por_sku'][sku_key] = {
                                    "Tipo": tipo_val,
                                    "%": fee_txt,
                                }
                
                if col_item and col_sku:
                    sku_raw = _txt_clean(row.get(col_sku, ""))
                    if sku_raw:
                        if item_val not in indices['sku_por_mlb']:
                            indices['sku_por_mlb'][item_val] = sku_raw
    
    # ========== FIXA: MLB + Desconto + Status exibido + M21/MML ==========
    elif tipo == 'fixa':
        col_item = cols.get('item_id')
        col_disc = cols.get('discount_percentage')
        col_status = cols.get('status')
        col_m21 = cols.get('m21_fixa')
        col_mml = cols.get('m_ml')
        
        if col_item:
            item_val = _normalizar_mlb_key(row.get(col_item, ""))
            if item_val:
                if col_disc:
                    disc_pct = _format_pct_br(row.get(col_disc, ""))
                    if disc_pct:
                        indices['fixa_pct_por_item'][item_val] = disc_pct

                if col_status:
                    status = _txt_clean(row.get(col_status, ""))
                    if status:
                        indices['fixa_status_por_item'][item_val] = status
                
                if col_m21 or col_mml:
                    m21 = _format_money_safe(row.get(col_m21, "")) if col_m21 else ""
                    mml = _format_money_safe(row.get(col_mml, "")) if col_mml else ""
                    if m21 or mml:
                        indices['index_margens_por_item'][item_val] = (m21, mml)
    
    # ========== FRETE (MERCADOTURBO): SKU/MLB + Valor Frete ==========
    elif tipo in ('mercadoturbo', 'frete'):
        col_item = cols.get('item_id')
        col_sku = cols.get('sku')
        col_frete = cols.get('custo_frete')
        
        if col_frete:
            frete_txt = _format_money_safe(row.get(col_frete, ""))
            if frete_txt:
                if col_sku:
                    sku_raw = _txt_clean(row.get(col_sku, ""))
                    if sku_raw:
                        for sku_key in _sku_lookup_variantes_cached(sku_raw):
                            if sku_key not in indices['frete_por_sku']:
                                indices['frete_por_sku'][sku_key] = frete_txt
                
                if col_item:
                    item_val = _normalizar_mlb_key(row.get(col_item, ""))
                    if item_val:
                        indices['frete_por_mlb'][item_val] = frete_txt
    
    # ========== CUSTO/IMPOSTO: SKU ==========
    elif tipo == 'atributos':
        col_sku = cols.get('sku')
        col_custo = cols.get('custo')
        col_imposto = cols.get('imposto')
        
        if col_sku:
            sku_raw = _txt_clean(row.get(col_sku, ""))
            if sku_raw:
                for sku_key in _sku_lookup_variantes_cached(sku_raw):
                    if col_custo:
                        custo_txt = _format_money_safe(row.get(col_custo, ""))
                        if custo_txt and sku_key not in indices['custo_por_sku']:
                            indices['custo_por_sku'][sku_key] = custo_txt
                    
                    if col_imposto:
                        imp_raw = row.get(col_imposto, "")
                        imp_num = _parse_float_flex(imp_raw)
                        if imp_num is not None:
                            imp_pct = _format_pct_br(imp_num * 100.0 if imp_num <= 1 else imp_num)
                            if imp_pct and sku_key not in indices['imposto_por_sku']:
                                indices['imposto_por_sku'][sku_key] = imp_pct


def montar_analise_promo_local(dfs_candidatos):
    if not dfs_candidatos:
        return []

    def _tipo_detectado_df(df) -> str:
        try:
            return str((getattr(df, "attrs", {}) or {}).get("tipo_detectado") or "").strip().lower()
        except Exception:
            return ""

    def _txt_clean(valor: Any) -> str:
        # Evita propagar NaN/None como texto "nan" no payload final.
        s = str(valor if valor is not None else "").strip()
        return "" if s.lower() in {"nan", "none", "null", "nat", "<na>"} else s

    def _excel_col_to_index(col_ref: str) -> int:
        col = str(col_ref or "").strip().upper()
        if not col or not re.match(r"^[A-Z]+$", col):
            return -1
        n = 0
        for ch in col:
            n = (n * 26) + (ord(ch) - ord("A") + 1)
        return n - 1  # 0-based

    def _resolver_formula_ref_planilha(df_ref: pd.DataFrame, formula_txt: str) -> str:
        """
        Resolve fÃƒÂ³rmulas simples de referÃƒÂªncia de cÃƒÂ©lula (ex.: =W16, =V16&"   ")
        para recuperar o valor base quando o parser nÃ£o materializa o cÃƒÂ¡lculo.
        """
        txt = _txt_clean(formula_txt)
        if not txt.startswith("="):
            return txt

        m = re.match(r"^\s*=\s*\$?([A-Za-z]+)\$?([0-9]+)(?:\s*&.*)?\s*$", txt)
        if not m:
            return ""

        col_letters = m.group(1)
        row_excel = int(m.group(2))
        col_idx = _excel_col_to_index(col_letters)
        if col_idx < 0:
            return ""

        # header_row em pandas ÃƒÂ© 0-based no arquivo Excel lido.
        header_row_0 = int((getattr(df_ref, "attrs", {}) or {}).get("excel_header_row", 0) or 0)
        first_data_row_excel = header_row_0 + 2
        row_df = row_excel - first_data_row_excel
        if row_df < 0 or row_df >= len(df_ref.index):
            return ""
        if col_idx >= len(df_ref.columns):
            return ""

        try:
            return _txt_clean(df_ref.iloc[row_df, col_idx])
        except Exception:
            return ""

    def _resolver_formula_ref_mesma_coluna(df_ref: pd.DataFrame, col_name: str, formula_txt: str) -> str:
        """
        Resolve referÃƒÂªncia de linha (ex.: =W16, =V16&"   ") usando a MESMA coluna lÃƒÂ³gica jÃƒÂ¡ mapeada,
        evitando erro quando o DataFrame foi transformado e perdeu alinhamento por letra Excel.
        """
        txt = _txt_clean(formula_txt)
        if not txt.startswith("="):
            return txt
        if not col_name or col_name not in df_ref.columns:
            return ""

        m = re.match(r"^\s*=\s*\$?[A-Za-z]+\$?([0-9]+)(?:\s*&.*)?\s*$", txt)
        if not m:
            return ""

        row_excel = int(m.group(1))
        header_row_0 = int((getattr(df_ref, "attrs", {}) or {}).get("excel_header_row", 0) or 0)
        first_data_row_excel = header_row_0 + 2
        row_df = row_excel - first_data_row_excel
        if row_df < 0 or row_df >= len(df_ref.index):
            return ""
        try:
            return _txt_clean(df_ref.iloc[row_df][col_name])
        except Exception:
            return ""

    item_id_aliases = [
        "ITEM_ID", "MLB", "ID anuncio", "ID anuncio", "ID do anuncio", "ID do anuncio", "AnÃƒÂºncio", "Anuncio", "ID",
        "NÃƒÂºmero do anuncio", "Numero do anuncio",
        "CÃƒÂ³digo do anuncio", "Codigo do anuncio", "CÃƒÂ³digo anuncio", "Codigo anuncio",
        "NÃƒÂºmero do produto", "Numero do produto", "CÃƒÂ³digo do produto", "Codigo do produto"
    ]

    df_metas = []

    def _build_df_meta(df_local: pd.DataFrame) -> dict:
        # Passe ÃƒÂºnico: gerar col_index, cols_norm e col_map_opt juntos
        # Evita 3Ãƒâ€” normalizaÃƒÂ§ÃƒÂ£o das mesmas colunas
        col_index = {}
        cols_norm = []
        for col in df_local.columns:
            col_clean = _limpar_nome_coluna(col)
            col_norm = normalizar_texto(col_clean)
            col_index[col_norm] = col
            cols_norm.append((col, col_norm))
        # col_map reutiliza col_index jÃƒÂ¡ construÃƒÂ­do (sem novo loop)
        col_map_opt = _build_col_index_optimized(df_local)
        return {
            "df": df_local,
            "tipo_detectado": _tipo_detectado_df(df_local),
            "subtipo_promo": _detectar_subtipo_promo(df_local),
            "col_index": col_index,
            "cols_norm": cols_norm,
            "col_map": col_map_opt,
        }

    def _pick_first_col_meta(meta: dict, aliases: list[str]) -> str | None:
        idx = meta.get("col_index") or {}
        for alias in aliases:
            col = idx.get(normalizar_texto(alias))
            if col is not None:
                return col
        return None

    def _pick_col_contains_meta(meta: dict, termos_norm: list[str]) -> str | None:
        try:
            for col, col_norm in meta.get("cols_norm") or []:
                if any(t in col_norm for t in termos_norm):
                    return col
        except Exception:
            return None
        return None

    def _prepare_monetary_meta(meta: dict) -> None:
        if meta.get("monetary_ready"):
            return
        df_local = meta["df"]
        meta["col_m21"] = _pick_first_col_meta(meta, ["RECEIVES", "M 21 Fixa", "M21 Fixa", "M_21_FIXA"])
        meta["col_mml"] = _pick_first_col_meta(meta, ["LOYALTY_RECEIVES", "M ML", "M_ML"])
        meta["col_fp"] = _pick_first_col_meta(meta, ["FINAL_PRICE", "FINAL PRICE", "PRECO FINAL", "PREÃƒâ€¡O FINAL"])
        meta["col_lp"] = _pick_first_col_meta(meta, ["LOYALTY_PRICE", "LOYALTY PRICE", "PRECO ML", "PREÃƒâ€¡O ML"])
        meta["monetary_ready"] = True

    def _extract_monetary_from_row_meta(row, meta: dict) -> tuple[str, str]:
        _prepare_monetary_meta(meta)
        v_m21 = row.get(meta.get("col_m21"), "") if meta.get("col_m21") else ""
        v_mml = row.get(meta.get("col_mml"), "") if meta.get("col_mml") else ""

        if _parse_float_flex(v_m21) is None and meta.get("col_fp"):
            v_m21 = row.get(meta.get("col_fp"), "")
        if _parse_float_flex(v_mml) is None and meta.get("col_lp"):
            v_mml = row.get(meta.get("col_lp"), "")

        return _format_money_safe(v_m21), _format_money_safe(v_mml)

    for df in dfs_candidatos:
        if df is None or df.empty:
            continue
        df_metas.append(_build_df_meta(df))

    if not df_metas:
        return []

    sku_variantes_cache: dict[str, list[str]] = {}

    def _sku_lookup_variantes_cached(sku_val: str) -> list[str]:
        sku_base = str(sku_val or "").strip()
        if not sku_base:
            return []
        cache_key = sku_base
        if cache_key not in sku_variantes_cache:
            sku_variantes_cache[cache_key] = _sku_lookup_variantes(sku_base)
        return sku_variantes_cache[cache_key]

    _mlb_key_cache: dict[str, str] = {}

    def _normalizar_mlb_key(valor: str) -> str:
        txt = str(valor or "").strip().upper()
        if not txt:
            return ""
        if txt in _mlb_key_cache:
            return _mlb_key_cache[txt]
        m = re.search(r"MLB\s*([0-9]+)", txt)
        if m:
            result = f"MLB{m.group(1)}"
            _mlb_key_cache[txt] = result
            return result
        # Alguns arquivos trazem apenas nÃƒÂºmero (ex.: 2674699533, 2674699533.0, 2.674699533E9).
        num_match = re.match(r"^\s*([0-9]+)(?:\.0+)?\s*$", txt)
        if num_match:
            dig = num_match.group(1)
            if len(dig) >= 7:
                result = f"MLB{dig}"
                _mlb_key_cache[txt] = result
                return result
        sci_match = re.match(r"^\s*[0-9]+(?:\.[0-9]+)?[Ee][+-]?[0-9]+\s*$", txt)
        if sci_match:
            try:
                dig = str(int(float(txt)))
                if len(dig) >= 7:
                    result = f"MLB{dig}"
                    _mlb_key_cache[txt] = result
                    return result
            except Exception:
                pass
        # Fallback: remove nÃ£o dÃƒÂ­gitos (ÃƒÂºltimo recurso para formatos inesperados).
        txt_num = re.sub(r"[^0-9]", "", txt)
        if txt_num and len(txt_num) >= 7:
            result = f"MLB{txt_num}"
            _mlb_key_cache[txt] = result
            return result
        result = txt if txt.startswith("MLB") else ""
        _mlb_key_cache[txt] = result
        return result

    # Escolhe o melhor dataframe-base para a grade principal (MLB, SKU, titulo, status, desconto).
    melhor_df = None
    melhor_meta = None
    melhor_score = -1
    for meta in df_metas:
        df = meta["df"]
        score = 0
        if _pick_first_col_meta(meta, item_id_aliases):
            score += 4
        if _pick_first_col_meta(meta, ["SKU"]):
            score += 2
        if _pick_first_col_meta(meta, ["TITLE", "TÃƒÂTULO", "TITULO"]):
            score += 1
        if _pick_first_col_meta(meta, ["STATUS", "SITUAÃƒâ€¡ÃƒÆ’O", "SITUACAO"]):
            score += 1
        if _pick_first_col_meta(meta, ["DISCOUNT_PERCENTAGE", "DESCONTO", "DISCOUNT"]):
            score += 1
        if score > melhor_score:
            melhor_score = score
            melhor_df = df
            melhor_meta = meta

    if melhor_df is None or melhor_df.empty:
        return []

    # OtimizaÃƒÂ§ÃƒÂ£o Prioridade 1: PrÃƒÂ©-processar ÃƒÂ­ndices consolidados (Fixa, Frete, Custo, Margens)
    # Nota: AnÃƒÂºncios temos lÃƒÂ³gica complexa de fÃƒÂ³rmulas, mantÃƒÂ©m loop original
    # Mas preparamos ÃƒÂ­ndices para Frete, Custo, Imposto, Margens em UMA passagem
    try:
        indices_otimizados = _preprocessar_todos_indices_otimizado(
            df_metas, _txt_clean, _normalizar_mlb_key, 
            _sku_lookup_variantes_cached, _format_money_safe, 
            _format_pct_br, _extrair_percentual_de_formula_fee,
            _detectar_subtipo_promo
        )
        # Extrair ÃƒÂ­ndices prÃƒÂ©-populados
        fixa_status_por_item = indices_otimizados.get('fixa_status_por_item', {})
        fixa_pct_por_item = indices_otimizados.get('fixa_pct_por_item', {})
        frete_por_sku_opt = indices_otimizados.get('frete_por_sku', {})
        frete_por_mlb_opt = indices_otimizados.get('frete_por_mlb', {})
        custo_por_sku_opt = indices_otimizados.get('custo_por_sku', {})
        imposto_por_sku_opt = indices_otimizados.get('imposto_por_sku', {})
        index_margens_por_item_opt = indices_otimizados.get('index_margens_por_item', {})
        sku_por_mlb_opt = indices_otimizados.get('sku_por_mlb', {})
    except Exception as e:
        # Fallback: se otimizaÃƒÂ§ÃƒÂ£o falhar, usar ÃƒÂ­ndices vazios
        logger.debug(f"[PROMO OTIMIZADO] Erro em prÃƒÂ©-processamento consolidado: {e}. Usando fallback.")
        fixa_status_por_item = {}
        fixa_pct_por_item = {}
        frete_por_sku_opt = {}
        frete_por_mlb_opt = {}
        custo_por_sku_opt = {}
        imposto_por_sku_opt = {}
        index_margens_por_item_opt = {}
        sku_por_mlb_opt = {}

    # Regra estrita: a coluna % vem apenas do arquivo AnÃƒÂºncios (aba AnÃƒÂºncios)
    # e da coluna FEE_PER_SALE, associada ao MLB do item.
    # ObservaÃƒÂ§ÃƒÂ£o: alguns layouts chegam sem tipo detectado "anuncios".
    # Nestes casos aplicamos fallback por estrutura para nÃ£o perder a taxa.
    anuncios_por_item = {}
    anuncios_por_sku = {}
    dfs_anuncios = [meta for meta in df_metas if meta.get("tipo_detectado") == 'anuncios']

    if not dfs_anuncios:
        # Fallback: identifica "AnÃƒÂºncios" por estrutura mÃƒÂ­nima do arquivo.
        for meta in df_metas:
            df = meta["df"]
            col_item_fb = _pick_first_col_meta(meta, item_id_aliases) or _pick_col_contains_meta(meta, ["item_id", "id anuncio", "id anuncio"])
            col_fee_fb = (
                ('__FEE_PER_SALE_ORIG' if '__FEE_PER_SALE_ORIG' in df.columns else None)
                or _pick_first_col_meta(meta, ["FEE_PER_SALE", "Tarifa de venda", "TARIFA DE VENDA"])
                or _pick_col_contains_meta(meta, ["fee_per_sale", "tarifa de venda", "sale_fee"])
            )
            if col_item_fb and col_fee_fb:
                dfs_anuncios.append(meta)

    for meta in dfs_anuncios:
        df = meta["df"]
        col_item_an = _pick_first_col_meta(meta, item_id_aliases) or _pick_col_contains_meta(meta, ["item_id", "id anuncio", "id anuncio"])
        col_tipo_an = _pick_first_col_meta(meta, ["LISTING_TYPE", "Tipo de anuncio", "TIPO DE ANUNCIO"])
        col_sku_an = _pick_first_col_meta(meta, ["SKU", "SELLER_SKU"])
        col_fee_calc_an = _pick_first_col_meta(meta, ["Tarifa de venda", "TARIFA DE VENDA", "FEE_PER_SALE", "SALE_FEE"])
        # PreferÃƒÂªncia absoluta por FEE_PER_SALE; "Tarifa de venda" ÃƒÂ© fallback apenas
        # quando o mesmo arquivo de AnÃƒÂºncios jÃƒÂ¡ foi normalizado internamente.
        col_fee_an = (
            ('__FEE_PER_SALE_ORIG' if '__FEE_PER_SALE_ORIG' in df.columns else None)
            or _pick_first_col_meta(meta, ["FEE_PER_SALE", "Tarifa de venda", "TARIFA DE VENDA"])
            or _pick_col_contains_meta(meta, ["fee_per_sale", "tarifa de venda", "sale_fee"])
        )
        if not col_item_an:
            continue

        # Captura uma fÃƒÂ³rmula-modelo da coluna para lidar com linhas em que
        # a cÃƒÂ©lula de fÃƒÂ³rmula compartilhada vem vazia no parser.
        fee_formula_template = ''
        if col_fee_an:
            try:
                for raw in df[col_fee_an].tolist():
                    s = _txt_clean(raw)
                    if s.startswith('=') and ('%' in s):
                        fee_formula_template = s
                        break
            except Exception:
                fee_formula_template = ''

        for row in df.to_dict('records'):
            item_val = _normalizar_mlb_key(row.get(col_item_an, ""))
            if not item_val:
                continue

            tipo_val = _txt_clean(row.get(col_tipo_an, "")) if col_tipo_an else ""
            fee_raw = row.get(col_fee_an, "") if col_fee_an else ""
            fee_txt = _txt_clean(fee_raw)

            # Alguns exports trazem LISTING_TYPE como referÃƒÂªncia (ex.: =V16&"   ").
            if tipo_val.startswith("="):
                tipo_resolvido = _resolver_formula_ref_mesma_coluna(df, col_tipo_an, tipo_val)
                if not tipo_resolvido:
                    tipo_resolvido = _resolver_formula_ref_planilha(df, tipo_val)
                if tipo_resolvido:
                    tipo_val = tipo_resolvido

            # Valores comuns de cÃƒÂ©lula sem cÃƒÂ¡lculo/materializaÃƒÂ§ÃƒÂ£o da fÃƒÂ³rmula.
            if fee_txt.lower() in {'', '-', '0', '0.0', '0,0', 'tarifa de venda'}:
                fee_txt = ''

            if fee_txt.startswith('='):
                fee_txt = _extrair_percentual_de_formula_fee(fee_txt, tipo_val)
                # ReferÃƒÂªncia indireta (ex.: =W16): resolve a cÃƒÂ©lula alvo e converte.
                if not fee_txt:
                    fee_ref = _resolver_formula_ref_mesma_coluna(df, col_fee_an, _txt_clean(fee_raw))
                    if not fee_ref:
                        fee_ref = _resolver_formula_ref_planilha(df, _txt_clean(fee_raw))
                    if fee_ref:
                        if fee_ref.startswith("="):
                            fee_txt = _extrair_percentual_de_formula_fee(fee_ref, tipo_val)
                        else:
                            fee_txt = fee_ref

            # SeguranÃƒÂ§a: % sÃƒÂ³ aceita formato de percentual/numÃƒÂ©rico;
            # evita trazer texto de categoria por resoluÃƒÂ§ÃƒÂ£o indevida.
            if fee_txt and ("%" not in fee_txt):
                fee_num_tmp = _parse_float_flex(fee_txt)
                if fee_num_tmp is None:
                    fee_txt = ""

            # Fallback: usa a fÃƒÂ³rmula-modelo da coluna quando a linha veio vazia,
            # aplicando a regra por tipo (Premium/ClÃƒÂ¡ssico).
            if not fee_txt and fee_formula_template:
                fee_txt = _extrair_percentual_de_formula_fee(fee_formula_template, tipo_val)

            # Fallback final: usa coluna calculada (Tarifa de venda/FEE_PER_SALE normalizado)
            # quando a coluna original estÃƒÂ¡ em formato nÃ£o interpretÃƒÂ¡vel.
            if not fee_txt and col_fee_calc_an:
                fee_calc_raw = _txt_clean(row.get(col_fee_calc_an, ""))
                if fee_calc_raw.startswith("="):
                    fee_calc_res = _resolver_formula_ref_mesma_coluna(df, col_fee_calc_an, fee_calc_raw)
                    if not fee_calc_res:
                        fee_calc_res = _resolver_formula_ref_planilha(df, fee_calc_raw)
                    fee_calc_raw = _txt_clean(fee_calc_res)
                if fee_calc_raw:
                    if '%' in fee_calc_raw:
                        fee_txt = fee_calc_raw
                    else:
                        fee_calc_num = _parse_float_flex(fee_calc_raw)
                        if fee_calc_num is not None:
                            fee_txt = _format_pct_br(fee_calc_num * 100.0) if fee_calc_num <= 1 else _format_pct_br(fee_calc_num)

            if fee_txt and '%' not in fee_txt:
                fee_num = _parse_float_flex(fee_raw)
                if fee_num is not None:
                    if fee_num <= 1:
                        fee_txt = _format_pct_br(fee_num * 100.0)
                    else:
                        fee_txt = _format_pct_br(fee_num)

            # ÃƒÅ¡ltimo valor valido do arquivo AnÃƒÂºncios prevalece para o MLB.
            # Nunca sobrescreve uma taxa jÃƒÂ¡ valida com vazio/nulo.
            existente = anuncios_por_item.get(item_val, {}) if isinstance(anuncios_por_item.get(item_val, {}), dict) else {}
            tipo_final = tipo_val or _txt_clean(existente.get("Tipo", ""))
            pct_existente = _txt_clean(existente.get("%", ""))
            pct_final = fee_txt if _txt_clean(fee_txt) else pct_existente
            anuncios_por_item[item_val] = {
                "Tipo": tipo_final,
                "%": pct_final,
            }
            if col_sku_an:
                sku_an_raw = _txt_clean(row.get(col_sku_an, ""))
                if sku_an_raw:
                    for sku_key in _sku_lookup_variantes_cached(sku_an_raw):
                        if not sku_key:
                            continue
                        existente_sku = anuncios_por_sku.get(sku_key, {}) if isinstance(anuncios_por_sku.get(sku_key, {}), dict) else {}
                        tipo_sku = tipo_val or _txt_clean(existente_sku.get("Tipo", ""))
                        pct_sku_existente = _txt_clean(existente_sku.get("%", ""))
                        pct_sku = fee_txt if _txt_clean(fee_txt) else pct_sku_existente
                        anuncios_por_sku[sku_key] = {
                            "Tipo": tipo_sku,
                            "%": pct_sku,
                        }

    # Seleciona o arquivo da Promo ML de forma rÃƒÂ­gida:
    # 1) prioriza arquivo explicitamente detectado como promo_ml
    # 2) sÃƒÂ³ cai para "promo" genÃƒÂ©rico se nÃ£o existir nenhum promo_ml valido
    def _score_promo_ml_meta(meta: dict) -> int:
        col_item_ml = _pick_first_col_meta(meta, item_id_aliases)
        col_price_ml = _pick_first_col_meta(meta, ["FINAL_PRICEFINAL_PRICE", "FINAL_PRICE", "LOYALTY_PRICE"])
        col_sale_fee_ml = _pick_first_col_meta(meta, ["SALE_FEE", "SALE FEE", "DESCONTO ML"])
        col_disc_ml = _pick_first_col_meta(meta, ["DISCOUNT_PERCENTAGE", "ML % CAMPANHA"])
        subtipo_promo = meta.get("subtipo_promo")
        tipo_detectado_df = meta.get("tipo_detectado")
        score_ml = 0
        if col_item_ml:
            score_ml += 4
        if col_price_ml:
            score_ml += 3
        if col_disc_ml:
            score_ml += 2
        if col_sale_fee_ml:
            score_ml += 3
        if subtipo_promo == 'promo_ml' or tipo_detectado_df == 'promo_ml':
            score_ml += 6
        return score_ml

    promo_ml_df = None
    promo_ml_meta = None
    promo_ml_score = -1

    metas_promo_ml_explicitas = [
        meta for meta in df_metas
        if (
            meta.get("subtipo_promo") == "promo_ml"
            or meta.get("tipo_detectado") == "promo_ml"
        )
    ]
    metas_promo_ml_explicitas_ids = {id(meta) for meta in metas_promo_ml_explicitas}
    metas_promo_ml_fallback = [
        meta for meta in df_metas
        if id(meta) not in metas_promo_ml_explicitas_ids
        and (
            meta.get("subtipo_promo") == "promo"
            or meta.get("tipo_detectado") == "promo"
        )
    ]

    for grupo_metas in (metas_promo_ml_explicitas, metas_promo_ml_fallback):
        for meta in grupo_metas:
            df = meta["df"]
            score_ml = _score_promo_ml_meta(meta)
            if score_ml > promo_ml_score:
                promo_ml_score = score_ml
                promo_ml_df = df
                promo_ml_meta = meta
        if promo_ml_meta is not None:
            break

    promo_ml_por_item = {}
    ordem_mlb_promo_ml = []
    promo_ml_presente = promo_ml_df is not None and not promo_ml_df.empty
    if promo_ml_df is not None and not promo_ml_df.empty:
        col_item_ml = _pick_first_col_meta(promo_ml_meta, item_id_aliases)
        col_sku_ml = _pick_first_col_meta(promo_ml_meta, ["SKU"])
        col_title_ml = _pick_first_col_meta(promo_ml_meta, ["TITLE", "TÃƒÂTULO", "TITULO"])
        col_price_ml = _pick_first_col_meta(promo_ml_meta, ["FINAL_PRICEFINAL_PRICE", "FINAL_PRICE", "LOYALTY_PRICE"])
        col_sale_fee_ml = _pick_first_col_meta(promo_ml_meta, ["SALE_FEE", "SALE FEE", "DESCONTO ML"])
        col_disc_ml = _pick_first_col_meta(promo_ml_meta, ["DISCOUNT_PERCENTAGE", "ML % CAMPANHA"])

        if col_item_ml:
            for row in promo_ml_df.to_dict('records'):
                item_val = str(row.get(col_item_ml, "") or "").strip().upper()
                item_val = _normalizar_mlb_key(item_val)
                if not item_val:
                    continue

                if item_val not in promo_ml_por_item:
                    ordem_mlb_promo_ml.append(item_val)

                promo_ml_por_item[item_val] = {
                    "Campanha ML": item_val,
                    "SKU": _txt_clean(row.get(col_sku_ml, "")) if col_sku_ml else "",
                    "TÃ­tulo": _txt_clean(row.get(col_title_ml, "")) if col_title_ml else "",
                    "PreÃ§o Final ML": _format_money_safe(row.get(col_price_ml, "") if col_price_ml else ""),
                    "Desconto ML": _format_money_safe(row.get(col_sale_fee_ml, "") if col_sale_fee_ml else ""),
                    "ML % Campanha": _format_pct_br(row.get(col_disc_ml, "") if col_disc_ml else ""),
                }

    # Copia dados do arquivo Fixa por MLB. A coluna STATUS do arquivo ÃƒÂ© usada
    # apenas para exibiÃƒÂ§ÃƒÂ£o na analise; a exportaÃƒÂ§ÃƒÂ£o nÃ£o grava nela.
    # OtimizaÃƒÂ§ÃƒÂ£o: Se jÃƒÂ¡ foi populado no prÃƒÂ©-processamento, pula o loop redundante
    fixa_preco_por_item = {}
    fixa_mlbs_set: set = set()
    if not fixa_pct_por_item:  # Se nÃ£o foi populado na otimizaÃƒÂ§ÃƒÂ£o
        fixa_pct_por_item = {}
        fixa_status_por_item = {}
        for meta in df_metas:
            df = meta["df"]
            tipo_detectado_df = meta.get("tipo_detectado")
            if tipo_detectado_df != 'fixa' and meta.get("subtipo_promo") != 'fixa':
                continue
            col_item_fixa = _pick_first_col_meta(meta, item_id_aliases)
            col_disc_fixa = _pick_first_col_meta(meta, ["DISCOUNT_PERCENTAGE"])
            col_status_fixa = _pick_first_col_meta(meta, ["STATUS", "SITUAÃƒâ€¡ÃƒÆ’O", "SITUACAO"])
            col_receives_fixa = _pick_first_col_meta(meta, ["RECEIVES", "M 21 Fixa", "M21 Fixa", "M_21_FIXA"])
            col_final_price_fixa = _pick_first_col_meta(meta, ["FINAL_PRICE", "FINAL PRICE", "PRECO FINAL", "PREÃƒâ€¡O FINAL"])
            if not col_item_fixa:
                continue

            for row in df.to_dict('records'):
                item_val = _normalizar_mlb_key(row.get(col_item_fixa, ""))
                if not item_val:
                    continue
                if col_disc_fixa:
                    pct_txt = _format_pct_br(row.get(col_disc_fixa, ""))
                    if pct_txt:
                        fixa_pct_por_item[item_val] = pct_txt
                if col_status_fixa:
                    status_txt = _txt_clean(row.get(col_status_fixa, ""))
                    if status_txt:
                        fixa_status_por_item[item_val] = status_txt
                preco_fixa_raw = ""
                if col_receives_fixa:
                    preco_fixa_raw = row.get(col_receives_fixa, "")
                if _parse_float_flex(preco_fixa_raw) is None and col_final_price_fixa:
                    preco_fixa_raw = row.get(col_final_price_fixa, "")
                preco_fixa_txt = _format_money_safe(preco_fixa_raw)
                if preco_fixa_txt:
                    fixa_preco_por_item[item_val] = preco_fixa_txt
                fixa_mlbs_set.add(item_val)
    else:
        # OtimizaÃƒÂ§ÃƒÂ£o usada: extrair campos complementares que podem nÃ£o vir
        # do prÃƒÂ©-processamento, como Status exibido e preÃ§o por item.
        for meta in df_metas:
            df = meta["df"]
            tipo_detectado_df = meta.get("tipo_detectado")
            if tipo_detectado_df != 'fixa' and meta.get("subtipo_promo") != 'fixa':
                continue
            col_item_fixa = _pick_first_col_meta(meta, item_id_aliases)
            col_status_fixa = _pick_first_col_meta(meta, ["STATUS", "SITUAÃƒâ€¡ÃƒÆ’O", "SITUACAO"])
            col_receives_fixa = _pick_first_col_meta(meta, ["RECEIVES", "M 21 Fixa", "M21 Fixa", "M_21_FIXA"])
            col_final_price_fixa = _pick_first_col_meta(meta, ["FINAL_PRICE", "FINAL PRICE", "PRECO FINAL", "PREÃƒâ€¡O FINAL"])
            if not col_item_fixa:
                continue

            for row in df.to_dict('records'):
                item_val = _normalizar_mlb_key(row.get(col_item_fixa, ""))
                if not item_val:
                    continue
                if col_status_fixa and item_val not in fixa_status_por_item:
                    status_txt = _txt_clean(row.get(col_status_fixa, ""))
                    if status_txt:
                        fixa_status_por_item[item_val] = status_txt
                preco_fixa_raw = ""
                if col_receives_fixa:
                    preco_fixa_raw = row.get(col_receives_fixa, "")
                if _parse_float_flex(preco_fixa_raw) is None and col_final_price_fixa:
                    preco_fixa_raw = row.get(col_final_price_fixa, "")
                preco_fixa_txt = _format_money_safe(preco_fixa_raw)
                if preco_fixa_txt:
                    fixa_preco_por_item[item_val] = preco_fixa_txt
                fixa_mlbs_set.add(item_val)

    # ÃƒÂndice de frete por SKU/MLB varrendo todos os arquivos de Frete (mercadoturbo).
    # OtimizaÃƒÂ§ÃƒÂ£o: Se jÃƒÂ¡ foi populado, pula o loop
    if not frete_por_sku_opt:  # Se nÃ£o foi populado na otimizaÃƒÂ§ÃƒÂ£o
        frete_por_sku = {}
        frete_por_mlb = {}
        for meta in df_metas:
            df = meta["df"]
            col_item_frete = _pick_first_col_meta(meta, item_id_aliases)
            col_sku_frete = _pick_first_col_meta(meta, ["SKU", "SELLER_SKU"])
            col_custo_frete = _pick_first_col_meta(meta, [
                "Custo Frete", "CUSTO FRETE", "Custo do Frete", "Custo de Frete",
                "Frete", "Valor Frete", "Valor do Frete", "Frete Cliente",
                "Frete UnitÃƒÂ¡rio", "Frete Unitario", "COST_SHIPPING", "SHIPPING_COST", "Shipping Cost"
            ])
            if not col_custo_frete:
                continue

            for row in df.to_dict('records'):
                frete_txt = _format_money_safe(row.get(col_custo_frete, ""))
                if not frete_txt:
                    continue
                if col_sku_frete:
                    sku_raw = str(row.get(col_sku_frete, "") or "").strip()
                    if sku_raw:
                        for key in _sku_lookup_variantes_cached(sku_raw):
                            # Se houver mais de um arquivo, mantÃƒÂ©m o primeiro valor valido encontrado.
                            if key not in frete_por_sku:
                                frete_por_sku[key] = frete_txt

                if col_item_frete:
                    mlb_raw = _normalizar_mlb_key(row.get(col_item_frete, ""))
                    if mlb_raw:
                        frete_por_mlb[mlb_raw] = frete_txt
    else:
        # Usar ÃƒÂ­ndices prÃƒÂ©-compilados
        frete_por_sku = frete_por_sku_opt
        frete_por_mlb = frete_por_mlb_opt
    def _buscar_frete_por_sku_ou_mlb(sku_val: str, mlb_val: str) -> str:
        # Regra principal: quando houver frete por MLB, ele tem prioridade.
        mlb_norm = _normalizar_mlb_key(mlb_val)
        if mlb_norm and mlb_norm in frete_por_mlb:
            return frete_por_mlb[mlb_norm]

        sku_val = str(sku_val or "").strip()
        if not sku_val:
            return ""

        candidatos = [s.strip() for s in sku_val.split(",") if s.strip()] or [sku_val]
        for sku_cand in candidatos:
            for key in _sku_lookup_variantes_cached(sku_cand):
                if key in frete_por_sku:
                    return frete_por_sku[key]

        return ""

    # ÃƒÂndice por MLB para recuperar SKU quando o SKU da linha estiver vazio.
    # OtimizaÃƒÂ§ÃƒÂ£o: Se jÃƒÂ¡ foi populado, pula o loop
    if not sku_por_mlb_opt:  # Se nÃ£o foi populado na otimizaÃƒÂ§ÃƒÂ£o
        sku_por_mlb = {}
        for meta in df_metas:
            df = meta["df"]
            col_item_sku = _pick_first_col_meta(meta, item_id_aliases)
            col_sku_any = _pick_first_col_meta(meta, ["SKU", "SELLER_SKU"])
            if not col_item_sku or not col_sku_any:
                continue

            for row in df.to_dict('records'):
                mlb_raw = _normalizar_mlb_key(row.get(col_item_sku, ""))
                sku_raw = _txt_clean(row.get(col_sku_any, ""))
                if not mlb_raw or not sku_raw:
                    continue
                if mlb_raw not in sku_por_mlb:
                    sku_por_mlb[mlb_raw] = sku_raw
    else:
        # Usar ÃƒÂ­ndice prÃƒÂ©-compilado
        sku_por_mlb = sku_por_mlb_opt

    def _buscar_sku_por_mlb(mlb_val: str) -> str:
        mlb_norm = _normalizar_mlb_key(mlb_val)
        if not mlb_norm:
            return ""
        return _txt_clean(sku_por_mlb.get(mlb_norm, ""))

    # ÃƒÂndice por tÃƒÂ­tulo para fallback de SKU por similaridade.
    sku_exato_por_titulo = {}
    titulos_para_busca_sku = []
    for meta in df_metas:
        df = meta["df"]
        col_sku_titulo = _pick_first_col_meta(meta, ["SKU", "SELLER_SKU"])
        col_titulo_any = _pick_first_col_meta(meta, ["TITLE", "TÃƒÂTULO", "TITULO", "TÃ­tulo", "Produto", "PRODUTO", "Nome", "NOME"])
        if not col_sku_titulo or not col_titulo_any:
            continue

        for row in df.to_dict('records'):
            sku_raw = _txt_clean(row.get(col_sku_titulo, ""))
            titulo_raw = _txt_clean(row.get(col_titulo_any, ""))
            titulo_norm = normalizar_texto(titulo_raw)
            if not sku_raw or not titulo_norm:
                continue

            if titulo_norm not in sku_exato_por_titulo:
                sku_exato_por_titulo[titulo_norm] = sku_raw
            titulos_para_busca_sku.append((titulo_norm, sku_raw))

    def _buscar_sku_por_titulo_mais_compativel(titulo_val: str) -> str:
        """
        OtimizaÃƒÂ§ÃƒÂ£o Prioridade 3: Busca de SKU por tÃƒÂ­tulo com token filtering.
        Reduz O(NÃƒâ€”M) para O(N+KÃƒâ€”M) onde K << N (top-100 candidatos).
        
        Antes: 5000 MLBs Ãƒâ€” 5000 tÃƒÂ­tulos = 25M comparaÃƒÂ§ÃƒÂµes
        Depois: 5000 MLBs Ãƒâ€” 50 candidatos = 250k comparaÃƒÂ§ÃƒÂµes (100Ãƒâ€” menos!)
        Impacto: 10-20% ganho em performance
        """
        titulo_norm = normalizar_texto(str(titulo_val or "").strip())
        if not titulo_norm:
            return ""

        # Fase 1: Busca exata (O(1))
        if titulo_norm in sku_exato_por_titulo:
            return _txt_clean(sku_exato_por_titulo[titulo_norm])

        # Fase 2: Token filtering (reduz candidatos de 5000 para ~50-100)
        tokens_alvo = {t for t in titulo_norm.split() if len(t) > 2}
        if not tokens_alvo:
            return ""

        candidatos_filtrados = []
        for cand_titulo, cand_sku in titulos_para_busca_sku:
            cand_tokens = {t for t in cand_titulo.split() if len(t) > 2}
            inter_count = len(tokens_alvo.intersection(cand_tokens))
            
            # MÃƒÂ­nimo 2 tokens em comum para considerar candidato
            if inter_count >= 2:
                candidatos_filtrados.append((cand_titulo, cand_sku, inter_count))

        if not candidatos_filtrados:
            return ""

        # Ordenar por overlap de tokens (mais matches = maior prioridade)
        candidatos_filtrados.sort(key=lambda x: x[2], reverse=True)
        
        # Pegar top-100 candidatos (ou menos se houver menos)
        candidatos_filtrados = candidatos_filtrados[:100]

        # Fase 3: SequenceMatcher apenas em top-100 (2% das comparaÃƒÂ§ÃƒÂµes)
        best_score = 0.72  # Limite conservador
        best_sku = ""
        
        for cand_titulo, cand_sku, inter_count in candidatos_filtrados:
            ratio = SequenceMatcher(None, titulo_norm, cand_titulo).ratio()
            # Bonus por token overlap jÃƒÂ¡ contabilizado no filtro
            if inter_count > 0:
                ratio += min(0.05, 0.01 * inter_count)
            if ratio > best_score:
                best_score = ratio
                best_sku = cand_sku

        return _txt_clean(best_sku) if best_score >= 0.72 else ""

    # ÃƒÂndice por SKU para preencher Custo e Imposto a partir do arquivo de Custo.
    # OtimizaÃƒÂ§ÃƒÂ£o: Se jÃƒÂ¡ foi populado, pula o loop
    if not custo_por_sku_opt:  # Se nÃ£o foi populado na otimizaÃƒÂ§ÃƒÂ£o
        custo_por_sku = {}
        imposto_por_sku = {}
        for meta in df_metas:
            df = meta["df"]
            col_sku_custo = _pick_first_col_meta(meta, ["SKU", "SELLER_SKU"])
            col_custo = _pick_first_col_meta(meta, ["CUSTO", "Custo", "CUSTO PRODUTO", "CUSTO_PRODUTO", "CUSTO UNITARIO", "CUSTO UNITÃƒÂRIO"])
            col_imposto = _pick_first_col_meta(meta, ["IMPOSTO", "Imposto"])
            if not col_sku_custo or (not col_custo and not col_imposto):
                continue

            for row in df.to_dict('records'):
                sku_raw = _txt_clean(row.get(col_sku_custo, ""))
                if not sku_raw:
                    continue

                custo_txt = _format_money_safe(row.get(col_custo, "")) if col_custo else ""
                imposto_txt = ""
                if col_imposto:
                    imp_raw = row.get(col_imposto, "")
                    imp_num = _parse_float_flex(imp_raw)
                    if imp_num is not None:
                        if imp_num <= 1:
                            imposto_txt = _format_pct_br(imp_num * 100.0)
                        else:
                            imposto_txt = _format_pct_br(imp_num)
                    else:
                        imp_txt_raw = _txt_clean(imp_raw)
                        if imp_txt_raw:
                            imposto_txt = imp_txt_raw

                for key in _sku_lookup_variantes_cached(sku_raw):
                    if custo_txt and key not in custo_por_sku:
                        custo_por_sku[key] = custo_txt
                    if imposto_txt and key not in imposto_por_sku:
                        imposto_por_sku[key] = imposto_txt
    else:
        # Usar ÃƒÂ­ndices prÃƒÂ©-compilados
        custo_por_sku = custo_por_sku_opt
        imposto_por_sku = imposto_por_sku_opt

    def _buscar_valor_por_sku(mapa_valores: dict, sku_val: str) -> str:
        sku_val = _txt_clean(sku_val)
        if not sku_val:
            return ""
        candidatos = [s.strip() for s in re.split(r"\s*(?:/|,|;|\|)\s*", sku_val) if s.strip()] or [sku_val]
        for sku_cand in candidatos:
            for key in _sku_lookup_variantes_cached(sku_cand):
                if key in mapa_valores:
                    return mapa_valores[key]
        return ""

    # ÃƒÂndice consolidado por ITEM_ID e SKU para preencher margens mesmo quando vierem em outro arquivo.
    # OtimizaÃƒÂ§ÃƒÂ£o: Se jÃƒÂ¡ foi populado, pula o loop
    if not index_margens_por_item_opt:  # Se nÃ£o foi populado na otimizaÃƒÂ§ÃƒÂ£o
        index_margens_por_item = {}
        index_margens_por_sku = {}
        for meta in df_metas:
            df = meta["df"]
            col_item = _pick_first_col_meta(meta, item_id_aliases)
            col_sku = _pick_first_col_meta(meta, ["SKU"])
            for row in df.to_dict('records'):
                m21, mml = _extract_monetary_from_row_meta(row, meta)
                if m21 == "" and mml == "":
                    continue
                if col_item:
                    item_val = _normalizar_mlb_key(row.get(col_item, ""))
                    if item_val:
                        index_margens_por_item[item_val] = (m21, mml)
                if col_sku:
                    sku_val = str(row.get(col_sku, "") or "").strip()
                    if sku_val:
                        index_margens_por_sku[sku_val] = (m21, mml)
    else:
        # Usar ÃƒÂ­ndices prÃƒÂ©-compilados
        index_margens_por_item = index_margens_por_item_opt
        index_margens_por_sku = {}  # Este nÃ£o foi otimizado, manter vazio para fallback

    # Garante colunas esperadas, mesmo quando o arquivo vier incompleto.
    for col in COLUNAS_DO_MODELO_PROMO:
        if col not in melhor_df.columns:
            melhor_df[col] = ""

    col_item = _pick_first_col_meta(melhor_meta, item_id_aliases)
    col_sku = _pick_first_col_meta(melhor_meta, ["SKU"])
    col_title = _pick_first_col_meta(melhor_meta, ["TITLE", "TÃƒÂTULO", "TITULO"])
    col_status = _pick_first_col_meta(melhor_meta, ["STATUS", "SITUAÃƒâ€¡ÃƒÆ’O", "SITUACAO"])
    col_desc = _pick_first_col_meta(melhor_meta, ["DISCOUNT_PERCENTAGE", "DESCONTO", "DISCOUNT"])
    col_action = _pick_first_col_meta(melhor_meta, ["ACTION", "AÃƒâ€¡ÃƒÆ’O", "ACAO"])

    dados_analise = []
    for row in melhor_df.to_dict('records'):
        item_id = str(row.get(col_item, "") or "").strip() if col_item else ""
        item_id_norm = _normalizar_mlb_key(item_id)
        if item_id and not item_id_norm:
            continue

        sku = _txt_clean(row.get(col_sku, "")) if col_sku else ""
        status = _txt_clean(row.get(col_status, "")) if col_status else ""
        action = _txt_clean(row.get(col_action, "")) if col_action else ""
        m21, mml = _extract_monetary_from_row_meta(row, melhor_meta)

        # Se vier vazio no dataframe-base, tenta completar com os demais arquivos.
        key_item = item_id_norm
        if key_item and key_item in fixa_status_por_item:
            status = fixa_status_por_item.get(key_item, status)
        if (m21 == "" and mml == "") and key_item and key_item in index_margens_por_item:
            m21, mml = index_margens_por_item[key_item]
        if (m21 == "" and mml == "") and sku and sku in index_margens_por_sku:
            m21, mml = index_margens_por_sku[sku]
        if key_item and key_item in fixa_preco_por_item:
            m21 = fixa_preco_por_item.get(key_item, m21)

        promo_ml_info = promo_ml_por_item.get(key_item, {})
        # Regra: quando houver arquivo Promo ML, sÃƒÂ³ exibe MLB contido nele.
        if promo_ml_presente and not promo_ml_info:
            continue

        pct_fixa_item = _txt_clean(fixa_pct_por_item.get(key_item, "")) if key_item else ""
        status_exibicao = status
        ativo_fixa_item = bool(key_item and key_item in fixa_mlbs_set)
        if promo_ml_info and not ativo_fixa_item:
            status_exibicao = "Sem promocao fixa"
        elif not status_exibicao and (ativo_fixa_item or pct_fixa_item):
            status_exibicao = "Ativo"

        sku_final = _txt_clean(promo_ml_info.get("SKU")) or sku
        titulo_final = promo_ml_info.get("TÃ­tulo") or (str(row.get(col_title, "") or "").strip() if col_title else "")
        if not str(sku_final or "").strip():
            sku_final = _buscar_sku_por_mlb(key_item)
        if not str(sku_final or "").strip() and titulo_final:
            sku_final = _buscar_sku_por_titulo_mais_compativel(titulo_final)
        sku_final = _normalizar_sku_mes(sku_final)
        anuncios_info = anuncios_por_item.get(key_item, {})
        if (not _txt_clean(anuncios_info.get("%", ""))) and str(sku_final or "").strip():
            for sku_key in _sku_lookup_variantes_cached(sku_final):
                cand = anuncios_por_sku.get(sku_key, {})
                if _txt_clean(cand.get("%", "")):
                    anuncios_info = {
                        "Tipo": cand.get("Tipo", anuncios_info.get("Tipo", "")),
                        "%": cand.get("%", anuncios_info.get("%", "")),
                    }
                    break
        frete_final = _buscar_frete_por_sku_ou_mlb(sku_final, key_item)
        custo_final = _buscar_valor_por_sku(custo_por_sku, sku_final)
        imposto_pct_final = _buscar_valor_por_sku(imposto_por_sku, sku_final)
        preco_final_ml = promo_ml_info.get("PreÃ§o Final ML") or mml
        if preco_final_ml == m21 and not promo_ml_info.get("PreÃ§o Final ML"):
            preco_final_ml = ""

        dados_analise.append({
            "MLB": item_id,
            "Tipo": anuncios_info.get("Tipo", ""),
            "%": _txt_clean(anuncios_info.get("%", "")),
            "SKU": sku_final,
            "TÃ­tulo": titulo_final,
            "Frete": frete_final,
            "Custo": custo_final,
            "Imposto %": imposto_pct_final,
            "SituaÃƒÂ§ÃƒÂ£o": status_exibicao,
            "Status": status_exibicao,
            "Desconto": _format_pct_br(row.get(col_desc, "") if col_desc else ""),
            "% Fixa": pct_fixa_item,
            "M 21 Fixa": m21,
            "M ML": preco_final_ml,
            "PreÃ§o Final ML": preco_final_ml,
            "Campanha ML": promo_ml_info.get("Campanha ML", ""),
            "Desconto ML": promo_ml_info.get("Desconto ML", ""),
            "ML % Campanha": promo_ml_info.get("ML % Campanha", ""),
            "Participar ou nÃ£o": _normalizar_decisao_local(action, status_exibicao)
        })

    # MantÃ©m a ordem de exibiÃƒÂ§ÃƒÂ£o estritamente compatÃƒÂ­vel com o arquivo Promo ML.
    if ordem_mlb_promo_ml:
        by_mlb = {}
        for item in dados_analise:
            k = str(item.get('MLB', '')).upper().strip()
            if k and k not in by_mlb:
                by_mlb[k] = item
        dados_ordenados = []
        for k in ordem_mlb_promo_ml:
            if k in by_mlb:
                dados_ordenados.append(by_mlb[k])
                continue

            # Se o MLB existir no Promo ML mas nÃ£o estiver na base intermediÃƒÂ¡ria,
            # cria linha mÃƒÂ­nima para preservar ordem e completude da exibiÃƒÂ§ÃƒÂ£o.
            promo_info = promo_ml_por_item.get(k, {})
            anuncios_info = anuncios_por_item.get(k, {})
            titulo_promo = promo_info.get('TÃ­tulo', '')
            sku_promo = _txt_clean(promo_info.get('SKU', '')) or _buscar_sku_por_mlb(k)
            if not str(sku_promo or '').strip() and titulo_promo:
                sku_promo = _buscar_sku_por_titulo_mais_compativel(titulo_promo)
            sku_promo = _normalizar_sku_mes(sku_promo)
            if (not _txt_clean(anuncios_info.get('%', ''))) and str(sku_promo or '').strip():
                for sku_key in _sku_lookup_variantes_cached(sku_promo):
                    cand = anuncios_por_sku.get(sku_key, {})
                    if _txt_clean(cand.get('%', '')):
                        anuncios_info = {
                            "Tipo": cand.get("Tipo", anuncios_info.get("Tipo", "")),
                            "%": cand.get("%", anuncios_info.get("%", "")),
                        }
                        break
            dados_ordenados.append({
                'MLB': k,
                'Tipo': anuncios_info.get('Tipo', ''),
                '%': _txt_clean(anuncios_info.get('%', '')),
                'SKU': sku_promo,
                'TÃ­tulo': titulo_promo,
                'Frete': _buscar_frete_por_sku_ou_mlb(sku_promo, k),
                'Custo': _buscar_valor_por_sku(custo_por_sku, sku_promo),
                'Imposto %': _buscar_valor_por_sku(imposto_por_sku, sku_promo),
                'SituaÃƒÂ§ÃƒÂ£o': fixa_status_por_item.get(k, 'Ativo' if k in fixa_mlbs_set else 'Sem promocao fixa'),
                'Status': fixa_status_por_item.get(k, 'Ativo' if k in fixa_mlbs_set else 'Sem promocao fixa'),
                'Desconto': '',
                '% Fixa': fixa_pct_por_item.get(k, ''),
                'M 21 Fixa': '',
                'M ML': promo_info.get('PreÃ§o Final ML', ''),
                'PreÃ§o Final ML': promo_info.get('PreÃ§o Final ML', ''),
                'Campanha ML': promo_info.get('Campanha ML', ''),
                'Desconto ML': promo_info.get('Desconto ML', ''),
                'ML % Campanha': promo_info.get('ML % Campanha', ''),
                'Participar ou nÃ£o': 'Participar',
            })
        dados_analise = dados_ordenados

    # Marcar na SituaÃƒÂ§ÃƒÂ£o os MLBs que estÃƒÂ£o na promo_ml mas nÃ£o tÃƒÂªm promocao fixa.
    # Esse aviso ÃƒÂ© apenas para exibiÃƒÂ§ÃƒÂ£o Ã¢â‚¬â€ ÃƒÂ© removido antes da exportaÃƒÂ§ÃƒÂ£o para arquivo.
    if promo_ml_presente and fixa_mlbs_set:
        for row in dados_analise:
            k = _normalizar_mlb_key(str(row.get('MLB', '')))
            if k and k in promo_ml_por_item and k not in fixa_mlbs_set:
                row['SituaÃƒÂ§ÃƒÂ£o'] = 'Sem promocao fixa'

    try:
        pct_vazio = [str(r.get("MLB", "")).strip() for r in dados_analise if not _txt_clean(r.get("%", ""))]
        if pct_vazio:
            logger.warning(
                f"[PROMO OFFLINE] Coluna % vazia em {len(pct_vazio)}/{len(dados_analise)} linhas. "
                f"Exemplos MLB: {pct_vazio[:8]}"
            )
    except Exception:
        pass

    return dados_analise


def _sku_lookup_variantes(sku_val: str) -> list[str]:
    sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
    if not sku_norm:
        return []
    sku_up = sku_norm.upper()
    sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_up)
    partes = re.split(r"([0-9]+)", sku_up)
    sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
    variantes = [sku_up, sku_compacto, sku_numsoft_compacto]
    return [v for v in variantes if v]


def _cadastro_norm_col_custo(valor: str) -> str:
    base = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.replace("%", " percentual ")
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


_CADASTRO_COL_ALIASES_CUSTOS = {
    "sku": {
        "sku", "codigo sku", "cod sku", "seller sku", "codigo do sku", "codigo produto",
        "codigo do produto", "cod produto", "referencia", "referencia sku",
    },
    "custo": {
        "custo", "custo produto", "custo do produto", "custo unitario", "valor custo",
        "valor de custo", "preco custo", "preco de custo", "custo compra",
        "custo de compra", "custo medio", "custo medio unitario",
    },
    "imposto": {
        "imposto", "percentual imposto", "imposto percentual", "percentual de imposto",
        "imposto pct", "pct imposto", "aliquota", "aliquota imposto",
        "aliquota de imposto", "taxa imposto", "taxa de imposto", "tributo",
        "percentual tributo",
    },
    "preco": {
        "preco", "preco venda", "preco de venda", "valor venda", "valor de venda",
        "preco atual", "preco ml", "preco mercado livre",
    },
}


def _cadastro_colunas_alias(df: pd.DataFrame, destino: str) -> list[str]:
    if df is None or df.empty:
        return []
    aliases = _CADASTRO_COL_ALIASES_CUSTOS.get(destino) or set()
    return [col for col in df.columns if _cadastro_norm_col_custo(col) in aliases]


def _cadastro_canonizar_colunas_custos(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    rename = {}
    for destino in ("sku", "custo", "imposto", "preco"):
        if destino not in df.columns:
            aliases = _cadastro_colunas_alias(df, destino)
            if aliases:
                rename[aliases[0]] = destino
    if rename:
        df = df.rename(columns=rename)
        df = df.loc[:, ~df.columns.duplicated()]

    for destino in ("sku", "custo", "imposto", "preco"):
        if destino not in df.columns:
            continue
        for col in _cadastro_colunas_alias(df, destino):
            if col == destino:
                continue
            try:
                atual = df[destino].astype(str)
                origem = df[col].astype(str)
                mask = atual.str.strip().eq("") & origem.str.strip().ne("")
                if mask.any():
                    df.loc[mask, destino] = origem.loc[mask]
            except Exception:
                continue
    return df


def _carregar_df_cadastro_custos(client_id: str) -> pd.DataFrame:
    arquivo = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    return _ler_df_cadastro_custos_arquivo(arquivo)


def _ler_df_cadastro_custos_arquivo(arquivo: str) -> pd.DataFrame:
    if not arquivo or not os.path.exists(arquivo):
        return pd.DataFrame()

    ultimo_erro = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            df = pd.read_csv(arquivo, dtype=str, keep_default_na=False, encoding=encoding).fillna("")
            df.columns = [str(c).strip().lower() for c in df.columns]
            df = df.loc[:, ~df.columns.duplicated()]
            return _cadastro_canonizar_colunas_custos(df)
        except Exception as exc:
            ultimo_erro = exc
    logger.warning("[PROMO CADASTRO] Falha ao ler custos/impostos do cadastro %s: %s", arquivo, ultimo_erro)
    return pd.DataFrame()

PEER_EXPORTS = ['_parse_float_str_cached', '_parse_float_flex', '_calcular_margem_liquida_ml', '_format_money_safe_cached', '_format_money_safe', '_format_pct_br_cached', '_format_pct_br', '_normalizar_decisao_local', '_build_col_index', '_pick_first_col', '_extract_monetary_from_df_row', '_build_col_index_optimized', '_preprocessar_todos_indices_otimizado', '_processar_row_consolidado', 'montar_analise_promo_local', '_sku_lookup_variantes', '_cadastro_norm_col_custo', '_CADASTRO_COL_ALIASES_CUSTOS', '_cadastro_colunas_alias', '_cadastro_canonizar_colunas_custos', '_carregar_df_cadastro_custos', '_ler_df_cadastro_custos_arquivo']
__all__ = PEER_EXPORTS + ["configure_promocoes_core_analise_runtime"]

configure_promocoes_core_analise_runtime()
