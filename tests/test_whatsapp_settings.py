import pytest
from fastapi import HTTPException

from backend.services import whatsapp_bridge
from backend.services.whatsapp import settings


@pytest.mark.parametrize(
    ("value", "fallback", "minimum", "maximum"),
    [
        (None, 4, 1, 8),
        ("invalid", 4, 1, 8),
        (0, 4, 1, 8),
        (99, 4, 1, 8),
    ],
)
def test_capacity_component_matches_facade(value, fallback, minimum, maximum) -> None:
    assert settings.normalize_capacity(value, fallback, minimum, maximum) == whatsapp_bridge._normalize_capacity(
        value, fallback, minimum, maximum
    )


def test_ai_and_dual_settings_components_match_facade() -> None:
    config = {
        "ai_model": "codex:gpt-5.5",
        "codex_reasoning_effort": "medium",
        "codex_reasoning_policy": "fixed",
        "codex_reasoning_max": "high",
        "conversation_worker_count": 6,
        "conversation_runtime_pool_size": 2,
        "function_manager_worker_count": 3,
        "function_manager_runtime_pool_size": 2,
        "job_deadline_seconds": 9999,
    }

    assert settings.ai_settings(config) == whatsapp_bridge._whatsapp_ai_settings(config)
    assert settings.dual_agent_settings(
        config,
        report_deadline_seconds=whatsapp_bridge.WHATSAPP_REPORT_DEADLINE_SECONDS,
        max_retry_attempts=whatsapp_bridge.WHATSAPP_MAX_RETRY_ATTEMPTS,
    ) == whatsapp_bridge._whatsapp_dual_agent_settings(config)


@pytest.mark.parametrize(
    ("normalizer", "arguments"),
    [
        (settings.normalize_ai_model, ("modelo invalido!",)),
        (settings.normalize_codex_reasoning_effort, ("maximum",)),
        (settings.normalize_codex_reasoning_policy, ("automatic",)),
        (settings.normalize_codex_agent_model, ("modelo invalido!", "gpt-5.6-sol")),
        (settings.normalize_agent_architecture, ("triple",)),
        (settings.normalize_response_provider_policy, ("por_complexidade",)),
        (settings.normalize_voice_model, ("voz invalida!", "gpt-4o-mini-tts")),
        (settings.normalize_voice_name, ("unknown",)),
    ],
)
def test_invalid_settings_are_rejected(normalizer, arguments) -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalizer(*arguments)
    assert exc_info.value.status_code == 400


def test_voice_settings_apply_bounds_and_readonly_contract() -> None:
    config = {
        "voice_enabled": True,
        "voice_max_call_minutes": 999,
        "voice_silence_timeout_seconds": 1,
        "voice_max_concurrent_calls": 0,
        "voice_progress_interval_seconds": 60,
        "voice_store_audio": True,
        "voice_read_only": False,
    }

    component = settings.normalize_voice_config(config)
    assert component == whatsapp_bridge._normalize_voice_config(config)
    assert component["voice_max_call_minutes"] == 60
    assert component["voice_silence_timeout_seconds"] == 30
    assert component["voice_max_concurrent_calls"] == 3
    assert component["voice_progress_interval_seconds"] == 30
    assert component["voice_store_audio"] is False
    assert component["voice_read_only"] is True


def test_phone_preferences_are_normalized_per_subject() -> None:
    config = {
        "phone_notification_settings": {
            "subject-1": {
                "label": "  Telefone   principal ",
                "send_ml_question_suggestions": False,
                "send_weekly_report": True,
                "send_monthly_report": True,
                "ai_behavior": " Seja   curto\r\nSem rodeios\x00 ",
                "allow_voice_calls": True,
                "client_id": "000001",
                "username": "USUARIO@EXAMPLE.COM",
            }
        }
    }

    component = settings.phone_notification_settings(config, "subject-1")
    assert component == whatsapp_bridge._phone_notification_settings(config, "subject-1")
    assert component == {
        "label": "Telefone principal",
        "send_ml_question_suggestions": False,
        "send_weekly_report": True,
        "send_monthly_report": True,
        "ai_behavior": "Seja curto\nSem rodeios",
        "allow_voice_calls": True,
        "subject_id": "subject-1",
        "client_id": "000001",
        "username": "usuario@example.com",
    }


def test_codex_is_the_core_and_ai_model_is_only_optional_fallback() -> None:
    configured = settings.ai_settings({
        "ai_model": "deepseek-chat",
        "response_provider_policy": "codex_then_configured_fallback",
    })
    assert configured["provider"] == "codex"
    assert configured["fallback_provider"] == "deepseek"
    assert configured["fallback_model"] == "deepseek-chat"
    assert configured["response_provider_policy"] == "codex_then_configured_fallback"


def test_legacy_function_manager_fields_are_not_written_again(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(whatsapp_bridge, "_json_read", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        whatsapp_bridge,
        "_json_write",
        lambda _path, value: captured.update(dict(value)),
    )

    runtime_value = whatsapp_bridge._save_config({
        "function_manager_enabled": False,
        "function_manager_worker_count": 1,
        "function_manager_runtime_pool_size": 1,
        "data_selection_enabled": True,
        "data_selection_worker_count": 3,
        "data_selection_runtime_pool_size": 3,
    })

    assert runtime_value["function_manager_legacy_fields_ignored"] is True
    assert captured["version"] == 10
    assert captured["response_provider_policy"] == "codex_only"
    assert captured["data_selection_worker_count"] == 3
    assert not any(key.startswith("function_manager_") for key in captured)
