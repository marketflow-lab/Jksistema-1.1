"""Mercado Livre API routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.services.mercadolivre import (
    ML_EXPORT_XLSX_MEDIA_TYPE,
    buscar_anuncio_mercado_livre,
    exportar_anuncios_ativos_mercado_livre,
    historico_visitas_anuncio_mercado_livre,
    invalidar_cache_mercado_livre,
    listar_anuncios_mercado_livre,
    listar_promocoes_ativas_mercado_livre,
    listar_promocoes_contagens_mercado_livre,
)


@dataclass(frozen=True)
class MercadoLivreRouterConfig:
    get_tenant_id: Callable


def create_mercado_livre_router(config: MercadoLivreRouterConfig) -> APIRouter:
    router = APIRouter(tags=["mercado-livre"])

    @router.post("/api/mercadolivre/cache/invalidar", name="ml_invalidar_cache")
    def ml_invalidar_cache(loja: str, client_id: str = Depends(config.get_tenant_id)):
        return invalidar_cache_mercado_livre(client_id, loja)

    @router.get("/api/mercadolivre/anuncios", name="ml_listar_anuncios")
    def ml_listar_anuncios(
        loja: str,
        offset: int = 0,
        limit: int = 50,
        sku: Optional[str] = None,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return listar_anuncios_mercado_livre(
            client_id=client_id,
            loja=loja,
            offset=offset,
            limit=limit,
            sku=sku,
        )

    @router.get("/api/mercadolivre/anuncios/exportar", name="ml_exportar_anuncios_ativos")
    def ml_exportar_anuncios_ativos(
        loja: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        output, filename = exportar_anuncios_ativos_mercado_livre(client_id=client_id, loja=loja)
        return StreamingResponse(
            output,
            media_type=ML_EXPORT_XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/api/mercadolivre/anuncios/{item_id}/visitas", name="ml_historico_visitas_anuncio")
    def ml_historico_visitas_anuncio(
        item_id: str,
        loja: str,
        dias: int = 150,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return historico_visitas_anuncio_mercado_livre(
            client_id=client_id,
            item_id=item_id,
            loja=loja,
            dias=dias,
        )

    @router.get("/api/mercadolivre/anuncios/{item_id}", name="ml_buscar_anuncio")
    def ml_buscar_anuncio(item_id: str, loja: str, client_id: str = Depends(config.get_tenant_id)):
        return buscar_anuncio_mercado_livre(client_id=client_id, item_id=item_id, loja=loja)

    @router.get("/api/mercadolivre/promocoes", name="mercadolivre_listar_promocoes_ativas")
    def mercadolivre_listar_promocoes_ativas(
        loja: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return listar_promocoes_ativas_mercado_livre(client_id, loja)

    @router.get("/api/mercadolivre/promocoes/contagens", name="mercadolivre_listar_promocoes_contagens")
    def mercadolivre_listar_promocoes_contagens(
        loja: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return listar_promocoes_contagens_mercado_livre(client_id, loja)

    return router
