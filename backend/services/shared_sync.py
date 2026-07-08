"""Compatibility facade for the Shared Sync module."""

from __future__ import annotations

from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import *
from backend.services.shared_sync_context import configure_shared_sync_context
from backend.services import shared_sync_config as _shared_sync_module_0
from backend.services import shared_sync_collect_files as _shared_sync_module_1
from backend.services import shared_sync_delta as _shared_sync_module_2
from backend.services import shared_sync_bundle as _shared_sync_module_3
from backend.services import shared_sync_remote as _shared_sync_module_4
from backend.services import shared_sync_merge_user_data as _shared_sync_module_5
from backend.services import shared_sync_merge_integracoes as _shared_sync_module_6
from backend.services import shared_sync_merge_sqlite as _shared_sync_module_7
from backend.services import shared_sync_apply_scope as _shared_sync_module_8
from backend.services import shared_sync_machine as _shared_sync_module_9
from backend.services import shared_sync_user_identity as _shared_sync_module_10
from backend.services import shared_sync_user_links as _shared_sync_module_11
from backend.services import shared_sync_user_pairs as _shared_sync_module_12
from backend.services import shared_sync_user_endpoints as _shared_sync_module_13
from backend.services import shared_sync_machine_endpoints as _shared_sync_module_14
from backend.services.shared_sync_config import *
from backend.services.shared_sync_collect_files import *
from backend.services.shared_sync_delta import *
from backend.services.shared_sync_bundle import *
from backend.services.shared_sync_remote import *
from backend.services.shared_sync_merge_user_data import *
from backend.services.shared_sync_merge_integracoes import *
from backend.services.shared_sync_merge_sqlite import *
from backend.services.shared_sync_apply_scope import *
from backend.services.shared_sync_machine import *
from backend.services.shared_sync_user_identity import *
from backend.services.shared_sync_user_links import *
from backend.services.shared_sync_user_pairs import *
from backend.services.shared_sync_user_endpoints import *
from backend.services.shared_sync_machine_endpoints import *

_SHARED_SYNC_MODULES = (
    _shared_sync_module_0,
    _shared_sync_module_1,
    _shared_sync_module_2,
    _shared_sync_module_3,
    _shared_sync_module_4,
    _shared_sync_module_5,
    _shared_sync_module_6,
    _shared_sync_module_7,
    _shared_sync_module_8,
    _shared_sync_module_9,
    _shared_sync_module_10,
    _shared_sync_module_11,
    _shared_sync_module_12,
    _shared_sync_module_13,
    _shared_sync_module_14,
)


def _shared_sync_peer_globals() -> dict[str, object]:
    peers = {
        name: value
        for name, value in globals().items()
        if name.startswith("_shared_sync") or name.startswith("shared_sync") or name.startswith("admin_shared_sync") or name.startswith("SHARED_SYNC")
    }
    for module in _SHARED_SYNC_MODULES:
        for name in getattr(module, "__all__", ()): 
            if name.startswith("configure_shared_sync"):
                continue
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_shared_sync_runtime(runtime_module=None):
    configure_shared_sync_context(runtime_module)
    peers = _shared_sync_peer_globals()
    for module in _SHARED_SYNC_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peers)
    peers = _shared_sync_peer_globals()
    for module in _SHARED_SYNC_MODULES:
        module.__dict__.update(peers)
    globals().update(peers)
    return runtime_module


configure_shared_sync_runtime()

__all__ = [
    "configure_shared_sync_runtime",
]
for _name in sorted(_shared_sync_peer_globals()):
    if _name not in __all__:
        __all__.append(_name)
del _name
