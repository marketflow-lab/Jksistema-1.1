"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import analytics as _analytics
from . import client as _client
from . import exact_orders as _exact_orders
from . import items as _items
from . import order_models as _order_models
from . import order_search as _order_search

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class _OrderQuery:
    client_id: str
    message: str
    store: str
    statuses: list[str]
    sku: str
    item_ids: list[str]
    order_id: str
    offset: int
    limit: int
    max_pages: int
    report_mode: bool
    details_requested: bool
    latest_targeted: bool
    start: datetime
    end: datetime
    deadline: float
    arguments: dict[str, Any]


def _request_values(mensagem, status, statuses, sku, item_id, offset, limite, modo_relatorio, max_paginas, id_pedido, incluir_detalhes):
    requested_statuses = statuses if statuses is not None else status
    if isinstance(requested_statuses, str):
        status_values = [part.strip().lower() for part in requested_statuses.split(",") if part.strip()]
    elif isinstance(requested_statuses, (list, tuple, set)):
        status_values = [str(part or "").strip().lower() for part in requested_statuses if str(part or "").strip()]
    else:
        status_values = []
    status_values = list(dict.fromkeys(value for value in status_values if value in _client.ML_IA_ORDER_STATUSES))
    if not status_values:
        status_values = ["paid", "partially_refunded"]
    local_status_filter = "partially_refunded" in status_values
    report_mode = bool(modo_relatorio)
    offset_value = _client._ia_ml_int(offset, 0, 0, 1_000_000)
    limit_value = _client._ia_ml_int(limite, 20_000 if report_mode else 50, 1, 20_000 if report_mode else 100)
    max_pages_value = _client._ia_ml_int(max_paginas, 400 if report_mode else 2, 1, 400 if report_mode else 2)
    sku_value = str(sku or "").strip()
    if _items._ia_ml_sku_parece_data(sku_value):
        sku_value = ""
    item_ids = _items._ia_ml_normalizar_item_ids(item_id, mensagem)
    latest_targeted = bool(
        not report_mode
        and _order_models._ia_ml_latest_requested(mensagem)
        and (sku_value or item_ids)
    )
    if latest_targeted:
        offset_value = 0
        limit_value = 1
    order_id_value = re.sub(r"\D+", "", str(id_pedido or "").strip())
    details_requested = _order_models._ia_ml_order_details_requested(mensagem, incluir_detalhes, order_id_value)
    return {
        "statuses": status_values, "local_status_filter": local_status_filter, "report_mode": report_mode,
        "offset": offset_value, "limit": limit_value, "max_pages": max_pages_value, "sku": sku_value,
        "item_ids": item_ids, "latest_targeted": latest_targeted, "order_id": order_id_value,
        "details_requested": details_requested,
    }


def _base_result(context: _OrderQuery, warnings: list, requested_period: dict) -> dict:
    result = _client._ia_ml_base_result(warnings=warnings)
    result.update({
        "found": False, "store": context.store, "coverage": "mercado_livre_api_last_12_months",
        "period": {"requested": requested_period, "effective": context.arguments["period"], "timezone": "America/Sao_Paulo"},
        "orders": [], "by_sku": [], "by_day": [], "totals": {},
        "chart_data": _analytics.sales_chart_data([], {}, coverage_complete=False),
        "paging": {
            "offset": context.offset, "limit": context.limit, "returned": 0, "total": 0,
            "next_offset": None, "has_more": False, "pages_fetched": 0,
            "max_pages": context.max_pages, "report_mode": context.report_mode,
        },
        "truncated": False,
    })
    return result


