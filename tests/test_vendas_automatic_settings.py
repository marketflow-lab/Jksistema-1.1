from __future__ import annotations

import datetime as dt
import logging

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from backend.core import AppPaths
from backend.modules.vendas import (
    LegacyBlingVendasAdapter, VendasModuleDependencies, create_vendas_module,
)
from backend.modules.vendas import sync_automation, sync_engine
from backend.modules.vendas.sync_queue import SyncQueue
from backend.schemas import VendasSyncRequest


NOW = dt.datetime(2026, 11, 16, 12, tzinfo=dt.timezone(dt.timedelta(hours=-3))).timestamp()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    def tenant(x_test_tenant: str = Header("tenant-a")):
        return x_test_tenant

    module = create_vendas_module(VendasModuleDependencies(
        get_tenant_id=tenant,
        paths=AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logging.getLogger(__name__)),
        logger=logging.getLogger(__name__), legacy=LegacyBlingVendasAdapter(),
    ))
    monkeypatch.setattr(sync_engine, "buscar_loja", lambda client, name, **kwargs: (
        {"nome": name, "store_id": client + "-store"} if name == "Loja A" else None
    ))
    monkeypatch.setattr(sync_automation, "start", lambda: None)
    monkeypatch.setattr(sync_automation, "dispatch", lambda _: [])
    monkeypatch.setattr(sync_automation, "remember_authorization", lambda *_: None)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app), module.sync_service


def request(**kwargs):
    return VendasSyncRequest(
        loja="Loja A", store_id="tenant-a-store", data_inicio="2026-11-13",
        data_fim="2026-11-16", **kwargs,
    )


def test_disabling_automatic_preserves_manual_work_other_stores_and_checkpoints(runtime):
    client, _ = runtime
    queue = SyncQueue("tenant-a")
    manual = queue.enqueue("tenant-a-store", "Loja A", "2026-10-01", "2026-10-02", automatico=False, now=NOW)
    job = queue.enqueue("tenant-a-store", "Loja A", "2026-11-13", "2026-11-16", automatico=True, now=NOW - 1)
    queue.claim("worker", now=NOW)
    queue.complete_day(job["id"], "worker", "2026-11-13", {"vendas": 4}, now=NOW + 1)
    other = queue.enqueue("other-store", "Outra loja", "2026-11-13", "2026-11-16", automatico=True, now=NOW)
    before = queue.get_job(job["id"])

    response = client.post("/api/vendas/sync/automatico", json=request(automatico=False).model_dump())

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["automatico"] is False
    paused = queue.get_job(job["id"])
    assert paused["status"] == "paused" and paused["owner"] is None
    assert paused["resultados_dias"] == before["resultados_dias"]
    assert not queue.owns(job["id"], "worker", now=NOW + 2)
    assert queue.get_job(manual["id"])["status"] == "queued"
    assert queue.get_job(other["id"])["status"] == "queued"
    assert len(queue.list_jobs()) == 3, "desativar não cria uma nova coleta"
    assert {row["store_id"]: row["habilitado"] for row in response.json()["automacao"]["lojas"]} == {
        "tenant-a-store": False, "other-store": True,
    }
    again = client.post("/api/vendas/sync/automatico", json=request(automatico=False).model_dump())
    assert again.status_code == 200 and len(queue.list_jobs()) == 3


def test_enable_resumes_persisted_cycle_and_never_forces_a_history_review(runtime):
    client, _ = runtime
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("tenant-a-store", "Loja A", "2026-11-13", "2026-11-16", automatico=True, now=NOW)
    queue.claim("worker", now=NOW)
    queue.complete_day(job["id"], "worker", "2026-11-13", {"vendas": 4}, now=NOW + 1)
    queue.pause_all(now=NOW + 2)

    response = client.post("/api/vendas/sync/automatico", json=request(automatico=True, forcar_resync=True).model_dump())

    assert response.status_code == 200
    assert response.json()["job_id"] == job["id"]
    assert response.json()["automatico"] is True
    assert response.json()["automacao"]["lojas"][0]["habilitado"] is True
    resumed = queue.get_job(job["id"])
    assert resumed["status"] == "queued"
    assert resumed["forcar_resync"] is False
    assert resumed["dias_concluidos"] == ["2026-11-13"]
    assert len(queue.list_jobs()) == 1
    # Reopening the queue must retain the setting after a process restart.
    assert SyncQueue("tenant-a").summary()["lojas"][0]["habilitado"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_settings_cannot_change_a_store_from_another_tenant(runtime, enabled):
    client, _ = runtime
    queue = SyncQueue("tenant-a")
    job = queue.enqueue("tenant-a-store", "Loja A", "2026-11-13", "2026-11-16", automatico=True, now=NOW)
    response = client.post("/api/vendas/sync/automatico", headers={"X-Test-Tenant": "tenant-b"},
                           json=request(automatico=enabled).model_dump())
    assert response.status_code == 409
    assert queue.get_job(job["id"])["status"] == "queued"
    assert queue.summary()["lojas"][0]["habilitado"] is True


def test_settings_resolve_the_store_before_disabling_anything(runtime):
    client, _ = runtime
    body = request(automatico=False).model_dump()
    body["loja"] = "Loja removida"
    response = client.post("/api/vendas/sync/automatico", json=body)
    assert response.status_code == 404
