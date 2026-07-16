"""Compatibility facade for the isolated Vendas dependencies/state."""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import Header, Request

from backend.modules.vendas.dependencies import get_tenant_path, get_vendas_dependencies, logger
from backend.modules.vendas.state import (
    SYNC_ACTIVE, SYNC_ACTIVE_LOCK, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS,
    SYNC_MAX_ACTIVE_VENDAS, SYNC_META, SYNC_PROGRESS, SYNC_STATE_LOCK, SYNC_THREAD_CONTEXT,
)


def configure_vendas_context(runtime_module=None, *, get_tenant_path=None, sync_state_lock=None):
    return runtime_module


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    dependency = get_vendas_dependencies().get_tenant_id
    result = dependency(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


CONTEXT_EXPORTS = [
    "configure_vendas_context", "get_tenant_id", "get_tenant_path", "logger", "SYNC_CANCEL_FLAGS",
    "SYNC_PROGRESS", "SYNC_LOGS", "SYNC_META", "SYNC_DAY_CONTEXT", "SYNC_MAX_ACTIVE_VENDAS",
    "SYNC_ACTIVE_LOCK", "SYNC_STATE_LOCK", "SYNC_THREAD_CONTEXT", "SYNC_ACTIVE",
]

__all__ = CONTEXT_EXPORTS
