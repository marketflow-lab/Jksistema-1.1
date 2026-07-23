"""Compatibility facade for favoritos_core."""

from __future__ import annotations

from backend.services import favoritos_storage as _module_0
from backend.services import favoritos_ranking_ia as _module_1
from backend.services import favoritos_ml as _module_2
from backend.services import favoritos_extract as _module_3
from backend.services import favoritos_busca as _module_4
from backend.services.favoritos_storage import *
from backend.services.favoritos_ranking_ia import *
from backend.services.favoritos_ml import *
from backend.services.favoritos_extract import *
from backend.services.favoritos_busca import *

_MODULES = (
    _module_0,
    _module_1,
    _module_2,
    _module_3,
    _module_4,
)


def _peer_globals() -> dict[str, object]:
    peers = {}
    for module in _MODULES:
        for name in getattr(module, "PEER_EXPORTS", getattr(module, "__all__", ())):
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_favoritos_core_runtime(runtime_module=None, peers=None):
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


__all__ = ['_normalizar_sku_match_favoritos', '_chave_loja_favoritos', '_lojas_favoritos_com_bling_ml', '_lojas_favoritos_com_ml', '_favoritos_sku_norm_loja', '_favoritos_sku_float', '_favoritos_sku_pick', '_favoritos_limpar_nome_produto', '_cadastro_nome_esta_suspeito', '_cadastro_limpar_nome', '_favoritos_ler_cadastro_csv', 'CADASTRO_PESQUISA_COLS', 'CADASTRO_COLS_BASE', 'CADASTRO_DESCRICAO_COL_ALIASES', '_cadastro_cols_base', '_cadastro_norm_coluna_texto', '_cadastro_canonizar_coluna_descricao', '_cadastro_garantir_colunas_pesquisa', '_favoritos_sku_caminhos', '_favoritos_ml_skus_anuncios_cache_id', '_favoritos_ml_anuncios_sku_cache_id', '_favoritos_arquivo_pesquisas_usuario', '_favoritos_chave_pesquisa_usuario', '_favoritos_pesquisa_score', '_favoritos_escolher_pesquisa_usuario', '_favoritos_carregar_pesquisas_usuario', '_favoritos_salvar_pesquisas_usuario_batch', '_favoritos_enriquecer_pesquisas_usuario', '_favoritos_carregar_skus_ocultos', '_favoritos_salvar_skus_ocultos', '_favoritos_normalizar_vendedores_ignorados', '_favoritos_arquivo_vendedores_ignorados', '_favoritos_carregar_vendedores_ignorados', '_favoritos_salvar_vendedores_ignorados', '_favoritos_arquivo_anuncios_ignorados', '_favoritos_normalizar_anuncios_ignorados', '_favoritos_carregar_anuncios_ignorados', '_favoritos_salvar_anuncios_ignorados', '_favoritos_limpar_texto_historico', '_favoritos_numero_historico', '_favoritos_int_historico', '_favoritos_lista_texto_historico', 'FAVORITOS_HISTORICO_MAX', 'FAVORITOS_HISTORICO_ANUNCIOS_MAX', 'FAVORITOS_HISTORICO_REALTIME_SCOPE', '_favoritos_normalizar_historico', '_favoritos_arquivo_historico', '_favoritos_carregar_historico', '_favoritos_aplicar_usuario_padrao_historico', '_favoritos_salvar_historico', '_favoritos_usuario_pode_sync_historico', '_favoritos_usuarios_sync_historico', '_favoritos_historico_payload_bytes', '_favoritos_mesclar_historico_usuario_com_fontes', '_favoritos_propagar_historico_para_usuarios', '_favoritos_reconciliar_historico_usuario', '_favoritos_historico_realtime_event_path', '_favoritos_historico_publicar_evento_realtime', '_favoritos_carregar_cadastro_por_sku', '_favoritos_chaves_match_sku', '_favoritos_carregar_estoque_loja_por_sku', '_favoritos_ml_enriquecer_estoque_cadastro', '_favoritos_listar_skus_payload', '_favoritos_salvar_descricao_cadastro', '_favoritos_salvar_pesquisas_batch', '_favoritos_normalizar_texto_pesquisa', '_favoritos_codigo_compacto', '_favoritos_extrair_codigos_pesquisa', '_favoritos_codigo_pesquisa_2', '_favoritos_texto_contem_codigo', '_favoritos_normalizar_sem_acentos', '_favoritos_veiculo_marcas', '_favoritos_extrair_marca_global', '_favoritos_limpar_modelo_veiculo', '_favoritos_extrair_aplicacoes_veiculares', '_favoritos_aplicacao_texto', '_favoritos_pesquisa_tem_aplicacao', '_favoritos_garantir_aplicacao_pesquisa', '_favoritos_gerar_pesquisa_heuristica', '_favoritos_ia_texto_resposta', '_favoritos_ia_gemini_pesquisa_texto', '_favoritos_ia_vertex_pesquisa_texto', '_favoritos_ia_pesquisa_texto', '_favoritos_ia_extrair_json', '_favoritos_parse_resultado_ia', '_favoritos_limpar_resposta_campo_ia', '_favoritos_prompt_campo_pesquisa', '_favoritos_gerar_campo_pesquisa_ia', '_favoritos_gerar_pesquisas_ia', '_favoritos_ranking_anuncio_id', '_favoritos_ranking_descricao_anuncio', '_favoritos_ranking_buscar_descricao_publica', '_favoritos_ranking_completar_descricoes', 'FAVORITOS_RANKING_DECISOES_LOCK', 'FAVORITOS_RANKING_DECISAO_VERSAO', '_favoritos_ranking_decisoes_cache_path', '_favoritos_ranking_ler_decisoes_cache', '_favoritos_ranking_salvar_decisoes_cache', '_favoritos_ranking_texto_norm', '_favoritos_ranking_codigo_norm', '_favoritos_ranking_extrair_codigos', '_favoritos_ranking_extrair_anos', 'FAVORITOS_RANKING_MARCAS', 'FAVORITOS_RANKING_MODELOS', 'FAVORITOS_RANKING_PECAS', '_favoritos_ranking_extrair_campos_tecnicos', '_favoritos_ranking_assinatura_decisao', '_favoritos_ranking_decisao_cache_valida', '_favoritos_ranking_preavaliar', 'FAVORITOS_BUSCA_CACHE_LOCK', '_favoritos_busca_externa_limpar_texto', '_favoritos_busca_externa_cache_path', '_favoritos_busca_externa_ler_cache', '_favoritos_busca_externa_salvar_cache', '_favoritos_busca_externa_cache_key', '_favoritos_busca_externa_cache_valido', '_favoritos_busca_externa_normalizar_resultados', '_favoritos_busca_externa_chamar_api', '_favoritos_busca_externa_cached', '_favoritos_busca_externa_extrair_codigos', '_favoritos_busca_externa_query', '_favoritos_ranking_contexto_busca_externa', '_favoritos_ranking_json_obj', '_favoritos_ranking_lista_ids', '_favoritos_ranking_chamar_ia_json', '_favoritos_ranking_filtrar_com_ia', 'ML_FAVORITOS_STATUS_SKUS', '_ml_favoritos_buscar_itens_por_sku', '_ml_favoritos_buscar_primeiros_itens_por_skus', '_ml_favoritos_listar_itens_ativos_loja', '_ml_favoritos_listar_todos_itens_ativos_loja', '_favoritos_ml_dividir_skus', '_favoritos_ml_imagem_item', '_favoritos_ml_url_item_id', '_favoritos_ml_resumo_anuncio_sku', '_favoritos_ml_skus_unicos_itens', '_favoritos_ml_garantir_sku_busca', '_ml_favoritos_mapear_itens_ativos_por_skus', '_ml_favoritos_extrair_texto_descricao', '_ml_favoritos_montar_descricao_por_item', '_ml_favoritos_obter_descricao_item', '_ml_favoritos_obter_descricao_item_rapida', '_favoritos_ml_float_close', '_favoritos_ml_preco_ranking_simulado', '_favoritos_ml_preco_minimo_margem_simulado', '_favoritos_ml_preco_final_verificacao', '_favoritos_ml_margem_estimada', '_favoritos_ml_preco_contingencia_sem_promocao', '_favoritos_ml_verificacao_exige_contingencia_por_margem', '_favoritos_ml_falha_por_percentual_promocao', '_favoritos_ml_aplicar_contingencia_sem_promocao', '_favoritos_ml_texto_promocao', '_favoritos_ml_promocao_para_remocao', '_favoritos_ml_promocoes_remocao_fallback', '_favoritos_ml_remocao_max_attempts', '_favoritos_ml_textos_resposta_remocao', '_favoritos_ml_remocao_erro_transitorio', '_favoritos_ml_remocao_retry_delay', '_favoritos_ml_remover_promocoes_atuais', '_favoritos_ml_listing_type_id', '_favoritos_ml_nome_listing_type', '_favoritos_ml_troca_listing_type_favoritos_suportada', '_favoritos_ml_listing_type_alvo_req', '_favoritos_ml_obter_listing_type_atual_e_disponiveis', '_favoritos_ml_validar_listing_type_disponivel', '_favoritos_ml_atualizar_tipo_listing_item', '_favoritos_ml_atualizar_preco_item', '_favoritos_ml_aguardar_preco_anuncio', '_favoritos_ml_verificar_efetivacao', '_favoritos_taxa_padrao_por_tipo_anuncio', '_favoritos_resolver_sku_para_margem', '_favoritos_frete_existente_anuncio', '_favoritos_anuncio_tem_frete_gratis', '_favoritos_chaves_frete_anuncio', '_favoritos_completar_frete_pausados_por_sku', '_favoritos_aplicar_margem_anuncio_ml', '_ml_headers', '_normalize_text', '_normalizar_termo_busca_ml', '_termos_busca_ml', '_extrair_item_id', '_extrair_catalog_id', '_ml_api_get', '_ml_api_item', '_ml_api_items_multiget_tenant', '_ml_api_item_com_oauth_tenant', '_ml_api_user', '_ml_api_user_com_oauth_tenant', '_ml_total_visitas_payload', '_ml_api_visitas_com_oauth_tenant', '_ml_api_search', '_ml_api_search_paginated', '_ml_parcelamento_sem_juros_api', '_ml_parcelamento_sem_juros_texto', '_normalizar_data_ml', '_extrair_data_criacao_codigo_fonte', '_ml_data_sort_key', '_ml_primeira_pergunta_publica_data', '_ml_wayback_timestamp_iso', '_ml_wayback_primeira_captura_data', '_ml_normalizar_data_cache_local', '_ml_data_criacao_por_imagem', '_ml_datas_cache_local', '_ml_data_criacao_cache_local', '_extrair_vendedor_codigo_fonte', '_extrair_info_anuncio', '_meses_desde_date', '_estimar_meses_anuncio', '_buscar_anuncios_mercadolivre', 'BUSCAS_PARALLELAS_FAVORITOS', '_deduplicar_lista', '_buscar_anuncios_paralelo', '_buscar_anuncios_por_link', '_buscar_anuncios_por_termo', '_buscar_anuncios_mercadolivre_html', '_extrair_preloaded_results', '_extrair_links_ld_json', '_simplificar_termo_busca', '_deduplicar_anuncios', '_parse_vendas_ml', '_normalizar_nome_vendedor_ml', '_obter_info_anuncio_api', '_enriquecer_vendas_com_api', '_normalizar_anuncio_favoritos', '_buscar_lista_resultados', '_extrair_anuncio_produto', '_extrair_dados_produto_html', '_extrair_atributos_produto', '_extrair_preloaded_state', '_buscar_lista_atributos', '_extrair_codigo_produto', '_extrair_veiculo_produto', '_extrair_anos_produto', '_extrair_codigo_oem', '_gerar_termo_tecnico', '_buscar_ofertas_catalogo', '_coletar_permalinks', '_montar_urls_busca_diretas', '_buscar_anuncios_mercadolivre_automatico', 'configure_favoritos_core_runtime']
for _favoritos_estado_export in (
    "_favoritos_ml_resumir_estado_item",
    "_favoritos_ml_obter_estado_item",
):
    if _favoritos_estado_export not in __all__:
        __all__.append(_favoritos_estado_export)

if "configure_favoritos_core_runtime" not in __all__:
    __all__.append("configure_favoritos_core_runtime")

configure_favoritos_core_runtime()
