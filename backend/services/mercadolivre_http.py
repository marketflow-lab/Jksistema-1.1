"""Shared Mercado Livre HTTP session helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter


logger = logging.getLogger("jk_sistema")


ML_HTTP_SESSION_POOL: dict[tuple[str, str, str, str], dict[str, Any]] = {}
ML_HTTP_SESSION_POOL_LOCK = threading.RLock()
ML_HTTP_SESSION_POOL_MAX = 80


def _ml_http_verify_setting() -> bool | str:
    """Return strict TLS verification or an explicitly configured CA bundle."""

    ca_bundle = str(os.environ.get("ML_CA_BUNDLE") or "").strip()
    return ca_bundle or True


def _ml_http_token_fingerprint(token: str) -> str:
    texto = str(token or "").strip()
    if not texto:
        return "sem-token"
    return hashlib.sha256(texto.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _ml_http_session_key(client_id: str, loja: str, token: str, verify_ssl: bool | str) -> tuple[str, str, str, str]:
    return (
        str(client_id or "").strip(),
        str(loja or "").strip().lower(),
        _ml_http_token_fingerprint(token),
        str(verify_ssl),
    )


def _ml_http_criar_session(verify_ssl: bool | str) -> requests.Session:
    session = requests.Session()
    session.verify = verify_ssl
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


def _ml_http_obter_session(client_id: str, loja: str, token: str, verify_ssl: bool | str) -> requests.Session:
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


def _ml_http_invalidar_session(client_id: str, loja: str, token: str, verify_ssl: bool | str) -> None:
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
    if verify_ssl is False:
        logger.warning("[ML HTTP] Tentativa de desabilitar TLS foi bloqueada para loja=%s.", loja)
    verification = _ml_http_verify_setting()
    session = _ml_http_obter_session(client_id, loja, token, verification)
    return session.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json,
        data=data,
        timeout=timeout,
        verify=verification,
    )
