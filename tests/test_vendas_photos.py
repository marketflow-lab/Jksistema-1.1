from __future__ import annotations

import copy
import csv
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request

from backend.modules.vendas import photos
from backend.modules.vendas.router import create_vendas_router


def _summary():
    return {
        "success": True,
        "rows": [
            {"sku": "001", "produto": "Título da venda preservado", "pedidos": 3, "itens": 4, "valor": 123.45, "devolucoes": 1, "valorDevolucao": 2.5},
            {"sku": "002", "produto": "Outro produto", "pedidos": 1, "itens": 1, "valor": 10.0, "devolucoes": 0},
        ],
        "totals": {"pedidos": 4, "itens": 5, "valor": 133.45, "valorLiquido": 130.95},
        "periodo": {"inicio": "2026-04-01", "fim": "2026-10-15"},
    }


def _strip_photo_metadata(payload):
    stripped = dict(payload)
    stripped.pop("photo_client_id", None)
    stripped["rows"] = [{key: value for key, value in row.items() if key not in {
        "foto", "store_id", "photo_scope", "photo_store_name", "photo_ambiguous",
    }} for row in payload["rows"]]
    return stripped


def _catalog(monkeypatch, tmp_path):
    from backend.services import cadastro_fotos, cadastro_lojas_produtos, integracoes

    info = tmp_path / "info"
    stores = [{"store_id": "store-a", "nome": "Loja A"}, {"store_id": "store-b", "nome": "Loja B"}]
    tenant_path = lambda client_id: str(info / client_id)
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(info))
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client_id: stores)
    monkeypatch.setattr(integracoes, "ler_lojas", lambda _client_id: stores)
    monkeypatch.setattr(cadastro_lojas_produtos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(info), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _client_id: stores)
    refs = {}
    for client_id in ("tenant-a", "tenant-b"):
        tenant = info / client_id
        tenant.mkdir(parents=True)
        (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
            "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA, "strict_store_scope": True, "shared_groups": [],
        }), encoding="utf-8")
        records = []
        for store_id in ("store-a", "store-b"):
            ref = f"cadastro_fotos/lojas/{cadastro_fotos._cadastro_store_id_foto_segmento(store_id)}/001.png"
            image = tenant / ref
            image.parent.mkdir(parents=True)
            image.write_bytes(f"{client_id}/{store_id}".encode())
            refs[(client_id, store_id)] = ref
            records.append({"store_id": store_id, "sku": "001", "sku_normalizado": "001", "nome": "Nome do cadastro diferente", "foto": ref, "row_version": "1", "deleted_at_utc": ""})
        with (tenant / "cadastro_produtos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    return stores, refs


def test_real_projection_keeps_store_tenant_and_financial_result(monkeypatch, tmp_path):
    _stores, refs = _catalog(monkeypatch, tmp_path)
    original = _summary()
    frozen = copy.deepcopy(original)
    first = photos.enrich_summary_photos(original, "tenant-a", "Loja A")
    second = photos.enrich_summary_photos(original, "tenant-b", "store-b")
    assert first["photo_client_id"] == "tenant-a"
    assert first["rows"][0]["store_id"] == "store-a"
    assert first["rows"][0]["foto"] == refs[("tenant-a", "store-a")]
    assert second["photo_client_id"] == "tenant-b"
    assert second["rows"][0]["store_id"] == "store-b"
    assert second["rows"][0]["foto"] == refs[("tenant-b", "store-b")]
    assert first["rows"][1]["foto"] == ""
    assert first["rows"][1]["store_id"] == ""
    assert _strip_photo_metadata(first) == frozen
    assert _strip_photo_metadata(second) == frozen
    assert original == frozen
    assert first["rows"][0] is not original["rows"][0]


@pytest.mark.parametrize("loja", [None, "", "__todas", "Todas as lojas"])
def test_all_stores_never_choose_an_ambiguous_photo(monkeypatch, loja):
    monkeypatch.setattr(photos, "_project_photos", lambda *_args: {
        "scope": "global_safe", "loja_resolvida": True, "store_id": "", "produtos": [{
            "sku": "001", "store_id": "store-a", "store_scope_ambiguous": True,
            "foto": "cadastro_fotos/lojas/store-a/001.png",
        }],
    })
    result = photos.enrich_summary_photos(_summary(), "tenant-a", loja)
    assert all(row["foto"] == row["store_id"] == "" for row in result["rows"])
    assert _strip_photo_metadata(result) == _summary()


def test_global_projection_with_one_exact_store_can_show_a_real_photo(monkeypatch, tmp_path):
    stores, refs = _catalog(monkeypatch, tmp_path)
    stores[:] = [stores[0]]
    result = photos.enrich_summary_photos(_summary(), "tenant-a", "__todas")
    assert result["rows"][0]["store_id"] == "store-a"
    assert result["rows"][0]["foto"] == refs[("tenant-a", "store-a")]
    assert _strip_photo_metadata(result) == _summary()


def test_global_projection_with_several_stores_has_no_ambiguous_photo(monkeypatch, tmp_path):
    _catalog(monkeypatch, tmp_path)
    result = photos.enrich_summary_photos(_summary(), "tenant-a", "__todas")
    assert all(row["foto"] == row["store_id"] == "" for row in result["rows"])
    assert _strip_photo_metadata(result) == _summary()


def test_ambiguous_store_fails_closed_and_titles_stay_from_sales(monkeypatch, tmp_path):
    stores, _refs = _catalog(monkeypatch, tmp_path)
    stores[1]["nome"] = "Loja A"
    result = photos.enrich_summary_photos(_summary(), "tenant-a", "Loja A")
    assert all(row["foto"] == row["store_id"] == "" for row in result["rows"])
    assert _strip_photo_metadata(result) == _summary()


def test_projection_failure_does_not_fail_sales_or_modify_cache(monkeypatch):
    original = _summary()
    def broken(*_args):
        raise OSError("Foto indisponível")
    monkeypatch.setattr(photos, "_project_photos", broken)
    result = photos.enrich_summary_photos(original, "tenant-a", "Loja A")
    assert _strip_photo_metadata(result) == original == _summary()
    assert all(row["foto"] == "" for row in result["rows"])


def test_projection_is_one_batch_and_does_not_expose_extra_catalog_fields(monkeypatch):
    calls = []
    segment = "sid-" + hashlib.sha256(b"store-a").hexdigest()
    def project(*args):
        calls.append(args)
        return {"scope": "store", "loja_resolvida": True, "store_id": "store-a", "produtos": [{
            "sku": "001", "store_id": "store-a", "foto": f"cadastro_fotos/lojas/{segment}/001.png",
            "nome": "Não substituir título", "custo": "99", "credencial": "não expor",
        }]}
    monkeypatch.setattr(photos, "_project_photos", project)
    result = photos.enrich_summary_photos(_summary(), "tenant-a", "Loja A")
    assert calls == [("tenant-a", "Loja A", ("001", "002"))]
    assert set(result["rows"][0]) == set(_summary()["rows"][0]) | {"foto", "store_id"}
    assert result["rows"][0]["produto"] == "Título da venda preservado"


@pytest.mark.parametrize("reference", [
    "cadastro_fotos/global.png", "cadastro_fotos/lojas/store-b/001.png",
    "/api/cadastro/foto/tenant-b/lojas/store-a/001.png", "javascript:alert(1)",
    "https://user:secret@example.test/photo.png", "file:///tmp/photo.png",
])
def test_unsafe_or_unscoped_photo_reference_is_not_exposed(reference):
    assert photos._photo_reference(reference, "tenant-a", "store-a") == ""


def test_http_opt_in_preserves_old_response_and_openapi(monkeypatch):
    original = _summary()
    projections = []
    monkeypatch.setattr(photos, "_project_photos", lambda *args: projections.append(args) or {})
    app = FastAPI()
    router = create_vendas_router(
        SimpleNamespace(resumo_vendas=lambda **_kwargs: original),
        SimpleNamespace(), SimpleNamespace(get_tenant_id=lambda: "tenant-a"),
    )
    app.include_router(router)
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/vendas/resumo")
    plain_request = Request({"type": "http", "headers": []})
    assert endpoint(plain_request, loja="Loja A", client_id="tenant-a") == original
    assert not projections
    photo_request = Request({"type": "http", "headers": [(photos.PHOTO_HEADER.lower().encode(), b"1")]})
    response = endpoint(photo_request, loja="Loja A", client_id="tenant-a")
    assert response["photo_client_id"] == "tenant-a"
    assert projections == [("tenant-a", "Loja A", ("001", "002"))]
    assert original == _summary()
    parameters = app.openapi()["paths"]["/api/vendas/resumo"]["get"]["parameters"]
    assert [item["name"] for item in parameters] == ["data_inicio", "data_fim", "sku", "loja", "unidade_negocio", "incluir_ebazar"]
    assert not any(item["in"] == "header" for item in parameters)
    assert len(router.routes) == 20


def _write_catalog_records(tenant, records):
    fields = list(dict.fromkeys(key for record in records for key in record))
    with (tenant / "cadastro_produtos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def _case_record(store, sku, name, *, photo="", deleted=""):
    return {"store_id": store, "sku": sku, "sku_normalizado": sku, "nome": name,
            "foto": photo, "row_version": "1", "deleted_at_utc": deleted}


def _reported_catalog(monkeypatch, tmp_path):
    """Anonymous fixture with the observed scope/counts, never real records."""
    from backend.services import cadastro_fotos

    stores, _refs = _catalog(monkeypatch, tmp_path)
    stores.extend({"store_id": f"store-{letter}", "nome": f"Loja {letter.upper()}"} for letter in "cdefghijk")
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    root = tenant / "cadastro_fotos" / "405.png"
    root.write_bytes(b"anonymous tenant-only handle photo")
    refs = {}
    for sku in ("405", "1021"):
        ref = f"cadastro_fotos/lojas/{cadastro_fotos._cadastro_store_id_foto_segmento('store-b')}/{sku}.png"
        (tenant / ref).write_bytes(f"anonymous scoped photo {sku}".encode())
        refs[sku] = ref
    records = []
    for index, letter in enumerate("abcdef"):
        title = "Macaneta Frontier" if index == 0 else f"Macaneta Frontier variante {index % 2}"
        records.append(_case_record(f"store-{letter}", "405", title, photo=refs["405"] if letter == "b" else ""))
    for sku, letters in (("1021", "ab"), ("2009", "abc"), ("171", "ab")):
        for letter in letters:
            record = _case_record(f"store-{letter}", sku, f"Produto {sku}", photo=refs["1021"] if sku == "1021" and letter == "b" else "")
            if sku in {"2009", "171"}:
                record["imagens_bling"] = json.dumps({"imagensURL": [], "imagensExternas": [], "imagens": []})
            records.append(record)
    _write_catalog_records(tenant, records)
    return tenant, stores, refs, records


def _reported_summary():
    return {
        "success": True,
        "rows": [
            {"sku": sku, "produto": "Macaneta Frontier" if sku == "405" else f"Produto {sku}",
             "loja_conta": "Loja A", "pedidos": 3, "itens": 4, "valor": 123.45, "devolucoes": 1}
            for sku in ("405", "1021", "2009", "171")
        ],
        "totals": {"pedidos": 12, "itens": 16, "valor": 493.8},
        "meta": {"data_inicio": "2026-04-01", "data_fim": "2026-10-15", "loja": "__todas", "unidade_negocio": "__todos"},
    }


def test_reported_four_skus_preserve_provenance_financials_and_catalog(monkeypatch, tmp_path):
    tenant, _stores, refs, _records = _reported_catalog(monkeypatch, tmp_path)
    original = _reported_summary()
    frozen = copy.deepcopy(original)
    sources = {path: path.read_bytes() for path in tenant.rglob("*") if path.is_file()}
    result = photos.enrich_summary_photos(original, "tenant-a", "__todas")
    rows = {row["sku"]: row for row in result["rows"]}
    assert rows["405"]["foto"] == "/api/cadastro/foto/tenant-a/405.png?tenant_only=true"
    assert rows["405"]["photo_scope"] == "tenant"
    assert rows["405"]["store_id"] == ""
    assert rows["1021"]["foto"] == refs["1021"]
    assert rows["1021"]["store_id"] == "store-b"
    assert rows["1021"]["photo_store_name"] == "Loja B"
    assert "photo_scope" not in rows["1021"]
    assert rows["2009"]["foto"] == rows["171"]["foto"] == ""
    assert _strip_photo_metadata(result) == frozen == original
    assert result["totals"] is original["totals"]
    assert {path: path.read_bytes() for path in sources} == sources


def test_representative_aggregate_photo_never_crosses_specific_store(monkeypatch, tmp_path):
    _tenant, _stores, refs, _records = _reported_catalog(monkeypatch, tmp_path)
    source = _reported_summary()
    aggregate = photos.enrich_summary_photos(source, "tenant-a", "__todas")
    first = photos.enrich_summary_photos(source, "tenant-a", "store-a")
    second = photos.enrich_summary_photos(source, "tenant-a", "store-b")
    assert aggregate["rows"][1]["foto"] == second["rows"][1]["foto"] == refs["1021"]
    assert first["rows"][1]["foto"] == ""
    assert first["rows"][0]["photo_scope"] == "tenant"
    assert _strip_photo_metadata(first) == _strip_photo_metadata(second) == source


@pytest.mark.parametrize("title,expected", [("Produto A", True), ("Produto B", False), ("-", False), ("", False)])
def test_conflicting_names_require_origin_and_decisive_sales_title(monkeypatch, tmp_path, title, expected):
    _stores, refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    _write_catalog_records(tenant, [
        _case_record("store-a", "001", "Produto A", photo=refs[("tenant-a", "store-a")]),
        _case_record("store-b", "001", "Produto B", photo=refs[("tenant-a", "store-b")]),
    ])
    source = {"rows": [{"sku": "001", "produto": title, "loja_conta": "Loja A", "valor": 7}], "totals": {"valor": 7}}
    result = photos.enrich_summary_photos(source, "tenant-a", "__todas")
    if expected:
        assert result["rows"][0]["foto"] == refs[("tenant-a", "store-a")]
        assert result["rows"][0]["photo_store_name"] == "Loja A"
    else:
        assert result["rows"][0]["foto"] == ""
        assert result["rows"][0]["photo_ambiguous"] is True
    assert _strip_photo_metadata(result) == source


def test_consistent_names_allow_decisive_origin_with_several_different_photos(monkeypatch, tmp_path):
    _stores, refs = _catalog(monkeypatch, tmp_path)
    source = _summary()
    source["rows"][0]["loja_conta"] = "Loja B"
    result = photos.enrich_summary_photos(source, "tenant-a", "__todas")
    assert result["rows"][0]["foto"] == refs[("tenant-a", "store-b")]
    assert result["rows"][0]["store_id"] == "store-b"
    assert _strip_photo_metadata(result) == source


def test_single_other_store_photo_is_blocked_when_canonical_product_names_conflict(monkeypatch, tmp_path):
    tenant, _stores, _refs, records = _reported_catalog(monkeypatch, tmp_path)
    next(record for record in records if record["sku"] == "1021" and record["store_id"] == "store-a")["nome"] = "Outro produto"
    _write_catalog_records(tenant, records)
    result = photos.enrich_summary_photos(_reported_summary(), "tenant-a", "__todas")
    row = result["rows"][1]
    assert row["foto"] == row["store_id"] == ""
    assert row["photo_ambiguous"] is True


@pytest.mark.parametrize("scope", ["__todas", "store-a", "store-b"])
def test_tombstones_never_fall_back_to_legacy_tenant_or_default_photos(monkeypatch, tmp_path, scope):
    from backend.services import cadastro_fotos

    _stores, _refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    _write_catalog_records(tenant, [_case_record("store-a", "099", "Deleted product", deleted="2026-01-01")])
    (tenant / "cadastro_fotos" / "099.png").write_bytes(b"old tenant root")
    default = tmp_path / "info" / "default" / "cadastro_fotos"
    default.mkdir(parents=True)
    (default / "099.png").write_bytes(b"default root")
    source = {"rows": [{"sku": "099", "produto": "Deleted product", "loja_conta": "Loja A", "valor": 5}]}
    result = photos.enrich_summary_photos(source, "tenant-a", scope)
    assert result["rows"][0]["foto"] == ""
    assert "photo_scope" not in result["rows"][0]
    assert _strip_photo_metadata(result) == source


@pytest.mark.parametrize("scope", ["__todas", "store-a"])
def test_legacy_sku_without_controlled_records_uses_only_its_tenant_root(monkeypatch, tmp_path, scope):
    from backend.services import cadastro_fotos

    _stores, _refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    (tenant / "cadastro_fotos" / "777.png").write_bytes(b"tenant-a legacy")
    other = tmp_path / "info" / "tenant-b" / "cadastro_fotos"
    (other / "888.png").write_bytes(b"tenant-b only")
    default = tmp_path / "info" / "default" / "cadastro_fotos"
    default.mkdir(parents=True)
    (default / "999.png").write_bytes(b"default only")
    source = {"rows": [{"sku": sku, "produto": "Legacy", "valor": 8} for sku in ("777", "888", "999")]}
    result = photos.enrich_summary_photos(source, "tenant-a", scope)
    assert result["rows"][0]["foto"] == "/api/cadastro/foto/tenant-a/777.png?tenant_only=true"
    assert result["rows"][0]["photo_scope"] == "tenant"
    assert result["rows"][0]["store_id"] == ""
    assert result["rows"][1]["foto"] == result["rows"][2]["foto"] == ""
    assert _strip_photo_metadata(result) == source


def test_removed_store_and_its_photos_are_never_representative(monkeypatch, tmp_path):
    tenant, stores, _refs, _records = _reported_catalog(monkeypatch, tmp_path)
    stores[:] = [store for store in stores if store["store_id"] != "store-b"]
    result = photos.enrich_summary_photos(_reported_summary(), "tenant-a", "__todas")
    assert result["rows"][1]["foto"] == ""
    assert result["rows"][1]["store_id"] == ""


@pytest.mark.parametrize("reference", [
    "/api/cadastro/foto/tenant-b/405.png?tenant_only=true",
    "https://example.test/api/cadastro/foto/tenant-b/405.png",
    "/api/cadastro/foto/tenant%2Db/405.png",
    "/api/cadastro/foto/tenant-a/%252e%252e%252f405.png",
    "cadastro_fotos/%255c405.png",
    "https://user:secret@example.test/api/cadastro/foto/tenant-a/405.png",
])
def test_global_photo_url_cannot_change_tenant_or_escape_its_root(monkeypatch, tmp_path, reference):
    _reported_catalog(monkeypatch, tmp_path)
    assert photos._photo_reference(reference, "tenant-a", "store-a") == ""


def test_global_photo_reference_requires_existing_non_strict_tenant_file(monkeypatch, tmp_path):
    from backend.services import cadastro_fotos

    tenant, _stores, _refs, _records = _reported_catalog(monkeypatch, tmp_path)
    expected = "/api/cadastro/foto/tenant-a/405.png?tenant_only=true"
    for reference in ("cadastro_fotos/405.png", expected, "/api/cadastro/foto-arquivo/405.png"):
        assert photos._photo_reference(reference, "tenant-a", "store-a") == expected
    assert photos._photo_reference("cadastro_fotos/missing.png", "tenant-a", "store-a") == ""
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
        "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA, "strict_store_scope": True, "shared_groups": [],
    }), encoding="utf-8")
    assert photos._photo_reference("cadastro_fotos/405.png", "tenant-a", "store-a") == ""
    assert not photos._tenant_root_photos("tenant-a", ("405",))


