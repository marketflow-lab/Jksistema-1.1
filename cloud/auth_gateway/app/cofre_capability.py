"""Issue a company-scoped Cofre capability without database or Firebase Auth calls."""

from __future__ import annotations

import base64
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth import credentials as google_credentials, iam, jwt
from google.auth.transport.requests import Request

from .errors import GatewayUnavailable
from .policy import machine_hash, normalize_permissions, normalized_expiry, user_client_id


AUDIENCE = "jk-cofre"
SESSION_SECONDS = 8 * 3600
CLAIMS = {"iss", "aud", "sub", "client_id", "machine_hash", "scope", "iat", "exp"}
SESSION_AUDIENCE = "jk-cofre-session"
GRANT_SECONDS = 30 * 24 * 3600
SESSION_CLAIMS = CLAIMS | {"valid_until", "session_id"}
SERVICE_ACCOUNT_EMAIL = re.compile(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.gserviceaccount\.com")
_METADATA_EMAIL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}


def valid_client_id(value):
    return (isinstance(value, str) and bool(_TENANT.fullmatch(value))
            and not value.endswith(".") and value.split(".")[0].lower() not in _RESERVED)


def _licence_end(validity):
    if validity is None:
        return None
    if not isinstance(validity, str):
        raise ValueError("invalid_cofre_session")
    parsed = datetime.strptime(validity, "%d/%m/%Y")
    if parsed.strftime("%d/%m/%Y") != validity:
        raise ValueError("invalid_cofre_session")
    return int((parsed + timedelta(days=1)).replace(tzinfo=ZoneInfo("America/Sao_Paulo")).timestamp())


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_cofre_session")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_json_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_cofre_session")))


def _unbase64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid_cofre_session")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _session_secret(value):
    if value in (None, "", b""):
        return b""
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("invalid_cofre_session_secret")
        return bytes.fromhex(value)
    if isinstance(value, bytes) and len(value) >= 32:
        return value
    raise ValueError("invalid_cofre_session_secret")


