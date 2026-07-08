"""Compatibility facade for mercadolivre_legacy_core."""

from __future__ import annotations

from backend.services import mercadolivre_legacy_api as _module_0
from backend.services import mercadolivre_legacy_pricing as _module_1
from backend.services import mercadolivre_legacy_planilhas as _module_2
from backend.services import mercadolivre_legacy_promocoes as _module_3
from backend.services import mercadolivre_legacy_items as _module_4
from backend.services.mercadolivre_legacy_api import *
from backend.services.mercadolivre_legacy_pricing import *
from backend.services.mercadolivre_legacy_planilhas import *
from backend.services.mercadolivre_legacy_promocoes import *
from backend.services.mercadolivre_legacy_items import *

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


def configure_mercadolivre_legacy_core_runtime(runtime_module=None, peers=None):
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


__all__ = ['_ml_refresh_token', '_headers_ml', '_ml_oauth_status', '_ml_oauth_config_completa', '_ml_normalizar_oauth_compartilhado', '_ml_descobrir_user_id_oauth', '_obter_cfg_ml', '_ml_api_request', '_ml_api_request_com_retry', '_ml_parse_error_detail', '_ml_response_eh_rate_limit', '_ml_retry_after_seconds', '_ml_extrair_item_condition', '_ml_extrair_sku', '_ml_favoritos_variacao_tem_sku', '_ml_favoritos_item_precisa_variacoes_detalhadas', '_ml_favoritos_completar_variacoes_item', '_normalizar_sku_compacto_favoritos', '_ml_favoritos_variantes_busca_sku', '_ml_favoritos_extrair_skus_item', '_ml_favoritos_item_corresponde_sku', '_ml_favoritos_api_request', '_ml_favoritos_buscar_itens_batch', '_ml_carregar_produtos_locais', '_ml_buscar_sku_variacao_local', '_ml_extrair_variacoes_resumo', '_ml_obter_preco_detalhado', '_cache_get', '_cache_set', '_backend_cache_get', '_backend_cache_set', '_backend_cache_invalidate_prefix', '_backend_cache_invalidate_user_views', '_ml_obter_promocoes_item', '_ml_extrair_ids_promocoes_item', '_ml_encontrar_promocao_raw_item', '_promo_status_item_promocao', '_promo_prioridade_status_item', '_promo_entry_item_id', '_promo_erro_candidate_not_found', '_promo_consultar_item_na_campanha', '_ml_contexto_frete_item', '_ml_obter_frete_detalhado', '_ml_montar_preco_listagem', '_ml_nome_tipo_anuncio', '_ml_estimar_taxa_fixa_por_preco', '_ml_obter_taxas_anuncio', '_ml_buscar_itens_batch', '_ml_montar_detalhe_anuncio_listagem', '_calcular_desconto_ml_valor', '_ml_extrair_recebivel_promocao_raw', '_ml_calcular_recebivel_promocao', '_ml_ajustar_desconto_tarifa_recebivel_promocao', '_flag_frete_gratis', '_ajustar_frete_por_preco_base', 'ML_FRETE_GRADE_OFICIAL', '_ml_price_band_index', '_recalcular_frete_por_grade_oficial', '_recalcular_frete_por_faixa_ml', '_normalizar_sku_saida', '_build_df_planilha_analise_promo', '_salvar_planilha_analise_promo', '_promo_linha_status_ativo_ou_programado', '_promo_linha_pct_fixa_maior_que_zero', '_promo_meta_contagem', '_promo_total_esperado_por_contagens', '_ml_classificar_status_promocao_valor', '_ml_classificar_status_promocao_entry', '_ml_classificar_status_promocao_por_id', '_ml_status_promocao_usuario_exibicao', '_ml_iterar_campos_payload_limitado', '_ml_extrair_preco_promocao_raw', '_ml_calcular_percentual_desconto_por_preco', '_ml_extrair_percentual_total_direto_promocao_raw', '_ml_resolver_percentual_desconto_campanha_raw', '_ml_parse_percentual_promocao_texto', '_ml_extrair_percentual_sugerido_campanha_raw', '_ml_extrair_desconto_tarifa_promocao_raw', '_ml_extrair_tarifa_cobrada_promocao_raw', '_ml_obter_item_promocao_raw', '_ml_promocao_raw_texto', '_ml_promocao_raw_id', '_ml_promocao_raw_tipo', '_ml_promocao_raw_nome', '_ml_promocao_raw_offer_id', '_ml_grupo_promocao_por_meta', '_ml_grupo_promocao_raw', '_ml_resolver_raw_promocao_equivalente_para_analise', '_ml_promocao_raw_esta_ativa_ou_indefinida', '_ml_extrair_percentual_desconto_tarifa_promocao_raw', '_ml_extrair_valor_desconto_taxa_promocao_raw', '_ml_obter_desconto_taxa_promocao_item', '_ml_aplicar_desconto_taxa_promocao_fee_data', '_ml_listar_ids_anuncios_ativos', '_ml_listar_itens_promocao_com_raw', '_ml_listar_itens_promocao_multistatus_com_raw', 'configure_mercadolivre_legacy_core_runtime']
if "configure_mercadolivre_legacy_core_runtime" not in __all__:
    __all__.append("configure_mercadolivre_legacy_core_runtime")

configure_mercadolivre_legacy_core_runtime()
