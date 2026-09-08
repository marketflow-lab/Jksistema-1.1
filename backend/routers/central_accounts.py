"""Local authenticated controls for manual central-account updates."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.services.central_accounts_client import current, current_migration, end_migration_login
from backend.services import central_accounts_migration


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    access: Literal["read", "write", "revoke"]


class MigrationExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    preview_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False


def create_central_accounts_router(get_tenant_id):
    router = APIRouter(prefix="/api/central", tags=["central-accounts"])

    def client(tenant=Depends(get_tenant_id)):
        value = current(tenant)
        if value is None:
            raise HTTPException(409, "A central de contas ainda não está ativa para esta sessão.")
        return value

    def migration_client(tenant=Depends(get_tenant_id)):
        value = current_migration(tenant)
        if value is None:
            raise HTTPException(409, "Faça o login novamente para obter autorização de migração.")
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
        if not re.fullmatch(r"(?:[a-f0-9]{24}|[a-f0-9]{32})", store_id):
            raise HTTPException(400, "Identidade de loja inválida.")
        return central.call("PUT", f"/stores/{store_id}/grant", payload.model_dump())

    @router.get("/migration/preview")
    def migration_preview(tenant=Depends(get_tenant_id), _migration=Depends(migration_client)):
        return central_accounts_migration.preview(tenant)

    @router.post("/migration/execute")
    def migration_execute(payload: MigrationExecuteRequest,
                          authorization: str = Header(default=""),
                          tenant=Depends(get_tenant_id), migration=Depends(migration_client)):
        result = central_accounts_migration.execute(
            tenant, migration, operation_id=payload.operation_id,
            preview_fingerprint=payload.preview_fingerprint, confirmed=payload.confirmed)
        if result.get("logout_required") and authorization.startswith("Bearer "):
            end_migration_login(authorization[7:])
        return result

    @router.get("/migration/status")
    def migration_status(operation_id: str = "", tenant=Depends(get_tenant_id),
                         migration=Depends(migration_client)):
        return central_accounts_migration.status(tenant, migration, operation_id)

    try:
        central_accounts_migration.cleanup_all_expired_backups()
    except Exception:
        pass

    return router
