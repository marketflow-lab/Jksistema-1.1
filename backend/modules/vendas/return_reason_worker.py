"""Bounded background enrichment using the authenticated Vendas lifecycle."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time

from backend.services.central_accounts_client import with_request_context

from .dependencies import get_tenant_path, get_vendas_dependencies
from .legacy import _listar_bancos_vendas_tenant, buscar_loja
from .performance import open_vendas_readonly
from .return_reason_queue import process_due
from .return_reasons_bling import resolve_reference
from .return_reasons_ml import resolve_reason
from .state import SYNC_CANCEL_FLAGS


_LOCK = threading.RLock()
_AUTH = {}
_RUNNING = {}
_STOP = threading.Event()
_BULK_AUTH = {}
_BULK_TURN = {}
_BULK_WAKE = threading.Event()
_BULK_THREAD = None
_WAKE_TENANTS = set()


def _conflicting(result):
    code = str(result.get("error_code") or "")
    return result.get("result_status") == "ambiguous" or "mismatch" in code or "identity" in code


def resolve_job(client_id: str, job: dict) -> dict:
    """Prefer the buyer's ML reason; retain only explicit Bling fallback text."""
    store_id, loja = str(job.get("store_id") or ""), str(job.get("loja") or "")
    if _STOP.is_set() or SYNC_CANCEL_FLAGS.get(client_id):
        return {"result_status": "unavailable", "retryable": True, "error_code": "worker_stopped"}
    store = buscar_loja(client_id, loja, store_id=store_id)
    if not store or str(store.get("store_id") or "") != store_id:
        return {"result_status": "unavailable", "error_code": "store_identity_mismatch"}
    loja = str(store.get("nome") or loja)
    references = job.get("references") or {}
    previous_result = None
    # The collector saved this field from numeroPedidoLoja, never from a display number.
    if references.get("order_id"):
        linked = {**job, "order_id": references["order_id"]}
        for key in ("claim_id", "return_id", "seller_id", "site_id"):
            linked.pop(key, None)
            if references.get(key):
                linked[key] = references[key]
        result = resolve_reason(client_id, store_id, loja, linked)
        previous_result = result
        if result.get("result_status") in {"identified", "reconnect_required"} or result.get("retryable") or _conflicting(result):
            return result
    if _STOP.is_set() or SYNC_CANCEL_FLAGS.get(client_id):
        return {"result_status": "unavailable", "retryable": True, "error_code": "worker_stopped"}
    bling = resolve_reference(client_id, store_id, loja, {**job, "order_id": references.get("order_id", "")})
    # Old result columns are never treated as a new documentary order link.
    order_id = str(bling.get("order_id") or "")
    if not order_id:
        return bling
    if _STOP.is_set() or SYNC_CANCEL_FLAGS.get(client_id):
        return {"result_status": "unavailable", "retryable": True, "error_code": "worker_stopped"}
    linked = {**job, "order_id": order_id}
    for key in ("claim_id", "return_id", "seller_id", "site_id"):
        linked.pop(key, None)
        if references.get(key):
            linked[key] = references[key]
    result = previous_result if previous_result and order_id == references.get("order_id") else resolve_reason(client_id, store_id, loja, linked)
    if result.get("result_status") == "identified":
        return result
    if result.get("result_status") == "reconnect_required" or _conflicting(result):
        return result
    if bling.get("result_status") == "identified":
        return bling
    return result


def remember_authorization(client_id: str, store_id: str):
    if client_id and store_id:
        with _LOCK:
            _AUTH[(client_id, store_id)] = with_request_context(_process_store)


def _due_stores(client_id: str):
    root = Path(get_tenant_path(client_id)).resolve()
    result = []
    for filename in _listar_bancos_vendas_tenant(client_id):
        path = Path(filename)
        if (not path.is_file() or path.is_symlink() or path.is_junction()
                or not path.resolve().is_relative_to(root)):
            continue
        conn = None
        try:
            conn = open_vendas_readonly(str(path))
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='devolucao_motivos'").fetchone()
            if exists:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(devolucao_motivos)")}
                if not {"store_id", "status", "next_attempt_at", "lease_until"} <= columns:
                    continue
                rows = conn.execute("""SELECT DISTINCT store_id FROM devolucao_motivos
                    WHERE ((status IN ('pending','retry','done') AND next_attempt_at<=?)
                        OR (status='processing' AND lease_until<=?))
                    ORDER BY store_id""", (time.time(), time.time())).fetchall()
                result.extend((str(path), str(row[0])) for row in rows if row[0])
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
    return result


