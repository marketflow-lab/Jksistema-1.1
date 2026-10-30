"""Saved-first summaries: synthetic sources, bounded jobs and publication fences."""

from concurrent.futures import Future
from contextvars import ContextVar
import copy
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from backend.modules.vendas import dependencies, performance, persistent_cache as disk
from backend.modules.vendas import summary_loading as loading


@pytest.fixture
def context(tmp_path, monkeypatch):
    info = tmp_path / "info"
    root = info / "tenant-a"
    root.mkdir(parents=True)
    source = root / "source.txt"
    source.write_text("10")
    monkeypatch.setattr(dependencies, "_dependencies", SimpleNamespace(
        paths=SimpleNamespace(info_dir=info, tenant_path=lambda client_id: str(info / client_id)),
        logger=logging.getLogger("summary-fixture")))
    monkeypatch.setattr(loading, "_sources", lambda arguments: performance.vendas_sources_signature([source]))
    monkeypatch.setattr(loading, "_JOBS", loading.OrderedDict())
    monkeypatch.setattr(disk, "_DISABLED", set())
    calls = []

    def calculate(**arguments):
        calls.append(arguments)
        value = int(source.read_text())
        return {"success": True, "rows": [{"sku": "SYNTHETIC", "itens": value}],
                "totals": {"itens": value}, "meta": {}}

    arguments = {"client_id": "tenant-a", "loja": "Loja A"}
    return SimpleNamespace(info=info, root=root, source=source, calls=calls,
                           calculate=calculate, arguments=arguments)


def read(context, *, saved_only=False, calculate=None, **filters):
    return loading.read_summary(calculate or context.calculate,
                                {**context.arguments, **filters}, saved_only=saved_only)


def settled(context, *, calculate=None, **filters):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result, status = read(context, calculate=calculate, **filters)
        if result["meta"]["leitura"]["estado"] in {"atualizado", "erro"}:
            return result, status
        time.sleep(.01)
    pytest.fail("Synthetic summary did not settle")


def test_saved_only_never_computes_schedules_or_reads_sources(context, monkeypatch):
    monkeypatch.setattr(loading, "_sources", lambda _args: pytest.fail("Saved-only read requested sources"))
    result, status = read(context, saved_only=True)
    assert status == 202 and result["rows"] == []
    assert result["meta"]["leitura"] == {"estado": "processando", "possui_salvo": False,
        "atualizando": False, "revisao": "", "salvo_em": None}
    assert not context.calls and not loading._JOBS


