from __future__ import annotations

import datetime as dt
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.modules.vendas.sync_queue import SyncQueue


def stamp(value):
    return dt.datetime.fromisoformat(value + "T12:00:00-03:00").timestamp()


BASE = stamp("2026-10-21")


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("tenant-a", tenant_path=tmp_path / "tenant-a")


def enqueue(queue, store="store-1", start="2026-10-01", end="2026-10-02", **kwargs):
    return queue.enqueue(store, "Loja " + store, start, end, **kwargs)


def finish(queue, job, owner, now, today=None):
    end = min(dt.date.fromisoformat(job["data_fim"]), dt.date.fromisoformat(today or job["data_fim"]))
    count = (end - dt.date.fromisoformat(job["data_inicio"])).days + 1
    for offset in range(count):
        day = dt.date.fromisoformat(job["data_inicio"]) + dt.timedelta(days=offset)
        queue.complete_day(job["id"], owner, str(day), {"vendas": 0, "notas": 0}, now=now,
                           collection_started_at=now)
    return queue.complete_job(job["id"], owner, now=now, today=today or job["data_fim"])


def test_expired_lease_resumes_only_acknowledged_days_including_empty(queue):
    original = enqueue(queue, now=BASE + 100)
    claimed = queue.claim("crashed-worker", now=BASE + 100, lease_seconds=10)
    queue.complete_day(claimed["id"], "crashed-worker", "2026-10-01", {"vendas": 0}, now=BASE + 101)
    restarted = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert restarted.claim("new-worker", now=BASE + 109) is None
    assert not restarted.owns(original["id"], "crashed-worker", now=BASE + 110)
    assert not restarted.heartbeat(original["id"], "crashed-worker", now=BASE + 110)
    with pytest.raises(RuntimeError):
        restarted.complete_day(original["id"], "crashed-worker", "2026-10-02", {}, now=BASE + 110)
    resumed = restarted.claim("new-worker", now=BASE + 110)
    assert resumed["id"] == original["id"]
    assert resumed["cycle_id"] == original["cycle_id"]
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert resumed["resultados_dias"]["2026-10-01"] == {"vendas": 0}
    assert resumed["attempts"] == 2
    with pytest.raises(ValueError, match="dias"):
        restarted.complete_job(resumed["id"], "new-worker", now=BASE + 111, today="2026-10-02")
    restarted.complete_day(resumed["id"], "new-worker", "2026-10-02", {"vendas": 2}, now=BASE + 112)
    done = restarted.complete_job(resumed["id"], "new-worker", now=BASE + 113, today="2026-10-02")
    assert done["status"] == "complete"
    next_cycle = enqueue(restarted, now=BASE + 114)
    assert next_cycle["id"] != original["id"]
    assert next_cycle["dias_concluidos"] == ["2026-10-01", "2026-10-02"]


