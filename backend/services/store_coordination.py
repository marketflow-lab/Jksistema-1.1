"""Bounded, reentrant coordination for each physical store configuration root."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from backend.services.cadastro_fotos_coordenacao import (
    CadastroFotosCoordenacaoErro,
    _bloquear_arquivo_posix,
    _diretorio_fisico,
)
from backend.services.path_coordination import canonical_path_key, path_lock_for


_LOCAL = threading.local()
DEFAULT_TIMEOUT = 10.0


class StoreCoordinationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__("A configuracao de lojas esta ocupada." if code == "locked"
                         else "Nao foi possivel coordenar a configuracao de lojas.")
        self.code = code


def _state() -> dict:
    if getattr(_LOCAL, "pid", None) != os.getpid():
        _LOCAL.pid = os.getpid()
        _LOCAL.state = {"held": {}, "deadlines": []}
    return _LOCAL.state


def remaining_timeout() -> float:
    """Return the remaining total acquisition budget, including nested scopes."""
    deadlines = _state()["deadlines"]
    return max(0.0, min(deadlines) - time.monotonic()) if deadlines else DEFAULT_TIMEOUT


def coordination_active() -> bool:
    return bool(_state()["deadlines"])


@contextmanager
def coordinated_lock(lock) -> Iterator[None]:
    """Bound acquisition while retaining the lock's existing identity."""
    if not coordination_active():
        with lock:
            yield
        return
    timeout = remaining_timeout()
    acquired = lock.acquire(timeout=timeout) if timeout else lock.acquire(blocking=False)
    if not acquired:
        raise StoreCoordinationError("locked")
    try:
        yield
    finally:
        lock.release()


@contextmanager
def coordinated_path_lock(path: os.PathLike[str] | str) -> Iterator[None]:
    """Use the existing canonical path mutex without an unbounded wait."""
    with coordinated_lock(path_lock_for(path)):
        yield


@contextmanager
def coordinated_path_locks(paths) -> Iterator[None]:
    with ExitStack() as stack:
        for path in sorted({canonical_path_key(path) for path in paths}):
            stack.enter_context(coordinated_path_lock(path))
        yield


@contextmanager
def coordinated_sqlite_lock(path: os.PathLike[str] | str) -> Iterator[None]:
    from backend.services.sqlite_coordination import sqlite_lock_for_path

    with coordinated_lock(sqlite_lock_for_path(path)):
        yield


@contextmanager
def coordinated_sqlite_locks(paths) -> Iterator[None]:
    # Preserve sqlite_coordination's existing acquisition order and namespace.
    with ExitStack() as stack:
        for path in sorted({os.path.normcase(os.path.abspath(os.fspath(p))) for p in paths}):
            stack.enter_context(coordinated_sqlite_lock(path))
        yield


@contextmanager
def _windows_mutex(key: str, *, namespace: str = "JKStores") -> Iterator[None]:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    release = kernel.ReleaseMutex
    release.argtypes = [wintypes.HANDLE]
    release.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(None, False, "Local\\" + namespace + "-" + key)
    if not handle:
        raise StoreCoordinationError("lock_unavailable")
    acquired = False
    try:
        result = wait(handle, min(0xFFFFFFFE, int(remaining_timeout() * 1000)))
        acquired = result in (0x00000000, 0x00000080)
        if not acquired:
            raise StoreCoordinationError("locked" if result == 0x00000102 else "lock_unavailable")
        yield
    finally:
        if acquired:
            release(handle)
        close(handle)


@contextmanager
def _resource_lock(root_path, namespace: str, timeout_seconds: float) -> Iterator[None]:
    try:
        tenant = _diretorio_fisico(root_path, "unsafe_tenant")
    except CadastroFotosCoordenacaoErro as exc:
        raise StoreCoordinationError(exc.code) from exc
    # The shared legacy source precedes all tenant locks, including reentries.
    key = ("0:" if namespace == "JKLegacyStores" else "1:") + canonical_path_key(tenant)
    state = _state()
    held = state["held"]
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    if held and key < max(held):
        raise StoreCoordinationError("lock_order")
    state["deadlines"].append(time.monotonic() + max(0.0, float(timeout_seconds)))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    lock_path = tenant / ("." + digest + ".stores-mutex-key") if os.name == "nt" else (
        Path("/tmp") / (".jk-stores-locks-" + str(os.getuid())) / (digest + ".lock")
    )
    try:
        with coordinated_path_lock(lock_path):
            try:
                cross_process = _windows_mutex(digest, namespace=namespace) if os.name == "nt" else (
                    _bloquear_arquivo_posix(lock_path, remaining_timeout())
                )
                with cross_process:
                    held[key] = 1
                    try:
                        yield
                    finally:
                        held.pop(key, None)
            except CadastroFotosCoordenacaoErro as exc:
                raise StoreCoordinationError(exc.code) from exc
    finally:
        state["deadlines"].pop()


@contextmanager
def store_lock(tenant_path: os.PathLike[str] | str, timeout_seconds: float = DEFAULT_TIMEOUT) -> Iterator[None]:
    """Hold one physical tenant across processes under the shared deadline.

    Multi-tenant callers acquire canonical paths in ascending order before
    downstream catalog, cost, photo and SQLite locks.
    """
    with _resource_lock(tenant_path, "JKStores", timeout_seconds):
        yield


@contextmanager
def legacy_resource_lock(root_path: os.PathLike[str] | str, timeout_seconds: float = DEFAULT_TIMEOUT) -> Iterator[None]:
    """Coordinate the global legacy source before entering any tenant lock."""
    with _resource_lock(root_path, "JKLegacyStores", timeout_seconds):
        yield


__all__ = ["StoreCoordinationError", "store_lock", "coordinated_path_lock",
           "coordinated_path_locks", "remaining_timeout", "coordination_active",
           "coordinated_lock", "coordinated_sqlite_lock", "coordinated_sqlite_locks",
           "legacy_resource_lock"]
