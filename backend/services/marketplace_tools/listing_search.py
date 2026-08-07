"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import client as _client
from . import items as _items

logger = logging.getLogger(__name__)

def search_listing_ids(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    status_item: str,
    offset: int,
    limite: int,
    sku: str = "",
    deadline: Optional[float] = None,
) -> tuple[list[str], dict, dict, Optional[dict]]:
    user_id = str((cfg or {}).get("user_id") or "").strip()
    if not user_id:
        return [], cfg, {"offset": offset, "limit": limite, "total": 0, "next_offset": None, "has_more": False, "pages_fetched": 0}, {
            "code": "seller_id_missing",
            "message": "A conta Mercado Livre nao informou o seller id.",
            "reconnect_required": False,
        }
    url = f"{_client.ML_IA_API_BASE}/users/{user_id}/items/search"
    ids = []
    seen = set()
    api_offset = offset
    total = 0
    pages = 0
    partial_response = False
    partial_content = []
    while len(ids) < limite and pages < 5:
        page_limit = min(20, limite - len(ids))
        params = {"offset": api_offset, "limit": page_limit, "status": status_item}
        if sku:
            params["seller_sku"] = sku
        resp, cfg = _client.request_get(
            client_id,
            loja,
            cfg,
            url,
            params=params,
            timeout=20,
            deadline=deadline,
        )
        if int(getattr(resp, "status_code", 0) or 0) not in {200, 206}:
            code, message, reconnect = _client._ia_ml_http_failure(resp, "Erro ao listar anuncios do Mercado Livre")
            return ids, cfg, {"offset": offset, "limit": limite, "total": total, "next_offset": None, "has_more": False, "pages_fetched": pages}, {
                "code": code,
                "message": message,
                "reconnect_required": reconnect,
            }
        if int(getattr(resp, "status_code", 0) or 0) == 206:
            partial_response = True
            headers = getattr(resp, "headers", {}) or {}
            missing = str(headers.get("X-Content-Missing") or headers.get("x-content-missing") or "").strip()
            if missing:
                partial_content.extend(part.strip() for part in missing.split(",") if part.strip())
        payload = resp.json() or {}
        raw_results = payload.get("results") or []
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _client._ia_ml_int(paging.get("total"), total, 0, 100_000_000)
        pages += 1
        if not raw_results:
            break
        for entry in raw_results:
            item_id = str((entry.get("id") if isinstance(entry, dict) else entry) or "").strip().upper()
            if item_id and item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
                if len(ids) >= limite:
                    break
        api_offset += len(raw_results)
        if api_offset >= total or len(raw_results) < page_limit:
            break
    has_more = bool(total > api_offset)
    return ids, cfg, {
        "offset": offset,
        "limit": limite,
        "returned": len(ids),
        "total": total,
        "next_offset": api_offset if has_more else None,
        "has_more": has_more,
        "pages_fetched": pages,
        "partial_response": partial_response,
        "partial_content": list(dict.fromkeys(partial_content)),
    }, None

def fetch_listing_items(
    client_id: str,
    loja: str,
    cfg: dict,
    item_ids: list[str],
    *,
    deadline: Optional[float] = None,
) -> tuple[list[dict], dict, Optional[dict]]:
    items_by_id = {}
    for start in range(0, len(item_ids), 20):
        chunk = item_ids[start : start + 20]
        resp, cfg = _client.request_get(
            client_id,
            loja,
            cfg,
            f"{_client.ML_IA_API_BASE}/items",
            params={"ids": ",".join(chunk), "include_attributes": "all"},
            timeout=20,
            deadline=deadline,
        )
        if int(getattr(resp, "status_code", 0) or 0) != 200:
            code, message, reconnect = _client._ia_ml_http_failure(resp, "Erro ao detalhar anuncios do Mercado Livre")
            return [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id], cfg, {
                "code": code,
                "message": message,
                "reconnect_required": reconnect,
            }
        payload = resp.json() or []
        for entry in payload if isinstance(payload, list) else []:
            body = entry.get("body") if isinstance(entry, dict) and isinstance(entry.get("body"), dict) else entry
            if not isinstance(body, dict):
                continue
            item_id = str(body.get("id") or "").strip().upper()
            if item_id:
                items_by_id[item_id] = body
    return [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id], cfg, None

