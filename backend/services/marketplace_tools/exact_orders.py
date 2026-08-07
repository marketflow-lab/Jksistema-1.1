"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import copy
import logging
import re
from typing import Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import analytics as _analytics
from . import client as _client
from . import post_sale_claims as _post_sale_claims
from . import post_sale_messages as _post_sale_messages

logger = logging.getLogger(__name__)

def _base_result(selected_store: str, requested: str) -> dict:
    result = _client._ia_ml_base_result()
    result.update({
        "exact_lookup": True, "requested_id": requested, "identifier_type": "", "found": False,
        "store": selected_store, "matched_stores": [], "resolved_order_ids": [], "searched_stores": [],
        "orders": [], "shipment": [], "fulfillment": [], "claims": [], "returns": [], "conversations": [],
        "by_sku": [], "by_day": [], "totals": {},
        "chart_data": _analytics.sales_chart_data([], {}, coverage_complete=False),
        "coverage": "mercado_livre_exact_order_and_post_sale", "coverage_complete": False,
        "partial_response": False, "truncated": False,
        "paging": {"offset": 0, "limit": 0, "returned": 0, "total": 0, "next_offset": None,
                   "has_more": False, "pages_fetched": 0, "report_mode": False},
    })
    return result


def _fetch_pack_orders(client_id: str, store: str, store_cfg: dict, pack: dict, *, deadline,
                       result: dict, sources: list, source_seen: set) -> tuple[list[dict], dict[str, dict], list[str]]:
    order_ids = [
        re.sub(r"\D+", "", str(item.get("id") or ""))
        for item in (pack.get("orders") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    pack_orders, cfg_by_store = [], {}
    for order_id in list(dict.fromkeys(order_ids)):
        linked_found = False
        for linked_store in [store]:
            try:
                linked_cfg = store_cfg if _runtime.normalize_text(linked_store) == _runtime.normalize_text(store) else _runtime.ml_config(client_id, linked_store)
                response, linked_cfg = _client.request_get(
                    client_id, linked_store, linked_cfg, f"{_client.ML_IA_API_BASE}/orders/{order_id}",
                    timeout=20, deadline=deadline,
                )
            except Exception:
                continue
            _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "orders/{id}", linked_store)
            status = int(getattr(response, "status_code", 0) or 0)
            if status not in {200, 206}:
                continue
            linked = response.json() or {}
            if not isinstance(linked, dict) or not linked.get("id"):
                continue
            linked_copy = copy.deepcopy(linked)
            linked_copy["__jk_exact_store"] = linked_store
            pack_orders.append(linked_copy)
            cfg_by_store[linked_store] = linked_cfg
            linked_found = True
            if status == 206:
                result["partial_response"] = True
            break
        if not linked_found:
            result["partial_response"] = True
            result["warnings"].append(f"A order {order_id} do pack nao pode ser confirmada na loja escolhida.")
    return pack_orders, cfg_by_store, order_ids


def _probe_store(client_id: str, store: str, requested: str, *, deadline, result: dict,
                 sources: list, source_seen: set) -> dict:
    diagnostic = {"store": store, "order_http": None, "pack_http": None, "result": "not_found"}
    state = {"raw_orders": [], "cfg": {}, "cfg_by_store": {}, "matched_store": "", "identifier_type": "",
             "forbidden": False, "reconnect": False, "diagnostic": diagnostic}
    try:
        store_cfg = _runtime.ml_config(client_id, store)
    except HTTPException as exc:
        status = int(getattr(exc, "status_code", 0) or 0)
        diagnostic.update({"result": "integration_error", "http": status})
        state["reconnect"] = status == 401
        return state
    try:
        order_response, store_cfg = _client.request_get(
            client_id, store, store_cfg, f"{_client.ML_IA_API_BASE}/orders/{requested}", timeout=20, deadline=deadline,
        )
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "orders/{id}", store)
        order_status = int(getattr(order_response, "status_code", 0) or 0)
        diagnostic["order_http"] = order_status
        payload = order_response.json() or {} if order_status in {200, 206} else {}
        if isinstance(payload, dict) and payload.get("id"):
            state.update({"raw_orders": [payload], "cfg": store_cfg, "cfg_by_store": {store: store_cfg},
                          "matched_store": store, "identifier_type": "order"})
            diagnostic["result"] = "matched_order"
            if order_status == 206:
                result["partial_response"] = True
                missing = _post_sale_messages._ia_ml_exact_partial_fields(order_response)
                result["warnings"].append("A order foi retornada parcialmente pelo Mercado Livre."
                                          + (f" Campos ausentes: {', '.join(missing)}." if missing else ""))
            return state
        state["forbidden"] = order_status == 403
        state["reconnect"] = order_status == 401
        pack_response, store_cfg = _client.request_get(
            client_id, store, store_cfg, f"{_client.ML_IA_API_BASE}/packs/{requested}", timeout=20, deadline=deadline,
        )
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "packs/{id}", store)
        pack_status = int(getattr(pack_response, "status_code", 0) or 0)
        diagnostic["pack_http"] = pack_status
        if pack_status in {200, 206}:
            pack = pack_response.json() or {}
            pack_orders, cfg_by_store, order_ids = _fetch_pack_orders(
                client_id, store, store_cfg, pack, deadline=deadline, result=result,
                sources=sources, source_seen=source_seen,
            )
            if pack_orders:
                state.update({"raw_orders": pack_orders, "cfg": cfg_by_store.get(store) or store_cfg,
                              "cfg_by_store": cfg_by_store, "matched_store": store, "identifier_type": "pack"})
                result["pack"] = {"pack_id": str(pack.get("id") or requested), "status": str(pack.get("status") or "").strip(),
                                  "status_detail": str(pack.get("status_detail") or "").strip()[:240],
                                  "date_created": str(pack.get("date_created") or "").strip(),
                                  "last_updated": str(pack.get("last_updated") or "").strip(), "order_ids": order_ids}
                diagnostic["result"] = "matched_pack"
                result["partial_response"] = bool(result["partial_response"] or pack_status == 206)
                return state
        state["forbidden"] = bool(state["forbidden"] or pack_status == 403)
        state["reconnect"] = bool(state["reconnect"] or pack_status == 401)
        if order_status == 403 or pack_status == 403:
            diagnostic["result"] = "access_denied"
        elif order_status == 401 or pack_status == 401:
            diagnostic["result"] = "reconnect_required"
    except requests.exceptions.Timeout:
        diagnostic["result"] = "timeout"
        result["partial_response"] = True
    except Exception as exc:
        diagnostic.update({"result": "provider_unavailable", "error_type": type(exc).__name__})
        result["partial_response"] = True
    return state


