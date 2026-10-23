"""HTTPS transport for the central JK Sistema authentication gateway."""

from __future__ import annotations

import os
import secrets
import time
from typing import Callable, Mapping

import requests
from google.oauth2.id_token import verify_firebase_token

from backend.services.remote_auth_contracts import (
    AUTH_PROTOCOL_VERSION,
    INTEGRITY_MESSAGE,
    PERMISSION_KEYS,
    REMOTE_ERROR_MESSAGES,
    UNAVAILABLE_MESSAGE,
    RemoteAuthAttempt,
    RemoteAuthConfiguration,
    RemoteAuthState,
    load_remote_auth_configuration,
)
from backend.services.remote_auth_verification import (
    canonical_hash as _canonical_hash,
    expected_uid as _expected_uid,
    machine_hash as _machine_hash,
    validate_success_payload,
    verify_signed_response,
)
from backend.services.transport_security import configure_requests_session


MAX_RESPONSE_BYTES = 64 * 1024


def _failure_code(response: requests.Response) -> str:
    if len(response.content or b"") > MAX_RESPONSE_BYTES:
        return ""
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return ""
    return str(payload.get("code") or "").strip() if isinstance(payload, dict) else ""


def _attempt_remote_request(
    *,
    configuration: RemoteAuthConfiguration,
    request_url: str,
    request_body: dict[str, str],
    username_normalized: str | None,
    machine_normalized: str,
    app_version: str,
    values: Mapping[str, str],
    session: requests.Session | None,
    verifier: Callable[..., Mapping],
    now: float | None,
    expected_code: str,
    allow_outage_fallback: bool,
    request_nonce: str,
) -> RemoteAuthAttempt:
    owns_session = session is None
    base_session = session or requests.Session()
    try:
        request_session = base_session if session is not None else configure_requests_session(base_session, values)
    except Exception:
        if owns_session:
            base_session.close()
        return RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, INTEGRITY_MESSAGE)
    try:
        try:
            response = request_session.post(
                request_url,
                json=request_body,
                headers={"User-Agent": f"JK-Sistema/{str(app_version or 'unknown').strip() or 'unknown'}",
                         "X-JK-Central-Protocol": "1", "X-JK-Firebase-Protocol": "1",
                         "X-JK-Cofre-Protocol": "1", "X-JK-Cofre-Session-Protocol": "2"},
                timeout=(3.05, configuration.timeout_seconds),
                allow_redirects=False,
            )
        except (requests.RequestException, OSError):
            return RemoteAuthAttempt(
                RemoteAuthState.UNAVAILABLE,
                UNAVAILABLE_MESSAGE,
                allow_legacy_fallback=allow_outage_fallback,
            )

        if response.status_code != 200:
            code = _failure_code(response)
            if code in REMOTE_ERROR_MESSAGES:
                return RemoteAuthAttempt(RemoteAuthState.REJECTED, REMOTE_ERROR_MESSAGES[code])
            if response.status_code == 404 or response.status_code >= 500 or code == "auth_unavailable":
                return RemoteAuthAttempt(
                    RemoteAuthState.UNAVAILABLE,
                    UNAVAILABLE_MESSAGE,
                    allow_legacy_fallback=allow_outage_fallback,
                )
            return RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, INTEGRITY_MESSAGE)

        if len(response.content or b"") > MAX_RESPONSE_BYTES:
            return RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, INTEGRITY_MESSAGE)
        try:
            payload = response.json()
            expected_username = username_normalized
            if expected_username is None:
                # Google identifies the account at the authority. The complete
                # envelope is still untrusted until verify_signed_response below.
                expected_username = payload.get("user_data", {}).get("username")
                if not isinstance(expected_username, str) or not expected_username:
                    raise ValueError("invalid_remote_google_username")
            validated = validate_success_payload(
                payload,
                expected_username,
                machine_normalized,
                request_nonce,
                expected_code,
            )
            verify_signed_response(
                validated,
                configuration,
                request_session,
                verifier=verifier,
                now=time.time() if now is None else now,
            )
        except Exception:
            return RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, INTEGRITY_MESSAGE)

        return RemoteAuthAttempt(
            RemoteAuthState.SUCCESS,
            user_data=validated["user_data"],
            # Extend a verified legacy map only after its original hash matched.
            permissions={key: validated["permissions"].get(key, validated["permissions"]["full"])
                         for key in PERMISSION_KEYS},
            policy=validated["policy"],
            central=validated.get("central", {}),
            central_migration=validated.get("central_migration", {}),
            firebase_access=validated.get("firebase_access", {}),
            cofre_access=validated.get("cofre_access", {}),
        )
    finally:
        if owns_session:
            request_session.close()


