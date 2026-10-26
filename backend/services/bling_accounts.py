"""Verified Bling company identities, separate from store and OAuth identities.

Credential aliases live only in memory. The canonical configuration stores the
public company ID together with the OAuth connection that was verified.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import re
from collections import OrderedDict
from urllib.parse import urlsplit

from fastapi import HTTPException


_LOCK = threading.RLock()
_TOKENS: OrderedDict[str, str] = OrderedDict()
_DISCOVERY_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _company_id(value) -> str:
    if isinstance(value, bool):
        return ""
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Fa-f0-9]{32}", text):
        return text.lower()
    return text if text != "0" and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text) else ""


def register_bling_account(access_token: str, empresa_id: str) -> str:
    """Bind a rotating credential to a verified company without retaining it."""
    identity = _company_id(empresa_id)
    token = str(access_token or "")
    if token and identity:
        with _LOCK:
            alias = hashlib.sha256(token.encode()).hexdigest()
            _TOKENS[alias] = identity
            _TOKENS.move_to_end(alias)
            while len(_TOKENS) > 4096:
                _TOKENS.popitem(last=False)
    return identity


def account_for_token(access_token: str) -> str:
    from .central_accounts_client import bling_account_for_marker, is_marker

    token = str(access_token or "")
    if is_marker(token):
        # A legacy Central must retain the conservative shared desktop quota.
        return _company_id(bling_account_for_marker(token, require_quota=True))
    with _LOCK:
        return _TOKENS.get(hashlib.sha256(token.encode()).hexdigest(), "") if token else ""


def remember_bling_configuration(config: dict | None) -> str:
    """Reuse public evidence only when it belongs to this exact connection."""
    cfg = config or {}
    from .central_accounts_client import bling_account_for_marker, is_marker

    token = str(cfg.get("access_token") or "")
    if is_marker(token):
        return bling_account_for_marker(token)
    connection = str(cfg.get("oauth_connection_id") or "")
    if connection and cfg.get("empresa_oauth_connection_id") == connection:
        return register_bling_account(token, cfg.get("empresa_id"))
    return ""


def resolve_bling_account(client_id: str, loja: str, *, store_id: str | None = None,
                          config: dict | None = None, cancel_callback=None) -> str:
    """Resolve once, CAS-persist the evidence, and bind the current token.

    Unknown credentials consume the exclusive shared request gate before
    discovery, so they cannot race a known alias of the same company.
    """
    from .central_accounts_client import bling_account_for_marker, is_marker
    from .integracoes import _atualizar_bling_cas, _bling_config_atual
    from .bling import _bling_get_with_adaptive_limit
    from .bling_oauth import BlingAuthenticationError, requires_bling_reauthorization

    if not store_id:
        raise HTTPException(409, "A identificação exata da loja é necessária para consultar a conta Bling.")
    key = (str(client_id), str(store_id))
    with _LOCK:
        lock = _DISCOVERY_LOCKS.setdefault(key, threading.Lock())
    with lock:
        cfg = dict(config if config is not None else _bling_config_atual(client_id, loja, store_id))
        if requires_bling_reauthorization(cfg):
            raise BlingAuthenticationError("Reconecte esta conta Bling em Lojas e APIs para continuar.")
        token = str(cfg.get("access_token") or "")
        if is_marker(token):
            identity = _company_id(bling_account_for_marker(token))
            if not identity:
                raise HTTPException(409, "A Central ainda não confirmou a identidade da conta Bling.")
            return identity
        cached = remember_bling_configuration(cfg)
        if cached:
            return cached
        if not token:
            raise HTTPException(409, "A conta Bling não possui uma conexão disponível.")
        def discover():
            return _bling_get_with_adaptive_limit(
                "https://api.bling.com.br/Api/v3/empresas/me/dados-basicos",
                headers={"Authorization": "Bearer " + token}, max_attempts=1,
                timeout=(3.05, 8), total_timeout=15, cancel_callback=cancel_callback,
            )
        response = discover()
        if response is None:
            return ""
        if response.status_code == 401:
            from .integracoes import renovar_token_bling_loja
            cfg = dict(renovar_token_bling_loja(client_id, loja, cfg, store_id=store_id))
            token = str(cfg.get("access_token") or "")
            response = discover()
            if response is None:
                return ""
        if response.status_code in {403, 503}:
            # Company metadata is not required to read sales. A restricted or
            # temporarily unavailable endpoint keeps the inclusive global gate.
            return ""
        destination = urlsplit(str(getattr(response, "url", "")))
        if (destination.scheme != "https" or destination.hostname != "api.bling.com.br"
                or destination.path != "/Api/v3/empresas/me/dados-basicos"
                or destination.username or destination.password or destination.port not in (None, 443)):
            raise HTTPException(502, "Destino inesperado na identificação da conta Bling.")
        if response.status_code != 200:
            retry_after = (getattr(response, "headers", {}) or {}).get("Retry-After")
            status = response.status_code if response.status_code in {401, 403, 429} else 502
            raise HTTPException(status, "A conta Bling não confirmou sua identidade.",
                                headers={"Retry-After": str(retry_after)} if retry_after else None)
        try:
            identity = _company_id(response.json()["data"]["id"])
        except (ValueError, TypeError, KeyError):
            identity = ""
        if not identity:
            raise HTTPException(502, "O Bling retornou uma identidade de empresa inválida.")
        if callable(cancel_callback):
            cancel_callback()
        connection = str(cfg.get("oauth_connection_id") or "") or secrets.token_urlsafe(24)
        saved, current = _atualizar_bling_cas(
            client_id, loja, store_id=store_id,
            expected_refresh_token=str(cfg.get("refresh_token") or ""),
            expected_access_token=token,
            expected_updated_at=str(cfg.get("updated_at") or ""),
            expected_sync_version=cfg.get("_sync_version"),
            dados_api={"oauth_connection_id": connection, "empresa_id": identity,
                       "empresa_oauth_connection_id": connection},
        )
        if not saved:
            # Never assign a response from a replaced authorization to its successor.
            raise HTTPException(409, "A conexão Bling mudou durante a identificação; tente novamente.")
        register_bling_account(token, identity)
        remember_bling_configuration(current)
        return identity
