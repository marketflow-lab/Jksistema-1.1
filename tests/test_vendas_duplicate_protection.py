"""Regression coverage for the historical short sale IDs and document item IDs."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import analytics, dependencies, performance, reports, repository, sync_period
from backend.modules.vendas.compositions import CompositionCatalog
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter
from backend.schemas import VendasSyncRequest
from backend.services import bling_vendas


DAY = "2026-04-12"
TENANT = "duplicate-fixture"
PRICE = 369.16


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    logger = logging.getLogger("vendas-duplicate-protection-test")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=logger, legacy=LegacyBlingVendasAdapter()))
    monkeypatch.setattr(sync_period, "_verificar_cancelamento", lambda *_: None)
    monkeypatch.setattr(performance, "get_tenant_path", lambda *_: str(tmp_path))
    performance.invalidate_vendas_cache()
    yield
    performance.invalidate_vendas_cache()


def _request(store_id=""):
    return VendasSyncRequest(loja="Loja A", store_id=store_id or None,
                             data_inicio=DAY, data_fim=DAY, forcar_resync=False)


def _sales(req, count=1, invoice="NF-10"):
    rows = [{"data": DAY, "numero": "12210", "sku": "200", "produto": "Produto teste",
             "canal": "Marketplace", "quantidade": 1, "valor": PRICE, "situacao": "Faturado",
             "comprador": "Comprador teste", "unidade_negocio": "Canal A",
             "id_pedido": "10", "origem_tipo": "pedido", "origem_id": "10",
             "item_id": str(index), "nota_fiscal_id": invoice, "numero_nf": "012152"}
            for index in range(1, count + 1)]
    return sync_period._preparar_payload_vendas(rows, req, TENANT)


def _legacy(req, invoice="NF-10"):
    row = list(_sales(req, invoice=invoice)[0][:20])
    # This short key predates both the quantity/value and document/item formats.
    row[0] = f"{req.loja}_12210_200"
    return tuple(row)


def _persist(path, req, rows=()):
    return sync_period._persistir_periodo_atomico(str(path), req, list(rows), [], [], TENANT)


def _snapshot(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT * FROM vendas ORDER BY id_unico").fetchall()


@pytest.mark.parametrize("store_id", ["", "store-a"])
@pytest.mark.parametrize("item_count", [1, 2])
def test_short_legacy_replacement_requires_account_proof_and_replay_is_stable(tmp_path, store_id, item_count):
    path = tmp_path / "sales.db"
    legacy_req = _request()
    _persist(path, legacy_req, [_legacy(legacy_req)])
    req = _request(store_id)
    typed = _sales(req, count=item_count)
    expected_count = item_count if store_id else item_count + 1
    expected_ids = {row[0] for row in typed}
    if not store_id:
        # A display name without a stable account ID cannot prove ownership.
        expected_ids.add(_legacy(legacy_req)[0])

    for _ in range(3):
        _persist(path, req, typed)
        with sqlite3.connect(path) as connection:
            count, quantity, revenue = connection.execute(
                "SELECT count(*),sum(quantidade),sum(valor) FROM vendas").fetchone()
            assert (count, quantity) == (expected_count, expected_count)
            assert revenue == pytest.approx(expected_count * PRICE)
            assert {row[0] for row in connection.execute("SELECT id_unico FROM vendas")} == expected_ids


def test_two_identical_items_in_one_document_remain_distinct(tmp_path):
    path = tmp_path / "sales.db"
    req = _request("store-a")
    typed = _sales(req, count=2)
    _persist(path, req, typed)
    _persist(path, req, typed)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT item_id,quantidade FROM vendas ORDER BY item_id").fetchall() == [("1", 1), ("2", 1)]
        connection.row_factory = sqlite3.Row
        raw = [dict(row) for row in connection.execute("SELECT * FROM vendas")]
    assert len(bling_vendas._deduplicar_vendas_consolidadas(raw)) == 2


def test_matching_number_with_conflicting_invoice_is_preserved(tmp_path):
    path = tmp_path / "sales.db"
    old_req, req = _request(), _request("store-a")
    old = _legacy(old_req, invoice="NF-DIFFERENT")
    _persist(path, old_req, [old])
    _persist(path, req, _sales(req))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*),sum(quantidade) FROM vendas").fetchone() == (2, 2)
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM vendas")]
    assert len(bling_vendas._deduplicar_vendas_consolidadas(rows)) == 2


@pytest.mark.parametrize("legacy_store", ["", "store-b"])
def test_homonymous_accounts_preserve_other_or_unattributed_legacy_sale(tmp_path, legacy_store):
    path = tmp_path / "sales.db"
    old_req = _request(legacy_store)
    old = _legacy(old_req)
    if not legacy_store:
        # A second known account makes the old display-name-only row ambiguous.
        _persist(path, _request("store-b"), _sales(_request("store-b")))
        with sqlite3.connect(path) as connection:
            connection.execute(sync_period._insert_com_store_id(sync_period._SQL_INSERT_VENDAS), (*old, ""))
    else:
        _persist(path, old_req, [old])
    _persist(path, _request("store-a"), _sales(_request("store-a")))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT store_id FROM vendas WHERE id_unico=?", (old[0],)).fetchone() == (legacy_store,)
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM vendas")]
    assert len(bling_vendas._deduplicar_vendas_consolidadas(rows)) == len(rows)


def test_failure_after_legacy_cleanup_rolls_back_the_whole_sale_refresh(tmp_path):
    path = tmp_path / "sales.db"
    old_req, req = _request(), _request("store-a")
    _persist(path, old_req, [_legacy(old_req)])
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_typed_sale BEFORE INSERT ON vendas
            WHEN NEW.origem_tipo = 'pedido'
            BEGIN SELECT RAISE(ABORT, 'fixture failed after legacy replacement'); END;
        """)
    before = _snapshot(path)
    with pytest.raises(Exception, match="fixture failed after legacy replacement"):
        _persist(path, req, _sales(req))
    assert _snapshot(path) == before


