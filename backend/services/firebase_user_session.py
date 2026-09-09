"""In-memory Firebase user credentials, isolated by authenticated local session.

Firebase ID tokens deliberately use Firestore's REST transport: gRPC requires IAM
credentials and would not enforce the user Security Rules used by these grants.
"""
from __future__ import annotations

import copy
import hashlib
import re
import threading
import time
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Mapping

import requests
from fastapi import HTTPException
from google.api_core.exceptions import Forbidden, NotFound, Unauthorized
from google.api_core.rest_streaming import ResponseIterator
from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.rest import FirestoreRestInterceptor, FirestoreRestTransport
from google.oauth2.id_token import verify_firebase_token

from backend.services.remote_auth_verification import expected_uid, machine_hash
from backend.services.transport_security import configure_requests_session


_MAX_SESSION_SECONDS = 8 * 3600
_TIMEOUT = 8
_SESSIONS: dict[str, "FirebaseUserSession"] = {}
_LOCK = threading.RLock()
_CURRENT: ContextVar[str | None] = ContextVar("firebase_user_session", default=None)


class FirebaseSessionError(HTTPException):
    def __init__(self, code: str = "session_expired"):
        self.code = code
        super().__init__(status_code=401 if code == "session_expired" else 503, detail=code)


class _CertificateRequest(GoogleAuthRequest):
    def __call__(self, url, method="GET", body=None, headers=None, **kwargs):
        kwargs.update(timeout=(3.05, _TIMEOUT), allow_redirects=False)
        return super().__call__(url, method=method, body=body, headers=headers, **kwargs)


def _verify_token(token: str, project_id: str) -> Mapping[str, Any]:
    with requests.Session() as session:
        configure_requests_session(session)
        return verify_firebase_token(
            token, _CertificateRequest(session=session), audience=project_id,
            clock_skew_in_seconds=0,
        )


class FirebaseUserCredentials(Credentials):
    """Refreshable ID token; never persists credentials or exposes them in repr."""

    def __init__(self, access: Mapping[str, Any], identity: Mapping[str, str]):
        super().__init__()
        self._project_id = access["project_id"]
        self._grant_id = access["grant_id"]
        self._identity = dict(identity)
        self._refresh_token = access["refresh_token"]
        self._api_key = access["api_key"]
        self._session_expires_at = access["session_expires_at"]
        self._refresh_lock = threading.RLock()
        self._revoked = False
        self._accept_token(access["id_token"], access["expires_at"])

    def __repr__(self):
        return "<FirebaseUserCredentials restricted>"

    def _assert_live(self):
        if self._revoked or time.time() >= self._session_expires_at:
            self.revoke()
            raise RefreshError("session_expired")

    def revoke(self):
        self._revoked = True
        self.token = None
        self._refresh_token = ""
        self._api_key = ""

    def _accept_token(self, token, reported_expiry):
        self._assert_live()
        claims = _verify_token(token, self._project_id)
        now = time.time()
        checks = {
            "aud": self._project_id,
            "iss": f"https://securetoken.google.com/{self._project_id}",
            "sub": expected_uid(self._identity["username"]),
            "jk_firebase_v": 1,
            "jk_firebase_grant": self._grant_id,
            "jk_client_id": self._identity["client_id"],
            "jk_username": self._identity["username"],
            "jk_machine_hash": machine_hash(self._identity["machine_id"]),
        }
        if not isinstance(claims, Mapping) or any(claims.get(k) != v for k, v in checks.items()):
            self.revoke()
            raise RefreshError("session_expired")
        expires = claims.get("exp")
        if (isinstance(expires, bool) or not isinstance(expires, (int, float))
                or not now < expires <= now + 3700
                or not isinstance(reported_expiry, (int, float))
                or isinstance(reported_expiry, bool) or reported_expiry <= now):
            self.revoke()
            raise RefreshError("session_expired")
        self.token = token
        self.expiry = datetime.fromtimestamp(min(expires, reported_expiry, self._session_expires_at), timezone.utc).replace(tzinfo=None)

    def refresh(self, request):
        del request  # Never allow the caller to choose a refresh endpoint/transport.
        with self._refresh_lock:
            self._assert_live()
            try:
                with requests.Session() as session:
                    configure_requests_session(session)
                    response = session.post(
                        "https://securetoken.googleapis.com/v1/token",
                        params={"key": self._api_key},
                        data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
                        timeout=(3.05, _TIMEOUT), allow_redirects=False,
                    )
                    if response.status_code in {400, 401, 403}:
                        self.revoke()
                        raise RefreshError("session_expired")
                    if response.status_code != 200 or len(response.content) > 64 * 1024:
                        raise RefreshError("connection_failed")
                    payload = response.json()
                token = payload.get("id_token")
                refresh_token = payload.get("refresh_token")
                if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                        or not isinstance(refresh_token, str) or not 1 <= len(refresh_token) <= 16384):
                    self.revoke()
                    raise RefreshError("session_expired")
                self._accept_token(token, time.time() + int(payload["expires_in"]))
                self._refresh_token = refresh_token
            except RefreshError:
                raise
            except (TypeError, ValueError, KeyError):
                self.revoke()
                raise RefreshError("session_expired") from None
            except Exception:
                raise RefreshError("connection_failed") from None

    def before_request(self, request, method, url, headers):
        self._assert_live()
        super().before_request(request, method, url, headers)


