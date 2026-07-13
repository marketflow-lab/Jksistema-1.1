"""Internal slice for perguntas_pos_venda_core."""

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
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake


def configure_perguntas_pos_venda_agent_runtime(runtime_module=None, peers=None):
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


configure_perguntas_pos_venda_agent_runtime()


def _perguntas_ia_mensagens_aprovacao(pergunta: dict, loja: str) -> list[dict]:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    chat = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    mensagens = []
    for evento in chat[-20:]:
        if not isinstance(evento, dict):
            continue
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        mensagens.append({
            "date": evento.get("date") or evento.get("date_created") or "",
            "from_role": "seller" if role in {"seller", "loja", "store"} else "buyer",
            "text": texto,
            "attachments": [],
        })
    if mensagens:
        return mensagens
    return [{
        "date": pergunta.get("date_created") or pergunta.get("created_at") or "",
        "from_role": "buyer",
        "text": pergunta.get("text") or "",
        "attachments": _ml_pos_venda_mensagem_anexos(pergunta, loja),
    }]


def _perguntas_ia_agent_input(
    client_id: str,
    loja: str,
    pergunta: dict,
    item: dict,
    contexto: dict,
    prompt: str,
) -> dict:
    contexto_dict = contexto if isinstance(contexto, dict) else {}
    intencao_atendimento = contexto_dict.get("intencao_atendimento") if isinstance(contexto_dict.get("intencao_atendimento"), dict) else {}
    fluxo_intencao = str(intencao_atendimento.get("fluxo") or "perguntas_anuncio").strip()
    tipo_treinamento = "pos_venda" if fluxo_intencao == "pos_venda" else "perguntas_anuncio"
    def _intencao_flag(chave: str, default: bool) -> bool:
        valor = intencao_atendimento.get(chave, default)
        if isinstance(valor, bool):
            return valor
        texto = str(valor or "").strip().lower()
        if texto in {"0", "false", "nao", "não", "off", "no"}:
            return False
        if texto in {"1", "true", "sim", "on", "yes"}:
            return True
        return bool(default)

    if fluxo_intencao == "pos_venda":
        allowed_tools: list[str] = []
        usar_busca_web = False
    else:
        usar_busca_web = _intencao_flag("usar_busca_web", True)
        allowed_tools = []
        if _intencao_flag("usar_mercado_livre_anuncio", True):
            allowed_tools.append("get_mercado_livre_listing")
        if _intencao_flag("usar_bling", True):
            allowed_tools.append("get_bling_product")
        if usar_busca_web:
            allowed_tools.extend([
                "web_search",
                "web_search_product_identity",
                "web_search_question_context",
            ])
    contexto_treinamento = {
        "modulo": "perguntas_pos_venda",
        "tipo": "resposta_pos_venda" if tipo_treinamento == "pos_venda" else "resposta_automatica_ml",
        "tipo_treinamento": tipo_treinamento,
        "loja": str(loja or "").strip(),
        "produto": contexto_dict,
    }
    app_guidance = _ia_treinamento_ppv_bloco_prompt(
        client_id,
        "Perguntas e pos venda",
        contexto_treinamento,
    ).strip()
    return {
        "task": "mercado_livre_question_draft",
        "locale": "pt-BR",
        "tenant_id": str(client_id or "").strip(),
        "store": str(loja or "").strip(),
        "prompt": str(prompt or "").strip(),
        "app_guidance": app_guidance[:24000],
        "app_guidance_source": "ia_treinamento_perguntas_pos_venda",
        "question": _perguntas_ia_pergunta_para_agente(pergunta),
        "item": _perguntas_ia_item_para_agente(item, contexto_dict.get("descricao") or ""),
        "context": contexto_dict,
        "intent": intencao_atendimento,
        "context_collection_pipeline": [
            {
                "step": 1,
                "name": "intent_classification",
                "description": "Classificar a intencao da ultima mensagem antes de escolher o fluxo de resposta.",
            },
            {
                "step": 2,
                "name": "buyer_current_and_previous_questions",
                "description": "Usar a pergunta atual e perguntas anteriores do mesmo comprador no mesmo anuncio.",
            },
            {
                "step": 3,
                "name": "mercado_livre_api_listing",
                "description": "Consultar SKU, descricao e estoque atual do anuncio pela API oficial do Mercado Livre.",
            },
            {
                "step": 4,
                "name": "internal_history_and_response_rules",
                "description": "Aplicar historico do app, Bling e regras/orientacoes de resposta salvas.",
            },
            {
                "step": 5,
                "name": "question_focused_web_research",
                "description": "Pesquisar novamente na internet para responder a pergunta atual dentro do contexto coletado.",
            },
            {
                "step": 6,
                "name": "vertex_gemini_answer",
                "description": "Somente depois das etapas anteriores enviar tudo ao modelo configurado para gerar o rascunho.",
            },
        ],
        "use_web_search": usar_busca_web,
        "web_search_required": usar_busca_web,
        "constraints": {
            "read_only": True,
            "do_not_send_to_mercado_livre": True,
            "max_chars": ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO,
            "no_markdown": True,
            "do_not_invent_links_or_compatibility": True,
            "internet_product_research_required": usar_busca_web,
        },
        "allowed_tools": allowed_tools,
        "tool_policy": {
            "usar_busca_web": usar_busca_web,
            "usar_mercado_livre_anuncio": "get_mercado_livre_listing" in allowed_tools,
            "usar_bling": "get_bling_product" in allowed_tools,
        },
    }


def _perguntas_ia_chamar_agente_cloud(
    client_id: str,
    loja: str,
    pergunta: dict,
    item: dict,
    contexto: dict,
    prompt: str,
) -> tuple[str, str]:
    agent_input = _perguntas_ia_agent_input(client_id, loja, pergunta, item, contexto, prompt)
    endpoint_url = _ia_agent_endpoint_url_configurado()
    resource_name = _ia_agent_resource_name_configurado()
    if endpoint_url:
        body = {"classMethod": "query", "input": agent_input}
        data = _ia_agent_http_post(_ia_agent_endpoint_query_url(endpoint_url), body, _ia_agent_endpoint_headers())
        origem = "agent:endpoint"
    elif resource_name:
        headers, _project_id = _vertex_ai_headers_e_project()
        url = _ia_agent_engine_query_url(resource_name)
        body = {"classMethod": "query", "input": agent_input}
        data = _ia_agent_http_post(url, body, headers)
        origem = "agent:reasoningEngine"
    else:
        raise PerguntasIARespostaIndisponivel(
            "Agente Cloud nao configurado. Informe o endpoint ou o resource name nas configuracoes."
        )

    texto = _ia_agent_extrair_texto(data.get("output") if isinstance(data, dict) else data)
    resposta_limpa = _perguntas_ia_limpar_resposta(texto)
    if not resposta_limpa:
        raise PerguntasIARespostaIndisponivel("Agente Cloud nao retornou uma resposta para enviar ao comprador.")
    if _perguntas_ia_resposta_fallback_invalida(resposta_limpa):
        raise PerguntasIARespostaIndisponivel("Resposta de fallback da IA de perguntas bloqueada.")
    return resposta_limpa, origem


def _ia_agent_endpoint_autorizar(request: Request) -> None:
    expected = _ia_agent_endpoint_api_key_configurada()
    if not expected:
        if _env_config_bool(("JK_AGENT_ENDPOINT_ALLOW_WITHOUT_KEY",), default=False):
            return
        raise HTTPException(
            status_code=503,
            detail="Endpoint do agente sem chave configurada. Configure JK_AGENT_ENDPOINT_API_KEY no Cloud Run.",
        )
    auth = str(request.headers.get("authorization") or "").strip()
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    provided = str(request.headers.get("x-jk-agent-key") or bearer or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Chave do agente invalida.")


def _ia_agent_input_dict(payload: IAAgentQueryRequest) -> dict:
    entrada = payload.input
    if isinstance(entrada, dict):
        return entrada
    if isinstance(entrada, str):
        return {"prompt": entrada, "question": {"text": entrada}}
    return {}


def _ia_agent_perguntas_texto_busca(agent_input: dict) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    partes = [
        question.get("text"),
        question.get("item_id"),
        item.get("id"),
        item.get("seller_sku"),
        item.get("title"),
        item.get("description"),
        context.get("sku"),
        context.get("item_id"),
        context.get("titulo"),
        context.get("descricao"),
    ]
    texto = " ".join([str(parte or "").strip() for parte in partes if str(parte or "").strip()])
    return texto[:1200]


def _ia_agent_perguntas_precisa_web(agent_input: dict) -> bool:
    if not _ia_web_busca_ativa():
        return False
    allowed = agent_input.get("allowed_tools") if isinstance(agent_input.get("allowed_tools"), list) else []
    allowed_set = {str(item or "").strip() for item in allowed}
    if allowed and not (allowed_set & {"web_search", "web_search_product_identity", "web_search_question_context"}):
        return False
    if bool(
        agent_input.get("use_web_search")
        or agent_input.get("usar_pesquisa_web")
        or agent_input.get("web_search_required")
        or ((agent_input.get("constraints") or {}).get("internet_product_research_required") if isinstance(agent_input.get("constraints"), dict) else False)
    ):
        return bool(_ia_agent_perguntas_texto_busca(agent_input))
    if str(agent_input.get("task") or "").strip() == "mercado_livre_question_draft":
        return bool(_ia_agent_perguntas_texto_busca(agent_input))
    texto = _normalizar_texto(
        " ".join([
            str(agent_input.get("prompt") or ""),
            _ia_agent_perguntas_texto_busca(agent_input),
        ])
    )
    gatilhos = (
        "PESQUISE", "PESQUISA", "BUSQUE", "BUSCA", "INTERNET", "GOOGLE", "WEB",
        "COMPARAR", "COMPARE", "COMPARACAO", "COMPARA", "VERSUS", " VS ",
        "COMPATIBILIDADE", "COMPATIVEL", "SERVE", "APLICA", "ENCAIXA",
        "CODIGO OEM", "OEM", "PART NUMBER", "NUMERO ORIGINAL", "REFERENCIA",
        "MEDIDA", "ESPECIFICACAO", "ESPECIFICACOES", "MODELO", "ANO",
        "SIMILAR", "EQUIVALENTE", "CONCORRENTE",
    )
    return any(gatilho in texto for gatilho in gatilhos)


def _ia_agent_perguntas_adicionar_parte_busca(parte: object, destino: list[str], vistos: set[str], limite: int = 180) -> None:
    texto = re.sub(r"\s+", " ", str(parte or "").strip())
    if not texto:
        return
    chave = _normalizar_texto(texto)
    if not chave or chave in vistos:
        return
    vistos.add(chave)
    destino.append(texto[:limite])


def _ia_agent_perguntas_query_web(agent_input: dict, tool_results: list[dict]) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}

    def _texto_busca_pergunta(valor: str) -> str:
        texto = re.sub(r"\s+", " ", str(valor or "").strip())
        if not texto:
            return ""
        padroes_alvo = (
            r"(?:serve|servir|aplica|encaixa|compat[ií]vel|compativel).*?(?:no|na|em|para|com)\s+(.+)$",
            r"(?:modelo|veiculo|veículo|carro|moto)\s+(.+)$",
        )
        for padrao in padroes_alvo:
            match = re.search(padrao, texto, flags=re.IGNORECASE)
            if match:
                alvo = match.group(1)
                alvo = re.sub(r"[?!.;,]+$", "", alvo).strip()
                if len(alvo) >= 4:
                    return alvo[:160]
        texto = re.sub(
            r"\b(compare|comparar|confira|conferir|verifique|verificar|pesquise|pesquisar|busque|buscar|internet|google|web|anuncio|anuncios|anúncio|anúncios|descricao|descrição|mesmo|mesma|esta|essa|esse|este|produto|peca|peça|item|pela|pelo|pelas|pelos|do|da|dos|das|e)\b",
            " ",
            texto,
            flags=re.IGNORECASE,
        )
        texto = re.sub(r"\s+", " ", texto).strip(" ?!.;,")
        if len(texto) < 4:
            return ""
        return texto[:180]

    codigos = _ia_agent_perguntas_codigos_web(agent_input, tool_results)
    partes: list[str] = []
    vistos: set[str] = set()
    _ia_agent_perguntas_adicionar_parte_busca(item.get("title"), partes, vistos)
    _ia_agent_perguntas_adicionar_parte_busca(item.get("seller_sku") or item.get("sku"), partes, vistos)
    for codigo in codigos[:4]:
        _ia_agent_perguntas_adicionar_parte_busca(codigo, partes, vistos)
    _ia_agent_perguntas_adicionar_parte_busca(_texto_busca_pergunta(question.get("text")), partes, vistos)
    for resultado in tool_results or []:
        if not isinstance(resultado, dict):
            continue
        matches = ((resultado.get("result") or {}).get("matches") or [])
        if not matches:
            continue
        primeiro = {}
        for match in matches:
            if isinstance(match, dict) and _ia_agent_perguntas_match_relevante_web(agent_input, match):
                primeiro = match
                break
        if not primeiro:
            continue
        for valor in (
            primeiro.get("nome"),
            primeiro.get("title"),
            primeiro.get("sku"),
            primeiro.get("id"),
            primeiro.get("id_bling"),
            primeiro.get("mlb_principal"),
            primeiro.get("marca"),
            primeiro.get("categoria"),
        ):
            _ia_agent_perguntas_adicionar_parte_busca(valor, partes, vistos)
    consulta = " ".join([str(parte or "").strip() for parte in partes if str(parte or "").strip()])
    consulta = re.sub(r"\s+", " ", consulta).strip()
    if not consulta:
        return ""
    if "compat" not in _normalizar_texto(consulta):
        consulta += " compatibilidade especificacao aplicacao"
    return consulta[:500]