def test_global_file_and_photo_directory_links_fail_closed(monkeypatch, tmp_path):
    from backend.services import cadastro_fotos

    tenant, _stores, _refs, _records = _reported_catalog(monkeypatch, tmp_path)
    original_link = cadastro_fotos._cadastro_caminho_e_link
    monkeypatch.setattr(cadastro_fotos, "_cadastro_caminho_e_link", lambda path: str(path).endswith("405.png") or original_link(path))
    assert photos._photo_reference("cadastro_fotos/405.png", "tenant-a", "store-a") == ""
    monkeypatch.setattr(cadastro_fotos, "_cadastro_caminho_e_link", lambda path: str(path) == str(tenant / "cadastro_fotos") or original_link(path))
    assert not photos._tenant_root_photos("tenant-a", ("405",))


def test_aggregate_catalog_reads_canonical_once_and_batches_by_store(monkeypatch, tmp_path):
    from backend.services import cadastro_lojas_produtos

    _tenant, stores, _refs, _records = _reported_catalog(monkeypatch, tmp_path)
    reads, batches = [], []
    read = cadastro_lojas_produtos._ler_registros_persistidos
    listing = cadastro_lojas_produtos._listar_produtos_loja_sync
    def tracked_read(*args):
        reads.append(args)
        return read(*args)
    def tracked_batch(*args, **kwargs):
        batches.append(kwargs["skus"])
        return listing(*args, **kwargs)
    monkeypatch.setattr(cadastro_lojas_produtos, "_ler_registros_persistidos", tracked_read)
    monkeypatch.setattr(cadastro_lojas_produtos, "_listar_produtos_loja_sync", tracked_batch)
    result = photos.enrich_summary_photos(_reported_summary(), "tenant-a", "__todas")
    assert result["rows"][1]["foto"]
    assert reads == [("tenant-a",)]
    assert len(batches) == len(stores)
    assert set(batches) == {("405", "1021", "2009", "171")}


