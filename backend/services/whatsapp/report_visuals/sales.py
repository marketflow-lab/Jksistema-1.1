"""Sales chart-contract adapters and composition."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .common import (
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

def _sales_series(chart_data: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_series = (
        chart_data.get("series")
        or chart_data.get("points")
        or chart_data.get("by_day")
        or payload.get("by_day")
        or []
    )
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

def _legacy_marketplace_sales_chart_data(
    results: list[dict[str, Any]], query_policy: dict[str, Any]
) -> Optional[dict[str, Any]]:
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
            chart_data = top_chart_data or (
                payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
            )
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
                    source=(
                        result.get("source_label")
                        or meta.get("source_label")
                        or "Histórico de vendas do JK Sistema"
                    ),
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
