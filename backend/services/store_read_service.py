"""Authorized operational store reads; never acquire a business writer lock."""
from __future__ import annotations

import copy
import os
import time

from fastapi import HTTPException


def unavailable():
    return HTTPException(503, detail={
        "code": "stores_snapshot_credentials_unavailable",
        "message": "A configuracao das lojas esta sendo atualizada.",
    }, headers={"Retry-After": "2"})


def read_stores(client_id):
    from . import integracoes as source
    from .central_accounts_client import current, session_expired
    from .store_listing_service import read_store_cards
    from .store_public_snapshot import provider_identity

    central = current(client_id)
    if central is not None:
        if central.expires_at <= time.time():
            raise session_expired()
        return source.mesclar_turbo_local(client_id, central.stores(), incluir_token=True)

    cards = read_store_cards(client_id, include_provider_identities=True)
    public = {row["store_id"]: row for row in cards["lojas"] if row.get("store_id")}
    deadline = time.monotonic() + .05
    while True:
        try:
            rows = source._integracoes_ler_lojas_config_arquivo(os.path.join(source._tenant_path(client_id), "lojas_config.json"))
            source._integracoes_validar_identidades_lojas_local(rows)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise unavailable() from None
            time.sleep(.001)
        except (OSError, ValueError, TypeError):
            raise unavailable() from None

    result = []
    for original in rows:
        published = public.get(str(original.get("store_id") or "").strip())
        if published is None:
            continue
        row = copy.deepcopy(original)
        # Names are display metadata; only the published opaque ID authorizes
        # the row. Current names allow commit callers to detect a rename.
        providers = row.setdefault("integracoes", {})
        identities = published.get("provider_identities")
        for provider in ("mercadolivre", "bling"):
            cfg = providers.get(provider) or {}
            if not cfg:
                continue
            if identities is not None:
                fingerprint = provider_identity(original, provider)
                matches = bool(fingerprint) and fingerprint == identities.get(provider)
                if provider == "bling" and not fingerprint:
                    from .store_listing_service import _ensure_initialization, _tenant
                    _ensure_initialization(client_id, _tenant(client_id))
            elif provider == "mercadolivre":
                matches = (str(cfg.get("user_id") or "").strip() == published["seller_id"] and
                           str(cfg.get("site_id") or original.get("site_id") or "").strip() == published["site_id"])
            else:
                # v1 did not bind Bling identities. Refresh the projection in
                # background; never authorize Bling through an ML seller ID.
                matches = False
                from .store_listing_service import _ensure_initialization, _tenant
                _ensure_initialization(client_id, _tenant(client_id))
            if not matches:
                providers.pop(provider, None)
                row.setdefault("_unavailable_providers", []).append(provider)
        result.append(row)
    return result


def find_store(client_id, nome_loja, store_id=None):
    from . import integracoes as source
    return source._integracoes_encontrar_loja_identidade(read_stores(client_id), nome_loja, store_id)