def _hmac_token(claims, secret):
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = encode(json.dumps(claims, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    message = (header + "." + payload).encode("ascii")
    return message.decode("ascii") + "." + encode(hmac.digest(secret, message, "sha256"))


class CofreSessionVerifier:
    """Server-only opaque-grant verification; never a desktop authority format."""

    def __init__(self, issuer, *, session_secret, previous_session_secret=b"", clock=time.time):
        if not isinstance(issuer, str) or not SERVICE_ACCOUNT_EMAIL.fullmatch(issuer):
            raise ValueError("invalid_cofre_issuer")
        self.issuer, self.clock = issuer, clock
        self._secret = _session_secret(session_secret)
        self._previous = _session_secret(previous_session_secret)

    def verify(self, token, machine_id):
        if not self._secret:
            raise GatewayUnavailable("cofre_session_not_configured")
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise ValueError("invalid_cofre_session")
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("invalid_cofre_session")
        header, claims = (_json(_unbase64(part)) for part in parts[:2])
        if header != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("invalid_cofre_session")
        if not isinstance(claims, dict) or set(claims) != SESSION_CLAIMS:
            raise ValueError("invalid_cofre_session")
        if claims["iss"] != self.issuer or claims["aud"] != SESSION_AUDIENCE:
            raise ValueError("invalid_cofre_session")
        signature = _unbase64(parts[2])
        message = (parts[0] + "." + parts[1]).encode("ascii")
        signatures_match = [hmac.compare_digest(hmac.digest(secret, message, "sha256"), signature)
                            for secret in (self._secret, self._previous) if secret]
        if not any(signatures_match):
            raise ValueError("invalid_cofre_session")
        now = int(self.clock())
        username = claims["sub"]
        if (not isinstance(username, str) or not 1 <= len(username) <= 254
                or username != username.strip().lower() or any(ord(c) < 32 for c in username)
                or not valid_client_id(claims["client_id"])
                or not isinstance(machine_id, str) or not machine_id.strip()
                or claims["machine_hash"] != machine_hash(machine_id)
                or claims["scope"] not in (["cofre:read"], ["cofre:read", "cofre:write"])
                or type(claims["iat"]) is not int or type(claims["exp"]) is not int
                or claims["iat"] < 0 or claims["iat"] > now + 60
                or claims["exp"] <= now or not 0 < claims["exp"] - claims["iat"] <= GRANT_SECONDS
                or not isinstance(claims["session_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", claims["session_id"])):
            raise ValueError("invalid_cofre_session")
        end = _licence_end(claims["valid_until"])
        if end is not None and claims["exp"] > end:
            raise ValueError("invalid_cofre_session")
        return claims


def capability_claims(user, username, machine_id, issuer, *, now, expires_at=None):
    """Use the authoritative profile already loaded by normal authentication."""
    permissions = normalize_permissions(user.get("permissions") or user.get("permissoes"))
    if not (permissions.get("full") is True or permissions.get("cofre") is True):
        return None
    client_id = user_client_id(user)
    if not valid_client_id(client_id):
        # A malformed Cofre namespace must not prevent login to unrelated modules.
        return None
    if not SERVICE_ACCOUNT_EMAIL.fullmatch(issuer):
        raise ValueError("invalid_cofre_issuer")
    issued = int(now)
    expires = issued + SESSION_SECONDS
    if expires_at is not None:
        expires = min(expires, int(expires_at))
    validity = normalized_expiry(user)
    if validity:
        end = (datetime.strptime(str(validity), "%d/%m/%Y") + timedelta(days=1)).replace(
            tzinfo=ZoneInfo("America/Sao_Paulo"))
        expires = min(expires, int(end.timestamp()))
    if expires <= issued:
        raise ValueError("expired_cofre_access")
    scope = ["cofre:read"]
    if permissions.get("full") is True or permissions.get("admin_usuarios") is True:
        scope.append("cofre:write")
    return {"iss": issuer, "aud": AUDIENCE, "sub": username,
            "client_id": client_id, "machine_hash": machine_hash(machine_id),
            "scope": scope, "iat": issued, "exp": expires}


class CofreCapabilityIssuer:
    """The signer can be IAM signBlob; private keys never reach the desktop."""

    def __init__(self, signer, email, *, clock=time.time, session_secret=b"", previous_session_secret=b""):
        self.signer, self.email, self.clock = signer, email, clock
        self._session_secret = _session_secret(session_secret)
        self._previous_secret = _session_secret(previous_session_secret)
        self.session_verifier = None

    def issue(self, user, username, machine_id, *, expires_at=None):
        claims = capability_claims(user, username, machine_id, self.email,
                                   now=self.clock(), expires_at=expires_at)
        if claims is None:
            return None
        token = jwt.encode(self.signer, claims)
        return {"protocol": 1, "capability": token.decode("ascii") if isinstance(token, bytes) else token}

    def issue_session(self, user, username, machine_id, *, expires_at=None):
        if not self._session_secret:
            return self.issue(user, username, machine_id, expires_at=expires_at)
        now = int(self.clock())
        claims = capability_claims(user, username, machine_id, self.email, now=now,
                                   expires_at=expires_at)
        if claims is None:
            return None
        validity = normalized_expiry(user)
        end = _licence_end(validity)
        expiry = min(now + GRANT_SECONDS, end) if end is not None else now + GRANT_SECONDS
        grant = {**claims, "aud": SESSION_AUDIENCE, "exp": expiry,
                 "valid_until": validity, "session_id": uuid.uuid4().hex}
        capability = jwt.encode(self.signer, claims)
        session_grant = _hmac_token(grant, self._session_secret)
        return {"protocol": 2,
                "capability": capability.decode("ascii") if isinstance(capability, bytes) else capability,
                "session_grant": session_grant, "session_expires_at": expiry}

    def renew_session(self, session_grant, machine_id):
        if self.session_verifier is None:
            self.session_verifier = CofreSessionVerifier(self.email, clock=self.clock,
                session_secret=self._session_secret, previous_session_secret=self._previous_secret)
        claims = self.session_verifier.verify(session_grant, machine_id)
        now = int(self.clock())
        capability = {key: claims[key] for key in CLAIMS}
        capability.update(aud=AUDIENCE, iat=now, exp=min(now + SESSION_SECONDS, claims["exp"]))
        if capability["exp"] <= now:
            raise ValueError("invalid_cofre_session")
        try:
            encoded = jwt.encode(self.signer, capability)
        except Exception as exc:
            raise GatewayUnavailable("cofre_signing_unavailable") from exc
        return {"protocol": 2, "capability": encoded.decode("ascii") if isinstance(encoded, bytes) else encoded,
                "session_grant": session_grant, "session_expires_at": claims["exp"]}


class GoogleCofreCapabilityIssuer:
    """Reuse the existing Firebase application's identity and signing permission."""

    def __init__(self, app, session_secret="", previous_session_secret=""):
        self.app = app
        self._session_secret = _session_secret(session_secret)
        self._previous_secret = _session_secret(previous_session_secret)
        self._issuer = None

    def _resolve(self):
        if self._issuer is not None:
            return self._issuer
        credential = self.app.credential.get_credential()
        transport = Request()

        def request(*args, **kwargs):
            kwargs.setdefault("timeout", 8)
            return transport(*args, **kwargs)

        if isinstance(credential, google_credentials.Signing):
            signer, email = credential.signer, credential.signer_email
        else:
            email = (self.app.options.get("serviceAccountId")
                     or getattr(credential, "service_account_email", ""))
            if not email or email == "default":
                response = request(url=_METADATA_EMAIL, headers={"Metadata-Flavor": "Google"})
                if response.status != 200 or len(response.data) > 512:
                    raise ValueError("cofre_signer_unavailable")
                email = response.data.decode("ascii").strip()
            if not isinstance(email, str) or not SERVICE_ACCOUNT_EMAIL.fullmatch(email):
                raise ValueError("invalid_cofre_issuer")
            signer = iam.Signer(request, credential, email)
        self._issuer = CofreCapabilityIssuer(signer, email, session_secret=self._session_secret,
                                              previous_session_secret=self._previous_secret)
        return self._issuer

    def issue(self, user, username, machine_id, *, expires_at=None):
        permissions = normalize_permissions(user.get("permissions") or user.get("permissoes"))
        if (not (permissions.get("full") is True or permissions.get("cofre") is True)
                or not valid_client_id(user_client_id(user))):
            return None
        try:
            return self._resolve().issue(user, username, machine_id, expires_at=expires_at)
        except Exception as exc:
            raise GatewayUnavailable("cofre_signing_unavailable") from exc

    def issue_session(self, user, username, machine_id, *, expires_at=None):
        permissions = normalize_permissions(user.get("permissions") or user.get("permissoes"))
        if (not (permissions.get("full") is True or permissions.get("cofre") is True)
                or not valid_client_id(user_client_id(user))):
            return None
        try:
            return self._resolve().issue_session(user, username, machine_id, expires_at=expires_at)
        except Exception as exc:
            raise GatewayUnavailable("cofre_signing_unavailable") from exc

    def renew_session(self, session_grant, machine_id):
        if not self._session_secret:
            raise GatewayUnavailable("cofre_session_not_configured")
        try:
            issuer = self._resolve()
        except Exception as exc:
            raise GatewayUnavailable("cofre_signing_unavailable") from exc
        return issuer.renew_session(session_grant, machine_id)
