"""Read-only settings checkpoints for background watcher cycles."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from backend.modules.context_hub.contracts import ContextHubError, ContextHubPaths
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.settings import _settings_from_row


class _WatcherRecoveryRequired(ContextHubError):
    pass


class _WatcherReadBusy(ContextHubError):
    pass


@dataclass(frozen=True)
class _WatcherDatabaseSnapshot:
    device: int
    inode: int
    schema_revision: int


def _watcher_db_identity(paths: ContextHubPaths) -> tuple[int, int]:
    validated = _tenant_paths(paths.client_id, info_root=paths.info_root)
    if validated != paths:
        raise _WatcherRecoveryRequired("Watcher paths changed.")
    for candidate in (paths.db_path, paths.journal_path):
        _assert_path_chain_safe(candidate, paths.info_root)
    if paths.journal_path.exists():
        raise _WatcherRecoveryRequired("Publication recovery is pending.")
    if not paths.db_path.is_file():
        raise _WatcherRecoveryRequired("Watcher database is unavailable.")
    stat = paths.db_path.stat()
    return int(stat.st_dev), int(stat.st_ino)


def _read_watcher_settings(
    paths: ContextHubPaths,
    surface: str,
    expected: _WatcherDatabaseSnapshot | None = None,
) -> tuple[dict, _WatcherDatabaseSnapshot]:
    """Never bootstrap, seed, migrate, change journaling or call _connect."""
    try:
        before = _watcher_db_identity(paths)
        uri = paths.db_path.absolute().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
            connection.row_factory = sqlite3.Row
            schema_before = int(connection.execute("PRAGMA schema_version").fetchone()[0])
            snapshot = _WatcherDatabaseSnapshot(*before, schema_before)
            if expected is not None and snapshot != expected:
                raise _WatcherRecoveryRequired("Watcher database checkpoint changed.")
            row = connection.execute(
                "SELECT auto_publish_enabled, watch_enabled, paused, debounce_seconds, "
                "retention_generations, updated_at FROM context_hub_settings WHERE singleton_id=1"
            ).fetchone()
            schema_after = int(connection.execute("PRAGMA schema_version").fetchone()[0])
        if row is None or schema_before != schema_after or before != _watcher_db_identity(paths):
            raise _WatcherRecoveryRequired("Watcher database changed during its read.")
        if (
            row["auto_publish_enabled"] != 0
            or row["watch_enabled"] not in (0, 1)
            or row["paused"] not in (0, 1)
            or not 1 <= int(row["debounce_seconds"]) <= 300
            or not 1 <= int(row["retention_generations"]) <= 20
        ):
            raise _WatcherRecoveryRequired("Watcher settings require recovery.")
        return _settings_from_row(row, surface), snapshot
    except sqlite3.Error as error:
        code = int(getattr(error, "sqlite_errorcode", 0)) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise _WatcherReadBusy("Watcher database is busy.") from error
        raise _WatcherRecoveryRequired("Watcher database cannot be read safely.") from error
    except (OSError, ValueError, TypeError) as error:
        raise _WatcherRecoveryRequired("Watcher settings cannot be read safely.") from error
