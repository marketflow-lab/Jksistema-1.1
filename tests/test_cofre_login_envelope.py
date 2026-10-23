"""Real signed login envelopes bind opaque Cofre credentials before persistence."""
import copy
import time
from types import SimpleNamespace
from unittest.mock import Mock

import bcrypt
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth.crypt import RSASigner

from backend.services import cofre_auth, remote_auth_client, remote_auth_verification as verification
from cloud.auth_gateway.app.cofre_capability import CofreCapabilityIssuer
from cloud.auth_gateway.app.contracts import GatewayLoginRequest
from cloud.auth_gateway.app.domain import AuthenticationService

PROJECT = "fixture-project"
ISSUER = "fixture-signer@fixture-project.iam.gserviceaccount.com"
USER, TENANT, MACHINE, NONCE = "fixture-user", "fixture-company", "fixture-pc", "n" * 43


@pytest.fixture(scope="module")
def envelope():
    now = int(time.time())
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    record = {"username": USER, "client_id": TENANT, "active": True, "permissions": {"cofre": True},
              "machine_ids": [MACHINE], "max_machines": 1,
              "password_hash": bcrypt.hashpw(b"fixture-password", bcrypt.gensalt(4)).decode()}

    class Identity:
        def issue(self, uid, claims):
            return jwt.encode({"iss": "https://securetoken.google.com/" + PROJECT,
                "aud": PROJECT, "sub": uid, "auth_time": now, "iat": now, "exp": now + 3600,
                "firebase": {"sign_in_provider": "custom"}, **claims}, pem, algorithm="RS256"), 3600

    identity = Identity()
    repository = Mock()
    repository.get_user.return_value = record
    repository.claim_machine.return_value = record
    issuing = CofreCapabilityIssuer(RSASigner.from_string(pem, key_id="fixture-key"), ISSUER,
        session_secret=b"fixture-server-only-mac-material-32-bytes", clock=lambda: now)
    payload = AuthenticationService(repository, identity, "1.0.124", cofre_capabilities=issuing).authenticate(
        GatewayLoginRequest(username=USER, password="fixture-password", machine_id=MACHINE,
                            app_version="1.0.272", request_nonce=NONCE), cofre_protocol=2)
    configuration = remote_auth_client.load_remote_auth_configuration({
        "JK_REMOTE_AUTH_URL": "https://auth.example.test/api/auth/v1/login",
        "JK_REMOTE_AUTH_PROJECT_ID": PROJECT, "JK_REMOTE_AUTH_MODE": "required"})
    return SimpleNamespace(now=now, public=private.public_key(), identity=identity,
                           payload=payload, configuration=configuration)


def verify(envelope, payload, monkeypatch):
    monkeypatch.setenv("JK_COFRE_ISSUER_EMAIL", ISSUER)
    monkeypatch.setattr(cofre_auth, "_public_keys", lambda *args, **kwargs: {"fixture-key": envelope.public})
    validated = verification.validate_success_payload(payload, USER, MACHINE, NONCE, "authenticated")

    def signed_identity(token, _request, **kwargs):
        return jwt.decode(token, envelope.public, algorithms=["RS256"], audience=kwargs["audience"],
                          issuer="https://securetoken.google.com/" + PROJECT)

    verification.verify_signed_response(validated, envelope.configuration, None,
                                        verifier=signed_identity, now=envelope.now)
    return validated


def resign(envelope, payload):
    material = {name: payload[name] for name in
                ("user_data", "permissions", "policy", "request_nonce", "cofre_access")}
    payload["identity_token"], _ = envelope.identity.issue(verification.expected_uid(USER), {
        "jk_auth_v": 1, "jk_username": USER, "jk_client_id": TENANT,
        "jk_machine_hash": verification.machine_hash(MACHINE),
        "jk_response_hash": verification.canonical_hash(material)})


def test_normal_signed_login_authenticates_opaque_grant_and_deadline(envelope, monkeypatch):
    validated = verify(envelope, copy.deepcopy(envelope.payload), monkeypatch)
    assert validated["cofre_access"] == envelope.payload["cofre_access"]
    assert jwt.get_unverified_header(validated["cofre_access"]["session_grant"])["alg"] == "HS256"


@pytest.mark.parametrize("field", ["session_grant", "session_expires_at", "capability", "google_email"])
def test_tampering_is_rejected_by_outer_signature_binding_before_persistence(envelope, monkeypatch, field):
    payload = copy.deepcopy(envelope.payload)
    if field == "session_expires_at":
        payload["cofre_access"][field] += 1
    elif field == "google_email":
        payload["user_data"][field] = "other@example.test"
    else:
        payload["cofre_access"][field] = "forged-fixture-value"
    with pytest.raises(ValueError, match="invalid_response_claim"):
        verify(envelope, payload, monkeypatch)


def test_even_signed_deadline_cannot_exceed_absolute_lease(envelope, monkeypatch):
    payload = copy.deepcopy(envelope.payload)
    payload["cofre_access"]["session_expires_at"] = envelope.now + 31 * 86400
    resign(envelope, payload)
    with pytest.raises(ValueError, match="invalid_cofre_session_deadline"):
        verify(envelope, payload, monkeypatch)


def test_opaque_grant_cannot_be_used_as_rsa_capability(envelope, monkeypatch):
    payload = copy.deepcopy(envelope.payload)
    payload["cofre_access"]["capability"] = payload["cofre_access"]["session_grant"]
    resign(envelope, payload)
    with pytest.raises(ValueError, match="invalid_cofre_header"):
        verify(envelope, payload, monkeypatch)
