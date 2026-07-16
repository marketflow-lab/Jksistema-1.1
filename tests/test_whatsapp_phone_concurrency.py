from __future__ import annotations

import json
import threading
import time

import pytest

from backend.services import codex_console, codex_whatsapp_agents, whatsapp_bridge


def _config(workers: int = 2) -> dict:
    return {
        **whatsapp_bridge._default_config(),
        "enabled": True,
        "agent_architecture": "dual_codex",
        "worker_url": "https://worker.example",
        "bridge_token": "token",
        "machine_id": "machine-1",
        "client_id": "client-1",
        "username": "admin",
        "conversation_worker_count": workers,
        "conversation_runtime_pool_size": workers,
    }


def _wait_dispatcher(timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with whatsapp_bridge.PHONE_DISPATCH_LOCK:
            if not whatsapp_bridge.PHONE_DISPATCH_EVENT_IDS and not whatsapp_bridge.PHONE_DISPATCH_ACTIVE:
                return
        time.sleep(0.01)
    raise AssertionError("phone dispatcher did not become idle")


@pytest.fixture(autouse=True)
def _clean_dispatcher():
    def reset() -> None:
        with whatsapp_bridge.PHONE_DISPATCH_LOCK:
            executor = whatsapp_bridge.PHONE_DISPATCH_EXECUTOR
            old = list(whatsapp_bridge.PHONE_DISPATCH_OLD_EXECUTORS)
            whatsapp_bridge.PHONE_DISPATCH_EXECUTOR = None
            whatsapp_bridge.PHONE_DISPATCH_EXECUTOR_WORKERS = 0
            whatsapp_bridge.PHONE_DISPATCH_INFLIGHT = 0
            whatsapp_bridge.PHONE_DISPATCH_OLD_EXECUTORS.clear()
            whatsapp_bridge.PHONE_DISPATCH_QUEUES.clear()
            whatsapp_bridge.PHONE_DISPATCH_ACTIVE.clear()
            whatsapp_bridge.PHONE_DISPATCH_EVENT_IDS.clear()
            whatsapp_bridge.PHONE_DISPATCH_TICK_IDS.clear()
            whatsapp_bridge.PHONE_DISPATCH_FUTURES.clear()
            whatsapp_bridge.PHONE_DISPATCH_ACCEPTING = True
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        for item in old:
            item.shutdown(wait=True, cancel_futures=True)

    reset()
    yield
    reset()


def test_different_phones_run_luna_events_concurrently(monkeypatch):
    started = 0
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()

    def process(_config_value, _state, _message):
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                both_started.set()
        release.wait(2)

    monkeypatch.setattr(whatsapp_bridge, "_process_message", process)
    monkeypatch.setattr(whatsapp_bridge, "_record_message_timing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(whatsapp_bridge, "_post_message_result", lambda *_args, **_kwargs: {"status": "queued"})
    config = _config(2)
    whatsapp_bridge._configure_phone_dispatcher(2)
    for index in (1, 2):
        message = {
            "message_id": f"wamid-{index}",
            "wa_id": f"551199999990{index}",
            "subject_id": f"subject-{index}",
            "client_id": "client-1",
            "username": "admin",
        }
        assert whatsapp_bridge._enqueue_phone_event(
            config,
            {},
            kind="inbound",
            event_id=message["message_id"],
            message=message,
            message_id=message["message_id"],
        )
    try:
        assert both_started.wait(1.0), "different phones were serialized"
    finally:
        release.set()
    _wait_dispatcher()


def test_same_phone_is_strict_fifo(monkeypatch):
    order: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def process(_config_value, _state, message):
        message_id = str(message["message_id"])
        order.append(f"start:{message_id}")
        if message_id == "wamid-1":
            first_started.set()
            release_first.wait(2)
        order.append(f"end:{message_id}")

    monkeypatch.setattr(whatsapp_bridge, "_process_message", process)
    monkeypatch.setattr(whatsapp_bridge, "_record_message_timing", lambda *_args, **_kwargs: None)
    config = _config(2)
    whatsapp_bridge._configure_phone_dispatcher(2)
    for index in (1, 2):
        message = {
            "message_id": f"wamid-{index}",
            "wa_id": "5511999999999",
            "subject_id": "same-subject",
            "client_id": "client-1",
            "username": "admin",
        }
        assert whatsapp_bridge._enqueue_phone_event(
            config,
            {},
            kind="inbound",
            event_id=message["message_id"],
            message=message,
            message_id=message["message_id"],
        )
    assert first_started.wait(1.0)
    time.sleep(0.1)
    assert order == ["start:wamid-1"]
    release_first.set()
    _wait_dispatcher()
    assert order == ["start:wamid-1", "end:wamid-1", "start:wamid-2", "end:wamid-2"]


def test_simulated_load_of_ten_phones_uses_four_workers(monkeypatch):
    active = 0
    maximum = 0
    processed: set[str] = set()
    lock = threading.Lock()

    def process(_config_value, _state, message):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with lock:
            processed.add(str(message["message_id"]))
            active -= 1

    monkeypatch.setattr(whatsapp_bridge, "_process_message", process)
    monkeypatch.setattr(whatsapp_bridge, "_record_message_timing", lambda *_args, **_kwargs: None)
    config = _config(4)
    whatsapp_bridge._configure_phone_dispatcher(4)
    for index in range(10):
        message = {
            "message_id": f"load-{index}",
            "wa_id": f"55117777{index:05d}",
            "subject_id": f"subject-{index}",
            "client_id": "client-1",
            "username": "admin",
        }
        assert whatsapp_bridge._enqueue_phone_event(
            config,
            {},
            kind="inbound",
            event_id=message["message_id"],
            message=message,
            message_id=message["message_id"],
        )
    _wait_dispatcher()
    assert processed == {f"load-{index}" for index in range(10)}
    assert maximum == 4


def test_local_queue_is_bounded_at_32(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(whatsapp_bridge, "_process_message", lambda *_args: release.wait(2))
    monkeypatch.setattr(whatsapp_bridge, "_record_message_timing", lambda *_args, **_kwargs: None)
    config = _config(1)
    whatsapp_bridge._configure_phone_dispatcher(1)
    accepted = []
    for index in range(33):
        message = {
            "message_id": f"wamid-{index}",
            "wa_id": "5511888888888",
            "subject_id": "subject",
            "client_id": "client-1",
            "username": "admin",
        }
        accepted.append(
            whatsapp_bridge._enqueue_phone_event(
                config,
                {},
                kind="inbound",
                event_id=message["message_id"],
                message=message,
                message_id=message["message_id"],
            )
        )
    try:
        assert accepted[:32] == [True] * 32
        assert accepted[32] is False
    finally:
        release.set()
    _wait_dispatcher()


def test_thirteenth_sol_waits_for_one_of_twelve_global_slots():
    gate = codex_console._ResizableConcurrencyGate(12, 12)
    for index in range(12):
        assert gate.acquire(f"task-{index}", f"phone-{index}") is True
    acquired = threading.Event()

    def wait_for_slot():
        if gate.acquire("task-12", "phone-12"):
            acquired.set()
            gate.release("phone-12")

    thread = threading.Thread(target=wait_for_slot)
    thread.start()
    time.sleep(0.1)
    assert not acquired.is_set()
    gate.release("phone-0")
    assert acquired.wait(1.0)
    for index in range(1, 12):
        gate.release(f"phone-{index}")
    thread.join(timeout=1)
    assert gate.diagnostics()["active"] == 0


def test_sol_gate_allows_six_siblings_but_blocks_seventh_for_same_phone(monkeypatch):
    gate = codex_console._ResizableConcurrencyGate(12, 6)
    monkeypatch.setattr(codex_console, "_codex_load_task", lambda _task_id: {"status": "queued"})
    for index in range(6):
        assert gate.acquire(f"task-{index}", "phone-a") is True
    acquired = threading.Event()

    def wait_for_phone_slot():
        if gate.acquire("task-6", "phone-a"):
            acquired.set()
            gate.release("phone-a")

    thread = threading.Thread(target=wait_for_phone_slot)
    thread.start()
    time.sleep(0.1)
    assert acquired.is_set() is False
    gate.release("phone-a")
    assert acquired.wait(1.0)
    for _index in range(5):
        gate.release("phone-a")
    thread.join(timeout=1)
    assert gate.diagnostics()["active"] == 0


def test_dual_sibling_tasks_have_distinct_queues_but_same_conversation_gate():
    base = {
        "origin": "whatsapp",
        "client_id": "client-1",
        "created_by": "admin",
        "conversation_id": "wa-worker",
        "conversation_generation": 1,
        "orchestration_profile": "whatsapp_dual_codex_worker",
        "agent_role": "task",
        "agent_lane": "worker",
    }
    first = {
        **base,
        "channel_metadata": {
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "agent_role": "task", "agent_lane": "worker",
            "job_group_id": "group-1", "subtask_id": "one",
        },
    }
    second = {
        **base,
        "channel_metadata": {**first["channel_metadata"], "subtask_id": "two"},
    }
    assert codex_console._codex_task_queue_key(first) != codex_console._codex_task_queue_key(second)
    assert codex_console._codex_task_conversation_gate_key(first) == codex_console._codex_task_conversation_gate_key(second)


def test_runtime_pool_uses_isolated_slots(monkeypatch):
    active = 0
    maximum = 0
    lock = threading.Lock()
    release = threading.Event()
    both_started = threading.Event()

    class FakeRuntime:
        def warm(self, *_args):
            return {"ready": True}

        def run(self, **_kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    both_started.set()
            release.wait(2)
            with lock:
                active -= 1
            return {"action": "reply", "reply_text": "ok"}

        def resolve_model(self, requested):
            return requested

        def diagnostics(self):
            return {"ready": True}

        def close(self):
            return None

    monkeypatch.setattr(codex_whatsapp_agents, "WarmConversationRuntime", FakeRuntime)
    pool = codex_whatsapp_agents.WarmConversationRuntimePool(default_size=2)
    pool.warm("luna", "sol", pool_size=2)
    threads = [threading.Thread(target=lambda: pool.run()) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert both_started.wait(1.0)
    finally:
        release.set()
    for thread in threads:
        thread.join(timeout=1)
    assert maximum == 2
    pool.close()


def test_restart_recovers_readonly_dual_sol_even_with_legacy_mutation_flags(monkeypatch, tmp_path):
    task_id = "dual-recovery"
    task = {
        "task_id": task_id,
        "status": "running",
        "origin": "whatsapp",
        "sandbox": "read_only",
        "external_safe_mode": True,
        "mutable_intent": True,
        "proposal": {"legacy": True},
        "channel_metadata": {
            "orchestration_profile": "whatsapp_dual_codex_worker",
            "agent_role": "task",
            "agent_lane": "worker",
        },
    }
    (tmp_path / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")
    started: list[str] = []
    monkeypatch.setattr(codex_console, "_codex_info_dir", lambda: str(tmp_path))
    monkeypatch.setattr(codex_console, "_codex_start_thread", lambda value: started.append(value))
    with codex_console.CODEX_TASKS_LOCK:
        codex_console.CODEX_TASKS.pop(task_id, None)
    result = codex_console.codex_console_recuperar_fila_background()
    recovered = json.loads((tmp_path / f"{task_id}.json").read_text(encoding="utf-8"))
    assert result["queued_task_ids"] == [task_id]
    assert recovered["status"] == "queued"
    assert recovered["wait_reason"] == "restart_recovery"
    assert recovered["restart_recovery_count"] == 1
    assert started == [task_id]
    with codex_console.CODEX_TASKS_LOCK:
        codex_console.CODEX_TASKS.pop(task_id, None)
