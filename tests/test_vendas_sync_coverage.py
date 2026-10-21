from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import sys
from contextlib import nullcontext
from types import ModuleType

import pytest

from backend.modules.vendas import sync_queue
from backend.modules.vendas.sync_queue import SyncQueue


def stamp(value):
    return dt.datetime.fromisoformat(value).timestamp()


NOW = stamp("2026-10-21T12:00:00+00:00")
CONFIRMED_AT = stamp("2026-10-20T12:00:00+00:00")


@pytest.fixture
def queue(tmp_path):
    return SyncQueue("tenant-a", tenant_path=tmp_path / "tenant-a")


def acknowledged_job(queue, *, start="2026-10-01", end="2026-10-04",
                     days=("2026-10-01", "2026-10-03"), status="paused",
                     confirmed_at=CONFIRMED_AT, store_id="store-a", kind="manual"):
    job = queue.enqueue(store_id, "Loja antiga", start, end, kind=kind,
                        now=confirmed_at - 10)
    claimed = queue.claim("source-worker", now=confirmed_at - 9)
    assert claimed["id"] == job["id"]
    for index, day in enumerate(days):
        queue.complete_day(job["id"], "source-worker", day,
                           {"vendas": index, "notas": 0}, now=confirmed_at)
    if status == "complete":
        queue.complete_job(job["id"], "source-worker", now=confirmed_at + 1)
    elif status == "retry":
        queue.fail_job(job["id"], "source-worker", "Indisponibilidade temporaria.",
                       retry_after=3 * 86400, now=confirmed_at + 1)
    else:
        assert status == "paused"
        queue.pause_all(now=confirmed_at + 1)
    return queue.get_job(job["id"])


def persisted_days(queue, job_id):
    with sqlite3.connect(queue.path) as conn:
        return conn.execute(
            "SELECT day, result, completed_at FROM days WHERE job_id = ? ORDER BY day",
            (job_id,),
        ).fetchall()


@pytest.mark.parametrize("status", ["paused", "retry"])
def test_changed_period_reuses_confirmed_days_without_filling_holes(queue, status):
    source = acknowledged_job(queue, status=status)
    requested = queue.enqueue("store-a", "Loja renomeada", "2026-10-01", "2026-10-05",
                              now=NOW)

    assert requested["id"] != source["id"]
    assert requested["loja"] == "Loja renomeada"
    assert requested["dias_concluidos"] == ["2026-10-01", "2026-10-03"]
    assert requested["resultados_dias"] == source["resultados_dias"]
    assert requested["resultados_dias"]["2026-10-01"]["vendas"] == 0
    assert persisted_days(queue, requested["id"]) == persisted_days(queue, source["id"])
    assert queue.get_job(source["id"])["status"] == status


def test_inheritance_is_limited_to_the_overlap_of_requested_days(queue):
    acknowledged_job(queue, start="2026-10-01", end="2026-10-03",
                     days=("2026-10-01", "2026-10-02", "2026-10-03"), status="complete")
    requested = queue.enqueue("store-a", "Loja A", "2026-10-02", "2026-10-04", now=NOW)

    assert requested["dias_total"] == 3
    assert requested["dias_concluidos"] == ["2026-10-02", "2026-10-03"]


def test_new_cycle_rechecks_the_last_seven_sao_paulo_days(queue):
    days = tuple(str(dt.date(2026, 10, 14) + dt.timedelta(days=i)) for i in range(8))
    acknowledged_job(queue, start=days[0], end=days[-1], days=days, status="complete",
                     confirmed_at=NOW - 60)
    requested = queue.enqueue("store-a", "Loja A", "2026-10-14", "2026-10-21", now=NOW)

    assert requested["dias_concluidos"] == ["2026-10-14"]
    assert requested["status"] == "queued"


def test_recent_boundary_uses_local_day_instead_of_utc_day(queue):
    local_october_20 = stamp("2026-10-21T01:00:00+00:00")
    acknowledged_job(queue, start="2026-10-13", end="2026-10-14",
                     days=("2026-10-13", "2026-10-14"), status="complete",
                     confirmed_at=stamp("2026-10-20T12:00:00+00:00"))
    requested = queue.enqueue("store-a", "Loja A", "2026-10-13", "2026-10-15",
                              now=local_october_20)

    assert requested["dias_concluidos"] == ["2026-10-13"]


@pytest.mark.parametrize("confirmed_at, expected", [
    ("2026-10-02T01:00:00+00:00", []),
    ("2026-10-02T03:00:00+00:00", ["2026-10-01"]),
])
def test_only_a_confirmation_after_local_day_end_can_cover_a_later_job(
    queue, confirmed_at, expected,
):
    acknowledged_job(queue, start="2026-10-01", end="2026-10-01",
                     days=("2026-10-01",), status="complete",
                     confirmed_at=stamp(confirmed_at))
    requested = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02", now=NOW)

    assert requested["dias_concluidos"] == expected


