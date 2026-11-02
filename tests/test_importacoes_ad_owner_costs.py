"""Import margins read financial values proven to belong to the ad owner's store."""

import asyncio
import json

import pytest

from backend.services import cadastro_compatibilidade as compatibility
from backend.services import cadastro_lojas_produtos as products
from backend.services import medias_compras_listas as imports
from backend.services.medias_compras_leitura import leitura_medias
from backend.services.retired_store_names import TOMBSTONES_FILE
from test_cadastro_produtos_lojas import _configure, _write_csv


SKU = "PART-1"


def _save(client="cliente-a", store="store-a", **fields):
    return products.salvar_produto_loja(client, store, {"sku": SKU, "nome": "Part", **fields})


def _financial(client="cliente-a", store="store-a", name="List store"):
    dependencies = imports._dependencias_margens_concorrentes()
    return dependencies["carregar_custos_impostos"](client, name, store, SKU)


@pytest.mark.parametrize("tax", ["10", "10%", "0,10"])
def test_canonical_store_edit_supplies_owner_cost_and_preserves_numeric_formats(monkeypatch, tmp_path, tax):
    _configure(monkeypatch, tmp_path)
    _save(custo="R$ 60,50", imposto=tax)
    _save(store="store-b", custo="999", imposto="90")

    costs, taxes = _financial(name="Loja B")

    assert costs == {SKU: 60.5}
    assert taxes == {SKU: .1}
    records, _ = products._ler_registros_persistidos("cliente-a")
    assert all("custo" not in row and "imposto" not in row for row in records)


def test_homonymous_stores_keep_distinct_financial_values_by_exact_id(monkeypatch, tmp_path):
    stores = {"cliente-a": [{"store_id": "store-a", "nome": "Same name"},
                             {"store_id": "store-b", "nome": "Same name"}]}
    _configure(monkeypatch, tmp_path, stores)
    _save(custo="60", imposto="10")
    _save(store="store-b", custo="90", imposto="20")

    with leitura_medias():
        assert _financial(name="Same name") == ({SKU: 60}, {SKU: .1})
        assert _financial(store="store-b", name="Same name") == ({SKU: 90}, {SKU: .2})


def test_same_store_and_sku_in_another_tenant_never_supply_owner_finances(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _save(custo="60", imposto="10")
    _save(client="cliente-b", custo="900", imposto="30")

    with leitura_medias():
        assert _financial() == ({SKU: 60}, {SKU: .1})
        assert _financial(client="cliente-b") == ({SKU: 900}, {SKU: .3})


def test_generic_financial_fields_in_a_legacy_shadow_do_not_become_owner_costs(monkeypatch, tmp_path):
    root, _ = _configure(monkeypatch, tmp_path)
    tenant = root / "cliente-a"
    _write_csv(tenant / "cadastro_produtos.csv", [{"sku": SKU, "custo": "999", "imposto": "99"}])
    _write_csv(tenant / "produtos_compilado.csv", [{"store_id": "store-a", "sku": SKU, "nome": "Owner part"}])
    _write_csv(tenant / "cadastro_custos_lojas.csv", [{"store_id": "store-b", "sku": SKU, "custo": "900", "imposto": "90"}])
    view = compatibility.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=[SKU])
    assert view["produtos"][0]["scope_source"] == "legacy_shadow"
    assert view["produtos"][0]["custo"] == "999", "A display shadow can retain a generic field."

    assert _financial() == ({}, {}), "Display inheritance is not proof that financial fields belong to the owner."


@pytest.mark.parametrize("missing", ["custo", "imposto"])
def test_blank_owner_financial_field_does_not_reuse_generic_or_legacy_name_value(monkeypatch, tmp_path, missing):
    root, _ = _configure(monkeypatch, tmp_path)
    _save()
    tenant = root / "cliente-a"
    _write_csv(tenant / "cadastro_produtos.csv", [{"sku": SKU, "custo": "999", "imposto": "99"}])
    exact = {"store_id": "store-a", "loja_sync": "Old display name", "sku": SKU, "custo": "60", "imposto": "10"}
    exact[missing] = ""
    _write_csv(tenant / "cadastro_custos_lojas.csv", [
        exact, {"loja_sync": "Loja A", "sku": SKU, "custo": "900", "imposto": "90"},
    ])

    assert _financial() == ({} if missing == "custo" else {SKU: 60}, {} if missing == "imposto" else {SKU: .1})


