"""Compatibility exports for durable Vendas synchronization state."""

from backend.modules.vendas.sync_persistence import (
    _vendas_sync_dias_periodo,
    _vendas_sync_job_key,
    _vendas_sync_load_state,
    _vendas_sync_parse_date,
    _vendas_sync_prepare_job,
    _vendas_sync_save_state,
    _vendas_sync_state_path,
    _vendas_sync_update_job,
)

def configure_vendas_context(*, get_tenant_path=None, sync_state_lock=None): return None

__all__ = [
    "configure_vendas_context", "_vendas_sync_parse_date", "_vendas_sync_dias_periodo",
    "_vendas_sync_state_path", "_vendas_sync_load_state", "_vendas_sync_save_state",
    "_vendas_sync_job_key", "_vendas_sync_prepare_job", "_vendas_sync_update_job",
]
