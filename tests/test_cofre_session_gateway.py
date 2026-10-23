"""Signed Cofre grants, renewable without repositories/Firebase; fictional identities only."""
from __future__ import annotations

import base64
import copy
import hmac
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import bcrypt
import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.auth.crypt import RSASigner

from cloud.auth_gateway.app import firebase_adapter, main
from cloud.auth_gateway.app.cofre_capability import (
    AUDIENCE, CLAIMS, GRANT_SECONDS, SESSION_AUDIENCE, SESSION_CLAIMS, SESSION_SECONDS,
    CofreCapabilityIssuer, CofreSessionVerifier, GoogleCofreCapabilityIssuer,
)
from cloud.auth_gateway.app.config import GatewaySettings
from cloud.auth_gateway.app.contracts import GatewayGoogleLoginRequest, GatewayLoginRequest
from cloud.auth_gateway.app.domain import AuthenticationService
from cloud.auth_gateway.app.firebase_sessions import FirebaseSessionService
from cloud.auth_gateway.app.policy import canonical_hash, machine_hash


NOW = int(time.time())
ISSUER = "fixture-signer@fixture-project.iam.gserviceaccount.com"
USER, TENANT, MACHINE = "fixture-user", "fixture-company", "pc:fixture"
EMAIL, GOOGLE_AUDIENCE = "fixture@example.test", "fixture.apps.googleusercontent.com"
NONCE = "n" * 43
FAKE_SESSION_SECRET = bytes.fromhex("73" * 32)
FAKE_NEXT_SESSION_SECRET = bytes.fromhex("74" * 32)


@pytest.fixture(scope="module")
def material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    return SimpleNamespace(private=private, pem=pem, public=private.public_key())


@pytest.fixture
def issuing(material):
    clock, signed, checked = [NOW], [], []
    delegate = RSASigner.from_string(material.pem)

    class Signer:
        key_id = "fixture-key"

        def sign(self, value):
            signed.append(True)
            return delegate.sign(value)

    verifier = CofreSessionVerifier(ISSUER, clock=lambda: clock[0], session_secret=FAKE_SESSION_SECRET)
    verify = verifier.verify

    def tracked(token, machine):
        checked.append(True)
        return verify(token, machine)

    verifier.verify = tracked
    issuer = CofreCapabilityIssuer(Signer(), ISSUER, clock=lambda: clock[0], session_secret=FAKE_SESSION_SECRET)
    issuer.session_verifier = verifier
    return SimpleNamespace(issuer=issuer, clock=clock, signed=signed, checked=checked, verifier=verifier)


def user(permissions=None):
    return {"username": USER, "client_id": TENANT, "email": EMAIL, "active": True,
            "machine_ids": [MACHINE], "max_machines": 1,
            "permissions": {"cofre": True} if permissions is None else permissions,
            "password_hash": bcrypt.hashpw(b"fixture-password", bcrypt.gensalt(4)).decode()}


def settings(**updates):
    values = dict(project_id="fixture-project", firebase_web_api_key="fixture-public-key",
                  users_collection="fixture-users", minimum_app_version="1.0.124",
                  firestore_timeout_seconds=5, identity_timeout_seconds=8,
                  rate_limit_attempts=100, rate_limit_window_seconds=300,
                  google_oauth_client_id=GOOGLE_AUDIENCE)
    return GatewaySettings(**{**values, **updates})


def body(grant, **updates):
    return {"session_grant": grant, "machine_id": MACHINE, "request_nonce": NONCE,
            "app_version": "1.0.269", **updates}


def unpack(token):
    return jwt.decode(token, options={"verify_signature": False})


def signed(material, claims, **header):
    return jwt.encode(claims, FAKE_SESSION_SECRET, algorithm="HS256", headers=header)


class Repository:
    def __init__(self, record):
        self.record, self.calls = record, []

    def get_user(self, username):
        self.calls.append("get")
        assert username == self.record["username"]
        return copy.deepcopy(self.record)

    def find_google_user(self, email):
        self.calls.append("google")
        if email not in {self.record.get("email"), self.record.get("google_email"), self.record["username"]}:
            return None
        return self.record["username"], copy.deepcopy(self.record)

    def claim_machine(self, username, password, machine):
        self.calls.append("claim")
        assert username == self.record["username"] and password == self.record["password_hash"]
        assert machine == MACHINE
        return copy.deepcopy(self.record)

    def change_password(self, username, old, new, machine):
        self.claim_machine(username, old, machine)
        self.record["password_hash"] = new
        return copy.deepcopy(self.record)


