"""Public, credential-minimizing authentication endpoint for JK Sistema."""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token

from .config import GatewayConfigurationError, GatewaySettings
from .contracts import (GatewayChangePasswordRequest, GatewayCofreSessionRequest,
                        GatewayGoogleLoginRequest, GatewayLoginRequest)
from .domain import (
    AuthRejected,
    AuthenticationService,
    ERROR_MESSAGES,
    GatewayUnavailable,
    LoginRateLimiter,
    assert_supported_version,
)
from .firebase_adapter import FirebaseIdentityTokenIssuer, FirestoreUserRepository, firebase_app
from .cofre_capability import GoogleCofreCapabilityIssuer
from .central_http import build_central, install_central_routes
from .firebase_sessions import build_firebase_sessions


logger = logging.getLogger("jk.auth_gateway")


def _client_address(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:80]
    return str(request.client.host if request.client else "unknown")[:80]


def _cofre_protocol(request: Request) -> int:
    if (request.headers.get("x-jk-cofre-session-protocol") == "2"
            or request.headers.get("x-jk-cofre-protocol") == "2"):
        return 2
    return 1 if request.headers.get("x-jk-cofre-protocol") == "1" else 0


def create_app(
    *,
    service: AuthenticationService | None = None,
    settings: GatewaySettings | None = None,
    central=None,
    cofre_capabilities=None,
    google_verifier=None,
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
                    firebase_sessions=build_firebase_sessions(gateway_settings),
                    cofre_capabilities=resolve_cofre_capabilities(),
                )
                application.state.auth_service = configured
            return configured

    def resolve_cofre_capabilities():
        with state_lock:
            configured = getattr(application.state, "cofre_capabilities", None)
            if configured is None:
                configured = cofre_capabilities or getattr(service, "_cofre_capabilities", None)
                if configured is None:
                    gateway_settings = resolve_settings()
                    configured = GoogleCofreCapabilityIssuer(firebase_app(gateway_settings.project_id),
                        session_secret=gateway_settings.cofre_session_secret,
                        previous_session_secret=gateway_settings.cofre_session_previous_secret)
                application.state.cofre_capabilities = configured
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
            if _cofre_protocol(request):
                return resolve_service().authenticate(payload, cofre_protocol=_cofre_protocol(request),
                    firebase_protocol=request.headers.get("x-jk-firebase-protocol") == "1",
                    central_protocol=request.headers.get("x-jk-central-protocol") == "1")
            if request.headers.get("x-jk-firebase-protocol") == "1":
                return resolve_service().authenticate(payload, firebase_protocol=True,
                    central_protocol=request.headers.get("x-jk-central-protocol") == "1")
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
            if _cofre_protocol(request):
                return resolve_service().change_password(payload, cofre_protocol=_cofre_protocol(request),
                    firebase_protocol=request.headers.get("x-jk-firebase-protocol") == "1",
                    central_protocol=request.headers.get("x-jk-central-protocol") == "1")
            if request.headers.get("x-jk-firebase-protocol") == "1":
                return resolve_service().change_password(payload, firebase_protocol=True,
                    central_protocol=request.headers.get("x-jk-central-protocol") == "1")
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
    @application.post("/api/auth/v1/google-login")
    def google_login(payload: GatewayGoogleLoginRequest, request: Request):
        try:
            if not resolve_limiter().consume("google-login", _client_address(request)):
                raise AuthRejected("rate_limited", 429)
            configured = resolve_settings()
            assert_supported_version(payload.app_version, configured.minimum_app_version)
            audience = getattr(configured, "google_oauth_client_id", "")
            if not audience:
                raise GatewayUnavailable("google_login_not_configured")
            try:
                credential = payload.credential.get_secret_value()
                if google_verifier is not None:
                    claims = google_verifier(credential, audience)
                else:
                    transport = GoogleRequest()

                    def bounded_request(*args, **kwargs):
                        kwargs["timeout"] = configured.identity_timeout_seconds
                        return transport(*args, **kwargs)

                    claims = google_id_token.verify_oauth2_token(credential, bounded_request, audience=audience)
                if (not isinstance(claims, dict)
                        or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
                        or claims.get("aud") != audience or not isinstance(claims.get("sub"), str)
                        or not 1 <= len(claims["sub"]) <= 255):
                    raise ValueError("invalid_google_identity")
            except (ValueError, TypeError):
                raise AuthRejected("invalid_credentials", 401) from None
            return resolve_service().authenticate_google(payload, claims,
                cofre_protocol=_cofre_protocol(request),
                firebase_protocol=request.headers.get("x-jk-firebase-protocol") == "1",
                central_protocol=request.headers.get("x-jk-central-protocol") == "1")
        except AuthRejected as exc:
            return JSONResponse(status_code=exc.status_code,
                content={"success": False, "code": exc.code,
                         "message": ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["auth_unavailable"])})
        except Exception as exc:
            logger.error("google_login_dependency_failure type=%s", type(exc).__name__)
            return JSONResponse(status_code=503,
                content={"success": False, "code": "auth_unavailable", "message": ERROR_MESSAGES["auth_unavailable"]})

    @application.post("/api/auth/v1/cofre/session")
    def cofre_session(payload: GatewayCofreSessionRequest, request: Request):
        try:
            # The bucket does not use any unverified identity or grant bytes.
            if not resolve_limiter().consume("cofre-session", _client_address(request)):
                raise AuthRejected("rate_limited", 429)
            assert_supported_version(payload.app_version, resolve_settings().minimum_app_version)
            # Do not resolve AuthenticationService: its repositories are irrelevant
            # to this exclusively signed-grant/IAM operation.
            result = resolve_cofre_capabilities().renew_session(
                payload.session_grant.get_secret_value(), payload.machine_id)
            return {"success": True, "protocol": 2, "request_nonce": payload.request_nonce,
                    "capability": result["capability"], "session_grant": result["session_grant"],
                    "session_expires_at": result["session_expires_at"]}
        except AuthRejected as exc:
            return JSONResponse(status_code=exc.status_code,
                content={"success": False, "code": exc.code,
                         "message": ERROR_MESSAGES.get(exc.code, ERROR_MESSAGES["auth_unavailable"])})
        except ValueError:
            return JSONResponse(status_code=401,
                content={"success": False, "code": "cofre_session_invalid", "message": "Autorizacao do Cofre invalida ou expirada."})
        except Exception as exc:
            logger.error("cofre_session_dependency_failure type=%s", type(exc).__name__)
            return JSONResponse(status_code=503,
                content={"success": False, "code": "auth_unavailable", "message": ERROR_MESSAGES["auth_unavailable"]})

    if settings is not None:
        application.state.settings = settings
    if service is not None:
        application.state.auth_service = service
    install_central_routes(application, resolve_central)
    return application


app = create_app()


__all__ = ["app", "create_app"]
