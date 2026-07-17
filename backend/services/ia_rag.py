"""IA RAG indexing, local vector search and reindex worker."""

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


def configure_ia_rag_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _ia_rag_backend_configurado() -> str:
    valor = (
        os.getenv("IA_RAG_BACKEND")
        or os.getenv("JK_IA_RAG_BACKEND")
        or "auto"
    ).strip().lower().replace("-", "_")
    aliases = {
        "sqlite": "local",
        "embedded": "local",
        "embutido": "local",
        "local_sqlite": "local",
        "pg": "postgres",
        "postgresql": "postgres",
    }
    return aliases.get(valor, valor if valor in {"auto", "local", "postgres"} else "auto")


def _ia_rag_pg_dsn() -> str:
    return (
        os.getenv("IA_VECTOR_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or ""
    ).strip()


def _ia_rag_backend_efetivo() -> str:
    backend = _ia_rag_backend_configurado()
    if backend == "local":
        return "local"
    if backend == "postgres":
        return "postgres"
    return "postgres" if _ia_rag_pg_dsn() else "local"


def _ia_rag_ativo() -> bool:
    valor = (os.getenv("IA_RAG_ENABLED") or "").strip().lower()
    if valor not in {"1", "true", "yes", "sim", "on"}:
        return False
    return _ia_rag_backend_efetivo() == "local" or bool(_ia_rag_pg_dsn())


def _ia_rag_env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(str(name or "").strip())
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "sim", "on"}


def _ia_rag_legacy_read_enabled() -> bool:
    """Legacy retrieval is opt-in while Context Hub owns trusted retrieval."""
    return _ia_rag_env_bool("IA_RAG_LEGACY_READ_ENABLED", default=False)


def _ia_rag_legacy_write_enabled() -> bool:
    """Legacy indexing is opt-in and additionally protected by full-admin routes."""
    return _ia_rag_env_bool("IA_RAG_LEGACY_WRITE_ENABLED", default=False)


def _ia_rag_legacy_generic_scan_enabled() -> bool:
    """Generic recursive JSON/CSV/SQLite ingestion stays disabled by default."""
    return _ia_rag_env_bool("IA_RAG_LEGACY_GENERIC_SCAN_ENABLED", default=False)


def _ia_rag_legacy_force_replace_enabled() -> bool:
    """Destructive replacement needs an independent emergency opt-in."""
    return _ia_rag_env_bool("IA_RAG_LEGACY_FORCE_REPLACE_ENABLED", default=False)


_IA_RAG_LEGACY_DLP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|password|senha)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
        ),
    ),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("cpf_cnpj", re.compile(r"(?<!\d)(?:\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}(?!\d)")),
    ("address", re.compile(r"\b(?:rua|avenida|av\.|travessa|alameda|rodovia)\s+[^\n,]{2,80},?\s+\d{1,6}\b", re.I)),
)
_IA_RAG_LEGACY_DLP_METADATA_KEY_RE = re.compile(
    r"(?:token|secret|api[_-]?key|senha|password|jwt|oauth|email|telefone|phone|cpf|cnpj|endere[cç]o|address|buyer|comprador)",
    re.I,
)


