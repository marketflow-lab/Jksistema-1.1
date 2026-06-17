"""Pydantic schemas for mercado livre."""

from typing import Any, Optional

from pydantic import BaseModel


class PromoRequest(BaseModel):
    decisoes: list
    filename: str


class StoreRequest(BaseModel):
    nome: str


class MLPrecoRequest(BaseModel):
    loja: str
    item_id: str
    novo_preco: float


class MLEstoqueRequest(BaseModel):
    loja: str
    item_id: str
    quantidade: int


class MLStatusRequest(BaseModel):
    loja: str
    item_id: str
    status: str


class MLPromocaoRequest(BaseModel):
    loja: str
    item_id: str
    tipo_desconto: str  # "percentual" ou "preco_final"
    valor: float  # Se percentual: 10 = 10%, se preco_final: valor em reais
    deal_id: str = None  # ID da campanha ML (opcional)


class MLPromocoesItensRequest(BaseModel):
    loja: str
    item_ids: list[str]


__all__ = [
    "PromoRequest",
    "StoreRequest",
    "MLPrecoRequest",
    "MLEstoqueRequest",
    "MLStatusRequest",
    "MLPromocaoRequest",
    "MLPromocoesItensRequest",
]
