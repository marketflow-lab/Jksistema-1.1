"""Dependency wiring for console components without importing the legacy facade."""

from __future__ import annotations

import threading
from importlib import import_module
from types import ModuleType
from typing import Any


COMPONENT_MODULES = (
    "state",
    "runtime",
    "runtime_policy",
    "task_views",
    "attachments",
    "conversation_store",
    "task_store",
    "prompt_context",
    "agent_prompt",
    "exact_reports",
    "agent_loop",
    "agent_cycle",
    "scope",
    "worker_setup",
    "worker_execution",
    "queue_worker",
    "api_admin",
    "task_creation",
    "api_create",
    "api_conversations",
    "api_actions",
)

_LOCK = threading.RLock()
_WIRED = False
_REGISTRY: dict[str, Any] = {}
_MODULES: tuple[ModuleType, ...] = ()


def _load_modules() -> tuple[ModuleType, ...]:
    prefix = __package__ or "backend.services.codex.console"
    return tuple(import_module(f"{prefix}.{name}") for name in COMPONENT_MODULES)


def wire() -> dict[str, Any]:
    """Wire only dependencies explicitly declared by each component."""

    global _WIRED, _MODULES
    with _LOCK:
        if _WIRED:
            return dict(_REGISTRY)
        modules = _load_modules()
        registry: dict[str, Any] = {}
        for module in modules:
            for name in tuple(getattr(module, "__codex_exports__", ())):
                if name in registry:
                    raise RuntimeError(f"Duplicate Codex Console owner for {name}")
                registry[name] = getattr(module, name)
            for name, value in vars(module).items():
                if name.startswith("_CODEX_"):
                    registry.setdefault(name, value)
        for support_name in ("state", "contracts"):
            support = import_module(f"{__package__}.{support_name}")
            for name in tuple(getattr(support, "__all__", ())):
                registry.setdefault(name, getattr(support, name))
        for module in modules:
            for name in tuple(getattr(module, "__codex_dependencies__", ())):
                if name not in registry:
                    raise RuntimeError(f"Missing Codex Console dependency {name} for {module.__name__}")
                module.__dict__[name] = registry[name]
        state = import_module(f"{__package__}.state")
        state.configure_task_loader(registry["_codex_load_task"])
        _REGISTRY.update(registry)
        _MODULES = modules
        _WIRED = True
        return dict(_REGISTRY)


def resolve(name: str) -> Any:
    registry = wire()
    try:
        return registry[name]
    except KeyError as exc:
        raise AttributeError(name) from exc


def modules() -> tuple[ModuleType, ...]:
    wire()
    return _MODULES


__all__ = ["COMPONENT_MODULES", "modules", "resolve", "wire"]
