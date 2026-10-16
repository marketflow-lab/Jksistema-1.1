"""Full-result cache contracts, with no application/runtime or production data."""

import asyncio
import logging
import threading
import time
from dataclasses import replace

import pytest
from fastapi import HTTPException

from backend.services import medias_compras_cache as cache


def test_attempt_diagnostics_preserve_retry_and_own_commit_without_private_data(caplog):
    service = cache.ResultadoCache()
    private = "PRIVATE-TENANT-STORE-SKU-TOKEN"
    path = "C:/" + private + "/lojas_config.json"
    state = cache.Fontes(("day", ((path, 1),)), (private,), private)
    calls = []
    def probe():
        return state
    def load(before):
        nonlocal state
        calls.append(before)
        if len(calls) == 1:
            state = replace(state, assinatura=("day", ((path, 2),)))
        else:
            state = replace(state, alvo="PRIVATE-COMMIT")
            cache.registrar_commit_lista(state.alvo)
        return {"payload": private}
    with caplog.at_level(logging.INFO, logger="jk_sistema"):
        value, accepted = service._calculate(probe, load, time.perf_counter(), state)
    assert value == {"payload": private} and accepted is state
    assert len(calls) == 2
    messages = [record.getMessage() for record in caplog.records]
    fences = [message for message in messages if "[MEDIAS_FENCE]" in message]
    attempts = [message for message in messages if "[MEDIAS_ATTEMPT]" in message]
    assert len(fences) == len(attempts) == 2
    assert 'categorias={"lojas_config":1}' in fences[0] and "consistente=0" in fences[0]
    assert "consistente=1" in fences[1] and "commit_proprio=1" in fences[1] and "alvo_alterado=0" in fences[1]
    assert any("tentativas=2" in message for message in messages)
    assert all(private not in message and "PRIVATE-COMMIT" not in message and "C:/" not in message for message in messages)


