"""Internal slice for ia_tools."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import base64
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError
from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
)
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
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *


def configure_ia_tools_produtos_runtime(runtime_module=None, peers=None):
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


configure_ia_tools_produtos_runtime()


logger = None


def _ia_estoque_texto(client_id: str) -> str:
    """LÃƒÂª produtos_compilado.csv e retorna texto compacto com estoque de todos os SKUs (apenas os com saldo > 0)."""
    try:
        caminho = os.path.join(get_tenant_path(client_id), "produtos_compilado.csv")
        if not os.path.exists(caminho):
            return ""
        df = pd.read_csv(caminho, dtype=str).fillna("")
        linhas = []
        for _, row in df.iterrows():
            sku = str(row.get("sku") or "").strip()
            if not sku:
                continue
            saldo_loja = float(row.get("saldo_loja") or 0)
            if saldo_loja <= 0:
                continue
            nome = str(row.get("nome_bling") or row.get("produto") or "").strip()[:100]
            linhas.append(f"{sku} | {nome} | loja:{int(saldo_loja)}")
        if not linhas:
            return ""
        header = "Estoque atual (SKU | produto | saldo loja):\n"
        return header + "\n".join(linhas)
    except Exception as exc:
        logger.warning(f"[IA] Falha ao ler estoque para contexto: {exc}")
        return ""


def _ia_carregar_produtos_tool_df(client_id: str) -> Optional[pd.DataFrame]:
    try:
        arquivo_estoque = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
        arquivo_cadastro = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)

        df_estoque = None
        df_cadastro = None
        if os.path.exists(arquivo_estoque):
            df_estoque = pd.read_csv(arquivo_estoque, dtype=str).fillna("")
        if os.path.exists(arquivo_cadastro):
            df_cadastro = pd.read_csv(arquivo_cadastro, dtype=str).fillna("")

        if df_estoque is None and df_cadastro is None:
            return None

        if df_estoque is None:
            df_base = df_cadastro.copy()
        elif df_cadastro is None:
            df_base = df_estoque.copy()
        else:
            cols_cadastro = [
                col for col in (
                    "sku",
                    "nome",
                    "categoria",
                    "marca",
                    "custo",
                    "imposto",
                    "preco",
                    "descricao",
                    "description",
                    "foto",
                    "link aliexpress",
                    "produto_bling",
                    "ncm",
                    "cest",
                    "categoria_fiscal",
                    "classificacao_autopeca",
                    "mlb_principal",
                    "titulos_anuncios_mlb",
                    "updated_at",
                    "qtd",
                )
                if col in df_cadastro.columns
            ]
            df_base = df_estoque.merge(df_cadastro[cols_cadastro], on="sku", how="outer")

        if "sku" not in df_base.columns:
            return None

        df_base = df_base.fillna("")
        df_base["sku_norm"] = df_base["sku"].astype(str).str.strip().str.upper()
        nome_principal = df_base.get("nome_bling", "").astype(str) if "nome_bling" in df_base.columns else ""
        nome_secundario = df_base.get("nome", "").astype(str) if "nome" in df_base.columns else ""
        df_base["nome_tool"] = nome_principal.where(nome_principal.str.strip() != "", nome_secundario)
        df_base["nome_tool"] = df_base["nome_tool"].astype(str).fillna("")
        df_base["nome_tool_norm"] = df_base["nome_tool"].map(_normalizar_texto)
        return df_base
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao carregar base de produtos: {exc}")
        return None


def _ia_extrair_referencia_produto_mensagem(mensagem: str) -> dict:
    texto_original = str(mensagem or "").strip()
    texto_norm = _normalizar_texto(texto_original)
    if not texto_norm:
        return {"sku": "", "termo": ""}

    stopwords_sku = {
        "MAIS", "MENOS", "TOP", "LIDER", "LIDEROU", "VENDEU", "VENDIDO", "VENDIDA",
        "PRODUTO", "SKU", "LOJA", "MES", "MESMO", "PERIODO", "ONTEM", "HOJE",
        "VAI", "CADA", "TODOS", "TODAS", "QUANDO", "ACABA", "ACABAR",
        "APARECE", "APARECER", "CODIGO", "CÃ“DIGO", "EXATO",
    }

    def _token_parece_data(valor: str) -> bool:
        token = str(valor or "").strip().upper()
        if not token:
            return False
        if re.match(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$", token):
            return True
        if re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$", token):
            return True
        return False

    def _sku_curto_valido(valor: str) -> bool:
        token = str(valor or "").strip()
        return bool(
            re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{1,40}", token)
            and token not in stopwords_sku
            and not _token_parece_data(token)
            and re.search(r"\d", token)
            and (len(token) >= 3 or re.search(r"[-_/]", token) or re.fullmatch(r"0\d", token))
        )

    texto_primeira_linha = _normalizar_texto(texto_original.split("\n", 1)[0]).strip()
    for texto_sku_curto in (texto_primeira_linha, texto_norm.strip()):
        if _sku_curto_valido(texto_sku_curto):
            return {"sku": texto_sku_curto, "termo": ""}

    match_sku = re.search(r"\bSKU\s+([A-Z0-9][A-Z0-9._/-]{0,40})\b", texto_norm)
    if match_sku:
        sku_match = match_sku.group(1).strip()
        if sku_match not in stopwords_sku and not _token_parece_data(sku_match):
            return {"sku": sku_match, "termo": ""}

    match_sku_como = re.search(r"\bSKU\b.{0,60}\b(?:COMO|CODIGO|CÃ“DIGO|COD)\s+([A-Z0-9][A-Z0-9._/-]{0,40})\b", texto_norm)
    if match_sku_como:
        sku_match = match_sku_como.group(1).strip()
        if sku_match not in stopwords_sku and not _token_parece_data(sku_match) and re.search(r"\d", sku_match):
            return {"sku": sku_match, "termo": ""}

    match_sku_solto = re.search(r"\b(MLB\d{6,}|[A-Z0-9]{2,}(?:[-_/][A-Z0-9]{1,})+)\b", texto_norm)
    if match_sku_solto:
        sku_match = match_sku_solto.group(1).strip()
        if sku_match not in stopwords_sku and not _token_parece_data(sku_match):
            return {"sku": sku_match, "termo": ""}

    match_aspas = re.search(r'"([^"]{3,80})"', texto_original)
    if not match_aspas:
        match_aspas = re.search(r"'([^']{3,80})'", texto_original)
    if match_aspas:
        return {"sku": "", "termo": match_aspas.group(1).strip()}

    match_produto = re.search(r"\bPRODUTO\s+([A-Z0-9 ]{3,80})", texto_norm)
    if match_produto:
        termo = match_produto.group(1).strip()
        for stop in (" ONTEM", " HOJE", " NO MES", " NO MÃƒÅ S", " EM ", " DE ", " DA ", " DO ", " NA ", " NO ", " LOJA "):
            if stop in termo:
                termo = termo.split(stop, 1)[0].strip()
                break
        return {"sku": "", "termo": termo}

    return {"sku": "", "termo": ""}


def _ia_normalizar_imagem_cadastro_url(imagem_ref: str) -> str:
    ref = str(imagem_ref or "").replace("\\", "/").strip()
    if not ref:
        return ""
    if re.match(r"^https?://", ref, re.IGNORECASE):
        return ref
    if ref.startswith("/api/cadastro/foto-arquivo/") or ref.startswith("/api/cadastro/foto/") or ref.startswith("/img/"):
        return ref
    if ref.lower().startswith("cadastro_fotos/"):
        ref = ref.split("/", 1)[1]
    nome = os.path.basename(ref)
    if not nome:
        return ""
    if re.search(r"\.(png|jpe?g|gif|webp|bmp)$", nome, re.IGNORECASE):
        return f"/api/cadastro/foto-arquivo/{quote_plus(nome)}"
    return ref


def _ia_tool_get_product_data(client_id: str, mensagem: str, limite: int = 5) -> Optional[dict]:
    ref = _ia_extrair_referencia_produto_mensagem(mensagem)
    sku_ref = _normalizar_sku_mes(str(ref.get("sku") or "").strip()).upper()
    termo_ref = str(ref.get("termo") or "").strip()
    if not sku_ref and not termo_ref:
        return None

    df = _ia_carregar_produtos_tool_df(client_id)
    if df is None or df.empty:
        return None

    candidatos = df
    argumentos = {}
    if sku_ref:
        argumentos["sku_ou_termo"] = sku_ref
        candidatos = df[df["sku_norm"] == sku_ref]
    else:
        termo_norm = _normalizar_texto(termo_ref)
        argumentos["sku_ou_termo"] = termo_ref
        candidatos = df[
            df["nome_tool_norm"].str.contains(termo_norm, na=False)
            | df["sku_norm"].str.contains(termo_norm, na=False)
        ]

    if candidatos.empty:
        return {
            "function": "get_product_data",
            "arguments": argumentos,
            "result": {"matches": [], "found": False},
        }

    registros = []
    mapa_fotos_locais = _cadastro_mapa_fotos_locais(client_id)
    for _, row in candidatos.head(limite).iterrows():
        imagem_url = ""
        for col_img in ("foto", "imagem", "imagem_url", "image_url", "url_imagem", "link_imagem"):
            if col_img in row.index:
                valor = str(row.get(col_img) or "").strip()
                if valor:
                    imagem_url = _ia_normalizar_imagem_cadastro_url(valor)
                    break
        if not imagem_url:
            foto_local = _cadastro_resolver_foto_local(mapa_fotos_locais, str(row.get("sku") or "").strip())
            if foto_local:
                imagem_url = _ia_normalizar_imagem_cadastro_url(foto_local)
        registros.append({
            "sku": str(row.get("sku") or "").strip(),
            "id_bling": str(row.get("id_bling") or "").strip(),
            "nome": str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
            "marca": str(row.get("marca") or "").strip(),
            "categoria": str(row.get("categoria") or "").strip(),
            "saldo_loja": float(row.get("saldo_loja") or 0),
            "saldo_full": float(row.get("saldo_full") or 0),
            "preco": str(row.get("preco") or "").strip(),
            "custo": str(row.get("custo") or "").strip(),
            "imposto": str(row.get("imposto") or "").strip(),
            "mlb_principal": str(row.get("mlb_principal") or "").strip(),
            "mlb_ids": str(row.get("mlb_ids") or "").strip(),
            "imagem_url": imagem_url,
        })

    return {
        "function": "get_product_data",
        "arguments": argumentos,
        "result": {
            "found": True,
            "matches": registros,
            "canonical_sku": registros[0].get("sku") if registros else "",
        },
    }


def _ia_tool_get_product_registry_info(client_id: str, mensagem: str, produto_tool: Optional[dict] = None, limite: int = 3) -> Optional[dict]:
    try:
        base_produto = produto_tool or _ia_tool_get_product_data(client_id, mensagem, limite=1)
        ref = _ia_extrair_referencia_produto_mensagem(mensagem)
        sku_ref = _normalizar_sku_mes(str(ref.get("sku") or "").strip()).upper()
        termo_ref = str(ref.get("termo") or "").strip()
        if not sku_ref and base_produto:
            sku_ref = _normalizar_sku_mes(str(((base_produto.get("result") or {}).get("canonical_sku") or "")).strip()).upper()

        df = _ia_carregar_produtos_tool_df(client_id)
        if df is None or df.empty:
            return None

        candidatos = df
        args = {}
        if sku_ref:
            candidatos = df[df["sku_norm"] == sku_ref]
            args["sku_ou_termo"] = sku_ref
        elif termo_ref:
            termo_norm = _normalizar_texto(termo_ref)
            candidatos = df[
                df["nome_tool_norm"].str.contains(termo_norm, na=False)
                | df["sku_norm"].str.contains(termo_norm, na=False)
            ]
            args["sku_ou_termo"] = termo_ref
        else:
            return None

        if candidatos.empty:
            return {
                "function": "get_product_registry_info",
                "arguments": args,
                "result": {"found": False, "matches": []},
            }

        if "sku_norm" in candidatos.columns:
            candidatos = candidatos.drop_duplicates(subset=["sku_norm"], keep="first")

        registros = []
        mapa_fotos_locais = _cadastro_mapa_fotos_locais(client_id)
        for _, row in candidatos.head(limite).iterrows():
            imagem_url = ""
            for col_img in ("foto", "imagem", "imagem_url", "image_url", "url_imagem", "link_imagem"):
                if col_img in row.index:
                    valor = str(row.get(col_img) or "").strip()
                    if valor:
                        imagem_url = _ia_normalizar_imagem_cadastro_url(valor)
                        break
            if not imagem_url:
                foto_local = _cadastro_resolver_foto_local(mapa_fotos_locais, str(row.get("sku") or "").strip())
                if foto_local:
                    imagem_url = _ia_normalizar_imagem_cadastro_url(foto_local)

            registros.append({
                "sku": str(row.get("sku") or "").strip(),
                "id_bling": str(row.get("id_bling") or "").strip(),
                "nome": str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
                "descricao": str(row.get("descricao") or row.get("description") or "").strip(),
                "marca": str(row.get("marca") or "").strip(),
                "categoria": str(row.get("categoria") or "").strip(),
                "preco": str(row.get("preco") or "").strip(),
                "custo": str(row.get("custo") or "").strip(),
                "imposto": str(row.get("imposto") or "").strip(),
                "ncm": str(row.get("ncm") or "").strip(),
                "cest": str(row.get("cest") or "").strip(),
                "categoria_fiscal": str(row.get("categoria_fiscal") or "").strip(),
                "classificacao_autopeca": str(row.get("classificacao_autopeca") or "").strip(),
                "mlb_principal": str(row.get("mlb_principal") or "").strip(),
                "mlb_ids": str(row.get("mlb_ids") or "").strip(),
                "titulos_anuncios_mlb": str(row.get("titulos_anuncios_mlb") or "").strip(),
                "link_aliexpress": str(row.get("link aliexpress") or "").strip(),
                "imagem_url": imagem_url,
                "updated_at": str(row.get("updated_at") or "").strip(),
            })

        return {
            "function": "get_product_registry_info",
            "arguments": args,
            "result": {
                "found": True,
                "matches": registros,
                "canonical_sku": registros[0].get("sku") if registros else "",
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter cadastro do produto: {exc}")
        return None


def _ia_tool_get_product_margin(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> Optional[dict]:
    base_produto = produto_tool or _ia_tool_get_product_data(client_id, mensagem)
    if not base_produto:
        return None
    resultado = base_produto.get("result") or {}
    matches = resultado.get("matches") or []
    if not matches:
        return {
            "function": "get_product_margin",
            "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or ""},
            "result": {"found": False},
        }

    item = matches[0]
    preco = _ia_tool_float(item.get("preco"))
    custo = _ia_tool_float(item.get("custo"))
    imposto_pct = _ia_tool_float(item.get("imposto"))
    imposto_valor = preco * (imposto_pct / 100.0) if preco > 0 and imposto_pct > 0 else 0.0
    margem_valor = preco - custo - imposto_valor if preco > 0 else 0.0
    margem_pct = ((margem_valor / preco) * 100.0) if preco > 0 else 0.0
    return {
        "function": "get_product_margin",
        "arguments": {"sku_ou_termo": (base_produto.get("arguments") or {}).get("sku_ou_termo") or item.get("sku") or ""},
        "result": {
            "found": True,
            "sku": str(item.get("sku") or "").strip(),
            "nome": str(item.get("nome") or "").strip(),
            "preco": preco,
            "custo": custo,
            "imposto_percentual": imposto_pct,
            "imposto_valor_estimado": imposto_valor,
            "margem_valor_estimada": margem_valor,
            "margem_percentual_estimada": margem_pct,
            "estimativa": True,
        },
    }


def _ia_produtos_info_por_sku(client_id: str, skus: list[str]) -> dict[str, dict]:
    try:
        skus_norm = {str(s or "").strip().upper() for s in (skus or []) if str(s or "").strip()}
        if not skus_norm:
            return {}
        df = _ia_carregar_produtos_tool_df(client_id)
        if df is None or df.empty or "sku_norm" not in df.columns:
            return {}

        out: dict[str, dict] = {}
        cols_img = ("foto", "imagem", "imagem_url", "image_url", "url_imagem", "link_imagem")
        candidatos = df[df["sku_norm"].isin(skus_norm)].drop_duplicates(subset=["sku_norm"], keep="first")
        for _, row in candidatos.iterrows():
            sku = str(row.get("sku_norm") or row.get("sku") or "").strip().upper()
            if not sku:
                continue
            imagem_url = ""
            for col_img in cols_img:
                if col_img in row.index:
                    valor = str(row.get(col_img) or "").strip()
                    if valor:
                        imagem_url = _ia_normalizar_imagem_cadastro_url(valor)
                        break
            out[sku] = {
                "sku": str(row.get("sku") or sku).strip(),
                "produto_cadastro": str(row.get("nome_tool") or row.get("nome") or row.get("nome_bling") or "").strip(),
                "imagem_url": imagem_url,
                "preco": str(row.get("preco") or "").strip(),
                "custo": str(row.get("custo") or "").strip(),
                "categoria": str(row.get("categoria") or "").strip(),
                "marca": str(row.get("marca") or "").strip(),
            }
        return out
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao enriquecer SKUs com cadastro: {exc}")
        return {}


def _ia_bling_produto_detalhe(access_token: str, produto_id: str) -> tuple[Any, int]:
    pid = str(produto_id or "").strip()
    if not pid:
        return None, 400
    resp = _bling_get_with_adaptive_limit(
        f"https://api.bling.com.br/Api/v3/produtos/{pid}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
        limiter=_BlingAdaptiveLimiter(start_interval=0.08),
        max_attempts=5,
    )
    if resp is None:
        return None, 503
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        return (resp.json() or {}).get("data") or {}, 200
    except Exception:
        return None, 502


def _ia_bling_buscar_produtos_codigo(access_token: str, codigo: str) -> tuple[Any, int]:
    codigo_txt = str(codigo or "").strip()
    if not codigo_txt:
        return [], 400
    tentativas = (
        {"codigo": codigo_txt, "limite": 20, "pagina": 1},
        {"criterio": codigo_txt, "limite": 20, "pagina": 1},
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    ultimo_status = 400
    alvo_norm = _normalizar_sku_match_favoritos(codigo_txt)
    for params in tentativas:
        resp = _bling_get_with_adaptive_limit(
            "https://api.bling.com.br/Api/v3/produtos",
            headers=headers,
            params=params,
            timeout=20,
            limiter=_BlingAdaptiveLimiter(start_interval=0.08),
            max_attempts=4,
        )
        if resp is None:
            ultimo_status = 503
            continue
        ultimo_status = resp.status_code
        if resp.status_code == 401:
            return [], 401
        if resp.status_code != 200:
            continue
        try:
            dados = (resp.json() or {}).get("data") or []
        except Exception:
            return [], 502
        filtrados = []
        for prod in dados:
            if not isinstance(prod, dict):
                continue
            codigo_prod = _normalizar_sku_match_favoritos(str(prod.get("codigo") or ""))
            nome_norm = _normalizar_texto(prod.get("nome") or "")
            if codigo_prod == alvo_norm or (alvo_norm and alvo_norm in codigo_prod) or _normalizar_texto(codigo_txt) in nome_norm:
                filtrados.append(prod)
        if filtrados:
            return filtrados, 200
    return [], ultimo_status


def _ia_bling_resumir_produto(produto: dict, loja: str, saldo: Optional[dict] = None) -> dict:
    estoque = produto.get("estoque") if isinstance(produto.get("estoque"), dict) else {}
    return {
        "loja": loja,
        "id_bling": str(produto.get("id") or "").strip(),
        "sku": str(produto.get("codigo") or "").strip(),
        "nome": str(produto.get("nome") or "").strip(),
        "situacao": str(produto.get("situacao") or "").strip(),
        "tipo": str(produto.get("tipo") or "").strip(),
        "formato": str(produto.get("formato") or "").strip(),
        "unidade": str(produto.get("unidade") or "").strip(),
        "preco": produto.get("preco"),
        "preco_custo": produto.get("precoCusto") or produto.get("preco_custo"),
        "ncm": _ia_bling_valor_tributario(produto, "ncm"),
        "cest": _ia_bling_valor_tributario(produto, "cest"),
        "saldo_loja": float((saldo or {}).get("loja") or estoque.get("saldoVirtualTotal") or 0),
        "saldo_full": float((saldo or {}).get("full") or 0),
        "estoque_minimo": estoque.get("minimo"),
        "estoque_maximo": estoque.get("maximo"),
    }


def _ia_tool_get_bling_product(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    produto_tool: Optional[dict] = None,
    limite: int = 5,
) -> Optional[dict]:
    try:
        ref = _ia_extrair_referencia_produto_mensagem(mensagem)
        sku = _ia_tool_resolver_sku(client_id, mensagem, produto_tool)
        if not sku and ref.get("sku"):
            sku = _normalizar_sku_mes(str(ref.get("sku") or "").strip()).upper()

        ids_bling = [m.group(1) for m in re.finditer(r"\b(?:ID\s*)?BLING\s*(\d{3,})\b", str(mensagem or ""), flags=re.IGNORECASE)]
        if produto_tool:
            for match in ((produto_tool.get("result") or {}).get("matches") or []):
                id_bling = str((match or {}).get("id_bling") or "").strip()
                if id_bling and id_bling not in ids_bling:
                    ids_bling.append(id_bling)
        if not ids_bling and sku:
            cadastro = _ia_tool_get_product_registry_info(client_id, mensagem, produto_tool=produto_tool, limite=3)
            for match in (((cadastro or {}).get("result") or {}).get("matches") or []):
                id_bling = str((match or {}).get("id_bling") or "").strip()
                if id_bling and id_bling not in ids_bling:
                    ids_bling.append(id_bling)

        if not sku and not ids_bling:
            return None

        lojas = _ia_lojas_com_integracao(client_id, "bling", loja)
        if not lojas:
            return {
                "function": "get_bling_product",
                "arguments": {"sku": sku, "ids_bling": ids_bling, "loja": loja or ""},
                "result": {"found": False, "matches": [], "message": "Nenhuma loja com Bling conectado."},
            }

        matches = []
        erros = []
        for nome_loja in lojas:
            if len(matches) >= limite:
                break
            try:
                cfg = _ia_obter_cfg_bling(client_id, nome_loja)
                candidatos = []
                if ids_bling:
                    candidatos = [{"id": pid} for pid in ids_bling[:limite]]
                elif sku:
                    candidatos, status_busca, cfg = _bling_executar_com_refresh(
                        client_id,
                        nome_loja,
                        cfg,
                        lambda token: _ia_bling_buscar_produtos_codigo(token, sku),
                    )
                    if status_busca != 200:
                        erros.append({"loja": nome_loja, "erro": f"Bling HTTP {status_busca} ao buscar produto"})
                        candidatos = []

                produtos_detalhe = []
                for candidato in candidatos or []:
                    pid = str((candidato or {}).get("id") or (candidato or {}).get("id_bling") or "").strip()
                    if not pid:
                        continue
                    detalhe, status_det, cfg = _bling_executar_com_refresh(
                        client_id,
                        nome_loja,
                        cfg,
                        lambda token, _pid=pid: _ia_bling_produto_detalhe(token, _pid),
                    )
                    if status_det == 200 and isinstance(detalhe, dict):
                        produtos_detalhe.append(detalhe)
                    else:
                        erros.append({"loja": nome_loja, "erro": f"Bling HTTP {status_det} ao detalhar produto {pid}"})

                saldos = {}
                ids_saldo = [str((p or {}).get("id") or "").strip() for p in produtos_detalhe if str((p or {}).get("id") or "").strip()]
                if ids_saldo:
                    mapa_dep, status_dep, cfg = _bling_executar_com_refresh(
                        client_id,
                        nome_loja,
                        cfg,
                        _bling_map_depositos,
                    )
                    if status_dep == 200 and isinstance(mapa_dep, dict):
                        saldos, status_saldo, cfg = _bling_executar_com_refresh(
                            client_id,
                            nome_loja,
                            cfg,
                            lambda token: _bling_saldos(token, ids_saldo, mapa_dep),
                        )
                        if status_saldo != 200 or not isinstance(saldos, dict):
                            saldos = {}

                for produto in produtos_detalhe:
                    if len(matches) >= limite:
                        break
                    pid = str(produto.get("id") or "").strip()
                    matches.append(_ia_bling_resumir_produto(produto, nome_loja, (saldos or {}).get(pid) or {}))
            except Exception as exc:
                erros.append({"loja": nome_loja, "erro": str(exc)[:180]})
                logger.warning("[IA TOOLS] Falha ao consultar Bling para IA (%s): %s", nome_loja, exc)

        return {
            "function": "get_bling_product",
            "arguments": {"sku": sku, "ids_bling": ids_bling, "loja": loja or ""},
            "result": {
                "found": bool(matches),
                "matches": matches,
                "errors": erros[:3],
                "read_only": True,
            },
        }
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha geral ao consultar Bling: %s", exc)
        return None


def _ia_tool_get_product_image(client_id: str, mensagem: str, produto_tool: Optional[dict] = None) -> Optional[dict]:
    try:
        base_produto = produto_tool or _ia_tool_get_product_data(client_id, mensagem, limite=1)
        result = (base_produto or {}).get("result") or {}
        matches = result.get("matches") or []
        if not matches:
            return {
                "function": "get_product_image",
                "arguments": {"sku_ou_termo": str(mensagem or "").strip()},
                "result": {"found": False, "imagem_encontrada": False, "message": "Produto nao encontrado no cadastro."},
            }

        produto = matches[0] or {}
        sku = str(produto.get("sku") or result.get("canonical_sku") or "").strip()
        nome = str(produto.get("nome") or produto.get("produto") or "").strip()
        imagem_url = _ia_normalizar_imagem_cadastro_url(str(produto.get("imagem_url") or "").strip())
        origem_imagem = "cadastro" if imagem_url else ""
        ml_imagem = {}

        if not imagem_url:
            cadastro = _ia_tool_get_product_registry_info(client_id, mensagem, produto_tool=base_produto, limite=1)
            cadastro_matches = ((cadastro or {}).get("result") or {}).get("matches") or []
            if cadastro_matches:
                cad = cadastro_matches[0] or {}
                sku = sku or str(cad.get("sku") or "").strip()
                nome = nome or str(cad.get("nome") or "").strip()
                imagem_url = _ia_normalizar_imagem_cadastro_url(str(cad.get("imagem_url") or "").strip())
                origem_imagem = "cadastro" if imagem_url else ""
                if not imagem_url:
                    foto_local = _cadastro_resolver_foto_local(_cadastro_mapa_fotos_locais(client_id), sku)
                    if foto_local:
                        imagem_url = _ia_normalizar_imagem_cadastro_url(foto_local)
                        origem_imagem = "cadastro"
                    else:
                        ml_imagem = _ia_buscar_imagem_ml_sku(client_id, sku, produto=produto, cadastro=cad)
            else:
                foto_local = _cadastro_resolver_foto_local(_cadastro_mapa_fotos_locais(client_id), sku)
                if foto_local:
                    imagem_url = _ia_normalizar_imagem_cadastro_url(foto_local)
                    origem_imagem = "cadastro"
                else:
                    ml_imagem = _ia_buscar_imagem_ml_sku(client_id, sku, produto=produto, cadastro=None)

        if not imagem_url and ml_imagem:
            imagem_url = str(ml_imagem.get("imagem_url") or "").strip()
            origem_imagem = "mercado_livre" if imagem_url else ""
            nome = nome or str(ml_imagem.get("titulo") or "").strip()

        imagem_markdown = f"![SKU {sku or 'produto'}]({imagem_url})" if imagem_url else ""
        return {
            "function": "get_product_image",
            "arguments": {"sku_ou_termo": sku or str(mensagem or "").strip()},
            "result": {
                "found": True,
                "imagem_encontrada": bool(imagem_url),
                "sku": sku,
                "produto": nome,
                "imagem_url": imagem_url,
                "imagem_markdown": imagem_markdown,
                "origem_imagem": origem_imagem,
                "mercado_livre_item_id": str(ml_imagem.get("item_id") or "") if ml_imagem else "",
                "mercado_livre_loja": str(ml_imagem.get("loja") or "") if ml_imagem else "",
            },
        }
    except Exception as exc:
        logger.warning(f"[IA TOOLS] Falha ao obter imagem do produto: {exc}")
        return None

PEER_EXPORTS = ['logger', '_ia_estoque_texto', '_ia_carregar_produtos_tool_df', '_ia_extrair_referencia_produto_mensagem', '_ia_normalizar_imagem_cadastro_url', '_ia_tool_get_product_data', '_ia_tool_get_product_registry_info', '_ia_tool_get_product_margin', '_ia_produtos_info_por_sku', '_ia_bling_produto_detalhe', '_ia_bling_buscar_produtos_codigo', '_ia_bling_resumir_produto', '_ia_tool_get_bling_product', '_ia_tool_get_product_image']
__all__ = PEER_EXPORTS + ["configure_ia_tools_produtos_runtime"]

configure_ia_tools_produtos_runtime()
