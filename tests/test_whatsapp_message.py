import pytest
from fastapi import HTTPException

from backend.services import codex_console, whatsapp_bridge
from backend.services.whatsapp import message as whatsapp_message
from backend.services.codex.console import attachments as console_attachments


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+55 (31) 99999-0000", "5531999990000"),
        ("003531999990000", "3531999990000"),
        ("31999990000", "5531999990000"),
    ],
)
def test_phone_normalization_component_matches_facade(raw: str, expected: str) -> None:
    assert whatsapp_message.normalize_registered_phone(raw) == whatsapp_bridge._normalize_registered_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "123", "1" * 13, "1" * 16])
def test_invalid_registered_phones_are_rejected(raw: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        whatsapp_message.normalize_registered_phone(raw)
    assert exc_info.value.status_code == 400


def test_message_phone_uses_injected_normalizer_and_subject_fallback() -> None:
    seen: list[object] = []

    def normalize(value) -> str:
        seen.append(value)
        return "normalized" if value else ""

    assert whatsapp_message.message_phone({}, {"wa_id": "raw"}, normalize_phone=normalize) == "normalized"
    assert whatsapp_message.message_phone(
        {"subject_id": "subject-1", "personal_phone": "+5531999990000"},
        {"subject_id": "subject-1"},
        normalize_phone=normalize,
    ) == "normalized"
    assert seen == ["raw", None, "+5531999990000"]

    config = {"subject_id": "subject-1", "personal_phone": "+55 (31) 99999-0000"}
    event = {"subject_id": "subject-1"}
    assert whatsapp_message.message_phone(
        config,
        event,
        normalize_phone=console_attachments._codex_normalize_phone,
    ) == whatsapp_bridge._message_phone(config, event)


def test_conversation_id_still_calls_facade_message_phone(monkeypatch) -> None:
    config = {"client_id": "000001", "username": "usuario"}
    event = {"message_id": "wamid.1"}

    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5511999999999")
    first = whatsapp_bridge._conversation_id(config, event)
    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5521999999999")
    second = whatsapp_bridge._conversation_id(config, event)

    assert first != second


def test_message_request_text_combines_transcription_and_limits_size() -> None:
    event = {"text_body": "mensagem"}
    transcription = {"success": True, "text": "audio"}
    assert whatsapp_message.message_request_text(event, transcription) == whatsapp_bridge._message_request_text(
        event, transcription
    ) == "mensagem\n\naudio"
    assert len(whatsapp_message.message_request_text({"text_body": "x" * 13000})) == 12000


@pytest.mark.parametrize(
    ("media", "transcription", "query_policy", "full", "general"),
    [
        (None, None, {}, False, True),
        (
            {"path": "C:/temp/audio.ogg", "mime_type": "audio/ogg", "size": 100},
            {"success": True, "text": "consultar saldo", "duration_seconds": 2, "confidence": 0.9, "language": "pt"},
            {"mode": "query_only", "domains": ["estoque"], "store": "JK Pecas", "bypass_cache": True},
            True,
            False,
        ),
        (
            None,
            {"success": False, "error": "audio invalido"},
            {"store_mode": "all", "stores": ["JK Pecas", "Deckas"]},
            False,
            False,
        ),
    ],
)
def test_prompt_component_matches_facade(media, transcription, query_policy, full, general) -> None:
    event = {"message_id": "wamid.1", "text_body": "mande a foto do SKU 001"}
    kwargs = {
        "mobile_full_access": full,
        "query_policy": query_policy,
        "ai_behavior": " Seja   objetivo ",
        "general_answer": general,
    }
    component = whatsapp_message.message_prompt(event, media, transcription, **kwargs)
    facade = whatsapp_bridge._message_prompt(event, media, transcription, **kwargs)

    assert component == facade
    assert "Mensagem externa: wamid.1" in component
    assert "Seja objetivo" in component
    assert "foto do cadastro" in component
