from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext

import pytest

from backend.services.favoritos_margem import margem_calcular_anuncio
from backend.services.mercadolivre_cotacao_financeira import (
    FINANCIAL_QUOTE_CONTRACT_VERSION,
    FinancialQuoteRequest,
    FinancialQuoteStatus,
    MonetaryEvidence,
    MonetaryEvidenceRole,
    MonetaryEvidenceState,
    calculate_quote,
)
from backend.services.promocoes_core_analise import _calcular_margem_liquida_ml


D = Decimal


def _evidence(
    amount,
    *,
    state=MonetaryEvidenceState.CONTEXTUAL,
    source="official-api",
    price_context="100.00",
    role=MonetaryEvidenceRole.TOTAL_SALE_FEE,
    context_fingerprint="quote-context",
):
    return MonetaryEvidence(
        amount=None if amount is None else D(amount),
        state=state,
        role=role,
        source=source,
        price_context=None if price_context is None else D(price_context),
        context_fingerprint=context_fingerprint,
    )


def _request(
    *,
    price="100.00",
    cost="30.00",
    tax_rate="0.10",
    fee="10.00",
    shipping="5.00",
    fee_state=MonetaryEvidenceState.CONTEXTUAL,
    shipping_state=MonetaryEvidenceState.CONTEXTUAL,
    fee_source="sites/MLB/listing_prices",
    shipping_source="users/shipping_options/free",
    fee_role=MonetaryEvidenceRole.TOTAL_SALE_FEE,
    shipping_role=MonetaryEvidenceRole.SELLER_SHIPPING_NET,
    fee_price_context=None,
    shipping_price_context=None,
    context_fingerprint="quote-context",
    fee_context_fingerprint=None,
    shipping_context_fingerprint=None,
    currency_id="BRL",
    contract_version=FINANCIAL_QUOTE_CONTRACT_VERSION,
):
    effective_price = D(price) if isinstance(price, str) else price
    default_context = str(price) if isinstance(price, str) else price
    return FinancialQuoteRequest(
        effective_price=effective_price,
        product_cost=D(cost) if isinstance(cost, str) else cost,
        tax_rate=D(tax_rate) if isinstance(tax_rate, str) else tax_rate,
        sale_fee=MonetaryEvidence(
            amount=D(fee) if isinstance(fee, str) else fee,
            state=fee_state,
            role=fee_role,
            source=fee_source,
            price_context=(
                D(default_context)
                if fee_price_context is None and isinstance(default_context, str)
                else default_context
                if fee_price_context is None
                else D(fee_price_context)
                if isinstance(fee_price_context, str)
                else fee_price_context
            ),
            context_fingerprint=(
                context_fingerprint
                if fee_context_fingerprint is None
                else fee_context_fingerprint
            ),
        ),
        seller_shipping=MonetaryEvidence(
            amount=D(shipping) if isinstance(shipping, str) else shipping,
            state=shipping_state,
            role=shipping_role,
            source=shipping_source,
            price_context=(
                D(default_context)
                if shipping_price_context is None and isinstance(default_context, str)
                else default_context
                if shipping_price_context is None
                else D(shipping_price_context)
                if isinstance(shipping_price_context, str)
                else shipping_price_context
            ),
            context_fingerprint=(
                context_fingerprint
                if shipping_context_fingerprint is None
                else shipping_context_fingerprint
            ),
        ),
        currency_id=currency_id,
        context_fingerprint=context_fingerprint,
        contract_version=contract_version,
    )


def test_exact_quote_uses_total_fee_once_and_keeps_sources():
    result = calculate_quote(
        _request(
            price="80.00",
            cost="20.00",
            tax_rate="0.10",
            fee="14.77",
            shipping="18.85",
        )
    )

    assert result.status is FinancialQuoteStatus.EXACT
    assert result.decision_eligible is True
    assert result.tax_amount == D("8.00")
    assert result.net_amount == D("18.38")
    assert result.margin_percent == D("22.975000")
    assert result.decision_margin_percent == D("22.98")
    assert result.missing == ()
    assert result.conflicts == ()
    assert result.sources.sale_fee == "sites/MLB/listing_prices"
    assert result.sources.seller_shipping == "users/shipping_options/free"
    assert result.context_fingerprint == "quote-context"


