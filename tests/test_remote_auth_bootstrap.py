"""Python/BAT bootstrap and the real login/capability chain, with fixtures only."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from google.auth.crypt import RSASigner

from backend.schemas import LoginRequest, LoginResponse
from backend.services import admin_usuarios_auth as auth, cofre_auth, remote_auth_client
from backend.services.remote_auth_bootstrap import MAX_CONFIG_BYTES, public_remote_auth_defaults
from backend.services.remote_auth_contracts import load_remote_auth_configuration
from backend.services.remote_auth_verification import canonical_hash, expected_uid
from cloud.auth_gateway.app.cofre_capability import CofreCapabilityIssuer


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = {"url": "https://fixture.test/api/auth/v1/login", "projectId": "fixture-project", "mode": "prefer"}
KEYS = ("JK_REMOTE_AUTH_URL", "JK_REMOTE_AUTH_PROJECT_ID", "JK_REMOTE_AUTH_MODE")


def config(base, value=None, *, fallback=False):
    path = base / "client-config.json" if fallback else base / "electron_app" / "client-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"remoteAuth": PUBLIC} if value is None else value), encoding="utf-8-sig")
    return path


def bootstrap_node():
    tree = ast.parse((ROOT / "backend_api.py").read_text(encoding="utf-8-sig"))
    calls = [node for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)]
    target = next(node for node in calls if isinstance(node.value.func, ast.Attribute)
                  and node.value.func.attr == "update" and any(isinstance(child, ast.Name)
                  and child.id == "public_remote_auth_defaults" for child in ast.walk(node)))
    dotenv = next(node for node in calls if isinstance(node.value.func, ast.Name) and node.value.func.id == "load_dotenv")
    runtime = next(node for node in calls if isinstance(node.value.func, ast.Attribute)
                   and node.value.func.attr == "configure_admin_usuarios_runtime")
    assert dotenv.lineno < target.lineno < runtime.lineno
    return target


def execute_bootstrap(base, environment):
    # Execute the actual startup statement without backend_api's DB/log/startup side effects.
    namespace = {"BASE_DIR": str(base), "os": SimpleNamespace(environ=environment),
                 "public_remote_auth_defaults": public_remote_auth_defaults}
    exec(compile(ast.Module(body=[bootstrap_node()], type_ignores=[]), "backend_api.py:public-bootstrap", "exec"), namespace)


@pytest.mark.parametrize("fallback", [False, True])
def test_known_code_roots_supply_only_public_defaults_without_mutating_input(tmp_path, fallback):
    path = config(tmp_path, {"remoteAuth": {**PUBLIC, "ignored": "not-an-environment-variable"},
                             "serverUrl": "https://unrelated.test"}, fallback=fallback)
    raw = path.read_bytes()
    environment = {"UNRELATED": "preserved"}
    result = public_remote_auth_defaults(tmp_path, environment)
    assert result == dict(zip(KEYS, PUBLIC.values()))
    assert environment == {"UNRELATED": "preserved"} and path.read_bytes() == raw
    environment.update(result)
    assert load_remote_auth_configuration(environment).project_id == PUBLIC["projectId"]


@pytest.mark.parametrize("url", ["https://explicit.test/api/auth/v1/login", "", "http://invalid.test/login"])
def test_explicit_url_including_disable_or_invalid_value_preserves_existing_behavior(tmp_path, url):
    path = config(tmp_path)
    path.write_text("invalid JSON", encoding="utf-8")
    environment = {"JK_REMOTE_AUTH_URL": url, "JK_REMOTE_AUTH_PROJECT_ID": "explicit-project", "JK_REMOTE_AUTH_MODE": "required"}
    assert public_remote_auth_defaults(tmp_path, environment) == {}
    if url.startswith("http:"):
        with pytest.raises(ValueError):
            load_remote_auth_configuration(environment)


def test_dotenv_values_and_explicit_partial_configuration_take_precedence(tmp_path):
    config(tmp_path)
    environment = {"JK_REMOTE_AUTH_PROJECT_ID": "explicit-project", "JK_REMOTE_AUTH_MODE": "required"}
    execute_bootstrap(tmp_path, environment)
    assert environment == {KEYS[0]: PUBLIC["url"], KEYS[1]: "explicit-project", KEYS[2]: "required"}
    # A URL populated by load_dotenv before this statement is equally authoritative.
    environment[KEYS[0]] = "https://dotenv.test/api/auth/v1/login"
    execute_bootstrap(tmp_path, environment)
    assert environment[KEYS[0]].startswith("https://dotenv.test/")


def test_missing_or_legacy_config_does_not_discover_cwd_or_info(tmp_path, monkeypatch):
    known = tmp_path / "known"
    known.mkdir()
    other = tmp_path / "other"
    config(other)
    config(known / "info")
    monkeypatch.chdir(other)
    assert public_remote_auth_defaults(known, {}) == {}
    config(known, {"serverUrl": "https://legacy.test"})
    assert public_remote_auth_defaults(known, {}) == {}


@pytest.mark.parametrize("remote", [None, [], {}, {**PUBLIC, "url": ""}, {**PUBLIC, "url": "http://bad.test/login"},
                                   {**PUBLIC, "url": "https://fixture.test/other"}, {**PUBLIC, "projectId": "bad"},
                                   {**PUBLIC, "mode": "unknown"}, {**PUBLIC, "url": 42}])
def test_invalid_present_config_fails_closed_without_partial_environment(tmp_path, remote):
    config(tmp_path, {"remoteAuth": remote})
    environment = {"UNCHANGED": "fixture"}
    with pytest.raises(ValueError, match="^invalid_public_remote_auth_configuration$"):
        execute_bootstrap(tmp_path, environment)
    assert environment == {"UNCHANGED": "fixture"}


@pytest.mark.parametrize("raw", ["{invalid", "[]", "x" * (MAX_CONFIG_BYTES + 1)],
                         ids=["malformed-json", "non-object", "oversized"])
def test_malformed_or_oversized_config_is_rejected(tmp_path, raw):
    path = config(tmp_path)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="^invalid_public_remote_auth_configuration$"):
        public_remote_auth_defaults(tmp_path, {})


def test_source_config_has_priority_over_payload_fallback(tmp_path):
    config(tmp_path)
    config(tmp_path, {"remoteAuth": {**PUBLIC, "url": "https://fallback.test/api/auth/v1/login"}}, fallback=True)
    assert public_remote_auth_defaults(tmp_path, {})[KEYS[0]] == PUBLIC["url"]


def test_config_link_is_not_followed(tmp_path):
    target = config(tmp_path / "outside")
    base = tmp_path / "known"
    (base / "electron_app").mkdir(parents=True)
    try:
        (base / "electron_app" / "client-config.json").symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable in this Windows environment")
    with pytest.raises(ValueError):
        public_remote_auth_defaults(base, {})


def test_python_bat_bootstrap_negotiates_real_capability_and_binds_login_without_firebase_io(tmp_path, monkeypatch):
    config(tmp_path)
    environment = {}
    execute_bootstrap(tmp_path, environment)
    now = int(time.time())
    issuer = "fixture-signer@fixture-project.iam.gserviceaccount.com"
    identity = {"username": "fixture-user", "client_id": "fixture-company", "machine_id": "pc:fixture", "name": "Fixture", "email": ""}
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    signer = CofreCapabilityIssuer(RSASigner.from_string(pem), issuer, clock=lambda: now)
    permissions = {key: key == "cofre" for key in remote_auth_client.PERMISSION_KEYS}
    calls = []

    class Transport:
        def close(self):
            pass

        def post(self, url, **kwargs):
            calls.append((url, kwargs["headers"]))
            assert kwargs["headers"]["X-JK-Cofre-Protocol"] == "1"
            body = kwargs["json"]
            capability = signer.issue({**identity, "permissions": permissions}, identity["username"], identity["machine_id"])
            payload = {"user_data": identity, "permissions": permissions,
                       "policy": {"active": True, "valid_until": None, "max_machines": 1},
                       "request_nonce": body["request_nonce"], "cofre_access": capability}
            token = jwt.encode({"iss": "https://securetoken.google.com/fixture-project", "aud": "fixture-project",
                "sub": expected_uid(identity["username"]), "auth_time": now, "firebase": {"sign_in_provider": "custom"},
                "jk_auth_v": 1, "jk_username": identity["username"], "jk_client_id": identity["client_id"],
                "jk_machine_hash": hashlib.sha256(identity["machine_id"].encode()).hexdigest(),
                "jk_response_hash": canonical_hash(payload)}, pem, algorithm="RS256")
            result = {"success": True, "code": "authenticated", **payload, "identity_token": token, "expires_in": 3600}
            return SimpleNamespace(status_code=200, content=json.dumps(result).encode(), json=lambda: copy.deepcopy(result))

    def verify(value, _request, **kwargs):
        return jwt.decode(value, private.public_key(), algorithms=["RS256"], audience=kwargs["audience"])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Bootstrap/login must not access Firebase, local profiles or the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", issuer)
    monkeypatch.setattr(cofre_auth, "_SESSIONS", {})
    monkeypatch.setattr(cofre_auth, "_STORE", SimpleNamespace(load=lambda *_: None,
        save=lambda *_: None, delete=lambda *_: None, revoke_identity=lambda *_: None))
    monkeypatch.setattr(cofre_auth, "_KEYS", {issuer: (now + 3600, {"fixture-key": private.public_key()})})
    monkeypatch.setattr(auth, "attempt_remote_login", lambda **kwargs: remote_auth_client.attempt_remote_login(
        **kwargs, environ=environment, session=Transport(), verifier=verify, now=now))
    for name, value in {
        "_validar_versao_minima_app_ou_426": lambda value: value,
        "_montar_machine_id_login": lambda _request, machine: (machine, {}),
        "_carregar_usuario_login": forbidden,
        "_salvar_perfil_autenticado_remoto": lambda *_args: dict(identity),
        "_registrar_login_maquina": lambda *_args: None,
        "_machine_presence_record": lambda *_args: {},
        "_machine_presence_save": lambda *_args: None,
        "_montar_resposta_login_sucesso": lambda _username, _user, permissions, _client, _machine: LoginResponse(
            success=True, message="fixture", user_data=dict(identity), permissions=permissions, access_token="fixture-local-handle"),
    }.items():
        monkeypatch.setattr(auth, name, value, raising=False)
    # login_endpoint has a synchronous body; drive it without creating Windows loopback event-loop sockets.
    coroutine = auth.login_endpoint(LoginRequest(username=identity["username"], password="fixture-password",
                                   machine_id=identity["machine_id"], app_version="1.0.268"), request=None)
    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    response = completed.value.value
    assert response.success and response.permissions["cofre"]
    assert len(calls) == 1 and calls[0][0] == PUBLIC["url"]
    assert "capability" not in response.model_dump_json()
    for _ in range(3):
        request = Request({"type": "http"})
        assert cofre_auth.bind_request(request, "Bearer " + response.access_token) == identity["client_id"]
    assert len(calls) == 1  # Cofre actions consult only the already verified RAM capability.
