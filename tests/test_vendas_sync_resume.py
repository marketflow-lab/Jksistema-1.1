from __future__ import annotations

import asyncio
from datetime import date
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

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
    composition_sync = ModuleType("backend.modules.vendas.composition_sync")
    composition_sync.sincronizar_composicoes_vendas = lambda *_, **__: {"updated": 0, "pending": False}
    monkeypatch.setitem(sys.modules, composition_sync.__name__, composition_sync)
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


def _composition_hook(monkeypatch, callback):
    module = sys.modules["backend.modules.vendas.composition_sync"]
    monkeypatch.setattr(module, "sincronizar_composicoes_vendas", callback)


def test_compositions_run_after_committed_days_before_job_completion(isolated, monkeypatch):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-02")
    job = queue.claim("first")
    _bind(job, queue, "first")
    calls = []

    async def collect(req, client, reset_estado=False):
        calls.append(req.data_inicio)
        return {"success": True, "total": 1, "vendas_processadas": 1}

    def compositions(client_id, store_id, *, cancel_callback):
        saved = queue.get_job(job["id"])
        assert saved["dias_concluidos"] == ["2026-10-01", "2026-10-02"]
        assert saved["status"] == "running"
        assert SYNC_THREAD_CONTEXT.queue_owner == "first"
        assert progress._sync_context_key(client_id) == job["id"]
        stage = SYNC_PROGRESS[job["id"]]
        assert stage["etapa"] == "Composições" and stage["concluido"] is False
        assert stage["percentual"] == 99
        assert stage["mensagem"] == "Conferindo as composições dos produtos vendidos."
        cancel_callback()
        calls.append((client_id, store_id))
        return {"updated": 2, "pending": False}

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", collect)
    _composition_hook(monkeypatch, compositions)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-02")
    result = asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert calls == ["2026-10-01", "2026-10-02", ("tenant-a", "store-a")]
    assert result["success"] and result["total"] == result["vendas_processadas"] == 2
    assert result["composicoes"] == {"updated": 2, "pending": False}
    final = SYNC_PROGRESS[job["id"]]
    assert final["result"]["composicoes"] == result["composicoes"]
    assert final["etapa"] == "Finalizado" and final["concluido"] is True
    assert final["percentual"] == 100 and final["mensagem"] == "Período e composições conferidos."
    assert queue.get_job(job["id"])["status"] == "complete"


@pytest.mark.parametrize("failure_type", ["runtime", "provider_http"])
def test_composition_failure_preserves_sales_and_reports_safe_pending_status(
    isolated, monkeypatch, caplog, failure_type,
):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01")
    job = queue.claim("first")
    _bind(job, queue, "first")

    async def collect(req, client, reset_estado=False):
        return {"success": True, "total": 3, "notas_entrada_total": 2}

    def unavailable(*_, **__):
        if failure_type == "provider_http":
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="fixture-private-response-never-log")
        raise RuntimeError("fixture-private-response-never-log")

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", collect)
    _composition_hook(monkeypatch, unavailable)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-01")
    result = asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert result["success"] and result["total"] == 3 and result["notas_entrada_total"] == 2
    assert result["composicoes"] == {"updated": 0, "pending": True}
    saved = queue.get_job(job["id"])
    assert saved["status"] == "complete" and saved["dias_concluidos"] == ["2026-10-01"]
    final = SYNC_PROGRESS[job["id"]]
    assert final["etapa"] == "Finalizado" and final["concluido"] is True
    assert final["percentual"] == 100
    assert final["mensagem"] == (
        "Período conferido; composições pendentes serão retomadas na próxima sincronização."
    )
    assert "conferência de composições ficará pendente" in caplog.text
    assert "fixture-private-response-never-log" not in caplog.text


def test_resume_with_all_days_committed_still_retries_compositions(isolated, monkeypatch):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-02")
    first = queue.claim("first")
    for day in ("2026-10-01", "2026-10-02"):
        queue.complete_day(first["id"], "first", day, {"success": True, "total": 0})
    queue.fail_job(first["id"], "first", "Interrupção antes de concluir o período.", retry_after=1)
    again = queue.enqueue("store-a", "A", "2026-10-01", "2026-10-02")
    resumed = queue.claim("second")
    assert again["id"] == resumed["id"] == first["id"]
    _bind(resumed, queue, "second")
    called = []

    async def already_committed(*_, **__):
        pytest.fail("Committed days must not be downloaded again")

    def compositions(client_id, store_id, *, cancel_callback):
        cancel_callback()
        assert queue.get_job(resumed["id"])["status"] == "running"
        called.append((client_id, store_id))
        return {"updated": 1, "pending": True}

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", already_committed)
    _composition_hook(monkeypatch, compositions)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-02")
    result = asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert called == [("tenant-a", "store-a")]
    assert result["composicoes"] == {"updated": 1, "pending": True}
    assert queue.get_job(resumed["id"])["status"] == "complete"


@pytest.mark.parametrize("check_inside_collector", [False, True])
def test_cancellation_during_composition_stage_prevents_job_completion(
    isolated, monkeypatch, check_inside_collector,
):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01")
    job = queue.claim("first")
    _bind(job, queue, "first")

    async def collect(req, client, reset_estado=False):
        return {"success": True, "total": 1}

    def compositions(client_id, store_id, *, cancel_callback):
        assert queue.get_job(job["id"])["dias_concluidos"] == ["2026-10-01"]
        queue.pause_all()
        if check_inside_collector:
            cancel_callback()
        return {"updated": 1, "pending": False}

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", collect)
    _composition_hook(monkeypatch, compositions)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-01")
    with pytest.raises(VendasDomainError) as exc:
        asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert exc.value.status_code == 409
    saved = queue.get_job(job["id"])
    assert saved["status"] == "paused" and saved["dias_concluidos"] == ["2026-10-01"]
    assert SYNC_PROGRESS[job["id"]].get("concluido") is not True


def test_one_shot_job_cancellation_is_propagated_with_a_valid_lease(isolated, monkeypatch, caplog):
    queue = SyncQueue("tenant-a")
    queue.enqueue("store-a", "A", "2026-10-01", "2026-10-01")
    job = queue.claim("first")
    _bind(job, queue, "first")

    async def collect(req, client, reset_estado=False):
        return {"success": True, "total": 1}

    def compositions(client_id, store_id, *, cancel_callback):
        assert queue.get_job(job["id"])["dias_concluidos"] == ["2026-10-01"]
        SYNC_CANCEL_FLAGS[job["id"]] = True
        cancel_callback()
        pytest.fail("The real callback must raise after consuming the job flag")

    monkeypatch.setattr(sync_engine, "_sincronizar_vendas_periodo_impl", collect)
    _composition_hook(monkeypatch, compositions)
    req = VendasSyncRequest(loja="A", store_id="store-a", data_inicio="2026-10-01", data_fim="2026-10-01")
    with pytest.raises(VendasDomainError) as exc:
        asyncio.run(sync_engine._sincronizar_vendas_impl(req, "tenant-a"))
    assert exc.value.status_code == 409
    assert job["id"] not in SYNC_CANCEL_FLAGS and not SYNC_CANCEL_FLAGS.get("tenant-a")
    assert queue.owns(job["id"], "first")
    saved = queue.get_job(job["id"])
    assert saved["status"] == "running" and saved["dias_concluidos"] == ["2026-10-01"]
    assert SYNC_PROGRESS[job["id"]].get("concluido") is not True
    assert "conferência de composições ficará pendente" not in caplog.text
