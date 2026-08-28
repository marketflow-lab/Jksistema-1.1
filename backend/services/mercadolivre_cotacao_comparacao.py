"""Ephemeral, tenant-scoped observations for financial quote comparisons.

The collector is deliberately small and fail-closed.  It keeps only sanitized
financial values in process memory, never writes to disk, never performs
network calls and never stores raw tenant/store identifiers.  Restarting the
backend clears both the observations and the process-local HMAC key.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Final


FINANCIAL_COMPARISON_CONTRACT_VERSION: Final = (
    "mercadolivre.financial_quote_comparison.v1"
)
FINANCIAL_COMPARISON_CAPTURE_ENV: Final = (
    "JK_ML_FINANCIAL_COMPARISON_CAPTURE"
)
FINANCIAL_COMPARISON_UI_ENV: Final = "JK_ML_FINANCIAL_COMPARISON_UI"
FINANCIAL_COMPARISON_TTL_SECONDS: Final = 24 * 60 * 60
FINANCIAL_COMPARISON_MAX_PER_SCOPE: Final = 250
FINANCIAL_COMPARISON_MAX_GLOBAL: Final = 2500
FINANCIAL_COMPARISON_QUOTE_RATE_WINDOW_SECONDS: Final = 60
FINANCIAL_COMPARISON_QUOTE_RATE_LIMIT: Final = 10
FINANCIAL_COMPARISON_QUOTE_TENANT_RATE_LIMIT: Final = 20
FINANCIAL_COMPARISON_QUOTE_RATE_MAX_SCOPES: Final = 500

_TRUE_VALUES = frozenset({"1", "true", "yes", "sim", "on"})
_ORIGINS = frozenset({"promocoes", "favoritos"})
_CLASSIFICATIONS = (
    "match",
    "rounding_difference",
    "exactness_difference",
    "value_difference",
    "shadow_incomplete",
    "shadow_error",
)
_QUOTE_STATUSES = frozenset({"exact", "incomplete", "invalid"})
_LEGACY_STATUSES = frozenset({"exact", "incomplete"})
_EVIDENCE_STATES = frozenset(
    {
        "confirmed",
        "contextual",
        "estimated",
        "fallback",
        "unavailable",
        "conflict",
    }
)
_INPUT_FIELDS = (
    "effective_price",
    "product_cost",
    "tax_rate",
    "sale_fee",
    "seller_shipping",
)
_RESULT_FIELDS = ("tax_amount", "net_amount", "margin_percent")
_DELTA_FIELDS = ("tax_amount", "net_amount", "margin_percent")

_SCOPE_SECRET = secrets.token_bytes(32)
_OBSERVATIONS_LOCK = threading.RLock()
_OBSERVATIONS: list[dict[str, Any]] = []
_STORE_QUOTE_ATTEMPTS: dict[str, list[float]] = {}
_TENANT_QUOTE_ATTEMPTS: dict[str, list[float]] = {}


def _env_enabled(name: str) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    return value in _TRUE_VALUES


def captura_comparacao_habilitada() -> bool:
    """Return whether sanitized in-memory capture is explicitly enabled."""

    return _env_enabled(FINANCIAL_COMPARISON_CAPTURE_ENV)


def interface_comparacao_habilitada() -> bool:
    """Return whether the isolated comparison router should be registered."""

    return _env_enabled(FINANCIAL_COMPARISON_UI_ENV)


def _normalizar_escopo(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("financial comparison scope must be text")
    text = value.strip()
    if not text or text != value or len(text) > 180 or "\x00" in text:
        raise ValueError("invalid financial comparison scope")
    return text


def _scope_token(client_id: Any, loja: Any) -> str:
    tenant = _normalizar_escopo(client_id)
    store = _normalizar_escopo(loja)
    material = json.dumps(
        ["mercadolivre.financial_quote_comparison.scope.v1", tenant, store],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_SCOPE_SECRET, material, hashlib.sha256).hexdigest()


def _decimal_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TypeError("boolean is not a financial value")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid financial value") from exc
    if not decimal_value.is_finite():
        raise ValueError("non-finite financial value")
    normalized = decimal_value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _status(value: Any, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError("invalid comparison status")
    return normalized


def _sanitize_inputs(raw: Any) -> dict[str, str | None]:
    data = raw if isinstance(raw, dict) else {}
    return {field: _decimal_text(data.get(field)) for field in _INPUT_FIELDS}


def _sanitize_result(
    raw: Any,
    *,
    allowed_statuses: frozenset[str],
    include_decision: bool,
) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {
        "status": _status(data.get("status"), allowed_statuses),
    }
    if include_decision:
        decision_eligible = data.get("decision_eligible")
        if not isinstance(decision_eligible, bool):
            raise TypeError("decision_eligible must be boolean")
        result["decision_eligible"] = decision_eligible
    for field in _RESULT_FIELDS:
        result[field] = _decimal_text(data.get(field))
    return result


def _sanitize_evidence(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    for field in ("sale_fee_state", "seller_shipping_state"):
        state = str(data.get(field) or "").strip().lower()
        if state not in _EVIDENCE_STATES:
            raise ValueError("invalid evidence state")
        result[field] = state
    for field in ("sale_fee_source_present", "seller_shipping_source_present"):
        value = data.get(field)
        if not isinstance(value, bool):
            raise TypeError("source presence must be boolean")
        result[field] = value
    return result


def _sanitize_delta(raw: Any) -> dict[str, str | None]:
    data = raw if isinstance(raw, dict) else {}
    return {field: _decimal_text(data.get(field)) for field in _DELTA_FIELDS}


def _sanitize_observation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError("comparison observation must be a mapping")
    origin = str(raw.get("origin") or "").strip().lower()
    if origin not in _ORIGINS:
        raise ValueError("invalid comparison origin")
    classification = str(raw.get("classification") or "").strip().lower()
    if classification not in _CLASSIFICATIONS:
        raise ValueError("invalid comparison classification")
    return {
        "origin": origin,
        "classification": classification,
        "inputs": _sanitize_inputs(raw.get("inputs")),
        "evidence": _sanitize_evidence(raw.get("evidence")),
        "legacy": _sanitize_result(
            raw.get("legacy"),
            allowed_statuses=_LEGACY_STATUSES,
            include_decision=False,
        ),
        "quote": _sanitize_result(
            raw.get("quote"),
            allowed_statuses=_QUOTE_STATUSES,
            include_decision=True,
        ),
        "delta": _sanitize_delta(raw.get("delta")),
    }


def _iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _prune_locked(now: float) -> None:
    cutoff = now - FINANCIAL_COMPARISON_TTL_SECONDS
    if _OBSERVATIONS:
        _OBSERVATIONS[:] = [
            record
            for record in _OBSERVATIONS
            if float(record.get("_epoch") or 0.0) >= cutoff
        ]
    if len(_OBSERVATIONS) > FINANCIAL_COMPARISON_MAX_GLOBAL:
        del _OBSERVATIONS[: len(_OBSERVATIONS) - FINANCIAL_COMPARISON_MAX_GLOBAL]


def _limit_scope_locked(scope: str) -> None:
    matching_indexes = [
        index
        for index, record in enumerate(_OBSERVATIONS)
        if record.get("_scope") == scope
    ]
    excess = len(matching_indexes) - FINANCIAL_COMPARISON_MAX_PER_SCOPE
    if excess <= 0:
        return
    for index in reversed(matching_indexes[:excess]):
        del _OBSERVATIONS[index]


def capturar_observacao_comparacao(
    *,
    client_id: Any,
    loja: Any,
    observacao: Any,
    now: float | None = None,
) -> bool:
    """Store one sanitized observation when capture is enabled.

    The public boundary never propagates failures to a production caller.
    """

    if not captura_comparacao_habilitada():
        return False
    try:
        scope = _scope_token(client_id, loja)
        sanitized = _sanitize_observation(observacao)
        epoch = float(time.time() if now is None else now)
        if not epoch > 0:
            raise ValueError("invalid observation time")
        record = {
            "_scope": scope,
            "_epoch": epoch,
            "captured_at": _iso_utc(epoch),
            **sanitized,
        }
        with _OBSERVATIONS_LOCK:
            _prune_locked(epoch)
            _OBSERVATIONS.append(record)
            _limit_scope_locked(scope)
            _prune_locked(epoch)
        return True
    except Exception:
        return False


def permitir_recotacao_readonly(
    *,
    client_id: Any,
    loja: Any,
    now: float | None = None,
) -> bool:
    """Consume one bounded, process-local allowance for an explicit re-quote."""

    try:
        scope = _scope_token(client_id, loja)
        epoch = float(time.time() if now is None else now)
        if not epoch > 0:
            raise ValueError("invalid quote time")
        cutoff = epoch - FINANCIAL_COMPARISON_QUOTE_RATE_WINDOW_SECONDS
        with _OBSERVATIONS_LOCK:
            for key in list(_STORE_QUOTE_ATTEMPTS):
                recent = [
                    stamp
                    for stamp in _STORE_QUOTE_ATTEMPTS[key]
                    if stamp >= cutoff
                ]
                if recent:
                    _STORE_QUOTE_ATTEMPTS[key] = recent
                else:
                    del _STORE_QUOTE_ATTEMPTS[key]
            attempts = _STORE_QUOTE_ATTEMPTS.get(scope, [])
            if len(attempts) >= FINANCIAL_COMPARISON_QUOTE_RATE_LIMIT:
                return False
            if (
                scope not in _STORE_QUOTE_ATTEMPTS
                and len(_STORE_QUOTE_ATTEMPTS)
                >= FINANCIAL_COMPARISON_QUOTE_RATE_MAX_SCOPES
            ):
                oldest_scope = min(
                    _STORE_QUOTE_ATTEMPTS,
                    key=lambda key: _STORE_QUOTE_ATTEMPTS[key][-1],
                )
                del _STORE_QUOTE_ATTEMPTS[oldest_scope]
            _STORE_QUOTE_ATTEMPTS.setdefault(scope, []).append(epoch)
        return True
    except Exception:
        return False


def permitir_recotacao_readonly_tenant(
    *,
    client_id: Any,
    now: float | None = None,
) -> bool:
    """Apply a tenant-wide ceiling before any store/configuration lookup."""

    try:
        tenant = _normalizar_escopo(client_id)
        material = json.dumps(
            ["mercadolivre.financial_quote_comparison.rate.tenant.v1", tenant],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        scope = hmac.new(_SCOPE_SECRET, material, hashlib.sha256).hexdigest()
        epoch = float(time.time() if now is None else now)
        if not epoch > 0:
            raise ValueError("invalid quote time")
        cutoff = epoch - FINANCIAL_COMPARISON_QUOTE_RATE_WINDOW_SECONDS
        with _OBSERVATIONS_LOCK:
            attempts = [
                stamp
                for stamp in _TENANT_QUOTE_ATTEMPTS.get(scope, [])
                if stamp >= cutoff
            ]
            if len(attempts) >= FINANCIAL_COMPARISON_QUOTE_TENANT_RATE_LIMIT:
                _TENANT_QUOTE_ATTEMPTS[scope] = attempts
                return False
            if (
                scope not in _TENANT_QUOTE_ATTEMPTS
                and len(_TENANT_QUOTE_ATTEMPTS)
                >= FINANCIAL_COMPARISON_QUOTE_RATE_MAX_SCOPES
            ):
                oldest_scope = min(
                    _TENANT_QUOTE_ATTEMPTS,
                    key=lambda key: _TENANT_QUOTE_ATTEMPTS[key][-1],
                )
                del _TENANT_QUOTE_ATTEMPTS[oldest_scope]
            _TENANT_QUOTE_ATTEMPTS[scope] = [*attempts, epoch]
        return True
    except Exception:
        return False


def resumo_comparacao(
    *,
    client_id: Any,
    loja: Any,
    limit: int = 100,
    now: float | None = None,
) -> dict[str, Any]:
    """Return a detached, sanitized summary for exactly one tenant/store."""

    scope = _scope_token(client_id, loja)
    epoch = float(time.time() if now is None else now)
    safe_limit = min(max(int(limit), 1), 250)
    with _OBSERVATIONS_LOCK:
        _prune_locked(epoch)
        scoped = [
            record for record in _OBSERVATIONS if record.get("_scope") == scope
        ]
        detached = []
        for record in reversed(scoped[-safe_limit:]):
            detached.append(copy.deepcopy({
                key: value
                for key, value in record.items()
                if key not in {"_scope", "_epoch"}
            }))

    classifications = {name: 0 for name in _CLASSIFICATIONS}
    origins = {name: 0 for name in sorted(_ORIGINS)}
    for record in scoped:
        classifications[record["classification"]] += 1
        origins[record["origin"]] += 1
    return {
        "contract_version": FINANCIAL_COMPARISON_CONTRACT_VERSION,
        "generated_at": _iso_utc(epoch),
        "window_seconds": FINANCIAL_COMPARISON_TTL_SECONDS,
        "total": len(scoped),
        "classifications": classifications,
        "origins": origins,
        "samples": detached,
    }


def reset_observacoes_comparacao() -> None:
    """Clear process-local observations, primarily for isolated tests."""

    with _OBSERVATIONS_LOCK:
        _OBSERVATIONS.clear()
        _STORE_QUOTE_ATTEMPTS.clear()
        _TENANT_QUOTE_ATTEMPTS.clear()


__all__ = [
    "FINANCIAL_COMPARISON_CAPTURE_ENV",
    "FINANCIAL_COMPARISON_CONTRACT_VERSION",
    "FINANCIAL_COMPARISON_MAX_GLOBAL",
    "FINANCIAL_COMPARISON_MAX_PER_SCOPE",
    "FINANCIAL_COMPARISON_QUOTE_RATE_LIMIT",
    "FINANCIAL_COMPARISON_QUOTE_RATE_MAX_SCOPES",
    "FINANCIAL_COMPARISON_QUOTE_TENANT_RATE_LIMIT",
    "FINANCIAL_COMPARISON_QUOTE_RATE_WINDOW_SECONDS",
    "FINANCIAL_COMPARISON_TTL_SECONDS",
    "FINANCIAL_COMPARISON_UI_ENV",
    "captura_comparacao_habilitada",
    "capturar_observacao_comparacao",
    "interface_comparacao_habilitada",
    "permitir_recotacao_readonly",
    "permitir_recotacao_readonly_tenant",
    "reset_observacoes_comparacao",
    "resumo_comparacao",
]
