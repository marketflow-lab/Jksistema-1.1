"""Customer-reply job queue, leases, and lifecycle persistence."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Any, Optional

from backend.services.vehicle_identity import (
    VEHICLE_IDENTITY_POLICY,
    VEHICLE_IDENTITY_SCHEMA,
    VehicleIdentityFactsV1,
)
from backend.services.vin_transient import contains_vin_like_identifier, is_valid_vin

from .common import (
    _connection,
    _json_dumps,
    _json_loads,
    _lock_for,
    _now_iso,
    _safe_id,
    codex_assistant_state_db_path,
)
from .customer_reply_state import (
    _CUSTOMER_REPLY_TRANSIENT,
    _CUSTOMER_REPLY_TRANSIENT_LOCK,
    _customer_reply_cache_key,
    _customer_reply_cleanup_rows,
    _customer_reply_durable_payload,
    _customer_reply_transient_merge,
    _customer_reply_transient_put,
)
from .schema import _ensure_state_schema


_REQUEST_GENERATION_CAS_APPLIED = "_request_generation_cas_applied"
_VEHICLE_IDENTITY_FIELDS = (
    "status",
    "manufacturer",
    "make",
    "model",
    "model_year",
    "series",
    "vehicle_type",
    "body_class",
    "engine_model",
    "engine_displacement_l",
    "fuel_type",
    "plant_country",
    "plant_company",
    "source",
    "reason",
)
_VEHICLE_IDENTITY_KEYS = frozenset(
    {"schema", "policy", *_VEHICLE_IDENTITY_FIELDS}
)
_VIN_CAPTURE_STATUSES = frozenset({"absent", "captured", "ambiguous", "invalid"})


def _safe_vehicle_identity(value: object) -> dict[str, str]:
    """Return only the closed, VIN-free VehicleIdentityFactsV1 contract."""

    if not isinstance(value, dict) or not value:
        return {}
    if set(value) - _VEHICLE_IDENTITY_KEYS:
        return {}
    if (
        str(value.get("schema") or "") != VEHICLE_IDENTITY_SCHEMA
        or str(value.get("policy") or "") != VEHICLE_IDENTITY_POLICY
    ):
        return {}
    normalized: dict[str, str] = {}
    for field_name in _VEHICLE_IDENTITY_FIELDS:
        raw = value.get(field_name, "")
        if not isinstance(raw, str) or len(raw) > 256:
            return {}
        if contains_vin_like_identifier(raw) or is_valid_vin(raw):
            return {}
        normalized[field_name] = raw
    if normalized.get("source") != "nhtsa_vpic":
        return {}
    try:
        return VehicleIdentityFactsV1(**normalized).as_dict()
    except (TypeError, ValueError):
        return {}


def _persist_safe_vehicle_identity(
    data: dict[str, Any],
    durable: dict[str, Any],
    transient: dict[str, Any],
) -> None:
    """Keep decoder facts durable while dropping every untrusted variant."""

    if "vehicle_identity" in data:
        safe_identity = _safe_vehicle_identity(data.get("vehicle_identity"))
        durable.pop("vehicle_identity", None)
        transient.pop("vehicle_identity", None)
        if safe_identity:
            data["vehicle_identity"] = safe_identity
            durable["vehicle_identity"] = safe_identity
        else:
            data.pop("vehicle_identity", None)
    if "vehicle_identity_capture_status" in data:
        capture_status = str(data.get("vehicle_identity_capture_status") or "")
        durable.pop("vehicle_identity_capture_status", None)
        transient.pop("vehicle_identity_capture_status", None)
        if capture_status in _VIN_CAPTURE_STATUSES:
            data["vehicle_identity_capture_status"] = capture_status
            durable["vehicle_identity_capture_status"] = capture_status
        else:
            data.pop("vehicle_identity_capture_status", None)


def codex_assistant_customer_reply_job_has_transient(
    info_base: str, client_id: str, job_id: str
) -> bool:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    key = _customer_reply_cache_key(db_path, job_id)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        cached = _CUSTOMER_REPLY_TRANSIENT.get(key)
        if cached and cached[0] > time.time():
            return True
        _CUSTOMER_REPLY_TRANSIENT.pop(key, None)
        return False


def codex_assistant_customer_reply_jobs_cleanup(info_base: str, client_id: str) -> int:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    expired: list[str] = []
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            expired = _customer_reply_cleanup_rows(conn)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        for job_id in expired:
            _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, job_id), None)
    return len(expired)


def _customer_reply_prepare_job(
    client_id: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], str, str]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    job_id = _safe_id(data.get("job_id"), "")
    if not job_id:
        job_id = hashlib.sha256(
            f"{time.time_ns()}|{client_id}|{data.get('task_type')}|{data.get('subject_key')}".encode("utf-8")
        ).hexdigest()[:32]
    now = _now_iso()
    data["job_id"] = job_id
    data.setdefault("profile", "mercado_livre_customer_reply")
    data.setdefault("status", "queued")
    data.setdefault("agent_state", "entendendo")
    data.setdefault("created_at", now)
    data.setdefault("lease_generation", 0)
    data["updated_at"] = now
    return data, job_id, now


def _customer_reply_queue_admission_result(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    job_id: str,
    existing_row: Optional[sqlite3.Row],
    max_origin_active: int,
    max_origin_active_store: int,
) -> Optional[dict[str, Any]]:
    if (
        existing_row
        or str(data.get("status") or "queued") not in {"queued", "running", "waiting_retry"}
        or not str(data.get("queue_origin") or "").strip()
        or (int(max_origin_active or 0) <= 0 and int(max_origin_active_store or 0) <= 0)
    ):
        return None
    origin = str(data.get("queue_origin") or "").strip()
    store = str(data.get("store") or "").strip()
    active_statuses = "('queued', 'running', 'waiting_retry')"
    limits = (
        (int(max_origin_active or 0), "", (origin,)),
        (int(max_origin_active_store or 0), " AND store = ?", (origin, store)),
    )
    for limit, store_clause, arguments in limits:
        if limit <= 0:
            continue
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM assistant_customer_reply_jobs "
            f"WHERE status IN {active_statuses} AND cancel_requested = 0 "
            f"AND queue_origin = ?{store_clause}",
            arguments,
        ).fetchone()
        if int(row["total"] or 0) >= limit:
            return {
                "job_id": job_id,
                "status": "deferred",
                "queue_admission_blocked": True,
                "blocked_reason": "queue_backpressure",
                "queue_origin": origin,
            }
    return None


def _customer_reply_existing_result(
    db_path: str,
    data: dict[str, Any],
    existing_row: Optional[sqlite3.Row],
    expected_lease_owner: str,
    expected_lease_generation: Optional[int],
    expected_request_generation: Optional[int],
) -> Optional[dict[str, Any]]:
    if not existing_row:
        return None
    existing = _json_loads(existing_row["payload_json"], {})
    if not isinstance(existing, dict):
        existing = {}
    if bool(existing_row["cancel_requested"]) or str(existing_row["status"] or "") == "cancelled":
        return _customer_reply_transient_merge(db_path, existing)
    existing_owner = str(existing_row["lease_owner"] or "")
    incoming_owner = str(data.get("lease_owner") or "")
    expected_owner = str(expected_lease_owner or "")
    existing_generation = max(0, int(existing_row["lease_generation"] or 0))
    incoming_generation = max(0, int(data.get("lease_generation") or 0))
    if expected_lease_generation is not None and int(expected_lease_generation) != existing_generation:
        return _customer_reply_transient_merge(db_path, existing)
    existing_request_generation = max(1, int(existing.get("request_generation") or 1))
    if (
        expected_request_generation is not None
        and int(expected_request_generation) != existing_request_generation
    ):
        return _customer_reply_transient_merge(db_path, existing)
    if incoming_generation != existing_generation:
        return _customer_reply_transient_merge(db_path, existing)
    if (
        str(existing_row["status"] or "") == "running"
        and existing_owner
        and existing_owner not in {incoming_owner, expected_owner}
    ):
        return _customer_reply_transient_merge(db_path, existing)
    data["lease_generation"] = existing_generation
    return None


def _customer_reply_upsert(
    conn: sqlite3.Connection, data: dict[str, Any], job_id: str, now: str
) -> dict[str, Any]:
    durable, transient = _customer_reply_durable_payload(data)
    _persist_safe_vehicle_identity(data, durable, transient)
    raw = _json_dumps(durable)
    conn.execute(
        """
        INSERT INTO assistant_customer_reply_jobs(
            job_id, profile, task_type, subject_key, store, store_id, seller_id, site_id, status,
            agent_state, idempotency_key, thread_id,
            queue_origin, queue_priority, queue_policy_version, lease_owner,
            lease_expires_ts, lease_generation, cancel_requested, created_at, updated_at,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            profile=excluded.profile,
            task_type=excluded.task_type,
            subject_key=excluded.subject_key,
            store=excluded.store,
            store_id=excluded.store_id,
            seller_id=excluded.seller_id,
            site_id=excluded.site_id,
            status=excluded.status,
            agent_state=excluded.agent_state,
            idempotency_key=excluded.idempotency_key,
            thread_id=excluded.thread_id,
            queue_origin=excluded.queue_origin,
            queue_priority=excluded.queue_priority,
            queue_policy_version=excluded.queue_policy_version,
            lease_owner=excluded.lease_owner,
            lease_expires_ts=excluded.lease_expires_ts,
            lease_generation=excluded.lease_generation,
            cancel_requested=excluded.cancel_requested,
            updated_at=excluded.updated_at,
            payload_json=excluded.payload_json
        """,
        (
            job_id,
            str(durable.get("profile") or "mercado_livre_customer_reply"),
            str(durable.get("task_type") or "question"),
            str(durable.get("subject_key") or ""),
            str(durable.get("store") or ""),
            str(durable.get("store_id") or ""),
            str(durable.get("seller_id") or ""),
            str(durable.get("site_id") or ""),
            str(durable.get("status") or "queued"),
            str(durable.get("agent_state") or "entendendo"),
            str(durable.get("idempotency_key") or ""),
            str(durable.get("thread_id") or ""),
            str(durable.get("queue_origin") or "legacy"),
            int(durable.get("queue_priority") or 0),
            str(durable.get("queue_policy_version") or ""),
            str(durable.get("lease_owner") or ""),
            float(durable.get("lease_expires_ts") or 0.0),
            max(0, int(durable.get("lease_generation") or 0)),
            1 if durable.get("cancel_requested") else 0,
            str(durable.get("created_at") or now),
            now,
            raw,
        ),
    )
    return transient


def _codex_assistant_customer_reply_job_save_cas(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    expected_lease_owner: str = "",
    expected_lease_generation: Optional[int] = None,
    expected_request_generation: Optional[int] = None,
    max_origin_active: int = 0,
    max_origin_active_store: int = 0,
) -> dict[str, Any]:
    """Persist a job with an optional internal request-generation CAS."""

    data, job_id, now = _customer_reply_prepare_job(client_id, payload)
    db_path = codex_assistant_state_db_path(info_base, client_id)
    expired_job_ids: list[str] = []
    with _lock_for(db_path):
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            expired_job_ids = _customer_reply_cleanup_rows(conn)
            existing_row = conn.execute(
                "SELECT status, lease_owner, lease_generation, cancel_requested, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if expected_request_generation is not None and existing_row is None:
                return {
                    "job_id": job_id,
                    _REQUEST_GENERATION_CAS_APPLIED: False,
                }
            admission = _customer_reply_queue_admission_result(
                conn, data, job_id, existing_row, max_origin_active, max_origin_active_store
            )
            if admission is not None:
                return admission
            existing = _customer_reply_existing_result(
                db_path,
                data,
                existing_row,
                expected_lease_owner,
                expected_lease_generation,
                expected_request_generation,
            )
            if existing is not None:
                result = dict(existing)
                if expected_request_generation is not None:
                    result[_REQUEST_GENERATION_CAS_APPLIED] = False
                return result
            transient = _customer_reply_upsert(conn, data, job_id, now)
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        for expired_job_id in expired_job_ids:
            _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, expired_job_id), None)
    _customer_reply_transient_put(db_path, data, transient)
    result = dict(data)
    if expected_request_generation is not None:
        result[_REQUEST_GENERATION_CAS_APPLIED] = True
    return result


def codex_assistant_customer_reply_job_save(
    info_base: str,
    client_id: str,
    payload: dict[str, Any],
    *,
    expected_lease_owner: str = "",
    expected_lease_generation: Optional[int] = None,
    max_origin_active: int = 0,
    max_origin_active_store: int = 0,
) -> dict[str, Any]:
    """Persist a Mercado Livre customer-reply orchestration job."""

    return _codex_assistant_customer_reply_job_save_cas(
        info_base,
        client_id,
        payload,
        expected_lease_owner=expected_lease_owner,
        expected_lease_generation=expected_lease_generation,
        max_origin_active=max_origin_active,
        max_origin_active_store=max_origin_active_store,
    )


def codex_assistant_customer_reply_job_get(
    info_base: str,
    client_id: str,
    job_id: str = "",
    *,
    idempotency_key: str = "",
) -> Optional[dict[str, Any]]:
    if job_id:
        clause, value = "job_id = ?", _safe_id(job_id, "")
    elif idempotency_key:
        clause, value = "idempotency_key = ?", str(idempotency_key)
    else:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            row = conn.execute(
                f"SELECT payload_json FROM assistant_customer_reply_jobs WHERE {clause} LIMIT 1",
                (value,),
            ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return _customer_reply_transient_merge(db_path, payload) if isinstance(payload, dict) else None


def codex_assistant_customer_reply_job_latest(
    info_base: str,
    client_id: str,
    *,
    task_type: str,
    store: str,
    subject_key: str,
    store_id: str = "",
) -> Optional[dict[str, Any]]:
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            exact_store_id = str(store_id or "").strip()
            if exact_store_id:
                row = conn.execute(
                    "SELECT payload_json FROM assistant_customer_reply_jobs "
                    "WHERE profile = 'mercado_livre_customer_reply' "
                    "AND task_type = ? AND store_id = ? AND subject_key = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(task_type or ""), exact_store_id, str(subject_key or "")),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT payload_json FROM assistant_customer_reply_jobs "
                    "WHERE profile = 'mercado_livre_customer_reply' "
                    "AND task_type = ? AND store = ? AND subject_key = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(task_type or ""), str(store or ""), str(subject_key or "")),
                ).fetchone()
    payload = _json_loads(row["payload_json"] if row else "", None)
    return _customer_reply_transient_merge(db_path, payload) if isinstance(payload, dict) else None


def codex_assistant_customer_reply_jobs_list(
    info_base: str,
    client_id: str,
    *,
    statuses: Optional[list[str]] = None,
    limit: int = 100,
    offset: int = 0,
    priority_order: bool = False,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = "SELECT payload_json FROM assistant_customer_reply_jobs"
    clean_statuses = [str(item or "").strip() for item in (statuses or []) if str(item or "").strip()]
    if clean_statuses:
        sql += " WHERE status IN (" + ",".join("?" for _ in clean_statuses) + ")"
        params.extend(clean_statuses)
    if priority_order:
        sql += " ORDER BY queue_priority DESC, created_at ASC, job_id ASC LIMIT ? OFFSET ?"
    else:
        sql += " ORDER BY created_at ASC, job_id ASC LIMIT ? OFFSET ?"
    params.append(max(1, min(int(limit or 100), 1000)))
    params.append(max(0, int(offset or 0)))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_loads(row["payload_json"], None)
        if isinstance(payload, dict):
            result.append(_customer_reply_transient_merge(db_path, payload))
    return result


def codex_assistant_customer_reply_queue_metrics(
    info_base: str,
    client_id: str,
    *,
    origin: str = "",
    store: str = "",
) -> dict[str, int]:
    """Return secret-free durable queue counters for one tenant database."""

    clauses = ["status IN ('queued', 'running', 'waiting_retry')", "cancel_requested = 0"]
    params: list[Any] = []
    if str(origin or "").strip():
        clauses.append("queue_origin = ?")
        params.append(str(origin).strip())
    if str(store or "").strip():
        clauses.append("store = ?")
        params.append(str(store).strip())
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM assistant_customer_reply_jobs "
                f"WHERE {' AND '.join(clauses)} GROUP BY status",
                params,
            ).fetchall()
    counts = {"queued": 0, "running": 0, "waiting_retry": 0}
    for row in rows:
        status = str(row["status"] or "")
        if status in counts:
            counts[status] = max(0, int(row["total"] or 0))
    counts["active"] = sum(counts.values())
    return counts


def codex_assistant_customer_reply_job_claim(
    info_base: str,
    client_id: str,
    job_id: str,
    *,
    owner: str,
    lease_seconds: float = 45.0,
    max_running: int = 0,
    enforce_priority: bool = False,
    queue_policy_version: str = "",
) -> Optional[dict[str, Any]]:
    """Atomically claim a queued job or a running job whose lease expired."""

    now_ts = time.time()
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, store, queue_priority, queue_policy_version, created_at, "
                "lease_owner, lease_expires_ts, lease_generation, cancel_requested, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (_safe_id(job_id, ""),),
            ).fetchone()
            if not row or bool(row["cancel_requested"]):
                return None
            status = str(row["status"] or "")
            lease_owner = str(row["lease_owner"] or "")
            lease_expires = float(row["lease_expires_ts"] or 0.0)
            if status not in {"queued", "running"}:
                return None
            if status == "running" and lease_expires > now_ts and lease_owner != str(owner or ""):
                return None
            expected_policy = str(queue_policy_version or "").strip()
            if expected_policy and str(row["queue_policy_version"] or "") != expected_policy:
                return None
            if max(0, int(max_running or 0)):
                running_total = conn.execute(
                    "SELECT COUNT(*) FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 "
                    "AND lease_expires_ts > ? AND job_id <> ?",
                    (now_ts, _safe_id(job_id, "")),
                ).fetchone()[0]
                if int(running_total or 0) >= max(1, int(max_running)):
                    return None
                same_store = conn.execute(
                    "SELECT 1 FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 "
                    "AND lease_expires_ts > ? AND store = ? AND job_id <> ? LIMIT 1",
                    (now_ts, str(row["store"] or ""), _safe_id(job_id, "")),
                ).fetchone()
                if same_store:
                    return None
            if enforce_priority:
                higher_priority = conn.execute(
                    "SELECT 1 FROM assistant_customer_reply_jobs "
                    "WHERE status = 'queued' AND cancel_requested = 0 AND job_id <> ? "
                    "AND (? = '' OR queue_policy_version = ?) "
                    "AND (queue_priority > ? OR (queue_priority = ? AND "
                    "(created_at < ? OR (created_at = ? AND job_id < ?)))) "
                    "AND store NOT IN ("
                    "SELECT store FROM assistant_customer_reply_jobs "
                    "WHERE status = 'running' AND cancel_requested = 0 AND lease_expires_ts > ?"
                    ") LIMIT 1",
                    (
                        _safe_id(job_id, ""), expected_policy, expected_policy,
                        int(row["queue_priority"] or 0), int(row["queue_priority"] or 0),
                        str(row["created_at"] or ""), str(row["created_at"] or ""),
                        _safe_id(job_id, ""), now_ts,
                    ),
                ).fetchone()
                if higher_priority:
                    return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            next_generation = max(
                0,
                int(row["lease_generation"] or data.get("lease_generation") or 0),
            ) + 1
            data.update(
                {
                    "status": "running",
                    "lease_owner": str(owner or ""),
                    "lease_expires_ts": now_ts + max(10.0, float(lease_seconds or 45.0)),
                    "lease_generation": next_generation,
                    "updated_at": _now_iso(),
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET status = 'running', lease_owner = ?, lease_expires_ts = ?,
                    lease_generation = ?, updated_at = ?, payload_json = ?
                WHERE job_id = ?
                """,
                (
                    data["lease_owner"],
                    data["lease_expires_ts"],
                    data["lease_generation"],
                    data["updated_at"],
                    _json_dumps(durable),
                    _safe_id(job_id, ""),
                ),
            )
    return data