def _ia_agent_perguntas_valor_codigo_web(valor: object) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "").strip()).strip(" ,;|")
    if not texto:
        return ""
    if len(texto) > 80:
        return ""
    norm = re.sub(r"[^A-Z0-9]", "", texto.upper())
    if len(norm) < 4:
        return ""
    if norm in {"NONE", "NULL", "NAN", "TRUE", "FALSE"}:
        return ""
    if re.fullmatch(r"(19|20)\d{2}", norm):
        return ""
    return texto


def _ia_agent_perguntas_codigo_norm_web(valor: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())


def _ia_agent_perguntas_adicionar_codigo_web(valor: object, codigos: list[str], vistos: set[str]) -> None:
    texto = _ia_agent_perguntas_valor_codigo_web(valor)
    if not texto:
        return
    for parte in re.split(r"[,;|]", texto):
        parte = _ia_agent_perguntas_valor_codigo_web(parte)
        if not parte:
            continue
        chave = _ia_agent_perguntas_codigo_norm_web(parte)
        if chave in vistos:
            continue
        vistos.add(chave)
        codigos.append(parte[:60])
        if len(codigos) >= 10:
            return


def _ia_agent_perguntas_match_relevante_web(agent_input: dict, match: dict) -> bool:
    if not isinstance(match, dict):
        return False
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}

    campos_codigo_base = ("sku", "seller_sku", "item_id", "id", "mlb_principal", "mlb_ids", "gtin", "ean", "codigo", "code")
    codigos_base = set()
    for origem in (question, item, context):
        for campo in campos_codigo_base:
            if origem is question and campo == "id":
                continue
            valor = origem.get(campo)
            for parte in re.split(r"[,;|]", str(valor or "")):
                codigo = _ia_agent_perguntas_valor_codigo_web(parte)
                if codigo:
                    codigos_base.add(_ia_agent_perguntas_codigo_norm_web(codigo))

    codigos_match = set()
    for campo in (
        "sku", "seller_sku", "id", "item_id", "mlb_principal", "mlb_ids", "id_bling",
        "gtin", "ean", "codigo", "code", "referencia", "oem", "part_number",
    ):
        valor = match.get(campo)
        for parte in re.split(r"[,;|]", str(valor or "")):
            codigo = _ia_agent_perguntas_valor_codigo_web(parte)
            if codigo:
                codigos_match.add(_ia_agent_perguntas_codigo_norm_web(codigo))
    for variacao in (match.get("variations") or [])[:5]:
        if isinstance(variacao, dict):
            for campo in ("sku", "seller_sku", "id", "gtin", "ean", "codigo"):
                codigo = _ia_agent_perguntas_valor_codigo_web(variacao.get(campo))
                if codigo:
                    codigos_match.add(_ia_agent_perguntas_codigo_norm_web(codigo))

    if codigos_base and codigos_match and codigos_base & codigos_match:
        return True

    titulo_base = " ".join([
        str(item.get("title") or ""),
        str(context.get("titulo") or ""),
    ])
    texto_match = " ".join([
        str(match.get("nome") or ""),
        str(match.get("title") or ""),
        str(match.get("descricao") or match.get("description") or ""),
    ])
    stopwords = {
        "DE", "DA", "DO", "DAS", "DOS", "PARA", "COM", "SEM", "POR", "UMA", "UM",
        "KIT", "NOVO", "ORIGINAL", "PRODUTO", "PECA", "PEÇA", "AUTOMOTIVO",
        "AUTOMOTIVA", "AUTO", "CARRO", "VEICULO", "VEÍCULO", "MOTOR",
    }
    genericos = stopwords | {
        "SENSOR", "TEMPERATURA", "VALVULA", "VÁLVULA", "FILTRO", "BOMBA",
        "INTERRUPTOR", "BOTAO", "BOTÃO", "CHAVE", "CABO", "MANGUEIRA",
        "SUPORTE", "TAMPA", "TRAVA", "CONEXAO", "CONEXÃO", "RESERVATORIO",
        "RESERVATÓRIO", "CONDICIONADO", "EVAPORADOR",
    }
    tokens_base = {
        token for token in re.findall(r"[A-Z0-9]{3,}", _normalizar_texto(titulo_base))
        if token not in stopwords and not re.fullmatch(r"(19|20)\d{2}", token)
    }
    tokens_match = {
        token for token in re.findall(r"[A-Z0-9]{3,}", _normalizar_texto(texto_match))
        if token not in stopwords and not re.fullmatch(r"(19|20)\d{2}", token)
    }
    if not tokens_base:
        return True
    intersecao = tokens_base & tokens_match
    tokens_base_fortes = {
        token for token in tokens_base
        if token not in genericos and (any(ch.isdigit() for ch in token) or len(token) <= 5)
    }
    if tokens_base_fortes:
        return bool(tokens_base_fortes & tokens_match)
    return len(intersecao) >= 3 and (len(intersecao) / max(1, len(tokens_base))) >= 0.5


def _ia_agent_perguntas_codigos_web(agent_input: dict, tool_results: list[dict]) -> list[str]:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    codigos: list[str] = []
    vistos: set[str] = set()

    campos_codigo_produto = (
        "sku", "seller_sku", "id", "item_id", "mlb", "mlb_id", "mlb_principal", "mlb_ids",
        "id_bling", "codigo", "code", "codigo_produto", "codigo_do_produto",
        "referencia", "referência", "ref", "oem", "codigo_oem", "part_number",
        "numero_peca", "numero_da_peca", "ean", "gtin", "barcode", "codigo_barras",
        "catalog_product_id", "product_id",
    )
    campos_codigo_pergunta = tuple(campo for campo in campos_codigo_produto if campo != "id")
    for origem, campos in (
        (question, campos_codigo_pergunta),
        (item, campos_codigo_produto),
        (context, campos_codigo_produto),
    ):
        for campo in campos:
            _ia_agent_perguntas_adicionar_codigo_web(origem.get(campo), codigos, vistos)

    textos_para_extrair = [
        question.get("text"),
        item.get("title"),
        item.get("description"),
        context.get("titulo"),
        context.get("descricao"),
    ]
    for resultado in tool_results or []:
        if not isinstance(resultado, dict):
            continue
        result = resultado.get("result") if isinstance(resultado.get("result"), dict) else {}
        for match in (result.get("matches") or [])[:3]:
            if not isinstance(match, dict):
                continue
            if not _ia_agent_perguntas_match_relevante_web(agent_input, match):
                continue
            for campo in campos_codigo_produto:
                _ia_agent_perguntas_adicionar_codigo_web(match.get(campo), codigos, vistos)
            for variacao in (match.get("variations") or [])[:5]:
                if isinstance(variacao, dict):
                    for campo in ("sku", "seller_sku", "id", "codigo", "ean", "gtin"):
                        _ia_agent_perguntas_adicionar_codigo_web(variacao.get(campo), codigos, vistos)
            textos_para_extrair.extend([
                match.get("nome"),
                match.get("title"),
                match.get("descricao"),
                match.get("description"),
                match.get("titulos_anuncios_mlb"),
            ])

    for texto in textos_para_extrair:
        for codigo in _favoritos_busca_externa_extrair_codigos(str(texto or "")):
            _ia_agent_perguntas_adicionar_codigo_web(codigo, codigos, vistos)
        for codigo in re.findall(r"\b\d{8,14}\b", str(texto or "")):
            _ia_agent_perguntas_adicionar_codigo_web(codigo, codigos, vistos)
        if len(codigos) >= 10:
            break
    return codigos[:10]


def _ia_agent_perguntas_slug_link_produto(permalink: object) -> str:
    url = str(permalink or "").strip()
    if not url:
        return ""
    try:
        caminho = urlparse(url).path or ""
    except Exception:
        caminho = url
    slug = os.path.basename(caminho).strip()
    slug = re.sub(r"_JM$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"\bMLB[-_ ]?\d{5,}\b", " ", slug, flags=re.IGNORECASE)
    slug = slug.replace("-", " ").replace("_", " ")
    slug = re.sub(r"\s+", " ", slug).strip()
    if len(slug) < 8:
        return ""
    return slug[:180]


def _ia_agent_perguntas_queries_identificacao_produto(agent_input: dict) -> list[dict]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}

    partes: list[str] = []
    vistos: set[str] = set()
    for valor in (
        item.get("title"),
        context.get("titulo"),
        _ia_agent_perguntas_slug_link_produto(item.get("permalink") or context.get("permalink") or context.get("link")),
        item.get("seller_sku") or item.get("sku"),
        context.get("sku"),
        item.get("id"),
        context.get("item_id"),
    ):
        _ia_agent_perguntas_adicionar_parte_busca(valor, partes, vistos)
    for codigo in _ia_agent_perguntas_codigos_web(agent_input, [])[:5]:
        _ia_agent_perguntas_adicionar_parte_busca(codigo, partes, vistos)

    base = re.sub(r"\s+", " ", " ".join(partes)).strip()
    if not base:
        return []

    queries = [{
        "type": "produto_link_identificacao",
        "query": f"{base} codigo peca OEM part number compatibilidade aplicacao",
    }]
    titulo = re.sub(r"\s+", " ", str(item.get("title") or context.get("titulo") or "").strip())[:180]
    item_id = str(item.get("id") or context.get("item_id") or "").strip()
    sku = str(item.get("seller_sku") or item.get("sku") or context.get("sku") or "").strip()
    comparacao_base = " ".join([parte for parte in (titulo, item_id, sku) if parte]).strip()
    if comparacao_base:
        queries.append({
            "type": "produto_anuncio_origem_e_similares",
            "query": f"{comparacao_base} Mercado Livre descricao anuncio similar compatibilidade",
        })
    return queries[:2]


def _ia_agent_perguntas_queries_web(agent_input: dict, tool_results: list[dict]) -> list[dict]:
    principal = _ia_agent_perguntas_query_web(agent_input, tool_results)
    if not principal:
        return []
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    codigos = _ia_agent_perguntas_codigos_web(agent_input, tool_results)
    titulo = re.sub(r"\s+", " ", str(item.get("title") or "").strip())[:180]
    pergunta_norm = _normalizar_texto(question.get("text") or "")
    quer_comparar_descricao = any(
        termo in pergunta_norm
        for termo in ("DESCRICAO", "ANUNCIO", "ANUNCIOS", "MESMO PRODUTO", "COMPARAR", "COMPARE", "SIMILAR", "EQUIVALENTE")
    )
    queries = [{"type": "compatibilidade_aplicacao", "query": principal}]
    base_comparacao = " ".join([parte for parte in [titulo, " ".join(codigos[:3])] if parte]).strip()
    if base_comparacao and (quer_comparar_descricao or codigos or titulo):
        comparacao = f"{base_comparacao} Mercado Livre anuncio descricao produto similar"
        comparacao = re.sub(r"\s+", " ", comparacao).strip()[:500]
        if _normalizar_texto(comparacao) != _normalizar_texto(principal):
            queries.append({"type": "anuncios_similares_descricao", "query": comparacao})
    return queries[:2]


