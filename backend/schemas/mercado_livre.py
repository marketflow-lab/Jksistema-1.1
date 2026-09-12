"""Pydantic schemas for mercado livre."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PromoRequest(BaseModel):
    decisoes: list
    filename: str


class StoreRequest(BaseModel):
    nome: str


class StoreRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1, max_length=100)

    @field_validator("nome", mode="before")
    @classmethod
    def validar_nome(cls, value):
        if not isinstance(value, str):
            raise ValueError("nome deve ser texto")
        return value.strip()


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
    "StoreRenameRequest",
    "MLPrecoRequest",
    "MLEstoqueRequest",
    "MLStatusRequest",
    "MLPromocaoRequest",
    "MLPromocoesItensRequest",
]
