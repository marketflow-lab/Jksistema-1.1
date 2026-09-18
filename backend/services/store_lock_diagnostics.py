"""Bounded, best-effort lock telemetry; producers never perform log I/O."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import queue
import threading
import time
import uuid

_OPERATION = ContextVar("store_operation", default=None)
_LABELS = frozenset({"stores_access", "stores_maintenance", "stores_write", "oauth_refresh",
                     "sync_bundle", "sync_apply", "context_catalog", "automation"})
_QUEUE = queue.Queue(maxsize=256)
_START = threading.Lock()
_WORKER = None
_LOGGER = logging.getLogger(__name__)
SLOW_SECONDS = 1.0
RATE_SECONDS = 30.0


@contextmanager
def operation_scope(label):
    token = _OPERATION.set((label if label in _LABELS else "stores_access", uuid.uuid4().hex))
    try:
        yield
    finally:
        _OPERATION.reset(token)


def _emit(record, phase, duration, result, limiter):
    now = time.monotonic()
    key = (record["resource"], record["operation"], phase, result)
    previous, suppressed = limiter.get(key, (float("-inf"), 0))
    if now - previous < RATE_SECONDS:
        limiter[key] = (previous, suppressed + 1)
        return
    if len(limiter) >= 512:
        limiter.clear()
    limiter[key] = (now, 0)
    try:
        _LOGGER.warning(
            "store_lock operation=%s operation_id=%s attempt_id=%s resource=%s namespace=%s "
            "pid=%s thread=%s phase=%s duration_ms=%s result=%s suppressed=%s",
            record["operation"], record["operation_id"], record["attempt_id"], record["resource"],
            record["namespace"], record["pid"], record["thread"], phase,
            max(0, round(duration * 1000)), result, suppressed,
        )
    except Exception:
        pass


def _consume(message, active, limiter):
    event, record, instant, outcome = message
    key = record["attempt_id"]
    if event == "begin":
        if len(active) < 256:
            active[key] = (record, instant, "waiting", False)
    elif event == "acquired":
        waiting = instant - record["started"]
        if waiting >= SLOW_SECONDS:
            _emit(record, "waiting", waiting, "acquired", limiter)
        if len(active) < 256 or key in active:
            active[key] = (record, instant, "holding", False)
    else:
        active.pop(key, None)
        acquired_at = record.get("acquired_at")
        duration = instant - (acquired_at if acquired_at is not None else record["started"])
        phase = "holding" if acquired_at is not None else "waiting"
        if duration >= SLOW_SECONDS or outcome != "ok":
            _emit(record, phase, duration, outcome, limiter)


def _observe_active(active, limiter):
    now = time.monotonic()
    for key, (record, started, phase, reported) in list(active.items()):
        if not reported and now - started >= SLOW_SECONDS:
            _emit(record, phase, now - started, "pending", limiter)
            active[key] = (record, started, phase, True)
        # A dropped completion event must never grow the registry indefinitely.
        if now - started > 3600:
            active.pop(key, None)


def _run():
    active, limiter = {}, {}
    while True:
        try:
            _consume(_QUEUE.get(timeout=.25), active, limiter)
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            _observe_active(active, limiter)
        except Exception:
            pass


def _enqueue(message):
    global _WORKER
    try:
        if _WORKER is None or not _WORKER.is_alive():
            if not _START.acquire(blocking=False):
                return
            try:
                if _WORKER is None or not _WORKER.is_alive():
                    _WORKER = threading.Thread(target=_run, name="store-lock-diagnostics", daemon=True)
                    _WORKER.start()
            finally:
                _START.release()
        _QUEUE.put_nowait(message)
    except Exception:
        pass


def begin(namespace, resource):
    try:
        if namespace not in {"stores", "legacy", "path", "sqlite", "oauth"}:
            return None
        if len(resource) != 64 or any(ch not in "0123456789abcdef" for ch in resource):
            return None
        operation, operation_id = _OPERATION.get() or ("stores_access", uuid.uuid4().hex)
        record = dict(operation=operation, operation_id=operation_id, attempt_id=uuid.uuid4().hex,
                      resource=resource, namespace=namespace, pid=os.getpid(),
                      thread=threading.get_ident(), started=time.monotonic())
        _enqueue(("begin", dict(record), record["started"], ""))
        return record
    except Exception:
        return None


def acquired(record):
    try:
        if record is not None:
            record["acquired_at"] = time.monotonic()
            _enqueue(("acquired", dict(record), record["acquired_at"], ""))
    except Exception:
        pass


def finished(record, result="ok"):
    try:
        if record is not None:
            result = result if result in {"ok", "timeout", "error"} else "error"
            _enqueue(("finished", dict(record), time.monotonic(), result))
    except Exception:
        pass
