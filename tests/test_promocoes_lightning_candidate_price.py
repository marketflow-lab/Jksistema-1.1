"""Offline coverage for choosing LIGHTNING prices inside official candidate bounds."""
import copy
from types import SimpleNamespace

import pytest

from backend.services import promocoes_api_participacoes as participation
from backend.services.mercadolivre_legacy_promocoes import _promo_erro_candidate_not_found
from backend.services import promocoes_lightning_reanalise as recovery
from tests.test_promocoes_seller_campaign_reference import (
    BASE_URL, ITEM, LIGHTNING, offline_runtime, reference_flow,
)


def candidate(price=75.28, minimum=16.68, maximum=75.08, **changes):
    return {
        "id": LIGHTNING, "item_id": ITEM, "type": "LIGHTNING",
        "status": "candidate", "ref_id": f"CANDIDATE-{ITEM}-PRICE-TEST",
        "price": price, "original_price": 83.42,
        "min_discounted_price": minimum, "max_discounted_price": maximum,
        "stock": {"min": 5, "max": 20}, **changes,
    }


@pytest.mark.parametrize("suggestion,standard,minimum,maximum,expected", [
    (75.28, 83.42, 16.68, 75.08, 75.08),
    (108.42, 78.36, 15.67, 70.52, 70.52),
    (108.42, 78.36, 15.67, 100.00, 78.35),
    (108.42, 78.36, 29.35, 132.04, 78.35),
    (10.00, 83.42, 50.00, 75.08, 50.00),
    (83.42, 83.42, 16.68, 83.42, 83.41),
    (75.28, 83.42, 75.08, 75.08, 75.08),
    (75.28, 83.42, 16.68, 75.075, 75.08),
    (70.00, 83.42, 16.68, 75.08, 70.00),
])
def test_candidate_price_is_nearest_value_in_official_interval(
    suggestion, standard, minimum, maximum, expected,
):
    raw = candidate(suggestion, minimum, maximum)
    before = copy.deepcopy(raw)
    reason, low, high, chosen = recovery._price_reason(
        raw, {"standard_price": standard, "sale_price": 61.90},
        adjust_candidate=True,
    )
    assert reason == ""
    assert chosen == expected
    assert recovery.money_cents(low) <= recovery.money_cents(chosen) <= recovery.money_cents(high)
    assert 0 < recovery.money_cents(chosen) < recovery.money_cents(standard)
    assert raw == before


@pytest.mark.parametrize("changes", [
    {"price": None}, {"price": True}, {"price": 0}, {"price": -1},
    {"price": "NaN"}, {"price": "Infinity"},
    {"min_discounted_price": None}, {"price": 108.42, "max_discounted_price": None},
    {"min_discounted_price": True}, {"max_discounted_price": True},
    {"min_discounted_price": "NaN"}, {"max_discounted_price": "Infinity"},
    {"min_discounted_price": 0}, {"max_discounted_price": -1},
    {"min_discounted_price": 80}, {"min_discounted_price": 83.42, "max_discounted_price": 90},
    {"min_deal_price": 30}, {"max_deal_price": 80},
    {"status": None}, {"status": "started"}, {"status": "pending"}, {"status": "finished"},
])
def test_invalid_suggestion_interval_or_state_is_never_adapted(changes):
    raw = candidate(**changes)
    before = copy.deepcopy(raw)
    reason, _low, _high, _chosen = recovery._price_reason(
        raw, {"standard_price": 83.42, "sale_price": 75.82},
        adjust_candidate=True,
    )
    assert reason
    assert raw == before


def test_no_positive_cent_remains_below_standard():
    reason, *_ = recovery._price_reason(
        candidate(.02, .01, .03),
        {"standard_price": .01, "sale_price": .01},
        adjust_candidate=True,
    )
    assert reason


def test_legacy_exact_policy_keeps_incompatible_suggestion_blocked():
    reason, _minimum, _maximum, chosen = recovery._price_reason(
        candidate(), {"standard_price": 83.42, "sale_price": 75.82},
    )
    assert reason and chosen == 75.28


