from __future__ import annotations

import threading

from backend.services.whatsapp.orchestration import pending as pending_component
from backend.services.whatsapp.orchestration import retry_coordinator
from backend.services.whatsapp.orchestration import function_manager
from backend.services.whatsapp.runtime import monitor


def test_active_selector_ignores_terminal_and_finalizing_jobs(monkeypatch) -> None:
    monkeypatch.setattr(
        retry_coordinator,
        "_pending_codex_tasks",
        lambda pending: [{"task_id": pending.get("task_id"), "status": "running"}],
        raising=False,
    )
    state = {
        "pending_messages": {
            "partial": {
                "conversation_id": "conversation-1", "kind": "dual_worker",
                "task_id": "task-partial", "job_state": "partial", "created_at": "2026-07-22T10:05:00Z",
            },
            "finalizing": {
                "conversation_id": "conversation-1", "kind": "dual_worker",
                "task_id": "task-finalizing", "job_state": "running",
                "terminal_delivery_started": True, "created_at": "2026-07-22T10:04:00Z",
            },
            "delivery-finalizing": {
                "conversation_id": "conversation-1", "kind": "dual_worker",
                "task_id": "task-delivery", "job_state": "running",
                "delivery_state": "partial_finalizing", "created_at": "2026-07-22T10:03:00Z",
            },
            "live": {
                "conversation_id": "conversation-1", "kind": "dual_worker",
                "task_id": "task-live", "job_state": "running", "created_at": "2026-07-22T10:00:00Z",
            },
        }
    }

    message_id, pending, task = retry_coordinator._IMPLEMENTATIONS["_active_pending_for_conversation"](
        state, "conversation-1",
    )

    assert message_id == "live"
    assert pending["task_id"] == "task-live"
    assert task["status"] == "running"


