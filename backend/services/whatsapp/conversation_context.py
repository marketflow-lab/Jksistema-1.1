"""Structured, tenant-scoped conversation references for Black Jhon Codex."""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from backend.services.whatsapp import intent as whatsapp_intent


RESOLVED_CONTEXT_TTL_SECONDS = 30 * 24 * 60 * 60
RESOLVED_CONTEXT_SCHEMA_VERSION = "1.0"
CONTEXT_FIELDS = ("store", "sku", "mlb", "period")
MUTABLE_FIELDS = ("store", "store_mode", "sku", "mlb", "period", "clear_fields")
CONTEXT_OPERATION_FIELDS = ("store", "store_mode", "sku", "mlb", "period")
TURN_MEMORY_SCHEMA_VERSION = "2.0"
TURN_MEMORY_TTL_SECONDS = 7 * 24 * 60 * 60
TURN_MEMORY_MAX_ITEMS = 16
TURN_MEMORY_REDACTED = "[conteudo sensivel removido da memoria]"


_TURN_DLP_FALLBACKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("secret", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{4,}", re.I)),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("phone", re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?[\s.-]*)?9?\d{4}[\s.-]?\d{4}(?!\d)")),
    ("cpf_cnpj", re.compile(r"(?<!\d)(?:\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-.\s]?\d{2}|\d{2}[.\s-]?\d{3}[.\s-]?\d{3}[/\s-]?\d{4}[-.\s]?\d{2})(?!\d)")),
)


def _turn_memory_ttl_seconds() -> int:
    """Return a bounded retention window controlled by the local server."""

    import os

    try:
        configured = int(os.getenv("JK_WHATSAPP_TURN_MEMORY_TTL_SECONDS") or TURN_MEMORY_TTL_SECONDS)
    except (TypeError, ValueError):
        configured = TURN_MEMORY_TTL_SECONDS
    return max(60 * 60, min(configured, 30 * 24 * 60 * 60))


def sanitize_turn_text(value: Any) -> tuple[str, list[str]]:
    """Sanitize one conversational turn without retaining DLP matches."""

    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:1200]
    if not text:
        return "", []
    categories: set[str] = set()
    try:
        from backend.modules.context_hub import dlp as context_hub_dlp

        for finding in context_hub_dlp.scan_dlp(text, source_ref="whatsapp_turn_memory"):
            if isinstance(finding, dict) and str(finding.get("category") or "").strip():
                categories.add(str(finding.get("category") or "").strip()[:40])
    except Exception:
        # The fallback is deliberately conservative and category-only.  A DLP
        # subsystem failure must not make durable dialogue memory less safe.
        categories.update(category for category, pattern in _TURN_DLP_FALLBACKS if pattern.search(text))
    if categories:
        return TURN_MEMORY_REDACTED, sorted(categories)
    return text, []


