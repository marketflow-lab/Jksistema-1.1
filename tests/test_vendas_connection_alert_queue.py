from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager

import pytest

from backend.modules.vendas.sync_queue import SyncQueue


NOW = dt.datetime(2026, 10, 22, 12, tzinfo=dt.timezone.utc).timestamp()
ERROR = "Sessao expirada. Faca login novamente."


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("tenant-a", tenant_path=tmp_path / "tenant-a")


def enqueue(queue, store="store-1", start="2026-10-01", end="2026-10-02", now=NOW):
    return queue.enqueue(store, "Loja " + store, start, end,
                         automatico=True, requires_session=True, now=now)


def failed_job(queue, store="store-1", start="2026-10-01", end="2026-10-02", now=NOW):
    job = enqueue(queue, store, start, end, now)
    claimed = queue.claim("failed-worker", now=now, lease_seconds=60)
    assert claimed["id"] == job["id"]
    queue.complete_day(job["id"], "failed-worker", start, {
        "vendas": 3,
        "checkpoint": {"documentos_salvos": 3, "ultimo_tipo": "pedidos", "ultimo_id": "123"},
    }, now=now + 1)
    return queue.fail_job(job["id"], "failed-worker", ERROR, retryable=False, now=now + 2)


def resume(queue, job, owner="verified-worker", now=NOW + 10, lease_seconds=60):
    resumed = enqueue(queue, job["store_id"], job["data_inicio"], job["data_fim"], now)
    assert resumed["id"] == job["id"]
    claimed = queue.claim(owner, now=now, lease_seconds=lease_seconds)
    assert claimed["id"] == job["id"]
    return claimed


def plan_state(queue):
    # These scheduling fields have no full public projection. Compare the saved
    # plans before/after acknowledgement, without reproducing its SQL or hashes.
    with sqlite3.connect(queue.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY store_id")]


def summary_job(queue, job_id):
    return next(job for job in queue.summary()["jobs"] if job["id"] == job_id)


def summary_store(queue, store_id):
    return next(store for store in queue.summary()["lojas"] if store["store_id"] == store_id)


def test_enqueue_keeps_previous_warning_and_completed_days(queue):
    failed = failed_job(queue)
    alerts = queue.connection_alerts("store-1")
    assert len(alerts) == 1

    resumed = enqueue(queue, now=NOW + 3)

    assert resumed["id"] == failed["id"]
    assert resumed["status"] == "queued"
    assert resumed["last_error"] == ERROR
    assert resumed["resultados_dias"] == failed["resultados_dias"]
    assert queue.connection_alerts("store-1") == alerts
    assert summary_store(queue, "store-1")["ultimo_erro"] == ERROR
    assert summary_job(queue, failed["id"])["aviso_conexao_verificado"] is False


def test_verified_provider_check_hides_only_observed_store_warning_and_preserves_work(queue):
    first = failed_job(queue)
    second = failed_job(queue, store="store-2", now=NOW + 3)
    running = resume(queue, first)
    first_alerts = queue.connection_alerts("store-1")
    second_alerts = queue.connection_alerts("store-2")
    assert summary_store(queue, "store-1")["ultimo_erro"] == ERROR
    jobs_before = queue.list_jobs()
    plans_before = plan_state(queue)
    summary_before = queue.summary()

    # The caller invokes this only after its provider check returned HTTP 200.
    count = queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", first_alerts + second_alerts,
        expected_attempts=running["attempts"], now=NOW + 11)

    assert count == 1
    assert queue.list_jobs() == jobs_before
    assert plan_state(queue) == plans_before
    assert queue.connection_alerts("store-1") == []
    assert queue.connection_alerts("store-2") == second_alerts
    assert summary_store(queue, "store-1")["ultimo_erro"] is None
    assert summary_store(queue, "store-2")["ultimo_erro"] == ERROR
    assert summary_job(queue, first["id"])["aviso_conexao_verificado"] is True
    assert summary_job(queue, second["id"])["aviso_conexao_verificado"] is False
    for job_id in (first["id"], second["id"]):
        assert queue.get_job(job_id)["last_error"] == ERROR
        assert summary_job(queue, job_id)["last_error"] == ERROR
    for key in ("pendentes", "pausados", "habilitadas"):
        assert queue.summary()[key] == summary_before[key]


