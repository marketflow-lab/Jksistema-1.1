"""Technical infrastructure router definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter

from backend.services.infra import build_health_payload, check_app_update, check_mobile_update


@dataclass(frozen=True)
class InfraRouterConfig:
    app_version: Callable[[], str]
    firebase_configured: Callable[[], bool]
    firebase_active: Callable[[], bool]
    firebase_live_features: Callable[[], bool]
    firebase_last_error: Callable[[], str]
    minimum_app_version: Callable[[], str]
    base_dir: str
    pasta_info: str


router = APIRouter(tags=["infra"])


def create_infra_router(config: InfraRouterConfig) -> APIRouter:
    infra_router = APIRouter(tags=["infra"])

    @infra_router.get("/health")
    async def health():
        payload = build_health_payload(
            app_version=config.app_version(),
            firebase_configured=bool(config.firebase_configured()),
            firebase_active=bool(config.firebase_active()),
            firebase_live_features=bool(config.firebase_live_features()),
            firebase_last_error=config.firebase_last_error(),
        )
        payload["versao_minima_app"] = config.minimum_app_version()
        payload["appMinimumVersion"] = payload["versao_minima_app"]
        return payload

    @infra_router.get("/api/app-update/check")
    def api_app_update_check(current_version: Optional[str] = None):
        return check_app_update(current_version)

    @infra_router.get("/api/mobile-update/check")
    def api_mobile_update_check(
        platform: str = "android",
        version: Optional[str] = None,
        versionCode: Optional[int] = None,
    ):
        return check_mobile_update(
            platform=platform,
            version=version,
            version_code=versionCode,
            base_dir=config.base_dir,
            pasta_info=config.pasta_info,
        )

    return infra_router
