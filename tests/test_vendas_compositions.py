from __future__ import annotations

import copy
import csv
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.modules.vendas.compositions import (
    CompositionCatalog, composition_source_paths, load_composition_catalog,
)


def product(sku, product_id, components=None, *, store="store-a", **extra):
    result = {
        "sku": sku, "store_id": store, "loja_sync": "Loja A",
        "id_bling": str(product_id), "produto_bling": f"Produto {sku}",
        **extra,
    }
    if components is not None:
        result["componentes_bling_json"] = json.dumps(components)
        result["formato_bling"] = "E"
    return result


def component(product_id, quantity):
    return {"produto": {"id": product_id}, "quantidade": quantity}


def row(sku, quantity, *, store="store-a", value=10, **extra):
    return {
        "id_unico": f"documento-{sku}", "sku": sku, "produto": f"Venda {sku}",
        "store_id": store, "loja_conta": "Loja A",
        "quantidade": quantity, "valor": value, "numero_nf": "NF-1", **extra,
    }


def fixture_catalog():
    return CompositionCatalog.from_products([
        product("479", 1),
        product("479-KIT2", 2, [component(1, 2)]),
        product("479-KIT3", 3, [component(1, 3)]),
        product("479-KIT5", 4, [component(1, 5)]),
    ])


def test_reference_case_59_units_preserves_money_documents_and_input():
    catalog = fixture_catalog()
    raw = [
        row("479", 20, value=200),
        row("479-KIT2", 9, value=180),
        row("479-KIT3", 2, value=60),
        row("479-KIT5", 3, value=150),
    ]
    frozen = copy.deepcopy(raw)
    projected = catalog.project_rows(raw)
    base = [item for item in projected if item["sku"] == "479"]
    assert sum(item["quantidade"] for item in base) == 59
    assert sum(item["quantidade"] for item in projected) == 59
    assert sum(item["valor"] for item in projected) == 590
    assert sum(item["pedidos_contribuicao"] for item in projected) == 4
    assert [item["quantidade_original"] for item in projected if item.get("__tipo_composicao") == "kit"] == [9, 2, 3]
    assert raw == frozen
    assert catalog.project_rows(projected) == projected
    assert catalog.metadata["incomplete"] is False
    assert len({item["id_unico"] for item in projected}) == len(projected)


def test_inverse_candidates_and_target_keep_commercial_kit_view():
    catalog = fixture_catalog()
    assert catalog.candidates("479", "store-a") == {"479", "479-KIT2", "479-KIT3", "479-KIT5"}
    assert catalog.candidates("479-KIT2", "store-a") == {"479-KIT2"}
    raw = [row("479", 20), row("479-KIT2", 9)]
    base = catalog.project_rows(raw, target_sku="479")
    assert [item["quantidade"] for item in base] == [20, 18]
    assert sum(item["valor"] for item in base) == 10
    kit = catalog.project_rows(raw, target_sku="479-KIT2")
    assert len(kit) == 1
    assert kit[0]["quantidade"] == 9
    assert kit[0]["valor"] == 10
    assert not kit[0].get("__composicao")
    assert catalog.project_rows(base, target_sku="479") == base


def test_nested_repeated_components_and_decimal_factors_are_exact():
    catalog = CompositionCatalog.from_products([
        product("BASE", 10), product("OTHER", 11),
        product("INNER", 20, [component(10, "0.1"), component(10, "0.2")]),
        product("OUTER", 30, [component(20, "2"), component(11, "0.5")]),
    ])
    resolved = catalog.resolve("OUTER", "store-a")
    assert [(item.sku, item.fator) for item in resolved] == [
        ("BASE", Decimal("0.6")), ("OTHER", Decimal("0.5")),
    ]
    projected = catalog.project_rows([row("OUTER", 3)])
    assert [item["quantidade"] for item in projected] == [0, 1.8, 1.5]
    assert catalog.candidates("BASE", "store-a") == {"BASE", "INNER", "OUTER"}
    assert catalog.candidates("INNER", "store-a") == {"INNER"}


