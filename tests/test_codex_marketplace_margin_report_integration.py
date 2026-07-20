from unittest.mock import patch

from backend.services import codex_assistant, codex_reports_advanced


def _advanced_base():
    return {
        "report_type": "weekly_sales_stock",
        "scope": {
            "period_start": "2026-07-13",
            "period_end": "2026-07-19",
            "comparison_start": "2026-07-06",
            "comparison_end": "2026-07-12",
            "stores": ["Loja A"],
        },
        "financial_summary": {"gross_revenue_brl": 100, "returns_brl": 0, "advertising_brl": 0},
        "financial_coverage": {"minimum_required_pct": 95},
        "store_summaries": [{"store": "Loja A", "gross_revenue_brl": 100, "advertising_brl": 0}],
        "sales_rows": [{"store": "Loja A", "sku": "A", "unit_cost": 30, "tax_pct": 10, "revenue": 100}],
        "inventory_rows": [],
        "data_quality": {"warnings": [], "source_health": []},
        "top_actions": [],
    }


def test_weekly_profile_makes_readonly_commercial_collection_mandatory():
    snapshot = {
        "status": "ok",
        "collected_at": "2026-07-20T12:00:00-03:00",
        "stores": ["Loja A"],
        "listing_rows": [
            {"store": "Loja A", "item_id": "MLB1", "variation_id": "V1", "sku": "A", "price": 100,
             "sale_fee_amount": 12, "shipping_seller_cost": 8, "currency_id": "BRL"}
        ],
        "ledger_rows": [],
        "sources": [{"provider": "mercado_livre", "method": "GET"}],
        "warnings": [],
    }
    context = {"tool_plan": {}, "warnings": []}

    with (
        patch.object(codex_reports_advanced, "build_profile_context", return_value=_advanced_base()),
        patch.object(codex_assistant, "_assistant_connected_ml_stores", return_value=["Loja A"]),
        patch.object(codex_assistant, "_assistant_marketplace_fetchers", return_value={
            "listing_fetcher": object(), "orders_fetcher": object(), "shipping_fetcher": object(),
            "billing_fetcher": object(), "order_billing_fetcher": object(),
        }),
        patch("backend.services.codex_marketplace_margin.collect_marketplace_commercial_snapshot", return_value=snapshot) as collect,
        patch.object(codex_assistant, "_assistant_enrich_marketplace_ledger", side_effect=lambda _client, _advanced, value: value),
        patch.object(codex_assistant, "_assistant_info_base", return_value="C:/tmp/info"),
    ):
        result = codex_assistant._assistant_apply_advanced_profile(
            "tenant-a",
            context,
            "weekly_sales_stock",
            force_refresh=True,
            prompt="relatorio semanal",
        )

    assert collect.call_count == 1
    assert collect.call_args.kwargs["force_refresh"] is True
    assert result["marketplace_commercial"]["status"] == "ok"
    assert result["listing_margin_rows"][0]["mlb"] == "MLB1"
    assert result["listing_margin_rows"][0]["unit_contribution_brl"] == 40


def test_import_order_and_nonfinancial_custom_do_not_collect_marketplace():
    for profile, prompt in (("import_order", "analise da importacao"), ("custom", "posicao de estoque")):
        base = _advanced_base()
        base["report_type"] = profile
        with (
            patch.object(codex_reports_advanced, "build_profile_context", return_value=base),
            patch("backend.services.codex_marketplace_margin.collect_marketplace_commercial_snapshot") as collect,
        ):
            codex_assistant._assistant_apply_advanced_profile(
                "tenant-a",
                {"tool_plan": {}, "warnings": []},
                profile,
                prompt=prompt,
            )
        collect.assert_not_called()


def test_marketplace_collection_profile_contract_is_explicit():
    assert codex_assistant._assistant_profile_requires_marketplace("weekly_sales_stock") is True
    assert codex_assistant._assistant_profile_requires_marketplace("daily_exceptions") is True
    assert codex_assistant._assistant_profile_requires_marketplace(
        "custom", "analise financeira de margem e custo do Mercado Livre"
    ) is True
    assert codex_assistant._assistant_profile_requires_marketplace("custom", "posicao de estoque") is False
    assert codex_assistant._assistant_profile_requires_marketplace(
        "import_order", "margem financeira da importacao"
    ) is False


def test_nested_commercial_payload_does_not_choose_first_mlb_for_same_sku():
    context = {
        "client_id": "tenant-a",
        "tool_plan": {"loja": "Loja A"},
        "tool_results": [
            {
                "function": "get_mercado_livre_listing",
                "result": {
                    "matches": [
                        {"id": "MLB1", "loja": "Loja A", "seller_sku": "A", "details": {"commercial": {
                            "price": {"amount": 100}, "fees": {"sale_fee_amount": 12},
                            "shipping": {"seller_cost": 8, "free_shipping": True},
                        }}},
                        {"id": "MLB2", "loja": "Loja A", "seller_sku": "A", "details": {"commercial": {
                            "price": {"amount": 120}, "fees": {"sale_fee_amount": 14},
                            "shipping": {"seller_cost": 9, "free_shipping": True},
                        }}},
                    ]
                },
            }
        ],
    }
    with (
        patch.object(codex_assistant, "_assistant_collect_sales_rank_rows", return_value=[
            {"sku": "A", "quantidade_num": 10, "valor_num": 1000, "produto": "Produto A", "store": "Loja A"}
        ]),
        patch.object(codex_assistant, "_assistant_load_margin_cost_maps", return_value=({"A": 30}, {"A": 10}, [])),
        patch.object(codex_assistant, "_assistant_resolve_margin_cost_tax", return_value=(30, 10)),
    ):
        rows = codex_assistant._assistant_collect_margin_rows(context)

    assert [row["MLB"] for row in rows] == ["MLB1", "MLB2"]
    assert rows[0]["Contribuicao unitaria atual"] == "R$ 40,00"
    assert rows[1]["Contribuicao unitaria atual"] == "R$ 55,00"
    assert all(row["Lucro estimado"] == "" for row in rows)
    assert all("nao rateado" in row["Status da margem"] for row in rows)


