"""Explicit, durable history searches for saved return reasons.

HTTP callers record an operation. Workers scan bounded local batches and resolve
exact source identities outside SQLite locks. Authorization stays in memory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from backend.services.central_accounts_client import current
from backend.services.store_read_service import read_stores
from backend.services.sqlite_coordination import sqlite_lock_for_path
from .compositions import load_composition_catalog
from .dependencies import get_tenant_path
from .errors import VendasDomainError
from .legacy import _listar_bancos_vendas_tenant
from .performance import open_vendas_readonly
from . import return_reason_queue as queue

FILENAME = "devolucao_motivos_busca.db"
SCAN_LIMIT = 200
RESOLVE_LIMIT = 5
OPERATION_LEASE_SECONDS = 180
COUNTS = ("total", "processed", "identified", "pending", "retry", "unlinked",
          "unavailable", "ambiguous", "reconnect_required", "skipped", "not_informed")


def _root(client_id):
    return Path(get_tenant_path(client_id)).resolve()


def _path(client_id):
    root = _root(client_id)
    path = root / FILENAME
    if path.is_symlink() or path.is_junction() or path.resolve().parent != root:
        raise VendasDomainError(409, "Armazenamento da busca indisponível.")
    return path


def _stores(client_id):
    result = {}
    for row in read_stores(client_id):
        store_id = queue._id(row.get("store_id"))
        if store_id:
            result[store_id] = str(row.get("nome") or "").strip()[:160]
    return result


def _scope(stores):
    return hashlib.sha256(json.dumps(sorted(stores), separators=(",", ":")).encode()).hexdigest()


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS operations (
        scope TEXT PRIMARY KEY, generation TEXT NOT NULL, phase TEXT NOT NULL,
        requires_session INTEGER NOT NULL, requested_at REAL NOT NULL, updated_at REAL NOT NULL,
        lease_token TEXT, lease_until REAL, error TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sources (
        scope TEXT NOT NULL, store_id TEXT NOT NULL, filename TEXT NOT NULL,
        cursor INTEGER NOT NULL DEFAULT 0, highwater INTEGER,
        complete INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(scope,store_id,filename))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS members (
        scope TEXT NOT NULL, store_id TEXT NOT NULL, item_id TEXT NOT NULL,
        nota_id TEXT NOT NULL, sku TEXT NOT NULL, filename TEXT NOT NULL,
        source_kind TEXT NOT NULL DEFAULT 'canonical',
        processed INTEGER NOT NULL DEFAULT 0, result_status TEXT,
        queue_status TEXT, checked_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(scope,store_id,item_id))""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
    if "source_kind" not in columns:
        conn.execute("ALTER TABLE members ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'canonical'")
    conn.execute("""CREATE TABLE IF NOT EXISTS skipped_members (
        scope TEXT NOT NULL, store_id TEXT NOT NULL, item_id TEXT NOT NULL,
        PRIMARY KEY(scope,store_id,item_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS reason_search_pending ON members(scope,processed,checked_at)")


def _source_path(client_id, filename):
    if Path(filename).name != filename or not filename.endswith(".db"):
        return None
    root = _root(client_id)
    path = root / filename
    if not path.is_file() or path.is_symlink() or path.is_junction() or path.resolve().parent != root:
        return None
    return path


def _source_names(client_id):
    root = _root(client_id)
    names = []
    for filename in _listar_bancos_vendas_tenant(client_id):
        path = Path(filename)
        if (path.is_file() and not path.is_symlink() and not path.is_junction()
                and path.resolve().parent == root and path.name.endswith(".db")):
            names.append(path.name)
    return sorted(set(names))


def _idle():
    return {"phase": "idle", "running": False, **{key: 0 for key in COUNTS}, "error": None}


def _status(conn, scope):
    conn.row_factory = sqlite3.Row
    op = conn.execute("SELECT * FROM operations WHERE scope=?", (scope,)).fetchone()
    if op is None:
        return _idle()
    skipped = conn.execute("""SELECT COUNT(*) FROM skipped_members s WHERE scope=?
        AND NOT EXISTS (SELECT 1 FROM members m WHERE m.scope=s.scope
            AND m.store_id=s.store_id AND m.item_id=s.item_id)""", (scope,)).fetchone()[0]
    rows = conn.execute("""SELECT processed,result_status,queue_status,COUNT(*) AS amount
        FROM members WHERE scope=? GROUP BY processed,result_status,queue_status""", (scope,)).fetchall()
    result = _idle()
    result.update(phase=op["phase"], error=op["error"], skipped=skipped, total=skipped, processed=skipped)
    for row in rows:
        amount = row["amount"]
        result["total"] += amount
        result["processed"] += amount if row["processed"] else 0
        key = "unlinked" if row["result_status"] == "not_linked" else row["result_status"]
        if key in {"identified", "unlinked", "unavailable", "ambiguous", "reconnect_required", "skipped", "not_informed"}:
            result[key] += amount
        if row["queue_status"] == "retry" and not row["processed"]:
            result["retry"] += amount
    result["pending"] = result["total"] - result["processed"]
    result["running"] = op["phase"] in {"preparing", "processing"}
    return result


def _remember(client_id, scope):
    from .return_reason_worker import remember_bulk_authorization, wake
    remember_bulk_authorization(client_id, scope)
    wake()


def start_search(client_id):
    stores = _stores(client_id)
    if not stores:
        raise VendasDomainError(409, "Nenhuma loja autorizada com identificação estável disponível.")
    scope = _scope(stores)
    names = _source_names(client_id)
    path = _path(client_id)
    stamp = time.time()
    with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        op = conn.execute("SELECT phase FROM operations WHERE scope=?", (scope,)).fetchone()
        if op is None or op[0] not in {"preparing", "processing"}:
            for table in ("sources", "members", "skipped_members"):
                conn.execute("DELETE FROM " + table + " WHERE scope=?", (scope,))
            conn.execute("""INSERT INTO operations(scope,generation,phase,requires_session,requested_at,updated_at)
                VALUES (?,?,'preparing',?,?,?) ON CONFLICT(scope) DO UPDATE SET
                generation=excluded.generation,phase=excluded.phase,requires_session=excluded.requires_session,
                requested_at=excluded.requested_at,updated_at=excluded.updated_at,
                lease_token=NULL,lease_until=NULL,error=NULL""",
                (scope, uuid.uuid4().hex, int(current(client_id) is not None), stamp, stamp))
            # Legacy rows require a separate bounded pass and canonical owner
            # proof. Their commercial store_id is never backfilled.
            conn.executemany("INSERT INTO sources(scope,store_id,filename) VALUES (?,?,?)",
                [(scope, store_id, name) for store_id in ["", *sorted(stores)] for name in names])
        result = _status(conn, scope)
    _remember(client_id, scope)
    return result


def search_status(client_id):
    stores = _stores(client_id)
    scope = _scope(stores)
    path = _path(client_id)
    if not path.exists():
        return _idle()
    with open_vendas_readonly(str(path)) as conn:
        result = _status(conn, scope)
    if result["running"]:
        _remember(client_id, scope)
    return result


def pending_operations(client_id):
    path = _path(client_id)
    if not path.exists():
        return []
    with open_vendas_readonly(str(path)) as conn:
        return [(row[0], bool(row[1])) for row in conn.execute("""SELECT scope,requires_session
            FROM operations WHERE phase IN ('preparing','processing') ORDER BY requested_at""")]


def _reason(conn, store_id, item_id):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(devolucao_motivos)")}
    fields = ("status", "result_status", "reason_id", "reason_description", "completed_at")
    if not {"store_id", "item_id_unico", *fields} <= columns:
        return {}
    optional = ("source_kind", "source_fingerprint", "nota_id", "sku")
    selected = [*fields, *(name if name in columns else "NULL AS " + name for name in optional)]
    row = conn.execute("SELECT " + ",".join(selected) + " FROM devolucao_motivos WHERE store_id=? AND item_id_unico=?",
                       (store_id, item_id)).fetchone()
    return {} if row is None or row[0] == "obsolete" else dict(zip((*fields, *optional), row))


def legacy_owner_resolver(client_id):
    def resolve(source):
        # Re-read authorization and current alias proof at both claim and finish.
        allowed = _stores(client_id)
        owner = load_composition_catalog(client_id)._row_store(source, None)
        return owner if owner in allowed else None
    return resolve


def _trusted_reason(conn, store_id, item_id, source_kind, owner_resolver):
    reason = _reason(conn, store_id, item_id)
    if source_kind == "legacy" and reason.get("source_kind") != "legacy":
        return {}
    if reason.get("source_kind") == "legacy" and not queue._legacy_matches(conn,
            {**reason, "store_id": store_id, "item_id_unico": item_id}, owner_resolver):
        return {}
    return reason


def _identified(reason):
    return reason.get("result_status") == "identified" and bool(reason.get("reason_id") or reason.get("reason_description"))


def _processed(reason, requested_at):
    if _identified(reason):
        return True
    recent = reason.get("completed_at") is not None and reason["completed_at"] >= requested_at
    # Rate limits/timeouts remain pending until the persisted retry completes.
    return bool(recent and (reason.get("status") == "done" or reason.get("result_status") == "reconnect_required"))


def _skip(conn, scope, store_id, item_id, source, rowid):
    key = item_id or "row:" + hashlib.sha256((source + ":" + str(rowid)).encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO skipped_members VALUES (?,?,?)", (scope, store_id, key))


def _scan(client_id, conn, scope, source, allowed):
    path = _source_path(client_id, source["filename"])
    rows, highwater, complete = [], source["highwater"], True
    if path is not None:
        with open_vendas_readonly(str(path)) as commercial:
            columns = {row[1] for row in commercial.execute("PRAGMA table_info(notas_entrada_itens)")}
            legacy = not source["store_id"]
            if "devolucao" in columns and (legacy or source["store_id"] in allowed) and (legacy or "store_id" in columns):
                if highwater is None:
                    highwater = commercial.execute("SELECT coalesce(MAX(rowid),0) FROM notas_entrada_itens").fetchone()[0]
                selected = [name if name in columns else "NULL AS " + name for name in ("id_unico", "id_nota", "sku", "loja_conta")]
                owner_clause = ("(store_id IS NULL OR trim(store_id)='')" if "store_id" in columns else "1") if legacy else "store_id=?"
                args = [] if legacy else [source["store_id"]]
                rows = commercial.execute("SELECT rowid," + ",".join(selected) + " FROM notas_entrada_itens WHERE " + owner_clause +
                    " AND devolucao=1 AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                    [*args, source["cursor"], highwater, SCAN_LIMIT]).fetchall()
                complete = len(rows) < SCAN_LIMIT
                catalog = load_composition_catalog(client_id) if legacy and rows else None
                for row in rows:
                    item_id, nota_id = queue._item_id(row[1]), queue._id(row[2])
                    sku = str(row[3] or "").strip()[:128]
                    store_id = source["store_id"]
                    if legacy:
                        store_id = catalog._row_store({"loja_conta": row[4]}, None) if row[4] else None
                        if store_id not in allowed:
                            continue
                    if not item_id or not nota_id or not sku:
                        _skip(conn, scope, store_id, item_id, source["filename"], row[0])
                        continue
                    kind = "legacy" if legacy else "canonical"
                    owner_resolver = legacy_owner_resolver(client_id)
                    reason = _trusted_reason(commercial, store_id, item_id, kind, owner_resolver)
                    identified = _identified(reason)
                    old = conn.execute("SELECT nota_id,sku FROM members WHERE scope=? AND store_id=? AND item_id=?",
                        (scope, store_id, item_id)).fetchone()
                    if old is not None and (old["nota_id"], old["sku"]) != (nota_id, sku):
                        conn.execute("UPDATE members SET processed=1,result_status='ambiguous' WHERE scope=? AND store_id=? AND item_id=?",
                                     (scope, store_id, item_id))
                        continue
                    conn.execute("""INSERT INTO members(scope,store_id,item_id,nota_id,sku,filename,source_kind,processed,result_status,queue_status)
                        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,store_id,item_id) DO UPDATE SET
                        filename=excluded.filename,source_kind=excluded.source_kind,processed=excluded.processed,
                        result_status=excluded.result_status,queue_status=excluded.queue_status
                        WHERE coalesce(members.result_status,'')!='ambiguous' AND
                            (excluded.result_status='identified' OR (members.source_kind='legacy'
                             AND excluded.source_kind='canonical' AND coalesce(members.result_status,'')!='identified'))""",
                        (scope, store_id, item_id, nota_id, sku, source["filename"], kind, int(identified),
                         "identified" if identified else None, reason.get("status")))
    cursor = rows[-1][0] if rows else source["cursor"]
    conn.execute("UPDATE sources SET cursor=?,highwater=?,complete=? WHERE scope=? AND store_id=? AND filename=?",
                 (cursor, highwater, int(complete), scope, source["store_id"], source["filename"]))


def _owned(conn, scope, operation, token, *, renew=False):
    stamp = time.time()
    row = conn.execute("""SELECT 1 FROM operations WHERE scope=? AND generation=? AND lease_token=?
        AND lease_until>? AND phase IN ('preparing','processing')""",
        (scope, operation["generation"], token, stamp)).fetchone()
    if row is not None and renew:
        conn.execute("UPDATE operations SET lease_until=? WHERE scope=? AND lease_token=?",
                     (stamp + OPERATION_LEASE_SECONDS, scope, token))
    return row is not None


def _save_result(conn, scope, member, reason, processed):
    conn.execute("""UPDATE members SET processed=?,result_status=?,queue_status=?,checked_at=?
        WHERE scope=? AND store_id=? AND item_id=?""", (int(processed), reason.get("result_status"),
        reason.get("status"), time.time(), scope, member["store_id"], member["item_id"]))


def advance_search(client_id, scope, resolver):
    """One bounded durable turn, with at most five exact source resolutions."""
    path = _path(client_id)
    if not path.exists():
        return
    allowed = _stores(client_id)
    if _scope(allowed) != scope:
        return
    with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        operation = conn.execute("SELECT * FROM operations WHERE scope=?", (scope,)).fetchone()
        if operation is None or operation["phase"] not in {"preparing", "processing"}:
            return
        if operation["requires_session"] and current(client_id) is None:
            return
        stamp = time.time()
        if (operation["lease_until"] or 0) > stamp:
            return
        token = uuid.uuid4().hex
        conn.execute("UPDATE operations SET lease_token=?,lease_until=? WHERE scope=?",
                     (token, stamp + OPERATION_LEASE_SECONDS, scope))
    try:
        with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            if not _owned(conn, scope, operation, token, renew=True):
                return
            source = conn.execute("SELECT * FROM sources WHERE scope=? AND complete=0 ORDER BY filename,store_id LIMIT 1", (scope,)).fetchone()
            if source is not None:
                _scan(client_id, conn, scope, source, allowed)
                conn.execute("UPDATE operations SET updated_at=?,error=NULL WHERE scope=?", (time.time(), scope))
                return
            conn.execute("UPDATE operations SET phase='processing' WHERE scope=?", (scope,))
            members = conn.execute("SELECT * FROM members WHERE scope=? AND processed=0 ORDER BY checked_at,store_id,item_id LIMIT ?",
                                   (scope, RESOLVE_LIMIT)).fetchall()
        owner_resolver = legacy_owner_resolver(client_id)
        for member in members:
            with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if not _owned(conn, scope, operation, token, renew=True):
                    return
            source_path = _source_path(client_id, member["filename"])
            if source_path is None or member["store_id"] not in allowed:
                reason, processed = {"result_status": "skipped"}, True
            else:
                with open_vendas_readonly(str(source_path)) as commercial:
                    reason = _trusted_reason(commercial, member["store_id"], member["item_id"], member["source_kind"], owner_resolver)
                processed = _processed(reason, operation["requested_at"])
                if not processed:
                    with sqlite_lock_for_path(source_path), sqlite3.connect(source_path, timeout=5) as commercial:
                        prepare = queue.prepare_legacy_saved_item if member["source_kind"] == "legacy" else queue.prepare_saved_item
                        options = {"owner_resolver": owner_resolver} if member["source_kind"] == "legacy" else {}
                        prepared = prepare(commercial, member["store_id"], allowed[member["store_id"]],
                            member["item_id"], member["nota_id"], member["sku"], requested_at=operation["requested_at"], **options)
                    if prepared:
                        queue.process_due(source_path, member["store_id"], resolver, limit=1, item_ids=[member["item_id"]], legacy_owner=owner_resolver)
                        with open_vendas_readonly(str(source_path)) as commercial:
                            reason = _trusted_reason(commercial, member["store_id"], member["item_id"], member["source_kind"], owner_resolver)
                        processed = _processed(reason, operation["requested_at"])
                    else:
                        reason, processed = {"result_status": "skipped"}, True
            with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if not _owned(conn, scope, operation, token):
                    return
                _save_result(conn, scope, member, reason, processed)
        with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not _owned(conn, scope, operation, token):
                return
            state = _status(conn, scope)
            phase = "processing" if state["pending"] else "blocked" if state["reconnect_required"] else "completed"
            conn.execute("UPDATE operations SET phase=?,updated_at=?,error=NULL WHERE scope=?", (phase, time.time(), scope))
    except Exception:
        with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
            conn.execute("UPDATE operations SET error='search_retry_pending',updated_at=? WHERE scope=? AND lease_token=?",
                         (time.time(), scope, token))
        raise
    finally:
        with sqlite_lock_for_path(path), sqlite3.connect(path, timeout=5) as conn:
            conn.execute("UPDATE operations SET lease_token=NULL,lease_until=NULL WHERE scope=? AND lease_token=?", (scope, token))
