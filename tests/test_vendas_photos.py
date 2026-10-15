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
    stripped["rows"] = [{key: value for key, value in row.items() if key not in {"foto", "store_id"}} for row in payload["rows"]]
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
