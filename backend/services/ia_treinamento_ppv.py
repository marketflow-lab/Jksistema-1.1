"""IA training memory helpers for Perguntas and Pos-venda prompts."""

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
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *

logger = None


def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
    if peers:
        target_globals.update(peers)
    return runtime


def configure_ia_treinamento_ppv_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _ia_treinamento_ppv_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "ia_treinamento_perguntas_pos_venda.json")


def _ia_treinamento_ppv_tipo_normalizar(tipo: str | None = None) -> str:
    tipo_norm = normalizar_texto(str(tipo or "")).replace("-", " ").replace("_", " ")
    if "pos" in tipo_norm and "venda" in tipo_norm:
        return "pos_venda"
    return "perguntas_anuncio"


def _ia_treinamento_ppv_tipo_label(tipo: str | None = None) -> str:
    return "pos-venda" if _ia_treinamento_ppv_tipo_normalizar(tipo) == "pos_venda" else "perguntas de anuncio"


def _ia_treinamento_ppv_normalizar_exemplos(valor) -> dict:
    base = {"perguntas_anuncio": [], "pos_venda": []}
    if isinstance(valor, dict):
        origem = valor
    elif isinstance(valor, list):
        origem = {"perguntas_anuncio": valor}
    else:
        return base

    for tipo_raw, itens in origem.items():
        tipo = _ia_treinamento_ppv_tipo_normalizar(tipo_raw)
        lista = itens if isinstance(itens, list) else []
        normalizados = []
        for item in lista:
            if not isinstance(item, dict):
                continue
            pergunta = str(item.get("pergunta") or item.get("question") or "").strip()
            resposta = str(item.get("resposta") or item.get("answer") or "").strip()
            if not pergunta or not resposta:
                continue
            normalizados.append({
                "pergunta": pergunta[:1200],
                "resposta": resposta[:1600],
                "sku": str(item.get("sku") or "").strip()[:80],
                "observacao": str(item.get("observacao") or item.get("obs") or "").strip()[:500],
                "updated_at": item.get("updated_at") or item.get("created_at") or None,
            })
            if len(normalizados) >= 60:
                break
        base[tipo] = normalizados
    return base


def _ia_treinamento_ppv_loja_key(loja: str | None = None) -> str:
    return _chave_loja_favoritos(str(loja or "").strip())


def _ia_treinamento_ppv_payload_vazio(loja: str = "", loja_key: str = "") -> dict:
    return {
        "orientacoes": "",
        "orientacoes_perguntas": "",
        "orientacoes_pos_venda": "",
        "contexto_loja": "",
        "compatibilidade_autopecas": "",
        "proibicoes": "",
        "notas_sku": {},
        "exemplos": {"perguntas_anuncio": [], "pos_venda": []},
        "updated_at": None,
        "updated_at_perguntas": None,
        "updated_at_pos_venda": None,
        "loja": str(loja or "").strip(),
        "loja_key": str(loja_key or "").strip(),
    }


def _ia_treinamento_ppv_normalizar_notas_sku(valor) -> dict:
    notas_sku = valor if isinstance(valor, dict) else {}
    notas_sku_norm = {}
    for sku_key, item in notas_sku.items():
        sku_norm = _normalizar_sku_mes(str(sku_key or "").strip())
        if not sku_norm:
            continue
        if isinstance(item, dict):
            notas = str(item.get("notas") or item.get("texto") or "").strip()[:8000]
            updated_sku = item.get("updated_at")
        else:
            notas = str(item or "").strip()[:8000]
            updated_sku = None
        if notas:
            notas_sku_norm[sku_norm] = {"notas": notas, "updated_at": updated_sku}
    return notas_sku_norm


