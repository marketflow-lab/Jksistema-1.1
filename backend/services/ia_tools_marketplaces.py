"""Compatibility facade for the named marketplace tool APIs."""

from __future__ import annotations

from typing import Any, Optional

from backend.schemas import IAChatRequest
from backend.services.marketplace_tools import api
from backend.services.marketplace_tools.runtime import MarketplaceToolsRuntime
from backend.services.marketplace_tools import runtime


def configure_ia_tools_marketplaces_runtime(runtime_module=None, peers=None) -> MarketplaceToolsRuntime:
    return runtime.configure(runtime_module, peers)


def get_integrations_status(client_id: str, loja: Optional[str] = None) -> Optional[dict]:
    return api.get_integrations_status(client_id, loja)


def get_mercado_livre_listing(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    produto_tool: Optional[dict] = None,
    limite: int = 8,
    incluir_descricao: bool = False,
    status: Optional[str] = None,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    offset: int = 0,
    incluir_detalhes: bool = False,
    force_refresh: bool = False,
    mlb: Optional[str] = None,
    query_deadline: Optional[float] = None,
    incluir_comercial: bool = False,
) -> Optional[dict]:
    return api.get_mercado_livre_listing(
        client_id,
        mensagem,
        loja,
        produto_tool,
        limite,
        incluir_descricao,
        status,
        sku,
        item_id,
        offset,
        incluir_detalhes,
        force_refresh,
        mlb,
        query_deadline,
        incluir_comercial,
    )


def get_mercado_livre_orders(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    status: Any = None,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    offset: int = 0,
    limite: Optional[int] = None,
    incluir_detalhes: bool = False,
    force_refresh: bool = False,
    statuses: Any = None,
    id_pedido: Optional[str] = None,
    query_deadline: Optional[float] = None,
    modo_relatorio: bool = False,
    max_paginas: Optional[int] = None,
) -> Optional[dict]:
    return api.get_mercado_livre_orders(
        client_id,
        mensagem,
        loja,
        data_inicio,
        data_fim,
        status,
        sku,
        item_id,
        offset,
        limite,
        incluir_detalhes,
        force_refresh,
        statuses,
        id_pedido,
        query_deadline,
        modo_relatorio,
        max_paginas,
    )


def get_mercado_livre_returns(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limite: int = 1,
    offset: int = 0,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> Optional[dict]:
    return api.get_mercado_livre_returns(
        client_id,
        mensagem,
        loja,
        data_inicio,
        data_fim,
        limite,
        offset,
        sku,
        item_id,
        force_refresh,
        query_deadline,
    )


def get_mercado_livre_visits(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    item_id: Optional[str] = None,
    dias: int = 30,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> Optional[dict]:
    return api.get_mercado_livre_visits(
        client_id, mensagem, loja, item_id, dias, force_refresh, query_deadline
    )


def get_mercado_livre_promotions(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    status: Optional[str] = None,
    promotion_id: Optional[str] = None,
    incluir_contagens: bool = False,
    limite: int = 20,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> Optional[dict]:
    return api.get_mercado_livre_promotions(
        client_id,
        mensagem,
        loja,
        status,
        promotion_id,
        incluir_contagens,
        limite,
        force_refresh,
        query_deadline,
    )


def resolve_exact_order(
    client_id: str,
    requested_id: str,
    selected_store: str,
    *,
    query_deadline: Optional[float] = None,
):
    return api.resolve_exact_order(
        client_id,
        requested_id,
        selected_store,
        query_deadline=query_deadline,
    )


def generate_sku_image_response(payload: IAChatRequest, client_id: str) -> Optional[str]:
    return api.generate_sku_image_response(payload, client_id)


__all__ = [
    "MarketplaceToolsRuntime",
    "configure_ia_tools_marketplaces_runtime",
    "get_integrations_status",
    "get_mercado_livre_listing",
    "get_mercado_livre_orders",
    "get_mercado_livre_returns",
    "get_mercado_livre_visits",
    "get_mercado_livre_promotions",
    "resolve_exact_order",
    "generate_sku_image_response",
]


configure_ia_tools_marketplaces_runtime()
