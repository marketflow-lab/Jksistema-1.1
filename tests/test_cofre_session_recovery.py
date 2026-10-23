"""Real signatures, fictitious identities, fake Credential Manager and HTTPS."""
from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from backend.services import cofre_auth, remote_auth_client, remote_auth_verification
from backend.services.cofre_session_store import CofreSessionStore
from backend.services.remote_auth_contracts import PERMISSION_KEYS, RemoteAuthAttempt, RemoteAuthState

ISSUER = "fixture-cofre@fixture-project.iam.gserviceaccount.com"
USER, COMPANY, MACHINE = "fixture-reader", "fixture-company", "fixture-machine"
LOCAL = "fictitious-local-session"
CONFIG = {"JK_REMOTE_AUTH_URL": "https://fixture.example/api/auth/v1/login",
          "JK_REMOTE_AUTH_PROJECT_ID": "fixture-project"}
SERVER_SECRET = b"fixture-only-server-mac-key-32-bytes"


class FakeCredentials:
    def __init__(self):
        self.values = {}
        self.fail_target = None
        self.writes = 0

    def read_scoped_secret(self, target):
        return self.values.get(target, "")

    def write_scoped_secret(self, target, value):
        assert len(value.encode("utf-8")) <= 2560
        if self.fail_target == target:
            self.fail_target = None
            raise OSError("fixture interrupted write")
        self.values[target] = value
        self.writes += 1
        return True

    def delete_scoped_secret(self, target):
        self.values.pop(target, None)
        return True


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.content = json.dumps(payload).encode()
        self.headers = {"Cache-Control": "max-age=300"}

    def json(self):
        return copy.deepcopy(self.payload)


@pytest.fixture(scope="module")
def signing():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def setup(monkeypatch, tmp_path, signing):
    clock = [int(time.time())]
    creds = FakeCredentials()
    store = CofreSessionStore(tmp_path / "fixture-info", credentials=creds)
    monkeypatch.setattr(cofre_auth, "_STORE", store)
    monkeypatch.setattr(cofre_auth, "_SESSIONS", {})
    monkeypatch.setattr(cofre_auth, "_KEYS", {ISSUER: (clock[0] + 40 * 86400, {"fixture": signing.public_key()})})
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", ISSUER)
    monkeypatch.setattr(cofre_auth.time, "time", lambda: clock[0])
    for key, value in CONFIG.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JK_APP_VERSION", "1.0.270")

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external network")

    monkeypatch.setattr(requests.Session, "post", forbidden)
    monkeypatch.setattr(requests.Session, "get", forbidden)
    return SimpleNamespace(clock=clock, credentials=creds, store=store, signing=signing,
                           tmp_path=tmp_path, issued=clock[0])


def signed(setup, claims):
    return jwt.encode(claims, setup.signing, algorithm="RS256", headers={"kid": getattr(setup, "key_id", "fixture")})


def opaque_grant(claims):
    # Only the fake gateway interprets this opaque MAC ticket, never the client.
    payload = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode()
    return payload + "." + hmac.new(SERVER_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def grant_claims(setup, **changes):
    return {"iss": ISSUER, "aud": "jk-cofre-session", "sub": USER, "client_id": COMPANY,
            "machine_hash": hashlib.sha256(MACHINE.encode()).hexdigest(), "scope": ["cofre:read"],
            "iat": setup.issued, "exp": setup.issued + 30 * 86400,
            "valid_until": None, "session_id": "a" * 32, **changes}


def capability(setup, grant=None, **changes):
    grant = grant or grant_claims(setup)
    claims = {key: grant[key] for key in ("iss", "sub", "client_id", "machine_hash", "scope")}
    return signed(setup, {**claims, "aud": "jk-cofre", "iat": setup.clock[0],
        "exp": min(setup.clock[0] + 8 * 3600, grant["exp"]), **changes})


def attempt(setup, *, claims=None, protocol=2):
    grant = claims or grant_claims(setup)
    access = {"protocol": protocol, "capability": capability(setup, grant)}
    if protocol == 2:
        access.update(session_grant=opaque_grant(grant), session_expires_at=grant["exp"])
    return RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={"username": USER, "client_id": COMPANY, "machine_id": MACHINE},
        permissions={"cofre": True, "admin_usuarios": "cofre:write" in grant["scope"]},
        policy={"valid_until": grant["valid_until"]}, cofre_access=access)


