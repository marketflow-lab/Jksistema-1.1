"""Keep local blocking work off the server event loop with bounded admission."""

from __future__ import annotations

import asyncio
import os
from functools import partial
from typing import Callable, Literal, TypeVar

import anyio
from anyio.lowlevel import RunVar


_Result = TypeVar("_Result")
_HEAVY_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("jk_heavy_worker_limiter")
_FINANCIAL_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("jk_financial_worker_limiter")
_IMPORT_OFFER_LIMITERS: RunVar[dict[str, anyio.CapacityLimiter]] = RunVar("jk_import_offer_limiters")
_IMPORT_FINANCIAL_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("jk_import_financial_limiter")
_IMPORT_LIST_FINANCIAL_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("jk_import_list_financial_limiter")
_PENDING_WORKERS: RunVar[set[asyncio.Task]] = RunVar("jk_pending_blocking_workers")
HEAVY_WORKER_LIMIT = 2
FINANCIAL_WORKER_LIMIT = 1


def heavy_worker_limiter() -> anyio.CapacityLimiter:
    """Use one limiter per server loop, independent of FastAPI's default pool."""
    try:
        return _HEAVY_LIMITER.get()
    except LookupError:
        limiter = anyio.CapacityLimiter(HEAVY_WORKER_LIMIT)
        _HEAVY_LIMITER.set(limiter)
        return limiter


async def run_heavy(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Copy request ContextVars and retain admission until the worker completes."""
    return await _run_worker(
        partial(function, *args, **kwargs), heavy_worker_limiter(),
    )


def financial_worker_limiter() -> anyio.CapacityLimiter:
    """Reserve at most one heavy worker for financial network enrichment."""
    try:
        return _FINANCIAL_LIMITER.get()
    except LookupError:
        limiter = anyio.CapacityLimiter(FINANCIAL_WORKER_LIMIT)
        _FINANCIAL_LIMITER.set(limiter)
        return limiter


async def run_financial(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Queue financial work before heavy admission, retaining both until done."""
    financial_limiter = financial_worker_limiter()
    heavy_limiter = heavy_worker_limiter()

    async def admitted():
        # Waiting financial requests must not occupy the other heavy slot,
        # which is also used by local import-list and fiscal calculations.
        async with financial_limiter:
            return await _run_worker(partial(function, *args, **kwargs), heavy_limiter)

    return await _retain_worker(asyncio.create_task(admitted(), name="jk-financial-worker"))


async def run_blocking(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Use normal worker admission for short operational reads such as stores."""
    return await _run_worker(partial(function, *args, **kwargs), None)


async def run_import_financial(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Preserve two interactive/legacy slots, independent of list background work."""
    try:
        limiter = _IMPORT_FINANCIAL_LIMITER.get()
    except LookupError:
        limiter = anyio.CapacityLimiter(2)
        _IMPORT_FINANCIAL_LIMITER.set(limiter)
    return await _run_worker(partial(function, *args, **kwargs), limiter)


def import_list_financial_limiter() -> anyio.CapacityLimiter:
    """Use one stable list limit per server loop, shared with its public config."""
    try:
        return _IMPORT_LIST_FINANCIAL_LIMITER.get()
    except LookupError:
        configured = os.getenv("JK_IMPORTACOES_MARGENS_CONCORRENCIA", "4").strip()
        limiter = anyio.CapacityLimiter(2 if configured == "2" else 4)
        _IMPORT_LIST_FINANCIAL_LIMITER.set(limiter)
        return limiter


async def run_import_list_financial(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Bound list analyses without borrowing the interactive or local-read slots."""
    return await _run_worker(partial(function, *args, **kwargs), import_list_financial_limiter())


async def run_import_offer_source(
    source: Literal["planilha", "anuncios"], function: Callable[..., _Result], *args, **kwargs,
) -> _Result:
    """Keep provider waits independent from each other and from cost workers."""
    try:
        limiters = _IMPORT_OFFER_LIMITERS.get()
    except LookupError:
        limiters = {"planilha": anyio.CapacityLimiter(2), "anuncios": anyio.CapacityLimiter(2)}
        _IMPORT_OFFER_LIMITERS.set(limiters)
    return await _run_worker(partial(function, *args, **kwargs), limiters[source])


async def _run_worker(function: Callable[[], _Result], limiter) -> _Result:
    # Raw asyncio Task.cancel() bypasses AnyIO CancelScope shielding. Keep the
    # task owning the limiter alive, so a disconnected request cannot free its
    # slot while its worker is still calculating or committing a transaction.
    worker = asyncio.create_task(anyio.to_thread.run_sync(
        function, limiter=limiter, abandon_on_cancel=False,
    ), name="jk-blocking-worker")
    return await _retain_worker(worker)


async def _retain_worker(worker: asyncio.Task) -> _Result:
    """Keep an admitted or queued operation alive after its caller disconnects."""
    try:
        pending = _PENDING_WORKERS.get()
    except LookupError:
        pending = set()
        _PENDING_WORKERS.set(pending)
    pending.add(worker)
    completion = asyncio.get_running_loop().create_future()

    def completed(task):
        pending.discard(task)
        if task.cancelled():
            if not completion.done():
                completion.cancel()
            return
        # Retrieve failures even after the caller has cancelled its own Future.
        # asyncio.shield on Python 3.14 logs later failures despite retrieval.
        error = task.exception()
        if not completion.done():
            if error is not None:
                completion.set_exception(error)
            else:
                completion.set_result(task.result())

    worker.add_done_callback(completed)
    with anyio.CancelScope(shield=True):
        # Raw Task.cancel() cancels only this completion Future, never worker.
        return await completion
