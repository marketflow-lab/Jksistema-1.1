from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.context_hub import api as context_hub_api
from backend.modules.context_hub import documents as hub_documents
from backend.modules.context_hub import generations as hub_generations
from backend.modules.context_hub import product_evidence_sync as sync
from backend.modules.context_hub import product_evidence_scheduler as transition_scheduler
from backend.modules.context_hub import storage
from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.locking import _exclusive_file_lock
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence import (
    add_product_evidence_claim,
    add_product_evidence_source,
    complete_product_evidence_batch,
    create_product_evidence_batch,
)
from backend.modules.context_hub.product_evidence_editorial import (
    collect_product_evidence_editorial_snapshot,
)
from backend.modules.context_hub.runtime import _runtime_config, _utc_now, configure_context_hub
from backend.modules.context_hub.state import CONTEXT_HUB_STATE
from backend.modules.context_hub.storage import _connect
from backend.routers.context_hub import create_context_hub_router


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self._value = value
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._value

    def set(self, value: datetime) -> None:
        with self._lock:
            self._value = value


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for product-evidence synchronization")


@pytest.fixture
def sync_env(tmp_path: Path) -> tuple[Path, str]:
    info_root = tmp_path / "info"
    info_root.mkdir()
    configure_context_hub(base_dir=tmp_path, info_root=info_root, surface="test")
    client_id = "tenant-sync"
    bootstrap_context_hub(client_id, info_root=info_root, surface="test")
    yield info_root, client_id

    prefix = str(info_root).casefold()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        workers = [
            value
            for key, value in CONTEXT_HUB_STATE.product_evidence_sync_workers.items()
            if key.startswith(prefix)
        ]
        for key in list(CONTEXT_HUB_STATE.product_evidence_sync_workers):
            if key.startswith(prefix):
                CONTEXT_HUB_STATE.product_evidence_sync_workers.pop(key, None)
    for thread, wake_event, stop_event in workers:
        stop_event.set()
        wake_event.set()
        thread.join(timeout=1.0)


def _paths(info_root: Path, client_id: str):
    return _tenant_paths(client_id, info_root=info_root)


def _mark(paths, *, count: int = 1) -> None:
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _ in range(count):
            sync._mark_product_evidence_sync_pending(connection, debounce_seconds=0)
        connection.commit()


def _outbox(paths) -> sqlite3.Row:
    with _connect(paths) as connection:
        return connection.execute(
            "SELECT * FROM context_hub_product_evidence_outbox WHERE singleton_id=1"
        ).fetchone()


def _insert_attested_generation(
    paths,
    *,
    generation_id: str,
    evidence_revision: int,
    snapshot_hash: str | None = None,
) -> dict:
    with _connect(paths) as connection:
        snapshot = collect_product_evidence_editorial_snapshot(
            connection,
            client_id=paths.client_id,
            as_of=_utc_now(),
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO context_hub_generations(
                generation_id, status, source_hash, source_version, surface,
                reason, created_at, validated_at
            ) VALUES (?, 'ready', ?, 'test', 'test', ?, ?, ?)
            """,
            (
                generation_id,
                hashlib.sha256(b"generation").hexdigest(),
                sync.PRODUCT_EVIDENCE_SYNC_REASON,
                _utc_now(),
                _utc_now(),
            ),
        )
        connection.execute(
            """
            INSERT INTO context_hub_generation_product_evidence(
                generation_id, evidence_revision, snapshot_hash, policy_version,
                captured_at, next_transition_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                evidence_revision,
                snapshot_hash or snapshot["snapshot_hash"],
                snapshot["policy_version"],
                snapshot["captured_at"],
                snapshot["next_transition_at"] or None,
            ),
        )
        connection.commit()
    return snapshot