def codex_assistant_customer_reply_job_heartbeat(
    info_base: str,
    client_id: str,
    job_id: str,
    *,
    owner: str,
    lease_generation: Optional[int] = None,
    lease_seconds: float = 45.0,
) -> Optional[dict[str, Any]]:
    """Atomically renew an active customer-reply job lease.

    The status and owner checks happen in the same database lock as the update,
    so a late heartbeat can never resurrect a job that already completed.
    """

    now = _now_iso()
    lease_expires = time.time() + max(10.0, float(lease_seconds or 45.0))
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, lease_owner, lease_generation, payload_json "
                "FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (_safe_id(job_id, ""),),
            ).fetchone()
            if not row or str(row["status"] or "") != "running":
                return None
            if str(row["lease_owner"] or "") != str(owner or ""):
                return None
            current_generation = max(0, int(row["lease_generation"] or 0))
            if current_generation > 0 and lease_generation is None:
                return None
            if lease_generation is not None and int(lease_generation) != current_generation:
                return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            data.update(
                {
                    "status": "running",
                    "lease_owner": str(owner or ""),
                    "lease_expires_ts": lease_expires,
                    "lease_generation": current_generation,
                    "heartbeat_at": now,
                    "updated_at": now,
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            updated = conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET lease_expires_ts = ?, updated_at = ?, payload_json = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ? AND lease_generation = ?
                """,
                (
                    lease_expires,
                    now,
                    _json_dumps(durable),
                    _safe_id(job_id, ""),
                    str(owner or ""),
                    current_generation,
                ),
            )
            if int(updated.rowcount or 0) != 1:
                return None
    return data


def codex_assistant_customer_reply_job_request_cancel(
    info_base: str,
    client_id: str,
    job_id: str,
) -> Optional[dict[str, Any]]:
    safe_job_id = _safe_id(job_id, "")
    if not safe_job_id:
        return None
    db_path = codex_assistant_state_db_path(info_base, client_id)
    lock = _lock_for(db_path)
    with lock:
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, payload_json FROM assistant_customer_reply_jobs WHERE job_id = ?",
                (safe_job_id,),
            ).fetchone()
            if not row:
                return None
            data = _json_loads(row["payload_json"], {})
            if not isinstance(data, dict):
                data = {}
            data = _customer_reply_transient_merge(db_path, data)
            if str(row["status"] or "") in {"completed", "cancelled"}:
                return data
            now = _now_iso()
            data.update(
                {
                    "job_id": safe_job_id,
                    "status": "cancelled",
                    "agent_state": "cancelado",
                    "current_step": "cancelar",
                    "cancel_requested": True,
                    "lease_owner": "",
                    "lease_expires_ts": 0.0,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            durable, _transient = _customer_reply_durable_payload(data)
            conn.execute(
                """
                UPDATE assistant_customer_reply_jobs
                SET status = 'cancelled', agent_state = 'cancelado',
                    lease_owner = '', lease_expires_ts = 0,
                    cancel_requested = 1, updated_at = ?, payload_json = ?
                WHERE job_id = ?
                """,
                (now, _json_dumps(durable), safe_job_id),
            )
    with _CUSTOMER_REPLY_TRANSIENT_LOCK:
        _CUSTOMER_REPLY_TRANSIENT.pop(_customer_reply_cache_key(db_path, safe_job_id), None)
    return durable
