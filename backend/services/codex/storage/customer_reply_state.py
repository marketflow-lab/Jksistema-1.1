"""Transient and durable customer-reply state helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.services import secure_credentials

from .common import _safe_id


_CUSTOMER_REPLY_TRANSIENT_LOCK = threading.RLock()


_CUSTOMER_REPLY_TRANSIENT: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


_CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS = 24 * 60 * 60


_CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS = 7 * 24 * 60 * 60


_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS = 7


_CUSTOMER_REPLY_SEALED_RESULT_FIELD = "sealed_result_v1"


_CUSTOMER_REPLY_SEALED_RESULT_VERSION = 1


_CUSTOMER_REPLY_SEALED_RESULT_TTL_SECONDS = 7 * 24 * 60 * 60


_CUSTOMER_REPLY_SEALED_RESULT_FIELDS = frozenset({
    "resposta", "model", "proposal_id", "proposal_version", "proposal_hash",
    "data_sufficient", "publish_attempted", "requires_approval",
    "completed_with_partial", "blocked_without_draft", "draft_source",
    "completion_reason", "review_required",
})


_CUSTOMER_REPLY_SEALED_CONTEXT_FIELDS = frozenset({
    "model", "modo_ia", "ia_decision", "ia_categoria", "ia_validacao_ok",
    "ia_requer_revisao_humana", "manual_edit_required", "manual_edit_reason",
})


# Customer prompts, buyer history, plaintext answers, evidence, queries, sources,
# tool output and operator guidance are intentionally absent. Only the closed,
# VIN-free VehicleIdentityFactsV1 and the separately sealed minimal draft join
# the durable scheduler/lease journal.
_CUSTOMER_REPLY_DURABLE_FIELDS = frozenset({
    "job_id", "profile", "task_type", "subject_key", "event_subject_key",
    "question_id", "item_id", "store", "store_id", "seller_id", "site_id",
    "client_id", "channel", "status",
    "agent_state", "current_step", "idempotency_key", "request_hash",
    "thread_id", "thread_reused", "thread_restart_reason", "prompt_version",
    "prompt_hash", "schema_version", "conversation_id", "previous_job_id",
    "plan_id", "proposal_id", "proposal_version", "proposal_hash", "action_id",
    "cancel_requested", "request_generation", "attempt_count",
    "vehicle_identity_capture_status", "vehicle_identity",
    "operational_failure_count", "retry_count", "retry_policy",
    "queue_origin", "queue_priority", "queue_policy_version",
    "evidence_attempt_count", "evidence_attempt_limit",
    "operational_failure_limit", "total_attempt_limit",
    "first_started_at_epoch", "execution_deadline_epoch",
    "next_retry_at_epoch", "next_retry_delay_seconds", "deadline_seconds",
    "deadline_at_epoch", "deadline_reached", "completed_with_partial",
    "blocked_without_draft", "contract_quarantined", "lease_owner",
    "lease_expires_ts", "lease_generation", "heartbeat_at", "retry_ready_at",
    "last_attempt_completed_at", "created_at", "updated_at", "completed_at",
    "data_sufficient", "publish_attempted", "requires_approval",
    "completion_reason", "draft_source", "review_required", "draft_expired", "retry_kind",
})


_CUSTOMER_REPLY_SCOPE_ID_FIELDS = frozenset({
    "question_id", "item_id", "buyer_id", "pack_id", "order_id",
})


def _customer_reply_safe_warnings(values: Any) -> list[str]:
    """Map internal diagnostics to a small operator-safe vocabulary."""

    result: list[str] = []
    rules = (
        (("evidencia", "coverage"), "Ainda faltam evidencias confiaveis para uma conclusao completa."),
        (("compatibilidade",), "A compatibilidade nao teve confirmacao tecnica completa."),
        (("rascunho", "informacoes disponiveis"), "A conclusao usa somente as informacoes disponiveis."),
        (("orientacao",), "A orientacao do operador mudou durante o processamento."),
        (("indispon", "timeout", "429", "connection"), "Um servico ficou temporariamente indisponivel."),
        (("reinicializa",), "O resultado anterior deixou de estar disponivel apos a reinicializacao."),
    )
    for value in values if isinstance(values, list) else []:
        normalized = str(value or "").strip().casefold()
        message = next(
            (safe for markers, safe in rules if any(marker in normalized for marker in markers)),
            "",
        )
        if message and message not in result:
            result.append(message)
    return result[:12]


def _customer_reply_cache_key(db_path: str, job_id: str) -> tuple[str, str]:
    return (os.path.abspath(db_path), _safe_id(job_id, ""))


def _customer_reply_scope_id(db_path: str) -> str:
    return _safe_id(os.path.basename(os.path.dirname(os.path.dirname(db_path))), "default")


def _customer_reply_sealed_results_enabled() -> bool:
    """Keep ordinary tests away from the user's credential vault."""

    return bool(
        secure_credentials.secure_store_available()
        and not os.environ.get("PYTEST_CURRENT_TEST")
    )