def test_returns_keep_original_value_once_and_count_only_original_document():
    catalog = fixture_catalog()
    source = row("479-KIT5", 2, valor_total=125, value=0)
    projected = catalog.project_rows([source])
    assert sum(item["quantidade"] for item in projected) == 10
    assert sum(item["valor_total"] for item in projected) == 125
    assert sum(item["devolucoes_contribuicao"] for item in projected) == 1
    base = catalog.project_rows([source], target_sku="479")
    assert len(base) == 1
    assert base[0]["quantidade"] == 10
    assert base[0]["valor_total"] == base[0]["devolucoes_contribuicao"] == 0
    assert base[0]["sku_original"] == "479-KIT5"
    assert base[0]["quantidade_original"] == 2
    assert base[0]["fator_composicao"] == 5


def test_return_projection_identity_uses_original_note_and_unit_value_stays_on_parent():
    catalog = fixture_catalog()
    first = row("479-KIT2", 2, numero_nota="RETURN-1", valor_total=25, valor_unitario=12.5)
    second = row("479-KIT2", 2, numero_nota="RETURN-2", valor_total=40, valor_unitario=20)
    first.pop("id_unico")
    second.pop("id_unico")
    projected = catalog.project_rows([first, second])
    synthetic = [item for item in projected if item.get("__composicao")]
    assert len({item["id_unico"] for item in synthetic}) == 2
    assert all(item["valor_unitario"] == 0 for item in synthetic)
    assert sum(item["valor_total"] for item in projected) == 65
    assert sum(item["quantidade"] for item in projected) == 8


@pytest.mark.parametrize("quantity", [None, 0, -1, True, "NaN", "Infinity", "-Infinity", "invalid"])
def test_invalid_component_quantity_preserves_whole_original(quantity):
    catalog = CompositionCatalog.from_products([
        product("BASE", 1), product("KIT", 2, [component(1, 2), component(1, quantity)]),
    ])
    projected = catalog.project_rows([row("KIT", 3, value=99)])
    assert len(projected) == 1
    assert projected[0]["sku"] == "KIT"
    assert projected[0]["quantidade"] == 3
    assert projected[0]["valor"] == 99
    assert "KIT" not in catalog.candidates("BASE", "store-a")
    assert catalog.resolve("KIT", "store-a")[0].fator == Decimal(1)
    assert catalog.metadata["incomplete"] is True
    assert any(item["code"] == "invalid_quantity" for item in catalog.warnings)


@pytest.mark.parametrize(
    ("records", "code"),
    [
        ([product("KIT", 2, [component(999, 2)])], "missing_component"),
        ([product("KIT", 2, [component(2, 2)])], "composition_cycle"),
        ([product("KIT", 2, [component(3, 1)]), product("INNER", 3, [component(2, 1)])], "composition_cycle"),
        ([product("KIT", 2, [])], "missing_components"),
        ([product("KIT", 2, componentes_bling_json="{invalid", formato_bling="E")], "invalid_components_json"),
        ([product("KIT", 2, [component(1, 2)]), product("A", 1), product("B", 1)], "ambiguous_component_id"),
        ([product("KIT", 2, [component(1, 2)]), product("BASE", 1), product("BASE", 1)], "ambiguous_product"),
        ([product("KIT", 2, [component(1, 2)]), product("BASE", 1, deleted_at_utc="2026-01-01")], "missing_component"),
    ],
)
def test_invalid_or_incomplete_graph_keeps_raw_quantity(records, code):
    catalog = CompositionCatalog.from_products(records)
    projected = catalog.project_rows([row("KIT", 3)])
    assert len(projected) == 1 and projected[0]["quantidade"] == 3
    assert not projected[0].get("__composicao")
    assert any(warning["code"] == code for warning in catalog.warnings)


