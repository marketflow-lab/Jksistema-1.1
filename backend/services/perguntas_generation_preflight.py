"""Canonical loading gate for manual AI. No browser readiness flag is evidence."""
from __future__ import annotations

import contextvars
import copy
from dataclasses import replace
import hashlib
import json
import math
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import questions_loading
from backend.modules.perguntas_pos_venda.endpoints.questions_loading_support import Scope, resolve_scope
from backend.services import perguntas_loading_cache

POLICY = "jk_manual_question_canonical_preflight_v1"
HISTORY_TRUNCATED_WARNING = "Historico consultado nas 50 perguntas recentes do anuncio, filtradas pelo comprador."
_SESSION_TTL_SECONDS = 15 * 60.0
_SESSIONS = OrderedDict()
_LOCK = threading.Lock()


@dataclass
class GenerationSession:
    expiry: float
    auth_context: contextvars.Context
    scope_key: tuple
    fingerprint: str
    snapshot_hash: str
    snapshot: dict | None


class GenerationContextUnavailable(HTTPException):
    pass


def _blocked(component="history", *, access=False, reason="component_not_ready", status=None,
             retryable=None, retry_after=None, detail=None):
    is_retryable = (not access) if retryable is None else bool(retryable)
    headers = {"X-JK-Error-Code": "generation_context_unavailable",
               "X-JK-Error-Component": component, "X-JK-Error-Reason": reason,
               "X-JK-Error-Scope": "resource", "X-JK-Retryable": str(is_retryable).lower()}
    if retry_after is not None and str(retry_after).isdigit():
        headers["Retry-After"] = str(retry_after)
    return GenerationContextUnavailable(status or (403 if access else 409),
        detail or "Confirme o carregamento da pergunta, anuncio e historico antes de gerar a resposta.",
        headers=headers)


def identity_mismatch(reason: str = "store_identity_mismatch") -> GenerationContextUnavailable:
    """Build the public preflight contract for a server-side identity mismatch."""

    normalized_reason = str(reason or "store_identity_mismatch")
    detail = {
        "store_identity_unconfirmed": "A identidade exata da loja nao foi confirmada.",
        "store_config_unconfirmed": "A configuracao da loja exata nao foi confirmada.",
        "seller_id_mismatch": "O seller_id nao pertence a loja informada.",
        "seller_id_unconfirmed": "O seller_id exato da loja nao foi confirmado.",
        "site_id_mismatch": "O site_id nao pertence a loja informada.",
        "site_id_invalid": "O site_id confirmado da loja e invalido.",
    }.get(normalized_reason, "Os dados da loja, pergunta ou anuncio mudaram.")
    return _blocked(
        "identity",
        access=True,
        reason=normalized_reason,
        status=409,
        retryable=False,
        detail=detail,
    )


def _translate(error: HTTPException) -> GenerationContextUnavailable:
    headers = error.headers or {}
    scope = headers.get("X-JK-Error-Scope", "resource")
    component = headers.get("X-JK-Error-Component") or (
        "session" if scope == "session" else "store" if scope == "store" else "context"
    )
    retry_after = headers.get("Retry-After")
    return _blocked(
        component,
        access=error.status_code in {401, 403, 404},
        reason=headers.get("X-JK-Error-Reason") or headers.get("X-JK-Error-Code") or "component_not_ready",
        status=error.status_code,
        retryable=headers.get("X-JK-Retryable", str(
            error.status_code in {408, 429} or error.status_code >= 500,
        ).lower()) == "true",
        retry_after=retry_after,
    )