@pytest.mark.parametrize("options", [{"forcar_resync": True}, {"kind": "history"}])
def test_forced_and_historical_rechecks_receive_no_inherited_days(queue, options):
    acknowledged_job(queue, start="2026-10-01", end="2026-10-01",
                     days=("2026-10-01",), status="complete")
    requested = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02",
                              now=NOW, **options)

    assert requested["dias_concluidos"] == []
    claimed = queue.claim("recheck-worker", now=NOW)
    assert claimed["id"] == requested["id"]
    assert claimed["dias_concluidos"] == []


def test_history_window_cannot_reuse_a_pending_manual_job(queue):
    requested = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02", now=NOW)
    history = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02",
                            kind="history", now=NOW + 1)

    assert history["id"] != requested["id"]
    assert history["kind"] == "history"
    assert queue.get_job(requested["id"])["kind"] == "manual"


def test_confirmations_do_not_cross_store_or_tenant(queue, tmp_path):
    acknowledged_job(queue, start="2026-10-01", end="2026-10-01",
                     days=("2026-10-01",), status="complete")
    another_store = queue.enqueue("store-b", "Loja antiga", "2026-10-01", "2026-10-02",
                                  now=NOW)
    another_tenant_queue = SyncQueue("tenant-b", tenant_path=tmp_path / "tenant-b")
    another_tenant = another_tenant_queue.enqueue(
        "store-a", "Loja antiga", "2026-10-01", "2026-10-02", now=NOW,
    )

    assert another_store["dias_concluidos"] == []
    assert another_tenant["dias_concluidos"] == []


@pytest.mark.parametrize("status", ["paused", "retry"])
def test_same_pending_cycle_keeps_its_own_recent_day_acknowledgements(queue, status):
    source = acknowledged_job(queue, start="2026-10-20", end="2026-10-21",
                              days=("2026-10-20",), status=status,
                              confirmed_at=NOW - 60)
    before = persisted_days(queue, source["id"])
    resumed = queue.enqueue("store-a", "Loja renomeada", "2026-10-20", "2026-10-21", now=NOW)

    assert resumed["id"] == source["id"]
    assert resumed["dias_concluidos"] == ["2026-10-20"]
    assert persisted_days(queue, resumed["id"]) == before


def test_same_cycle_rechecks_preliminary_day_only_after_local_day_turns(queue):
    while_october_20 = stamp("2026-10-21T01:00:00+00:00")
    source = acknowledged_job(queue, start="2026-10-20", end="2026-10-21",
                              days=("2026-10-20",), confirmed_at=while_october_20)
    same_day = queue.enqueue("store-a", "Loja A", "2026-10-20", "2026-10-21",
                             now=while_october_20 + 60)
    next_day = queue.enqueue("store-a", "Loja A", "2026-10-20", "2026-10-21", now=NOW)

    assert same_day["id"] == next_day["id"] == source["id"]
    assert same_day["dias_concluidos"] == ["2026-10-20"]
    assert next_day["dias_concluidos"] == []


def test_waiting_job_refreshes_coverage_when_it_is_claimed(queue):
    source = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02", now=NOW - 10)
    queue.claim("source-worker", now=NOW - 9)
    waiting = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-03", now=NOW - 8)
    assert waiting["dias_concluidos"] == []

    queue.complete_day(source["id"], "source-worker", "2026-10-01", {"vendas": 0}, now=NOW - 7)
    queue.complete_day(source["id"], "source-worker", "2026-10-02", {"vendas": 2}, now=NOW - 6)
    queue.complete_job(source["id"], "source-worker", now=NOW - 5)
    claimed = queue.claim("next-worker", now=NOW)

    assert claimed["id"] == waiting["id"]
    assert claimed["dias_concluidos"] == ["2026-10-01", "2026-10-02"]
    assert persisted_days(queue, claimed["id"]) == persisted_days(queue, source["id"])


def test_successive_inheritance_keeps_the_original_confirmation_time(queue):
    original_time = stamp("2026-10-10T12:00:00+00:00")
    source = acknowledged_job(queue, start="2026-10-01", end="2026-10-01",
                              days=("2026-10-01",), status="complete",
                              confirmed_at=original_time)
    original_row = persisted_days(queue, source["id"])[0]
    middle = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-02",
                           now=CONFIRMED_AT)
    assert persisted_days(queue, middle["id"]) == [original_row]
    assert queue.claim("middle-worker", now=CONFIRMED_AT)["id"] == middle["id"]
    queue.complete_day(middle["id"], "middle-worker", "2026-10-02", {"vendas": 2},
                       now=CONFIRMED_AT + 1)
    queue.complete_job(middle["id"], "middle-worker", now=CONFIRMED_AT + 2)
    final = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-03", now=NOW)

    assert final["dias_concluidos"] == ["2026-10-01", "2026-10-02"]
    final_rows = persisted_days(queue, final["id"])
    assert final_rows == persisted_days(queue, middle["id"])
    assert final_rows[0] == original_row
    assert final_rows[0][2] == original_time


