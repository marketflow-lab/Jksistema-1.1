from __future__ import annotations

import json
from typing import Any

import pytest

from backend.services import ia_tools_marketplaces as ml_tools


class FakeResponse:
    def __init__(self, status_code: int, payload: Any, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.headers = dict(headers or {})

    def json(self):
        return self._payload


def test_complete_query_deadline_caps_each_call_and_fails_closed(monkeypatch):
    monkeypatch.setattr(ml_tools.time, "monotonic", lambda: 100.0)

    assert ml_tools._ia_ml_remaining_timeout(105.0, 20) == 5
    with pytest.raises(ml_tools.requests.exceptions.Timeout):
        ml_tools._ia_ml_remaining_timeout(100.0, 20)


def _configure_store(monkeypatch, stores=None):
    stores = stores or ["JK Pecas"]
    monkeypatch.setattr(ml_tools, "_ia_lojas_ml_conectadas", lambda _client_id: list(stores), raising=False)
    monkeypatch.setattr(
        ml_tools,
        "_obter_cfg_ml",
        lambda _client_id, _store: {"access_token": "secret", "user_id": "12345"},
        raising=False,
    )


def _contains_forbidden_pii(value: Any) -> bool:
    forbidden = {"buyer", "seller", "shipping", "phone", "email", "address", "receiver_address", "document"}
    if isinstance(value, dict):
        return any(
            (str(key).lower() in forbidden and str(key).lower() not in {"buyer_name", "buyer_city"})
            or _contains_forbidden_pii(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_pii(item) for item in value)
    return False


def _order(order_id: int, *, quantity=1, unit_price=10, status="paid", refund=None):
    payment = {"status": "approved", "transaction_amount": quantity * unit_price}
    if refund is not None:
        payment["transaction_amount_refunded"] = refund
    return {
        "id": order_id,
        "status": status,
        "date_created": "2026-07-10T10:00:00.000-03:00",
        "currency_id": "BRL",
        "total_amount": quantity * unit_price,
        "paid_amount": quantity * unit_price,
        "buyer": {"id": "PII", "email": "buyer@example.com", "phone": "3700000000"},
        "shipping": {"receiver_address": {"street_name": "segredo"}},
        "payments": [payment],
        "order_items": [
            {
                "quantity": quantity,
                "unit_price": unit_price,
                "item": {"id": "MLB100", "title": "Produto A", "seller_sku": "SKU-1"},
            }
        ],
    }


def test_orders_uses_get_paginates_aggregates_and_removes_pii(monkeypatch):
    _configure_store(monkeypatch)
    calls = []
    first_page = [_order(index) for index in range(1, 51)]
    second_page = [_order(51, quantity=2, status="partially_refunded", refund=5)]

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {})})
        offset = int((kwargs.get("params") or {}).get("offset") or 0)
        rows = first_page if offset == 0 else second_page
        return FakeResponse(200, {"paging": {"total": 51, "offset": offset}, "results": rows}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)

    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "vendas de julho",
        "jk pecas",
        "2026-07-01",
        "2026-07-12",
        None,
        None,
        None,
        0,
        51,
    )
    result = response["result"]

    assert response["function"] == "get_mercado_livre_orders"
    assert len(calls) == 2
    assert all(call["method"] == "GET" for call in calls)
    assert all(call["url"].endswith("/orders/search") for call in calls)
    assert "order.status" not in calls[0]["params"]
    assert calls[0]["params"]["order.date_created.from"].endswith("-03:00")
    assert result["read_only"] is True
    assert result["paging"]["returned"] == 51
    assert result["paging"]["pages_fetched"] == 2
    assert result["totals"]["orders"] == 51
    assert result["totals"]["gross_amount"] == 520.0
    assert result["totals"]["refund_amount"] == 5.0
    assert result["totals"]["net_amount"] == 515.0
    assert result["by_sku"][0]["sku"] == "SKU-1"
    assert result["by_sku"][0]["quantity"] == 52.0
    assert not _contains_forbidden_pii(result["orders"])


