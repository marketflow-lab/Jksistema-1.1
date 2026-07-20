from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services import codex_assistant, codex_bling_tools, whatsapp_bridge
from backend.services.whatsapp import intent, tool_results
from backend.services.whatsapp.orchestration import manager_results


@pytest.mark.parametrize(
    "message",
    [
        "Quantos SKUs estao com estoque na loja?",
        "Quantos SKUs estao em estoque?",
        "Total de SKUs em estoque",
        "Qual a quantidade de produtos com saldo positivo?",
        "Me diga o total de itens que tem estoque",
    ],
)
def test_positive_stock_count_intent_is_catalog_wide(message: str) -> None:
    assert intent.positive_stock_sku_count_requested(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Quantas unidades tem o SKU 245?",
        "Qual o saldo do SKU ABC-10?",
        "Quantos SKUs estao no Full?",
    ],
)
def test_positive_stock_count_intent_does_not_replace_exact_or_full_balance(message: str) -> None:
    assert intent.positive_stock_sku_count_requested(message) is False


def test_assistant_routes_count_only_to_aggregate_tool() -> None:
    message = "Ok, mas quero saber quantos SKU estao com estoque na loja"
    policy = codex_assistant._assistant_source_routing_policy(message)

    assert policy["required_tools"] == ["bling_positive_stock_sku_count"]
    assert "bling_stock_balances" in policy["forbidden_tools"]
    assert policy["aggregation_policy"] == "single_store_scalar_no_sum"
    assert codex_assistant._assistant_select_tool_ids(message, "chat", {}) == [
        "bling_positive_stock_sku_count"
    ]


def test_whatsapp_manager_keeps_only_aggregate_call_and_drops_false_sku() -> None:
    message = "Quantos SKUs estao com estoque? Loja selecionada: Loja 245"
    source_policy = codex_assistant._assistant_source_routing_policy(message)
    plan = whatsapp_bridge._function_manager_enforce_plan(
        {
            "tool_calls": [
                {"tool_id": "bling_stock_balances", "arguments": {"sku": "s"}, "required": True}
            ],
            "requires_sol": False,
            "requires_web": False,
        },
        request_text=message,
        query_policy={
            "store": "Loja 245",
            "store_mode": "single",
            "source_policy": source_policy,
        },
        catalog=[
            {"id": "bling_positive_stock_sku_count"},
            {"id": "bling_stock_balances"},
            {"id": "mercado_livre_listing"},
            {"id": "stock_data"},
        ],
        max_calls=6,
    )

    assert [call["tool_id"] for call in plan["tool_calls"]] == [
        "bling_positive_stock_sku_count"
    ]
    assert plan["sku"] == ""
    assert "sku" not in plan["tool_calls"][0]["arguments"]
    assert plan["tool_calls"][0]["arguments"]["loja"] == "Loja 245"
    assert plan["manager_guard"]["positive_stock_sku_count"] is True


def test_complete_paginator_reads_until_short_page() -> None:
    pages = {
        1: [{"id": index} for index in range(100)],
        2: [{"id": 100}],
    }

    def fake_get(_token, _path, params, **_kwargs):
        page = int(dict(params)["pagina"])
        return pages.get(page, []), 200

    with patch.object(codex_bling_tools, "_get_json", side_effect=fake_get):
        rows, status, complete, meta = codex_bling_tools._list_complete_paginated(
            "token",
            "/produtos",
            {},
            max_records=500,
            deadline=None,
        )

    assert status == 200
    assert complete is True
    assert len(rows) == 101
    assert meta["pages"] == 2


def test_complete_paginator_marks_hard_cap_as_incomplete() -> None:
    def fake_get(_token, _path, params, **_kwargs):
        requested = int(dict(params)["limite"])
        return [{"id": index} for index in range(requested)], 200

    with patch.object(codex_bling_tools, "_get_json", side_effect=fake_get):
        rows, status, complete, meta = codex_bling_tools._list_complete_paginated(
            "token",
            "/produtos",
            {},
            max_records=150,
            deadline=None,
        )

    assert status == 200
    assert len(rows) == 150
    assert complete is False
    assert "limite seguro" in meta["reason"]


