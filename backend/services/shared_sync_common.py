"""Shared-sync service helpers.

This module owns deterministic shared-sync utilities and shared cache state
used by the legacy sync endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException


SHARED_SYNC_DEFAULT_MAX_FILE_BYTES = 75 * 1024 * 1024
SHARED_SYNC_DEFAULT_MAX_BUNDLE_BYTES = 75 * 1024 * 1024
SHARED_SYNC_DEFAULT_LOCAL_BACKUP_RETENTION = 24

SHARED_SYNC_DOCS_CACHE_LOCK = threading.RLock()
SHARED_SYNC_DOCS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _shared_sync_safe_doc_id(*partes: str) -> str:
    raw = "|".join(str(parte or "").strip().lower() for parte in partes)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _shared_sync_doc_id(client_id: str, scope: str) -> str:
    return _shared_sync_safe_doc_id("shared-sync", client_id, scope)


def _shared_sync_pair_doc_id(source_client_id: str, source_username: str, target_client_id: str, target_username: str, scope: str) -> str:
    return _shared_sync_safe_doc_id("shared-sync-user-pair", source_client_id, source_username, target_client_id, target_username, scope)


def _shared_sync_machine_doc_id(client_id: str, username: str, scope: str) -> str:
    return _shared_sync_safe_doc_id("shared-sync-machine", client_id, username, scope)


def _shared_sync_config_doc_id(client_id: str) -> str:
    return _shared_sync_safe_doc_id("shared-sync-config", client_id)


def _shared_sync_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _shared_sync_env_int(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(float(os.getenv(nome, str(padrao)) or padrao))
        return max(minimo, min(valor, maximo))
    except Exception:
        return padrao


def _shared_sync_docs_cache_ttl_seconds() -> int:
    return _shared_sync_env_int("JK_SHARED_SYNC_DOCS_CACHE_TTL_S", 300, 30, 3600)


def _shared_sync_docs_cache_key(collection_name: str, local_path: str) -> tuple[str, str]:
    return (str(collection_name or "").strip(), os.path.abspath(str(local_path or "")))


def _shared_sync_json_clone(valor: Any) -> Any:
    try:
        return json.loads(json.dumps(valor, ensure_ascii=False, default=str))
    except Exception:
        return valor


def _shared_sync_clone_docs(items: list[dict]) -> list[dict]:
    return [_shared_sync_json_clone(item) for item in (items or []) if isinstance(item, dict)]


def _shared_sync_docs_cache_get(collection_name: str, local_path: str, allow_expired: bool = False) -> Optional[list[dict]]:
    key = _shared_sync_docs_cache_key(collection_name, local_path)
    now = time.time()
    ttl = _shared_sync_docs_cache_ttl_seconds()
    with SHARED_SYNC_DOCS_CACHE_LOCK:
        cached = SHARED_SYNC_DOCS_CACHE.get(key)
        if not cached:
            return None
        if not allow_expired and now - float(cached.get("ts") or 0) > ttl:
            return None
        return _shared_sync_clone_docs(cached.get("docs") or [])


def _shared_sync_docs_cache_set(collection_name: str, local_path: str, docs: list[dict]) -> None:
    key = _shared_sync_docs_cache_key(collection_name, local_path)
    with SHARED_SYNC_DOCS_CACHE_LOCK:
        SHARED_SYNC_DOCS_CACHE[key] = {
            "ts": time.time(),
            "docs": _shared_sync_clone_docs(docs),
        }


def _shared_sync_docs_cache_invalidate(collection_name: Optional[str] = None, local_path: Optional[str] = None) -> None:
    with SHARED_SYNC_DOCS_CACHE_LOCK:
        if collection_name is None and local_path is None:
            SHARED_SYNC_DOCS_CACHE.clear()
            return
        target_key = _shared_sync_docs_cache_key(collection_name or "", local_path or "")
        for key in list(SHARED_SYNC_DOCS_CACHE.keys()):
            if collection_name is not None and key[0] != target_key[0]:
                continue
            if local_path is not None and key[1] != target_key[1]:
                continue
            SHARED_SYNC_DOCS_CACHE.pop(key, None)


def _shared_sync_max_file_bytes() -> int:
    return _shared_sync_env_int("JK_SHARED_SYNC_MAX_FILE_BYTES", SHARED_SYNC_DEFAULT_MAX_FILE_BYTES, 1024 * 1024, 250 * 1024 * 1024)


def _shared_sync_max_bundle_bytes() -> int:
    return _shared_sync_env_int("JK_SHARED_SYNC_MAX_BUNDLE_BYTES", SHARED_SYNC_DEFAULT_MAX_BUNDLE_BYTES, 1024 * 1024, 250 * 1024 * 1024)


def _shared_sync_local_backup_retention() -> int:
    return _shared_sync_env_int("JK_SHARED_SYNC_LOCAL_BACKUP_RETENTION", SHARED_SYNC_DEFAULT_LOCAL_BACKUP_RETENTION, 0, 500)


def _shared_sync_safe_filename(valor: str) -> str:
    texto = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(valor or "default").strip())
    return (texto or "default")[:90]


def _shared_sync_relativo_seguro(rel_path: str) -> str:
    rel = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    norm = os.path.normpath(rel).replace("\\", "/")
    if not norm or norm == "." or norm == ".." or norm.startswith("../") or os.path.isabs(norm):
        raise HTTPException(status_code=400, detail="Pacote contem caminho invalido.")
    return norm


def _shared_sync_path_permanently_excluded(rel_path: str) -> bool:
    """Runtime knowledge and Obsidian state are never Shared Sync payloads."""
    try:
        rel = _shared_sync_relativo_seguro(rel_path).lower()
    except HTTPException:
        return True
    parts = [part for part in rel.split("/") if part]
    return any(part in {"contextvault", "context_hub", ".obsidian"} for part in parts)


def _shared_sync_resolve_tenant_path(tenant_root: str, rel_path: str) -> str:
    """Resolve a tenant path without crossing symlinks, junctions or reparse targets."""
    rel = _shared_sync_relativo_seguro(rel_path)
    if _shared_sync_path_permanently_excluded(rel):
        raise HTTPException(status_code=400, detail="Caminho permanentemente excluido do Shared Sync.")

    root_abs = os.path.abspath(str(tenant_root or ""))
    root_real = os.path.realpath(root_abs)
    if os.path.normcase(os.path.normpath(root_abs)) != os.path.normcase(os.path.normpath(root_real)):
        raise HTTPException(
            status_code=400,
            detail="Links simbolicos e junctions nao sao aceitos como raiz do tenant no Shared Sync.",
        )
    candidate_abs = os.path.abspath(os.path.join(root_abs, rel.replace("/", os.sep)))
    expected_real = os.path.abspath(os.path.join(root_real, rel.replace("/", os.sep)))
    candidate_real = os.path.realpath(candidate_abs)
    try:
        lexical_inside = os.path.commonpath([root_abs, candidate_abs]) == root_abs
        resolved_inside = os.path.commonpath([root_real, candidate_real]) == root_real
    except (ValueError, OSError):
        lexical_inside = False
        resolved_inside = False
    if not lexical_inside or not resolved_inside:
        raise HTTPException(status_code=400, detail="Caminho fora do tenant no Shared Sync.")
    if os.path.normcase(candidate_real) != os.path.normcase(expected_real):
        raise HTTPException(status_code=400, detail="Links simbolicos e junctions nao sao aceitos no Shared Sync.")
    try:
        resolved_rel = os.path.relpath(candidate_real, root_real).replace("\\", "/")
    except ValueError:
        raise HTTPException(status_code=400, detail="Caminho fora do tenant no Shared Sync.")
    if _shared_sync_path_permanently_excluded(resolved_rel):
        raise HTTPException(status_code=400, detail="Caminho permanentemente excluido do Shared Sync.")
    return candidate_abs


def _shared_sync_sha256_file(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _shared_sync_bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data or b"").hexdigest()

SHARED_SYNC_AUTO_RATE_LIMIT_LOCK = threading.RLock()
SHARED_SYNC_SQLITE_FILE_LOCKS_LOCK = threading.RLock()
SHARED_SYNC_SQLITE_FILE_LOCKS: dict[str, Any] = {}
SHARED_SYNC_AUTO_RATE_LIMIT: dict[str, float] = {}
SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS = 15000
SHARED_SYNC_SQLITE_LOCK_RETRIES = 4

SHARED_SYNC_SCOPES = {
    "cadastro": {
        "label": "Cadastro de produtos",
        "description": "Produtos cadastrados, fotos e cache de cadastro.",
        "patterns": [
            "cadastro_produtos.csv",
            "cadastro_produtos_meta.json",
            "cadastro_produtos_fotos/**",
            "cadastro_fotos/**",
            "SKU/**",
            "produtos_compilado.csv",
        ],
        "user_scoped": False,
        "sensitive": False,
    },
    "lojas_integracoes": {
        "label": "Lojas e integracoes",
        "description": "Configuracoes de lojas e credenciais de integracao.",
        # temp_integracao.json, state e codigos/callbacks OAuth nunca entram no pacote.
        "patterns": ["lojas_config.json", "integracoes.json", "lojas_sync_tombstones.json"],
        "user_scoped": False,
        "sensitive": True,
    },
    "vendas": {
        "label": "Vendas e estoque",
        "description": "Historico local de vendas, estoque e unidades de negocio.",
        "patterns": [
            "vendas_historico.db",
            "vendas_sync_state.json",
            "estoque_historico.db",
            "produtos_compilado.csv",
            "unidades_negocios.json",
            "mapeamento_unidades.json",
        ],
        "user_scoped": False,
        "sensitive": False,
    },
    "favoritos_historico": {
        "label": "Historico de favoritos",
        "description": "Historico e simulacoes do modulo Favoritos por usuario.",
        "patterns": [
            "favoritos_historico_*.db",
            "favoritos_historico_*.sqlite",
            "favoritos_historico_*.sqlite3",
            "favoritos_historico_*.json",
            "favoritos_simulador_*.json",
        ],
        "user_scoped": True,
        "sensitive": False,
    },
    "sku_campos_pesquisa": {
        "label": "Campos de pesquisa por SKU",
        "description": "Campos de pesquisa e observacoes por SKU salvos no Favoritos.",
        "patterns": ["favoritos_pesquisas_*.json", "sku_campos_pesquisa_*.json"],
        "user_scoped": True,
        "sensitive": False,
    },
    "favoritos_planilhas": {
        "label": "Planilhas do Favoritos",
        "description": "Links de Google Sheets por loja salvos na aba Planilhas do Favoritos.",
        "patterns": ["favoritos_planilhas_lojas.json"],
        "user_scoped": False,
        "sensitive": False,
    },
    "anuncios_ml": {
        "label": "Anuncios ignorados e filtros ML",
        "description": "Listas de anuncios, vendedores e SKUs ocultos no Favoritos.",
        "patterns": [
            "favoritos_anuncios_ignorados_*.json",
            "favoritos_vendedores_ignorados_*.json",
            "favoritos_skus_ocultos_*.json",
        ],
        "user_scoped": True,
        "sensitive": False,
    },
}

SHARED_SYNC_ALLOWED_EXTENSIONS = {".json", ".csv", ".db", ".sqlite", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp"}
SHARED_SYNC_CHUNK_CHARS = 620_000

__all__ = [
    name
    for name in globals()
    if name.startswith("_shared_sync") or name.startswith("SHARED_SYNC")
]