def test_period_report_fetches_more_than_two_mercado_livre_pages(monkeypatch):
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        calls.append({"method": method, "url": url, "params": params})
        offset = int(params.get("offset") or 0)
        page_limit = int(params.get("limit") or 0)
        rows = [_order(index) for index in range(offset + 1, min(120, offset + page_limit) + 1)]
        return FakeResponse(200, {"paging": {"total": 120, "offset": offset}, "results": rows}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "relatorio de vendas do periodo",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        limite=120,
        modo_relatorio=True,
        max_paginas=20,
    )
    result = response["result"]

    assert [call["params"]["offset"] for call in calls] == [0, 50, 100]
    assert [call["params"]["limit"] for call in calls] == [50, 50, 20]
    assert all(call["method"] == "GET" for call in calls)
    assert result["paging"]["pages_fetched"] == 3
    assert result["paging"]["report_mode"] is True
    assert result["paging"]["returned"] == 120
    assert result["totals"]["orders"] == 120
    assert result["coverage_complete"] is True
    assert result["truncated"] is False


def test_period_report_marks_coverage_incomplete_when_page_budget_is_reached(monkeypatch):
    _configure_store(monkeypatch)
    offsets = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        offset = int(params.get("offset") or 0)
        offsets.append(offset)
        rows = [_order(index) for index in range(offset + 1, offset + 51)]
        return FakeResponse(200, {"paging": {"total": 2_000, "offset": offset}, "results": rows}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    result = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "relatorio de vendas do periodo",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        limite=1_000,
        modo_relatorio=True,
        max_paginas=3,
    )["result"]

    assert offsets == [0, 50, 100]
    assert result["paging"]["returned"] == 150
    assert result["paging"]["next_offset"] == 150
    assert result["coverage_complete"] is False
    assert result["truncated"] is True
    assert any("3 pagina(s)" in warning and "150 pedido(s)" in warning for warning in result["warnings"])


def test_orders_classifies_401_after_oauth_refresh(monkeypatch):
    _configure_store(monkeypatch)
    monkeypatch.setattr(
        ml_tools,
        "_ml_api_request",
        lambda _client_id, _store, cfg, method, url, **kwargs: (FakeResponse(401, {"message": "unauthorized"}), cfg),
        raising=False,
    )

    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas", "JK Pecas", "2026-07-01", "2026-07-12"
    )

    assert response["result"]["error"] == "reconnect_required"
    assert response["result"]["reconnect_required"] is True
    assert response["result"]["orders"] == []


def test_order_details_exposes_only_buyer_name_and_city(monkeypatch):
    _configure_store(monkeypatch)
    calls = []
    raw_order = _order(9001)
    raw_order["buyer"] = {
        "first_name": "Maria",
        "last_name": "Silva",
        "nickname": "MARIA-S",
        "email": "segredo@example.com",
        "phone": {"number": "3700000000"},
        "billing_info": {"doc_number": "00000000000"},
    }
    raw_order["shipping"] = {"id": 778899}

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append({"method": method, "url": url})
        if "/shipments/" in url:
            return FakeResponse(200, {
                "receiver_address": {
                    "city": {"name": "Belo Horizonte"},
                    "street_name": "Rua Secreta",
                    "street_number": "123",
                    "zip_code": "00000-000",
                    "phone": "3700000000",
                }
            }), cfg
        return FakeResponse(200, {"paging": {"total": 1}, "results": [raw_order]}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "detalhes da ultima venda e do comprador",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        limite=1,
    )
    result = response["result"]
    order = result["orders"][0]

    assert order["buyer_name"] == "Maria Silva"
    assert order["buyer_city"] == "Belo Horizonte"
    assert result["buyer_summary"]["sensitive_fields_omitted"] is True
    assert result["buyer_summary"]["cities_returned"] == 1
    assert all(call["method"] == "GET" for call in calls)
    assert any(call["url"].endswith("/shipments/778899") for call in calls)
    serialized = repr(result)
    for secret in ("segredo@example.com", "Rua Secreta", "00000-000", "3700000000", "778899"):
        assert secret not in serialized
    assert not _contains_forbidden_pii(result["orders"])


