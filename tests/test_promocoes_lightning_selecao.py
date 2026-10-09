"""Date selection boundaries: same campaign, distinct windows and isolated stores."""
import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.services import promocoes_api_analise as analysis
from backend.services.promocoes_reanalise_contexto import capturar_contexto_reanalise
from backend.services.promocoes_lightning import lightning_offer_window
from backend.services.promocoes_lightning_acao import expand_lightning_offers
from backend.services.promocoes_lightning_selecao import (
    filter_lightning_selection, lightning_selection_options, normalize_campaign_selection,
    normalize_lightning_window, selection_identity,
)
from tests import test_promocoes_desconto_ml_fluxo as flow_fixture
from tests.test_promocoes_lightning_fluxo import CAMPAIGN, ITEM_ID, aggregate, offer


def selection(raw):
    start, finish = lightning_offer_window(raw)
    window = {"start_date": start, "finish_date": finish} if start or finish else {"undated": True}
    return {"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING",
            **normalize_campaign_selection(CAMPAIGN, "LIGHTNING", {"lightning_window": window})}


def unscheduled():
    raw = offer(18)
    raw.pop("start_date")
    raw.pop("finish_date")
    return raw


def analyze(raw, selections, mode):
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    return harness._executar_fluxo(
        raw, promo_b_id=CAMPAIGN, promo_b_type="LIGHTNING", retornar_resultado=True,
        selecoes=selections, price_info={"price": 90, "standard_price": 92.28,
                                       "original_price": 92.28, "promotion_id": flow_fixture.PROMO_A_ID,
                                       "promotion_type": "SELLER_CAMPAIGN"}, **mode,
    )


MODES = [{}, {"com_arquivos": True}, {"via_api_direta": True}]


def test_options_group_same_campaign_by_exact_window_and_keep_unknown_separate():
    raw = aggregate([offer(9, status="pending"), offer(10), offer(12), unscheduled()])
    pairs = expand_lightning_offers([ITEM_ID], {ITEM_ID: raw}, "LIGHTNING", CAMPAIGN)
    options = lightning_selection_options(CAMPAIGN, pairs)
    assert len(options) == 4
    assert len({entry["selection_id"] for entry in options}) == 4
    assert options[0]["active_count"] == 1
    assert all(entry["eligible_count"] == 1 for entry in options[1:])
    assert options[-1]["lightning_window"] == {"undated": True}
    assert [len(filter_lightning_selection(pairs, entry["lightning_window"])) for entry in options] == [1] * 4


def test_equivalent_zones_are_same_selection_but_naive_dates_remain_distinct():
    first = selection(offer(9))
    equivalent = selection({**offer(9), "start_date": "2026-10-09T15:00:00Z", "finish_date": "2026-10-10T03:00:00Z"})
    naive = selection({**offer(9), "start_date": "2026-10-09T12:00:00", "finish_date": "2026-10-10T00:00:00"})
    assert first == equivalent
    assert first != naive
    assert selection_identity(CAMPAIGN, "LIGHTNING", first) == selection_identity(CAMPAIGN, "lightning", equivalent)


@pytest.mark.parametrize("bad", [[], {}, {"undated": 1}, {"undated": True, "start_date": ""},
    {"start_date": "2026-10-09"}, {"start_date": "garbage"}, {"start_date": "0001-01-01T00:00:00+14:00"},
    {"start_date": "9999-12-31T23:59:59-14:00"}, {"start_date": 123},
    {"start_date": "2026-10-10T12:00:00Z", "finish_date": "2026-10-09T12:00:00Z"},
    {"start_date": "2026-10-09T12:00:00Z", "finish_date": "2026-10-10T12:00:00"},
    {"undated": True, "foreign": "ignored?"},
])
def test_invalid_window_never_becomes_whole_campaign(bad):
    with pytest.raises(ValueError):
        normalize_lightning_window(bad)