class IdentityIssuer:
    def __init__(self):
        self.claims = []

    def issue(self, _uid, claims):
        self.claims.append(claims)
        return "fixture-identity-token", 3600


@pytest.mark.parametrize("permissions,scope", [({"cofre": True}, ["cofre:read"]),
    ({"cofre": True, "admin_usuarios": True}, ["cofre:read", "cofre:write"]),
    ({"full": True}, ["cofre:read", "cofre:write"]), ({"admin_usuarios": True}, None),
    ({"cofre": False}, None)])
def test_v2_issue_uses_profile_scopes_and_exact_signed_contract(issuing, material, permissions, scope):
    result = issuing.issuer.issue_session(user(permissions), USER, MACHINE)
    if scope is None:
        assert result is None and not issuing.signed
        return
    assert set(result) == {"protocol", "capability", "session_grant", "session_expires_at"} and result["protocol"] == 2
    grant = issuing.verifier.verify(result["session_grant"], MACHINE)
    assert set(grant) == SESSION_CLAIMS and grant["scope"] == scope
    assert grant["exp"] == NOW + GRANT_SECONDS and grant["valid_until"] is None
    assert len(grant["session_id"]) == 32 and len(issuing.signed) == 1
    assert result["session_expires_at"] == grant["exp"]
    cap = jwt.decode(result["capability"], material.public, algorithms=["RS256"], audience=AUDIENCE)
    assert set(cap) == CLAIMS and cap["scope"] == scope and cap["exp"] == NOW + SESSION_SECONDS
    assert cap["client_id"] == TENANT and cap["machine_hash"] == machine_hash(MACHINE)


def test_renewal_preserves_absolute_grant_and_does_not_inherit_short_firebase_deadline(issuing):
    repository, identities = Repository(user()), IdentityIssuer()
    firebase = SimpleNamespace(bootstrap_session=lambda *_: {"session_expires_at": NOW + 120})
    service = AuthenticationService(repository, identities, "1.0.124", firebase_sessions=firebase,
                                    cofre_capabilities=issuing.issuer)
    result = service.authenticate(GatewayLoginRequest(username=USER, password="fixture-password", machine_id=MACHINE,
        app_version="1.0.269", request_nonce=NONCE), cofre_protocol=2, firebase_protocol=True)
    access = result["cofre_access"]
    assert unpack(access["session_grant"])["exp"] == NOW + GRANT_SECONDS
    material = {key: value for key, value in result.items()
                if key not in {"success", "code", "identity_token", "expires_in", "registered_machines"}}
    assert identities.claims[0]["jk_response_hash"] == canonical_hash(material)
    issuing.clock[0] += SESSION_SECONDS + 60
    refreshed = issuing.issuer.renew_session(access["session_grant"], MACHINE)
    assert refreshed["session_grant"] == access["session_grant"]
    assert unpack(refreshed["capability"])["iat"] == issuing.clock[0]
    assert unpack(refreshed["capability"])["exp"] == issuing.clock[0] + SESSION_SECONDS
    assert repository.calls == ["get", "claim"] and len(identities.claims) == 1
    issuing.clock[0] = NOW + GRANT_SECONDS - 20
    final = issuing.issuer.renew_session(access["session_grant"], MACHINE)
    assert unpack(final["capability"])["exp"] == NOW + GRANT_SECONDS
    issuing.clock[0] += 20
    with pytest.raises(ValueError):
        issuing.issuer.renew_session(access["session_grant"], MACHINE)


def test_licence_end_is_midnight_sao_paulo_and_can_only_shorten_grant(issuing):
    issuing.clock[0] = int(datetime(2030, 1, 2, 2, tzinfo=timezone.utc).timestamp())
    result = issuing.issuer.issue_session({**user(), "valid_until": "2030-01-01"}, USER, MACHINE)
    assert unpack(result["session_grant"])["valid_until"] == "01/01/2030"
    assert unpack(result["session_grant"])["exp"] == issuing.clock[0] + 3600
    assert unpack(result["capability"])["exp"] == issuing.clock[0] + 3600


