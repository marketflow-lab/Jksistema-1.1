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

from .dependencies import get_vendas_dependencies, logger
from .progress import _sync_register_active_unlocked, _sync_unregister_active
from .state import SYNC_ACTIVE_LOCK, SYNC_CANCEL_FLAGS
from .sync_queue import SyncQueue, SYNC_TIMEZONE

_LOCK = threading.RLock()
_WAKE = threading.Event()
_STOP = threading.Event()
_THREAD = None
_RUNNING = {}
_AUTH = {}


def remember_authorization(client_id: str, store_id: str | None = None):
    """Refresh memory-only authorization during an authenticated request."""
    central = current(client_id)
    if central is not None:
        stores = [str(row["store_id"]) for row in central.stores()]
        if store_id:
            stores = [store_id] if store_id in stores else []
    else:
        stores = [store_id] if store_id else []
    with _LOCK:
        for store in stores:
            _AUTH[(client_id, store)] = with_request_context(_run_job)
    wake()


def _run_job(req, client_id, job_id, owner):
    from .sync_engine import _sincronizar_vendas_thread_worker
    try:
        return _sincronizar_vendas_thread_worker(req, client_id, job_id, owner)
    finally:
        _sync_unregister_active(client_id, job_id)
        wake()


def dispatch(client_id: str):
    """Claim before launching, so restarts and other processes cannot duplicate work."""
    started = []
    queue = SyncQueue(client_id)
    with _LOCK:
        _reap()
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
        _THREAD = threading.Thread(target=_loop, name="vendas-fila", daemon=True)
        _THREAD.start()


def stop():
    _STOP.set()
    with _LOCK:
        for _, job_id in _RUNNING:
            SYNC_CANCEL_FLAGS[job_id] = True
        _AUTH.clear()
    wake()
    if _THREAD is not None and _THREAD is not threading.current_thread():
        _THREAD.join(timeout=2)


__all__ = ["remember_authorization", "dispatch", "tick", "wake", "start", "stop"]
