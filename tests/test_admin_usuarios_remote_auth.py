from __future__ import annotations

import asyncio

import bcrypt
from fastapi import Request

from backend.schemas import LoginRequest, LoginResponse, UserChangePasswordRequest
from backend.services import admin_usuarios_auth as auth
from backend.services.remote_auth_client import RemoteAuthAttempt, RemoteAuthState


def request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/login",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8001),
            "scheme": "http",
        }
    )


def configure_common(monkeypatch):
    monkeypatch.setattr(auth, "bcrypt", bcrypt, raising=False)
    monkeypatch.setattr(auth, "_validar_versao_minima_app_ou_426", lambda _value: "1.0.124", raising=False)
    monkeypatch.setattr(
        auth,
        "_montar_machine_id_login",
        lambda _request, machine_id: (str(machine_id).strip(), {}),
        raising=False,
    )
    monkeypatch.setattr(auth, "_registrar_login_maquina", lambda *_args: None, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_record", lambda *_args: {}, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_save", lambda *_args: None, raising=False)


def test_clean_install_uses_remote_auth_without_loading_local_users(monkeypatch):
    configure_common(monkeypatch)
    calls = {}
    remote = RemoteAuthAttempt(
        RemoteAuthState.SUCCESS,
        user_data={
            "username": "usuario",
            "name": "Usuario",
            "email": "usuario@example.test",
            "client_id": "cliente-central",
            "machine_id": "pc:novo",
        },
        permissions={"vendas": True},
        policy={"active": True, "valid_until": "31/12/2030", "max_machines": 2},
    )
    def remote_login(**kwargs):
        calls["remote"] = kwargs
        return remote

    monkeypatch.setattr(auth, "attempt_remote_login", remote_login)
    monkeypatch.setattr(
        auth,
        "carregar_usuarios_sheets",
        lambda: (_ for _ in ()).throw(AssertionError("clean install must not load local users")),
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "_salvar_usuarios_sql",
        lambda users, source: calls.setdefault("cache", (users, source)),
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "_montar_resposta_login_sucesso",
        lambda username, user, permissions, client_id, machine_id: LoginResponse(
            success=True,
            message="ok",
            user_data={"username": username, "client_id": client_id, "machine_id": machine_id},
            permissions=permissions,
            access_token="local-token",
        ),
        raising=False,
    )

    response = asyncio.run(
        auth.login_endpoint(
            LoginRequest(
                username="usuario",
                password="senha correta",
                client_id="cliente-injetado",
                machine_id="pc:novo",
                app_version="1.0.124",
            ),
            request(),
        )
    )

    assert response.success is True
    assert calls["remote"]["username"] == "usuario"
    assert "client_id" not in calls["remote"]
    cached_user = calls["cache"][0]["usuario"]
    assert cached_user["client_id"] == "cliente-central"
    assert cached_user["source"] == "remote-auth-profile"
    assert cached_user["password"].startswith("$2")
    assert not bcrypt.checkpw(b"senha correta", cached_user["password"].encode("utf-8"))


def test_remote_rejection_never_falls_back_to_stale_local_credentials(monkeypatch):
    configure_common(monkeypatch)
    monkeypatch.setattr(
        auth,
        "attempt_remote_login",
        lambda **_kwargs: RemoteAuthAttempt(
            RemoteAuthState.REJECTED,
            "Usuario inativo. Contate o administrador.",
        ),
    )
    monkeypatch.setattr(
        auth,
        "carregar_usuarios_sheets",
        lambda: (_ for _ in ()).throw(AssertionError("rejected remote login must be authoritative")),
        raising=False,
    )

    response = asyncio.run(
        auth.login_endpoint(
            LoginRequest(username="usuario", password="senha", machine_id="pc:novo", app_version="1.0.124"),
            request(),
        )
    )

    assert response.success is False
    assert "inativo" in response.message.lower()


def test_clean_install_reports_remote_outage_instead_of_missing_users(monkeypatch):
    configure_common(monkeypatch)
    outage = "Nao foi possivel conectar ao servico de autenticacao. Verifique a internet e tente novamente."
    monkeypatch.setattr(
        auth,
        "attempt_remote_login",
        lambda **_kwargs: RemoteAuthAttempt(
            RemoteAuthState.UNAVAILABLE,
            outage,
            allow_legacy_fallback=True,
        ),
    )
    monkeypatch.setattr(auth, "_carregar_usuario_login", lambda _username: (None, None, []), raising=False)

    response = asyncio.run(
        auth.login_endpoint(
            LoginRequest(username="usuario", password="senha", machine_id="pc:novo", app_version="1.0.124"),
            request(),
        )
    )

    assert response.success is False
    assert response.message == outage


def test_remote_profile_password_change_updates_central_service(monkeypatch):
    configure_common(monkeypatch)
    monkeypatch.setattr(
        auth,
        "_payload_sessao_por_authorization",
        lambda _authorization: {
            "username": "usuario",
            "client_id": "cliente-central",
            "machine_id": "pc:novo",
        },
        raising=False,
    )
    monkeypatch.setattr(
        auth,
        "_obter_usuario_sql",
        lambda _username: {
            "username": "usuario",
            "client_id": "cliente-central",
            "source": "remote-auth-profile",
        },
        raising=False,
    )
    calls = {}
    result = RemoteAuthAttempt(
        RemoteAuthState.SUCCESS,
        user_data={
            "username": "usuario",
            "name": "Usuario",
            "email": "usuario@example.test",
            "client_id": "cliente-central",
            "machine_id": "pc:novo",
        },
        permissions={"vendas": True},
        policy={"active": True, "valid_until": "31/12/2030", "max_machines": 2},
    )

    def change_password(**kwargs):
        calls["change"] = kwargs
        return result

    monkeypatch.setattr(auth, "attempt_remote_password_change", change_password)
    monkeypatch.setattr(
        auth,
        "_salvar_perfil_autenticado_remoto",
        lambda username, attempt: calls.setdefault("cache", (username, attempt)),
    )
    monkeypatch.setenv("JK_APP_VERSION", "1.0.124")

    response = auth.trocar_minha_senha(
        UserChangePasswordRequest(
            current_password="senha atual",
            new_password="senha nova segura",
            confirm_password="senha nova segura",
        ),
        authorization="Bearer local-token",
    )

    assert response["success"] is True
    assert calls["change"] == {
        "username": "usuario",
        "current_password": "senha atual",
        "new_password": "senha nova segura",
        "machine_id": "pc:novo",
        "app_version": "1.0.124",
    }
    assert calls["cache"] == ("usuario", result)
