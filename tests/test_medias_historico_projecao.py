from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import os
import random
import re
import sqlite3
import unicodedata

import pytest

from backend.services import medias_compras_historico as historico
from backend.services.bling_vendas import _sql_filtro_loja_vendas
from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services.medias_compras_leitura import leitura_medias


def _titulo(value):
    title = re.sub(r"\s+", " ", str(value or "").strip())
    normal = "".join(c for c in unicodedata.normalize("NFKD", title.lower()) if not unicodedata.combining(c))
    return "" if normal in {"", "-", "produto s descricao", "produto sem descricao", "sem descricao"} else title


@pytest.fixture(autouse=True)
def caches_isoladas(monkeypatch):
    monkeypatch.setattr(historico, "_PROJECOES", historico._MemoLimitado(4 * 1024 * 1024, 32))
    monkeypatch.setattr(historico, "_CONSOLIDADOS", historico._MemoLimitado(4 * 1024 * 1024, 16))


def _banco(path, rows):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE vendas (id_unico TEXT, sku TEXT, data TEXT, produto TEXT, quantidade REAL, loja_conta TEXT)")
        conn.execute("CREATE INDEX idx_vendas_sku_data ON vendas(sku, data)")
        conn.executemany("INSERT INTO vendas VALUES (?,?,?,?,?,?)", rows)
    return str(path)


def _ler(paths, *, meses=3, loja="__todas", client="tenant", store="", fim="2026-10-20", **kwargs):
    sql, params = _sql_filtro_loja_vendas(loja)
    return historico.ler_historico_vendas(
        client_id=client, loja=loja, store_id=store, db_paths=paths,
        meses=meses, fim_periodo=fim, filtro_sql=sql, filtro_params=params,
        normalizar_sku=_normalizar_sku_mes, titulo_valido=_titulo, **kwargs,
    )


def _referencia_sql_antigo(paths, meses, loja, fim="2026-10-20"):
    """Independent oracle: the three original SQL passes and their merge rules."""
    refs = historico._meses_referencia(fim[:7], meses)
    filtro, params = _sql_filtro_loja_vendas(loja)
    vendas, titulos, ultimas, vistos = {}, {}, {}, set()
    for path in paths:
        with sqlite3.connect(path) as conn:
            rows = conn.execute("""
                SELECT id_unico, sku, data, COALESCE(produto, ''),
                       strftime('%Y-%m', date(data)), COALESCE(quantidade, 0)
                FROM vendas WHERE sku IS NOT NULL AND trim(sku) != ''
                AND date(data) BETWEEN ? AND ?
            """ + filtro, [refs[0] + "-01", fim, *params]).fetchall()
            for identifier, sku, data, produto, mes, qty in rows:
                raw = str(sku or "").strip()
                if not raw:
                    continue
                identifier = str(identifier or "").strip()
                qty = float(qty or 0)
                key = identifier or f"{os.path.basename(path)}::{raw}::{mes}::{qty}"
                if key in vistos:
                    continue
                vistos.add(key)
                normalized = _normalizar_sku_mes(raw)
                title = _titulo(produto)
                data = str(data or "").strip()
                if title and (normalized not in titulos or data >= titulos[normalized]["data"]):
                    titulos[normalized] = {"titulo": title, "data": data}
                if normalized not in vendas:
                    vendas[normalized] = {"sku": normalized, "vendas_mensais": dict.fromkeys(refs, 0.0)}
                if mes in vendas[normalized]["vendas_mensais"]:
                    vendas[normalized]["vendas_mensais"][mes] += qty
            for sku, data, produto in conn.execute("""
                SELECT sku, data, COALESCE(produto, '') FROM vendas
                WHERE sku IS NOT NULL AND trim(sku) != '' AND trim(COALESCE(produto, '')) != ''
            """ + filtro + " ORDER BY date(data) DESC, data DESC", params):
                raw = str(sku or "").strip()
                if not raw:
                    continue
                normalized = _normalizar_sku_mes(raw)
                title = _titulo(produto)
                data = str(data or "").strip()
                if title and (normalized not in titulos or data >= titulos[normalized]["data"]):
                    titulos[normalized] = {"titulo": title, "data": data}
            for sku, data in conn.execute("""
                SELECT sku, MAX(date(data)) FROM vendas
                WHERE sku IS NOT NULL AND trim(sku) != ''
            """ + filtro + " GROUP BY sku", params):
                normalized = _normalizar_sku_mes(str(sku or "").strip())
                if normalized and data and (normalized not in ultimas or data > ultimas[normalized]):
                    ultimas[normalized] = data
    return vendas, titulos, ultimas