def _ia_rag_legacy_dlp_codes(documento: IARagDocumento) -> list[str]:
    values = [str(documento.title or ""), str(documento.source or ""), str(documento.content or "")]
    try:
        metadata_text = json.dumps(documento.metadata or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        metadata_text = ""
    values.append(metadata_text)
    combined = "\n".join(values)
    codes = {code for code, pattern in _IA_RAG_LEGACY_DLP_PATTERNS if pattern.search(combined)}

    def scan_keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _IA_RAG_LEGACY_DLP_METADATA_KEY_RE.search(str(key or "")):
                    codes.add("sensitive_metadata")
                scan_keys(child)
        elif isinstance(value, list):
            for child in value:
                scan_keys(child)

    scan_keys(documento.metadata or {})
    return sorted(codes)


def _ia_rag_config() -> dict:
    backend = _ia_rag_backend_efetivo()
    return {
        "enabled": _ia_rag_ativo(),
        "legacy_read_enabled": _ia_rag_legacy_read_enabled(),
        "legacy_write_enabled": _ia_rag_legacy_write_enabled(),
        "legacy_generic_scan_enabled": _ia_rag_legacy_generic_scan_enabled(),
        "legacy_force_replace_enabled": _ia_rag_legacy_force_replace_enabled(),
        "backend": backend,
        "backend_configurado": _ia_rag_backend_configurado(),
        "postgres_configurado": bool(_ia_rag_pg_dsn()),
        "psycopg_instalado": psycopg is not None,
        "ollama_base_url": (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/"),
        "ollama_embedding_model": (os.getenv("OLLAMA_EMBED_MODEL") or "nomic-embed-text").strip(),
        "top_k": int(os.getenv("IA_RAG_TOP_K") or "5"),
        "local_dim": _ia_rag_local_dim(),
        "local_engine": "sqlite-hash",
        "usa_ollama": backend == "postgres",
    }


def _ia_rag_conectar():
    if psycopg is None:
        raise RuntimeError("Dependencia psycopg nao instalada.")
    dsn = _ia_rag_pg_dsn()
    if not dsn:
        raise RuntimeError("IA_VECTOR_DATABASE_URL/DATABASE_URL nao configurada.")
    return psycopg.connect(dsn, row_factory=dict_row)


def _ia_rag_embedding_sql(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in embedding) + "]"


def _ia_rag_cosine_score(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for va, vb in zip(a, b):
        fa = float(va)
        fb = float(vb)
        dot += fa * fb
        norm_a += fa * fa
        norm_b += fb * fb
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


IA_RAG_LOCAL_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "esta", "este", "isso", "na", "nas", "no", "nos", "o", "os",
    "ou", "para", "por", "qual", "quais", "que", "se", "sem", "sobre", "um",
    "uma", "vendas", "dados", "todos", "todas", "sistema",
}


def _ia_rag_local_dim() -> int:
    try:
        return max(128, min(int(os.getenv("IA_RAG_LOCAL_DIM") or "512"), 4096))
    except Exception:
        return 512


def _ia_rag_normalizar_texto_busca(texto: str) -> str:
    raw = str(texto or "").lower()
    sem_acento = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return sem_acento


def _ia_rag_local_tokens(texto: str) -> list[str]:
    normalizado = _ia_rag_normalizar_texto_busca(texto)
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", normalizado)
    filtrados = []
    for token in tokens:
        token = token.strip("_-")
        if len(token) < 2 or token in IA_RAG_LOCAL_STOPWORDS:
            continue
        filtrados.append(token[:80])
    return filtrados[:1200]


def _ia_rag_local_hash_index(token: str, dim: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8", "ignore"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _ia_rag_local_vector(texto: str, dim: Optional[int] = None) -> np.ndarray:
    dim = int(dim or _ia_rag_local_dim())
    vetor = np.zeros(dim, dtype=np.float32)
    tokens = _ia_rag_local_tokens(texto)
    if not tokens:
        return vetor

    for token in tokens:
        peso = 1.0
        if any(ch.isdigit() for ch in token):
            peso = 1.35
        vetor[_ia_rag_local_hash_index(token, dim)] += peso

    for atual, proximo in zip(tokens, tokens[1:]):
        if atual != proximo:
            vetor[_ia_rag_local_hash_index(f"{atual}_{proximo}", dim)] += 0.75

    norma = float(np.linalg.norm(vetor))
    if norma > 0:
        vetor = vetor / norma
    return vetor.astype(np.float32, copy=False)


def _ia_rag_local_db_path(client_id: str) -> str:
    client_norm = str(client_id or "default").strip() or "default"
    pasta = os.path.join(PASTA_INFO, client_norm)
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, "ia_rag_local.db")


def _ia_rag_local_conectar(client_id: str):
    db_path = _ia_rag_local_db_path(client_id)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ia_rag_local_documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            vector BLOB NOT NULL,
            dim INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ia_rag_local_cliente_source_idx
        ON ia_rag_local_documentos (client_id, source)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ia_rag_local_created_idx
        ON ia_rag_local_documentos (created_at)
        """
    )
    conn.commit()
    return conn


def _ia_rag_local_blob_para_vetor(blob, dim: int) -> Optional[np.ndarray]:
    if blob is None:
        return None
    try:
        vetor = np.frombuffer(bytes(blob), dtype=np.float32)
        if vetor.size != int(dim):
            return None
        return vetor
    except Exception:
        return None


def _ia_rag_local_text_boost(query_terms: list[str], row: sqlite3.Row) -> float:
    if not query_terms:
        return 0.0
    texto = _ia_rag_normalizar_texto_busca(
        f"{row['title']} {row['source']} {row['content'][:5000]}"
    )
    hits = 0
    for termo in query_terms[:8]:
        termo_norm = _ia_rag_normalizar_texto_busca(termo)
        if termo_norm and termo_norm in texto:
            hits += 1
    return min(0.22, hits * 0.045)


def _ia_rag_local_status(client_id: str) -> dict:
    db_path = _ia_rag_local_db_path(client_id)
    status = {
        "local_db_name": os.path.basename(db_path),
        "local_db_exists": os.path.exists(db_path),
        "local_db_mb": 0.0,
        "local_documentos": 0,
        "local_ok": False,
        "local_error": "",
    }
    try:
        if os.path.exists(db_path):
            status["local_db_mb"] = round(os.path.getsize(db_path) / (1024 * 1024), 3)
        with _ia_rag_local_conectar(client_id) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM ia_rag_local_documentos WHERE client_id = ?",
                (str(client_id or "default").strip() or "default",),
            ).fetchone()
            status["local_documentos"] = int(row["total"] if row else 0)
            status["local_ok"] = True
    except Exception as exc:
        status["local_error"] = type(exc).__name__
    return status


def _ia_rag_termos_busca(query: str) -> list[str]:
    texto = str(query or "")
    candidatos = re.findall(r"[\w._/-]{3,}", texto, flags=re.UNICODE)
    termos = []
    for termo in candidatos:
        termo = termo.strip()
        if not termo:
            continue
        if termo.lower() in {"como", "qual", "quais", "para", "sobre", "todos", "dados", "vendas"}:
            continue
        if termo not in termos:
            termos.append(termo[:80])
    return termos[:6]


def _ia_rag_coluna_relevante(coluna: str, arquivo: str = "", tabela: str = "") -> bool:
    nome = str(coluna or "").strip().lower()
    nome_norm = re.sub(r"[^a-z0-9_/-]+", "_", nome)
    if not nome_norm:
        return False

    irrelevantes_exatos = {
        "id",
        "id_unico",
        "raw_json",
        "dados_extras_json",
        "metadata",
        "hash",
        "checksum",
        "created_at",
        "updated_at",
        "recorded_at",
        "last_seen",
        "machine_id",
    }
    if nome_norm in irrelevantes_exatos:
        return False

    irrelevantes_parciais = (
        "token",
        "secret",
        "senha",
        "password",
        "credential",
        "api_key",
        "access_",
        "refresh_",
        "oauth",
        "raw_",
        "payload",
        "trace",
        "stack",
        "cookie",
        "session",
    )
    if any(t in nome_norm for t in irrelevantes_parciais):
        return False

    relevantes_parciais = (
        "sku",
        "produto",
        "nome",
        "descricao",
        "categoria",
        "marca",
        "ncm",
        "cest",
        "preco",
        "custo",
        "imposto",
        "saldo",
        "estoque",
        "quantidade",
        "qtd",
        "valor",
        "data",
        "mes",
        "loja",
        "canal",
        "situacao",
        "devolucao",
        "fornecedor",
        "comprador",
        "numero",
        "natureza",
        "finalidade",
        "unidade",
        "cfop",
        "gtin",
        "oem",
        "mlb",
        "anuncio",
        "titulo",
        "frete",
        "mva",
        "ii",
        "ipi",
        "pis",
        "cofins",
        "observacao",
        "fonte",
    )
    return any(t in nome_norm for t in relevantes_parciais)


def _ia_rag_valor_relevante(valor) -> str:
    texto = _ia_txt(valor, 220)
    if not texto:
        return ""
    if len(texto) > 80 and re.fullmatch(r"[A-Za-z0-9|,;:._/-]+", texto):
        return texto[:80]
    return texto


def _ia_rag_gerar_embedding(texto: str) -> list[float]:
    embeddings = _ia_rag_gerar_embeddings([texto])
    if not embeddings:
        raise RuntimeError("Ollama nao retornou embedding.")
    return embeddings[0]


def _ia_rag_gerar_embeddings(textos: list[str]) -> list[list[float]]:
    textos_limpos = []
    for texto in textos:
        valor = str(texto or "").strip()
        if not valor:
            continue
        textos_limpos.append(valor[:3500])
    if not textos_limpos:
        return []

    if len(textos_limpos) == 1:
        entrada = textos_limpos[0]
    else:
        entrada = textos_limpos

    cfg = _ia_rag_config()
    base_url = cfg["ollama_base_url"]
    model = cfg["ollama_embedding_model"]

    try:
        resp = requests.post(
            f"{base_url}/api/embed",
            json={"model": model, "input": entrada},
            timeout=180,
        )
        if resp.status_code == 404:
            resp = requests.post(
                f"{base_url}/api/embeddings",
                json={"model": model, "prompt": textos_limpos[0]},
                timeout=180,
            )
    except requests.RequestException as exc:
        raise RuntimeError(f"Falha ao conectar no Ollama: {exc}") from exc

    if not resp.ok:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    embedding = data.get("embedding")
    if embedding is None:
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return [[float(v) for v in emb] for emb in embeddings if isinstance(emb, list)]

    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("Ollama nao retornou embedding.")

    return [[float(v) for v in embedding]]


def _ia_rag_garantir_schema(conn, dimensao: int) -> bool:
    pgvector_ok = False
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            pgvector_ok = True
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.warning(f"[IA RAG] pgvector indisponivel, usando fallback JSON: {exc}")

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ia_rag_documentos (
                id bigserial PRIMARY KEY,
                client_id text NOT NULL,
                source text NOT NULL DEFAULT 'manual',
                title text NOT NULL DEFAULT '',
                content text NOT NULL,
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                embedding_json jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ia_rag_documentos_cliente_idx
            ON ia_rag_documentos (client_id)
            """
        )
        if pgvector_ok:
            cur.execute(
                f"""
                ALTER TABLE ia_rag_documentos
                ADD COLUMN IF NOT EXISTS embedding vector({int(dimensao)})
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ia_rag_documentos_embedding_idx
                ON ia_rag_documentos
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )
    conn.commit()
    return pgvector_ok


def _ia_rag_indexar_documentos(client_id: str, documentos: list[IARagDocumento]) -> int:
    if not _ia_rag_legacy_write_enabled():
        raise PermissionError("A gravacao no RAG legado esta desativada.")
    if not documentos:
        return 0

    docs_validos = [doc for doc in documentos if str(doc.content or "").strip()]
    if not docs_validos:
        return 0
    dlp_codes = sorted({code for doc in docs_validos for code in _ia_rag_legacy_dlp_codes(doc)})
    if dlp_codes:
        raise ValueError(
            "Documento bloqueado pela politica DLP do RAG legado (codigos: "
            + ", ".join(dlp_codes)
            + ")."
        )

    if _ia_rag_backend_efetivo() == "local":
        dim = _ia_rag_local_dim()
        inseridos = 0
        client_norm = str(client_id or "default").strip() or "default"
        with _ia_rag_local_conectar(client_norm) as conn:
            for doc in docs_validos:
                texto = str(doc.content or "").strip()
                vetor = _ia_rag_local_vector(
                    f"{doc.title or ''}\n{doc.source or ''}\n{texto}",
                    dim,
                )
                if not np.any(vetor):
                    continue
                conn.execute(
                    """
                    INSERT INTO ia_rag_local_documentos
                        (client_id, source, title, content, metadata, vector, dim)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        client_norm,
                        str(doc.source or "manual")[:200],
                        str(doc.title or "")[:300],
                        texto,
                        json.dumps(doc.metadata or {}, ensure_ascii=False, default=str),
                        vetor.tobytes(),
                        dim,
                    ),
                )
                inseridos += 1
            conn.commit()
        return inseridos

    primeiro_embedding = _ia_rag_gerar_embedding(str(docs_validos[0].content or "").strip())

    inseridos = 0
    with _ia_rag_conectar() as conn:
        pgvector_ok = _ia_rag_garantir_schema(conn, len(primeiro_embedding))
        with conn.cursor() as cur:
            tamanho_lote = max(1, int(os.getenv("IA_RAG_BATCH_SIZE") or "4"))
            for inicio in range(0, len(docs_validos), tamanho_lote):
                lote = docs_validos[inicio:inicio + tamanho_lote]
                textos = [str(doc.content or "").strip() for doc in lote]
                embeddings = _ia_rag_gerar_embeddings(textos)
                if len(embeddings) != len(lote):
                    embeddings = [_ia_rag_gerar_embedding(texto) for texto in textos]

                for doc, texto, embedding in zip(lote, textos, embeddings):
                    params_base = (
                        client_id,
                        str(doc.source or "manual")[:200],
                        str(doc.title or "")[:300],
                        texto,
                        json.dumps(doc.metadata or {}, ensure_ascii=False, default=str),
                        json.dumps(embedding),
                    )
                    if pgvector_ok:
                        cur.execute(
                            """
                            INSERT INTO ia_rag_documentos
                                (client_id, source, title, content, metadata, embedding_json, embedding)
                            VALUES
                                (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector)
                            """,
                            (*params_base, _ia_rag_embedding_sql(embedding)),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO ia_rag_documentos
                                (client_id, source, title, content, metadata, embedding_json)
                            VALUES
                                (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                            """,
                            params_base,
                        )
                    inseridos += 1
                conn.commit()
    return inseridos


def _ia_rag_buscar(query: str, client_id: str, top_k: Optional[int] = None) -> list[dict]:
    if not _ia_rag_ativo():
        return []

    if _ia_rag_backend_efetivo() == "local":
        dim = _ia_rag_local_dim()
        query_vec = _ia_rag_local_vector(query, dim)
        if not np.any(query_vec):
            return _ia_rag_busca_textual(query, client_id, top_k)
        limite = int(top_k or _ia_rag_config()["top_k"] or 5)
        scan_limit = max(limite * 20, int(os.getenv("IA_RAG_LOCAL_SCAN_LIMIT") or "5000"))
        client_norm = str(client_id or "default").strip() or "default"
        termos = _ia_rag_termos_busca(query)
        resultados = []
        with _ia_rag_local_conectar(client_norm) as conn:
            rows = conn.execute(
                """
                SELECT id, source, title, content, metadata, vector, dim
                FROM ia_rag_local_documentos
                WHERE client_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (client_norm, scan_limit),
            ).fetchall()
            for row in rows:
                vetor = _ia_rag_local_blob_para_vetor(row["vector"], int(row["dim"] or dim))
                if vetor is None or vetor.size != query_vec.size:
                    continue
                score = float(np.dot(query_vec, vetor)) + _ia_rag_local_text_boost(termos, row)
                if score <= 0:
                    continue
                try:
                    metadata = json.loads(row["metadata"] or "{}")
                except Exception:
                    metadata = {}
                resultados.append({
                    "id": row["id"],
                    "source": row["source"],
                    "title": row["title"],
                    "content": row["content"],
                    "metadata": metadata,
                    "score": min(score, 1.0),
                })
        resultados.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        return resultados[: max(limite, min(12, limite + len(termos)))]

    embedding = _ia_rag_gerar_embedding(query)
    embedding_sql = _ia_rag_embedding_sql(embedding)
    limite = int(top_k or _ia_rag_config()["top_k"] or 5)

    with _ia_rag_conectar() as conn:
        _ia_rag_garantir_schema(conn, len(embedding))
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    source,
                    title,
                    content,
                    metadata,
                    1 - (embedding <=> %s::vector) AS score
                FROM ia_rag_documentos
                WHERE client_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (embedding_sql, client_id, embedding_sql, limite),
            )
            resultados = list(cur.fetchall() or [])
            vistos = {row.get("id") for row in resultados}
            termos = _ia_rag_termos_busca(query)
            for termo in termos:
                cur.execute(
                    """
                    SELECT id, source, title, content, metadata, 0.650 AS score
                    FROM ia_rag_documentos
                    WHERE client_id = %s
                      AND content ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT 3
                    """,
                    (client_id, f"%{termo}%"),
                )
                for row in list(cur.fetchall() or []):
                    if row.get("id") not in vistos:
                        resultados.append(row)
                        vistos.add(row.get("id"))
            resultados.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
            return resultados[: max(limite, min(12, limite + len(termos)))]