def test_confirmed_evidence_is_also_exact_for_the_bound_price():
    result = calculate_quote(
        _request(
            fee_state=MonetaryEvidenceState.CONFIRMED,
            shipping_state=MonetaryEvidenceState.CONFIRMED,
        )
    )

    assert result.status is FinancialQuoteStatus.EXACT
    assert result.decision_eligible is True


def test_total_sale_fee_is_not_rebuilt_or_charged_twice():
    result = calculate_quote(
        _request(
            price="100.00",
            cost="30.00",
            tax_rate="0.10",
            fee="25.22",
            shipping="8.00",
        )
    )

    assert result.net_amount == D("26.78")
    assert result.margin_percent == D("26.780000")
    assert result.decision_margin_percent == D("26.78")


def test_decimal_cent_rounding_matches_favoritos_and_characterizes_promocoes():
    result = calculate_quote(
        _request(
            price="99.99",
            cost="31.11",
            tax_rate="0.0725",
            fee="13.27",
            shipping="8.54",
        )
    )
    favoritos = margem_calcular_anuncio(
        {
            "price": "99.99",
            "sale_fee_amount": "13.27",
            "shipping_seller_cost": "8.54",
            "currency_id": "BRL",
        },
        custo="31.11",
        imposto_rate="7.25",
        require_shipping=True,
    )
    promocoes = _calcular_margem_liquida_ml(
        "99.99",
        "31.11",
        "0.0725",
        "13.27",
        "8.54",
    )

    assert result.tax_amount == D("7.25")
    assert result.net_amount == D("39.82")
    assert result.margin_percent == D("39.823982")
    assert result.decision_margin_percent == D("39.82")
    assert favoritos["imposto_valor"] == 7.25
    assert favoritos["valor_liquido"] == 39.82
    assert favoritos["margem_percentual"] == 39.82
    assert D(str(promocoes["imposto"])).quantize(D("0.01")) == result.tax_amount
    assert D(str(promocoes["valor_liquido"])).quantize(D("0.01")) == result.net_amount
    assert promocoes["margem_percentual"] != float(result.margin_percent)


def test_rounding_near_margin_threshold_is_explicitly_characterized():
    result = calculate_quote(
        _request(
            price="10.01",
            cost="5.00",
            tax_rate="0.0725",
            fee="2.00",
            shipping="0.78",
        )
    )
    favoritos = margem_calcular_anuncio(
        {
            "price": "10.01",
            "sale_fee_amount": "2.00",
            "shipping_seller_cost": "0.78",
            "currency_id": "BRL",
        },
        custo="5.00",
        imposto_rate="7.25",
        require_shipping=True,
    )
    promocoes = _calcular_margem_liquida_ml(
        "10.01",
        "5.00",
        "0.0725",
        "2.00",
        "0.78",
    )

    assert result.tax_amount == D("0.73")
    assert result.net_amount == D("1.50")
    assert result.margin_percent == D("14.985015")
    assert result.decision_margin_percent == D("14.99")
    assert favoritos["margem_percentual"] == 14.99
    assert promocoes["margem_percentual"] > 15


def test_decision_margin_rounds_directly_without_six_decimal_double_rounding():
    result = calculate_quote(
        _request(
            price="200.02",
            cost="200.01",
            tax_rate="0.00",
            fee="0.00",
            shipping="0.00",
        )
    )

    assert result.net_amount == D("0.01")
    assert result.margin_percent == D("0.005000")
    assert result.decision_margin_percent == D("0.00")


def test_round_half_up_is_used_for_half_cent_tax():
    result = calculate_quote(
        _request(
            price="0.05",
            cost="0.00",
            tax_rate="0.10",
            fee="0.00",
            shipping="0.00",
        )
    )

    assert result.tax_amount == D("0.01")
    assert result.net_amount == D("0.04")
    assert result.margin_percent == D("80.000000")
    assert result.decision_margin_percent == D("80.00")


