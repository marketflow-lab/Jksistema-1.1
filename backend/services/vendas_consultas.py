"""Compatibility facade for Vendas query endpoints."""

from __future__ import annotations

from backend.services.vendas_listagem import *
from backend.services.vendas_listagem import configure_vendas_listagem_runtime
from backend.services.vendas_grafico import *
from backend.services.vendas_grafico import configure_vendas_grafico_runtime


def configure_vendas_consultas_runtime(runtime_module=None):
    configure_vendas_listagem_runtime(runtime_module)
    configure_vendas_grafico_runtime(runtime_module)
    return runtime_module


configure_vendas_consultas_runtime()

__all__ = [
    "configure_vendas_consultas_runtime",
    "resumo_vendas",
    "listar_vendas",
    "limpar_todos_bancos_vendas",
    "listar_vendas_todas",
    "grafico_vendas",
    "skus_sem_venda",
    "limites_vendas",
]