def _latest_result(context: _OrderQuery, cfg: dict, result: dict) -> dict:
    exact, _cfg = _order_search._ia_ml_find_latest_order_by_target(
        context.client_id, context.store, cfg, seller_id=str(cfg.get("user_id") or ""),
        inicio=context.start, fim=context.end, statuses=context.statuses, sku=context.sku,
        item_ids=context.item_ids, include_buyer_summary=context.details_requested, deadline=context.deadline,
    )
    orders = list(exact.get("orders") or [])
    totals, by_sku, aggregate_warnings = _analytics.aggregate_orders(orders)
    by_day, daily_warnings = _analytics.aggregate_orders_by_day(orders)
    result["warnings"].extend(exact.get("warnings") or [])
    result["warnings"].extend(aggregate_warnings)
    result["warnings"].extend(daily_warnings)
    coverage = dict(exact.get("coverage") or {})
    complete = bool(coverage.get("complete"))
    target = dict(exact.get("target") or {})
    resolved_ids = list(target.get("item_ids") or [])
    result["sources"].append({
        "provider": "mercado_livre", "resource": "users/{seller_id}/items/search", "method": "GET",
        "store": context.store, "filters": list(target.get("filters_used") or []),
    })
    if resolved_ids:
        result["sources"].append({
            "provider": "mercado_livre", "resource": "orders/search", "method": "GET",
            "store": context.store, "filters": ["seller", "q=item_id", "date_created", "sort=date_desc"],
        })
    paging = {
        "offset": 0, "limit": 1, "returned": len(orders), "total": len(orders), "next_offset": None,
        "has_more": not complete, "pages_fetched": int(coverage.get("pages_fetched") or 0),
        "scanned": int(coverage.get("orders_inspected") or 0), "report_mode": False,
    }
    result.update({
        "found": bool(orders), "orders": orders, "by_sku": by_sku, "by_day": by_day, "totals": totals,
        "target": {"sku": str(target.get("sku") or context.sku), "item_ids": resolved_ids,
                   "resolution_complete": bool(target.get("resolution_complete")),
                   "filters_used": list(target.get("filters_used") or [])},
        "match": dict(exact.get("match") or {}), "coverage": coverage, "exact_coverage": coverage,
        "coverage_complete": complete, "evidence": list(exact.get("evidence") or []),
        "partial_response": bool(exact.get("partial_response")), "truncated": not complete, "paging": paging,
        "chart_data": _analytics.sales_chart_data(by_day, totals, coverage_complete=complete, paging=paging),
        "buyer_summary": {"requested": context.details_requested, "allowed_fields": ["buyer_name", "buyer_city"],
                          "cities_returned": 0, "city_lookup_limit": 0, "sensitive_fields_omitted": True},
    })
    if exact.get("error"):
        result.update({"error": str(exact.get("error") or ""), "message": str(exact.get("message") or "")[:240],
                       "reconnect_required": bool(exact.get("reconnect_required"))})
    if not orders and not result.get("error"):
        warning = "A API do Mercado Livre nao encontrou venda deste SKU no periodo consultado." if complete else "A busca terminou sem cobertura suficiente para confirmar a ultima venda deste SKU."
        result["warnings"].append(warning)
    return {"function": "get_mercado_livre_orders", "arguments": context.arguments, "result": result}


