import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import codex_console, whatsapp_bridge, whatsapp_transcribe
from backend.services.whatsapp import audio_processing as whatsapp_audio_processing
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import transcription as transcription_component
from backend.services.whatsapp.runtime import lifecycle as lifecycle_component
from backend.services.whatsapp.orchestration import processor as processor_component


def _downloaded_audio(path: Path, *, size: int | None = None):
    return transcription_component.DownloadedMedia(
        public_payload={
            "id": "audio-public-id",
            "name": "voice.ogg",
            "mime_type": "audio/ogg",
            "size": int(path.stat().st_size if size is None else size),
        },
        local_path=path.resolve(),
    )


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


def test_machine_wide_audio_status_exposes_no_per_client_events(monkeypatch, tmp_path):
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
    status = transcription_component._audio_messages_status()
    assert status["last_error_code"] == "low_confidence"
    assert status["last_latency_ms"] == 321
    assert status["telemetry"] == {"stage_counts": {}, "error_counts": {}, "last": {}}
    serialized = json.dumps(status["telemetry"])
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


def test_downloaded_media_is_frozen_and_keeps_private_path_out_of_public_payload(tmp_path):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")

    downloaded = _downloaded_audio(attachment)

    assert downloaded.local_path == attachment.resolve()
    assert downloaded.public_payload == {
        "id": "audio-public-id",
        "name": "voice.ogg",
        "mime_type": "audio/ogg",
        "size": len(b"raw-audio"),
    }
    serialized = json.dumps(dict(downloaded.public_payload))
    assert "path" not in downloaded.public_payload
    assert str(tmp_path) not in serialized
    with pytest.raises(FrozenInstanceError):
        downloaded.local_path = tmp_path / "other.ogg"
    with pytest.raises(TypeError):
        downloaded.public_payload["name"] = "changed.ogg"
    with pytest.raises(TypeError):
        dict.__setitem__(downloaded.public_payload, "name", "bypass.ogg")


