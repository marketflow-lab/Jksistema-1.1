"""Promocoes router definitions."""

from __future__ import annotations

from fastapi import APIRouter


def create_promocoes_router() -> APIRouter:
    from backend.services import promocoes_api

    router = APIRouter(tags=["promocoes"])
    router.add_api_route("/api/promo/analise-via-api", promocoes_api.analisar_promo_via_api, methods=["POST"], name="analisar_promo_via_api")
    router.add_api_route("/api/promo/analise-via-api-arquivos", promocoes_api.analisar_promo_via_api_com_arquivos, methods=["POST"], name="analisar_promo_via_api_com_arquivos")
    router.add_api_route("/api/promo/analise-via-api/start", promocoes_api.iniciar_analise_promo_via_api, methods=["POST"], name="iniciar_analise_promo_via_api")
    router.add_api_route("/api/promo/analise-via-api-arquivos/start", promocoes_api.iniciar_analise_promo_via_api_com_arquivos, methods=["POST"], name="iniciar_analise_promo_via_api_com_arquivos")
    router.add_api_route("/api/promo/analise-via-api-arquivos/progresso/{job_id}", promocoes_api.progresso_analise_promo_via_api_com_arquivos, methods=["GET"], name="progresso_analise_promo_via_api_com_arquivos")
    router.add_api_route("/api/promo/analise-via-api-arquivos/cancelar/{job_id}", promocoes_api.cancelar_analise_promo_via_api_com_arquivos, methods=["POST"], name="cancelar_analise_promo_via_api_com_arquivos")
    router.add_api_route("/api/promo/automacao", promocoes_api.promo_automacao_obter, methods=["GET"], name="promo_automacao_obter")
    router.add_api_route("/api/promo/automacao", promocoes_api.promo_automacao_salvar, methods=["PUT"], name="promo_automacao_salvar")
    router.add_api_route("/api/promo/aplicar-participacoes", promocoes_api.aplicar_participacoes_promocoes, methods=["POST"], name="aplicar_participacoes_promocoes")
    router.add_api_route("/api/promo/aplicar-participacoes/start", promocoes_api.aplicar_participacoes_promocoes_start, methods=["POST"], name="aplicar_participacoes_promocoes_start")
    router.add_api_route("/api/promo/aplicar-participacoes/jobs/{job_id}", promocoes_api.aplicar_participacoes_promocoes_job, methods=["GET"], name="aplicar_participacoes_promocoes_job")
    router.add_api_route("/api/promo/analise", promocoes_api.analisar_promo_automatico, methods=["GET"], name="analisar_promo_automatico")
    return router


__all__ = ["create_promocoes_router"]
