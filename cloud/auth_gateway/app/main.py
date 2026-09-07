"""Public, credential-minimizing authentication endpoint for JK Sistema."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import GatewayConfigurationError, GatewaySettings
from .contracts import GatewayChangePasswordRequest, GatewayLoginRequest
from .domain import (
    AuthRejected,
    AuthenticationService,
    ERROR_MESSAGES,
    GatewayUnavailable,
    LoginRateLimiter,
)
from .firebase_adapter import FirebaseIdentityTokenIssuer, FirestoreUserRepository
from .central_http import build_central, install_central_routes


logger = logging.getLogger("jk.auth_gateway")


def _client_address(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:80]
    return str(request.client.host if request.client else "unknown")[:80]


def create_app(
    *,
    service: AuthenticationService | None = None,
    settings: GatewaySettings | None = None,
    central=None,
) -> FastAPI:
    application = FastAPI(
        title="JK Sistema Authentication Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    state_lock = threading.RLock()

    def resolve_central():
        with state_lock:
            if not hasattr(application.state, "central_service"):
                application.state.central_service = central if central is not None else build_central(resolve_settings())
            return application.state.central_service

    def resolve_settings() -> GatewaySettings:
        configured = getattr(application.state, "settings", None)
        if configured is not None:
            return configured
        with state_lock:
            configured = getattr(application.state, "settings", None)
            if configured is None:
                configured = settings or GatewaySettings.from_environment()
                application.state.settings = configured
            return configured

    def resolve_service() -> AuthenticationService:
        configured = getattr(application.state, "auth_service", None)
        if configured is not None:
            return configured
        with state_lock:
            configured = getattr(application.state, "auth_service", None)
            if configured is None:
                gateway_settings = resolve_settings()
                configured = service or AuthenticationService(
                    FirestoreUserRepository(gateway_settings),
                    FirebaseIdentityTokenIssuer(gateway_settings),
                    gateway_settings.minimum_app_version,
                    central=resolve_central(),
                )
                application.state.auth_service = configured
            return configured

    def resolve_limiter() -> LoginRateLimiter:
        configured = getattr(application.state, "rate_limiter", None)
        if configured is not None:
            return configured
        with state_lock:
            configured = getattr(application.state, "rate_limiter", None)
            if configured is None:
                gateway_settings = resolve_settings()
                configured = LoginRateLimiter(
                    gateway_settings.rate_limit_attempts,
                    gateway_settings.rate_limit_window_seconds,
                )
                application.state.rate_limiter = configured
            return configured

    @application.middleware("http")
    async def add_security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            # Never log exceptions containing provider request data or vault contents.
            response = JSONResponse(status_code=503, content={"success": False, "code": "central_unavailable"})
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"success": False, "code": "invalid_request", "message": "Dados enviados invalidos."},
        )

    @application.get("/health")
    @application.get("/api/auth/v1/health")
    def health() -> dict[str, Any]:
        try:
            resolve_settings()
            return {"ok": True, "service": "jk-auth-gateway", "protocol": 1}
        except GatewayConfigurationError:
            return {"ok": False, "service": "jk-auth-gateway", "protocol": 1}

    @application.post("/api/auth/v1/login")
    def login(payload: GatewayLoginRequest, request: Request):
        try:
            limiter = resolve_limiter()
            if not limiter.consume(payload.username, _client_address(request)):
                raise AuthRejected("rate_limited", 429)
            if request.headers.get("x-jk-central-protocol") == "1":
                return resolve_service().authenticate(payload, central_protocol=True)
            return resolve_service().authenticate(payload)
        except AuthRejected as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "code": exc.code,
                    "message": ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["auth_unavailable"]),
                },
            )
        except (GatewayConfigurationError, GatewayUnavailable) as exc:
            logger.error("authentication_dependency_failure type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "auth_unavailable",
                    "message": ERROR_MESSAGES["auth_unavailable"],
                },
            )
        except Exception as exc:  # Keep credential-bearing failures out of logs.
            logger.error("authentication_unexpected_failure type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "auth_unavailable",
                    "message": ERROR_MESSAGES["auth_unavailable"],
                },
            )

    @application.post("/api/auth/v1/change-password")
    def change_password(payload: GatewayChangePasswordRequest, request: Request):
        try:
            limiter = resolve_limiter()
            if not limiter.consume(payload.username, _client_address(request)):
                raise AuthRejected("rate_limited", 429)
            if request.headers.get("x-jk-central-protocol") == "1":
                return resolve_service().change_password(payload, central_protocol=True)
            return resolve_service().change_password(payload)
        except AuthRejected as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "code": exc.code,
                    "message": ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["auth_unavailable"]),
                },
            )
        except (GatewayConfigurationError, GatewayUnavailable) as exc:
            logger.error("password_change_dependency_failure type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "auth_unavailable",
                    "message": ERROR_MESSAGES["auth_unavailable"],
                },
            )
        except Exception as exc:
            logger.error("password_change_unexpected_failure type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "code": "auth_unavailable",
                    "message": ERROR_MESSAGES["auth_unavailable"],
                },
            )
    if settings is not None:
        application.state.settings = settings
    if service is not None:
        application.state.auth_service = service
    install_central_routes(application, resolve_central)
    return application


app = create_app()


__all__ = ["app", "create_app"]
