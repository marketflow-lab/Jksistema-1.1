"""Tenant authentication dependency for Perguntas/Pós-venda endpoints."""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Header, Request

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_dependency


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    result = runtime_dependency("get_tenant_id")(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = ["get_tenant_id"]