def test_signed_v2_contract_is_accepted_by_the_real_desktop_verifier(issuing, material):
    from backend.services.cofre_auth import verify_capability

    issued = issuing.issuer.issue_session(user(), USER, MACHINE)
    options = dict(username=USER, client_id=TENANT, machine_id=MACHINE,
                   permissions={"cofre": True}, issuer=ISSUER, keys={"fixture-key": material.public}, now=NOW)
    capability = verify_capability(issued["capability"], **options)
    assert issued["session_expires_at"] == NOW + GRANT_SECONDS
    assert capability["exp"] <= issued["session_expires_at"]
    with pytest.raises(ValueError):
        verify_capability(issued["session_grant"], **options)
    issuing.clock[0] = NOW + SESSION_SECONDS + 1
    renewed = issuing.issuer.renew_session(issued["session_grant"], MACHINE)
    assert verify_capability(renewed["capability"], **{**options, "now": issuing.clock[0]})["client_id"] == TENANT


def test_bootstrap_deadline_caps_initial_cap_but_never_shortens_renewable_grant(issuing):
    issuing.clock[0] = NOW + 10
    repo, identities = Repository(user()), IdentityIssuer()
    firebase = SimpleNamespace(bootstrap_session=lambda *_: {"session_expires_at": NOW + SESSION_SECONDS})
    service = AuthenticationService(repo, identities, "1.0.124", firebase_sessions=firebase,
                                    cofre_capabilities=issuing.issuer)
    result = service.authenticate(GatewayLoginRequest(username=USER, password="fixture-password", machine_id=MACHINE,
        app_version="1.0.269", request_nonce=NONCE), cofre_protocol=2, firebase_protocol=True)
    assert unpack(result["cofre_access"]["capability"])["exp"] == NOW + SESSION_SECONDS
    assert unpack(result["cofre_access"]["session_grant"])["exp"] == NOW + 10 + GRANT_SECONDS


@pytest.mark.parametrize("field,value", [("iss", "attacker@other.iam.gserviceaccount.com"),
    ("aud", AUDIENCE), ("aud", [SESSION_AUDIENCE]), ("sub", " User "), ("sub", ""),
    ("client_id", "../other"), ("client_id", "CON"), ("machine_hash", "0" * 64),
    ("scope", ["cofre:write"]), ("scope", ["cofre:read", "cofre:read"]),
    ("scope", ["cofre:read", "other"]), ("scope", "cofre:read"),
    ("iat", True), ("iat", NOW + 61), ("iat", str(NOW)), ("exp", True),
    ("exp", NOW), ("exp", NOW + GRANT_SECONDS + 1),
    ("valid_until", "not-a-date"), ("valid_until", "01/01/2020"),
    ("session_id", "a" * 31), ("session_id", "G" * 32), ("extra", True)])
def test_signed_malicious_claims_are_rejected_before_iam(issuing, material, field, value):
    grant = unpack(issuing.issuer.issue_session(user(), USER, MACHINE)["session_grant"])
    count = len(issuing.signed)
    with pytest.raises(ValueError):
        issuing.issuer.renew_session(signed(material, {**grant, field: value}), MACHINE)
    assert len(issuing.signed) == count


def test_missing_claim_forgery_wrong_machine_alg_and_remote_header_fail_closed(issuing, material):
    result = issuing.issuer.issue_session(user(), USER, MACHINE)
    grant = unpack(result["session_grant"])
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tokens = [signed(material, {key: value for key, value in grant.items() if key != "valid_until"}),
              jwt.encode(grant, wrong, algorithm="RS256"),
              jwt.encode(grant, "fixture-hmac-key-that-is-long-enough", algorithm="HS256"),
              signed(material, grant, jku="https://attacker.test/keys"),
              signed(material, grant, kid="unknown"), "not-a-jwt"]
    count = len(issuing.signed)
    for token in tokens:
        with pytest.raises(ValueError):
            issuing.issuer.renew_session(token, MACHINE)
    with pytest.raises(ValueError):
        issuing.issuer.renew_session(result["session_grant"], "pc:other")
    assert len(issuing.signed) == count


