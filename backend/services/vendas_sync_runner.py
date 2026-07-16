"""Compatibility facade for the isolated Vendas synchronization engine."""

from backend.modules.vendas.sync_engine import _sincronizar_vendas_impl, _sincronizar_vendas_thread_worker
from backend.services.vendas_facade import facade_call, sync_service

def configure_vendas_sync_runner_runtime(runtime_module=None): return runtime_module
def sincronizar_vendas(req, client_id: str): return facade_call(sync_service().iniciar, req, client_id)

__all__ = ["configure_vendas_sync_runner_runtime", "_sincronizar_vendas_thread_worker", "sincronizar_vendas", "_sincronizar_vendas_impl"]
