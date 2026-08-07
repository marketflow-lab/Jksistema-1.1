"""Compatibility facade for ia_tools."""

from __future__ import annotations

from backend.services import ia_tools_produtos as _module_0
from backend.services import ia_tools_vendas as _module_1
from backend.services import ia_tools_marketplaces as _module_2
from backend.services import ia_tools_dispatcher as _module_3
from backend.services.ia_tools_produtos import *
from backend.services.ia_tools_vendas import (
    _ia_chat_scope_notice,
    _ia_devolucoes_db_resumo,
    _ia_extrair_item_ids_ml,
    _ia_extrair_mes_ano_mensagem,
    _ia_extrair_meses_ano_mensagem,
    _ia_extrair_periodo_mensagem_vendas,
    _ia_extrair_periodos_comparacao,
    _ia_lojas_ml_conectadas,
    _ia_metricas_periodo_raw,
    _ia_obter_data_referencia_vendas,
    _ia_periodo_mensal_padrao,
    _ia_resolver_loja_mensagem_vendas,
    _ia_tool_compare_sku_sales_months,
    _ia_tool_float,
    _ia_tool_get_avg_ticket_by_period,
    _ia_tool_get_days_without_sale,
    _ia_tool_get_days_without_sale_top,
    _ia_tool_get_month_sales_returns_details,
    _ia_tool_get_period_comparison,
    _ia_tool_get_profit_by_period,
    _ia_tool_get_return_rate_by_period,
    _ia_tool_get_returns_by_period,
    _ia_tool_get_returns_by_sku_period,
    _ia_tool_get_returns_data,
    _ia_tool_get_returns_quantity_by_period,
    _ia_tool_get_sales_by_month_period,
    _ia_tool_get_sales_by_period,
    _ia_tool_get_sales_by_sku_virtual_store,
    _ia_tool_get_sales_by_virtual_store_period,
    _ia_tool_get_sales_quantity_by_period,
    _ia_tool_get_sales_timeseries,
    _ia_tool_get_sku_sales_by_month,
    _ia_tool_get_stock_data,
    _ia_tool_get_stockout_forecast,
    _ia_tool_get_top_skus_returns_by_month,
    _ia_tool_get_top_skus_sales_by_month,
    _ia_tool_resolver_sku,
    _ia_tool_detect_sales_anomalies,
    _ia_vendas_contexto_exato,
    _ia_vendas_db_consulta_sku,
    _ia_vendas_db_consulta_sku_vendas_devolucoes,
    _ia_vendas_db_texto,
    _ia_vendas_db_top_skus,
    _ia_vendas_timeseries_raw,
)
from backend.services.ia_tools_marketplaces import (
    MarketplaceToolsRuntime,
    generate_sku_image_response,
    get_integrations_status,
    get_mercado_livre_listing,
    get_mercado_livre_orders,
    get_mercado_livre_promotions,
    get_mercado_livre_returns,
    get_mercado_livre_visits,
    resolve_exact_order,
)
from backend.services.ia_tools_dispatcher import *

_MODULES = (
    _module_0,
    _module_1,
    _module_2,
    _module_3,
)


def _peer_globals() -> dict[str, object]:
    peers = {}
    for module in _MODULES:
        for name in getattr(module, "PEER_EXPORTS", getattr(module, "__all__", ())):
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_ia_tools_runtime(runtime_module=None, peers=None):
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


__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
        or name in {
            "MarketplaceToolsRuntime",
            "generate_sku_image_response",
            "get_integrations_status",
            "get_mercado_livre_listing",
            "get_mercado_livre_orders",
            "get_mercado_livre_promotions",
            "get_mercado_livre_returns",
            "get_mercado_livre_visits",
            "resolve_exact_order",
        }
    )
]
if "configure_ia_tools_runtime" not in __all__:
    __all__.append("configure_ia_tools_runtime")

configure_ia_tools_runtime()
