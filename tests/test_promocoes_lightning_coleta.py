"""Regressoes de identidade, janelas e paginacao LIGHTNING sem acesso externo."""

import copy
from itertools import permutations
from urllib.parse import parse_qs, urlparse

import backend_api  # noqa: F401 - configura fachadas do runtime
import pytest

from backend.services import mercadolivre_legacy_promocoes as promotions
from backend.services.promocoes_lightning import (
    LIGHTNING_OFFERS_KEY,
    lightning_offer_key,
    lightning_offer_window,
    lightning_offers,
    lightning_window_valid,
    merge_lightning_raw,
)

ITEM = "MLB1234567"
CAMPAIGN = "LGH-MLB1000"


class Response:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.payload = copy.deepcopy(payload or {})

    def json(self):
        return copy.deepcopy(self.payload)


def offer(day, *, status="candidate", offer_id="", item=ITEM, **extra):
    raw = {
        "id": item,
        "status": status,
        "start_date": f"2026-10-{day:02}T12:00:00-03:00",
        "finish_date": f"2026-10-{day + 1:02}T00:00:00-03:00",
        "price": 80.0,
        "original_price": 100.0,
        "stock": {"min": 2, "max": 8},
    }
    if offer_id:
        raw["offer_id"] = offer_id
    raw.update(extra)
    return raw


def collect(monkeypatch, entries, *, promotion_type="LIGHTNING", status="candidate", details=False):
    calls = []

    def request(_client, _store, cfg, method, url, **_kwargs):
        assert method == "GET"
        calls.append(url)
        return Response({"results": entries, "paging": {"total": len(entries)}}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    result = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN,
        promotion_type=promotion_type, buscar_detalhes=details,
        status_promocao=status, status_item_preferencial="",
    )
    return result, calls


def test_three_windows_same_item_are_preserved_without_offer_id(monkeypatch):
    entries = [offer(day) for day in (9, 10, 12)]
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, entries)
    assert len(items) == 1  # O anuncio continua unico; as ofertas nao.
    individual = lightning_offers(raw_by_item[ITEM])
    assert [lightning_offer_window(raw)[0] for raw in individual] == [raw["start_date"] for raw in entries]
    assert len({lightning_offer_key(raw) for raw in individual}) == 3
    assert all(raw["_jk_contexto_promocao_confirmado"] for raw in individual)
    assert len(items[0]["offers"]) == 3
    assert all(LIGHTNING_OFFERS_KEY not in raw for raw in individual)


def test_same_offer_identifier_with_different_windows_does_not_collapse(monkeypatch):
    entries = [offer(day, offer_id=f"CANDIDATE-{ITEM}-SAME") for day in (9, 10, 12)]
    (_items, raw_by_item, _cfg), _calls = collect(monkeypatch, entries)
    assert len(lightning_offers(raw_by_item[ITEM])) == 3


def test_duplicate_same_window_is_deduplicated_and_copies_are_isolated(monkeypatch):
    first = offer(9)
    equivalent = dict(first, start_date="2026-10-09T15:00:00Z", finish_date="2026-10-10T03:00:00+00:00")
    (_items, raw_by_item, _cfg), _calls = collect(monkeypatch, [first, copy.deepcopy(first), equivalent])
    individual = lightning_offers(raw_by_item[ITEM])
    assert len(individual) == 1
    individual[0]["stock"]["min"] = 99
    assert lightning_offers(raw_by_item[ITEM])[0]["stock"]["min"] == 2
    assert first["stock"]["min"] == 2


@pytest.mark.parametrize("cursor_location", ["root", "paging"])
def test_cursor_paginates_short_pages_and_identical_item_ids(monkeypatch, cursor_location):
    calls = []
    tokens = ("next/a+one", "next/two")

    def request(_client, _store, cfg, method, url, **_kwargs):
        assert method == "GET"
        calls.append(url)
        params = parse_qs(urlparse(url).query)
        token = params.get("search_after", [""])[0]
        index = {"": 0, tokens[0]: 1, tokens[1]: 2}[token]
        payload = {"results": [offer((9, 10, 12)[index])], "paging": {"total": 100}}
        if index < 2:
            payload["search_after"] = tokens[index] if cursor_location == "root" else None
            if cursor_location == "paging":
                payload["paging"]["search_after"] = tokens[index]
        return Response(payload), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN,
        promotion_type="LIGHTNING", buscar_detalhes=False,
        status_promocao="candidate", status_item_preferencial="", max_items=10,
    )
    assert len(lightning_offers(raw_by_item[ITEM])) == 3
    assert len(calls) == 3
    assert len(items[0]["offers"]) == 3
    assert "offset" not in parse_qs(urlparse(calls[1]).query)


