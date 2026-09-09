"""Stable contracts and governance limits for Perguntas/Pós-venda endpoints."""

from __future__ import annotations


PERGUNTAS_POS_VENDA_ENDPOINTS: tuple[str, ...] = (
    "ml_perguntas_listar_lojas",
    "ml_perguntas_salvar_config_loja",
    "ml_perguntas_salvar_config_lojas_lote",
    "ml_perguntas_automacao_status",
    "ml_perguntas_automacao_poll",
    "ml_perguntas_aprovacoes_listar",
    "ml_perguntas_aprovacoes_aprovar",
    "ml_perguntas_aprovacoes_rejeitar",
    "ml_questions_v2_webhook",
    "ml_questions_v2_process",
    "ml_questions_v2_pending_review",
    "ml_questions_v2_review_approve",
    "ml_questions_v2_review_reject",
    "ml_questions_v2_audit_question",
    "ml_questions_v2_metrics",
    "ml_perguntas_gerar_resposta_manual",
    "ml_perguntas_responder_manual",
    "ml_ia_treinamento_sincronizacao_obter",
    "ml_ia_treinamento_sincronizacao_solicitar",
    "ml_ia_treinamento_obter",
    "ml_ia_treinamento_salvar",
    "ml_ia_treinamento_listar_skus",
    "ml_ia_treinamento_simular",
    "ml_pos_venda_listar_conversas",
    "ml_pos_venda_listar_mediacoes",
    "ml_pos_venda_detalhe_conversa",
    "ml_pos_venda_obter_anexo",
    "ml_pos_venda_gerar_resposta_conversa",
    "ml_pos_venda_responder_conversa",
    "ml_pos_venda_automacao_poll",
    "ml_customer_reply_job_status",
    "ml_customer_reply_job_cancel",
    "ml_customer_reply_solicitacoes_list",
    "ml_listar_perguntas",
)

_ML_POS_VENDA_RECENT_DAYS = 30
_ML_POS_VENDA_SYNC_TTL_SECONDS = 300
_ML_POS_VENDA_MAX_MESSAGE_WORKERS = 3
_ML_POS_VENDA_REMOTE_CURSOR_LIMIT = 10_000
_PERGUNTAS_AUTOMACAO_PAGE_LIMIT = 50
_PERGUNTAS_AUTOMACAO_MAX_PAGES = 2000
_PERGUNTAS_AUTOMACAO_PROCESS_BATCH = 50