def test_real_download_flows_through_processor_and_routes_only_text(monkeypatch, tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_dir = attachment_root / "client" / "conversation"
    attachment_dir.mkdir(parents=True)
    response_closed = []
    transcribed: list[Path] = []
    routed: list[dict] = []

    class FakeResponse:
        headers = {
            "content-type": "audio/ogg",
            "x-jk-filename": "voice.ogg",
        }

        @staticmethod
        def iter_content(_chunk_size):
            yield b"OggS-real-simulated-audio"

        @staticmethod
        def close():
            response_closed.append(True)

    monkeypatch.setattr(whatsapp_bridge, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(whatsapp_bridge, "_gateway_request", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(
        whatsapp_bridge.codex_console,
        "_codex_attachment_dir",
        lambda *_args: attachment_dir,
    )
    monkeypatch.setattr(
        whatsapp_bridge.codex_console,
        "_codex_attachments_base_dir",
        lambda: attachment_root,
    )
    monkeypatch.setattr(whatsapp_bridge, "_start_typing_pulse", lambda *_args: None)
    monkeypatch.setattr(whatsapp_bridge, "_pending_task_for_message", lambda *_args: None)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_reload_bound_session",
        lambda *_args: {
            "client_id": "client",
            "username": "admin",
            "permissions": {"full": True},
            "is_full": True,
        },
    )
    monkeypatch.setattr(whatsapp_bridge, "_phone_notification_settings", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(whatsapp_bridge, "_normalize_phone_ai_behavior", lambda _value: "")
    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5511999999999")
    monkeypatch.setattr(whatsapp_bridge, "_conversation_id", lambda *_args: "conversation")

    def transcribe(path: Path):
        transcribed.append(path)
        assert path.is_file()
        path.resolve().relative_to(attachment_root.resolve())
        return {
            "success": True,
            "text": "consulte o SKU ABC-123",
            "duration_seconds": 3.0,
            "confidence": 0.95,
        }

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
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
        {"machine_id": "machine", "subject_id": "subject", "client_id": "client", "username": "admin"},
        {},
        {
            "message_id": "wamid.real-download",
            "machine_id": "machine",
            "subject_id": "subject",
            "client_id": "client",
            "username": "admin",
            "wa_id": "5511999999999",
            "media_id": "media",
            "media_mime": "audio/ogg",
            "media_size": len(b"OggS-real-simulated-audio"),
            "message_type": "audio",
        },
    )

    assert response_closed == [True]
    assert len(transcribed) == 1
    assert not transcribed[0].exists()
    assert routed == [{
        "message_id": "wamid.real-download",
        "machine_id": "machine",
        "subject_id": "subject",
        "client_id": "client",
        "username": "admin",
        "wa_id": "5511999999999",
        "media_id": "media",
        "media_mime": "audio/ogg",
        "media_size": len(b"OggS-real-simulated-audio"),
        "message_type": "text",
        "text_body": "consulte o SKU ABC-123",
    }]
    assert not any(attachment_root.rglob("*.ogg"))


@pytest.mark.parametrize("transient_error", ["resource_busy", "queue_timeout", "child_failed"])
def test_transient_transcription_failure_retries_once_then_routes_once(
    monkeypatch, tmp_path, transient_error
):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    outcomes = [
        {"success": False, "error_code": transient_error, "local_only": True},
        {"success": True, "text": "consulte o SKU 001", "duration_seconds": 3.0, "confidence": 0.95},
    ]
    attempts: list[Path] = []
    replies: list[str] = []
    routed: list[dict] = []
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
    monkeypatch.setattr(whatsapp_bridge, "_download_media", lambda *_args: _downloaded_audio(attachment))

    def transcribe(path: Path):
        attempts.append(path)
        return outcomes.pop(0)

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
    monkeypatch.setattr(
        whatsapp_bridge,
        "_message_request_text",
        lambda _message, transcription: str(transcription.get("text") or ""),
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_command_reply",
        lambda _cfg, _mid, text, _title: replies.append(text),
    )
    monkeypatch.setattr(
        processor_component,
        "_handle_inbound_commands",
        lambda _config, _state, message, _session: routed.append(dict(message)) or True,
        raising=False,
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject"},
        {},
        {
            "message_id": "wamid.retry-once",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert len(attempts) == 2
    assert attempts[0] == attempts[1] == attachment.resolve()
    assert replies == []
    assert len(routed) == 1
    assert routed[0]["text_body"] == "consulte o SKU 001"
    assert not attachment.exists()


@pytest.mark.parametrize("terminal_error", [
    "no_speech", "low_confidence", "audio_corrupt", "audio_empty",
    "audio_size_limit", "duration_limit", "model_invalid", "transcription_timeout",
])
def test_terminal_transcription_failure_is_not_retried_and_replies_once(
    monkeypatch, tmp_path, terminal_error
):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    attempts: list[Path] = []
    replies: list[str] = []
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
    monkeypatch.setattr(whatsapp_bridge, "_download_media", lambda *_args: _downloaded_audio(attachment))

    def transcribe(path: Path):
        attempts.append(path)
        return {"success": False, "error_code": terminal_error, "local_only": True}

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
    monkeypatch.setattr(whatsapp_bridge, "_message_request_text", lambda *_args: "")
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_command_reply",
        lambda _cfg, _mid, text, _title: replies.append(text),
    )
    monkeypatch.setattr(
        processor_component,
        "_handle_inbound_commands",
        lambda *_args: pytest.fail("terminal failure must stop before routing"),
        raising=False,
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject"},
        {},
        {
            "message_id": "wamid.terminal",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert attempts == [attachment.resolve()]
    assert len(replies) == 1
    assert not attachment.exists()


def test_transient_transcription_exhaustion_stops_after_two_attempts_and_replies_once(monkeypatch, tmp_path):
    attachment = tmp_path / ".codex-remote-attachments" / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    attempts: list[Path] = []
    replies: list[str] = []
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
    monkeypatch.setattr(whatsapp_bridge, "_download_media", lambda *_args: _downloaded_audio(attachment))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_transcribe_audio",
        lambda path: attempts.append(path) or {"success": False, "error_code": "resource_busy", "local_only": True},
    )
    monkeypatch.setattr(whatsapp_bridge, "_message_request_text", lambda *_args: "")
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_command_reply",
        lambda _cfg, _mid, text, _title: replies.append(text),
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject"},
        {},
        {
            "message_id": "wamid.retry-exhausted",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert len(attempts) == 2
    assert len(replies) == 1
    assert not attachment.exists()


def test_audio_path_validation_rejects_missing_directory_and_escape(tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_root.mkdir()
    valid = attachment_root / "valid.ogg"
    valid.write_bytes(b"raw-audio")
    assert whatsapp_audio_processing.validate_inbound_audio_path(valid, attachment_root) == valid.resolve()

    directory = attachment_root / "directory.ogg"
    directory.mkdir()
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"must-not-delete")
    traversal = attachment_root / "nested" / ".." / ".." / outside.name
    missing = attachment_root / "missing.ogg"

    for invalid in (Path(), directory, outside, traversal, missing):
        with pytest.raises(ValueError, match="audio_path_invalid"):
            whatsapp_audio_processing.validate_inbound_audio_path(invalid, attachment_root)
    with pytest.raises(ValueError, match="audio_path_missing"):
        whatsapp_audio_processing.inbound_audio_path({}, tmp_path)



def test_audio_path_validation_rejects_symlink(tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_root.mkdir()
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"must-not-delete")
    symlink = attachment_root / "linked.ogg"
    try:
        symlink.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError, match="audio_path_invalid"):
        whatsapp_audio_processing.validate_inbound_audio_path(symlink, attachment_root)
    assert outside.read_bytes() == b"must-not-delete"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_audio_path_validation_rejects_windows_junction(tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_root.mkdir()
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (outside / "voice.ogg").write_bytes(b"must-not-delete")
    junction = attachment_root / "junction"
    created = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation unavailable")
    try:
        with pytest.raises(ValueError, match="audio_path_invalid"):
            whatsapp_audio_processing.validate_inbound_audio_path(junction / "voice.ogg", attachment_root)
        assert (outside / "voice.ogg").read_bytes() == b"must-not-delete"
    finally:
        os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_download_rejects_junction_before_writing_audio_bytes(monkeypatch, tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_root.mkdir()
    outside = tmp_path / "outside-directory"
    outside.mkdir()
    junction = attachment_root / "redirected-client"
    created = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation unavailable")
    iterated: list[bool] = []
    closed: list[bool] = []

    class FakeResponse:
        headers = {"content-type": "audio/ogg", "x-jk-filename": "voice.ogg"}

        @staticmethod
        def iter_content(_chunk_size):
            iterated.append(True)
            yield b"OggS-must-not-be-written"

        @staticmethod
        def close():
            closed.append(True)

    monkeypatch.setattr(whatsapp_bridge, "_gateway_request", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(whatsapp_bridge.codex_console, "_codex_attachments_base_dir", lambda: attachment_root)
    monkeypatch.setattr(whatsapp_bridge.codex_console, "_codex_attachment_dir", lambda *_args: junction)
    try:
        with pytest.raises(RuntimeError, match="media_local_path_invalid"):
            whatsapp_bridge._download_media(
                {"client_id": "client", "username": "admin"},
                {
                    "message_id": "wamid-junction",
                    "media_mime": "audio/ogg",
                    "media_size": len(b"OggS-must-not-be-written"),
                },
                "conversation",
            )
        assert iterated == []
        assert closed == [True]
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(junction)


def test_audio_cleanup_retries_a_transient_windows_lock(monkeypatch, tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment = attachment_root / "client" / "voice.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    original_unlink = Path.unlink
    attempts = []

    def transient_unlink(path: Path, *args, **kwargs):
        if path == attachment.resolve():
            attempts.append(path)
            if len(attempts) == 1:
                raise PermissionError(32, "simulated sharing violation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient_unlink)

    assert whatsapp_audio_processing.delete_inbound_audio(attachment, attachment_root) is True
    assert len(attempts) == 2
    assert not attachment.exists()


def test_audio_cleanup_telemetry_uses_only_hmac_and_safe_buckets(monkeypatch, tmp_path):
    _reset_transcription_queue(monkeypatch)
    recorded: list[tuple[str, dict]] = []

    class FakeTelemetry:
        def schedule_retention(self, _client_id):
            return True

        def record_event(self, client_id, **payload):
            recorded.append((str(client_id), dict(payload)))
            return True

    monkeypatch.setattr(codex_console, "_codex_ai_telemetry_instance", lambda: FakeTelemetry())
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment = attachment_root / "client" / "5511999999999-texto-secreto.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")

    with whatsapp_audio_processing.audio_telemetry_scope(
        client_id="tenant-a",
        trace_id="cleanup-safe",
        surface="whatsapp",
    ):
        assert whatsapp_audio_processing.delete_inbound_audio(attachment, attachment_root) is True

    assert recorded[-1][0] == "tenant-a"
    assert recorded[-1][1]["dimensions"]["audio_stage"] == "cleanup_succeeded"
    assert recorded[-1][1]["dimensions"]["audio_cleanup_state"] == "removed"
    serialized = json.dumps(recorded[-1][1], ensure_ascii=False)
    assert "5511999999999" not in serialized
    assert "texto-secreto" not in serialized
    assert str(tmp_path) not in serialized


def test_audio_telemetry_is_recorded_per_client_without_content(monkeypatch, tmp_path):
    recorded: list[tuple[str, dict]] = []

    class FakeTelemetry:
        def pseudonymize(self, _value, *, namespace="id"):
            return f"hmac-sha256:v1:{namespace}-safe"

        def schedule_retention(self, _client_id):
            return True

        def record_event(self, client_id, **payload):
            recorded.append((str(client_id), dict(payload)))
            return True

    monkeypatch.setattr(codex_console, "_codex_ai_telemetry_instance", lambda: FakeTelemetry())
    private_path = tmp_path / "5511999999999-transcricao-secreta.ogg"
    with whatsapp_audio_processing.audio_telemetry_scope(
        client_id="tenant-a",
        trace_id="wamid-safe",
        user_id="operator-a",
        surface="whatsapp",
    ):
        transcription_component._record_audio_stage(
            "transcription_attempt",
            audio_path=private_path,
            size_bytes=2048,
            duration_seconds=3,
            attempt=1,
            retries=0,
        )

    assert len(recorded) == 1
    client_id, event = recorded[0]
    assert client_id == "tenant-a"
    assert event["event_type"] == "audio_processing"
    assert event["dimensions"] == {
        "surface": "whatsapp",
        "category": "audio",
        "audio_stage": "transcription_attempt",
        "audio_attempt": "1",
        "audio_retries": "0",
        "audio_mime_bucket": "audio/ogg",
        "audio_size_bucket": "lt_1mb",
        "audio_duration_bucket": "lt_30s",
        "audio_cleanup_state": "none",
    }
    serialized = json.dumps(event, ensure_ascii=False)
    assert "5511999999999" not in serialized
    assert "transcricao-secreta" not in serialized
    assert str(tmp_path) not in serialized


def test_successful_transcription_with_persistent_cleanup_failure_fails_closed_without_retranscribing(
    monkeypatch, tmp_path
):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment = attachment_root / "client" / "private-5511999999999.ogg"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"raw-audio")
    private_transcript = "conteudo privado do audio"
    transcriptions: list[Path] = []
    queued: list[tuple[Path, Path]] = []
    replies: list[str] = []
    routed: list[dict] = []
    state: dict = {}
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
    monkeypatch.setattr(whatsapp_bridge, "_download_media", lambda *_args: _downloaded_audio(attachment))
    monkeypatch.setattr(
        whatsapp_bridge,
        "_transcribe_audio",
        lambda path: transcriptions.append(path) or {"success": True, "text": private_transcript, "local_only": True},
    )
    monkeypatch.setattr(whatsapp_audio_processing, "delete_inbound_audio", lambda *_args: False)
    monkeypatch.setattr(
        whatsapp_audio_processing,
        "queue_inbound_audio_cleanup",
        lambda path, root: queued.append((Path(path), Path(root))) or True,
    )
    monkeypatch.setattr(
        whatsapp_bridge,
        "_post_command_reply",
        lambda _cfg, _mid, text, _title: replies.append(text),
    )
    monkeypatch.setattr(
        processor_component,
        "_handle_inbound_commands",
        lambda _config, _state, message, _session: routed.append(dict(message)) or True,
        raising=False,
    )

    whatsapp_bridge._process_message(
        {"machine_id": "machine", "subject_id": "subject"},
        state,
        {
            "message_id": "wamid.cleanup-failed",
            "machine_id": "machine",
            "subject_id": "subject",
            "wa_id": "5511999999999",
            "media_id": "media",
            "message_type": "audio",
        },
    )

    assert transcriptions == [attachment.resolve()]
    assert queued == [(attachment.resolve(), attachment_root.resolve())]
    assert routed == []
    assert len(replies) == 1
    sanitized = json.dumps({"reply": replies[0], "state": state}, ensure_ascii=False)
    assert private_transcript not in sanitized
    assert "5511999999999" not in sanitized
    assert str(tmp_path) not in sanitized


def test_stale_audio_cleanup_is_bounded_and_recorded_per_client(monkeypatch, tmp_path):
    recorded: list[tuple[str, dict]] = []

    class FakeTelemetry:
        def schedule_retention(self, _client_id):
            return True

        def record_event(self, client_id, **payload):
            recorded.append((str(client_id), dict(payload)))
            return True

    monkeypatch.setattr(codex_console, "_codex_ai_telemetry_instance", lambda: FakeTelemetry())
    attachment_root = tmp_path / ".codex-remote-attachments"
    owned_dir = attachment_root / "client-a" / "user-a"
    owned_dir.mkdir(parents=True)
    old_audio = owned_dir / "old.ogg"
    fresh_audio = owned_dir / "fresh.ogg"
    old_image = owned_dir / "old.jpg"
    unowned_root_audio = attachment_root / "unowned.ogg"
    outside_audio = tmp_path / "outside.ogg"
    for path in (old_audio, fresh_audio, old_image, unowned_root_audio, outside_audio):
        path.write_bytes(b"fixture")
    now_epoch = time.time()
    old_epoch = now_epoch - 901
    os.utime(old_audio, (old_epoch, old_epoch))
    os.utime(old_image, (old_epoch, old_epoch))
    os.utime(unowned_root_audio, (old_epoch, old_epoch))
    os.utime(outside_audio, (old_epoch, old_epoch))
    os.utime(fresh_audio, (now_epoch - 899, now_epoch - 899))

    result = whatsapp_audio_processing.cleanup_stale_audio_files(
        tmp_path,
        older_than_seconds=900,
        now_epoch=now_epoch,
    )

    assert result["removed"] == 1
    assert not old_audio.exists()
    assert fresh_audio.exists()
    assert old_image.exists()
    assert unowned_root_audio.exists()
    assert outside_audio.exists()
    assert recorded and {client_id for client_id, _event in recorded} == {"client-a"}
    assert recorded[-1][1]["dimensions"]["surface"] == "audio_janitor"


def test_audio_cleanup_janitor_runs_at_startup_then_at_most_hourly(monkeypatch, tmp_path):
    calls = []
    runtime_state: dict = {}
    clock = {"now": 10_000.0}
    monkeypatch.setattr(lifecycle_component, "RUNTIME_STATE", runtime_state)
    monkeypatch.setattr(lifecycle_component, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle_component.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        whatsapp_audio_processing,
        "cleanup_stale_audio_files",
        lambda base_dir, **kwargs: calls.append((Path(base_dir), dict(kwargs))) or {
            "scanned": 2,
            "removed": 1,
            "queued": 0,
            "failed": 0,
            "private_path": str(tmp_path / "must-not-persist.ogg"),
        },
    )

    startup = lifecycle_component._IMPLEMENTATIONS["_run_audio_cleanup_janitor"](force=True)
    suppressed = lifecycle_component._IMPLEMENTATIONS["_run_audio_cleanup_janitor"]()
    clock["now"] += 3_601
    periodic = lifecycle_component._IMPLEMENTATIONS["_run_audio_cleanup_janitor"]()

    assert startup == suppressed == periodic == {
        "scanned": 2,
        "removed": 1,
        "queued": 0,
        "failed": 0,
    }
    assert len(calls) == 2
    assert all(call[0] == tmp_path for call in calls)
    assert all(call[1]["older_than_seconds"] == 900 for call in calls)
    assert "private_path" not in runtime_state["audio_cleanup_janitor_result"]
    assert str(tmp_path) not in json.dumps(runtime_state)


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
        lambda *_args: _downloaded_audio(attachment),
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
        lambda *_args: _downloaded_audio(attachment),
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
            "media": {"id": "audio-public-id", "name": "voice.ogg", "mime_type": "audio/ogg", "size": 10},
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
