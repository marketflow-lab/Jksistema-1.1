"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

import requests
from . import runtime as _runtime
from . import client as _client
from . import items as _items
from . import order_models as _order_models

logger = logging.getLogger(__name__)

def _ia_ml_resolve_sku_item_ids(
    client_id: str,
    loja: str,
    cfg: dict,
    sku: str,
    *,
    item_ids: Optional[list[str]] = None,
    deadline: Optional[float] = None,
) -> tuple[dict[str, Any], dict]:
    """Resolve an exact seller SKU to listing ids without restricting status."""

    explicit_ids = _items._ia_ml_normalizar_item_ids(*(item_ids or []))
    sku_value = str(sku or "").strip()
    target = {
        "sku": sku_value,
        "item_ids": explicit_ids[:_client.ML_IA_TARGET_MAX_ITEM_IDS],
        "resolution_complete": True,
        "filters_used": ["explicit_item_id"] if explicit_ids else [],
        "sources": [],
        "warnings": [],
        "error": "",
        "message": "",
        "reconnect_required": False,
    }
    if explicit_ids or not sku_value:
        return target, cfg

    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not seller_id:
        target.update({
            "resolution_complete": False,
            "error": "seller_id_missing",
            "message": "A conta Mercado Livre nao informou o seller id.",
        })
        return target, cfg

    url = f"{_client.ML_IA_API_BASE}/users/{seller_id}/items/search"
    seen: set[str] = set()
    successful_filters = 0
    for field in ("seller_sku", "sku"):
        try:
            response, cfg = _client.request_get(
                client_id,
                loja,
                cfg,
                url,
                params={field: sku_value, "offset": 0, "limit": _client.ML_IA_TARGET_MAX_ITEM_IDS},
                timeout=10,
                deadline=deadline,
            )
        except requests.exceptions.Timeout:
            target["resolution_complete"] = False
            target["warnings"].append(f"A resolucao do SKU pelo campo {field} excedeu o prazo.")
            break
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(
                response,
                "Erro ao localizar os anuncios do SKU no Mercado Livre",
            )
            target["resolution_complete"] = False
            target["warnings"].append(f"Filtro {field}: {message}")
            if not target["error"]:
                target.update({
                    "error": code,
                    "message": message,
                    "reconnect_required": reconnect,
                })
            if reconnect:
                break
            continue
        successful_filters += 1
        target["filters_used"].append(field)
        target["sources"].append("users/{seller_id}/items/search")
        payload = response.json() or {}
        raw_results = payload.get("results") or []
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _client._ia_ml_int(paging.get("total"), len(raw_results), 0, 100_000_000)
        if status_code == 206 or total > len(raw_results):
            target["resolution_complete"] = False
        for entry in raw_results:
            item_id = str((entry.get("id") if isinstance(entry, dict) else entry) or "").strip().upper()
            if item_id.startswith("MLB") and item_id not in seen:
                seen.add(item_id)
                target["item_ids"].append(item_id)
                if len(target["item_ids"]) >= _client.ML_IA_TARGET_MAX_ITEM_IDS:
                    break
        if len(target["item_ids"]) >= _client.ML_IA_TARGET_MAX_ITEM_IDS:
            target["resolution_complete"] = target["resolution_complete"] and total <= _client.ML_IA_TARGET_MAX_ITEM_IDS
            break

    target["item_ids"] = list(dict.fromkeys(target["item_ids"]))[:_client.ML_IA_TARGET_MAX_ITEM_IDS]
    if successful_filters and not target["item_ids"]:
        target["message"] = "Nenhum anuncio foi localizado para o SKU informado."
    if successful_filters and target["error"] and target["item_ids"]:
        target["error"] = ""
        target["message"] = ""
        target["reconnect_required"] = False
    return target, cfg