class _FirebaseResponseIterator(ResponseIterator):
    def _process_chunk(self, chunk):
        # google-api-core 2.30's REST parser rejects standalone whitespace
        # chunks outside the response array, although JSON permits them.
        # Keep this compatibility fix local to our Firebase REST client.
        if self._level == 0:
            chunk = chunk.lstrip()
        if chunk:
            super()._process_chunk(chunk)


class _FirebaseRestInterceptor(FirestoreRestInterceptor):
    @staticmethod
    def _stream(response):
        return _FirebaseResponseIterator(response._response, response._response_message_cls)

    def post_run_query(self, response):
        return self._stream(response)

    def post_batch_get_documents(self, response):
        return self._stream(response)

    def post_run_aggregation_query(self, response):
        return self._stream(response)


class UserFirestoreClient(Client):
    @property
    def _firestore_api(self):
        if self._firestore_api_internal is None:
            api = FirestoreClient(
                transport=FirestoreRestTransport(
                    credentials=self._credentials, interceptor=_FirebaseRestInterceptor(),
                ),
                client_options={"api_endpoint": "firestore.googleapis.com"},
            )
            configure_requests_session(api.transport._session)
            api.transport._session.max_redirects = 0
            self._firestore_api_internal = api
        return self._firestore_api_internal


class FirebaseUserSession:
    def __init__(self, identity, access=None):
        self.identity = dict(identity)
        self._access = dict(access or {})
        self._credentials = None
        self._db = None
        self._code = "server_setup_required"
        self._lock = threading.RLock()

    def __repr__(self):
        return "<FirebaseUserSession restricted>"

    def revoke(self):
        with self._lock:
            self._code = "session_expired"
            self._access = {}
            if self._credentials:
                self._credentials.revoke()

    def _check(self):
        if self._code == "session_expired":
            raise FirebaseSessionError("session_expired")
        if not self._credentials:
            raise FirebaseSessionError(self._code)
        try:
            self._credentials._assert_live()
        except RefreshError:
            self.revoke()
            raise FirebaseSessionError("session_expired") from None

    def connect(self):
        with self._lock:
            self._connect()

    def _connect(self):
        if self._code == "session_expired":
            self.revoke()
            return
        if not self._access:
            return
        try:
            access = self._access
            if (set(access) != {"protocol", "project_id", "grant_id", "id_token", "refresh_token", "expires_at", "session_expires_at", "api_key"}
                    or type(access["protocol"]) is not int or access["protocol"] != 1
                    or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", str(access["project_id"]))
                    or not re.fullmatch(r"[a-f0-9]{64}", str(access["grant_id"]))
                    or not all(isinstance(access[k], str) and 1 <= len(access[k]) <= 16384 for k in ("id_token", "refresh_token", "api_key"))
                    or type(access["session_expires_at"]) is not int
                    or type(access["expires_at"]) is not int
                    or access["expires_at"] <= time.time()
                    or not time.time() < access["session_expires_at"] <= time.time() + _MAX_SESSION_SECONDS):
                raise FirebaseSessionError("session_expired")
            self._credentials = FirebaseUserCredentials(access, self.identity)
            self._db = UserFirestoreClient(project=access["project_id"], credentials=self._credentials)
            self.profile()
            self._code = "ready"
        except Exception as exc:
            self._record_failure(exc)
        finally:
            # Retain a pending bootstrap only in memory while a transient
            # certificate/connection failure prevents Credentials creation.
            # A status retry revalidates the original signed expiry deadlines.
            if self._credentials is not None or self._code != "connection_failed":
                self._access = {}

    def _record_failure(self, exc):
        if isinstance(exc, (Forbidden, Unauthorized, NotFound, ValueError)) or getattr(exc, "code", None) == "session_expired" or isinstance(exc, RefreshError) and "session_expired" in str(exc):
            self.revoke()
        else:
            self._code = "connection_failed"

    @property
    def db(self):
        self._check()
        return self._db

    def profile(self):
        self._check()
        try:
            snapshot = self._db.collection("jk_sistema_firebase_grants").document(self._credentials._grant_id).get(timeout=_TIMEOUT, retry=None)
            grant = snapshot.to_dict() if snapshot.exists else None
            profile = grant.get("profile") if isinstance(grant, dict) else None
            if (not isinstance(profile, dict) or grant.get("uid") != expected_uid(self.identity["username"])
                    or any(grant.get(k) != v or profile.get(k) != v for k, v in self.identity.items())
                    or profile.get("active") is not True
                    or not isinstance(profile.get("permissions"), dict)
                    or not isinstance(profile.get("machine_ids"), list)
                    or self.identity["machine_id"] not in profile["machine_ids"]):
                raise FirebaseSessionError("session_expired")
            allowed = ("username", "name", "email", "client_id", "machine_id", "machine_ids", "permissions", "active", "valid_until", "max_machines")
            self._code = "ready"
            return {**{k: copy.deepcopy(profile[k]) for k in allowed if k in profile}, "source": "user_session"}
        except Exception as exc:
            self._record_failure(exc)
            raise FirebaseSessionError(self._code) from None

    def status(self):
        with self._lock:
            if self._code == "connection_failed" and self._credentials is None and self._access:
                self._connect()
        try:
            self.profile()
        except FirebaseSessionError:
            pass
        ready = self._code == "ready"
        return {"success": ready, "configured": bool(self._credentials), "ready": ready,
                "source": "user_session", "code": self._code, "restart_required": False,
                "can_migrate": False, "replacement_required": False}


