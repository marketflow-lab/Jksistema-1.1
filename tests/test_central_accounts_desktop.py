from __future__ import annotations

import asyncio
import base64
import copy
import json
import threading
import time

import pytest
import requests
from fastapi import HTTPException

from backend.services import central_accounts_client as desktop
from backend.services import remote_auth_client
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState, load_remote_auth_configuration
from backend.services.remote_auth_verification import validate_success_payload, verify_signed_response
from test_remote_auth_client import ENV, NOW, NONCE, success_payload, token_claims
from test_admin_usuarios_remote_auth import configure_common, request


STORE = {"store_id": "a" * 32, "nome": "Shop", "owner_client_id": "tenant-a", "access": "owner",
         "integracoes": {"mercadolivre": {"central": True, "connected": True, "user_id": "123", "site_id": "MLB"}}}


class Transport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.status = 200
        self.body = {"status": 200, "headers": {}, "body_base64": base64.b64encode(b'{"ok":true}').decode()}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        response = requests.Response()
        response.status_code = self.status
        response._content = json.dumps(self.body).encode()
        return response


@pytest.fixture(autouse=True)
def clean_context():
    token = desktop._current.set(None)
    desktop._sessions.clear()
    desktop._references.clear()
    yield
    desktop._current.reset(token)
    desktop._sessions.clear()
    desktop._references.clear()


def client():
    transport = Transport()
    value = desktop.CentralClient({"session": "opaque-session", "expires_at": time.time() + 300, "stores": [STORE]},
                                   {"username": "owner", "client_id": "tenant-a", "machine_id": "machine-a"},
                                   {"vendas": True}, configuration=load_remote_auth_configuration(ENV), transport=transport)
    return value, transport


def test_bootstrap_extension_is_signed_and_tampering_is_rejected():
    payload = success_payload()
    payload["central"] = {"protocol": 1, "sync_mode": "manual", "session": "s" * 200,
                          "expires_at": NOW + 28800, "stores": [STORE]}
    claims = token_claims(payload)
    claims["jk_response_hash"] = remote_auth_client._canonical_hash({
        key: payload[key] for key in ("user_data", "permissions", "policy", "request_nonce", "central")})
    config = load_remote_auth_configuration(ENV)

    def verify(value):
        validated = validate_success_payload(value, "usuario", "pc:maquina-1", NONCE, "authenticated")
        verify_signed_response(validated, config, requests.Session(), verifier=lambda *a, **k: claims, now=NOW)

    verify(payload)
    changed = copy.deepcopy(payload)
    changed["central"]["stores"][0]["nome"] = "Tampered"
    with pytest.raises(ValueError, match="invalid_response_claim"):
        verify(changed)
    changed = copy.deepcopy(payload)
    changed["central"]["stores"][0]["integracoes"]["mercadolivre"]["access_token"] = "must-not-pass"
    with pytest.raises(ValueError):
        verify(changed)


