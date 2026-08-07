"""Context Hub runtime component."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.contracts import (
    ContextHubRuntimeConfig,
)

from backend.modules.context_hub.path_safety import (
    _snapshot_info_root,
)

from backend.modules.context_hub.state import CONTEXT_HUB_STATE


def _utc_now() -> str:
    with CONTEXT_HUB_STATE.config_guard:
        runtime = CONTEXT_HUB_STATE.runtime_config
    value = runtime.clock() if runtime is not None else datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _new_id() -> str:
    with CONTEXT_HUB_STATE.config_guard:
        runtime = CONTEXT_HUB_STATE.runtime_config
    return runtime.id_factory() if runtime is not None else uuid.uuid4().hex


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _json_canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_surface(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"installed", "packaged", "package", "development", "checkout", "test"}:
        return "development" if normalized == "checkout" else normalized
    return "development"


def configure_context_hub(
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> ContextHubRuntimeConfig:
    """Configure roots without creating files or starting background work."""

    default_base = Path(__file__).resolve().parents[3]
    base = Path(base_dir or default_base).expanduser().resolve()
    raw_info = Path(info_root or os.getenv("JK_INFO_DIR") or (base / "info")).expanduser()
    if not raw_info.is_absolute():
        raw_info = base / raw_info
    raw_info = raw_info.absolute()
    info_snapshot = _snapshot_info_root(raw_info)
    configured = ContextHubRuntimeConfig(
        base_dir=base,
        info_root=info_snapshot.resolved,
        surface=_normalize_surface(surface or os.getenv("JK_CONTEXT_HUB_SURFACE") or "development"),
    )
    with CONTEXT_HUB_STATE.config_guard:
        if CONTEXT_HUB_STATE.runtime_config == configured:
            return CONTEXT_HUB_STATE.runtime_config
        CONTEXT_HUB_STATE.runtime_config = configured
        return configured


def _runtime_config(
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> ContextHubRuntimeConfig:
    if base_dir is not None or info_root is not None or surface is not None:
        with CONTEXT_HUB_STATE.config_guard:
            current = CONTEXT_HUB_STATE.runtime_config
        current = current or configure_context_hub()
        base = Path(base_dir).resolve() if base_dir is not None else current.base_dir
        info = Path(info_root).expanduser().absolute() if info_root is not None else current.info_root
        return ContextHubRuntimeConfig(base, info, _normalize_surface(surface or current.surface))
    with CONTEXT_HUB_STATE.config_guard:
        current = CONTEXT_HUB_STATE.runtime_config
    return current or configure_context_hub()
