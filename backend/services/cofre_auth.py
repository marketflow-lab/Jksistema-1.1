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
MAX_GRANT_SECONDS = 30 * 24 * 3600
DEFAULT_ISSUER = "jk-auth-gateway@jkjkjk-485920.iam.gserviceaccount.com"
_CLAIMS = {"iss", "aud", "sub", "client_id", "machine_hash", "scope", "iat", "exp"}
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}
_EMAIL = re.compile(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.gserviceaccount\.com")
_LOCK = threading.RLock()
_SESSIONS: dict[str, dict] = {}
_KEYS: dict[str, tuple[float, dict]] = {}
_STORE = None


def valid_client_id(value):
    return (isinstance(value, str) and bool(_TENANT.fullmatch(value))
            and not value.endswith(".") and value.split(".")[0].lower() not in _RESERVED)


def trusted_issuer():
    # Public identity only. Never select a key URL from an unverified JWT.
    email = str(os.getenv("JK_COFRE_ISSUER_EMAIL") or DEFAULT_ISSUER).strip()
    if not _EMAIL.fullmatch(email):
        raise ValueError("cofre_issuer_not_configured")
    return email


def _public_keys(issuer, *, session=None, now=None, force_refresh=False):
    now = time.time() if now is None else now
    with _LOCK:
        cached = _KEYS.get(issuer)
        if cached and cached[0] > now and not force_refresh:
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


def _signed_claims(token, *, audience, fields, issuer, session, now, keys):
    if (not isinstance(issuer, str) or not _EMAIL.fullmatch(issuer)
            or not isinstance(token, str) or not 1 <= len(token) <= 16384):
        raise ValueError("invalid_cofre_capability")
    header = jwt.get_unverified_header(token)
    if (set(header) - {"alg", "typ", "kid"} or header.get("alg") != "RS256"
            or header.get("typ") != "JWT" or ("kid" in header and
            (not isinstance(header["kid"], str) or not 1 <= len(header["kid"]) <= 128))):
        raise ValueError("invalid_cofre_header")
    # Denial only: never fetch or refresh keys for an obviously expired token.
    # No identity, scope or other unverified claim can grant access here.
    unverified = jwt.decode(token, options={"verify_signature": False})
    if type(unverified.get("exp")) is int and unverified["exp"] <= now:
        raise ValueError("expired_cofre_capability")
    keyset = keys if keys is not None else _public_keys(issuer, session=session, now=now)
    claims = None
    for attempt in range(1 if keys is not None else 2):
        candidates = ([keyset[header["kid"]]] if header.get("kid") in keyset else []) if "kid" in header else list(keyset.values())[:10]
        for key in candidates:
            try:
                claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=issuer,
                                    options={"verify_exp": False, "verify_iat": False,
                                             "require": list(fields - {"valid_until"})})
                break
            except jwt.InvalidSignatureError:
                continue
        if claims is not None:
            break
        if attempt == 0 and keys is None:
            # Refresh at most once from the configured issuer's fixed Google URL.
            # This also handles IAM tokens whose signer could not provide a kid.
            keyset = _public_keys(issuer, session=session, now=now, force_refresh=True)
    if not isinstance(claims, dict) or set(claims) != fields:
        raise ValueError("invalid_cofre_signature")
    return claims


def verify_capability(token, *, username, client_id, machine_id, permissions,
                      valid_until=None, session=None, now=None, issuer=None, keys=None):
    """Verify a company capability; no Firebase operation is involved."""
    now = time.time() if now is None else now
    issuer = trusted_issuer() if issuer is None else issuer
    claims = _signed_claims(token, audience=AUDIENCE, fields=_CLAIMS, issuer=issuer,
                            session=session, now=now, keys=keys)
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


def validate_session_deadline(deadline, *, valid_until=None, now=None, established_at=None):
    """Bound an envelope-authenticated lease; never authenticate an opaque grant.

    The initial clock-skew allowance is fixed at login and cannot slide when
    restoring or renewing. The gateway remains authoritative for the grant MAC.
    """
    now = time.time() if now is None else now
    established_at = now if established_at is None else established_at
    if (type(deadline) is not int or type(established_at) not in (int, float)
            or not 0 < established_at <= now + 60
            or not now < deadline <= established_at + MAX_GRANT_SECONDS + 60):
        raise ValueError("invalid_cofre_session_deadline")
    if valid_until is not None:
        if not isinstance(valid_until, str):
            raise ValueError("invalid_cofre_session_validity")
        parsed = datetime.strptime(valid_until, "%d/%m/%Y")
        if parsed.strftime("%d/%m/%Y") != valid_until:
            raise ValueError("invalid_cofre_session_validity")
        end = (parsed + timedelta(days=1)).replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
        if deadline > int(end.timestamp()):
            raise ValueError("invalid_cofre_session_deadline")
    return deadline


