"""Runtime context for the Estoque module."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Optional

from fastapi import Header, Request

from backend.services.runtime_bridge import bind_runtime_globals, current_backend_runtime

logger = logging.getLogger("jk_sistema")
_get_tenant_id_fn = None
_get_tenant_path_fn = None
_runtime = None
ESTOQUE_SYNC_ACTIVE: dict[str, Any] = {}
ESTOQUE_SYNC_PROGRESS: dict[str, Any] = {}
ESTOQUE_SYNC_LOGS: dict[str, list] = {}
ESTOQUE_SYNC_META: dict[str, Any] = {}
ESTOQUE_SYNC_CANCEL_FLAGS: dict[str, Any] = {}
ESTOQUE_LANC_SYNC_ACTIVE: dict[str, Any] = {}
ESTOQUE_LANC_SYNC_PROGRESS: dict[str, Any] = {}
ESTOQUE_LANC_SYNC_LOGS: dict[str, list] = {}
ESTOQUE_LANC_SYNC_META: dict[str, Any] = {}


def configure_estoque_context(runtime_module=None, *, get_tenant_path=None):
    runtime = runtime_module or current_backend_runtime()
    global logger, _get_tenant_id_fn, _get_tenant_path_fn, _runtime
    global ESTOQUE_SYNC_ACTIVE, ESTOQUE_SYNC_PROGRESS, ESTOQUE_SYNC_LOGS, ESTOQUE_SYNC_META, ESTOQUE_SYNC_CANCEL_FLAGS
    global ESTOQUE_LANC_SYNC_ACTIVE, ESTOQUE_LANC_SYNC_PROGRESS, ESTOQUE_LANC_SYNC_LOGS, ESTOQUE_LANC_SYNC_META
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
            "ESTOQUE_SYNC_ACTIVE", "ESTOQUE_SYNC_PROGRESS", "ESTOQUE_SYNC_LOGS", "ESTOQUE_SYNC_META",
            "ESTOQUE_SYNC_CANCEL_FLAGS", "ESTOQUE_LANC_SYNC_ACTIVE", "ESTOQUE_LANC_SYNC_PROGRESS",
            "ESTOQUE_LANC_SYNC_LOGS", "ESTOQUE_LANC_SYNC_META",
        ):
            if hasattr(runtime, name):
                globals()[name] = getattr(runtime, name)
    if get_tenant_path is not None:
        _get_tenant_path_fn = get_tenant_path
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_get_tenant_id_fn):
        raise RuntimeError("Estoque runtime was not configured.")
    result = _get_tenant_id_fn(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def get_tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path_fn):
        raise RuntimeError("Estoque runtime was not configured.")
    return _get_tenant_path_fn(client_id)


CONTEXT_EXPORTS = [
    "configure_estoque_context",
    "get_tenant_id",
    "get_tenant_path",
    "logger",
    "ESTOQUE_SYNC_ACTIVE",
    "ESTOQUE_SYNC_PROGRESS",
    "ESTOQUE_SYNC_LOGS",
    "ESTOQUE_SYNC_META",
    "ESTOQUE_SYNC_CANCEL_FLAGS",
    "ESTOQUE_LANC_SYNC_ACTIVE",
    "ESTOQUE_LANC_SYNC_PROGRESS",
    "ESTOQUE_LANC_SYNC_LOGS",
    "ESTOQUE_LANC_SYNC_META",
]

__all__ = CONTEXT_EXPORTS
