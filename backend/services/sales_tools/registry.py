"""Explicit mapping from legacy Codex executor IDs to named APIs."""

from collections.abc import Callable
from typing import Any

from . import api

ToolExecutor = Callable[..., Any]

TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "_ia_tool_get_avg_ticket_by_period": api.get_avg_ticket_by_period,
    "_ia_tool_get_period_comparison": api.get_period_comparison,
    "_ia_tool_get_profit_by_period": api.get_profit_by_period,
    "_ia_tool_get_return_rate_by_period": api.get_return_rate_by_period,
    "_ia_tool_get_returns_by_period": api.get_returns_by_period,
    "_ia_tool_detect_sales_anomalies": api.detect_sales_anomalies,
    "_ia_tool_get_sales_by_period": api.get_sales_by_period,
    "_ia_tool_get_sales_quantity_by_period": api.get_sales_quantity_by_period,
    "_ia_tool_get_sales_timeseries": api.get_sales_timeseries,
    "_ia_tool_get_days_without_sale_top": api.get_days_without_sale_top,
    "_ia_tool_get_stock_data": api.get_stock_data,
    "_ia_tool_get_stockout_forecast": api.get_stockout_forecast,
}


def resolve_executor(executor_id: str) -> ToolExecutor | None:
    return TOOL_EXECUTORS.get(str(executor_id or "").strip())


__all__ = ["TOOL_EXECUTORS", "resolve_executor"]
