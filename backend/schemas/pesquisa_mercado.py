"""Authenticated persistence requests for the desktop market collector."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PesquisaMercadoStatus = Literal[
    "running", "paused", "waiting_login", "cancelled", "completed",
    "completed_partial", "failed",
]


class PesquisaMercadoCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=500, strict=True)
    nome: str = Field(min_length=1, max_length=400, strict=True)
    url: str = Field(min_length=1, max_length=4000, strict=True)


class PesquisaMercadoStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: PesquisaMercadoCategory | None = None


class PesquisaMercadoCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: dict[str, Any] | None = None
    status: PesquisaMercadoStatus | None = None
    counters: dict[str, int] | None = None
    message: str | None = Field(default=None, max_length=600)


class PesquisaMercadoSellerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller: dict[str, Any]
    anuncios: list[dict[str, Any]]


class PesquisaMercadoNameUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_key: str = Field(min_length=1, max_length=500, strict=True)
    nome: str = Field(min_length=1, max_length=200, strict=True)
    seller_id: str = Field(max_length=80, strict=True)
    seller_perfil_url: str = Field(max_length=4000, strict=True)
    seller_name_kind: Literal["seller", "store_brand"]
    source: Literal["perfil", "avant"]


class PesquisaMercadoNamesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[PesquisaMercadoNameUpdate] = Field(min_length=1, max_length=100)


class PesquisaMercadoClosedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PesquisaMercadoDeleteResponse(PesquisaMercadoClosedResponse):
    deleted: Literal[True]
    job_id: str
    revision: int


class PesquisaMercadoCounters(PesquisaMercadoClosedResponse):
    categories: int
    pages: int
    sellers: int
    ads: int
    pending: int


class PesquisaMercadoHistoryItem(PesquisaMercadoClosedResponse):
    id: str
    status: PesquisaMercadoStatus
    revision: int
    scope_category: PesquisaMercadoCategory | None
    counters: PesquisaMercadoCounters
    created_at: str
    updated_at: str


class PesquisaMercadoHistoryResponse(PesquisaMercadoClosedResponse):
    items: list[PesquisaMercadoHistoryItem]
    total: int
    owner_revision: int


class PesquisaMercadoHistoryJobResponse(PesquisaMercadoClosedResponse):
    job: PesquisaMercadoHistoryItem


class PesquisaMercadoConsolidatedAd(PesquisaMercadoClosedResponse):
    id: str
    url: str
    titulo: str
    imagem: str
    preco: float | None
    vendedor: str
    seller_key: str
    categoria: str
    vendas: float | None
    vendasFonte: str
    data_criacao: str
    media_mensal_fonte: str
    media_vendas_mensal: float | None
    rankable: bool
    status: Literal["ranked", "pending"]
    erro: str
    posicao: int
    seller_id: str
    seller_perfil_url: str
    catalogo: bool
    avant_seller_id: str
    avant_seller_perfil_url: str
    ordem_coleta: int
    posicao_original: int
    rank: int | None
    ad_key: str
    source_job_id: str
    observed_at: str
    date_approximate: bool
    observations: int
    research_count: int
    scope_category: PesquisaMercadoCategory | None


class PesquisaMercadoConsolidatedRankingResponse(PesquisaMercadoClosedResponse):
    items: list[PesquisaMercadoConsolidatedAd]
    total: int
    owner_revision: int


class PesquisaMercadoConsolidatedSummaryResponse(PesquisaMercadoClosedResponse):
    researches: int
    observations: int
    unique_ads: int
    sellers: int
    rankable: int
    pending: int
    first_research_at: str | None
    last_research_at: str | None
    last_observation_at: str | None
    owner_revision: int


class PesquisaMercadoConsolidatedSeller(PesquisaMercadoClosedResponse):
    seller_key: str
    vendedor: str
    ads: int
    pending: int


class PesquisaMercadoConsolidatedSellersResponse(PesquisaMercadoClosedResponse):
    items: list[PesquisaMercadoConsolidatedSeller]
    total: int
    owner_revision: int


class PesquisaMercadoConsolidatedCategory(PesquisaMercadoCategory):
    researches: int


class PesquisaMercadoConsolidatedCategoriesResponse(PesquisaMercadoClosedResponse):
    items: list[PesquisaMercadoConsolidatedCategory]
    total: int
    owner_revision: int


class PesquisaMercadoOrigin(PesquisaMercadoClosedResponse):
    source_job_id: str
    status: PesquisaMercadoStatus
    scope_category: PesquisaMercadoCategory | None
    observed_at: str
    date_approximate: bool
    rankable: bool
    media_vendas_mensal: float | None
    vendas: float | None


class PesquisaMercadoOriginsResponse(PesquisaMercadoClosedResponse):
    items: list[PesquisaMercadoOrigin]
    total: int
    owner_revision: int
