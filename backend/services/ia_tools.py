"""Compatibility facade for ia_tools."""

from __future__ import annotations

from backend.services import ia_tools_produtos as _module_0
from backend.services import ia_tools_vendas as _module_1
from backend.services import ia_tools_marketplaces as _module_2
from backend.services import ia_tools_dispatcher as _module_3
from backend.services.ia_tools_produtos import *
from backend.services.ia_tools_vendas import *
from backend.services.ia_tools_marketplaces import *
from backend.services.ia_tools_dispatcher import *

_MODULES = (
    _module_0,
    _module_1,
    _module_2,
    _module_3,
)


def _peer_globals() -> dict[str, object]:
    peers = {}
    for module in _MODULES:
        for name in getattr(module, "PEER_EXPORTS", getattr(module, "__all__", ())):
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_ia_tools_runtime(runtime_module=None, peers=None):
    combined = dict(peers or {})
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    combined.update(_peer_globals())
    for module in _MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, combined)
    for name, value in combined.items():
        if name in __all__ or name.startswith("_"):
            globals()[name] = value
    return runtime_module


__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.startswith("IA_")
        or name.startswith("FAVORITOS_PESQUISAS_IA")
        or name.startswith("GEMINI_")
        or name.startswith("VERTEX_")
        or name.startswith("ia_")
        or name == "servir_imagem_ia"
    )
]
if "configure_ia_tools_runtime" not in __all__:
    __all__.append("configure_ia_tools_runtime")

configure_ia_tools_runtime()
