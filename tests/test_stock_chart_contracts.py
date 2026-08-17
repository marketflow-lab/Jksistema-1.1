from __future__ import annotations

import json

from backend.services import (
    codex_bling_tools,
    ia_tools_vendas,
    whatsapp_report_visuals,
)
from backend.services.codex.assistant import evidence as assistant_evidence


def _stale_provider_result() -> dict:
    result = {
        "loja": "Uai Mineirinho",
        "data_referencia": "2026-07-13",
        "total_skus_avaliados": 3,
        "total_skus_retornados": 3,
        "resultado_truncado": False,
        "itens": [
            {
                "sku": "A-1",
                "produto": "Produto nunca vendido",
                "saldo_loja": 10,
                "status": "nunca_vendeu",
                "dias_sem_vender": None,
                "custo_cadastrado": True,
                "valor_custo_estoque_loja": 125,
                "buyer_email": "nao-copiar@example.com",
            },
            {
                "sku": "B-2",
                "produto": "Produto antigo",
                "saldo_loja": 5,
                "status": "sem_venda_recente",
                "dias_sem_vender": 220,
                "custo_cadastrado": False,
                "telefone": "11999999999",
            },
            {
                "sku": "C-3",
                "produto": "Produto recente",
                "saldo_loja": 8,
                "status": "sem_venda_recente",
                "dias_sem_vender": 5,
                "custo_cadastrado": True,
                "valor_custo_estoque_loja": 80,
            },
        ],
    }
    result["chart_data"] = ia_tools_vendas._ia_stale_stock_chart_data(result)
    return {"function": "get_days_without_sale_top", "arguments": {}, "result": result}


def _stockout_provider_result() -> dict:
    result = {
        "loja": "Uai Mineirinho",
        "data_referencia": "2026-07-13",
        "janela_dias": 30,
        "total_skus_analisados": 3,
        "itens": [
            {
                "sku": "CRIT-1",
                "nome": "Produto crítico",
                "saldo_loja": 3,
                "saldo_full": 0,
                "saldo_total": 3,
                "quantidade_vendida_janela": 30,
                "media_venda_dia": 1,
                "dias_ate_ruptura": 3,
                "data_prevista_ruptura": "2026-07-16",
                "risco_ruptura": "critico",
                "comprador": "Pessoa protegida",
            },
            {
                "sku": "HIGH-2",
                "nome": "Produto alto risco",
                "saldo_loja": 10,
                "saldo_full": 0,
                "saldo_total": 10,
                "quantidade_vendida_janela": 30,
                "media_venda_dia": 1,
                "dias_ate_ruptura": 10,
                "data_prevista_ruptura": "2026-07-23",
                "risco_ruptura": "alto",
            },
            {
                "sku": "IDLE-3",
                "nome": "Produto sem consumo",
                "saldo_loja": 4,
                "saldo_full": 0,
                "saldo_total": 4,
                "quantidade_vendida_janela": 0,
                "media_venda_dia": 0,
                "dias_ate_ruptura": None,
                "data_prevista_ruptura": "",
                "risco_ruptura": "sem_consumo",
            },
        ],
    }
    result["chart_data"] = ia_tools_vendas._ia_stockout_chart_data(result)
    return {"function": "get_stockout_forecast", "arguments": {}, "result": result}


def test_stale_stock_contract_has_age_bands_complete_ranking_and_no_pii():
    chart = _stale_provider_result()["result"]["chart_data"]

    assert chart["schema"] == "jk.stock.stale_inventory.v1"
    assert chart["read_only"] is True
    assert chart["pii_included"] is False
    assert chart["coverage_complete"] is True
    assert chart["totals"] == {
        "stale_skus": 2,
        "stale_quantity": 15.0,
        "capital_known": 125.0,
        "skus_with_known_cost": 1,
        "skus_without_known_cost": 1,
    }
    assert [item["sku"] for item in chart["ranking"]] == ["A-1", "B-2"]
    assert chart["ranking"][0]["ranking_metric"] == "known_capital"
    assert chart["ranking"][1]["ranking_metric"] == "quantity"
    assert {item["key"]: item["skus"] for item in chart["age_bands"]} == {
        "never_sold": 1,
        "days_180_plus": 1,
        "days_90_179": 0,
        "days_30_89": 0,
    }
    serialized = json.dumps(chart, ensure_ascii=False)
    assert "nao-copiar@example.com" not in serialized
    assert "11999999999" not in serialized