def test_complete_paginator_rejects_invalid_page_rows() -> None:
    with patch.object(
        codex_bling_tools,
        "_get_json",
        return_value=([{"id": 1}, "invalid"], 200),
    ):
        rows, status, complete, meta = codex_bling_tools._list_complete_paginated(
            "token",
            "/produtos",
            {},
            max_records=500,
            deadline=None,
        )

    assert rows == [{"id": 1}]
    assert status == 502
    assert complete is False
    assert "invalido" in meta["reason"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_inventory_number_rejects_non_finite_values(value) -> None:
    quantity, valid = codex_bling_tools._bling_inventory_number(value)

    assert quantity == 0
    assert valid is False


def _catalog_and_deposits(path: str):
    if path == "/produtos":
        return (
            [
                {"id": 1, "codigo": "A", "tipo": "P"},
                {"id": 2, "codigo": "B", "tipo": "P"},
                {"id": 3, "codigo": "b", "tipo": "P"},
                {"id": 4, "codigo": "", "tipo": "P"},
                {"id": 5, "codigo": "SERV", "tipo": "S"},
            ],
            200,
            True,
            {"pages": 1, "reason": ""},
        )
    assert path == "/depositos"
    return (
        [
            {"id": 10, "descricao": "Estoque principal", "situacao": 1, "desconsiderarSaldo": False},
            {"id": 20, "descricao": "Mercado Livre Full", "situacao": 1, "desconsiderarSaldo": False},
            {"id": 30, "descricao": "Inativo", "situacao": 0, "desconsiderarSaldo": False},
            {"id": 40, "descricao": "Ignorado", "situacao": 1, "desconsiderarSaldo": True},
        ],
        200,
        True,
        {"pages": 1, "reason": ""},
    )


def test_snapshot_counts_distinct_positive_skus_and_excludes_full() -> None:
    balance_rows = [
        {"produto": {"id": 1}, "depositos": [{"id": 10, "saldoFisico": 2}, {"id": 20, "saldoFisico": 100}]},
        {"produto": {"id": 2}, "depositos": [{"id": 10, "saldoFisico": 3}]},
        {"produto": {"id": 3}, "depositos": [{"id": 10, "saldoFisico": -1}]},
        {"produto": {"id": 4}, "depositos": [{"id": 10, "saldoFisico": 5}]},
    ]

    with (
        patch.object(codex_bling_tools, "_list_complete_paginated", side_effect=lambda _t, path, *_a, **_k: _catalog_and_deposits(path)),
        patch.object(codex_bling_tools, "_get_json", return_value=(balance_rows, 200)),
    ):
        snapshot, status = codex_bling_tools._bling_positive_stock_snapshot(
            "token", "JK Pecas", deadline=None
        )

    assert status == 200
    assert snapshot["coverage_complete"] is True
    assert snapshot["positive_sku_count"] == 2
    assert snapshot["store_available"] == 9
    assert snapshot["catalog_distinct_skus"] == 2
    assert snapshot["duplicate_skus_collapsed"] == 1
    assert snapshot["products_without_sku_positive"] == 1
    assert snapshot["services_excluded"] == 1
    assert snapshot["full_deposit_rows_excluded"] == 1


def test_snapshot_never_confirms_count_with_unknown_deposit() -> None:
    balance_rows = [
        {"produto": {"id": product_id}, "depositos": [{"id": 999, "saldoFisico": 2}]}
        for product_id in (1, 2, 3, 4)
    ]

    with (
        patch.object(codex_bling_tools, "_list_complete_paginated", side_effect=lambda _t, path, *_a, **_k: _catalog_and_deposits(path)),
        patch.object(codex_bling_tools, "_get_json", return_value=(balance_rows, 200)),
    ):
        snapshot, status = codex_bling_tools._bling_positive_stock_snapshot(
            "token", "JK Pecas", deadline=None
        )

    assert status == 200
    assert snapshot["coverage_complete"] is False
    assert snapshot["positive_sku_count"] is None
    assert "nao classificado" in snapshot["partial_reason"]


def test_zero_is_confirmed_and_formats_as_sku_count() -> None:
    result = {
        "tool_id": "bling_positive_stock_sku_count",
        "manager_store": "JK Pecas",
        "top_rows": [
            {
                "schema": "jk.stock.bling_positive_sku_count.v1",
                "loja": "JK Pecas",
                "positive_sku_count": 0,
                "store_available": 0,
                "catalog_products_scanned": 10,
                "balances_requested": 10,
                "balances_returned": 10,
                "coverage_complete": True,
                "full_excluded": True,
            }
        ],
    }

    contract = tool_results.positive_stock_sku_count_contract(result)
    text = manager_results._deterministic_positive_stock_sku_count_text(
        {"tool_results": [result]}, {}
    )

    assert contract["confirmed"] is True
    assert contract["positive_sku_count"] == 0
    assert "*0 SKUs*" in text
    assert "SKU 245" not in text

    compact = tool_results.function_manager_compact_result({"success": True, **result})
    assert compact["dados_suficientes"] is True
    assert compact["coverage_complete"] is True
    assert compact["positive_stock_sku_count"]["positive_sku_count"] == 0


def test_partial_result_formatter_does_not_expose_observed_count_as_exact() -> None:
    result = {
        "tool_id": "bling_positive_stock_sku_count",
        "manager_store": "JK Pecas",
        "top_rows": [
            {
                "schema": "jk.stock.bling_positive_sku_count.v1",
                "loja": "JK Pecas",
                "positive_sku_count": None,
                "positive_sku_count_observed": 245,
                "catalog_products_scanned": 300,
                "balances_requested": 300,
                "balances_returned": 200,
                "coverage_complete": False,
                "partial_reason": "timeout",
            }
        ],
    }

    text = manager_results._deterministic_positive_stock_sku_count_text(
        {"tool_results": [result]}, {}
    )

    assert "Nao consegui confirmar" in text
    assert "245" not in text
    assert "Nenhum numero parcial" in text


def test_tool_rejects_default_tenant_without_loading_store_configuration() -> None:
    with patch.object(codex_bling_tools, "_connected_stores") as connected:
        raw = codex_bling_tools.tool_bling_positive_stock_sku_count(
            "default", "quantos SKUs tem estoque", "JK Pecas"
        )

    connected.assert_not_called()
    assert raw["result"]["records"] == 0
    assert raw["result"]["coverage_complete"] is False


def test_tool_requires_exact_store_and_disables_default_tenant_fallback() -> None:
    with patch.object(codex_bling_tools, "_connected_stores", return_value=([], ["loja ausente"])) as connected:
        raw = codex_bling_tools.tool_bling_positive_stock_sku_count(
            "tenant-123", "quantos SKUs tem estoque", "JK Pecas"
        )

    connected.assert_called_once_with(
        "tenant-123",
        "JK Pecas",
        require_exact=True,
        allow_default_fallback=False,
    )
    assert raw["result"]["records"] == 0
    assert raw["result"]["coverage_complete"] is False
