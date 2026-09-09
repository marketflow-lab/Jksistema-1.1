"""Deadline-aware admission before executor submission, with interactive reserves."""
from __future__ import annotations

import contextvars
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from fastapi import HTTPException

from backend.services.perguntas_loading_transport import remaining, read_budget


def unavailable(status=503, code="queue_full"):
    return HTTPException(status, "Consulta ocupada ou prazo esgotado. Tente novamente.", headers={
        "X-JK-Error-Code": code, "X-JK-Error-Scope": "resource", "X-JK-Retryable": "true"})


_current_admission = contextvars.ContextVar("questions_admission", default=None)


def retain_current_admission():
    """Keep admission reserved while a timed-out complementary worker drains."""
    lease = _current_admission.get()
    return lease.retain() if lease else lambda: None


class _Lease:
    def __init__(self, scheduler, task):
        self.scheduler, self.task, self.references = scheduler, task, 1

    def retain(self):
        with self.scheduler._condition:
            self.references += 1
        return self.release

    def release(self):
        scheduler = self.scheduler
        with scheduler._condition:
            self.references -= 1
            if self.references == 0:
                scheduler._active -= 1
                scheduler._tenants[self.task.tenant] -= 1
                if self.task.secondary:
                    scheduler._secondary -= 1
                    scheduler._secondary_tenants[self.task.tenant] -= 1
                for counts in (scheduler._tenants, scheduler._secondary_tenants):
                    if not counts[self.task.tenant]:
                        counts.pop(self.task.tenant, None)
                scheduler._condition.notify_all()


@dataclass
class _Task:
    tenant: str
    secondary: bool
    priority: int
    submitted: float
    deadline: float
    context: contextvars.Context
    callback: object
    future: Future


class ReadScheduler:
    def __init__(self, workers=16, capacity=64):
        self.workers, self.capacity = workers, capacity
        self._condition = threading.Condition()
        self._pending = []
        self._active = self._secondary = 0
        self._tenants, self._secondary_tenants = Counter(), Counter()
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="questions-read")
        self._dispatcher = threading.Thread(target=self._dispatch, daemon=True, name="questions-admission")
        self._dispatcher.start()

    def submit(self, key, callback):
        now = time.monotonic()
        budget = remaining()
        kind = key[5]
        task = _Task(str(key[0]), kind not in {"detail", "items", "identity"},
                     {"detail": 0, "items": 1, "identity": 1, "list": 2}.get(kind, 3),
                     now, now + (15 if budget is None else budget),
                     contextvars.copy_context(), callback, Future())
        with self._condition:
            if self._closed or self._active + len(self._pending) >= self.capacity:
                raise unavailable()
            self._pending.append(task)
            self._condition.notify_all()
        return task.future

    def _eligible(self, task):
        return (self._active < self.workers and self._tenants[task.tenant] < 4
                and (not task.secondary or (self._secondary < min(8, self.workers)
                     and self._secondary_tenants[task.tenant] < 2)))

    def _dispatch(self):
        while True:
            expired, admitted = [], []
            with self._condition:
                now = time.monotonic()
                for task in list(self._pending):
                    if task.future.cancelled() or task.deadline <= now or self._closed:
                        self._pending.remove(task)
                        if not task.future.cancelled():
                            expired.append(task)
                ordered = sorted(self._pending, key=lambda task: (
                    max(0, task.priority - int((now - task.submitted) / 2)), task.submitted))
                for task in ordered:
                    if self._eligible(task):
                        self._pending.remove(task)
                        self._active += 1
                        self._tenants[task.tenant] += 1
                        if task.secondary:
                            self._secondary += 1
                            self._secondary_tenants[task.tenant] += 1
                        admitted.append((task, _Lease(self, task)))
                done = self._closed and not self._pending
                if not expired and not admitted and not done:
                    timeout = min((task.deadline - now for task in self._pending), default=None)
                    self._condition.wait(timeout=max(0.001, timeout) if timeout is not None else None)
                    continue
            # Future callbacks may take the cache lock; never invoke them under admission lock.
            for task in expired:
                if task.future.set_running_or_notify_cancel():
                    task.future.set_exception(unavailable(504, "read_timeout"))
            for task, lease in admitted:
                self._pool.submit(task.context.run, self._execute, task, lease)
            if done:
                return

    def _execute(self, task, lease):
        token = _current_admission.set(lease)
        try:
            if not task.future.set_running_or_notify_cancel():
                return
            if task.deadline <= time.monotonic():
                raise unavailable(504, "read_timeout")
            with read_budget(task.deadline - time.monotonic()):
                result = task.callback()
        except BaseException as error:
            task.future.set_exception(error)
        else:
            task.future.set_result(result)
        finally:
            _current_admission.reset(token)
            lease.release()

    def shutdown(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._dispatcher.join()
        self._pool.shutdown(wait=True)
