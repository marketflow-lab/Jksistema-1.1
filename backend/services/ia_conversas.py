"""IA saved conversation persistence helpers."""

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

IA_CONVERSATION_SCHEMA_VERSION = "sidebar-conversation-v2"
IA_CONVERSATION_DEFAULT_MODE = "legacy"
IA_CONVERSATION_DEFAULT_SURFACE = "legacy_ia"
IA_CONVERSATION_RECENT_MESSAGES = 10
IA_CONVERSATION_SUMMARY_MAX_CHARS = 4000


def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
    if peers:
        target_globals.update(peers)
    return runtime


def configure_ia_conversas_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _ia_conversas_db_path(client_id: str) -> str:
    """Retorna o caminho do banco SQLite para conversas do cliente."""
    pasta = os.path.join(PASTA_INFO, client_id)
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, "ia_conversas.db")


def _ia_conversas_inicializar_db(client_id: str):
    """Cria as tabelas de conversas se nÃ£o existirem."""
    db_path = _ia_conversas_db_path(client_id)
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Tabela de conversas
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ia_conversas (
                id TEXT PRIMARY KEY,
                usuario TEXT NOT NULL DEFAULT '',
                modulo TEXT NOT NULL,
                titulo TEXT NOT NULL DEFAULT '',
                data_criacao TEXT NOT NULL,
                data_atualizacao TEXT NOT NULL
            )
        """)
        colunas_conversas = {str(row[1]) for row in cur.execute("PRAGMA table_info(ia_conversas)").fetchall()}
        if "usuario" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
        if "external_id" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN external_id TEXT NOT NULL DEFAULT ''")
        if "modo" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN modo TEXT NOT NULL DEFAULT 'legacy'")
        if "surface" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN surface TEXT NOT NULL DEFAULT 'legacy_ia'")
        if "resumo" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN resumo TEXT NOT NULL DEFAULT ''")
        if "codex_thread_id" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN codex_thread_id TEXT NOT NULL DEFAULT ''")
        if "prompt_fingerprint" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN prompt_fingerprint TEXT NOT NULL DEFAULT ''")
        if "schema_fingerprint" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN schema_fingerprint TEXT NOT NULL DEFAULT ''")
        if "scope_fingerprint" not in colunas_conversas:
            cur.execute("ALTER TABLE ia_conversas ADD COLUMN scope_fingerprint TEXT NOT NULL DEFAULT ''")
        cur.execute("UPDATE ia_conversas SET external_id = id WHERE external_id = '' OR external_id IS NULL")
        
        # Tabela de mensagens
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ia_mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversa_id TEXT NOT NULL,
                role TEXT NOT NULL,
                texto TEXT NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY(conversa_id) REFERENCES ia_conversas(id) ON DELETE CASCADE
            )
        """)
        
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error(f"[IA] Erro ao inicializar DB de conversas para {client_id}: {exc}")


def _normalizar_usuario_ia(usuario: str | None) -> str:
    return str(usuario or "").strip().lower()


def _normalizar_modo_conversa_ia(modo: str | None) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "_", str(modo or "").strip().lower()).strip("_")
    return value[:40] or IA_CONVERSATION_DEFAULT_MODE


def _normalizar_surface_conversa_ia(surface: str | None) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "_", str(surface or "").strip().lower()).strip("_")
    return value[:40] or IA_CONVERSATION_DEFAULT_SURFACE


def _normalizar_conversa_id_ia(conversa_id: str | None) -> str:
    value = re.sub(r"[^A-Za-z0-9._:-]+", "_", str(conversa_id or "").strip()).strip("_")
    return value[:160]


def _ia_conversa_storage_id(usuario: str, conversa_id: str, modo: str) -> str:
    """Return a server-owned key isolated by authenticated user and conversation mode."""

    usuario_norm = _normalizar_usuario_ia(usuario)
    conversa_norm = _normalizar_conversa_id_ia(conversa_id)
    modo_norm = _normalizar_modo_conversa_ia(modo)
    digest = hashlib.sha256(
        f"{usuario_norm}\0{modo_norm}\0{conversa_norm}".encode("utf-8", "ignore")
    ).hexdigest()[:32]
    return f"v2_{digest}"


