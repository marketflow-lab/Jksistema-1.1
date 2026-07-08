"""Renovacao API routes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.schemas.renovacao import (
    RenovacaoAgendamentoRequest,
    RenovacaoCampanhaCriarRequest,
    RenovacaoCampanhaExcluirRequest,
    RenovacaoCampanhaPeriodoRequest,
    RenovacaoCampanhaSincronizarRequest,
)
from backend.services.renovacao import analisar_renovacao_logic, gerar_excel_renovacao


@dataclass(frozen=True)
class RenovacaoRouterConfig:
    get_tenant_id: Callable
    listar_campanhas_usuario: Callable[..., dict]
    criar_ou_completar_proximo_mes: Callable[..., dict]
    atualizar_periodo_campanha: Callable[..., dict]
    deletar_campanha: Callable[..., dict]
    sincronizar_promocao_existente: Callable[..., dict]
    iniciar_sincronizacao_promocao: Callable[..., dict]
    sync_job_get: Callable[[str], dict]
    agendamento_atual: Callable[..., dict]
    agendamento_put: Callable[..., dict]


def create_renovacao_router(config: RenovacaoRouterConfig) -> APIRouter:
    router = APIRouter(tags=["renovacao"])

    @router.get("/api/renovacao/campanhas-usuario", name="renovacao_listar_campanhas_usuario")
    def renovacao_listar_campanhas_usuario(
        loja: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.listar_campanhas_usuario(client_id, loja)

    @router.post("/api/renovacao/criar-proximo-mes", name="renovacao_criar_proximo_mes")
    def renovacao_criar_proximo_mes(
        req: RenovacaoCampanhaCriarRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.criar_ou_completar_proximo_mes(
            client_id,
            req.loja,
            req.campanha_id,
            req.nome,
            req.promotion_type or "SELLER_CAMPAIGN",
        )

    @router.put("/api/renovacao/campanha-periodo", name="renovacao_alterar_periodo_campanha")
    def renovacao_alterar_periodo_campanha(
        req: RenovacaoCampanhaPeriodoRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.atualizar_periodo_campanha(
            client_id,
            req.loja,
            req.campanha_id,
            req.start_date,
            req.finish_date,
            req.promotion_type or "SELLER_CAMPAIGN",
            req.nome,
        )

    @router.delete("/api/renovacao/campanha", name="renovacao_deletar_campanha")
    def renovacao_deletar_campanha(
        req: RenovacaoCampanhaExcluirRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.deletar_campanha(
            client_id,
            req.loja,
            req.campanha_id,
            req.promotion_type or "SELLER_CAMPAIGN",
        )

    @router.post("/api/renovacao/sincronizar-promocao", name="renovacao_sincronizar_promocao")
    def renovacao_sincronizar_promocao(
        req: RenovacaoCampanhaSincronizarRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.sincronizar_promocao_existente(
            client_id,
            req.loja,
            req.campanha_origem_id,
            req.campanha_destino_id,
            req.promotion_type_origem or "SELLER_CAMPAIGN",
            req.promotion_type_destino or "SELLER_CAMPAIGN",
        )

    @router.post("/api/renovacao/sincronizar-promocao/iniciar", name="renovacao_sincronizar_promocao_iniciar")
    def renovacao_sincronizar_promocao_iniciar(
        req: RenovacaoCampanhaSincronizarRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.iniciar_sincronizacao_promocao(client_id, req)

    @router.get("/api/renovacao/sincronizar-promocao/progresso/{job_id}", name="renovacao_sincronizar_promocao_progresso")
    def renovacao_sincronizar_promocao_progresso(
        job_id: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        job = config.sync_job_get(job_id)
        if not job or job.get("client_id") != client_id:
            raise HTTPException(status_code=404, detail="Job de sincronizacao nao encontrado.")
        job.pop("client_id", None)
        return job

    @router.get("/api/renovacao/agendamento", name="renovacao_agendamento_get")
    def renovacao_agendamento_get(
        loja: str,
        campanha_id: str,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return {
            "success": True,
            "agendamento": config.agendamento_atual(client_id, loja, campanha_id),
        }

    @router.put("/api/renovacao/agendamento", name="renovacao_agendamento_put")
    def renovacao_agendamento_put(
        req: RenovacaoAgendamentoRequest,
        client_id: str = Depends(config.get_tenant_id),
    ):
        return config.agendamento_put(client_id, req)

    @router.post("/api/renovacao/analisar")
    async def renovacao_analisar_endpoint(
        antiga: UploadFile = File(...),
        nova: UploadFile = File(...),
        client_id: str = Depends(config.get_tenant_id),
    ):
        antiga_bytes = await antiga.read()
        nova_bytes = await nova.read()
        dados, erro = analisar_renovacao_logic(antiga_bytes, nova_bytes)
        if erro and dados is None:
            raise HTTPException(status_code=400, detail=erro)
        return {"success": True, "data": dados or []}

    @router.post("/api/renovacao/exportar")
    async def renovacao_exportar_endpoint(
        nova: UploadFile = File(...),
        decisoes: str = Form(...),
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            decisoes_list = json.loads(decisoes or "[]")
        except Exception:
            raise HTTPException(status_code=400, detail="Decisoes invalidas.")
        output, erro = gerar_excel_renovacao(await nova.read(), decisoes_list)
        if output is None:
            raise HTTPException(status_code=400, detail=erro)
        filename = nova.filename or "renovacao.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
