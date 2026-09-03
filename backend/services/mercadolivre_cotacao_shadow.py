"""Shared shadow observer for Mercado Livre financial quotes.

Promoções and Favoritos may call this adapter only after their current result
has been calculated.  The adapter has no authority over that result.  It makes
no network or filesystem calls, stores no business identifiers and exposes the
same content-free aggregate counters used by the original Promoções pilot.
"""

from __future__ import annotations

import hashlib
import json
import threading
from decimal import (
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_UP,
    localcontext,
)
from enum import Enum
from typing import Any

from backend.services.mercadolivre_cotacao_financeira import (
    MONEY_QUANTUM,
    MARGIN_DECISION_QUANTUM,
    FinancialQuoteRequest,
    FinancialQuoteStatus,
    MonetaryEvidence,
    MonetaryEvidenceRole,
    MonetaryEvidenceState,
    calculate_quote,
)


_MATCH = "match"
_ROUNDING_DIFFERENCE = "rounding_difference"
_EXACTNESS_DIFFERENCE = "exactness_difference"
_VALUE_DIFFERENCE = "value_difference"
_SHADOW_INCOMPLETE = "shadow_incomplete"
_SHADOW_ERROR = "shadow_error"
_CLASSIFICATIONS = (
    _MATCH,
    _ROUNDING_DIFFERENCE,
    _EXACTNESS_DIFFERENCE,
    _VALUE_DIFFERENCE,
    _SHADOW_INCOMPLETE,
    _SHADOW_ERROR,
)
_ORIGINS = frozenset({"promocoes", "favoritos"})

_COUNTERS_LOCK = threading.Lock()
_COUNTERS = {classification: 0 for classification in _CLASSIFICATIONS}

_LEGACY_EXACT_KEYS = (
    "exato",
    "exact",
    "decision_eligible",
    "action_financeiro_exato",
    "margem_completa",
)
_LEGACY_TAX_KEYS = ("imposto", "imposto_valor", "tax_amount")
_LEGACY_NET_KEYS = (
    "valor_liquido",
    "valor_liquido_ml",
    "net_amount",
    "action_valor_liquido_ml",
)
_LEGACY_MARGIN_KEYS = (
    "margem_percentual",
    "margem",
    "margin_percent",
    "action_margem_ml",
)
_EXACTNESS_ONLY_CONFLICTS = frozenset(
    {
        "sale_fee.price_context_mismatch",
        "seller_shipping.price_context_mismatch",
        "sale_fee.context_fingerprint_mismatch",
        "seller_shipping.context_fingerprint_mismatch",
    }
)


def _registrar_classificacao(classification: str) -> str:
    if classification not in _COUNTERS:
        classification = _SHADOW_ERROR
    with _COUNTERS_LOCK:
        _COUNTERS[classification] += 1
    return classification


def registrar_erro_shadow(*, origem: str | None = None) -> str:
    """Record one content-free shadow failure and return its classification."""

    del origem
    return _registrar_classificacao(_SHADOW_ERROR)


def snapshot_contadores_shadow() -> dict[str, int]:
    """Return a detached snapshot containing aggregate counters only."""

    with _COUNTERS_LOCK:
        return {
            classification: _COUNTERS[classification]
            for classification in _CLASSIFICATIONS
        }


def reset_contadores_shadow() -> None:
    """Reset aggregate counters, primarily for isolated tests."""

    with _COUNTERS_LOCK:
        for classification in _CLASSIFICATIONS:
            _COUNTERS[classification] = 0


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TypeError("boolean is not a monetary value")
    return Decimal(str(value))


def _decimal_fingerprint_token(value: Decimal | None) -> object:
    if value is None:
        return None
    if not value.is_finite():
        return {"decimal_non_finite": str(value)}
    sign, digits, exponent = value.as_tuple()
    digits_list = list(digits)
    if not any(digits_list):
        return {"decimal": [0, "0", 0]}
    while digits_list and digits_list[-1] == 0:
        digits_list.pop()
        exponent += 1
    return {
        "decimal": [
            int(sign),
            "".join(str(digit) for digit in digits_list),
            int(exponent),
        ]
    }


