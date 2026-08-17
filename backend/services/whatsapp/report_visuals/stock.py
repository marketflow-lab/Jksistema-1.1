"""Stock, stale-inventory, and stockout chart-contract adapters."""

from __future__ import annotations

from typing import Any, Optional

from .common import (
    _coverage_complete,
    _normalized_tool_id,
    _provider_chart_data,
    _safe_int,
    _safe_label,
    _safe_number,
    _summary_entries,
)

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
                        "label": _safe_label(
                            deposit.get("descricao") or deposit.get("nome") or deposit.get("deposito"),
                            "Depósito",
                        ),
                        "value": _safe_number(
                            deposit.get("saldo_fisico")
                            or deposit.get("saldo")
                            or deposit.get("quantidade")
                        ),
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
        quantity = _safe_number(
            row.get("quantity") if row.get("quantity") is not None else row.get("classified_quantity")
        )
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
        store_names = [
            _safe_label(
                result.get("loja") or payload.get("loja") or query_policy.get("store"),
                "Loja selecionada",
            )
        ]
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
    provider_ranking = (
        [item for item in chart.get("ranking", []) if isinstance(item, dict)]
        if isinstance(chart.get("ranking"), list)
        else []
    )
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
    provider_ranking = (
        [item for item in chart.get("ranking", []) if isinstance(item, dict)]
        if isinstance(chart.get("ranking"), list)
        else []
    )
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
