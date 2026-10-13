from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from backend.modules.vendas import analytics, repository
from backend.services import bling_vendas


NOTES_SCHEMA = """
CREATE TABLE notas_entrada (
    id_unico TEXT, loja_conta TEXT, unidade_negocio TEXT,
    unidade_negocio_virtual TEXT, id_bling TEXT
);
CREATE TABLE notas_entrada_itens (
    id_unico TEXT, loja_conta TEXT, unidade_negocio TEXT,
    unidade_negocio_virtual TEXT, id_nota TEXT, sku TEXT, descricao TEXT,
    data_emissao TEXT, quantidade REAL, valor_total REAL,
    natureza_operacao TEXT, devolucao INTEGER
);
"""


SCOPE_ROWS = [
    ("own", "JK Peças", "", "Mercado Livre Full"),
    ("accent-case", "jK PeÇas", "", ""),
    ("alias", "JKPECAS LTDA", "", ""),
    ("Carlos José_own", "JK Peças", "", "Carlos Jose"),
    ("JK Peças_other-account", "Carlos José", "", "jk pecas ltda"),
    ("other-same-unit", "Uai Mineirinho", "jk pecas ltda", ""),
    ("JK Peças_123_RET", "", "", "Carlos Jose"),
    ("Carlos José_987_OUT", "", "", "jk pecas ltda"),
    ("anonymous-unit", "", "", "jk pecas ltda"),
    ("unknown", None, None, None),
]
SCOPE_METADATA = {"JK Peças_123_RET": ("123", "RET"), "Carlos José_987_OUT": ("987", "OUT")}
EXPECTED_OWN = {"own", "accent-case", "alias", "Carlos José_own", "JK Peças_123_RET"}
EXPECTED_OWN_SKUS = {"own", "accent-case", "alias", "Carlos José_own", "RET"}


def _insert_items(connection, rows, metadata=None):
    metadata = metadata or {}
    connection.executemany(
        "INSERT INTO notas_entrada_itens VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (*row, *metadata.get(row[0], ("", row[0])), "Produto " + row[0], "2026-01-15", 1, 10, "Devolução", 1)
            for row in rows
        ],
    )


def _insert_headers(connection, rows, metadata=None):
    metadata = metadata or {}
    connection.executemany(
        "INSERT INTO notas_entrada VALUES (?,?,?,?,?)",
        [(*row, metadata.get(row[0], "")) for row in rows],
    )


def _filtered_ids(connection, loja="JK Peças", table="notas_entrada_itens"):
    cursor = connection.cursor()
    clause, params = bling_vendas._sql_filtro_loja_notas_entrada(cursor, loja)
    return {
        row[0]
        for row in cursor.execute(f"SELECT id_unico FROM {table} WHERE 1=1" + clause, params)
    }


@pytest.mark.parametrize("loja", ["JK Peças", "jk pecas", "JKPEÇAS LTDA"])
def test_account_precedes_persisted_prefix_and_unit(loja):
    headers = [
        ("header-own", "JK Peças", "", "Mercado Livre Full"),
        ("header-accent", "jK PeÇas", "", ""),
        ("Carlos José_456", "JK Peças", "", "Carlos Jose"),
        ("JK Peças_123", "", "", "Carlos Jose"),
        ("JK Peças_777", "Carlos José", "", "jk pecas ltda"),
        ("Carlos José_987", "", "", "jk pecas ltda"),
    ]
    header_metadata = {"Carlos José_456": "456", "JK Peças_123": "123", "JK Peças_777": "777", "Carlos José_987": "987"}
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, SCOPE_ROWS, SCOPE_METADATA)
        _insert_headers(connection, headers, header_metadata)
        assert _filtered_ids(connection, loja) == EXPECTED_OWN
        assert _filtered_ids(connection, loja, "notas_entrada") == {"header-own", "header-accent", "Carlos José_456", "JK Peças_123"}


@pytest.mark.parametrize("conflicting_header", [
    ("header-other", "Carlos José", "jk pecas ltda", ""),
    ("Carlos José_987", "", "jk pecas ltda", ""),
])
def test_unit_fallback_only_without_identity_and_without_conflicting_owner(conflicting_header):
    rows = [
        ("legacy-unit", "", "JKPEÇAS LTDA", ""),
        ("legacy-virtual", None, "", "jk pecas"),
        ("unknown", "", "", ""),
        ("physical-mismatch", "", "JKPEÇAS LTDA", "Carlos Jose"),
    ]
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows)
        assert _filtered_ids(connection) == {"legacy-unit", "legacy-virtual"}
        _insert_headers(connection, [("Carlos José_own", "JK Peças", "jk pecas ltda", "")])
        assert _filtered_ids(connection) == {"legacy-unit", "legacy-virtual"}
        _insert_headers(connection, [conflicting_header], {"Carlos José_987": "987"})
        assert _filtered_ids(connection) == set()


