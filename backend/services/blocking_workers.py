"""Keep local blocking work off the server event loop with bounded admission."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Callable, TypeVar

import anyio
from anyio.lowlevel import RunVar


_Result = TypeVar("_Result")
_HEAVY_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("jk_heavy_worker_limiter")
_PENDING_WORKERS: RunVar[set[asyncio.Task]] = RunVar("jk_pending_blocking_workers")
HEAVY_WORKER_LIMIT = 2


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


async def run_blocking(function: Callable[..., _Result], *args, **kwargs) -> _Result:
    """Use normal worker admission for short operational reads such as stores."""
    return await _run_worker(partial(function, *args, **kwargs), None)


async def _run_worker(function: Callable[[], _Result], limiter) -> _Result:
    # Raw asyncio Task.cancel() bypasses AnyIO CancelScope shielding. Keep the
    # task owning the limiter alive, so a disconnected request cannot free its
    # slot while its worker is still calculating or committing a transaction.
    try:
        pending = _PENDING_WORKERS.get()
    except LookupError:
        pending = set()
        _PENDING_WORKERS.set(pending)
    worker = asyncio.create_task(anyio.to_thread.run_sync(
        function, limiter=limiter, abandon_on_cancel=False,
    ), name="jk-blocking-worker")
    pending.add(worker)

    def completed(task):
        pending.discard(task)
        # A cancelled caller cannot retrieve a later worker failure itself.
        if not task.cancelled():
            task.exception()

    worker.add_done_callback(completed)
    with anyio.CancelScope(shield=True):
        return await asyncio.shield(worker)