def test_primitive_sku_and_empty_components_do_not_infer_kit_from_name():
    catalog = CompositionCatalog.from_products([
        product("KIT100", 1),
        product("EMPTY", 2, componentes_bling_json="[]", formato_bling="S"),
    ])
    projected = catalog.project_rows([row("KIT100", 3), row("EMPTY", 2)])
    assert len(projected) == 2
    assert [item["quantidade"] for item in projected] == [3, 2]
    assert catalog.metadata["incomplete"] is False


def test_structure_fallback_uses_product_id_within_same_store_only():
    catalog = CompositionCatalog.from_products([
        product("BASE-A", 1),
        product("KIT", 2, estrutura_bling=json.dumps({"componentes": [component(1, 2)]})),
        product("BASE-B", 1, store="store-b"),
        product("KIT", 2, [component(1, 5)], store="store-b"),
    ])
    assert catalog.resolve("KIT", "store-a")[0].sku == "BASE-A"
    assert catalog.resolve("KIT", "store-b")[0].sku == "BASE-B"
    assert catalog.candidates("BASE-A", "store-a") == {"BASE-A", "KIT"}
    assert catalog.candidates("BASE-A", "store-b") == {"BASE-A"}
    assert catalog.project_rows([row("KIT", 2, store="store-b")])[1]["quantidade"] == 10


def test_stable_store_wins_name_historical_names_work_and_ambiguous_names_fail_closed():
    stores = [
        {"store_id": "store-a", "nome": "Nome igual", "nomes_anteriores": ["Nome anterior"]},
        {"store_id": "store-b", "nome": "Nome igual"},
    ]
    products = [
        product("BASE-A", 1), product("KIT", 2, [component(1, 2)]),
        product("BASE-B", 1, store="store-b"), product("KIT", 2, [component(1, 5)], store="store-b"),
    ]
    catalog = CompositionCatalog.from_products(products, stores=stores)
    stable = catalog.project_rows([row("KIT", 3, loja_conta="Nome igual")], store="store-b")
    assert stable[1]["sku"] == "BASE-A" and stable[1]["quantidade"] == 6
    renamed = catalog.project_rows([row("KIT", 3, store="", loja_conta="Nome anterior")])
    assert renamed[1]["sku"] == "BASE-A"
    ambiguous = catalog.project_rows([row("KIT", 3, store="", loja_conta="Nome igual")], store="store-a")
    assert len(ambiguous) == 1 and ambiguous[0]["quantidade"] == 3
    invalid_id = catalog.project_rows([row("KIT", 3, store="unknown", loja_conta="Nome anterior")])
    assert len(invalid_id) == 1 and invalid_id[0]["quantidade"] == 3
    assert any(warning["code"] == "unresolved_sale_store" for warning in catalog.warnings)


def test_duplicate_config_id_cannot_borrow_another_account():
    catalog = CompositionCatalog.from_products(
        [
            product("BASE", 1), product("KIT", 2, [component(1, 2)]),
            product("BASE-B", 1, store="store-b"),
            product("KIT", 2, [component(1, 5)], store="store-b"),
        ],
        stores=[
            {"store_id": "store-a", "nome": "A"}, {"store_id": "store-a", "nome": "B"},
            {"store_id": "store-b", "nome": "store-a"},
        ],
    )
    projected = catalog.project_rows([row("KIT", 3)])
    assert len(projected) == 1 and projected[0]["quantidade"] == 3
    assert catalog.resolve("KIT", "store-a")[0].sku == "KIT"
    assert catalog.resolve("KIT", "store-b")[0].sku == "BASE-B"
    assert {"code": "ambiguous_store", "store_id": "store-a", "sku": ""} in catalog.warnings


