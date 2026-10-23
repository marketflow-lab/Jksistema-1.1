"""Refresh the real JK session with signed fixture Cofre access and no network."""
import ast
import hashlib
import time
from pathlib import Path

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.services import admin_usuarios_auth as auth
from backend.services import admin_usuarios_common as common
from backend.services import admin_usuarios_login_core as login_core
from backend.services import cofre_auth, firebase_user_session
from backend.services.cofre_session_store import CofreSessionStore
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState


USER, COMPANY, MACHINE = "refresh-fixture", "fixture-company", "fixture-machine"
ISSUER = "fixture@fixture-project.iam.gserviceaccount.com"


class Credentials:
    def __init__(self):
        self.values = {}

    def read_scoped_secret(self, target):
        return self.values.get(target, "")

    def write_scoped_secret(self, target, value):
        self.values[target] = value
        return True

    def delete_scoped_secret(self, target):
        self.values.pop(target, None)
        return True


@pytest.fixture
def refresh(monkeypatch, tmp_path):
    source = ast.parse((Path(__file__).resolve().parents[1] / "backend_api.py").read_text(encoding="utf-8-sig"))
    create = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == "criar_access_token")
    runtime = {"jwt": jwt, "Optional": __import__("typing").Optional,
               "JWT_SECRET": "fixture-only-local-signing-key-at-least-32-bytes",
               "JWT_ALGORITHM": "HS256"}
    exec(compile(ast.Module(body=[create], type_ignores=[]), "fixture-jk-token", "exec"), runtime)
    create_token = runtime["criar_access_token"]

    def decode(token):
        return jwt.decode(token, runtime["JWT_SECRET"], algorithms=["HS256"])

    def forbidden(*args, **kwargs):
        pytest.fail("Session transfer must not query Firebase or any network service")

    monkeypatch.setattr(requests.Session, "get", forbidden)
    monkeypatch.setattr(requests.Session, "post", forbidden)
    monkeypatch.setattr(firebase_user_session, "current", lambda *args: None)
    monkeypatch.setattr(common, "decodificar_access_token", decode, raising=False)
    monkeypatch.setattr(auth, "_payload_sessao_por_authorization", common._payload_sessao_por_authorization, raising=False)
    monkeypatch.setattr(auth, "_authenticated_session_machine_id", lambda identity, machine: identity["machine_id"], raising=False)
    monkeypatch.setattr(login_core, "criar_access_token", create_token, raising=False)
    monkeypatch.setattr(login_core, "_normalizar_email", lambda value: str(value or "").lower(), raising=False)
    monkeypatch.setattr(login_core, "_normalizar_permissoes", lambda value: dict(value), raising=False)
    monkeypatch.setattr(auth, "_montar_resposta_login_sucesso", login_core._montar_resposta_login_sucesso, raising=False)
    profile = {"username": USER, "client_id": COMPANY, "machine_id": MACHINE,
               "active": True, "permissions": {"cofre": True}, "valid_until": None,
               "central": True}
    monkeypatch.setattr(auth, "_obter_usuario_sql", lambda username: dict(profile), raising=False)
    monkeypatch.setattr(auth, "_login_usuario_ativo", lambda user: True, raising=False)
    monkeypatch.setattr(auth, "_login_validade_ok", lambda user: (True, ""), raising=False)
    monkeypatch.setattr(auth, "_carregar_permissoes_usuario", lambda *args: {"cofre": True}, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_record", lambda *args: {}, raising=False)
    monkeypatch.setattr(auth, "_machine_presence_save", lambda *args: None, raising=False)
    monkeypatch.setattr(cofre_auth, "_STORE", CofreSessionStore(tmp_path, credentials=Credentials()))
    monkeypatch.setattr(cofre_auth, "_SESSIONS", {})
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", ISSUER)
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    monkeypatch.setattr(cofre_auth, "_KEYS", {ISSUER: (now + 3600, {"fixture": signing.public_key()})})
    capability = jwt.encode({"iss": ISSUER, "aud": "jk-cofre", "sub": USER,
        "client_id": COMPANY, "machine_hash": hashlib.sha256(MACHINE.encode()).hexdigest(),
        "scope": ["cofre:read"], "iat": now, "exp": now + 8 * 3600},
        signing, algorithm="RS256", headers={"kid": "fixture"})
    attempt = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": USER, "client_id": COMPANY, "machine_id": MACHINE},
        permissions={"cofre": True}, policy={"valid_until": None},
        cofre_access={"protocol": 2, "capability": capability,
                      "session_grant": "opaque-fixture-grant", "session_expires_at": now + 30 * 86400})
    identity = {"sub": USER, "client_id": COMPANY, "machine_id": MACHINE,
                "permissions": {"cofre": True}}
    return create_token(USER, COMPANY, MACHINE, central=True), attempt, identity


def test_real_jk_refresh_preserves_cofre_binding_and_markers_after_restart(refresh):
    token, attempt, identity = refresh
    before = cofre_auth.register_login(token, attempt)
    assert cofre_auth.principal_for_token(token, identity=identity)["scope"] == ["cofre:read"]
    response = auth.minha_sessao_auth(None, authorization="Bearer " + token)
    assert response.success
    assert response.access_token == token
    claims = jwt.decode(response.access_token, options={"verify_signature": False})
    assert claims["jk_central"] == 1
    assert claims["jti"]
    assert cofre_auth.principal_for_token(response.access_token, identity=identity) == before
    cofre_auth._SESSIONS.clear()
    assert cofre_auth.principal_for_token(response.access_token, identity=identity) == before
    assert "opaque-fixture-grant" not in response.model_dump_json()


def test_overlapping_profile_refreshes_keep_the_same_cofre_authorization(refresh):
    from concurrent.futures import ThreadPoolExecutor
    token, attempt, identity = refresh
    before = cofre_auth.register_login(token, attempt)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: auth.minha_sessao_auth(
            None, authorization="Bearer " + token), range(2)))
    assert all(response.success and response.access_token == token for response in responses)
    cofre_auth._SESSIONS.clear()
    for response in responses:
        assert cofre_auth.principal_for_token(response.access_token, identity=identity) == before


def test_real_jk_refresh_cannot_create_cofre_authority_without_remote_proof(refresh):
    token, attempt, identity = refresh
    response = auth.minha_sessao_auth(None, authorization="Bearer " + token)
    assert response.success
    with pytest.raises(HTTPException) as error:
        cofre_auth.principal_for_token(response.access_token, identity=identity)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "cofre_session_unavailable"


def test_denied_jk_refresh_does_not_change_existing_cofre_proof(refresh, monkeypatch):
    token, attempt, identity = refresh
    before = cofre_auth.register_login(token, attempt)
    ticket = cofre_auth._store().load(cofre_auth._key(token))
    monkeypatch.setattr(auth, "_login_usuario_ativo", lambda user: False)
    response = auth.minha_sessao_auth(None, authorization="Bearer " + token)
    assert response.success is False
    assert not response.access_token
    assert cofre_auth._store().load(cofre_auth._key(token)) == ticket
    assert cofre_auth.principal_for_token(token, identity=identity) == before