def identity(**changes):
    return {"sub": USER, "client_id": COMPANY, "machine_id": MACHINE,
            "permissions": {"cofre": True}, **changes}


def bind(*, token=LOCAL, payload=None, decoder=None):
    request = Request({"type": "http"})
    tenant = cofre_auth.bind_request(request, "Bearer " + token,
                                   decode_session=decoder or (lambda _: payload or identity()))
    return tenant, request.state.cofre_principal


class Exchange:
    def __init__(self, setup):
        self.setup, self.calls, self.status = setup, [], 200
        self.key_requests = 0
        self.mutate = None

    def post(self, url, **kwargs):
        assert url == "https://fixture.example/api/auth/v1/cofre/session"
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["X-JK-Cofre-Protocol"] == "1"
        assert kwargs["headers"]["X-JK-Cofre-Session-Protocol"] == "2"
        body = kwargs["json"]
        assert set(body) == {"session_grant", "machine_id", "app_version", "request_nonce"}
        assert body["machine_id"] == MACHINE
        self.calls.append(url)
        if self.status != 200:
            return Response({"code": "fixture-unavailable"}, self.status)
        try:
            encoded, signature = body["session_grant"].split(".")
            expected = hmac.new(SERVER_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
            assert hmac.compare_digest(expected, signature)
            grant = json.loads(base64.urlsafe_b64decode(encoded))
            assert grant["exp"] > self.setup.clock[0]
            assert grant["machine_hash"] == hashlib.sha256(body["machine_id"].encode()).hexdigest()
        except Exception:
            return Response({"code": "cofre_session_invalid"}, 401)
        payload = {"success": True, "protocol": 2, "request_nonce": body["request_nonce"],
                   "session_grant": body["session_grant"], "session_expires_at": grant["exp"],
                   "capability": capability(self.setup, grant)}
        if self.mutate:
            self.mutate(payload)
        return Response(payload)

    def close(self):
        pass

    def get(self, url, **kwargs):
        assert url.startswith("https://www.googleapis.com/service_accounts/v1/jwk/")
        assert kwargs["timeout"] == (3.05, 8) and kwargs["allow_redirects"] is False
        self.key_requests += 1
        key = jwt.algorithms.RSAAlgorithm.to_jwk(self.setup.signing.public_key(), as_dict=True)
        return Response({"keys": [{**key, "kid": getattr(self.setup, "key_id", "fixture"), "alg": "RS256", "use": "sig"}]})


def test_restart_restores_signed_capability_without_remote_calls(setup):
    original = cofre_auth.register_login(LOCAL, attempt(setup))
    setup.clock[0] += 60
    cofre_auth._SESSIONS.clear()
    tenant, principal = bind()
    assert tenant == COMPANY and principal["capability"] == original["capability"]
    assert set(principal) == {"client_id", "username", "scope", "exp", "capability"}


def test_expired_capability_renews_once_for_concurrent_requests_without_firebase(setup, monkeypatch):
    from backend.services import firebase_user_session
    import firebase_admin.auth
    import firebase_admin.firestore

    def forbidden(*args, **kwargs):
        raise AssertionError("Firebase must not be used for Cofre recovery")

    monkeypatch.setattr(firebase_user_session.FirebaseUserSession, "profile", forbidden)
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", forbidden)
    monkeypatch.setattr(firebase_admin.firestore, "client", forbidden)
    cofre_auth.register_login(LOCAL, attempt(setup))
    original = setup.store.load(cofre_auth._key(LOCAL))["session_grant"]
    setup.clock[0] += 8 * 3600 + 1
    cofre_auth._SESSIONS.clear()
    exchange = Exchange(setup)
    monkeypatch.setattr(remote_auth_client, "configure_requests_session", lambda *args: exchange)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: bind()[1], range(16)))
    assert len(exchange.calls) == 1
    assert all(result["exp"] > setup.clock[0] and result["scope"] == ["cofre:read"] for result in results)
    assert setup.store.load(cofre_auth._key(LOCAL))["session_grant"] == original


