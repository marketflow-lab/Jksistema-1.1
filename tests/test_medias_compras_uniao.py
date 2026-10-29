import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.schemas import medias_compras as schemas
from backend.schemas.medias_compras import ListasPedidosUniaoPreviaRequest as Previa, ListasPedidosUniaoRequest as Uniao
from backend.services import medias_compras_common as common, medias_compras_uniao as service


def product(sku="001", qty=2, price=5, **extra):
    return {"SKU": sku, "Quantidade": qty, "Valor unidade": price, "Valor total": qty * price,
            "Título do produto em inglês": "Peça sintética", "OEM": "OEM-A", "Color/side": "Direito",
            "M3 individual": "0.125", "Estimed CBM": float(Decimal(str(qty)) * Decimal("0.125")),
            "Estimed Weigh": float(Decimal(str(qty)) * Decimal("0.2")),
            **extra}


@pytest.fixture
def store(tmp_path, monkeypatch):
    location = lambda tenant: str(tmp_path / tenant / "listas_pedidos.json")
    monkeypatch.setattr(common, "_arquivo_listas_pedidos", location)
    monkeypatch.setattr(common, "_limpar_cache_lista_pedido", lambda *a, **kw: None)
    monkeypatch.setattr(common, "_resolver_escopo_loja_medias", lambda tenant, loja, store_id, **kw:
                        {"loja": loja, "store_id": store_id or "store-a", "scope": "store"})
    monkeypatch.setattr(common, "_resolver_fornecedor_lista", lambda tenant, supplier:
                        {"fornecedor_id": supplier, "supplier": "Fornecedor sintético"})
    rows = [{"id": key, "nome_lista": "Pedido " + key, "loja": "Loja A", "store_id": "store-a",
             "fornecedor_id": "supplier-a", "currency": "USD", "status": "Lista gerada",
             "itens": [product(qty=qty)], "updated_at": "2026-10-29T12:00:00"}
            for key, qty in [("a", 2), ("b", 3), ("c", 4)]]
    common._salvar_listas_pedidos("tenant-a", rows)
    return location


def preview(ids=("a", "b"), tenant="tenant-a"):
    return service._previa_sync(Previa(lista_ids=list(ids)), tenant)["previa"]


def request(data, **overrides):
    return Uniao(lista_ids=[row["id"] for row in data["listas"]], nome_lista="Pedido consolidado",
                 previa_token=data["token"], operacao_id="operacao-sintetica-001", **overrides)


def edit(mutator, tenant="tenant-a"):
    rows = common._carregar_listas_pedidos(tenant)
    mutator(rows)
    common._salvar_listas_pedidos(tenant, rows)


def reopen(lista, store, monkeypatch):
    from backend.services import medias_compras_listas as lists, medias_compras_fiscal as fiscal, cadastro_compatibilidade
    monkeypatch.setattr(lists, "_limpar_cache_lista_pedido", lambda *a, **kw: None)
    monkeypatch.setattr(lists, "get_tenant_path", lambda tenant: str(Path(store(tenant)).parent))
    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja", lambda *a, **kw:
                        {"scope": "store", "store_id": "store-a", "produtos": [], "loja_resolvida": True})
    # Isolate catalogue I/O while exercising the actual fiscal item normalizer.
    monkeypatch.setattr(fiscal, "_enriquecer_itens_lista_pedido_com_impostos", lambda tenant, items, **kw: copy.deepcopy(items))
    return lists._api_medias_compras_lista_pedido_detalhe_sync(lista["id"], client_id="tenant-a")["lista"]


def test_three_lists_preview_is_read_only_and_commit_sums_totals(store):
    before = Path(store("tenant-a")).read_bytes()
    data = preview(("c", "b", "a"))
    assert Path(store("tenant-a")).read_bytes() == before
    assert data["total_listas"] == 3
    assert data["total_skus"] == data["skus_repetidos"] == 1
    assert data["conflitos"] == []
    result = service._unir_sync(request(data), "tenant-a")
    lista, item = result["lista"], result["lista"]["itens"][0]
    assert lista["status"] == "Lista gerada"
    assert item["Quantidade"] == 9
    assert item["Valor total"] == 45
    assert item["Estimed CBM"] == "" and item["Cubagem status"] == "pendente"
    assert item["Estimed Weigh"] == pytest.approx(1.8)
    current = common._carregar_listas_pedidos("tenant-a")
    assert len(current) == 4
    for original, archived in zip(json.loads(before), current[1:]):
        assert archived["itens"] == original["itens"]
        assert archived["unida_em_lista_id"] == lista["id"]
        assert archived["unida_em_lista_nome"] == "Pedido consolidado"
    assert {row["id"] for row in lista["uniao_origens"]} == {"a", "b", "c"}


