"""Canonical in-process coordination for read/modify/replace file cycles."""

from __future__ import annotations

import os
import threading
from contextlib import ExitStack, contextmanager
from typing import Iterable, Iterator


_PATH_LOCKS_GUARD = threading.RLock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def canonical_path_key(path: os.PathLike[str] | str) -> str:
    """Return one stable key for equivalent absolute/relative file paths."""

    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def path_lock_for(path: os.PathLike[str] | str) -> threading.RLock:
    """Return the shared reentrant lock for a filesystem path."""

    key = canonical_path_key(path)
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def path_locks_for(paths: Iterable[os.PathLike[str] | str]) -> Iterator[None]:
    """Acquire unique path locks in canonical order to prevent deadlocks."""

    ordered = sorted({canonical_path_key(path) for path in paths})
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(path_lock_for(path))
        yield


__all__ = ["canonical_path_key", "path_lock_for", "path_locks_for"]
