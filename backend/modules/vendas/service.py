"""Application service for Vendas queries and mutations."""

from __future__ import annotations

from dataclasses import dataclass

from .repository import VendasRepository


@dataclass(frozen=True)
class VendasService:
    repository: VendasRepository

    def listar_vendas(self, **kwargs): return self.repository.listar(**kwargs)
    def resumo_vendas(self, **kwargs): return self.repository.resumo(**kwargs)
    def limpar_todos_bancos_vendas(self, client_id: str): return self.repository.limpar(client_id)
    def listar_vendas_todas(self, client_id: str): return self.repository.listar_todas(client_id)
    def grafico_vendas(self, **kwargs): return self.repository.grafico(**kwargs)
    def skus_sem_venda(self, **kwargs): return self.repository.skus_sem_venda(**kwargs)
    def limites_vendas(self, **kwargs): return self.repository.limites(**kwargs)
    def listar_notas_entrada(self, **kwargs): return self.repository.listar_notas(**kwargs)
    def listar_itens_devolucoes(self, **kwargs): return self.repository.listar_devolucoes(**kwargs)
    def listar_notas_entrada_por_sku(self, **kwargs): return self.repository.listar_notas_por_sku(**kwargs)
    def listar_unidades_negocios(self, **kwargs): return self.repository.listar_unidades(**kwargs)
    def atualizar_unidade_negocio(self, **kwargs): return self.repository.atualizar_unidade(**kwargs)
    def salvar_mapeamento_unidades(self, **kwargs): return self.repository.salvar_mapeamento(**kwargs)


__all__ = ["VendasService"]
