"""Compatibility facade for the Impostos module."""

from __future__ import annotations

from backend.services.impostos_common import *
from backend.services.impostos_context import *
from backend.services.impostos_context import configure_impostos_context
from backend.services import impostos_siscomex as _impostos_module_0
from backend.services import impostos_aliquotas as _impostos_module_1
from backend.services import impostos_regras as _impostos_module_2
from backend.services import impostos_ncm as _impostos_module_3
from backend.services import impostos_convenio_mg as _impostos_module_4
from backend.services import impostos_simulador as _impostos_module_5
from backend.services.impostos_siscomex import *
from backend.services.impostos_aliquotas import *
from backend.services.impostos_regras import *
from backend.services.impostos_ncm import *
from backend.services.impostos_convenio_mg import *
from backend.services.impostos_simulador import *

_IMPOSTOS_MODULES = (
    _impostos_module_0,
    _impostos_module_1,
    _impostos_module_2,
    _impostos_module_3,
    _impostos_module_4,
    _impostos_module_5,
)


def _impostos_peer_globals() -> dict[str, object]:
    peers = {
        name: value
        for name, value in globals().items()
        if (
            (name.startswith("_") and not name.startswith("__"))
            or name.startswith("SISCOMEX")
            or name.startswith("PIS_COFINS")
            or name.startswith("ALIQUOTAS")
            or name.endswith("_impostos")
            or name.startswith("consultar_")
            or name.startswith("obter_")
            or name.startswith("salvar_")
            or name.startswith("testar_")
            or name.startswith("atualizar_")
            or name.startswith("importar_")
            or name.startswith("listar_")
            or name.startswith("simular_")
            or name.startswith("aplicar_")
            or name.startswith("calcular_")
        )
    }
    for module in _IMPOSTOS_MODULES:
        for name in getattr(module, "__all__", ()): 
            if name.startswith("configure_impostos"):
                continue
            if hasattr(module, name):
                peers[name] = getattr(module, name)
    return peers


def configure_impostos_runtime(runtime_module=None):
    configure_impostos_context(runtime_module)
    peers = _impostos_peer_globals()
    for module in _IMPOSTOS_MODULES:
        configure = getattr(module, f"configure_{module.__name__.rsplit('.', 1)[-1]}_runtime", None)
        if callable(configure):
            configure(runtime_module, peers)
    peers = _impostos_peer_globals()
    for module in _IMPOSTOS_MODULES:
        module.__dict__.update(peers)
    globals().update(peers)
    return runtime_module


configure_impostos_runtime()

__all__ = ["configure_impostos_runtime"]
for _name in sorted(_impostos_peer_globals()):
    if _name not in __all__:
        __all__.append(_name)
del _name