@pytest.mark.parametrize("scope", ["__todas", "store-a", "store-b"])
@pytest.mark.parametrize("canonical,sales_sku", [("001", "1"), ("2-3", "2-MAR")])
def test_canonical_sku_alias_tombstone_cannot_resurrect_root_photo(monkeypatch, tmp_path, scope, canonical, sales_sku):
    from backend.services import cadastro_fotos

    _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    _write_catalog_records(tenant, [_case_record("store-a", canonical, "Deleted", deleted="2026-01-01")])
    (tenant / "cadastro_fotos" / f"{sales_sku}.png").write_bytes(b"must not resurrect")
    original = {"rows": [{"sku": sales_sku, "produto": "Deleted", "valor": 12}]}
    result = photos.enrich_summary_photos(original, "tenant-a", scope)
    assert result["rows"][0]["foto"] == ""
    assert _strip_photo_metadata(result) == original


@pytest.mark.parametrize("canonical,sales_sku", [("001", "1"), ("2-3", "2-MAR")])
def test_canonical_sku_alias_matches_active_photo_without_soft_key(monkeypatch, tmp_path, canonical, sales_sku):
    from backend.services import cadastro_fotos

    _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    ref = f"cadastro_fotos/lojas/{cadastro_fotos._cadastro_store_id_foto_segmento('store-a')}/{canonical}.png"
    (tenant / ref).write_bytes(b"active canonical product")
    _write_catalog_records(tenant, [_case_record("store-a", canonical, "Active", photo=ref)])
    original = {"rows": [{"sku": sales_sku, "produto": "Active", "loja_conta": "Loja A", "valor": 12}]}
    result = photos.enrich_summary_photos(original, "tenant-a", "__todas")
    assert result["rows"][0]["foto"] == ref
    assert result["rows"][0]["sku"] == sales_sku
    assert photos._sku_key("001A") != photos._sku_key("1A")
    assert _strip_photo_metadata(result) == original


