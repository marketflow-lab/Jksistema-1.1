"""Durable, store-scoped return enrichment beside the commercial SQLite tables.

Connection-based functions leave commit/rollback to their caller. No raw API
response, observation, credential or exception message is stored here. The
dispatcher calls the resolver after committing the lease and releasing the lock.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import uuid

from backend.services.sqlite_coordination import sqlite_lock_for_path


REFRESH_SECONDS = 24 * 60 * 60
RECONNECT_SECONDS = 15 * 60
MAX_RETRY_SECONDS = 15 * 60
RULE_VERSION = 2
RESULT_STATUSES = frozenset({
    "identified", "not_linked", "not_informed", "ambiguous", "unavailable",
    "reconnect_required",
})
REFERENCE_IDS = frozenset({
    "order_id", "bling_order_id", "original_invoice_id", "claim_id", "return_id",
})
REFERENCE_NUMBERS = frozenset({"bling_order_number", "original_invoice_number"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_PERSONAL_OR_SECRET = re.compile(
    r"@|https?://|bearer\b|access[_ -]?token|refresh[_ -]?token|authorization"
    r"|\b\d(?:[\s()./+\-]*\d){7,}\b", re.IGNORECASE,
)


def _time(value=None):
    stamp = time.time() if value is None else float(value)
    if not math.isfinite(stamp):
        raise ValueError("Invalid queue timestamp")
    return stamp


def _id(value):
    text = str(value if value is not None else "").strip()
    return text if _IDENTIFIER.fullmatch(text) else ""


def _item_id(value):
    # Commercial item identities are opaque text; unlike provider IDs they may
    # contain spaces and accents. Preserve exact bytes/characters for SQL joins.
    text = str(value if value is not None else "")
    if not text.strip() or len(text) > 512 or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in text):
        return ""
    return text


def _description(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if _PERSONAL_OR_SECRET.search(text):
        return ""
    return text[:240]


def _references(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in sorted(REFERENCE_IDS):
        item = _id(value.get(key))
        if item:
            result[key] = item
    for key in sorted(REFERENCE_NUMBERS):
        item = str(value.get(key) or "").strip()
        if re.fullmatch(r"[0-9]{1,64}", item):
            result[key] = item
    reason = _description(value.get("explicit_bling_reason"))
    if reason:
        result["explicit_bling_reason"] = reason
    return result


def _begin(conn):
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def ensure_schema(conn):
    """Create only queue structures; never implicitly commit commercial work."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS devolucao_motivos (
            store_id TEXT NOT NULL,
            item_id_unico TEXT NOT NULL,
            nota_id TEXT NOT NULL,
            loja TEXT NOT NULL,
            sku TEXT NOT NULL,
            reference_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            rule_version INTEGER NOT NULL DEFAULT 1,
            source_kind TEXT NOT NULL DEFAULT 'canonical',
            source_fingerprint TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','processing','retry','done','obsolete')),
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL,
            lease_token TEXT,
            lease_until REAL,
            result_status TEXT,
            reason_id TEXT,
            reason_description TEXT,
            reason_source TEXT,
            order_id TEXT,
            claim_id TEXT,
            return_id TEXT,
            seller_id TEXT,
            site_id TEXT,
            error_code TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL,
            PRIMARY KEY (store_id, item_id_unico)
        )
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(devolucao_motivos)")}
    if "rule_version" not in columns:
        conn.execute("ALTER TABLE devolucao_motivos ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 1")
    if "source_kind" not in columns:
        conn.execute("ALTER TABLE devolucao_motivos ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'canonical'")
    if "source_fingerprint" not in columns:
        conn.execute("ALTER TABLE devolucao_motivos ADD COLUMN source_fingerprint TEXT")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_devolucao_motivos_due
        ON devolucao_motivos(store_id, status, next_attempt_at, lease_until)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_devolucao_motivos_note
        ON devolucao_motivos(store_id, nota_id)""")


def _store_id(value):
    result = _id(value)
    if not result:
        raise ValueError("A stable store_id is required")
    return result


def enqueue(conn, store_id, loja, payload_notas, payload_itens, *, references_by_note=None, now=None):
    """Enqueue persisted return items in the caller's commercial transaction.

    The tuple positions are the public sync_period insert shapes. Observed
    documents also obsolete queue entries for removed or non-return items.
    Repeating an unchanged item preserves its result, retry time and live lease.
    """
    store_id = _store_id(store_id)
    stamp = _time(now)
    _begin(conn)
    ensure_schema(conn)
    references_by_note = references_by_note if isinstance(references_by_note, dict) else {}
    notes = {}
    for row in payload_notas:
        if len(row) != 13:
            raise ValueError("Invalid return note tuple")
        note_id = _id(row[1])
        if note_id:
            notes[note_id] = int(row[6] or 0) == 1
    active = {note_id: set() for note_id in notes}
    jobs = []
    for row in payload_itens:
        if len(row) != 17:
            raise ValueError("Invalid return item tuple")
        item_id, note_id = _item_id(row[0]), _id(row[1])
        if not item_id or not note_id:
            raise ValueError("Return item requires stable document identifiers")
        active.setdefault(note_id, set())
        if int(row[12] or 0) != 1 or notes.get(note_id) is False:
            continue
        active[note_id].add(item_id)
        refs = _references(references_by_note.get(note_id))
        reference_json = json.dumps(refs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        sku = str(row[5] or "").strip()[:128]
        # Resolver revisions must not invalidate unchanged commercial evidence.
        material = [1, store_id, item_id, note_id, sku, row[3], row[4],
                    row[7], row[9], refs]
        fingerprint = hashlib.sha256(json.dumps(
            material, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
        ).encode("utf-8")).hexdigest()
        jobs.append((store_id, item_id, note_id, str(loja or "").strip()[:160],
                     sku, reference_json, fingerprint, RULE_VERSION, stamp, stamp, stamp))

    # Updating instead of replacing invalidates a lease without deleting history
    # or changing any of the commercial source tables.
    changed = 0
    for values in jobs:
        cursor = conn.execute("""
            INSERT INTO devolucao_motivos (
                store_id,item_id_unico,nota_id,loja,sku,reference_json,fingerprint,rule_version,
                next_attempt_at,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(store_id,item_id_unico) DO UPDATE SET
                nota_id=excluded.nota_id,loja=excluded.loja,sku=excluded.sku,
                reference_json=excluded.reference_json,fingerprint=excluded.fingerprint,rule_version=excluded.rule_version,
                source_kind='canonical',source_fingerprint=NULL,
                status='pending',attempts=0,next_attempt_at=excluded.next_attempt_at,
                lease_token=NULL,lease_until=NULL,result_status=NULL,reason_id=NULL,
                reason_description=NULL,reason_source=NULL,order_id=NULL,claim_id=NULL,
                return_id=NULL,seller_id=NULL,site_id=NULL,error_code=NULL,
                completed_at=NULL,updated_at=excluded.updated_at
            WHERE devolucao_motivos.fingerprint != excluded.fingerprint
               OR devolucao_motivos.status = 'obsolete'
        """, values)
        changed += cursor.rowcount
    for note_id, item_ids in active.items():
        rows = conn.execute(
            "SELECT item_id_unico FROM devolucao_motivos WHERE store_id=? AND nota_id=? AND status!='obsolete'",
            (store_id, note_id),
        ).fetchall()
        for row in rows:
            if row[0] not in item_ids:
                _obsolete(conn, store_id, row[0], stamp)
    return changed


def _obsolete(conn, store_id, item_id, stamp):
    conn.execute("""UPDATE devolucao_motivos SET status='obsolete',lease_token=NULL,
        lease_until=NULL,next_attempt_at=NULL,updated_at=?
        WHERE store_id=? AND item_id_unico=?""", (stamp, store_id, item_id))


def _legacy_source(conn, item_id, owner_resolver, store_id):
    if owner_resolver is None:
        return None
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notas_entrada_itens)")}
    if not {"id_unico", "id_nota", "sku", "devolucao", "loja_conta"} <= columns:
        return None
    fields = [name for name in ("id_unico", "id_nota", "store_id", "sku", "devolucao", "loja_conta",
                               "data_emissao", "numero_nota", "quantidade", "valor_unitario", "valor_total") if name in columns]
    owner_clause = " AND (store_id IS NULL OR trim(store_id)='')" if "store_id" in columns else ""
    rows = conn.execute("SELECT " + ",".join(fields) + " FROM notas_entrada_itens WHERE id_unico=? AND devolucao=1" + owner_clause,
                        (item_id,)).fetchall()
    if len(rows) != 1:
        return None
    source = dict(zip(fields, rows[0]))
    if owner_resolver(source) != store_id:
        return None
    digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), default=str).encode()).hexdigest()
    return source, digest


def _legacy_matches(conn, job, owner_resolver):
    source = _legacy_source(conn, job["item_id_unico"], owner_resolver, job["store_id"])
    return bool(source and source[1] == job.get("source_fingerprint")
                and _id(source[0]["id_nota"]) == job.get("nota_id")
                and str(source[0]["sku"] or "").strip() == job.get("sku"))


def _live_item(conn, store_id, item_id, job=None, legacy_owner=None):
    if job and job.get("source_kind") == "legacy":
        return _legacy_matches(conn, job, legacy_owner)
    return conn.execute("""SELECT 1 FROM notas_entrada_itens
        WHERE store_id=? AND id_unico=? AND devolucao=1 LIMIT 1""", (store_id, item_id)).fetchone() is not None


def _job(cursor, row):
    result = dict(zip((column[0] for column in cursor.description), row))
    refs = _references(json.loads(result.pop("reference_json")))
    result["references"] = refs
    result.update(refs)
    # A direct order reference is still available after a previous result used
    # a different order_id, since result columns and refs have distinct origins.
    return result


def claim(conn, store_id, now=None, lease_seconds=120, *, item_ids=None, legacy_owner=None):
    """Lease one due item; expired leases and 24-hour refreshes are resumable."""
    store_id = _store_id(store_id)
    stamp = _time(now)
    duration = min(3600, max(1, int(lease_seconds)))
    selected = None if item_ids is None else sorted({_item_id(value) for value in item_ids if _item_id(value)})[:100]
    if selected == []:
        return None
    clause = "" if selected is None else " AND item_id_unico IN (" + ",".join("?" for _ in selected) + ")"
    _begin(conn)
    ensure_schema(conn)
    while True:
        cursor = conn.execute("""SELECT * FROM devolucao_motivos
            WHERE store_id=? AND (
                (status IN ('pending','retry','done') AND next_attempt_at<=?)
                OR (status='processing' AND lease_until<=?)
            ) """ + clause + " ORDER BY next_attempt_at,created_at,item_id_unico LIMIT 1", (store_id, stamp, stamp, *(selected or [])))
        row = cursor.fetchone()
        if row is None:
            return None
        job = _job(cursor, row)
        if not _live_item(conn, store_id, job["item_id_unico"], job, legacy_owner):
            _obsolete(conn, store_id, job["item_id_unico"], stamp)
            continue
        token = uuid.uuid4().hex
        cursor = conn.execute("""UPDATE devolucao_motivos
            SET status='processing',lease_token=?,lease_until=?,attempts=attempts+1,updated_at=?,rule_version=?
            WHERE store_id=? AND item_id_unico=? AND fingerprint=? AND (
                (status IN ('pending','retry','done') AND next_attempt_at<=?)
                OR (status='processing' AND lease_until<=?)
            )""", (token, stamp + duration, stamp, RULE_VERSION, store_id, job["item_id_unico"],
                    job["fingerprint"], stamp, stamp))
        if cursor.rowcount:
            job.update(status="processing", lease_token=token, lease_until=stamp + duration,
                       attempts=job["attempts"] + 1, updated_at=stamp, rule_version=RULE_VERSION)
            return job


def _result(value):
    if not isinstance(value, dict) or value.get("result_status") not in RESULT_STATUSES:
        return {"result_status": "unavailable", "error_code": "invalid_result", "retryable": True}
    result = {"result_status": value["result_status"], "retryable": value.get("retryable") is True}
    for key in ("reason_id", "order_id", "claim_id", "return_id", "seller_id", "site_id", "error_code"):
        result[key] = _id(value.get(key)) or None
    result["reason_description"] = _description(value.get("reason_description")) or None
    result["reason_source"] = value.get("reason_source") if value.get("reason_source") in {"bling", "mercado_livre"} else None
    delay = value.get("retry_after_seconds")
    if isinstance(delay, (float, int)) and not isinstance(delay, bool) and math.isfinite(delay):
        result["retry_after_seconds"] = max(30, min(MAX_RETRY_SECONDS, float(delay)))
    if result["result_status"] == "identified" and not (result["reason_id"] or result["reason_description"]):
        result["result_status"] = "not_informed"
    return result


def finish(conn, claim, result, now=None, *, legacy_owner=None):
    """Accept only the current live lease and evidence; leave commit to caller."""
    stamp = _time(now)
    store_id = _store_id(claim.get("store_id"))
    item_id = _item_id(claim.get("item_id_unico"))
    _begin(conn)
    ensure_schema(conn)
    cursor = conn.execute("""SELECT * FROM devolucao_motivos WHERE store_id=? AND item_id_unico=?
        AND status='processing' AND fingerprint=? AND lease_token=? AND lease_until>?""",
        (store_id, item_id, claim.get("fingerprint"), claim.get("lease_token"), stamp))
    row = cursor.fetchone()
    if row is None:
        return False
    old = dict(zip((column[0] for column in cursor.description), row))
    if not _live_item(conn, store_id, item_id, old, legacy_owner):
        _obsolete(conn, store_id, item_id, stamp)
        return False
    sanitized = _result(result)
    result_status = sanitized["result_status"]
    transient = result_status == "unavailable" and sanitized.get("retryable", False)
    retry = transient or result_status == "reconnect_required"
    if result_status == "reconnect_required":
        delay = RECONNECT_SECONDS
    elif transient:
        delay = min(MAX_RETRY_SECONDS, 30 * (2 ** min(5, max(0, old["attempts"] - 1))))
        delay = max(delay, sanitized.get("retry_after_seconds", 0))
    else:
        delay = REFRESH_SECONDS
    # A failed refresh does not discard an earlier identified reason. New source
    # evidence already clears it through enqueue's fingerprint comparison.
    preserve = result_status in {"unavailable", "reconnect_required"} and old["result_status"] == "identified"
    fields = ("result_status", "reason_id", "reason_description", "reason_source",
              "order_id", "claim_id", "return_id", "seller_id", "site_id")
    values = [old[field] if preserve else sanitized.get(field) for field in fields]
    cursor = conn.execute("""UPDATE devolucao_motivos SET status=?,next_attempt_at=?,
        lease_token=NULL,lease_until=NULL,attempts=?,result_status=?,reason_id=?,
        reason_description=?,reason_source=?,order_id=?,claim_id=?,return_id=?,
        seller_id=?,site_id=?,error_code=?,completed_at=?,updated_at=?
        WHERE store_id=? AND item_id_unico=? AND status='processing'
          AND fingerprint=? AND lease_token=? AND lease_until>?""",
        ("retry" if retry else "done", stamp + delay, old["attempts"] if retry else 0,
         *values, sanitized.get("error_code"), old["completed_at"] if preserve else stamp,
         stamp, store_id, item_id, claim.get("fingerprint"), claim.get("lease_token"), stamp))
    return cursor.rowcount == 1


def process_due(db_path, store_id, resolver, limit=10, *, item_ids=None, legacy_owner=None):
    """Resolve bounded work outside SQLite transactions and local DB locks."""
    store_id = _store_id(store_id)
    path = Path(db_path).resolve(strict=True)
    summary = {"claimed": 0, "finished": 0, "identified": 0, "retry": 0, "stale": 0}
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=15)
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        for _ in range(max(0, min(100, int(limit)))):
            with sqlite_lock_for_path(path), conn:
                job = claim(conn, store_id, item_ids=item_ids, legacy_owner=legacy_owner)
            if job is None:
                break
            summary["claimed"] += 1
            try:
                result = resolver(job)
            except Exception:
                result = {"result_status": "unavailable", "retryable": True, "error_code": "resolver_exception"}
            with sqlite_lock_for_path(path), conn:
                accepted = finish(conn, job, result, legacy_owner=legacy_owner)
                state = conn.execute("""SELECT status,result_status FROM devolucao_motivos
                    WHERE store_id=? AND item_id_unico=?""", (store_id, job["item_id_unico"])).fetchone()
            if not accepted:
                summary["stale"] += 1
            else:
                summary["finished"] += 1
                summary["identified"] += int(state[1] == "identified")
                summary["retry"] += int(state[0] == "retry")
    finally:
        conn.close()
    return summary


def prepare_saved_item(conn, store_id, loja, item_id, nota_id, sku, *, requested_at=None, now=None):
    """Explicit history request: enqueue one exact saved identity, never a display number.

    No business row is written. Identified results and active leases survive
    repeated clicks. Callers bound their batches and own commit/rollback.
    """
    store_id = _store_id(store_id)
    item_id, nota_id = _item_id(item_id), _id(nota_id)
    sku = str(sku or "").strip()[:128]
    if not item_id or not nota_id or not sku:
        return False
    stamp = _time(now)
    _begin(conn)
    ensure_schema(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notas_entrada_itens)")}
    if not {"store_id", "id_unico", "id_nota", "sku", "devolucao"} <= columns:
        return False
    source = conn.execute("""SELECT id_nota,sku FROM notas_entrada_itens
        WHERE store_id=? AND id_unico=? AND devolucao=1""", (store_id,item_id)).fetchall()
    if len(source) != 1 or str(source[0][0] or "").strip() != nota_id or str(source[0][1] or "").strip() != sku:
        return False
    fingerprint = hashlib.sha256(json.dumps([RULE_VERSION,store_id,item_id,nota_id,sku],
        ensure_ascii=False,separators=(",", ":")).encode()).hexdigest()
    conn.execute("""INSERT INTO devolucao_motivos
        (store_id,item_id_unico,nota_id,loja,sku,reference_json,fingerprint,rule_version,
         next_attempt_at,created_at,updated_at)
        VALUES (?,?,?,?,?,'{}',?,?,?,?,?) ON CONFLICT(store_id,item_id_unico) DO NOTHING""",
        (store_id,item_id,nota_id,str(loja or "").strip()[:160],sku,fingerprint,RULE_VERSION,stamp,stamp,stamp))
    cutoff = stamp if requested_at is None else _time(requested_at)
    # Explicit reprocessing changes only availability; never steals a live lease
    # or overwrites existing documentary links/evidence.
    conn.execute("""UPDATE devolucao_motivos SET status='pending',next_attempt_at=?,updated_at=?,
        lease_token=NULL,lease_until=NULL WHERE store_id=? AND item_id_unico=?
        AND nota_id=? AND sku=? AND coalesce(result_status,'')!='identified'
        AND (status='obsolete' OR (status IN ('done','retry')
            AND (completed_at IS NULL OR completed_at<?)))""",
        (stamp,stamp,store_id,item_id,nota_id,sku,cutoff))
    return True


def prepare_legacy_saved_item(conn, store_id, loja, item_id, nota_id, sku, owner_resolver, *, requested_at=None, now=None):
    """Queue a legacy row only with current canonical owner proof and source hash."""
    store_id = _store_id(store_id)
    item_id, nota_id = _item_id(item_id), _id(nota_id)
    sku = str(sku or "").strip()[:128]
    if not item_id or not nota_id or not sku:
        return False
    stamp = _time(now)
    cutoff = stamp if requested_at is None else _time(requested_at)
    _begin(conn)
    ensure_schema(conn)
    source = _legacy_source(conn, item_id, owner_resolver, store_id)
    if not source or _id(source[0]["id_nota"]) != nota_id or str(source[0]["sku"] or "").strip() != sku:
        return False
    digest = source[1]
    fingerprint = hashlib.sha256(json.dumps([1, store_id, item_id, nota_id, sku, "legacy", digest],
                                           ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    conn.execute("""INSERT INTO devolucao_motivos
        (store_id,item_id_unico,nota_id,loja,sku,reference_json,fingerprint,rule_version,
         source_kind,source_fingerprint,next_attempt_at,created_at,updated_at)
        VALUES (?,?,?,?,?,'{}',?,?,'legacy',?,?,?,?)
        ON CONFLICT(store_id,item_id_unico) DO UPDATE SET
            nota_id=excluded.nota_id,loja=excluded.loja,sku=excluded.sku,
            source_fingerprint=excluded.source_fingerprint,fingerprint=excluded.fingerprint,
            reference_json='{}',rule_version=excluded.rule_version,
            status='pending',attempts=0,next_attempt_at=excluded.next_attempt_at,
            lease_token=NULL,lease_until=NULL,result_status=NULL,reason_id=NULL,
            reason_description=NULL,reason_source=NULL,order_id=NULL,claim_id=NULL,
            return_id=NULL,seller_id=NULL,site_id=NULL,error_code=NULL,
            completed_at=NULL,updated_at=excluded.updated_at
        WHERE devolucao_motivos.source_kind='legacy' AND
            (devolucao_motivos.source_fingerprint!=excluded.source_fingerprint OR devolucao_motivos.status='obsolete')""",
        (store_id, item_id, nota_id, str(loja or "").strip()[:160], sku, fingerprint,
         RULE_VERSION, digest, stamp, stamp, stamp))
    row = conn.execute("SELECT source_kind FROM devolucao_motivos WHERE store_id=? AND item_id_unico=?", (store_id, item_id)).fetchone()
    if row[0] != "legacy":
        return False
    conn.execute("""UPDATE devolucao_motivos SET status='pending',next_attempt_at=?,updated_at=?,lease_token=NULL,lease_until=NULL
        WHERE store_id=? AND item_id_unico=? AND source_kind='legacy'
            AND coalesce(result_status,'')!='identified' AND status IN ('done','retry')
            AND (completed_at IS NULL OR completed_at<?)""", (stamp, stamp, store_id, item_id, cutoff))
    return True
