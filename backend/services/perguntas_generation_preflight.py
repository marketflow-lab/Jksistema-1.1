"""Canonical loading gate for manual AI. No browser readiness flag is evidence."""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import time
import uuid
from collections import OrderedDict

from fastapi import HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import questions_loading
from backend.modules.perguntas_pos_venda.endpoints.questions_loading_support import Scope

POLICY = "jk_manual_question_canonical_preflight_v1"
HISTORY_TRUNCATED_WARNING = "Historico consultado nas 50 perguntas recentes do anuncio, filtradas pelo comprador."
_SESSIONS = OrderedDict()
_LOCK = threading.Lock()


class GenerationContextUnavailable(HTTPException):
    pass


def _blocked(component="history", *, access=False):
    return GenerationContextUnavailable(403 if access else 409,
        "Confirme o carregamento da pergunta, anuncio e historico antes de gerar a resposta.",
        headers={"X-JK-Error-Code": "generation_context_unavailable",
                 "X-JK-Error-Component": component, "X-JK-Error-Scope": "resource",
                 "X-JK-Retryable": str(not access).lower()})


def validate_context(data: dict, scope: Scope, question_id: str) -> dict:
    identity = data.get("scope") or {}
    for field in ("client_id", "username", "store_id", "seller_id", "site_id", "name"):
        if not getattr(scope, field) or str(identity.get(field) or "") != getattr(scope, field):
            raise _blocked("identity", access=True)
    components = data.get("components") or {}
    for field in ("question", "item", "history"):
        state = (components.get(field) or {}).get("state")
        if state != "ready":
            raise _blocked(field, access=state in {"access_blocked", "blocked"})
    question, item = data.get("question") or {}, data.get("item") or {}
    if (data.get("stale") or str(question.get("id") or "") != str(question_id)
            or not question.get("text") or not item.get("id")
            or str(question.get("item_id") or "") != str(item.get("id"))
            or str(item.get("seller_id") or "") != scope.seller_id
            or str(item.get("site_id") or scope.site_id) != scope.site_id):
        raise _blocked("identity", access=True)
    return data


def load_context(request: Request, scope: Scope, question_id: str) -> dict:
    if request is None or scope is None:
        raise _blocked("session", access=True)
    try:
        data = questions_loading.canonical_context(
            request, scope.client_id, scope.store_id, str(question_id), force=True,
        )
    except HTTPException as error:
        raise GenerationContextUnavailable(error.status_code, error.detail, error.headers) from error
    return validate_context(data, scope, str(question_id))


def remember_session(request: Request, scope: Scope, question_id: str = "") -> str:
    """Keep the initiating auth context only in memory; restart requires a new request."""
    payload = getattr(request.state, "auth_payload", {}) or {}
    try:
        expiry = min(float(payload.get("exp") or 0), time.time() + 900)
    except (ValueError, TypeError):
        expiry = 0
    if expiry <= time.time():
        raise _blocked("session", access=True)
    # Session claims are supplied by authenticated middleware, never the body.
    # A stable handle deduplicates concurrent clicks without sharing two logins.
    fingerprint = hashlib.sha256(json.dumps(
        [payload, scope.key("generation"), str(question_id)],
        sort_keys=True, default=str, separators=(",", ":"),
    ).encode()).hexdigest()
    key = uuid.uuid4().hex
    with _LOCK:
        for existing in list(_SESSIONS):
            if _SESSIONS[existing][0] <= time.time():
                _SESSIONS.pop(existing)
        for existing, entry in _SESSIONS.items():
            if entry[3] == fingerprint:
                return existing
        _SESSIONS[key] = (expiry, contextvars.copy_context(), scope.key("generation"), fingerprint)
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
    if (not remembered or remembered[0] <= time.time()
            or remembered[2] != scope.key("generation")):
        raise _blocked("session", access=True)
    try:
        return remembered[1].copy().run(callback)
    except GenerationContextUnavailable:
        raise
    except HTTPException as error:
        if error.status_code in {401, 403}:
            raise GenerationContextUnavailable(error.status_code, error.detail, error.headers) from error
        raise


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
            raise _blocked("session", access=True)
        request = Request({"type": "http", "headers": []})
        request.state.username, request.state.client_id = scope.username, scope.client_id
        return load_context(request, scope, str(job.get("question_id") or job.get("event_subject_key") or ""))

    try:
        return run_with_session(job, load)
    except GenerationContextUnavailable:
        raise
    except Exception as error:
        raise _blocked("session", access=True) from error
