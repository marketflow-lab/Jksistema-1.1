import asyncio
import csv
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services import cadastro_fotos, estoque_common, estoque_fotos


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(field for row in rows for field in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def scope(monkeypatch, tmp_path):
    root = tmp_path / "info"
    tenants = {}
    stores = [
        {"store_id": "store-a", "nome": "Loja A"},
        {"store_id": "store-b", "nome": "Loja B"},
        {"store_id": "0007", "nome": "Loja Numérica"},
    ]
    for tenant_id in ("tenant-a", "tenant-b"):
        tenant = root / tenant_id
        tenant.mkdir(parents=True)
        tenants[tenant_id] = tenant
        (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
            "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
            "strict_store_scope": True,
            "shared_groups": [],
        }), encoding="utf-8")
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(root), raising=False)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda tenant: str(root / tenant))
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda tenant: stores)
    return tenants, stores


def _photo(tenant: Path, store_id: str, sku: str = "001", content=b"image") -> str:
    segment = cadastro_fotos._cadastro_store_id_foto_segmento(store_id)
    reference = f"cadastro_fotos/lojas/{segment}/{sku}.png"
    target = tenant / reference
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return reference


def test_same_sku_uses_its_exact_store_and_tenant(scope):
    tenants, _ = scope
    reference_a = _photo(tenants["tenant-a"], "store-a", content=b"a")
    reference_b = _photo(tenants["tenant-a"], "store-b", content=b"b")
    _photo(tenants["tenant-b"], "store-a", content=b"other tenant")
    _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "001", "foto": reference_a},
        {"store_id": "store-b", "sku": "001", "foto": reference_b},
    ])
    records = [
        {"store_id": "store-a", "sku": "001", "loja_sync": "Loja B", "saldo_loja": 10},
        {"store_id": "store-b", "sku": "001", "saldo_loja": 20},
    ]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"].startswith("/api/cadastro/foto/tenant-a/lojas/")
    assert records[0]["__estoque_foto"].endswith("/001.png?store_id=store-a")
    assert records[1]["__estoque_foto"].endswith("/001.png?store_id=store-b")
    assert records[0]["__estoque_foto"] != records[1]["__estoque_foto"]
    filename_a = records[0]["__estoque_foto"].split("/tenant-a/", 1)[1].split("?", 1)[0]
    filename_b = records[1]["__estoque_foto"].split("/tenant-a/", 1)[1].split("?", 1)[0]
    assert Path(cadastro_fotos._cadastro_resolver_arquivo_foto("tenant-a", filename_a, "store-a")).read_bytes() == b"a"
    assert Path(cadastro_fotos._cadastro_resolver_arquivo_foto("tenant-a", filename_b, "store-b")).read_bytes() == b"b"
    assert records[0]["saldo_loja"] == 10


def test_legacy_store_name_requires_one_exact_current_match(scope):
    tenants, stores = scope
    _photo(tenants["tenant-a"], "store-a")
    _photo(tenants["tenant-a"], "store-b")
    stores.extend([
        {"store_id": "store-c", "nome": "Homonima"},
        {"store_id": "store-d", "nome": "Homonima"},
    ])
    records = [
        {"sku": "001", "loja_sync": "Loja A"},
        {"sku": "001", "loja_sync": "loja a"},
        {"sku": "001", "loja_sync": "Homonima"},
        {"sku": "001", "loja_sync": "Ausente"},
        {"sku": "001", "store_id": "STORE-A", "loja_sync": "Loja A"},
        {"sku": "001"},
    ]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"].endswith("store_id=store-a")
    assert all(row["__estoque_foto"] == "" for row in records[1:])
    assert "store_id" not in records[0]


def test_wrong_tenant_store_missing_file_and_unsafe_references_fail_closed(scope):
    tenants, _ = scope
    valid = _photo(tenants["tenant-a"], "store-b")
    other_tenant = _photo(tenants["tenant-b"], "store-a")
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    references = [
        valid,
        f"/api/cadastro/foto/tenant-b/lojas/{segment}/001.png",
        f"https://jk.test/api/cadastro/foto/tenant-b/lojas/{segment}/001.png",
        f"cadastro_fotos/lojas/{segment}/missing.png",
        f"cadastro_fotos/lojas/{segment}/../001.png",
        "data:image/png;base64,AAAA",
        "blob:https://jk.test/123",
        "javascript:alert(1)",
        "https://user:secret@cdn.test/photo.png",
        "https://cdn.test:wrong/photo.png",
        "//cdn.test/photo.png",
        "https://cdn.test/\nphoto.png",
        "cadastro_fotos/001.png",
    ]
    assert other_tenant
    for reference in references:
        _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [
            {"store_id": "store-a", "sku": "001", "foto": reference},
        ])
        records = [{"store_id": "store-a", "sku": "001"}]
        estoque_fotos.anexar_fotos_estoque("tenant-a", records)
        assert records[0]["__estoque_foto"] == "", reference


