"""Local, tenant-scoped durable work queue. This module never calls a data source.

Day acknowledgements are written only after the caller has committed the entire
day. Replaying an expired lease must therefore remain idempotent in the caller.
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

    def _enqueue(self, conn, store_id, loja, start, end, force, automatic, kind, now, requires_session=False):
        signature = hashlib.sha256(json.dumps([store_id, start, end, bool(force)],
                                             separators=(",", ":")).encode()).hexdigest()
        old = conn.execute("SELECT id, status FROM jobs WHERE signature = ? AND status != 'complete'",
                           (signature,)).fetchone()
        if old:
            if old["status"] != "running":
                conn.execute("""UPDATE jobs SET status = 'queued', next_attempt = ?, last_error = NULL,
                    owner = NULL, lease_until = NULL, updated_at = ?, loja = ?, automatico = ?,
                    requires_session = ? WHERE id = ?""",
                             (now, now, loja, int(automatic), int(requires_session), old["id"]))
            return self._job(conn, old["id"])
        job_id = uuid.uuid4().hex
        conn.execute("""INSERT INTO jobs
            (id, signature, store_id, loja, data_inicio, data_fim, forcar_resync,
             automatico, kind, next_attempt, created_at, updated_at, requires_session)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (job_id, signature, store_id, loja, start, end, int(force),
                      int(automatic), kind, now, now, now, int(requires_session)))
        return self._job(conn, job_id)

    def enqueue(self, store_id, loja, data_inicio, data_fim, forcar_resync=False,
                automatico=False, kind="manual", now=None, requires_session=False):
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
            return self._enqueue(conn, store_id, loja, str(start), str(end),
                                 forcar_resync, automatico, kind, now, requires_session)

    def claim(self, owner, now=None, lease_seconds=120, limit=2, exclude_store_ids=()):
        if not str(owner or "").strip() or lease_seconds <= 0:
            raise ValueError("Executor e duracao de concessao validos sao obrigatorios.")
        now = _time(now)
        with self._transaction() as conn:
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
            conn.execute("""UPDATE jobs SET status = 'running', owner = ?, lease_until = ?,
                attempts = attempts + 1, updated_at = ? WHERE id = ?""",
                         (str(owner), now + lease_seconds, now, row["id"]))
            return self._job(conn, row["id"])

    @staticmethod
    def _owns(conn, job_id, owner, now):
        return conn.execute("""SELECT 1 FROM jobs WHERE id = ? AND owner = ?
            AND status = 'running' AND lease_until > ?""", (job_id, owner, now)).fetchone() is not None

    def owns(self, job_id, owner, now=None):
        with self._connection() as conn:
            return self._owns(conn, job_id, owner, _time(now))

    def heartbeat(self, job_id, owner, now=None, lease_seconds=120):
        if lease_seconds <= 0:
            raise ValueError("Duracao de concessao invalida.")
        now = _time(now)
        with self._transaction() as conn:
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
        day, now = str(_date(day)), _time(now)
        # Never persist source payloads, credentials or arbitrary tool results.
        counts = {key: int(value) for key, value in (result or {}).items()
                  if key in {"vendas", "notas", "itens", "devolucoes"}
                  and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0}
        checkpoint = _checkpoint((result or {}).get("checkpoint"))
        if checkpoint:
            counts["checkpoint"] = checkpoint
        with self._transaction() as conn:
            job = self._require_owner(conn, job_id, owner, now)
            if not job["data_inicio"] <= day <= job["data_fim"]:
                raise ValueError("Dia fora do periodo da sincronizacao.")
            conn.execute("INSERT OR IGNORE INTO days VALUES (?, ?, ?, ?)",
                         (job_id, day, json.dumps(counts), now))
            conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id))
            return self._job(conn, job_id)

    def complete_job(self, job_id, owner, now=None, today=None):
        now = _time(now)
        today = _date(today) if today is not None else dt.datetime.fromtimestamp(now, SYNC_TIMEZONE).date()
        with self._transaction() as conn:
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
        now = _time(now)
        with self._transaction() as conn:
            job = self._require_owner(conn, job_id, owner, now)
            delay = min(INTERVAL_SECONDS, 15 * 2 ** min(6, max(0, job["attempts"] - 1)))
            if retry_after is not None:
                delay = max(1, float(retry_after))
            conn.execute("""UPDATE jobs SET status = ?, last_error = ?, next_attempt = ?,
                owner = NULL, lease_until = NULL, updated_at = ? WHERE id = ?""",
                         ("retry" if retryable else "blocked", str(message)[:240],
                          now + delay if retryable else None, now, job_id))
            return self._job(conn, job_id)

    def pause_all(self, now=None):
        with self._transaction() as conn:
            conn.execute("""UPDATE jobs SET status = 'paused', owner = NULL, lease_until = NULL,
                updated_at = ? WHERE status != 'complete'""", (_time(now),))
            conn.execute("UPDATE plans SET enabled = 0")

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
                recent_start = max(start, today - dt.timedelta(days=6))
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
            for job in jobs:
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
                    "ultimo_erro": job["last_error"] if job else None,
                    "ultima_conferencia": _iso(plan["last_success"]),
                    "job_id": job["id"] if job else None,
                    "forcar_resync": job["forcar_resync"] if job else bool(plan["forcar_resync"])})
            return {"lojas": stores, "jobs": jobs,
                    "pendentes": sum(job["status"] not in {"complete", "paused"} for job in jobs),
                    "pausados": sum(job["status"] == "paused" for job in jobs),
                    "habilitadas": sum(bool(plan["enabled"]) for plan in plans)}
