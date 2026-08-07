"""Compatibility facade for the modular internal Codex Console."""

from __future__ import annotations

from backend.services.codex.console import api, bootstrap
from backend.services.codex.console.api_conversations import (
    codex_complementar_tarefa_para_sessao,
)
from backend.services.codex.console.api_create import (
    codex_registrar_interacao_whatsapp_externa,
)
from backend.services.codex.console.contracts import (
    CodexActionApprovalRequest,
    CodexActionProposalRequest,
    CodexActionRevisionRequest,
    CodexAgentGuidanceRequest,
    CodexAgentGuidanceSimulationRequest,
    CodexCapabilityResolveRequest,
    CodexConversationResetRequest,
    CodexEvaluationRunAPIRequest,
    CodexMCPRolloutRequest,
    CodexTaskApprovalRequest,
    CodexTaskFeedbackRequest,
    CodexTaskRequest,
    CodexTaskSteerRequest,
)
from backend.services.codex.console.queue_worker import (
    codex_console_recuperar_fila_background,
)


def configure_codex_console_runtime(runtime_module=None):
    return bootstrap.configure(runtime_module)


for _name in api.__all__:
    globals()[_name] = getattr(api, _name)

configure_codex_console_runtime()

__all__ = [
    "CodexTaskRequest",
    "CodexTaskSteerRequest",
    "CodexConversationResetRequest",
    "CodexActionProposalRequest",
    "CodexActionApprovalRequest",
    "CodexActionRevisionRequest",
    "CodexAgentGuidanceRequest",
    "CodexAgentGuidanceSimulationRequest",
    "CodexCapabilityResolveRequest",
    "configure_codex_console_runtime",
    "codex_status",
    "codex_upload_attachments",
    "codex_criar_tarefa",
    "codex_listar_tarefas",
    "codex_registrar_interacao_whatsapp_externa",
    "codex_listar_conversas_whatsapp",
    "codex_listar_mensagens_conversa_whatsapp",
    "codex_obter_mensagem_conversa_whatsapp",
    "codex_obter_tarefa",
    "codex_deletar_tarefa",
    "codex_deletar_conversa",
    "codex_reset_current_conversation",
    "codex_aprovar_tarefa",
    "codex_cancelar_tarefa",
    "codex_complementar_tarefa",
    "codex_complementar_tarefa_para_sessao",
    "codex_program_functions",
    "codex_capabilities_listar",
    "codex_capabilities_modules",
    "codex_capabilities_resolver",
    "codex_actions_listar",
    "codex_actions_listar_propostas",
    "codex_actions_criar_proposta",
    "codex_actions_aprovar_proposta",
    "codex_actions_revisar_proposta",
    "codex_actions_rejeitar_proposta",
    "codex_actions_obter_execucao",
    "codex_actions_cancelar_execucao",
    "codex_agent_settings_get",
    "codex_agent_guidance_put",
    "codex_agent_guidance_simulate",
    "codex_agent_capability_coverage",
    "codex_agent_plan_get",
    "codex_console_recuperar_fila_background",
]
