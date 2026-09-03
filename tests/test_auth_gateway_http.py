from __future__ import annotations

from fastapi.testclient import TestClient

from cloud.auth_gateway.app.config import GatewaySettings
from cloud.auth_gateway.app.domain import AuthRejected
from cloud.auth_gateway.app.main import create_app


SETTINGS = GatewaySettings(
    project_id="jkjkjk-485920",
    firebase_web_api_key="public-test-key",
    users_collection="jk_sistema_usuarios",
    minimum_app_version="1.0.124",
    firestore_timeout_seconds=5,
    identity_timeout_seconds=8,
    rate_limit_attempts=10,
    rate_limit_window_seconds=300,
)
NONCE = "n" * 43


class CapturingService:
    def __init__(self, result=None, error=None):
        self.result = result or {"success": True}
        self.error = error
        self.calls = []

    def authenticate(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.result


def test_gateway_rejects_tenant_injection_and_never_echoes_password():
    service = CapturingService()
    client = TestClient(create_app(service=service, settings=SETTINGS))

    response = client.post(
        "/api/auth/v1/login",
        json={
            "username": "usuario",
            "password": "segredo-que-nao-pode-voltar",
            "machine_id": "pc:novo",
            "app_version": "1.0.124",
            "request_nonce": NONCE,
            "client_id": "cliente-injetado",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert "segredo-que-nao-pode-voltar" not in response.text
    assert service.calls == []
    assert response.headers["cache-control"] == "no-store"


def test_gateway_maps_domain_rejection_to_stable_code():
    service = CapturingService(error=AuthRejected("device_limit", 403))
    client = TestClient(create_app(service=service, settings=SETTINGS))

    response = client.post(
        "/api/auth/v1/login",
        json={
            "username": "usuario",
            "password": "senha",
            "machine_id": "pc:novo",
            "app_version": "1.0.124",
            "request_nonce": NONCE,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "device_limit"
    assert "password" not in response.text.lower()


def test_gateway_rate_limit_is_applied_before_authentication():
    settings = GatewaySettings(**{**SETTINGS.__dict__, "rate_limit_attempts": 2})
    service = CapturingService(error=AuthRejected("invalid_credentials", 401))
    client = TestClient(create_app(service=service, settings=settings))
    body = {
        "username": "usuario",
        "password": "senha",
        "machine_id": "pc:novo",
        "app_version": "1.0.124",
        "request_nonce": NONCE,
    }

    assert client.post("/api/auth/v1/login", json=body).status_code == 401
    assert client.post("/api/auth/v1/login", json=body).status_code == 401
    limited = client.post("/api/auth/v1/login", json=body)

    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert len(service.calls) == 2
