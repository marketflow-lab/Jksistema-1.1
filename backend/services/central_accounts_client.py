"""Private, memory-only central session and compatibility transport.

Provider credentials never enter this process. Opaque transport references are
created for existing backend helpers and stripped from public store responses.
"""
from __future__ import annotations

import base64
import contextvars
import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlsplit

import requests
from fastapi import HTTPException

from .remote_auth_contracts import load_remote_auth_configuration
from .transport_security import configure_requests_session


_current = contextvars.ContextVar("jk_central_session", default=None)
_migration_current = contextvars.ContextVar("jk_central_migration_session", default=None)
_sessions = {}
_migration_sessions = {}
_references = {}
_lock = threading.RLock()
PREFIX = "jk-central:"


def validate_public_stores(stores):
    identities = set()
    if not isinstance(stores, list) or len(stores) > 100:
        raise ValueError("invalid_central_stores")
    for row in stores:
        if (not isinstance(row, dict) or set(row) != {"store_id", "nome", "owner_client_id", "access", "integracoes"}
                or not re.fullmatch(r"(?:[a-f0-9]{24}|[a-f0-9]{32})", str(row.get("store_id", "")))
                or row["store_id"] in identities or row["access"] not in ("owner", "read", "write")
                or not isinstance(row["nome"], str) or not 1 <= len(row["nome"]) <= 100
                or not isinstance(row["owner_client_id"], str) or not 1 <= len(row["owner_client_id"]) <= 128
                or not isinstance(row["integracoes"], dict)
                or set(row["integracoes"]) - {"mercadolivre", "bling"}):
            raise ValueError("invalid_central_store")
        identities.add(row["store_id"])
        for cfg in row["integracoes"].values():
            if (not isinstance(cfg, dict) or set(cfg) != {"connected", "central", "user_id", "site_id"}
                    or cfg["connected"] is not True or cfg["central"] is not True
                    or not isinstance(cfg["user_id"], str) or len(cfg["user_id"]) > 128
                    or not isinstance(cfg["site_id"], str) or len(cfg["site_id"]) > 20):
                raise ValueError("invalid_central_connection")


def session_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_expired():
    return HTTPException(401, "Sessão da central expirada. Faça o login novamente.",
                         headers={"WWW-Authenticate": "Bearer"})


def migration_session_expired():
    return HTTPException(401, "Autorização de migração expirada. Faça o login novamente.",
                         headers={"WWW-Authenticate": "Bearer"})


class CentralClient:
    def __init__(self, extension, user_data, permissions, *, configuration=None, transport=None):
        self.configuration = configuration or load_remote_auth_configuration(os.environ)
        if self.configuration is None:
            raise ValueError("central_configuration_missing")
        self._credential = extension["session"]
        self.expires_at = extension["expires_at"]
        self.tenant = user_data["client_id"]
        self.username = user_data["username"]
        self.machine = user_data["machine_id"]
        self.permissions = dict(permissions)
        self._stores = copy.deepcopy(extension["stores"])
        validate_public_stores(self._stores)
        self._transport = transport
        self._ref = secrets.token_hex(16)
        parsed = urlsplit(self.configuration.url)
        self._origin = parsed.scheme + "://" + parsed.netloc
        self.refreshed_at = int(time.time())

    def call(self, method, path, body=None):
        from backend.services.perguntas_loading_transport import check_budget, request_timeout
        check_budget()
        if self.expires_at <= time.time():
            raise session_expired()
        if not path.startswith("/") or ".." in path or "?" in path:
            raise ValueError("central_path_invalid")
        owns_session = self._transport is None
        session = self._transport or configure_requests_session(requests.Session(), os.environ)
        try:
            response = session.request(method, self._origin + "/api/central/v1" + path,
                json=body, headers={"Authorization": "Bearer " + self._credential, "X-JK-Machine": self.machine},
                timeout=request_timeout((3.05, 35)), allow_redirects=False)
            check_budget()
            if len(response.content) > 12 * 1024 * 1024:
                raise HTTPException(502, "Resposta da central excedeu o limite.")
            if response.status_code == 401:
                raise session_expired()
            if not 200 <= response.status_code < 300:
                try:
                    code = response.json().get("code", "central_unavailable")
                except (ValueError, AttributeError):
                    code = "central_unavailable"
                messages = {
                    "central_refresh_busy": "A conexão está sendo renovada. Aguarde e solicite novamente.",
                    "central_reconnect_required": "Reconecte esta conta em Lojas e APIs.",
                    "central_result_uncertain": "Não foi possível confirmar o resultado. Consulte a plataforma antes de repetir.",
                    "central_operation_already_submitted": "Esta operação já foi enviada. Confira o resultado antes de repetir.",
                    "central_store_denied": "Você não tem mais acesso a esta loja. Atualize a lista de lojas.",
                    "central_store_read_only": "Seu acesso a esta loja permite somente consultas.",
                    "central_owner_required": "Somente o responsável pela loja pode alterar seu acesso.",
                }
                if code == "central_result_uncertain":
                    raise HTTPException(409, messages[code])
                raise HTTPException(response.status_code if response.status_code in (400, 403, 404, 409, 413, 429) else 503,
                                    messages.get(code, "A central não conseguiu concluir a solicitação."))
            return response.json()
        except (requests.RequestException, ValueError):
            if path.endswith("/request") and (body or {}).get("method") != "GET":
                raise HTTPException(409, "Resultado incerto. Consulte a plataforma antes de repetir a operação.") from None
            raise HTTPException(503, "Não foi possível consultar a central. Verifique a conexão e tente novamente.") from None
        finally:
            if owns_session:
                session.close()

    def refresh_stores(self):
        result = self.call("GET", "/bootstrap")
        validate_public_stores(result.get("stores"))
        self._stores = copy.deepcopy(result["stores"])
        self.refreshed_at = int(time.time())
        return self.public_stores()

    def public_stores(self):
        return copy.deepcopy(self._stores)

    def stores(self):
        rows = self.public_stores()
        for row in rows:
            for provider, cfg in row["integracoes"].items():
                marker = PREFIX + self._ref + ":" + row["store_id"] + ":" + provider
                cfg.update(access_token=marker, refresh_token="", oauth_invalid=False)
                with _lock:
                    _references[marker] = (self, row["store_id"], provider)
        return rows