def test_order_buyer_name_falls_back_to_public_nickname(monkeypatch):
    _configure_store(monkeypatch)
    raw_order = _order(9002)
    raw_order["buyer"] = {"id": 1, "nickname": "CLIENTE-ML", "email": "oculto@example.com"}
    raw_order["shipping"] = {"receiver_address": {"city": {"name": "Divinopolis"}, "street_name": "Oculta"}}
    monkeypatch.setattr(
        ml_tools,
        "_ml_api_request",
        lambda _client_id, _store, cfg, method, url, **kwargs: (
            FakeResponse(200, {"paging": {"total": 1}, "results": [raw_order]}), cfg
        ),
        raising=False,
    )

    result = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "qual a cidade e comprador da ultima venda", "JK Pecas", "2026-07-01", "2026-07-12", limite=1
    )["result"]

    assert result["orders"][0]["buyer_name"] == "CLIENTE-ML"
    assert result["orders"][0]["buyer_city"] == "Divinopolis"
    assert "oculto@example.com" not in repr(result)


def test_orders_accepts_206_and_marks_partial_response(monkeypatch):
    _configure_store(monkeypatch)
    monkeypatch.setattr(
        ml_tools,
        "_ml_api_request",
        lambda _client_id, _store, cfg, method, url, **kwargs: (
            FakeResponse(
                206,
                {"paging": {"total": 1}, "results": [_order(1)]},
                {"X-Content-Missing": "buyer,shipping"},
            ),
            cfg,
        ),
        raising=False,
    )

    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas", "JK Pecas", "2026-07-01", "2026-07-12"
    )
    result = response["result"]

    assert result["found"] is True
    assert result["partial_response"] is True
    assert result["truncated"] is True
    assert any("HTTP 206" in warning and "buyer" in warning for warning in result["warnings"])


def test_orders_429_is_not_retried(monkeypatch):
    _configure_store(monkeypatch)
    call_count = 0

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return FakeResponse(429, {"message": "too many requests"}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas", "JK Pecas", "2026-07-01", "2026-07-12"
    )

    assert call_count == 1
    assert response["result"]["error"] == "rate_limited"


def test_orders_5xx_is_retried_only_once(monkeypatch):
    _configure_store(monkeypatch)
    statuses = [503, 200]

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        status_code = statuses.pop(0)
        payload = {"message": "unavailable"} if status_code == 503 else {"paging": {"total": 0}, "results": []}
        return FakeResponse(status_code, payload), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas", "JK Pecas", "2026-07-01", "2026-07-12"
    )

    assert statuses == []
    assert "error" not in response["result"]


def test_orders_timeout_is_retried_only_once(monkeypatch):
    _configure_store(monkeypatch)
    call_count = 0

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ml_tools.requests.exceptions.Timeout("timeout")
        return FakeResponse(200, {"paging": {"total": 0}, "results": []}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas", "JK Pecas", "2026-07-01", "2026-07-12"
    )

    assert call_count == 2
    assert "error" not in response["result"]


def test_orders_filters_sku_locally_without_unsupported_q(monkeypatch):
    _configure_store(monkeypatch)
    params_seen = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params_seen.append(dict(kwargs.get("params") or {}))
        rows = [_order(1), _order(2)]
        rows[1]["order_items"][0]["item"]["seller_sku"] = "OUTRO-SKU"
        return FakeResponse(200, {"paging": {"total": 2}, "results": rows}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "vendas do produto",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        None,
        "SKU-1",
    )

    assert "q" not in params_seen[0]
    assert [order["order_id"] for order in response["result"]["orders"]] == ["1"]


def test_orders_scans_up_to_100_before_cutting_sku_matches(monkeypatch):
    _configure_store(monkeypatch)
    params_seen = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        params_seen.append(params)
        offset = int(params.get("offset") or 0)
        if offset == 0:
            rows = [_order(index) for index in range(1, 51)]
            for row in rows:
                row["order_items"][0]["item"]["seller_sku"] = "OUTRO-SKU"
        else:
            rows = [_order(51)]
        return FakeResponse(200, {"paging": {"total": 51}, "results": rows}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "vendas do SKU-1",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        None,
        "SKU-1",
        None,
        0,
        10,
    )

    assert len(params_seen) == 2
    assert all("q" not in params for params in params_seen)
    assert [order["order_id"] for order in response["result"]["orders"]] == ["51"]
    assert response["result"]["paging"]["scanned"] == 51


