from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from backend.routers.codex_console import create_codex_console_router
from backend.services import codex_assistant
from backend.services.codex.assistant import contracts
from backend.services.codex.assistant.catalog_data import CODEX_DATA_TOOLS
from backend.services.codex.assistant.settings import CODEX_DATA_TOOLS_VERSION


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "services" / "codex" / "assistant"
PUBLIC_EXPORTS = [
    "CodexAssistantChatRequest", "CodexAssistantRunRequest", "CodexAssistantReportRequest",
    "CodexAssistantReportSettingsRequest", "CodexAssistantFinancialAdjustmentRequest",
    "CodexAssistantActionQueueRequest", "CodexAssistantActionQueueUpdateRequest",
    "CodexAssistantEvaluationRequest", "CodexOperationalMemoryRequest",
    "configure_codex_assistant_runtime", "codex_assistant_collect_context",
    "codex_assistant_execute_tool_call", "codex_assistant_chat", "codex_assistant_suggestions",
    "codex_assistant_tools", "codex_assistant_evaluation_get", "codex_assistant_evaluation_run",
    "codex_assistant_memory_get", "codex_assistant_memory_post", "codex_assistant_memory_delete",
    "codex_assistant_data_sources", "codex_assistant_bling_resources",
    "codex_assistant_mercado_livre_resources", "codex_assistant_proactive_run",
    "codex_assistant_daily_analysis_run", "codex_assistant_weekly_analysis_run",
    "codex_assistant_report_create", "codex_assistant_report_get",
    "codex_assistant_report_settings_get", "codex_assistant_report_settings_put",
    "codex_assistant_financial_adjustments_get", "codex_assistant_financial_adjustments_post",
    "codex_assistant_financial_adjustments_delete", "codex_assistant_action_queue_get",
    "codex_assistant_action_queue_post", "codex_assistant_action_queue_patch",
    "codex_assistant_report_download",
]
CONTRACT_HASHES = {
    "routes": "4ebfd34e22c94caa47be18cd4a906c46aaf9913a595c1c697b0f934bc66b073c",
    "models": "50577d8671eb505eadc3d1e1641d6d5a109966e7e8359a6cbf5957090e848652",
    "tools": "a451f8d036f47c1516d7391cfea7861c9212cd58087c70bfbe3bc91154d1b51e",
    "exports": "65e73e076f4e0d4ba8a9a55a44ed06c0bde213106372c12cb368c03c8496bb1a",
}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_snapshot() -> dict[str, object]:
    routes = sorted(
        [{"path": route.path, "methods": sorted(route.methods or []), "name": route.name}
         for route in create_codex_console_router().routes
         if str(route.name).startswith("codex_assistant_")],
        key=lambda item: (item["path"], item["methods"], item["name"]),
    )
    model_names = sorted(
        name for name in dir(contracts)
        if name.startswith("Codex") and hasattr(getattr(contracts, name), "model_json_schema")
    )
    models = {name: getattr(contracts, name).model_json_schema() for name in model_names}
    keys = ("id", "module", "executor", "external", "read_only", "required_permissions",
            "zero_is_authoritative", "status", "source_role", "aggregation_policy")
    tools = sorted([{key: tool.get(key) for key in keys} for tool in CODEX_DATA_TOOLS], key=lambda item: item["id"])
    return {"routes": routes, "models": models, "tools": tools, "exports": list(codex_assistant.__all__)}


def test_public_contract_snapshot_matches_v118() -> None:
    snapshot = _contract_snapshot()
    assert len(snapshot["routes"]) == 25
    assert len(snapshot["models"]) == 9
    assert len(snapshot["tools"]) == 57
    assert snapshot["exports"] == PUBLIC_EXPORTS
    assert {key: _digest(value) for key, value in snapshot.items()} == CONTRACT_HASHES


def test_facade_and_components_respect_architecture_budgets() -> None:
    facade = ROOT / "backend" / "services" / "codex_assistant.py"
    assert len(facade.read_text(encoding="utf-8").splitlines()) <= 300
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"
            if isinstance(node, ast.ImportFrom):
                assert not any(alias.name == "*" for alias in node.names), path.name
                assert node.module != "backend.services.codex_assistant", path.name


def test_facade_has_no_private_helpers_and_consumers_use_owned_modules() -> None:
    assert list(codex_assistant.__all__) == PUBLIC_EXPORTS
    assert not any(name.startswith("_assistant_") for name in vars(codex_assistant))
    for path in (ROOT / "backend").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        assert "codex_assistant._assistant" not in source, path
        assert "tool_validation" not in source, path
        assert "dados_suficientes" not in source, path
        assert "proximas_fontes" not in source, path
    assert CODEX_DATA_TOOLS_VERSION == "20260731-data-tools-v23-evidence-envelope"
