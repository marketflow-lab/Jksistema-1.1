"""Admission, deadline and cache recovery contracts; no provider traffic."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from fastapi import HTTPException

from backend.services import perguntas_loading_cache as cache
from backend.services.perguntas_loading_transport import read_budget


def test_secondary_work_reserves_capacity_for_selected_question():
    release = threading.Event()
    lock = threading.Lock()
    active = [0]
    maximum = [0]
    started = threading.Event()
    def count():
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            if active[0] == 2:
                started.set()
        try:
            assert release.wait(3)
            return {"perguntas": 1}
        finally:
            with lock:
                active[0] -= 1
    tenant = "scheduler-reservation"
    with ThreadPoolExecutor(max_workers=5) as clients:
        counts = [clients.submit(cache.read, (tenant, str(i), "u", "s", "MLB", "count"), count, ttl=0) for i in range(4)]
        try:
            assert started.wait(1)
            selected = clients.submit(cache.read, (tenant, "selected", "u", "s", "MLB", "detail"), lambda: {"selected": True}, ttl=0)
            assert selected.result(timeout=0.5)["selected"] is True
            assert maximum[0] <= 2
        finally:
            release.set()
        for job in counts:
            job.result(2)


def test_queued_expired_work_never_reaches_provider():
    from backend.services.perguntas_loading_scheduler import ReadScheduler
    scheduler = ReadScheduler(workers=2, capacity=8)
    release = threading.Event()
    both = threading.Event()
    lock = threading.Lock()
    running = [0]
    def hold():
        with lock:
            running[0] += 1
            if running[0] == 2:
                both.set()
        assert release.wait(3)
    futures = [scheduler.submit(("t", str(i), "u", "s", "MLB", "detail"), hold) for i in range(2)]
    called = []
    try:
        assert both.wait(1)
        with read_budget(0.08):
            expired = scheduler.submit(("t", "queued", "u", "s", "MLB", "detail"), lambda: called.append(True))
        with pytest.raises(HTTPException) as error:
            expired.result(0.5)
        assert error.value.status_code == 504
        assert not called
    finally:
        release.set()
        for job in futures:
            job.result(2)
        scheduler.shutdown()


def test_resource_denial_preserves_other_cached_data():
    base = ("scheduler-resource", "A", "u", "s", "MLB")
    other = (*base, "items")
    denied = (*base, "detail", "1")
    cache.read(other, lambda: {"items": []}, ttl=900)
    cache.read(denied, lambda: {"question": {}}, ttl=60)
    def fail():
        raise HTTPException(403, "Recurso indisponível.", headers={"X-JK-Error-Scope": "resource"})
    with pytest.raises(HTTPException):
        cache.read(denied, fail, ttl=60, force=True)
    assert other in cache._ENTRIES
    assert denied not in cache._ENTRIES


def test_network_timeout_is_reported_as_timeout_not_gateway_error():
    def timeout():
        raise requests.Timeout("synthetic")
    with pytest.raises(HTTPException) as error:
        cache.read(("scheduler-timeout", "A", "u", "s", "MLB", "detail"), timeout, ttl=60)
    assert error.value.status_code == 504


def test_stale_metadata_cannot_claim_components_ready():
    key = ("scheduler-stale", "A", "u", "s", "MLB", "detail")
    payload = {"components": {"question": {"state": "ready"}, "history": {"state": "ready"}},
               "item_states": {"MLB1": {"state": "ready"}}}
    cache.read(key, lambda: payload, ttl=60)
    entry = cache._ENTRIES[key]
    timestamp = entry.consulted_at
    result = cache._metadata(entry, key, stale=True, source="memory_cache")
    assert result["components"]["history"]["state"] == "stale"
    assert result["item_states"]["MLB1"]["state"] == "stale"
    assert result["consultado_em"] == timestamp
    assert entry.value["components"]["history"]["state"] == "ready"


def test_global_secondary_limit_leaves_eight_interactive_slots():
    from backend.services.perguntas_loading_scheduler import ReadScheduler
    scheduler = ReadScheduler()
    release, eight = threading.Event(), threading.Event()
    lock = threading.Lock()
    started = []
    def count():
        with lock:
            started.append(True)
            if len(started) == 8:
                eight.set()
        assert release.wait(3)
    jobs = [scheduler.submit((str(i), "A", "u", "s", "MLB", "count"), count) for i in range(16)]
    try:
        assert eight.wait(1)
        assert scheduler.submit(("interactive", "A", "u", "s", "MLB", "detail"), lambda: 1).result(.5) == 1
        assert len(started) == 8
    finally:
        release.set()
        for job in jobs:
            job.result(2)
        scheduler.shutdown()


def test_complement_retains_slot_until_it_finishes():
    from backend.services.perguntas_loading_scheduler import ReadScheduler, retain_current_admission
    scheduler = ReadScheduler(workers=1, capacity=2)
    releases = []
    try:
        def parent():
            releases.append(retain_current_admission())
            return "partial"
        key = ("t", "A", "u", "s", "MLB", "detail")
        assert scheduler.submit(key, parent).result(1) == "partial"
        called = threading.Event()
        next_job = scheduler.submit(key, called.set)
        assert not called.wait(.05)
        with pytest.raises(HTTPException) as error:
            scheduler.submit(key, lambda: None)
        assert error.value.status_code == 503
        releases.pop()()
        next_job.result(1)
        assert called.is_set()
    finally:
        for release in releases:
            release()
        scheduler.shutdown()


@pytest.mark.parametrize("same_tenant,limit,total", [(True, 4, 6), (False, 16, 20)])
def test_interactive_admission_respects_tenant_and_global_limits(same_tenant, limit, total):
    from backend.services.perguntas_loading_scheduler import ReadScheduler
    scheduler = ReadScheduler()
    release, saturated = threading.Event(), threading.Event()
    lock = threading.Lock()
    running = [0]
    maximum = [0]
    def hold():
        with lock:
            running[0] += 1
            maximum[0] = max(maximum[0], running[0])
            if running[0] == limit:
                saturated.set()
        try:
            assert release.wait(3)
        finally:
            with lock:
                running[0] -= 1
    jobs = [scheduler.submit(("same" if same_tenant else str(i), str(i), "u", "s", "MLB", "detail"), hold)
            for i in range(total)]
    try:
        assert saturated.wait(1)
        with scheduler._condition:
            assert scheduler._active == limit
            assert len(scheduler._pending) == total - limit
    finally:
        release.set()
        for job in jobs:
            job.result(2)
        scheduler.shutdown()
    assert maximum[0] == limit


def test_stale_fallback_preserves_retry_after_for_component_recovery():
    key = ("scheduler-retry-after", "A", "u", "s", "MLB", "detail")
    cache.read(key, lambda: {"components": {"history": {"state": "ready"}}}, ttl=60)
    def limited():
        raise HTTPException(429, "Limite temporario.", headers={"Retry-After": "12"})
    result = cache.read(key, limited, ttl=60, force=True)
    assert result["stale"] is True
    assert result["components"]["history"]["retry_after"] == 12


def test_component_retry_finishing_after_original_ttl_is_marked_stale():
    key = ("scheduler-reused-expiry", "A", "u", "s", "MLB", "detail")
    original_time = time.monotonic() - 61
    result = cache.read(key, lambda: {
        "components": {"question": {"state": "ready"}, "history": {"state": "ready"}},
        "_cache_origin": (original_time, int(time.time() * 1000) - 61000),
    }, ttl=60, force=True)
    assert result["stale"] is True
    assert result["components"]["question"]["state"] == "stale"
    assert cache._ENTRIES[key].monotonic_at == original_time
