from __future__ import annotations

import sqlite3

import pytest

from backend.modules.vendas import sync_period
from backend.schemas import VendasSyncRequest


DAY = "2026-07-18"


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.setattr(sync_period, "_verificar_cancelamento", lambda *_: None)
    monkeypatch.setattr(sync_period, "_classificar_unidade_virtual_devolucao", lambda value, *_: value)


def request(store="Loja A", day=DAY):
    return VendasSyncRequest(loja=store, data_inicio=day, data_fim=day, forcar_resync=False)


def sales(req, quantities=(1, 1), document="10", price=10, status="Faturado"):
    rows = [
        {"data": req.data_inicio, "numero": "PED-" + document, "sku": "SKU", "canal": "Canal",
         "id_pedido": document, "origem_tipo": "pedido", "origem_id": document, "item_id": str(index),
         "quantidade": quantity, "valor": quantity * price, "situacao": status,
         "nota_fiscal_id": "NF-" + document, "numero_nf": "100" + document}
        for index, quantity in enumerate(quantities, 1)
    ]
    return sync_period._preparar_payload_vendas(rows, req, "tenant")


def notes(req, quantities=(2, 3), document="20"):
    headers = [{"id": document, "numero": document, "data_emissao": req.data_inicio, "devolucao": 1, "valor": sum(quantities) * 10}]
    items = [{"id_nota": document, "numero_nota": document, "data_emissao": req.data_inicio,
              "sku": "SKU", "quantidade": quantity, "valor_unitario": 10, "valor_total": quantity * 10,
              "devolucao": 1, "item_id": str(index)} for index, quantity in enumerate(quantities, 1)]
    return sync_period._preparar_payload_notas(headers, items, req, "tenant")


def persist(path, req, sale_rows=(), note_rows=(), item_rows=()):
    return sync_period._persistir_periodo_atomico(str(path), req, list(sale_rows), list(note_rows), list(item_rows), "tenant")


def test_repeated_skus_are_preserved_and_late_sale_changes_replace_document(tmp_path):
    path = tmp_path / "sales.db"
    req = request()
    original = sales(req)
    assert len({row[0] for row in original}) == 2
    persist(path, req, original)
    persist(path, req, original)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*),sum(quantidade),sum(valor) FROM vendas").fetchone() == (2, 2, 20)
    other = request("Loja B")
    persist(path, other, sales(other, quantities=(7,)))
    earlier = request(day="2026-07-17")
    persist(path, earlier, sales(earlier, quantities=(9,), document="99"))
    # A new cycle found changed price/status and one removed item on the old day.
    persist(path, req, sales(req, quantities=(4,), price=12, status="Cancelado"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT quantidade,valor,situacao FROM vendas WHERE loja_conta='Loja A' AND origem_id='10'").fetchall() == [(4, 48, "Cancelado")]
        assert conn.execute("SELECT quantidade FROM vendas WHERE loja_conta='Loja B'").fetchall() == [(7,)]
        assert conn.execute("SELECT quantidade FROM vendas WHERE origem_id='99'").fetchall() == [(9,)]


def test_repeated_return_skus_and_removed_items_preserve_other_notes_and_stores(tmp_path):
    path = tmp_path / "sales.db"
    req = request()
    headers, items = notes(req)
    persist(path, req, note_rows=headers, item_rows=items)
    persist(path, req, note_rows=headers, item_rows=items)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*),sum(quantidade) FROM notas_entrada_itens").fetchone() == (2, 5)
    other = request("Loja B")
    headers, items = notes(other, (7,))
    persist(path, other, note_rows=headers, item_rows=items)
    earlier = request(day="2026-07-17")
    headers, items = notes(earlier, (9,), document="99")
    persist(path, earlier, note_rows=headers, item_rows=items)
    headers, items = notes(req, (4,))
    persist(path, req, note_rows=headers, item_rows=items)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT quantidade FROM notas_entrada_itens WHERE loja_conta='Loja A' AND id_nota='20'").fetchall() == [(4,)]
        assert conn.execute("SELECT quantidade FROM notas_entrada_itens WHERE loja_conta='Loja B'").fetchall() == [(7,)]
        assert conn.execute("SELECT quantidade FROM notas_entrada_itens WHERE id_nota='99'").fetchall() == [(9,)]


def test_migration_adopts_exact_legacy_order_identity_without_duplicate(tmp_path):
    path = tmp_path / "sales.db"
    req = request()
    legacy = list(sales(req, quantities=(1,))[0][:20])
    legacy[0] = sync_period._montar_id_unico_venda(req.loja, id_pedido="10", sku="SKU", quantidade=1, valor=10)
    persist(path, req, [tuple(legacy)])
    # Existing unrelated legacy row must survive the migration.
    other = list(legacy)
    other[0] = "unrelated-legacy-id"
    other[4] = "OTHER"
    other[12] = "OTHER-NUMBER"
    other[15] = "OTHER-INVOICE"
    persist(path, req, [tuple(other)])
    persist(path, req, sales(req, quantities=(3,)))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*),sum(quantidade) FROM vendas").fetchone() == (2, 4)
        assert conn.execute("SELECT quantidade FROM vendas WHERE origem_id='10'").fetchall() == [(3,)]
        assert conn.execute("SELECT id_unico FROM vendas WHERE coalesce(origem_id,'')=''").fetchall() == [("unrelated-legacy-id",)]


