from __future__ import annotations

import csv
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.vendas import analytics, performance
from backend.modules.vendas.router import create_vendas_router
from backend.services.bling_vendas import _sql_filtro_loja_vendas


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 13, 12, 0, 0)
        return value if tz is None else value.astimezone(tz)


def _write_products(path: Path, rows: list[dict]) -> None:
    columns = ["sku", "nome_bling", "saldo_loja", "loja_sync", "store_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, columns)
        writer.writeheader()
        writer.writerows(rows)


def _stock(sku, quantity, store_id="", loja=""):
    return {"sku": sku, "nome_bling": "Produto " + sku, "saldo_loja": quantity,
            "store_id": store_id, "loja_sync": loja}


@pytest.fixture
def scoped_endpoint(tmp_path, monkeypatch):
    performance.invalidate_vendas_cache()
    stores = [
        {"store_id": "0001", "nome": "Loja A", "nomes_anteriores": ["Loja Antiga A", "Compartilhada"]},
        {"store_id": "store-b", "nome": "Loja B", "nomes_anteriores": ["Compartilhada"]},
    ]
    for client in ("tenant-a", "tenant-b"):
        tenant = tmp_path / client
        tenant.mkdir()
        (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
        db = sqlite3.connect(tenant / "vendas.db")
        db.executescript("""
            CREATE TABLE vendas (data TEXT, devolucao INTEGER, sku TEXT, loja_conta TEXT);
            INSERT INTO vendas VALUES
                ('2026-01-01T08:00:00', 0, 'COMUM', 'Loja A'),
                ('2026-10-12T08:00:00', 0, 'COMUM', 'Loja B'),
                ('2026-10-12T08:00:00', 1, 'EXCLUSIVO-A', 'Loja A');
        """)
        db.commit()
        db.close()
    tenant_path = lambda client: str(tmp_path / client)
    monkeypatch.setattr(analytics, "get_tenant_path", tenant_path)
    monkeypatch.setattr(performance, "get_tenant_path", tenant_path)
    monkeypatch.setattr(analytics, "datetime", _FixedDatetime)
    monkeypatch.setattr(analytics, "logger", logging.getLogger("test-stock-scope"))
    monkeypatch.setattr(analytics, "_sql_filtro_loja_vendas", _sql_filtro_loja_vendas)
    monkeypatch.setattr(analytics, "_listar_bancos_vendas_tenant",
                        lambda client, _loja: [str(tmp_path / client / "vendas.db")])
    monkeypatch.setattr(analytics, "_migrar_arquivo_legado_para_tenant",
                        lambda client, filename, _legacy: str(tmp_path / client / filename))
    monkeypatch.setattr(analytics, "get_vendas_dependencies", lambda: SimpleNamespace(
        paths=SimpleNamespace(legacy_products_csv=str(tmp_path / "produtos_compilado.csv")),
    ))
    clients = {}
    for client in ("tenant-a", "tenant-b"):
        app = FastAPI()
        app.include_router(create_vendas_router(
            SimpleNamespace(skus_sem_venda=analytics.skus_sem_venda), object(),
            SimpleNamespace(get_tenant_id=lambda value=client: value),
        ))
        clients[client] = TestClient(app)

    def request(client="tenant-a", loja="Loja A"):
        params = {} if loja is None else {"loja": loja}
        response = clients[client].get("/api/vendas/skus-sem-venda", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    yield tmp_path, stores, request
    performance.invalidate_vendas_cache()
    for client in clients.values():
        client.close()


def _items(result):
    return {item["sku"]: item for key, group in result.items()
            if key.startswith("ultimos_") for item in group}


def test_real_endpoint_isolates_store_stock_and_last_sale(scoped_endpoint):
    root, _stores, request = scoped_endpoint
    _write_products(root / "tenant-a" / "produtos_compilado.csv", [
        _stock("EXCLUSIVO-A", 3, "0001", "Loja A"),
        _stock("COMUM", 5, "0001", "Loja A"),
        _stock("EXCLUSIVO-B", 9, "store-b", "Loja B"),
        _stock("COMUM", 50, "store-b", "Loja B"),
        _stock("ZERO", 0, "0001", "Loja A"),
    ])
    result_a = request(loja="Loja A")
    result_b = request(loja="Loja B")
    assert result_a["total_skus_com_estoque"] == 2
    assert set(_items(result_a)) == {"EXCLUSIVO-A", "COMUM"}
    assert _items(result_a)["COMUM"]["estoque"] == 5
    assert _items(result_a)["COMUM"]["ultima_venda"] == "2026-01-01"
    assert result_b["total_skus_com_estoque"] == 2
    assert set(_items(result_b)) == {"EXCLUSIVO-B"}
    assert _items(result_b)["EXCLUSIVO-B"]["estoque"] == 9


def test_ids_override_names_and_only_unique_legacy_aliases_are_accepted(scoped_endpoint):
    root, _stores, request = scoped_endpoint
    _write_products(root / "tenant-a" / "produtos_compilado.csv", [
        _stock("ID-A", 2, "0001", "Nome sem relacao"),
        _stock("NOME-CONFLITANTE", 99, "store-b", "Loja A"),
        _stock("ID-DESCONHECIDO", 99, "orphan", "Loja A"),
        _stock("LEGADO", 4, "", "  lOjA aNtIgA a  "),
        _stock("LEGADO-ATUAL", 6, "", "loja a"),
        _stock("AMBIGUO", 99, "", "Compartilhada"),
        _stock("SEM-METADADOS", 99),
    ])
    result = request()
    assert set(_items(result)) == {"ID-A", "LEGADO", "LEGADO-ATUAL"}
    assert set(_items(request(loja="Loja Antiga A"))) == set(_items(result))
    assert not _items(request(loja="Compartilhada"))
    assert not _items(request(loja="Loja inexistente"))


def test_global_view_adds_balances_across_stores_without_counting_duplicates(scoped_endpoint):
    root, _stores, request = scoped_endpoint
    _write_products(root / "tenant-a" / "produtos_compilado.csv", [
        _stock("SALDO", 5, "0001", "Loja A"),
        _stock("SALDO", 50, "store-b", "Loja B"),
        _stock("SALDO", 7, "0001", "Loja A"),
        _stock("LEGADO", 3),
    ])
    for loja in (None, "__todas"):
        result = request(loja=loja)
        assert result["total_skus_com_estoque"] == 2
        assert _items(result)["SALDO"]["estoque"] == 57
        assert _items(result)["LEGADO"]["estoque"] == 3
    assert _items(request())["SALDO"]["estoque"] == 7


def test_cache_does_not_reuse_another_tenant_projection_even_for_shared_csv(scoped_endpoint, monkeypatch):
    root, _stores, request = scoped_endpoint
    shared = root / "shared.csv"
    _write_products(shared, [
        _stock("TENANT-A", 3, "0001", "Loja A"),
        _stock("TENANT-B", 8, "0002", "Loja A"),
    ])
    (root / "tenant-b" / "lojas_config.json").write_text(json.dumps([
        {"store_id": "0002", "nome": "Loja A"},
    ]), encoding="utf-8")
    monkeypatch.setattr(analytics, "_migrar_arquivo_legado_para_tenant", lambda *_args: str(shared))
    assert set(_items(request(client="tenant-a"))) == {"TENANT-A"}
    assert set(_items(request(client="tenant-b"))) == {"TENANT-B"}
    assert set(_items(request(client="tenant-a"))) == {"TENANT-A"}


def test_cache_revalidates_store_configuration_and_legacy_aliases(scoped_endpoint):
    root, stores, request = scoped_endpoint
    _write_products(root / "tenant-a" / "produtos_compilado.csv", [
        _stock("ORIGINAL", 3, "0001", "Loja A"),
        _stock("NOVA-CONTA", 8, "0002", "Loja A"),
        _stock("LEGADO", 4, "", "Loja Antiga A"),
    ])
    assert set(_items(request())) == {"ORIGINAL", "LEGADO"}
    stores[0]["store_id"] = "0002"
    stores[0]["nomes_anteriores"] = []
    (root / "tenant-a" / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
    assert set(_items(request())) == {"NOVA-CONTA"}


def test_missing_configuration_and_metadata_fail_closed_only_for_specific_store(scoped_endpoint):
    root, _stores, request = scoped_endpoint
    _write_products(root / "tenant-a" / "produtos_compilado.csv", [
        _stock("GLOBAL", 5),
        _stock("ROTULO", 2, "", "Loja A"),
    ])
    (root / "tenant-a" / "lojas_config.json").unlink()
    assert not _items(request())
    assert set(_items(request(loja="__todas"))) == {"GLOBAL", "ROTULO"}
