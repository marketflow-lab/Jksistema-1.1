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


def request_context(resolved: Any, memory: dict[str, Any]) -> dict[str, Any]:
    """Return only references the Codex conversation agent applied now."""

    value = resolved if isinstance(resolved, dict) else {}
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
    return value


__all__ = [
    "RESOLVED_CONTEXT_SCHEMA_VERSION",
    "RESOLVED_CONTEXT_TTL_SECONDS",
    "agent_state",
    "apply_resolved_context",
    "merge_round_pin",
    "request_context",
    "snapshot",
]
