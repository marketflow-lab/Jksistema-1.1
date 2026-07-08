"""Runtime context for the Vendas module."""

from __future__ import annotations

import inspect
import logging
import threading
from typing import Any, Optional

from fastapi import Header, Request

from backend.services.runtime_bridge import bind_runtime_globals, current_backend_runtime

logger = logging.getLogger("jk_sistema")
_get_tenant_id_fn = None
_get_tenant_path_fn = None
_runtime = None
SYNC_CANCEL_FLAGS: dict[str, Any] = {}
SYNC_PROGRESS: dict[str, Any] = {}
SYNC_LOGS: dict[str, list] = {}
SYNC_META: dict[str, Any] = {}
SYNC_DAY_CONTEXT: dict[str, Any] = {}
SYNC_MAX_ACTIVE_VENDAS = 2
SYNC_ACTIVE_LOCK = threading.RLock()
SYNC_STATE_LOCK = threading.RLock()
SYNC_THREAD_CONTEXT = threading.local()
SYNC_ACTIVE: dict[str, Any] = {}


def configure_vendas_context(runtime_module=None, *, get_tenant_path=None, sync_state_lock=None):
    runtime = runtime_module or current_backend_runtime()
    global logger, _get_tenant_id_fn, _get_tenant_path_fn, _runtime
    global SYNC_CANCEL_FLAGS, SYNC_PROGRESS, SYNC_LOGS, SYNC_META, SYNC_DAY_CONTEXT
    global SYNC_MAX_ACTIVE_VENDAS, SYNC_ACTIVE_LOCK, SYNC_STATE_LOCK, SYNC_THREAD_CONTEXT, SYNC_ACTIVE
    _runtime = runtime
    bind_runtime_globals(globals(), runtime)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            logger = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            _get_tenant_id_fn = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            _get_tenant_path_fn = getattr(runtime, "get_tenant_path")
        for name in (
            "SYNC_CANCEL_FLAGS", "SYNC_PROGRESS", "SYNC_LOGS", "SYNC_META", "SYNC_DAY_CONTEXT",
            "SYNC_MAX_ACTIVE_VENDAS", "SYNC_ACTIVE_LOCK", "SYNC_STATE_LOCK", "SYNC_THREAD_CONTEXT", "SYNC_ACTIVE",
        ):
            if hasattr(runtime, name):
                globals()[name] = getattr(runtime, name)
    if get_tenant_path is not None:
        _get_tenant_path_fn = get_tenant_path
    if sync_state_lock is not None:
        SYNC_STATE_LOCK = sync_state_lock
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_get_tenant_id_fn):
        raise RuntimeError("Vendas runtime was not configured.")
    result = _get_tenant_id_fn(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def get_tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path_fn):
        raise RuntimeError("Vendas runtime was not configured.")
    return _get_tenant_path_fn(client_id)


CONTEXT_EXPORTS = [
    "configure_vendas_context",
    "get_tenant_id",
    "get_tenant_path",
    "logger",
    "SYNC_CANCEL_FLAGS",
    "SYNC_PROGRESS",
    "SYNC_LOGS",
    "SYNC_META",
    "SYNC_DAY_CONTEXT",
    "SYNC_MAX_ACTIVE_VENDAS",
    "SYNC_ACTIVE_LOCK",
    "SYNC_STATE_LOCK",
    "SYNC_THREAD_CONTEXT",
    "SYNC_ACTIVE",
]

__all__ = CONTEXT_EXPORTS