@pytest.mark.parametrize("scope", ["__todas", "store-b"])
def test_controlled_sku_legacy_shadow_cannot_resurrect_tenant_photo(monkeypatch, tmp_path, scope):
    from backend.services import cadastro_fotos, cadastro_lojas_produtos

    _stores, refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / refs[("tenant-a", "store-b")]).unlink()
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    _write_catalog_records(tenant, [_case_record("store-a", "001", "Deleted", deleted="2026-01-01")])
    (tenant / "cadastro_fotos" / "001.png").write_bytes(b"old root file must not resurrect")
    with (tenant / "cadastro_produtos.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "nome", "loja_sync", "foto"])
        writer.writeheader()
        writer.writerow({"sku": "1", "nome": "Legacy", "loja_sync": "Loja B", "foto": "cadastro_fotos/001.png"})
    projected = cadastro_lojas_produtos._listar_produtos_loja_sync("tenant-a", "store-b", skus=("1",))
    assert projected[0]["scope_source"] == "legacy_shadow"
    assert projected[0]["sku"] == "001"
    assert projected[0]["foto"] == "cadastro_fotos/001.png"
    source = {"rows": [{"sku": "1", "produto": "Legacy", "loja_conta": "Loja B", "valor": 13}]}
    result = photos.enrich_summary_photos(source, "tenant-a", scope)
    assert result["rows"][0]["foto"] == ""
    assert "photo_scope" not in result["rows"][0]
    assert _strip_photo_metadata(result) == source


@pytest.mark.parametrize("scope", ["__todas", "store-b"])
def test_canonical_scoped_record_can_keep_its_permitted_legacy_global_photo(monkeypatch, tmp_path, scope):
    from backend.services import cadastro_fotos

    _stores, refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / refs[("tenant-a", "store-b")]).unlink()
    (tenant / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).unlink()
    (tenant / "cadastro_fotos" / "001.png").write_bytes(b"explicit scoped legacy image")
    _write_catalog_records(tenant, [
        _case_record("store-a", "001", "Deleted", deleted="2026-01-01"),
        _case_record("store-b", "001", "Current", photo="cadastro_fotos/001.png"),
    ])
    source = {"rows": [{"sku": "1", "produto": "Current", "loja_conta": "Loja B", "valor": 13}]}
    result = photos.enrich_summary_photos(source, "tenant-a", scope)
    assert result["rows"][0]["foto"] == "/api/cadastro/foto/tenant-a/001.png?tenant_only=true"
    assert result["rows"][0]["photo_scope"] == "tenant"
    assert result["rows"][0]["store_id"] == ""
    assert _strip_photo_metadata(result) == source


