from __future__ import annotations

import datetime as dt
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.modules.vendas.sync_queue import SyncQueue


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("tenant-a", tenant_path=tmp_path / "tenant-a")


def enqueue(queue, store="store-1", start="2026-10-01", end="2026-10-02", **kwargs):
    return queue.enqueue(store, "Loja " + store, start, end, **kwargs)


def finish(queue, job, owner, now, today=None):
    for offset in range(job["dias_total"]):
        day = dt.date.fromisoformat(job["data_inicio"]) + dt.timedelta(days=offset)
        queue.complete_day(job["id"], owner, str(day), {"vendas": 0, "notas": 0}, now=now)
    return queue.complete_job(job["id"], owner, now=now, today=today)


def test_expired_lease_resumes_only_acknowledged_days_including_empty(queue):
    original = enqueue(queue, now=100)
    claimed = queue.claim("crashed-worker", now=100, lease_seconds=10)
    queue.complete_day(claimed["id"], "crashed-worker", "2026-10-01", {"vendas": 0}, now=101)
    restarted = SyncQueue("tenant-a", tenant_path=queue.path.parent)
    assert restarted.claim("new-worker", now=109) is None
    assert not restarted.owns(original["id"], "crashed-worker", now=110)
    assert not restarted.heartbeat(original["id"], "crashed-worker", now=110)
    with pytest.raises(RuntimeError):
        restarted.complete_day(original["id"], "crashed-worker", "2026-10-02", {}, now=110)
    resumed = restarted.claim("new-worker", now=110)
    assert resumed["id"] == original["id"]
    assert resumed["cycle_id"] == original["cycle_id"]
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert resumed["resultados_dias"]["2026-10-01"] == {"vendas": 0}
    assert resumed["attempts"] == 2
    with pytest.raises(ValueError, match="dias"):
        restarted.complete_job(resumed["id"], "new-worker", now=111)
    restarted.complete_day(resumed["id"], "new-worker", "2026-10-02", {"vendas": 2}, now=112)
    done = restarted.complete_job(resumed["id"], "new-worker", now=113)
    assert done["status"] == "complete"
    next_cycle = enqueue(restarted, now=114)
    assert next_cycle["id"] != original["id"]
    assert next_cycle["dias_concluidos"] == []


def test_day_acknowledgement_is_idempotent_and_filters_payload(queue):
    job = enqueue(queue, now=100)
    queue.claim("worker", now=100)
    first = queue.complete_day(job["id"], "worker", "2026-10-01", {
        "vendas": 3, "notas": 1, "access_token": "DO-NOT-STORE",
        "checkpoint": {"documentos_salvos": 4, "ultimo_tipo": "pedidos",
                       "ultimo_id": "1234", "atualizado_em": "2026-10-01T12:00:00+00:00",
                       "authorization": "DO-NOT-STORE"}}, now=101)
    second = queue.complete_day(job["id"], "worker", "2026-10-01", {"vendas": 99}, now=102)
    assert first["resultados_dias"] == second["resultados_dias"]
    assert second["checkpoint"]["ultimo_id"] == "1234"
    assert "DO-NOT-STORE" not in repr(second)
    assert b"DO-NOT-STORE" not in queue.path.read_bytes()
    with pytest.raises(ValueError, match="fora"):
        queue.complete_day(job["id"], "worker", "2026-10-03", {}, now=103)


def test_claim_is_atomic_limits_two_per_tenant_and_one_per_store(queue, tmp_path):
    enqueue(queue, store="store-1", now=100)
    enqueue(queue, store="store-1", start="2026-09-01", end="2026-09-02", now=101)
    enqueue(queue, store="store-2", now=102)
    enqueue(queue, store="store-3", now=103)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda i: queue.claim(f"worker-{i}", now=110, limit=50), range(8)))
    claimed = [job for job in claimed if job]
    assert len(claimed) == 2
    assert len({job["store_id"] for job in claimed}) == 2
    other = SyncQueue("tenant-b", tenant_path=tmp_path / "tenant-b")
    enqueue(other, now=110)
    assert other.claim("independent-worker", now=110)
    assert other.get_job(claimed[0]["id"]) is None
    with pytest.raises(ValueError, match="outro cliente"):
        SyncQueue("tenant-b", tenant_path=queue.path.parent)


