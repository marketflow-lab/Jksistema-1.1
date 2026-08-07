"""Shared state and constants for the Codex console components."""

from __future__ import annotations

import os
import threading
from typing import Any, Optional

from backend.services import codex_ai_telemetry, codex_evaluations

CODEX_SANDBOXES = {"read_only", "workspace_write", "full_access"}
CODEX_INTERNAL_ALLOWED_SANDBOXES = {"read_only"}
CODEX_EXECUTION_PLANE = "in_app_operations"
CODEX_DEVELOPMENT_WRITE_ENABLED = False
CODEX_DEVELOPMENT_ERROR_CODE = "DEVELOPMENT_REQUIRES_CODEX_DESKTOP"

_TASK_LOADER = lambda _task_id: None


def configure_task_loader(loader) -> None:
    global _TASK_LOADER
    _TASK_LOADER = loader

class _ResizableConcurrencyGate:
    def __init__(self, limit: int, per_key_limit: int = 3) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._limit = max(1, int(limit or 1))
        self._per_key_limit = max(1, int(per_key_limit or 1))
        self._active = 0
        self._active_by_key: dict[str, int] = {}
        self._waiting = 0

    def configure(self, limit: int, per_key_limit: Optional[int] = None) -> int:
        with self._condition:
            self._limit = max(1, min(12, int(limit or 1)))
            if per_key_limit is not None:
                self._per_key_limit = max(1, min(6, int(per_key_limit or 1)))
            self._condition.notify_all()
            return self._limit

    def acquire(self, task_id: str, key: str = "") -> bool:
        gate_key = str(key or "")
        with self._condition:
            self._waiting += 1
            try:
                while (
                    self._active >= self._limit
                    or self._active_by_key.get(gate_key, 0) >= self._per_key_limit
                ):
                    self._condition.wait(1.0)
                    if (
                        self._active >= self._limit
                        or self._active_by_key.get(gate_key, 0) >= self._per_key_limit
                    ):
                        task = _TASK_LOADER(task_id)
                        if not isinstance(task, dict) or str(task.get("status") or "") != "queued":
                            return False
                self._active += 1
                self._active_by_key[gate_key] = self._active_by_key.get(gate_key, 0) + 1
                return True
            finally:
                self._waiting = max(0, self._waiting - 1)

    def release(self, key: str = "") -> None:
        gate_key = str(key or "")
        with self._condition:
            self._active = max(0, self._active - 1)
            current = max(0, self._active_by_key.get(gate_key, 0) - 1)
            if current:
                self._active_by_key[gate_key] = current
            else:
                self._active_by_key.pop(gate_key, None)
            self._condition.notify_all()

    def diagnostics(self) -> dict[str, Any]:
        with self._condition:
            return {
                "limit": self._limit,
                "per_key_limit": self._per_key_limit,
                "active": self._active,
                "active_by_key": dict(self._active_by_key),
                "waiting": self._waiting,
                "available": max(0, self._limit - self._active),
            }