def test_stockout_contract_has_risk_kpis_complete_ranking_and_no_pii():
    chart = _stockout_provider_result()["result"]["chart_data"]

    assert chart["schema"] == "jk.stock.stockout_forecast.v1"
    assert chart["coverage_complete"] is True
    assert chart["totals"]["at_risk_skus"] == 2
    assert chart["totals"]["critical_skus"] == 1
    assert [item["sku"] for item in chart["ranking"]] == ["CRIT-1", "HIGH-2", "IDLE-3"]
    assert {item["key"]: item["skus"] for item in chart["risk_bands"]}["sem_consumo"] == 1
    assert "Pessoa protegida" not in json.dumps(chart, ensure_ascii=False)


def test_bling_stock_contract_exposes_reliable_totals_and_all_deposits():
    rows = [
        {
            "loja": "Uai Mineirinho",
            "sku": "001",
            "produto": "Cebolão do Radiador Sensor Temperatura",
            "saldo_bruto_retornado": 186,
            "saldo_loja_total": 61,
            "saldo_loja_parcial": 61,
            "cobertura_depositos_completa": True,
            "depositos": [{"id": 1, "descricao": "Loja", "saldo_fisico": 61}],
            "depositos_excluidos": [
                {"id": 2, "descricao": "Fulfillment", "saldo_fisico": 113, "motivo": "Estoque Full/Fulfillment", "tipo_detectado": "FULL"},
                {"id": 3, "descricao": "Devoluções/Conserto", "saldo_fisico": 10, "motivo": "desconsiderarSaldo=true na Bling", "tipo_detectado": "LOJA"},
                {"id": 4, "descricao": "Defeito", "saldo_fisico": 2, "motivo": "desconsiderarSaldo=true na Bling", "tipo_detectado": "LOJA"},
                {"id": 5, "descricao": "Shopee Fulfillment", "saldo_fisico": 0, "motivo": "Estoque Full/Fulfillment", "tipo_detectado": "FULL"},
            ],
        }
    ]
    chart = codex_bling_tools._bling_stock_chart_data(rows, ["Uai Mineirinho"])

    assert chart["schema"] == "jk.stock.bling_balances.v1"
    assert chart["coverage_complete"] is True
    assert chart["totals"]["gross_returned"] == 186
    assert chart["totals"]["store_available"] == 61
    assert chart["totals"]["included_deposits"] == 1
    assert chart["totals"]["excluded_deposits"] == 4
    assert [item["name"] for item in chart["deposits"]["excluded"]][-1] == "Shopee Fulfillment"
    assert chart["pii_included"] is False


def test_bling_stock_contract_fails_closed_when_deposit_coverage_is_partial():
    chart = codex_bling_tools._bling_stock_chart_data(
        [
            {
                "loja": "JK Peças",
                "sku": "X",
                "produto": "Produto X",
                "saldo_bruto_retornado": 12,
                "saldo_loja_total": None,
                "saldo_loja_parcial": 5,
                "cobertura_depositos_completa": False,
                "depositos": [{"id": 1, "descricao": "Loja", "saldo_fisico": 5}],
                "depositos_excluidos": [{"id": 999, "descricao": "Depósito ID 999", "saldo_fisico": 7, "motivo": "não classificado"}],
            }
        ],
        ["JK Peças"],
    )

    assert chart["coverage_complete"] is False
    assert chart["partial"] is True
    assert chart["totals"]["store_available"] is None
    assert chart["totals"]["classified_quantity"] == 5
    assert chart["ranking"][0]["quantity_reliable"] is False


