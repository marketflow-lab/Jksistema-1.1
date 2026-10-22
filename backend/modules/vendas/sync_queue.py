"""Local, tenant-scoped durable work queue. This module never calls a data source.

Day acknowledgements are written only after the caller has committed the entire
day. Replaying an expired lease must therefore remain idempotent in the caller.
Closed historical days can be reused across cycles for the same store, while
recent days and explicit historical reviews continue to consult the source.
Only counts, dates and queue metadata are stored; callers sanitize error messages.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


INTERVAL_SECONDS = 15 * 60
HISTORY_INTERVAL_SECONDS = 24 * 60 * 60
RECENT_WINDOW_DAYS = 7
try:
    SYNC_TIMEZONE = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    SYNC_TIMEZONE = dt.timezone(dt.timedelta(hours=-3))


def _time(value=None):
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=value.tzinfo or dt.timezone.utc).timestamp()
    return time.time() if value is None else float(value)


def _date(value):
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("Data invalida. Use YYYY-MM-DD.")
    return dt.date.fromisoformat(text)


def _iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat() if value is not None else None


def _checkpoint(value):
    if not isinstance(value, dict):
        return {}
    output = {}
    count = value.get("documentos_salvos")
    if isinstance(count, int) and count >= 0:
        output["documentos_salvos"] = count
    for key in ("ultimo_tipo", "ultimo_id"):
        item = str(value.get(key) or "")
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item):
            output[key] = item
    try:
        timestamp = dt.datetime.fromisoformat(str(value.get("atualizado_em", "")))
        output["atualizado_em"] = timestamp.isoformat()
    except ValueError:
        pass
    return output


class SyncQueue:
    def __init__(self, client_id, *, tenant_path=None):
        self.client_id = str(client_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.client_id):
            raise ValueError("Cliente invalido para a fila de sincronizacao.")
        if tenant_path is None:
            from .dependencies import get_tenant_path
            tenant_path = get_tenant_path(self.client_id)
        root = Path(tenant_path)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "vendas_sync_queue.db"
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tenant (client_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, signature TEXT NOT NULL, store_id TEXT NOT NULL,
                    loja TEXT NOT NULL, data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
                    forcar_resync INTEGER NOT NULL, automatico INTEGER NOT NULL,
                    kind TEXT NOT NULL, requires_session INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL,
                    last_error TEXT, owner TEXT, lease_until REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL
                );
                CREATE INDEX IF NOT EXISTS jobs_store_status ON jobs(store_id, status);
                CREATE INDEX IF NOT EXISTS jobs_store_created ON jobs(store_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_pending_signature
                    ON jobs(signature) WHERE status != 'complete';
                CREATE TABLE IF NOT EXISTS connection_alert_ack (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id),
                    error_signature TEXT NOT NULL, verified_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS days (
                    job_id TEXT NOT NULL REFERENCES jobs(id), day TEXT NOT NULL,
                    result TEXT NOT NULL, completed_at REAL NOT NULL,
                    PRIMARY KEY(job_id, day)
                );
                CREATE TABLE IF NOT EXISTS plans (
                    store_id TEXT PRIMARY KEY, loja TEXT NOT NULL, enabled INTEGER NOT NULL,
                    data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
                    forcar_resync INTEGER NOT NULL, requires_session INTEGER NOT NULL DEFAULT 0,
                    next_due REAL NOT NULL,
                    last_success REAL, last_success_day TEXT, history_cursor TEXT NOT NULL,
                    history_next_due REAL NOT NULL
                );
            """)
            conn.execute("BEGIN IMMEDIATE")
            tenant = conn.execute("SELECT client_id FROM tenant").fetchone()
            if tenant is not None and tenant[0] != self.client_id:
                raise ValueError("A fila pertence a outro cliente.")
            if tenant is None:
                conn.execute("INSERT INTO tenant VALUES (?)", (self.client_id,))
            conn.execute("""CREATE TABLE IF NOT EXISTS connection_check_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                requested_generation INTEGER NOT NULL DEFAULT 0,
                completed_generation INTEGER NOT NULL DEFAULT 0,
                claimed_generation INTEGER NOT NULL DEFAULT 0,
                requires_session INTEGER NOT NULL DEFAULT 0,
                owner TEXT, lease_until REAL
            )""")
            if conn.execute("SELECT 1 FROM connection_check_state").fetchone() is None:
                pending = conn.execute("""SELECT 1 FROM jobs
                    WHERE status NOT IN ('complete', 'paused') LIMIT 1""").fetchone()
                generation = int(bool(pending) and self._has_connection_alerts(conn))
                requires_session = bool(generation and conn.execute("""SELECT 1 FROM jobs
                    WHERE status != 'complete' AND requires_session = 1
                    AND (status != 'paused' OR last_error IS NOT NULL) LIMIT 1""").fetchone())
                conn.execute("""INSERT INTO connection_check_state
                    (singleton, requested_generation, requires_session) VALUES (1, ?, ?)""",
                             (generation, int(requires_session)))
            conn.commit()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()

    def _job(self, conn, job_id, detailed=True):
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Sincronizacao nao encontrada.")
        job = dict(row)
        job["job_id"] = job["cycle_id"] = job["id"]
        job["forcar_resync"] = bool(job["forcar_resync"])
        job["automatico"] = bool(job["automatico"])
        job["requires_session"] = bool(job["requires_session"])
        job["dias_total"] = (_date(job["data_fim"]) - _date(job["data_inicio"])).days + 1
        columns = "day, result" if detailed else "day"
        days = conn.execute(f"SELECT {columns} FROM days WHERE job_id = ? ORDER BY day", (job_id,)).fetchall()
        job["dias_concluidos"] = [row["day"] for row in days]
        if detailed:
            job["resultados_dias"] = {row["day"]: json.loads(row["result"]) for row in days}
        latest = conn.execute("SELECT result FROM days WHERE job_id = ? ORDER BY completed_at DESC, day DESC LIMIT 1",
                              (job_id,)).fetchone()
        job["checkpoint"] = json.loads(latest[0]).get("checkpoint", {}) if latest else {}
        return job

    def _reuse_completed_days(self, conn, job_id, now):
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        today = dt.datetime.fromtimestamp(now, SYNC_TIMEZONE).date()
        own_days = conn.execute("SELECT day, completed_at FROM days WHERE job_id = ? AND day < ?",
                                (job_id, str(today))).fetchall()
        # A retry on the same day retains its progress. After the day closes,
        # a preliminary check must be collected again, including future days
        # that an older interrupted period may have confirmed while empty.
        expired = [(job_id, row["day"]) for row in own_days
                   if row["day"] >= str(dt.datetime.fromtimestamp(row["completed_at"], SYNC_TIMEZONE).date())]
        conn.executemany("DELETE FROM days WHERE job_id = ? AND day = ?", expired)
        if job["forcar_resync"] or job["kind"] == "history":
            return
        historical_end = min(job["data_fim"], str(today - dt.timedelta(days=RECENT_WINDOW_DAYS)))
        if job["data_inicio"] > historical_end:
            return
        rows = conn.execute("""SELECT d.day, d.result, d.completed_at FROM days d
            JOIN jobs source ON source.id = d.job_id
            WHERE source.store_id = ? AND d.job_id != ? AND d.day BETWEEN ? AND ?
                AND d.completed_at <= ?
            ORDER BY d.completed_at DESC""",
                            (job["store_id"], job_id, job["data_inicio"], historical_end, now)).fetchall()
        confirmed = {}
        for row in rows:
            if row["day"] in confirmed:
                continue
            checked_day = dt.datetime.fromtimestamp(row["completed_at"], SYNC_TIMEZONE).date()
            # A day checked before it ended (including a future empty day) is
            # not permanent coverage. Never refresh the original timestamp when
            # inheriting, or a later cycle could legitimize incomplete coverage.
            if row["day"] < str(checked_day):
                confirmed[row["day"]] = (job_id, row["day"], row["result"], row["completed_at"])
        conn.executemany("INSERT OR IGNORE INTO days VALUES (?, ?, ?, ?)", confirmed.values())

    def _enqueue(self, conn, store_id, loja, start, end, force, automatic, kind, now, requires_session=False):
        identity = [store_id, start, end, bool(force)]
        if kind == "history":
            # A historical review must not adopt an incremental job whose days
            # were inherited. Keep existing ordinary-job signatures compatible.
            identity.append("history")
        signature = hashlib.sha256(json.dumps(identity,
                                             separators=(",", ":")).encode()).hexdigest()
        old = conn.execute("SELECT id, status FROM jobs WHERE signature = ? AND status != 'complete'",
                           (signature,)).fetchone()
        if old:
            if old["status"] != "running":
                conn.execute("""UPDATE jobs SET status = 'queued', next_attempt = ?,
                    owner = NULL, lease_until = NULL, updated_at = ?, loja = ?, automatico = ?,
                    requires_session = ? WHERE id = ?""",
                             (now, now, loja, int(automatic), int(requires_session), old["id"]))
                self._reuse_completed_days(conn, old["id"], now)
            return self._job(conn, old["id"])
        job_id = uuid.uuid4().hex
        conn.execute("""INSERT INTO jobs
            (id, signature, store_id, loja, data_inicio, data_fim, forcar_resync,
             automatico, kind, next_attempt, created_at, updated_at, requires_session)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (job_id, signature, store_id, loja, start, end, int(force),
                      int(automatic), kind, now, now, now, int(requires_session)))
        self._reuse_completed_days(conn, job_id, now)
        return self._job(conn, job_id)

    def enqueue(self, store_id, loja, data_inicio, data_fim, forcar_resync=False,
                automatico=False, kind="manual", now=None, requires_session=False,
                verify_connections=False):
        """Update this store's plan and enqueue without discarding earlier jobs.

        An explicit automatico=False disables its plan and pauses automatic jobs;
        the requested matching job is resumed with the existing acknowledgements.
        """
        store_id, loja = str(store_id or "").strip(), str(loja or "").strip()
        if not store_id or not loja:
            raise ValueError("Loja e identificador estavel sao obrigatorios.")
        start, end = _date(data_inicio), _date(data_fim)
        if start > end:
            raise ValueError("Data inicial nao pode ser maior que a data final.")
        if kind not in {"manual", "initial", "recent", "history"}:
            raise ValueError("Tipo de sincronizacao invalido.")
        now = _time(now)
        with self._transaction() as conn:
            conn.execute("""INSERT INTO plans
                (store_id, loja, enabled, data_inicio, data_fim, forcar_resync,
                 next_due, history_cursor, history_next_due, requires_session)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET loja = excluded.loja, enabled = excluded.enabled,
                    data_inicio = MIN(plans.data_inicio, excluded.data_inicio),
                    data_fim = MAX(plans.data_fim, excluded.data_fim),
                    last_success_day = CASE WHEN excluded.data_inicio < plans.data_inicio
                        THEN NULL ELSE plans.last_success_day END,
                    forcar_resync = excluded.forcar_resync,
                    requires_session = excluded.requires_session,
                    next_due = MIN(plans.next_due, excluded.next_due)""",
                         (store_id, loja, int(automatico), str(start), str(end),
                          int(forcar_resync), now, str(start), now, int(requires_session)))
            if not automatico:
                conn.execute("""UPDATE jobs SET status = 'paused', owner = NULL, lease_until = NULL,
                    updated_at = ? WHERE store_id = ? AND automatico = 1 AND status != 'complete'""",
                             (now, store_id))
            else:
                conn.execute("""UPDATE jobs SET status = 'queued', next_attempt = ?, updated_at = ?
                    WHERE store_id = ? AND automatico = 1 AND status = 'paused'""", (now, now, store_id))
            job = self._enqueue(conn, store_id, loja, str(start), str(end),
                                forcar_resync, automatico, kind, now, requires_session)
            if verify_connections:
                self._request_connection_check(conn, requires_session=requires_session)
            return job

    def claim(self, owner, now=None, lease_seconds=120, limit=2, exclude_store_ids=()):
        if not str(owner or "").strip() or lease_seconds <= 0:
            raise ValueError("Executor e duracao de concessao validos sao obrigatorios.")
        with self._transaction() as conn:
            now = _time(now)
            if self._connection_check_pending(conn):
                return None
            active = conn.execute("SELECT store_id FROM jobs WHERE status = 'running' AND lease_until > ?",
                                  (now,)).fetchall()
            if len(active) >= min(2, max(0, int(limit))):
                return None
            rows = conn.execute("""SELECT id, store_id FROM jobs WHERE
                (status IN ('queued', 'retry') AND COALESCE(next_attempt, 0) <= ?)
                OR (status = 'running' AND COALESCE(lease_until, 0) <= ?)
                ORDER BY created_at, rowid""", (now, now)).fetchall()
            busy = {row["store_id"] for row in active} | {str(value) for value in exclude_store_ids}
            row = next((row for row in rows if row["store_id"] not in busy), None)
            if row is None:
                return None
            # Coverage may have improved while this period waited behind an
            # earlier job for the store. Import before the worker takes its snapshot.
            self._reuse_completed_days(conn, row["id"], now)
            previous = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            acknowledged = conn.execute("SELECT error_signature FROM connection_alert_ack WHERE job_id = ?",
                                        (row["id"],)).fetchone()
            transfer_ack = bool(previous["last_error"] and acknowledged and
                                acknowledged[0] == self._error_signature(previous))
            conn.execute("""UPDATE jobs SET status = 'running', owner = ?, lease_until = ?,
                attempts = attempts + 1, updated_at = ? WHERE id = ?""",
                         (str(owner), now + lease_seconds, now, row["id"]))
            job = self._job(conn, row["id"])
            if transfer_ack:
                # Starting collection does not create another provider error.
                # fail_job deletes this acknowledgement on every new failure.
                conn.execute("UPDATE connection_alert_ack SET error_signature = ? WHERE job_id = ?",
                             (self._error_signature(job), job["id"]))
            return job

    @staticmethod
    def _owns(conn, job_id, owner, now):
        return conn.execute("""SELECT 1 FROM jobs WHERE id = ? AND owner = ?
            AND status = 'running' AND lease_until > ?""", (job_id, owner, now)).fetchone() is not None

    def owns(self, job_id, owner, now=None):
        with self._connection() as conn:
            row = conn.execute("SELECT owner, status, lease_until FROM jobs WHERE id = ?",
                               (job_id,)).fetchone()
            return bool(row and row["owner"] == owner and row["status"] == "running"
                        and (row["lease_until"] or 0) > _time(now))

    def heartbeat(self, job_id, owner, now=None, lease_seconds=120):
        if lease_seconds <= 0:
            raise ValueError("Duracao de concessao invalida.")
        with self._transaction() as conn:
            now = _time(now)
            if not self._owns(conn, job_id, owner, now):
                return False
            conn.execute("UPDATE jobs SET lease_until = ?, updated_at = ? WHERE id = ?",
                         (now + lease_seconds, now, job_id))
            return True

    def _require_owner(self, conn, job_id, owner, now):
        if not self._owns(conn, job_id, owner, now):
            raise RuntimeError("A sincronizacao foi pausada ou a concessao expirou.")
        return self._job(conn, job_id)

    def complete_day(self, job_id, owner, day, result, now=None):
        day = str(_date(day))
        # Never persist source payloads, credentials or arbitrary tool results.
        counts = {key: int(value) for key, value in (result or {}).items()
                  if key in {"vendas", "notas", "itens", "devolucoes"}
                  and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0}
        checkpoint = _checkpoint((result or {}).get("checkpoint"))
        if checkpoint:
            counts["checkpoint"] = checkpoint
        with self._transaction() as conn:
            now = _time(now)
            job = self._require_owner(conn, job_id, owner, now)
            if not job["data_inicio"] <= day <= job["data_fim"]:
                raise ValueError("Dia fora do periodo da sincronizacao.")
            conn.execute("INSERT OR IGNORE INTO days VALUES (?, ?, ?, ?)",
                         (job_id, day, json.dumps(counts), now))
            conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id))
            return self._job(conn, job_id)

    def complete_job(self, job_id, owner, now=None, today=None):
        with self._transaction() as conn:
            now = _time(now)
            today = _date(today) if today is not None else dt.datetime.fromtimestamp(now, SYNC_TIMEZONE).date()
            job = self._require_owner(conn, job_id, owner, now)
            if len(job["dias_concluidos"]) != job["dias_total"]:
                raise ValueError("Ainda existem dias sem conferencia concluida.")
            conn.execute("""UPDATE jobs SET status = 'complete', owner = NULL, lease_until = NULL,
                last_error = NULL, next_attempt = NULL, updated_at = ?, completed_at = ? WHERE id = ?""",
                         (now, now, job_id))
            if job["kind"] == "history":
                cursor = str(_date(job["data_fim"]) + dt.timedelta(days=1))
                conn.execute("""UPDATE plans SET history_cursor = ?, history_next_due = ?,
                    last_success = ? WHERE store_id = ?""",
                             (cursor, now + HISTORY_INTERVAL_SECONDS, now, job["store_id"]))
            else:
                covered_until = min(_date(job["data_fim"]), today)
                plan = conn.execute("SELECT data_inicio, last_success_day FROM plans WHERE store_id = ?",
                                    (job["store_id"],)).fetchone()
                previous = plan["last_success_day"] if plan else None
                frontier = _date(previous) + dt.timedelta(days=1) if previous else _date(plan["data_inicio"])
                # A later manual interval cannot turn an unvisited gap into coverage.
                watermark = max(previous or "", str(covered_until)) if _date(job["data_inicio"]) <= frontier else previous
                conn.execute("""UPDATE plans SET next_due = ?, last_success = ?,
                    last_success_day = ? WHERE store_id = ?""",
                             (now + INTERVAL_SECONDS, now, watermark, job["store_id"]))
            return self._job(conn, job_id)

    def fail_job(self, job_id, owner, message, retryable=True, retry_after=None, now=None):
        """message must be a sanitized operational label; retry_after is seconds."""
        with self._transaction() as conn:
            now = _time(now)
            job = self._require_owner(conn, job_id, owner, now)
            delay = min(INTERVAL_SECONDS, 15 * 2 ** min(6, max(0, job["attempts"] - 1)))
            if retry_after is not None:
                delay = max(1, float(retry_after))
            conn.execute("DELETE FROM connection_alert_ack WHERE job_id = ?", (job_id,))
            conn.execute("""UPDATE jobs SET status = ?, last_error = ?, next_attempt = ?,
                owner = NULL, lease_until = NULL, updated_at = ? WHERE id = ?""",
                         ("retry" if retryable else "blocked", str(message)[:240],
                          now + delay if retryable else None, now, job_id))
            return self._job(conn, job_id)

    @staticmethod
    def _error_signature(job):
        # Heartbeats update updated_at. Only the error and attempt identify the
        # warning; fail_job explicitly invalidates acknowledgements even when a
        # later failure repeats the exact same message in this attempt.
        values = [job["id"], job["attempts"], job["last_error"]]
        return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()

    def connection_alerts(self, store_id):
        with self._connection() as conn:
            rows = conn.execute("""SELECT j.*, a.error_signature AS acknowledged_error
                FROM jobs j LEFT JOIN connection_alert_ack a ON a.job_id = j.id
                WHERE j.store_id = ? AND j.status != 'complete' AND j.last_error IS NOT NULL""",
                                (store_id,)).fetchall()
            return [{"id": row["id"], "signature": self._error_signature(row)}
                    for row in rows if row["acknowledged_error"] != self._error_signature(row)]

    def _connection_alert_rows(self, conn):
        rows = conn.execute("""SELECT j.*, a.error_signature AS acknowledged_error
            FROM jobs j LEFT JOIN connection_alert_ack a ON a.job_id = j.id
            WHERE j.status != 'complete' AND j.last_error IS NOT NULL
            ORDER BY j.store_id, j.created_at, j.rowid""").fetchall()
        return [row for row in rows if row["acknowledged_error"] != self._error_signature(row)]

    def _has_connection_alerts(self, conn):
        return bool(self._connection_alert_rows(conn))

    @staticmethod
    def _connection_check_pending(conn):
        row = conn.execute("""SELECT requested_generation, completed_generation
            FROM connection_check_state WHERE singleton = 1""").fetchone()
        return bool(row and row["requested_generation"] > row["completed_generation"])

    def _request_connection_check(self, conn, requires_session=False):
        pending = self._connection_check_pending(conn)
        if pending or self._has_connection_alerts(conn):
            conn.execute("""UPDATE connection_check_state
                SET requested_generation = requested_generation + 1,
                    requires_session = CASE WHEN ? OR (requested_generation > completed_generation
                        AND requires_session = 1) THEN 1 ELSE 0 END
                WHERE singleton = 1""", (int(bool(requires_session)),))

    def connection_check_pending(self):
        with self._connection() as conn:
            return self._connection_check_pending(conn)

    @staticmethod
    def _owns_connection_check(conn, owner, generation, now):
        return conn.execute("""SELECT 1 FROM connection_check_state WHERE singleton = 1
            AND owner = ? AND claimed_generation = ? AND lease_until > ?
            AND completed_generation < claimed_generation""",
                            (owner, generation, now)).fetchone() is not None

    def claim_connection_check(self, owner, now=None, lease_seconds=120):
        if not str(owner or "").strip() or lease_seconds <= 0:
            raise ValueError("Executor e duracao de concessao validos sao obrigatorios.")
        with self._transaction() as conn:
            now = _time(now)
            if not self._connection_check_pending(conn):
                return None
            state = conn.execute("SELECT * FROM connection_check_state WHERE singleton = 1").fetchone()
            if state["owner"] is not None and (state["lease_until"] or 0) > now:
                return None
            if conn.execute("""SELECT 1 FROM jobs WHERE status = 'running'
                AND lease_until > ? LIMIT 1""", (now,)).fetchone():
                return None
            generation = max(state["requested_generation"], state["claimed_generation"] + 1)
            # Fence an expired attempt even if a caller reuses the same owner.
            conn.execute("""UPDATE connection_check_state SET requested_generation = ?,
                claimed_generation = ?, owner = ?, lease_until = ? WHERE singleton = 1""",
                         (generation, generation, str(owner), now + lease_seconds))
            stores = {}
            for row in self._connection_alert_rows(conn):
                store = stores.setdefault(row["store_id"], {
                    "store_id": row["store_id"], "loja": row["loja"],
                    "data_inicio": row["data_inicio"], "data_fim": row["data_fim"],
                    "requires_session": False, "alerts": [],
                })
                store["data_inicio"] = min(store["data_inicio"], row["data_inicio"])
                store["data_fim"] = max(store["data_fim"], row["data_fim"])
                store["requires_session"] |= bool(row["requires_session"])
                store["alerts"].append({"id": row["id"], "signature": self._error_signature(row)})
            return {"generation": generation, "requires_session": bool(state["requires_session"]),
                    "stores": list(stores.values())}

    def owns_connection_check(self, owner, generation, now=None):
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM connection_check_state WHERE singleton = 1").fetchone()
            return bool(row and row["owner"] == owner and row["claimed_generation"] == generation
                        and row["completed_generation"] < generation
                        and (row["lease_until"] or 0) > _time(now))

    def heartbeat_connection_check(self, owner, generation, now=None, lease_seconds=120):
        if lease_seconds <= 0:
            raise ValueError("Duracao de concessao invalida.")
        with self._transaction() as conn:
            now = _time(now)
            if not self._owns_connection_check(conn, owner, generation, now):
                return False
            conn.execute("UPDATE connection_check_state SET lease_until = ? WHERE singleton = 1",
                         (now + lease_seconds,))
            return True

    def acknowledge_checked_connection(self, owner, generation, store_id, alerts, now=None):
        """Recognize observed errors under the batch lease, including paused jobs."""
        count = 0
        with self._transaction() as conn:
            now = _time(now)
            if not self._owns_connection_check(conn, owner, generation, now):
                return 0
            for alert in alerts:
                row = conn.execute("""SELECT * FROM jobs WHERE id = ? AND store_id = ?
                    AND status != 'complete' AND last_error IS NOT NULL""",
                                   (alert["id"], store_id)).fetchone()
                if row is None or self._error_signature(row) != alert["signature"]:
                    continue
                conn.execute("""INSERT INTO connection_alert_ack VALUES (?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET error_signature = excluded.error_signature,
                        verified_at = excluded.verified_at""", (row["id"], alert["signature"], now))
                count += 1
        return count

    def complete_connection_check(self, owner, generation, now=None):
        with self._transaction() as conn:
            now = _time(now)
            if not self._owns_connection_check(conn, owner, generation, now):
                return False
            conn.execute("""UPDATE connection_check_state SET completed_generation = ?,
                requires_session = CASE WHEN requested_generation = ? THEN 0 ELSE requires_session END,
                owner = NULL, lease_until = NULL WHERE singleton = 1""", (generation, generation))
            return True

    def abandon_connection_check(self, owner, generation, now=None):
        """Release only this live grant; its requested generation stays pending."""
        with self._transaction() as conn:
            now = _time(now)
            if not self._owns_connection_check(conn, owner, generation, now):
                return False
            conn.execute("""UPDATE connection_check_state SET owner = NULL, lease_until = NULL
                WHERE singleton = 1""")
            return True

    def acknowledge_connection_alerts(self, job_id, owner, store_id, alerts, *, expected_attempts, now=None):
        """Dismiss only observed warnings after a real provider check, keeping history.

        This never completes work, changes scheduling or edits checkpoints. A
        lost lease or an error from a newer attempt cannot be acknowledged.
        """
        count = 0
        with self._transaction() as conn:
            # BEGIN IMMEDIATE may wait. Evaluate the clock after the lock, not
            # before it, so an expired lease cannot acknowledge an old reply.
            now = _time(now)
            if not self._owns(conn, job_id, owner, now):
                return 0
            running = self._job(conn, job_id, detailed=False)
            if running["store_id"] != store_id or running["attempts"] != expected_attempts:
                return 0
            for alert in alerts:
                row = conn.execute("""SELECT * FROM jobs WHERE id = ? AND store_id = ?
                    AND status != 'complete' AND last_error IS NOT NULL""",
                                   (alert["id"], store_id)).fetchone()
                if row is None or self._error_signature(row) != alert["signature"]:
                    continue
                conn.execute("""INSERT INTO connection_alert_ack VALUES (?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET error_signature = excluded.error_signature,
                        verified_at = excluded.verified_at""", (row["id"], alert["signature"], now))
                count += 1
        return count

    def pause_all(self, now=None):
        with self._transaction() as conn:
            conn.execute("""UPDATE jobs SET status = 'paused', owner = NULL, lease_until = NULL,
                updated_at = ? WHERE status != 'complete'""", (_time(now),))
            conn.execute("UPDATE plans SET enabled = 0")
            conn.execute("""UPDATE connection_check_state
                SET completed_generation = requested_generation, requires_session = 0,
                    owner = NULL, lease_until = NULL
                WHERE singleton = 1""")

    def list_jobs(self):
        with self._connection() as conn:
            return [self._job(conn, row[0]) for row in
                    conn.execute("SELECT id FROM jobs ORDER BY created_at DESC, rowid DESC").fetchall()]

    def get_job(self, job_id):
        with self._connection() as conn:
            if not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
                return None
            return self._job(conn, job_id)

    def schedule_due(self, today, now=None):
        """Fill recent gaps and rotate older seven-day windows once a day.

        Historical cursors advance only on completion, so retries and interrupted
        runs never skip a window. No scheduler thread is started by this module.
        """
        today, now = _date(today), _time(now)
        scheduled = []
        with self._transaction() as conn:
            plans = conn.execute("SELECT * FROM plans WHERE enabled = 1 ORDER BY store_id").fetchall()
            for plan in plans:
                if conn.execute("SELECT 1 FROM jobs WHERE store_id = ? AND status NOT IN ('complete', 'paused') LIMIT 1",
                                (plan["store_id"],)).fetchone():
                    continue
                start = _date(plan["data_inicio"])
                if start > today:
                    continue
                recent_start = max(start, today - dt.timedelta(days=RECENT_WINDOW_DAYS - 1))
                if plan["next_due"] <= now:
                    gap = _date(plan["last_success_day"]) if plan["last_success_day"] else start
                    begin = max(start, min(recent_start, gap))
                    scheduled.append(self._enqueue(conn, plan["store_id"], plan["loja"],
                        str(begin), str(today), False, True, "recent", now, plan["requires_session"]))
                history_end = recent_start - dt.timedelta(days=1)
                if plan["history_next_due"] <= now and start <= history_end:
                    cursor = _date(plan["history_cursor"])
                    if cursor < start or cursor > history_end:
                        cursor = start
                    end = min(history_end, cursor + dt.timedelta(days=6))
                    scheduled.append(self._enqueue(conn, plan["store_id"], plan["loja"],
                        str(cursor), str(end), False, True, "history", now, plan["requires_session"]))
            if scheduled:
                self._request_connection_check(conn, requires_session=any(
                    job["requires_session"] for job in scheduled))
            return scheduled

    def summary(self, now=None):
        with self._connection() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("""SELECT id FROM jobs WHERE status != 'complete'
                OR id IN (SELECT id FROM jobs WHERE status = 'complete' ORDER BY created_at DESC, rowid DESC LIMIT 20)
                OR id IN (SELECT j.id FROM jobs j WHERE j.id = (
                    SELECT latest.id FROM jobs latest WHERE latest.store_id = j.store_id
                    ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1))
                ORDER BY created_at DESC, rowid DESC""").fetchall()
            jobs = [self._job(conn, row[0], detailed=False) for row in rows]
            acknowledgements = {row["job_id"]: row["error_signature"] for row in
                                conn.execute("SELECT job_id, error_signature FROM connection_alert_ack")}
            for job in jobs:
                job["aviso_conexao_verificado"] = bool(job["last_error"] and
                    acknowledgements.get(job["id"]) == self._error_signature(job))
                for private in ("owner", "signature", "requires_session"):
                    job.pop(private, None)
            plans = conn.execute("SELECT * FROM plans ORDER BY loja, store_id").fetchall()
            stores = []
            for plan in plans:
                related = [job for job in jobs if job["store_id"] == plan["store_id"]]
                pending = [job for job in related if job["status"] != "complete"]
                operational = [job for job in pending if job["status"] != "paused"]
                job = next((job for job in pending if job["status"] == "running"), None)
                latest = related[0] if related else None
                selected = (latest if latest and latest["status"] == "complete" else pending[-1] if pending else latest)
                job = job or (operational[-1] if operational else selected)
                due = plan["next_due"] if plan["enabled"] else None
                if job and job["status"] != "complete":
                    due = job["next_attempt"] if job["status"] in {"queued", "retry"} else None
                stores.append({"loja": plan["loja"], "store_id": plan["store_id"],
                    "habilitado": bool(plan["enabled"]), "status": job["status"] if job else "idle",
                    "data_inicio": job["data_inicio"] if job else plan["data_inicio"],
                    "data_fim": job["data_fim"] if job else plan["data_fim"],
                    "dias_concluidos": len(job["dias_concluidos"]) if job else 0,
                    "dias_total": job["dias_total"] if job else 0,
                    "pendentes": len(operational), "pausados": len(pending) - len(operational),
                    "checkpoint": job["checkpoint"] if job else {},
                    "proxima_execucao": _iso(due),
                    "ultimo_erro": job["last_error"] if job and not job["aviso_conexao_verificado"] else None,
                    "aviso_conexao_verificado": bool(job and job["aviso_conexao_verificado"]),
                    "ultima_conferencia": _iso(plan["last_success"]),
                    "job_id": job["id"] if job else None,
                    "forcar_resync": job["forcar_resync"] if job else bool(plan["forcar_resync"])})
            return {"lojas": stores, "jobs": jobs,
                    "verificando_conexoes": self._connection_check_pending(conn),
                    "pendentes": sum(job["status"] not in {"complete", "paused"} for job in jobs),
                    "pausados": sum(job["status"] == "paused" for job in jobs),
                    "habilitadas": sum(bool(plan["enabled"]) for plan in plans)}
