"""Shared, versioned Codex turn-context contracts for JK Sistema surfaces.

This module is intentionally runtime-agnostic.  It owns deterministic context
schemas, conversation identity, provider-thread restart decisions, bounded JSON
serialization and durable-memory hygiene.  Authentication, permissions, tool
execution, persistence and publishing remain responsibilities of callers.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any


CONTRACT_VERSION = "jk.codex-turn-context.v2"
CONVERSATION_DECISION_V1 = "jk.codex.conversation-decision.v1"
CONVERSATION_DECISION_V2 = "jk.codex.conversation-decision.v2"
TURN_CONTEXT_V1 = "jk.codex.turn-context.v1"
TURN_CONTEXT_V2 = "jk.codex.turn-context.v2"
EVIDENCE_ENVELOPE_V1 = "jk.codex.evidence-envelope.v1"
EVIDENCE_ENVELOPE_V2 = "jk.codex.evidence-envelope.v2"
FINAL_RESPONSE_V1 = "jk.codex.final-response.v1"
FINAL_RESPONSE_V2 = "jk.codex.final-response.v2"

DEFAULT_CONTEXT_BUDGET_BYTES = 24 * 1024
DEFAULT_MEMORY_BUDGET_BYTES = 8 * 1024
DEFAULT_THREAD_MAX_AGE_DAYS = 30

_V1_MARKERS = {"", "1", "1.0", "v1", "legacy-v1"}
_CONFIDENCE_VALUES = {"high", "medium", "low", "unknown"}
_EVIDENCE_STATUSES = {"completed", "partial", "missing", "blocked", "failed"}
_THREAD_ACTIONS = {"start", "reuse", "restart"}
_NO_VALUE = object()


def _canonical_json(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
        default=str,
    )


def _json_bytes(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _clean_text(value: Any, limit: int = 12_000) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", str(value or "")).strip()[:limit]


def _clean_list(value: Any, *, max_items: int, item_limit: int) -> tuple[str, ...]:
    values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in list(values)[:max_items]:
        text = _clean_text(item, item_limit)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return tuple(result)


def _json_safe(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
    if _depth > 32:
        return "[depth_limit]"
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    seen = _seen if _seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return "[cycle]"
    if isinstance(value, Mapping):
        seen.add(value_id)
        try:
            return {
                _clean_text(key, 240): _json_safe(item, _depth=_depth + 1, _seen=seen)
                for key, item in value.items()
                if _clean_text(key, 240)
            }
        finally:
            seen.discard(value_id)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        seen.add(value_id)
        try:
            return [_json_safe(item, _depth=_depth + 1, _seen=seen) for item in value]
        finally:
            seen.discard(value_id)
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item, _depth=_depth + 1, _seen=seen) for item in sorted(value, key=str)]
    return _clean_text(value, 4000)


def fingerprint(value: Any, *, namespace: str = "") -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-compatible data."""

    safe = _json_safe(value)
    payload = f"{_clean_text(namespace, 200)}\0{_canonical_json(safe, sort_keys=True)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_source(value: Mapping[str, Any] | None, current: str, legacy: str) -> str:
    raw = (value or {}).get("schema_version", "")
    text = _clean_text(raw, 120).lower()
    if raw == 1 or text in _V1_MARKERS or text.endswith(".v1"):
        return legacy
    if text == current.lower():
        return current
    return _clean_text(raw, 120) or legacy


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_identity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value, 500))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _identity_display(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", _clean_text(value, 500))).strip()


def conversation_key(
    surface: Any,
    client_id: Any,
    store: Any = "",
    user: Any = "",
    subject: Any = "",
) -> str:
    """Build an opaque key isolated by all five required identity dimensions."""

    surface_norm = _normalize_identity(surface)
    client_norm = _normalize_identity(client_id)
    if not surface_norm:
        raise ValueError("conversation_surface_required")
    if not client_norm:
        raise ValueError("conversation_client_required")
    identity = {
        "surface": surface_norm,
        "client_id": client_norm,
        "store": _normalize_identity(store) or "<none>",
        "user": _normalize_identity(user) or "<none>",
        "subject": _normalize_identity(subject) or "<none>",
    }
    digest = fingerprint(identity, namespace="jk.codex.conversation-key.v2")[:32]
    prefix = re.sub(r"[^a-z0-9]+", "-", surface_norm).strip("-")[:24] or "surface"
    return f"ctx2_{prefix}_{digest}"


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path) or "$"


def _normalize_priority_paths(value: Any) -> tuple[tuple[str, ...], ...]:
    values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []
    result: list[tuple[str, ...]] = []
    for item in values:
        if isinstance(item, str):
            parts = tuple(part for part in item.split(".") if part)
        elif isinstance(item, Sequence):
            parts = tuple(str(part) for part in item if str(part))
        else:
            parts = ()
        if parts:
            result.append(parts)
    return tuple(result)


_PRIORITY_BY_KEY = {
    "schema_version": 16,
    "source_schema_version": 13,
    "request": 16,
    "user_message": 16,
    "question": 16,
    "prompt": 16,
    "text": 14,
    "resposta": 14,
    "conversation_key": 14,
    "turn_id": 14,
    "surface": 13,
    "client_id": 13,
    "store": 13,
    "user": 13,
    "subject": 13,
    "records": 14,
    "facts": 14,
    "sources": 14,
    "gaps": 14,
    "missing": 14,
    "evidence": 13,
    "policy": 11,
    "scope": 11,
    "short_memory": 9,
    "durable_memory": 8,
    "history": 5,
    "attachments": 4,
    "tool_results": 3,
    "raw": 1,
    "diagnostics": 1,
}


