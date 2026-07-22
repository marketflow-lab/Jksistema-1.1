from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import codex_marketplace_margin as margin


def _listing(item_id: str = "MLB123", sku: str = "SKU-1") -> dict:
    return {
        "id": item_id,
        "title": "Produto",
        "status": "active",
        "seller_custom_field": sku,
        "price": 100,
        "currency_id": "BRL",
        "category_id": "MLB1",
        "listing_type_id": "gold_special",
        "available_quantity": 1,
        "shipping": {"free_shipping": True, "mode": "me2"},
        "details": {
            "commercial": {
                "price": {
                    "amount": 90,
                    "regular_amount": 100,
                    "currency_id": "BRL",
                    "promotion_id": "PROMO-1",
                    "promotion_type": "DEAL",
                },
                "fees": {
                    "sale_fee_amount": 14.5,
                    "fixed_fee_amount": 6,
                    "percentage_fee": 15.5,
                },
                "shipping": {
                    "free_shipping": True,
                    "seller_cost": 12.75,
                    "seller_cost_available": True,
                },
                "coverage_complete": True,
                "warnings": [],
            }
        },
    }


def _order(order_id: str, item_ids: list[str], *, pack_id: str = "PACK-1", day: str = "2026-07-20") -> dict:
    payload = {
        "id": order_id,
        "date_created": f"{day}T12:00:00-03:00",
        "status": "paid",
        "pack_id": pack_id,
        "currency_id": "BRL",
        "order_items": [
            {
                "item": {"id": item_id, "title": f"Item {index}", "seller_sku": f"SKU-{index}"},
                "quantity": 1,
                "unit_price": 100 + index,
            }
            for index, item_id in enumerate(item_ids, start=1)
        ],
    }
    if pack_id:
        payload["shipping"] = {"id": f"SHIP-{pack_id}"}
    return payload


def _billing_payload(order_ids: list[str], orders_by_id: dict[str, dict]) -> dict:
    rows = []
    for order_id in order_ids:
        order = orders_by_id[order_id]
        items = []
        for raw in order["order_items"]:
            items.append({"item_id": raw["item"]["id"], "sale_fee_amount": 12})
        rows.append({"order_id": order_id, "items": items})
    return {"orders": rows}


def _empty_orders(**_: object) -> dict:
    return {"orders": [], "paging": {"total": 0}, "coverage": {"complete": True}}


def test_structured_api_error_never_closes_day_as_zero_orders(tmp_path: Path):
    snapshot = margin.collect_marketplace_commercial_snapshot(
        info_base=tmp_path,
        client_id="tenant-a",
        stores=["Loja A"],
        period_start="2026-07-20",
        period_end="2026-07-20",
        relevant_skus=[],
        listing_fetcher=lambda **_: {"listings": [], "coverage": {"complete": True}},
        orders_fetcher=lambda **_: {"error": "timeout", "orders": [], "coverage_complete": False},
        force_refresh=True,
    )

    day = snapshot["coverage"]["stores"]["Loja A"]["orders"]["days"][0]
    ledger = margin.MarketplaceMarginLedger(tmp_path, "tenant-a")
    assert snapshot["status"] == "partial"
    assert day["status"] == "partial"
    assert any("sera reconciliado novamente" in warning for warning in snapshot["warnings"])
    assert ledger.should_reconcile("Loja A", "2026-07-20") is True


