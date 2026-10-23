from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from backend.modules.vendas import repository, sync_engine, sync_period
from backend.modules.vendas.errors import VendasDomainError
from backend.modules.vendas.state import SYNC_THREAD_CONTEXT
from backend.modules.vendas.sync_queue import SyncQueue
from backend.schemas import VendasSyncRequest
from backend.services import bling_vendas, vendas_collection_checkpoint as checkpoint
from test_vendas_collection_checkpoint import DAY, Response, sales_api
from test_vendas_sync_resume import isolated, _bind


@pytest.fixture
def pipeline(isolated, monkeypatch, sales_api):
    root = isolated / "info" / "tenant-a"
    queue = SyncQueue("tenant-a")
    db = root / "vendas_historico_a.db"
    cfg = {"access_token": "test", "id": "app", "secret": "test", "oauth_connection_id": "connection-a"}
    store = {"store_id": "store-a", "nome": "A", "integracoes": {"bling": cfg}}
    monkeypatch.setattr(sync_engine, "buscar_loja", lambda *a, **k: store)
    monkeypatch.setattr(sync_period, "buscar_loja", lambda *a, **k: store)
    db_path = lambda client, name: str(root / f"vendas_historico_{name.lower()}.db")
    monkeypatch.setattr(sync_engine, "_get_vendas_db_path", db_path)
    monkeypatch.setattr(sync_period, "_get_vendas_db_path", db_path)
    monkeypatch.setattr(sync_engine, "_listar_bancos_vendas_tenant", lambda *a: list(map(str, root.glob("vendas_historico*.db"))))
    monkeypatch.setattr(sync_period, "_carregar_mapeamento_unidades", lambda: {})
    monkeypatch.setattr(sync_period, "_carregar_mapeamento_lojas_virtuais_cliente", lambda *a: {})
    monkeypatch.setattr(sync_period, "_sync_log", lambda *a: None)
    monkeypatch.setattr(sync_period, "invalidate_vendas_cache", lambda *a: None)
    monkeypatch.setattr(sync_period, "legacy_store_conflicts", lambda *a, **k: set())
    seen_days = []

    def sales(token, start, end, name, *args):
        seen_days.append(start)
        return bling_vendas._bling_listar_vendas(token, start, end, name) if start == DAY else ([], 200)

    def execute(client, name, config, call, **kwargs):
        data, status = call(config["access_token"])
        return data, status, config

    monkeypatch.setattr(sync_period, "_bling_executar_com_refresh", execute)
    monkeypatch.setattr(sync_period, "_bling_listar_vendas", sales)
    monkeypatch.setattr(sync_period, "_bling_listar_vendas_fallback_nf_saida", lambda *a: ([], 200))
    monkeypatch.setattr(sync_period, "_bling_listar_naturezas", lambda *a: ({}, 200))
    monkeypatch.setattr(sync_period, "_bling_listar_notas_entrada", lambda *a: ([], [], 200))

    def run(end=DAY, force=False):
        request = VendasSyncRequest(loja=store["nome"], store_id="store-a", data_inicio=DAY,
                                   data_fim=end, forcar_resync=force)
        job = queue.enqueue("store-a", store["nome"], DAY, end, forcar_resync=force)
        claimed = queue.claim("worker")
        assert claimed and claimed["id"] == job["id"]
        _bind(claimed, queue, "worker")
        sync_engine._prepare_store_collection(request, "tenant-a", queue, job["id"], "worker")
        try:
            return asyncio.run(sync_engine._sincronizar_vendas_impl(request, "tenant-a"))
        except Exception:
            if queue.owns(job["id"], "worker"):
                queue.fail_job(job["id"], "worker", "Teste interrompido.")
            raise

    yield {"queue": queue, "db": db, "store": store, "cfg": cfg, "run": run, "api": sales_api, "days": seen_days}
    for name in ("collection_validate", "collection_identity"):
        if hasattr(SYNC_THREAD_CONTEXT, name):
            delattr(SYNC_THREAD_CONTEXT, name)