def test_schema_seeds_tenant_outbox_and_attestation_contract(sync_env) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    with sqlite3.connect(paths.db_path) as connection:
        outbox_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(context_hub_product_evidence_outbox)"
            )
        }
        attestation = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(context_hub_generation_product_evidence)"
            )
        }
        seeded = connection.execute(
            "SELECT requested_revision, completed_revision, attempt_count, "
            "last_error_code FROM context_hub_product_evidence_outbox"
        ).fetchall()

    assert seeded == [(0, 0, 0, "")]
    assert outbox_columns == {
        "singleton_id",
        "requested_revision",
        "completed_revision",
        "completed_generation_id",
        "not_before",
        "attempt_count",
        "last_attempt_at",
        "last_success_at",
        "last_error_code",
        "updated_at",
    }
    assert not outbox_columns & {
        "vin", "question", "answer", "buyer", "prompt", "content", "payload"
    }
    assert attestation["policy_version"][3] == 1
    assert "projection_hash" in attestation
    assert "next_transition_at" in attestation
    assert "projection_next_transition_at" in attestation


def test_migrates_attestation_policy_and_transition_columns() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE context_hub_generation_product_evidence (
                generation_id TEXT PRIMARY KEY,
                evidence_revision INTEGER NOT NULL,
                snapshot_hash TEXT NOT NULL,
                captured_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO context_hub_generation_product_evidence VALUES (?, 3, ?, ?)",
            ("a" * 32, "b" * 64, _utc_now()),
        )
        connection.execute(
            """
            CREATE TABLE context_hub_product_evidence_outbox (
                singleton_id INTEGER PRIMARY KEY,
                requested_revision INTEGER NOT NULL,
                completed_revision INTEGER NOT NULL
            )
            """
        )

        storage._migrate_product_evidence_sync_schema(connection)

        columns = {
            row[1]: row
            for row in connection.execute(
                "PRAGMA table_info(context_hub_generation_product_evidence)"
            )
        }
        migrated = connection.execute(
            "SELECT policy_version, projection_hash, next_transition_at, "
            "projection_next_transition_at "
            "FROM context_hub_generation_product_evidence"
        ).fetchone()
        outbox_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(context_hub_product_evidence_outbox)"
            )
        }
    assert columns["policy_version"][3] == 1
    assert columns["projection_hash"][3] == 1
    assert "next_transition_at" in columns
    assert "projection_next_transition_at" in columns
    assert "completed_generation_id" in outbox_columns
    assert migrated == ("", "", None, None)


def test_pending_revision_is_atomic_and_coalesced_in_one_tenant_row(sync_env) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths, count=2)

    row = _outbox(paths)
    assert row["requested_revision"] == 2
    assert row["completed_revision"] == 0
    with _connect(paths) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM context_hub_product_evidence_outbox"
        ).fetchone()[0] == 1


