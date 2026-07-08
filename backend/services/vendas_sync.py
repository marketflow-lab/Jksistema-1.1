"""Compatibility facade for Vendas synchronization endpoints."""

from __future__ import annotations

from backend.services.vendas_sync_progress import *
from backend.services.vendas_sync_progress import configure_vendas_sync_progress_runtime
from backend.services.vendas_sync_periodo import *
from backend.services.vendas_sync_periodo import configure_vendas_sync_periodo_runtime
from backend.services.vendas_sync_runner import *
from backend.services.vendas_sync_runner import configure_vendas_sync_runner_runtime


def configure_vendas_sync_runtime(runtime_module=None):
    configure_vendas_sync_progress_runtime(runtime_module)
    configure_vendas_sync_periodo_runtime(runtime_module)
    configure_vendas_sync_runner_runtime(runtime_module)
    return runtime_module


configure_vendas_sync_runtime()

__all__ = [
    "configure_vendas_sync_runtime",
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