def test_listing_margin_never_inherits_cost_or_tax_from_another_store():
    base = _advanced_base()
    base["sales_rows"] = [
        {"store": "Loja A", "sku": "A", "unit_cost": 30, "tax_pct": 10, "revenue": 100}
    ]
    snapshot = {
        "status": "ok",
        "listing_rows": [
            {"store": "Loja B", "item_id": "MLB2", "variation_id": "V1", "sku": "A", "price": 100,
             "sale_fee_amount": 12, "shipping_seller_cost": 8, "currency_id": "BRL"}
        ],
        "ledger_rows": [],
    }

    result = codex_reports_advanced.apply_marketplace_commercial(base, snapshot)
    row = result["listing_margin_rows"][0]
    assert row["store"] == "Loja B"
    assert row["unit_cost_brl"] is None
    assert row["tax_pct"] is None
    assert row["margin_status"] == "unavailable"
    assert row["cost_scope"] == "unavailable"
    assert row["tax_scope"] == "unavailable"


def test_explicit_global_cost_fallback_is_identified():
    base = _advanced_base()
    base["sales_rows"] = [
        {"store": "", "cost_scope": "global", "sku": "A", "unit_cost": 30, "tax_pct": 10, "revenue": 0}
    ]
    snapshot = {"status": "ok", "listing_rows": [
        {"store": "Loja B", "item_id": "MLB2", "variation_id": "V1", "sku": "A", "price": 100,
         "sale_fee_amount": 12, "shipping_seller_cost": 8, "currency_id": "BRL"}
    ], "ledger_rows": []}

    row = codex_reports_advanced.apply_marketplace_commercial(base, snapshot)["listing_margin_rows"][0]
    assert row["margin_status"] == "available"
    assert row["cost_scope"] == "global"
    assert row["tax_scope"] == "global"


def test_old_sale_with_current_cost_snapshot_stays_out_of_historical_margin(tmp_path):
    advanced = _advanced_base()
    advanced["scope"]["period_start"] = "2026-07-01"
    snapshot = {
        "collected_at": "2026-07-20T12:00:00-03:00",
        "stores": ["Loja A"],
        "ledger_rows": [
            {
                "client_id": "tenant-a", "store": "Loja A", "order_id": "ORDER-OLD", "line_number": 0,
                "order_date": "2026-07-01", "item_id": "MLB1", "variation_id": "V1", "sku": "A",
                "quantity": 1, "unit_price": 100, "gross_amount": 100, "sale_fee_amount": 12,
                "shipping_seller_cost": 8, "pack_item_count": 1,
            }
        ],
    }
    with patch.object(codex_assistant, "_assistant_info_base", return_value=str(tmp_path)):
        enriched = codex_assistant._assistant_enrich_marketplace_ledger("tenant-a", advanced, snapshot)

    ledger_row = enriched["ledger_rows"][0]
    assert ledger_row["unit_cost"] == 30
    assert ledger_row["historical_cost_confirmed"] is False
    assert ledger_row["historical_tax_confirmed"] is False
    assert ledger_row["reconciliation_state"] == "partial"

    result = codex_reports_advanced.apply_marketplace_commercial(advanced, enriched)
    assert result["historical_margin_ledger"][0]["margin_status"] == "unavailable"
    assert result["historical_margin_ledger"][0]["contribution_total_brl"] is None
    assert result["financial_coverage"]["contribution_margin_valid"] is False


def test_historical_deduplication_preserves_repeated_order_lines():
    base = _advanced_base()
    base["financial_summary"]["gross_revenue_brl"] = 200
    common = {
        "store": "Loja A", "order_id": "ORDER-1", "item_id": "MLB1", "variation_id": "V1", "sku": "A",
        "quantity": 1, "unit_price": 100, "gross_amount": 100, "unit_cost": 30, "tax_pct": 10,
        "sale_fee_amount": 12, "shipping_seller_cost": 8, "pack_item_count": 1,
        "reconciliation_state": "reconciled",
    }
    snapshot = {"status": "ok", "listing_rows": [], "ledger_rows": [
        {**common, "line_number": 0},
        {**common, "line_number": 1},
    ]}

    result = codex_reports_advanced.apply_marketplace_commercial(base, snapshot)
    assert len(result["historical_margin_ledger"]) == 2
    assert result["financial_coverage"]["covered_revenue_brl"] == 200
    assert result["financial_coverage"]["complete_margin_by_revenue_pct"] == 100


def test_financial_output_uses_contribution_language_not_net_profit_claim():
    context = _advanced_base()
    context["financial_summary"]["net_profit_after_ads_brl"] = 40
    html = codex_assistant._assistant_build_advanced_report_html("Relatorio", context)
    chat = codex_assistant._assistant_advanced_report_chat_text("Relatorio", context)
    assert "Resultado de contribuicao apos publicidade" in html
    assert "Resultado de contribuicao apos publicidade" in chat
    assert "Lucro apos publicidade" not in html
    assert "Lucro apos publicidade" not in chat
