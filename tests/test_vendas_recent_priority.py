"""Behavioral coverage for newest-first daily turns and durable account exclusion."""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from backend.modules.vendas.sync_queue import INTERVAL_SECONDS, SyncQueue


NOW = dt.datetime.fromisoformat("2026-10-26T12:00:00-03:00").timestamp()


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("recent-test", tenant_path=tmp_path)


def enqueue(queue, store, *, start="2026-10-24", end="2026-10-26", **kwargs):
    return queue.enqueue(store, store, start, end, now=NOW, **kwargs)


def newest(job):
    end = dt.date.fromisoformat(job["data_fim"])
    done = set(job["dias_concluidos"])
    while str(end) in done:
        end -= dt.timedelta(days=1)
    return str(end)


def test_recent_days_of_all_accounts_precede_older_pending_days(queue):
    for store in "ABCDEF":
        enqueue(queue, store)
    events = []
    for turn in range(18):
        owner = f"worker-{turn}"
        job = queue.claim(owner, now=NOW + turn)
        day = newest(job)
        events.append((job["store_id"], day))
        queue.complete_day(job["id"], owner, day, {"vendas": 1}, now=NOW + turn)
        if len(job["dias_concluidos"]) + 1 == job["dias_total"]:
            queue.complete_job(job["id"], owner, now=NOW + turn)
        else:
            queue.yield_job(job["id"], owner, now=NOW + turn + .1)
    assert [day for _, day in events] == ["2026-10-26"] * 6 + ["2026-10-25"] * 6 + ["2026-10-24"] * 6
    assert all({store for store, _ in events[index:index + 6]} == set("ABCDEF") for index in (0, 6, 12))
    assert queue.summary(now=NOW + 30)["dias_confirmados"] == 18


def test_waiting_account_wins_a_tie_after_another_account_yields(queue):
    enqueue(queue, "A", start="2026-10-25", end="2026-10-25")
    enqueue(queue, "B")
    first = queue.claim("one", now=NOW)
    assert first["store_id"] == "B"
    queue.complete_day(first["id"], "one", "2026-10-26", {}, now=NOW + 1)
    queue.yield_job(first["id"], "one", now=NOW + 1)
    assert queue.claim("two", now=NOW + 2)["store_id"] == "A"


def test_verified_company_excludes_other_store_and_persists_across_restart(queue):
    first_id = enqueue(queue, "A")["id"]
    second_id = enqueue(queue, "B")["id"]
    first = queue.claim("one", now=NOW)
    second = queue.claim("two", now=NOW)
    assert {first["id"], second["id"]} == {first_id, second_id}
    assert queue.bind_job_account(first["id"], "one", "123", now=NOW)
    assert not queue.bind_job_account(second["id"], "two", "123", now=NOW)
    restarted = SyncQueue("recent-test", tenant_path=queue.path.parent)
    assert restarted.claim("three", now=NOW + 1) is None
    queue.complete_day(first["id"], "one", "2026-10-26", {}, now=NOW + 1)
    queue.yield_job(first["id"], "one", now=NOW + 1)
    assert restarted.claim("four", now=NOW + 2)["id"] == second["id"]


def test_unidentified_company_collects_exclusively_against_known_accounts(queue):
    enqueue(queue, "A")
    enqueue(queue, "B")
    first = queue.claim("unknown", now=NOW)
    second = queue.claim("known", now=NOW)
    assert queue.bind_job_account(first["id"], "unknown", "", now=NOW)
    assert not queue.bind_job_account(second["id"], "known", "123", now=NOW)
    assert queue.claim("must-wait", now=NOW) is None
    queue.complete_day(first["id"], "unknown", "2026-10-26", {}, now=NOW)
    queue.yield_job(first["id"], "unknown", now=NOW + 1)
    known = queue.claim("known-next", now=NOW + 2)
    assert known["id"] == second["id"]
    assert queue.bind_job_account(known["id"], "known-next", "123", now=NOW + 2)
    assert queue.claim("unknown-waits", now=NOW + 2) is None


def test_voluntary_turns_do_not_increase_failure_backoff_or_finish_cycle(queue):
    job = enqueue(queue, "A")
    for turn in range(8):
        claimed = queue.claim(str(turn), now=NOW + turn)
        queue.yield_job(claimed["id"], str(turn), now=NOW + turn)
    claimed = queue.claim("failed", now=NOW + 10)
    failed = queue.fail_job(job["id"], "failed", "Unavailable", now=NOW + 10)
    assert failed["status"] == "retry" and failed["next_attempt"] == NOW + 25
    assert failed["consecutive_failures"] == 1
    assert queue.summary(now=NOW + 10)["pendentes"] == 1
    assert queue.claim("early", now=NOW + 24) is None
    assert queue.claim("ready", now=NOW + 25)