def test_batch_completion_commits_pending_before_scheduling_outside_locks(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    batch = create_product_evidence_batch(
        client_id,
        store_ref="store-a",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-1",
        info_root=info_root,
    )
    observed: list[int] = []

    def scheduler(_client_id: object, *, info_root: object = None) -> dict:
        del _client_id, info_root
        assert not paths.lock_path.exists()
        with _exclusive_file_lock(paths, timeout=0):
            observed.append(int(_outbox(paths)["requested_revision"]))
        raise RuntimeError("scheduler details must not escape")

    monkeypatch.setattr(sync, "schedule_product_evidence_sync", scheduler)
    result = complete_product_evidence_batch(
        client_id,
        batch["batch_id"],
        coverage_complete=True,
        stop_reason="coverage_complete",
        info_root=info_root,
    )

    assert result["status"] == "completed"
    assert observed == [1]
    row = _outbox(paths)
    assert row["requested_revision"] == 1
    assert row["completed_revision"] == 0


def test_worker_accepts_older_revision_when_current_snapshot_hash_is_identical(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths, count=2)
    generation_id = "a" * 32
    _insert_attested_generation(
        paths,
        generation_id=generation_id,
        evidence_revision=1,
    )
    monkeypatch.setattr(
        sync,
        "_rebuild_product_evidence_projection",
        lambda _config, _paths: {
            "success": True,
            "generation_id": generation_id,
            "status": "ready",
            "idempotent": True,
        },
    )

    result = sync._run_product_evidence_sync_worker(
        _runtime_config(info_root=info_root),
        paths,
        threading.Event(),
        max_attempts=1,
        wait_for_debounce=False,
    )

    row = _outbox(paths)
    assert result == "idle"
    assert row["requested_revision"] == 2
    assert row["completed_revision"] == 2
    assert row["completed_generation_id"] == generation_id
    assert row["attempt_count"] == 0
    assert row["last_error_code"] == ""
    with _connect(paths) as connection:
        assert connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()[0] is None


def test_hash_cas_does_not_clear_a_newer_changed_snapshot(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths)
    generation_id = "c" * 32
    _insert_attested_generation(
        paths,
        generation_id=generation_id,
        evidence_revision=1,
    )
    monkeypatch.setattr(
        sync,
        "schedule_product_evidence_sync",
        lambda *_args, **_kwargs: {"success": True, "started": False},
    )
    batch = create_product_evidence_batch(
        client_id,
        store_ref="store-a",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-2",
        info_root=info_root,
    )
    source = add_product_evidence_source(
        client_id,
        batch["batch_id"],
        url="https://maker.example/sku-2",
        source_type="official_manufacturer",
        content_hash=hashlib.sha256(b"maker-sku-2").hexdigest(),
        info_root=info_root,
    )
    add_product_evidence_claim(
        client_id,
        batch["batch_id"],
        field_name="electrical.voltage",
        scope="product",
        value="12 V",
        source_ids=[source["source_id"]],
        info_root=info_root,
    )
    complete_product_evidence_batch(
        client_id,
        batch["batch_id"],
        coverage_complete=True,
        stop_reason="coverage_complete",
        info_root=info_root,
    )

    outcome = sync._ack_attested_generation(
        paths,
        expected_revision=1,
        generation_id=generation_id,
    )
    row = _outbox(paths)
    assert outcome == "superseded"
    assert row["requested_revision"] == 2
    assert row["completed_revision"] == 0
    assert row["completed_generation_id"] == ""


def test_ack_schedules_and_promotes_the_next_ttl_transition(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths)
    generation_id = "e" * 32
    _insert_attested_generation(
        paths,
        generation_id=generation_id,
        evidence_revision=1,
    )
    next_transition = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat(timespec="microseconds")
    with _connect(paths) as connection:
        connection.execute(
            "UPDATE context_hub_generation_product_evidence "
            "SET next_transition_at=? WHERE generation_id=?",
            (next_transition, generation_id),
        )
    monkeypatch.setattr(sync, "_operational_snapshot_matches", lambda *_args: True)

    assert sync._ack_attested_generation(
        paths,
        expected_revision=1,
        generation_id=generation_id,
    ) == "acked"
    synchronized = _outbox(paths)
    assert synchronized["requested_revision"] == 1
    assert synchronized["completed_revision"] == 1
    assert synchronized["completed_generation_id"] == generation_id
    assert synchronized["not_before"] == next_transition
    assert sync._read_pending(paths).transition_only is True

    expired_transition = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="microseconds")
    with _connect(paths) as connection:
        connection.execute(
            "UPDATE context_hub_product_evidence_outbox SET not_before=? "
            "WHERE singleton_id=1",
            (expired_transition,),
        )
    promoted = sync._wait_for_pending(
        paths,
        threading.Event(),
        wait_for_debounce=False,
    )
    assert promoted is not None
    assert promoted.transition_only is False
    assert promoted.requested_revision == 2
    assert promoted.completed_revision == 1


