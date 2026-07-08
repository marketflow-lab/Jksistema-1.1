"""Renovacao service facade.

Keep public imports stable while the implementation stays split by responsibility.
"""

from __future__ import annotations

from backend.services.renovacao_context import (
    RenovacaoServiceConfig,
    configure_renovacao_context,
    get_tenant_path,
)
from backend.services.renovacao_planilhas import (
    analisar_renovacao_logic,
    find_col,
    find_common_key,
    find_sheet_name,
    gerar_excel_renovacao,
    processar_renovacao_logic,
)
from backend.services.renovacao_promocoes_helpers import _renovacao_sync_job_get
from backend.services.renovacao_promocoes import (
    _renovacao_atualizar_periodo_campanha_ml,
    _renovacao_criar_ou_completar_proximo_mes,
    _renovacao_deletar_campanha_ml,
    _renovacao_listar_campanhas_usuario_payload,
    _renovacao_sincronizar_promocao_existente,
    _renovacao_sincronizar_promocao_iniciar_payload,
)
from backend.services.renovacao_agendamento_store import (
    _renovacao_agendamento_atual,
    _renovacao_agendamento_put_payload,
)
from backend.services.renovacao_agendamento_worker import _renovacao_iniciar_agendamento_background

__all__ = [
    "RenovacaoServiceConfig",
    "configure_renovacao_context",
    "get_tenant_path",
    "analisar_renovacao_logic",
    "find_col",
    "find_common_key",
    "find_sheet_name",
    "gerar_excel_renovacao",
    "processar_renovacao_logic",
    "_renovacao_agendamento_atual",
    "_renovacao_agendamento_put_payload",
    "_renovacao_atualizar_periodo_campanha_ml",
    "_renovacao_criar_ou_completar_proximo_mes",
    "_renovacao_deletar_campanha_ml",
    "_renovacao_iniciar_agendamento_background",
    "_renovacao_listar_campanhas_usuario_payload",
    "_renovacao_sincronizar_promocao_existente",
    "_renovacao_sincronizar_promocao_iniciar_payload",
    "_renovacao_sync_job_get",
]
