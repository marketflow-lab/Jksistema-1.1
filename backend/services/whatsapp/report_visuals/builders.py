"""Public report-chart contract builders."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .common import _safe_label
from .listings import _listing_chart_data
from .sales import _sales_chart_data
from .stock import _stock_chart_data

def build_chart_data(
    prompt: Any,
    tool_results: Iterable[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    results = [item for item in tool_results if isinstance(item, dict)]
    policy = dict(query_policy or {}) if isinstance(query_policy, dict) else {}
    for builder in (_sales_chart_data, _stock_chart_data, _listing_chart_data):
        chart = builder(results, policy)
        if chart:
            return chart
    return {
        "analysis_type": "generic",
        "title": "Análise visual",
        "source": "JK Sistema",
        "period_start": _safe_label(policy.get("data_inicio")),
        "period_end": _safe_label(policy.get("data_fim")),
        "coverage_complete": False,
        "stores": [],
        "kpis": {},
        "series": [],
        "ranking": [],
        "categories": [],
        "empty_message": "Dados insuficientes para formar uma série",
    }