def test_normalize_commercial_listing_flattens_one_row_per_variation_and_keeps_scope():
    listing = _listing()
    listing["variations"] = [
        {"id": 11, "seller_sku": "azul", "available_quantity": 3},
        {
            "id": 22,
            "attributes": [{"id": "SELLER_SKU", "value_name": "vermelho"}],
            "available_quantity": 4,
        },
    ]

    rows = margin.normalize_commercial_listing(listing, "000002", "JK Pecas")

    assert [(row["variation_id"], row["sku"]) for row in rows] == [("11", "AZUL"), ("22", "VERMELHO")]
    assert all(row["client_id"] == "000002" and row["store"] == "JK Pecas" for row in rows)
    assert all(row["item_id"] == "MLB123" for row in rows)
    assert rows[0]["price"] == 90
    assert rows[0]["regular_price"] == 100
    assert rows[0]["sale_fee_amount"] == 14.5
    assert rows[0]["shipping_seller_cost"] == 12.75
    assert rows[0]["commercial_coverage_complete"] is True
    assert margin.normalize_commercial_listing({"id": "MLA123"}, "000002", "JK Pecas") == []


def test_snapshot_uses_sqlite_cache_for_15_minutes_and_force_refresh_bypasses_it(tmp_path: Path):
    calls = {"listing": 0, "orders": 0}

    def listing_fetcher(**_: object) -> dict:
        calls["listing"] += 1
        return {"listings": [_listing()], "coverage": {"complete": True}}

    def orders_fetcher(**_: object) -> dict:
        calls["orders"] += 1
        return _empty_orders()

    kwargs = {
        "info_base": str(tmp_path / "info"),
        "client_id": "000002",
        "stores": ["JK Pecas"],
        "period_start": "2026-07-20",
        "period_end": "2026-07-20",
        "relevant_skus": ["SKU-1"],
        "listing_fetcher": listing_fetcher,
        "orders_fetcher": orders_fetcher,
    }
    first = margin.collect_marketplace_commercial_snapshot(**kwargs)
    second = margin.collect_marketplace_commercial_snapshot(**kwargs)
    refreshed = margin.collect_marketplace_commercial_snapshot(**kwargs, force_refresh=True)

    assert first["status"] == "ok"
    assert first["coverage"]["cache_hit"] is False
    assert second["coverage"]["cache_hit"] is True
    assert refreshed["coverage"]["cache_hit"] is False
    assert calls == {"listing": 2, "orders": 2}
    db = tmp_path / "info" / "000002" / "codex_assistant" / "codex_marketplace_margin.sqlite3"
    assert db.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM margin_snapshot_cache").fetchone()[0] == 1


def test_singleflight_deduplicates_concurrent_identical_collection(tmp_path: Path):
    calls = {"listing": 0, "orders": 0}
    calls_lock = threading.Lock()

    def listing_fetcher(**_: object) -> dict:
        with calls_lock:
            calls["listing"] += 1
        time.sleep(0.08)
        return {"listings": [_listing()], "coverage": {"complete": True}}

    def orders_fetcher(**_: object) -> dict:
        with calls_lock:
            calls["orders"] += 1
        return _empty_orders()

    kwargs = dict(
        info_base=str(tmp_path / "info"),
        client_id="000002",
        stores=["JK Pecas"],
        period_start="2026-07-20",
        period_end="2026-07-20",
        relevant_skus=["SKU-1"],
        listing_fetcher=listing_fetcher,
        orders_fetcher=orders_fetcher,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: margin.collect_marketplace_commercial_snapshot(**kwargs), range(2)))

    assert calls == {"listing": 1, "orders": 1}
    assert sorted(result["coverage"]["cache_hit"] for result in results) == [False, True]


def test_tenant_path_comparison_normalizes_windows_device_prefix():
    regular = Path(r"C:\temp\info\000002\codex_assistant")
    device = Path(r"\\?\C:\temp\info\000002\codex_assistant")

    assert margin._path_without_windows_device_prefix(device) == regular


