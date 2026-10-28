"""Regressoes da coleta paginada usada para renovar campanhas, sem rede."""

import copy
from urllib.parse import parse_qs, urlparse

import backend_api  # noqa: F401 - configura as dependencias das fachadas
import pytest
import requests

from backend.services import mercadolivre_legacy_promocoes as promotions


CAMPAIGN = "C-MLB998001"
TENANT, STORE, SELLER = "tenant-paginacao", "loja-paginacao", "123"


class Response:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.payload = copy.deepcopy(payload)

    def json(self):
        return copy.deepcopy(self.payload)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Consulta externa nao simulada")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    cache = {}
    monkeypatch.setattr(promotions, "_ml_cache_get", cache.get)
    monkeypatch.setattr(promotions, "_ml_cache_set", cache.__setitem__)
    return cache


def entry(number, status="started"):
    item_id = f"MLB{998000000 + number}"
    raw = {"id": item_id, "status": status, "price": 80, "original_price": 100}
    if status == "pending":
        raw["offer_id"] = f"CANDIDATE-{item_id}-TEST"
    return raw


def collect(**kwargs):
    return promotions._ml_listar_itens_promocao_com_raw(
        TENANT, STORE, {"user_id": SELLER}, CAMPAIGN,
        promotion_type="SELLER_CAMPAIGN", buscar_detalhes=False,
        status_item_preferencial="", **kwargs,
    )


def test_active_scan_keeps_filter_and_excludes_paused_items_after_first_page(monkeypatch):
    calls = []
    expected = [entry(i)["id"] for i in range(102)]
    paused = "MLB999999999"

    def request(client, store, cfg, method, url, *, params, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "GET")
        assert url.endswith(f"/users/{SELLER}/items/search")
        calls.append(dict(params))
        if "scroll_id" not in params:
            return Response({"results": expected[:100], "scroll_id": "next/one"}), cfg
        if params["scroll_id"] == "next/one":
            rows = expected[99:] if params.get("status") == "active" else [paused]
            return Response({"results": rows, "scroll_id": "next/two"}), cfg
        return Response({"results": []}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    actual, _cfg = promotions._ml_listar_ids_anuncios_ativos(
        TENANT, STORE, {}, SELLER,
    )
    assert actual == expected
    assert paused not in actual
    assert len(calls) == 3
    assert all(call["status"] == "active" for call in calls)


def test_active_scan_does_not_reuse_cache_from_unfiltered_scroll(monkeypatch, offline):
    old_key = f"promo:ml_ids_ativos:{TENANT}:{STORE}:{SELLER}:5000"
    offline[old_key] = ["MLB999999999"]
    expected = entry(1)["id"]
    calls = []

    def request(_client, _store, cfg, method, _url, **kwargs):
        assert method == "GET"
        calls.append(kwargs["params"])
        return Response({"results": [expected]}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    first, _cfg = promotions._ml_listar_ids_anuncios_ativos(TENANT, STORE, {}, SELLER)
    second, _cfg = promotions._ml_listar_ids_anuncios_ativos(TENANT, STORE, {}, SELLER)
    assert first == second == [expected]
    assert len(calls) == 1
    assert offline[old_key] == ["MLB999999999"]


@pytest.mark.parametrize("cursor_location,cursor_key", [
    ("root", "search_after"), ("root", "searchAfter"),
    ("paging", "search_after"), ("paging", "searchAfter"),
])
@pytest.mark.parametrize("status", ["started", "pending"])
def test_campaign_cursor_collects_618_without_losing_campaign_or_status(
    monkeypatch, cursor_location, cursor_key, status,
):
    rows = [entry(i, status) for i in range(618)]
    calls = []

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "GET")
        assert urlparse(url).path == f"/seller-promotions/promotions/{CAMPAIGN}/items"
        params = parse_qs(urlparse(url).query)
        assert params["promotion_type"] == ["SELLER_CAMPAIGN"]
        assert params["status"] == [status]
        assert params["app_version"] == ["v2"]
        calls.append(params)
        token = params.get("search_after", [""])[0]
        index = int(token.split("/")[1].split("+")[0]) if token else 0
        if index:
            assert "offset" not in params
        payload = {"results": rows[index:index + 50], "paging": {"total": len(rows)}}
        if index + 50 < len(rows):
            target = payload if cursor_location == "root" else payload["paging"]
            target[cursor_key] = f"next/{index + 50}+token"
        return Response(payload), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, raw, _cfg = collect(status_promocao=status, max_items=5000)
    assert [item["id"] for item in items] == [row["id"] for row in rows]
    assert set(raw) == {row["id"] for row in rows}
    assert len(calls) == 13


def test_cursor_short_and_overlapping_pages_do_not_truncate_unique_items(monkeypatch):
    calls = []
    pages = [[entry(1)], [entry(1), entry(2)], [entry(3)]]

    def request(_client, _store, cfg, _method, url, **kwargs):
        params = parse_qs(urlparse(url).query)
        index = int(params.get("search_after", ["0"])[0])
        calls.append(params)
        payload = {"results": pages[index]}
        if index < 2:
            payload["paging"] = {"searchAfter": str(index + 1)}
        return Response(payload), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, raw, _cfg = collect(status_promocao="started", max_items=3)
    assert [item["id"] for item in items] == [entry(i)["id"] for i in (1, 2, 3)]
    assert len(raw) == 3
    assert len(calls) == 3


def test_repeated_cursor_stops_after_including_new_items(monkeypatch):
    calls = []

    def request(_client, _store, cfg, _method, url, **kwargs):
        calls.append(url)
        assert len(calls) <= 2
        return Response({"results": [entry(len(calls))],
                         "paging": {"searchAfter": "same-token"}}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, raw, _cfg = collect(status_promocao="started")
    assert [item["id"] for item in items] == [entry(i)["id"] for i in (1, 2)]
    assert len(calls) == len(raw) == 2


def test_campaign_without_cursor_still_uses_offset(monkeypatch):
    rows = [entry(i) for i in range(61)]
    offsets = []

    def request(_client, _store, cfg, _method, url, **kwargs):
        params = parse_qs(urlparse(url).query)
        assert "search_after" not in params
        offset = int(params["offset"][0])
        offsets.append(offset)
        return Response({"results": rows[offset:offset + 50], "paging": {"total": 61}}), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, _raw, _cfg = collect(status_promocao="started")
    assert len(items) == 61
    assert offsets == [0, 50]


def test_status_item_filter_is_preserved_in_cursor_requests(monkeypatch):
    calls = []

    def request(_client, _store, cfg, _method, url, **kwargs):
        params = parse_qs(urlparse(url).query)
        assert params["status_item"] == ["active"]
        calls.append(params)
        payload = {"results": [entry(len(calls))]}
        if len(calls) == 1:
            payload["paging"] = {"searchAfter": "cursor"}
        return Response(payload), cfg

    monkeypatch.setattr(promotions, "_ml_api_request_com_retry", request)
    items, _raw, _cfg = promotions._ml_listar_itens_promocao_com_raw(
        TENANT, STORE, {}, CAMPAIGN, promotion_type="SELLER_CAMPAIGN",
        buscar_detalhes=False, status_item_preferencial="active",
    )
    assert len(items) == len(calls) == 2
