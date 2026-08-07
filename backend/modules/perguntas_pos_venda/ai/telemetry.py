"""Secret-free performance telemetry facade for the agent."""

from __future__ import annotations

from typing import Any

from .context import (
    _ia_agent_perguntas_log_perf,
    _ia_agent_perguntas_perf_etapa_tool,
    _ia_agent_perguntas_perf_meta,
)
from .runtime import resolve_runtime_adapter


def metadata(client_id: str, store: str, agent_input: dict | None) -> dict[str, str]:
    return _ia_agent_perguntas_perf_meta(client_id, store, agent_input)


def record(*args: Any, **kwargs: Any) -> None:
    resolve_runtime_adapter("telemetry", "record", _ia_agent_perguntas_log_perf)(*args, **kwargs)


def tool_stage(name: str) -> str:
    return _ia_agent_perguntas_perf_etapa_tool(name)


__all__ = ["metadata", "record", "tool_stage"]
