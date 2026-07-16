"""Compatibility facade for Vendas listing/query functions."""

from __future__ import annotations

from backend.services.vendas_facade import facade_call, service


def configure_vendas_listagem_runtime(runtime_module=None): return runtime_module

def listar_vendas(client_id: str, data_inicio: str = None, data_fim: str = None, sku: str = None, loja: str = None, unidade_negocio: str = None, resolver_nf: bool = False, incluir_ebazar: bool = False):
    return facade_call(service().listar_vendas, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, sku=sku, loja=loja, unidade_negocio=unidade_negocio, resolver_nf=resolver_nf, incluir_ebazar=incluir_ebazar)

def resumo_vendas(client_id: str, data_inicio: str = None, data_fim: str = None, sku: str = None, loja: str = None, unidade_negocio: str = None, incluir_ebazar: bool = False):
    return facade_call(service().resumo_vendas, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, sku=sku, loja=loja, unidade_negocio=unidade_negocio, incluir_ebazar=incluir_ebazar)

def limpar_todos_bancos_vendas(client_id: str): return facade_call(service().limpar_todos_bancos_vendas, client_id)
def listar_vendas_todas(client_id: str): return facade_call(service().listar_vendas_todas, client_id)

def limites_vendas(loja: str = None, unidade_negocio: str = None, client_id: str = ""):
    return facade_call(service().limites_vendas, loja=loja, unidade_negocio=unidade_negocio, client_id=client_id)


__all__ = ["configure_vendas_listagem_runtime", "resumo_vendas", "listar_vendas", "limpar_todos_bancos_vendas", "listar_vendas_todas", "limites_vendas"]