def test_minimal_login_never_calls_local_firebase_or_exports_private_session(monkeypatch):
    from backend.schemas import LoginRequest, LoginResponse
    from backend.services import admin_usuarios_auth as auth
    configure_common(monkeypatch)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    attempt = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": "owner", "client_id": "tenant-a", "machine_id": "machine-a"},
        permissions={"vendas": True}, policy={"active": True, "max_machines": 2},
        central={"protocol": 1, "sync_mode": "manual", "session": "private-central-session",
                 "expires_at": int(time.time()) + 300, "stores": [STORE]})
    saved = []
    monkeypatch.setattr(auth, "attempt_remote_login", lambda **_: attempt)
    monkeypatch.setattr(auth, "_salvar_usuarios_sql", lambda *a, **k: saved.append(a), raising=False)
    monkeypatch.setattr(auth, "_montar_resposta_login_sucesso", lambda *a: LoginResponse(
        success=True, message="ok", user_data={"username": "owner"}, access_token="local-jwt"), raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("must not use local Firebase, audit or presence")

    monkeypatch.setattr(auth, "_registrar_login_maquina", forbidden)
    monkeypatch.setattr(auth, "_machine_presence_save", forbidden)
    monkeypatch.setattr(auth, "_carregar_usuario_login", forbidden, raising=False)
    result = asyncio.run(auth.login_endpoint(LoginRequest(username="owner", password="password",
        machine_id="machine-a", app_version="1.0.133"), request()))
    assert result.success
    assert "private-central-session" not in result.model_dump_json()
    assert "private-central-session" not in repr(saved)
    assert result.user_data["central"]["sync_mode"] == "manual"
    assert desktop._sessions[desktop.session_key("local-jwt")].public_stores() == [STORE]


def test_provider_transport_is_central_only_and_does_not_sync_on_cache_read():
    value, transport = client()
    desktop._current.set(value)
    assert value.public_stores() == [STORE]
    marker = value.stores()[0]["integracoes"]["mercadolivre"]["access_token"]
    assert transport.calls == []
    response = desktop.provider_request(marker, "GET", "https://api.mercadolibre.com/orders/search",
                                        params={"seller": "123"})
    assert response.json() == {"ok": True}
    method, url, kwargs = transport.calls[0]
    assert url.startswith("https://auth.example.test/api/central/v1/stores/")
    assert kwargs["json"]["path"] == "/orders/search"
    assert "opaque-session" not in json.dumps(value.public_stores())
    assert marker not in json.dumps(value.public_stores())


def test_foreign_session_cannot_reuse_another_store_reference():
    first, _ = client()
    second, _ = client()
    marker = first.stores()[0]["integracoes"]["mercadolivre"]["access_token"]
    desktop._current.set(second)
    with pytest.raises(HTTPException) as error:
        desktop.provider_request(marker, "GET", "https://api.mercadolibre.com/items")
    assert error.value.status_code == 403


def test_expired_or_restarted_session_cannot_fall_back_to_local_credentials():
    with pytest.raises(HTTPException) as error:
        desktop.bind_request("lost-on-restart", {"jk_central": 1, "sub": "owner", "client_id": "tenant-a"})
    assert error.value.status_code == 401
    value, transport = client()
    value.expires_at = time.time() - 1
    with pytest.raises(HTTPException):
        value.call("GET", "/bootstrap")
    assert transport.calls == []


def test_job_keeps_only_initiating_session():
    first, _ = client()
    second, _ = client()
    desktop._current.set(first)
    results = []
    job = desktop.with_request_context(lambda: results.append(desktop.current()))
    desktop._current.set(second)
    thread = threading.Thread(target=job)
    thread.start()
    thread.join(3)
    assert results == [first]
    assert desktop.current() is second


@pytest.mark.parametrize("path", ["/api/shared-sync/auto-pull", "/api/shared-sync/machine-sync/auto",
                                   "/api/shared-sync/user-shares/auto-push", "/api/ia/secrets/provisionar"])
def test_automatic_endpoints_fail_before_any_network_access(path):
    value, transport = client()
    desktop._current.set(value)
    with pytest.raises(HTTPException) as error:
        desktop.assert_manual_path(path)
    assert error.value.status_code == 409 and transport.calls == []


def test_mutation_timeout_cannot_be_treated_as_retryable_provider_error():
    value, transport = client()
    transport.error = requests.Timeout("opaque private request")
    desktop._current.set(value)
    marker = value.stores()[0]["integracoes"]["mercadolivre"]["access_token"]
    with pytest.raises(HTTPException) as error:
        desktop.provider_request(marker, "POST", "https://api.mercadolibre.com/items", json={"title": "Item"})
    assert error.value.status_code == 409
    assert "opaque private" not in str(error.value.detail)
    assert len(transport.calls) == 1


def test_existing_store_ml_and_bling_entrypoints_use_central_without_secrets(monkeypatch):
    from backend.services import integracoes, mercadolivre_http, bling, cadastro_catalogo_bling
    from backend.services import mercadolivre_legacy_api as ml
    value, transport = client()
    value._stores[0]["integracoes"]["bling"] = {"connected": True, "central": True, "user_id": "company-id", "site_id": ""}
    desktop._current.set(value)
    monkeypatch.setattr(ml, "buscar_loja", integracoes.buscar_loja, raising=False)
    cfg = ml._obter_cfg_ml("tenant-a", "Shop")
    assert ml._ml_oauth_status(cfg)["conectado"]
    assert ml._ml_oauth_config_completa(cfg)
    assert mercadolivre_http._ml_http_request("tenant-a", "Shop", cfg["access_token"], "GET",
                                             "https://api.mercadolibre.com/items").json() == {"ok": True}
    bling_cfg = integracoes.buscar_loja("tenant-a", "Shop")["integracoes"]["bling"]
    assert cadastro_catalogo_bling._preparar_credencial_coleta("tenant-a", "a" * 32, "Shop", bling_cfg) == bling_cfg
    response = bling.BLING_SESSION.get("https://api.bling.com.br/Api/v3/produtos",
                                       headers={"Authorization": "Bearer " + bling_cfg["access_token"]})
    assert response.json() == {"ok": True}
    assert len(transport.calls) == 2
    assert all(call[1].startswith("https://auth.example.test/") for call in transport.calls)
    with pytest.raises(HTTPException) as error:
        integracoes.salvar_lojas("tenant-a", value.stores())
    assert error.value.status_code == 409
