from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core import AppPaths
from backend.modules.vendas import (
    LegacyBlingVendasAdapter,
    VendasModuleDependencies,
    create_vendas_module,
    install_default_vendas_module,
)
from backend.modules.vendas import progress as vendas_progress
from backend.modules.vendas import sync_engine as vendas_sync_engine
from backend.modules.vendas.state import SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS
from backend.modules.vendas.sync_config import MAX_CONCURRENT_ACCOUNTS
from backend.schemas import VendasSyncRequest


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "modules" / "vendas"

EXPECTED_ROUTES = {
    ("GET", "/api/vendas/resumo", "resumo_vendas"),
    ("GET", "/api/vendas", "listar_vendas"),
    ("POST", "/api/vendas/limpar-tudo", "limpar_todos_bancos_vendas"),
    ("GET", "/api/vendas/todas", "listar_vendas_todas"),
    ("GET", "/api/vendas/grafico", "grafico_vendas"),
    ("GET", "/api/vendas/skus-sem-venda", "skus_sem_venda"),
    ("GET", "/api/vendas/limites", "limites_vendas"),
    ("GET", "/api/vendas/relatorios/pareto-80", "relatorio_pareto_80"),
    ("GET", "/api/vendas/relatorios/pareto-80/exportar", "exportar_relatorio_pareto_80"),
    ("GET", "/api/vendas/relatorios/vendas-estoque-devolucoes", "relatorio_geral_skus"),
    ("GET", "/api/vendas/relatorios/vendas-estoque-devolucoes/exportar", "exportar_relatorio_geral_skus"),
    ("POST", "/api/vendas/sync/cancel", "cancelar_sincronizacao_vendas"),
    ("GET", "/api/vendas/sync/progress", "progresso_sincronizacao_vendas"),
    ("POST", "/api/vendas/sync", "sincronizar_vendas"),
    ("GET", "/api/notas-entrada", "listar_notas_entrada"),
    ("GET", "/api/notas-entrada/itens", "listar_itens_devolucoes"),
    ("GET", "/api/notas-entrada/sku/{sku}", "listar_notas_entrada_por_sku"),
    ("GET", "/api/unidades-negocios", "listar_unidades_negocios"),
    ("POST", "/api/unidades-negocios/atualizar", "atualizar_unidade_negocio"),
    ("POST", "/api/unidades-negocios/mapeamento", "salvar_mapeamento_unidades"),
}


async def _tenant_dependency(*_args, **_kwargs) -> str:
    return "tenant-test"


def _create_module(tmp_path: Path, *, max_active_sync: int = MAX_CONCURRENT_ACCOUNTS):
    logger = logging.getLogger("test-vendas-isolation")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    return create_vendas_module(
        VendasModuleDependencies(
            get_tenant_id=_tenant_dependency,
            paths=paths,
            logger=logger,
            legacy=LegacyBlingVendasAdapter(),
            max_active_sync=max_active_sync,
        )
    )


def test_domain_import_does_not_load_backend_api() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    code = "import sys; import backend.modules.vendas; assert 'backend_api' not in sys.modules"
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_composed_app_preserves_global_and_openapi_contracts() -> None:
    code = r'''
import collections, hashlib, json
import backend_api
from backend.modules.vendas.sync_config import MAX_CONCURRENT_ACCOUNTS
assert backend_api.SYNC_MAX_ACTIVE_VENDAS == MAX_CONCURRENT_ACCOUNTS
assert backend_api._vendas_domain.state.max_active_sync == MAX_CONCURRENT_ACCOUNTS
routes=[]
for route in backend_api.app.routes:
    effective_route_contexts=getattr(route, "effective_route_contexts", None)
    if callable(effective_route_contexts):
        routes.extend(effective_route_contexts())
    else:
        routes.append(route)
pairs=[]
for route in routes:
    for method in set(getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}:
        pairs.append((method, route.path))
duplicates=[item for item,count in collections.Counter(pairs).items() if count > 1]
paths={path:item for path,item in backend_api.app.openapi().get("paths",{}).items() if path.startswith("/api/vendas") or path.startswith("/api/notas-entrada") or path.startswith("/api/unidades-negocios")}
raw=json.dumps(paths,sort_keys=True,ensure_ascii=False,separators=(",",":"))
print(json.dumps({"routes":len(routes),"pairs":len(pairs),"duplicates":duplicates,"openapi":hashlib.sha256(raw.encode()).hexdigest()}))
'''
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        # Renovacao acrescentou conferir-promocao (2a5528cb) e corrigir-divergencias
        # (a1a19e1d): duas rotas/pares sobre 525/523. Vendas mantem seu hash e 20 rotas.
        "routes": 527,
        "pairs": 525,
        "duplicates": [],
        "openapi": "4592ad7b7431ffb069c65eca1874b0f26010549fab41b2836d740e8cc037d0f1",
    }


