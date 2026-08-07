from __future__ import annotations

import json
from types import SimpleNamespace

from backend.services import codex_console, codex_whatsapp_agents, whatsapp_bridge
from backend.services.whatsapp import settings
from backend.services.codex.console import bindings as console_bindings
from backend.services.codex.console import attachments as console_attachments
from backend.services.codex.console import state as console_state
from backend.services.codex.console import tasks as console_tasks
from backend.services.codex.console import task_creation as console_task_creation
from backend.services.codex.console import queue_worker as console_queue_worker
from backend.services.codex.console import runtime as console_runtime
from backend.services.codex.console import scope as console_scope


def _phone_setting(subject: str, *, client: str, user: str, primary: bool, label: str = ""):
    return {
        "subject_id": subject,
        "client_id": client,
        "username": user,
        "phone_number": "5537999993818",
        "label": label,
        "is_primary": primary,
    }


def test_primary_selection_is_exclusive_and_owner_scoped():
    configured = {
        "caio-a": _phone_setting("caio-a", client="000002", user="caio", primary=False),
        "caio-b": _phone_setting("caio-b", client="000002", user="caio", primary=True),
        "other": _phone_setting("other", client="000003", user="caio", primary=True),
    }

    selected = settings.select_primary_phone_setting(
        configured,
        subject_id="caio-a",
        client_id="000002",
        username="CAIO",
        enabled=True,
    )

    assert selected["caio-a"]["is_primary"] is True
    assert selected["caio-b"]["is_primary"] is False
    assert selected["other"]["is_primary"] is True
    primary = settings.primary_phone_setting(
        {"phone_notification_settings": selected},
        client_id="000002",
        username="caio",
    )
    assert primary["subject_id"] == "caio-a"


def test_primary_resolution_fails_closed_on_duplicates_or_binding_mismatch():
    config = {
        "phone_notification_settings": {
            "one": _phone_setting("one", client="000002", user="caio", primary=True),
            "two": _phone_setting("two", client="000002", user="caio", primary=True),
        }
    }
    assert settings.primary_phone_setting(config, client_id="000002", username="caio") == {}

    config["phone_notification_settings"]["two"]["is_primary"] = False
    bindings = [
        {
            "subject_id": "one",
            "client_id": "000002",
            "username": "outro",
            "machine_id": "machine-a",
            "phone_number": "5537999993818",
        }
    ]
    assert settings.primary_phone_binding(
        config,
        bindings,
        client_id="000002",
        username="caio",
        machine_id="machine-a",
    ) == {}


def test_legacy_primary_labels_never_infer_cross_channel_consent():
    migrated = settings.migrate_legacy_primary_phone_labels(
        {
            "target": _phone_setting(
                "target", client="000002", user="caio", primary=False, label="  Telefone   principal "
            ),
            "other": _phone_setting(
                "other", client="000002", user="caio", primary=False, label="Financeiro"
            ),
        }
    )
    assert migrated["target"]["is_primary"] is False
    assert migrated["other"]["is_primary"] is False

    personal = settings.migrate_legacy_primary_phone_labels(
        {
            "target": _phone_setting(
                "target", client="000002", user="caio", primary=False, label="Caio"
            ),
            "other": _phone_setting(
                "other", client="000002", user="caio", primary=False, label="Paraiba"
            ),
        }
    )
    assert personal["target"]["is_primary"] is False
    assert personal["other"]["is_primary"] is False

    ambiguous = settings.migrate_legacy_primary_phone_labels(
        {
            "a": _phone_setting("a", client="000002", user="caio", primary=False, label="Telefone principal"),
            "b": _phone_setting("b", client="000002", user="caio", primary=False, label="telefone principal"),
        }
    )
    assert ambiguous["a"]["is_primary"] is False
    assert ambiguous["b"]["is_primary"] is False


def test_rewritten_control_reply_rotates_thread_and_keeps_only_delivered_text(monkeypatch):
    conversation_id = "bj_shared"
    state = {
        "dual_agent_conversations": {
            conversation_id: {
                "conversation_id": conversation_id,
                "thread_id": "thread-with-provisional-reply",
                "recent_turns": [
                    {"role": "user", "text": "cancele", "event_type": "user_message"},
                    {"role": "assistant", "text": "Resposta provisoria", "event_type": "user_message"},
                ],
            }
        }
    }
    monkeypatch.setattr(whatsapp_bridge, "_save_state", lambda _state: None)

    whatsapp_bridge._replace_provisional_assistant_reply(
        state,
        conversation_id,
        "Resposta provisoria",
        "A tarefa foi cancelada.",
    )

    record = state["dual_agent_conversations"][conversation_id]
    assert record["thread_id"] == ""
    assert record["thread_reset_reason"] == "control_reply_rewritten"
    assert [turn["text"] for turn in record["recent_turns"]] == [
        "cancele",
        "A tarefa foi cancelada.",
    ]


