"""Context Hub watchers component."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import (
    Any,
    Iterator,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    ContextHubError,
    ContextHubPaths,
    ContextHubRuntimeConfig,
    ContextHubValidationError,
)

from backend.modules.context_hub.generations import (
    rebuild_context,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_relative_to,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _json_canonical,
    _runtime_config,
    _sha256_text,
)

from backend.modules.context_hub.settings import (
    get_settings,
)

from backend.modules.context_hub.state import CONTEXT_HUB_STATE


def _watch_roots(config: ContextHubRuntimeConfig, paths: ContextHubPaths) -> list[Path]:
    del config
    candidates = [paths.curated_dir]
    allowed: list[Path] = []
    for candidate in candidates:
        authority = paths.info_root
        try:
            _assert_path_chain_safe(candidate, authority)
        except ContextHubValidationError:
            continue
        if candidate.exists():
            allowed.append(candidate)
    return allowed


_WATCH_EXCLUDED_DIRS = {
    ".git",
    ".obsidian",
    "__pycache__",
    "build",
    "context_hub",
    "contextvault",
    "dist",
    "dist-client-setup",
    "logs",
    "node_modules",
    "test-results",
}


def _iter_watch_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for directory, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
        dir_names[:] = [
            name
            for name in dir_names
            if name.lower() not in _WATCH_EXCLUDED_DIRS
            and not name.lower().startswith(".venv")
            and not name.lower().startswith("dist-")
            and not name.lower().startswith(".context-hub")
        ]
        current = Path(directory)
        for file_name in file_names:
            yield current / file_name


def _watch_fingerprint(config: ContextHubRuntimeConfig, paths: ContextHubPaths) -> str:
    records: list[tuple[str, str]] = []
    accepted_suffixes = {".py", ".js", ".html", ".json", ".md", ".toml", ".yml", ".yaml"}
    for root in _watch_roots(config, paths):
        for candidate in _iter_watch_files(root):
            if not candidate.is_file() or candidate.suffix.lower() not in accepted_suffixes:
                continue
            authority = paths.info_root if _is_relative_to(candidate.absolute(), paths.info_root.absolute()) else config.base_dir
            try:
                _assert_path_chain_safe(candidate, authority)
                stat = candidate.stat()
            except (OSError, ContextHubValidationError):
                continue
            try:
                relative = candidate.relative_to(authority).as_posix()
            except ValueError:
                continue
            if stat.st_size > 1_000_000:
                digest = f"oversize:{int(stat.st_size)}:{int(stat.st_mtime_ns)}"
            else:
                try:
                    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                except OSError:
                    continue
            records.append((relative, digest))
    return _sha256_text(_json_canonical(sorted(records)))


def scan_context_hub_changes(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    fingerprint = _watch_fingerprint(config, paths)
    key = str(paths.internal_dir).lower()
    with CONTEXT_HUB_STATE.watchers_guard:
        previous = CONTEXT_HUB_STATE.watch_fingerprints.get(key)
        CONTEXT_HUB_STATE.watch_fingerprints[key] = fingerprint
    return {
        "success": True,
        "client_id": paths.client_id,
        "changed": previous is not None and previous != fingerprint,
        "initialized": previous is not None,
        "fingerprint": fingerprint,
    }


def _watch_loop(config: ContextHubRuntimeConfig, paths: ContextHubPaths, stop_event: threading.Event) -> None:
    last_change: Optional[float] = None
    while not stop_event.wait(1.0):
        try:
            settings = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
            if settings["paused"] or not settings["watch_enabled"]:
                last_change = None
                continue
            scan = scan_context_hub_changes(
                paths.client_id,
                base_dir=config.base_dir,
                info_root=config.info_root,
                surface=config.surface,
            )
            if scan["changed"]:
                last_change = time.monotonic()
            if last_change is not None and time.monotonic() - last_change >= settings["debounce_seconds"]:
                rebuild_context(
                    paths.client_id,
                    reason="watcher_change",
                    base_dir=config.base_dir,
                    info_root=config.info_root,
                    surface=config.surface,
                )
                last_change = None
        except ContextHubError:
            # Fail closed and try again; no source content or exception text is logged.
            last_change = None
        except Exception:
            last_change = None


def start_context_hub_watcher(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    bootstrap_context_hub(paths.client_id, base_dir=config.base_dir, info_root=config.info_root, surface=config.surface)
    settings = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
    if not settings["watch_enabled"] or settings["paused"]:
        return {"success": False, "client_id": paths.client_id, "started": False, "reason": "watcher_disabled"}
    key = str(paths.internal_dir).lower()
    with CONTEXT_HUB_STATE.watchers_guard:
        existing = CONTEXT_HUB_STATE.watchers.get(key)
        if existing and existing[0].is_alive():
            return {"success": True, "client_id": paths.client_id, "started": False, "already_running": True}
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_watch_loop,
            args=(config, paths, stop_event),
            name=f"context-hub-{paths.client_id}",
            daemon=True,
        )
        CONTEXT_HUB_STATE.watchers[key] = (thread, stop_event)
        CONTEXT_HUB_STATE.watch_fingerprints[key] = _watch_fingerprint(config, paths)
        thread.start()
    return {"success": True, "client_id": paths.client_id, "started": True}


def stop_context_hub_watcher(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    key = str(paths.internal_dir).lower()
    with CONTEXT_HUB_STATE.watchers_guard:
        watcher = CONTEXT_HUB_STATE.watchers.pop(key, None)
        CONTEXT_HUB_STATE.watch_fingerprints.pop(key, None)
    if watcher:
        thread, event = watcher
        event.set()
        thread.join(timeout=3.0)
    return {"success": True, "client_id": paths.client_id, "stopped": bool(watcher)}


def stop_all_context_hub_watchers() -> None:
    with CONTEXT_HUB_STATE.watchers_guard:
        watchers = list(CONTEXT_HUB_STATE.watchers.values())
        CONTEXT_HUB_STATE.watchers.clear()
        CONTEXT_HUB_STATE.watch_fingerprints.clear()
    for thread, event in watchers:
        event.set()
        thread.join(timeout=3.0)
