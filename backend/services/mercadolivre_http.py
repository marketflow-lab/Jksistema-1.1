"""Shared Mercado Livre HTTP session helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import SSLError


logger = logging.getLogger("jk_sistema")


ML_HTTP_SESSION_POOL: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
ML_HTTP_SESSION_POOL_LOCK = threading.RLock()
ML_HTTP_SESSION_POOL_MAX = 80


def _ml_http_ssl_fallback_habilitado() -> bool:
    valor = str(os.environ.get("ML_SSL_FALLBACK_ON_CERT_ERROR", "1")).strip().lower()
    return valor not in {"0", "false", "no", "off", "nao", "não"}


def _ml_http_host_permite_ssl_fallback(url: str) -> bool:
    host = urlparse(str(url or "")).hostname or ""
    host = host.lower()
    return host == "api.mercadolibre.com" or host.endswith(".mercadolibre.com")


def _ml_http_ssl_error_certificado(exc: BaseException) -> bool:
    texto = str(exc).lower()
    return (
        "certificate verify failed" in texto
        or "certificate_verify_failed" in texto
        or "sslcertverificationerror" in texto
    )


def _ml_http_deve_tentar_sem_ssl(url: str, verify_ssl: bool, exc: BaseException) -> bool:
    return (
        bool(verify_ssl)
        and _ml_http_ssl_fallback_habilitado()
        and _ml_http_host_permite_ssl_fallback(url)
        and _ml_http_ssl_error_certificado(exc)
    )


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
    try:
        return session.request(
            method,
            url,
            headers=headers,
            params=params,
        json=json,
        data=data,
        timeout=timeout,
        verify=bool(verify_ssl),
        )
    except SSLError as exc:
        if not _ml_http_deve_tentar_sem_ssl(url, verify_ssl, exc):
            raise
        logger.warning(
            "[ML HTTP] Validacao SSL falhou para %s loja=%s; repetindo chamada sem verificacao SSL.",
            urlparse(str(url or "")).hostname or url,
            loja,
        )
        fallback_session = _ml_http_obter_session(client_id, loja, token, False)
        return fallback_session.request(
            method,
            url,
            headers=headers,
            params=params,
        json=json,
        data=data,
        timeout=timeout,
        verify=False,
    )