def test_ttl_transition_builds_expired_ready_without_research_or_auto_publish(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    initial_time = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    clock = _MutableClock(initial_time)
    runtime = _runtime_config(info_root=info_root)
    monkeypatch.setattr(
        CONTEXT_HUB_STATE,
        "runtime_config",
        replace(runtime, clock=clock),
    )
    monkeypatch.setattr(sync, "PRODUCT_EVIDENCE_SYNC_DEBOUNCE_SECONDS", 0.0)
    inventory = {
        "source_version": "ttl-e2e",
        "entities": [{
            "id": "jk:domain:test",
            "kind": "domain",
            "domain": "sistema",
            "title": "Contexto seguro",
            "content": "Conhecimento operacional seguro.",
            "source_refs": ["backend/modules/context_hub"],
            "source_hash": hashlib.sha256(b"ttl-domain").hexdigest(),
        }],
        "findings": [],
        "stats": {"entities": 1},
    }
    monkeypatch.setattr(hub_generations, "_build_inventory", lambda *_args: (inventory, []))
    monkeypatch.setattr(hub_generations, "_collect_curated_notes", lambda *_args: ([], [], []))
    monkeypatch.setattr(
        hub_generations,
        "_load_context_bundle",
        lambda *_args, **_kwargs: ([], [], []),
    )
    monkeypatch.setattr(
        hub_documents.curation_records,
        "_render_inventory",
        lambda _inventory: {
            "70_Gerado/Dominios/Contexto-seguro.md": (
                "---\nid: jk:domain:test\ntype: domain\n---\n\n"
                "# Contexto seguro\n\nConhecimento operacional seguro.\n"
            )
        },
    )

    batch = create_product_evidence_batch(
        client_id,
        store_ref="Loja Teste",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-TTL",
        started_at=initial_time,
        info_root=info_root,
    )
    source = add_product_evidence_source(
        client_id,
        batch["batch_id"],
        url="https://fabricante.example/ficha/ttl",
        source_type="official_manufacturer",
        content_hash=hashlib.sha256(b"ttl-source").hexdigest(),
        collected_at=initial_time,
        info_root=info_root,
    )
    add_product_evidence_claim(
        client_id,
        batch["batch_id"],
        field_name="electrical.voltage",
        scope="product",
        value="12 V",
        source_ids=[source["source_id"]],
        as_of=initial_time,
        info_root=info_root,
    )
    complete_product_evidence_batch(
        client_id,
        batch["batch_id"],
        coverage_complete=True,
        stop_reason="coverage_complete",
        finished_at=initial_time,
        info_root=info_root,
    )

    def synchronized_revision(revision: int) -> bool:
        row = _outbox(paths)
        return int(row["completed_revision"]) >= revision

    try:
        _wait_until(lambda: synchronized_revision(1))
        with _connect(paths) as connection:
            first = connection.execute(
                "SELECT generation_id, status FROM context_hub_generations "
                "WHERE reason=? ORDER BY created_at DESC LIMIT 1",
                (sync.PRODUCT_EVIDENCE_SYNC_REASON,),
            ).fetchone()
            before = collect_product_evidence_editorial_snapshot(
                connection,
                client_id=client_id,
                as_of=initial_time,
            )
            expiry = datetime.fromisoformat(str(_outbox(paths)["not_before"]))
            operational_counts = tuple(connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM product_evidence_batches), "
                "(SELECT COUNT(*) FROM product_evidence_sources), "
                "(SELECT COUNT(*) FROM product_evidence_claims)"
            ).fetchone())
        assert first is not None and first["status"] == "ready"
        assert before["stats"]["verified"] == 1
        assert before["stats"]["expired"] == 0
        assert before["next_transition_at"] == expiry.isoformat(timespec="microseconds")
        published = context_hub_api.publish_generation(
            client_id,
            first["generation_id"],
            info_root=info_root,
        )
        assert published["status"] == "active"

        clock.set(expiry - timedelta(hours=1))
        sync.schedule_product_evidence_sync(client_id, info_root=info_root)
        time.sleep(0.05)
        assert int(_outbox(paths)["completed_revision"]) == 1

        clock.set(expiry)
        sync.schedule_product_evidence_sync(client_id, info_root=info_root)
        _wait_until(lambda: synchronized_revision(2))
        with _connect(paths) as connection:
            generations = connection.execute(
                "SELECT generation_id, status, published_at FROM context_hub_generations "
                "WHERE reason=? ORDER BY created_at, generation_id",
                (sync.PRODUCT_EVIDENCE_SYNC_REASON,),
            ).fetchall()
            after = collect_product_evidence_editorial_snapshot(
                connection,
                client_id=client_id,
                as_of=expiry,
            )
            final_counts = tuple(connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM product_evidence_batches), "
                "(SELECT COUNT(*) FROM product_evidence_sources), "
                "(SELECT COUNT(*) FROM product_evidence_claims)"
            ).fetchone())
        final_outbox = _outbox(paths)
        active = [row for row in generations if row["status"] == "active"]
        ready = [row for row in generations if row["status"] == "ready"]
        assert len(generations) == 2
        assert [row["generation_id"] for row in active] == [first["generation_id"]]
        assert len(ready) == 1 and ready[0]["published_at"] is None
        assert after["stats"]["verified"] == 0
        assert after["stats"]["expired"] == 1
        assert after["snapshot_hash"] != before["snapshot_hash"]
        assert after["next_transition_at"] == ""
        assert final_counts == operational_counts == (1, 1, 1)
        assert int(final_outbox["requested_revision"]) == 2
        assert int(final_outbox["completed_revision"]) == 2
        assert final_outbox["not_before"] == ""
        expired_root = paths.generations_dir / ready[0]["generation_id"] / "70_Gerado"
        assert any("Expiradas" in path.parts for path in expired_root.rglob("*.md"))
    finally:
        sync.stop_all_product_evidence_sync_workers()


