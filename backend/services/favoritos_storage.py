"""Internal slice for favoritos_core."""

from __future__ import annotations

from __future__ import annotations
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


def configure_favoritos_storage_runtime(runtime_module=None, peers=None):
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


configure_favoritos_storage_runtime()


def _normalizar_sku_match_favoritos(sku: str) -> str:
    """Normaliza SKU usado no fluxo de favoritos."""
    return _normalizar_sku_mes(str(sku or "").strip())


def _chave_loja_favoritos(nome_loja: str | None) -> str:
    """Normaliza nome de loja para comparar seleções no frontend e backend."""
    return re.sub(r"[^a-z0-9]+", "", normalizar_texto(str(nome_loja or "").strip()))


def _lojas_favoritos_com_bling_ml(client_id: str) -> list[dict]:
    """Retorna somente lojas com integrações de Bling e Mercado Livre ativas."""
    lojas = carregar_lojas(client_id) or []
    favoritas = []
    for loja in lojas:
        if not isinstance(loja, dict):
            continue
        nome_loja = str(loja.get("nome") or "").strip()
        if not nome_loja:
            continue

        integracoes = loja.get("integracoes") or {}
        if not isinstance(integracoes, dict):
            continue

        cfg_mercado = integracoes.get("mercadolivre") or {}
        cfg_bling = integracoes.get("bling") or {}
        if not isinstance(cfg_mercado, dict) or not isinstance(cfg_bling, dict):
            continue
        if not str(cfg_mercado.get("access_token") or "").strip():
            continue
        if not str(cfg_bling.get("access_token") or "").strip():
            continue

        favoritas.append(loja)
    return favoritas


def _lojas_favoritos_com_ml(client_id: str) -> list[dict]:
    """Retorna lojas com Mercado Livre conectado para consultas diretas na API do ML."""
    lojas = carregar_lojas(client_id) or []
    favoritas = []
    for loja in lojas:
        if not isinstance(loja, dict):
            continue
        nome_loja = str(loja.get("nome") or "").strip()
        if not nome_loja:
            continue
        integracoes = loja.get("integracoes") or {}
        cfg_mercado = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        if not isinstance(cfg_mercado, dict):
            continue
        if not str(cfg_mercado.get("access_token") or "").strip():
            continue
        favoritas.append(loja)
    return favoritas


def _favoritos_sku_norm_loja(valor: str) -> str:
    return _chave_loja_favoritos(valor)


def _favoritos_sku_float(valor, default: float = 0.0) -> float:
    try:
        if valor is None:
            return default
        txt = str(valor).strip()
        if not txt:
            return default
        txt = txt.replace("R$", "").replace(" ", "")
        if "," in txt and "." in txt:
            txt = txt.replace(".", "").replace(",", ".")
        elif "," in txt:
            txt = txt.replace(",", ".")
        return float(txt)
    except Exception:
        return default


def _favoritos_sku_pick(row: dict, campos: list[str], fallback: str = "") -> str:
    row_norm = {str(k or "").strip().lower(): v for k, v in (row or {}).items()}
    for campo in campos:
        key = str(campo or "").strip().lower()
        if key in row_norm:
            valor = str(row_norm.get(key) if row_norm.get(key) is not None else "").strip()
            if valor:
                return valor
    return fallback


def _favoritos_limpar_nome_produto(valor: str) -> str:
    nome = str(valor or "").strip()
    if not nome:
        return ""

    # Evita gravar no campo nome conteúdos óbvios de descrição (textos longos/multifráse).
    if "\n" in nome or "\r" in nome:
        return ""
    if len(nome) > 220:
        return ""
    if (nome.count(".") + nome.count(";")) >= 2:
        return ""
    return nome


def _cadastro_nome_esta_suspeito(valor: str) -> bool:
    texto = str(valor or "").strip()
    if not texto:
        return True
    if "\n" in texto or "\r" in texto:
        return True
    if len(texto) > 220:
        return True
    if texto.count(";") >= 2:
        return True
    return False


def _cadastro_limpar_nome(valor: str) -> str:
    return "" if _cadastro_nome_esta_suspeito(valor) else str(valor or "").strip()


def _favoritos_ler_cadastro_csv(cadastro_path: str, cols_padrao: list[str] | None = None) -> pd.DataFrame:
    if not cadastro_path or not os.path.exists(cadastro_path):
        return pd.DataFrame(columns=cols_padrao or [])

    try:
        df = pd.read_csv(cadastro_path, dtype=str, keep_default_na=False).fillna("")
    except Exception as exc:
        logger.warning("[Favoritos SKU] Leitura normal do cadastro falhou, usando leitura tolerante: %s", exc)
        try:
            csv.field_size_limit(min(sys.maxsize, 2_147_483_647))
        except Exception:
            pass

        with open(cadastro_path, newline="", encoding="utf-8-sig", errors="replace") as arquivo:
            reader = csv.reader(arquivo)
            try:
                header = next(reader)
            except StopIteration:
                return pd.DataFrame(columns=cols_padrao or [])

            header = [str(c or "").strip().lower() for c in header]
            total_cols = len(header)
            linhas = []
            for row in reader:
                if not row:
                    continue
                if len(row) < total_cols:
                    row = row + [""] * (total_cols - len(row))
                elif len(row) > total_cols and total_cols > 0:
                    row = row[:total_cols - 1] + [",".join(row[total_cols - 1:])]
                linhas.append(row[:total_cols])

        df = pd.DataFrame(linhas, columns=header)

    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    return df.fillna("")


CADASTRO_PESQUISA_COLS = ["pesquisa_1", "pesquisa_2", "pesquisa_3"]


CADASTRO_COLS_BASE = ["sku", "nome", "categoria", "marca", "custo", "preco", "imposto", "descricao", "updated_at"] + CADASTRO_PESQUISA_COLS


CADASTRO_DESCRICAO_COL_ALIASES = {
    "descricao",
    "description",
    "descricao produto",
    "descricao do produto",
    "descricaoproduto",
    "descricaodoproduto",
}


def _cadastro_cols_base(*extras: str) -> list[str]:
    cols = list(CADASTRO_COLS_BASE)
    for col in extras:
        col = str(col or "").strip()
        if col and col not in cols:
            cols.append(col)
    return cols