def _validate_context(context, identity, *, now):
    fields = {"username", "client_id", "machine_hash", "scope", "valid_until",
              "session_expires_at", "established_at"}
    if (not isinstance(context, dict) or set(context) != fields
            or context["username"] != identity["sub"] or context["client_id"] != identity["client_id"]
            or context["machine_hash"] != hashlib.sha256(identity["machine_id"].encode("utf-8")).hexdigest()
            or context["scope"] not in (["cofre:read"], ["cofre:read", "cofre:write"])):
        raise ValueError("invalid_cofre_session_context")
    validate_session_deadline(context["session_expires_at"], valid_until=context["valid_until"],
                              now=now, established_at=context["established_at"])
    return context


def _key(local_token):
    return hashlib.sha256(local_token.encode("utf-8")).hexdigest()


def _store():
    global _STORE
    if _STORE is None:
        from backend.services.cofre_session_store import CofreSessionStore
        _STORE = CofreSessionStore()
    return _STORE


def _identity_key(username, client_id, machine_hash):
    return hashlib.sha256((username + "\x00" + client_id + "\x00" + machine_hash).encode("utf-8")).hexdigest()


def _unavailable():
    return HTTPException(503, {"code": "cofre_session_unavailable",
        "message": "Não foi possível recuperar a autorização do Cofre. Tente novamente."})


def _permissions(scope):
    return {"cofre": "cofre:read" in scope, "admin_usuarios": "cofre:write" in scope}


def _visible(principal, identity=None):
    result = {key: copy.deepcopy(principal[key]) for key in ("client_id", "username", "scope", "exp", "capability")}
    if identity is not None and isinstance(identity.get("permissions"), dict):
        permissions = identity["permissions"]
        if not (permissions.get("cofre") is True or permissions.get("full") is True):
            raise HTTPException(403, "Acesso ao Cofre não autorizado.")
        if not (permissions.get("full") is True or permissions.get("admin_usuarios") is True):
            result["scope"] = [scope for scope in result["scope"] if scope != "cofre:write"]
    return result


def register_login(local_token, attempt):
    """Call only after the complete remote login envelope has been verified."""
    if not local_token or not getattr(attempt, "success", False):
        raise ValueError("invalid_cofre_login")
    user = attempt.user_data
    identity_hash = hashlib.sha256(str(user.get("machine_id") or "").encode()).hexdigest()
    identity_key = _identity_key(user["username"], user["client_id"], identity_hash)
    with _LOCK:
        for key, previous in list(_SESSIONS.items()):
            if key == _key(local_token) or previous["exp"] <= time.time() or (
                previous["client_id"] == user.get("client_id")
                and previous["username"] == user.get("username")
                and previous["machine_hash"] == identity_hash):
                _SESSIONS.pop(key, None)
        try:
            _store().revoke_identity(identity_key)
        except Exception:
            pass  # Secure-store availability must not break the normal JK login.
    access = getattr(attempt, "cofre_access", None)
    if not access:
        return None  # Older gateways/local logins cannot grant shared access.
    principal = verify_capability(
        access["capability"], username=user["username"], client_id=user["client_id"],
        machine_id=user["machine_id"], permissions=attempt.permissions,
        valid_until=attempt.policy.get("valid_until"))
    context = None
    if access.get("protocol") == 2:
        if not isinstance(access.get("session_grant"), str) or not 1 <= len(access["session_grant"]) <= 16384:
            raise ValueError("invalid_cofre_session_ticket")
        established_at = int(time.time())
        deadline = validate_session_deadline(access.get("session_expires_at"),
            valid_until=attempt.policy.get("valid_until"), now=established_at, established_at=established_at)
        if principal["exp"] > deadline:
            raise ValueError("invalid_cofre_session_expiry")
        # The normal login's verified outer signature binds the opaque grant and
        # deadline. Current capability signature binds identity and exact scope.
        context = {"username": user["username"], "client_id": user["client_id"],
            "machine_hash": identity_hash, "scope": list(principal["scope"]),
            "valid_until": attempt.policy.get("valid_until"), "session_expires_at": deadline,
            "established_at": established_at}
    with _LOCK:
        _SESSIONS[_key(local_token)] = {**principal, "machine_hash": identity_hash,
                                      "session_grant": access.get("session_grant") if context else None,
                                      "context": context}
        if context:
            try:
                _store().save(_key(local_token), {"session_grant": access["session_grant"],
                    "capability": access["capability"], "identity_key": identity_key, "context": context})
            except Exception:
                pass  # RAM access remains valid; there is no insecure disk fallback.
    return copy.deepcopy(principal)


def revoke(local_token):
    if local_token:
        with _LOCK:
            _SESSIONS.pop(_key(local_token), None)
            try:
                _store().delete(_key(local_token))
            except Exception:
                pass


