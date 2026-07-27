from __future__ import annotations

import logging
import ssl

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


class _SslContext:
    def __init__(self, verify_flags: int):
        self.verify_flags = verify_flags
        self.verify_mode = ssl.CERT_REQUIRED
        self.check_hostname = True


def test_ml_http_python314_windows_compat_removes_only_x509_strict(monkeypatch):
    strict_flag = int(getattr(ssl, "VERIFY_X509_STRICT", 32) or 32)
    other_flag = 4
    context = _SslContext(strict_flag | other_flag)
    calls = []

    monkeypatch.setattr(mercadolivre_http.os, "name", "nt")
    monkeypatch.setattr(mercadolivre_http.ssl, "VERIFY_X509_STRICT", strict_flag, raising=False)
    monkeypatch.setattr(
        mercadolivre_http.ssl,
        "create_default_context",
        lambda *, cafile=None: calls.append(cafile) or context,
    )

    result = mercadolivre_http._ml_http_windows_compatible_ssl_context("C:/empresa/ca.pem")

    assert result is context
    assert calls == ["C:/empresa/ca.pem"]
    assert context.verify_flags & strict_flag == 0
    assert context.verify_flags & other_flag == other_flag
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_ml_http_windows_compat_is_not_applied_outside_windows(monkeypatch):
    monkeypatch.setattr(mercadolivre_http.os, "name", "posix")
    monkeypatch.setattr(
        mercadolivre_http.ssl,
        "create_default_context",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("context should not be created")),
    )

    assert mercadolivre_http._ml_http_windows_compatible_ssl_context("/empresa/ca.pem") is None


def test_ml_http_windows_compat_refuses_context_without_certificate_or_hostname_validation(monkeypatch):
    context = _SslContext(0)
    context.verify_mode = ssl.CERT_NONE
    context.check_hostname = False
    monkeypatch.setattr(mercadolivre_http.os, "name", "nt")
    monkeypatch.setattr(mercadolivre_http.ssl, "VERIFY_X509_STRICT", 32, raising=False)
    monkeypatch.setattr(mercadolivre_http.ssl, "create_default_context", lambda **_kwargs: context)

    try:
        mercadolivre_http._ml_http_windows_compatible_ssl_context("C:/empresa/ca.pem")
    except RuntimeError as exc:
        assert "verify certificates and hostnames" in str(exc)
    else:
        raise AssertionError("insecure SSL context was accepted")


def test_ml_http_session_mounts_compatible_context_only_for_https(monkeypatch):
    context = _SslContext(0)
    monkeypatch.setattr(
        mercadolivre_http,
        "_ml_http_windows_compatible_ssl_context",
        lambda _verify: context,
    )

    session = mercadolivre_http._ml_http_criar_session("C:/empresa/ca.pem")

    assert session.verify == "C:/empresa/ca.pem"
    assert isinstance(session.adapters["https://"], mercadolivre_http._MercadoLivreHttpsAdapter)
    assert session.adapters["https://"]._ml_ssl_context is context
    assert type(session.adapters["http://"]) is mercadolivre_http.HTTPAdapter


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
