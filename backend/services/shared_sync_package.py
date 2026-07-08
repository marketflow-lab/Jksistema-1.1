"""Compatibility facade for Shared Sync package helpers."""

from __future__ import annotations

from backend.services import shared_sync_collect as _collect
from backend.services.shared_sync_collect import *
from backend.services import shared_sync_remote as _remote
from backend.services.shared_sync_remote import *

_CHILD_MODULES = (_collect, _remote,)


def configure_shared_sync_package_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    for module in _CHILD_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peer_globals)
    return runtime_module


configure_shared_sync_package_runtime()

__all__ = ["configure_shared_sync_package_runtime"]
for _module in _CHILD_MODULES:
    for _name in getattr(_module, "__all__", ()): 
        if _name not in __all__:
            __all__.append(_name)
del _module, _name