def principal_for_token(local_token, *, now=None, identity=None):
    now = time.time() if now is None else now
    with _LOCK:
        principal = _SESSIONS.get(_key(local_token)) if local_token else None
        if identity is not None:
            username, client_id, machine_id = identity["sub"], identity["client_id"], identity["machine_id"]
            machine_hash = hashlib.sha256(machine_id.encode("utf-8")).hexdigest()
            if principal is not None and (principal["username"] != username or principal["client_id"] != client_id
                                         or principal["machine_hash"] != machine_hash):
                raise HTTPException(403, "Sessão inválida para o Cofre.")
            renewable = principal is not None and principal.get("session_grant") and (principal.get("context") or {}).get("session_expires_at", 0) > principal["exp"]
            if principal is not None and principal["exp"] > now and (not renewable or principal["exp"] > now + 60):
                return _visible(principal, identity)
            try:
                session_grant = principal.get("session_grant") if principal else None
                try:
                    ticket = _store().load(_key(local_token))
                except Exception:
                    if not session_grant:
                        raise
                    ticket = None
                session_grant = session_grant or (ticket or {}).get("session_grant")
                context = (principal.get("context") if principal else None) or (ticket or {}).get("context")
                if not isinstance(session_grant, str) or not 1 <= len(session_grant) <= 16384 or context is None:
                    raise _unavailable()
                context = _validate_context(context, identity, now=now)
                # A local profile can restrict, never enlarge, the signed scope.
                _visible({"username": username, "client_id": client_id, "scope": context["scope"],
                          "exp": context["session_expires_at"], "capability": ""}, identity)
                capability = (ticket or {}).get("capability")
                restored = None
                if capability:
                    try:
                        restored = verify_capability(capability, username=username, client_id=client_id,
                            machine_id=machine_id, permissions=_permissions(context["scope"]),
                            valid_until=context["valid_until"], now=now)
                        if restored["exp"] > context["session_expires_at"] or (restored["exp"] <= now + 60 and restored["exp"] < context["session_expires_at"]):
                            restored = None
                    except Exception:
                        restored = None
                if restored is None:
                    from backend.services.remote_auth_client import attempt_cofre_session
                    attempt = attempt_cofre_session(session_grant=session_grant, username=username,
                        client_id=client_id, machine_id=machine_id,
                        scope=context["scope"], valid_until=context["valid_until"],
                        session_expires_at=context["session_expires_at"], established_at=context["established_at"],
                        app_version=str(os.getenv("JK_APP_VERSION") or ""), now=now)
                    if not attempt.success:
                        raise _unavailable()
                    restored = verify_capability(attempt.cofre_access["capability"], username=username,
                        client_id=client_id, machine_id=machine_id, permissions=_permissions(context["scope"]),
                        valid_until=context["valid_until"], now=now)
                    if (restored["exp"] > context["session_expires_at"] or attempt.cofre_access.get("session_grant") != session_grant
                            or attempt.cofre_access.get("session_expires_at") != context["session_expires_at"]):
                        raise _unavailable()
                identity_key = _identity_key(username, client_id, machine_hash)
                try:
                    _store().save(_key(local_token), {"session_grant": session_grant,
                        "capability": restored["capability"], "identity_key": identity_key, "context": context})
                except Exception:
                    pass
                principal = {**restored, "machine_hash": machine_hash, "session_grant": session_grant,
                             "context": context}
                _SESSIONS[_key(local_token)] = principal
                return _visible(principal, identity)
            except HTTPException:
                raise
            except Exception:
                raise _unavailable() from None
        if principal is None or principal["exp"] <= now:
            if local_token:
                _SESSIONS.pop(_key(local_token), None)
            raise HTTPException(401, "A autorização do Cofre expirou ou não está disponível nesta sessão.")
        return _visible(principal)


def bind_request(request: Request, authorization, *, decode_session=None):
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token de autenticação ausente.", headers={"WWW-Authenticate": "Bearer"})
    local_token = authorization[7:].strip()
    identity = None
    if decode_session is not None:
        try:
            identity = decode_session(local_token)
            if (not isinstance(identity, dict) or not isinstance(identity.get("sub"), str)
                    or not identity["sub"] or not valid_client_id(identity.get("client_id"))
                    or not isinstance(identity.get("machine_id"), str) or not identity["machine_id"]):
                raise ValueError("invalid_local_session")
        except HTTPException as exc:
            if exc.status_code == 401:
                raise HTTPException(401, exc.detail, headers={**(exc.headers or {}), "WWW-Authenticate": "Bearer"}) from None
            raise
        except Exception:
            raise HTTPException(401, "Sessão JK inválida.", headers={"WWW-Authenticate": "Bearer"}) from None
    principal = principal_for_token(local_token, identity=identity)
    request.state.cofre_principal = principal
    request.state.client_id = principal["client_id"]
    request.state.username = principal["username"]
    return principal["client_id"]
