from contextvars import Context
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden
from google.auth.exceptions import RefreshError
from google.auth.credentials import AnonymousCredentials
from requests import Response
from google.cloud.firestore_v1.services.firestore.transports.rest import FirestoreRestTransport

from backend.services import firebase_user_session as service


IDENTITY = {"username": "usuario", "client_id": "empresa-a", "machine_id": "pc:um"}


@pytest.fixture
def setup(monkeypatch):
    now = service.time.time()
    access = {"protocol": 1, "project_id": "project-safe", "grant_id": "a" * 64,
              "id_token": "private-id-token", "refresh_token": "private-refresh-token",
              "api_key": "public-web-api-key", "expires_at": int(now + 3500),
              "session_expires_at": int(now + 28000)}
    claims = {"aud": access["project_id"], "iss": "https://securetoken.google.com/project-safe",
              "sub": service.expected_uid(IDENTITY["username"]), "jk_firebase_v": 1,
              "jk_firebase_grant": access["grant_id"], "jk_client_id": IDENTITY["client_id"],
              "jk_username": IDENTITY["username"], "jk_machine_hash": service.machine_hash(IDENTITY["machine_id"]),
              "exp": int(now + 3600)}
    verify = Mock(return_value=claims)
    monkeypatch.setattr(service, "_verify_token", verify)
    grant = {**IDENTITY, "uid": claims["sub"], "profile": {
        **IDENTITY, "machine_ids": [IDENTITY["machine_id"]], "permissions": {"cadastro": True},
        "active": True, "valid_until": None, "password_hash": "never-return"},
        "session_expires_at": access["session_expires_at"]}
    snapshot = SimpleNamespace(exists=True, to_dict=lambda: grant)
    getter = Mock(return_value=snapshot)
    db = Mock()
    db.collection.return_value.document.return_value.get = getter
    monkeypatch.setattr(service, "UserFirestoreClient", Mock(return_value=db))
    service._SESSIONS.clear()
    marker = service._CURRENT.set(None)
    yield SimpleNamespace(access=access, claims=claims, verify=verify, grant=grant, getter=getter, db=db)
    service._SESSIONS.clear()
    service._CURRENT.reset(marker)


def login(setup, token="local-secret", identity=None):
    attempt = SimpleNamespace(success=True, user_data=identity or IDENTITY, firebase_access=setup.access)
    return service.register_login(token, attempt)


def payload(**overrides):
    return {"sub": IDENTITY["username"], "client_id": IDENTITY["client_id"],
            "machine_id": IDENTITY["machine_id"], "jk_firebase": 1, **overrides}


def test_login_probes_grant_and_only_exposes_safe_profile(setup):
    session = login(setup)
    assert session.status()["ready"] is True
    assert "password_hash" not in session.profile()
    assert "private" not in repr(session) + repr(session._credentials) + repr(session.status())
    setup.getter.assert_called_with(timeout=8, retry=None)
    assert "local-secret" not in service._SESSIONS
    assert session._access == {}


def test_revoked_grant_cannot_fall_back_or_report_ready(setup):
    session = login(setup)
    setup.getter.side_effect = Forbidden("denied")
    assert session.status()["code"] == "session_expired"
    marker = service.bind_request("local-secret", payload())
    try:
        assert service.current() is session
        with pytest.raises(service.FirebaseSessionError) as exc:
            service.current().db
        assert exc.value.status_code == 401
    finally:
        service.reset_request(marker)


@pytest.mark.parametrize("field,value", [("sub", "another"), ("client_id", "empresa-b"), ("machine_id", "pc:dois")])
def test_cannot_bind_other_user_tenant_or_machine(setup, field, value):
    login(setup)
    with pytest.raises(service.FirebaseSessionError):
        service.bind_request("local-secret", payload(**{field: value}))


def test_context_isolation_and_missing_session_fail_closed(setup):
    session = login(setup)
    marker = service.bind_request("local-secret", payload())
    try:
        assert service.current("empresa-a") is session
        assert Context().run(service.current) is None
        with pytest.raises(service.FirebaseSessionError):
            service.current("empresa-b")
        other = service.bind_request("unknown-token", payload())
        assert service.current().status()["code"] == "session_expired"
        service.reset_request(other)
        assert service.current() is session
    finally:
        service.reset_request(marker)
    marker = service.bind_request("legacy", {"sub": "legacy"})
    assert service.current() is None
    service.reset_request(marker)


def test_new_login_revokes_previous_same_identity(setup):
    previous = login(setup, "first")
    newer = login(setup, "second")
    assert previous.status()["code"] == "session_expired"
    assert newer.status()["ready"]


def test_deadline_applies_even_when_id_token_has_not_expired(setup):
    session = login(setup)
    session._credentials._session_expires_at = service.time.time() - 1
    with pytest.raises(RefreshError, match="session_expired"):
        session._credentials.before_request(None, "GET", "https://firestore.googleapis.com", {})
    assert session.status()["code"] == "session_expired"


def test_timeout_is_sanitized_and_status_retries_grant(setup):
    setup.getter.side_effect = TimeoutError("sensitive response")
    session = login(setup)
    assert session.status()["code"] == "connection_failed"
    assert "sensitive" not in repr(session.status())
    setup.getter.side_effect = None
    assert session.status()["ready"]