def test_changed_period_resumes_confirmed_document_and_skips_closed_days(pipeline):
    p = pipeline
    with pytest.raises(Exception):
        p["run"]()
    assert not p["db"].exists(), "a failed document cannot commit a partial day"
    assert p["api"]["details"] == [10, 20]
    p["queue"].pause_all()
    p["api"]["fail"] = False
    p["run"]("2026-07-19")
    assert p["api"]["details"] == [10, 20, 20]
    assert p["api"]["pages"] == [1, 2], "complete manifest must survive a changed period"
    with sqlite3.connect(p["db"]) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(quantidade), SUM(valor) FROM vendas").fetchone() == (4, 4, 40)
    calls = list(p["days"])
    result = p["run"]("2026-07-20")
    assert p["days"] == calls + ["2026-07-20"]
    assert result["dias_aproveitados"] == 2
    p["run"]("2026-07-20")
    assert p["days"] == calls + ["2026-07-20"], "repeating a confirmed period must not invoke its collectors"
    p["run"](force=True)
    assert p["api"]["details"] == [10, 20, 20, 10, 20]
    with sqlite3.connect(p["db"]) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(valor) FROM vendas").fetchone() == (4, 40)


def test_crash_after_commercial_commit_reuses_documents_without_duplicate_rows(pipeline, monkeypatch):
    p = pipeline
    p["api"]["fail"] = False
    original = SyncQueue.complete_day
    fail_once = [True]

    def acknowledge(queue, *args, **kwargs):
        if fail_once:
            fail_once.pop()
            raise RuntimeError("crash after commercial commit")
        return original(queue, *args, **kwargs)

    monkeypatch.setattr(SyncQueue, "complete_day", acknowledge)
    with pytest.raises(RuntimeError, match="commercial commit"):
        p["run"]()
    assert p["db"].exists()
    p["queue"].pause_all()
    p["run"]("2026-07-19")
    assert p["api"]["pages"] == [1, 2]
    assert p["api"]["details"] == [10, 20]
    with sqlite3.connect(p["db"]) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(valor) FROM vendas").fetchone() == (4, 40)


def test_missing_database_and_changed_connection_do_not_inherit_coverage(pipeline):
    p = pipeline
    p["api"]["fail"] = False
    p["run"]()
    p["db"].unlink()
    p["run"]()
    assert p["api"]["details"] == [10, 20, 10, 20]
    p["cfg"]["oauth_connection_id"] = "connection-b"
    p["run"]()
    assert p["api"]["details"] == [10, 20, 10, 20, 10, 20]


def test_connection_change_before_commit_rolls_back_the_entire_day(pipeline, monkeypatch):
    p = pipeline
    p["api"]["fail"] = False
    original = sync_period._persistir_periodo_atomico

    def switch(*args, **kwargs):
        p["cfg"]["oauth_connection_id"] = "connection-b"
        return original(*args, **kwargs)

    monkeypatch.setattr(sync_period, "_persistir_periodo_atomico", switch)
    with pytest.raises(VendasDomainError, match="conexão Bling mudou"):
        p["run"]()
    assert not p["db"].exists()
    assert all(not job["dias_concluidos"] for job in p["queue"].list_jobs())


def test_clear_history_invalidates_coverage_and_stops_old_lease(pipeline, monkeypatch):
    p = pipeline
    p["api"]["fail"] = False
    p["run"]()
    waiting = p["queue"].enqueue("store-a", "A", DAY, "2026-07-19", automatico=True)
    p["queue"].claim("obsolete")
    monkeypatch.setattr(repository, "invalidate_vendas_cache", lambda *a: None)
    result = repository.limpar_todos_bancos_vendas("tenant-a")
    assert result["success"] and not p["db"].exists()
    assert all(not job["dias_concluidos"] for job in p["queue"].list_jobs())
    assert not p["queue"].owns(waiting["id"], "obsolete")
    assert p["queue"].summary()["habilitadas"] == 1
    p["run"]()
    assert p["api"]["details"] == [10, 20, 10, 20]


def test_store_rename_preserves_confirmations_and_missing_old_database_only_reopens_its_days(pipeline):
    p = pipeline
    p["api"]["fail"] = False
    p["run"]()
    p["store"]["nome"] = "Renamed"
    p["run"]()
    assert p["days"] == [DAY]
    p["run"]("2026-07-19")
    assert p["days"] == [DAY, "2026-07-19"]
    assert p["db"].with_name("vendas_historico_renamed.db").exists()
    p["db"].unlink()
    p["run"]("2026-07-19")
    assert p["days"] == [DAY, "2026-07-19", DAY]


