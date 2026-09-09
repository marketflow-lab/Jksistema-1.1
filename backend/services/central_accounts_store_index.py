"""Persist only authorized central identities for the local Cadastro validator."""
from __future__ import annotations

import copy
import os

from fastapi import HTTPException

from backend.services import integracoes


def _local_stores(client_id):
    path = os.path.join(integracoes._tenant_path(client_id), "lojas_config.json")
    try:
        stores = integracoes._integracoes_ler_lojas_config_arquivo(path)
        integracoes._integracoes_validar_identidades_lojas_local(stores)
    except FileNotFoundError:
        stores = []
    except (ValueError, OSError):
        raise HTTPException(409, "O índice local de lojas não pôde ser validado.") from None
    return path, stores


def assert_legacy_sync_allowed(client_id):
    """Old machine snapshots must never reinstate a migrated refresh authority."""
    from backend.services.central_accounts_client import current

    if current(client_id) is not None:
        raise HTTPException(409, "Atualize as lojas pela Central de Contas; as conexões já são centrais.")
    with integracoes._LOJAS_CONFIG_LOCK:
        _, stores = _local_stores(client_id)
        if any(cfg.get("central") or cfg.get("central_migrated")
               for store in stores for cfg in (store.get("integracoes") or {}).values()
               if isinstance(cfg, dict)):
            raise HTTPException(409, "As conexões foram migradas. Entre na Central de Contas para atualizar as lojas.")


def materialize_store_index(client_id, public_stores):
    from backend.services.central_accounts_client import validate_public_stores
    from backend.services.shared_sync_merge_sqlite import _shared_sync_store_ids_tombstonados

    validate_public_stores(public_stores)
    with integracoes._integracoes_bloquear_rmw_lojas(client_id):
        path, before = _local_stores(client_id)
        tombstones = integracoes._integracoes_ler_tombstones_estrito(client_id)
        excluded = _shared_sync_store_ids_tombstonados(tombstones)
        result = copy.deepcopy(before)
        by_id = {str(row.get("store_id") or ""): row for row in result}
        for public in public_stores:
            store_id = public["store_id"]
            if store_id in excluded:
                continue
            row = by_id.get(store_id)
            if row is None:
                row = {"store_id": store_id, "integracoes": {}}
                result.append(row)
                by_id[store_id] = row
            row.update(nome=public["nome"], owner_client_id=public["owner_client_id"],
                       access=public["access"])
            configs = row.setdefault("integracoes", {})
            # Entire central provider configs are allowlisted; no transport markers,
            # provider secrets or stale invalid-token flags belong in this index.
            for provider in ("mercadolivre", "bling"):
                if provider in public["integracoes"]:
                    configs[provider] = {**copy.deepcopy(public["integracoes"][provider]),
                                         "central_migrated": True}
                elif provider in configs:
                    configs[provider] = {"central": True, "central_migrated": True,
                                         "connected": False}
        integracoes._integracoes_validar_identidades_lojas_local(result)
        if result != before:
            integracoes._integracoes_escrever_lojas_config_atomico(path, result)
    return result
