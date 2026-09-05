"""Shared Mercado Livre HTTP session helpers."""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter


logger = logging.getLogger("jk_sistema")


ML_HTTP_SESSION_POOL: dict[tuple[str, str, str, str], dict[str, Any]] = {}
ML_HTTP_SESSION_POOL_LOCK = threading.RLock()
ML_HTTP_SESSION_POOL_MAX = 80


def _ml_http_ca_bundle(verify_ssl: bool | str) -> str | None:
    if isinstance(verify_ssl, str) and verify_ssl.strip():
        return verify_ssl.strip()
    for env_key in ("ML_CA_BUNDLE", "JK_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        configured = str(os.environ.get(env_key) or "").strip()
        if configured:
            return configured
    return None


def _ml_http_windows_compatible_ssl_context(verify_ssl: bool | str) -> ssl.SSLContext | None:
    """Keep certificate verification while accepting legacy CAs trusted by Windows.

    Python 3.14 enables OpenSSL's ``VERIFY_X509_STRICT`` in urllib3. Some
    enterprise CAs present in the Windows trust store predate the requirement
    that ``Basic Constraints`` be marked critical. Windows trusts those roots,
    but OpenSSL rejects the chain before hostname and trust validation finish.

    Clearing only that compatibility flag restores the pre-3.14 behavior. The
    context still requires a trusted certificate and validates the hostname.
    """

    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if os.name != "nt" or not strict_flag:
        return None
    ca_bundle = _ml_http_ca_bundle(verify_ssl)
    if not ca_bundle:
        return None

    context = ssl.create_default_context(cafile=ca_bundle)
    context.verify_flags &= ~strict_flag
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("Mercado Livre TLS context must verify certificates and hostnames")
    return context


class _MercadoLivreHttpsAdapter(HTTPAdapter):
    def __init__(self, verify_ssl: bool | str, *args, **kwargs):
        self._ml_ssl_context = _ml_http_windows_compatible_ssl_context(verify_ssl)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        if self._ml_ssl_context is not None:
            pool_kwargs.setdefault("ssl_context", self._ml_ssl_context)
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        if self._ml_ssl_context is not None:
            proxy_kwargs.setdefault("ssl_context", self._ml_ssl_context)
        return super().proxy_manager_for(proxy, **proxy_kwargs)


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
    https_adapter = _MercadoLivreHttpsAdapter(
        verify_ssl,
        pool_connections=16,
        pool_maxsize=16,
        pool_block=False,
    )
    session.mount("https://", https_adapter)
    session.mount("http://", HTTPAdapter(pool_connections=16, pool_maxsize=16, pool_block=False))
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
    from backend.services.central_accounts_client import is_marker, provider_request
    if is_marker(token):
        return provider_request(token, method, url, headers=headers, params=params, json=json, data=data)
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