def _canonicalizar_contexto(value: Any, active_containers: set[int]) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Enum):
        return _canonicalizar_contexto(value.value, active_containers)
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return _decimal_fingerprint_token(Decimal(str(value)))
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("cyclic financial context")
        active_containers.add(container_id)
        try:
            items = []
            for key in sorted(value):
                if not isinstance(key, str):
                    raise TypeError("financial context keys must be strings")
                items.append(
                    [key, _canonicalizar_contexto(value[key], active_containers)]
                )
            return {"mapping": items}
        finally:
            active_containers.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError("cyclic financial context")
        active_containers.add(container_id)
        try:
            return {
                "sequence": [
                    _canonicalizar_contexto(item, active_containers)
                    for item in value
                ]
            }
        finally:
            active_containers.remove(container_id)
    raise TypeError("unsupported financial context value")


def _fingerprint_contexto(
    *,
    preco: Decimal | None,
    custo: Decimal | None,
    imposto_rate: Decimal | None,
    tarifa_preco_contexto: Decimal | None,
    frete_preco_contexto: Decimal | None,
    contexto_financeiro: Any,
) -> str:
    payload = {
        "contract": "mercadolivre.financial_quote_shadow_context.v1",
        "effective_price": _decimal_fingerprint_token(preco),
        "product_cost": _decimal_fingerprint_token(custo),
        "tax_rate": _decimal_fingerprint_token(imposto_rate),
        "sale_fee_price_context": _decimal_fingerprint_token(
            tarifa_preco_contexto
        ),
        "seller_shipping_price_context": _decimal_fingerprint_token(
            frete_preco_contexto
        ),
        "business_context": _canonicalizar_contexto(
            contexto_financeiro,
            set(),
        ),
    }
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be boolean")
    return value


def _evidence_state(
    exact: bool,
    amount: Decimal | None,
    *,
    fallback: bool = False,
) -> MonetaryEvidenceState:
    if exact:
        return MonetaryEvidenceState.CONTEXTUAL
    if amount is None:
        return MonetaryEvidenceState.UNAVAILABLE
    if fallback:
        return MonetaryEvidenceState.FALLBACK
    return MonetaryEvidenceState.ESTIMATED


def _legacy_explicit_exact(resultado_legado: dict[str, Any]) -> bool | None:
    explicit_values = []
    for key in _LEGACY_EXACT_KEYS:
        if key not in resultado_legado or resultado_legado[key] is None:
            continue
        value = resultado_legado[key]
        if not isinstance(value, bool):
            raise TypeError("legacy exactness must be boolean")
        explicit_values.append(value)
    if not explicit_values:
        return None
    if any(value != explicit_values[0] for value in explicit_values[1:]):
        raise ValueError("conflicting legacy exactness")
    return explicit_values[0]


def _legacy_decimal(
    resultado_legado: dict[str, Any],
    aliases: tuple[str, ...],
) -> Decimal | None:
    values = []
    for key in aliases:
        if key not in resultado_legado or resultado_legado[key] in (None, ""):
            continue
        parsed = _decimal_or_none(resultado_legado[key])
        if parsed is None or not parsed.is_finite():
            raise ValueError("invalid legacy financial value")
        values.append(parsed)
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise ValueError("conflicting legacy financial values")
    return values[0]


