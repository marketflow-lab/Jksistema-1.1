"""Coordenacao SQLite compartilhada entre servicos do backend.

Os locks sao locais ao processo e indexados pelo caminho canonico do banco.
Consumidores que precisem tocar mais de um arquivo devem usar
``sqlite_locks_for_paths`` para manter uma ordem global e evitar deadlocks.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import ExitStack, contextmanager
from typing import Iterable, Iterator


SQLITE_BUSY_TIMEOUT_MS = 15_000

_SQLITE_LOCKS_GUARD = threading.RLock()
_SQLITE_LOCKS: dict[str, threading.RLock] = {}


def _sqlite_path_key(path: os.PathLike[str] | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def sqlite_lock_for_path(path: os.PathLike[str] | str) -> threading.RLock:
    """Retorna o lock reentrante compartilhado para um arquivo SQLite."""

    key = _sqlite_path_key(path)
    with _SQLITE_LOCKS_GUARD:
        lock = _SQLITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SQLITE_LOCKS[key] = lock
        return lock


@contextmanager
def sqlite_locks_for_paths(paths: Iterable[os.PathLike[str] | str]) -> Iterator[None]:
    """Adquire locks unicos em ordem deterministica e libera em ordem inversa."""

    ordered = sorted({_sqlite_path_key(path) for path in paths})
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(sqlite_lock_for_path(path))
        yield


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    writable: bool = False,
    busy_timeout_ms: int = SQLITE_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Aplica as configuracoes comuns e devolve a propria conexao."""

    timeout_ms = max(0, int(busy_timeout_ms))
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    if writable:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


__all__ = [
    "SQLITE_BUSY_TIMEOUT_MS",
    "configure_sqlite_connection",
    "sqlite_lock_for_path",
    "sqlite_locks_for_paths",
]
