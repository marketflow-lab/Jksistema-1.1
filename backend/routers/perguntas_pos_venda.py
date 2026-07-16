"""Mercado Livre questions, after-sales, and AI training router definitions.

The endpoint implementations are still in backend_api.py while the Mercado
Livre support pipeline is untangled. This module owns the route table so the
monolith no longer registers these routes directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from backend.services import perguntas_pos_venda_endpoints


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str


LEGACY_PERGUNTAS_POS_VENDA_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/mercadolivre/perguntas/lojas", "ml_perguntas_listar_lojas"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/lojas/config", "ml_perguntas_salvar_config_loja"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/lojas/config-lote", "ml_perguntas_salvar_config_lojas_lote"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/automacao/poll", "ml_perguntas_automacao_poll"),
    LegacyRouteSpec("GET", "/api/mercadolivre/perguntas/aprovacoes", "ml_perguntas_aprovacoes_listar"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/aprovacoes/aprovar", "ml_perguntas_aprovacoes_aprovar"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/aprovacoes/rejeitar", "ml_perguntas_aprovacoes_rejeitar"),
    LegacyRouteSpec("POST", "/webhooks/mercadolivre", "ml_questions_v2_webhook"),
    LegacyRouteSpec("POST", "/questions/{question_id}/process", "ml_questions_v2_process"),
    LegacyRouteSpec("GET", "/questions/pending-review", "ml_questions_v2_pending_review"),
    LegacyRouteSpec("POST", "/reviews/{approval_id}/approve", "ml_questions_v2_review_approve"),
    LegacyRouteSpec("POST", "/reviews/{approval_id}/reject", "ml_questions_v2_review_reject"),
    LegacyRouteSpec("GET", "/audit/questions/{question_id}", "ml_questions_v2_audit_question"),
    LegacyRouteSpec("GET", "/metrics/ai-questions", "ml_questions_v2_metrics"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/resposta/gerar", "ml_perguntas_gerar_resposta_manual"),
    LegacyRouteSpec("POST", "/api/mercadolivre/perguntas/responder", "ml_perguntas_responder_manual"),
    LegacyRouteSpec("GET", "/api/mercadolivre/ia-treinamento", "ml_ia_treinamento_obter"),
    LegacyRouteSpec("POST", "/api/mercadolivre/ia-treinamento", "ml_ia_treinamento_salvar"),
    LegacyRouteSpec("GET", "/api/mercadolivre/ia-treinamento/skus", "ml_ia_treinamento_listar_skus"),
    LegacyRouteSpec("POST", "/api/mercadolivre/ia-treinamento/simular", "ml_ia_treinamento_simular"),
    LegacyRouteSpec("GET", "/api/mercadolivre/pos-venda/conversas", "ml_pos_venda_listar_conversas"),
    LegacyRouteSpec("GET", "/api/mercadolivre/pos-venda/mediacoes", "ml_pos_venda_listar_mediacoes"),
    LegacyRouteSpec("GET", "/api/mercadolivre/pos-venda/conversas/detalhe", "ml_pos_venda_detalhe_conversa"),
    LegacyRouteSpec("GET", "/api/mercadolivre/pos-venda/anexos/{attachment_id}", "ml_pos_venda_obter_anexo"),
    LegacyRouteSpec("POST", "/api/mercadolivre/pos-venda/conversas/gerar-resposta", "ml_pos_venda_gerar_resposta_conversa"),
    LegacyRouteSpec("POST", "/api/mercadolivre/pos-venda/conversas/responder", "ml_pos_venda_responder_conversa"),
    LegacyRouteSpec("POST", "/api/mercadolivre/pos-venda/automacao/poll", "ml_pos_venda_automacao_poll"),
    LegacyRouteSpec("GET", "/api/mercadolivre/assistant/jobs/{job_id}", "ml_customer_reply_job_status"),
    LegacyRouteSpec("POST", "/api/mercadolivre/assistant/jobs/{job_id}/cancel", "ml_customer_reply_job_cancel"),
    LegacyRouteSpec("GET", "/api/mercadolivre/perguntas", "ml_listar_perguntas"),
)


router = APIRouter(tags=["perguntas-pos-venda"])


def create_perguntas_pos_venda_router() -> APIRouter:
    perguntas_pos_venda_router = APIRouter(tags=["perguntas-pos-venda"])

    for spec in LEGACY_PERGUNTAS_POS_VENDA_ROUTES:
        endpoint = getattr(perguntas_pos_venda_endpoints, spec.endpoint_name)
        perguntas_pos_venda_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
        )

    return perguntas_pos_venda_router
