"""Source fences must follow the sales rows consumed by the selected store."""

from dataclasses import replace
import os
import sqlite3

import pytest

from backend.services import medias_compras_historico as historico
from backend.services.bling_vendas import _sql_filtro_loja_vendas
from backend.services.medias_compras_fontes import capturar_fontes, fontes_consistentes


LOJA = "JK Pe\u00e7as"
OUTRA = "Uai Mineirinho"


@pytest.fixture(autouse=True)
def caches_isoladas(monkeypatch):
    monkeypatch.setattr(historico, "_PROJECOES", historico._MemoLimitado(4 * 1024 * 1024, 32))
    monkeypatch.setattr(historico, "_CONSOLIDADOS", historico._MemoLimitado(4 * 1024 * 1024, 16))
    monkeypatch.setattr(historico, "_ASSINATURAS", historico._MemoLimitado(1024 * 1024, 64))


def _banco(path, rows):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE vendas (id_unico TEXT, sku TEXT, data TEXT, produto TEXT, quantidade REAL, loja_conta TEXT, valor REAL)")
        conn.executemany("INSERT INTO vendas VALUES (?,?,?,?,?,?,?)", [(*row, 10) for row in rows])
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _mutar(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    stamp = os.stat(path).st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


@pytest.fixture
def bancos(tmp_path):
    # A store-specific filename can still contain rows from another store.
    return [
        _banco(tmp_path / "vendas_historico_uai_mineirinho.db", [
            ("jk", "001", "2026-10-10", "Produto JK", 2, LOJA),
            ("uai", "UAI", "2026-10-11", "Produto Uai", 3, OUTRA),
        ]),
        _banco(tmp_path / "vendas_historico.db", [
            ("legado", "LEGADO", "2026-09-11", "Produto legado", 4, LOJA),
        ]),
    ]


def _fontes(root, paths, *, loja=LOJA, store="jk"):
    scope = {"loja": loja, "store_id": store}
    return capturar_fontes(
        root, extras=paths, escopo=(store, loja),
        snapshot={"escopo": scope, "db_paths": tuple(paths)},
    )


def _ler(source, *, tenant="tenant"):
    scope = source.snapshot["escopo"]
    sql, params = _sql_filtro_loja_vendas(scope["loja"])
    return historico.ler_historico_vendas(
        client_id=tenant, loja=scope["loja"], store_id=scope["store_id"],
        db_paths=source.snapshot["db_paths"], meses=6, fim_periodo="2026-10-29",
        filtro_sql=sql, filtro_params=params, normalizar_sku=str,
        titulo_valido=str, snapshot=source.snapshot,
    )


def test_outra_loja_no_banco_misto_nao_descarta_calculo_e_digest_e_reutilizado(tmp_path, bancos, monkeypatch):
    before = _fontes(tmp_path, bancos)
    result = _ler(before)
    assert result[0]["001"]["vendas_mensais"]["2026-10"] == 2
    _mutar(bancos[0], "UPDATE vendas SET quantidade=7 WHERE loja_conta=?", (OUTRA,))
    after = _fontes(tmp_path, bancos)
    assert before.assinatura != after.assinatura
    original = historico._assinatura_consulta
    calls = []

    def count(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(historico, "_assinatura_consulta", count)
    assert fontes_consistentes(before, after)
    assert fontes_consistentes(before, after)
    assert calls == [bancos[0]]
    assert _ler(after) == result


@pytest.mark.parametrize("field,value", [
    ("id_unico", "novo-id"), ("sku", "OUTRO-SKU"), ("data", "2026-07-10"),
    ("produto", "Novo titulo"), ("quantidade", 11), ("loja_conta", OUTRA),
])
def test_alteracao_relevante_em_banco_com_nome_de_outra_loja_invalida(tmp_path, bancos, field, value):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    _mutar(bancos[0], "UPDATE vendas SET " + field + "=? WHERE id_unico='jk'", (value,))
    assert not fontes_consistentes(before, _fontes(tmp_path, bancos))


def test_alteracao_relevante_no_legado_invalida(tmp_path, bancos):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    _mutar(bancos[1], "DELETE FROM vendas WHERE id_unico='legado'")
    assert not fontes_consistentes(before, _fontes(tmp_path, bancos))


def test_coluna_ou_tabela_nao_consumida_nao_descarta(tmp_path, bancos):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    _mutar(bancos[0], "UPDATE vendas SET valor=999 WHERE id_unico='jk'")
    _mutar(bancos[1], "CREATE TABLE notas_auxiliares (id TEXT)")
    assert fontes_consistentes(before, _fontes(tmp_path, bancos))


def test_visao_todas_as_lojas_continua_invalidando_outra_loja(tmp_path, bancos):
    before = _fontes(tmp_path, bancos, loja="__todas", store="")
    _ler(before)
    _mutar(bancos[0], "UPDATE vendas SET quantidade=7 WHERE loja_conta=?", (OUTRA,))
    assert not fontes_consistentes(before, _fontes(tmp_path, bancos, loja="__todas", store=""))


def test_checkpoint_sem_alteracao_logica_preserva_fence(tmp_path, bancos):
    conn = sqlite3.connect(bancos[0])
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("UPDATE vendas SET quantidade=5 WHERE id_unico='jk'")
        conn.commit()
        before = _fontes(tmp_path, bancos)
        _ler(before)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = _fontes(tmp_path, bancos)
        assert before.assinatura != after.assinatura
        assert fontes_consistentes(before, after)
    finally:
        conn.close()


def test_sem_snapshot_com_escopo_diferente_ou_fontes_nao_vendas_falha_fechado(tmp_path, bancos):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    _mutar(bancos[0], "UPDATE vendas SET quantidade=8 WHERE loja_conta=?", (OUTRA,))
    after = _fontes(tmp_path, bancos)
    assert not fontes_consistentes(replace(before, snapshot=None), after)
    assert not fontes_consistentes(before, _fontes(tmp_path, bancos, loja=OUTRA, store="uai"))
    assert not fontes_consistentes(before, replace(after, pendente=True))
    (tmp_path / "produtos_compilado.csv").write_text("sku,saldo\n001,9\n", encoding="utf-8")
    assert not fontes_consistentes(before, _fontes(tmp_path, bancos))


def test_mudanca_de_inventario_nao_e_ignorada(tmp_path, bancos):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    extra = _banco(tmp_path / "vendas_historico_nova.db", [("nova", "N", "2026-10-10", "Nova", 1, LOJA)])
    assert not fontes_consistentes(before, _fontes(tmp_path, [*bancos, extra]))
    os.remove(bancos[1])
    assert not fontes_consistentes(before, _fontes(tmp_path, [bancos[0], extra]))


def test_mudanca_durante_validacao_nao_publica_digest_ou_resultado(tmp_path, bancos, monkeypatch):
    before = _fontes(tmp_path, bancos)
    _ler(before)
    _mutar(bancos[0], "UPDATE vendas SET quantidade=8 WHERE loja_conta=?", (OUTRA,))
    after = _fontes(tmp_path, bancos)
    original = historico._assinatura_consulta

    def changing(*args):
        value = original(*args)
        _mutar(args[0], "UPDATE vendas SET quantidade=90 WHERE id_unico='jk'")
        return value

    monkeypatch.setattr(historico, "_assinatura_consulta", changing)
    assert not fontes_consistentes(before, after)
    assert not historico._ASSINATURAS.entries


def test_captura_e_polling_nao_leem_linhas_de_vendas(tmp_path, bancos, monkeypatch):
    def forbidden(*_args):
        raise AssertionError("preflight must not read sales rows")

    monkeypatch.setattr(historico, "_assinatura_consulta", forbidden)
    monkeypatch.setattr(historico, "_ler_projecao", forbidden)
    before = _fontes(tmp_path, bancos)
    assert fontes_consistentes(before, _fontes(tmp_path, bancos))
