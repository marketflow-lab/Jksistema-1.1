"""Safe local-audio adapter shared by authenticated Black Jhon inputs.

The speech engine remains the local WhatsApp Whisper runtime.  This adapter
only accepts a bounded multipart upload, validates its container signature,
creates a short-lived file and returns a compact, non-sensitive contract.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from backend.services.whatsapp import audio_processing as whatsapp_audio_processing


LOCAL_AUDIO_MAX_BYTES = 16 * 1024 * 1024
LOCAL_AUDIO_ALLOWED_MIMES: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}
LOCAL_AUDIO_SAFE_ERRORS = frozenset(
    {
        "audio_corrupt",
        "audio_cleanup_pending",
        "audio_empty",
        "audio_size_limit",
        "child_failed",
        "dependency_missing",
        "duration_limit",
        "low_confidence",
        "model_invalid",
        "no_speech",
        "preflight_failed",
        "queue_timeout",
        "resource_busy",
        "runner_invalid",
        "transcription_timeout",
    }
)

def _unlink_audio_file(path: Path, attempts: int = 3, *, attachment_root: Path | None = None) -> bool:
    # ``attempts`` remains in the private signature for compatibility. The
    # centralized implementation owns the fixed, reviewed retry schedule.
    del attempts
    return whatsapp_audio_processing.delete_inbound_audio(path, attachment_root or path.parent)


def _queue_cleanup(path: Path, *, attachment_root: Path | None = None) -> None:
    whatsapp_audio_processing.queue_inbound_audio_cleanup(path, attachment_root or path.parent)


def _normalized_mime(value: Any) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _safe_error_code(value: Any) -> str:
    code = str(value or "").strip().lower().replace("-", "_")
    return code if code in LOCAL_AUDIO_SAFE_ERRORS else "child_failed"


def _signature_matches(mime_type: str, header: bytes) -> bool:
    value = bytes(header or b"")
    if mime_type == "audio/webm":
        return value.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "audio/ogg":
        return value.startswith(b"OggS")
    if mime_type in {"audio/wav", "audio/x-wav"}:
        return len(value) >= 12 and value.startswith(b"RIFF") and value[8:12] == b"WAVE"
    if mime_type == "audio/mp4":
        return len(value) >= 12 and value[4:8] == b"ftyp"
    if mime_type == "audio/mpeg":
        return value.startswith(b"ID3") or (len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0)
    if mime_type == "audio/aac":
        return len(value) >= 2 and value[0] == 0xFF and value[1] & 0xF6 == 0xF0
    if mime_type == "audio/amr":
        return value.startswith(b"#!AMR")
    return False


def _failure(error_code: Any, *, raw_audio_retained: bool = False) -> dict[str, Any]:
    return {
        "success": False,
        "text": "",
        "error_code": _safe_error_code(error_code),
        "local_only": True,
        "raw_audio_retained": bool(raw_audio_retained),
    }


def transcribe_authenticated_upload(
    upload: UploadFile,
    *,
    client_id: str,
    username: str,
) -> dict[str, Any]:
    """Transcribe one authenticated upload without retaining raw audio.

    ``client_id`` and ``username`` are intentionally accepted to make tenant
    binding explicit at the call site.  They are never written to the audio
    filename or included in the transcription result.
    """

    if not str(client_id or "").strip() or not str(username or "").strip():
        raise HTTPException(status_code=401, detail="Sessao invalida para transcricao de audio.")
    mime_type = _normalized_mime(upload.content_type)
    suffix = LOCAL_AUDIO_ALLOWED_MIMES.get(mime_type)
    if not suffix:
        raise HTTPException(status_code=415, detail="Formato de audio nao suportado.")

    # Keep local voice under the same private attachment root as WhatsApp.
    # The global operating-system temp directory must never be scanned/deleted.
    from backend.services import codex_console

    attachment_root = codex_console._codex_attachments_base_dir()
    target_dir = codex_console._codex_attachment_dir(client_id, username, "local-voice")
    target_dir = whatsapp_audio_processing.validate_audio_attachment_directory(
        target_dir,
        attachment_root,
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="jk-codex-voice-",
        suffix=suffix,
        dir=str(target_dir),
    )
    os.close(descriptor)
    temporary = whatsapp_audio_processing.validate_inbound_audio_path(
        Path(temporary_name),
        attachment_root,
    )
    size = 0
    header = b""
    result_payload: dict[str, Any] | None = None
    pending_error: BaseException | None = None
    with whatsapp_audio_processing.audio_telemetry_scope(
        client_id=client_id,
        trace_id=uuid.uuid4().hex,
        user_id=username,
        surface="local_voice",
    ):
        try:
            with temporary.open("wb") as target:
                while True:
                    chunk = upload.file.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > LOCAL_AUDIO_MAX_BYTES:
                        raise HTTPException(status_code=413, detail="Audio acima do limite de 16 MB.")
                    if len(header) < 32:
                        header += chunk[: 32 - len(header)]
                    target.write(chunk)
            if size <= 0:
                result_payload = _failure("audio_empty")
            elif not _signature_matches(mime_type, header):
                result_payload = _failure("audio_corrupt")

            if result_payload is None:
                # Import lazily so the WhatsApp component is already composed with
                # the application runtime. No audio bytes or transcript are logged.
                from backend.services import whatsapp_bridge

                validated = whatsapp_audio_processing.validate_inbound_audio_path(
                    temporary,
                    attachment_root,
                )
                result = whatsapp_audio_processing.transcribe_audio_with_retry(
                    whatsapp_bridge._transcribe_audio,
                    validated,
                    total_attempts=2,
                )
                if not isinstance(result, dict) or result.get("success") is not True:
                    result_payload = _failure((result or {}).get("error_code") if isinstance(result, dict) else "child_failed")
                else:
                    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(result.get("text") or ""))
                    text = re.sub(r"[ \t]+", " ", text).strip()[:12000]
                    result_payload = _failure("no_speech") if not text else {
                        "success": True,
                        "text": text,
                        "error_code": "",
                        "local_only": True,
                        "raw_audio_retained": False,
                    }
        except BaseException as exc:
            pending_error = exc
        finally:
            removed = _unlink_audio_file(temporary, attachment_root=attachment_root)
            if not removed:
                _queue_cleanup(temporary, attachment_root=attachment_root)
    if not removed:
        return _failure("audio_cleanup_pending", raw_audio_retained=True)
    if pending_error is not None:
        raise pending_error
    return result_payload or _failure("child_failed")


__all__ = [
    "LOCAL_AUDIO_ALLOWED_MIMES",
    "LOCAL_AUDIO_MAX_BYTES",
    "transcribe_authenticated_upload",
]