def _path_priority(path: tuple[str, ...], explicit: tuple[tuple[str, ...], ...]) -> int:
    for index, wanted in enumerate(explicit):
        if path[: len(wanted)] == wanted or wanted[: len(path)] == path:
            return 100 + max(0, len(explicit) - index)
    values = [_PRIORITY_BY_KEY.get(part.casefold(), 6) for part in path]
    return max(values or [6])


def _minimal_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return ""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return ""


def _fit_string(value: str, budget: int) -> str | object:
    if budget < 2:
        return _NO_VALUE
    if _json_bytes(value) <= budget:
        return value
    low, high = 0, len(value)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = value[:middle].rstrip()
        if middle < len(value) and candidate:
            candidate += "…"
        if _json_bytes(candidate) <= budget:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _fit_json_node(
    value: Any,
    budget: int,
    path: tuple[str, ...],
    priorities: tuple[tuple[str, ...], ...],
    omitted: list[str],
    truncated: list[str],
) -> Any:
    if budget <= 0:
        return _NO_VALUE
    if isinstance(value, str):
        fitted = _fit_string(value, budget)
        if fitted is not _NO_VALUE and fitted != value:
            truncated.append(_path_text(path))
        return fitted
    if value is None or isinstance(value, (bool, int, float)):
        if _json_bytes(value) <= budget:
            return value
        if budget >= 4:
            truncated.append(_path_text(path))
            return None
        return _NO_VALUE
    if isinstance(value, list):
        if budget < 2:
            return _NO_VALUE
        if not value:
            return []
        maximum_items = min(len(value), 64, max(1, (budget - 2) // 24))
        if maximum_items < len(value):
            omitted.extend(f"{_path_text(path)}[{index}]" for index in range(maximum_items, len(value)))
            truncated.append(_path_text(path))
        selected = value[:maximum_items]
        comma_bytes = max(0, len(selected) - 1)
        available = max(0, budget - 2 - comma_bytes)
        result: list[Any] = []
        for index, item in enumerate(selected):
            remaining = len(selected) - index
            child_budget = max(2, available // max(1, remaining))
            fitted = _fit_json_node(item, child_budget, (*path, str(index)), priorities, omitted, truncated)
            if fitted is _NO_VALUE:
                omitted.append(f"{_path_text(path)}[{index}]")
                truncated.append(_path_text(path))
                continue
            actual = _json_bytes(fitted)
            available = max(0, available - actual)
            result.append(fitted)
        while result and _json_bytes(result) > budget:
            index = len(result) - 1
            result.pop()
            omitted.append(f"{_path_text(path)}[{index}]")
            truncated.append(_path_text(path))
        return result
    if isinstance(value, Mapping):
        if budget < 2:
            return _NO_VALUE
        if not value:
            return {}
        entries = list(value.items())
        ranked = sorted(
            enumerate(entries),
            key=lambda pair: (-_path_priority((*path, str(pair[1][0])), priorities), pair[0]),
        )
        selected: list[tuple[int, str, Any, int]] = []
        minimum_total = 2
        for original_index, (raw_key, item) in ranked:
            key = str(raw_key)
            minimum = _minimal_json_value(item)
            field_bytes = _json_bytes(key) + 1 + _json_bytes(minimum) + (1 if selected else 0)
            if minimum_total + field_bytes <= budget:
                selected.append((original_index, key, item, _path_priority((*path, key), priorities)))
                minimum_total += field_bytes
            else:
                omitted.append(_path_text((*path, key)))
                truncated.append(_path_text(path))
        if not selected:
            return {}
        key_overhead = 2 + max(0, len(selected) - 1) + sum(_json_bytes(key) + 1 for _, key, _, _ in selected)
        value_budget = max(0, budget - key_overhead)
        minima = [_json_bytes(_minimal_json_value(item)) for _, _, item, _ in selected]
        extra = max(0, value_budget - sum(minima))
        weights = [max(1, priority) for _, _, _, priority in selected]
        total_weight = sum(weights)
        allocations = [minimum + (extra * weight // total_weight) for minimum, weight in zip(minima, weights)]
        unallocated = max(0, value_budget - sum(allocations))
        for index in range(unallocated):
            allocations[index % len(allocations)] += 1
        fitted_by_index: dict[int, tuple[str, Any]] = {}
        for entry, child_budget in zip(selected, allocations):
            original_index, key, item, _priority = entry
            fitted = _fit_json_node(item, child_budget, (*path, key), priorities, omitted, truncated)
            if fitted is _NO_VALUE:
                fitted = _minimal_json_value(item)
                truncated.append(_path_text((*path, key)))
            fitted_by_index[original_index] = (key, fitted)
        result = {key: item for _, (key, item) in sorted(fitted_by_index.items())}
        while result and _json_bytes(result) > budget:
            removable = min(
                result,
                key=lambda key: (_path_priority((*path, key), priorities), -_json_bytes(result[key])),
            )
            result.pop(removable)
            omitted.append(_path_text((*path, removable)))
            truncated.append(_path_text(path))
        return result
    return _fit_json_node(_clean_text(value, 4000), budget, path, priorities, omitted, truncated)


@dataclass(frozen=True)
class CompactionResult:
    value: Any
    json_text: str
    original_bytes: int
    compacted_bytes: int
    budget_bytes: int
    truncated: bool
    omitted_paths: tuple[str, ...]
    truncated_paths: tuple[str, ...]
    fingerprint: str

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "original_bytes": self.original_bytes,
            "compacted_bytes": self.compacted_bytes,
            "budget_bytes": self.budget_bytes,
            "truncated": self.truncated,
            "omitted_count": len(self.omitted_paths),
            "truncated_count": len(self.truncated_paths),
            "fingerprint": self.fingerprint,
        }


def compact_json_structural(
    value: Any,
    *,
    max_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES,
    priority_paths: Sequence[str | Sequence[str]] = (),
) -> CompactionResult:
    """Fit complete JSON values into a byte budget without slicing serialization."""

    budget = int(max_bytes or 0)
    if budget < 2:
        raise ValueError("json_budget_minimum_2")
    safe = _json_safe(value)
    original_bytes = _json_bytes(safe)
    omitted: list[str] = []
    truncated_paths: list[str] = []
    if original_bytes <= budget:
        compacted = safe
    else:
        compacted = _fit_json_node(
            safe,
            budget,
            (),
            _normalize_priority_paths(priority_paths),
            omitted,
            truncated_paths,
        )
        if compacted is _NO_VALUE:
            compacted = {}
    text = _canonical_json(compacted)
    if len(text.encode("utf-8")) > budget:
        compacted, text = {}, "{}"
        omitted.append("$")
        truncated_paths.append("$")
    omitted_unique = tuple(dict.fromkeys(omitted))
    truncated_unique = tuple(dict.fromkeys(truncated_paths))
    return CompactionResult(
        value=compacted,
        json_text=text,
        original_bytes=original_bytes,
        compacted_bytes=len(text.encode("utf-8")),
        budget_bytes=budget,
        truncated=bool(original_bytes > budget or omitted_unique or truncated_unique),
        omitted_paths=omitted_unique,
        truncated_paths=truncated_unique,
        fingerprint=fingerprint(compacted, namespace="jk.codex.compacted-json.v2"),
    )


def bounded_json(
    value: Any,
    *,
    max_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES,
    priority_paths: Sequence[str | Sequence[str]] = (),
) -> str:
    return compact_json_structural(value, max_bytes=max_bytes, priority_paths=priority_paths).json_text


def _ascii_normalized(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


_DURABLE_FORBIDDEN_KEY_TOKENS = {
    "preco", "price", "pricing", "estoque", "stock", "inventory", "saldo",
    "pedido", "order", "orderid", "pack", "packid", "envio", "shipping",
    "shipment", "tracking", "frete", "pagamento", "payment", "paid",
    "reclamacao", "claim", "mediacao", "mediation", "toolresult",
    "toolresults", "raw", "payload", "result", "results", "records",
    "evidence", "facts", "sources", "response", "finalresponse",
}
_DURABLE_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:\b(?:preco|price|pricing|estoque|stock|inventory|saldo|pedido|order|pack|"
    r"envio|shipping|shipment|tracking|frete|pagamento|payment|reclamacao|claim|"
    r"mediacao|mediation)\b|\bR\$\s*\d|\$\s*\d)",
    flags=re.I,
)


def _memory_key_forbidden(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", _ascii_normalized(value))
    return normalized in _DURABLE_FORBIDDEN_KEY_TOKENS


def _memory_text_forbidden(value: Any) -> bool:
    return bool(_DURABLE_FORBIDDEN_TEXT_RE.search(_ascii_normalized(value)))


def _sanitize_memory_node(value: Any, path: tuple[str, ...], removed: list[str]) -> Any:
    if isinstance(value, Mapping):
        textual = " ".join(
            str(value.get(key) or "")
            for key in ("category", "title", "content", "summary", "text", "value")
            if key in value
        )
        if textual and _memory_text_forbidden(textual):
            removed.append(_path_text(path))
            return _NO_VALUE
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _clean_text(raw_key, 160)
            child_path = (*path, key)
            if not key or _memory_key_forbidden(key):
                removed.append(_path_text(child_path))
                continue
            child = _sanitize_memory_node(item, child_path, removed)
            if child is not _NO_VALUE:
                result[key] = child
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for index, item in enumerate(value):
            child = _sanitize_memory_node(item, (*path, str(index)), removed)
            if child is not _NO_VALUE:
                result.append(child)
        return result
    if isinstance(value, str):
        if _memory_text_forbidden(value):
            removed.append(_path_text(path))
            return _NO_VALUE
        return _clean_text(value, 4000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean_text(value, 1000)


@dataclass(frozen=True)
class SanitizedMemory:
    value: Any
    removed_paths: tuple[str, ...]
    fingerprint: str
    compacted: bool
    bytes: int

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "removed_count": len(self.removed_paths),
            "removed_paths": list(self.removed_paths),
            "compacted": self.compacted,
            "bytes": self.bytes,
            "fingerprint": self.fingerprint,
        }


def sanitize_durable_memory(
    value: Any,
    *,
    max_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES,
) -> SanitizedMemory:
    """Remove volatile operational facts and raw results from durable memory."""

    removed: list[str] = []
    sanitized = _sanitize_memory_node(_json_safe(value), (), removed)
    if sanitized is _NO_VALUE:
        sanitized = {}
    compacted = compact_json_structural(
        sanitized,
        max_bytes=max_bytes,
        priority_paths=("preferences", "preferencias", "decisions", "decisoes", "rules", "regras"),
    )
    all_removed = tuple(dict.fromkeys([*removed, *compacted.omitted_paths]))
    return SanitizedMemory(
        value=compacted.value,
        removed_paths=all_removed,
        fingerprint=fingerprint(compacted.value, namespace="jk.codex.durable-memory.v2"),
        compacted=compacted.truncated,
        bytes=compacted.compacted_bytes,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = _clean_text(value, 120)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now(value: Any = None) -> datetime:
    return _parse_datetime(value) or datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConversationDecisionV2:
    schema_version: str = CONVERSATION_DECISION_V2
    source_schema_version: str = CONVERSATION_DECISION_V2
    conversation_key: str = ""
    action: str = "continue"
    intent: str = ""
    response_mode: str = ""
    missing_fields: tuple[str, ...] = ()
    confidence: str = "unknown"
    task: Mapping[str, Any] = field(default_factory=dict)
    resolved_context: Mapping[str, Any] = field(default_factory=dict)
    thread_action: str = "start"
    reuse_thread: bool = False
    restart_required: bool = False
    restart_reasons: tuple[str, ...] = ()
    prompt_fingerprint: str = ""
    schema_fingerprint: str = ""
    scope_fingerprint: str = ""
    age_days: float | None = None
    fingerprint: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "conversation_key": self.conversation_key,
            "action": self.action,
            "intent": self.intent,
            "response_mode": self.response_mode,
            "missing_fields": list(self.missing_fields),
            "confidence": self.confidence,
            "task": _json_safe(self.task),
            "resolved_context": _json_safe(self.resolved_context),
            "thread_action": self.thread_action,
            "reuse_thread": self.reuse_thread,
            "restart_required": self.restart_required,
            "restart_reasons": list(self.restart_reasons),
            "prompt_fingerprint": self.prompt_fingerprint,
            "schema_fingerprint": self.schema_fingerprint,
            "scope_fingerprint": self.scope_fingerprint,
            "age_days": self.age_days,
            "fingerprint": self.fingerprint,
            "diagnostics": _json_safe(self.diagnostics),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ConversationDecisionV2":
        source = _mapping(value)
        source_schema = _schema_source(source, CONVERSATION_DECISION_V2, CONVERSATION_DECISION_V1)
        task = _mapping(source.get("task"))
        if not task and any(source.get(key) not in (None, "") for key in ("job_title", "job_prompt", "requires_web")):
            task = {
                "title": _clean_text(source.get("job_title"), 180),
                "prompt": _clean_text(source.get("job_prompt"), 12_000),
                "requires_web": bool(source.get("requires_web")),
                "reasoning_effort": _clean_text(source.get("reasoning_effort"), 20) or "low",
            }
        restart_reasons = _clean_list(
            source.get("restart_reasons") or ([source.get("restart_reason")] if source.get("restart_reason") else []),
            max_items=8,
            item_limit=120,
        )
        restart_required = bool(source.get("restart_required") or source.get("should_restart") or restart_reasons)
        reuse_thread = bool(source.get("reuse_thread") or source.get("thread_reused")) and not restart_required
        thread_action = _clean_text(source.get("thread_action"), 20)
        if thread_action not in _THREAD_ACTIONS:
            thread_action = "restart" if restart_required else ("reuse" if reuse_thread else "start")
        confidence = _clean_text(source.get("confidence"), 20).lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "unknown"
        payload = {
            "source_schema_version": source_schema,
            "conversation_key": _clean_text(source.get("conversation_key") or source.get("conversation_id"), 160),
            "action": _clean_text(source.get("action") or source.get("decision"), 80) or "continue",
            "intent": _clean_text(source.get("intent"), 120),
            "response_mode": _clean_text(source.get("response_mode"), 80),
            "missing_fields": _clean_list(source.get("missing_fields"), max_items=12, item_limit=200),
            "confidence": confidence,
            "task": task,
            "resolved_context": _mapping(source.get("resolved_context") or source.get("conversation_state")),
            "thread_action": thread_action,
            "reuse_thread": reuse_thread,
            "restart_required": restart_required,
            "restart_reasons": restart_reasons,
            "prompt_fingerprint": _clean_text(source.get("prompt_fingerprint") or source.get("prompt_hash"), 128),
            "schema_fingerprint": _clean_text(source.get("schema_fingerprint") or source.get("schema_hash"), 128),
            "scope_fingerprint": _clean_text(source.get("scope_fingerprint") or source.get("scope_hash"), 128),
            "age_days": float(source.get("age_days")) if isinstance(source.get("age_days"), (int, float)) else None,
            "diagnostics": _mapping(source.get("diagnostics")),
        }
        calculated = fingerprint(payload, namespace=CONVERSATION_DECISION_V2)
        return cls(**payload, fingerprint=_clean_text(source.get("fingerprint"), 128) or calculated)


def decide_conversation(
    previous: Mapping[str, Any] | None,
    *,
    surface: Any,
    client_id: Any,
    store: Any = "",
    user: Any = "",
    subject: Any = "",
    prompt_contract: Any,
    schema_contract: Any,
    scope: Mapping[str, Any] | None = None,
    now: Any = None,
    max_age_days: int = DEFAULT_THREAD_MAX_AGE_DAYS,
) -> ConversationDecisionV2:
    """Choose start/reuse/restart using prompt, schema, scope and age rules."""

    current_time = _now(now)
    key = conversation_key(surface, client_id, store, user, subject)
    scope_payload = {
        "surface": _normalize_identity(surface),
        "client_id": _normalize_identity(client_id),
        "store": _normalize_identity(store),
        "user": _normalize_identity(user),
        "subject": _normalize_identity(subject),
        "scope": _json_safe(scope or {}),
    }
    prompt_fp = fingerprint(prompt_contract, namespace="jk.codex.prompt-contract")
    schema_fp = fingerprint(schema_contract, namespace="jk.codex.schema-contract")
    scope_fp = fingerprint(scope_payload, namespace="jk.codex.scope-contract")
    prior = _mapping(previous)
    prior_thread = _clean_text(prior.get("thread_id") or prior.get("provider_thread_id"), 200)
    reasons: list[str] = []
    age_days: float | None = None
    if prior_thread:
        previous_prompt = _clean_text(prior.get("prompt_fingerprint") or prior.get("prompt_hash"), 128)
        previous_schema = _clean_text(prior.get("schema_fingerprint") or prior.get("schema_hash"), 128)
        previous_scope = _clean_text(prior.get("scope_fingerprint") or prior.get("scope_hash"), 128)
        previous_key = _clean_text(prior.get("conversation_key") or prior.get("conversation_id"), 200)
        if previous_prompt != prompt_fp:
            reasons.append("prompt_contract_changed")
        if previous_schema != schema_fp:
            reasons.append("schema_contract_changed")
        if previous_scope != scope_fp or (previous_key and previous_key != key):
            reasons.append("scope_changed")
        last_used = _parse_datetime(
            prior.get("last_used_at") or prior.get("updated_at") or prior.get("created_at") or prior.get("started_at")
        )
        if last_used is not None:
            age_days = max(0.0, (current_time - last_used).total_seconds() / 86_400.0)
            if age_days >= max(1, int(max_age_days)):
                reasons.append("context_expired_30d")
    if not prior_thread:
        thread_action = "start"
    elif reasons:
        thread_action = "restart"
    else:
        thread_action = "reuse"
    payload = {
        "source_schema_version": _schema_source(prior, CONVERSATION_DECISION_V2, CONVERSATION_DECISION_V1),
        "conversation_key": key,
        "action": _clean_text(prior.get("action"), 80) or "continue",
        "thread_action": thread_action,
        "reuse_thread": thread_action == "reuse",
        "restart_required": thread_action == "restart",
        "restart_reasons": tuple(dict.fromkeys(reasons)),
        "prompt_fingerprint": prompt_fp,
        "schema_fingerprint": schema_fp,
        "scope_fingerprint": scope_fp,
        "age_days": round(age_days, 6) if age_days is not None else None,
        "diagnostics": {
            "evaluated_at": current_time.isoformat().replace("+00:00", "Z"),
            "max_age_days": max(1, int(max_age_days)),
            "previous_thread_present": bool(prior_thread),
            "previous_schema_version": _schema_source(prior, CONVERSATION_DECISION_V2, CONVERSATION_DECISION_V1),
        },
    }
    decision_fp = fingerprint(payload, namespace=CONVERSATION_DECISION_V2)
    return ConversationDecisionV2(**payload, fingerprint=decision_fp)


def _clean_record(value: Any, fallback_field: str = "") -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    field_name = _clean_text(value.get("field") or value.get("name") or value.get("key") or fallback_field, 200)
    if not field_name:
        return None
    raw_value = value.get("value")
    if raw_value is None:
        for alias in ("fact", "snippet", "content", "summary"):
            if value.get(alias) not in (None, "", [], {}):
                raw_value = value.get(alias)
                break
    return {
        "field": field_name,
        "value": _json_safe(raw_value),
        "store": _clean_text(value.get("store") or value.get("loja") or value.get("store_ref"), 200),
        "period": _clean_text(value.get("period") or value.get("periodo"), 160),
        "source": _clean_text(value.get("source") or value.get("reference") or value.get("tool_id"), 1000),
        "authority": _clean_text(value.get("authority") or value.get("truth_class"), 120),
        "fetched_at": _clean_text(value.get("fetched_at") or value.get("generated_at"), 80),
    }


@dataclass(frozen=True)
class EvidenceEnvelopeV2:
    schema_version: str = EVIDENCE_ENVELOPE_V2
    source_schema_version: str = EVIDENCE_ENVELOPE_V2
    status: str = "missing"
    records: tuple[Mapping[str, Any], ...] = ()
    facts: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    confidence: str = "unknown"
    evidence_sufficient: bool = False
    coverage_complete: bool = False
    fetched_at: str = ""
    scope: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "status": self.status,
            "records": [_json_safe(item) for item in self.records],
            "facts": list(self.facts),
            "sources": list(self.sources),
            "gaps": list(self.gaps),
            "confidence": self.confidence,
            "evidence_sufficient": self.evidence_sufficient,
            "coverage_complete": self.coverage_complete,
            "fetched_at": self.fetched_at,
            "scope": _json_safe(self.scope),
            "fingerprint": self.fingerprint,
            "diagnostics": _json_safe(self.diagnostics),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "EvidenceEnvelopeV2":
        source = _mapping(value)
        source_schema = _schema_source(source, EVIDENCE_ENVELOPE_V2, EVIDENCE_ENVELOPE_V1)
        raw_records = source.get("records") or source.get("rows") or source.get("data") or []
        if isinstance(raw_records, Mapping):
            raw_records = [{"field": key, "value": item} for key, item in raw_records.items()]
        records: list[Mapping[str, Any]] = []
        if isinstance(raw_records, Sequence) and not isinstance(raw_records, (str, bytes, bytearray)):
            for item in list(raw_records)[:80]:
                normalized = _clean_record(item)
                if normalized is not None:
                    records.append(normalized)
        facts = _clean_list(source.get("facts") or source.get("verified_facts"), max_items=60, item_limit=2000)
        sources = _clean_list(source.get("sources") or source.get("references"), max_items=60, item_limit=1000)
        gaps = _clean_list(source.get("gaps") or source.get("missing"), max_items=40, item_limit=1000)
        status = _clean_text(source.get("status"), 20).lower()
        if status not in _EVIDENCE_STATUSES:
            status = "completed" if (records or facts) and not gaps else ("partial" if records or facts else "missing")
        confidence = _clean_text(source.get("confidence"), 20).lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "unknown"
        sufficient = bool(source.get("evidence_sufficient", source.get("data_sufficient", False)))
        coverage = bool(source.get("coverage_complete", source.get("data_complete", False)))
        payload = {
            "source_schema_version": source_schema,
            "status": status,
            "records": tuple(records),
            "facts": facts,
            "sources": sources,
            "gaps": gaps,
            "confidence": confidence,
            "evidence_sufficient": sufficient,
            "coverage_complete": coverage,
            "fetched_at": _clean_text(source.get("fetched_at") or source.get("generated_at"), 80),
            "scope": _mapping(source.get("scope")),
            "diagnostics": _mapping(source.get("diagnostics")),
        }
        evidence_fp = fingerprint(payload, namespace=EVIDENCE_ENVELOPE_V2)
        return cls(**payload, fingerprint=_clean_text(source.get("fingerprint"), 128) or evidence_fp)


def normalize_evidence_envelope(value: Mapping[str, Any] | None) -> EvidenceEnvelopeV2:
    return EvidenceEnvelopeV2.from_mapping(value)


@dataclass(frozen=True)
class TurnContextV2:
    schema_version: str = TURN_CONTEXT_V2
    source_schema_version: str = TURN_CONTEXT_V2
    conversation_key: str = ""
    turn_id: str = ""
    surface: str = ""
    client_id: str = ""
    store: str = ""
    user: str = ""
    subject: str = ""
    request: Any = field(default_factory=dict)
    policy: Mapping[str, Any] = field(default_factory=dict)
    scope: Mapping[str, Any] = field(default_factory=dict)
    short_memory: Any = field(default_factory=list)
    durable_memory: Any = field(default_factory=dict)
    evidence: EvidenceEnvelopeV2 = field(default_factory=EvidenceEnvelopeV2)
    created_at: str = ""
    fingerprints: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "conversation_key": self.conversation_key,
            "turn_id": self.turn_id,
            "surface": self.surface,
            "client_id": self.client_id,
            "store": self.store,
            "user": self.user,
            "subject": self.subject,
            "request": _json_safe(self.request),
            "policy": _json_safe(self.policy),
            "scope": _json_safe(self.scope),
            "short_memory": _json_safe(self.short_memory),
            "durable_memory": _json_safe(self.durable_memory),
            "evidence": self.evidence.to_dict(),
            "created_at": self.created_at,
            "fingerprints": _json_safe(self.fingerprints),
            "diagnostics": _json_safe(self.diagnostics),
        }

    def compact(self, max_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES) -> CompactionResult:
        return compact_json_structural(
            self.to_dict(),
            max_bytes=max_bytes,
            priority_paths=(
                "schema_version", "conversation_key", "turn_id", "surface", "client_id",
                "store", "user", "subject", "request", "evidence.records", "evidence.facts",
                "evidence.sources", "evidence.gaps", "policy", "short_memory", "durable_memory",
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "TurnContextV2":
        source = _mapping(value)
        source_schema = _schema_source(source, TURN_CONTEXT_V2, TURN_CONTEXT_V1)
        scope = _mapping(source.get("scope"))
        surface = _identity_display(source.get("surface") or scope.get("surface") or "unknown")
        client_id = _identity_display(source.get("client_id") or source.get("tenant_id") or scope.get("client_id") or "default")
        store = _identity_display(source.get("store") or source.get("loja") or scope.get("store"))
        user = _identity_display(source.get("user") or source.get("username") or scope.get("user"))
        subject = _identity_display(source.get("subject") or source.get("subject_key") or scope.get("subject"))
        key = _clean_text(source.get("conversation_key") or source.get("conversation_id"), 200)
        if not key:
            key = conversation_key(surface, client_id, store, user, subject)
        request = source.get("request")
        if request is None:
            request = source.get("user_message") or source.get("question") or source.get("prompt") or {}
        short_memory = source.get("short_memory")
        if short_memory is None:
            short_memory = source.get("conversation_context") or source.get("recent_messages") or source.get("history") or []
        durable_source = source.get("durable_memory")
        if durable_source is None:
            durable_source = source.get("memory") or source.get("conversation_state") or {}
        sanitized = sanitize_durable_memory(durable_source)
        evidence = normalize_evidence_envelope(source.get("evidence") or source.get("worker_result") or source.get("data_selection"))
        created_at = _clean_text(source.get("created_at"), 80) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        turn_id = _clean_text(source.get("turn_id") or source.get("request_id"), 160)
        if not turn_id:
            turn_id = "turn_" + fingerprint({"key": key, "request": request, "created_at": created_at}, namespace=TURN_CONTEXT_V2)[:24]
        policy = _mapping(source.get("policy") or source.get("guidance") or source.get("prompt_policy"))
        fingerprints = {
            **_mapping(source.get("fingerprints")),
            "request": fingerprint(request, namespace="jk.codex.turn-request.v2"),
            "policy": fingerprint(policy, namespace="jk.codex.turn-policy.v2"),
            "short_memory": fingerprint(short_memory, namespace="jk.codex.short-memory.v2"),
            "durable_memory": sanitized.fingerprint,
            "evidence": evidence.fingerprint,
            "scope": fingerprint(scope, namespace="jk.codex.turn-scope.v2"),
        }
        fingerprints["context"] = fingerprint(
            {
                "conversation_key": key,
                "turn_id": turn_id,
                "request": request,
                "policy": policy,
                "scope": scope,
                "short_memory": short_memory,
                "durable_memory": sanitized.value,
                "evidence": evidence.to_dict(),
            },
            namespace=TURN_CONTEXT_V2,
        )
        diagnostics = {
            **_mapping(source.get("diagnostics")),
            "durable_memory": sanitized.diagnostics,
            "source_schema_version": source_schema,
        }
        return cls(
            source_schema_version=source_schema,
            conversation_key=key,
            turn_id=turn_id,
            surface=surface,
            client_id=client_id,
            store=store,
            user=user,
            subject=subject,
            request=_json_safe(request),
            policy=policy,
            scope=scope,
            short_memory=_json_safe(short_memory),
            durable_memory=sanitized.value,
            evidence=evidence,
            created_at=created_at,
            fingerprints=fingerprints,
            diagnostics=diagnostics,
        )


def build_turn_context(
    *,
    surface: Any,
    client_id: Any,
    store: Any = "",
    user: Any = "",
    subject: Any = "",
    request: Any,
    policy: Mapping[str, Any] | None = None,
    scope: Mapping[str, Any] | None = None,
    short_memory: Any = None,
    durable_memory: Any = None,
    evidence: Mapping[str, Any] | EvidenceEnvelopeV2 | None = None,
    turn_id: Any = "",
    created_at: Any = None,
) -> TurnContextV2:
    evidence_payload = evidence.to_dict() if isinstance(evidence, EvidenceEnvelopeV2) else evidence
    return TurnContextV2.from_mapping(
        {
            "schema_version": TURN_CONTEXT_V2,
            "surface": surface,
            "client_id": client_id,
            "store": store,
            "user": user,
            "subject": subject,
            "request": request,
            "policy": policy or {},
            "scope": scope or {},
            "short_memory": short_memory or [],
            "durable_memory": durable_memory or {},
            "evidence": evidence_payload or {},
            "turn_id": turn_id,
            "created_at": (_now(created_at).isoformat().replace("+00:00", "Z") if created_at is not None else ""),
        }
    )


@dataclass(frozen=True)
class FinalResponseV2:
    schema_version: str = FINAL_RESPONSE_V2
    source_schema_version: str = FINAL_RESPONSE_V2
    status: str = "completed"
    text: str = ""
    evidence_refs: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    confidence: str = "unknown"
    requires_human_review: bool = False
    approval_required: bool = False
    automation_eligible: bool = False
    publish_authorized: bool = False
    conversation_key: str = ""
    turn_id: str = ""
    thread_id: str = ""
    model: str = ""
    fingerprint: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_schema_version": self.source_schema_version,
            "status": self.status,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
            "gaps": list(self.gaps),
            "confidence": self.confidence,
            "requires_human_review": self.requires_human_review,
            "approval_required": self.approval_required,
            "automation_eligible": self.automation_eligible,
            "publish_authorized": self.publish_authorized,
            "conversation_key": self.conversation_key,
            "turn_id": self.turn_id,
            "thread_id": self.thread_id,
            "model": self.model,
            "fingerprint": self.fingerprint,
            "diagnostics": _json_safe(self.diagnostics),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "FinalResponseV2":
        source = _mapping(value)
        source_schema = _schema_source(source, FINAL_RESPONSE_V2, FINAL_RESPONSE_V1)
        status = _clean_text(source.get("status"), 30).lower() or "completed"
        confidence = _clean_text(source.get("confidence"), 20).lower()
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "unknown"
        gaps = _clean_list(source.get("gaps") or source.get("missing") or source.get("warnings"), max_items=30, item_limit=1000)
        evidence_refs = _clean_list(
            source.get("evidence_refs") or source.get("sources") or source.get("references"),
            max_items=40,
            item_limit=1000,
        )
        human_review = bool(source.get("requires_human_review") or source.get("human_review_required"))
        approval_required = bool(source.get("approval_required", source.get("requires_approval", False)))
        publish_authorized = bool(source.get("publish_authorized", False))
        payload = {
            "source_schema_version": source_schema,
            "status": status,
            "text": _clean_text(
                source.get("text") or source.get("response") or source.get("resposta") or source.get("final_response") or source.get("answer"),
                50_000,
            ),
            "evidence_refs": evidence_refs,
            "gaps": gaps,
            "confidence": confidence,
            "requires_human_review": human_review,
            "approval_required": approval_required,
            "automation_eligible": bool(source.get("automation_eligible", source.get("pode_enviar_automaticamente", False))),
            "publish_authorized": publish_authorized,
            "conversation_key": _clean_text(source.get("conversation_key") or source.get("conversation_id"), 200),
            "turn_id": _clean_text(source.get("turn_id") or source.get("request_id"), 160),
            "thread_id": _clean_text(source.get("thread_id") or source.get("codex_thread_id"), 200),
            "model": _clean_text(source.get("model"), 160),
            "diagnostics": _mapping(source.get("diagnostics")),
        }
        response_fp = fingerprint(payload, namespace=FINAL_RESPONSE_V2)
        return cls(**payload, fingerprint=_clean_text(source.get("fingerprint"), 128) or response_fp)


def normalize_final_response(value: Mapping[str, Any] | None) -> FinalResponseV2:
    return FinalResponseV2.from_mapping(value)


def read_conversation_decision(value: Mapping[str, Any] | None) -> ConversationDecisionV2:
    return ConversationDecisionV2.from_mapping(value)


def read_turn_context(value: Mapping[str, Any] | None) -> TurnContextV2:
    return TurnContextV2.from_mapping(value)


def read_evidence_envelope(value: Mapping[str, Any] | None) -> EvidenceEnvelopeV2:
    return EvidenceEnvelopeV2.from_mapping(value)


def read_final_response(value: Mapping[str, Any] | None) -> FinalResponseV2:
    return FinalResponseV2.from_mapping(value)


CONVERSATION_DECISION_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version", "conversation_key", "action", "thread_action", "reuse_thread",
        "restart_required", "restart_reasons", "prompt_fingerprint", "schema_fingerprint",
        "scope_fingerprint", "fingerprint",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [CONVERSATION_DECISION_V2]},
        "conversation_key": {"type": "string", "maxLength": 200},
        "action": {"type": "string", "maxLength": 80},
        "thread_action": {"type": "string", "enum": sorted(_THREAD_ACTIONS)},
        "reuse_thread": {"type": "boolean"},
        "restart_required": {"type": "boolean"},
        "restart_reasons": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "prompt_fingerprint": {"type": "string", "maxLength": 128},
        "schema_fingerprint": {"type": "string", "maxLength": 128},
        "scope_fingerprint": {"type": "string", "maxLength": 128},
        "fingerprint": {"type": "string", "maxLength": 128},
    },
}

EVIDENCE_ENVELOPE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version", "status", "records", "facts", "sources", "gaps",
        "evidence_sufficient", "coverage_complete", "fingerprint",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [EVIDENCE_ENVELOPE_V2]},
        "status": {"type": "string", "enum": sorted(_EVIDENCE_STATUSES)},
        "records": {"type": "array"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "evidence_sufficient": {"type": "boolean"},
        "coverage_complete": {"type": "boolean"},
        "fingerprint": {"type": "string", "maxLength": 128},
    },
}

TURN_CONTEXT_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version", "conversation_key", "turn_id", "surface", "client_id",
        "request", "policy", "scope", "short_memory", "durable_memory", "evidence",
        "fingerprints", "diagnostics",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [TURN_CONTEXT_V2]},
        "conversation_key": {"type": "string", "maxLength": 200},
        "turn_id": {"type": "string", "maxLength": 160},
        "surface": {"type": "string", "maxLength": 120},
        "client_id": {"type": "string", "maxLength": 160},
        "request": {},
        "policy": {"type": "object"},
        "scope": {"type": "object"},
        "short_memory": {},
        "durable_memory": {},
        "evidence": EVIDENCE_ENVELOPE_V2_SCHEMA,
        "fingerprints": {"type": "object"},
        "diagnostics": {"type": "object"},
    },
}

