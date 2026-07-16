"""Compatibility facade for Vendas return-note functions."""

from __future__ import annotations

from backend.services.vendas_facade import facade_call, service


def configure_vendas_notas_runtime(runtime_module=None): return runtime_module

def listar_notas_entrada(client_id: str, data_inicio: str = None, data_fim: str = None, agrupado: bool = False, unidade_negocio: str = None, loja: str = None):
    return facade_call(service().listar_notas_entrada, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, agrupado=agrupado, unidade_negocio=unidade_negocio, loja=loja)

def listar_itens_devolucoes(client_id: str, data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    return facade_call(service().listar_itens_devolucoes, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, unidade_negocio=unidade_negocio, loja=loja)

def listar_notas_entrada_por_sku(sku: str, client_id: str, data_inicio: str = None, data_fim: str = None, unidade_negocio: str = None, loja: str = None):
    return facade_call(service().listar_notas_entrada_por_sku, sku=sku, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, unidade_negocio=unidade_negocio, loja=loja)


__all__ = ["configure_vendas_notas_runtime", "listar_notas_entrada", "listar_itens_devolucoes", "listar_notas_entrada_por_sku"]