def test_http_photo_is_metadata_only_and_preserves_escaped_query(scope, monkeypatch):
    tenants, _ = scope
    photo = "https://cdn.test/item%20001.png?size=small&v=2"
    _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "001", "foto": photo},
    ])
    monkeypatch.setattr(cadastro_fotos.requests, "get", lambda *a, **kw: pytest.fail("No external image request is allowed"))
    records = [{"store_id": "store-a", "sku": "001"}]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"] == photo


def test_deleted_duplicate_or_inconsistent_cadastro_key_is_not_reintroduced(scope):
    tenants, _ = scope
    _photo(tenants["tenant-a"], "store-a")
    cases = [
        [{"store_id": "store-a", "sku": "001", "deleted_at_utc": "2026-10-01"}],
        [{"store_id": "store-a", "sku": "001", "foto": "https://cdn.test/a.png"},
         {"store_id": "store-a", "sku": "001", "foto": "https://cdn.test/b.png"}],
        [{"store_id": "store-a", "sku": "002", "sku_normalizado": "001", "foto": "https://cdn.test/a.png"}],
    ]
    for rows in cases:
        _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", rows)
        records = [{"store_id": "store-a", "sku": "001"}]
        estoque_fotos.anexar_fotos_estoque("tenant-a", records)
        assert records[0]["__estoque_foto"] == ""


def test_default_estoque_payload_is_unchanged_and_photos_are_opt_in(scope, monkeypatch):
    tenants, _ = scope
    path = tenants["tenant-a"] / "produtos_compilado.csv"
    _write_csv(path, [{
        "sku": "0010", "store_id": "0007", "loja_sync": "Loja Numérica", "saldo_loja": "12",
        "titulo_turbo": "hidden", "preco_turbo": "hidden", "status_turbo": "hidden", "id_bling": "hidden",
    }])
    _photo(tenants["tenant-a"], "0007", "0010")
    monkeypatch.setattr(estoque_common, "ARQUIVO_DB_PRODUTOS", str(path), raising=False)
    monkeypatch.setattr(estoque_common, "_migrar_arquivo_legado_para_tenant", lambda *args: str(path), raising=False)
    expected = pd.read_csv(path).fillna("").drop(columns=["titulo_turbo", "preco_turbo", "status_turbo", "id_bling"]).to_dict(orient="records")
    assert asyncio.run(estoque_common.listar_estoque(client_id="tenant-a")) == expected
    app = FastAPI()
    app.add_api_route("/api/estoque", estoque_common.listar_estoque, methods=["GET"])
    app.dependency_overrides[estoque_common.get_tenant_id] = lambda: "tenant-a"
    with TestClient(app) as client:
        assert client.get("/api/estoque").json() == expected
        enriched = client.get("/api/estoque?incluir_fotos=true").json()[0]
    assert enriched["sku"] == "0010"
    assert enriched["store_id"] == "0007"
    assert enriched["saldo_loja"] == 12
    assert enriched["__estoque_foto"].endswith("/0010.png?store_id=0007")
    assert "titulo_turbo" not in enriched


def test_unsafe_tenant_and_duplicate_store_configuration_omit_photos(scope, monkeypatch):
    tenants, stores = scope
    _photo(tenants["tenant-a"], "store-a")
    stores.append(dict(stores[0]))
    records = [{"store_id": "store-a", "sku": "001", "saldo_loja": 9}]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"] == ""
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", lambda tenant: str(tenants["tenant-b"]))
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0] == {"store_id": "store-a", "sku": "001", "saldo_loja": 9, "__estoque_foto": ""}


def test_empty_cadastro_photo_uses_exact_store_file_and_never_global_default(scope):
    tenants, _ = scope
    reference = _photo(tenants["tenant-a"], "store-a")
    _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "001", "foto": ""},
        {"store_id": "store-b", "sku": "001", "foto": ""},
    ])
    global_photo = tenants["tenant-a"] / "cadastro_fotos" / "002.png"
    global_photo.write_bytes(b"unscoped")
    default_photo = tenants["tenant-a"].parent / "default" / reference
    default_photo.parent.mkdir(parents=True, exist_ok=True)
    default_photo.write_bytes(b"default scope")
    records = [
        {"store_id": "store-a", "sku": "001"},
        {"store_id": "store-b", "sku": "001"},
        {"store_id": "store-a", "sku": "002"},
        {"store_id": "store-a", "sku": "01"},
    ]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"].endswith("/001.png?store_id=store-a")
    assert all(row["__estoque_foto"] == "" for row in records[1:])


def test_missing_stock_returns_empty_without_photo_reads(monkeypatch):
    monkeypatch.setattr(estoque_common, "ARQUIVO_DB_PRODUTOS", "missing", raising=False)
    monkeypatch.setattr(estoque_common, "_migrar_arquivo_legado_para_tenant", lambda *args: "missing", raising=False)
    monkeypatch.setattr(estoque_fotos, "anexar_fotos_estoque", lambda *args: pytest.fail("No photo read for an empty stock"))
    assert asyncio.run(estoque_common.listar_estoque(client_id="tenant-a", incluir_fotos=True)) == []


