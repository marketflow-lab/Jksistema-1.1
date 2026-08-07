from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from backend.services import codex_console, local_audio_transcription
from backend.services.whatsapp import audio_processing as whatsapp_audio_processing
from backend.services.codex.console import agent_loop as console_agent_loop
from backend.services.codex.console import attachments_api as console_attachments
from backend.services.codex.console import task_store as console_task_store


@pytest.fixture(autouse=True)
def _isolated_local_voice_root(monkeypatch, tmp_path):
    attachment_root = tmp_path / ".codex-remote-attachments"
    attachment_root.mkdir()

    def attachment_dir(client_id: str, username: str, conversation_id: str) -> Path:
        target = attachment_root / client_id / username / conversation_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(console_attachments, "base_dir", lambda: attachment_root)
    monkeypatch.setattr(console_attachments, "conversation_dir", attachment_dir)
    return attachment_root


def _upload(payload: bytes, mime_type: str = "audio/webm") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename="voice.webm",
        headers=Headers({"content-type": mime_type}),
    )


def test_local_voice_transcription_is_editable_text_and_deletes_raw(monkeypatch, _isolated_local_voice_root):
    from backend.services import whatsapp_bridge

    captured: list[Path] = []
    cleanup_calls: list[tuple[Path, Path]] = []
    real_delete = whatsapp_audio_processing.delete_inbound_audio

    def transcribe(path: Path):
        captured.append(path)
        assert path.is_file()
        return {"success": True, "text": "  loja principal SKU ABC-123  "}

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
    monkeypatch.setattr(
        whatsapp_audio_processing,
        "delete_inbound_audio",
        lambda path, root: cleanup_calls.append((Path(path), Path(root))) or real_delete(path, root),
    )
    result = local_audio_transcription.transcribe_authenticated_upload(
        _upload(b"\x1a\x45\xdf\xa3" + b"safe-audio"),
        client_id="tenant-a",
        username="user-a",
    )

    assert result == {
        "success": True,
        "text": "loja principal SKU ABC-123",
        "error_code": "",
        "local_only": True,
        "raw_audio_retained": False,
    }
    assert captured and not captured[0].exists()
    assert cleanup_calls == [(captured[0], _isolated_local_voice_root)]


def test_local_voice_transcription_fails_closed_when_raw_cleanup_is_pending(monkeypatch):
    from backend.services import whatsapp_bridge

    captured: list[Path] = []
    queued: list[Path] = []

    def transcribe(path: Path):
        captured.append(path)
        return {"success": True, "text": "texto privado"}

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
    monkeypatch.setattr(whatsapp_audio_processing, "delete_inbound_audio", lambda _path, _root: False)
    monkeypatch.setattr(
        whatsapp_audio_processing,
        "queue_inbound_audio_cleanup",
        lambda path, _root: queued.append(Path(path)) or True,
    )

    result = local_audio_transcription.transcribe_authenticated_upload(
        _upload(b"\x1a\x45\xdf\xa3safe-audio"),
        client_id="tenant-a",
        username="user-a",
    )

    assert result == {
        "success": False,
        "text": "",
        "error_code": "audio_cleanup_pending",
        "local_only": True,
        "raw_audio_retained": True,
    }
    assert queued == captured
    captured[0].unlink(missing_ok=True)


def test_local_voice_transcription_rejects_container_spoof_without_engine(monkeypatch):
    from backend.services import whatsapp_bridge

    monkeypatch.setattr(
        whatsapp_bridge,
        "_transcribe_audio",
        lambda _path: pytest.fail("engine must not receive a spoofed container"),
    )
    result = local_audio_transcription.transcribe_authenticated_upload(
        _upload(b"not-a-webm-container"),
        client_id="tenant-a",
        username="user-a",
    )
    assert result["success"] is False
    assert result["error_code"] == "audio_corrupt"
    assert result["raw_audio_retained"] is False


def test_local_voice_transcription_enforces_bound_size(monkeypatch):
    monkeypatch.setattr(local_audio_transcription, "LOCAL_AUDIO_MAX_BYTES", 8)
    with pytest.raises(HTTPException) as error:
        local_audio_transcription.transcribe_authenticated_upload(
            _upload(b"\x1a\x45\xdf\xa3" + b"too-large"),
            client_id="tenant-a",
            username="user-a",
        )
    assert error.value.status_code == 413


def test_local_voice_transcription_requires_authenticated_tenant():
    with pytest.raises(HTTPException) as error:
        local_audio_transcription.transcribe_authenticated_upload(
            _upload(b"\x1a\x45\xdf\xa3safe"),
            client_id="",
            username="user-a",
        )
    assert error.value.status_code == 401


def test_codex_router_exposes_authenticated_audio_transcription_contract():
    router_source = Path("backend/routers/codex_console.py").read_text(encoding="utf-8")
    service_source = Path("backend/services/codex/console/api_admin.py").read_text(encoding="utf-8")
    assert '"/api/codex/audio/transcriptions"' in router_source
    assert "codex_console.codex_transcribe_audio" in router_source
    assert "_codex_require_authenticated(request, authorization)" in service_source
    assert "transcribe_authenticated_upload" in service_source


def test_whatsapp_codex_task_keeps_bounded_listing_bundle_for_voice_delivery(monkeypatch):
    updates: list[tuple[str, dict]] = []
    monkeypatch.setattr(console_agent_loop, "_codex_update_task", lambda task_id, **payload: updates.append((task_id, payload)))
    task = {"origin": "whatsapp"}
    result = {
        "tool_id": "mercado_livre_listing",
        "records": 1,
        "data": [{
            "loja": "Loja Principal",
            "id": "MLB123456789",
            "seller_sku": "ABC-123",
            "title": "Produto real",
            "permalink": "https://produto.mercadolivre.com.br/MLB123456789",
            "description": "Descricao oficial",
            "picture_urls": ["https://http2.mlstatic.com/photo.jpg"],
        }],
    }
    console_agent_loop._codex_capture_whatsapp_listing_bundle("task-1", task, result)
    assert task["whatsapp_listing_bundle"]["listings"][0]["item_id"] == "MLB123456789"
    assert task["whatsapp_listing_bundle"]["listings"][0]["sku"] == "ABC-123"
    assert updates[0][0] == "task-1"
    assert updates[0][1]["whatsapp_listing_bundle"]["picture_count"] == 1
