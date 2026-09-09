from __future__ import annotations

import multiprocessing
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.services import store_coordination as coordination
from backend.services.path_coordination import path_lock_for
from backend.services.sqlite_coordination import configure_sqlite_connection


def _hold_in_process(path, ready, release, kind="stores"):
    if kind == "photos":
        from backend.services.cadastro_fotos_coordenacao import bloquear_transicao_fotos_tenant
        scope = bloquear_transicao_fotos_tenant(path)
    else:
        scope = coordination.store_lock(path)
    with scope:
        ready.set()
        if not release.wait(10):
            raise RuntimeError("Test release was not received")


@contextmanager
def _other_process_holding(path, kind="stores"):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_in_process, args=(str(path), ready, release, kind))
    process.start()
    try:
        assert ready.wait(15), "Lock owner failed to start"
        yield process
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0


def test_same_tenant_is_exclusive_across_processes(tmp_path):
    with _other_process_holding(tmp_path):
        with pytest.raises(coordination.StoreCoordinationError) as caught:
            with coordination.store_lock(tmp_path, timeout_seconds=.05):
                pytest.fail("A second process entered the transaction")
        assert caught.value.code == "locked"
    with coordination.store_lock(tmp_path, timeout_seconds=.1):
        assert coordination.coordination_active()
    assert not coordination.coordination_active()


def test_other_tenant_and_photo_namespace_remain_independent(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    with _other_process_holding(first):
        with coordination.store_lock(second, timeout_seconds=.1):
            pass
    with _other_process_holding(first, "photos"):
        with coordination.store_lock(first, timeout_seconds=.1):
            pass


def test_nested_aliases_keep_original_deadline_and_release_on_error(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(coordination.time, "monotonic", lambda: now[0])
    with pytest.raises(ValueError):
        with coordination.store_lock(tmp_path, timeout_seconds=1):
            now[0] += .75
            with coordination.store_lock(tmp_path / ".", timeout_seconds=20):
                assert coordination.remaining_timeout() == pytest.approx(.25)
                raise ValueError("Synthetic transaction failure")
    assert not coordination.coordination_active()
    with coordination.store_lock(tmp_path, timeout_seconds=.1):
        pass


def test_path_contention_honors_store_budget_without_changing_mutex(tmp_path):
    path = tmp_path / "catalog.csv"
    ready, release = threading.Event(), threading.Event()

    def holder():
        with path_lock_for(path):
            ready.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert ready.wait(2)
        with coordination.store_lock(tmp_path, timeout_seconds=.05):
            with pytest.raises(coordination.StoreCoordinationError) as caught:
                with coordination.coordinated_path_lock(path):
                    pytest.fail("Path mutex identity was not retained")
            assert caught.value.code == "locked"
            assert coordination.remaining_timeout() < .01
    finally:
        release.set()
        thread.join(2)


def test_path_coordination_outside_store_preserves_original_lock_protocol(monkeypatch):
    calls = []

    class ExistingMutex:
        def __enter__(self):
            calls.append("enter")

        def __exit__(self, *_args):
            calls.append("exit")

        def acquire(self, **_kwargs):
            pytest.fail("Outside store coordination must not override the mutex timeout")

    monkeypatch.setattr(coordination, "path_lock_for", lambda _: ExistingMutex())
    with coordination.coordinated_path_lock("synthetic"):
        assert calls == ["enter"]
    assert calls == ["enter", "exit"]


def test_local_wait_reduces_cross_process_budget(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(coordination.time, "monotonic", lambda: now[0])
    seen = []

    class SimulatedLocalMutex:
        def acquire(self, **kwargs):
            seen.append(kwargs["timeout"])
            now[0] += .7
            return True

        def release(self):
            pass

    @contextmanager
    def cross_process(*_args, **_kwargs):
        seen.append(coordination.remaining_timeout())
        yield

    monkeypatch.setattr(coordination, "path_lock_for", lambda _: SimulatedLocalMutex())
    monkeypatch.setattr(coordination, "_windows_mutex", cross_process)
    monkeypatch.setattr(coordination, "_bloquear_arquivo_posix", cross_process)
    with coordination.store_lock(tmp_path, timeout_seconds=1):
        assert seen == pytest.approx([1, .3])


def test_store_order_and_legacy_reentrance_are_enforced(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    with coordination.legacy_resource_lock(tmp_path):
        with coordination.store_lock(first):
            with coordination.legacy_resource_lock(tmp_path):
                with coordination.store_lock(second):
                    pass
    with coordination.store_lock(second):
        with pytest.raises(coordination.StoreCoordinationError) as caught:
            with coordination.store_lock(first):
                pass
        assert caught.value.code == "lock_order"
        with pytest.raises(coordination.StoreCoordinationError):
            with coordination.legacy_resource_lock(tmp_path):
                pass


def test_missing_or_linked_tenant_fails_closed(tmp_path):
    with pytest.raises(coordination.StoreCoordinationError) as caught:
        with coordination.store_lock(tmp_path / "missing"):
            pass
    assert caught.value.code == "unsafe_tenant"
    link = tmp_path / "alias"
    target = tmp_path / "real"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(coordination.StoreCoordinationError):
        with coordination.store_lock(link):
            pass


def test_sqlite_busy_timeout_uses_remaining_budget_only_inside_store(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(coordination.time, "monotonic", lambda: now[0])
    with sqlite3.connect(":memory:") as conn:
        configure_sqlite_connection(conn, busy_timeout_ms=15000)
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
        with coordination.store_lock(tmp_path, timeout_seconds=1):
            now[0] += .75
            configure_sqlite_connection(conn, busy_timeout_ms=15000)
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250


def test_production_consumers_do_not_use_global_store_lock():
    root = Path(__file__).resolve().parents[1] / "backend"
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if p.name != "integracoes.py" and "_LOJAS_CONFIG_LOCK" in p.read_text(encoding="utf-8-sig")]
    assert offenders == []
