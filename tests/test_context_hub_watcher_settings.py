from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from backend.modules.context_hub import bootstrap, settings, watchers, watcher_settings
from backend.modules.context_hub.contracts import ContextHubConflictError, ContextHubValidationError
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import configure_context_hub
from backend.modules.context_hub.state import CONTEXT_HUB_STATE


@pytest.fixture
def watcher_env(tmp_path):
    previous = CONTEXT_HUB_STATE.runtime_config
    base, info = tmp_path / "app", tmp_path / "info"
    base.mkdir()
    info.mkdir()
    config = configure_context_hub(base_dir=base, info_root=info, surface="test")
    bootstrap.bootstrap_context_hub("tenant-a", base_dir=base, info_root=info)
    settings.update_settings("tenant-a", watch_enabled=True, debounce_seconds=1)
    paths = _tenant_paths("tenant-a", info_root=info)
    yield config, paths
    watchers.stop_all_context_hub_watchers()
    with CONTEXT_HUB_STATE.config_guard:
        CONTEXT_HUB_STATE.runtime_config = previous


def test_watcher_cycles_are_read_only_without_bootstrap_or_connect(watcher_env, monkeypatch):
    config, paths = watcher_env
    original_connect = sqlite3.connect
    statements = []

    def read_only_connection(*args, **kwargs):
        assert kwargs["uri"] is True and args[0].endswith("?mode=ro")
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    def forbidden(*args, **kwargs):
        pytest.fail("The watcher must not bootstrap or use the read-write connector during normal cycles.")

    monkeypatch.setattr(watcher_settings.sqlite3, "connect", read_only_connection)
    monkeypatch.setattr(watchers, "bootstrap_context_hub", forbidden)
    monkeypatch.setattr(settings, "bootstrap_context_hub", forbidden)
    monkeypatch.setattr(settings, "_connect", forbidden)
    checkpoint = None
    for _ in range(8):
        current, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface, checkpoint)
        assert current["watch_enabled"] is True
    assert len(statements) == 24
    assert all(sql.startswith(("SELECT ", "PRAGMA schema_version")) for sql in statements)


@pytest.mark.parametrize("change", ["missing", "schema", "journal", "replace"])
def test_watcher_suspends_on_database_replacement_schema_missing_or_journal(watcher_env, change):
    config, paths = watcher_env
    _current, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface)
    if change == "missing":
        paths.db_path.unlink()
    elif change == "schema":
        with sqlite3.connect(paths.db_path) as connection:
            connection.execute("CREATE TABLE independent_table(id INTEGER)")
    elif change == "journal":
        paths.journal_path.write_text("{}", encoding="utf-8")
    else:
        replacement = paths.db_path.with_name("replacement.db")
        with closing(sqlite3.connect(paths.db_path)) as source, closing(sqlite3.connect(replacement)) as target:
            source.backup(target)
        replacement.replace(paths.db_path)
    with pytest.raises(watcher_settings._WatcherRecoveryRequired):
        watcher_settings._read_watcher_settings(paths, config.surface, checkpoint)


def test_settings_pause_is_read_fresh_without_triggering_recovery(watcher_env):
    config, paths = watcher_env
    _current, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface)
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute("UPDATE context_hub_settings SET paused=1 WHERE singleton_id=1")
    current, unchanged = watcher_settings._read_watcher_settings(paths, config.surface, checkpoint)
    assert current["paused"] is True and unchanged == checkpoint


def test_real_sqlite_busy_read_is_distinguished_from_recovery(watcher_env):
    config, paths = watcher_env
    with closing(sqlite3.connect(paths.db_path, isolation_level=None)) as owner:
        owner.execute("PRAGMA journal_mode=DELETE")
        owner.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(watcher_settings._WatcherReadBusy):
                watcher_settings._read_watcher_settings(paths, config.surface)
        finally:
            owner.execute("ROLLBACK")


class Ticks:
    def __init__(self, cycles=9):
        self.tick = 0
        self.cycles = cycles

    def wait(self, seconds):
        assert seconds == 1.0
        self.tick += 1
        return self.tick > self.cycles