def _ia_ml_order_matches_target(order: dict, sku: str, item_ids: list[str]) -> dict[str, Any]:
    """Match an order item exactly by SKU, or by an item id resolved from it."""

    sanitized = order if isinstance(order.get("items"), list) else _order_models._ia_ml_sanitize_order(order)
    sku_norm = _runtime.normalize_text(str(sku or ""))
    ids = {str(item or "").strip().upper() for item in item_ids if str(item or "").strip()}
    item_id_fallback = None
    for entry in sanitized.get("items") or []:
        if not isinstance(entry, dict):
            continue
        entry_sku = str(entry.get("sku") or "").strip()
        entry_id = str(entry.get("item_id") or "").strip().upper()
        if sku_norm and _runtime.normalize_text(entry_sku) == sku_norm:
            return {
                "exact": True,
                "matched_by": "sku",
                "sku": entry_sku or str(sku or ""),
                "item_id": entry_id,
            }
        if entry_id and entry_id in ids and (not sku_norm or not entry_sku):
            item_id_fallback = {
                "exact": True,
                "matched_by": "item_id_resolved_from_sku" if sku_norm else "item_id",
                "sku": str(sku or entry_sku),
                "item_id": entry_id,
            }
    return item_id_fallback or {
        "exact": False,
        "matched_by": "",
        "sku": str(sku or ""),
        "item_id": "",
    }