@pytest.fixture
def candidate_flow(reference_flow, monkeypatch):
    """Reuse real finance/reference/transport functions with only simulated I/O."""
    state = SimpleNamespace(
        reference=reference_flow, changes=[{}], responses=[201], context_changes={},
        send_delta=0, send_context_changes={}, confirm_count=0, send_contexts=[],
        initial_candidate=None, applied=False, last_sent=None,
    )
    original_apply = recovery.apply_reanalysed_lightning
    monkeypatch.setattr(participation, "_promo_erro_candidate_not_found",
                        _promo_erro_candidate_not_found, raising=False)

    def run(suggestion=75.28, reference_price=75.82, standard=83.42,
            minimum=16.68, maximum=75.08):
        state.confirm_count = 0
        state.send_contexts.clear()
        state.initial_candidate = None
        state.applied = False
        state.last_sent = None
        original_confirm = participation._promo_confirmar_oferta_lightning
        original_send = participation._promo_aplicar_item_participacao_ml

        def intercept_apply(*args, **kwargs):
            original_request = participation._ml_api_request
            original_records = participation._ml_obter_promocoes_item
            kwargs["row_context"].update(state.context_changes)

            def records(*record_args, **record_kwargs):
                entries, cfg = original_records(*record_args, **record_kwargs)
                entries = copy.deepcopy(entries)
                for index, raw in enumerate(entries):
                    if raw.get("id") != LIGHTNING:
                        continue
                    if state.initial_candidate is None:
                        state.initial_candidate = copy.deepcopy(raw)
                    if state.applied:
                        raw["status"] = "pending"
                        raw["price"] = state.last_sent
                    else:
                        raw = copy.deepcopy(state.initial_candidate)
                        raw.update(min_discounted_price=minimum, max_discounted_price=maximum)
                        raw.update(state.changes[min(max(state.confirm_count - 1, 0), len(state.changes) - 1)])
                        entries[index] = raw
                return entries, cfg

            def request(client, store, cfg, method, url, **request_kwargs):
                response, cfg = original_request(client, store, cfg, method, url, **request_kwargs)
                if method != "POST":
                    return response, cfg
                assert url == f"{BASE_URL}/seller-promotions/items/{ITEM}"
                state.last_sent = request_kwargs["json"]["deal_price"]
                status = state.responses[min(len(reference_flow.posts) - 1, len(state.responses) - 1)]
                state.applied = status in (200, 201)
                body = response.json()
                if state.applied:
                    body["price"] = state.last_sent
                else:
                    body = {"error": "bad_request", "message": "ERROR_CREDIBILITY_DISCOUNTED_PRICE"}
                return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(body)), cfg

            def confirm(*confirm_args, **confirm_kwargs):
                state.confirm_count += 1
                return original_confirm(*confirm_args, **confirm_kwargs)

            def send(*send_args, **send_kwargs):
                state.send_contexts.append(copy.deepcopy(send_kwargs["lightning_context"]))
                send_kwargs["deal_price"] += state.send_delta
                send_kwargs["lightning_context"] = copy.deepcopy(send_kwargs["lightning_context"])
                send_kwargs["lightning_context"].update(state.send_context_changes)
                return original_send(*send_args, **send_kwargs)

            monkeypatch.setattr(participation, "_ml_obter_promocoes_item", records)
            monkeypatch.setattr(participation, "_ml_api_request", request)
            monkeypatch.setattr(participation, "_promo_confirmar_oferta_lightning", confirm)
            monkeypatch.setattr(participation, "_promo_aplicar_item_participacao_ml", send)
            return original_apply(*args, **kwargs)

        monkeypatch.setattr(recovery, "apply_reanalysed_lightning", intercept_apply)
        return reference_flow.run(suggestion, reference_price, standard)

    state.run = run
    return state


@pytest.mark.parametrize("suggestion,reference,standard,minimum,maximum,expected", [
    (75.28, 75.82, 83.42, 16.68, 75.08, 75.08),
    (108.42, 61.90, 78.36, 15.67, 70.52, 70.52),
    (108.42, 61.90, 78.36, 15.67, 100.00, 78.35),
    (108.42, 61.90, 78.36, 29.35, 132.04, 78.35),
    (10.00, 52.00, 83.42, 50.00, 75.08, 50.00),
    (83.42, 75.82, 83.42, 16.68, 83.42, 83.41),
    (70.00, 70.40, 83.42, 16.68, 75.08, 70.00),
])
def test_server_reanalysis_sends_chosen_price_and_preserves_invitation(
    candidate_flow, suggestion, reference, standard, minimum, maximum, expected,
):
    (ok, message, _cfg), details = candidate_flow.run(
        suggestion, reference, standard, minimum, maximum,
    )
    assert ok, message
    assert candidate_flow.reference.posts == [
        {"promotion_type": "LIGHTNING", "deal_price": expected, "stock": 5},
    ]
    diagnostic = details["price_diagnostics"]
    assert diagnostic["decision_code"] == "PARTICIPATION_CONFIRMED"
    assert diagnostic["invitation_price"] == suggestion
    assert diagnostic["official_price"] == diagnostic["sent_price"] == expected
    assert diagnostic["attempt_count"] == 1
    assert candidate_flow.send_contexts[0]["price"] == suggestion
    assert candidate_flow.initial_candidate["price"] == suggestion
    assert candidate_flow.reference.fees == [expected, reference]
    assert candidate_flow.reference.shipping == [expected, reference]
    updates = details["analysis_row_updates"]
    assert updates["action_deal_price"] == expected
    assert updates["action_financeiro_estimado"] is True
    assert updates["action_margem_ml"] >= candidate_flow.reference.margin_minimum
    assert updates["reference_status"] == "Ativo"


