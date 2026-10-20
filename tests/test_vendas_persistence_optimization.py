from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3

import pytest

from backend.modules.vendas import sync_period
from backend.schemas import VendasSyncRequest


def _previous():
    path = Path(__file__).parent / "fixtures" / "vendas_persistence_before_optimization.py"
    spec = importlib.util.spec_from_file_location("previous_vendas_persistence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(store_id="store-a"):
    return VendasSyncRequest(
        loja=" Loja / A _ X ", store_id=store_id,
        data_inicio="2026-10-01", data_fim="2026-10-01",
    )


def _row(req, index, *, kind="pedido", document=None):
    document = f"order_{index}" if document is None else document
    return (
        f"new-{kind}-{index}", "2026-10-01", req.loja, "Canal", f"NUM_{index % 23}",
        "Faturado", 0, ["S_K U", "SKU", "", "0"][index % 4], "Produto", 2.5, 30.75,
        "2026-10", f"FIS_{index % 27}", "", "Unidade", f"NF_{index % 37}",
        "", "", "", "", kind, document, f"item-{index}",
    )


def _seed(req, size=240, ambiguity=None):
    connection = sqlite3.connect(":memory:")
    sync_period._ensure_sync_schema(connection.cursor())
    for index in range(size):
        row = list(_row(req, index)[:20])
        row[1] = "2024-01-01"  # Adoption must still find old dates and changed values.
        row[9], row[10] = (1.125, 12.3456789) if index % 2 else (0, -1.25)
        reference = f"order_{index}"
        if index % 7 == 0:
            reference = f"ord er_{index}"  # Several raw IDs normalize to the same ID.
        if index % 5 == 0:
            reference = ""
        row[0] = sync_period._montar_id_unico_venda(
            req.loja, id_pedido=reference, numero_nf=row[12], numero=row[4],
            sku=row[7], quantidade=row[9], valor=row[10],
        )
        if index % 11 == 0:
            row[0] = "opaque-legacy-" + str(index)
        elif index % 13 == 0:
            row[0] = " " + row[0] + " "
        connection.execute(sync_period._SQL_INSERT_VENDAS, row)
    # Typed documents and another display name survive unrelated refreshes.
    typed = list(_row(req, 50))
    typed[0] = "old-typed-document"
    connection.execute(sync_period._insert_com_store_id(sync_period._SQL_INSERT_VENDAS_IDENTIFICADAS), (*typed, req.store_id or ""))
    other = list(_row(req, 60)[:20])
    other[0], other[2] = "other-store-legacy", "Outra loja"
    connection.execute(sync_period._SQL_INSERT_VENDAS, other)
    if ambiguity:
        date_column = "data" if ambiguity == "vendas" else "data_emissao"
        connection.execute(
            f"INSERT INTO {ambiguity}(id_unico,loja_conta,store_id,{date_column}) VALUES (?,?,?,?)",
            ("other-account-same-name", req.loja, "store-b", "2026-10-01"),
        )
    connection.commit()
    return connection


def _documents(req, include_other_kind=False):
    rows = [_row(req, index) for index in range(110)]
    # Repeated SKU, changed item, blank references and normalization collisions.
    for index, reference in enumerate(("ord er_14", "order_14", "  ", "", 0, None), 300):
        row = list(_row(req, index, document="placeholder"))
        row[21] = reference
        row[4], row[15] = "NUM_14", "NF_14"
        rows.append(tuple(row))
    rows += [_row(req, index, kind="nfe_saida", document=f"NF_{index % 37}") for index in range(15)]
    if include_other_kind:
        rows.append(_row(req, 800, kind="legacy-kind"))
    return rows


def _database_rows(connection):
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY id_unico").fetchall()
        for table in ("vendas", "notas_entrada", "notas_entrada_itens")
    }


@pytest.mark.parametrize("store_id", ["", "store-a"])
@pytest.mark.parametrize("ambiguity", [None, "vendas", "notas_entrada", "notas_entrada_itens"])
@pytest.mark.parametrize("include_other_kind", [False, True])
def test_document_refresh_matches_frozen_previous_algorithm(store_id, ambiguity, include_other_kind):
    req = _request(store_id)
    previous = _previous()
    before = _seed(req, ambiguity=ambiguity)
    after = sqlite3.connect(":memory:")
    before.backup(after)
    try:
        documents = _documents(req, include_other_kind)
        observed = {"NF_3", "NF_4", "not-observed-in-orders"}
        previous._substituir_documentos_vendas(before.cursor(), documents, req, observed)
        sync_period._substituir_documentos_vendas(after.cursor(), documents, req, observed)
        assert _database_rows(after) == _database_rows(before)
        # Replaying the observed documents is also identical and idempotent.
        expected = _database_rows(after)
        sync_period._substituir_documentos_vendas(after.cursor(), documents, req, observed)
        assert _database_rows(after) == expected
    finally:
        before.close()
        after.close()


def test_empty_legacy_payload_never_reads_history():
    connection = sqlite3.connect(":memory:")
    connection.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
    try:
        assert sync_period._filtrar_vendas_ja_existentes(connection.cursor(), [], "Loja A", "store-a") == []
    finally:
        connection.close()


def test_large_history_keeps_rows_and_avoids_document_times_history_id_work(monkeypatch):
    req = _request()
    previous = _previous()
    before = _seed(req, size=1600)
    after = sqlite3.connect(":memory:")
    before.backup(after)
    original = sync_period._montar_id_unico_venda
    calls = {"count": 0}

    def counted(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    previous._montar_id_unico_venda = counted
    documents = _documents(req)
    try:
        previous._substituir_documentos_vendas(before.cursor(), documents, req, {"NF_3"})
        previous_calls = calls["count"]
        calls["count"] = 0
        monkeypatch.setattr(sync_period, "_montar_id_unico_venda", counted)
        sync_period._substituir_documentos_vendas(after.cursor(), documents, req, {"NF_3"})
        assert _database_rows(after) == _database_rows(before)
        assert previous_calls > 100_000
        assert calls["count"] <= 1600
    finally:
        before.close()
        after.close()