def test_heartbeat_does_not_change_observed_error_or_reinstate_acknowledged_warning(queue):
    running = resume(queue, failed_job(queue))
    alerts = queue.connection_alerts("store-1")

    assert queue.heartbeat(running["id"], "verified-worker", now=NOW + 11)
    assert queue.connection_alerts("store-1") == alerts
    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", alerts,
        expected_attempts=running["attempts"], now=NOW + 12) == 1
    assert queue.heartbeat(running["id"], "verified-worker", now=NOW + 13)

    assert queue.connection_alerts("store-1") == []
    assert queue.get_job(running["id"])["last_error"] == ERROR
    assert summary_job(queue, running["id"])["aviso_conexao_verificado"] is True
    assert summary_store(queue, "store-1")["ultimo_erro"] is None


@pytest.mark.parametrize("invalidated", ["expired", "paused", "reassigned", "wrong_owner", "wrong_store"])
def test_ack_requires_current_live_lease_and_matching_store(queue, invalidated):
    running = resume(queue, failed_job(queue), lease_seconds=10)
    alerts = queue.connection_alerts("store-1")
    now, owner, store = NOW + 11, "verified-worker", "store-1"
    if invalidated == "expired":
        now = NOW + 20
    elif invalidated == "paused":
        queue.pause_all(now=now)
    elif invalidated == "reassigned":
        now = NOW + 20
        replacement = queue.claim("replacement-worker", now=now)
        assert replacement["id"] == running["id"]
        assert queue.owns(running["id"], "replacement-worker", now=now)
    elif invalidated == "wrong_owner":
        owner = "unrelated-worker"
    elif invalidated == "wrong_store":
        store = "store-2"
    before = queue.list_jobs()

    assert queue.acknowledge_connection_alerts(
        running["id"], owner, store, alerts, expected_attempts=running["attempts"], now=now) == 0

    assert queue.list_jobs() == before
    assert len(queue.connection_alerts("store-1")) == 1
    assert summary_job(queue, running["id"])["aviso_conexao_verificado"] is False
    assert summary_store(queue, "store-1")["ultimo_erro"] == ERROR


@pytest.mark.parametrize("new_error", [ERROR, "Integracao temporariamente indisponivel."])
def test_stale_snapshot_cannot_acknowledge_error_from_newer_attempt(queue, new_error):
    running = resume(queue, failed_job(queue))
    snapshot = queue.connection_alerts("store-1")
    failed = queue.fail_job(running["id"], "verified-worker", new_error,
                            retryable=False, now=NOW + 11)
    latest = resume(queue, failed, owner="next-worker", now=NOW + 12)
    assert latest["attempts"] == running["attempts"] + 1

    assert queue.acknowledge_connection_alerts(
        latest["id"], "next-worker", "store-1", snapshot,
        expected_attempts=latest["attempts"], now=NOW + 13) == 0

    fresh = queue.connection_alerts("store-1")
    assert len(fresh) == 1
    assert fresh != snapshot
    assert summary_store(queue, "store-1")["ultimo_erro"] == new_error
    assert summary_job(queue, latest["id"])["aviso_conexao_verificado"] is False
    assert queue.acknowledge_connection_alerts(
        latest["id"], "next-worker", "store-1", fresh,
        expected_attempts=latest["attempts"], now=NOW + 14) == 1


def test_repeated_failure_in_same_attempt_reinstates_warning_even_with_same_message(queue):
    running = resume(queue, failed_job(queue))
    alerts = queue.connection_alerts("store-1")
    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", alerts,
        expected_attempts=running["attempts"], now=NOW + 11) == 1
    assert queue.connection_alerts("store-1") == []

    failed = queue.fail_job(running["id"], "verified-worker", ERROR,
                            retryable=False, now=NOW + 12)

    assert failed["attempts"] == running["attempts"]
    assert queue.connection_alerts("store-1") == alerts
    assert summary_job(queue, running["id"])["aviso_conexao_verificado"] is False
    assert summary_store(queue, "store-1")["ultimo_erro"] == ERROR
    assert failed["resultados_dias"] == running["resultados_dias"]


