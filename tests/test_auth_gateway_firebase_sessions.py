"""Authentication/bootstrap regressions without real credentials or cloud writes."""
from copy import deepcopy
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi.testclient import TestClient

from cloud.auth_gateway.app.config import GatewaySettings
from cloud.auth_gateway.app.contracts import GatewayLoginRequest
from cloud.auth_gateway.app.domain import AuthenticationService, canonical_hash
from cloud.auth_gateway.app.errors import AuthRejected, GatewayUnavailable
from cloud.auth_gateway.app.firebase_sessions import FirebaseSessionService, build_grant, resource_hash
from cloud.auth_gateway.app.main import create_app
from cloud.auth_gateway.app.policy import machine_claim_updates


def user():
    return {"username": "ana", "client_id": "empresa-a", "active": True,
            "permissions": {"cadastro": True, "integracao": False},
            "machine_ids": ["pc:a"], "max_machines": 2,
            "password_hash": bcrypt.hashpw(b"password", bcrypt.gensalt(4)).decode()}


class Store:
    def __init__(self):
        self.docs = {}
    def collection(self, collection):
        assert collection == "jk_sistema_firebase_grants"
        return self
    def document(self, identifier):
        assert len(identifier) == 64
        return SimpleNamespace(create=lambda data, **kw: self.docs.update({identifier: deepcopy(data)}))


class Issuer:
    def __init__(self):
        self.calls = []
    def issue_session(self, uid, claims):
        self.calls.append((uid, claims))
        return {"id_token": "scoped", "refresh_token": "refresh", "expires_in": 3600}
    def issue(self, uid, claims):
        self.calls.append((uid, claims))
        return "signed-login", 3600


class Repository:
    def __init__(self, record):
        self.record = record
    def get_user(self, username):
        return deepcopy(self.record) if username == self.record["username"] else None
    def claim_machine(self, username, password, machine):
        self.record.update(machine_claim_updates(self.record, username, machine))
        return deepcopy(self.record)


SETTINGS = GatewaySettings("test-project", "public-key", "jk_sistema_usuarios", "1.0.124", 5, 8, 10, 300)


def request():
    return GatewayLoginRequest(username="ana", password="password", machine_id="pc:a",
                               app_version="1.0.140", request_nonce="n" * 43)


def service(record=None):
    store, issuer = Store(), Issuer()
    sessions = FirebaseSessionService(SETTINGS, store, issuer, clock=lambda: 1800000000)
    auth = AuthenticationService(Repository(record or user()), issuer, "1.0.124", firebase_sessions=sessions)
    return auth, store, issuer


def test_grant_preserves_historical_identifiers_and_isolates_user_tenant_and_scope():
    grant = build_grant(user(), "ana", "pc:a", SETTINGS.users_collection, now=1800000000)
    assert grant["scope_pointers"] == {"cadastro": resource_hash("shared-sync-machine", "empresa-a", "ana", "cadastro")}
    assert grant["keyring_id"] == resource_hash("shared-sync-keyring", "empresa-a", "ana")
    assert grant["member_id"] in grant["member_ids"]
    assert grant["session_expires_at"] == 1800000000 + 28800
    other = {**user(), "client_id": "empresa-b"}
    assert build_grant(other, "ana", "pc:a", SETTINGS.users_collection)["keyring_id"] != grant["keyring_id"]
    assert build_grant(user(), "bia", "pc:a", SETTINGS.users_collection)["keyring_id"] != grant["keyring_id"]
    assert "password_hash" not in str(grant) and "refresh_token" not in str(grant)
    assert grant["profile"]["machine_ids"] == ["pc:a"]


def test_full_grant_has_all_scopes_without_unlocking_other_users():
    grant = build_grant({**user(), "permissions": {"full": True}}, "ana", "pc:a", SETTINGS.users_collection)
    assert len(grant["pointers"]) == 7
    assert grant["profile"]["username"] == "ana"
    assert grant["profile"]["client_id"] == "empresa-a"


@pytest.mark.parametrize("change,machine", [({"active": False}, "pc:a"), ({}, "pc:unknown"),
                                            ({"valid_until": "01/01/2000"}, "pc:a")])
def test_inactive_expired_or_unapproved_machine_gets_no_grant(change, machine):
    with pytest.raises(AuthRejected):
        build_grant({**user(), **change}, "ana", machine, SETTINGS.users_collection)


def test_login_opt_in_signs_scoped_extension_and_writes_no_tokens_to_firestore():
    auth, store, issuer = service()
    result = auth.authenticate(request(), firebase_protocol=True)
    assert result["firebase_access"]["id_token"] == "scoped"
    assert len(store.docs) == 1
    grant_id, grant = next(iter(store.docs.items()))
    assert result["firebase_access"]["grant_id"] == grant_id
    assert issuer.calls[0][1]["jk_firebase_grant"] == grant_id
    signed = {key: result[key] for key in ("user_data", "permissions", "policy", "request_nonce", "firebase_access")}
    assert issuer.calls[-1][1]["jk_response_hash"] == canonical_hash(signed)
    assert "scoped" not in str(grant) and "refresh_token" not in str(grant)


def test_legacy_login_does_not_create_grant_or_change_signed_shape():
    auth, store, issuer = service()
    result = auth.authenticate(request())
    assert "firebase_access" not in result and not store.docs
    assert len(issuer.calls) == 1


def test_wrong_password_never_issues_or_persists_credentials():
    auth, store, issuer = service()
    invalid = request().model_copy(update={"password": SimpleNamespace(get_secret_value=lambda: "wrong")})
    with pytest.raises(AuthRejected):
        auth.authenticate(invalid, firebase_protocol=True)
    assert not store.docs and not issuer.calls


def test_scoped_bootstrap_failure_never_claims_success():
    auth, store, issuer = service()
    issuer.issue_session = lambda *args: {"id_token": "x", "refresh_token": ""}
    with pytest.raises(GatewayUnavailable):
        auth.authenticate(request(), firebase_protocol=True)
    assert not issuer.calls


def test_protocol_header_reaches_authenticated_bootstrap_and_hides_errors():
    auth, store, issuer = service()
    client = TestClient(create_app(service=auth, settings=SETTINGS))
    body = {"username": "ana", "password": "password", "machine_id": "pc:a", "app_version": "1.0.140", "request_nonce": "n" * 43}
    legacy = client.post("/api/auth/v1/login", json=body)
    scoped = client.post("/api/auth/v1/login", json=body, headers={"X-JK-Firebase-Protocol": "1"})
    assert legacy.status_code == scoped.status_code == 200
    assert "firebase_access" not in legacy.json() and "firebase_access" in scoped.json()
    assert scoped.headers["Cache-Control"] == "no-store"