def _ia_ml_order_sort_value(order: dict) -> float:
    for value in (
        order.get("date_closed"),
        order.get("date_created"),
        order.get("date_last_updated"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _scan_latest_order_item(
    client_id: str, loja: str, cfg: dict, *, seller_id: str, item_id: str,
    inicio: datetime, fim: datetime, statuses: list[str], sku: str,
    include_buyer_summary: bool, deadline: float, inspected: int, outcome: dict,
) -> tuple[dict, list[tuple[float, dict, dict]], int, int, bool]:
    candidates: list[tuple[float, dict, dict]] = []
    api_offset = item_pages = pages = 0
    item_complete = False
    local_status_filter = "partially_refunded" in statuses
    while item_pages < 20 and inspected < _client.ML_IA_TARGET_MAX_ORDERS:
        params = {
            "seller": seller_id, "q": item_id,
            "order.date_created.from": inicio.isoformat(timespec="milliseconds"),
            "order.date_created.to": fim.isoformat(timespec="milliseconds"),
            "sort": "date_desc", "offset": api_offset, "limit": 50,
        }
        if not local_status_filter:
            params["order.status"] = ",".join(statuses)
        response, cfg = _client.request_get(
            client_id, loja, cfg, f"{_client.ML_IA_API_BASE}/orders/search",
            params=params, timeout=10, deadline=deadline,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(response, "Erro ao consultar pedidos do SKU")
            outcome.update({"error": code, "message": message, "reconnect_required": reconnect})
            outcome["coverage"]["stop_reason"] = code
            break
        if status_code == 206:
            outcome["partial_response"] = True
        payload = response.json() or {}
        rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _client._ia_ml_int(paging.get("total"), len(rows), 0, 100_000_000)
        pages += 1
        item_pages += 1
        if not rows:
            item_complete = True
            break
        found_for_item = False
        for raw in rows:
            inspected += 1
            sanitized = _order_models._ia_ml_sanitize_order(raw, include_buyer_summary=include_buyer_summary)
            if str(sanitized.get("status") or "").strip().lower() not in statuses:
                continue
            match = _ia_ml_order_matches_target(sanitized, sku, [item_id])
            if not match.get("exact"):
                continue
            candidates.append((_ia_ml_order_sort_value(sanitized), sanitized, match))
            found_for_item = item_complete = True
            break
        api_offset += len(rows)
        if found_for_item or api_offset >= total or len(rows) < 50:
            item_complete = True
            break
    return cfg, candidates, inspected, pages, item_complete


def _ia_ml_find_latest_order_by_target(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    seller_id: str,
    inicio: datetime,
    fim: datetime,
    statuses: list[str],
    sku: str,
    item_ids: list[str],
    include_buyer_summary: bool,
    deadline: float,
) -> tuple[dict[str, Any], dict]:
    """Find the newest exact order for every listing mapped to the target SKU."""

    target, cfg = _ia_ml_resolve_sku_item_ids(
        client_id,
        loja,
        cfg,
        sku,
        item_ids=item_ids,
        deadline=deadline,
    )
    ids = list(target.get("item_ids") or [])
    outcome: dict[str, Any] = {
        "target": target,
        "orders": [],
        "match": {"exact": False, "matched_by": "", "sku": str(sku or ""), "item_id": ""},
        "coverage": {
            "complete": bool(target.get("resolution_complete")),
            "stop_reason": "target_not_resolved" if not ids else "exhausted",
            "pages_fetched": 0,
            "orders_inspected": 0,
            "item_ids_total": len(ids),
            "item_ids_checked": 0,
        },
        "evidence": [],
        "warnings": list(target.get("warnings") or []),
        "partial_response": False,
        "error": str(target.get("error") or ""),
        "message": str(target.get("message") or ""),
        "reconnect_required": bool(target.get("reconnect_required")),
    }
    if not ids:
        outcome["coverage"]["complete"] = bool(target.get("resolution_complete") and not target.get("error"))
        return outcome, cfg

    candidates: list[tuple[float, dict, dict]] = []
    inspected = 0
    pages = 0
    checked_ids = 0
    complete = bool(target.get("resolution_complete"))
    for item_id in ids:
        if time.monotonic() >= deadline or inspected >= _client.ML_IA_TARGET_MAX_ORDERS:
            complete = False
            outcome["coverage"]["stop_reason"] = "deadline" if time.monotonic() >= deadline else "order_cap"
            break
        cfg, item_candidates, inspected, item_pages, item_complete = _scan_latest_order_item(
            client_id, loja, cfg, seller_id=seller_id, item_id=item_id, inicio=inicio, fim=fim,
            statuses=statuses, sku=sku, include_buyer_summary=include_buyer_summary,
            deadline=deadline, inspected=inspected, outcome=outcome,
        )
        candidates.extend(item_candidates)
        pages += item_pages
        if outcome.get("partial_response"):
            complete = False
        if outcome.get("error"):
            complete = False
            break
        checked_ids += 1
        if not item_complete:
            complete = False
            outcome["coverage"]["stop_reason"] = "page_cap"

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    if candidates:
        _sort, selected, match = candidates[0]
        outcome["orders"] = [selected]
        outcome["match"] = match
        if complete and checked_ids == len(ids):
            outcome["coverage"]["stop_reason"] = "exact_match"
    elif complete and checked_ids == len(ids):
        outcome["coverage"]["stop_reason"] = "exhausted"
    outcome["coverage"].update({
        "complete": bool(complete and checked_ids == len(ids) and not outcome.get("partial_response") and not outcome.get("error")),
        "pages_fetched": pages,
        "orders_inspected": inspected,
        "item_ids_checked": checked_ids,
    })
    outcome["evidence"] = [
        {"resource": "users/{seller_id}/items/search", "identifiers": ids[:20]},
        {"resource": "orders/search", "identifiers": [str(row[1].get("order_id") or "") for row in candidates[:20]]},
    ]
    return outcome, cfg

def _inspect_return_claim(client_id: str, loja: str, cfg: dict, claim: dict, *, sku: str,
                          resolved_ids: list[str], deadline: float, outcome: dict) -> tuple[dict, dict]:
    state = {"row": None, "match": None, "degraded": False, "order_inspected": 0}
    order_id = str(claim.get("resource_id") or claim.get("order_id") or "").strip()
    if not order_id:
        state["degraded"] = True
        return state, cfg
    response, cfg = _client.request_get(
        client_id, loja, cfg, f"{_client.ML_IA_API_BASE}/orders/{order_id}", timeout=10, deadline=deadline,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status not in {200, 206}:
        code, message, reconnect = _client._ia_ml_http_failure(response, f"Pedido {order_id} indisponivel")
        outcome["warnings"].append(message)
        state["degraded"] = True
        if reconnect:
            outcome.update({"error": code, "message": message, "reconnect_required": True})
            outcome["coverage"]["stop_reason"] = code
        return state, cfg
    if status == 206:
        state["degraded"] = True
        outcome["partial_response"] = True
    state["order_inspected"] = 1
    sanitized_order = _order_models._ia_ml_sanitize_order(response.json() or {})
    match = _ia_ml_order_matches_target(sanitized_order, sku, resolved_ids)
    if not match.get("exact"):
        return state, cfg
    effective_claim = claim
    claim_id = str(claim.get("id") or "").strip()
    if not _order_models._ia_ml_claim_has_return(effective_claim):
        detail_response, cfg = _client.request_get(
            client_id, loja, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/{claim_id}",
            timeout=10, deadline=deadline,
        )
        if int(getattr(detail_response, "status_code", 0) or 0) not in {200, 206}:
            state["degraded"] = True
            outcome["warnings"].append(f"Reclamacao {claim_id}: detalhes indisponiveis.")
            return state, cfg
        detail_payload = detail_response.json() or {}
        if isinstance(detail_payload, dict):
            effective_claim = detail_payload
    if not _order_models._ia_ml_claim_has_return(effective_claim):
        return state, cfg
    return_response, cfg = _client.request_get(
        client_id, loja, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v2/claims/{claim_id}/returns",
        timeout=10, deadline=deadline,
    )
    if int(getattr(return_response, "status_code", 0) or 0) not in {200, 206}:
        state["degraded"] = True
        outcome["warnings"].append(f"Reclamacao {claim_id}: retorno indisponivel.")
        return state, cfg
    return_payload: Any = return_response.json() or {}
    if isinstance(return_payload, list):
        return_payload = next((item for item in return_payload if isinstance(item, dict)), {})
    if not isinstance(return_payload, dict) or not return_payload.get("id"):
        state["degraded"] = True
        return state, cfg
    return_detail = _order_models._ia_ml_sanitize_return_detail(return_payload)
    row = _order_models._ia_ml_sanitize_return_claim(effective_claim)
    row.update({
        "order": sanitized_order, "return_detail": return_detail,
        "event_at": str(return_detail.get("last_updated") or return_detail.get("refund_at")
                        or return_detail.get("date_created") or row.get("last_updated") or ""),
        "match": match,
    })
    state.update({"row": row, "match": match})
    return state, cfg


def _scan_latest_returns(client_id: str, loja: str, cfg: dict, *, seller_id: str, inicio: datetime,
                         fim: datetime, sku: str, resolved_ids: list[str], deadline: float,
                         outcome: dict, coverage_degraded: bool) -> tuple[dict, dict]:
    state = {"claim_offset": 0, "claims_total": 0, "claims_scanned": 0,
             "orders_inspected": 0, "pages": 0, "degraded": coverage_degraded, "found": False}
    while state["claims_scanned"] < _client.ML_IA_TARGET_MAX_CLAIMS and state["pages"] < 5:
        if time.monotonic() >= deadline:
            outcome["coverage"]["stop_reason"] = "deadline"
            state["degraded"] = True
            break
        page_limit = min(20, _client.ML_IA_TARGET_MAX_CLAIMS - state["claims_scanned"])
        response, cfg = _client.request_get(
            client_id, loja, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/search",
            params={"player_user_id": seller_id, "player_role": "respondent", "resource": "order",
                    "range": f"last_updated:after:{inicio.isoformat(timespec='milliseconds')},before:{fim.isoformat(timespec='milliseconds')}",
                    "sort": "last_updated:desc", "offset": state["claim_offset"], "limit": page_limit},
            timeout=10, deadline=deadline,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(response, "Erro ao consultar devolucoes do Mercado Livre")
            outcome.update({"error": code, "message": message, "reconnect_required": reconnect})
            outcome["coverage"]["stop_reason"] = code
            state["degraded"] = True
            break
        if status == 206:
            outcome["partial_response"] = True
            state["degraded"] = True
        payload = response.json() or {}
        claims = [item for item in (payload.get("data") or []) if isinstance(item, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        state["claims_total"] = _client._ia_ml_int(paging.get("total"), state["claims_total"], 0, 100_000_000)
        state["pages"] += 1
        if not claims:
            break
        for claim in claims:
            state["claims_scanned"] += 1
            inspected, cfg = _inspect_return_claim(
                client_id, loja, cfg, claim, sku=sku, resolved_ids=resolved_ids, deadline=deadline, outcome=outcome,
            )
            state["orders_inspected"] += inspected["order_inspected"]
            state["degraded"] = state["degraded"] or inspected["degraded"]
            if inspected["row"] is not None:
                outcome.update({"rows": [inspected["row"]], "match": inspected["match"]})
                outcome["coverage"]["stop_reason"] = "exact_match"
                state["found"] = True
                break
            if outcome.get("reconnect_required"):
                break
        if outcome.get("reconnect_required") or state["found"]:
            break
        state["claim_offset"] += len(claims)
        if state["claim_offset"] >= state["claims_total"] or len(claims) < page_limit:
            break
    return state, cfg


def _finalize_latest_return(outcome: dict, state: dict, resolved_ids: list[str]) -> dict:
    remaining = bool(state["claim_offset"] < state["claims_total"] and not state["found"])
    if not state["found"] and remaining and state["claims_scanned"] >= _client.ML_IA_TARGET_MAX_CLAIMS:
        outcome["coverage"]["stop_reason"] = "claim_cap"
        state["degraded"] = True
    if not state["found"] and not remaining and not outcome.get("error"):
        outcome["coverage"]["stop_reason"] = "exhausted"
    outcome["coverage"].update({
        "complete": bool((state["found"] or not remaining) and not state["degraded"]
                         and not outcome.get("partial_response") and not outcome.get("error")),
        "pages_fetched": state["pages"], "claims_total": state["claims_total"],
        "claims_scanned": state["claims_scanned"], "orders_inspected": state["orders_inspected"],
    })
    outcome["evidence"] = [
        {"resource": "users/{seller_id}/items/search", "identifiers": resolved_ids[:20]},
        {"resource": "post-purchase/v1/claims/search", "identifiers": []},
        {"resource": "orders/{order_id}", "identifiers": [str(row.get("order_id") or "") for row in outcome.get("rows") or []]},
        {"resource": "post-purchase/v2/claims/{claim_id}/returns",
         "identifiers": [str(row.get("claim_id") or "") for row in outcome.get("rows") or []]},
    ]
    return outcome


def _ia_ml_find_latest_return_by_target(
    client_id: str, loja: str, cfg: dict, *, seller_id: str, inicio: datetime, fim: datetime,
    sku: str, item_ids: list[str], deadline: float,
) -> tuple[dict[str, Any], dict]:
    """Find the newest return whose order contains the exact target SKU."""
    target, cfg = _ia_ml_resolve_sku_item_ids(client_id, loja, cfg, sku, item_ids=item_ids, deadline=deadline)
    resolved_ids = list(target.get("item_ids") or [])
    outcome: dict[str, Any] = {
        "target": target, "rows": [],
        "match": {"exact": False, "matched_by": "", "sku": str(sku or ""), "item_id": ""},
        "coverage": {"complete": False, "stop_reason": "exhausted", "pages_fetched": 0,
                     "claims_total": 0, "claims_scanned": 0, "orders_inspected": 0},
        "evidence": [], "warnings": list(target.get("warnings") or []), "partial_response": False,
        "error": str(target.get("error") or ""), "message": str(target.get("message") or ""),
        "reconnect_required": bool(target.get("reconnect_required")),
    }
    if not sku and not resolved_ids:
        outcome.update({"error": str(target.get("error") or "target_required"),
                        "message": str(target.get("message") or "Informe um SKU ou MLB para localizar a devolucao exata."),
                        "reconnect_required": bool(target.get("reconnect_required"))})
        outcome["coverage"]["stop_reason"] = "target_not_resolved"
        return outcome, cfg
    degraded = bool(not target.get("resolution_complete") or target.get("error"))
    state, cfg = _scan_latest_returns(
        client_id, loja, cfg, seller_id=seller_id, inicio=inicio, fim=fim, sku=sku,
        resolved_ids=resolved_ids, deadline=deadline, outcome=outcome, coverage_degraded=degraded,
    )
    return _finalize_latest_return(outcome, state, resolved_ids), cfg
