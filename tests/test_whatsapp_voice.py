from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import codex_console, whatsapp_voice


class _Response:
    ok = True
    status_code = 200


class _Bridge:
    @staticmethod
    def _gateway_json(_config, method, path, payload=None, timeout=0):
        assert method == "GET"
        assert path == "/bridge/voice/status"
        assert timeout == 15
        return {
            "success": True,
            "configured": True,
            "openai": {
                "api_key_configured": True,
                "webhook_secret_configured": True,
                "project_configured": True,
                "key_fingerprint": whatsapp_voice.openai_key_fingerprint(),
            },
            "sip": {"configured": True, "host": "voice.example.com"},
            "heartbeats": [{"key_fingerprint": "should-not-leak"}],
            "calls": [{"id": "should-not-leak-from-preflight"}],
        }


def test_voice_preflight_reuses_existing_key_without_exposing_fingerprint(monkeypatch):
    monkeypatch.setattr(whatsapp_voice.ia_providers, "_obter_openai_api_key", lambda: "sk-test-existing-key")
    monkeypatch.setattr(whatsapp_voice, "_websockets_available", lambda: True)
    monkeypatch.setattr(whatsapp_voice.requests, "get", lambda *_args, **_kwargs: _Response())

    result = whatsapp_voice.VoiceRuntime().preflight(
        {"voice_model": "gpt-realtime-2.1"},
        _Bridge,
        check_openai=True,
    )

    assert result["success"] is True
    assert result["key_match"] is True
    assert result["openai_model"]["ready"] is True
    serialized = json.dumps(result)
    assert "sk-test-existing-key" not in serialized
    assert whatsapp_voice.openai_key_fingerprint() not in serialized
    assert "should-not-leak" not in serialized


def test_voice_defaults_are_fail_closed_and_read_only():
    from backend.services import whatsapp_bridge

    config = whatsapp_bridge._default_config()
    assert config["voice_enabled"] is False
    assert config["voice_read_only"] is True
    assert config["voice_store_audio"] is False
    assert config["voice_model"] == "gpt-realtime-2.1"
    assert config["voice_transcription_model"] == "gpt-4o-transcribe"
    assert config["voice_name"] == "cedar"
    assert config["voice_max_call_minutes"] == 30
    assert config["voice_max_concurrent_calls"] == 3


def test_completed_voice_exchange_uses_phone_conversation_and_history(tmp_path, monkeypatch):
    monkeypatch.setitem(codex_console.__dict__, "BASE_DIR", str(tmp_path))
    monkeypatch.setitem(codex_console.__dict__, "PASTA_INFO", str(tmp_path / "info"))
    codex_console.CODEX_TASKS.clear()
    session = {"client_id": "cliente", "username": "operador", "permissions": {"full": True}}

    result = codex_console.codex_registrar_interacao_whatsapp_externa(
        client_id="cliente",
        username="operador",
        phone="+55 (37) 99999-3818",
        prompt="Qual é o estoque do SKU 299-2?",
        response="A consulta confirmou uma unidade na loja selecionada.",
        call_id="call-test-1",
        duration_seconds=47,
        sources=["Bling"],
    )
    records = codex_console._codex_whatsapp_history_records(session)
    assert len(records) == 1
    assert records[0]["conversation_id"] == result["conversation_id"]
    message = codex_console._codex_whatsapp_history_message_preview(records[0]["task"])
    assert message["interaction_type"] == "call"
    assert message["call_id"] == "call-test-1"
    assert message["duration_seconds"] == 47
    task_file = Path(codex_console._codex_task_path(result["task_id"]))
    stored = task_file.read_text(encoding="utf-8")
    assert "audio" not in stored.lower()
    codex_console.CODEX_TASKS.clear()


def test_voice_d1_schema_never_stores_audio_or_transcript():
    migration = Path("cloudflare/whatsapp-gateway/migrations/0006_voice_calls.sql").read_text(encoding="utf-8").lower()
    assert "create table if not exists voice_calls" in migration
    assert "audio_blob" not in migration
    assert "audio_url" not in migration
    assert "transcript" not in migration


