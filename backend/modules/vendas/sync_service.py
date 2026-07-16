"""Application service for resumable Vendas synchronization."""

from __future__ import annotations

from dataclasses import dataclass

from backend.schemas import VendasSyncRequest

from . import progress, sync_engine
from .state import VendasSyncState


@dataclass(frozen=True)
class VendasSyncService:
    state: VendasSyncState

    def iniciar(self, req: VendasSyncRequest, client_id: str):
        return sync_engine.sincronizar_vendas(req, client_id)

    def progresso(self, client_id: str):
        return progress.progresso_sincronizacao_vendas(client_id)

    def cancelar(self, client_id: str):
        return progress.cancelar_sincronizacao_vendas(client_id)


__all__ = ["VendasSyncService"]
