from __future__ import annotations

import logging

from backend.services import integracoes, mercadolivre_http


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {"x-private": "must-not-be-logged"}

    def json(self):
        return dict(self._payload)


class _Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Response(200)


def test_ml_http_never_disables_tls_and_accepts_configured_ca(monkeypatch):
    session = _Session()
    monkeypatch.setenv("ML_CA_BUNDLE", "C:/empresa/ca-confiavel.pem")
    monkeypatch.setattr(mercadolivre_http, "_ml_http_obter_session", lambda *_args: session)

    response = mercadolivre_http._ml_http_request(
        "tenant-a",
        "Loja A",
        "token-secreto",
        "GET",
        "https://api.mercadolibre.com/users/me",
        verify_ssl=False,
    )

    assert response.status_code == 200
    assert session.calls[0][2]["verify"] == "C:/empresa/ca-confiavel.pem"


def test_oauth_exchange_logs_no_code_token_headers_or_body(monkeypatch, caplog):
    authorization_code = "authorization-code-super-secreto"
    access_token = "APP_USR-token-super-secreto"
    refresh_token = "TG-refresh-super-secreto"
    monkeypatch.setattr(
        integracoes.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            200,
            {"access_token": access_token, "refresh_token": refresh_token, "user_id": 12345},
        ),
    )

    with caplog.at_level(logging.INFO):
        success, payload = integracoes.auth_ml_exchange(
            "app-id",
            "client-secret-super-secreto",
            authorization_code,
            "https://app.example/callback",
        )

    rendered = caplog.text
    assert success is True
    assert payload["access_token"] == access_token
    for secret in (authorization_code, access_token, refresh_token, "client-secret-super-secreto", "must-not-be-logged"):
        assert secret not in rendered
