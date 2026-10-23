from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden, NotFound, ServiceUnavailable, TooManyRequests, Unauthorized
from google.auth.exceptions import RefreshError
from google.auth.credentials import AnonymousCredentials, Credentials
from requests import Response
from requests.adapters import HTTPAdapter
from requests.exceptions import ChunkedEncodingError, ConnectionError as RequestsConnectionError
from urllib3.exceptions import ProtocolError, ReadTimeoutError
from google.cloud.firestore_v1.services.firestore.transports.rest import FirestoreRestTransport

from backend.services import firebase_user_session as service
from backend.services.transport_security import TRUSTED_CA_ENV_KEYS


IDENTITY = {"username": "usuario", "client_id": "empresa-a", "machine_id": "pc:um"}


@pytest.fixture(autouse=True)
def isolated_tls_environment(monkeypatch):
    for key in TRUSTED_CA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


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


def test_permission_profile_recovers_pending_connection_without_status_page(setup):
    setup.verify.side_effect = [TimeoutError("certificates unavailable"), setup.claims]
    session = login(setup)
    assert session._code == "connection_failed"
    assert session._credentials is None
    profile = session.profile()
    assert profile["client_id"] == IDENTITY["client_id"]
    assert profile["permissions"] == {"cadastro": True}
    assert session._code == "ready"
    assert setup.verify.call_count == 2
    assert session._access == {}


def test_permission_retry_still_unavailable_is_bounded_and_preserves_pending_access(setup):
    setup.verify.side_effect = TimeoutError("private connection details")
    session = login(setup)
    with pytest.raises(service.FirebaseSessionError) as exc:
        session.profile()
    assert exc.value.status_code == 503
    assert exc.value.detail == "connection_failed"
    assert setup.verify.call_count == 2
    assert session._access == setup.access
    setup.getter.assert_not_called()
    assert "private" not in str(exc.value)


def test_concurrent_permission_checks_share_one_successful_bootstrap(setup):
    setup.verify.side_effect = [TimeoutError("certificates unavailable"), setup.claims]
    session = login(setup)
    barrier = Barrier(4)

    def permission_check(_):
        barrier.wait(timeout=5)
        return session.profile()

    with ThreadPoolExecutor(max_workers=4) as executor:
        profiles = list(executor.map(permission_check, range(4)))
    assert all(profile["client_id"] == IDENTITY["client_id"] for profile in profiles)
    assert setup.verify.call_count == 2
    assert session._code == "ready"
    assert session._access == {}


@pytest.mark.parametrize("deadline", ["expires_at", "session_expires_at"])
def test_permission_retry_never_extends_original_signed_deadline(setup, deadline):
    setup.verify.side_effect = TimeoutError("certificates unavailable")
    session = login(setup)
    session._access[deadline] = int(service.time.time()) - 1
    with pytest.raises(service.FirebaseSessionError) as exc:
        session.profile()
    assert exc.value.status_code == 401
    assert exc.value.detail == "session_expired"
    assert setup.verify.call_count == 1
    assert session._access == {}
    setup.getter.assert_not_called()


@pytest.mark.parametrize("invalid", ["token", "grant", "revoked"])
def test_permission_recovery_does_not_accept_invalid_identity_or_revocation(setup, invalid):
    setup.verify.side_effect = [TimeoutError("certificates unavailable"), setup.claims]
    session = login(setup)
    if invalid == "token":
        setup.claims["jk_client_id"] = "another-tenant"
    elif invalid == "grant":
        setup.grant["profile"]["machine_ids"] = ["another-machine"]
    else:
        session.revoke()
    with pytest.raises(service.FirebaseSessionError) as exc:
        session.profile()
    assert exc.value.status_code == 401
    assert session._access == {}
    assert session._code == "session_expired"
    if invalid != "grant":
        setup.getter.assert_not_called()
    if invalid == "revoked":
        assert setup.verify.call_count == 1


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


class SyntheticRestCredentials(Credentials):
    def __init__(self):
        super().__init__()
        self.token = "synthetic-before-refresh"
        self.refreshes = 0

    def refresh(self, request):
        self.refreshes += 1
        self.token = "synthetic-after-refresh"


