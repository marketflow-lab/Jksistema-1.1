"""Small HTTP adapters for the indexed Obsidian display projection."""
from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import training_read_service as service


class TrainingRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_id: str = Field(min_length=1, max_length=200)
    sku: str = Field(default="", max_length=200)


def ml_ia_treinamento_ficha_obter(store_id: str, sku: str, client_id: str = Depends(get_tenant_id)):
    return service.read_ficha(client_id, store_id, sku)


def ml_ia_treinamento_ficha_atualizar(req: TrainingRefreshRequest, client_id: str = Depends(get_tenant_id)):
    return JSONResponse(service.request_refresh(client_id, req.store_id, req.sku), status_code=202)