def _ia_agent_perguntas_relaxar_query_web(query: str) -> str:
    texto = str(query or "")
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU[-_/A-Z0-9]{2,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\b\d{8,14}\b", " ", texto)
    texto = re.sub(r"\b[A-Z]{2,8}[-./][A-Z0-9]{3,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:500]


def _ia_agent_perguntas_query_ml_publica(query: str) -> str:
    texto = _ia_agent_perguntas_relaxar_query_web(query)
    texto = re.sub(
        r"\b(mercado livre|anuncio|anuncios|descri[cç][aã]o|produto similar|compatibilidade|especificacao|aplicacao)\b",
        " ",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:180]


def _ia_agent_perguntas_anuncios_publicos_ml(query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            "Origin": "https://www.mercadolivre.com.br",
            "Referer": "https://www.mercadolivre.com.br/",
        }
        resp = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
            headers=headers,
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        resultados = []
        for item in (payload.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            descricao = ""
            if item_id:
                try:
                    desc_resp = requests.get(
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        headers=headers,
                        timeout=10,
                        verify=False,
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                except Exception as exc:
                    logger.warning("[IA AGENT PERGUNTAS] Falha ao consultar descricao publica ML %s: %s", item_id, exc)
            resultados.append({
                "id": item_id,
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("permalink") or "").strip(),
                "price": item.get("price"),
                "available_quantity": item.get("available_quantity"),
                "condition": item.get("condition"),
                "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                "description": descricao[:900],
            })
        return resultados
    except Exception as exc:
        logger.warning("[IA AGENT PERGUNTAS] Falha na busca publica de anuncios ML: %s", exc)
        return []


def _ia_agent_perguntas_anuncios_ml_autenticado(client_id: str, loja: str, query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    lojas = _ia_lojas_com_integracao(client_id, "mercadolivre", loja)
    if not lojas:
        return []
    for nome_loja in lojas[:3]:
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            resp, cfg = _ml_api_request(
                client_id,
                nome_loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/sites/MLB/search",
                params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
                timeout=18,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json() or {}
            resultados = []
            for item in (payload.get("results") or [])[:max_results]:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                descricao = ""
                if item_id:
                    desc_resp, cfg = _ml_api_request(
                        client_id,
                        nome_loja,
                        cfg,
                        "GET",
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        timeout=10,
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                resultados.append({
                    "loja_consulta": nome_loja,
                    "id": item_id,
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("permalink") or "").strip(),
                    "price": item.get("price"),
                    "available_quantity": item.get("available_quantity"),
                    "condition": item.get("condition"),
                    "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                    "description": descricao[:900],
                })
            if resultados:
                return resultados
        except Exception as exc:
            logger.warning("[IA AGENT PERGUNTAS] Falha na busca autenticada de anuncios ML (%s): %s", nome_loja, exc)
            continue
    return []


def _ia_agent_perguntas_contexto_web(client_id: str, loja: str, queries: list[dict]) -> str:
    if not queries:
        return ""
    linhas: list[str] = []
    urls_vistas: set[str] = set()
    for consulta in queries:
        if not isinstance(consulta, dict):
            continue
        query = str(consulta.get("query") or "").strip()
        tipo = str(consulta.get("type") or "web").strip()
        if not query:
            continue

        consultas_tentadas = [query]
        query_relaxada = _ia_agent_perguntas_relaxar_query_web(query)
        if query_relaxada and _normalizar_texto(query_relaxada) != _normalizar_texto(query):
            consultas_tentadas.append(query_relaxada)

        itens = []
        query_usada = query
        for tentativa_query in consultas_tentadas:
            resultados = _ia_web_buscar_cached(tentativa_query, client_id=client_id, max_results=4)
            for item in resultados or []:
                if not isinstance(item, dict):
                    continue
                url = _ia_web_normalizar_result_url(item.get("url") or "")
                parsed_url = urlparse(url) if url else None
                if parsed_url and "duckduckgo.com" in (parsed_url.netloc or "") and parsed_url.path.startswith("/y.js"):
                    continue
                chave_url = url.lower().split("?", 1)[0]
                if not url or chave_url in urls_vistas:
                    continue
                urls_vistas.add(chave_url)
                itens.append((item, url))
                if len(itens) >= 4:
                    break
            if itens:
                query_usada = tentativa_query
                break

        anuncios_publicos = (
            _ia_agent_perguntas_anuncios_ml_autenticado(client_id, loja, query_usada or query, max_results=3)
            or _ia_agent_perguntas_anuncios_publicos_ml(query_usada or query, max_results=3)
        )
        if not itens and not anuncios_publicos:
            continue
        linhas.append(f"Busca {len(linhas) + 1} ({tipo}): {query_usada}")
        for idx, (item, url) in enumerate(itens, start=1):
            bloco = f"{idx}. {item.get('title')}\nURL: {url}"
            if item.get("provider"):
                bloco += f"\nProvedor: {item.get('provider')}"
            if item.get("source"):
                bloco += f"\nFonte: {item.get('source')}"
            if item.get("published_at"):
                bloco += f"\nData: {item.get('published_at')}"
            bloco += f"\nResumo: {item.get('snippet') or 'Sem resumo disponivel.'}"
            linhas.append(bloco)
        if anuncios_publicos:
            linhas.append("Anuncios publicos do Mercado Livre para comparar titulo e descricao:")
            for idx, item in enumerate(anuncios_publicos, start=1):
                bloco = f"{idx}. {item.get('title')}\nID: {item.get('id')}\nURL: {item.get('url')}"
                if item.get("loja_consulta"):
                    bloco += f"\nConsulta API ML via loja conectada: {item.get('loja_consulta')}"
                if item.get("price") is not None:
                    bloco += f"\nPreco: {item.get('price')}"
                if item.get("condition"):
                    bloco += f"\nCondicao: {item.get('condition')}"
                if item.get("description"):
                    bloco += f"\nDescricao: {str(item.get('description') or '')[:700]}"
                else:
                    bloco += "\nDescricao: Nao retornada pela API publica."
                linhas.append(bloco)
    return "\n\n".join(linhas)


def _ia_agent_perguntas_web_tool(client_id: str, agent_input: dict, tool_results: list[dict]) -> Optional[dict]:
    if not _ia_agent_perguntas_precisa_web(agent_input):
        return None
    queries = _ia_agent_perguntas_queries_web(agent_input, tool_results)
    if not queries:
        return None
    try:
        loja = str(agent_input.get("store") or agent_input.get("loja") or "").strip()
        contexto_web = _ia_agent_perguntas_contexto_web(client_id, loja, queries)
    except Exception as exc:
        logger.warning("[IA AGENT PERGUNTAS] Falha em web_search: %s", exc)
        return {
            "function": "web_search_question_context",
            "arguments": {"query": queries[0].get("query") if queries else "", "queries": queries},
            "result": {"found": False, "context": "", "error": str(exc)[:180]},
        }
    return {
        "function": "web_search_question_context",
        "arguments": {"query": queries[0].get("query") if queries else "", "queries": queries},
        "result": {
            "found": bool(contexto_web),
            "context": contexto_web[:5000],
            "read_only": True,
            "phase": "5_question_focused_web_research",
            "instruction": (
                "Pesquisa externa final, feita depois do contexto interno e das APIs. "
                "Use estes achados para responder a pergunta atual do comprador dentro do contexto ja coletado. "
                "Compare codigos, titulos e descricoes de anuncios similares quando disponiveis. "
                "Nao trate resultado web como certeza se conflitar com cadastro, Mercado Livre ou Bling; nesses casos, responda com cautela e recomende confirmacao."
            ),
        },
    }


def _ia_agent_perguntas_product_identity_web_tool(client_id: str, agent_input: dict) -> Optional[dict]:
    if not _ia_agent_perguntas_precisa_web(agent_input):
        return None
    queries = _ia_agent_perguntas_queries_identificacao_produto(agent_input)
    if not queries:
        return None
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    try:
        loja = str(agent_input.get("store") or agent_input.get("loja") or "").strip()
        contexto_web = _ia_agent_perguntas_contexto_web(client_id, loja, queries)
    except Exception as exc:
        logger.warning("[IA AGENT PERGUNTAS] Falha em web_search_product_identity: %s", exc)
        return {
            "function": "web_search_product_identity",
            "arguments": {
                "query": queries[0].get("query") if queries else "",
                "queries": queries,
                "product_link": item.get("permalink") or context.get("permalink") or context.get("link") or "",
            },
            "result": {"found": False, "context": "", "error": str(exc)[:180]},
        }
    return {
        "function": "web_search_product_identity",
        "arguments": {
            "query": queries[0].get("query") if queries else "",
            "queries": queries,
            "product_link": item.get("permalink") or context.get("permalink") or context.get("link") or "",
        },
        "result": {
            "found": bool(contexto_web),
            "context": contexto_web[:5000],
            "read_only": True,
            "phase": "1_product_link_research",
            "instruction": (
                "Pesquisa inicial pelo link/titulo do nosso anuncio. "
                "Use para identificar qual e a peca, codigos conhecidos, aplicacao, uso e compatibilidade provavel antes de interpretar a pergunta atual. "
                "Nao responda ainda somente com esta etapa; ela serve para formar a identidade tecnica do produto."
            ),
        },
    }


def _ia_agent_perguntas_tools_timeout_s() -> float:
    try:
        valor = float(str(os.getenv("ML_PERGUNTAS_IA_TOOLS_TIMEOUT_S") or "8").replace(",", "."))
    except Exception:
        valor = 8.0
    return max(2.0, min(valor, 20.0))


def _ia_agent_perguntas_tool_error(function_name: str, erro: object, timeout: bool = False) -> dict:
    result: dict[str, Any] = {
        "found": False,
        "error": str(erro or "Falha ao consultar ferramenta.")[:180],
        "read_only": True,
    }
    if timeout:
        result["timeout"] = True
    if function_name in {"get_product_data", "get_mercado_livre_listing", "get_bling_product"}:
        result["matches"] = []
    if function_name in {"web_search_product_identity", "web_search_question_context"}:
        result["context"] = ""
    return {
        "function": function_name,
        "arguments": {},
        "result": result,
    }


def _ia_agent_perguntas_perf_meta(client_id: str, loja: str, agent_input: Optional[dict]) -> dict[str, str]:
    entrada = agent_input if isinstance(agent_input, dict) else {}
    question = entrada.get("question") if isinstance(entrada.get("question"), dict) else {}
    item = entrada.get("item") if isinstance(entrada.get("item"), dict) else {}
    context = entrada.get("context") if isinstance(entrada.get("context"), dict) else {}
    pergunta_id = (
        question.get("id")
        or question.get("question_id")
        or question.get("pergunta_id")
        or entrada.get("question_id")
        or ""
    )
    item_id = (
        item.get("id")
        or question.get("item_id")
        or context.get("item_id")
        or entrada.get("item_id")
        or ""
    )
    sku = (
        item.get("seller_sku")
        or item.get("sku")
        or context.get("sku")
        or context.get("seller_sku")
        or ""
    )
    return {
        "tenant": str(client_id or entrada.get("tenant_id") or "").strip()[:80],
        "loja": str(loja or entrada.get("store") or entrada.get("loja") or "").strip()[:160],
        "pergunta": str(pergunta_id or "").strip()[:80],
        "item": str(item_id or "").strip()[:80],
        "sku": str(sku or "").strip()[:120],
    }


def _ia_agent_perguntas_log_perf(
    client_id: str,
    loja: str,
    agent_input: Optional[dict],
    etapa: str,
    tempo_s: float,
    **detalhes: Any,
) -> None:
    meta = _ia_agent_perguntas_perf_meta(client_id, loja, agent_input)
    partes = []
    for chave, valor in detalhes.items():
        if valor is None:
            continue
        if isinstance(valor, bool):
            valor_txt = "true" if valor else "false"
        elif isinstance(valor, float):
            valor_txt = f"{valor:.3f}"
        else:
            valor_txt = str(valor)
        valor_txt = re.sub(r"\s+", " ", valor_txt).strip()[:220]
        if valor_txt:
            partes.append(f"{chave}={valor_txt}")
    sufixo = f" {' '.join(partes)}" if partes else ""
    logger.info(
        "[ML PERGUNTAS PERF] tenant=%s loja=%s pergunta=%s item=%s sku=%s etapa=%s tempo=%.3fs%s",
        meta["tenant"] or "-",
        meta["loja"] or "-",
        meta["pergunta"] or "-",
        meta["item"] or "-",
        meta["sku"] or "-",
        str(etapa or "-"),
        max(0.0, float(tempo_s or 0.0)),
        sufixo,
    )


def _ia_agent_perguntas_perf_etapa_tool(nome: str) -> str:
    nome = str(nome or "").strip()
    if nome == "product_data":
        return "cadastro_interno"
    if nome == "mercado_livre":
        return "mercado_livre"
    if nome == "bling":
        return "bling"
    if nome in {"product_identity", "web_question"}:
        return "busca_web"
    return nome or "ferramenta"


def _ia_agent_perguntas_preparar_tools(client_id: str, loja: str, agent_input: dict) -> list[dict]:
    perf_total_t0 = time.perf_counter()
    consulta = _ia_agent_perguntas_texto_busca(agent_input)
    if not consulta:
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "ferramentas_total",
            time.perf_counter() - perf_total_t0,
            status="sem_consulta",
        )
        return []
    allowed_raw = agent_input.get("allowed_tools") if isinstance(agent_input, dict) else None
    allowed_definido = isinstance(allowed_raw, list)
    allowed_set = {str(item or "").strip() for item in (allowed_raw or []) if str(item or "").strip()} if allowed_definido else set()

    def ferramenta_permitida(function_name: str) -> bool:
        return not allowed_definido or str(function_name or "").strip() in allowed_set

    if allowed_definido and not allowed_set:
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "ferramentas_total",
            time.perf_counter() - perf_total_t0,
            status="sem_ferramentas_permitidas",
        )
        return []

    def consultar_identidade_web() -> Optional[dict]:
        return _ia_agent_perguntas_product_identity_web_tool(client_id, agent_input)

    def consultar_mercado_livre() -> Optional[dict]:
        return _ia_tool_get_mercado_livre_listing(
            client_id,
            consulta,
            loja=loja,
            produto_tool=None,
            limite=5,
            incluir_descricao=str(agent_input.get("task") or "").strip() == "mercado_livre_question_draft",
        )

    def consultar_bling() -> Optional[dict]:
        return _ia_tool_get_bling_product(client_id, consulta, loja=loja, produto_tool=None, limite=3)

    def consultar_web_pergunta() -> Optional[dict]:
        return _ia_agent_perguntas_web_tool(client_id, agent_input, [])

    tarefas_base: list[tuple[int, str, str, Callable[[], Optional[dict]]]] = [
        (0, "product_identity", "web_search_product_identity", consultar_identidade_web),
        (2, "mercado_livre", "get_mercado_livre_listing", consultar_mercado_livre),
        (3, "bling", "get_bling_product", consultar_bling),
        (4, "web_question", "web_search_question_context", consultar_web_pergunta),
    ]
    tarefas = [tarefa for tarefa in tarefas_base if ferramenta_permitida(tarefa[2])]
    if not tarefas:
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "ferramentas_total",
            time.perf_counter() - perf_total_t0,
            status="sem_ferramentas_permitidas",
        )
        return []
    timeout_s = _ia_agent_perguntas_tools_timeout_s()

    def executar_tool(func: Callable[[], Optional[dict]]) -> dict:
        perf_tool_t0 = time.perf_counter()
        try:
            return {
                "tool_result": func(),
                "tempo_s": time.perf_counter() - perf_tool_t0,
                "erro": None,
            }
        except Exception as exc:
            return {
                "tool_result": None,
                "tempo_s": time.perf_counter() - perf_tool_t0,
                "erro": exc,
            }

    futuros = {
        IA_PERGUNTAS_TOOLS_EXECUTOR.submit(executar_tool, func): (ordem, nome, function_name)
        for ordem, nome, function_name, func in tarefas
    }
    done, pending = wait(futuros.keys(), timeout=timeout_s)
    resultados_por_ordem: dict[int, dict] = {}

    for futuro in done:
        ordem, nome, function_name = futuros[futuro]
        tempo_tool = 0.0
        try:
            exec_result = futuro.result()
            tempo_tool = float(exec_result.get("tempo_s") or 0.0) if isinstance(exec_result, dict) else 0.0
            erro = exec_result.get("erro") if isinstance(exec_result, dict) else None
            tool_result = exec_result.get("tool_result") if isinstance(exec_result, dict) else None
            if erro:
                raise erro
        except Exception as exc:
            logger.warning("[IA AGENT PERGUNTAS] Falha em ferramenta local %s: %s", nome, exc)
            tool_result = _ia_agent_perguntas_tool_error(function_name, exc)
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                _ia_agent_perguntas_perf_etapa_tool(nome),
                tempo_tool,
                ferramenta=function_name,
                status="erro",
                erro=type(exc).__name__,
            )
        else:
            result = tool_result.get("result") if isinstance(tool_result, dict) and isinstance(tool_result.get("result"), dict) else {}
            matches = result.get("matches") if isinstance(result.get("matches"), list) else []
            contexto = str(result.get("context") or "")
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                _ia_agent_perguntas_perf_etapa_tool(nome),
                tempo_tool,
                ferramenta=function_name,
                status="ok" if tool_result else "vazio",
                found=bool(result.get("found")),
                matches=len(matches),
                context_chars=len(contexto),
                timeout=bool(result.get("timeout")),
            )
        if tool_result:
            resultados_por_ordem[ordem] = tool_result

    if pending:
        nomes_pendentes = []
        tempo_ate_timeout = time.perf_counter() - perf_total_t0
        for futuro in pending:
            ordem, nome, function_name = futuros[futuro]
            nomes_pendentes.append(nome)
            futuro.cancel()
            resultados_por_ordem[ordem] = _ia_agent_perguntas_tool_error(
                function_name,
                f"Ferramenta excedeu o prazo global de {timeout_s:.1f}s e foi ignorada nesta resposta.",
                timeout=True,
            )
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                _ia_agent_perguntas_perf_etapa_tool(nome),
                tempo_ate_timeout,
                ferramenta=function_name,
                status="timeout",
                limite_s=timeout_s,
            )
        logger.warning(
            "[IA AGENT PERGUNTAS] Timeout global das ferramentas locais (%.1fs). Pendentes: %s",
            timeout_s,
            ", ".join(nomes_pendentes),
        )

    resultados = [resultados_por_ordem[idx] for idx in sorted(resultados_por_ordem)]
    _ia_agent_perguntas_log_perf(
        client_id,
        loja,
        agent_input,
        "ferramentas_total",
        time.perf_counter() - perf_total_t0,
        status="ok",
        ferramentas=len(resultados),
        timeout_count=len(pending),
    )
    return resultados