def _cadastro_norm_coluna_texto(coluna: str) -> str:
    texto = str(coluna or "").strip().lower()
    texto = (
        texto
        .replace("\u00e3\u00a7", "c")
        .replace("\u00e3\u00a3", "a")
        .replace("\u00e3\u00a9", "e")
        .replace("\u00e3\u00aa", "e")
        .replace("\u00e3\u00ad", "i")
        .replace("\u00e3\u00b3", "o")
        .replace("\u00e3\u00ba", "u")
    )
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _cadastro_canonizar_coluna_descricao(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    aliases = [
        col for col in list(df.columns)
        if _cadastro_norm_coluna_texto(col) in CADASTRO_DESCRICAO_COL_ALIASES
    ]
    if "descricao" not in df.columns:
        df["descricao"] = ""
    for col in aliases:
        if col == "descricao" or col not in df.columns:
            continue
        atual = df["descricao"].astype(str)
        origem = df[col].astype(str)
        mask = atual.str.strip().eq("") & origem.str.strip().ne("")
        if mask.any():
            df.loc[mask, "descricao"] = origem.loc[mask]
        df = df.drop(columns=[col], errors="ignore")
    return df


def _cadastro_garantir_colunas_pesquisa(df: pd.DataFrame) -> pd.DataFrame:
    df = _cadastro_canonizar_coluna_descricao(df)
    for col in CADASTRO_PESQUISA_COLS:
        if col not in df.columns:
            df[col] = ""
    return df


def _cadastro_valor_preenchido(valor: Any) -> bool:
    if valor is None:
        return False
    try:
        if pd.isna(valor):
            return False
    except Exception:
        pass
    return str(valor).strip() != ""


def _consolidar_cadastro_por_sku(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df is None:
        return pd.DataFrame(columns=_cadastro_cols_base()), 0

    df = _cadastro_garantir_colunas_pesquisa(df.copy())
    if "sku" not in df.columns:
        df["sku"] = ""

    for col in _cadastro_cols_base():
        if col not in df.columns:
            df[col] = ""

    colunas = list(df.columns)
    linhas: list[dict[str, Any]] = []
    indice_por_sku: dict[str, int] = {}
    duplicados = 0

    for _, row in df.iterrows():
        item = row.to_dict()
        sku_norm = _normalizar_sku_match_favoritos(str(item.get("sku") or ""))
        item["sku"] = sku_norm
        if not sku_norm:
            linhas.append(item)
            continue

        idx = indice_por_sku.get(sku_norm.lower())
        if idx is None:
            indice_por_sku[sku_norm.lower()] = len(linhas)
            linhas.append(item)
            continue

        duplicados += 1
        base = linhas[idx]
        for col in colunas:
            novo = item.get(col)
            if col == "updated_at":
                if _cadastro_valor_preenchido(novo):
                    base[col] = novo
                continue
            if not _cadastro_valor_preenchido(base.get(col)) and _cadastro_valor_preenchido(novo):
                base[col] = novo
        linhas[idx] = base

    return pd.DataFrame(linhas, columns=colunas), duplicados


def _favoritos_sku_caminhos(client_id: str) -> tuple[str, str]:
    tenant_path = get_tenant_path(client_id)
    cadastro_path = os.path.join(tenant_path, "cadastro_produtos.csv")
    estoque_path = os.path.join(tenant_path, "produtos_compilado.csv")
    if not os.path.exists(cadastro_path):
        cadastro_path = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    if not os.path.exists(estoque_path):
        estoque_path = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
    return cadastro_path, estoque_path


def _favoritos_ml_skus_anuncios_cache_id(loja: Any = "", todas_lojas: bool = False) -> str:
    if todas_lojas:
        return "todas_lojas"
    return _chave_loja_favoritos(loja) or str(loja or "").strip() or "auto"


def _favoritos_ml_anuncios_sku_cache_id(nome_loja: str, sku: str, compartilhar: bool = False, ids_forcados_key: str = "") -> str:
    partes = [
        _chave_loja_favoritos(nome_loja) or nome_loja,
        _normalizar_sku_match_favoritos(sku),
        "compartilhar" if compartilhar else "loja",
    ]
    if ids_forcados_key:
        partes.append(hashlib.sha1(ids_forcados_key.encode("utf-8", errors="ignore")).hexdigest()[:16])
    return "::".join(str(parte or "").strip() for parte in partes if str(parte or "").strip())


def _favoritos_arquivo_pesquisas_usuario(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"favoritos_pesquisas_{_favoritos_usuario_slug(username)}.json")


def _favoritos_chave_pesquisa_usuario(loja: Any, sku: Any) -> str:
    sku_key = _normalizar_sku_match_favoritos(str(sku or "").strip()).upper()
    return f"sku::{sku_key}" if sku_key else ""


def _favoritos_pesquisa_score(item: dict) -> tuple[int, float]:
    if not isinstance(item, dict):
        return (0, 0.0)
    preenchidos = sum(1 for campo in CADASTRO_PESQUISA_COLS if str(item.get(campo) or "").strip())
    data_raw = str(item.get("updated_at") or "").strip()
    timestamp = 0.0
    if data_raw:
        try:
            timestamp = datetime.fromisoformat(data_raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            timestamp = 0.0
    return (preenchidos, timestamp)


def _favoritos_escolher_pesquisa_usuario(atual: dict | None, novo: dict) -> dict:
    if not atual:
        return novo
    score_atual = _favoritos_pesquisa_score(atual)
    score_novo = _favoritos_pesquisa_score(novo)
    if score_novo[1] and score_atual[1] and score_novo[1] != score_atual[1]:
        return novo if score_novo[1] >= score_atual[1] else atual
    if score_novo[0] != score_atual[0]:
        return novo if score_novo[0] >= score_atual[0] else atual
    return novo


def _favoritos_carregar_pesquisas_usuario(client_id: str, username: str) -> dict:
    caminho = _favoritos_arquivo_pesquisas_usuario(client_id, username)
    if not os.path.exists(caminho):
        return {"pesquisas": {}, "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        pesquisas = dados.get("pesquisas") if isinstance(dados, dict) else {}
        if not isinstance(pesquisas, dict):
            pesquisas = {}
        normalizadas: dict[str, dict] = {}
        for chave, item in pesquisas.items():
            if not isinstance(item, dict):
                continue
            loja = str(item.get("loja") or "").strip()
            sku = _normalizar_sku_match_favoritos(str(item.get("sku") or "").strip())
            chave_norm = _favoritos_chave_pesquisa_usuario(loja, sku) or str(chave or "").strip()
            if not chave_norm:
                continue
            item_norm = {
                "loja": loja,
                "sku": sku,
                "produto": str(item.get("produto") or "").strip(),
                "pesquisa_1": str(item.get("pesquisa_1") or "").strip(),
                "pesquisa_2": str(item.get("pesquisa_2") or "").strip(),
                "pesquisa_3": str(item.get("pesquisa_3") or "").strip(),
                "updated_at": item.get("updated_at"),
            }
            normalizadas[chave_norm] = _favoritos_escolher_pesquisa_usuario(normalizadas.get(chave_norm), item_norm)
        return {
            "pesquisas": normalizadas,
            "updated_at": dados.get("updated_at") if isinstance(dados, dict) else None,
        }
    except Exception as exc:
        logger.warning("[Favoritos SKU] Falha ao carregar pesquisas do usuario %s: %s", username, exc)
        return {"pesquisas": {}, "updated_at": None}


def _favoritos_salvar_pesquisas_usuario_batch(
    client_id: str,
    username: str,
    loja: str,
    itens: list[dict],
) -> list[dict]:
    if not itens:
        return []
    loja_nome = str(loja or "").strip()
    if not loja_nome:
        loja_nome = "Sem loja"

    payload = _favoritos_carregar_pesquisas_usuario(client_id, username)
    pesquisas = dict(payload.get("pesquisas") or {})
    agora = datetime.now().isoformat(timespec="seconds")
    atualizados: list[dict] = []

    for item in itens:
        sku_norm = _normalizar_sku_match_favoritos(str((item or {}).get("sku") or "").strip())
        if not sku_norm:
            continue
        pesquisa_1 = str((item or {}).get("pesquisa_1") or "").strip()
        pesquisa_2 = str((item or {}).get("pesquisa_2") or "").strip()
        pesquisa_3 = str((item or {}).get("pesquisa_3") or "").strip()
        produto = _favoritos_limpar_nome_produto((item or {}).get("produto") or (item or {}).get("nome") or (item or {}).get("titulo") or "")
        chave = _favoritos_chave_pesquisa_usuario("", sku_norm)
        if not chave:
            continue
        pesquisas[chave] = {
            "loja": loja_nome,
            "sku": sku_norm,
            "produto": produto,
            "pesquisa_1": pesquisa_1,
            "pesquisa_2": pesquisa_2,
            "pesquisa_3": pesquisa_3,
            "updated_at": agora,
        }
        atualizados.append({
            "sku": sku_norm,
            "loja": loja_nome,
            "pesquisa_1": pesquisa_1,
            "pesquisa_2": pesquisa_2,
            "pesquisa_3": pesquisa_3,
        })

    if atualizados:
        caminho = _favoritos_arquivo_pesquisas_usuario(client_id, username)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"pesquisas": pesquisas, "updated_at": agora}, f, ensure_ascii=False, indent=2)
    return atualizados


def _favoritos_enriquecer_pesquisas_usuario(
    client_id: str,
    username: str,
    loja: str,
    skus: list[dict],
) -> list[dict]:
    if not skus:
        return skus
    pesquisas = (_favoritos_carregar_pesquisas_usuario(client_id, username).get("pesquisas") or {})
    cadastro_legado: dict[str, dict] | None = None
    for item in skus:
        if not isinstance(item, dict):
            continue
        chave = _favoritos_chave_pesquisa_usuario("", item.get("sku"))
        salvo = pesquisas.get(chave)
        fonte = "favoritos_usuario_sku"
        if not salvo:
            if cadastro_legado is None:
                cadastro_legado, _ = _favoritos_carregar_cadastro_por_sku(client_id)
            for sku_chave in _favoritos_chaves_match_sku(item.get("sku")):
                candidato = cadastro_legado.get(sku_chave) if cadastro_legado else None
                if candidato and any(str(candidato.get(campo) or "").strip() for campo in CADASTRO_PESQUISA_COLS):
                    salvo = candidato
                    fonte = "cadastro_produtos_legado"
                    break
        if not salvo:
            continue
        for campo in CADASTRO_PESQUISA_COLS:
            item[campo] = str(salvo.get(campo) or "").strip()
        item["pesquisas_fonte"] = fonte
        item["pesquisas_updated_at"] = salvo.get("updated_at")
    return skus


def _favoritos_carregar_skus_ocultos(client_id: str, username: str) -> dict:
    caminho = _favoritos_arquivo_skus_ocultos(client_id, username)
    if not os.path.exists(caminho):
        return {"skus_ocultos": [], "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            return {
                "skus_ocultos": _favoritos_normalizar_skus_ocultos(dados.get("skus_ocultos") or []),
                "updated_at": dados.get("updated_at"),
            }
        if isinstance(dados, list):
            return {"skus_ocultos": _favoritos_normalizar_skus_ocultos(dados), "updated_at": None}
    except Exception as exc:
        logger.warning("[Favoritos SKU] Falha ao carregar SKUs ocultos do usuario %s: %s", username, exc)
    return {"skus_ocultos": [], "updated_at": None}


def _favoritos_salvar_skus_ocultos(client_id: str, username: str, skus_ocultos: Any) -> dict:
    caminho = _favoritos_arquivo_skus_ocultos(client_id, username)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    payload = {
        "skus_ocultos": _favoritos_normalizar_skus_ocultos(skus_ocultos),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _favoritos_normalizar_vendedores_ignorados(lista: Any) -> list[str]:
    if not isinstance(lista, list):
        return []
    vistos: set[str] = set()
    saida: list[str] = []
    for item in lista:
        nome = re.sub(r"\s+", " ", str(item or "").strip())
        if not nome:
            continue
        chave = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii").lower()
        chave = re.sub(r"[^a-z0-9]+", "", chave)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(nome[:120])
        if len(saida) >= 5000:
            break
    return sorted(saida, key=lambda valor: valor.lower())


def _favoritos_arquivo_vendedores_ignorados(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"favoritos_vendedores_ignorados_{_favoritos_usuario_slug(username)}.json")


def _favoritos_carregar_vendedores_ignorados(client_id: str, username: str) -> dict:
    caminho = _favoritos_arquivo_vendedores_ignorados(client_id, username)
    if not os.path.exists(caminho):
        return {"vendedores_ignorados": [], "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            return {
                "vendedores_ignorados": _favoritos_normalizar_vendedores_ignorados(dados.get("vendedores_ignorados") or []),
                "updated_at": dados.get("updated_at"),
            }
        if isinstance(dados, list):
            return {"vendedores_ignorados": _favoritos_normalizar_vendedores_ignorados(dados), "updated_at": None}
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha ao carregar vendedores ignorados do usuario %s: %s", username, exc)
    return {"vendedores_ignorados": [], "updated_at": None}


def _favoritos_salvar_vendedores_ignorados(client_id: str, username: str, vendedores_ignorados: Any) -> dict:
    caminho = _favoritos_arquivo_vendedores_ignorados(client_id, username)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    payload = {
        "vendedores_ignorados": _favoritos_normalizar_vendedores_ignorados(vendedores_ignorados),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _favoritos_arquivo_anuncios_ignorados(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"favoritos_anuncios_ignorados_{_favoritos_usuario_slug(username)}.json")


def _favoritos_normalizar_anuncios_ignorados(valor: Any) -> dict[str, list[dict]]:
    if not isinstance(valor, dict):
        return {}
    saida: dict[str, list[dict]] = {}
    total = 0
    for sku_raw, itens_raw in valor.items():
        sku = _normalizar_sku_mes(str(sku_raw or "").strip())
        if not sku or not isinstance(itens_raw, list):
            continue
        vistos: set[str] = set()
        itens: list[dict] = []
        for item in itens_raw:
            if not isinstance(item, dict):
                continue
            item_id = re.sub(r"[^A-Z0-9]+", "", str(item.get("id") or item.get("mlb") or "").strip().upper())
            url = _favoritos_limpar_texto_historico(item.get("url") or item.get("permalink") or "", 500)
            titulo = _favoritos_limpar_texto_historico(item.get("titulo") or item.get("title") or "", 500)
            vendedor = _favoritos_limpar_texto_historico(item.get("vendedor") or item.get("seller") or "", 180)
            chaves = []
            if isinstance(item.get("chaves"), list):
                chaves = [
                    _favoritos_limpar_texto_historico(chave, 700)
                    for chave in item.get("chaves")
                    if str(chave or "").strip()
                ][:12]
            if item_id and not any(str(chave).upper() == f"ID:{item_id}" for chave in chaves):
                chaves.insert(0, f"id:{item_id}")
            chave_unica = item_id or (chaves[0] if chaves else "") or f"{titulo}|{vendedor}|{url}"
            if not chave_unica or chave_unica in vistos:
                continue
            vistos.add(chave_unica)
            itens.append({
                "id": item_id,
                "url": url,
                "titulo": titulo,
                "vendedor": vendedor,
                "imagem": _favoritos_limpar_texto_historico(item.get("imagem") or item.get("thumbnail") or "", 500),
                "preco": _favoritos_numero_historico(item.get("preco")),
                "preco_promocional": _favoritos_numero_historico(item.get("preco_promocional")),
                "chaves": chaves,
                "loja": _favoritos_limpar_texto_historico(item.get("loja") or "", 160),
                "ignorado_em": _favoritos_limpar_texto_historico(item.get("ignorado_em") or item.get("data_iso") or "", 80),
            })
            total += 1
            if len(itens) >= 500 or total >= 10000:
                break
        if itens:
            saida[sku] = itens
        if total >= 10000:
            break
    return saida


def _favoritos_carregar_anuncios_ignorados(client_id: str, username: str) -> dict:
    caminho = _favoritos_arquivo_anuncios_ignorados(client_id, username)
    if not os.path.exists(caminho):
        return {"anuncios_ignorados": {}, "updated_at": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            return {
                "anuncios_ignorados": _favoritos_normalizar_anuncios_ignorados(dados.get("anuncios_ignorados") or dados),
                "updated_at": dados.get("updated_at"),
            }
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha ao carregar anuncios ignorados do usuario %s: %s", username, exc)
    return {"anuncios_ignorados": {}, "updated_at": None}


def _favoritos_salvar_anuncios_ignorados(client_id: str, username: str, anuncios_ignorados: Any) -> dict:
    caminho = _favoritos_arquivo_anuncios_ignorados(client_id, username)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    payload = {
        "anuncios_ignorados": _favoritos_normalizar_anuncios_ignorados(anuncios_ignorados),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _favoritos_limpar_texto_historico(valor: Any, limite: int = 600) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "").strip())
    return texto[:limite]


def _favoritos_numero_historico(valor: Any):
    if valor in (None, ""):
        return None
    try:
        numero = float(valor)
        if not math.isfinite(numero):
            return None
        if numero.is_integer():
            return int(numero)
        return numero
    except Exception:
        return valor


def _favoritos_int_historico(valor: Any, padrao: int = 0) -> int:
    numero = _favoritos_numero_historico(valor)
    try:
        return int(numero)
    except Exception:
        return int(padrao)


def _favoritos_lista_texto_historico(valor: Any, limite_item: int = 220, max_itens: int = 5) -> list[str]:
    if not isinstance(valor, list):
        return []
    saida: list[str] = []
    for item in valor[:max_itens]:
        texto = _favoritos_limpar_texto_historico(item, limite_item)
        if texto:
            saida.append(texto)
    return saida


FAVORITOS_HISTORICO_MAX = 500


FAVORITOS_HISTORICO_ANUNCIOS_MAX = 60


FAVORITOS_HISTORICO_REALTIME_SCOPE = "favoritos_historico"


def _favoritos_normalizar_linha_relatorio_alteracao(raw: Any) -> dict | None:
    if isinstance(raw, str):
        detalhe = _favoritos_limpar_texto_historico(raw, 1200)
        if not detalhe:
            return None
        return {"tipo": "info", "titulo": "Relatorio", "detalhe": detalhe}
    if not isinstance(raw, dict):
        return None
    detalhe = _favoritos_limpar_texto_historico(
        raw.get("detalhe") or raw.get("descricao") or raw.get("mensagem"),
        1200,
    )
    titulo = _favoritos_limpar_texto_historico(
        raw.get("titulo") or raw.get("title") or raw.get("itemId") or raw.get("item_id") or "Relatorio",
        180,
    )
    if not detalhe and not titulo:
        return None
    return {
        "tipo": _favoritos_limpar_texto_historico(raw.get("tipo") or raw.get("status") or "info", 40),
        "itemId": _favoritos_limpar_texto_historico(raw.get("itemId") or raw.get("item_id"), 40),
        "titulo": titulo,
        "detalhe": detalhe,
    }


def _favoritos_normalizar_relatorio_alteracao(raw: Any) -> dict | None:
    if isinstance(raw, str):
        resumo = _favoritos_limpar_texto_historico(raw, 1600)
        if not resumo:
            return None
        return {"titulo": "Relatorio", "resumo": resumo, "linhas": []}
    if not isinstance(raw, dict):
        return None
    linhas = []
    for linha in (raw.get("linhas") or [])[:220]:
        linha_norm = _favoritos_normalizar_linha_relatorio_alteracao(linha)
        if linha_norm:
            linhas.append(linha_norm)
    return {
        "titulo": _favoritos_limpar_texto_historico(raw.get("titulo") or raw.get("title") or "Relatorio", 180),
        "resumo": _favoritos_limpar_texto_historico(raw.get("resumo") or raw.get("mensagem") or raw.get("status"), 1600),
        "sucessos": _favoritos_int_historico(raw.get("sucessos"), 0),
        "falhas": _favoritos_int_historico(raw.get("falhas"), 0),
        "linhas": linhas,
    }


def _favoritos_normalizar_anuncio_alteracao_historico(anuncio: Any) -> dict:
    if not isinstance(anuncio, dict):
        return {}
    url = _favoritos_limpar_texto_historico(anuncio.get("url") or anuncio.get("permalink") or anuncio.get("link"), 800)
    anuncio_id = _favoritos_limpar_texto_historico(
        anuncio.get("id") or anuncio.get("mlb") or anuncio.get("item_id") or _extrair_item_id(url),
        40,
    )
    preco_promocional = _favoritos_numero_historico(
        anuncio.get("preco_promocional")
        or anuncio.get("promotional_price")
        or anuncio.get("promotion_price")
        or anuncio.get("discounted_price")
    )
    preco = _favoritos_numero_historico(anuncio.get("preco") or anuncio.get("price"))
    preco_original = _favoritos_numero_historico(
        anuncio.get("preco_original")
        or anuncio.get("original_price")
        or anuncio.get("regular_amount")
        or anuncio.get("standard_price")
    )
    imagem = _favoritos_limpar_texto_historico(anuncio.get("imagem") or anuncio.get("thumbnail") or anuncio.get("foto"), 800)
    return {
        "id": anuncio_id,
        "mlb": anuncio_id,
        "historico_estatico": bool(anuncio.get("historico_estatico", True)),
        "ranking_historico_estatico": bool(anuncio.get("ranking_historico_estatico", True)),
        "bloquear_atualizacao_historico": bool(anuncio.get("bloquear_atualizacao_historico", True)),
        "fonte_registro": _favoritos_limpar_texto_historico(anuncio.get("fonte_registro") or "historico_ranqueamento", 80),
        "url": url,
        "permalink": url,
        "link": url,
        "titulo": _favoritos_limpar_texto_historico(anuncio.get("titulo") or anuncio.get("title"), 500),
        "title": _favoritos_limpar_texto_historico(anuncio.get("title") or anuncio.get("titulo"), 500),
        "vendedor": _favoritos_limpar_texto_historico(
            anuncio.get("vendedor") or anuncio.get("seller_name") or anuncio.get("seller_nickname") or anuncio.get("nickname"),
            180,
        ),
        "imagem": imagem,
        "thumbnail": imagem,
        "foto": imagem,
        "preco": preco,
        "price": preco_promocional if preco_promocional is not None else preco,
        "preco_original": preco_original,
        "original_price": preco_original,
        "preco_promocional": preco_promocional,
        "promotional_price": preco_promocional,
        "custo": _favoritos_numero_historico(
            anuncio.get("custo")
            or anuncio.get("custo_unitario")
            or anuncio.get("custo_produto")
            or anuncio.get("preco_custo")
            or anuncio.get("valor_custo")
        ),
        "custo_unitario": _favoritos_numero_historico(
            anuncio.get("custo_unitario")
            or anuncio.get("custo")
            or anuncio.get("custo_produto")
            or anuncio.get("preco_custo")
            or anuncio.get("valor_custo")
        ),
        "custo_produto": _favoritos_numero_historico(
            anuncio.get("custo_produto")
            or anuncio.get("custo")
            or anuncio.get("custo_unitario")
            or anuncio.get("preco_custo")
            or anuncio.get("valor_custo")
        ),
        "preco_custo": _favoritos_numero_historico(
            anuncio.get("preco_custo")
            or anuncio.get("custo")
            or anuncio.get("custo_unitario")
            or anuncio.get("custo_produto")
            or anuncio.get("valor_custo")
        ),
        "valor_custo": _favoritos_numero_historico(
            anuncio.get("valor_custo")
            or anuncio.get("custo")
            or anuncio.get("custo_unitario")
            or anuncio.get("custo_produto")
            or anuncio.get("preco_custo")
        ),
        "custo_frete": _favoritos_numero_historico(anuncio.get("custo_frete")),
        "discount_pct": _favoritos_numero_historico(anuncio.get("discount_pct") or anuncio.get("discount_percent") or anuncio.get("discount_percentage")),
        "moeda": _favoritos_limpar_texto_historico(anuncio.get("moeda") or anuncio.get("currency_id") or "BRL", 12),
        "currency_id": _favoritos_limpar_texto_historico(anuncio.get("currency_id") or anuncio.get("moeda") or "BRL", 12),
        "tipo_anuncio": _favoritos_limpar_texto_historico(anuncio.get("tipo_anuncio") or anuncio.get("listing_type_name"), 80),
        "listing_type_id": _favoritos_limpar_texto_historico(anuncio.get("listing_type_id") or anuncio.get("listingTypeId"), 80),
        "media_mensal": _favoritos_numero_historico(anuncio.get("media_mensal") or anuncio.get("ritmo_atual")),
        "vendas": _favoritos_numero_historico(anuncio.get("vendas")),
        "data_criacao": _favoritos_limpar_texto_historico(anuncio.get("data_criacao"), 80),
    }


def _favoritos_normalizar_simulacao_alteracao_historico(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        "ok": bool(raw.get("ok")),
        "preco_previsto": _favoritos_numero_historico(raw.get("preco_previsto")),
        "preco_promocional_previsto": _favoritos_numero_historico(raw.get("preco_promocional_previsto")),
        "preco_aplicado": _favoritos_numero_historico(raw.get("preco_aplicado")),
        "preco_promocional_aplicado": _favoritos_numero_historico(raw.get("preco_promocional_aplicado")),
        "margem_prevista": _favoritos_numero_historico(raw.get("margem_prevista")),
        "margem_aplicada": _favoritos_numero_historico(raw.get("margem_aplicada") or raw.get("margem_estimada_contingencia")),
        "custo": _favoritos_numero_historico(
            raw.get("custo")
            or raw.get("custo_base")
            or raw.get("custo_unitario")
            or raw.get("custo_produto")
            or raw.get("preco_custo")
            or raw.get("valor_custo")
        ),
        "custo_base": _favoritos_numero_historico(
            raw.get("custo_base")
            or raw.get("custo")
            or raw.get("custo_unitario")
            or raw.get("custo_produto")
            or raw.get("preco_custo")
            or raw.get("valor_custo")
        ),
        "custo_unitario": _favoritos_numero_historico(
            raw.get("custo_unitario")
            or raw.get("custo")
            or raw.get("custo_base")
            or raw.get("custo_produto")
            or raw.get("preco_custo")
            or raw.get("valor_custo")
        ),
        "limite_margem_aplicado": bool(raw.get("limite_margem_aplicado") or raw.get("limiteMargemAplicado")),
        "preco_minimo_margem": _favoritos_numero_historico(raw.get("preco_minimo_margem") or raw.get("precoMinimoMargem")),
        "preco_alvo_custo_ideal": _favoritos_numero_historico(raw.get("preco_alvo_custo_ideal") or raw.get("precoAlvoCustoIdeal")),
        "custo_ideal_abaixo_base": _favoritos_numero_historico(
            raw.get("custo_ideal_abaixo_base")
            or raw.get("custoIdealAbaixoBase")
            or raw.get("preco_custo_necessario")
            or raw.get("custo_para_concorrer")
            or raw.get("custo_maximo_para_concorrer")
        ),
        "reducao_custo_ideal": _favoritos_numero_historico(raw.get("reducao_custo_ideal") or raw.get("reducaoCustoIdeal")),
        "tipo_anuncio_atual": _favoritos_limpar_texto_historico(raw.get("tipo_anuncio_atual"), 80),
        "tipo_anuncio_alvo": _favoritos_limpar_texto_historico(raw.get("tipo_anuncio_alvo"), 80),
        "campanha_id": _favoritos_limpar_texto_historico(raw.get("campanha_id"), 120),
        "campanha_nome": _favoritos_limpar_texto_historico(raw.get("campanha_nome"), 240),
        "fallback_sem_promocao": bool(raw.get("fallback_sem_promocao")),
    }


def _favoritos_normalizar_opcoes_promocao_historico(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    campanha_raw = raw.get("campanha") if isinstance(raw.get("campanha"), dict) else {}
    desconto_raw = raw.get("desconto") if isinstance(raw.get("desconto"), dict) else {}
    return {
        "usar_promocao": bool(raw.get("usar_promocao")),
        "campanha": {
            "id": _favoritos_limpar_texto_historico(campanha_raw.get("id"), 120),
            "nome": _favoritos_limpar_texto_historico(campanha_raw.get("nome"), 240),
            "tipo": _favoritos_limpar_texto_historico(campanha_raw.get("tipo"), 80),
            "status": _favoritos_limpar_texto_historico(campanha_raw.get("status"), 80),
        },
        "desconto": {
            "modo": _favoritos_limpar_texto_historico(desconto_raw.get("modo"), 80),
            "percentual": _favoritos_numero_historico(desconto_raw.get("percentual")),
        },
    }


def _favoritos_normalizar_vinculo_alteracao_historico(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    nosso = _favoritos_normalizar_anuncio_alteracao_historico(raw.get("nosso") or raw.get("anuncio"))
    base = _favoritos_normalizar_anuncio_alteracao_historico(raw.get("base") or raw.get("ranking"))
    item_id = _favoritos_limpar_texto_historico(raw.get("itemId") or raw.get("item_id") or nosso.get("id"), 40)
    if not item_id and not nosso.get("id") and not base.get("id"):
        return None
    return {
        "ordem": _favoritos_int_historico(raw.get("ordem") or raw.get("rank") or raw.get("posicao"), 0),
        "sku": _favoritos_limpar_texto_historico(raw.get("sku"), 80),
        "itemId": item_id,
        "loja": _favoritos_limpar_texto_historico(raw.get("loja"), 180),
        "status": _favoritos_limpar_texto_historico(raw.get("status") or raw.get("tipo") or "info", 40),
        "status_texto": _favoritos_limpar_texto_historico(raw.get("status_texto") or raw.get("mensagem"), 300),
        "nosso": nosso,
        "base": base,
        "simulacao": _favoritos_normalizar_simulacao_alteracao_historico(raw.get("simulacao")),
        "relatorio_inicial": _favoritos_normalizar_linha_relatorio_alteracao(raw.get("relatorio_inicial")),
        "relatorio_final": _favoritos_normalizar_linha_relatorio_alteracao(raw.get("relatorio_final")),
    }


def _favoritos_normalizar_alteracao_favoritos_historico(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    vinculos = []
    for vinculo in (raw.get("vinculos") or [])[:240]:
        vinculo_norm = _favoritos_normalizar_vinculo_alteracao_historico(vinculo)
        if vinculo_norm:
            vinculos.append(vinculo_norm)
    if not vinculos:
        return None
    return {
        "data_iso": _favoritos_limpar_texto_historico(raw.get("data_iso"), 80),
        "sku": _favoritos_limpar_texto_historico(raw.get("sku"), 80),
        "titulo": _favoritos_limpar_texto_historico(raw.get("titulo"), 500),
        "loja": _favoritos_limpar_texto_historico(raw.get("loja"), 180),
        "usuario": _favoritos_limpar_texto_historico(raw.get("usuario") or raw.get("nome_usuario"), 160),
        "origem_ranking_id": _favoritos_limpar_texto_historico(raw.get("origem_ranking_id"), 120),
        "escopo": _favoritos_limpar_texto_historico(raw.get("escopo"), 40),
        "opcoes_promocao": _favoritos_normalizar_opcoes_promocao_historico(raw.get("opcoes_promocao")),
        "mensagem_final": _favoritos_limpar_texto_historico(raw.get("mensagem_final"), 1600),
        "relatorio_inicial": _favoritos_normalizar_relatorio_alteracao(raw.get("relatorio_inicial")),
        "relatorio_final": _favoritos_normalizar_relatorio_alteracao(raw.get("relatorio_final")),
        "vinculos": vinculos,
    }


def _favoritos_normalizar_historico(lista: Any) -> list[dict]:
    if not isinstance(lista, list):
        return []
    saida: list[dict] = []
    for entrada in lista:
        if not isinstance(entrada, dict):
            continue
        grupos_saida: list[dict] = []
        for grupo in (entrada.get("grupos") or [])[:80]:
            if not isinstance(grupo, dict):
                continue
            sku = _favoritos_limpar_texto_historico(grupo.get("sku"), 80)
            if not sku:
                continue
            anuncios_saida: list[dict] = []
            for anuncio in (grupo.get("anuncios") or [])[:FAVORITOS_HISTORICO_ANUNCIOS_MAX]:
                if not isinstance(anuncio, dict):
                    continue
                anuncio_id = _favoritos_limpar_texto_historico(
                    anuncio.get("id") or anuncio.get("mlb") or _extrair_item_id(str(anuncio.get("url") or "")),
                    40,
                )
                anuncios_saida.append({
                    "id": anuncio_id,
                    "historico_estatico": bool(anuncio.get("historico_estatico", True)),
                    "ranking_historico_estatico": bool(anuncio.get("ranking_historico_estatico", True)),
                    "bloquear_atualizacao_historico": bool(anuncio.get("bloquear_atualizacao_historico", True)),
                    "fonte_registro": _favoritos_limpar_texto_historico(anuncio.get("fonte_registro") or "historico_ranqueamento", 80),
                    "url": _favoritos_limpar_texto_historico(anuncio.get("url"), 800),
                    "titulo": _favoritos_limpar_texto_historico(anuncio.get("titulo") or anuncio.get("title"), 500),
                    "vendedor": _favoritos_limpar_texto_historico(anuncio.get("vendedor"), 160),
                    "vendas": _favoritos_numero_historico(anuncio.get("vendas")),
                    "vendasFonte": _favoritos_limpar_texto_historico(anuncio.get("vendasFonte") or anuncio.get("vendas_fonte"), 80),
                    "vendas_fonte": _favoritos_limpar_texto_historico(anuncio.get("vendas_fonte") or anuncio.get("vendasFonte"), 80),
                    "data_criacao": _favoritos_limpar_texto_historico(anuncio.get("data_criacao"), 80),
                    "imagem": _favoritos_limpar_texto_historico(anuncio.get("imagem") or anuncio.get("thumbnail"), 800),
                    "thumbnail": _favoritos_limpar_texto_historico(anuncio.get("thumbnail") or anuncio.get("imagem"), 800),
                    "preco": _favoritos_numero_historico(anuncio.get("preco")),
                    "price": _favoritos_numero_historico(anuncio.get("price")),
                    "preco_original": _favoritos_numero_historico(
                        anuncio.get("preco_original")
                        or anuncio.get("original_price")
                        or anuncio.get("regular_amount")
                        or anuncio.get("standard_price")
                    ),
                    "original_price": _favoritos_numero_historico(
                        anuncio.get("original_price")
                        or anuncio.get("preco_original")
                        or anuncio.get("regular_amount")
                        or anuncio.get("standard_price")
                    ),
                    "preco_promocional": _favoritos_numero_historico(
                        anuncio.get("preco_promocional")
                        or anuncio.get("promotional_price")
                        or anuncio.get("promotion_price")
                        or anuncio.get("discounted_price")
                    ),
                    "promotional_price": _favoritos_numero_historico(
                        anuncio.get("promotional_price")
                        or anuncio.get("preco_promocional")
                        or anuncio.get("promotion_price")
                        or anuncio.get("discounted_price")
                    ),
                    "discount_pct": _favoritos_numero_historico(anuncio.get("discount_pct") or anuncio.get("discount_percent") or anuncio.get("discount_percentage")),
                    "fonte_preco": _favoritos_limpar_texto_historico(anuncio.get("fonte_preco") or anuncio.get("preco_fonte") or anuncio.get("price_source"), 80),
                    "parcelamento_sem_juros": bool(anuncio.get("parcelamento_sem_juros")) if anuncio.get("parcelamento_sem_juros") is not None else None,
                    "tipo_anuncio": _favoritos_limpar_texto_historico(
                        anuncio.get("tipo_anuncio") or anuncio.get("tipoAnuncio") or anuncio.get("listing_type_name"),
                        80,
                    ),
                    "listing_type_id": _favoritos_limpar_texto_historico(
                        anuncio.get("listing_type_id") or anuncio.get("listingTypeId"),
                        80,
                    ),
                    "listing_type_name": _favoritos_limpar_texto_historico(
                        anuncio.get("listing_type_name") or anuncio.get("tipo_anuncio") or anuncio.get("tipoAnuncio"),
                        80,
                    ),
                    "logistic_type": _favoritos_limpar_texto_historico(
                        anuncio.get("logistic_type") or anuncio.get("logisticType") or anuncio.get("shipping_logistic_type"),
                        80,
                    ),
                    "shipping_mode": _favoritos_limpar_texto_historico(
                        anuncio.get("shipping_mode") or anuncio.get("shippingMode"),
                        80,
                    ),
                    "is_full": bool(anuncio.get("is_full") or anuncio.get("isFull") or anuncio.get("full")),
                    "media_vendas_mensal": _favoritos_numero_historico(anuncio.get("media_vendas_mensal")),
                    "meses_desde_criacao": _favoritos_numero_historico(anuncio.get("meses_desde_criacao")),
                    "pesquisas_origem": _favoritos_lista_texto_historico(anuncio.get("pesquisas_origem"), 220, 3),
                    "campos_origem": _favoritos_lista_texto_historico(anuncio.get("campos_origem"), 80, 3),
                    "motivo_ia": _favoritos_limpar_texto_historico(anuncio.get("motivo_ia") or anuncio.get("motivo"), 240),
                })
            removidos_ia_saida: list[dict] = []
            for anuncio in (grupo.get("removidos_ia") or grupo.get("removidosIa") or [])[:80]:
                if not isinstance(anuncio, dict):
                    continue
                anuncio_id = _favoritos_limpar_texto_historico(
                    anuncio.get("id") or anuncio.get("mlb") or _extrair_item_id(str(anuncio.get("url") or "")),
                    40,
                )
                removidos_ia_saida.append({
                    "id": anuncio_id,
                    "historico_estatico": bool(anuncio.get("historico_estatico", True)),
                    "ranking_historico_estatico": bool(anuncio.get("ranking_historico_estatico", True)),
                    "bloquear_atualizacao_historico": bool(anuncio.get("bloquear_atualizacao_historico", True)),
                    "fonte_registro": _favoritos_limpar_texto_historico(anuncio.get("fonte_registro") or "historico_ranqueamento", 80),
                    "url": _favoritos_limpar_texto_historico(anuncio.get("url"), 800),
                    "titulo": _favoritos_limpar_texto_historico(anuncio.get("titulo") or anuncio.get("title"), 500),
                    "vendedor": _favoritos_limpar_texto_historico(anuncio.get("vendedor"), 160),
                    "imagem": _favoritos_limpar_texto_historico(anuncio.get("imagem") or anuncio.get("thumbnail"), 800),
                    "thumbnail": _favoritos_limpar_texto_historico(anuncio.get("thumbnail") or anuncio.get("imagem"), 800),
                    "motivo_ia": _favoritos_limpar_texto_historico(anuncio.get("motivo_ia") or anuncio.get("motivo"), 240),
                })
            if not anuncios_saida and not removidos_ia_saida:
                continue
            termos_saida = []
            for termo in (grupo.get("termos") or [])[:3]:
                if not isinstance(termo, dict):
                    continue
                termos_saida.append({
                    "campo": _favoritos_limpar_texto_historico(termo.get("campo"), 40),
                    "termo": _favoritos_limpar_texto_historico(termo.get("termo"), 220),
                })
            opcoes_promocao_saida = None
            opcoes_promocao_raw = grupo.get("opcoes_promocao")
            if isinstance(opcoes_promocao_raw, dict):
                campanha_raw = opcoes_promocao_raw.get("campanha") if isinstance(opcoes_promocao_raw.get("campanha"), dict) else {}
                desconto_raw = opcoes_promocao_raw.get("desconto") if isinstance(opcoes_promocao_raw.get("desconto"), dict) else {}
                opcoes_promocao_saida = {
                    "usar_promocao": bool(opcoes_promocao_raw.get("usar_promocao")),
                    "campanha": {
                        "id": _favoritos_limpar_texto_historico(campanha_raw.get("id"), 80),
                        "nome": _favoritos_limpar_texto_historico(campanha_raw.get("nome"), 220),
                        "tipo": _favoritos_limpar_texto_historico(campanha_raw.get("tipo"), 80),
                        "status": _favoritos_limpar_texto_historico(campanha_raw.get("status"), 80),
                    },
                    "desconto": {
                        "modo": _favoritos_limpar_texto_historico(desconto_raw.get("modo"), 80),
                        "percentual": _favoritos_numero_historico(desconto_raw.get("percentual")),
                    },
                }
            grupos_saida.append({
                "sku": sku,
                "titulo": _favoritos_limpar_texto_historico(grupo.get("titulo"), 500),
                "historico_estatico": bool(grupo.get("historico_estatico", True)),
                "ranking_historico_estatico": bool(grupo.get("ranking_historico_estatico", True)),
                "bloquear_atualizacao_historico": bool(grupo.get("bloquear_atualizacao_historico", True)),
                "termos": termos_saida,
                "opcoes_promocao": opcoes_promocao_saida,
                "avulso": bool(grupo.get("avulso") or grupo.get("pesquisa_avulsa") or sku.strip().lower() == "avulso"),
                "pesquisa_avulsa": bool(grupo.get("pesquisa_avulsa") or grupo.get("avulso") or sku.strip().lower() == "avulso"),
                "total_anuncios": _favoritos_int_historico(grupo.get("total_anuncios"), len(anuncios_saida)),
                "usou_ia": bool(grupo.get("usou_ia")),
                "ia_confirmados": _favoritos_int_historico(grupo.get("ia_confirmados"), 0),
                "ia_max_confirmados": _favoritos_int_historico(grupo.get("ia_max_confirmados"), 0),
                "removidos_ia_total": _favoritos_int_historico(grupo.get("removidos_ia_total"), len(removidos_ia_saida)),
                "removidos_ia": removidos_ia_saida,
                "anuncios": anuncios_saida,
            })
        alteracoes_saida: list[dict] = []
        for alteracao in (entrada.get("alteracoes_favoritos") or [])[:80]:
            alteracao_norm = _favoritos_normalizar_alteracao_favoritos_historico(alteracao)
            if alteracao_norm:
                alteracoes_saida.append(alteracao_norm)
        if not grupos_saida and not alteracoes_saida:
            continue
        total_anuncios_padrao = sum(len(g["anuncios"]) for g in grupos_saida) + sum(len(a.get("vinculos") or []) for a in alteracoes_saida)
        skus_alteracao = {a.get("sku") for a in alteracoes_saida if a.get("sku")}
        entrada_saida = {
            "id": _favoritos_limpar_texto_historico(entrada.get("id"), 80) or f"hist_{len(saida) + 1}",
            "tipo": _favoritos_limpar_texto_historico(entrada.get("tipo"), 80),
            "data_iso": _favoritos_limpar_texto_historico(entrada.get("data_iso"), 80),
            "loja": _favoritos_limpar_texto_historico(entrada.get("loja"), 180),
            "usuario": _favoritos_limpar_texto_historico(
                entrada.get("usuario") or entrada.get("nome_usuario") or entrada.get("usuario_nome") or entrada.get("created_by_name") or entrada.get("criado_por_nome") or entrada.get("username") or entrada.get("created_by") or entrada.get("criado_por"),
                160,
            ),
            "nome_usuario": _favoritos_limpar_texto_historico(
                entrada.get("nome_usuario") or entrada.get("usuario_nome") or entrada.get("usuario") or entrada.get("created_by_name") or entrada.get("criado_por_nome") or entrada.get("username") or entrada.get("created_by") or entrada.get("criado_por"),
                160,
            ),
            "username": _favoritos_limpar_texto_historico(entrada.get("username") or entrada.get("created_by") or entrada.get("criado_por") or entrada.get("usuario"), 160),
            "total_skus": _favoritos_int_historico(entrada.get("total_skus"), len(grupos_saida) or len(skus_alteracao)),
            "total_anuncios": _favoritos_int_historico(entrada.get("total_anuncios"), total_anuncios_padrao),
            "grupos": grupos_saida,
        }
        if alteracoes_saida:
            entrada_saida["tipo"] = entrada_saida.get("tipo") or "alteracao_favoritos"
            entrada_saida["alteracoes_favoritos"] = alteracoes_saida
        saida.append(entrada_saida)
        if len(saida) >= FAVORITOS_HISTORICO_MAX:
            break
    return saida


FAVORITOS_HISTORICO_SQLITE_SCHEMA_VERSION = "1"
FAVORITOS_HISTORICO_SQLITE_TIMEOUT_S = 15
FAVORITOS_HISTORICO_SQLITE_LOCKS_LOCK = threading.RLock()
FAVORITOS_HISTORICO_SQLITE_LOCKS: dict[str, threading.RLock] = {}


def _favoritos_historico_lock_for_path(caminho: str):
    key = os.path.abspath(str(caminho or ""))
    with FAVORITOS_HISTORICO_SQLITE_LOCKS_LOCK:
        lock = FAVORITOS_HISTORICO_SQLITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            FAVORITOS_HISTORICO_SQLITE_LOCKS[key] = lock
        return lock


def _favoritos_arquivo_historico(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"favoritos_historico_{_favoritos_usuario_slug(username)}.db")


def _favoritos_arquivo_historico_json_legacy(client_id: str, username: str) -> str:
    return os.path.join(get_tenant_path(client_id), f"favoritos_historico_{_favoritos_usuario_slug(username)}.json")


def _favoritos_historico_sqlite_connect(caminho: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=FAVORITOS_HISTORICO_SQLITE_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return conn


def _favoritos_historico_ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favoritos_historico_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favoritos_historico_entries (
            id TEXT PRIMARY KEY,
            data_iso TEXT,
            loja TEXT,
            usuario TEXT,
            nome_usuario TEXT,
            username TEXT,
            tipo TEXT,
            total_skus INTEGER,
            total_anuncios INTEGER,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_favoritos_historico_data_iso ON favoritos_historico_entries(data_iso DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_favoritos_historico_loja ON favoritos_historico_entries(loja)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_favoritos_historico_username ON favoritos_historico_entries(username)")
    conn.execute(
        "INSERT OR REPLACE INTO favoritos_historico_meta (key, value) VALUES (?, ?)",
        ("schema_version", FAVORITOS_HISTORICO_SQLITE_SCHEMA_VERSION),
    )


def _favoritos_historico_payload_json(entrada: dict) -> str:
    return json.dumps(entrada or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _favoritos_historico_hash_payload(entrada: dict) -> str:
    return hashlib.sha256(_favoritos_historico_payload_json(entrada).encode("utf-8")).hexdigest()


def _favoritos_historico_key(entrada: dict) -> str:
    if not isinstance(entrada, dict):
        return hashlib.sha256(json.dumps(entrada, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    base = str(entrada.get("id") or "").strip()
    if base:
        return base
    return _favoritos_historico_hash_payload(entrada)


def _favoritos_historico_meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM favoritos_historico_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _favoritos_historico_meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO favoritos_historico_meta (key, value) VALUES (?, ?)",
        (str(key or ""), str(value if value is not None else "")),
    )


def _favoritos_historico_read_json_legacy(caminho_json: str, username: str) -> dict:
    if not os.path.exists(caminho_json):
        return {"historico": [], "updated_at": None}
    with open(caminho_json, "r", encoding="utf-8") as f:
        dados = json.load(f)
    if isinstance(dados, dict):
        historico = _favoritos_normalizar_historico(dados.get("historico") or [])
        updated_at = dados.get("updated_at")
    elif isinstance(dados, list):
        historico = _favoritos_normalizar_historico(dados)
        updated_at = None
    else:
        historico = []
        updated_at = None
    _favoritos_aplicar_usuario_padrao_historico(historico, username)
    return {"historico": historico, "updated_at": updated_at}


def _favoritos_historico_write_conn(conn: sqlite3.Connection, historico: Any, updated_at: Optional[str] = None) -> dict:
    historico_normalizado = _favoritos_normalizar_historico(historico)
    agora = updated_at or datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM favoritos_historico_entries")
    for idx, entrada in enumerate(historico_normalizado):
        entrada_id = _favoritos_historico_key(entrada)
        payload_json = _favoritos_historico_payload_json(entrada)
        conn.execute(
            """
            INSERT OR REPLACE INTO favoritos_historico_entries (
                id, data_iso, loja, usuario, nome_usuario, username, tipo,
                total_skus, total_anuncios, payload_json, payload_hash,
                sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM favoritos_historico_entries WHERE id = ?),
                ?
            ), ?)
            """,
            (
                entrada_id,
                entrada.get("data_iso") or "",
                entrada.get("loja") or "",
                entrada.get("usuario") or "",
                entrada.get("nome_usuario") or "",
                entrada.get("username") or "",
                entrada.get("tipo") or "",
                _favoritos_int_historico(entrada.get("total_skus"), 0),
                _favoritos_int_historico(entrada.get("total_anuncios"), 0),
                payload_json,
                hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                idx,
                entrada_id,
                agora,
                agora,
            ),
        )
    _favoritos_historico_meta_set(conn, "updated_at", agora)
    return {"historico": historico_normalizado, "updated_at": agora}


def _favoritos_historico_payload_from_conn(conn: sqlite3.Connection) -> dict:
    _favoritos_historico_ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT payload_json
        FROM favoritos_historico_entries
        ORDER BY
            CASE WHEN data_iso IS NULL OR data_iso = '' THEN 1 ELSE 0 END,
            data_iso DESC,
            sort_order ASC
        """
    ).fetchall()
    historico = []
    for row in rows:
        try:
            item = json.loads(row["payload_json"] or "{}")
        except Exception:
            continue
        if isinstance(item, dict):
            historico.append(item)
    historico = _favoritos_normalizar_historico(historico)
    return {
        "historico": historico,
        "updated_at": _favoritos_historico_meta_get(conn, "updated_at"),
    }


def _favoritos_historico_payload_from_sqlite_path(caminho: str) -> dict:
    if not os.path.exists(caminho):
        return {"historico": [], "updated_at": None}
    with _favoritos_historico_lock_for_path(caminho):
        conn = _favoritos_historico_sqlite_connect(caminho)
        try:
            return _favoritos_historico_payload_from_conn(conn)
        finally:
            conn.close()


def _favoritos_historico_bytes_is_sqlite(data: bytes) -> bool:
    return bytes(data or b"")[:16] == b"SQLite format 3\x00"


def _favoritos_historico_payload_from_sqlite_bytes(data: bytes, rel: str = "") -> dict:
    if not data:
        return {"historico": [], "updated_at": None}
    fd, tmp_path = tempfile.mkstemp(prefix="favoritos_historico_sync_", suffix=".db")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(data or b"")
        return _favoritos_historico_payload_from_sqlite_path(tmp_path)
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=502, detail=f"SQLite invalido no historico de favoritos ({rel or 'pacote'}): {exc}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _favoritos_historico_payload_from_bytes(data: bytes, rel: str = "", username: str = "") -> dict:
    lower = str(rel or "").lower()
    if lower.endswith((".db", ".sqlite", ".sqlite3")) or _favoritos_historico_bytes_is_sqlite(data):
        return _favoritos_historico_payload_from_sqlite_bytes(data, rel)
    try:
        payload = json.loads((data or b"").decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Arquivo de historico invalido no pacote ({rel}): {exc}") from exc
    if isinstance(payload, dict):
        historico = _favoritos_normalizar_historico(payload.get("historico") or [])
        updated_at = payload.get("updated_at")
    else:
        historico = _favoritos_normalizar_historico(payload or [])
        updated_at = None
    _favoritos_aplicar_usuario_padrao_historico(historico, username)
    return {"historico": historico, "updated_at": updated_at}


def _favoritos_historico_sqlite_bytes_from_payload(payload: dict) -> bytes:
    fd, tmp_path = tempfile.mkstemp(prefix="favoritos_historico_payload_", suffix=".db")
    os.close(fd)
    try:
        conn = _favoritos_historico_sqlite_connect(tmp_path)
        try:
            _favoritos_historico_ensure_schema(conn)
            _favoritos_historico_write_conn(conn, (payload or {}).get("historico") or [], (payload or {}).get("updated_at"))
            conn.commit()
        finally:
            conn.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _favoritos_historico_json_sha256(caminho_json: str) -> str:
    sha = hashlib.sha256()
    with open(caminho_json, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _favoritos_historico_merge_listas(base: list[dict], nova: list[dict]) -> list[dict]:
    por_id: dict[str, dict] = {}
    for entrada in _favoritos_normalizar_historico(base or []):
        por_id[_favoritos_historico_key(entrada)] = entrada
    for entrada in _favoritos_normalizar_historico(nova or []):
        chave = _favoritos_historico_key(entrada)
        atual = por_id.get(chave)
        data_nova = str(entrada.get("data_iso") or entrada.get("updated_at") or "")
        data_atual = str((atual or {}).get("data_iso") or (atual or {}).get("updated_at") or "")
        if not atual or data_nova >= data_atual:
            por_id[chave] = entrada
    return sorted(por_id.values(), key=lambda item: str((item or {}).get("data_iso") or ""), reverse=True)[:FAVORITOS_HISTORICO_MAX]


def _favoritos_migrar_historico_json_para_sqlite(client_id: str, username: str) -> None:
    caminho_json = _favoritos_arquivo_historico_json_legacy(client_id, username)
    if not os.path.exists(caminho_json):
        return
    caminho_db = _favoritos_arquivo_historico(client_id, username)
    try:
        json_hash = _favoritos_historico_json_sha256(caminho_json)
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha ao calcular hash do historico legado %s: %s", caminho_json, exc)
        return
    with _favoritos_historico_lock_for_path(caminho_db):
        conn = _favoritos_historico_sqlite_connect(caminho_db)
        try:
            _favoritos_historico_ensure_schema(conn)
            if _favoritos_historico_meta_get(conn, "legacy_json_sha256") == json_hash:
                conn.commit()
                return
            legado = _favoritos_historico_read_json_legacy(caminho_json, username)
            atual = _favoritos_historico_payload_from_conn(conn)
            historico = _favoritos_historico_merge_listas(
                (atual or {}).get("historico") or [],
                (legado or {}).get("historico") or [],
            )
            updated_at = (atual or {}).get("updated_at") or (legado or {}).get("updated_at") or datetime.now().isoformat(timespec="seconds")
            _favoritos_historico_write_conn(conn, historico, updated_at)
            _favoritos_historico_meta_set(conn, "legacy_json_sha256", json_hash)
            _favoritos_historico_meta_set(conn, "legacy_json_imported_at", datetime.now().isoformat(timespec="seconds"))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("[Favoritos ML] Falha ao migrar historico legado do usuario %s para SQLite: %s", username, exc)
        finally:
            conn.close()


def _favoritos_carregar_historico(client_id: str, username: str) -> dict:
    caminho = _favoritos_arquivo_historico(client_id, username)
    _favoritos_migrar_historico_json_para_sqlite(client_id, username)
    try:
        payload = _favoritos_historico_payload_from_sqlite_path(caminho)
        _favoritos_aplicar_usuario_padrao_historico(payload.get("historico") or [], username)
        return payload
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha ao carregar historico do usuario %s: %s", username, exc)
    return {"historico": [], "updated_at": None}


def _favoritos_aplicar_usuario_padrao_historico(historico_normalizado: list[dict], username: str) -> None:
    usuario_padrao = _favoritos_limpar_texto_historico(username, 160)
    if not usuario_padrao:
        return
    for entrada in historico_normalizado:
        if not isinstance(entrada, dict):
            continue
        if not entrada.get("usuario"):
            entrada["usuario"] = usuario_padrao
        if not entrada.get("nome_usuario"):
            entrada["nome_usuario"] = entrada.get("usuario") or usuario_padrao
        if not entrada.get("username"):
            entrada["username"] = usuario_padrao


def _favoritos_salvar_historico(client_id: str, username: str, historico: Any) -> dict:
    caminho = _favoritos_arquivo_historico(client_id, username)
    _favoritos_migrar_historico_json_para_sqlite(client_id, username)
    historico_normalizado = _favoritos_normalizar_historico(historico)
    _favoritos_aplicar_usuario_padrao_historico(historico_normalizado, username)
    with _favoritos_historico_lock_for_path(caminho):
        conn = _favoritos_historico_sqlite_connect(caminho)
        try:
            _favoritos_historico_ensure_schema(conn)
            payload = _favoritos_historico_write_conn(conn, historico_normalizado)
            conn.commit()
            return payload
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _favoritos_usuario_pode_sync_historico(usuario: dict, client_id: str) -> bool:
    if not isinstance(usuario, dict):
        return False
    usuario_client = _shared_sync_normalizar_client_id(usuario.get("client_id") or "default")
    if usuario_client != _shared_sync_normalizar_client_id(client_id):
        return False
    if not _login_usuario_ativo(usuario):
        return False
    validade_ok, _msg = _login_validade_ok(usuario)
    if not validade_ok:
        return False
    permissoes = _normalizar_permissoes(usuario.get("permissions") or {})
    return bool(permissoes.get("full") or permissoes.get("favoritos"))


def _favoritos_usuarios_sync_historico(client_id: str) -> list[str]:
    try:
        usuarios, _ws, _headers = carregar_usuarios_sheets()
    except Exception as exc:
        logger.warning("[Favoritos Sync] Falha ao carregar usuarios para sync realtime: %s", exc)
        usuarios = {}
    saida = []
    if isinstance(usuarios, dict):
        for username, usuario in usuarios.items():
            user = dict(usuario or {})
            user["username"] = str(user.get("username") or username or "").strip().lower()
            if not user["username"]:
                continue
            if _favoritos_usuario_pode_sync_historico(user, client_id):
                saida.append(user["username"])
    return sorted(set(saida))


def _favoritos_historico_payload_bytes(payload: dict) -> bytes:
    return _favoritos_historico_sqlite_bytes_from_payload(payload or {})


def _favoritos_mesclar_historico_usuario_com_fontes(client_id: str, username: str, fontes: list[tuple[str, bytes]]) -> dict:
    antes = _favoritos_carregar_historico(client_id, username)
    assinatura_antes = [
        _shared_sync_historico_key(item)
        for item in _favoritos_normalizar_historico((antes or {}).get("historico") or [])
    ]
    depois = _shared_sync_merge_historico_usuario(client_id, username, fontes)
    assinatura_depois = [
        _shared_sync_historico_key(item)
        for item in _favoritos_normalizar_historico((depois or {}).get("historico") or [])
    ]
    return {
        "payload": depois,
        "changed": assinatura_depois != assinatura_antes,
        "item_count": len(assinatura_depois),
    }


def _favoritos_propagar_historico_para_usuarios(client_id: str, source_username: str, payload: dict) -> dict:
    client_norm = _shared_sync_normalizar_client_id(client_id)
    source_norm = _shared_sync_normalizar_username(source_username)
    usuarios = _favoritos_usuarios_sync_historico(client_norm)
    if source_norm not in usuarios:
        return {
            "eligible_users": usuarios,
            "updated_users": [],
            "failed": [],
            "skipped": "source_without_favoritos_permission",
        }
    fonte_rel = f"favoritos_historico_{_favoritos_usuario_slug(source_norm)}.db"
    fonte_bytes = _favoritos_historico_payload_bytes(payload)
    atualizados = []
    falhas = []
    for username in usuarios:
        if _shared_sync_normalizar_username(username) == source_norm:
            continue
        try:
            resultado = _favoritos_mesclar_historico_usuario_com_fontes(client_norm, username, [(fonte_rel, fonte_bytes)])
            if resultado.get("changed"):
                atualizados.append(username)
        except Exception as exc:
            logger.warning("[Favoritos Sync] Falha ao propagar historico de %s para %s: %s", source_norm, username, exc)
            falhas.append({"username": username, "error": str(exc)})
    return {
        "eligible_users": usuarios,
        "updated_users": atualizados,
        "failed": falhas,
    }


def _favoritos_reconciliar_historico_usuario(client_id: str, username: str) -> dict:
    client_norm = _shared_sync_normalizar_client_id(client_id)
    username_norm = _shared_sync_normalizar_username(username)
    usuarios = _favoritos_usuarios_sync_historico(client_norm)
    if username_norm not in usuarios:
        atual = _favoritos_carregar_historico(client_norm, username_norm)
        return {
            "payload": atual,
            "changed": False,
            "source_count": 0,
            "item_count": len(atual.get("historico") or []),
            "skipped": "user_without_favoritos_permission",
        }
    fontes = []
    for origem in usuarios:
        origem_norm = _shared_sync_normalizar_username(origem)
        if not origem_norm:
            continue
        payload = _favoritos_carregar_historico(client_norm, origem_norm)
        historico = _favoritos_normalizar_historico((payload or {}).get("historico") or [])
        if not historico:
            continue
        rel = f"favoritos_historico_{_favoritos_usuario_slug(origem_norm)}.db"
        fontes.append((rel, _favoritos_historico_payload_bytes({"historico": historico, "updated_at": payload.get("updated_at")})))
    if not fontes:
        atual = _favoritos_carregar_historico(client_norm, username_norm)
        return {"payload": atual, "changed": False, "source_count": 0, "item_count": len(atual.get("historico") or [])}
    resultado = _favoritos_mesclar_historico_usuario_com_fontes(client_norm, username_norm, fontes)
    resultado["source_count"] = len(fontes)
    return resultado


def _favoritos_historico_realtime_event_path(client_id: str) -> str:
    client_key = _firebase_presence_client_key(_shared_sync_normalizar_client_id(client_id))
    return f"{_firebase_presence_root_path()}/clients/{client_key}/events/favoritos_historico"


def _favoritos_historico_publicar_evento_realtime(client_id: str, source_username: str, meta: Optional[dict] = None) -> bool:
    if not _firebase_deve_usar() or firebase_realtime_db is None:
        return False
    app_fb = _firebase_app()
    if app_fb is None:
        return False
    try:
        payload = {
            "event_id": uuid.uuid4().hex,
            "scope": FAVORITOS_HISTORICO_REALTIME_SCOPE,
            "client_id": _shared_sync_normalizar_client_id(client_id),
            "source_username": _shared_sync_normalizar_username(source_username),
            "updated_at": _shared_sync_now_iso(),
            "updated_ts": int(time.time()),
            "meta": meta or {},
        }
        ref = firebase_realtime_db.reference(_favoritos_historico_realtime_event_path(client_id), app=app_fb)
        ref.set(payload)
        return True
    except Exception as exc:
        logger.warning("[Favoritos Sync] Falha ao publicar evento realtime: %s", exc)
        return False


def _favoritos_carregar_cadastro_por_sku(client_id: str) -> tuple[dict[str, dict], str]:
    cadastro_path, _estoque_path = _favoritos_sku_caminhos(client_id)
    if not cadastro_path or not os.path.exists(cadastro_path):
        return {}, cadastro_path
    try:
        df = _favoritos_ler_cadastro_csv(cadastro_path)
        if "sku" not in df.columns:
            return {}, cadastro_path
        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df, _ = _consolidar_cadastro_por_sku(df)
        mapa = {}
        for _, row in df.iterrows():
            item = row.to_dict()
            sku = str(item.get("sku") or "").strip()
            if sku and sku not in mapa:
                mapa[sku] = item
        return mapa, cadastro_path
    except Exception as exc:
        logger.warning("[Favoritos SKU] Falha ao ler cadastro: %s", exc)
        return {}, cadastro_path


def _favoritos_chaves_match_sku(sku: Any) -> list[str]:
    """Chaves tolerantes para casar SKU do Mercado Livre com o cadastro interno."""
    bruto = str(sku or "").strip()
    if not bruto:
        return []
    candidatos = [
        bruto,
        _normalizar_sku_match_favoritos(bruto),
        _normalizar_sku_compacto_favoritos(bruto),
    ]
    vistos: set[str] = set()
    chaves: list[str] = []
    for candidato in candidatos:
        chave = str(candidato or "").strip().upper()
        if chave and chave not in vistos:
            vistos.add(chave)
            chaves.append(chave)
    return chaves


def _favoritos_carregar_estoque_loja_por_sku(client_id: str, nome_loja: str) -> dict[str, dict]:
    """Lê produtos_compilado.csv e retorna saldo_loja apenas da loja selecionada."""
    _cadastro_path, estoque_path = _favoritos_sku_caminhos(client_id)
    if not estoque_path or not os.path.exists(estoque_path):
        return {}

    loja_alvo = _favoritos_sku_norm_loja(nome_loja)
    try:
        df = pd.read_csv(estoque_path, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha ao ler estoque da loja %s: %s", nome_loja, exc)
        return {}

    if "sku" not in df.columns:
        return {}
    if "loja_sync" not in df.columns and "loja" in df.columns:
        df["loja_sync"] = df["loja"]
    if "loja_sync" not in df.columns:
        df["loja_sync"] = ""

    mapa: dict[str, dict] = {}
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        loja_sync = str(row_dict.get("loja_sync") or "").strip()
        if loja_alvo and _favoritos_sku_norm_loja(loja_sync) != loja_alvo:
            continue

        sku = _normalizar_sku_match_favoritos(row_dict.get("sku") or "")
        if not sku:
            continue

        saldo_loja = _favoritos_sku_float(
            row_dict.get("saldo_loja")
            or row_dict.get("estoque_loja")
            or row_dict.get("saldo")
            or row_dict.get("saldo_total")
        )
        saldo_full = _favoritos_sku_float(row_dict.get("saldo_full") or row_dict.get("estoque_full"))
        registro = {
            "sku": sku,
            "loja_sync": loja_sync,
            "saldo_loja": saldo_loja,
            "saldo_full": saldo_full,
            "last_update": str(row_dict.get("last_update") or row_dict.get("updated_at") or "").strip(),
            "id_bling": str(row_dict.get("id_bling") or "").strip(),
            "nome_bling": str(row_dict.get("nome_bling") or row_dict.get("produto") or row_dict.get("nome") or "").strip(),
        }

        for chave in _favoritos_chaves_match_sku(sku):
            atual = mapa.get(chave)
            if atual:
                atual["saldo_loja"] = _favoritos_sku_float(atual.get("saldo_loja")) + saldo_loja
                atual["saldo_full"] = _favoritos_sku_float(atual.get("saldo_full")) + saldo_full
                if not atual.get("last_update"):
                    atual["last_update"] = registro["last_update"]
                if not atual.get("nome_bling"):
                    atual["nome_bling"] = registro["nome_bling"]
            else:
                mapa[chave] = dict(registro)
    return mapa


def _favoritos_ml_enriquecer_estoque_cadastro(
    client_id: str,
    nome_loja: str,
    skus: list[dict],
) -> list[dict]:
    estoque_por_sku = _favoritos_carregar_estoque_loja_por_sku(client_id, nome_loja)
    for item_sku in skus or []:
        if not isinstance(item_sku, dict):
            continue
        estoque = None
        for chave in _favoritos_chaves_match_sku(item_sku.get("sku")):
            estoque = estoque_por_sku.get(chave)
            if estoque:
                break

        saldo_loja = _favoritos_sku_float((estoque or {}).get("saldo_loja"), 0.0)
        saldo_full = _favoritos_sku_float((estoque or {}).get("saldo_full"), 0.0)
        item_sku["saldo_loja"] = saldo_loja
        item_sku["estoque_loja"] = saldo_loja
        item_sku["estoque_cadastro_loja"] = saldo_loja
        item_sku["saldo_full"] = saldo_full
        item_sku["estoque_full"] = saldo_full
        item_sku["estoque_cadastro_encontrado"] = bool(estoque)
        item_sku["estoque_fonte"] = "cadastro_estoque" if estoque else "cadastro_estoque_nao_encontrado"
        item_sku["estoque_cadastro_atualizado_em"] = (estoque or {}).get("last_update") or ""
        if estoque and estoque.get("sku"):
            sku_canonico = str(estoque.get("sku") or "").strip()
            sku_atual = str(item_sku.get("sku") or "").strip()
            if sku_canonico and sku_atual and sku_canonico.upper() != sku_atual.upper():
                item_sku.setdefault("sku_ml", sku_atual)
                item_sku.setdefault("sku_original_ml", sku_atual)
                item_sku["sku"] = sku_canonico
        if estoque and estoque.get("id_bling"):
            item_sku["id_bling"] = estoque.get("id_bling")
        if estoque and estoque.get("nome_bling"):
            item_sku["nome_bling"] = estoque.get("nome_bling")
    return skus


def _favoritos_listar_skus_payload(client_id: str) -> dict:
    lojas_validas = _lojas_favoritos_com_bling_ml(client_id)
    lojas_payload = [
        {"nome": str(loja.get("nome") or "").strip()}
        for loja in lojas_validas
        if str(loja.get("nome") or "").strip()
    ]
    lojas_norm = {_favoritos_sku_norm_loja(loja["nome"]) for loja in lojas_payload}
    if not lojas_norm:
        return {"success": True, "lojas": lojas_payload, "skus": [], "total": 0}

    cadastro_por_sku, _cadastro_path = _favoritos_carregar_cadastro_por_sku(client_id)
    _cadastro_path, estoque_path = _favoritos_sku_caminhos(client_id)
    if not estoque_path or not os.path.exists(estoque_path):
        return {"success": True, "lojas": lojas_payload, "skus": [], "total": 0}

    try:
        df = pd.read_csv(estoque_path, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar SKUs do estoque: {exc}")

    if "sku" not in df.columns:
        return {"success": True, "lojas": lojas_payload, "skus": [], "total": 0}
    if "loja_sync" not in df.columns and "loja" in df.columns:
        df["loja_sync"] = df["loja"]
    if "loja_sync" not in df.columns:
        df["loja_sync"] = ""

    itens: list[dict] = []
    vistos: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        sku = _normalizar_sku_mes(row_dict.get("sku") or "")
        loja_sync = str(row_dict.get("loja_sync") or "").strip()
        if not sku or not loja_sync:
            continue
        if lojas_norm and _favoritos_sku_norm_loja(loja_sync) not in lojas_norm:
            continue

        chave = (_favoritos_sku_norm_loja(loja_sync), sku.lower())
        if chave in vistos:
            continue
        vistos.add(chave)

        cadastro = cadastro_por_sku.get(sku) or {}
        nome = (
            _favoritos_sku_pick(cadastro, ["nome", "produto", "produto_bling", "titulo"])
            or _favoritos_sku_pick(row_dict, ["nome_bling", "produto", "nome", "titulo"])
            or "-"
        )
        descricao_salva = _favoritos_sku_pick(cadastro, ["descricao", "descrição", "description"])
        itens.append({
            "sku": sku,
            "nome": nome,
            "produto": nome,
            "loja_sync": loja_sync,
            "loja": loja_sync,
            "id_bling": _favoritos_sku_pick(row_dict, ["id_bling"]),
            "nome_bling": _favoritos_sku_pick(row_dict, ["nome_bling"]),
            "saldo_loja": _favoritos_sku_float(row_dict.get("saldo_loja") or row_dict.get("estoque_loja") or row_dict.get("saldo")),
            "pesquisa_1": _favoritos_sku_pick(cadastro, ["pesquisa_1", "pesquisa1", "pesquisa 1"]),
            "pesquisa_2": _favoritos_sku_pick(cadastro, ["pesquisa_2", "pesquisa2", "pesquisa 2"]),
            "pesquisa_3": _favoritos_sku_pick(cadastro, ["pesquisa_3", "pesquisa3", "pesquisa 3"]),
            "descricao_ml": descricao_salva,
            "descricao_ml_status": "ok" if descricao_salva else "",
        })

    itens.sort(key=lambda item: (
        str(item.get("loja_sync") or "").lower(),
        str(item.get("sku") or "").lower(),
    ))
    return {"success": True, "lojas": lojas_payload, "skus": itens, "total": len(itens)}


def _favoritos_salvar_descricao_cadastro(client_id: str, sku: str, descricao: str) -> None:
    sku_norm = _normalizar_sku_match_favoritos(sku)
    if not sku_norm:
        return

    cadastro_path, _ = _favoritos_sku_caminhos(client_id)
    if not cadastro_path:
        return

    descricao_limpa = str(descricao or "").strip()
    if not descricao_limpa:
        return

    base_cols = _cadastro_cols_base()

    try:
        if os.path.exists(cadastro_path):
            df = _favoritos_ler_cadastro_csv(cadastro_path, base_cols)
            if "sku" not in df.columns:
                df["sku"] = ""
        else:
            df = pd.DataFrame(columns=base_cols)

        df = _cadastro_garantir_colunas_pesquisa(df)

        for c in base_cols:
            if c not in df.columns:
                df[c] = ""

        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df, _ = _consolidar_cadastro_por_sku(df)

        mask = df["sku"].astype(str).str.strip().str.lower() == sku_norm.lower()
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        if mask.any():
            df.loc[mask, "descricao"] = descricao_limpa
            df.loc[mask, "updated_at"] = agora
        else:
            nova_linha = {c: "" for c in df.columns}
            nova_linha["sku"] = sku_norm
            nova_linha["descricao"] = descricao_limpa
            nova_linha["updated_at"] = agora
            df = pd.concat([df, pd.DataFrame([nova_linha])], ignore_index=True)

        df, _ = _consolidar_cadastro_por_sku(df)
        os.makedirs(os.path.dirname(cadastro_path), exist_ok=True)
        df.to_csv(cadastro_path, index=False)
    except Exception as exc:
        logger.warning("[Favoritos SKU] Falha ao salvar descricao no cadastro do SKU %s: %s", sku_norm, exc)


def _favoritos_salvar_pesquisas_batch(
    client_id: str,
    itens: list[dict],
    preencher_nome_quando_vazio: bool = True,
) -> list[dict]:
    """Persiste Pesquisa 1/2/3 de diversos SKUs no cadastro do cliente."""
    if not itens:
        return []

    cadastro_path, _ = _favoritos_sku_caminhos(client_id)
    if not cadastro_path:
        return []

    registros: dict[str, dict[str, str]] = {}
    for item in itens:
        sku_norm = _normalizar_sku_match_favoritos(str((item or {}).get("sku") or ""))
        if not sku_norm:
            continue
        sku_norm = str(sku_norm)
        produto = _favoritos_limpar_nome_produto((item or {}).get("produto") or (item or {}).get("nome") or "")
        pesquisa_1 = str((item or {}).get("pesquisa_1") or "").strip()
        pesquisa_2 = str((item or {}).get("pesquisa_2") or "").strip()
        pesquisa_3 = str((item or {}).get("pesquisa_3") or "").strip()
        registros[sku_norm] = {
            "sku": sku_norm,
            "nome": produto,
            "pesquisa_1": pesquisa_1,
            "pesquisa_2": pesquisa_2,
            "pesquisa_3": pesquisa_3,
        }
    if not registros:
        return []

    try:
        if os.path.exists(cadastro_path):
            df = _favoritos_ler_cadastro_csv(cadastro_path, ["sku", "nome", "pesquisa_1", "pesquisa_2", "pesquisa_3"])
            if "sku" not in df.columns:
                df["sku"] = ""
        else:
            df = pd.DataFrame(columns=["sku", "nome", "pesquisa_1", "pesquisa_2", "pesquisa_3"])

        for c in ("sku", "nome", "pesquisa_1", "pesquisa_2", "pesquisa_3"):
            if c not in df.columns:
                df[c] = ""

        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df, _ = _consolidar_cadastro_por_sku(df)

        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        atualizados: list[dict] = []

        for sku_norm, valor in registros.items():
            mask = df["sku"].astype(str).str.strip().str.lower() == sku_norm.lower()
            produto = valor.get("nome", "")
            pesquisa_1 = valor.get("pesquisa_1", "")
            pesquisa_2 = valor.get("pesquisa_2", "")
            pesquisa_3 = valor.get("pesquisa_3", "")

            if mask.any():
                idx = df.index[mask][0]
                if preencher_nome_quando_vazio and produto and not str(df.at[idx, "nome"] or "").strip():
                    df.at[idx, "nome"] = produto
                df.at[idx, "pesquisa_1"] = pesquisa_1
                df.at[idx, "pesquisa_2"] = pesquisa_2
                df.at[idx, "pesquisa_3"] = pesquisa_3
                if "updated_at" in df.columns:
                    df.at[idx, "updated_at"] = agora
                atualizados.append({
                    "sku": sku_norm,
                    "pesquisa_1": pesquisa_1,
                    "pesquisa_2": pesquisa_2,
                    "pesquisa_3": pesquisa_3,
                })
                continue

            linha_nova = {c: "" for c in df.columns}
            linha_nova["sku"] = sku_norm
            linha_nova["nome"] = produto
            linha_nova["pesquisa_1"] = pesquisa_1
            linha_nova["pesquisa_2"] = pesquisa_2
            linha_nova["pesquisa_3"] = pesquisa_3
            if "updated_at" in df.columns:
                linha_nova["updated_at"] = agora
            df = pd.concat([df, pd.DataFrame([linha_nova])], ignore_index=True)
            atualizados.append({
                "sku": sku_norm,
                "pesquisa_1": pesquisa_1,
                "pesquisa_2": pesquisa_2,
                "pesquisa_3": pesquisa_3,
            })

        df, _ = _consolidar_cadastro_por_sku(df)
        os.makedirs(os.path.dirname(cadastro_path), exist_ok=True)
        df.to_csv(cadastro_path, index=False)
        return atualizados
    except Exception as exc:
        logger.warning("[Favoritos SKU] Falha ao salvar pesquisas em lote no cadastro: %s", exc)
        raise


def _favoritos_gerar_pesquisas_ia(
    client_id: str,
    itens: list[dict],
    limite_por_chamada: int | None = None,
    model: str | None = None,
) -> list[dict]:
    if not itens:
        return []

    itens_normalizados: list[dict] = []
    for item in itens:
        sku = _normalizar_sku_match_favoritos(str((item or {}).get("sku") or "")).strip()
        if not sku:
            continue
        itens_normalizados.append({
            "sku": sku,
            "produto": str((item or {}).get("titulo") or (item or {}).get("produto") or "").strip(),
            "descricao": str((item or {}).get("descricao") or "").strip(),
        })

    if not itens_normalizados:
        return []

    resultados = []
    for item in itens_normalizados:
        produto = str(item.get("produto") or "")
        descricao = str(item.get("descricao") or "")
        erros_item = []
        pesquisa_1 = ""
        try:
            pesquisa_1 = _favoritos_gerar_campo_pesquisa_ia(client_id, model, produto, descricao, 1)
        except Exception as exc:
            erros_item.append(f"Pesquisa 1: {exc}")
            logger.warning("[Favoritos IA] Falha ao gerar Pesquisa 1 do SKU %s: %s", item.get("sku"), exc)
        pesquisa_2 = _favoritos_codigo_pesquisa_2(produto, descricao)
        if not pesquisa_2:
            try:
                pesquisa_2 = _favoritos_gerar_campo_pesquisa_ia(client_id, model, produto, descricao, 2)
            except Exception as exc:
                erros_item.append(f"Pesquisa 2: {exc}")
                logger.warning("[Favoritos IA] Falha ao gerar Pesquisa 2 do SKU %s: %s", item.get("sku"), exc)
        pesquisa_3 = ""
        try:
            pesquisa_3 = _favoritos_gerar_campo_pesquisa_ia(client_id, model, produto, descricao, 3)
        except Exception as exc:
            erros_item.append(f"Pesquisa 3: {exc}")
            logger.warning("[Favoritos IA] Falha ao gerar Pesquisa 3 do SKU %s: %s", item.get("sku"), exc)

        resultados.append({
            "sku": item.get("sku"),
            "produto": produto,
            "pesquisa_1": pesquisa_1,
            "pesquisa_2": pesquisa_2,
            "pesquisa_3": pesquisa_3,
            "fonte": "ia" if (pesquisa_1 or pesquisa_2 or pesquisa_3) else "erro",
            "erro": "; ".join(erros_item)[:500],
        })
    return resultados


def _favoritos_ranking_salvar_decisoes_cache(client_id: str, sku: str, dados: dict) -> None:
    caminho = _favoritos_ranking_decisoes_cache_path(client_id, sku)
    tmp = caminho + ".tmp"
    dados = dados if isinstance(dados, dict) else {}
    dados["sku"] = str(sku or "").strip()
    dados["updated_at"] = datetime.now().isoformat(timespec="seconds")
    dados.setdefault("decisoes", {})
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)


def _favoritos_busca_externa_salvar_cache(client_id: str, sku: str, dados: dict) -> None:
    caminho = _favoritos_busca_externa_cache_path(client_id, sku)
    tmp = caminho + ".tmp"
    dados = dados if isinstance(dados, dict) else {}
    dados["sku"] = str(sku or "").strip()
    dados["updated_at"] = datetime.now().isoformat(timespec="seconds")
    dados.setdefault("consultas", {})
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)

PEER_EXPORTS = ['_normalizar_sku_match_favoritos', '_chave_loja_favoritos', '_lojas_favoritos_com_bling_ml', '_lojas_favoritos_com_ml', '_favoritos_sku_norm_loja', '_favoritos_sku_float', '_favoritos_sku_pick', '_favoritos_limpar_nome_produto', '_cadastro_nome_esta_suspeito', '_cadastro_limpar_nome', '_favoritos_ler_cadastro_csv', 'CADASTRO_PESQUISA_COLS', 'CADASTRO_COLS_BASE', 'CADASTRO_DESCRICAO_COL_ALIASES', '_cadastro_cols_base', '_cadastro_norm_coluna_texto', '_cadastro_canonizar_coluna_descricao', '_cadastro_garantir_colunas_pesquisa', '_favoritos_sku_caminhos', '_favoritos_ml_skus_anuncios_cache_id', '_favoritos_ml_anuncios_sku_cache_id', '_favoritos_arquivo_pesquisas_usuario', '_favoritos_chave_pesquisa_usuario', '_favoritos_pesquisa_score', '_favoritos_escolher_pesquisa_usuario', '_favoritos_carregar_pesquisas_usuario', '_favoritos_salvar_pesquisas_usuario_batch', '_favoritos_enriquecer_pesquisas_usuario', '_favoritos_carregar_skus_ocultos', '_favoritos_salvar_skus_ocultos', '_favoritos_normalizar_vendedores_ignorados', '_favoritos_arquivo_vendedores_ignorados', '_favoritos_carregar_vendedores_ignorados', '_favoritos_salvar_vendedores_ignorados', '_favoritos_arquivo_anuncios_ignorados', '_favoritos_normalizar_anuncios_ignorados', '_favoritos_carregar_anuncios_ignorados', '_favoritos_salvar_anuncios_ignorados', '_favoritos_limpar_texto_historico', '_favoritos_numero_historico', '_favoritos_int_historico', '_favoritos_lista_texto_historico', 'FAVORITOS_HISTORICO_MAX', 'FAVORITOS_HISTORICO_ANUNCIOS_MAX', 'FAVORITOS_HISTORICO_REALTIME_SCOPE', 'FAVORITOS_HISTORICO_SQLITE_SCHEMA_VERSION', '_favoritos_normalizar_historico', '_favoritos_arquivo_historico', '_favoritos_arquivo_historico_json_legacy', '_favoritos_carregar_historico', '_favoritos_aplicar_usuario_padrao_historico', '_favoritos_salvar_historico', '_favoritos_historico_payload_from_sqlite_path', '_favoritos_historico_payload_from_sqlite_bytes', '_favoritos_historico_payload_from_bytes', '_favoritos_historico_sqlite_bytes_from_payload', '_favoritos_usuario_pode_sync_historico', '_favoritos_usuarios_sync_historico', '_favoritos_historico_payload_bytes', '_favoritos_mesclar_historico_usuario_com_fontes', '_favoritos_propagar_historico_para_usuarios', '_favoritos_reconciliar_historico_usuario', '_favoritos_historico_realtime_event_path', '_favoritos_historico_publicar_evento_realtime', '_favoritos_carregar_cadastro_por_sku', '_favoritos_chaves_match_sku', '_favoritos_carregar_estoque_loja_por_sku', '_favoritos_ml_enriquecer_estoque_cadastro', '_favoritos_listar_skus_payload', '_favoritos_salvar_descricao_cadastro', '_favoritos_salvar_pesquisas_batch', '_favoritos_gerar_pesquisas_ia', '_favoritos_ranking_salvar_decisoes_cache', '_favoritos_busca_externa_salvar_cache']
__all__ = PEER_EXPORTS + ["configure_favoritos_storage_runtime"]

configure_favoritos_storage_runtime()
