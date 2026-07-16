"""Isolated local Whisper runner used by the WhatsApp bridge.

This module intentionally runs in a child process so the bridge can enforce a
hard timeout without sending audio to any remote transcription service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any


MODEL_NAME = "small"
MODEL_EXPECTED_BYTES = 488_000_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*") if path.exists() else []:
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _write_manifest(model_dir: Path) -> dict[str, Any]:
    files = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file() or path.name == "model-manifest.json":
            continue
        try:
            files.append(
                {
                    "path": path.relative_to(model_dir).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        except OSError:
            continue
    manifest = {
        "model": MODEL_NAME,
        "engine": "faster-whisper==1.2.1",
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_bytes": sum(int(item["size"]) for item in files),
        "files": files,
    }
    target = model_dir / "model-manifest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return manifest


def _verify_manifest(model_dir: Path) -> None:
    manifest_path = model_dir / "model-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("whisper_manifest_missing_or_invalid") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        raise RuntimeError("whisper_manifest_empty")
    root = model_dir.resolve()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("whisper_manifest_entry_invalid")
        path = (root / str(item.get("path") or "")).resolve()
        try:
            if os.path.commonpath([str(root), str(path)]) != str(root):
                raise RuntimeError("whisper_manifest_path_escape")
        except ValueError as exc:
            raise RuntimeError("whisper_manifest_path_escape") from exc
        if not path.is_file() or path.stat().st_size != int(item.get("size") or -1):
            raise RuntimeError(f"whisper_model_size_mismatch:{path.name}")
        if _sha256(path) != str(item.get("sha256") or ""):
            raise RuntimeError(f"whisper_model_hash_mismatch:{path.name}")


def _load_model(model_dir: Path):
    from faster_whisper import WhisperModel

    model_dir.mkdir(parents=True, exist_ok=True)
    return WhisperModel(
        MODEL_NAME,
        device="cpu",
        compute_type="int8",
        download_root=str(model_dir),
    )


def _audio_duration(path: Path) -> float:
    import av

    with av.open(str(path)) as container:
        if container.duration is not None:
            return max(0.0, float(container.duration) / float(av.time_base))
        durations = []
        for stream in container.streams.audio:
            if stream.duration is not None and stream.time_base is not None:
                durations.append(float(stream.duration * stream.time_base))
        return max(durations or [0.0])


def _transcribe(model_dir: Path, audio_path: Path) -> dict[str, Any]:
    duration = _audio_duration(audio_path)
    if duration > 600.0:
        raise RuntimeError("audio_exceeds_ten_minutes")
    _verify_manifest(model_dir)
    model = _load_model(model_dir)
    def run(language: str | None):
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
        )
        texts: list[str] = []
        log_probs: list[float] = []
        for segment in segments:
            text = str(getattr(segment, "text", "") or "").strip()
            if text:
                texts.append(text)
            value = getattr(segment, "avg_logprob", None)
            if isinstance(value, (int, float)):
                log_probs.append(float(value))
        return texts, log_probs, info

    texts, log_probs, info = run("pt")
    confidence_preview = math.exp(sum(log_probs) / len(log_probs)) if log_probs else 0.0
    used_detection_fallback = False
    if not texts or confidence_preview < 0.20:
        fallback_texts, fallback_log_probs, fallback_info = run(None)
        fallback_confidence = math.exp(sum(fallback_log_probs) / len(fallback_log_probs)) if fallback_log_probs else 0.0
        if fallback_texts and (not texts or fallback_confidence > confidence_preview):
            texts, log_probs, info = fallback_texts, fallback_log_probs, fallback_info
            used_detection_fallback = True
    confidence = None
    if log_probs:
        confidence = round(max(0.0, min(1.0, math.exp(sum(log_probs) / len(log_probs)))), 4)
    detected_language = str(getattr(info, "language", "") or "pt")
    probability = getattr(info, "language_probability", None)
    _write_manifest(model_dir)
    return {
        "success": True,
        "text": " ".join(texts).strip(),
        "duration_seconds": round(duration or float(getattr(info, "duration", 0.0) or 0.0), 3),
        "confidence": confidence,
        "language": detected_language,
        "language_probability": round(float(probability), 4) if isinstance(probability, (int, float)) else None,
        "used_detection_fallback": used_detection_fallback,
        "local_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--audio")
    parser.add_argument("--output", required=True)
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    output_path = Path(args.output).resolve()
    model_dir = Path(args.model_dir).resolve()
    try:
        if args.download_only:
            _load_model(model_dir)
            result = {"success": True, "download_only": True, **_write_manifest(model_dir)}
        else:
            if not args.audio:
                raise RuntimeError("audio_required")
            result = _transcribe(model_dir, Path(args.audio).resolve())
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc)[:1000],
            "downloaded_bytes": _directory_size(model_dir),
            "expected_bytes": MODEL_EXPECTED_BYTES,
        }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
