"""HTTP date round trips over the real tenant-scoped purchase-list JSON store."""

import copy
import json

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import cadastro_compatibilidade
from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as listas


@pytest.fixture
def order_date_api(monkeypatch, tmp_path):
    root = tmp_path / "info"
    paths = {}
    original = {
        "id": "purchase-a",
        "nome_lista": "Synthetic purchase A",
        "loja": "Synthetic Store A",
        "store_id": "store-a",
        "status": "Analisando orçamento",
        "created_at": "2026-09-01T10:00:00",
        "updated_at": "2026-09-01T10:00:00",
        "fornecedor_id": "supplier-a",
        "supplier": "Synthetic supplier A",
        "nacionalizador_id": "nationalizer-a",
        "nacionalizador": "Synthetic nationalizer A",
        "numero_invoice": "SYNTHETIC-INV-A",
        "currency": "USD",
        "incoterm": "FOB",
        "exchange_rate": 5.25,
        "promised_ship_date": "2026-11-01",
        "itens": [{
            "SKU": "TEST-001", "Quantidade": 7, "Valor unidade": 2.5,
            "Valor total": 17.5, "compra_aprovada": True,
            "compra_aprovada_em": "2026-09-02T11:00:00",
            "analise_concorrentes": {"custo_unitario": 2.5},
        }],
        "custom_metadata": {"preserve": ["synthetic", 123]},
    }
    other_list = {
        "id": "untouched-a", "nome_lista": "Untouched synthetic purchase",
        "order_date": "2026-08-03", "itens": [],
    }
    other_tenant = {
        **copy.deepcopy(original), "id": "purchase-b", "nome_lista": "Synthetic purchase B",
        "loja": "Synthetic Store B", "store_id": "store-b", "order_date": "2026-08-04",
    }
    for tenant, rows in (
        ("tenant-a", [original, other_list]), ("tenant-b", [other_tenant]),
    ):
        directory = root / tenant
        directory.mkdir(parents=True)
        paths[tenant] = directory / "listas_pedidos.json"
        paths[tenant].write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def tenant_path(tenant):
        assert tenant in paths, "The fixture must never resolve an operational tenant."
        return str(paths[tenant].parent)

    monkeypatch.setattr(common, "get_tenant_path", tenant_path)
    monkeypatch.setattr(listas, "get_tenant_path", tenant_path)
    # Product/fiscal enrichment is independent of an order-date-only mutation.
    # Keep real HTTP handlers, summaries, snapshots, atomic writes and caches.
    monkeypatch.setattr(listas, "_construir_mapa_m3_sku", lambda _tenant: {})
    monkeypatch.setattr(
        listas, "_recalcular_frete_internacional_itens_lista",
        lambda _tenant, items, **_kwargs: copy.deepcopy(items),
    )
    monkeypatch.setattr(
        cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja",
        lambda _tenant, store, **_kwargs: {
            "produtos": [], "store_id": store, "scope": "store", "loja_resolvida": True,
        },
    )

    def client(tenant="tenant-a"):
        app = FastAPI()
        app.include_router(create_medias_compras_router())

        def authenticate(x_test_tenant: str = Header()):
            if x_test_tenant not in paths:
                raise HTTPException(status_code=401, detail="Unknown synthetic tenant.")
            return x_test_tenant

        app.dependency_overrides[common.get_tenant_id] = authenticate
        return TestClient(app, headers={"X-Test-Tenant": tenant})

    return {"client": client, "paths": paths}


def _read(api, tenant="tenant-a"):
    return json.loads(api["paths"][tenant].read_text(encoding="utf-8"))


def _seed_date(api, value):
    rows = _read(api)
    if value is not None:
        rows[0]["order_date"] = value
    api["paths"]["tenant-a"].write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def _get_dates_after_reload(api, expected, tenant="tenant-a", list_id="purchase-a"):
    # A fresh app/client must reconstruct both representations from persisted JSON.
    with api["client"](tenant) as client:
        summary = client.get("/api/medias-compras/listas-pedidos")
        assert summary.status_code == 200, summary.text
        row = next(item for item in summary.json()["listas"] if item["id"] == list_id)
        assert row["order_date"] == expected
        detail = client.get("/api/medias-compras/listas-pedidos/" + list_id)
        assert detail.status_code == 200, detail.text
        assert detail.json()["lista"]["order_date"] == expected
        return detail.json()["lista"]


