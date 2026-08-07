"""Explicit runtime adapters for the Codex Console."""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Optional


SessionLoader = Callable[[Optional[str]], dict[str, Any]]
PermissionsLoader = Callable[[str, str], dict[str, Any]]


def _missing_session(_authorization: Optional[str]) -> dict[str, Any]:
    raise RuntimeError("Codex Console runtime sem adaptador de sessao.")


def _missing_permissions(_username: str, _client_id: str) -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class ConsoleRuntime:
    base_dir: str
    info_dir: str
    session_loader: SessionLoader
    permissions_loader: PermissionsLoader
    source_module: Optional[ModuleType] = None


_LOCK = threading.RLock()
_RUNTIME = ConsoleRuntime(
    base_dir=os.path.abspath(os.getcwd()),
    info_dir=os.path.abspath(os.path.join(os.getcwd(), "info")),
    session_loader=_missing_session,
    permissions_loader=_missing_permissions,
)


def _default_runtime_module() -> Optional[ModuleType]:
    return sys.modules.get("backend_api") or sys.modules.get("__main__")


def configure(runtime_module: Optional[ModuleType] = None) -> ConsoleRuntime:
    """Configure only the four adapters actually consumed by the console."""

    module = runtime_module or _default_runtime_module()
    if module is None:
        return current()
    base_dir = os.path.abspath(str(getattr(module, "BASE_DIR", "") or os.getcwd()))
    raw_info = str(getattr(module, "PASTA_INFO", "") or os.path.join(base_dir, "info"))
    info_dir = os.path.abspath(raw_info if os.path.isabs(raw_info) else os.path.join(base_dir, raw_info))
    session_loader = getattr(module, "_payload_sessao_por_authorization", _missing_session)
    permissions_loader = getattr(module, "_carregar_permissoes_usuario", _missing_permissions)
    configured = ConsoleRuntime(
        base_dir=base_dir,
        info_dir=info_dir,
        session_loader=session_loader,
        permissions_loader=permissions_loader,
        source_module=module,
    )
    global _RUNTIME
    with _LOCK:
        if _RUNTIME == configured:
            return _RUNTIME
        _RUNTIME = configured
        return _RUNTIME


def current() -> ConsoleRuntime:
    with _LOCK:
        return _RUNTIME


__all__ = ["ConsoleRuntime", "configure", "current"]
