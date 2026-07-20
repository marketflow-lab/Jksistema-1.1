"""Coleta comercial read-only do Mercado Livre para relatorios gerenciais.

O modulo nao conhece credenciais nem executa HTTP diretamente. Integradores devem
fornecer callbacks somente de leitura com as seguintes assinaturas por palavra-chave:

* ``listing_fetcher(client_id, store, relevant_skus)``;
* ``orders_fetcher(client_id, store, date_from, date_to, offset, limit)``;
* ``shipping_fetcher(client_id, store, shipment_id)``;
* ``commercial_fetcher(client_id, store, item_id, listing)``;
* ``order_billing_fetcher(client_id, store, order_ids)`` (ate 60 IDs).

``billing_fetcher`` continua aceito como alias legado de ``commercial_fetcher``.

Essa fronteira deixa retry, paginacao, normalizacao, cache e persistencia
testaveis sem acessar uma conta real do Mercado Livre.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional
from zoneinfo import ZoneInfo


CACHE_TTL_SECONDS = 15 * 60
MAX_CONCURRENCY = 4
MAX_ORDER_BATCH = 60
MAX_ORDER_PAGES_PER_DAY = 1_000
MAX_RETRY_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
SCHEMA_VERSION = "jk.marketplace.margin.v1"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

_TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MLB_RE = re.compile(r"^MLB\d+$", re.IGNORECASE)
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

_DB_LOCKS: dict[str, threading.RLock] = {}
_DB_LOCKS_GUARD = threading.Lock()
_FLIGHT_LOCKS: dict[str, threading.Lock] = {}
_FLIGHT_LOCKS_GUARD = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: Optional[datetime] = None) -> str:
    current = value or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sku(value: Any) -> Optional[str]:
    normalized = _text(value).upper()
    return normalized or None


def _store(value: Any) -> str:
    normalized = _text(value)
    if not normalized:
        raise ValueError("store deve identificar uma loja Mercado Livre exata")
    return normalized


def _client(value: Any) -> str:
    normalized = _text(value)
    if not _TENANT_RE.fullmatch(normalized):
        raise ValueError("client_id invalido para persistencia tenant-scoped")
    return normalized


def _mlb(value: Any) -> Optional[str]:
    normalized = _text(value).upper()
    return normalized if _MLB_RE.fullmatch(normalized) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


def _attribute_value(payload: dict[str, Any], attribute_id: str) -> Any:
    wanted = attribute_id.upper()
    for attribute in _as_list(payload.get("attributes")) + _as_list(payload.get("attribute_combinations")):
        if not isinstance(attribute, dict):
            continue
        if _text(attribute.get("id")).upper() == wanted:
            return _first(attribute, ("value_name", "value_id", "value"))
    return None


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.astimezone(SAO_PAULO).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    raw = _text(value)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError(f"data invalida: {raw!r}") from exc


def _local_order_day(value: Any) -> Optional[str]:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SAO_PAULO)
    else:
        parsed = parsed.astimezone(SAO_PAULO)
    return parsed.date().isoformat()


def _days(start: Any, end: Any) -> Iterator[date]:
    current = _parse_date(start)
    last = _parse_date(end)
    if last < current:
        raise ValueError("period_end deve ser igual ou posterior a period_start")
    while current <= last:
        yield current
        current += timedelta(days=1)


def _tenant_directory(info_base: Any, client_id: Any) -> Path:
    tenant = _client(client_id)
    base = Path(str(info_base or "info")).expanduser().resolve()
    target = (base / tenant / "codex_assistant").resolve()
    if base != target and base not in target.parents:
        raise ValueError("caminho tenant fora de info_base")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _lock_for_db(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _DB_LOCKS_GUARD:
        return _DB_LOCKS.setdefault(key, threading.RLock())


def _lock_for_flight(key: str) -> threading.Lock:
    with _FLIGHT_LOCKS_GUARD:
        return _FLIGHT_LOCKS.setdefault(key, threading.Lock())


def retry_after_seconds(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """Converte Retry-After numerico ou HTTP-date em segundos limitados."""

    raw = _text(value)
    if not raw:
        return None
    numeric = _number(raw)
    if numeric is not None:
        return max(0.0, min(MAX_RETRY_AFTER_SECONDS, numeric))
    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    delay = (target.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds()
    return max(0.0, min(MAX_RETRY_AFTER_SECONDS, delay))


def _exception_http_details(exc: BaseException) -> tuple[Optional[int], Optional[float]]:
    response = getattr(exc, "response", None)
    status = _integer(getattr(response, "status_code", None))
    if status is None:
        status = _integer(getattr(exc, "status_code", None))
    headers = getattr(response, "headers", None)
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None and isinstance(headers, dict):
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
    return status, retry_after_seconds(retry_after)


def call_with_retry(
    callback: Callable[..., Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: Optional[int] = None,
    **kwargs: Any,
) -> Any:
    """Executa callback read-only com retry limitado e suporte a Retry-After."""

    maximum = max(1, int(MAX_RETRY_ATTEMPTS if attempts is None else attempts))
    for attempt in range(maximum):
        try:
            result = callback(**kwargs)
            status = _integer(getattr(result, "status_code", None))
            if status in _RETRYABLE_STATUS:
                error = RuntimeError(f"resposta HTTP temporaria: {status}")
                setattr(error, "status_code", status)
                headers = getattr(result, "headers", None)
                if isinstance(headers, dict):
                    setattr(error, "retry_after", headers.get("Retry-After") or headers.get("retry-after"))
                raise error
            return result
        except Exception as exc:
            status, retry_after = _exception_http_details(exc)
            retryable = isinstance(exc, (TimeoutError, ConnectionError)) or status in _RETRYABLE_STATUS
            if not retryable or attempt + 1 >= maximum:
                raise
            delay = retry_after if retry_after is not None else min(2.0, 0.25 * (2**attempt))
            sleep(max(0.0, delay))
    raise RuntimeError("retry esgotado")


def _commercial_from_listing(listing: dict[str, Any]) -> dict[str, Any]:
    details = _mapping(listing.get("details"))
    commercial = _mapping(details.get("commercial"))
    return commercial


def _variation_sku(variation: dict[str, Any], listing: dict[str, Any]) -> tuple[Optional[str], str]:
    candidates = (
        (variation.get("seller_sku"), "variation.seller_sku"),
        (variation.get("seller_custom_field"), "variation.seller_custom_field"),
        (_attribute_value(variation, "SELLER_SKU"), "variation.attribute.SELLER_SKU"),
        (listing.get("seller_sku"), "listing.seller_sku"),
        (listing.get("seller_custom_field"), "listing.seller_custom_field"),
        (_attribute_value(listing, "SELLER_SKU"), "listing.attribute.SELLER_SKU"),
    )
    for value, source in candidates:
        normalized = _sku(value)
        if normalized:
            return normalized, source
    return None, "missing"


def normalize_commercial_listing(listing: dict, client_id: str, store: str) -> list[dict]:
    """Achata ``details.commercial`` e preserva uma linha por variacao."""

    tenant = _client(client_id)
    exact_store = _store(store)
    payload = _mapping(listing)
    item_id = _mlb(payload.get("id") or payload.get("item_id"))
    if not item_id:
        return []

    commercial = _commercial_from_listing(payload)
    base_price = _mapping(commercial.get("price"))
    base_fees = _mapping(commercial.get("fees"))
    base_commercial_shipping = _mapping(commercial.get("shipping"))
    commercial_variations = _mapping(commercial.get("variations"))
    item_shipping = _mapping(payload.get("shipping"))
    warnings = [str(value) for value in _as_list(commercial.get("warnings")) if _text(value)]
    variations = [value for value in _as_list(payload.get("variations")) if isinstance(value, dict)] or [{}]
    rows: list[dict[str, Any]] = []

    for variation in variations:
        sku, sku_source = _variation_sku(variation, payload)
        variation_id = _text(variation.get("id") or variation.get("variation_id")) or None
        variation_commercial = _mapping(commercial_variations.get(str(variation_id or "")))
        price = _mapping(variation_commercial.get("price")) or base_price
        fees = _mapping(variation_commercial.get("fees")) or base_fees
        commercial_shipping = _mapping(variation_commercial.get("shipping")) or base_commercial_shipping
        variation_warnings = warnings + [
            str(value) for value in _as_list(variation_commercial.get("warnings")) if _text(value)
        ]
        row = {
            "schema": SCHEMA_VERSION,
            "client_id": tenant,
            "store": exact_store,
            "item_id": item_id,
            "variation_id": variation_id,
            "sku": sku,
            "sku_source": sku_source,
            "title": _text(payload.get("title")),
            "listing_status": _text(payload.get("status")) or None,
            "listing_type_id": _text(payload.get("listing_type_id")) or None,
            "category_id": _text(payload.get("category_id")) or None,
            "catalog_listing": payload.get("catalog_listing") if isinstance(payload.get("catalog_listing"), bool) else None,
            "price": _number(_first(price, ("amount", "price")))
            if price
            else _number(_first(variation, ("price",)) or payload.get("price")),
            "regular_price": _number(_first(price, ("regular_amount", "original_price")))
            if price
            else _number(payload.get("original_price")),
            "currency_id": _text(price.get("currency_id") or payload.get("currency_id")) or None,
            "promotion_id": _text(price.get("promotion_id")) or None,
            "promotion_type": _text(price.get("promotion_type")) or None,
            "sale_fee_amount": _number(fees.get("sale_fee_amount")),
            "listing_fee_amount": _number(fees.get("listing_fee_amount")),
            "fixed_fee_amount": _number(fees.get("fixed_fee_amount")),
            "percentage_fee": _number(fees.get("percentage_fee")),
            "meli_percentage_fee": _number(fees.get("meli_percentage_fee")),
            "financing_add_on_fee": _number(fees.get("financing_add_on_fee")),
            "shipping_mode": _text(commercial_shipping.get("mode") or item_shipping.get("mode")) or None,
            "logistic_type": _text(commercial_shipping.get("logistic_type") or item_shipping.get("logistic_type")) or None,
            "free_shipping": (
                commercial_shipping.get("free_shipping")
                if isinstance(commercial_shipping.get("free_shipping"), bool)
                else item_shipping.get("free_shipping") if isinstance(item_shipping.get("free_shipping"), bool) else None
            ),
            "shipping_seller_cost": _number(commercial_shipping.get("seller_cost")),
            "shipping_seller_cost_available": (
                commercial_shipping.get("seller_cost_available")
                if isinstance(commercial_shipping.get("seller_cost_available"), bool)
                else None
            ),
            "available_quantity": _number(
                variation.get("available_quantity")
                if variation.get("available_quantity") is not None
                else payload.get("available_quantity")
            ),
            "sold_quantity": _number(
                variation.get("sold_quantity") if variation.get("sold_quantity") is not None else payload.get("sold_quantity")
            ),
            "commercial_coverage_complete": (
                variation_commercial.get("coverage_complete")
                if isinstance(variation_commercial.get("coverage_complete"), bool)
                else commercial.get("coverage_complete") if isinstance(commercial.get("coverage_complete"), bool) else None
            ),
            "commercial_scope": "current_listing",
            "price_scope": "current_listing",
            "fee_scope": "current_listing",
            "warnings": list(dict.fromkeys(variation_warnings)),
            "sources": deepcopy(_as_list(payload.get("sources"))) + deepcopy(_as_list(variation_commercial.get("sources"))),
        }
        rows.append(row)
    return rows


def _merge_billing(listing: dict[str, Any], billing: Any) -> dict[str, Any]:
    result = deepcopy(listing)
    billing_mapping = _mapping(billing)
    if isinstance(billing_mapping.get("listing_variations"), list):
        result["variations"] = deepcopy(billing_mapping["listing_variations"])
    merged_sources = deepcopy(_as_list(result.get("sources")))
    for source in _as_list(billing_mapping.get("sources")):
        if source not in merged_sources:
            merged_sources.append(deepcopy(source))
    if merged_sources:
        result["sources"] = merged_sources
    details = result.setdefault("details", {})
    if not isinstance(details, dict):
        details = {}
        result["details"] = details
    current = deepcopy(_mapping(details.get("commercial")))
    candidate = billing_mapping
    if isinstance(candidate.get("details"), dict):
        candidate = _mapping(candidate["details"].get("commercial"))
    elif isinstance(candidate.get("commercial"), dict):
        candidate = _mapping(candidate.get("commercial"))
    for key, value in candidate.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key] = {**current[key], **value}
        else:
            current[key] = value
    details["commercial"] = current
    return result


def _extract_rows(payload: Any, names: Iterable[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [value for value in payload if isinstance(value, dict)]
    if not isinstance(payload, dict):
        return []
    for name in names:
        values = payload.get(name)
        if isinstance(values, list):
            return [value for value in values if isinstance(value, dict)]
    return []


def _order_item_sku(item: dict[str, Any], nested: dict[str, Any]) -> Optional[str]:
    for value in (
        item.get("sku"),
        item.get("seller_sku"),
        nested.get("seller_sku"),
        nested.get("seller_custom_field"),
        _attribute_value(item, "SELLER_SKU"),
        _attribute_value(nested, "SELLER_SKU"),
    ):
        normalized = _sku(value)
        if normalized:
            return normalized
    return None


def normalize_order_rows(order: dict, client_id: str, store: str) -> list[dict]:
    """Normaliza linhas de pedido sem ratear totais ou frete entre itens."""

    tenant = _client(client_id)
    exact_store = _store(store)
    payload = _mapping(order)
    order_id = _text(payload.get("order_id") or payload.get("id"))
    if not order_id:
        return []
    date_created = payload.get("date_created") or payload.get("created_at")
    order_day = _local_order_day(date_created)
    shipping = _mapping(payload.get("shipping"))
    pack_id = _text(payload.get("pack_id")) or None
    shipment_id = _text(shipping.get("id") or payload.get("shipping_id")) or None
    items = _extract_rows(payload, ("items", "order_items"))
    rows: list[dict[str, Any]] = []
    for line_number, item in enumerate(items):
        nested = _mapping(item.get("item"))
        item_id = _mlb(item.get("item_id") or nested.get("id") or item.get("id"))
        if not item_id:
            continue
        variation = _mapping(item.get("variation") or nested.get("variation"))
        variation_id = _text(
            item.get("variation_id") or nested.get("variation_id") or variation.get("id")
        ) or None
        quantity = _number(item.get("quantity"))
        unit_price = _number(_first(item, ("unit_price", "full_unit_price", "sale_price")))
        gross_amount = _number(_first(item, ("gross_amount", "total_amount")))
        if gross_amount is None and quantity is not None and unit_price is not None:
            gross_amount = round(quantity * unit_price, 2)
        rows.append(
            {
                "schema": SCHEMA_VERSION,
                "client_id": tenant,
                "store": exact_store,
                "order_id": order_id,
                "line_number": line_number,
                "order_date": order_day,
                "date_created": _text(date_created) or None,
                "order_status": _text(payload.get("status")) or None,
                "pack_id": pack_id,
                "shipment_id": shipment_id,
                "item_id": item_id,
                "variation_id": variation_id,
                "sku": _order_item_sku(item, nested),
                "title": _text(item.get("title") or nested.get("title")) or None,
                "quantity": quantity,
                "unit_price": unit_price,
                "sold_unit_price": unit_price,
                "gross_amount": gross_amount,
                "paid_amount": _number(item.get("paid_amount")),
                "refund_amount": _number(item.get("refund_amount")),
                "currency_id": _text(payload.get("currency_id") or item.get("currency_id")) or None,
                "shipping_seller_cost": None,
                "seller_shipping_cost": None,
                "pack_shipping_total": None,
                "pack_shipping_reference": False,
                "pack_item_count": None,
                "shipping_allocation_status": "not_collected",
                "sale_fee_amount": None,
                "sale_fee_total": None,
                "unit_cost": None,
                "tax_pct": None,
                "reconciliation_state": "partial",
                "billing_reconciliation_state": "not_requested",
                "price_scope": "order_historical",
                "fee_scope": None,
                "sources": [],
                "warnings": [],
            }
        )
    return rows


def _shipping_cost(payload: Any) -> Optional[float]:
    data = _mapping(payload)
    shipping = _mapping(data.get("shipping"))
    costs = _mapping(data.get("costs"))
    for value in (
        data.get("seller_cost"),
        data.get("shipping_seller_cost"),
        data.get("shipping_cost"),
        data.get("cost"),
        shipping.get("seller_cost"),
        shipping.get("sender_cost"),
        shipping.get("cost"),
        costs.get("seller"),
    ):
        number = _number(value)
        if number is not None:
            return number
    senders = [value for value in _as_list(data.get("senders")) if isinstance(value, dict)]
    expected_seller = _text(data.get("_seller_id") or data.get("seller_id"))
    if expected_seller:
        exact = [value for value in senders if _text(value.get("user_id")) == expected_seller]
        if len(exact) == 1:
            return _number(exact[0].get("cost"))
    elif len(senders) == 1:
        return _number(senders[0].get("cost"))
    return None


def _declared_pack_lines(payload: Any) -> Optional[int]:
    data = _mapping(payload)
    count = _integer(data.get("item_count") or data.get("items_count"))
    if count is not None:
        return count
    orders = _extract_rows(data, ("orders",))
    if orders:
        return sum(len(_extract_rows(order, ("items", "order_items"))) for order in orders)
    items = _extract_rows(data, ("items", "order_items"))
    return len(items) if items else None


def apply_pack_shipping(
    rows: list[dict],
    shipment_payloads: dict[str, Any],
    failed_shipments: Optional[set[str]] = None,
) -> list[dict]:
    """Aplica frete integral apenas a packs com uma unica linha de item."""

    output = deepcopy(rows)
    pack_groups: dict[str, list[dict[str, Any]]] = {}
    shipment_groups: dict[str, list[dict[str, Any]]] = {}
    for row in output:
        pack_id = _text(row.get("pack_id"))
        if pack_id:
            pack_groups.setdefault(pack_id, []).append(row)
        shipment_id = _text(row.get("shipment_id"))
        if shipment_id:
            shipment_groups.setdefault(shipment_id, []).append(row)
    for group in pack_groups.values():
        for row in group:
            row["pack_item_count"] = len(group)
    for row in output:
        if row.get("pack_item_count") is None:
            same_order = [candidate for candidate in output if candidate.get("order_id") == row.get("order_id")]
            row["pack_item_count"] = max(1, len(same_order))

    failed = failed_shipments or set()
    for shipment_id, group in shipment_groups.items():
        pack_id = _text(group[0].get("pack_id"))
        pack_group = pack_groups.get(pack_id) if pack_id else group
        pack_line_count = len(pack_group or group)
        if shipment_id in failed:
            for row in group:
                row["shipping_allocation_status"] = "unavailable"
                row.setdefault("warnings", []).append("Frete do shipment indisponivel nesta coleta.")
            continue
        payload = shipment_payloads.get(shipment_id)
        cost = _shipping_cost(payload)
        declared = _declared_pack_lines(payload)
        line_count = max(pack_line_count, declared or 0)
        for row in pack_group or group:
            row["pack_item_count"] = line_count
        if cost is None:
            for row in group:
                row["shipping_allocation_status"] = "unavailable"
                row.setdefault("warnings", []).append("Custo do frete do shipment nao foi informado.")
            continue
        if line_count == 1:
            group[0]["shipping_seller_cost"] = cost
            group[0]["seller_shipping_cost"] = cost
            group[0]["pack_shipping_total"] = cost
            group[0]["pack_shipping_reference"] = True
            group[0]["shipping_allocation_status"] = "whole_pack_single_item"
            continue
        for index, row in enumerate(group):
            row["shipping_seller_cost"] = None
            row["seller_shipping_cost"] = cost if index == 0 else None
            row["pack_shipping_total"] = cost if index == 0 else None
            row["pack_shipping_reference"] = index == 0
            row["shipping_allocation_status"] = "pack_multi_item_not_allocated"
            row.setdefault("warnings", []).append("Frete total do pack multi-item nao foi rateado.")
    return output


def _chunks(values: list[str], size: int) -> Iterator[list[str]]:
    width = max(1, min(MAX_ORDER_BATCH, int(size)))
    for index in range(0, len(values), width):
        yield values[index : index + width]


def _order_billing_records(payload: Any) -> dict[str, dict[str, Any]]:
    data = _mapping(payload)
    rows = _extract_rows(data, ("orders", "results", "billings", "data"))
    if not rows and _text(data.get("order_id") or data.get("id")):
        rows = [data]
    if not rows and data:
        for key, value in data.items():
            if isinstance(value, dict) and _text(key).isdigit():
                rows.append({"order_id": _text(key), **value})
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        order_id = _text(row.get("order_id") or row.get("id"))
        if order_id:
            records[order_id] = row
    return records


def _fee_values(payload: dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    fee_unit = _number(_first(payload, ("fee_unit", "sale_fee_amount", "marketplace_fee_unit")))
    fee_total = _number(_first(payload, ("sale_fee_total", "fee_total", "marketplace_fee_total")))
    sale_fee = _mapping(payload.get("sale_fee"))
    if fee_total is None:
        fee_total = _number(sale_fee.get("net"))
    return fee_unit, fee_total


def _billing_line_matches(row: dict[str, Any], candidate: dict[str, Any]) -> bool:
    nested = _mapping(candidate.get("item"))
    candidate_item = _mlb(candidate.get("item_id") or nested.get("id") or candidate.get("mlb"))
    if candidate_item and candidate_item != row.get("item_id"):
        return False
    candidate_variation = _text(
        candidate.get("variation_id") or nested.get("variation_id") or _mapping(candidate.get("variation")).get("id")
    )
    if candidate_variation and candidate_variation != _text(row.get("variation_id")):
        return False
    candidate_sku = _order_item_sku(candidate, nested)
    if candidate_sku and candidate_sku != _sku(row.get("sku")):
        return False
    return bool(candidate_item or candidate_variation or candidate_sku)


def apply_order_billing(
    rows: list[dict],
    billing_records: dict[str, dict[str, Any]],
    *,
    store: str,
) -> tuple[list[dict], set[str]]:
    """Aplica tarifa real somente quando ha correspondencia exata de linha."""

    output = deepcopy(rows)
    by_order: dict[str, list[dict[str, Any]]] = {}
    for row in output:
        by_order.setdefault(_text(row.get("order_id")), []).append(row)
    reconciled_orders: set[str] = set()
    for order_id, order_rows in by_order.items():
        billing = billing_records.get(order_id)
        if not billing:
            for row in order_rows:
                row["billing_reconciliation_state"] = "partial"
                row.setdefault("warnings", []).append("Tarifa real do pedido nao foi localizada.")
            continue
        candidates = _extract_rows(billing, ("items", "order_items", "charges", "fees"))
        order_fee_unit, order_fee_total = _fee_values(billing)
        order_reconciled = True
        for index, row in enumerate(order_rows):
            matches = [candidate for candidate in candidates if _billing_line_matches(row, candidate)]
            candidate = matches[0] if len(matches) == 1 else billing if len(order_rows) == 1 and not candidates else None
            fee_unit: Optional[float] = None
            fee_total: Optional[float] = None
            if candidate is not None:
                fee_unit, fee_total = _fee_values(candidate)
            elif len(order_rows) == 1 and (order_fee_unit is not None or order_fee_total is not None):
                fee_unit, fee_total = order_fee_unit, order_fee_total
            if fee_unit is None and fee_total is None:
                order_reconciled = False
                row["billing_reconciliation_state"] = "partial"
                row.setdefault("warnings", []).append("Tarifa real sem correspondencia exata para a linha do pedido.")
            else:
                row["sale_fee_amount"] = fee_unit
                row["sale_fee_total"] = fee_total
                row["billing_reconciliation_state"] = "reconciled"
                row["fee_scope"] = "order_historical"
                row.setdefault("sources", []).append(
                    {
                        "provider": "mercado_livre",
                        "resource": "order_billing",
                        "method": "GET",
                        "store": store,
                        "order_id": order_id,
                    }
                )
            pack_count = _integer(row.get("pack_item_count")) or 1
            shipping_known = row.get("seller_shipping_cost") is not None
            row["reconciliation_state"] = (
                "reconciled"
                if row.get("billing_reconciliation_state") == "reconciled" and shipping_known and pack_count <= 1
                else "partial"
            )
            if index == 0 and len(order_rows) > 1 and not candidates and order_fee_total is not None:
                row["order_sale_fee_total_unallocated"] = order_fee_total
                row.setdefault("warnings", []).append("Tarifa total multi-item nao foi rateada.")
        if order_reconciled:
            reconciled_orders.add(order_id)
    return output, reconciled_orders


@dataclass(frozen=True)
class _CacheEntry:
    created_ts: float
    payload: dict[str, Any]


class MarketplaceMarginLedger:
    """Ledger append-only e cache SQLite isolados por ``client_id``."""

    def __init__(self, info_base: str, client_id: str):
        self.client_id = _client(client_id)
        self.tenant_dir = _tenant_directory(info_base, self.client_id)
        self.path = self.tenant_dir / "codex_marketplace_margin.sqlite3"
        self._lock = _lock_for_db(self.path)
        self._ensure_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS margin_snapshot_cache (
                    cache_key TEXT PRIMARY KEY,
                    created_ts REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS margin_order_ledger (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    natural_key TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    store TEXT NOT NULL,
                    order_date TEXT,
                    order_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    variation_id TEXT,
                    sku TEXT,
                    observed_ts REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_margin_order_scope
                    ON margin_order_ledger(client_id, store, order_date, sku);
                CREATE INDEX IF NOT EXISTS idx_margin_order_natural
                    ON margin_order_ledger(natural_key, observed_ts DESC, seq DESC);
                CREATE TABLE IF NOT EXISTS margin_reconciliation_ledger (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    client_id TEXT NOT NULL,
                    store TEXT NOT NULL,
                    target_day TEXT NOT NULL,
                    checked_on TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_ts REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_margin_reconciliation_scope
                    ON margin_reconciliation_ledger(client_id, store, target_day, observed_ts DESC);
                """
            )

    def _cache_entry(self, cache_key: str) -> Optional[_CacheEntry]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT created_ts, payload_json FROM margin_snapshot_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return _CacheEntry(float(row["created_ts"]), payload if isinstance(payload, dict) else {})

    def _cache_set(self, cache_key: str, payload: dict, *, created_ts: Optional[float] = None) -> None:
        timestamp = float(created_ts if created_ts is not None else time.time())
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO margin_snapshot_cache(cache_key, created_ts, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    created_ts = excluded.created_ts,
                    payload_json = excluded.payload_json
                """,
                (cache_key, timestamp, _canonical_json(payload)),
            )

    def upsert_orders(
        self,
        store: str,
        orders: Iterable[dict],
        *,
        observed_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Acrescenta observacoes; payload identico e reprocessado nao duplica."""

        exact_store = _store(store)
        observed = observed_at or _utc_now()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed_ts = observed.timestamp()
        observed_iso = _iso_utc(observed)
        inserted = 0
        duplicates = 0
        accepted: list[dict[str, Any]] = []
        with self._lock, self._connection() as conn:
            for raw in orders:
                if not isinstance(raw, dict):
                    continue
                row = deepcopy(raw)
                if _client(row.get("client_id") or self.client_id) != self.client_id:
                    raise ValueError("linha de outro client_id recusada pelo ledger")
                if _store(row.get("store") or exact_store) != exact_store:
                    raise ValueError("linha de outra loja recusada pelo ledger")
                item_id = _mlb(row.get("item_id"))
                order_id = _text(row.get("order_id"))
                if not item_id or not order_id:
                    continue
                row["client_id"] = self.client_id
                row["store"] = exact_store
                row["item_id"] = item_id
                row["variation_id"] = _text(row.get("variation_id")) or None
                row["sku"] = _sku(row.get("sku"))
                natural_key = _digest(
                    {
                        "client_id": self.client_id,
                        "store": exact_store,
                        "order_id": order_id,
                        "line_number": row.get("line_number"),
                        "item_id": item_id,
                        "variation_id": row.get("variation_id"),
                    }
                )
                previous_row = conn.execute(
                    """
                    SELECT payload_json
                    FROM margin_order_ledger
                    WHERE natural_key = ? AND client_id = ? AND store = ?
                    ORDER BY observed_ts DESC, rowid DESC
                    LIMIT 1
                    """,
                    (natural_key, self.client_id, exact_store),
                ).fetchone()
                if previous_row is not None:
                    try:
                        previous_payload = json.loads(previous_row["payload_json"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        previous_payload = {}
                    preservation_groups = (
                        (
                            "historical_cost_confirmed",
                            ("unit_cost", "cost_source", "cost_scope", "cost_observed_at", "historical_cost_basis"),
                        ),
                        (
                            "historical_tax_confirmed",
                            ("tax_pct", "tax_source", "tax_scope", "tax_observed_at", "historical_tax_basis"),
                        ),
                    )
                    preserved_historical_component = False
                    for confirmation_key, component_keys in preservation_groups:
                        if previous_payload.get(confirmation_key) is not True or row.get(confirmation_key) is True:
                            continue
                        row[confirmation_key] = True
                        for component_key in component_keys:
                            if component_key in previous_payload:
                                row[component_key] = deepcopy(previous_payload.get(component_key))
                        preserved_historical_component = True
                    if preserved_historical_component and previous_payload.get("historical_component_basis"):
                        row["historical_component_basis"] = previous_payload.get("historical_component_basis")
                event_payload = {key: value for key, value in row.items() if key not in {"event_id", "observed_at"}}
                event_id = _digest({"natural_key": natural_key, "payload": event_payload})
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO margin_order_ledger(
                        event_id, natural_key, client_id, store, order_date, order_id,
                        item_id, variation_id, sku, observed_ts, observed_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        natural_key,
                        self.client_id,
                        exact_store,
                        _text(row.get("order_date")) or None,
                        order_id,
                        item_id,
                        row.get("variation_id"),
                        row.get("sku"),
                        observed_ts,
                        observed_iso,
                        _canonical_json(event_payload),
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
                accepted.append({**event_payload, "event_id": event_id, "observed_at": observed_iso})
        return {"inserted": inserted, "duplicates": duplicates, "rows": accepted}

    def list_rows(
        self,
        stores: Optional[Iterable[str]] = None,
        period_start: Any = None,
        period_end: Any = None,
        relevant_skus: Optional[Iterable[str]] = None,
    ) -> list[dict]:
        exact_stores = {_store(value) for value in stores or []}
        wanted_skus = {_sku(value) for value in relevant_skus or [] if _sku(value)}
        clauses = ["client_id = ?"]
        params: list[Any] = [self.client_id]
        if exact_stores:
            marks = ",".join("?" for _ in exact_stores)
            clauses.append(f"store IN ({marks})")
            params.extend(sorted(exact_stores))
        if period_start is not None:
            clauses.append("order_date >= ?")
            params.append(_parse_date(period_start).isoformat())
        if period_end is not None:
            clauses.append("order_date <= ?")
            params.append(_parse_date(period_end).isoformat())
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT seq, event_id, natural_key, observed_at, payload_json
                FROM margin_order_ledger
                WHERE {' AND '.join(clauses)}
                ORDER BY observed_ts DESC, seq DESC
                """,
                params,
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for raw in rows:
            natural_key = str(raw["natural_key"])
            if natural_key in latest:
                continue
            try:
                payload = json.loads(str(raw["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if wanted_skus and _sku(payload.get("sku")) not in wanted_skus:
                continue
            latest[natural_key] = {
                **payload,
                "event_id": str(raw["event_id"]),
                "observed_at": str(raw["observed_at"]),
            }
        return sorted(
            latest.values(),
            key=lambda row: (
                _text(row.get("store")),
                _text(row.get("order_date")),
                _text(row.get("order_id")),
                _integer(row.get("line_number")) or 0,
            ),
        )

    def should_reconcile(self, store: str, day: Any, *, now: Optional[datetime] = None) -> bool:
        exact_store = _store(store)
        target_day = _parse_date(day).isoformat()
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        checked_on = current.astimezone(SAO_PAULO).date().isoformat()
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT status, checked_on
                FROM margin_reconciliation_ledger
                WHERE client_id = ? AND store = ? AND target_day = ?
                ORDER BY observed_ts DESC, seq DESC
                LIMIT 1
                """,
                (self.client_id, exact_store, target_day),
            ).fetchone()
        return not bool(row and row["status"] == "complete" and row["checked_on"] == checked_on)

    def mark_reconciled(
        self,
        store: str,
        day: Any,
        status: str,
        metadata: Optional[dict] = None,
        *,
        reconciled_at: Optional[datetime] = None,
    ) -> dict[str, Any]:
        exact_store = _store(store)
        target_day = _parse_date(day).isoformat()
        normalized_status = _text(status).lower()
        if normalized_status not in {"complete", "partial"}:
            raise ValueError("status de reconciliacao deve ser complete ou partial")
        observed = reconciled_at or _utc_now()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed_ts = observed.timestamp()
        observed_iso = _iso_utc(observed)
        checked_on = observed.astimezone(SAO_PAULO).date().isoformat()
        clean_metadata = deepcopy(metadata) if isinstance(metadata, dict) else {}
        event_id = _digest(
            {
                "client_id": self.client_id,
                "store": exact_store,
                "target_day": target_day,
                "checked_on": checked_on,
                "status": normalized_status,
                "metadata": clean_metadata,
            }
        )
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO margin_reconciliation_ledger(
                    event_id, client_id, store, target_day, checked_on, status,
                    observed_ts, observed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    self.client_id,
                    exact_store,
                    target_day,
                    checked_on,
                    normalized_status,
                    observed_ts,
                    observed_iso,
                    _canonical_json(clean_metadata),
                ),
            )
        return {"event_id": event_id, "inserted": bool(cursor.rowcount), "status": normalized_status}


def _response_is_partial(payload: Any) -> bool:
    data = _mapping(payload)
    status = _text(data.get("status")).lower()
    coverage = _mapping(data.get("coverage"))
    structured_error = data.get("error") not in (None, "", False, [], {}) or bool(data.get("errors"))
    explicit_partial = bool(data.get("partial_response")) or data.get("success") is False
    incomplete_coverage = (
        coverage.get("complete") is False
        or data.get("coverage_complete") is False
        or data.get("complete") is False
        or data.get("truncated") is True
    )
    return (
        status in {"partial", "blocked", "unavailable", "error", "failed", "timeout"}
        or structured_error
        or explicit_partial
        or incomplete_coverage
    )


def _next_order_offset(payload: Any, offset: int, returned: int) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    data = _mapping(payload)
    paging = _mapping(data.get("paging"))
    explicit = _integer(data.get("next_offset") if data.get("next_offset") is not None else paging.get("next_offset"))
    if explicit is not None and explicit > offset:
        return explicit
    has_more = data.get("has_more") if isinstance(data.get("has_more"), bool) else paging.get("has_more")
    total = _integer(paging.get("total") if paging.get("total") is not None else data.get("total"))
    candidate = offset + returned
    if has_more is True:
        return candidate if candidate > offset else None
    if total is not None and candidate < total:
        return candidate if candidate > offset else None
    if total is None and has_more is None and returned >= MAX_ORDER_BATCH:
        return candidate
    return None


def _fetch_orders_for_day(
    fetcher: Callable[..., Any],
    client_id: str,
    store: str,
    day: date,
) -> tuple[list[dict], bool, list[str], int]:
    collected: list[dict] = []
    warnings: list[str] = []
    offset = 0
    pages = 0
    complete = True
    while pages < MAX_ORDER_PAGES_PER_DAY:
        try:
            payload = call_with_retry(
                fetcher,
                client_id=client_id,
                store=store,
                date_from=day.isoformat(),
                date_to=day.isoformat(),
                offset=offset,
                limit=MAX_ORDER_BATCH,
            )
        except Exception as exc:
            complete = False
            warnings.append(f"Pedidos de {day.isoformat()} indisponiveis: {type(exc).__name__}.")
            break
        pages += 1
        rows = _extract_rows(payload, ("orders", "results", "items", "data"))
        collected.extend(rows)
        if _response_is_partial(payload):
            complete = False
            warnings.append(f"Resposta parcial de pedidos para {day.isoformat()}; o dia sera reconciliado novamente.")
        next_offset = _next_order_offset(payload, offset, len(rows))
        if next_offset is None:
            break
        offset = next_offset
    else:
        complete = False
        warnings.append(f"Paginacao de pedidos de {day.isoformat()} atingiu o limite de seguranca.")
    deduplicated: dict[str, dict] = {}
    for index, order in enumerate(collected):
        identity = _text(order.get("order_id") or order.get("id")) or f"payload:{_digest(order)}:{index}"
        deduplicated[identity] = order
    return list(deduplicated.values()), complete, warnings, pages


def _bounded_call_map(
    values: Iterable[Any],
    operation: Callable[[Any], Any],
) -> tuple[dict[Any, Any], dict[Any, Exception]]:
    entries = list(values)
    results: dict[Any, Any] = {}
    failures: dict[Any, Exception] = {}
    if not entries:
        return results, failures
    workers = max(1, min(int(MAX_CONCURRENCY), len(entries)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ml-margin") as executor:
        future_map = {executor.submit(operation, value): value for value in entries}
        for future in as_completed(future_map):
            value = future_map[future]
            try:
                results[value] = future.result()
            except Exception as exc:
                failures[value] = exc
    return results, failures


def _cache_key(
    client_id: str,
    stores: Iterable[str],
    period_start: date,
    period_end: date,
    relevant_skus: Iterable[str],
    capabilities: dict[str, bool],
) -> str:
    return "marketplace-margin:" + _digest(
        {
            "schema": SCHEMA_VERSION,
            "client_id": client_id,
            "stores": sorted(stores),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "relevant_skus": sorted(relevant_skus),
            "capabilities": capabilities,
        }
    )


def _empty_snapshot(
    client_id: str,
    stores: list[str],
    period_start: date,
    period_end: date,
    warning: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "status": "partial",
        "collected_at": _iso_utc(),
        "stale": False,
        "client_id": client_id,
        "stores": stores,
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat(), "timezone": str(SAO_PAULO)},
        "coverage": {"complete": False, "cache_hit": False, "stores": {}},
        "listing_rows": [],
        "order_rows": [],
        "ledger_rows": [],
        "sources": [],
        "warnings": [warning],
    }


def _collect_uncached(
    ledger: MarketplaceMarginLedger,
    client_id: str,
    stores: list[str],
    period_start: date,
    period_end: date,
    relevant_skus: set[str],
    *,
    force_refresh: bool,
    listing_fetcher: Optional[Callable[..., Any]],
    orders_fetcher: Optional[Callable[..., Any]],
    shipping_fetcher: Optional[Callable[..., Any]],
    commercial_fetcher: Optional[Callable[..., Any]],
    order_billing_fetcher: Optional[Callable[..., Any]],
) -> dict[str, Any]:
    warnings: list[str] = []
    sources: list[dict[str, Any]] = []
    listing_rows: list[dict[str, Any]] = []
    coverage_stores: dict[str, Any] = {}
    complete = True

    for exact_store in stores:
        store_coverage: dict[str, Any] = {
            "listings": {"status": "partial", "returned": None},
            "orders": {"status": "partial", "days": []},
            "commercial": {"status": "not_requested", "requested": 0, "completed": 0},
            "billing": {"status": "not_requested", "requested": 0, "completed": 0},
            "order_billing": {"status": "not_requested", "requested": 0, "completed": 0},
            "shipping": {"status": "not_requested", "requested": 0, "completed": 0},
        }
        coverage_stores[exact_store] = store_coverage

        raw_listings: list[dict] = []
        listings_complete = False
        if listing_fetcher is None:
            warnings.append(f"Coletor de anuncios nao configurado para {exact_store}.")
            complete = False
        else:
            try:
                listing_payload = call_with_retry(
                    listing_fetcher,
                    client_id=client_id,
                    store=exact_store,
                    relevant_skus=sorted(relevant_skus),
                )
                raw_listings = _extract_rows(listing_payload, ("listings", "results", "items", "data"))
                listings_complete = not _response_is_partial(listing_payload)
                sources.append({"provider": "mercado_livre", "resource": "items/search+multiget", "method": "GET", "store": exact_store})
            except Exception as exc:
                warnings.append(f"Anuncios de {exact_store} indisponiveis: {type(exc).__name__}.")
                complete = False

        enriched: dict[int, dict] = {index: listing for index, listing in enumerate(raw_listings)}
        if commercial_fetcher is not None and raw_listings:
            store_coverage["commercial"]["status"] = "complete"
            store_coverage["commercial"]["requested"] = len(raw_listings)

            def fetch_billing(index: int) -> dict:
                listing = raw_listings[index]
                item_id = _mlb(listing.get("id") or listing.get("item_id"))
                if not item_id:
                    raise ValueError("MLB invalido")
                payload = call_with_retry(
                    commercial_fetcher,
                    client_id=client_id,
                    store=exact_store,
                    item_id=item_id,
                    listing=deepcopy(listing),
                )
                return _merge_billing(listing, payload)

            billed, billing_failures = _bounded_call_map(range(len(raw_listings)), fetch_billing)
            enriched.update(billed)
            store_coverage["commercial"]["completed"] = len(billed)
            if billing_failures:
                store_coverage["commercial"]["status"] = "partial"
                complete = False
                for index, exc in billing_failures.items():
                    item_id = _text(raw_listings[index].get("id") or raw_listings[index].get("item_id"))
                    warnings.append(f"Dados comerciais de {exact_store}/{item_id or 'MLB desconhecido'} indisponiveis: {type(exc).__name__}.")
            sources.append({"provider": "mercado_livre", "resource": "sale_price+listing_prices", "method": "GET", "store": exact_store})
        store_coverage["billing"] = deepcopy(store_coverage["commercial"])

        normalized_for_store: list[dict] = []
        invalid_listings = 0
        for index in range(len(raw_listings)):
            rows = normalize_commercial_listing(enriched[index], client_id, exact_store)
            if not rows:
                invalid_listings += 1
                continue
            normalized_for_store.extend(rows)
        if invalid_listings:
            listings_complete = False
            warnings.append(f"{invalid_listings} anuncio(s) de {exact_store} foram ignorados por MLB invalido.")
        commercial_incomplete = [row for row in normalized_for_store if row.get("commercial_coverage_complete") is not True]
        if commercial_incomplete:
            listings_complete = False
        store_coverage["listings"] = {
            "status": "complete" if listings_complete else "partial",
            "returned": len(normalized_for_store) if raw_listings or listings_complete else None,
            "observed": len(normalized_for_store),
            "invalid": invalid_listings,
            "commercial_incomplete": len(commercial_incomplete),
        }
        if not listings_complete:
            complete = False
        listing_rows.extend(normalized_for_store)

        if orders_fetcher is None:
            warnings.append(f"Coletor de pedidos nao configurado para {exact_store}.")
            complete = False
            for day in _days(period_start, period_end):
                store_coverage["orders"]["days"].append({"day": day.isoformat(), "status": "partial", "orders": None, "pages": None})
            continue

        all_days_complete = True
        for day in _days(period_start, period_end):
            if not force_refresh and not ledger.should_reconcile(exact_store, day):
                store_coverage["orders"]["days"].append(
                    {"day": day.isoformat(), "status": "complete", "orders": None, "pages": 0, "source": "ledger"}
                )
                continue

            raw_orders, orders_complete, day_warnings, pages = _fetch_orders_for_day(
                orders_fetcher,
                client_id,
                exact_store,
                day,
            )
            warnings.extend(day_warnings)
            order_rows: list[dict] = []
            invalid_order_lines = 0
            for order in raw_orders:
                rows = normalize_order_rows(order, client_id, exact_store)
                if not rows and _extract_rows(order, ("items", "order_items")):
                    invalid_order_lines += 1
                order_rows.extend(rows)

            shipment_ids = sorted({_text(row.get("shipment_id")) for row in order_rows if _text(row.get("shipment_id"))})
            shipment_payloads: dict[str, Any] = {}
            shipment_failures: dict[str, Exception] = {}
            rows_without_shipment = [row for row in order_rows if not _text(row.get("shipment_id"))]
            if shipment_ids and shipping_fetcher is not None:
                store_coverage["shipping"]["status"] = "complete"
                store_coverage["shipping"]["requested"] += len(shipment_ids)

                def fetch_shipping(shipment_id: str) -> Any:
                    return call_with_retry(
                        shipping_fetcher,
                        client_id=client_id,
                        store=exact_store,
                        shipment_id=shipment_id,
                    )

                shipment_payloads, shipment_failures = _bounded_call_map(shipment_ids, fetch_shipping)
                store_coverage["shipping"]["completed"] += len(shipment_payloads)
                if shipment_failures:
                    store_coverage["shipping"]["status"] = "partial"
                    for shipment_id, exc in shipment_failures.items():
                        warnings.append(f"Frete de {exact_store}/shipment {shipment_id} indisponivel: {type(exc).__name__}.")
            elif shipment_ids:
                store_coverage["shipping"]["status"] = "partial"
                store_coverage["shipping"]["requested"] += len(shipment_ids)
                warnings.append(f"Coletor de frete nao configurado para {exact_store}.")
            if rows_without_shipment:
                store_coverage["shipping"]["status"] = "partial"
                warnings.append(f"{len(rows_without_shipment)} linha(s) de pedido de {exact_store} sem shipment_id; frete nao consultado.")

            order_rows = apply_pack_shipping(order_rows, shipment_payloads, set(shipment_failures))
            shipping_complete = not order_rows or (
                shipping_fetcher is not None
                and not shipment_failures
                and not rows_without_shipment
                and all(_shipping_cost(shipment_payloads.get(shipment_id)) is not None for shipment_id in shipment_ids)
            )

            order_ids = sorted({_text(row.get("order_id")) for row in order_rows if _text(row.get("order_id"))})
            billing_records: dict[str, dict[str, Any]] = {}
            billing_failures: dict[tuple[str, ...], Exception] = {}
            billing_complete = not order_ids
            if order_ids and order_billing_fetcher is not None:
                batches = [tuple(batch) for batch in _chunks(order_ids, MAX_ORDER_BATCH)]
                store_coverage["order_billing"]["status"] = "complete"
                store_coverage["order_billing"]["requested"] += len(order_ids)

                def fetch_order_billing(batch: tuple[str, ...]) -> Any:
                    return call_with_retry(
                        order_billing_fetcher,
                        client_id=client_id,
                        store=exact_store,
                        order_ids=list(batch),
                    )

                billing_payloads, billing_failures = _bounded_call_map(batches, fetch_order_billing)
                for payload in billing_payloads.values():
                    billing_records.update(_order_billing_records(payload))
                order_rows, reconciled_orders = apply_order_billing(order_rows, billing_records, store=exact_store)
                store_coverage["order_billing"]["completed"] += len(reconciled_orders)
                billing_complete = not billing_failures and len(reconciled_orders) == len(order_ids)
                if not billing_complete:
                    store_coverage["order_billing"]["status"] = "partial"
                    warnings.append(
                        f"Faturamento pos-venda de {exact_store} ficou parcial: "
                        f"{len(reconciled_orders)}/{len(order_ids)} pedido(s) reconciliado(s)."
                    )
                for batch, exc in billing_failures.items():
                    warnings.append(
                        f"Faturamento de {exact_store} para lote de {len(batch)} pedido(s) indisponivel: {type(exc).__name__}."
                    )
                sources.append(
                    {
                        "provider": "mercado_livre",
                        "resource": "order_billing",
                        "method": "GET",
                        "store": exact_store,
                        "day": day.isoformat(),
                    }
                )
            elif order_ids:
                store_coverage["order_billing"]["status"] = "partial"
                store_coverage["order_billing"]["requested"] += len(order_ids)
                order_rows, _ = apply_order_billing(order_rows, {}, store=exact_store)
                warnings.append(f"Coletor de faturamento pos-venda nao configurado para {exact_store}.")

            day_complete = orders_complete and shipping_complete and billing_complete and invalid_order_lines == 0
            ledger_result = ledger.upsert_orders(exact_store, order_rows)
            metadata = {
                "orders": len(raw_orders) if orders_complete else None,
                "observed_orders": len(raw_orders),
                "rows": len(order_rows) if day_complete else None,
                "observed_rows": len(order_rows),
                "pages": pages,
                "invalid_order_lines": invalid_order_lines,
                "shipping_complete": shipping_complete,
                "order_billing_complete": billing_complete,
                "ledger_inserted": ledger_result["inserted"],
                "ledger_duplicates": ledger_result["duplicates"],
            }
            ledger.mark_reconciled(exact_store, day, "complete" if day_complete else "partial", metadata)
            store_coverage["orders"]["days"].append(
                {
                    "day": day.isoformat(),
                    "status": "complete" if day_complete else "partial",
                    "orders": len(raw_orders) if orders_complete else None,
                    "observed_orders": len(raw_orders),
                    "pages": pages,
                }
            )
            sources.append({"provider": "mercado_livre", "resource": "orders/search", "method": "GET", "store": exact_store, "day": day.isoformat()})
            if shipment_ids:
                sources.append({"provider": "mercado_livre", "resource": "shipments/{id}/costs", "method": "GET", "store": exact_store, "day": day.isoformat()})
            if not day_complete:
                all_days_complete = False
                complete = False
        store_coverage["orders"]["status"] = "complete" if all_days_complete else "partial"

    ledger_rows = ledger.list_rows(stores, period_start, period_end, None)
    sold_keys = {
        (_text(row.get("store")), _text(row.get("item_id")), _text(row.get("variation_id")))
        for row in ledger_rows
    }
    scoped_listing_rows: list[dict[str, Any]] = []
    for row in listing_rows:
        exact_key = (_text(row.get("store")), _text(row.get("item_id")), _text(row.get("variation_id")))
        item_key = (exact_key[0], exact_key[1], "")
        has_stock = (_number(row.get("available_quantity")) or 0.0) > 0
        sold_in_period = exact_key in sold_keys or item_key in sold_keys or any(
            sold_store == exact_key[0] and sold_item == exact_key[1]
            for sold_store, sold_item, _ in sold_keys
        )
        if has_stock or sold_in_period:
            scoped_listing_rows.append(row)
    filtered_count = len(listing_rows) - len(scoped_listing_rows)
    scoped_by_store: dict[str, int] = {}
    for row in scoped_listing_rows:
        scoped_by_store[_text(row.get("store"))] = scoped_by_store.get(_text(row.get("store")), 0) + 1
    for exact_store in stores:
        listing_coverage = _mapping(_mapping(coverage_stores.get(exact_store)).get("listings"))
        listing_coverage["scope_returned"] = scoped_by_store.get(exact_store, 0)
        listing_coverage["scope"] = "sold_in_period_or_available_quantity_gt_zero"
    if filtered_count:
        warnings.append(
            f"{filtered_count} linha(s) de anuncio sem estoque e sem venda no periodo ficaram fora do snapshot gerencial."
        )
    listing_rows = scoped_listing_rows
    unique_sources = []
    seen_sources: set[str] = set()
    for source in sources:
        identity = _canonical_json(source)
        if identity not in seen_sources:
            seen_sources.add(identity)
            unique_sources.append(source)
    return {
        "schema": SCHEMA_VERSION,
        "status": "ok" if complete else "partial",
        "collected_at": _iso_utc(),
        "stale": False,
        "client_id": client_id,
        "stores": stores,
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat(), "timezone": str(SAO_PAULO)},
        "coverage": {"complete": complete, "cache_hit": False, "stores": coverage_stores},
        "listing_rows": listing_rows,
        "order_rows": deepcopy(ledger_rows),
        "ledger_rows": ledger_rows,
        "sources": unique_sources,
        "warnings": warnings[:200],
    }


def collect_marketplace_commercial_snapshot(
    info_base: str,
    client_id: str,
    stores: Iterable[str],
    period_start: Any,
    period_end: Any,
    relevant_skus: Optional[Iterable[str]],
    force_refresh: bool = False,
    listing_fetcher: Optional[Callable[..., Any]] = None,
    orders_fetcher: Optional[Callable[..., Any]] = None,
    shipping_fetcher: Optional[Callable[..., Any]] = None,
    billing_fetcher: Optional[Callable[..., Any]] = None,
    commercial_fetcher: Optional[Callable[..., Any]] = None,
    order_billing_fetcher: Optional[Callable[..., Any]] = None,
) -> dict:
    """Coleta snapshot comercial isolado, cacheado e reconciliado por loja/dia."""

    tenant = _client(client_id)
    exact_stores = []
    seen_stores: set[str] = set()
    for value in stores or []:
        exact = _store(value)
        if exact not in seen_stores:
            seen_stores.add(exact)
            exact_stores.append(exact)
    if not exact_stores:
        raise ValueError("stores deve conter ao menos uma loja exata")
    start = _parse_date(period_start)
    end = _parse_date(period_end)
    if end < start:
        raise ValueError("period_end deve ser igual ou posterior a period_start")
    wanted_skus = {_sku(value) for value in relevant_skus or [] if _sku(value)}
    effective_commercial_fetcher = commercial_fetcher or billing_fetcher
    ledger = MarketplaceMarginLedger(info_base, tenant)
    cache_key = _cache_key(
        tenant,
        exact_stores,
        start,
        end,
        wanted_skus,
        {
            "listing": listing_fetcher is not None,
            "orders": orders_fetcher is not None,
            "shipping": shipping_fetcher is not None,
            "commercial": effective_commercial_fetcher is not None,
            "order_billing": order_billing_fetcher is not None,
        },
    )
    flight_key = f"{ledger.path.resolve()}::{cache_key}"
    request_started = time.time()

    initial_cache = ledger._cache_entry(cache_key)
    if not force_refresh and initial_cache and request_started - initial_cache.created_ts <= CACHE_TTL_SECONDS:
        cached = deepcopy(initial_cache.payload)
        cached["stale"] = False
        cached.setdefault("coverage", {})["cache_hit"] = True
        return cached

    with _lock_for_flight(flight_key):
        current_cache = ledger._cache_entry(cache_key)
        if current_cache and time.time() - current_cache.created_ts <= CACHE_TTL_SECONDS:
            newer_than_request = current_cache.created_ts >= request_started
            if not force_refresh or newer_than_request:
                cached = deepcopy(current_cache.payload)
                cached["stale"] = False
                cached.setdefault("coverage", {})["cache_hit"] = True
                return cached
        try:
            snapshot = _collect_uncached(
                ledger,
                tenant,
                exact_stores,
                start,
                end,
                wanted_skus,
                force_refresh=bool(force_refresh),
                listing_fetcher=listing_fetcher,
                orders_fetcher=orders_fetcher,
                shipping_fetcher=shipping_fetcher,
                commercial_fetcher=effective_commercial_fetcher,
                order_billing_fetcher=order_billing_fetcher,
            )
        except Exception as exc:
            if current_cache:
                stale = deepcopy(current_cache.payload)
                stale["status"] = "partial"
                stale["stale"] = True
                stale.setdefault("coverage", {})["complete"] = False
                stale["coverage"]["cache_hit"] = True
                stale.setdefault("warnings", []).append(
                    f"Coleta atual falhou ({type(exc).__name__}); snapshot anterior identificado como desatualizado."
                )
                return stale
            return _empty_snapshot(
                tenant,
                exact_stores,
                start,
                end,
                f"Coleta comercial indisponivel: {type(exc).__name__}.",
            )
        ledger._cache_set(cache_key, snapshot)
        return deepcopy(snapshot)


__all__ = [
    "CACHE_TTL_SECONDS",
    "MAX_CONCURRENCY",
    "MAX_ORDER_BATCH",
    "MarketplaceMarginLedger",
    "apply_pack_shipping",
    "apply_order_billing",
    "call_with_retry",
    "collect_marketplace_commercial_snapshot",
    "normalize_commercial_listing",
    "normalize_order_rows",
    "retry_after_seconds",
]