def test_admin_status_redacts_fingerprint_and_filters_other_users_calls(monkeypatch):
    from backend.services import whatsapp_bridge

    fingerprint = "a" * 16
    monkeypatch.setattr(whatsapp_bridge, "_whisper_status", lambda: {"ready": True})
    monkeypatch.setattr(codex_console, "_codex_status_payload", lambda: {"ready": True, "enabled": True})
    monkeypatch.setattr(whatsapp_bridge, "_load_state", lambda: {})
    monkeypatch.setattr(
        whatsapp_bridge.codex_whatsapp_agents.CONVERSATION_RUNTIME,
        "diagnostics",
        lambda: {"ready": True},
    )
    monkeypatch.setattr(
        whatsapp_bridge.whatsapp_voice.VOICE_RUNTIME,
        "diagnostics",
        lambda: {
            "ready": True,
            "dependency_installed": True,
            "api_key_configured": True,
            "api_key_fingerprint": fingerprint,
            "active_calls": [],
            "active_call_count": 0,
        },
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_gateway_json",
        lambda *_args, **_kwargs: {
            "success": True,
            "configured": True,
            "openai": {"api_key_configured": True, "key_fingerprint": fingerprint},
            "sip": {"configured": True},
            "heartbeats": [{"key_fingerprint": fingerprint}],
            "calls": [
                {"id": "mine", "client_id": "cliente", "username": "operador"},
                {"id": "other", "client_id": "outro", "username": "operador"},
            ],
        },
    )

    status = whatsapp_bridge._public_status(
        {
            **whatsapp_bridge._default_config(),
            "client_id": "cliente",
            "username": "operador",
            "machine_id": "machine-1",
            "worker_url": "https://example.workers.dev",
            "bridge_token": "configured",
            "voice_enabled": True,
        },
        {"success": True, "bindings": []},
    )

    assert status["voice"]["ready"] is True
    assert [item["id"] for item in status["voice"]["recent_calls"]] == ["mine"]
    serialized = json.dumps(status["voice"])
    assert fingerprint not in serialized
    assert "key_fingerprint" not in serialized


def test_voice_listing_delivery_uses_bound_subject_and_proactive_photos(monkeypatch):
    from backend.services.whatsapp import artifacts

    text_payloads: list[dict] = []
    photo_calls: list[dict] = []

    class DeliveryBridge:
        @staticmethod
        def _post_proactive(_config, payload):
            text_payloads.append(payload)
            return {"success": True, "status": "sent"}

    def deliver_photos(_config, **kwargs):
        photo_calls.append(kwargs)
        return [{"success": True}, {"success": True}]

    monkeypatch.setattr(artifacts, "_whatsapp_deliver_marketplace_listing_images_proactive", deliver_photos)
    bundle = {
        "source": "mercado_livre_api",
        "stores": ["Loja Principal"],
        "listings": [{
            "store": "Loja Principal",
            "item_id": "MLB123456789",
            "sku": "ABC-123",
            "title": "Produto real",
            "status": "active",
            "currency_id": "BRL",
            "price": 99.9,
            "available_quantity": 4,
            "permalink": "https://produto.mercadolivre.com.br/MLB123456789",
            "description": "Descricao oficial",
            "pictures": [{"secure_url": "https://http2.mlstatic.com/photo.jpg"}],
        }],
        "listing_count": 1,
        "picture_count": 1,
        "coverage_complete": True,
    }
    result = whatsapp_voice.VoiceRuntime._deliver_requested_listing(
        {"machine_id": "machine"},
        DeliveryBridge,
        {"id": "call-1", "subject_id": "subject-bound"},
        {"task_id": "task-1", "final_response": "fallback", "whatsapp_listing_bundle": bundle},
        "Mande o link, a descricao e as fotos do SKU ABC-123",
        "Encontrei o anuncio.",
    )

    assert result == {
        "attempted": True,
        "text_sent": True,
        "pictures_requested": True,
        "images_sent": 2,
        "images_attempted": 2,
    }
    assert text_payloads[0]["subject_id"] == "subject-bound"
    assert text_payloads[0]["event_type"] == "task_completed"
    assert text_payloads[0]["fingerprint"].startswith("voice-task:")
    assert "MLB123456789" in text_payloads[0]["text"]
    assert "https://produto.mercadolivre.com.br/MLB123456789" in text_payloads[0]["text"]
    assert photo_calls[0]["subject_id"] == "subject-bound"
    assert photo_calls[0]["max_images"] == 3


def test_voice_does_not_send_proactively_without_explicit_listing_delivery():
    class DeliveryBridge:
        @staticmethod
        def _post_proactive(_config, _payload):
            raise AssertionError("must not send a general spoken answer proactively")

    result = whatsapp_voice.VoiceRuntime._deliver_requested_listing(
        {},
        DeliveryBridge,
        {"id": "call-1", "subject_id": "subject-bound"},
        {"task_id": "task-1"},
        "Qual e o horario de atendimento?",
        "Atendemos em horario comercial.",
    )
    assert result == {"attempted": False, "text_sent": False, "images_sent": 0}
