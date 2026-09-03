"""Firebase token and signed-profile verification for remote authentication."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest

from backend.services.remote_auth_contracts import (
    AUTH_PROTOCOL_VERSION,
    PERMISSION_KEYS,
    RemoteAuthConfiguration,
)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def machine_hash(machine_id: str) -> str:
    return hashlib.sha256(machine_id.encode("utf-8")).hexdigest()


def expected_uid(username: str) -> str:
    return "jk-" + hashlib.sha256(username.encode("utf-8")).hexdigest()[:40]


def validate_success_payload(
    payload: Any,
    requested_username: str,
    requested_machine_id: str,
    requested_nonce: str,
    expected_code: str,
) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or str(payload.get("code") or "") != expected_code
    ):
        raise ValueError("remote_response_not_success")
    identity_token = str(payload.get("identity_token") or "").strip()
    if not identity_token or len(identity_token) > 16 * 1024:
        raise ValueError("invalid_identity_token")

    user_data_source = payload.get("user_data")
    permissions_source = payload.get("permissions")
    policy_source = payload.get("policy")
    if not isinstance(user_data_source, dict) or not isinstance(permissions_source, dict) or not isinstance(policy_source, dict):
        raise ValueError("invalid_signed_payload")
    response_nonce = str(payload.get("request_nonce") or "").strip()
    if not response_nonce or response_nonce != requested_nonce:
        raise ValueError("remote_nonce_mismatch")

    username = str(user_data_source.get("username") or "").strip().lower()
    client_id = str(user_data_source.get("client_id") or "").strip()
    machine_id = str(user_data_source.get("machine_id") or "").strip()
    name = str(user_data_source.get("name") or username).strip()
    email = str(user_data_source.get("email") or "").strip().lower()
    if username != requested_username or machine_id != requested_machine_id:
        raise ValueError("remote_identity_mismatch")
    if not client_id or len(client_id) > 128 or not name or len(name) > 200 or len(email) > 254:
        raise ValueError("invalid_remote_profile")

    if set(permissions_source) != set(PERMISSION_KEYS):
        raise ValueError("invalid_permissions_contract")
    permissions: dict[str, bool] = {}
    for key in PERMISSION_KEYS:
        value = permissions_source.get(key)
        if value is not True and value is not False:
            raise ValueError("invalid_permissions_contract")
        permissions[key] = value

    valid_until = policy_source.get("valid_until")
    max_machines = policy_source.get("max_machines")
    if policy_source.get("active") is not True:
        raise ValueError("invalid_remote_policy")
    if valid_until is not None and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(valid_until)):
        raise ValueError("invalid_remote_policy")
    if isinstance(max_machines, bool) or not isinstance(max_machines, int) or not 0 <= max_machines <= 1000:
        raise ValueError("invalid_remote_policy")

    return {
        "identity_token": identity_token,
        "user_data": {
            "username": username,
            "name": name,
            "email": email,
            "client_id": client_id,
            "machine_id": machine_id,
        },
        "permissions": permissions,
        "policy": {
            "active": True,
            "valid_until": str(valid_until) if valid_until is not None else None,
            "max_machines": max_machines,
        },
        "request_nonce": response_nonce,
    }


def verify_signed_response(
    validated: dict[str, Any],
    configuration: RemoteAuthConfiguration,
    session: requests.Session,
    *,
    verifier: Callable[..., Mapping[str, Any]],
    now: float,
) -> None:
    claims = verifier(
        validated["identity_token"],
        GoogleAuthRequest(session=session),
        audience=configuration.project_id,
        clock_skew_in_seconds=60,
    )
    if not isinstance(claims, Mapping):
        raise ValueError("invalid_token_claims")
    expected_issuer = f"https://securetoken.google.com/{configuration.project_id}"
    if str(claims.get("iss") or "") != expected_issuer:
        raise ValueError("invalid_token_issuer")
    if str(claims.get("aud") or "") != configuration.project_id:
        raise ValueError("invalid_token_audience")
    if str(claims.get("sub") or "") != expected_uid(validated["user_data"]["username"]):
        raise ValueError("invalid_token_subject")
    if int(claims.get("jk_auth_v") or 0) != AUTH_PROTOCOL_VERSION:
        raise ValueError("invalid_auth_protocol")
    if str(claims.get("jk_username") or "") != validated["user_data"]["username"]:
        raise ValueError("invalid_username_claim")
    if str(claims.get("jk_client_id") or "") != validated["user_data"]["client_id"]:
        raise ValueError("invalid_client_claim")
    if str(claims.get("jk_machine_hash") or "") != machine_hash(validated["user_data"]["machine_id"]):
        raise ValueError("invalid_machine_claim")

    signed_payload = {
        "user_data": validated["user_data"],
        "permissions": validated["permissions"],
        "policy": validated["policy"],
        "request_nonce": validated["request_nonce"],
    }
    if str(claims.get("jk_response_hash") or "") != canonical_hash(signed_payload):
        raise ValueError("invalid_response_claim")
    firebase_claim = claims.get("firebase")
    if not isinstance(firebase_claim, Mapping) or str(firebase_claim.get("sign_in_provider") or "") != "custom":
        raise ValueError("invalid_sign_in_provider")
    try:
        auth_time = int(claims.get("auth_time") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_auth_time") from exc
    if auth_time <= 0 or abs(now - auth_time) > 300:
        raise ValueError("stale_auth_token")


__all__ = [
    "canonical_hash",
    "expected_uid",
    "machine_hash",
    "validate_success_payload",
    "verify_signed_response",
]