def _ia_agent_perguntas_montar_prompt(client_id: str, agent_input: dict, tool_results: list[dict]) -> str:
    base_prompt = str(agent_input.get("prompt") or "").strip()
    app_guidance = str(
        agent_input.get("app_guidance")
        or agent_input.get("training_guidance")
        or agent_input.get("orientacoes")
        or agent_input.get("app_instructions")
        or agent_input.get("instructions")
        or ""
    ).strip()[:24000]
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    intent = _perguntas_ia_intencao_agent(agent_input)
    fluxo_pos_venda = intent.get("fluxo") == "pos_venda"
    constraints = agent_input.get("constraints") if isinstance(agent_input.get("constraints"), dict) else {}
    pipeline = agent_input.get("context_collection_pipeline") if isinstance(agent_input.get("context_collection_pipeline"), list) else []
    bloco_pipeline = json.dumps(pipeline or [], ensure_ascii=False, default=str)[:4000]
    bloco_tools = json.dumps(tool_results or [], ensure_ascii=False, default=str)[:24000]
    bloco_question = json.dumps(question, ensure_ascii=False, default=str)[:4000]
    bloco_item = json.dumps(item, ensure_ascii=False, default=str)[:5000]
    bloco_intencao = json.dumps(intent or {}, ensure_ascii=False, default=str)[:3000]
    loja = str(agent_input.get("store") or agent_input.get("loja") or "").strip()
    perf_memoria_t0 = time.perf_counter()
    bloco_memoria = "" if fluxo_pos_venda else _perguntas_ia_memoria_bloco_prompt(client_id, agent_input)
    _ia_agent_perguntas_log_perf(
        client_id,
        loja,
        agent_input,
        "memoria_sku",
        time.perf_counter() - perf_memoria_t0,
        status="desativada_pos_venda" if fluxo_pos_venda else ("ok" if bloco_memoria else "vazio"),
        chars=len(bloco_memoria or ""),
    )
    rascunho_atual = str(question.get("current_draft_to_avoid") or "").strip()
    bloco_rascunho_atual = _perguntas_ia_compactar_contexto(rascunho_atual, 1200)
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    linhas_historico = []
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        texto_evento = str(evento.get("text") or "").strip()
        if not texto_evento:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        rotulo = "Loja" if role in {"seller", "loja", "store"} else "Comprador"
        linhas_historico.append(f"{rotulo}: {texto_evento[:500]}")
    bloco_historico = "\n".join(linhas_historico)[:2000]
    if fluxo_pos_venda:
        return (
            "Voce e o agente de pos-venda do Mercado Livre do JK Sistema. "
            "Gere somente um rascunho de resposta ao comprador. "
            "Nao envie, nao publique e nao altere nada no Mercado Livre, Bling ou cadastro. "
            "A mensagem foi classificada como pos-venda, entao NAO responda como compatibilidade, aplicacao, serve ou venda do produto. "
            "Se o comprador relata defeito, mau funcionamento, item apagando, quebrado, troca ou garantia, reconheca o problema e oriente o proximo passo de atendimento. "
            "Quando houver relato de mau funcionamento, peca foto do item/problema e oriente a chamar pelo detalhe da compra ou informar os dados necessarios, conforme as regras salvas. "
            "Nao invente causa tecnica, prazo, garantia, compatibilidade, estoque ou procedimento que nao esteja nas orientacoes. "
            "Nao mencione SKU, codigo interno, preco, nome da loja ou link do proprio anuncio. "
            "Responda em portugues do Brasil, sem markdown, sem tabela, sem emoji e sem aspas externas. "
            f"Limite de caracteres: {constraints.get('max_chars') or ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO}.\n\n"
            f"Intencao classificada em JSON:\n{bloco_intencao or '{}'}\n\n"
            f"Orientacoes do app e treinamento salvos:\n{app_guidance or '-'}\n\n"
            f"Historico resumido da conversa:\n{bloco_historico or '-'}\n\n"
            f"Resposta atual no campo, se existir; corrija/substitua e nao repita literalmente:\n{bloco_rascunho_atual or '-'}\n\n"
            f"Pergunta normalizada em JSON:\n{bloco_question or '{}'}\n\n"
            f"Anuncio recebido em JSON somente para identificar a compra/produto, nao para responder compatibilidade:\n{bloco_item or '{}'}"
        )
    return (
        "Voce e o agente Cloud de perguntas do Mercado Livre do JK Sistema. "
        "Gere somente um rascunho de resposta ao comprador. "
        "Nao envie, nao publique e nao altere nada no Mercado Livre, Bling ou cadastro. "
        "Responda em portugues do Brasil, sem markdown, sem tabela, sem emoji e sem aspas externas. "
        "Responda estritamente a ultima pergunta do comprador; nao troque o assunto para outro produto, veiculo, ano ou compatibilidade. "
        "Antes de finalizar, confirme que todo produto, modelo, veiculo, ano ou codigo citado na resposta aparece na pergunta, no anuncio atual ou no contexto tecnico confiavel do anuncio atual. "
        "Se a intencao classificada nao for compatibilidade, nao responda dizendo que serve ou que e compativel. "
        "Se o comprador perguntar sobre conector, entrada, cabo, USB-C/tipo C, Lightning/iPhone ou Micro USB, responda primeiro exatamente esse conector ou diga que nao ha informacao segura; nao substitua por outro conector ou aparelho. "
        "Se o comprador perguntar sobre material, itens inclusos, lado, quantidade ou variacao, responda primeiro esse atributo especifico. "
        "Nao invente compatibilidade, prazo, garantia, estoque, medidas, links ou dados tecnicos. "
        "Nao mencione SKU, codigo interno, quantidade em estoque, preco, nome da loja, status do anuncio ou link do proprio anuncio, exceto quando as orientacoes do app pedirem explicitamente. "
        "Se a pergunta for sobre compatibilidade, responda a compatibilidade de forma direta e curta; nao reinicie o atendimento com resumo do produto. "
        "Quando mencionar compatibilidade, nunca copie a pergunta inteira como se fosse o nome do veiculo; extraia apenas modelo, motor, ano e cambio, ou use 'veiculo informado'. "
        "Em perguntas de compatibilidade automotiva sem confirmacao objetiva, nao peça chassi; recomende confirmar com mecanico de confianca. "
        "Quando houver historico da conversa, responda a ultima pergunta considerando as mensagens anteriores e evite saudacao longa/repetitiva. "
        "Siga as orientacoes do app e do treinamento salvo para tom, estrutura, politica comercial e conteudo permitido. "
        "Use resultados das ferramentas e contexto recebido como fonte principal de fatos, respeitando a ordem do pipeline. "
        "Primeiro considere web_search_product_identity para entender qual e a peca do nosso anuncio, codigos, uso e compatibilidade provavel. "
        "Depois considere Mercado Livre, Bling, historico e regras do app. "
        "Por ultimo use web_search_question_context para responder a pergunta atual com comparacao de codigos, titulos e descricoes de anuncios similares, manuais, catalogos ou fontes publicas disponiveis. "
        "Nao invente detalhes quando a internet nao trouxer evidencias suficientes; responda com cautela e recomende confirmacao tecnica. "
        "Se os dados externos divergirem do cadastro, Mercado Livre ou Bling, prefira os dados internos para dados comerciais e use a web apenas como apoio tecnico. "
        "Se o dado estiver ausente, peça a informacao necessaria com cordialidade, exceto chassi em compatibilidade automotiva. "
        f"Limite de caracteres: {constraints.get('max_chars') or ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO}.\n\n"
        f"Pipeline obrigatorio de contexto executado pelo app:\n{bloco_pipeline or '[]'}\n\n"
        f"Intencao classificada em JSON:\n{bloco_intencao or '{}'}\n\n"
        f"Orientacoes do app e treinamento salvos:\n{app_guidance or '-'}\n\n"
        f"Memoria tecnica local deste SKU:\n{bloco_memoria or '-'}\n\n"
        f"Prompt original do app:\n{base_prompt or '-'}\n\n"
        f"Historico resumido da conversa:\n{bloco_historico or '-'}\n\n"
        f"Resposta atual no campo, se existir; corrija/substitua e nao repita literalmente:\n{bloco_rascunho_atual or '-'}\n\n"
        f"Pergunta normalizada em JSON:\n{bloco_question or '{}'}\n\n"
        f"Anuncio recebido em JSON:\n{bloco_item or '{}'}\n\n"
        f"Resultados das ferramentas read-only em JSON:\n{bloco_tools or '[]'}"
    )


def _ia_agent_perguntas_chamar_modelo(client_id: str, payload: IAChatRequest, model_req: str) -> tuple[str, str]:
    if _modelo_eh_codex(model_req):
        resposta = _chamar_codex_chat(payload, client_id)
        model_usado = f"codex:{_codex_modelo_nome_curto(model_req)}"
    elif _modelo_eh_vertex_ai(model_req):
        resposta = _chamar_vertex_ai_chat(payload, client_id)
        model_usado = f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    elif _modelo_eh_gemini_api(model_req):
        resposta = _chamar_gemini_chat(payload, client_id)
        model_usado = f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    elif model_req.startswith("deepseek-"):
        resposta = _chamar_deepseek_chat(payload, client_id)
        model_usado = model_req
    else:
        resposta = _chamar_openai_responses(payload, client_id)
        model_usado = model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()
    return resposta, model_usado