def _ia_ml_complete_listing_variations(
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    *,
    deadline: Optional[float] = None,
) -> tuple[dict, dict, Optional[dict]]:
    item_id = str((item or {}).get("id") or "").strip().upper()
    variations = (item or {}).get("variations") if isinstance((item or {}).get("variations"), list) else []
    if not item_id or not variations:
        return item, cfg, None
    resp, cfg = _client.request_get(
        client_id,
        loja,
        cfg,
        f"{_client.ML_IA_API_BASE}/items/{item_id}/variations",
        params={"include_attributes": "all"},
        timeout=15,
        deadline=deadline,
    )
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        code, message, reconnect = _client._ia_ml_http_failure(resp, "Erro ao detalhar variacoes do anuncio")
        return item, cfg, {"code": code, "message": message, "reconnect_required": reconnect}
    payload = resp.json() or []
    detailed = payload.get("variations") or payload.get("results") or [] if isinstance(payload, dict) else payload
    details_by_id = {
        str(variation.get("id") or "").strip(): variation
        for variation in detailed or []
        if isinstance(variation, dict) and str(variation.get("id") or "").strip()
    }
    if not details_by_id:
        return item, cfg, None
    merged_variations = []
    for variation in variations:
        if not isinstance(variation, dict):
            merged_variations.append(variation)
            continue
        detail = details_by_id.get(str(variation.get("id") or "").strip()) or {}
        merged = dict(detail)
        merged.update({key: value for key, value in variation.items() if value not in (None, "", [])})
        if detail.get("attributes") and not merged.get("attributes"):
            merged["attributes"] = detail.get("attributes")
        if detail.get("attribute_combinations") and not merged.get("attribute_combinations"):
            merged["attribute_combinations"] = detail.get("attribute_combinations")
        merged_variations.append(merged)
    enriched = dict(item)
    enriched["variations"] = merged_variations
    return enriched, cfg, None

def validate_sku_matches(
    client_id: str,
    loja: str,
    cfg: dict,
    items: list[dict],
    requested_sku: str,
    *,
    deadline: Optional[float] = None,
) -> tuple[list[dict], dict, dict]:
    verified: list[dict] = []
    rejected_ids: list[str] = []
    unverified_ids: list[str] = []
    errors: list[dict] = []
    variation_requests = 0
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = raw_item
        match = _items._ia_ml_find_exact_sku_match(item, requested_sku)
        if not match and isinstance(item.get("variations"), list) and item.get("variations"):
            variation_requests += 1
            try:
                item, cfg, failure = _ia_ml_complete_listing_variations(
                    client_id,
                    loja,
                    cfg,
                    item,
                    deadline=deadline,
                )
            except requests.exceptions.Timeout:
                failure = {
                    "code": "timeout",
                    "message": "A validacao das variacoes excedeu o tempo disponivel.",
                    "reconnect_required": False,
                }
            if failure:
                item_id = str(item.get("id") or "").strip()
                unverified_ids.append(item_id)
                errors.append({"item_id": item_id, **failure})
                continue
            match = _items._ia_ml_find_exact_sku_match(item, requested_sku)
        if not match:
            rejected_ids.append(str(item.get("id") or "").strip())
            continue
        matched_item = dict(item)
        matched_item["_ia_sku_match"] = match
        verified.append(matched_item)
    return verified, cfg, {
        "requested_sku": str(requested_sku or "").strip(),
        "search_results": len(items),
        "verified_matches": len(verified),
        "rejected_ids": rejected_ids,
        "unverified_ids": unverified_ids,
        "variation_requests": variation_requests,
        "complete": not bool(unverified_ids),
        "errors": errors,
    }

