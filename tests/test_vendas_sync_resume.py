from __future__ import annotations

import asyncio
from datetime import date
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core import AppPaths
from backend.modules.vendas import dependencies, progress, sync_automation, sync_engine
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.state import (
    SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS,
    SYNC_META, SYNC_PROGRESS, SYNC_THREAD_CONTEXT,
)
from backend.modules.vendas.sync_queue import SyncQueue
from backend.schemas import VendasSyncRequest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logging.getLogger("sync-test"))
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(paths=paths, logger=paths.logger))
    dictionaries = [SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS]
    saved = [dict(value) for value in dictionaries]
    for value in dictionaries:
        value.clear()
    monkeypatch.setattr(sync_automation, "_RUNNING", {})
    monkeypatch.setattr(sync_automation, "_AUTH", {})
    monkeypatch.setattr(sync_automation, "start", lambda: None)
    monkeypatch.setattr(sync_automation, "remember_authorization", lambda *_: None)
    monkeypatch.setattr(sync_automation, "current", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_resolve_store", lambda req, client: req.model_copy(
        update={"store_id": req.store_id or "store-" + req.loja.replace(" ", "-")}))
    yield tmp_path
    for value, snapshot in zip(dictionaries, saved):
        value.clear()
        value.update(snapshot)
    for attr in ("job_id", "loja", "queue_job", "queue_owner", "lease_check"):
        if hasattr(SYNC_THREAD_CONTEXT, attr):
            delattr(SYNC_THREAD_CONTEXT, attr)


class WaitingThread:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.alive = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive


def test_three_stores_are_durable_even_when_only_two_can_start(isolated, monkeypatch):
    monkeypatch.setattr(sync_automation.threading, "Thread", WaitingThread)
    requests = [VendasSyncRequest(loja=name, data_inicio="2026-10-01", data_fim="2026-10-02",
                                 automatico=True) for name in ("A", "B", "C")]
    results = [sync_engine.sincronizar_vendas(req, "tenant-a") for req in requests]
    repeated = sync_engine.sincronizar_vendas(requests[0], "tenant-a")
    queue = SyncQueue("tenant-a")
    assert results[0]["started"] and results[1]["started"]
    assert results[2]["queued"]
    assert repeated["job_id"] == results[0]["job_id"]
    assert len(queue.list_jobs()) == 3
    assert len([job for job in queue.list_jobs() if job["status"] == "running"]) == 2
    state = progress.progresso_sincronizacao_vendas("tenant-a")
    assert state["active"] and state["automacao"]["pendentes"] == 3
    progress.cancelar_sincronizacao_vendas("tenant-a")
    assert all(job["status"] == "paused" for job in queue.list_jobs())
    assert queue.summary()["habilitadas"] == 0


def _bind(job, queue, owner):
    SYNC_THREAD_CONTEXT.job_id = job["id"]
    SYNC_THREAD_CONTEXT.queue_job = job
    SYNC_THREAD_CONTEXT.queue_owner = owner
    SYNC_THREAD_CONTEXT.lease_check = lambda: queue.owns(job["id"], owner)


def test_interrupted_days_resume_and_empty_day_is_a_real_acknowledgement(isolated, monkeypatch):
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("store-a", "A", "2026-10-01", "2026-10-03")
    job = queue.claim("first")
    _bind(job, queue, "first")
    seen = []
    async def interrupted(req, client, reset_estado=False):
        seen.append(req.data_inicio)
        if req.data_inicio == "2026-10-02":
            raise VendasDomainError(status_code=503, detail="fixture outage")
        return {"success": True, "total": 0}
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", interrupted)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-03")
    with pytest.raises(VendasDomainError):
        asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert queue.get_job(job["id"])["dias_concluidos"] == ["2026-10-01"]
    queue.fail_job(job["id"], "first", "Indisponível.", retry_after=1)
    # A POST resumes the same saved cycle instead of depending on the UI's row count.
    again = queue.enqueue("store-a", "A", req.data_inicio, req.data_fim)
    assert again["id"] == job["id"]
    resumed = queue.claim("second")
    _bind(resumed, queue, "second")
    async def complete(req, client, reset_estado=False):
        seen.append(req.data_inicio)
        return {"success": True, "total": 1}
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", complete)
    asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert seen == ["2026-10-01", "2026-10-02", "2026-10-02", "2026-10-03"]
    assert queue.get_job(job["id"])["status"] == "complete"