def _fetch_pages(context: _OrderQuery, cfg: dict, seller_id: str) -> dict:
    rows, api_offset, total, pages, partial, missing_fields = [], context.offset, 0, 0, False, []
    local_filter = bool(context.sku or context.item_ids or context.order_id or "partially_refunded" in context.statuses)
    collection_limit = max(100, context.limit) if local_filter else context.limit
    collection_limit = min(collection_limit, 20_000 if context.report_mode else 100)
    page_budget = min(context.max_pages, max(1, (collection_limit + 49) // 50))
    while len(rows) < collection_limit and pages < page_budget:
        page_limit = min(50, collection_limit - len(rows))
        params = {
            "seller": seller_id, "order.date_created.from": context.start.isoformat(timespec="milliseconds"),
            "order.date_created.to": context.end.isoformat(timespec="milliseconds"), "sort": "date_desc",
            "offset": api_offset, "limit": page_limit,
        }
        if "partially_refunded" not in context.statuses:
            params["order.status"] = ",".join(context.statuses)
        if context.order_id:
            params["q"] = context.order_id
        elif context.item_ids:
            params["q"] = context.item_ids[0]
        response, cfg = _client.request_get(
            context.client_id, context.store, cfg, f"{_client.ML_IA_API_BASE}/orders/search",
            params=params, timeout=20, deadline=context.deadline,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(response, "Erro ao consultar pedidos do Mercado Livre")
            return {"failure": (code, message, reconnect), "cfg": cfg}
        if status == 206:
            partial = True
            headers = getattr(response, "headers", {}) or {}
            missing = str(headers.get("X-Content-Missing") or headers.get("x-content-missing") or "").strip()
            missing_fields.extend(part.strip() for part in missing.split(",") if part.strip())
        payload = response.json() or {}
        page_rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _client._ia_ml_int(paging.get("total"), total, 0, 100_000_000)
        pages += 1
        if not page_rows:
            break
        rows.extend(page_rows[: collection_limit - len(rows)])
        api_offset += len(page_rows)
        if api_offset >= total or len(page_rows) < page_limit:
            break
    return {"cfg": cfg, "rows": rows, "api_offset": api_offset, "total": total, "pages": pages,
            "partial": partial, "missing_fields": missing_fields}


def _filter_orders(context: _OrderQuery, raw_orders: list[dict]) -> list[tuple[int, dict, dict]]:
    filtered = []
    item_filter = context.item_ids[0] if context.item_ids else ""
    sku_filter = _runtime.normalize_text(context.sku)
    for index, raw in enumerate(raw_orders):
        order = _order_models._ia_ml_sanitize_order(raw, include_shipment_id=context.report_mode)
        if str(order.get("status") or "").strip().lower() not in context.statuses:
            continue
        if context.order_id and str(order.get("order_id") or "") != context.order_id:
            continue
        if item_filter and not any(str(item.get("item_id") or "").upper() == item_filter for item in order.get("items") or []):
            continue
        if sku_filter and not any(_runtime.normalize_text(str(item.get("sku") or "")) == sku_filter for item in order.get("items") or []):
            continue
        filtered.append((index, order, raw))
    return filtered


def _enrich_buyers(context: _OrderQuery, cfg: dict, result: dict, selected: list) -> tuple[dict, int, list[str]]:
    cities, shipment_lookups, warnings = 0, 0, []
    if not context.details_requested:
        return cfg, cities, warnings
    for _index, order, raw in selected[:10]:
        order["buyer_name"] = _order_models._ia_ml_buyer_name(raw)
        order["buyer_city"] = _order_models._ia_ml_order_embedded_city(raw)
        if not order["buyer_city"] and _order_models._ia_ml_order_shipment_id(raw):
            shipment_lookups += 1
        city, cfg, warning = _analytics._ia_ml_fetch_order_city(
            context.client_id, context.store, cfg, raw, context.deadline,
        )
        order["buyer_city"] = city
        cities += int(bool(city))
        if warning:
            warnings.append(warning)
    if len(selected) > 10:
        warnings.append("A cidade foi consultada somente para os 10 primeiros pedidos deste resultado.")
    if shipment_lookups:
        result["sources"].append({"provider": "mercado_livre", "resource": "shipments/{id}", "method": "GET",
                                  "store": context.store, "fields_used": ["receiver_address.city.name"]})
    return cfg, cities, warnings


def _finalize(context: _OrderQuery, result: dict, page: dict, filtered: list, cities: int, detail_warnings: list[str]) -> dict:
    selected = filtered[:context.limit]
    sanitized = [order for _index, order, _raw in selected]
    totals, by_sku, aggregate_warnings = _analytics.aggregate_orders(sanitized)
    by_day, daily_warnings = _analytics.aggregate_orders_by_day(sanitized)
    result["warnings"].extend(aggregate_warnings)
    result["warnings"].extend(daily_warnings)
    result["warnings"].extend(dict.fromkeys(detail_warnings))
    if page["partial"]:
        suffix = f" Campos omitidos: {', '.join(dict.fromkeys(page['missing_fields']))}." if page["missing_fields"] else ""
        result["warnings"].append(f"O Mercado Livre retornou resposta parcial (HTTP 206).{suffix}")
    has_more = bool(len(filtered) > len(selected) or page["total"] > page["api_offset"])
    next_offset = context.offset + selected[-1][0] + 1 if has_more and selected else page["api_offset"] if has_more else None
    complete = not bool(has_more or page["partial"])
    paging = {
        "offset": context.offset, "limit": context.limit, "returned": len(sanitized), "total": page["total"],
        "next_offset": next_offset, "has_more": has_more, "pages_fetched": page["pages"],
        "max_pages": context.max_pages, "page_size": 50, "report_mode": context.report_mode,
        "scanned": len(page["rows"]),
    }
    result.update({
        "found": bool(sanitized), "orders": sanitized, "by_sku": by_sku, "by_day": by_day, "totals": totals,
        "paging": paging, "chart_data": _analytics.sales_chart_data(by_day, totals, coverage_complete=complete, paging=paging),
        "truncated": bool(has_more or page["partial"]), "partial_response": page["partial"], "coverage_complete": complete,
        "buyer_summary": {"requested": context.details_requested, "allowed_fields": ["buyer_name", "buyer_city"],
                          "cities_returned": cities, "city_lookup_limit": 10, "sensitive_fields_omitted": True},
    })
    if context.report_mode and has_more:
        result["warnings"].append(
            f"O relatorio consultou {page['pages']} pagina(s) e {len(page['rows'])} pedido(s) do Mercado Livre, "
            "mas o periodo possui mais registros; a cobertura foi marcada como incompleta."
        )
    if not sanitized:
        result["warnings"].append("A API do Mercado Livre retornou zero pedidos para os filtros informados.")
    return {"function": "get_mercado_livre_orders", "arguments": context.arguments, "result": result}


def _execute(context: _OrderQuery, result: dict) -> dict:
    cfg = _runtime.ml_config(context.client_id, context.store)
    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not seller_id:
        result.update({"error": "seller_id_missing", "message": "A conta Mercado Livre nao informou o seller id."})
        return {"function": "get_mercado_livre_orders", "arguments": context.arguments, "result": result}
    if context.latest_targeted:
        return _latest_result(context, cfg, result)
    result["sources"].append({"provider": "mercado_livre", "resource": "orders/search", "method": "GET", "store": context.store})
    page = _fetch_pages(context, cfg, seller_id)
    if page.get("failure"):
        code, message, reconnect = page["failure"]
        result.update({"error": code, "message": message, "reconnect_required": reconnect})
        return {"function": "get_mercado_livre_orders", "arguments": context.arguments, "result": result}
    filtered = _filter_orders(context, page["rows"])
    selected = filtered[:context.limit]
    _cfg, cities, detail_warnings = _enrich_buyers(context, page["cfg"], result, selected)
    return _finalize(context, result, page, filtered, cities, detail_warnings)


def query(
    client_id: str, mensagem: str, loja: Optional[str] = None, data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None, status: Any = None, sku: Optional[str] = None,
    item_id: Optional[str] = None, offset: int = 0, limite: Optional[int] = None,
    incluir_detalhes: bool = False, force_refresh: bool = False, statuses: Any = None,
    id_pedido: Optional[str] = None, query_deadline: Optional[float] = None,
    modo_relatorio: bool = False, max_paginas: Optional[int] = None,
) -> dict:
    del force_refresh
    values = _request_values(mensagem, status, statuses, sku, item_id, offset, limite, modo_relatorio, max_paginas, id_pedido, incluir_detalhes)
    arguments = {
        "loja": str(loja or "").strip(),
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "statuses": values["statuses"], "sku": values["sku"],
        "item_id": values["item_ids"][0] if values["item_ids"] else "", "order_id": values["order_id"],
        "offset": values["offset"], "limit": values["limit"], "report_mode": values["report_mode"],
        "max_pages": values["max_pages"], "include_buyer_summary": values["details_requested"],
    }
    nome_loja, failure = _client.resolve_store(client_id, loja)
    if not nome_loja:
        return _client._ia_ml_store_failure("get_mercado_livre_orders", arguments, failure)
    if values["order_id"]:
        exact_response = _exact_orders.resolve_exact_order(client_id, values["order_id"], nome_loja, query_deadline=query_deadline)
        exact_arguments = exact_response.setdefault("arguments", {})
        exact_arguments.update(arguments)
        exact_arguments["exact_lookup"] = True
        exact_arguments["force_refresh"] = True
        return exact_response
    inicio, fim, warnings, requested_period, historical_only = _order_models._ia_ml_periodo_orders(mensagem, data_inicio, data_fim)
    if inicio is None or fim is None:
        failure = {"code": "invalid_period", "message": "A data inicial deve ser anterior ou igual a data final.", "available_stores": [nome_loja]}
        return _client._ia_ml_store_failure("get_mercado_livre_orders", arguments, failure)
    arguments["period"] = {"from": inicio.isoformat(timespec="milliseconds"), "to": fim.isoformat(timespec="milliseconds")}
    deadline = _order_models.latest_deadline(query_deadline) if values["latest_targeted"] else time.monotonic() + _client.ML_IA_QUERY_TIMEOUT_SECONDS
    if not values["latest_targeted"] and query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    context = _OrderQuery(client_id, mensagem, nome_loja, values["statuses"], values["sku"], values["item_ids"],
                          values["order_id"], values["offset"], values["limit"], values["max_pages"],
                          values["report_mode"], values["details_requested"], values["latest_targeted"],
                          inicio, fim, deadline, arguments)
    result = _base_result(context, warnings, requested_period)
    if historical_only:
        result.update({
            "status": "historical_period_local_only", "coverage": "historical_period_local_only",
            "historical_period_local_only": True, "api_consulted": False,
            "message": "Periodo fora da cobertura direta do Mercado Livre; consulte o historico local sem misturar as fontes.",
        })
        return {"function": "get_mercado_livre_orders", "arguments": arguments, "result": result}
    if values["local_status_filter"]:
        result["warnings"].append(
            "O filtro partially_refunded nao e aceito pelo search desta conta; consultei o periodo e apliquei paid/partially_refunded localmente."
        )
    try:
        return _execute(context, result)
    except requests.exceptions.Timeout:
        timeout_seconds = _client.ML_IA_LATEST_QUERY_TIMEOUT_SECONDS if context.latest_targeted else _client.ML_IA_QUERY_TIMEOUT_SECONDS
        result.update({
            "error": "timeout", "message": f"A consulta do Mercado Livre excedeu {timeout_seconds} segundos.", "coverage_complete": False,
        })
        if context.latest_targeted:
            _client._ia_ml_mark_exact_failure(result, sku=context.sku, item_ids=context.item_ids, stop_reason="timeout")
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({
            "error": "reconnect_required" if reconnect else "integration_error",
            "message": str(getattr(exc, "detail", None) or exc)[:240], "reconnect_required": reconnect,
        })
        if context.latest_targeted:
            _client._ia_ml_mark_exact_failure(result, sku=context.sku, item_ids=context.item_ids,
                                              stop_reason="reconnect_required" if reconnect else "integration_error")
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar pedidos Mercado Livre (%s): %s", nome_loja, exc)
        result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar os pedidos do Mercado Livre agora."})
        if context.latest_targeted:
            _client._ia_ml_mark_exact_failure(result, sku=context.sku, item_ids=context.item_ids, stop_reason="provider_unavailable")
    return {"function": "get_mercado_livre_orders", "arguments": arguments, "result": result}
