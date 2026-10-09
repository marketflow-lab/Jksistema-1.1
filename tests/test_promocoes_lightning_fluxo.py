"""Integration regressions for offer windows; synthetic data derived from the reported scenario."""
import copy
import pytest

from tests import test_promocoes_desconto_ml_fluxo as flow_fixture
ITEM_ID = flow_fixture.ITEM_ID
PROMO_A_ID = flow_fixture.PROMO_A_ID
from tests.test_promocoes_participacao_resultados import ml, apply
from backend.services import promocoes_api_participacoes as participation
from backend.services import promocoes_api_jobs as jobs
from backend.services.promocoes_lightning import merge_lightning_raw, lightning_offer_key
from backend.services.promocoes_lightning_acao import (
    expand_lightning_offers, lightning_row_fields, lightning_stock_error,
)

CAMPAIGN = "LGH-MLB1000"


def offer(day=9, price=66.32, status="candidate", item=ITEM_ID, campaign=CAMPAIGN):
    return {
        "promotion_id": campaign, "type": "LIGHTNING", "item_id": item, "status": status,
        "ref_id": f"CANDIDATE-{item}-{day}", "price": price, "original_price": 92.28,
        "start_date": f"2026-10-{day:02d}T12:00:00-03:00",
        "finish_date": f"2026-10-{day+1:02d}T00:00:00-03:00",
        "min_stock": 5, "max_stock": 20,
        "_jk_campaign_id_consultado": campaign, "_jk_item_id_consultado": item,
        "_jk_promotion_type_consultado": "LIGHTNING", "_jk_contexto_promocao_confirmado": True,
    }


def aggregate(offers):
    result = {}
    for raw in offers:
        result = merge_lightning_raw(result, raw, campaign_id=CAMPAIGN, item_id=ITEM_ID)
    return result


@pytest.mark.parametrize("mode", [{}, {"via_api_direta": True}, {"com_arquivos": True}])
def test_three_windows_keep_three_rows_and_exact_action_prices(mode):
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    raw = aggregate([offer(9, 66.32), offer(10, 67.45), offer(12, 68.56)])
    result = harness._executar_fluxo(
        raw, promo_b_id=CAMPAIGN, promo_b_type="LIGHTNING", retornar_resultado=True,
        price_info={"price": 90, "standard_price": 92.28, "original_price": 92.28,
                    "promotion_id": PROMO_A_ID, "promotion_type": "SELLER_CAMPAIGN"}, **mode,
    )
    rows = result["analises"][0]["data"]
    assert len(rows) == 3
    assert len({row["lightning_offer_key"] for row in rows}) == 3
    by_date = {row["lightning_start_date"]: row for row in rows}
    for day, price in ((9, 66.32), (10, 67.45), (12, 68.56)):
        row = by_date[offer(day)["start_date"]]
        assert row["action_deal_price"] == row["deal_price"] == price
        assert row["action_offer_id"] == offer(day)["ref_id"]
        assert row["lightning_action_blocked"] is False
        assert row["action_tecnico_apto"] is True
        assert row["lightning_stock"] == 5


def selected(raw, stock=6):
    return {
        "item_id": ITEM_ID, "deal_price": raw["price"], "client_ref": "selected-date",
        **lightning_row_fields(raw, "LIGHTNING", CAMPAIGN, ITEM_ID),
        "lightning_stock": stock,
    }


def fresh(monkeypatch, records):
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(records), cfg), raising=False)