def details_from_item(item: dict) -> dict:
    """Extrai somente detalhes read-only presentes no recurso de item, sem PII."""
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    attributes = []
    for attribute in (item.get("attributes") or [])[:100]:
        if not isinstance(attribute, dict):
            continue
        attributes.append({
            "id": str(attribute.get("id") or "").strip()[:100],
            "name": str(attribute.get("name") or "").strip()[:160],
            "value_id": str(attribute.get("value_id") or "").strip()[:100],
            "value_name": str(attribute.get("value_name") or "").strip()[:300],
        })
    price = item.get("price")
    original_price = item.get("original_price")
    promotion_detected = bool(
        price is not None
        and original_price is not None
        and _client._ia_ml_float(original_price) > _client._ia_ml_float(price)
    )
    fee_fields = {
        "sale_fee_amount": item.get("sale_fee_amount"),
        "listing_fee_amount": item.get("listing_fee_amount"),
    }
    return {
        "category": {
            "id": str(item.get("category_id") or "").strip(),
            "domain_id": str(item.get("domain_id") or "").strip(),
        },
        "attributes": attributes,
        "shipping": {
            "mode": str(shipping.get("mode") or "").strip(),
            "logistic_type": str(shipping.get("logistic_type") or "").strip(),
            "free_shipping": bool(shipping.get("free_shipping")),
            "store_pick_up": bool(shipping.get("store_pick_up")),
        },
        "fees": fee_fields,
        "promotion": {
            "detected_from_price": promotion_detected,
            "price": price,
            "original_price": original_price,
            "campaign_details_available": False,
        },
    }

def commercial_requested(message: Any, explicit: bool = False) -> bool:
    if explicit:
        return True
    normalized = _runtime.normalize_text(str(message or "")).lower()
    return bool(
        re.search(
            r"\b(taxa|taxas|tarifa|tarifas|comissao|comissoes|custo do anuncio|"
            r"frete|custo de envio|preco liquido|valor liquido|recebivel)\b",
            normalized,
        )
    )

def _commercial_base(item: dict) -> dict:
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    return {
        "price": {
            "amount": item.get("price"),
            "regular_amount": item.get("original_price"),
            "currency_id": str(item.get("currency_id") or "BRL").strip(),
            "promotion_id": "",
            "promotion_type": "",
            "source": "item_resource",
            "available": item.get("price") is not None,
        },
        "fees": {
            "sale_fee_amount": None,
            "listing_fee_amount": None,
            "fixed_fee_amount": None,
            "percentage_fee": None,
            "meli_percentage_fee": None,
            "financing_add_on_fee": None,
            "source": "listing_prices",
            "available": False,
            "estimated": False,
        },
        "shipping": {
            "mode": str(shipping.get("mode") or "").strip(),
            "logistic_type": str(shipping.get("logistic_type") or "").strip(),
            "free_shipping": bool(shipping.get("free_shipping")),
            "store_pick_up": bool(shipping.get("store_pick_up")),
            "seller_cost": None,
            "seller_cost_available": False,
            "source": "item_resource",
            "warning": "Custo monetario de frete nao foi estimado sem um contexto oficial exato.",
        },
        "coverage_complete": True,
        "warnings": [],
    }


def _fetch_sale_price(client_id: str, loja: str, cfg: dict, item_id: str, detail: dict, deadline) -> tuple[dict, list[dict]]:
    sources: list[dict] = []
    try:
        sale_resp, cfg = _client.request_get(
            client_id,
            loja,
            cfg,
            f"{_client.ML_IA_API_BASE}/items/{item_id}/sale_price",
            params={"context": "channel_marketplace"},
            timeout=12,
            deadline=deadline,
        )
        sources.append({"provider": "mercado_livre", "resource": "items/{item_id}/sale_price", "method": "GET", "store": loja})
        if int(getattr(sale_resp, "status_code", 0) or 0) == 200:
            payload = sale_resp.json() or {}
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            detail["price"].update({
                "amount": payload.get("amount", detail["price"]["amount"]),
                "regular_amount": payload.get("regular_amount", detail["price"]["regular_amount"]),
                "currency_id": str(payload.get("currency_id") or detail["price"]["currency_id"]).strip(),
                "promotion_id": str(metadata.get("promotion_id") or "").strip(),
                "promotion_type": str(metadata.get("promotion_type") or "").strip(),
                "source": "sale_price",
                "available": payload.get("amount") is not None,
            })
        elif int(getattr(sale_resp, "status_code", 0) or 0) not in {404}:
            detail["coverage_complete"] = False
            detail["warnings"].append("O preco comercial detalhado nao ficou disponivel nesta consulta.")
    except (requests.exceptions.Timeout, HTTPException):
        detail["coverage_complete"] = False
        detail["warnings"].append("O preco comercial detalhado nao ficou disponivel nesta consulta.")
    return cfg, sources


