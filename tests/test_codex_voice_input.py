from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from backend.services import codex_console, local_audio_transcription


def _upload(payload: bytes, mime_type: str = "audio/webm") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename="voice.webm",
        headers=Headers({"content-type": mime_type}),
    )


def test_local_voice_transcription_is_editable_text_and_deletes_raw(monkeypatch):
    from backend.services import whatsapp_bridge

    captured: list[Path] = []

    def transcribe(path: Path):
        captured.append(path)
        assert path.is_file()
        return {"success": True, "text": "  loja principal SKU ABC-123  "}

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
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


def test_local_voice_transcription_fails_closed_when_raw_cleanup_is_pending(monkeypatch):
    from backend.services import whatsapp_bridge

    captured: list[Path] = []
    queued: list[Path] = []

    def transcribe(path: Path):
        captured.append(path)
        return {"success": True, "text": "texto privado"}

    monkeypatch.setattr(whatsapp_bridge, "_transcribe_audio", transcribe)
    monkeypatch.setattr(local_audio_transcription, "_unlink_audio_file", lambda _path, attempts=3: False)
    monkeypatch.setattr(local_audio_transcription, "_queue_cleanup", lambda path: queued.append(Path(path)))

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
    service_source = Path("backend/services/codex_console.py").read_text(encoding="utf-8")
    assert '"/api/codex/audio/transcriptions"' in router_source
    assert "codex_console.codex_transcribe_audio" in router_source
    assert "_codex_require_authenticated(request, authorization)" in service_source
    assert "transcribe_authenticated_upload" in service_source


def test_whatsapp_codex_task_keeps_bounded_listing_bundle_for_voice_delivery(monkeypatch):
    updates: list[tuple[str, dict]] = []
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda task_id, **payload: updates.append((task_id, payload)))
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
    codex_console._codex_capture_whatsapp_listing_bundle("task-1", task, result)
    assert task["whatsapp_listing_bundle"]["listings"][0]["item_id"] == "MLB123456789"
    assert task["whatsapp_listing_bundle"]["listings"][0]["sku"] == "ABC-123"
    assert updates[0][0] == "task-1"
    assert updates[0][1]["whatsapp_listing_bundle"]["picture_count"] == 1
