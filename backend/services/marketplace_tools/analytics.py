"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .contracts import LISTING_SNAPSHOT_SCHEMA, SALES_BY_DAY_SCHEMA
from . import client as _client
from . import order_models as _order_models

logger = logging.getLogger(__name__)

def _ia_ml_fetch_order_city(
    client_id: str,
    store: str,
    cfg: dict,
    raw_order: dict,
    deadline: float,
) -> tuple[str, dict, Optional[str]]:
    embedded = _order_models._ia_ml_order_embedded_city(raw_order)
    if embedded:
        return embedded, cfg, None
    shipment_id = _order_models._ia_ml_order_shipment_id(raw_order)
    if not shipment_id:
        return "", cfg, "Cidade do comprador indisponivel: o pedido nao informou o envio associado."
    response, cfg = _client.request_get(
        client_id,
        store,
        cfg,
        f"{_client.ML_IA_API_BASE}/shipments/{shipment_id}",
        timeout=20,
        deadline=deadline,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code not in {200, 206}:
        return "", cfg, "Cidade do comprador indisponivel no recurso de envio do Mercado Livre."
    payload = response.json() or {}
    # O payload completo do envio contem endereco e contato. Extraia apenas a cidade
    # e descarte o restante sem inclui-lo no resultado, cache ou auditoria.
    address = payload.get("receiver_address") if isinstance(payload.get("receiver_address"), dict) else {}
    city = _order_models._ia_ml_city_name(address.get("city") or address.get("city_name"))
    if not city:
        destination = payload.get("destination") if isinstance(payload.get("destination"), dict) else {}
        shipping_address = destination.get("shipping_address") if isinstance(destination.get("shipping_address"), dict) else {}
        city = _order_models._ia_ml_city_name(shipping_address.get("city") or shipping_address.get("city_name"))
    return city, cfg, None if city else "Cidade do comprador nao informada pelo Mercado Livre."

def aggregate_orders(orders: list[dict]) -> tuple[dict, list[dict], list[str]]:
    totals = {
        "orders": len(orders),
        "items_quantity": 0.0,
        "gross_amount": 0.0,
        "paid_amount": 0.0,
        "refund_amount": 0.0,
        "net_amount": 0.0,
        "currency_id": "BRL",
    }
    by_sku: dict[str, dict] = {}
    unknown_refund = False
    for order in orders:
        totals["currency_id"] = str(order.get("currency_id") or totals["currency_id"])
        gross = _client._ia_ml_float(order.get("gross_amount"))
        paid = _client._ia_ml_float(order.get("paid_amount"))
        refund = order.get("refund_amount")
        totals["gross_amount"] += gross
        totals["paid_amount"] += paid
        if refund is None:
            unknown_refund = True
        else:
            totals["refund_amount"] += _client._ia_ml_float(refund)
        order_items = order.get("items") or []
        item_gross_total = sum(_client._ia_ml_float(item.get("gross_amount")) for item in order_items if isinstance(item, dict))
        for item in order_items:
            if not isinstance(item, dict):
                continue
            sku = str(item.get("sku") or item.get("item_id") or "SEM_SKU").strip()
            row = by_sku.setdefault(sku, {
                "sku": sku,
                "title": str(item.get("title") or "").strip(),
                "orders": set(),
                "quantity": 0.0,
                "gross_amount": 0.0,
                "paid_amount": 0.0,
                "refund_amount": 0.0,
                "net_amount": 0.0,
                "refund_known": True,
            })
            row["orders"].add(str(order.get("order_id") or ""))
            row["quantity"] += _client._ia_ml_float(item.get("quantity"))
            totals["items_quantity"] += _client._ia_ml_float(item.get("quantity"))
            item_gross = _client._ia_ml_float(item.get("gross_amount"))
            share = (item_gross / item_gross_total) if item_gross_total > 0 else (1.0 / max(1, len(order_items)))
            # O total do pedido pode diferir da soma das linhas por descontos
            # ou arredondamentos. Ratear o bruto confirmado pelo mesmo peso
            # garante que a abertura por SKU feche com os indicadores gerais.
            row["gross_amount"] += gross * share
            row["paid_amount"] += paid * share
            if refund is None:
                row["refund_known"] = False
            else:
                row["refund_amount"] += _client._ia_ml_float(refund) * share

    warnings = []
    if unknown_refund:
        totals["refund_amount"] = None
        totals["net_amount"] = None
        warnings.append("Uma ou mais vendas parcialmente reembolsadas nao informaram o valor do reembolso; liquido indisponivel.")
    else:
        totals["net_amount"] = totals["paid_amount"] - totals["refund_amount"]
    for key in ("items_quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
        if totals.get(key) is not None:
            totals[key] = round(float(totals[key]), 2)

    rows = []
    for row in by_sku.values():
        row["orders"] = len([value for value in row["orders"] if value])
        if row.pop("refund_known", True):
            row["net_amount"] = row["paid_amount"] - row["refund_amount"]
        else:
            row["refund_amount"] = None
            row["net_amount"] = None
        rows.append(row)

    # Fecha residuos de centavos no SKU de maior participacao. Sem isso, duas
    # parcelas de 4,995 viram 5,00 + 5,00 para um pedido de 9,99.
    metric_totals = {
        "quantity": totals.get("items_quantity"),
        "gross_amount": totals.get("gross_amount"),
        "paid_amount": totals.get("paid_amount"),
        "refund_amount": totals.get("refund_amount"),
        "net_amount": totals.get("net_amount"),
    }
    for key, expected in metric_totals.items():
        if expected is None or not rows or any(row.get(key) is None for row in rows):
            continue
        for row in rows:
            row[key] = round(float(row.get(key) or 0), 2)
        actual = round(sum(float(row.get(key) or 0) for row in rows), 2)
        residual = round(float(expected) - actual, 2)
        if residual:
            target = max(rows, key=lambda row: (abs(float(row.get(key) or 0)), str(row.get("sku") or "")))
            target[key] = round(float(target.get(key) or 0) + residual, 2)
    for row in rows:
        for key in ("quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
            if row.get(key) is not None:
                row[key] = round(float(row[key]), 2)
    rows.sort(key=lambda row: (-_client._ia_ml_float(row.get("gross_amount")), str(row.get("sku") or "")))
    return totals, rows, warnings

def _ia_ml_order_date_sao_paulo(value: Any) -> Optional[str]:
    """Converte o instante do pedido para o dia civil de America/Sao_Paulo."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    tz = ZoneInfo("America/Sao_Paulo")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.date().isoformat()

def aggregate_orders_by_day(orders: list[dict]) -> tuple[list[dict], list[str]]:
    """Serie diaria numerica e sem PII, derivada somente dos pedidos sanitizados."""
    buckets: dict[Optional[str], dict[str, Any]] = {}
    missing_dates = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        local_date = _ia_ml_order_date_sao_paulo(order.get("date_created"))
        if local_date is None:
            missing_dates += 1
        bucket = buckets.setdefault(local_date, {
            "date": local_date,
            "label": local_date or "Data indisponível",
            "timezone": "America/Sao_Paulo",
            "orders": 0,
            "items_quantity": 0.0,
            "gross_amount": 0.0,
            "paid_amount": 0.0,
            "refund_amount": 0.0,
            "net_amount": 0.0,
            "currency_id": str(order.get("currency_id") or "BRL"),
            "refund_known": True,
        })
        bucket["orders"] += 1
        bucket["currency_id"] = str(order.get("currency_id") or bucket["currency_id"] or "BRL")
        bucket["items_quantity"] += sum(
            _client._ia_ml_float(item.get("quantity"))
            for item in (order.get("items") or [])
            if isinstance(item, dict)
        )
        bucket["gross_amount"] += _client._ia_ml_float(order.get("gross_amount"))
        bucket["paid_amount"] += _client._ia_ml_float(order.get("paid_amount"))
        if order.get("refund_amount") is None:
            bucket["refund_known"] = False
        else:
            bucket["refund_amount"] += _client._ia_ml_float(order.get("refund_amount"))

    points: list[dict] = []
    for _date, bucket in sorted(buckets.items(), key=lambda item: (item[0] is None, item[0] or "")):
        refund_known = bool(bucket.pop("refund_known", True))
        if refund_known:
            bucket["net_amount"] = bucket["paid_amount"] - bucket["refund_amount"]
        else:
            bucket["refund_amount"] = None
            bucket["net_amount"] = None
        for key in ("items_quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
            if bucket.get(key) is not None:
                bucket[key] = round(float(bucket[key]), 2)
        points.append(bucket)

    warnings = []
    if missing_dates:
        warnings.append(
            f"{missing_dates} pedido(s) nao informaram uma data valida; foram mantidos no agregado diario com date=null."
        )
    return points, warnings

def sales_chart_data(
    by_day: list[dict],
    totals: dict,
    *,
    coverage_complete: bool,
    paging: Optional[dict] = None,
) -> dict:
    """Contrato estavel para consumidores de graficos read-only."""
    paging = dict(paging or {}) if isinstance(paging, dict) else {}
    metric_keys = (
        "orders",
        "items_quantity",
        "gross_amount",
        "paid_amount",
        "refund_amount",
        "net_amount",
    )
    reconciliation: dict[str, Optional[bool]] = {}
    for key in metric_keys:
        expected = totals.get(key) if isinstance(totals, dict) else None
        values = [point.get(key) for point in by_day if isinstance(point, dict)]
        if expected is None or any(value is None for value in values):
            reconciliation[key] = None
            continue
        actual = round(sum(_client._ia_ml_float(value) for value in values), 2)
        reconciliation[key] = abs(actual - _client._ia_ml_float(expected)) < 0.01
    temporal_coverage_complete = all(point.get("date") for point in by_day if isinstance(point, dict))
    chart_coverage_complete = bool(coverage_complete and temporal_coverage_complete)
    return {
        "schema": SALES_BY_DAY_SCHEMA,
        "kind": "sales_by_day",
        "timezone": "America/Sao_Paulo",
        "x_key": "date",
        "currency_id": str((totals or {}).get("currency_id") or "BRL"),
        "metrics": [
            {"key": "orders", "type": "integer"},
            {"key": "items_quantity", "type": "number"},
            {"key": "gross_amount", "type": "currency"},
            {"key": "paid_amount", "type": "currency"},
            {"key": "refund_amount", "type": "currency", "nullable": True},
            {"key": "net_amount", "type": "currency", "nullable": True},
        ],
        "points": copy.deepcopy(by_day),
        "totals": {key: (totals or {}).get(key) for key in (*metric_keys, "currency_id")},
        "reconciliation": reconciliation,
        "coverage_complete": chart_coverage_complete,
        "partial": not chart_coverage_complete,
        "coverage": {
            "pages_fetched": _client._ia_ml_int(paging.get("pages_fetched"), 0, 0, 100_000),
            "scanned_orders": _client._ia_ml_int(paging.get("scanned"), (totals or {}).get("orders") or 0, 0, 100_000_000),
            "included_orders": _client._ia_ml_int((totals or {}).get("orders"), 0, 0, 100_000_000),
            "provider_total": _client._ia_ml_int(paging.get("total"), 0, 0, 100_000_000),
            "has_more": bool(paging.get("has_more")),
            "dates_complete": temporal_coverage_complete,
        },
        "pii_included": False,
        "read_only": True,
    }

def _ia_ml_listing_chart_data(matches: list[dict], *, coverage_complete: bool, paging: Optional[dict] = None) -> dict:
    """Snapshot estruturado de anuncios; usa apenas campos publicos/read-only."""
    paging = dict(paging or {}) if isinstance(paging, dict) else {}
    points = []
    status_counts: dict[str, int] = {}
    for item in matches:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown").strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        points.append({
            "item_id": str(item.get("id") or "").strip(),
            "sku": str(item.get("seller_sku") or item.get("id") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "status": status,
            "currency_id": str(item.get("currency_id") or "BRL").strip(),
            "price": item.get("price"),
            "available_quantity": item.get("available_quantity"),
            "sold_quantity": item.get("sold_quantity"),
            "sold_quantity_scope": "acumulado_do_anuncio",
        })
    return {
        "schema": LISTING_SNAPSHOT_SCHEMA,
        "kind": "listing_snapshot",
        "currency_id": str((points[0] if points else {}).get("currency_id") or "BRL"),
        "metrics": [
            {"key": "price", "type": "currency"},
            {"key": "available_quantity", "type": "number"},
            {"key": "sold_quantity", "type": "number", "scope": "acumulado_do_anuncio"},
        ],
        "points": points,
        "summary": {
            "returned": len(points),
            "status_counts": status_counts,
            "available_quantity": round(sum(_client._ia_ml_float(item.get("available_quantity")) for item in points), 2),
            "sold_quantity_accumulated": round(sum(_client._ia_ml_float(item.get("sold_quantity")) for item in points), 2),
        },
        "coverage_complete": bool(coverage_complete),
        "partial": not bool(coverage_complete),
        "coverage": {
            "pages_fetched": _client._ia_ml_int(paging.get("pages_fetched"), 0, 0, 100_000),
            "provider_total": _client._ia_ml_int(paging.get("total"), 0, 0, 100_000_000),
            "has_more": bool(paging.get("has_more")),
        },
        "pii_included": False,
        "read_only": True,
    }