def _ia_rag_busca_textual(query: str, client_id: str, top_k: Optional[int] = None) -> list[dict]:
    if not _ia_rag_ativo():
        return []

    limite = int(top_k or _ia_rag_config()["top_k"] or 5)
    termos = _ia_rag_termos_busca(query)
    if not termos:
        bruto = str(query or "").strip()
        if bruto:
            termos = [bruto[:80]]
    if not termos:
        return []

    if _ia_rag_backend_efetivo() == "local":
        limite = int(top_k or _ia_rag_config()["top_k"] or 5)
        client_norm = str(client_id or "default").strip() or "default"
        resultados: list[dict] = []
        vistos: set[int] = set()
        with _ia_rag_local_conectar(client_norm) as conn:
            for termo in termos[:4]:
                rows = conn.execute(
                    """
                    SELECT id, source, title, content, metadata
                    FROM ia_rag_local_documentos
                    WHERE client_id = ? AND content LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (client_norm, f"%{termo}%", max(4, limite)),
                ).fetchall()
                for row in rows:
                    if int(row["id"]) in vistos:
                        continue
                    vistos.add(int(row["id"]))
                    try:
                        metadata = json.loads(row["metadata"] or "{}")
                    except Exception:
                        metadata = {}
                    resultados.append({
                        "id": row["id"],
                        "source": row["source"],
                        "title": row["title"],
                        "content": row["content"],
                        "metadata": metadata,
                        "score": 0.55 + _ia_rag_local_text_boost(termos, row),
                    })
        resultados.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        return resultados[:limite]

    termos_top = termos[:3]
    resultados: list[dict] = []
    vistos: set[int] = set()
    with _ia_rag_conectar() as conn:
        with conn.cursor() as cur:
            # Query ÃƒÂºnica com OR para todos os termos Ã¢â‚¬â€ evita N round-trips ao banco
            conditions = " OR ".join(["content ILIKE %s"] * len(termos_top))
            params = [client_id] + [f"%{t}%" for t in termos_top] + [max(5, limite * len(termos_top))]
            cur.execute(
                f"""
                SELECT id, source, title, content, metadata, 0.550 AS score
                FROM ia_rag_documentos
                WHERE client_id = %s
                  AND ({conditions})
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            for row in list(cur.fetchall() or []):
                rid = row.get("id")
                if rid in vistos:
                    continue
                resultados.append(row)
                vistos.add(rid)

    return resultados[:limite]


def _ia_rag_contexto(query: str, client_id: str) -> str:
    if not _ia_rag_legacy_read_enabled():
        return ""
    if bool(IA_RAG_REINDEX_ACTIVE.get(client_id)):
        return ""

    timeout_s = max(0.3, float(os.getenv("IA_RAG_SEARCH_TIMEOUT_S") or "2.5"))
    resultados = []
    try:
        future = IA_RAG_SEARCH_EXECUTOR.submit(_ia_rag_buscar, query, client_id)
        resultados = future.result(timeout=timeout_s)
    except FuturesTimeoutError:
        try:
            future.cancel()
        except Exception:
            pass
        logger.warning(f"[IA RAG] Busca semantica excedeu timeout ({timeout_s:.1f}s); usando fallback textual.")
        try:
            resultados = _ia_rag_busca_textual(query, client_id)
        except Exception as exc:
            logger.warning(f"[IA RAG] Fallback textual indisponivel: {type(exc).__name__}: {exc}")
            return ""
    except Exception as exc:
        logger.warning(f"[IA RAG] Busca indisponivel: {type(exc).__name__}: {exc}; tentando fallback textual.")
        try:
            resultados = _ia_rag_busca_textual(query, client_id)
        except Exception as fallback_exc:
            logger.warning(f"[IA RAG] Fallback textual indisponivel: {type(fallback_exc).__name__}: {fallback_exc}")
            return ""

    partes = []
    for item in resultados:
        titulo = str(item.get("title") or item.get("source") or "Documento").strip()
        score = item.get("score")
        conteudo = str(item.get("content") or "").strip()
        if not conteudo:
            continue
        partes.append(f"- {titulo} (score={float(score or 0):.3f}):\n{conteudo[:1800]}")
    return "\n\n".join(partes).strip()


def _ia_rag_limpar_fontes(client_id: str, prefixo: str) -> int:
    if _ia_rag_backend_efetivo() == "local":
        client_norm = str(client_id or "default").strip() or "default"
        with _ia_rag_local_conectar(client_norm) as conn:
            cur = conn.execute(
                "DELETE FROM ia_rag_local_documentos WHERE client_id = ? AND source LIKE ?",
                (client_norm, f"{prefixo}%"),
            )
            removidos = cur.rowcount or 0
            conn.commit()
            return removidos

    if not _ia_rag_pg_dsn() or psycopg is None:
        return 0
    with _ia_rag_conectar() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ia_rag_documentos WHERE client_id = %s AND source LIKE %s",
                (client_id, f"{prefixo}%"),
            )
            removidos = cur.rowcount or 0
        conn.commit()
    return removidos


