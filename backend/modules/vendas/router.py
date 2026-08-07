"""FastAPI transport for the isolated Vendas domain."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.schemas import VendasSyncRequest

from .dependencies import VendasModuleDependencies
from .errors import VendasDomainError
from .service import VendasService
from .sync_service import VendasSyncService


def _transport(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except VendasDomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=exc.headers) from exc


def create_vendas_router(
    service: VendasService,
    sync_service: VendasSyncService,
    dependencies: VendasModuleDependencies,
) -> APIRouter:
    router = APIRouter(tags=["vendas"])
    tenant_dependency = dependencies.get_tenant_id

    @router.get("/api/vendas/resumo", name="resumo_vendas")
    def resumo_vendas(
        data_inicio: str = None,
        data_fim: str = None,
        sku: str = None,
        loja: str = None,
        unidade_negocio: str = None,
        incluir_ebazar: bool = False,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.resumo_vendas, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, sku=sku, loja=loja, unidade_negocio=unidade_negocio, incluir_ebazar=incluir_ebazar)

    @router.get("/api/vendas", name="listar_vendas")
    def listar_vendas(
        data_inicio: str = None,
        data_fim: str = None,
        sku: str = None,
        loja: str = None,
        unidade_negocio: str = None,
        resolver_nf: bool = False,
        incluir_ebazar: bool = False,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.listar_vendas, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, sku=sku, loja=loja, unidade_negocio=unidade_negocio, resolver_nf=resolver_nf, incluir_ebazar=incluir_ebazar)

    @router.post(
        "/api/vendas/limpar-tudo",
        name="limpar_todos_bancos_vendas",
        description="Apaga todos os bancos de vendas do tenant (legado + segregados por loja).",
    )
    def limpar_todos_bancos_vendas(client_id: str = Depends(tenant_dependency)):
        return _transport(service.limpar_todos_bancos_vendas, client_id)

    @router.get("/api/vendas/todas", name="listar_vendas_todas")
    def listar_vendas_todas(client_id: str = Depends(tenant_dependency)):
        return _transport(service.listar_vendas_todas, client_id)

    @router.get(
        "/api/vendas/grafico",
        name="grafico_vendas",
        description="Endpoint para gerar dados de gráfico de vendas\nperiodo: 3m, 6m, 1a, 2a, max\nintervalo: dia, semana, mes\nsku: filtro opcional por SKU",
    )
    def grafico_vendas(
        periodo: str = "3m",
        intervalo: str = "dia",
        sku: str = None,
        data_inicio: str = None,
        data_fim: str = None,
        loja: str = None,
        unidade_negocio: str = None,
        mostrar_estoque_geral: bool = False,
        mostrar_estoque_sku: bool = False,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.grafico_vendas, periodo=periodo, intervalo=intervalo, sku=sku, data_inicio=data_inicio, data_fim=data_fim, loja=loja, unidade_negocio=unidade_negocio, mostrar_estoque_geral=mostrar_estoque_geral, mostrar_estoque_sku=mostrar_estoque_sku, client_id=client_id)

    @router.get(
        "/api/vendas/skus-sem-venda",
        name="skus_sem_venda",
        description="Retorna SKUs que não têm vendas há 30, 60 e 90 dias e que possuem estoque.\nAgrupa por períodos: ultimos_30_dias, ultimos_60_dias, ultimos_90_dias",
    )
    def skus_sem_venda(
        loja: str = None,
        unidade_negocio: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.skus_sem_venda, loja=loja, unidade_negocio=unidade_negocio, client_id=client_id)

    @router.get("/api/vendas/limites", name="limites_vendas")
    def limites_vendas(
        loja: str = None,
        unidade_negocio: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.limites_vendas, loja=loja, unidade_negocio=unidade_negocio, client_id=client_id)

    @router.get(
        "/api/vendas/relatorios/pareto-80",
        name="relatorio_pareto_80",
        description="Analisa os SKUs responsáveis por 80% do faturamento da conta selecionada.",
    )
    def relatorio_pareto_80(
        loja: str,
        periodo: str = "12m",
        data_inicio: str = None,
        data_fim: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(
            service.relatorio_pareto,
            client_id=client_id,
            loja=loja,
            periodo=periodo,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )

    @router.get(
        "/api/vendas/relatorios/pareto-80/exportar",
        name="exportar_relatorio_pareto_80",
        description="Exporta o Pareto 80% em XLSX ou PDF sem persistir arquivos no servidor.",
    )
    def exportar_relatorio_pareto_80(
        loja: str,
        formato: str,
        periodo: str = "12m",
        data_inicio: str = None,
        data_fim: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        content, filename, media_type = _transport(
            service.exportar_relatorio_pareto,
            client_id=client_id,
            loja=loja,
            formato=formato,
            periodo=periodo,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
        return StreamingResponse(
            io.BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get(
        "/api/vendas/relatorios/vendas-estoque-devolucoes",
        name="relatorio_geral_skus",
        description=(
            "Analisa vendas, estoque local e devoluções de todos os SKUs da conta selecionada."
        ),
    )
    def relatorio_geral_skus(
        loja: str,
        periodo: str = "12m",
        data_inicio: str = None,
        data_fim: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(
            service.relatorio_geral_skus,
            client_id=client_id,
            loja=loja,
            periodo=periodo,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )

    @router.get(
        "/api/vendas/relatorios/vendas-estoque-devolucoes/exportar",
        name="exportar_relatorio_geral_skus",
        description=(
            "Exporta vendas, estoque local e devoluções de todos os SKUs em XLSX ou PDF."
        ),
    )
    def exportar_relatorio_geral_skus(
        loja: str,
        formato: str,
        periodo: str = "12m",
        data_inicio: str = None,
        data_fim: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        content, filename, media_type = _transport(
            service.exportar_relatorio_geral_skus,
            client_id=client_id,
            loja=loja,
            formato=formato,
            periodo=periodo,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )
        return StreamingResponse(
            io.BytesIO(content),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/api/vendas/sync/cancel", name="cancelar_sincronizacao_vendas")
    def cancelar_sincronizacao_vendas(client_id: str = Depends(tenant_dependency)):
        return _transport(sync_service.cancelar, client_id)

    @router.get("/api/vendas/sync/progress", name="progresso_sincronizacao_vendas")
    def progresso_sincronizacao_vendas(client_id: str = Depends(tenant_dependency)):
        return _transport(sync_service.progresso, client_id)

    @router.post("/api/vendas/sync", name="sincronizar_vendas")
    def sincronizar_vendas(req: VendasSyncRequest, client_id: str = Depends(tenant_dependency)):
        return _transport(sync_service.iniciar, req, client_id)

    @router.get("/api/notas-entrada", name="listar_notas_entrada")
    def listar_notas_entrada(
        data_inicio: str = None,
        data_fim: str = None,
        agrupado: bool = False,
        unidade_negocio: str = None,
        loja: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.listar_notas_entrada, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, agrupado=agrupado, unidade_negocio=unidade_negocio, loja=loja)

    @router.get(
        "/api/notas-entrada/itens",
        name="listar_itens_devolucoes",
        description="Retorna todos os itens de devoluÃ§Ãµes (para cÃ¡lculo de totais)",
    )
    def listar_itens_devolucoes(
        data_inicio: str = None,
        data_fim: str = None,
        unidade_negocio: str = None,
        loja: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.listar_itens_devolucoes, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, unidade_negocio=unidade_negocio, loja=loja)

    @router.get("/api/notas-entrada/sku/{sku}", name="listar_notas_entrada_por_sku")
    def listar_notas_entrada_por_sku(
        sku: str,
        data_inicio: str = None,
        data_fim: str = None,
        unidade_negocio: str = None,
        loja: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.listar_notas_entrada_por_sku, sku=sku, client_id=client_id, data_inicio=data_inicio, data_fim=data_fim, unidade_negocio=unidade_negocio, loja=loja)

    @router.get(
        "/api/unidades-negocios",
        name="listar_unidades_negocios",
        description="Lista as unidades de negócio e seus nomes configurados",
    )
    def listar_unidades_negocios(
        loja: str = None,
        sku: str = None,
        data_inicio: str = None,
        data_fim: str = None,
        client_id: str = Depends(tenant_dependency),
    ):
        return _transport(service.listar_unidades_negocios, loja=loja, sku=sku, data_inicio=data_inicio, data_fim=data_fim, client_id=client_id)

    @router.post(
        "/api/unidades-negocios/atualizar",
        name="atualizar_unidade_negocio",
        description="Atualiza o nome de uma unidade de negócio no banco de dados",
    )
    def atualizar_unidade_negocio(nome_antigo: str, nome_novo: str, client_id: str = Depends(tenant_dependency)):
        return _transport(service.atualizar_unidade_negocio, nome_antigo=nome_antigo, nome_novo=nome_novo, client_id=client_id)

    @router.post(
        "/api/unidades-negocios/mapeamento",
        name="salvar_mapeamento_unidades",
        description="Salva o mapeamento de IDs para nomes de unidades",
    )
    def salvar_mapeamento_unidades(mapeamento: dict, client_id: str = Depends(tenant_dependency)):
        return _transport(service.salvar_mapeamento_unidades, mapeamento=mapeamento, client_id=client_id)

    return router


__all__ = ["create_vendas_router"]