def test_orders_supports_exact_order_id_with_direct_get(monkeypatch):
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {})})
        if url.endswith("/orders/987654321"):
            return FakeResponse(200, _order(987654321)), cfg
        if "/claims/search" in url:
            return FakeResponse(200, {"data": [], "paging": {"total": 0}}), cfg
        if "/messages/packs/" in url:
            return FakeResponse(200, {"messages": [], "paging": {"total": 0}}), cfg
        return FakeResponse(404, {}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "pedido 987654321",
        "JK Pecas",
        "2026-07-01",
        "2026-07-12",
        id_pedido="987654321",
    )

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/orders/987654321")
    assert calls[0]["params"] == {}
    assert all(call["method"] == "GET" for call in calls)
    assert response["arguments"]["order_id"] == "987654321"
    assert response["result"]["orders"][0]["order_id"] == "987654321"


def test_exact_pack_ignores_period_and_loads_full_claim_return_and_all_messages(monkeypatch):
    _configure_store(monkeypatch)
    calls = []
    pack_id = "2000013990113115"
    order_id = "2000017389080442"
    raw_order = _order(int(order_id), quantity=2, unit_price=49.9)
    raw_order.update({
        "pack_id": int(pack_id),
        "date_closed": "2026-07-13T01:13:00.000-03:00",
        "buyer": {
            "first_name": "Cliente",
            "last_name": "Teste",
            "nickname": "COMPRADOR_TESTE",
            "email": "nao-exibir@example.com",
            "phone": "5511999999999",
        },
        "shipping": {"id": 777001, "receiver_address": {"street_name": "Segredo"}},
    })

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        calls.append({"method": method, "url": url, "params": params})
        if url.endswith(f"/orders/{pack_id}"):
            return FakeResponse(404, {}), cfg
        if url.endswith(f"/packs/{pack_id}"):
            return FakeResponse(200, {"id": int(pack_id), "orders": [{"id": int(order_id)}]}), cfg
        if url.endswith(f"/orders/{order_id}"):
            return FakeResponse(200, raw_order), cfg
        if url.endswith("/shipments/777001"):
            return FakeResponse(200, {
                "id": 777001,
                "status": "delivered",
                "substatus": "delivered",
                "logistic_type": "fulfillment",
                "date_delivered": "2026-07-14T15:30:00.000-03:00",
                "receiver_address": {"street_name": "Nunca retornar"},
            }), cfg
        if url.endswith("/post-purchase/v1/claims/search"):
            return FakeResponse(200, {
                "data": [{
                    "id": 88001,
                    "type": "return",
                    "stage": "claim",
                    "status": "closed",
                    "reason_id": "PDD9549",
                    "last_updated": "2026-07-15T10:00:00Z",
                    "players": [{"user_id": 12345, "type": "seller", "role": "respondent"}],
                }],
                "paging": {"total": 1},
            }), cfg
        if url.endswith("/post-purchase/v1/claims/88001/detail"):
            return FakeResponse(200, {"title": "Produto com defeito", "description": "Comprador relatou falha."}), cfg
        if url.endswith("/post-purchase/v1/claims/88001/messages"):
            return FakeResponse(200, [
                {"sender_role": "complainant", "message_date": "2026-07-15T09:00:00Z", "message": "O produto apresentou falha."},
                {"sender_role": "respondent", "message_date": "2026-07-15T09:30:00Z", "message": "Vamos auxiliar com a devolução."},
            ]), cfg
        if url.endswith("/post-purchase/v2/claims/88001/returns"):
            return FakeResponse(200, {
                "id": 99001,
                "status": "closed",
                "status_money": "refunded",
                "shipments": [{"id": 99002, "status": "delivered"}],
            }), cfg
        if "/messages/packs/" in url:
            offset = int(params.get("offset") or 0)
            page = [
                {
                    "id": f"msg-{index}",
                    "message_date": f"2026-07-13T{index % 24:02d}:00:00Z",
                    "from": {"user_id": 12345 if index % 2 == 0 else 54321},
                    "text": f"Mensagem integral {index}",
                    "message_attachments": [{"filename": f"arquivo-{index}.pdf"}] if index == 1 else [],
                }
                for index in range(offset, min(offset + 50, 51))
            ]
            return FakeResponse(200, {"messages": page, "paging": {"total": 51}}), cfg
        raise AssertionError(f"endpoint inesperado: {url}")

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        f"verifique a venda {pack_id}",
        "JK Pecas",
        "2020-01-01",
        "2020-01-02",
        id_pedido=pack_id,
    )
    result = response["result"]
    order = result["orders"][0]

    assert result["exact_lookup"] is True
    assert result["requested_id"] == pack_id
    assert result["identifier_type"] == "pack"
    assert result["resolved_order_ids"] == [order_id]
    assert result["matched_stores"] == ["JK Pecas"]
    assert order["fulfillment"]["is_full"] is True
    assert order["shipment"]["delivery_state"] == "delivered"
    assert order["return_status"]["has_return"] is True
    assert order["claims"][0]["claim_id"] == "88001"
    assert order["conversations"]["post_sale"]["loaded_messages"] == 51
    assert order["conversations"]["post_sale"]["messages"][0]["label"] in {"Loja", "Comprador"}
    assert all(call["method"] == "GET" for call in calls)
    message_calls = [call for call in calls if "/messages/packs/" in call["url"]]
    assert [call["params"]["offset"] for call in message_calls] == [0, 50]
    assert all(call["params"]["mark_as_read"] == "false" for call in message_calls)
    serialized = json.dumps(result, ensure_ascii=False)
    assert "nao-exibir@example.com" not in serialized
    assert "5511999999999" not in serialized
    assert "Nunca retornar" not in serialized
    assert "/orders/search" not in " ".join(call["url"] for call in calls)


