"""Named public API for Perguntas/Pós-venda HTTP handlers."""

from backend.modules.perguntas_pos_venda.endpoints.approvals import (
    ml_perguntas_aprovacoes_aprovar,
    ml_perguntas_aprovacoes_listar,
    ml_perguntas_aprovacoes_rejeitar,
)
from backend.modules.perguntas_pos_venda.endpoints.jobs import (
    ml_customer_reply_job_cancel,
    ml_customer_reply_job_status,
    ml_customer_reply_solicitacoes_list,
)
from backend.modules.perguntas_pos_venda.endpoints.manual_questions import (
    ml_perguntas_gerar_resposta_manual,
    ml_perguntas_responder_manual,
)
from backend.modules.perguntas_pos_venda.endpoints.post_sale_actions import (
    ml_pos_venda_gerar_resposta_conversa,
    ml_pos_venda_responder_conversa,
)
from backend.modules.perguntas_pos_venda.endpoints.post_sale_automation import (
    ml_pos_venda_automacao_poll,
)
from backend.modules.perguntas_pos_venda.endpoints.post_sale_queries import (
    ml_pos_venda_detalhe_conversa,
    ml_pos_venda_listar_conversas,
    ml_pos_venda_listar_mediacoes,
    ml_pos_venda_obter_anexo,
)
from backend.modules.perguntas_pos_venda.endpoints.question_automation import (
    ml_perguntas_automacao_poll,
)
from backend.modules.perguntas_pos_venda.endpoints.questions_listing import ml_listar_perguntas
from backend.modules.perguntas_pos_venda.endpoints.questions_loading import (
    ml_perguntas_lista_rapida,
    ml_perguntas_itens_rapidos,
    ml_perguntas_detalhe_rapido,
    ml_perguntas_resumo_rapido,
)
from backend.modules.perguntas_pos_venda.endpoints.questions_v2 import (
    ml_questions_v2_audit_question,
    ml_questions_v2_metrics,
    ml_questions_v2_pending_review,
    ml_questions_v2_process,
    ml_questions_v2_review_approve,
    ml_questions_v2_review_reject,
    ml_questions_v2_webhook,
)
from backend.modules.perguntas_pos_venda.endpoints.store_config import (
    ml_perguntas_automacao_status,
    ml_perguntas_listar_lojas,
    ml_perguntas_salvar_config_loja,
    ml_perguntas_salvar_config_lojas_lote,
)
from backend.modules.perguntas_pos_venda.endpoints.training import (
    ml_ia_treinamento_listar_skus,
    ml_ia_treinamento_obter,
    ml_ia_treinamento_salvar,
    ml_ia_treinamento_simular,
)


__all__ = [
    "ml_perguntas_lista_rapida",
    "ml_perguntas_itens_rapidos",
    "ml_perguntas_detalhe_rapido",
    "ml_perguntas_resumo_rapido",
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
]