def test_singleflight_survives_disconnect_and_legacy_keeps_schema():
    async def scenario():
        service = cache.ResultadoCache()
        entered, release = threading.Event(), threading.Event()
        calls = []
        state = cache.Fontes(("v1",), ("policy1",), "target1")
        def probe():
            return state
        def load(_state):
            calls.append(1)
            entered.set()
            release.wait(3)
            return {"success": True, "itens": [1]}
        args = ("tenant", ("root", "tenant", "visao", 6), probe, load)
        first = await service.consultar(*args, stale=True)
        assert first.status_code == 202
        assert await asyncio.to_thread(entered.wait, 5)
        waiter = asyncio.create_task(service.consultar(*args, stale=False))
        await asyncio.sleep(.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        second = await service.consultar(*args, stale=True)
        assert second.status_code == 202 and calls == [1]
        survivor = asyncio.create_task(service.consultar(*args, stale=False))
        await asyncio.sleep(.01)
        release.set()
        result = await survivor
        assert result == {"success": True, "itens": [1]}
        warm = await service.consultar(*args, stale=True)
        assert warm["_cache"]["estado"] == "ready"
        assert warm["_cache"]["stale"] is False
        warm["itens"].append(2)
        assert (await service.consultar(*args, stale=True))["itens"] == [1]
        assert calls == [1]
    asyncio.run(scenario())


def test_source_changes_once_retry_twice_fail_and_backfill_receipt():
    async def scenario():
        service = cache.ResultadoCache()
        state = cache.Fontes((0,), ("policy",), "old")
        calls = []
        def probe():
            return state
        def load(_state):
            nonlocal state
            calls.append(1)
            if len(calls) == 1:
                state = replace(state, assinatura=(1,))
            return {"value": len(calls)}
        assert await service.consultar("tenant", ("retry",), probe, load, stale=False) == {"value": 2}
        assert len(calls) == 2
        calls.clear()
        def unstable(_state):
            nonlocal state
            calls.append(1)
            state = replace(state, assinatura=(len(calls) + 5,))
            return {"value": len(calls)}
        with pytest.raises(HTTPException) as failure:
            await service.consultar("tenant", ("unstable",), probe, unstable, stale=False)
        assert failure.value.status_code == 409 and len(calls) == 2
        def commit(_state):
            nonlocal state
            state = replace(state, alvo="committed")
            cache.registrar_commit_lista("committed")
            return {"value": "committed"}
        result = await service.consultar("tenant", ("commit",), probe, commit, stale=False)
        assert result == {"value": "committed"}
        warm = await service.consultar("tenant", ("commit",), probe, commit, stale=True)
        assert warm["_cache"]["estado"] == "ready"
    asyncio.run(scenario())


def test_hard_invalidation_never_returns_old_edit_or_policy():
    async def scenario():
        service = cache.ResultadoCache()
        state = cache.Fontes((1,), ("policy",), "target1")
        release = threading.Event()
        def probe():
            return state
        def load(_state):
            return {"value": _state.alvo}
        key = ("scope",)
        await service.consultar("tenant", key, probe, load, stale=False)
        state = replace(state, alvo="target2")
        def slow(current):
            release.wait(3)
            return load(current)
        response = await service.consultar("tenant", key, probe, slow, stale=True)
        assert response.status_code == 202
        release.set()
        await service.consultar("tenant", key, probe, slow, stale=False)
        state = replace(state, autoridade=("new-policy",))
        response = await service.consultar("tenant", key, probe, slow, stale=True)
        assert response.status_code == 202
        await service.consultar("tenant", key, probe, slow, stale=False)
    asyncio.run(scenario())


def test_retry_budget_includes_transaction_conflict():
    from backend.services.medias_compras_common import _ListaPedidoConflict
    async def scenario():
        service = cache.ResultadoCache()
        state = cache.Fontes((0,), ("policy",))
        calls = []
        def probe():
            return state
        def load(_state):
            nonlocal state
            calls.append(1)
            if len(calls) == 1:
                raise _ListaPedidoConflict()
            state = replace(state, assinatura=(1,))
            return {"success": True}
        with pytest.raises(HTTPException) as error:
            await service.consultar("tenant", ("conflict",), probe, load, stale=False)
        assert error.value.status_code == 409
        assert calls == [1, 1]
        assert not service._results
    asyncio.run(scenario())


def test_stale_ttl_error_warning_and_retry_do_not_claim_freshness():
    async def scenario():
        now = [0]
        service = cache.ResultadoCache(clock=lambda: now[0])
        state = cache.Fontes((0,), ("policy",))
        def probe():
            return state
        def good(_state):
            return {"itens": ["old"]}
        args = ("a", ("root", "query"), probe)
        await service.consultar(*args, good, stale=False)
        now[0] = 61
        entered, release = threading.Event(), threading.Event()
        def failed(_state):
            entered.set()
            release.wait(3)
            raise RuntimeError("PRIVATE /path/token must not be returned")
        old = await service.consultar(*args, failed, stale=True)
        assert old["itens"] == ["old"]
        assert old["_cache"]["estado"] == "refreshing" and old["_cache"]["stale"]
        task = next(iter(service._flights.values()))[1]
        assert await asyncio.to_thread(entered.wait, 3)
        release.set()
        with pytest.raises(RuntimeError):
            await asyncio.shield(task)
        warning = await service.consultar(*args, failed, stale=True)
        assert warning["itens"] == ["old"]
        assert warning["_cache"]["estado"] == "error"
        assert warning["_cache"]["gerado_em"] == old["_cache"]["gerado_em"]
        assert "PRIVATE" not in str(warning)
        now[0] = 901
        cold = await service.consultar(*args, good, stale=True)
        assert cold.status_code == 202
        await asyncio.shield(next(iter(service._flights.values()))[1])
    asyncio.run(scenario())


def test_lru_and_bytes_bounded_per_tenant_and_globally():
    service = cache.ResultadoCache()
    service.TENANT_ENTRIES = 2
    service.MAX_ENTRIES = 3
    state = cache.Fontes((0,), ("p",))
    for tenant, query in (("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2)):
        service._store((tenant, query), {"value": query}, state)
    assert set(service._results) == {("a", 3), ("b", 1), ("b", 2)}
    service.TENANT_BYTES = 20
    assert service._store(("a", "large"), {"value": "x" * 30}, state) is False
    assert ("a", "large") not in service._results


def test_oversize_legacy_still_returns_original_payload_opt_in_fails_honestly():
    async def scenario():
        service = cache.ResultadoCache()
        service.TENANT_BYTES = 1024
        state = cache.Fontes((0,), ("p",))
        result = await service.consultar("a", (1,), lambda: state, lambda _s: {"value": "original"}, stale=False)
        assert result == {"value": "original"} and not service._results
        with pytest.raises(HTTPException) as error:
            await service.consultar("a", (1,), lambda: state, lambda _s: {}, stale=True)
        assert error.value.status_code == 503
        assert error.value.detail["code"] == "result_too_large"
    asyncio.run(scenario())


def test_capacity_bounds_queue_and_keeps_two_heavy_workers():
    async def scenario():
        service = cache.ResultadoCache()
        release = threading.Event()
        state = cache.Fontes((0,), ("policy",))
        active, peak = [0], [0]
        guard = threading.Lock()
        def load(_state):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            release.wait(3)
            with guard:
                active[0] -= 1
            return {"ok": True}
        for tenant in ("a", "b"):
            for index in range(4):
                assert (await service.consultar(tenant, (index,), lambda: state, load, stale=True)).status_code == 202
        queued = await service.consultar("a", (5,), lambda: state, load, stale=True)
        assert queued.status_code == 202
        with pytest.raises(HTTPException) as error:
            await service.consultar("a", (5,), lambda: state, load, stale=False)
        assert error.value.status_code == 503
        assert (await service.consultar("c", (1,), lambda: state, load, stale=True)).status_code == 202
        assert len(service._flights) == 8
        owners = [value[1] for value in service._flights.values()]
        release.set()
        await asyncio.gather(*owners)
        assert peak[0] <= 2
        assert not service._flights
    asyncio.run(scenario())


def test_authority_is_rechecked_before_even_a_fresh_hit():
    async def scenario():
        service = cache.ResultadoCache()
        state = cache.Fontes((0,), ("p",))
        await service.consultar("a", ("root",), lambda: state, lambda _s: {"secret": "a"}, stale=False)
        def denied():
            raise HTTPException(403, "denied")
        with pytest.raises(HTTPException) as error:
            await service.consultar("a", ("root",), denied, lambda _s: {}, stale=True)
        assert error.value.status_code == 403
        other = await service.consultar("b", ("root",), lambda: state, lambda _s: {"secret": "b"}, stale=False)
        assert other == {"secret": "b"}
        separate = await service.consultar("a", ("other-root",), lambda: state, lambda _s: {"secret": "other"}, stale=False)
        assert separate == {"secret": "other"}
    asyncio.run(scenario())