def test_revision_counts_only_real_confirmations_and_resume_keeps_it(queue):
    job = enqueue(queue, "A")
    queue.claim("one", now=NOW)
    assert queue.summary(now=NOW)["data_revision"] == 0
    queue.complete_day(job["id"], "one", "2026-10-26", {"total": 9}, now=NOW)
    queue.complete_day(job["id"], "one", "2026-10-26", {"total": 999}, now=NOW)
    queue.yield_job(job["id"], "one", now=NOW)
    summary = SyncQueue("recent-test", tenant_path=queue.path.parent).summary(now=NOW)
    assert summary["data_revision"] == 1
    assert summary["dias_confirmados"] == 1 and summary["dias_total"] == 3
    assert summary["lojas"][0]["proximo_dia"] == "2026-10-25"
    assert summary["lojas"][0]["aguardando"] is True


def test_completion_does_not_advance_watermark_over_a_gap_and_cycle_waits(queue):
    enqueue(queue, "A", automatico=True)
    job = queue.claim("one", now=NOW)
    queue.complete_day(job["id"], "one", "2026-10-26", {}, now=NOW)
    queue.yield_job(job["id"], "one", now=NOW)
    assert queue.schedule_due("2026-10-26", now=NOW + INTERVAL_SECONDS * 2) == []
    with sqlite3.connect(queue.path) as conn:
        assert conn.execute("SELECT last_success_day FROM plans").fetchone()[0] is None
    job = queue.claim("two", now=NOW + 1)
    with pytest.raises(ValueError, match="dias"):
        queue.complete_job(job["id"], "two", now=NOW + 1)
    for day in ("2026-10-25", "2026-10-24"):
        queue.complete_day(job["id"], "two", day, {}, now=NOW + 2)
    queue.complete_job(job["id"], "two", now=NOW + 3)
    assert queue.schedule_due("2026-10-26", now=NOW + 3 + INTERVAL_SECONDS - 1) == []
    assert len(queue.schedule_due("2026-10-26", now=NOW + 3 + INTERVAL_SECONDS)) == 1


def test_additive_migration_preserves_existing_lease_and_confirmed_days(queue):
    job = enqueue(queue, "A")
    queue.claim("one", now=NOW)
    queue.complete_day(job["id"], "one", "2026-10-26", {"total": 9}, now=NOW)
    with sqlite3.connect(queue.path) as conn:
        for column in ("account_id", "waiting_since", "consecutive_failures"):
            conn.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
        conn.execute("DROP TABLE sync_revision")
    migrated = SyncQueue("recent-test", tenant_path=queue.path.parent)
    saved = migrated.get_job(job["id"])
    assert migrated.owns(job["id"], "one", now=NOW + 1)
    assert saved["dias_concluidos"] == ["2026-10-26"]
    assert saved["resultados_dias"]["2026-10-26"] == {"total": 9}
    assert saved["account_id"] is None and saved["consecutive_failures"] == 0


def test_future_dates_and_days_outside_selection_are_not_claimed_as_work(queue):
    enqueue(queue, "A", start="2026-10-01", end="2026-10-03")
    summary = queue.summary(now=NOW)
    assert summary["lojas"][0]["proximo_dia"] == "2026-10-03"
    enqueue(queue, "B", start="2026-10-25", end="2026-11-01")
    summary = queue.summary(now=NOW)
    selected = next(store for store in summary["lojas"] if store["store_id"] == "B")
    assert selected["proximo_dia"] == "2026-10-26" and selected["dias_total"] == 2


def test_automatic_mode_does_not_expand_a_completed_historical_selection(queue):
    job = enqueue(queue, "A", start="2026-10-01", end="2026-10-01", automatico=True)
    queue.claim("first", now=NOW)
    queue.complete_day(job["id"], "first", "2026-10-01", {}, now=NOW)
    queue.complete_job(job["id"], "first", now=NOW)
    assert queue.schedule_due("2026-10-26", now=NOW + INTERVAL_SECONDS) == []
    assert len(queue.list_jobs()) == 1


def test_progress_excludes_completed_requests_from_a_previous_batch(queue):
    old = enqueue(queue, "Old", start="2026-01-01", end="2026-01-01")
    queue.claim("old", now=NOW)
    queue.complete_day(old["id"], "old", "2026-01-01", {}, now=NOW)
    queue.complete_job(old["id"], "old", now=NOW)
    current = enqueue(queue, "Current")
    state = queue.summary(now=NOW)
    assert state["dias_confirmados"] == 0 and state["dias_total"] == 3
    claimed = queue.claim("current", now=NOW)
    queue.complete_day(current["id"], "current", "2026-10-26", {}, now=NOW)
    queue.yield_job(current["id"], "current", now=NOW)
    assert queue.summary(now=NOW)["dias_confirmados"] == 1