def test_retry_honors_retry_after_without_real_sleep():
    sleeps: list[float] = []
    attempts = 0

    class TemporaryError(RuntimeError):
        def __init__(self):
            super().__init__("limited")
            self.response = SimpleNamespace(status_code=429, headers={"Retry-After": "2"})

    def callback(**_: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TemporaryError()
        return "ok"

    result = margin.call_with_retry(callback, sleep=sleeps.append)

    assert result == "ok"
    assert attempts == 2
    assert sleeps == [2.0]


def test_orders_are_reconciled_daily_in_pages_of_at_most_60(tmp_path: Path):
    orders = [
        _order(str(index), [f"MLB{1000 + index}"], pack_id=f"PACK-{index}", day="2026-07-20")
        for index in range(61)
    ]
    orders_by_id = {str(order["id"]): order for order in orders}
    requests: list[tuple[int, int]] = []
    billing_requests: list[list[str]] = []

    def orders_fetcher(**kwargs: object) -> dict:
        offset = int(kwargs["offset"])
        limit = int(kwargs["limit"])
        requests.append((offset, limit))
        return {
            "orders": orders[offset : offset + limit],
            "paging": {"total": len(orders)},
            "coverage": {"complete": True},
        }

    def order_billing_fetcher(**kwargs: object) -> dict:
        order_ids = list(kwargs["order_ids"])
        billing_requests.append(order_ids)
        return _billing_payload(order_ids, orders_by_id)

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        [],
        listing_fetcher=lambda **_: {"listings": [], "coverage": {"complete": True}},
        orders_fetcher=orders_fetcher,
        shipping_fetcher=lambda **_: {"seller_cost": 0},
        order_billing_fetcher=order_billing_fetcher,
    )

    assert requests == [(0, 60), (60, 60)]
    assert all(limit <= 60 for _, limit in requests)
    assert sorted(len(batch) for batch in billing_requests) == [1, 60]
    assert all(len(batch) <= 60 for batch in billing_requests)
    assert len(result["ledger_rows"]) == 61
    assert all(row["sale_fee_amount"] == 12 for row in result["ledger_rows"])
    assert all(row["sold_unit_price"] == row["unit_price"] for row in result["ledger_rows"])
    assert all(row["unit_cost"] is None and row["tax_pct"] is None for row in result["ledger_rows"])
    assert all(row["reconciliation_state"] == "reconciled" for row in result["ledger_rows"])
    assert all(any(source.get("resource") == "order_billing" for source in row["sources"]) for row in result["ledger_rows"])
    assert result["coverage"]["stores"]["JK Pecas"]["orders"]["days"][0]["orders"] == 61
    assert result["status"] == "ok"


def test_partial_order_failure_never_turns_unknown_coverage_into_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(margin, "MAX_RETRY_ATTEMPTS", 1)

    def unavailable(**_: object) -> dict:
        raise TimeoutError("timeout")

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        [],
        listing_fetcher=lambda **_: {"listings": [], "coverage": {"complete": True}},
        orders_fetcher=unavailable,
    )

    daily = result["coverage"]["stores"]["JK Pecas"]["orders"]["days"][0]
    assert result["status"] == "partial"
    assert daily["status"] == "partial"
    assert daily["orders"] is None
    assert daily["observed_orders"] == 0
    assert result["order_rows"] == []
    assert result["ledger_rows"] == []


def test_pack_shipping_is_whole_for_single_item_and_never_allocated_for_multi_item(tmp_path: Path):
    orders = [
        _order("1", ["MLB101"], pack_id="PACK-SINGLE"),
        _order("2", ["MLB201", "MLB202"], pack_id="PACK-MULTI"),
    ]
    orders_by_id = {str(order["id"]): order for order in orders}

    def shipping_fetcher(**kwargs: object) -> dict:
        return {"seller_cost": 8 if kwargs["shipment_id"] == "SHIP-PACK-SINGLE" else 20}

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        [],
        listing_fetcher=lambda **_: {"listings": [], "coverage": {"complete": True}},
        orders_fetcher=lambda **_: {"orders": orders, "paging": {"total": 2}, "coverage": {"complete": True}},
        shipping_fetcher=shipping_fetcher,
        order_billing_fetcher=lambda **kwargs: _billing_payload(list(kwargs["order_ids"]), orders_by_id),
    )

    single = [row for row in result["order_rows"] if row["pack_id"] == "PACK-SINGLE"]
    multi = [row for row in result["order_rows"] if row["pack_id"] == "PACK-MULTI"]
    assert single[0]["shipping_seller_cost"] == 8
    assert single[0]["seller_shipping_cost"] == 8
    assert single[0]["pack_item_count"] == 1
    assert single[0]["shipping_allocation_status"] == "whole_pack_single_item"
    assert all(row["shipping_seller_cost"] is None for row in multi)
    assert all(row["pack_item_count"] == 2 for row in multi)
    assert all(row["shipping_allocation_status"] == "pack_multi_item_not_allocated" for row in multi)
    assert sum(row["pack_shipping_total"] or 0 for row in multi) == 20