def _fetch_listing_fees(client_id: str, loja: str, cfg: dict, item: dict, detail: dict, deadline) -> tuple[dict, list[dict]]:
    sources: list[dict] = []
    category_id = str(item.get("category_id") or "").strip()
    listing_type_id = str(item.get("listing_type_id") or "").strip()
    price_value = detail["price"].get("amount")
    if category_id and listing_type_id and price_value not in (None, ""):
        site_id = str((cfg or {}).get("site_id") or "MLB").strip().upper()
        if not re.fullmatch(r"ML[A-Z]", site_id):
            site_id = "MLB"
        fee_params = {
            "price": price_value,
            "listing_type_id": listing_type_id,
            "category_id": category_id,
        }
        if detail["shipping"]["logistic_type"]:
            fee_params["logistic_type"] = detail["shipping"]["logistic_type"]
        if detail["shipping"]["mode"]:
            fee_params["shipping_mode"] = detail["shipping"]["mode"]
        try:
            fee_resp, cfg = _client.request_get(
                client_id,
                loja,
                cfg,
                f"{_client.ML_IA_API_BASE}/sites/{site_id}/listing_prices",
                params=fee_params,
                timeout=12,
                deadline=deadline,
            )
            sources.append({"provider": "mercado_livre", "resource": "sites/{site_id}/listing_prices", "method": "GET", "store": loja})
            if int(getattr(fee_resp, "status_code", 0) or 0) == 200:
                payload = fee_resp.json() or {}
                if isinstance(payload, list):
                    candidates = [value for value in payload if isinstance(value, dict)]
                    payload = next(
                        (value for value in candidates if str(value.get("listing_type_id") or "") == listing_type_id),
                        candidates[0] if candidates else {},
                    )
                if not isinstance(payload, dict):
                    payload = {}
                sale_details = payload.get("sale_fee_details") if isinstance(payload.get("sale_fee_details"), dict) else {}
                listing_details = payload.get("listing_fee_details") if isinstance(payload.get("listing_fee_details"), dict) else {}
                detail["fees"].update({
                    "sale_fee_amount": payload.get("sale_fee_amount"),
                    "listing_fee_amount": payload.get("listing_fee_amount"),
                    "fixed_fee_amount": sale_details.get("fixed_fee", listing_details.get("fixed_fee")),
                    "percentage_fee": sale_details.get("percentage_fee"),
                    "meli_percentage_fee": sale_details.get("meli_percentage_fee"),
                    "financing_add_on_fee": sale_details.get("financing_add_on_fee"),
                    "available": True,
                })
            else:
                detail["coverage_complete"] = False
                detail["warnings"].append("As tarifas oficiais do anuncio nao ficaram disponiveis nesta consulta.")
        except (requests.exceptions.Timeout, HTTPException):
            detail["coverage_complete"] = False
            detail["warnings"].append("As tarifas oficiais do anuncio nao ficaram disponiveis nesta consulta.")
    else:
        detail["coverage_complete"] = False
        detail["warnings"].append("Categoria, tipo de anuncio ou preco insuficiente para consultar tarifas oficiais.")
    return cfg, sources


def commercial_details(
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    *,
    deadline: Optional[float] = None,
) -> tuple[dict, dict, list[dict]]:
    """Consulta somente recursos GET oficiais e nunca completa tarifas por estimativa."""
    detail = _commercial_base(item)
    item_id = str(item.get("id") or "").strip()
    cfg, sale_sources = _fetch_sale_price(client_id, loja, cfg, item_id, detail, deadline)
    cfg, fee_sources = _fetch_listing_fees(client_id, loja, cfg, item, detail, deadline)
    detail["warnings"] = detail["warnings"][:10]
    return detail, cfg, [*sale_sources, *fee_sources]

def resolve_owned_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    *,
    deadline: Optional[float] = None,
) -> tuple[Optional[dict], dict, dict]:
    items, cfg, failure = fetch_listing_items(client_id, loja, cfg, [item_id], deadline=deadline)
    if failure:
        return None, cfg, failure
    item = items[0] if items and isinstance(items[0], dict) else None
    expected_seller = str((cfg or {}).get("user_id") or "").strip()
    actual_seller = str((item or {}).get("seller_id") or "").strip()
    if not item or not expected_seller or actual_seller != expected_seller:
        return None, cfg, {
            "code": "listing_not_in_store",
            "message": "O MLB informado nao pertence, ou nao pode ser confirmado, na loja escolhida.",
        }
    return item, cfg, {}