def _process_store(client_id, db_path, store_id):
    try:
        from .return_reason_actions import legacy_owner_resolver
        process_due(db_path, store_id, lambda job: resolve_job(client_id, job), limit=5,
                    legacy_owner=legacy_owner_resolver(client_id))
    except Exception:
        # Leases survive process failure. No exception/payload is logged.
        pass


def dispatch(client_id: str, *, bulk_only=False):
    if _STOP.is_set() or SYNC_CANCEL_FLAGS.get(client_id):
        return
    with _LOCK:
        for key, thread in list(_RUNNING.items()):
            if not thread.is_alive():
                _RUNNING.pop(key, None)
        if len(_RUNNING) >= 2 or any(key[0] == client_id for key in _RUNNING):
            return
        from .return_reason_actions import pending_operations
        bulk = []
        try:
            bulk = [(scope, required) for scope, required in pending_operations(client_id)
                    if (client_id, scope) in _BULK_AUTH]
        except Exception:
            pass
        # Background discovery never invents a local authorization context.
        authorized = {key[1] for key in _AUTH if key[0] == client_id}
        due = [] if bulk_only or not authorized else [
            (path, store) for path, store in _due_stores(client_id) if store in authorized
        ]
        if bulk and (_BULK_TURN.get(client_id, True) or not due):
            scope, _required = bulk[0]
            key = (client_id, "bulk/" + scope)
            target = _BULK_AUTH[(client_id, scope)]
            thread = threading.Thread(target=target, args=(client_id, scope), name="vendas-motivos", daemon=True)
            _RUNNING[key] = thread
            _BULK_TURN[client_id] = False
            try:
                thread.start()
            except Exception:
                _RUNNING.pop(key, None)
                raise
            return
        for db_path, store_id in due:
            _BULK_TURN[client_id] = True
            key = (client_id, store_id)
            target = _AUTH[key]
            thread = threading.Thread(target=target, args=(client_id, db_path, store_id),
                                      name="vendas-motivos", daemon=True)
            _RUNNING[key] = thread
            try:
                thread.start()
            except Exception:
                _RUNNING.pop(key, None)
                raise
            break


def remember_bulk_authorization(client_id, scope):
    with _LOCK:
        _BULK_AUTH[(client_id, scope)] = with_request_context(_process_bulk)


def wake(client_id=None):
    if client_id:
        with _LOCK:
            _WAKE_TENANTS.add(client_id)
    _BULK_WAKE.set()


def _process_bulk(client_id, scope):
    from .return_reason_actions import advance_search
    try:
        if not _STOP.is_set() and not SYNC_CANCEL_FLAGS.get(client_id):
            advance_search(client_id, scope, lambda job: resolve_job(client_id, job))
    except Exception:
        # The durable operation/queue retains progress. Never log provider data.
        pass


def _bulk_loop():
    from .return_reason_actions import FILENAME
    while not _STOP.is_set():
        _BULK_WAKE.clear()
        try:
            with _LOCK:
                tenants = tuple(_WAKE_TENANTS)
                _WAKE_TENANTS.clear()
            for client_id in tenants:
                dispatch(client_id)
            root = Path(get_vendas_dependencies().paths.info_dir)
            for path in root.glob("*/" + FILENAME):
                if _STOP.is_set():
                    break
                if not path.is_symlink() and not path.parent.is_symlink() and not path.parent.is_junction():
                    dispatch(path.parent.name, bulk_only=True)
        except Exception:
            pass
        _BULK_WAKE.wait(2)


def start():
    global _BULK_THREAD
    _STOP.clear()
    with _LOCK:
        if _BULK_THREAD is None or not _BULK_THREAD.is_alive():
            _BULK_THREAD = threading.Thread(target=_bulk_loop, name="vendas-motivos-historico", daemon=True)
            _BULK_THREAD.start()


def stop():
    _STOP.set()
    wake()
    with _LOCK:
        _AUTH.clear()
        _BULK_AUTH.clear()
        _BULK_TURN.clear()
        _WAKE_TENANTS.clear()
    thread = _BULK_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2)