class MigrationClient:
    def __init__(self, extension, user_data, permissions, *, configuration=None, transport=None):
        self.configuration = configuration or load_remote_auth_configuration(os.environ)
        if self.configuration is None:
            raise ValueError("central_configuration_missing")
        self._credential = extension["session"]
        self.expires_at = extension["expires_at"]
        self.tenant = user_data["client_id"]
        self.username = user_data["username"]
        self.machine = user_data["machine_id"]
        self.permissions = dict(permissions)
        self._transport = transport
        parsed = urlsplit(self.configuration.url)
        self._origin = parsed.scheme + "://" + parsed.netloc

    def call(self, method, path, body=None):
        if self.expires_at <= time.time():
            raise migration_session_expired()
        if not path.startswith("/migrations/legacy") or ".." in path or "?" in path:
            raise ValueError("central_migration_path_invalid")
        owns_session = self._transport is None
        session = self._transport or configure_requests_session(requests.Session(), os.environ)
        try:
            response = session.request(
                method, self._origin + "/api/central/v1" + path, json=body,
                headers={"Authorization": "Bearer " + self._credential,
                         "X-JK-Machine": self.machine},
                timeout=(3.05, 90), allow_redirects=False)
            if len(response.content) > 256 * 1024:
                raise HTTPException(502, "Resposta de migração excedeu o limite.")
            if response.status_code == 401:
                raise migration_session_expired()
            if not 200 <= response.status_code < 300:
                try:
                    error = response.json()
                except (ValueError, AttributeError):
                    error = {}
                code = str(error.get("code") or "central_unavailable")
                store_id = str(error.get("store_id") or "")
                provider = str(error.get("provider") or "")
                messages = {
                    "central_reconnect_required": "A conexão precisa de uma nova autorização.",
                    "central_identity_mismatch": "A conta retornada pela plataforma diverge da configuração local.",
                    "central_account_already_owned": "A conta já pertence a outra empresa na central.",
                    "central_store_identity_conflict": "O identificador da loja já pertence a outro cadastro.",
                    "central_migration_conflict": "Esta operação já existe com outro conteúdo.",
                    "central_migration_revoked": "Esta máquina ou usuário não está mais autorizado a migrar.",
                }
                detail = {"code": code, "message": messages.get(
                    code, "A central não conseguiu concluir a migração.")}
                if store_id:
                    detail["store_id"] = store_id
                if provider in {"mercadolivre", "bling"}:
                    detail["provider"] = provider
                raise HTTPException(response.status_code if response.status_code in (400, 403, 404, 409, 413, 429) else 503,
                                    detail)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("central_migration_response_invalid")
            return result
        except HTTPException:
            raise
        except (requests.RequestException, ValueError):
            raise HTTPException(503, "Não foi possível consultar a central. Tente novamente.") from None
        finally:
            if owns_session:
                session.close()


def register_login(local_token, attempt):
    client = CentralClient(attempt.central, attempt.user_data, attempt.permissions)
    with _lock:
        stale = [key for key, value in _sessions.items() if value.expires_at <= time.time()]
        for key in stale:
            _sessions.pop(key, None)
        for marker, (old, _, _) in list(_references.items()):
            if old.expires_at <= time.time():
                _references.pop(marker, None)
        _sessions[session_key(local_token)] = client
    return client


