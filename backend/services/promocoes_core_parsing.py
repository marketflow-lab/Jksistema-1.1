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


def configure_promocoes_core_parsing_runtime(runtime_module=None, peers=None):
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


configure_promocoes_core_parsing_runtime()


logger = logging.getLogger("jk_sistema")


TAXAS_FILE = os.path.join(PASTA_INFO, "taxas_db.json")


DB_TAXAS_PADRAO = {
    'molduras de estereos': {'ClÃƒÂ¡ssico': 0.10, 'Premium': 0.17},
    'audio para veiculos': {'ClÃƒÂ¡ssico': 0.10, 'Premium': 0.17},
    'scanners': {'ClÃƒÂ¡ssico': 0.12, 'Premium': 0.18},
}


TAXA_PADRAO = {'ClÃƒÂ¡ssico': 0.12, 'Premium': 0.17}


COLUNAS_DO_MODELO_ANUNCIOS = [
    'Agrupador de variaÃƒÂ§ÃƒÂµes', 'CÃƒÂ³digo do anuncio', 'NÃƒÂºmero do produto', 'NÃƒÂºmero da variaÃƒÂ§ÃƒÂ£o',
    'SKU', 'TÃ­tulo', 'VariaÃƒÂ§ÃƒÂµes', 'PreÃƒÂ§o', 'Moeda', 'Seu preÃ§o competitivo em outros canais',
    'PreÃƒÂ§o de atacado 1 [ML]', 'Unnamed: 11', 'PreÃƒÂ§o de atacado 2 [ML]', 'Unnamed: 13',
    'PreÃƒÂ§o de atacado 3 [ML]', 'Unnamed: 15', 'PreÃƒÂ§o de atacado 4 [ML]', 'Unnamed: 17',
    'PreÃƒÂ§o de atacado 5 [ML]', 'Unnamed: 19', 'Forma de entrega', 'Tipo de anuncio',
    'Tarifa de venda', 'Categoria'
]


COLUNAS_DO_MODELO_PROMO = [
    'TITLE', 'ITEM_ID', 'SKU', 'ORIGINAL_PRICE', 'DISCOUNT_PERCENTAGE',
    'FINAL_PRICE', 'SALE_FEE', 'CANDIDATE_ID', 'SUGGESTION', 'RECEIVES', 'LOYALTY_DISCOUNT_PERCENTAGE',
    'LOYALTY_PRICE', 'LOYALTY_RECEIVES', 'STATUS', 'ACTION', 'ERRORS'
]


COLUNAS_PLANILHA_ANALISE_PROMO = [
    'Tipo', '%', 'SKU', 'TÃ­tulo', 'Frete', 'Frete ML', 'Frete GrÃ¡tis', 'Frete GrÃ¡tis ML',
    'Custo', 'Tarifa', 'Tarifa ML', 'MLB', 'Campanha ML', '% Fixa', 'ML % Campanha',
    'PreÃ§o Final', 'Imposto %', 'Imposto', 'Imposto Fixa', 'PreÃ§o Final ML', 'Imposto ML', 'Desconto ML',
    'Valor LÃ­quido', 'Valor lÃ­quido ML', 'Status', 'Margem', 'Margem ML', 'AÃ§Ã£o'
]


NOME_INFORMADO_POR_TIPO_PROMO = {
    'anuncios': 'AnÃƒÂºncios',
    'atributos': 'Custo',
    'promo_ml': 'Promo ML',
    'mercadoturbo': 'Frete',
    'fixa': 'Fixa',
    'promo': 'Promo',
    'desconhecido': 'Desconhecido',
}


def _limpar_nome_coluna(col):
    s = str(col or '').replace('\ufeff', '').strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def _extrair_percentual_de_formula_fee(formula_texto: str, tipo_anuncio: str = '') -> str:
    """Extrai percentual textual de fÃƒÂ³rmulas Excel comuns de FEE_PER_SALE.

    Exemplo tÃƒÂ­pico: =SE(INDIRETO("V454")="ClÃƒÂ¡ssico";"11%";SE(..."Premium";"14%";"-"))
    """
    txt = str(formula_texto or '').strip()
    if not txt:
        return ''
    if not txt.startswith('='):
        return txt

    tipo_norm = normalizar_texto(tipo_anuncio)

    def _fmt(v):
        s = str(v).replace('.', ',').strip()
        return f"{s}%" if s else ''

    # Caso mais comum: valores percentuais entre aspas.
    encontrados = re.findall(r'"\s*([0-9]+(?:[\.,][0-9]+)?)\s*%?\s*"', txt)
    if not encontrados:
        # Fallback para fÃƒÂ³rmulas que nÃ£o trazem o nÃƒÂºmero entre aspas.
        # Ex.: =IF(V6="ClÃƒÂ¡ssico",12,IF(V6="Premium",17,0))
        txt_norm = normalizar_texto(txt)
        m_classico = re.search(r'classic[oa]?.{0,60}?([0-9]+(?:[\.,][0-9]+)?)', txt_norm)
        m_premium = re.search(r'premium.{0,60}?([0-9]+(?:[\.,][0-9]+)?)', txt_norm)
        if 'premium' in tipo_norm and m_premium:
            return _fmt(m_premium.group(1))
        if ('classico' in tipo_norm or 'clÃƒÂ¡ssico' in tipo_norm) and m_classico:
            return _fmt(m_classico.group(1))
        if m_classico:
            return _fmt(m_classico.group(1))
        if m_premium:
            return _fmt(m_premium.group(1))
        return ''

    if 'premium' in tipo_norm:
        if len(encontrados) >= 2:
            return _fmt(encontrados[1])
        return _fmt(encontrados[-1])
    if 'classico' in tipo_norm or 'clÃƒÂ¡ssico' in tipo_norm:
        return _fmt(encontrados[0])
    return _fmt(encontrados[0])