def _ia_treinamento_ppv_normalizar_payload(data, loja: str = "", loja_key: str = "") -> dict:
    data = data if isinstance(data, dict) else {}
    orientacoes_legado = str(data.get("orientacoes") or "")[:12000]
    orientacoes_perguntas = str(data.get("orientacoes_perguntas") or data.get("perguntas_anuncio") or orientacoes_legado)[:12000]
    orientacoes_pos_venda = str(data.get("orientacoes_pos_venda") or data.get("pos_venda") or "")[:12000]
    updated_at = data.get("updated_at")
    updated_at_perguntas = data.get("updated_at_perguntas") or updated_at
    updated_at_pos_venda = data.get("updated_at_pos_venda")
    payload = _ia_treinamento_ppv_payload_vazio(
        str(data.get("loja") or loja or "").strip(),
        str(data.get("loja_key") or loja_key or "").strip(),
    )
    payload.update({
        "orientacoes": orientacoes_perguntas,
        "orientacoes_perguntas": orientacoes_perguntas,
        "orientacoes_pos_venda": orientacoes_pos_venda,
        "contexto_loja": str(data.get("contexto_loja") or "")[:12000],
        "compatibilidade_autopecas": str(data.get("compatibilidade_autopecas") or "")[:12000],
        "proibicoes": str(data.get("proibicoes") or "")[:8000],
        "notas_sku": _ia_treinamento_ppv_normalizar_notas_sku(data.get("notas_sku")),
        "exemplos": _ia_treinamento_ppv_normalizar_exemplos(data.get("exemplos")),
        "updated_at": updated_at_perguntas or updated_at_pos_venda,
        "updated_at_perguntas": updated_at_perguntas,
        "updated_at_pos_venda": updated_at_pos_venda,
    })
    return payload


