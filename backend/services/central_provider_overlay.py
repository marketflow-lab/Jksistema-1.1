"""Bling authority alongside local integrations; no network on store reads."""
from __future__ import annotations

import copy
import os
import time

from fastapi import HTTPException


def is_central(config):
    return isinstance(config, dict) and bool(config.get("central") or config.get("central_migrated"))


def unavailable_config(config=None):
    cfg = config or {}
    return {"central": True, "central_migrated": True, "central_scope": "provider",
            "connected": False, "user_id": str(cfg.get("user_id") or ""),
            "site_id": "", "status": "central_indisponivel"}


def overlay_stores(client_id, stores, *, transport=True):
    """Only the authenticated Central can supply a migrated Bling connection."""
    from .central_accounts_client import session_client

    result = copy.deepcopy(stores)
    central = session_client(client_id)
    scoped = central is not None and getattr(central, "provider_scoped", False)
    authorized = {}
    if scoped and central.expires_at > time.time():
        rows = central.stores() if transport else central.public_stores()
        authorized = {row["store_id"]: row for row in rows}
    for row in result:
        configs = row.setdefault("integracoes", {})
        public = authorized.get(str(row.get("store_id") or ""))
        if public is not None:
            cfg = public["integracoes"].get("bling")
            configs["bling"] = (dict(cfg, central_migrated=True, central_scope="provider")
                                  if cfg else unavailable_config(configs.get("bling")))
            row["owner_client_id"] = public["owner_client_id"]
            row["access"] = public["access"]
            unavailable = row.get("_unavailable_providers", [])
            if "bling" in unavailable:
                unavailable.remove("bling")
        elif is_central(configs.get("bling")):
            configs["bling"] = unavailable_config(configs["bling"])
    # Login/explicit refresh materializes authorized stores under the writer
    # lock. A read never resurrects a row excluded by a local tombstone.
    return result


def prepare_local_write(stores, before):
    """Commit local changes without serializing opaque transport references."""
    from .central_accounts_client import is_marker

    # salvar_lojas assigns durable identities back into the caller's rows.
    result = stores
    by_id = {str(row.get("store_id") or ""): row for row in before}
    for row in result:
        configs = row.setdefault("integracoes", {})
        old = (by_id.get(str(row.get("store_id") or ""), {}).get("integracoes") or {}).get("bling")
        if is_central(old):
            from .shared_sync_merge_integracoes import _shared_sync_bling_central_public
            configs["bling"] = _shared_sync_bling_central_public("bling", old)
        elif is_central(configs.get("bling")):
            configs["bling"] = unavailable_config(configs["bling"])
        if any(is_marker((cfg or {}).get("access_token")) for cfg in configs.values() if isinstance(cfg, dict)):
            raise HTTPException(409, "Conexões centrais devem ser alteradas pela central.")
    return result


def assert_local_provider_allowed(client_id, store_id, provider):
    """Late callbacks and offline copies cannot revive a migrated credential."""
    if provider != "bling":
        return
    from . import integracoes
    from .central_accounts_client import current_for_provider

    central = current_for_provider(client_id, provider, store_id)
    if central is not None:
        raise HTTPException(409, "Esta conexão Bling é renovada e alterada pela Central de Contas.")
    path = os.path.join(integracoes._tenant_path(client_id), "lojas_config.json")
    try:
        rows = integracoes._integracoes_ler_lojas_config_arquivo(path)
    except FileNotFoundError:
        return
    for row in rows:
        if str(row.get("store_id") or "") == str(store_id or "") and is_central(
                (row.get("integracoes") or {}).get("bling")):
            raise HTTPException(409, "Entre na Central de Contas para acessar esta conexão Bling.")