def _materializar_fee_per_sale_como_valor(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simula o comportamento de "colar somente valores" (Ctrl+Shift+V) para a
    coluna de taxa do arquivo de AnÃƒÂºncios, convertendo fÃƒÂ³rmulas em valor textual.
    """
    if df is None or df.empty:
        return df

    def _txt(v):
        s = str(v if v is not None else "").strip()
        return "" if s.lower() in {"nan", "none", "null", "nat", "<na>"} else s

    def _col_to_idx(col_ref: str) -> int:
        col = str(col_ref or "").strip().upper()
        if not col or not re.match(r"^[A-Z]+$", col):
            return -1
        n = 0
        for ch in col:
            n = (n * 26) + (ord(ch) - ord("A") + 1)
        return n - 1

    def _resolver_ref_mesma_coluna(col_name: str, formula_txt: str, depth: int = 0) -> str:
        if depth > 8:
            return ""
        txt = _txt(formula_txt)
        if not txt.startswith("="):
            return txt
        if not col_name or col_name not in df.columns:
            return ""
        m = re.match(r"^\s*=\s*\$?[A-Za-z]+\$?([0-9]+)(?:\s*&.*)?\s*$", txt)
        if not m:
            return ""
        row_excel = int(m.group(1))
        header_row_0 = int((getattr(df, "attrs", {}) or {}).get("excel_header_row", 0) or 0)
        first_data_row_excel = header_row_0 + 2
        row_df = row_excel - first_data_row_excel
        if row_df < 0 or row_df >= len(df.index):
            return ""
        val = _txt(df.iloc[row_df][col_name])
        if val.startswith("="):
            return _resolver_ref_mesma_coluna(col_name, val, depth + 1)
        return val

    def _resolver_ref_letra_col(formula_txt: str, depth: int = 0) -> str:
        if depth > 8:
            return ""
        txt = _txt(formula_txt)
        if not txt.startswith("="):
            return txt
        m = re.match(r"^\s*=\s*\$?([A-Za-z]+)\$?([0-9]+)(?:\s*&.*)?\s*$", txt)
        if not m:
            return ""
        col_idx = _col_to_idx(m.group(1))
        row_excel = int(m.group(2))
        if col_idx < 0:
            return ""
        header_row_0 = int((getattr(df, "attrs", {}) or {}).get("excel_header_row", 0) or 0)
        first_data_row_excel = header_row_0 + 2
        row_df = row_excel - first_data_row_excel
        if row_df < 0 or row_df >= len(df.index) or col_idx >= len(df.columns):
            return ""
        val = _txt(df.iloc[row_df, col_idx])
        if val.startswith("="):
            return _resolver_ref_letra_col(val, depth + 1)
        return val

    def _normalizar_pct(v: str) -> str:
        t = _txt(v)
        if not t:
            return ""
        if "%" in t:
            n = _parse_float_flex(t)
            if n is not None:
                return _format_pct_br(n)
            return t
        n = _parse_float_flex(t)
        if n is None:
            return ""
        return _format_pct_br(n * 100.0) if n <= 1 else _format_pct_br(n)

    col_tipo = _pick_first_col(df, ["LISTING_TYPE", "Tipo de anuncio", "TIPO DE ANUNCIO"])
    col_fee = (
        ('__FEE_PER_SALE_ORIG' if '__FEE_PER_SALE_ORIG' in df.columns else None)
        or _pick_first_col(df, ["FEE_PER_SALE", "Tarifa de venda", "TARIFA DE VENDA", "SALE_FEE"])
    )
    if not col_fee:
        return df

    fee_formula_template = ""
    try:
        for raw in df[col_fee].tolist():
            s = _txt(raw)
            if s.startswith("=") and ("%" in s or "classico" in normalizar_texto(s) or "premium" in normalizar_texto(s)):
                fee_formula_template = s
                break
    except Exception:
        fee_formula_template = ""

    out_vals = []
    for _, row in df.iterrows():
        tipo = _txt(row.get(col_tipo, "")) if col_tipo else ""
        fee_raw = _txt(row.get(col_fee, ""))

        if tipo.startswith("=") and col_tipo:
            tipo_res = _resolver_ref_mesma_coluna(col_tipo, tipo) or _resolver_ref_letra_col(tipo)
            if tipo_res:
                tipo = tipo_res

        fee_val = ""
        if not fee_raw:
            fee_val = ""
        elif fee_raw.startswith("="):
            # FÃƒÂ³rmula IF/SE com percentuais embutidos.
            fee_val = _extrair_percentual_de_formula_fee(fee_raw, tipo)
            if not fee_val:
                # ReferÃƒÂªncia de cÃƒÂ©lula (ex.: =W16).
                ref_val = _resolver_ref_mesma_coluna(col_fee, fee_raw) or _resolver_ref_letra_col(fee_raw)
                if ref_val:
                    if ref_val.startswith("="):
                        fee_val = _extrair_percentual_de_formula_fee(ref_val, tipo)
                    if not fee_val:
                        fee_val = _normalizar_pct(ref_val)
            if not fee_val and fee_formula_template:
                fee_val = _extrair_percentual_de_formula_fee(fee_formula_template, tipo)
        else:
            fee_val = _normalizar_pct(fee_raw)

        out_vals.append(_normalizar_pct(fee_val))

    df[col_fee] = out_vals
    return df


def _detectar_tipo_por_nome_informado(filename_hint=''):
    hint = normalizar_texto(filename_hint or '')
    if not hint:
        return ''

    # Prioridade alta para frete/mercadoturbo: alguns arquivos tÃƒÂªm "anuncios"
    # no nome e nÃ£o podem ser classificados como arquivo de anuncios.
    if 'frete' in hint or 'mercadoturbo' in hint:
        return 'mercadoturbo'
    if 'anuncio' in hint:
        return 'anuncios'
    if 'custo' in hint or 'atributo' in hint:
        return 'atributos'
    if 'promo ml' in hint or 'promo_ml' in hint or ('promo' in hint and 'ml' in hint):
        return 'promo_ml'
    if 'fixa' in hint:
        return 'fixa'
    if 'promo' in hint:
        return 'promo'
    return ''


def _detectar_tipo_dataframe(df, filename_hint=''):
    if df is None or df.empty:
        return 'desconhecido'

    cols = {normalizar_texto(_limpar_nome_coluna(c)) for c in list(df.columns)}
    hint = normalizar_texto(filename_hint or '')

    keys_anuncios = {
        'item_id', 'family_id', 'variation_id', 'product_number',
        'tipo de anuncio', 'tarifa de venda', 'categoria'
    }
    keys_promo = {
        'discount_percentage', 'loyalty_discount_percentage', 'final_price',
        'loyalty_price', 'status', 'action', 'errors', 'candidate_id', 'suggestion'
    }
    keys_mercadoturbo = {
        'id anuncio', 'id variacao', 'titulo do anuncio', 'disponiveis',
        'gtin', 'preco original', 'custo frete', 'modalidade', 'tipo envio'
    }
    keys_atributos = {'custo', 'imposto', 'sku'}

    score_anuncios = len(cols & keys_anuncios)
    score_promo = len(cols & keys_promo)
    score_mercadoturbo = len(cols & keys_mercadoturbo)
    score_atributos = len(cols & keys_atributos)

    # DetecÃƒÂ§ÃƒÂ£o por estrutura (preferencial): tolera pequenas variaÃƒÂ§ÃƒÂµes de coluna.
    if score_atributos >= 2 and ('sku' in cols) and ('custo' in cols or 'imposto' in cols):
        return 'atributos'
    if score_mercadoturbo >= 3:
        return 'mercadoturbo'
    if score_promo >= 2:
        return 'promo'
    if score_anuncios >= 2:
        return 'anuncios'

    # fallback por nome informado, apenas quando estrutura for insuficiente
    tipo_nomeado = _detectar_tipo_por_nome_informado(hint)
    if tipo_nomeado:
        return tipo_nomeado
    return 'desconhecido'


def _detectar_subtipo_promo(df):
    if df is None or df.empty:
        return ''

    cols_norm = {normalizar_texto(_limpar_nome_coluna(c)): c for c in list(df.columns)}

    def _non_empty_count(alias_list):
        col_real = None
        for a in alias_list:
            k = normalizar_texto(a)
            if k in cols_norm:
                col_real = cols_norm[k]
                break
        if col_real is None:
            return 0
        s = df[col_real].astype(str).str.strip().str.lower()
        # Ignora placeholders comuns de template para medir preenchimento real.
        invalid = {'', 'nan', 'none', 'null'}
        return int((~s.isin(invalid)).sum())

    # Assinaturas de Promo ML (fee/campanha).
    n_sale_fee = _non_empty_count(['sale_fee', 'desconto ml', 'reducao nas suas tarifas de venda'])
    n_candidate = _non_empty_count(['candidate_id'])

    # Assinaturas de Fixa (campos loyalty/suggestion realmente preenchidos).
    n_suggestion = _non_empty_count(['suggestion'])
    n_receives = _non_empty_count(['receives'])
    n_loyalty_disc = _non_empty_count(['loyalty_discount_percentage'])
    n_loyalty_price = _non_empty_count(['loyalty_price'])
    n_loyalty_receives = _non_empty_count(['loyalty_receives'])
    fixa_signature = n_suggestion + n_receives + n_loyalty_disc + n_loyalty_price + n_loyalty_receives

    # Regra de desempate: Promo ML ganha quando hÃƒÂ¡ fee/candidato preenchidos.
    if (n_sale_fee + n_candidate) > 0:
        return 'promo_ml'
    if fixa_signature > 0:
        return 'fixa'

    # Fallback por existÃƒÂªncia de colunas para casos de baixa qualidade de dados.
    keys_fixa = {
        'suggestion', 'receives', 'loyalty_discount_percentage',
        'loyalty_price', 'loyalty_receives'
    }
    keys_promo_ml = {'sale_fee', 'candidate_id'}
    cols = set(cols_norm.keys())
    if len(cols & keys_promo_ml) >= 1:
        return 'promo_ml'
    if len(cols & keys_fixa) >= 2:
        return 'fixa'

    return 'promo'


def _rotulo_tipo_arquivo(tipo_detectado):
    mapa = {
        'anuncios': 'AnÃƒÂºncios',
        'mercadoturbo': 'Frete',
        'atributos': 'Custo',
        'promo_ml': 'Promo ML',
        'fixa': 'Fixa',
        'promo': 'Promo',
        'desconhecido': 'Desconhecido',
    }
    return mapa.get(tipo_detectado, 'Desconhecido')


def _nome_informado_tipo_arquivo(tipo_detectado):
    return NOME_INFORMADO_POR_TIPO_PROMO.get(tipo_detectado, 'Desconhecido')


def _escolher_aba_excel_por_estrutura(xls, aba_alvo=None):
    abas = list(xls.sheet_names)
    if not abas:
        return 0

    # Prioriza aba pedida explicitamente
    if aba_alvo:
        alvo = normalizar_texto(aba_alvo)
        for a in abas:
            if normalizar_texto(a) == alvo:
                return a
        for a in abas:
            if alvo in normalizar_texto(a):
                return a

    melhor_aba = abas[0]
    melhor_score = -1
    for a in abas:
        try:
            prev = pd.read_excel(xls, sheet_name=a, header=0, nrows=5, dtype=str)
            prev.columns = [_limpar_nome_coluna(c) for c in prev.columns]
            tipo = _detectar_tipo_dataframe(prev)
            score_map = {
                'anuncios': 5,
                'promo': 5,
                'mercadoturbo': 5,
                'atributos': 4,
                'desconhecido': 0,
            }
            score = score_map.get(tipo, 0)
            if score > melhor_score:
                melhor_score = score
                melhor_aba = a
        except Exception:
            continue

    return melhor_aba


def carregar_id_planilha_sistema(client_id: str = None):
    logger.info(f"[PROMO] Carregando planilha para client_id='{client_id}'")
    
    # Primeiro tenta config por cliente (multi-tenant)
    if client_id:
        try:
            tenant_path = get_tenant_path(client_id)
            tenant_conf = os.path.join(tenant_path, 'config_sheet.json')
            logger.info(f"[PROMO] Verificando arquivo: {tenant_conf}")
            
            if os.path.exists(tenant_conf):
                with open(tenant_conf, 'r') as f:
                    data = json.load(f)
                    sheet_id = data.get('spreadsheet_id', '').strip()
                    if sheet_id:
                        logger.info(f"[PROMO] Ã¢Å“â€¦ Planilha do cliente encontrada: {sheet_id}")
                        return sheet_id
                    else:
                        logger.warning(f"[PROMO] Arquivo existe mas 'spreadsheet_id' estÃƒÂ¡ vazio")
            else:
                logger.warning(f"[PROMO] Arquivo de config do cliente NÃƒO existe: {tenant_conf}")
        except Exception as e:
            logger.error(f"[PROMO] Erro ao carregar config do cliente: {e}")

    logger.warning("[PROMO] Nenhuma planilha vinculada ao usuÃƒÂ¡rio logado foi encontrada.")
    return None


def carregar_taxas():
    if os.path.exists(TAXAS_FILE):
        try:
            with open(TAXAS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return DB_TAXAS_PADRAO.copy()


def salvar_taxas(taxas):
    try:
        with open(TAXAS_FILE, 'w', encoding='utf-8') as f:
            json.dump(taxas, f, indent=4, ensure_ascii=False)
    except: pass


def _normalizar_texto_str(s: str) -> str:
    """Cache de normalizaÃƒÂ§ÃƒÂ£o de texto para strings. Evita recomputar valores repetidos."""
    return unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII').lower().strip()


def normalizar_texto(texto):
    if not isinstance(texto, str):
        return _normalizar_texto_str(str(texto))
    return _normalizar_texto_str(texto)


def renomear_colunas_duplicadas(df):
    if df.empty: return df
    cols = pd.Series(df.columns)
    for dup in cols[cols.duplicated()].unique(): 
        cols[cols[cols == dup].index.values.tolist()] = [dup + '.' + str(i) if i != 0 else dup for i in range(sum(cols == dup))] 
    df.columns = cols
    return df


def formatar_moeda_br(valor):
    try:
        s = str(valor).replace("R$", "").strip()
        if not s: return ""
        if '.' in s and ',' not in s: val = float(s)
        elif ',' in s and '.' not in s: val = float(s.replace(',', '.'))
        else: val = float(s.replace(',', ''))
        s_fmt = f"{val:,.2f}" 
        s_fmt = "R$ " + s_fmt.replace(',', 'X').replace('.', ',').replace('X', '.') 
        return s_fmt
    except:
        return str(valor).replace('.', ',')


def obter_taxa_por_categoria(tipo_anuncio, nome_categoria, db_taxas):
    if not isinstance(tipo_anuncio, str): return 0.0
    tipo_norm = normalizar_texto(tipo_anuncio)
    cat_norm = normalizar_texto(nome_categoria)
    chave_tipo = None
    if 'classico' in tipo_norm: chave_tipo = 'ClÃƒÂ¡ssico'
    elif 'premium' in tipo_norm: chave_tipo = 'Premium'
    if not chave_tipo: return 0.0
    taxas_selecionadas = TAXA_PADRAO
    for termo, taxas in db_taxas.items():
        if termo in cat_norm:
            taxas_selecionadas = taxas
            break 
    
    val = taxas_selecionadas.get(chave_tipo, 0.0)
    try:
        if isinstance(val, str):
            val = float(val.replace(',', '.').replace('%', '').strip())
        val = float(val)
        if val > 1.0: val = val / 100.0
        return val
    except:
        return 0.0


def normalizar_df_anuncios(df):
    if df is None or df.empty: return df
    termos_intrusos = ["impostos incluidos", "tax_inclusion_type"]
    colunas_para_remover = []
    for col in df.columns:
        col_norm = normalizar_texto(col)
        if any(termo in col_norm for termo in termos_intrusos):
            colunas_para_remover.append(col)
    if colunas_para_remover:
        df = df.drop(columns=colunas_para_remover)
    if len(df.columns) == len(COLUNAS_DO_MODELO_ANUNCIOS):
        df.columns = COLUNAS_DO_MODELO_ANUNCIOS
    return df


def normalizar_df_promo(df):
    if df is None or df.empty: return df

    # Mapeia cabeÃƒÂ§alhos comuns (PT/variaÃƒÂ§ÃƒÂµes) para o modelo esperado.
    mapa_colunas = {
        'titulo do anuncio': 'TITLE',
        'titulo': 'TITLE',
        'title': 'TITLE',
        'numero do anuncio': 'ITEM_ID',
        'id anuncio': 'ITEM_ID',
        'item_id': 'ITEM_ID',
        'mlb': 'ITEM_ID',
        'sku': 'SKU',
        'preco original': 'ORIGINAL_PRICE',
        'preco final': 'FINAL_PRICE',
        'final_price': 'FINAL_PRICE',
        'final_pricefinal_price': 'FINAL_PRICE',
        'reducao nas suas tarifas de venda': 'SALE_FEE',
        'sale_fee': 'SALE_FEE',
        'desconto total': 'DISCOUNT_PERCENTAGE',
        'discount_percentage': 'DISCOUNT_PERCENTAGE',
        'status': 'STATUS',
        'acao': 'ACTION',
        'aÃƒÂ§ÃƒÂ£o': 'ACTION',
        'errors': 'ERRORS',
    }

    ren = {}
    for c in list(df.columns):
        c_norm = normalizar_texto(_limpar_nome_coluna(c))
        if c_norm in mapa_colunas:
            ren[c] = mapa_colunas[c_norm]
    if ren:
        df = df.rename(columns=ren)

    for col in COLUNAS_DO_MODELO_PROMO:
        if col not in df.columns:
            df[col] = "" 
    df = df[COLUNAS_DO_MODELO_PROMO]
    return df


def ler_e_tratar_arquivo(file_bytes, filename, aba_alvo=None):
    """LÃƒÆ’Ã‚Âª arquivo Excel/CSV/HTML a partir de bytes com detecÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o inteligente de cabeÃƒÆ’Ã‚Â§alho e fallback"""
    df = pd.DataFrame()
    excel_sheet_used = None
    excel_header_row = 0
    
    # 1. Tenta ler como Excel (se a extensÃƒÆ’Ã‚Â£o indicar)
    if filename.endswith(('.xlsx', '.xls')):
        try:
            with io.BytesIO(file_bytes) as b:
                xls = pd.ExcelFile(b)
                aba_ler = _escolher_aba_excel_por_estrutura(xls, aba_alvo)

                # Leitura em passada ÃƒÂºnica: evita reler a mesma aba duas vezes.
                df_raw = pd.read_excel(xls, sheet_name=aba_ler, header=None, dtype=str)
                header_row = 0
                preview_rows = min(30, len(df_raw.index))
                for i in range(preview_rows):
                    row_vals = df_raw.iloc[i].astype(str).tolist()
                    s_norm = [normalizar_texto(x) for x in row_vals]
                    has_sku = any("sku" in x for x in s_norm)
                    has_keywords = (
                        any("tarifa" in x for x in s_norm) or
                        any("tipo" in x for x in s_norm) or
                        any("preco" in x for x in s_norm) or
                        any("price" in x for x in s_norm) or
                        any("status" in x for x in s_norm) or
                        any("original_price" in x for x in s_norm)
                    )
                    if has_sku and has_keywords:
                        header_row = i
                        break

                if not df_raw.empty:
                    header_vals = [
                        _limpar_nome_coluna("" if pd.isna(v) else v)
                        for v in df_raw.iloc[header_row].tolist()
                    ]
                    header_vals = [
                        h if h else f"col_{idx}"
                        for idx, h in enumerate(header_vals, start=1)
                    ]
                    df = df_raw.iloc[header_row + 1:].copy().reset_index(drop=True)
                    df.columns = header_vals
                else:
                    df = pd.DataFrame()

                excel_sheet_used = aba_ler
                excel_header_row = header_row
        except Exception as e:
            print(f"Aviso: Falha ao ler {filename} como Excel: {e}")

    # 2. Se falhar ou nÃƒÆ’Ã‚Â£o for Excel, tenta CSV/Texto (Fallback robusto)
    if df.empty:
        # Lista de tentativas (Separador, Encoding)
        tentativas = [
            (',', 'utf-8'), (';', 'utf-8'), ('\t', 'utf-8'),
            (',', 'latin1'), (';', 'latin1'), ('\t', 'latin1'),
            (',', 'cp1252'), (';', 'cp1252'), ('\t', 'cp1252'),
            (',', 'utf-8-sig'), (';', 'utf-8-sig') # Adicionado BOM support
        ]
        
        # Tenta detectar cabeÃƒÆ’Ã‚Â§alho primeiro
        header_row = 0
        try:
            with io.BytesIO(file_bytes) as b:
                # LÃƒÆ’Ã‚Âª as primeiras linhas para achar o cabeÃƒÆ’Ã‚Â§alho
                for line_idx, line in enumerate(b):
                    if line_idx > 30: break
                    try:
                        line_str = line.decode('latin1', errors='ignore').lower()
                        if "sku" in line_str:
                            header_row = line_idx
                            break
                    except: pass
        except: pass

        for sep, enc in tentativas:
            try:
                with io.BytesIO(file_bytes) as b:
                    df_temp = pd.read_csv(b, sep=sep, header=header_row, dtype=str, encoding=enc, on_bad_lines='skip')
                
                if not df_temp.empty and len(df_temp.columns) > 1:
                    df = df_temp
                    break
            except:
                continue

    # 3. Fallback para HTML (arquivos .xls/.xlsx que sÃƒÆ’Ã‚Â£o HTML)
    # Removida restriÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o de extensÃƒÆ’Ã‚Â£o para tentar HTML em qualquer arquivo que falhou antes
    if df.empty:
        try:
            with io.BytesIO(file_bytes) as b:
                dfs = pd.read_html(b, header=0)
                if dfs:
                    df = dfs[0].astype(str)
        except Exception as e:
            # Apenas loga se for uma extensÃƒÆ’Ã‚Â£o que deveria ter funcionado
            if filename.endswith(('.xls', '.xlsx')):
                print(f"Aviso: Falha ao ler {filename} como HTML: {e}")

    if not df.empty:
        try:
            df.attrs["excel_sheet_used"] = excel_sheet_used
            df.attrs["excel_header_row"] = int(excel_header_row or 0)
        except Exception:
            pass

        # Remove espaÃƒÆ’Ã‚Â§os em branco dos nomes das colunas
        df.columns = [_limpar_nome_coluna(c) for c in df.columns]
        
        # --- LÃƒÆ’Ã¢â‚¬Å“GICA ESPECÃƒÆ’Ã‚ÂFICA POR TIPO DE ARQUIVO ---
        filename_norm = normalizar_texto(filename)
        tipo_df = _detectar_tipo_dataframe(df, filename_norm)

        if tipo_df == 'anuncios':
            if "mercadoturbo" not in filename_norm:
                df = normalizar_df_anuncios(df)
            df = renomear_colunas_duplicadas(df)

            # Preserva a taxa original do arquivo (FEE_PER_SALE) para a Analise de Promocao.
            # A coluna "Tarifa de venda" pode ser recalculada mais abaixo para outras rotinas,
            # mas a coluna % da analise deve refletir o valor original da planilha enviada.
            col_fee_orig = None
            for c in list(df.columns):
                if normalizar_texto(_limpar_nome_coluna(c)) == 'fee_per_sale':
                    col_fee_orig = c
                    break
            if col_fee_orig is not None:
                df['__FEE_PER_SALE_ORIG'] = df[col_fee_orig]

            # Modo teste solicitado: materializa FEE_PER_SALE em valor final
            # (equivalente a "colar somente valores" na coluna %).
            try:
                df = _materializar_fee_per_sale_como_valor(df)
            except Exception:
                logger.exception("[PROMO OFFLINE] Falha ao materializar FEE_PER_SALE como valor")

            # Preenche lacunas da taxa original com a fÃƒÂ³rmula bruta lida da planilha,
            # apenas quando realmente necessÃƒÂ¡rio (evita custo alto em todos os arquivos).
            fee_formula_raw = []
            needs_formula_fill = '__FEE_PER_SALE_ORIG' not in df.columns
            if not needs_formula_fill:
                serie_fee = df['__FEE_PER_SALE_ORIG'].astype(str).str.strip().str.lower()
                # FÃƒÂ³rmulas sem cache calculado podem aparecer como vazio/0/-.
                # Nestes casos, precisamos ler a fÃƒÂ³rmula bruta para recuperar a taxa real.
                invalid_markers = {'', 'nan', 'none', 'null', 'nat', '<na>', '-', '0', '0.0', '0,0'}
                needs_formula_fill = bool(serie_fee.isin(invalid_markers).any())

            if (
                needs_formula_fill
                and filename.endswith(('.xlsx', '.xls'))
                and excel_sheet_used is not None
                and len(df.index) > 0
            ):
                try:
                    wb_formula = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=False, read_only=True)
                    ws_formula = wb_formula[excel_sheet_used] if excel_sheet_used in wb_formula.sheetnames else wb_formula[wb_formula.sheetnames[0]]

                    header_excel_row = int(excel_header_row) + 1
                    header_vals = []
                    for cell in ws_formula[header_excel_row]:
                        header_vals.append(_limpar_nome_coluna(cell.value))

                    fee_col_idx = None
                    aliases_fee = {
                        'fee_per_sale', 'tarifa de venda', 'tarifa venda', 'tarifa',
                        'comissao', 'comissÃƒÂ£o', 'sale_fee'
                    }
                    for idx_h, h in enumerate(header_vals, start=1):
                        if normalizar_texto(h) in aliases_fee:
                            fee_col_idx = idx_h
                            break

                    if fee_col_idx is not None:
                        inicio_dados = header_excel_row + 1
                        for row_num in range(inicio_dados, inicio_dados + len(df.index)):
                            v = ws_formula.cell(row=row_num, column=fee_col_idx).value
                            fee_formula_raw.append('' if v is None else str(v))
                except Exception:
                    fee_formula_raw = []

            if fee_formula_raw and len(fee_formula_raw) == len(df.index):
                if '__FEE_PER_SALE_ORIG' not in df.columns:
                    df['__FEE_PER_SALE_ORIG'] = [''] * len(df.index)

                def _is_blank_fee(v):
                    s = str(v if v is not None else '').strip().lower()
                    if s in {'', 'nan', 'none', 'null', 'nat', '<na>', '-', '0', '0.0', '0,0'}:
                        return True
                    # Sem sÃƒÂ­mbolo de % e com valor <= 0 tambÃƒÂ©m ÃƒÂ© invalido para taxa.
                    if '%' not in s:
                        n = _parse_float_flex(s)
                        if n is not None and n <= 0:
                            return True
                    return False

                fee_atual = list(df['__FEE_PER_SALE_ORIG'])
                fee_final = []
                for i, cur in enumerate(fee_atual):
                    fee_final.append(fee_formula_raw[i] if _is_blank_fee(cur) else cur)
                df['__FEE_PER_SALE_ORIG'] = fee_final

            mapa = {'FEE_PER_SALE': 'Tarifa de venda','LISTING_TYPE': 'Tipo de anuncio','CATEGORY': 'Categoria', 'CATEGORY_ID': 'Categoria'}
            df.rename(columns=mapa, inplace=True)
            
            # --- APRENDIZADO DE TAXAS (CriaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o do Banco de Dados) ---
            db_taxas = carregar_taxas()
            if 'Tarifa de venda' in df.columns and 'PreÃƒÂ§o' in df.columns and 'Categoria' in df.columns and 'Tipo de anuncio' in df.columns:
                taxas_atualizadas = False
                for _, row in df.iterrows():
                    try:
                        cat = normalizar_texto(str(row['Categoria']))
                        tipo = normalizar_texto(str(row['Tipo de anuncio']))
                        
                        chave_tipo = None
                        if 'classico' in tipo: chave_tipo = 'ClÃƒÂ¡ssico'
                        elif 'premium' in tipo: chave_tipo = 'Premium'
                        
                        if chave_tipo and cat:
                            # Parse simples de valores monetÃƒÆ’Ã‚Â¡rios
                            def parse_val(s):
                                s = str(s).replace('R$', '').strip()
                                if not s: return 0.0
                                if ',' in s and '.' in s: return float(s.replace('.','').replace(',','.'))
                                if ',' in s: return float(s.replace(',','.'))
                                return float(s) if s else 0.0
                            
                            preco = abs(parse_val(row['PreÃƒÂ§o']))
                            tarifa_val = str(row['Tarifa de venda'])
                            tarifa = 0.0
                            if '%' in tarifa_val:
                                tarifa = (parse_val(tarifa_val.replace('%', '')) / 100.0) * preco
                            else:
                                tarifa = abs(parse_val(tarifa_val))
                            
                            if preco > 0 and tarifa > 0:
                                rate = round(tarifa / preco, 4)
                                if cat not in db_taxas: db_taxas[cat] = TAXA_PADRAO.copy()
                                db_taxas[cat][chave_tipo] = rate
                                taxas_atualizadas = True
                    except: pass
                
                if taxas_atualizadas: salvar_taxas(db_taxas)

            # Calculo de Tarifa (crucial para a planilha funcionar)
            if 'Tipo de anuncio' in df.columns and 'Categoria' in df.columns:
                # Preenchimento de tipo vazio
                df['Tipo de anuncio'] = df['Tipo de anuncio'].replace(r'^\s*$', np.nan, regex=True).ffill().fillna('')
                
                def calcula_tarifa_real(row):
                    tipo = str(row.get('Tipo de anuncio', ''))
                    cat = str(row.get('Categoria', ''))
                    taxa = obter_taxa_por_categoria(tipo, cat, db_taxas)
                    if taxa == 0: return "-"
                    val_pct = taxa * 100
                    # Verifica se e inteiro (ex: 12.0 -> True)
                    return f"{int(val_pct)}%" if val_pct.is_integer() else f"{val_pct:.1f}%".replace('.', ',')
                df['Tarifa de venda'] = df.apply(calcula_tarifa_real, axis=1)

        elif tipo_df == 'promo':
            df = normalizar_df_promo(df)

    return df


def preparar_para_sheets(df):
    if df.empty: return df
    df = df.dropna(how='all')
    df = df.replace([np.nan, np.inf, -np.inf, None], "")
    df = df.fillna("")
    df = df.astype(str)
    # Formata nÃƒÆ’Ã‚Âºmeros para padrÃƒÆ’Ã‚Â£o BR (vÃƒÆ’Ã‚Â­rgula decimal) para o Sheets reconhecer
    def formatar_valor(val):
        s = str(val).strip()
        if re.match(r'^[\d\.]+$', s) and '.' in s:
            parts = s.rsplit('.', 1)
            if len(parts) == 2: return f"{parts[0]},{parts[1]}"
        return s
    for col in df.columns: df[col] = df[col].apply(formatar_valor)
    return df


def atualizar_aba(sh, nome_aba, df, value_input_option='USER_ENTERED'):
    if df is None or df.empty: 
        print(f"ÃƒÂ¢Ã…Â¡Ã‚Â ÃƒÂ¯Ã‚Â¸Ã‚Â Aba '{nome_aba}' ignorada (DataFrame vazio).")
        return
    try:
        try: ws = sh.worksheet(nome_aba)
        except: ws = sh.add_worksheet(title=nome_aba, rows="1000", cols="20")
        dados = [df.columns.values.tolist()] + df.astype(str).values.tolist()
        num_rows = len(dados)
        num_cols = len(dados[0]) if num_rows > 0 else 0

        if num_rows > 0 and num_cols > 0:
            if str(nome_aba).strip().lower() == "ml promo":
                # MantÃ©m as 6 primeiras linhas e limpa o restante antes de colar.
                max_rows = max(getattr(ws, "row_count", 1000), 7)
                ws.batch_clear([f'A7:ZZ{max_rows}'])
            else:
                end_col = openpyxl.utils.get_column_letter(num_cols)
                range_to_clear = f'A1:{end_col}{num_rows + 50}'
                ws.batch_clear([range_to_clear])

            ws.update('A1', dados, value_input_option=value_input_option)
        else:
            ws.batch_clear(['A1:Z1000'])
        print(f"ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Aba '{nome_aba}' atualizada com sucesso ({len(df)} linhas).")
    except Exception:
        try:
            if str(nome_aba).strip().lower() == "ml promo":
                max_rows = max(getattr(ws, "row_count", 1000), 7)
                ws.batch_clear([f'A7:ZZ{max_rows}'])
            else:
                ws.clear()
            dados = [df.columns.values.tolist()] + df.astype(str).values.tolist()
            ws.update('A1', dados, value_input_option=value_input_option)
        except Exception as e_inner:
            print(f"Erro atualizar aba {nome_aba}: {e_inner}")


def _normalize_log_text(text):
    if text is None:
        return ""
    fixed = str(text)
    try:
        fixed = fixed.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        pass
    fixed = unicodedata.normalize('NFKD', fixed)
    return "".join([c for c in fixed if not unicodedata.combining(c)])


def carregar_dados_analise(sh):
    try:
        ws = None
        for sheet_name in ("Analise", "An\u00e1lise"):
            try:
                ws = sh.worksheet(sheet_name)
                break
            except Exception:
                continue
        if ws is None:
            print("Erro ler analise: Aba nao encontrada (Analise/An\u00e1lise).")
            return []
        dados_brutos = ws.get_all_values()
        if not dados_brutos: return []
        
        # Simplesmente retorna os dados brutos para o frontend processar/exibir
        # Ou processa minimamente aqui
        colunas_necessarias = 27
        dados_normalizados = []
        for row in dados_brutos:
            while len(row) < colunas_necessarias: row.append("")
            dados_normalizados.append(row)
            
        df = pd.DataFrame(dados_normalizados)
        # LÃƒÆ’Ã‚Â³gica de extraÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o das colunas especÃƒÆ’Ã‚Â­ficas (baseado no promo.py)
        col_indices = [12, 2, 3, 22, 19, 23, 24, 25] 
        df_analise = df.iloc[3:, col_indices].copy()
        df_analise.columns = ["MLB", "SKU", "TÃ­tulo", "SituaÃƒÂ§ÃƒÂ£o", "Desconto", "M 21 Fixa", "M ML", "Participar ou nÃ£o"]
        
        # Filtrar apenas linhas MLB
        df_analise = df_analise[df_analise['MLB'].str.strip().str.upper().str.startswith('MLB')]
        
        def padronizar_decisao(valor):
            if valor is None: return ""
            # Trata NaN do pandas/numpy se passar despercebido
            if isinstance(valor, float) and np.isnan(valor): return ""
            
            v_limpo = str(valor).strip().lower()
            if not v_limpo: return ""
            
            # Prioridade para deteccao negativa
            if "nÃ£o" in v_limpo or "nao" in v_limpo: return "NÃ£o participar"
            if "participar" in v_limpo: return "Participar"
            return str(valor).strip()
            
        df_analise['Participar ou nÃ£o'] = df_analise['Participar ou nÃ£o'].apply(padronizar_decisao)
        return df_analise.to_dict(orient="records")
    except Exception as e:
        print(f"Erro ler analise: {_normalize_log_text(e)}")
        return []

PEER_EXPORTS = ['logger', 'TAXAS_FILE', 'DB_TAXAS_PADRAO', 'TAXA_PADRAO', 'COLUNAS_DO_MODELO_ANUNCIOS', 'COLUNAS_DO_MODELO_PROMO', 'COLUNAS_PLANILHA_ANALISE_PROMO', 'NOME_INFORMADO_POR_TIPO_PROMO', '_limpar_nome_coluna', '_extrair_percentual_de_formula_fee', '_materializar_fee_per_sale_como_valor', '_detectar_tipo_por_nome_informado', '_detectar_tipo_dataframe', '_detectar_subtipo_promo', '_rotulo_tipo_arquivo', '_nome_informado_tipo_arquivo', '_escolher_aba_excel_por_estrutura', 'carregar_id_planilha_sistema', 'carregar_taxas', 'salvar_taxas', '_normalizar_texto_str', 'normalizar_texto', 'renomear_colunas_duplicadas', 'formatar_moeda_br', 'obter_taxa_por_categoria', 'normalizar_df_anuncios', 'normalizar_df_promo', 'ler_e_tratar_arquivo', 'preparar_para_sheets', 'atualizar_aba', '_normalize_log_text', 'carregar_dados_analise']
__all__ = PEER_EXPORTS + ["configure_promocoes_core_parsing_runtime"]

configure_promocoes_core_parsing_runtime()
