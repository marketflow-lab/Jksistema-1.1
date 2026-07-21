from __future__ import annotations

from backend.services.whatsapp import conversation_context
from backend.services.whatsapp.orchestration import function_manager
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore


def test_turn_memory_redacts_dlp_and_expires():
    record = {}
    conversation_context.append_turn(
        record, role="user", text="Meu e-mail e pessoa@example.com", now_epoch=1000,
    )
    assert record["recent_turns"][0]["text"] == conversation_context.TURN_MEMORY_REDACTED
    assert record["recent_turns"][0]["dlp_blocked"] is True
    conversation_context.expire_turn_memory(record, now_epoch=1000 + 31 * 24 * 60 * 60)
    assert record["recent_turns"] == []


def test_legacy_turn_is_sanitized_and_migrated_with_expiration():
    turns = conversation_context.sanitize_turns(
        [{"role": "user", "text": "Contato pessoa@example.com"}], now_epoch=2000,
    )
    assert turns[0]["schema_version"] == conversation_context.TURN_MEMORY_SCHEMA_VERSION
    assert turns[0]["expires_at_epoch"] > 2000
    assert "pessoa@example.com" not in turns[0]["text"]


def test_bridge_store_never_persists_sensitive_recent_turn(tmp_path):
    path = tmp_path / "bridge.sqlite3"
    store = WhatsappBridgeStore(path)
    store.save_state(
        {
            "dual_agent_conversations": {
                "conversation-a": {
                    "recent_turns": [{"role": "user", "text": "Contato pessoa@example.com"}],
                }
            }
        }
    )
    loaded = store.load_state()
    text = loaded["dual_agent_conversations"]["conversation-a"]["recent_turns"][0]["text"]
    assert text == conversation_context.TURN_MEMORY_REDACTED
    assert "pessoa@example.com" not in path.read_bytes().decode("latin-1", "ignore")


def test_whatsapp_mutation_handoff_never_creates_executable_task(monkeypatch):
    monkeypatch.setattr(
        function_manager,
        "_create_selected_ai_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("mutation task created")),
        raising=False,
    )
    monkeypatch.setattr(function_manager, "_record_function_manager_diagnostic", lambda *_args, **_kwargs: None)
    result = function_manager._function_manager_handoff_mutation_candidate(
        {}, {}, "message-a",
        {"session_is_full": True, "session_permissions": {"full": True}},
        {"manager_guard": {"data_selection_action": "mutation_candidate"}},
    )
    assert result is False


def test_mutation_plan_never_executes_tools():
    result = function_manager._function_manager_execute_tools(
        {"client_id": "tenant", "session_permissions": {"full": True}},
        {
            "manager_guard": {"data_selection_action": "mutation_candidate"},
            "tool_calls": [{"tool_id": "product_data", "arguments": {}}],
        },
        {"stores": ["JK Pecas"]},
    )
    assert result == []


def test_action_tool_never_executes_even_if_plan_guard_is_missing():
    result = function_manager._function_manager_execute_tools(
        {"client_id": "tenant", "session_permissions": {"full": True}},
        {"tool_calls": [{"tool_id": "operational_dispatcher", "arguments": {}}]},
        {"stores": ["JK Pecas"]},
    )
    assert result == []
