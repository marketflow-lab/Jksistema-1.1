"""Real RSA signatures across legacy/new gateway permission contracts."""
from __future__ import annotations

import copy
import json

import bcrypt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from backend.services import remote_auth_client as client
from backend.services import remote_auth_verification as verification
from cloud.auth_gateway.app.contracts import GatewayChangePasswordRequest, GatewayLoginRequest
from cloud.auth_gateway.app.domain import AuthenticationService
from cloud.auth_gateway.app.policy import COFRE_PERMISSION_MIN_APP_VERSION, PERMISSION_KEYS

NOW = 1_800_000_000
NONCE = "n" * 43
PROJECT = "jkjkjk-485920"
USERNAME = "fixture-user"
MACHINE = "pc:fixture"
LEGACY_KEYS = set(PERMISSION_KEYS) - {"cofre"}
ENV = {"JK_REMOTE_AUTH_URL": "https://auth.example.test/api/auth/v1/login",
       "JK_REMOTE_AUTH_PROJECT_ID": PROJECT, "JK_REMOTE_AUTH_MODE": "required"}


class SignedIssuer:
    def __init__(self, private, public):
        self.private, self.public = private, public

    def issue(self, uid, claims):
        claims = {"iss": "https://securetoken.google.com/" + PROJECT,
                  "aud": PROJECT, "sub": uid, "auth_time": NOW,
                  "firebase": {"sign_in_provider": "custom"}, **claims}
        return jwt.encode(claims, self.private, algorithm="RS256"), 3600

    def verify(self, token, _request, **kwargs):
        return jwt.decode(token, self.public, algorithms=["RS256"],
                          audience=kwargs["audience"],
                          issuer="https://securetoken.google.com/" + PROJECT)


@pytest.fixture(scope="module")
def issuer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return SignedIssuer(private, public)


class Repository:
    def __init__(self, permissions):
        self.user = {"username": USERNAME, "client_id": "fixture-tenant", "name": "Fixture",
                     "email": "fixture@example.test", "active": True,
                     "permissions": permissions, "machine_ids": [MACHINE], "max_machines": 1,
                     "password_hash": bcrypt.hashpw(b"fixture-password", bcrypt.gensalt(4)).decode()}

    def get_user(self, username):
        return copy.deepcopy(self.user) if username == USERNAME else None

    def claim_machine(self, _username, _password, _machine):
        return copy.deepcopy(self.user)

    def change_password(self, _username, _password, new_hash, _machine):
        self.user["password_hash"] = new_hash
        return copy.deepcopy(self.user)


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return copy.deepcopy(self.payload)


class Session:
    def __init__(self, payload):
        self.payload = payload

    def post(self, _url, **_kwargs):
        return Response(self.payload)

    def close(self):
        pass


def signed_material(payload):
    return {key: payload[key] for key in ("user_data", "permissions", "policy", "request_nonce",
                                         "firebase_access", "central", "central_migration") if key in payload}


def resign(payload, issuer):
    payload["identity_token"], _ = issuer.issue(verification.expected_uid(USERNAME), {
        "jk_auth_v": 1, "jk_username": USERNAME, "jk_client_id": "fixture-tenant",
        "jk_machine_hash": verification.machine_hash(MACHINE),
        "jk_response_hash": verification.canonical_hash(signed_material(payload))})
    return payload


def gateway_payload(issuer, permissions, version, operation="login"):
    service = AuthenticationService(Repository(permissions), issuer, "1.0.124")
    common = {"username": USERNAME, "machine_id": MACHINE,
              "app_version": version, "request_nonce": NONCE}
    if operation == "change-password":
        return service.change_password(GatewayChangePasswordRequest(
            **common, current_password="fixture-password", new_password="fixture-new-password"))
    return service.authenticate(GatewayLoginRequest(**common, password="fixture-password"))


def attempt(payload, issuer, operation="login"):
    common = {"username": USERNAME, "machine_id": MACHINE,
              "app_version": COFRE_PERMISSION_MIN_APP_VERSION,
              "environ": ENV, "session": Session(payload), "verifier": issuer.verify,
              "now": NOW, "request_nonce": NONCE}
    if operation == "change-password":
        return client.attempt_remote_password_change(
            **common, current_password="fixture-password", new_password="fixture-new-password")
    return client.attempt_remote_login(**common, password="fixture-password")