def test_heartbeat_requires_live_owner_and_excluded_store_is_not_claimed(queue):
    first = enqueue(queue, store="store-1", now=100)
    second = enqueue(queue, store="store-2", now=100)
    claimed = queue.claim("worker", now=100, exclude_store_ids=["store-1"])
    assert claimed["id"] == second["id"]
    assert not queue.heartbeat(second["id"], "other", now=101)
    assert queue.heartbeat(second["id"], "worker", now=219, lease_seconds=200)
    assert queue.owns(second["id"], "worker", now=418)
    assert not queue.owns(second["id"], "worker", now=419)
    assert queue.claim("other", now=200, exclude_store_ids=["store-1"]) is None
    assert queue.claim("other", now=200)["id"] == first["id"]


def test_retry_backoff_block_and_explicit_resume_preserve_checkpoint(queue):
    job = enqueue(queue, now=100)
    queue.claim("worker", now=100)
    queue.complete_day(job["id"], "worker", "2026-10-01", {}, now=101)
    failed = queue.fail_job(job["id"], "worker", "Servico indisponivel.", now=102)
    assert failed["status"] == "retry"
    assert failed["next_attempt"] == 117
    assert queue.claim("next", now=116) is None
    resumed = queue.claim("next", now=117)
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    failed = queue.fail_job(job["id"], "next", "Servico indisponivel.", now=118)
    assert failed["next_attempt"] == 148
    queue.claim("third", now=148)
    blocked = queue.fail_job(job["id"], "third", "Autorizacao necessaria.", retryable=False, now=149)
    assert blocked["status"] == "blocked"
    assert queue.claim("fourth", now=10000) is None
    resumed = enqueue(queue, now=10001)
    assert resumed["id"] == job["id"]
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert queue.claim("fourth", now=10001)


def test_pause_invalidates_running_lease_and_retains_progress(queue):
    job = enqueue(queue, automatico=True, now=100)
    queue.claim("worker", now=100)
    queue.complete_day(job["id"], "worker", "2026-10-01", {}, now=101)
    queue.pause_all(now=102)
    assert not queue.owns(job["id"], "worker", now=103)
    assert queue.claim("other", now=10000) is None
    assert queue.schedule_due(dt.date(2026, 10, 15), now=10000) == []
    summary = queue.summary()
    assert summary["habilitadas"] == 0
    assert summary["pendentes"] == 0
    assert summary["pausados"] == 1
    assert summary["lojas"][0]["proxima_execucao"] is None
    resumed = enqueue(queue, automatico=True, now=10001)
    assert resumed["dias_concluidos"] == ["2026-10-01"]
    assert resumed["id"] == job["id"]


def test_manual_http_replay_does_not_interrupt_running_manual_job(queue):
    first = enqueue(queue, now=100)
    queue.claim("worker", now=100)
    replay = enqueue(queue, now=101)
    assert replay["id"] == first["id"]
    assert replay["status"] == "running"
    assert replay["owner"] == "worker"
    assert queue.owns(first["id"], "worker", now=102)
    assert queue.summary()["habilitadas"] == 0


def test_changed_plan_keeps_pending_old_period_and_paused_jobs_resume(queue):
    first = enqueue(queue, automatico=True, now=100)
    queue.claim("worker", now=100)
    queue.complete_day(first["id"], "worker", "2026-10-01", {}, now=101)
    queue.pause_all(now=102)
    second = enqueue(queue, start="2026-09-01", end="2026-09-03", automatico=True, now=103)
    assert second["id"] != first["id"]
    assert queue.get_job(first["id"])["dias_concluidos"] == ["2026-10-01"]
    assert queue.get_job(first["id"])["status"] == "queued"
    assert queue.summary()["pendentes"] == 2
    resumed = queue.claim("resumed", now=104)
    finish(queue, resumed, "resumed", now=105)
    assert queue.get_job(second["id"])["status"] == "queued"
    assert queue.schedule_due(dt.date(2026, 10, 15), now=2000) == []