def test_renewal_http_is_strict_and_uses_no_repository_firestore_auth_or_profile(issuing, monkeypatch):
    access = issuing.issuer.issue_session(user(), USER, MACHINE)
    issuing.clock[0] += SESSION_SECONDS + 1

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cofre renewal cannot consult Firebase, users, auth or network")

    monkeypatch.setattr(main, "FirestoreUserRepository", forbidden)
    monkeypatch.setattr(main, "FirebaseIdentityTokenIssuer", forbidden)
    monkeypatch.setattr(main, "build_firebase_sessions", forbidden)
    monkeypatch.setattr(main, "firebase_app", forbidden)
    monkeypatch.setattr(firebase_adapter.auth, "create_custom_token", forbidden)
    monkeypatch.setattr(firebase_adapter.firestore, "client", forbidden)
    monkeypatch.setattr(FirebaseSessionService, "bootstrap_session", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    client = TestClient(main.create_app(settings=settings(), cofre_capabilities=issuing.issuer))
    response = client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"]))
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"success", "protocol", "request_nonce", "capability", "session_grant", "session_expires_at"}
    assert result["success"] is True and result["protocol"] == 2 and result["request_nonce"] == NONCE
    assert result["session_grant"] == access["session_grant"] and response.headers["cache-control"] == "no-store"
    assert result["session_expires_at"] == access["session_expires_at"]
    assert unpack(result["capability"])["iat"] == issuing.clock[0]
    assert client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"], username="injected")).status_code == 422
    assert client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"], client_id="other")).status_code == 422
    assert client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"], machine_id="other")).status_code == 401
    assert client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"], request_nonce="short")).status_code == 422
    assert client.post("/api/auth/v1/cofre/session", json=body(access["session_grant"], app_version="1.0.1")).status_code == 426


def test_rate_limit_precedes_keys_and_iam_and_does_not_echo_grant(issuing):
    access = issuing.issuer.issue_session(user(), USER, MACHINE)
    count = len(issuing.signed)
    app = main.create_app(settings=settings(), cofre_capabilities=issuing.issuer)
    app.state.rate_limiter = SimpleNamespace(consume=lambda *_: False)
    response = TestClient(app).post("/api/auth/v1/cofre/session", json=body(access["session_grant"]))
    assert response.status_code == 429 and response.json()["code"] == "rate_limited"
    assert not issuing.checked and len(issuing.signed) == count
    assert access["session_grant"] not in response.text


@pytest.mark.parametrize("protocol", [False, True, 2])
def test_normal_login_has_identical_repository_and_identity_footprint(issuing, protocol):
    repo, identity, bootstraps = Repository(user()), IdentityIssuer(), []

    def bootstrap(*_args):
        bootstraps.append(True)
        return {"session_expires_at": NOW + SESSION_SECONDS}

    service = AuthenticationService(repo, identity, "1.0.124",
        firebase_sessions=SimpleNamespace(bootstrap_session=bootstrap), cofre_capabilities=issuing.issuer)
    result = service.authenticate(GatewayLoginRequest(username=USER, password="fixture-password", machine_id=MACHINE,
        app_version="1.0.269", request_nonce=NONCE), cofre_protocol=protocol, firebase_protocol=True)
    assert result["success"] and repo.calls == ["get", "claim"] and len(identity.claims) == len(bootstraps) == 1


def test_untrusted_header_or_issuer_never_calls_iam(issuing, material):
    claims = unpack(issuing.issuer.issue_session(user(), USER, MACHINE)["session_grant"])
    for token in (signed(material, claims, jku="https://attacker.test/keys"),
                  signed(material, {**claims, "iss": "attacker@other.iam.gserviceaccount.com"})):
        with pytest.raises(ValueError):
            issuing.issuer.renew_session(token, MACHINE)
    assert len(issuing.signed) == 1


def test_duplicate_json_claims_are_rejected_and_signing_failure_is_503(issuing, material, monkeypatch):
    access = issuing.issuer.issue_session(user(), USER, MACHINE)
    claims = unpack(access["session_grant"])
    raw = json.dumps(claims)[:-1] + ',"client_id":"other-company"}'
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    message = encode(b'{"alg":"HS256","typ":"JWT"}') + "." + encode(raw.encode())
    token = message + "." + encode(hmac.digest(FAKE_SESSION_SECRET, message.encode(), "sha256"))
    with pytest.raises(ValueError):
        issuing.issuer.renew_session(token, MACHINE)

    def failed(_value):
        raise RuntimeError("fixture-private-provider-error")

    monkeypatch.setattr(issuing.issuer.signer, "sign", failed)
    response = TestClient(main.create_app(settings=settings(), cofre_capabilities=issuing.issuer)).post(
        "/api/auth/v1/cofre/session", json=body(access["session_grant"]))
    assert response.status_code == 503 and "fixture-private-provider-error" not in response.text