@pytest.fixture
def mixed_source(tmp_path, monkeypatch):
    path = tmp_path / "sales.db"
    old_req, req = _request(), _request("store-a")
    _persist(path, old_req, [_legacy(old_req)])
    # Reproduce the already persisted migration problem without invoking cleanup.
    with sqlite3.connect(path) as connection:
        connection.executemany(
            sync_period._insert_com_store_id(sync_period._SQL_INSERT_VENDAS_IDENTIFICADAS),
            [(*row, "store-a") for row in _sales(req, count=2)],
        )
    catalog = CompositionCatalog.from_products([], stores=[])
    for module in (repository, analytics, reports):
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: [str(path)])
        monkeypatch.setattr(module, "_deve_excluir_venda_ebazar", lambda *_: False)
    for module in (repository, analytics):
        monkeypatch.setattr(module, "load_composition_catalog", lambda *_: catalog)
        monkeypatch.setattr(module, "_sql_filtro_loja_vendas", lambda store: (" AND loja_conta = ?", [store]))
        monkeypatch.setattr(module, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *_: {})
        monkeypatch.setattr(module, "_sql_filtro_loja_notas_entrada", lambda *_: ("", []))
        monkeypatch.setattr(module, "_sql_filtro_unidade_devolucao", lambda *_: ("", []))
    monkeypatch.setattr(repository, "_resolver_nome_loja_virtual", lambda *_args, **_kwargs: "Canal A")
    monkeypatch.setattr(repository, "_bling_obter_numero_nf", lambda *_: pytest.fail("NF already available"))
    monkeypatch.setattr(analytics, "_filtrar_vendas_lojas_ativas", lambda rows, *_: rows)
    monkeypatch.setattr(analytics, "_vendas_series_estoque_historico", lambda **_: {
        "estoque_geral": [], "estoque_sku": [], "estoque_skus_com_saldo": [],
        "estoque_skus_pareto_com_saldo": [], "estoque_meta": {},
    })
    return path


@pytest.mark.parametrize("resolve_invoice", [False, True])
@pytest.mark.parametrize("sku", [None, "200"])
def test_existing_migration_duplicates_are_hidden_in_both_detail_paths(mixed_source, resolve_invoice, sku):
    before = hashlib.sha256(mixed_source.read_bytes()).digest()
    rows = repository.listar_vendas(TENANT, data_inicio=DAY, data_fim=DAY,
                                   loja="Loja A", sku=sku, resolver_nf=resolve_invoice)
    assert len(rows) == 2
    assert sum(row["quantidade"] for row in rows) == 2
    assert sum(row["valor"] for row in rows) == pytest.approx(PRICE * 2)
    assert hashlib.sha256(mixed_source.read_bytes()).digest() == before


