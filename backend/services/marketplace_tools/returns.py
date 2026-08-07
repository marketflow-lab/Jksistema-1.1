"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import client as _client
from . import items as _items
from . import order_models as _order_models
from . import order_search as _order_search

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class _ReturnQuery:
    client_id: str
    store: str
    sku: str
    item_ids: list[str]
    limit: int
    offset: int
    latest_targeted: bool
    start: datetime
    end: datetime
    deadline: float
    arguments: dict[str, Any]


def _base_result(context: _ReturnQuery, requested_period: dict) -> dict:
    result = _client._ia_ml_base_result()
    result.update({
        "found": False, "store": context.store, "coverage": "mercado_livre_claims_returns_api",
        "period": {"requested": requested_period, "effective": requested_period, "timezone": "America/Sao_Paulo", "field": "last_updated"},
        "devolucoes": [], "returns": [],
        "paging": {"offset": context.offset, "limit": context.limit, "returned": 0, "total": 0,
                   "next_offset": None, "has_more": False},
        "latest_first": True,
    })
    return result


def _latest_result(context: _ReturnQuery, cfg: dict, seller_id: str, result: dict) -> dict:
    exact, _cfg = _order_search._ia_ml_find_latest_return_by_target(
        context.client_id, context.store, cfg, seller_id=seller_id, inicio=context.start, fim=context.end,
        sku=context.sku, item_ids=context.item_ids, deadline=context.deadline,
    )
    rows = list(exact.get("rows") or [])
    coverage = dict(exact.get("coverage") or {})
    complete = bool(coverage.get("complete"))
    target = dict(exact.get("target") or {})
    resolved_ids = list(target.get("item_ids") or [])
    result["warnings"].extend(exact.get("warnings") or [])
    result["sources"].extend([
        {"provider": "mercado_livre", "resource": "users/{seller_id}/items/search", "method": "GET",
         "store": context.store, "filters": list(target.get("filters_used") or [])},
        {"provider": "mercado_livre", "resource": "post-purchase/v1/claims/search", "method": "GET",
         "store": context.store, "filters": ["player_user_id", "resource=order", "last_updated", "sort=desc"]},
    ])
    if rows:
        result["sources"].extend([
            {"provider": "mercado_livre", "resource": "orders/{order_id}", "method": "GET", "store": context.store},
            {"provider": "mercado_livre", "resource": "post-purchase/v2/claims/{claim_id}/returns", "method": "GET", "store": context.store},
        ])
    result.update({
        "found": bool(rows), "devolucoes": rows, "returns": rows,
        "target": {"sku": str(target.get("sku") or context.sku), "item_ids": resolved_ids,
                   "resolution_complete": bool(target.get("resolution_complete")),
                   "filters_used": list(target.get("filters_used") or [])},
        "match": dict(exact.get("match") or {}), "coverage": coverage, "exact_coverage": coverage,
        "coverage_complete": complete, "evidence": list(exact.get("evidence") or []),
        "partial_response": bool(exact.get("partial_response")),
        "paging": {
            "offset": 0, "limit": 1, "returned": len(rows), "total": len(rows), "total_complete": complete,
            "claims_total": int(coverage.get("claims_total") or 0), "claims_scanned": int(coverage.get("claims_scanned") or 0),
            "orders_inspected": int(coverage.get("orders_inspected") or 0),
            "pages_fetched": int(coverage.get("pages_fetched") or 0), "next_offset": None, "has_more": not complete,
        },
    })
    if exact.get("error"):
        result.update({"error": str(exact.get("error") or ""), "message": str(exact.get("message") or "")[:240],
                       "reconnect_required": bool(exact.get("reconnect_required"))})
    if not rows and not result.get("error"):
        warning = "A API do Mercado Livre nao encontrou devolucao deste SKU no periodo consultado." if complete else "A busca terminou sem cobertura suficiente para confirmar a ultima devolucao deste SKU."
        result["warnings"].append(warning)
    return {"function": "get_mercado_livre_returns", "arguments": context.arguments, "result": result}


def _return_from_claim(context: _ReturnQuery, cfg: dict, claim: dict) -> tuple[dict | None, dict, bool, bool]:
    effective = claim
    detail_used = False
    if not _order_models._ia_ml_claim_has_return(effective):
        claim_id = str(claim.get("id") or "").strip()
        if not claim_id:
            return None, cfg, False, False
        response, cfg = _client.request_get(
            context.client_id, context.store, cfg,
            f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/{claim_id}", timeout=20, deadline=context.deadline,
        )
        if int(getattr(response, "status_code", 0) or 0) not in {200, 206}:
            return None, cfg, False, False
        detail_used = True
        payload = response.json() or {}
        if isinstance(payload, dict):
            effective = payload
    if not _order_models._ia_ml_claim_has_return(effective):
        return None, cfg, detail_used, False
    claim_id = str(effective.get("id") or claim.get("id") or "").strip()
    if not claim_id:
        return None, cfg, detail_used, False
    response, cfg = _client.request_get(
        context.client_id, context.store, cfg,
        f"{_client.ML_IA_API_BASE}/post-purchase/v2/claims/{claim_id}/returns", timeout=20, deadline=context.deadline,
    )
    if int(getattr(response, "status_code", 0) or 0) not in {200, 206}:
        return None, cfg, detail_used, False
    payload: Any = response.json() or {}
    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), {})
    if not isinstance(payload, dict) or not payload.get("id"):
        return None, cfg, detail_used, False
    row = _order_models._ia_ml_sanitize_return_claim(effective)
    row["return_detail"] = _order_models._ia_ml_sanitize_return_detail(payload)
    return row, cfg, detail_used, True