def test_offset_fallback_does_not_mistake_new_windows_for_repeated_page(monkeypatch):
    calls = []

    def request(_client, _store, cfg, _method, url, **_kwargs):
        calls.append(url)
        offset = int(parse_qs(urlparse(url).query)["offset"][0])
        entries = [offer(9 if offset == 0 else 10) for _ in range(50)]
        return Response({"results": entries, "paging": {"total": 100}}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    _items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN,
        promotion_type="LIGHTNING", buscar_detalhes=False,
        status_promocao="candidate", status_item_preferencial="",
    )
    assert len(lightning_offers(raw_by_item[ITEM])) == 2
    assert len(calls) == 2


def test_repeated_cursor_stops_without_dropping_new_offer(monkeypatch):
    calls = []

    def request(_client, _store, cfg, _method, url, **_kwargs):
        calls.append(url)
        assert len(calls) <= 2
        return Response({"results": [offer(8 + len(calls))], "search_after": "same-token"}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    _items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN,
        promotion_type="LIGHTNING", buscar_detalhes=False,
        status_promocao="candidate", status_item_preferencial="",
    )
    assert len(lightning_offers(raw_by_item[ITEM])) == 2
    assert len(calls) == 2


def test_repeated_offer_with_advancing_cursor_keeps_later_window(monkeypatch):
    calls = []

    def request(_client, _store, cfg, _method, url, **_kwargs):
        calls.append(url)
        assert len(calls) <= 3
        payload = {"results": [offer(12 if len(calls) == 3 else 9)]}
        if len(calls) < 3:
            payload["search_after"] = f"cursor-{len(calls)}"
        return Response(payload), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    _items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN,
        promotion_type="LIGHTNING", buscar_detalhes=False,
        status_promocao="candidate", status_item_preferencial="",
    )
    assert len(lightning_offers(raw_by_item[ITEM])) == 2
    assert len(calls) == 3


@pytest.mark.parametrize("status", ["started", "pending"])
def test_lightning_active_and_programmed_windows_without_offer_id_are_preserved(monkeypatch, status):
    (_items, raw_by_item, _cfg), _calls = collect(monkeypatch, [offer(9, status=status)], status=status)
    individual = lightning_offers(raw_by_item[ITEM])
    assert len(individual) == 1
    assert individual[0]["status"] == status
    assert individual[0]["_jk_contexto_promocao_confirmado"] is True


@pytest.mark.parametrize("extra", [
    {"promotion_id": "LGH-OTHER"},
    {"promotion_type": "DEAL"},
    {"item_id": "MLB7654321"},
    {"offer_id": "CANDIDATE-MLB7654321-9"},
])
def test_conflicting_campaign_item_type_and_offer_are_rejected(monkeypatch, extra):
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, [offer(9, **extra)])
    assert items == []
    assert raw_by_item == {}


@pytest.mark.parametrize("marker", [LIGHTNING_OFFERS_KEY, "_jk_lightning_observations"])
def test_external_internal_offer_list_cannot_inject_another_window(monkeypatch, marker):
    contaminated = offer(9, **{marker: [offer(12, promotion_id="LGH-OTHER")]})
    (_items, raw_by_item, _cfg), _calls = collect(monkeypatch, [contaminated])
    individual = lightning_offers(raw_by_item[ITEM])
    assert len(individual) == 1
    assert individual[0]["start_date"] == offer(9)["start_date"]


def test_other_campaign_types_keep_legacy_shape_and_duplicate_selection(monkeypatch):
    entries = [offer(day, offer_id=f"CANDIDATE-{ITEM}-{day}") for day in (9, 10, 12)]
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, entries, promotion_type="SMART")
    assert items == [{"id": ITEM}]
    assert LIGHTNING_OFFERS_KEY not in raw_by_item[ITEM]
    assert raw_by_item[ITEM]["start_date"] == entries[0]["start_date"]


def test_other_type_still_rejects_started_offer_without_identifier(monkeypatch):
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, [offer(9, status="started")], promotion_type="SMART", status="started")
    assert items == []
    assert raw_by_item == {}


def test_invalid_active_window_without_identifier_cannot_be_confirmed(monkeypatch):
    raw = offer(9, status="started", finish_date="2026-10-09T01:00:00-03:00")
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, [raw], status="started")
    assert items == []
    assert raw_by_item == {}


