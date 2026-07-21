"""Safe helpers for terminal handling of inbound WhatsApp audio."""

from __future__ import annotations

import os
import queue
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional


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

AUDIO_FILE_EXTENSIONS = frozenset({".aac", ".amr", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"})
AUDIO_CLEANUP_MIN_AGE_SECONDS = 15 * 60
AUDIO_CLEANUP_QUEUE_MAX = 128
_IMMEDIATE_CLEANUP_DELAYS_SECONDS = (0.0, 0.05, 0.2, 0.5)
_QUEUED_CLEANUP_DELAYS_SECONDS = (0.25, 1.0, 5.0)
_TRANSCRIPTION_RETRY_CODES = frozenset({"resource_busy", "queue_timeout", "child_failed"})


@dataclass(frozen=True)
class _CleanupRequest:
    audio_path: Path
    attachment_root: Path
    telemetry_context: tuple[tuple[str, str], ...] = ()


_CLEANUP_QUEUE: "queue.Queue[_CleanupRequest]" = queue.Queue(maxsize=AUDIO_CLEANUP_QUEUE_MAX)
_CLEANUP_THREAD_LOCK = threading.Lock()
_CLEANUP_THREAD: Optional[threading.Thread] = None
_TELEMETRY_HOOK: Optional[Callable[..., None]] = None
_AUDIO_TELEMETRY_CONTEXT: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "jk_audio_telemetry_context",
    default=(),
)


def _bounded_context_value(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


@contextmanager
def audio_telemetry_scope(
    *,
    client_id: Any,
    trace_id: Any,
    user_id: Any = "",
    surface: str = "whatsapp",
) -> Iterator[None]:
    """Keep tenant identifiers in-memory only while one audio is processed."""

    context = tuple(
        (name, value)
        for name, value in (
            ("client_id", _bounded_context_value(client_id, 80)),
            ("trace_id", _bounded_context_value(trace_id)),
            ("user_id", _bounded_context_value(user_id, 80)),
            ("surface", _bounded_context_value(surface, 40)),
        )
        if value
    )
    token = _AUDIO_TELEMETRY_CONTEXT.set(context)
    try:
        yield
    finally:
        _AUDIO_TELEMETRY_CONTEXT.reset(token)


def current_audio_telemetry_context() -> dict[str, str]:
    return dict(_AUDIO_TELEMETRY_CONTEXT.get())


def configure_audio_telemetry(hook: Optional[Callable[..., None]]) -> None:
    """Bind the in-memory sanitized telemetry sink without creating a cycle."""

    global _TELEMETRY_HOOK
    _TELEMETRY_HOOK = hook if callable(hook) else None


def _emit_audio_event(
    stage: str,
    *,
    audio_path: Optional[Path] = None,
    success: Optional[bool] = None,
    error_code: str = "",
    attempt: int = 0,
    retries: int = 0,
    cleanup_state: str = "",
    telemetry_context: Optional[Mapping[str, str]] = None,
) -> None:
    hook = _TELEMETRY_HOOK
    if hook is None:
        return
    try:
        hook(
            stage,
            audio_path=audio_path,
            success=success,
            error_code=error_code,
            attempt=max(0, int(attempt or 0)),
            retries=max(0, int(retries or 0)),
            cleanup_state=str(cleanup_state or "")[:40],
            telemetry_context=dict(telemetry_context or current_audio_telemetry_context()),
        )
    except Exception:
        # Telemetry must never change processing or retention behavior.
        pass


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(str(path)))
    except OSError:
        return True


def _lexical_path_inside_root(path: Path, root: Path) -> tuple[Path, Path]:
    lexical_root = Path(os.path.abspath(str(root)))
    lexical_path = Path(os.path.abspath(str(path)))
    try:
        lexical_path.relative_to(lexical_root)
    except ValueError as exc:
        raise ValueError("audio_path_invalid") from exc
    return lexical_path, lexical_root


def _has_link_or_junction_boundary(path: Path, root: Path) -> bool:
    current = path
    while True:
        try:
            if current.is_symlink() or _is_junction(current):
                return True
        except OSError:
            return True
        if current == root:
            return False
        parent = current.parent
        if parent == current:
            return True
        current = parent


def validate_regular_attachment_file(file_path: Path, attachment_root: Path) -> Path:
    """Return a regular file constrained to the root, without following reparse points."""

    lexical_path, lexical_root = _lexical_path_inside_root(Path(file_path), Path(attachment_root))
    if _has_link_or_junction_boundary(lexical_path, lexical_root):
        raise ValueError("audio_path_invalid")
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved_path = lexical_path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
        path_stat = resolved_path.lstat()
    except (OSError, ValueError) as exc:
        raise ValueError("audio_path_invalid") from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("audio_path_invalid")
    return resolved_path


def validate_inbound_audio_path(audio_path: Path, attachment_root: Path) -> Path:
    """Return a real regular audio path constrained to a non-reparse root."""

    resolved_path = validate_regular_attachment_file(audio_path, attachment_root)
    if resolved_path.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
        raise ValueError("audio_path_invalid")
    return resolved_path


def validate_audio_attachment_directory(directory: Path, attachment_root: Path) -> Path:
    """Validate a storage directory before any inbound audio bytes are written."""

    lexical_dir, lexical_root = _lexical_path_inside_root(Path(directory), Path(attachment_root))
    if _has_link_or_junction_boundary(lexical_dir, lexical_root):
        raise ValueError("audio_path_invalid")
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved_dir = lexical_dir.resolve(strict=True)
        resolved_dir.relative_to(resolved_root)
        directory_stat = resolved_dir.lstat()
    except (OSError, ValueError) as exc:
        raise ValueError("audio_path_invalid") from exc
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ValueError("audio_path_invalid")
    return resolved_dir


def inbound_audio_path(media: dict[str, Any], base_dir: Path) -> Path:
    """Strict compatibility adapter for legacy private callers."""

    raw_value = str(media.get("path") or "").strip()
    if not raw_value:
        raise ValueError("audio_path_missing")
    raw = Path(raw_value)
    candidate = raw if raw.is_absolute() else (Path(base_dir) / raw)
    return validate_inbound_audio_path(candidate, Path(base_dir) / ".codex-remote-attachments")


def _validated_missing_path(audio_path: Path, attachment_root: Path) -> bool:
    """Accept an absent target only when its lexical parents remain safe."""

    try:
        lexical_path, lexical_root = _lexical_path_inside_root(Path(audio_path), Path(attachment_root))
        if _has_link_or_junction_boundary(lexical_path.parent, lexical_root):
            return False
        return not lexical_path.exists() and not lexical_path.is_symlink() and not _is_junction(lexical_path)
    except (OSError, ValueError):
        return False


def _delete_inbound_audio_once(audio_path: Path, attachment_root: Path) -> bool:
    try:
        if _validated_missing_path(audio_path, attachment_root):
            return True
        resolved = validate_inbound_audio_path(audio_path, attachment_root)
        resolved.unlink()
        return _validated_missing_path(resolved, attachment_root)
    except (OSError, ValueError):
        return False


def delete_inbound_audio(audio_path: Path, attachment_root: Path) -> bool:
    """Delete a validated regular audio file with bounded immediate retries."""

    for delay_seconds in _IMMEDIATE_CLEANUP_DELAYS_SECONDS:
        if delay_seconds:
            threading.Event().wait(delay_seconds)
        if _delete_inbound_audio_once(Path(audio_path), Path(attachment_root)):
            _emit_audio_event(
                "cleanup_succeeded",
                audio_path=Path(audio_path),
                success=True,
                cleanup_state="removed",
            )
            return True
    _emit_audio_event(
        "cleanup_failed",
        audio_path=Path(audio_path),
        success=False,
        error_code="audio_cleanup_failed",
        cleanup_state="immediate_failed",
    )
    return False


def _cleanup_queue_worker() -> None:
    while True:
        request = _CLEANUP_QUEUE.get()
        removed = False
        try:
            for delay_seconds in _QUEUED_CLEANUP_DELAYS_SECONDS:
                threading.Event().wait(delay_seconds)
                if _delete_inbound_audio_once(request.audio_path, request.attachment_root):
                    removed = True
                    _emit_audio_event(
                        "janitor_removed",
                        audio_path=request.audio_path,
                        success=True,
                        cleanup_state="removed",
                        telemetry_context=dict(request.telemetry_context),
                    )
                    break
            if not removed:
                _emit_audio_event(
                    "janitor_failed",
                    audio_path=request.audio_path,
                    success=False,
                    error_code="audio_cleanup_failed",
                    cleanup_state="retained",
                    telemetry_context=dict(request.telemetry_context),
                )
        finally:
            _CLEANUP_QUEUE.task_done()


def start_audio_cleanup_worker() -> None:
    global _CLEANUP_THREAD
    with _CLEANUP_THREAD_LOCK:
        if _CLEANUP_THREAD is not None and _CLEANUP_THREAD.is_alive():
            return
        _CLEANUP_THREAD = threading.Thread(
            target=_cleanup_queue_worker,
            name="jk-audio-cleanup",
            daemon=True,
        )
        _CLEANUP_THREAD.start()


def queue_inbound_audio_cleanup(audio_path: Path, attachment_root: Path) -> bool:
    try:
        validated = validate_inbound_audio_path(audio_path, attachment_root)
    except ValueError:
        return _validated_missing_path(Path(audio_path), Path(attachment_root))
    request = _CleanupRequest(
        validated,
        Path(attachment_root),
        tuple(current_audio_telemetry_context().items()),
    )
    try:
        _CLEANUP_QUEUE.put_nowait(request)
    except queue.Full:
        _emit_audio_event(
            "cleanup_queue_full",
            audio_path=validated,
            success=False,
            error_code="audio_cleanup_failed",
            cleanup_state="queue_full",
        )
        return False
    start_audio_cleanup_worker()
    _emit_audio_event(
        "cleanup_queued",
        audio_path=validated,
        success=None,
        cleanup_state="queued",
    )
    return True


def transcription_failure(error_code: Any) -> dict[str, Any]:
    code = str(error_code or "").strip().lower().replace("-", "_")
    if code not in _SAFE_FAILURE_CODES:
        code = "child_failed"
    return {"success": False, "error_code": code, "error": code, "local_only": True}


def transcribe_audio_with_retry(
    transcriber: Callable[[Path], dict[str, Any]],
    audio_path: Path,
    *,
    total_attempts: int = 2,
) -> dict[str, Any]:
    """Run no more than two attempts and retry only transient safe failures."""

    attempts = max(1, min(int(total_attempts or 1), 2))
    result: dict[str, Any] = transcription_failure("child_failed")
    for attempt in range(attempts):
        _emit_audio_event(
            "transcription_attempt",
            audio_path=Path(audio_path),
            success=None,
            attempt=attempt + 1,
            retries=attempt,
        )
        try:
            candidate = transcriber(Path(audio_path))
            result = candidate if isinstance(candidate, dict) else transcription_failure("child_failed")
        except Exception:
            result = transcription_failure("child_failed")
        if result.get("success") is True:
            return result
        error_code = str(result.get("error_code") or "child_failed").strip().lower().replace("-", "_")
        if error_code not in _TRANSCRIPTION_RETRY_CODES or attempt + 1 >= attempts:
            return transcription_failure(error_code)
        _emit_audio_event(
            "transcription_retry",
            audio_path=Path(audio_path),
            success=None,
            error_code=error_code,
            attempt=attempt + 1,
            retries=attempt + 1,
        )
        threading.Event().wait(0.25)
    return result


def _iter_stale_audio_candidates(base_dir: Path) -> list[tuple[Path, Path]]:
    attachment_root = Path(base_dir) / ".codex-remote-attachments"
    candidates: list[tuple[Path, Path]] = []
    if attachment_root.exists() and not attachment_root.is_symlink() and not _is_junction(attachment_root):
        for walk_root, directories, filenames in os.walk(attachment_root, topdown=True, followlinks=False):
            current = Path(walk_root)
            directories[:] = [
                name
                for name in directories
                if not (current / name).is_symlink() and not _is_junction(current / name)
            ]
            for name in filenames:
                candidate = current / name
                if candidate.suffix.lower() in AUDIO_FILE_EXTENSIONS:
                    candidates.append((candidate, attachment_root))

    return candidates


def cleanup_stale_audio_files(
    base_dir: Path,
    *,
    older_than_seconds: int = AUDIO_CLEANUP_MIN_AGE_SECONDS,
    now_epoch: Optional[float] = None,
) -> dict[str, int]:
    """Remove only owned regular audio files older than the retention grace."""

    cutoff = float(time.time() if now_epoch is None else now_epoch) - max(900, int(older_than_seconds or 0))
    scanned = 0
    removed = 0
    queued = 0
    failed = 0
    for candidate, root in _iter_stale_audio_candidates(Path(base_dir)):
        scanned += 1
        try:
            validated = validate_inbound_audio_path(candidate, root)
            if validated.stat().st_mtime > cutoff:
                continue
        except (OSError, ValueError):
            continue
        try:
            relative = validated.relative_to(root.resolve(strict=True))
            if len(relative.parts) < 3:
                # The application-owned layout is at least client/user/file.
                # A loose file at the root has no provable tenant ownership.
                continue
            client_id = relative.parts[0]
        except (OSError, ValueError):
            failed += 1
            continue
        with audio_telemetry_scope(
            client_id=client_id,
            trace_id=uuid.uuid4().hex,
            surface="audio_janitor",
        ):
            if delete_inbound_audio(validated, root):
                removed += 1
            elif queue_inbound_audio_cleanup(validated, root):
                queued += 1
            else:
                failed += 1
    return {"scanned": scanned, "removed": removed, "queued": queued, "failed": failed}


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
