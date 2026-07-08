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
from backend.services.cadastro_custos import (
    _cadastro_custos_lojas_path,
    _cadastro_mapa_custos_lojas,
    _cadastro_norm_loja_custo,
)
from backend.services.promocoes_common import *


def configure_promocoes_core_custos_runtime(runtime_module=None, peers=None):
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


configure_promocoes_core_custos_runtime()


def _listar_arquivos_cadastro_custos(client_id: str, incluir_custos_lojas: bool = True) -> list[str]:
    principal = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    caminhos = []
    vistos = set()

    def adicionar(caminho: str):
        if not caminho:
            return
        try:
            real = os.path.abspath(caminho)
        except Exception:
            real = caminho
        if real in vistos or not os.path.exists(real):
            return
        vistos.add(real)
        caminhos.append(real)

    adicionar(principal)
    if incluir_custos_lojas:
        try:
            adicionar(_cadastro_custos_lojas_path(client_id))
        except Exception:
            pass
    pastas = []
    try:
        pastas.append(get_tenant_path(client_id))
    except Exception:
        pass
    pastas.append(os.path.join(PASTA_INFO, "default"))

    candidatos = []
    for pasta in pastas:
        if not pasta or not os.path.isdir(pasta):
            continue
        try:
            for nome in os.listdir(pasta):
                nome_lower = nome.lower()
                if not nome_lower.startswith("cadastro_produtos"):
                    continue
                if "backup" not in nome_lower and nome_lower != "cadastro_produtos.csv":
                    continue
                caminho = os.path.join(pasta, nome)
                if not os.path.isfile(caminho):
                    continue
                try:
                    mtime = os.path.getmtime(caminho)
                except Exception:
                    mtime = 0
                candidatos.append((mtime, caminho))
        except Exception:
            continue

    for _mtime, caminho in sorted(candidatos, reverse=True):
        adicionar(caminho)
    return caminhos


def _iterar_dfs_cadastro_custos(client_id: str, incluir_custos_lojas: bool = True):
    for caminho in _listar_arquivos_cadastro_custos(client_id, incluir_custos_lojas=incluir_custos_lojas):
        df = _ler_df_cadastro_custos_arquivo(caminho)
        if df is None or df.empty:
            continue
        yield df, caminho


def _carregar_custos_cadastro_por_sku(client_id: str, incluir_custos_lojas: bool = True) -> dict:
    mapa = {}
    chaves_exatas = set()
    fontes_usadas = []

    for df, caminho in _iterar_dfs_cadastro_custos(client_id, incluir_custos_lojas=incluir_custos_lojas):
        if "sku" not in df.columns:
            continue
        col_custo = "custo" if "custo" in df.columns else None
        if not col_custo:
            continue

        antes = len(mapa)
        for _, row in df.iterrows():
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            custo = _parse_float_flex(row.get(col_custo, ""))
            if custo is None:
                continue
            chave_exata = _normalizar_sku_mes(sku).upper()
            if chave_exata and chave_exata not in chaves_exatas:
                mapa[chave_exata] = float(custo)
                chaves_exatas.add(chave_exata)
            for key in _sku_lookup_variantes(sku):
                if not key or key == chave_exata or key in chaves_exatas:
                    continue
                mapa.setdefault(key, float(custo))
        if len(mapa) > antes:
            fontes_usadas.append(os.path.basename(caminho))

    if mapa:
        logger.info(
            "[PROMO CADASTRO] Custos carregados: %s SKU-chave(s) | fontes: %s",
            len(mapa),
            ", ".join(fontes_usadas) or "-",
        )
    return mapa


def _carregar_impostos_cadastro_por_sku(client_id: str, incluir_custos_lojas: bool = True) -> dict:
    mapa = {}
    chaves_exatas = set()
    fontes_usadas = []

    for df, caminho in _iterar_dfs_cadastro_custos(client_id, incluir_custos_lojas=incluir_custos_lojas):
        if "sku" not in df.columns:
            continue
        col_imposto = "imposto" if "imposto" in df.columns else None
        for nome in ("imposto", "aliquota_imposto", "aliquota imposto", "aliquota"):
            if nome in df.columns:
                col_imposto = nome
                break
        if not col_imposto:
            continue

        antes = len(mapa)
        for _, row in df.iterrows():
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            imposto_rate = _to_rate_safe(row.get(col_imposto, ""))
            if imposto_rate is None:
                continue
            chave_exata = _normalizar_sku_mes(sku).upper()
            if chave_exata and chave_exata not in chaves_exatas:
                mapa[chave_exata] = float(imposto_rate)
                chaves_exatas.add(chave_exata)
            for key in _sku_lookup_variantes(sku):
                if not key or key == chave_exata or key in chaves_exatas:
                    continue
                mapa.setdefault(key, float(imposto_rate))
        if len(mapa) > antes:
            fontes_usadas.append(os.path.basename(caminho))

    if mapa:
        logger.info(
            "[PROMO CADASTRO] Impostos carregados: %s SKU-chave(s) | fontes: %s",
            len(mapa),
            ", ".join(fontes_usadas) or "-",
        )
    return mapa