def _ia_conversa_resumo(mensagens: list[dict[str, Any]]) -> str:
    """Compact only older turns; recent messages stay structured and authoritative."""

    older = list(mensagens or [])[:-IA_CONVERSATION_RECENT_MESSAGES]
    if not older:
        return ""
    lines = ["Resumo das mensagens anteriores desta conversa:"]
    for item in older[-16:]:
        role = "Usuario" if str(item.get("role") or "").lower() == "user" else "Assistente"
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if text:
            lines.append(f"- {role}: {text[:320]}")
    return "\n".join(lines)[:IA_CONVERSATION_SUMMARY_MAX_CHARS]


def _ia_nome_usuario(client_id: str, username: str) -> str:
    username_norm = _normalizar_usuario_ia(username)
    if not username_norm:
        return ""

    for loader in (_carregar_usuarios_sql, _carregar_cache_usuarios, _carregar_usuarios_local):
        try:
            if loader is _carregar_usuarios_sql:
                usuarios, _headers = loader(seed_if_empty=True)
            else:
                usuarios, _headers = loader()
        except Exception:
            usuarios = None

        if not isinstance(usuarios, dict) or username_norm not in usuarios:
            continue
        item = usuarios.get(username_norm) or {}
        if str(item.get("client_id") or "").strip() not in {"", str(client_id or "").strip()}:
            continue
        nome = str(item.get("name") or username_norm).strip()
        return nome or username_norm

    return username_norm


