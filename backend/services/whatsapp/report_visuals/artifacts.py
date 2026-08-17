"""Private PNG generation, validation, retention, and cleanup."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .builders import build_chart_data
from .common import (
    REPORT_CHART_DIRNAME,
    REPORT_CHART_MAX_IMAGES,
    REPORT_CHART_TTL_SECONDS,
    _safe_label,
)
from .intent import should_generate_report_charts

def _safe_client_id(value: Any) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "default").strip())[:80]
    return safe.strip("._-") or "default"

def chart_output_dir(base_info_dir: str | Path, client_id: Any) -> Path:
    root = Path(base_info_dir).resolve()
    output = (root / _safe_client_id(client_id) / REPORT_CHART_DIRNAME).resolve()
    output.relative_to(root)
    output.mkdir(parents=True, exist_ok=True)
    return output

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _expires_epoch(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if text:
        try:
            return int(float(text))
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp())
            except ValueError:
                pass
    return int(time.time() + REPORT_CHART_TTL_SECONDS)

def _validated_artifacts(raw: Any, output_dir: Path, max_images: int) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            path = Path(str(item.get("path") or "")).resolve()
            path.relative_to(output_dir.resolve())
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            size = path.stat().st_size
            if size <= 0 or size > 5 * 1024 * 1024:
                path.unlink(missing_ok=True)
                continue
            digest = _sha256(path)
            expected = str(item.get("sha256") or "").strip().lower()
            if expected and expected != digest:
                path.unlink(missing_ok=True)
                continue
            artifact = {
                **item,
                "artifact_type": "report_chart",
                "path": str(path),
                "mime_type": "image/png",
                "sha256": digest,
                "byte_size": size,
                "expires_at": _expires_epoch(item.get("expires_at")),
            }
            artifacts.append(artifact)
        except (OSError, ValueError, TypeError):
            continue
        if len(artifacts) >= max(1, min(int(max_images or 1), REPORT_CHART_MAX_IMAGES)):
            break
    return artifacts

def generate_task_chart_artifacts(
    *,
    base_info_dir: str | Path,
    client_id: Any,
    task_id: Any,
    prompt: Any,
    tool_results: Iterable[dict[str, Any]],
    query_policy: Optional[dict[str, Any]] = None,
    max_images: int = REPORT_CHART_MAX_IMAGES,
) -> dict[str, Any]:
    if not should_generate_report_charts(prompt):
        return {"expected": False, "status": "not_analytical", "artifacts": []}
    output_dir = chart_output_dir(base_info_dir, client_id)
    chart_data = build_chart_data(prompt, tool_results, query_policy)
    chart_data["task_id"] = _safe_label(task_id)
    try:
        from backend.services import report_charts

        raw = report_charts.generate_report_charts(chart_data, output_dir, max_images=max_images)
        artifacts = _validated_artifacts(raw, output_dir, max_images)
        return {
            "expected": True,
            "status": "generated" if artifacts else "generation_empty",
            "artifacts": artifacts,
            "analysis_type": chart_data.get("analysis_type") or "generic",
            "coverage_complete": chart_data.get("coverage_complete") is True,
        }
    except Exception as exc:
        return {
            "expected": True,
            "status": "generation_failed",
            "artifacts": [],
            "error": str(exc)[:500],
            "analysis_type": chart_data.get("analysis_type") or "generic",
        }

def cleanup_stale_chart_files(base_info_dir: str | Path, max_age_seconds: int = REPORT_CHART_TTL_SECONDS) -> int:
    root = Path(base_info_dir).resolve()
    cutoff = time.time() - max(60, int(max_age_seconds or REPORT_CHART_TTL_SECONDS))
    removed = 0
    for path in root.glob(f"*/{REPORT_CHART_DIRNAME}/*.png"):
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            if resolved.is_file() and resolved.stat().st_mtime < cutoff:
                resolved.unlink(missing_ok=True)
                removed += 1
        except (OSError, ValueError):
            continue
    return removed
