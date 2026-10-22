from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.modules.vendas import repository
from backend.modules.vendas.compositions import CompositionCatalog
from backend.services import bling_vendas


ARGUMENTS = ("loja_id", "unidade_id", "nome_oficial", "intermediador_nome", "intermediador_cnpj", "canal")


@pytest.fixture
def unit_source(tmp_path, monkeypatch):
    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE vendas (
            id_unico TEXT PRIMARY KEY, data TEXT, loja_conta TEXT, store_id TEXT,
            sku TEXT, produto TEXT, quantidade REAL, valor REAL, devolucao INTEGER,
            canal TEXT, numero TEXT, comprador TEXT, unidade_negocio TEXT,
            loja_id TEXT, unidade_id TEXT, intermediador_nome TEXT, intermediador_cnpj TEXT
        )""")
    maps = {}
    calls = {"resolver": [], "normalizer": []}
    catalog = CompositionCatalog(stores=[{"store_id": "store-a", "nome": "Loja A"}])
    monkeypatch.setattr(repository, "logger", logging.getLogger("summary-units-memo-test"))
    monkeypatch.setattr(repository, "_listar_bancos_vendas_tenant", lambda *_: [str(path)])
    monkeypatch.setattr(repository, "load_composition_catalog", lambda *_: catalog)
    monkeypatch.setattr(repository, "_carregar_mapeamento_lojas_virtuais_cliente", lambda tenant: maps[tenant])
    monkeypatch.setattr(repository, "_deve_excluir_venda_ebazar", bling_vendas._deve_excluir_venda_ebazar)
    monkeypatch.setattr(repository, "_deduplicar_vendas_consolidadas", bling_vendas._deduplicar_vendas_consolidadas)

    def resolve(mapping, **kwargs):
        calls["resolver"].append((mapping, tuple(kwargs[key] for key in ARGUMENTS)))
        return bling_vendas._resolver_nome_loja_virtual(mapping, **kwargs)

    def normalize(unit, channel, *, prefer_full):
        calls["normalizer"].append((unit, channel, prefer_full))
        return bling_vendas._normalizar_unidade_negocio_ml(unit, channel, prefer_full=prefer_full)

    monkeypatch.setattr(repository, "_resolver_nome_loja_virtual", resolve)
    monkeypatch.setattr(repository, "_normalizar_unidade_negocio_ml", normalize)

    def insert(combinations, repeats=40):
        rows = []
        for index, args in enumerate(combinations):
            for number in range(repeats):
                rows.append((
                    f"case-{index}-{number}", "2026-10-10", "Loja A", "store-a",
                    f"CASE-{index}", f"Produto {index}", 2, 3.25, 0, args[5],
                    f"number-{index}-{number}", "", args[2], args[0], args[1], args[3], args[4],
                ))
        with sqlite3.connect(path) as db:
            db.executemany("INSERT INTO vendas VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def read(tenant):
        # Bypass the response cache so every invocation must own a new unit memo.
        return repository.resumo_vendas.__wrapped__(
            tenant, data_inicio="2026-10-01", data_fim="2026-10-31",
        )

    return path, maps, calls, insert, read


def test_summary_resolves_each_distinct_six_argument_combination_once_with_identical_units(unit_source):
    path, maps, calls, insert, read = unit_source
    mapping = {
        "loja_id": {"store-alias": "Unidade Alias da loja"},
        "unidade_id": {"unit-alias": "Unidade Alias da unidade"},
        "cnpj": {"11.111.111/0001-11": "Unidade Alias do intermediador"},
    }
    maps["tenant-a"] = mapping
    base = ("", "", "Mercado Livre", "Mercado Livre", "", "Mercado Livre")
    combinations = [base]
    for index, value in enumerate((
        "store-alias", "unit-alias", "Unidade Oficial", "Unidade Parceiro",
        "11111111000111", "Unidade Canal",
    )):
        changed = list(base)
        changed[index] = value
        combinations.append(tuple(changed))
    combinations.append(("", "", None, "", "", None))
    insert(combinations)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = read("tenant-a")

    assert len(calls["resolver"]) == len(calls["normalizer"]) == len(combinations)
    assert {args for _, args in calls["resolver"]} == set(combinations)
    assert all(actual_map is mapping for actual_map, _ in calls["resolver"])
    rows = {row["sku"]: row for row in result["rows"]}
    expected_units = set()
    for index, args in enumerate(combinations):
        resolved = bling_vendas._resolver_nome_loja_virtual(mapping, **dict(zip(ARGUMENTS, args)))
        unit = bling_vendas._normalizar_unidade_negocio_ml(resolved or args[2], args[5], prefer_full=False)
        expected_units.update([unit] if unit else [])
        row = rows[f"CASE-{index}"]
        assert row["unidade_negocio"] == str((unit or args[2]) or "")
        assert (row["pedidos"], row["itens"], row["valor"]) == (40, 80, 130)
    assert result["meta"]["unidades"] == sorted(expected_units)
    assert result["totals"]["pedidos"] == 40 * len(combinations)
    assert result["totals"]["itens"] == 80 * len(combinations)
    assert result["totals"]["valor"] == 130 * len(combinations)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_unit_memo_does_not_survive_map_changes_or_another_tenant(unit_source):
    _, maps, calls, insert, read = unit_source
    insert([("alias", "", "Mercado Livre", "", "", "Mercado Livre")])
    maps.update({
        "tenant-a": {"loja_id": {"alias": "Unidade Canal A"}},
        "tenant-b": {"loja_id": {"alias": "Unidade Canal B"}},
    })
    assert read("tenant-a")["meta"]["unidades"] == ["Canal A"]
    maps["tenant-a"]["loja_id"]["alias"] = "Unidade Canal A atualizado"
    assert read("tenant-a")["meta"]["unidades"] == ["Canal A atualizado"]
    assert read("tenant-b")["meta"]["unidades"] == ["Canal B"]
    maps["tenant-a"] = {"loja_id": {"alias": "Unidade Novo snapshot A"}}
    assert read("tenant-a")["meta"]["unidades"] == ["Novo snapshot A"]
    assert len(calls["resolver"]) == len(calls["normalizer"]) == 4


def test_concurrent_summaries_keep_tenant_maps_separate(unit_source, monkeypatch):
    _, maps, calls, insert, read = unit_source
    insert([("alias", "", "Mercado Livre", "", "", "Mercado Livre")])
    maps.update({
        "tenant-a": {"loja_id": {"alias": "Unidade Canal A"}},
        "tenant-b": {"loja_id": {"alias": "Unidade Canal B"}},
    })
    barrier = threading.Barrier(2)
    original = repository._resolver_nome_loja_virtual

    def overlapping(mapping, **kwargs):
        barrier.wait(timeout=10)
        return original(mapping, **kwargs)

    monkeypatch.setattr(repository, "_resolver_nome_loja_virtual", overlapping)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(read, "tenant-a")
        second = executor.submit(read, "tenant-b")
        assert first.result(timeout=15)["meta"]["unidades"] == ["Canal A"]
        assert second.result(timeout=15)["meta"]["unidades"] == ["Canal B"]
    assert len(calls["resolver"]) == len(calls["normalizer"]) == 2
