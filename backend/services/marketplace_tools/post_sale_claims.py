"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote_plus

from . import client as _client
from . import order_models as _order_models
from . import post_sale_messages as _post_sale_messages

logger = logging.getLogger(__name__)

def _fetch_claim_pages(client_id: str, store: str, cfg: dict, order_id: str, deadline) -> tuple[dict, dict]:
    raw_claims: list[dict] = []
    claims_offset = 0
    total_claims: Optional[int] = None
    complete = True
    while len(raw_claims) < _post_sale_messages.ML_IA_EXACT_MAX_CLAIMS:
        page_limit = min(30, _post_sale_messages.ML_IA_EXACT_MAX_CLAIMS - len(raw_claims))
        response, cfg = _client.request_get(
            client_id,
            store,
            cfg,
            f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/search",
            params={
                "order_id": order_id,
                "limit": page_limit,
                "offset": claims_offset,
                "sort": "last_updated:desc",
            },
            timeout=20,
            deadline=deadline,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            if raw_claims:
                complete = False
                break
            code, message, reconnect = _client._ia_ml_http_failure(response, "Nao foi possivel consultar as reclamacoes.")
            return {"failure": {"available": False, "complete": False, "has_claims": None,
                                "claims": [], "returns": [], "error": code, "message": message,
                                "reconnect_required": reconnect}}, cfg
        payload = response.json() or {}
        page = [item for item in (payload.get("data") or payload.get("results") or []) if isinstance(item, dict)]
        raw_claims.extend(page[: _post_sale_messages.ML_IA_EXACT_MAX_CLAIMS - len(raw_claims)])
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        try:
            total_claims = int(paging.get("total")) if paging.get("total") is not None else total_claims
        except (TypeError, ValueError):
            pass
        complete = complete and status_code == 200 and not bool(_post_sale_messages._ia_ml_exact_partial_fields(response))
        claims_offset += len(page)
        if not page or (total_claims is not None and claims_offset >= total_claims):
            break
    return {"raw_claims": raw_claims, "total_claims": total_claims, "complete": complete}, cfg


def _fetch_claim_detail(client_id: str, store: str, cfg: dict, claim_id: str, claim: dict, deadline) -> tuple[dict, bool, str]:
    response, cfg = _client.request_get(
        client_id, store, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/{quote_plus(claim_id)}/detail",
        timeout=15, deadline=deadline,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status in {200, 206}:
        detail = response.json() or {}
        claim["detail"] = {
            "title": str(detail.get("title") or "").strip()[:300],
            "description": str(detail.get("description") or "").strip()[:1200],
            "problem": str(detail.get("problem") or "").strip()[:1200],
            "due_date": str(detail.get("due_date") or "").strip(),
            "action_responsible": str(detail.get("action_responsible") or "").strip(),
        }
    if status not in {200, 206, 403, 404}:
        return cfg, False, f"Detalhes da reclamacao {claim_id} indisponiveis."
    return cfg, True, ""


def _fetch_claim_conversation(client_id: str, store: str, cfg: dict, claim_id: str, raw_claim: dict,
                              seller_id: str, deadline) -> tuple[dict, dict, bool]:
    response, cfg = _client.request_get(
        client_id, store, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v1/claims/{quote_plus(claim_id)}/messages",
        timeout=15, deadline=deadline,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status in {200, 206}:
        payload = response.json() or []
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("messages") or []
        messages = _post_sale_messages._ia_ml_exact_normalize_claim_messages(
            [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else [], raw_claim, seller_id,
        )
        return cfg, {"available": True, "complete": status == 200, "has_messages": bool(messages),
                     "total_messages": len(messages), "messages": messages}, True
    if status == 404:
        return cfg, {"available": True, "complete": True, "has_messages": False,
                     "total_messages": 0, "messages": []}, True
    return cfg, {"available": False, "complete": False, "has_messages": None,
                 "total_messages": None, "messages": []}, False


def _fetch_claim_return(client_id: str, store: str, cfg: dict, claim_id: str, raw_claim: dict, deadline) -> tuple[dict, dict | None, bool, str]:
    if not _order_models._ia_ml_claim_has_return(raw_claim):
        return cfg, None, True, ""
    response, cfg = _client.request_get(
        client_id, store, cfg, f"{_client.ML_IA_API_BASE}/post-purchase/v2/claims/{quote_plus(claim_id)}/returns",
        timeout=15, deadline=deadline,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    if status in {200, 206}:
        detail = _order_models._ia_ml_sanitize_return_detail(response.json() or {})
        if detail.get("return_id") or detail.get("status"):
            detail["claim_id"] = claim_id
            return cfg, detail, True, ""
        return cfg, None, True, ""
    if status not in {403, 404}:
        return cfg, None, False, f"Detalhes da devolucao da reclamacao {claim_id} indisponiveis."
    return cfg, None, True, ""


def _enrich_claim(client_id: str, store: str, cfg: dict, raw_claim: dict, seller_id: str, deadline) -> tuple[dict, dict, dict | None, bool, list[str]]:
    claim = _post_sale_messages._ia_ml_exact_claim_summary(raw_claim)
    claim_id = str(claim.get("claim_id") or "")
    warnings = []
    cfg, detail_complete, warning = _fetch_claim_detail(client_id, store, cfg, claim_id, claim, deadline)
    if warning:
        warnings.append(warning)
    cfg, conversation, conversation_complete = _fetch_claim_conversation(
        client_id, store, cfg, claim_id, raw_claim, seller_id, deadline,
    )
    claim["conversation"] = conversation
    cfg, return_detail, return_complete, warning = _fetch_claim_return(
        client_id, store, cfg, claim_id, raw_claim, deadline,
    )
    if warning:
        warnings.append(warning)
    if return_detail is not None:
        claim["return_detail"] = return_detail
    return claim, cfg, return_detail, bool(detail_complete and conversation_complete and return_complete), warnings


def _ia_ml_exact_fetch_claims(
    client_id: str, store: str, cfg: dict, order_id: str, seller_id: str, deadline: Optional[float],
) -> tuple[dict, dict]:
    page_state, cfg = _fetch_claim_pages(client_id, store, cfg, order_id, deadline)
    if page_state.get("failure"):
        return page_state["failure"], cfg
    complete = bool(page_state["complete"])
    claims, returns, warnings = [], [], []
    for raw_claim in page_state["raw_claims"]:
        claim, cfg, return_detail, item_complete, item_warnings = _enrich_claim(
            client_id, store, cfg, raw_claim, seller_id, deadline,
        )
        claims.append(claim)
        if return_detail is not None:
            returns.append(return_detail)
        complete = complete and item_complete
        warnings.extend(item_warnings)
    total_claims = page_state["total_claims"]
    if total_claims is None:
        total_claims = len(claims)
    if total_claims > len(claims):
        complete = False
        warnings.append(f"A order possui {total_claims} reclamacoes; foram carregadas {len(claims)}.")
    return {
        "available": True,
        "complete": complete,
        "has_claims": bool(claims),
        "total_claims": total_claims,
        "claims": claims,
        "returns": returns,
        "warnings": warnings,
    }, cfg

def _ia_ml_exact_return_state(claims_payload: dict) -> tuple[Optional[bool], str, str]:
    if not claims_payload.get("available"):
        return None, "unavailable", "Indisponivel"
    returns = [item for item in (claims_payload.get("returns") or []) if isinstance(item, dict)]
    if not returns:
        has_return_claim = any(
            str(claim.get("type") or "").strip().lower() in {"return", "returns"}
            for claim in (claims_payload.get("claims") or [])
            if isinstance(claim, dict)
        )
        if has_return_claim:
            return True, "return_opened", "Devolucao registrada"
        return False, "not_requested", "Sem devolucao registrada"
    shipment_statuses = {
        str(shipment.get("status") or "").strip().lower()
        for item in returns
        for shipment in (item.get("shipments") or [])
        if isinstance(shipment, dict)
    }
    if "delivered" in shipment_statuses:
        return True, "returned", "Produto devolvido ao destino"
    if shipment_statuses & {"shipped", "ready_to_ship"}:
        return True, "return_in_transit", "Devolucao em transito"
    if any(str(item.get("status_money") or "").strip().lower() == "refunded" for item in returns):
        return True, "refunded", "Valor devolvido ao comprador"
    return True, "return_opened", "Devolucao registrada"

def _ia_ml_exact_enrich_order(
    client_id: str,
    store: str,
    cfg: dict,
    raw_order: dict,
    deadline: Optional[float],
    sources: list[dict],
    source_seen: set[tuple[str, str]],
) -> tuple[dict, dict, list[str], bool]:
    warnings: list[str] = []
    partial = False
    order = _order_models._ia_ml_sanitize_order(raw_order, include_buyer_summary=True)
    order["store"] = store
    order["buyer_city"] = ""
    seller_id = str((cfg or {}).get("user_id") or "").strip()

    shipment, cfg = _post_sale_messages._ia_ml_exact_fetch_shipment(client_id, store, cfg, raw_order, deadline)
    _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "shipments/{id}", store)
    order["shipment"] = shipment
    logistic_type = str(shipment.get("logistic_type") or "").strip().lower()
    order["fulfillment"] = {
        "available": bool(logistic_type),
        "is_full": True if logistic_type == "fulfillment" else (False if logistic_type else None),
        "logistic_type": str(shipment.get("logistic_type") or ""),
        "evidence": "shipments/{id}.logistic_type" if logistic_type else "",
    }
    if not shipment.get("complete"):
        partial = True
        if shipment.get("message") or shipment.get("reason"):
            warnings.append(str(shipment.get("message") or shipment.get("reason"))[:300])

    claims_payload, cfg = _ia_ml_exact_fetch_claims(
        client_id,
        store,
        cfg,
        str(order.get("order_id") or ""),
        seller_id,
        deadline,
    )
    _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/search", store)
    if claims_payload.get("claims"):
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/{id}/detail", store)
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/{id}/messages", store)
    if claims_payload.get("returns"):
        _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "post-purchase/v2/claims/{id}/returns", store)
    order["claims"] = claims_payload.get("claims") or []
    order["returns"] = claims_payload.get("returns") or []
    order["claims_status"] = {
        key: claims_payload.get(key)
        for key in ("available", "complete", "has_claims", "total_claims", "error", "message")
        if key in claims_payload
    }
    returned, return_state, return_label = _ia_ml_exact_return_state(claims_payload)
    order["return_status"] = {
        "available": claims_payload.get("available") is True,
        "has_return": returned,
        "state": return_state,
        "label": return_label,
    }
    warnings.extend(str(item)[:300] for item in (claims_payload.get("warnings") or []))
    if not claims_payload.get("complete"):
        partial = True
        if claims_payload.get("message"):
            warnings.append(str(claims_payload.get("message"))[:300])

    pack_id = str(order.get("pack_id") or order.get("order_id") or "").strip()
    post_sale, cfg = _post_sale_messages._ia_ml_exact_fetch_post_sale_messages(
        client_id,
        store,
        cfg,
        pack_id,
        seller_id,
        deadline,
    )
    _post_sale_messages._ia_ml_exact_source_add(sources, source_seen, "messages/packs/{pack_id}/sellers/{seller_id}", store)
    claim_conversations = [
        {
            "claim_id": str(claim.get("claim_id") or ""),
            **(claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}),
        }
        for claim in order.get("claims") or []
        if isinstance(claim, dict)
    ]
    order["conversations"] = {
        "post_sale": post_sale,
        "claims": claim_conversations,
        "total_messages": int(post_sale.get("loaded_messages") or len(post_sale.get("messages") or []))
        + sum(int(item.get("total_messages") or 0) for item in claim_conversations),
        "complete": bool(post_sale.get("complete")) and all(item.get("complete") for item in claim_conversations),
    }
    if not post_sale.get("complete"):
        partial = True
        if post_sale.get("message") or post_sale.get("reason"):
            warnings.append(str(post_sale.get("message") or post_sale.get("reason"))[:300])

    order["data_quality"] = {
        "order": "confirmed",
        "shipment": "confirmed" if shipment.get("available") and shipment.get("complete") else "partial_or_unavailable",
        "claims": "confirmed" if claims_payload.get("available") and claims_payload.get("complete") else "partial_or_unavailable",
        "messages": "confirmed" if post_sale.get("available") and post_sale.get("complete") else "partial_or_unavailable",
    }
    return order, cfg, list(dict.fromkeys(warnings)), partial
