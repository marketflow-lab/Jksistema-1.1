"""Compatibility facade for perguntas_pos_venda_core."""

from __future__ import annotations

from backend.modules.perguntas_pos_venda.ai import api as perguntas_agent_api
from backend.services import perguntas_pos_venda_state as _module_0
from backend.services import perguntas_pos_venda_perguntas_ml as _module_2
from backend.services import perguntas_pos_venda_pos_venda as _module_3
from backend.services import perguntas_pos_venda_automacao as _module_4
from backend.services.perguntas_pos_venda_state import *
from backend.services.perguntas_pos_venda_perguntas_ml import *
from backend.services.perguntas_pos_venda_pos_venda import *
from backend.services.perguntas_pos_venda_automacao import *

_MODULES = (
    _module_0,
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


def configure_perguntas_pos_venda_core_runtime(runtime_module=None, peers=None):
    combined = dict(peers or {})
    perguntas_agent_api.configure_perguntas_pos_venda_agent_runtime(runtime_module, combined)
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


__all__ = ['ML_RESPOSTA_PERGUNTA_MAX_CHARS', 'ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO', 'ML_PERGUNTAS_IA_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_DESCRICAO_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS', 'ML_PERGUNTAS_IA_CONTEXTO_EXTRA_PROMPT_MAX_CHARS', 'ML_PERGUNTAS_IA_MEMORIA_SKU_MIN_BYTES', 'ML_PERGUNTAS_IA_MEMORIA_SKU_MAX_EVENTOS', 'ML_PERGUNTAS_IA_MEMORIA_SKU_PROMPT_MAX_CHARS', 'IA_CHAT_MESSAGE_MAX_CHARS', 'IA_CHAT_MESSAGE_COMPACT_TARGET_CHARS', 'ML_POS_VENDA_DEFAULT_MAX_CHARS', 'ML_POS_VENDA_LIMITE_SEGURO', 'PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN', 'PERGUNTAS_AUTOMACAO_INTERVALO_MIN', 'PERGUNTAS_AUTOMACAO_INTERVALO_MAX', 'PERGUNTAS_AUTOMACAO_BG_LOCK', 'PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED', 'PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS', 'PERGUNTAS_AUTOMACAO_BG_RUNNING', 'PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS', 'PERGUNTAS_IA_MEMORIA_SKU_LOCK', '_perguntas_loja_config_path', '_perguntas_loja_configs_carregar', '_perguntas_loja_config_normalizar', '_perguntas_loja_config_obter', '_perguntas_loja_config_salvar', '_perguntas_ia_state_path', '_perguntas_ia_aprovacoes_path', '_ml_questions_v2_webhook_events_path', '_perguntas_ia_ler_json', '_perguntas_ia_salvar_json', '_perguntas_ia_state_carregar', '_perguntas_ia_state_salvar', '_perguntas_ia_aprovacoes_carregar', '_perguntas_ia_aprovacoes_salvar', '_perguntas_ia_aprovacao_id', '_pos_venda_ia_aprovacao_id', '_perguntas_ia_marcar_processada', '_perguntas_ia_ja_processada', '_perguntas_ia_aprovacao_pendente', '_perguntas_ia_resolver_aprovacao', '_perguntas_ia_resolver_aprovacoes_pendentes', '_perguntas_ia_pergunta_respondida_ml', '_ml_pos_venda_conversa_respondida_pela_loja', '_perguntas_ia_limpar_resposta', '_perguntas_ia_assinatura_loja', '_perguntas_ia_remover_apresentacao_sistema', '_perguntas_ia_resposta_final_loja', 'PerguntasIARespostaIndisponivel', '_perguntas_ia_resposta_fallback_invalida', 'ML_PERGUNTAS_IA_INTENCOES', 'ML_PERGUNTAS_IA_INTENCOES_POS_VENDA', '_perguntas_ia_intencao_fluxo', '_perguntas_ia_intencao_heuristica', '_perguntas_ia_json_obj', '_perguntas_ia_intencao_normalizar', '_perguntas_ia_classificar_intencao', '_perguntas_ia_intencao_agent', '_perguntas_ia_fluxo_pos_venda', '_perguntas_ia_compactar_contexto', '_perguntas_ia_limitar_prompt', '_perguntas_ia_memoria_sku_limite_bytes', '_perguntas_ia_memoria_sku_normalizar', '_perguntas_ia_memoria_sku_de_fontes', '_perguntas_ia_memoria_sku_dir', '_perguntas_ia_memoria_sku_path', '_perguntas_ia_memoria_payload_vazio', '_perguntas_ia_memoria_normalizar', '_perguntas_ia_memoria_carregar', '_perguntas_ia_memoria_bytes', '_perguntas_ia_memoria_salvar', '_perguntas_ia_memoria_resumir_matches', '_perguntas_ia_memoria_resumir_tool_results', '_perguntas_ia_memoria_evento_base', '_perguntas_ia_memoria_compactar_local', '_perguntas_ia_memoria_compactar_com_ia', '_perguntas_ia_memoria_garantir_limite', '_perguntas_ia_memoria_registrar_evento', '_perguntas_ia_memoria_registrar_pesquisa', '_perguntas_ia_memoria_registrar_resposta_aprovada', '_perguntas_ia_memoria_bloco_prompt', '_ml_pos_venda_memoria_items', '_ml_pos_venda_memoria_ultima_mensagem', '_ml_pos_venda_memoria_historico', '_ml_pos_venda_perguntas_anuncio_chat', '_ml_pos_venda_memoria_question_id', '_ml_pos_venda_memoria_bloco_prompt', '_ml_pos_venda_memoria_registrar_evento', '_ml_pos_venda_memoria_registrar_geracao', '_ml_pos_venda_memoria_registrar_resposta_enviada', '_ia_agent_extrair_texto', '_ia_agent_engine_query_url', '_ia_agent_endpoint_query_url', '_ia_agent_endpoint_headers', '_ia_agent_http_post', '_perguntas_ia_item_para_agente', '_perguntas_ia_pergunta_para_agente', '_perguntas_ia_mensagens_aprovacao', '_perguntas_ia_agent_input', '_perguntas_ia_chamar_agente_cloud', '_ia_agent_endpoint_api_key_configurada', '_ia_agent_endpoint_autorizar', '_ia_agent_input_dict', '_ia_agent_perguntas_texto_busca', '_ia_agent_perguntas_precisa_web', '_ia_agent_perguntas_adicionar_parte_busca', '_ia_agent_perguntas_query_web', '_ia_agent_perguntas_valor_codigo_web', '_ia_agent_perguntas_codigo_norm_web', '_ia_agent_perguntas_adicionar_codigo_web', '_ia_agent_perguntas_match_relevante_web', '_ia_agent_perguntas_codigos_web', '_ia_agent_perguntas_slug_link_produto', '_ia_agent_perguntas_queries_identificacao_produto', '_ia_agent_perguntas_queries_web', '_ia_agent_perguntas_relaxar_query_web', '_ia_agent_perguntas_query_ml_publica', '_ia_agent_perguntas_anuncios_publicos_ml', '_ia_agent_perguntas_anuncios_ml_autenticado', '_ia_agent_perguntas_contexto_web', '_ia_agent_perguntas_web_tool', '_ia_agent_perguntas_product_identity_web_tool', '_ia_agent_perguntas_tools_timeout_s', '_ia_agent_perguntas_tool_error', '_ia_agent_perguntas_perf_meta', '_ia_agent_perguntas_log_perf', '_ia_agent_perguntas_perf_etapa_tool', '_ia_agent_perguntas_preparar_tools', '_ia_agent_perguntas_montar_prompt', '_ia_agent_perguntas_chamar_modelo', 'ML_PERGUNTAS_IA_TERMOS_VEICULO', 'ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS', '_ia_agent_perguntas_termos_contexto', '_ia_agent_perguntas_texto_fonte', '_ia_agent_perguntas_codigos_modelo', '_ia_agent_perguntas_codigos_modelo_tem_match', '_ia_agent_perguntas_conectores', '_ia_agent_perguntas_resposta_pede_chassi', '_ia_agent_perguntas_pede_conector', '_ia_agent_perguntas_violacoes_resposta', 'ML_PERGUNTAS_IA_V2_MODO', 'ML_POS_VENDA_IA_V2_MODO', '_perguntas_ia_v2_exigir_aprovacao', '_pos_venda_ia_v2_exigir_aprovacao', '_perguntas_ia_v2_query_pesquisa', '_PerguntasVertexGeminiV2Client', '_perguntas_ia_v2_prompt', '_perguntas_ia_v2_resposta_segura_compatibilidade', '_perguntas_ia_v2_corrigir_resposta_bloqueada', '_perguntas_ia_v2_gerar_resposta', '_ia_agent_perguntas_gerar_resposta_legado_desativado', '_pos_venda_ia_limpar_resposta', '_pos_venda_ia_resposta_final_loja', 'ML_POS_VENDA_PIPELINE_V2_MODO', 'ML_POS_VENDA_PIPELINE_ETAPAS', '_ml_pos_venda_pipeline_base', '_ml_pos_venda_pipeline_marcar', '_ml_pos_venda_auditoria_path', '_ml_pos_venda_auditoria_compactar', '_ml_pos_venda_auditoria_registrar', '_ml_pos_venda_texto_norm', '_ml_pos_venda_ultima_mensagem_comprador', '_ml_pos_venda_resumir_pagamento', '_ml_pos_venda_shipping_id', '_ml_pos_venda_status_envio_base', '_ml_pos_venda_buscar_status_envio', '_ml_pos_venda_resumir_anuncio_para_ia', '_ml_pos_venda_buscar_dados_anuncios', '_ml_pos_venda_buscar_nota_fiscal_local', '_ml_pos_venda_buscar_reclamacao_pedido', '_ml_pos_venda_classificar_motivo', '_ml_pos_venda_decidir_regras_oficiais', '_ml_pos_venda_decidir_automatizacao', '_ml_pos_venda_montar_contexto_pipeline', '_ml_pos_venda_contexto_prompt', '_ml_pos_venda_validar_resposta', '_ml_pos_venda_pipeline_resumo', '_ml_pos_venda_executar_pipeline_ia', '_perguntas_ia_descricao_item', '_perguntas_ia_indica_busca_outra_peca', '_perguntas_ia_query_peca', '_perguntas_ia_score_texto', '_perguntas_ia_buscar_cadastro_peca', '_perguntas_ia_resumir_item_ml', '_perguntas_ia_buscar_anuncios_ml_peca', '_perguntas_ia_contexto_outra_peca', '_perguntas_ia_gerar_resposta', '_perguntas_ia_enviar_resposta_ml', '_ml_perguntas_resumir_status', '_ml_perguntas_tempo_resposta', '_ml_perguntas_foto_item', '_ml_perguntas_nome_comprador', '_ml_perguntas_buscar_usuarios', '_ml_perguntas_normalizar', '_ml_perguntas_completar_skus_itens', '_ml_perguntas_chave_historico', '_ml_perguntas_copia_historico', '_ml_perguntas_montar_chat_historico', '_ml_perguntas_anexar_historico_comprador', '_ml_pos_venda_anexar_perguntas_anuncio_comprador', '_ml_pos_venda_data_iso', '_ml_pos_venda_normalizar_texto_mensagem', '_ml_pos_venda_id_mensagem', '_ml_pos_venda_mensagem_data', '_ml_pos_venda_from_id', '_ml_pos_venda_flag_verdadeira', '_ml_pos_venda_flag_falsa', '_ml_pos_venda_inteiro_positivo', '_ml_pos_venda_campo_numero_nao_lidas', '_ml_pos_venda_dados_indicam_nao_lida', '_ml_pos_venda_mensagem_indica_lida', '_ml_pos_venda_mensagem_eh_comprador', '_ml_pos_venda_mensagem_nao_lida', '_ml_pos_venda_conversa_nao_lida', '_ml_pos_venda_contar_nao_lidas', '_ml_pos_venda_anexo_url', '_ml_pos_venda_mensagem_anexos', '_ml_pos_venda_normalizar_mensagens', '_ml_pos_venda_normalizar_pedido', '_ml_pos_venda_normalizar_termo_busca', '_ml_pos_venda_conversa_corresponde_busca', '_ml_pos_venda_pedido_corresponde_busca', '_ml_mediacao_player_id', '_ml_mediacao_order_id', '_ML_MEDIACAO_REASON_CACHE', '_ML_MEDIACAO_REASON_CACHE_TTL', '_ml_mediacao_texto_motivo', '_ml_mediacao_motivo_generico_por_codigo', '_ml_mediacao_buscar_motivos_claims', '_ml_mediacao_normalizar_claim', '_ml_pos_venda_buscar_mensagens_pack', '_ml_pos_venda_descobrir_buyer_id', 'ML_POS_VENDA_AGENT_USER_IDS', '_ml_pos_venda_resolver_site_id', '_ml_pos_venda_destinatarios_mensagem', '_ml_pos_venda_enviar_resposta_ml', '_ml_pos_venda_gerar_resposta_ia', '_ml_pos_venda_buscar_pedido', '_ml_pos_venda_item_ids_pedido', '_ml_pos_venda_montar_conversa_normalizada', '_ml_pos_venda_preparar_conversa_ia', '_perguntas_automacao_bg_tenants', '_perguntas_automacao_bg_key', '_perguntas_automacao_bg_marcar_inicio', '_perguntas_automacao_bg_finalizar', '_perguntas_automacao_bg_executar', '_perguntas_automacao_bg_tick', '_perguntas_automacao_bg_worker', '_perguntas_automacao_iniciar_background', 'configure_perguntas_pos_venda_core_runtime']
_AGENT_PRIVATE_EXPORTS = {
    '_perguntas_ia_mensagens_aprovacao', '_perguntas_ia_agent_input', '_perguntas_ia_chamar_agente_cloud', '_ia_agent_endpoint_autorizar',
    '_ia_agent_input_dict', '_ia_agent_perguntas_texto_busca', '_ia_agent_perguntas_precisa_web', '_ia_agent_perguntas_adicionar_parte_busca',
    '_ia_agent_perguntas_query_web', '_perguntas_ia_v2_texto_busca_curto', '_perguntas_ia_v2_alvo_compatibilidade', '_ia_agent_perguntas_valor_codigo_web',
    '_ia_agent_perguntas_codigo_norm_web', '_ia_agent_perguntas_adicionar_codigo_web', '_ia_agent_perguntas_match_relevante_web', '_ia_agent_perguntas_codigos_web',
    '_ia_agent_perguntas_slug_link_produto', '_ia_agent_perguntas_queries_identificacao_produto', '_ia_agent_perguntas_queries_web', '_ia_agent_perguntas_relaxar_query_web',
    '_ia_agent_perguntas_query_ml_publica', '_ia_agent_perguntas_anuncios_publicos_ml', '_ia_agent_perguntas_anuncios_ml_autenticado', '_ia_agent_perguntas_contexto_web',
    '_ia_agent_perguntas_web_tool', '_ia_agent_perguntas_product_identity_web_tool', '_ia_agent_perguntas_tools_timeout_s', '_ia_agent_perguntas_tool_error',
    '_ia_agent_perguntas_perf_meta', '_ia_agent_perguntas_log_perf', '_ia_agent_perguntas_perf_etapa_tool', '_ia_agent_perguntas_preparar_tools',
    '_ia_agent_perguntas_montar_prompt', '_ia_agent_perguntas_chamar_modelo', '_perguntas_codex_provider_selection', '_perguntas_codex_compact_json',
    'ML_PERGUNTAS_IA_TERMOS_VEICULO', 'ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS', '_ia_agent_perguntas_termos_contexto', '_ia_agent_perguntas_texto_fonte',
    '_ia_agent_perguntas_codigos_modelo', '_ia_agent_perguntas_codigos_modelo_tem_match', '_ia_agent_perguntas_conectores', '_ia_agent_perguntas_resposta_pede_chassi',
    '_ia_agent_perguntas_resposta_pede_foto', '_ia_agent_perguntas_recomenda_mecanico_generico', '_ia_agent_perguntas_pede_conector', '_ia_agent_perguntas_violacoes_resposta',
    'ML_PERGUNTAS_IA_V2_MODO', 'ML_POS_VENDA_IA_V2_MODO', '_perguntas_ia_v2_exigir_aprovacao', '_pos_venda_ia_v2_exigir_aprovacao',
    '_perguntas_ia_v2_query_pesquisa', '_perguntas_ia_v2_compatibilidade_padrao', '_perguntas_ia_v2_compatibilidade_normalizar', '_PerguntasVertexGeminiV2Client',
    '_PerguntasCodexV3Client', '_perguntas_ia_v2_prompt', '_perguntas_ia_v2_corrigir_resposta_bloqueada', '_perguntas_ia_v2_gerar_resposta',
    '_ia_agent_perguntas_gerar_resposta_legado_desativado', '_ml_pos_venda_contexto_prompt', '_ml_pos_venda_validar_resposta',
}
__all__ = [
    name for name in __all__
    if name not in _AGENT_PRIVATE_EXPORTS and name in globals()
]
if "configure_perguntas_pos_venda_core_runtime" not in __all__:
    __all__.append("configure_perguntas_pos_venda_core_runtime")

configure_perguntas_pos_venda_core_runtime()
