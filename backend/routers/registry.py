from __future__ import annotations

from fastapi import FastAPI

from . import ia


def include_feature_routers(app: FastAPI) -> None:
    """Include routers that are ready for newly migrated feature endpoints."""
    for router in (
        ia.router,
    ):
        app.include_router(router)
