"""Helpers for legacy service extraction modules.

These modules keep old public call sites working while large blocks move out of
``backend_api.py``.  The bridge copies the current app runtime globals into the
extracted module so existing helper bodies can keep resolving shared dependencies
until those dependencies are isolated behind explicit configs.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import MutableMapping


def current_backend_runtime() -> ModuleType | None:
    return sys.modules.get("backend_api") or sys.modules.get("__main__")


def bind_runtime_globals(target_globals: MutableMapping[str, object], runtime: ModuleType | None = None) -> ModuleType | None:
    runtime = runtime or current_backend_runtime()
    if runtime is None:
        return None

    for name, value in vars(runtime).items():
        if name.startswith("__") or name in target_globals:
            continue
        target_globals[name] = value
    target_globals["_runtime"] = runtime
    return runtime