def test_explicit_zero_components_are_exact_and_missing_is_not_zero():
    exact = calculate_quote(
        _request(cost="0.00", tax_rate="0.00", fee="0.00", shipping="0.00")
    )
    missing = calculate_quote(
        FinancialQuoteRequest(
            effective_price=D("100.00"),
            product_cost=D("0.00"),
            tax_rate=D("0.00"),
            sale_fee=_evidence("0.00"),
            seller_shipping=_evidence(
                None,
                state="unavailable",
                role=MonetaryEvidenceRole.SELLER_SHIPPING_NET,
            ),
            currency_id="BRL",
            context_fingerprint="quote-context",
        )
    )

    assert exact.status is FinancialQuoteStatus.EXACT
    assert exact.net_amount == D("100.00")
    assert exact.margin_percent == D("100.000000")
    assert missing.status is FinancialQuoteStatus.INCOMPLETE
    assert missing.decision_eligible is False
    assert "seller_shipping" in missing.missing
    assert missing.net_amount is None
    assert missing.margin_percent is None
    assert missing.decision_margin_percent is None


@pytest.mark.parametrize(
    ("fee_state", "shipping_state", "expected_missing"),
    [
        (MonetaryEvidenceState.ESTIMATED, MonetaryEvidenceState.CONTEXTUAL, "sale_fee.exact_evidence"),
        (MonetaryEvidenceState.FALLBACK, MonetaryEvidenceState.CONTEXTUAL, "sale_fee.exact_evidence"),
        (MonetaryEvidenceState.CONTEXTUAL, MonetaryEvidenceState.ESTIMATED, "seller_shipping.exact_evidence"),
        (MonetaryEvidenceState.CONTEXTUAL, MonetaryEvidenceState.FALLBACK, "seller_shipping.exact_evidence"),
    ],
)
def test_estimated_or_fallback_evidence_never_becomes_decision_eligible(
    fee_state,
    shipping_state,
    expected_missing,
):
    result = calculate_quote(
        _request(
            fee_state=fee_state,
            shipping_state=shipping_state,
        )
    )

    assert result.status is FinancialQuoteStatus.INCOMPLETE
    assert result.decision_eligible is False
    assert expected_missing in result.missing
    assert result.tax_amount is None
    assert result.net_amount is None
    assert result.margin_percent is None


def test_exact_evidence_requires_source_and_exact_price_context():
    missing_source = calculate_quote(_request(fee_source=""))
    one_cent_mismatch = calculate_quote(_request(fee_price_context="99.99"))
    mismatched_context = calculate_quote(_request(shipping_price_context="99.97"))

    assert missing_source.status is FinancialQuoteStatus.INCOMPLETE
    assert "sale_fee.source" in missing_source.missing
    assert one_cent_mismatch.status is FinancialQuoteStatus.INVALID
    assert "sale_fee.price_context_mismatch" in one_cent_mismatch.conflicts
    assert mismatched_context.status is FinancialQuoteStatus.INVALID
    assert "seller_shipping.price_context_mismatch" in mismatched_context.conflicts


def test_conflicting_evidence_fails_closed_without_partial_financial_values():
    result = calculate_quote(
        _request(fee_state=MonetaryEvidenceState.CONFLICT)
    )

    assert result.status is FinancialQuoteStatus.INVALID
    assert result.decision_eligible is False
    assert "sale_fee" in result.conflicts
    assert result.tax_amount is None
    assert result.net_amount is None
    assert result.margin_percent is None
    assert result.decision_margin_percent is None


def test_evidence_role_and_context_fingerprint_must_match_the_request():
    fixed_fee_only = calculate_quote(
        _request(fee_role=MonetaryEvidenceRole.SELLER_SHIPPING_NET)
    )
    other_context = calculate_quote(
        _request(shipping_context_fingerprint="other-quote")
    )

    assert fixed_fee_only.status is FinancialQuoteStatus.INVALID
    assert "sale_fee.role_mismatch" in fixed_fee_only.conflicts
    assert other_context.status is FinancialQuoteStatus.INVALID
    assert "seller_shipping.context_fingerprint_mismatch" in other_context.conflicts


def test_invalid_evidence_state_or_price_context_fails_closed():
    unknown_state = calculate_quote(_request(fee_state="unknown"))
    non_finite_context = calculate_quote(
        _request(shipping_price_context=D("NaN"))
    )

    assert unknown_state.status is FinancialQuoteStatus.INVALID
    assert "sale_fee.state" in unknown_state.conflicts
    assert non_finite_context.status is FinancialQuoteStatus.INVALID
    assert "seller_shipping.price_context_invalid" in non_finite_context.conflicts