def test_real_conflicts_require_explicit_valid_choices(store):
    edit(lambda rows: rows[1]["itens"][0].update({"Valor unidade": 7, "OEM": "OEM-B", "Color/side": "Esquerdo"}))
    data = preview()
    assert {conflict["campo"] for conflict in data["conflitos"]} == {"Valor unidade", "OEM", "Color/side"}
    before = Path(store("tenant-a")).read_bytes()
    with pytest.raises(HTTPException) as missing:
        service._unir_sync(request(data), "tenant-a")
    assert missing.value.status_code == 400
    choices = {conflict["id"]: conflict["opcoes"][-1]["id"] for conflict in data["conflitos"]}
    with pytest.raises(HTTPException):
        service._unir_sync(request(data, escolhas={**choices, "unknown": "injected"}), "tenant-a")
    assert Path(store("tenant-a")).read_bytes() == before
    item = service._unir_sync(request(data, escolhas=choices), "tenant-a")["lista"]["itens"][0]
    assert item["Quantidade"] == 5 and item["Valor unidade"] == 7 and item["Valor total"] == 35
    assert item["OEM"] == "OEM-B" and item["Color/side"] == "Esquerdo"


def test_canonical_numeric_equality_zero_and_unique_skus(store):
    def change(rows):
        rows[0]["itens"] = [product("001", 2, 0), product("1", 1, 2)]
        rows[1]["itens"] = [product("001", 3, 0, **{"Valor unidade": "0.00", "M3 individual": "0,125"})]
    edit(change)
    data = preview()
    assert data["conflitos"] == []
    result = service._unir_sync(request(data), "tenant-a")["lista"]
    assert len(result["itens"]) == 2
    assert result["itens"][0]["Quantidade"] == 5
    assert result["itens"][0]["Valor total"] == 0


@pytest.mark.parametrize("quantity", [1.25, "2,5"])
def test_fractional_quantities_are_rejected_before_they_can_be_truncated(store, quantity):
    edit(lambda rows: rows[0]["itens"][0].update({"Quantidade": quantity}))
    before = Path(store("tenant-a")).read_bytes()
    with pytest.raises(HTTPException, match="inteira") as error:
        preview()
    assert error.value.status_code == 409
    assert Path(store("tenant-a")).read_bytes() == before


def test_saved_result_survives_full_detail_normalization(store, monkeypatch):
    created = service._unir_sync(request(preview(("a", "b", "c"))), "tenant-a")["lista"]
    reopened = reopen(created, store, monkeypatch)
    assert reopened["itens"][0]["Quantidade"] == created["itens"][0]["Quantidade"] == 9
    assert reopened["itens"][0]["Valor total"] == created["itens"][0]["Valor total"] == 45


def test_total_without_unit_price_is_preserved(store):
    edit(lambda rows: [row["itens"][0].pop("Valor unidade") for row in rows])
    data = preview()
    assert data["conflitos"] == []
    item = service._unir_sync(request(data), "tenant-a")["lista"]["itens"][0]
    assert item["Valor unidade"] == 5 and item["Valor total"] == 25


def test_catalogue_fields_are_not_offered_as_manual_choices_and_are_reset(store):
    def change(rows):
        rows[0]["itens"][0].update({"M3 individual": "", "Estimed CBM": "", "Cubagem status": "pendente",
                                     "Foto": "foto-a", "NCM": "1234", "CEST": "9876",
                                     "analise_concorrentes": {"margem": 99}, "Imposto": "Resultado antigo"})
        rows[1]["itens"][0].update({"Foto": "foto-b", "NCM": "5678", "CEST": "5432"})
    edit(change)
    data = preview()
    assert data["conflitos"] == []
    result = service._unir_sync(request(data), "tenant-a")["lista"]
    item = result["itens"][0]
    assert item["Cubagem status"] == "pendente"
    assert item["Estimed CBM"] == "" and item["M3 individual"] == "" and item["Foto"] == ""
    assert not item.get("NCM") and not item.get("CEST")
    assert result["m3_completo"] is False
    assert "analise_concorrentes" not in item and "Imposto" not in item


