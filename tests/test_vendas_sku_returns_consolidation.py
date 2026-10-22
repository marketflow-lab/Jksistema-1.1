from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.modules.vendas import notes
from backend.modules.vendas.compositions import CompositionCatalog
from backend.services import bling_vendas


SCHEMA = """
CREATE TABLE notas_entrada_itens (
    id_unico TEXT, store_id TEXT, id_nota TEXT, numero_nota TEXT,
    origem_codigo TEXT, data_emissao TEXT, sku TEXT, descricao TEXT,
    quantidade REAL, valor_unitario REAL, valor_total REAL, fornecedor TEXT,
    unidade_negocio_virtual TEXT, unidade_negocio TEXT, natureza_operacao TEXT,
    finalidade_operacao TEXT, loja_conta TEXT, devolucao INTEGER
);
"""


def returned(ident, **changes):
    row = dict(
        id_unico=ident, store_id="store-a", id_nota=ident or "source",
        numero_nota=ident or "NF-source", origem_codigo="",
        data_emissao="2026-10-15 12:00:00", sku="200", descricao="Produto",
        quantidade=1, valor_unitario=10, valor_total=10, fornecedor="",
        unidade_negocio_virtual="Canal A", unidade_negocio="Canal A",
        natureza_operacao="Devolucao", finalidade_operacao="",
        loja_conta="Loja A", devolucao=1,
    )
    row.update(changes)
    return row


@pytest.fixture
def sources(tmp_path, monkeypatch):
    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    catalog = CompositionCatalog.from_products(
        [
            {"store_id": "store-a", "sku": "200", "id_bling": 101, "produto": "Produto"},
            {
                "store_id": "store-a", "sku": "KIT-200", "id_bling": 102,
                "produto": "Kit", "formato_bling": "E",
                "componentes_bling_json": json.dumps([
                    {"produto": {"id": 101}, "quantidade": 3},
                ]),
            },
            {"store_id": "store-b", "sku": "200", "id_bling": 201, "produto": "Outro"},
        ],
        stores=[{"store_id": "store-a", "nome": "Loja A"},
                {"store_id": "store-b", "nome": "Loja B"}],
    )
    monkeypatch.setattr(bling_vendas, "get_tenant_path", lambda client_id: str(tmp_path / client_id))
    monkeypatch.setattr(notes, "_get_vendas_db_path", bling_vendas._get_vendas_db_path)
    monkeypatch.setattr(notes, "_listar_bancos_vendas_tenant", bling_vendas._listar_bancos_vendas_tenant)
    monkeypatch.setattr(notes, "_sql_filtro_loja_notas_entrada", bling_vendas._sql_filtro_loja_notas_entrada)
    monkeypatch.setattr(notes, "_sql_filtro_unidade_devolucao", bling_vendas._sql_filtro_unidade_devolucao)
    monkeypatch.setattr(notes, "_classificar_unidade_virtual_devolucao", bling_vendas._classificar_unidade_virtual_devolucao)
    monkeypatch.setattr(notes, "load_composition_catalog", lambda _client_id: catalog)

    def create(name, rows=(), *, client_id="tenant-a", has_table=True):
        folder = tmp_path / client_id
        folder.mkdir(exist_ok=True)
        path = folder / name
        with sqlite3.connect(path) as connection:
            if has_table:
                connection.executescript(SCHEMA)
                for row in rows:
                    columns = ", ".join(row)
                    placeholders = ", ".join("?" for _ in row)
                    connection.execute(
                        f"INSERT INTO notas_entrada_itens ({columns}) VALUES ({placeholders})",
                        list(row.values()),
                    )
        return path

    def read(**filters):
        return notes.listar_notas_entrada_por_sku("200", "tenant-a", **filters)

    return SimpleNamespace(create=create, read=read, tenant=tenant)