@pytest.mark.parametrize("protocol", [None, "1", "2", "dual"])
@pytest.mark.parametrize("path", ["login", "change-password"])
def test_http_negotiation_preserves_v1_and_legacy(issuing, protocol, path):
    repo, identities = Repository(user()), IdentityIssuer()
    service = AuthenticationService(repo, identities, "1.0.124", cofre_capabilities=issuing.issuer)
    client = TestClient(main.create_app(settings=settings(), service=service))
    request = {"username": USER, "machine_id": MACHINE, "app_version": "1.0.269", "request_nonce": NONCE}
    request.update({"password": "fixture-password"} if path == "login" else
                   {"current_password": "fixture-password", "new_password": "fixture-new-password"})
    headers = ({"X-JK-Cofre-Protocol": "1", "X-JK-Cofre-Session-Protocol": "2"} if protocol == "dual"
               else {"X-JK-Cofre-Protocol": protocol} if protocol else {})
    result = client.post("/api/auth/v1/" + path, json=request, headers=headers).json()
    assert result["success"] is True
    if protocol is None:
        assert "cofre_access" not in result and not issuing.signed
    else:
        expected = 2 if protocol in {"2", "dual"} else 1
        assert result["cofre_access"]["protocol"] == expected
        assert set(result["cofre_access"]) == ({"protocol", "capability", "session_grant", "session_expires_at"} if expected == 2
                                              else {"protocol", "capability"})


def google_claims(**updates):
    return {"iss": "https://accounts.google.com", "aud": GOOGLE_AUDIENCE, "sub": "fixture-google-id",
            "email": EMAIL.upper(), "email_verified": True, **updates}


@pytest.mark.parametrize("record_updates", [{}, {"username": EMAIL, "email": ""},
    {"username": EMAIL, "email": "contact@example.test"},
    {"google_email": EMAIL, "email": "contact@example.test"}])
def test_google_login_uses_verified_identity_and_same_v2_profile_signing(issuing, record_updates):
    record = {**user(), **record_updates}
    original = copy.deepcopy(record)
    repo, identities = Repository(record), IdentityIssuer()
    service = AuthenticationService(repo, identities, "1.0.124", cofre_capabilities=issuing.issuer)
    verified = []

    def verifier(credential, audience):
        assert credential == "fixture-google-credential" and audience == GOOGLE_AUDIENCE
        verified.append(True)
        return google_claims()

    client = TestClient(main.create_app(settings=settings(), service=service, google_verifier=verifier))
    request = {"credential": "fixture-google-credential", "machine_id": MACHINE,
               "app_version": "1.0.269", "request_nonce": NONCE}
    response = client.post("/api/auth/v1/google-login", json=request,
        headers={"X-JK-Cofre-Protocol": "1", "X-JK-Cofre-Session-Protocol": "2"})
    assert response.status_code == 200
    result = response.json()
    assert repo.calls == ["google", "claim"] and verified == [True] and len(identities.claims) == 1
    assert result["user_data"]["email"] == record["email"] and result["user_data"]["client_id"] == TENANT
    assert result["user_data"]["google_email"] == EMAIL
    assert repo.record == original
    material = {key: value for key, value in result.items()
                if key not in {"success", "code", "identity_token", "expires_in", "registered_machines"}}
    assert identities.claims[0]["jk_response_hash"] == canonical_hash(material)
    grant = issuing.verifier.verify(result["cofre_access"]["session_grant"], MACHINE)
    assert grant["sub"] == record["username"] and grant["client_id"] == TENANT
    assert client.post("/api/auth/v1/google-login", json={**request, "username": "injected"}).status_code == 422


@pytest.mark.parametrize("updates", [{"iss": "https://attacker.test"}, {"aud": "other"},
    {"sub": ""}, {"email_verified": False}, {"email_verified": "true"}, {"email": "unlinked@example.test"}])
