"""Safe helpers for terminal handling of inbound WhatsApp audio."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


_SAFE_FAILURE_CODES = {
    "audio_cleanup_failed",
    "audio_corrupt",
    "audio_empty",
    "audio_missing",
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


def inbound_audio_path(media: dict[str, Any], base_dir: Path) -> Path:
    raw = Path(str(media.get("path") or ""))
    return raw if raw.is_absolute() else (base_dir / raw)


def delete_inbound_audio(audio_path: Path, attachment_root: Path) -> bool:
    """Delete only a regular file located inside the attachment root."""

    try:
        if not audio_path.exists() and not audio_path.is_symlink():
            return True
        root = attachment_root.resolve()
        lexical_path = Path(os.path.abspath(str(audio_path)))
        lexical_path.relative_to(root)
        if lexical_path.is_symlink():
            lexical_path.unlink(missing_ok=True)
            return not lexical_path.exists() and not lexical_path.is_symlink()
        resolved = lexical_path.resolve()
        resolved.relative_to(root)
        if not resolved.is_file():
            return False
        resolved.unlink(missing_ok=True)
        return not resolved.exists()
    except (OSError, ValueError):
        return False


def transcription_failure(error_code: Any) -> dict[str, Any]:
    code = str(error_code or "").strip().lower().replace("-", "_")
    if code not in _SAFE_FAILURE_CODES:
        code = "child_failed"
    return {"success": False, "error_code": code, "error": code, "local_only": True}


def transcription_reply(error_code: Any) -> str:
    code = str(error_code or "").strip().lower()
    if code == "no_speech":
        return "Nao identifiquei uma fala nesse audio. Envie novamente falando um pouco mais perto do microfone."
    if code == "low_confidence":
        return "Nao consegui entender o audio com seguranca. Envie novamente com menos ruido ou escreva a mensagem."
    if code in {"resource_busy", "queue_timeout"}:
        return "O transcritor local esta ocupado no momento. Aguarde um pouco e envie o audio novamente."
    if code in {"audio_size_limit", "duration_limit", "transcription_timeout"}:
        return "Esse audio excede o limite local de processamento. Envie um audio de ate 10 minutos e 16 MB."
    if code in {"dependency_missing", "model_invalid", "runner_invalid", "preflight_failed"}:
        return "O transcritor local do Black Jhon nao esta pronto. Verifique o preflight de audios no aplicativo."
    if code == "audio_cleanup_failed":
        return "Nao consegui concluir o processamento seguro desse audio. Envie a mensagem por texto."
    return "Nao consegui transcrever esse audio localmente. Envie novamente ou escreva a mensagem."
