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

PROMO_CUSTOS_CACHE_LOCK = threading.RLock()
PROMO_CUSTOS_BASE_CACHE: dict[tuple[str, bool], dict] = {}
PROMO_CUSTOS_LOJA_CACHE: dict[tuple[str, str], dict] = {}


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


def _fingerprint_arquivos_custos(client_id: str, incluir_custos_lojas: bool) -> tuple:
    caminhos = list(_listar_arquivos_cadastro_custos(client_id, incluir_custos_lojas=incluir_custos_lojas))
    if not incluir_custos_lojas:
        try:
            caminho_lojas = _cadastro_custos_lojas_path(client_id)
            if caminho_lojas and caminho_lojas not in caminhos:
                caminhos.append(caminho_lojas)
        except Exception:
            pass
    fingerprint = []
    for caminho in caminhos:
        try:
            stat = os.stat(caminho)
            fingerprint.append((os.path.abspath(caminho), int(stat.st_size), int(stat.st_mtime_ns)))
        except OSError:
            fingerprint.append((os.path.abspath(caminho), -1, -1))
    return tuple(fingerprint)


def invalidar_cache_custos_impostos(client_id: str | None = None) -> None:
    client_key = str(client_id or "").strip()
    with PROMO_CUSTOS_CACHE_LOCK:
        if not client_key:
            PROMO_CUSTOS_BASE_CACHE.clear()
            PROMO_CUSTOS_LOJA_CACHE.clear()
            return
        for cache in (PROMO_CUSTOS_BASE_CACHE, PROMO_CUSTOS_LOJA_CACHE):
            for key in list(cache):
                if key and key[0] == client_key:
                    cache.pop(key, None)


def _adicionar_valor_sku(mapa: dict, chaves_exatas: set, sku: str, valor: float) -> None:
    chave_exata = _normalizar_sku_mes(sku).upper()
    if chave_exata and chave_exata not in chaves_exatas:
        mapa[chave_exata] = float(valor)
        chaves_exatas.add(chave_exata)
    for key in _sku_lookup_variantes(sku):
        if not key or key == chave_exata or key in chaves_exatas:
            continue
        mapa.setdefault(key, float(valor))


def _carregar_custos_impostos_base(
    client_id: str,
    incluir_custos_lojas: bool = True,
) -> tuple[dict, dict]:
    client_key = str(client_id or "").strip()
    cache_key = (client_key, bool(incluir_custos_lojas))
    fingerprint = _fingerprint_arquivos_custos(client_key, bool(incluir_custos_lojas))
    with PROMO_CUSTOS_CACHE_LOCK:
        cached = PROMO_CUSTOS_BASE_CACHE.get(cache_key)
        if cached and cached.get("fingerprint") == fingerprint:
            logger.debug(
                "[PROMO CADASTRO] cache_hit cliente=%s incluir_lojas=%s custos=%s impostos=%s",
                client_key,
                bool(incluir_custos_lojas),
                len(cached.get("custos") or {}),
                len(cached.get("impostos") or {}),
            )
            return dict(cached.get("custos") or {}), dict(cached.get("impostos") or {})

    started = time.perf_counter()
    custos: dict[str, float] = {}
    impostos: dict[str, float] = {}
    custos_exatos: set[str] = set()
    impostos_exatos: set[str] = set()
    fontes_usadas: list[str] = []
    for df, caminho in _iterar_dfs_cadastro_custos(
        client_key,
        incluir_custos_lojas=incluir_custos_lojas,
    ):
        if "sku" not in df.columns:
            continue
        col_custo = "custo" if "custo" in df.columns else None
        col_imposto = next(
            (
                nome
                for nome in ("imposto", "aliquota_imposto", "aliquota imposto", "aliquota")
                if nome in df.columns
            ),
            None,
        )
        if not col_custo and not col_imposto:
            continue
        colunas = ["sku"] + [nome for nome in (col_custo, col_imposto) if nome and nome != "sku"]
        antes = len(custos) + len(impostos)
        for valores in df[colunas].itertuples(index=False, name=None):
            row = dict(zip(colunas, valores))
            sku = str(row.get("sku", "") or "").strip()
            if not sku:
                continue
            if col_custo:
                custo = _parse_float_flex(row.get(col_custo, ""))
                if custo is not None:
                    _adicionar_valor_sku(custos, custos_exatos, sku, float(custo))
            if col_imposto:
                imposto_rate = _to_rate_safe(row.get(col_imposto, ""))
                if imposto_rate is not None:
                    _adicionar_valor_sku(impostos, impostos_exatos, sku, float(imposto_rate))
        if len(custos) + len(impostos) > antes:
            fontes_usadas.append(os.path.basename(caminho))

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    with PROMO_CUSTOS_CACHE_LOCK:
        PROMO_CUSTOS_BASE_CACHE[cache_key] = {
            "fingerprint": fingerprint,
            "custos": dict(custos),
            "impostos": dict(impostos),
            "loaded_at": time.time(),
        }
    logger.info(
        "[PROMO CADASTRO] cache_miss cliente=%s incluir_lojas=%s custos=%s impostos=%s ms=%s fontes=%s",
        client_key,
        bool(incluir_custos_lojas),
        len(custos),
        len(impostos),
        elapsed_ms,
        ", ".join(fontes_usadas) or "-",
    )
    return dict(custos), dict(impostos)