def _not_found(arguments: dict, result: dict, *, reconnect: bool, forbidden: bool) -> dict:
    if reconnect:
        result.update({"error": "reconnect_required", "message": "A autenticacao da loja Mercado Livre precisa ser refeita.",
                       "reconnect_required": True})
    elif forbidden:
        result.update({"error": "access_denied_or_not_found",
                       "message": "O numero nao foi encontrado, ou a loja escolhida recusou acesso ao recurso."})
    else:
        result.update({"error": "not_found",
                       "message": "O numero nao foi localizado como order nem como pack na loja Mercado Livre escolhida."})
    result["warnings"].append("Nenhum historico local foi usado para substituir a consulta exata da API.")
    return {"function": "get_mercado_livre_orders", "arguments": arguments, "result": result}


def _enrich_orders(client_id: str, state: dict, result: dict, sources: list, source_seen: set, deadline) -> list[dict]:
    enriched = []
    for raw_order in state["raw_orders"]:
        order_store = str(raw_order.pop("__jk_exact_store", "") or state["matched_store"]).strip()
        order_cfg = state["cfg_by_store"].get(order_store) or state["cfg"]
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "orders/{id}", order_store)
        order, order_cfg, warnings, partial = _post_sale_claims._ia_ml_exact_enrich_order(
            client_id, order_store, order_cfg, raw_order, deadline, sources, source_seen,
        )
        state["cfg_by_store"][order_store] = order_cfg
        enriched.append(order)
        result["warnings"].extend(warnings)
        result["partial_response"] = bool(result["partial_response"] or partial)
    return enriched


