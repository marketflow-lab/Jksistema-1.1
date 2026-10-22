from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import dependencies, notes, performance, repository
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter
from backend.modules.vendas.compositions import CompositionCatalog


def _catalog(multiplier=2, base_sku="479"):
    products = [{"store_id": "store-a", "sku": base_sku, "id_bling": 101, "produto": "Base"}]
    for sku, ident, factor in [("479-K", 102, multiplier), ("479-K3", 103, 3), ("479-K5", 105, 5)]:
        products.append({"store_id": "store-a", "sku": sku, "id_bling": ident,
                         "produto": sku, "formato_bling": "E",
                         "componentes_bling_json": json.dumps([{"produto": {"id": 101}, "quantidade": factor}])})
    return CompositionCatalog.from_products(products, stores=[{"store_id": "store-a", "nome": "Loja A"}])


@pytest.fixture
def sales(tmp_path, monkeypatch):
    logger = logging.getLogger("composition-summary-test")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=logger, legacy=LegacyBlingVendasAdapter()))
    path = tmp_path / "vendas.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE vendas (
                id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, store_id TEXT,
                sku TEXT, produto TEXT, quantidade REAL, valor REAL,
                devolucao INTEGER DEFAULT 0, canal TEXT, numero TEXT, situacao TEXT,
                numero_nf TEXT, nota_fiscal_id TEXT, comprador TEXT, unidade_negocio TEXT,
                loja_id TEXT, unidade_id TEXT, intermediador_nome TEXT, intermediador_cnpj TEXT
            );
            CREATE TABLE notas_entrada_itens (
                id_unico TEXT PRIMARY KEY, data_emissao TEXT, loja_conta TEXT, store_id TEXT,
                sku TEXT, descricao TEXT, quantidade REAL, valor_total REAL,
                devolucao INTEGER, numero_nota TEXT, origem_codigo TEXT,
                valor_unitario REAL, fornecedor TEXT, unidade_negocio TEXT,
                unidade_negocio_virtual TEXT, natureza_operacao TEXT, finalidade_operacao TEXT
            );
        """)
        for sku, count, quantity, total in [("479", 10, 2, "18372.77"), ("479-K", 9, 1, "16054.93"),
                                             ("479-K3", 2, 1, "5652.30"), ("479-K5", 3, 1, "16940.94")]:
            amount = (Decimal(total) / count).quantize(Decimal("0.01"))
            for index in range(count):
                value = amount if index < count - 1 else Decimal(total) - amount * (count - 1)
                db.execute("INSERT INTO vendas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    sku + ":" + str(index), "2026-10-01", "Loja A", "store-a", sku, "Base" if sku == "479" else sku,
                    quantity, float(value), 0, "Loja virtual", sku + ":" + str(index), "Faturado", "", "", "", "Canal A", "", "", "", ""))
    holder = SimpleNamespace(catalog=_catalog(), paths=[str(path)])
    monkeypatch.setattr(repository, "load_composition_catalog", lambda *_: holder.catalog)
    monkeypatch.setattr(notes, "load_composition_catalog", lambda *_: holder.catalog)
    monkeypatch.setattr(repository, "_listar_bancos_vendas_tenant", lambda *_: holder.paths)
    monkeypatch.setattr(notes, "_listar_bancos_vendas_tenant", lambda *_: holder.paths)
    monkeypatch.setattr(performance, "get_tenant_path", lambda *_: str(tmp_path))
    monkeypatch.setattr(repository, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *_: {})
    monkeypatch.setattr(repository, "_resolver_nome_loja_virtual", lambda *_args, **_kw: "Canal A")
    monkeypatch.setattr(repository, "_sql_filtro_loja_vendas", lambda store: (" AND loja_conta = ?", [store]))
    monkeypatch.setattr(repository, "_sql_filtro_unidade_com_mapa", lambda unit, *_: (" AND unidade_negocio = ?", [unit]))
    monkeypatch.setattr(repository, "_sql_filtro_loja_notas_entrada", lambda cur, store: (" AND loja_conta = ?", [store]) if store else ("", []))
    performance.invalidate_vendas_cache()
    yield path, holder
    performance.invalidate_vendas_cache()


def _summary(**kwargs):
    return repository.resumo_vendas("fixture", data_inicio="2026-10-01", data_fim="2026-10-31", **kwargs)


def test_screenshot_units_and_money_reconcile_without_changing_source_database(sales):
    path, _ = sales
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = _summary()
    rows = {row["sku"]: row for row in payload["rows"]}
    assert rows["479"]["itens"] == 59
    assert (rows["479"]["itensDiretos"], rows["479"]["itensComposicao"]) == (20, 39)
    assert (rows["479"]["pedidos"], rows["479"]["valor"]) == (10, 18372.77)
    assert [rows[key]["itens"] for key in ("479-K", "479-K3", "479-K5")] == [9, 2, 3]
    assert payload["totals"]["itens"] == sum(row["itensComponentes"] for row in rows.values()) == 59
    assert payload["totals"]["pedidos"] == sum(row["pedidos"] for row in rows.values()) == 24
    assert payload["totals"]["valor"] == round(sum(row["valor"] for row in rows.values()), 2) == 57020.94
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original


def test_component_filter_and_detail_include_kits_without_fictitious_orders_or_money(sales):
    payload = _summary(sku="479", loja="Loja A")
    assert len(payload["rows"]) == 1
    assert payload["totals"]["itens"] == payload["rows"][0]["itens"] == 59
    assert payload["totals"]["pedidos"] == 10
    assert payload["totals"]["valor"] == 18372.77
    details = repository.listar_vendas("fixture", sku="479", loja="Loja A")
    assert sum(row["quantidade"] for row in details) == 59
    assert sum(row.get("pedidos_contribuicao", 1) for row in details) == 10
    assert round(sum(row["valor"] for row in details), 2) == 18372.77
    kits = repository.listar_vendas("fixture", sku="479-K", loja="Loja A")
    assert len(kits) == 9 and sum(row["quantidade"] for row in kits) == 9
    raw = repository.listar_vendas("fixture")
    assert len(raw) == 24 and sum(row["quantidade"] for row in raw) == 34
    assert not any(row.get("__composicao") for row in raw)


def test_filters_and_existing_dedup_are_applied_before_composition(sales):
    _, holder = sales
    holder.paths *= 2
    assert _summary(loja="Loja A", unidade_negocio="Canal A")["totals"]["itens"] == 59
    assert _summary(loja="Outra Loja")["rows"] == []
    assert _summary(unidade_negocio="Canal B")["rows"] == []
    assert repository.resumo_vendas("fixture", data_inicio="2026-11-01")["rows"] == []


def test_return_compositions_reconcile_summary_and_sku_detail(sales):
    path, _ = sales
    with sqlite3.connect(path) as db:
        for ident, sku, quantity, value in [("base-return", "479", 1, 100), ("kit-return", "479-K3", 2, 600)]:
            db.execute("INSERT INTO notas_entrada_itens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                ident, "2026-10-02", "Loja A", "store-a", sku, sku, quantity, value, 1, ident, ident,
                value / quantity, "", "Canal A", "", "Devolucao", ""))
    payload = _summary()
    row = next(row for row in payload["rows"] if row["sku"] == "479")
    assert row["itensDevolucao"] == 7
    assert row["itensDevolucaoDiretos"] == 1 and row["itensDevolucaoComposicao"] == 6
    assert (row["devolucoes"], row["valorDevolucao"]) == (1, 100)
    assert payload["totals"]["itensDevolucao"] == 7
    assert payload["totals"]["devolucoes"] == 2
    assert payload["totals"]["valorDevolucao"] == 700
    assert payload["totals"]["itensLiquidos"] == 52
    details = notes.listar_notas_entrada_por_sku("479", "fixture", loja="Loja A")
    assert sum(row["quantidade"] for row in details) == 7
    assert sum(row.get("devolucoes_contribuicao", 1) for row in details) == 1
    assert sum(row["valor_total"] for row in details) == 100


def test_composition_edit_invalidates_summary_cache(sales, tmp_path, monkeypatch):
    from backend.modules.vendas import compositions
    marker = tmp_path / "cadastro_produtos_lojas.csv"
    marker.write_text("two", encoding="utf-8")
    monkeypatch.setattr(compositions, "composition_source_paths", lambda *_: [str(marker)])
    _, holder = sales
    assert _summary()["totals"]["itens"] == 59
    holder.catalog = _catalog(4)
    # Same source signature still serves the prior coherent snapshot.
    assert _summary()["totals"]["itens"] == 59
    marker.write_text("four units", encoding="utf-8")
    assert _summary()["totals"]["itens"] == 77


def test_fractional_component_factors_are_not_rounded_like_currency(sales):
    _, holder = sales
    holder.catalog = _catalog(0.125)
    payload = _summary()
    row = next(row for row in payload["rows"] if row["sku"] == "479")
    assert row["itens"] == 42.125
    assert payload["totals"]["itens"] == 42.125
    assert payload["totals"]["valor"] == 57020.94


def test_canonical_and_sold_sku_case_merge_without_changing_display_or_identity(sales):
    path, holder = sales
    holder.catalog = _catalog(base_sku="BASE")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE vendas SET sku = 'base' WHERE sku = '479'")
    payload = _summary()
    bases = [row for row in payload["rows"] if row["sku"].casefold() == "base"]
    assert len(bases) == 1
    assert (bases[0]["sku"], bases[0]["itens"], bases[0]["pedidos"], bases[0]["valor"]) == ("base", 59, 10, 18372.77)
    assert _summary(sku="BASE")["totals"]["itens"] == 59


def test_nf_resolution_keeps_distinct_document_ids_until_after_composition(sales, monkeypatch):
    path, _ = sales
    with sqlite3.connect(path) as db:
        template = list(db.execute("SELECT * FROM vendas WHERE sku = '479-K' LIMIT 1").fetchone())
        db.execute("DELETE FROM vendas")
        for ident in ["doc-1", "doc-2"]:
            item = list(template)
            item[0] = ident
            item[10] = ""
            db.execute("INSERT INTO vendas VALUES (" + ",".join("?" for _ in item) + ")", item)
    monkeypatch.setattr(repository, "_bling_obter_numero_nf", lambda *_args, **_kw: pytest.fail("no new provider call"))
    normal = repository.listar_vendas("fixture", sku="479", loja="Loja A")
    resolved = repository.listar_vendas("fixture", sku="479", loja="Loja A", resolver_nf=True)
    assert len(normal) == len(resolved) == 2
    assert sum(row["quantidade"] for row in resolved) == 4
    assert {row["id_unico_original"] for row in resolved} == {"doc-1", "doc-2"}
    assert all("id_unico" not in row for row in resolved)