def test_new_job_error_after_snapshot_remains_visible_and_no_job_is_removed(queue):
    old = failed_job(queue)
    snapshot = queue.connection_alerts("store-1")
    new = failed_job(queue, start="2026-10-03", end="2026-10-04", now=NOW + 3)
    verifier = enqueue(queue, start="2026-10-05", end="2026-10-06", now=NOW + 6)
    running = queue.claim("verified-worker", now=NOW + 6)
    assert running["id"] == verifier["id"]
    jobs_before = queue.list_jobs()

    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", snapshot,
        expected_attempts=running["attempts"], now=NOW + 7) == 1

    assert queue.list_jobs() == jobs_before
    assert {alert["id"] for alert in queue.connection_alerts("store-1")} == {new["id"]}
    assert summary_job(queue, old["id"])["aviso_conexao_verificado"] is True
    assert summary_job(queue, new["id"])["aviso_conexao_verificado"] is False


def test_old_provider_reply_cannot_ack_after_same_owner_reclaims_verifier(queue):
    failed = failed_job(queue)
    verifier = enqueue(queue, start="2026-10-03", end="2026-10-04", now=NOW + 3)
    initial = queue.claim("reused-worker", now=NOW + 3, lease_seconds=10)
    assert initial["id"] == verifier["id"]
    snapshot = queue.connection_alerts("store-1")

    reclaimed = queue.claim("reused-worker", now=NOW + 13)
    assert reclaimed["id"] == initial["id"]
    assert reclaimed["attempts"] == initial["attempts"] + 1
    assert queue.connection_alerts("store-1") == snapshot
    jobs_before = queue.list_jobs()

    # Owner, store and observed warning still match. Only the verifier's
    # attempt distinguishes an obsolete HTTP reply from the current check.
    assert queue.acknowledge_connection_alerts(
        reclaimed["id"], "reused-worker", "store-1", snapshot,
        expected_attempts=initial["attempts"], now=NOW + 14) == 0
    assert queue.list_jobs() == jobs_before
    assert queue.connection_alerts("store-1") == snapshot
    assert summary_job(queue, failed["id"])["aviso_conexao_verificado"] is False
    assert queue.acknowledge_connection_alerts(
        reclaimed["id"], "reused-worker", "store-1", snapshot,
        expected_attempts=reclaimed["attempts"], now=NOW + 15) == 1


def test_same_store_id_in_other_tenant_cannot_be_acknowledged(queue, tmp_path):
    other = SyncQueue("tenant-b", tenant_path=tmp_path / "tenant-b")
    running = resume(queue, failed_job(queue))
    other_failed = failed_job(other)
    own_alerts = queue.connection_alerts("store-1")
    other_alerts = other.connection_alerts("store-1")
    other_jobs_before = other.list_jobs()

    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", other_alerts,
        expected_attempts=running["attempts"], now=NOW + 11) == 0
    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", own_alerts,
        expected_attempts=running["attempts"], now=NOW + 12) == 1

    assert queue.connection_alerts("store-1") == []
    assert other.connection_alerts("store-1") == other_alerts
    assert other.list_jobs() == other_jobs_before
    assert summary_job(other, other_failed["id"])["aviso_conexao_verificado"] is False
    assert summary_store(other, "store-1")["ultimo_erro"] == ERROR


def test_acknowledgement_survives_reopening_database_and_later_heartbeat(queue):
    running = resume(queue, failed_job(queue))
    alerts = queue.connection_alerts("store-1")
    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", alerts,
        expected_attempts=running["attempts"], now=NOW + 11) == 1
    jobs_before = queue.list_jobs()
    plans_before = plan_state(queue)

    reopened = SyncQueue("tenant-a", tenant_path=queue.path.parent)

    assert reopened.list_jobs() == jobs_before
    assert plan_state(reopened) == plans_before
    assert reopened.connection_alerts("store-1") == []
    assert summary_job(reopened, running["id"])["aviso_conexao_verificado"] is True
    assert reopened.heartbeat(running["id"], "verified-worker", now=NOW + 12)
    assert reopened.connection_alerts("store-1") == []
    assert reopened.get_job(running["id"])["last_error"] == ERROR