def test_identity_rejects_spoofed_id_and_window_on_other_promotion_type():
    first, second = selection(offer(9)), selection(offer(10))
    with pytest.raises(ValueError):
        normalize_campaign_selection(CAMPAIGN, "LIGHTNING", {**second, "selection_id": first["selection_id"]})
    with pytest.raises(ValueError):
        normalize_campaign_selection(CAMPAIGN, "SMART", first)
    with pytest.raises(ValueError):
        normalize_campaign_selection(CAMPAIGN, "LIGHTNING", {"selection_id": first["selection_id"]})
    assert normalize_campaign_selection(CAMPAIGN, "LIGHTNING", {}) == {}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("chosen", [offer(10, 67.45), unscheduled()])
def test_analysis_and_financial_action_only_include_chosen_window(mode, chosen):
    captured = []
    with capturar_contexto_reanalise(captured.append):
        result = analyze(aggregate([offer(9, 66.32), offer(10, 67.45), offer(12, 68.56), unscheduled()]), [selection(chosen)], mode)
    group = result["analises"][0]
    assert len(group["data"]) == 1
    row = group["data"][0]
    assert row["action_offer_id"] == chosen["ref_id"]
    assert row["action_deal_price"] == chosen["price"]
    assert row["lightning_start_date"] == chosen.get("start_date", "")
    if not mode.get("via_api_direta"):
        assert group["selection_id"] == selection(chosen)["selection_id"]
        assert len(captured) == 1
        context = captured[0]
        assert len(context["rows"]) == 1
        assert set(context["rows"]) == {row["analysis_client_ref"]}
        assert context["promocoes_b"][0]["selection_id"] == selection(chosen)["selection_id"]


@pytest.mark.parametrize("mode", MODES)
def test_three_selections_of_same_campaign_remain_separate_and_reuse_snapshot(mode):
    offers = [offer(9), offer(10), offer(12)]
    with patch.object(analysis, "_promo_enriquecer_janelas_lightning", wraps=analysis._promo_enriquecer_janelas_lightning) as enrich:
        result = analyze(aggregate(offers), [selection(raw) for raw in offers], mode)
    if mode.get("via_api_direta"):
        assert len(result["analises"][0]["data"]) == 3
    else:
        assert len(result["analises"]) == 3
        # Exports generated in the same second must never overwrite another date.
        assert len({group["planilha_gerada"] for group in result["analises"]}) == 3
        for group, raw in zip(result["analises"], offers):
            assert group["selection_id"] == selection(raw)["selection_id"]
            assert len(group["data"]) == 1
            assert group["data"][0]["action_offer_id"] == raw["ref_id"]
    if not mode:
        assert enrich.call_count == 1


@pytest.mark.parametrize("mode", MODES)
def test_disappeared_selected_window_does_not_fall_back_to_other_dates(mode):
    result = analyze(aggregate([offer(9), offer(12), unscheduled()]), [selection(offer(10))], mode)
    assert result["analises"][0]["data"] == []
    if not mode.get("via_api_direta"):
        assert result["analises"][0]["selection_id"] == selection(offer(10))["selection_id"]


@pytest.mark.parametrize("stage", ["collection", "details"])
def test_failed_analysis_keeps_selected_window_identity(stage):
    flow_fixture.PromocoesDescontoMlFluxoTests.setUpClass()
    selected = selection(offer(9))
    def fail(*args, **kwargs):
        raise HTTPException(status_code=503, detail="Temporarily unavailable")
    with patch.object(analysis, "_obter_cfg_ml", return_value={"user_id": "synthetic"}), \
         patch.object(analysis, "_ml_listar_itens_promocao_com_raw", return_value=([], {}, {})), \
         patch.object(analysis, "_carregar_custos_impostos_cadastro_por_sku_loja", return_value=({}, {})), \
         patch.object(analysis, "_ml_listar_itens_promocao_multistatus_com_raw", side_effect=fail if stage == "collection" else None,
                      return_value=([{"id": ITEM_ID}], {ITEM_ID: offer(9)}, {})), \
         patch.object(analysis, "_ml_buscar_itens_batch", side_effect=fail):
        result = asyncio.run(analysis.analisar_promo_via_api_sem_arquivos(loja="store-a", promocao_a_id="SELLER-A",
            promocoes_b_meta=json.dumps([selected]), client_id="tenant-a"))
    assert result["analises"][0]["selection_id"] == selected["selection_id"]
    assert result["analises"][0]["data"] == []
    assert result["analises"][0]["error"]


