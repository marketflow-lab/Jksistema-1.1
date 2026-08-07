from __future__ import annotations

import ast
import hashlib
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.services import ia_tools_vendas
from backend.services.codex.assistant.catalog_data import CODEX_DATA_TOOLS
from backend.services.sales_tools import contracts, registry, runtime


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "services" / "sales_tools"
FACADE = ROOT / "backend" / "services" / "ia_tools_vendas.py"
LAYERS = {
    "contracts": 0,
    "runtime": 0,
    "charts": 1,
    "parsing": 1,
    "repository": 1,
    "context": 2,
    "inventory": 2,
    "metrics": 2,
    "monthly": 2,
    "returns": 2,
    "virtual_stores": 2,
    "api": 3,
    "registry": 4,
    "__init__": 5,
}


CONTRACT_HASHES = {
    "exports": "24a9b67f6f89e4d01adefdb540ef3b8e523225dd5d2d1a8f41750a3567726ace",
    "signatures": "403419f9fc990fa086bbd01a6a5166407869adca8261cf16b59374ee6fd63ea7",
    "tools": "fad506bcc7e7c3beb6ecc325b4cdb80775bfd465e35460f9e722429d1a782a1e",
    "chart_contracts": "9012e7cd9b5f8f992f390b756c60d756e2e4a8b7a8fe909be6b47b195d39e6e0",
}


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot() -> dict[str, object]:
    exports = list(ia_tools_vendas.__all__)
    private_exports = [name for name in exports if name.startswith("_")]
    signatures = {
        name: str(inspect.signature(getattr(ia_tools_vendas, name)))
        for name in private_exports
    }
    keys = (
        "id",
        "module",
        "executor",
        "external",
        "read_only",
        "required_permissions",
        "zero_is_authoritative",
        "status",
        "source_role",
        "aggregation_policy",
    )
    tools = sorted(
        [
            {key: tool.get(key) for key in keys}
            for tool in CODEX_DATA_TOOLS
            if tool.get("executor") in private_exports
        ],
        key=lambda item: item["id"],
    )
    chart_contracts = {
        "stale": ia_tools_vendas._ia_stale_stock_chart_data({"itens": []}),
        "stockout": ia_tools_vendas._ia_stockout_chart_data({"previsoes": []}),
    }
    return {
        "exports": exports,
        "signatures": signatures,
        "tools": tools,
        "chart_contracts": chart_contracts,
    }


def test_sales_tools_contract_snapshot() -> None:
    snapshot = _snapshot()
    assert len(snapshot["exports"]) == 45
    assert len(snapshot["signatures"]) == 44
    assert len(snapshot["tools"]) == 12
    assert {
        name: _digest(value) for name, value in snapshot.items()
    } == CONTRACT_HASHES


def _local_dependencies(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        names = [node.module.split(".", 1)[0]] if node.module else [alias.name for alias in node.names]
        dependencies.update(name for name in names if (PACKAGE / f"{name}.py").exists())
    return dependencies


def test_sales_tools_architecture_budgets_and_static_imports() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        assert "bind_runtime_globals" not in source, path.name
        assert "globals().update" not in source, path.name
        assert "PEER_EXPORTS" not in source, path.name
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"
            if isinstance(node, ast.ImportFrom):
                assert not any(alias.name == "*" for alias in node.names), path.name
                assert node.module != "backend.services.ia_tools_vendas", path.name


def test_sales_tools_dependency_direction_has_no_cycles() -> None:
    graph = {path.stem: _local_dependencies(path) for path in PACKAGE.glob("*.py")}
    for owner, dependencies in graph.items():
        for dependency in dependencies:
            assert LAYERS[dependency] <= LAYERS[owner], f"{owner} -> {dependency}"
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        assert module not in visiting, f"ciclo detectado em {module}"
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_sales_tools_runtime_is_allowlisted_idempotent_and_thread_safe() -> None:
    original = runtime.current()
    sentinel = lambda *_args, **_kwargs: "sentinel"
    peers = {"_listar_bancos_vendas_tenant": sentinel, "not_allowlisted": object()}
    try:
        first = runtime.configure(peers=peers)
        second = runtime.configure(peers=peers)
        assert first is second
        assert first.databases.list_sales_databases is sentinel
        assert not hasattr(first, "not_allowlisted")
        with ThreadPoolExecutor(max_workers=8) as executor:
            configured = list(executor.map(lambda _: runtime.configure(peers=peers), range(16)))
        assert all(item is first for item in configured)
        assert runtime.configure(first) is first
    finally:
        runtime.configure(original)


def test_sales_tool_registry_and_consumers_use_named_apis() -> None:
    assert tuple(registry.TOOL_EXECUTORS) == contracts.TOOL_EXECUTORS
    assert all(callable(value) for value in registry.TOOL_EXECUTORS.values())
    for path in (ROOT / "backend").rglob("*.py"):
        if path == FACADE or path.name == "ia_tools.py" or path.parent == PACKAGE:
            continue
        source = path.read_text(encoding="utf-8-sig")
        assert "from backend.services.ia_tools_vendas import _" not in source, path
        assert "ia_tools_vendas._ia_" not in source, path
