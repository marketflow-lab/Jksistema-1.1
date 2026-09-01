"""Durable, coalesced synchronization of product evidence into Context Hub.

The operational product-evidence tables remain authoritative.  This module
only schedules a normal Context Hub rebuild and acknowledges an outbox
revision after the returned generation is attested against the current
operational snapshot.  It never publishes a generation.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.contracts import (
    ContextHubPaths,
    ContextHubRuntimeConfig,
    ContextHubValidationError,
)
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_link_or_junction,
    _snapshot_info_root,
)
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence_scheduler import (
    cancel_product_evidence_transition,
    schedule_product_evidence_transition,
    stop_product_evidence_transition_scheduler,
)
from backend.modules.context_hub.runtime import _runtime_config, _utc_now
from backend.modules.context_hub.state import CONTEXT_HUB_STATE
from backend.modules.context_hub.storage import _connect


PRODUCT_EVIDENCE_SYNC_REASON = "product_evidence_sync"
PRODUCT_EVIDENCE_SYNC_DEBOUNCE_SECONDS = 0.25
PRODUCT_EVIDENCE_SYNC_MAX_ATTEMPTS = 3
PRODUCT_EVIDENCE_SYNC_RETRY_DELAYS = (0.25, 1.0, 4.0)
PRODUCT_EVIDENCE_SYNC_EXHAUSTED_BACKOFF_SECONDS = 30.0
PRODUCT_EVIDENCE_SYNC_MAX_BACKOFF_SECONDS = 300.0

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_GENERATION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_POLICY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_ERROR_CODES = frozenset(
    {
        "attestation_invalid",
        "attestation_missing",
        "cas_conflict",
        "generation_missing",
        "rebuild_exception",
        "rebuild_failed",
        "snapshot_mismatch",
        "snapshot_unavailable",
    }
)


@dataclass(frozen=True)
class _PendingSync:
    requested_revision: int
    completed_revision: int
    not_before: str
    attempt_count: int
    transition_only: bool = False


@dataclass(frozen=True)
class _AttestedSnapshot:
    snapshot_hash: str
    projection_hash: str
    policy_version: str
    next_transition_at: str
    projection_next_transition_at: str


def _as_utc(value: object) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _mark_product_evidence_sync_pending(
    connection: sqlite3.Connection,
    *,
    debounce_seconds: Optional[float] = None,
) -> int:
    """Increment the tenant-local revision inside the caller's transaction."""

    debounce = (
        PRODUCT_EVIDENCE_SYNC_DEBOUNCE_SECONDS
        if debounce_seconds is None
        else max(0.0, float(debounce_seconds))
    )
    now = _as_utc(_utc_now())
    not_before = _iso(now + timedelta(seconds=debounce))
    updated = connection.execute(
        """
        UPDATE context_hub_product_evidence_outbox
           SET requested_revision=requested_revision+1,
               not_before=?, attempt_count=0, last_error_code='', updated_at=?
         WHERE singleton_id=1
        """,
        (not_before, _iso(now)),
    )
    if updated.rowcount != 1:
        connection.execute(
            """
            INSERT INTO context_hub_product_evidence_outbox(
                singleton_id, requested_revision, completed_revision, not_before,
                attempt_count, last_error_code, updated_at
            ) VALUES (1, 1, 0, ?, 0, '', ?)
            """,
            (not_before, _iso(now)),
        )
    row = connection.execute(
        "SELECT requested_revision FROM context_hub_product_evidence_outbox "
        "WHERE singleton_id=1"
    ).fetchone()
    return int(row[0])