def test_cold_restart_after_thirteen_hours_survives_removed_rsa_key_with_zero_firebase(setup, monkeypatch):
    from backend.services import firebase_user_session
    import firebase_admin.auth
    import firebase_admin.firestore

    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError("Firebase is forbidden during rotation recovery")

    monkeypatch.setattr(firebase_user_session.FirebaseUserSession, "profile", forbidden)
    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", forbidden)
    monkeypatch.setattr(firebase_admin.firestore, "client", forbidden)
    cofre_auth.register_login(LOCAL, attempt(setup))
    original = setup.store.load(cofre_auth._key(LOCAL))
    setup.clock[0] += 13 * 3600
    setup.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    setup.key_id = "fixture-b"
    cofre_auth._SESSIONS.clear()
    cofre_auth._KEYS.clear()
    # A new gateway instance has only its stable server MAC key, no old RSA key
    # or login RAM. Its public JWKS advertises only the replacement RSA key B.
    exchange = Exchange(setup)
    monkeypatch.setattr(remote_auth_client, "configure_requests_session", lambda *args: exchange)
    monkeypatch.setattr(cofre_auth, "configure_requests_session", lambda *args: exchange)
    tenant, principal = bind(payload=identity(permissions={"full": True}))
    assert tenant == COMPANY and principal["scope"] == ["cofre:read"]
    assert jwt.get_unverified_header(principal["capability"])["kid"] == "fixture-b"
    assert set(cofre_auth._KEYS[ISSUER][1]) == {"fixture-b"}
    assert exchange.key_requests == 1 and len(exchange.calls) == 1 and forbidden_calls == []
    restored = setup.store.load(cofre_auth._key(LOCAL))
    assert restored["session_grant"] == original["session_grant"]
    assert restored["context"] == original["context"]


@pytest.mark.parametrize("with_kid", [True, False])
def test_hot_jwks_cache_refreshes_once_for_new_legitimate_signing_key(setup, with_kid):
    setup.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    setup.key_id = "fixture-b"
    value = capability(setup)
    if not with_kid:
        value = jwt.encode(jwt.decode(value, options={"verify_signature": False}), setup.signing, algorithm="RS256")
    exchange = Exchange(setup)
    principal = cofre_auth.verify_capability(value, username=USER, client_id=COMPANY,
        machine_id=MACHINE, permissions={"cofre": True}, session=exchange)
    assert principal["scope"] == ["cofre:read"] and exchange.key_requests == 1
    assert set(cofre_auth._KEYS[ISSUER][1]) == {"fixture-b"}


@pytest.mark.parametrize("with_kid", [True, False])
def test_invalid_signature_stops_after_one_trusted_jwks_refresh(setup, with_kid):
    setup.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    setup.key_id = "fixture-b"
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = jwt.decode(capability(setup), options={"verify_signature": False})
    value = jwt.encode(payload, attacker, algorithm="RS256", headers={"kid": "fixture-b"} if with_kid else None)
    exchange = Exchange(setup)
    with pytest.raises(ValueError, match="invalid_cofre_signature"):
        cofre_auth.verify_capability(value, username=USER, client_id=COMPANY,
            machine_id=MACHINE, permissions={"cofre": True}, session=exchange)
    assert exchange.key_requests == 1


def test_explicit_verification_keys_never_refresh_and_expired_tokens_never_fetch(setup):
    old_keys = dict(cofre_auth._KEYS[ISSUER][1])
    setup.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    setup.key_id = "fixture-b"
    exchange = Exchange(setup)
    with pytest.raises(ValueError):
        cofre_auth.verify_capability(capability(setup), username=USER, client_id=COMPANY,
            machine_id=MACHINE, permissions={"cofre": True}, session=exchange, keys=old_keys)
    with pytest.raises(ValueError, match="expired_cofre_capability"):
        cofre_auth.verify_capability(capability(setup, exp=setup.clock[0] - 1), username=USER,
            client_id=COMPANY, machine_id=MACHINE, permissions={"cofre": True}, session=exchange)
    assert exchange.key_requests == 0


def test_jwks_refresh_keeps_ten_key_bound_and_timeouts(setup):
    setup.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    setup.key_id = "fixture-b"
    exchange = Exchange(setup)
    original_get = exchange.get

    def too_many(url, **kwargs):
        response = original_get(url, **kwargs)
        key = response.payload["keys"][0]
        return Response({"keys": [{**key, "kid": str(number)} for number in range(11)]})

    exchange.get = too_many
    with pytest.raises(ValueError, match="invalid_cofre_public_keys"):
        cofre_auth.verify_capability(capability(setup), username=USER, client_id=COMPANY,
            machine_id=MACHINE, permissions={"cofre": True}, session=exchange)
    assert exchange.key_requests == 1


