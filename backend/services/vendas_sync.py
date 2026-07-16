"""Compatibility exports for Vendas synchronization."""

from backend.services.vendas_sync_periodo import _sincronizar_vendas_periodo_impl, configure_vendas_sync_periodo_runtime
from backend.services.vendas_sync_progress import (
    _corrigir_texto_mojibake, _criar_progresso, _limpar_progresso, _set_progresso,
    _sync_active_jobs, _sync_active_jobs_unlocked, _sync_build_aggregate_progress,
    _sync_context_key, _sync_context_loja, _sync_log, _sync_register_active_unlocked,
    _sync_unregister_active, _verificar_cancelamento, cancelar_sincronizacao_vendas,
    configure_vendas_sync_progress_runtime, progresso_sincronizacao_vendas,
)
from backend.services.vendas_sync_runner import (
    _sincronizar_vendas_impl, _sincronizar_vendas_thread_worker,
    configure_vendas_sync_runner_runtime, sincronizar_vendas,
)

def configure_vendas_sync_runtime(runtime_module=None): return runtime_module

__all__ = [name for name in (
    "configure_vendas_sync_runtime", "_corrigir_texto_mojibake", "_criar_progresso", "_sync_context_key",
    "_sync_context_loja", "_sync_active_jobs_unlocked", "_sync_active_jobs", "_sync_register_active_unlocked",
    "_sync_unregister_active", "_sync_build_aggregate_progress", "_set_progresso", "_limpar_progresso",
    "_sync_log", "_verificar_cancelamento", "cancelar_sincronizacao_vendas",
    "progresso_sincronizacao_vendas", "_sincronizar_vendas_thread_worker", "sincronizar_vendas",
    "_sincronizar_vendas_impl", "_sincronizar_vendas_periodo_impl",
)]
