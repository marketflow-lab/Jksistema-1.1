"""Read-only reservations for labels belonging to locally removed stores.

Reservations only constrain legacy name matching. An explicit store_id remains
authoritative. Callers capture these bytes with their store snapshot and read
them again under the store lock before committing a name-based operation.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


TOMBSTONES_FILE = "lojas_sync_tombstones.json"


@dataclass(frozen=True)
class RetiredStoreNames:
    names: tuple[str, ...] = ()
    invalid: bool = False

    def blocks(self, name: object, normalize: Callable[[object], str] | None = None) -> bool:
        if self.invalid:
            return True
        normalize = normalize or (lambda value: str(value or "").strip().casefold())
        key = normalize(name)
        return bool(key) and any(normalize(value) == key for value in self.names)


def parse_retired_store_names(raw: bytes) -> RetiredStoreNames:
    """Missing legacy fields reserve nothing; malformed present fields fail closed."""
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError
        names: set[str] = set()
        seen: set[tuple[str, str, str]] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError
            if "store_names" not in item or item.get("type") == "integration":
                continue
            kind = item.get("type")
            store_id = item.get("store_id")
            if kind not in {"store", "integration"} or not isinstance(store_id, str) or not store_id.strip():
                raise ValueError
            key = (kind, store_id, str(item.get("service") or ""))
            if key in seen:
                raise ValueError
            seen.add(key)
            deleted, restored = item.get("deleted_at"), item.get("restored_at")
            if any(value is not None and not isinstance(value, str) for value in (deleted, restored)):
                raise ValueError
            if bool(deleted) == bool(restored):
                raise ValueError
            instant = datetime.fromisoformat((deleted or restored).replace("Z", "+00:00"))
            if instant.utcoffset() is None or instant.utcoffset().total_seconds() != 0:
                raise ValueError
            if restored:
                continue
            aliases = item["store_names"]
            if not isinstance(aliases, list) or not aliases:
                raise ValueError
            for name in aliases:
                if not isinstance(name, str) or not name.strip() or any(ord(char) < 32 for char in name):
                    raise ValueError
                names.add(name.strip())
        return RetiredStoreNames(tuple(sorted(names)))
    except (ValueError, TypeError, UnicodeError):
        return RetiredStoreNames(invalid=True)


def capture_retired_store_names(tenant: os.PathLike[str] | str) -> bytes:
    """Capture only this tenant's file, without following linked entries."""
    path = Path(tenant) / TOMBSTONES_FILE
    try:
        metadata = path.lstat()
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or metadata.st_nlink > 1
                or not stat.S_ISREG(metadata.st_mode)):
            return b"null"
        return path.read_bytes()
    except FileNotFoundError:
        return b"[]"
    except OSError:
        return b"null"


def read_retired_store_names(tenant: os.PathLike[str] | str) -> RetiredStoreNames:
    return parse_retired_store_names(capture_retired_store_names(tenant))
