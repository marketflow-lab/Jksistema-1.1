"""Named HTTP handler surface for the Codex Console router."""

from .api_actions import (
    codex_actions_aprovar_proposta,
    codex_actions_cancelar_execucao,
    codex_actions_criar_proposta,
    codex_actions_listar,
    codex_actions_listar_propostas,
    codex_actions_obter_execucao,
    codex_actions_rejeitar_proposta,
    codex_actions_revisar_proposta,
    codex_agent_capability_coverage,
    codex_agent_guidance_put,
    codex_agent_guidance_simulate,
    codex_agent_plan_get,
    codex_agent_settings_get,
    codex_capabilities_listar,
    codex_capabilities_modules,
    codex_capabilities_resolver,
    codex_program_functions,
)
from .api_admin import (
    codex_evaluation_run_get,
    codex_evaluation_run_post,
    codex_evaluation_runs,
    codex_mcp_rollout_get,
    codex_mcp_rollout_put,
    codex_task_feedback,
    codex_telemetry_summary,
    codex_telemetry_timeseries,
    codex_transcribe_audio,
)
from .api_conversations import (
    codex_aprovar_tarefa,
    codex_cancelar_tarefa,
    codex_complementar_tarefa,
    codex_deletar_conversa,
    codex_deletar_tarefa,
    codex_listar_conversas_whatsapp,
    codex_listar_mensagens_conversa_whatsapp,
    codex_obter_mensagem_conversa_whatsapp,
    codex_obter_tarefa,
    codex_reset_current_conversation,
)
from .api_create import (
    codex_criar_tarefa,
    codex_listar_tarefas,
    codex_status,
    codex_upload_attachments,
)


__all__ = [name for name in tuple(globals()) if name.startswith("codex_")]
__codex_dependencies__ = []
