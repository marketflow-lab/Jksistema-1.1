"""Stable textual executor ids mapped to named APIs."""
from importlib import import_module

TOOL_EXECUTORS = {
    "_ia_tool_get_integrations_status": ("backend.services.marketplace_tools.integrations", "get_status"),
    "_ia_tool_get_mercado_livre_listing": ("backend.services.marketplace_tools.listings", "query"),
    "_ia_tool_get_mercado_livre_orders": ("backend.services.marketplace_tools.orders", "query"),
    "_ia_tool_get_mercado_livre_returns": ("backend.services.marketplace_tools.returns", "query"),
    "_ia_tool_get_mercado_livre_visits": ("backend.services.marketplace_tools.traffic", "query_visits"),
    "_ia_tool_get_mercado_livre_promotions": ("backend.services.marketplace_tools.promotions", "query"),
}

def resolve_executor(executor_id: str):
    module_name, attribute = TOOL_EXECUTORS[executor_id]
    return getattr(import_module(module_name), attribute)

__all__ = ["TOOL_EXECUTORS", "resolve_executor"]
