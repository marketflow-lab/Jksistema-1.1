"""Production summary/chart snapshots under concurrent six-month collection."""

from __future__ import annotations

from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from contextvars import ContextVar
import csv
import datetime as dt
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import analytics, dependencies, performance, repository
from backend.modules.vendas import summary_loading as loading
from backend.modules.vendas.compositions import CompositionCatalog
from backend.modules.vendas.dependencies import LegacyBlingVendasAdapter
from backend.modules.vendas.sync_period import _ensure_sync_schema
from backend.services import bling_vendas


SALES_COUNT = 75_000
START = "2026-05-01"
END = "2026-11-02"
WAIT = 90


@pytest.fixture
def source(tmp_path, monkeypatch):
    logger = logging.getLogger("snapshot-loading-integration")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=logger, legacy=LegacyBlingVendasAdapter(),
    ))
    monkeypatch.setattr(loading, "_JOBS", OrderedDict())
    monkeypatch.setattr(loading, "_SLOTS", threading.BoundedSemaphore(loading.MAX_WORKERS + loading.MAX_PENDING))
    tenant = Path(paths.tenant_path("synthetic-tenant"))
    stores = [{"store_id": f"synthetic-store-{index}", "nome": f"Loja Sintetica {index}"}
              for index in range(3)]
    products = []
    databases = []
    for index, store in enumerate(stores):
        for sku in range(200):
            product = {"store_id": store["store_id"], "sku": f"SKU-{sku}",
                       "id_bling": sku + 1, "produto_bling": f"Produto {sku}"}
            if sku == 0:
                product.update(formato_bling="E", componentes_bling_json=json.dumps([
                    {"produto": {"id": 2}, "quantidade": 2},
                ]))
            products.append(product)
        path = tenant / f"vendas_historico_synthetic_{index}.db"
        databases.append(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            _ensure_sync_schema(connection.cursor())
            connection.executemany("""INSERT INTO vendas
                (id_unico,data,loja_conta,store_id,sku,produto,quantidade,valor,
                 devolucao,canal,numero,unidade_negocio)
                VALUES (?,?,?,?,?,?,1,10,0,'Marketplace',?,'Marketplace')""", (
                    (f"sale-{index}-{number}",
                     str(dt.date.fromisoformat(START) + dt.timedelta(days=number % 186)),
                     store["nome"], store["store_id"], f"SKU-{number % 200}",
                     f"Produto {number % 200}", f"order-{index}-{number}")
                    for number in range(SALES_COUNT // 3)
                ))
            if index == 0:
                connection.execute("""INSERT INTO notas_entrada_itens
                    (id_unico,data_emissao,loja_conta,store_id,sku,descricao,
                     quantidade,valor_total,devolucao,numero_nota,origem_codigo)
                    VALUES ('return-0',? ,?,?,'SKU-0','Produto 0',0,0,1,'return-0','return-0')""",
                                   (START, store["nome"], store["store_id"]))
            connection.commit()

    (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
    with (tenant / "cadastro_produtos_lojas.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for product in products for key in product}))
        writer.writeheader()
        writer.writerows(products)
    catalog = CompositionCatalog.from_products(products, stores=stores)
    for module in (repository, analytics):
        monkeypatch.setattr(module, "_listar_bancos_vendas_tenant", lambda *_: list(map(str, databases)))
        monkeypatch.setattr(module, "load_composition_catalog", lambda *_: catalog)
        monkeypatch.setattr(module, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *_: {})
    monkeypatch.setattr(repository, "_deduplicar_vendas_consolidadas", bling_vendas._deduplicar_vendas_consolidadas)
    monkeypatch.setattr(analytics, "_vendas_series_estoque_historico", lambda **_kwargs: {
        "estoque_geral": [], "estoque_sku": [], "estoque_skus_com_saldo": [],
        "estoque_skus_pareto_com_saldo": [], "estoque_meta": {"success": True, "requested": False},
    })
    performance.invalidate_vendas_cache()

    def commit(version):
        with closing(sqlite3.connect(databases[0])) as connection:
            connection.execute("UPDATE vendas SET quantidade=?,valor=? WHERE id_unico='sale-0-0'",
                               (1 + version, 10 + version))
            connection.execute("UPDATE notas_entrada_itens SET quantidade=?,valor_total=? WHERE id_unico='return-0'",
                               (version, version))
            connection.commit()
        performance.invalidate_vendas_cache("synthetic-tenant", preserve_saved=True)

    yield SimpleNamespace(databases=databases, commit=commit)
    performance.invalidate_vendas_cache()


def wait_for(predicate, message):
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    pytest.fail(message)


def test_first_complete_summary_and_shared_chart_survive_continuous_collection(source, monkeypatch):
    arguments = {"client_id": "synthetic-tenant", "data_inicio": START, "data_fim": END}
    captured = threading.Event()
    release = threading.Event()
    graph_joined = threading.Event()
    summaries_joined = threading.Event()
    commands = [threading.Event() for _ in range(5)]
    commits = [threading.Event() for _ in range(5)]
    stop = threading.Event()
    errors = []
    counts = Counter()
    counts_lock = threading.Lock()
    role = ContextVar("snapshot-integration-role", default="")

    for module, name in ((repository, "summary"), (analytics, "graph")):
        original = module.begin_vendas_snapshot

        def begin(connection, *, original=original, name=name):
            with counts_lock:
                counts[name] += 1
            return original(connection)

        monkeypatch.setattr(module, "begin_vendas_snapshot", begin)

    original_deduplicate = repository._deduplicar_vendas_consolidadas

    def after_capture(*args, **kwargs):
        with counts_lock:
            counts["summary_calculations"] += 1
            first = counts["summary_calculations"] == 1
        if first:
            captured.set()
            assert release.wait(WAIT), "the captured summary was not released"
        return original_deduplicate(*args, **kwargs)

    monkeypatch.setattr(repository, "_deduplicar_vendas_consolidadas", after_capture)
    original_result = performance._ReadFlight.result

    def joining(flight, *args, **kwargs):
        with counts_lock:
            if role.get() == "graph":
                graph_joined.set()
            elif role.get() == "summary":
                counts["summary_followers"] += 1
                if counts["summary_followers"] == 2:
                    summaries_joined.set()
        return original_result(flight, *args, **kwargs)

    monkeypatch.setattr(performance._ReadFlight, "result", joining)
    original_returns = analytics._graph_return_rows

    def commit_between_sales_and_returns(cursor, **kwargs):
        with counts_lock:
            counts["graph_return_queries"] += 1
            first = counts["graph_return_queries"] == 1
        if first:
            assert cursor.connection.in_transaction
            commands[3].set()
            assert commits[3].wait(WAIT), "collection could not commit while the graph read its snapshot"
        return original_returns(cursor, **kwargs)

    monkeypatch.setattr(analytics, "_graph_return_rows", commit_between_sales_and_returns)

    def collect():
        try:
            for index, command in enumerate(commands):
                while not command.wait(.05):
                    if stop.is_set():
                        return
                if stop.is_set():
                    return
                source.commit(index + 1)
                commits[index].set()
        except Exception as error:
            errors.append(error)

    def concurrent(kind, function):
        token = role.set(kind)
        try:
            return function()
        finally:
            role.reset(token)

    def summary():
        return repository.resumo_vendas(**arguments)

    def graph():
        return analytics.grafico_vendas(periodo="6m", intervalo="dia", **arguments)

    def load():
        return loading.read_summary(repository.resumo_vendas, arguments)

    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    try:
        first, status = load()
        assert status == 202 and not first["meta"]["leitura"]["possui_salvo"]
        assert captured.wait(WAIT)
        with ThreadPoolExecutor(max_workers=4) as pool:
            summary_futures = [pool.submit(concurrent, "summary", summary) for _ in range(2)]
            graph_futures = [pool.submit(concurrent, "graph", graph) for _ in range(2)]
            try:
                assert summaries_joined.wait(WAIT) and graph_joined.wait(WAIT)
                for index in range(3):
                    commands[index].set()
                    assert commits[index].wait(WAIT)
                    pending, status = load()
                    assert status == 202 and not pending["meta"]["leitura"]["possui_salvo"]
                    assert len(loading._JOBS) == 1
                assert counts["summary"] == 3 and counts["graph"] == 0
                release.set()
                assert commits[3].wait(WAIT)

                def saved():
                    payload, result_status = loading.read_summary(repository.resumo_vendas, arguments, saved_only=True)
                    return payload if result_status == 200 else None

                complete = wait_for(saved, "the first complete snapshot was discarded during collection")
                assert collector.is_alive(), "publishing the first summary must not require stopping collection"
                assert complete["totals"]["pedidos"] == SALES_COUNT
                assert complete["totals"]["valor"] == SALES_COUNT * 10
                assert complete["totals"]["itensLiquidos"] == SALES_COUNT + SALES_COUNT // 200
                commands[4].set()
                assert commits[4].wait(WAIT)
                refreshing, status = load()
                assert status == 200 and refreshing["meta"]["leitura"]["possui_salvo"]
                assert refreshing["totals"]["valor"] == SALES_COUNT * 10
                assert len(loading._JOBS) == 1
                for _ in range(3):
                    load()
                    assert len(loading._JOBS) == 1
                old_summaries = [future.result(WAIT) for future in summary_futures]
                graphs = [future.result(WAIT) for future in graph_futures]
            finally:
                release.set()

        def updated():
            payload, _ = load()
            return payload if payload["meta"]["leitura"]["estado"] == "atualizado" else None

        latest = wait_for(updated, "the coalesced follow-up did not publish the latest complete version")
        for old in old_summaries:
            assert old["totals"]["valor"] == SALES_COUNT * 10
        for result in graphs:
            assert len(result["labels"]) == 186
            assert sum(result["valores_vendas"]) == SALES_COUNT * 10 + 3
            assert sum(result["valores_devolucoes"]) == 3
            assert sum(result["quantidades_vendas"]) - sum(result["quantidades_devolucoes"]) == SALES_COUNT + SALES_COUNT // 200
        assert latest["totals"]["valor"] == SALES_COUNT * 10 + 5
        assert latest["totals"]["valorDevolucao"] == 5
        assert latest["totals"]["itensLiquidos"] == SALES_COUNT + SALES_COUNT // 200
        assert counts["summary_calculations"] == 2 and counts["summary"] == 6
        assert counts["graph"] == 3
        fresh_graph = graph()
        assert sum(fresh_graph["valores_vendas"]) == latest["totals"]["valor"]
        assert sum(fresh_graph["valores_devolucoes"]) == latest["totals"]["valorDevolucao"]
        assert graph() == fresh_graph and counts["graph"] == 6
        assert not errors
    finally:
        release.set()
        stop.set()
        collector.join(WAIT)
        assert not collector.is_alive()
        wait_for(lambda: all(job.state != "processando" for job in loading._JOBS.values()),
                 "a summary worker outlived the isolated fixture")