@pytest.mark.parametrize("store", [None, "__todas"])
def test_all_stores_reads_segregated_returns_with_empty_legacy_database(sources, store):
    sources.create("vendas_historico.db")
    sources.create("vendas_historico_loja_a.db", [returned("a", quantidade=2, valor_total=20)])
    sources.create("vendas_historico_loja_b.db", [returned(
        "b", loja_conta="Loja B", store_id="store-b", quantidade=3, valor_total=30,
        data_emissao="2026-10-20 08:00:00",
    )])
    sources.create("vendas_historico_sem_notas.db", has_table=False)
    sources.create("vendas_historico_loja_a.backup_old.db", [returned("backup", quantidade=99)])
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sources.tenant.glob("*.db")}

    rows = sources.read(loja=store, data_inicio="2026-10-01", data_fim="2026-10-31")

    assert [row["id_unico"] for row in rows] == ["b", "a"]
    assert sum(row["quantidade"] for row in rows) == 5
    assert sum(row["valor_total"] for row in rows) == 50
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources.tenant.glob("*.db")} == before


def test_mirrors_are_deduplicated_without_merging_distinct_store_identities(sources):
    first = returned("shared", quantidade=2, valor_total=20)
    anonymous = returned("", numero_nota="NF-anonymous")
    sources.create("vendas_historico.db", [first, anonymous])
    sources.create("vendas_historico_loja_a.db", [
        {**first, "store_id": "STORE-A", "descricao": "Descricao no espelho"},
        {**anonymous, "descricao": "Descricao complementar", "fornecedor": "Espelho"},
    ])
    sources.create("vendas_historico_loja_b.db", [returned(
        "shared", store_id="store-b", loja_conta="Loja B", quantidade=3, valor_total=30,
    )])

    rows = sources.read()

    assert len(rows) == 3
    assert {(row["store_id"], row["id_unico"]) for row in rows} == {
        ("store-a", "shared"), ("store-a", ""), ("store-b", "shared"),
    }
    assert sum(row["quantidade"] for row in rows) == 6
    assert sum(row["valor_total"] for row in rows) == 60


def test_account_unit_and_inclusive_dates_filter_every_database_and_tenant(sources):
    sources.create("vendas_historico.db", [returned(
        "before", data_emissao="2026-09-30 23:59:59",
    )])
    sources.create("vendas_historico_loja_a.db")
    sources.create("vendas_historico_antiga.db", [
        returned("start", data_emissao="2026-10-01 00:00:00"),
        returned("end", data_emissao="2026-10-31 23:59:59"),
        returned("after", data_emissao="2026-11-01 00:00:00"),
        returned("other-unit", unidade_negocio_virtual="Canal B", unidade_negocio="Canal B"),
        returned("other-sku", sku="201"),
        returned("not-return", devolucao=0),
    ])
    sources.create("vendas_historico_loja_b.db", [returned(
        "other-account", loja_conta="Loja B", store_id="store-b",
    )])
    sources.create("vendas_historico_loja_a.db", [returned("other-tenant")], client_id="tenant-b")

    rows = sources.read(loja="Loja A", unidade_negocio="Canal A",
                        data_inicio="2026-10-01", data_fim="2026-10-31")

    assert [row["id_unico"] for row in rows] == ["end", "start"]
    assert sum(row["quantidade"] for row in rows) == 2
    assert sources.read(loja="Loja inexistente") == []


def test_composition_is_projected_after_deduplicating_source_returns(sources):
    direct = returned("direct")
    kit = returned("kit", sku="KIT-200", quantidade=2, valor_total=60, valor_unitario=30)
    sources.create("vendas_historico.db", [direct, kit])
    sources.create("vendas_historico_loja_a.db", [direct, kit])

    rows = sources.read(loja="Loja A")

    assert len(rows) == 2
    assert sum(row["quantidade"] for row in rows) == 7
    assert sum(row["valor_total"] for row in rows) == 10
    assert sum(row.get("devolucoes_contribuicao", 1) for row in rows) == 1
    projected = next(row for row in rows if row.get("__composicao"))
    assert projected["sku_original"] == "KIT-200"
    assert projected["quantidade"] == 6
    assert projected["fator_composicao"] == 3
    assert projected["valor_total"] == 0
