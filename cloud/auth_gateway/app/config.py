"""Environment-backed configuration for the authentication gateway."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping


class GatewayConfigurationError(RuntimeError):
    """Raised when a required server-side setting is missing or invalid."""


def _env_text(values: Mapping[str, str], name: str, default: str = "") -> str:
    return str(values.get(name) or default).strip()


def _env_int(
    values: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = _env_text(values, name, str(default))
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError(f"invalid_integer:{name}") from exc
    if parsed < minimum or parsed > maximum:
        raise GatewayConfigurationError(f"integer_out_of_range:{name}")
    return parsed


@dataclass(frozen=True)
class GatewaySettings:
    project_id: str
    firebase_web_api_key: str
    users_collection: str
    minimum_app_version: str
    firestore_timeout_seconds: int
    identity_timeout_seconds: int
    rate_limit_attempts: int
    rate_limit_window_seconds: int
    google_oauth_client_id: str = ""
    cofre_session_secret: str = field(default="", repr=False)
    cofre_session_previous_secret: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "GatewaySettings":
        values = os.environ if environ is None else environ
        project_id = _env_text(values, "FIREBASE_PROJECT_ID") or _env_text(values, "GOOGLE_CLOUD_PROJECT")
        api_key = _env_text(values, "FIREBASE_WEB_API_KEY")
        collection = _env_text(values, "FIREBASE_USERS_COLLECTION", "jk_sistema_usuarios")
        minimum_version = _env_text(values, "JK_AUTH_MIN_APP_VERSION", "1.0.124")
        google_client = _env_text(values, "GOOGLE_LOGIN_CLIENT_ID")
        if google_client and not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", google_client):
            raise GatewayConfigurationError("invalid:GOOGLE_LOGIN_CLIENT_ID")
        session_secret = _env_text(values, "JK_COFRE_SESSION_SECRET")
        previous_secret = _env_text(values, "JK_COFRE_SESSION_PREVIOUS_SECRET")
        for name, secret in (("JK_COFRE_SESSION_SECRET", session_secret),
                             ("JK_COFRE_SESSION_PREVIOUS_SECRET", previous_secret)):
            if secret and not re.fullmatch(r"[a-fA-F0-9]{64}", secret):
                raise GatewayConfigurationError("invalid:" + name)
        if previous_secret and not session_secret:
            raise GatewayConfigurationError("missing:JK_COFRE_SESSION_SECRET")

        if not project_id:
            raise GatewayConfigurationError("missing:FIREBASE_PROJECT_ID")
        if not api_key:
            raise GatewayConfigurationError("missing:FIREBASE_WEB_API_KEY")
        if "/" in collection or not collection:
            raise GatewayConfigurationError("invalid:FIREBASE_USERS_COLLECTION")

        return cls(
            project_id=project_id,
            firebase_web_api_key=api_key,
            users_collection=collection,
            minimum_app_version=minimum_version,
            firestore_timeout_seconds=_env_int(
                values,
                "JK_AUTH_FIRESTORE_TIMEOUT_SECONDS",
                5,
                minimum=1,
                maximum=30,
            ),
            identity_timeout_seconds=_env_int(
                values,
                "JK_AUTH_IDENTITY_TIMEOUT_SECONDS",
                8,
                minimum=1,
                maximum=30,
            ),
            rate_limit_attempts=_env_int(
                values,
                "JK_AUTH_RATE_LIMIT_ATTEMPTS",
                10,
                minimum=2,
                maximum=100,
            ),
            rate_limit_window_seconds=_env_int(
                values,
                "JK_AUTH_RATE_LIMIT_WINDOW_SECONDS",
                300,
                minimum=30,
                maximum=3600,
            ),
            google_oauth_client_id=google_client,
            cofre_session_secret=session_secret,
            cofre_session_previous_secret=previous_secret,
        )


__all__ = ["GatewayConfigurationError", "GatewaySettings"]