@pytest.fixture
def bancos(tmp_path):
    rng = random.Random(274)
    dates = ["2026-10-15", "2026-08-01", "2026-07-01", "2026-04-01", "2025-11-01", "2025-10-31", "2020-01-15", "2026-11-01", "invalida", None]
    skus = ["001", "1", "01", "010", "10", "1-JAN", "1/Jan", "A.B", "A-B", "", None]
    stores = ["JK Peças", "jk pecas ltda", "Outra Loja"]
    titles = ["Produto 1", "Produto 2", " ", "Sem descrição", "Produto\ncom espaço"]
    paths = []
    for index, name in enumerate(["vendas_historico_jk_pecas.db", "vendas_historico.db", "vendas_historico_antigo.db"]):
        rows = [(str(rng.randrange(120)) if rng.randrange(4) else "", rng.choice(skus), rng.choice(dates), rng.choice(titles), rng.choice([0, .1, .2, 1, 3, -1]), rng.choice(stores)) for _ in range(240)]
        rows.extend([
            ("shared", "DEDUP", "2026-10-10", "Mesmo ID", index + 1, "JK Peças"),
            ("period-dependent", "PERIOD", "2026-07-10" if index == 0 else "2026-10-10", "Periodo", index + 1, "JK Peças"),
            ("same-date-1", "TIE", "2026-10-01 ", "Espaço final", 1, "JK Peças"),
            ("same-date-2", "TIE", "2026-10-01", "Sem espaço final", 1, "JK Peças"),
            ("old-only", "OLD", "2020-01-15", "Título fora do período", 3, "JK Peças"),
            ("future", "FUTURE", "2026-11-01", "Título futuro mantido", 7, "JK Peças"),
            ("invalid-only", "INVALID", "inválida", "Título data inválida", 1, "JK Peças"),
            ("invalid-qty-old", "OLDQTY", "2010-01-01", "Histórico", "não numérico", "JK Peças"),
        ])
        paths.append(_banco(tmp_path / name, rows))
    return paths


@pytest.mark.parametrize("meses", [3, 6, 12])
@pytest.mark.parametrize("loja", ["__todas", "JK Peças", "Outra Loja"])
def test_paridade_literal_com_as_tres_consultas_originais(bancos, meses, loja):
    assert _ler(bancos, meses=meses, loja=loja) == _referencia_sql_antigo(bancos, meses, loja)


def test_reaproveita_periodos_e_rele_somente_banco_alterado(bancos):
    with leitura_medias() as first:
        resultado = _ler(bancos, meses=3)
    assert first.metrics["historico_bancos_lidos"] == 3
    with leitura_medias() as warm:
        assert _ler(bancos, meses=12) == _referencia_sql_antigo(bancos, 12, "__todas")
    assert warm.metrics.get("historico_bancos_lidos", 0) == 0
    resultado[0].clear()
    assert _ler(bancos)[0]
    with sqlite3.connect(bancos[1]) as conn:
        conn.execute("INSERT INTO vendas VALUES ('new', 'NEW', '2026-10-20', 'Nova venda', 9, 'JK Peças')")
    with leitura_medias() as changed:
        atual = _ler(bancos)
    assert changed.metrics["historico_bancos_lidos"] == 1
    assert atual == _referencia_sql_antigo(bancos, 3, "__todas")
    assert atual[0]["NEW"]["vendas_mensais"]["2026-10"] == 9


