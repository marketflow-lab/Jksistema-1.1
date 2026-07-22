from __future__ import annotations

from backend.services.whatsapp.orchestration import conversation


def _decision(**changes):
    value = {
        "schema_version": "jk.whatsapp.conversation-decision.v3",
        "intent_id": "other",
        "relation_to_active_job": "none",
        "answer_basis": "conversation_only",
        "data_requirement": "required",
        "context_operations": [],
        "action": "delegate",
        "reply_text": "Vou consultar.",
    }
    value.update(changes)
    return value


def test_status_semantics_never_start_a_new_query():
    guarded = conversation._enforce_dual_decision_effect(
        _decision(
            intent_id="system.task_status",
            relation_to_active_job="status",
            action="delegate",
            context_operations=[{"op": "keep", "field": "sku", "value": "001"}],
        ),
        event_type="user_message",
        active_job={"job_id": "job-1", "status": "queued"},
    )

    assert guarded["action"] == "reply"
    assert guarded["answer_basis"] == "active_job"
    assert guarded["data_requirement"] == "none"
    assert guarded["context_operations"] == []
    assert "fila" in guarded["reply_text"].lower()


def test_short_question_queue_overrides_incorrect_status_classification():
    guarded = conversation._enforce_dual_decision_effect(
        _decision(
            intent_id="system.task_status",
            relation_to_active_job="status",
            action="reply",
            data_requirement="none",
        ),
        event_type="user_message",
        active_job={"job_id": "job-old", "status": "running"},
        user_message="Tem perguntas?",
    )

    assert guarded["intent_id"] == "mercado_livre.question"
    assert guarded["action"] == "queue"
    assert guarded["relation_to_active_job"] == "new_parallel"
    assert guarded["data_requirement"] == "required"
    assert {item["field"] for item in guarded["context_operations"]} == {"sku", "mlb"}


def test_process_short_question_queue_cannot_stop_at_incorrect_status(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        conversation,
        "_dual_active_job_snapshot",
        lambda *_args, **_kwargs: (
            "old-message",
            {"kind": "dual_function_manager"},
            {"task_id": "old-task", "status": "running"},
            {"job_id": "old-task", "status": "running"},
        ),
    )
    monkeypatch.setattr(conversation, "_record_dual_user_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        conversation,
        "_dual_initial_decision",
        lambda *_args, **_kwargs: _decision(
            intent_id="system.task_status",
            relation_to_active_job="status",
            action="reply",
            data_requirement="none",
        ),
    )
    monkeypatch.setattr(
        conversation,
        "_handle_dual_control_action",
        lambda *_args, **_kwargs: (False, str(_args[3].get("action") or "")),
    )
    monkeypatch.setattr(conversation, "_dual_resolve_job_policy", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        conversation,
        "_queue_dual_function_manager",
        lambda _config, _state, _session, decision, *_args, **_kwargs: captured.update(decision) or True,
    )
    monkeypatch.setattr(
        conversation,
        "_queue_dual_workers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected worker fallback")),
    )
    monkeypatch.setattr(
        conversation.whatsapp_settings,
        "context_hub_enabled_for_client",
        lambda *_args, **_kwargs: False,
    )

    handled = conversation._process_dual_codex_message(
        {},
        {},
        {"message_id": "new-message"},
        session={"client_id": "tenant", "username": "operator"},
        conversation_id="conversation",
        message_id="new-message",
        subject="phone",
        phone="5511999999999",
        request_text="Tem perguntas?",
        media=None,
        transcription=None,
        phone_ai_behavior="",
    )

    assert handled is True
    assert captured["action"] == "queue"
    assert captured["intent_id"] == "mercado_livre.question"


def test_status_without_active_job_is_closed_without_stale_context():
    guarded = conversation._enforce_dual_decision_effect(
        _decision(intent_id="system.task_status", action="delegate", answer_basis="active_job"),
        event_type="user_message",
        active_job={},
    )

    assert guarded["action"] == "reply"
    assert guarded["answer_basis"] == "unavailable"
    assert guarded["resolved_context"] == {}
    assert "nao ha" in guarded["reply_text"].lower()


def test_new_parallel_relation_cannot_steer_existing_job():
    guarded = conversation._enforce_dual_decision_effect(
        _decision(relation_to_active_job="new_parallel", action="steer"),
        event_type="user_message",
        active_job={"job_id": "job-old", "status": "running"},
    )

    assert guarded["action"] == "queue"


def test_steer_without_confirmed_followup_cannot_touch_existing_job():
    guarded = conversation._enforce_dual_decision_effect(
        _decision(relation_to_active_job="none", action="steer"),
        event_type="user_message",
        active_job={"job_id": "job-old", "status": "running"},
    )

    assert guarded["action"] == "queue"


def test_status_context_materialization_does_not_apply_memory_operations(monkeypatch):
    applied = []
    monkeypatch.setattr(
        conversation.whatsapp_conversation_context,
        "apply_context_operations",
        lambda *_args, **_kwargs: applied.append(True) or {},
    )
    decision = _decision(
        intent_id="system.task_status",
        relation_to_active_job="status",
        action="reply",
        context_operations=[{"op": "keep", "field": "sku", "value": "001"}],
    )

    conversation._materialize_agent_decision_context(
        {"context_memory": {"sku": "001"}},
        decision,
        event_type="user_message",
        authorized_stores=["JK Pecas"],
        quoted_context=None,
    )

    assert applied == []
    assert decision["context_operations"] == []
    assert decision["resolved_context"].get("sku", "") == ""


def test_active_snapshot_rejects_pending_from_another_session(monkeypatch):
    state = {
        "pending_messages": {
            "old": {
                "kind": "dual_function_manager",
                "conversation_id": "conversation",
                "client_id": "tenant-other",
                "username": "operator",
                "subject_id": "phone",
                "job_state": "manager_running",
            }
        }
    }
    monkeypatch.setattr(
        conversation,
        "_active_pending_for_conversation",
        lambda scoped, *_args, **_kwargs: (
            ("old", scoped["pending_messages"]["old"], {"task_id": "job", "status": "running"})
            if scoped["pending_messages"]
            else ("", {}, {})
        ),
        raising=False,
    )

    message_id, pending, task, snapshot = conversation._dual_active_job_snapshot(
        state,
        "conversation",
        client_id="tenant-current",
        username="operator",
        subject_id="phone",
    )

    assert (message_id, pending, task, snapshot) == ("", {}, {}, {})


def test_active_snapshot_fallback_does_not_resurrect_terminal_worker(monkeypatch):
    live = {
        "kind": "dual_function_manager",
        "conversation_id": "conversation",
        "job_group_id": "live-job",
        "job_state": "manager_queued",
        "request_text": "consulta atual",
    }
    terminal = {
        "kind": "dual_worker",
        "conversation_id": "conversation",
        "task_id": "terminal-task",
        "job_state": "partial",
        "request_text": "consulta antiga",
    }
    state = {"pending_messages": {"live": live, "terminal": terminal}}
    monkeypatch.setattr(
        conversation,
        "_active_pending_for_conversation",
        lambda *_args, **_kwargs: ("live", live, {"task_id": "live-job", "status": "queued"}),
        raising=False,
    )
    monkeypatch.setattr(
        conversation.codex_console,
        "_codex_load_task",
        lambda _task_id: {"task_id": "terminal-task", "status": "running"},
    )

    message_id, pending, task, snapshot = conversation._dual_active_job_snapshot(
        state,
        "conversation",
    )

    assert message_id == "live"
    assert pending is live
    assert task["status"] == "queued"
    assert snapshot["job_id"] == "live-job"
