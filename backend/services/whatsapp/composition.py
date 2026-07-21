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

    bound_dependencies = target.get("_BRIDGE_BOUND_DEPENDENCIES")
    if not isinstance(bound_dependencies, dict):
        bound_dependencies = {}
    protected = {
        "_BRIDGE_BOUND_DEPENDENCIES",
        "_IMPLEMENTATIONS",
        "_COMPONENT_FUNCTIONS",
        "bind_bridge_dependencies",
        "invoke",
    }
    for name, value in dependencies.namespace.items():
        if name.startswith("__") or name in protected:
            continue
        previous = bound_dependencies.get(name)
        current = target.get(name)
        if name in bound_dependencies and current is not previous and callable(current):
            # A component-scoped override was installed after the last bind
            # (for example by a test or a runtime adapter).  Nested facade
            # calls must not erase it midway through the active invocation.
            # Mutable runtime scalars are deliberately refreshed because the
            # facade is their compatibility authority between invocations.
            continue
        implementation = implementations.get(name)
        if implementation is not None and getattr(value, "__wrapped__", None) is implementation:
            # Keep calls between functions of the same component local.  Copying
            # the facade's default delegator back into the component would
            # re-enter ``_sync_components`` and overwrite component-scoped
            # monkeypatches halfway through an invocation.  A real facade
            # override has no matching ``__wrapped__`` marker and is still
            # propagated for backwards compatibility.
            value = implementation
        target[name] = value
        bound_dependencies[name] = value
    target["_BRIDGE_BOUND_DEPENDENCIES"] = bound_dependencies
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
