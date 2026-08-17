"""Builds safe, deterministic chart payloads for WhatsApp reports.

This module deliberately consumes only aggregate/tool output. It never parses
the model's prose and never includes buyer, address or authentication fields in
the chart contract. The implementation lives in the modular report_visuals
package while this module preserves the established import surface.
"""

from __future__ import annotations

from backend.services.whatsapp.report_visuals.common import (
    REPORT_CHART_DIRNAME,
    REPORT_CHART_MAX_IMAGES,
    REPORT_CHART_TTL_SECONDS,
    _coverage_complete,
    _normalized_tool_id,
    _period_from,
    _provider_chart_data,
    _safe_int,
    _safe_label,
    _safe_number,
    _summary_entries,
    _text_key,
)
from backend.services.whatsapp.report_visuals.intent import should_generate_report_charts
from backend.services.whatsapp.report_visuals.sales import (
    _api_sales_periods,
    _comparison_sales_periods,
    _compose_sales_chart,
    _dedupe_sales_periods,
    _legacy_marketplace_sales_chart_data,
    _period_label,
    _sales_chart_data,
    _sales_metrics,
    _sales_period_record,
    _sales_ranking,
    _sales_series,
    _standalone_sales_periods,
    _timeseries_sales_periods,
)
from backend.services.whatsapp.report_visuals.stock import (
    _bling_stock_visual,
    _legacy_stock_chart_data,
    _stale_stock_visual,
    _stock_chart_data,
    _stockout_visual,
)
from backend.services.whatsapp.report_visuals.listings import _listing_chart_data
from backend.services.whatsapp.report_visuals.builders import (
    build_chart_data,
)
from backend.services.whatsapp.report_visuals.artifacts import (
    _expires_epoch,
    _safe_client_id,
    _sha256,
    _validated_artifacts,
    chart_output_dir,
    cleanup_stale_chart_files,
    generate_task_chart_artifacts,
)

__all__ = [
    "REPORT_CHART_DIRNAME",
    "REPORT_CHART_MAX_IMAGES",
    "build_chart_data",
    "chart_output_dir",
    "cleanup_stale_chart_files",
    "generate_task_chart_artifacts",
    "should_generate_report_charts",
]
