"""Pure, versioned financial quote contract for Mercado Livre.

This module intentionally has no network, filesystem, routing or logging
dependencies.  Callers must resolve the effective sale fee and seller shipping
before building the request.  ``sale_fee.amount`` is the total sale fee: this
core never adds fixed, percentage or financing components to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_UP,
    localcontext,
)
from enum import Enum
from typing import Final


FINANCIAL_QUOTE_CONTRACT_VERSION: Final = "mercadolivre.financial_quote.v1"
PRICE_CONTEXT_TOLERANCE: Final = Decimal("0.00")
MONEY_QUANTUM: Final = Decimal("0.01")
MARGIN_PERCENT_QUANTUM: Final = Decimal("0.000001")
MARGIN_DECISION_QUANTUM: Final = Decimal("0.01")


class MonetaryEvidenceState(str, Enum):
    """Allowed provenance states for a monetary amount."""

    CONFIRMED = "confirmed"
    CONTEXTUAL = "contextual"
    ESTIMATED = "estimated"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"


class MonetaryEvidenceRole(str, Enum):
    """Economic role of the terminal value consumed by the calculation."""

    TOTAL_SALE_FEE = "total_sale_fee"
    SELLER_SHIPPING_NET = "seller_shipping_net"


class FinancialQuoteStatus(str, Enum):
    """Completeness state of a financial quote."""

    EXACT = "exact"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class MonetaryEvidence:
    """A monetary amount plus the evidence that binds it to a price."""

    amount: Decimal | None
    state: MonetaryEvidenceState | str
    role: MonetaryEvidenceRole | str
    source: str
    price_context: Decimal | None
    context_fingerprint: str


@dataclass(frozen=True, slots=True)
class FinancialQuoteRequest:
    """Strict input contract for one BRL financial quote."""

    effective_price: Decimal
    product_cost: Decimal
    tax_rate: Decimal
    sale_fee: MonetaryEvidence
    seller_shipping: MonetaryEvidence
    currency_id: str
    context_fingerprint: str
    contract_version: str = FINANCIAL_QUOTE_CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class FinancialQuoteSources:
    """Sources used for the two externally resolved monetary components."""

    sale_fee: str = ""
    seller_shipping: str = ""


@dataclass(frozen=True, slots=True)
class FinancialQuoteResult:
    """Versioned, fail-closed result returned by :func:`calculate_quote`.

    ``decision_eligible`` means only that every financial input is exact enough
    to feed a later business decision.  It never means that the resulting
    margin satisfies a store or campaign threshold.
    """

    contract_version: str
    status: FinancialQuoteStatus
    decision_eligible: bool
    tax_amount: Decimal | None
    net_amount: Decimal | None
    margin_percent: Decimal | None
    decision_margin_percent: Decimal | None
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]
    sources: FinancialQuoteSources
    context_fingerprint: str


def _append_unique(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _source_text(evidence: object) -> str:
    if not isinstance(evidence, MonetaryEvidence) or not isinstance(evidence.source, str):
        return ""
    return evidence.source.strip()


def _result(
    status: FinancialQuoteStatus,
    *,
    missing: list[str] | tuple[str, ...] = (),
    conflicts: list[str] | tuple[str, ...] = (),
    sources: FinancialQuoteSources | None = None,
    tax_amount: Decimal | None = None,
    net_amount: Decimal | None = None,
    margin_percent: Decimal | None = None,
    decision_margin_percent: Decimal | None = None,
    context_fingerprint: str = "",
) -> FinancialQuoteResult:
    return FinancialQuoteResult(
        contract_version=FINANCIAL_QUOTE_CONTRACT_VERSION,
        status=status,
        decision_eligible=status is FinancialQuoteStatus.EXACT,
        tax_amount=tax_amount,
        net_amount=net_amount,
        margin_percent=margin_percent,
        decision_margin_percent=decision_margin_percent,
        missing=tuple(missing),
        conflicts=tuple(conflicts),
        sources=sources or FinancialQuoteSources(),
        context_fingerprint=context_fingerprint,
    )


def _validate_required_decimal(
    value: object,
    field: str,
    missing: list[str],
    conflicts: list[str],
    *,
    positive: bool = False,
) -> Decimal | None:
    if value is None:
        _append_unique(missing, field)
        return None
    if not isinstance(value, Decimal):
        _append_unique(conflicts, f"{field}.type")
        return None
    if not value.is_finite():
        _append_unique(conflicts, f"{field}.non_finite")
        return None
    if positive and value <= 0:
        _append_unique(conflicts, f"{field}.non_positive")
        return None
    if not positive and value < 0:
        _append_unique(conflicts, f"{field}.negative")
        return None
    return value


def _normalize_evidence_state(value: object) -> MonetaryEvidenceState | None:
    if isinstance(value, MonetaryEvidenceState):
        return value
    if isinstance(value, str):
        try:
            return MonetaryEvidenceState(value)
        except ValueError:
            return None
    return None


def _normalize_evidence_role(value: object) -> MonetaryEvidenceRole | None:
    if isinstance(value, MonetaryEvidenceRole):
        return value
    if isinstance(value, str):
        try:
            return MonetaryEvidenceRole(value)
        except ValueError:
            return None
    return None


def _validate_evidence(
    evidence: object,
    field: str,
    effective_price: Decimal | None,
    expected_role: MonetaryEvidenceRole,
    expected_context_fingerprint: str,
    missing: list[str],
    conflicts: list[str],
    *,
    allow_negative: bool = False,
) -> tuple[Decimal | None, bool]:
    if evidence is None:
        _append_unique(missing, field)
        return None, False
    if not isinstance(evidence, MonetaryEvidence):
        _append_unique(conflicts, f"{field}.type")
        return None, False

    state = _normalize_evidence_state(evidence.state)
    if state is None:
        _append_unique(conflicts, f"{field}.state")
        return None, False

    role = _normalize_evidence_role(evidence.role)
    if role is None:
        _append_unique(conflicts, f"{field}.role")
    elif role is not expected_role:
        _append_unique(conflicts, f"{field}.role_mismatch")

    evidence_fingerprint = (
        evidence.context_fingerprint.strip()
        if isinstance(evidence.context_fingerprint, str)
        else ""
    )
    if not isinstance(evidence.context_fingerprint, str):
        _append_unique(conflicts, f"{field}.context_fingerprint_type")
    elif not evidence_fingerprint:
        _append_unique(missing, f"{field}.context_fingerprint")
    elif (
        expected_context_fingerprint
        and evidence_fingerprint != expected_context_fingerprint
    ):
        _append_unique(conflicts, f"{field}.context_fingerprint_mismatch")

    source_valid = isinstance(evidence.source, str)
    source = evidence.source.strip() if source_valid else ""
    if not source_valid:
        _append_unique(conflicts, f"{field}.source_type")

    amount = evidence.amount
    if amount is not None:
        if not isinstance(amount, Decimal):
            _append_unique(conflicts, f"{field}.amount_type")
            amount = None
        elif not amount.is_finite():
            _append_unique(conflicts, f"{field}.amount_non_finite")
            amount = None
        elif amount < 0 and not allow_negative:
            _append_unique(conflicts, f"{field}.amount_negative")
            amount = None

    price_context = evidence.price_context
    if price_context is not None:
        if not isinstance(price_context, Decimal):
            _append_unique(conflicts, f"{field}.price_context_type")
            price_context = None
        elif not price_context.is_finite() or price_context <= 0:
            _append_unique(conflicts, f"{field}.price_context_invalid")
            price_context = None
        elif (
            effective_price is not None
            and abs(price_context - effective_price) > PRICE_CONTEXT_TOLERANCE
        ):
            _append_unique(conflicts, f"{field}.price_context_mismatch")

    if state is MonetaryEvidenceState.CONFLICT:
        _append_unique(conflicts, field)
        return None, False

    if state is MonetaryEvidenceState.UNAVAILABLE:
        if evidence.amount is not None:
            _append_unique(conflicts, f"{field}.unavailable_with_amount")
        else:
            _append_unique(missing, field)
        return None, False

    if amount is None:
        if not any(item.startswith(f"{field}.amount_") for item in conflicts):
            _append_unique(missing, f"{field}.amount")
        return None, False

    if not source:
        _append_unique(missing, f"{field}.source")

    if state in {MonetaryEvidenceState.ESTIMATED, MonetaryEvidenceState.FALLBACK}:
        _append_unique(missing, f"{field}.exact_evidence")
        return amount, False

    if price_context is None:
        if not any(item.startswith(f"{field}.price_context_") for item in conflicts):
            _append_unique(missing, f"{field}.price_context")
        return amount, False

    context_matches = (
        effective_price is not None
        and abs(price_context - effective_price) <= PRICE_CONTEXT_TOLERANCE
    )
    exact = bool(
        state in {MonetaryEvidenceState.CONFIRMED, MonetaryEvidenceState.CONTEXTUAL}
        and role is expected_role
        and source
        and context_matches
        and evidence_fingerprint
        and evidence_fingerprint == expected_context_fingerprint
    )
    return amount, exact


def calculate_quote(request: FinancialQuoteRequest) -> FinancialQuoteResult:
    """Calculate a deterministic BRL quote without resolving external data.

    Only ``confirmed`` and ``contextual`` fee/shipping evidence can produce an
    exact quote.  Explicit zero amounts are valid.  Missing, estimated or
    fallback evidence returns an incomplete result; contradictory or malformed
    data returns an invalid result.  No partial financial values are exposed
    when the quote is not exact.
    """

    if not isinstance(request, FinancialQuoteRequest):
        return _result(FinancialQuoteStatus.INVALID, conflicts=("request.type",))

    missing: list[str] = []
    conflicts: list[str] = []
    sources = FinancialQuoteSources(
        sale_fee=_source_text(request.sale_fee),
        seller_shipping=_source_text(request.seller_shipping),
    )
    context_fingerprint = (
        request.context_fingerprint.strip()
        if isinstance(request.context_fingerprint, str)
        else ""
    )

    if request.contract_version != FINANCIAL_QUOTE_CONTRACT_VERSION:
        _append_unique(conflicts, "contract_version")
    if not isinstance(request.currency_id, str) or request.currency_id != "BRL":
        _append_unique(conflicts, "currency_id")
    if not isinstance(request.context_fingerprint, str):
        _append_unique(conflicts, "context_fingerprint.type")
    elif not context_fingerprint:
        _append_unique(missing, "context_fingerprint")

    effective_price = _validate_required_decimal(
        request.effective_price,
        "effective_price",
        missing,
        conflicts,
        positive=True,
    )
    product_cost = _validate_required_decimal(
        request.product_cost,
        "product_cost",
        missing,
        conflicts,
    )
    tax_rate = _validate_required_decimal(
        request.tax_rate,
        "tax_rate",
        missing,
        conflicts,
    )
    if tax_rate is not None and tax_rate > 1:
        _append_unique(conflicts, "tax_rate.out_of_range")
        tax_rate = None

    sale_fee, sale_fee_exact = _validate_evidence(
        request.sale_fee,
        "sale_fee",
        effective_price,
        MonetaryEvidenceRole.TOTAL_SALE_FEE,
        context_fingerprint,
        missing,
        conflicts,
    )
    seller_shipping, seller_shipping_exact = _validate_evidence(
        request.seller_shipping,
        "seller_shipping",
        effective_price,
        MonetaryEvidenceRole.SELLER_SHIPPING_NET,
        context_fingerprint,
        missing,
        conflicts,
        allow_negative=True,
    )

    if conflicts:
        return _result(
            FinancialQuoteStatus.INVALID,
            missing=missing,
            conflicts=conflicts,
            sources=sources,
            context_fingerprint=context_fingerprint,
        )
    if (
        missing
        or effective_price is None
        or product_cost is None
        or tax_rate is None
        or sale_fee is None
        or seller_shipping is None
        or not sale_fee_exact
        or not seller_shipping_exact
    ):
        return _result(
            FinancialQuoteStatus.INCOMPLETE,
            missing=missing,
            sources=sources,
            context_fingerprint=context_fingerprint,
        )

    try:
        calculation_context = Context(
            prec=50,
            rounding=ROUND_HALF_UP,
            Emin=-999999,
            Emax=999999,
            capitals=1,
            clamp=0,
        )
        # Build the complete trap policy instead of inheriting any Decimal
        # settings from the caller.  This keeps identical requests
        # deterministic even when a surrounding workflow changes its context.
        for signal in calculation_context.traps:
            calculation_context.traps[signal] = False
        calculation_context.traps[InvalidOperation] = True
        calculation_context.traps[DivisionByZero] = True
        calculation_context.traps[Overflow] = True
        with localcontext(calculation_context):
            tax_amount = (effective_price * tax_rate).quantize(
                MONEY_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            net_amount = (
                effective_price
                - product_cost
                - tax_amount
                - sale_fee
                - seller_shipping
            ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            raw_margin_percent = net_amount * Decimal("100") / effective_price
            margin_percent = raw_margin_percent.quantize(
                MARGIN_PERCENT_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            decision_margin_percent = raw_margin_percent.quantize(
                MARGIN_DECISION_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
    except DecimalException:
        return _result(
            FinancialQuoteStatus.INVALID,
            conflicts=("calculation",),
            sources=sources,
            context_fingerprint=context_fingerprint,
        )

    if not all(
        value.is_finite()
        for value in (tax_amount, net_amount, margin_percent)
    ):
        return _result(
            FinancialQuoteStatus.INVALID,
            conflicts=("calculation.non_finite",),
            sources=sources,
            context_fingerprint=context_fingerprint,
        )

    return _result(
        FinancialQuoteStatus.EXACT,
        sources=sources,
        tax_amount=tax_amount,
        net_amount=net_amount,
        margin_percent=margin_percent,
        decision_margin_percent=decision_margin_percent,
        context_fingerprint=context_fingerprint,
    )


__all__ = [
    "FINANCIAL_QUOTE_CONTRACT_VERSION",
    "PRICE_CONTEXT_TOLERANCE",
    "MONEY_QUANTUM",
    "MARGIN_PERCENT_QUANTUM",
    "MARGIN_DECISION_QUANTUM",
    "MonetaryEvidenceState",
    "MonetaryEvidenceRole",
    "FinancialQuoteStatus",
    "MonetaryEvidence",
    "FinancialQuoteRequest",
    "FinancialQuoteSources",
    "FinancialQuoteResult",
    "calculate_quote",
]
