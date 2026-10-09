"""Official LIGHTNING suggestion retry, using real financial and send gates offline."""
import copy
from types import SimpleNamespace

import pytest

from backend.services import promocoes_lightning_reanalise as recovery
from tests.test_promocoes_lightning_candidate_price import candidate, candidate_flow
from tests.test_promocoes_seller_campaign_reference import (
    ITEM, LIGHTNING, offline_runtime, reference_flow,
)


def configure_retry(flow, suggestion=109.65):
    flow.responses = [400, 201]
    flow.changes = [{"suggested_discounted_price": suggestion}]


def run_reported(flow, price=97.65, sale=98.84, standard=128.99,
                 minimum=25.80, maximum=116.10):
    return flow.run(price, sale, standard, minimum, maximum)


@pytest.mark.parametrize("price,sale,standard,minimum,maximum,suggested", [
    (97.65, 98.84, 128.99, 25.80, 116.10, 109.65),
    (62.95, 61.98, 80.89, 16.18, 72.81, 68.76),
])
def test_reported_rejection_uses_one_refreshed_official_suggestion(
    candidate_flow, price, sale, standard, minimum, maximum, suggested,
):
    configure_retry(candidate_flow, suggested)
    (ok, message, _), details = run_reported(
        candidate_flow, price, sale, standard, minimum, maximum,
    )
    assert ok, message
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [price, suggested]
    assert candidate_flow.reference.fees == [price, sale, suggested, sale]
    assert candidate_flow.reference.shipping == [price, sale, suggested, sale]
    assert all(raw["price"] == price for raw in candidate_flow.send_contexts)
    assert candidate_flow.initial_candidate["price"] == price
    diagnostic = details["price_diagnostics"]
    assert diagnostic["attempt_count"] == 2
    assert diagnostic["price_source"] == "suggested_discounted_price"
    assert diagnostic["suggested_price"] == diagnostic["sent_price"] == suggested
    assert details["analysis_row_updates"]["action_deal_price"] == suggested
    assert details["analysis_row_updates"]["action_tarifa_ml"] == round(suggested * .1, 2)


def test_retry_uses_fresh_suggestion_instead_of_previous_response(candidate_flow):
    configure_retry(candidate_flow)
    candidate_flow.changes = [
        {"suggested_discounted_price": 109.65},
        {"suggested_discounted_price": 108.65},
    ]
    (ok, message, _), details = run_reported(candidate_flow)
    assert ok, message
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65, 108.65]
    assert details["price_diagnostics"]["suggested_price"] == 108.65


def test_current_financial_minimum_blocks_official_retry(candidate_flow):
    configure_retry(candidate_flow, 90)
    candidate_flow.reference.margin_minimum = 56
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65]
    assert candidate_flow.reference.fees == [97.65, 98.84, 90, 98.84]
    assert candidate_flow.reference.shipping == [97.65, 98.84, 90, 98.84]
    assert details["price_diagnostics"]["decision_code"] == "RECALCULATED_MARGIN_REJECTED"


@pytest.mark.parametrize("suggested", [None, True, False, 0, -.01, "NaN", "Infinity", {}, [],
                                         25.79, 116.11, 128.99, 129, 97.65, "97.650"])
def test_invalid_absent_or_repeated_suggestion_never_creates_second_post(candidate_flow, suggested):
    configure_retry(candidate_flow, suggested)
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65]
    assert details["price_diagnostics"]["attempt_count"] == 1


@pytest.mark.parametrize("changes", [
    {"min_discounted_price": None}, {"max_discounted_price": None},
    {"minimum_discounted_price": 26}, {"maximum_discounted_price": 115},
    {"min_discounted_price": "NaN"}, {"max_discounted_price": True},
    {"max_discounted_price": 25},
])
def test_refreshed_missing_invalid_or_conflicting_bounds_block_retry(candidate_flow, changes):
    configure_retry(candidate_flow)
    candidate_flow.changes = [
        {"suggested_discounted_price": 109.65},
        {"suggested_discounted_price": 109.65, **changes},
    ]
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65]
    assert details["price_diagnostics"]["attempt_count"] == 1


def test_second_credibility_rejection_does_not_try_another_discount(candidate_flow):
    configure_retry(candidate_flow)
    candidate_flow.responses = [400]
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65, 109.65]
    assert details["price_diagnostics"]["decision_code"] == "ML_PRICE_CREDIBILITY_REJECTED"
    assert details["price_diagnostics"]["attempt_count"] == 2


def test_suggestion_from_changed_candidate_cannot_be_submitted(candidate_flow):
    configure_retry(candidate_flow)
    candidate_flow.changes = [
        {"suggested_discounted_price": 109.65},
        {"suggested_discounted_price": 109.65, "ref_id": f"CANDIDATE-{ITEM}-OTHER"},
    ]
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65]
    assert details["price_diagnostics"]["decision_code"] == "OFFICIAL_INVITATION_UNCONFIRMED"


def test_revised_candidate_price_takes_precedence_over_alternate_field(candidate_flow):
    configure_retry(candidate_flow)
    candidate_flow.changes = [
        {"suggested_discounted_price": 109.65},
        {"suggested_discounted_price": 109.65, "price": 96.65},
    ]
    (ok, message, _), details = run_reported(candidate_flow)
    assert ok, message
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [97.65, 96.65]
    assert details["price_diagnostics"]["price_source"] == "candidate_price"


