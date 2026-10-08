"""Regressoes de identidade, janelas e paginacao LIGHTNING sem acesso externo."""

import copy
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


def test_external_internal_offer_list_cannot_inject_another_window(monkeypatch):
    contaminated = offer(9, **{LIGHTNING_OFFERS_KEY: [offer(12, promotion_id="LGH-OTHER")]})
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
