from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.modules.context_hub import api as context_hub_api
from backend.modules.context_hub.contracts import ContextHubRuntime
from backend.modules.context_hub.state import CONTEXT_HUB_STATE


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "modules" / "context_hub"
FACADE = ROOT / "backend" / "services" / "context_hub.py"


def _python_files() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def _module_name(path: Path) -> str:
    return f"backend.modules.context_hub.{path.stem}"


def _internal_graph() -> dict[str, set[str]]:
    modules = {_module_name(path): path for path in _python_files()}
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in modules:
                graph[name].add(node.module)
            elif isinstance(node, ast.Import):
                graph[name].update(alias.name for alias in node.names if alias.name in modules)
    return graph


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"Context Hub dependency cycle at {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in graph[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in graph:
        visit(module)


def test_context_hub_line_budgets_and_function_budgets() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path}:{node.lineno}:{node.name}"


def test_context_hub_has_closed_import_graph_and_no_dynamic_globals() -> None:
    _assert_acyclic(_internal_graph())
    forbidden_fragments = ("globals().update", "bind_runtime_globals", "PEER_EXPORTS")
    for path in [*_python_files(), FACADE]:
        source = path.read_text(encoding="utf-8")
        assert not any(fragment in source for fragment in forbidden_fragments), path
        tree = ast.parse(source)
        assert not any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path
        if path.parent == PACKAGE:
            assert not any(
                (
                    isinstance(node, ast.ImportFrom)
                    and (
                        node.module == "backend.services.context_hub"
                        or (
                            node.module == "backend.services"
                            and any(alias.name == "context_hub" for alias in node.names)
                        )
                    )
                )
                or (
                    isinstance(node, ast.Import)
                    and any(alias.name == "backend.services.context_hub" for alias in node.names)
                )
                for node in ast.walk(tree)
            ), path


def test_production_consumers_do_not_import_context_hub_facade() -> None:
    facade_imports: list[str] = []
    for path in (ROOT / "backend").rglob("*.py"):
        if path == FACADE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "backend.services.context_hub":
                    facade_imports.append(str(path))
                if node.module == "backend.services" and any(alias.name == "context_hub" for alias in node.names):
                    facade_imports.append(str(path))
            elif isinstance(node, ast.Import) and any(
                alias.name == "backend.services.context_hub" for alias in node.names
            ):
                facade_imports.append(str(path))
    assert facade_imports == []


def test_runtime_configuration_is_explicit_idempotent_and_thread_safe(tmp_path: Path) -> None:
    previous = CONTEXT_HUB_STATE.runtime_config
    base = tmp_path / "checkout"
    info = tmp_path / "info"
    try:
        first = context_hub_api.configure_context_hub(base_dir=base, info_root=info, surface="test")
        second = context_hub_api.configure_context_hub(base_dir=base, info_root=info, surface="test")
        assert isinstance(first, ContextHubRuntime)
        assert second is first
        with ThreadPoolExecutor(max_workers=8) as executor:
            configured = list(
                executor.map(
                    lambda _index: context_hub_api.configure_context_hub(
                        base_dir=base,
                        info_root=info,
                        surface="test",
                    ),
                    range(32),
                )
            )
        assert all(runtime is first for runtime in configured)
    finally:
        with CONTEXT_HUB_STATE.config_guard:
            CONTEXT_HUB_STATE.runtime_config = previous