@pytest.mark.parametrize(
    ("field", "value", "conflict"),
    [
        ("price", D("0"), "effective_price.non_positive"),
        ("price", D("-1"), "effective_price.non_positive"),
        ("cost", D("-1"), "product_cost.negative"),
        ("tax_rate", D("-0.01"), "tax_rate.negative"),
        ("tax_rate", D("1.01"), "tax_rate.out_of_range"),
        ("fee", D("-1"), "sale_fee.amount_negative"),
    ],
)
def test_negative_or_out_of_range_components_are_invalid(field, value, conflict):
    result = calculate_quote(_request(**{field: value}))

    assert result.status is FinancialQuoteStatus.INVALID
    assert result.decision_eligible is False
    assert conflict in result.conflicts


def test_negative_seller_shipping_net_is_an_explicit_credit_not_invalid_input():
    result = calculate_quote(
        _request(
            price="100.00",
            cost="30.00",
            tax_rate="0.10",
            fee="10.00",
            shipping=D("-3.00"),
        )
    )

    assert result.status is FinancialQuoteStatus.EXACT
    assert result.net_amount == D("53.00")
    assert result.margin_percent == D("53.000000")
    assert result.decision_margin_percent == D("53.00")


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity")])
@pytest.mark.parametrize(
    ("field", "conflict_prefix"),
    [
        ("price", "effective_price.non_finite"),
        ("cost", "product_cost.non_finite"),
        ("tax_rate", "tax_rate.non_finite"),
        ("fee", "sale_fee.amount_non_finite"),
        ("shipping", "seller_shipping.amount_non_finite"),
    ],
)
def test_non_finite_values_return_invalid_without_raising(field, conflict_prefix, value):
    result = calculate_quote(_request(**{field: value}))

    assert result.status is FinancialQuoteStatus.INVALID
    assert conflict_prefix in result.conflicts


def test_calculation_cannot_inherit_permissive_decimal_traps_or_return_nan():
    with localcontext() as caller_context:
        caller_context.traps[InvalidOperation] = False
        result = calculate_quote(
            _request(
                price=D("1E+100"),
                cost=D("0"),
                tax_rate=D("0.10"),
                fee=D("0"),
                shipping=D("0"),
            )
        )

    assert result.status is FinancialQuoteStatus.INVALID
    assert result.decision_eligible is False
    assert "calculation" in result.conflicts
    assert result.tax_amount is None
    assert result.net_amount is None
    assert result.margin_percent is None
    assert result.decision_margin_percent is None


@pytest.mark.parametrize("signal", [Inexact, Rounded])
def test_calculation_does_not_inherit_restrictive_decimal_traps(signal):
    with localcontext() as caller_context:
        caller_context.traps[signal] = True
        result = calculate_quote(
            _request(
                price="99.99",
                cost="31.11",
                tax_rate="0.0725",
                fee="13.27",
                shipping="8.54",
            )
        )

    assert result.status is FinancialQuoteStatus.EXACT
    assert result.tax_amount == D("7.25")
    assert result.net_amount == D("39.82")
    assert result.decision_margin_percent == D("39.82")


def test_wrong_currency_contract_version_and_non_decimal_inputs_are_invalid():
    wrong_currency = calculate_quote(_request(currency_id="USD"))
    wrong_version = calculate_quote(_request(contract_version="financial_quote.v0"))
    non_decimal = calculate_quote(_request(price=100.0))

    assert wrong_currency.status is FinancialQuoteStatus.INVALID
    assert "currency_id" in wrong_currency.conflicts
    assert wrong_version.status is FinancialQuoteStatus.INVALID
    assert "contract_version" in wrong_version.conflicts
    assert non_decimal.status is FinancialQuoteStatus.INVALID
    assert "effective_price.type" in non_decimal.conflicts


def test_valid_negative_margin_is_not_confused_with_invalid_components():
    result = calculate_quote(
        _request(
            price="100.00",
            cost="90.00",
            tax_rate="0.10",
            fee="15.00",
            shipping="5.00",
        )
    )

    assert result.status is FinancialQuoteStatus.EXACT
    # Ready to feed a decision is not the same as approving the margin.
    assert result.decision_eligible is True
    assert result.net_amount == D("-20.00")
    assert result.margin_percent == D("-20.000000")
    assert result.decision_margin_percent == D("-20.00")


def test_contract_objects_are_immutable():
    request = _request()

    with pytest.raises(FrozenInstanceError):
        request.currency_id = "USD"