@pytest.mark.parametrize("change", ["remove", "replace_token", "replace_app"])
def test_legacy_connection_snapshot_change_blocks_commit(pipeline, monkeypatch, change):
    p = pipeline
    p["api"]["fail"] = False
    p["cfg"].pop("oauth_connection_id")
    original = sync_period._persistir_periodo_atomico

    def switch(*args, **kwargs):
        if change == "remove":
            p["store"]["integracoes"].clear()
        elif change == "replace_token":
            p["cfg"]["access_token"] = "another-connection"
        else:
            p["cfg"]["id"] = "another-app"
        return original(*args, **kwargs)

    monkeypatch.setattr(sync_period, "_persistir_periodo_atomico", switch)
    with pytest.raises(VendasDomainError, match="conexão Bling mudou"):
        p["run"]()
    assert not p["db"].exists()
    assert all(not job["dias_concluidos"] for job in p["queue"].list_jobs())


def test_token_rotation_in_same_identified_connection_allows_commit(pipeline, monkeypatch):
    p = pipeline
    p["api"]["fail"] = False
    original = sync_period._persistir_periodo_atomico

    def rotate(*args, **kwargs):
        p["cfg"]["access_token"] = "renewed-token"
        p["cfg"]["refresh_token"] = "renewed-refresh-token"
        return original(*args, **kwargs)

    monkeypatch.setattr(sync_period, "_persistir_periodo_atomico", rotate)
    p["run"]()
    assert p["db"].exists()


@pytest.mark.parametrize("failure", ["return_detail", "after_commit"])
def test_returns_resume_without_losing_items_or_duplicating_commercial_rows(pipeline, monkeypatch, failure):
    p = pipeline
    p["api"]["fail"] = False
    state = {"fail": True, "pages": [], "details": []}
    sales_request = bling_vendas._bling_get_with_adaptive_limit

    def request(url, **kwargs):
        if url != "https://api.bling.com.br/Api/v3/nfe":
            return sales_request(url, **kwargs)
        params = kwargs["params"]
        assert params["tipo"] == 0
        state["pages"].append(params["pagina"])
        return Response(200, [
            {"id": identity, "numero": identity, "dataEmissao": DAY,
             "naturezaOperacao": {"id": 1, "descricao": "Devolução de mercadoria"}}
            for identity in (30, 40)
        ] if params["pagina"] == 1 else [])

    def detail(token, identity, **kwargs):
        identity = int(identity)
        state["details"].append(identity)
        if failure == "return_detail" and state["fail"] and identity == 40:
            return None, 503
        return {"id": identity, "numero": identity, "dataEmissao": DAY,
                "naturezaOperacao": {"id": 1, "descricao": "Devolução de mercadoria"},
                "itens": [{"codigo": "SKU", "quantidade": 2, "valor": 5},
                          {"codigo": "SKU", "quantidade": 3, "valor": 5}]}, 200

    def returns(token, start, end, *args):
        return bling_vendas._bling_listar_notas_entrada(token, start, end, *args) if start == DAY else ([], [], 200)

    acknowledge = SyncQueue.complete_day

    def complete_day(queue, *args, **kwargs):
        if failure == "after_commit" and state["fail"]:
            raise RuntimeError("crash after commercial commit")
        return acknowledge(queue, *args, **kwargs)

    monkeypatch.setattr(bling_vendas, "_bling_get_with_adaptive_limit", request)
    monkeypatch.setattr(bling_vendas, "_bling_obter_detalhes_nf", detail)
    monkeypatch.setattr(sync_period, "_bling_listar_notas_entrada", returns)
    monkeypatch.setattr(sync_period, "_classificar_unidade_virtual_devolucao",
                        bling_vendas._classificar_unidade_virtual_devolucao)
    monkeypatch.setattr(SyncQueue, "complete_day", complete_day)
    with pytest.raises(Exception):
        p["run"]()
    assert state["details"] == [30, 40]
    assert all(not job["dias_concluidos"] for job in p["queue"].list_jobs())
    assert p["db"].exists() == (failure == "after_commit")
    p["queue"].pause_all()
    state["fail"] = False
    p["run"]("2026-07-19")
    assert state["pages"] == [1, 2]
    assert state["details"] == ([30, 40, 40] if failure == "return_detail" else [30, 40])
    assert p["api"]["details"] == [10, 20]
    with sqlite3.connect(p["db"]) as conn:
        assert conn.execute("SELECT COUNT(*), SUM(valor) FROM vendas").fetchone() == (4, 40)
        assert conn.execute("SELECT COUNT(*) FROM notas_entrada").fetchone() == (2,)
        assert conn.execute("SELECT COUNT(*), SUM(quantidade), SUM(valor_total), SUM(devolucao) FROM notas_entrada_itens").fetchone() == (4, 10, 50, 4)
