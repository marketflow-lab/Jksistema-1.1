"""Local authenticated controls for manual central-account updates."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.services.central_accounts_client import current


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    access: Literal["read", "write", "revoke"]


def create_central_accounts_router(get_tenant_id):
    router = APIRouter(prefix="/api/central", tags=["central-accounts"])

    def client(tenant=Depends(get_tenant_id)):
        value = current(tenant)
        if value is None:
            raise HTTPException(409, "A central de contas ainda não está ativa para esta sessão.")
        return value

    @router.get("/status")
    def status(tenant=Depends(get_tenant_id)):
        central = current(tenant)
        return {"enabled": central is not None, "sync_mode": "manual" if central else "legacy",
                "refreshed_at": central.refreshed_at if central else None,
                "stores": central.public_stores() if central else []}

    @router.post("/refresh")
    def refresh(central=Depends(client)):
        stores = central.refresh_stores()
        return {"success": True, "stores": stores, "refreshed_at": central.refreshed_at}

    @router.put("/stores/{store_id}/grant")
    def grant(store_id: str, payload: GrantRequest, central=Depends(client)):
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", store_id):
            raise HTTPException(400, "Identidade de loja inválida.")
        return central.call("PUT", f"/stores/{store_id}/grant", payload.model_dump())

    return router
