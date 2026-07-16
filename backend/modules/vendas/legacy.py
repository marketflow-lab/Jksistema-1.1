"""Explicit, pre-bound adapter boundary for shared Vendas helpers."""

from __future__ import annotations

from backend.services.bling_vendas import (
    _bling_executar_com_refresh,
    _bling_listar_naturezas,
    _bling_listar_notas_entrada,
    _bling_listar_vendas,
    _bling_listar_vendas_fallback_nf_saida,
    _bling_marcar_oauth_invalido,
    _bling_obter_detalhes_nf,
    _bling_obter_numero_nf,
    _bling_refresh_token,
    _bling_renovar_token_loja,
    _carregar_mapeamento_lojas_virtuais_cliente,
    _carregar_mapeamento_unidades,
    _classificar_unidade_virtual_devolucao,
    _deduplicar_vendas_consolidadas,
    _deve_excluir_venda_ebazar,
    _eh_devolucao_nota_entrada,
    _eh_devolucao_por_cfop_itens,
    _extrair_codigo_origem_nf,
    _get_notas_entrada_db,
    _get_vendas_db,
    _get_vendas_db_path,
    _listar_bancos_vendas_tenant,
    _normalizar_nome_loja_virtual_candidato,
    _normalizar_texto,
    _normalizar_unidade_devolucao_entrada,
    _normalizar_unidade_negocio_ml,
    _resolver_nome_loja_virtual,
    _salvar_mapeamento_unidades,
    _sql_filtro_loja_notas_entrada,
    _sql_filtro_loja_vendas,
    _sql_filtro_unidade_com_mapa,
    _sql_filtro_unidade_devolucao,
    _tipo_devolucao_cfop_full_estoque,
)
from backend.services.estoque_historico import _normalizar_sku_estoque, _vendas_series_estoque_historico
from backend.services.integracoes import atualizar_api_loja, buscar_loja

from .dependencies import migrate_legacy_file


def _migrar_arquivo_legado_para_tenant(*args, **kwargs):
    return migrate_legacy_file(*args, **kwargs)


__all__ = [
    "buscar_loja", "atualizar_api_loja", "_migrar_arquivo_legado_para_tenant",
    "_normalizar_sku_estoque", "_vendas_series_estoque_historico", "_bling_refresh_token",
    "_bling_marcar_oauth_invalido", "_bling_renovar_token_loja", "_bling_executar_com_refresh",
    "_carregar_mapeamento_unidades", "_salvar_mapeamento_unidades",
    "_normalizar_nome_loja_virtual_candidato", "_carregar_mapeamento_lojas_virtuais_cliente",
    "_resolver_nome_loja_virtual", "_normalizar_unidade_negocio_ml",
    "_normalizar_unidade_devolucao_entrada", "_eh_devolucao_nota_entrada",
    "_eh_devolucao_por_cfop_itens", "_tipo_devolucao_cfop_full_estoque",
    "_classificar_unidade_virtual_devolucao", "_sql_filtro_unidade_devolucao",
    "_sql_filtro_loja_notas_entrada", "_bling_obter_numero_nf", "_sql_filtro_unidade_com_mapa",
    "_sql_filtro_loja_vendas", "_get_vendas_db_path", "_listar_bancos_vendas_tenant",
    "_deduplicar_vendas_consolidadas", "_deve_excluir_venda_ebazar", "_get_vendas_db",
    "_bling_listar_vendas", "_bling_listar_naturezas", "_normalizar_texto",
    "_bling_obter_detalhes_nf", "_extrair_codigo_origem_nf", "_bling_listar_notas_entrada",
    "_bling_listar_vendas_fallback_nf_saida", "_get_notas_entrada_db",
]
