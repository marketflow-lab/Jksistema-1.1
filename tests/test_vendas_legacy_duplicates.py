from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import importlib.util
from pathlib import Path

import pytest


# Load the pure helper without initializing the application package or collectors.
_PATH = Path(__file__).resolve().parents[1] / "backend/modules/vendas/legacy_duplicates.py"
_SPEC = importlib.util.spec_from_file_location("vendas_legacy_duplicates_under_test", _PATH)
_HELPER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPER)
is_short_legacy_sale = _HELPER.is_short_legacy_sale
without_legacy_sale_duplicates = _HELPER.without_legacy_sale_duplicates


def _legacy(**changes):
    row = {
        "id_unico": "Conta sintética_12210_200", "loja_conta": "Conta sintética",
        "store_id": "", "numero": "12210", "sku": "200", "data": "2026-04-12",
        "nota_fiscal_id": "invoice-12", "numero_nf": "012152",
        "quantidade": 1, "valor": 369.16, "comprador": "Comprador sintético",
        "origem_tipo": None, "origem_id": None, "item_id": None,
    }
    row.update(changes)
    return row


def _typed(**changes):
    row = _legacy(
        id_unico="bling_v2_fixture", store_id="store-a", origem_tipo="pedido",
        origem_id="order-12", item_id="1",
    )
    row.update(changes)
    return row


@pytest.mark.parametrize("kind", ["pedido", "nfe_saida"])
def test_confirmed_short_copy_is_suppressed_for_either_document_kind(kind):
    old, current = _legacy(), _typed(origem_tipo=kind)
    assert without_legacy_sale_duplicates([old, current]) == [current]
    assert without_legacy_sale_duplicates([current, old]) == [current]


def test_literal_identity_handles_underscores_accents_and_spaces_without_splitting():
    changes = {"loja_conta": " Conta_Á / X ", "numero": "ORD_12", "sku": "SKU_200-A"}
    old = _legacy(**changes, id_unico=" Conta_Á / X _ORD_12_SKU_200-A")
    current = _typed(**changes)
    assert is_short_legacy_sale(old)
    assert without_legacy_sale_duplicates([old, current]) == [current]
    assert not is_short_legacy_sale(dict(old, id_unico=old["id_unico"].strip()))


@pytest.mark.parametrize("identity", ["opaque", "Conta sintética_12210_200_1_369.16", "", None])
def test_other_legacy_identities_are_preserved(identity):
    old, current = _legacy(id_unico=identity), _typed()
    assert not is_short_legacy_sale(old)
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


@pytest.mark.parametrize("field", ["origem_tipo", "origem_id", "item_id"])
def test_partially_typed_identity_is_never_treated_as_short_legacy(field):
    old, current = _legacy(**{field: "partial"}), _typed()
    assert not is_short_legacy_sale(old)
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


def test_repeated_identical_typed_items_and_legacy_only_rows_are_preserved():
    old = _legacy()
    first = _typed()
    second = _typed(id_unico="bling_v2_item_2", item_id="2")
    output = without_legacy_sale_duplicates([old, first, second])
    assert output == [first, second]
    assert sum(row["quantidade"] for row in output) == 2
    assert sum(Decimal(str(row["valor"])) for row in output) == Decimal("738.32")
    assert without_legacy_sale_duplicates([old, deepcopy(old)]) == [old, old]


@pytest.mark.parametrize("changes", [
    {"origem_tipo": "other"}, {"origem_tipo": ""}, {"origem_id": None},
    {"item_id": ""}, {"store_id": ""},
])
def test_incomplete_typed_metadata_cannot_replace_legacy(changes):
    old, current = _legacy(), _typed(**changes)
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


@pytest.mark.parametrize("changes", [
    {"nota_fiscal_id": "other-invoice"}, {"nota_fiscal_id": ""},
    {"numero": "other-order"}, {"sku": "200-A"}, {"sku": "0200"},
    {"data": "2026-04-13"}, {"quantidade": 2}, {"valor": 369.17},
    {"comprador": "Outro comprador"},
])
def test_commercial_coincidence_or_conflicting_evidence_keeps_legacy(changes):
    old, current = _legacy(), _typed(**changes)
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


