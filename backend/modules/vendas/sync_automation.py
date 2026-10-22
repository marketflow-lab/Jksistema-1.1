"""Local background dispatcher; durable work is owned by SyncQueue.

Only the existing authorized store reader and Bling collection pipeline are used.
Session contexts stay in memory and are never written to the queue.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import uuid

from backend.schemas import VendasSyncRequest
from backend.services.central_accounts_client import current, with_request_context
from backend.services.env_config import _agent_service_only

from .dependencies import _is_fastapi_http_exception, get_vendas_dependencies, logger
from .errors import VendasDomainError
from .progress import _sync_register_active_unlocked, _sync_unregister_active
from .state import SYNC_ACTIVE_LOCK, SYNC_CANCEL_FLAGS, SYNC_THREAD_CONTEXT
from .sync_queue import SyncQueue, SYNC_TIMEZONE

_LOCK = threading.RLock()
_WAKE = threading.Event()
_STOP = threading.Event()
_THREAD = None
_RUNNING = {}
_AUTH = {}
_CHECKING = {}
_CHECK_AUTH = {}
_CENTRAL_CHECK_AUTH = {}
_STORE_CHECK_AUTH = {}


def remember_authorization(client_id: str, store_id: str | None = None):
    """Refresh memory-only authorization during an authenticated request."""
    central = current(client_id)
    if central is not None:
        stores = [str(row["store_id"]) for row in central.stores()]
        check_stores = stores
        if store_id:
            stores = [store_id] if store_id in stores else []
    else:
        stores = [store_id] if store_id else []
        check_stores = stores
    with _LOCK:
        _CHECK_AUTH[client_id] = with_request_context(_run_connection_check)
        if central is not None:
            _CENTRAL_CHECK_AUTH[client_id] = _CHECK_AUTH[client_id]
        for store in check_stores:
            _STORE_CHECK_AUTH[(client_id, store)] = with_request_context(_check_store_connection)
        for store in stores:
            _AUTH[(client_id, store)] = with_request_context(_run_job)
            from .return_reason_worker import remember_authorization as remember_reason_authorization
            remember_reason_authorization(client_id, store)
    wake()


def _run_job(req, client_id, job_id, owner):
    from .sync_engine import _sincronizar_vendas_thread_worker
    try:
        return _sincronizar_vendas_thread_worker(req, client_id, job_id, owner)
    finally:
        _sync_unregister_active(client_id, job_id)
        wake()


def _check_store_connection(client_id, store, requires_session=False):
    from .sync_engine import _resolve_store, _verificar_conexao_bling

    if (requires_session or store["requires_session"]) and current(client_id) is None:
        raise VendasDomainError(401, "Sessão da Central necessária para verificar esta conexão.")
    req = VendasSyncRequest(
        loja=store["loja"], store_id=store["store_id"],
        data_inicio=store["data_inicio"], data_fim=store["data_fim"],
    )
    req = _resolve_store(req, client_id)
    _verificar_conexao_bling(req, client_id)
    return req.store_id


def _run_connection_check(client_id, owner, grant):
    """Finish the whole connection phase before releasing any collection claim.

    Paused stores are checked too, without resuming their work. Failed checks
    leave their warnings intact; they do not stop the remaining stores' checks.
    """
    from .progress import _verificar_cancelamento

    queue = SyncQueue(client_id)
    generation = grant["generation"]
    key = "conexoes-" + owner
    previous = {name: getattr(SYNC_THREAD_CONTEXT, name, None) for name in ("job_id", "lease_check")}
    SYNC_THREAD_CONTEXT.job_id = key
    SYNC_THREAD_CONTEXT.lease_check = lambda: queue.owns_connection_check(owner, generation)
    completed = False
    try:
        if grant["requires_session"] and current(client_id) is None:
            return
        for store in grant["stores"]:
            if _STOP.is_set() or not queue.heartbeat_connection_check(owner, generation):
                return
            try:
                _verificar_cancelamento(client_id)
                with _LOCK:
                    check = _STORE_CHECK_AUTH.get((client_id, store["store_id"])) or _check_store_connection
                store_id = check(client_id, store, grant["requires_session"])
                _verificar_cancelamento(client_id)
                queue.acknowledge_checked_connection(
                    owner, generation, store_id, store["alerts"],
                )
            except Exception as exc:
                if not isinstance(exc, VendasDomainError) and not _is_fastapi_http_exception(exc):
                    raise
                if _STOP.is_set() or not queue.owns_connection_check(owner, generation):
                    return
                # A 401, timeout or denied store must not prevent checking the
                # other accounts, or erase the original error/checkpoints.
                continue
        if not _STOP.is_set():
            completed = queue.complete_connection_check(owner, generation)
    finally:
        try:
            if not completed:
                queue.abandon_connection_check(owner, generation)
        finally:
            for name, value in previous.items():
                if value is None:
                    delattr(SYNC_THREAD_CONTEXT, name)
                else:
                    setattr(SYNC_THREAD_CONTEXT, name, value)
            SYNC_CANCEL_FLAGS.pop(key, None)
            wake()


def dispatch(client_id: str):
    """Claim before launching, so restarts and other processes cannot duplicate work."""
    started = []
    queue = SyncQueue(client_id)
    with _LOCK:
        _reap()
        if queue.connection_check_pending():
            if client_id in _CHECKING:
                return started
            owner = uuid.uuid4().hex
            grant = queue.claim_connection_check(owner)
            if grant is None:
                return started
            with SYNC_ACTIVE_LOCK:
                SYNC_CANCEL_FLAGS.pop(client_id, None)
            authorizations = _CENTRAL_CHECK_AUTH if grant["requires_session"] else _CHECK_AUTH
            target = authorizations.get(client_id) or with_request_context(_run_connection_check)
            thread = threading.Thread(
                target=target, args=(client_id, owner, grant),
                name="vendas-conexoes", daemon=True,
            )
            _CHECKING[client_id] = {"thread": thread, "owner": owner, "generation": grant["generation"]}
            try:
                thread.start()
            except Exception:
                _CHECKING.pop(client_id, None)
                queue.abandon_connection_check(owner, grant["generation"])
                raise
            return started
        while len([key for key in _RUNNING if key[0] == client_id]) < 2:
            exclusions = [value["store_id"] for key, value in _RUNNING.items() if key[0] == client_id]
            owner = uuid.uuid4().hex
            job = queue.claim(owner, exclude_store_ids=exclusions)
            if not job:
                break
            req = VendasSyncRequest(
                loja=job["loja"], store_id=job["store_id"],
                data_inicio=job["data_inicio"], data_fim=job["data_fim"],
                forcar_resync=bool(job["forcar_resync"]), automatico=bool(job["automatico"]),
            )
            job_id = job["id"]
            with SYNC_ACTIVE_LOCK:
                SYNC_CANCEL_FLAGS.pop(client_id, None)
                _sync_register_active_unlocked(client_id, req, job_id=job_id)
            target = _AUTH.get((client_id, job["store_id"])) or with_request_context(_run_job)
            thread = threading.Thread(
                target=target, args=(req, client_id, job_id, owner),
                name="vendas-coleta", daemon=True,
            )
            _RUNNING[(client_id, job_id)] = {
                "thread": thread, "owner": owner, "store_id": job["store_id"],
            }
            try:
                thread.start()
            except Exception:
                _RUNNING.pop((client_id, job_id), None)
                _sync_unregister_active(client_id, job_id)
                queue.fail_job(job_id, owner, "Não foi possível iniciar a coleta. A fila foi preservada.")
                raise
            started.append(job_id)
    return started


def _reap():
    for key, value in list(_RUNNING.items()):
        if not value["thread"].is_alive():
            _RUNNING.pop(key, None)
    for client_id, value in list(_CHECKING.items()):
        if not value["thread"].is_alive():
            _CHECKING.pop(client_id, None)


def _tenants():
    root = Path(get_vendas_dependencies().paths.info_dir)
    if not root.exists():
        return []
    return [path.parent.name for path in root.glob("*/vendas_sync_queue.db") if not path.is_symlink()]


def tick():
    with _LOCK:
        _reap()
        for (client_id, job_id), value in list(_RUNNING.items()):
            if not SyncQueue(client_id).heartbeat(job_id, value["owner"]):
                SYNC_CANCEL_FLAGS[job_id] = True
        for client_id, value in list(_CHECKING.items()):
            if not SyncQueue(client_id).heartbeat_connection_check(value["owner"], value["generation"]):
                SYNC_CANCEL_FLAGS["conexoes-" + value["owner"]] = True
    for client_id in _tenants():
        if _STOP.is_set():
            return
        try:
            queue = SyncQueue(client_id)
            queue.schedule_due(datetime.now(SYNC_TIMEZONE).date())
            dispatch(client_id)
        except Exception:
            # A failed queue is never converted to an empty/successful queue.
            logger.warning("Não foi possível processar uma fila de vendas; nova tentativa pendente.")
        try:
            from .return_reason_worker import dispatch as dispatch_return_reasons
            dispatch_return_reasons(client_id)
        except Exception:
            logger.warning("A identificação dos motivos de devolução aguardará nova tentativa.")


def _loop():
    while not _STOP.is_set():
        try:
            tick()
        except Exception:
            logger.warning("Trabalhador de vendas aguardando nova tentativa.")
        _WAKE.wait(15)
        _WAKE.clear()


def wake():
    _WAKE.set()


def start():
    global _THREAD
    if _agent_service_only():
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        from .return_reason_worker import start as start_return_reasons
        start_return_reasons()
        _THREAD = threading.Thread(target=_loop, name="vendas-fila", daemon=True)
        _THREAD.start()


def stop():
    _STOP.set()
    from .return_reason_worker import stop as stop_return_reasons
    stop_return_reasons()
    with _LOCK:
        for _, job_id in _RUNNING:
            SYNC_CANCEL_FLAGS[job_id] = True
        for client_id, value in list(_CHECKING.items()):
            SYNC_CANCEL_FLAGS["conexoes-" + value["owner"]] = True
            SyncQueue(client_id).abandon_connection_check(value["owner"], value["generation"])
        _AUTH.clear()
        _CHECK_AUTH.clear()
        _CENTRAL_CHECK_AUTH.clear()
        _STORE_CHECK_AUTH.clear()
    wake()
    if _THREAD is not None and _THREAD is not threading.current_thread():
        _THREAD.join(timeout=2)


__all__ = ["remember_authorization", "dispatch", "tick", "wake", "start", "stop"]
