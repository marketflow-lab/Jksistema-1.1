"""Compatibility facade for Vendas business-unit functions."""

from __future__ import annotations

from backend.services.vendas_facade import facade_call, service


def configure_vendas_unidades_runtime(runtime_module=None): return runtime_module

def listar_unidades_negocios(loja: str = None, sku: str = None, data_inicio: str = None, data_fim: str = None, client_id: str = ""):
    return facade_call(service().listar_unidades_negocios, loja=loja, sku=sku, data_inicio=data_inicio, data_fim=data_fim, client_id=client_id)

def atualizar_unidade_negocio(nome_antigo: str, nome_novo: str, client_id: str = ""):
    return facade_call(service().atualizar_unidade_negocio, nome_antigo=nome_antigo, nome_novo=nome_novo, client_id=client_id)

def salvar_mapeamento_unidades(mapeamento: dict, client_id: str = ""):
    return facade_call(service().salvar_mapeamento_unidades, mapeamento=mapeamento, client_id=client_id)


__all__ = ["configure_vendas_unidades_runtime", "listar_unidades_negocios", "atualizar_unidade_negocio", "salvar_mapeamento_unidades"]
