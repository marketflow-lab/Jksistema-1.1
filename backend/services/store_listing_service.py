"""Read-only store cards and transaction-owned local projection lifecycle."""
from __future__ import annotations

import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException

from . import store_public_snapshot as projection
from .path_coordination import canonical_path_key
from .store_coordination import StoreCoordinationError, legacy_resource_lock, store_lock
from .store_snapshot_transactions import (
    StorePublicationRecoveryError, publication_pending, publication_transaction,
)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stores-projection")
_JOBS_LOCK = threading.Lock()
_JOBS = set()
_FAILURES = {}


def _source():
    from . import integracoes
    return integracoes


def _tenant(client_id):
    source = _source()
    path = source._tenant_path(client_id)
    return source._integracoes_tenant_fotos_coordenacao(client_id, path)


def _validate_source(client_id):
    source = _source()
    journal_path = Path(source._integracoes_transacao_pendente_path(client_id))
    if journal_path.exists():
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8-sig"))
            source._integracoes_validar_transacao_payload(journal)
            if journal.get("committed") is not True and journal.get("aborted") is not True:
                raise ValueError()
        except Exception:
            raise StorePublicationRecoveryError("canonical_transaction_pending") from None
    source._integracoes_validar_estado_atual_para_envio(client_id)


def _publish(client_id, tenant_path, *, empty_initialized=False):
    source = _source()
    _validate_source(client_id)
    path = Path(tenant_path) / "lojas_config.json"
    if not path.exists() and not empty_initialized:
        raise projection.SnapshotUnavailable("not_initialized")
    stores = source._integracoes_ler_lojas_config_arquivo(str(path)) if path.exists() else []
    source._integracoes_validar_identidades_lojas_local(stores)
    snapshot = projection.build_snapshot(stores)
    try:
        old = projection.read_snapshot(tenant_path)
    except projection.SnapshotUnavailable:
        old = None
    if old and old["lojas"] == snapshot["lojas"]:
        return old
    projection.write_snapshot(tenant_path, snapshot)
    return snapshot


def _recover(client_id):
    # Existing domain recovery owns its journal and canonical validation. A
    # publication barrier must never blindly revert already-committed CSV/SQL.
    stores = _source().carregar_lojas(client_id)
    if not (Path(_tenant(client_id)) / "lojas_config.json").exists():
        _source().salvar_lojas(client_id, stores)
    _validate_source(client_id)


@contextlib.contextmanager
def lojas_config_lock(client_id, *, recovery="validate"):
    tenant_path = _tenant(client_id)
    try:
        legacy = (legacy_resource_lock(_source().PASTA_INFO)
                  if str(client_id).strip() == "default" else contextlib.nullcontext())
        with legacy, store_lock(tenant_path):
            with publication_transaction(
                tenant_path, lambda: _publish(client_id, tenant_path),
                lambda: _recover(client_id), recovery=recovery,
            ):
                yield
    except StoreCoordinationError as exc:
        raise HTTPException(409, detail={
            "code": "stores_busy" if exc.code == "locked" else "stores_coordination_unavailable",
            "message": "As lojas deste cliente estao sendo atualizadas. Tente novamente.",
        }) from None
    except StorePublicationRecoveryError:
        raise HTTPException(409, detail={
            "code": "stores_recovery_required",
            "message": "A configuracao de lojas precisa concluir sua recuperacao.",
        }) from None


def _initialize(client_id, tenant_path, key):
    try:
        if canonical_path_key(_tenant(client_id)) != key:
            raise StorePublicationRecoveryError("tenant_context_changed")
        # Cross-process election uses the tenant mutex, with a second check.
        with lojas_config_lock(client_id):
            initialize = publication_pending(tenant_path)
            try:
                projection.read_snapshot(tenant_path)
            except projection.SnapshotUnavailable:
                initialize = True
            if initialize:
                stores = _source().carregar_lojas(client_id)
                if not (Path(tenant_path) / "lojas_config.json").exists():
                    _source().salvar_lojas(client_id, stores)
        projection.read_snapshot(tenant_path)
        if publication_pending(tenant_path):
            raise projection.SnapshotUnavailable("publication_pending")
        with _JOBS_LOCK:
            _FAILURES.pop(key, None)
    except Exception:
        with _JOBS_LOCK:
            _FAILURES[key] = time.monotonic()
    finally:
        with _JOBS_LOCK:
            _JOBS.discard(key)


def _ensure_initialization(client_id, tenant_path):
    key = canonical_path_key(tenant_path)
    with _JOBS_LOCK:
        failed_at = _FAILURES.get(key)
        if key not in _JOBS and (failed_at is None or time.monotonic() - failed_at >= 8):
            _JOBS.add(key)
            _EXECUTOR.submit(_initialize, client_id, tenant_path, key)
        return failed_at is None


def read_store_cards(client_id):
    """Never waits for a store/catalog/photo lock or performs canonical writes."""
    from .central_accounts_client import current, session_expired
    central = current(client_id)
    if central is not None:
        if central.expires_at <= time.time():
            raise session_expired()
        snapshot = projection.build_snapshot(central.public_stores())
        return {"lojas": snapshot["lojas"], "session_scoped": True, "snapshot": {
            "generation": snapshot["generation"], "published_at": snapshot["published_at"],
            "status": "ready",
        }}
    tenant_path = _tenant(client_id)
    try:
        snapshot = projection.read_snapshot(tenant_path)
    except projection.SnapshotUnavailable:
        initializing = _ensure_initialization(client_id, tenant_path)
        raise HTTPException(503, detail={
            "code": "stores_snapshot_initializing" if initializing else "stores_snapshot_unavailable",
            "message": "Carregando configuracao de lojas." if initializing else "Nao foi possivel atualizar a configuracao de lojas.",
        }, headers={"Retry-After": "2"}) from None
    pending = publication_pending(tenant_path)
    if pending:
        _ensure_initialization(client_id, tenant_path)
    return {"lojas": snapshot["lojas"], "snapshot": {
        "generation": snapshot["generation"], "published_at": snapshot["published_at"],
        "status": "updating" if pending else "ready",
    }}
