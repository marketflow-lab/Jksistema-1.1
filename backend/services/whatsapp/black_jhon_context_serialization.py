"""Bounded JSON serialization for Black Jhon prompt contexts."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Sequence


_PROTECTED_CONTEXT_KEYS = frozenset(
    {"records", "facts", "sources", "gaps", "verified_facts", "missing"}
)
_USER_CONTEXT_KEYS = frozenset({"user_message", "request_text", "job_prompt"})


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return _clean_text(value, 500)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.replace("\x00", "")[:12000]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            result[_clean_text(key, 120)] = json_safe(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_safe(item, depth=depth + 1) for item in list(value)[:100]]
    return _clean_text(value, 2000)


def _reduction_candidates(
    value: Any,
    path: tuple[Any, ...] = (),
) -> list[tuple[int, int, tuple[Any, ...], str]]:
    candidates: list[tuple[int, int, tuple[Any, ...], str]] = []
    protected = any(str(part) in _PROTECTED_CONTEXT_KEYS for part in path)
    user_content = any(str(part) in _USER_CONTEXT_KEYS for part in path)
    priority = 2 if protected else (1 if user_content else 0)
    if isinstance(value, str) and len(value) > 80:
        candidates.append((priority, -len(value), path, "string"))
    elif isinstance(value, list):
        if len(value) > 1:
            candidates.append((priority, -len(_canonical_json(value)), path, "list"))
        for index, item in enumerate(value):
            candidates.extend(_reduction_candidates(item, (*path, index)))
    elif isinstance(value, dict):
        for key, item in value.items():
            candidates.extend(_reduction_candidates(item, (*path, key)))
    return candidates


def _path_value(root: Any, path: tuple[Any, ...]) -> Any:
    current = root
    for part in path:
        current = current[part]
    return current


def _replace_path(root: Any, path: tuple[Any, ...], value: Any) -> None:
    if not path:
        raise ValueError("root_replacement_not_supported")
    parent = _path_value(root, path[:-1])
    parent[path[-1]] = value


def _shrink_once(value: dict[str, Any]) -> bool:
    candidates = sorted(
        _reduction_candidates(value),
        key=lambda item: (item[0], item[1], len(item[2])),
    )
    if not candidates:
        return False
    _priority, _size, path, kind = candidates[0]
    current = _path_value(value, path)
    if kind == "list":
        keep = max(1, len(current) // 2)
        replacement = current[-keep:] if "conversation_context" in path else current[:keep]
    else:
        keep = max(40, min(len(current) - 4, len(current) // 2))
        replacement = current[:keep].rstrip() + "..."
    _replace_path(value, path, replacement)
    return True


def bounded_context_json(
    context: Mapping[str, Any],
    *,
    max_chars: int,
    hard_max_chars: int,
    schema_version: str,
) -> str:
    """Serialize a complete JSON object within the requested character budget."""

    safe_limit = max(1000, min(int(max_chars or hard_max_chars), hard_max_chars))
    source = dict(context) if isinstance(context, Mapping) else {}
    payload = json_safe({"schema_version": schema_version, **source})
    serialized = _canonical_json(payload)
    reductions = 0
    while len(serialized) > safe_limit and reductions < 1000 and _shrink_once(payload):
        reductions += 1
        serialized = _canonical_json(payload)
    if len(serialized) <= safe_limit:
        return serialized
    return _canonical_json(
        {
            "schema_version": schema_version,
            "context_omitted": True,
            "reason": "context_budget_exceeded",
        }
    )


def bounded_evidence_envelope(
    envelope: dict[str, Any],
    *,
    evidence_schema_version: str,
    prompt_context_schema_version: str,
    max_chars: int,
    hard_max_chars: int,
) -> dict[str, Any]:
    candidate = copy.deepcopy(envelope)
    serialized = bounded_context_json(
        candidate,
        max_chars=max_chars,
        hard_max_chars=hard_max_chars,
        schema_version=prompt_context_schema_version,
    )
    decoded = json.loads(serialized)
    decoded.pop("schema_version", None)
    result = {"schema_version": evidence_schema_version, **decoded}
    facts = list(result.get("facts") or result.get("verified_facts") or [])
    gaps = list(result.get("gaps") or result.get("missing") or [])
    result["facts"] = facts
    result["sources"] = list(result.get("sources") or [])
    result["gaps"] = gaps
    result["verified_facts"] = facts
    result["missing"] = gaps
    return result


__all__ = ["bounded_context_json", "bounded_evidence_envelope", "json_safe"]
