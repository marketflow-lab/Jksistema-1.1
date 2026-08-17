from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

from backend.services import whatsapp_report_visuals as visuals_facade


ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "backend" / "services" / "whatsapp_report_visuals.py"
PACKAGE_PATH = ROOT / "backend" / "services" / "whatsapp" / "report_visuals"

EXPECTED_PUBLIC_EXPORTS = (
    "REPORT_CHART_DIRNAME",
    "REPORT_CHART_MAX_IMAGES",
    "build_chart_data",
    "chart_output_dir",
    "cleanup_stale_chart_files",
    "generate_task_chart_artifacts",
    "should_generate_report_charts",
)
EXPECTED_FUNCTIONS = (
    "_text_key",
    "should_generate_report_charts",
    "_safe_number",
    "_safe_int",
    "_safe_label",
    "_safe_client_id",
    "chart_output_dir",
    "_summary_entries",
    "_normalized_tool_id",
    "_provider_chart_data",
    "_period_from",
    "_coverage_complete",
    "_sales_series",
    "_sales_ranking",
    "_legacy_marketplace_sales_chart_data",
    "_period_label",
    "_sales_metrics",
    "_sales_period_record",
    "_standalone_sales_periods",
    "_comparison_sales_periods",
    "_api_sales_periods",
    "_timeseries_sales_periods",
    "_dedupe_sales_periods",
    "_compose_sales_chart",
    "_sales_chart_data",
    "_legacy_stock_chart_data",
    "_bling_stock_visual",
    "_stale_stock_visual",
    "_stockout_visual",
    "_stock_chart_data",
    "_listing_chart_data",
    "build_chart_data",
    "_sha256",
    "_expires_epoch",
    "_validated_artifacts",
    "generate_task_chart_artifacts",
    "cleanup_stale_chart_files",
)
EXPECTED_SIGNATURES_SHA256 = "e4ffa4d713740a1ec7f17395da7c4929e99db18ae0ed2096453463e5fc4a4379"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_visuals_facade_preserves_contract() -> None:
    assert tuple(visuals_facade.__all__) == EXPECTED_PUBLIC_EXPORTS
    assert visuals_facade.REPORT_CHART_DIRNAME == "whatsapp_report_charts"
    assert visuals_facade.REPORT_CHART_MAX_IMAGES == 2
    assert visuals_facade.REPORT_CHART_TTL_SECONDS == 7 * 24 * 60 * 60
    signature_contract = "\n".join(
        f"{name}{inspect.signature(getattr(visuals_facade, name))}"
        for name in EXPECTED_FUNCTIONS
    )
    assert hashlib.sha256(signature_contract.encode("utf-8")).hexdigest() == EXPECTED_SIGNATURES_SHA256


def test_visuals_modules_stay_within_size_limits() -> None:
    assert len(FACADE_PATH.read_text(encoding="utf-8").splitlines()) <= 200
    for path in PACKAGE_PATH.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 600, path.name
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"


def test_visuals_modules_do_not_import_facade_or_form_cycles() -> None:
    module_paths = {
        path.stem: path for path in PACKAGE_PATH.glob("*.py") if path.name != "__init__.py"
    }
    dependencies = {name: set() for name in module_paths}
    for name, path in module_paths.items():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "backend.services.whatsapp_report_visuals"
                assert all(alias.name != "*" for alias in node.names)
                if node.level == 1 and node.module in module_paths:
                    dependencies[name].add(node.module)

    visited: set[str] = set()
    active: set[str] = set()

    def visit(module: str) -> None:
        assert module not in active, f"cyclic report-visuals import at {module}"
        if module in visited:
            return
        active.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        active.remove(module)
        visited.add(module)

    for module in dependencies:
        visit(module)