@pytest.mark.parametrize("operation", ["login", "change-password"])
@pytest.mark.parametrize("full", [False, True])
def test_new_desktop_verifies_original_legacy_map_before_extending_cofre(issuer, operation, full):
    payload = gateway_payload(issuer, {"full": full, "vendas": True}, "1.0.260", operation)
    original = copy.deepcopy(payload)
    assert set(payload["permissions"]) == LEGACY_KEYS
    validated = verification.validate_success_payload(
        payload, USERNAME, MACHINE, NONCE, payload["code"])
    assert validated["permissions"] == original["permissions"]
    result = attempt(payload, issuer, operation)
    assert result.success
    assert set(result.permissions) == set(PERMISSION_KEYS)
    assert result.permissions["cofre"] is full
    assert payload == original


@pytest.mark.parametrize("operation", ["login", "change-password"])
@pytest.mark.parametrize("version", ["1.0.260", COFRE_PERMISSION_MIN_APP_VERSION, "2.0.0"])
def test_gateway_negotiates_only_signed_permission_keys_and_preserves_legacy_client(issuer, monkeypatch, operation, version):
    payload = gateway_payload(issuer, {"cofre": True}, version, operation)
    expected = LEGACY_KEYS if version == "1.0.260" else set(PERMISSION_KEYS)
    assert set(payload["permissions"]) == expected
    if version == "1.0.260":
        # Original desktops require every historical key and reject all additions.
        assert all(type(value) is bool for value in payload["permissions"].values())
        monkeypatch.setattr(verification, "PERMISSION_KEYS", tuple(LEGACY_KEYS))
    validated = verification.validate_success_payload(
        payload, USERNAME, MACHINE, NONCE, payload["code"])
    verification.verify_signed_response(
        validated, client.load_remote_auth_configuration(ENV), Session(payload),
        verifier=issuer.verify, now=NOW)


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, False)])
def test_signed_non_full_cofre_permission_is_explicit_and_never_inferred(issuer, value, expected):
    permissions = {"cofre": value} if value is not None else {"vendas": True}
    payload = gateway_payload(issuer, permissions, COFRE_PERMISSION_MIN_APP_VERSION)
    assert payload["permissions"]["full"] is False
    result = attempt(payload, issuer)
    assert result.success and result.permissions["cofre"] is expected


@pytest.mark.parametrize("change", ["missing-historical", "missing-historical-legacy", "extra",
                                     "cofre-string", "cofre-number", "cofre-null", "cofre-list",
                                     "historical-string", "historical-number"])
def test_even_correctly_signed_malformed_permission_maps_fail_closed(issuer, change):
    payload = gateway_payload(issuer, {"vendas": True}, COFRE_PERMISSION_MIN_APP_VERSION)
    permissions = payload["permissions"]
    if change.startswith("missing-historical"):
        permissions.pop("vendas")
        if change.endswith("legacy"):
            permissions.pop("cofre")
    elif change == "extra":
        permissions["unexpected"] = True
    else:
        field, kind = change.split("-")
        permissions["cofre" if field == "cofre" else "vendas"] = {
            "string": "true", "number": 1, "null": None, "list": []}[kind]
    resign(payload, issuer)
    result = attempt(payload, issuer)
    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE
    assert result.allow_legacy_fallback is False


@pytest.mark.parametrize("change", ["add-cofre", "remove-cofre", "change-full"])
def test_permission_contract_transition_still_rejects_post_signature_tampering(issuer, change):
    legacy = change == "add-cofre"
    payload = gateway_payload(issuer, {"vendas": True},
                              "1.0.260" if legacy else COFRE_PERMISSION_MIN_APP_VERSION)
    if change == "add-cofre":
        payload["permissions"]["cofre"] = True
    elif change == "remove-cofre":
        payload["permissions"].pop("cofre")
    else:
        payload["permissions"]["full"] = True
    result = attempt(payload, issuer)
    assert result.state is client.RemoteAuthState.INTEGRITY_FAILURE
    assert not result.allow_legacy_fallback


def test_legacy_signed_firebase_extension_remains_bound_to_its_original_permission_map(issuer):
    payload = gateway_payload(issuer, {"full": True}, "1.0.260")
    payload["firebase_access"] = {
        "protocol": 1, "project_id": PROJECT, "grant_id": "a" * 64,
        "id_token": "fixture-scoped-token", "refresh_token": "fixture-refresh-token",
        "api_key": "fixture-public-key", "expires_at": NOW + 3600,
        "session_expires_at": NOW + 28800}
    resign(payload, issuer)
    result = attempt(payload, issuer)
    assert result.success and result.permissions["cofre"] is True
    assert result.firebase_access == payload["firebase_access"]
    payload["firebase_access"]["refresh_token"] = "fixture-tampered-token"
    assert attempt(payload, issuer).state is client.RemoteAuthState.INTEGRITY_FAILURE