FINAL_RESPONSE_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version", "status", "text", "evidence_refs", "gaps",
        "requires_human_review", "approval_required", "automation_eligible",
        "publish_authorized", "fingerprint",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [FINAL_RESPONSE_V2]},
        "status": {"type": "string", "maxLength": 30},
        "text": {"type": "string", "maxLength": 50_000},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "requires_human_review": {"type": "boolean"},
        "approval_required": {"type": "boolean"},
        "automation_eligible": {"type": "boolean"},
        "publish_authorized": {"type": "boolean"},
        "fingerprint": {"type": "string", "maxLength": 128},
    },
}

_CONTRACT_HASH_INPUT = {
    "contract_version": CONTRACT_VERSION,
    "schemas": {
        CONVERSATION_DECISION_V2: CONVERSATION_DECISION_V2_SCHEMA,
        TURN_CONTEXT_V2: TURN_CONTEXT_V2_SCHEMA,
        EVIDENCE_ENVELOPE_V2: EVIDENCE_ENVELOPE_V2_SCHEMA,
        FINAL_RESPONSE_V2: FINAL_RESPONSE_V2_SCHEMA,
    },
    "restart_days": DEFAULT_THREAD_MAX_AGE_DAYS,
    "identity_dimensions": ["surface", "client_id", "store", "user", "subject"],
    "durable_memory_forbidden": sorted(_DURABLE_FORBIDDEN_KEY_TOKENS),
}
CONTRACT_HASH = fingerprint(_CONTRACT_HASH_INPUT, namespace=CONTRACT_VERSION)