def test_another_store_never_completes_a_missing_component():
    catalog = CompositionCatalog.from_products([
        product("KIT", 2, [component(1, 2)]), product("BASE", 1, store="store-b"),
    ])
    assert catalog.resolve("KIT", "store-a")[0].sku == "KIT"
    assert any(warning["code"] == "missing_component" for warning in catalog.warnings)


def test_generic_legacy_composition_or_component_identity_never_borrows_an_account():
    generic_bom = CompositionCatalog.from_products([
        product("BASE", 1), product("KIT", 2, [component(1, 2)], scope_source="legacy_shadow"),
    ])
    assert len(generic_bom.project_rows([row("KIT", 3)])) == 1
    assert any(item["code"] == "legacy_composition_unverified" for item in generic_bom.warnings)
    generic_id = CompositionCatalog.from_products([
        product("BASE", 1, scope_source="legacy_shadow"), product("KIT", 2, [component(1, 2)]),
    ])
    assert len(generic_id.project_rows([row("KIT", 3)])) == 1
    assert any(item["code"] == "legacy_component_identity_unverified" for item in generic_id.warnings)


def test_explicit_sku_reference_must_agree_with_bling_id():
    valid = {"produto": {"id": 1, "codigo": "BASE"}, "quantidade": 2}
    conflict = {"produto": {"id": 1, "codigo": "OTHER"}, "quantidade": 2}
    products = [product("BASE", 1), product("GOOD", 2, [valid]), product("BAD", 3, [conflict])]
    catalog = CompositionCatalog.from_products(products)
    assert catalog.resolve("GOOD", "store-a")[0].sku == "BASE"
    assert catalog.resolve("BAD", "store-a")[0].sku == "BAD"
    assert any(item["code"] == "conflicting_component_reference" for item in catalog.warnings)