def test_google_rejects_unverified_unlinked_or_wrong_audience_before_signing(issuing, updates):
    repo, identities = Repository(user()), IdentityIssuer()
    service = AuthenticationService(repo, identities, "1.0.124", cofre_capabilities=issuing.issuer)
    client = TestClient(main.create_app(settings=settings(), service=service,
                                      google_verifier=lambda *_: google_claims(**updates)))
    response = client.post("/api/auth/v1/google-login", json={"credential": "fixture-google-credential",
        "machine_id": MACHINE, "app_version": "1.0.269", "request_nonce": NONCE},
        headers={"X-JK-Cofre-Protocol": "2"})
    assert response.status_code == 401 and not issuing.signed and not identities.claims
    assert "claim" not in repo.calls


def test_google_configuration_and_rate_limit_are_checked_before_verification(issuing):
    def forbidden(*_args):
        raise AssertionError("Google verification must not run")

    request = {"credential": "fixture-secret", "machine_id": MACHINE,
               "app_version": "1.0.269", "request_nonce": NONCE}
    client = TestClient(main.create_app(settings=settings(google_oauth_client_id=""), google_verifier=forbidden))
    response = client.post("/api/auth/v1/google-login", json=request)
    assert response.status_code == 503 and "fixture-secret" not in response.text
    app = main.create_app(settings=settings(), google_verifier=forbidden)
    app.state.rate_limiter = SimpleNamespace(consume=lambda *_: False)
    assert TestClient(app).post("/api/auth/v1/google-login", json=request).status_code == 429


def test_google_certificate_transport_is_bounded_and_outage_fails_closed_without_relogin(issuing, monkeypatch):
    repo, identity, transport_calls = Repository(user()), IdentityIssuer(), []
    service = AuthenticationService(repo, identity, "1.0.124", cofre_capabilities=issuing.issuer)

    def unavailable(*_args, **kwargs):
        transport_calls.append(kwargs)
        raise requests.ConnectionError("fixture-provider-private-error")

    def verify(_credential, transport, **kwargs):
        assert kwargs == {"audience": GOOGLE_AUDIENCE}
        return transport(url="https://www.googleapis.com/oauth2/v1/certs", method="GET", timeout=120)

    monkeypatch.setattr(main, "GoogleRequest", lambda: unavailable)
    monkeypatch.setattr(main.google_id_token, "verify_oauth2_token", verify)
    client = TestClient(main.create_app(settings=settings(), service=service))
    response = client.post("/api/auth/v1/google-login", json={"credential": "fixture-secret",
        "machine_id": MACHINE, "app_version": "1.0.269", "request_nonce": NONCE})
    assert response.status_code == 503 and not repo.calls and not identity.claims and not issuing.signed
    assert transport_calls == [{"url": "https://www.googleapis.com/oauth2/v1/certs", "method": "GET", "timeout": 8}]
    assert "fixture-secret" not in response.text and "fixture-provider-private-error" not in response.text


def test_google_preserves_existing_central_and_firebase_negotiation(issuing):
    repo, identity, calls = Repository(user()), IdentityIssuer(), []
    central = SimpleNamespace(enabled_for=lambda _: True,
        bootstrap_session=lambda *_: calls.append("central") or {"expires_at": NOW + 120})
    firebase = SimpleNamespace(bootstrap_session=lambda *_:
        calls.append("firebase") or {"session_expires_at": NOW + 120})
    service = AuthenticationService(repo, identity, "1.0.124", central=central,
        firebase_sessions=firebase, cofre_capabilities=issuing.issuer)
    request = GatewayGoogleLoginRequest(credential="fixture", machine_id=MACHINE,
                                        app_version="1.0.269", request_nonce=NONCE)
    result = service.authenticate_google(request, google_claims(), cofre_protocol=2,
                                        central_protocol=True, firebase_protocol=True)
    assert calls == ["central", "firebase"] and repo.calls == ["google", "claim"]
    assert result["central"] and result["firebase_access"]
    assert unpack(result["cofre_access"]["session_grant"])["exp"] == NOW + GRANT_SECONDS


@pytest.mark.parametrize("record_updates", [{"active": False}, {"valid_until": "01/01/2020"}])
def test_google_access_policy_is_enforced_before_claim_and_signing(issuing, record_updates):
    repo, identity = Repository({**user(), **record_updates}), IdentityIssuer()
    service = AuthenticationService(repo, identity, "1.0.124", cofre_capabilities=issuing.issuer)
    client = TestClient(main.create_app(settings=settings(), service=service, google_verifier=lambda *_: google_claims()))
    response = client.post("/api/auth/v1/google-login", json={"credential": "fixture",
        "machine_id": MACHINE, "app_version": "1.0.269", "request_nonce": NONCE},
        headers={"X-JK-Cofre-Session-Protocol": "2"})
    assert response.status_code == 403 and repo.calls == ["google"] and not issuing.signed and not identity.claims


