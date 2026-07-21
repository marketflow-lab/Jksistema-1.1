"""TLS verification policy for outbound IA provider traffic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


TRUSTED_CA_ENV_KEYS = (
    "JK_IA_CA_BUNDLE",
    "IA_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


class TransportSecurityError(RuntimeError):
    """Raised when an explicitly configured CA bundle cannot be trusted."""


def requests_tls_verify(environ: Mapping[str, str] | None = None) -> bool | str:
    values = os.environ if environ is None else environ
    for key in TRUSTED_CA_ENV_KEYS:
        configured = str(values.get(key) or "").strip()
        if not configured:
            continue
        path = Path(configured).expanduser()
        if not path.is_file():
            raise TransportSecurityError(f"trusted_ca_bundle_not_found:{key}")
        return str(path.resolve())
    return True


def configure_requests_session(session: Any, environ: Mapping[str, str] | None = None) -> Any:
    session.verify = requests_tls_verify(environ)
    return session


__all__ = [
    "TRUSTED_CA_ENV_KEYS",
    "TransportSecurityError",
    "configure_requests_session",
    "requests_tls_verify",
]

