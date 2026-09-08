"""Opt-in shared deadline for the question screen's read operations.

The context follows OAuth refreshes and central-account requests. Legacy callers
retain their existing timeout values when no read budget is active. Endpoint
workers must also bound their wait: socket inactivity timeouts alone cannot
guarantee a wall-clock deadline against a peer that continuously trickles bytes.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from urllib3.util import Timeout


_deadline: ContextVar[float | None] = ContextVar("perguntas_read_deadline", default=None)


@contextmanager
def read_budget(seconds: float = 15):
    deadline = time.monotonic() + max(0.0, float(seconds))
    parent = _deadline.get()
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        check_budget()
        yield
        check_budget()
    finally:
        _deadline.reset(token)


def remaining() -> float | None:
    deadline = _deadline.get()
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def check_budget() -> None:
    available = remaining()
    if available is not None and available <= 0:
        raise requests.Timeout("Tempo de consulta das perguntas esgotado.")


def request_timeout(original):
    available = remaining()
    if available is None:
        return original
    check_budget()
    if isinstance(original, tuple):
        connect, read = original
    else:
        connect = read = original
    return Timeout(
        total=available,
        connect=min(float(connect), available) if connect is not None else available,
        read=min(float(read), available) if read is not None else available,
    )
