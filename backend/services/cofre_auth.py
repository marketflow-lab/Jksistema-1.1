"""Cofre authorization bound to verified login, with no Firebase request per action."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import jwt
import requests
from fastapi import HTTPException, Request

from backend.services.transport_security import configure_requests_session


AUDIENCE = "jk-cofre"
MAX_SESSION_SECONDS = 8 * 3600
DEFAULT_ISSUER = "jk-auth-gateway@jkjkjk-485920.iam.gserviceaccount.com"
_CLAIMS = {"iss", "aud", "sub", "client_id", "machine_hash", "scope", "iat", "exp"}
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}
_EMAIL = re.compile(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.gserviceaccount\.com")
_LOCK = threading.RLock()
_SESSIONS: dict[str, dict] = {}
_KEYS: dict[str, tuple[float, dict]] = {}


def valid_client_id(value):
    return (isinstance(value, str) and bool(_TENANT.fullmatch(value))
            and not value.endswith(".") and value.split(".")[0].lower() not in _RESERVED)


def trusted_issuer():
    # Public identity only. Never select a key URL from an unverified JWT.
    email = str(os.getenv("JK_COFRE_ISSUER_EMAIL") or DEFAULT_ISSUER).strip()
    if not _EMAIL.fullmatch(email):
        raise ValueError("cofre_issuer_not_configured")
    return email


def _public_keys(issuer, *, session=None, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        cached = _KEYS.get(issuer)
        if cached and cached[0] > now:
            return cached[1]
        owns_session = session is None
        transport = session or configure_requests_session(requests.Session())
        try:
            response = transport.get(
                "https://www.googleapis.com/service_accounts/v1/jwk/" + quote(issuer, safe=""),
                timeout=(3.05, 8), allow_redirects=False)
            if response.status_code != 200 or len(response.content) > 64 * 1024:
                raise ValueError("cofre_public_keys_unavailable")
            payload = response.json()
            entries = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
                raise ValueError("invalid_cofre_public_keys")
            result = {}
            for entry in entries:
                if (not isinstance(entry, dict) or entry.get("kty") != "RSA"
                        or entry.get("alg", "RS256") != "RS256"
                        or entry.get("use", "sig") != "sig"
                        or not isinstance(entry.get("kid"), str)
                        or not 1 <= len(entry["kid"]) <= 128 or entry["kid"] in result):
                    raise ValueError("invalid_cofre_public_keys")
                key = jwt.algorithms.RSAAlgorithm.from_jwk(entry)
                if key.key_size < 2048:
                    raise ValueError("invalid_cofre_public_keys")
                result[entry["kid"]] = key
            match = re.search(r"(?:^|,)\s*max-age=(\d+)", response.headers.get("Cache-Control", ""))
            ttl = min(3600, int(match.group(1))) if match else 300
            _KEYS[issuer] = (now + max(0, ttl), result)
            return result
        finally:
            if owns_session:
                transport.close()


def verify_capability(token, *, username, client_id, machine_id, permissions,
                      valid_until=None, session=None, now=None, issuer=None, keys=None):
    """Verify once at login. Subsequent Cofre actions use only the bound RAM session."""
    now = time.time() if now is None else now
    issuer = trusted_issuer() if issuer is None else issuer
    if (not isinstance(issuer, str) or not _EMAIL.fullmatch(issuer)
            or not isinstance(token, str) or not 1 <= len(token) <= 16384):
        raise ValueError("invalid_cofre_capability")
    header = jwt.get_unverified_header(token)
    if (set(header) - {"alg", "typ", "kid"} or header.get("alg") != "RS256"
            or header.get("typ") != "JWT" or ("kid" in header and
            (not isinstance(header["kid"], str) or not 1 <= len(header["kid"]) <= 128))):
        raise ValueError("invalid_cofre_header")
    keyset = keys if keys is not None else _public_keys(issuer, session=session, now=now)
    candidates = ([keyset[header["kid"]]] if header.get("kid") in keyset else []) if "kid" in header else list(keyset.values())[:10]
    claims = None
    for key in candidates:
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=AUDIENCE, issuer=issuer,
                                options={"verify_exp": False, "verify_iat": False,
                                         "require": list(_CLAIMS)})
            break
        except jwt.InvalidSignatureError:
            continue
    if not isinstance(claims, dict) or set(claims) != _CLAIMS:
        raise ValueError("invalid_cofre_signature")
    scope = claims["scope"]
    full = permissions.get("full") is True
    readable = full or permissions.get("cofre") is True
    expected_scope = ["cofre:read"]
    if full or permissions.get("admin_usuarios") is True:
        expected_scope.append("cofre:write")
    if (not readable or scope != expected_scope or claims["aud"] != AUDIENCE or claims["iss"] != issuer
            or claims["sub"] != username or claims["client_id"] != client_id
            or not valid_client_id(client_id)
            or not isinstance(username, str) or not 1 <= len(username) <= 200
            or claims["machine_hash"] != hashlib.sha256(machine_id.encode("utf-8")).hexdigest()
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or not 0 < claims["iat"] <= now + 60
            or not now < claims["exp"] <= claims["iat"] + MAX_SESSION_SECONDS):
        raise ValueError("invalid_cofre_claims")
    if valid_until:
        end = (datetime.strptime(valid_until, "%d/%m/%Y") + timedelta(days=1)).replace(
            tzinfo=ZoneInfo("America/Sao_Paulo"))
        if claims["exp"] > int(end.timestamp()):
            raise ValueError("invalid_cofre_expiry")
    return {"client_id": client_id, "username": username, "scope": list(scope),
            "exp": claims["exp"], "capability": token}


def _key(local_token):
    return hashlib.sha256(local_token.encode("utf-8")).hexdigest()


def register_login(local_token, attempt):
    """Call only after the complete remote login envelope has been verified."""
    if not local_token or not getattr(attempt, "success", False):
        raise ValueError("invalid_cofre_login")
    user = attempt.user_data
    identity_hash = hashlib.sha256(str(user.get("machine_id") or "").encode()).hexdigest()
    with _LOCK:
        for key, previous in list(_SESSIONS.items()):
            if key == _key(local_token) or previous["exp"] <= time.time() or (
                previous["client_id"] == user.get("client_id")
                and previous["username"] == user.get("username")
                and previous["machine_hash"] == identity_hash):
                _SESSIONS.pop(key, None)
    access = getattr(attempt, "cofre_access", None)
    if not access:
        return None  # Older gateways/local logins cannot grant shared access.
    principal = verify_capability(
        access["capability"], username=user["username"], client_id=user["client_id"],
        machine_id=user["machine_id"], permissions=attempt.permissions,
        valid_until=attempt.policy.get("valid_until"))
    with _LOCK:
        _SESSIONS[_key(local_token)] = {**principal, "machine_hash": identity_hash}
    return copy.deepcopy(principal)


def revoke(local_token):
    if local_token:
        with _LOCK:
            _SESSIONS.pop(_key(local_token), None)


def principal_for_token(local_token, *, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        principal = _SESSIONS.get(_key(local_token)) if local_token else None
        if principal is None or principal["exp"] <= now:
            if local_token:
                _SESSIONS.pop(_key(local_token), None)
            raise HTTPException(401, "A autorização do Cofre expirou ou não está disponível nesta sessão.")
        return {key: copy.deepcopy(value) for key, value in principal.items() if key != "machine_hash"}


def bind_request(request: Request, authorization):
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token de autenticação ausente.")
    principal = principal_for_token(authorization[7:].strip())
    request.state.cofre_principal = principal
    request.state.client_id = principal["client_id"]
    request.state.username = principal["username"]
    return principal["client_id"]
