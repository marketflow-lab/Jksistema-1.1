from __future__ import annotations

import ast
from pathlib import Path

from backend.services import whatsapp_bridge
from backend.services import whatsapp as whatsapp_package


PACKAGE_DIR = Path(whatsapp_package.__file__).resolve().parent
BRIDGE_PATH = Path(whatsapp_bridge.__file__).resolve()


def test_bridge_and_components_respect_file_size_budgets() -> None:
    assert len(BRIDGE_PATH.read_text(encoding="utf-8").splitlines()) <= 1800
    oversized = {
        str(path.relative_to(PACKAGE_DIR)): len(path.read_text(encoding="utf-8").splitlines())
        for path in PACKAGE_DIR.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 900
    }
    assert oversized == {}


def test_bridge_and_components_respect_function_size_budget() -> None:
    oversized = {}
    for path in (BRIDGE_PATH, *PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines = (node.end_lineno or node.lineno) - node.lineno + 1
                if lines > 120:
                    oversized[f"{path.name}:{node.name}"] = lines
    assert oversized == {}


def test_modular_components_do_not_import_compatibility_facade() -> None:
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "import whatsapp_bridge" in source or "from backend.services import whatsapp_bridge" in source:
            offenders.append(str(path.relative_to(PACKAGE_DIR)))
    assert offenders == []


def test_runtime_state_owns_mutable_concurrency_objects() -> None:
    runtime = whatsapp_bridge.BRIDGE_RUNTIME
    assert whatsapp_bridge.DUAL_AGENT_STATE_LOCK is runtime.dual_agent_state_lock
    assert whatsapp_bridge.BRIDGE_STATE_LOCK is runtime.bridge_state_lock
    assert whatsapp_bridge.CONFIG_LOCK is runtime.config_lock
    assert whatsapp_bridge.BRIDGE_STOP_EVENT is runtime.stop_event
    assert whatsapp_bridge.TYPING_PULSES is runtime.typing_pulses
    assert whatsapp_bridge.PROGRESS_PULSES is runtime.progress_pulses
    assert whatsapp_bridge.RUNTIME_STATE is runtime.runtime_diagnostics
    assert whatsapp_bridge.PHONE_DISPATCH_QUEUES is runtime.phone_dispatch_queues
    assert whatsapp_bridge.FUNCTION_MANAGER_FUTURES is runtime.function_manager_futures
    assert whatsapp_bridge.PHONE_LATENCY_SAMPLES is runtime.phone_latency_samples


def test_component_dependencies_are_resolved_after_monkeypatch(monkeypatch) -> None:
    persisted: list[tuple[str, dict]] = []
    state: dict = {}

    monkeypatch.setattr(
        whatsapp_bridge,
        "_save_state",
        lambda value: persisted.append(("first", dict(value))),
    )
    whatsapp_bridge._save_pending(state, "message-1", {"status": "queued"})

    monkeypatch.setattr(
        whatsapp_bridge,
        "_save_state",
        lambda value: persisted.append(("second", dict(value))),
    )
    whatsapp_bridge._save_pending(state, "message-2", {"status": "queued"})

    assert [label for label, _value in persisted] == ["first", "second"]
    assert set(state["pending_messages"]) == {"message-1", "message-2"}