def _ia_rag_fontes_existentes(client_id: str, prefixo: str = "jkdata:") -> set[str]:
    if _ia_rag_backend_efetivo() == "local":
        client_norm = str(client_id or "default").strip() or "default"
        try:
            with _ia_rag_local_conectar(client_norm) as conn:
                rows = conn.execute(
                    "SELECT source FROM ia_rag_local_documentos WHERE client_id = ? AND source LIKE ?",
                    (client_norm, f"{prefixo}%"),
                ).fetchall()
                return {str(row["source"] or "") for row in rows}
        except Exception:
            return set()

    if not _ia_rag_pg_dsn() or psycopg is None:
        return set()
    try:
        with _ia_rag_conectar() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT source FROM ia_rag_documentos WHERE client_id = %s AND source LIKE %s",
                    (client_id, f"{prefixo}%"),
                )
                return {str(row.get("source") or "") for row in list(cur.fetchall() or [])}
    except Exception:
        return set()


def _ia_txt(valor, limite: int = 160) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    texto = str(valor).strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto[:limite]


def _ia_rag_coluna_expr(colunas: set[str], candidatos: tuple[str, ...], default: str = "''") -> str:
    for coluna in candidatos:
        if coluna in colunas:
            return f"COALESCE({coluna}, {default})"
    return default


def _ia_rag_colunas_tabela(conn, tabela: str) -> set[str]:
    cur = conn.cursor()
    try:
        cur.execute(f'PRAGMA table_info("{tabela}")')
        return {str(row[1] or "").strip() for row in cur.fetchall() if row and row[1]}
    finally:
        try:
            cur.close()
        except Exception:
            pass


def _ia_float(valor) -> float:
    if valor is None:
        return 0.0
    texto = str(valor).strip().replace("R$", "").replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except Exception:
        return 0.0


def _ia_rag_doc(title: str, source: str, content: str, metadata: Optional[dict] = None) -> IARagDocumento:
    return IARagDocumento(
        title=title,
        source=source,
        content=content,
        metadata=metadata or {},
    )


def _ia_rag_deve_ignorar_arquivo(caminho: str) -> bool:
    rel = os.path.relpath(caminho, PASTA_INFO).replace("\\", "/").lower()
    nome = os.path.basename(caminho).lower()
    partes = set(rel.split("/"))
    if "tmp" in partes or "cache" in nome or "backup" in nome or ".backup" in nome:
        return True
    termos_sensiveis = (
        "credential",
        "credentials",
        "secret",
        "token",
        "api_key",
        "openai",
        "jwt",
        "temp_integracao",
    )
    return any(termo in nome for termo in termos_sensiveis)


def _ia_rag_listar_arquivos_por_prefixos(client_id: str, prefixos: tuple[str, ...], extensoes: tuple[str, ...]) -> list[str]:
    tenant_path = get_tenant_path(client_id)
    if not os.path.isdir(tenant_path):
        return []
    arquivos = []
    for raiz, dirs, nomes in os.walk(tenant_path):
        dirs[:] = [d for d in dirs if d.lower() != "tmp"]
        for nome in nomes:
            nome_lower = nome.lower()
            if extensoes and not any(nome_lower.endswith(ext) for ext in extensoes):
                continue
            if prefixos and not any(nome_lower.startswith(pfx) for pfx in prefixos):
                continue
            caminho = os.path.join(raiz, nome)
            if _ia_rag_deve_ignorar_arquivo(caminho):
                continue
            arquivos.append(caminho)
    return sorted(dict.fromkeys(arquivos))