ML_PERGUNTAS_IA_TERMOS_VEICULO = (
    "corolla", "passat", "peugeot", "fusion", "tiguan", "jetta", "mercedes",
    "c180", "c200", "c250", "c300", "c350", "slk", "hilux", "polo", "fiesta",
    "palio", "tucson", "pajero", "bmw", "320i", "golf", "fox", "gol", "voyage",
    "saveiro", "onix", "civic", "fit", "city", "focus", "ranger", "ecosport",
    "cruze", "s10", "spin", "astra", "vectra", "clio", "sandero", "logan",
    "duster", "compass", "renegade", "toro", "strada", "uno", "mobi", "argo",
    "hb20", "creta", "ix35", "azera", "santa fe", "cerato", "sportage",
    "audi", "volkswagen", "volvo", "toyota", "honda", "hyundai", "kia",
    "chevrolet", "gm", "ford", "fiat", "renault", "citroen", "nissan",
    "mitsubishi", "jeep",
)


ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS = {
    "A", "AS", "O", "OS", "UM", "UMA", "UNS", "UMAS",
    "DE", "DA", "DO", "DAS", "DOS", "NO", "NA", "NOS", "NAS",
    "EM", "PARA", "PRA", "COM", "SEM", "MEU", "MINHA", "ANO", "ANOS",
}


def _ia_agent_perguntas_termos_contexto(texto: str, termos: tuple[str, ...] = ML_PERGUNTAS_IA_TERMOS_VEICULO) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    encontrados: set[str] = set()
    if not texto_norm:
        return encontrados
    for termo in termos:
        termo_norm = _favoritos_normalizar_sem_acentos(termo)
        if not termo_norm:
            continue
        padrao = r"(?<![a-z0-9])" + re.escape(termo_norm) + r"(?![a-z0-9])"
        if re.search(padrao, texto_norm):
            encontrados.add(termo_norm)
    return encontrados


def _ia_agent_perguntas_texto_fonte(agent_input: dict) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    partes = [
        str(question.get("text") or ""),
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(context.get("titulo") or ""),
        str(context.get("descricao") or ""),
    ]
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            continue
        partes.append(str(evento.get("text") or ""))
    return "\n".join(partes)


def _ia_agent_perguntas_codigos_modelo(texto: str) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "").upper()
    codigos: set[str] = set()
    for match in re.finditer(r"\b[A-Z]{1,6}[\s\-]?\d{2,5}[A-Z]?\b|\b\d{3,4}[A-Z]{1,3}\b", texto_norm):
        bruto = (match.group(0) or "").strip()
        partes = re.match(r"^([A-Z]{1,6})[\s\-]+(\d{2,5})([A-Z]?)$", bruto)
        prefixo = partes.group(1) if partes else ""
        numero = partes.group(2) if partes else ""
        sufixo = partes.group(3) if partes else ""
        if prefixo in ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS:
            if numero and numero.isdigit() and 1900 <= int(numero) <= 2099:
                continue
            codigo = f"{numero}{sufixo}".strip()
        else:
            codigo = re.sub(r"[\s\-]+", "", bruto).strip()
            if prefixo and numero and (sufixo or len(numero) == 3 or int(numero) > 2099):
                codigos.add(f"{numero}{sufixo}".strip())
        if not codigo:
            continue
        if codigo.startswith("MLB") or codigo in {"2022", "2023", "2024", "2025", "2026"}:
            continue
        codigos.add(codigo)
    return codigos


def _ia_agent_perguntas_codigos_modelo_tem_match(codigos_pergunta: set[str], codigos_resposta: set[str]) -> bool:
    for perguntado in codigos_pergunta or set():
        for respondido in codigos_resposta or set():
            if perguntado == respondido:
                return True
            menor, maior = sorted((perguntado, respondido), key=len)
            if len(menor) >= 3 and maior.endswith(menor):
                return True
    return False


def _ia_agent_perguntas_conectores(texto: str) -> set[str]:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    compacto = re.sub(r"[^a-z0-9]+", "", texto_norm)
    encontrados: set[str] = set()
    if (
        "tipo c" in texto_norm
        or "type c" in texto_norm
        or "usb c" in texto_norm
        or "usb-c" in texto_norm
        or "usbc" in compacto
    ):
        encontrados.add("usb-c/tipo c")
    if "lightning" in texto_norm or "iphone" in texto_norm:
        encontrados.add("lightning/iphone")
    if "micro usb" in texto_norm or "micro-usb" in texto_norm or "microusb" in compacto:
        encontrados.add("micro usb")
    if "v8" in texto_norm and any(sinal in texto_norm for sinal in ("conector", "cabo", "entrada", "usb")):
        encontrados.add("micro usb")
    return encontrados


