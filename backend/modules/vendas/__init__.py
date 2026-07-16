"""Public composition API for the isolated Vendas domain."""

from .dependencies import LegacyBlingVendasAdapter, VendasModuleDependencies
from .module import VendasModule, create_vendas_module, get_default_vendas_module, install_default_vendas_module
from .repository import VendasRepository
from .service import VendasService
from .state import VendasSyncState
from .sync_service import VendasSyncService

__all__ = [
    "LegacyBlingVendasAdapter",
    "VendasModuleDependencies",
    "VendasModule",
    "VendasRepository",
    "VendasService",
    "VendasSyncService",
    "VendasSyncState",
    "create_vendas_module",
    "install_default_vendas_module",
    "get_default_vendas_module",
]
