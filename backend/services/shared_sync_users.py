"""Compatibility facade for Shared Sync user-share helpers."""

from __future__ import annotations

from backend.services import shared_sync_user_store as _user_store
from backend.services.shared_sync_user_store import *
from backend.services import shared_sync_user_pairs as _user_pairs
from backend.services.shared_sync_user_pairs import *

_CHILD_MODULES = (_user_store, _user_pairs,)


def configure_shared_sync_users_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    for module in _CHILD_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peer_globals)
    return runtime_module


configure_shared_sync_users_runtime()

__all__ = ["configure_shared_sync_users_runtime"]
for _module in _CHILD_MODULES:
    for _name in getattr(_module, "__all__", ()): 
        if _name not in __all__:
            __all__.append(_name)
del _module, _name