def _customer_reply_result_key(client_id: str, *, create: bool) -> bytes:
    if not _customer_reply_sealed_results_enabled():
        return b""
    tenant_hash = hashlib.sha256(str(client_id or "default").encode("utf-8")).hexdigest()
    target = f"codex/customer-reply-result/v1/{tenant_hash}"
    encoded = str(secure_credentials.read_scoped_secret(target) or "").strip()
    if encoded:
        try:
            key = base64.b64decode(encoded, validate=True)
            if len(key) == 32:
                return key
        except Exception:
            pass
        if not create:
            return b""
    if not create:
        return b""
    key = secrets.token_bytes(32)
    try:
        if secure_credentials.write_scoped_secret(target, base64.b64encode(key).decode("ascii")):
            return key
    except Exception:
        return b""
    return b""


def _customer_reply_sealed_aad(
    client_id: str,
    job_id: str,
    proposal_hash: str,
    expires_at: int,
) -> bytes:
    return json.dumps(
        [
            _CUSTOMER_REPLY_SEALED_RESULT_VERSION,
            str(client_id or "default"),
            _safe_id(job_id, ""),
            str(proposal_hash or ""),
            int(expires_at),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _customer_reply_sealable_result(payload: dict[str, Any]) -> dict[str, Any]:
    if (
        str(payload.get("status") or "") != "completed"
        or str(payload.get("agent_state") or "") != "aguardando_aprovacao"
    ):
        return {}
    source = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    answer = source.get("resposta")
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 20_000:
        return {}
    if source.get("requires_approval") is False or source.get("publish_attempted") is True:
        return {}
    if source.get("approved") is True:
        return {}
    result = {
        key: source.get(key)
        for key in _CUSTOMER_REPLY_SEALED_RESULT_FIELDS
        if key in source
    }
    result["resposta"] = answer
    warnings = _customer_reply_safe_warnings(source.get("warnings"))
    if warnings:
        result["warnings"] = warnings
    context = source.get("contexto") if isinstance(source.get("contexto"), dict) else {}
    sealed_context = {
        key: context.get(key)
        for key in _CUSTOMER_REPLY_SEALED_CONTEXT_FIELDS
        if key in context and isinstance(context.get(key), (str, bool, int, float))
    }
    if sealed_context:
        result["contexto"] = sealed_context
    return result


def _customer_reply_seal_result(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = _customer_reply_sealable_result(payload)
    if not result:
        return {}
    client_id = _customer_reply_scope_id(db_path)
    job_id = _safe_id(payload.get("job_id"), "")
    proposal_hash = str(result.get("proposal_hash") or payload.get("proposal_hash") or "")
    if not job_id or not proposal_hash:
        return {}
    key = _customer_reply_result_key(client_id, create=True)
    if len(key) != 32:
        return {}
    expires_at = int(time.time()) + _CUSTOMER_REPLY_SEALED_RESULT_TTL_SECONDS
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(
        nonce,
        plaintext,
        _customer_reply_sealed_aad(client_id, job_id, proposal_hash, expires_at),
    )
    return {
        "version": _CUSTOMER_REPLY_SEALED_RESULT_VERSION,
        "algorithm": "AES-256-GCM",
        "expires_at": expires_at,
        "proposal_hash": proposal_hash,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def _customer_reply_unseal_result(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    envelope = payload.get(_CUSTOMER_REPLY_SEALED_RESULT_FIELD)
    if not isinstance(envelope, dict):
        return {}
    try:
        if (
            int(envelope.get("version") or 0) != _CUSTOMER_REPLY_SEALED_RESULT_VERSION
            or str(envelope.get("algorithm") or "") != "AES-256-GCM"
        ):
            return {}
        expires_at = int(envelope.get("expires_at") or 0)
        if expires_at <= int(time.time()):
            return {}
        client_id = _customer_reply_scope_id(db_path)
        if _safe_id(payload.get("client_id"), "default") != client_id:
            return {}
        job_id = _safe_id(payload.get("job_id"), "")
        proposal_hash = str(envelope.get("proposal_hash") or "")
        durable_hash = str(payload.get("proposal_hash") or "")
        if not job_id or not proposal_hash or (durable_hash and durable_hash != proposal_hash):
            return {}
        key = _customer_reply_result_key(client_id, create=False)
        if len(key) != 32:
            return {}
        nonce = base64.b64decode(str(envelope.get("nonce") or ""), validate=True)
        ciphertext = base64.b64decode(str(envelope.get("ciphertext") or ""), validate=True)
        if len(nonce) != 12 or not ciphertext:
            return {}
        plaintext = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            _customer_reply_sealed_aad(client_id, job_id, proposal_hash, expires_at),
        )
        result = json.loads(plaintext.decode("utf-8"))
        if not isinstance(result, dict):
            return {}
        if str(result.get("proposal_hash") or "") != proposal_hash:
            return {}
        if not isinstance(result.get("resposta"), str) or not result["resposta"].strip():
            return {}
        return result
    except Exception:
        return {}


def _customer_reply_durable_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {key: value for key, value in source.items() if key in _CUSTOMER_REPLY_DURABLE_FIELDS}
    scope = source.get("scope_verifiers") if isinstance(source.get("scope_verifiers"), dict) else {}
    durable_scope = {
        key: str(value or "")[:160]
        for key, value in scope.items()
        if key in _CUSTOMER_REPLY_SCOPE_ID_FIELDS and str(value or "").strip()
    }
    if durable_scope:
        durable["scope_verifiers"] = durable_scope
    warnings = _customer_reply_safe_warnings(source.get("warnings"))
    if warnings:
        durable["warnings"] = [
            value for value in warnings
        ]
    evidence = source.get("evidence_status") if isinstance(source.get("evidence_status"), list) else []
    if evidence:
        durable["evidence_status"] = [
            {
                key: row.get(key)
                for key in ("id", "intent", "status", "confidence")
                if key in row
            }
            for row in evidence[:12]
            if isinstance(row, dict)
        ]
    result = source.get("result") if isinstance(source.get("result"), dict) else {}
    for key in (
        "proposal_id", "proposal_version", "proposal_hash", "data_sufficient",
        "publish_attempted", "requires_approval", "completed_with_partial",
        "blocked_without_draft", "draft_source",
    ):
        if key in result and key not in durable:
            durable[key] = result.get(key)
    result_warnings = _customer_reply_safe_warnings(result.get("warnings"))
    if result_warnings:
        durable["warnings"] = [
            value for value in result_warnings
        ]
    result_evidence = result.get("evidence_status") if isinstance(result.get("evidence_status"), list) else []
    if result_evidence:
        durable["evidence_status"] = [
            {
                key: row.get(key)
                for key in ("id", "intent", "status", "confidence")
                if key in row
            }
            for row in result_evidence[:12]
            if isinstance(row, dict)
        ]
    steps = source.get("agent_steps") if isinstance(source.get("agent_steps"), list) else []
    if steps:
        durable["agent_steps"] = [
            {
                key: item.get(key)
                for key in ("state", "step", "at")
                if key in item
            }
            for item in steps[-60:]
            if isinstance(item, dict)
        ]
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    if verification:
        durable["verification"] = {
            key: verification.get(key)
            for key in ("status", "confirmed", "verified_at")
            if key in verification
        }
    transient = {key: value for key, value in source.items() if key not in durable}
    return durable, transient


def _customer_reply_plan_durable_payload(payload: Any) -> dict[str, Any]:
    source = dict(payload or {}) if isinstance(payload, dict) else {}
    durable = {
        key: source.get(key)
        for key in (
            "plan_id", "task_id", "conversation_id", "conversation_generation",
            "channel", "agent_state", "current_step", "idempotency_key",
            "created_at", "updated_at",
        )
        if key in source
    }
    durable["required_input"] = []
    durable["guidance_applied"] = []
    durable["steps"] = [
        {
            key: step.get(key)
            for key in ("step_id", "status", "started_at", "completed_at")
            if key in step
        }
        for step in (source.get("steps") or [])
        if isinstance(step, dict)
    ]
    durable["state_history"] = [
        {
            key: state.get(key)
            for key in ("state", "step", "at")
            if key in state
        }
        for state in (source.get("state_history") or [])[-100:]
        if isinstance(state, dict)
    ]
    proposal = source.get("proposal") if isinstance(source.get("proposal"), dict) else {}
    durable["proposal"] = {
        key: proposal.get(key)
        for key in (
            "proposal_id", "version", "proposal_hash", "action_id",
            "channels_allowed", "requires_confirmation",
        )
        if key in proposal
    }
    verification = source.get("verification") if isinstance(source.get("verification"), dict) else {}
    durable["verification"] = {
        key: verification.get(key)
        for key in ("status", "confirmed", "verified_at")
        if key in verification
    }
    return durable


def _customer_reply_transient_put(db_path: str, payload: dict[str, Any], transient: dict[str, Any]) -> None:
    job_id = _safe_id(payload.get("job_id"), "")
    if not job_id:
        return
    key = _customer_reply_cache_key(db_path, job_id)
    status = str(payload.get("status") or "")
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        if status == "cancelled":
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return
        previous = _CUSTOMER_REPLY_TRANSIENT.get(key)
        merged = dict(previous[1]) if previous and previous[0] > time.time() else {}
        for durable_key in ("vehicle_identity", "vehicle_identity_capture_status"):
            if durable_key in payload and durable_key not in transient:
                merged.pop(durable_key, None)
        merged.update(transient)
        if not merged:
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            return
        ttl = (
            _CUSTOMER_REPLY_COMPLETED_TRANSIENT_TTL_SECONDS
            if status == "completed"
            else _CUSTOMER_REPLY_ACTIVE_TRANSIENT_TTL_SECONDS
        )
        _CUSTOMER_REPLY_TRANSIENT[key] = (time.time() + ttl, merged)


def _customer_reply_transient_merge(
    db_path: str,
    payload: Any,
    expected_job_id: str = "",
) -> Any:
    if not isinstance(payload, dict):
        return payload
    public_payload = dict(payload)
    public_payload.pop(_CUSTOMER_REPLY_SEALED_RESULT_FIELD, None)
    if expected_job_id and _safe_id(public_payload.get("job_id"), "") != _safe_id(expected_job_id, ""):
        return public_payload
    key = _customer_reply_cache_key(db_path, str(public_payload.get("job_id") or ""))
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        cached = _CUSTOMER_REPLY_TRANSIENT.get(key)
        if cached and cached[0] <= time.time():
            _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
            cached = None
        if cached:
            return {**public_payload, **cached[1]}
    recovered = _customer_reply_unseal_result(db_path, payload)
    if not recovered:
        return public_payload
    transient = {"result": recovered}
    _customer_reply_transient_put(db_path, public_payload, transient)
    return {**public_payload, **transient}


def _customer_reply_cleanup_rows(conn: sqlite3.Connection) -> list[str]:
    expired = [
        str(row["job_id"] or "")
        for row in conn.execute(
            "SELECT job_id FROM assistant_customer_reply_jobs "
            "WHERE status IN ('completed', 'cancelled') "
            "AND julianday(updated_at) < julianday('now', ?)",
            (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
        ).fetchall()
    ]
    conn.execute(
        "DELETE FROM assistant_customer_reply_jobs "
        "WHERE status IN ('completed', 'cancelled') "
        "AND julianday(updated_at) < julianday('now', ?)",
        (f"-{_CUSTOMER_REPLY_TERMINAL_ROW_TTL_DAYS} days",),
    )
    return expired
