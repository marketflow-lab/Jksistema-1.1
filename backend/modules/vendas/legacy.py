"""Explicit adapter boundary for shared Vendas helpers."""

from __future__ import annotations

from .dependencies import legacy_call, migrate_legacy_file


def _delegate(helper_name: str):
    def delegated(*args, **kwargs):
        return legacy_call(helper_name, *args, **kwargs)

    delegated.__name__ = helper_name
    delegated.__qualname__ = helper_name
    return delegated


buscar_loja = _delegate("buscar_loja")
atualizar_api_loja = _delegate("atualizar_api_loja")
_normalizar_sku_estoque = _delegate("_normalizar_sku_estoque")
_vendas_series_estoque_historico = _delegate("_vendas_series_estoque_historico")

_bling_refresh_token = _delegate("_bling_refresh_token")
_bling_marcar_oauth_invalido = _delegate("_bling_marcar_oauth_invalido")
_bling_renovar_token_loja = _delegate("_bling_renovar_token_loja")
_bling_executar_com_refresh = _delegate("_bling_executar_com_refresh")
_carregar_mapeamento_unidades = _delegate("_carregar_mapeamento_unidades")
_salvar_mapeamento_unidades = _delegate("_salvar_mapeamento_unidades")
_normalizar_nome_loja_virtual_candidato = _delegate("_normalizar_nome_loja_virtual_candidato")
_carregar_mapeamento_lojas_virtuais_cliente = _delegate("_carregar_mapeamento_lojas_virtuais_cliente")
_resolver_nome_loja_virtual = _delegate("_resolver_nome_loja_virtual")
_normalizar_unidade_negocio_ml = _delegate("_normalizar_unidade_negocio_ml")
_normalizar_unidade_devolucao_entrada = _delegate("_normalizar_unidade_devolucao_entrada")
_eh_devolucao_nota_entrada = _delegate("_eh_devolucao_nota_entrada")
_eh_devolucao_por_cfop_itens = _delegate("_eh_devolucao_por_cfop_itens")
_tipo_devolucao_cfop_full_estoque = _delegate("_tipo_devolucao_cfop_full_estoque")
_classificar_unidade_virtual_devolucao = _delegate("_classificar_unidade_virtual_devolucao")
_sql_filtro_unidade_devolucao = _delegate("_sql_filtro_unidade_devolucao")
_sql_filtro_loja_notas_entrada = _delegate("_sql_filtro_loja_notas_entrada")
_bling_obter_numero_nf = _delegate("_bling_obter_numero_nf")
_sql_filtro_unidade_com_mapa = _delegate("_sql_filtro_unidade_com_mapa")
_sql_filtro_loja_vendas = _delegate("_sql_filtro_loja_vendas")
_get_vendas_db_path = _delegate("_get_vendas_db_path")
_listar_bancos_vendas_tenant = _delegate("_listar_bancos_vendas_tenant")
_deduplicar_vendas_consolidadas = _delegate("_deduplicar_vendas_consolidadas")
_deve_excluir_venda_ebazar = _delegate("_deve_excluir_venda_ebazar")
_get_vendas_db = _delegate("_get_vendas_db")
_bling_listar_vendas = _delegate("_bling_listar_vendas")
_bling_listar_naturezas = _delegate("_bling_listar_naturezas")
_normalizar_texto = _delegate("_normalizar_texto")
_bling_obter_detalhes_nf = _delegate("_bling_obter_detalhes_nf")
_extrair_codigo_origem_nf = _delegate("_extrair_codigo_origem_nf")
_bling_listar_notas_entrada = _delegate("_bling_listar_notas_entrada")
_bling_listar_vendas_fallback_nf_saida = _delegate("_bling_listar_vendas_fallback_nf_saida")
_get_notas_entrada_db = _delegate("_get_notas_entrada_db")


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
