"""Public IA endpoint implementations."""

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


def configure_ia_endpoints_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


async def ia_secrets_status(client_id: str = Depends(get_tenant_id)):
    """Retorna apenas o estado do cofre local, sem expor valores sensiveis."""
    return {
        "success": True,
        "client_id": client_id,
        "provisioning_configured": bool(_ia_secrets_provisioning_url("/download")),
        "local_store": _secure_secrets_status(),
    }


async def ia_secrets_provisionar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    client_id: str = Depends(get_tenant_id),
):
    sessao = _payload_sessao_por_authorization(authorization)
    if str(sessao.get("client_id") or "").strip() != str(client_id or "").strip():
        raise HTTPException(status_code=403, detail="Sessao invalida para este cliente.")
    _ia_secrets_validar_sessao_ativa(sessao)
    return await asyncio.to_thread(_ia_secrets_provisionar_cofre_local, sessao)


def ia_agent_perguntas_query(payload: IAAgentQueryRequest, request: Request):
    _ia_agent_endpoint_autorizar(request)
    metodo = str(payload.classMethod or "query").strip() or "query"
    if metodo not in {"query", "run"}:
        raise HTTPException(status_code=400, detail="Metodo do agente nao suportado.")
    agent_input = _ia_agent_input_dict(payload)
    client_id = str(
        agent_input.get("tenant_id")
        or agent_input.get("client_id")
        or request.headers.get("x-client-id")
        or ""
    ).strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Informe tenant_id no input do agente.")
    task = str(agent_input.get("task") or "").strip()
    if task and task not in {"mercado_livre_question_draft", "mercado_livre_question_draft_v2"}:
        raise HTTPException(status_code=400, detail="Tarefa do agente nao suportada neste endpoint.")
    try:
        resposta, model_usado, diagnostico_ia = _perguntas_ia_v2_gerar_resposta(client_id, agent_input)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "output": {
            "success": True,
            "resposta": resposta,
            "answer": resposta,
            "model": model_usado,
            "tool_results": [],
            "diagnostico_ia": diagnostico_ia,
            "modo_ia": ML_PERGUNTAS_IA_V2_MODO,
            "read_only": True,
        }
    }


