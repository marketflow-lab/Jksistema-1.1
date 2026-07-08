"""Compatibility facade for the IA module."""

from __future__ import annotations

from backend.services.ia_common import *
from backend.services.ia_context import *
from backend.services.ia_context import configure_ia_context
from backend.services.ia_state import *
from backend.services import ia_tools as _ia_module_0
from backend.services import ia_conversas as _ia_module_1
from backend.services import ia_rag as _ia_module_2
from backend.services import ia_providers as _ia_module_3
from backend.services import ia_web as _ia_module_4
from backend.services import ia_treinamento_ppv as _ia_module_5
from backend.services import ia_endpoints as _ia_module_6
from backend.services.ia_tools import *
from backend.services.ia_conversas import *
from backend.services.ia_rag import *
from backend.services.ia_providers import *
from backend.services.ia_web import *
from backend.services.ia_treinamento_ppv import *
from backend.services.ia_endpoints import *

_IA_MODULES = (
    _ia_module_0,
    _ia_module_1,
    _ia_module_2,
    _ia_module_3,
    _ia_module_4,
    _ia_module_5,
    _ia_module_6,
)


def _ia_peer_globals() -> dict[str, object]:
    peers = {
        name: value
        for name, value in globals().items()
        if (
            (name.startswith("_") and not name.startswith("__"))
            or name.startswith("IA_")
            or name.startswith("FAVORITOS_PESQUISAS_IA")
            or name.startswith("GEMINI_")
            or name.startswith("VERTEX_")
            or name.startswith("ia_")
            or name == "servir_imagem_ia"
        )
    }
    for module in _IA_MODULES:
        for name in getattr(module, "__all__", ()): 
            if name.startswith("configure_ia"):
                continue
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_ia_runtime(runtime_module=None):
    configure_ia_context(runtime_module)
    peers = _ia_peer_globals()
    for module in _IA_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peers)
    peers = _ia_peer_globals()
    for module in _IA_MODULES:
        module.__dict__.update(peers)
    globals().update(peers)
    return runtime_module


configure_ia_runtime()

__all__ = ["configure_ia_runtime"]
for _name in sorted(_ia_peer_globals()):
    if _name not in __all__:
        __all__.append(_name)
del _name
