"""Every user-selected row receives its own explicit participation result."""

from types import SimpleNamespace

import pytest
import requests

from backend.schemas.promocoes import PromoAplicarParticipacaoRequest
from backend.services import promocoes_api_participacoes as participacoes
from backend.services import mercadolivre_legacy_promocoes as ml_promocoes
from backend.services.mercadolivre_legacy_promocoes import _promo_erro_candidate_not_found


@pytest.fixture
def ml(monkeypatch):
    state = SimpleNamespace(calls=[], lookups=[], response=None, eligibility=None)
    monkeypatch.setattr(participacoes, "_obter_cfg_ml", lambda client, store: {"scope": (client, store)}, raising=False)

    def lookup(client, store, cfg, campaign, kind, item):
        assert cfg["scope"] == (client, store)
        state.lookups.append((client, store, campaign, item))
        if isinstance(state.eligibility, Exception):
            raise state.eligibility
        return state.eligibility or {"success": True, "found": False}, cfg

    def request(client, store, cfg, method, url, **kwargs):
        assert cfg["scope"] == (client, store)
        state.calls.append((client, store, method, url, kwargs))
        response = state.response
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        status, data = response or (201, {})
        return SimpleNamespace(status_code=status, json=lambda: data), cfg

    monkeypatch.setattr(participacoes, "_promo_consultar_item_na_campanha", lookup, raising=False)
    monkeypatch.setattr(participacoes, "_ml_api_request", request, raising=False)
    monkeypatch.setattr(participacoes, "_ml_parse_error_detail", lambda response, default: str(response.json().get("message") or default), raising=False)
    monkeypatch.setattr(participacoes, "_ml_obter_item_promocao_raw", lambda client, store, cfg, *args: ({}, cfg), raising=False)
    monkeypatch.setattr(participacoes, "_ml_promocao_raw_offer_id", lambda raw: raw.get("offer_id", ""), raising=False)
    monkeypatch.setattr(participacoes, "_promo_erro_candidate_not_found", _promo_erro_candidate_not_found, raising=False)
    monkeypatch.setattr(ml_promocoes, "normalizar_texto", participacoes.normalizar_texto, raising=False)
    return state


def apply(items, *, campaign="CAMPAIGN-A", kind="SELLER_CAMPAIGN", client="tenant-a", store="store-a"):
    return participacoes._aplicar_participacoes_promocoes_payload(
        PromoAplicarParticipacaoRequest(loja=store, promocoes=[{
            "promotion_id": campaign, "promotion_type": kind, "items": items,
        }]), client,
    )


def row(index=0, **kwargs):
    return {"item_id": f"MLB{1000000 + index}", "deal_price": 90, **kwargs}


def test_all_236_selected_are_processed_despite_financial_recommendation(ml):
    result = apply([row(i, action_financeiro_exato=i < 11, client_ref=f"selected-{i}") for i in range(236)])
    assert len(ml.calls) == result["total_itens"] == result["total_sucesso"] == 236
    assert len(result["detalhes"]) == 236
    assert all(detail["outcome"] == "applied" for detail in result["detalhes"])
    assert result["detalhes"][235]["client_ref"] == "selected-235"


def test_more_than_300_results_and_legacy_references(ml):
    result = apply([row(i) for i in range(321)])
    assert len(result["detalhes"]) == result["total_itens"] == 321
    assert result["detalhes"][-1]["client_ref"] == "0:320"
    assert result["total_itens"] == sum(result[key] for key in ("total_sucesso", "total_falha", "total_ignorados"))


@pytest.mark.parametrize("eligibility", [
    {"success": True, "found": False},
    {"success": False, "found": False},
    {"success": True, "found": True, "can_participate": False, "status": "finished"},
    requests.Timeout(),
])
def test_inconclusive_lookup_still_attempts_selected_item(ml, eligibility):
    ml.eligibility = eligibility
    assert apply([row()])["detalhes"][0]["outcome"] == "applied"
    assert len(ml.calls) == 1


def test_proven_existing_participation_does_not_post(ml):
    ml.eligibility = {"success": True, "found": True, "already_participating": True, "status": "pending"}
    result = apply([row()])
    assert result["detalhes"][0]["outcome"] == "already_participating"
    assert result["total_ignorados"] == 1
    assert result["total_sucesso"] == 0
    assert ml.calls == []


@pytest.mark.parametrize("item,campaign,kind", [
    ({"client_ref": "no-mlb"}, "A", "SELLER_CAMPAIGN"),
    (row(item_id="MLB_INVALID"), "A", "SELLER_CAMPAIGN"),
    (row(), "", "SELLER_CAMPAIGN"),
    (row(deal_price=None), "A", "SELLER_CAMPAIGN"),
    (row(deal_price=float("nan")), "A", "SELLER_CAMPAIGN"),
    (row(), "P-A", "SMART"),
    (None, "A", "SELLER_CAMPAIGN"),
    (row(action_promotion_id="OTHER"), "A", "SELLER_CAMPAIGN"),
    (row(offer_id="OFFER-MLB9999999"), "P-A", "SMART"),
    (row(action_deal_price=None, action_offer_id=None), "A", "SELLER_CAMPAIGN"),
])
def test_technical_impediment_is_explicit_and_never_posts(ml, item, campaign, kind):
    result = apply([item], campaign=campaign, kind=kind)
    assert result["total_falha"] == result["total_itens"] == 1
    assert result["success"] is False
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert result["detalhes"][0]["error"]
    assert ml.calls == []


