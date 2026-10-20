"""Composition refresh through the real preview, atomic writer and CSV reader."""

import csv
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.modules.vendas.compositions import load_composition_catalog
from backend.services import cadastro_catalogo_bling as provider
from backend.services import cadastro_custos, cadastro_fotos, cadastro_lojas_produtos as store
from backend.services import cadastro_importacao_catalogos as imports
from backend.services import integracoes


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    stores = [{"store_id": "store-a", "nome": "Loja A"}, {"store_id": "store-b", "nome": "Loja B"}]

    def tenant_path(client_id):
        path = tmp_path / client_id
        path.mkdir(exist_ok=True)
        return str(path)

    for module in (store, cadastro_custos, cadastro_fotos):
        monkeypatch.setattr(module, "get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(integracoes, "ler_lojas", lambda _: stores)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _: stores)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: stores)
    monkeypatch.setattr(store, "_notificar_ficha_catalogo", lambda *_: None)
    return tmp_path


def _component(product_id, quantity):
    return {"produto": {"id": product_id}, "quantidade": quantity}


def _fields(product_id, sku, *, components=None, format="E", **extra):
    raw = {"id": product_id, "codigo": sku, "formato": format, **extra}
    if components is not None:
        raw["estrutura"] = {"tipoEstoque": "F", "componentes": components}
    return provider._mapear_campos_produto(raw, consultado_em_utc="2026-10-20T00:00:00Z")


def _preview(fields_by_sku, *, account="store-a", tenant="tenant-a"):
    current = store._listar_produtos_loja_sync(tenant, account, include_legacy_snapshot_hash=True)
    return imports._construir_preview(
        "bling", {
            "coverage_complete": True, "sku_coverage_complete": True, "store_id": account,
            "items": [{"sku": sku, "fields": fields} for sku, fields in fields_by_sku.items()],
        }, current, client_id=tenant,
    )


def _apply(fields_by_sku, *, account="store-a", tenant="tenant-a"):
    preview = _preview(fields_by_sku, account=account, tenant=tenant)
    payloads = []
    for row in preview["apply_rows"]:
        payload = {
            "sku": row["sku"], "row_version": row["row_version"],
            "__expected_scope": row["expected_scope"], **row["fields"],
        }
        if row.get("legacy_snapshot_hash"):
            payload["__legacy_snapshot_hash"] = row["legacy_snapshot_hash"]
        payloads.append(payload)
    if payloads:
        imports._salvar_payloads_importacao_catalogo(
            tenant, account, "bling", payloads,
            campos_derivados_permitidos=imports._PRIVILEGED_DERIVED_FIELDS["bling"],
            precommit_validator=lambda _: None,
        )
    return preview


def _seed(*, account="store-a", tenant="tenant-a"):
    for sku in ("BASE", "KIT"):
        store.salvar_produto_loja(tenant, account, {
            "sku": sku, "nome": f"Manual {sku}", "custo": "17.25", "preco": "30",
            "imposto": "5", "descricao": "Descricao manual", "marca": "Marca manual",
        })


def _initial():
    return {
        "BASE": _fields(1, "BASE", format="S"),
        "KIT": _fields(2, "KIT", components=[_component(1, 2)]),
    }


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_existing_skus_receive_composition_and_ids_without_touching_manual_fields_or_other_stores(catalog):
    _seed()
    _seed(account="store-b")
    _seed(tenant="tenant-b")
    before_other = store._listar_produtos_loja_sync("tenant-a", "store-b")
    before_tenant = store._listar_produtos_loja_sync("tenant-b", "store-a")
    cost_path = catalog / "tenant-a" / "cadastro_custos_lojas.csv"
    cost_bytes = cost_path.read_bytes()
    preview = _apply(_initial())
    assert preview["summary"]["aplicaveis"] == 2
    persisted = _read_csv(catalog / "tenant-a" / "cadastro_produtos_lojas.csv")
    kit = next(row for row in persisted if row["store_id"] == "store-a" and row["sku"] == "KIT")
    assert kit["id_bling"] == "2" and kit["formato_bling"] == "E"
    assert json.loads(kit["componentes_bling_json"]) == [_component(1, 2)]
    assert (kit["nome"], kit["descricao"], kit["marca"]) == (
        "Manual KIT", "Descricao manual", "Marca manual",
    )
    assert cost_path.read_bytes() == cost_bytes
    after_other = store._listar_produtos_loja_sync("tenant-a", "store-b")
    assert [{key: value for key, value in item.items() if value != ""} for item in after_other] == [
        {key: value for key, value in item.items() if value != ""} for item in before_other
    ]
    assert all(not item.get(field) for item in after_other for field in imports._BLING_UNSCOPED_COMPOSITION_FIELDS)
    assert store._listar_produtos_loja_sync("tenant-b", "store-a") == before_tenant
    resolved = load_composition_catalog("tenant-a")
    assert resolved.resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert resolved.resolve("KIT", "store-b")[0].sku == "KIT"
    assert load_composition_catalog("tenant-b").resolve("KIT", "store-a")[0].sku == "KIT"


def test_partial_response_without_components_preserves_complete_bom(catalog):
    _seed()
    _apply(_initial())
    path = catalog / "tenant-a" / "cadastro_produtos_lojas.csv"
    before = path.read_bytes()
    partial = _fields(2, "KIT", estrutura={"tipoEstoque": "V"})
    preview = _apply({"KIT": partial})
    assert not preview["apply_rows"]
    assert path.read_bytes() == before
    assert load_composition_catalog("tenant-a").resolve("KIT", "store-a")[0].fator == Decimal(2)


def test_explicit_composite_to_simple_clears_old_bom_and_remains_a_leaf_of_other_kits(catalog):
    _seed()
    _apply(_initial())
    _apply({"PARENT": _fields(3, "PARENT", components=[_component(2, 3)])})
    _apply({"KIT": _fields(2, "KIT", format="S")})
    kit = next(row for row in _read_csv(catalog / "tenant-a" / "cadastro_produtos_lojas.csv") if row["sku"] == "KIT")
    assert kit["formato_bling"] == "S"
    assert json.loads(kit["componentes_bling_json"]) == []
    assert json.loads(kit["estrutura_bling"]) == {}
    resolved = load_composition_catalog("tenant-a")
    assert resolved.resolve("KIT", "store-a")[0].sku == "KIT"
    assert resolved.candidates("KIT", "store-a") == {"KIT", "PARENT"}
    projected = resolved.project_rows([{
        "sku": "PARENT", "quantidade": 2, "store_id": "store-a", "valor": 100,
    }], target_sku="KIT")
    assert len(projected) == 1 and projected[0]["quantidade"] == 6


def test_conflicting_bling_id_preserves_existing_identity_and_composition(catalog):
    _seed()
    _apply(_initial())
    preview = _apply({"KIT": _fields(999, "KIT", components=[_component(1, 8)])})
    assert preview["apply_rows"] == []
    assert "id_bling:valores_divergentes_na_fonte" in preview["items"][0]["conflicts"]
    kit = next(row for row in _read_csv(catalog / "tenant-a" / "cadastro_produtos_lojas.csv") if row["sku"] == "KIT")
    assert kit["id_bling"] == "2"
    assert load_composition_catalog("tenant-a").resolve("KIT", "store-a")[0].fator == Decimal(2)


def test_conflicting_or_invalid_bom_keeps_previous_valid_composition(catalog):
    _seed()
    _apply(_initial())
    invalid = _fields(2, "KIT", components=[_component(1, 0)])
    preview = _apply({"KIT": invalid})
    assert preview["apply_rows"] == []
    assert "composicao_bling:valores_divergentes_na_fonte" in preview["items"][0]["conflicts"]
    assert load_composition_catalog("tenant-a").resolve("KIT", "store-a")[0].fator == Decimal(2)
    current = store._listar_produtos_loja_sync("tenant-a", "store-a")
    preview = imports._construir_preview("bling", {
        "store_id": "store-a", "coverage_complete": True,
        "items": [
            {"sku": "KIT", "fields": _fields(2, "KIT", components=[_component(1, 2)])},
            {"sku": "KIT", "fields": _fields(2, "KIT", components=[_component(1, 3)])},
        ],
    }, current)
    assert not preview["apply_rows"]
    assert "composicao_bling:valores_divergentes_na_fonte" in preview["items"][0]["conflicts"]


def _legacy(catalog):
    tenant = catalog / "tenant-a"
    tenant.mkdir(exist_ok=True)
    rows = [
        {"sku": "BASE", **_fields(1, "BASE", format="S")},
        {"sku": "KIT", **_fields(2, "KIT", components=[_component(1, 9)])},
    ]
    fields = sorted({field for row in rows for field in row})
    with (tenant / "cadastro_produtos.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (tenant / "cadastro_custos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["store_id", "loja", "sku", "custo"])
        writer.writeheader()
        writer.writerows([
            {"store_id": account, "loja": name, "sku": sku, "custo": 17}
            for account, name in [("store-a", "Loja A"), ("store-b", "Loja B")]
            for sku in ("BASE", "KIT")
        ])


def test_generic_legacy_bom_is_not_promoted_when_source_does_not_supply_it(catalog):
    _legacy(catalog)
    preview = _apply({"KIT": {"produto_bling": "Titulo recebido"}})
    assert preview["summary"]["aplicaveis"] == 1
    kit = _read_csv(catalog / "tenant-a" / "cadastro_produtos_lojas.csv")[0]
    assert kit["store_id"] == "store-a"
    assert not any(kit.get(field) for field in imports._BLING_UNSCOPED_COMPOSITION_FIELDS)
    assert load_composition_catalog("tenant-a").resolve("KIT", "store-a")[0].sku == "KIT"


def test_verified_refresh_materializes_only_its_account_and_replaces_generic_bom(catalog):
    _legacy(catalog)
    legacy_path = catalog / "tenant-a" / "cadastro_produtos.csv"
    before = legacy_path.read_bytes()
    _apply(_initial())
    resolved = load_composition_catalog("tenant-a")
    assert resolved.resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert resolved.resolve("KIT", "store-b")[0].sku == "KIT"
    assert legacy_path.read_bytes() == before
    assert any(item["code"] == "legacy_composition_unverified" for item in resolved.warnings)


def test_serializer_keeps_explicit_empty_components_but_not_absent():
    explicit = provider._mapear_campos_produto({"componentes": []}, consultado_em_utc="now")
    absent = provider._mapear_campos_produto({}, consultado_em_utc="now")
    assert explicit["componentes_bling_json"] == "[]"
    assert "componentes_bling_json" not in absent


def test_detail_and_external_identity_must_agree_before_composition_refresh(catalog):
    _seed()
    _apply(_initial())
    current = store._listar_produtos_loja_sync("tenant-a", "store-a")
    preview = imports._construir_preview("bling", {
        "store_id": "store-a", "coverage_complete": True,
        "items": [{
            "sku": "KIT", "external_ids": {"id_bling": "999"},
            "fields": _fields(2, "KIT", components=[_component(1, 8)]),
        }],
    }, current)
    assert preview["apply_rows"] == []
    assert "id_bling:valores_divergentes_na_fonte" in preview["items"][0]["conflicts"]
