"""Compatibility facade for Vendas chart functions."""

from __future__ import annotations

from backend.services.vendas_facade import facade_call, service


def configure_vendas_grafico_runtime(runtime_module=None): return runtime_module

def grafico_vendas(periodo: str = "3m", intervalo: str = "dia", sku: str = None, data_inicio: str = None, data_fim: str = None, loja: str = None, unidade_negocio: str = None, mostrar_estoque_geral: bool = False, mostrar_estoque_sku: bool = False, client_id: str = "", incluir_previsao_mes_atual: bool = False):
    return facade_call(service().grafico_vendas, periodo=periodo, intervalo=intervalo, sku=sku, data_inicio=data_inicio, data_fim=data_fim, loja=loja, unidade_negocio=unidade_negocio, mostrar_estoque_geral=mostrar_estoque_geral, mostrar_estoque_sku=mostrar_estoque_sku, incluir_previsao_mes_atual=incluir_previsao_mes_atual, client_id=client_id)

def skus_sem_venda(loja: str = None, unidade_negocio: str = None, client_id: str = ""):
    return facade_call(service().skus_sem_venda, loja=loja, unidade_negocio=unidade_negocio, client_id=client_id)


__all__ = ["configure_vendas_grafico_runtime", "grafico_vendas", "skus_sem_venda"]