def test_details_fetch_does_not_remove_offer_lists(monkeypatch):
    monkeypatch.setattr(promotions, "_ml_buscar_itens_batch", lambda _client, _store, cfg, ids, **_kwargs: ([{"id": ITEM, "title": "Fixture"}], cfg))
    (items, raw_by_item, _cfg), _calls = collect(monkeypatch, [offer(day) for day in (9, 10, 12)], details=True)
    assert items[0]["title"] == "Fixture"
    assert len(items[0]["offers"]) == 3
    assert len(lightning_offers(raw_by_item[ITEM])) == 3


def test_multistatus_preserves_other_windows_and_merges_only_same_identity(monkeypatch):
    pending = offer(7, status="pending")
    started = offer(8, status="started")
    candidates = [offer(day) for day in (9, 10, 12)]
    all_offers = [pending, started] + candidates

    def listar(_client, _store, cfg, _campaign, **kwargs):
        status = kwargs.get("status_promocao")
        entries = {"pending": [pending], "started": [started], "candidate": candidates,
                   "eligible": [dict(candidates[0], status="eligible")], "": all_offers}[status]
        aggregate = {}
        for entry in entries:
            aggregate = merge_lightning_raw(aggregate, entry, campaign_id=CAMPAIGN, item_id=ITEM,
                                            merge_same_offer=promotions._promo_mesclar_raw_multistatus)
        return [{"id": ITEM}], {ITEM: aggregate}, cfg

    monkeypatch.setattr(promotions, "_ml_listar_itens_promocao_com_raw", listar)
    items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_multistatus_com_raw(
        "tenant-test", "store-test", {}, CAMPAIGN, promotion_type="LIGHTNING",
    )
    individual = lightning_offers(raw_by_item[ITEM])
    assert len(individual) == 5
    assert len(items[0]["offers"]) == 5
    assert [raw["status"] for raw in individual] == ["pending", "started", "candidate", "candidate", "candidate"]
    assert raw_by_item[ITEM]["status"] == "pending"


def test_heavy_fallback_preserves_all_item_windows(monkeypatch):
    entries = [dict(offer(day), id=CAMPAIGN, type="LIGHTNING") for day in (9, 10, 12)]
    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", lambda *_args, **_kwargs: (Response(status=404), _args[2]))
    monkeypatch.setattr(promotions, "_ml_listar_ids_anuncios_ativos", lambda *_args, **_kwargs: ([ITEM], _args[2]))
    monkeypatch.setattr(promotions, "_ml_obter_promocoes_item", lambda *_args, **_kwargs: (copy.deepcopy(entries), {}))
    items, raw_by_item, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        "tenant-test", "store-test", {"user_id": "seller-test"}, CAMPAIGN,
        promotion_type="LIGHTNING", buscar_detalhes=False, usar_fallback_pesado=True,
    )
    assert len(lightning_offers(raw_by_item[ITEM])) == 3
    assert len(items[0]["offers"]) == 3


def test_identity_separates_campaign_item_and_local_vs_explicit_timezone():
    raw = offer(9)
    base = lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM)
    assert lightning_offer_key(raw, campaign_id="LGH-OTHER", item_id=ITEM) != base
    assert lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id="MLB7654321") != base
    local = dict(raw, start_date="2026-10-09T12:00:00", finish_date="2026-10-10T00:00:00")
    assert lightning_offer_key(local, campaign_id=CAMPAIGN, item_id=ITEM) != base
    assert lightning_window_valid(local)
    assert not lightning_window_valid(dict(raw, finish_date="2026-10-10T00:00:00"))
    assert not lightning_window_valid(dict(raw, start_date="invalid"))


def test_legacy_raw_is_available_as_one_individual_offer_without_list():
    raw = offer(9)
    result = lightning_offers(raw)
    assert result == [raw]
    result[0]["stock"]["max"] = 999
    assert raw["stock"]["max"] == 8


def test_date_only_does_not_claim_valid_window():
    assert not lightning_window_valid({"start_date": "2026-10-09", "finish_date": "2026-10-10"})


def test_missing_reference_aliases_only_one_confirmed_offer_in_window():
    anonymous = offer(9)
    confirmed = offer(9, offer_id=f"CANDIDATE-{ITEM}-9")
    combined = merge_lightning_raw(anonymous, confirmed, campaign_id=CAMPAIGN, item_id=ITEM)
    individual = lightning_offers(combined)
    assert len(individual) == 1
    assert lightning_offer_key(individual[0], campaign_id=CAMPAIGN, item_id=ITEM) == lightning_offer_key(
        confirmed, campaign_id=CAMPAIGN, item_id=ITEM)
    reverse = merge_lightning_raw(confirmed, anonymous, campaign_id=CAMPAIGN, item_id=ITEM)
    assert len(lightning_offers(reverse)) == 1


