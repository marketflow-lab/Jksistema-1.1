"""Compatibility facade for the isolated Vendas router."""

from backend.modules.vendas.module import get_default_vendas_module


def create_vendas_router(module=None):
    return (module or get_default_vendas_module()).router


__all__ = ["create_vendas_router"]
