from __future__ import annotations

import ast
from pathlib import Path

from backend.services import codex_console
from backend.services.codex.console import composition
from backend.services.codex.console import state as console_state


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend/services/codex/console"
FACADE = ROOT / "backend/services/codex_console.py"

EXPECTED_PUBLIC_EXPORTS = (
    "CodexTaskRequest",
    "CodexTaskSteerRequest",
    "CodexConversationResetRequest",
    "CodexActionProposalRequest",
    "CodexActionApprovalRequest",
    "CodexActionRevisionRequest",
    "CodexAgentGuidanceRequest",
    "CodexAgentGuidanceSimulationRequest",
    "CodexCapabilityResolveRequest",
    "configure_codex_console_runtime",
    "codex_status",
    "codex_upload_attachments",
    "codex_criar_tarefa",
    "codex_listar_tarefas",
    "codex_registrar_interacao_whatsapp_externa",
    "codex_listar_conversas_whatsapp",
    "codex_listar_mensagens_conversa_whatsapp",
    "codex_obter_mensagem_conversa_whatsapp",
    "codex_obter_tarefa",
    "codex_deletar_tarefa",
    "codex_deletar_conversa",
    "codex_reset_current_conversation",
    "codex_aprovar_tarefa",
    "codex_cancelar_tarefa",
    "codex_complementar_tarefa",
    "codex_complementar_tarefa_para_sessao",
    "codex_program_functions",
    "codex_capabilities_listar",
    "codex_capabilities_modules",
    "codex_capabilities_resolver",
    "codex_actions_listar",
    "codex_actions_listar_propostas",
    "codex_actions_criar_proposta",
    "codex_actions_aprovar_proposta",
    "codex_actions_revisar_proposta",
    "codex_actions_rejeitar_proposta",
    "codex_actions_obter_execucao",
    "codex_actions_cancelar_execucao",
    "codex_agent_settings_get",
    "codex_agent_guidance_put",
    "codex_agent_guidance_simulate",
    "codex_agent_capability_coverage",
    "codex_agent_plan_get",
    "codex_console_recuperar_fila_background",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def test_facade_is_small_and_exports_only_the_frozen_public_surface():
    assert len(FACADE.read_text(encoding="utf-8-sig").splitlines()) <= 300
    assert tuple(codex_console.__all__) == EXPECTED_PUBLIC_EXPORTS
    assert not [name for name in vars(codex_console) if name.startswith("_codex_")]


def test_components_respect_file_and_function_budgets():
    failures: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if len(lines) > 800:
            failures.append(f"{path.name}: {len(lines)} linhas")
        for node in ast.walk(_tree(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = int(node.end_lineno or node.lineno) - node.lineno + 1
                if size > 120:
                    failures.append(f"{path.name}:{node.lineno} {node.name}: {size} linhas")
    assert failures == []


def test_components_do_not_import_the_facade_or_use_wildcards_or_global_bridge():
    failures: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        text = path.read_text(encoding="utf-8-sig")
        if "bind_runtime_globals" in text:
            failures.append(f"{path.name}: bind_runtime_globals")
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom):
                if node.module == "backend.services.codex_console":
                    failures.append(f"{path.name}: importa fachada")
                if any(alias.name == "*" for alias in node.names):
                    failures.append(f"{path.name}: import curinga")
            elif isinstance(node, ast.Import):
                if any(alias.name == "backend.services.codex_console" for alias in node.names):
                    failures.append(f"{path.name}: importa fachada")
    assert failures == []


def test_component_dependency_registry_is_complete_and_acyclic():
    registry = composition.wire()
    modules = composition.modules()
    assert registry["_codex_run_worker"].__module__.endswith(".worker_execution")
    assert registry["_codex_agent_run_loop"].__module__.endswith(".agent_cycle")
    for module in modules:
        missing = [
            name
            for name in getattr(module, "__codex_dependencies__", ())
            if name not in registry
        ]
        assert missing == [], module.__name__

    graph: dict[str, set[str]] = {path.stem: set() for path in PACKAGE.glob("*.py")}
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                target = node.module.split(".", 1)[0]
                if target in graph:
                    graph[path.stem].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise AssertionError(f"ciclo de imports internos em {name}")
        if name in visited:
            return
        visiting.add(name)
        for target in graph[name]:
            visit(target)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)


def test_console_state_owns_shared_mutable_objects():
    state = console_state.CONSOLE_STATE
    assert console_state.CODEX_TASKS is state.tasks
    assert console_state.CODEX_TASKS_LOCK is state.tasks_lock
    assert console_state.CODEX_ACTIVE_QUEUES is state.active_queues
    assert console_state.CODEX_ACTIVE_TURNS is state.active_turns
    assert console_state.CODEX_DUAL_SOL_GATE is state.dual_sol_gate
    assert console_state.CODEX_RUNTIME_BIN_LOCK is state.runtime_bin_lock


def test_production_consumers_do_not_use_private_facade_symbols():
    failures: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        if path == FACADE or PACKAGE in path.parents:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if "codex_console._codex_" in text:
            failures.append(str(path.relative_to(ROOT)))
        if "codex_console.CODEX_TASKS" in text:
            failures.append(str(path.relative_to(ROOT)))
    assert failures == []
