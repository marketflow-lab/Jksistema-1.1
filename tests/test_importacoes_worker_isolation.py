"""Financial network waits leave local import calculations a heavy-worker slot."""

import asyncio
import contextvars
import threading

import anyio
import pytest

from backend.services import blocking_workers as workers


_REQUEST = contextvars.ContextVar("financial_test_request", default="")


class _Activity:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = self.financial = self.maximum = self.financial_maximum = 0


class _Work:
    def __init__(self, activity, *, financial=False):
        self.activity = activity
        self.financial = financial
        self.entered = threading.Event()
        self.release = threading.Event()
        self.context = None
        self.thread = None

    def run(self, label, *, fail=False):
        self.context = _REQUEST.get()
        self.thread = threading.get_ident()
        with self.activity.lock:
            self.activity.active += 1
            self.activity.financial += int(self.financial)
            self.activity.maximum = max(self.activity.maximum, self.activity.active)
            self.activity.financial_maximum = max(self.activity.financial_maximum, self.activity.financial)
        self.entered.set()
        try:
            assert self.release.wait(5), "Synthetic blocking work was not released"
            if fail:
                raise RuntimeError("Synthetic financial failure")
            return {"label": label, "request": self.context}
        finally:
            with self.activity.lock:
                self.activity.active -= 1
                self.activity.financial -= int(self.financial)


async def _started(work):
    assert await anyio.to_thread.run_sync(work.entered.wait, 2), "Synthetic worker did not start"


async def _idle():
    with anyio.fail_after(2):
        while (workers.heavy_worker_limiter().borrowed_tokens
               or workers.financial_worker_limiter().borrowed_tokens
               or workers._PENDING_WORKERS.get()):
            await anyio.sleep(.01)


def test_financial_queue_leaves_a_local_slot_preserves_context_and_global_bound():
    async def scenario():
        loop_thread = threading.get_ident()
        activity = _Activity()
        financial = [_Work(activity, financial=True) for _ in range(2)]
        local = [_Work(activity) for _ in range(2)]

        async def request(work, label, runner):
            token = _REQUEST.set(label)
            try:
                return await runner(work.run, label, fail=False)
            finally:
                _REQUEST.reset(token)

        first = asyncio.create_task(request(financial[0], "financial-a", workers.run_financial))
        await _started(financial[0])
        second = asyncio.create_task(request(financial[1], "financial-b", workers.run_financial))
        third = asyncio.create_task(request(local[0], "local-a", workers.run_heavy))
        await _started(local[0])
        fourth = asyncio.create_task(request(local[1], "local-b", workers.run_heavy))
        try:
            await anyio.sleep(.05)
            assert workers.heavy_worker_limiter().borrowed_tokens == 2
            assert workers.financial_worker_limiter().borrowed_tokens == 1
            assert not financial[1].entered.is_set()
            assert not local[1].entered.is_set()
            local[0].release.set()
            await _started(local[1])
            assert not financial[1].entered.is_set()
        finally:
            for work in [*financial, *local]:
                work.release.set()
        results = await asyncio.gather(first, second, third, fourth)
        assert results == [{"label": label, "request": label} for label in (
            "financial-a", "financial-b", "local-a", "local-b",
        )]
        assert activity.maximum == 2
        assert activity.financial_maximum == 1
        assert all(work.thread != loop_thread for work in [*financial, *local])
        assert _REQUEST.get() == ""
        await _idle()

    anyio.run(scenario)


@pytest.mark.parametrize("stage", ["financial_queue", "heavy_queue", "running"])
def test_raw_cancel_retains_queued_or_running_financial_work_and_admission(stage):
    async def scenario():
        activity = _Activity()
        financial = [_Work(activity, financial=True) for _ in range(3)]
        local = [_Work(activity) for _ in range(2)]
        tasks = []
        try:
            if stage == "financial_queue":
                tasks.append(asyncio.create_task(workers.run_financial(financial[0].run, "owner")))
                await _started(financial[0])
                target_work = financial[1]
            elif stage == "heavy_queue":
                tasks.extend(asyncio.create_task(workers.run_heavy(work.run, "local")) for work in local)
                for work in local:
                    await _started(work)
                target_work = financial[0]
            else:
                target_work = financial[0]

            target = asyncio.create_task(workers.run_financial(target_work.run, "cancelled"))
            tasks.append(target)
            if stage == "running":
                await _started(target_work)
            else:
                await anyio.sleep(.05)
                assert not target_work.entered.is_set()
            target.cancel()
            await anyio.sleep(.05)
            assert target.cancelled()
            assert workers.financial_worker_limiter().borrowed_tokens == 1
            if stage == "heavy_queue":
                assert workers.heavy_worker_limiter().borrowed_tokens == 2
                local[0].release.set()
                await _started(target_work)
            elif stage == "financial_queue":
                financial[0].release.set()
                await _started(target_work)

            follower_work = financial[2]
            follower = asyncio.create_task(workers.run_financial(follower_work.run, "follower"))
            tasks.append(follower)
            await anyio.sleep(.05)
            assert workers.financial_worker_limiter().borrowed_tokens == 1
            assert not follower_work.entered.is_set()
            target_work.release.set()
            await _started(follower_work)
            assert activity.maximum <= 2
            assert activity.financial_maximum == 1
        finally:
            for work in [*financial, *local]:
                work.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await _idle()

    anyio.run(scenario)


def test_scope_cancel_retains_financial_lane_until_work_finishes():
    async def scenario():
        activity = _Activity()
        first = _Work(activity, financial=True)
        second = _Work(activity, financial=True)
        scopes = []

        async def request():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await workers.run_financial(first.run, "scope-cancelled")

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(request)
            await _started(first)
            scopes[0].cancel()
            tasks.start_soon(workers.run_financial, second.run, "next")
            try:
                await anyio.sleep(.05)
                assert workers.financial_worker_limiter().borrowed_tokens == 1
                assert workers.heavy_worker_limiter().borrowed_tokens == 1
                assert not second.entered.is_set()
            finally:
                first.release.set()
                second.release.set()
        assert second.entered.is_set()
        assert activity.financial_maximum == 1
        await _idle()

    anyio.run(scenario)


@pytest.mark.parametrize("cancel_caller", [False, True])
def test_financial_failure_releases_admission_and_retrieves_background_error(cancel_caller):
    async def scenario():
        loop = asyncio.get_running_loop()
        errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))
        activity = _Activity()
        failed = _Work(activity, financial=True)
        following = _Work(activity, financial=True)
        task = asyncio.create_task(workers.run_financial(failed.run, "failure", fail=True))
        try:
            await _started(failed)
            if cancel_caller:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            failed.release.set()
            if not cancel_caller:
                with pytest.raises(RuntimeError, match="Synthetic financial failure"):
                    await task
            await _idle()
            following.release.set()
            assert await workers.run_financial(following.run, "next") == {"label": "next", "request": ""}
            await _idle()
            assert errors == []
        finally:
            failed.release.set()
            following.release.set()
            await asyncio.gather(task, return_exceptions=True)
            loop.set_exception_handler(previous_handler)

    anyio.run(scenario)