def test_restart_recovery_does_not_reactivate_terminal_records(monkeypatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(
        retry_coordinator,
        "_save_pending",
        lambda _state, message_id, _pending: saved.append(message_id),
        raising=False,
    )
    monkeypatch.setattr(retry_coordinator, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    state = {
        "pending_messages": {
            "partial-manager": {
                "kind": "dual_function_manager", "manager_state": "partial", "job_state": "partial",
            },
            "finalizing-manager": {
                "kind": "dual_function_manager", "manager_state": "running", "job_state": "manager_running",
                "terminal_delivery_started": True, "delivery_state": "partial_finalizing",
            },
            "completed-worker": {
                "kind": "dual_worker", "state": "waiting_retry", "job_state": "completed",
            },
            "live-manager": {
                "kind": "dual_function_manager", "manager_state": "running", "job_state": "manager_running",
            },
        }
    }

    recovered = retry_coordinator._IMPLEMENTATIONS["_recover_dual_pending_after_restart"](state)

    assert recovered == 1
    assert saved == ["live-manager"]
    assert state["pending_messages"]["partial-manager"]["manager_state"] == "partial"
    assert state["pending_messages"]["finalizing-manager"]["manager_state"] == "running"
    assert state["pending_messages"]["completed-worker"]["state"] == "waiting_retry"
    assert state["pending_messages"]["live-manager"]["manager_state"] == "queued"


def test_partial_finalization_recovers_expired_lease_and_persists_delivery(monkeypatch) -> None:
    pending = {
        "kind": "dual_function_manager",
        "subject_id": "subject-1",
        "job_group_id": "job-1",
        "job_state": "partial",
        "manager_state": "partial",
        "terminal_delivery_started": True,
        "delivery_state": "partial_finalizing",
    }
    state = {"pending_messages": {"message-1": pending}}
    proactive: list[dict] = []
    removed: list[dict] = []
    saved: list[str] = []
    monkeypatch.setattr(pending_component, "BRIDGE_STATE_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(pending_component, "_pending_partial_text", lambda *_args: "Resposta parcial segura.", raising=False)
    monkeypatch.setattr(
        pending_component,
        "_post_proactive",
        lambda _config, payload: proactive.append(dict(payload)) or {"status": "sent"},
        raising=False,
    )
    monkeypatch.setattr(
        pending_component,
        "_save_pending",
        lambda _state, message_id, _pending: saved.append(message_id),
        raising=False,
    )
    monkeypatch.setattr(pending_component, "_update_pending_codex_tasks", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(pending_component, "_record_message_timing", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(pending_component, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(
        pending_component,
        "_remove_pending",
        lambda _state, _message_id, **values: removed.append(values),
        raising=False,
    )

    completed = pending_component._IMPLEMENTATIONS["_terminate_pending_partial"](
        {}, state, "message-1", pending, reason="watchdog_sem_progresso",
    )

    assert completed is True
    assert len(proactive) == 1
    assert proactive[0]["fingerprint"] == "job:job-1:partial-terminal"
    assert pending["delivery_state"] == "sent"
    assert pending["terminal_delivery_completed_at_epoch"] > 0
    assert saved == ["message-1", "message-1"]
    assert removed == [{"status": "partial", "reason": "watchdog_sem_progresso"}]


def test_recent_partial_delivery_lease_blocks_duplicate_sender(monkeypatch) -> None:
    pending = {
        "job_state": "partial",
        "terminal_delivery_started": True,
        "terminal_delivery_started_at_epoch": 200,
        "delivery_state": "partial_finalizing",
    }
    state = {"pending_messages": {"message-1": pending}}
    monkeypatch.setattr(pending_component, "BRIDGE_STATE_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(pending_component.time, "time", lambda: 210)
    monkeypatch.setattr(
        pending_component,
        "_post_proactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate delivery")),
        raising=False,
    )

    completed = pending_component._IMPLEMENTATIONS["_terminate_pending_partial"](
        {}, state, "message-1", pending, reason="resultado_parcial",
    )

    assert completed is False
    assert pending["delivery_state"] == "partial_finalizing"


def test_persisted_terminal_delivery_is_archived_without_resending(monkeypatch) -> None:
    pending = {
        "job_state": "partial",
        "terminal_reason": "resultado_parcial",
        "terminal_delivery_started": True,
        "delivery_state": "sent",
        "partial_delivery_event_type": "task_partial",
    }
    state = {"pending_messages": {"message-1": pending}}
    removed: list[dict] = []
    monkeypatch.setattr(pending_component, "BRIDGE_STATE_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(pending_component, "_pending_partial_text", lambda *_args: "Resposta parcial segura.", raising=False)
    monkeypatch.setattr(
        pending_component,
        "_post_proactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("terminal delivery already persisted")),
        raising=False,
    )
    monkeypatch.setattr(pending_component, "_update_pending_codex_tasks", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(pending_component, "_record_message_timing", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(pending_component, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(
        pending_component,
        "_remove_pending",
        lambda _state, _message_id, **values: removed.append(values),
        raising=False,
    )

    completed = pending_component._IMPLEMENTATIONS["_terminate_pending_partial"](
        {}, state, "message-1", pending, reason="resultado_parcial",
    )

    assert completed is True
    assert removed == [{"status": "partial", "reason": "resultado_parcial"}]


def test_progress_watchdog_falls_back_to_partial_for_stalled_manager(monkeypatch) -> None:
    pending = {
        "kind": "dual_function_manager",
        "manager_state": "running",
        "job_state": "manager_running",
        "created_at_epoch": 100,
    }
    saved: list[dict] = []
    terminated: list[str] = []
    monkeypatch.setattr(monitor, "_pending_codex_tasks", lambda _pending: [], raising=False)
    monkeypatch.setattr(monitor, "_save_pending", lambda _state, _message_id, value: saved.append(dict(value)), raising=False)
    monkeypatch.setattr(monitor, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(monitor, "_PENDING_PROGRESS_STALL_SECONDS", 30, raising=False)
    monkeypatch.setattr(
        monitor,
        "_terminate_pending_partial",
        lambda _config, _state, _message_id, _pending, *, reason: terminated.append(reason) or True,
        raising=False,
    )

    handled = monitor._IMPLEMENTATIONS["_expire_dual_pending_if_due"](
        {}, {}, "message-1", pending,
    )

    assert handled is True
    assert pending["manager_state"] == "partial"
    assert pending["job_state"] == "partial"
    assert pending["manager_revision"] == 1
    assert pending["terminal_reason"] == "watchdog_sem_progresso"
    assert saved[-1]["progress_watchdog_triggered_at_epoch"] > 100
    assert terminated == ["watchdog_sem_progresso"]


def test_progress_watchdog_closes_stalled_waiting_retry_holder(monkeypatch) -> None:
    pending = {
        "kind": "dual_job_group",
        "job_state": "waiting_retry",
        "created_at_epoch": 100,
        "subtasks": [{"state": "waiting_retry", "next_retry_at_epoch": 9999}],
    }
    monkeypatch.setattr(monitor, "_pending_codex_tasks", lambda _pending: [], raising=False)
    monkeypatch.setattr(monitor, "_save_pending", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(monitor, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(monitor, "_PENDING_PROGRESS_STALL_SECONDS", 30, raising=False)

    expired = monitor._expire_stalled_pending({}, "message-1", pending, now_epoch=131)

    assert expired is True
    assert pending["job_state"] == "partial"
    assert pending["subtasks"][0] == {
        "state": "partial", "next_retry_at_epoch": 0, "retry_reason": "watchdog_sem_progresso",
    }


def test_progress_watchdog_closes_stalled_running_worker(monkeypatch) -> None:
    pending = {
        "kind": "dual_worker",
        "job_state": "running",
        "created_at_epoch": 100,
        "task_id": "task-stalled",
    }
    monkeypatch.setattr(monitor, "_pending_codex_tasks", lambda _pending: [], raising=False)
    monkeypatch.setattr(monitor, "_save_pending", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(monitor, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(monitor, "_PENDING_PROGRESS_STALL_SECONDS", 30, raising=False)

    expired = monitor._expire_stalled_pending({}, "message-1", pending, now_epoch=131)

    assert expired is True
    assert pending["job_state"] == "partial"
    assert pending["state"] == "partial"
    assert pending["terminal_reason"] == "watchdog_sem_progresso"


def test_stale_manager_exception_cannot_resurrect_watchdog_terminal(monkeypatch) -> None:
    pending = {
        "kind": "dual_function_manager",
        "manager_revision": 0,
        "manager_state": "queued",
        "job_state": "manager_queued",
    }
    state = {"pending_messages": {"message-1": pending}}
    saved: list[dict] = []
    monkeypatch.setattr(function_manager, "BRIDGE_STATE_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(function_manager, "_data_selection_register_plan_attempt", lambda _pending: None)
    monkeypatch.setattr(function_manager, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)
    monkeypatch.setattr(function_manager, "_record_latency", lambda *_args, **_kwargs: None, raising=False)

    def save(_state, message_id, value):
        snapshot = dict(value)
        saved.append(snapshot)
        _state["pending_messages"][message_id] = snapshot

    def watchdog_then_fail(_config, _pending):
        current = state["pending_messages"]["message-1"]
        current.update({
            "manager_revision": 1,
            "manager_state": "partial",
            "job_state": "partial",
            "terminal_reason": "watchdog_sem_progresso",
        })
        raise RuntimeError("late manager failure")

    monkeypatch.setattr(function_manager, "_save_pending", save, raising=False)
    monkeypatch.setattr(function_manager, "_function_manager_build_plan", watchdog_then_fail)
    monkeypatch.setattr(
        function_manager,
        "_post_proactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale callback delivered")),
        raising=False,
    )

    function_manager._IMPLEMENTATIONS["_function_manager_job"]({}, state, "message-1")

    assert state["pending_messages"]["message-1"]["manager_revision"] == 1
    assert state["pending_messages"]["message-1"]["job_state"] == "partial"
    assert len(saved) == 1


def test_manager_compare_and_save_rejects_terminal_revision_atomically(monkeypatch) -> None:
    current = {
        "kind": "dual_function_manager",
        "manager_revision": 2,
        "manager_state": "partial",
        "job_state": "partial",
        "terminal_delivery_started": True,
    }
    state = {"pending_messages": {"message-1": current}}
    saved = []
    monkeypatch.setattr(function_manager, "BRIDGE_STATE_LOCK", threading.RLock(), raising=False)
    monkeypatch.setattr(
        function_manager,
        "_save_pending",
        lambda *_args, **_kwargs: saved.append(True),
        raising=False,
    )

    accepted = function_manager._function_manager_save_if_revision(
        state,
        "message-1",
        {**current, "manager_state": "running", "job_state": "manager_running"},
        1,
    )

    assert accepted is False
    assert saved == []
    assert state["pending_messages"]["message-1"]["job_state"] == "partial"


def test_insufficient_manager_evidence_stays_nonterminal_until_handoff(monkeypatch) -> None:
    pending = {"kind": "dual_function_manager", "job_state": "manager_running"}
    insufficient = {
        "status": "partial",
        "evidence_sufficient": False,
        "verified_facts": ["fila consultada"],
        "sources": ["Mercado Livre"],
    }
    monkeypatch.setattr(function_manager, "_function_manager_execute_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(function_manager, "_function_manager_evidence", lambda *_args, **_kwargs: insufficient)
    monkeypatch.setattr(function_manager, "_function_manager_merge_evidence", lambda *_args, **_kwargs: insufficient)
    monkeypatch.setattr(
        function_manager.codex_whatsapp_agents.DATA_SELECTION_RUNTIME,
        "record_evidence_size",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(function_manager, "_now", lambda: "2026-07-22T10:00:00Z", raising=False)

    _results, evidence = function_manager._function_manager_run_tools(
        {},
        pending,
        {"intents": [], "requested_fields": []},
        {},
        0.0,
    )

    assert evidence["evidence_sufficient"] is False
    assert pending["manager_state"] == "running"
    assert pending["data_selection_state"] == "evidence_ready"
    assert pending["job_state"] == "manager_running"
    assert pending["manager_evidence_status"] == "partial"
    assert retry_coordinator._pending_is_terminal_or_finalizing(pending) is False