class ConsoleState:
    """Single owner for mutable process state used by the console runtime."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.tasks_lock = threading.RLock()
        self.full_access_lock = threading.Lock()
        self.conversation_lock = threading.RLock()
        self.queue_lock = threading.RLock()
        self.active_queues: set[str] = set()
        self.active_turns_lock = threading.RLock()
        self.active_turns: dict[str, Any] = {}
        self.telemetry_lock = threading.RLock()
        self.telemetry: Optional[codex_ai_telemetry.CodexAITelemetry] = None
        self.evaluation_runner: Optional[codex_evaluations.EvaluationRunner] = None
        self.dual_sol_gate = _ResizableConcurrencyGate(12, 6)
        self.runtime_bin_lock = threading.Lock()
        self.runtime_bin_cache: Optional[str] = None
        self.runtime_selected_path_cache = ""
        self.runtime_selected_version_cache: tuple[int, ...] = ()
        self.runtime_config_error_cache = ""


CONSOLE_STATE = ConsoleState()

# Compatibility aliases for extracted components while ownership remains in
# ``CONSOLE_STATE``. Collections, locks and gates retain object identity.
CODEX_TASKS = CONSOLE_STATE.tasks
CODEX_TASKS_LOCK = CONSOLE_STATE.tasks_lock
CODEX_FULL_ACCESS_LOCK = CONSOLE_STATE.full_access_lock
CODEX_CONVERSATION_LOCK = CONSOLE_STATE.conversation_lock
CODEX_QUEUE_LOCK = CONSOLE_STATE.queue_lock
CODEX_ACTIVE_QUEUES = CONSOLE_STATE.active_queues
CODEX_ACTIVE_TURNS_LOCK = CONSOLE_STATE.active_turns_lock
CODEX_ACTIVE_TURNS = CONSOLE_STATE.active_turns
CODEX_AI_TELEMETRY_LOCK = CONSOLE_STATE.telemetry_lock
CODEX_AI_TELEMETRY = CONSOLE_STATE.telemetry
CODEX_EVALUATION_RUNNER = CONSOLE_STATE.evaluation_runner
CODEX_DUAL_SOL_GATE = CONSOLE_STATE.dual_sol_gate
CODEX_RUNTIME_BIN_LOCK = CONSOLE_STATE.runtime_bin_lock
CODEX_RUNTIME_BIN_CACHE = CONSOLE_STATE.runtime_bin_cache
CODEX_RUNTIME_SELECTED_PATH_CACHE = CONSOLE_STATE.runtime_selected_path_cache
CODEX_RUNTIME_SELECTED_VERSION_CACHE = CONSOLE_STATE.runtime_selected_version_cache
CODEX_RUNTIME_CONFIG_ERROR_CACHE = CONSOLE_STATE.runtime_config_error_cache

BLACK_JHON_DISPLAY_NAME = "Black Jhon"

CODEX_DEFAULT_MODEL = "gpt-5.5"
CODEX_SIDEBAR_TASK_PROMPT_VERSION = "black-jhon-sidebar-task-2026-07-20-v2"
CODEX_SIDEBAR_TASK_SCHEMA_VERSION = "codex-sidebar-task-v2"

CODEX_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}

CODEX_SPEEDS = {"standard", "fast"}

CODEX_AGENT_MAX_CYCLES = 6

CODEX_AGENT_REPORT_MAX_CYCLES = 10

CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE = 5

CODEX_AGENT_SCREEN_CONTEXT_LIMIT = 6000

CODEX_AGENT_TOOL_RESULT_LIMIT = 60000

CODEX_AGENT_CATALOG_LIMIT = 60000

CODEX_AGENT_INPUT_TOKEN_SOFT_LIMIT = 850000

CODEX_AGENT_INPUT_TOKEN_TARGET = 150000

CODEX_PATHS_MAX_COUNT = 80

CODEX_ATTACHMENT_MAX_COUNT = 20

CODEX_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024

CODEX_ATTACHMENT_TOTAL_MAX_BYTES = 100 * 1024 * 1024

CODEX_ATTACHMENT_TTL_SECONDS = 7 * 24 * 60 * 60

CODEX_CONVERSATION_RECENT_MESSAGES = int(os.getenv("JK_CODEX_CONVERSATION_RECENT_MESSAGES") or "12")

CODEX_CONVERSATION_RECENT_CHAR_LIMIT = int(os.getenv("JK_CODEX_CONVERSATION_RECENT_CHAR_LIMIT") or "24000")

CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT = int(os.getenv("JK_CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT") or "12000")

CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT = int(os.getenv("JK_CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT") or "120000")

CODEX_API_AUTH_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "OPENAI_PROJECT",
    "OPENAI_PROJECT_ID",
)

CODEX_SCOPE_TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".css",
    ".csv",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".txt",
    ".vbs",
    ".xml",
    ".yaml",
    ".yml",
}

CODEX_SCOPE_RUNTIME_PREFIXES = (
    ".codex-remote-attachments/",
    "info/codex_actions/",
    "info/codex_console/",
    "info/tmp_",
)

CODEX_SCOPE_SNAPSHOT_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "backups",
    "Cache",
    "Cache_Data",
    "dist",
    "dist-client-setup",
    "info",
    "node_modules",
    "tmp",
}

__all__ = [
    name
    for name in tuple(globals())
    if name.startswith("CODEX_")
    or name in {"BLACK_JHON_DISPLAY_NAME", "CONSOLE_STATE", "ConsoleState", "_ResizableConcurrencyGate"}
]
