from __future__ import annotations

import json
from types import SimpleNamespace

from backend.services import codex_whatsapp_agents, whatsapp_bridge
from backend.services.whatsapp import settings
from backend.services.codex.console import attachments as console_attachments


def _phone_setting(subject: str, *, client: str, user: str, primary: bool, label: str = ""):
    return {
        "subject_id": subject,
        "client_id": client,
        "username": user,
        "phone_number": "5537999993818",
        "label": label,
        "is_primary": primary,
    }


def test_legacy_phone_features_are_removed_during_config_migration():
    migrated = settings.migrate_legacy_primary_phone_labels(
        {
            "target": {
                **_phone_setting(
                    "target", client="000002", user="caio", primary=True, label="Comercial"
                ),
                "send_weekly_report": True,
                "send_monthly_report": True,
                "allow_voice_calls": True,
                "welcome_message": "Bem-vindo",
                "send_welcome_message": True,
            },
        }
    )
    assert migrated["target"]["label"] == "Comercial"
    assert not {
        "is_primary",
        "send_weekly_report",
        "send_monthly_report",
        "allow_voice_calls",
        "welcome_message",
        "send_welcome_message",
    } & migrated["target"].keys()


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


def test_whatsapp_and_sidebar_use_independent_pii_free_identities(monkeypatch):
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
    whatsapp_id = whatsapp_bridge._conversation_id(config, message)
    sidebar_id = console_attachments._codex_canonical_conversation_id(
        "000002", "caio", channel="app"
    )

    assert whatsapp_id.startswith("wa_")
    assert sidebar_id.startswith("app_")
    assert "3818" not in whatsapp_id
    assert whatsapp_id != sidebar_id
    assert console_attachments._codex_shared_continuity_for_session(
        {"client_id": "000002", "username": "caio"}
    ) == {}
    assert whatsapp_bridge._shared_continuity_for_session(
        {"client_id": "000002", "username": "caio"}
    ) == {}

    message["subject_id"] = "subject-secondary"
    message["binding_is_primary"] = 0
    isolated = whatsapp_bridge._conversation_id(config, message)
    assert isolated.startswith("wa_")
    assert isolated == whatsapp_id


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
