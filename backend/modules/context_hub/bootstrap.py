"""Context Hub bootstrap component."""

from __future__ import annotations

import os
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.contracts import (
    ContextHubConflictError,
    ContextHubValidationError,
    VAULT_DIRECTORIES,
)

from backend.modules.context_hub.curation_records import (
    _refresh_curation_dashboard_best_effort,
)

from backend.modules.context_hub.filesystem import (
    _ensure_obsidian_graph_groups,
    _write_json_atomic,
)

from backend.modules.context_hub.journal import (
    _recover_publish_journal,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _tenant_thread_lock,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _runtime_config,
)

from backend.modules.context_hub.storage import (
    _initialize_database,
)

from backend.modules.context_hub.state import CONTEXT_HUB_STATE


def bootstrap_context_hub(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    """Create the persistent vault skeleton and private state for one tenant."""

    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    paths.info_root.mkdir(parents=True, exist_ok=True)
    _assert_path_chain_safe(paths.tenant_dir, paths.info_root)
    paths.tenant_dir.mkdir(parents=True, exist_ok=True)
    for relative in VAULT_DIRECTORIES:
        target = paths.vault_dir / relative
        _assert_path_chain_safe(target, paths.info_root)
        target.mkdir(parents=True, exist_ok=True)
    obsidian = paths.vault_dir / ".obsidian"
    if not obsidian.exists():
        obsidian.mkdir(parents=True, exist_ok=False)
        _write_json_atomic(obsidian / "app.json", {"alwaysUpdateLinks": True})
        _write_json_atomic(obsidian / "community-plugins.json", [])
    elif not obsidian.is_dir():
        raise ContextHubValidationError("Configuracao .obsidian invalida para o Context Hub.")
    _assert_path_chain_safe(obsidian, paths.info_root)
    graph_config = obsidian / "graph.json"
    _assert_path_chain_safe(graph_config, paths.info_root)
    _ensure_obsidian_graph_groups(graph_config)
    for private_dir in (
        paths.staging_dir,
        paths.generations_dir,
        paths.backups_dir,
        paths.restore_staging_dir,
    ):
        _assert_path_chain_safe(private_dir, paths.info_root)
        private_dir.mkdir(parents=True, exist_ok=True)
        _assert_path_chain_safe(private_dir, paths.info_root)
    _initialize_database(paths, config.surface)
    # Recovery mutates only swap artefacts.  Never race it with a live publish.
    if paths.journal_path.exists() and not paths.lock_path.exists():
        with _tenant_thread_lock(paths):
            try:
                with _exclusive_file_lock(paths, timeout=0.0):
                    _recover_publish_journal(paths)
            except ContextHubConflictError:
                pass
    dashboard_key = str(paths.internal_dir).casefold()
    with CONTEXT_HUB_STATE.tenant_locks_guard:
        refresh_dashboard = dashboard_key not in CONTEXT_HUB_STATE.curation_dashboards_refreshed
        if refresh_dashboard:
            CONTEXT_HUB_STATE.curation_dashboards_refreshed.add(dashboard_key)
    if refresh_dashboard and _refresh_curation_dashboard_best_effort(paths) is None:
        with CONTEXT_HUB_STATE.tenant_locks_guard:
            CONTEXT_HUB_STATE.curation_dashboards_refreshed.discard(dashboard_key)
    return {
        "success": True,
        "client_id": paths.client_id,
        "surface": config.surface,
        "initialized": True,
    }
