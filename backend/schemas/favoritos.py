"""Pydantic schemas for favoritos."""

from typing import Any, Optional

from pydantic import BaseModel


class FavoritosEfetivarPromocaoRequest(BaseModel):
    loja: str
    item_id: str
    sku: Optional[str] = ""
    preco_anuncio: float
    preco_promocional: Optional[float] = None
    preco_competitivo: Optional[float] = None
    percentual_promocao: Optional[float] = None
    campanha_id: str
    campanha_nome: Optional[str] = ""
    promotion_type: Optional[str] = "SELLER_CAMPAIGN"
    listing_type_id_alvo: Optional[str] = ""
    tipo_anuncio_alvo: Optional[str] = ""
    tipo_anuncio_atual: Optional[str] = ""
    simulacao: Optional[dict] = None
    anuncio: Optional[dict] = None


class FavoritosValidarEfetivacaoItemRequest(BaseModel):
    loja: str
    item_id: str
    listing_type_id_alvo: Optional[str] = ""
    tipo_anuncio_alvo: Optional[str] = ""


class FavoritosValidarEfetivacaoRequest(BaseModel):
    itens: list[FavoritosValidarEfetivacaoItemRequest]


class FavoritosSearchRequest(BaseModel):
    termo: str
    max_anuncios: int | None = 60


class FavoritosPrimeiraPaginaRequest(BaseModel):
    termo: str
    max_anuncios: int | None = 60
    usar_automatico: bool | None = False
    termo_original: str | None = None
    termos_busca: list[str] | None = None


class FavoritosEnriquecerDatasRequest(BaseModel):
    anuncios: list[dict] | None = None
    max_anuncios: int | None = 60


class FavoritosSkuDescricoesRequest(BaseModel):
    skus: list[str]
    loja: str | None = None
    item_ids_por_sku: dict[str, list[str]] | None = None


class FavoritosSkuPesquisaRequest(BaseModel):
    sku: str
    produto: str | None = ""
    loja: str | None = ""
    pesquisa_1: str | None = ""
    pesquisa_2: str | None = ""
    pesquisa_3: str | None = ""


class FavoritosSkuPesquisaIAItem(BaseModel):
    sku: str
    titulo: str | None = ""
    produto: str | None = ""
    descricao: str | None = ""
    pesquisa_1: str | None = ""
    pesquisa_2: str | None = ""
    pesquisa_3: str | None = ""


class FavoritosSkusPesquisaIARequest(BaseModel):
    itens: list[FavoritosSkuPesquisaIAItem]
    loja: str | None = None
    model: str | None = None
    sobrescrever: bool = False
    chunk_tamanho: int | None = 24


class FavoritosRankingIARequest(BaseModel):
    sku: str
    titulo: str | None = ""
    descricao: str | None = ""
    pesquisas: list[str] | None = None
    meus_anuncios: list[dict] | None = None
    anuncios: list[dict] | None = None
    max_anuncios: int | None = 160
    max_confirmados: int | None = None
    usar_imagem: bool | None = None


class FavoritosSkusOcultosRequest(BaseModel):
    skus_ocultos: list[str] | None = None


class FavoritosVendedoresIgnoradosRequest(BaseModel):
    vendedores_ignorados: list[str] | None = None


class FavoritosAnunciosIgnoradosRequest(BaseModel):
    anuncios_ignorados: dict[str, list[dict]] | None = None


class FavoritosHistoricoRequest(BaseModel):
    historico: list[dict] | None = None


class FavoritosHistoricoRealtimeSyncRequest(BaseModel):
    machine_id: str | None = ""
    reason: str | None = ""


class FavoritosPlanilhaLojaItem(BaseModel):
    loja: str
    url: str | None = ""


class FavoritosPlanilhasLojasRequest(BaseModel):
    planilhas: list[FavoritosPlanilhaLojaItem] | None = None


__all__ = [
    "FavoritosEfetivarPromocaoRequest",
    "FavoritosValidarEfetivacaoItemRequest",
    "FavoritosValidarEfetivacaoRequest",
    "FavoritosSearchRequest",
    "FavoritosPrimeiraPaginaRequest",
    "FavoritosEnriquecerDatasRequest",
    "FavoritosSkuDescricoesRequest",
    "FavoritosSkuPesquisaRequest",
    "FavoritosSkuPesquisaIAItem",
    "FavoritosSkusPesquisaIARequest",
    "FavoritosRankingIARequest",
    "FavoritosSkusOcultosRequest",
    "FavoritosVendedoresIgnoradosRequest",
    "FavoritosAnunciosIgnoradosRequest",
    "FavoritosHistoricoRequest",
    "FavoritosHistoricoRealtimeSyncRequest",
    "FavoritosPlanilhaLojaItem",
    "FavoritosPlanilhasLojasRequest",
]
