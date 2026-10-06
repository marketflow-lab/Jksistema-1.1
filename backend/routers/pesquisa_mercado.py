"""Persistence endpoints; synchronous SQLite operations run in FastAPI workers."""

from fastapi import APIRouter, Depends, Query, Request
from typing import Literal

from backend.schemas.pesquisa_mercado import (
    PesquisaMercadoCheckpointRequest,
    PesquisaMercadoSellerRequest,
    PesquisaMercadoStartRequest,
    PesquisaMercadoStatus,
    PesquisaMercadoHistoryResponse,
    PesquisaMercadoHistoryJobResponse,
    PesquisaMercadoConsolidatedSummaryResponse,
    PesquisaMercadoConsolidatedRankingResponse,
    PesquisaMercadoConsolidatedSellersResponse,
    PesquisaMercadoConsolidatedCategoriesResponse,
    PesquisaMercadoOriginsResponse,
)
from backend.services import favoritos_endpoints
from backend.services.pesquisa_mercado import get_pesquisa_mercado_store


def _username(request: Request) -> str:
    return favoritos_endpoints._extrair_username_do_request(request)


def create_pesquisa_mercado_router() -> APIRouter:
    router = APIRouter(prefix="/api/pesquisa-mercado", tags=["pesquisa-mercado"])

    @router.post("/jobs")
    def start(req: PesquisaMercadoStartRequest, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        category = req.category.model_dump() if req.category is not None else None
        return {"job": get_pesquisa_mercado_store().start(client_id, _username(request), category=category)}

    @router.get("/jobs/latest")
    def latest(request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().latest(client_id, _username(request))}

    @router.get("/jobs", response_model=PesquisaMercadoHistoryResponse)
    def history(request: Request, offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=25, ge=1, le=100), q: str = Query(default="", max_length=200), status: PesquisaMercadoStatus | Literal[""] = "", scope_category: str = Query(default="", max_length=500), revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().history(client_id, _username(request), offset=offset, limit=limit, q=q, status=status, scope_category=scope_category, revision=revision)

    @router.get("/consolidado/resumo", response_model=PesquisaMercadoConsolidatedSummaryResponse)
    def consolidated_summary(request: Request, q: str = Query(default="", max_length=200), scope_category: str = Query(default="", max_length=500), seller_key: str = Query(default="", max_length=4500), evidence: Literal["", "rankable", "pending"] = "", revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().consolidated_summary(client_id, _username(request), q=q, scope_category=scope_category, seller_key=seller_key, evidence=evidence, revision=revision)

    @router.get("/consolidado/ranking", response_model=PesquisaMercadoConsolidatedRankingResponse)
    def consolidated_ranking(request: Request, offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=100, ge=1, le=100), q: str = Query(default="", max_length=200), scope_category: str = Query(default="", max_length=500), seller_key: str = Query(default="", max_length=4500), evidence: Literal["", "rankable", "pending"] = "", revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().consolidated_ranking(client_id, _username(request), offset=offset, limit=limit, q=q, scope_category=scope_category, seller_key=seller_key, evidence=evidence, revision=revision)

    @router.get("/consolidado/vendedores", response_model=PesquisaMercadoConsolidatedSellersResponse)
    def consolidated_sellers(request: Request, offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=100, ge=1, le=100), q: str = Query(default="", max_length=200), scope_category: str = Query(default="", max_length=500), seller_key: str = Query(default="", max_length=4500), evidence: Literal["", "rankable", "pending"] = "", revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().consolidated_sellers(client_id, _username(request), offset=offset, limit=limit, q=q, scope_category=scope_category, seller_key=seller_key, evidence=evidence, revision=revision)

    @router.get("/consolidado/categorias", response_model=PesquisaMercadoConsolidatedCategoriesResponse)
    def consolidated_categories(request: Request, offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=100, ge=1, le=100), revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().consolidated_categories(client_id, _username(request), offset=offset, limit=limit, revision=revision)

    @router.get("/consolidado/origens", response_model=PesquisaMercadoOriginsResponse)
    def consolidated_origins(request: Request, ad_key: str = Query(min_length=1, max_length=4500), seller_key: str = Query(min_length=1, max_length=4500), offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=25, ge=1, le=100), revision: int | None = Query(default=None, ge=0), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().consolidated_origins(client_id, _username(request), ad_key=ad_key, seller_key=seller_key, offset=offset, limit=limit, revision=revision)

    @router.get("/jobs/{job_id}")
    def get(job_id: str, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().get(job_id, client_id, _username(request))}

    @router.get("/jobs/{job_id}/resumo", response_model=PesquisaMercadoHistoryJobResponse)
    def job_summary(job_id: str, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().job_summary(job_id, client_id, _username(request))}

    @router.patch("/jobs/{job_id}/checkpoint")
    def checkpoint(job_id: str, req: PesquisaMercadoCheckpointRequest, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().checkpoint(job_id, client_id, _username(request), checkpoint=req.checkpoint, status=req.status, counters=req.counters, message=req.message)}

    @router.put("/jobs/{job_id}/vendedores/{seller_key:path}")
    def publish(job_id: str, seller_key: str, req: PesquisaMercadoSellerRequest, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().publish(job_id, seller_key, client_id, _username(request), req.seller, req.anuncios)}

    @router.get("/jobs/{job_id}/ranking")
    def ranking(job_id: str, request: Request, offset: int = Query(default=0, ge=0, le=2**53 - 1), limit: int = Query(default=100, ge=1, le=100), q: str = Query(default="", max_length=200), category: str = Query(default="", max_length=400), seller_key: str = Query(default="", max_length=500), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().ranking(job_id, client_id, _username(request), offset=offset, limit=limit, q=q, category=category, seller_key=seller_key)

    @router.get("/jobs/{job_id}/vendedores")
    def sellers(job_id: str, request: Request, seller_key: str = Query(default="", max_length=500), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().sellers(job_id, client_id, _username(request), seller_key=seller_key)

    @router.get("/jobs/{job_id}/categorias")
    def categories(job_id: str, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().categories(job_id, client_id, _username(request))

    @router.get("/jobs/{job_id}/descoberta")
    def discovery(job_id: str, request: Request, seed_key: str = Query(min_length=1, max_length=4000), client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return get_pesquisa_mercado_store().discovery(job_id, seed_key, client_id, _username(request))

    return router
