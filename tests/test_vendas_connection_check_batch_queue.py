from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from backend.modules.vendas import sync_queue
from backend.modules.vendas.sync_queue import SyncQueue


NOW = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc).timestamp()
ERROR = "A integracao esta indisponivel."


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("tenant-a", tenant_path=tmp_path / "tenant-a")


def enqueue(queue, store="store-0", *, now=NOW, verify=False, **kwargs):
    return queue.enqueue(store, "Loja " + store, "2026-10-01", "2026-10-02",
                         now=now, verify_connections=verify, **kwargs)


def failed(queue, store="store-0", *, now=NOW, requires_session=True):
    job = enqueue(queue, store, now=now, automatico=True, requires_session=requires_session)
    claimed = queue.claim("previous-worker", now=now)
    assert claimed["id"] == job["id"]
    queue.complete_day(job["id"], "previous-worker", "2026-10-01", {"vendas": 3}, now=now + 1)
    return queue.fail_job(job["id"], "previous-worker", ERROR, retryable=False, now=now + 2)


def prepared(queue):
    job = failed(queue)
    queue.pause_all(now=NOW + 3)
    enqueue(queue, now=NOW + 4, verify=True)
    batch = queue.claim_connection_check("checker", now=NOW + 5, lease_seconds=20)
    assert batch and len(batch["stores"]) == 1
    return job, batch


