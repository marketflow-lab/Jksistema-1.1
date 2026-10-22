"""Real RSA proofs and offline authorization; all identities are fictitious."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import bcrypt
import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, Request
from fastapi.testclient import TestClient
from google.auth.crypt import RSASigner

from backend.modules.cofre import storage
from backend.modules.cofre.shared import SharedCofre
from backend.routers.cofre import create_cofre_router
from backend.services import cofre_auth, remote_auth_client, remote_auth_verification
from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState
from cloud.auth_gateway.app.cofre_capability import CofreCapabilityIssuer, capability_claims
from cloud.auth_gateway.app.config import GatewaySettings
from cloud.auth_gateway.app.contracts import GatewayLoginRequest, GatewayChangePasswordRequest
from cloud.auth_gateway.app.domain import AuthenticationService
from cloud.auth_gateway.app.firebase_sessions import FirebaseSessionService
from cloud.auth_gateway.app.main import create_app


NOW = int(time.time())
ISSUER = "fixture-signer@fixture-project.iam.gserviceaccount.com"
PROJECT = "fixture-project"
USER = "fixture-user"
TENANT = "fixture-company"
MACHINE = "pc:fixture"
NONCE = "n" * 43


@dataclass
class SigningMaterial:
    private: bytes = field(repr=False)
    public: object = field(repr=False)
    signer: object = field(repr=False)


@pytest.fixture(scope="module")
def signing():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    return SigningMaterial(private=pem, public=private.public_key(), signer=RSASigner.from_string(pem))


@pytest.fixture(autouse=True)
def isolated_sessions(monkeypatch, signing):
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", ISSUER)
    with cofre_auth._LOCK:
        cofre_auth._SESSIONS.clear()
        cofre_auth._KEYS.clear()
        cofre_auth._KEYS[ISSUER] = (NOW + 3600, {"fixture-key": signing.public})
    yield
    with cofre_auth._LOCK:
        cofre_auth._SESSIONS.clear()
        cofre_auth._KEYS.clear()


def user(permissions=None):
    return {"username": USER, "client_id": TENANT, "active": True,
            "permissions": permissions if permissions is not None else {"cofre": True},
            "machine_ids": [MACHINE], "max_machines": 1,
            "password_hash": bcrypt.hashpw(b"fixture-password", bcrypt.gensalt(4)).decode()}


def claims(permissions=None):
    return capability_claims(user(permissions), USER, MACHINE, ISSUER, now=NOW)


def token(signing, payload=None, *, header=None):
    return jwt.encode(payload or claims(), signing.private, algorithm="RS256", headers=header)


def verify(value, *, permissions=None, **kwargs):
    return cofre_auth.verify_capability(
        value, username=USER, client_id=TENANT, machine_id=MACHINE,
        permissions=permissions or {"cofre": True}, now=NOW, **kwargs)


def attempt(signing, permissions=None, **changes):
    return RemoteAuthAttempt(
        RemoteAuthState.SUCCESS,
        user_data={"username": USER, "client_id": TENANT, "machine_id": MACHINE},
        permissions=permissions or {"cofre": True}, policy={"valid_until": None},
        cofre_access={"protocol": 1, "capability": token(signing, claims(permissions))}, **changes)


@pytest.mark.parametrize("permissions,expected", [
    ({"cofre": True}, ["cofre:read"]),
    ({"cofre": True, "admin_usuarios": True}, ["cofre:read", "cofre:write"]),
    ({"full": True}, ["cofre:read", "cofre:write"]),
    ({"admin_usuarios": True}, None),
    ({"cofre": False}, None),
])
def test_issuer_derives_scopes_from_authoritative_permissions(signing, permissions, expected):
    issuer = CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: NOW)
    issued = issuer.issue(user(permissions), USER, MACHINE)
    if expected is None:
        assert issued is None
    else:
        assert jwt.get_unverified_header(issued["capability"]) == {"typ": "JWT", "alg": "RS256"}
        principal = verify(issued["capability"], permissions=permissions)
        assert principal["scope"] == expected
        assert principal["exp"] == NOW + 8 * 3600


@pytest.mark.parametrize("field,value", [
    ("iss", "attacker@other-project.iam.gserviceaccount.com"),
    ("aud", "other-service"), ("aud", ["jk-cofre", "other-service"]),
    ("sub", "other-user"), ("client_id", "other-company"),
    ("client_id", "../outside"), ("machine_hash", "0" * 64),
    ("scope", ["cofre:write"]), ("scope", ["cofre:read", "cofre:write"]),
    ("scope", ["cofre:read", "cofre:read"]), ("scope", "cofre:read"),
    ("exp", NOW), ("exp", NOW + 28801), ("exp", True),
    ("iat", NOW + 61), ("iat", True), ("iat", "1800000000"),
])
def test_signed_but_invalid_identity_scope_and_expiry_are_rejected(signing, field, value):
    payload = {**claims(), field: value}
    with pytest.raises((ValueError, jwt.InvalidTokenError)):
        verify(token(signing, payload))


def test_scope_and_claims_contract_is_closed(signing):
    for payload in ({**claims(), "extra": True}, {k: v for k, v in claims().items() if k != "client_id"}):
        with pytest.raises((ValueError, jwt.InvalidTokenError)):
            verify(token(signing, payload))
    with pytest.raises((ValueError, jwt.InvalidTokenError)):
        verify(token(signing, header={"jku": "https://attacker.test/keys"}))
    with pytest.raises((ValueError, jwt.InvalidTokenError)):
        verify(jwt.encode(claims(), "fixture-hmac-key-with-32-bytes-test", algorithm="HS256"))


def test_signature_forgery_and_unknown_key_fail_closed(signing):
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims(), wrong, algorithm="RS256")
    with pytest.raises(ValueError):
        verify(forged)
    with pytest.raises(ValueError):
        verify(token(signing, header={"kid": "attacker-key"}))
    with pytest.raises(ValueError):
        cofre_auth.register_login("fixture-local", RemoteAuthAttempt(
            RemoteAuthState.SUCCESS, user_data=attempt(signing).user_data,
            permissions={"cofre": True}, policy={},
            cofre_access={"protocol": 1, "capability": forged}))


def test_issuer_caps_expiry_at_account_end_and_existing_session(signing):
    before_midnight = int(datetime(2030, 1, 2, 2, 0, tzinfo=timezone.utc).timestamp())
    issuer = CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: before_midnight)
    issued = issuer.issue({**user(), "vencimento": "2030-01-01"}, USER, MACHINE)
    decoded = jwt.decode(issued["capability"], options={"verify_signature": False})
    assert decoded["exp"] == before_midnight + 3600
    shorter = issuer.issue(user(), USER, MACHINE, expires_at=before_midnight + 300)
    assert jwt.decode(shorter["capability"], options={"verify_signature": False})["exp"] == before_midnight + 300
    with pytest.raises(ValueError):
        issuer.issue(user(), USER, MACHINE, expires_at=before_midnight)
    with pytest.raises(ValueError):
        verify(token(signing), valid_until="01/01/2020")


def test_local_token_is_only_an_opaque_ram_session_handle(signing):
    cofre_auth.register_login("fixture-local-a", attempt(signing))
    actual = cofre_auth.principal_for_token("fixture-local-a", now=NOW)
    assert set(actual) == {"client_id", "username", "scope", "exp", "capability"}
    actual["scope"].append("cofre:write")
    assert cofre_auth.principal_for_token("fixture-local-a", now=NOW)["scope"] == ["cofre:read"]
    for handle in ("", "fixture-local-b", token(signing)):
        with pytest.raises(Exception) as error:
            cofre_auth.principal_for_token(handle, now=NOW)
        assert error.value.status_code == 401
    with pytest.raises(Exception) as error:
        cofre_auth.principal_for_token("fixture-local-a", now=NOW + 28800)
    assert error.value.status_code == 401


def test_new_login_revokes_previous_capability_even_when_permission_was_removed(signing):
    cofre_auth.register_login("fixture-local-old", attempt(signing))
    cofre_auth.register_login("fixture-local-new", RemoteAuthAttempt(
        RemoteAuthState.SUCCESS, user_data=attempt(signing).user_data,
        permissions={"cofre": False}, policy={}))
    for handle in ("fixture-local-old", "fixture-local-new"):
        with pytest.raises(Exception) as error:
            cofre_auth.principal_for_token(handle)
        assert error.value.status_code == 401


class Repository:
    def __init__(self, record):
        self.record, self.reads, self.claims = record, 0, 0
    def get_user(self, username):
        assert username == USER
        self.reads += 1
        return copy.deepcopy(self.record)
    def claim_machine(self, *_):
        self.claims += 1
        return copy.deepcopy(self.record)
    def change_password(self, _username, _old_password, new_password_hash, _machine):
        self.record["password_hash"] = new_password_hash
        return copy.deepcopy(self.record)


class IdentityIssuer:
    def __init__(self, signing):
        self.signing, self.signins, self.sessions = signing, 0, 0
    def issue(self, uid, claims):
        self.signins += 1
        payload = {"iss": "https://securetoken.google.com/" + PROJECT, "aud": PROJECT,
                   "sub": uid, "auth_time": NOW, "firebase": {"sign_in_provider": "custom"}, **claims}
        return token(self.signing, payload), 3600
    def issue_session(self, *_):
        self.sessions += 1
        return {"id_token": "fixture-firebase-token", "refresh_token": "fixture-refresh-token", "expires_in": 3600}
    def verify(self, value, _request, **kwargs):
        return jwt.decode(value, self.signing.public, algorithms=["RS256"], audience=kwargs["audience"])


class Store:
    def __init__(self):
        self.writes = 0
    def collection(self, collection):
        assert collection == "jk_sistema_firebase_grants"
        return self
    def document(self, _identifier):
        return self
    def create(self, _grant, **_kwargs):
        self.writes += 1


def gateway(signing, *, cofre_protocol, client_id=TENANT):
    repository, identities, store = Repository({**user(), "client_id": client_id}), IdentityIssuer(signing), Store()
    settings = GatewaySettings(PROJECT, "fixture-public-key", "jk_sistema_usuarios", "1.0.124", 5, 8, 10, 300)
    service = AuthenticationService(repository, identities, "1.0.124",
        firebase_sessions=FirebaseSessionService(settings, store, identities, clock=lambda: NOW),
        cofre_capabilities=CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: NOW))
    result = service.authenticate(GatewayLoginRequest(
        username=USER, password="fixture-password", machine_id=MACHINE,
        app_version="1.0.262", request_nonce=NONCE),
        firebase_protocol=True, cofre_protocol=cofre_protocol)
    return result, (repository.reads, repository.claims, store.writes, identities.signins, identities.sessions), identities


def test_capability_adds_no_firestore_operations_or_firebase_signin(signing):
    legacy, baseline, _ = gateway(signing, cofre_protocol=False)
    current, actual, _ = gateway(signing, cofre_protocol=True)
    assert baseline == actual == (1, 1, 1, 1, 1)
    assert "cofre_access" not in legacy
    assert set(current["cofre_access"]) == {"protocol", "capability"}


def test_password_change_and_explicit_protocol_use_the_same_signed_capability(signing):
    service = AuthenticationService(Repository(user()), IdentityIssuer(signing), "1.0.124",
        cofre_capabilities=CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: NOW))
    result = service.change_password(GatewayChangePasswordRequest(
        username=USER, current_password="fixture-password", new_password="fixture-new-password",
        machine_id=MACHINE, app_version="1.0.260", request_nonce=NONCE), cofre_protocol=True)
    assert result["code"] == "password_changed"
    assert result["permissions"]["cofre"] is True
    assert verify(result["cofre_access"]["capability"])["client_id"] == TENANT


def test_actual_login_hook_keeps_capability_in_backend_ram(signing):
    from backend.schemas import LoginResponse
    from backend.services import admin_usuarios_auth
    response = LoginResponse(success=True, message="fixture", user_data=attempt(signing).user_data,
                             permissions={"cofre": True}, access_token="fixture-local")
    result = admin_usuarios_auth._conectar_firebase_apos_login(response, attempt(signing))
    assert result.access_token == "fixture-local"
    assert "cofre_access" not in result.user_data
    assert "capability" not in result.model_dump_json()
    assert cofre_auth.principal_for_token(result.access_token)["client_id"] == TENANT


@pytest.mark.parametrize("operation", ["login", "change-password"])
@pytest.mark.parametrize("negotiate", [False, True])
def test_http_opt_in_preserves_legacy_hash_and_supports_both_auth_flows(signing, operation, negotiate):
    identities = IdentityIssuer(signing)
    service = AuthenticationService(Repository(user()), identities, "1.0.124",
        cofre_capabilities=CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: NOW))
    settings = GatewaySettings(PROJECT, "fixture-public-key", "jk_sistema_usuarios", "1.0.124", 5, 8, 10, 300)
    client = TestClient(create_app(service=service, settings=settings))
    body = {"username": USER, "machine_id": MACHINE, "app_version": "1.0.260", "request_nonce": NONCE}
    body.update({"password": "fixture-password"} if operation == "login" else
                {"current_password": "fixture-password", "new_password": "fixture-new-password"})
    response = client.post("/api/auth/v1/" + operation, json=body,
                           headers={"X-JK-Cofre-Protocol": "1"} if negotiate else {})
    assert response.status_code == 200
    payload = response.json()
    assert ("cofre_access" in payload) is negotiate
    assert ("cofre" in payload["permissions"]) is negotiate
    material = {k: v for k, v in payload.items() if k not in
                {"success", "code", "identity_token", "expires_in", "registered_machines"}}
    signed = jwt.decode(payload["identity_token"], signing.public, algorithms=["RS256"], audience=PROJECT)
    assert signed["jk_response_hash"] == remote_auth_verification.canonical_hash(material)


class Response:
    status_code = 200
    def __init__(self, value):
        self.value = value
        self.content = json.dumps(value).encode()
    def json(self):
        return copy.deepcopy(self.value)


class Transport:
    def __init__(self, payload):
        self.payload, self.headers = payload, None
    def post(self, _url, **kwargs):
        self.headers = kwargs["headers"]
        return Response(self.payload)
    def close(self):
        pass


def remote_attempt(payload, identities):
    transport = Transport(payload)
    result = remote_auth_client.attempt_remote_login(
        username=USER, password="fixture-password", machine_id=MACHINE, app_version="1.0.262",
        environ={"JK_REMOTE_AUTH_URL": "https://fixture.test/api/auth/v1/login",
                 "JK_REMOTE_AUTH_PROJECT_ID": PROJECT},
        session=transport, verifier=identities.verify, now=NOW, request_nonce=NONCE)
    assert transport.headers["X-JK-Cofre-Protocol"] == "1"
    return result


def test_signed_envelope_negotiation_and_capability_integrity(signing):
    payload, _, identities = gateway(signing, cofre_protocol=True)
    result = remote_attempt(payload, identities)
    assert result.success and result.cofre_access == payload["cofre_access"]
    assert "capability" not in repr(result)  # Credential fields are not represented.
    legacy, _, legacy_identities = gateway(signing, cofre_protocol=False)
    assert remote_attempt(legacy, legacy_identities).success
    tampered = copy.deepcopy(payload)
    tampered["cofre_access"]["capability"] = token(signing, {**claims(), "client_id": "other-company"})
    assert remote_attempt(tampered, identities).state is RemoteAuthState.INTEGRITY_FAILURE
    material = {k: v for k, v in tampered.items() if k not in {"success", "code", "identity_token", "expires_in", "registered_machines"}}
    tampered["identity_token"], _ = identities.issue(remote_auth_verification.expected_uid(USER), {
        "jk_auth_v": 1, "jk_username": USER, "jk_client_id": TENANT,
        "jk_machine_hash": hashlib.sha256(MACHINE.encode()).hexdigest(),
        "jk_response_hash": remote_auth_verification.canonical_hash(material)})
    assert remote_attempt(tampered, identities).state is RemoteAuthState.INTEGRITY_FAILURE


@pytest.mark.parametrize("client_id", ["_company", "-company", "a" * 65, "company.",
                                       "CON", "aux.txt", "NuL", "com1", "LPT9.cache", "../company"])
def test_invalid_cofre_namespace_does_not_block_the_normal_login(signing, client_id):
    payload, counts, identities = gateway(signing, cofre_protocol=True, client_id=client_id)
    assert counts == (1, 1, 1, 1, 1)
    assert "cofre_access" not in payload
    result = remote_attempt(payload, identities)
    assert result.success and result.user_data["client_id"] == client_id
    assert cofre_auth.register_login("fixture-local", result) is None
    with pytest.raises(ValueError):
        cofre_auth.verify_capability(token(signing, {**claims(), "client_id": client_id}),
            username=USER, client_id=client_id, machine_id=MACHINE,
            permissions={"cofre": True}, now=NOW)


@pytest.mark.parametrize("client_id", ["company..department", "a" * 64, "0-company_a.b"])
def test_valid_storage_namespaces_remain_compatible(signing, client_id):
    issued = CofreCapabilityIssuer(signing.signer, ISSUER, clock=lambda: NOW).issue(
        {**user(), "client_id": client_id}, USER, MACHINE)
    assert cofre_auth.verify_capability(issued["capability"], username=USER, client_id=client_id,
        machine_id=MACHINE, permissions={"cofre": True}, now=NOW)["client_id"] == client_id


def test_every_cofre_action_is_offline_after_login(signing, monkeypatch, tmp_path):
    from backend.services import admin_usuarios_common, admin_usuarios_firebase, admin_usuarios_presence_core
    from backend.services.firebase_user_session import FirebaseUserSession
    import firebase_admin.firestore

    cofre_auth.register_login("fixture-local", attempt(signing, {"full": True}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cofre must not query Firebase, profile, permissions, presence or keys")

    monkeypatch.setattr(FirebaseUserSession, "profile", forbidden)
    monkeypatch.setattr(admin_usuarios_common, "_carregar_permissoes_usuario", forbidden)
    monkeypatch.setattr(admin_usuarios_firebase, "_firebase_obter_usuario", forbidden)
    monkeypatch.setattr(admin_usuarios_presence_core, "_machine_presence_auto_touch", forbidden)
    monkeypatch.setattr(admin_usuarios_presence_core, "_machine_presence_save", forbidden)
    monkeypatch.setattr(firebase_admin.firestore, "client", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)

    async def dependency(request: Request, authorization=Header(None)):
        return cofre_auth.bind_request(request, authorization)

    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=dependency,
        sharing=True, shared_service=SharedCofre(tmp_path, url="")))
    client = TestClient(app)
    headers = {"Authorization": "Bearer fixture-local"}
    manifest = {"format": storage.FORMAT, "guild": {"id": "g1", "name": "Fixture"},
                "exported_at": "2026-10-22T12:00:00Z",
                "channels": [{"id": "c1", "name": "Fixture", "messages": [
                    {"id": "m1", "content": "Fixture text"}]}]}
    assert client.post("/api/cofre/importar", headers=headers,
        files={"arquivo": ("fixture.json", json.dumps(manifest).encode())}).status_code == 200
    channel = "/api/cofre/canais/c1?servidor_id=g1"
    message = "/api/cofre/canais/c1/mensagens/m1?servidor_id=g1"
    actions = [
        ("GET", "/api/cofre/servidores", {}, 200),
        ("GET", "/api/cofre/servidores/g1/canais", {}, 200),
        ("GET", "/api/cofre/canais/c1/mensagens?servidor_id=g1&q=Fixture", {}, 200),
        ("PATCH", message, {"json": {"content": "Changed"}}, 200),
        ("DELETE", message, {}, 200),
        ("GET", "/api/cofre/canais/c1/mensagens?servidor_id=g1&excluidos=true", {}, 200),
        ("POST", "/api/cofre/canais/c1/mensagens/m1/restaurar?servidor_id=g1", {}, 200),
        ("PATCH", channel, {"json": {"name": "Changed channel"}}, 200),
        ("GET", "/api/cofre/arquivos/g1/" + "a" * 64 + "/fixture.txt", {}, 404),
        ("GET", "/api/cofre/compartilhamento", {}, 200),
        ("POST", "/api/cofre/compartilhamento/sincronizar", {}, 200),
        ("POST", "/api/cofre/compartilhamento/publicar", {}, 503),
    ]
    for method, path, options, expected in actions:
        assert client.request(method, path, headers=headers, **options).status_code == expected
        assert client.request(method, path, **options).status_code == 401
    assert client.get("/api/cofre/servidores", headers={"Authorization": "Bearer forged-local"}).status_code == 401