def rest_response(status, payload):
    response = Response()
    response.status_code = status
    response.headers["Content-Type"] = "application/json; charset=UTF-8"
    response.encoding = "utf-8"
    response._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response._content_consumed = True
    return response


@pytest.fixture
def rest_sdk(monkeypatch):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    clients = []

    def create(responses):
        credentials = SyntheticRestCredentials()
        db = service.UserFirestoreClient(project="project-safe", credentials=credentials)
        session = db._firestore_api.transport._session
        sent = []

        class FixtureAdapter(HTTPAdapter):
            def send(self, request, **kwargs):
                assert len(sent) < len(responses), "Unexpected extra Firestore request"
                response = responses[len(sent)]
                response.request = request
                sent.append(response)
                return response

        session.mount("https://firestore.googleapis.com/", FixtureAdapter())
        clients.append(db)
        return SimpleNamespace(db=db, session=session, credentials=credentials, sent=sent)

    yield create
    for db in clients:
        db.close()


@pytest.mark.parametrize("status,code,error_type,mapped", [
    (401, "UNAUTHENTICATED", Unauthorized, "session_expired"),
    (403, "PERMISSION_DENIED", Forbidden, "session_expired"),
    (404, "NOT_FOUND", NotFound, "session_expired"),
    (429, "RESOURCE_EXHAUSTED", TooManyRequests, "connection_failed"),
    (503, "UNAVAILABLE", ServiceUnavailable, "connection_failed"),
])
@pytest.mark.parametrize("envelope", ["object", "array"])
def test_real_rest_error_keeps_http_classification_and_session_policy(rest_sdk, status, code, error_type, mapped, envelope):
    error = {"error": {"code": status, "status": code, "message": "Synthetic error"}}
    response = rest_response(status, [error] if envelope == "array" else error)
    original_body, original_headers = response.content, dict(response.headers)
    sdk = rest_sdk([response])
    # Test the terminal HTTP error directly; refresh behavior is exercised below.
    sdk.session._max_refresh_attempts = 0

    with pytest.raises(error_type) as raised:
        sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert raised.value.code == status
    assert len(sdk.sent) == 1
    assert sdk.credentials.refreshes == 0
    assert response.status_code == status
    assert response.content == original_body
    assert dict(response.headers) == original_headers
    session = service.FirebaseUserSession(IDENTITY)
    session._record_failure(raised.value)
    assert session._code == mapped
    assert session.status()["ready"] is False


def test_real_rest_401_array_keeps_normal_refresh_before_success(rest_sdk):
    unauthorized = rest_response(401, [{"error": {
        "code": 401, "status": "UNAUTHENTICATED", "message": "Synthetic expired ID token",
    }}])
    success = rest_response(200, [{"found": {
        "name": "projects/project-safe/databases/(default)/documents/items/one",
        "fields": {"label": {"stringValue": "line  one"}},
    }}])
    sdk = rest_sdk([unauthorized, success])

    snapshot = sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert snapshot.exists
    assert snapshot.to_dict() == {"label": "line  one"}
    assert sdk.credentials.refreshes == 1
    assert len(sdk.sent) == 2
    assert unauthorized.status_code == 401
    assert success.status_code == 200


def test_repeated_rest_401_arrays_reject_after_bounded_refresh(rest_sdk):
    responses = [rest_response(401, [{"error": {
        "code": 401, "status": "UNAUTHENTICATED", "message": "Synthetic rejected ID token",
    }}]) for _ in range(3)]
    sdk = rest_sdk(responses)

    with pytest.raises(Unauthorized):
        sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert sdk.credentials.refreshes == 2
    assert len(sdk.sent) == 3


def broken_rest_body(kind):
    response = Response()
    response.status_code = 401
    response.headers["Content-Type"] = "application/json; charset=UTF-8"
    response.encoding = "utf-8"

    class BrokenRaw:
        def stream(self, *args, **kwargs):
            yield b"[{"
            if kind == "chunked":
                raise ProtocolError("Synthetic truncated error body")
            raise ReadTimeoutError(None, None, "Synthetic error body timeout")

        def close(self):
            pass

        def release_conn(self):
            pass

    response.raw = BrokenRaw()
    return response


