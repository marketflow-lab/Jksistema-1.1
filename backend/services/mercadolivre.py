"""Mercado Livre service helpers.

This module owns shared Mercado Livre HTTP sessions and lightweight in-memory
cache state used by several legacy endpoints.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter


ML_HTTP_SESSION_POOL: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
ML_HTTP_SESSION_POOL_LOCK = threading.RLock()
ML_HTTP_SESSION_POOL_MAX = 80

_ml_cache: dict = {}
_ML_CACHE_TTL = 120
_ML_CACHE_TTL_CAMPANHA = 1800
_ML_CACHE_TTL_CAMPANHA_STALE = 21600
_ML_CACHE_TTL_CONTAGENS = 900


def _ml_http_token_fingerprint(token: str) -> str:
    texto = str(token or "").strip()
    if not texto:
        return "sem-token"
    return hashlib.sha256(texto.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _ml_http_session_key(client_id: str, loja: str, token: str, verify_ssl: bool) -> tuple[str, str, str, bool]:
    return (
        str(client_id or "").strip(),
        str(loja or "").strip().lower(),
        _ml_http_token_fingerprint(token),
        bool(verify_ssl),
    )


def _ml_http_criar_session(verify_ssl: bool) -> requests.Session:
    session = requests.Session()
    session.verify = bool(verify_ssl)
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, pool_block=False)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _ml_http_evict_locked() -> None:
    excesso = len(ML_HTTP_SESSION_POOL) - ML_HTTP_SESSION_POOL_MAX
    if excesso <= 0:
        return
    antigos = sorted(
        ML_HTTP_SESSION_POOL.items(),
        key=lambda item: float((item[1] or {}).get("last_used") or 0),
    )[:excesso]
    for key, entry in antigos:
        session = (entry or {}).get("session")
        try:
            if session:
                session.close()
        except Exception:
            pass
        ML_HTTP_SESSION_POOL.pop(key, None)


def _ml_http_obter_session(client_id: str, loja: str, token: str, verify_ssl: bool) -> requests.Session:
    key = _ml_http_session_key(client_id, loja, token, verify_ssl)
    agora = time.time()
    with ML_HTTP_SESSION_POOL_LOCK:
        entry = ML_HTTP_SESSION_POOL.get(key)
        if not entry:
            entry = {
                "session": _ml_http_criar_session(verify_ssl),
                "created_at": agora,
                "last_used": agora,
            }
            ML_HTTP_SESSION_POOL[key] = entry
            _ml_http_evict_locked()
        else:
            entry["last_used"] = agora
        return entry["session"]


def _ml_http_invalidar_session(client_id: str, loja: str, token: str, verify_ssl: bool) -> None:
    key = _ml_http_session_key(client_id, loja, token, verify_ssl)
    with ML_HTTP_SESSION_POOL_LOCK:
        entry = ML_HTTP_SESSION_POOL.pop(key, None)
    session = (entry or {}).get("session")
    try:
        if session:
            session.close()
    except Exception:
        pass


def _ml_http_request(
    client_id: str,
    loja: str,
    token: str,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params=None,
    json=None,
    data=None,
    timeout: int = 15,
    verify_ssl: bool = True,
) -> requests.Response:
    session = _ml_http_obter_session(client_id, loja, token, verify_ssl)
    return session.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json,
        data=data,
        timeout=timeout,
    )


def _ml_cache_get(chave: str, ttl: int = None):
    entry = _ml_cache.get(chave)
    ttl_usado = ttl if ttl is not None else _ML_CACHE_TTL
    if entry:
        ts, dados = entry
        if (time.time() - ts) < ttl_usado:
            return dados
    return None


def _ml_cache_get_stale(chave: str, max_age: int):
    if not max_age or max_age <= 0:
        return None
    entry = _ml_cache.get(chave)
    if not entry:
        return None
    ts, dados = entry
    if max_age is not None and max_age > 0 and (time.time() - float(ts)) > float(max_age):
        return None
    return dados


def _ml_cache_set(chave: str, dados):
    _ml_cache[chave] = (time.time(), dados)
    if len(_ml_cache) > 200:
        agora = time.time()
        expiradas = [k for k, (ts, _) in _ml_cache.items() if (agora - ts) >= _ML_CACHE_TTL]
        for k in expiradas:
            _ml_cache.pop(k, None)


def _cache_invalidar_loja(client_id: str, loja: str):
    """Remove todas as entradas de cache de uma loja especifica."""
    prefixos = (
        f"anuncios:{client_id}:{loja}:",
        f"visitas:{client_id}:{loja}:",
        f"promocoes:{client_id}:{loja}",
        f"campanha_itens:{client_id}:{loja}:",
        f"camp_contagens:{client_id}:{loja}",
    )
    chaves = [k for k in list(_ml_cache.keys()) if any(k.startswith(p) for p in prefixos)]
    for k in chaves:
        _ml_cache.pop(k, None)
    return len(chaves)
