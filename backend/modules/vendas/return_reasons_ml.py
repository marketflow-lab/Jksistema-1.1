"""Resolve buyer return reasons only through explicit, owned ML identities."""

from __future__ import annotations

import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from backend.services.marketplace_tools import client, runtime


_BASE = "https://api.mercadolibre.com"
_TIMEOUT = 10
_DEADLINE = 30
_PAGE_SIZE = 30
_MAX_CLAIMS = 100
_MAX_PACK_ORDERS = 20


def _id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    return text if re.fullmatch(r"[1-9][0-9]{0,31}", text) else ""


def _site(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else ""


def _provided(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _sku(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _quantity(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        quantity = Decimal(str(value))
        return quantity if quantity.is_finite() and quantity >= 0 else None
    except InvalidOperation:
        return None


def _item_key(item: dict, site: str, *, returned=False) -> tuple[str, str] | None:
    item_id = item.get("item_id" if returned else "id")
    if not isinstance(item_id, str) or not re.fullmatch(re.escape(site) + r"[1-9][0-9]{0,31}", item_id):
        return None
    # Missing variation is not evidence that an item has no variation.
    if "variation_id" not in item:
        return None
    variation = item["variation_id"]
    if variation is not None and not _id(variation):
        return None
    return item_id, _id(variation)


def _sold_items(order: dict, site: str, sku: str, *, official_only=False, require_match=True) -> dict:
    """Use the SKU recorded on the sale, never a title or today's listing."""
    rows = order.get("order_items")
    if not isinstance(rows, list) or not rows:
        raise _Stop("order_items_missing", status="not_linked")
    sold = {}
    for row in rows:
        item = row.get("item") if isinstance(row, dict) else None
        key = _item_key(item, site) if isinstance(item, dict) else None
        quantity = _quantity(row.get("quantity")) if isinstance(row, dict) else None
        if key is None or quantity is None or quantity <= 0:
            raise _Stop("invalid_order_item_identity")
        # seller_custom_field is independent seller metadata. It cannot
        # contradict or replace the official SKU recorded on the order.
        # Legacy orders without seller_sku may use an exact textual match.
        official_sku = item.get("seller_sku")
        item_sku = _sku(official_sku) if _provided(official_sku) else (
            "" if official_only else _sku(item.get("seller_custom_field"))
        )
        previous = sold.get(key)
        if previous and previous["sku"] != item_sku:
            raise _Stop("conflicting_order_sku", status="ambiguous")
        sold[key] = {"sku": item_sku, "quantity": quantity + (previous["quantity"] if previous else 0)}
    if require_match and not any(item["sku"] == sku for item in sold.values()):
        raise _Stop("order_sku_not_linked", status="not_linked")
    return sold


class _Stop(Exception):
    def __init__(self, code: str, *, status="unavailable", retry=False, after=0):
        self.code, self.status, self.retry, self.after = code, status, retry, after


def _result() -> dict:
    return {
        "result_status": "unavailable", "reason_id": "", "reason_description": "",
        "reason_source": "mercado_livre", "order_id": "", "claim_id": "",
        "return_id": "", "seller_id": "", "site_id": "", "retryable": False,
        "retry_after_seconds": 0, "error_code": "",
    }


def _rate_delay(response) -> int:
    headers = getattr(response, "headers", {}) or {}
    value = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), "60")
    try:
        return min(3600, max(5, int(value)))
    except (TypeError, ValueError):
        return 60


class _Lookup:
    def __init__(self, tenant, store_id, store, cfg, seller, site):
        self.tenant, self.store_id, self.store = tenant, store_id, store
        self.cfg, self.seller, self.site = cfg, seller, site
        self.deadline = time.monotonic() + _DEADLINE

    def get(self, path, *, params=None):
        if time.monotonic() >= self.deadline:
            raise _Stop("timeout", retry=True, after=60)
        response, cfg = client.request_get(
            self.tenant, self.store, self.cfg, _BASE + path,
            params=params, timeout=_TIMEOUT, deadline=self.deadline,
        )
        self.cfg = cfg
        if (_id(cfg.get("user_id")) != self.seller
                or (cfg.get("_store_id_context") and cfg["_store_id_context"] != self.store_id)
                or (cfg.get("site_id") and self.site and _site(cfg["site_id"]) != self.site)):
            raise _Stop("store_identity_changed")
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise _Stop("reconnect_required", status="reconnect_required")
        if status == 429:
            raise _Stop("rate_limited", retry=True, after=_rate_delay(response))
        if status == 404:
            return None
        if status == 206:
            raise _Stop("partial_response", retry=True, after=60)
        if 500 <= status <= 599:
            raise _Stop("provider_unavailable", retry=True, after=60)
        if status != 200:
            raise _Stop("access_denied" if status == 403 else "provider_http_error")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise _Stop("invalid_response", retry=True, after=60) from None
        # None is reserved for an actual HTTP 404. A 200/null response must not
        # reinterpret the document number as a pack or an absent return.
        if payload is None:
            raise _Stop("invalid_response", retry=True, after=60)
        return payload

    def revalidate(self):
        """Discard evidence if the canonical store changed during the lookup."""
        row = runtime.find_store(self.tenant, self.store, self.store_id)
        if (not isinstance(row, dict) or str(row.get("store_id") or "").strip() != self.store_id
                or client._ia_ml_store_match_key(row.get("nome")) != client._ia_ml_store_match_key(self.store)):
            raise _Stop("store_identity_changed")
        cfg = runtime.ml_config(self.tenant, self.store, self.store_id)
        if (not isinstance(cfg, dict) or _id(cfg.get("user_id")) != self.seller
                or (cfg.get("_store_id_context") and cfg["_store_id_context"] != self.store_id)):
            raise _Stop("store_identity_changed")
        raw_site = cfg.get("site_id") or row.get("site_id")
        if raw_site and _site(raw_site) != self.site:
            raise _Stop("store_identity_changed")
        self.cfg = cfg
        if not raw_site:
            account = self.get("/users/me")
            if (not isinstance(account, dict) or _id(account.get("id")) != self.seller
                    or _site(account.get("site_id")) != self.site):
                raise _Stop("store_identity_changed")


def _store_context(client_id, store_id, loja, job) -> _Lookup:
    if not client_id or not store_id or not loja:
        raise _Stop("store_identity_required")
    for key, expected in (("client_id", client_id), ("store_id", store_id)):
        if job.get(key) is not None and str(job[key]).strip() != expected:
            raise _Stop("job_scope_mismatch")
    store, _failure = client.resolve_store(client_id, loja)
    if not store:
        raise _Stop("store_not_authorized")
    row = runtime.find_store(client_id, store, store_id)
    if (not isinstance(row, dict) or str(row.get("store_id") or "").strip() != store_id
            or client._ia_ml_store_match_key(row.get("nome")) != client._ia_ml_store_match_key(store)):
        raise _Stop("store_identity_mismatch")
    cfg = runtime.ml_config(client_id, store, store_id)
    if not isinstance(cfg, dict):
        raise _Stop("store_identity_unconfirmed")
    seller = _id(cfg.get("user_id"))
    raw_site = cfg.get("site_id") or row.get("site_id")
    site = _site(raw_site)
    if not seller or (raw_site and not site):
        raise _Stop("store_identity_unconfirmed")
    if cfg.get("_store_id_context") and cfg["_store_id_context"] != store_id:
        raise _Stop("store_identity_mismatch")
    context = _Lookup(client_id, store_id, store, cfg, seller, site)
    if not site:
        account = context.get("/users/me")
        if not isinstance(account, dict) or _id(account.get("id")) != seller or not _site(account.get("site_id")):
            raise _Stop("store_identity_unconfirmed")
        context.site = _site(account["site_id"])
    if ((_provided(job.get("seller_id")) and _id(job["seller_id"]) != seller)
            or (_provided(job.get("site_id")) and _site(job["site_id"]) != context.site)):
        raise _Stop("job_scope_mismatch")
    return context


def _order_seller(order, order_id, context, *, pack_id="", allow_foreign=False):
    seller = order.get("seller") if isinstance(order, dict) else None
    seller_id = _id(seller.get("id")) if isinstance(seller, dict) else ""
    if (not isinstance(order, dict) or _id(order.get("id")) != order_id or not seller_id
            or (not allow_foreign and seller_id != context.seller)
            or (_provided(order.get("seller_id")) and _id(order["seller_id"]) != seller_id)):
        raise _Stop("order_scope_mismatch")
    if pack_id and _id(order.get("pack_id")) != pack_id:
        raise _Stop("order_pack_mismatch")
    order_context = order.get("context") if isinstance(order.get("context"), dict) else {}
    if any(_provided(value) and _site(value) != context.site
           for value in (order.get("site_id"), order_context.get("site"))):
        raise _Stop("order_site_mismatch")
    return seller_id


def _pack_order(context, pack_id, sku):
    """Resolve only a unique owned order whose official sale SKU matches."""
    pack = context.get("/packs/" + pack_id)
    if pack is None:
        raise _Stop("order_not_found", status="not_linked")
    if not isinstance(pack, dict) or _id(pack.get("id")) != pack_id:
        raise _Stop("pack_scope_mismatch")
    pack_context = pack.get("context") if isinstance(pack.get("context"), dict) else {}
    if any(_provided(value) and _site(value) != context.site
           for value in (pack.get("site_id"), pack_context.get("site"))):
        raise _Stop("pack_site_mismatch")
    rows = pack.get("orders")
    if not isinstance(rows, list) or not rows:
        raise _Stop("incomplete_pack_orders", retry=True, after=60)
    if len(rows) > _MAX_PACK_ORDERS:
        raise _Stop("pack_order_limit", status="ambiguous")
    ids = [_id(row.get("id")) if isinstance(row, dict) else "" for row in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise _Stop("invalid_pack_orders", status="ambiguous")
    matches = []
    for order_id in ids:
        try:
            order = context.get("/orders/" + order_id)
        except _Stop as error:
            if error.code == "access_denied":
                # A forbidden order cannot be assumed to belong to another seller.
                raise _Stop("pack_order_inaccessible", status="ambiguous") from None
            raise
        if order is None:
            raise _Stop("incomplete_pack_orders", retry=True, after=60)
        seller = _order_seller(order, order_id, context, pack_id=pack_id, allow_foreign=True)
        if seller != context.seller:
            continue
        sold = _sold_items(order, context.site, sku, official_only=True, require_match=False)
        if any(not item["sku"] for item in sold.values()):
            raise _Stop("pack_order_sku_unconfirmed", status="ambiguous")
        if any(item["sku"] == sku for item in sold.values()):
            matches.append((order_id, sold))
    if not matches:
        raise _Stop("pack_sku_not_linked", status="not_linked")
    if len(matches) != 1:
        raise _Stop("multiple_pack_orders", status="ambiguous")
    return matches[0]


def _owned_claim(claim, context, order_id, explicit_id="") -> dict:
    if not isinstance(claim, dict) or not _id(claim.get("id")):
        raise _Stop("invalid_claim_response", retry=True, after=60)
    players = claim.get("players")
    resource = claim.get("resource")
    owner_type = {"order": "seller", "payment": "collector", "shipment": "sender"}.get(resource)
    owned = any(
        isinstance(player, dict) and _id(player.get("user_id")) == context.seller
        and player.get("role") == "respondent" and player.get("type") == owner_type
        for player in (players if isinstance(players, list) else [])
    )
    if (not owner_type or not owned or not _id(claim.get("resource_id"))
            or (resource == "order" and _id(claim.get("resource_id")) != order_id)
            or _site(claim.get("site_id")) != context.site
            or (claim.get("order_id") and _id(claim["order_id"]) != order_id)
            or (explicit_id and _id(claim["id"]) != explicit_id)):
        raise _Stop("claim_scope_mismatch")
    return claim


def _claims(context, order_id, claim_id):
    if claim_id:
        claim = context.get("/post-purchase/v1/claims/" + claim_id)
        return [] if claim is None else [_owned_claim(claim, context, order_id, claim_id)]
    claims = {}
    offset = 0
    while offset < _MAX_CLAIMS:
        limit = min(_PAGE_SIZE, _MAX_CLAIMS - offset)
        payload = context.get("/post-purchase/v1/claims/search", params={
            "order_id": order_id,
            "players.user_id": context.seller, "players.role": "respondent",
            "limit": limit, "offset": offset, "sort": "last_updated:desc",
        })
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise _Stop("invalid_claim_search", retry=True, after=60)
        page = payload["data"]
        if len(page) > limit:
            raise _Stop("invalid_claim_search", retry=True, after=60)
        for raw in page:
            claim = _owned_claim(raw, context, order_id)
            ident = _id(claim["id"])
            if ident in claims and any(claims[ident].get(field) != claim.get(field)
                                       for field in ("reason_id", "resource", "resource_id")):
                raise _Stop("conflicting_claim_evidence", status="ambiguous")
            if ident in claims:
                raise _Stop("incomplete_claim_search", retry=True, after=60)
            claims.setdefault(ident, claim)
        paging = payload.get("paging") or {}
        total = paging.get("total") if isinstance(paging, dict) else None
        if total is not None and (isinstance(total, bool) or not str(total).isdigit()):
            raise _Stop("invalid_claim_search", retry=True, after=60)
        offset += len(page)
        if total is not None:
            if offset > int(total):
                raise _Stop("invalid_claim_search", retry=True, after=60)
            if offset >= int(total):
                return list(claims.values())
            if not page:
                raise _Stop("incomplete_claim_search", retry=True, after=60)
        elif len(page) < limit:
            return list(claims.values())
    raise _Stop("incomplete_claim_search", retry=True, after=60)


def _returned_sku(row, order_id, sold, sku, site):
    """A return must identify the sold item/variation and a returned quantity."""
    orders = row.get("orders")
    if not isinstance(orders, list) or not orders:
        raise _Stop("return_items_missing", status="not_linked")
    matched = False
    quantities = {}
    for item in orders:
        if not isinstance(item, dict) or not _id(item.get("order_id")):
            raise _Stop("invalid_return_item_identity")
        if _id(item["order_id"]) != order_id:
            continue
        key = _item_key(item, site, returned=True)
        if key is None:
            raise _Stop("return_item_identity_missing", status="not_linked")
        if key not in sold:
            raise _Stop("return_item_scope_mismatch")
        quantity = _quantity(item.get("return_quantity"))
        total = _quantity(item.get("total_quantity"))
        if (quantity is None or ("total_quantity" in item and (total is None or quantity > total))
                or quantity > sold[key]["quantity"]):
            raise _Stop("invalid_return_quantity")
        kind = item.get("context_type")
        if kind not in {"total", "partial", "incomplete"}:
            raise _Stop("invalid_return_item_context")
        if kind == "incomplete" or quantity == 0:
            continue
        quantities[key] = quantities.get(key, Decimal(0)) + quantity
        if quantities[key] > sold[key]["quantity"]:
            raise _Stop("invalid_return_quantity")
        if not sold[key]["sku"]:
            raise _Stop("return_sku_not_linked", status="not_linked")
        matched = matched or sold[key]["sku"] == sku
    return matched


def _returns(context, claim, order_id):
    claim_id = _id(claim["id"])
    payload = context.get("/post-purchase/v2/claims/" + claim_id + "/returns")
    if payload is None:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    result = []
    for row in rows:
        if not isinstance(row, dict) or not _id(row.get("id")):
            raise _Stop("invalid_return_response", retry=True, after=60)
        resources = row.get("resources") or []
        linked_claim = _id(row.get("claim_id")) == claim_id or any(
            isinstance(resource, dict) and resource.get("resource") == "claim"
            and _id(resource.get("resource_id")) == claim_id
            for resource in (resources if isinstance(resources, list) else [])
        )
        orders = row.get("orders")
        order_ids = {_id(order.get("order_id"))
                     for order in (orders if isinstance(orders, list) else [])
                     if isinstance(order, dict)}
        linked_order = order_id in order_ids
        if not orders and row.get("resource_type") == "order":
            linked_order = _id(row.get("resource_id")) == order_id
        conflicting_resource = (orders and row.get("resource_type") == "order"
                                and row.get("resource_id") and _id(row["resource_id"]) not in order_ids)
        if (not linked_claim or not linked_order or conflicting_resource
                or (row.get("site_id") and _site(row["site_id"]) != context.site)
                or (row.get("seller_id") and _id(row["seller_id"]) != context.seller)):
            raise _Stop("return_scope_mismatch")
        result.append(row)
    return result


def resolve_reason(client_id: str, store_id: str, loja: str, job: dict) -> dict:
    """GET only; return bounded evidence and safe machine errors, never payloads."""
    result = _result()
    try:
        if not isinstance(job, dict):
            raise _Stop("invalid_job", status="not_linked")
        order_id = _id(job.get("order_id"))
        if not order_id:
            raise _Stop("explicit_order_required", status="not_linked")
        sku = _sku(job.get("sku"))
        if not sku:
            raise _Stop("explicit_sku_required", status="not_linked")
        claim_id, return_id = _id(job.get("claim_id")), _id(job.get("return_id"))
        if ((_provided(job.get("claim_id")) and not claim_id)
                or (_provided(job.get("return_id")) and not return_id)):
            raise _Stop("invalid_link_identifier", status="not_linked")
        context = _store_context(str(client_id or "").strip(), str(store_id or "").strip(), str(loja or "").strip(), job)
        result.update(seller_id=context.seller, site_id=context.site)
        order = context.get("/orders/" + order_id)
        if order is None:
            order_id, sold = _pack_order(context, order_id, sku)
        else:
            _order_seller(order, order_id, context)
            result["order_id"] = order_id
            sold = _sold_items(order, context.site, sku)
        result["order_id"] = order_id
        matches = {}
        linked_return = False
        for claim in _claims(context, order_id, claim_id):
            for returned in _returns(context, claim, order_id):
                if not return_id or _id(returned["id"]) == return_id:
                    linked_return = True
                    if not _returned_sku(returned, order_id, sold, sku, context.site):
                        continue
                    key = (_id(claim["id"]), _id(returned["id"]))
                    if key in matches and matches[key] != (claim, returned):
                        raise _Stop("conflicting_return_evidence", status="ambiguous")
                    matches[key] = (claim, returned)
        if not matches:
            if linked_return:
                raise _Stop("sku_not_returned", status="not_linked")
            raise _Stop("return_not_linked" if claim_id or return_id else "return_not_found",
                        status="not_linked" if claim_id or return_id else "not_informed")
        if len(matches) != 1:
            raise _Stop("multiple_linked_returns", status="ambiguous")
        claim, returned = next(iter(matches.values()))
        result.update(claim_id=_id(claim["id"]), return_id=_id(returned["id"]))
        reason_id = claim.get("reason_id")
        if not reason_id:
            raise _Stop("reason_not_informed", status="not_informed")
        if not isinstance(reason_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", reason_id):
            raise _Stop("invalid_reason_identifier")
        reason = context.get("/post-purchase/v1/claims/reasons/" + reason_id)
        if reason is None:
            raise _Stop("reason_not_found", status="not_informed")
        if not isinstance(reason, dict) or str(reason.get("id") or "") != reason_id:
            raise _Stop("reason_identity_mismatch")
        description = next((value.strip()[:2000] for key in ("detail", "name")
                            if isinstance(value := reason.get(key), str) and value.strip()), "")
        if not description:
            raise _Stop("reason_not_informed", status="not_informed")
        context.revalidate()
        result.update(result_status="identified", reason_id=reason_id, reason_description=description)
    except _Stop as error:
        result.update(result_status=error.status, retryable=error.retry,
                      retry_after_seconds=error.after, error_code=error.code)
    except requests.exceptions.Timeout:
        result.update(retryable=True, retry_after_seconds=60, error_code="timeout")
    except requests.exceptions.RequestException:
        result.update(retryable=True, retry_after_seconds=60, error_code="network_error")
    except Exception as error:
        status = getattr(error, "status_code", None)
        if status == 401:
            result.update(result_status="reconnect_required", error_code="reconnect_required")
        elif status in {403, 404, 409}:
            result.update(error_code="store_not_authorized" if status in {403, 404} else "store_identity_mismatch")
        else:
            result.update(retryable=True, retry_after_seconds=60, error_code="provider_unavailable")
    return result


__all__ = ["resolve_reason"]
