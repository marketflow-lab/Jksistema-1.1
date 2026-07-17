"""Full-admin FastAPI handlers for the Context Hub.

The authenticated session is the sole source of ``client_id``.  Request
models intentionally reject extra fields, so a caller cannot smuggle a tenant
selector into an administrative operation.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.services import context_hub


class ContextHubSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_publish_enabled: Optional[bool] = None
    auto_publish: Optional[bool] = None
    paused: Optional[bool] = None
    watch_enabled: Optional[bool] = None
    watch: Optional[bool] = None
    debounce_seconds: Optional[int] = Field(default=None, ge=1, le=300)
    retention_generations: Optional[int] = Field(default=None, ge=1, le=20)


class ContextHubRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="manual_admin", pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    force: bool = False


class ContextHubSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str = Field(default="", max_length=100)
    ids: list[str] = Field(default_factory=list, max_length=100)
    source_type: str = Field(default="", max_length=100)
    environment: str = Field(default="", max_length=100)


class ContextHubSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=500)
    limit: int = Field(default=12, ge=1, le=12)
    module: str = Field(default="", max_length=100)
    ids: list[str] = Field(default_factory=list, max_length=100)
    source_type: str = Field(default="", max_length=100)
    environment: str = Field(default="", max_length=100)
    filters: Optional[ContextHubSearchFilters] = None


def _require_full_admin(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    # Late import avoids coupling this module's import to backend_api composition.
    from backend.services import codex_console

    return codex_console._codex_require_full_admin(request, authorization)


def _client_id_from_session(session: dict[str, Any]) -> str:
    client_id = str(session.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem cliente valido para o Context Hub.")
    return client_id


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, context_hub.ContextHubNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, context_hub.ContextHubConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, context_hub.ContextHubValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, context_hub.ContextHubError):
        return HTTPException(status_code=503, detail="Context Hub temporariamente indisponivel.")
    return HTTPException(status_code=500, detail="Falha interna no Context Hub.")


def context_hub_status(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub.get_status(_client_id_from_session(session))
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_settings_put(
    payload: ContextHubSettingsRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    if (
        payload.auto_publish_enabled is not None
        and payload.auto_publish is not None
        and payload.auto_publish_enabled != payload.auto_publish
    ):
        raise HTTPException(status_code=422, detail="Configuracoes de publicacao conflitantes.")
    if payload.watch_enabled is not None and payload.watch is not None and payload.watch_enabled != payload.watch:
        raise HTTPException(status_code=422, detail="Configuracoes de watcher conflitantes.")
    try:
        return {
            "success": True,
            "settings": context_hub.update_settings(
                _client_id_from_session(session),
                auto_publish_enabled=(
                    payload.auto_publish_enabled
                    if payload.auto_publish_enabled is not None
                    else payload.auto_publish
                ),
                paused=payload.paused,
                watch_enabled=payload.watch_enabled if payload.watch_enabled is not None else payload.watch,
                debounce_seconds=payload.debounce_seconds,
                retention_generations=payload.retention_generations,
            ),
        }
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_rebuild(
    request: Request,
    payload: Optional[ContextHubRebuildRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    safe_payload = payload or ContextHubRebuildRequest()
    try:
        return context_hub.rebuild_context(
            _client_id_from_session(session),
            reason=safe_payload.reason,
            force=safe_payload.force,
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub.list_generations(_client_id_from_session(session), limit=limit)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_get(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub.get_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_publish(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub.publish_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_rollback(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub.rollback_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_search(
    payload: ContextHubSearchRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    filters = payload.filters.model_dump() if payload.filters is not None else {}
    for key in ("module", "ids", "source_type", "environment"):
        value = getattr(payload, key)
        if value not in ("", [], None):
            filters[key] = value
    try:
        return context_hub.search_context(
            _client_id_from_session(session),
            payload.query,
            filters=filters,
            limit=payload.limit,
        )
    except Exception as error:
        raise _translate_error(error) from error


__all__ = [
    "ContextHubRebuildRequest",
    "ContextHubSearchRequest",
    "ContextHubSettingsRequest",
    "context_hub_generation_get",
    "context_hub_generation_publish",
    "context_hub_generation_rollback",
    "context_hub_generations",
    "context_hub_rebuild",
    "context_hub_search",
    "context_hub_settings_put",
    "context_hub_status",
]
