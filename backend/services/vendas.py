"""Compatibility facade for the Vendas module."""

from __future__ import annotations

from backend.schemas import VendasQuery, VendasSyncRequest
from backend.services.vendas_context import *
from backend.services.vendas_context import configure_vendas_context as configure_vendas_runtime_context
from backend.services.vendas_sync_state import *
from backend.services.vendas_sync_state import (
    configure_vendas_context as configure_vendas_sync_state_context,
    _vendas_sync_parse_date,
    _vendas_sync_dias_periodo,
    _vendas_sync_state_path,
    _vendas_sync_load_state,
    _vendas_sync_save_state,
    _vendas_sync_job_key,
    _vendas_sync_prepare_job,
    _vendas_sync_update_job,
)
from backend.services.vendas_consultas import *
from backend.services.vendas_consultas import configure_vendas_consultas_runtime
from backend.services.vendas_notas import *
from backend.services.vendas_notas import configure_vendas_notas_runtime
from backend.services.vendas_unidades import *
from backend.services.vendas_unidades import configure_vendas_unidades_runtime
from backend.services.vendas_sync import *
from backend.services.vendas_sync import configure_vendas_sync_runtime


def configure_vendas_context(*, get_tenant_path, sync_state_lock=None):
    configure_vendas_runtime_context(get_tenant_path=get_tenant_path, sync_state_lock=sync_state_lock)
    configure_vendas_sync_state_context(get_tenant_path=get_tenant_path, sync_state_lock=sync_state_lock)


def configure_vendas_runtime(runtime_module=None):
    configure_vendas_runtime_context(runtime_module)
    configure_vendas_sync_state_context(
        get_tenant_path=get_tenant_path,
        sync_state_lock=SYNC_STATE_LOCK,
    )
    configure_vendas_consultas_runtime(runtime_module)
    configure_vendas_notas_runtime(runtime_module)
    configure_vendas_unidades_runtime(runtime_module)
    configure_vendas_sync_runtime(runtime_module)
    return runtime_module


configure_vendas_runtime()

__all__ = [
    "configure_vendas_context",
    "configure_vendas_runtime",
    "VendasQuery",
    "VendasSyncRequest",
    "_vendas_sync_parse_date",
    "_vendas_sync_dias_periodo",
    "_vendas_sync_state_path",
    "_vendas_sync_load_state",
    "_vendas_sync_save_state",
    "_vendas_sync_job_key",
    "_vendas_sync_prepare_job",
    "_vendas_sync_update_job",
    "resumo_vendas",
    "listar_vendas",
    "limpar_todos_bancos_vendas",
    "listar_vendas_todas",
    "grafico_vendas",
    "skus_sem_venda",
    "limites_vendas",
    "listar_notas_entrada",
    "listar_itens_devolucoes",
    "listar_notas_entrada_por_sku",
    "listar_unidades_negocios",
    "atualizar_unidade_negocio",
    "salvar_mapeamento_unidades",
    "_corrigir_texto_mojibake",
    "_criar_progresso",
    "_sync_context_key",
    "_sync_context_loja",
    "_sync_active_jobs_unlocked",
    "_sync_active_jobs",
    "_sync_register_active_unlocked",
    "_sync_unregister_active",
    "_sync_build_aggregate_progress",
    "_set_progresso",
    "_limpar_progresso",
    "_sync_log",
    "_verificar_cancelamento",
    "cancelar_sincronizacao_vendas",
    "progresso_sincronizacao_vendas",
    "_sincronizar_vendas_thread_worker",
    "sincronizar_vendas",
    "_sincronizar_vendas_impl",
    "_sincronizar_vendas_periodo_impl",
]