def sanitize_turns(
    value: Any,
    *,
    now_epoch: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Migrate, sanitize and expire the bounded durable dialogue window."""

    now_value = float(time.time() if now_epoch is None else now_epoch)
    ttl = _turn_memory_ttl_seconds()
    output: list[dict[str, Any]] = []
    for raw in list(value or [])[-TURN_MEMORY_MAX_ITEMS:]:
        if not isinstance(raw, dict):
            continue
        try:
            created_at = float(raw.get("created_at_epoch") or now_value)
        except (TypeError, ValueError):
            created_at = now_value
        try:
            expires_at = float(raw.get("expires_at_epoch") or (created_at + ttl))
        except (TypeError, ValueError):
            expires_at = created_at + ttl
        if expires_at <= now_value:
            continue
        text, categories = sanitize_turn_text(raw.get("text"))
        existing_categories = {
            str(item or "").strip()[:40]
            for item in list(raw.get("dlp_categories") or [])
            if str(item or "").strip()
        }
        categories = sorted(set(categories).union(existing_categories))
        if not text:
            continue
        output.append(
            {
                "schema_version": TURN_MEMORY_SCHEMA_VERSION,
                "role": "assistant" if str(raw.get("role") or "").strip().lower() == "assistant" else "user",
                "text": text,
                "event_type": str(raw.get("event_type") or "")[:40],
                "at": str(raw.get("at") or "")[:40],
                "created_at_epoch": created_at,
                "expires_at_epoch": min(expires_at, created_at + 30 * 24 * 60 * 60),
                "dlp_blocked": bool(categories) or raw.get("dlp_blocked") is True,
                "dlp_categories": categories,
            }
        )
    return output[-TURN_MEMORY_MAX_ITEMS:]


def append_turn(
    record: dict[str, Any],
    *,
    role: str,
    text: Any,
    event_type: str = "",
    at: str = "",
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    now_value = float(time.time() if now_epoch is None else now_epoch)
    content, categories = sanitize_turn_text(text)
    if not content:
        return record
    turns = sanitize_turns(record.get("recent_turns"), now_epoch=now_value)
    normalized_role = "assistant" if str(role or "").strip().lower() == "assistant" else "user"
    if turns and turns[-1]["role"] == normalized_role and turns[-1]["text"] == content:
        record["recent_turns"] = turns
        return record
    turns.append(
        {
            "schema_version": TURN_MEMORY_SCHEMA_VERSION,
            "role": normalized_role,
            "text": content,
            "event_type": str(event_type or "")[:40],
            "at": str(at or "")[:40],
            "created_at_epoch": now_value,
            "expires_at_epoch": now_value + _turn_memory_ttl_seconds(),
            "dlp_blocked": bool(categories),
            "dlp_categories": categories,
        }
    )
    record["recent_turns"] = turns[-TURN_MEMORY_MAX_ITEMS:]
    record["turn_memory_expires_at_epoch"] = max(item["expires_at_epoch"] for item in record["recent_turns"])
    return record


def recent_turns(
    record: dict[str, Any],
    *,
    current_user_message: str = "",
    now_epoch: Optional[float] = None,
) -> list[dict[str, str]]:
    turns = sanitize_turns(record.get("recent_turns"), now_epoch=now_epoch)
    record["recent_turns"] = turns
    record["turn_memory_expires_at_epoch"] = max(
        (float(item.get("expires_at_epoch") or 0) for item in turns), default=0.0
    )
    current, _categories = sanitize_turn_text(current_user_message)
    result = [{"role": item["role"], "text": str(item["text"])[:900]} for item in turns[-12:]]
    if result and current and result[-1]["role"] == "user" and result[-1]["text"] == current[:900]:
        result.pop()
    return result[-10:]


def expire_turn_memory(record: dict[str, Any], *, now_epoch: Optional[float] = None) -> dict[str, Any]:
    turns = sanitize_turns(record.get("recent_turns"), now_epoch=now_epoch)
    record["recent_turns"] = turns
    record["turn_memory_expires_at_epoch"] = max(
        (float(item.get("expires_at_epoch") or 0) for item in turns), default=0.0
    )
    return record


def snapshot(record: dict[str, Any], *, now_epoch: Optional[float] = None) -> dict[str, Any]:
    raw = record.get("resolved_context") if isinstance(record.get("resolved_context"), dict) else {}
    now_value = float(time.time() if now_epoch is None else now_epoch)
    expires_at = float(raw.get("expires_at_epoch") or 0)
    if expires_at and expires_at <= now_value:
        record.pop("resolved_context", None)
        raw = {}
    store_mode = str(raw.get("store_mode") or "none").strip().lower()
    if store_mode not in {"none", "single", "all"}:
        store_mode = "none"
    store = str(raw.get("store") or "").strip()[:200] if store_mode == "single" else ""
    sku = str(raw.get("sku") or "").strip()[:100]
    mlb = re.sub(r"[^A-Z0-9]", "", str(raw.get("mlb") or "").strip().upper())[:60]
    if not re.fullmatch(r"MLB\d{6,}", mlb):
        mlb = ""
    period = str(raw.get("period") or "").strip()[:160]
    confirmed_fields = [
        field
        for field, value in (
            ("store", store or ("all" if store_mode == "all" else "")),
            ("sku", sku),
            ("mlb", mlb),
            ("period", period),
        )
        if value
    ]
    sources = raw.get("field_sources") if isinstance(raw.get("field_sources"), dict) else {}
    return {
        "schema_version": RESOLVED_CONTEXT_SCHEMA_VERSION,
        "store_mode": store_mode,
        "store": store,
        "sku": sku,
        "mlb": mlb,
        "period": period,
        "confirmed_fields": confirmed_fields,
        "field_sources": {
            field: str(sources.get(field) or "")[:40]
            for field in CONTEXT_FIELDS
            if field in confirmed_fields
        },
        "revision": max(0, int(raw.get("revision") or 0)),
        "updated_at_epoch": float(raw.get("updated_at_epoch") or 0),
        "expires_at_epoch": expires_at if raw else 0.0,
    }


def _provided_fields(value: dict[str, Any]) -> set[str]:
    if "provided_fields" in value:
        return {
            str(item or "").strip().lower()
            for item in list(value.get("provided_fields") or [])
            if str(item or "").strip().lower() in MUTABLE_FIELDS
        }
    return {key for key in MUTABLE_FIELDS if key in value}


def normalize_context_operations(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in list(value or [])[:10]:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip().lower()
        operation = str(item.get("operation") or "").strip().lower()
        if field not in CONTEXT_OPERATION_FIELDS or operation not in {"keep", "set", "clear"} or field in seen:
            continue
        content = str(item.get("value") or "").strip()[:200]
        if operation == "set" and not content:
            continue
        source = str(item.get("source") or "").strip().lower()
        confidence = str(item.get("confidence") or "").strip().lower()
        result.append(
            {
                "field": field,
                "operation": operation,
                "value": content if operation == "set" else "",
                "source": source if source in {"current_turn", "conversation_memory", "quoted_context"} else (
                    "conversation_memory" if operation == "keep" else "current_turn"
                ),
                "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
            }
        )
        seen.add(field)
    return result


def apply_resolved_context(
    record: dict[str, Any],
    resolved: Any,
    *,
    authorized_stores: Optional[list[str]] = None,
    source: str,
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    current = snapshot(record, now_epoch=now_epoch)
    value = resolved if isinstance(resolved, dict) else {}
    provided = _provided_fields(value)
    clear_fields = {
        str(item or "").strip().lower()
        for item in list(value.get("clear_fields") or [])[:5]
        if str(item or "").strip().lower() in CONTEXT_FIELDS
    }
    updated = dict(current)
    sources = dict(current.get("field_sources") or {})
    changed = False
    touched = False

    for field in clear_fields:
        if field == "store":
            changed = changed or bool(updated.get("store")) or updated.get("store_mode") != "none"
            updated.update({"store": "", "store_mode": "none"})
        elif updated.get(field):
            updated[field] = ""
            changed = True
        sources.pop(field, None)

    requested_mode = str(value.get("store_mode") or "").strip().lower()
    if "store_mode" in provided and requested_mode == "all":
        touched = True
        changed = changed or updated.get("store_mode") != "all" or bool(updated.get("store"))
        updated.update({"store": "", "store_mode": "all"})
        sources["store"] = str(source or "codex_conversation")[:40]

    requested_store = str(value.get("store") or "").strip()[:200]
    if "store" in provided and requested_store:
        matches = whatsapp_intent.exact_store_matches(requested_store, list(authorized_stores or []))
        if len(matches) == 1:
            touched = True
            canonical_store = matches[0]
            changed = changed or updated.get("store") != canonical_store or updated.get("store_mode") != "single"
            updated.update({"store": canonical_store, "store_mode": "single"})
            sources["store"] = str(source or "codex_conversation")[:40]
        else:
            changed = changed or bool(updated.get("store")) or updated.get("store_mode") == "single"
            updated.update({"store": "", "store_mode": "none"})
            sources.pop("store", None)

    requested_sku = str(value.get("sku") or "").strip()[:100]
    if "sku" in provided and requested_sku and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", requested_sku):
        touched = True
        if updated.get("sku") != requested_sku:
            changed = True
            if "mlb" not in provided or not re.fullmatch(
                r"MLB\d{6,}", re.sub(r"[^A-Z0-9]", "", str(value.get("mlb") or "").upper())
            ):
                updated["mlb"] = ""
                sources.pop("mlb", None)
        updated["sku"] = requested_sku
        sources["sku"] = str(source or "codex_conversation")[:40]

    requested_mlb = re.sub(r"[^A-Z0-9]", "", str(value.get("mlb") or "").strip().upper())[:60]
    if "mlb" in provided and re.fullmatch(r"MLB\d{6,}", requested_mlb):
        touched = True
        if updated.get("mlb") != requested_mlb:
            changed = True
            if "sku" not in provided or not str(value.get("sku") or "").strip():
                updated["sku"] = ""
                sources.pop("sku", None)
        updated["mlb"] = requested_mlb
        sources["mlb"] = str(source or "codex_conversation")[:40]

    requested_period = str(value.get("period") or "").strip()[:160]
    if "period" in provided and requested_period:
        touched = True
        changed = changed or updated.get("period") != requested_period
        updated["period"] = requested_period
        sources["period"] = str(source or "codex_conversation")[:40]

    if not changed and not touched:
        return current
    now_value = float(time.time() if now_epoch is None else now_epoch)
    updated.update(
        {
            "schema_version": RESOLVED_CONTEXT_SCHEMA_VERSION,
            "field_sources": sources,
            "revision": max(0, int(current.get("revision") or 0)) + (1 if changed else 0),
            "updated_at_epoch": now_value,
            "expires_at_epoch": now_value + RESOLVED_CONTEXT_TTL_SECONDS,
        }
    )
    record["resolved_context"] = updated
    return snapshot(record, now_epoch=now_value)


def apply_context_operations(
    record: dict[str, Any],
    operations: Any,
    *,
    authorized_stores: Optional[list[str]] = None,
    source: str,
    now_epoch: Optional[float] = None,
) -> dict[str, Any]:
    """Materialize agent-owned operations through the existing safety checks."""

    normalized = normalize_context_operations(operations)
    if not normalized:
        return snapshot(record, now_epoch=now_epoch)
    resolved: dict[str, Any] = {"provided_fields": [], "clear_fields": []}
    for item in normalized:
        field = item["field"]
        operation = item["operation"]
        if operation == "keep":
            continue
        if operation == "clear":
            clear_field = "store" if field == "store_mode" else field
            if clear_field not in resolved["clear_fields"]:
                resolved["clear_fields"].append(clear_field)
            continue
        resolved[field] = item["value"]
        resolved["provided_fields"].append(field)
    return apply_resolved_context(
        record,
        resolved,
        authorized_stores=authorized_stores,
        source=source,
        now_epoch=now_epoch,
    )


def effective_context(operations: Any, memory: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical references selected for this turn and their revision."""

    normalized = normalize_context_operations(operations)
    selected: set[str] = set()
    for item in normalized:
        field = item["field"]
        if item["operation"] == "keep":
            selected.add(field)
            continue
        if item["operation"] != "set":
            continue
        expected = item["value"]
        if field == "store" and str(memory.get("store_mode") or "") == "single" and memory.get("store"):
            selected.add(field)
        elif field == "store_mode" and str(memory.get("store_mode") or "none") == expected:
            selected.add(field)
        elif field == "sku" and str(memory.get("sku") or "") == expected:
            selected.add(field)
        elif field == "mlb" and str(memory.get("mlb") or "") == re.sub(r"[^A-Z0-9]", "", expected.upper()):
            selected.add(field)
        elif field == "period" and str(memory.get("period") or "") == expected:
            selected.add(field)
    if "store" in selected or "store_mode" in selected:
        selected.update({"store", "store_mode"})
    store_mode = str(memory.get("store_mode") or "none") if "store_mode" in selected else "none"
    store = str(memory.get("store") or "") if store_mode == "single" and "store" in selected else ""
    result = {
        "schema_version": RESOLVED_CONTEXT_SCHEMA_VERSION,
        "store_mode": store_mode,
        "store": store,
        "sku": str(memory.get("sku") or "") if "sku" in selected else "",
        "mlb": str(memory.get("mlb") or "") if "mlb" in selected else "",
        "period": str(memory.get("period") or "") if "period" in selected else "",
        "applied_fields": sorted(selected),
        "revision": max(0, int(memory.get("revision") or 0)),
        "context_operations": normalized,
    }
    sources = memory.get("field_sources") if isinstance(memory.get("field_sources"), dict) else {}
    result["field_sources"] = {
        field: str(sources.get(field) or "")[:40]
        for field in CONTEXT_FIELDS
        if field in selected and str(sources.get(field) or "")
    }
    result["confirmed_fields"] = [
        field
        for field, content in (
            ("store", result["store"] or ("all" if result["store_mode"] == "all" else "")),
            ("sku", result["sku"]),
            ("mlb", result["mlb"]),
            ("period", result["period"]),
        )
        if content
    ]
    return result


def request_context(resolved: Any, memory: dict[str, Any]) -> dict[str, Any]:
    """Return only references the Codex conversation agent applied now."""

    value = resolved if isinstance(resolved, dict) else {}
    if isinstance(value.get("context_operations"), list):
        return effective_context(value.get("context_operations"), memory)
    applied = {
        str(item or "").strip().lower()
        for item in list(value.get("applied_fields") or value.get("provided_fields") or [])[:5]
        if str(item or "").strip().lower() in {"store", "store_mode", "sku", "mlb", "period"}
    }
    if not applied and "applied_fields" not in value and "provided_fields" not in value:
        applied = {
            field for field in ("store", "sku", "mlb", "period") if str(value.get(field) or "").strip()
        }
        if str(value.get("store_mode") or "none").strip().lower() == "all":
            applied.add("store_mode")
    store_applied = bool({"store", "store_mode"} & applied)
    store_mode = str(memory.get("store_mode") or "none") if store_applied else "none"
    store = str(memory.get("store") or "") if store_applied and store_mode == "single" else ""
    result = {
        "schema_version": RESOLVED_CONTEXT_SCHEMA_VERSION,
        "store_mode": store_mode,
        "store": store,
        "sku": str(memory.get("sku") or "") if "sku" in applied else "",
        "mlb": str(memory.get("mlb") or "") if "mlb" in applied else "",
        "period": str(memory.get("period") or "") if "period" in applied else "",
        "applied_fields": sorted(applied),
        "revision": max(0, int(memory.get("revision") or 0)),
        "context_operations": [],
    }
    result["confirmed_fields"] = [
        field
        for field, content in (
            ("store", result["store"] or ("all" if result["store_mode"] == "all" else "")),
            ("sku", result["sku"]),
            ("mlb", result["mlb"]),
            ("period", result["period"]),
        )
        if content
    ]
    return result


def agent_state(
    record: dict[str, Any],
    *,
    authorized_stores: Optional[list[str]],
) -> dict[str, Any]:
    current = snapshot(record)
    if authorized_stores is not None and current.get("store_mode") == "single":
        matches = whatsapp_intent.exact_store_matches(
            str(current.get("store") or ""), list(authorized_stores or [])
        )
        if len(matches) != 1:
            current = apply_resolved_context(
                record,
                {"clear_fields": ["store"]},
                authorized_stores=authorized_stores,
                source="authorization_refresh",
            )
    current["authorized_stores"] = [
        str(item or "").strip()[:200]
        for item in list(authorized_stores or [])
        if str(item or "").strip()
    ]
    return current


def merge_round_pin(resolved: Any, pin: Any) -> dict[str, Any]:
    value = dict(resolved) if isinstance(resolved, dict) else {}
    fixed = pin if isinstance(pin, dict) else {}
    if not fixed or time.time() - float(fixed.get("created_at_epoch") or 0) > 600:
        return value
    value.update({
        "store": str(fixed.get("store") or ""),
        "store_mode": str(fixed.get("store_mode") or "none"),
    })
    applied = set(value.get("applied_fields") or value.get("provided_fields") or [])
    applied.update({"store", "store_mode"})
    value["applied_fields"] = sorted(applied)
    value["provided_fields"] = sorted(applied)
    value["clear_fields"] = [field for field in list(value.get("clear_fields") or []) if field != "store"]
    operations = [
        item
        for item in normalize_context_operations(value.get("context_operations"))
        if item["field"] not in {"store", "store_mode"}
    ]
    store_mode = str(fixed.get("store_mode") or "none")
    operations.append({
        "field": "store_mode", "operation": "set", "value": store_mode,
        "source": "current_turn", "confidence": "high",
    })
    if store_mode == "single" and str(fixed.get("store") or "").strip():
        operations.append({
            "field": "store", "operation": "set", "value": str(fixed.get("store") or ""),
            "source": "current_turn", "confidence": "high",
        })
    value["context_operations"] = operations
    return value


__all__ = [
    "RESOLVED_CONTEXT_SCHEMA_VERSION",
    "RESOLVED_CONTEXT_TTL_SECONDS",
    "TURN_MEMORY_MAX_ITEMS",
    "TURN_MEMORY_REDACTED",
    "TURN_MEMORY_SCHEMA_VERSION",
    "TURN_MEMORY_TTL_SECONDS",
    "append_turn",
    "agent_state",
    "apply_context_operations",
    "apply_resolved_context",
    "effective_context",
    "merge_round_pin",
    "expire_turn_memory",
    "recent_turns",
    "normalize_context_operations",
    "request_context",
    "snapshot",
    "sanitize_turn_text",
    "sanitize_turns",
]