def _key(local_token: str) -> str:
    return hashlib.sha256(local_token.encode("utf-8")).hexdigest()


def register_login(local_token: str, attempt) -> FirebaseUserSession:
    """Only call after the remote login response has been signature-verified."""
    if not local_token or not getattr(attempt, "success", False):
        raise FirebaseSessionError("session_expired")
    user = attempt.user_data
    identity = {k: str(user.get(k) or "").strip() for k in ("username", "client_id", "machine_id")}
    if not all(identity.values()):
        raise FirebaseSessionError("session_expired")
    session = FirebaseUserSession(identity, getattr(attempt, "firebase_access", None))
    with _LOCK:
        for previous in _SESSIONS.values():
            if previous.identity == identity:
                previous.revoke()
        _SESSIONS[_key(local_token)] = session
    session.connect()
    return session


def bind_request(local_token: str, payload: Mapping[str, Any]) -> Token:
    key = _key(local_token) if local_token else None
    with _LOCK:
        session = _SESSIONS.get(key)
    if session:
        identity = {"username": str(payload.get("sub") or ""), "client_id": str(payload.get("client_id") or ""), "machine_id": str(payload.get("machine_id") or "")}
        if identity != session.identity:
            raise FirebaseSessionError("session_expired")
    elif payload.get("jk_firebase"):
        # A backend restart loses in-memory credentials; never fall back to Admin.
        session = FirebaseUserSession({"username": str(payload.get("sub") or ""), "client_id": str(payload.get("client_id") or ""), "machine_id": str(payload.get("machine_id") or "")})
        session._code = "session_expired"
        with _LOCK:
            _SESSIONS[key] = session
    return _CURRENT.set(key if session else None)


def reset_request(token: Token) -> None:
    _CURRENT.reset(token)


def current(client_id: str | None = None) -> FirebaseUserSession | None:
    with _LOCK:
        session = _SESSIONS.get(_CURRENT.get())
    if session and client_id is not None and client_id != session.identity["client_id"]:
        raise FirebaseSessionError("session_expired")
    return session


def status(client_id: str | None = None) -> dict[str, Any] | None:
    session = current(client_id)
    return session.status() if session else None


def revoke(local_token: str) -> None:
    with _LOCK:
        session = _SESSIONS.get(_key(local_token))
        if session:
            session.revoke()
