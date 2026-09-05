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
    username_normalized: str,
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
                         "X-JK-Central-Protocol": "1"},
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
            validated = validate_success_payload(
                response.json(),
                username_normalized,
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
            permissions=validated["permissions"],
            policy=validated["policy"],
            central=validated.get("central", {}),
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


__all__ = [
    "AUTH_PROTOCOL_VERSION",
    "INTEGRITY_MESSAGE",
    "PERMISSION_KEYS",
    "RemoteAuthAttempt",
    "RemoteAuthState",
    "UNAVAILABLE_MESSAGE",
    "attempt_remote_login",
    "attempt_remote_password_change",
]