def _carregar_custos_cadastro_por_sku(client_id: str, incluir_custos_lojas: bool = True) -> dict:
    custos, _impostos = _carregar_custos_impostos_base(client_id, incluir_custos_lojas)
    return custos


def _carregar_impostos_cadastro_por_sku(client_id: str, incluir_custos_lojas: bool = True) -> dict:
    _custos, impostos = _carregar_custos_impostos_base(client_id, incluir_custos_lojas)
    return impostos


def _carregar_custos_impostos_cadastro_por_sku_loja(client_id: str, loja: str) -> tuple[dict, dict]:
    client_key = str(client_id or "").strip()
    loja_key = _cadastro_norm_loja_custo(loja)
    cache_key = (client_key, loja_key)
    fingerprint = _fingerprint_arquivos_custos(client_key, incluir_custos_lojas=False)
    with PROMO_CUSTOS_CACHE_LOCK:
        cached = PROMO_CUSTOS_LOJA_CACHE.get(cache_key)
        if cached and cached.get("fingerprint") == fingerprint:
            logger.debug(
                "[PROMO CADASTRO] cache_hit cliente=%s loja=%s custos=%s impostos=%s",
                client_key,
                loja_key or "-",
                len(cached.get("custos") or {}),
                len(cached.get("impostos") or {}),
            )
            return dict(cached.get("custos") or {}), dict(cached.get("impostos") or {})

    custos_por_sku, impostos_por_sku = _carregar_custos_impostos_base(
        client_key,
        incluir_custos_lojas=False,
    )
    if not loja_key:
        return custos_por_sku, impostos_por_sku

    started = time.perf_counter()
    try:
        mapa_lojas = _cadastro_mapa_custos_lojas(client_key)
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

    logger.info(
        "[PROMO CADASTRO] cache_miss_loja loja=%s custos=%s impostos=%s ms=%s",
        loja,
        custos_loja,
        impostos_loja,
        int((time.perf_counter() - started) * 1000),
    )
    with PROMO_CUSTOS_CACHE_LOCK:
        PROMO_CUSTOS_LOJA_CACHE[cache_key] = {
            "fingerprint": fingerprint,
            "custos": dict(custos_por_sku),
            "impostos": dict(impostos_por_sku),
            "loaded_at": time.time(),
        }
    return dict(custos_por_sku), dict(impostos_por_sku)

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

PEER_EXPORTS = ['_listar_arquivos_cadastro_custos', '_iterar_dfs_cadastro_custos', 'invalidar_cache_custos_impostos', '_carregar_custos_cadastro_por_sku', '_carregar_impostos_cadastro_por_sku', '_carregar_custos_impostos_cadastro_por_sku_loja', '_resolver_custo_por_sku', '_extrair_skus_para_custo', '_resolver_custo_medio_por_skus', '_resolver_imposto_rate_por_sku', '_to_float_safe', '_to_rate_safe']
__all__ = PEER_EXPORTS + ["configure_promocoes_core_custos_runtime"]

configure_promocoes_core_custos_runtime()