def _carregar_custos_impostos_cadastro_por_sku_loja(client_id: str, loja: str) -> tuple[dict, dict]:
    custos_por_sku = _carregar_custos_cadastro_por_sku(client_id, incluir_custos_lojas=False)
    impostos_por_sku = _carregar_impostos_cadastro_por_sku(client_id, incluir_custos_lojas=False)
    loja_key = _cadastro_norm_loja_custo(loja)
    if not loja_key:
        return custos_por_sku, impostos_por_sku

    try:
        mapa_lojas = _cadastro_mapa_custos_lojas(client_id)
    except Exception as exc:
        logger.warning("[PROMO CADASTRO] Falha ao carregar custos por loja para %s: %s", loja, exc)
        return custos_por_sku, impostos_por_sku

    custos_loja = 0
    impostos_loja = 0
    for sku_key, lojas_sku in (mapa_lojas or {}).items():
        if not sku_key or not isinstance(lojas_sku, dict):
            continue
        dados_loja = lojas_sku.get(loja_key)
        if not isinstance(dados_loja, dict):
            continue
        custo = _parse_float_flex(dados_loja.get("custo"))
        imposto_rate = _to_rate_safe(dados_loja.get("imposto"))
        if custo is not None:
            custos_por_sku[sku_key] = float(custo)
            custos_loja += 1
        if imposto_rate is not None:
            impostos_por_sku[sku_key] = float(imposto_rate)
            impostos_loja += 1

    if custos_loja or impostos_loja:
        logger.info(
            "[PROMO CADASTRO] Custos/impostos por loja aplicados: loja=%s custos=%s impostos=%s",
            loja,
            custos_loja,
            impostos_loja,
        )
    return custos_por_sku, impostos_por_sku

    df = _carregar_df_cadastro_custos(client_id)
    if "sku" not in df.columns:
        return {}

    col_imposto = "imposto" if "imposto" in df.columns else None
    for nome in ("imposto", "aliquota_imposto", "alÃƒÂ­quota imposto", "aliquota", "alÃƒÂ­quota"):
        if nome in df.columns:
            col_imposto = nome
            break
    if not col_imposto:
        return {}

    mapa = {}
    for _, row in df.iterrows():
        sku = str(row.get("sku", "") or "").strip()
        if not sku:
            continue
        imposto_rate = _to_rate_safe(row.get(col_imposto, ""))
        if imposto_rate is None:
            continue
        for key in _sku_lookup_variantes(sku):
            mapa[key] = float(imposto_rate)
    return mapa


def _resolver_custo_por_sku(custos_por_sku: dict, sku_bruto: str):
    if not sku_bruto:
        return None
    # Alguns anuncios retornam mÃƒÂºltiplos SKUs separados por vÃƒÂ­rgula.
    candidatos = [s.strip() for s in str(sku_bruto).split(",") if s.strip()]
    if not candidatos:
        candidatos = [str(sku_bruto).strip()]

    for sku in candidatos:
        for key in _sku_lookup_variantes(sku):
            if key in custos_por_sku:
                return custos_por_sku[key]
    return None


def _extrair_skus_para_custo(*valores) -> list[str]:
    skus = []
    vistos = set()

    def adicionar(valor):
        if valor is None:
            return
        if isinstance(valor, (list, tuple, set)):
            for item in valor:
                adicionar(item)
            return
        texto = str(valor or "").strip()
        if not texto:
            return
        for parte in re.split(r"\s*(?:/|,|;|\|)\s*", texto):
            sku = str(parte or "").strip()
            if not sku or sku == "-":
                continue
            chave = _normalizar_sku_mes(sku).upper()
            if chave and chave not in vistos:
                vistos.add(chave)
                skus.append(sku)

    for valor in valores:
        adicionar(valor)
    return skus


def _resolver_custo_medio_por_skus(custos_por_sku: dict, *sku_valores):
    custos = []
    vistos = set()
    for sku in _extrair_skus_para_custo(*sku_valores):
        chave = _normalizar_sku_mes(sku).upper()
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        custo = _resolver_custo_por_sku(custos_por_sku, sku)
        if custo is not None:
            custos.append(float(custo))
    if not custos:
        return None
    return sum(custos) / len(custos)


def _resolver_imposto_rate_por_sku(impostos_por_sku: dict, sku_bruto: str):
    if not sku_bruto:
        return None
    candidatos = _extrair_skus_para_custo(sku_bruto)
    if not candidatos:
        candidatos = [str(sku_bruto).strip()]

    for sku in candidatos:
        for key in _sku_lookup_variantes(sku):
            if key in impostos_por_sku:
                return impostos_por_sku[key]
    return None


def _to_float_safe(v):
    x = _parse_float_flex(v)
    return float(x) if x is not None else None


def _to_rate_safe(v):
    x = _parse_float_flex(v)
    if x is None:
        return None
    # Aceita tanto 12 quanto 0.12 como entrada de percentual.
    return (float(x) / 100.0) if float(x) > 1.0 else float(x)

PEER_EXPORTS = ['_listar_arquivos_cadastro_custos', '_iterar_dfs_cadastro_custos', '_carregar_custos_cadastro_por_sku', '_carregar_impostos_cadastro_por_sku', '_carregar_custos_impostos_cadastro_por_sku_loja', '_resolver_custo_por_sku', '_extrair_skus_para_custo', '_resolver_custo_medio_por_skus', '_resolver_imposto_rate_por_sku', '_to_float_safe', '_to_rate_safe']
__all__ = PEER_EXPORTS + ["configure_promocoes_core_custos_runtime"]

configure_promocoes_core_custos_runtime()