def test_transient_exchange_failure_preserves_ticket_and_jk_session(setup, monkeypatch):
    cofre_auth.register_login(LOCAL, attempt(setup))
    ticket = setup.store.load(cofre_auth._key(LOCAL))
    setup.clock[0] += 8 * 3600 + 1
    cofre_auth._SESSIONS.clear()
    exchange = Exchange(setup)
    exchange.status = 503
    monkeypatch.setattr(remote_auth_client, "configure_requests_session", lambda *args: exchange)
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "cofre_session_unavailable"
    assert setup.store.load(cofre_auth._key(LOCAL)) == ticket
    exchange.status = 200
    assert bind()[0] == COMPANY
    assert len(exchange.calls) == 2


def test_refreshes_near_expiry_before_action_without_extending_absolute_grant(setup, monkeypatch):
    first = cofre_auth.register_login(LOCAL, attempt(setup))
    setup.clock[0] = first["exp"] - 45
    exchange = Exchange(setup)
    monkeypatch.setattr(remote_auth_client, "configure_requests_session", lambda *args: exchange)
    renewed = bind()[1]
    assert len(exchange.calls) == 1 and renewed["exp"] > first["exp"]
    # The final capability ends at the original grant deadline and cannot slide.
    setup.clock[0] = setup.issued + 30 * 86400 - 45
    final = bind()[1]
    assert final["exp"] == setup.issued + 30 * 86400 and len(exchange.calls) == 2
    setup.clock[0] += 10
    assert bind()[1]["exp"] == final["exp"] and len(exchange.calls) == 2


def test_grant_license_deadline_uses_sao_paulo_end_of_day(setup):
    validity_date = datetime.fromtimestamp(setup.issued, ZoneInfo("America/Sao_Paulo")) + timedelta(days=2)
    valid_until = validity_date.strftime("%d/%m/%Y")
    end = (datetime.strptime(valid_until, "%d/%m/%Y") + timedelta(days=1)).replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    deadline = int(end.timestamp())
    assert cofre_auth.validate_session_deadline(deadline, valid_until=valid_until) == deadline
    with pytest.raises(ValueError):
        cofre_auth.validate_session_deadline(deadline + 1, valid_until=valid_until)


def test_protected_establishment_time_does_not_slide_on_restore_or_renewal(setup):
    original = setup.issued
    setup.clock[0] += 20 * 86400
    assert cofre_auth.validate_session_deadline(original + 30 * 86400,
        established_at=original) == original + 30 * 86400
    with pytest.raises(ValueError):
        cofre_auth.validate_session_deadline(setup.clock[0] + 30 * 86400, established_at=original)


def test_opaque_ticket_without_protected_login_context_is_not_authority(setup):
    cofre_auth.register_login(LOCAL, attempt(setup))
    ticket = setup.store.load(cofre_auth._key(LOCAL))
    ticket.pop("context")
    setup.store.save(cofre_auth._key(LOCAL), ticket)
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind(payload=identity(permissions={"full": True}))
    assert error.value.status_code == 503


@pytest.mark.parametrize("field,value", [("client_id", "other-company"), ("sub", "other-user"), ("machine_id", "other-machine")])
def test_persisted_grant_cannot_cross_jk_identity(setup, field, value):
    cofre_auth.register_login(LOCAL, attempt(setup))
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind(payload=identity(**{field: value}))
    assert error.value.status_code == 503


def test_invalid_jk_token_is_401_and_decoder_denial_stays_403(setup):
    cofre_auth.register_login(LOCAL, attempt(setup))

    def invalid(_):
        raise ValueError("invalid fixture jwt")

    with pytest.raises(HTTPException) as error:
        bind(decoder=invalid)
    assert error.value.status_code == 401

    def denied(_):
        raise HTTPException(403, "fixture denied")

    with pytest.raises(HTTPException) as error:
        bind(decoder=denied)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        bind(payload=identity(permissions={"cofre": False}))
    assert error.value.status_code == 403


