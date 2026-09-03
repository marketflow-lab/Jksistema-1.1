"""Bounded lifecycle for concurrent public-web search prefetch."""

from __future__ import annotations

import hashlib
import time
from queue import Empty, Queue
from threading import BoundedSemaphore, Lock, Thread
from typing import Any, Optional


_CIRCUIT_LOCK = Lock()
_OPEN_CIRCUITS: dict[int, tuple[object, set[object]]] = {}


class _SlotLease:
    """Release one acquired permit at most once, even across two threads."""

    def __init__(self, slots: BoundedSemaphore) -> None:
        self._slots = slots
        self._lock = Lock()
        self._released = False

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
            self._slots.release()


class _SearchInvocation:
    """Track live workers so one stalled callable stays isolated until recovery."""

    def __init__(self, search: object) -> None:
        self.search = search
        self._lock = Lock()
        self._unfinished_workers = 0
        self._circuit_registered = False

    def worker_started(self) -> None:
        with self._lock:
            self._unfinished_workers += 1

    def worker_finished(self) -> None:
        with self._lock:
            if self._unfinished_workers > 0:
                self._unfinished_workers -= 1
            clear_circuit = self._unfinished_workers == 0 and self._circuit_registered
        if clear_circuit:
            _clear_search_circuit(self)

    def register_stall(self) -> bool:
        with self._lock:
            if self._unfinished_workers <= 0:
                return False
            self._circuit_registered = True
            key = id(self.search)
            with _CIRCUIT_LOCK:
                state = _OPEN_CIRCUITS.get(key)
                if state is None or state[0] is not self.search:
                    state = (self.search, set())
                    _OPEN_CIRCUITS[key] = state
                state[1].add(self)
            return True

    def unfinished_workers(self) -> int:
        with self._lock:
            return self._unfinished_workers


def _clear_search_circuit(invocation: _SearchInvocation) -> None:
    key = id(invocation.search)
    with _CIRCUIT_LOCK:
        state = _OPEN_CIRCUITS.get(key)
        if state is None or state[0] is not invocation.search:
            return
        state[1].discard(invocation)
        if not state[1]:
            _OPEN_CIRCUITS.pop(key, None)


def web_search_circuit_open(search: object) -> bool:
    """Return whether this exact search callable still owns stalled workers."""

    key = id(search)
    with _CIRCUIT_LOCK:
        state = _OPEN_CIRCUITS.get(key)
        return bool(state is not None and state[0] is search and state[1])


def prefetch_web(
    client_id: str,
    queries: list[str],
    search,
    *,
    query_slots: BoundedSemaphore,
    worker_slots: BoundedSemaphore,
    max_seconds: float,
    logger,
    thread_factory=Thread,
    deadline_monotonic: Optional[float] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Prefetch safely, restoring caller capacity while bounding stalled workers."""

    results: dict[str, list[dict[str, Any]]] = {}
    if not queries:
        return results
    started_at = time.monotonic()
    internal_deadline = started_at + max(0.01, float(max_seconds))
    try:
        requested_deadline = (
            float(deadline_monotonic) if deadline_monotonic is not None else internal_deadline
        )
    except (TypeError, ValueError):
        requested_deadline = internal_deadline
    deadline = min(requested_deadline, internal_deadline)
    if deadline <= started_at or web_search_circuit_open(search):
        return results
    queue: Queue = Queue()
    lock = Lock()
    accept_results = True
    invocation = _SearchInvocation(search)
    for query in queries:
        queue.put_nowait(query)

    def worker(query_lease: _SlotLease, worker_lease: _SlotLease) -> None:
        try:
            while time.monotonic() < deadline:
                try:
                    query = queue.get_nowait()
                except Empty:
                    return
                try:
                    try:
                        value = search(query, client_id=client_id, max_results=8, fast=True)
                    except Exception as exc:
                        logger.warning(
                            "[IA AGENT PERGUNTAS] Falha na busca rapida query_hash=%s erro=%s",
                            hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest()[:16],
                            type(exc).__name__,
                        )
                        value = []
                    if time.monotonic() < deadline:
                        with lock:
                            if not accept_results:
                                return
                            results[query] = value if isinstance(value, list) else []
                finally:
                    queue.task_done()
        finally:
            query_lease.release()
            worker_lease.release()
            invocation.worker_finished()

    workers: list[tuple[Thread, _SlotLease, _SlotLease]] = []
    for index in range(min(6, len(queries))):
        if not query_slots.acquire(blocking=False):
            break
        query_lease = _SlotLease(query_slots)
        if not worker_slots.acquire(blocking=False):
            query_lease.release()
            break
        worker_lease = _SlotLease(worker_slots)
        try:
            thread = thread_factory(
                target=worker,
                args=(query_lease, worker_lease),
                name=f"ml-questions-web-{index + 1}",
                daemon=True,
            )
        except Exception as exc:
            query_lease.release()
            worker_lease.release()
            logger.warning(
                "[IA AGENT PERGUNTAS] Falha ao preparar busca rapida: %s",
                type(exc).__name__,
            )
            continue
        workers.append((thread, query_lease, worker_lease))
    started_workers: list[tuple[Thread, _SlotLease]] = []
    for thread, query_lease, worker_lease in workers:
        invocation.worker_started()
        try:
            thread.start()
            started_workers.append((thread, query_lease))
        except Exception as exc:
            invocation.worker_finished()
            query_lease.release()
            worker_lease.release()
            logger.warning(
                "[IA AGENT PERGUNTAS] Falha ao iniciar busca rapida: %s",
                type(exc).__name__,
            )
    for thread, _query_lease in started_workers:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        thread.join(timeout=remaining)
    if invocation.unfinished_workers() > 0:
        invocation.register_stall()
        for _thread, query_lease in started_workers:
            query_lease.release()
    with lock:
        accept_results = False
        return dict(results)
