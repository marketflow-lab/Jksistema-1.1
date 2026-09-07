"""Opt-in central API, installed alongside the existing login contract."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .central_accounts import CentralAccounts, CentralError
from .central_contracts import (ConnectRequest, DisconnectRequest, LegacyAdoptionRequest,
                                ProviderRequest, StoreCreate, StoreGrant)
from .central_provider import ProviderTransport
from .central_store import FirestoreDocuments, Vault
from .errors import GatewayUnavailable


def build_central(settings):
    if os.environ.get("JK_CENTRAL_ENABLED") != "1":
        return None
    from firebase_admin import firestore
    from .firebase_adapter import FirestoreUserRepository, firebase_app
    origin = os.environ.get("JK_CENTRAL_PUBLIC_ORIGIN", "").strip().rstrip("/")
    parts = urlsplit(origin)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path):
        raise GatewayUnavailable("central_origin_invalid")
    vault = Vault(os.environ.get("JK_CENTRAL_VAULT_KEY", ""))
    client = firestore.client(app=firebase_app(settings.project_id))
    return CentralAccounts(FirestoreUserRepository(settings),
                           FirestoreDocuments(client, settings.firestore_timeout_seconds),
                           vault, ProviderTransport(), origin,
                           enrollment_required=os.environ.get("JK_CENTRAL_REQUIRE_ENROLLMENT", "1") != "0")


def install_central_routes(app, resolve_central):
    router = APIRouter(prefix="/api/central/v1")

    def service():
        central = resolve_central()
        if central is None:
            raise CentralError("central_not_enabled", 503)
        return central

    def principal(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 8192:
            raise CentralError("central_session_required", 401)
        return service().authenticate(authorization[7:], request.headers.get("x-jk-machine", ""))

    def migration_principal(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 8192:
            raise CentralError("central_migration_session_required", 401)
        return service().authenticate_migration(
            authorization[7:], request.headers.get("x-jk-machine", ""))

    @app.exception_handler(CentralError)
    async def central_error(_request, exc):
        details = {key: value for key, value in exc.details.items()
                   if key in {"store_id", "provider"} and isinstance(value, str)}
        return JSONResponse(status_code=exc.status,
                            content={"success": False, "code": exc.code, **details})

    @app.exception_handler(GatewayUnavailable)
    async def central_unavailable(_request, _exc):
        return JSONResponse(status_code=503, content={"success": False, "code": "central_unavailable"})

    @router.get("/bootstrap")
    def bootstrap(actor=Depends(principal)):
        return {"protocol": 1, "sync_mode": "manual", "stores": service().list_stores(actor)}

    @router.post("/stores")
    def create(payload: StoreCreate, actor=Depends(principal)):
        return service().create_store(actor, payload)

    def store_id(value):
        if not re.fullmatch(r"(?:[a-f0-9]{24}|[a-f0-9]{32})", value):
            raise CentralError("central_store_invalid", 400)
        return value

    @router.post("/migrations/legacy")
    def adopt_legacy(payload: LegacyAdoptionRequest, actor=Depends(migration_principal)):
        return service().adopt_legacy(actor, payload)

    @router.get("/migrations/legacy/{operation_id}")
    def migration_status(operation_id: str, actor=Depends(migration_principal)):
        if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise CentralError("central_migration_invalid", 400)
        return service().migration_status(actor, operation_id)

    @router.delete("/stores/{identity}")
    def remove(identity: str, actor=Depends(principal)):
        return service().delete_store(actor, store_id(identity))

    @router.put("/stores/{identity}/grant")
    def grant(identity: str, payload: StoreGrant, actor=Depends(principal)):
        return service().grant(actor, store_id(identity), payload)

    @router.post("/stores/{identity}/connect")
    def connect(identity: str, payload: ConnectRequest, actor=Depends(principal)):
        return service().connect(actor, store_id(identity), payload)

    @router.post("/stores/{identity}/disconnect")
    def disconnect(identity: str, payload: DisconnectRequest, actor=Depends(principal)):
        return service().disconnect(actor, store_id(identity), payload.provider)

    @router.post("/stores/{identity}/request")
    def provider_request(identity: str, payload: ProviderRequest, actor=Depends(principal)):
        return service().request(actor, store_id(identity), payload)

    @router.get("/oauth/callback")
    def callback(state: str = "", code: str = ""):
        try:
            if not (20 <= len(state) <= 200 and 1 <= len(code) <= 4096):
                raise ValueError()
            service().complete_oauth(state, code)
            message = "Conexão concluída. Volte ao JK Sistema e clique em Atualizar lojas."
            status = 200
        except Exception:
            message = "Não foi possível concluir. Volte ao JK Sistema e inicie uma nova conexão."
            status = 400
        return HTMLResponse("<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
                            "<title>JK Sistema</title><p>" + message + "</p></html>", status_code=status,
                            headers={"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})

    app.include_router(router)
