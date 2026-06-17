"""Pydantic schemas for vendas."""

from typing import Any, Optional

from pydantic import BaseModel


class VendasQuery(BaseModel):
    data_inicio: str | None = None
    data_fim: str | None = None


class VendasSyncRequest(BaseModel):
    loja: str
    data_inicio: str
    data_fim: str
    forcar_resync: bool = False


__all__ = [
    "VendasQuery",
    "VendasSyncRequest",
]