def ia_chat(payload: IAChatRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    perf_t0 = time.perf_counter()
    contexto = payload.context if isinstance(payload.context, dict) else {}
    mensagem_original = str(payload.message or "").strip()
    fallback_read_only = bool(payload.fallback_read_only)
    modo_rapido_sidebar = fallback_read_only or bool(isinstance(contexto, dict) and contexto.get("modo_rapido_sidebar")) or _ia_chat_eh_pedido_rapido_sidebar(
        mensagem_original,
        contexto if isinstance(contexto, dict) else {},
        payload.attachments or [],
    )
    username = _extrair_username_do_request(request)
    nome_usuario = _ia_nome_usuario(client_id, username) if username else ""
    if username or nome_usuario:
        contexto = dict(contexto)
        contexto["usuario_atual"] = {
            "username": username,
            "nome": nome_usuario or username,
        }
        contexto["nome_usuario"] = nome_usuario or username
        contexto["preferencias_resposta_usuario"] = (
            f"Chame o usuario pelo nome '{nome_usuario or username}' quando for natural, "
            "sem repetir o nome em toda frase. Use a memoria de conversas do usuario para manter continuidade."
        )
        if not modo_rapido_sidebar:
            memoria_usuario = _ia_conversas_contexto_usuario(client_id, username)
            if memoria_usuario:
                contexto["memoria_conversas_usuario"] = memoria_usuario
        payload.context = contexto

    resumo_historico = _ia_chat_resumo_historico(payload.history, limite=10)
    if resumo_historico:
        contexto = dict(contexto)
        contexto["historico_recente"] = resumo_historico
        payload.context = contexto
    if modo_rapido_sidebar:
        contexto = dict(contexto)
        contexto["modo_rapido_sidebar"] = True
        if fallback_read_only:
            contexto["fallback_read_only"] = True
            contexto["restricao_fallback"] = (
                "Fallback estritamente de leitura: responda apenas em texto; nao execute ferramentas, "
                "nao gere arquivos ou imagens e nao proponha como concluida nenhuma alteracao externa."
            )
        payload.context = contexto

    try:
        payload_execucao = payload.model_copy(deep=True)
    except AttributeError:
        payload_execucao = payload.copy(deep=True)
    payload_execucao.message = mensagem_original if modo_rapido_sidebar else _ia_chat_mensagem_contextual(payload)
    if fallback_read_only:
        payload_execucao.message = (
            "MODO FALLBACK ESTRITAMENTE DE LEITURA. Responda somente em texto. "
            "Nao execute nem alegue ter executado alteracoes, ferramentas, envios, arquivos ou imagens.\n\n"
            f"Pedido do usuario: {mensagem_original}"
        )
        payload.message = payload_execucao.message

    perf_tools_t0 = time.perf_counter()
    if modo_rapido_sidebar:
        payload.tool_results = []
    else:
        try:
            payload.tool_results = _ia_chat_executar_funcoes(payload_execucao, client_id)
        except Exception as exc:
            logger.warning(f"[IA TOOLS] Falha ao preparar funcoes do chat: {exc}")
            payload.tool_results = []
    perf_tools = time.perf_counter() - perf_tools_t0
    modelo_chat_padrao = _ia_modelo_chat_configurado()
    if _usuario_pode_escolher_modelo_chat(request, client_id):
        model_req = str(payload.model or "").strip() or modelo_chat_padrao
    else:
        model_req = modelo_chat_padrao
    model_req = _normalizar_ia_modelo_padrao(model_req)
    payload.model = model_req
    # GeraÃƒÂ§ÃƒÂ£o de imagem deve considerar apenas a mensagem atual.
    # O payload_execucao inclui histÃƒÂ³rico recente e pode herdar pedidos antigos como "gere uma imagem".
    resposta_imagem = None if fallback_read_only else _ia_gerar_imagem_sku_resposta(payload, client_id)
    perf_provider_t0 = time.perf_counter()
    if resposta_imagem:
        resposta = resposta_imagem
        model_usado = (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()
    elif _modelo_eh_codex(model_req):
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
    perf_provider = time.perf_counter() - perf_provider_t0

    conversa_id = str(payload.conversa_id or "").strip()
    modulo = str(payload.modulo or payload.page or "assistente").strip() or "assistente"
    mensagens_conversa = payload.conversa_mensagens if isinstance(payload.conversa_mensagens, list) else None
    if conversa_id and mensagens_conversa is not None:
        try:
            mensagens_norm = []
            for msg in mensagens_conversa:
                role = str((msg or {}).get("role") or "user").strip().lower()
                if role not in ("user", "assistant"):
                    role = "user"
                text = str((msg or {}).get("text") or (msg or {}).get("content") or "").strip()
                if not text:
                    continue
                mensagens_norm.append({"role": role, "text": text[:50000]})

            if not mensagens_norm or mensagens_norm[-1].get("role") != "assistant":
                mensagens_norm.append({"role": "assistant", "text": str(resposta or "")[:50000]})

            titulo = "Conversa"
            for msg in mensagens_norm:
                if msg.get("role") == "user" and str(msg.get("text") or "").strip():
                    titulo = str(msg.get("text") or "").strip()[:60]
                    break

            _ia_conversas_salvar(client_id, username, modulo, conversa_id, titulo, mensagens_norm[-80:])
        except Exception as exc:
            logger.warning(f"[IA] Falha ao salvar conversa no /api/ia/chat: {exc}")

    perf_total = time.perf_counter() - perf_t0
    if perf_total >= 2.5:
        logger.info(
            "[IA CHAT PERF] model=%s fast=%s tools=%.2fs provider=%.2fs total=%.2fs tools_count=%s page=%s",
            model_usado,
            modo_rapido_sidebar,
            perf_tools,
            perf_provider,
            perf_total,
            len(payload.tool_results or []),
            str(payload.page or "")[:80],
        )

    return {
        "success": True,
        "model": model_usado,
        "resposta": resposta,
        "tool_results": payload.tool_results or [],
    }


async def ia_listar_modelos(request: Request, client_id: str = Depends(get_tenant_id)):
    pode_escolher_modelo = _usuario_pode_escolher_modelo_chat(request, client_id)
    return {
        "success": True,
        "pode_escolher_modelo_chat": pode_escolher_modelo,
        "codex": _listar_modelos_codex_configuraveis() if pode_escolher_modelo else [],
        "openai": [
            {"name": "gpt-5.4-nano", "display_name": "Nano"},
            {"name": "gpt-5.4-mini", "display_name": "Mini"},
            {"name": "gpt-5.4", "display_name": "GPT-5.4"},
            {"name": "gpt-5.5", "display_name": "GPT-5.5"},
        ] if pode_escolher_modelo else [],
        "deepseek": [
            {"name": "deepseek-v4-flash", "display_name": "DeepSeek V4 Flash"},
            {"name": "deepseek-v4-pro", "display_name": "DeepSeek V4 Pro"},
        ] if pode_escolher_modelo else [],
        "gemini": _listar_modelos_gemini_api() if pode_escolher_modelo else [],
        "vertex": _listar_modelos_vertex_ai() if pode_escolher_modelo else [],
        "defaults": {
            "sistema": _ia_modelo_padrao_configurado(),
            "perguntas": _ia_modelo_perguntas_configurado(),
            "pos_venda": _ia_modelo_pos_venda_configurado(),
            "chat": _ia_modelo_chat_configurado(),
            "favoritos": _ia_modelo_favoritos_configurado(),
            "favoritos_usar_imagem": _ia_favoritos_usar_imagem_configurado(),
            "openai_ativa": _ia_provedor_ativo("openai"),
            "deepseek_ativa": _ia_provedor_ativo("deepseek"),
            "gemini_ativa": _ia_provedor_ativo("gemini"),
            "vertex_ativa": _ia_provedor_ativo("vertex"),
            "openai": (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip(),
            "deepseek": "deepseek-v4-flash",
            "gemini": "gemini:gemini-2.5-flash",
            "vertex": f"vertex:{_vertex_ai_modelo_padrao()}",
            "codex": "codex:gpt-5.5",
            "vertex_project_id": _vertex_ai_project_id_configurado(),
            "vertex_location": _vertex_ai_location(),
            "vertex_service_account_email": _vertex_ai_service_account_email(),
            "agent_api_key_configurada": bool(_vertex_ai_agent_api_key()),
        },
    }


async def ia_salvar_conversa(payload: IASalvarConversaRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    """Salva uma conversa no servidor."""
    username = _extrair_username_do_request(request)
    sucesso = _ia_conversas_salvar(
        client_id,
        username,
        payload.modulo,
        payload.conversa_id,
        payload.titulo,
        payload.mensagens
    )
    return {
        "success": sucesso,
        "conversa_id": payload.conversa_id,
    }


async def ia_listar_conversas(request: Request, modulo: str = None, client_id: str = Depends(get_tenant_id)):
    """Lista conversas salvas do cliente, opcionalmente filtradas por mÃƒÂ³dulo."""
    username = _extrair_username_do_request(request)
    conversas = _ia_conversas_listar(client_id, username, modulo)
    return {
        "success": True,
        "conversas": conversas,
    }


async def ia_carregar_conversa(conversa_id: str, request: Request, client_id: str = Depends(get_tenant_id)):
    """Carrega uma conversa completa com suas mensagens."""
    username = _extrair_username_do_request(request)
    conversa = _ia_conversas_carregar(client_id, conversa_id, username)
    if not conversa:
        raise HTTPException(status_code=404, detail="Conversa nÃ£o encontrada")
    return {
        "success": True,
        "conversa": conversa,
    }


async def ia_deletar_conversa(conversa_id: str, request: Request, client_id: str = Depends(get_tenant_id)):
    """Deleta uma conversa e suas mensagens."""
    username = _extrair_username_do_request(request)
    sucesso = _ia_conversas_deletar(client_id, conversa_id, username)
    return {
        "success": sucesso,
        "conversa_id": conversa_id,
    }


async def ia_rag_status(client_id: str = Depends(get_tenant_id)):
    cfg = _ia_rag_config()
    status = {
        **cfg,
        "client_id": client_id,
        "ollama_ok": False,
        "postgres_ok": False,
        "pgvector_ok": False,
    }

    if cfg.get("backend") == "local":
        status.update(_ia_rag_local_status(client_id))
    else:
        try:
            requests.get(f"{cfg['ollama_base_url']}/api/tags", timeout=5).raise_for_status()
            status["ollama_ok"] = True
        except Exception as exc:
            status["ollama_error"] = str(exc)

    try:
        if cfg.get("backend") == "postgres" and psycopg is not None and _ia_rag_pg_dsn():
            with _ia_rag_conectar() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    status["postgres_ok"] = True
                    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS ok")
                    row = cur.fetchone() or {}
                    status["pgvector_ok"] = bool(row.get("ok"))
    except Exception as exc:
        status["postgres_error"] = str(exc)

    with IA_RAG_REINDEX_LOCK:
        status["reindex_active"] = bool(IA_RAG_REINDEX_ACTIVE.get(client_id))
        status["reindex_status"] = dict(IA_RAG_REINDEX_META.get(client_id) or {})

    return status


async def ia_rag_indexar(payload: IARagIndexRequest, client_id: str = Depends(get_tenant_id)):
    if not _ia_rag_ativo():
        raise HTTPException(
            status_code=503,
            detail="RAG nao configurado. Defina IA_RAG_ENABLED=true e IA_VECTOR_DATABASE_URL no .env."
        )
    try:
        inseridos = _ia_rag_indexar_documentos(client_id, payload.documents or [])
    except Exception as exc:
        logger.exception(f"[IA RAG] Falha ao indexar documentos: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))
    return {"success": True, "inseridos": inseridos}


async def ia_rag_reindexar(payload: IARagReindexRequest = IARagReindexRequest(), client_id: str = Depends(get_tenant_id)):
    if not _ia_rag_ativo():
        raise HTTPException(
            status_code=503,
            detail="RAG nao configurado. Defina IA_RAG_ENABLED=true e IA_VECTOR_DATABASE_URL no .env."
        )

    try:
        info = _ia_rag_iniciar_reindex_async(client_id, force=payload.force)
        return {
            "success": True,
            "background": True,
            **info,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[IA RAG] Falha ao iniciar reindex em background: {exc}")
        raise HTTPException(status_code=502, detail=str(exc))


async def servir_imagem_ia(filename: str):
    nome_original = str(filename or "").replace("\\", "/").strip("/")
    nome_seguro = os.path.basename(nome_original)
    if not nome_seguro:
        raise HTTPException(status_code=404, detail="Arquivo de imagem invÃƒÆ’Ã‚Â¡lido")

    candidatos = []
    try:
        for pasta in os.listdir(PASTA_INFO):
            tenant_dir = os.path.join(PASTA_INFO, pasta)
            if os.path.isdir(tenant_dir):
                candidatos.append(os.path.join(tenant_dir, "ia_imagens", nome_seguro))
    except Exception:
        pass

    caminho_arquivo = next((p for p in candidatos if os.path.exists(p)), None)
    if not caminho_arquivo:
        raise HTTPException(status_code=404, detail="Imagem gerada nÃƒÆ’Ã‚Â£o encontrada")

    response = FileResponse(caminho_arquivo, media_type="image/png")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

configure_ia_endpoints_runtime()

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