def test_domain_has_no_dynamic_runtime_bridge_or_wildcard_imports() -> None:
    forbidden = ("runtime_bridge", "sys.modules", "globals().update")
    for path in DOMAIN.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        assert not any(token in source for token in forbidden), path.name
        tree = ast.parse(source)
        assert not any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path.name
        if path.name != "router.py":
            assert "from fastapi" not in source and "import fastapi" not in source, path.name


def test_module_factory_preserves_the_twenty_route_contracts(tmp_path: Path) -> None:
    module = _create_module(tmp_path)
    actual = set()
    for route in module.router.routes:
        for method in set(route.methods or set()) - {"HEAD", "OPTIONS"}:
            actual.add((method, route.path, route.name))
    assert actual == EXPECTED_ROUTES


def test_legacy_facades_delegate_to_the_explicit_default_module(tmp_path: Path) -> None:
    module = install_default_vendas_module(_create_module(tmp_path))
    from backend.services import vendas

    assert vendas.limites_vendas(client_id="tenant-test") == {"inicio": None, "fim": None}
    assert vendas.progresso_sincronizacao_vendas("tenant-test")["success"] is True
    assert module.state.max_active_sync == MAX_CONCURRENT_ACCOUNTS


@pytest.mark.parametrize("max_active_sync", [2, 3, 5])
def test_module_factory_rejects_capacity_that_differs_from_shared_limit(tmp_path: Path, max_active_sync: int) -> None:
    with pytest.raises(ValueError, match=f"permanecer em {MAX_CONCURRENT_ACCOUNTS}"):
        _create_module(tmp_path, max_active_sync=max_active_sync)


def test_app_paths_never_moves_global_legacy_file_into_non_default_tenant(
    tmp_path: Path,
) -> None:
    logger = logging.getLogger("test-vendas-paths")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    legacy = tmp_path / "produtos_compilado.csv"
    legacy.write_text("sku,estoque\nA,1\n", encoding="utf-8")
    destination = Path(paths.migrate_legacy_file("000002", legacy.name, legacy))
    assert destination == tmp_path / "info" / "000002" / legacy.name
    assert not destination.exists()
    assert legacy.read_text(encoding="utf-8") == "sku,estoque\nA,1\n"


def test_sync_queue_keeps_capacity_limit_and_cancellation_contract(tmp_path, monkeypatch) -> None:
    # Work that exceeds capacity is now durably queued instead of discarded.
    from backend.modules.vendas import sync_automation
    from backend.modules.vendas.sync_queue import SyncQueue
    _create_module(tmp_path)

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.alive = False
        def start(self):
            self.alive = True
        def is_alive(self):
            return self.alive

    stores = (SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS)
    snapshots = [dict(store) for store in stores]
    monkeypatch.setattr(sync_automation.threading, "Thread", FakeThread)
    monkeypatch.setattr(sync_automation, "start", lambda: None)
    monkeypatch.setattr(sync_automation, "_RUNNING", {})
    monkeypatch.setattr(sync_automation, "_AUTH", {})
    monkeypatch.setattr(vendas_sync_engine, "_resolve_store",
                        lambda req, _: req.model_copy(update={"store_id": req.loja}))
    try:
        for store in stores:
            store.clear()
        reqs = [VendasSyncRequest(loja=name, data_inicio="2026-07-01", data_fim="2026-07-01")
                for name in ("A", "B", "C", "D", "E")]
        first = vendas_sync_engine.sincronizar_vendas(reqs[0], "tenant-sync")
        duplicate = vendas_sync_engine.sincronizar_vendas(reqs[0], "tenant-sync")
        second = vendas_sync_engine.sincronizar_vendas(reqs[1], "tenant-sync")
        third = vendas_sync_engine.sincronizar_vendas(reqs[2], "tenant-sync")
        fourth = vendas_sync_engine.sincronizar_vendas(reqs[3], "tenant-sync")
        queued = vendas_sync_engine.sincronizar_vendas(reqs[4], "tenant-sync")
        assert all(result["started"] for result in (first, second, third, fourth))
        assert duplicate["job_id"] == first["job_id"]
        assert queued["queued"] and queued["accepted"]
        assert len(SYNC_ACTIVE["tenant-sync"]) == 4
        cancelled = vendas_progress.cancelar_sincronizacao_vendas("tenant-sync")
        assert cancelled["success"] is True
        assert all(job["status"] == "paused" for job in SyncQueue("tenant-sync").list_jobs())
    finally:
        for store, snapshot in zip(stores, snapshots):
            store.clear()
            store.update(snapshot)