def test_background_is_immediate_deduplicated_and_restores_after_memory_reset(context, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(**kwargs):
        entered.set()
        assert release.wait(5)
        return context.calculate(**kwargs)

    start = time.monotonic()
    first, status = read(context, calculate=slow)
    assert time.monotonic() - start < 1
    assert status == 202 and first["meta"]["leitura"]["atualizando"]
    assert entered.wait(2)
    try:
        for _ in range(5):
            assert read(context, calculate=slow)[1] == 202
        assert len(loading._JOBS) == 1
    finally:
        release.set()
    result, status = settled(context, calculate=slow)
    assert status == 200 and result["totals"]["itens"] == 10
    assert result["meta"]["leitura"]["estado"] == "atualizado"
    assert result["meta"]["leitura"]["salvo_em"] > 0
    assert len(context.calls) == 1
    monkeypatch.setattr(loading, "_JOBS", loading.OrderedDict())
    result, status = read(context, saved_only=True,
                          calculate=lambda **kwargs: pytest.fail("Restart calculated saved data"))
    assert status == 200 and result["totals"]["itens"] == 10
    assert result["meta"]["leitura"]["estado"] == "salvo"
    assert "_saved_summary" not in result["meta"]
    assert read(context)[0]["meta"]["leitura"]["estado"] == "atualizado"
    assert len(context.calls) == 1


def test_old_saved_data_is_visible_while_new_sources_refresh(context):
    original, _ = settled(context)
    context.source.write_text("20")
    entered, release = threading.Event(), threading.Event()

    def slow(**kwargs):
        entered.set()
        assert release.wait(5)
        return context.calculate(**kwargs)

    try:
        result, status = read(context, calculate=slow)
        assert status == 200 and result["totals"]["itens"] == 10
        assert result["meta"]["leitura"]["atualizando"]
        assert entered.wait(2)
        assert read(context, saved_only=True)[0]["totals"]["itens"] == 10
    finally:
        release.set()
    updated, _ = settled(context, calculate=slow)
    assert updated["totals"]["itens"] == 20
    assert updated["meta"]["leitura"]["revisao"] != original["meta"]["leitura"]["revisao"]


@pytest.mark.parametrize("failure", ["exception", "partial", "catalog", "unproven-warning"])
def test_failure_preserves_last_valid_snapshot(context, failure):
    settled(context)
    snapshots = {p.name: p.read_bytes() for p in (context.root / disk.CACHE_DIRECTORY).glob("*.json")}
    context.source.write_text("20")

    def fail(**arguments):
        if failure == "exception":
            raise PermissionError("PRIVATE SYNTHETIC EXCEPTION")
        result = context.calculate(**arguments)
        if failure == "partial":
            performance.mark_vendas_read_incomplete()
        else:
            result["meta"]["composicao"] = {"incomplete": True,
                "warnings": [{"code": "catalog_unavailable" if failure == "catalog" else "missing_components"}]}
            if failure == "catalog":
                result["meta"]["composicao"]["read_complete"] = False
        return result

    result, status = settled(context, calculate=fail)
    assert status == 200 and result["totals"]["itens"] == 10
    assert result["meta"]["leitura"]["estado"] == "erro"
    assert "PRIVATE" not in json.dumps(result)
    assert snapshots == {p.name: p.read_bytes() for p in (context.root / disk.CACHE_DIRECTORY).glob("*.json")}


def test_semantic_warnings_with_proven_complete_read_are_saved(context):
    def warned(**arguments):
        result = context.calculate(**arguments)
        result["meta"]["composicao"] = {"incomplete": True, "read_complete": True,
            "warnings": [{"code": "missing_components", "sku": "SYNTHETIC"}]}
        return result

    result, _ = settled(context, calculate=warned)
    assert result["meta"]["leitura"]["estado"] == "atualizado"
    restored, _ = read(context, saved_only=True)
    assert restored["meta"]["composicao"] == result["meta"]["composicao"]


@pytest.mark.parametrize("mutation", ["source", "tenant", "global"])
def test_change_during_read_prevents_publication_and_resurrection(context, mutation):
    entered, release = threading.Event(), threading.Event()

    def slow(**kwargs):
        result = context.calculate(**kwargs)
        entered.set()
        assert release.wait(5)
        return result

    read(context, calculate=slow)
    assert entered.wait(2)
    try:
        if mutation == "source":
            context.source.write_text("20")
        else:
            assert disk.invalidate(context.info if mutation == "global" else context.root,
                                   global_scope=mutation == "global")
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while any(job.state == "processando" for job in loading._JOBS.values()) and time.monotonic() < deadline:
        time.sleep(.01)
    result, status = read(context, saved_only=True)
    assert status == 202 and not result["meta"]["leitura"]["possui_salvo"]
    assert not list((context.root / disk.CACHE_DIRECTORY).glob("*.json"))


@pytest.mark.parametrize("filter_name,value", [("client_id", "tenant-b"), ("loja", "Loja B"),
    ("sku", "B"), ("data_inicio", "2026-01-01"), ("data_fim", "2026-02-01"),
    ("unidade_negocio", "Full"), ("incluir_ebazar", True)])
def test_all_scopes_are_isolated(context, filter_name, value):
    settled(context)
    result, status = read(context, saved_only=True, **{filter_name: value})
    assert status == 202 and not result["meta"]["leitura"]["possui_salvo"]
    assert len(context.calls) == 1


def test_source_provider_failure_returns_saved_without_new_calculation(context, monkeypatch):
    settled(context)
    monkeypatch.setattr(loading, "_sources", lambda _args: (_ for _ in ()).throw(PermissionError()))
    result, status = read(context)
    assert status == 200 and result["totals"]["itens"] == 10
    assert result["meta"]["leitura"]["estado"] == "erro"
    assert len(context.calls) == 1


def test_bounded_submission_allows_only_two_workers_and_sixteen_waiters(context, monkeypatch):
    submitted = []

    class PausedExecutor:
        def submit(self, *args):
            submitted.append(args)
            return Future()

    monkeypatch.setattr(loading, "_POOL", PausedExecutor())
    monkeypatch.setattr(loading, "_SLOTS", threading.BoundedSemaphore(loading.MAX_WORKERS + loading.MAX_PENDING))
    for index in range(loading.MAX_WORKERS + loading.MAX_PENDING):
        result, _ = read(context, sku=str(index))
        assert result["meta"]["leitura"]["atualizando"]
    result, status = read(context, sku="overflow")
    assert status == 202 and not result["meta"]["leitura"]["atualizando"]
    assert len(submitted) == 18
    read(context, sku="0")
    assert len(submitted) == 18


def test_background_copies_context_and_does_not_mutate_request_arguments(context):
    scope = ContextVar("synthetic-request-scope", default="missing")
    seen = []

    def calculate(**arguments):
        seen.append(scope.get())
        return context.calculate(**arguments)

    token = scope.set("request-a")
    try:
        result, _ = settled(context, calculate=calculate)
    finally:
        scope.reset(token)
    assert result["meta"]["leitura"]["estado"] == "atualizado"
    assert seen == ["request-a"]
    assert context.arguments == {"client_id": "tenant-a", "loja": "Loja A"}


def test_router_opt_in_keeps_legacy_default_and_skips_photo_enrichment(monkeypatch):
    from fastapi import Request
    from backend.modules.vendas.router import create_vendas_router
    from backend.modules.vendas import photos

    original = {"success": True, "rows": [], "totals": {}, "meta": {}}
    calls = []
    service = SimpleNamespace(resumo_vendas=lambda **kwargs: calls.append(kwargs) or copy.deepcopy(original))
    router = create_vendas_router(service, SimpleNamespace(), SimpleNamespace(get_tenant_id=lambda: "tenant-a"))
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/vendas/resumo")
    request = Request({"type": "http", "headers": []})
    assert endpoint(request, client_id="tenant-a") == original
    assert len(calls) == 1
    monkeypatch.setattr(loading, "read_summary", lambda *args, **kwargs: ({**original,
        "meta": {"leitura": {"estado": "processando", "possui_salvo": False}}}, 202))
    monkeypatch.setattr(photos, "enrich_summary_photos", lambda *args: pytest.fail("Saved read enriched photos"))
    request = Request({"type": "http", "headers": [(b"x-jk-vendas-fotos", b"1")]})
    assert endpoint(request, somente_salvos=True, client_id="tenant-a").status_code == 202
    assert endpoint(request, cache_mode="stale", client_id="tenant-a").status_code == 202
    assert len(calls) == 1


def test_independent_process_restores_saved_only_without_calculator(context):
    settled(context)
    child = r'''
import json, logging, pathlib, sys
from types import SimpleNamespace
from backend.modules.vendas import dependencies, summary_loading
info=pathlib.Path(sys.argv[1])
dependencies._dependencies=SimpleNamespace(paths=SimpleNamespace(info_dir=info,
    tenant_path=lambda client_id:str(info/client_id)),logger=logging.getLogger("fixture"))
def forbidden(**arguments):raise AssertionError("Saved-only child calculated")
result,status=summary_loading.read_summary(forbidden,{"client_id":"tenant-a","loja":"Loja A"},saved_only=True)
print(json.dumps({"status":status,"items":result["totals"].get("itens"),"state":result["meta"]["leitura"]["estado"]}))
'''
    completed = subprocess.run([sys.executable, "-B", "-c", child, str(context.info)],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},
        capture_output=True,text=True,encoding="utf-8",timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {"status":200,"items":10,"state":"salvo"}


def test_field_proof_exception_cannot_enter_catalog_or_summary_cache(context, monkeypatch):
    from contextlib import nullcontext
    from backend.modules.vendas import compositions
    from backend.services import cadastro_compatibilidade as compatibility
    calls = []
    monkeypatch.setattr(compositions, "composition_source_paths", lambda _client: [context.source])
    monkeypatch.setattr(compatibility, "_capturar_cadastro_para_leitura", lambda _client: ([],[],{},{}))
    monkeypatch.setattr(compatibility, "_cadastro_fotos_contexto_leitura", lambda _photos: nullcontext())

    def denied(*args):
        calls.append(1)
        raise PermissionError("Synthetic field proof read failure")

    monkeypatch.setattr(compositions, "_store_field_proofs", denied)
    for _ in range(2):
        catalog = compositions.load_composition_catalog("tenant-a")
        assert catalog.metadata["read_complete"] is False
        assert catalog.metadata["incomplete"] is False
    assert calls == [1,1]
    result = {"success":True,"rows":[],"totals":{},"meta":{"composicao":catalog.metadata}}
    assert not disk.valid_payload("resumo",result)


@pytest.mark.parametrize("name", ["lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json"])
def test_changed_store_identity_cannot_resurrect_saved_sales(context, name):
    settled(context)
    (context.root / name).write_text('{"synthetic_identity":"replacement-store"}')
    result, status = read(context, saved_only=True)
    assert status == 202 and not result["meta"]["leitura"]["possui_salvo"]
    assert len(context.calls) == 1


def test_identity_change_during_job_cannot_publish_old_account(context):
    entered, release = threading.Event(), threading.Event()

    def slow(**kwargs):
        result = context.calculate(**kwargs)
        entered.set()
        assert release.wait(5)
        return result

    read(context, calculate=slow)
    assert entered.wait(2)
    try:
        (context.root / "lojas_config.json").write_text('{"synthetic_identity":"replacement-store"}')
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while any(job.state == "processando" for job in loading._JOBS.values()) and time.monotonic() < deadline:
        time.sleep(.01)
    assert read(context, saved_only=True)[1] == 202
    assert not list((context.root / disk.CACHE_DIRECTORY).glob("*.json"))


def test_completed_job_load_failure_preserves_initial_saved_payload(context, monkeypatch):
    settled(context)
    context.source.write_text("20")
    normalized = loading._arguments(context.arguments)
    generation = loading._generation(normalized,create=False)
    key = loading._key(generation,normalized)
    original_load = loading._load
    old = original_load(generation,key)
    revision = loading._revision(generation,loading._sources(normalized))
    monkeypatch.setattr(loading,"_schedule",lambda *args:loading._Job(state="atualizado"))
    responses = iter([old,None])
    monkeypatch.setattr(loading,"_load",lambda *args:next(responses))
    result,status = read(context)
    assert status == 200 and result["totals"]["itens"] == 10
    assert result["meta"]["leitura"]["estado"] == "erro"


def test_identity_change_during_saved_read_is_rechecked_before_response(context, monkeypatch):
    settled(context)
    original = loading._load

    def changing(generation,key):
        saved = original(generation,key)
        (context.root / "lojas_config.json").write_text('{"synthetic_identity":"replacement-store"}')
        return saved

    monkeypatch.setattr(loading,"_load",changing)
    result,status = read(context,saved_only=True)
    assert status == 202 and not result["meta"]["leitura"]["possui_salvo"]


@pytest.mark.parametrize("loss", ["removed", "corrupt"])
def test_completed_job_recovers_when_its_saved_snapshot_is_lost(context, loss):
    settled(context)
    path = next((context.root / disk.CACHE_DIRECTORY).glob("*.json"))
    if loss == "removed":
        path.unlink()
    else:
        path.write_text('{"truncated":')
    assert read(context,saved_only=True)[1] == 202
    assert len(context.calls) == 1
    result,status = settled(context)
    assert status == 200 and result["meta"]["leitura"]["estado"] == "atualizado"
    assert result["totals"]["itens"] == 10
    assert len(context.calls) == 2
    for _ in range(3):
        assert read(context)[0]["meta"]["leitura"]["estado"] == "atualizado"
    assert len(context.calls) == 2