def test_exact_order_searches_other_permitted_stores_after_selected_store_misses(monkeypatch):
    _configure_store(monkeypatch, ["JK Pecas", "Deckas"])
    calls = []
    order_id = "2000017389080442"

    def fake_api(_client_id, store, cfg, method, url, **kwargs):
        calls.append((store, method, url))
        if store == "Deckas" and url.endswith(f"/orders/{order_id}"):
            return FakeResponse(200, _order(int(order_id))), cfg
        if "/claims/search" in url:
            return FakeResponse(200, {"data": [], "paging": {"total": 0}}), cfg
        if "/messages/packs/" in url:
            return FakeResponse(200, {"messages": [], "paging": {"total": 0}}), cfg
        return FakeResponse(404, {}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    result = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        f"venda {order_id}",
        "JK Pecas",
        id_pedido=order_id,
    )["result"]

    assert result["found"] is True
    assert result["identifier_type"] == "order"
    assert result["matched_stores"] == ["Deckas"]
    assert result["resolved_order_ids"] == [order_id]
    assert [(store, url.rsplit("/", 1)[-1]) for store, _method, url in calls[:3]] == [
        ("JK Pecas", order_id),
        ("JK Pecas", order_id),
        ("Deckas", order_id),
    ]


def test_exact_pack_resolves_visible_orders_across_permitted_accounts(monkeypatch):
    _configure_store(monkeypatch, ["JK Pecas", "Deckas"])
    pack_id = "2000013990113115"

    def fake_api(_client_id, store, cfg, method, url, **kwargs):
        if url.endswith(f"/orders/{pack_id}"):
            return FakeResponse(404, {}), cfg
        if store == "JK Pecas" and url.endswith(f"/packs/{pack_id}"):
            return FakeResponse(200, {"id": int(pack_id), "orders": [{"id": 101}, {"id": 202}]}), cfg
        if store == "JK Pecas" and url.endswith("/orders/101"):
            order = _order(101)
            order["pack_id"] = int(pack_id)
            return FakeResponse(200, order), cfg
        if store == "Deckas" and url.endswith("/orders/202"):
            order = _order(202)
            order["pack_id"] = int(pack_id)
            return FakeResponse(200, order), cfg
        if "/claims/search" in url:
            return FakeResponse(200, {"data": [], "paging": {"total": 0}}), cfg
        if "/messages/packs/" in url:
            return FakeResponse(200, {"messages": [], "paging": {"total": 0}}), cfg
        return FakeResponse(404, {}), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    result = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        f"venda {pack_id}",
        "JK Pecas",
        id_pedido=pack_id,
    )["result"]

    assert result["identifier_type"] == "pack"
    assert result["resolved_order_ids"] == ["101", "202"]
    assert result["matched_stores"] == ["JK Pecas", "Deckas"]
    assert [order["store"] for order in result["orders"]] == ["JK Pecas", "Deckas"]