def test_snapshot_mismatch_and_exception_leave_revision_pending_without_details(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths)
    generation_id = "b" * 32
    _insert_attested_generation(
        paths,
        generation_id=generation_id,
        evidence_revision=1,
        snapshot_hash="f" * 64,
    )
    monkeypatch.setattr(sync, "PRODUCT_EVIDENCE_SYNC_RETRY_DELAYS", (0.0,))
    monkeypatch.setattr(
        sync,
        "_rebuild_product_evidence_projection",
        lambda _config, _paths: {"success": True, "generation_id": generation_id},
    )
    assert sync._run_product_evidence_sync_worker(
        _runtime_config(info_root=info_root),
        paths,
        threading.Event(),
        max_attempts=1,
        wait_for_debounce=False,
    ) == "backoff"
    mismatch = _outbox(paths)
    assert mismatch["requested_revision"] == 1
    assert mismatch["completed_revision"] == 0
    assert mismatch["last_error_code"] == "snapshot_mismatch"

    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        sync._mark_product_evidence_sync_pending(connection, debounce_seconds=0)
        connection.commit()

    def fail_without_leaking(_config, _paths):
        raise RuntimeError("VIN 1HGBH41JXMN109186 buyer@example.test")

    monkeypatch.setattr(sync, "_rebuild_product_evidence_projection", fail_without_leaking)
    assert sync._run_product_evidence_sync_worker(
        _runtime_config(info_root=info_root),
        paths,
        threading.Event(),
        max_attempts=1,
        wait_for_debounce=False,
    ) == "backoff"
    failed = _outbox(paths)
    assert failed["requested_revision"] == 2
    assert failed["completed_revision"] == 0
    assert failed["last_error_code"] == "rebuild_exception"
    with sqlite3.connect(paths.db_path) as connection:
        serialized = " ".join(str(value) for value in connection.execute(
            "SELECT * FROM context_hub_product_evidence_outbox"
        ).fetchone())
    assert "1HGBH41JXMN109186" not in serialized
    assert "buyer@example.test" not in serialized


