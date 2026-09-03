"""Shared, non-secret contracts for Cadastro catalog collectors."""

from __future__ import annotations

import hashlib
import json
from typing import Any


_BLING_APPLICATION_VOLATILE_KEYS = {
    "access_token",
    "refresh_token",
    "updated_at",
    "_sync_version",
    "_sync_updated_at",
}


def configuracao_catalogo_fingerprint(
    source: str,
    store_id: str,
    store_name: str,
    config: dict[str, Any],
) -> str:
    """Hash one exact integration snapshot without exposing its credentials."""

    serializado = json.dumps(
        {
            "source": str(source or "").strip().lower(),
            "store_id": str(store_id or "").strip(),
            "store_name": str(store_name or "").strip(),
            "config": dict(config or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serializado).hexdigest()


def configuracao_catalogo_aplicacao_fingerprint(
    source: str,
    store_id: str,
    store_name: str,
    config: dict[str, Any],
) -> str:
    """Hash the stable connection identity used from preview to apply.

    A Bling token refresh must not invalidate a completed preview.  The
    volatile OAuth/sync fields are ignored only when the connection has an
    explicit opaque epoch; legacy connections stay fail-closed on the exact
    snapshot until their first successful owned refresh seeds that epoch.
    """

    fonte = str(source or "").strip().lower()
    config_snapshot = dict(config or {})
    if fonte != "bling" or not str(
        config_snapshot.get("oauth_connection_id") or ""
    ).strip():
        return configuracao_catalogo_fingerprint(
            fonte,
            store_id,
            store_name,
            config_snapshot,
        )

    stable_config = {
        key: value
        for key, value in config_snapshot.items()
        if key not in _BLING_APPLICATION_VOLATILE_KEYS
    }
    return configuracao_catalogo_fingerprint(
        fonte,
        store_id,
        store_name,
        stable_config,
    )


__all__ = [
    "configuracao_catalogo_aplicacao_fingerprint",
    "configuracao_catalogo_fingerprint",
]
