"""Read-only fetchers used by advanced Mercado Livre reports."""

from __future__ import annotations

import copy
import re
import time
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException

from .utils import _assistant_float


def _result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    return value if isinstance(value, dict) else {}


def _orders(ctx: dict[str, Any], *, client_id: str, store: str, date_from: str, date_to: str, offset: int, limit: int) -> dict[str, Any]:
    cache_key = (store, date_from, date_to, int(offset), int(limit))
    cache = ctx["order_cache"]
    if cache_key in cache:
        return copy.deepcopy(cache[cache_key])
    payload = ctx["orders"].query(
        client_id, "conciliacao financeira somente leitura do relatorio", loja=store,
        data_inicio=date_from, data_fim=date_to, offset=offset, limite=limit,
        modo_relatorio=True, max_paginas=2, force_refresh=True,
    )
    value = _result(payload)
    cache[cache_key] = copy.deepcopy(value)
    return value


def _listing_status_pages(ctx: dict[str, Any], client_id: str, store: str, status: str) -> tuple[dict[str, dict[str, Any]], list[str], bool]:
    collected: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    offset = 0
    for _page in range(1000):
        payload = ctx["listings"].query(
            client_id, f"listar anuncios {status}", loja=store, limite=100, status=status,
            offset=offset, incluir_detalhes=False, incluir_comercial=False, force_refresh=True,
        )
        value = _result(payload)
        warnings.extend(str(item) for item in value.get("warnings") or [])
        if value.get("error"):
            return collected, warnings, False
        for item in value.get("matches") or []:
            if isinstance(item, dict) and _assistant_float(item.get("available_quantity")) > 0:
                collected[str(item.get("id") or "").upper()] = item
        paging = value.get("paging") if isinstance(value.get("paging"), dict) else {}
        if not paging.get("has_more") or paging.get("next_offset") in (None, ""):
            return collected, warnings, True
        offset = int(paging["next_offset"])
    warnings.append(f"Paginacao de anuncios {status} excedeu o limite de seguranca em {store}.")
    return collected, warnings, False


def _sold_listing_ids(ctx: dict[str, Any], client_id: str, store: str) -> tuple[set[str], list[str], bool]:
    sold_ids: set[str] = set()
    warnings: list[str] = []
    complete = True
    cursor = date.fromisoformat(ctx["period_start"])
    end = date.fromisoformat(ctx["period_end"])
    while cursor <= end:
        offset = 0
        for _page in range(400):
            orders = _orders(
                ctx, client_id=client_id, store=store, date_from=cursor.isoformat(),
                date_to=cursor.isoformat(), offset=offset, limit=60,
            )
            for order in orders.get("orders") or []:
                if not isinstance(order, dict):
                    continue
                for item in order.get("items") or []:
                    item_id = str(item.get("item_id") or "").strip().upper() if isinstance(item, dict) else ""
                    if re.fullmatch(r"MLB\d+", item_id):
                        sold_ids.add(item_id)
            paging = orders.get("paging") if isinstance(orders.get("paging"), dict) else {}
            if not paging.get("has_more") or paging.get("next_offset") in (None, ""):
                break
            offset = int(paging["next_offset"])
        else:
            complete = False
            warnings.append(f"Paginacao de pedidos excedeu o limite de seguranca em {store}/{cursor.isoformat()}.")
        cursor += timedelta(days=1)
    return sold_ids, warnings, complete


def _listing(ctx: dict[str, Any], *, client_id: str, store: str, relevant_skus: list[str]) -> dict[str, Any]:
    del relevant_skus
    collected: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    complete = True
    for status in ("active", "paused"):
        items, local_warnings, local_complete = _listing_status_pages(ctx, client_id, store, status)
        collected.update(items)
        warnings.extend(local_warnings)
        complete = complete and local_complete
    sold_ids, sold_warnings, sold_complete = _sold_listing_ids(ctx, client_id, store)
    warnings.extend(sold_warnings)
    complete = complete and sold_complete
    for item_id in sorted(sold_ids - set(collected)):
        payload = ctx["listings"].query(
            client_id, f"consultar {item_id}", loja=store, item_id=item_id, limite=1,
            incluir_detalhes=False, incluir_comercial=False, force_refresh=True,
        )
        matches = [item for item in _result(payload).get("matches") or [] if isinstance(item, dict)]
        if matches:
            collected[item_id] = matches[0]
        else:
            complete = False
            warnings.append(f"Anuncio vendido {store}/{item_id} nao ficou disponivel para o snapshot atual.")
    return {"items": list(collected.values()), "coverage": {"complete": complete},
        "warnings": list(dict.fromkeys(warnings))[:100]}


