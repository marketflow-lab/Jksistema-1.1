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


def _ia_conversas_salvar(client_id: str, usuario: str, modulo: str, conversa_id: str, titulo: str, mensagens: list):
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
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        agora = datetime.utcnow().isoformat() + "Z"
        
        # Inserir ou atualizar conversa
        cur.execute("""
            INSERT INTO ia_conversas (id, usuario, modulo, titulo, data_criacao, data_atualizacao)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                usuario=excluded.usuario,
                modulo=excluded.modulo,
                titulo=excluded.titulo,
                data_atualizacao=excluded.data_atualizacao
        """, (conversa_id, usuario_norm, modulo, titulo[:200], agora, agora))
        
        # Limpar mensagens antigas dessa conversa
        cur.execute("DELETE FROM ia_mensagens WHERE conversa_id = ?", (conversa_id,))
        
        # Inserir novas mensagens
        for msg in (mensagens or []):
            cur.execute("""
                INSERT INTO ia_mensagens (conversa_id, role, texto, data)
                VALUES (?, ?, ?, ?)
            """, (conversa_id, msg.get("role", "user"), msg.get("text", "")[:50000], agora))
        
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.error(f"[IA] Erro ao salvar conversa {conversa_id}: {exc}")
        return False


def _ia_conversas_listar(client_id: str, usuario: str, modulo: str = None) -> list:
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
        
        if modulo:
            cur.execute("""
                SELECT id, usuario, modulo, titulo, data_criacao, data_atualizacao
                FROM ia_conversas
                WHERE usuario = ? AND modulo = ?
                ORDER BY data_atualizacao DESC
                LIMIT 20
            """, (usuario_norm, modulo))
        else:
            cur.execute("""
                SELECT id, usuario, modulo, titulo, data_criacao, data_atualizacao
                FROM ia_conversas
                WHERE usuario = ?
                ORDER BY data_atualizacao DESC
                LIMIT 50
            """, (usuario_norm,))
        
        resultado = [dict(row) for row in cur.fetchall()]
        conn.close()
        return resultado
    except Exception as exc:
        logger.error(f"[IA] Erro ao listar conversas do {client_id}: {exc}")
        return []


def _ia_conversas_carregar(client_id: str, conversa_id: str, usuario: str = "") -> dict:
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
        cur.execute("""
            SELECT id, usuario, modulo, titulo, data_criacao, data_atualizacao
            FROM ia_conversas
            WHERE id = ? AND usuario = ?
        """, (conversa_id, usuario_norm))
        
        conv_row = cur.fetchone()
        if not conv_row:
            conn.close()
            return {}
        
        # Carregar mensagens
        cur.execute("""
            SELECT role, texto
            FROM ia_mensagens
            WHERE conversa_id = ?
            ORDER BY id ASC
        """, (conversa_id,))
        
        mensagens = [{"role": row["role"], "text": row["texto"]} for row in cur.fetchall()]
        conn.close()
        
        return {
            "id": dict(conv_row)["id"],
            "usuario": dict(conv_row)["usuario"],
            "modulo": dict(conv_row)["modulo"],
            "titulo": dict(conv_row)["titulo"],
            "data_criacao": dict(conv_row)["data_criacao"],
            "data_atualizacao": dict(conv_row)["data_atualizacao"],
            "mensagens": mensagens
        }
    except Exception as exc:
        logger.error(f"[IA] Erro ao carregar conversa {conversa_id}: {exc}")
        return {}


def _ia_conversas_deletar(client_id: str, conversa_id: str, usuario: str = "") -> bool:
    """Deleta uma conversa e suas mensagens."""
    _ia_conversas_inicializar_db(client_id)
    db_path = _ia_conversas_db_path(client_id)
    usuario_norm = _normalizar_usuario_ia(usuario)
    
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM ia_conversas WHERE id = ? AND usuario = ?", (conversa_id, usuario_norm))
        conn.commit()
        conn.close()
        return cur.rowcount > 0
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
