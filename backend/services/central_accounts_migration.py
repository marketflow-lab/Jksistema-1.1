"""Explicit, fail-closed adoption of local ML/Bling connections by the central vault."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone

import requests
from fastapi import HTTPException

from backend.services import integracoes
from backend.services.secure_credentials import (
    delete_scoped_secret,
    read_scoped_secret,
    secure_store_available,
    write_scoped_secret,
)
from backend.services.transport_security import configure_requests_session


PROVIDERS = ("mercadolivre", "bling")
SECRET_FIELDS = ("app_id", "app_secret", "access_token", "refresh_token")
STORE_ID_RE = re.compile(r"(?:[a-f0-9]{24}|[a-f0-9]{32})")
OPERATION_ID_RE = re.compile(r"[a-f0-9]{32}")
BACKUP_TTL_SECONDS = 30 * 86400
_EXECUTE_LOCK = threading.RLock()


def ensure_available(migration_client):
    """Use the existing read-only operation route to verify deployment and auth."""
    try:
        migration_client.call("GET", "/migrations/legacy/" + "0" * 32)
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if exc.status_code == 404 and code == "central_migration_not_found":
            return
        if exc.status_code == 404:
            raise HTTPException(503, "O serviço de migração da Central ainda não está disponível. Atualize o serviço antes de ativar as contas.") from None
        raise


def _state_path(client_id: str) -> str:
    return os.path.join(integracoes._tenant_path(client_id), "central_migration.json")


def _stores_path(client_id: str) -> str:
    return os.path.join(integracoes._tenant_path(client_id), "lojas_config.json")


def _read_state(client_id: str) -> dict:
    try:
        with open(_state_path(client_id), "r", encoding="utf-8-sig") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _write_state(client_id: str, state: dict) -> None:
    integracoes._integracoes_escrever_lojas_config_atomico(_state_path(client_id), state)


def _raw_stores(client_id: str) -> list[dict]:
    path = _stores_path(client_id)
    try:
        stores = integracoes._integracoes_ler_lojas_config_arquivo(path)
        integracoes._integracoes_validar_identidades_lojas_local(stores)
        return stores
    except FileNotFoundError:
        raise HTTPException(409, "Não existe configuração local de lojas para migrar.") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, "A configuração local de lojas não pôde ser validada.") from None


def _canonical_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _provider_config(store: dict, provider: str) -> dict:
    integrations = store.get("integracoes")
    value = integrations.get(provider) if isinstance(integrations, dict) else None
    return value if isinstance(value, dict) else {}


def _connection_values(config: dict) -> dict:
    return {
        "app_id": str(config.get("app_id") or config.get("client_id") or config.get("id") or "").strip(),
        "app_secret": str(config.get("app_secret") or config.get("client_secret") or config.get("secret") or "").strip(),
        "access_token": str(config.get("access_token") or "").strip(),
        "refresh_token": str(config.get("refresh_token") or "").strip(),
    }


def _expected_identity(config: dict, provider: str) -> tuple[str, str]:
    account_keys = (("seller_id", "user_id", "account_id") if provider == "mercadolivre"
                    else ("empresa_id", "account_id", "user_id"))
    account = next((str(config.get(key) or "").strip() for key in account_keys
                    if str(config.get(key) or "").strip()), "")
    site = str(config.get("site_id") or "").strip() if provider == "mercadolivre" else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{0,128}", account):
        account = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{0,20}", site):
        site = ""
    return account, site


def _validate_stores(stores: list[dict]) -> list[dict]:
    if not stores or len(stores) > 100:
        raise HTTPException(409, "A quantidade de lojas locais não é válida para a migração.")
    seen = set()
    result = []
    for store in stores:
        store_id = str(store.get("store_id") or "").strip()
        name = str(store.get("nome") or "").strip()
        if not STORE_ID_RE.fullmatch(store_id) or store_id in seen or not 1 <= len(name) <= 100:
            raise HTTPException(409, "Há uma loja com identidade inválida ou duplicada.")
        seen.add(store_id)
        connections = []
        for provider in PROVIDERS:
            config = _provider_config(store, provider)
            values = _connection_values(config)
            if not all(values.values()):
                raise HTTPException(409, detail={
                    "code": "legacy_connection_incomplete", "store_id": store_id,
                    "provider": provider,
                    "message": "A conexão local está incompleta e precisa ser autorizada novamente.",
                })
            expected_account, expected_site = _expected_identity(config, provider)
            try:
                expires_at = int(float(config.get("expires_at") or 0))
            except (TypeError, ValueError):
                expires_at = 0
            connections.append({"provider": provider, **values,
                                "expected_account_id": expected_account,
                                "expected_site_id": expected_site,
                                "expires_at": expires_at})
        result.append({"store_id": store_id, "name": name, "connections": connections,
                       "turbo_local": bool(_provider_config(store, "mercadoturbo").get("token"))})
    return result


def preview(client_id: str) -> dict:
    stores = _validate_stores(_raw_stores(client_id))
    fingerprint = _canonical_hash(stores)
    return {
        "eligible": True,
        "preview_fingerprint": fingerprint,
        "stores_total": len(stores),
        "connections_total": sum(len(store["connections"]) for store in stores),
        "turbo_local_total": sum(1 for store in stores if store["turbo_local"]),
        "stores": [{"store_id": store["store_id"], "name": store["name"],
                    "connections": [connection["provider"] for connection in store["connections"]],
                    "turbo_local": store["turbo_local"]} for store in stores],
    }


def _request(method: str, url: str, **kwargs):
    base = requests.Session()
    session = configure_requests_session(base, os.environ)
    try:
        return session.request(method, url, timeout=(3.05, 25), allow_redirects=False, **kwargs)
    finally:
        session.close()


def _identity(provider: str, access_token: str) -> tuple[str, str]:
    url = ("https://api.mercadolibre.com/users/me" if provider == "mercadolivre"
           else "https://api.bling.com.br/Api/v3/empresas/me/dados-basicos")
    try:
        response = _request("GET", url, headers={"Authorization": "Bearer " + access_token})
    except requests.RequestException:
        raise HTTPException(503, "Não foi possível validar a conta na plataforma.") from None
    if response.status_code == 401:
        raise HTTPException(401, "A conexão precisa ser renovada.")
    if response.status_code == 429:
        raise HTTPException(429, "A plataforma limitou temporariamente a validação da conta.")
    if response.status_code != 200:
        raise HTTPException(503, "A plataforma não respondeu à validação da conta.")
    try:
        payload = response.json()
        account = payload if provider == "mercadolivre" else payload.get("data")
        identity = str(account.get("id") or "")
        site = str(account.get("site_id") or "") if provider == "mercadolivre" else ""
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(502, "A plataforma retornou uma identidade inválida.") from None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity):
        raise HTTPException(502, "A plataforma retornou uma identidade inválida.")
    return identity, site if re.fullmatch(r"[A-Za-z0-9_-]{0,20}", site) else ""


def _refresh(provider: str, values: dict) -> dict:
    if provider == "mercadolivre":
        url = "https://api.mercadolibre.com/oauth/token"
        kwargs = {"headers": {"Accept": "application/json"}, "data": {
            "grant_type": "refresh_token", "client_id": values["app_id"],
            "client_secret": values["app_secret"], "refresh_token": values["refresh_token"]}}
    else:
        from requests.auth import HTTPBasicAuth
        url = "https://www.bling.com.br/Api/v3/oauth/token"
        kwargs = {"auth": HTTPBasicAuth(values["app_id"], values["app_secret"]),
                  "headers": {"Accept": "application/json", "enable-jwt": "1"},
                  "data": {"grant_type": "refresh_token", "refresh_token": values["refresh_token"]}}
    try:
        response = _request("POST", url, **kwargs)
    except requests.RequestException:
        raise HTTPException(503, {"code": "legacy_refresh_uncertain",
                                 "message": "A plataforma não confirmou a renovação. Confira a conexão antes de repetir a migração."}) from None
    if response.status_code in (400, 401, 403):
        raise HTTPException(401, "A conexão precisa de uma nova autorização.")
    if response.status_code == 429:
        raise HTTPException(429, "A plataforma limitou temporariamente as renovações.")
    if response.status_code != 200:
        raise HTTPException(503, {"code": "legacy_refresh_uncertain",
                                 "message": "A plataforma não confirmou a renovação. Confira a conexão antes de repetir a migração."})
    try:
        payload = response.json()
        access = str(payload.get("access_token") or "").strip()
        refresh = str(payload.get("refresh_token") or values["refresh_token"]).strip()
        expires_in = int(payload.get("expires_in") or 0)
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(502, {"code": "legacy_refresh_uncertain",
                                 "message": "A resposta de renovação não pôde ser validada. Confira a conexão antes de repetir."}) from None
    if not access or not refresh or expires_in < 60:
        raise HTTPException(502, {"code": "legacy_refresh_uncertain",
                                 "message": "A resposta de renovação não pôde ser validada. Confira a conexão antes de repetir."})
    return {**values, "access_token": access, "refresh_token": refresh,
            "expires_at": int(time.time()) + expires_in}


def _persist_refresh(client_id: str, store_id: str, provider: str, before: dict, after: dict) -> None:
    with integracoes._LOJAS_CONFIG_LOCK:
        stores = _raw_stores(client_id)
        matches = [store for store in stores if str(store.get("store_id") or "").strip() == store_id]
        if len(matches) != 1:
            raise HTTPException(409, "A loja mudou durante a migração.")
        config = _provider_config(matches[0], provider)
        current = _connection_values(config)
        if not secrets.compare_digest(current["refresh_token"], before["refresh_token"]):
            raise HTTPException(409, "A conexão mudou durante a migração; gere uma nova prévia.")
        config.update(access_token=after["access_token"], refresh_token=after["refresh_token"],
                      expires_at=after["expires_at"], updated_at=str(time.time()))
        integracoes._integracoes_validar_lojas_config(stores, "migração para a central")
        integracoes._integracoes_escrever_lojas_config_atomico(_stores_path(client_id), stores)


def _validation_failure(exc, store_id, provider, values):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
    code = detail.get("code") or ("legacy_reauthorization_required" if exc.status_code == 401
                                  else "legacy_connection_validation_failed")
    failure = {"code": code, "store_id": store_id, "provider": provider,
               "message": detail.get("message", "Não foi possível validar a conexão.")}
    if code == "legacy_refresh_uncertain":
        failure["refresh_fingerprint"] = hashlib.sha256(values["refresh_token"].encode()).hexdigest()
    return HTTPException(exc.status_code, failure)


def _prepare_connections(client_id: str, stores: list[dict]) -> list[dict]:
    prepared = copy.deepcopy(stores)
    for store in prepared:
        for connection in store["connections"]:
            provider = connection["provider"]
            values = {field: connection[field] for field in SECRET_FIELDS}
            expires_at = int(connection.get("expires_at") or 0)
            refresh_needed = expires_at <= int(time.time()) + 300
            try:
                if not refresh_needed:
                    identity, site = _identity(provider, values["access_token"])
                else:
                    refreshed = _refresh(provider, values)
                    _persist_refresh(client_id, store["store_id"], provider, values, refreshed)
                    values = {field: refreshed[field] for field in SECRET_FIELDS}
                    expires_at = refreshed["expires_at"]
                    identity, site = _identity(provider, values["access_token"])
            except HTTPException as exc:
                if exc.status_code == 401 and not refresh_needed:
                    try:
                        refreshed = _refresh(provider, values)
                        _persist_refresh(client_id, store["store_id"], provider, values, refreshed)
                        values = {field: refreshed[field] for field in SECRET_FIELDS}
                        expires_at = refreshed["expires_at"]
                        identity, site = _identity(provider, values["access_token"])
                    except HTTPException as retry_exc:
                        raise _validation_failure(retry_exc, store["store_id"], provider, values) from None
                else:
                    raise _validation_failure(exc, store["store_id"], provider, values) from None
            expected = str(connection.get("expected_account_id") or "")
            expected_site = str(connection.get("expected_site_id") or "")
            if (expected and not secrets.compare_digest(expected, identity)) or (
                    expected_site and not secrets.compare_digest(expected_site, site)):
                raise HTTPException(409, detail={
                    "code": "legacy_identity_mismatch", "store_id": store["store_id"],
                    "provider": provider,
                    "message": "A conta retornada pela plataforma diverge da configuração local.",
                })
            connection.update(values, expires_at=expires_at,
                              expected_account_id=identity, expected_site_id=site)
    return prepared


def _backup_target(client_id: str, operation_id: str, store_id: str, provider: str, field: str) -> str:
    return ":".join(("central-migration-v1", client_id, operation_id, store_id, provider, field))


def _delete_backup(client_id: str, state: dict) -> bool:
    operation_id = str(state.get("operation_id") or "")
    deleted = True
    for entry in state.get("backup_entries") or []:
        if not isinstance(entry, dict):
            deleted = False
            continue
        try:
            removed = delete_scoped_secret(_backup_target(
                client_id, operation_id, entry["store_id"], entry["provider"], entry["field"]))
            deleted = bool(removed) and deleted
        except Exception:
            deleted = False
    return deleted


def cleanup_expired_backup(client_id: str, *, now: int | None = None) -> dict:
    state = _read_state(client_id)
    expires = int(state.get("backup_expires_at") or 0)
    if expires and expires <= int(time.time() if now is None else now) and state.get("backup_status") != "deleted":
        deleted = _delete_backup(client_id, state)
        state["backup_status"] = "deleted" if deleted else "delete_failed"
        if deleted:
            state["backup_deleted_at"] = int(time.time() if now is None else now)
        _write_state(client_id, state)
    return state


def cleanup_all_expired_backups() -> None:
    root = str(getattr(integracoes, "PASTA_INFO", "") or "")
    if not root or not os.path.isdir(root):
        return
    for name in os.listdir(root):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
            continue
        if os.path.isfile(os.path.join(root, name, "central_migration.json")):
            cleanup_expired_backup(name)


def _create_backup(client_id: str, operation_id: str, stores: list[dict], source_fingerprint: str) -> dict:
    if not secure_store_available():
        raise HTTPException(503, "O Gerenciador de Credenciais do Windows não está disponível.")
    created = int(time.time())
    entries = []
    written = []
    try:
        for store in stores:
            for connection in store["connections"]:
                for field in SECRET_FIELDS:
                    value = str(connection[field])
                    target = _backup_target(client_id, operation_id, store["store_id"], connection["provider"], field)
                    if not write_scoped_secret(target, value):
                        raise RuntimeError("secure_write_failed")
                    written.append(target)
                    if not secrets.compare_digest(read_scoped_secret(target), value):
                        raise RuntimeError("secure_readback_failed")
                    entries.append({"store_id": store["store_id"], "provider": connection["provider"],
                                    "field": field, "value_hash": hashlib.sha256(value.encode()).hexdigest()})
    except Exception:
        for target in written:
            try:
                delete_scoped_secret(target)
            except Exception:
                pass
        raise HTTPException(503, "Não foi possível criar e verificar o backup seguro; nada foi enviado.") from None
    return {
        "schema": 1, "operation_id": operation_id, "status": "backup_ready",
        "source_fingerprint": source_fingerprint,
        "stores_total": len(stores), "connections_total": sum(len(s["connections"]) for s in stores),
        "backup_status": "frozen", "backup_created_at": created,
        "backup_expires_at": created + BACKUP_TTL_SECONDS, "backup_entries": entries,
        "request_metadata": [{"store_id": store["store_id"], "name": store["name"],
                              "connections": [{key: connection[key] for key in (
                                  "provider", "expires_at", "expected_account_id", "expected_site_id")}
                                  for connection in store["connections"]]} for store in stores],
    }


def _cloud_payload(operation_id: str, stores: list[dict]) -> dict:
    return {"operation_id": operation_id, "stores": [{
        "store_id": store["store_id"], "name": store["name"],
        "connections": [{key: connection[key] for key in (
            "provider", "app_id", "app_secret", "access_token", "refresh_token", "expires_at",
            "expected_account_id", "expected_site_id")}
                        for connection in store["connections"]],
    } for store in stores]}


def _scrub_local(client_id: str, store_ids: set[str], operation_id: str) -> None:
    removable = {"app_id", "client_id", "id", "app_secret", "client_secret", "secret",
                 "access_token", "refresh_token", "oauth_draft", "oauth_pending_state"}
    with integracoes._LOJAS_CONFIG_LOCK:
        stores = _raw_stores(client_id)
        present = {str(store.get("store_id") or "").strip() for store in stores}
        if not store_ids <= present:
            raise HTTPException(409, "A configuração local mudou antes da limpeza final.")
        for store in stores:
            store_id = str(store.get("store_id") or "").strip()
            if store_id not in store_ids:
                continue
            for provider in PROVIDERS:
                config = _provider_config(store, provider)
                for key in removable:
                    config.pop(key, None)
                config.update(central_migrated=True, connected=True, migration_id=operation_id)
        integracoes._integracoes_validar_lojas_config(stores, "limpeza após migração")
        integracoes._integracoes_escrever_lojas_config_atomico(_stores_path(client_id), stores)


def execute(client_id: str, migration_client, *, operation_id: str, preview_fingerprint: str,
            confirmed: bool) -> dict:
    # Two explicit clicks must not rotate the same local token before its backup.
    with _EXECUTE_LOCK:
        return _execute(client_id, migration_client, operation_id=operation_id,
                        preview_fingerprint=preview_fingerprint, confirmed=confirmed)


def _completed_result(client_id, state):
    store_ids = {entry["store_id"] for entry in state.get("backup_entries") or []}
    if not store_ids:
        raise HTTPException(409, "A migração não possui identidades locais verificáveis.")
    _scrub_local(client_id, store_ids, state["operation_id"])
    state.update(status="completed", completed_at=int(time.time()), failure=None)
    _write_state(client_id, state)
    return {"success": True, "status": "completed", "operation_id": state["operation_id"],
            "stores_total": state["stores_total"], "connections_total": state["connections_total"],
            "backup_expires_at": state["backup_expires_at"], "logout_required": True}


def _execute(client_id: str, migration_client, *, operation_id: str, preview_fingerprint: str,
             confirmed: bool) -> dict:
    if not confirmed:
        raise HTTPException(400, "Confirme a migração depois de revisar a prévia.")
    if not OPERATION_ID_RE.fullmatch(str(operation_id or "")):
        raise HTTPException(400, "Identificador de migração inválido.")
    state = cleanup_expired_backup(client_id)
    same_operation = (state.get("operation_id") == operation_id
                      and state.get("source_fingerprint") == preview_fingerprint)
    if same_operation and state.get("status") == "completed":
        return _completed_result(client_id, state)
    previous_id = str(state.get("operation_id") or "")
    if state.get("status") in {"uploading", "failed"} and state.get("backup_entries"):
        # A failed response may have activated the central refresh authority.
        # Consult the original operation BEFORE any local validation or renewal.
        try:
            remote = migration_client.call("GET", "/migrations/legacy/" + previous_id)
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if exc.status_code != 404 or code != "central_migration_not_found":
                raise
            remote = None
        if remote is not None:
            if remote.get("operation_id") != previous_id:
                raise HTTPException(502, "A central retornou outra operação de migração.")
            if remote.get("status") == "completed" and remote.get("success") is True:
                return _completed_result(client_id, state)
            if remote.get("status") != "failed":
                raise HTTPException(409, "A migração ainda está em andamento. Consulte o status antes de repetir.")
        if not same_operation:
            raise HTTPException(409, {"code": "legacy_migration_pending", "operation_id": previous_id,
                                      "preview_fingerprint": state.get("source_fingerprint"),
                                      "message": "Retome a operação original antes de iniciar outra migração."})
    current_preview = preview(client_id)
    resumable = (state.get("operation_id") == operation_id
                 and state.get("source_fingerprint") == preview_fingerprint
                 and state.get("backup_status") == "frozen"
                 and state.get("status") in {"backup_ready", "uploading", "failed"})
    if current_preview["preview_fingerprint"] != preview_fingerprint and not resumable:
        raise HTTPException(409, "As lojas mudaram depois da prévia; revise novamente.")
    stores = _validate_stores(_raw_stores(client_id))
    previous_failure = state.get("failure") or {}
    if previous_failure.get("code") == "legacy_refresh_uncertain":
        for store in stores:
            for connection in store["connections"]:
                if (store["store_id"] == previous_failure.get("store_id")
                        and connection["provider"] == previous_failure.get("provider")
                        and hashlib.sha256(connection["refresh_token"].encode()).hexdigest()
                        == previous_failure.get("refresh_fingerprint")):
                    raise HTTPException(409, previous_failure)
    if not resumable:
        try:
            prepared = _prepare_connections(client_id, stores)
            state = _create_backup(client_id, operation_id, prepared, preview_fingerprint)
            _write_state(client_id, state)
        except HTTPException as exc:
            public_failure = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            _write_state(client_id, {"schema": 1, "operation_id": operation_id, "status": "failed",
                                     "source_fingerprint": preview_fingerprint,
                                     "failure": public_failure})
            raise
    else:
        # Replay only the frozen request. Renewing here could race with a central
        # activation and change the idempotency fingerprint of this operation.
        prepared = stores
        expected = {(entry["store_id"], entry["provider"], entry["field"]): entry["value_hash"]
                    for entry in state.get("backup_entries") or []}
        for store in prepared:
            for connection in store["connections"]:
                for field in SECRET_FIELDS:
                    key = (store["store_id"], connection["provider"], field)
                    value = connection[field]
                    target = _backup_target(client_id, operation_id, *key)
                    if expected.get(key) != hashlib.sha256(value.encode()).hexdigest() or not secrets.compare_digest(
                            read_scoped_secret(target), value):
                        raise HTTPException(409, "O backup congelado não corresponde mais às conexões locais.")
        if state.get("request_metadata"):
            values_by_key = {(store["store_id"], connection["provider"]): connection
                             for store in prepared for connection in store["connections"]}
            prepared = copy.deepcopy(state["request_metadata"])
            for store in prepared:
                for connection in store["connections"]:
                    local = values_by_key.get((store["store_id"], connection["provider"]))
                    if local is None:
                        raise HTTPException(409, "As identidades locais mudaram durante a migração.")
                    connection.update({field: local[field] for field in SECRET_FIELDS})
    state.update(status="uploading", failure=None)
    _write_state(client_id, state)
    try:
        result = migration_client.call("POST", "/migrations/legacy", _cloud_payload(operation_id, prepared))
    except HTTPException as exc:
        state.update(status="failed", failure=(exc.detail if isinstance(exc.detail, dict)
                                               else {"message": str(exc.detail)}))
        _write_state(client_id, state)
        raise
    if result.get("status") != "completed" or result.get("success") is not True:
        state.update(status="failed", failure={"message": "A central não confirmou a ativação."})
        _write_state(client_id, state)
        raise HTTPException(503, "A central não confirmou a ativação.")
    if result.get("operation_id") != operation_id:
        raise HTTPException(502, "A central retornou outra operação de migração.")
    return _completed_result(client_id, state)


def status(client_id: str, migration_client=None, operation_id: str = "") -> dict:
    state = cleanup_expired_backup(client_id)
    operation_id = operation_id or str(state.get("operation_id") or "")
    remote_error = None
    if operation_id and migration_client is not None and OPERATION_ID_RE.fullmatch(operation_id):
        try:
            remote = migration_client.call("GET", "/migrations/legacy/" + operation_id)
            if remote.get("operation_id") != operation_id:
                raise HTTPException(502, {"code": "central_migration_response_invalid",
                                          "message": "A central retornou outra operação de migração."})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            if exc.status_code != 404 or detail.get("code") != "central_migration_not_found":
                remote_error = {"status_code": exc.status_code,
                                "code": detail.get("code", "central_unavailable"),
                                "message": detail.get("message", "Não foi possível consultar o status da migração.")}
            remote = None
    else:
        remote = None
    return {
        "available": migration_client is not None,
        "operation_id": str(state.get("operation_id") or ""),
        "preview_fingerprint": str(state.get("source_fingerprint") or ""),
        "status": str(state.get("status") or "not_started"),
        "stores_total": int(state.get("stores_total") or 0),
        "connections_total": int(state.get("connections_total") or 0),
        "backup_status": str(state.get("backup_status") or "none"),
        "backup_expires_at": state.get("backup_expires_at"),
        "failure": state.get("failure"),
        "remote": remote,
        "remote_error": remote_error,
        "needs_local_finalize": bool(remote and remote.get("success") is True
                                     and remote.get("status") == "completed"
                                     and remote.get("operation_id") == state.get("operation_id")
                                     and state.get("status") != "completed"),
    }


def finalize_after_central_login(client_id: str, central_stores: list[dict]) -> bool:
    """Finish local scrubbing after an uncertain upload that did activate centrally."""
    state = cleanup_expired_backup(client_id)
    if (state.get("backup_status") != "frozen"
            or state.get("status") not in {"backup_ready", "uploading", "failed"}
            or not OPERATION_ID_RE.fullmatch(str(state.get("operation_id") or ""))):
        return False
    targets = {}
    for entry in state.get("backup_entries") or []:
        if not isinstance(entry, dict):
            return False
        store_id = str(entry.get("store_id") or "")
        provider = str(entry.get("provider") or "")
        field = str(entry.get("field") or "")
        if provider not in PROVIDERS or field not in SECRET_FIELDS or not STORE_ID_RE.fullmatch(store_id):
            return False
        targets.setdefault(store_id, set()).add(provider)
    if not targets or any(providers != set(PROVIDERS) for providers in targets.values()):
        return False
    central_by_id = {str((store or {}).get("store_id") or ""): store for store in central_stores or []}
    for store_id in targets:
        integrations = (central_by_id.get(store_id) or {}).get("integracoes") or {}
        if not all(bool((integrations.get(provider) or {}).get("central")) for provider in PROVIDERS):
            return False
    _scrub_local(client_id, set(targets), str(state["operation_id"]))
    state.update(status="completed", completed_at=int(time.time()), failure=None)
    _write_state(client_id, state)
    return True
