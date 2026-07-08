"""Mercado Livre service facade.

The implementation is split by responsibility in sibling modules. This facade
keeps the legacy import surface stable for backend_api and routers.
"""

from __future__ import annotations

from backend.services.mercadolivre_anuncios import (
    buscar_anuncio_mercado_livre,
    historico_visitas_anuncio_mercado_livre,
    listar_anuncios_mercado_livre,
)
from backend.services.mercadolivre_cache import (
    _cache_invalidar_loja,
    _ml_cache_get,
    _ml_cache_get_stale,
    _ml_cache_set,
    invalidar_cache_mercado_livre,
)
from backend.services.mercadolivre_context import (
    MercadoLivreServiceConfig,
    _ctx,
    configure_mercado_livre_context,
)
from backend.services.mercadolivre_http import (
    _ml_http_invalidar_session,
    _ml_http_request,
)
from backend.services.mercadolivre_promocoes import (
    _ml_contar_itens_promocao_status,
    _ml_extrair_contagem_campanha,
    _ml_listar_promocoes_ativas_payload,
    listar_promocoes_ativas_mercado_livre,
    listar_promocoes_contagens_mercado_livre,
)

__all__ = [
    "MercadoLivreServiceConfig",
    "configure_mercado_livre_context",
    "_ctx",
    "_ml_http_invalidar_session",
    "_ml_http_request",
    "_ml_cache_get",
    "_ml_cache_get_stale",
    "_ml_cache_set",
    "_cache_invalidar_loja",
    "invalidar_cache_mercado_livre",
    "listar_anuncios_mercado_livre",
    "historico_visitas_anuncio_mercado_livre",
    "buscar_anuncio_mercado_livre",
    "_ml_listar_promocoes_ativas_payload",
    "_ml_extrair_contagem_campanha",
    "_ml_contar_itens_promocao_status",
    "listar_promocoes_ativas_mercado_livre",
    "listar_promocoes_contagens_mercado_livre",
]
