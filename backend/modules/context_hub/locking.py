"""Context Hub locking component."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import (
    Any,
    Iterator,
    Mapping,
)



from backend.modules.context_hub.contracts import (
    ContextHubConflictError,
    ContextHubPaths,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
)

from backend.modules.context_hub.runtime import (
    _json_canonical,
    _new_id,
    _utc_now,
)

from backend.modules.context_hub.state import CONTEXT_HUB_STATE


def _tenant_thread_lock(paths: ContextHubPaths) -> threading.RLock:
    key = str(paths.internal_dir).lower()
    with CONTEXT_HUB_STATE.tenant_locks_guard:
        return CONTEXT_HUB_STATE.tenant_locks.setdefault(key, threading.RLock())


def _read_lock_owner(lock_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _pid_is_running(pid: object) -> bool:
    try:
        normalized = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized <= 0:
        return False
    if normalized == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_timeout = 0x00000102
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, normalized)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(normalized, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_owned_lock(lock_path: Path, token: str) -> bool:
    owner = _read_lock_owner(lock_path)
    if not token or str(owner.get("token") or "") != token:
        return False
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextlib.contextmanager
def _exclusive_file_lock(paths: ContextHubPaths, *, timeout: float = 8.0) -> Iterator[None]:
    deadline = time.monotonic() + max(0.0, timeout)
    owner_token = _new_id()
    paths.internal_dir.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(paths.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(
                    descriptor,
                    _json_canonical(
                        {"pid": os.getpid(), "token": owner_token, "created_at": _utc_now()}
                    ).encode("utf-8"),
                )
            finally:
                os.close(descriptor)
            break
        except FileExistsError:
            owner = _read_lock_owner(paths.lock_path)
            try:
                stale = (time.time() - paths.lock_path.stat().st_mtime) > 300
            except OSError:
                stale = False
            stale_token = str(owner.get("token") or "")
            if stale and stale_token and not _pid_is_running(owner.get("pid")):
                _remove_owned_lock(paths.lock_path, stale_token)
                continue
            if time.monotonic() >= deadline:
                raise ContextHubConflictError("Outra operacao do Context Hub esta em andamento.")
            time.sleep(0.05)
    try:
        yield
    finally:
        _remove_owned_lock(paths.lock_path, owner_token)


def _safe_remove_tree(path: Path, root: Path) -> None:
    if not path.exists():
        return
    _assert_path_chain_safe(path, root)
    shutil.rmtree(path)
