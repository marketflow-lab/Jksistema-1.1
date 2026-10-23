"""A mutable cache can deny Cofre access, never replace its signed ticket."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import bcrypt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request

from backend.services import admin_usuarios_auth as auth, admin_usuarios_store as store, cofre_auth
from backend.services.cofre_session_store import CofreSessionStore
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = {"sub": "fixture-user", "client_id": "fixture-company", "machine_id": "fixture-pc"}
ISSUER = "fixture-signer@fixture-project.iam.gserviceaccount.com"
LOCAL_KEY = "fixture-local-signature-" + "x" * 64


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def read_scoped_secret(self, target):
        return self.values.get(target, "")

    def write_scoped_secret(self, target, value):
        assert len(value.encode()) <= 2560
        self.values[target] = value
        return True

    def delete_scoped_secret(self, target):
        self.values.pop(target, None)
        return True


def machines(value, fallback=None):
    values = json.loads(value) if isinstance(value, str) and value else list(value or [])
    if fallback and fallback not in values:
        values.append(fallback)
    return values


@pytest.fixture(scope="module")
def signing():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def fixture(monkeypatch, tmp_path, signing):
    for name, value in {
        "PASTA_INFO": str(tmp_path), "ARQUIVO_AUTH_DB": str(tmp_path / "auth.db"),
        "_hash_password_se_preciso": lambda value: value,
        "_normalizar_email": lambda value: str(value or "").lower(),
        "_normalizar_empresa": lambda value: str(value or ""),
        "_normalizar_permissoes": lambda value: dict(value),
        "_normalizar_data_sistema": lambda value: value,
        "_normalizar_max_machines": lambda value: int(value),
        "_normalizar_lista_maquinas": machines,
    }.items():
        monkeypatch.setattr(store, name, value, raising=False)
    monkeypatch.setattr(auth, "_salvar_usuarios_sql", store._salvar_usuarios_sql, raising=False)
    monkeypatch.setattr(auth, "bcrypt", bcrypt, raising=False)
    credentials = MemoryCredentials()
    monkeypatch.setattr(cofre_auth, "_STORE", CofreSessionStore(tmp_path, credentials=credentials))
    monkeypatch.setattr(cofre_auth, "_SESSIONS", {})
    now = int(time.time())
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", ISSUER)
    monkeypatch.setattr(cofre_auth, "_KEYS", {ISSUER: (now + 3600, {"fixture": signing.public_key()})})

    def forbidden(*args, **kwargs):
        raise AssertionError("Cache policy must not contact Firebase or the network")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    for name in ("_firebase_obter_usuario", "_firebase_listar_usuarios", "_carregar_usuarios_sql"):
        monkeypatch.setattr(store, name, forbidden, raising=False)
    scope = ["cofre:read"]
    capability = jwt.encode({"iss": ISSUER, "aud": "jk-cofre", "sub": IDENTITY["sub"],
        "client_id": IDENTITY["client_id"], "machine_hash": hashlib.sha256(IDENTITY["machine_id"].encode()).hexdigest(),
        "scope": scope, "iat": now, "exp": now + 8 * 3600}, signing, algorithm="RS256", headers={"kid": "fixture"})
    attempt = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": IDENTITY["sub"], "client_id": IDENTITY["client_id"], "machine_id": IDENTITY["machine_id"]},
        permissions={"cofre": True}, policy={"active": True, "valid_until": None, "max_machines": 1},
        cofre_access={"protocol": 2, "capability": capability, "session_grant": "fixture-protected-opaque-proof",
                      "session_expires_at": now + 30 * 86400})
    profile = auth._salvar_perfil_autenticado_remoto(IDENTITY["sub"], attempt)
    token = jwt.encode(IDENTITY, LOCAL_KEY, algorithm="HS256")
    return SimpleNamespace(profile=profile, token=token, attempt=attempt, credentials=credentials,
                           path=tmp_path, signing=signing)


def real_bridge():
    tree = ast.parse((ROOT / "backend_api.py").read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_cofre_verified_jk_session")
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "backend.services.admin_usuarios_store"]
    scope = {"HTTPException": HTTPException,
             "decodificar_access_token": lambda token: jwt.decode(token, LOCAL_KEY, algorithms=["HS256"]),
             "_login_usuario_ativo": lambda user: user.get("active") is True,
             "_login_validade_ok": lambda user: (user.get("valid_until") != "expired", None),
             "_normalizar_permissoes": lambda value: dict(value)}
    exec(compile(ast.Module(body=imports + [function], type_ignores=[]), "backend-cofre-real-imports", "exec"), scope)
    return scope["_cofre_verified_jk_session"]


def bind(token):
    request = Request({"type": "http"})
    cofre_auth.bind_request(request, "Bearer " + token, decode_session=real_bridge())
    return request.state.cofre_principal


def cache_profile(fixture, *, source="firebase-cache", **changes):
    store._salvar_usuarios_sql({IDENTITY["sub"]: {**fixture.profile, **changes}}, source=source)


def test_signed_login_ticket_survives_normal_firebase_cache_overwrite(fixture):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    assert bind(fixture.token)["scope"] == ["cofre:read"]
    assert store._obter_perfil_autenticado_remoto_sql(IDENTITY["sub"])
    cache_profile(fixture)
    # Administrative recovery must keep requiring the original trusted source.
    assert store._obter_perfil_autenticado_remoto_sql(IDENTITY["sub"]) == {}
    cofre_auth._SESSIONS.clear()
    assert bind(fixture.token)["scope"] == ["cofre:read"]


@pytest.mark.parametrize("source", ["firebase-cache", "sql-admin", "cache", "local"])
def test_cache_origin_does_not_replace_proof_or_break_valid_proof(fixture, source):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    cache_profile(fixture, source=source)
    cofre_auth._SESSIONS.clear()
    assert bind(fixture.token)["client_id"] == IDENTITY["client_id"]


def test_allowing_policy_without_capability_or_protected_ticket_stays_unavailable(fixture):
    cache_profile(fixture)
    with pytest.raises(HTTPException) as error:
        bind(fixture.token)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "cofre_session_unavailable"
    assert fixture.credentials.values == {}


@pytest.mark.parametrize("changes", [
    {"client_id": "other-company"}, {"permissions": {"cofre": False}},
    {"active": False}, {"valid_until": "expired"},
    {"machine_id": "other-pc", "machine_ids": ["other-pc"]},
])
def test_mutable_policy_can_still_deny_company_permission_activity_validity_machine(fixture, changes):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    cache_profile(fixture, **changes)
    with pytest.raises(HTTPException) as error:
        bind(fixture.token)
    assert error.value.status_code == 403


def test_local_full_permission_never_expands_read_only_signed_capability(fixture):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    cache_profile(fixture, permissions={"full": True})
    cofre_auth._SESSIONS.clear()
    assert bind(fixture.token)["scope"] == ["cofre:read"]


def test_local_policy_can_reduce_signed_write_scope_after_cache_overwrite(fixture):
    attempt = copy.deepcopy(fixture.attempt)
    attempt.permissions.update(cofre=True, admin_usuarios=True)
    claims = jwt.decode(attempt.cofre_access["capability"], fixture.signing.public_key(),
                        algorithms=["RS256"], audience="jk-cofre", issuer=ISSUER)
    claims["scope"] = ["cofre:read", "cofre:write"]
    attempt.cofre_access["capability"] = jwt.encode(claims, fixture.signing,
        algorithm="RS256", headers={"kid": "fixture"})
    cofre_auth.register_login(fixture.token, attempt)
    cache_profile(fixture, permissions={"cofre": True})
    cofre_auth._SESSIONS.clear()
    assert bind(fixture.token)["scope"] == ["cofre:read"]


def test_missing_or_corrupt_local_policy_fails_closed(fixture):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    with store._auth_db_conexao() as conn:
        conn.execute("UPDATE usuarios_auth SET permissions_json = ?", ("invalid fixture json",))
    with pytest.raises(HTTPException) as error:
        bind(fixture.token)
    assert error.value.status_code == 503


def test_cached_other_user_cannot_supply_policy_for_valid_session(fixture):
    cofre_auth.register_login(fixture.token, fixture.attempt)
    with store._auth_db_conexao() as conn:
        conn.execute("UPDATE usuarios_auth SET username = ?", ("other-user",))
    with pytest.raises(HTTPException) as error:
        bind(fixture.token)
    assert error.value.status_code == 503
    assert store._obter_politica_local_cofre_sql("' OR 1 = 1 --") == {}


def test_local_policy_lookup_does_not_seed_or_initialize_database(fixture, monkeypatch):
    cache_profile(fixture)

    def forbidden(*args, **kwargs):
        raise AssertionError("A policy SELECT cannot seed or migrate the database")

    monkeypatch.setattr(store, "_init_auth_db", forbidden)
    monkeypatch.setattr(store, "_auth_db_conexao", forbidden)
    policy = store._obter_politica_local_cofre_sql(IDENTITY["sub"])
    assert policy["permissions"] == {"cofre": True}
    missing = fixture.path / "missing.db"
    monkeypatch.setattr(store, "ARQUIVO_AUTH_DB", str(missing))
    assert store._obter_politica_local_cofre_sql(IDENTITY["sub"]) == {}
    assert not missing.exists()
