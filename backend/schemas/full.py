"""Pydantic schemas for full."""

from typing import Any, Optional

from pydantic import BaseModel


class FullEnvioTransitoItem(BaseModel):
    sku: str = ""
    produto: str = ""
    variacao: str = ""
    quantidade: float = 0
    mlb: str = ""


class FullEnvioTransitoPayload(BaseModel):
    codigo_envio: str = ""
    loja: str = ""
    status: str = "aguardando_inicio"
    situacao_ml: str = ""
    data_envio: str = ""
    data_recebimento: str = ""
    total_unidades: float | None = None
    observacoes: str = ""
    ativo: bool = True
    itens: list[FullEnvioTransitoItem] = []


class FullEnvioTransitoUpdate(BaseModel):
    codigo_envio: str | None = None
    loja: str | None = None
    status: str | None = None
    situacao_ml: str | None = None
    data_envio: str | None = None
    data_recebimento: str | None = None
    total_unidades: float | None = None
    observacoes: str | None = None
    ativo: bool | None = None
    itens: list[FullEnvioTransitoItem] | None = None


__all__ = [
    "FullEnvioTransitoItem",
    "FullEnvioTransitoPayload",
    "FullEnvioTransitoUpdate",
]
