from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import analytics, dependencies, performance, repository
from backend.modules.vendas.compositions import CompositionCatalog
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter
from backend.modules.vendas.read_snapshot import begin_vendas_snapshot
from backend.modules.vendas.sync_period import _ensure_sync_schema
from backend.services import bling_vendas


@pytest.fixture
def source(tmp_path, monkeypatch):
    logger = logging.getLogger("sales-read-snapshot-test")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=logger, legacy=LegacyBlingVendasAdapter(),
    ))
    path = Path(paths.tenant_path("tenant-a")) / "vendas_historico.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_sync_schema(conn.cursor())
        conn.execute("""INSERT INTO vendas
            (id_unico,data,loja_conta,store_id,sku,produto,quantidade,valor,devolucao,numero)
            VALUES ('sale-1','2026-11-01','Loja A','store-a','SKU','Produto',1,10,0,'order-1')""")
        conn.execute("""INSERT INTO notas_entrada_itens
            (id_unico,data_emissao,loja_conta,store_id,sku,descricao,quantidade,valor_total,devolucao)
            VALUES ('return-1','2026-11-01','Loja A','store-a','SKU','Produto',1,2,1)""")
    catalog = CompositionCatalog(stores=[{"store_id": "store-a", "nome": "Loja A"}])
    for module in (repository, analytics):
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: [str(path)])
        monkeypatch.setattr(module, "load_composition_catalog", lambda *_: catalog)
        monkeypatch.setattr(module, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *_: {})
        monkeypatch.setattr(module, "_deve_excluir_venda_ebazar", lambda *_: False)
        monkeypatch.setattr(module, "_sql_filtro_loja_vendas", lambda *_: ("", []))
        monkeypatch.setattr(module, "_sql_filtro_loja_notas_entrada", lambda *_: ("", []))
        monkeypatch.setattr(module, "_sql_filtro_unidade_devolucao", lambda *_: ("", []))
    monkeypatch.setattr(repository, "_resolver_nome_loja_virtual", lambda *_, **kwargs: kwargs.get("nome_oficial", ""))
    monkeypatch.setattr(repository, "_normalizar_unidade_negocio_ml", lambda unit, *_, **kwargs: unit)
    monkeypatch.setattr(repository, "_classificar_unidade_virtual_devolucao", lambda *_: "")
    monkeypatch.setattr(repository, "_deduplicar_vendas_consolidadas", bling_vendas._deduplicar_vendas_consolidadas)
    monkeypatch.setattr(analytics, "_vendas_series_estoque_historico", lambda **kwargs: {
        "estoque_geral": [], "estoque_sku": [], "estoque_skus_com_saldo": [],
        "estoque_skus_pareto_com_saldo": [], "estoque_meta": {"success": True},
    })
    return SimpleNamespace(path=path, catalog=catalog)


def read(kind):
    if kind == "summary":
        result = repository.resumo_vendas.__wrapped__(
            "tenant-a", data_inicio="2026-11-01", data_fim="2026-11-01",
        )
        return result["totals"]["valor"], result["totals"]["valorDevolucao"]
    result = analytics.grafico_vendas.__wrapped__(
        client_id="tenant-a", data_inicio="2026-11-01", data_fim="2026-11-01",
    )
    return sum(result["valores_vendas"]), sum(result["valores_devolucoes"])


@pytest.mark.parametrize("kind", ["summary", "graph"])
def test_atomic_day_commit_between_sales_and_returns_keeps_one_read_version(source, monkeypatch, kind):
    module = repository if kind == "summary" else analytics
    helper = "_summary_return_rows" if kind == "summary" else "_graph_return_rows"
    original = getattr(module, helper)
    committed = []

    def after_sales(cur, *args, **kwargs):
        assert cur.connection.in_transaction
        if not committed:
            with sqlite3.connect(source.path) as writer:
                writer.execute("UPDATE vendas SET valor=100")
                writer.execute("UPDATE notas_entrada_itens SET valor_total=20")
            committed.append(True)
        return original(cur, *args, **kwargs)

    monkeypatch.setattr(module, helper, after_sales)
    assert read(kind) == (10, 2)
    assert committed == [True]
    assert read(kind) == (100, 20)


