from __future__ import annotations

import ast
import hashlib
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.services import ia_tools_marketplaces
from backend.services.codex.assistant.catalog_data import CODEX_DATA_TOOLS
from backend.services.marketplace_tools import analytics, registry, runtime


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "services" / "marketplace_tools"
FACADE = ROOT / "backend" / "services" / "ia_tools_marketplaces.py"
PUBLIC_EXPORTS = [
    "MarketplaceToolsRuntime",
    "configure_ia_tools_marketplaces_runtime",
    "get_integrations_status",
    "get_mercado_livre_listing",
    "get_mercado_livre_orders",
    "get_mercado_livre_returns",
    "get_mercado_livre_visits",
    "get_mercado_livre_promotions",
    "resolve_exact_order",
    "generate_sku_image_response",
]
LEGACY_PRIVATE_EXPORTS = {
    "_ia_lojas_bling_conectadas", "_ia_lojas_com_integracao", "_ia_obter_cfg_bling",
    "_ia_tool_get_integrations_status", "_ia_ml_precisa_descricao", "_ia_ml_item_resumo",
    "_ia_ml_obter_descricao_item", "_ia_ml_listar_anuncios", "_ia_ml_resolve_exact_order",
    "_ia_tool_get_mercado_livre_listing", "_ia_tool_get_mercado_livre_orders",
    "_ia_tool_get_mercado_livre_returns", "_ia_tool_get_mercado_livre_visits",
    "_ia_tool_get_mercado_livre_promotions", "_ia_bling_valor_tributario",
    "_ia_buscar_imagem_ml_sku", "_ia_montar_prompt_geracao_imagem_sku",
    "_ia_salvar_imagem_gerada", "_ia_gerar_imagem_sku_resposta",
}
CONTRACT_HASHES = {
    "signatures": "83c2844a25903641dee209b552a8d5bb8d85e3d59e3de1278f794cf84ee60c64",
    "tools": "a4689ee0245cd072f6b572710181f018a7120b8dcbd60bf35f9f0b8ad9595a41",
    "schemas": "8c65d95e5b944c926c3d5cf4ad07fc1a72cc6783388de4d8281884667ba18d77",
    "registry": "15657b56276a1102ac081485a64a371a09b8703db107282d299fddc8b0070b59",
    "exports": "e3b3e8938a82282c8c972f96c10b403bc3ec4b4023a6773bb9d006f2b2695246",
}
LAYERS = {
    "contracts": 0, "runtime": 0, "registry": 0,
    "items": 1, "integrations": 1, "images": 1,
    "client": 2,
    "listing_search": 3, "order_models": 3,
    "analytics": 4, "order_search": 4, "post_sale_messages": 4,
    "post_sale_claims": 5,
    "exact_orders": 6,
    "listings": 7, "orders": 7, "returns": 7, "traffic": 7, "promotions": 7,
    "api": 8, "__init__": 9,
}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_snapshot() -> dict[str, object]:
    function_names = [name for name in PUBLIC_EXPORTS if name not in {
        "MarketplaceToolsRuntime", "configure_ia_tools_marketplaces_runtime",
    }]
    signatures = {name: str(inspect.signature(getattr(ia_tools_marketplaces, name))) for name in function_names}
    keys = ("id", "module", "executor", "external", "read_only", "required_permissions",
            "zero_is_authoritative", "status", "source_role", "aggregation_policy")
    tools = sorted(
        [{key: tool.get(key) for key in keys} for tool in CODEX_DATA_TOOLS
         if tool.get("executor") in registry.TOOL_EXECUTORS],
        key=lambda item: item["id"],
    )
    schemas = {
        "sales": analytics.sales_chart_data([], {}, coverage_complete=False)["schema"],
        "listing": analytics._ia_ml_listing_chart_data([], coverage_complete=False)["schema"],
    }
    return {
        "signatures": signatures,
        "tools": tools,
        "schemas": schemas,
        "registry": registry.TOOL_EXECUTORS,
        "exports": list(ia_tools_marketplaces.__all__),
    }


def _local_dependencies(path: Path) -> set[str]:
    dependencies: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        names = [node.module.split(".", 1)[0]] if node.module else [alias.name for alias in node.names]
        dependencies.update(name for name in names if (PACKAGE / f"{name}.py").exists())
    return dependencies


def test_marketplace_public_contract_snapshot() -> None:
    snapshot = _contract_snapshot()
    assert len(snapshot["tools"]) == 6
    assert snapshot["schemas"] == {
        "sales": "jk.marketplace.sales_by_day.v1",
        "listing": "jk.marketplace.listing_snapshot.v1",
    }
    assert snapshot["exports"] == PUBLIC_EXPORTS
    assert {name: _digest(value) for name, value in snapshot.items()} == CONTRACT_HASHES


def test_marketplace_facade_and_components_respect_budgets() -> None:
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) <= 300
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"
            if isinstance(node, ast.ImportFrom):
                assert not any(alias.name == "*" for alias in node.names), path.name
                assert node.module != "backend.services.ia_tools_marketplaces", path.name


def test_marketplace_dependency_direction_has_no_cycles_or_dynamic_globals() -> None:
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
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "bind_runtime_globals" not in source, path.name
        assert "globals().update" not in source, path.name
        assert "PEER_EXPORTS" not in source, path.name


def test_marketplace_runtime_is_allowlisted_idempotent_and_thread_safe() -> None:
    original = runtime.current()
    sentinel = lambda *_args, **_kwargs: "sentinel"
    peers = {"carregar_lojas": sentinel, "not_allowlisted": object()}
    try:
        first = runtime.configure(peers=peers)
        second = runtime.configure(peers=peers)
        assert first is second
        assert first.stores.load is sentinel
        assert not hasattr(first, "not_allowlisted")
        with ThreadPoolExecutor(max_workers=8) as executor:
            configured = list(executor.map(lambda _: runtime.configure(peers=peers), range(16)))
        assert all(item is first for item in configured)
        assert runtime.configure(first) is first
    finally:
        runtime.configure(original)


def test_legacy_private_exports_are_absent_and_not_consumed_from_facade() -> None:
    assert list(ia_tools_marketplaces.__all__) == PUBLIC_EXPORTS
    assert LEGACY_PRIVATE_EXPORTS.isdisjoint(vars(ia_tools_marketplaces))
    for path in (ROOT / "backend").rglob("*.py"):
        if path.parent == PACKAGE:
            continue
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "backend.services.ia_tools_marketplaces":
                assert not any(alias.name.startswith("_") for alias in node.names), path
        assert not any(f"ia_tools_marketplaces.{name}" in source for name in LEGACY_PRIVATE_EXPORTS), path
