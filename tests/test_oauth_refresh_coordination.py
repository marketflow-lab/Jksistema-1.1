import multiprocessing
import threading

import pytest
from backend.services import store_coordination as coordination


def _hold_gate(path, ready, release):
    with coordination.oauth_refresh_lock(path, "store-a"):
        ready.set()
        if not release.wait(15):
            raise AssertionError("Test gate was not released")


def test_oauth_is_single_flight_between_processes_without_blocking_store_commit(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_gate, args=(str(tmp_path), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(coordination.StoreCoordinationError) as error:
            with coordination.oauth_refresh_lock(tmp_path, "store-a", timeout_seconds=.03):
                pytest.fail("Concurrent token exchange")
        assert error.value.code == "locked"
        with coordination.oauth_refresh_lock(tmp_path, "store-b", timeout_seconds=.1):
            assert not coordination.coordination_active()
        with coordination.oauth_refresh_lock(tmp_path, "store-a", provider="bling", timeout_seconds=.1):
            pass
        with coordination.store_lock(tmp_path, timeout_seconds=.1):
            pass
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


def test_oauth_commit_uses_fresh_budget_and_gate_releases_after_exception(tmp_path):
    with pytest.raises(ValueError):
        with coordination.oauth_refresh_lock(tmp_path, "store-a", timeout_seconds=.01):
            with coordination.store_lock(tmp_path):
                assert coordination.remaining_timeout() > 9
            raise ValueError("synthetic exchange failure")
    acquired = []
    def acquire():
        with coordination.oauth_refresh_lock(tmp_path, "store-a", timeout_seconds=.1):
            acquired.append(True)
    thread = threading.Thread(target=acquire)
    thread.start()
    thread.join(1)
    assert acquired == [True]


def test_refresh_must_not_start_while_holding_store_locks(tmp_path):
    with coordination.store_lock(tmp_path):
        with pytest.raises(coordination.StoreCoordinationError) as error:
            with coordination.oauth_refresh_lock(tmp_path, "store-a"):
                pytest.fail("Lock order inverted")
        assert error.value.code == "lock_order"
