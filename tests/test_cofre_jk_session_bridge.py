"""JK-session bridge and normal Google-login delegation; fictional data only."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from typing import Optional

import pytest
from fastapi import Header, HTTPException, Request
from backend.schemas import LoginResponse
from backend.services import admin_usuarios_auth as auth
from backend.services import admin_usuarios_login_core as login_core
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState
from cloud.auth_gateway.app.config import GatewayConfigurationError, GatewaySettings
from cloud.auth_gateway.app.firebase_adapter import FirestoreUserRepository


ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"sub": "example", "client_id": "example-company", "machine_id": "example-pc"}


def bridge(profile=None, payload=None, *, missing_profile=False):
    source = ast.parse((ROOT / "backend_api.py").read_text(encoding="utf-8-sig"))
    node = next(item for item in source.body if isinstance(item, ast.FunctionDef)
                and item.name == "_cofre_verified_jk_session")
    record = {"client_id": "example-company", "permissions": {"cofre": True},
              "active": True, "valid_until": None, "machine_ids": ["example-pc"], **(profile or {})}
    scope = {"HTTPException": HTTPException, "decodificar_access_token": lambda value: payload or dict(IDENTITY),
             "_obter_politica_local_cofre_sql": lambda username: {} if missing_profile else record,
             "_login_usuario_ativo": lambda user: user.get("active") is True,
             "_login_validade_ok": lambda user: (user.get("valid_until") != "expired", None),
             "_normalizar_permissoes": lambda value: dict(value)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "isolated-bridge", "exec"), scope)
    return scope["_cofre_verified_jk_session"]


def test_bridge_uses_existing_verified_session_and_local_policy_only():
    identity = bridge()("signed-jk-session")
    assert identity["sub"] == "example"
    assert identity["client_id"] == "example-company"
    assert identity["machine_id"] == "example-pc"
    assert identity["permissions"] == {"cofre": True}


def test_bridge_resolves_canonical_lookup_from_actual_backend_imports(monkeypatch):
    from backend.services import admin_usuarios_store
    lookup = Mock(return_value={"client_id": "example-company", "permissions": {"cofre": True},
        "active": True, "valid_until": None, "machine_ids": ["example-pc"]})
    monkeypatch.setattr(admin_usuarios_store, "_obter_politica_local_cofre_sql", lookup)
    function = bridge()
    scope = function.__globals__
    del scope["_obter_politica_local_cofre_sql"]
    tree = ast.parse((ROOT / "backend_api.py").read_text(encoding="utf-8-sig"))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "backend.services.admin_usuarios_store"]
    exec(compile(ast.Module(body=imports, type_ignores=[]), "backend-store-imports", "exec"), scope)
    assert function("signed-jk-session")["client_id"] == "example-company"
    lookup.assert_called_once_with("example")


@pytest.mark.parametrize("profile", [
    {"client_id": "other-company"}, {"permissions": {"cofre": False}},
    {"active": False}, {"valid_until": "expired"}, {"machine_ids": []},
])
def test_cached_policy_can_only_deny_or_narrow_access(profile):
    with pytest.raises(HTTPException) as caught:
        bridge(profile)("signed-jk-session")
    assert caught.value.status_code == 403


def test_bridge_requires_complete_authenticated_identity():
    with pytest.raises(HTTPException) as caught:
        bridge(payload={"sub": "example", "client_id": "example-company"})("signed-jk-session")
    assert caught.value.status_code == 401
    assert caught.value.headers == {"WWW-Authenticate": "Bearer"}


def test_valid_jk_session_with_missing_profile_is_recoverable_without_logout():
    with pytest.raises(HTTPException) as caught:
        bridge(missing_profile=True)("signed-jk-session")
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "cofre_session_unavailable"


def test_verified_google_login_delegates_before_legacy_directory_lookup(monkeypatch):
    monkeypatch.setattr(login_core, "_validar_versao_minima_app_ou_426", lambda value: value, raising=False)
    monkeypatch.setattr(login_core, "_normalizar_email", lambda value: str(value or "").lower(), raising=False)
    delegate = Mock(return_value=LoginResponse(success=True, message="ok", user_data={"username": "example"}, access_token="jk-session"))
    monkeypatch.setattr(auth, "_autenticar_google_remoto", delegate)
    monkeypatch.setattr(login_core, "carregar_usuarios_sheets",
                        lambda: pytest.fail("Remote Google sign-in replaces the legacy Firestore directory read."), raising=False)
    response = login_core._autenticar_usuario_por_google_info(
        {"iss": "https://accounts.google.com", "email": "example@example.test", "email_verified": True},
        "example-pc", None, "1.0.272", credential="private-google-proof")
    assert response.success is True
    assert delegate.call_args.args[1] == "private-google-proof"
    assert "private-google-proof" not in response.model_dump_json()


def test_unverified_google_identity_is_rejected_before_delegation(monkeypatch):
    monkeypatch.setattr(login_core, "_validar_versao_minima_app_ou_426", lambda value: value, raising=False)
    monkeypatch.setattr(login_core, "_normalizar_email", lambda value: str(value or "").lower(), raising=False)
    delegate = Mock(side_effect=AssertionError("An unverified Google account never reaches session setup."))
    monkeypatch.setattr(auth, "_autenticar_google_remoto", delegate)
    response = login_core._autenticar_usuario_por_google_info(
        {"iss": "https://accounts.google.com", "email": "example@example.test", "email_verified": False},
        "example-pc", None, "1.0.272", credential="private-google-proof")
    assert response.success is False
    delegate.assert_not_called()


def test_remote_google_response_must_match_verified_email(monkeypatch):
    monkeypatch.setattr(auth, "_montar_machine_id_login", lambda request, machine: (machine, {}), raising=False)
    remote = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": "other", "email": "other@example.test", "client_id": "other-company", "machine_id": "example-pc"})
    monkeypatch.setattr(auth, "attempt_remote_google_login", lambda **kwargs: remote)
    complete = Mock(side_effect=AssertionError("Mismatched identities must not be registered."))
    monkeypatch.setattr(auth, "_complete_remote_login", complete)
    response = auth._autenticar_google_remoto({"email": "example@example.test"}, "private-proof",
                                             "example-pc", None, "1.0.272")
    assert response.success is False
    complete.assert_not_called()


def test_remote_google_binding_is_independent_of_contact_email(monkeypatch):
    monkeypatch.setattr(auth, "_montar_machine_id_login", lambda request, machine: (machine, {}), raising=False)
    remote = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": "example", "email": "contact@example.test", "google_email": "login@example.test",
                   "client_id": "example-company", "machine_id": "example-pc"})
    monkeypatch.setattr(auth, "attempt_remote_google_login", lambda **kwargs: remote)
    complete = Mock(return_value=LoginResponse(success=True, message="ok"))
    monkeypatch.setattr(auth, "_complete_remote_login", complete)
    response = auth._autenticar_google_remoto({"email": "login@example.test"}, "private-fixture-proof",
                                             "example-pc", None, "1.0.272")
    assert response.success is True
    complete.assert_called_once_with(remote, None, "1.0.272")


class Snapshot:
    def __init__(self, username, **fields):
        self.id = username
        self.record = {"username": username, **fields}
    def to_dict(self):
        return dict(self.record)


def google_repository(snapshots):
    repository = object.__new__(FirestoreUserRepository)
    repository._settings = SimpleNamespace(firestore_timeout_seconds=5)
    repository._collection = Mock()
    repository._collection.stream.return_value = iter(snapshots)
    return repository


def test_google_directory_lookup_is_one_normal_signin_scan():
    repository = google_repository([Snapshot("example", email="Example@example.test"),
                                    Snapshot("other", email="other@example.test")])
    username, record = repository.find_google_user("example@example.test")
    assert username == "example" and record["email"] == "Example@example.test"
    repository._collection.stream.assert_called_once_with(retry=None, timeout=5)


def test_google_lookup_rejects_ambiguous_account_binding():
    repository = google_repository([Snapshot("first", email="example@example.test"),
                                    Snapshot("second", google_email="example@example.test")])
    with pytest.raises(Exception) as caught:
        repository.find_google_user("example@example.test")
    assert getattr(caught.value, "code", None) == "invalid_credentials"


def test_google_gateway_audience_is_public_configuration_and_optional():
    env = {"FIREBASE_PROJECT_ID": "example-project", "FIREBASE_WEB_API_KEY": "public-fixture-key"}
    assert GatewaySettings.from_environment(env).google_oauth_client_id == ""
    assert GatewaySettings.from_environment({**env, "GOOGLE_LOGIN_CLIENT_ID": "123-example.apps.googleusercontent.com"}).google_oauth_client_id
    with pytest.raises(GatewayConfigurationError):
        GatewaySettings.from_environment({**env, "GOOGLE_LOGIN_CLIENT_ID": "https://attacker.test"})


def test_cofre_refresh_secret_is_server_only_and_hidden_from_settings_repr():
    env = {"FIREBASE_PROJECT_ID": "example-project", "FIREBASE_WEB_API_KEY": "public-fixture-key"}
    assert not GatewaySettings.from_environment(env).cofre_session_secret
    current, previous = "c1" * 32, "e2" * 32  # Fictional fixtures, never production keys.
    configured = GatewaySettings.from_environment({**env, "JK_COFRE_SESSION_SECRET": current,
        "JK_COFRE_SESSION_PREVIOUS_SECRET": previous})
    assert configured.cofre_session_secret == current
    assert current not in repr(configured) and previous not in repr(configured)
    for updates in ({"JK_COFRE_SESSION_SECRET": "too-short"},
                    {"JK_COFRE_SESSION_PREVIOUS_SECRET": previous},
                    {"JK_COFRE_SESSION_SECRET": current, "JK_COFRE_SESSION_PREVIOUS_SECRET": "invalid"}):
        with pytest.raises(GatewayConfigurationError):
            GatewaySettings.from_environment({**env, **updates})