def test_day_acknowledgement_is_idempotent_and_filters_payload(queue):
    job = enqueue(queue, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    first = queue.complete_day(job["id"], "worker", "2026-10-01", {
        "vendas": 3, "notas": 1, "access_token": "DO-NOT-STORE",
        "checkpoint": {"documentos_salvos": 4, "ultimo_tipo": "pedidos",
                       "ultimo_id": "1234", "atualizado_em": "2026-10-01T12:00:00+00:00",
                       "authorization": "DO-NOT-STORE"}}, now=BASE + 101)
    second = queue.complete_day(job["id"], "worker", "2026-10-01", {"vendas": 99}, now=BASE + 102)
    assert first["resultados_dias"] == second["resultados_dias"]
    assert second["checkpoint"]["ultimo_id"] == "1234"
    assert "DO-NOT-STORE" not in repr(second)
    assert b"DO-NOT-STORE" not in queue.path.read_bytes()
    with pytest.raises(ValueError, match="fora"):
        queue.complete_day(job["id"], "worker", "2026-10-03", {}, now=BASE + 103)


def test_claim_is_atomic_limits_four_per_tenant_and_one_per_store(queue, tmp_path):
    enqueue(queue, store="store-1", now=BASE + 100)
    enqueue(queue, store="store-1", start="2026-09-01", end="2026-09-02", now=BASE + 101)
    enqueue(queue, store="store-2", now=BASE + 102)
    enqueue(queue, store="store-3", now=BASE + 103)
    enqueue(queue, store="store-4", now=BASE + 104)
    enqueue(queue, store="store-5", now=BASE + 105)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda i: queue.claim(f"worker-{i}", now=BASE + 110, limit=50), range(8)))
    claimed = [job for job in claimed if job]
    # Nonblocking history-gate contention may defer a worker to the scheduler's
    # next dispatch. Sequential retries must fill, but never exceed, the limit.
    for index in range(4):
        retry = queue.claim(f"retry-{index}", now=BASE + 110, limit=50)
        if retry is not None:
            claimed.append(retry)
    assert len(claimed) == 4
    assert len({job["store_id"] for job in claimed}) == 4
    other = SyncQueue("tenant-b", tenant_path=tmp_path / "tenant-b")
    enqueue(other, now=BASE + 110)
    assert other.claim("independent-worker", now=BASE + 110)
    assert other.get_job(claimed[0]["id"]) is None
    with pytest.raises(ValueError, match="outro cliente"):
        SyncQueue("tenant-b", tenant_path=queue.path.parent)


def test_heartbeat_requires_live_owner_and_excluded_store_is_not_claimed(queue):
    first = enqueue(queue, store="store-1", now=BASE + 100)
    second = enqueue(queue, store="store-2", now=BASE + 100)
    claimed = queue.claim("worker", now=BASE + 100, exclude_store_ids=["store-1"])
    assert claimed["id"] == second["id"]
    assert not queue.heartbeat(second["id"], "other", now=BASE + 101)
    assert queue.heartbeat(second["id"], "worker", now=BASE + 219, lease_seconds=200)
    assert queue.owns(second["id"], "worker", now=BASE + 418)
    assert not queue.owns(second["id"], "worker", now=BASE + 419)
    assert queue.claim("other", now=BASE + 200, exclude_store_ids=["store-1"]) is None
    assert queue.claim("other", now=BASE + 200)["id"] == first["id"]


