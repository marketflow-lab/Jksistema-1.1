"""Issue a company-scoped Cofre capability without database or Firebase Auth calls."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.auth import credentials as google_credentials, iam, jwt
from google.auth.transport.requests import Request

from .errors import GatewayUnavailable
from .policy import machine_hash, normalize_permissions, normalized_expiry, user_client_id


AUDIENCE = "jk-cofre"
SESSION_SECONDS = 8 * 3600
CLAIMS = {"iss", "aud", "sub", "client_id", "machine_hash", "scope", "iat", "exp"}
SERVICE_ACCOUNT_EMAIL = re.compile(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.gserviceaccount\.com")
_METADATA_EMAIL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{x}" for x in range(1, 10)), *(f"lpt{x}" for x in range(1, 10))}


def valid_client_id(value):
    return (isinstance(value, str) and bool(_TENANT.fullmatch(value))
            and not value.endswith(".") and value.split(".")[0].lower() not in _RESERVED)


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

    def __init__(self, signer, email, *, clock=time.time):
        self.signer, self.email, self.clock = signer, email, clock

    def issue(self, user, username, machine_id, *, expires_at=None):
        claims = capability_claims(user, username, machine_id, self.email,
                                   now=self.clock(), expires_at=expires_at)
        if claims is None:
            return None
        token = jwt.encode(self.signer, claims)
        return {"protocol": 1, "capability": token.decode("ascii") if isinstance(token, bytes) else token}


class GoogleCofreCapabilityIssuer:
    """Reuse the existing Firebase application's identity and signing permission."""

    def __init__(self, app):
        self.app = app
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
        self._issuer = CofreCapabilityIssuer(signer, email)
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
