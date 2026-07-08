"""Compatibility facade for promocoes_core."""

from __future__ import annotations

from backend.services import promocoes_core_parsing as _module_0
from backend.services import promocoes_core_analise as _module_1
from backend.services import promocoes_core_custos as _module_2
from backend.services import promocoes_core_export as _module_3
from backend.services.promocoes_core_parsing import *
from backend.services.promocoes_core_analise import *
from backend.services.promocoes_core_custos import *
from backend.services.promocoes_core_export import *

_MODULES = (
    _module_0,
    _module_1,
    _module_2,
    _module_3,
)


def _peer_globals() -> dict[str, object]:
    peers = {}
    for module in _MODULES:
        for name in getattr(module, "PEER_EXPORTS", getattr(module, "__all__", ())):
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_promocoes_core_runtime(runtime_module=None, peers=None):
    combined = dict(peers or {})
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    combined.update(_peer_globals())
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    for name, value in combined.items():
        if name in __all__ or name.startswith("_"):
            globals()[name] = value
    return runtime_module


__all__ = ['TAXAS_FILE', 'configure_promocoes_core_runtime', 'DB_TAXAS_PADRAO', 'TAXA_PADRAO', 'COLUNAS_DO_MODELO_ANUNCIOS', 'COLUNAS_DO_MODELO_PROMO', 'COLUNAS_PLANILHA_ANALISE_PROMO', 'NOME_INFORMADO_POR_TIPO_PROMO', '_limpar_nome_coluna', '_extrair_percentual_de_formula_fee', '_materializar_fee_per_sale_como_valor', '_detectar_tipo_por_nome_informado', '_detectar_tipo_dataframe', '_detectar_subtipo_promo', '_rotulo_tipo_arquivo', '_nome_informado_tipo_arquivo', '_escolher_aba_excel_por_estrutura', 'carregar_id_planilha_sistema', 'carregar_taxas', 'salvar_taxas', '_normalizar_texto_str', 'normalizar_texto', 'renomear_colunas_duplicadas', 'formatar_moeda_br', 'obter_taxa_por_categoria', 'normalizar_df_anuncios', 'normalizar_df_promo', 'ler_e_tratar_arquivo', 'preparar_para_sheets', 'atualizar_aba', '_normalize_log_text', 'carregar_dados_analise', '_parse_float_str_cached', '_parse_float_flex', '_format_money_safe_cached', '_format_money_safe', '_format_pct_br_cached', '_format_pct_br', '_normalizar_decisao_local', '_build_col_index', '_pick_first_col', '_extract_monetary_from_df_row', '_build_col_index_optimized', '_preprocessar_todos_indices_otimizado', '_processar_row_consolidado', 'montar_analise_promo_local', '_sku_lookup_variantes', '_cadastro_norm_col_custo', '_cadastro_colunas_alias', '_cadastro_canonizar_colunas_custos', '_carregar_df_cadastro_custos', '_ler_df_cadastro_custos_arquivo', '_listar_arquivos_cadastro_custos', '_iterar_dfs_cadastro_custos', '_carregar_custos_cadastro_por_sku', '_carregar_impostos_cadastro_por_sku', '_carregar_custos_impostos_cadastro_por_sku_loja', '_resolver_custo_por_sku', '_extrair_skus_para_custo', '_resolver_custo_medio_por_skus', '_resolver_imposto_rate_por_sku', '_to_float_safe', '_to_rate_safe', 'limpar_dados_para_exportacao', '_normalizar_coluna_exportacao', '_valor_df_por_alias', '_normalizar_decisao_planilha', '_decisao_linha_exportacao', '_localizar_coluna_em_linha', '_localizar_cabecalho_exportacao', 'gerar_excel_atualizado']
if "configure_promocoes_core_runtime" not in __all__:
    __all__.append("configure_promocoes_core_runtime")

configure_promocoes_core_runtime()
