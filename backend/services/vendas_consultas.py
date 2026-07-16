"""Compatibility exports for Vendas read operations."""

from backend.services.vendas_grafico import configure_vendas_grafico_runtime, grafico_vendas, skus_sem_venda
from backend.services.vendas_listagem import (
    configure_vendas_listagem_runtime,
    limites_vendas,
    limpar_todos_bancos_vendas,
    listar_vendas,
    listar_vendas_todas,
    resumo_vendas,
)

def configure_vendas_consultas_runtime(runtime_module=None): return runtime_module

__all__ = [
    "configure_vendas_consultas_runtime", "resumo_vendas", "listar_vendas", "limpar_todos_bancos_vendas",
    "listar_vendas_todas", "limites_vendas", "grafico_vendas", "skus_sem_venda",
]
