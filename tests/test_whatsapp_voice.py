from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from backend.services import codex_console, whatsapp_voice
from backend.services.codex.console import bindings as console_bindings
from backend.services.codex.console import runtime as console_runtime
from backend.services.codex.console import state as console_state
from backend.services.codex.console import task_store as console_task_store
from backend.services.codex.console import tasks as console_tasks


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
    current_runtime = console_bindings.current()
    monkeypatch.setattr(console_bindings, "_RUNTIME", console_bindings.ConsoleRuntime(
        str(tmp_path), str(tmp_path / "info"), current_runtime.session_loader,
        current_runtime.permissions_loader, current_runtime.source_module,
    ))
    console_state.CODEX_TASKS.clear()
    session = {"client_id": "cliente", "username": "operador", "permissions": {"full": True}}

    result = console_tasks.register_external_exchange(
        client_id="cliente",
        username="operador",
        phone="+55 (37) 99999-3818",
        prompt="Qual é o estoque do SKU 299-2?",
        response="A consulta confirmou uma unidade na loja selecionada.",
        call_id="call-test-1",
        duration_seconds=47,
        sources=["Bling"],
    )
    records = console_task_store._codex_whatsapp_history_records(session)
    assert len(records) == 1
    assert records[0]["conversation_id"] == result["conversation_id"]
    message = console_task_store._codex_whatsapp_history_message_preview(records[0]["task"])
    assert message["interaction_type"] == "call"
    assert message["call_id"] == "call-test-1"
    assert message["duration_seconds"] == 47
    task_file = Path(console_runtime._codex_task_path(result["task_id"]))
    stored = task_file.read_text(encoding="utf-8")
    assert "audio" not in stored.lower()
    console_state.CODEX_TASKS.clear()


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
    monkeypatch.setattr(console_runtime, "_codex_status_payload", lambda: {"ready": True, "enabled": True})
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


def test_voice_task_keeps_running_after_old_global_deadline(monkeypatch):
    decisions = iter((
        {
            "action": "delegate",
            "job_prompt": "consulte os dados completos",
            "job_title": "Consulta longa",
            "resolved_context": {},
        },
        {"action": "reply", "reply_text": "Consulta concluida com dados confirmados."},
    ))

    class RuntimeBridge:
        @staticmethod
        def _reload_bound_session(_config, _message):
            return {"client_id": "cliente", "username": "operador", "permissions": {"full": True}}

        @staticmethod
        def _conversation_id(_config, _message):
            return "voice-conversation"

        @staticmethod
        def _phone_notification_settings(*_args, **_kwargs):
            return {"ai_behavior": "consultivo"}

        @staticmethod
        def _normalize_phone_ai_behavior(value):
            return value

        @staticmethod
        def _load_state():
            return {}

        @staticmethod
        def _whatsapp_session_stores(_session):
            return ["JK Pecas"]

        @staticmethod
        def _run_conversation_agent(*_args, **_kwargs):
            return next(decisions)

        @staticmethod
        def _dual_agent_query_policy(*_args, **_kwargs):
            return {"store_mode": "single", "store_matches": ["JK Pecas"]}

        @staticmethod
        def _create_dual_worker_task(*_args, **_kwargs):
            return {"task_id": "voice-task-long", "status": "running", "channel_metadata": {}}

    running = {"task_id": "voice-task-long", "status": "running", "channel_metadata": {}}
    completed = {
        "task_id": "voice-task-long",
        "status": "completed",
        "channel_metadata": {},
        "final_response": "Consulta concluida com dados confirmados.",
    }
    loaded_tasks = iter((running, completed, completed))
    clock = iter((0.0, 0.0, 1_000.0))
    progress_messages: list[tuple[str, bool]] = []

    monkeypatch.setattr(whatsapp_voice.time, "time", lambda: next(clock, 1_000.0))
    monkeypatch.setattr(whatsapp_voice.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: next(loaded_tasks))
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: completed)
    monkeypatch.setattr(console_tasks, "cancel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("voice must not cancel by elapsed time")),
    )
    monkeypatch.setattr(
        whatsapp_voice.codex_whatsapp_agents,
        "normalize_worker_result",
        lambda _task: {"status": "completed", "summary": "Consulta concluida com dados confirmados."},
    )
    monkeypatch.setattr(
        whatsapp_voice.VoiceRuntime,
        "_deliver_requested_listing",
        lambda *_args, **_kwargs: {"attempted": False, "text_sent": False, "images_sent": 0},
    )

    answer = whatsapp_voice.VoiceRuntime()._answer_turn(
        {},
        RuntimeBridge,
        {"id": "call-long", "subject_id": "subject", "wa_id": "5537999990000"},
        {"task_ids": []},
        "Consulte um relatorio demorado",
        lambda text, choice=False: progress_messages.append((text, choice)),
    )

    assert answer == "Consulta concluida com dados confirmados."
    assert any(choice for _text, choice in progress_messages)


