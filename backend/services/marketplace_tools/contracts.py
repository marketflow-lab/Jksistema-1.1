"""Stable contracts shared by the marketplace tool domains."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

SALES_BY_DAY_SCHEMA = "jk.marketplace.sales_by_day.v1"
LISTING_SNAPSHOT_SCHEMA = "jk.marketplace.listing_snapshot.v1"

TOOL_EXECUTOR_IDS = (
    "_ia_tool_get_integrations_status",
    "_ia_tool_get_mercado_livre_listing",
    "_ia_tool_get_mercado_livre_orders",
    "_ia_tool_get_mercado_livre_returns",
    "_ia_tool_get_mercado_livre_visits",
    "_ia_tool_get_mercado_livre_promotions",
)


@dataclass(frozen=True, slots=True)
class MarketplaceToolResult:
    function: str
    arguments: Mapping[str, Any]
    result: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "arguments": dict(self.arguments),
            "result": dict(self.result),
        }


__all__ = [
    "LISTING_SNAPSHOT_SCHEMA",
    "MarketplaceToolResult",
    "SALES_BY_DAY_SCHEMA",
    "TOOL_EXECUTOR_IDS",
]