@pytest.mark.parametrize("response,expected", [
    ((400, {"message": "CANDIDATE_NOT_FOUND"}), "rejected"),
    ((400, {"message": "FINAL_PRICE_INVALID"}), "rejected"),
    ((200, {"errors": [{"error": "not eligible"}]}), "rejected"),
    ((200, {"error": "invalid_price"}), "rejected"),
    ((201, {"success": False}), "rejected"),
    ((200, None), "unknown"),
    ((200, []), "unknown"),
    ((400, {"message": "already participating"}), "rejected"),
    ((400, {"message": "already expired"}), "rejected"),
    ((503, {}), "unknown"),
    ((408, {}), "unknown"),
    ((0, {}), "unknown"),
    (requests.Timeout(), "unknown"),
    (requests.ConnectionError(), "unknown"),
])
def test_explicit_outcome_of_submission(ml, response, expected):
    ml.response = response
    result = apply([row()])
    assert result["detalhes"][0]["outcome"] == expected
    assert len(ml.calls) == 1  # In particular, no PUT for already participating.
    assert result["total_ignorados"] == int(expected == "already_participating")


def test_rejected_exact_price_is_not_replaced_by_suggestion(ml, monkeypatch):
    ml.response = (400, {"message": "ERROR_CREDIBILITY_DISCOUNTED_PRICE"})
    def forbidden(*args, **kwargs):
        pytest.fail("Alternative price lookup must not occur")
    monkeypatch.setattr(participacoes, "_ml_obter_item_promocao_raw", forbidden)
    result = apply([row(discount_percentage=20)])
    assert result["detalhes"][0]["outcome"] == "rejected"
    assert len(ml.calls) == 1
    assert ml.calls[0][4]["json"] == {"promotion_id": "CAMPAIGN-A", "promotion_type": "SELLER_CAMPAIGN", "deal_price": 90}


def test_failure_preserves_previous_results_and_continues(ml):
    ml.response = [(201, {}), requests.Timeout(), (201, {})]
    result = apply([row(i) for i in range(3)])
    assert [detail["outcome"] for detail in result["detalhes"]] == ["applied", "unknown", "applied"]
    assert result["total_sucesso"] == 2
    assert result["total_falha"] == 1


def test_same_item_and_campaign_do_not_mix_stores_or_tenants(ml):
    for client, store in [("tenant-a", "store-a"), ("tenant-a", "store-b"), ("tenant-b", "store-a")]:
        assert apply([row()], client=client, store=store)["success"]
    assert [(call[0], call[1]) for call in ml.calls] == [
        ("tenant-a", "store-a"), ("tenant-a", "store-b"), ("tenant-b", "store-a"),
    ]


def test_smart_offer_can_be_recovered_only_in_requested_scope(ml, monkeypatch):
    def raw(client, store, cfg, campaign, kind, item):
        assert (client, store, campaign, kind, item) == ("tenant-a", "store-a", "P-A", "SMART", "MLB1000000")
        return {"item_id": item, "promotion_id": campaign, "offer_id": "CANDIDATE-MLB1000000-1", "status": "candidate"}, cfg
    monkeypatch.setattr(participacoes, "_ml_obter_item_promocao_raw", raw)
    assert apply([row()], campaign="P-A", kind="SMART")["success"]
    assert ml.calls[0][4]["json"] == {"promotion_id": "P-A", "promotion_type": "SMART", "offer_id": "CANDIDATE-MLB1000000-1"}


@pytest.mark.parametrize("raw", [
    {"item_id": "MLB1000000", "promotion_id": "P-OTHER", "offer_id": "CANDIDATE-MLB1000000-1"},
    {"item_id": "MLB9999999", "promotion_id": "P-A", "offer_id": "CANDIDATE-MLB9999999-1"},
    {"item_id": "MLB1000000", "_jk_campaign_id_consultado": "P-OTHER", "offer_id": "CANDIDATE-MLB1000000-1"},
    {"item_id": "MLB1000000", "promotion_type": "OTHER", "offer_id": "CANDIDATE-MLB1000000-1"},
    {"offer_id": "CANDIDATE-MLB1000000-1"},
])
def test_recovered_offer_with_conflicting_or_incomplete_identity_is_blocked(ml, monkeypatch, raw):
    monkeypatch.setattr(participacoes, "_ml_obter_item_promocao_raw", lambda client, store, cfg, *args: (raw, cfg))
    assert apply([row()], campaign="P-A", kind="SMART")["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_already_response_needs_confirmed_participation(ml, monkeypatch):
    ml.response = (400, {"message": "already participating"})
    checks = iter([{"success": True, "found": False}, {"success": True, "found": True, "already_participating": True}])
    monkeypatch.setattr(participacoes, "_promo_consultar_item_na_campanha", lambda client, store, cfg, *args: (next(checks), cfg))
    assert apply([row()])["detalhes"][0]["outcome"] == "already_participating"
    assert len(ml.calls) == 1


def test_legacy_helper_keeps_three_value_return_and_existing_update(ml):
    ml.response = [(400, {"message": "already participating"}), (200, {})]
    result = participacoes._promo_aplicar_item_participacao_ml(
        "tenant-a", "store-a", {"scope": ("tenant-a", "store-a")},
        item_id="MLB1000000", promotion_id="A", promotion_type="SELLER_CAMPAIGN", deal_price=90,
    )
    assert len(result) == 3
    assert result[0] is True
    assert [call[2] for call in ml.calls] == ["POST", "PUT"]