def test_listing_is_scoped_to_tenant_store_and_reads_fresh_offer_dates(monkeypatch):
    calls = []
    def cfg(client, store):
        return {"user_id": client + ":" + store}
    def collect(client, store, config, campaign, **kwargs):
        assert config["user_id"] == client + ":" + store
        assert campaign == CAMPAIGN and kwargs["promotion_type"] == "LIGHTNING"
        calls.append((client, store))
        raw = offer(9 if client == "tenant-a" and store == "store-a" else 12)
        return [{"id": ITEM_ID}], {ITEM_ID: raw}, config
    monkeypatch.setattr(analysis, "_obter_cfg_ml", cfg, raising=False)
    monkeypatch.setattr(analysis, "_ml_listar_itens_promocao_multistatus_com_raw", collect, raising=False)
    monkeypatch.setattr(analysis, "_ml_obter_promocoes_item", lambda client, store, config, item, **kwargs: ([], config), raising=False)
    first = analysis.listar_janelas_campanha_lightning(CAMPAIGN, "store-a", "tenant-a")
    other_store = analysis.listar_janelas_campanha_lightning(CAMPAIGN, "store-b", "tenant-a")
    other_tenant = analysis.listar_janelas_campanha_lightning(CAMPAIGN, "store-a", "tenant-b")
    assert first["windows"][0]["selection_id"] == selection(offer(9))["selection_id"]
    assert other_store["windows"][0]["selection_id"] == selection(offer(12))["selection_id"]
    assert other_tenant["windows"] == other_store["windows"]
    assert calls == [("tenant-a", "store-a"), ("tenant-a", "store-b"), ("tenant-b", "store-a")]


@pytest.mark.parametrize("campaign", ["SMART-A", "../LGH-MLB1000", "LGH-MLB1000?x=1", ""])
def test_window_endpoint_rejects_invalid_campaign_before_any_api_call(monkeypatch, campaign):
    monkeypatch.setattr(analysis, "_obter_cfg_ml", lambda *args: pytest.fail("must reject before reading config"), raising=False)
    with pytest.raises(HTTPException) as exc:
        analysis.listar_janelas_campanha_lightning(campaign, "store-a", "tenant-a")
    assert exc.value.status_code == 400


def test_window_endpoint_is_registered_as_read_only():
    from backend.routers.promocoes import create_promocoes_router
    route = next(route for route in create_promocoes_router().routes if route.path == "/api/promo/campanhas/{campaign_id}/janelas")
    assert route.methods == {"GET"}


@pytest.mark.parametrize("reverse", [False, True])
def test_snapshot_completeness_considers_later_dates_before_collection(reverse):
    flow_fixture.PromocoesDescontoMlFluxoTests.setUpClass()
    selections = [{**selection(offer(9)), "active_count": 0, "eligible_count": 1},
                  {**selection(offer(12)), "active_count": 0, "eligible_count": 50}]
    if reverse:
        selections.reverse()
    expected_counts = []
    def collect(*args, **kwargs):
        expected_counts.append(kwargs["expected_min_items"])
        return [{"id": ITEM_ID}], {ITEM_ID: offer(9)}, {}
    with patch.object(analysis, "_obter_cfg_ml", return_value={"user_id": "synthetic"}), \
         patch.object(analysis, "_ml_listar_itens_promocao_com_raw", return_value=([], {}, {})), \
         patch.object(analysis, "_carregar_custos_impostos_cadastro_por_sku_loja", return_value=({}, {})), \
         patch.object(analysis, "_ml_listar_itens_promocao_multistatus_com_raw", side_effect=collect), \
         patch.object(analysis, "_ml_buscar_itens_batch", side_effect=HTTPException(status_code=503, detail="retry later")):
        result = asyncio.run(analysis.analisar_promo_via_api_sem_arquivos(loja="store-a", promocao_a_id="SELLER-A",
            promocoes_b_meta=json.dumps(selections), client_id="tenant-a"))
    assert expected_counts == [51]
    assert len(result["analises"]) == 2
