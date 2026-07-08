"""Runtime context for Shared Sync service modules."""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Header, HTTPException, Request

from backend.services.runtime_bridge import bind_runtime_globals

_runtime = None
_runtime_get_tenant_id = None


def configure_shared_sync_context(runtime_module=None):
    global _runtime, _runtime_get_tenant_id
    runtime = bind_runtime_globals(globals(), runtime_module)
    if runtime is not None:
        _runtime = runtime
        candidate = getattr(runtime, "get_tenant_id", None)
        if callable(candidate):
            _runtime_get_tenant_id = candidate
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_runtime_get_tenant_id):
        raise HTTPException(status_code=503, detail="Contexto de autenticacao ainda nao inicializado.")
    resultado = _runtime_get_tenant_id(request, authorization)
    if inspect.isawaitable(resultado):
        return await resultado
    return resultado


configure_shared_sync_context()

__all__ = ["configure_shared_sync_context", "get_tenant_id"]
