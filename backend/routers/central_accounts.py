"""Local authenticated controls for manual central-account updates."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.services.central_accounts_client import current_migration, end_migration_login, session_client
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
    providers: list[Literal["mercadolivre", "bling"]] | None = Field(default=None, min_length=1, max_length=2)
    store_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    legacy_copies_stopped: bool = False


def create_central_accounts_router(get_tenant_id):
    router = APIRouter(prefix="/api/central", tags=["central-accounts"])

    def client(tenant=Depends(get_tenant_id)):
        value = session_client(tenant)
        if value is None:
            raise HTTPException(409, "A central de contas ainda não está ativa para esta sessão.")
        return value

    def migration_client(tenant=Depends(get_tenant_id)):
        value = current_migration(tenant)
        if value is None:
            scoped = session_client(tenant)
            if scoped is not None and getattr(scoped, "sync_mode", "manual") == "provider":
                value = scoped
        if value is None:
            raise HTTPException(409, "Faça o login novamente para obter autorização de migração.")
        return value

    @router.get("/status")
    def status(tenant=Depends(get_tenant_id)):
        central = session_client(tenant)
        return {"enabled": central is not None, "sync_mode": getattr(central, "sync_mode", "manual") if central else "legacy",
                "providers": list(getattr(central, "providers", ())) if central else [],
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
    def migration_preview(provider: Literal["bling"] | None = None, store_id: str | None = None,
                          tenant=Depends(get_tenant_id), _migration=Depends(migration_client)):
        central_accounts_migration.ensure_available(_migration)
        return central_accounts_migration.preview(tenant, [provider] if provider else None,
                                                 [store_id] if store_id else None)

    @router.post("/migration/execute")
    def migration_execute(payload: MigrationExecuteRequest,
                          authorization: str = Header(default=""),
                          tenant=Depends(get_tenant_id), migration=Depends(migration_client)):
        central_accounts_migration.ensure_available(migration)
        result = central_accounts_migration.execute(
            tenant, migration, operation_id=payload.operation_id,
            preview_fingerprint=payload.preview_fingerprint, confirmed=payload.confirmed,
            providers=payload.providers, store_ids=payload.store_ids,
            legacy_copies_stopped=payload.legacy_copies_stopped)
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
