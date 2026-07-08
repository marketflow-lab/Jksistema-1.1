"""Runtime context for the Sala de Reuniao module."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Optional

from fastapi import Header, Request

from backend.services.runtime_bridge import current_backend_runtime

logger = logging.getLogger("jk_sistema")
_get_tenant_id_fn = None
_get_tenant_path_fn = None
_shared_sync_session_fn = None
_runtime = None


def configure_sala_reuniao_context(runtime_module=None):
    runtime = runtime_module or current_backend_runtime()
    global logger, _get_tenant_id_fn, _get_tenant_path_fn, _shared_sync_session_fn, _runtime
    _runtime = runtime
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            logger = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            _get_tenant_id_fn = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            _get_tenant_path_fn = getattr(runtime, "get_tenant_path")
        if hasattr(runtime, "_shared_sync_session"):
            _shared_sync_session_fn = getattr(runtime, "_shared_sync_session")
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_get_tenant_id_fn):
        raise RuntimeError("Sala de Reuniao runtime was not configured.")
    result = _get_tenant_id_fn(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def get_tenant_path(client_id: str):
    if not callable(_get_tenant_path_fn):
        raise RuntimeError("Sala de Reuniao runtime was not configured.")
    return _get_tenant_path_fn(client_id)


def shared_sync_session(authorization: Optional[str], client_id: str) -> dict[str, Any]:
    if callable(_shared_sync_session_fn):
        return _shared_sync_session_fn(authorization, client_id)
    return {"username": "local", "client_id": client_id, "is_admin": True, "permissions": {}}


def _shared_sync_session(authorization: Optional[str], client_id: str) -> dict[str, Any]:
    return shared_sync_session(authorization, client_id)


__all__ = [
    "configure_sala_reuniao_context",
    "get_tenant_id",
    "get_tenant_path",
    "shared_sync_session",
    "_shared_sync_session",
    "logger",
]
