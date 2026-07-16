"""Composition root for the isolated Vendas domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .dependencies import VendasModuleDependencies, install_vendas_dependencies
from .performance import _vendas_preparar_bancos_background
from .repository import VendasRepository
from .router import create_vendas_router
from .service import VendasService
from .state import DEFAULT_SYNC_STATE, VendasSyncState
from .sync_service import VendasSyncService


@dataclass(frozen=True)
class VendasModule:
    router: Any
    service: VendasService
    sync_service: VendasSyncService
    repository: VendasRepository
    state: VendasSyncState
    prepare_databases: Callable[[], None]


_default_module: VendasModule | None = None


def create_vendas_module(dependencies: VendasModuleDependencies) -> VendasModule:
    install_vendas_dependencies(dependencies)
    DEFAULT_SYNC_STATE.max_active_sync = int(dependencies.max_active_sync)
    repository = VendasRepository()
    service = VendasService(repository)
    sync_service = VendasSyncService(DEFAULT_SYNC_STATE)
    router = create_vendas_router(service, sync_service, dependencies)
    return VendasModule(
        router=router,
        service=service,
        sync_service=sync_service,
        repository=repository,
        state=DEFAULT_SYNC_STATE,
        prepare_databases=_vendas_preparar_bancos_background,
    )


def install_default_vendas_module(module: VendasModule) -> VendasModule:
    global _default_module
    _default_module = module
    return module


def get_default_vendas_module() -> VendasModule:
    if _default_module is None:
        raise RuntimeError("O modulo Vendas ainda nao foi composto pela aplicacao.")
    return _default_module


__all__ = [
    "VendasModule",
    "create_vendas_module",
    "install_default_vendas_module",
    "get_default_vendas_module",
]