def _local_catalog(monkeypatch, tmp_path):
    from backend.services import cadastro_fotos, cadastro_lojas_produtos, integracoes

    stores = [{"store_id": "store-a", "nome": "Loja A", "nomes_anteriores": ["Anterior"]}]
    tenant_path = lambda client_id: str(tmp_path / client_id)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "ler_lojas", lambda _client_id: copy.deepcopy(stores))
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _client_id: stores)
    for client_id, factor in [("tenant-a", 2), ("tenant-b", 5)]:
        tenant = tmp_path / client_id
        tenant.mkdir()
        records = [product("BASE", 1), product("KIT", 2, [component(1, factor)])]
        fields = sorted({field for record in records for field in record})
        with (tenant / "cadastro_produtos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    return stores


def test_loader_reads_real_canonical_csv_per_tenant_and_never_mutates_it(monkeypatch, tmp_path):
    _local_catalog(monkeypatch, tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    first = load_composition_catalog("tenant-a")
    second = load_composition_catalog("tenant-b")
    assert first.resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert second.resolve("KIT", "store-a")[0].fator == Decimal(5)
    assert first.resolve("KIT", "Anterior")[0].fator == Decimal(2)
    assert first.metadata["incomplete"] is False
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("exact_store", [False, True])
def test_loader_requires_exact_store_proof_for_legacy_bom(monkeypatch, tmp_path, exact_store):
    _local_catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "tenant-a"
    records = [product("BASE", 1), product("KIT", 2, [component(1, 2)])]
    if not exact_store:
        for record in records:
            record.pop("store_id")
            record.pop("loja_sync")
    fields = sorted({field for record in records for field in record})
    with (tenant / "cadastro_produtos.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    # A legacy account link proves which SKUs exist, but does not identify the
    # account that owned IDs inherited from a generic Cadastro row.
    with (tenant / "cadastro_custos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["store_id", "sku", "loja", "custo"])
        writer.writeheader()
        writer.writerows([
            {"store_id": "store-a", "sku": "BASE", "loja": "Loja A", "custo": 1},
            {"store_id": "store-a", "sku": "KIT", "loja": "Loja A", "custo": 2},
        ])
    (tenant / "cadastro_produtos_lojas.csv").write_text("store_id,sku\n", encoding="utf-8")
    catalog = load_composition_catalog("tenant-a")
    projected = catalog.project_rows([row("KIT", 3)])
    if exact_store:
        assert sum(item["quantidade"] for item in projected) == 6
        assert catalog.metadata["incomplete"] is False
    else:
        assert len(projected) == 1 and projected[0]["quantidade"] == 3
        assert any(item["code"] == "legacy_composition_unverified" for item in catalog.warnings)


def test_missing_runtime_is_visible_and_preserves_sales(monkeypatch):
    from backend.services import cadastro_compatibilidade

    def unavailable(*_args):
        raise RuntimeError("private source detail must not leak")

    monkeypatch.setattr(cadastro_compatibilidade, "_capturar_cadastro_para_leitura", unavailable)
    catalog = load_composition_catalog("tenant-a")
    projected = catalog.project_rows([row("KIT", 3)])
    assert projected[0]["quantidade"] == 3
    assert catalog.metadata["incomplete"] is True
    assert catalog.warnings == [{"code": "catalog_unavailable", "store_id": "", "sku": ""}]


@pytest.mark.parametrize("provenance", ["generic", "canonical_bom_only", "exact_store_legacy"])
def test_enriched_store_file_requires_field_proof_not_just_row_scope(monkeypatch, tmp_path, provenance):
    from backend.services import cadastro_lojas_produtos

    _local_catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "tenant-a"
    canonical = [
        {"store_id": "store-a", "sku": "BASE", "row_version": 1},
        {"store_id": "store-a", "sku": "KIT", "row_version": 1},
    ]
    inherited = [product("BASE", 1), product("KIT", 2, [component(1, 2)])]
    if provenance != "exact_store_legacy":
        for record in inherited:
            record.pop("store_id")
    if provenance == "canonical_bom_only":
        canonical[1].update(product("KIT", 2, [component(1, 2)]))
    for filename, records in [
        ("cadastro_produtos_lojas.csv", canonical),
        ("produtos_compilado.csv", inherited),
    ]:
        with (tenant / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted({key for record in records for key in record}))
            writer.writeheader()
            writer.writerows(records)
    enriched = cadastro_lojas_produtos._listar_produtos_loja_sync("tenant-a", "store-a")
    assert all(record["scope_source"] == "store_file" for record in enriched)
    assert any(record.get("componentes_bling_json") for record in enriched)
    catalog = load_composition_catalog("tenant-a")
    projected = catalog.project_rows([row("KIT", 3)])
    if provenance == "exact_store_legacy":
        assert sum(item["quantidade"] for item in projected) == 6
        assert catalog.metadata["incomplete"] is False
    else:
        assert len(projected) == 1 and projected[0]["quantidade"] == 3
        assert catalog.metadata["incomplete"] is True


def test_undetermined_legacy_metadata_emits_one_coverage_warning_per_store():
    catalog = CompositionCatalog.from_products([
        product("BASE", "", scope_source="legacy_shadow"),
        product("ANOTHER", "", scope_source="legacy_shadow"),
    ])
    assert catalog.warnings == [{"code": "legacy_catalog_unverified", "store_id": "store-a", "sku": ""}]


def test_source_paths_include_all_local_dependencies_without_reading_stores(monkeypatch, tmp_path):
    from backend.services import cadastro_lojas_produtos

    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(cadastro_lojas_produtos, "_lojas_atuais", lambda *_: pytest.fail("Must not query stores"))
    paths = composition_source_paths("tenant-a")
    assert {Path(path).name for path in paths} == {
        "cadastro_produtos_lojas.csv", "cadastro_produtos.csv", "cadastro_custos_lojas.csv",
        "produtos_compilado.csv", "lojas_config.json", "lojas_config.json.bak",
    }
    assert all(Path(path).parent == tmp_path / "tenant-a" for path in paths)
    assert not (tmp_path / "tenant-a").exists()
