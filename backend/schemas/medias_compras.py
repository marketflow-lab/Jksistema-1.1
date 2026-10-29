"""Pydantic schemas for medias compras."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    store_id: str | None = None
    fornecedor_id: str | None = None
    periodo_meses: int | None = None
    quantidades_sugeridas: dict[str, int] | None = None


class ListaPedidoUpdateRequest(BaseModel):
    nome_lista: str | None = None
    loja: str | None = None
    store_id: str | None = None
    status: str | None = None
    itens: list[dict] | None = None
    numero_invoice: str | None = None
    fornecedor_id: str | None = None
    supplier: str | None = None
    nacionalizador_id: str | None = None
    nacionalizador: str | None = None
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


class ListasPedidosUniaoPreviaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lista_ids: list[str] = Field(min_length=2, max_length=100)

    @field_validator("lista_ids")
    @classmethod
    def validar_ids(cls, values):
        ids = [value.strip() for value in values]
        if any(not value or len(value) > 128 for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Selecione pelo menos duas listas distintas com identificadores válidos.")
        return sorted(ids)


class ListasPedidosUniaoRequest(ListasPedidosUniaoPreviaRequest):
    nome_lista: str = Field(min_length=1, max_length=160)
    previa_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    operacao_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    escolhas: dict[str, str] = Field(default_factory=dict, max_length=20000)

    @field_validator("nome_lista")
    @classmethod
    def validar_nome(cls, value):
        if not value.strip():
            raise ValueError("Informe o nome da lista unificada.")
        return value.strip()


class ListaPedidoAddSkuRequest(BaseModel):
    sku: str
    quantidade: float
    valor_unitario: float


class ListaPedidoSkuAprovacaoRequest(BaseModel):
    aprovada: bool


class ListaPedidoSkuAnaliseConcorrentesRequest(BaseModel):
    custo_unitario: float | None = None
    precos_concorrentes: dict[str, float] = Field(default_factory=dict)
    anuncios_loja: dict[str, str] = Field(default_factory=dict)


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
    "ListasPedidosUniaoPreviaRequest",
    "ListasPedidosUniaoRequest",
    "ListaPedidoAddSkuRequest",
    "ListaPedidoSkuAprovacaoRequest",
    "ListaPedidoSkuAnaliseConcorrentesRequest",
    "ListaPedidoPreferenciasColunasRequest",
    "MediasComprasSkusOcultosRequest",
]