def test_primary_whatsapp_and_sidebar_share_pii_free_identity(monkeypatch):
    config = {
        "client_id": "000002",
        "username": "caio",
        "phone_notification_settings": {
            "subject-primary": _phone_setting(
                "subject-primary", client="000002", user="caio", primary=True
            )
        },
    }
    message = {
        "client_id": "000002",
        "username": "caio",
        "subject_id": "subject-primary",
        "wa_id": "5537999993818",
        "binding_is_primary": 1,
    }
    shared = whatsapp_bridge._conversation_id(config, message)

    assert shared == console_attachments._codex_shared_conversation_id("000002", "caio")
    assert shared.startswith("bj_")
    assert "3818" not in shared
    assert shared != console_attachments._codex_shared_conversation_id("000002", "outro")
    assert shared != console_attachments._codex_shared_conversation_id("000003", "caio")

    message["subject_id"] = "subject-secondary"
    message["binding_is_primary"] = 0
    isolated = whatsapp_bridge._conversation_id(config, message)
    assert isolated.startswith("wa_")
    assert isolated != shared


def test_authorization_fingerprint_rotates_on_permissions_or_store_scope():
    component = whatsapp_bridge._COMPONENTS["conversation"]
    base = {
        "client_id": "000002",
        "username": "caio",
        "is_full": True,
        "permissions": {"full": True, "vendas": True},
    }
    first = component._shared_authorization_fingerprint(base, ["JK Pecas"])
    other_store = component._shared_authorization_fingerprint(base, ["Uai Mineirinho"])
    reduced = component._shared_authorization_fingerprint(
        {**base, "permissions": {"full": True, "vendas": False}},
        ["JK Pecas"],
    )

    assert first != other_store
    assert first != reduced


def test_incremental_decision_prompt_omits_bootstrap_and_server_history():
    full = codex_whatsapp_agents._decision_prompt(
        event_type="user_message",
        user_message="Oi",
        active_job=None,
        worker_result=None,
        conversation_context=[{"role": "user", "text": "HISTORICO_ANTIGO"}],
        conversation_state={},
        ai_behavior="",
        tick_index=0,
        include_bootstrap=True,
        origin_channel="app",
    )
    delta = codex_whatsapp_agents._decision_prompt(
        event_type="user_message",
        user_message="E agora?",
        active_job=None,
        worker_result=None,
        conversation_context=[{"role": "user", "text": "HISTORICO_ANTIGO"}],
        conversation_state={},
        ai_behavior="",
        tick_index=0,
        include_bootstrap=False,
        origin_channel="whatsapp",
    )

    assert "HISTORICO_ANTIGO" in full
    assert "HISTORICO_ANTIGO" not in delta
    assert "Decida a proxima acao" in full
    assert "Decida a proxima acao" not in delta
    assert "E agora?" in delta
    assert '"channel":"whatsapp"' in delta


def test_warm_runtime_bootstraps_start_and_sends_delta_on_resume(monkeypatch):
    prompts: list[str] = []
    starts: list[dict] = []
    resumes: list[dict] = []

    class FakeThread:
        id = "thread-shared"

        def run(self, prompt, **_kwargs):
            prompts.append(prompt)
            return SimpleNamespace(
                final_response=json.dumps(
                    {
                        "schema_version": "jk.whatsapp.conversation-decision.v3",
                        "action": "reply",
                        "reply_text": "Tudo certo.",
                        "intent": "greeting",
                        "intent_kind": "general",
                        "relation_to_active_job": "none",
                        "answer_basis": "conversation",
                        "data_requirement": "none",
                        "response_mode": "direct",
                        "confidence": "high",
                    }
                )
            )

    class FakeClient:
        def thread_start(self, **kwargs):
            starts.append(kwargs)
            return FakeThread()

        def thread_resume(self, _thread_id, **kwargs):
            resumes.append(kwargs)
            return FakeThread()

    runtime = codex_whatsapp_agents.WarmConversationRuntime()
    monkeypatch.setattr(runtime, "_start_locked", lambda: FakeClient())
    monkeypatch.setattr(runtime, "resolve_model", lambda requested: requested)

    base = {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "event_type": "user_message",
        "active_job": None,
        "worker_result": None,
        "conversation_state": {},
        "ai_behavior": "",
        "tick_index": 0,
        "origin_channel": "app",
    }
    runtime.run(
        thread_id="",
        user_message="Oi",
        conversation_context=[{"role": "user", "text": "CONTEXTO_INICIAL"}],
        **base,
    )
    runtime.run(
        thread_id="thread-shared",
        user_message="Continue",
        conversation_context=[{"role": "user", "text": "CONTEXTO_INICIAL"}],
        **base,
    )

    assert len(starts) == 1
    assert len(resumes) == 1
    assert "developer_instructions" in starts[0]
    assert "developer_instructions" not in resumes[0]
    assert "CONTEXTO_INICIAL" in prompts[0]
    assert "CONTEXTO_INICIAL" not in prompts[1]
    assert "Turno incremental" in prompts[1]