def test_order_billing_failure_persists_partial_rows_without_inventing_fee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(margin, "MAX_RETRY_ATTEMPTS", 1)
    order = _order("77", ["MLB777"], pack_id="PACK-77")

    def unavailable_billing(**_: object) -> dict:
        raise TimeoutError("billing timeout")

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        [],
        listing_fetcher=lambda **_: {"listings": [], "coverage": {"complete": True}},
        orders_fetcher=lambda **_: {"orders": [order], "paging": {"total": 1}, "coverage": {"complete": True}},
        shipping_fetcher=lambda **_: {"seller_cost": 7},
        order_billing_fetcher=unavailable_billing,
    )

    assert result["status"] == "partial"
    assert len(result["ledger_rows"]) == 1
    assert result["ledger_rows"][0]["sale_fee_amount"] is None
    assert result["ledger_rows"][0]["sale_fee_total"] is None
    assert result["ledger_rows"][0]["billing_reconciliation_state"] == "partial"
    assert result["ledger_rows"][0]["reconciliation_state"] == "partial"
    assert result["coverage"]["stores"]["JK Pecas"]["order_billing"]["completed"] == 0


def test_ledger_is_additive_idempotent_and_isolated_by_tenant_and_store(tmp_path: Path):
    ledger_a = margin.MarketplaceMarginLedger(str(tmp_path / "info"), "000002")
    ledger_b = margin.MarketplaceMarginLedger(str(tmp_path / "info"), "000003")
    row = margin.normalize_order_rows(_order("1", ["MLB101"], pack_id=""), "000002", "JK Pecas")[0]
    observed = datetime(2026, 7, 20, 15, tzinfo=timezone.utc)

    first = ledger_a.upsert_orders("JK Pecas", [row], observed_at=observed)
    second = ledger_a.upsert_orders("JK Pecas", [row], observed_at=observed)
    mark_one = ledger_a.mark_reconciled("JK Pecas", "2026-07-20", "complete", {"orders": 1}, reconciled_at=observed)
    mark_two = ledger_a.mark_reconciled("JK Pecas", "2026-07-20", "complete", {"orders": 1}, reconciled_at=observed)

    assert first["inserted"] == 1 and first["duplicates"] == 0
    assert second["inserted"] == 0 and second["duplicates"] == 1
    assert len(ledger_a.list_rows(["JK Pecas"], "2026-07-20", "2026-07-20")) == 1
    assert ledger_a.list_rows(["Outra Loja"], "2026-07-20", "2026-07-20") == []
    assert ledger_b.list_rows(["JK Pecas"], "2026-07-20", "2026-07-20") == []
    assert mark_one["inserted"] is True and mark_two["inserted"] is False
    assert ledger_a.should_reconcile("JK Pecas", "2026-07-20", now=observed) is False
    assert ledger_a.path != ledger_b.path


