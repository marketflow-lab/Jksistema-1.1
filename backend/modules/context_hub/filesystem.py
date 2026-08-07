"""Context Hub filesystem component."""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Optional,
)



from backend.modules.context_hub.contracts import (
    OBSIDIAN_GRAPH_DEFAULTS,
)

from backend.modules.context_hub.runtime import _new_id


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{_new_id()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        _replace_with_retry(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _replace_with_retry(source: Path | str, target: Path | str, *, attempts: int = 12) -> None:
    """Preserve atomic replacement while tolerating short Windows file locks."""

    maximum_attempts = max(1, int(attempts))
    for attempt in range(maximum_attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= maximum_attempts:
                raise
            time.sleep(min(0.25, 0.02 * (2 ** min(attempt, 4))))


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_obsidian_graph_json_if_absent(
    path: Path,
    payload: Mapping[str, Any],
) -> bool:
    """Atomically publish graph.json without ever replacing an existing file."""

    temporary: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{_new_id()}.tmp")
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
        temporary.write_text(rendered, encoding="utf-8", newline="\n")
        # A hard-link publish is atomic and fails when the destination appears
        # concurrently. Unlike os.replace, it can never overwrite preferences
        # written by Obsidian after bootstrap observed the path as absent.
        os.link(temporary, path)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def _ensure_obsidian_graph_groups(path: Path) -> bool:
    """Create graph defaults only when no user-owned graph.json exists."""

    if os.path.lexists(path):
        return True
    return _write_obsidian_graph_json_if_absent(path, OBSIDIAN_GRAPH_DEFAULTS)
