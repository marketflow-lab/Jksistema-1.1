"""Candidatos sem agenda da API devem continuar analisaveis nos tres fluxos."""
import copy

import pytest

from tests import test_promocoes_desconto_ml_fluxo as flow_fixture

ITEM_ID = flow_fixture.ITEM_ID
CAMPAIGN = "LGH-MLB1000"
MODES = [{}, {"via_api_direta": True}, {"com_arquivos": True}]


def candidate():
    return {
        "promotion_id": CAMPAIGN,
        "type": "LIGHTNING",
        "item_id": ITEM_ID,
        "status": "candidate",
        "ref_id": f"CANDIDATE-{ITEM_ID}-9",
        "price": 66.32,
        "original_price": 92.28,
        "stock": {"min": 5, "max": 20},
        "_jk_campaign_id_consultado": CAMPAIGN,
        "_jk_item_id_consultado": ITEM_ID,
        "_jk_promotion_type_consultado": "LIGHTNING",
        "_jk_contexto_promocao_confirmado": True,
    }


def analyze(raw, mode):
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    return harness._executar_fluxo(
        copy.deepcopy(raw), promo_b_id=CAMPAIGN, promo_b_type="LIGHTNING",
        price_info={
            "price": 90,
            "standard_price": 92.28,
            "original_price": 92.28,
            "promotion_id": flow_fixture.PROMO_A_ID,
            "promotion_type": "SELLER_CAMPAIGN",
        }, retornar_resultado=True, **mode,
    )["analises"][0]["data"]


@pytest.mark.parametrize("mode", MODES)
def test_candidate_without_dates_survives_analysis_and_serialization(mode):
    rows = analyze(candidate(), mode)
    assert len(rows) == 1
    row = rows[0]
    assert row["lightning_schedule_pending"] is True
    assert row["lightning_action_blocked"] is False
    assert row["lightning_action_reason"] == ""
    assert row["action_tecnico_apto"] is True
    assert row["lightning_start_date"] == ""
    assert row["lightning_finish_date"] == ""
    assert row["action_deal_price"] == row["deal_price"] == 66.32
    assert row["action_offer_id"] == candidate()["ref_id"]
    assert row["lightning_stock_min"] == row["lightning_stock"] == 5
    assert row["lightning_stock_max"] == 20
    assert row["lightning_offer_key"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("bad_dates", [
    {"start_date": "2026-10-09T12:00:00"},
    {"start_date": 1791543600, "finish_date": 1791586800},
    {"start_date": "invalid", "finish_date": "invalid"},
])
def test_partial_or_invalid_dates_do_not_become_unscheduled_candidates(mode, bad_dates):
    rows = analyze(dict(candidate(), **bad_dates), mode)
    assert len(rows) == 1
    row = rows[0]
    assert row["lightning_schedule_pending"] is False
    assert row["lightning_action_blocked"] is True
    assert row["action_tecnico_apto"] is False
    assert row["lightning_action_reason"]