def saved_plans(queue):
    with sqlite3.connect(queue.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY store_id")]


def acknowledge(queue, batch, *, owner="checker", now=NOW + 6):
    return sum(queue.acknowledge_checked_connection(
        owner, batch["generation"], store["store_id"], store["alerts"], now=now,
    ) for store in batch["stores"])


def test_clean_enqueue_does_not_add_a_connection_barrier(queue):
    job = enqueue(queue, verify=True)
    assert not queue.connection_check_pending()
    assert queue.claim_connection_check("checker", now=NOW) is None
    assert queue.summary()["verificando_conexoes"] is False
    assert queue.claim("collector", now=NOW)["id"] == job["id"]


def test_all_eleven_alerted_stores_are_checked_before_any_collection_claim(queue):
    for index in range(11):
        failed(queue, f"store-{index:02}", now=NOW + index * 3)
    queue.pause_all(now=NOW + 40)
    requested = enqueue(queue, "store-00", verify=True, now=NOW + 41)
    jobs_before, plans_before = queue.list_jobs(), saved_plans(queue)

    assert queue.claim("collector", now=NOW + 42, limit=50) is None
    batch = queue.claim_connection_check("checker", now=NOW + 42)
    assert len(batch["stores"]) == 11
    assert queue.summary()["verificando_conexoes"] is True
    for store in batch["stores"]:
        assert queue.acknowledge_checked_connection(
            "checker", batch["generation"], store["store_id"], store["alerts"], now=NOW + 43) == 1
        assert queue.claim("collector", now=NOW + 43) is None
    assert queue.list_jobs() == jobs_before
    assert saved_plans(queue) == plans_before
    assert all(not queue.connection_alerts(store["store_id"]) for store in batch["stores"])
    assert queue.complete_connection_check("checker", batch["generation"], now=NOW + 44)
    claimed = queue.claim("collector", now=NOW + 45)
    assert claimed["id"] == requested["id"]
    assert claimed["last_error"] == ERROR
    assert claimed["dias_concluidos"] == ["2026-10-01"]
    assert queue.connection_alerts("store-00") == []
    assert queue.summary()["verificando_conexoes"] is False
    assert sum(job["status"] == "paused" for job in queue.list_jobs()) == 10


def test_batch_waits_for_preexisting_collector_then_snapshots_its_new_failure(queue):
    failed(queue)
    active = enqueue(queue, "already-running", now=NOW + 3)
    queue.claim("collector", now=NOW + 3, lease_seconds=10)
    enqueue(queue, "requested", now=NOW + 4, verify=True)
    assert queue.claim_connection_check("checker", now=NOW + 5) is None
    assert queue.claim("second-collector", now=NOW + 5) is None
    queue.fail_job(active["id"], "collector", ERROR, retryable=False, now=NOW + 6)
    batch = queue.claim_connection_check("checker", now=NOW + 7)
    assert {store["store_id"] for store in batch["stores"]} == {"store-0", "already-running"}
    with pytest.raises(RuntimeError):
        queue.fail_job(active["id"], "collector", ERROR, now=NOW + 8)


def test_generation_does_not_finish_a_new_request_arriving_during_the_batch(queue):
    _, batch = prepared(queue)
    enqueue(queue, "new-request", now=NOW + 6, verify=True)
    assert queue.owns_connection_check("checker", batch["generation"], now=NOW + 7)
    assert acknowledge(queue, batch, now=NOW + 7) == 1
    assert queue.complete_connection_check("checker", batch["generation"], now=NOW + 8)
    assert queue.connection_check_pending()
    assert queue.claim("collector", now=NOW + 8) is None
    next_batch = queue.claim_connection_check("next-checker", now=NOW + 9)
    assert next_batch["generation"] > batch["generation"]
    assert next_batch["stores"] == []
    assert queue.complete_connection_check("next-checker", next_batch["generation"], now=NOW + 10)
    assert queue.claim("collector", now=NOW + 11)


def test_batch_claim_is_atomic_and_other_queue_connections_cannot_bypass_it(queue):
    failed(queue)
    enqueue(queue, verify=True, now=NOW + 3)
    peers = [SyncQueue("tenant-a", tenant_path=queue.path.parent) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(lambda pair: pair[1].claim_connection_check(
            f"checker-{pair[0]}", now=NOW + 4), enumerate(peers)))
    assert sum(batch is not None for batch in batches) == 1
    assert all(peer.claim(f"collector-{index}", now=NOW + 5) is None
               for index, peer in enumerate(peers))


def test_expired_batch_reply_is_fenced_even_when_the_owner_is_reused(queue):
    _, batch = prepared(queue)
    replacement = queue.claim_connection_check("checker", now=NOW + 25)
    assert replacement["generation"] > batch["generation"]
    old_generation = batch["generation"]
    assert not queue.owns_connection_check("checker", old_generation, now=NOW + 26)
    assert not queue.heartbeat_connection_check("checker", old_generation, now=NOW + 26)
    assert acknowledge(queue, batch, now=NOW + 26) == 0
    assert not queue.complete_connection_check("checker", old_generation, now=NOW + 26)
    assert not queue.abandon_connection_check("checker", old_generation, now=NOW + 26)
    assert queue.connection_check_pending()


def test_heartbeat_extends_only_the_current_batch_lease(queue):
    _, batch = prepared(queue)
    generation = batch["generation"]
    assert not queue.heartbeat_connection_check("unrelated", generation, now=NOW + 6)
    assert queue.heartbeat_connection_check("checker", generation, now=NOW + 24, lease_seconds=10)
    assert queue.owns_connection_check("checker", generation, now=NOW + 33)
    assert not queue.owns_connection_check("checker", generation, now=NOW + 34)
    assert not queue.heartbeat_connection_check("checker", generation, now=NOW + 34)


def test_abandon_keeps_work_and_barrier_pending_until_a_fresh_grant(queue):
    _, batch = prepared(queue)
    jobs, plans = queue.list_jobs(), saved_plans(queue)
    assert queue.abandon_connection_check("checker", batch["generation"], now=NOW + 6)
    assert queue.connection_check_pending()
    assert queue.claim("collector", now=NOW + 7) is None
    assert queue.list_jobs() == jobs and saved_plans(queue) == plans
    assert acknowledge(queue, batch, now=NOW + 7) == 0
    fresh = queue.claim_connection_check("checker", now=NOW + 7)
    assert fresh["generation"] > batch["generation"]


def test_pause_revokes_batch_and_new_enqueue_requests_a_new_generation(queue):
    _, batch = prepared(queue)
    before = queue.get_job(batch["stores"][0]["alerts"][0]["id"])
    queue.pause_all(now=NOW + 6)
    assert not queue.connection_check_pending()
    assert not queue.owns_connection_check("checker", batch["generation"], now=NOW + 7)
    assert acknowledge(queue, batch, now=NOW + 7) == 0
    assert not queue.complete_connection_check("checker", batch["generation"], now=NOW + 7)
    assert queue.claim("collector", now=NOW + 7) is None
    paused = queue.get_job(before["id"])
    assert paused["last_error"] == before["last_error"]
    assert paused["resultados_dias"] == before["resultados_dias"]
    enqueue(queue, verify=True, now=NOW + 8)
    fresh = queue.claim_connection_check("checker", now=NOW + 9)
    assert fresh["generation"] > batch["generation"]


def test_ack_is_tenant_and_store_scoped_and_keeps_raw_errors(queue, tmp_path):
    _, batch = prepared(queue)
    other = SyncQueue("tenant-b", tenant_path=tmp_path / "tenant-b")
    _, foreign = prepared(other)
    store = batch["stores"][0]
    before = queue.list_jobs(), other.list_jobs(), saved_plans(queue)
    assert queue.acknowledge_checked_connection(
        "checker", batch["generation"], "different-store", store["alerts"], now=NOW + 6) == 0
    assert queue.acknowledge_checked_connection(
        "checker", batch["generation"], "store-0", foreign["stores"][0]["alerts"], now=NOW + 6) == 0
    assert acknowledge(queue, batch) == 1
    assert (queue.list_jobs(), other.list_jobs(), saved_plans(queue)) == before
    assert queue.connection_alerts("store-0") == []
    assert other.connection_alerts("store-0")
    assert queue.get_job(store["alerts"][0]["id"])["last_error"] == ERROR


def test_starting_collection_transfers_ack_but_repeated_failure_restores_warning(queue):
    job, batch = prepared(queue)
    assert acknowledge(queue, batch) == 1
    assert queue.complete_connection_check("checker", batch["generation"], now=NOW + 7)
    claimed = queue.claim("collector", now=NOW + 8)
    assert claimed["attempts"] == job["attempts"] + 1
    assert queue.connection_alerts("store-0") == []
    queue.fail_job(claimed["id"], "collector", ERROR, now=NOW + 9)
    assert queue.connection_alerts("store-0")
    assert queue.summary()["lojas"][0]["ultimo_erro"] == ERROR


@pytest.mark.parametrize("paused", [False, True])
def test_migration_seeds_barrier_once_only_when_operational_work_and_alerts_exist(queue, paused):
    failed(queue)
    if paused:
        queue.pause_all(now=NOW + 3)
    jobs, plans = queue.list_jobs(), saved_plans(queue)
    with sqlite3.connect(queue.path) as conn:
        conn.execute("DROP TABLE connection_check_state")
    migrated = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert migrated.connection_check_pending() is (not paused)
    assert migrated.list_jobs() == jobs and saved_plans(migrated) == plans
    if not paused:
        batch = migrated.claim_connection_check("checker", now=NOW + 4)
        reopened = SyncQueue("tenant-a", tenant_path=queue.path.parent)
        assert reopened.owns_connection_check("checker", batch["generation"], now=NOW + 5)
        assert reopened.complete_connection_check("checker", batch["generation"], now=NOW + 5)
        assert not SyncQueue("tenant-a", tenant_path=queue.path.parent).connection_check_pending()


@pytest.mark.parametrize("requires_session", [False, True])
def test_scheduler_requests_barrier_for_alerts_in_paused_other_stores(queue, requires_session):
    failed(queue)
    queue.pause_all(now=NOW + 3)
    job = queue.enqueue("automatic", "Loja automatic", "2026-10-21", "2026-10-22",
                        now=NOW + 4, automatico=True, requires_session=requires_session)
    queue.claim("collector", now=NOW + 4)
    for day in ("2026-10-21", "2026-10-22"):
        queue.complete_day(job["id"], "collector", day, {}, now=NOW + 5)
    queue.complete_job(job["id"], "collector", now=NOW + 6)
    assert queue.schedule_due("2026-10-22", now=NOW + 906)
    assert queue.connection_check_pending()
    assert queue.claim("collector", now=NOW + 907) is None
    batch = queue.claim_connection_check("checker", now=NOW + 907)
    assert [store["store_id"] for store in batch["stores"]] == ["store-0"]
    assert batch["requires_session"] is requires_session


def test_central_request_with_legacy_local_alerts_keeps_session_requirement_after_restart_and_abandon(queue):
    failed(queue, requires_session=False)
    queue.pause_all(now=NOW + 3)
    enqueue(queue, "central-request", now=NOW + 4, verify=True, requires_session=True)
    batch = queue.claim_connection_check("checker", now=NOW + 5)
    assert batch["requires_session"] is True
    assert all(store["requires_session"] is False for store in batch["stores"])
    reopened = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert reopened.abandon_connection_check("checker", batch["generation"], now=NOW + 6)
    resumed = reopened.claim_connection_check("checker", now=NOW + 7)
    assert resumed["requires_session"] is True
    enqueue(reopened, "local-request", now=NOW + 8, verify=True)
    assert reopened.complete_connection_check("checker", resumed["generation"], now=NOW + 9)
    assert reopened.connection_check_pending()
    next_batch = reopened.claim_connection_check("checker", now=NOW + 10)
    assert next_batch["requires_session"] is True


def test_late_central_request_after_last_ack_cannot_be_cleared_by_the_previous_generation(queue):
    _, batch = prepared(queue)
    assert batch["requires_session"] is False
    assert acknowledge(queue, batch) == 1
    assert not queue.connection_alerts("store-0")
    enqueue(queue, "late-central", now=NOW + 7, verify=True, requires_session=True)
    assert queue.complete_connection_check("checker", batch["generation"], now=NOW + 8)
    assert queue.connection_check_pending()
    next_batch = queue.claim_connection_check("checker", now=NOW + 9)
    assert next_batch["generation"] > batch["generation"]
    assert next_batch["requires_session"] is True
    assert next_batch["stores"] == []


@pytest.mark.parametrize("paused", [False, True])
def test_session_requirement_is_cleared_after_completion_or_pause(queue, paused):
    failed(queue, requires_session=False)
    enqueue(queue, "central-request", now=NOW + 3, verify=True, requires_session=True)
    batch = queue.claim_connection_check("checker", now=NOW + 4)
    assert batch["requires_session"] is True
    if paused:
        queue.pause_all(now=NOW + 5)
    else:
        assert queue.complete_connection_check("checker", batch["generation"], now=NOW + 5)
    assert not queue.connection_check_pending()
    enqueue(queue, "local-request", now=NOW + 6, verify=True)
    next_batch = queue.claim_connection_check("checker", now=NOW + 7)
    assert next_batch["requires_session"] is False


@pytest.mark.parametrize("central_source", ["operational", "paused-alert"])
def test_migration_derives_session_requirement_from_operational_work_or_historical_alert(queue, central_source):
    failed(queue, requires_session=central_source == "paused-alert")
    queue.pause_all(now=NOW + 3)
    enqueue(queue, "operational", now=NOW + 4, requires_session=central_source == "operational")
    jobs_before = queue.list_jobs()
    with sqlite3.connect(queue.path) as conn:
        conn.execute("DROP TABLE connection_check_state")
    migrated = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    batch = migrated.claim_connection_check("checker", now=NOW + 5)
    assert batch["requires_session"] is True
    assert migrated.list_jobs() == jobs_before
    reopened = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert reopened.owns_connection_check("checker", batch["generation"], now=NOW + 6)
    assert reopened.abandon_connection_check("checker", batch["generation"], now=NOW + 6)
    assert reopened.claim_connection_check("checker", now=NOW + 7)["requires_session"] is True


@pytest.mark.parametrize("operation", ["ack", "heartbeat", "complete", "abandon"])
def test_lease_is_rechecked_after_waiting_for_sqlite_writer(queue, monkeypatch, operation):
    _, batch = prepared(queue)
    clock, entered = [NOW + 6], threading.Event()
    monkeypatch.setattr(sync_queue.time, "time", lambda: clock[0])
    original_connection = queue._connection

    @contextmanager
    def observed_connection():
        with original_connection() as conn:
            entered.set()
            yield conn

    monkeypatch.setattr(queue, "_connection", observed_connection)
    calls = {
        "ack": lambda: acknowledge(queue, batch, now=None),
        "heartbeat": lambda: queue.heartbeat_connection_check("checker", batch["generation"]),
        "complete": lambda: queue.complete_connection_check("checker", batch["generation"]),
        "abandon": lambda: queue.abandon_connection_check("checker", batch["generation"]),
    }
    with sqlite3.connect(queue.path, isolation_level=None) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute("BEGIN IMMEDIATE")
        future = pool.submit(calls[operation])
        assert entered.wait(5)
        clock[0] = NOW + 25
        blocker.rollback()
        assert not future.result(timeout=5)
    assert queue.connection_check_pending()
    assert queue.connection_alerts("store-0")


@pytest.mark.parametrize("operation", ["heartbeat", "fail", "day", "complete"])
def test_old_collector_cannot_revive_or_write_after_sqlite_wait_and_expiry(queue, monkeypatch, operation):
    job = enqueue(queue)
    queue.claim("collector", now=NOW, lease_seconds=10)
    clock, entered = [NOW + 1], threading.Event()
    monkeypatch.setattr(sync_queue.time, "time", lambda: clock[0])
    original_connection = queue._connection

    @contextmanager
    def observed_connection():
        with original_connection() as conn:
            entered.set()
            yield conn

    monkeypatch.setattr(queue, "_connection", observed_connection)
    calls = {
        "heartbeat": lambda: queue.heartbeat(job["id"], "collector"),
        "fail": lambda: queue.fail_job(job["id"], "collector", ERROR),
        "day": lambda: queue.complete_day(job["id"], "collector", "2026-10-01", {}),
        "complete": lambda: queue.complete_job(job["id"], "collector"),
    }
    with sqlite3.connect(queue.path, isolation_level=None) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute("BEGIN IMMEDIATE")
        future = pool.submit(calls[operation])
        assert entered.wait(5)
        clock[0] = NOW + 10
        blocker.rollback()
        if operation == "heartbeat":
            assert future.result(timeout=5) is False
        else:
            with pytest.raises(RuntimeError):
                future.result(timeout=5)