def _ia_agent_perguntas_resposta_pede_chassi(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    if "chassi" not in texto_norm and "vin" not in texto_norm:
        return False
    termos_pedido = (
        "informe", "envie", "mande", "passe", "forneca", "digite", "encaminhe",
        "pode informar", "poderia informar", "favor informar", "preciso",
        "precisamos", "necessario", "necessaria",
    )
    padrao_pedido = r"(?:{})".format("|".join(re.escape(termo) for termo in termos_pedido))
    padrao_chassi = r"(?:chassi|vin)"
    return bool(
        re.search(padrao_pedido + r".{0,90}\b" + padrao_chassi + r"\b", texto_norm)
        or re.search(r"\b" + padrao_chassi + r"\b.{0,90}" + padrao_pedido, texto_norm)
    )


def _ia_agent_perguntas_pede_conector(texto: str) -> bool:
    texto_norm = _favoritos_normalizar_sem_acentos(texto or "")
    if _ia_agent_perguntas_conectores(texto_norm):
        return True
    return any(
        termo in texto_norm
        for termo in (
            "conector", "entrada", "plug", "cabo", "carregador", "carregamento",
            "tipo de ponta", "ponta do cabo", "porta usb", "usb",
        )
    )


def _ia_agent_perguntas_violacoes_resposta(agent_input: dict, resposta: str) -> list[str]:
    texto = str(resposta or "").strip()
    if not texto:
        return []
    texto_norm = _normalizar_texto(texto)
    texto_sem_acentos = _favoritos_normalizar_sem_acentos(texto)
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    textos_comprador = [str(question.get("text") or "")]
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            continue
        textos_comprador.append(str(evento.get("text") or ""))
    pergunta_norm = _normalizar_texto(" ".join(textos_comprador))
    pergunta_sem_acentos = _favoritos_normalizar_sem_acentos(" ".join(textos_comprador))
    rascunho_atual_norm = _normalizar_texto(str(question.get("current_draft_to_avoid") or ""))
    loja = str(agent_input.get("store") or agent_input.get("loja") or "").strip()
    violacoes = []
    intent = _perguntas_ia_intencao_agent(agent_input)
    intencao_nome = str(intent.get("intencao") or "").strip()
    if intent.get("fluxo") == "pos_venda":
        termos_compat = (
            "serve", "servi", "compativel", "compatibilidade", "aplicacao", "veiculo informado",
            "mecanico de confianca", "aguardamos sua compra",
        )
        if any(termo in texto_sem_acentos for termo in termos_compat):
            violacoes.append("tratou pos-venda como compatibilidade/venda")
        sinais_defeito = (
            "defeito", "problema", "apagando", "apaga", "nao funciona", "parou", "queimou",
            "mal funcionamento", "fica apagando", "trocar", "troca", "garantia",
        )
        respostas_esperadas = (
            "foto", "fotos", "compra", "pedido", "mensagem", "detalhe da compra",
            "verificar", "ajudar", "atendimento", "problema", "troca", "garantia",
        )
        if any(sinal in pergunta_sem_acentos for sinal in sinais_defeito) and not any(sinal in texto_sem_acentos for sinal in respostas_esperadas):
            violacoes.append("nao tratou o defeito/troca relatado pelo comprador")
    if re.search(r"\bSKU\b", texto, flags=re.IGNORECASE):
        violacoes.append("mencionou SKU/codigo interno")
    seller_sku = str(item.get("seller_sku") or item.get("sku") or "").strip()
    if seller_sku and _ia_agent_perguntas_codigo_norm_web(seller_sku) in _ia_agent_perguntas_codigo_norm_web(texto):
        violacoes.append("mencionou o codigo interno do produto")
    item_id = str(item.get("id") or question.get("item_id") or "").strip()
    if item_id and item_id.upper() in texto.upper():
        violacoes.append("mencionou o ID/link do anuncio atual")
    permalink = str(item.get("permalink") or "").strip().lower()
    if permalink and permalink in texto.lower():
        violacoes.append("incluiu link do proprio anuncio")
    if "produto.mercadolivre.com" in texto.lower() or "mercadolivre.com.br" in texto.lower():
        violacoes.append("incluiu link de Mercado Livre sem necessidade")
    if re.search(r"\b\d+\s+unidades?\b", texto, flags=re.IGNORECASE) or "UNIDADES DISPONIVEIS" in texto_norm:
        violacoes.append("mencionou quantidade em estoque")
    if "ESTOQUE NA LOJA" in texto_norm or "DISPONIVEIS EM ESTOQUE" in texto_norm:
        violacoes.append("mencionou estoque interno")
    pergunta_pede_preco = any(termo in pergunta_norm for termo in ("PRECO", "VALOR", "CUSTA", "QUANTO"))
    if not pergunta_pede_preco and (re.search(r"\bR\$\s*\d", texto) or "O VALOR E" in texto_norm or "O PRECO E" in texto_norm):
        violacoes.append("respondeu preco sem o comprador perguntar")
    texto_norm_sem_assinatura = re.sub(
        r"EQUIPE\s+.+?\s+AGRADECE\s+(?:O\s+)?SEU\s+CONTATO\.?\s*$",
        "",
        texto_norm,
        flags=re.IGNORECASE,
    ).strip()
    if loja and _normalizar_texto(loja) and _normalizar_texto(loja) in texto_norm_sem_assinatura:
        violacoes.append("mencionou nome da loja")
    if "ANUNCIO ATIVO" in texto_norm or "ANUNCIO DESSE PRODUTO ESTA ATIVO" in texto_norm:
        violacoes.append("mencionou status do anuncio")
    if "CONFORME O ANUNCIO" in texto_norm or "CONFORME ANUNCIO" in texto_norm:
        violacoes.append("usou expressao proibida: conforme o anuncio")
    if "NAO CONSEGUIMOS CONFIRMAR A COMPATIBILIDADE" in texto_norm:
        violacoes.append("usou expressao proibida sobre nao confirmar compatibilidade")
    if _ia_agent_perguntas_resposta_pede_chassi(texto):
        violacoes.append("pediu chassi em pergunta de compatibilidade")
    if re.search(r"COMPAT\w*\s+COM\s+(?:O|A)?\s*(?:BOA|BOM|OLA|OI)", texto_norm):
        violacoes.append("copiou a pergunta inteira como veiculo")
    if "COMPAT" in texto_norm and "COM" in texto_norm and any(t in texto_norm for t in ("ESSA PECA", "ESSA PEÇA", "ESSA PE", "MEU CARRO", "MINHA MOTO")):
        violacoes.append("copiou trecho da pergunta como veiculo")
    if rascunho_atual_norm and len(rascunho_atual_norm) >= 40:
        texto_compacto = re.sub(r"\s+", " ", texto_norm).strip()
        rascunho_compacto = re.sub(r"\s+", " ", rascunho_atual_norm).strip()
        if texto_compacto == rascunho_compacto or texto_compacto in rascunho_compacto or rascunho_compacto in texto_compacto:
            violacoes.append("repetiu a resposta atual sem corrigir")
    pergunta_compatibilidade = any(
        termo in pergunta_sem_acentos
        for termo in ("serve", "servi", "compat", "aplica", "encaixa", "veiculo", "carro", "chassi", "vin", "peugeot", "thp", "308cc")
    )
    resposta_compatibilidade = any(
        termo in texto_sem_acentos
        for termo in ("serve", "compat", "pode ser compat", "provavelmente", "mecanico", "confirmar")
    )
    if resposta_compatibilidade and not pergunta_compatibilidade and intencao_nome not in {"compatibilidade", "outra_peca"}:
        violacoes.append("respondeu compatibilidade sem a pergunta pedir")
    if pergunta_compatibilidade and not resposta_compatibilidade:
        violacoes.append("nao respondeu a pergunta de compatibilidade")
    termos_resposta = _ia_agent_perguntas_termos_contexto(texto)
    if termos_resposta:
        termos_fonte = _ia_agent_perguntas_termos_contexto(_ia_agent_perguntas_texto_fonte(agent_input))
        termos_fora = sorted(termos_resposta - termos_fonte)
        if termos_fora:
            violacoes.append("mencionou veiculo/produto fora do contexto: " + ", ".join(termos_fora[:4]))
    codigos_pergunta = _ia_agent_perguntas_codigos_modelo(" ".join(textos_comprador))
    codigos_resposta = _ia_agent_perguntas_codigos_modelo(texto)
    if (
        not pergunta_compatibilidade
        and codigos_pergunta
        and codigos_resposta
        and not _ia_agent_perguntas_codigos_modelo_tem_match(codigos_pergunta, codigos_resposta)
    ):
        violacoes.append(
            "nao respondeu ao modelo/codigo perguntado: "
            + ", ".join(sorted(codigos_pergunta)[:4])
        )
    texto_comprador_completo = " ".join(textos_comprador)
    conectores_pergunta = _ia_agent_perguntas_conectores(texto_comprador_completo)
    conectores_resposta = _ia_agent_perguntas_conectores(texto)
    if conectores_pergunta and not (conectores_pergunta & conectores_resposta):
        violacoes.append(
            "nao respondeu ao conector/variacao perguntado: "
            + ", ".join(sorted(conectores_pergunta))
        )
    elif conectores_pergunta and conectores_resposta and not (conectores_pergunta & conectores_resposta):
        violacoes.append("respondeu outro conector/variacao")
    elif _ia_agent_perguntas_pede_conector(texto_comprador_completo) and not any(
        termo in texto_sem_acentos
        for termo in ("conector", "entrada", "plug", "cabo", "usb", "tipo c", "type c", "lightning", "iphone", "micro usb")
    ):
        violacoes.append("nao respondeu a pergunta sobre conector")
    pergunta_quantidade = any(
        termo in pergunta_norm
        for termo in (
            "PAR", "UNIDADE", "LADO DIREITO", "LADO ESQUERDO", "PECA LADO", "PEÇA LADO",
            "DUAS PECAS", "DUAS PEÇAS", "2 PECAS", "2 PEÇAS", "QUANTIDADE",
        )
    )
    if pergunta_quantidade and not any(
        termo in texto_norm
        for termo in (
            "PAR", "UNIDADE", "DIREITO", "ESQUERDO", "LADO", "PECA", "PEÇA",
            "DUAS", "2", "VARIACAO", "VARIAÇÃO", "DESCRICAO", "DESCRIÇÃO",
            "ANUNCIO", "ANÚNCIO",
        )
    ):
        violacoes.append("nao respondeu a duvida de quantidade/variacao")
    pergunta_caracteristica = any(
        termo in pergunta_norm
        for termo in (
            "PLASTICO", "PLÁSTICO", "ALUMINIO", "ALUMÍNIO", "JUNTA", "PARAFUSO",
            "VEM COM", "ACOMPANHA", "INCLUSO", "INCLUI",
        )
    )
    if pergunta_caracteristica and not any(
        termo in texto_norm
        for termo in (
            "PLASTICO", "PLÁSTICO", "ALUMINIO", "ALUMÍNIO", "JUNTA", "PARAFUSO",
            "ACOMPANHA", "INCLUSO", "INCLUI", "VEM COM",
        )
    ):
        violacoes.append("nao respondeu itens/material perguntados")
    return list(dict.fromkeys(violacoes))


ML_PERGUNTAS_IA_V2_MODO = "novo_fluxo_perguntas_v2"


ML_POS_VENDA_IA_V2_MODO = "novo_fluxo_pos_venda_v2"


def _perguntas_ia_v2_exigir_aprovacao() -> bool:
    valor = str(os.getenv("ML_PERGUNTAS_IA_V2_PERMITIR_ENVIO_DIRETO") or "").strip().lower()
    return valor not in {"1", "true", "sim", "yes", "on"}


def _pos_venda_ia_v2_exigir_aprovacao() -> bool:
    valor = str(os.getenv("ML_POS_VENDA_IA_V2_PERMITIR_ENVIO_DIRETO") or "").strip().lower()
    return valor not in {"1", "true", "sim", "yes", "on"}


def _perguntas_ia_v2_query_pesquisa(metadata: Optional[dict[str, Any]]) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    pergunta = re.sub(r"\s+", " ", str(meta.get("question_text") or "").strip())
    link = str(meta.get("listing_link") or "").strip()
    titulo = re.sub(r"\s+", " ", str(meta.get("listing_title") or "").strip())[:240]
    item_id = str(meta.get("item_id") or "").strip()
    if not link and item_id:
        link = _favoritos_ml_url_item_id(item_id)
    partes = [parte for parte in (titulo, pergunta, link) if parte]
    if not partes:
        return ""
    return " ".join(partes)[:600]


def _perguntas_ia_v2_resposta_precisa_web(resposta: Any, metadata: Optional[dict[str, Any]] = None) -> bool:
    meta = metadata if isinstance(metadata, dict) else {}
    categoria = str(meta.get("category") or "").strip().lower()
    if categoria not in {"compatibility", "product_feature", "warranty_originality", "other_product", "unknown"}:
        return False
    texto = str(getattr(resposta, "answer", "") or "").strip()
    if not texto:
        return True
    try:
        confianca = float(getattr(resposta, "confidence", 0.0) or 0.0)
    except Exception:
        confianca = 0.0
    motivo = _favoritos_normalizar_sem_acentos(str(getattr(resposta, "reason", "") or ""))
    texto_norm = _favoritos_normalizar_sem_acentos(texto)
    marcadores_ausencia = (
        "missing_listing_evidence",
        "listing evidence missing",
        "nao consta no anuncio",
        "nao consta na descricao",
        "nao encontrei essa informacao",
        "nao foi informado no anuncio",
        "informacao nao disponivel no anuncio",
        "sem evidencia no anuncio",
    )
    return bool(
        getattr(resposta, "requires_human_review", False)
        or confianca < 0.78
        or any(marcador in motivo or marcador in texto_norm for marcador in marcadores_ausencia)
    )


def _perguntas_ia_v2_fontes_web(tool_result: Optional[dict[str, Any]]) -> list[str]:
    result = tool_result.get("result") if isinstance(tool_result, dict) and isinstance(tool_result.get("result"), dict) else {}
    contexto = str(result.get("context") or "")
    fontes: list[str] = []
    for url in re.findall(r"https?://[^\s<>'\"]+", contexto, flags=re.IGNORECASE):
        limpa = url.rstrip(".,;:)]}")[:600]
        if limpa and limpa not in fontes:
            fontes.append(limpa)
        if len(fontes) >= 8:
            break
    return fontes


class _PerguntasVertexGeminiV2Client:
    def __init__(self, client_id: str, loja: str, model_req: str, agent_input: Optional[dict[str, Any]] = None):
        self.client_id = client_id
        self.loja = loja
        self.model_req = model_req
        self.model_usado = model_req
        self.parser = AIResponseParser()
        self.agent_input = copy.deepcopy(agent_input) if isinstance(agent_input, dict) else {}
        self.context_pipeline: list[dict[str, Any]] = []

    def _call_model(
        self,
        prompt: str,
        metadata: dict[str, Any],
        *,
        stage: str,
        tool_results: Optional[list[dict[str, Any]]] = None,
    ) -> Any:
        fluxo_pos_venda = str(metadata.get("category") or "").strip() == "post_sale"
        payload = IAChatRequest(
            message=prompt,
            page="Perguntas e pos venda",
            context={
                "modulo": "perguntas_pos_venda",
                "tipo": ML_POS_VENDA_IA_V2_MODO if fluxo_pos_venda else f"{ML_PERGUNTAS_IA_V2_MODO}_{stage}",
                "tipo_treinamento": "pos_venda" if fluxo_pos_venda else "perguntas_anuncio",
                "origem_ia": "mercado_livre_perguntas_pos_venda_v2" if fluxo_pos_venda else "mercado_livre_perguntas_v2_contexto_sequencial",
                "desativar_recursos_chat": True,
                "desativar_busca_web_chat": True,
                "context_collection_stage": stage,
                "loja": self.loja,
                "metadata": metadata,
            },
            model=self.model_req,
            tool_results=list(tool_results or []),
        )
        resposta, model_usado = _ia_agent_perguntas_chamar_modelo(self.client_id, payload, self.model_req)
        self.model_usado = model_usado
        parsed = self.parser.parse(resposta)
        if getattr(parsed, "answer", ""):
            return parsed
        resposta_limpa = _perguntas_ia_limpar_resposta(resposta)
        if resposta_limpa:
            return AIAnswer(
                answer=resposta_limpa,
                confidence=0.70,
                requires_human_review=True,
                reason="plain_text_requires_evidence_review",
                raw=resposta,
            )
        return parsed

    def generate(self, prompt: str, metadata: Optional[dict[str, Any]] = None) -> AIAnswer:
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        fluxo_pos_venda = str(metadata_dict.get("category") or "").strip() == "post_sale"
        history_count = int(metadata_dict.get("history_count") or 0)
        self.context_pipeline = [
            {
                "step": 1,
                "name": "buyer_question_and_history",
                "status": "completed",
                "history_count": history_count,
                "history_source": str(metadata_dict.get("history_source") or "same_buyer_or_listing"),
            },
            {
                "step": 2,
                "name": "listing_product_analysis",
                "status": "completed",
                "item_id": str(metadata_dict.get("item_id") or ""),
                "listing_title": str(metadata_dict.get("listing_title") or "")[:240],
            },
        ]
        prompt_interno = (
            prompt
            + "\n\nETAPA INTERNA OBRIGATORIA: use primeiro somente a pergunta, o historico e os dados do produto do anuncio. "
            "Nao pesquise na internet nesta etapa. Se esses dados nao responderem com evidencia, retorne "
            "requires_human_review=true e reason=missing_listing_evidence para liberar o fallback externo."
        )
        parsed = self._call_model(prompt_interno, metadata_dict, stage="listing_only")
        precisa_web = bool(not fluxo_pos_venda and _perguntas_ia_v2_resposta_precisa_web(parsed, metadata_dict))
        if not precisa_web:
            self.context_pipeline.append({
                "step": 3,
                "name": "external_research_fallback",
                "status": "skipped",
                "reason": "answer_found_in_listing_or_history" if not fluxo_pos_venda else "post_sale_flow",
            })
            return parsed

        web_result = _ia_agent_perguntas_web_tool(self.client_id, self.agent_input, [])
        web_data = web_result.get("result") if isinstance(web_result, dict) and isinstance(web_result.get("result"), dict) else {}
        fontes = _perguntas_ia_v2_fontes_web(web_result)
        web_found = bool(web_data.get("found") and str(web_data.get("context") or "").strip())
        self.context_pipeline.append({
            "step": 3,
            "name": "external_research_fallback",
            "status": "completed" if web_found else "unavailable",
            "reason": "missing_listing_evidence",
            "query": str(((web_result or {}).get("arguments") or {}).get("query") or _perguntas_ia_v2_query_pesquisa(metadata_dict))[:600],
            "source_count": len(fontes),
            "sources": fontes,
        })
        if not web_found:
            return parsed

        prompt_web = (
            prompt
            + "\n\nETAPA DE FALLBACK EXTERNO: a leitura do anuncio e do historico nao encontrou evidencia suficiente. "
            "Compare o produto anunciado com as fontes publicas abaixo e responda somente quando houver correspondencia clara "
            "de produto, codigo, medida, aplicacao ou caracteristica. Dados do anuncio prevalecem em caso de divergencia. "
            "Nao mencione a pesquisa nem URLs ao comprador e marque revisao humana se as fontes continuarem inconclusivas.\n\n"
            "RESULTADOS_DA_PESQUISA_EXTERNA:\n"
            + json.dumps(web_result, ensure_ascii=False, default=str)[:10000]
        )
        resposta_web = self._call_model(
            prompt_web,
            metadata_dict,
            stage="external_fallback",
            tool_results=[web_result],
        )
        return resposta_web if getattr(resposta_web, "answer", "") else parsed


def _perguntas_ia_v2_prompt(
    client_id: str,
    agent_input: dict,
    *,
    resposta_bloqueada: str = "",
    violacoes: Optional[list[str]] = None,
) -> str:
    agent_input = agent_input if isinstance(agent_input, dict) else {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    intent = _perguntas_ia_intencao_agent(agent_input)
    fluxo_pos_venda = intent.get("fluxo") == "pos_venda"
    app_guidance = str(agent_input.get("app_guidance") or "").strip()
    memoria_sku = "" if fluxo_pos_venda else _perguntas_ia_memoria_bloco_prompt(client_id, agent_input)
    dados = {
        "loja": agent_input.get("store") or agent_input.get("loja") or "",
        "assinatura_obrigatoria": _perguntas_ia_assinatura_loja(str(agent_input.get("store") or agent_input.get("loja") or "")),
        "pergunta": question,
        "anuncio": {
            "id": item.get("id") or "",
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "attributes": item.get("attributes") or [],
        },
        "intencao": intent,
        "contexto_produto": {
            "titulo": context.get("titulo") or "",
            "descricao": context.get("descricao") or "",
            "busca_outra_peca": context.get("busca_outra_peca") or {},
        },
    }
    partes = [
        "Voce e a nova IA V2 de respostas do Mercado Livre do JK Sistema.",
        "Nunca se apresente como IA, assistente, Gemini, Vertex ou JK Sistema.",
        "Responda como a equipe da loja, sem mencionar sistema interno, app, prompt ou treinamento.",
        f"A resposta deve terminar exatamente com: {_perguntas_ia_assinatura_loja(str(agent_input.get('store') or agent_input.get('loja') or ''))}",
        "Gere somente UM rascunho de resposta ao comprador, pronto para revisao humana.",
        "Nao envie, nao publique, nao altere anuncio, nao altere estoque e nao chame ferramentas externas.",
        "Use somente os dados deste prompt: pergunta, historico, anuncio, regras salvas, memoria do SKU e contexto interno.",
        "Nao use web, nao use Bling ao vivo e nao invente dados ausentes.",
        "Responda em portugues do Brasil, sem markdown, sem tabela, sem emoji e sem aspas externas.",
        f"Limite maximo: {ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO} caracteres.",
    ]
    if fluxo_pos_venda:
        partes.extend([
            "A intencao foi classificada como POS-VENDA.",
            "Nao responda como venda, compatibilidade, aplicacao ou convite de compra.",
            "Se houver defeito, troca, garantia ou mau funcionamento, reconheca o problema e peca o proximo dado necessario.",
            "Quando adequado, peca foto do item/problema e oriente continuar pelo detalhe da compra.",
        ])
    else:
        partes.extend([
            "A intencao foi classificada como PERGUNTA DE ANUNCIO.",
            "Responda diretamente a ultima pergunta do comprador; nao reinicie o atendimento.",
            "Nao mencione SKU, codigo interno, quantidade em estoque, status do anuncio, nome da loja ou link do proprio anuncio.",
            "Se faltar dado tecnico ou compatibilidade segura, responda com cautela; em compatibilidade automotiva, nao peca chassi.",
            "Em compatibilidade automotiva sem confirmacao objetiva, recomende confirmar com mecanico de confianca e nao use a frase 'nao conseguimos confirmar a compatibilidade'.",
            "Se a pergunta for sobre outra peca, so informe link quando o contexto interno trouxer anuncio ativo e link.",
        ])
    if app_guidance:
        partes.append("Regras e treinamento salvos pelo usuario:\n" + app_guidance[:18000])
    if memoria_sku:
        partes.append("Memoria tecnica local aprovada deste SKU:\n" + memoria_sku[:6000])
    partes.append("Dados normalizados para a resposta:\n" + json.dumps(dados, ensure_ascii=False, default=str)[:18000])
    resposta_bloqueada = str(resposta_bloqueada or "").strip()
    if resposta_bloqueada or violacoes:
        partes.append(
            "A tentativa anterior foi bloqueada e nao pode ser reaproveitada literalmente.\n"
            f"Resposta bloqueada:\n{resposta_bloqueada or '-'}\n\n"
            f"Problemas detectados: {', '.join(violacoes or []) or '-'}\n"
            "Reescreva corrigindo todos os problemas, com resposta curta e objetiva."
        )
    return _perguntas_ia_compactar_contexto("\n\n".join(partes), 32000)


def _perguntas_ia_v2_resposta_segura_compatibilidade(agent_input: dict, loja: str) -> str:
    agent_input = agent_input if isinstance(agent_input, dict) else {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    historico = question.get("history") if isinstance(question.get("history"), list) else []
    textos = [str(question.get("text") or "")]
    for evento in historico[-10:]:
        if not isinstance(evento, dict):
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        if role in {"seller", "loja", "store"}:
            continue
        textos.append(str(evento.get("text") or ""))
    pergunta_sem_acentos = _favoritos_normalizar_sem_acentos(" ".join(textos))
    intent = _perguntas_ia_intencao_agent(agent_input)
    if intent.get("intencao") != "compatibilidade" and not any(
        termo in pergunta_sem_acentos
        for termo in ("serve", "servi", "compat", "aplica", "encaixa", "veiculo", "carro", "chassi", "vin")
    ):
        return ""
    return _perguntas_ia_resposta_final_loja(
        "Para o veiculo informado, nao temos confirmacao objetiva da aplicacao. "
        "Recomendamos confirmar com seu mecanico de confianca antes da compra.",
        loja,
    )


def _perguntas_ia_v2_corrigir_resposta_bloqueada(
    client_id: str,
    loja: str,
    agent_input: dict,
    model_req: str,
    resposta_bloqueada: str,
    violacoes: list[str],
) -> tuple[str, str]:
    mensagem = _perguntas_ia_v2_prompt(
        client_id,
        agent_input,
        resposta_bloqueada=resposta_bloqueada,
        violacoes=violacoes,
    )
    payload = IAChatRequest(
        message=mensagem,
        page="Perguntas e pos venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "correcao_app_perguntas_v2",
            "tipo_treinamento": "perguntas_anuncio",
            "origem_ia": "mercado_livre_perguntas_v2_correcao_validacao_app",
            "desativar_recursos_chat": True,
            "desativar_busca_web_chat": True,
            "modo_rapido_sidebar": True,
            "loja": loja,
        },
        model=model_req,
    )
    resposta, model_usado = _ia_agent_perguntas_chamar_modelo(client_id, payload, model_req)
    resposta_limpa = _perguntas_ia_limpar_resposta(resposta)
    if not resposta_limpa:
        return "", model_usado
    return _perguntas_ia_resposta_final_loja(resposta_limpa, loja), model_usado


def _perguntas_ia_v2_gerar_resposta(client_id: str, agent_input: dict) -> tuple[str, str, list[dict]]:
    perf_total_t0 = time.perf_counter()
    loja = str((agent_input or {}).get("store") or (agent_input or {}).get("loja") or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja no input da nova IA.")
    settings = GeminiQuestionsSettings.from_env()
    fluxo_pos_venda = _perguntas_ia_fluxo_pos_venda(agent_input)
    settings.max_chars = min(int(settings.max_chars or ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO), ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO)
    exige_aprovacao = _pos_venda_ia_v2_exigir_aprovacao() if fluxo_pos_venda else _perguntas_ia_v2_exigir_aprovacao()
    settings.auto_publish_enabled = bool(settings.auto_publish_enabled and not exige_aprovacao)
    modelo_configurado = _ia_modelo_pos_venda_configurado() if fluxo_pos_venda else _ia_modelo_perguntas_configurado()
    model_req = _normalizar_ia_modelo_padrao(settings.model or modelo_configurado)
    if not (_modelo_eh_vertex_ai(model_req) or _modelo_eh_codex(model_req)):
        model_req = IA_MODELO_PADRAO_SISTEMA
    settings.model = model_req
    diagnostico = [{
        "function": ML_PERGUNTAS_IA_V2_MODO,
        "result": {
            "found": True,
            "message": "Fluxo V2 usa o modelo configurado com validacao antes de qualquer envio.",
            "read_only": True,
            "gemini_model": model_req,
            "vertex_gemini": _modelo_eh_vertex_ai(model_req),
            "codex": _modelo_eh_codex(model_req),
            "auto_publish_enabled": settings.auto_publish_enabled,
            "fluxo_pos_venda": fluxo_pos_venda,
        },
    }]
    try:
        question_ctx, listing_snapshot, previous_questions, seller_rules = context_from_agent_input(
            agent_input,
            auto_publish_enabled=settings.auto_publish_enabled,
        )
        seller_rules.min_confidence = settings.min_confidence
        seller_rules.max_chars = settings.max_chars
        seller_rules.max_sentences = settings.max_sentences
        seller_rules.whitelisted_domains = list(settings.whitelisted_domains)
        gemini_client = _PerguntasVertexGeminiV2Client(client_id, loja, model_req, agent_input)
        orchestrator = QuestionAnswerOrchestrator(settings=settings, gemini_client=gemini_client)
        perf_orq_t0 = time.perf_counter()
        resultado = orchestrator.process(
            question=question_ctx,
            listing=listing_snapshot,
            previous_questions=previous_questions,
            rules=seller_rules,
        )
        resposta_limpa = _perguntas_ia_limpar_resposta(resultado.answer)
        if not resposta_limpa:
            raise PerguntasIARespostaIndisponivel("Nova IA de perguntas nao gerou resposta.")
        resposta_limpa = _perguntas_ia_resposta_final_loja(resposta_limpa, loja)
        model_usado = gemini_client.model_usado or model_req
        diagnostico[0]["result"].update({
            "category": resultado.category.value,
            "route": resultado.route.value,
            "decision": resultado.decision.value,
            "needs_human_review": resultado.needs_human,
            "confidence": resultado.confidence,
            "source": resultado.source,
            "reason": resultado.reason,
            "validation_ok": resultado.validation.ok,
            "validation_issues": list(resultado.validation.issues),
            "prompt_chars": len(resultado.prompt or ""),
            "audit": resultado.audit,
            "context_collection_pipeline": list(gemini_client.context_pipeline),
        })
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "v2_orquestrador_gemini",
            time.perf_counter() - perf_orq_t0,
            tentativa=1,
            modelo=model_usado,
            status="revisao" if resultado.needs_human else "ok",
            prompt_chars=len(resultado.prompt or ""),
            resposta_chars=len(resposta_limpa or ""),
            categoria=resultado.category.value,
            rota=resultado.route.value,
            decisao=resultado.decision.value,
        )
        if _perguntas_ia_resposta_fallback_invalida(resposta_limpa):
            raise PerguntasIARespostaIndisponivel("Resposta de fallback da nova IA de perguntas bloqueada.")
        violacoes = _ia_agent_perguntas_violacoes_resposta(agent_input, resposta_limpa)
        if violacoes:
            diagnostico[0]["result"]["app_validation_issues"] = violacoes[:8]
            violacoes_pendentes = list(violacoes)
            if resultado.source == "gemini":
                perf_corr_t0 = time.perf_counter()
                try:
                    resposta_corrigida, model_corrigido = _perguntas_ia_v2_corrigir_resposta_bloqueada(
                        client_id,
                        loja,
                        agent_input,
                        model_req,
                        resposta_limpa,
                        violacoes,
                    )
                except Exception as corr_exc:
                    resposta_corrigida, model_corrigido = "", model_usado
                    _ia_agent_perguntas_log_perf(
                        client_id,
                        loja,
                        agent_input,
                        "v2_correcao_validacao_app",
                        time.perf_counter() - perf_corr_t0,
                        tentativa=2,
                        modelo=model_req,
                        status="erro",
                        erro=type(corr_exc).__name__,
                    )
                else:
                    violacoes_corrigidas = _ia_agent_perguntas_violacoes_resposta(agent_input, resposta_corrigida)
                    if resposta_corrigida and not _perguntas_ia_resposta_fallback_invalida(resposta_corrigida) and not violacoes_corrigidas:
                        resposta_limpa = resposta_corrigida
                        model_usado = model_corrigido or model_usado
                        violacoes_pendentes = []
                        diagnostico[0]["result"].update({
                            "app_validation_repaired": True,
                            "app_validation_repair_issues": [],
                        })
                    else:
                        violacoes_pendentes = violacoes_corrigidas or violacoes_pendentes
                        diagnostico[0]["result"].update({
                            "app_validation_repaired": False,
                            "app_validation_repair_issues": violacoes_pendentes[:8],
                        })
                    _ia_agent_perguntas_log_perf(
                        client_id,
                        loja,
                        agent_input,
                        "v2_correcao_validacao_app",
                        time.perf_counter() - perf_corr_t0,
                        tentativa=2,
                        modelo=model_corrigido or model_req,
                        status="ok" if not violacoes_pendentes else "violacao",
                        violacoes="|".join(violacoes_pendentes[:5]) if violacoes_pendentes else "",
                    )
            if violacoes_pendentes:
                resposta_segura = _perguntas_ia_v2_resposta_segura_compatibilidade(agent_input, loja)
                violacoes_seguras = _ia_agent_perguntas_violacoes_resposta(agent_input, resposta_segura) if resposta_segura else violacoes_pendentes
                if resposta_segura and not violacoes_seguras:
                    resposta_limpa = resposta_segura
                    violacoes_pendentes = []
                    diagnostico[0]["result"].update({
                        "app_validation_repaired": True,
                        "app_validation_repair_source": "fallback_local_compatibilidade",
                        "app_validation_repair_issues": [],
                    })
            if violacoes_pendentes:
                mensagem = "Nova IA de perguntas gerou resposta fora das orientacoes"
                if resultado.source == "gemini":
                    mensagem += " do app"
                raise PerguntasIARespostaIndisponivel(
                    mensagem + ": " + ", ".join(violacoes_pendentes[:6])
                )
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "total",
            time.perf_counter() - perf_total_t0,
            status="ok",
            modelo=model_usado,
            modo=ML_PERGUNTAS_IA_V2_MODO,
        )
        return resposta_limpa, model_usado, diagnostico
    except Exception as exc:
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "total",
            time.perf_counter() - perf_total_t0,
            status="erro",
            erro=type(exc).__name__,
            modo=ML_PERGUNTAS_IA_V2_MODO,
        )
        raise


