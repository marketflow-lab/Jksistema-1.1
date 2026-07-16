import pytest

from backend.services import whatsapp_bridge
from backend.services.whatsapp import tool_results


def _bling_result(quantity, *, coverage: bool = True, warnings: list[str] | None = None) -> dict:
    return {
        "success": True,
        "tool_id": "bling_stock_balances",
        "summary": [
            {
                "tool_id": "bling_stock_balances",
                "loja": "JK Pecas",
                "summary": {
                    "stock_scope": "bling_non_full_only",
                    "full_provider": "mercado_livre_api_only",
                    "chart_data": {
                        "stores": ["JK Pecas"],
                        "totals": {"skus": 1, "store_available": quantity},
                        "ranking": [
                            {
                                "sku": "001",
                                "title": "Produto 001",
                                "quantity": quantity,
                                "quantity_reliable": coverage,
                            }
                        ],
                        "coverage_complete": coverage,
                    },
                    "partial": not coverage,
                },
            }
        ],
        "tool_validation": {
            "dados_suficientes": coverage,
            "warnings": warnings or [],
        },
    }


@pytest.mark.parametrize("quantity", [12, 0, 1.5])
def test_bling_positive_and_zero_balances_are_confirmed(quantity) -> None:
    source = _bling_result(quantity)
    component = tool_results.stock_balance_contract(source)
    assert component == whatsapp_bridge._stock_balance_contract(source)
    assert component["confirmed"] is True
    assert component["store_available"] == float(quantity)
    assert component["full_excluded"] is True


def test_expired_auth_and_partial_coverage_are_not_confirmed() -> None:
    expired = _bling_result(None, coverage=False, warnings=["Token Bling expirado para esta loja."])
    contract = tool_results.stock_balance_contract(expired)
    assert contract == whatsapp_bridge._stock_balance_contract(expired)
    assert contract["confirmed"] is False
    assert contract["auth_failed"] is True
    assert contract["error_class"] == "authentication"
    assert contract["retryable"] is False

    partial = tool_results.stock_balance_contract(_bling_result(10, coverage=False))
    assert partial["confirmed"] is False
    assert partial["error_class"] == "insufficient_evidence"


@pytest.mark.parametrize(
    ("source", "expected_class", "retryable", "coverage"),
    [
        ({"success": False, "error": "HTTP 401 token expirado"}, "authentication", False, False),
        ({"success": False, "error": "HTTP 504 timeout"}, "transient_dependency", True, False),
        (
            {"success": True, "data": [{"id": 1}], "source": "Bling", "coverage_complete": True},
            "",
            False,
            True,
        ),
        (
            {"success": True, "data": [{"id": 1}], "paging": {"has_more": True}},
            "",
            False,
            False,
        ),
    ],
)
def test_generic_tool_contract_classifies_errors_and_coverage(source, expected_class, retryable, coverage) -> None:
    component = tool_results.normalize_tool_result_contract(source)
    assert component == whatsapp_bridge._normalize_tool_result_contract(source)
    assert component["error_class"] == expected_class
    assert component["retryable"] is retryable
    assert component["coverage_complete"] is coverage


def test_compaction_preserves_contract_and_limits_large_payloads() -> None:
    source = {
        "success": True,
        "tool_id": "product_data",
        "rows": [{"index": index, "description": "x" * 1000} for index in range(100)],
        "source": "Cadastro local",
    }
    component = tool_results.function_manager_compact_result(source)
    assert component == whatsapp_bridge._function_manager_compact_result(source)
    assert isinstance(component["rows"], str)
    assert len(component["rows"]) == 12000
    assert component["records"] == 100

    bling = tool_results.function_manager_compact_result(_bling_result(0))
    assert bling["stock_balance"]["confirmed"] is True
    assert bling["dados_suficientes"] is True


def test_marketplace_and_local_stock_require_numeric_complete_contracts() -> None:
    listing = {
        "tool_id": "mercado_livre_listing",
        "dados_suficientes": True,
        "coverage_complete": True,
        "top_rows": [{"id": "MLB1", "seller_sku": "001", "title": "Produto", "available_quantity": 0}],
    }
    listing_contract = tool_results.marketplace_listing_stock_contract(listing)
    assert listing_contract == whatsapp_bridge._marketplace_listing_stock_contract(listing)
    assert listing_contract["confirmed"] is True
    assert tool_results.stock_tool_result_confirmed(listing) is True

    local = {
        "tool_id": "stock_data",
        "dados_suficientes": True,
        "summary": [
            {
                "tool_id": "stock_data",
                "summary": {
                    "found": True,
                    "sku": "001",
                    "nome": "Produto",
                    "saldo_loja_total": 3,
                    "saldo_full_total": 2,
                    "saldo_total": 5,
                },
            }
        ],
    }
    local_contract = tool_results.local_stock_contract(local)
    assert local_contract == whatsapp_bridge._local_stock_contract(local)
    assert local_contract["confirmed"] is True
    assert local_contract["total_available"] == 5


def test_evidence_marks_required_partial_result_and_preserves_prior_confirmation() -> None:
    plan = {"tool_calls": [{"tool_id": "bling_stock_balances"}]}
    results = [
        {
            "tool_id": "bling_stock_balances",
            "success": False,
            "manager_required": True,
            "dados_suficientes": False,
            "coverage_complete": False,
            "error": "token expirado",
            "error_class": "authentication",
        }
    ]
    component = tool_results.function_manager_evidence(plan, results)
    assert component == whatsapp_bridge._function_manager_evidence(plan, results)
    assert component["status"] == "partial"
    assert component["evidence_sufficient"] is False
    assert component["failures"] == ["token expirado"]

    previous = {
        "status": "completed",
        "evidence_sufficient": True,
        "coverage_complete": True,
        "confidence": "high",
        "verified_facts": ["Saldo 10"],
        "sources": ["Bling"],
        "validations": [{"tool_id": "stock", "required": True, "dados_suficientes": True}],
        "tool_results": [{"tool_id": "stock", "success": True}],
    }
    current = {
        "status": "partial",
        "evidence_sufficient": False,
        "coverage_complete": False,
        "confidence": "low",
        "verified_facts": [],
        "sources": [],
        "validations": [{"tool_id": "stock", "required": True, "dados_suficientes": False}],
        "tool_results": [{"tool_id": "stock", "success": False}],
    }
    merged = tool_results.function_manager_merge_evidence(previous, current)
    assert merged == whatsapp_bridge._function_manager_merge_evidence(previous, current)
    assert merged["status"] == "completed"
    assert merged["evidence_sufficient"] is True
    assert merged["validations"][0]["dados_suficientes"] is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [(10, "10"), (1.25, "1,25"), ("3.500", "3,5"), (None, "indisponivel")],
)
def test_stock_quantity_format_matches_facade(value, expected: str) -> None:
    assert tool_results.format_stock_quantity(value) == whatsapp_bridge._format_stock_quantity(value) == expected