def _finalize(arguments: dict, result: dict, state: dict, enriched: list[dict]) -> dict:
    totals, by_sku, aggregate_warnings = _analytics.aggregate_orders(enriched)
    by_day, daily_warnings = _analytics.aggregate_orders_by_day(enriched)
    result["warnings"].extend(aggregate_warnings)
    result["warnings"].extend(daily_warnings)
    result["warnings"] = list(dict.fromkeys(str(item) for item in result["warnings"] if str(item).strip()))
    result.update({
        "identifier_type": state["identifier_type"], "store": state["matched_store"],
        "matched_stores": list(dict.fromkeys(str(order.get("store") or "") for order in enriched if order.get("store"))),
        "resolved_order_ids": [str(order.get("order_id") or "") for order in enriched if order.get("order_id")],
        "orders": enriched,
        "shipment": [{"order_id": order.get("order_id"), **(order.get("shipment") or {})} for order in enriched],
        "fulfillment": [{"order_id": order.get("order_id"), **(order.get("fulfillment") or {})} for order in enriched],
        "claims": [{"order_id": order.get("order_id"), "items": copy.deepcopy(order.get("claims") or [])} for order in enriched],
        "returns": [{"order_id": order.get("order_id"), "items": copy.deepcopy(order.get("returns") or [])} for order in enriched],
        "conversations": [{"order_id": order.get("order_id"), **copy.deepcopy(order.get("conversations") or {})} for order in enriched],
        "by_sku": by_sku, "by_day": by_day, "totals": totals, "found": bool(enriched),
        "coverage_complete": not bool(result["partial_response"]),
        "truncated": any(bool(((order.get("conversations") or {}).get("post_sale") or {}).get("truncated"))
                         for order in enriched if isinstance(order, dict)),
    })
    result["paging"].update({"limit": len(enriched), "returned": len(enriched), "total": len(enriched)})
    result["chart_data"] = _analytics.sales_chart_data(by_day, totals, coverage_complete=result["coverage_complete"], paging=result["paging"])
    result["data_quality"] = {
        "exact_identifier_resolved": True, "identifier_type": state["identifier_type"],
        "store_confirmed": state["matched_store"], "coverage_complete": result["coverage_complete"],
        "missing_values_are_null": True, "sensitive_buyer_fields_omitted": True,
    }
    return {"function": "get_mercado_livre_orders", "arguments": arguments, "result": result}


def resolve_exact_order(
    client_id: str, requested_id: str, selected_store: str, *, query_deadline: Optional[float] = None,
) -> dict:
    requested = re.sub(r"\D+", "", str(requested_id or ""))
    arguments = {"loja": selected_store, "order_id": requested, "exact_lookup": True, "force_refresh": True}
    result = _base_result(selected_store, requested)
    if not requested:
        result.update({"error": "invalid_order_id", "message": "Informe um numero de venda, order ou pack valido."})
        return {"function": "get_mercado_livre_orders", "arguments": arguments, "result": result}

    # A loja exata e uma fronteira de autorizacao. Um order/pack inexistente
    # nela nunca autoriza busca silenciosa nas demais contas do cliente.
    sources: list[dict] = []
    source_seen: set[tuple[str, str]] = set()
    state = _probe_store(client_id, selected_store, requested, deadline=query_deadline, result=result,
                         sources=sources, source_seen=source_seen)
    result["searched_stores"].append(state["diagnostic"])
    result["sources"] = sources
    if not state["raw_orders"] or not state["matched_store"]:
        return _not_found(arguments, result, reconnect=state["reconnect"], forbidden=state["forbidden"])
    enriched = _enrich_orders(client_id, state, result, sources, source_seen, query_deadline)
    return _finalize(arguments, result, state, enriched)
