"""Marketplace listing chart-contract adapter."""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from .common import (
    _coverage_complete,
    _normalized_tool_id,
    _provider_chart_data,
    _safe_label,
    _safe_number,
    _summary_entries,
)

def _listing_chart_data(results: list[dict[str, Any]], query_policy: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    store = _safe_label(query_policy.get("store"), "Loja selecionada")
    complete = True
    for result in results:
        if not isinstance(result, dict) or _normalized_tool_id(result) != "mercado_livre_listing":
            continue
        store = _safe_label(result.get("loja") or store, store)
        rows.extend(item for item in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(item, dict))
        top_chart_data = _provider_chart_data(result)
        for meta, payload in _summary_entries(result):
            store = _safe_label(meta.get("loja") or payload.get("loja") or payload.get("store") or store, store)
            complete = complete and _coverage_complete(payload)
            chart_data = top_chart_data or (
                payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
            )
            for point in chart_data.get("points") if isinstance(chart_data.get("points"), list) else []:
                if isinstance(point, dict):
                    rows.append(point)
    if not rows:
        return None
    unique_rows: list[dict[str, Any]] = []
    seen_items: set[str] = set()
    for row in rows:
        identity = _safe_label(row.get("id") or row.get("item_id") or row.get("sku") or row.get("seller_sku"))
        identity = identity or f"row:{len(unique_rows)}"
        if identity in seen_items:
            continue
        seen_items.add(identity)
        unique_rows.append(row)
    rows = unique_rows
    statuses: Counter[str] = Counter()
    ranking: list[dict[str, Any]] = []
    for row in rows:
        status = _safe_label(row.get("status"), "não informado")
        statuses[status] += 1
        item_id = _safe_label(row.get("seller_sku") or row.get("sku") or row.get("id") or row.get("item_id"), "Anúncio")
        sold = _safe_number(row.get("sold_quantity"))
        ranking.append(
            {
                "label": item_id,
                "sku": item_id,
                "title": _safe_label(row.get("title") or row.get("nome"), "Anúncio"),
                "quantity": sold,
                "value": sold,
                "value_label": "vendidos acumulados",
            }
        )
    ranking.sort(key=lambda item: item["value"], reverse=True)
    return {
        "analysis_type": "listings",
        "title": "Análise visual de anúncios",
        "source": "Mercado Livre — sold_quantity acumulado do anúncio",
        "period_start": "",
        "period_end": "",
        "coverage_complete": complete,
        "stores": [{"name": store}],
        "kpis": {"listings": len(rows), "active": statuses.get("active", 0)},
        "ranking": ranking,
        "categories": [{"label": label, "value": value} for label, value in statuses.most_common()],
    }