def test_sidebar_direct_reply_uses_shared_history_without_starting_worker(tmp_path, monkeypatch):
    session = {
        "client_id": "000002",
        "username": "caio",
        "permissions": {"full": True, "vendas": True},
        "is_full": True,
    }
    shared_id = console_attachments._codex_shared_conversation_id("000002", "caio")
    started: list[str] = []
    current_runtime = console_bindings.current()
    monkeypatch.setattr(console_bindings, "_RUNTIME", console_bindings.ConsoleRuntime(
        str(tmp_path), str(tmp_path / "info"), current_runtime.session_loader,
        current_runtime.permissions_loader, current_runtime.source_module,
    ))
    monkeypatch.setattr(console_task_creation, "_codex_enabled", lambda: True)
    monkeypatch.setattr(console_task_creation, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(console_task_creation, "_codex_start_thread", started.append)
    monkeypatch.setattr(console_task_creation, "_codex_readonly_cwd_for_session", lambda *_args: str(tmp_path))
    monkeypatch.setattr(console_task_creation, "_codex_shared_continuity_for_session",
        lambda _session: {"conversation_id": shared_id, "subject_id": "primary"},
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_shared_sidebar_conversation_turn",
        lambda *_args, **_kwargs: {
            "conversation_id": shared_id,
            "decision": {"action": "reply", "reply_text": "Olá, Caio."},
        },
    )
    console_state.CODEX_TASKS.clear()

    task = console_tasks.create(
        codex_console.CodexTaskRequest(prompt="Oi"),
        session,
    )["task"]

    assert task["conversation_id"] == shared_id
    assert task["conversation_state"] == "active"
    assert task["status"] == "completed"
    assert task["final_response"] == "Olá, Caio."
    assert started == []
    console_state.CODEX_TASKS.clear()


def test_sidebar_shared_responder_failure_falls_back_to_readonly_worker(tmp_path, monkeypatch):
    session = {
        "client_id": "000002",
        "username": "caio",
        "permissions": {"full": True, "vendas": True},
        "is_full": True,
    }
    shared_id = console_attachments._codex_shared_conversation_id("000002", "caio")
    started: list[str] = []
    current_runtime = console_bindings.current()
    monkeypatch.setattr(console_bindings, "_RUNTIME", console_bindings.ConsoleRuntime(
        str(tmp_path), str(tmp_path / "info"), current_runtime.session_loader,
        current_runtime.permissions_loader, current_runtime.source_module,
    ))
    monkeypatch.setattr(console_task_creation, "_codex_enabled", lambda: True)
    monkeypatch.setattr(console_task_creation, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(console_task_creation, "_codex_start_thread", started.append)
    monkeypatch.setattr(console_task_creation, "_codex_readonly_cwd_for_session", lambda *_args: str(tmp_path))
    monkeypatch.setattr(console_task_creation, "_codex_shared_continuity_for_session",
        lambda _session: {"conversation_id": shared_id, "subject_id": "primary"},
    )

    def fail_responder(*_args, **_kwargs):
        raise TimeoutError("conversation responder timed out")

    monkeypatch.setattr(whatsapp_bridge, "_shared_sidebar_conversation_turn", fail_responder)
    console_state.CODEX_TASKS.clear()

    task = console_tasks.create(
        codex_console.CodexTaskRequest(prompt="Consulte o estoque"),
        session,
    )["task"]

    assert task["conversation_id"] == shared_id
    assert task["status"] == "queued"
    assert started == [task["task_id"]]
    console_state.CODEX_TASKS.clear()
