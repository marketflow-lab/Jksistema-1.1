from __future__ import annotations

import json
from typing import Any

from backend.services import ia_tools_marketplaces as ml_tools, whatsapp_report_visuals


class _Response:
    def __init__(self, status_code: int, payload: Any, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})
        self.text = ""

    def json(self):
        return self._payload


def _configure_store(monkeypatch):
    monkeypatch.setattr(ml_tools, "_ia_lojas_ml_conectadas", lambda _client_id: ["JK Pecas"], raising=False)
    monkeypatch.setattr(
        ml_tools,
        "_obter_cfg_ml",
        lambda _client_id, _store: {"access_token": "secret", "user_id": "12345"},
        raising=False,
    )


def _sanitized_order(order_id: str, created: str, *, quantity: float, gross: float, refund: float | None = 0) -> dict:
    return {
        "order_id": order_id,
        "date_created": created,
        "currency_id": "BRL",
        "gross_amount": gross,
        "paid_amount": gross,
        "refund_amount": refund,
        "net_amount": None if refund is None else gross - refund,
        "buyer_name": "Não deve entrar no gráfico",
        "buyer_city": "Cidade não deve entrar no gráfico",
        "items": [{
            "item_id": "MLB100",
            "sku": "SKU-1",
            "title": "Produto A",
            "quantity": quantity,
            "gross_amount": gross,
        }],
    }


def _raw_order(order_id: int, created: str, *, quantity: int = 1, unit_price: float = 10) -> dict:
    return {
        "id": order_id,
        "status": "paid",
        "date_created": created,
        "currency_id": "BRL",
        "total_amount": quantity * unit_price,
        "paid_amount": quantity * unit_price,
        "buyer": {"email": "segredo@example.com", "phone": "5511999999999"},
        "shipping": {"receiver_address": {"street_name": "Rua secreta"}},
        "payments": [{"status": "approved", "transaction_amount": quantity * unit_price}],
        "order_items": [{
            "quantity": quantity,
            "unit_price": unit_price,
            "item": {"id": "MLB100", "title": "Produto A", "seller_sku": "SKU-1"},
        }],
    }


def test_sales_by_day_uses_sao_paulo_closes_totals_and_contains_no_pii():
    orders = [
        _sanitized_order("1", "2026-07-13T01:30:00Z", quantity=2, gross=20),
        _sanitized_order("2", "2026-07-13T04:00:00Z", quantity=1, gross=15, refund=5),
    ]
    totals, _by_sku, _warnings = ml_tools._ia_ml_aggregate_orders(orders)
    by_day, daily_warnings = ml_tools._ia_ml_aggregate_orders_by_day(orders)
    chart = ml_tools._ia_ml_sales_chart_data(
        by_day,
        totals,
        coverage_complete=True,
        paging={"pages_fetched": 2, "scanned": 2, "total": 2, "has_more": False},
    )

    assert daily_warnings == []
    assert [point["date"] for point in by_day] == ["2026-07-12", "2026-07-13"]
    assert [point["orders"] for point in by_day] == [1, 1]
    assert sum(point["items_quantity"] for point in by_day) == totals["items_quantity"] == 3
    assert sum(point["gross_amount"] for point in by_day) == totals["gross_amount"] == 35
    assert sum(point["paid_amount"] for point in by_day) == totals["paid_amount"] == 35
    assert sum(point["refund_amount"] for point in by_day) == totals["refund_amount"] == 5
    assert sum(point["net_amount"] for point in by_day) == totals["net_amount"] == 30
    assert all(value is True for value in chart["reconciliation"].values())
    assert chart["schema"] == "jk.marketplace.sales_by_day.v1"
    assert chart["timezone"] == "America/Sao_Paulo"
    assert chart["coverage_complete"] is True
    assert chart["partial"] is False
    assert chart["pii_included"] is False
    serialized = json.dumps(chart, ensure_ascii=False)
    assert "Não deve entrar no gráfico" not in serialized
    assert "Cidade não deve entrar no gráfico" not in serialized


def test_unknown_refund_remains_null_in_day_and_reconciliation():
    orders = [_sanitized_order("1", "2026-07-13T10:00:00-03:00", quantity=1, gross=20, refund=None)]
    totals, _by_sku, _warnings = ml_tools._ia_ml_aggregate_orders(orders)
    by_day, _daily_warnings = ml_tools._ia_ml_aggregate_orders_by_day(orders)
    chart = ml_tools._ia_ml_sales_chart_data(by_day, totals, coverage_complete=True)

    assert totals["refund_amount"] is None
    assert by_day[0]["refund_amount"] is None
    assert by_day[0]["net_amount"] is None
    assert chart["reconciliation"]["refund_amount"] is None
    assert chart["reconciliation"]["net_amount"] is None


def test_by_sku_values_reconcile_order_totals_after_discount_and_cent_rounding():
    orders = [{
        "order_id": "1",
        "date_created": "2026-07-13T10:00:00-03:00",
        "currency_id": "BRL",
        "gross_amount": 9.99,
        "paid_amount": 9.99,
        "refund_amount": 0,
        "items": [
            {"item_id": "MLB1", "sku": "SKU-A", "title": "Produto A", "quantity": 1, "gross_amount": 3.33},
            {"item_id": "MLB2", "sku": "SKU-B", "title": "Produto B", "quantity": 1, "gross_amount": 3.33},
        ],
    }]

    totals, by_sku, _warnings = ml_tools._ia_ml_aggregate_orders(orders)

    assert sum(row["quantity"] for row in by_sku) == totals["items_quantity"] == 2
    assert sum(row["gross_amount"] for row in by_sku) == totals["gross_amount"] == 9.99
    assert sum(row["paid_amount"] for row in by_sku) == totals["paid_amount"] == 9.99
    assert sum(row["refund_amount"] for row in by_sku) == totals["refund_amount"] == 0
    assert sum(row["net_amount"] for row in by_sku) == totals["net_amount"] == 9.99