@pytest.mark.parametrize("shadow_name", ["Produto B", ""])
def test_different_or_unnamed_legacy_shadow_invalidates_aggregate_identity(monkeypatch, tmp_path, shadow_name):
    from backend.services import cadastro_lojas_produtos

    _stores, refs = _catalog(monkeypatch, tmp_path)
    tenant = tmp_path / "info" / "tenant-a"
    (tenant / refs[("tenant-a", "store-b")]).unlink()
    _write_catalog_records(tenant, [_case_record("store-a", "001", "Produto A", photo=refs[("tenant-a", "store-a")])])
    remote = "https://images.example.test/product-b.png"
    with (tenant / "cadastro_produtos.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "nome", "loja_sync", "foto"])
        writer.writeheader()
        writer.writerow({"sku": "001", "nome": shadow_name, "loja_sync": "Loja B", "foto": remote})
    projected = cadastro_lojas_produtos._listar_produtos_loja_sync("tenant-a", "store-b", skus=("001",))
    assert projected[0]["scope_source"] == "legacy_shadow"
    assert projected[0]["foto"] == remote
    projection = photos._project_photos("tenant-a", "__todas", ("001",))
    assert "001" not in projection["photo_identity_consistent"]
    source = {"rows": [{"sku": "001", "produto": "Produto A", "loja_conta": "Loja B", "valor": 14}]}
    result = photos.enrich_summary_photos(source, "tenant-a", "__todas")
    assert result["rows"][0]["foto"] == ""
    assert result["rows"][0]["photo_ambiguous"] is True
    assert _strip_photo_metadata(result) == source
