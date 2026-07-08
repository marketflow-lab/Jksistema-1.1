"""Runtime context for Impostos service modules."""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Header, HTTPException, Request

from backend.services.runtime_bridge import bind_runtime_globals

_runtime = None
_runtime_get_tenant_id = None
_runtime_get_tenant_path = None


def configure_impostos_context(runtime_module=None):
    global _runtime, _runtime_get_tenant_id, _runtime_get_tenant_path
    runtime = bind_runtime_globals(globals(), runtime_module)
    if runtime is not None:
        _runtime = runtime
        candidate = getattr(runtime, "get_tenant_id", None)
        if callable(candidate):
            _runtime_get_tenant_id = candidate
        path_candidate = getattr(runtime, "get_tenant_path", None)
        if callable(path_candidate):
            _runtime_get_tenant_path = path_candidate
    return runtime


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_runtime_get_tenant_id):
        raise HTTPException(status_code=503, detail="Contexto de autenticacao ainda nao inicializado.")
    resultado = _runtime_get_tenant_id(request, authorization)
    if inspect.isawaitable(resultado):
        return await resultado
    return resultado


def get_tenant_path(client_id: str):
    if not callable(_runtime_get_tenant_path):
        raise RuntimeError("Contexto de tenant ainda nao inicializado.")
    return _runtime_get_tenant_path(client_id)


configure_impostos_context()

__all__ = ["configure_impostos_context", "get_tenant_id", "get_tenant_path"]