@pytest.mark.parametrize("initial, new_value", [
    (None, "2026-10-06"),
    ("2026-10-06", "2026-11-21"),
    ("2026-10-06", ""),
    ("2026-10-06", None),
], ids=["include", "change", "clear-empty", "clear-null"])
def test_order_date_put_persists_and_reloads_without_changing_other_data(order_date_api, initial, new_value):
    api = order_date_api
    before = copy.deepcopy(_seed_date(api, initial))
    other_tenant_bytes = api["paths"]["tenant-b"].read_bytes()
    expected = new_value or ""
    with api["client"]() as client:
        response = client.put("/api/medias-compras/listas-pedidos/purchase-a", json={"order_date": new_value})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert response.json()["lista"]["order_date"] == expected

    persisted = _read(api)
    assert persisted[0]["order_date"] == new_value
    assert persisted[0]["updated_at"] != before[0]["updated_at"]
    for key, value in before[0].items():
        if key not in {"order_date", "updated_at"}:
            assert persisted[0][key] == value, key
    assert persisted[1:] == before[1:]
    assert api["paths"]["tenant-b"].read_bytes() == other_tenant_bytes

    detail = _get_dates_after_reload(api, expected)
    assert detail["itens"] == before[0]["itens"]
    assert detail["fornecedor_id"] == before[0]["fornecedor_id"]
    assert detail["nacionalizador_id"] == before[0]["nacionalizador_id"]
    assert detail["store_id"] == before[0]["store_id"]
    assert _read(api)[0]["order_date"] == new_value


@pytest.mark.parametrize("initial", [None, "2026-10-06", "06/10/2026"], ids=["legacy-absent", "iso", "legacy-text"])
def test_omitting_order_date_keeps_its_existing_value(order_date_api, initial):
    api = order_date_api
    before = _seed_date(api, initial)
    with api["client"]() as client:
        response = client.put(
            "/api/medias-compras/listas-pedidos/purchase-a", json={"nome_lista": "Renamed synthetic purchase"},
        )
    assert response.status_code == 200, response.text
    after = _read(api)
    assert after[0].get("order_date") == before[0].get("order_date")
    assert ("order_date" in after[0]) == ("order_date" in before[0])
    assert after[0]["nome_lista"] == "Renamed synthetic purchase"
    assert after[0]["itens"] == before[0]["itens"]
    _get_dates_after_reload(api, initial or "")


def test_another_tenant_cannot_read_or_update_the_purchase(order_date_api):
    api = order_date_api
    before = {tenant: path.read_bytes() for tenant, path in api["paths"].items()}
    with api["client"]("tenant-b") as client:
        response = client.put(
            "/api/medias-compras/listas-pedidos/purchase-a", json={"order_date": "2026-12-24"},
        )
        detail = client.get("/api/medias-compras/listas-pedidos/purchase-a")
    assert response.status_code == detail.status_code == 404
    assert {tenant: path.read_bytes() for tenant, path in api["paths"].items()} == before


def test_matching_purchase_ids_are_isolated_by_authenticated_tenant(order_date_api):
    api = order_date_api
    rows = _read(api, "tenant-b")
    rows[0]["id"] = "purchase-a"
    api["paths"]["tenant-b"].write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tenant_a_before = api["paths"]["tenant-a"].read_bytes()
    with api["client"]("tenant-b") as client:
        response = client.put(
            "/api/medias-compras/listas-pedidos/purchase-a", json={"order_date": "2026-12-24"},
        )
    assert response.status_code == 200, response.text
    assert _read(api, "tenant-b")[0]["order_date"] == "2026-12-24"
    _get_dates_after_reload(api, "2026-12-24", tenant="tenant-b")
    assert api["paths"]["tenant-a"].read_bytes() == tenant_a_before