def register_migration_login(local_token, attempt):
    client = MigrationClient(attempt.central_migration, attempt.user_data, attempt.permissions)
    with _lock:
        stale = [key for key, value in _migration_sessions.items() if value.expires_at <= time.time()]
        for key in stale:
            _migration_sessions.pop(key, None)
        _migration_sessions[session_key(local_token)] = client
    return client


def end_migration_login(local_token):
    with _lock:
        _migration_sessions.pop(session_key(local_token), None)
    _migration_current.set(None)


def bind_request(local_token, payload):
    with _lock:
        client = _sessions.get(session_key(local_token)) if payload.get("jk_central") == 1 else None
        migration = (_migration_sessions.get(session_key(local_token))
                     if payload.get("jk_central_migration") == 1 else None)
    if payload.get("jk_central") == 1:
        if (client is None or client.expires_at <= time.time()
                or client.tenant != payload.get("client_id") or client.username != payload.get("sub")
                or client.machine != payload.get("machine_id")):
            raise session_expired()
    if payload.get("jk_central_migration") == 1:
        if (migration is None or migration.expires_at <= time.time()
                or migration.tenant != payload.get("client_id") or migration.username != payload.get("sub")
                or migration.machine != payload.get("machine_id")):
            raise migration_session_expired()
    _current.set(client)
    _migration_current.set(migration)
    return client


def current(client_id=None):
    client = _current.get()
    if client is not None and client_id is not None and client.tenant != client_id:
        raise HTTPException(403, "A sessão não autoriza outra empresa.")
    return client


def current_migration(client_id=None):
    client = _migration_current.get()
    if client is not None and client_id is not None and client.tenant != client_id:
        raise HTTPException(403, "A migração não autoriza outra empresa.")
    return client


def is_marker(value):
    return str(value or "").startswith(PREFIX)


def provider_request(marker, method, url, *, headers=None, params=None, json=None, data=None, **_kwargs):
    with _lock:
        reference = _references.get(marker)
    if reference is None:
        raise session_expired()
    client, store_id, provider = reference
    active = current()
    if active is not None and active is not client:
        raise HTTPException(403, "A conexão pertence a outra sessão.")
    parts = urlsplit(url)
    allowed = {"mercadolivre": {"api.mercadolibre.com"}, "bling": {"api.bling.com.br", "www.bling.com.br"}}
    if (parts.scheme != "https" or parts.hostname not in allowed[provider] or parts.username or parts.password
            or parts.port not in (None, 443) or parts.fragment):
        raise HTTPException(400, "Destino não autorizado para esta conexão.")
    path = parts.path
    if provider == "bling":
        if not path.startswith("/Api/v3/"):
            raise HTTPException(400, "Rota não autorizada para esta conexão.")
        path = path[len("/Api/v3"):]
    parameters = dict(parse_qsl(parts.query))
    parameters.update(dict(params or {}))
    if "access_token" in parameters:
        if parameters.pop("access_token") != marker:
            raise HTTPException(400, "Credencial inesperada na solicitação.")
    if data is not None and json is None:
        try:
            json = __import__("json").loads(data) if isinstance(data, (str, bytes)) else data
        except ValueError:
            raise HTTPException(400, "Formato não suportado para esta operação.") from None
    result = client.call("POST", f"/stores/{store_id}/request", {
        "provider": provider, "method": method.upper(), "path": path, "params": parameters,
        "headers": {str(key).lower(): str(value) for key, value in (headers or {}).items()
                    if str(key).lower() in {"x-format-new", "x-version", "if-match", "if-none-match"}},
        "body": json, "request_id": secrets.token_hex(16)})
    response = requests.Response()
    response.status_code = result["status"]
    if response.status_code == 401 or (method.upper() != "GET" and response.status_code >= 500):
        raise HTTPException(409, "A plataforma não confirmou esta operação. Confira a conexão e o resultado antes de repetir.")
    response._content = base64.b64decode(result["body_base64"], validate=True)
    response.headers.update(result.get("headers", {}))
    response.url = url
    response.encoding = "utf-8"
    return response


class CentralAwareSession(requests.Session):
    def request(self, method, url, **kwargs):
        header = str((kwargs.get("headers") or {}).get("Authorization", ""))
        marker = header.removeprefix("Bearer ")
        if is_marker(marker):
            return provider_request(marker, method, url, **kwargs)
        return super().request(method, url, **kwargs)


def with_request_context(function):
    """Carry the initiating authorization into an explicitly requested job."""
    import functools
    context = contextvars.copy_context()

    @functools.wraps(function)
    def invoke(*args, **kwargs):
        return context.copy().run(function, *args, **kwargs)

    return invoke


def assert_manual_path(path):
    if current() is None:
        return
    if (path.endswith(("/auto", "/auto-push", "/auto-pull"))
            or path in ("/api/ia/secrets/provisionar", "/api/firebase/realtime-presence/session")):
        raise HTTPException(409, "A sessão central usa sincronização somente quando solicitada.")
