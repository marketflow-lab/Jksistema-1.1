"""Pydantic schemas for estoque."""

from typing import Any, Optional

from pydantic import BaseModel


class EstoqueSyncRequest(BaseModel):
    loja: str
    store_id: Optional[str] = None
    todas_lojas: bool = False


class EstoqueLancamentosSyncRequest(BaseModel):
    loja: str
    store_id: Optional[str] = None
    sku: str
    data_inicio: str | None = None
    data_fim: str | None = None


class EstoqueLancamentosSyncLoteRequest(BaseModel):
    loja: str
    store_id: Optional[str] = None
    data_inicio: str | None = None
    data_fim: str | None = None


class EstoquePreferenciasColunasRequest(BaseModel):
    ordem_colunas: list[str] | None = None
    larguras_colunas: dict[str, int] | None = None


__all__ = [
    "EstoqueSyncRequest",
    "EstoqueLancamentosSyncRequest",
    "EstoqueLancamentosSyncLoteRequest",
    "EstoquePreferenciasColunasRequest",
]
