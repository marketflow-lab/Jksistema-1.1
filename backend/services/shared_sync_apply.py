"""Compatibility facade for Shared Sync apply helpers."""

from __future__ import annotations

from backend.services import shared_sync_merge as _merge
from backend.services.shared_sync_merge import *
from backend.services import shared_sync_apply_scope as _apply_scope
from backend.services.shared_sync_apply_scope import *

_CHILD_MODULES = (_merge, _apply_scope,)


def configure_shared_sync_apply_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    for module in _CHILD_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peer_globals)
    return runtime_module


configure_shared_sync_apply_runtime()

__all__ = ["configure_shared_sync_apply_runtime"]
for _module in _CHILD_MODULES:
    for _name in getattr(_module, "__all__", ()): 
        if _name not in __all__:
            __all__.append(_name)
del _module, _name
