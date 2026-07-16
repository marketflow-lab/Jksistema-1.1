"""Compatibility export for the isolated Vendas period synchronizer."""

from backend.modules.vendas.sync_period import _sincronizar_vendas_periodo_impl

def configure_vendas_sync_periodo_runtime(runtime_module=None): return runtime_module

__all__ = ["configure_vendas_sync_periodo_runtime", "_sincronizar_vendas_periodo_impl"]