def _read_pending(paths: ContextHubPaths) -> Optional[_PendingSync]:
    try:
        with _connect(paths) as connection:
            row = connection.execute(
                """
                SELECT requested_revision, completed_revision, not_before, attempt_count
                  FROM context_hub_product_evidence_outbox WHERE singleton_id=1
                """
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if row is None:
        return None
    requested_revision = int(row["requested_revision"])
    completed_revision = int(row["completed_revision"])
    not_before = str(row["not_before"] or "")
    if completed_revision > requested_revision:
        return None
    transition_only = completed_revision == requested_revision
    if transition_only and not not_before:
        return None
    return _PendingSync(
        requested_revision=requested_revision,
        completed_revision=completed_revision,
        not_before=not_before,
        attempt_count=int(row["attempt_count"]),
        transition_only=transition_only,
    )


def _promote_due_transition(paths: ContextHubPaths, pending: _PendingSync) -> bool:
    now = _utc_now()
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE context_hub_product_evidence_outbox
                   SET requested_revision=requested_revision+1, not_before=?,
                       attempt_count=0, last_error_code='', updated_at=?
                 WHERE singleton_id=1 AND requested_revision=?
                   AND completed_revision=? AND not_before=? AND not_before!=''
                   AND not_before<=?
                """,
                (
                    now,
                    now,
                    pending.requested_revision,
                    pending.completed_revision,
                    pending.not_before,
                    now,
                ),
            )
            connection.commit()
    return updated.rowcount == 1


def _wait_for_pending(
    paths: ContextHubPaths,
    wake_event: threading.Event,
    *,
    wait_for_debounce: bool,
    stop_event: Optional[threading.Event] = None,
) -> Optional[_PendingSync]:
    while True:
        if stop_event is not None and stop_event.is_set():
            return None
        pending = _read_pending(paths)
        if pending is None:
            return pending
        delay = (_as_utc(pending.not_before) - _as_utc(_utc_now())).total_seconds()
        if pending.transition_only:
            if delay <= 0:
                _promote_due_transition(paths, pending)
                continue
            return pending
        elif not wait_for_debounce:
            return pending
        if delay <= 0:
            return pending
        wake_event.wait(min(delay, 1.0))
        if stop_event is not None and stop_event.is_set():
            return None
        wake_event.clear()


def _record_attempt(paths: ContextHubPaths, expected_revision: int) -> Optional[int]:
    now = _utc_now()
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE context_hub_product_evidence_outbox
                   SET attempt_count=attempt_count+1, last_attempt_at=?, updated_at=?
                 WHERE singleton_id=1 AND requested_revision=?
                   AND completed_revision<requested_revision
                """,
                (now, now, expected_revision),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            row = connection.execute(
                "SELECT attempt_count FROM context_hub_product_evidence_outbox "
                "WHERE singleton_id=1"
            ).fetchone()
            connection.commit()
    return int(row[0])


def _record_failure(
    paths: ContextHubPaths,
    expected_revision: int,
    error_code: str,
    *,
    retry_delay: float,
) -> bool:
    if error_code not in _ERROR_CODES:
        error_code = "rebuild_failed"
    now = _as_utc(_utc_now())
    retry_at = _iso(now + timedelta(seconds=max(0.0, retry_delay)))
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT not_before FROM context_hub_product_evidence_outbox "
                "WHERE singleton_id=1 AND requested_revision=? "
                "AND completed_revision<requested_revision",
                (expected_revision,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            not_before = max(str(row["not_before"] or ""), retry_at)
            updated = connection.execute(
                """
                UPDATE context_hub_product_evidence_outbox
                   SET not_before=?, last_error_code=?, updated_at=?
                 WHERE singleton_id=1 AND requested_revision=?
                   AND completed_revision<requested_revision
                """,
                (not_before, error_code, _iso(now), expected_revision),
            )
            connection.commit()
    return updated.rowcount == 1


def _normalized_transition(value: object) -> str:
    return str(value or "").strip()


def _validated_attestation(
    connection: sqlite3.Connection,
    generation_id: str,
    requested_revision: int,
) -> tuple[str, Optional[_AttestedSnapshot]]:
    row = connection.execute(
        """
        SELECT a.evidence_revision, a.snapshot_hash, a.projection_hash,
               a.policy_version, a.captured_at, a.next_transition_at,
               a.projection_next_transition_at, g.status
          FROM context_hub_generation_product_evidence a
          JOIN context_hub_generations g ON g.generation_id=a.generation_id
         WHERE a.generation_id=?
        """,
        (generation_id,),
    ).fetchone()
    if row is None:
        return "attestation_missing", None
    snapshot_hash = str(row["snapshot_hash"] or "").strip().lower()
    projection_hash = str(row["projection_hash"] or "").strip().lower()
    policy_version = str(row["policy_version"] or "").strip()
    try:
        attested_revision = int(row["evidence_revision"])
    except (TypeError, ValueError):
        return "attestation_invalid", None
    if (
        str(row["status"] or "") not in {"ready", "active"}
        or not _HASH_RE.fullmatch(snapshot_hash)
        or bool(projection_hash and not _HASH_RE.fullmatch(projection_hash))
        or not _POLICY_RE.fullmatch(policy_version)
        or attested_revision < 0
        or attested_revision > requested_revision
        or not str(row["captured_at"] or "").strip()
    ):
        return "attestation_invalid", None
    return "", _AttestedSnapshot(
        snapshot_hash=snapshot_hash,
        projection_hash=projection_hash,
        policy_version=policy_version,
        next_transition_at=_normalized_transition(row["next_transition_at"]),
        projection_next_transition_at=_normalized_transition(
            row["projection_next_transition_at"] or row["next_transition_at"]
        ),
    )


def _operational_snapshot_matches(
    connection: sqlite3.Connection,
    paths: ContextHubPaths,
    attestation: _AttestedSnapshot,
) -> Optional[bool]:
    try:
        from backend.modules.context_hub.product_evidence_editorial import (
            collect_product_evidence_editorial_snapshot,
        )

        operational = collect_product_evidence_editorial_snapshot(
            connection,
            client_id=paths.client_id,
            as_of=_utc_now(),
        )
    except Exception:
        return None
    operational_projection_hash = str(
        operational.get("projection_hash")
        or operational.get("snapshot_hash")
        or ""
    ).strip().lower()
    projection_matches = (
        operational_projection_hash == attestation.projection_hash
        if attestation.projection_hash
        else list(operational.get("editorial_identities") or [])
        == list(operational.get("identities") or [])
    )
    return (
        str(operational.get("snapshot_hash") or "").strip().lower()
        == attestation.snapshot_hash
        and projection_matches
        and str(operational.get("policy_version") or "").strip()
        == attestation.policy_version
        and _normalized_transition(operational.get("next_transition_at"))
        == attestation.next_transition_at
        and _normalized_transition(
            operational.get("projection_next_transition_at")
            or operational.get("next_transition_at")
        )
        == attestation.projection_next_transition_at
    )


def _ack_attested_generation(
    paths: ContextHubPaths,
    *,
    expected_revision: int,
    generation_id: object,
) -> str:
    """Acknowledge the current revision only under a snapshot-hash CAS."""

    normalized_generation = str(generation_id or "").strip().lower()
    if not _GENERATION_ID_RE.fullmatch(normalized_generation):
        return "generation_missing"
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            outbox = connection.execute(
                """
                SELECT requested_revision, completed_revision
                  FROM context_hub_product_evidence_outbox WHERE singleton_id=1
                """
            ).fetchone()
            if outbox is None:
                connection.rollback()
                return "cas_conflict"
            requested_revision = int(outbox["requested_revision"])
            completed_revision = int(outbox["completed_revision"])
            if completed_revision >= requested_revision:
                connection.rollback()
                return "idle"

            attestation_error, attestation = _validated_attestation(
                connection,
                normalized_generation,
                requested_revision,
            )
            if attestation is None:
                connection.rollback()
                return (
                    "superseded"
                    if requested_revision != expected_revision
                    else attestation_error
                )
            matches = _operational_snapshot_matches(connection, paths, attestation)
            if matches is None:
                connection.rollback()
                return (
                    "superseded"
                    if requested_revision != expected_revision
                    else "snapshot_unavailable"
                )
            if not matches:
                connection.rollback()
                return (
                    "superseded"
                    if requested_revision != expected_revision
                    else "snapshot_mismatch"
                )

            now = _utc_now()
            updated = connection.execute(
                """
                UPDATE context_hub_product_evidence_outbox
                   SET completed_revision=?, completed_generation_id=?,
                       attempt_count=0, last_success_at=?, last_error_code='',
                       not_before=?, updated_at=?
                 WHERE singleton_id=1 AND requested_revision=?
                   AND completed_revision=?
                """,
                (
                    requested_revision,
                    normalized_generation,
                    now,
                    attestation.projection_next_transition_at,
                    now,
                    requested_revision,
                    completed_revision,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return "cas_conflict"
            connection.commit()
    return "acked"


def _rebuild_product_evidence_projection(
    config: ContextHubRuntimeConfig,
    paths: ContextHubPaths,
) -> dict[str, Any]:
    from backend.modules.context_hub.generations import rebuild_context

    return rebuild_context(
        paths.client_id,
        reason=PRODUCT_EVIDENCE_SYNC_REASON,
        base_dir=config.base_dir,
        info_root=config.info_root,
        surface=config.surface,
    )


def _retry_delay(attempt: int, attempt_limit: int) -> float:
    if attempt < attempt_limit:
        retry_index = min(
            max(0, attempt - 1),
            len(PRODUCT_EVIDENCE_SYNC_RETRY_DELAYS) - 1,
        )
        return max(0.0, float(PRODUCT_EVIDENCE_SYNC_RETRY_DELAYS[retry_index]))
    exhausted_round = min(max(0, attempt - attempt_limit), 8)
    return min(
        max(0.0, float(PRODUCT_EVIDENCE_SYNC_MAX_BACKOFF_SECONDS)),
        max(0.0, float(PRODUCT_EVIDENCE_SYNC_EXHAUSTED_BACKOFF_SECONDS))
        * (2 ** exhausted_round),
    )


def _run_product_evidence_sync_worker(
    config: ContextHubRuntimeConfig,
    paths: ContextHubPaths,
    wake_event: threading.Event,
    *,
    max_attempts: int = PRODUCT_EVIDENCE_SYNC_MAX_ATTEMPTS,
    wait_for_debounce: bool = True,
    stop_event: Optional[threading.Event] = None,
) -> str:
    """Drain one tenant's outbox; retained failures remain durably pending."""

    attempt_limit = max(1, int(max_attempts))
    while True:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        pending = _wait_for_pending(
            paths,
            wake_event,
            wait_for_debounce=wait_for_debounce,
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        if pending is None:
            return "idle"
        if pending.transition_only:
            scheduled = schedule_product_evidence_transition(
                paths,
                pending.not_before,
                lambda: schedule_product_evidence_sync(
                    paths.client_id,
                    info_root=config.info_root,
                    bootstrap_if_missing=False,
                ),
            )
            if scheduled:
                return "scheduled"
            continue
        attempt = _record_attempt(paths, pending.requested_revision)
        if attempt is None:
            continue
        try:
            generation = _rebuild_product_evidence_projection(config, paths)
        except Exception:
            outcome = "rebuild_exception"
        else:
            if not generation.get("success"):
                outcome = "rebuild_failed"
            elif not generation.get("generation_id"):
                outcome = "generation_missing"
            else:
                outcome = _ack_attested_generation(
                    paths,
                    expected_revision=pending.requested_revision,
                    generation_id=generation.get("generation_id"),
                )
        if outcome in {"acked", "idle", "superseded", "cas_conflict"}:
            if outcome == "cas_conflict":
                continue
            continue

        retained = _record_failure(
            paths,
            pending.requested_revision,
            outcome,
            retry_delay=_retry_delay(attempt, attempt_limit),
        )
        if retained and attempt >= attempt_limit:
            return "backoff"


def _worker_entry(
    config: ContextHubRuntimeConfig,
    paths: ContextHubPaths,
    wake_event: threading.Event,
    stop_event: threading.Event,
) -> None:
    key = str(paths.internal_dir).casefold()
    current = threading.current_thread()
    while True:
        result = _run_product_evidence_sync_worker(
            config,
            paths,
            wake_event,
            stop_event=stop_event,
        )
        with CONTEXT_HUB_STATE.product_evidence_sync_guard:
            registered = CONTEXT_HUB_STATE.product_evidence_sync_workers.get(key)
            if not registered or registered[0] is not current:
                return
            if stop_event.is_set() or result == "stopped":
                CONTEXT_HUB_STATE.product_evidence_sync_workers.pop(key, None)
                return
            if wake_event.is_set():
                wake_event.clear()
                continue
            if result == "backoff":
                continue
            if result == "idle" and _read_pending(paths) is not None:
                continue
            CONTEXT_HUB_STATE.product_evidence_sync_workers.pop(key, None)
            return


def schedule_product_evidence_sync(
    client_id: object,
    *,
    info_root: Optional[object] = None,
    bootstrap_if_missing: bool = True,
) -> dict[str, Any]:
    """Start or wake a single daemon worker for one tenant."""

    config = _runtime_config(info_root=info_root)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    _assert_path_chain_safe(paths.db_path, paths.info_root)
    if not paths.db_path.is_file():
        if not bootstrap_if_missing:
            return {
                "success": False,
                "client_id": paths.client_id,
                "started": False,
                "coalesced": False,
                "reason": "database_missing",
            }
        bootstrap_context_hub(
            paths.client_id,
            base_dir=config.base_dir,
            info_root=config.info_root,
            surface=config.surface,
        )
    cancel_product_evidence_transition(paths)
    key = str(paths.internal_dir).casefold()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        existing = CONTEXT_HUB_STATE.product_evidence_sync_workers.get(key)
        if existing and existing[0].is_alive():
            existing[1].set()
            return {
                "success": True,
                "client_id": paths.client_id,
                "started": False,
                "coalesced": True,
            }
        wake_event = threading.Event()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_worker_entry,
            args=(config, paths, wake_event, stop_event),
            name="context-hub-product-evidence-sync",
            daemon=True,
        )
        CONTEXT_HUB_STATE.product_evidence_sync_workers[key] = (
            thread,
            wake_event,
            stop_event,
        )
        thread.start()
    return {
        "success": True,
        "client_id": paths.client_id,
        "started": True,
        "coalesced": False,
    }


def _pending_revision_readonly(paths: ContextHubPaths) -> Optional[bool]:
    try:
        resolved = paths.db_path.resolve(strict=True)
        uri = f"{resolved.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            row = connection.execute(
                "SELECT requested_revision, completed_revision, not_before "
                "FROM context_hub_product_evidence_outbox WHERE singleton_id=1"
            ).fetchone()
    except (OSError, sqlite3.Error, ValueError):
        return None
    if row is None:
        return None
    try:
        requested_revision = int(row[0])
        completed_revision = int(row[1])
    except (TypeError, ValueError):
        return None
    if (
        requested_revision < 0
        or completed_revision < 0
        or completed_revision > requested_revision
    ):
        return None
    return requested_revision > completed_revision or (
        requested_revision == completed_revision and bool(str(row[2] or "").strip())
    )


def recover_pending_product_evidence_syncs(
    *,
    info_root: Optional[object] = None,
) -> dict[str, Any]:
    """Find valid tenant databases and resume only their pending outboxes."""

    counters = {
        "tenants_scanned": 0,
        "pending_found": 0,
        "scheduled": 0,
        "coalesced": 0,
        "skipped": 0,
        "errors": 0,
    }
    try:
        config = _runtime_config(info_root=info_root)
        root = _snapshot_info_root(config.info_root).resolved
    except (OSError, ContextHubValidationError):
        return {"success": False, "error_code": "info_root_invalid", **counters}
    if not root.exists():
        return {"success": True, **counters}
    try:
        candidates = tuple(root.iterdir())
    except OSError:
        return {"success": False, "error_code": "info_root_unavailable", **counters}

    for candidate in candidates:
        try:
            if _is_link_or_junction(candidate) or not candidate.is_dir():
                counters["skipped"] += 1
                continue
            paths = _tenant_paths(candidate.name, info_root=root)
            if paths.tenant_dir.absolute() != candidate.absolute():
                counters["skipped"] += 1
                continue
            _assert_path_chain_safe(paths.db_path, root)
            if not paths.db_path.is_file() or _is_link_or_junction(paths.db_path):
                counters["skipped"] += 1
                continue
            pending = _pending_revision_readonly(paths)
        except (OSError, ContextHubValidationError):
            counters["skipped"] += 1
            continue
        if pending is None:
            counters["errors"] += 1
            continue
        counters["tenants_scanned"] += 1
        if not pending:
            continue
        counters["pending_found"] += 1
        try:
            scheduled = schedule_product_evidence_sync(
                paths.client_id,
                info_root=root,
                bootstrap_if_missing=False,
            )
        except Exception:
            counters["errors"] += 1
            continue
        if not scheduled.get("success"):
            counters["errors"] += 1
        elif scheduled.get("started"):
            counters["scheduled"] += 1
        elif scheduled.get("coalesced"):
            counters["coalesced"] += 1
    return {"success": True, **counters}


def stop_all_product_evidence_sync_workers() -> None:
    """Stop persistent daemon workers without changing their durable outboxes."""

    stop_product_evidence_transition_scheduler()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        workers = list(CONTEXT_HUB_STATE.product_evidence_sync_workers.values())
        CONTEXT_HUB_STATE.product_evidence_sync_workers.clear()
    for thread, wake_event, stop_event in workers:
        stop_event.set()
        wake_event.set()
        thread.join(timeout=1.0)
    stop_product_evidence_transition_scheduler()


__all__ = [
    "PRODUCT_EVIDENCE_SYNC_REASON",
    "recover_pending_product_evidence_syncs",
    "schedule_product_evidence_sync",
    "stop_all_product_evidence_sync_workers",
]