def test_order_replaces_legacy_invoice_fallback_for_same_invoice(tmp_path):
    path = tmp_path / "sales.db"
    req = request()
    legacy = list(sales(req, quantities=(1,))[0][:20])
    legacy[0] = sync_period._montar_id_unico_venda(req.loja, numero_nf=legacy[12], numero=legacy[4], sku="SKU", quantidade=1, valor=10)
    persist(path, req, [tuple(legacy)])
    persist(path, req, sales(req, quantities=(2,)))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*),sum(quantidade) FROM vendas").fetchone() == (1, 2)


def test_store_id_keeps_document_identity_when_display_name_changes():
    before = request("Nome anterior")
    after = request("Nome atual")
    before.store_id = after.store_id = "stable-store-id"
    assert [row[0] for row in sales(before)] == [row[0] for row in sales(after)]
    old_headers, old_items = notes(before)
    new_headers, new_items = notes(after)
    assert old_headers[0][0] == new_headers[0][0]
    assert [row[0] for row in old_items] == [row[0] for row in new_items]
    other = request("Nome atual")
    other.store_id = "another-store-id"
    assert {row[0] for row in sales(other)}.isdisjoint(row[0] for row in sales(after))


def test_rename_replaces_removed_items_using_persisted_store_id(tmp_path):
    path = tmp_path / "sales.db"
    before, after = request("Nome anterior"), request("Nome atual")
    before.store_id = after.store_id = "store-1"
    headers, items = notes(before, (1, 2))
    persist(path, before, sales(before, (1, 2)), headers, items)
    headers, items = notes(after, (4,))
    persist(path, after, sales(after, (4,)), headers, items)
    with sqlite3.connect(path) as conn:
        for table in ("vendas", "notas_entrada", "notas_entrada_itens"):
            assert conn.execute(f"SELECT store_id,loja_conta FROM {table}").fetchall() == [("store-1", "Nome atual")]
        assert conn.execute("SELECT sum(quantidade) FROM vendas").fetchone() == (4,)
        assert conn.execute("SELECT sum(quantidade) FROM notas_entrada_itens").fetchone() == (4,)