@pytest.mark.parametrize("exact_first", [False, True])
def test_exact_owner_cost_wins_over_name_rows_in_both_file_orders(monkeypatch, tmp_path, exact_first):
    root, _ = _configure(monkeypatch, tmp_path)
    _save()
    exact = {"store_id": "store-a", "loja_sync": "Outdated name", "sku": SKU, "custo": "60", "imposto": "10"}
    legacy = {"loja_sync": "Loja A", "sku": SKU, "custo": "900", "imposto": "90"}
    other = {"store_id": "store-b", "loja_sync": "Loja A", "sku": SKU, "custo": "800", "imposto": "80"}
    rows = [exact, legacy, other] if exact_first else [other, legacy, exact]
    _write_csv(root / "cliente-a" / "cadastro_custos_lojas.csv", rows)

    assert _financial() == ({SKU: 60}, {SKU: .1})


@pytest.mark.parametrize("label", ["Loja A", "Previous name"])
def test_authorized_unique_legacy_store_labels_remain_compatible(monkeypatch, tmp_path, label):
    stores = {"cliente-a": [{"store_id": "store-a", "nome": "Loja A", "nomes_anteriores": ["Previous name"]},
                             {"store_id": "store-b", "nome": "Loja B"}]}
    root, _ = _configure(monkeypatch, tmp_path, stores)
    _write_csv(root / "cliente-a" / "cadastro_custos_lojas.csv", [{"loja_sync": label, "sku": SKU, "custo": "60", "imposto": "10"}])

    assert _financial() == ({SKU: 60}, {SKU: .1})


@pytest.mark.parametrize("historical", [False, True])
def test_ambiguous_current_or_historical_labels_never_supply_owner_costs(monkeypatch, tmp_path, historical):
    first = {"store_id": "store-a", "nome": "Current name" if historical else "Same name"}
    if historical:
        first["nomes_anteriores"] = ["Same name"]
    stores = {"cliente-a": [first, {"store_id": "store-b", "nome": " same NAME "}]}
    root, _ = _configure(monkeypatch, tmp_path, stores)
    _save()
    _write_csv(root / "cliente-a" / "cadastro_custos_lojas.csv", [{"loja_sync": "Same name", "sku": SKU, "custo": "900", "imposto": "90"}])

    assert _financial() == ({}, {})


@pytest.mark.parametrize("exact", [False, True])
def test_retired_label_reservation_blocks_legacy_rows_but_preserves_exact_owner_id(monkeypatch, tmp_path, exact):
    root, _ = _configure(monkeypatch, tmp_path)
    _save()
    tenant = root / "cliente-a"
    event = {"key": "store:retired:", "type": "store", "store_id": "retired", "service": "", "version": 2,
             "deleted_at": "2026-10-21T10:00:00Z", "store_names": ["Loja A"]}
    (tenant / TOMBSTONES_FILE).write_text(json.dumps([event]), encoding="utf-8")
    row = {"loja_sync": "Loja A", "sku": SKU, "custo": "60", "imposto": "10"}
    if exact:
        row["store_id"] = "store-a"
    _write_csv(tenant / "cadastro_custos_lojas.csv", [row])

    assert _financial() == (({SKU: 60}, {SKU: .1}) if exact else ({}, {}))


def test_product_tombstone_blocks_financial_values_that_remain_in_owner_cost_file(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    saved = _save(custo="60", imposto="10")
    asyncio.run(products.excluir_produto_loja("store-a", SKU, saved["row_version"], "cliente-a"))

    assert _financial() == ({}, {})


def test_deleted_store_identity_is_not_remapped_to_current_same_name(monkeypatch, tmp_path):
    root, _ = _configure(monkeypatch, tmp_path)
    _save()
    _write_csv(root / "cliente-a" / "cadastro_custos_lojas.csv", [
        {"store_id": "deleted-store", "loja_sync": "Loja A", "sku": SKU, "custo": "900", "imposto": "90"},
    ])

    assert _financial() == ({}, {})