@pytest.mark.parametrize("options", [{}, {"forcar_resync": True}, {"kind": "history"}])
def test_later_claim_discards_a_future_empty_day_confirmed_before_it_happened(queue, options):
    early_check = stamp("2026-10-01T12:00:00+00:00")
    requested = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-03",
                              now=early_check - 10, **options)
    queue.claim("early-worker", now=early_check - 9)
    queue.complete_day(requested["id"], "early-worker", "2026-10-02", {"vendas": 0},
                       now=early_check)
    queue.fail_job(requested["id"], "early-worker", "Interrupcao temporaria.",
                   retry_after=1, now=early_check + 1)
    assert queue.get_job(requested["id"])["dias_concluidos"] == ["2026-10-02"]

    resumed = queue.claim("later-worker", now=NOW)

    assert resumed["id"] == requested["id"]
    assert resumed["dias_concluidos"] == []
    assert persisted_days(queue, resumed["id"]) == []


def test_running_replay_keeps_its_lease_and_snapshot_when_more_coverage_becomes_available(queue):
    acknowledged_job(queue, start="2026-10-14", end="2026-10-14",
                     days=("2026-10-14",), status="complete")
    requested = queue.enqueue("store-a", "Loja A", "2026-10-14", "2026-10-15",
                              now=CONFIRMED_AT + 10)
    claimed = queue.claim("active-worker", now=CONFIRMED_AT + 11,
                          lease_seconds=3 * 86400)
    assert claimed["id"] == requested["id"]
    assert claimed["dias_concluidos"] == []
    queue.complete_day(claimed["id"], "active-worker", "2026-10-15", {"vendas": 1},
                       now=CONFIRMED_AT + 12)
    snapshot = queue.get_job(claimed["id"])
    rows = persisted_days(queue, claimed["id"])

    # On October 21, October 14 has left the recent seven-day window. A new
    # period can now inherit it, while the active worker retains its snapshot.
    other_period = queue.enqueue("store-a", "Loja A", "2026-10-14", "2026-10-16", now=NOW)
    assert other_period["dias_concluidos"] == ["2026-10-14"]
    replay = queue.enqueue("store-a", "Loja A", "2026-10-14", "2026-10-15", now=NOW + 1)

    assert replay == snapshot
    assert replay["owner"] == "active-worker"
    assert replay["lease_until"] == claimed["lease_until"]
    assert persisted_days(queue, replay["id"]) == rows
    assert queue.owns(replay["id"], "active-worker", now=NOW + 1)


def test_daily_runner_downloads_only_gaps_after_the_period_changes(queue, monkeypatch):
    from backend.modules.vendas import sync_engine
    from backend.modules.vendas.state import SYNC_THREAD_CONTEXT
    from backend.schemas import VendasSyncRequest

    acknowledged_job(queue)
    requested = queue.enqueue("store-a", "Loja A", "2026-10-01", "2026-10-05", now=NOW)
    claimed = queue.claim("next-worker", now=NOW)
    assert claimed["id"] == requested["id"]

    monkeypatch.setattr(sync_queue.time, "time", lambda: NOW)
    monkeypatch.setattr(sync_engine, "SyncQueue", lambda client_id: queue)
    monkeypatch.setattr(sync_engine, "SYNC_META", {})
    monkeypatch.setattr(sync_engine, "SYNC_DAY_CONTEXT", {})
    monkeypatch.setattr(sync_engine, "_set_progresso", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_verificar_cancelamento", lambda *_: None)
    monkeypatch.setattr(sync_engine, "collection_checkpoint", lambda *_: nullcontext())
    monkeypatch.setattr(sync_engine, "collection_day_summary", lambda *_: {})
    monkeypatch.setattr(sync_engine, "finish_collection_day", lambda *_: None)
    for name, value in {"job_id": claimed["id"], "queue_job": claimed,
                        "queue_owner": "next-worker"}.items():
        monkeypatch.setattr(SYNC_THREAD_CONTEXT, name, value, raising=False)
    composition_module = ModuleType("backend.modules.vendas.composition_sync")
    composition_module.sincronizar_composicoes_vendas = lambda *_, **__: {
        "updated": 0, "pending": False,
    }
    monkeypatch.setitem(sys.modules, composition_module.__name__, composition_module)
    downloaded = []

    async def collect(day_request, client_id, reset_estado=False):
        assert client_id == "tenant-a" and day_request.store_id == "store-a"
        assert day_request.data_inicio == day_request.data_fim
        downloaded.append(day_request.data_inicio)
        return {"success": True, "total": 0}

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", collect)
    request = VendasSyncRequest(loja="Loja A", store_id="store-a",
                               data_inicio="2026-10-01", data_fim="2026-10-05")
    result = asyncio.run(sync_engine._sincronizar_vendas_impl(request, "tenant-a"))

    assert result["success"]
    assert downloaded == ["2026-10-02", "2026-10-04", "2026-10-05"]
    assert queue.get_job(claimed["id"])["status"] == "complete"