def test_same_name_and_document_never_replace_a_different_store(tmp_path):
    path = tmp_path / "sales.db"
    first, second = request("Nome igual"), request("Nome igual")
    first.store_id, second.store_id = "store-1", "store-2"
    for req, quantity in ((first, 5), (second, 8), (first, 6)):
        headers, items = notes(req, (quantity,))
        persist(path, req, sales(req, (quantity,)), headers, items)
    with sqlite3.connect(path) as conn:
        for table in ("vendas", "notas_entrada_itens"):
            assert conn.execute(f"SELECT store_id,quantidade FROM {table} ORDER BY store_id").fetchall() == [("store-1", 6), ("store-2", 8)]
        assert conn.execute("SELECT store_id FROM notas_entrada ORDER BY store_id").fetchall() == [("store-1",), ("store-2",)]
        # Unattributed legacy records are ambiguous once two accounts share a
        # name. A forced reset of either account must preserve these records.
        for table, date_column in (("vendas", "data"), ("notas_entrada", "data_emissao"), ("notas_entrada_itens", "data_emissao")):
            conn.execute(f"INSERT INTO {table}(id_unico,loja_conta,{date_column}) VALUES (?,?,?)", ("ambiguous", "Nome igual", DAY))
    first.forcar_resync = True
    persist(path, first)
    with sqlite3.connect(path) as conn:
        for table in ("vendas", "notas_entrada", "notas_entrada_itens"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE store_id='store-1'").fetchone() == (0,)
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE store_id='store-2'").fetchone() == (1,)
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE id_unico='ambiguous'").fetchone() == (1,)


def test_exact_legacy_order_is_adopted_after_date_and_channel_change(tmp_path):
    path = tmp_path / "sales.db"
    before, after = request(day="2026-07-17"), request(day="2026-07-18")
    legacy = list(sales(before, quantities=(1,))[0][:20])
    legacy[0] = sync_period._montar_id_unico_venda(before.loja, id_pedido="10", sku="SKU", quantidade=1, valor=10)
    legacy[3] = "Canal anterior"
    persist(path, before, [tuple(legacy)])
    after.store_id = "store-1"
    persist(path, after, sales(after, (4,)))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT data,canal,store_id,quantidade FROM vendas").fetchall() == [(after.data_inicio, "Canal", "store-1", 4)]


def test_invoice_fallback_preserves_unobserved_items_until_complete_note_is_seen(tmp_path):
    path = tmp_path / "sales.db"
    req = request()
    fallback = []
    for index, sku in enumerate(("SKU", "EXTRA"), 1):
        row = list(sales(req, quantities=(1,))[0])
        row[0] = sync_period._id_item_documento(req.loja, "nfe_saida", "NF-10", str(index))
        row[7] = sku
        row[20:] = ["nfe_saida", "NF-10", str(index)]
        fallback.append(tuple(row))
    persist(path, req, fallback)
    # NF listing was unavailable (the existing tolerated 403): the order only
    # proves coverage for SKU. Its update cannot erase the unobserved EXTRA.
    persist(path, req, sales(req, quantities=(2,)))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT sku,quantidade FROM vendas ORDER BY sku").fetchall() == [("EXTRA", 1), ("SKU", 2)]
    # A later complete NF detail proves that EXTRA disappeared from that note.
    sync_period._persistir_periodo_atomico(
        str(path), req, sales(req, quantities=(2,)), [], [], "tenant",
        notas_saida_observadas={"NF-10"},
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT sku,quantidade FROM vendas").fetchall() == [("SKU", 2)]


def test_note_item_replacement_rolls_back_on_late_failure(tmp_path, monkeypatch):
    path = tmp_path / "sales.db"
    req = request()
    headers, items = notes(req)
    persist(path, req, note_rows=headers, item_rows=items)
    updated_headers, updated_items = notes(req, (4,))
    calls = [0]
    def cancel(_client):
        calls[0] += 1
        if calls[0] == 4:
            raise RuntimeError("interrupted after item replacement")
    monkeypatch.setattr(sync_period, "_verificar_cancelamento", cancel)
    with pytest.raises(Exception, match="interrupted after item replacement"):
        persist(path, req, note_rows=updated_headers, item_rows=updated_items)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*),sum(quantidade) FROM notas_entrada_itens").fetchone() == (2, 5)