def test_multiple_live_windows_send_selected_offer(ml, monkeypatch):
    records = [offer(9), offer(10), offer(12)]
    fresh(monkeypatch, records)
    row = selected(records[1])
    ml.response = (201, {**records[1], "status": "pending"})
    result = apply([row], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert result["detalhes"][0]["lightning_start_date"] == records[1]["start_date"]
    assert len(ml.calls) == 1
    assert ml.calls[0][4]["json"] == {"promotion_type": "LIGHTNING", "deal_price": 66.32, "stock": 6}


@pytest.mark.parametrize("stock", [None, 0, -1, 4, 21, 5.5, True, "NaN", "Infinity"])
def test_reserved_stock_invalid_never_posts(ml, monkeypatch, stock):
    raw = offer()
    fresh(monkeypatch, [raw])
    result = apply([selected(raw, stock)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_single_window_posts_official_body_and_confirms_matching_window(ml, monkeypatch):
    raw = offer()
    fresh(monkeypatch, [raw])
    ml.response = (201, {**raw, "status": "pending"})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert result["detalhes"][0]["lightning_start_date"] == raw["start_date"]
    assert len(ml.calls) == 1
    assert ml.calls[0][4]["json"] == {"promotion_type": "LIGHTNING", "deal_price": 66.32, "stock": 6}


@pytest.mark.parametrize("response", [{}, offer(10), {"success": True}])
def test_success_without_selected_window_confirmation_is_unknown(ml, monkeypatch, response):
    raw = offer()
    fresh(monkeypatch, [raw])
    ml.response = (201, response)
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "unknown"
    assert len(ml.calls) == 1


@pytest.mark.parametrize("changed", [offer(10), offer(9, campaign="OTHER"), offer(9, item="MLB888777666")])
def test_stale_or_foreign_window_never_posts(ml, monkeypatch, changed):
    fresh(monkeypatch, [changed])
    result = apply([selected(offer())], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_existing_other_window_does_not_shortcut_selected_candidate(ml, monkeypatch):
    active = offer(9, status="started")
    active["ref_id"] = f"OFFER-{ITEM_ID}-9"
    candidate = offer(10)
    fresh(monkeypatch, [active, candidate])
    ml.eligibility = {"success": True, "found": True, "already_participating": True}
    ml.response = (201, {**candidate, "status": "pending"})
    result = apply([selected(candidate)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert len(ml.calls) == 1


def test_selected_already_pending_window_has_independent_result(ml, monkeypatch):
    raw = offer(status="pending")
    raw["ref_id"] = f"OFFER-{ITEM_ID}-9"
    fresh(monkeypatch, [raw, offer(10)])
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "already_participating"
    assert ml.calls == []


def test_auto_skips_invalid_window_and_retains_valid_offer_identity():
    rows = []
    for day, blocked in ((9, True), (10, False)):
        row = {"MLB": ITEM_ID, "Ação": "Participar", "Participar ou não": "Participar",
               "action_tecnico_apto": True, "action_financeiro_exato": True,
               "action_promotion_id": CAMPAIGN, "action_deal_price": 66.32,
               **lightning_row_fields(offer(day), "LIGHTNING", CAMPAIGN, ITEM_ID)}
        row.update(lightning_action_blocked=blocked, lightning_stock=6)
        rows.append(row)
    payload = jobs._promo_automacao_montar_participacoes({
        "analises": [{"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING", "data": rows}]
    }, {"loja": "store-a"})
    assert len(payload["promocoes"][0]["items"]) == 1
    assert payload["promocoes"][0]["items"][0]["lightning_offer_key"] == rows[1]["lightning_offer_key"]
    assert payload["promocoes"][0]["items"][0]["lightning_stock"] == 6


def test_official_nested_stock_and_campaign_id_shape(ml, monkeypatch):
    raw = offer()
    raw["id"] = CAMPAIGN
    raw.pop("item_id")
    raw.pop("promotion_id")
    raw.pop("min_stock")
    raw.pop("max_stock")
    raw["stock"] = {"min": 5, "max": 20}
    fresh(monkeypatch, [raw])
    ml.response = (201, {**raw, "status": "pending"})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert ml.calls[0][4]["json"]["stock"] == 6


@pytest.mark.parametrize("field,value", [("item_id", "MLB888777666"), ("promotion_id", "OTHER"),
                                         ("id", "LGH-OTHER"), ("type", "SMART")])
def test_success_body_with_wrong_scope_is_unknown(ml, monkeypatch, field, value):
    raw = offer()
    fresh(monkeypatch, [raw])
    ml.response = (201, {**raw, field: value})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "unknown"


def test_equivalent_timezones_confirm_same_window(ml, monkeypatch):
    raw = offer()
    fresh(monkeypatch, [raw])
    response = {**raw, "status": "pending", "start_date": "2026-10-09T15:00:00Z", "finish_date": "2026-10-10T03:00:00Z"}
    ml.response = (201, response)
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "applied"


def test_official_datetimes_without_timezone_preserve_unique_window(ml, monkeypatch):
    raw = offer()
    raw["start_date"] = "2026-10-09T12:00:00"
    raw["finish_date"] = "2026-10-10T00:00:00"
    fresh(monkeypatch, [raw])
    row = selected(raw)
    assert row["lightning_action_blocked"] is False
    assert row["lightning_start_date"] == raw["start_date"]
    ml.response = (201, {**raw, "status": "pending"})
    assert apply([row], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "applied"
    assert len(ml.calls) == 1



def test_public_price_only_response_requires_fresh_window_confirmation(ml, monkeypatch):
    raw = offer()
    pending = {**raw, "status": "pending", "ref_id": f"OFFER-{ITEM_ID}-9"}
    snapshots = iter([[raw], [pending]])
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(next(snapshots)), cfg), raising=False)
    ml.response = (201, {"price": 66.32, "original_price": 92.28})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert len(ml.calls) == 1


def test_price_only_response_with_other_pending_window_stays_unknown(ml, monkeypatch):
    raw = offer()
    pending = offer(10, status="pending")
    pending["ref_id"] = f"OFFER-{ITEM_ID}-10"
    snapshots = iter([[raw], [pending]])
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(next(snapshots)), cfg), raising=False)
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "unknown"
    assert len(ml.calls) == 1


def test_wrong_returned_price_never_declares_success_or_retries(ml, monkeypatch):
    raw = offer()
    fresh(monkeypatch, [raw])
    ml.response = (201, {"price": 60, **{key: value for key, value in raw.items() if key != "price"}})
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "unknown"
    assert len(ml.calls) == 1


def test_candidate_without_offer_id_uses_window_identity(ml, monkeypatch):
    raw = offer()
    raw.pop("ref_id")
    fresh(monkeypatch, [raw])
    ml.response = (201, {**raw, "status": "pending"})
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "applied"


def test_missing_campaign_provenance_blocks_item_get(ml, monkeypatch):
    raw = offer()
    raw.pop("promotion_id")
    fresh(monkeypatch, [raw])
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_contradictory_response_type_cannot_be_saved_by_fresh_get(ml, monkeypatch):
    raw = offer()
    pending = {**raw, "status": "pending", "ref_id": f"OFFER-{ITEM_ID}-9"}
    snapshots = iter([[raw], [pending]])
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(next(snapshots)), cfg), raising=False)
    ml.response = (201, {"price": 66.32, "type": "SMART"})
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "unknown"
    assert len(ml.calls) == 1


def test_export_contains_all_window_identities_without_changing_standard_columns():
    from backend.services import promocoes_api_analise as analysis
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    rows = [{"MLB": ITEM_ID, "Campanha ML": CAMPAIGN,
             **lightning_row_fields(offer(day), "LIGHTNING", CAMPAIGN, ITEM_ID)}
            for day in (9, 10, 12)]
    normal = analysis._build_df_planilha_analise_promo(rows)
    exported = analysis._build_df_planilha_analise_promo(rows, destacar_estimativas=True)
    assert "Oferta relampago - inicio" not in normal.columns
    assert list(exported["Oferta relampago - inicio"]) == [offer(day)["start_date"] for day in (9, 10, 12)]
    assert len(set(exported["Oferta relampago - identidade"])) == 3


def test_item_lookup_adds_missing_windows_once_per_distinct_item(monkeypatch):
    from backend.services import promocoes_api_analise as analysis
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    records = [offer(day) for day in (9, 10, 12)]
    for raw in records:
        raw["id"] = CAMPAIGN
        raw.pop("item_id")
    calls = []
    def lookup(client, store, cfg, item, **kwargs):
        calls.append((client, store, item, kwargs))
        return copy.deepcopy(records), cfg
    monkeypatch.setattr(analysis, "_ml_obter_promocoes_item", lookup)
    enriched, snapshots = analysis._promo_enriquecer_janelas_lightning(
        "tenant-a", "store-a", {}, CAMPAIGN, [ITEM_ID, ITEM_ID],
        {ITEM_ID: aggregate([offer(9)])})
    assert len(expand_lightning_offers([ITEM_ID], enriched, "LIGHTNING", CAMPAIGN)) == 3
    assert calls == [("tenant-a", "store-a", ITEM_ID, {"force_refresh": True})]
    assert len(snapshots[ITEM_ID][0]) == 3


def test_failed_item_lookup_preserves_campaign_windows(monkeypatch):
    from backend.services import promocoes_api_analise as analysis
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    def fail(*args, **kwargs):
        raise RuntimeError("fixture authentication failure")
    monkeypatch.setattr(analysis, "_ml_obter_promocoes_item", fail)
    original = {ITEM_ID: aggregate([offer(day) for day in (9, 10, 12)])}
    enriched, snapshots = analysis._promo_enriquecer_janelas_lightning(
        "tenant-a", "store-a", {}, CAMPAIGN, [ITEM_ID], original)
    assert enriched == original
    assert snapshots[ITEM_ID][0] is None


def test_item_lookup_discards_foreign_campaign_and_item(monkeypatch):
    from backend.services import promocoes_api_analise as analysis
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    records = [offer(10, campaign="LGH-OTHER"), offer(12, item="MLB9999999999")]
    monkeypatch.setattr(analysis, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(records), cfg))
    enriched, _ = analysis._promo_enriquecer_janelas_lightning(
        "tenant-a", "store-a", {}, CAMPAIGN, [ITEM_ID], {ITEM_ID: aggregate([offer(9)])})
    expanded = expand_lightning_offers([ITEM_ID], enriched, "LIGHTNING", CAMPAIGN)
    assert len(expanded) == 1
    assert expanded[0][2]["start_date"] == offer(9)["start_date"]