@pytest.mark.parametrize("loja", [None, "", "__todas", "__TODAS"])
def test_all_stores_remains_unfiltered(loja):
    clause, params = bling_vendas._sql_filtro_loja_notas_entrada(None, loja)
    assert clause == ""
    assert params == []


def test_other_store_accents_case_and_parameter_binding():
    rows = [
        ("own-carlos", "cARLOS JOSÉ", "", ""),
        ("Carlos José_987_OUT", "", "", ""),
        ("other", "JK Peças", "", "Carlos Jose"),
    ]
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows, {"Carlos José_987_OUT": ("987", "OUT")})
        assert _filtered_ids(connection, "Carlos José") == {"own-carlos", "Carlos José_987_OUT"}
        assert _filtered_ids(connection, "' OR 1=1 --") == set()


@pytest.mark.parametrize("loja", ["Minha_Loja", "minha_loja"])
def test_complete_store_prefix_with_literal_underscore(loja):
    rows = [
        ("Minha_Loja_123_X14", "", "Minha_Loja", ""),
        ("Minha_Loja_456_ABC_DEF", "", "", ""),
        ("Minha_Loja_789_X14", "Outra_Loja", "Minha_Loja", ""),
        ("Outra_Loja_123_X14", "", "Minha_Loja", ""),
        ("MinhaXLoja_123_X14", "", "Minha_Loja", ""),
        ("anonymous-unit", "", "Minha_Loja", ""),
    ]
    metadata = {row[0]: ("123", "X14") for row in (rows[0], rows[3], rows[4])}
    metadata.update({"Minha_Loja_456_ABC_DEF": ("456", "ABC_DEF"), "Minha_Loja_789_X14": ("789", "X14")})
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows, metadata)
        assert _filtered_ids(connection, loja) == {"Minha_Loja_123_X14", "Minha_Loja_456_ABC_DEF"}


@pytest.mark.parametrize("store", ["Minha", "Minha_Loja"])
def test_persisted_note_and_sku_remove_longer_store_and_numeric_name_ambiguity(store):
    rows = [
        (f"{store}_123_X14", "", store + "_Full", ""),
        (f"{store}_456_ABC_DEF", "", "", ""),
        (f"{store}_Full_123_X14", "", store + "_Full", ""),
        (f"{store}_2_456_X14", "", store + "_2", ""),
        (f"{store}_2_789_X14", "", store, ""),
    ]
    metadata = {
        rows[0][0]: ("123", "X14"), rows[1][0]: ("456", "ABC_DEF"),
        rows[2][0]: ("123", "X14"), rows[3][0]: ("456", "X14"), rows[4][0]: ("789", "X14"),
    }
    headers = [
        (f"{store}_123", "", store + "_Full", ""),
        (f"{store}_Full_123", "", store + "_Full", ""),
        (f"{store}_2_456", "", store + "_2", ""),
    ]
    header_metadata = {headers[0][0]: "123", headers[1][0]: "123", headers[2][0]: "456"}
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows, metadata)
        _insert_headers(connection, headers, header_metadata)
        assert _filtered_ids(connection, store) == {rows[0][0], rows[1][0]}
        assert _filtered_ids(connection, store, "notas_entrada") == {headers[0][0]}


def test_prefix_without_metadata_or_mismatching_persisted_suffix_is_unproven():
    rows = [
        ("Minha_Loja_123_X14", "", "Minha_Loja", ""),
        ("Minha_Loja_456_X14", "", "Minha_Loja", ""),
        ("Minha_Loja_789_X14", "", "Minha_Loja", ""),
        ("Minha_Loja_999_X14", "Minha_Loja", "", ""),
    ]
    metadata = {rows[1][0]: ("456", ""), rows[2][0]: ("777", "X14")}
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows, metadata)
        assert _filtered_ids(connection, "Minha_Loja") == {"Minha_Loja_999_X14"}
        connection.execute("ALTER TABLE notas_entrada_itens DROP COLUMN id_nota")
        assert _filtered_ids(connection, "Minha_Loja") == {"Minha_Loja_999_X14"}


def test_shared_id_with_conflicting_header_item_identity_is_unproven():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, [("Minha_1_2", "", "Minha", "")], {"Minha_1_2": ("1", "2")})
        _insert_headers(connection, [("Minha_1_2", "", "Minha_1", "")], {"Minha_1_2": "2"})
        assert _filtered_ids(connection, "Minha") == set()
        assert _filtered_ids(connection, "Minha_1", "notas_entrada") == set()


