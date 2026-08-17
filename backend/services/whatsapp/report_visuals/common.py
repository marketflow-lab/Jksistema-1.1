"""Shared normalization and provider-contract helpers for report visuals."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

REPORT_CHART_DIRNAME = "whatsapp_report_charts"
REPORT_CHART_MAX_IMAGES = 2
REPORT_CHART_TTL_SECONDS = 7 * 24 * 60 * 60

def _text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()).strip()

def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number

def _safe_int(value: Any, default: int = 0) -> int:
    return int(round(_safe_number(value, float(default))))

def _safe_label(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return (text or fallback)[:240]

def _summary_entries(result: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in result.get("summary") if isinstance(result.get("summary"), list) else []:
        if not isinstance(raw, dict):
            continue
        payload = raw.get("summary") if isinstance(raw.get("summary"), dict) else raw
        entries.append((raw, payload))
    if not entries:
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        entries.append((result, payload))
    return entries

def _normalized_tool_id(result: dict[str, Any]) -> str:
    raw = str(result.get("tool_id") or result.get("function") or "").strip()
    return {
        "get_mercado_livre_orders": "mercado_livre_orders",
        "get_mercado_livre_listing": "mercado_livre_listing",
        "get_days_without_sale_top": "stale_stock",
        "get_stockout_forecast": "stockout_forecast",
        "get_sales_by_period": "sales_ranking",
        "get_period_comparison": "period_comparison",
        "get_sales_timeseries": "sales_timeseries",
    }.get(raw, raw)

def _provider_chart_data(result: dict[str, Any]) -> dict[str, Any]:
    direct = result.get("chart_data") if isinstance(result.get("chart_data"), dict) else {}
    if direct:
        return direct
    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    nested = payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
    if nested:
        return nested
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    nested = summary.get("chart_data") if isinstance(summary.get("chart_data"), dict) else {}
    if nested:
        return nested
    for _, summary_payload in _summary_entries(result):
        candidate = summary_payload.get("chart_data") if isinstance(summary_payload.get("chart_data"), dict) else {}
        if candidate:
            return candidate
    return {}

def _period_from(meta: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
    period = payload.get("period") if isinstance(payload.get("period"), dict) else {}
    requested = period.get("requested") if isinstance(period.get("requested"), dict) else {}
    fallback = meta.get("periodo") if isinstance(meta.get("periodo"), dict) else {}
    start = _safe_label(requested.get("from") or fallback.get("data_inicio") or payload.get("data_inicio"))
    end = _safe_label(requested.get("to") or fallback.get("data_fim") or payload.get("data_fim"))
    return start, end

def _coverage_complete(payload: dict[str, Any]) -> bool:
    paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
    return bool(
        payload.get("coverage_complete") is not False
        and payload.get("partial_response") is not True
        and payload.get("truncated") is not True
        and paging.get("has_more") is not True
    )