def validate_context(data: dict, scope: Scope, question_id: str) -> dict:
    identity = data.get("scope") or {}
    for field in ("client_id", "username", "store_id", "seller_id", "site_id"):
        if not getattr(scope, field) or str(identity.get(field) or "") != getattr(scope, field):
            raise _blocked("identity", access=True, reason=f"{field}_mismatch", status=409)
    components = data.get("components") or {}
    for field in ("question", "item", "history"):
        state = (components.get(field) or {}).get("state")
        if state != "ready":
            component = components.get(field) or {}
            raise _blocked(field, access=state in {"access_blocked", "blocked"},
                           reason=str(component.get("code") or "component_not_ready"),
                           retryable=bool(component.get("retryable")),
                           retry_after=component.get("retry_after"))
    question, item = data.get("question") or {}, data.get("item") or {}
    if (data.get("stale") or str(question.get("id") or "") != str(question_id)
            or not question.get("text") or not item.get("id")
            or str(question.get("item_id") or "") != str(item.get("id"))
            or str(item.get("seller_id") or "") != scope.seller_id
            or str(item.get("site_id") or scope.site_id) != scope.site_id):
        raise _blocked("identity", access=True, reason="question_or_item_mismatch", status=409)
    return data


def load_context(request: Request, scope: Scope, question_id: str) -> dict:
    if request is None or scope is None:
        raise _blocked("session", access=True)
    try:
        data = questions_loading.canonical_context(
            request, scope.client_id, scope.store_id, str(question_id), force=False,
        )
    except HTTPException as error:
        if error.status_code in {401, 403, 404}:
            perguntas_loading_cache._discard_scope((scope.client_id, scope.store_id))
        raise _translate(error) from error
    return validate_context(data, scope, str(question_id))


def _snapshot_hash(canonical: dict | None) -> str:
    stable = copy.deepcopy(canonical) if isinstance(canonical, dict) else {}
    status = stable.get("context_status")
    if isinstance(status, dict):
        status.pop("age_seconds", None)
    return hashlib.sha256(json.dumps(
        stable, sort_keys=True, default=str, separators=(",", ":"),
    ).encode()).hexdigest()


def remember_session(request: Request, scope: Scope, question_id: str = "", canonical: dict | None = None) -> str:
    """Keep the initiating auth context only in memory; restart requires a new request."""
    payload = getattr(request.state, "auth_payload", {}) or {}
    if (not isinstance(payload, dict)
            or str(payload.get("sub") or "") != scope.username
            or str(payload.get("client_id") or "") != scope.client_id):
        raise _blocked("session", access=True, reason="session_identity_invalid")
    now = time.time()
    expiry = now + _SESSION_TTL_SECONDS
    if "exp" in payload:
        try:
            token_expiry = float(payload["exp"])
        except (ValueError, TypeError):
            raise _blocked("session", access=True, reason="session_expiry_invalid") from None
        if not math.isfinite(token_expiry):
            raise _blocked("session", access=True, reason="session_expiry_invalid")
        expiry = min(expiry, token_expiry)
    if expiry <= now:
        raise _blocked("session", access=True)
    # Session claims are supplied by authenticated middleware, never the body.
    # A stable handle deduplicates concurrent clicks without sharing two logins.
    frozen = copy.deepcopy(canonical) if isinstance(canonical, dict) else None
    snapshot_hash = _snapshot_hash(frozen)
    fingerprint = hashlib.sha256(json.dumps(
        [payload, scope.key("generation"), str(question_id), snapshot_hash],
        sort_keys=True, default=str, separators=(",", ":"),
    ).encode()).hexdigest()
    key = uuid.uuid4().hex
    with _LOCK:
        for existing in list(_SESSIONS):
            if _SESSIONS[existing].expiry <= time.time():
                _SESSIONS.pop(existing)
        for existing, entry in _SESSIONS.items():
            if entry.fingerprint == fingerprint:
                _SESSIONS.move_to_end(existing)
                return existing
        _SESSIONS[key] = GenerationSession(
            expiry, contextvars.copy_context(), scope.key("generation"), fingerprint,
            snapshot_hash, frozen,
        )
        _SESSIONS.move_to_end(key)
        while len(_SESSIONS) > 128:
            _SESSIONS.popitem(last=False)
    return key