def test_ended_call_releases_capacity_while_nonterminal_task_is_observed(monkeypatch):
    runtime = whatsapp_voice.VoiceRuntime()
    call = {
        "id": "call-never-terminal",
        "subject_id": "subject",
        "wa_id": "5537999990000",
    }
    state = {
        "started_epoch": 1.0,
        "task_ids": [],
        "usage": {},
        "call_released_event": threading.Event(),
        "background_delivery_started": False,
    }
    runtime._calls[call["id"]] = state
    runtime._threads[call["id"]] = threading.current_thread()
    detached: list[str] = []
    decisions = iter(({
        "action": "delegate",
        "job_prompt": "consulta sem prazo global",
        "job_title": "Consulta longa",
        "resolved_context": {},
    },))

    class RuntimeBridge:
        @staticmethod
        def _now():
            return "2026-07-21T00:00:00Z"

        @staticmethod
        def _gateway_json(*_args, **_kwargs):
            return {"success": True}

        @staticmethod
        def _reload_bound_session(_config, _message):
            return {"client_id": "cliente", "username": "operador", "permissions": {"full": True}}

        @staticmethod
        def _conversation_id(_config, _message):
            return "voice-conversation"

        @staticmethod
        def _phone_notification_settings(*_args, **_kwargs):
            return {"ai_behavior": "consultivo"}

        @staticmethod
        def _normalize_phone_ai_behavior(value):
            return value

        @staticmethod
        def _load_state():
            return {}

        @staticmethod
        def _whatsapp_session_stores(_session):
            return ["JK Pecas"]

        @staticmethod
        def _run_conversation_agent(*_args, **_kwargs):
            return next(decisions)

        @staticmethod
        def _dual_agent_query_policy(*_args, **_kwargs):
            return {"store_mode": "single", "store_matches": ["JK Pecas"]}

        @staticmethod
        def _create_dual_worker_task(*_args, **_kwargs):
            return {"task_id": "voice-task-never", "status": "running", "channel_metadata": {}}

    async def ended_call(_config, _bridge, _call, current_state):
        current_state["call_released_event"].set()
        assert runtime._answer_turn(
            {}, RuntimeBridge, call, current_state, "consulta longa", lambda *_args, **_kwargs: None,
        ) == ""

    monkeypatch.setattr(runtime, "_run_call", ended_call)
    monkeypatch.setattr(
        runtime,
        "_start_detached_task_observer",
        lambda *_args, **kwargs: detached.append(str(kwargs.get("task_id") or "")),
    )
    monkeypatch.setattr(console_tasks, "load", lambda _task_id: {
        "task_id": "voice-task-never", "status": "running", "channel_metadata": {},
    })
    monkeypatch.setattr(console_tasks, "update", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(console_tasks, "cancel",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("call end must not cancel task")),
    )

    runtime._run_call_thread({}, RuntimeBridge, call, state)

    assert detached == ["voice-task-never"]
    assert call["id"] not in runtime._calls
    assert call["id"] not in runtime._threads


def test_detached_voice_observer_delivers_completed_task_by_message(monkeypatch):
    runtime = whatsapp_voice.VoiceRuntime()
    state = {"background_delivery_started": False}
    delivered = threading.Event()
    sent: list[str] = []
    tasks = iter((
        {"task_id": "voice-task", "status": "running"},
        {"task_id": "voice-task", "status": "completed"},
    ))

    monkeypatch.setattr(console_tasks, "load", lambda _task_id: next(tasks))
    monkeypatch.setattr(whatsapp_voice.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runtime, "_complete_delegated_turn", lambda *_args, **_kwargs: "Resultado confirmado.")

    def send(_config, _bridge, _call, answer):
        sent.append(answer)
        delivered.set()
        return {"success": True}

    monkeypatch.setattr(runtime, "_send_result_message", send)

    runtime._start_detached_task_observer(
        {}, object(), {"id": "call", "wa_id": "5537999990000"}, state,
        transcript="consulta",
        task_id="voice-task",
        latest={"task_id": "voice-task", "status": "running"},
        local_state={},
        conversation_id="conversation",
        ai_behavior="consultivo",
        session={"client_id": "cliente"},
    )

    assert delivered.wait(2)
    assert sent == ["Resultado confirmado."]