def test_scheduler_uses_one_daemon_and_coalesces_wakeups(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    started = threading.Event()
    release = threading.Event()

    def blocked_worker(_config, _paths, _wake_event, _stop_event) -> None:
        started.set()
        release.wait(2)

    monkeypatch.setattr(sync, "_worker_entry", blocked_worker)
    first = sync.schedule_product_evidence_sync(client_id, info_root=info_root)
    assert started.wait(1)
    second = sync.schedule_product_evidence_sync(client_id, info_root=info_root)
    key = str(_paths(info_root, client_id).internal_dir).casefold()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        thread = CONTEXT_HUB_STATE.product_evidence_sync_workers[key][0]
    assert first["started"] is True
    assert second == {
        "success": True,
        "client_id": client_id,
        "started": False,
        "coalesced": True,
    }
    assert thread.daemon is True
    release.set()
    thread.join(timeout=2)
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        CONTEXT_HUB_STATE.product_evidence_sync_workers.pop(key, None)


def test_future_ttl_wakeups_share_one_scheduler_without_tenant_pollers(
    sync_env,
) -> None:
    info_root, client_id = sync_env
    second_client = "tenant-sync-second"
    bootstrap_context_hub(second_client, info_root=info_root, surface="test")
    first_paths = _paths(info_root, client_id)
    second_paths = _paths(info_root, second_client)
    fired: list[str] = []
    due = (datetime.now(timezone.utc) + timedelta(milliseconds=100)).isoformat(
        timespec="microseconds"
    )

    try:
        assert transition_scheduler.schedule_product_evidence_transition(
            first_paths,
            due,
            lambda: fired.append(client_id),
        )
        assert transition_scheduler.schedule_product_evidence_transition(
            second_paths,
            due,
            lambda: fired.append(second_client),
        )
        with CONTEXT_HUB_STATE.product_evidence_sync_guard:
            registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
            scheduled_count = len(
                CONTEXT_HUB_STATE.product_evidence_transition_schedule
            )
            tenant_workers = len(CONTEXT_HUB_STATE.product_evidence_sync_workers)
        assert registered is not None and registered[0].daemon is True
        assert scheduled_count == 2
        assert tenant_workers == 0

        _wait_until(lambda: len(fired) == 2)
        assert set(fired) == {client_id, second_client}
    finally:
        transition_scheduler.stop_product_evidence_transition_scheduler()


def test_ttl_scheduler_retains_failed_callback_and_retries_with_backoff(
    sync_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    attempts: list[int] = []
    monkeypatch.setattr(transition_scheduler, "_RETRY_BASE_SECONDS", 0.1)
    monkeypatch.setattr(transition_scheduler, "_RETRY_MAX_SECONDS", 0.1)

    def fail_once() -> dict[str, bool]:
        attempts.append(len(attempts) + 1)
        return {"success": len(attempts) > 1}

    due = (datetime.now(timezone.utc) + timedelta(milliseconds=20)).isoformat(
        timespec="microseconds"
    )
    try:
        assert transition_scheduler.schedule_product_evidence_transition(
            paths, due, fail_once
        )
        key = str(paths.internal_dir).casefold()
        _wait_until(
            lambda: bool(
                key in CONTEXT_HUB_STATE.product_evidence_transition_schedule
                and CONTEXT_HUB_STATE.product_evidence_transition_schedule[key].attempts == 1
            )
        )
        with CONTEXT_HUB_STATE.product_evidence_sync_guard:
            assert len(CONTEXT_HUB_STATE.product_evidence_sync_workers) == 0
            assert CONTEXT_HUB_STATE.product_evidence_transition_scheduler is not None

        _wait_until(lambda: len(attempts) == 2)
        _wait_until(
            lambda: key not in CONTEXT_HUB_STATE.product_evidence_transition_schedule
        )
    finally:
        transition_scheduler.stop_product_evidence_transition_scheduler()


def test_persistent_worker_recovers_after_exhausted_attempts(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    _mark(paths)
    generation_id = "d" * 32
    _insert_attested_generation(
        paths,
        generation_id=generation_id,
        evidence_revision=1,
    )
    calls = 0

    def eventually_succeeds(_config, _paths):
        nonlocal calls
        calls += 1
        if calls <= sync.PRODUCT_EVIDENCE_SYNC_MAX_ATTEMPTS:
            raise RuntimeError("transient provider details")
        return {"success": True, "generation_id": generation_id}

    monkeypatch.setattr(sync, "_rebuild_product_evidence_projection", eventually_succeeds)
    monkeypatch.setattr(sync, "PRODUCT_EVIDENCE_SYNC_RETRY_DELAYS", (0.0, 0.0, 0.0))
    monkeypatch.setattr(sync, "PRODUCT_EVIDENCE_SYNC_EXHAUSTED_BACKOFF_SECONDS", 0.01)
    monkeypatch.setattr(sync, "PRODUCT_EVIDENCE_SYNC_MAX_BACKOFF_SECONDS", 0.01)

    scheduled = sync.schedule_product_evidence_sync(client_id, info_root=info_root)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if int(_outbox(paths)["completed_revision"]) == 1:
            break
        time.sleep(0.01)

    row = _outbox(paths)
    assert scheduled["started"] is True
    assert calls >= sync.PRODUCT_EVIDENCE_SYNC_MAX_ATTEMPTS + 1
    assert row["completed_revision"] == 1
    assert row["completed_generation_id"] == generation_id
    assert row["last_error_code"] == ""


def test_startup_recovery_reschedules_commit_left_pending_after_restart(
    sync_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root, client_id = sync_env
    paths = _paths(info_root, client_id)
    batch = create_product_evidence_batch(
        client_id,
        store_ref="store-a",
        seller_id="seller-a",
        site_id="MLB",
        sku="SKU-RESTART",
        info_root=info_root,
    )
    monkeypatch.setattr(
        sync,
        "schedule_product_evidence_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("process stopped")),
    )
    complete_product_evidence_batch(
        client_id,
        batch["batch_id"],
        coverage_complete=True,
        stop_reason="coverage_complete",
        info_root=info_root,
    )
    bootstrap_context_hub("tenant-complete", info_root=info_root, surface="test")
    (info_root / "Tenant Invalid").mkdir()
    assert _outbox(paths)["requested_revision"] == 1
    assert _outbox(paths)["completed_revision"] == 0

    recovered: list[tuple[str, bool]] = []

    def record_schedule(
        recovered_client: object,
        *,
        info_root: object = None,
        bootstrap_if_missing: bool = True,
    ) -> dict:
        del info_root
        recovered.append((str(recovered_client), bootstrap_if_missing))
        return {
            "success": True,
            "client_id": str(recovered_client),
            "started": True,
            "coalesced": False,
        }

    monkeypatch.setattr(sync, "schedule_product_evidence_sync", record_schedule)
    result = sync.recover_pending_product_evidence_syncs(info_root=info_root)

    assert result["success"] is True
    assert result["tenants_scanned"] == 2
    assert result["pending_found"] == 1
    assert result["scheduled"] == 1
    assert result["skipped"] >= 1
    assert recovered == [(client_id, False)]


def test_router_lifecycle_recovers_and_stops_product_evidence_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info_root = tmp_path / "info"
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        context_hub_api,
        "recover_pending_product_evidence_syncs",
        lambda *, info_root=None: calls.append(("startup", info_root)),
    )
    monkeypatch.setattr(
        context_hub_api,
        "stop_all_product_evidence_sync_workers",
        lambda: calls.append(("shutdown", None)),
    )
    app = FastAPI()
    app.include_router(
        create_context_hub_router(
            base_dir=tmp_path,
            info_root=info_root,
            surface="test",
        )
    )

    with TestClient(app):
        pass

    assert calls == [("startup", info_root), ("shutdown", None)]
