"""Small, dependency-free contracts used by the WhatsApp function manager."""

from __future__ import annotations

import re
from typing import Any


_FORBIDDEN_ACTION_TOOLS = {
    "program_action_match",
    "operational_dispatcher",
}


def function_manager_catalog(permissions: Any) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    catalog: list[dict[str, Any]] = []
    for item in codex_assistant._assistant_tools_public(permissions):
        if not isinstance(item, dict) or item.get("read_only") is not True:
            continue
        tool_id = str(item.get("id") or "").strip()
        if not tool_id or tool_id in _FORBIDDEN_ACTION_TOOLS:
            continue
        catalog.append({
            "id": tool_id,
            "description": str(item.get("description") or "")[:500],
            "external": item.get("external") is True,
            "output_fields": [str(value or "")[:100] for value in list(item.get("output_fields") or [])[:30]],
            "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
        })
    return catalog[:100]


def function_manager_extract_identifiers(value: Any) -> tuple[str, str]:
    """Recognize only canonical MLB identifiers; never infer a SKU from prose."""

    item_match = re.search(r"\bMLB[\s_-]*(\d{6,})\b", str(value or ""), re.IGNORECASE)
    return "", (f"MLB{item_match.group(1)}" if item_match else "").upper()


__all__ = ["function_manager_catalog", "function_manager_extract_identifiers"]
