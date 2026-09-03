from __future__ import annotations

import json

import requests

from backend.services import remote_auth_client as client


NOW = 1_800_000_000
NONCE = "n" * 43
ENV = {
    "JK_REMOTE_AUTH_URL": "https://auth.example.test/api/auth/v1/login",
    "JK_REMOTE_AUTH_PROJECT_ID": "jkjkjk-485920",
    "JK_REMOTE_AUTH_MODE": "prefer",
}


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


def success_payload(
    *,
    client_id: str = "cliente-7",
    machine_id: str = "pc:maquina-1",
    request_nonce: str = NONCE,
):
    return {
        "success": True,
        "code": "authenticated",
        "user_data": {
            "username": "usuario",
            "name": "Usuario Teste",
            "email": "usuario@example.test",
            "client_id": client_id,
            "machine_id": machine_id,
        },
        "permissions": {key: key == "vendas" for key in client.PERMISSION_KEYS},
        "policy": {"active": True, "valid_until": "31/12/2030", "max_machines": 2},
        "request_nonce": request_nonce,
        "identity_token": "signed-firebase-id-token",
        "expires_in": 3600,
    }


def token_claims(payload, **overrides):
    signed = {
        "user_data": payload["user_data"],
        "permissions": payload["permissions"],
        "policy": payload["policy"],
        "request_nonce": payload["request_nonce"],
    }
    claims = {
        "iss": "https://securetoken.google.com/jkjkjk-485920",
        "aud": "jkjkjk-485920",
        "sub": client._expected_uid(payload["user_data"]["username"]),
        "auth_time": NOW,
        "firebase": {"sign_in_provider": "custom"},
        "jk_auth_v": client.AUTH_PROTOCOL_VERSION,
        "jk_username": payload["user_data"]["username"],
        "jk_client_id": payload["user_data"]["client_id"],
        "jk_machine_hash": client._machine_hash(payload["user_data"]["machine_id"]),
        "jk_response_hash": client._canonical_hash(signed),
    }
    claims.update(overrides)
    return claims


def test_remote_login_verifies_signed_tenant_profile_and_omits_client_hint():
    payload = success_payload()
    session = FakeSession(FakeResponse(200, payload))
    verifier_calls = []

    def verifier(token, request, **kwargs):
        verifier_calls.append((token, request, kwargs))
        return token_claims(payload)

    result = client.attempt_remote_login(
        username=" Usuario ",
        password="senha correta",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
        verifier=verifier,
        now=NOW,
        request_nonce=NONCE,
    )

    assert result.state is client.RemoteAuthState.SUCCESS
    assert result.user_data["client_id"] == "cliente-7"
    assert result.permissions["vendas"] is True
    assert len(session.calls) == 1
    sent = session.calls[0][1]
    assert sent["json"] == {
        "username": "usuario",
        "password": "senha correta",
        "machine_id": "pc:maquina-1",
        "app_version": "1.0.124",
        "request_nonce": NONCE,
    }
    assert "client_id" not in sent["json"]
    assert sent["allow_redirects"] is False
    assert verifier_calls[0][2]["audience"] == "jkjkjk-485920"


def test_remote_login_rejects_tenant_claim_mismatch_without_legacy_fallback():
    payload = success_payload(client_id="cliente-b")
    session = FakeSession(FakeResponse(200, payload))

    result = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
        verifier=lambda *_args, **_kwargs: token_claims(payload, jk_client_id="cliente-a"),
        now=NOW,
        request_nonce=NONCE,
    )

    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE
    assert result.allow_legacy_fallback is False


def test_remote_login_rejects_permissions_modified_after_signature():
    payload = success_payload()
    claims = token_claims(payload)
    payload["permissions"]["full"] = True
    session = FakeSession(FakeResponse(200, payload))

    result = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
        verifier=lambda *_args, **_kwargs: claims,
        now=NOW,
        request_nonce=NONCE,
    )

    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE


def test_remote_rejection_is_authoritative_and_does_not_allow_local_cache():
    session = FakeSession(
        FakeResponse(
            401,
            {"success": False, "code": "invalid_credentials", "message": "ignored"},
        )
    )

    result = client.attempt_remote_login(
        username="usuario",
        password="errada",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
    )

    assert result.state is client.RemoteAuthState.REJECTED
    assert result.allow_legacy_fallback is False
    assert result.message == "Usuario ou senha invalidos."


def test_remote_outage_allows_legacy_only_in_prefer_mode():
    prefer = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=FakeSession(error=requests.ConnectionError("offline")),
    )
    required = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ={**ENV, "JK_REMOTE_AUTH_MODE": "required"},
        session=FakeSession(error=requests.ConnectionError("offline")),
    )

    assert prefer.state is client.RemoteAuthState.UNAVAILABLE
    assert prefer.allow_legacy_fallback is True
    assert required.state is client.RemoteAuthState.UNAVAILABLE
    assert required.allow_legacy_fallback is False


def test_invalid_or_unsigned_remote_configuration_fails_closed():
    result = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ={**ENV, "JK_REMOTE_AUTH_URL": "http://auth.example.test/login"},
        session=FakeSession(),
    )

    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE
    assert result.allow_legacy_fallback is False


def test_remote_password_change_uses_dedicated_endpoint_and_signed_result():
    payload = success_payload()
    payload["code"] = "password_changed"
    session = FakeSession(FakeResponse(200, payload))

    result = client.attempt_remote_password_change(
        username="usuario",
        current_password="senha atual",
        new_password="senha nova segura",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
        verifier=lambda *_args, **_kwargs: token_claims(payload),
        now=NOW,
        request_nonce=NONCE,
    )

    assert result.state is client.RemoteAuthState.SUCCESS
    url, call = session.calls[0]
    assert url == "https://auth.example.test/api/auth/v1/change-password"
    assert call["json"] == {
        "username": "usuario",
        "current_password": "senha atual",
        "new_password": "senha nova segura",
        "machine_id": "pc:maquina-1",
        "app_version": "1.0.124",
        "request_nonce": NONCE,
    }


def test_remote_login_rejects_replayed_response_with_another_nonce():
    payload = success_payload(request_nonce=NONCE)
    session = FakeSession(FakeResponse(200, payload))

    result = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=session,
        verifier=lambda *_args, **_kwargs: token_claims(payload),
        now=NOW,
        request_nonce="x" * 43,
    )

    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE
    assert result.allow_legacy_fallback is False


def test_remote_login_closes_the_session_it_creates(monkeypatch):
    payload = success_payload()
    owned_session = FakeSession(FakeResponse(200, payload))
    monkeypatch.setattr(
        client,
        "configure_requests_session",
        lambda _session, _values: owned_session,
    )

    result = client.attempt_remote_login(
        username="usuario",
        password="senha",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        verifier=lambda *_args, **_kwargs: token_claims(payload),
        now=NOW,
        request_nonce=NONCE,
    )

    assert result.state is client.RemoteAuthState.SUCCESS
    assert owned_session.closed is True


def test_remote_password_change_never_falls_back_to_local_on_outage():
    result = client.attempt_remote_password_change(
        username="usuario",
        current_password="senha atual",
        new_password="senha nova segura",
        machine_id="pc:maquina-1",
        app_version="1.0.124",
        environ=ENV,
        session=FakeSession(error=requests.ConnectionError("offline")),
    )

    assert result.state is client.RemoteAuthState.UNAVAILABLE
    assert result.allow_legacy_fallback is False