def test_existing_queue_schema_is_upgraded_without_losing_jobs_days_or_plans(queue):
    running = resume(queue, failed_job(queue))
    jobs_before = queue.list_jobs()
    plans_before = plan_state(queue)
    # A database from before this feature has all existing data, but no alert
    # acknowledgement table. Only this disposable fixture is changed directly.
    with sqlite3.connect(queue.path) as conn:
        conn.execute("DROP TABLE connection_alert_ack")

    upgraded = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert upgraded.list_jobs() == jobs_before
    assert plan_state(upgraded) == plans_before
    assert len(upgraded.connection_alerts("store-1")) == 1
    assert upgraded.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1",
        upgraded.connection_alerts("store-1"), expected_attempts=running["attempts"], now=NOW + 11) == 1

    reopened = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert reopened.list_jobs() == jobs_before
    assert plan_state(reopened) == plans_before
    assert reopened.connection_alerts("store-1") == []
    assert summary_job(reopened, running["id"])["aviso_conexao_verificado"] is True


def test_verified_historical_warning_stays_hidden_when_new_period_completes(queue):
    historical = failed_job(queue)
    snapshot = queue.connection_alerts("store-1")
    newer = enqueue(queue, start="2026-10-03", end="2026-10-04", now=NOW + 3)
    running = queue.claim("new-period-worker", now=NOW + 3)
    assert running["id"] == newer["id"]
    assert queue.acknowledge_connection_alerts(
        running["id"], "new-period-worker", "store-1", snapshot,
        expected_attempts=running["attempts"], now=NOW + 4) == 1

    queue.complete_day(newer["id"], "new-period-worker", "2026-10-03", {"vendas": 2}, now=NOW + 5)
    queue.complete_day(newer["id"], "new-period-worker", "2026-10-04", {"vendas": 0}, now=NOW + 6)
    completed = queue.complete_job(newer["id"], "new-period-worker",
                                   now=NOW + 7, today="2026-10-22")

    assert completed["status"] == "complete"
    assert queue.get_job(historical["id"]) == historical
    store = summary_store(queue, "store-1")
    assert store["job_id"] == historical["id"]
    assert store["status"] == "blocked"
    assert store["pendentes"] == queue.summary()["pendentes"] == 1
    assert store["ultimo_erro"] is None
    assert store["aviso_conexao_verificado"] is True
    history = summary_job(queue, historical["id"])
    assert history["aviso_conexao_verificado"] is True
    assert history["last_error"] == ERROR
    assert history["dias_concluidos"] == historical["dias_concluidos"]
    assert queue.connection_alerts("store-1") == []


def test_ack_rechecks_clock_after_transaction_wait_expires_lease(queue, monkeypatch):
    running = resume(queue, failed_job(queue), lease_seconds=10)
    snapshot = queue.connection_alerts("store-1")
    jobs_before = queue.list_jobs()
    plans_before = plan_state(queue)
    clock = {"now": NOW + 11}
    monkeypatch.setattr("backend.modules.vendas.sync_queue.time.time", lambda: clock["now"])
    assert queue.owns(running["id"], "verified-worker")
    transaction = queue._transaction

    @contextmanager
    def transaction_after_wait():
        with transaction() as conn:
            # Simulate acquiring BEGIN IMMEDIATE only after a competing writer
            # released its lock. No wall-clock sleep or database stub is needed.
            clock["now"] = running["lease_until"] + 1
            yield conn

    monkeypatch.setattr(queue, "_transaction", transaction_after_wait)
    assert queue.acknowledge_connection_alerts(
        running["id"], "verified-worker", "store-1", snapshot,
        expected_attempts=running["attempts"], now=None) == 0

    assert queue.list_jobs() == jobs_before
    assert plan_state(queue) == plans_before
    assert queue.connection_alerts("store-1") == snapshot
    assert summary_job(queue, running["id"])["aviso_conexao_verificado"] is False
    assert summary_store(queue, "store-1")["ultimo_erro"] == ERROR