def _ia_agent_perguntas_gerar_resposta_legado_desativado(client_id: str, agent_input: dict) -> tuple[str, str, list[dict]]:
    raise PerguntasIARespostaIndisponivel(
        "Fluxo local legado de perguntas removido. Use a nova IA Vertex Gemini V2."
    )
    perf_total_t0 = time.perf_counter()
    loja = str(agent_input.get("store") or agent_input.get("loja") or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja no input do agente.")
    try:
        tool_results = _ia_agent_perguntas_preparar_tools(client_id, loja, agent_input)
        perf_prompt_t0 = time.perf_counter()
        mensagem = _ia_agent_perguntas_montar_prompt(client_id, agent_input, tool_results)
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "montagem_prompt",
            time.perf_counter() - perf_prompt_t0,
            chars=len(mensagem or ""),
            ferramentas=len(tool_results or []),
        )
        model_req = _normalizar_ia_modelo_padrao(_ia_modelo_perguntas_configurado())
        tipo_treinamento_payload = "pos_venda" if _perguntas_ia_fluxo_pos_venda(agent_input) else "perguntas_anuncio"
        payload = IAChatRequest(
            message=mensagem,
            page="Perguntas e pós venda",
            context={
                "modulo": "perguntas_pos_venda",
                "tipo": "agente_cloud_perguntas_ml",
                "tipo_treinamento": tipo_treinamento_payload,
                "origem_ia": "mercado_livre_perguntas_sem_chat",
                "desativar_recursos_chat": True,
                "desativar_busca_web_chat": True,
                "loja": loja,
                "tool_results": tool_results,
            },
            model=model_req,
            tool_results=tool_results,
        )
        perf_ia_t0 = time.perf_counter()
        try:
            resposta, model_usado = _ia_agent_perguntas_chamar_modelo_legado_removido(client_id, payload, model_req)
        except Exception as exc:
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                "chamada_ia",
                time.perf_counter() - perf_ia_t0,
                tentativa=1,
                modelo=model_req,
                status="erro",
                erro=type(exc).__name__,
            )
            raise
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "chamada_ia",
            time.perf_counter() - perf_ia_t0,
            tentativa=1,
            modelo=model_usado,
            status="ok",
            prompt_chars=len(payload.message or ""),
            resposta_chars=len(resposta or ""),
        )

        resposta_limpa = _perguntas_ia_limpar_resposta(resposta)
        if not resposta_limpa:
            raise PerguntasIARespostaIndisponivel("IA de perguntas nao gerou resposta.")
        if _perguntas_ia_resposta_fallback_invalida(resposta_limpa):
            raise PerguntasIARespostaIndisponivel("Resposta de fallback da IA de perguntas bloqueada.")
        perf_validacao_t0 = time.perf_counter()
        violacoes = _ia_agent_perguntas_violacoes_resposta(agent_input, resposta_limpa)
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "validacao_resposta",
            time.perf_counter() - perf_validacao_t0,
            status="violacao" if violacoes else "ok",
            violacoes="|".join(violacoes[:5]) if violacoes else "",
        )
        if violacoes:
            intent = _perguntas_ia_intencao_agent(agent_input)
            if intent.get("fluxo") == "pos_venda":
                orientacao_correcao = (
                    "Reescreva a resposta agora como atendimento de POS-VENDA. "
                    "Nao diga que serve, nao diga que e compativel, nao recomende mecanico e nao tente vender o produto. "
                    "Responda a ultima mensagem do comprador reconhecendo o problema/troca/garantia e pedindo o proximo dado necessario. "
                    "Se houver relato de mau funcionamento, peça foto do item/problema e oriente a chamar pelo detalhe da compra quando adequado. "
                    "Seja curto, cordial e objetivo."
                )
            else:
                orientacao_correcao = (
                    "Reescreva a resposta agora, mantendo apenas o que responde a ultima pergunta do comprador. "
                    "Nao troque para outro produto, outro veiculo, outro ano ou outro assunto. "
                    "Nao cite modelo, veiculo ou produto que nao apareca na pergunta, no titulo, na descricao ou no contexto confiavel do anuncio atual. "
                    "Se a intencao nao for compatibilidade, nao responda dizendo que serve ou que e compativel. "
                    "Se o comprador perguntou conector, entrada, cabo, USB-C/tipo C, Lightning/iPhone ou Micro USB, responda exatamente esse conector ou diga que nao ha informacao segura; nao responda sobre outro conector/aparelho. "
                    "Se o comprador perguntou quantidade, variacao, material ou itens inclusos, responda exatamente esse ponto. "
                    "Nao mencione SKU, codigo interno, quantidade em estoque, preco, nome da loja, status do anuncio, ID do anuncio ou link do proprio anuncio. "
                    "Se for pergunta de compatibilidade sem confirmacao objetiva, responda de forma curta que provavelmente pode ser compativel, mas recomenda confirmar com mecanico de confianca, sem usar expressoes proibidas."
                )
            payload.message = (
                f"{mensagem}\n\n"
                "A resposta abaixo violou orientacoes do app e NAO pode ser usada:\n"
                f"{resposta_limpa}\n\n"
                f"Violacoes detectadas: {', '.join(violacoes)}.\n"
                f"{orientacao_correcao}"
            )
            perf_ia_corr_t0 = time.perf_counter()
            try:
                resposta, model_usado = _ia_agent_perguntas_chamar_modelo_legado_removido(client_id, payload, model_req)
            except Exception as exc:
                _ia_agent_perguntas_log_perf(
                    client_id,
                    loja,
                    agent_input,
                    "chamada_ia",
                    time.perf_counter() - perf_ia_corr_t0,
                    tentativa=2,
                    modelo=model_req,
                    status="erro",
                    erro=type(exc).__name__,
                )
                raise
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                "chamada_ia",
                time.perf_counter() - perf_ia_corr_t0,
                tentativa=2,
                modelo=model_usado,
                status="ok",
                prompt_chars=len(payload.message or ""),
                resposta_chars=len(resposta or ""),
            )
            resposta_limpa = _perguntas_ia_limpar_resposta(resposta)
            if not resposta_limpa:
                raise PerguntasIARespostaIndisponivel("IA de perguntas nao gerou resposta corrigida.")
            if _perguntas_ia_resposta_fallback_invalida(resposta_limpa):
                raise PerguntasIARespostaIndisponivel("Resposta de fallback da IA de perguntas bloqueada.")
            perf_validacao2_t0 = time.perf_counter()
            violacoes_restantes = _ia_agent_perguntas_violacoes_resposta(agent_input, resposta_limpa)
            _ia_agent_perguntas_log_perf(
                client_id,
                loja,
                agent_input,
                "validacao_resposta",
                time.perf_counter() - perf_validacao2_t0,
                tentativa=2,
                status="violacao" if violacoes_restantes else "ok",
                violacoes="|".join(violacoes_restantes[:5]) if violacoes_restantes else "",
            )
            if violacoes_restantes:
                intent_restante = _perguntas_ia_intencao_agent(agent_input)
                if intent_restante.get("fluxo") == "pos_venda":
                    raise PerguntasIARespostaIndisponivel(
                        "IA de perguntas nao conseguiu responder como pos-venda: " + ", ".join(violacoes_restantes)
                    )
                raise PerguntasIARespostaIndisponivel(
                    "IA de perguntas gerou resposta fora das orientacoes do app: " + ", ".join(violacoes_restantes)
                )
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "memoria_sku_registro",
            0.0,
            status="ignorado",
            motivo="rascunho_nao_aprovado",
        )
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "total",
            time.perf_counter() - perf_total_t0,
            status="ok",
            modelo=model_usado,
        )
        return resposta_limpa, model_usado, tool_results
    except Exception as exc:
        _ia_agent_perguntas_log_perf(
            client_id,
            loja,
            agent_input,
            "total",
            time.perf_counter() - perf_total_t0,
            status="erro",
            erro=type(exc).__name__,
        )
        raise


