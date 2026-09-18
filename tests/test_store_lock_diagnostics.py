import logging
import queue
import threading
from types import SimpleNamespace

from backend.services import store_coordination as coordination
from backend.services import store_lock_diagnostics as diagnostics


def test_slow_hold_and_timeout_are_correlated_without_paths(tmp_path, monkeypatch, caplog):
    events = []
    monkeypatch.setattr(diagnostics, "_enqueue", events.append)
    with diagnostics.operation_scope("sync_bundle"):
        with coordination.store_lock(tmp_path):
            pass
    events = [event for event in events if event[1]["namespace"] == "stores"]
    record = events[0][1]
    assert record["operation"] == "sync_bundle"
    assert len(record["resource"]) == 64
    assert [event[0] for event in events] == ["begin", "acquired", "finished"]
    active, limiter = {}, {}
    for event in events[:2]:
        diagnostics._consume(event, active, limiter)
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: record["started"] + 2)
    with caplog.at_level(logging.WARNING):
        diagnostics._observe_active(active, limiter)
        timeout_record = {**record, "attempt_id": "another-attempt"}
        diagnostics._consume(("finished", timeout_record, record["started"] + 3, "timeout"), active, limiter)
    assert "phase=holding" in caplog.text and "result=timeout" in caplog.text
    assert record["operation_id"] in caplog.text
    assert str(tmp_path) not in caplog.text


def test_untrusted_labels_and_identifiers_never_enter_diagnostics(monkeypatch):
    events = []
    monkeypatch.setattr(diagnostics, "_enqueue", events.append)
    with diagnostics.operation_scope("secret-token shop-name C:/private"):
        assert diagnostics.begin("stores", "C:/private") is None
        record = diagnostics.begin("stores", "a" * 64)
    assert record["operation"] == "stores_access"
    assert "secret-token" not in str(events)


def test_full_queue_or_logging_failure_does_not_affect_lock(tmp_path, monkeypatch):
    full = queue.Queue(maxsize=1)
    full.put("sentinel")
    monkeypatch.setattr(diagnostics, "_QUEUE", full)
    monkeypatch.setattr(diagnostics, "_WORKER", SimpleNamespace(is_alive=lambda: True))
    with coordination.store_lock(tmp_path):
        assert coordination.coordination_active()
    assert full.qsize() == 1
    def broken(*args):
        raise RuntimeError("logging unavailable")
    monkeypatch.setattr(diagnostics._LOGGER, "warning", broken)
    record = diagnostics.begin("stores", "b" * 64)
    diagnostics._emit(record, "waiting", 10, "timeout", {})


def test_timeout_in_another_thread_emits_safe_diagnostics(tmp_path, monkeypatch):
    events, caught = [], []
    monkeypatch.setattr(diagnostics, "_enqueue", events.append)
    def wait():
        try:
            with coordination.store_lock(tmp_path, timeout_seconds=.02):
                raise AssertionError("Writer lock bypassed")
        except coordination.StoreCoordinationError as exc:
            caught.append(exc.code)
    with coordination.store_lock(tmp_path):
        worker = threading.Thread(target=wait)
        worker.start()
        worker.join(1)
        assert not worker.is_alive()
    assert caught == ["locked"]
    assert any(event[0] == "finished" and event[3] == "timeout" for event in events)


def test_rate_limit_keeps_suppressed_count(monkeypatch, caplog):
    monkeypatch.setattr(diagnostics, "_enqueue", lambda message: None)
    clock = [100.0]
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: clock[0])
    record, limiter = diagnostics.begin("stores", "a" * 64), {}
    with caplog.at_level(logging.WARNING):
        diagnostics._emit(record, "waiting", 10, "timeout", limiter)
        diagnostics._emit(record, "waiting", 10, "timeout", limiter)
        clock[0] += 31
        diagnostics._emit(record, "waiting", 10, "timeout", limiter)
    assert len(caplog.records) == 2
    assert "suppressed=1" in caplog.records[-1].message
