from __future__ import annotations

import copy

import pytest

pytest.importorskip("openai_codex")

from openai_codex.api import TurnHandle
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import (
    CodexErrorInfoValue,
    ErrorNotification,
    ReasoningEffort,
    TurnCompletedNotification,
    TurnStartResponse,
)
from openai_codex.models import UnknownNotification

from backend.services.codex.console import agent_loop


@pytest.fixture
def client(monkeypatch):
    # Restore the SDK methods after every case, including when already adapted
    # by another test in the same process. No app-server is launched.
    monkeypatch.setattr(CodexClient, "request", CodexClient.request)
    monkeypatch.setattr(CodexClient, "_coerce_notification", CodexClient._coerce_notification)
    agent_loop._codex_apply_sdk_protocol_compat()
    return CodexClient()


def _failed_turn():
    return {
        "id": "turn-test",
        "status": "failed",
        "items": [],
        "error": {
            "message": "Este modelo nao pode ser usado nesta conta.",
            "codexErrorInfo": "newRuntimeErrorCategory",
            "additionalDetails": "Detalhe preservado.",
        },
    }


def test_new_error_category_preserves_failure_and_finishes_turn_stream(client, monkeypatch):
    raw_turn = _failed_turn()
    raw_error = {
        "threadId": "thread-test",
        "turnId": raw_turn["id"],
        "error": raw_turn["error"],
        "willRetry": False,
    }
    client.register_turn_notifications(raw_turn["id"])
    handle = TurnHandle(client, "thread-test", raw_turn["id"])
    for method, params in (
        ("error", raw_error),
        ("turn/completed", {"threadId": "thread-test", "turn": raw_turn}),
    ):
        client._router.route_notification(client._coerce_notification(method, params))

    # Consume real routed SDK events without blocking if completion is lost.
    monkeypatch.setattr(
        client,
        "next_turn_notification",
        lambda turn_id: client._router._turn_notifications[turn_id].get_nowait(),
    )
    if hasattr(handle, "_subscription"):
        subscription = handle._subscription
        original_next = type(subscription).next

        def next_available(current):
            assert current._cursor < current._state.next_event, "A conclusao do turno foi perdida."
            return original_next(current)

        monkeypatch.setattr(type(subscription), "next", next_available)
    events = list(handle.stream())

    assert [event.method for event in events] == ["error", "turn/completed"]
    assert isinstance(events[0].payload, ErrorNotification)
    assert events[0].payload.will_retry is False
    assert isinstance(events[1].payload, TurnCompletedNotification)
    assert events[1].payload.turn.status.value == "failed"
    for error in (events[0].payload.error, events[1].payload.turn.error):
        assert error.message == raw_turn["error"]["message"]
        assert error.additional_details == raw_turn["error"]["additionalDetails"]
        assert error.codex_error_info.root == CodexErrorInfoValue.other
    assert raw_turn["error"]["codexErrorInfo"] == "newRuntimeErrorCategory"
    client.unregister_turn_notifications(raw_turn["id"])


def test_new_error_category_is_normalized_in_rpc_response(client, monkeypatch):
    raw = {"turn": _failed_turn()}
    monkeypatch.setattr(client, "_request_raw", lambda _method, _params: raw)

    response = client.request("turn/start", {}, response_model=TurnStartResponse)

    assert response.turn.status.value == "failed"
    assert response.turn.error.message == raw["turn"]["error"]["message"]
    assert response.turn.error.codex_error_info.root == CodexErrorInfoValue.other


def test_known_error_codes_structured_variants_and_reasoning_compat_are_preserved():
    codes = [code.value for code in CodexErrorInfoValue]
    payload = {
        "known": [{"codexErrorInfo": code} for code in codes],
        "structured": {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}},
        "reasoning": [{"reasoningEffort": effort} for effort in ("max", "ultra", "high")],
    }
    original = copy.deepcopy(payload)

    result = agent_loop._codex_sdk_response_compat(payload)

    assert result["known"] == payload["known"]
    assert result["structured"] == payload["structured"]
    expected_efforts = []
    for effort in ("max", "ultra", "high"):
        try:
            expected_efforts.append(ReasoningEffort(effort).value)
        except ValueError:
            expected_efforts.append("xhigh")
    assert result["reasoning"] == [{"reasoningEffort": effort} for effort in expected_efforts]
    assert payload == original


def test_known_and_unknown_notifications_keep_payload_and_retry_semantics(client):
    raw_error = {
        "threadId": "thread-test",
        "turnId": "turn-test",
        "error": {"message": "Falha temporaria.", "codexErrorInfo": "serverOverloaded"},
        "willRetry": True,
    }
    event = client._coerce_notification("error", raw_error)
    assert isinstance(event.payload, ErrorNotification)
    assert event.payload.will_retry is True
    assert event.payload.error.codex_error_info.root == CodexErrorInfoValue("serverOverloaded")

    unknown_params = {"codexErrorInfo": "futureCode", "reasoningEffort": "ultra", "turnId": "turn-test"}
    unknown = client._coerce_notification("future/notification", unknown_params)
    assert isinstance(unknown.payload, UnknownNotification)
    assert unknown.payload.params == unknown_params

    malformed = client._coerce_notification("error", {"error": {"message": "Incompleto."}})
    assert isinstance(malformed.payload, UnknownNotification)


def test_notification_adapter_is_independent_and_idempotent(client, monkeypatch):
    adapted_request = CodexClient.request
    existing_coerce = CodexClient._coerce_notification

    def unmarked_coerce(self, method, params):
        return existing_coerce(self, method, params)

    monkeypatch.setattr(CodexClient, "_coerce_notification", unmarked_coerce)
    agent_loop._codex_apply_sdk_protocol_compat()
    adapted_coerce = CodexClient._coerce_notification
    assert adapted_coerce is not unmarked_coerce
    assert CodexClient.request is adapted_request

    agent_loop._codex_apply_sdk_protocol_compat()
    assert CodexClient._coerce_notification is adapted_coerce
    assert CodexClient.request is adapted_request


@pytest.mark.parametrize(
    ("requested", "configured", "expected"),
    [
        (None, None, "gpt-6.1-sol"),
        ("gpt-5.5", None, "gpt-6.1-sol"),
        (None, "gpt-5.5", "gpt-6.1-sol"),
        ("gpt-5.6-sol", "gpt-5.5", "gpt-5.6-sol"),
        (None, "gpt-5.6-sol", "gpt-5.6-sol"),
    ],
)
def test_legacy_model_uses_current_default_and_keeps_valid_configuration(
    monkeypatch, requested, configured, expected
):
    monkeypatch.setattr(agent_loop, "CODEX_DEFAULT_MODEL", "gpt-6.1-sol", raising=False)
    if configured is None:
        monkeypatch.delenv("JK_CODEX_MODEL", raising=False)
    else:
        monkeypatch.setenv("JK_CODEX_MODEL", configured)

    assert agent_loop._codex_normalizar_model(requested) == expected