def _ia_rag_lote_linhas(linhas: list[str], tamanho: int = 120):
    for idx in range(0, len(linhas), tamanho):
        yield idx // tamanho + 1, linhas[idx:idx + tamanho]


def _ia_rag_docs_csv_generico(client_id: str, caminho: str) -> list[IARagDocumento]:
    try:
        df = pd.read_csv(caminho, dtype=str).fillna("")
    except Exception as exc:
        logger.warning(f"[IA RAG] Falha ao ler CSV {caminho}: {exc}")
        return []

    rel = os.path.relpath(caminho, get_tenant_path(client_id)).replace("\\", "/")
    linhas = []
    for idx, row in df.iterrows():
        pares = []
        for col, val in row.items():
            if not _ia_rag_coluna_relevante(col, rel):
                continue
            texto = _ia_rag_valor_relevante(val)
            if texto:
                pares.append(f"{col}: {texto}")
        if pares:
            linhas.append(f"Linha {idx + 1}: " + "; ".join(pares))

    docs = []
    for bloco_idx, bloco in _ia_rag_lote_linhas(linhas, 180):
        docs.append(_ia_rag_doc(
            title=f"Dados completos CSV {rel} bloco {bloco_idx}",
            source=f"jkdata:full:csv:{rel}:{bloco_idx:05d}",
            content=f"Arquivo CSV completo: {rel}\n" + "\n".join(bloco),
            metadata={"tipo": "csv_completo", "arquivo": rel, "bloco": bloco_idx},
        ))
    return docs


def _ia_rag_docs_json_generico(client_id: str, caminho: str) -> list[IARagDocumento]:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning(f"[IA RAG] Falha ao ler JSON {caminho}: {exc}")
        return []

    rel = os.path.relpath(caminho, get_tenant_path(client_id)).replace("\\", "/")
    texto = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    docs = []
    tamanho = 4500
    for idx in range(0, len(texto), tamanho):
        bloco_idx = idx // tamanho + 1
        docs.append(_ia_rag_doc(
            title=f"Dados completos JSON {rel} bloco {bloco_idx}",
            source=f"jkdata:full:json:{rel}:{bloco_idx:05d}",
            content=f"Arquivo JSON completo: {rel}\n{texto[idx:idx + tamanho]}",
            metadata={"tipo": "json_completo", "arquivo": rel, "bloco": bloco_idx},
        ))
    return docs