def test_watcher_waits_five_seconds_before_recovery_and_suspends_scanning(watcher_env, monkeypatch):
    config, paths = watcher_env
    ticks, scans, recoveries = Ticks(), [], []
    valid, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface)

    def read(*args):
        if ticks.tick < 6:
            raise watcher_settings._WatcherRecoveryRequired("Recovery required.")
        return valid, checkpoint

    monkeypatch.setattr(watchers.time, "monotonic", lambda: float(ticks.tick))
    monkeypatch.setattr(watchers, "_read_watcher_settings", read)
    monkeypatch.setattr(watchers, "_recover_watcher_state", lambda *_args: recoveries.append(ticks.tick) or checkpoint)
    monkeypatch.setattr(watchers, "scan_context_hub_changes", lambda *_args, **_kw: scans.append(ticks.tick) or {"changed": False})
    watchers._watch_loop(config, paths, ticks, checkpoint)
    assert recoveries == [6]
    assert scans == [6, 7, 8, 9]


def test_busy_reads_never_trigger_migrations_or_bootstrap(watcher_env, monkeypatch):
    config, paths = watcher_env
    ticks = Ticks(cycles=3)
    valid, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface)

    def read(*args):
        if ticks.tick == 1:
            raise watcher_settings._WatcherReadBusy("Busy.")
        return valid, checkpoint

    def forbidden(*args):
        pytest.fail("Busy reads must not attempt migrations.")

    monkeypatch.setattr(watchers, "_read_watcher_settings", read)
    monkeypatch.setattr(watchers, "_recover_watcher_state", forbidden)
    monkeypatch.setattr(watchers, "scan_context_hub_changes", lambda *_a, **_kw: {"changed": False})
    watchers._watch_loop(config, paths, ticks, checkpoint)


def test_pause_discards_pending_debounce_and_new_changes_rebuild_once(watcher_env, monkeypatch):
    config, paths = watcher_env
    ticks, rebuilt = Ticks(cycles=7), []
    valid, checkpoint = watcher_settings._read_watcher_settings(paths, config.surface)
    valid["debounce_seconds"] = 2

    def read(*args):
        return valid | {"paused": ticks.tick in (2, 3)}, checkpoint

    monkeypatch.setattr(watchers.time, "monotonic", lambda: float(ticks.tick))
    monkeypatch.setattr(watchers, "_read_watcher_settings", read)
    monkeypatch.setattr(watchers, "scan_context_hub_changes", lambda *_a, **_kw: {"changed": ticks.tick in (1, 5)})
    monkeypatch.setattr(watchers, "rebuild_context", lambda *_a, **_kw: rebuilt.append(ticks.tick))
    watchers._watch_loop(config, paths, ticks, checkpoint)
    assert rebuilt == [7]


def test_watcher_recovery_preserves_file_lock_exclusivity(watcher_env, monkeypatch):
    config, paths = watcher_env
    attempts = []
    monkeypatch.setattr(watchers, "bootstrap_context_hub", lambda *_a, **_kw: attempts.append(1))
    with _exclusive_file_lock(paths, timeout=0.0):
        with pytest.raises(ContextHubConflictError):
            watchers._recover_watcher_state(config, paths)
    assert not attempts


def test_watcher_recovery_preserves_thread_lock_exclusivity(watcher_env, monkeypatch):
    config, paths = watcher_env
    entered, release = threading.Event(), threading.Event()

    def locked():
        with _tenant_thread_lock(paths):
            entered.set()
            assert release.wait(5)

    owner = threading.Thread(target=locked)
    owner.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ContextHubConflictError):
            watchers._recover_watcher_state(config, paths)
    finally:
        release.set()
        owner.join(timeout=5)


def test_watcher_recovery_bootstraps_missing_database_under_locks(watcher_env, monkeypatch):
    config, paths = watcher_env
    paths.db_path.unlink()
    original = watchers.bootstrap_context_hub
    calls = []

    def guarded(*args, **kwargs):
        assert paths.lock_path.is_file()
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(watchers, "bootstrap_context_hub", guarded)
    checkpoint = watchers._recover_watcher_state(config, paths)
    current, unchanged = watcher_settings._read_watcher_settings(paths, config.surface, checkpoint)
    assert calls == [1] and unchanged == checkpoint
    assert current["watch_enabled"] is False


def test_watcher_revalidates_junctions_before_reading_database(watcher_env, monkeypatch):
    config, paths = watcher_env
    from backend.modules.context_hub import path_safety
    original = path_safety._is_link_or_junction
    monkeypatch.setattr(path_safety, "_is_link_or_junction", lambda path: path == paths.internal_dir or original(path))
    with pytest.raises(ContextHubValidationError):
        watcher_settings._read_watcher_settings(paths, config.surface)