def _ia_treinamento_ppv_carregar(client_id: str) -> dict:
    caminho = _ia_treinamento_ppv_path(client_id)
    if not os.path.exists(caminho):
        return {**_ia_treinamento_ppv_payload_vazio(), "por_loja": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        payload = _ia_treinamento_ppv_normalizar_payload(data)
        por_loja_raw = data.get("por_loja") if isinstance(data.get("por_loja"), dict) else {}
        por_loja = {}
        for chave_raw, item_raw in por_loja_raw.items():
            if not isinstance(item_raw, dict):
                continue
            nome_loja = str(item_raw.get("loja") or chave_raw or "").strip()
            loja_key = _ia_treinamento_ppv_loja_key(nome_loja) or _ia_treinamento_ppv_loja_key(chave_raw)
            if not loja_key:
                continue
            por_loja[loja_key] = _ia_treinamento_ppv_normalizar_payload(item_raw, nome_loja, loja_key)
        payload["por_loja"] = por_loja
        return payload
    except Exception as exc:
        logger.warning("[IA TREINO PPV] Falha ao carregar treinamento: %s", exc)
        return {**_ia_treinamento_ppv_payload_vazio(), "por_loja": {}}


def _ia_treinamento_ppv_salvar(
    client_id: str,
    orientacoes: str,
    tipo: str | None = None,
    loja: str | None = None,
    contexto_loja: str | None = None,
    compatibilidade_autopecas: str | None = None,
    proibicoes: str | None = None,
    sku: str | None = None,
    notas_sku: str | None = None,
    exemplos: list[dict] | None = None,
) -> dict:
    tipo_norm = _ia_treinamento_ppv_tipo_normalizar(tipo)
    texto = str(orientacoes or "").strip()[:12000]
    payload_raiz = _ia_treinamento_ppv_carregar(client_id)
    loja_nome = str(loja or "").strip()
    loja_key = _ia_treinamento_ppv_loja_key(loja_nome)
    if loja_key:
        por_loja = payload_raiz.setdefault("por_loja", {})
        payload = _ia_treinamento_ppv_normalizar_payload(por_loja.get(loja_key), loja_nome, loja_key)
        payload["loja"] = loja_nome
        payload["loja_key"] = loja_key
    else:
        payload = payload_raiz
    agora = dt.datetime.now().isoformat(timespec="seconds")
    payload["contexto_loja"] = str(contexto_loja if contexto_loja is not None else payload.get("contexto_loja") or "").strip()[:12000]
    payload["compatibilidade_autopecas"] = str(compatibilidade_autopecas if compatibilidade_autopecas is not None else payload.get("compatibilidade_autopecas") or "").strip()[:12000]
    payload["proibicoes"] = str(proibicoes if proibicoes is not None else payload.get("proibicoes") or "").strip()[:8000]

    sku_norm = _normalizar_sku_mes(str(sku or "").strip())
    if sku_norm:
        notas = str(notas_sku or "").strip()[:8000]
        payload.setdefault("notas_sku", {})
        if notas:
            payload["notas_sku"][sku_norm] = {"notas": notas, "updated_at": agora}
        else:
            payload["notas_sku"].pop(sku_norm, None)

    if exemplos is not None:
        exemplos_payload = _ia_treinamento_ppv_normalizar_exemplos(payload.get("exemplos"))
        novos = _ia_treinamento_ppv_normalizar_exemplos({tipo_norm: exemplos}).get(tipo_norm, [])
        exemplos_payload[tipo_norm] = novos[:60]
        payload["exemplos"] = exemplos_payload

    if tipo_norm == "pos_venda":
        payload["orientacoes_pos_venda"] = texto
        payload["updated_at_pos_venda"] = agora
    else:
        payload["orientacoes_perguntas"] = texto
        payload["orientacoes"] = texto
        payload["updated_at_perguntas"] = agora
        payload["updated_at"] = agora
    payload["tipo"] = tipo_norm
    if loja_key:
        payload_raiz.setdefault("por_loja", {})[loja_key] = payload
    else:
        payload_raiz = payload
    caminho = _ia_treinamento_ppv_path(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(payload_raiz, fh, ensure_ascii=False, indent=2)
    return {**payload, "orientacoes": texto, "updated_at": agora}


def _ia_treinamento_ppv_resolver(client_id: str, loja: str | None = None) -> dict:
    payload = _ia_treinamento_ppv_carregar(client_id)
    loja_nome = str(loja or "").strip()
    loja_key = _ia_treinamento_ppv_loja_key(loja_nome)
    if loja_key:
        item = (payload.get("por_loja") or {}).get(loja_key)
        if isinstance(item, dict):
            base_global = {k: v for k, v in payload.items() if k != "por_loja"}
            combinado = dict(base_global)
            for chave, valor in item.items():
                if chave == "por_loja":
                    continue
                if isinstance(valor, str):
                    if valor.strip():
                        combinado[chave] = valor
                elif valor not in (None, [], {}):
                    combinado[chave] = valor
            return {**combinado, "loja": item.get("loja") or loja_nome, "loja_key": loja_key}
        return {**payload, "loja": loja_nome, "loja_key": loja_key}
    return payload


def _ia_treinamento_ppv_deve_aplicar(page: Optional[str], context: Optional[dict]) -> bool:
    page_norm = normalizar_texto(str(page or ""))
    contexto = context if isinstance(context, dict) else {}
    modulo_norm = normalizar_texto(str(contexto.get("modulo") or contexto.get("page") or ""))
    return (
        "perguntas_pos_venda" in modulo_norm
        or "perguntas pos venda" in page_norm
        or ("perguntas" in page_norm and "pos" in page_norm)
    )


def _ia_treinamento_ppv_tipo_contexto(context: Optional[dict], tipo: str | None = None) -> str:
    if tipo:
        return _ia_treinamento_ppv_tipo_normalizar(tipo)
    contexto = context if isinstance(context, dict) else {}
    campos = [
        contexto.get("tipo_treinamento"),
        contexto.get("tipo_resposta"),
        contexto.get("aba_treinamento"),
        contexto.get("fluxo"),
        contexto.get("tipo"),
    ]
    texto = " ".join(str(c or "") for c in campos)
    texto_norm = normalizar_texto(texto).replace("-", " ").replace("_", " ")
    if "pos" in texto_norm and "venda" in texto_norm:
        return "pos_venda"
    return "perguntas_anuncio"


def _ia_treinamento_ppv_loja_contexto(context: Optional[dict]) -> str:
    contexto = context if isinstance(context, dict) else {}
    produto = contexto.get("produto") if isinstance(contexto.get("produto"), dict) else {}
    conversa = contexto.get("conversa") if isinstance(contexto.get("conversa"), dict) else {}
    candidatos = [
        contexto.get("loja"),
        contexto.get("store"),
        contexto.get("loja_nome"),
        produto.get("loja"),
        produto.get("loja_nome"),
        conversa.get("loja"),
        conversa.get("loja_nome"),
        conversa.get("store"),
    ]
    for candidato in candidatos:
        texto = str(candidato or "").strip()
        if texto:
            return texto
    return ""


def _ia_treinamento_ppv_bloco_prompt(client_id: str, page: Optional[str], context: Optional[dict]) -> str:
    if not _ia_treinamento_ppv_deve_aplicar(page, context):
        return ""
    tipo = _ia_treinamento_ppv_tipo_contexto(context)
    loja_ctx = _ia_treinamento_ppv_loja_contexto(context)
    data = _ia_treinamento_ppv_resolver(client_id, loja_ctx)
    chave = "orientacoes_pos_venda" if tipo == "pos_venda" else "orientacoes_perguntas"
    orientacoes = str(data.get(chave) or "").strip()
    contexto_loja = str(data.get("contexto_loja") or "").strip()
    compatibilidade = str(data.get("compatibilidade_autopecas") or "").strip()
    proibicoes = str(data.get("proibicoes") or "").strip()
    contexto_dict = context if isinstance(context, dict) else {}
    produto_ctx = contexto_dict.get("produto") if isinstance(contexto_dict.get("produto"), dict) else {}
    sku_ctx = _normalizar_sku_mes(
        str(contexto_dict.get("sku") or produto_ctx.get("sku") or produto_ctx.get("item_sku") or "").strip()
    )
    nota_sku = ""
    if sku_ctx:
        item_nota = (data.get("notas_sku") or {}).get(sku_ctx) or {}
        if isinstance(item_nota, dict):
            nota_sku = str(item_nota.get("notas") or "").strip()
    exemplos = []
    for exemplo in (_ia_treinamento_ppv_normalizar_exemplos(data.get("exemplos")).get(tipo) or []):
        sku_ex = _normalizar_sku_mes(str(exemplo.get("sku") or "").strip())
        if sku_ctx and sku_ex and sku_ex != sku_ctx:
            continue
        if sku_ex and not sku_ctx:
            continue
        exemplos.append(exemplo)
        if len(exemplos) >= 6:
            break

    if not any([orientacoes, contexto_loja, compatibilidade, proibicoes, nota_sku, exemplos]):
        return ""
    loja_label = str(data.get("loja") or loja_ctx or "").strip()
    escopo = f" da loja {loja_label}" if loja_label else ""
    partes = [
        f"\n\nOrientacoes salvas no treinamento de IA para {_ia_treinamento_ppv_tipo_label(tipo)}{escopo}. "
        "Use estas orientacoes ao simular ou redigir este tipo de resposta para clientes do Mercado Livre. "
        "Se houver conflito, preserve a verdade dos dados e as politicas do marketplace, mas adapte tom, estrutura e conteudo conforme abaixo:"
    ]
    if orientacoes:
        partes.append(f"\nRegras especificas deste atendimento:\n{orientacoes[:12000]}")
    if contexto_loja:
        partes.append(f"\nBase de conhecimento da loja:\n{contexto_loja[:12000]}")
    if compatibilidade:
        partes.append(f"\nRegras de compatibilidade de autopecas:\n{compatibilidade[:12000]}")
    if proibicoes:
        partes.append(f"\nCoisas proibidas de afirmar:\n{proibicoes[:8000]}")
    if nota_sku:
        partes.append(f"\nNotas especificas do SKU {sku_ctx}:\n{nota_sku[:8000]}")
    if exemplos:
        linhas = []
        for idx, exemplo in enumerate(exemplos, start=1):
            sku_txt = f" SKU {exemplo.get('sku')}" if exemplo.get("sku") else ""
            linhas.append(
                f"Exemplo {idx}{sku_txt}\nPergunta: {exemplo.get('pergunta')}\nResposta ideal: {exemplo.get('resposta')}"
                + (f"\nObservacao: {exemplo.get('observacao')}" if exemplo.get("observacao") else "")
            )
        partes.append("\nExemplos de boas respostas salvos pelo usuario:\n" + "\n\n".join(linhas))
    return "\n".join(partes)


def _ia_treinamento_ppv_produto_por_sku(client_id: str, sku: str) -> dict:
    sku_norm = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_norm:
        return {}
    arquivo = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    if not arquivo or not os.path.exists(arquivo):
        return {"sku": sku_norm}
    try:
        df = pd.read_csv(arquivo, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        if "sku" not in df.columns:
            return {"sku": sku_norm}
        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df, _ = _consolidar_cadastro_por_sku(df)
        mask = df["sku"].astype(str).str.strip().str.lower() == sku_norm.lower()
        if not mask.any():
            return {"sku": sku_norm}

        linha = df.loc[mask].iloc[0].to_dict()
        campos = [
            "sku", "nome", "produto", "produto_bling", "categoria", "marca", "preco",
            "descricao", "mlb_ids", "titulos_anuncios_mlb", "foto",
        ]
        produto = {}
        for campo in campos:
            valor = linha.get(campo)
            if valor is None or pd.isna(valor):
                continue
            texto = str(valor or "").strip()
            if not texto:
                continue
            if campo in {"nome", "produto", "produto_bling"}:
                texto = _cadastro_limpar_nome(texto)
            produto[campo] = texto
        produto.setdefault("sku", sku_norm)
        return produto
    except Exception as exc:
        logger.warning("[IA TREINO PPV] Falha ao carregar SKU %s: %s", sku_norm, exc)
        return {"sku": sku_norm}


def _ia_treinamento_ppv_listar_skus(client_id: str) -> list[dict]:
    arquivo = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    if not arquivo or not os.path.exists(arquivo):
        return []
    try:
        df = pd.read_csv(arquivo, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        if "sku" not in df.columns:
            return []
        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df = df[df["sku"].astype(str).str.strip() != ""].copy()
        if df.empty:
            return []
        df, _ = _consolidar_cadastro_por_sku(df)
        campos = ["sku", "nome", "produto", "produto_bling", "categoria", "marca", "foto", "mlb_ids"]
        produtos = []
        for row in df.to_dict(orient="records"):
            item = {}
            for campo in campos:
                valor = row.get(campo, "")
                if valor is None or pd.isna(valor):
                    valor = ""
                texto = str(valor or "").strip()
                if campo in {"nome", "produto", "produto_bling"}:
                    texto = _cadastro_limpar_nome(texto)
                item[campo] = texto
            if item.get("sku"):
                produtos.append(item)
        return sorted(produtos, key=lambda item: str(item.get("sku") or ""))
    except Exception as exc:
        logger.warning("[IA TREINO PPV] Falha ao listar SKUs: %s", exc)
        return []


def _ia_treinamento_ppv_produto_prompt(produto: dict) -> str:
    if not isinstance(produto, dict) or not produto:
        return ""
    labels = {
        "sku": "SKU",
        "nome": "Nome",
        "produto": "Produto",
        "produto_bling": "Produto Bling",
        "categoria": "Categoria",
        "marca": "Marca",
        "preco": "Preco",
        "descricao": "Descricao",
        "mlb_ids": "Anuncios MLB",
        "titulos_anuncios_mlb": "Titulos dos anuncios",
    }
    linhas = []
    for campo, label in labels.items():
        valor = str(produto.get(campo) or "").strip()
        if valor:
            linhas.append(f"- {label}: {valor[:1200]}")
    if not linhas:
        return ""
    return "\n\nSKU selecionado no cadastro para esta simulacao:\n" + "\n".join(linhas)

configure_ia_treinamento_ppv_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
    )
]
