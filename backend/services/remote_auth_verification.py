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
    google_email = None
    if "google_email" in user_data_source:
        google_email = user_data_source["google_email"]
        if (not isinstance(google_email, str) or not 1 <= len(google_email) <= 254
                or google_email != google_email.strip().lower() or "@" not in google_email
                or any(ord(char) < 32 for char in google_email)):
            raise ValueError("invalid_remote_google_identity")
    if username != requested_username or machine_id != requested_machine_id:
        raise ValueError("remote_identity_mismatch")
    if not client_id or len(client_id) > 128 or not name or len(name) > 200 or len(email) > 254:
        raise ValueError("invalid_remote_profile")

    permission_keys = set(PERMISSION_KEYS)
    legacy_permission_keys = permission_keys - {"cofre"}
    if set(permissions_source) not in (permission_keys, legacy_permission_keys):
        raise ValueError("invalid_permissions_contract")
    permissions: dict[str, bool] = {}
    # Keep the signed map intact: older gateways omit only the new Cofre key.
    for key, value in permissions_source.items():
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

    extension = {}
    if "cofre_access" in payload:
        cofre = payload["cofre_access"]
        if (not isinstance(cofre, dict) or type(cofre.get("protocol")) is not int
                or cofre["protocol"] not in {1, 2}
                or set(cofre) != ({"protocol", "capability", "session_grant", "session_expires_at"} if cofre["protocol"] == 2 else {"protocol", "capability"})
                or not isinstance(cofre["capability"], str)
                or not 1 <= len(cofre["capability"]) <= 16384
                or cofre["protocol"] == 2 and (not isinstance(cofre["session_grant"], str)
                    or not 1 <= len(cofre["session_grant"]) <= 16384
                    or type(cofre["session_expires_at"]) is not int)):
            raise ValueError("invalid_cofre_access")
        extension["cofre_access"] = dict(cofre)
    if "firebase_access" in payload:
        access = payload["firebase_access"]
        fields = {"protocol", "project_id", "grant_id", "id_token", "refresh_token",
                  "api_key", "expires_at", "session_expires_at"}
        if (not isinstance(access, dict) or set(access) != fields
                or type(access["protocol"]) is not int or access["protocol"] != 1
                or not isinstance(access["project_id"], str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{4,29}", access["project_id"])
                or not isinstance(access["grant_id"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", access["grant_id"])
                or any(not isinstance(access[key], str) or not 1 <= len(access[key]) <= limit
                       for key, limit in (("id_token", 16384), ("refresh_token", 8192), ("api_key", 256)))
                or type(access["expires_at"]) is not int
                or type(access["session_expires_at"]) is not int):
            raise ValueError("invalid_firebase_access")
        extension["firebase_access"] = dict(access)
    if "central" in payload:
        central = payload["central"]
        if (not isinstance(central, dict)
                or set(central) != {"protocol", "sync_mode", "session", "expires_at", "stores"}
                or central["protocol"] != 1 or central["sync_mode"] != "manual"
                or not isinstance(central["session"], str) or not 100 <= len(central["session"]) <= 8192
                or not isinstance(central["expires_at"], int) or isinstance(central["expires_at"], bool)
                or not isinstance(central["stores"], list) or len(central["stores"]) > 100):
            raise ValueError("invalid_central_bootstrap")
        from backend.services.central_accounts_client import validate_public_stores
        validate_public_stores(central["stores"])
        extension["central"] = central
    if "central_migration" in payload:
        migration = payload["central_migration"]
        if (not isinstance(migration, dict)
                or set(migration) != {"protocol", "mode", "session", "expires_at"}
                or migration["protocol"] != 1 or migration["mode"] != "legacy_adoption"
                or not isinstance(migration["session"], str)
                or not 100 <= len(migration["session"]) <= 8192
                or not isinstance(migration["expires_at"], int)
                or isinstance(migration["expires_at"], bool)):
            raise ValueError("invalid_central_migration")
        extension["central_migration"] = migration
    return {
        **extension,
        "identity_token": identity_token,
        "user_data": {
            "username": username,
            "name": name,
            "email": email,
            "client_id": client_id,
            "machine_id": machine_id,
            **({"google_email": google_email} if google_email is not None else {}),
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
    if "firebase_access" in validated:
        access = validated["firebase_access"]
        if (access["project_id"] != configuration.project_id
                or not now < access["expires_at"] <= now + 3700
                or not now < access["session_expires_at"] <= now + 8 * 3600 + 60):
            raise ValueError("invalid_firebase_access_expiry")
        signed_payload["firebase_access"] = access
    if "central" in validated:
        signed_payload["central"] = validated["central"]
        if not now < validated["central"]["expires_at"] <= now + 9 * 3600:
            raise ValueError("invalid_central_session_expiry")
    if "central_migration" in validated:
        signed_payload["central_migration"] = validated["central_migration"]
        if not now < validated["central_migration"]["expires_at"] <= now + 31 * 60:
            raise ValueError("invalid_central_migration_expiry")
    if "cofre_access" in validated:
        signed_payload["cofre_access"] = validated["cofre_access"]
    # This cryptographic binding covers the entire Cofre extension, including
    # the opaque refresh credential and its deadline. The verified Firebase
    # identity above also binds username, tenant, machine and request nonce.
    # Never register or persist a refresh credential before this check passes.
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
    if "cofre_access" in validated:
        from backend.services.cofre_auth import verify_capability
        user = validated["user_data"]
        principal = verify_capability(
            validated["cofre_access"]["capability"], username=user["username"],
            client_id=user["client_id"], machine_id=user["machine_id"],
            permissions=validated["permissions"], valid_until=validated["policy"]["valid_until"],
            session=session, now=now)
        if "firebase_access" in validated and principal["exp"] > validated["firebase_access"]["session_expires_at"]:
            raise ValueError("invalid_cofre_session_expiry")
        if "central" in validated and principal["exp"] > validated["central"]["expires_at"]:
            raise ValueError("invalid_cofre_session_expiry")
        if validated["cofre_access"]["protocol"] == 2:
            from backend.services.cofre_auth import validate_session_deadline
            deadline = validated["cofre_access"]["session_expires_at"]
            # This branch is reached only after signature/identity/hash checks
            # of the complete normal-login envelope AND independent RS256
            # capability verification against this user's exact identity and
            # permissions. The desktop never decodes/trusts the opaque grant;
            # only the gateway verifies its stable server-only HMAC secret.
            validate_session_deadline(deadline, valid_until=validated["policy"]["valid_until"],
                                      now=now, established_at=now)
            if principal["exp"] > deadline:
                raise ValueError("invalid_cofre_session_grant")


__all__ = [
    "canonical_hash",
    "expected_uid",
    "machine_hash",
    "validate_success_payload",
    "verify_signed_response",
]
