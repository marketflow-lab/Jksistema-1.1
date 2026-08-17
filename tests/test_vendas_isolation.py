from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

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


def _create_module(tmp_path: Path):
    logger = logging.getLogger("test-vendas-isolation")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    return create_vendas_module(
        VendasModuleDependencies(
            get_tenant_id=_tenant_dependency,
            paths=paths,
            logger=logger,
            legacy=LegacyBlingVendasAdapter(),
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
        "routes": 430,
        "pairs": 428,
        "duplicates": [],
        "openapi": "5a653c9f14bab6b1b8aaebeb41c934f2ec1c657a1b82668b10e4f2c20df236bc",
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
    assert module.state.max_active_sync == 2


def test_app_paths_migrates_legacy_file_into_tenant(tmp_path: Path) -> None:
    logger = logging.getLogger("test-vendas-paths")
    paths = AppPaths.create(base_dir=tmp_path, info_dir=tmp_path / "info", logger=logger)
    legacy = tmp_path / "produtos_compilado.csv"
    legacy.write_text("sku,estoque\nA,1\n", encoding="utf-8")
    destination = Path(paths.migrate_legacy_file("000002", legacy.name, legacy))
    assert destination == tmp_path / "info" / "000002" / legacy.name
    assert destination.read_text(encoding="utf-8") == "sku,estoque\nA,1\n"


def test_sync_preserves_already_running_two_account_limit_and_cancel(monkeypatch) -> None:
    stores = (SYNC_ACTIVE, SYNC_CANCEL_FLAGS, SYNC_DAY_CONTEXT, SYNC_LOGS, SYNC_META, SYNC_PROGRESS)
    snapshots = [dict(store) for store in stores]

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(vendas_sync_engine.threading, "Thread", FakeThread)
    try:
        for store in stores:
            store.clear()
        req_a = VendasSyncRequest(loja="Loja A", data_inicio="2026-07-01", data_fim="2026-07-01")
        req_b = VendasSyncRequest(loja="Loja B", data_inicio="2026-07-01", data_fim="2026-07-01")
        req_c = VendasSyncRequest(loja="Loja C", data_inicio="2026-07-01", data_fim="2026-07-01")

        first = vendas_sync_engine.sincronizar_vendas(req_a, "tenant-sync")
        duplicate = vendas_sync_engine.sincronizar_vendas(req_a, "tenant-sync")
        second = vendas_sync_engine.sincronizar_vendas(req_b, "tenant-sync")
        limited = vendas_sync_engine.sincronizar_vendas(req_c, "tenant-sync")
        cancelled = vendas_progress.cancelar_sincronizacao_vendas("tenant-sync")

        assert first["started"] is True
        assert duplicate == {
            "started": False,
            "already_running": True,
            "job_id": first["job_id"],
            "message": "Esta loja ja esta sincronizando.",
        }
        assert second["started"] is True and second["active_count"] == 2
        assert limited["already_running"] is True
        assert limited["limit_reached"] is True
        assert limited["active_count"] == limited["limit"] == 2
        assert cancelled["success"] is True
        assert SYNC_CANCEL_FLAGS["tenant-sync"] is True
    finally:
        for store, snapshot in zip(stores, snapshots):
            store.clear()
            store.update(snapshot)
