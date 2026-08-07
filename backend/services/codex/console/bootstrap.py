"""Idempotent startup for Codex Console and its adjacent service adapters."""

from backend.services import codex_actions, codex_capabilities, codex_operational_memory

from . import bindings, composition


def configure(runtime_module=None):
    configured = bindings.configure(runtime_module)
    codex_actions.configure_codex_actions_runtime(runtime_module)
    codex_capabilities.configure_codex_capabilities_runtime(runtime_module)
    codex_operational_memory.configure_codex_operational_memory_runtime(runtime_module)
    composition.wire()
    return configured.source_module


__all__ = ["configure"]
