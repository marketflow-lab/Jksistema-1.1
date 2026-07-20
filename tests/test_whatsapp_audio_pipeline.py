import json
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import whatsapp_bridge, whatsapp_transcribe
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import transcription as transcription_component
from backend.services.whatsapp.orchestration import processor as processor_component


def _reset_transcription_queue(monkeypatch) -> dict[str, int | str]:
    state: dict[str, int | str] = {
        "active": 0,
        "waiting": 0,
        "accepted": 0,
        "completed": 0,
        "failed": 0,
        "rejected_busy": 0,
        "last_error_code": "",
        "last_latency_ms": 0,
    }
    monkeypatch.setattr(transcription_component, "_AUDIO_TRANSCRIPTION_CAPACITY", threading.BoundedSemaphore(5))
    monkeypatch.setattr(transcription_component, "_AUDIO_TRANSCRIPTION_EXECUTION", threading.Lock())
    monkeypatch.setattr(transcription_component, "_AUDIO_TRANSCRIPTION_STATE_LOCK", threading.Lock())
    monkeypatch.setattr(transcription_component, "_AUDIO_TRANSCRIPTION_STATE", state)
    return state


def _configure_transcription_child(monkeypatch, tmp_path, run) -> Path:
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"local-audio")
    model = tmp_path / "model"
    model.mkdir()
    runner = tmp_path / "whatsapp_transcribe.py"
    runner.write_text("# local runner", encoding="utf-8")
    monkeypatch.setattr(transcription_component, "_whisper_status", lambda: {"ready": True, "error_code": ""})
    monkeypatch.setattr(transcription_component, "_info_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(transcription_component, "_model_dir", lambda: model, raising=False)
    monkeypatch.setattr(transcription_component, "_whisper_runner", lambda: runner)
    monkeypatch.setattr(transcription_component, "TRANSCRIPTION_TIMEOUT_SECONDS", 10, raising=False)
    monkeypatch.setattr(
        transcription_component,
        "_json_read",
        lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
        raising=False,
    )
    monkeypatch.setattr(transcription_component.subprocess, "run", run)
    return audio


def test_local_model_load_uses_verified_snapshot_without_download(monkeypatch, tmp_path):
    snapshot = tmp_path / "model" / "snapshots" / "verified"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"verified")
    (tmp_path / "model" / "model-manifest.json").write_text(
        json.dumps({"files": [{"path": "snapshots/verified/model.bin"}]}),
        encoding="utf-8",
    )
    captured = {}

    class FakeWhisperModel:
        def __init__(self, model_path, **kwargs):
            captured.update({"model_path": model_path, **kwargs})

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))

    whatsapp_transcribe._load_model(tmp_path / "model")

    assert captured["model_path"] == str(snapshot.resolve())
    assert captured["local_files_only"] is True
    assert "download_root" not in captured