def test_cached_permissions_can_reduce_but_not_expand_signed_scope(setup):
    cofre_auth.register_login(LOCAL, attempt(setup))
    assert bind(payload=identity(permissions={"full": True}))[1]["scope"] == ["cofre:read"]
    cofre_auth.register_login(LOCAL, attempt(setup, claims=grant_claims(setup, scope=["cofre:read", "cofre:write"])))
    assert bind()[1]["scope"] == ["cofre:read"]


def test_expired_or_missing_grant_is_503_without_logout(setup):
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503
    cofre_auth.register_login(LOCAL, attempt(setup))
    setup.clock[0] += 30 * 86400
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503


def test_v1_keeps_current_eight_hour_access_without_inventing_a_grant(setup):
    cofre_auth.register_login(LOCAL, attempt(setup, protocol=1))
    assert bind()[0] == COMPANY
    setup.clock[0] += 8 * 3600
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503


def test_new_login_and_logout_revoke_persisted_old_handles(setup):
    cofre_auth.register_login(LOCAL, attempt(setup))
    cofre_auth.register_login("next-fixture-local-session", attempt(setup))
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503
    assert bind(token="next-fixture-local-session")[0] == COMPANY
    cofre_auth.revoke("next-fixture-local-session")
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind(token="next-fixture-local-session")
    assert error.value.status_code == 503


@pytest.mark.parametrize("changes", [
    {"client_id": "another-company"}, {"username": "another-user"}, {"machine_hash": "0" * 64},
    {"scope": ["cofre:write"]}, {"scope": ["cofre:read", "cofre:read"]},
    {"extra": True}, {"valid_until": "31/02/2030"}, {"session_expires_at": 9999999999},
    {"session_expires_at": True}, {"established_at": True},
])
def test_recovery_rejects_invalid_protected_context_without_authorizing_from_opaque_grant(setup, changes):
    cofre_auth.register_login(LOCAL, attempt(setup))
    ticket = setup.store.load(cofre_auth._key(LOCAL))
    ticket["context"].update(changes)
    setup.store.save(cofre_auth._key(LOCAL), ticket)
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503


def test_untrusted_opaque_ticket_and_local_token_are_not_exchange_authority(setup):
    ticket = opaque_grant(grant_claims(setup))
    forged = ticket[:-8] + "00000000"
    for value in (forged, LOCAL):
        result = remote_auth_client.attempt_cofre_session(session_grant=value, username=USER,
            client_id=COMPANY, machine_id=MACHINE, app_version="1.0.270", environ=CONFIG,
            scope=["cofre:read"], valid_until=None, session_expires_at=setup.issued + 30 * 86400,
            established_at=setup.issued, session=Exchange(setup))
        assert not result.success


@pytest.mark.parametrize("mutation", ["nonce", "grant", "tenant", "deadline", "extra", "lease", "scope"])
def test_exchange_response_is_bound_to_nonce_identity_and_absolute_grant(setup, mutation):
    exchange = Exchange(setup)

    def alter(payload):
        if mutation == "nonce": payload["request_nonce"] = "wrong"
        if mutation == "grant": payload["session_grant"] = opaque_grant(grant_claims(setup, session_id="b" * 32))
        if mutation == "tenant": payload["capability"] = capability(setup, client_id="other-company")
        if mutation == "deadline": payload["capability"] = capability(setup, exp=setup.issued + 31 * 86400)
        if mutation == "extra": payload["extra"] = True
        if mutation == "lease": payload["session_expires_at"] += 3600
        if mutation == "scope": payload["capability"] = capability(setup, scope=["cofre:read", "cofre:write"])

    exchange.mutate = alter
    result = remote_auth_client.attempt_cofre_session(session_grant=opaque_grant(grant_claims(setup)),
        username=USER, client_id=COMPANY, machine_id=MACHINE, app_version="1.0.270", environ=CONFIG,
        scope=["cofre:read"], valid_until=None, session_expires_at=setup.issued + 30 * 86400, established_at=setup.issued,
        session=exchange, request_nonce="n" * 43)
    assert not result.success and len(exchange.calls) == 1


def test_store_chunks_are_bounded_and_names_never_contain_tokens_or_info_path(setup):
    token_key = cofre_auth._key(LOCAL)
    ticket = {"session_grant": "g" * 9000, "capability": "c" * 9000, "identity_key": "a" * 64, "context": {}}
    setup.store.save(token_key, ticket)
    assert setup.store.load(token_key) == ticket
    assert len(setup.credentials.values) > 4
    assert all(LOCAL not in target and str(setup.tmp_path) not in target for target in setup.credentials.values)
    other_root = CofreSessionStore(setup.tmp_path / "other-info", credentials=setup.credentials)
    assert other_root.load(token_key) is None