def test_different_explicit_references_in_same_window_remain_separate():
    first = offer(9, offer_id=f"CANDIDATE-{ITEM}-1")
    second = offer(9, offer_id=f"CANDIDATE-{ITEM}-2")
    combined = merge_lightning_raw(first, second, campaign_id=CAMPAIGN, item_id=ITEM)
    assert len(lightning_offers(combined)) == 2



def merge_sequence(records, *, callback=promotions._promo_mesclar_raw_multistatus):
    combined = {}
    for raw in records:
        combined = merge_lightning_raw(combined, raw, campaign_id=CAMPAIGN, item_id=ITEM,
                                      merge_same_offer=callback)
    return lightning_offers(combined)


@pytest.mark.parametrize("start,finish", [
    ("2026-10-12T12:00:00", "2026-10-12T23:59:59"),
    ("2026-10-12T00:00:00", "2026-10-12T11:59:59"),
])
def test_reported_pending_reference_with_and_without_dates_is_one_offer(start, finish):
    # Forma dos dois endpoints do caso reportado; identidades comerciais ficticias.
    dated = {"id": ITEM, "ref_id": f"OFFER-{ITEM}-REDACTED", "status": "pending",
             "start_date": start, "finish_date": finish, "stock": {"min": 2, "max": 8}}
    undated = {"id": CAMPAIGN, "item_id": ITEM, "type": "LIGHTNING",
               "ref_id": dated["ref_id"], "status": "pending", "price": 80.0}
    original = copy.deepcopy([dated, undated])
    for sequence in permutations([dated, undated]):
        result = merge_sequence(sequence)
        assert len(result) == 1
        assert lightning_offer_window(result[0]) == (start, finish)
        assert result[0]["status"] == "pending"
        assert result[0]["price"] == 80.0
        assert result[0]["stock"] == {"min": 2, "max": 8}
        assert lightning_window_valid(result[0])
        combined = merge_lightning_raw(*sequence, campaign_id=CAMPAIGN, item_id=ITEM,
                                      merge_same_offer=promotions._promo_mesclar_raw_multistatus)
        assert lightning_offer_window(combined) == (start, finish)
        assert combined["status"] == "pending"
    assert [dated, undated] == original


@pytest.mark.parametrize("status", ["started", "active", "paused", "finished", "cancelled"])
@pytest.mark.parametrize("old_status", ["pending", "candidate"])
def test_same_reference_does_not_reactivate_from_an_undated_response(status, old_status):
    dated = offer(12, offer_id=f"OFFER-{ITEM}-1", status=status)
    old = {"id": ITEM, "offer_id": dated["offer_id"], "status": old_status}
    for sequence in permutations([dated, old]):
        # Enriquecimento tambem pode sobrepor a resposta nova ao callback legado.
        result = merge_sequence(sequence, callback=lambda previous, fresh: {
            **promotions._promo_mesclar_raw_multistatus(previous, fresh), **fresh,
        })
        assert len(result) == 1
        assert result[0]["status"] == status
        assert lightning_offer_window(result[0]) == lightning_offer_window(dated)


@pytest.mark.parametrize("missing", ["start_date", "finish_date"])
def test_compatible_partial_boundary_is_filled_without_inventing_dates(missing):
    dated = offer(12, offer_id=f"OFFER-{ITEM}-1", status="pending")
    partial = dict(dated)
    partial.pop(missing)
    for sequence in permutations([dated, partial]):
        result = merge_sequence(sequence)
        assert len(result) == 1
        assert lightning_offer_window(result[0]) == lightning_offer_window(dated)


@pytest.mark.parametrize("boundary,value", [
    ("start_date", "2026-10-13T12:00:00-03:00"),
    ("finish_date", "2026-10-14T00:00:00-03:00"),
    ("start_date", "2026-10-12T12:00:00"),
    ("start_date", "2026-10-12"),
    ("start_date", "invalid"),
    ("start_date", 123),
])
def test_conflicting_or_invalid_partial_boundary_is_not_aliased(boundary, value):
    dated = offer(12, offer_id=f"OFFER-{ITEM}-1")
    partial = {"id": ITEM, "offer_id": dated["offer_id"], boundary: value}
    for sequence in permutations([dated, partial]):
        result = merge_sequence(sequence)
        assert len(result) == 2
        assert sum(lightning_window_valid(raw) for raw in result) == 1