@pytest.mark.parametrize("suggestion", [75.28, 75.10, 75.09])
def test_old_candidate_fee_and_receivable_do_not_approve_adjusted_margin(candidate_flow, suggestion):
    candidate_flow.changes = [{"seller_receives": 75.0, "sale_fee_amount": .01}]
    (ok, message, _cfg), details = candidate_flow.run(suggestion=suggestion)
    assert ok, message
    expected_fee = round(75.08 * .1, 2)
    assert details["analysis_row_updates"]["action_tarifa_ml"] == expected_fee
    assert details["analysis_row_updates"]["action_margem_ml"] == pytest.approx(
        (75.08 - expected_fee - 2 - 20 - 75.08 * .1) * 100 / 75.08,
    )
    assert candidate_flow.send_contexts[0]["seller_receives"] == 75.0
    assert candidate_flow.reference.fees == [75.08, 75.82]


@pytest.mark.parametrize("minimum,tolerance", [(90, 5), (15, 0)])
def test_adjusted_price_still_requires_current_minimum_and_reference_margin(
    candidate_flow, minimum, tolerance,
):
    candidate_flow.reference.margin_minimum = minimum
    candidate_flow.reference.tolerance = tolerance
    (ok, _message, _cfg), details = candidate_flow.run()
    assert not ok
    assert details["price_diagnostics"]["decision_code"] == "RECALCULATED_MARGIN_REJECTED"
    assert candidate_flow.reference.posts == []
    assert candidate_flow.reference.fees == [75.08, 75.82]


def test_adjustment_does_not_bypass_reference_campaign_identity(candidate_flow):
    candidate_flow.reference.reference_record_changes = {"item_id": "MLB999000456"}
    (ok, _message, _cfg), details = candidate_flow.run()
    assert not ok
    assert details["price_diagnostics"]["decision_code"] == "FINANCIAL_CONTEXT_UNCONFIRMED"
    assert candidate_flow.reference.posts == []


@pytest.mark.parametrize("delta", [-.01, .01])
def test_lower_gate_rejects_price_that_differs_from_server_choice(candidate_flow, delta):
    candidate_flow.send_delta = delta
    (ok, _message, _cfg), _details = candidate_flow.run()
    assert not ok
    assert candidate_flow.reference.posts == []


def test_lower_gate_revalidates_the_candidate_bounds(candidate_flow):
    candidate_flow.send_context_changes = {"max_discounted_price": 75.09}
    (ok, _message, _cfg), _details = candidate_flow.run()
    assert not ok
    assert candidate_flow.reference.posts == []


@pytest.mark.parametrize("changes", [
    {"min_discounted_price": None}, {"price": 108.42, "max_discounted_price": None},
    {"min_discounted_price": 83.42, "max_discounted_price": 90},
    {"min_discounted_price": "NaN"}, {"status": "paused"},
])
def test_invalid_current_candidate_never_reaches_post(candidate_flow, changes):
    candidate_flow.changes = [changes]
    (ok, _message, _cfg), details = candidate_flow.run()
    assert not ok
    assert candidate_flow.reference.posts == []
    assert details["price_diagnostics"]["attempt_count"] == 0


def test_credibility_rejection_does_not_repeat_unchanged_adjusted_price(candidate_flow):
    candidate_flow.responses = [400]
    candidate_flow.changes = [{}, {"price": 75.27}]
    (ok, _message, _cfg), details = candidate_flow.run()
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [75.08]
    assert details["price_diagnostics"]["decision_code"] == "OFFICIAL_PRICE_UNCHANGED_AFTER_REJECTION"


def test_new_official_bound_can_produce_one_recalculated_retry(candidate_flow):
    candidate_flow.responses = [400, 201]
    candidate_flow.changes = [{}, {"max_discounted_price": 74.08}]
    (ok, message, _cfg), details = candidate_flow.run()
    assert ok, message
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [75.08, 74.08]
    assert candidate_flow.reference.fees == [75.08, 75.82, 74.08, 75.82]
    assert details["price_diagnostics"]["attempt_count"] == 2
    assert [raw["price"] for raw in candidate_flow.send_contexts] == [75.28, 75.28]


def test_two_rejected_adjusted_prices_never_send_a_third(candidate_flow):
    candidate_flow.responses = [400]
    candidate_flow.changes = [
        {}, {"max_discounted_price": 74.08}, {"max_discounted_price": 73.08},
    ]
    (ok, _message, _cfg), details = candidate_flow.run()
    assert not ok
    assert [post["deal_price"] for post in candidate_flow.reference.posts] == [75.08, 74.08]
    assert details["price_diagnostics"]["attempt_count"] == 2