def run_with_session(job: dict, callback):
    scope = Scope(str(job.get("client_id") or ""), str(job.get("store_id") or ""),
                  str(job.get("created_by") or ""), str(job.get("seller_id") or ""),
                  str(job.get("site_id") or ""), str(job.get("store") or ""))
    with _LOCK:
        remembered = _SESSIONS.get((job.get("request") or {}).get("_generation_session"))
    if (not remembered or remembered.expiry <= time.time()
            or remembered.scope_key != scope.key("generation")):
        raise _blocked("session", access=True, reason="session_expired")
    if (remembered.snapshot is not None
            and int(remembered.snapshot.get("cache_revision", -1)) != perguntas_loading_cache.revision(
                scope.client_id, scope.store_id)):
        raise _blocked("context", reason="context_superseded", retryable=True)
    try:
        return remembered.auth_context.copy().run(callback)
    except GenerationContextUnavailable:
        raise
    except HTTPException as error:
        if error.status_code in {401, 403}:
            raise GenerationContextUnavailable(error.status_code, error.detail, error.headers) from error
        raise


def forget_session(job: dict) -> None:
    handle = (job.get("request") or {}).get("_generation_session")
    if handle:
        with _LOCK:
            _SESSIONS.pop(handle, None)


def forget_store_sessions(client_id: str, store_id: str) -> None:
    """Purge frozen snapshots after a confirmed mutation or access change."""
    with _LOCK:
        for handle, remembered in list(_SESSIONS.items()):
            if remembered.scope_key[:2] == (str(client_id), str(store_id)):
                _SESSIONS.pop(handle, None)


perguntas_loading_cache.register_invalidation_listener(forget_store_sessions)


def load_job_context(job: dict, runtime) -> dict:
    scope = Scope(str(job.get("client_id") or ""), str(job.get("store_id") or ""),
                  str(job.get("created_by") or ""), str(job.get("seller_id") or ""),
                  str(job.get("site_id") or ""), str(job.get("store") or ""))

    def load():
        # This runs under the initiating Firebase/central session, never an ambient
        # admin credential from a scheduler thread. Those clients enforce revocation.
        from backend.services import central_accounts_client
        central = central_accounts_client.current(scope.client_id)
        if central is not None:
            if central.expires_at <= time.time() or central.username != scope.username:
                raise _blocked("session", access=True)
            permissions = central.permissions
        else:
            user = runtime._obter_usuario_sql(scope.username)
            if (str(user.get("client_id") or "") != scope.client_id
                    or not runtime._login_usuario_ativo(user)
                    or not runtime._login_validade_ok(user)[0]):
                raise _blocked("session", access=True)
            permissions = runtime._carregar_permissoes_usuario(scope.username, scope.client_id)
        if not runtime._permissoes_autorizam_rota(permissions, "perguntas_pos_venda"):
            raise _blocked("session", access=True, reason="permission_revoked")
        request = Request({"type": "http", "headers": []})
        request.state.username, request.state.client_id = scope.username, scope.client_id
        current = resolve_scope(request, scope.client_id, scope.store_id, resolve_site=False)
        if not current.site_id:
            cached_identity = perguntas_loading_cache.peek_current(
                current.key("identity"), max_age=3600,
            )
            cached_site = str(((cached_identity or {}).get("value") or {}).get("site_id") or "").strip()
            if cached_site:
                current = replace(current, site_id=cached_site)
        if current.key("identity")[:5] != scope.key("identity")[:5]:
            raise _blocked("identity", access=True, reason="store_changed", status=409)
        with _LOCK:
            remembered = _SESSIONS.get((job.get("request") or {}).get("_generation_session"))
            snapshot = copy.deepcopy(remembered.snapshot) if remembered and remembered.snapshot else None
        if snapshot is None:
            raise _blocked("context", reason="context_expired", retryable=True)
        if int(snapshot.get("cache_revision", -1)) != perguntas_loading_cache.revision(
                scope.client_id, scope.store_id):
            raise _blocked("context", reason="context_superseded", retryable=True)
        return validate_context(
            snapshot, scope, str(job.get("question_id") or job.get("event_subject_key") or ""),
        )

    try:
        return run_with_session(job, load)
    except GenerationContextUnavailable:
        forget_session(job)
        raise
    except HTTPException as error:
        # Store publication is independent of authentication. Keep the initiating
        # session so the orchestrator can retry the same authorized operation.
        from backend.services.perguntas_pos_venda_codex import _is_store_contention
        if _is_store_contention(error):
            raise
        raise _blocked("session", access=True) from error
    except Exception as error:
        raise _blocked("session", access=True) from error