def test_missing_order_date_is_retained_but_marks_temporal_chart_partial():
    orders = [_sanitized_order("1", "", quantity=1, gross=20)]
    totals, _by_sku, _warnings = ml_tools._ia_ml_aggregate_orders(orders)
    by_day, daily_warnings = ml_tools._ia_ml_aggregate_orders_by_day(orders)
    chart = ml_tools._ia_ml_sales_chart_data(by_day, totals, coverage_complete=True)

    assert by_day[0]["date"] is None
    assert by_day[0]["label"] == "Data indisponível"
    assert by_day[0]["gross_amount"] == totals["gross_amount"]
    assert daily_warnings
    assert chart["coverage_complete"] is False
    assert chart["partial"] is True
    assert chart["coverage"]["dates_complete"] is False


def test_orders_result_exposes_partial_chart_contract_from_sanitized_rows(monkeypatch):
    _configure_store(monkeypatch)
    rows = [
        _raw_order(1, "2026-07-13T01:30:00Z"),
        _raw_order(2, "2026-07-13T04:00:00Z", quantity=2),
    ]

    monkeypatch.setattr(
        ml_tools,
        "_ml_api_request",
        lambda _client_id, _store, cfg, method, url, **kwargs: (
            _Response(206, {"paging": {"total": 100}, "results": rows}, {"X-Content-Missing": "buyer"}),
            cfg,
        ),
        raising=False,
    )

    result = ml_tools._ia_tool_get_mercado_livre_orders(
        "000002",
        "vendas do periodo",
        "JK Pecas",
        "2026-07-01",
        "2026-07-13",
        limite=2,
    )["result"]

    assert result["by_sku"][0]["quantity"] == 3
    assert result["by_day"] == result["chart_data"]["points"]
    assert result["chart_data"]["coverage_complete"] is False
    assert result["chart_data"]["partial"] is True
    assert result["chart_data"]["coverage"]["provider_total"] == 100
    assert result["chart_data"]["coverage"]["included_orders"] == 2
    assert all(value is True for value in result["chart_data"]["reconciliation"].values())
    serialized = json.dumps(result["chart_data"], ensure_ascii=False)
    for secret in ("segredo@example.com", "5511999999999", "Rua secreta"):
        assert secret not in serialized


def test_listing_result_exposes_read_only_snapshot_fields(monkeypatch):
    _configure_store(monkeypatch)
    monkeypatch.setattr(ml_tools, "_ia_tool_resolver_sku", lambda *_args, **_kwargs: "", raising=False)
    monkeypatch.setattr(ml_tools, "_ia_extrair_referencia_produto_mensagem", lambda *_args, **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        ml_tools,
        "_ia_ml_search_listing_ids",
        lambda *_args, **_kwargs: (
            ["MLB1", "MLB2"],
            {"access_token": "secret", "user_id": "12345"},
            {"offset": 0, "limit": 2, "returned": 2, "total": 3, "next_offset": 2, "has_more": True, "pages_fetched": 1},
            None,
        ),
    )
    monkeypatch.setattr(
        ml_tools,
        "_ia_ml_fetch_listing_items",
        lambda *_args, **_kwargs: ([
            {"id": "MLB1", "title": "Anúncio 1", "status": "active", "seller_sku": "SKU-1", "currency_id": "BRL", "price": 50, "available_quantity": 4, "sold_quantity": 10},
            {"id": "MLB2", "title": "Anúncio 2", "status": "active", "seller_sku": "SKU-2", "currency_id": "BRL", "price": 70, "available_quantity": 6, "sold_quantity": 20},
        ], {"access_token": "secret", "user_id": "12345"}, None),
    )

    result = ml_tools._ia_tool_get_mercado_livre_listing(
        "000002",
        "listar anuncios ativos",
        "JK Pecas",
        limite=2,
    )["result"]
    chart = result["chart_data"]

    assert chart["schema"] == "jk.marketplace.listing_snapshot.v1"
    assert chart["kind"] == "listing_snapshot"
    assert chart["read_only"] is True
    assert chart["currency_id"] == "BRL"
    assert chart["pii_included"] is False
    assert chart["partial"] is True
    assert chart["summary"]["returned"] == 2
    assert chart["summary"]["available_quantity"] == 10
    assert chart["summary"]["sold_quantity_accumulated"] == 30
    assert chart["points"][0]["sold_quantity_scope"] == "acumulado_do_anuncio"


def test_listing_visual_adapter_uses_accumulated_snapshot_without_name_error():
    result = {
        "success": True,
        "tool_id": "mercado_livre_listing",
        "loja": "JK Pecas",
        "chart_data": {
            "schema": "jk.marketplace.listing_snapshot.v1",
            "coverage_complete": True,
            "points": [{
                "item_id": "MLB1",
                "sku": "SKU-1",
                "title": "Anúncio 1",
                "status": "active",
                "sold_quantity": 12,
                "sold_quantity_scope": "acumulado_do_anuncio",
            }],
        },
        "summary": [{
            "tool_id": "mercado_livre_listing",
            "loja": "JK Pecas",
            "summary": {"coverage_complete": True},
        }],
    }

    chart = whatsapp_report_visuals.build_chart_data("análise dos anúncios", [result], {"store": "JK Pecas"})

    assert chart["analysis_type"] == "listings"
    assert chart["source"] == "Mercado Livre — sold_quantity acumulado do anúncio"
    assert chart["ranking"][0]["quantity"] == 12
    assert chart["ranking"][0]["value_label"] == "vendidos acumulados"
