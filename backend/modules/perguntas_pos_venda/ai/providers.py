"""Named provider operations for sibling services."""

from __future__ import annotations

from typing import Any

from .inputs import _perguntas_codex_compact_json, _perguntas_codex_provider_selection
from .provider_transport import invoke_model as _invoke_model


def select_response_provider(model: str, operational_failure_count: Any = None) -> dict[str, Any]:
    return _perguntas_codex_provider_selection(model, operational_failure_count)


def compact_json(value: Any, max_chars: int) -> str:
    return _perguntas_codex_compact_json(value, max_chars)


def invoke_model(client_id: str, payload: Any, model: str) -> tuple[str, str]:
    return _invoke_model(client_id, payload, model)


__all__ = ["compact_json", "invoke_model", "select_response_provider"]
