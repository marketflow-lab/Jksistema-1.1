"""Context Hub settings component."""

from __future__ import annotations

import os
import sqlite3
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    ContextHubValidationError,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _tenant_thread_lock,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _runtime_config,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _settings_from_row(row: Optional[sqlite3.Row], surface: str) -> dict[str, Any]:
    if row is None:
        return {
            "auto_publish_enabled": False,
            "watch_enabled": False,
            "paused": False,
            "debounce_seconds": 10,
            "retention_generations": 5,
            "watcher_supported": True,
        }
    return {
        "auto_publish_enabled": False,
        "watch_enabled": bool(row["watch_enabled"]),
        "paused": bool(row["paused"]),
        "debounce_seconds": int(row["debounce_seconds"]),
        "retention_generations": int(row["retention_generations"]),
        "watcher_supported": True,
        "updated_at": row["updated_at"],
    }


def get_settings(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    with _connect(paths) as connection:
        row = connection.execute("SELECT * FROM context_hub_settings WHERE singleton_id=1").fetchone()
    return _settings_from_row(row, config.surface)


def update_settings(
    client_id: object,
    *,
    auto_publish_enabled: Optional[bool] = None,
    paused: Optional[bool] = None,
    watch_enabled: Optional[bool] = None,
    debounce_seconds: Optional[int] = None,
    retention_generations: Optional[int] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    current = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
    if debounce_seconds is not None and not 1 <= int(debounce_seconds) <= 300:
        raise ContextHubValidationError("Debounce deve ficar entre 1 e 300 segundos.")
    if retention_generations is not None and not 1 <= int(retention_generations) <= 20:
        raise ContextHubValidationError("Retencao deve ficar entre 1 e 20 geracoes.")
    if auto_publish_enabled is True:
        raise ContextHubValidationError("Publicacao automatica e proibida; use a acao administrativa de publicar.")
    updated = {
        "auto_publish_enabled": False,
        "watch_enabled": current["watch_enabled"] if watch_enabled is None else bool(watch_enabled),
        "paused": current["paused"] if paused is None else bool(paused),
        "debounce_seconds": current["debounce_seconds"] if debounce_seconds is None else int(debounce_seconds),
        "retention_generations": current["retention_generations"] if retention_generations is None else int(retention_generations),
    }
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE context_hub_settings SET
                    auto_publish_enabled=?, watch_enabled=?, paused=?, debounce_seconds=?,
                    retention_generations=?, updated_at=? WHERE singleton_id=1
                """,
                (
                    int(updated["auto_publish_enabled"]),
                    int(updated["watch_enabled"]),
                    int(updated["paused"]),
                    updated["debounce_seconds"],
                    updated["retention_generations"],
                    _utc_now(),
                ),
            )
            connection.commit()
    return get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