def test_later_order_event_preserves_confirmed_near_sale_cost_and_tax(tmp_path: Path):
    ledger = margin.MarketplaceMarginLedger(str(tmp_path / "info"), "tenant-a")
    original = {
        "client_id": "tenant-a", "store": "Loja A", "order_id": "ORDER-1", "line_number": 0,
        "order_date": "2026-07-01", "item_id": "MLB1", "variation_id": "V1", "sku": "A",
        "order_status": "paid", "unit_cost": 30, "tax_pct": 10,
        "historical_cost_confirmed": True, "historical_tax_confirmed": True,
        "historical_component_basis": "near_sale_reconciliation_snapshot",
        "cost_source": "cadastro_snapshot_reconciliacao", "tax_source": "cadastro_snapshot_reconciliacao",
    }
    ledger.upsert_orders("Loja A", [original], observed_at=datetime(2026, 7, 2, tzinfo=timezone.utc))

    update = {
        "client_id": "tenant-a", "store": "Loja A", "order_id": "ORDER-1", "line_number": 0,
        "order_date": "2026-07-01", "item_id": "MLB1", "variation_id": "V1", "sku": "A",
        "order_status": "partially_refunded", "unit_cost": 99, "tax_pct": 18,
        "historical_cost_confirmed": False, "historical_tax_confirmed": False,
        "historical_component_basis": "current_reconciliation_snapshot",
    }
    ledger.upsert_orders("Loja A", [update], observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc))

    latest = ledger.list_rows(["Loja A"], "2026-07-01", "2026-07-01")[0]
    assert latest["order_status"] == "partially_refunded"
    assert latest["unit_cost"] == 30
    assert latest["tax_pct"] == 10
    assert latest["historical_cost_confirmed"] is True
    assert latest["historical_tax_confirmed"] is True
    assert latest["historical_component_basis"] == "near_sale_reconciliation_snapshot"


def test_billing_enrichment_respects_bounded_concurrency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(margin, "MAX_CONCURRENCY", 2)
    active = 0
    maximum = 0
    guard = threading.Lock()
    listings = [_listing(f"MLB{100 + index}", f"SKU-{index}") for index in range(6)]
    for listing in listings:
        listing["details"] = {}

    def billing_fetcher(**_: object) -> dict:
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return {
            "price": {"amount": 100, "currency_id": "BRL"},
            "fees": {"sale_fee_amount": 12},
            "shipping": {"seller_cost": 5, "seller_cost_available": True},
            "coverage_complete": True,
        }

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        [],
        listing_fetcher=lambda **_: {"listings": listings, "coverage": {"complete": True}},
        orders_fetcher=_empty_orders,
        billing_fetcher=billing_fetcher,
    )

    assert maximum == 2
    assert len(result["listing_rows"]) == 6
    assert all(row["sale_fee_amount"] == 12 for row in result["listing_rows"])
    assert result["coverage"]["stores"]["JK Pecas"]["billing"] == {
        "status": "complete",
        "requested": 6,
        "completed": 6,
    }
    assert result["status"] == "ok"


def test_listing_scope_keeps_sold_or_in_stock_even_outside_relevant_skus(tmp_path: Path):
    in_stock = _listing("MLB501", "OUTSIDE-STOCK")
    in_stock["available_quantity"] = 2
    sold = _listing("MLB502", "OUTSIDE-SOLD")
    sold["available_quantity"] = 0
    neither = _listing("MLB503", "OUTSIDE-EMPTY")
    neither["available_quantity"] = 0
    order = _order("900", ["MLB502"], pack_id="PACK-900")

    result = margin.collect_marketplace_commercial_snapshot(
        str(tmp_path / "info"),
        "000002",
        ["JK Pecas"],
        "2026-07-20",
        "2026-07-20",
        ["ONLY-THIS-SKU"],
        listing_fetcher=lambda **_: {"listings": [in_stock, sold, neither], "coverage": {"complete": True}},
        orders_fetcher=lambda **_: {"orders": [order], "paging": {"total": 1}, "coverage": {"complete": True}},
        shipping_fetcher=lambda **kwargs: {"shipment_id": kwargs["shipment_id"], "seller_cost": 5},
        order_billing_fetcher=lambda **kwargs: _billing_payload(list(kwargs["order_ids"]), {"900": order}),
    )

    assert {row["item_id"] for row in result["listing_rows"]} == {"MLB501", "MLB502"}
    assert {row["item_id"] for row in result["ledger_rows"]} == {"MLB502"}
    assert any("sem estoque e sem venda" in warning for warning in result["warnings"])


