"""Mercado Livre service context configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MercadoLivreServiceConfig:
    logger: Any
    obter_cfg_ml: Callable
    ml_api_request: Callable
    ml_api_request_com_retry: Callable
    ml_parse_error_detail: Callable
    ml_buscar_itens_batch: Callable
    ml_montar_detalhe_anuncio_listagem: Callable
    ml_obter_preco_detalhado: Callable
    ml_obter_frete_detalhado: Callable
    ml_obter_taxas_anuncio: Callable
    ml_estimar_taxa_fixa_por_preco: Callable
    parse_float_flex: Callable
    formatar_moeda_br: Callable
    normalizar_texto: Callable


_mercado_livre_context: MercadoLivreServiceConfig | None = None


def configure_mercado_livre_context(config: MercadoLivreServiceConfig) -> None:
    global _mercado_livre_context
    _mercado_livre_context = config


def _ctx() -> MercadoLivreServiceConfig:
    if _mercado_livre_context is None:
        raise RuntimeError("Mercado Livre service context was not configured")
    return _mercado_livre_context
