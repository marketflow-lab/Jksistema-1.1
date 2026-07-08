"""Importacoes router definitions.

The Medias Compras pedido-list routes moved to ``routers.medias_compras``.
This factory remains as a compatibility hook for future Importacoes endpoints.
"""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter


router = APIRouter(tags=["importacoes"])


def create_importacoes_router(_legacy_module: ModuleType | None = None) -> APIRouter:
    return APIRouter(tags=["importacoes"])


__all__ = ["create_importacoes_router"]