def test_summary_graph_and_reports_agree_on_existing_migration_duplicates(mixed_source):
    before = hashlib.sha256(mixed_source.read_bytes()).digest()
    summary = repository.resumo_vendas.__wrapped__(TENANT, data_inicio=DAY, data_fim=DAY, loja="Loja A")
    graph = analytics.grafico_vendas.__wrapped__(client_id=TENANT, data_inicio=DAY, data_fim=DAY, loja="Loja A")
    raw, processed, errors = reports._read_sales_rows(TENANT, "Loja A", DAY, DAY)
    product_rows, fiscal_rows, audit = reports.prepare_eligible_sales_rows(raw)

    assert errors == [] and len(processed) == 1
    assert summary["totals"]["itens"] == sum(graph["quantidades_vendas"]) == sum(row["quantidade"] for row in product_rows) == 2
    assert summary["totals"]["valor"] == pytest.approx(PRICE * 2)
    assert sum(graph["valores_vendas"]) == pytest.approx(PRICE * 2)
    assert sum(row["valor"] for row in product_rows) == pytest.approx(PRICE * 2)
    assert len(product_rows) == audit["linhas_elegiveis"] == 2
    assert fiscal_rows == []
    assert hashlib.sha256(mixed_source.read_bytes()).digest() == before


def _reader_quantities():
    filters = {"data_inicio": DAY, "data_fim": DAY, "loja": "Loja A", "sku": "200"}
    details = [repository.listar_vendas(TENANT, **filters, resolver_nf=resolve) for resolve in (False, True)]
    summary = repository.resumo_vendas.__wrapped__(TENANT, **filters)
    graph = analytics.grafico_vendas.__wrapped__(client_id=TENANT, **filters)
    raw, _processed, errors = reports._read_sales_rows(TENANT, "Loja A", DAY, DAY)
    product_rows, _fiscal_rows, _audit = reports.prepare_eligible_sales_rows(raw)
    assert errors == []
    return {
        "listagem": sum(row["quantidade"] for row in details[0]),
        "listagem_resolver_nf": sum(row["quantidade"] for row in details[1]),
        "resumo": summary["totals"]["itens"],
        "grafico": sum(graph["quantidades_vendas"]),
        "relatorio": sum(row["quantidade"] for row in product_rows),
    }


def _add_outside_account(connection, table):
    date_column = "data" if table == "vendas" else "data_emissao"
    columns = "id_unico,loja_conta,store_id," + date_column
    values = ["outside-scope", "Loja A", "store-b", "2026-04-11"]
    if table != "notas_entrada":
        columns += ",sku"
        values.append("OUTSIDE-SKU")
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", values,
    )


@pytest.mark.parametrize("table", ["vendas", "notas_entrada", "notas_entrada_itens"])
@pytest.mark.parametrize("separate_database", [False, True])
def test_account_conflicts_outside_sku_and_date_filters_keep_legacy_in_every_reader(
    mixed_source, tmp_path, monkeypatch, table, separate_database,
):
    evidence_path = mixed_source
    paths = [str(mixed_source)]
    if separate_database:
        evidence_path = tmp_path / "outside-scope.db"
        _persist(evidence_path, _request())
        paths.append(str(evidence_path))
        for module in (repository, analytics, reports):
            monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: paths)
    with sqlite3.connect(evidence_path) as connection:
        _add_outside_account(connection, table)
    before = {path: hashlib.sha256(Path(path).read_bytes()).digest() for path in paths}

    quantities = _reader_quantities()

    assert quantities == {reader: 3 for reader in quantities}
    assert {path: hashlib.sha256(Path(path).read_bytes()).digest() for path in paths} == before


def test_failure_while_scanning_another_database_keeps_legacy_in_every_reader(mixed_source, tmp_path, monkeypatch):
    second = tmp_path / "unreadable-accounts.db"
    _persist(second, _request())
    paths = [str(mixed_source), str(second)]
    for module in (repository, analytics, reports):
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: paths)
    readonly = performance.open_vendas_readonly
    failed_reads = []

    def unavailable_account_scan(path, **kwargs):
        if str(path) == str(second):
            failed_reads.append(path)
            raise sqlite3.OperationalError("fixture account scan unavailable")
        return readonly(path, **kwargs)

    # Readers retain their own working handles; only the safety scan fails.
    monkeypatch.setattr(performance, "open_vendas_readonly", unavailable_account_scan)
    quantities = _reader_quantities()
    assert failed_reads
    assert quantities == {reader: 3 for reader in quantities}