def _ia_rag_docs_sqlite_generico(client_id: str, caminho: str) -> list[IARagDocumento]:
    rel = os.path.relpath(caminho, get_tenant_path(client_id)).replace("\\", "/")
    docs = []
    try:
        conn = sqlite3.connect(caminho)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        tabelas = [
            row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if row[0] != "sqlite_sequence"
        ]
        for tabela in tabelas:
            rows = cur.execute(f'SELECT * FROM "{tabela}"').fetchall()
            linhas = []
            for idx, row in enumerate(rows):
                pares = []
                for key in row.keys():
                    if not _ia_rag_coluna_relevante(key, rel, tabela):
                        continue
                    texto = _ia_rag_valor_relevante(row[key])
                    if texto:
                        pares.append(f"{key}: {texto}")
                if pares:
                    linhas.append(f"Linha {idx + 1}: " + "; ".join(pares))
            for bloco_idx, bloco in _ia_rag_lote_linhas(linhas, 180):
                docs.append(_ia_rag_doc(
                    title=f"Dados completos SQLite {rel}/{tabela} bloco {bloco_idx}",
                    source=f"jkdata:full:sqlite:{rel}:{tabela}:{bloco_idx:05d}",
                    content=f"Banco SQLite completo: {rel}\nTabela: {tabela}\n" + "\n".join(bloco),
                    metadata={"tipo": "sqlite_completo", "arquivo": rel, "tabela": tabela, "bloco": bloco_idx},
                ))
    except Exception as exc:
        logger.warning(f"[IA RAG] Falha ao ler SQLite {caminho}: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return docs


def _ia_rag_docs_dados_completos(client_id: str) -> list[IARagDocumento]:
    tenant_path = get_tenant_path(client_id)
    docs = []
    for raiz, _dirs, arquivos in os.walk(tenant_path):
        for nome in arquivos:
            caminho = os.path.join(raiz, nome)
            if _ia_rag_deve_ignorar_arquivo(caminho):
                continue
            ext = os.path.splitext(nome)[1].lower()
            if ext == ".csv":
                docs.extend(_ia_rag_docs_csv_generico(client_id, caminho))
            elif ext == ".json":
                docs.extend(_ia_rag_docs_json_generico(client_id, caminho))
            elif ext == ".db":
                docs.extend(_ia_rag_docs_sqlite_generico(client_id, caminho))
    return docs


def _ia_rag_docs_csv_cadastro(client_id: str) -> list[IARagDocumento]:
    docs = []

    tenant_path = get_tenant_path(client_id)
    arquivos = []
    for raiz, dirs, nomes in os.walk(tenant_path):
        dirs[:] = [d for d in dirs if d.lower() != "tmp"]
        for nome in nomes:
            nome_lower = nome.lower()
            if not nome_lower.startswith("cadastro_produtos"):
                continue
            if not ("csv" in nome_lower or ".backup" in nome_lower or ".bak" in nome_lower or nome_lower.endswith(".csv")):
                continue
            caminho = os.path.join(raiz, nome)
            if _ia_rag_deve_ignorar_arquivo(caminho):
                continue
            arquivos.append(caminho)
    arquivos = sorted(dict.fromkeys(arquivos))

    for caminho in arquivos:
        try:
            df = pd.read_csv(caminho, dtype=str).fillna("")
        except Exception as exc:
            logger.warning(f"[IA RAG] Falha ao ler cadastro para indexacao ({caminho}): {exc}")
            continue

        linhas = []
        for _, row in df.iterrows():
            sku = _ia_txt(row.get("sku"), 80)
            nome = _ia_txt(row.get("nome") or row.get("produto_bling") or row.get("cg_product name"), 220)
            if not sku and not nome:
                continue
            linhas.append(
                "SKU {sku}; nome {nome}; marca {marca}; categoria {categoria}; NCM {ncm}; CEST {cest}; "
                "preco {preco}; custo {custo}; OEM {oem}; anuncios MLB {mlb}; fornecedor {fornecedor}; observacao {obs}".format(
                    sku=sku,
                    nome=nome,
                    marca=_ia_txt(row.get("marca"), 80),
                    categoria=_ia_txt(row.get("categoria") or row.get("categoria_fiscal"), 120),
                    ncm=_ia_txt(row.get("ncm"), 40),
                    cest=_ia_txt(row.get("cest"), 40),
                    preco=_ia_txt(row.get("preco"), 40),
                    custo=_ia_txt(row.get("custo"), 40),
                    oem=_ia_txt(row.get("cg_oem") or row.get("oem/ model"), 120),
                    mlb=_ia_txt(row.get("mlb_principal") or row.get("mlb_ids"), 120),
                    fornecedor=_ia_txt(row.get("fornecedor"), 120),
                    obs=_ia_txt(row.get("observacao") or row.get("observaÃƒÂ§ÃƒÂµes"), 120),
                )
            )

        rel_cadastro = os.path.relpath(caminho, get_tenant_path(client_id)).replace("\\", "/")
        for idx in range(0, len(linhas), 25):
            bloco = linhas[idx:idx + 25]
            docs.append(_ia_rag_doc(
                title=f"Cadastro de produtos - {os.path.basename(caminho)} bloco {idx // 25 + 1}",
                source=f"jkdata:cadastro:{rel_cadastro}:{idx // 25 + 1:04d}",
                content="Cadastro de produtos do cliente.\nArquivo: {arq}\n".format(arq=rel_cadastro) + "\n".join(bloco),
                metadata={"tipo": "cadastro_produtos", "arquivo": rel_cadastro},
            ))
    return docs


def _ia_rag_docs_csv_estoque(client_id: str) -> list[IARagDocumento]:
    caminho = os.path.join(get_tenant_path(client_id), "produtos_compilado.csv")
    if not os.path.exists(caminho):
        return []
    try:
        df = pd.read_csv(caminho, dtype=str).fillna("")
    except Exception as exc:
        logger.warning(f"[IA RAG] Falha ao ler estoque para indexacao: {exc}")
        return []

    docs = []
    linhas = []
    for _, row in df.iterrows():
        sku = _ia_txt(row.get("sku"), 80)
        nome = _ia_txt(row.get("nome_bling"), 220)
        if not sku and not nome:
            continue
        saldo_loja = _ia_float(row.get("saldo_loja"))
        saldo_full = _ia_float(row.get("saldo_full"))
        linhas.append(
            "SKU {sku}; produto {nome}; loja {loja}; situacao {situacao}; NCM {ncm}; "
            "saldo loja {saldo_loja:g}; saldo full {saldo_full:g}; saldo total {total:g}; atualizado {dt}".format(
                sku=sku,
                nome=nome,
                loja=_ia_txt(row.get("loja_sync"), 80),
                situacao=_ia_txt(row.get("situacao_bling"), 40),
                ncm=_ia_txt(row.get("ncm_bling"), 40),
                saldo_loja=saldo_loja,
                saldo_full=saldo_full,
                total=saldo_loja + saldo_full,
                dt=_ia_txt(row.get("last_update"), 60),
            )
        )

    for idx in range(0, len(linhas), 40):
        bloco = linhas[idx:idx + 40]
        docs.append(_ia_rag_doc(
            title=f"Estoque atual bloco {idx // 40 + 1}",
            source=f"jkdata:estoque:{idx // 40 + 1:04d}",
            content="Estoque atual por SKU/loja.\n" + "\n".join(bloco),
            metadata={"tipo": "estoque_atual", "arquivo": os.path.basename(caminho)},
        ))
    return docs


def _ia_rag_listar_dbs_vendas(client_id: str) -> list[str]:
    tenant_path = get_tenant_path(client_id)
    if not os.path.isdir(tenant_path):
        return []
    caminhos = []
    for raiz, dirs, arquivos in os.walk(tenant_path):
        dirs[:] = [d for d in dirs if d.lower() != "tmp"]
        for nome in arquivos:
            nome_lower = nome.lower()
            if not nome_lower.startswith("vendas_historico"):
                continue
            if not (
                nome_lower.endswith(".db")
                or ".bak" in nome_lower
                or ".backup" in nome_lower
                or nome_lower.endswith(".sqlite")
            ):
                continue
            caminhos.append(os.path.join(raiz, nome))
    return sorted(dict.fromkeys(caminhos))


def _ia_rag_docs_db_vendas(client_id: str) -> list[IARagDocumento]:
    docs = []
    for caminho in _ia_rag_listar_dbs_vendas(client_id):
        base = os.path.basename(caminho)
        loja_hint = base.replace("vendas_historico", "").replace(".db", "").strip("_") or "geral"
        fonte_hint = re.sub(r"[^a-z0-9_]+", "_", loja_hint.lower()).strip("_") or "geral"
        try:
            conn = sqlite3.connect(caminho)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
        except Exception as exc:
            logger.warning(f"[IA RAG] Falha ao abrir {base}: {exc}")
            continue

        try:
            total = cur.execute("SELECT COUNT(*) AS total FROM vendas").fetchone()["total"]
            if total:
                vendas_cols = _ia_rag_colunas_tabela(conn, "vendas")
                col_loja = _ia_rag_coluna_expr(vendas_cols, ("loja_conta", "loja", "loja_nome"), "''")
                col_unidade = _ia_rag_coluna_expr(vendas_cols, ("unidade_negocio", "unidade", "unidade_nome"), "''")
                col_data = _ia_rag_coluna_expr(vendas_cols, ("data", "data_venda", "data_emissao"), "''")
                col_sku = _ia_rag_coluna_expr(vendas_cols, ("sku", "codigo", "codigo_produto"), "''")
                col_produto = _ia_rag_coluna_expr(vendas_cols, ("produto", "descricao", "nome_produto"), "''")
                col_num_nf = _ia_rag_coluna_expr(vendas_cols, ("numero_nf", "numero", "nf"), "''")
                col_comprador = _ia_rag_coluna_expr(vendas_cols, ("comprador", "cliente", "nome_cliente"), "''")
                col_qtd = _ia_rag_coluna_expr(vendas_cols, ("quantidade", "qtd", "qtde"), "0")
                col_valor = _ia_rag_coluna_expr(vendas_cols, ("valor", "valor_total", "total"), "0")
                col_dev = _ia_rag_coluna_expr(vendas_cols, ("devolucao", "is_devolucao"), "0")
                rows_mes = cur.execute(
                    """
                    SELECT
                        {col_loja} AS loja,
                        {col_unidade} AS unidade,
                        substr({col_data}, 1, 7) AS periodo,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_vendida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_vendido,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_devolvida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_devolvido,
                        COUNT(*) AS linhas
                    FROM vendas
                    WHERE COALESCE({col_data}, '') <> ''
                    GROUP BY loja, unidade, periodo
                    ORDER BY periodo DESC
                    LIMIT 120
                    """.format(
                        col_loja=col_loja,
                        col_unidade=col_unidade,
                        col_data=col_data,
                        col_qtd=col_qtd,
                        col_valor=col_valor,
                        col_dev=col_dev,
                    )
                ).fetchall()
                linhas = [
                    "Periodo {periodo}; loja {loja}; unidade {unidade}; vendas {qtd:g} itens / R$ {valor:.2f}; devolucoes {qtd_dev:g} itens / R$ {valor_dev:.2f}; linhas {linhas}".format(
                        periodo=_ia_txt(r["periodo"], 20),
                        loja=_ia_txt(r["loja"] or loja_hint, 80),
                        unidade=_ia_txt(r["unidade"], 80),
                        qtd=float(r["qtd_vendida"] or 0),
                        valor=float(r["valor_vendido"] or 0),
                        qtd_dev=float(r["qtd_devolvida"] or 0),
                        valor_dev=float(r["valor_devolvido"] or 0),
                        linhas=int(r["linhas"] or 0),
                    )
                    for r in rows_mes
                ]
                if linhas:
                    docs.append(_ia_rag_doc(
                        title=f"Resumo mensal de vendas - {loja_hint}",
                        source=f"jkdata:vendas:{loja_hint}:mensal",
                        content="Resumo mensal de vendas e devolucoes.\n" + "\n".join(linhas),
                        metadata={"tipo": "vendas_mensal", "arquivo": base},
                    ))

                rows_sku = cur.execute(
                    """
                    SELECT
                        {col_sku} AS sku,
                        {col_produto} AS produto,
                        {col_loja} AS loja,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_vendida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_vendido,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_devolvida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_devolvido,
                        MAX({col_data}) AS ultima_data
                    FROM vendas
                    WHERE COALESCE({col_sku}, '') <> ''
                    GROUP BY sku, produto, loja
                    ORDER BY valor_vendido DESC
                    LIMIT 250
                    """.format(
                        col_sku=col_sku,
                        col_produto=col_produto,
                        col_loja=col_loja,
                        col_qtd=col_qtd,
                        col_valor=col_valor,
                        col_dev=col_dev,
                        col_data=col_data,
                    )
                ).fetchall()
                linhas = [
                    "SKU {sku}; produto {produto}; loja {loja}; vendido {qtd:g} itens / R$ {valor:.2f}; devolvido {qtd_dev:g} itens / R$ {valor_dev:.2f}; ultima venda {data}".format(
                        sku=_ia_txt(r["sku"], 80),
                        produto=_ia_txt(r["produto"], 180),
                        loja=_ia_txt(r["loja"] or loja_hint, 80),
                        qtd=float(r["qtd_vendida"] or 0),
                        valor=float(r["valor_vendido"] or 0),
                        qtd_dev=float(r["qtd_devolvida"] or 0),
                        valor_dev=float(r["valor_devolvido"] or 0),
                        data=_ia_txt(r["ultima_data"], 30),
                    )
                    for r in rows_sku
                ]
                for idx in range(0, len(linhas), 35):
                    bloco = linhas[idx:idx + 35]
                    docs.append(_ia_rag_doc(
                        title=f"Top SKUs de vendas - {loja_hint} bloco {idx // 35 + 1}",
                        source=f"jkdata:vendas:{loja_hint}:skus:{idx // 35 + 1:04d}",
                        content="SKUs com maior valor vendido e devolucoes relacionadas.\n" + "\n".join(bloco),
                        metadata={"tipo": "vendas_top_skus", "arquivo": base},
                    ))

                # IndexaÃƒÂ§ÃƒÂ£o completa das linhas de vendas, incluindo bancos de backup.
                rows_full = cur.execute(
                    """
                    SELECT
                        {col_data} AS data,
                        {col_loja} AS loja,
                        {col_unidade} AS unidade,
                        {col_sku} AS sku,
                        {col_produto} AS produto,
                        {col_num_nf} AS numero_nf,
                        {col_comprador} AS comprador,
                        COALESCE({col_qtd}, 0) AS quantidade,
                        COALESCE({col_valor}, 0) AS valor,
                        COALESCE({col_dev}, 0) AS devolucao
                    FROM vendas
                    ORDER BY COALESCE({col_data}, '') DESC, rowid DESC
                    """.format(
                        col_data=col_data,
                        col_loja=col_loja,
                        col_unidade=col_unidade,
                        col_sku=col_sku,
                        col_produto=col_produto,
                        col_num_nf=col_num_nf,
                        col_comprador=col_comprador,
                        col_qtd=col_qtd,
                        col_valor=col_valor,
                        col_dev=col_dev,
                    )
                ).fetchall()
                linhas_full = [
                    "data {data}; loja {loja}; unidade {unidade}; SKU {sku}; produto {produto}; NF {nf}; comprador {comprador}; qtd {qtd:g}; valor R$ {valor:.2f}; devolucao {devolucao}".format(
                        data=_ia_txt(r["data"], 30),
                        loja=_ia_txt(r["loja"] or loja_hint, 80),
                        unidade=_ia_txt(r["unidade"], 80),
                        sku=_ia_txt(r["sku"], 90),
                        produto=_ia_txt(r["produto"], 200),
                        nf=_ia_txt(r["numero_nf"], 40),
                        comprador=_ia_txt(r["comprador"], 120),
                        qtd=float(r["quantidade"] or 0),
                        valor=float(r["valor"] or 0),
                        devolucao="sim" if int(r["devolucao"] or 0) == 1 else "nao",
                    )
                    for r in rows_full
                ]
                for idx, bloco in _ia_rag_lote_linhas(linhas_full, 180):
                    docs.append(_ia_rag_doc(
                        title=f"Vendas detalhadas - {loja_hint} bloco {idx}",
                        source=f"jkdata:vendas_full:{fonte_hint}:{idx:05d}",
                        content=(
                            "Base detalhada de vendas (inclui bancos principais e backups quando presentes).\n"
                            f"Arquivo origem: {base}\n" + "\n".join(bloco)
                        ),
                        metadata={"tipo": "vendas_detalhadas", "arquivo": base, "bloco": idx},
                    ))

                rows_periodo_sku = cur.execute(
                    """
                    SELECT
                        substr({col_data}, 1, 7) AS periodo,
                        {col_sku} AS sku,
                        {col_produto} AS produto,
                        {col_loja} AS loja,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_vendida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 0 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_vendido,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_qtd}, 0) ELSE 0 END) AS qtd_devolvida,
                        SUM(CASE WHEN COALESCE({col_dev}, 0) = 1 THEN COALESCE({col_valor}, 0) ELSE 0 END) AS valor_devolvido,
                        MAX({col_data}) AS ultima_data
                    FROM vendas
                    WHERE COALESCE({col_data}, '') <> ''
                      AND COALESCE({col_sku}, '') <> ''
                    GROUP BY periodo, sku, produto, loja
                    ORDER BY periodo DESC, valor_vendido DESC, qtd_devolvida DESC
                    """.format(
                        col_data=col_data,
                        col_sku=col_sku,
                        col_produto=col_produto,
                        col_loja=col_loja,
                        col_qtd=col_qtd,
                        col_valor=col_valor,
                        col_dev=col_dev,
                    )
                ).fetchall()
                linhas_periodo = [
                    "periodo {periodo}; SKU {sku}; produto {produto}; loja {loja}; vendido {qtd_vendida:g} itens / R$ {valor_vendido:.2f}; devolvido {qtd_devolvida:g} itens / R$ {valor_devolvido:.2f}; ultima movimentacao {ultima_data}".format(
                        periodo=_ia_txt(r["periodo"], 20),
                        sku=_ia_txt(r["sku"], 90),
                        produto=_ia_txt(r["produto"], 180),
                        loja=_ia_txt(r["loja"] or loja_hint, 80),
                        qtd_vendida=float(r["qtd_vendida"] or 0),
                        valor_vendido=float(r["valor_vendido"] or 0),
                        qtd_devolvida=float(r["qtd_devolvida"] or 0),
                        valor_devolvido=float(r["valor_devolvido"] or 0),
                        ultima_data=_ia_txt(r["ultima_data"], 30),
                    )
                    for r in rows_periodo_sku
                ]
                for idx, bloco in _ia_rag_lote_linhas(linhas_periodo, 160):
                    docs.append(_ia_rag_doc(
                        title=f"Vendas por periodo e SKU - {loja_hint} bloco {idx}",
                        source=f"jkdata:vendas_periodo_sku:{fonte_hint}:{idx:05d}",
                        content=(
                            "Resumo por periodo e SKU com vendas e devolucoes separadas.\n"
                            f"Arquivo origem: {base}\n" + "\n".join(bloco)
                        ),
                        metadata={"tipo": "vendas_periodo_sku", "arquivo": base, "bloco": idx},
                    ))

            total_nf = cur.execute("SELECT COUNT(*) AS total FROM notas_entrada_itens").fetchone()["total"]
            if total_nf:
                itens_cols = _ia_rag_colunas_tabela(conn, "notas_entrada_itens")
                col_sku_it = _ia_rag_coluna_expr(itens_cols, ("sku", "codigo", "codigo_produto"), "''")
                col_desc_it = _ia_rag_coluna_expr(itens_cols, ("descricao", "produto", "nome_produto"), "''")
                col_loja_it = _ia_rag_coluna_expr(itens_cols, ("loja_conta", "loja", "loja_nome"), "''")
                col_unidade_it = _ia_rag_coluna_expr(itens_cols, ("unidade_negocio_virtual", "unidade_negocio", "unidade", "unidade_nome"), "''")
                col_qtd_it = _ia_rag_coluna_expr(itens_cols, ("quantidade", "qtd", "qtde"), "0")
                col_valor_it = _ia_rag_coluna_expr(itens_cols, ("valor_total", "valor", "total"), "0")
                col_data_it = _ia_rag_coluna_expr(itens_cols, ("data_emissao", "data", "emissao"), "''")
                col_dev_it = _ia_rag_coluna_expr(itens_cols, ("devolucao", "is_devolucao"), "0")
                rows_dev = cur.execute(
                    """
                    SELECT
                        {col_sku_it} AS sku,
                        {col_desc_it} AS descricao,
                        {col_loja_it} AS loja,
                        {col_unidade_it} AS unidade,
                        SUM(COALESCE({col_qtd_it}, 0)) AS qtd,
                        SUM(COALESCE({col_valor_it}, 0)) AS valor,
                        MAX({col_data_it}) AS ultima_data
                    FROM notas_entrada_itens
                    WHERE COALESCE({col_dev_it}, 0) = 1
                    GROUP BY sku, descricao, loja, unidade
                    ORDER BY valor DESC
                    LIMIT 180
                    """.format(
                        col_sku_it=col_sku_it,
                        col_desc_it=col_desc_it,
                        col_loja_it=col_loja_it,
                        col_unidade_it=col_unidade_it,
                        col_qtd_it=col_qtd_it,
                        col_valor_it=col_valor_it,
                        col_data_it=col_data_it,
                        col_dev_it=col_dev_it,
                    )
                ).fetchall()
                linhas = [
                    "SKU {sku}; descricao {desc}; loja {loja}; unidade {unidade}; devolvido {qtd:g} itens / R$ {valor:.2f}; ultima devolucao {data}".format(
                        sku=_ia_txt(r["sku"], 80),
                        desc=_ia_txt(r["descricao"], 180),
                        loja=_ia_txt(r["loja"] or loja_hint, 80),
                        unidade=_ia_txt(r["unidade"], 80),
                        qtd=float(r["qtd"] or 0),
                        valor=float(r["valor"] or 0),
                        data=_ia_txt(r["ultima_data"], 30),
                    )
                    for r in rows_dev
                ]
                for idx in range(0, len(linhas), 35):
                    bloco = linhas[idx:idx + 35]
                    docs.append(_ia_rag_doc(
                        title=f"Devolucoes por SKU - {loja_hint} bloco {idx // 35 + 1}",
                        source=f"jkdata:devolucoes:{loja_hint}:{idx // 35 + 1:04d}",
                        content="Itens de notas de entrada classificados como devolucao.\n" + "\n".join(bloco),
                        metadata={"tipo": "devolucoes_sku", "arquivo": base},
                    ))
        except Exception as exc:
            logger.warning(f"[IA RAG] Falha ao resumir {base}: {exc}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return docs


def _ia_rag_docs_app(client_id: str) -> list[IARagDocumento]:
    docs = []
    docs.extend(_ia_rag_docs_csv_cadastro(client_id))
    docs.extend(_ia_rag_docs_csv_estoque(client_id))
    docs.extend(_ia_rag_docs_db_vendas(client_id))
    if _ia_rag_legacy_generic_scan_enabled():
        docs.extend(_ia_rag_docs_dados_completos(client_id))
    return docs


def _ia_rag_reindexar_app(client_id: str, force: bool = False) -> dict:
    if not _ia_rag_legacy_write_enabled():
        raise HTTPException(status_code=403, detail="A gravacao no RAG legado esta desativada.")
    if force and not _ia_rag_legacy_force_replace_enabled():
        raise HTTPException(
            status_code=403,
            detail="A substituicao destrutiva do RAG legado esta desativada.",
        )
    if not _ia_rag_ativo():
        raise HTTPException(
            status_code=503,
            detail="RAG nao configurado. Defina IA_RAG_ENABLED=true e IA_VECTOR_DATABASE_URL no .env."
        )
    docs = _ia_rag_docs_app(client_id)
    dlp_codes = sorted({code for doc in docs for code in _ia_rag_legacy_dlp_codes(doc)})
    if dlp_codes:
        raise ValueError(
            "Documento bloqueado pela politica DLP do RAG legado (codigos: "
            + ", ".join(dlp_codes)
            + ")."
        )
    removidos = _ia_rag_limpar_fontes(client_id, "jkdata:") if force else 0
    if not force:
        existentes = _ia_rag_fontes_existentes(client_id, "jkdata:")
        docs = [doc for doc in docs if str(doc.source or "") not in existentes]
    inseridos = _ia_rag_indexar_documentos(client_id, docs)
    return {
        "success": True,
        "client_id": client_id,
        "documentos_preparados": len(docs),
        "removidos": removidos,
        "inseridos": inseridos,
    }


def _ia_rag_iniciar_reindex_async(client_id: str, force: bool = False) -> dict:
    with IA_RAG_REINDEX_LOCK:
        if bool(IA_RAG_REINDEX_ACTIVE.get(client_id)):
            meta = dict(IA_RAG_REINDEX_META.get(client_id) or {})
            return {
                "accepted": False,
                "already_running": True,
                "client_id": client_id,
                "status": meta,
            }

        IA_RAG_REINDEX_ACTIVE[client_id] = True
        IA_RAG_REINDEX_META[client_id] = {
            "status": "running",
            "force": bool(force),
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
            "last_error": "",
            "result": None,
        }

    def _worker():
        try:
            resultado = _ia_rag_reindexar_app(client_id, force=force)
            with IA_RAG_REINDEX_LOCK:
                IA_RAG_REINDEX_META[client_id] = {
                    **dict(IA_RAG_REINDEX_META.get(client_id) or {}),
                    "status": "ok",
                    "finished_at": datetime.utcnow().isoformat(),
                    "last_error": "",
                    "result": resultado,
                }
        except Exception as exc:
            logger.error(
                "[IA RAG] Falha no reindex async (%s): %s",
                client_id,
                type(exc).__name__,
            )
            with IA_RAG_REINDEX_LOCK:
                IA_RAG_REINDEX_META[client_id] = {
                    **dict(IA_RAG_REINDEX_META.get(client_id) or {}),
                    "status": "error",
                    "finished_at": datetime.utcnow().isoformat(),
                    "last_error": type(exc).__name__,
                    "result": None,
                }
        finally:
            with IA_RAG_REINDEX_LOCK:
                IA_RAG_REINDEX_ACTIVE[client_id] = False

    threading.Thread(target=_worker, name=f"ia-rag-reindex-{client_id}", daemon=True).start()
    return {
        "accepted": True,
        "already_running": False,
        "client_id": client_id,
        "status": dict(IA_RAG_REINDEX_META.get(client_id) or {}),
    }

configure_ia_rag_runtime()

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