def _configuration_or_attempt(
    values: Mapping[str, str],
) -> tuple[RemoteAuthConfiguration | None, RemoteAuthAttempt | None]:
    try:
        configuration = load_remote_auth_configuration(values)
    except ValueError:
        return None, RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, INTEGRITY_MESSAGE)
    if configuration is None:
        return None, RemoteAuthAttempt(RemoteAuthState.NOT_CONFIGURED, allow_legacy_fallback=True)
    return configuration, None


def attempt_remote_login(
    *,
    username: str,
    password: str,
    machine_id: str,
    app_version: str,
    environ: Mapping[str, str] | None = None,
    session: requests.Session | None = None,
    verifier: Callable[..., Mapping] = verify_firebase_token,
    now: float | None = None,
    request_nonce: str | None = None,
) -> RemoteAuthAttempt:
    values = os.environ if environ is None else environ
    configuration, early = _configuration_or_attempt(values)
    if early is not None:
        return early
    username_normalized = str(username or "").strip().lower()
    machine_normalized = str(machine_id or "").strip()
    nonce = str(request_nonce or secrets.token_urlsafe(32)).strip()
    return _attempt_remote_request(
        configuration=configuration,
        request_url=configuration.url,
        request_body={
            "username": username_normalized,
            "password": str(password or ""),
            "machine_id": machine_normalized,
            "app_version": str(app_version or "").strip(),
            "request_nonce": nonce,
        },
        username_normalized=username_normalized,
        machine_normalized=machine_normalized,
        app_version=app_version,
        values=values,
        session=session,
        verifier=verifier,
        now=now,
        expected_code="authenticated",
        allow_outage_fallback=configuration.mode == "prefer",
        request_nonce=nonce,
    )


def attempt_remote_password_change(
    *,
    username: str,
    current_password: str,
    new_password: str,
    machine_id: str,
    app_version: str,
    environ: Mapping[str, str] | None = None,
    session: requests.Session | None = None,
    verifier: Callable[..., Mapping] = verify_firebase_token,
    now: float | None = None,
    request_nonce: str | None = None,
) -> RemoteAuthAttempt:
    values = os.environ if environ is None else environ
    configuration, early = _configuration_or_attempt(values)
    if early is not None:
        return early
    username_normalized = str(username or "").strip().lower()
    machine_normalized = str(machine_id or "").strip()
    nonce = str(request_nonce or secrets.token_urlsafe(32)).strip()
    return _attempt_remote_request(
        configuration=configuration,
        request_url=configuration.url[: -len("login")] + "change-password",
        request_body={
            "username": username_normalized,
            "current_password": str(current_password or ""),
            "new_password": str(new_password or ""),
            "machine_id": machine_normalized,
            "app_version": str(app_version or "").strip(),
            "request_nonce": nonce,
        },
        username_normalized=username_normalized,
        machine_normalized=machine_normalized,
        app_version=app_version,
        values=values,
        session=session,
        verifier=verifier,
        now=now,
        expected_code="password_changed",
        allow_outage_fallback=False,
        request_nonce=nonce,
    )


def attempt_remote_google_login(*, credential: str, machine_id: str, app_version: str,
                                environ=None, session=None, verifier=verify_firebase_token,
                                now=None, request_nonce=None) -> RemoteAuthAttempt:
    values = os.environ if environ is None else environ
    configuration, early = _configuration_or_attempt(values)
    if early is not None:
        return RemoteAuthAttempt(early.state, early.message)
    machine = str(machine_id or "").strip()
    nonce = str(request_nonce or secrets.token_urlsafe(32)).strip()
    return _attempt_remote_request(configuration=configuration,
        request_url=configuration.url[:-len("login")] + "google-login",
        request_body={"credential": str(credential or ""), "machine_id": machine,
                      "app_version": str(app_version or "").strip(), "request_nonce": nonce},
        username_normalized=None, machine_normalized=machine, app_version=app_version,
        values=values, session=session, verifier=verifier, now=now,
        expected_code="authenticated", allow_outage_fallback=False, request_nonce=nonce)