def _ia_conversas_salvar(
    client_id: str,
    usuario: str,
    modulo: str,
    conversa_id: str,
    titulo: str,
    mensagens: list,
    *,
    modo: str = IA_CONVERSATION_DEFAULT_MODE,
    surface: str = IA_CONVERSATION_DEFAULT_SURFACE,
    codex_thread_id: str = "",
    prompt_fingerprint: str = "",
    schema_fingerprint: str = "",
    scope_fingerprint: str = "",
):
    """
    Salva ou atualiza uma conversa no banco de dados.
    
    Args:
        client_id: ID do cliente
        usuario: Username do usuario logado
        modulo: Nome do mÃƒÂ³dulo (vendas, estoque, etc)
        conversa_id: ID ÃƒÂºnico da conversa
        titulo: TÃ­tulo/preview da conversa
        mensagens: Lista de dict com {role, text}
    """
    _ia_conversas_inicializar_db(client_id)
    db_path = _ia_conversas_db_path(client_id)
    usuario_norm = _normalizar_usuario_ia(usuario)
    conversa_externa = _normalizar_conversa_id_ia(conversa_id)
    modo_norm = _normalizar_modo_conversa_ia(modo)
    surface_norm = _normalizar_surface_conversa_ia(surface)
    if not usuario_norm or not conversa_externa:
        return False
    storage_id = _ia_conversa_storage_id(usuario_norm, conversa_externa, modo_norm)
    mensagens_norm = []
    for msg in list(mensagens or [])[-80:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip().lower()
        if role not in {"user", "assistant"}:
            role = "user"
        text = str(msg.get("text") or msg.get("content") or "").replace("\x00", "").strip()
        if text:
            mensagens_norm.append({"role": role, "text": text[:50000]})
    resumo = _ia_conversa_resumo(mensagens_norm)
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        agora = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Inserir ou atualizar conversa
        cur.execute("""
            INSERT INTO ia_conversas (
                id, external_id, usuario, modulo, modo, surface, titulo, resumo,
                codex_thread_id, prompt_fingerprint, schema_fingerprint,
                scope_fingerprint, data_criacao, data_atualizacao
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                modulo=excluded.modulo,
                modo=excluded.modo,
                surface=excluded.surface,
                titulo=excluded.titulo,
                resumo=excluded.resumo,
                codex_thread_id=CASE
                    WHEN excluded.codex_thread_id <> '' THEN excluded.codex_thread_id
                    ELSE ia_conversas.codex_thread_id
                END,
                prompt_fingerprint=excluded.prompt_fingerprint,
                schema_fingerprint=excluded.schema_fingerprint,
                scope_fingerprint=excluded.scope_fingerprint,
                data_atualizacao=excluded.data_atualizacao
        """, (
            storage_id,
            conversa_externa,
            usuario_norm,
            str(modulo or "assistente")[:80],
            modo_norm,
            surface_norm,
            str(titulo or "Conversa")[:200],
            resumo,
            str(codex_thread_id or "").strip()[:200],
            str(prompt_fingerprint or "").strip()[:128],
            str(schema_fingerprint or "").strip()[:128],
            str(scope_fingerprint or "").strip()[:128],
            agora,
            agora,
        ))
        
        # Limpar mensagens antigas dessa conversa
        cur.execute("DELETE FROM ia_mensagens WHERE conversa_id = ?", (storage_id,))
        
        # Inserir novas mensagens
        for msg in mensagens_norm:
            cur.execute("""
                INSERT INTO ia_mensagens (conversa_id, role, texto, data)
                VALUES (?, ?, ?, ?)
            """, (storage_id, msg["role"], msg["text"], agora))
        
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error(f"[IA] Erro ao salvar conversa {conversa_id}: {exc}")
        return False


def _ia_conversas_listar(
    client_id: str,
    usuario: str,
    modulo: str = None,
    *,
    modo: str | None = None,
    surface: str | None = None,
) -> list:
    """
    Lista conversas do cliente, opcionalmente filtradas por mÃƒÂ³dulo.
    
    Returns:
        Lista de dict com {id, modulo, titulo, data_criacao, data_atualizacao}
    """
    _ia_conversas_inicializar_db(client_id)
    db_path = _ia_conversas_db_path(client_id)
    usuario_norm = _normalizar_usuario_ia(usuario)
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        clauses = ["usuario = ?"]
        args: list[Any] = [usuario_norm]
        if modulo:
            clauses.append("modulo = ?")
            args.append(modulo)
        if modo:
            clauses.append("modo = ?")
            args.append(_normalizar_modo_conversa_ia(modo))
        if surface:
            clauses.append("surface = ?")
            args.append(_normalizar_surface_conversa_ia(surface))
        cur.execute(f"""
            SELECT external_id AS id, usuario, modulo, modo, surface, titulo, resumo,
                   codex_thread_id, prompt_fingerprint, schema_fingerprint,
                   scope_fingerprint, data_criacao, data_atualizacao
            FROM ia_conversas
            WHERE {' AND '.join(clauses)}
            ORDER BY data_atualizacao DESC
            LIMIT ?
        """, (*args, 20 if modulo else 50))
        
        resultado = [dict(row) for row in cur.fetchall()]
        conn.close()
        return resultado
    except Exception as exc:
        logger.error(f"[IA] Erro ao listar conversas do {client_id}: {exc}")
        return []


def _ia_conversas_carregar(
    client_id: str,
    conversa_id: str,
    usuario: str = "",
    *,
    modo: str | None = None,
    surface: str | None = None,
) -> dict:
    """
    Carrega uma conversa completa com todas as mensagens.
    
    Returns:
        Dict com {id, modulo, titulo, mensagens: [...]}
    """
    _ia_conversas_inicializar_db(client_id)
    db_path = _ia_conversas_db_path(client_id)
    usuario_norm = _normalizar_usuario_ia(usuario)
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Carregar conversa
        conversa_externa = _normalizar_conversa_id_ia(conversa_id)
        clauses = ["external_id = ?", "usuario = ?"]
        args: list[Any] = [conversa_externa, usuario_norm]
        if modo:
            clauses.append("modo = ?")
            args.append(_normalizar_modo_conversa_ia(modo))
        if surface:
            clauses.append("surface = ?")
            args.append(_normalizar_surface_conversa_ia(surface))
        cur.execute(f"""
            SELECT id AS storage_id, external_id AS id, usuario, modulo, modo, surface,
                   titulo, resumo, codex_thread_id, prompt_fingerprint,
                   schema_fingerprint, scope_fingerprint, data_criacao, data_atualizacao
            FROM ia_conversas
            WHERE {' AND '.join(clauses)}
            ORDER BY data_atualizacao DESC
            LIMIT 1
        """, args)
        
        conv_row = cur.fetchone()
        if not conv_row:
            conn.close()
            return {}
        
        # Carregar mensagens
        storage_id = str(dict(conv_row).get("storage_id") or "")
        cur.execute("""
            SELECT role, texto
            FROM ia_mensagens
            WHERE conversa_id = ?
            ORDER BY id ASC
        """, (storage_id,))
        
        mensagens = [{"role": row["role"], "text": row["texto"]} for row in cur.fetchall()]
        conn.close()
        
        return {
            "id": dict(conv_row)["id"],
            "usuario": dict(conv_row)["usuario"],
            "modulo": dict(conv_row)["modulo"],
            "titulo": dict(conv_row)["titulo"],
            "modo": dict(conv_row)["modo"],
            "surface": dict(conv_row)["surface"],
            "resumo": dict(conv_row)["resumo"],
            "codex_thread_id": dict(conv_row)["codex_thread_id"],
            "prompt_fingerprint": dict(conv_row)["prompt_fingerprint"],
            "schema_fingerprint": dict(conv_row)["schema_fingerprint"],
            "scope_fingerprint": dict(conv_row)["scope_fingerprint"],
            "data_criacao": dict(conv_row)["data_criacao"],
            "data_atualizacao": dict(conv_row)["data_atualizacao"],
            "mensagens": mensagens
        }
    except Exception as exc:
        logger.error(f"[IA] Erro ao carregar conversa {conversa_id}: {exc}")
        return {}


def _ia_conversas_deletar(
    client_id: str,
    conversa_id: str,
    usuario: str = "",
    *,
    modo: str | None = None,
) -> bool:
    """Deleta uma conversa e suas mensagens."""
    _ia_conversas_inicializar_db(client_id)
    db_path = _ia_conversas_db_path(client_id)
    usuario_norm = _normalizar_usuario_ia(usuario)
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        clauses = ["external_id = ?", "usuario = ?"]
        args: list[Any] = [_normalizar_conversa_id_ia(conversa_id), usuario_norm]
        if modo:
            clauses.append("modo = ?")
            args.append(_normalizar_modo_conversa_ia(modo))
        storage_ids = [
            str(row[0])
            for row in cur.execute(
                f"SELECT id FROM ia_conversas WHERE {' AND '.join(clauses)}",
                args,
            ).fetchall()
        ]
        cur.execute(f"DELETE FROM ia_conversas WHERE {' AND '.join(clauses)}", args)
        deleted = cur.rowcount
        for storage_id in storage_ids:
            cur.execute("DELETE FROM ia_mensagens WHERE conversa_id = ?", (storage_id,))
        conn.commit()
        conn.close()
        return deleted > 0
    except Exception as exc:
        logger.error(f"[IA] Erro ao deletar conversa {conversa_id}: {exc}")
        return False


def _ia_conversas_contexto_usuario(client_id: str, usuario: str, limite_conversas: int = 5) -> str:
    usuario_norm = _normalizar_usuario_ia(usuario)
    if not usuario_norm:
        return ""

    linhas = []
    for conversa in _ia_conversas_listar(client_id, usuario_norm, None)[:limite_conversas]:
        conversa_id = str(conversa.get("id") or "").strip()
        if not conversa_id:
            continue
        completa = _ia_conversas_carregar(client_id, conversa_id, usuario_norm)
        mensagens = completa.get("mensagens") if isinstance(completa, dict) else []
        if not isinstance(mensagens, list):
            mensagens = []
        recorte = []
        for msg in mensagens[-6:]:
            role = "Usuario" if str((msg or {}).get("role") or "").lower() == "user" else "IA"
            texto = str((msg or {}).get("text") or "").strip()
            if texto:
                recorte.append(f"{role}: {texto[:220]}")
        if recorte:
            linhas.append(
                f"- {str(conversa.get('titulo') or 'Conversa')[:80]} "
                f"({str(conversa.get('modulo') or 'assistente')[:40]}): "
                + " | ".join(recorte)
            )
    return "\n".join(linhas)[:6000]


def _ia_conversas_contexto_conversa(
    client_id: str,
    usuario: str,
    conversa_id: str,
    *,
    modo: str,
    surface: str,
    limite_mensagens: int = IA_CONVERSATION_RECENT_MESSAGES,
) -> dict[str, Any]:
    """Load only the explicitly selected conversation for one user and mode."""

    conversa = _ia_conversas_carregar(
        client_id,
        conversa_id,
        usuario,
        modo=modo,
        surface=surface,
    )
    if not conversa:
        return {
            "schema_version": IA_CONVERSATION_SCHEMA_VERSION,
            "conversation_id": _normalizar_conversa_id_ia(conversa_id),
            "mode": _normalizar_modo_conversa_ia(modo),
            "surface": _normalizar_surface_conversa_ia(surface),
            "summary": "",
            "recent_messages": [],
            "codex_thread_id": "",
            "prompt_fingerprint": "",
            "schema_fingerprint": "",
            "scope_fingerprint": "",
            "updated_at": "",
        }
    recent = []
    for item in list(conversa.get("mensagens") or [])[-max(8, min(12, int(limite_mensagens or 10))) :]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if str(item.get("role") or "").lower() == "assistant" else "user"
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:1800]
        if text:
            recent.append({"role": role, "content": text})
    return {
        "schema_version": IA_CONVERSATION_SCHEMA_VERSION,
        "conversation_id": str(conversa.get("id") or ""),
        "mode": str(conversa.get("modo") or modo),
        "surface": str(conversa.get("surface") or surface),
        "summary": str(conversa.get("resumo") or "")[:IA_CONVERSATION_SUMMARY_MAX_CHARS],
        "recent_messages": recent,
        "codex_thread_id": str(conversa.get("codex_thread_id") or "")[:200],
        "prompt_fingerprint": str(conversa.get("prompt_fingerprint") or "")[:128],
        "schema_fingerprint": str(conversa.get("schema_fingerprint") or "")[:128],
        "scope_fingerprint": str(conversa.get("scope_fingerprint") or "")[:128],
        "updated_at": str(conversa.get("data_atualizacao") or "")[:80],
    }


def _ia_conversas_atualizar_thread_codex(
    client_id: str,
    usuario: str,
    conversa_id: str,
    *,
    modo: str,
    thread_id: str,
) -> bool:
    _ia_conversas_inicializar_db(client_id)
    try:
        with sqlite3.connect(_ia_conversas_db_path(client_id)) as conn:
            cur = conn.execute(
                """
                UPDATE ia_conversas
                SET codex_thread_id = ?, data_atualizacao = ?
                WHERE external_id = ? AND usuario = ? AND modo = ?
                """,
                (
                    str(thread_id or "").strip()[:200],
                    dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    _normalizar_conversa_id_ia(conversa_id),
                    _normalizar_usuario_ia(usuario),
                    _normalizar_modo_conversa_ia(modo),
                ),
            )
            return cur.rowcount > 0
    except Exception as exc:
        if logger is not None:
            logger.warning("[IA] Falha ao atualizar thread Codex da conversa: %s", exc)
        return False

configure_ia_conversas_runtime()

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
