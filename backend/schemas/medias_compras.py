"""Pydantic schemas for medias compras."""

from typing import Any, Optional

from pydantic import BaseModel


class MediasComprasItem(BaseModel):
    sku: str = ""
    descricao: str = ""
    media_mensal: float = 0
    estoque_atual: float = 0
    custo_unitario: float = 0
    lead_time_dias: int = 15


class MediasComprasRequest(BaseModel):
    meses_cobertura: float = 2
    itens: list[MediasComprasItem]


class ListaCompraRequest(BaseModel):
    opcao: str
    crescimento_percent: float | None = None
    hidden_skus: list[str] | None = None
    nome_lista: str | None = None
    loja: str | None = None
    periodo_meses: int | None = None


class ListaPedidoUpdateRequest(BaseModel):
    nome_lista: str | None = None
    status: str | None = None
    itens: list[dict] = []


class ListaPedidoStatusRequest(BaseModel):
    status: str


class ListaPedidoAddSkuRequest(BaseModel):
    sku: str
    quantidade: float
    valor_unitario: float


class ListaPedidoPreferenciasColunasRequest(BaseModel):
    ordem_colunas: list[str] | None = None
    larguras_colunas: dict[str, int] | None = None


class MediasComprasSkusOcultosRequest(BaseModel):
    skus_ocultos: list[str] | None = None


__all__ = [
    "MediasComprasItem",
    "MediasComprasRequest",
    "ListaCompraRequest",
    "ListaPedidoUpdateRequest",
    "ListaPedidoStatusRequest",
    "ListaPedidoAddSkuRequest",
    "ListaPedidoPreferenciasColunasRequest",
    "MediasComprasSkusOcultosRequest",
]