def contract_diagnostics() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "contract_hash": CONTRACT_HASH,
        "schemas": {
            "conversation_decision": CONVERSATION_DECISION_V2,
            "turn_context": TURN_CONTEXT_V2,
            "evidence_envelope": EVIDENCE_ENVELOPE_V2,
            "final_response": FINAL_RESPONSE_V2,
        },
        "v1_readers_enabled": True,
        "identity_dimensions": ["surface", "client_id", "store", "user", "subject"],
        "default_context_budget_bytes": DEFAULT_CONTEXT_BUDGET_BYTES,
        "default_memory_budget_bytes": DEFAULT_MEMORY_BUDGET_BYTES,
        "thread_max_age_days": DEFAULT_THREAD_MAX_AGE_DAYS,
    }


def turn_context_diagnostics(value: TurnContextV2 | Mapping[str, Any]) -> dict[str, Any]:
    context = value if isinstance(value, TurnContextV2) else read_turn_context(value)
    payload = context.to_dict()
    return {
        "schema_version": context.schema_version,
        "source_schema_version": context.source_schema_version,
        "conversation_key": context.conversation_key,
        "turn_id": context.turn_id,
        "surface": context.surface,
        "bytes": _json_bytes(payload),
        "fingerprint": fingerprint(payload, namespace=TURN_CONTEXT_V2),
        "evidence_status": context.evidence.status,
        "evidence_fingerprint": context.evidence.fingerprint,
        "durable_memory_fingerprint": context.fingerprints.get("durable_memory", ""),
        "durable_memory_removed_count": int(
            (_mapping(context.diagnostics.get("durable_memory"))).get("removed_count") or 0
        ),
    }