@pytest.mark.parametrize("other_oem", ["", "OEM-B"])
def test_textual_missing_values_and_legacy_aliases_survive_detail(store, monkeypatch, other_oem):
    def change(rows):
        rows[0]["itens"][0].pop("OEM")
        rows[0]["itens"][0]["OEM/ Model"] = "OEM-A"
        rows[1]["itens"][0]["OEM"] = other_oem
    edit(change)
    data = preview()
    assert len(data["conflitos"]) == (1 if other_oem else 0)
    choices = {}
    for conflict in data["conflitos"]:
        assert all(option["valor"] for option in conflict["opcoes"])
        choices[conflict["id"]] = next(option["id"] for option in conflict["opcoes"] if option["valor"] == other_oem)
    created = service._unir_sync(request(data, escolhas=choices), "tenant-a")["lista"]
    assert "OEM/ Model" not in created["itens"][0]
    assert created["itens"][0]["OEM"] == (other_oem or "OEM-A")
    assert reopen(created, store, monkeypatch)["itens"][0]["OEM"] == (other_oem or "OEM-A")


def test_financial_overflow_is_rejected_before_commit(store):
    edit(lambda rows: [row["itens"][0].update({"Quantidade": 1e200, "Valor unidade": 1e200,
                                              "Estimed Weigh": "", "M3 individual": "", "Estimed CBM": ""}) for row in rows])
    data = preview()
    before = Path(store("tenant-a")).read_bytes()
    with pytest.raises(HTTPException) as error:
        service._unir_sync(request(data), "tenant-a")
    assert error.value.status_code == 409
    assert Path(store("tenant-a")).read_bytes() == before


@pytest.mark.parametrize("field,value", [("store_id", "store-b"), ("fornecedor_id", "supplier-b"), ("currency", "BRL"),
                                           ("status", "Em produção"), ("status", "Em trânsito"), ("status", "Recebido"),
                                           ("status", "Pedido Aprovado"), ("status", "invalido"),
                                           ("unida_em_lista_id", "old"), ("packing_list", {"documento_id": "packing"}),
                                           ("actual_ship_date", "2026-10-29"), ("fornecedor_id", "")])
def test_incompatible_or_operational_lists_are_rejected(store, field, value):
    edit(lambda rows: rows[1].update({field: value}))
    before = Path(store("tenant-a")).read_bytes()
    with pytest.raises(HTTPException) as error:
        preview()
    assert error.value.status_code in {400, 409}
    assert Path(store("tenant-a")).read_bytes() == before


@pytest.mark.parametrize("changes", [{"compra_aprovada": True}, {"Compra aprovada": "sim"},
                                      {"Quantidade recebida": 1}, {"received_quantity": -1},
                                      {"Quantidade": "NaN"}, {"Valor unidade": "inf"},
                                      {"MOQ": -2}, {"package_multiple": "NaN"}, {"package_multiple": 0},
                                      {"Estimed Weigh": "Infinity"}])
def test_invalid_items_and_approved_purchases_are_rejected(store, changes):
    edit(lambda rows: rows[1]["itens"][0].update(changes))
    with pytest.raises(HTTPException) as error:
        preview()
    assert error.value.status_code == 409


def test_stale_preview_detects_content_changes_without_timestamp_change(store):
    data = preview()
    edit(lambda rows: rows[1]["itens"][0].update({"Quantidade": 9}))
    before = Path(store("tenant-a")).read_bytes()
    with pytest.raises(HTTPException) as error:
        service._unir_sync(request(data), "tenant-a")
    assert error.value.status_code == 409
    assert Path(store("tenant-a")).read_bytes() == before