def test_whatsapp_visuals_accept_raw_provider_results_without_tool_id():
    stale = whatsapp_report_visuals.build_chart_data(
        "analise de estoque parado",
        [_stale_provider_result()],
        {"store": "Uai Mineirinho"},
    )
    stockout = whatsapp_report_visuals.build_chart_data(
        "analise de ruptura",
        [_stockout_provider_result()],
        {"store": "Uai Mineirinho"},
    )
    bling_chart = codex_bling_tools._bling_stock_chart_data(
        [{
            "loja": "Uai Mineirinho",
            "sku": "001",
            "produto": "Produto",
            "saldo_bruto_retornado": 8,
            "saldo_loja_total": 5,
            "saldo_loja_parcial": 5,
            "cobertura_depositos_completa": True,
            "depositos": [{"id": 1, "descricao": "Loja", "saldo_fisico": 5}],
            "depositos_excluidos": [{"id": 2, "descricao": "Full", "saldo_fisico": 3, "motivo": "Estoque Full/Fulfillment", "tipo_detectado": "FULL"}],
        }],
        ["Uai Mineirinho"],
    )
    bling = whatsapp_report_visuals.build_chart_data(
        "analise de estoque",
        [{"function": "bling_stock_balances", "arguments": {}, "result": {"chart_data": bling_chart}}],
        {"store": "Uai Mineirinho"},
    )

    assert stale["analysis_type"] == "stale_inventory"
    assert len(stale["ranking"]) == 2
    assert stockout["analysis_type"] == "stockout_forecast"
    assert len(stockout["ranking"]) == 3
    assert stockout["ranking"][0]["display_value"] == "Crítico — 3 dia(s)"
    assert stockout["ranking"][-1]["display_value"] == "Sem consumo — sem consumo"
    assert all(item["value_label"] == "Risco" for item in stockout["ranking"])
    assert bling["analysis_type"] == "stock_bling"
    assert [item["label"] for item in bling["categories"]] == [
        "Loja — incluído",
        "Full — Estoque Full/Fulfillment",
    ]


def test_whatsapp_visuals_normalize_raw_marketplace_orders_and_listing():
    orders_chart = {
        "schema": "jk.marketplace.sales_by_day.v1",
        "store": "JK Peças",
        "points": [{"date": "2026-07-13", "orders": 2, "items_quantity": 3, "gross_amount": 150, "paid_amount": 150, "refund_amount": 0, "net_amount": 150}],
        "totals": {"orders": 2, "items_quantity": 3, "gross_amount": 150, "paid_amount": 150, "refund_amount": 0, "net_amount": 150},
        "coverage_complete": True,
        "pii_included": False,
        "read_only": True,
    }
    sales = whatsapp_report_visuals.build_chart_data(
        "relatorio de vendas",
        [{"function": "get_mercado_livre_orders", "result": {"chart_data": orders_chart, "by_sku": [{"sku": "001", "title": "Produto", "quantity": 3, "gross_amount": 150}]}}],
        {"store": "JK Peças"},
    )
    listing_chart = {
        "schema": "jk.marketplace.listing_snapshot.v1",
        "points": [{"item_id": "MLB1", "sku": "001", "title": "Anúncio", "status": "active", "sold_quantity": 7}],
        "coverage_complete": True,
        "pii_included": False,
        "read_only": True,
    }
    listing = whatsapp_report_visuals.build_chart_data(
        "analise de anuncios",
        [{"function": "get_mercado_livre_listing", "result": {"chart_data": listing_chart, "loja": "JK Peças"}}],
        {"store": "JK Peças"},
    )

    assert sales["analysis_type"] == "sales"
    assert sales["kpis"]["orders"] == 2
    assert sales["ranking"][0]["sku"] == "001"
    assert listing["analysis_type"] == "listings"
    assert listing["ranking"][0]["sku"] == "001"


def test_codex_package_preserves_only_the_requested_provider_chart_contract():
    stale_raw = _stale_provider_result()
    unrelated_sales = {
        "function": "get_mercado_livre_orders",
        "result": {"chart_data": {"schema": "jk.marketplace.sales_by_day.v1", "points": []}},
    }
    package = assistant_evidence._assistant_agent_result_package(
        "tenant-a",
        "stale_stock",
        {},
        [unrelated_sales, stale_raw],
        [{"tool_id": "stale_stock", "records": 3, "rows": stale_raw["result"]["itens"], "summary": {}}],
        [],
        permissions={"full": True},
    )

    assert package["chart_data"]["schema"] == "jk.stock.stale_inventory.v1"
    assert [item["sku"] for item in package["chart_data"]["ranking"]] == ["A-1", "B-2"]
    assert package["chart_data"]["pii_included"] is False
