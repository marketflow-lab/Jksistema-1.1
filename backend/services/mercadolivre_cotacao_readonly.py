"""Read-only candidate-price resolver for the isolated comparison pilot.

This module deliberately does not know about routes or promotion decisions.  It
reuses injected Mercado Livre readers to resolve a total sale fee and seller
shipping for one candidate price, then delegates only the arithmetic and
evidence validation to ``mercadolivre_cotacao_financeira``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable

from backend.services.mercadolivre_cotacao_financeira import (
    FinancialQuoteRequest,
    MonetaryEvidence,
    MonetaryEvidenceRole,
    MonetaryEvidenceState,
    calculate_quote,
)


READONLY_QUOTE_CONTRACT_VERSION = "mercadolivre.financial_quote_readonly.v1"


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be numeric")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise ValueError(f"invalid {field}")
    return parsed


def _optional_decimal(value: Any, *, allow_negative: bool = False) -> Decimal | None:
    if value in (None, ""):
        return None
    if allow_negative:
        if isinstance(value, bool):
            raise TypeError("financial component must be numeric")
        try:
            parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid financial component") from exc
        if not parsed.is_finite():
            raise ValueError("invalid financial component")
        return parsed
    return _decimal(value, "financial component")


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Decimal, float)):
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise ValueError("non-finite context")
        return format(parsed.normalize(), "f")
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return str(value)


def _fingerprint(
    *,
    effective_price: Decimal,
    product_cost: Decimal,
    tax_rate: Decimal,
    item: dict,
    shipping_context: dict,
) -> str:
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    payload = {
        "contract": READONLY_QUOTE_CONTRACT_VERSION,
        "effective_price": effective_price,
        "product_cost": product_cost,
        "tax_rate": tax_rate,
        "category_id": item.get("category_id") or "",
        "listing_type_id": item.get("listing_type_id") or "",
        "condition": item.get("condition") or "",
        "currency_id": item.get("currency_id") or "BRL",
        "logistic_type": shipping.get("logistic_type") or "",
        "shipping_mode": shipping.get("mode") or "",
        "shipping_context": shipping_context,
    }
    raw = json.dumps(
        _canonical(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _state(exact: bool, amount: Decimal | None) -> MonetaryEvidenceState:
    if exact:
        return MonetaryEvidenceState.CONTEXTUAL
    if amount is None:
        return MonetaryEvidenceState.UNAVAILABLE
    return MonetaryEvidenceState.ESTIMATED


def _source_kind(value: Any, component: str) -> str:
    source = str(value or "").strip().lower()
    if component == "sale_fee" and "listing_prices" in source:
        return "mercadolivre.listing_prices"
    if component == "seller_shipping" and (
        "shipping_options" in source or "shipping_option" in source
    ):
        return "mercadolivre.shipping_options"
    if source:
        return "mercadolivre.other"
    return "unavailable"


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _response_payload(response: Any) -> dict[str, Any]:
    if int(getattr(response, "status_code", 0) or 0) != 200:
        return {}
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_sale_fee_readonly(
    *,
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    effective_price: Decimal,
    request_fn: Callable[..., Any],
) -> tuple[dict[str, Any], dict]:
    """Query listing_prices directly, without touching the legacy fee cache."""

    category_id = str(item.get("category_id") or "").strip()
    listing_type_id = str(item.get("listing_type_id") or "").strip()
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    logistic_type = str(shipping.get("logistic_type") or "").strip()
    shipping_mode = str(shipping.get("mode") or "").strip()
    result = {
        "amount": None,
        "exact": False,
        "source": "",
        "price_context": effective_price,
    }
    if not category_id or not listing_type_id:
        return result, cfg
    response, cfg = request_fn(
        client_id,
        loja,
        dict(cfg or {}),
        "GET",
        "https://api.mercadolibre.com/sites/MLB/listing_prices",
        params={
            "price": _decimal_text(effective_price),
            "currency_id": "BRL",
            "listing_type_id": listing_type_id,
            "category_id": category_id,
            "quantity": 1,
            **({"logistic_type": logistic_type} if logistic_type else {}),
            **({"shipping_mode": shipping_mode} if shipping_mode else {}),
        },
        timeout=12,
    )
    payload = _response_payload(response)
    amount = _optional_decimal(payload.get("sale_fee_amount"))
    if amount is not None:
        result.update(
            {
                "amount": amount,
                "exact": bool(logistic_type and shipping_mode),
                "source": "sites/MLB/listing_prices",
            }
        )
    return result, cfg


def _resolve_seller_shipping_readonly(
    *,
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    shipping_context: dict,
    effective_price: Decimal,
    request_fn: Callable[..., Any],
) -> tuple[dict[str, Any], dict]:
    """Make one contextual shipping query, with no fallback or shared cache."""

    result = {
        "amount": None,
        "exact": False,
        "source": "",
        "price_context": effective_price,
    }
    user_id = str((cfg or {}).get("user_id") or "").strip()
    if not user_id:
        return result, cfg
    params: dict[str, Any] = {
        "item_id": item_id,
        "item_price": _decimal_text(effective_price),
        "free_shipping": (
            "true" if bool(shipping_context.get("free_shipping")) else "false"
        ),
        "verbose": "true",
    }
    for source_key, target_key in (
        ("listing_type_id", "listing_type_id"),
        ("condition", "condition"),
        ("category_id", "category_id"),
        ("mode", "mode"),
        ("shipping_mode", "mode"),
        ("logistic_type", "logistic_type"),
        ("dimensions", "dimensions"),
    ):
        value = shipping_context.get(source_key)
        if value not in (None, "") and target_key not in params:
            params[target_key] = value
    response, cfg = request_fn(
        client_id,
        loja,
        dict(cfg or {}),
        "GET",
        f"https://api.mercadolibre.com/users/{user_id}/shipping_options/free",
        params=params,
        timeout=12,
    )
    payload = _response_payload(response)
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    all_country = (
        coverage.get("all_country")
        if isinstance(coverage.get("all_country"), dict)
        else {}
    )
    source_path = ""
    amount = None
    for field in ("seller_cost", "list_cost"):
        try:
            candidate = _optional_decimal(
                all_country.get(field),
                allow_negative=True,
            )
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None:
            amount = candidate
            source_path = field
            break
    if amount is not None:
        result.update(
            {
                "amount": amount,
                "exact": True,
                "source": (
                    "users/shipping_options/free/contexto:"
                    f"coverage.all_country.{source_path}"
                ),
            }
        )
    return result, cfg


def _public_result(
    *,
    quote: Any,
    sale_fee: Decimal | None,
    sale_fee_exact: bool,
    sale_fee_source: Any,
    seller_shipping: Decimal | None,
    seller_shipping_exact: bool,
    seller_shipping_source: Any,
) -> dict[str, Any]:
    return {
        "contract_version": READONLY_QUOTE_CONTRACT_VERSION,
        "quote_contract_version": quote.contract_version,
        "status": quote.status.value,
        "decision_eligible": bool(quote.decision_eligible),
        "tax_amount": _decimal_text(quote.tax_amount),
        "net_amount": _decimal_text(quote.net_amount),
        "margin_percent": _decimal_text(quote.margin_percent),
        "decision_margin_percent": _decimal_text(quote.decision_margin_percent),
        "missing": list(quote.missing),
        "conflicts": list(quote.conflicts),
        "components": {
            "sale_fee": {
                "amount": _decimal_text(sale_fee),
                "exact": bool(sale_fee_exact),
                "source": _source_kind(sale_fee_source, "sale_fee"),
            },
            "seller_shipping": {
                "amount": _decimal_text(seller_shipping),
                "exact": bool(seller_shipping_exact),
                "source": _source_kind(
                    seller_shipping_source,
                    "seller_shipping",
                ),
            },
        },
    }


def cotar_preco_candidato_readonly(
    *,
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    preco_efetivo: Any,
    custo_produto: Any,
    aliquota_imposto: Any,
    construir_contexto_frete: Callable[[dict, Any], dict],
    request_fn: Callable[..., Any],
) -> tuple[dict[str, Any], dict]:
    """Resolve and calculate one candidate quote without mutating the item."""

    if not str(client_id or "").strip() or not str(loja or "").strip():
        raise ValueError("missing quote scope")
    if not isinstance(item, dict):
        raise TypeError("item must be a mapping")
    item_id = str(item.get("id") or "").strip().upper()
    if not item_id:
        raise ValueError("item identity unavailable")
    effective_price = _decimal(preco_efetivo, "effective_price", positive=True)
    product_cost = _decimal(custo_produto, "product_cost")
    tax_rate = _decimal(aliquota_imposto, "tax_rate")
    if tax_rate > 1:
        raise ValueError("invalid tax_rate")

    item_quote = copy.deepcopy(item)
    item_quote["price"] = float(effective_price)
    shipping_context = construir_contexto_frete(item_quote, float(effective_price))
    if not isinstance(shipping_context, dict):
        raise TypeError("shipping context must be a mapping")
    cfg_local = dict(cfg or {})
    fee_data, cfg_local = _resolve_sale_fee_readonly(
        client_id=client_id,
        loja=loja,
        cfg=cfg_local,
        item=item_quote,
        effective_price=effective_price,
        request_fn=request_fn,
    )
    shipping_data, cfg_local = _resolve_seller_shipping_readonly(
        client_id=client_id,
        loja=loja,
        cfg=cfg_local,
        item_id=item_id,
        shipping_context=shipping_context,
        effective_price=effective_price,
        request_fn=request_fn,
    )
    fee_data = fee_data if isinstance(fee_data, dict) else {}
    shipping_data = shipping_data if isinstance(shipping_data, dict) else {}

    sale_fee = _optional_decimal(fee_data.get("amount"))
    seller_shipping = _optional_decimal(
        shipping_data.get("amount"),
        allow_negative=True,
    )
    sale_fee_exact = fee_data.get("exact") is True
    seller_shipping_exact = shipping_data.get("exact") is True
    sale_fee_source = fee_data.get("source") or ""
    seller_shipping_source = shipping_data.get("source") or ""
    fingerprint = _fingerprint(
        effective_price=effective_price,
        product_cost=product_cost,
        tax_rate=tax_rate,
        item=item_quote,
        shipping_context=shipping_context,
    )
    quote = calculate_quote(
        FinancialQuoteRequest(
            effective_price=effective_price,
            product_cost=product_cost,
            tax_rate=tax_rate,
            sale_fee=MonetaryEvidence(
                amount=sale_fee,
                state=_state(sale_fee_exact, sale_fee),
                role=MonetaryEvidenceRole.TOTAL_SALE_FEE,
                source=str(sale_fee_source or ""),
                price_context=_optional_decimal(fee_data.get("price_context")),
                context_fingerprint=fingerprint,
            ),
            seller_shipping=MonetaryEvidence(
                amount=seller_shipping,
                state=_state(seller_shipping_exact, seller_shipping),
                role=MonetaryEvidenceRole.SELLER_SHIPPING_NET,
                source=str(seller_shipping_source or ""),
                price_context=_optional_decimal(shipping_data.get("price_context")),
                context_fingerprint=fingerprint,
            ),
            currency_id=str(item_quote.get("currency_id") or "BRL").strip().upper(),
            context_fingerprint=fingerprint,
        )
    )
    return _public_result(
        quote=quote,
        sale_fee=sale_fee,
        sale_fee_exact=sale_fee_exact,
        sale_fee_source=sale_fee_source,
        seller_shipping=seller_shipping,
        seller_shipping_exact=seller_shipping_exact,
        seller_shipping_source=seller_shipping_source,
    ), cfg_local


__all__ = [
    "READONLY_QUOTE_CONTRACT_VERSION",
    "cotar_preco_candidato_readonly",
]