def _legacy_financial_values(
    resultado_legado: Any,
    *,
    tarifa_exata: bool,
    frete_exato: bool,
) -> tuple[bool, Decimal | None, Decimal | None, Decimal | None]:
    if resultado_legado is None:
        return False, None, None, None
    if not isinstance(resultado_legado, dict):
        raise TypeError("legacy result must be a mapping")

    explicit_exact = _legacy_explicit_exact(resultado_legado)
    legacy_tax = _legacy_decimal(resultado_legado, _LEGACY_TAX_KEYS)
    legacy_net = _legacy_decimal(resultado_legado, _LEGACY_NET_KEYS)
    legacy_margin = _legacy_decimal(resultado_legado, _LEGACY_MARGIN_KEYS)
    values_complete = legacy_net is not None and legacy_margin is not None
    if explicit_exact is False:
        return False, legacy_tax, legacy_net, legacy_margin
    if explicit_exact is True:
        if not values_complete:
            raise ValueError("exact legacy result is missing financial values")
        return True, legacy_tax, legacy_net, legacy_margin
    inferred_exact = bool(tarifa_exata and frete_exato and values_complete)
    return inferred_exact, legacy_tax, legacy_net, legacy_margin


def _quantize_for_comparison(value: Decimal, quantum: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        for signal in context.traps:
            context.traps[signal] = False
        context.traps[InvalidOperation] = True
        context.traps[DivisionByZero] = True
        context.traps[Overflow] = True
        result = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if not result.is_finite():
        raise ValueError("non-finite rounded legacy value")
    return result


def _classificar_valores(
    *,
    quote: Any,
    legacy_tax: Decimal | None,
    legacy_net: Decimal,
    legacy_margin: Decimal,
) -> str:
    if quote.net_amount is None or quote.margin_percent is None:
        raise ValueError("exact shadow quote is missing financial values")

    tax_matches = legacy_tax is None or legacy_tax == quote.tax_amount
    if (
        tax_matches
        and legacy_net == quote.net_amount
        and legacy_margin == quote.margin_percent
    ):
        return _MATCH

    rounded_tax_matches = (
        legacy_tax is None
        or (
            quote.tax_amount is not None
            and _quantize_for_comparison(legacy_tax, MONEY_QUANTUM)
            == quote.tax_amount
        )
    )
    rounded_net_matches = (
        _quantize_for_comparison(legacy_net, MONEY_QUANTUM) == quote.net_amount
    )
    rounded_margin_matches = (
        quote.decision_margin_percent is not None
        and _quantize_for_comparison(legacy_margin, MARGIN_DECISION_QUANTUM)
        == quote.decision_margin_percent
    )
    if rounded_tax_matches and rounded_net_matches and rounded_margin_matches:
        return _ROUNDING_DIFFERENCE
    return _VALUE_DIFFERENCE


def _invalid_quote_is_exactness_difference(quote: Any) -> bool:
    conflicts = getattr(quote, "conflicts", ())
    return bool(
        conflicts
        and all(conflict in _EXACTNESS_ONLY_CONFLICTS for conflict in conflicts)
    )


def _delta(new_value: Decimal | None, legacy_value: Decimal | None) -> Decimal | None:
    if new_value is None or legacy_value is None:
        return None
    return new_value - legacy_value


def _comparison_payload(
    *,
    origin: str,
    classification: str,
    quote: Any,
    legacy_exact: bool,
    legacy_tax: Decimal | None,
    legacy_net: Decimal | None,
    legacy_margin: Decimal | None,
    effective_price: Decimal | None,
    product_cost: Decimal | None,
    tax_rate: Decimal | None,
    sale_fee: Decimal | None,
    seller_shipping: Decimal | None,
    sale_fee_state: MonetaryEvidenceState,
    seller_shipping_state: MonetaryEvidenceState,
    sale_fee_source: Any,
    seller_shipping_source: Any,
) -> dict[str, Any]:
    return {
        "origin": origin,
        "classification": classification,
        "inputs": {
            "effective_price": effective_price,
            "product_cost": product_cost,
            "tax_rate": tax_rate,
            "sale_fee": sale_fee,
            "seller_shipping": seller_shipping,
        },
        "evidence": {
            "sale_fee_state": sale_fee_state.value,
            "seller_shipping_state": seller_shipping_state.value,
            "sale_fee_source_present": bool(str(sale_fee_source or "").strip()),
            "seller_shipping_source_present": bool(
                str(seller_shipping_source or "").strip()
            ),
        },
        "legacy": {
            "status": "exact" if legacy_exact else "incomplete",
            "tax_amount": legacy_tax,
            "net_amount": legacy_net,
            "margin_percent": legacy_margin,
        },
        "quote": {
            "status": quote.status.value,
            "decision_eligible": bool(quote.decision_eligible),
            "tax_amount": quote.tax_amount,
            "net_amount": quote.net_amount,
            "margin_percent": quote.margin_percent,
        },
        "delta": {
            "tax_amount": _delta(quote.tax_amount, legacy_tax),
            "net_amount": _delta(quote.net_amount, legacy_net),
            "margin_percent": _delta(quote.margin_percent, legacy_margin),
        },
    }


def _observar_cotacao_financeira(
    *,
    origem: str,
    preco_efetivo: Any,
    custo_produto: Any,
    aliquota_imposto: Any,
    tarifa_total: Any,
    tarifa_exata: bool,
    tarifa_fonte: Any,
    tarifa_contexto_preco: Any,
    frete_vendedor: Any,
    frete_exato: bool,
    frete_fonte: Any,
    frete_contexto_preco: Any,
    contexto_financeiro: Any,
    resultado_legado: Any,
    currency_id: Any,
    tarifa_fallback: bool,
    frete_fallback: bool,
) -> tuple[str, dict[str, Any]]:
    origin = str(origem or "").strip().lower()
    if origin not in _ORIGINS:
        raise ValueError("invalid financial quote origin")
    tarifa_exata_bool = _require_bool(tarifa_exata, "tarifa_exata")
    frete_exato_bool = _require_bool(frete_exato, "frete_exato")
    tarifa_fallback_bool = _require_bool(tarifa_fallback, "tarifa_fallback")
    frete_fallback_bool = _require_bool(frete_fallback, "frete_fallback")
    preco_decimal = _decimal_or_none(preco_efetivo)
    custo_decimal = _decimal_or_none(custo_produto)
    imposto_decimal = _decimal_or_none(aliquota_imposto)
    tarifa_decimal = _decimal_or_none(tarifa_total)
    tarifa_contexto_decimal = _decimal_or_none(tarifa_contexto_preco)
    frete_decimal = _decimal_or_none(frete_vendedor)
    frete_contexto_decimal = _decimal_or_none(frete_contexto_preco)
    sale_fee_state = _evidence_state(
        tarifa_exata_bool,
        tarifa_decimal,
        fallback=tarifa_fallback_bool,
    )
    seller_shipping_state = _evidence_state(
        frete_exato_bool,
        frete_decimal,
        fallback=frete_fallback_bool,
    )

    fingerprint = _fingerprint_contexto(
        preco=preco_decimal,
        custo=custo_decimal,
        imposto_rate=imposto_decimal,
        tarifa_preco_contexto=tarifa_contexto_decimal,
        frete_preco_contexto=frete_contexto_decimal,
        contexto_financeiro=contexto_financeiro,
    )
    request = FinancialQuoteRequest(
        effective_price=preco_decimal,
        product_cost=custo_decimal,
        tax_rate=imposto_decimal,
        sale_fee=MonetaryEvidence(
            amount=tarifa_decimal,
            state=sale_fee_state,
            role=MonetaryEvidenceRole.TOTAL_SALE_FEE,
            source=tarifa_fonte,
            price_context=tarifa_contexto_decimal,
            context_fingerprint=fingerprint,
        ),
        seller_shipping=MonetaryEvidence(
            amount=frete_decimal,
            state=seller_shipping_state,
            role=MonetaryEvidenceRole.SELLER_SHIPPING_NET,
            source=frete_fonte,
            price_context=frete_contexto_decimal,
            context_fingerprint=fingerprint,
        ),
        currency_id=currency_id,
        context_fingerprint=fingerprint,
    )
    quote = calculate_quote(request)
    legacy_exact, legacy_tax, legacy_net, legacy_margin = (
        _legacy_financial_values(
            resultado_legado,
            tarifa_exata=tarifa_exata_bool,
            frete_exato=frete_exato_bool,
        )
    )

    invalid_por_exatidao = bool(
        quote.status is FinancialQuoteStatus.INVALID
        and _invalid_quote_is_exactness_difference(quote)
    )
    if quote.status is FinancialQuoteStatus.INVALID and not invalid_por_exatidao:
        classification = _SHADOW_ERROR
    else:
        shadow_exact = quote.status is FinancialQuoteStatus.EXACT
        if shadow_exact != legacy_exact:
            classification = _EXACTNESS_DIFFERENCE
        elif not shadow_exact:
            classification = _SHADOW_INCOMPLETE
        elif legacy_net is None or legacy_margin is None:
            classification = _SHADOW_ERROR
        else:
            classification = _classificar_valores(
                quote=quote,
                legacy_tax=legacy_tax,
                legacy_net=legacy_net,
                legacy_margin=legacy_margin,
            )

    comparison = _comparison_payload(
        origin=origin,
        classification=classification,
        quote=quote,
        legacy_exact=legacy_exact,
        legacy_tax=legacy_tax,
        legacy_net=legacy_net,
        legacy_margin=legacy_margin,
        effective_price=preco_decimal,
        product_cost=custo_decimal,
        tax_rate=imposto_decimal,
        sale_fee=tarifa_decimal,
        seller_shipping=frete_decimal,
        sale_fee_state=sale_fee_state,
        seller_shipping_state=seller_shipping_state,
        sale_fee_source=tarifa_fonte,
        seller_shipping_source=frete_fonte,
    )
    return classification, comparison


def observar_cotacao_financeira(
    *,
    origem: str,
    preco_efetivo: Any,
    custo_produto: Any,
    aliquota_imposto: Any,
    tarifa_total: Any,
    tarifa_exata: bool,
    tarifa_fonte: Any,
    tarifa_contexto_preco: Any,
    frete_vendedor: Any,
    frete_exato: bool,
    frete_fonte: Any,
    frete_contexto_preco: Any,
    contexto_financeiro: Any,
    resultado_legado: Any,
    currency_id: Any = "BRL",
    tarifa_fallback: bool = False,
    frete_fallback: bool = False,
    client_id: Any = None,
    loja: Any = None,
) -> str:
    """Observe one legacy result and return a content-free classification."""

    try:
        classification, comparison = _observar_cotacao_financeira(
            origem=origem,
            preco_efetivo=preco_efetivo,
            custo_produto=custo_produto,
            aliquota_imposto=aliquota_imposto,
            tarifa_total=tarifa_total,
            tarifa_exata=tarifa_exata,
            tarifa_fonte=tarifa_fonte,
            tarifa_contexto_preco=tarifa_contexto_preco,
            frete_vendedor=frete_vendedor,
            frete_exato=frete_exato,
            frete_fonte=frete_fonte,
            frete_contexto_preco=frete_contexto_preco,
            contexto_financeiro=contexto_financeiro,
            resultado_legado=resultado_legado,
            currency_id=currency_id,
            tarifa_fallback=tarifa_fallback,
            frete_fallback=frete_fallback,
        )
    except Exception:
        return registrar_erro_shadow(origem=origem)

    recorded = _registrar_classificacao(classification)
    try:
        from backend.services.mercadolivre_cotacao_comparacao import (
            capturar_observacao_comparacao,
        )

        capturar_observacao_comparacao(
            client_id=client_id,
            loja=loja,
            observacao=comparison,
        )
    except Exception:
        pass
    return recorded


__all__ = [
    "observar_cotacao_financeira",
    "registrar_erro_shadow",
    "reset_contadores_shadow",
    "snapshot_contadores_shadow",
]
