"""Typed contracts for the Mercado Livre questions and post-sale agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from logging import Logger
from typing import Any, Callable, Mapping


Adapter = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelAdapters:
    public_model: Adapter
    post_sale_model: Adapter
    public_reasoning: Adapter
    post_sale_reasoning: Adapter
    call_codex: Adapter
    call_codex_thread: Adapter
    call_vertex: Adapter
    call_gemini: Adapter
    call_deepseek: Adapter
    call_openai: Adapter


@dataclass(frozen=True, slots=True)
class ToolAdapters:
    product_data: Adapter
    mercado_livre_listing: Adapter
    bling_product: Adapter
    executor: Any


@dataclass(frozen=True, slots=True)
class SourceAdapters:
    web_cached: Adapter
    web_broad_cached: Adapter
    mercado_livre_request: Adapter
    mercado_livre_config: Adapter


@dataclass(frozen=True, slots=True)
class StateAdapters:
    intent: Adapter
    memory_prompt: Adapter
    clean_response: Adapter
    final_response: Adapter
    invalid_fallback: Adapter
    store_signature: Adapter


@dataclass(frozen=True, slots=True)
class PolicyAdapters:
    public_requires_approval: Adapter
    post_sale_requires_approval: Adapter
    compatibility_language_issues: Adapter


@dataclass(frozen=True, slots=True)
class TelemetryAdapters:
    record: Adapter
    logger: Logger


@dataclass(frozen=True, slots=True)
class PerguntasAgentRuntime:
    """Explicit, immutable dependency container used by the agent package."""

    models: ModelAdapters
    tools: ToolAdapters
    sources: SourceAdapters
    state: StateAdapters
    policies: PolicyAdapters
    telemetry: TelemetryAdapters
    logger: Logger
    overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuestionAgentResult:
    answer: str
    model: str
    diagnostics: list[dict[str, Any]]


__all__ = [
    "ModelAdapters",
    "PolicyAdapters",
    "PerguntasAgentRuntime",
    "QuestionAgentResult",
    "SourceAdapters",
    "StateAdapters",
    "TelemetryAdapters",
    "ToolAdapters",
]