def test_orders_older_than_twelve_months_uses_local_history_signal_without_api(monkeypatch):
    _configure_store(monkeypatch)
    called = False

    def should_not_call(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("ML API must not be called for historical-only coverage")

    monkeypatch.setattr(ml_tools, "_ml_api_request", should_not_call, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002", "vendas antigas", "JK Pecas", "2024-01-01", "2024-01-31"
    )

    assert called is False
    assert response["result"]["coverage"] == "historical_period_local_only"
    assert response["result"]["api_consulted"] is False
    assert response["result"]["warnings"]


def test_orders_rejects_non_exact_or_ambiguous_store_without_api_call(monkeypatch):
    _configure_store(monkeypatch, ["JK Pecas", "JK Pecas Centro"])
    called = False

    def should_not_call(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("API must not be called")

    monkeypatch.setattr(ml_tools, "_ml_api_request", should_not_call, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_orders("000002", "vendas", "JK")

    assert response["result"]["error"] == "ambiguous_store"
    assert response["result"]["available_stores"] == ["JK Pecas", "JK Pecas Centro"]
    assert called is False


def test_latest_return_uses_claims_api_and_enriches_order_without_pii(monkeypatch):
    _configure_store(monkeypatch)
    calls = []
    raw_order = _order(987654321, quantity=2, unit_price=25, status="partially_refunded", refund=25)
    raw_order["buyer"] = {"email": "segredo@example.com", "phone": "3700000000"}

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {})})
        if url.endswith("/post-purchase/v1/claims/search"):
            return FakeResponse(200, {
                "paging": {"total": 1, "offset": 0, "limit": 1},
                "data": [{
                    "id": 555001,
                    "resource_id": 987654321,
                    "resource": "order",
                    "type": "return",
                    "stage": "none",
                    "status": "closed",
                    "reason_id": "PDD123",
                    "quantity_type": "partial",
                    "claimed_quantity": 1,
                    "date_created": "2026-07-11T10:00:00.000-03:00",
                    "last_updated": "2026-07-12T12:00:00.000-03:00",
                    "players": [{"user_id": "PII"}],
                    "resolution": {"reason": "payment_refunded", "date_created": "2026-07-12T12:00:00.000-03:00"},
                }],
            }), cfg
        if url.endswith("/orders/987654321"):
            return FakeResponse(200, raw_order), cfg
        if url.endswith("/post-purchase/v2/claims/555001/returns"):
            return FakeResponse(200, {
                "id": 7001,
                "status": "closed",
                "status_money": "refunded",
                "last_updated": "2026-07-12T12:00:00.000-03:00",
                "shipments": [{
                    "shipment_id": 8001,
                    "status": "delivered",
                    "last_updated": "2026-07-12T11:00:00.000-03:00",
                    "destination": {"shipping_address": {"street_name": "Rua secreta"}},
                }],
            }), cfg
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_returns(
        "000002",
        "qual foi a ultima devolucao",
        "JK Pecas",
        limite=1,
    )
    result = response["result"]

    assert response["function"] == "get_mercado_livre_returns"
    assert [call["method"] for call in calls] == ["GET", "GET", "GET"]
    search = calls[0]
    assert search["params"]["player_user_id"] == "12345"
    assert search["params"]["player_role"] == "respondent"
    assert search["params"]["resource"] == "order"
    assert "type" not in search["params"]
    assert search["params"]["sort"] == "last_updated:desc"
    assert search["params"]["range"].startswith("last_updated:after:")
    assert result["found"] is True
    assert result["latest_first"] is True
    assert result["devolucoes"][0]["claim_id"] == "555001"
    assert result["devolucoes"][0]["order_id"] == "987654321"
    assert result["devolucoes"][0]["order"]["items"][0]["sku"] == "SKU-1"
    assert result["devolucoes"][0]["return_detail"]["status_money"] == "refunded"
    serialized = repr(result)
    for secret in ("segredo@example.com", "3700000000", "Rua secreta", "PII"):
        assert secret not in serialized


