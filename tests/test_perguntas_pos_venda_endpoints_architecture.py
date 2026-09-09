from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from backend.modules.perguntas_pos_venda.endpoints import runtime
from backend.services import perguntas_pos_venda_endpoints as facade


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "modules" / "perguntas_pos_venda" / "endpoints"
FACADE = ROOT / "backend" / "services" / "perguntas_pos_venda_endpoints.py"
FORBIDDEN_FRAGMENTS = (
    "globals().update",
    "bind_runtime_globals",
    "PEER_EXPORTS",
    "_copy_runtime_globals",
)


def _module_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            prefix = "backend.modules.perguntas_pos_venda.endpoints."
            if node.module.startswith(prefix):
                imports.add(node.module.removeprefix(prefix))
            assert not any(alias.name == "*" for alias in node.names), path.name
        elif isinstance(node, ast.Import):
            assert not any(alias.name == "backend.services.perguntas_pos_venda_endpoints" for alias in node.names)
    return imports


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise AssertionError(f"cycle detected at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, set()):
            if dependency in graph:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for module in graph:
        visit(module)


def test_endpoint_facade_and_modules_respect_line_budgets() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"


def test_endpoint_package_has_no_dynamic_globals_wildcards_or_facade_imports() -> None:
    graph: dict[str, set[str]] = {}
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in source, f"{path.name}: {fragment}"
        assert "backend.services.perguntas_pos_venda_endpoints" not in source, path.name
        graph[path.stem] = _module_imports(path)
    _assert_acyclic(graph)


def test_endpoint_facade_does_not_reexport_private_helpers() -> None:
    # Additive GET/POST catalog synchronization facade; private helpers remain private.
    assert len(facade.__all__) == 36
    assert not any(name.startswith("_") for name in facade.__all__)
    assert not hasattr(facade, "_customer_reply_wait_or_raise")
    assert not hasattr(facade, "_ml_pos_venda_sync_running")
    assert not hasattr(facade, "_perguntas_ia_diagnostico_aprovacao")


def test_endpoint_runtime_is_allowlisted_and_idempotent() -> None:
    original = runtime.ENDPOINTS_STATE.runtime
    source = SimpleNamespace(**{
        name: (object() if name == "logger" else lambda *_args, **_kwargs: None)
        for name in runtime.ALLOWED_DEPENDENCIES
    })
    try:
        first = runtime.configure_runtime(source)
        second = runtime.configure_runtime(source)
        assert first is second
        assert set(runtime.ALLOWED_DEPENDENCIES) == set().union(*runtime.DEPENDENCY_GROUPS.values())
    finally:
        runtime.ENDPOINTS_STATE.runtime = original


def test_no_production_consumer_uses_private_endpoint_facade() -> None:
    forbidden = (
        "perguntas_pos_venda_endpoints._",
        "ppv_endpoints._",
    )
    for path in (ROOT / "backend").rglob("*.py"):
        if path == FACADE or PACKAGE in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        for fragment in forbidden:
            assert fragment not in source, f"{path.relative_to(ROOT)}: {fragment}"