def test_real_preflight_loads_child_locally_and_returns_safe_status(monkeypatch, tmp_path):
    runner = tmp_path / "whatsapp_transcribe.py"
    runner.write_text("# runner", encoding="utf-8")
    model = tmp_path / "private-model-dir"
    model.mkdir()
    monkeypatch.setattr(
        transcription_component,
        "_whisper_status",
        lambda: {
            "ready": True,
            "dependency_installed": True,
            "av_installed": True,
            "runner_ready": True,
            "integrity": "verified_sha256",
            "engine": "faster-whisper==1.2.1",
            "error_code": "",
        },
    )
    monkeypatch.setattr(transcription_component, "_info_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(transcription_component, "_model_dir", lambda: model, raising=False)
    monkeypatch.setattr(transcription_component, "_whisper_runner", lambda: runner)
    monkeypatch.setattr(
        transcription_component,
        "_json_read",
        lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path.exists() else default,
        raising=False,
    )

    def fake_run(args, **_kwargs):
        assert "--preflight" in args
        assert "--audio" not in args
        output = Path(args[args.index("--output") + 1])
        output.write_text(json.dumps({"success": True, "local_only": True}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="must-not-leak")

    monkeypatch.setattr(transcription_component.subprocess, "run", fake_run)

    result = transcription_component._audio_messages_preflight()

    assert result["success"] is True
    assert result["audio_messages"]["local_only"] is True
    assert result["audio_messages"]["retention"] == {
        "raw_audio": "delete_immediately_after_terminal",
        "transcription_persisted": False,
    }
    assert result["audio_messages"]["preflight"]["model_load"] is True
    serialized = json.dumps(result)
    assert str(model) not in serialized
    assert "must-not-leak" not in serialized


def test_audio_telemetry_keeps_only_safe_buckets(monkeypatch, tmp_path):
    _reset_transcription_queue(monkeypatch)
    monkeypatch.setenv("JK_APP_VERSION", "1.0.104")
    monkeypatch.setattr(
        transcription_component,
        "_whisper_status",
        lambda: {
            "ready": True,
            "engine": "faster-whisper==1.2.1",
            "dependency_installed": True,
            "av_installed": True,
            "runner_ready": True,
            "integrity": "verified_sha256",
        },
    )
    private_path = tmp_path / "5511999999999-texto-secreto.ogg"

    transcription_component._record_audio_outcome(
        success=False,
        error_code="low_confidence",
        latency_ms=321,
        audio_path=private_path,
        size_bytes=2 * 1024 * 1024,
        duration_seconds=42,
    )
    telemetry = transcription_component._audio_messages_status()["telemetry"]
    last = telemetry["last"]

    assert set(last) == {
        "stage", "mime", "size_bucket", "duration_bucket",
        "runtime_version", "latency_ms", "success", "error_code",
    }
    assert last == {
        "stage": "transcription_failed",
        "mime": "audio/ogg",
        "size_bucket": "1_to_4mb",
        "duration_bucket": "30_to_120s",
        "runtime_version": "1.0.104",
        "latency_ms": 321,
        "success": False,
        "error_code": "low_confidence",
    }
    serialized = json.dumps(telemetry)
    assert "5511999999999" not in serialized
    assert "texto-secreto" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize("segments, expected", [([], "no_speech"), ([(-2.0, "inaudivel")], "low_confidence")])
def test_runner_rejects_empty_or_low_confidence_transcription(monkeypatch, tmp_path, segments, expected):
    class Segment:
        def __init__(self, logprob, text):
            self.avg_logprob = logprob
            self.text = text

    class Info:
        language = "pt"
        language_probability = 0.9
        duration = 1.0

    class Model:
        def transcribe(self, _path, **_kwargs):
            return iter([Segment(logprob, text) for logprob, text in segments]), Info()

    monkeypatch.setattr(whatsapp_transcribe, "_audio_duration", lambda _path: 1.0)
    monkeypatch.setattr(whatsapp_transcribe, "_verify_manifest", lambda _path: None)
    monkeypatch.setattr(whatsapp_transcribe, "_load_model", lambda _path: Model())

    with pytest.raises(RuntimeError, match=expected):
        whatsapp_transcribe._transcribe(tmp_path / "model", tmp_path / "voice.ogg")


def test_transcription_queue_allows_one_active_four_waiting_and_rejects_sixth(monkeypatch, tmp_path):
    state = _reset_transcription_queue(monkeypatch)
    release = threading.Event()

    def fake_run(args, **_kwargs):
        release.wait(5)
        output = Path(args[args.index("--output") + 1])
        output.write_text(
            json.dumps({"success": True, "text": "pedido por audio", "duration_seconds": 1.2, "confidence": 0.9}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    audio = _configure_transcription_child(monkeypatch, tmp_path, fake_run)
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(transcription_component._transcribe_audio(audio)))
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with transcription_component._AUDIO_TRANSCRIPTION_STATE_LOCK:
            if state["active"] == 1 and state["waiting"] == 4:
                break
        time.sleep(0.01)
    with transcription_component._AUDIO_TRANSCRIPTION_STATE_LOCK:
        assert state["active"] == 1
        assert state["waiting"] == 4

    rejected = transcription_component._transcribe_audio(audio)
    release.set()
    for thread in threads:
        thread.join(5)

    assert rejected["error_code"] == "resource_busy"
    assert len(results) == 5
    assert all(result["success"] is True and result["local_only"] is True for result in results)
    assert state["rejected_busy"] == 1
    assert state["active"] == 0
    assert state["waiting"] == 0


def test_terminal_audio_failure_replies_without_calling_ai_and_deletes_raw_file(monkeypatch, tmp_path):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    replies = []
    monkeypatch.setattr(whatsapp_bridge, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_pending_task_for_message", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_reload_bound_session",
        lambda *_args: {"client_id": "client", "username": "admin", "permissions": {"full": True}, "is_full": True},
    )
    monkeypatch.setattr(whatsapp_bridge, "_phone_notification_settings", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(whatsapp_bridge, "_normalize_phone_ai_behavior", lambda _value: "")
    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5511999999999")
    monkeypatch.setattr(whatsapp_bridge, "_conversation_id", lambda *_args: "conversation")
    monkeypatch.setattr(
        whatsapp_bridge,
        "_download_media",
        lambda *_args: {
            "mime_type": "audio/ogg",
            "size": attachment.stat().st_size,
            "path": ".codex-remote-attachments/client/voice.ogg",
        },
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_transcribe_audio",
        lambda path: {"success": False, "error_code": "low_confidence", "error": str(path), "local_only": True},
    )
    monkeypatch.setattr(whatsapp_bridge, "_message_request_text", lambda *_args: "")
    monkeypatch.setattr(whatsapp_bridge, "_post_command_reply", lambda _cfg, _mid, text, title: replies.append((title, text)))
    monkeypatch.setattr(processor_component, "_handle_inbound_commands", lambda *_args: pytest.fail("failure must stop before routing"), raising=False)
    monkeypatch.setattr(whatsapp_bridge, "_create_selected_ai_task", lambda *_args, **_kwargs: pytest.fail("failure must not call AI"))

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject", "voice_enabled": False},
        {},
        {
            "message_id": "wamid.audio",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert not attachment.exists()
    assert replies == [("BLACK JHON - AUDIO NAO PROCESSADO", "Nao consegui entender o audio com seguranca. Envie novamente com menos ruido ou escreva a mensagem.")]
    assert str(tmp_path) not in replies[0][1]


def test_successful_audio_routes_transcript_only_after_raw_delete(monkeypatch, tmp_path):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    routed = []
    monkeypatch.setattr(whatsapp_bridge, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_pending_task_for_message", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_reload_bound_session",
        lambda *_args: {"client_id": "client", "username": "admin", "permissions": {"full": True}, "is_full": True},
    )
    monkeypatch.setattr(whatsapp_bridge, "_phone_notification_settings", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(whatsapp_bridge, "_normalize_phone_ai_behavior", lambda _value: "")
    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5511999999999")
    monkeypatch.setattr(whatsapp_bridge, "_conversation_id", lambda *_args: "conversation")
    monkeypatch.setattr(
        whatsapp_bridge,
        "_download_media",
        lambda *_args: {
            "mime_type": "audio/ogg",
            "size": attachment.stat().st_size,
            "path": ".codex-remote-attachments/client/voice.ogg",
        },
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_transcribe_audio",
        lambda path: {"success": True, "text": "consulte o MLB123", "duration_seconds": 2.0, "confidence": 0.95},
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_message_request_text",
        lambda _message, transcription: str(transcription.get("text") or ""),
    )
    monkeypatch.setattr(
        processor_component,
        "_handle_inbound_commands",
        lambda _config, _state, message, _session: routed.append(dict(message)) or True,
        raising=False,
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject", "voice_enabled": False},
        {},
        {
            "message_id": "wamid.audio-success",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert not attachment.exists()
    assert routed == [{
        "message_id": "wamid.audio-success",
        "machine_id": "machine",
        "subject_id": "subject",
        "wa_id": "5511999999999",
        "media_id": "media",
        "message_type": "text",
        "text_body": "consulte o MLB123",
    }]


def test_audio_preflight_endpoint_requires_admin_and_delegates(monkeypatch):
    authorized = []
    monkeypatch.setattr(whatsapp_bridge, "_require_full", lambda request, authorization: authorized.append((request, authorization)))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_audio_messages_preflight",
        lambda: {"success": True, "audio_messages": {"ready": True, "local_only": True}},
    )
    request = object()

    result = whatsapp_bridge.whatsapp_bridge_audio_preflight(request, "Bearer admin")

    assert authorized == [(request, "Bearer admin")]
    assert result == {"success": True, "audio_messages": {"ready": True, "local_only": True}}


def test_processor_hands_only_transcript_text_to_luna(monkeypatch):
    captured = []
    action_message = {
        "message_id": "wamid.text-only",
        "machine_id": "machine",
        "subject_id": "subject",
        "message_type": "text",
        "text_body": "consulte o MLB123",
    }
    monkeypatch.setattr(
        processor_component,
        "_prepare_inbound_message",
        lambda *_args: {
            "handled": False,
            "message_id": "wamid.text-only",
            "subject": "subject",
            "session": {
                "username": "admin", "client_id": "cliente",
                "is_full": True, "permissions": {"full": True},
            },
            "phone_ai_behavior": "",
            "phone": "5511999999999",
            "conversation_id": "conversation",
            "media": {"mime_type": "audio/ogg", "path": "private/voice.ogg"},
            "transcription": {
                "success": True,
                "text": "consulte o MLB123",
                "duration_seconds": 3.2,
                "confidence": 0.91,
                "language": "pt",
            },
            "request_text": "consulte o MLB123",
            "message": action_message,
        },
    )
    monkeypatch.setattr(processor_component, "_handle_inbound_commands", lambda *_args: False, raising=False)
    monkeypatch.setattr(
        processor_component,
        "_apply_store_selection",
        lambda _cfg, _state, message, _session, _conversation, request, _mid, _subject: (message, request, False),
    )
    monkeypatch.setattr(
        processor_component,
        "_process_dual_codex_message",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    processor_component._IMPLEMENTATIONS["_process_message"]({"machine_id": "machine"}, {}, action_message)

    assert len(captured) == 1
    launch_args, launch_kwargs = captured[0]
    assert launch_kwargs["request_text"] == "consulte o MLB123"
    assert launch_kwargs["media"] is None
    assert launch_kwargs["transcription"] is None
    prompt = whatsapp_message.message_prompt(launch_args[2], launch_kwargs["media"], launch_kwargs["transcription"])
    prompt_lower = prompt.lower()
    assert "consulte o mlb123" in prompt_lower
    for forbidden in ("private/voice.ogg", "audio/ogg", "duracao:", "confianca:", "idioma:"):
        assert forbidden not in prompt_lower
    assert processor_component._provider_task_attachments([]) == []


@pytest.mark.parametrize(
    ("mime_type", "extension"),
    [
        ("audio/ogg", ".ogg"),
        ("audio/aac", ".aac"),
        ("audio/mp4", ".m4a"),
        ("audio/mpeg", ".mp3"),
        ("audio/amr", ".amr"),
    ],
)
def test_supported_audio_mime_and_extension_contract(mime_type, extension):
    assert whatsapp_media.SUPPORTED_AUDIO_MIMES[mime_type] == 16 * 1024 * 1024
    assert whatsapp_media.media_extension(mime_type) == extension


@pytest.mark.parametrize(
    ("suffix", "container_format", "codec_name", "sample_rate", "frame_samples"),
    [
        (".ogg", "ogg", "libopus", 48_000, 960),
        (".aac", "adts", "aac", 44_100, 1024),
        (".m4a", "ipod", "aac", 44_100, 1024),
        (".mp3", "mp3", "libmp3lame", 44_100, 1152),
        (".amr", "amr", "libopencore_amrnb", 8_000, 160),
    ],
)
def test_pyav_decodes_synthetic_silence_formats(
    tmp_path, suffix, container_format, codec_name, sample_rate, frame_samples
):
    av = pytest.importorskip("av")
    try:
        av.codec.Codec(codec_name, "w")
    except Exception:
        pytest.skip(f"encoder {codec_name} unavailable; MIME admission remains covered")
    target = tmp_path / f"synthetic-silence{suffix}"
    try:
        with av.open(str(target), mode="w", format=container_format) as container:
            stream = container.add_stream(codec_name, rate=sample_rate)
            stream.layout = "mono"
            written = 0
            target_samples = sample_rate
            while written < target_samples:
                samples = min(frame_samples, target_samples - written)
                frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
                frame.sample_rate = sample_rate
                frame.pts = written
                frame.time_base = Fraction(1, sample_rate)
                frame.planes[0].update(bytes(frame.planes[0].buffer_size))
                for packet in stream.encode(frame):
                    container.mux(packet)
                written += samples
            for packet in stream.encode(None):
                container.mux(packet)
    except Exception as exc:
        if suffix == ".amr":
            pytest.skip(f"AMR encoder/muxer unavailable; MIME admission remains covered: {type(exc).__name__}")
        raise

    duration = whatsapp_transcribe._audio_duration(target)

    assert 0.75 <= duration <= 1.25