def test_interrupted_secure_publication_preserves_previous_usable_ticket(setup):
    token_key = cofre_auth._key(LOCAL)
    old = {"session_grant": "fixture-grant", "capability": "fixture-cap", "identity_key": "a" * 64, "context": {}}
    setup.store.save(token_key, old)
    setup.credentials.fail_target = setup.store._target(token_key)
    with pytest.raises(OSError):
        setup.store.save(token_key, {**old, "capability": "updated-fixture-cap"})
    assert setup.store.load(token_key) == old


def test_unavailable_secure_store_never_writes_a_plaintext_fallback(setup, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("fixture secure store unavailable")

    monkeypatch.setattr(setup.credentials, "write_scoped_secret", unavailable)
    before = set(setup.tmp_path.rglob("*"))
    cofre_auth.register_login(LOCAL, attempt(setup))
    assert bind()[0] == COMPANY  # Existing RAM authorization still works.
    cofre_auth._SESSIONS.clear()
    with pytest.raises(HTTPException) as error:
        bind()
    assert error.value.status_code == 503
    assert set(setup.tmp_path.rglob("*")) == before


def test_google_login_uses_authority_username_and_still_verifies_signed_envelope(setup):
    nonce = "n" * 43
    permissions = {key: key == "cofre" for key in PERMISSION_KEYS}
    body = {"user_data": {"username": USER, "client_id": COMPANY, "machine_id": MACHINE,
                          "name": USER, "email": "fixture@example.invalid"},
            "permissions": permissions, "policy": {"active": True, "valid_until": None, "max_machines": 1},
            "request_nonce": nonce, "cofre_access": attempt(setup).cofre_access}
    verified = {"iss": "https://securetoken.google.com/fixture-project", "aud": "fixture-project",
        "sub": remote_auth_verification.expected_uid(USER), "jk_auth_v": 1, "jk_username": USER,
        "jk_client_id": COMPANY, "jk_machine_hash": hashlib.sha256(MACHINE.encode()).hexdigest(),
        "jk_response_hash": remote_auth_verification.canonical_hash(body),
        "firebase": {"sign_in_provider": "custom"}, "auth_time": setup.clock[0]}

    class GoogleTransport:
        def post(self, url, **kwargs):
            assert url == "https://fixture.example/api/auth/v1/google-login"
            assert kwargs["headers"]["X-JK-Cofre-Protocol"] == "1"
            assert kwargs["headers"]["X-JK-Cofre-Session-Protocol"] == "2"
            assert kwargs["allow_redirects"] is False
            return Response({"success": True, "code": "authenticated", **body,
                             "identity_token": "verified-fixture-token", "expires_in": 3600})

        def close(self):
            pass

    transport = GoogleTransport()
    result = remote_auth_client.attempt_remote_google_login(credential="fictitious-google-id-token",
        machine_id=MACHINE, app_version="1.0.270", environ=CONFIG, session=transport,
        verifier=lambda *args, **kwargs: verified, now=setup.clock[0], request_nonce=nonce)
    assert result.success and result.user_data["username"] == USER
    altered = {**verified, "jk_response_hash": "0" * 64}
    rejected = remote_auth_client.attempt_remote_google_login(credential="fictitious-google-id-token",
        machine_id=MACHINE, app_version="1.0.270", environ=CONFIG, session=transport,
        verifier=lambda *args, **kwargs: altered, now=setup.clock[0], request_nonce=nonce)
    assert not rejected.success


@pytest.mark.parametrize("status", [404, 500])
def test_google_gateway_missing_or_unavailable_never_allows_legacy_fallback(setup, status):
    transport = SimpleNamespace(post=lambda *args, **kwargs: Response({}, status))
    result = remote_auth_client.attempt_remote_google_login(credential="fixture-google",
        machine_id=MACHINE, app_version="1.0.270", environ=CONFIG, session=transport)
    assert not result.success and result.allow_legacy_fallback is False
    absent = remote_auth_client.attempt_remote_google_login(credential="fixture-google",
        machine_id=MACHINE, app_version="1.0.270", environ={})
    assert not absent.success and absent.allow_legacy_fallback is False