@pytest.mark.parametrize("kind", ["chunked", "timeout"])
def test_rest_401_body_transport_failure_still_allows_refresh_then_success(rest_sdk, kind):
    failed_body = broken_rest_body(kind)
    success = rest_response(200, [{"found": {
        "name": "projects/project-safe/databases/(default)/documents/items/one",
        "fields": {"ok": {"booleanValue": True}},
    }}])
    sdk = rest_sdk([failed_body, success])

    snapshot = sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert snapshot.to_dict() == {"ok": True}
    assert sdk.credentials.refreshes == 1
    assert len(sdk.sent) == 2
    assert failed_body.status_code == 401


@pytest.mark.parametrize("kind,error_type", [
    ("chunked", ChunkedEncodingError), ("timeout", RequestsConnectionError),
])
def test_repeated_rest_401_body_failure_stops_after_bounded_refresh(rest_sdk, kind, error_type):
    sdk = rest_sdk([broken_rest_body(kind) for _ in range(3)])

    with pytest.raises(error_type):
        sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert sdk.credentials.refreshes == 2
    assert len(sdk.sent) == 3


def test_rest_error_hook_does_not_swallow_programming_errors(rest_sdk):
    response = rest_response(401, {})
    response.json = Mock(side_effect=TypeError("Synthetic programming error"))
    sdk = rest_sdk([response])

    with pytest.raises(TypeError, match="Synthetic programming error"):
        sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert sdk.credentials.refreshes == 0
    assert len(sdk.sent) == 1


@pytest.mark.parametrize("payload", [
    [],
    [{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "one"}},
     {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "two"}}],
    [{"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "Mismatched HTTP"}}],
    [{"error": {"code": True, "status": "PERMISSION_DENIED", "message": "Invalid code"}}],
    [{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": {"unexpected": "object"}}}],
    [{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Invalid details", "details": ["invalid"]}}],
    [{"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Unexpected envelope"}, "other": 1}],
    b"not JSON",
])
def test_malformed_rest_error_is_not_unwrapped_and_never_grants_access(rest_sdk, payload):
    response = rest_response(403, payload)
    original_body = response.content
    sdk = rest_sdk([response])

    with pytest.raises((Forbidden, AttributeError)) as raised:
        sdk.db.collection("items").document("one").get(timeout=8, retry=None)

    assert len(sdk.sent) == 1
    assert sdk.credentials.refreshes == 0
    assert response.content == original_body
    if not isinstance(payload, bytes):
        assert response.json() == payload
    session = service.FirebaseUserSession(IDENTITY)
    session._record_failure(raised.value)
    assert session.status()["ready"] is False


@pytest.mark.parametrize("operation", ["get", "missing", "query", "aggregate"])
@pytest.mark.parametrize("padding", ["", " \r\n\t"])
def test_real_rest_success_streams_are_unchanged_with_error_hook(rest_sdk, operation, padding):
    document = {"name": "projects/project-safe/databases/(default)/documents/items/one",
                "fields": {"label": {"stringValue": "line  one"}}}
    payloads = {
        "get": [{"found": document}],
        "missing": [{"missing": document["name"]}],
        "query": [{"document": document}],
        "aggregate": [{"result": {"aggregateFields": {"count": {"integerValue": "3"}}}}],
    }
    body = (padding + json.dumps(payloads[operation]) + padding).encode()
    response = rest_response(200, body)
    sdk = rest_sdk([response])

    if operation == "get":
        assert sdk.db.collection("items").document("one").get(timeout=8, retry=None).to_dict() == {"label": "line  one"}
    elif operation == "missing":
        assert not sdk.db.collection("items").document("one").get(timeout=8, retry=None).exists
    elif operation == "query":
        assert [item.to_dict() for item in sdk.db.collection("items").stream(timeout=8, retry=None)] == [{"label": "line  one"}]
    else:
        result = sdk.db.collection("items").count(alias="count").get(timeout=8, retry=None)
        assert result[0][0].value == 3
    assert response.content == body
    assert response.status_code == 200
    assert response.json() == payloads[operation]
    assert sdk.credentials.refreshes == 0
    assert len(sdk.sent) == 1
