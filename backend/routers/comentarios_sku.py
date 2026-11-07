"""Comments shared by the Médias and Vendas SKU tables."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services.comentarios_sku import listar_comentarios, salvar_comentario
from backend.services.medias_compras_common import _resolver_escopo_loja_medias


class ComentarioSkuInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
    comentario: Annotated[str, StringConstraints(strict=True, max_length=2000)]


def create_comentarios_sku_router(get_tenant_id, get_tenant_path) -> APIRouter:
    router = APIRouter(prefix="/api/comentarios-sku", tags=["comentarios-sku"])

    @router.get("")
    def listar(
        response: Response,
        client_id: str = Depends(get_tenant_id),
        loja: str = Query(default="", max_length=200),
        store_id: str = Query(default="", max_length=200),
    ) -> dict[str, dict[str, str]]:
        escopo = _resolver_escopo_loja_medias(client_id, loja, store_id)
        response.headers["Cache-Control"] = "no-store"
        return {"comentarios": listar_comentarios(get_tenant_path(client_id), escopo["store_id"])}

    @router.put("")
    def salvar(
        dados: ComentarioSkuInput,
        response: Response,
        client_id: str = Depends(get_tenant_id),
        loja: str = Query(default="", max_length=200),
        store_id: str = Query(default="", max_length=200),
    ) -> dict[str, str]:
        escopo = _resolver_escopo_loja_medias(client_id, loja, store_id)
        sku = _normalizar_sku_mes(dados.sku).upper()
        comentario = dados.comentario.strip()
        salvar_comentario(get_tenant_path(client_id), escopo["store_id"], sku, comentario)
        response.headers["Cache-Control"] = "no-store"
        return {"sku": sku, "comentario": comentario}

    return router