@pytest.mark.parametrize("current_price", [66.33, 71.32])
def test_changed_invitation_price_never_sends_stale_selection(ml, monkeypatch, current_price):
    original = offer()
    current = dict(original, price=current_price)
    fresh(monkeypatch, [current])
    result = apply([selected(original)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert "preco desta oferta mudou" in result["detalhes"][0]["error"]
    assert ml.calls == []


def test_action_price_is_checked_instead_of_fallback_price(ml, monkeypatch):
    current = offer()
    fresh(monkeypatch, [current])
    row = dict(selected(current), action_deal_price=60)
    result = apply([row], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_fresh_item_lookup_updates_price_and_aliases_missing_reference(monkeypatch):
    from backend.services import promocoes_api_analise as analysis
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    anonymous = offer(price=60)
    anonymous.pop("ref_id")
    current = offer(price=66.32)
    monkeypatch.setattr(analysis, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: ([copy.deepcopy(current)], cfg))
    enriched, _ = analysis._promo_enriquecer_janelas_lightning(
        "tenant-a", "store-a", {}, CAMPAIGN, [ITEM_ID], {ITEM_ID: aggregate([anonymous])})
    expanded = expand_lightning_offers([ITEM_ID], enriched, "LIGHTNING", CAMPAIGN)
    assert len(expanded) == 1
    assert expanded[0][2]["price"] == 66.32
    assert expanded[0][1] == lightning_offer_key(current, campaign_id=CAMPAIGN, item_id=ITEM_ID)


@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_item_lookup_shape_does_not_block_unique_invitation(ml, monkeypatch, reverse):
    current = offer()
    anonymous = dict(current)
    anonymous.pop("ref_id")
    records = [anonymous, current] if not reverse else [current, anonymous]
    fresh(monkeypatch, records)
    ml.response = (201, {**current, "status": "pending"})
    result = apply([selected(current)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied"
    assert len(ml.calls) == 1


def test_reference_bearing_record_supplies_price_when_aliasing_anonymous():
    anonymous = offer(price=60)
    anonymous.pop("ref_id")
    identified = offer(price=66.32)
    combined = aggregate([anonymous, identified])
    expanded = expand_lightning_offers([ITEM_ID], {ITEM_ID: combined}, "LIGHTNING", CAMPAIGN)
    assert len(expanded) == 1
    assert expanded[0][2]["price"] == 66.32


@pytest.mark.parametrize("mode", [{}, {"via_api_direta": True}, {"com_arquivos": True}])
def test_approved_analysis_queues_three_dates_with_reserved_stock(mode):
    harness = flow_fixture.PromocoesDescontoMlFluxoTests(methodName="runTest")
    harness.setUpClass()
    raw = aggregate([offer(day) for day in (9, 10, 12)])
    result = harness._executar_fluxo(
        raw, promo_b_id=CAMPAIGN, promo_b_type="LIGHTNING", retornar_resultado=True,
        margem_minima=15, margem_tolerancia=5, custo=2,
        fee_por_preco={66.32: {"ad_cost": 11.27}},
        frete_b_data={"shipping_cost": 0}, **mode,
    )
    rows = result["analises"][0]["data"]
    assert len(rows) == 3
    assert all(row["action_financeiro_estimado"] is True for row in rows)
    assert all(row["action_financeiro_exato"] is False for row in rows)
    payload = jobs._promo_automacao_montar_participacoes(result, "store-a")
    items = payload["promocoes"][0]["items"]
    assert len(items) == 3
    assert len({row["lightning_offer_key"] for row in items}) == 3
    assert [row["lightning_stock"] for row in items] == [5, 5, 5]
    assert [row["deal_price"] for row in items] == [66.32, 66.32, 66.32]
    if mode.get("via_api_direta"):
        direct = jobs._promo_automacao_montar_participacoes(result["analises"][0], "store-a")
        assert direct["promocoes"][0]["promotion_id"] == CAMPAIGN
        assert direct["promocoes"][0]["promotion_type"] == "LIGHTNING"
        assert direct["promocoes"][0]["items"] == items


def test_three_equal_price_windows_are_sent_sequentially_and_confirmed_independently(ml, monkeypatch):
    records = [offer(day) for day in (9, 10, 12)]
    observed = copy.deepcopy(records)
    events = []
    original_request = participation._ml_api_request
    def lookup(client, store, cfg, item, **kwargs):
        assert kwargs["force_refresh"] is True
        events.append("GET")
        return copy.deepcopy(observed), cfg
    def send(client, store, cfg, method, url, **kwargs):
        if method != "POST":
            return original_request(client, store, cfg, method, url, **kwargs)
        index = len(ml.calls)
        events.append(f"POST-{(9, 10, 12)[index]}")
        response, cfg = original_request(client, store, cfg, method, url, **kwargs)
        observed[index]["status"] = "pending"
        observed[index]["ref_id"] = f"OFFER-{ITEM_ID}-{index + 90}"
        return response, cfg
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item", lookup, raising=False)
    monkeypatch.setattr(participation, "_ml_api_request", send)
    ml.response = [(201, {"price": 66.32, "original_price": 92.28}) for _ in records]
    result = apply([dict(selected(raw, 5), client_ref=f"date-{day}")
                    for day, raw in zip((9, 10, 12), records)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["total_sucesso"] == 3
    assert [row["outcome"] for row in result["detalhes"]] == ["applied"] * 3
    assert [row["lightning_start_date"] for row in result["detalhes"]] == [raw["start_date"] for raw in records]
    assert events == ["GET", "POST-9", "GET", "GET", "POST-10", "GET", "GET", "POST-12", "GET"]
    assert [call[4]["json"] for call in ml.calls] == [
        {"promotion_type": "LIGHTNING", "deal_price": 66.32, "stock": 5}
    ] * 3


def test_changed_candidate_reference_recognizes_only_its_pending_window(ml, monkeypatch):
    original = offer(9)
    pending = dict(original, status="pending", ref_id=f"OFFER-{ITEM_ID}-91")
    fresh(monkeypatch, [pending, offer(10)])
    ml.response = (201, {**offer(10), "status": "pending"})
    result = apply([selected(original), selected(offer(10))], campaign=CAMPAIGN, kind="LIGHTNING")
    assert [row["outcome"] for row in result["detalhes"]] == ["already_participating", "applied"]
    assert len(ml.calls) == 1


@pytest.mark.parametrize("confirmed_day,expected", [(9, "already_participating"), (10, "rejected")])
def test_already_response_rechecks_exact_selected_window(ml, monkeypatch, confirmed_day, expected):
    original = offer(9)
    pending = offer(confirmed_day, status="pending")
    pending["ref_id"] = f"OFFER-{ITEM_ID}-{confirmed_day + 90}"
    after = [pending] if confirmed_day == 9 else [original, pending]
    snapshots = iter([[original, offer(10)], after])
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item",
        lambda client, store, cfg, item, **kwargs: (copy.deepcopy(next(snapshots)), cfg), raising=False)
    ml.response = (400, {"message": "Already participating in promotion"})
    result = apply([selected(original)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == expected
    assert len(ml.calls) == 1
    assert all(call[2] == "POST" for call in ml.calls)


def test_one_rejected_window_does_not_stop_other_approved_dates(ml, monkeypatch):
    records = [offer(day) for day in (9, 10, 12)]
    fresh(monkeypatch, records)
    ml.response = [
        (400, {"message": "FINAL_PRICE_INVALID"}),
        (201, {**records[1], "status": "pending"}),
        (201, {**records[2], "status": "pending"}),
    ]
    result = apply([selected(raw) for raw in records], campaign=CAMPAIGN, kind="LIGHTNING")
    assert [row["outcome"] for row in result["detalhes"]] == ["rejected", "applied", "applied"]
    assert result["total_sucesso"] == 2
    assert result["total_falha"] == 1
    assert len(ml.calls) == 3


@pytest.mark.parametrize("minimum,maximum,expected", [(2, 8, 2), (5, 20, 5), (0, 20, None), (5, 4, None)])
def test_analysis_initial_stock_is_valid_minimum_per_offer(minimum, maximum, expected):
    raw = dict(offer(), min_stock=minimum, max_stock=maximum, _jk_lightning_offer_count=3)
    fields = lightning_row_fields(raw, "LIGHTNING", CAMPAIGN, ITEM_ID)
    assert fields["lightning_stock"] == expected
    assert fields["lightning_action_blocked"] is (expected is None)


def test_lightning_pending_nested_status_is_recognized(ml, monkeypatch):
    raw = offer()
    fresh(monkeypatch, [raw, offer(10)])
    ml.response = (201, {**raw, "status": {"id": "pending"}})
    assert apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")["detalhes"][0]["outcome"] == "applied"


def test_negative_analysis_decision_keeps_lightning_out_of_automatic_queue():
    rows = [
        {"MLB": ITEM_ID, "Ação": action, "action_tecnico_apto": True,
         "action_financeiro_exato": False, "action_financeiro_estimado": True,
         "action_promotion_id": CAMPAIGN, "action_deal_price": 66.32,
         **lightning_row_fields(offer(day), "LIGHTNING", CAMPAIGN, ITEM_ID)}
        for day, action in ((9, "Participar"), (10, "Não participar"), (12, "Participar"))
    ]
    payload = jobs._promo_automacao_montar_participacoes({
        "analises": [{"promo_b_id": CAMPAIGN, "promo_b_type": "LIGHTNING", "data": rows}]
    }, "store-a")
    assert [row["lightning_start_date"] for row in payload["promocoes"][0]["items"]] == [
        offer(9)["start_date"], offer(12)["start_date"]
    ]


# The real API exposes candidate references and stock bounds before assigning
# a calendar. Confirmation must still come from scoped, fresh reads.
def undated_offer(**changes):
    raw = offer()
    raw.pop("start_date")
    raw.pop("finish_date")
    raw.update(changes)
    return raw


def observed_pending(day=10, **changes):
    raw = offer(day, status="pending")
    raw["ref_id"] = f"OFFER-{ITEM_ID}-{day}"
    raw.update(changes)
    return raw


def changing_reads(monkeypatch, before, after):
    events = []
    def lookup(client, store, cfg, item, **kwargs):
        assert item == ITEM_ID and kwargs == {"force_refresh": True}
        events.append("GET")
        records = before if len(events) == 1 else after
        return copy.deepcopy(records), cfg
    monkeypatch.setattr(participation, "_ml_obter_promocoes_item", lookup, raising=False)
    return events


def test_undated_candidate_exposes_pending_schedule_and_stock():
    raw = undated_offer()
    row = selected(raw)
    assert row["lightning_schedule_pending"] is True
    assert row["lightning_action_blocked"] is False
    assert row["lightning_start_date"] == row["lightning_finish_date"] == ""
    assert row["lightning_stock_min"] == 5 and row["lightning_stock_max"] == 20


@pytest.mark.parametrize("changes", [
    {"price": True}, {"price": False}, {"price": 0}, {"price": "NaN"},
    {"ref_id": f"CANDIDATE-MLB888777666-9"}, {"ref_id": ""},
    {"_jk_contexto_promocao_confirmado": False},
    {"start_date": 0}, {"start_date": True}, {"start_date": "invalid"},
    {"start_date": offer()["start_date"]}, {"offer": {"finish_date": 1}},
    {"min_stock": True}, {"min_stock": None}, {"max_stock": 4},
])
def test_unproven_undated_invitation_remains_blocked(changes):
    row = selected(undated_offer(**changes))
    assert row["lightning_schedule_pending"] is False
    assert row["lightning_action_blocked"] is True


@pytest.mark.parametrize("changes", [{}, {"start_date": None, "finish_date": " "}])
def test_undated_invite_posts_then_confirms_new_dated_participation(ml, monkeypatch, changes):
    raw = undated_offer(**changes)
    pending = observed_pending()
    events = changing_reads(monkeypatch, [raw], [pending])
    original_request = participation._ml_api_request
    def request(*args, **kwargs):
        if args[3] == "POST":
            events.append("POST")
        return original_request(*args, **kwargs)
    monkeypatch.setattr(participation, "_ml_api_request", request)
    ml.response = (201, {"price": 66.32, "original_price": 92.28})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    detail = result["detalhes"][0]
    assert events == ["GET", "POST", "GET"]
    assert detail["outcome"] == "applied" and detail["status"] == "pending"
    assert detail["lightning_schedule_pending"] is False
    assert detail["lightning_start_date"] == pending["start_date"]
    assert detail["lightning_finish_date"] == pending["finish_date"]
    assert detail["client_ref"] == "selected-date"
    assert ml.calls[0][4]["json"] == {"promotion_type": "LIGHTNING", "deal_price": 66.32, "stock": 6}


@pytest.mark.parametrize("changes", [
    {"price": 65.32}, {"price": True}, {"min_stock": 6}, {"max_stock": 21},
    {"start_date": offer()["start_date"]}, {"start_date": 0},
    {"status": "pending"}, {"type": "DEAL"}, {"item_id": "MLB888777666"},
    {"promotion_id": "LGH-OTHER"}, {"seller_id": "222"},
])
def test_fresh_undated_invite_changes_block_without_post(ml, monkeypatch, changes):
    raw = undated_offer()
    fresh(monkeypatch, [undated_offer(**changes)])
    row = selected(raw)
    row["lightning_schedule_pending"] = True  # UI assertions cannot override GET.
    result = apply([row], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked"
    assert ml.calls == []


def test_undated_missing_invite_does_not_claim_unrelated_existing_window(ml, monkeypatch):
    raw = undated_offer()
    fresh(monkeypatch, [observed_pending()])
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked" and ml.calls == []


def test_distinct_undated_candidates_at_same_price_are_ambiguous(ml, monkeypatch):
    raw = undated_offer()
    other = undated_offer(ref_id=f"CANDIDATE-{ITEM_ID}-other")
    fresh(monkeypatch, [raw, other])
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked" and ml.calls == []


@pytest.mark.parametrize("changes", [{"price": 65.32}, {"max_stock": 21}, {"status": "pending"}])
def test_duplicate_reference_with_conflicting_observations_blocks(ml, monkeypatch, changes):
    raw = undated_offer()
    fresh(monkeypatch, [raw, undated_offer(**changes)])
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "blocked" and ml.calls == []


def test_identical_duplicate_candidate_observations_have_one_submission(ml, monkeypatch):
    raw = undated_offer()
    changing_reads(monkeypatch, [raw, copy.deepcopy(raw)], [observed_pending()])
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied" and len(ml.calls) == 1


@pytest.mark.parametrize("after", [
    [], [undated_offer()], [observed_pending(price=65.32)],
    [observed_pending(), observed_pending(12)],
    [observed_pending(status="candidate")],
    [observed_pending(item_id="MLB888777666")],
    [observed_pending(type="DEAL")], [observed_pending(seller_id="222")],
])
def test_undated_post_without_unique_current_effect_is_unknown(ml, monkeypatch, after):
    raw = undated_offer()
    changing_reads(monkeypatch, [raw], after)
    ml.response = (201, observed_pending())  # Direct body alone cannot confirm it.
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "unknown" and len(ml.calls) == 1


def test_undated_post_ignores_foreign_participations(ml, monkeypatch):
    raw = undated_offer()
    changing_reads(monkeypatch, [raw], [observed_pending(item_id="MLB888777666"),
        observed_pending(promotion_id="LGH-OTHER"), observed_pending()])
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied" and len(ml.calls) == 1


@pytest.mark.parametrize("after", [observed_pending(9), observed_pending(9, ref_id=f"OFFER-{ITEM_ID}-changed")])
def test_previous_participation_cannot_confirm_undated_post(ml, monkeypatch, after):
    raw, previous = undated_offer(), observed_pending(9)
    changing_reads(monkeypatch, [raw, previous], [after])
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "unknown" and len(ml.calls) == 1


def test_new_pending_window_is_distinguished_from_previous_participation(ml, monkeypatch):
    raw, previous, new = undated_offer(), observed_pending(9), observed_pending(10)
    changing_reads(monkeypatch, [raw, previous], [previous, new])
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    detail = result["detalhes"][0]
    assert detail["outcome"] == "applied" and detail["lightning_start_date"] == new["start_date"]
    assert len(ml.calls) == 1


def test_undated_already_error_cannot_claim_another_schedule_or_send_put(ml, monkeypatch):
    raw = undated_offer()
    changing_reads(monkeypatch, [raw], [observed_pending()])
    ml.response = (400, {"message": "already participating in promotion"})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "rejected"
    assert [call[2] for call in ml.calls] == ["POST"]


@pytest.mark.parametrize("confirmed", [True, False])
def test_duplicate_selected_invite_never_reposts_even_if_result_unknown(ml, monkeypatch, confirmed):
    raw = undated_offer()
    changing_reads(monkeypatch, [raw], [observed_pending()] if confirmed else [raw])
    ml.response = (201, {"price": 66.32})
    one, two = selected(raw), selected(raw)
    two["client_ref"] = "duplicate-row"
    result = apply([one, two], campaign=CAMPAIGN, kind="LIGHTNING")
    expected = ["applied", "already_participating"] if confirmed else ["unknown", "unknown"]
    assert [detail["outcome"] for detail in result["detalhes"]] == expected
    assert result["detalhes"][1]["client_ref"] == "duplicate-row"
    assert len(ml.calls) == 1


def test_scoped_owner_matches_before_and_after_inclusion(ml, monkeypatch):
    monkeypatch.setattr(participation, "_obter_cfg_ml", lambda client, store: {"scope": (client, store), "user_id": "111"})
    raw = undated_offer(seller_id=111)
    changing_reads(monkeypatch, [raw], [observed_pending(seller_id=111)])
    ml.response = (201, {"price": 66.32})
    result = apply([selected(raw)], campaign=CAMPAIGN, kind="LIGHTNING")
    assert result["detalhes"][0]["outcome"] == "applied" and len(ml.calls) == 1