def test_scope_validation_rejects_blank_store_and_unsafe_client(tmp_path: Path):
    with pytest.raises(ValueError, match="loja"):
        margin.collect_marketplace_commercial_snapshot(
            str(tmp_path / "info"), "000002", [""], "2026-07-20", "2026-07-20", []
        )
    with pytest.raises(ValueError, match="client_id"):
        margin.MarketplaceMarginLedger(str(tmp_path / "info"), "../000002")


def test_official_shipment_cost_shape_uses_exact_sender_cost():
    payload = {
        "_seller_id": "81387353",
        "gross_amount": 24.55,
        "receiver": {"user_id": 1, "cost": 0},
        "senders": [
            {"user_id": 999, "cost": 99},
            {"user_id": 81387353, "cost": 8.19},
        ],
    }

    assert margin._shipping_cost(payload) == 8.19


def test_variation_commercial_values_remain_separate():
    listing = _listing("MLB700", "PARENT")
    listing["variations"] = [
        {"id": "V1", "seller_sku": "SKU-V1", "price": 90, "available_quantity": 1},
        {"id": "V2", "seller_sku": "SKU-V2", "price": 120, "available_quantity": 2},
    ]
    listing["details"]["commercial"]["variations"] = {
        "V1": {
            "price": {"amount": 90, "currency_id": "BRL"},
            "fees": {"sale_fee_amount": 12},
            "shipping": {"seller_cost": 8, "seller_cost_available": True},
            "coverage_complete": True,
        },
        "V2": {
            "price": {"amount": 120, "currency_id": "BRL"},
            "fees": {"sale_fee_amount": 17},
            "shipping": {"seller_cost": 10, "seller_cost_available": True},
            "coverage_complete": True,
        },
    }

    rows = margin.normalize_commercial_listing(listing, "000002", "JK Pecas")

    assert [(row["variation_id"], row["sku"], row["price"], row["sale_fee_amount"], row["shipping_seller_cost"]) for row in rows] == [
        ("V1", "SKU-V1", 90, 12, 8),
        ("V2", "SKU-V2", 120, 17, 10),
    ]


def test_official_billing_sale_fee_net_is_used_only_when_order_has_one_line():
    one = [{"order_id": "1", "item_id": "MLB1", "variation_id": None, "sku": "A", "quantity": 2,
            "shipping_seller_cost": 4, "pack_item_count": 1, "sources": []}]
    reconciled, completed = margin.apply_order_billing(
        one,
        {"1": {"order_id": 1, "sale_fee": {"gross": 30, "net": 24, "rebate": 6}, "details": []}},
        store="JK Pecas",
    )

    assert completed == {"1"}
    assert reconciled[0]["sale_fee_amount"] is None
    assert reconciled[0]["sale_fee_total"] == 24

    multi = [
        {"order_id": "2", "item_id": "MLB2", "sku": "A", "quantity": 1, "pack_item_count": 2, "sources": []},
        {"order_id": "2", "item_id": "MLB3", "sku": "B", "quantity": 1, "pack_item_count": 2, "sources": []},
    ]
    partial, completed = margin.apply_order_billing(
        multi,
        {"2": {"order_id": 2, "sale_fee": {"net": 20}, "details": []}},
        store="JK Pecas",
    )

    assert completed == set()
    assert all(row.get("sale_fee_amount") is None and row.get("sale_fee_total") is None for row in partial)
