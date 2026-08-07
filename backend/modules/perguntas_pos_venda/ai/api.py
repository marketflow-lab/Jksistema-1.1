"""Stable, named API for the questions and post-sale agent."""

from __future__ import annotations

from typing import Any

from .contracts import PerguntasAgentRuntime, QuestionAgentResult
from .execution import _perguntas_ia_v2_gerar_resposta
from .inputs import _ia_agent_endpoint_autorizar, _ia_agent_input_dict, _perguntas_ia_agent_input
from .post_sale import _ml_pos_venda_contexto_prompt, _ml_pos_venda_validar_resposta
from .runtime import configure_runtime


def configure_perguntas_pos_venda_agent_runtime(
    runtime_module: Any = None,
    peers: Any = None,
) -> PerguntasAgentRuntime:
    return configure_runtime(runtime_module, peers)


def authorize_request(request: Any) -> None:
    _ia_agent_endpoint_autorizar(request)


def parse_request_input(payload: Any) -> dict:
    return _ia_agent_input_dict(payload)


def build_agent_input(*args: Any, **kwargs: Any) -> dict:
    return _perguntas_ia_agent_input(*args, **kwargs)


def generate_response(client_id: str, agent_input: dict) -> QuestionAgentResult:
    answer, model, diagnostics = _perguntas_ia_v2_gerar_resposta(client_id, agent_input)
    return QuestionAgentResult(answer=answer, model=model, diagnostics=diagnostics)


def build_post_sale_context(context: dict | None) -> str:
    return _ml_pos_venda_contexto_prompt(context)


def validate_post_sale_response(response: str, context: dict, limit: int | None = None) -> dict:
    return _ml_pos_venda_validar_resposta(response, context, limit)


__all__ = [
    "authorize_request",
    "build_agent_input",
    "build_post_sale_context",
    "configure_perguntas_pos_venda_agent_runtime",
    "generate_response",
    "parse_request_input",
    "validate_post_sale_response",
]
