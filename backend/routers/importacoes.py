"""Importacoes router definitions."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, Depends, HTTPException

from backend.services.importacoes_tracking import (
    ErroRastreamentoNavio,
    rastrear_navio_datalastic,
)


router = APIRouter(tags=["importacoes"])


def create_importacoes_router(_legacy_module: ModuleType | None = None) -> APIRouter:
    importacoes_router = APIRouter(tags=["importacoes"])

    def api_importacoes_rastrear_navio(imo: str = "", mmsi: str = ""):
        try:
            return rastrear_navio_datalastic(imo=imo, mmsi=mmsi)
        except ErroRastreamentoNavio as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"codigo": exc.codigo, "mensagem": exc.mensagem},
            ) from exc

    dependency = getattr(_legacy_module, "get_tenant_id", None) if _legacy_module else None
    dependencies = [Depends(dependency)] if callable(dependency) else []
    importacoes_router.add_api_route(
        "/api/importacoes/rastreamento/navio",
        api_importacoes_rastrear_navio,
        methods=["GET"],
        name="api_importacoes_rastrear_navio",
        dependencies=dependencies,
    )
    return importacoes_router


__all__ = ["create_importacoes_router"]
