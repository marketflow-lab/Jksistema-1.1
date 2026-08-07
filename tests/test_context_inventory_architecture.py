from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "services" / "context_inventory"
FACADE = ROOT / "backend" / "services" / "context_hub_inventory.py"

LAYERS = {
    "contracts": 0,
    "normalization": 1,
    "security": 2,
    "entities": 3,
    "python_scanner": 4,
    "sku_content": 4,
    "markdown": 4,
    "manifest": 4,
    "route_scanner": 5,
    "sku_scanner": 5,
    "legacy": 5,
    "markdown_maps": 5,
    "surface_scanner": 6,
    "builder": 7,
    "api": 8,
    "__init__": 9,
}


def _python_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _module_name(path: Path) -> str:
    return f"backend.services.context_inventory.{path.stem}"


def _internal_dependencies(path: Path) -> set[str]:
    package_modules = {_module_name(item) for item in _python_files()}
    dependencies: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    current = _module_name(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module:
                candidate = f"backend.services.context_inventory.{node.module}"
                if candidate in package_modules:
                    dependencies.add(candidate)
            elif node.module in package_modules:
                dependencies.add(str(node.module))
        elif isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names if alias.name in package_modules)
    dependencies.discard(current)
    return dependencies


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"context_inventory dependency cycle at {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_context_inventory_line_and_function_budgets() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                length = node.end_lineno - node.lineno + 1
                assert length <= 120, f"{path}:{node.lineno}:{node.name}:{length}"


def test_context_inventory_has_closed_directional_import_graph() -> None:
    graph = {_module_name(path): _internal_dependencies(path) for path in _python_files()}
    _assert_acyclic(graph)
    for module, dependencies in graph.items():
        owner = module.rsplit(".", 1)[-1]
        for dependency in dependencies:
            dependency_name = dependency.rsplit(".", 1)[-1]
            assert LAYERS[dependency_name] <= LAYERS[owner], (
                f"invalid dependency direction: {owner} -> {dependency_name}"
            )


def test_context_inventory_has_no_dynamic_globals_or_wildcard_imports() -> None:
    forbidden = ("globals().update", "bind_runtime_globals", "PEER_EXPORTS")
    for path in [*_python_files(), FACADE]:
        source = path.read_text(encoding="utf-8")
        assert not any(fragment in source for fragment in forbidden), path
        tree = ast.parse(source)
        assert not any(
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path


def test_production_consumers_do_not_import_inventory_facade_or_private_helpers() -> None:
    violations: list[str] = []
    for path in (ROOT / "backend").rglob("*.py"):
        if path == FACADE or PACKAGE in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "backend.services.context_hub_inventory":
                    violations.append(str(path))
                if node.module and node.module.startswith("backend.services.context_inventory"):
                    if any(alias.name in {"_slug", "_sku_content"} for alias in node.names):
                        violations.append(str(path))
            elif isinstance(node, ast.Import) and any(
                alias.name == "backend.services.context_hub_inventory" for alias in node.names
            ):
                violations.append(str(path))
    assert violations == []
