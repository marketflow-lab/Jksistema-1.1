"""Public auth defaults for Python/BAT startup; no credentials or cloud access."""
from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Mapping

from backend.services.remote_auth_contracts import load_remote_auth_configuration


MAX_CONFIG_BYTES = 64 * 1024
_FIELDS = {
    "url": "JK_REMOTE_AUTH_URL",
    "projectId": "JK_REMOTE_AUTH_PROJECT_ID",
    "mode": "JK_REMOTE_AUTH_MODE",
}


def _regular_path(path: Path, *, directory: bool = False) -> bool:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))):
        raise ValueError("invalid_public_remote_auth_configuration")
    return True


def public_remote_auth_defaults(base_dir: str | Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Return only missing public variables, anchored to the known code root.

    An explicitly supplied URL, including an empty URL to disable remote auth,
    preserves the existing environment-only behavior. This function never writes
    the environment or any file. An invalid present configuration fails closed.
    """
    if "JK_REMOTE_AUTH_URL" in environ:
        return {}
    try:
        base = Path(base_dir)
        if not _regular_path(base, directory=True):
            return {}
        candidate = None
        electron = base / "electron_app"
        if _regular_path(electron, directory=True):
            first = electron / "client-config.json"
            if _regular_path(first):
                candidate = first
        if candidate is None:
            fallback = base / "client-config.json"
            if not _regular_path(fallback):
                return {}
            candidate = fallback
        with candidate.open("rb") as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError("invalid_public_remote_auth_configuration")
        payload = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_public_remote_auth_configuration")
        if "remoteAuth" not in payload:
            return {}
        remote = payload["remoteAuth"]
        if (not isinstance(remote, dict)
                or any(not isinstance(remote.get(key), str) for key in ("url", "projectId"))
                or ("mode" in remote and not isinstance(remote["mode"], str))):
            raise ValueError("invalid_public_remote_auth_configuration")
        defaults = {env_key: str(remote.get(field, "prefer")).strip()
                    for field, env_key in _FIELDS.items()}
        updates = {key: value for key, value in defaults.items() if key not in environ}
        effective = {**environ, **updates}
        if load_remote_auth_configuration(effective) is None:
            raise ValueError("invalid_public_remote_auth_configuration")
        return updates
    except (OSError, UnicodeError, ValueError, RecursionError):
        # Never include file contents, selected URLs, or private paths in errors.
        raise ValueError("invalid_public_remote_auth_configuration") from None


__all__ = ["public_remote_auth_defaults"]