def attempt_cofre_session(*, session_grant: str, username: str, client_id: str,
                          machine_id: str, app_version: str, scope, valid_until,
                          session_expires_at: int, established_at, environ=None,
                          session=None, now=None, request_nonce=None) -> RemoteAuthAttempt:
    """Exchange an opaque ticket with protected login context, never decode it.

    The desktop does not possess the server's MAC key. Authorization is granted
    only by a fresh RS256 capability bound to the protected identity and scope.
    """
    from backend.services.cofre_auth import verify_capability, validate_session_deadline

    values = os.environ if environ is None else environ
    configuration, early = _configuration_or_attempt(values)
    if early is not None:
        return RemoteAuthAttempt(early.state, early.message)
    current = time.time() if now is None else now
    nonce = str(request_nonce or secrets.token_urlsafe(32)).strip()
    owns_session = session is None
    transport = session
    try:
        if (not isinstance(session_grant, str) or not 1 <= len(session_grant) <= 16384
                or scope not in (["cofre:read"], ["cofre:read", "cofre:write"])):
            raise ValueError("invalid_cofre_session_context")
        validate_session_deadline(session_expires_at, valid_until=valid_until,
                                   now=current, established_at=established_at)
        transport = transport or configure_requests_session(requests.Session(), values)
        response = transport.post(configuration.url[:-len("login")] + "cofre/session",
            json={"session_grant": session_grant, "machine_id": machine_id,
                  "request_nonce": nonce, "app_version": str(app_version or "").strip()},
            headers={"User-Agent": f"JK-Sistema/{str(app_version or 'unknown').strip() or 'unknown'}",
                     "X-JK-Cofre-Protocol": "1", "X-JK-Cofre-Session-Protocol": "2"},
            timeout=(3.05, configuration.timeout_seconds), allow_redirects=False)
        if response.status_code != 200:
            state = RemoteAuthState.REJECTED if response.status_code in (400, 401, 403, 426) else RemoteAuthState.UNAVAILABLE
            return RemoteAuthAttempt(state, "cofre_session_unavailable")
        if len(response.content or b"") > MAX_RESPONSE_BYTES:
            raise ValueError("invalid_cofre_session_response")
        payload = response.json()
        if (not isinstance(payload, dict)
                or set(payload) != {"success", "protocol", "request_nonce", "capability", "session_grant", "session_expires_at"}
                or payload["success"] is not True or type(payload["protocol"]) is not int
                or payload["protocol"] != 2 or payload["request_nonce"] != nonce
                or payload["session_grant"] != session_grant
                or type(payload["session_expires_at"]) is not int or payload["session_expires_at"] != session_expires_at):
            raise ValueError("invalid_cofre_session_response")
        permissions = {"cofre": True, "admin_usuarios": "cofre:write" in scope}
        capability = verify_capability(payload["capability"], username=username, client_id=client_id,
            machine_id=machine_id, permissions=permissions, valid_until=valid_until,
            session=transport, now=current)
        if capability["exp"] > session_expires_at or capability["scope"] != scope:
            raise ValueError("invalid_cofre_session_deadline")
        return RemoteAuthAttempt(RemoteAuthState.SUCCESS,
            user_data={"username": username, "client_id": client_id, "machine_id": machine_id},
            permissions=permissions, policy={"valid_until": valid_until},
            cofre_access={"protocol": 2, "capability": payload["capability"],
                          "session_grant": session_grant, "session_expires_at": session_expires_at})
    except (requests.RequestException, OSError):
        return RemoteAuthAttempt(RemoteAuthState.UNAVAILABLE, "cofre_session_unavailable")
    except Exception:
        return RemoteAuthAttempt(RemoteAuthState.INTEGRITY_FAILURE, "cofre_session_unavailable")
    finally:
        if owns_session and transport is not None:
            transport.close()


__all__ = [
    "AUTH_PROTOCOL_VERSION",
    "INTEGRITY_MESSAGE",
    "PERMISSION_KEYS",
    "RemoteAuthAttempt",
    "RemoteAuthState",
    "UNAVAILABLE_MESSAGE",
    "attempt_remote_login",
    "attempt_remote_password_change",
    "attempt_remote_google_login",
    "attempt_cofre_session",
]