@pytest.mark.parametrize("claim", ["aud", "iss", "sub", "jk_firebase_grant", "jk_client_id", "jk_machine_hash"])
def test_signed_id_token_identity_is_checked(setup, claim):
    setup.claims[claim] = "wrong"
    assert login(setup).status()["ready"] is False
    setup.getter.assert_not_called()


def test_refresh_uses_fixed_tls_endpoint_rotates_token_without_extending_session(setup, monkeypatch):
    session = login(setup)
    creds = session._credentials
    original_deadline = creds._session_expires_at
    response = SimpleNamespace(status_code=200, content=b"{}", json=lambda: {
        "id_token": "new-id-token", "refresh_token": "new-refresh-token", "expires_in": "3600"})
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.post.return_value = response
    monkeypatch.setattr(service.requests, "Session", lambda: http)
    creds.refresh(None)
    assert creds.token == "new-id-token"
    assert creds._refresh_token == "new-refresh-token"
    assert creds._session_expires_at == original_deadline
    assert http.verify is True
    args, kwargs = http.post.call_args
    assert args == ("https://securetoken.googleapis.com/v1/token",)
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (3.05, 8)
    setup.verify.assert_called_with("new-id-token", "project-safe")


def test_real_sdk_uses_rest_transport_without_admin_credentials(monkeypatch):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    credentials = Mock(spec=service.Credentials)
    credentials.universe_domain = "googleapis.com"
    # This creates real SDK transports but makes no HTTP request.
    db = service.UserFirestoreClient(project="project-safe", credentials=credentials)
    assert isinstance(db._firestore_api.transport, FirestoreRestTransport)
    assert db._firestore_api.transport._credentials is credentials
    assert db.collection("jk_sistema_firebase_grants").document("abc").path == "jk_sistema_firebase_grants/abc"
    assert db._firestore_api.transport._session.verify is True


def test_grant_cannot_substitute_another_tenant_profile(setup):
    setup.grant["profile"]["client_id"] = "empresa-b"
    session = login(setup)
    assert session.status()["code"] == "session_expired"
    assert session._credentials.token is None
    assert session._credentials._refresh_token == ""


def test_refresh_rejects_redirect_and_does_not_accept_response_token(setup, monkeypatch):
    creds = login(setup)._credentials
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.post.return_value = SimpleNamespace(status_code=302, content=b"{}")
    monkeypatch.setattr(service.requests, "Session", lambda: http)
    with pytest.raises(RefreshError, match="connection_failed"):
        creds.refresh(None)
    assert creds.token == "private-id-token"


def test_untrusted_tls_configuration_prevents_refresh_network(setup, monkeypatch):
    creds = login(setup)._credentials
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(service.requests, "Session", lambda: http)
    monkeypatch.setenv("JK_IA_CA_BUNDLE", "nonexistent-ca-for-firebase-test.pem")
    with pytest.raises(RefreshError, match="connection_failed"):
        creds.refresh(None)
    http.post.assert_not_called()


def test_status_retries_initial_certificate_outage_and_erases_pending_copy(setup):
    setup.verify.side_effect = [TimeoutError("certificates unavailable"), setup.claims]
    session = login(setup)
    assert session._code == "connection_failed"
    assert session._credentials is None
    assert session._access["refresh_token"] == "private-refresh-token"
    assert "private" not in repr(session)
    result = session.status()
    assert result["ready"] is True
    assert session._access == {}
    assert setup.verify.call_count == 2
    assert "private" not in repr(result)


@pytest.mark.parametrize("replace_login", [False, True])
def test_revocation_erases_pending_credentials_and_never_retries(setup, replace_login):
    setup.verify.side_effect = TimeoutError("certificates unavailable")
    previous = login(setup)
    assert previous._access
    if replace_login:
        login(setup, "replacement-local-token")
    else:
        service.revoke("local-secret")
    call_count = setup.verify.call_count
    assert previous._access == {}
    assert previous.status()["code"] == "session_expired"
    assert setup.verify.call_count == call_count


@pytest.mark.parametrize("deadline", ["expires_at", "session_expires_at"])
def test_pending_bootstrap_expires_before_retry_network(setup, deadline):
    setup.verify.side_effect = TimeoutError("certificates unavailable")
    session = login(setup)
    session._access[deadline] = int(service.time.time()) - 1
    assert session.status()["code"] == "session_expired"
    assert session._access == {}
    assert setup.verify.call_count == 1


@pytest.mark.parametrize("padding", ["\n", " \r\n\t", ""])
def test_real_rest_sdk_query_accepts_json_whitespace_after_array(monkeypatch, padding):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    db = service.UserFirestoreClient(project="project-safe", credentials=AnonymousCredentials())
    response = Response()
    response.status_code = 200
    response.encoding = "utf-8"
    response._content = (padding + '[{"document":{"name":"projects/project-safe/databases/(default)/documents/items/one",'
                         '"fields":{"label":{"stringValue":"line  one"}}}}]' + padding).encode()
    response._content_consumed = True
    request = Mock(return_value=response)
    monkeypatch.setattr(db._firestore_api.transport._session, "request", request)
    documents = list(db.collection("items").stream(timeout=8, retry=None))
    assert [(document.id, document.to_dict()) for document in documents] == [("one", {"label": "line  one"})]
    assert request.call_count == 1
