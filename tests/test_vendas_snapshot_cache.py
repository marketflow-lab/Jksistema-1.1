"""Captured reads stay usable across collection writes without crossing hard fences."""

from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from backend.modules.vendas import dependencies, performance, persistent_cache as disk


@pytest.fixture
def context(tmp_path, monkeypatch):
    info = tmp_path / "info"
    tenant = info / "tenant-a"
    tenant.mkdir(parents=True)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=SimpleNamespace(info_dir=info, tenant_path=lambda client: str(info / client)),
        logger=logging.getLogger("snapshot-cache-fixture")))
    source = tenant / "vendas_historico_fixture.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE sales(value INTEGER)")
        conn.execute("INSERT INTO sales VALUES (1)")
    conn.close()
    mapping = tenant / "lojas_virtuais_map.json"
    mapping.write_text('{}')
    performance.invalidate_vendas_cache()
    yield SimpleNamespace(info=info, tenant=tenant, source=source, mapping=mapping)
    performance.invalidate_vendas_cache()


def append(context, value):
    with sqlite3.connect(context.source) as conn:
        conn.execute("INSERT INTO sales VALUES (?)", (value,))
    conn.close()


def test_database_writes_advance_freshness_without_changing_snapshot_identity(context):
    paths = [context.source, context.mapping]
    guard = performance.vendas_snapshot_signature(paths)
    version = performance.vendas_sources_signature(paths)
    append(context, 2)
    assert performance.vendas_sources_signature(paths) != version
    assert performance.vendas_snapshot_signature(paths) == guard
    context.mapping.write_text('{"store":"changed"}')
    assert performance.vendas_snapshot_signature(paths) != guard


@pytest.mark.parametrize("mutation", ["removed", "replaced"])
def test_database_removal_or_replacement_revokes_snapshot_identity(context, mutation):
    before = performance.vendas_snapshot_signature([context.source])
    if mutation == "removed":
        context.source.unlink()
    else:
        replacement = context.tenant / "replacement.db"
        with sqlite3.connect(replacement) as conn:
            conn.execute("CREATE TABLE sales(value INTEGER)")
        conn.close()
        os.replace(replacement, context.source)
    assert performance.vendas_snapshot_signature([context.source]) != before


def test_unknown_file_identity_falls_back_to_strict_content_signatures(context, monkeypatch):
    original = performance.os.stat

    class AnonymousStat:
        st_ino = 0

        def __init__(self, stat):
            self.stat = stat

        def __getattr__(self, name):
            return getattr(self.stat, name)

    def stat(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return AnonymousStat(value) if os.fspath(path) == str(context.source) else value

    monkeypatch.setattr(performance.os, "stat", stat)
    before = performance.vendas_snapshot_signature([context.source])
    append(context, 2)
    assert performance.vendas_snapshot_signature([context.source]) != before


def test_soft_collection_invalidation_keeps_saved_payload_but_hard_invalidation_revokes(context):
    generation = disk.capture(str(context.info), str(context.tenant))
    payload = {"success": True, "rows": [], "totals": {}, "meta": {}}
    key = "a" * 64
    assert disk.save(generation, "resumo", key, payload, lambda: True)
    performance.invalidate_vendas_cache("tenant-a", preserve_saved=True)
    assert disk.current(generation)
    assert disk.load(generation, "resumo", key) == payload
    performance.invalidate_vendas_cache("tenant-a")
    assert not disk.current(generation)
    assert disk.load(generation, "resumo", key) is None


def test_soft_collection_writes_do_not_rebuild_unchanged_composition_catalog(context):
    calls = []

    @performance.cache_vendas_response("composition_catalog", lambda _args: [context.mapping])
    def catalog(client_id):
        calls.append(client_id)
        return {"complete": True}

    assert catalog("tenant-a") == {"complete": True}
    append(context, 2)
    performance.invalidate_vendas_cache("tenant-a", preserve_saved=True)
    assert catalog("tenant-a") == {"complete": True}
    assert len(calls) == 1
    performance.invalidate_vendas_cache("tenant-a")
    catalog("tenant-a")
    assert len(calls) == 2


def test_captured_flight_is_shared_across_soft_revisions_with_its_actual_sources(context, monkeypatch):
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    original_result = performance.Future.result

    def waited(self, *args, **kwargs):
        waiting.set()
        return original_result(self, *args, **kwargs)

    monkeypatch.setattr(performance.Future, "result", waited)

    @performance.cache_vendas_response("resumo", lambda _args: [context.source, context.mapping],
                                      persistent=True, consistent_snapshot=True)
    def calculate(client_id):
        with performance.open_vendas_readonly(str(context.source)) as conn:
            value = conn.execute("SELECT SUM(value) FROM sales").fetchone()[0]
        calls.append(value)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return {"success": True, "rows": [{"sku": "FIXTURE", "itens": value}],
                "totals": {"itens": value}, "meta": {}}

    def read():
        sources, confidence = [], [True]
        source_token = performance._READ_SOURCES.set(sources)
        confidence_token = performance._READ_CONFIDENCE.set(confidence)
        try:
            payload = calculate("tenant-a")
            return payload, sources[0], confidence[0]
        finally:
            performance._READ_SOURCES.reset(source_token)
            performance._READ_CONFIDENCE.reset(confidence_token)

    original_sources = performance.vendas_sources_signature([context.source, context.mapping])
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(read)
        assert entered.wait(5)
        append(context, 2)
        performance.invalidate_vendas_cache("tenant-a", preserve_saved=True)
        follower = pool.submit(read)
        try:
            assert waiting.wait(5)
        finally:
            release.set()
        first, second = owner.result(timeout=5), follower.result(timeout=5)
    assert first[0]["totals"]["itens"] == second[0]["totals"]["itens"] == 1
    assert first[1] == second[1] == original_sources
    assert first[2] is second[2] is True
    assert calls == [1]
    assert read()[0]["totals"]["itens"] == 3
    assert calls == [1, 3]


def test_summary_and_graph_work_are_serialized_without_holding_collection_writes(context, monkeypatch):
    entered, second_attempt, release = threading.Event(), threading.Event(), threading.Event()
    graph_entered = threading.Event()

    class ObservedGate:
        def __init__(self):
            self.lock = threading.RLock()
            self.attempts = 0

        def __enter__(self):
            self.attempts += 1
            if self.attempts == 2:
                second_attempt.set()
            self.lock.acquire()

        def __exit__(self, *_args):
            self.lock.release()

    monkeypatch.setattr(performance, "_READ_WORK_LOCKS", (ObservedGate(),))

    @performance.cache_vendas_response("resumo", lambda _args: [], serialize_reads=True)
    def summary(client_id):
        entered.set()
        assert release.wait(5)
        return {"rows": []}

    @performance.cache_vendas_response("grafico", lambda _args: [], serialize_reads=True)
    def graph(client_id):
        graph_entered.set()
        return {"labels": []}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(summary, "tenant-a")
        assert entered.wait(5)
        second = pool.submit(graph, "tenant-a")
        try:
            assert second_attempt.wait(5)
            assert not graph_entered.is_set()
            append(context, 2)
        finally:
            release.set()
        assert first.result(timeout=5) == {"rows": []}
        assert second.result(timeout=5) == {"labels": []}