def test_restart_after_twelve_hours_and_removed_old_rsa_key_renews_with_current_key(issuing, monkeypatch):
    from backend.services.cofre_auth import verify_capability

    initial = issuing.issuer.issue_session(user(), USER, MACHINE)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    after_rotation = NOW + 13 * 3600
    restarted = CofreCapabilityIssuer(RSASigner.from_string(pem, key_id="rotated-key"), ISSUER,
        clock=lambda: after_rotation, session_secret=FAKE_SESSION_SECRET)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Old RSA keys, Firebase and network must not be needed for opaque renewal")

    monkeypatch.setattr(requests.Session, "request", forbidden)
    renewed = restarted.renew_session(initial["session_grant"], MACHINE)
    assert renewed["session_grant"] == initial["session_grant"]
    assert renewed["session_expires_at"] == initial["session_expires_at"]
    assert jwt.get_unverified_header(renewed["capability"])["kid"] == "rotated-key"
    options = dict(username=USER, client_id=TENANT, machine_id=MACHINE, permissions={"cofre": True},
                   issuer=ISSUER, keys={"rotated-key": private.public_key()}, now=after_rotation)
    assert verify_capability(renewed["capability"], **options)["exp"] == after_rotation + SESSION_SECONDS
    with pytest.raises(ValueError):
        verify_capability(initial["capability"], **options)


def test_server_secret_rotation_accepts_previous_only_within_absolute_grant_lease(issuing, material):
    initial = issuing.issuer.issue_session(user(), USER, MACHINE)
    rotated = CofreCapabilityIssuer(RSASigner.from_string(material.pem), ISSUER,
        clock=lambda: NOW + 13 * 3600, session_secret=FAKE_NEXT_SESSION_SECRET,
        previous_session_secret=FAKE_SESSION_SECRET)
    assert rotated.renew_session(initial["session_grant"], MACHINE)["session_grant"] == initial["session_grant"]
    retired = CofreCapabilityIssuer(RSASigner.from_string(material.pem), ISSUER,
        clock=lambda: NOW + 13 * 3600, session_secret=FAKE_NEXT_SESSION_SECRET)
    with pytest.raises(ValueError):
        retired.renew_session(initial["session_grant"], MACHINE)
    expired = CofreCapabilityIssuer(RSASigner.from_string(material.pem), ISSUER,
        clock=lambda: NOW + GRANT_SECONDS, session_secret=FAKE_NEXT_SESSION_SECRET,
        previous_session_secret=FAKE_SESSION_SECRET)
    with pytest.raises(ValueError):
        expired.renew_session(initial["session_grant"], MACHINE)


def test_missing_server_secret_preserves_v1_login_but_renewal_is_unavailable(material):
    from cloud.auth_gateway.app.errors import GatewayUnavailable

    issuer = CofreCapabilityIssuer(RSASigner.from_string(material.pem), ISSUER, clock=lambda: NOW)
    initial = issuer.issue_session(user(), USER, MACHINE)
    assert initial["protocol"] == 1 and set(initial) == {"protocol", "capability"}
    with pytest.raises(GatewayUnavailable):
        issuer.renew_session("fixture-opaque-ticket", MACHINE)
    # Missing configuration must fail before resolving a Google app/credential.
    with pytest.raises(GatewayUnavailable):
        GoogleCofreCapabilityIssuer(None).renew_session("fixture-opaque-ticket", MACHINE)
    response = TestClient(main.create_app(settings=settings(), cofre_capabilities=issuer)).post(
        "/api/auth/v1/cofre/session", json=body("fixture-opaque-ticket"))
    assert response.status_code == 503


@pytest.mark.parametrize("secret", ["short", "7" * 63, "z" * 64, b"x" * 31])
def test_session_secret_never_uses_weak_or_implicit_material(material, secret):
    with pytest.raises(ValueError):
        CofreCapabilityIssuer(RSASigner.from_string(material.pem), ISSUER, session_secret=secret)

