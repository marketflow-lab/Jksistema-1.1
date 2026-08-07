"""Compatibility facade for the modular questions and post-sale agent."""

from backend.modules.perguntas_pos_venda.ai.api import (
    build_agent_input,
    build_post_sale_context,
    configure_perguntas_pos_venda_agent_runtime,
    generate_response,
    validate_post_sale_response,
)
from backend.modules.perguntas_pos_venda.ai.contracts import PerguntasAgentRuntime, QuestionAgentResult

__all__ = [
    "PerguntasAgentRuntime",
    "QuestionAgentResult",
    "configure_perguntas_pos_venda_agent_runtime",
    "build_agent_input",
    "generate_response",
    "build_post_sale_context",
    "validate_post_sale_response",
]