def _commercial_base(ctx: dict[str, Any], client_id: str, store: str, item_id: str, listing: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = ctx["listings"].query(
        client_id, f"consultar dados comerciais {item_id}", loja=store, item_id=item_id,
        limite=1, incluir_detalhes=True, incluir_comercial=True, force_refresh=True,
    )
    result = _result(payload)
    matches = [item for item in result.get("matches") or [] if isinstance(item, dict)]
    exact = matches[0] if matches else {}
    details = exact.get("details") if isinstance(exact.get("details"), dict) else {}
    commercial = copy.deepcopy(details.get("commercial") if isinstance(details.get("commercial"), dict) else {})
    shipping = commercial.get("shipping") if isinstance(commercial.get("shipping"), dict) else {}
    try:
        cfg = ctx["runtime"].ml_config(client_id, store)
        shipping_info, _cfg = ctx["pricing"]._ml_obter_frete_detalhado(
            client_id, store, cfg, item_id, shipping,
            contexto_frete={"item_price": ((commercial.get("price") or {}).get("amount") if isinstance(commercial.get("price"), dict) else exact.get("price")),
                "listing_type_id": exact.get("listing_type_id") or listing.get("listing_type_id"),
                "category_id": exact.get("category_id") or listing.get("category_id"), "mode": shipping.get("mode"),
                "logistic_type": shipping.get("logistic_type"), "free_shipping": shipping.get("free_shipping")},
        )
        seller_cost = shipping_info.get("shipping_seller_cost")
        shipping.update(seller_cost=seller_cost, seller_cost_available=seller_cost is not None,
            source=str(shipping_info.get("shipping_cost_retry_source") or "shipping_options"))
        if seller_cost is None:
            commercial["coverage_complete"] = False
            commercial.setdefault("warnings", []).append("Custo monetario do frete do vendedor indisponivel.")
    except Exception as exc:
        shipping.update(seller_cost=None, seller_cost_available=False)
        commercial["coverage_complete"] = False
        commercial.setdefault("warnings", []).append(f"Frete do vendedor indisponivel: {type(exc).__name__}.")
    commercial["shipping"] = shipping
    return commercial, exact, result


def _variation_fee(ctx: dict[str, Any], client_id: str, store: str, cfg: dict[str, Any], entry: dict[str, Any], *, price: Any, listing_type: str, category: str, site_id: str) -> dict[str, Any]:
    if price is None or not listing_type or not category:
        entry["coverage_complete"] = False
        entry["warnings"].append("Variacao sem preco, categoria ou tipo de anuncio para tarifa oficial.")
        return cfg
    params: dict[str, Any] = {"price": price, "listing_type_id": listing_type, "category_id": category}
    if entry["shipping"]["logistic_type"]:
        params["logistic_type"] = entry["shipping"]["logistic_type"]
    if entry["shipping"]["mode"]:
        params["shipping_mode"] = entry["shipping"]["mode"]
    response, cfg = ctx["client"].request_get(
        client_id, store, cfg, f"{ctx['client'].ML_IA_API_BASE}/sites/{site_id}/listing_prices",
        params=params, timeout=12,
    )
    if int(getattr(response, "status_code", 0) or 0) == 200:
        payload = response.json() or {}
        if isinstance(payload, list):
            candidates = [value for value in payload if isinstance(value, dict)]
            payload = next((value for value in candidates if str(value.get("listing_type_id") or "") == listing_type), candidates[0] if candidates else {})
        payload = payload if isinstance(payload, dict) else {}
        details = payload.get("sale_fee_details") if isinstance(payload.get("sale_fee_details"), dict) else {}
        entry["fees"] = {"sale_fee_amount": payload.get("sale_fee_amount"), "fixed_fee_amount": details.get("fixed_fee"),
            "percentage_fee": details.get("percentage_fee"), "meli_percentage_fee": details.get("meli_percentage_fee"),
            "financing_add_on_fee": details.get("financing_add_on_fee"), "source": "listing_prices",
            "available": payload.get("sale_fee_amount") is not None}
    if entry["fees"].get("sale_fee_amount") is None:
        entry["coverage_complete"] = False
        entry["warnings"].append("Tarifa oficial da variacao indisponivel.")
    entry["sources"].append({"provider": "mercado_livre", "resource": "listing_prices", "method": "GET", "store": store})
    return cfg


def _variation_shipping(ctx: dict[str, Any], client_id: str, store: str, cfg: dict[str, Any], item_id: str, item_shipping: dict[str, Any], entry: dict[str, Any], *, price: Any, listing_type: str, category: str) -> dict[str, Any]:
    try:
        value, cfg = ctx["pricing"]._ml_obter_frete_detalhado(
            client_id, store, cfg, item_id, item_shipping,
            contexto_frete={"item_price": price, "listing_type_id": listing_type, "category_id": category,
                "mode": entry["shipping"]["mode"], "logistic_type": entry["shipping"]["logistic_type"],
                "free_shipping": entry["shipping"]["free_shipping"]},
        )
        seller_cost = value.get("shipping_seller_cost")
        entry["shipping"].update(seller_cost=seller_cost, seller_cost_available=seller_cost is not None,
            source=str(value.get("shipping_cost_retry_source") or "shipping_options"))
        if seller_cost is None:
            entry["coverage_complete"] = False
            entry["warnings"].append("Frete do vendedor da variacao indisponivel.")
        entry["sources"].append({"provider": "mercado_livre", "resource": "shipping_options", "method": "GET", "store": store})
    except Exception as exc:
        entry["coverage_complete"] = False
        entry["warnings"].append(f"Frete da variacao indisponivel: {type(exc).__name__}.")
    return cfg


def _commercial_variations(ctx: dict[str, Any], client_id: str, store: str, item_id: str, listing: dict[str, Any], exact: dict[str, Any], commercial: dict[str, Any]) -> list[dict[str, Any]]:
    raw_variations: list[dict[str, Any]] = []
    try:
        cfg = ctx["runtime"].ml_config(client_id, store)
        raw_item, cfg, failure = ctx["listing_search"].resolve_owned_item(client_id, store, cfg, item_id, deadline=time.monotonic() + 45)
        if failure:
            raise ValueError(str(failure.get("code") or "listing_not_in_store"))
        raw_item = raw_item if isinstance(raw_item, dict) else {}
        raw_variations = [item for item in raw_item.get("variations") or [] if isinstance(item, dict)]
        item_shipping = raw_item.get("shipping") if isinstance(raw_item.get("shipping"), dict) else {}
        listing_type = str(raw_item.get("listing_type_id") or exact.get("listing_type_id") or listing.get("listing_type_id") or "")
        category = str(raw_item.get("category_id") or exact.get("category_id") or listing.get("category_id") or "")
        site_id = str(cfg.get("site_id") or "MLB").strip().upper()
        site_id = site_id if re.fullmatch(r"ML[A-Z]", site_id) else "MLB"
        entries: dict[str, dict[str, Any]] = {}
        for variation in raw_variations:
            variation_id, price = str(variation.get("id") or "").strip(), variation.get("price")
            entry = {"price": {"amount": price, "regular_amount": None, "currency_id": str(raw_item.get("currency_id") or "BRL"), "source": "item_variation", "available": price is not None},
                "fees": {"sale_fee_amount": None, "source": "listing_prices", "available": False},
                "shipping": {"mode": str(item_shipping.get("mode") or (commercial.get("shipping") or {}).get("mode") or ""),
                    "logistic_type": str(item_shipping.get("logistic_type") or (commercial.get("shipping") or {}).get("logistic_type") or ""),
                    "free_shipping": bool(item_shipping.get("free_shipping")), "seller_cost": None, "seller_cost_available": False},
                "coverage_complete": True, "warnings": [], "sources": []}
            cfg = _variation_fee(ctx, client_id, store, cfg, entry, price=price, listing_type=listing_type, category=category, site_id=site_id)
            cfg = _variation_shipping(ctx, client_id, store, cfg, item_id, item_shipping, entry, price=price, listing_type=listing_type, category=category)
            if variation_id:
                entries[variation_id] = entry
        if entries:
            commercial["variations"] = entries
    except Exception as exc:
        if exact.get("variations"):
            commercial["coverage_complete"] = False
            commercial.setdefault("warnings", []).append(f"Detalhamento comercial de variacoes indisponivel: {type(exc).__name__}.")
    return raw_variations


def _commercial(ctx: dict[str, Any], *, client_id: str, store: str, item_id: str, listing: dict[str, Any]) -> dict[str, Any]:
    commercial, exact, result = _commercial_base(ctx, client_id, store, item_id, listing)
    raw_variations = _commercial_variations(ctx, client_id, store, item_id, listing, exact, commercial)
    sources = [item for item in result.get("sources") or [] if isinstance(item, dict)]
    sources.append({"provider": "mercado_livre", "resource": "shipping_options", "method": "GET", "store": store})
    return {"details": {"commercial": commercial},
        "listing_variations": raw_variations or exact.get("variations") or [], "sources": sources}


def _shipping(ctx: dict[str, Any], *, client_id: str, store: str, shipment_id: str = "", pack_id: str = "", **_kwargs: Any) -> dict[str, Any]:
    target = str(shipment_id or pack_id or "").strip()
    if not target:
        raise ValueError("shipment_id ausente")
    cfg = ctx["runtime"].ml_config(client_id, store)
    response, _cfg = ctx["client"].request_get(client_id, store, cfg,
        f"{ctx['client'].ML_IA_API_BASE}/shipments/{target}/costs", timeout=15)
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise HTTPException(status_code=int(getattr(response, "status_code", 500) or 500), detail="Frete real indisponivel")
    result = response.json() or {}
    if isinstance(result, dict):
        result["_seller_id"] = str(cfg.get("user_id") or "")
    return result


def _order_billing(ctx: dict[str, Any], *, client_id: str, store: str, order_ids: list[str], **_kwargs: Any) -> dict[str, Any]:
    clean_ids = [re.sub(r"\D+", "", str(value or "")) for value in order_ids][:60]
    clean_ids = [value for value in clean_ids if value]
    if not clean_ids:
        return {"results": []}
    cfg = ctx["runtime"].ml_config(client_id, store)
    response, _cfg = ctx["client"].request_get(client_id, store, cfg,
        f"{ctx['client'].ML_IA_API_BASE}/billing/integration/group/ML/order/details",
        params={"order_ids": ",".join(clean_ids)}, timeout=20)
    if int(getattr(response, "status_code", 0) or 0) != 200:
        raise HTTPException(status_code=int(getattr(response, "status_code", 500) or 500), detail="Faturamento ML indisponivel")
    return response.json() or {}


def build_marketplace_fetchers(period_start: str, period_end: str) -> dict[str, Any]:
    from backend.services import mercadolivre_legacy_pricing
    from backend.services.marketplace_tools import client, listing_search, listings, orders, runtime

    ctx = {"client": client, "listing_search": listing_search, "listings": listings, "orders": orders, "runtime": runtime, "pricing": mercadolivre_legacy_pricing,
        "period_start": period_start, "period_end": period_end, "order_cache": {}}
    return {
        "listing_fetcher": lambda **kwargs: _listing(ctx, **kwargs),
        "orders_fetcher": lambda **kwargs: _orders(ctx, **kwargs),
        "shipping_fetcher": lambda **kwargs: _shipping(ctx, **kwargs),
        "billing_fetcher": lambda **kwargs: _commercial(ctx, **kwargs),
        "order_billing_fetcher": lambda **kwargs: _order_billing(ctx, **kwargs),
    }
