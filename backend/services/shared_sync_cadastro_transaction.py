"""One rollback boundary for a Cadastro snapshot, including photo fan-out."""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os
from pathlib import Path

from fastapi import HTTPException

_active = ContextVar("shared_sync_cadastro_transaction", default=None)


def enlist(paths):
    """Retain each preimage and its writer lock until the entire apply finishes."""
    journal = _active.get()
    if journal is None:
        return
    from backend.services.cadastro_lojas_produtos import _capturar_estados_arquivos
    from backend.services.store_coordination import coordinated_path_locks as path_locks_for
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


def enlist_context_paths(tenant, paths):
    """Enlist only curated notes and the private sync ledger.

    The generic Shared Sync resolver deliberately rejects ContextVault and
    context_hub.  This narrow entrypoint keeps that boundary intact while
    extending the already-active Cadastro rollback journal to the two exact
    Context Hub locations used by the typed machine-sync importer.
    """

    journal = _active.get()
    if journal is None:
        raise HTTPException(409, "O contexto de IA exige a transacao ativa do Cadastro.")
    tenant_abs = os.path.abspath(str(tenant or ""))
    if os.path.normcase(journal["tenant"]) != os.path.normcase(tenant_abs):
        raise HTTPException(409, "Cliente divergente na transacao do contexto de IA.")

    from backend.modules.context_hub.path_safety import _assert_path_chain_safe
    from backend.services.cadastro_lojas_produtos import _capturar_estados_arquivos
    from backend.services.store_coordination import coordinated_path_locks as path_locks_for
    from backend.services.shared_sync_ai_context import AI_CONTEXT_LEDGER_NAME

    tenant_path = Path(tenant_abs)
    curated_root = tenant_path / "ContextVault" / "80_Curadoria"
    ledger_path = tenant_path / "context_hub" / AI_CONTEXT_LEDGER_NAME
    allowed = []
    for raw in paths:
        candidate = Path(os.path.abspath(str(raw or "")))
        _assert_path_chain_safe(candidate, tenant_path.parent)
        try:
            candidate.relative_to(curated_root)
            under_curated = True
        except ValueError:
            under_curated = False
        if not under_curated and os.path.normcase(str(candidate)) != os.path.normcase(str(ledger_path)):
            raise HTTPException(409, "Destino invalido na transacao do contexto de IA.")
        allowed.append(str(candidate))

    pending = [path for path in allowed if path not in journal["states"]]
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
