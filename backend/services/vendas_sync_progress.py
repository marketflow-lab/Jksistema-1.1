"""Compatibility facade for Vendas synchronization progress."""

from backend.modules.vendas.progress import (
    _corrigir_texto_mojibake,
    _criar_progresso,
    _limpar_progresso,
    _set_progresso,
    _sync_active_jobs,
    _sync_active_jobs_unlocked,
    _sync_build_aggregate_progress,
    _sync_context_key,
    _sync_context_loja,
    _sync_log,
    _sync_register_active_unlocked,
    _sync_unregister_active,
    _verificar_cancelamento,
)
from backend.services.vendas_facade import facade_call, sync_service

def configure_vendas_sync_progress_runtime(runtime_module=None): return runtime_module
def cancelar_sincronizacao_vendas(client_id: str): return facade_call(sync_service().cancelar, client_id)
def progresso_sincronizacao_vendas(client_id: str): return facade_call(sync_service().progresso, client_id)

__all__ = [
    "configure_vendas_sync_progress_runtime", "_corrigir_texto_mojibake", "_criar_progresso",
    "_sync_context_key", "_sync_context_loja", "_sync_active_jobs_unlocked", "_sync_active_jobs",
    "_sync_register_active_unlocked", "_sync_unregister_active", "_sync_build_aggregate_progress",
    "_set_progresso", "_limpar_progresso", "_sync_log", "_verificar_cancelamento",
    "cancelar_sincronizacao_vendas", "progresso_sincronizacao_vendas",
]
