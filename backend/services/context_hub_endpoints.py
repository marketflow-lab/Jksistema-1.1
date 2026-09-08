"""Full-admin FastAPI handlers for the Context Hub.

The authenticated session is the sole source of ``client_id``.  Request
models intentionally reject extra fields, so a caller cannot smuggle a tenant
selector into an administrative operation.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend.modules.context_hub import api as context_hub_api
from backend.modules.context_hub import contracts as context_hub_contracts


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

    sku: str = Field(default="", max_length=120)
    mlb: str = Field(default="", max_length=60)
    store_ref: str = Field(default="", max_length=180)
    module: str = Field(default="", max_length=100)
    ids: list[str] = Field(default_factory=list, max_length=100)
    source_type: str = Field(default="", max_length=100)
    environment: str = Field(default="", max_length=100)
    surface: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=12)
    truth_class: str = Field(default="", max_length=100)
    authority: Literal["", "authoritative", "verified_technical", "advisory", "unverified"] = ""
    sensitivity: str = Field(default="", max_length=80)
    validity: Literal["", "active_generation", "unverified"] = ""
    valid_at: str = Field(
        default="",
        max_length=40,
        pattern=r"^$|^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?$",
    )
    document_types: list[str] = Field(default_factory=list, max_length=10)


class ContextHubSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=500)
    limit: int = Field(default=12, ge=1, le=12)
    sku: str = Field(default="", max_length=120)
    mlb: str = Field(default="", max_length=60)
    store_ref: str = Field(default="", max_length=180)
    module: str = Field(default="", max_length=100)
    ids: list[str] = Field(default_factory=list, max_length=100)
    source_type: str = Field(default="", max_length=100)
    environment: str = Field(default="", max_length=100)
    surface: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=12)
    truth_class: str = Field(default="", max_length=100)
    authority: Literal["", "authoritative", "verified_technical", "advisory", "unverified"] = ""
    sensitivity: str = Field(default="", max_length=80)
    validity: Literal["", "active_generation", "unverified"] = ""
    valid_at: str = Field(
        default="",
        max_length=40,
        pattern=r"^$|^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?$",
    )
    document_types: list[str] = Field(default_factory=list, max_length=10)
    filters: Optional[ContextHubSearchFilters] = None


class CuratedNoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=900_000)
    category: Literal["Notas", "Regras", "ADRs"] = "Notas"


class CuratedNoteRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class CuratedBackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passphrase: SecretStr = Field(min_length=12, max_length=1024)


class CuratedPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="manual_curation_publish", pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    force: bool = False


class StoreSkuScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_ref: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
    store_name: str = Field(default="", max_length=160)
    seller_id: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]{1,32}$")
    site_id: Literal["MLB"] = "MLB"


def _require_full_admin(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    # Late import avoids coupling this module's import to backend_api composition.
    from backend.services.codex.console import security as console_security

    return console_security.require_full_admin(request, authorization)


def _client_id_from_session(session: dict[str, Any]) -> str:
    client_id = str(session.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem cliente valido para o Context Hub.")
    return client_id


def _actor_from_session(session: dict[str, Any]) -> str:
    return str(
        session.get("username")
        or session.get("email")
        or session.get("user_id")
        or session.get("id")
        or "full-admin"
    )


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(error, context_hub_contracts.ContextHubNotFoundError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, context_hub_contracts.ContextHubConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, context_hub_contracts.ContextHubValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, context_hub_contracts.ContextHubError):
        return HTTPException(status_code=503, detail="Context Hub temporariamente indisponivel.")
    return HTTPException(status_code=500, detail="Falha interna no Context Hub.")


def context_hub_status(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.get_status(_client_id_from_session(session))
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
            "settings": context_hub_api.update_settings(
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
        return context_hub_api.rebuild_context(
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
        return context_hub_api.list_generations(_client_id_from_session(session), limit=limit)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_get(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.get_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_publish(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.publish_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_generation_rollback(
    generation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.rollback_generation(_client_id_from_session(session), generation_id)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_search(
    payload: ContextHubSearchRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    filters = payload.filters.model_dump() if payload.filters is not None else {}
    for key in (
        "sku", "mlb", "store_ref", "module", "ids", "source_type", "environment",
        "surface", "tags", "truth_class", "authority", "sensitivity", "validity",
        "valid_at", "document_types",
    ):
        value = getattr(payload, key)
        if value not in ("", [], None):
            filters[key] = value
    try:
        return context_hub_api.search_context(
            _client_id_from_session(session),
            payload.query,
            filters=filters,
            limit=payload.limit,
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_notes(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.list_curated_notes(_client_id_from_session(session))
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_note_create(
    payload: CuratedNoteCreateRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.create_curated_note(
            _client_id_from_session(session),
            title=payload.title,
            body=payload.body,
            category=payload.category,
            actor=_actor_from_session(session),
        )
    except Exception as error:
        raise _translate_error(error) from error


def _curated_transition(
    action: str,
    note_id: str,
    request: Request,
    authorization: Optional[str],
    *,
    reason: str = "",
):
    session = _require_full_admin(request, authorization)
    client_id = _client_id_from_session(session)
    actor = _actor_from_session(session)
    function = {
        "validate": context_hub_api.validate_curated_note,
        "review": context_hub_api.review_curated_note,
        "approve": context_hub_api.approve_curated_note,
    }.get(action)
    try:
        if action == "reject":
            return context_hub_api.reject_curated_note(client_id, note_id, actor=actor, reason=reason)
        if function is None:
            raise context_hub_contracts.ContextHubValidationError("Acao de curadoria invalida.")
        return function(client_id, note_id, actor=actor)
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_note_validate(note_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    return _curated_transition("validate", note_id, request, authorization)


def context_hub_curated_note_review(note_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    return _curated_transition("review", note_id, request, authorization)


def context_hub_curated_note_approve(note_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    return _curated_transition("approve", note_id, request, authorization)


def context_hub_curated_note_reject(
    note_id: str,
    payload: CuratedNoteRejectRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    return _curated_transition("reject", note_id, request, authorization, reason=payload.reason)


def context_hub_curated_publish(
    request: Request,
    payload: Optional[CuratedPublishRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    safe_payload = payload or CuratedPublishRequest()
    try:
        return context_hub_api.publish_curated_context(
            _client_id_from_session(session),
            reason=safe_payload.reason,
            force=safe_payload.force,
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_store_sku_publish(
    payload: StoreSkuScopeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """Publish only approved guidance into the exact active store generation."""

    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.publish_approved_store_guidance(
            _client_id_from_session(session),
            {
                **payload.model_dump(),
                "surface": "mercado_livre_public_questions",
            },
            actor=_actor_from_session(session),
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_store_sku_rollback(
    generation_id: str,
    payload: StoreSkuScopeRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """Roll back one exact store without changing any other store pointer."""

    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.rollback_store_sku_generation(
            _client_id_from_session(session),
            {
                **payload.model_dump(),
                "surface": "mercado_livre_public_questions",
            },
            generation_id,
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_backups(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.list_curated_backups(_client_id_from_session(session))
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_backup_create(
    payload: CuratedBackupRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.create_curated_backup(
            _client_id_from_session(session),
            passphrase=payload.passphrase.get_secret_value(),
        )
    except Exception as error:
        raise _translate_error(error) from error


def context_hub_curated_backup_restore(
    backup_id: str,
    payload: CuratedBackupRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    session = _require_full_admin(request, authorization)
    try:
        return context_hub_api.restore_curated_backup(
            _client_id_from_session(session),
            backup_id,
            passphrase=payload.passphrase.get_secret_value(),
        )
    except Exception as error:
        raise _translate_error(error) from error


__all__ = [
    "CuratedBackupRequest",
    "CuratedNoteCreateRequest",
    "CuratedNoteRejectRequest",
    "CuratedPublishRequest",
    "ContextHubRebuildRequest",
    "ContextHubSearchFilters",
    "ContextHubSearchRequest",
    "ContextHubSettingsRequest",
    "StoreSkuScopeRequest",
    "context_hub_generation_get",
    "context_hub_generation_publish",
    "context_hub_generation_rollback",
    "context_hub_generations",
    "context_hub_rebuild",
    "context_hub_search",
    "context_hub_settings_put",
    "context_hub_status",
    "context_hub_curated_backup_create",
    "context_hub_curated_backup_restore",
    "context_hub_curated_backups",
    "context_hub_curated_note_approve",
    "context_hub_curated_note_create",
    "context_hub_curated_note_reject",
    "context_hub_curated_note_review",
    "context_hub_curated_note_validate",
    "context_hub_curated_notes",
    "context_hub_curated_publish",
    "context_hub_store_sku_publish",
    "context_hub_store_sku_rollback",
]
