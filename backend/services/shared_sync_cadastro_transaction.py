"""One rollback boundary for a Cadastro snapshot, including photo fan-out."""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os

from fastapi import HTTPException

_active = ContextVar("shared_sync_cadastro_transaction", default=None)


def enlist(paths):
    """Retain each preimage and its writer lock until the entire apply finishes."""
    journal = _active.get()
    if journal is None:
        return
    from backend.services.cadastro_lojas_produtos import _capturar_estados_arquivos
    from backend.services.path_coordination import path_locks_for
    from backend.services.shared_sync_common import _shared_sync_resolve_tenant_path

    pending = []
    for path in paths:
        path = os.path.abspath(path)
        rel = os.path.relpath(path, journal["tenant"]).replace("\\", "/")
        safe = _shared_sync_resolve_tenant_path(journal["tenant"], rel)
        if os.path.normcase(safe) != os.path.normcase(path):
            raise HTTPException(409, "Destino invalido na transacao do Cadastro.")
        if path not in journal["states"]:
            pending.append(path)
    journal["locks"].enter_context(path_locks_for(pending))
    journal["states"].update(_capturar_estados_arquivos(pending))


@contextmanager
def transaction(tenant, paths):
    current = _active.get()
    if current is not None:
        if current["tenant"] != tenant:
            raise HTTPException(409, "Cliente divergente na transacao do Cadastro.")
        enlist(paths)
        yield
        return
    from backend.services.cadastro_lojas_produtos import _rollback_arquivos

    with ExitStack() as locks:
        journal = {"tenant": tenant, "states": {}, "locks": locks}
        token = _active.set(journal)
        try:
            enlist(paths)
            try:
                yield
            except BaseException:
                _rollback_arquivos(journal["states"])
                raise
        finally:
            _active.reset(token)