def test_lease_loss_after_data_commit_does_not_acknowledge_the_day(isolated, monkeypatch):
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01")
    job = queue.claim("first")
    _bind(job, queue, "first")
    async def cancel_after_commit(req, client, reset_estado=False):
        queue.pause_all()
        return {"success": True, "total": 1}
    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", cancel_after_commit)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-01")
    with pytest.raises(VendasDomainError):
        asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert queue.get_job(job["id"])["dias_concluidos"] == []
    assert queue.get_job(job["id"])["status"] == "paused"


def test_worker_waits_for_the_original_authorization_after_restart(isolated, monkeypatch):
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01", requires_session=True)
    job = queue.claim("first")
    import backend.services.central_accounts_client as central
    monkeypatch.setattr(central, "current", lambda *_: None)
    monkeypatch.setattr(sync_engine, "_resolve_store", lambda *_: pytest.fail("must not fall back to another store context"))
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-01")
    sync_engine._sincronizar_vendas_thread_worker(req, "tenant-a", job["id"], "first")
    saved = queue.get_job(job["id"])
    assert saved["status"] == "retry" and not saved["dias_concluidos"]
    assert "login" in saved["last_error"]


def test_failed_job_is_not_displayed_as_complete_between_attempts(isolated):
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01", automatico=True)
    job = queue.claim("first")
    queue.fail_job(job["id"], "first", "Indisponível.", retry_after=300)
    state = progress.progresso_sincronizacao_vendas("tenant-a")
    assert not state["active"]
    assert state["progress"]["concluido"] is False
    assert state["automacao"]["pendentes"] == 1


def test_local_checkpoints_cannot_cross_shared_sync():
    from backend.services.shared_sync_common import _shared_sync_path_permanently_excluded
    for name in ("vendas_sync_queue.db", "vendas_collection_checkpoint.db"):
        for suffix in ("", "-wal", "-shm", ".backup"):
            assert _shared_sync_path_permanently_excluded(name + suffix)
    assert not _shared_sync_path_permanently_excluded("vendas_historico_loja.db")


def test_day_progress_never_reports_the_whole_job_complete(isolated):
    SYNC_THREAD_CONTEXT.job_id = "job-test"
    SYNC_DAY_CONTEXT["job-test"] = {"dia_indice": 1, "total_dias": 3, "dia_atual": "2026-10-01"}
    progress._set_progresso("tenant-a", {"etapa": "Finalizado", "percentual": 100, "concluido": True})
    assert SYNC_PROGRESS["job-test"]["concluido"] is False
    assert SYNC_PROGRESS["job-test"]["percentual"] < 100


def test_accepted_response_survives_a_worker_that_finishes_before_post_returns(isolated, monkeypatch):
    def finish(client_id):
        queue = SyncQueue(client_id)
        job = queue.claim("quick")
        queue.complete_day(job["id"], "quick", "2026-10-01", {"vendas": 0})
        queue.complete_job(job["id"], "quick")
    monkeypatch.setattr(sync_automation, "dispatch", finish)
    req = VendasSyncRequest(loja="A", data_inicio="2026-10-01", data_fim="2026-10-01")
    result = sync_engine.sincronizar_vendas(req, "tenant-a")
    assert result["accepted"] and result["status"] == "complete"


def test_old_pause_does_not_hide_a_later_completed_request(isolated):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-09-01", "2026-09-01")
    queue.pause_all()
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01", automatico=True)
    job = queue.claim("latest")
    queue.complete_day(job["id"], "latest", "2026-10-01", {"vendas": 0})
    queue.complete_job(job["id"], "latest")
    state = progress.progresso_sincronizacao_vendas("tenant-a")
    assert state["automacao"]["pendentes"] == 0
    assert state["automacao"]["pausados"] == 1
    assert state["automacao"]["lojas"][0]["status"] == "complete"
    assert state["automacao"]["lojas"][0]["proxima_execucao"]
    assert state["progress"]["concluido"] is True


def test_scheduler_uses_the_same_sao_paulo_day_as_completion(isolated, monkeypatch):
    from datetime import datetime, timezone
    captured = []
    class FixedClock:
        @classmethod
        def now(cls, tz):
            return datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(sync_automation, "datetime", FixedClock)
    monkeypatch.setattr(sync_automation, "_tenants", lambda: ["tenant-a"])
    monkeypatch.setattr(sync_automation, "SyncQueue", lambda _: SimpleNamespace(
        schedule_due=lambda today: captured.append(today)))
    monkeypatch.setattr(sync_automation, "dispatch", lambda *_: None)
    sync_automation._STOP.clear()
    sync_automation.tick()
    assert captured == [date(2026, 10, 15)]
