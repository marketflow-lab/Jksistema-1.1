"""Builds safe, deterministic chart payloads for WhatsApp reports.

This module deliberately consumes only aggregate/tool output.  It never parses
the model's prose and never includes buyer, address or authentication fields in
the chart contract.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


REPORT_CHART_DIRNAME = "whatsapp_report_charts"
REPORT_CHART_MAX_IMAGES = 2
REPORT_CHART_TTL_SECONDS = 24 * 60 * 60


def _text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()).strip()


def should_generate_report_charts(prompt: Any) -> bool:
    """Return True only for analytical/report requests or an explicit chart."""

    text = _text_key(prompt)
    if not text:
        return False
    if re.search(r"\b(grafico|graficos|visual|visualizacao|chart)\b", text):
        return True
    if re.search(r"\b(relatorio|analise|comparacao|comparativo|diagnostico|balanco|consolidado)\b", text):
        return True
    numeric_domain = bool(
        re.search(
            r"\b(vendas?|pedidos?|faturamento|estoque|depositos?|anuncios?|skus?|produtos?|alertas?|operacional|lojas?|contas?)\b",
            text,
        )
    )
    if numeric_domain and re.search(
        r"\b(comparar|compare|comparando|versus|vs|diferenca|evolucao|tendencia|ranking|"
        r"mais vendido|mais vendida|menos vendido|menos vendida|por loja|por conta|por dia|por semana|por mes)\b",
        text,
    ):
        return True
    return bool(
        re.search(r"\bresumo\b", text)
        and re.search(
            r"\b(vendas?|pedidos?|faturamento|estoque|depositos?|anuncios?|skus?|produtos?|alertas?|operacional)\b",
            text,
        )
    )


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


def _safe_client_id(value: Any) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "default").strip())[:80]
    return safe.strip("._-") or "default"


def chart_output_dir(base_info_dir: str | Path, client_id: Any) -> Path:
    root = Path(base_info_dir).resolve()
    output = (root / _safe_client_id(client_id) / REPORT_CHART_DIRNAME).resolve()
    output.relative_to(root)
    output.mkdir(parents=True, exist_ok=True)
    return output


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


def _sales_series(chart_data: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_series = chart_data.get("series") or chart_data.get("points") or chart_data.get("by_day") or payload.get("by_day") or []
    output: list[dict[str, Any]] = []
    for raw in raw_series if isinstance(raw_series, list) else []:
        if not isinstance(raw, dict):
            continue
        label = _safe_label(raw.get("label") or raw.get("date") or raw.get("data") or raw.get("bucket"))
        if not label:
            continue
        output.append(
            {
                "label": label,
                "date": _safe_label(raw.get("date") or raw.get("data") or label),
                "orders": _safe_int(raw.get("orders") or raw.get("pedidos")),
                "items": _safe_number(raw.get("items") or raw.get("items_quantity") or raw.get("quantidade")),
                "gross": _safe_number(raw.get("gross") or raw.get("gross_amount") or raw.get("valor_bruto")),
                "paid": _safe_number(raw.get("paid") or raw.get("paid_amount") or raw.get("valor_pago")),
                "refunds": (
                    None
                    if raw.get("refunds", raw.get("refund_amount")) is None
                    else _safe_number(raw.get("refunds") or raw.get("refund_amount") or raw.get("estornos"))
                ),
                "net": (
                    None
                    if raw.get("net", raw.get("net_amount")) is None
                    else _safe_number(raw.get("net") or raw.get("net_amount") or raw.get("valor_liquido"))
                ),
            }
        )
    return output


def _sales_ranking(result: dict[str, Any], chart_data: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = chart_data.get("ranking") or chart_data.get("by_sku") or payload.get("by_sku")
    if not isinstance(raw_rows, list):
        raw_rows = result.get("all_rows") or result.get("top_rows") or []
    output: list[dict[str, Any]] = []
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        sku = _safe_label(
            raw.get("sku") or raw.get("seller_sku") or raw.get("item_id") or raw.get("mlb") or raw.get("id"),
            "SKU não identificado",
        )
        output.append(
            {
                "label": sku,
                "sku": sku,
                "title": _safe_label(raw.get("title") or raw.get("produto") or raw.get("nome"), "Produto"),
                "quantity": _safe_number(raw.get("quantity") or raw.get("quantidade") or raw.get("qtd")),
                "value": _safe_number(
                    raw.get("gross_amount") or raw.get("gross") or raw.get("valor_total") or raw.get("valor")
                ),
                "gross": _safe_number(
                    raw.get("gross_amount") or raw.get("gross") or raw.get("valor_total") or raw.get("valor")
                ),
            }
        )
    output.sort(key=lambda item: (item["value"], item["quantity"]), reverse=True)
    return output


def _legacy_marketplace_sales_chart_data(results: list[dict[str, Any]], query_policy: dict[str, Any]) -> Optional[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    source = "Mercado Livre"
    period_start = ""
    period_end = ""
    complete = True
    for result in results:
        if not isinstance(result, dict) or _normalized_tool_id(result) != "mercado_livre_orders":
            continue
        top_chart_data = _provider_chart_data(result)
        for meta, payload in _summary_entries(result):
            meta_tool = _normalized_tool_id(meta) or _normalized_tool_id(result)
            if meta_tool != "mercado_livre_orders":
                continue
            chart_data = top_chart_data or (payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {})
            totals = chart_data.get("totals") if isinstance(chart_data.get("totals"), dict) else (
                payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
            )
            start, end = _period_from(meta, payload)
            period_start = period_start or start
            period_end = period_end or end
            store = _safe_label(
                chart_data.get("store")
                or meta.get("loja")
                or payload.get("store")
                or query_policy.get("store"),
                "Loja selecionada",
            )
            series = _sales_series(chart_data, payload)
            ranking = _sales_ranking(result, chart_data, payload)
            store_complete = _coverage_complete(payload) and chart_data.get("coverage_complete") is not False
            complete = complete and store_complete
            stores.append(
                {
                    "name": store,
                    "kpis": {
                        "orders": _safe_int(totals.get("orders")),
                        "items": _safe_number(totals.get("items_quantity") or totals.get("items")),
                        "gross": _safe_number(totals.get("gross_amount") or totals.get("gross")),
                        "paid": _safe_number(totals.get("paid_amount") or totals.get("paid")),
                        "refunds": (
                            None
                            if totals.get("refund_amount", totals.get("refunds")) is None
                            else _safe_number(totals.get("refund_amount") or totals.get("refunds"))
                        ),
                        "net": (
                            None
                            if totals.get("net_amount", totals.get("net")) is None
                            else _safe_number(totals.get("net_amount") or totals.get("net"))
                        ),
                    },
                    "series": series,
                    "ranking": ranking,
                    "coverage_complete": store_complete,
                }
            )
            break
    if not stores:
        return None
    multi_store = len(stores) > 1 or str(query_policy.get("store_mode") or "") == "all"
    first = stores[0]
    render_stores = []
    combined_series: list[dict[str, Any]] = []
    for item in stores:
        metrics = dict(item.get("kpis") or {})
        render_stores.append({"name": item.get("name") or "Loja", **metrics})
        for point in item.get("series") if isinstance(item.get("series"), list) else []:
            combined_series.append({**point, "store": item.get("name") or "Loja"})
    return {
        "analysis_type": "sales_multi_store" if multi_store else "sales",
        "title": "Análise visual de vendas",
        "source": source,
        "period_start": period_start,
        "period_end": period_end,
        "coverage_complete": complete,
        "stores": render_stores,
        "kpis": first.get("kpis") if not multi_store else {
            "stores": len(stores),
            "orders": sum(_safe_int(item.get("kpis", {}).get("orders")) for item in stores),
            "items": sum(_safe_number(item.get("kpis", {}).get("items")) for item in stores),
            "gross": sum(_safe_number(item.get("kpis", {}).get("gross")) for item in stores),
            "net": (
                None
                if any(item.get("kpis", {}).get("net") is None for item in stores)
                else sum(_safe_number(item.get("kpis", {}).get("net")) for item in stores)
            ),
        },
        "series": first.get("series", []) if not multi_store else combined_series,
        "ranking": first.get("ranking", []) if not multi_store else [],
    }


def _period_label(start: Any, end: Any) -> str:
    start_text = _safe_label(start)
    end_text = _safe_label(end)
    try:
        start_date = datetime.fromisoformat(start_text[:10]).date()
        end_date = datetime.fromisoformat(end_text[:10]).date()
    except (TypeError, ValueError):
        return _safe_label(f"{start_text} a {end_text}".strip(" a"), "Período")
    if start_date.year == end_date.year and start_date.month == end_date.month:
        return start_date.strftime("%m/%Y")
    return f"{start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}"


def _sales_metrics(totals: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    def first(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)

    metrics: dict[str, Any] = {
        "orders": _safe_int(first(totals.get("orders"), payload.get("pedidos_total"), 0)),
        "items": _safe_number(first(
            totals.get("items_quantity"), totals.get("items"),
            payload.get("quantidade_total"), payload.get("quantidade_vendida_total"), 0,
        )),
        "gross": _safe_number(first(
            totals.get("gross_amount"), totals.get("gross"),
            payload.get("valor_total"), payload.get("valor_vendido_total"), 0,
        )),
    }
    optional = {
        "paid": first(totals.get("paid_amount"), totals.get("paid"), payload.get("valor_pago")),
        "refunds": first(
            totals.get("refund_amount"), totals.get("refunds"),
            payload.get("valor_devolvido_total"), payload.get("valor_estornado"),
        ),
        "net": first(totals.get("net_amount"), totals.get("net"), payload.get("valor_liquido")),
    }
    metrics.update({key: _safe_number(value) for key, value in optional.items() if value is not None})
    return metrics


def _sales_period_record(
    *,
    store: Any,
    start: Any,
    end: Any,
    metrics: dict[str, Any],
    series: list[dict[str, Any]],
    ranking: list[dict[str, Any]],
    source: Any,
    coverage_complete: bool,
) -> dict[str, Any]:
    return {
        "store": _safe_label(store, "Loja selecionada"),
        "start": _safe_label(start),
        "end": _safe_label(end),
        "label": _period_label(start, end),
        "kpis": dict(metrics or {}),
        "series": [dict(item) for item in series if isinstance(item, dict)],
        "ranking": [dict(item) for item in ranking if isinstance(item, dict)],
        "source": _safe_label(source, "JK Sistema"),
        "coverage_complete": bool(coverage_complete),
    }


def _standalone_sales_periods(
    results: list[dict[str, Any]], query_policy: dict[str, Any]
) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for result in results:
        if _normalized_tool_id(result) != "sales_ranking":
            continue
        top_chart = _provider_chart_data(result)
        for meta, payload in _summary_entries(result):
            if _normalized_tool_id(meta) not in {"", "sales_ranking"}:
                continue
            chart = top_chart or (payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {})
            totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else (
                chart.get("kpis") if isinstance(chart.get("kpis"), dict) else {}
            )
            start, end = _period_from(meta, payload)
            start = start or _safe_label(chart.get("period_start"))
            end = end or _safe_label(chart.get("period_end"))
            if not (start and end):
                point_dates = [
                    _safe_label(item.get("date") or item.get("data"))
                    for item in (chart.get("points") or chart.get("series") or chart.get("by_day") or [])
                    if isinstance(item, dict) and (item.get("date") or item.get("data"))
                ]
                start = start or (min(point_dates) if point_dates else "")
                end = end or (max(point_dates) if point_dates else "")
            if not (start or end):
                continue
            meta_rows = meta.get("rows") if isinstance(meta.get("rows"), list) else []
            fallback_rows = result.get("top_rows") if isinstance(result.get("top_rows"), list) else []
            ranking = _sales_ranking({"top_rows": meta_rows or fallback_rows}, chart, payload)
            series = _sales_series(chart, payload)
            metrics = _sales_metrics(totals, payload)
            if not series:
                series = [{"label": _period_label(start, end), **metrics}]
            arguments = meta.get("arguments") if isinstance(meta.get("arguments"), dict) else {}
            store = (
                chart.get("store") or payload.get("loja") or meta.get("loja")
                or arguments.get("loja") or query_policy.get("store")
            )
            periods.append(_sales_period_record(
                store=store,
                start=start,
                end=end,
                metrics=metrics,
                series=series,
                ranking=ranking,
                source=result.get("source_label") or meta.get("source_label") or "Histórico de vendas do JK Sistema",
                coverage_complete=_coverage_complete(payload) and chart.get("coverage_complete") is not False,
            ))
            break
    return periods


def _comparison_sales_periods(
    results: list[dict[str, Any]], query_policy: dict[str, Any]
) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for result in results:
        if _normalized_tool_id(result) != "period_comparison":
            continue
        for meta, payload in _summary_entries(result):
            if _normalized_tool_id(meta) not in {"", "period_comparison"}:
                continue
            arguments = meta.get("arguments") if isinstance(meta.get("arguments"), dict) else {}
            store = meta.get("loja") or arguments.get("loja") or query_policy.get("store")
            local_periods: list[dict[str, Any]] = []
            for key in ("periodo_a", "periodo_b"):
                period = payload.get(key) if isinstance(payload.get(key), dict) else {}
                if not period:
                    continue
                start = period.get("data_inicio")
                end = period.get("data_fim")
                metrics = _sales_metrics({}, period)
                local_periods.append(_sales_period_record(
                    store=store,
                    start=start,
                    end=end,
                    metrics=metrics,
                    series=[{"label": _period_label(start, end), **metrics}],
                    ranking=[],
                    source=result.get("source_label") or meta.get("source_label") or "Histórico de vendas do JK Sistema",
                    coverage_complete=_coverage_complete(payload),
                ))
            if local_periods:
                periods.extend(local_periods)
                break
    return periods


def _api_sales_periods(
    results: list[dict[str, Any]], query_policy: dict[str, Any]
) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for result in results:
        if _normalized_tool_id(result) != "mercado_livre_orders":
            continue
        top_chart = _provider_chart_data(result)
        for meta, payload in _summary_entries(result):
            if (_normalized_tool_id(meta) or "mercado_livre_orders") != "mercado_livre_orders":
                continue
            chart = top_chart or (payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {})
            totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else (
                payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
            )
            start, end = _period_from(meta, payload)
            start = start or _safe_label(chart.get("period_start"))
            end = end or _safe_label(chart.get("period_end"))
            if not (start and end):
                point_dates = [
                    _safe_label(item.get("date") or item.get("data"))
                    for item in (chart.get("points") or chart.get("series") or chart.get("by_day") or [])
                    if isinstance(item, dict) and (item.get("date") or item.get("data"))
                ]
                start = start or (min(point_dates) if point_dates else "")
                end = end or (max(point_dates) if point_dates else "")
            if not (start or end):
                continue
            store = chart.get("store") or meta.get("loja") or payload.get("store") or query_policy.get("store")
            periods.append(_sales_period_record(
                store=store,
                start=start,
                end=end,
                metrics=_sales_metrics(totals, payload),
                series=_sales_series(chart, payload),
                ranking=_sales_ranking(result, chart, payload),
                source=result.get("source_label") or "Mercado Livre",
                coverage_complete=_coverage_complete(payload) and chart.get("coverage_complete") is not False,
            ))
            break
    return periods


def _timeseries_sales_periods(
    results: list[dict[str, Any]], query_policy: dict[str, Any]
) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for result in results:
        if _normalized_tool_id(result) != "sales_timeseries":
            continue
        chart = _provider_chart_data(result)
        for meta, payload in _summary_entries(result):
            if _normalized_tool_id(meta) not in {"", "sales_timeseries"}:
                continue
            nested = payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
            chart_data = chart or nested
            start, end = _period_from(meta, payload)
            start = start or _safe_label(chart_data.get("period_start"))
            end = end or _safe_label(chart_data.get("period_end"))
            totals = chart_data.get("kpis") if isinstance(chart_data.get("kpis"), dict) else {}
            periods.append(_sales_period_record(
                store=chart_data.get("store") or payload.get("loja") or meta.get("loja") or query_policy.get("store"),
                start=start,
                end=end,
                metrics=_sales_metrics(totals, payload),
                series=_sales_series(chart_data, payload),
                ranking=[],
                source=result.get("source_label") or "Histórico de vendas do JK Sistema",
                coverage_complete=_coverage_complete(payload) and chart_data.get("coverage_complete") is not False,
            ))
            break
    return periods


def _dedupe_sales_periods(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in periods:
        key = (_text_key(item.get("store")), str(item.get("start") or ""), str(item.get("end") or ""))
        current = selected.get(key)
        if current is None or len(item.get("ranking") or []) > len(current.get("ranking") or []):
            selected[key] = item
    return sorted(selected.values(), key=lambda item: (str(item.get("start") or ""), _text_key(item.get("store"))))


def _compose_sales_chart(periods: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    periods = _dedupe_sales_periods(periods)
    if not periods:
        return None
    store_names: list[str] = []
    source_names: list[str] = []
    for item in periods:
        store = _safe_label(item.get("store"), "Loja selecionada")
        source = _safe_label(item.get("source"), "JK Sistema")
        if _text_key(store) not in {_text_key(name) for name in store_names}:
            store_names.append(store)
        if _text_key(source) not in {_text_key(name) for name in source_names}:
            source_names.append(source)
    starts = [str(item.get("start") or "") for item in periods if item.get("start")]
    ends = [str(item.get("end") or "") for item in periods if item.get("end")]
    complete = all(item.get("coverage_complete") is True for item in periods)
    by_store: dict[str, list[dict[str, Any]]] = {}
    for item in periods:
        by_store.setdefault(_text_key(item.get("store")), []).append(item)

    if len(store_names) == 1 and len(periods) >= 2:
        latest = periods[-1]
        latest_label = latest.get("label") or "período mais recente"
        latest_kpis = latest.get("kpis") if isinstance(latest.get("kpis"), dict) else {}
        return {
            "analysis_type": "sales_period_comparison",
            "title": "Comparação de vendas entre períodos",
            "source": " + ".join(source_names),
            "period_start": min(starts) if starts else "",
            "period_end": max(ends) if ends else "",
            "coverage_complete": complete,
            "stores": [{"name": store_names[0]}],
            "kpis": {
                "Períodos comparados": len(periods),
                f"Faturamento {latest_label}": latest_kpis.get("gross", 0),
                f"Pedidos {latest_label}": latest_kpis.get("orders", 0),
                f"Itens {latest_label}": latest_kpis.get("items", 0),
            },
            "series": [
                {"label": item.get("label"), **dict(item.get("kpis") or {})}
                for item in periods
            ],
            "ranking": list(latest.get("ranking") or []),
        }

    if len(store_names) == 1:
        item = periods[0]
        return {
            "analysis_type": "sales",
            "title": "Análise visual de vendas",
            "source": " + ".join(source_names),
            "period_start": item.get("start") or "",
            "period_end": item.get("end") or "",
            "coverage_complete": complete,
            "stores": [{"name": store_names[0], **dict(item.get("kpis") or {})}],
            "kpis": dict(item.get("kpis") or {}),
            "series": list(item.get("series") or []),
            "ranking": list(item.get("ranking") or []),
        }

    render_stores: list[dict[str, Any]] = []
    combined_series: list[dict[str, Any]] = []
    multiple_periods = any(len(items) > 1 for items in by_store.values())
    for store_name in store_names:
        items = by_store.get(_text_key(store_name), [])
        latest = items[-1]
        render_stores.append({"name": store_name, **dict(latest.get("kpis") or {})})
        if len(items) > 1:
            combined_series.extend(
                {"label": item.get("label"), "store": store_name, **dict(item.get("kpis") or {})}
                for item in items
            )
        else:
            combined_series.extend({**point, "store": store_name} for point in (latest.get("series") or []))
    kpis: dict[str, Any] = {"stores": len(store_names)}
    if not multiple_periods:
        for metric in ("orders", "items", "gross"):
            kpis[metric] = sum(_safe_number(item.get(metric)) for item in render_stores)
    return {
        "analysis_type": "sales_multi_store",
        "title": "Comparação de vendas entre lojas",
        "source": " + ".join(source_names),
        "period_start": min(starts) if starts else "",
        "period_end": max(ends) if ends else "",
        "coverage_complete": complete,
        "stores": render_stores,
        "kpis": kpis,
        "series": combined_series,
        "ranking": [],
    }


def _sales_chart_data(results: list[dict[str, Any]], query_policy: dict[str, Any]) -> Optional[dict[str, Any]]:
    api_periods = _api_sales_periods(results, query_policy)
    if api_periods:
        return _compose_sales_chart(api_periods)
    standalone = _standalone_sales_periods(results, query_policy)
    if len(_dedupe_sales_periods(standalone)) >= 2:
        # Fechamentos mensais explícitos prevalecem sobre um fallback antigo
        # que eventualmente tenha usado uma janela móvel.
        return _compose_sales_chart(standalone)
    comparison = _comparison_sales_periods(results, query_policy)
    composed = _compose_sales_chart([*comparison, *standalone])
    return composed or _compose_sales_chart(_timeseries_sales_periods(results, query_policy))


def _legacy_stock_chart_data(results: list[dict[str, Any]], query_policy: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    store = _safe_label(query_policy.get("store"), "Loja selecionada")
    source = "Bling"
    complete = True
    for result in results:
        if not isinstance(result, dict) or str(result.get("tool_id") or "") != "bling_stock_balances":
            continue
        store = _safe_label(result.get("loja") or store, store)
        rows.extend(item for item in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(item, dict))
        top_chart_data = result.get("chart_data") if isinstance(result.get("chart_data"), dict) else {}
        for meta, payload in _summary_entries(result):
            store = _safe_label(meta.get("loja") or store, store)
            complete = complete and _coverage_complete(payload)
    if not rows:
        return None
    ranking = []
    total_units = 0.0
    for row in rows:
        units = _safe_number(
            row.get("saldo_loja_total")
            if row.get("saldo_loja_total") is not None
            else row.get("saldo") or row.get("quantidade") or row.get("estoque")
        )
        total_units += units
        sku = _safe_label(row.get("sku") or row.get("codigo") or row.get("id"), "SKU não identificado")
        ranking.append(
            {
                "label": sku,
                "sku": sku,
                "title": _safe_label(row.get("descricao") or row.get("nome") or row.get("produto"), "Produto"),
                "quantity": units,
                "value": units,
            }
        )
    ranking.sort(key=lambda item: item["value"], reverse=True)
    categories: list[dict[str, Any]] = []
    if len(rows) == 1:
        for deposit in rows[0].get("depositos") if isinstance(rows[0].get("depositos"), list) else []:
            if isinstance(deposit, dict):
                categories.append(
                    {
                        "label": _safe_label(deposit.get("descricao") or deposit.get("nome") or deposit.get("deposito"), "Depósito"),
                        "value": _safe_number(deposit.get("saldo_fisico") or deposit.get("saldo") or deposit.get("quantidade")),
                    }
                )
    return {
        "analysis_type": "stock",
        "title": "Análise visual de estoque",
        "source": source,
        "period_start": "",
        "period_end": "",
        "coverage_complete": complete,
        "stores": [{"name": store, "kpis": {"skus": len(rows), "items": total_units}, "ranking": ranking}],
        "kpis": {"skus": len(rows), "items": total_units},
        "ranking": ranking,
        "categories": categories,
    }


def _bling_stock_visual(chart: dict[str, Any], result: dict[str, Any], query_policy: dict[str, Any]) -> dict[str, Any]:
    totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else {}
    provider_ranking = chart.get("ranking") if isinstance(chart.get("ranking"), list) else []
    ranking = []
    for row in provider_ranking:
        if not isinstance(row, dict):
            continue
        quantity = _safe_number(row.get("quantity") if row.get("quantity") is not None else row.get("classified_quantity"))
        ranking.append(
            {
                "sku": _safe_label(row.get("sku"), "SKU não identificado"),
                "title": _safe_label(row.get("title"), "Produto"),
                "quantity": quantity,
                "value": quantity,
                "money": False,
                "value_label": "Saldo disponível" if row.get("quantity_reliable") is True else "Saldo classificado",
            }
        )
    deposits = chart.get("deposits") if isinstance(chart.get("deposits"), dict) else {}
    included = [item for item in deposits.get("included", []) if isinstance(item, dict)]
    excluded = [item for item in deposits.get("excluded", []) if isinstance(item, dict)]
    categories: list[dict[str, Any]] = []
    if len(provider_ranking) == 1:
        for item in included:
            categories.append(
                {
                    "label": f"{_safe_label(item.get('name'), 'Depósito')} — incluído",
                    "value": _safe_number(item.get("quantity")),
                }
            )
        for item in excluded:
            categories.append(
                {
                    "label": (
                        f"{_safe_label(item.get('name'), 'Depósito')} — "
                        f"{_safe_label(item.get('reason'), 'excluído')}"
                    ),
                    "value": _safe_number(item.get("quantity")),
                }
            )
    else:
        categories = [
            {"label": "Depósitos incluídos", "value": sum(_safe_number(item.get("quantity")) for item in included)},
            {"label": "Depósitos excluídos", "value": sum(_safe_number(item.get("quantity")) for item in excluded)},
        ]
    stores = chart.get("stores") if isinstance(chart.get("stores"), list) else []
    store_names = [_safe_label(item) for item in stores if _safe_label(item)]
    if not store_names:
        payload = result.get("result") if isinstance(result.get("result"), dict) else {}
        store_names = [_safe_label(result.get("loja") or payload.get("loja") or query_policy.get("store"), "Loja selecionada")]
    available = totals.get("store_available")
    shown_quantity = _safe_number(available if available is not None else totals.get("classified_quantity"))
    return {
        "analysis_type": "stock_bling",
        "title": "Análise visual de estoque Bling",
        "source": "Bling — depósitos classificados",
        "period_start": "",
        "period_end": "",
        "coverage_complete": chart.get("coverage_complete") is True,
        "stores": [{"name": name} for name in store_names],
        "kpis": {
            "skus": _safe_int(totals.get("skus"), len(ranking)),
            "items": shown_quantity,
            "Saldo bruto retornado": _safe_number(totals.get("gross_returned")),
            "Depósitos excluídos": _safe_int(totals.get("excluded_deposits")),
        },
        "ranking": ranking,
        "categories": categories,
    }


def _stale_stock_visual(chart: dict[str, Any], result: dict[str, Any], query_policy: dict[str, Any]) -> dict[str, Any]:
    totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else {}
    provider_ranking = [item for item in chart.get("ranking", []) if isinstance(item, dict)] if isinstance(chart.get("ranking"), list) else []
    all_costs_known = bool(provider_ranking) and all(item.get("capital_known") is True for item in provider_ranking)
    ranking = []
    for row in provider_ranking:
        quantity = _safe_number(row.get("quantity"))
        value = _safe_number(row.get("capital_value")) if all_costs_known else quantity
        ranking.append(
            {
                "sku": _safe_label(row.get("sku"), "SKU não identificado"),
                "title": _safe_label(row.get("title"), "Produto"),
                "quantity": quantity,
                "value": value,
                "money": all_costs_known,
                "value_label": "Valor de custo conhecido" if all_costs_known else "Saldo de loja",
            }
        )
    age_bands = chart.get("age_bands") if isinstance(chart.get("age_bands"), list) else []
    categories = [
        {"label": _safe_label(item.get("label"), "Faixa"), "value": _safe_int(item.get("skus"))}
        for item in age_bands
        if isinstance(item, dict)
    ]
    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    store = _safe_label(
        chart.get("store") or result.get("loja") or payload.get("loja") or query_policy.get("store"),
        "Loja selecionada",
    )
    return {
        "analysis_type": "stale_inventory",
        "title": "Análise visual de estoque parado",
        "source": "Histórico de vendas e estoque do JK Sistema",
        "period_start": "",
        "period_end": _safe_label(chart.get("reference_date")),
        "coverage_complete": chart.get("coverage_complete") is True,
        "stores": [{"name": store}],
        "kpis": {
            "skus": _safe_int(totals.get("stale_skus"), len(ranking)),
            "items": _safe_number(totals.get("stale_quantity")),
            "Valor de custo conhecido": _safe_number(totals.get("capital_known")),
            "SKUs sem custo": _safe_int(totals.get("skus_without_known_cost")),
        },
        "ranking": ranking,
        "categories": categories,
    }


def _stockout_visual(chart: dict[str, Any], result: dict[str, Any], query_policy: dict[str, Any]) -> dict[str, Any]:
    totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else {}
    provider_ranking = [item for item in chart.get("ranking", []) if isinstance(item, dict)] if isinstance(chart.get("ranking"), list) else []
    ranking = []
    for row in provider_ranking:
        risk_order = max(0, min(4, _safe_int(row.get("risk_order"), 4)))
        risk_label = _safe_label(row.get("risk_label"), "Sem consumo")
        days_to_stockout = row.get("days_to_stockout")
        if days_to_stockout is None:
            display_value = f"{risk_label} — sem consumo"
        else:
            days_number = _safe_number(days_to_stockout)
            days_text = str(int(days_number)) if days_number.is_integer() else f"{days_number:.1f}".replace(".", ",")
            display_value = f"{risk_label} — {days_text} dia(s)"
        ranking.append(
            {
                "sku": _safe_label(row.get("sku"), "SKU não identificado"),
                "title": _safe_label(row.get("title"), "Produto"),
                "quantity": _safe_number(row.get("quantity")),
                "value": 5 - risk_order,
                "money": False,
                "display_value": display_value,
                "value_label": "Risco",
            }
        )
    risk_bands = chart.get("risk_bands") if isinstance(chart.get("risk_bands"), list) else []
    categories = [
        {"label": _safe_label(item.get("label"), "Risco"), "value": _safe_int(item.get("skus"))}
        for item in risk_bands
        if isinstance(item, dict)
    ]
    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    store = _safe_label(
        chart.get("store") or result.get("loja") or payload.get("loja") or query_policy.get("store"),
        "Loja selecionada",
    )
    return {
        "analysis_type": "stockout_forecast",
        "title": "Análise visual de risco de ruptura",
        "source": "Histórico de vendas e estoque do JK Sistema",
        "period_start": "",
        "period_end": _safe_label(chart.get("reference_date")),
        "coverage_complete": chart.get("coverage_complete") is True,
        "stores": [{"name": store}],
        "kpis": {
            "skus": _safe_int(totals.get("analyzed_skus"), len(ranking)),
            "SKUs em risco": _safe_int(totals.get("at_risk_skus")),
            "critical": _safe_int(totals.get("critical_skus")),
            "Risco alto": _safe_int(totals.get("high_risk_skus")),
        },
        "ranking": ranking,
        "categories": categories,
    }


def _stock_chart_data(results: list[dict[str, Any]], query_policy: dict[str, Any]) -> Optional[dict[str, Any]]:
    adapters = {
        "jk.stock.bling_balances.v1": _bling_stock_visual,
        "jk.stock.stale_inventory.v1": _stale_stock_visual,
        "jk.stock.stockout_forecast.v1": _stockout_visual,
    }
    for result in results:
        if not isinstance(result, dict) or _normalized_tool_id(result) not in {
            "bling_stock_balances",
            "stale_stock",
            "stockout_forecast",
        }:
            continue
        chart = _provider_chart_data(result)
        adapter = adapters.get(str(chart.get("schema") or ""))
        if adapter and chart.get("pii_included") is False and chart.get("read_only") is True:
            return adapter(chart, result, query_policy)
    return _legacy_stock_chart_data(results, query_policy)


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
            chart_data = top_chart_data or (payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {})
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


def build_weekly_chart_data(suggestions: Iterable[dict[str, Any]], week_key: Any) -> dict[str, Any]:
    items = [item for item in suggestions if isinstance(item, dict)]
    severity_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for item in items:
        severity = _text_key(item.get("severity") or "info") or "info"
        severity = "atenção" if severity in {"warning", "high"} else "crítico" if severity == "critical" else "informativo"
        severity_counts[severity] += 1
        category = _safe_label(item.get("category") or item.get("type") or item.get("module"), "Operacional")
        category_counts[category] += 1
    return {
        "analysis_type": "weekly",
        "title": "Resumo operacional semanal",
        "source": "JK Sistema",
        "period_start": _safe_label(week_key),
        "period_end": _safe_label(week_key),
        "coverage_complete": True,
        "stores": [],
        "kpis": {
            "alerts": len(items),
            "critical": severity_counts.get("crítico", 0),
            "attention": severity_counts.get("atenção", 0),
        },
        "categories": [
            *[
                {"label": f"Severidade — {label}", "value": value}
                for label, value in severity_counts.most_common()
            ],
            *[
                {"label": f"Categoria — {label}", "value": value}
                for label, value in category_counts.most_common(10)
            ],
        ],
        "ranking": [{"label": label, "value": value, "quantity": value} for label, value in category_counts.most_common(10)],
        "series": [],
    }


def build_scheduled_report_chart_data(
    context: dict[str, Any],
    period: dict[str, Any],
    kind: Any,
) -> dict[str, Any]:
    """Monta o contrato visual usando exatamente o contexto do relatório agendado."""

    safe_context = dict(context or {}) if isinstance(context, dict) else {}
    safe_period = dict(period or {}) if isinstance(period, dict) else {}
    report_kind = "monthly" if _text_key(kind) in {"monthly", "mensal", "mes"} else "weekly"
    title = "Relatório mensal" if report_kind == "monthly" else "Relatório semanal"
    start = _safe_label(safe_period.get("start"))
    end = _safe_label(safe_period.get("end"))
    prompt = f"{title} de vendas e operações de {start} a {end}"
    tool_results = [item for item in (safe_context.get("tool_results") or []) if isinstance(item, dict)]
    tool_plan = safe_context.get("tool_plan") if isinstance(safe_context.get("tool_plan"), dict) else {}
    query_policy = {
        "data_inicio": start,
        "data_fim": end,
        "store": tool_plan.get("loja") or tool_plan.get("store") or "",
        "store_mode": tool_plan.get("store_mode") or "all",
    }
    chart = build_chart_data(prompt, tool_results, query_policy)
    has_business_data = bool(
        chart.get("kpis")
        or chart.get("series")
        or chart.get("ranking")
        or chart.get("categories")
    )
    if not has_business_data:
        chart = build_weekly_chart_data(safe_context.get("suggestions") or [], safe_period.get("key") or start)
        chart["analysis_type"] = f"scheduled_{report_kind}"
    chart["title"] = f"{title} — vendas e operações"
    chart["period_start"] = start
    chart["period_end"] = end
    return chart


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expires_epoch(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if text:
        try:
            return int(float(text))
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp())
            except ValueError:
                pass
    return int(time.time() + REPORT_CHART_TTL_SECONDS)


def _validated_artifacts(raw: Any, output_dir: Path, max_images: int) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            path = Path(str(item.get("path") or "")).resolve()
            path.relative_to(output_dir.resolve())
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            size = path.stat().st_size
            if size <= 0 or size > 5 * 1024 * 1024:
                path.unlink(missing_ok=True)
                continue
            digest = _sha256(path)
            expected = str(item.get("sha256") or "").strip().lower()
            if expected and expected != digest:
                path.unlink(missing_ok=True)
                continue
            artifact = {
                **item,
                "artifact_type": "report_chart",
                "path": str(path),
                "mime_type": "image/png",
                "sha256": digest,
                "byte_size": size,
                "expires_at": _expires_epoch(item.get("expires_at")),
            }
            artifacts.append(artifact)
        except (OSError, ValueError, TypeError):
            continue
        if len(artifacts) >= max(1, min(int(max_images or 1), REPORT_CHART_MAX_IMAGES)):
            break
    return artifacts


def generate_task_chart_artifacts(
    *,
    base_info_dir: str | Path,
    client_id: Any,
    task_id: Any,
    prompt: Any,
    tool_results: Iterable[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
    max_images: int = REPORT_CHART_MAX_IMAGES,
) -> dict[str, Any]:
    if not should_generate_report_charts(prompt):
        return {"expected": False, "status": "not_analytical", "artifacts": []}
    output_dir = chart_output_dir(base_info_dir, client_id)
    chart_data = build_chart_data(prompt, tool_results, query_policy)
    chart_data["task_id"] = _safe_label(task_id)
    try:
        from backend.services import report_charts

        raw = report_charts.generate_report_charts(chart_data, output_dir, max_images=max_images)
        artifacts = _validated_artifacts(raw, output_dir, max_images)
        return {
            "expected": True,
            "status": "generated" if artifacts else "generation_empty",
            "artifacts": artifacts,
            "analysis_type": chart_data.get("analysis_type") or "generic",
            "coverage_complete": chart_data.get("coverage_complete") is True,
        }
    except Exception as exc:
        return {
            "expected": True,
            "status": "generation_failed",
            "artifacts": [],
            "error": str(exc)[:500],
            "analysis_type": chart_data.get("analysis_type") or "generic",
        }


def generate_weekly_chart_artifacts(
    *,
    base_info_dir: str | Path,
    client_id: Any,
    week_key: Any,
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    output_dir = chart_output_dir(base_info_dir, client_id)
    safe_data = dict(chart_data or {})
    safe_data["analysis_type"] = "weekly"
    try:
        from backend.services import report_charts

        raw = report_charts.generate_report_charts(safe_data, output_dir, max_images=1)
        artifacts = _validated_artifacts(raw, output_dir, 1)
        return {
            "expected": True,
            "status": "generated" if artifacts else "generation_empty",
            "artifacts": artifacts,
            "week_key": _safe_label(week_key),
        }
    except Exception as exc:
        return {"expected": True, "status": "generation_failed", "artifacts": [], "error": str(exc)[:500]}


def generate_scheduled_report_chart_artifacts(
    *,
    base_info_dir: str | Path,
    client_id: Any,
    period: dict[str, Any],
    kind: Any,
    chart_data: dict[str, Any],
    max_images: int = REPORT_CHART_MAX_IMAGES,
) -> dict[str, Any]:
    output_dir = chart_output_dir(base_info_dir, client_id)
    safe_data = dict(chart_data or {})
    try:
        from backend.services import report_charts

        raw = report_charts.generate_report_charts(safe_data, output_dir, max_images=max_images)
        artifacts = _validated_artifacts(raw, output_dir, max_images)
        return {
            "expected": True,
            "status": "generated" if artifacts else "generation_empty",
            "artifacts": artifacts,
            "report_kind": "monthly" if _text_key(kind) in {"monthly", "mensal", "mes"} else "weekly",
            "period_key": _safe_label((period or {}).get("key") if isinstance(period, dict) else ""),
        }
    except Exception as exc:
        return {
            "expected": True,
            "status": "generation_failed",
            "artifacts": [],
            "error": str(exc)[:500],
        }


def cleanup_stale_chart_files(base_info_dir: str | Path, max_age_seconds: int = REPORT_CHART_TTL_SECONDS) -> int:
    root = Path(base_info_dir).resolve()
    cutoff = time.time() - max(60, int(max_age_seconds or REPORT_CHART_TTL_SECONDS))
    removed = 0
    for path in root.glob(f"*/{REPORT_CHART_DIRNAME}/*.png"):
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if resolved.is_file() and resolved.stat().st_mtime < cutoff:
                resolved.unlink(missing_ok=True)
                removed += 1
        except (OSError, ValueError):
            continue
    return removed


__all__ = [
    "REPORT_CHART_DIRNAME",
    "REPORT_CHART_MAX_IMAGES",
    "build_chart_data",
    "build_scheduled_report_chart_data",
    "build_weekly_chart_data",
    "chart_output_dir",
    "cleanup_stale_chart_files",
    "generate_task_chart_artifacts",
    "generate_scheduled_report_chart_artifacts",
    "generate_weekly_chart_artifacts",
    "should_generate_report_charts",
]