__all__ = [
    "CONTRACT_VERSION",
    "CONTRACT_HASH",
    "CONVERSATION_DECISION_V1",
    "CONVERSATION_DECISION_V2",
    "TURN_CONTEXT_V1",
    "TURN_CONTEXT_V2",
    "EVIDENCE_ENVELOPE_V1",
    "EVIDENCE_ENVELOPE_V2",
    "FINAL_RESPONSE_V1",
    "FINAL_RESPONSE_V2",
    "CONVERSATION_DECISION_V2_SCHEMA",
    "TURN_CONTEXT_V2_SCHEMA",
    "EVIDENCE_ENVELOPE_V2_SCHEMA",
    "FINAL_RESPONSE_V2_SCHEMA",
    "DEFAULT_CONTEXT_BUDGET_BYTES",
    "DEFAULT_MEMORY_BUDGET_BYTES",
    "DEFAULT_THREAD_MAX_AGE_DAYS",
    "CompactionResult",
    "SanitizedMemory",
    "ConversationDecisionV2",
    "TurnContextV2",
    "EvidenceEnvelopeV2",
    "FinalResponseV2",
    "fingerprint",
    "conversation_key",
    "decide_conversation",
    "compact_json_structural",
    "bounded_json",
    "sanitize_durable_memory",
    "normalize_evidence_envelope",
    "normalize_final_response",
    "build_turn_context",
    "read_conversation_decision",
    "read_turn_context",
    "read_evidence_envelope",
    "read_final_response",
    "contract_diagnostics",
    "turn_context_diagnostics",
]