def test_complete_store_prefix_does_not_make_unit_ambiguous():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, [("anonymous-unit", "", "Minha_Loja", "")])
        _insert_headers(connection, [("Minha_Loja_123", "", "Minha_Loja", "")], {"Minha_Loja_123": "123"})
        assert _filtered_ids(connection, "Minha_Loja") == {"anonymous-unit"}


def test_longer_store_prefix_blocks_anonymous_unit_attribution():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, [("anonymous-unit", "", "Minha_Loja", "")])
        _insert_headers(connection, [("Minha_Loja_2_123", "", "Minha_Loja", "")], {"Minha_Loja_2_123": "123"})
        assert _filtered_ids(connection, "Minha_Loja") == set()


def test_filter_preparation_skips_conflict_scan_without_anonymous_candidates():
    rows = [
        ("identified", "JK Peças", "jk pecas ltda", ""),
        ("JK Peças_123_RET", "", "jk pecas ltda", ""),
        ("anonymous-other-unit", "", "Outra unidade", ""),
    ]
    statements = []
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(NOTES_SCHEMA)
        _insert_items(connection, rows, SCOPE_METADATA)
        _insert_headers(connection, [("JK Peças_123", "", "jk pecas ltda", "")], {"JK Peças_123": "123"})
        connection.set_trace_callback(statements.append)
        clause, params = bling_vendas._sql_filtro_loja_notas_entrada(connection.cursor(), "JK Peças")
        assert not any("NOT IN" in statement or "scope_match" in statement for statement in statements)
        assert {
            row[0] for row in connection.execute("SELECT id_unico FROM notas_entrada_itens WHERE 1=1" + clause, params)
        } == {"identified", "JK Peças_123_RET"}


@pytest.fixture
def sales_sources(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "vendas.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(NOTES_SCHEMA)
        connection.execute("""
            CREATE TABLE vendas (
                id_unico TEXT, data TEXT, loja_conta TEXT, canal TEXT, numero TEXT,
                situacao TEXT, devolucao INTEGER, sku TEXT, produto TEXT,
                quantidade REAL, valor REAL, comprador TEXT, unidade_negocio TEXT,
                nota_fiscal_id TEXT
            )
        """)
        _insert_items(connection, SCOPE_ROWS, SCOPE_METADATA)
    for module in (analytics, repository):
        monkeypatch.setattr(module, "logger", logging.getLogger("returns-scope-test"))
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_args: [str(db_path)])
        monkeypatch.setattr(module, "_sql_filtro_loja_notas_entrada", bling_vendas._sql_filtro_loja_notas_entrada)
        monkeypatch.setattr(module, "_sql_filtro_loja_vendas", bling_vendas._sql_filtro_loja_vendas)
        monkeypatch.setattr(module, "_sql_filtro_unidade_devolucao", bling_vendas._sql_filtro_unidade_devolucao)
        monkeypatch.setattr(module, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *_args: {})
        monkeypatch.setattr(module, "_deve_excluir_venda_ebazar", lambda *_args: False)
    monkeypatch.setattr(repository, "_deduplicar_vendas_consolidadas", bling_vendas._deduplicar_vendas_consolidadas)
    monkeypatch.setattr(repository, "_classificar_unidade_virtual_devolucao", bling_vendas._classificar_unidade_virtual_devolucao)
    monkeypatch.setattr(analytics, "_filtrar_vendas_lojas_ativas", lambda rows, *_args: rows)
    monkeypatch.setattr(
        analytics,
        "_vendas_series_estoque_historico",
        lambda **_kwargs: {
            "estoque_geral": [], "estoque_sku": [], "estoque_skus_com_saldo": [],
            "estoque_skus_pareto_com_saldo": [], "estoque_meta": {},
        },
    )
    return db_path


def test_sales_summary_uses_real_return_scope(sales_sources):
    result = repository.resumo_vendas.__wrapped__(
        client_id="returns-scope", loja="JK Peças",
        data_inicio="2026-01-01", data_fim="2026-01-31",
    )
    assert result["success"] is True
    assert {row["sku"] for row in result["rows"]} == EXPECTED_OWN_SKUS
    assert result["totals"]["devolucoes"] == 5
    assert result["totals"]["itensDevolucao"] == 5
    assert result["totals"]["valorDevolucao"] == 50


def test_sales_graph_uses_real_return_scope(sales_sources):
    result = analytics.grafico_vendas.__wrapped__(
        client_id="returns-scope", loja="JK Peças", intervalo="mes",
        data_inicio="2026-01-01", data_fim="2026-01-31",
    )
    assert result["quantidades_devolucoes"] == [5]
    assert result["valores_devolucoes"] == [50]