def test_latest_return_detects_return_related_to_mediation_claim(monkeypatch):
    _configure_store(monkeypatch)
    calls = []
    claim = {
        "id": 555002,
        "resource_id": 987654322,
        "resource": "order",
        "type": "mediations",
        "stage": "dispute",
        "status": "opened",
        "reason_id": "PDD9949",
        "date_created": "2026-07-13T08:00:00.000-04:00",
        "last_updated": "2026-07-13T09:00:00.000-04:00",
    }

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": dict(kwargs.get("params") or {})})
        if url.endswith("/post-purchase/v1/claims/search"):
            return FakeResponse(200, {"paging": {"total": 1}, "data": [claim]}), cfg
        if url.endswith("/post-purchase/v1/claims/555002"):
            return FakeResponse(200, {**claim, "related_entities": ["return"]}), cfg
        if url.endswith("/post-purchase/v2/claims/555002/returns"):
            return FakeResponse(200, {
                "id": 7002,
                "status": "label_generated",
                "last_updated": "2026-07-13T13:00:01.000+00:00",
            }), cfg
        if url.endswith("/orders/987654322"):
            return FakeResponse(200, _order(987654322)), cfg
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    response = ml_tools._ia_tool_get_mercado_livre_returns(
        "000002", "qual foi a ultima devolucao", "JK Pecas", limite=1
    )
    result = response["result"]

    assert [call["method"] for call in calls] == ["GET", "GET", "GET", "GET"]
    assert calls[1]["url"].endswith("/post-purchase/v1/claims/555002")
    assert calls[2]["url"].endswith("/post-purchase/v2/claims/555002/returns")
    assert result["devolucoes"][0]["type"] == "mediations"
    assert result["devolucoes"][0]["return_detail"]["return_id"] == "7002"
    assert result["devolucoes"][0]["order"]["order_id"] == "987654322"
    assert result["paging"]["claims_scanned"] == 1
    assert {source["resource"] for source in result["sources"]} == {
        "post-purchase/v1/claims/search",
        "post-purchase/v1/claims/{claim_id}",
        "post-purchase/v2/claims/{claim_id}/returns",
        "orders/{order_id}",
    }


def test_listing_defaults_active_paginates_five_pages_and_caps_at_100(monkeypatch):
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        calls.append({"method": method, "url": url, "params": params})
        if url.endswith("/items/search"):
            offset = int(params["offset"])
            ids = [f"MLB{offset + index + 1}" for index in range(20)]
            return FakeResponse(200, {"paging": {"total": 150, "offset": offset}, "results": ids}), cfg
        ids = str(params.get("ids") or "").split(",")
        payload = [
            {
                "code": 200,
                "body": {
                    "id": item_id,
                    "title": f"Produto {item_id}",
                    "status": "active",
                    "seller_sku": f"SKU-{item_id[3:]}",
                    "sold_quantity": 7,
                },
            }
            for item_id in ids
            if item_id
        ]
        return FakeResponse(200, payload), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    monkeypatch.setattr(ml_tools, "_ia_tool_resolver_sku", lambda *_args, **_kwargs: "", raising=False)
    monkeypatch.setattr(ml_tools, "_ia_extrair_referencia_produto_mensagem", lambda *_args: {}, raising=False)

    response = ml_tools._ia_tool_get_mercado_livre_listing(
        "000002", "liste os anuncios", "JK Pecas", limite=500
    )
    result = response["result"]
    search_calls = [call for call in calls if call["url"].endswith("/items/search")]

    assert len(search_calls) == 5
    assert all(call["method"] == "GET" for call in calls)
    assert all(call["params"]["status"] == "active" for call in search_calls)
    assert [call["params"]["offset"] for call in search_calls] == [0, 20, 40, 60, 80]
    assert result["read_only"] is True
    assert len(result["matches"]) == 100
    assert result["paging"]["limit"] == 100
    assert result["paging"]["next_offset"] == 100
    assert result["truncated"] is True
    assert result["matches"][0]["sold_quantity_scope"] == "acumulado_do_anuncio"


