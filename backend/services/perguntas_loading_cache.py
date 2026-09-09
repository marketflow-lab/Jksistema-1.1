"""Bounded, scoped read cache for the interactive public-question screen."""

from __future__ import annotations

import copy
import threading
import time
import itertools
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException
import requests

from backend.services.perguntas_loading_transport import read_budget, remaining
from backend.services.perguntas_loading_scheduler import ReadScheduler, unavailable, retain_current_admission


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
_SCHEDULER = ReadScheduler()
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


def peek_current(key: tuple, *, max_age=60):
    """Detached authorized snapshot for component retries; never extends its age."""
    with _LOCK:
        entry = _ENTRIES.get(key)
        if (entry is None or entry.revision != _REVISIONS.get(key[:2], 0)
                or time.monotonic() - entry.monotonic_at > max_age):
            return None
        return {"value": copy.deepcopy(entry.value),
                "origin": (entry.monotonic_at, entry.consulted_at)}


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
    if stale:
        for field in ("components", "item_states"):
            for component in (result.get(field) or {}).values():
                if isinstance(component, dict) and component.get("state") == "ready":
                    component["state"] = "stale"
    return result


def _perform(key: tuple, loader: Callable[[], dict], generation: int) -> Entry:
    with read_budget(seconds=15):
        with _LOCK:
            if generation != _REVISIONS.get(key[:2], 0):
                raise HTTPException(409, "As perguntas mudaram durante a consulta. Atualize a lista.")
        try:
            value = loader()
        except HTTPException as error:
            scope = (error.headers or {}).get("X-JK-Error-Scope", "store")
            if error.status_code in {401, 403} and scope != "resource":
                _discard_scope(key)
            elif error.status_code in {401, 403, 404}:
                with _LOCK:
                    _ENTRIES.pop(key, None)
            raise
    origin = value.pop("_cache_origin", None)
    monotonic_at, consulted_at = time.monotonic(), int(time.time() * 1000)
    if (isinstance(origin, tuple) and len(origin) == 2
            and isinstance(origin[0], (int, float)) and isinstance(origin[1], int)
            and 0 <= origin[0] <= monotonic_at):
        monotonic_at, consulted_at = origin
    entry = Entry(copy.deepcopy(value), monotonic_at, consulted_at, generation)
    with _LOCK:
        if generation != _REVISIONS.get(key[:2], 0):
            raise HTTPException(409, "As perguntas mudaram durante a consulta. Atualize a lista.")
        current = _ENTRIES.get(key)
        if origin is not None and current and current.monotonic_at > entry.monotonic_at:
            # A completed full refresh wins over a partial retry based on an older snapshot.
            return current
        _ENTRIES[key] = entry
        _ENTRIES.move_to_end(key)
        while len(_ENTRIES) > MAX_ENTRIES:
            _ENTRIES.popitem(last=False)
    return entry


def _start(key: tuple, loader: Callable[[], dict], *, flight_variant=()) -> Future:
    # Called under _LOCK; only requests with compatible component work join this future.
    generation = _REVISIONS.setdefault(key[:2], next(_VERSION_SEQUENCE))
    flight_key = (*key, ("variant", flight_variant), generation)
    if flight_key in _RUNNING:
        return _RUNNING[flight_key]
    future = _SCHEDULER.submit(key, lambda: _perform(key, loader, generation))
    _RUNNING[flight_key] = future

    def finished(_future):
        with _LOCK:
            _RUNNING.pop(flight_key, None)
            _prune_revisions_locked()

    future.add_done_callback(finished)
    return future


def read(key: tuple, loader: Callable[[], dict], *, ttl: int, stale_seconds: int = 600,
         force: bool = False, flight_variant=()) -> dict:
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
            future = _start(key, loader, flight_variant=flight_variant)
        except HTTPException:
            if stale_entry and not force:
                return _metadata(stale_entry, key, stale=True, source="memory_cache")
            raise
        if stale_entry and not force:
            return _metadata(stale_entry, key, stale=True, source="memory_cache")
    try:
        available = remaining()
        fresh = future.result(timeout=max(0, (15 if available is None else available) - 0.01))
        # A complementary retry can finish after its reused question expires.
        # Preserve the original timestamp and report that age instead of readiness.
        return _metadata(fresh, key, stale=time.monotonic() - fresh.monotonic_at > ttl,
                         source="mercadolivre")
    except (FutureTimeout, TimeoutError, requests.Timeout):
        error = unavailable(504, "read_timeout")
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
            headers = error.headers or {}
            retry_after = headers.get("Retry-After", "")
            if str(retry_after).isdigit():
                result["retry_after"] = int(retry_after)
                for field in ("components", "item_states"):
                    for component in (result.get(field) or {}).values():
                        if isinstance(component, dict):
                            component["retry_after"] = int(retry_after)
            return result
    raise error
