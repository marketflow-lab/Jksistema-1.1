"""Bounded, scoped read cache for the interactive public-question screen."""

from __future__ import annotations

import copy
import contextvars
import threading
import time
import weakref
import itertools
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException

from backend.services.perguntas_loading_transport import read_budget, remaining


@dataclass
class Entry:
    value: dict
    monotonic_at: float
    consulted_at: int
    revision: int


_LOCK = threading.RLock()
_ENTRIES: OrderedDict[tuple, Entry] = OrderedDict()
_RUNNING: dict[tuple, Future] = {}
_REVISIONS: OrderedDict[tuple[str, str], int] = OrderedDict()
_VERSION_SEQUENCE = itertools.count(1)
_CLIENT_SLOTS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="questions-read")
_QUEUE = threading.BoundedSemaphore(64)
MAX_ENTRIES = 500


def _prune_revisions_locked() -> None:
    if len(_REVISIONS) <= MAX_ENTRIES + 64:
        return
    active = {key[:2] for key in _ENTRIES} | {key[:2] for key in _RUNNING}
    for existing in list(_REVISIONS):
        if len(_REVISIONS) <= MAX_ENTRIES + 64:
            break
        if existing not in active:
            del _REVISIONS[existing]


def revision(client_id: str, store_id: str) -> int:
    with _LOCK:
        return _REVISIONS.get((client_id, store_id), 0)


def invalidate_store(client_id: str, store_id: str) -> None:
    """Invalidate only mutable question data; fence already running reads."""
    with _LOCK:
        scope = (client_id, store_id)
        _REVISIONS[scope] = next(_VERSION_SEQUENCE)
        _REVISIONS.move_to_end(scope)
        for key in list(_ENTRIES):
            if key[:2] == scope:
                if key[5] in {"items", "metrics", "identity"}:
                    _ENTRIES[key].revision = _REVISIONS[scope]
                else:
                    del _ENTRIES[key]
        _prune_revisions_locked()


def peek_items(scope_key: tuple, item_ids: list[str]) -> dict[str, dict]:
    """Reuse fresh authorized item batches without creating work or extending TTL."""
    wanted = set(item_ids)
    result = {}
    now = time.monotonic()
    with _LOCK:
        for key, entry in reversed(_ENTRIES.items()):
            if key[:5] != scope_key[:5] or key[5] != "items" or now - entry.monotonic_at > 900:
                continue
            for item in entry.value.get("items", []):
                item_id = str(item.get("id") or "")
                if item_id in wanted and item_id not in result:
                    result[item_id] = copy.deepcopy(item)
            if len(result) == len(wanted):
                break
    return result


def _discard_scope(key: tuple) -> None:
    # Never keep stale content after a provider explicitly denies access.
    with _LOCK:
        _REVISIONS[key[:2]] = next(_VERSION_SEQUENCE)
        for existing in list(_ENTRIES):
            if existing[:2] == key[:2]:
                del _ENTRIES[existing]


def _metadata(entry: Entry, key: tuple, *, stale: bool, source: str) -> dict:
    with _LOCK:
        current_revision = revision(key[0], key[1])
        if entry.revision != current_revision:
            raise HTTPException(409, "As perguntas mudaram durante a consulta. Atualize a lista.")
        result = copy.deepcopy(entry.value)
    metadata = {
        "consultado_em": entry.consulted_at,
        "source": source,
        "stale": stale,
        "partial": bool(result.get("partial")),
        "revision": entry.revision,
    }
    result.update(metadata)
    result["metadata"] = dict(metadata)
    return result


def _perform(key: tuple, loader: Callable[[], dict], generation: int) -> Entry:
    with _LOCK:
        slots = _CLIENT_SLOTS.setdefault(key[0], threading.BoundedSemaphore(4))
    with read_budget(seconds=15):
        if not slots.acquire(timeout=max(0, (remaining() or 15) - 0.01)):
            raise HTTPException(503, "Consultas em andamento. Tente novamente em instantes.")
        try:
            value = loader()
        except HTTPException as error:
            if error.status_code in {401, 403}:
                _discard_scope(key)
            raise
        finally:
            slots.release()
    entry = Entry(copy.deepcopy(value), time.monotonic(), int(time.time() * 1000), generation)
    with _LOCK:
        if generation != _REVISIONS.get(key[:2], 0):
            raise HTTPException(409, "As perguntas mudaram durante a consulta. Atualize a lista.")
        _ENTRIES[key] = entry
        _ENTRIES.move_to_end(key)
        while len(_ENTRIES) > MAX_ENTRIES:
            _ENTRIES.popitem(last=False)
    return entry


def _start(key: tuple, loader: Callable[[], dict]) -> Future:
    # Called under _LOCK; every concurrent request for this key joins this future.
    generation = _REVISIONS.setdefault(key[:2], next(_VERSION_SEQUENCE))
    flight_key = (*key, generation)
    if flight_key in _RUNNING:
        return _RUNNING[flight_key]
    if not _QUEUE.acquire(blocking=False):
        raise HTTPException(503, "Fila de consultas ocupada. Tente novamente em instantes.")
    context = contextvars.copy_context()
    future = _POOL.submit(context.run, _perform, key, loader, generation)
    _RUNNING[flight_key] = future

    def finished(_future):
        with _LOCK:
            _RUNNING.pop(flight_key, None)
            _prune_revisions_locked()
        _QUEUE.release()

    future.add_done_callback(finished)
    return future


def read(key: tuple, loader: Callable[[], dict], *, ttl: int, stale_seconds: int = 600,
         force: bool = False) -> dict:
    """Caller must authorize the complete scope before invoking this function.

    Key layout: tenant, exact store, user, seller, site, kind, query parameters.
    Stale is bounded from the original successful consultation, not the last hit.
    """
    with _LOCK:
        entry = _ENTRIES.get(key)
        age = time.monotonic() - entry.monotonic_at if entry else float("inf")
        if entry is not None:
            _ENTRIES.move_to_end(key)
        effective_ttl = min(ttl, 5) if entry and entry.value.get("partial") else ttl
        if entry and age <= effective_ttl and not force:
            return _metadata(entry, key, stale=False, source="memory_cache")
        stale_entry = entry if entry and age <= max(ttl, stale_seconds) else None
        try:
            future = _start(key, loader)
        except HTTPException:
            if stale_entry and not force:
                return _metadata(stale_entry, key, stale=True, source="memory_cache")
            raise
        if stale_entry and not force:
            return _metadata(stale_entry, key, stale=True, source="memory_cache")
    try:
        available = remaining()
        fresh = future.result(timeout=max(0, (15 if available is None else available) - 0.01))
        return _metadata(fresh, key, stale=False, source="mercadolivre")
    except (FutureTimeout, TimeoutError):
        error = HTTPException(504, "O Mercado Livre demorou para responder. Tente atualizar novamente.")
    except HTTPException as caught:
        if caught.status_code in {401, 403, 404, 409}:
            raise
        error = caught
    except Exception:
        error = HTTPException(502, "Nao foi possivel atualizar os dados do Mercado Livre.")
    with _LOCK:
        # A concurrent answer or denial must not resurrect the stale snapshot.
        current = _ENTRIES.get(key)
        if stale_entry and current is stale_entry and stale_entry.revision == revision(key[0], key[1]):
            result = _metadata(stale_entry, key, stale=True, source="memory_cache")
            result["erro"] = str(error.detail)
            result["partial"] = result["metadata"]["partial"] = True
            return result
    raise error