def test_visible_invoice_number_never_replaces_missing_internal_invoice_evidence():
    old, current = _legacy(nota_fiscal_id=""), _typed(nota_fiscal_id="")
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


@pytest.mark.parametrize("field", ["quantidade", "valor"])
@pytest.mark.parametrize("value", [None, "", "NaN", "sNaN", "Infinity", "-Infinity", float("inf")])
def test_missing_or_nonfinite_numeric_evidence_never_matches(field, value):
    old, current = _legacy(**{field: value}), _typed(**{field: value})
    result = without_legacy_sale_duplicates([old, current])
    assert len(result) == 2
    assert result[0] is old and result[1] is current


@pytest.mark.parametrize("value", ["", "2026-02-30", "20260412", "2026-W15-7"])
def test_invalid_or_noncanonical_dates_cannot_prove_a_match(value):
    old, current = _legacy(data=value), _typed(data=value)
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


def test_valid_day_and_decimal_representations_can_match():
    old = _legacy(quantidade="1.000", valor=Decimal("369.1600"))
    current = _typed(data="2026-04-12T11:22:33-03:00", quantidade=Decimal(1), valor="369.16")
    assert without_legacy_sale_duplicates([old, current]) == [current]


@pytest.mark.parametrize("old_buyer,new_buyer", [
    ("", "Comprador sintético"), ("Comprador sintético", ""),
    (" COMPRADOR   SINTÉTICO ", "comprador sintético"),
])
def test_optional_buyer_enrichment_and_letter_case_are_compatible(old_buyer, new_buyer):
    old, current = _legacy(comprador=old_buyer), _typed(comprador=new_buyer)
    assert without_legacy_sale_duplicates([old, current]) == [current]


def test_different_explicit_accounts_are_never_merged_despite_identical_names():
    old, current = _legacy(store_id="store-b"), _typed()
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


def test_explicit_account_id_allows_renamed_account_and_ignores_name_block():
    old = _legacy(store_id="store-a")
    current = _typed(loja_conta="Conta renomeada")
    assert without_legacy_sale_duplicates([old, current], blocked_store_names={old["loja_conta"]}) == [current]


def test_legacy_account_requires_literal_unambiguous_name():
    old = _legacy()
    current = _typed(loja_conta="conta sintética")
    assert without_legacy_sale_duplicates([old, current]) == [old, current]
    current["loja_conta"] = old["loja_conta"]
    unrelated = _typed(id_unico="other", store_id="store-b", sku="unrelated")
    assert without_legacy_sale_duplicates([old, current, unrelated]) == [old, current, unrelated]
    assert without_legacy_sale_duplicates([old, current], blocked_store_names={old["loja_conta"]}) == [old, current]


def test_typed_documents_with_coincident_values_are_not_merged_with_each_other():
    order = _typed()
    another_order = _typed(id_unico="bling_v2_order_2", origem_id="order-2")
    invoice = _typed(id_unico="bling_v2_invoice", origem_tipo="nfe_saida", origem_id="invoice-12")
    assert without_legacy_sale_duplicates([order, another_order, invoice]) == [order, another_order, invoice]


def test_external_ambiguity_marker_prevents_suppression():
    old, current = _legacy(__legacy_store_ambiguous=True), _typed()
    assert without_legacy_sale_duplicates([old, current]) == [old, current]


def test_suppression_is_idempotent_preserves_order_and_never_mutates_inputs():
    old = _legacy()
    first = _typed(id_unico="other-sale", numero="other-order")
    current = _typed()
    rows = [first, old, current]
    before = deepcopy(rows)
    result = without_legacy_sale_duplicates(rows)
    assert rows == before
    assert result == [first, current]
    assert result[0] is first and result[1] is current
    assert without_legacy_sale_duplicates(result) == result
