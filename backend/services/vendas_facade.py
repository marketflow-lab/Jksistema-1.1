"""Compatibility helpers for legacy Vendas import paths."""

from __future__ import annotations

from fastapi import HTTPException

from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.module import get_default_vendas_module


def facade_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except VendasDomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=exc.headers) from exc


def service():
    return get_default_vendas_module().service


def sync_service():
    return get_default_vendas_module().sync_service


__all__ = ["facade_call", "service", "sync_service"]
