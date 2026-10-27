"""Durable read-cache proofs using synthetic files and independent processes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import builtins
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from backend.modules.vendas import dependencies, performance
from backend.modules.vendas import persistent_cache as disk


ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import json, logging, os, pathlib, sqlite3, sys
from types import SimpleNamespace
from backend.modules.vendas import dependencies, performance
info = pathlib.Path(sys.argv[1])
def tenant(client_id):
    result = info / client_id
    result.mkdir(parents=True, exist_ok=True)
    return str(result)
dependencies._dependencies = SimpleNamespace(
    paths=SimpleNamespace(info_dir=info, tenant_path=tenant), logger=logging.getLogger('fixture'))
mode = sys.argv[2]
if mode in ('global', 'tenant'):
    performance.invalidate_vendas_cache(None if mode == 'global' else 'tenant-a')
    print(json.dumps({'invalidated': mode}), flush=True)
    sys.exit(0)
source = pathlib.Path(tenant('tenant-a')) / sys.argv[3]
@performance.cache_vendas_response('resumo', lambda args: [source], persistent=True)
def calculate(client_id, loja='Loja A', sku='SKU-A'):
    if mode == 'forbid':
        raise AssertionError('Calculation must not run after restart')
    with (info / 'calculations.txt').open('a', encoding='utf-8') as stream:
        stream.write('calculated\n')
    if source.suffix == '.db':
        with sqlite3.connect(source) as db:
            value = db.execute('SELECT sum(amount) FROM fixture').fetchone()[0]
    else:
        value = int(source.read_text(encoding='utf-8'))
    return {'success': True, 'rows': [{'sku': sku, 'itens': value}],
            'totals': {'itens': value}, 'meta': {'loja': loja}}
def emit():
    print(json.dumps({'payload': calculate('tenant-a'), 'stats': performance.vendas_cache_stats()}), flush=True)
emit()
if mode == 'wait':
    if sys.stdin.readline().strip() == 'again':
        emit()
'''


def summary(value=10):
    return {"success": True, "rows": [{"sku": "SKU-A", "itens": value}],
            "totals": {"itens": value}, "meta": {}}


def chart(value=10):
    return {"labels": ["2026-10-01"], "valores_vendas": [value], "quantidades_vendas": [1],
            "valores_devolucoes": [0], "quantidades_devolucoes": [0]}


def clear_ram():
    with performance._CACHE_LOCK:
        performance._RESPONSE_CACHE.clear()
        performance._FILE_CACHE.clear()
        performance._SCHEMA_CACHE.clear()
        performance._RESPONSE_FLIGHTS.clear()


@pytest.fixture
def context(tmp_path, monkeypatch):
    info = tmp_path / "info"

    def tenant(client_id):
        path = info / client_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    paths = SimpleNamespace(info_dir=info, tenant_path=tenant)
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=paths, logger=logging.getLogger("persistent-cache-fixture")))
    monkeypatch.setattr(disk, "_DISABLED", set())
    root = Path(tenant("tenant-a"))
    source = root / "source.txt"
    source.write_text("10", encoding="utf-8")
    clear_ram()
    yield SimpleNamespace(info=info, root=root, source=source, paths=paths)
    clear_ram()


def snapshots(context):
    return sorted((context.root / disk.CACHE_DIRECTORY).glob("*.json"))


def run_child(context, mode="calculate", source="source.txt"):
    result = subprocess.run(
        [sys.executable, "-B", "-c", CHILD, str(context.info), mode, source],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, encoding="utf-8", timeout=25,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def calculator(context, calls, *, namespace="resumo", provider=None, **options):
    @performance.cache_vendas_response(
        namespace, provider or (lambda args: [context.source]), persistent=True, **options,
    )
    def calculate(client_id, loja="Loja A", sku="SKU-A"):
        calls.append((client_id, loja, sku))
        value = int(context.source.read_text(encoding="utf-8"))
        return chart(value) if namespace == "grafico" else summary(value)
    return calculate


def test_independent_process_restores_without_calculation(context):
    first = run_child(context)
    second = run_child(context, "forbid")
    assert first["payload"] == second["payload"]
    assert second["stats"]["disk_hits"] == 1
    assert (context.info / "calculations.txt").read_text().splitlines() == ["calculated"]


@pytest.mark.parametrize("scope", ["global", "tenant"])
def test_invalidation_in_fresh_process_survives_restart(context, scope):
    run_child(context)
    run_child(context, scope)
    result = run_child(context)
    assert result["stats"]["disk_hits"] == 0
    assert len((context.info / "calculations.txt").read_text().splitlines()) == 2


@pytest.mark.parametrize("scope", ["global", "tenant"])
def test_other_process_invalidation_precedes_warm_ram_lookup(context, scope):
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", CHILD, str(context.info), "wait", "source.txt"],
        cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        warm = json.loads(process.stdout.readline())
        assert warm["stats"]["sets"] == 1
        run_child(context, scope)
        stdout, stderr = process.communicate("again\n", timeout=25)
        assert process.returncode == 0, stderr
        refreshed = json.loads(stdout.strip().splitlines()[-1])
        assert refreshed["stats"]["sets"] == 2
        assert refreshed["stats"]["disk_hits"] == 0
        assert len((context.info / "calculations.txt").read_text().splitlines()) == 2
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_sqlite_uncheckpointed_wal_commit_invalidates_persisted_result(context):
    path = context.root / "source.db"
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE fixture(amount REAL)")
        writer.execute("INSERT INTO fixture VALUES (10)")
        writer.commit()
        database_before = hashlib.sha256(path.read_bytes()).hexdigest()
        assert run_child(context, source="source.db")["payload"]["totals"]["itens"] == 10
        assert run_child(context, "forbid", "source.db")["stats"]["disk_hits"] == 1
        writer.execute("INSERT INTO fixture VALUES (5)")
        writer.commit()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == database_before
        result = run_child(context, source="source.db")
        assert result["payload"]["totals"]["itens"] == 15
        assert result["stats"]["disk_hits"] == 0
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert run_child(context, source="source.db")["payload"]["totals"]["itens"] == 15
    finally:
        writer.close()


def test_empty_and_absent_wal_have_same_signature(context):
    database = context.root / "empty.db"
    database.write_bytes(b"fixture")
    before = performance.vendas_sources_signature([database])
    Path(str(database) + "-wal").write_bytes(b"")
    assert performance.vendas_sources_signature([database]) == before


def test_filters_tenants_and_physical_root_are_isolated(context, tmp_path, monkeypatch):
    calls = []
    calculate = calculator(context, calls)
    calculate("tenant-a")
    calculate("tenant-a", loja="Loja B")
    calculate("tenant-a", sku="SKU-B")
    calculate("tenant-b")
    assert len(calls) == 4
    clear_ram()
    calculate("tenant-a")
    calculate("tenant-a", loja="Loja B")
    calculate("tenant-a", sku="SKU-B")
    calculate("tenant-b")
    assert len(calls) == 4
    original = context.root
    other_info = tmp_path / "other-info"
    other_tenant = other_info / "tenant-a"
    other_tenant.mkdir(parents=True)
    monkeypatch.setattr(context.paths, "info_dir", other_info)
    monkeypatch.setattr(context.paths, "tenant_path", lambda _client: str(other_tenant))
    clear_ram()
    calculate("tenant-a")
    assert len(calls) == 5 and original != other_tenant


def test_ram_expiration_rehydrates_disk_and_only_opted_namespaces_persist(context):
    calls = []
    calculate = calculator(context, calls)
    first = calculate("tenant-a")
    with performance._CACHE_LOCK:
        for key, (_, value) in list(performance._RESPONSE_CACHE.items()):
            performance._RESPONSE_CACHE[key] = (0, value)
    assert first == calculate("tenant-a")
    assert len(calls) == 1 and len(snapshots(context)) == 1
    other = calculator(context, calls, namespace="catalog-object", ttl_seconds=-1)
    other("tenant-a")
    clear_ram()
    other("tenant-a")
    assert len(calls) == 3 and len(snapshots(context)) == 1


def test_chart_day_rollover_and_calculation_revision_force_recalculation(context, monkeypatch):
    class Clock(date):
        current = date(2026, 10, 27)

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(performance, "date", Clock)
    calls = []
    calculate = calculator(context, calls, namespace="grafico")
    calculate("tenant-a")
    clear_ram()
    calculate("tenant-a")
    assert len(calls) == 1
    Clock.current = date(2026, 10, 28)
    calculate("tenant-a")
    assert len(calls) == 2
    monkeypatch.setattr(disk, "CALCULATION_VERSION", "fixture-next-calculation")
    clear_ram()
    calculate("tenant-a")
    assert len(calls) == 3


def test_source_provider_failure_bypasses_both_caches(context):
    calls = []
    fail = False

    def sources(_args):
        if fail:
            raise PermissionError("Synthetic source denied")
        return [context.source]

    calculate = calculator(context, calls, provider=sources)
    calculate("tenant-a")
    original = snapshots(context)[0].read_bytes()
    fail = True
    calculate("tenant-a")
    calculate("tenant-a")
    assert len(calls) == 3
    assert snapshots(context)[0].read_bytes() == original


def test_partial_result_never_replaces_last_valid_copy_or_enters_ram(context):
    partial = False
    calls = []

    @performance.cache_vendas_response("resumo", lambda _args: [context.source], persistent=True)
    def calculate(client_id):
        calls.append(client_id)
        if partial:
            performance.mark_vendas_read_incomplete()
        return summary(5 if partial else 10)

    calculate("tenant-a")
    original = snapshots(context)[0].read_bytes()
    clear_ram()
    context.source.write_text("changed", encoding="utf-8")
    partial = True
    assert calculate("tenant-a")["totals"]["itens"] == 5
    assert calculate("tenant-a")["totals"]["itens"] == 5
    assert len(calls) == 3 and not performance._RESPONSE_CACHE
    assert len(snapshots(context)) == 1 and snapshots(context)[0].read_bytes() == original
    partial = False
    calculate("tenant-a")
    assert len(snapshots(context)) == 2


def test_nested_singleflight_propagates_partial_confidence_to_both_outer_requests(context, monkeypatch):
    entered, follower, release = threading.Event(), threading.Event(), threading.Event()
    original_result = performance.Future.result
    calls = []

    def waiting(self, *args, **kwargs):
        if not self.done():
            follower.set()
        return original_result(self, *args, **kwargs)

    monkeypatch.setattr(performance.Future, "result", waiting)

    @performance.cache_vendas_response("composition_catalog", lambda _args: [context.source])
    def catalog(client_id):
        calls.append(client_id)
        entered.set()
        assert release.wait(5)
        performance.mark_vendas_read_incomplete()
        # Represents a legacy field-proof fallback without public warnings.
        return SimpleNamespace(value=5)

    @performance.cache_vendas_response("resumo", lambda _args: [context.source], persistent=True)
    def calculate(client_id, loja):
        return summary(catalog(client_id).value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(calculate, "tenant-a", "Loja A")
        assert entered.wait(5)
        second = pool.submit(calculate, "tenant-a", "Loja B")
        try:
            assert follower.wait(5)
        finally:
            release.set()
        assert owner.result(timeout=5) == second.result(timeout=5) == summary(5)
    assert calls == ["tenant-a"]
    assert not snapshots(context) and not performance._RESPONSE_CACHE


@pytest.mark.parametrize("mutation", ["source", "tenant", "global", "inventory"])
def test_change_during_calculation_cannot_publish_stale_snapshot(context, mutation):
    entered, release = threading.Event(), threading.Event()
    inventory = [context.source]
    calls = []

    @performance.cache_vendas_response("resumo", lambda _args: inventory, persistent=True)
    def calculate(client_id):
        calls.append(client_id)
        value = int(context.source.read_text())
        if len(calls) == 1:
            entered.set()
            assert release.wait(35)
        return summary(value)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(calculate, "tenant-a")
        assert entered.wait(5)
        try:
            if mutation == "source":
                context.source.write_text("20")
            elif mutation == "inventory":
                added = context.root / "added.txt"
                added.write_text("1")
                inventory.append(added)
            else:
                run_child(context, mutation)
        finally:
            release.set()
        assert result.result(timeout=5)["totals"]["itens"] == 10
    assert not snapshots(context) and not performance._RESPONSE_CACHE
    calculate("tenant-a")
    assert len(calls) == 2 and len(snapshots(context)) == 1


@pytest.mark.parametrize("corruption", ["truncated", "checksum", "schema", "generation", "payload", "oversized", "huge-timestamp"])
def test_corrupt_cache_falls_back_to_live_calculation(context, corruption, monkeypatch):
    calls = []
    calculate = calculator(context, calls)
    calculate("tenant-a")
    path = snapshots(context)[0]
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "truncated":
        path.write_text('{"schema":', encoding="utf-8")
    elif corruption == "oversized":
        monkeypatch.setattr(disk, "MAX_ENTRY_BYTES", 10)
    else:
        if corruption == "checksum":
            envelope["payload"]["totals"]["itens"] = 999
        elif corruption == "schema":
            envelope["schema"] = -1
        elif corruption == "generation":
            envelope["generation"] = ["0" * 32, "0" * 32]
        elif corruption == "huge-timestamp":
            envelope["created_at"] = 10 ** 400
        else:
            envelope["payload"]["rows"] = [None]
            envelope["checksum"] = disk._digest(envelope["payload"])
        path.write_text(json.dumps(envelope), encoding="utf-8")
    clear_ram()
    assert calculate("tenant-a")["totals"]["itens"] == 10
    assert len(calls) == 2


def test_atomic_replace_failure_preserves_previous_copy_and_live_fallback(context, monkeypatch):
    generation = disk.capture(context.info, context.root)
    key = "a" * 64
    assert disk.save(generation, "resumo", key, summary(10), lambda: True)
    path = context.root / disk.CACHE_DIRECTORY / (key + ".json")
    original = path.read_bytes()
    replace = disk.os.replace

    def fail_snapshot(source, target):
        if str(target).endswith(".json"):
            raise PermissionError("Synthetic atomic replace denied")
        return replace(source, target)

    monkeypatch.setattr(disk.os, "replace", fail_snapshot)
    assert not disk.save(generation, "resumo", key, summary(99), lambda: True)
    assert path.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))
    calls = []
    assert calculator(context, calls)("tenant-a")["totals"]["itens"] == 10
    assert len(calls) == 1


def test_failed_invalidation_receipt_prevents_resurrection_in_new_process(context, monkeypatch):
    run_child(context)
    atomic = disk._atomic_write

    def fail_revision(path, *args, **kwargs):
        if path.name == "tenant.revision":
            raise PermissionError("Synthetic revision replace denied")
        return atomic(path, *args, **kwargs)

    monkeypatch.setattr(disk, "_atomic_write", fail_revision)
    assert not disk.invalidate(context.root)
    assert list((context.root / disk.CACHE_DIRECTORY).glob(".pending-*"))
    assert disk.capture(context.info, context.root) is None
    result = run_child(context)
    assert result["stats"]["disk_hits"] == 0
    assert len((context.info / "calculations.txt").read_text().splitlines()) == 2


def test_retention_is_bounded_and_oversized_or_failed_stock_payload_is_rejected(context, monkeypatch):
    generation = disk.capture(context.info, context.root)
    monkeypatch.setattr(disk, "MAX_ENTRIES", 2)
    for index in range(4):
        assert disk.save(generation, "resumo", format(index, "064x"), summary(index), lambda: True)
    assert len(snapshots(context)) == 2
    graph = {**chart(), "estoque_meta": {"success": False}}
    assert not disk.save(generation, "grafico", "e" * 64, graph, lambda: True)
    monkeypatch.setattr(disk, "MAX_ENTRY_BYTES", 10)
    assert not disk.save(generation, "resumo", "f" * 64, summary(), lambda: True)


def test_unavailable_cache_directory_keeps_live_reads_available(context, monkeypatch):
    def denied(_root):
        raise PermissionError("Synthetic cache storage denied")

    monkeypatch.setattr(disk, "_directory", denied)
    calls = []
    calculate = calculator(context, calls)
    assert calculate("tenant-a") == calculate("tenant-a")
    assert len(calls) == 2 and not performance._RESPONSE_CACHE


def stock_fixture(context, monkeypatch):
    from backend.services import estoque_historico as stock

    monkeypatch.setattr(stock, "get_tenant_path", context.paths.tenant_path)
    (context.root / "lojas_config.json").write_text(
        json.dumps([{"store_id": "store-a", "nome": "Loja A"}]), encoding="utf-8")
    stock._registrar_snapshot_historico_estoque(
        "tenant-a", "Loja A", [{"sku": "SKU-A", "saldo_loja": 20}],
        event_id="fixture-committed", recorded_at="2026-10-01T12:00:00-03:00",
    )
    # The application binds this schema helper at startup; this isolated reader
    # fixture supplies its existing table without importing the real runtime.
    with sqlite3.connect(context.root / "estoque_historico.db") as connection:
        connection.execute("CREATE TABLE estoque_lancamentos (loja_sync TEXT, sku TEXT, data_ref TEXT, entrada REAL, saida REAL)")
    monkeypatch.setattr(stock, "_garantir_tabela_lancamentos_estoque", lambda _client: None, raising=False)
    return stock


def stock_calculator(context, stock, calls):
    @performance.cache_vendas_response(
        "grafico", lambda args: performance.vendas_source_paths(args["client_id"], include_stock=True),
        persistent=True,
    )
    def calculate(client_id):
        calls.append(client_id)
        result = stock._vendas_series_estoque_historico(
            client_id=client_id, loja="Loja A", intervalo="dia",
            data_inicio_ref=datetime(2026, 10, 1), data_fim_ref=datetime(2026, 10, 1),
            chaves_periodo=["2026-10-01"], mostrar_estoque_geral=True,
            mostrar_estoque_sku=False, sku=None,
        )
        return {**chart(), **result}
    return calculate


def test_stock_event_failure_once_does_not_freeze_successful_legacy_fallback(context, monkeypatch):
    stock = stock_fixture(context, monkeypatch)
    database = context.root / "estoque_historico.db"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE estoque_historico SET saldo_loja = 10")
    original = stock._vendas_series_estoque_eventos
    events_calls = []

    def events(*args, **kwargs):
        events_calls.append(True)
        if len(events_calls) == 1:
            raise sqlite3.OperationalError("Synthetic one-time events failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(stock, "_vendas_series_estoque_eventos", events)
    calls = []
    calculate = stock_calculator(context, stock, calls)
    first = calculate("tenant-a")
    assert first["estoque_meta"]["success"] is True and first["estoque_geral"] == [10]
    assert not snapshots(context) and not performance._RESPONSE_CACHE
    second = calculate("tenant-a")
    assert second["estoque_geral"] == [20] and second["estoque_meta"]["fonte"] == "eventos"
    clear_ram()
    assert calculate("tenant-a") == second
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["staging", "recovery"])
def test_pending_stock_is_not_persisted_until_recovery_completes(context, monkeypatch, failure):
    stock = stock_fixture(context, monkeypatch)
    csv = context.root / "produtos_compilado.csv"
    csv.write_text("sku,saldo_loja\nSKU-A,30\n", encoding="utf-8")
    stock._registrar_snapshot_historico_estoque(
        "tenant-a", "Loja A", [{"sku": "SKU-A", "saldo_loja": 30}],
        event_id="fixture-pending", recorded_at="2026-10-01T13:00:00-03:00",
        status="pending", csv_hash=hashlib.sha256(csv.read_bytes()).hexdigest(),
    )
    temporary = context.root / ".produtos_compilado.fixture-pending.tmp"
    original = stock._atualizar_projecao_legada_evento
    fail = True
    if failure == "staging":
        temporary.write_text("staged", encoding="utf-8")
    else:
        def projection(*args, **kwargs):
            if fail:
                raise sqlite3.OperationalError("Synthetic recovery failure")
            return original(*args, **kwargs)
        monkeypatch.setattr(stock, "_atualizar_projecao_legada_evento", projection)
    calls = []
    calculate = stock_calculator(context, stock, calls)
    assert calculate("tenant-a")["estoque_geral"] == [20]
    assert not snapshots(context) and not performance._RESPONSE_CACHE
    fail = False
    if temporary.exists():
        temporary.unlink()
    assert calculate("tenant-a")["estoque_geral"] == [30]
    assert calculate("tenant-a")["estoque_geral"] == [30]
    clear_ram()
    assert calculate("tenant-a")["estoque_geral"] == [30]
    assert len(calls) == 3


def test_stock_csv_is_in_signature_even_when_no_database_file_changes(context):
    paths = performance.vendas_source_paths("tenant-a", include_stock=True)
    csv = context.root / "produtos_compilado.csv"
    assert str(csv) in paths
    before = performance.vendas_sources_signature(paths)
    csv.write_text("sku,saldo_loja\nSKU-A,30\n", encoding="utf-8")
    assert performance.vendas_sources_signature(paths) != before


def test_virtual_store_mapping_read_and_write_failure_is_not_cached(context, monkeypatch):
    from backend.services import bling_vendas

    monkeypatch.setattr(bling_vendas, "get_tenant_path", context.paths.tenant_path)
    path = context.root / "lojas_virtuais_map.json"
    path.write_text(json.dumps({"loja_id": {"fixture-id": "Synthetic channel"}}), encoding="utf-8")
    bling_vendas._carregar_mapeamento_lojas_virtuais_cliente("tenant-a")
    original = path.read_bytes()
    real_open = builtins.open
    denied = True

    def guarded_open(filename, *args, **kwargs):
        if denied and os.path.abspath(os.fspath(filename)) == str(path):
            raise PermissionError("Synthetic map access denied")
        return real_open(filename, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    calls = []

    @performance.cache_vendas_response("resumo", lambda _args: [path], persistent=True)
    def calculate(client_id):
        calls.append(client_id)
        mapping = bling_vendas._carregar_mapeamento_lojas_virtuais_cliente(client_id)
        return summary(int("fixture-id" in mapping["loja_id"]))

    assert calculate("tenant-a")["totals"]["itens"] == 0
    assert not snapshots(context) and not performance._RESPONSE_CACHE
    assert path.read_bytes() == original
    denied = False
    assert calculate("tenant-a")["totals"]["itens"] == 1
    clear_ram()
    assert calculate("tenant-a")["totals"]["itens"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["read", "malformed"])
def test_active_store_fallback_does_not_freeze_wrong_pareto(context, monkeypatch, failure):
    from backend.modules.vendas import analytics

    config = context.root / "lojas_config.json"
    config.write_text(json.dumps([{"nome": "Loja A"}]), encoding="utf-8")
    rows = [("one", "", "", "", "", "", "Loja A"),
            ("two", "", "", "", "", "", "Loja excluída")]
    real_open = builtins.open
    denied = True

    def guarded_open(filename, *args, **kwargs):
        if denied and os.path.abspath(os.fspath(filename)) == str(config):
            if failure == "read":
                raise PermissionError("Synthetic active-store read denied")
            from io import StringIO
            return StringIO('{}')
        return real_open(filename, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    calls = []

    @performance.cache_vendas_response("grafico", lambda _args: [config], persistent=True)
    def calculate(client_id):
        calls.append(client_id)
        return chart(len(analytics._filtrar_vendas_lojas_ativas(rows, client_id, None)))

    assert calculate("tenant-a")["valores_vendas"] == [2]
    assert not snapshots(context) and not performance._RESPONSE_CACHE
    denied = False
    assert calculate("tenant-a")["valores_vendas"] == [1]
    clear_ram()
    assert calculate("tenant-a")["valores_vendas"] == [1]
    assert len(calls) == 2
