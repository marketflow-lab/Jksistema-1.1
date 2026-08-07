from __future__ import annotations

import math
from typing import Any


def _ia_stock_chart_number(value: Any, default: float = 0.0) -> float:
    """Convert an internal stock metric without copying arbitrary source data."""

    if value is None or isinstance(value, bool):
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _ia_stale_stock_chart_data(result: dict[str, Any]) -> dict[str, Any]:
    """Build the complete, PII-free contract used by stock-age visuals."""

    source = result if isinstance(result, dict) else {}
    raw_items = source.get("itens") if isinstance(source.get("itens"), list) else []
    stale_items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        quantity = _ia_stock_chart_number(item.get("saldo_loja"))
        days_raw = item.get("dias_sem_vender")
        days = None
        if days_raw is not None:
            try:
                days = max(0, int(float(days_raw)))
            except (TypeError, ValueError):
                days = None
        never_sold = str(item.get("status") or "").strip().lower() == "nunca_vendeu"
        if quantity <= 0 or (not never_sold and (days is None or days < 30)):
            continue
        if never_sold:
            band_key, band_label, band_order = "never_sold", "Nunca vendeu", 0
        elif days is not None and days >= 180:
            band_key, band_label, band_order = "days_180_plus", "180 dias ou mais", 1
        elif days is not None and days >= 90:
            band_key, band_label, band_order = "days_90_179", "90 a 179 dias", 2
        else:
            band_key, band_label, band_order = "days_30_89", "30 a 89 dias", 3

        capital_known = item.get("custo_cadastrado") is True and item.get("valor_custo_estoque_loja") is not None
        capital_value = (
            round(_ia_stock_chart_number(item.get("valor_custo_estoque_loja")), 2)
            if capital_known
            else None
        )
        stale_items.append(
            {
                "sku": str(item.get("sku") or "").strip(),
                "title": str(item.get("produto") or item.get("nome") or "").strip(),
                "store": str(item.get("loja") or source.get("loja") or "").strip(),
                "quantity": round(quantity, 3),
                "days_without_sale": days,
                "last_sale": str(item.get("ultima_venda") or "").strip(),
                "age_band": band_key,
                "age_band_label": band_label,
                "age_band_order": band_order,
                "capital_known": capital_known,
                "capital_value": capital_value,
                "ranking_metric": "known_capital" if capital_known else "quantity",
                "ranking_value": capital_value if capital_known else round(quantity, 3),
                "value_type": "currency" if capital_known else "quantity",
            }
        )

    band_definitions = (
        ("never_sold", "Nunca vendeu"),
        ("days_180_plus", "180 dias ou mais"),
        ("days_90_179", "90 a 179 dias"),
        ("days_30_89", "30 a 89 dias"),
    )
    age_bands = []
    for key, label in band_definitions:
        matches = [item for item in stale_items if item.get("age_band") == key]
        age_bands.append(
            {
                "key": key,
                "label": label,
                "skus": len(matches),
                "quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in matches), 3),
            }
        )

    ranking = sorted(
        stale_items,
        key=lambda item: (
            0 if item.get("capital_known") else 1,
            -_ia_stock_chart_number(item.get("ranking_value")),
            int(item.get("age_band_order") or 0),
            str(item.get("sku") or ""),
        ),
    )
    total_evaluated = max(0, int(_ia_stock_chart_number(source.get("total_skus_avaliados"))))
    total_returned = max(0, int(_ia_stock_chart_number(source.get("total_skus_retornados"), len(raw_items))))
    truncated = bool(source.get("resultado_truncado")) or total_returned < total_evaluated
    known = [item for item in stale_items if item.get("capital_known")]
    totals = {
        "stale_skus": len(stale_items),
        "stale_quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in stale_items), 3),
        "capital_known": round(sum(_ia_stock_chart_number(item.get("capital_value")) for item in known), 2),
        "skus_with_known_cost": len(known),
        "skus_without_known_cost": len(stale_items) - len(known),
    }
    return {
        "schema": "jk.stock.stale_inventory.v1",
        "kind": "stale_inventory",
        "currency_id": "BRL",
        "store": str(source.get("loja") or "").strip(),
        "reference_date": str(source.get("data_referencia") or "").strip(),
        "metrics": [
            {"key": "quantity", "type": "number"},
            {"key": "days_without_sale", "type": "integer", "nullable": True},
            {"key": "capital_value", "type": "currency", "nullable": True},
        ],
        "totals": totals,
        "age_bands": age_bands,
        "ranking": ranking,
        "ranking_basis": "known_capital_then_quantity",
        "coverage_complete": not truncated,
        "partial": truncated,
        "coverage": {
            "evaluated_skus": total_evaluated,
            "returned_skus": total_returned,
            "stale_skus_returned": len(stale_items),
            "truncated": truncated,
        },
        "pii_included": False,
        "read_only": True,
    }