def test_retry_backoff_block_and_explicit_resume_preserve_checkpoint(queue):
    job = enqueue(queue, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    queue.complete_day(job["id"], "worker", "2026-10-01", {}, now=BASE + 101)
    failed = queue.fail_job(job["id"], "worker", "Servico indisponivel.", now=BASE + 102)
    assert failed["status"] == "retry"
    assert failed["next_attempt"] == BASE + 117
    assert queue.claim("next", now=BASE + 116) is None
    resumed = queue.claim("next", now=BASE + 117)
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    failed = queue.fail_job(job["id"], "next", "Servico indisponivel.", now=BASE + 118)
    assert failed["next_attempt"] == BASE + 148
    queue.claim("third", now=BASE + 148)
    blocked = queue.fail_job(job["id"], "third", "Autorizacao necessaria.", retryable=False, now=BASE + 149)
    assert blocked["status"] == "blocked"
    assert queue.claim("fourth", now=BASE + 10000) is None
    resumed = enqueue(queue, now=BASE + 10001)
    assert resumed["id"] == job["id"]
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert queue.claim("fourth", now=BASE + 10001)


def test_pause_invalidates_running_lease_and_retains_progress(queue):
    job = enqueue(queue, automatico=True, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    queue.complete_day(job["id"], "worker", "2026-10-01", {}, now=BASE + 101)
    queue.pause_all(now=BASE + 102)
    assert not queue.owns(job["id"], "worker", now=BASE + 103)
    assert queue.claim("other", now=BASE + 10000) is None
    assert queue.schedule_due(dt.date(2026, 10, 15), now=BASE + 10000) == []
    summary = queue.summary()
    assert summary["habilitadas"] == 0
    assert summary["pendentes"] == 0
    assert summary["pausados"] == 1
    assert summary["lojas"][0]["proxima_execucao"] is None
    resumed = enqueue(queue, automatico=True, now=BASE + 10001)
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert resumed["id"] == job["id"]


def test_manual_http_replay_does_not_interrupt_running_manual_job(queue):
    first = enqueue(queue, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    replay = enqueue(queue, now=BASE + 101)
    assert replay["id"] == first["id"]
    assert replay["status"] == "running"
    assert replay["owner"] == "worker"
    assert queue.owns(first["id"], "worker", now=BASE + 102)
    assert queue.summary()["habilitadas"] == 0


def test_changed_plan_keeps_pending_old_period_and_paused_jobs_resume(queue):
    first = enqueue(queue, automatico=True, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    queue.complete_day(first["id"], "worker", "2026-10-01", {}, now=BASE + 101)
    queue.pause_all(now=BASE + 102)
    second = enqueue(queue, start="2026-09-01", end="2026-09-03", automatico=True, now=BASE + 103)
    assert second["id"] != first["id"]
    assert queue.get_job(first["id"])["dias_concluidos"] == ["2026-10-01"]
    assert queue.get_job(first["id"])["status"] == "queued"
    assert queue.summary()["pendentes"] == 2
    resumed = queue.claim("resumed", now=BASE + 104)
    finish(queue, resumed, "resumed", now=BASE + 105)
    assert queue.get_job(second["id"])["status"] == "queued"
    assert queue.schedule_due(dt.date(2026, 10, 15), now=BASE + 2000) == []


def test_manual_request_disables_auto_without_dropping_other_period(queue):
    automatic = enqueue(queue, automatico=True, now=BASE + 100)
    queue.claim("worker", now=BASE + 100)
    manual = enqueue(queue, start="2026-09-01", end="2026-09-02", now=BASE + 101)
    assert queue.get_job(automatic["id"])["status"] == "paused"
    assert not queue.owns(automatic["id"], "worker", now=BASE + 102)
    assert queue.get_job(manual["id"])["status"] == "queued"
    assert queue.summary()["habilitadas"] == 0


def test_schedule_recovers_offline_gap_without_reviewing_completed_history(queue):
    initial_time, now = stamp("2026-01-04"), stamp("2026-10-15")
    enqueue(queue, start="2026-01-01", end="2026-12-31", automatico=True,
            requires_session=True, now=initial_time)
    initial = queue.claim("initial", now=initial_time)
    finish(queue, initial, "initial", now=initial_time + 1, today="2026-01-03")
    due = queue.schedule_due(dt.date(2026, 10, 15), now=now)
    assert len(due) == 1 and due[0]["kind"] == "recent"
    recent = due[0]
    assert recent["data_inicio"] == "2026-01-04"
    assert recent["data_fim"] == "2026-10-15"
    assert all(job["requires_session"] and not job["forcar_resync"] for job in due)
    assert queue.schedule_due(dt.date(2026, 10, 15), now=now + 1) == []
    running = queue.claim("recent", now=now + 1)
    queue.complete_day(running["id"], "recent", "2026-01-04", {}, now=now + 2)
    queue.fail_job(running["id"], "recent", "Servico indisponivel.", now=now + 3)
    assert queue.schedule_due(dt.date(2026, 10, 16), now=now + 86400) == []
    resumed = queue.claim("resumed", now=now + 86400)
    assert resumed["id"] == recent["id"]
    assert resumed["dias_concluidos"] == ["2026-01-04"]
    finish(queue, resumed, "resumed", now=now + 86401)
    next_runs = queue.schedule_due(dt.date(2026, 10, 17), now=now + 172800)
    assert len(next_runs) == 1
    assert next_runs[0]["data_inicio"] == "2026-10-16"
    assert next_runs[0]["data_fim"] == "2026-10-17"


def test_schedule_revisits_only_today_and_does_not_rotate_closed_history(queue):
    now = stamp("2026-10-15")
    enqueue(queue, start="2026-10-01", end="2026-10-31", automatico=True, now=now)
    finish(queue, queue.claim("initial", now=now), "initial", now=now + 1, today="2026-10-15")
    assert queue.schedule_due(dt.date(2026, 10, 15), now=now + 2) == []
    first = queue.schedule_due(dt.date(2026, 10, 15), now=now + 901)
    assert [(job["data_inicio"], job["data_fim"]) for job in first] == [("2026-10-15", "2026-10-15")]
    finish(queue, queue.claim("today", now=now + 902), "today", now=now + 903)
    second = queue.schedule_due(dt.date(2026, 10, 16), now=now + 86400)
    assert [(job["data_inicio"], job["data_fim"]) for job in second] == [("2026-10-15", "2026-10-16")]
    finish(queue, queue.claim("closed-yesterday", now=now + 86401), "closed-yesterday", now=now + 86402)
    third = queue.schedule_due(dt.date(2026, 10, 16), now=now + 87302)
    assert [(job["data_inicio"], job["data_fim"]) for job in third] == [("2026-10-16", "2026-10-16")]
    with sqlite3.connect(queue.path) as conn:
        assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "tenant", "jobs", "plans", "days", "connection_alert_ack", "connection_check_state", "store_sources", "sync_revision"}


def test_future_empty_days_do_not_hide_gap_after_long_downtime(queue):
    now = stamp("2026-10-15")
    enqueue(queue, start="2026-10-01", end="2026-12-31", automatico=True, now=now)
    finish(queue, queue.claim("initial", now=now), "initial", now=now + 1, today="2026-10-15")
    scheduled = queue.schedule_due(dt.date(2027, 1, 15), now=stamp("2027-01-15"))
    recent = next(job for job in scheduled if job["kind"] == "recent")
    assert recent["data_inicio"] == "2026-10-15"
    assert recent["data_fim"] == "2026-12-31"


def test_newer_manual_period_does_not_forget_original_start_or_unvisited_gap(queue):
    initial_time, manual_time = stamp("2026-01-04"), stamp("2026-10-04")
    enqueue(queue, start="2026-01-01", end="2026-01-03", automatico=True, now=initial_time)
    finish(queue, queue.claim("initial", now=initial_time), "initial", now=initial_time + 1)
    enqueue(queue, start="2026-10-01", end="2026-10-03", automatico=True, now=manual_time)
    finish(queue, queue.claim("manual", now=manual_time), "manual", now=manual_time + 1)
    scheduled = queue.schedule_due(dt.date(2026, 10, 15), now=stamp("2026-10-15"))
    assert len(scheduled) == 1 and scheduled[0]["data_inicio"] == "2026-01-04"
    assert scheduled[0]["dias_concluidos"] == ["2026-10-01", "2026-10-02", "2026-10-03"]


def test_summary_prioritizes_running_then_operational_and_keeps_public_shape_small(queue):
    paused = enqueue(queue, automatico=True, now=BASE + 100)
    manual = enqueue(queue, start="2026-09-01", end="2026-09-02", now=BASE + 101)
    summary = queue.summary()
    assert summary["lojas"][0]["job_id"] == manual["id"]
    assert summary["lojas"][0]["pendentes"] == 1
    assert summary["lojas"][0]["pausados"] == 1
    queue.claim("manual", now=BASE + 102)
    queue.complete_day(manual["id"], "manual", "2026-09-01", {"vendas": 0}, now=BASE + 103)
    summary = queue.summary()
    assert summary["lojas"][0]["status"] == "running"
    assert {job["id"] for job in summary["jobs"]} == {paused["id"], manual["id"]}
    assert all(not {"owner", "signature", "requires_session", "resultados_dias"}.intersection(job)
               for job in summary["jobs"])


def test_summary_limits_completed_history_without_losing_pending_jobs(queue):
    for index in range(22):
        enqueue(queue, end="2026-10-01", now=BASE + 100 + index * 2)
        finish(queue, queue.claim(f"worker-{index}", now=BASE + 100 + index * 2),
               f"worker-{index}", now=BASE + 101 + index * 2)
    pending = enqueue(queue, now=BASE + 200)
    assert len(queue.list_jobs()) == 23
    summary = queue.summary()
    assert len(summary["jobs"]) == 21
    assert any(job["id"] == pending["id"] for job in summary["jobs"])


def test_paused_manual_period_does_not_block_a_new_automatic_plan(queue):
    manual = enqueue(queue, start="2026-09-01", end="2026-09-03", now=BASE + 100)
    queue.pause_all(now=BASE + 101)
    enqueue(queue, start="2026-10-01", end="2026-10-03", automatico=True, now=BASE + 102)
    finish(queue, queue.claim("automatic", now=BASE + 102), "automatic", now=BASE + 103, today="2026-10-03")
    assert queue.get_job(manual["id"])["status"] == "paused"
    scheduled = queue.schedule_due(dt.date(2026, 10, 15), now=BASE + 1003)
    assert {job["kind"] for job in scheduled} == {"recent"}
    assert queue.get_job(manual["id"])["status"] == "paused"


@pytest.mark.parametrize("start,end", [("2026-10-03", "2026-10-01"),
    ("2026-1-01", "2026-10-01"), ("2026-02-30", "2026-10-01"),
    ("2026-10-01T00:00:00", "2026-10-02")])
def test_dates_are_validated_without_partial_plan_changes(queue, start, end):
    with pytest.raises(ValueError):
        enqueue(queue, start=start, end=end)
    assert queue.list_jobs() == []
    assert queue.summary()["lojas"] == []


def test_history_guard_blocks_other_claims_without_blocking_lease_validation(queue):
    job = enqueue(queue, automatico=True, now=BASE + 100)
    queue.claim("old-worker", now=BASE + 100)
    other = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    with queue.history_reset_guard():
        assert other.owns(job["id"], "old-worker", now=BASE + 101)
        queue.reset_history(now=BASE + 102)
        assert not other.owns(job["id"], "old-worker", now=BASE + 103)
        assert not other.heartbeat(job["id"], "old-worker", now=BASE + 103)
        requested = other.enqueue("store-1", "Loja store-1", "2026-10-01", "2026-10-02",
                                  automatico=True, now=BASE + 104)
        assert requested["id"] == job["id"]
        assert other.claim("during-delete", now=BASE + 104) is None
        assert other.get_job(job["id"])["status"] == "queued"
        assert other.summary()["habilitadas"] == 1
    resumed = other.claim("after-delete", now=BASE + 105)
    assert resumed["id"] == job["id"]
    assert resumed["owner"] == "after-delete"
    assert other.summary()["habilitadas"] == 1


def test_history_guard_releases_lock_after_cleanup_exception(queue):
    job = enqueue(queue, now=BASE + 100)
    with pytest.raises(RuntimeError, match="cleanup fixture"):
        with queue.history_reset_guard():
            raise RuntimeError("cleanup fixture")
    assert queue.claim("retry", now=BASE + 101)["id"] == job["id"]


def test_history_gate_is_released_after_process_crash(queue):
    job = enqueue(queue, now=BASE + 100)
    gate = queue.path.with_name("vendas_history_gate.db")
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import os, sqlite3, sys; c = sqlite3.connect(sys.argv[1]); "
        "c.execute('BEGIN IMMEDIATE'); os._exit(0)", str(gate),
    ], timeout=15, check=False, capture_output=True)
    assert result.returncode == 0
    assert queue.claim("after-crash", now=BASE + 101)["id"] == job["id"]