def _ml_pos_venda_contexto_prompt(contexto: Optional[dict]) -> str:
    if not isinstance(contexto, dict):
        return ""
    prompt_context = {
        "pipeline": contexto.get("etapas_pipeline") or [],
        "loja": contexto.get("loja") or "",
        "pedido": contexto.get("pedido") or {},
        "mensagem": contexto.get("mensagem") or {},
        "anuncios": contexto.get("anuncios") or [],
        "envio": contexto.get("envio") or {},
        "pagamento": contexto.get("pagamento") or {},
        "nota_fiscal": contexto.get("nota_fiscal") or {},
        "reclamacao_mediacao": contexto.get("reclamacao_mediacao") or {},
        "classificacao": contexto.get("classificacao") or {},
        "decisao_automacao": contexto.get("decisao_automacao") or {},
        "regras_oficiais": contexto.get("regras_oficiais") or {},
        "perguntas_anteriores_anuncio": contexto.get("perguntas_anteriores_anuncio") or [],
        "memoria_sku": contexto.get("memoria_sku") or "",
    }
    bruto = json.dumps(prompt_context, ensure_ascii=False, default=str)
    return _perguntas_ia_compactar_contexto(bruto, 6500)


def _ml_pos_venda_validar_resposta(resposta: str, contexto: dict, limite: int | None = None) -> dict:
    limite_num = int(limite or contexto.get("max_chars") or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    limite_num = max(1, min(limite_num, ML_POS_VENDA_DEFAULT_MAX_CHARS))
    limite_seguro = min(limite_num, ML_POS_VENDA_LIMITE_SEGURO)
    texto = str(resposta or "").strip()
    norm = _ml_pos_venda_texto_norm(texto)
    issues = []
    if not texto:
        issues.append("resposta_vazia")
    if len(texto) > limite_seguro:
        issues.append("resposta_acima_do_limite")
    if any(term in norm for term in ("jk sistema", "sou assistente", "sou uma ia", "gemini", "vertex")):
        issues.append("identidade_incorreta")
    if any(term in norm for term in ("whatsapp", "telefone", "email", "e-mail", "fora do mercado livre")):
        issues.append("contato_externo")
    assinatura = _ml_pos_venda_texto_norm(_perguntas_ia_assinatura_loja(str(contexto.get("loja") or "")))
    assinatura_curta = _ml_pos_venda_texto_norm(_perguntas_ia_assinatura_loja(""))
    if assinatura and assinatura not in norm and assinatura_curta not in norm:
        issues.append("sem_assinatura_loja")
    regras = contexto.get("regras_oficiais") if isinstance(contexto.get("regras_oficiais"), dict) else {}
    decisao = contexto.get("decisao_automacao") if isinstance(contexto.get("decisao_automacao"), dict) else {}
    requires_human = bool(regras.get("precisa_consultar") or not decisao.get("pode_responder_automaticamente"))
    if requires_human:
        issues.append("requer_revisao_humana")
    return {
        "ok": not [issue for issue in issues if issue not in {"requer_revisao_humana"}],
        "requires_human_review": requires_human or bool([issue for issue in issues if issue != "requer_revisao_humana"]),
        "issues": list(dict.fromkeys(issues)),
    }

PEER_EXPORTS = ['_perguntas_ia_mensagens_aprovacao', '_perguntas_ia_agent_input', '_perguntas_ia_chamar_agente_cloud', '_ia_agent_endpoint_autorizar', '_ia_agent_input_dict', '_ia_agent_perguntas_texto_busca', '_ia_agent_perguntas_precisa_web', '_ia_agent_perguntas_adicionar_parte_busca', '_ia_agent_perguntas_query_web', '_ia_agent_perguntas_valor_codigo_web', '_ia_agent_perguntas_codigo_norm_web', '_ia_agent_perguntas_adicionar_codigo_web', '_ia_agent_perguntas_match_relevante_web', '_ia_agent_perguntas_codigos_web', '_ia_agent_perguntas_slug_link_produto', '_ia_agent_perguntas_queries_identificacao_produto', '_ia_agent_perguntas_queries_web', '_ia_agent_perguntas_relaxar_query_web', '_ia_agent_perguntas_query_ml_publica', '_ia_agent_perguntas_anuncios_publicos_ml', '_ia_agent_perguntas_anuncios_ml_autenticado', '_ia_agent_perguntas_contexto_web', '_ia_agent_perguntas_web_tool', '_ia_agent_perguntas_product_identity_web_tool', '_ia_agent_perguntas_tools_timeout_s', '_ia_agent_perguntas_tool_error', '_ia_agent_perguntas_perf_meta', '_ia_agent_perguntas_log_perf', '_ia_agent_perguntas_perf_etapa_tool', '_ia_agent_perguntas_preparar_tools', '_ia_agent_perguntas_montar_prompt', '_ia_agent_perguntas_chamar_modelo', 'ML_PERGUNTAS_IA_TERMOS_VEICULO', 'ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS', '_ia_agent_perguntas_termos_contexto', '_ia_agent_perguntas_texto_fonte', '_ia_agent_perguntas_codigos_modelo', '_ia_agent_perguntas_codigos_modelo_tem_match', '_ia_agent_perguntas_conectores', '_ia_agent_perguntas_resposta_pede_chassi', '_ia_agent_perguntas_pede_conector', '_ia_agent_perguntas_violacoes_resposta', 'ML_PERGUNTAS_IA_V2_MODO', 'ML_POS_VENDA_IA_V2_MODO', '_perguntas_ia_v2_exigir_aprovacao', '_pos_venda_ia_v2_exigir_aprovacao', '_perguntas_ia_v2_query_pesquisa', '_PerguntasVertexGeminiV2Client', '_perguntas_ia_v2_prompt', '_perguntas_ia_v2_resposta_segura_compatibilidade', '_perguntas_ia_v2_corrigir_resposta_bloqueada', '_perguntas_ia_v2_gerar_resposta', '_ia_agent_perguntas_gerar_resposta_legado_desativado', '_ml_pos_venda_contexto_prompt', '_ml_pos_venda_validar_resposta']
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_agent_runtime"]

configure_perguntas_pos_venda_agent_runtime()