@pytest.mark.parametrize("status", [401, 403, 422, 500, 503])
def test_non_400_response_does_not_activate_price_retry(candidate_flow, status):
    configure_retry(candidate_flow)
    candidate_flow.responses = [status, 201]
    (ok, _, _), details = run_reported(candidate_flow)
    assert not ok
    assert len(candidate_flow.reference.posts) == 1
    assert details["price_diagnostics"]["attempt_count"] == 1


def suggestion_raw(**changes):
    return candidate(97.65, 25.80, 116.10, original_price=128.99,
                     suggested_discounted_price=109.65, **changes)


def validate(raw, selected=109.65, source="suggested_discounted_price"):
    prices = {"standard_price": 128.99, "sale_price": 98.84,
              "_jk_lightning_price_source": source}
    diagnostics = {}
    validated, message, _ = recovery.validate_exact_lightning(
        None, "tenant", "store", {}, LIGHTNING, ITEM, raw, selected, 5, diagnostics, prices,
    )
    return validated, message, diagnostics


@pytest.mark.parametrize("selected", [109.64, 109.66, 97.65, 116.10])
def test_lower_gate_compares_exact_suggested_cents(selected):
    validated, _, diagnostics = validate(suggestion_raw(), selected)
    assert validated is None
    assert diagnostics["decision_code"] == "OFFICIAL_PRICE_CHANGED_REANALYSIS_REQUIRED"


@pytest.mark.parametrize("changes", [
    {"min_discounted_price": 110}, {"max_discounted_price": 109},
    {"suggested_discounted_price": 109.64}, {"suggested_discounted_price": None},
    {"status": "pending"}, {"status": "eligible"},
])
def test_lower_gate_rechecks_current_suggestion_and_bounds(changes):
    raw = suggestion_raw()
    raw.update(changes)
    validated, _, _ = validate(raw)
    assert validated is None


@pytest.mark.parametrize("source", ["candidate_price", "suggested_price", "min_discounted_price", "client", "", None])
def test_lower_gate_rejects_unapproved_source_and_raw_markers(source):
    validated, _, _ = validate(suggestion_raw(_jk_lightning_price_source="suggested_discounted_price"), source=source)
    assert validated is None


def test_lower_gate_preserves_raw_and_legacy_exact_policy(monkeypatch):
    raw = suggestion_raw(_jk_lightning_price_source="suggested_discounted_price")
    before = copy.deepcopy(raw)
    validated, message, _ = validate(raw)
    assert validated == raw, message
    assert raw == before
    monkeypatch.setattr(recovery, "_official_prices", lambda *args, **kwargs:
                        ({"standard_price": 128.99, "sale_price": 98.84}, {}))
    diagnostics = {}
    validated, _, _ = recovery.validate_exact_lightning(
        None, "tenant", "store", {}, LIGHTNING, ITEM, raw, 109.65, 5, diagnostics,
    )
    assert validated is None
    assert diagnostics["decision_code"] == "OFFICIAL_PRICE_CHANGED_REANALYSIS_REQUIRED"


@pytest.mark.parametrize("field", ["error_code", "error_message"])
def test_error_code_can_appear_only_in_official_cause_fields(field):
    response = SimpleNamespace(json=lambda: {"error": "bad_request", "cause": [
        {field: "ERROR_CREDIBILITY_DISCOUNTED_PRICE"},
    ]})
    assert recovery.ml_error_code(response) == "ERROR_CREDIBILITY_DISCOUNTED_PRICE"


@pytest.mark.parametrize("changes", [
    {"min_discounted_price": 25.81}, {"max_discounted_price": 116.09},
    {"suggested_discounted_price": 109.64}, {"minimum_discounted_price": 25.81},
    {"suggested_discounted_price": "NaN"},
])
def test_duplicate_invitation_signature_detects_different_monetary_terms(changes):
    raw = suggestion_raw()
    assert recovery.lightning_price_signature(raw) != recovery.lightning_price_signature({**raw, **changes})


def test_equivalent_bound_aliases_share_the_same_monetary_signature():
    raw = suggestion_raw()
    other = {key: value for key, value in raw.items()
             if key not in {"min_discounted_price", "max_discounted_price"}}
    other.update(min_deal_price="25.80", maximum_discounted_price="116.10",
                 suggested_discounted_price="109.650")
    assert recovery.lightning_price_signature(raw) == recovery.lightning_price_signature(other)


@pytest.mark.parametrize("suggested", [128.99, 129])
def test_alternative_must_be_below_standard_even_when_bounds_allow_it(suggested):
    raw = suggestion_raw()
    raw.update(max_discounted_price=150, suggested_discounted_price=suggested)
    validated, _, diagnostics = validate(raw, suggested)
    assert validated is None
    assert diagnostics["decision_code"] == "OFFICIAL_PRICE_INCONSISTENT"


def test_retry_requotes_fees_without_reusing_original_receivables(candidate_flow):
    configure_retry(candidate_flow)
    candidate_flow.changes = [{"suggested_discounted_price": 109.65,
                               "seller_receives": 97.64, "sale_fee_amount": .01}]
    (ok, message, _), details = run_reported(candidate_flow)
    assert ok, message
    assert details["analysis_row_updates"]["action_tarifa_ml"] == 10.97
    assert details["analysis_row_updates"]["action_margem_ml"] == pytest.approx(
        (109.65 - 10.97 - 2 - 20 - 109.65 * .1) * 100 / 109.65,
    )
    assert candidate_flow.send_contexts[1]["sale_fee_amount"] == .01
    assert candidate_flow.send_contexts[1]["seller_receives"] == 97.64