def test_equivalent_partial_timezone_joins_the_confirmed_window():
    dated = offer(12, offer_id=f"OFFER-{ITEM}-1")
    partial = {"id": ITEM, "ref_id": dated["offer_id"], "startDate": "2026-10-12T15:00:00Z"}
    for sequence in permutations([dated, partial]):
        result = merge_sequence(sequence)
        assert len(result) == 1
        assert lightning_offer_window(result[0]) == lightning_offer_window(dated)


def test_ambiguous_reference_keeps_observation_separate_regardless_of_arrival_order():
    windows = [offer(day, offer_id=f"OFFER-{ITEM}-SAME") for day in (9, 10, 12)]
    undated = {"id": ITEM, "offer_id": windows[0]["offer_id"], "status": "candidate"}
    expected = {lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM) for raw in windows + [undated]}
    for sequence in permutations(windows + [undated]):
        result = merge_sequence(sequence)
        assert len(result) == 4
        assert {lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM) for raw in result} == expected
        assert sum(lightning_window_valid(raw) for raw in result) == 3
        assert all("_jk_lightning_observations" not in raw for raw in result)


def test_partial_reference_can_disambiguate_one_of_three_confirmed_windows():
    windows = [offer(day, offer_id=f"OFFER-{ITEM}-SAME") for day in (9, 10, 12)]
    partial = {"id": ITEM, "offer_id": windows[0]["offer_id"], "start_date": windows[1]["start_date"]}
    expected = {lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM) for raw in windows}
    for sequence in permutations(windows + [partial]):
        result = merge_sequence(sequence)
        assert len(result) == 3
        assert {lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM) for raw in result} == expected


def test_missing_reference_does_not_alias_an_ambiguous_window_after_earlier_merge():
    first = offer(12, offer_id=f"OFFER-{ITEM}-1")
    second = offer(12, offer_id=f"OFFER-{ITEM}-2")
    anonymous = offer(12)
    for sequence in permutations([first, second, anonymous]):
        result = merge_sequence(sequence)
        assert len(result) == 3
        assert len({lightning_offer_key(raw, campaign_id=CAMPAIGN, item_id=ITEM) for raw in result}) == 3


def test_same_reference_is_isolated_by_campaign_and_item_without_explicit_scope_override():
    first = dict(offer(12, offer_id="OFFER-SAME"), promotion_id=CAMPAIGN)
    for other in (dict(first, id="MLB7654321"), dict(first, promotion_id="LGH-OTHER")):
        other.pop("start_date")
        other.pop("finish_date")
        for sequence in permutations([first, other]):
            result = {}
            for raw in sequence:
                result = merge_lightning_raw(result, raw)
            assert len(lightning_offers(result)) == 2


def test_collection_combines_dated_campaign_and_undated_item_shape(monkeypatch):
    dated = offer(12, offer_id=f"OFFER-{ITEM}-1", status="pending")
    undated = dict(dated)
    undated.pop("start_date")
    undated.pop("finish_date")
    for entries in ([dated, undated], [undated, dated]):
        (items, raw_by_item, _cfg), _calls = collect(monkeypatch, entries, status="pending")
        assert len(items) == 1
        assert len(items[0]["offers"]) == 1
        assert lightning_offer_window(lightning_offers(raw_by_item[ITEM])[0]) == lightning_offer_window(dated)



def test_complementary_fields_do_not_restore_provenance_rejected_by_callback():
    dated = dict(offer(12, offer_id=f"OFFER-{ITEM}-1"), _jk_contexto_promocao_confirmado=True)
    undated = {"id": ITEM, "offer_id": dated["offer_id"], "price": 81.0}
    def rejected_context(previous, fresh):
        return {name: value for name, value in previous.items() if not name.startswith("_jk_")}
    for sequence in permutations([dated, undated]):
        result = merge_sequence(sequence, callback=rejected_context)
        assert len(result) == 1
        assert "_jk_contexto_promocao_confirmado" not in result[0]
        assert lightning_window_valid(result[0])


def test_complementary_partial_dates_without_a_confirmed_window_stay_incomplete():
    start = {"id": ITEM, "offer_id": f"OFFER-{ITEM}-1", "start_date": "2026-10-12T12:00:00"}
    finish = {"id": ITEM, "offer_id": start["offer_id"], "finish_date": "2026-10-12T23:59:59"}
    for sequence in permutations([start, finish]):
        result = merge_sequence(sequence)
        assert len(result) == 2
        assert not any(lightning_window_valid(raw) for raw in result)
