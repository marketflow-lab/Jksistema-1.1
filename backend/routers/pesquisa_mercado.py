"""Persistence endpoints; synchronous SQLite operations run in FastAPI workers."""

from fastapi import APIRouter, Depends, Query, Request

from backend.schemas.pesquisa_mercado import (
    PesquisaMercadoCheckpointRequest,
    PesquisaMercadoSellerRequest,
    PesquisaMercadoStartRequest,
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

    @router.get("/jobs/{job_id}")
    def get(job_id: str, request: Request, client_id: str = Depends(favoritos_endpoints.get_tenant_id)):
        return {"job": get_pesquisa_mercado_store().get(job_id, client_id, _username(request))}

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