@pytest.mark.parametrize("table", ["vendas", "notas_entrada", "notas_entrada_itens"])
def test_persistence_preserves_legacy_when_other_historical_database_proves_homonymous_account(tmp_path, table):
    path = tmp_path / "vendas_historico_loja_a.db"
    other = tmp_path / "vendas_historico_loja_b.db"
    old_req, req = _request(), _request("store-a")
    _persist(path, old_req, [_legacy(old_req)])
    _persist(other, _request("store-b"))
    with sqlite3.connect(other) as connection:
        _add_outside_account(connection, table)
    other_before = hashlib.sha256(other.read_bytes()).digest()

    for _ in range(2):
        _persist(path, req, _sales(req))
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT store_id FROM vendas WHERE id_unico=?", (_legacy(old_req)[0],)).fetchone() == ("",)
            assert connection.execute("SELECT count(*),sum(quantidade) FROM vendas").fetchone() == (2, 2)
    assert hashlib.sha256(other.read_bytes()).digest() == other_before


@pytest.mark.parametrize("reader", ["scanner", "resumo", "grafico"])
def test_account_conflict_cache_observes_wal_commit_with_writer_still_open(mixed_source, reader):
    filters = {"data_inicio": DAY, "data_fim": DAY, "loja": "Loja A", "sku": "200"}
    with sqlite3.connect(mixed_source) as connection:
        connection.row_factory = sqlite3.Row
        filtered_rows = [dict(row) for row in connection.execute("SELECT * FROM vendas")]

    def read():
        if reader == "scanner":
            return performance.legacy_store_conflicts([str(mixed_source)], filtered_rows)
        if reader == "resumo":
            return repository.resumo_vendas(TENANT, **filters)["totals"]["itens"]
        return sum(analytics.grafico_vendas(client_id=TENANT, **filters)["quantidades_vendas"])

    writer = sqlite3.connect(mixed_source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert read() == (set() if reader == "scanner" else 2)
        main_signature = performance.vendas_file_signature(mixed_source)
        wal_signature = performance.vendas_file_signature(str(mixed_source) + "-wal")

        _add_outside_account(writer, "vendas")
        writer.commit()

        assert performance.vendas_file_signature(mixed_source) == main_signature
        assert performance.vendas_file_signature(str(mixed_source) + "-wal") != wal_signature
        assert read() == ({"Loja A"} if reader == "scanner" else 3)
        # The writer remains open through the second read, preventing its close
        # from checkpointing WAL changes and hiding an invalid cache key.
        assert writer.execute("SELECT count(*) FROM vendas").fetchone()[0] == 4
    finally:
        writer.close()


def test_wal_schema_addition_reveals_account_conflict_after_schema_cache_was_warmed(
    mixed_source, tmp_path, monkeypatch,
):
    second = tmp_path / "legacy-schema.db"
    _persist(second, _request())
    with sqlite3.connect(second) as connection:
        connection.execute("ALTER TABLE notas_entrada DROP COLUMN store_id")
        connection.execute(
            "INSERT INTO notas_entrada(id_unico,loja_conta,data_emissao) VALUES (?,?,?)",
            ("old-schema-note", "Loja A", "2026-04-11"),
        )
    paths = [str(mixed_source), str(second)]
    for module in (repository, analytics, reports):
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: paths)
    with sqlite3.connect(mixed_source) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM vendas")]

    writer = sqlite3.connect(second)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert "store_id" not in performance.cached_table_columns(str(second), "notas_entrada", writer)
        assert performance.legacy_store_conflicts(paths, rows) == set()
        main_signature = performance.vendas_file_signature(second)

        writer.execute("ALTER TABLE notas_entrada ADD COLUMN store_id TEXT")
        writer.execute("UPDATE notas_entrada SET store_id='store-b'")
        writer.commit()

        assert performance.vendas_file_signature(second) == main_signature
        assert "store_id" in performance.cached_table_columns(str(second), "notas_entrada", writer)
        assert performance.legacy_store_conflicts(paths, rows) == {"Loja A"}
        quantities = _reader_quantities()
        assert quantities == {reader: 3 for reader in quantities}
        assert writer.execute("SELECT store_id FROM notas_entrada").fetchone() == ("store-b",)
    finally:
        writer.close()
