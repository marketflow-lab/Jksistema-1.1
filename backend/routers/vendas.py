"""Vendas router definitions."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import vendas


router = APIRouter(tags=["vendas"])


def create_vendas_router() -> APIRouter:
    vendas_router = APIRouter(tags=["vendas"])

    vendas_router.add_api_route("/api/vendas/resumo", vendas.resumo_vendas, methods=["GET"], name="resumo_vendas")
    vendas_router.add_api_route("/api/vendas", vendas.listar_vendas, methods=["GET"], name="listar_vendas")
    vendas_router.add_api_route(
        "/api/vendas/limpar-tudo",
        vendas.limpar_todos_bancos_vendas,
        methods=["POST"],
        name="limpar_todos_bancos_vendas",
    )
    vendas_router.add_api_route("/api/vendas/todas", vendas.listar_vendas_todas, methods=["GET"], name="listar_vendas_todas")
    vendas_router.add_api_route("/api/vendas/grafico", vendas.grafico_vendas, methods=["GET"], name="grafico_vendas")
    vendas_router.add_api_route("/api/vendas/skus-sem-venda", vendas.skus_sem_venda, methods=["GET"], name="skus_sem_venda")
    vendas_router.add_api_route("/api/vendas/limites", vendas.limites_vendas, methods=["GET"], name="limites_vendas")
    vendas_router.add_api_route(
        "/api/vendas/sync/cancel",
        vendas.cancelar_sincronizacao_vendas,
        methods=["POST"],
        name="cancelar_sincronizacao_vendas",
    )
    vendas_router.add_api_route(
        "/api/vendas/sync/progress",
        vendas.progresso_sincronizacao_vendas,
        methods=["GET"],
        name="progresso_sincronizacao_vendas",
    )
    vendas_router.add_api_route("/api/vendas/sync", vendas.sincronizar_vendas, methods=["POST"], name="sincronizar_vendas")
    vendas_router.add_api_route("/api/notas-entrada", vendas.listar_notas_entrada, methods=["GET"], name="listar_notas_entrada")
    vendas_router.add_api_route(
        "/api/notas-entrada/itens",
        vendas.listar_itens_devolucoes,
        methods=["GET"],
        name="listar_itens_devolucoes",
    )
    vendas_router.add_api_route(
        "/api/notas-entrada/sku/{sku}",
        vendas.listar_notas_entrada_por_sku,
        methods=["GET"],
        name="listar_notas_entrada_por_sku",
    )
    vendas_router.add_api_route(
        "/api/unidades-negocios",
        vendas.listar_unidades_negocios,
        methods=["GET"],
        name="listar_unidades_negocios",
    )
    vendas_router.add_api_route(
        "/api/unidades-negocios/atualizar",
        vendas.atualizar_unidade_negocio,
        methods=["POST"],
        name="atualizar_unidade_negocio",
    )
    vendas_router.add_api_route(
        "/api/unidades-negocios/mapeamento",
        vendas.salvar_mapeamento_unidades,
        methods=["POST"],
        name="salvar_mapeamento_unidades",
    )
    return vendas_router


__all__ = ["create_vendas_router"]
