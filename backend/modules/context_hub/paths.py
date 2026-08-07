"""Context Hub paths component."""

from __future__ import annotations

import os
from typing import Optional



from backend.modules.context_hub.contracts import (
    ContextHubPaths,
    ContextHubValidationError,
    _SAFE_CLIENT_ID_RE,
    _WINDOWS_RESERVED_NAMES,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _snapshot_info_root,
)

from backend.modules.context_hub.runtime import (
    _runtime_config,
)


def _normalize_client_id(client_id: object) -> str:
    raw = str(client_id or "")
    normalized = raw.strip()
    reserved_stem = normalized.split(".", 1)[0].lower()
    if (
        not normalized
        or raw != normalized
        or normalized.endswith((".", " "))
        or normalized != normalized.lower()
        or normalized in {".", ".."}
        or reserved_stem in _WINDOWS_RESERVED_NAMES
        or not _SAFE_CLIENT_ID_RE.fullmatch(normalized)
    ):
        raise ContextHubValidationError("Identificador de cliente invalido para o Context Hub.")
    return normalized


def _tenant_paths(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> ContextHubPaths:
    client = _normalize_client_id(client_id)
    config = _runtime_config(info_root=info_root)
    root_snapshot = _snapshot_info_root(config.info_root)
    root = root_snapshot.absolute
    tenant = root / client
    _assert_path_chain_safe(tenant, root)
    vault = tenant / "ContextVault"
    internal = tenant / "context_hub"
    for candidate in (vault, internal):
        _assert_path_chain_safe(candidate, root)
    final_root_snapshot = _snapshot_info_root(config.info_root)
    if final_root_snapshot != root_snapshot:
        raise ContextHubValidationError(
            "A raiz info do Context Hub foi alterada durante a operacao."
        )
    return ContextHubPaths(
        client_id=client,
        info_root=root,
        tenant_dir=tenant,
        vault_dir=vault,
        generated_dir=vault / "70_Gerado",
        curated_dir=vault / "80_Curadoria",
        internal_dir=internal,
        db_path=internal / "context_hub.db",
        staging_dir=internal / "staging",
        generations_dir=internal / "generations",
        lock_path=internal / "operation.lock",
        journal_path=internal / "publish-journal.json",
        backups_dir=internal / "backups",
        restore_staging_dir=internal / "restore-staging",
    )