def test_manual_request_disables_auto_without_dropping_other_period(queue):
    automatic = enqueue(queue, automatico=True, now=100)
    queue.claim("worker", now=100)
    manual = enqueue(queue, start="2026-09-01", end="2026-09-02", now=101)
    assert queue.get_job(automatic["id"])["status"] == "paused"
    assert not queue.owns(automatic["id"], "worker", now=102)
    assert queue.get_job(manual["id"])["status"] == "queued"
    assert queue.summary()["habilitadas"] == 0


def test_schedule_recovers_offline_gap_and_rotates_history_only_after_completion(queue):
    enqueue(queue, start="2026-01-01", end="2026-01-03", automatico=True,
            requires_session=True, now=100)
    initial = queue.claim("initial", now=100)
    finish(queue, initial, "initial", now=101, today="2026-01-03")
    due = queue.schedule_due(dt.date(2026, 10, 15), now=1001)
    assert {job["kind"] for job in due} == {"recent", "history"}
    recent = next(job for job in due if job["kind"] == "recent")
    history = next(job for job in due if job["kind"] == "history")
    assert recent["data_inicio"] == "2026-01-03"
    assert recent["data_fim"] == "2026-10-15"
    assert history["data_inicio"] == "2026-01-01"
    assert history["data_fim"] == "2026-01-07"
    assert all(job["requires_session"] and not job["forcar_resync"] for job in due)
    assert queue.schedule_due(dt.date(2026, 10, 15), now=1002) == []
    running = queue.claim("recent", now=1002)
    finish(queue, running, "recent", now=1003, today="2026-10-15")
    running = queue.claim("history", now=1004)
    assert running["id"] == history["id"]
    queue.complete_day(running["id"], "history", "2026-01-01", {}, now=1005)
    queue.fail_job(running["id"], "history", "Servico indisponivel.", now=1006)
    assert queue.schedule_due(dt.date(2026, 10, 16), now=90000) == []
    resumed = queue.claim("history-resumed", now=90000)
    assert resumed["id"] == history["id"]
    finish(queue, resumed, "history-resumed", now=90001)
    next_runs = queue.schedule_due(dt.date(2026, 10, 17), now=176402)
    historical = next(job for job in next_runs if job["kind"] == "history")
    latest = next(job for job in next_runs if job["kind"] == "recent")
    assert historical["data_inicio"] == "2026-01-08"
    assert historical["data_fim"] == "2026-01-14"
    assert latest["data_inicio"] == "2026-10-11"


def test_history_wraps_and_no_new_source_payload_is_stored(queue):
    enqueue(queue, start="2026-10-01", end="2026-10-15", automatico=True, now=100)
    finish(queue, queue.claim("initial", now=100), "initial", now=101, today="2026-10-15")
    first = queue.schedule_due(dt.date(2026, 10, 15), now=102)
    assert [(job["data_inicio"], job["data_fim"]) for job in first] == [("2026-10-01", "2026-10-07")]
    finish(queue, queue.claim("history", now=103), "history", now=104)
    second = queue.schedule_due(dt.date(2026, 10, 15), now=86505)
    assert next(job for job in second if job["kind"] == "history")["data_fim"] == "2026-10-08"
    finish(queue, queue.claim("recent", now=86506), "recent", now=86507)
    finish(queue, queue.claim("history", now=86508), "history", now=86509)
    third = queue.schedule_due(dt.date(2026, 10, 15), now=172910)
    assert next(job for job in third if job["kind"] == "history")["data_inicio"] == "2026-10-01"
    with sqlite3.connect(queue.path) as conn:
        assert {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "tenant", "jobs", "plans", "days"}