def _scan_claims(context: _ReturnQuery, cfg: dict, seller_id: str, result: dict) -> dict:
    target_matches = context.offset + context.limit
    scan_cap = min(100, max(20, target_matches * 10))
    claim_offset = scanned = total = pages = 0
    partial = detail_used = return_used = False
    matches: list[dict[str, Any]] = []
    result["sources"].append({
        "provider": "mercado_livre", "resource": "post-purchase/v1/claims/search", "method": "GET",
        "store": context.store, "filters": ["player_user_id", "player_role=respondent", "resource=order", "last_updated"],
    })
    while len(matches) < target_matches and scanned < scan_cap and pages < 5:
        page_limit = min(20, scan_cap - scanned)
        params = {
            "player_user_id": seller_id, "player_role": "respondent", "resource": "order",
            "range": f"last_updated:after:{context.start.isoformat(timespec='milliseconds')},before:{context.end.isoformat(timespec='milliseconds')}",
            "sort": "last_updated:desc", "offset": claim_offset, "limit": page_limit,
        }
        response, cfg = _client.request_get(
            context.client_id, context.store, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/search",
            params=params, timeout=20, deadline=context.deadline,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(response, "Erro ao consultar devolucoes do Mercado Livre")
            if pages == 0:
                return {"failure": (code, message, reconnect), "cfg": cfg}
            result["warnings"].append("A busca de reclamacoes foi interrompida antes de concluir a varredura de devolucoes.")
            break
        partial = partial or status == 206
        payload = response.json() or {}
        claims = [item for item in (payload.get("data") or []) if isinstance(item, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _client._ia_ml_int(paging.get("total"), total, 0, 100_000_000)
        pages += 1
        if not claims:
            break
        for claim in claims:
            scanned += 1
            row, cfg, used_detail, used_return = _return_from_claim(context, cfg, claim)
            detail_used = detail_used or used_detail
            return_used = return_used or used_return
            if row is not None:
                matches.append(row)
            if len(matches) >= target_matches:
                break
        claim_offset += len(claims)
        if claim_offset >= total or len(claims) < page_limit:
            break
    return {"cfg": cfg, "matches": matches, "claim_offset": claim_offset, "scanned": scanned, "total": total,
            "pages": pages, "partial": partial, "detail_used": detail_used, "return_used": return_used}


def _append_detail_sources(context: _ReturnQuery, result: dict, scan: dict, rows: list[dict]) -> None:
    if scan["detail_used"]:
        result["sources"].append({"provider": "mercado_livre", "resource": "post-purchase/v1/claims/{claim_id}",
                                  "method": "GET", "store": context.store, "fields_used": ["related_entities"]})
    if scan["return_used"]:
        result["sources"].append({"provider": "mercado_livre", "resource": "post-purchase/v2/claims/{claim_id}/returns",
                                  "method": "GET", "store": context.store})
    if rows:
        result["sources"].append({"provider": "mercado_livre", "resource": "orders/{order_id}",
                                  "method": "GET", "store": context.store})


def _enrich_orders(context: _ReturnQuery, cfg: dict, result: dict, rows: list[dict]) -> dict:
    for row in rows[:10]:
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            continue
        response, cfg = _client.request_get(
            context.client_id, context.store, cfg, f"{_client.ML_IA_API_BASE}/orders/{order_id}",
            timeout=20, deadline=context.deadline,
        )
        if int(getattr(response, "status_code", 0) or 0) in {200, 206}:
            row["order"] = _order_models._ia_ml_sanitize_order(response.json() or {})
        else:
            result["warnings"].append(f"Pedido {order_id}: detalhes indisponiveis na API do Mercado Livre.")
    return cfg


def _finalize(context: _ReturnQuery, result: dict, scan: dict) -> dict:
    rows = scan["matches"][context.offset : context.offset + context.limit]
    _append_detail_sources(context, result, scan, rows)
    _enrich_orders(context, scan["cfg"], result, rows)
    remaining = bool(scan["claim_offset"] < scan["total"])
    has_more = bool(len(scan["matches"]) > context.offset + len(rows) or remaining)
    next_offset = context.offset + len(rows) if has_more and rows else None
    result.update({
        "found": bool(rows), "devolucoes": rows, "returns": rows,
        "paging": {
            "offset": context.offset, "limit": context.limit, "returned": len(rows),
            "total": context.offset + len(rows), "total_complete": not has_more,
            "claims_total": scan["total"], "claims_scanned": scan["scanned"], "pages_fetched": scan["pages"],
            "next_offset": next_offset, "has_more": has_more,
        },
        "coverage_complete": bool(rows) or not has_more, "partial_response": scan["partial"],
    })
    if scan["partial"]:
        result["warnings"].append("O Mercado Livre retornou resposta parcial (HTTP 206) para devolucoes.")
    if not rows and has_more:
        result["warnings"].append("A varredura atingiu o limite de 100 reclamacoes sem confirmar uma devolucao; informe um periodo menor para ampliar a precisao.")
    if not rows:
        result["warnings"].append("A API do Mercado Livre nao retornou devolucoes para o periodo informado.")
    return {"function": "get_mercado_livre_returns", "arguments": context.arguments, "result": result}


def _execute(context: _ReturnQuery, result: dict) -> dict:
    cfg = _runtime.ml_config(context.client_id, context.store)
    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not seller_id:
        result.update({"error": "seller_id_missing", "message": "A conta Mercado Livre nao informou o seller id."})
        return {"function": "get_mercado_livre_returns", "arguments": context.arguments, "result": result}
    if context.latest_targeted:
        return _latest_result(context, cfg, seller_id, result)
    scan = _scan_claims(context, cfg, seller_id, result)
    if scan.get("failure"):
        code, message, reconnect = scan["failure"]
        result.update({"error": code, "message": message, "reconnect_required": reconnect})
        return {"function": "get_mercado_livre_returns", "arguments": context.arguments, "result": result}
    return _finalize(context, result, scan)


def query(
    client_id: str, mensagem: str, loja: Optional[str] = None, data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None, limite: int = 1, offset: int = 0, sku: Optional[str] = None,
    item_id: Optional[str] = None, force_refresh: bool = False, query_deadline: Optional[float] = None,
) -> dict:
    """Consulta devolucoes diretamente na API de claims/returns do Mercado Livre."""
    del force_refresh
    sku_value = str(sku or "").strip()
    if _items._ia_ml_sku_parece_data(sku_value):
        sku_value = ""
    item_ids = _items._ia_ml_normalizar_item_ids(item_id, mensagem)
    latest_targeted = bool(_order_models._ia_ml_latest_requested(mensagem) and (sku_value or item_ids))
    limit_value = _client._ia_ml_int(limite, 1, 1, 100)
    offset_value = _client._ia_ml_int(offset, 0, 0, 10_000)
    if latest_targeted:
        limit_value = 1
        offset_value = 0
    arguments = {
        "loja": str(loja or "").strip(),
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "sku": sku_value,
        "item_id": item_ids[0] if item_ids else "",
        "limit": limit_value,
        "offset": offset_value,
        "sort": "last_updated:desc",
        "association": "related_entities:return",
    }
    nome_loja, failure = _client.resolve_store(client_id, loja)
    if not nome_loja:
        return _client._ia_ml_store_failure("get_mercado_livre_returns", arguments, failure)
    inicio, fim, requested_period = _order_models._ia_ml_claims_period(mensagem, data_inicio, data_fim)
    arguments["period"] = requested_period
    if inicio is None or fim is None:
        return _client._ia_ml_store_failure(
            "get_mercado_livre_returns", arguments,
            {"code": "invalid_period", "message": "A data inicial deve ser anterior ou igual a data final.", "available_stores": [nome_loja]},
        )
    deadline = (
        _order_models.latest_deadline(query_deadline)
        if latest_targeted
        else time.monotonic() + _client.ML_IA_QUERY_TIMEOUT_SECONDS
    )
    if not latest_targeted and query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    context = _ReturnQuery(client_id, nome_loja, sku_value, item_ids, limit_value, offset_value,
                           latest_targeted, inicio, fim, deadline, arguments)
    result = _base_result(context, requested_period)
    try:
        return _execute(context, result)
    except requests.exceptions.Timeout:
        timeout_seconds = _client.ML_IA_LATEST_QUERY_TIMEOUT_SECONDS if latest_targeted else _client.ML_IA_QUERY_TIMEOUT_SECONDS
        result.update({
            "error": "timeout",
            "message": f"A consulta de devolucoes do Mercado Livre excedeu {timeout_seconds} segundos.",
            "coverage_complete": False,
        })
        if latest_targeted:
            _client._ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="timeout")
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({
            "error": "reconnect_required" if reconnect else "integration_error",
            "message": str(getattr(exc, "detail", None) or exc)[:240],
            "reconnect_required": reconnect,
        })
        if latest_targeted:
            _client._ia_ml_mark_exact_failure(
                result,
                sku=sku_value,
                item_ids=item_ids,
                stop_reason="reconnect_required" if reconnect else "integration_error",
            )
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar devolucoes Mercado Livre (%s): %s", nome_loja, exc)
        result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar as devolucoes do Mercado Livre agora."})
        if latest_targeted:
            _client._ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="provider_unavailable")
    return {"function": "get_mercado_livre_returns", "arguments": arguments, "result": result}