def test_replay_returns_same_list_and_rejects_changed_operation(store):
    req = request(preview())
    first = service._unir_sync(req, "tenant-a")
    before = Path(store("tenant-a")).read_bytes()
    assert service._unir_sync(req, "tenant-a") == first
    assert Path(store("tenant-a")).read_bytes() == before
    with pytest.raises(HTTPException) as error:
        service._unir_sync(req.model_copy(update={"nome_lista": "Outra"}), "tenant-a")
    assert error.value.status_code == 409


def test_commit_failure_keeps_every_source_unchanged(store, monkeypatch):
    req = request(preview())
    before = Path(store("tenant-a")).read_bytes()
    monkeypatch.setattr(common.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("synthetic")))
    with pytest.raises(PermissionError):
        service._unir_sync(req, "tenant-a")
    assert Path(store("tenant-a")).read_bytes() == before


@pytest.mark.parametrize("source_changed", [True, False])
def test_race_preserves_concurrent_changes(store, monkeypatch, source_changed):
    req = request(preview())
    original_save = common._salvar_listas_pedidos
    calls = []
    def raced(tenant, rows):
        if not calls:
            calls.append(True)
            concurrent = common._carregar_listas_pedidos(tenant)
            concurrent[0 if source_changed else 2]["nome_lista"] = "Edição concorrente"
            original_save(tenant, concurrent)
        return original_save(tenant, rows)
    monkeypatch.setattr(common, "_salvar_listas_pedidos", raced)
    if source_changed:
        with pytest.raises(HTTPException) as error:
            service._unir_sync(req, "tenant-a")
        assert error.value.status_code == 409
        assert len(common._carregar_listas_pedidos("tenant-a")) == 3
    else:
        service._unir_sync(req, "tenant-a")
    rows = common._carregar_listas_pedidos("tenant-a")
    assert next(row for row in rows if row["id"] == ("a" if source_changed else "c"))["nome_lista"] == "Edição concorrente"


def test_simultaneous_same_operation_creates_once(store, monkeypatch):
    req = request(preview())
    barrier = threading.Barrier(2)
    original = common._salvar_listas_pedidos
    def synchronized(tenant, rows):
        barrier.wait(timeout=5)
        return original(tenant, rows)
    monkeypatch.setattr(common, "_salvar_listas_pedidos", synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service._unir_sync(req, "tenant-a"), range(2)))
    assert results[0] == results[1]
    assert len(common._carregar_listas_pedidos("tenant-a")) == 4


def test_tenant_isolation_missing_ids_and_closed_requests(store):
    req = request(preview())
    with pytest.raises(HTTPException) as error:
        service._unir_sync(req, "tenant-b")
    assert error.value.status_code == 404
    assert not Path(store("tenant-b")).exists()
    for ids in (["a"], ["a", "a"], ["a", " "]):
        with pytest.raises(ValidationError):
            Previa(lista_ids=ids)
    with pytest.raises(ValidationError):
        Previa(lista_ids=["a", "b"], client_id="tenant-b")


def test_previous_schemas_and_routes_preserved():
    from backend.routers.medias_compras import create_medias_compras_router
    baseline = json.loads((Path(__file__).parent / "fixtures/medias_uniao_baseline_contract.json").read_text())
    for name, expected in baseline["models"].items():
        actual = hashlib.sha256(json.dumps(getattr(schemas, name).model_json_schema(), sort_keys=True).encode()).hexdigest()
        assert actual == expected, name
    router = create_medias_compras_router()
    old = [(route.path, sorted(route.methods), route.name) for route in router.routes if "/uniao" not in route.path]
    assert hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest() == baseline["routes_sha256"]
    assert len(router.routes) == len(old) + 2


def test_http_preview_and_confirmation_use_tenant_dependency(store):
    from backend.routers.medias_compras import create_medias_compras_router
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "tenant-a"
    with TestClient(app) as client:
        result = client.post("/api/medias-compras/listas-pedidos/uniao/previa", json={"lista_ids": ["a", "b"]})
        assert result.status_code == 200, result.text
        req = request(result.json()["previa"])
        confirmation = client.post("/api/medias-compras/listas-pedidos/uniao", json=req.model_dump())
        assert confirmation.status_code == 200, confirmation.text
        assert confirmation.json()["lista"]["status"] == "Lista gerada"