def test_listing_accepts_206_and_marks_partial(monkeypatch):
    _configure_store(monkeypatch)

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        if url.endswith("/items/search"):
            return FakeResponse(
                206,
                {"paging": {"total": 1}, "results": ["MLB1"]},
                {"X-Content-Missing": "filters"},
            ), cfg
        return FakeResponse(200, [{"body": {"id": "MLB1", "title": "Produto", "status": "active"}}]), cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    monkeypatch.setattr(ml_tools, "_ia_tool_resolver_sku", lambda *_args, **_kwargs: "", raising=False)
    monkeypatch.setattr(ml_tools, "_ia_extrair_referencia_produto_mensagem", lambda *_args: {}, raising=False)

    response = ml_tools._ia_tool_get_mercado_livre_listing("000002", "liste anuncios", "JK Pecas")

    assert response["result"]["partial_response"] is True
    assert response["result"]["truncated"] is True
    assert any("HTTP 206" in warning for warning in response["result"]["warnings"])


def test_listing_explicit_items_limits_descriptions_to_ten(monkeypatch):
    _configure_store(monkeypatch)
    description_calls = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        assert method == "GET"
        ids = str((kwargs.get("params") or {}).get("ids") or "").split(",")
        return FakeResponse(
            200,
            [{"body": {"id": item_id, "title": item_id, "status": "active", "seller_sku": item_id}} for item_id in ids],
        ), cfg

    def fake_description(_client_id, _store, cfg, item_id, **_kwargs):
        description_calls.append(item_id)
        return f"Descricao {item_id}", cfg

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    monkeypatch.setattr(ml_tools, "_ia_ml_obter_descricao_item", fake_description)
    ids = ",".join(f"MLB{index}" for index in range(1, 13))

    response = ml_tools._ia_tool_get_mercado_livre_listing(
        "000002",
        "detalhes dos anuncios",
        "JK Pecas",
        limite=12,
        item_id=ids,
        incluir_detalhes=True,
    )

    assert len(response["result"]["matches"]) == 12
    assert len(description_calls) == 10
    assert response["result"]["matches"][9]["description"]
    assert response["result"]["matches"][10]["description"] == ""
    assert any("10 anuncios" in warning for warning in response["result"]["warnings"])


def test_listing_does_not_treat_date_as_sku(monkeypatch):
    _configure_store(monkeypatch)
    search_params = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        params = dict(kwargs.get("params") or {})
        if url.endswith("/items/search"):
            search_params.append(params)
            return FakeResponse(200, {"paging": {"total": 0}, "results": []}), cfg
        raise AssertionError("No item detail request expected")

    monkeypatch.setattr(ml_tools, "_ml_api_request", fake_api, raising=False)
    monkeypatch.setattr(ml_tools, "_ia_tool_resolver_sku", lambda *_args, **_kwargs: "01/07/2026", raising=False)
    monkeypatch.setattr(ml_tools, "_ia_extrair_referencia_produto_mensagem", lambda *_args: {}, raising=False)

    response = ml_tools._ia_tool_get_mercado_livre_listing(
        "000002", "liste anuncios de 01/07/2026 a 12/07/2026", "JK Pecas"
    )

    assert response["arguments"]["sku"] == ""
    assert "seller_sku" not in search_params[0]