def _ia_stockout_chart_data(result: dict[str, Any]) -> dict[str, Any]:
    """Build the complete, PII-free contract used by stockout visuals."""

    source = result if isinstance(result, dict) else {}
    raw_items = source.get("itens") if isinstance(source.get("itens"), list) else []
    if not raw_items and source.get("sku"):
        raw_items = [source]
    risk_order = {"critico": 0, "alto": 1, "medio": 2, "baixo": 3, "sem_consumo": 4}
    risk_labels = {
        "critico": "Crítico",
        "alto": "Alto",
        "medio": "Médio",
        "baixo": "Baixo",
        "sem_consumo": "Sem consumo",
    }
    ranking: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        risk = str(item.get("risco_ruptura") or "sem_consumo").strip().lower()
        if risk not in risk_order:
            risk = "sem_consumo"
        days_raw = item.get("dias_ate_ruptura")
        days = None if days_raw is None else round(max(0.0, _ia_stock_chart_number(days_raw)), 2)
        quantity = round(_ia_stock_chart_number(item.get("saldo_total")), 3)
        daily_demand = round(_ia_stock_chart_number(item.get("media_venda_dia")), 4)
        urgency_score = round(
            (len(risk_order) - risk_order[risk]) * 1_000_000
            + max(0.0, 100_000.0 - (days if days is not None else 100_000.0))
            + daily_demand,
            4,
        )
        ranking.append(
            {
                "sku": str(item.get("sku") or "").strip(),
                "title": str(item.get("nome") or item.get("produto") or "").strip(),
                "store": str(item.get("loja") or source.get("loja") or "").strip(),
                "risk": risk,
                "risk_label": risk_labels[risk],
                "risk_order": risk_order[risk],
                "quantity": quantity,
                "store_quantity": round(_ia_stock_chart_number(item.get("saldo_loja")), 3),
                "full_quantity": round(_ia_stock_chart_number(item.get("saldo_full")), 3),
                "sold_in_window": round(_ia_stock_chart_number(item.get("quantidade_vendida_janela")), 3),
                "daily_demand": daily_demand,
                "days_to_stockout": days,
                "stockout_date": str(item.get("data_prevista_ruptura") or "").strip(),
                "urgency_score": urgency_score,
            }
        )
    ranking.sort(
        key=lambda item: (
            int(item.get("risk_order") or 0),
            float(item.get("days_to_stockout")) if item.get("days_to_stockout") is not None else float("inf"),
            -_ia_stock_chart_number(item.get("daily_demand")),
            str(item.get("sku") or ""),
        )
    )
    for position, item in enumerate(ranking, start=1):
        item["rank"] = position

    risk_bands = []
    for key in ("critico", "alto", "medio", "baixo", "sem_consumo"):
        matches = [item for item in ranking if item.get("risk") == key]
        risk_bands.append(
            {
                "key": key,
                "label": risk_labels[key],
                "skus": len(matches),
                "quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in matches), 3),
            }
        )
    total_analyzed = max(0, int(_ia_stock_chart_number(source.get("total_skus_analisados"), len(ranking))))
    truncated = len(ranking) < total_analyzed
    at_risk = [item for item in ranking if item.get("risk") in {"critico", "alto", "medio"}]
    return {
        "schema": "jk.stock.stockout_forecast.v1",
        "kind": "stockout_forecast",
        "store": str(source.get("loja") or "").strip(),
        "reference_date": str(source.get("data_referencia") or "").strip(),
        "lookback_days": max(0, int(_ia_stock_chart_number(source.get("janela_dias")))),
        "metrics": [
            {"key": "quantity", "type": "number"},
            {"key": "daily_demand", "type": "number"},
            {"key": "days_to_stockout", "type": "number", "nullable": True},
        ],
        "totals": {
            "analyzed_skus": total_analyzed,
            "returned_skus": len(ranking),
            "at_risk_skus": len(at_risk),
            "critical_skus": sum(1 for item in ranking if item.get("risk") == "critico"),
            "high_risk_skus": sum(1 for item in ranking if item.get("risk") == "alto"),
            "stock_quantity": round(sum(_ia_stock_chart_number(item.get("quantity")) for item in ranking), 3),
            "daily_demand": round(sum(_ia_stock_chart_number(item.get("daily_demand")) for item in ranking), 4),
        },
        "risk_bands": risk_bands,
        "ranking": ranking,
        "ranking_basis": "risk_then_days_to_stockout",
        "coverage_complete": not truncated,
        "partial": truncated,
        "coverage": {
            "analyzed_skus": total_analyzed,
            "returned_skus": len(ranking),
            "truncated": truncated,
        },
        "pii_included": False,
        "read_only": True,
    }


__all__ = [
    "_ia_stock_chart_number",
    "_ia_stale_stock_chart_data",
    "_ia_stockout_chart_data",
]
