"""Bounded process-local cache of derived static inventory objects."""

from __future__ import annotations

import contextlib
import contextvars
import copy
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Iterator

from .cache_sources import _static_source_signature
from .contracts import INVENTORY_SCHEMA_VERSION, SKU_SCHEMA_VERSION


_CACHE_FORMAT_VERSION = 1
_FORCE_REBUILD = contextvars.ContextVar("context_inventory_force_rebuild", default=False)


@contextlib.contextmanager
def _inventory_cache_bypass(force: bool) -> Iterator[None]:
    token = _FORCE_REBUILD.set(bool(force))
    try:
        yield
    finally:
        _FORCE_REBUILD.reset(token)


def _object_size(value: Any) -> int:
    """Account for actual Python containers, keys and values, without double counts."""
    seen: set[int] = set()
    pending = [value]
    total = 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
    return total


class _StaticInventoryCache:
    def __init__(self, *, max_entries: int = 4, max_bytes: int = 64 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._guard = threading.Lock()
        self._entries: OrderedDict[tuple, tuple[dict, int]] = OrderedDict()
        self._base_bytes = sys.getsizeof(self._entries)
        self._flights: dict[tuple, threading.Event] = {}
        self._bytes = 0

    def _key(self, base: Path, surface: str) -> tuple:
        return (
            str(base), surface, INVENTORY_SCHEMA_VERSION, SKU_SCHEMA_VERSION,
            _CACHE_FORMAT_VERSION, *_static_source_signature(base),
        )

    def _store(self, key: tuple, value: dict) -> None:
        stored = copy.deepcopy(value)
        size = _object_size((key, stored, 0)) + sys.getsizeof(0)
        # Include the OrderedDict node overhead, not just serialized payload bytes.
        size += sys.getsizeof(OrderedDict([(key, (None, 0))])) - sys.getsizeof(OrderedDict())
        with self._guard:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            if size + self._base_bytes > self.max_bytes or self.max_entries <= 0:
                return
            while self._entries and (
                len(self._entries) >= self.max_entries
                or self._bytes + size + self._base_bytes > self.max_bytes
            ):
                _old_key, (_old_value, old_size) = self._entries.popitem(last=False)
                self._bytes -= old_size
            self._entries[key] = (stored, size)
            self._bytes += size

    def get_or_build(
        self, base: Path, surface: str, build: Callable[[], dict], *, force: bool = False,
    ) -> dict:
        bypass = bool(force or _FORCE_REBUILD.get())
        changes = 0
        while changes < 3:
            key = self._key(base, surface)
            with self._guard:
                hit = None if bypass else self._entries.get(key)
                if hit is not None:
                    self._entries.move_to_end(key)
                flight = self._flights.get(key)
                owner = hit is None and flight is None
                if owner:
                    flight = threading.Event()
                    self._flights[key] = flight
            if hit is not None:
                isolated = copy.deepcopy(hit[0])
                if key == self._key(base, surface):
                    return isolated
                changes += 1
                continue
            if not owner:
                flight.wait()
                continue
            try:
                value = build()
                if key != self._key(base, surface):
                    changes += 1
                    continue
                self._store(key, value)
                return value
            finally:
                with self._guard:
                    self._flights.pop(key, None)
                flight.set()
        raise ValueError("Inventory sources changed repeatedly during construction.")


STATIC_INVENTORY_CACHE = _StaticInventoryCache()
