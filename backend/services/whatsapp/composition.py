"""Runtime composition primitives for the modular WhatsApp bridge.

Components never import the compatibility facade.  The facade supplies the
current namespace immediately before an invocation so legacy monkeypatches keep
working while implementations live in smaller domain modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping


@dataclass(frozen=True, slots=True)
class BridgeDependencies:
    """Late-bound dependency view supplied by the compatibility facade."""

    namespace: Mapping[str, Any]

    @classmethod
    def from_namespace(cls, namespace: Mapping[str, Any]) -> "BridgeDependencies":
        return cls(MappingProxyType(dict(namespace)))

    def resolve(self, name: str) -> Any:
        return self.namespace[name]


def bind_component_namespace(
    target: MutableMapping[str, Any],
    implementations: Mapping[str, Any],
    dependencies: BridgeDependencies,
) -> None:
    """Refresh component globals without replacing stored implementations."""

    protected = {
        "_IMPLEMENTATIONS",
        "_COMPONENT_FUNCTIONS",
        "bind_bridge_dependencies",
        "invoke",
    }
    for name, value in dependencies.namespace.items():
        if name.startswith("__") or name in protected:
            continue
        target[name] = value
    target["_IMPLEMENTATIONS"] = implementations


def invoke_component(
    implementations: Mapping[str, Any],
    name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Any:
    """Invoke an original implementation retained outside the rebound globals."""

    return implementations[name](*args, **dict(kwargs))


__all__ = ["BridgeDependencies", "bind_component_namespace", "invoke_component"]
