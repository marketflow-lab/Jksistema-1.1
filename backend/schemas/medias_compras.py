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
    loja: str | None = None
    status: str | None = None
    itens: list[dict] | None = None
    supplier: str | None = None
    currency: str | None = None
    incoterm: str | None = None
    exchange_rate: float | None = None
    lead_time_days: int | None = None
    moq_default: float | None = None
    package_multiple_default: float | None = None
    order_date: str | None = None
    promised_ship_date: str | None = None
    actual_ship_date: str | None = None
    eta_date: str | None = None
    customs_clearance_date: str | None = None
    received_at: str | None = None
    promised_delivery_date: str | None = None


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
