"""Named status and telemetry API."""

from .runtime import _codex_status_payload as status_payload
from .runtime_policy import _codex_ai_telemetry_instance as instance

__all__ = ["instance", "status_payload"]
__codex_dependencies__ = []
