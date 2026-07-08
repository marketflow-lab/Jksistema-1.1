"""Compatibility facade for Shared Sync collection helpers."""

from __future__ import annotations

from backend.services import shared_sync_collect_files as _child_0
from backend.services.shared_sync_collect_files import *
from backend.services import shared_sync_delta as _child_1
from backend.services.shared_sync_delta import *
from backend.services import shared_sync_bundle as _child_2
from backend.services.shared_sync_bundle import *

_CHILD_MODULES = (_child_0, _child_1, _child_2,)


def configure_shared_sync_collect_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    for module in _CHILD_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peer_globals)
    return runtime_module


configure_shared_sync_collect_runtime()

__all__ = ["configure_shared_sync_collect_runtime"]
for _module in _CHILD_MODULES:
    for _name in getattr(_module, "__all__", ()): 
        if _name not in __all__:
            __all__.append(_name)
del _module, _name