def test_photo_lookup_preserves_skus_that_pandas_normally_reads_as_missing(scope, monkeypatch):
    tenants, _ = scope
    path = tenants["tenant-a"] / "produtos_compilado.csv"
    _write_csv(path, [{"sku": "NA", "store_id": "store-a", "saldo_loja": "2"}])
    _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [{
        "sku": "NA", "store_id": "store-a", "foto": "https://cdn.test/na.png",
    }])
    monkeypatch.setattr(estoque_common, "ARQUIVO_DB_PRODUTOS", str(path), raising=False)
    monkeypatch.setattr(estoque_common, "_migrar_arquivo_legado_para_tenant", lambda *args: str(path), raising=False)
    default = asyncio.run(estoque_common.listar_estoque(client_id="tenant-a"))
    assert default[0]["sku"] == ""
    enriched = asyncio.run(estoque_common.listar_estoque(client_id="tenant-a", incluir_fotos=True))
    assert enriched[0]["sku"] == "NA"
    assert enriched[0]["__estoque_foto"] == "https://cdn.test/na.png"


def test_current_scoped_photo_wins_over_stale_or_external_saved_reference(scope):
    tenants, _ = scope
    _photo(tenants["tenant-a"], "store-a")
    for stale in ("https://cdn.test/old.png", "cadastro_fotos/lojas/store-b/missing.png"):
        _write_csv(tenants["tenant-a"] / "cadastro_produtos_lojas.csv", [{
            "sku": "001", "store_id": "store-a", "foto": stale,
        }])
        records = [{"sku": "001", "store_id": "store-a"}]
        estoque_fotos.anexar_fotos_estoque("tenant-a", records)
        assert records[0]["__estoque_foto"].endswith("/001.png?store_id=store-a")


def test_legacy_root_photo_requires_explicit_scope_and_never_falls_back_to_default(scope):
    tenants, _ = scope
    tenant = tenants["tenant-a"]
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
        "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": False,
        "shared_groups": [],
    }), encoding="utf-8")
    root_photo = tenant / "cadastro_fotos" / "001.png"
    root_photo.parent.mkdir(parents=True, exist_ok=True)
    root_photo.write_bytes(b"own tenant")
    default_photo = tenant.parent / "default" / "cadastro_fotos" / "001.png"
    default_photo.parent.mkdir(parents=True, exist_ok=True)
    default_photo.write_bytes(b"default tenant")
    _write_csv(tenant / "cadastro_produtos_lojas.csv", [{
        "store_id": "store-a", "sku": "001", "foto": "cadastro_fotos/001.png",
    }])
    records = [{"store_id": "store-a", "sku": "001"}, {"store_id": "store-b", "sku": "001"}]
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"] == "/api/cadastro/foto/tenant-a/001.png?store_id=store-a&tenant_only=true"
    assert records[1]["__estoque_foto"] == ""
    app = FastAPI()
    app.add_api_route("/api/cadastro/foto/{client_id}/{filename:path}", cadastro_fotos.servir_foto_cadastro, methods=["GET"])
    app.dependency_overrides[cadastro_fotos.get_tenant_id] = lambda: "tenant-a"
    with TestClient(app) as client:
        assert client.get(records[0]["__estoque_foto"]).content == b"own tenant"
        root_photo.unlink()
        assert client.get(records[0]["__estoque_foto"]).status_code == 404
        # Existing callers keep their historical behavior unless opting in.
        assert client.get("/api/cadastro/foto/tenant-a/001.png").content == b"default tenant"
    estoque_fotos.anexar_fotos_estoque("tenant-a", records)
    assert records[0]["__estoque_foto"] == ""


def test_legacy_root_api_reference_validates_tenant_before_conversion(scope):
    tenants, _ = scope
    tenant = tenants["tenant-a"]
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
        "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
        "strict_store_scope": False,
        "shared_groups": [],
    }), encoding="utf-8")
    photo = tenant / "cadastro_fotos" / "001.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"own tenant")
    valid_refs = ("/api/cadastro/foto/tenant-a/001.png", "/api/cadastro/foto-arquivo/001.png")
    invalid_refs = (
        "/api/cadastro/foto/tenant-b/001.png",
        "https://jk.test/api/cadastro/foto/tenant-b/001.png",
        "/api/cadastro/foto/tenant%2Db/001.png",
        "cadastro_fotos/../001.png",
    )
    for reference in (*valid_refs, *invalid_refs):
        _write_csv(tenant / "cadastro_produtos_lojas.csv", [{"sku": "001", "store_id": "store-a", "foto": reference}])
        records = [{"sku": "001", "store_id": "store-a"}]
        estoque_fotos.anexar_fotos_estoque("tenant-a", records)
        if reference in valid_refs:
            assert records[0]["__estoque_foto"].endswith("store_id=store-a&tenant_only=true")
        else:
            assert records[0]["__estoque_foto"] == "", reference