def test_prioridade_de_bancos_dedup_e_fallback_por_mes(tmp_path):
    paths = [_banco(tmp_path / f"base-{i}.db", [
        ("id", "SKU", "2026-10-10", "Título", qty, "JK Peças"),
        ("", "SEM-ID", "2026-10-10", "Título", 2, "JK Peças"),
        ("", "SEM-ID", "2026-10-11", "Título", 2, "JK Peças"),
        ("", "SEM-ID", "2026-09-11", "Título", 2, "JK Peças"),
    ]) for i, qty in enumerate([1, 10])]
    first = _ler(paths)[0]
    assert first["SKU"]["vendas_mensais"]["2026-10"] == 1
    assert first["SEM-ID"]["vendas_mensais"]["2026-10"] == 4
    assert first["SEM-ID"]["vendas_mensais"]["2026-09"] == 4
    assert _ler(paths[::-1])[0]["SKU"]["vendas_mensais"]["2026-10"] == 10


def test_revision_wal_commit_e_checkpoint_invalidam(tmp_path):
    path = _banco(tmp_path / "wal.db", [("1", "SKU", "2026-10-10", "Primeiro", 1, "JK Peças")])
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        before = _ler([path])
        conn.execute("UPDATE vendas SET quantidade=4, produto='Atualizado'")
        conn.commit()
        after = _ler([path])
        assert before != after
        assert after == _referencia_sql_antigo([path], 3, "__todas")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert _ler([path]) == after
    finally:
        conn.close()


def test_escopo_normalizacao_e_dia_separam_cache(bancos):
    _ler(bancos)
    for options in [{"client": "outro"}, {"store": "outra"}, {"loja": "JK Peças"}, {"fim": "2026-10-21"}, {"normalizacao_token": "nova-regra"}]:
        with leitura_medias() as read:
            _ler(bancos, **options)
        assert read.metrics["historico_bancos_lidos"] == 3


def test_nao_cria_indice_ou_modifica_banco_em_get(tmp_path):
    path = _banco(tmp_path / "readonly.db", [("1", "SKU", "2026-10-01", "Produto", 1, "JK Peças")])
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX idx_vendas_sku_data")
    before = open(path, "rb").read()
    assert _ler([path])[0]
    assert open(path, "rb").read() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall() == []


def test_memoria_limitada_e_resultado_grande_continua_correto(bancos, monkeypatch):
    monkeypatch.setattr(historico, "_PROJECOES", historico._MemoLimitado(1, 1))
    monkeypatch.setattr(historico, "_CONSOLIDADOS", historico._MemoLimitado(1, 1))
    assert _ler(bancos) == _referencia_sql_antigo(bancos, 3, "__todas")
    assert historico._PROJECOES.bytes == historico._CONSOLIDADOS.bytes == 0
    cache = historico._MemoLimitado(10, 2)
    for number in range(8):
        assert cache.obter(number, "rev", lambda n=number: (n, 4), lambda: True) == number
        assert len(cache.entries) <= 2 and cache.bytes <= 10


def test_single_flight_e_copia_independente_sob_concorrencia(bancos, monkeypatch):
    original = historico._ler_projecao
    entered, release = Event(), Event()
    calls = []

    def slow(*args):
        calls.append(args[0])
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(historico, "_ler_projecao", slow)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_ler, bancos) for _ in range(6)]
        assert entered.wait(10)
        release.set()
        results = [future.result(timeout=10) for future in futures]
    assert len(calls) == len(bancos)
    assert all(result == results[0] for result in results)
    results[0][0].clear()
    assert results[1][0]


def test_nao_publica_cache_de_fonte_que_mudou_durante_leitura(bancos, monkeypatch):
    original = historico._ler_projecao
    changed = []

    def changing(*args):
        result = original(*args)
        if not changed:
            changed.append(True)
            with sqlite3.connect(args[0]) as conn:
                conn.execute("INSERT INTO vendas VALUES ('during', 'DURING', '2026-10-20', 'Nova', 6, 'JK Peças')")
        return result

    monkeypatch.setattr(historico, "_ler_projecao", changing)
    _ler(bancos)
    assert not historico._CONSOLIDADOS.entries
    assert _ler(bancos) == _referencia_sql_antigo(bancos, 3, "__todas")