@pytest.mark.parametrize("kind", ["summary", "graph"])
def test_connections_close_before_python_projection_or_account_deduplication(source, monkeypatch, kind):
    module = repository if kind == "summary" else analytics
    original_open = module.open_vendas_readonly
    opened = []

    class TrackedConnection:
        def __init__(self, conn):
            self.conn, self.closed = conn, False
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def close(self):
            self.conn.close()
            self.closed = True

    def open_tracked(*args, **kwargs):
        conn = TrackedConnection(original_open(*args, **kwargs))
        opened.append(conn)
        return conn

    original_project = source.catalog.project_rows
    def project(*args, **kwargs):
        assert opened and all(conn.closed for conn in opened)
        return original_project(*args, **kwargs)

    original_conflicts = module.legacy_store_conflicts
    def conflicts(*args, captured_accounts, **kwargs):
        assert all(conn.closed for conn in opened)
        assert captured_accounts == {("Loja A", "store-a")}
        return original_conflicts(*args, captured_accounts=captured_accounts, **kwargs)

    monkeypatch.setattr(module, "open_vendas_readonly", open_tracked)
    monkeypatch.setattr(module, "legacy_store_conflicts", conflicts)
    monkeypatch.setattr(source.catalog, "project_rows", project)
    assert read(kind) == (10, 2)
    assert len(opened) == 1


def test_account_evidence_from_all_three_tables_is_frozen_outside_requested_window(source):
    with sqlite3.connect(source.path) as writer:
        writer.execute("""INSERT INTO notas_entrada
            (id_unico,data_emissao,loja_conta,store_id) VALUES ('old-note','2020-01-01','Loja A','store-b')""")
        writer.execute("UPDATE notas_entrada_itens SET store_id='store-c', data_emissao='2020-01-01'")
    conn = performance.open_vendas_readonly(str(source.path))
    try:
        accounts = begin_vendas_snapshot(conn)
        with sqlite3.connect(source.path) as writer:
            writer.execute("UPDATE notas_entrada SET store_id='store-after-capture'")
        assert accounts == {("Loja A", "store-a"), ("Loja A", "store-b"), ("Loja A", "store-c")}
        assert conn.execute("SELECT store_id FROM notas_entrada").fetchone()[0] == "store-b"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM vendas")
    finally:
        conn.close()


@pytest.mark.parametrize("kind", ["summary", "graph"])
def test_legacy_account_check_uses_captured_evidence_without_reopening_databases(source, monkeypatch, kind):
    with sqlite3.connect(source.path) as writer:
        writer.execute("""INSERT INTO vendas
            (id_unico,data,loja_conta,store_id,sku,produto,quantidade,valor,devolucao,numero)
            VALUES ('Loja A_order-1_SKU','2026-11-01','Loja A','','SKU','Produto',1,10,0,'order-1')""")
    monkeypatch.setattr(performance, "open_vendas_readonly", lambda *_args, **_kwargs: pytest.fail(
        "legacy account evidence must not reopen a database after the read snapshot closes",
    ))
    assert read(kind) == (20, 2)


@pytest.mark.parametrize("kind", ["summary", "graph"])
def test_missing_database_marks_snapshot_incomplete(source, monkeypatch, kind):
    module = repository if kind == "summary" else analytics
    monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: [str(source.path.with_name("missing.db"))])
    confidence = [True]
    token = performance._READ_CONFIDENCE.set(confidence)
    try:
        assert read(kind) == (0, 0)
        assert confidence == [False]
    finally:
        performance._READ_CONFIDENCE.reset(token)