def test_future_empty_days_do_not_hide_gap_after_long_downtime(queue):
    enqueue(queue, start="2026-10-01", end="2026-12-31", automatico=True, now=100)
    finish(queue, queue.claim("initial", now=100), "initial", now=101, today="2026-10-15")
    scheduled = queue.schedule_due(dt.date(2027, 1, 15), now=1001)
    recent = next(job for job in scheduled if job["kind"] == "recent")
    assert recent["data_inicio"] == "2026-10-15"
    assert recent["data_fim"] == "2027-01-15"


def test_newer_manual_period_does_not_forget_original_start_or_unvisited_gap(queue):
    enqueue(queue, start="2026-01-01", end="2026-01-03", automatico=True, now=100)
    finish(queue, queue.claim("initial", now=100), "initial", now=101, today="2026-01-03")
    enqueue(queue, start="2026-10-01", end="2026-10-03", automatico=True, now=102)
    finish(queue, queue.claim("manual", now=102), "manual", now=103, today="2026-10-03")
    scheduled = queue.schedule_due(dt.date(2026, 10, 15), now=1003)
    assert next(job for job in scheduled if job["kind"] == "recent")["data_inicio"] == "2026-01-03"
    assert next(job for job in scheduled if job["kind"] == "history")["data_inicio"] == "2026-01-01"


def test_summary_prioritizes_running_then_operational_and_keeps_public_shape_small(queue):
    paused = enqueue(queue, automatico=True, now=100)
    manual = enqueue(queue, start="2026-09-01", end="2026-09-02", now=101)
    summary = queue.summary()
    assert summary["lojas"][0]["job_id"] == manual["id"]
    assert summary["lojas"][0]["pendentes"] == 1
    assert summary["lojas"][0]["pausados"] == 1
    queue.claim("manual", now=102)
    queue.complete_day(manual["id"], "manual", "2026-09-01", {"vendas": 0}, now=103)
    summary = queue.summary()
    assert summary["lojas"][0]["status"] == "running"
    assert {job["id"] for job in summary["jobs"]} == {paused["id"], manual["id"]}
    assert all(not {"owner", "signature", "requires_session", "resultados_dias"}.intersection(job)
               for job in summary["jobs"])


def test_summary_limits_completed_history_without_losing_pending_jobs(queue):
    for index in range(22):
        enqueue(queue, end="2026-10-01", now=100 + index * 2)
        finish(queue, queue.claim(f"worker-{index}", now=100 + index * 2),
               f"worker-{index}", now=101 + index * 2)
    pending = enqueue(queue, now=200)
    assert len(queue.list_jobs()) == 23
    summary = queue.summary()
    assert len(summary["jobs"]) == 21
    assert any(job["id"] == pending["id"] for job in summary["jobs"])


def test_paused_manual_period_does_not_block_a_new_automatic_plan(queue):
    manual = enqueue(queue, start="2026-09-01", end="2026-09-03", now=100)
    queue.pause_all(now=101)
    enqueue(queue, start="2026-10-01", end="2026-10-03", automatico=True, now=102)
    finish(queue, queue.claim("automatic", now=102), "automatic", now=103, today="2026-10-03")
    assert queue.get_job(manual["id"])["status"] == "paused"
    scheduled = queue.schedule_due(dt.date(2026, 10, 15), now=1003)
    assert {job["kind"] for job in scheduled} == {"recent", "history"}
    assert queue.get_job(manual["id"])["status"] == "paused"


@pytest.mark.parametrize("start,end", [("2026-10-03", "2026-10-01"),
    ("2026-1-01", "2026-10-01"), ("2026-02-30", "2026-10-01"),
    ("2026-10-01T00:00:00", "2026-10-02")])
def test_dates_are_validated_without_partial_plan_changes(queue, start, end):
    with pytest.raises(ValueError):
        enqueue(queue, start=start, end=end)
    assert queue.list_jobs() == []
    assert queue.summary()["lojas"] == []
