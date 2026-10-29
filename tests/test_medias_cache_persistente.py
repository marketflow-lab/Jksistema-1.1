"""Durable Medias snapshots exercised with isolated synthetic tenants."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

import pytest
from fastapi import HTTPException

from backend.services import medias_compras_cache as cache
from backend.services import medias_compras_cache_disco as disk


def payload(label="old", months=6):
    return {"success": True, "periodo_meses": months, "loja": "Synthetic store",
            "store_id": "store-a", "colunas_meses": [{"key": "2026-09", "label": "09/2026"}],
            "itens": [{"sku": "001", "titulo_anuncio": label, "media_mensal": 2}],
            "resumo": {"total_skus": 1}}


def fontes():
    return cache.Fontes(("sources-v1",), ("policy-a",), politica_fotos=("photos-a",))


KEY = ("tenant-root", "visao", 6, "store-a")


def args(source, key=KEY, tenant="client-a"):
    return tenant, key, lambda: source


async def finish(service):
    return await asyncio.gather(*(entry[1] for entry in list(service._flights.values())), return_exceptions=True)


def test_persistent_view_survives_memory_ttl_without_recalculation(tmp_path):
    async def scenario():
        now = [0]
        service = cache.ResultadoCache(clock=lambda: now[0])
        source = fontes()
        calls = []
        def load(_source):
            calls.append(1)
            return payload()
        await service.consultar(*args(source), load, stale=False, persistir_em=tmp_path)
        now[0] = 24 * 3600
        result = await service.consultar(*args(source), load, stale=True, persistir_em=tmp_path)
        assert result["itens"][0]["sku"] == "001"
        assert result["_cache"]["estado"] == "ready" and not result["_cache"]["stale"]
        assert not service._flights and calls == [1]
        assert len(list((tmp_path / disk.DIRECTORY).glob("*.json"))) == 1
    asyncio.run(scenario())


def test_restart_serves_last_snapshot_while_changed_sources_refresh_and_legacy_waits(tmp_path):
    async def scenario():
        source = fontes()
        original = cache.ResultadoCache()
        await original.consultar(*args(source), lambda _s: payload(), stale=False, persistir_em=tmp_path)
        original_time = original._results[("client-a", *KEY)].gerado_em
        restarted = cache.ResultadoCache()
        changed = replace(source, assinatura=("sources-v2",))
        entered, release = threading.Event(), threading.Event()
        def slow(_source):
            entered.set()
            assert release.wait(5)
            return payload("new")
        old = await restarted.consultar(*args(changed), slow, stale=True, persistir_em=tmp_path)
        assert old["itens"][0]["titulo_anuncio"] == "old"
        assert old["_cache"]["stale"] and old["_cache"]["atualizando"]
        assert old["_cache"]["gerado_em"] == original_time
        assert await asyncio.to_thread(entered.wait, 3)
        legacy = asyncio.create_task(restarted.consultar(*args(changed), slow, stale=False, persistir_em=tmp_path))
        await asyncio.sleep(.02)
        assert not legacy.done()
        release.set()
        current = await legacy
        assert "_cache" not in current and current["itens"][0]["titulo_anuncio"] == "new"
        ready = await restarted.consultar(*args(changed), slow, stale=True, persistir_em=tmp_path)
        assert ready["_cache"]["estado"] == "ready"
        assert ready["itens"][0]["titulo_anuncio"] == "new"
    asyncio.run(scenario())


def test_failed_refresh_preserves_last_valid_disk_and_visible_result(tmp_path):
    async def scenario():
        source = fontes()
        first = cache.ResultadoCache()
        await first.consultar(*args(source), lambda _s: payload(), stale=False, persistir_em=tmp_path)
        path = next((tmp_path / disk.DIRECTORY).glob("*.json"))
        before = path.read_bytes()
        changed = replace(source, assinatura=("sources-v2",))
        def fail(_source):
            raise RuntimeError("PRIVATE-ERROR-MUST-NOT-BE-EXPOSED")
        restarted = cache.ResultadoCache()
        stale = await restarted.consultar(*args(changed), fail, stale=True, persistir_em=tmp_path)
        assert stale["itens"][0]["titulo_anuncio"] == "old"
        failures = await finish(restarted)
        assert any(isinstance(item, RuntimeError) for item in failures)
        warned = await restarted.consultar(*args(changed), fail, stale=True, persistir_em=tmp_path)
        assert warned["itens"] == stale["itens"] and warned["_cache"]["estado"] == "error"
        assert warned["_cache"]["stale"] and "PRIVATE" not in str(warned)
        assert path.read_bytes() == before
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["authority", "photos", "pending", "target"])
def test_incompatible_policy_or_pending_publication_never_restores_snapshot(tmp_path, change):
    async def scenario():
        source = fontes()
        first = cache.ResultadoCache()
        await first.consultar(*args(source), lambda _s: payload(), stale=False, persistir_em=tmp_path)
        changes = {"authority": {"autoridade": ("policy-b",)}, "photos": {"politica_fotos": ("photos-b",)},
                   "pending": {"pendente": True}, "target": {"alvo": "changed-target"}}
        changed = replace(source, **changes[change])
        restarted = cache.ResultadoCache()
        response = await restarted.consultar(*args(changed), lambda _s: payload("new"), stale=True, persistir_em=tmp_path)
        assert response.status_code == 202
        assert json.loads(response.body)["dados"] is None
        await finish(restarted)
    asyncio.run(scenario())


def test_authentication_precedes_disk_lookup_and_is_rechecked_after_disk_read(tmp_path, monkeypatch):
    async def scenario():
        source = fontes()
        first = cache.ResultadoCache()
        await first.consultar(*args(source), lambda _s: payload(), stale=False, persistir_em=tmp_path)
        original_read = disk.ler
        reads = []
        def read(*values):
            reads.append(1)
            return original_read(*values)
        monkeypatch.setattr(disk, "ler", read)
        def denied():
            raise HTTPException(403, "denied")
        with pytest.raises(HTTPException) as failure:
            await cache.ResultadoCache().consultar("client-a", KEY, denied, lambda _s: payload(), stale=True, persistir_em=tmp_path)
        assert failure.value.status_code == 403 and reads == []
        calls = []
        def revoked():
            calls.append(1)
            if len(calls) > 1:
                raise HTTPException(403, "denied")
            return source
        with pytest.raises(HTTPException) as failure:
            await cache.ResultadoCache().consultar("client-a", KEY, revoked, lambda _s: payload(), stale=True, persistir_em=tmp_path)
        assert failure.value.status_code == 403 and reads == [1]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["tenant", "root", "store", "months"])
def test_snapshot_identity_isolates_tenant_root_store_and_period(tmp_path, change):
    async def scenario():
        source = fontes()
        first = cache.ResultadoCache()
        await first.consultar(*args(source), lambda _s: payload(), stale=False, persistir_em=tmp_path)
        tenant, key, root = "client-a", KEY, tmp_path
        if change == "tenant":
            tenant = "client-b"
        elif change == "root":
            root = tmp_path / "other"
            root.mkdir()
        elif change == "store":
            key = (*KEY[:-1], "store-b")
        else:
            key = (*KEY[:2], 3, KEY[-1])
        service = cache.ResultadoCache()
        response = await service.consultar(*args(source, key, tenant), lambda _s: payload("new"), stale=True, persistir_em=root)
        assert response.status_code == 202 and json.loads(response.body)["dados"] is None
        await finish(service)
    asyncio.run(scenario())


def record(service, key, source):
    assert service._store(key, payload(), source, persistente=True)
    return service._snapshot(service._results[key])


def test_checksum_schema_calculation_revision_and_private_metadata(tmp_path, monkeypatch):
    source = cache.Fontes(("PRIVATE-SOURCE-PATH",), ("PRIVATE-POLICY",), "PRIVATE-TARGET", politica_fotos=("PRIVATE-PHOTO",))
    key = ("PRIVATE-TENANT", "PRIVATE-ROOT", "visao", 6, "PRIVATE-STORE")
    saved = record(cache.ResultadoCache(), key, source)
    assert disk.gravar(tmp_path, key, saved)
    path = next((tmp_path / disk.DIRECTORY).glob("*.json"))
    original = path.read_bytes()
    assert b"PRIVATE" not in original and str(tmp_path).encode() not in original
    assert disk.ler(tmp_path, key) == saved
    envelope = json.loads(original)
    envelope["record"]["payload"]["itens"][0]["media_mensal"] = 999
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert disk.ler(tmp_path, key) is None
    path.write_bytes(original)
    monkeypatch.setattr(disk, "SCHEMA", 2)
    assert disk.ler(tmp_path, key) is None
    monkeypatch.setattr(disk, "SCHEMA", 1)
    monkeypatch.setattr(disk, "calculation_revision", lambda: "new-calculation")
    assert disk.ler(tmp_path, key) is None


def test_atomic_write_failure_and_oversize_preserve_previous_snapshot(tmp_path, monkeypatch):
    key = ("tenant", *KEY)
    saved = record(cache.ResultadoCache(), key, fontes())
    assert disk.gravar(tmp_path, key, saved)
    path = next((tmp_path / disk.DIRECTORY).glob("*.json"))
    original = path.read_bytes()
    descriptor, orphan = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"interrupted incomplete publication")
    unrelated = path.parent / ".write-keep.txt"
    unrelated.write_text("not a cache temporary", encoding="utf-8")
    newer = {**saved, "payload": payload("new")}
    def fail(*_args):
        raise OSError("synthetic failure")
    monkeypatch.setattr(disk.os, "replace", fail)
    assert disk.gravar(tmp_path, key, newer) is False
    assert path.read_bytes() == original and disk.ler(tmp_path, key) == saved
    assert not Path(orphan).exists()
    assert not list((tmp_path / disk.DIRECTORY).glob(".write-*.tmp"))
    assert unrelated.read_text(encoding="utf-8") == "not a cache temporary"
    monkeypatch.setattr(disk, "MAX_ENTRY_BYTES", 10)
    assert disk.gravar(tmp_path, key, newer) is False
    assert path.read_bytes() == original


def test_retention_bounds_entries_bytes_and_age(tmp_path, monkeypatch):
    monkeypatch.setattr(disk, "MAX_ENTRIES", 2)
    saved = record(cache.ResultadoCache(), ("tenant", *KEY), fontes())
    for index in range(3):
        assert disk.gravar(tmp_path, ("tenant", "visao", index), saved)
    paths = list((tmp_path / disk.DIRECTORY).glob("*.json"))
    assert len(paths) == 2
    size = max(path.stat().st_size for path in paths)
    monkeypatch.setattr(disk, "MAX_BYTES", size + 10)
    assert disk.gravar(tmp_path, ("tenant", "visao", 4), saved)
    assert len(list((tmp_path / disk.DIRECTORY).glob("*.json"))) == 1
    ancient = {**saved, "gerado_em": (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()}
    assert disk.gravar(tmp_path, ("tenant", "visao", 5), ancient) is False


def test_durable_result_is_reused_in_another_python_process(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import asyncio, json, sys
from backend.services.medias_compras_cache import ResultadoCache, Fontes
source=Fontes(('same-sources',), ('same-policy',), politica_fotos=('same-photos',))
payload={'success':True, 'periodo_meses':6, 'itens':[{'sku':'001','media_mensal':2}], 'colunas_meses':[], 'resumo':{}}
def load(_source):
    if sys.argv[2] == 'read':
        raise AssertionError('Restart unexpectedly recalculated the view')
    return payload
async def main():
    value=await ResultadoCache().consultar('tenant', ('root','visao',6,'store-a'), lambda:source, load, stale=sys.argv[2]=='read', persistir_em=sys.argv[1])
    print(json.dumps({'sku':value['itens'][0]['sku'], 'state':value.get('_cache',{}).get('estado')}))
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    outputs = []
    for mode in ("write", "read"):
        child = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), mode], cwd=root,
                               env=env, capture_output=True, text=True, timeout=30)
        assert child.returncode == 0, child.stderr
        outputs.append(json.loads(child.stdout))
    assert outputs == [{"sku": "001", "state": None}, {"sku": "001", "state": "ready"}]
