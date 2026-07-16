"""Internal Codex console endpoints and task runner."""

from __future__ import annotations

import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import codex_actions, codex_agent_runtime, codex_assistant_storage, codex_capabilities, codex_operational_memory
from backend.services.runtime_bridge import bind_runtime_globals


CODEX_SANDBOXES = {"read_only", "workspace_write", "full_access"}
CODEX_TASKS: dict[str, dict[str, Any]] = {}
CODEX_TASKS_LOCK = threading.RLock()
CODEX_FULL_ACCESS_LOCK = threading.Lock()
CODEX_CONVERSATION_LOCK = threading.RLock()
CODEX_QUEUE_LOCK = threading.RLock()
CODEX_ACTIVE_QUEUES: set[str] = set()
CODEX_ACTIVE_TURNS_LOCK = threading.RLock()
CODEX_ACTIVE_TURNS: dict[str, Any] = {}


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
                        task = _codex_load_task(task_id)
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


CODEX_DUAL_SOL_GATE = _ResizableConcurrencyGate(12, 6)
BLACK_JHON_DISPLAY_NAME = "Black Jhon"
CODEX_DEFAULT_MODEL = "gpt-5.5"
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
CODEX_RUNTIME_BIN_LOCK = threading.Lock()
CODEX_RUNTIME_BIN_CACHE: Optional[str] = None
CODEX_RUNTIME_SELECTED_PATH_CACHE = ""
CODEX_RUNTIME_SELECTED_VERSION_CACHE: tuple[int, ...] = ()
CODEX_RUNTIME_CONFIG_ERROR_CACHE = ""
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


class CodexTaskRequest(BaseModel):
    prompt: str
    sandbox: str = "read_only"
    thread_id: Optional[str] = None
    conversation_id: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    approval_mode: Optional[str] = None
    reasoning_effort: Optional[str] = None
    speed: Optional[str] = None
    service_tier: Optional[str] = None
    goal: Optional[str] = None
    planning_mode: bool = False
    paths: Optional[list[str]] = None
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None
    request_id: Optional[str] = None


class CodexTaskSteerRequest(BaseModel):
    message: str
    request_id: Optional[str] = None


class CodexTaskApprovalRequest(BaseModel):
    paths: Optional[list[str]] = None
    screen_context: Optional[dict[str, Any]] = None


class CodexConversationResetRequest(BaseModel):
    confirm: bool = False


class CodexActionProposalRequest(BaseModel):
    message: str
    action_id: Optional[str] = None
    capability_id: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    conversation_id: Optional[str] = None
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None


class CodexActionApprovalRequest(BaseModel):
    proposal_version: Optional[int] = None
    proposal_hash: Optional[str] = None


class CodexActionRevisionRequest(BaseModel):
    params: Optional[dict[str, Any]] = None
    message: Optional[str] = None


class CodexAgentGuidanceRequest(BaseModel):
    guidance_id: Optional[str] = None
    scope_type: str = "global"
    scope_key: Optional[str] = None
    text: str = ""
    active: bool = True


class CodexAgentGuidanceSimulationRequest(BaseModel):
    module: Optional[str] = None
    store: Optional[str] = None
    supplier: Optional[str] = None
    sku: Optional[str] = None


class CodexCapabilityResolveRequest(BaseModel):
    message: str = ""
    capability_id: Optional[str] = None
    module: Optional[str] = None
    category: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    limit: int = 8


def configure_codex_console_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    codex_actions.configure_codex_actions_runtime(runtime_module)
    codex_capabilities.configure_codex_capabilities_runtime(runtime_module)
    codex_operational_memory.configure_codex_operational_memory_runtime(runtime_module)
    return runtime


def _codex_bool_env(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "sim", "on", "enabled"}


def _codex_int_env(name: str, default: int, minimum: int = 1, maximum: int = 1000000) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except Exception:
        value = int(default)
    return max(minimum, min(value, maximum))


def _codex_enabled() -> bool:
    return _codex_bool_env("JK_CODEX_CONSOLE_ENABLED", False)


def _codex_agent_mode_enabled() -> bool:
    if _codex_bool_env("JK_CODEX_LEGACY_CONTEXT_MODE", False):
        return False
    return _codex_bool_env("JK_CODEX_AGENT_MODE_ENABLED", True)


def _codex_base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _codex_base_info_dir() -> str:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_codex_base_dir(), "info")).strip()
    if not os.path.isabs(base_info):
        base_info = os.path.join(_codex_base_dir(), base_info)
    os.makedirs(base_info, exist_ok=True)
    return base_info


def _codex_info_dir() -> str:
    path = os.path.join(_codex_base_info_dir(), "codex_console")
    os.makedirs(path, exist_ok=True)
    return path


def _codex_task_path(task_id: str) -> str:
    safe_id = "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return os.path.join(_codex_info_dir(), f"{safe_id}.json")


def _codex_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _codex_deadline_at(seconds: int) -> str:
    safe_seconds = max(30, min(int(seconds or 180), 600))
    return datetime.fromtimestamp(time.time() + safe_seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _codex_deadline_epoch(value: Any) -> int:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except Exception:
        return int(time.time()) + 180


def _codex_native_mcp_enabled(task: Any) -> bool:
    if not isinstance(task, dict) or str(task.get("origin") or "") != "whatsapp":
        return False
    migration = task.get("mcp_migration") if isinstance(task.get("mcp_migration"), dict) else {}
    return bool(migration.get("native_enabled"))


def _codex_native_mcp_result_path(task_id: Any) -> Path:
    safe = _codex_safe_id(str(task_id or ""), "task")
    path = Path(_codex_info_dir()) / "mcp_runtime" / f"{safe}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _codex_native_mcp_read_results(task_id: Any, seen: set[str]) -> list[dict[str, Any]]:
    path = _codex_native_mcp_result_path(task_id)
    if not path.is_file():
        return []
    results: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines[-100:]:
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict) or str(record.get("task_id") or "") != str(task_id or ""):
            continue
        call_hash = str(record.get("call_hash") or "")
        result = record.get("result") if isinstance(record.get("result"), dict) else None
        if not call_hash or call_hash in seen or result is None:
            continue
        seen.add(call_hash)
        results.append(result)
    return results


def _codex_native_mcp_cleanup(task_id: Any) -> None:
    try:
        _codex_native_mcp_result_path(task_id).unlink(missing_ok=True)
    except Exception:
        pass


def _codex_native_mcp_thread_config(task: dict[str, Any], screen_context: Any) -> dict[str, Any]:
    """Build an ephemeral, signed stdio MCP definition for one WhatsApp task."""

    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    raw_policy = metadata.get("query_policy") if isinstance(metadata.get("query_policy"), dict) else {}
    authorized_stores = [
        str(item or "").strip()
        for item in (raw_policy.get("authorized_stores") or raw_policy.get("stores") or [])
        if str(item or "").strip()
    ]
    if not authorized_stores and str(raw_policy.get("store") or "").strip():
        authorized_stores = [str(raw_policy.get("store") or "").strip()]
    payload = {
        "version": 1,
        "task_id": str(task.get("task_id") or ""),
        "conversation_id": str(task.get("conversation_id") or ""),
        "client_id": str(task.get("client_id") or "default"),
        "username": str(task.get("created_by") or "whatsapp"),
        "wa_id_hash": hashlib.sha256(str(metadata.get("wa_id") or "").encode("utf-8")).hexdigest(),
        "permissions": task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
        "authorized_stores": authorized_stores,
        "source_policy": _codex_agent_source_policy_from_screen(screen_context),
        "screen_context": _codex_agent_screen_summary(screen_context),
        "result_path": str(_codex_native_mcp_result_path(task.get("task_id")).resolve()),
        "deadline_at_epoch": _codex_deadline_epoch(task.get("deadline_at")),
        "expires_at": _codex_deadline_epoch(task.get("deadline_at")) + 300,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = secrets.token_urlsafe(32)
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    server_path = str(Path(__file__).with_name("jk_codex_mcp_server.py").resolve())
    return {
        "mcp_servers": {
            "jk_system": {
                "command": sys.executable,
                "args": [server_path],
                "env": {
                    "JK_CODEX_MCP_CONTEXT_B64": encoded,
                    "JK_CODEX_MCP_CONTEXT_SIGNATURE": signature,
                    "JK_CODEX_MCP_CONTEXT_SECRET": secret,
                },
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 300,
                "enabled": True,
            }
        }
    }


def _codex_sdk_installed() -> bool:
    return importlib.util.find_spec("openai_codex") is not None


def _codex_auth_file_path() -> str:
    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        return os.path.join(os.path.expanduser(codex_home), "auth.json")
    return os.path.join(str(Path.home()), ".codex", "auth.json")


def _codex_auth_detected() -> bool:
    return os.path.exists(_codex_auth_file_path())


def _codex_cli_version() -> tuple[bool, str]:
    codex_bin = shutil.which("codex")
    if codex_bin:
        return True, codex_bin
    try:
        from codex_cli_bin import bundled_codex_path

        bundled = str(Path(bundled_codex_path()).resolve())
        if os.path.isfile(bundled):
            return True, bundled
    except Exception:
        pass
    return False, ""


def _codex_bin_version(path: str | Path) -> tuple[int, ...]:
    candidate = str(path or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return ()
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [candidate, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=creation_flags,
        )
    except Exception:
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)(?:[^0-9]+(\d+))?", f"{proc.stdout}\n{proc.stderr}")
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _codex_desktop_runtime_candidates() -> list[Path]:
    """Localiza runtimes instalados pelo aplicativo Codex no Windows."""
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if not local_app_data:
        return []
    bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    if not bin_root.is_dir():
        return []
    candidates = [bin_root / "codex.exe"]
    candidates.extend(bin_root.rglob("codex.exe"))
    return list(dict.fromkeys(candidates))


def _codex_bin_config_preflight(path: str | Path) -> tuple[bool, str]:
    """Confirma que o runtime consegue carregar o config.toml/MCP padrao."""
    candidate = str(path or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return False, "Executavel Codex nao encontrado."
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [candidate, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired:
        return False, "Tempo excedido ao validar a configuracao MCP do Codex."
    except Exception as exc:
        return False, f"Falha ao validar a configuracao MCP do Codex: {exc}"
    if proc.returncode == 0:
        return True, ""
    detail = str(proc.stderr or proc.stdout or "").strip()
    detail = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    return False, detail[-1200:] or f"Codex encerrou o preflight com codigo {proc.returncode}."


def _codex_runtime_diagnostics() -> dict[str, Any]:
    _codex_runtime_bin()
    with CODEX_RUNTIME_BIN_LOCK:
        version = ".".join(str(part) for part in CODEX_RUNTIME_SELECTED_VERSION_CACHE)
        return {
            "path": CODEX_RUNTIME_SELECTED_PATH_CACHE,
            "version": version,
            "config_ok": not bool(CODEX_RUNTIME_CONFIG_ERROR_CACHE),
            "config_error": CODEX_RUNTIME_CONFIG_ERROR_CACHE,
        }


def _codex_runtime_require_ready() -> Optional[str]:
    runtime_bin = _codex_runtime_bin()
    diagnostics = _codex_runtime_diagnostics()
    if diagnostics.get("config_error"):
        config_path = _codex_config_file_path()
        raise RuntimeError(
            "A configuracao local do Codex nao e compativel com os runtimes encontrados. "
            "O login continua valido. Atualize o aplicativo Codex ou revise "
            f"{config_path}, especialmente [mcp_servers.node_repl]. "
            f"Detalhe tecnico: {diagnostics['config_error']}"
        )
    return runtime_bin


def _codex_runtime_bin() -> Optional[str]:
    """Seleciona o Codex mais novo que consiga carregar a configuracao local."""
    global CODEX_RUNTIME_BIN_CACHE
    global CODEX_RUNTIME_SELECTED_PATH_CACHE
    global CODEX_RUNTIME_SELECTED_VERSION_CACHE
    global CODEX_RUNTIME_CONFIG_ERROR_CACHE
    with CODEX_RUNTIME_BIN_LOCK:
        if CODEX_RUNTIME_BIN_CACHE is not None:
            return CODEX_RUNTIME_BIN_CACHE or None

        candidates: list[Path] = []
        for env_name in ("JK_CODEX_BIN", "CODEX_BIN"):
            raw = str(os.getenv(env_name) or "").strip()
            if raw:
                candidates.append(Path(os.path.expandvars(os.path.expanduser(raw))))
        path_bin = shutil.which("codex")
        if path_bin:
            candidates.append(Path(path_bin))
        candidates.extend(_codex_desktop_runtime_candidates())

        home = Path.home()
        extension_roots = (
            home / ".vscode" / "extensions",
            home / ".vscode-insiders" / "extensions",
            home / ".cursor" / "extensions",
        )
        platform_dir = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
        executable = "codex.exe" if os.name == "nt" else "codex"
        for root in extension_roots:
            if not root.is_dir():
                continue
            candidates.extend(root.glob(f"openai.chatgpt-*/bin/{platform_dir}/{executable}"))

        try:
            from codex_cli_bin import bundled_codex_path

            bundled = Path(bundled_codex_path()).resolve()
            bundled_version = _codex_bin_version(bundled)
        except Exception:
            bundled = None
            bundled_version = ()

        unique: dict[str, tuple[Path, tuple[int, ...], bool]] = {}
        all_candidates = list(candidates)
        if bundled is not None:
            all_candidates.append(bundled)
        for candidate in all_candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                continue
            if not resolved.is_file():
                continue
            version = _codex_bin_version(resolved)
            if version:
                is_bundled = bool(bundled is not None and resolved == bundled)
                unique[str(resolved).lower()] = (resolved, version, is_bundled)

        ordered = sorted(unique.values(), key=lambda item: item[1], reverse=True)
        failures: list[str] = []
        selected: tuple[Path, tuple[int, ...], bool] | None = None
        for candidate, version, is_bundled in ordered:
            config_ok, config_error = _codex_bin_config_preflight(candidate)
            if config_ok:
                selected = (candidate, version, is_bundled)
                break
            label = f"{candidate.name} {'.'.join(str(part) for part in version)}"
            failures.append(f"{label}: {config_error}")

        if selected is not None:
            selected_path, selected_version, selected_is_bundled = selected
            CODEX_RUNTIME_SELECTED_PATH_CACHE = str(selected_path)
            CODEX_RUNTIME_SELECTED_VERSION_CACHE = selected_version
            CODEX_RUNTIME_CONFIG_ERROR_CACHE = ""
            CODEX_RUNTIME_BIN_CACHE = "" if selected_is_bundled else str(selected_path)
        elif ordered:
            fallback_path, fallback_version, fallback_is_bundled = ordered[0]
            CODEX_RUNTIME_SELECTED_PATH_CACHE = str(fallback_path)
            CODEX_RUNTIME_SELECTED_VERSION_CACHE = fallback_version
            CODEX_RUNTIME_CONFIG_ERROR_CACHE = " | ".join(failures)[-1800:]
            CODEX_RUNTIME_BIN_CACHE = "" if fallback_is_bundled else str(fallback_path)
        else:
            CODEX_RUNTIME_SELECTED_PATH_CACHE = ""
            CODEX_RUNTIME_SELECTED_VERSION_CACHE = bundled_version
            CODEX_RUNTIME_CONFIG_ERROR_CACHE = ""
            CODEX_RUNTIME_BIN_CACHE = ""
        return CODEX_RUNTIME_BIN_CACHE or None


def _codex_local_request_allowed(request: Request) -> bool:
    if _codex_bool_env("JK_CODEX_ALLOW_REMOTE", False):
        return True
    host = ""
    try:
        host = str(request.client.host if request.client else "").strip().lower()
    except Exception:
        host = ""
    return host in {"127.0.0.1", "::1", "localhost"}


def _codex_payload_sessao(authorization: Optional[str]) -> dict[str, Any]:
    if "_payload_sessao_por_authorization" not in globals():
        raise HTTPException(status_code=503, detail="Runtime de autenticacao ainda nao configurado.")
    return _payload_sessao_por_authorization(authorization)


def _codex_require_authenticated(
    request: Request,
    authorization: Optional[str],
) -> dict[str, Any]:
    if not _codex_local_request_allowed(request):
        raise HTTPException(status_code=403, detail="Codex Console esta liberado apenas em acesso local.")

    sessao = _codex_payload_sessao(authorization)
    username = str(sessao.get("username") or "").strip().lower()
    client_id = str(sessao.get("client_id") or "").strip()
    if not username or not client_id:
        raise HTTPException(status_code=401, detail="Sessao invalida para usar o Black Jhon.")
    permissoes = _carregar_permissoes_usuario(username, client_id)
    return {
        "username": username,
        "client_id": client_id,
        "permissions": permissoes,
        "is_full": permissoes.get("full") is True,
    }


def _codex_require_full_admin(
    request: Request,
    authorization: Optional[str],
) -> dict[str, Any]:
    sessao = _codex_require_authenticated(request, authorization)
    permissoes = sessao.get("permissions") if isinstance(sessao.get("permissions"), dict) else {}
    if permissoes.get("full") is not True:
        raise HTTPException(status_code=403, detail="Apenas administradores full podem usar o Codex.")
    return sessao


def _codex_status_payload() -> dict[str, Any]:
    sdk_ok = _codex_sdk_installed()
    enabled = _codex_enabled()
    runtime = _codex_runtime_diagnostics() if sdk_ok and enabled else {
        "path": "",
        "version": "",
        "config_ok": True,
        "config_error": "",
    }
    cli_ok, cli_path = _codex_cli_version()
    if runtime.get("path"):
        cli_ok = True
        cli_path = str(runtime.get("path") or "")
    auth_file = _codex_auth_file_path()
    auth_file_exists = _codex_auth_detected()
    runtime_config_ok = bool(runtime.get("config_ok", True))
    ready = bool(enabled and sdk_ok and runtime_config_ok)
    if not sdk_ok:
        runtime_status = "dependency_missing"
    elif not runtime_config_ok:
        runtime_status = "configuration_invalid"
    elif not auth_file_exists:
        runtime_status = "authentication_pending"
    elif enabled:
        runtime_status = "ready"
    else:
        runtime_status = "disabled"
    message = f"{BLACK_JHON_DISPLAY_NAME} pronto com Codex como IA principal."
    if not enabled:
        message = f"{BLACK_JHON_DISPLAY_NAME} esta com o Codex desabilitado pela configuracao local."
    elif not sdk_ok:
        message = "Instale a dependencia openai-codex no runtime Python."
    elif not runtime_config_ok:
        message = (
            "A configuracao MCP local do Codex e incompativel. Atualize o aplicativo Codex "
            "ou revise [mcp_servers.node_repl] no config.toml; o login nao foi perdido."
        )
    elif not auth_file_exists:
        message = "SDK instalado. Se nao houver credencial no keyring, rode codex login nesta maquina."

    return {
        "success": True,
        "enabled": enabled,
        "ready": ready,
        "runtime_status": runtime_status,
        "authentication_required": bool(sdk_ok and not auth_file_exists),
        "sdk_installed": sdk_ok,
        "cli_available": cli_ok,
        "cli_path": cli_path,
        "runtime_version": str(runtime.get("version") or ""),
        "runtime_config_ok": runtime_config_ok,
        "auth_file_detected": auth_file_exists,
        "auth_file_path": auth_file,
        "cwd": _codex_base_dir(),
        "defaults": {
            "model": str(os.getenv("JK_CODEX_MODEL") or CODEX_DEFAULT_MODEL).strip() or CODEX_DEFAULT_MODEL,
            "reasoning_effort": str(os.getenv("JK_CODEX_REASONING_EFFORT") or "xhigh").strip() or "xhigh",
            "speed": str(os.getenv("JK_CODEX_SPEED") or "standard").strip() or "standard",
            "approval_mode": "request",
            "sandbox": "read_only",
            "agent_mode": _codex_agent_mode_enabled(),
        },
        "message": message,
    }


def _codex_status_for_session(sessao: dict[str, Any]) -> dict[str, Any]:
    payload = _codex_status_payload()
    is_full = bool(sessao.get("is_full"))
    payload["access"] = {
        "mode": "full" if is_full else "read_only",
        "can_mutate": is_full,
        "can_approve": is_full,
        "can_upload": is_full,
    }
    if not is_full:
        payload.pop("cli_path", None)
        payload.pop("auth_file_path", None)
        payload.pop("cwd", None)
        defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
        defaults.update(
            {
                "approval_mode": "read_only",
                "sandbox": "read_only",
            }
        )
        payload["defaults"] = defaults
    client_id = str(sessao.get("client_id") or "").strip()
    username = str(sessao.get("username") or "").strip().lower()
    if client_id and username:
        state = _codex_load_or_create_conversation_state(client_id, username, channel="app")
        conversation_id = str(state.get("conversation_id") or "")
        generation = int(state.get("generation") or 1)
        active_statuses = {"queued", "running", "awaiting_approval", "cancel_requested"}
        active_tasks = [
            task
            for task in _codex_owned_persisted_tasks(client_id, username)
            if _codex_task_stored_conversation_id(task) == conversation_id
            and int(task.get("conversation_generation") or 1) == generation
            and str(task.get("status") or "") in active_statuses
        ]
        payload["conversation"] = {
            "conversation_id": conversation_id,
            "channel": "app",
            "generation": generation,
            "state": "active",
            "queue": {
                "running": sum(1 for task in active_tasks if str(task.get("status") or "") in {"running", "cancel_requested"}),
                "pending": sum(1 for task in active_tasks if str(task.get("status") or "") in {"queued", "awaiting_approval"}),
            },
            "can_reset": not active_tasks,
        }
    return payload


def _codex_observability_from_task(task: dict[str, Any]) -> dict[str, Any]:
    tool_calls = [item for item in (task.get("tool_calls") or []) if isinstance(item, dict)]
    result_summaries = [item for item in (task.get("tool_results_summary") or []) if isinstance(item, dict)]
    last_call = tool_calls[-1] if tool_calls else {}
    last_result = result_summaries[-1] if result_summaries else {}
    validation = last_result.get("tool_validation") if isinstance(last_result.get("tool_validation"), dict) else {}
    raw_sources = (
        last_result.get("sources_human")
        if isinstance(last_result.get("sources_human"), list)
        else last_result.get("sources")
        if isinstance(last_result.get("sources"), list)
        else []
    )
    sources: list[str] = []
    source_label = str(last_result.get("source_label") or "").strip()
    if source_label:
        sources.append(source_label)
    for source in raw_sources:
        if isinstance(source, dict):
            text = str(source.get("source_label") or source.get("function_label") or source.get("label") or source.get("source") or source.get("function") or source.get("tool_id") or "").strip()
        else:
            text = str(source or "").strip()
        if text and text not in sources:
            sources.append(text)
    if not sources:
        for source in task.get("sources") or []:
            text = str((source.get("source_label") or source.get("source")) if isinstance(source, dict) else source or "").strip()
            if text and text not in sources:
                sources.append(text)
    warnings = [str(item or "").strip() for item in (last_result.get("warnings") or task.get("warnings") or []) if str(item or "").strip()]
    failures = []
    empty_reason = str(last_result.get("empty_reason") or "").strip()
    if empty_reason:
        failures.append(empty_reason)
    failures.extend(warnings[:4])
    next_fallbacks = list(
        validation.get("proximas_fontes_humanas")
        or last_result.get("next_fallbacks_human")
        or validation.get("proximas_fontes")
        or last_result.get("next_fallbacks")
        or []
    )
    return {
        "current_status": str(task.get("live_status") or task.get("status") or "").strip(),
        "last_tool": str(last_result.get("tool_label") or last_result.get("tool_id") or last_call.get("tool_id") or "").strip(),
        "last_tool_module": str(last_result.get("module") or "").strip(),
        "source": sources[0] if sources else "",
        "sources": sources[:8],
        "records": int(last_result.get("records") or 0) if last_result else 0,
        "confidence": str(validation.get("confidence") or "").strip(),
        "enough_data": validation.get("dados_suficientes") if validation else None,
        "failures": failures[:6],
        "next_fallbacks": [str(item or "").strip() for item in next_fallbacks[:8] if str(item or "").strip()],
        "tool_calls_count": len(tool_calls),
        "tool_results_count": len(result_summaries),
        "updated_at": _codex_now(),
    }


def _codex_agent_guidance_context(prompt: str, screen_context: Any) -> dict[str, str]:
    context = screen_context if isinstance(screen_context, dict) else {}
    selection = context.get("selection") if isinstance(context.get("selection"), dict) else {}
    filters = context.get("filters") if isinstance(context.get("filters"), dict) else {}

    def first(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text[:240]
        return ""

    corpus = "\n".join(
        str(item or "")
        for item in (
            prompt,
            context.get("visible_text"),
            context.get("title"),
        )
    )
    sku_match = re.search(r"\bSKU\s*[:#-]?\s*([A-Za-z0-9._/-]{1,80})\b", corpus, flags=re.I)
    return {
        "module": first(context.get("modulo_atual"), context.get("module"), selection.get("module")),
        "store": first(
            selection.get("loja"),
            selection.get("store"),
            filters.get("loja"),
            filters.get("store"),
            context.get("loja"),
        ),
        "supplier": first(selection.get("fornecedor"), selection.get("supplier"), filters.get("fornecedor")),
        "sku": first(selection.get("sku"), filters.get("sku"), sku_match.group(1) if sku_match else ""),
    }


def _codex_sync_plan_fields(task: dict[str, Any], plan: Any) -> None:
    if not isinstance(plan, dict):
        return
    task.update(
        {
            "plan_id": str(plan.get("plan_id") or task.get("plan_id") or ""),
            "agent_state": str(plan.get("agent_state") or task.get("agent_state") or "entendendo"),
            "steps": list(plan.get("steps") or []),
            "current_step": str(plan.get("current_step") or ""),
            "required_input": list(plan.get("required_input") or []),
            "proposal": plan.get("proposal") if isinstance(plan.get("proposal"), dict) else {},
            "guidance_applied": list(plan.get("guidance_applied") or []),
            "verification": plan.get("verification") if isinstance(plan.get("verification"), dict) else {},
            "idempotency_key": str(plan.get("idempotency_key") or task.get("idempotency_key") or ""),
        }
    )


def _codex_transition_task_plan(
    task_id: str,
    state: str,
    *,
    current_step: str = "",
    step_status: str = "",
    required_input: Optional[list[Any]] = None,
    proposal: Optional[dict[str, Any]] = None,
    verification: Optional[dict[str, Any]] = None,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not isinstance(task, dict) or not task.get("plan_id"):
        return {}
    plan = codex_agent_runtime.transition_plan(
        _codex_base_info_dir(),
        str(task.get("client_id") or "default"),
        str(task.get("plan_id") or ""),
        state,
        current_step=current_step,
        step_status=step_status,
        required_input=required_input,
        proposal=proposal,
        verification=verification,
        details=details,
    )
    updates: dict[str, Any] = {}
    _codex_sync_plan_fields(updates, plan)
    if updates:
        _codex_update_task(task_id, **updates)
    return plan


def _codex_public_task(task: dict[str, Any]) -> dict[str, Any]:
    conversation = _codex_task_conversation_metadata(task)
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "sandbox": task.get("sandbox"),
        "cwd": task.get("cwd"),
        "thread_id": task.get("thread_id"),
        "conversation_id": conversation.get("conversation_id") or task.get("conversation_id") or task.get("task_id"),
        "conversation_generation": int(conversation.get("conversation_generation") or 1),
        "conversation_state": conversation.get("conversation_state") or "archived",
        "channel": conversation.get("channel") or "app",
        "queue_position": _codex_task_queue_position(task),
        "prompt": task.get("prompt"),
        "mutable_intent": bool(task.get("mutable_intent")),
        "model": task.get("model"),
        "approval_mode": task.get("approval_mode"),
        "reasoning_effort": task.get("reasoning_effort"),
        "reasoning_level": task.get("reasoning_level") or task.get("reasoning_effort") or "",
        "reasoning_policy": task.get("reasoning_policy") or "fixed",
        "reasoning_max": task.get("reasoning_max") or task.get("reasoning_effort") or "",
        "orchestration_profile": task.get("orchestration_profile") or "default",
        "agent_role": task.get("agent_role") or "",
        "agent_lane": task.get("agent_lane") or "",
        "parent_job_id": task.get("parent_job_id") or "",
        "job_group_id": task.get("job_group_id") or "",
        "subtask_id": task.get("subtask_id") or "",
        "logical_subtask_id": task.get("logical_subtask_id") or "",
        "current_attempt": int(task.get("current_attempt") or 1),
        "attempt_task_ids": [str(item or "")[:100] for item in list(task.get("attempt_task_ids") or [])[-50:]],
        "retry_count": int(task.get("retry_count") or 0),
        "retry_reason": str(task.get("retry_reason") or "")[:1000],
        "next_retry_at_epoch": float(task.get("next_retry_at_epoch") or 0),
        "handoff_status": task.get("handoff_status") or "",
        "last_conversation_tick_at": task.get("last_conversation_tick_at") or "",
        "delivery_state": task.get("delivery_state") or "",
        "sol_queue_wait_started_at": task.get("sol_queue_wait_started_at") or "",
        "sol_started_at": task.get("sol_started_at") or "",
        "restart_recovery_count": int(task.get("restart_recovery_count") or 0),
        "runtime_retry_count": int(task.get("runtime_retry_count") or 0),
        "runtime_retry_after_seconds": int(task.get("runtime_retry_after_seconds") or 0),
        "tool_protocol": task.get("tool_protocol") or "typed_catalog_text_v1",
        "mcp_migration": task.get("mcp_migration") if isinstance(task.get("mcp_migration"), dict) else {},
        "speed": task.get("speed"),
        "service_tier": task.get("service_tier"),
        "goal": task.get("goal") or "",
        "planning_mode": bool(task.get("planning_mode")),
        "paths": list(task.get("paths") or []),
        "scope": task.get("scope") if isinstance(task.get("scope"), dict) else {},
        "scope_changed_files": list(task.get("scope_changed_files") or []),
        "scope_violations": list(task.get("scope_violations") or []),
        "screen_context": task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {},
        "history": list(task.get("history") or [])[-40:],
        "context_stats": task.get("context_stats") if isinstance(task.get("context_stats"), dict) else {},
        "app_data_context": task.get("app_data_context") if isinstance(task.get("app_data_context"), dict) else {},
        "conversation_summary": task.get("conversation_summary") if isinstance(task.get("conversation_summary"), dict) else {},
        "conversation_compaction": task.get("conversation_compaction") if isinstance(task.get("conversation_compaction"), dict) else {},
        "agent_mode": bool(task.get("agent_mode")),
        "plan_id": task.get("plan_id") or "",
        "agent_state": task.get("agent_state") or ("concluido" if task.get("status") == "completed" else "entendendo"),
        "steps": list(task.get("steps") or []),
        "current_step": task.get("current_step") or "",
        "required_input": list(task.get("required_input") or []),
        "proposal": task.get("proposal") if isinstance(task.get("proposal"), dict) else {},
        "action_run": task.get("action_run") if isinstance(task.get("action_run"), dict) else {},
        "guidance_applied": list(task.get("guidance_applied") or []),
        "verification": task.get("verification") if isinstance(task.get("verification"), dict) else {},
        "idempotency_key": task.get("idempotency_key") or "",
        "agent_steps": list(task.get("agent_steps") or []),
        "tool_calls": list(task.get("tool_calls") or []),
        "tool_results_summary": list(task.get("tool_results_summary") or []),
        "sources": list(task.get("sources") or []),
        "warnings": list(task.get("warnings") or []),
        "observability": _codex_observability_from_task(task),
        "live_status": task.get("live_status") or "",
        "live_answer": task.get("live_answer") or "",
        "reasoning_summary": task.get("reasoning_summary") or "",
        "live_plan": task.get("live_plan") or "",
        "token_usage": task.get("token_usage") if isinstance(task.get("token_usage"), dict) else {},
        "turn_id": task.get("turn_id") or "",
        "active_turn_id": task.get("active_turn_id") or "",
        "can_steer": bool(task.get("can_steer")),
        "wait_reason": task.get("wait_reason") or "",
        "progress_events": list(task.get("progress_events") or [])[-120:],
        "last_progress_at": task.get("last_progress_at") or "",
        "deadline_at": task.get("deadline_at") or "",
        "deadline_seconds": int(task.get("deadline_seconds") or 0),
        "steer_events": list(task.get("steer_events") or [])[-20:],
        "final_response": task.get("final_response") or "",
        "error": task.get("error") or "",
        "message_kind": task.get("message_kind") or "",
        "memory_excluded": bool(task.get("memory_excluded")),
        "report_id": task.get("report_id") or "",
        "report_formats": list(task.get("report_formats") or []),
        "logs": list(task.get("logs") or [])[-80:],
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "created_by": task.get("created_by"),
        "client_id": task.get("client_id"),
        "origin": task.get("origin") or "app",
        "channel_message_id": task.get("channel_message_id") or "",
        "channel_metadata": task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {},
        "external_safe_mode": bool(task.get("external_safe_mode")),
        "whatsapp_full_access": bool(task.get("whatsapp_full_access")),
        "whatsapp_query_only": bool(task.get("whatsapp_query_only")),
        "query_policy": task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {},
        "access_mode": task.get("access_mode") or ("full" if task.get("sandbox") != "read_only" else "read_only"),
        "approval_required": bool(task.get("approval_required")),
        "approved": bool(task.get("approved")),
    }


def _codex_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    conversation = _codex_task_conversation_metadata(task)
    prompt = str(task.get("prompt") or "")
    response = str(
        task.get("final_response")
        or task.get("live_answer")
        or task.get("error")
        or ""
    )
    return {
        "task_id": task.get("task_id"),
        "client_id": task.get("client_id"),
        "created_by": task.get("created_by"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "model": task.get("model"),
        "conversation_id": conversation.get("conversation_id") or task.get("conversation_id") or task.get("task_id"),
        "conversation_generation": int(conversation.get("conversation_generation") or 1),
        "conversation_state": conversation.get("conversation_state") or "archived",
        "channel": conversation.get("channel") or "app",
        "queue_position": _codex_task_queue_position(task),
        "thread_id": task.get("thread_id") or "",
        "prompt_preview": prompt[:240],
        "response_preview": response[:600],
        "message_kind": task.get("message_kind") or "",
        "agent_role": task.get("agent_role") or "",
        "parent_job_id": task.get("parent_job_id") or "",
        "delivery_state": task.get("delivery_state") or "",
        "plan_id": task.get("plan_id") or "",
        "agent_state": task.get("agent_state") or "",
        "current_step": task.get("current_step") or "",
        "required_input": list(task.get("required_input") or []),
        "proposal_id": str((task.get("proposal") or {}).get("proposal_id") or "") if isinstance(task.get("proposal"), dict) else "",
        "verification": task.get("verification") if isinstance(task.get("verification"), dict) else {},
        "memory_excluded": bool(task.get("memory_excluded")),
        "report_id": task.get("report_id") or "",
        "report_formats": list(task.get("report_formats") or []),
        "context_stats": task.get("context_stats") if isinstance(task.get("context_stats"), dict) else {},
        "token_usage": task.get("token_usage") if isinstance(task.get("token_usage"), dict) else {},
    }


def codex_register_report_history(
    *,
    client_id: str,
    username: str,
    prompt: str,
    report: dict[str, Any],
    thread_id: str = "",
    conversation_id: str = "",
    screen_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report_id = str((report or {}).get("report_id") or "").strip()
    if not report_id:
        return {}
    state = _codex_load_or_create_conversation_state(
        str(client_id or "default"),
        str(username or "").strip().lower(),
        channel="app",
    )
    canonical_conversation_id = str(state.get("conversation_id") or "")
    task_id = report_id
    now = _codex_now()
    created_at = str((report or {}).get("created_at") or now)
    chat_text = str((report or {}).get("chat_text") or "").strip()
    title = str((report or {}).get("title") or f"Relatorio {BLACK_JHON_DISPLAY_NAME}").strip()
    formats = []
    if isinstance((report or {}).get("chat_download_formats"), list):
        formats = [str(fmt or "").strip().lower() for fmt in report.get("chat_download_formats") or [] if str(fmt or "").strip()]
    elif isinstance((report or {}).get("formats"), dict):
        formats = [fmt for fmt, enabled in (report.get("formats") or {}).items() if enabled]
    task = {
        "task_id": task_id,
        "status": "completed",
        "sandbox": "read_only",
        "cwd": _codex_base_dir(),
        "thread_id": "",
        "conversation_id": canonical_conversation_id,
        "conversation_generation": int(state.get("generation") or 1),
        "prompt": str(prompt or title).strip() or title,
        "model": "codex-assistant-report",
        "approval_mode": "read_only",
        "reasoning_effort": "",
        "speed": "",
        "service_tier": "",
        "goal": "",
        "planning_mode": False,
        "paths": [],
        "screen_context": screen_context if isinstance(screen_context, dict) else {},
        "history": [],
        "context_stats": {},
        "app_data_context": {},
        "conversation_summary": {},
        "conversation_compaction": {},
        "agent_mode": False,
        "agent_steps": list((report or {}).get("status_steps") or []),
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": list((report or {}).get("sources") or []),
        "warnings": list((report or {}).get("warnings") or []),
        "live_status": "",
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
        "token_usage": {},
        "turn_id": "",
        "final_response": chat_text or title,
        "error": "",
        "message_kind": "report",
        "memory_excluded": True,
        "report_id": report_id,
        "report_formats": formats,
        "logs": [{"at": now, "text": f"Relatorio {BLACK_JHON_DISPLAY_NAME} gerado e persistido no historico.", "kind": "report"}],
        "created_at": created_at,
        "started_at": created_at,
        "completed_at": created_at,
        "created_by": str(username or ""),
        "client_id": str(client_id or "default"),
        "origin": "app",
        "channel_metadata": {},
        "approval_required": False,
        "approved": True,
    }
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    return _codex_public_task(task)


def _codex_safe_id(value: str, fallback: str = "default") -> str:
    safe_id = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe_id or fallback


def _codex_task_belongs_to_session(task: Any, sessao: dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    task_client = str(task.get("client_id") or "").strip()
    task_owner = str(task.get("created_by") or "").strip().lower()
    session_client = str(sessao.get("client_id") or "").strip()
    session_owner = str(sessao.get("username") or "").strip().lower()
    return bool(
        task_client
        and task_owner
        and session_client
        and session_owner
        and task_client == session_client
        and task_owner == session_owner
    )


def _codex_require_owned_task(task_id: str, sessao: dict[str, Any]) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not task or not _codex_task_belongs_to_session(task, sessao):
        # 404 evita revelar a existencia de tarefas de outro usuario/cliente.
        raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada.")
    return task


def _codex_safe_filename(value: str, fallback: str = "arquivo") -> str:
    name = os.path.basename(str(value or "").replace("\\", "/")).strip()
    if not name:
        name = fallback
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    if not name:
        name = fallback
    stem, ext = os.path.splitext(name[:180])
    stem = stem.strip(" .-_") or fallback
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:24]
    return (stem[:140] + ext)[:180]


def _codex_attachments_base_dir() -> Path:
    path = Path(_codex_base_dir()) / ".codex-remote-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_attachment_conversation_dir(client_id: str, username: str, conversation_id: str) -> Path:
    return (
        _codex_attachments_base_dir()
        / _codex_safe_id(client_id)
        / _codex_safe_id(username, "user")
        / _codex_safe_id(conversation_id)
    )


def _codex_attachment_dir(client_id: str, username: str, conversation_id: str) -> Path:
    date_part = time.strftime("%Y-%m-%d", time.localtime())
    path = _codex_attachment_conversation_dir(client_id, username, conversation_id) / date_part
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_cleanup_old_attachments() -> None:
    base = _codex_attachments_base_dir()
    cutoff = time.time() - CODEX_ATTACHMENT_TTL_SECONDS
    for path in list(base.rglob("*")):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            continue
    for path in sorted(base.rglob("*"), key=lambda item: len(str(item)), reverse=True):
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except Exception:
            continue


def _codex_attachment_public_payload(path: Path, original_name: str, mime_type: str, size: int) -> dict[str, Any]:
    absolute = path.resolve()
    try:
        rel = os.path.relpath(str(absolute), _codex_base_dir()).replace("\\", "/")
    except Exception:
        rel = _codex_relpath(str(absolute), _codex_base_dir())
    return {
        "id": path.stem.split("_", 1)[0],
        "name": original_name,
        "mime_type": mime_type or "application/octet-stream",
        "size": int(size or 0),
        "path": rel,
        "relative_path": rel,
    }


def _codex_conversation_id(thread_id: str = "", task_id: str = "") -> str:
    return _codex_safe_id(str(thread_id or "").strip() or str(task_id or "").strip() or uuid.uuid4().hex)


def _codex_normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) < 8 or len(digits) > 15:
        return ""
    return digits


def _codex_phone_identity(value: Any) -> str:
    """Normaliza a identidade sem separar a variante brasileira com nono digito."""
    digits = _codex_normalize_phone(value)
    if digits.startswith("55") and len(digits) == 13 and digits[4] == "9":
        return digits[:4] + digits[5:]
    return digits


def _codex_canonical_conversation_id(
    client_id: str,
    username: str,
    *,
    channel: str = "app",
    phone: Any = "",
    lane: Any = "",
) -> str:
    channel_norm = "whatsapp" if str(channel or "").strip().lower() == "whatsapp" else "app"
    client_norm = str(client_id or "default").strip().lower() or "default"
    username_norm = str(username or "user").strip().lower() or "user"
    phone_norm = _codex_phone_identity(phone) if channel_norm == "whatsapp" else ""
    lane_norm = _codex_safe_id(str(lane or "").strip().lower(), "")[:40]
    if channel_norm == "whatsapp" and not phone_norm:
        raise HTTPException(
            status_code=409,
            detail="Nao foi possivel identificar com seguranca o telefone do WhatsApp.",
        )
    raw = (
        f"{client_norm}:{username_norm}:{channel_norm}:{phone_norm}:{lane_norm}"
        if lane_norm
        else f"{client_norm}:{username_norm}:{channel_norm}:{phone_norm}"
    )
    digest = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]
    prefix = "wa" if channel_norm == "whatsapp" else "app"
    return f"{prefix}_{lane_norm}_{digest}" if lane_norm else f"{prefix}_{digest}"


def _codex_universal_conversation_id(client_id: str, username: str) -> str:
    """Compatibilidade: a conversa universal antiga agora aponta para o canal app."""
    return _codex_canonical_conversation_id(client_id, username, channel="app")


def _codex_resolve_new_conversation_id(
    sessao: dict[str, Any],
    requested: Any = "",
    *,
    origin: str = "app",
    channel_metadata: Optional[dict[str, Any]] = None,
) -> str:
    # `requested` permanece no contrato para clientes antigos, mas nunca define
    # a identidade. Isso impede criar varias conversas trocando um ID no browser.
    del requested
    metadata = channel_metadata if isinstance(channel_metadata, dict) else {}
    channel = "whatsapp" if str(origin or "").strip().lower() == "whatsapp" else "app"
    return _codex_canonical_conversation_id(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
        channel=channel,
        phone=metadata.get("wa_id") or metadata.get("phone"),
        lane=metadata.get("agent_lane") or metadata.get("agent_role"),
    )


def _codex_conversation_dir(client_id: str, username: str) -> Path:
    path = (
        Path(_codex_info_dir())
        / "conversations"
        / _codex_safe_id(client_id)
        / _codex_safe_id(username, "user")
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _codex_conversation_path(client_id: str, username: str, conversation_id: str) -> Path:
    return _codex_conversation_dir(client_id, username) / f"{_codex_safe_id(conversation_id)}.json"


def _codex_task_channel(task: dict[str, Any]) -> str:
    return "whatsapp" if str(task.get("origin") or "").strip().lower() == "whatsapp" else "app"


def _codex_task_phone(task: dict[str, Any]) -> str:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return _codex_normalize_phone(metadata.get("wa_id") or metadata.get("phone"))


def _codex_task_lane(task: dict[str, Any]) -> str:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return _codex_safe_id(
        str(task.get("agent_lane") or metadata.get("agent_lane") or task.get("agent_role") or metadata.get("agent_role") or "").strip().lower(),
        "",
    )[:40]


def _codex_task_stored_conversation_id(task: dict[str, Any]) -> str:
    return _codex_safe_id(
        str(task.get("conversation_id") or task.get("thread_id") or task.get("task_id") or "").strip(),
        "",
    )


def _codex_find_latest_legacy_conversation(
    client_id: str,
    username: str,
    *,
    channel: str,
    phone: str = "",
    canonical_id: str,
) -> tuple[str, dict[str, Any]]:
    username_norm = str(username or "").strip().lower()
    try:
        paths = sorted(
            Path(_codex_info_dir()).glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        paths = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        if _codex_task_channel(task) != channel:
            continue
        if channel == "whatsapp" and _codex_task_phone(task) != phone:
            continue
        if str(task.get("status") or "") != "completed":
            continue
        if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
            continue
        if not str(task.get("prompt") or "").strip() or not str(task.get("final_response") or "").strip():
            continue
        legacy_id = _codex_task_stored_conversation_id(task)
        if legacy_id and legacy_id != canonical_id:
            return legacy_id, task
    return "", {}


def _codex_load_or_create_conversation_state(
    client_id: str,
    username: str,
    *,
    channel: str = "app",
    phone: Any = "",
    lane: Any = "",
) -> dict[str, Any]:
    channel_norm = "whatsapp" if str(channel or "").strip().lower() == "whatsapp" else "app"
    phone_norm = _codex_normalize_phone(phone) if channel_norm == "whatsapp" else ""
    lane_norm = _codex_safe_id(str(lane or "").strip().lower(), "")[:40]
    conversation_id = _codex_canonical_conversation_id(
        client_id,
        username,
        channel=channel_norm,
        phone=phone_norm,
        lane=lane_norm,
    )
    with CODEX_CONVERSATION_LOCK:
        stored = _codex_load_conversation_summary(client_id, username, conversation_id)
        if (
            str(stored.get("conversation_id") or "") == conversation_id
            and int(stored.get("generation") or 0) >= 1
        ):
            return stored

        legacy_id, legacy_task = ("", {})
        if not lane_norm:
            legacy_id, legacy_task = _codex_find_latest_legacy_conversation(
                client_id,
                username,
                channel=channel_norm,
                phone=phone_norm,
                canonical_id=conversation_id,
            )
        legacy_summary = _codex_load_conversation_summary(client_id, username, legacy_id) if legacy_id else {}
        now = _codex_now()
        payload = {
            "conversation_id": conversation_id,
            "client_id": str(client_id or "default"),
            "created_by": str(username or "").strip().lower(),
            "channel": channel_norm,
            "agent_lane": lane_norm,
            "phone_fingerprint": hashlib.sha256(phone_norm.encode("utf-8")).hexdigest()[:16] if phone_norm else "",
            "generation": 1,
            "state": "active",
            "summary": str(legacy_summary.get("summary") or ""),
            "recent_messages": list(legacy_summary.get("recent_messages") or []),
            "legacy_conversation_ids": [legacy_id] if legacy_id else [],
            "latest_thread_id": str(legacy_task.get("thread_id") or "") if legacy_task else "",
            "created_at": now,
            "updated_at": now,
            "reset_audit": [],
        }
        _codex_save_conversation_summary(client_id, username, conversation_id, payload)
        return payload


def _codex_conversation_state_for_task(task: dict[str, Any]) -> dict[str, Any]:
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    channel = _codex_task_channel(task)
    phone = _codex_task_phone(task)
    lane = _codex_task_lane(task)
    if not client_id or not username or (channel == "whatsapp" and not phone):
        return {}
    return _codex_load_or_create_conversation_state(
        client_id,
        username,
        channel=channel,
        phone=phone,
        lane=lane,
    )


def _codex_save_conversation_state(task: dict[str, Any], **updates: Any) -> dict[str, Any]:
    with CODEX_CONVERSATION_LOCK:
        state = _codex_conversation_state_for_task(task)
        if not state:
            return {}
        generation = int(task.get("conversation_generation") or 1)
        if generation != int(state.get("generation") or 1):
            return state
        state.update(updates)
        state["updated_at"] = _codex_now()
        _codex_save_conversation_summary(
            str(task.get("client_id") or "default"),
            str(task.get("created_by") or "").strip().lower(),
            str(state.get("conversation_id") or ""),
            state,
        )
        return state


def _codex_task_conversation_metadata(task: dict[str, Any]) -> dict[str, Any]:
    stored_id = _codex_task_stored_conversation_id(task)
    state = _codex_conversation_state_for_task(task)
    if not state:
        return {
            "conversation_id": stored_id,
            "conversation_generation": int(task.get("conversation_generation") or 1),
            "conversation_state": "archived",
            "channel": _codex_task_channel(task),
        }
    canonical_id = str(state.get("conversation_id") or stored_id)
    generation = int(task.get("conversation_generation") or 1)
    active_generation = int(state.get("generation") or 1)
    aliases = {
        _codex_safe_id(str(item or "").strip(), "")
        for item in (state.get("legacy_conversation_ids") or [])
        if str(item or "").strip()
    }
    selected = stored_id == canonical_id or (generation == 1 and stored_id in aliases)
    return {
        "conversation_id": canonical_id if selected else stored_id,
        "conversation_generation": generation,
        "conversation_state": "active" if selected and generation == active_generation else "archived",
        "channel": str(state.get("channel") or _codex_task_channel(task)),
    }


def _codex_deleted_conversations_path(client_id: str, username: str) -> Path:
    return _codex_conversation_dir(client_id, username) / "_deleted_conversations.json"


def _codex_load_deleted_conversations(client_id: str, username: str) -> dict[str, Any]:
    path = _codex_deleted_conversations_path(client_id, username)
    if not path.exists():
        return {"deleted": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            deleted = payload.get("deleted")
            if isinstance(deleted, dict):
                return {"deleted": deleted}
    except Exception:
        pass
    return {"deleted": {}}


def _codex_deleted_conversation_ids(client_id: str, username: str) -> set[str]:
    payload = _codex_load_deleted_conversations(client_id, username)
    deleted = payload.get("deleted") if isinstance(payload.get("deleted"), dict) else {}
    return {str(item or "").strip() for item in deleted.keys() if str(item or "").strip()}


def _codex_is_conversation_deleted(client_id: str, username: str, conversation_id: str) -> bool:
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    return bool(conv_id and conv_id in _codex_deleted_conversation_ids(client_id, username))


def _codex_mark_conversation_deleted(client_id: str, conversation_id: str, username: str = "", task_ids: Optional[list[str]] = None) -> None:
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not conv_id:
        return
    path = _codex_deleted_conversations_path(client_id, username)
    payload = _codex_load_deleted_conversations(client_id, username)
    deleted = payload.setdefault("deleted", {})
    if not isinstance(deleted, dict):
        deleted = {}
        payload["deleted"] = deleted
    deleted[conv_id] = {
        "conversation_id": conv_id,
        "deleted_at": _codex_now(),
        "deleted_by": str(username or "")[:120],
        "task_ids": [str(item or "")[:80] for item in (task_ids or []) if str(item or "").strip()][:500],
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def _codex_normalizar_history(value: Any, limit: int = 40) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            role = "assistant" if role in {"bot", "codex", "joao"} else "user"
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "role": role,
                "text": re.sub(r"\s+", " ", text)[:2400],
                "task_id": str(item.get("task_id") or "")[:80],
                "kind": str(item.get("kind") or item.get("message_kind") or "")[:40],
            }
        )
    return normalized


def _codex_load_conversation_summary(client_id: str, username: str, conversation_id: str) -> dict[str, Any]:
    path = _codex_conversation_path(client_id, username, conversation_id)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _codex_save_conversation_summary(client_id: str, username: str, conversation_id: str, payload: dict[str, Any]) -> None:
    path = _codex_conversation_path(client_id, username, conversation_id)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)


def _codex_task_conversation_id(task: dict[str, Any]) -> str:
    return _codex_conversation_id(str(task.get("conversation_id") or task.get("thread_id") or ""), str(task.get("task_id") or ""))


def _codex_task_conversation_keys(task: dict[str, Any]) -> set[str]:
    keys = {_codex_task_conversation_id(task)}
    conversation_id = _codex_safe_id(str(task.get("conversation_id") or "").strip(), "")
    thread_id = _codex_safe_id(str(task.get("thread_id") or "").strip(), "")
    task_id = _codex_safe_id(str(task.get("task_id") or "").strip(), "")
    if conversation_id:
        keys.add(conversation_id)
    if thread_id:
        keys.add(thread_id)
        keys.add(f"thread_{thread_id}")
    if task_id:
        keys.add(task_id)
        keys.add(f"task_{task_id}")
    return {item for item in keys if item}


def _codex_task_history_messages(task: dict[str, Any]) -> list[dict[str, str]]:
    if str(task.get("status") or "") != "completed":
        return []
    if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
        return []
    messages: list[dict[str, str]] = []
    prompt = str(task.get("prompt") or "").strip()
    if prompt:
        messages.append({"role": "user", "text": prompt[:2400], "task_id": str(task.get("task_id") or "")})
    final = str(task.get("final_response") or "").strip()
    if final:
        kind = str(task.get("message_kind") or "")
        messages.append({"role": "assistant", "text": final[:3600], "task_id": str(task.get("task_id") or ""), "kind": kind})
    return messages


def _codex_recent_persisted_messages(
    client_id: str,
    username: str,
    conversation_id: str,
    exclude_task_id: str = "",
    limit: int = 40,
    generation: int = 1,
    aliases: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    username_norm = str(username or "").strip().lower()
    if _codex_is_conversation_deleted(client_id, username_norm, conversation_id):
        return []
    try:
        paths = sorted(Path(_codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime)
    except Exception:
        paths = []
    alias_ids = {
        _codex_safe_id(str(item or "").strip(), "")
        for item in (aliases or [])
        if str(item or "").strip()
    }
    canonical_id = _codex_safe_id(conversation_id)
    for path in paths[-240:]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        if str(task.get("task_id") or "") == str(exclude_task_id or ""):
            continue
        stored_id = _codex_task_stored_conversation_id(task)
        task_generation = int(task.get("conversation_generation") or 1)
        canonical_match = stored_id == canonical_id and task_generation == int(generation or 1)
        legacy_match = int(generation or 1) == 1 and stored_id in alias_ids
        if not canonical_match and not legacy_match:
            continue
        messages.extend(_codex_task_history_messages(task))
    return messages[-limit:]


def _codex_conversation_keywords(messages: list[dict[str, str]]) -> dict[str, list[str]]:
    text = "\n".join(str(item.get("text") or "") for item in messages)
    skus = sorted(set(re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,24}\b", text.upper())))[:30]
    reports = sorted(set(re.findall(r"\bcodex_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}\b", text, flags=re.I)))[:20]
    lojas = []
    for match in re.findall(r"\b(JK\s*Pecas|JK\s*Peças|Mercado Livre|Bling|Full)\b", text, flags=re.I):
        label = re.sub(r"\s+", " ", match).strip()
        if label and label not in lojas:
            lojas.append(label)
    return {"skus": skus, "reports": reports, "lojas": lojas[:20]}


def _codex_compact_summary(existing_summary: str, older_messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    if existing_summary:
        lines.append(str(existing_summary).strip()[:CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT])
    keywords = _codex_conversation_keywords(older_messages)
    lines.append("Resumo operacional compactado da conversa atual:")
    if keywords.get("lojas"):
        lines.append("Lojas/contas citadas: " + ", ".join(keywords["lojas"]))
    if keywords.get("skus"):
        lines.append("SKUs/codigos citados: " + ", ".join(keywords["skus"][:20]))
    if keywords.get("reports"):
        lines.append("Relatorios citados: " + ", ".join(keywords["reports"][:12]))
    for item in older_messages[-60:]:
        role = "Usuario" if item.get("role") == "user" else BLACK_JHON_DISPLAY_NAME
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if text:
            lines.append(f"- {role}: {text[:520]}")
    summary = "\n".join(line for line in lines if line).strip()
    if len(summary) > CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT:
        summary = summary[-CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT:]
    return summary


def _codex_prepare_conversation_context(task: dict[str, Any]) -> dict[str, Any]:
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    stored = _codex_load_conversation_summary(client_id, username, conversation_id)
    summary = str(stored.get("summary") or "").strip()
    generation = int(task.get("conversation_generation") or stored.get("generation") or 1)
    aliases = list(stored.get("legacy_conversation_ids") or []) if generation == 1 else []
    # O browser nao e fonte de autoridade do contexto. A memoria vem apenas de
    # tarefas persistidas desta conversa/geracao no servidor.
    browser_history: list[dict[str, str]] = []
    persisted = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        60,
        generation,
        aliases,
    )
    recent_messages = (persisted + browser_history)[-max(4, CODEX_CONVERSATION_RECENT_MESSAGES):]
    raw_context = json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)
    estimated_before = _codex_estimar_tokens(raw_context)
    compacted = False
    if estimated_before > CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT:
        combined = persisted + browser_history
        keep = max(4, CODEX_CONVERSATION_RECENT_MESSAGES)
        older = combined[:-keep]
        recent_messages = combined[-keep:]
        summary = _codex_compact_summary(summary, older)
        compacted = True
        payload = {
            **stored,
            "conversation_id": conversation_id,
            "client_id": client_id,
            "created_by": username,
            "summary": summary,
            "compacted_until": _codex_now(),
            "summary_updated_at": _codex_now(),
            "estimated_tokens_before": estimated_before,
            "estimated_tokens_after": _codex_estimar_tokens(json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)),
        }
        _codex_save_conversation_summary(client_id, username, conversation_id, payload)
        stored = payload
    return {
        "conversation_id": conversation_id,
        "summary": summary,
        "recent_messages": recent_messages,
        "history_chars": len(raw_context),
        "estimated_history_tokens": estimated_before,
        "compacted": compacted,
        "compaction": {
            "compacted": compacted,
            "compacted_until": stored.get("compacted_until") or "",
            "summary_updated_at": stored.get("summary_updated_at") or "",
            "estimated_tokens_before": stored.get("estimated_tokens_before") or estimated_before,
            "estimated_tokens_after": stored.get("estimated_tokens_after") or estimated_before,
        },
    }


def _codex_update_conversation_memory(task_id: str) -> dict[str, Any]:
    task = _codex_load_task(task_id)
    if not task:
        return {}
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    stored = _codex_load_conversation_summary(client_id, username, conversation_id)
    summary = str(stored.get("summary") or "").strip()
    generation = int(task.get("conversation_generation") or stored.get("generation") or 1)
    aliases = list(stored.get("legacy_conversation_ids") or []) if generation == 1 else []
    messages = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        160,
        generation,
        aliases,
    )
    messages.extend(_codex_task_history_messages(task))
    keep = max(4, CODEX_CONVERSATION_RECENT_MESSAGES)
    raw = json.dumps({"summary": summary, "messages": messages}, ensure_ascii=False, default=str)
    estimated_before = _codex_estimar_tokens(raw)
    compacted = estimated_before > CODEX_CONVERSATION_COMPACT_TOKEN_LIMIT or bool(summary)
    if compacted and len(messages) > keep:
        summary = _codex_compact_summary(summary, messages[:-keep])
    recent_messages = messages[-keep:]
    payload = {
        **stored,
        "conversation_id": conversation_id,
        "client_id": client_id,
        "created_by": username,
        "summary": summary,
        "recent_messages": recent_messages,
        "compacted_until": _codex_now() if compacted else str(stored.get("compacted_until") or ""),
        "summary_updated_at": _codex_now(),
        "estimated_tokens_before": estimated_before,
        "estimated_tokens_after": _codex_estimar_tokens(json.dumps({"summary": summary, "recent_messages": recent_messages}, ensure_ascii=False, default=str)),
    }
    _codex_save_conversation_summary(client_id, username, conversation_id, payload)
    return payload


def _codex_delete_conversation_memory_if_unused(task: dict[str, Any]) -> None:
    client_id = str(task.get("client_id") or "default")
    username = str(task.get("created_by") or "").strip().lower()
    conversation_id = _codex_task_conversation_id(task)
    current_task_id = str(task.get("task_id") or "")
    try:
        for path in Path(_codex_info_dir()).glob("*.json"):
            with path.open("r", encoding="utf-8") as fh:
                other = json.load(fh)
            if not isinstance(other, dict):
                continue
            if str(other.get("task_id") or "") == current_task_id:
                continue
            if (
                str(other.get("client_id") or "") == client_id
                and str(other.get("created_by") or "").strip().lower() == username
                and _codex_safe_id(conversation_id) in _codex_task_conversation_keys(other)
            ):
                return
        summary_path = _codex_conversation_path(client_id, username, conversation_id)
        if summary_path.exists():
            summary_path.unlink()
    except Exception:
        return


def _codex_assistant_reports_dir(client_id: str) -> Path:
    return Path(_codex_base_info_dir()) / _codex_safe_id(client_id) / "codex_assistant" / "reports"


def _codex_backfill_assistant_report_tasks(client_id: str, username: str = "", limit: int = 30) -> None:
    max_reports = max(1, min(100, int(limit or 30)))
    username_norm = str(username or "").strip().lower()

    def backfill_report(report: dict[str, Any], fallback_report_id: str = "") -> None:
        if not isinstance(report, dict):
            return
        report_owner = str(
            report.get("created_by")
            or report.get("username")
            or report.get("owner")
            or ""
        ).strip().lower()
        # Relatorio legado sem dono nao pode ser apropriado pelo primeiro
        # usuario que abrir o historico.
        if not report_owner or report_owner != username_norm:
            return
        report_id = str(report.get("report_id") or fallback_report_id).strip()
        conversation_id = str(report.get("conversation_id") or report.get("thread_id") or report_id).strip()
        if _codex_is_conversation_deleted(client_id, username, conversation_id) or _codex_is_conversation_deleted(client_id, username, report_id):
            return
        if not report_id or os.path.exists(_codex_task_path(report_id)):
            return
        report["report_id"] = report_id
        codex_register_report_history(
            client_id=client_id,
            username=report_owner,
            prompt=str(report.get("prompt") or report.get("title") or f"Relatorio {BLACK_JHON_DISPLAY_NAME}"),
            report=report,
            thread_id=str(report.get("thread_id") or ""),
            conversation_id=conversation_id,
            screen_context=report.get("screen_context") if isinstance(report.get("screen_context"), dict) else {},
        )

    try:
        reports = codex_assistant_storage.codex_assistant_reports_list(_codex_base_info_dir(), client_id, max_reports)
        for report in reports:
            backfill_report(report)
    except Exception:
        pass

    reports_dir = _codex_assistant_reports_dir(client_id)
    if not reports_dir.exists() or not reports_dir.is_dir():
        return
    try:
        report_dirs = sorted(
            [item for item in reports_dir.iterdir() if item.is_dir()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:max_reports]
    except Exception:
        return

    for report_dir in report_dirs:
        metadata_path = report_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            with metadata_path.open("r", encoding="utf-8") as fh:
                report = json.load(fh)
            if not isinstance(report, dict):
                continue
            backfill_report(report, report_dir.name)
        except Exception:
            continue


def _codex_persist_task(task: dict[str, Any]) -> None:
    try:
        payload = _codex_public_task(task)
        # Metadados internos necessarios para retomar uma tarefa apos reinicio.
        payload["permissions"] = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
        payload["trusted_model_config"] = bool(task.get("trusted_model_config"))
        payload["thread_resume_retried"] = bool(task.get("thread_resume_retried"))
        # Artefatos visuais do WhatsApp sao privados: precisam sobreviver a um
        # reinicio para a ponte concluir o envio, mas nunca saem em
        # ``_codex_public_task`` nem chegam ao frontend.
        payload["whatsapp_artifacts"] = [
            dict(item)
            for item in (task.get("whatsapp_artifacts") or [])
            if isinstance(item, dict)
        ][:4]
        payload["whatsapp_chart_expected"] = bool(task.get("whatsapp_chart_expected"))
        payload["whatsapp_chart_status"] = str(task.get("whatsapp_chart_status") or "")[:80]
        payload["whatsapp_chart_error"] = str(task.get("whatsapp_chart_error") or "")[:500]
        with open(_codex_task_path(task.get("task_id")), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        try:
            logger.warning("[CODEX CONSOLE] Falha ao persistir tarefa: %s", exc)
        except Exception:
            pass


def _codex_load_task(task_id: str) -> Optional[dict[str, Any]]:
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
    if task:
        return task
    path = _codex_task_path(task_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            task = json.load(fh)
        if isinstance(task, dict):
            with CODEX_TASKS_LOCK:
                CODEX_TASKS[task_id] = task
            return task
    except Exception:
        return None
    return None


def _codex_owned_persisted_tasks(client_id: str, username: str) -> list[dict[str, Any]]:
    username_norm = str(username or "").strip().lower()
    tasks: dict[str, dict[str, Any]] = {}
    try:
        paths = list(Path(_codex_info_dir()).glob("*.json"))
    except Exception:
        paths = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "") != str(client_id or ""):
            continue
        if str(task.get("created_by") or "").strip().lower() != username_norm:
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if task_id:
            tasks[task_id] = task
    with CODEX_TASKS_LOCK:
        for task_id, task in CODEX_TASKS.items():
            if (
                isinstance(task, dict)
                and str(task.get("client_id") or "") == str(client_id or "")
                and str(task.get("created_by") or "").strip().lower() == username_norm
            ):
                tasks[str(task_id)] = task
    return list(tasks.values())


def _codex_whatsapp_phone_display(phone: str) -> str:
    digits = _codex_normalize_phone(phone)
    if not digits:
        return "Telefone indisponivel"
    if digits.startswith("55") and len(digits) == 13:
        return f"+55 ({digits[2:4]}) {digits[4:9]}-{digits[9:]}"
    if digits.startswith("55") and len(digits) == 12:
        return f"+55 ({digits[2:4]}) {digits[4:8]}-{digits[8:]}"
    return f"+{digits}"


def _codex_timestamp_from_seconds(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _codex_registered_whatsapp_bindings(sessao: dict[str, Any]) -> list[dict[str, Any]]:
    """Consulta os vinculos ativos para exibir tambem telefones ainda sem tarefa."""
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    if not username:
        return []
    try:
        # Importacao local evita o ciclo: whatsapp_bridge usa este modulo para
        # criar e acompanhar as tarefas recebidas do gateway.
        from backend.services import whatsapp_bridge

        config = whatsapp_bridge._load_config()
        worker = whatsapp_bridge._worker_health(config)
        raw_bindings = worker.get("bindings") if isinstance(worker.get("bindings"), list) else []
    except Exception:
        return []

    bindings: list[dict[str, Any]] = []
    for item in raw_bindings:
        if not isinstance(item, dict):
            continue
        binding_client_id = str(item.get("client_id") or "")
        binding_username = str(item.get("username") or "").strip().lower()
        if binding_client_id != client_id or binding_username != username:
            continue
        phone = _codex_normalize_phone(item.get("phone_number") or item.get("wa_id"))
        if not phone:
            continue
        subject_id = str(item.get("subject_id") or "").strip()
        try:
            settings = whatsapp_bridge._phone_notification_settings(
                config,
                subject_id,
                client_id=binding_client_id,
                username=binding_username,
            )
        except Exception:
            settings = {}
        bindings.append(
            {
                "conversation_id": _codex_canonical_conversation_id(
                    client_id,
                    username,
                    channel="whatsapp",
                    phone=phone,
                ),
                "phone": phone,
                "label": str(settings.get("label") or "").strip()[:60],
                "registered_at": _codex_timestamp_from_seconds(item.get("created_at")),
                "last_inbound_at": _codex_timestamp_from_seconds(item.get("last_inbound_at")),
            }
        )
    return bindings


def _codex_whatsapp_history_records(sessao: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna somente tarefas WhatsApp pertencentes ao usuario autenticado."""
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    deleted_ids = _codex_deleted_conversation_ids(client_id, username)
    records: list[dict[str, Any]] = []
    for task in _codex_owned_persisted_tasks(client_id, username):
        if _codex_task_channel(task) != "whatsapp":
            continue
        if str(task.get("message_kind") or "") == "report" or task.get("memory_excluded") is True:
            continue
        phone = _codex_task_phone(task)
        if not phone:
            # Nunca reunir historico sem telefone em um balde compartilhado.
            continue
        conversation_id = _codex_canonical_conversation_id(
            client_id,
            username,
            channel="whatsapp",
            phone=phone,
        )
        if conversation_id in deleted_ids or (_codex_task_conversation_keys(task) & deleted_ids):
            continue
        records.append(
            {
                "task": task,
                "conversation_id": conversation_id,
                "phone": phone,
                "activity_at": str(
                    task.get("completed_at")
                    or task.get("started_at")
                    or task.get("created_at")
                    or ""
                ),
            }
        )
    records.sort(key=lambda item: str(item.get("activity_at") or ""), reverse=True)
    return records


def _codex_whatsapp_completed_exchange(task: dict[str, Any]) -> bool:
    return bool(
        str(task.get("status") or "") == "completed"
        and str(task.get("prompt") or "").strip()
        and str(task.get("final_response") or "").strip()
        and str(task.get("message_kind") or "") != "report"
        and task.get("memory_excluded") is not True
    )


def _codex_whatsapp_history_message_preview(task: dict[str, Any]) -> dict[str, Any]:
    prompt = str(task.get("prompt") or "").strip()
    response = str(task.get("final_response") or "").strip()
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    interaction_type = "call" if str(metadata.get("interaction_type") or metadata.get("source") or "").lower() in {"call", "whatsapp_call"} else "message"
    prompt_limit = 4000
    response_limit = 12000
    return {
        "task_id": str(task.get("task_id") or ""),
        "status": str(task.get("status") or ""),
        "model": str(task.get("model") or ""),
        "created_at": str(task.get("created_at") or ""),
        "completed_at": str(task.get("completed_at") or ""),
        "prompt": prompt[:prompt_limit],
        "prompt_truncated": len(prompt) > prompt_limit,
        "response": response[:response_limit],
        "response_truncated": len(response) > response_limit,
        "interaction_type": interaction_type,
        "call_id": str(metadata.get("call_id") or "")[:200],
        "duration_seconds": max(0, int(metadata.get("duration_seconds") or 0)),
    }


def _codex_update_task(task_id: str, **updates: Any) -> dict[str, Any]:
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
        if not task:
            raise KeyError(task_id)
        task.update(updates)
        _codex_persist_task(task)
        return task


def _codex_log(task: dict[str, Any], text: str, kind: str = "status") -> None:
    logs = task.setdefault("logs", [])
    logs.append({"at": _codex_now(), "text": str(text or "")[:2000], "kind": str(kind or "status")[:40]})
    task["logs"] = logs[-240:]
    _codex_persist_task(task)


def _codex_normalizar_sandbox(value: str) -> str:
    sandbox = str(value or "read_only").strip().lower()
    if sandbox not in CODEX_SANDBOXES:
        raise HTTPException(status_code=400, detail="Sandbox invalido para Codex.")
    return sandbox


def _codex_whatsapp_query_policy(origin: Any, channel_metadata: Any) -> dict[str, Any]:
    if str(origin or "").strip().lower() != "whatsapp" or not isinstance(channel_metadata, dict):
        return {}
    raw = channel_metadata.get("query_policy")
    if not isinstance(raw, dict) or str(raw.get("mode") or "").strip().lower() != "query_only":
        return {}
    allowed_domains = {"vendas", "anuncios_ml", "estoque", "mercado_full"}
    domains = [
        str(item or "").strip().lower()
        for item in (raw.get("domains") or [])
        if str(item or "").strip().lower() in allowed_domains
    ]
    if not domains:
        return {}
    policy: dict[str, Any] = {
        "mode": "query_only",
        "domains": list(dict.fromkeys(domains)),
        "read_only": True,
        "deny_approval": True,
        "store_required": bool(raw.get("store_required")),
        "store": _codex_clean_text(str(raw.get("store") or ""), 160),
    }
    raw_store_mode = str(raw.get("store_mode") or "").strip().lower()
    raw_stores = [
        _codex_clean_text(str(item or ""), 160)
        for item in (raw.get("stores") or [])
        if str(item or "").strip()
    ]
    raw_stores = list(dict.fromkeys(item for item in raw_stores if item))[:20]
    if raw_store_mode == "all" and raw_stores:
        policy["store_mode"] = "all"
        policy["stores"] = raw_stores
    source_raw = raw.get("source_policy") if isinstance(raw.get("source_policy"), dict) else {}
    allowed_source_tools = {
        "bling_stock_balances",
        "mercado_livre_listing",
        "mercado_livre_orders",
        "mercado_livre_returns",
        "mercado_livre_full_stock",
    }
    required_tools = [
        str(item or "").strip()
        for item in (source_raw.get("required_tools") or [])
        if str(item or "").strip() in allowed_source_tools
    ]
    forbidden_tools = [
        str(item or "").strip()
        for item in (source_raw.get("forbidden_tools") or [])
        if str(item or "").strip() in {
            "bling_stock_balances", "bling_deposits", "stock_data", "product_data",
            "bling_sales_orders", "mercado_livre_listing", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_full_stock",
        }
    ]
    if required_tools:
        policy["source_policy"] = {
            "version": _codex_clean_text(str(source_raw.get("version") or ""), 80),
            "intent": _codex_clean_text(str(source_raw.get("intent") or ""), 240),
            "required_tools": list(dict.fromkeys(required_tools)),
            "forbidden_tools": list(dict.fromkeys(forbidden_tools)),
            "preferred_providers": [
                str(item or "").strip()
                for item in (source_raw.get("preferred_providers") or [])
                if str(item or "").strip() in {"bling", "mercado_livre"}
            ],
            "force_refresh": bool(source_raw.get("force_refresh")),
            "include_listing_details": bool(source_raw.get("include_listing_details")),
            "sum_requested": bool(source_raw.get("sum_requested")),
            "full_exclusive": bool(source_raw.get("full_exclusive")),
            "full_stock_provider": "mercado_livre_api_only",
            "bling_stock_scope": "exclude_full",
            "aggregation_policy": _codex_clean_text(str(source_raw.get("aggregation_policy") or ""), 120),
        }
    if raw.get("base_request"):
        policy["base_request"] = _codex_clean_text(str(raw.get("base_request") or ""), 2000)
    if str(raw.get("pagination") or "") == "next":
        policy["pagination"] = "next"
    if raw.get("inherited") is True:
        policy["inherited"] = True
        policy["base_request"] = _codex_clean_text(str(raw.get("base_request") or ""), 2000)
    if raw.get("fresh") is True or raw.get("bypass_cache") is True:
        policy["fresh"] = True
        policy["bypass_cache"] = True
    if raw.get("report_mode") is True and "mercado_livre_orders" in required_tools:
        policy["report_mode"] = True
    try:
        if raw.get("offset") is not None:
            policy["offset"] = max(0, min(int(raw.get("offset")), 100000))
    except Exception:
        pass
    try:
        if raw.get("limit") is not None:
            maximum = 20000 if policy.get("report_mode") is True else 100
            policy["limit"] = max(1, min(int(raw.get("limit")), maximum))
    except Exception:
        pass
    return policy


def _codex_task_whatsapp_query_only(task: Any) -> bool:
    if not isinstance(task, dict) or str(task.get("origin") or "").strip().lower() != "whatsapp":
        return False
    if task.get("whatsapp_query_only") is True:
        return True
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return bool(_codex_whatsapp_query_policy("whatsapp", metadata))


def _codex_clean_text(value: Optional[str], limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _codex_texto_sem_acentos(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _codex_prompt_pede_alteracao(prompt: str) -> bool:
    text = _codex_texto_sem_acentos(prompt)
    if re.search(r"\b(responda|responder|envie|enviar|mande|mandar|aprove|aprovar)\b", text) and re.search(
        r"\b(pergunta|pos venda|pos-venda|resposta|mensagem (?:ao|para o) comprador|mercado livre|mercadolivre)\b",
        text,
    ):
        return True
    if re.search(r"\b(execute|executar|rode|rodar|cancele|cancelar|pause|pausar|publique|publicar)\b", text):
        return True
    patterns = (
        r"\b(altere|alterar|alteracao|ajuste|ajustar|corrija|corrigir|correcao)\b",
        r"\b(implemente|implementar|crie|criar|adicione|adicionar|inclua|incluir)\b",
        r"\b(edite|editar|modifique|modificar|troque|trocar|substitua|substituir)\b",
        r"\b(remova|remover|apague|apagar|delete|deletar|exclua|excluir)\b",
        r"\b(salve|salvar|grave|gravar|atualize|atualizar|sincronize|sincronizar)\b",
        r"\b(instale|instalar|publique|publicar|gere arquivo|gerar arquivo)\b",
        r"\b(change|edit|fix|implement|create|add|remove|delete|update|save|write|modify|patch)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _codex_normalizar_screen_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    allowed_keys = {
        "title",
        "url",
        "url_completa",
        "pathname",
        "modulo_atual",
        "periodo",
        "filtros",
        "cards",
        "table_headers",
        "table_rows",
        "table",
        "listas",
        "visible_text",
        "controls",
        "selection",
        "viewport",
    }

    def trim(obj: Any, depth: int = 0) -> Any:
        if depth > 4:
            return ""
        if isinstance(obj, str):
            return obj.strip()[:4000]
        if isinstance(obj, (int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, list):
            return [trim(item, depth + 1) for item in obj[:60]]
        if isinstance(obj, dict):
            clean: dict[str, Any] = {}
            for key, item in list(obj.items())[:80]:
                clean[str(key)[:80]] = trim(item, depth + 1)
            return clean
        return str(obj)[:1000]

    screen_context = {
        key: trim(value.get(key))
        for key in allowed_keys
        if key in value and value.get(key) not in (None, "", [], {})
    }
    raw = json.dumps(screen_context, ensure_ascii=False)
    if len(raw) <= 18000:
        return screen_context
    return {
        "truncated": True,
        "raw_preview": raw[:18000],
    }


def _codex_screen_context_json(screen_context: Any, limit: int = 18000) -> str:
    if not isinstance(screen_context, dict) or not screen_context:
        return ""
    text = json.dumps(screen_context, ensure_ascii=False, indent=2)
    return text[:limit]


def _codex_app_data_context(
    prompt: str,
    screen_context: Any,
    client_id: str,
    permissions: Any = None,
) -> dict[str, Any]:
    if not _codex_bool_env("JK_CODEX_APP_DATA_CONTEXT_ENABLED", True):
        return {}
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("full") is not True:
        return {
            "enabled": True,
            "permission_filtered": True,
            "tool_results_count": 0,
            "tool_context": "",
        }
    mensagem = str(prompt or "").strip()
    tenant = str(client_id or "").strip()
    if not mensagem or not tenant:
        return {}
    try:
        from backend.services import codex_assistant

        return codex_assistant.codex_assistant_collect_context(
            mensagem,
            screen_context if isinstance(screen_context, dict) else {},
            tenant,
            mode="chat",
        )
    except Exception:
        pass
    try:
        from backend.schemas.ia import IAChatRequest
        from backend.services import ia as ia_service

        contexto = screen_context if isinstance(screen_context, dict) else {}
        page = (
            str(contexto.get("modulo_atual") or "").strip()
            or str(contexto.get("pathname") or "").strip().strip("/").split("/", 1)[0]
            or str(contexto.get("title") or "").strip()
            or "codex"
        )
        payload = IAChatRequest(
            message=mensagem,
            page=page,
            modulo=page,
            context=dict(contexto),
            history=payload.history if isinstance(payload.history, list) else [],
        )
        tool_results = ia_service._ia_chat_executar_funcoes(payload, tenant)
        if not tool_results:
            return {"enabled": True, "tool_results_count": 0, "tool_context": ""}
        payload.tool_results = tool_results
        tool_context = ia_service._ia_chat_contexto_funcoes(payload, tenant)
        tool_json = json.dumps(tool_results, ensure_ascii=False, default=str)
        return {
            "enabled": True,
            "tool_results_count": len(tool_results),
            "tool_context": str(tool_context or "")[:24000],
            "tool_results_preview": tool_json[:24000],
        }
    except Exception as exc:
        return {
            "enabled": True,
            "tool_results_count": 0,
            "tool_context": "",
            "error": str(exc)[:600],
        }


def _codex_estimar_tokens(text: str) -> int:
    return max(0, ceil(len(str(text or "")) / 4))


def _codex_context_stats(prompt: str, screen_context: Any, app_data_context: Any = None) -> dict[str, Any]:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    if isinstance(app_data_context, dict):
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "")
    run_prompt = _codex_prompt_com_contexto_tela(prompt, screen_context, app_data_context)
    visible_text = ""
    controls_count = 0
    table_rows_count = 0
    filtros_count = 0
    listas_count = 0
    app_tool_results_count = 0
    if isinstance(screen_context, dict):
        visible_text = str(screen_context.get("visible_text") or "")
        controls_count = len(screen_context.get("controls") or []) if isinstance(screen_context.get("controls"), list) else 0
        table_rows_count = len(screen_context.get("table_rows") or []) if isinstance(screen_context.get("table_rows"), list) else 0
        filtros_count = len(screen_context.get("filtros") or []) if isinstance(screen_context.get("filtros"), list) else 0
        listas_count = len(screen_context.get("listas") or []) if isinstance(screen_context.get("listas"), list) else 0
    if isinstance(app_data_context, dict):
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    return {
        "prompt_chars": len(str(prompt or "")),
        "screen_context_chars": len(context_json),
        "screen_context_bytes": len(context_json.encode("utf-8")) if context_json else 0,
        "app_data_context_chars": len(app_data_text),
        "app_data_context_bytes": len(app_data_text.encode("utf-8")) if app_data_text else 0,
        "app_tool_results_count": app_tool_results_count,
        "visible_text_chars": len(visible_text),
        "controls_count": controls_count,
        "table_rows_count": table_rows_count,
        "filtros_count": filtros_count,
        "listas_count": listas_count,
        "run_prompt_chars": len(run_prompt),
        "estimated_input_tokens": _codex_estimar_tokens(run_prompt),
        "estimated_context_tokens": _codex_estimar_tokens(context_json),
        "estimated_app_data_tokens": _codex_estimar_tokens(app_data_text),
    }


def _codex_prompt_com_contexto_tela(prompt: str, screen_context: Any, app_data_context: Any = None) -> str:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    app_data_error = ""
    app_tool_results_count = 0
    app_data_enabled = False
    if isinstance(app_data_context, dict):
        app_data_enabled = bool(app_data_context.get("enabled"))
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "").strip()
        app_data_error = str(app_data_context.get("error") or "").strip()
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    parts: list[str] = []
    if context_json:
        parts.append(
            "Contexto da tela atual do JK Sistema, capturado no momento em que o usuario enviou a mensagem:\n"
            f"{context_json}\n\n"
            "Use esse contexto como a tela que o usuario esta vendo agora. "
            "Se o usuario mencionar 'a pergunta', 'essa pergunta', 'a tela', 'isso' ou algo semelhante, "
            "responda usando os dados visiveis nesse contexto. "
            "Quando houver varias perguntas visiveis, priorize a linha selecionada; se nao houver selecao, priorize a primeira pergunta nao respondida ou a primeira pergunta visivel."
        )
    if app_data_text:
        parts.append(
            "Resultados de consultas internas read-only do JK Sistema, executadas pelo backend com dados reais antes do Codex responder:\n"
            f"{app_data_text}\n\n"
            "Para perguntas de negocio, vendas, estoque, produtos, Mercado Livre, Bling, devolucoes, integracoes ou relatorios, "
            "priorize estes resultados estruturados em vez de inferir pela tela. "
            "Se os resultados nao cobrirem a pergunta, diga exatamente qual dado esta faltando."
        )
    elif app_data_enabled and app_tool_results_count == 0:
        parts.append(
            "As ferramentas internas de leitura do JK Sistema nao retornaram resultados estruturados para esta mensagem. "
            "Use o contexto da tela e, se necessario, explique quais dados faltam para uma resposta exata."
        )
    if app_data_error:
        parts.append(f"Aviso: houve falha ao preparar consultas internas read-only: {app_data_error}")
    if not parts:
        return prompt
    return (
        "\n\n".join(parts)
        + "\n\n"
        "Mensagem do usuario:\n"
        f"{prompt}"
    )


def _codex_context_stats_from_prompt(
    prompt: str,
    run_prompt: str,
    screen_context: Any,
    app_data_context: Any = None,
    conversation_context: Any = None,
) -> dict[str, Any]:
    context_json = _codex_screen_context_json(screen_context)
    app_data_text = ""
    app_tool_results_count = 0
    if isinstance(app_data_context, dict):
        app_data_text = str(app_data_context.get("tool_context") or app_data_context.get("tool_results_preview") or "")
        app_tool_results_count = int(app_data_context.get("tool_results_count") or 0)
    visible_text = ""
    controls_count = 0
    table_rows_count = 0
    filtros_count = 0
    listas_count = 0
    if isinstance(screen_context, dict):
        visible_text = str(screen_context.get("visible_text") or "")
        controls_count = len(screen_context.get("controls") or []) if isinstance(screen_context.get("controls"), list) else 0
        table_rows_count = len(screen_context.get("table_rows") or []) if isinstance(screen_context.get("table_rows"), list) else 0
        filtros_count = len(screen_context.get("filtros") or []) if isinstance(screen_context.get("filtros"), list) else 0
        listas_count = len(screen_context.get("listas") or []) if isinstance(screen_context.get("listas"), list) else 0
    estimated = _codex_estimar_tokens(run_prompt)
    history_chars = 0
    estimated_history_tokens = 0
    conversation_id = ""
    conversation_compacted = False
    if isinstance(conversation_context, dict):
        history_chars = int(conversation_context.get("history_chars") or 0)
        estimated_history_tokens = int(conversation_context.get("estimated_history_tokens") or 0)
        conversation_id = str(conversation_context.get("conversation_id") or "")
        conversation_compacted = bool(conversation_context.get("compacted"))
    return {
        "prompt_chars": len(str(prompt or "")),
        "screen_context_chars": len(context_json),
        "screen_context_bytes": len(context_json.encode("utf-8")) if context_json else 0,
        "app_data_context_chars": len(app_data_text),
        "app_data_context_bytes": len(app_data_text.encode("utf-8")) if app_data_text else 0,
        "app_tool_results_count": app_tool_results_count,
        "visible_text_chars": len(visible_text),
        "controls_count": controls_count,
        "table_rows_count": table_rows_count,
        "filtros_count": filtros_count,
        "listas_count": listas_count,
        "run_prompt_chars": len(run_prompt),
        "estimated_input_tokens": estimated,
        "estimated_context_tokens": _codex_estimar_tokens(context_json),
        "estimated_app_data_tokens": _codex_estimar_tokens(app_data_text),
        "history_chars": history_chars,
        "estimated_history_tokens": estimated_history_tokens,
        "conversation_id": conversation_id,
        "conversation_compacted": conversation_compacted,
        "agent_mode": True,
        "context_soft_limit": CODEX_AGENT_INPUT_TOKEN_SOFT_LIMIT,
        "context_target": CODEX_AGENT_INPUT_TOKEN_TARGET,
    }


def _codex_agent_json(value: Any, limit: int = 60000, indent: Optional[int] = 2) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, indent=indent)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)] + "\n... [conteudo compactado]"


def _codex_agent_screen_summary(screen_context: Any) -> dict[str, Any]:
    if not isinstance(screen_context, dict) or not screen_context:
        return {}

    def trim(value: Any, depth: int = 0) -> Any:
        if depth > 3:
            return str(value)[:160]
        if isinstance(value, str):
            return value.strip()[:1600]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [trim(item, depth + 1) for item in value[:20]]
        if isinstance(value, dict):
            return {str(key)[:80]: trim(item, depth + 1) for key, item in list(value.items())[:40]}
        return str(value)[:600]

    summary = {
        "title": screen_context.get("title") or "",
        "pathname": screen_context.get("pathname") or "",
        "modulo_atual": screen_context.get("modulo_atual") or "",
        "periodo": trim(screen_context.get("periodo")),
        "filtros": trim(screen_context.get("filtros")),
        "selection": trim(screen_context.get("selection")),
        "cards": trim(screen_context.get("cards")),
        "table_headers": trim(screen_context.get("table_headers")),
        "table_rows_preview": trim(screen_context.get("table_rows")),
        "controls_preview": trim(screen_context.get("controls")),
        "visible_text_preview": trim(screen_context.get("visible_text")),
    }
    summary = {key: value for key, value in summary.items() if value not in (None, "", [], {})}
    raw = json.dumps(summary, ensure_ascii=False, default=str)
    if len(raw) <= CODEX_AGENT_SCREEN_CONTEXT_LIMIT:
        return summary
    return {
        "truncated": True,
        "title": summary.get("title") or "",
        "pathname": summary.get("pathname") or "",
        "modulo_atual": summary.get("modulo_atual") or "",
        "preview": raw[:CODEX_AGENT_SCREEN_CONTEXT_LIMIT],
    }


def _codex_agent_source_policy_from_screen(screen_context: Any) -> dict[str, Any]:
    if not isinstance(screen_context, dict):
        return {}
    selection = screen_context.get("selection") if isinstance(screen_context.get("selection"), dict) else {}
    query_policy = selection.get("query_policy") if isinstance(selection.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    return dict(source_policy)


def _codex_agent_tool_catalog(
    permissions: Any = None,
    *,
    read_only_only: bool = False,
    source_policy: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    try:
        from backend.services import codex_assistant

        tools = codex_assistant._assistant_tools_public(permissions)
    except Exception:
        tools = []
    source_policy = source_policy if isinstance(source_policy, dict) else {}
    forbidden_tools = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    compact: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if read_only_only and (
            tool.get("read_only") is not True
            or str(tool.get("id") or "") in {"program_action_match", "operational_dispatcher"}
        ):
            continue
        if str(tool.get("id") or "") in forbidden_tools:
            continue
        compact.append(
            {
                "id": tool.get("id") or "",
                "module": tool.get("module") or "",
                "description": str(tool.get("description") or "")[:260],
                "input_schema": tool.get("input_schema") or {},
                "external": bool(tool.get("external")),
                "fallbacks": list(tool.get("fallbacks") or [])[:6],
                "read_only": bool(tool.get("read_only", True)),
            }
        )
    return compact


def _codex_agent_capability_catalog(
    client_id: str = "",
    permissions: Any = None,
    *,
    read_only_only: bool = False,
) -> dict[str, Any]:
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("full") is not True:
        return {
            "version": "permission-filtered",
            "total_capabilities": 0,
            "modules": [],
            "capabilities": [],
        }
    try:
        return codex_capabilities.compact_capability_catalog(
            client_id=str(client_id or ""),
            limit=80,
            read_only_only=read_only_only,
        )
    except Exception as exc:
        return {
            "version": "unavailable",
            "total_capabilities": 0,
            "modules": [],
            "error": str(exc),
        }


def _codex_agent_is_report_request(prompt: str) -> bool:
    text = _codex_texto_sem_acentos(prompt)
    return bool(re.search(r"\b(relatorio|analise completa|diagnostico|ultimos?\s+\d+\s+dias?)\b", text))


def _codex_agent_initial_prompt(
    prompt: str,
    screen_context: Any,
    conversation_context: Any,
    client_id: str,
    permissions: Any,
    sandbox: str,
    model: str,
    reasoning_effort: str,
    speed: str,
    approval_profile: str,
    external_safe_mode: bool = False,
    whatsapp_full_access: bool = False,
    native_mcp: bool = False,
) -> str:
    source_policy = _codex_agent_source_policy_from_screen(screen_context)
    catalog = _codex_agent_tool_catalog(
        permissions,
        read_only_only=external_safe_mode,
        source_policy=source_policy,
    )
    capabilities = _codex_agent_capability_catalog(
        client_id,
        permissions,
        read_only_only=external_safe_mode,
    )
    screen_summary = _codex_agent_screen_summary(screen_context)
    guidance_items = codex_agent_runtime.resolve_guidance(
        _codex_base_info_dir(),
        str(client_id or "default"),
        context=_codex_agent_guidance_context(prompt, screen_context),
    )
    guidance_text = codex_agent_runtime.guidance_prompt(guidance_items)
    conversation_context = conversation_context if isinstance(conversation_context, dict) else {}
    conversation_summary = str(conversation_context.get("summary") or "").strip()
    recent_messages = conversation_context.get("recent_messages") if isinstance(conversation_context.get("recent_messages"), list) else []
    report_mode = _codex_agent_is_report_request(prompt)
    max_cycles = _codex_int_env(
        "JK_CODEX_AGENT_REPORT_MAX_CYCLES" if report_mode else "JK_CODEX_AGENT_MAX_CYCLES",
        CODEX_AGENT_REPORT_MAX_CYCLES if report_mode else CODEX_AGENT_MAX_CYCLES,
        1,
        20,
    )
    max_calls = _codex_int_env("JK_CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE", CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE, 1, 10)
    memory_parts: list[str] = []
    operational_memory: dict[str, Any] = {}
    if isinstance(permissions, dict) and permissions.get("full") is True:
        try:
            operational_memory = codex_operational_memory.compact_context(
                client_id=str(client_id or "default"),
                message=str(prompt or ""),
                limit_chars=6000,
            )
        except Exception:
            operational_memory = {}
    operational_summary = str((operational_memory or {}).get("summary") or "").strip()
    if operational_summary:
        memory_parts.append(
            f"Memoria operacional persistida do {BLACK_JHON_DISPLAY_NAME}:\n"
            f"{operational_summary[:6000]}"
        )
    if conversation_summary:
        memory_parts.append(
            "Memoria compactada da conversa atual:\n"
            f"{conversation_summary[:CODEX_CONVERSATION_SUMMARY_CHAR_LIMIT]}"
        )
    if recent_messages:
        memory_parts.append(
            "Ultimas mensagens relevantes da conversa atual:\n"
            f"{_codex_agent_json(recent_messages, CODEX_CONVERSATION_RECENT_CHAR_LIMIT)}"
        )
    memory_text = "\n\n".join(memory_parts) if memory_parts else "Sem historico persistido relevante para esta conversa."
    approved_mobile_execution = bool(whatsapp_full_access and sandbox == "full_access")
    mutable_rule = (
        "- Esta tarefa mutavel ja foi confirmada fora do modelo por codigo unico no mesmo numero de WhatsApp. "
        "Pode executar o pedido com as ferramentas nativas do Codex, sem solicitar uma segunda confirmacao. "
        "O protocolo jk_tool_calls continua reservado a consultas e preparacao segura de dados.\n"
        if approved_mobile_execution
        else "- Acao mutavel nao pode ser executada por ferramenta: editar arquivo, comando, sincronizar, publicar, responder pergunta, alterar banco, Bling ou Mercado Livre exige aprovacao explicita.\n"
    )
    mobile_report_rule = (
        "Estilo de conversa no WhatsApp:\n"
        "- Fale de forma natural, cordial e descontraida, como um colega prestativo.\n"
        "- Va direto ao ponto e varie a abertura; nao transforme toda resposta em comunicado formal.\n"
        "- Nao coloque titulo em respostas simples, nao repita o nome Black Jhon e nao assine ao final.\n"
        "- Use titulos e secoes somente quando ajudarem a organizar relatorios ou respostas realmente longas.\n"
        "- Nao use emojis nas respostas do WhatsApp.\n\n"
        "Formato de relatorio para WhatsApp:\n"
        "- Escreva para uma tela pequena, com frases curtas, espaco entre blocos e sem tabelas Markdown.\n"
        "- Use nesta ordem: Relatorio, Dados principais, Mais vendidos quando houver ranking, Analise e Fontes e cobertura.\n"
        "- Em Dados principais, mostre no maximo 8 indicadores objetivos. Em consultas comuns, use no maximo 5 itens; em relatorios completos, liste todos os SKUs e deixe o formatador dividir ate 8 por card.\n"
        "- Para cada item do ranking, informe SKU, nome completo do produto, quantidade e valor; nao misture dois produtos no mesmo paragrafo e nao corte texto com reticencias.\n"
        "- No relatorio do dia, use obrigatoriamente os pedidos da API do Mercado Livre e liste todos os SKUs vendidos, cada um com quantidade, valor unitario medio e total vendido.\n"
        "- Deixe as fontes por ultimo, em linguagem simples, incluindo periodo, conta ou loja, quantidade de registros e eventual lacuna.\n\n"
        if whatsapp_full_access
        else ""
    )
    source_routing_rule = ""
    if source_policy:
        source_routing_rule = (
            "Politica obrigatoria de origem dos dados para esta pergunta do WhatsApp:\n"
            "- Execute primeiro todas as ferramentas de required_tools antes de qualquer fallback.\n"
            "- Estoque comum vem da API atual da Bling, sempre sem depositos Full.\n"
            "- Descricao de anuncios, pedidos e vendas vem primeiro da API do Mercado Livre.\n"
            "- Estoque Full vem exclusivamente da API de inventario fulfillment do Mercado Livre.\n"
            "- Nunca consulte nem use saldo Full da Bling ou saldo Full de cadastro/cache local.\n"
            "- Se a soma combinar loja e Full, use loja=Bling sem Full e Full=Mercado Livre; se faltar uma fonte, nao estime.\n"
            f"Politica calculada pelo servidor: {_codex_agent_json(source_policy, 4000)}\n\n"
        )
    guidance_section = guidance_text + "\n\n" if guidance_text else ""
    if native_mcp:
        tool_protocol = (
            "Protocolo de ferramenta:\n"
            "Use diretamente as ferramentas tipadas do servidor MCP jk_system. "
            "Nao escreva blocos jk_tool_calls quando o MCP estiver disponivel.\n"
            "Se o servidor MCP nao estiver disponivel neste turno, use o rollback temporario: "
            "<jk_tool_calls>[{\"tool_id\":\"id_autorizado\",\"args\":{}}]</jk_tool_calls>.\n"
            f"Use no maximo {max_calls} ferramentas por etapa e no maximo {max_cycles} etapas. "
            "Depois de cada retorno, verifique tool_validation.dados_suficientes e tente as proximas fontes autorizadas antes de concluir.\n\n"
        )
    else:
        tool_protocol = (
            "Protocolo de ferramenta de compatibilidade:\n"
            "Quando precisar consultar dados, responda somente com um bloco JSON valido neste formato:\n"
            "<jk_tool_calls>\n"
            "[{\"tool_id\":\"sales_ranking\",\"args\":{\"message\":\"pedido original\",\"data_inicio\":\"YYYY-MM-DD\",\"data_fim\":\"YYYY-MM-DD\",\"loja\":\"JK Pecas\",\"limite\":50},\"reason\":\"por que precisa\"}]\n"
            "</jk_tool_calls>\n"
            f"Use no maximo {max_calls} ferramentas por ciclo e no maximo {max_cycles} ciclos. "
            "Depois que receber resultados suficientes, responda normalmente sem o bloco jk_tool_calls.\n\n"
        )
    return (
        f"Voce e o {BLACK_JHON_DISPLAY_NAME}, assistente interno unificado do JK Sistema. "
        "O Codex e sua IA principal de raciocinio e execucao.\n"
        "Trabalhe em modo agente: primeiro entenda a pergunta, depois solicite somente as ferramentas read-only necessarias. "
        "Nao invente dados e nao dependa da tela atual quando houver ferramenta de dados mais apropriada.\n\n"
        "Regras de seguranca:\n"
        "- Consultas internas e externas read-only podem ser solicitadas pelo protocolo abaixo.\n"
        f"{mutable_rule}"
        "- Se os dados vierem vazios, peca fallbacks do catalogo antes de responder, respeitando os limites.\n"
        "- Depois de cada resultado, confira tool_validation.dados_suficientes. Se for falso e houver proximas_fontes, peca outra ferramenta antes de concluir.\n"
        "- Para dados que nao estejam na tela, use fontes read-only: banco local, CSV, cache, logs de sync, Bling, Mercado Livre, perguntas, anuncios e fiscal.\n\n"
        "Use somente as ferramentas presentes no catalogo deste turno; ferramentas omitidas nao estao autorizadas para este usuario. "
        "Depois tente uma ferramenta especializada permitida. Se vier vazio, use apenas os fallbacks que tambem aparecem no catalogo. "
        "Nunca tente descobrir, enumerar ou consultar fontes, capacidades, acoes ou modulos ausentes do catalogo.\n\n"
        "Regra de comunicacao com o usuario:\n"
        "- Use os IDs de ferramentas apenas dentro do bloco jk_tool_calls.\n"
        "- Na resposta final, status e relatorios, nunca mostre nomes internos como local_database_query, source_discovery, stock_data, function, executor ou nomes de arquivos tecnicos.\n"
        "- Explique as fontes em linguagem simples: historico de vendas, historico de estoque, cadastro de produtos, status das integracoes, Bling ou Mercado Livre.\n\n"
        f"{mobile_report_rule}"
        f"{source_routing_rule}"
        f"{guidance_section}"
        f"{tool_protocol}"
        "No final de respostas com dados, inclua onde consultou em linguagem simples, periodo, loja/conta, quantidade de registros e avisos de dados incompletos.\n\n"
        f"Configuracao: modelo={model}, raciocinio={reasoning_effort}, velocidade={speed}, aprovacao={approval_profile}, sandbox={sandbox}.\n\n"
        "Contexto de continuidade da conversa:\n"
        f"{memory_text}\n\n"
        "Resumo curto da tela atual:\n"
        f"{_codex_agent_json(screen_summary, CODEX_AGENT_SCREEN_CONTEXT_LIMIT)}\n\n"
        "Catalogo compacto de ferramentas read-only disponiveis:\n"
        f"{_codex_agent_json(catalog, CODEX_AGENT_CATALOG_LIMIT)}\n\n"
        "Catalogo compacto de capacidades do JK Sistema:\n"
        f"{_codex_agent_json(capabilities, CODEX_AGENT_CATALOG_LIMIT)}\n\n"
        "Pergunta do usuario:\n"
        f"{prompt}"
    )


def _codex_agent_extract_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    content = str(text or "")
    blocks = re.findall(r"<jk_tool_calls>\s*(.*?)\s*</jk_tool_calls>", content, flags=re.I | re.S)
    if not blocks:
        blocks = re.findall(r"```(?:jk_tool_calls|json)\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", content, flags=re.I)
    if not blocks:
        return [], ""
    raw = blocks[-1].strip()
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return [], f"Bloco jk_tool_calls invalido: {exc}"
    if isinstance(parsed, dict):
        parsed = parsed.get("calls") or parsed.get("tool_calls") or [parsed]
    if not isinstance(parsed, list):
        return [], "Bloco jk_tool_calls precisa ser uma lista JSON."
    calls: list[dict[str, Any]] = []
    for item in parsed[:CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE]:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("tool_id") or item.get("id") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        reason = str(item.get("reason") or item.get("motivo") or "").strip()
        if tool_id:
            calls.append({"tool_id": tool_id, "args": args, "reason": reason[:600]})
    return calls, ""


def _codex_agent_tool_status(tool_id: str) -> str:
    try:
        from backend.services import codex_assistant

        meta = codex_assistant._assistant_tool_meta(tool_id)
        return str(meta.get("status") or meta.get("module") or tool_id).strip()
    except Exception:
        return tool_id


def _codex_agent_result_without_exact_transcripts(value: Any) -> Any:
    """Mantem fatos da venda no prompt, mas reserva falas ao formatador servidor."""
    if not isinstance(value, dict):
        return value
    safe = copy.deepcopy(value)
    if not (safe.get("exact_metadata") or {}).get("exact_lookup"):
        return safe
    for rows_key in ("top_rows", "all_rows"):
        rows = safe.get(rows_key)
        if not isinstance(rows, list):
            continue
        compact_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact = copy.deepcopy(row)
            conversations = compact.pop("conversations", {})
            compact["conversation_counts"] = {
                "total_messages": int((conversations or {}).get("total_messages") or 0),
                "complete": bool((conversations or {}).get("complete")),
            }
            for claim in compact.get("claims") or []:
                if isinstance(claim, dict):
                    conversation = claim.pop("conversation", {})
                    claim["conversation_count"] = int((conversation or {}).get("total_messages") or 0)
            compact_rows.append(compact)
        safe[rows_key] = compact_rows
    return safe


def _codex_agent_results_prompt(cycle: int, results: list[dict[str, Any]]) -> str:
    prompt_results = [
        _codex_agent_result_without_exact_transcripts(item)
        for item in results
        if isinstance(item, dict)
    ]
    payload = {
        "cycle": cycle,
        "tool_results": prompt_results,
        "next_instruction": (
            "Analise estes resultados e leia tool_validation de cada ferramenta. Se dados_suficientes for falso "
            "e houver proximas_fontes, solicite novo bloco jk_tool_calls com esses fallbacks antes de concluir. "
            "Se ja houver dados suficientes, responda ao usuario em portugues, "
            "incluindo onde consultou em linguagem simples, periodo, loja/conta, quantidade de registros e avisos. "
            "Use source_label, sources_human, tool_label e proximas_fontes_humanas para falar com o usuario; "
            "nao exponha tool_id, function, executor ou nomes internos de ferramenta."
        ),
    }
    return "Resultados compactos das ferramentas read-only:\n" + _codex_agent_json(payload, CODEX_AGENT_TOOL_RESULT_LIMIT)


def _codex_whatsapp_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _codex_whatsapp_money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return "R$ " + f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _codex_whatsapp_report_date(value: Any) -> str:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    return f"{match.group(3)}/{match.group(2)}/{match.group(1)}" if match else raw


def _codex_whatsapp_inline(value: Any, fallback: str = "") -> str:
    """Normaliza apenas espacos; nunca corta nomes com reticencias."""
    text = " ".join(str(value or "").split()).strip()
    return text or fallback


def _codex_whatsapp_user_request_text(task: dict[str, Any]) -> str:
    """Extrai somente o pedido do usuario do envelope interno do WhatsApp."""
    prompt = str(task.get("prompt") or "").strip()
    marker = "Texto recebido:"
    if marker not in prompt:
        return prompt
    prompt = prompt.split(marker, 1)[1].strip()
    for suffix in (
        "\nO usuario pediu explicitamente uma foto",
        "\nAnexo local recebido pelo WhatsApp:",
        "\nTranscricao local do audio:",
    ):
        if suffix in prompt:
            prompt = prompt.split(suffix, 1)[0].strip()
    return prompt


def _codex_whatsapp_ml_report_requested(
    task: dict[str, Any],
    *,
    tool_id: str = "",
    args: Optional[dict[str, Any]] = None,
    results: Optional[list[dict[str, Any]]] = None,
) -> bool:
    """Reconhece relatorio ML mesmo quando a continuacao perdeu query_policy."""
    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return False
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    args = dict(args or {}) if isinstance(args, dict) else {}
    results = list(results or []) if isinstance(results, list) else []
    result_matches = [
        item for item in results
        if isinstance(item, dict) and str(item.get("tool_id") or "") == "mercado_livre_orders"
    ]
    ml_orders_context = bool(
        str(tool_id or "") == "mercado_livre_orders"
        or "mercado_livre_orders" in (source_policy.get("required_tools") or [])
        or result_matches
    )
    if not ml_orders_context:
        return False
    if query_policy.get("report_mode") is True:
        return True
    if str(args.get("mode") or args.get("modo") or "").strip().lower() in {"report", "daily", "relatorio"}:
        return True
    for item in result_matches:
        result_args = item.get("args") if isinstance(item.get("args"), dict) else {}
        if str(result_args.get("mode") or result_args.get("modo") or "").strip().lower() in {"report", "daily", "relatorio"}:
            return True
        for summary_item in item.get("summary") if isinstance(item.get("summary"), list) else []:
            payload = summary_item.get("summary") if isinstance(summary_item, dict) and isinstance(summary_item.get("summary"), dict) else {}
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            if paging.get("report_mode") is True:
                return True
    text = _codex_texto_sem_acentos(" ".join([
        _codex_whatsapp_user_request_text(task),
        str(query_policy.get("base_request") or ""),
        str(args.get("message") or args.get("mensagem") or ""),
    ]))
    if re.search(r"\b(relatorio|analise|resumo|balanco|fechamento|consolidado)\b", text):
        return True
    months = (
        r"janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro"
    )
    return bool(
        re.search(rf"\bvendas?\b[^.]*\b(hoje|ontem|dia|semana|mes|periodo|{months})\b", text)
        or re.search(rf"\b(hoje|ontem|dia|semana|mes|periodo|{months})\b[^.]*\bvendas?\b", text)
    )


def _codex_whatsapp_prepare_agent_tool_call(
    task: dict[str, Any],
    tool_id: str,
    args: Optional[dict[str, Any]],
    previous_results: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], bool]:
    prepared = dict(args or {}) if isinstance(args, dict) else {}
    if str(tool_id or "") in {"mercado_livre_orders", "mercado_livre_returns"}:
        prepared["message"] = str(
            prepared.get("message")
            or prepared.get("mensagem")
            or _codex_whatsapp_user_request_text(task)
            or ""
        ).strip()
    complete_report = _codex_whatsapp_ml_report_requested(
        task,
        tool_id=tool_id,
        args=prepared,
        results=previous_results,
    )
    if complete_report and str(tool_id or "") == "mercado_livre_orders":
        try:
            requested_limit = int(prepared.get("limite") or prepared.get("limit") or 0)
        except (TypeError, ValueError):
            requested_limit = 0
        try:
            requested_pages = int(prepared.get("max_paginas") or prepared.get("max_pages") or 0)
        except (TypeError, ValueError):
            requested_pages = 0
        prepared["mode"] = "report"
        prepared["limite"] = max(20_000, requested_limit)
        prepared["max_paginas"] = max(400, requested_pages)
        prepared["force_refresh"] = True
        prepared["status"] = "paid,partially_refunded"
        prepared["statuses"] = "paid,partially_refunded"
    return prepared, complete_report


def _codex_whatsapp_deposit_reason(value: Any) -> str:
    raw = _codex_whatsapp_inline(value)
    normalized = _codex_texto_sem_acentos(raw)
    if "full" in normalized or "fulfillment" in normalized:
        return "excluído por ser Full/Fulfillment"
    if "desconsiderarsaldo" in normalized or "desconsiderar saldo" in normalized:
        return "desconsiderado pela configuração da Bling"
    if "inativo" in normalized:
        return "depósito inativo na Bling"
    if "id nao encontrado" in normalized:
        return "não classificado; ID não encontrado no catálogo de depósitos"
    if "metadados insuficientes" in normalized:
        return "não classificado; metadados insuficientes para calcular o saldo confiável"
    return raw or "não classificado; não incluído no total confiável"


def _codex_whatsapp_bling_stock_response(task: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """Formata o saldo Bling com cada deposito nomeado e sua classificacao."""
    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return ""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "bling_stock_balances"
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    rows = [row for row in (result.get("top_rows") or result.get("rows") or []) if isinstance(row, dict)]
    if not rows:
        return ""
    blocks: list[str] = []
    seen_blocks: set[str] = set()
    for row in rows:
        store = _codex_whatsapp_inline(row.get("loja") or row.get("store"), "loja selecionada")
        sku = _codex_whatsapp_inline(row.get("sku") or row.get("codigo"), "não informado")
        product = _codex_whatsapp_inline(row.get("produto") or row.get("nome"), "Produto sem nome")
        included = [item for item in (row.get("depositos") or []) if isinstance(item, dict)]
        excluded = [item for item in (row.get("depositos_excluidos") or []) if isinstance(item, dict)]
        gross = row.get("saldo_bruto_retornado")
        if gross is None:
            gross = sum(
                float(item.get("saldo_fisico", item.get("saldoFisico", item.get("saldo", 0))) or 0)
                for item in included + excluded
            )
        store_balance = row.get("saldo_loja_total", row.get("saldo_total"))
        if store_balance is None:
            store_balance_text = "indisponível — a classificação dos depósitos está incompleta"
        else:
            store_balance_text = f"{_codex_whatsapp_number(store_balance)} unidades"
        lines = [
            f"Na **{store}**, o SKU **{sku}** é **{product}**.",
            "",
            f"**Estoque disponível de loja na Bling:** {store_balance_text}",
            f"**Saldo bruto retornado pela Bling:** {_codex_whatsapp_number(gross)} unidades",
            "",
            "**Depósitos incluídos no saldo de loja**",
        ]
        if not included:
            lines.append("- Nenhum depósito pôde ser incluído com segurança.")
        for deposit in included:
            deposit_id = _codex_whatsapp_inline(deposit.get("id"))
            name = _codex_whatsapp_inline(deposit.get("nome") or deposit.get("descricao"))
            if not name or "nao classificado" in _codex_texto_sem_acentos(name):
                name = f"Depósito ID {deposit_id or 'desconhecido'} — não classificado"
            balance = deposit.get("saldo_fisico", deposit.get("saldoFisico", deposit.get("saldo", 0)))
            lines.append(f"- **{name}:** {_codex_whatsapp_number(balance)} unidades — incluído")
        lines.extend(["", "**Depósitos excluídos do saldo de loja**"])
        if not excluded:
            lines.append("- Nenhum depósito excluído.")
        for deposit in excluded:
            deposit_id = _codex_whatsapp_inline(deposit.get("id"))
            name = _codex_whatsapp_inline(deposit.get("nome") or deposit.get("descricao"))
            if not name or "nao classificado" in _codex_texto_sem_acentos(name):
                name = f"Depósito ID {deposit_id or 'desconhecido'} — não classificado"
            balance = deposit.get("saldo_fisico", deposit.get("saldoFisico", deposit.get("saldo", 0)))
            reason = _codex_whatsapp_deposit_reason(
                deposit.get("motivo") or deposit.get("reason") or deposit.get("motivo_exclusao")
            )
            lines.append(f"- **{name}:** {_codex_whatsapp_number(balance)} unidades — {reason}")
        if row.get("cobertura_depositos_completa") is False:
            lines.extend([
                "",
                "⚠️ A classificação dos depósitos está incompleta; depósitos não identificados não entraram no saldo disponível.",
            ])
        elif row.get("full_excluido") is True:
            lines.extend(["", "O estoque Full foi excluído desta consulta."])
        block = "\n".join(lines)
        if block not in seen_blocks:
            seen_blocks.add(block)
            blocks.append(block)
    return "\n\n".join(blocks).strip()


CODEX_EXACT_ORDER_HISTORY_MARKER = "<!-- JK_EXACT_ORDER_FULL_HISTORY -->"


def _codex_exact_value(value: Any, fallback: str = "indisponível") -> str:
    if value is None:
        return fallback
    text = " ".join(str(value).split()).strip()
    return text if text else fallback


def _codex_exact_money(value: Any) -> str:
    return "indisponível" if value is None or value == "" else _codex_whatsapp_money(value)


def _codex_exact_message_lines(messages: Any, seen: set[tuple[str, str, str]]) -> list[str]:
    lines: list[str] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text") or "").strip()
        attachments = [
            _codex_whatsapp_inline(item.get("name") if isinstance(item, dict) else item)
            for item in (message.get("attachments") or [])
        ]
        attachments = [item for item in attachments if item]
        key = (
            str(message.get("message_id") or message.get("date") or ""),
            str(message.get("role") or ""),
            text,
        )
        if key in seen:
            continue
        seen.add(key)
        label = _codex_exact_value(message.get("label"), "Participante")
        date_label = _codex_whatsapp_report_date(message.get("date")) or "data indisponível"
        if text:
            quoted = "\n> ".join(line.rstrip() for line in text.replace("\r", "").split("\n"))
            lines.append(f"- {date_label} — **{label}:**\n> {quoted}")
        else:
            lines.append(f"- {date_label} — **{label}:** mensagem sem texto disponível")
        if attachments:
            lines.append("  Anexos: " + ", ".join(attachments))
        moderation = _codex_whatsapp_inline(message.get("moderation_status"))
        if moderation and moderation.lower() not in {"available", "clean"}:
            lines.append(f"  Moderação: {moderation}")
    return lines


def _codex_exact_status_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "confirmed": "Confirmado",
        "payment_required": "Aguardando pagamento",
        "payment_in_process": "Pagamento em análise",
        "partially_paid": "Parcialmente pago",
        "paid": "Pago",
        "partially_refunded": "Parcialmente reembolsado",
        "pending_cancel": "Cancelamento pendente",
        "cancelled": "Cancelado",
        "invalid": "Inválido",
    }.get(key, _codex_exact_value(value))


def _codex_exact_shipment_label(shipment: dict[str, Any]) -> str:
    state = str(shipment.get("delivery_state") or "").strip().lower()
    return {
        "delivered": "Entregue",
        "in_transit": "Em trânsito",
        "preparing": "Em preparação",
        "not_delivered": "Não entregue",
        "cancelled": "Cancelado",
        "unavailable": "Indisponível",
    }.get(state, _codex_exact_value(shipment.get("delivery_state_label")))


def _codex_exact_logistics_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "fulfillment": "Mercado Full",
        "cross_docking": "Cross docking",
        "xd_drop_off": "Cross docking com postagem",
        "drop_off": "Postagem em agência",
        "self_service": "Mercado Envios Flex",
        "custom": "Logística própria",
    }.get(key, _codex_exact_value(value))


def _codex_exact_quantity(value: Any) -> str:
    if value is None or value == "":
        return "indisponível"
    return _codex_whatsapp_number(value)


def _codex_exact_ml_order_whatsapp_response(
    metadata: dict[str, Any],
    orders: list[dict[str, Any]],
) -> str:
    """Ficha móvel única; detalhes técnicos repetidos ficam fora do WhatsApp."""
    requested_id = _codex_exact_value(metadata.get("requested_id"), "não informado")
    stores = [str(item).strip() for item in (metadata.get("matched_stores") or []) if str(item).strip()]
    store_label = ", ".join(stores) or "loja indisponível"
    lines = [
        CODEX_EXACT_ORDER_HISTORY_MARKER,
        f"# Venda {requested_id} — {store_label}",
    ]
    multiple_orders = len(orders) > 1
    seen_post_sale: set[tuple[str, str, str]] = set()

    for index, order in enumerate(orders, start=1):
        order_id = _codex_exact_value(order.get("order_id"))
        pack_id = _codex_exact_value(order.get("pack_id"), "")
        if multiple_orders:
            lines.extend(["", f"## Pedido {index} — {order_id}"])
        elif order_id != requested_id:
            lines.append(f"Pedido: **{order_id}**")

        sale_date = _codex_whatsapp_report_date(order.get("date_created"))
        closed_date = _codex_whatsapp_report_date(order.get("date_closed"))
        lines.extend([
            f"**Status:** {_codex_exact_status_label(order.get('status'))}",
            f"**Data da venda:** {_codex_exact_value(sale_date)}",
        ])
        if closed_date and closed_date != sale_date:
            lines.append(f"**Fechamento:** {closed_date}")
        if pack_id and pack_id not in {order_id, requested_id}:
            lines.append(f"**Pack:** {pack_id}")

        buyer = _codex_exact_value(order.get("buyer_name"), "")
        nickname = _codex_exact_value(order.get("buyer_nickname"), "")
        if buyer or nickname:
            buyer_label = buyer or nickname
            if buyer and nickname and buyer.lower() != nickname.lower():
                buyer_label = f"{buyer} • usuário ML {nickname}"
            lines.append(f"**Comprador:** {buyer_label}")

        lines.extend(["", "## Produto"])
        items = [item for item in (order.get("items") or []) if isinstance(item, dict)]
        if not items:
            lines.append("Produto indisponível na resposta da API.")
        for item in items:
            quantity = _codex_exact_quantity(item.get("quantity"))
            sku = _codex_exact_value(item.get("sku"))
            title = _codex_exact_value(item.get("title"), "Produto sem título")
            lines.append(f"• **{quantity}x • SKU {sku}** — {title}")
            price_line = f"  {_codex_exact_money(item.get('unit_price'))} por unidade"
            try:
                show_subtotal = float(item.get("quantity") or 0) > 1
            except (TypeError, ValueError):
                show_subtotal = False
            if show_subtotal:
                price_line += f" • subtotal {_codex_exact_money(item.get('gross_amount'))}"
            lines.append(price_line)

        lines.extend([
            "",
            "## Valores",
            f"• **Venda:** {_codex_exact_money(order.get('gross_amount'))}",
            f"• **Pago:** {_codex_exact_money(order.get('paid_amount'))}",
            f"• **Reembolsado:** {_codex_exact_money(order.get('refund_amount'))}",
            f"• **Líquido:** {_codex_exact_money(order.get('net_amount'))}",
        ])

        shipment = order.get("shipment") if isinstance(order.get("shipment"), dict) else {}
        fulfillment = order.get("fulfillment") if isinstance(order.get("fulfillment"), dict) else {}
        is_full = fulfillment.get("is_full")
        full_label = "Sim" if is_full is True else ("Não" if is_full is False else "Indisponível")
        logistic_type = fulfillment.get("logistic_type") or shipment.get("logistic_type")
        delivery_date = shipment.get("date_delivered") or shipment.get("estimated_delivery")
        lines.extend([
            "",
            "## Envio",
            f"• **Situação:** {_codex_exact_shipment_label(shipment)}",
            f"• **Logística:** {_codex_exact_logistics_label(logistic_type)}",
            f"• **Full:** {full_label}",
        ])
        if shipment.get("shipment_id"):
            lines.append(f"• **Código do envio:** {_codex_exact_value(shipment.get('shipment_id'))}")
        if delivery_date:
            lines.append(f"• **Entrega/previsão:** {_codex_whatsapp_report_date(delivery_date)}")

        claims = [claim for claim in (order.get("claims") or []) if isinstance(claim, dict)]
        claims_status = order.get("claims_status") if isinstance(order.get("claims_status"), dict) else {}
        return_status = order.get("return_status") if isinstance(order.get("return_status"), dict) else {}
        conversations = order.get("conversations") if isinstance(order.get("conversations"), dict) else {}
        post_sale = conversations.get("post_sale") if isinstance(conversations.get("post_sale"), dict) else {}
        total_messages = conversations.get("total_messages")
        claims_label = (
            "nenhuma"
            if claims_status.get("available") is True and not claims
            else str(len(claims))
            if claims
            else "indisponível"
        )
        return_label = _codex_exact_value(return_status.get("label"))
        if total_messages is None:
            messages_label = "indisponível"
        elif int(total_messages or 0) > 0:
            messages_label = str(int(total_messages or 0))
        elif conversations.get("complete") is True and post_sale.get("available") is True:
            messages_label = "nenhuma"
        else:
            messages_label = "indisponível"
        lines.extend([
            "",
            "## Pós-venda",
            f"• **Reclamações:** {claims_label}",
            f"• **Devolução:** {return_label}",
            f"• **Mensagens:** {messages_label}",
        ])

        for claim in claims:
            detail = claim.get("detail") if isinstance(claim.get("detail"), dict) else {}
            lines.append(
                f"• **Reclamação {_codex_exact_value(claim.get('claim_id'))}:** "
                f"{_codex_exact_value(detail.get('title') or claim.get('reason_id'))} — "
                f"{_codex_exact_value(claim.get('status'))}"
            )

        post_lines = _codex_exact_message_lines(post_sale.get("messages"), seen_post_sale)
        if post_lines:
            lines.extend(["", "## Histórico pós-venda", *post_lines])
        for claim in claims:
            conversation = claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}
            claim_lines = _codex_exact_message_lines(conversation.get("messages"), set())
            if claim_lines:
                lines.extend([
                    "",
                    f"## Histórico da reclamação {_codex_exact_value(claim.get('claim_id'))}",
                    *claim_lines,
                ])

    partial = metadata.get("partial_response") is True or metadata.get("coverage_complete") is False
    lines.extend([
        "",
        (
            "⚠️ **Cobertura parcial:** alguma seção não foi disponibilizada pela API."
            if partial
            else "_Fonte: API do Mercado Livre • consulta atual • cobertura completa._"
        ),
    ])
    return "\n".join(lines).strip()


def _codex_exact_ml_order_response(
    task: dict[str, Any],
    results: list[dict[str, Any]],
    ai_summary: str = "",
) -> str:
    """Anexa fatos e falas diretamente do resultado, sem reescrita pelo modelo."""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "mercado_livre_orders"
            and isinstance(item.get("exact_metadata"), dict)
            and item.get("exact_metadata", {}).get("exact_lookup") is True
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    metadata = result.get("exact_metadata") or {}
    requested_id = _codex_exact_value(metadata.get("requested_id"), "não informado")
    if not metadata.get("found"):
        searched = [
            _codex_exact_value(item.get("store"))
            for item in (metadata.get("searched_stores") or [])
            if isinstance(item, dict) and item.get("store")
        ]
        lines = [
            CODEX_EXACT_ORDER_HISTORY_MARKER,
            f"Não localizei o número **{requested_id}** como order nem como pack nas contas permitidas.",
        ]
        if searched:
            lines.append("Lojas consultadas diretamente na API do Mercado Livre: " + ", ".join(dict.fromkeys(searched)) + ".")
        message = _codex_whatsapp_inline(metadata.get("message"))
        if message:
            lines.append(message)
        lines.append("O histórico local não foi usado para substituir essa consulta exata.")
        return "\n\n".join(lines).strip()

    orders = [row for row in (result.get("all_rows") or []) if isinstance(row, dict)]
    if str(task.get("origin") or "").strip().lower() == "whatsapp":
        return _codex_exact_ml_order_whatsapp_response(metadata, orders)
    identifier_type = "pack" if metadata.get("identifier_type") == "pack" else "order"
    matched_stores = [str(item) for item in (metadata.get("matched_stores") or []) if str(item).strip()]
    lines = [
        CODEX_EXACT_ORDER_HISTORY_MARKER,
        f"# Venda específica {requested_id}",
        f"Identificador reconhecido: **{identifier_type}**",
    ]
    if matched_stores:
        lines.append("Loja: **" + ", ".join(matched_stores) + "**")
    summary_text = str(ai_summary or "").strip().replace(CODEX_EXACT_ORDER_HISTORY_MARKER, "")
    if summary_text:
        lines.extend(["", "## Resumo da IA", summary_text])

    seen_post_sale: set[tuple[str, str, str]] = set()
    for index, order in enumerate(orders, start=1):
        order_id = _codex_exact_value(order.get("order_id"))
        pack_id = _codex_exact_value(order.get("pack_id"))
        store = _codex_exact_value(order.get("store"), matched_stores[0] if matched_stores else "indisponível")
        lines.extend([
            "",
            f"## Pedido {index} — order {order_id}",
            f"- **Loja:** {store}",
            f"- **Pack:** {pack_id}",
            f"- **Status do pedido:** {_codex_exact_value(order.get('status'))}",
            f"- **Data da venda:** {_codex_exact_value(_codex_whatsapp_report_date(order.get('date_created')))}",
            f"- **Data de fechamento:** {_codex_exact_value(_codex_whatsapp_report_date(order.get('date_closed')))}",
        ])
        buyer = _codex_exact_value(order.get("buyer_name"), "")
        nickname = _codex_exact_value(order.get("buyer_nickname"), "")
        buyer_label = buyer or nickname or "indisponível"
        if buyer and nickname and nickname.lower() != buyer.lower():
            buyer_label = f"{buyer} ({nickname})"
        lines.append(f"- **Comprador:** {buyer_label}")
        lines.extend([
            f"- **Total da venda:** {_codex_exact_money(order.get('gross_amount'))}",
            f"- **Valor pago:** {_codex_exact_money(order.get('paid_amount'))}",
            f"- **Valor reembolsado:** {_codex_exact_money(order.get('refund_amount'))}",
            f"- **Valor líquido:** {_codex_exact_money(order.get('net_amount'))}",
            "",
            "### Produtos",
        ])
        items = [item for item in (order.get("items") or []) if isinstance(item, dict)]
        if not items:
            lines.append("- Produtos indisponíveis na resposta da API.")
        for item in items:
            variation = ", ".join(
                f"{_codex_exact_value(attribute.get('name'))}: {_codex_exact_value(attribute.get('value'))}"
                for attribute in (item.get("variation_attributes") or [])
                if isinstance(attribute, dict)
            )
            lines.extend([
                f"- **{_codex_exact_value(item.get('title'), 'Produto sem título')}**",
                f"  SKU: {_codex_exact_value(item.get('sku'))} | Item: {_codex_exact_value(item.get('item_id'))}",
                f"  Quantidade: {_codex_exact_value(item.get('quantity'))} | Preço unitário: {_codex_exact_money(item.get('unit_price'))}",
            ])
            if variation:
                lines.append(f"  Variação: {variation}")

        shipment = order.get("shipment") if isinstance(order.get("shipment"), dict) else {}
        fulfillment = order.get("fulfillment") if isinstance(order.get("fulfillment"), dict) else {}
        is_full = fulfillment.get("is_full")
        full_label = "Sim" if is_full is True else ("Não" if is_full is False else "Indisponível")
        delivery_date = shipment.get("date_delivered") or shipment.get("estimated_delivery")
        lines.extend([
            "",
            "### Envio",
            f"- **Atendido pelo Full:** {full_label}",
            f"- **Situação:** {_codex_exact_value(shipment.get('delivery_state_label'))}",
            f"- **Status/substatus:** {_codex_exact_value(shipment.get('status'))} / {_codex_exact_value(shipment.get('substatus'))}",
            f"- **Entrega ou previsão:** {_codex_exact_value(_codex_whatsapp_report_date(delivery_date))}",
        ])

        return_status = order.get("return_status") if isinstance(order.get("return_status"), dict) else {}
        claims = [claim for claim in (order.get("claims") or []) if isinstance(claim, dict)]
        lines.extend([
            "",
            "### Reclamações e devolução",
            f"- **Devolução:** {_codex_exact_value(return_status.get('label'))}",
            f"- **Reclamações encontradas:** {len(claims)}",
        ])
        for claim in claims:
            detail = claim.get("detail") if isinstance(claim.get("detail"), dict) else {}
            lines.extend([
                f"- **Reclamação { _codex_exact_value(claim.get('claim_id')) }:** {_codex_exact_value(claim.get('status'))}",
                f"  Motivo: {_codex_exact_value(detail.get('title') or claim.get('reason_id'))}",
                f"  Situação: {_codex_exact_value(claim.get('stage'))} | Atualização: {_codex_exact_value(_codex_whatsapp_report_date(claim.get('last_updated')))}",
            ])
            if detail.get("description") or detail.get("problem"):
                lines.append("  Detalhe: " + _codex_exact_value(detail.get("description") or detail.get("problem")))

        conversations = order.get("conversations") if isinstance(order.get("conversations"), dict) else {}
        post_sale = conversations.get("post_sale") if isinstance(conversations.get("post_sale"), dict) else {}
        post_lines = _codex_exact_message_lines(post_sale.get("messages"), seen_post_sale)
        lines.extend(["", "### Histórico pós-venda do pack"])
        if post_lines:
            lines.extend(post_lines)
        elif post_sale.get("available") is True:
            lines.append("Nenhuma mensagem pós-venda foi retornada pela API.")
        else:
            lines.append("Histórico pós-venda indisponível na API.")

        for claim in claims:
            claim_id = _codex_exact_value(claim.get("claim_id"))
            conversation = claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}
            claim_lines = _codex_exact_message_lines(conversation.get("messages"), set())
            lines.extend(["", f"### Histórico da reclamação {claim_id}"])
            if claim_lines:
                lines.extend(claim_lines)
            elif conversation.get("available") is True:
                lines.append("Nenhuma mensagem da reclamação foi retornada pela API.")
            else:
                lines.append("Histórico da reclamação indisponível na API.")

    partial = metadata.get("partial_response") is True or metadata.get("coverage_complete") is False
    lines.extend([
        "",
        "## Fontes e cobertura",
        "Consulta read-only feita diretamente nos recursos de orders/packs, envio, reclamações, devoluções e mensagens pós-venda do Mercado Livre.",
        "As mensagens foram preservadas em ordem cronológica; a consulta usou mark_as_read=false e não enviou respostas.",
        (
            "Cobertura parcial: uma ou mais seções ficaram indisponíveis; valores ausentes foram mantidos como indisponíveis."
            if partial
            else "Cobertura completa para todas as seções disponibilizadas pela API nesta consulta."
        ),
    ])
    return "\n".join(lines).strip()


def _codex_whatsapp_complete_ml_report(task: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """Gera o relatorio completo sem permitir que o modelo resuma o agregado por SKU."""
    if str(task.get("origin") or "") != "whatsapp":
        return ""
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    if str(query_policy.get("store_mode") or "single") == "all":
        return ""
    if not _codex_whatsapp_ml_report_requested(task, results=results):
        return ""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "mercado_livre_orders"
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    summaries = [item for item in (result.get("summary") or []) if isinstance(item, dict)]
    primary = next(
        (item for item in summaries if str(item.get("tool_id") or "") == "mercado_livre_orders"),
        summaries[0] if summaries else {},
    )
    data = primary.get("summary") if isinstance(primary.get("summary"), dict) else {}
    if data.get("api_consulted") is False or data.get("error"):
        return ""
    rows = [row for row in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(row, dict)]
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    requested = period.get("requested") if isinstance(period.get("requested"), dict) else {}
    fallback_period = primary.get("periodo") if isinstance(primary.get("periodo"), dict) else {}
    start = _codex_whatsapp_report_date(requested.get("from") or fallback_period.get("data_inicio"))
    end = _codex_whatsapp_report_date(requested.get("to") or fallback_period.get("data_fim"))
    period_label = start if start and start == end else f"{start} a {end}".strip(" a")
    store = str(query_policy.get("store") or primary.get("loja") or data.get("store") or "").strip()

    lines = [f"# Relatório de vendas — {store or 'Mercado Livre'}"]
    if period_label:
        lines.append(f"Período: {period_label}")
    if store:
        lines.append(f"Loja: {store}")
    lines.extend([
        "", "## Dados principais",
        f"- **Pedidos pagos:** {_codex_whatsapp_number(totals.get('orders'))}",
        f"- **Itens vendidos:** {_codex_whatsapp_number(totals.get('items_quantity'))}",
        f"- **Valor bruto:** {_codex_whatsapp_money(totals.get('gross_amount'))}",
        f"- **Valor pago:** {_codex_whatsapp_money(totals.get('paid_amount'))}",
    ])
    if totals.get("refund_amount") is None:
        lines.extend(["- **Estornos:** indisponível", "- **Valor líquido:** indisponível"])
    else:
        lines.extend([
            f"- **Estornos:** {_codex_whatsapp_money(totals.get('refund_amount'))}",
            f"- **Valor líquido:** {_codex_whatsapp_money(totals.get('net_amount'))}",
        ])
    lines.extend(["", "## SKUs vendidos"])
    if not rows:
        lines.append("Nenhum SKU vendido no período consultado.")
    for row in rows:
        sku = _codex_whatsapp_inline(
            row.get("sku") or row.get("seller_sku") or row.get("item_id") or row.get("mlb") or row.get("id"),
            "não informado",
        )
        title = _codex_whatsapp_inline(row.get("title") or row.get("produto") or row.get("nome"), "Produto sem título")
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        try:
            gross = float(row.get("gross_amount") or 0)
        except (TypeError, ValueError):
            gross = 0.0
        unit = gross / quantity if quantity > 0 else 0.0
        lines.extend([
            f"- **SKU:** {sku}",
            f"  **Produto:** {title}",
            f"  **Qtd.:** {_codex_whatsapp_number(quantity)}",
            f"  **Valor unitário médio:** {_codex_whatsapp_money(unit)}",
            f"  **Total vendido:** {_codex_whatsapp_money(gross)}",
        ])

    has_more = bool(paging.get("has_more") or data.get("truncated") or data.get("coverage_complete") is False)
    pages = int(paging.get("pages_fetched") or 0)
    scanned = int(paging.get("scanned") or paging.get("returned") or totals.get("orders") or 0)
    considered = int(totals.get("orders") or paging.get("returned") or 0)
    lines.extend([
        "", "## Fontes e cobertura",
        (
            f"Consulta direta à API do Mercado Livre da loja {store or 'selecionada'}, "
            f"no período {period_label or 'informado'}, com {pages} página(s), "
            f"{scanned} pedido(s) verificado(s), {considered} pedido(s) considerado(s) "
            f"e {len(rows)} SKU(s) consolidado(s)."
        ),
        (
            "Cobertura completa: todas as páginas disponíveis para o período foram consultadas."
            if not has_more
            else "Cobertura incompleta: a API ainda indicou páginas pendentes; nenhum valor ausente foi estimado."
        ),
    ])
    return "\n".join(lines).strip()


def _codex_generate_whatsapp_chart_artifacts(
    task_id: str,
    task: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create requested private report artifacts while tool results still exist."""

    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return {"expected": False, "status": "not_whatsapp", "artifacts": []}
    try:
        from backend.services import whatsapp_report_files, whatsapp_report_visuals

        chart_query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
        if not chart_query_policy:
            channel_metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
            chart_query_policy = (
                channel_metadata.get("query_policy")
                if isinstance(channel_metadata.get("query_policy"), dict)
                else {}
            )
        if not chart_query_policy:
            screen_context = task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {}
            selection = screen_context.get("selection") if isinstance(screen_context.get("selection"), dict) else {}
            chart_query_policy = (
                selection.get("query_policy")
                if isinstance(selection.get("query_policy"), dict)
                else {}
            )

        prompt = _codex_whatsapp_user_request_text(task) or task.get("prompt") or ""
        requested_formats = whatsapp_report_files.requested_report_formats(prompt)
        chart_outcome = (
            whatsapp_report_visuals.generate_task_chart_artifacts(
                base_info_dir=_codex_base_info_dir(),
                client_id=task.get("client_id") or "default",
                task_id=task_id,
                prompt=prompt,
                tool_results=results,
                query_policy=chart_query_policy,
                max_images=2,
            )
            if "png" in requested_formats
            else {"expected": False, "status": "not_requested", "artifacts": []}
        )
        document_outcome = whatsapp_report_files.generate_report_documents(
            base_info_dir=_codex_base_info_dir(),
            client_id=task.get("client_id") or "default",
            task_id=task_id,
            prompt=prompt,
            tool_results=results,
            query_policy=chart_query_policy,
            formats=requested_formats,
        )
        artifacts = [
            *list(chart_outcome.get("artifacts") or []),
            *list(document_outcome.get("artifacts") or []),
        ][:4]
        expected = bool(requested_formats & {"png", "pdf", "xlsx"})
        errors = [
            str(chart_outcome.get("error") or ""),
            *[str(item or "") for item in list(document_outcome.get("errors") or [])],
        ]
        expected = bool(requested_formats & {"png", "pdf", "xlsx"})
        outcome = {
            "expected": expected,
            "status": (
                "not_requested"
                if not expected
                else "completed"
                if len(artifacts) >= len(requested_formats & {"png", "pdf", "xlsx"})
                else "partial"
                if artifacts
                else "generation_failed"
            ),
            "artifacts": artifacts,
            "chart_expected": "png" in requested_formats,
            "error": "; ".join(item for item in errors if item)[:500],
        }
    except Exception as exc:
        outcome = {
            "expected": False,
            "status": "generation_failed",
            "artifacts": [],
            "error": str(exc)[:500],
        }
    if outcome.get("expected"):
        _codex_update_task(
            task_id,
            whatsapp_artifacts=list(outcome.get("artifacts") or [])[:4],
            whatsapp_chart_expected=bool(outcome.get("chart_expected")),
            whatsapp_chart_status=str(outcome.get("status") or "")[:80],
            whatsapp_chart_error=str(outcome.get("error") or "")[:500],
        )
    return outcome


def _codex_agent_unique_extend(target: list[Any], values: Any) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        if value not in target:
            target.append(value)


def _codex_agent_update_trace(task_id: str, trace: dict[str, Any], **updates: Any) -> None:
    _codex_update_live(
        task_id,
        agent_steps=list(trace.get("agent_steps") or [])[-80:],
        tool_calls=list(trace.get("tool_calls") or [])[-80:],
        tool_results_summary=list(trace.get("tool_results_summary") or [])[-80:],
        sources=list(trace.get("sources") or [])[-80:],
        warnings=list(trace.get("warnings") or [])[-80:],
        **updates,
    )


def _codex_agent_source_policy_for_task(task: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    return dict(source_policy)


def _codex_agent_source_policy_error(
    tool_id: str,
    source_policy: dict[str, Any],
    previous_results: list[dict[str, Any]],
) -> str:
    if not source_policy:
        return ""
    tool_id = str(tool_id or "").strip()
    required = [str(item or "").strip() for item in (source_policy.get("required_tools") or []) if str(item or "").strip()]
    forbidden = {str(item or "").strip() for item in (source_policy.get("forbidden_tools") or []) if str(item or "").strip()}
    if tool_id in forbidden:
        return "Fonte bloqueada pela politica do WhatsApp para este tipo de dado."
    if source_policy.get("full_exclusive") is True and tool_id not in set(required) | {"integrations_status"}:
        return "Estoque Full aceita exclusivamente a ferramenta de inventario Full da API do Mercado Livre."
    attempted = {
        str(item.get("tool_id") or "").strip()
        for item in previous_results
        if isinstance(item, dict) and str(item.get("tool_id") or "").strip()
    }
    pending_required = [item for item in required if item not in attempted]
    if pending_required and tool_id not in pending_required:
        return "Antes de qualquer fallback, consulte as fontes obrigatorias: " + ", ".join(pending_required)
    return ""


def _codex_agent_order_calls_by_source_policy(
    calls: list[dict[str, Any]],
    source_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run required sources in their deterministic policy order."""

    required = [
        str(item or "").strip()
        for item in (source_policy.get("required_tools") or [])
        if str(item or "").strip()
    ]
    if not required:
        return list(calls or [])
    rank = {tool_id: index for index, tool_id in enumerate(required)}
    indexed = list(enumerate(calls or []))
    indexed.sort(
        key=lambda pair: (
            0 if str((pair[1] or {}).get("tool_id") or "") in rank else 1,
            rank.get(str((pair[1] or {}).get("tool_id") or ""), len(rank)),
            pair[0],
        )
    )
    return [call for _index, call in indexed]


def _codex_agent_run_loop(
    task_id: str,
    task: dict[str, Any],
    thread: Any,
    run_kwargs: dict[str, Any],
    initial_prompt: str,
    screen_context: Any,
    report_mode: bool,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    max_cycles = _codex_int_env(
        "JK_CODEX_AGENT_REPORT_MAX_CYCLES" if report_mode else "JK_CODEX_AGENT_MAX_CYCLES",
        CODEX_AGENT_REPORT_MAX_CYCLES if report_mode else CODEX_AGENT_MAX_CYCLES,
        1,
        20,
    )
    max_calls = _codex_int_env("JK_CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE", CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE, 1, 10)
    current_prompt = initial_prompt
    previous_results: list[dict[str, Any]] = []
    api_query_deadline: Optional[float] = None
    trace: dict[str, Any] = {
        "agent_steps": [],
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": [],
        "warnings": [],
        "token_usage": {},
    }
    final_state: dict[str, Any] = {"items": [], "live_answer": "", "reasoning_summary": "", "live_plan": ""}
    final_response = ""
    mcp_seen: set[str] = set()
    started_monotonic = time.monotonic()
    deadline_seconds = max(30, min(int(task.get("deadline_seconds") or (600 if report_mode else 180)), 600))
    source_policy = _codex_agent_source_policy_for_task(task)
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}

    for cycle in range(1, max_cycles + 1):
        if time.monotonic() - started_monotonic >= deadline_seconds:
            trace["deadline_exceeded"] = True
            trace["warnings"].append(f"Limite seguro de {deadline_seconds} segundos atingido.")
            final_response = (
                "A consulta atingiu o limite seguro de tempo. "
                "Vou apresentar somente os dados que consegui confirmar ate aqui, sem completar informacoes por suposicao."
            )
            break
        status = "interpretando pergunta" if cycle == 1 else f"analisando resultados do ciclo {cycle - 1}"
        trace["agent_steps"].append({"cycle": cycle, "status": status, "at": _codex_now()})
        _codex_agent_update_trace(task_id, trace, live_status=status, live_answer="")
        turn = thread.turn(current_prompt, **run_kwargs)
        state: dict[str, Any] = {"items": [], "live_answer": "", "reasoning_summary": "", "live_plan": ""}
        _codex_update_live(task_id, live_status=status, wait_reason="codex_turn", live_answer="")
        _codex_register_active_turn(task_id, turn)
        try:
            for event in turn.stream():
                _codex_process_stream_event(task_id, task, event, state)
        finally:
            _codex_unregister_active_turn(task_id, turn)

        completed_turn = state.get("completed_turn")
        if completed_turn is not None:
            status_value = str(getattr(getattr(completed_turn, "status", None), "value", getattr(completed_turn, "status", "")) or "")
            if status_value == "failed":
                error = getattr(completed_turn, "error", None)
                message = str(getattr(error, "message", "") or "turn failed with status failed")
                raise RuntimeError(message)

        response = _codex_final_response_from_items(
            list(state.get("items") or []),
            fallback=str(state.get("live_answer") or ""),
        )
        if isinstance(state.get("token_usage"), dict):
            trace["token_usage"] = state.get("token_usage") or {}
        mcp_cycle_results = _codex_native_mcp_read_results(task_id, mcp_seen)
        for result in mcp_cycle_results:
            tool_id = str(result.get("tool_id") or "")
            raw_sources = list(result.get("sources_raw") or [])[:8]
            human_sources = list(result.get("sources_human") or result.get("sources") or [])[:8]
            source_label = str(result.get("source_label") or ((human_sources[:1] or raw_sources[:1] or [""])[0]) or "")
            validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
            summary = {
                "cycle": cycle,
                "tool_id": tool_id,
                "tool_label": result.get("tool_label") or _codex_agent_tool_status(tool_id),
                "module": result.get("module") or "",
                "records": int(result.get("records") or 0),
                "success": bool(result.get("success")),
                "sources": human_sources or raw_sources,
                "sources_raw": raw_sources,
                "sources_human": human_sources,
                "source": source_label,
                "source_label": source_label,
                "warnings": list(result.get("warnings") or [])[:6],
                "empty_reason": str(result.get("empty_reason") or result.get("error") or "")[:900],
                "tool_validation": validation,
                "confidence": str(validation.get("confidence") or ""),
                "next_fallbacks": list(validation.get("proximas_fontes_humanas") or validation.get("proximas_fontes") or [])[:8],
                "generated_at": result.get("generated_at") or _codex_now(),
            }
            trace["tool_calls"].append({"cycle": cycle, "tool_id": tool_id, "protocol": "mcp", "at": _codex_now()})
            trace["tool_results_summary"].append(summary)
            trace["agent_steps"].append({"cycle": cycle, "status": f"consulta MCP concluida: {_codex_agent_tool_status(tool_id)}", "tool_id": tool_id, "at": _codex_now()})
            _codex_agent_unique_extend(trace["sources"], human_sources or raw_sources)
            _codex_agent_unique_extend(trace["warnings"], list(result.get("warnings") or []))
            previous_results.append(result)
        if mcp_cycle_results:
            _codex_agent_update_trace(task_id, trace, live_status="validando resultados das consultas", wait_reason="data_validation")
        calls, parse_error = _codex_agent_extract_tool_calls(response)
        if parse_error:
            trace["warnings"].append(parse_error)
            current_prompt = (
                "O bloco jk_tool_calls anterior estava invalido. Reenvie somente JSON valido dentro de "
                "<jk_tool_calls>...</jk_tool_calls>, ou responda ao usuario se nao precisar de ferramenta.\n"
                f"Erro: {parse_error}"
            )
            _codex_agent_update_trace(task_id, trace, live_status="corrigindo chamada de ferramenta")
            final_state = state
            continue

        calls = _codex_agent_order_calls_by_source_policy(calls, source_policy)

        if not calls:
            attempted = {str(item.get("tool_id") or "") for item in previous_results if isinstance(item, dict)}
            pending_fallbacks: list[str] = []
            for result in mcp_cycle_results:
                validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
                if validation.get("dados_suficientes") is False:
                    for item in validation.get("proximas_fontes") or []:
                        tool_id = str(item or "").strip()
                        if tool_id and tool_id not in attempted and tool_id not in pending_fallbacks:
                            pending_fallbacks.append(tool_id)
            if pending_fallbacks and cycle < max_cycles:
                if str(task.get("reasoning_policy") or "") == "adaptive":
                    levels = ("low", "medium", "high", "xhigh")
                    current_level = str((_codex_load_task(task_id) or task).get("reasoning_level") or task.get("reasoning_effort") or "medium")
                    maximum = str(task.get("reasoning_max") or "xhigh")
                    try:
                        next_level = levels[min(levels.index(current_level) + 1, levels.index(maximum))]
                    except ValueError:
                        next_level = "high"
                    run_kwargs["effort"] = _codex_reasoning_effort_enum(next_level)
                    _codex_update_task(task_id, reasoning_level=next_level)
                current_prompt = (
                    "Os dados ainda nao sao suficientes. Continue pelas proximas fontes autorizadas, sem repetir chamadas: "
                    + ", ".join(pending_fallbacks[:8])
                    + ". Se nenhuma estiver disponivel, responda apenas com o que foi confirmado e declare a lacuna."
                )
                final_state = state
                continue
            final_response = (
                _codex_exact_ml_order_response(task, previous_results, response)
                or _codex_whatsapp_complete_ml_report(task, previous_results)
                or _codex_whatsapp_bling_stock_response(task, previous_results)
                or response
            )
            final_state = state
            break

        if cycle >= max_cycles:
            final_response = _codex_exact_ml_order_response(task, previous_results) or (
                "Nao consegui concluir a resposta dentro do limite de ciclos do agente. "
                "Ferramentas solicitadas: "
                + ", ".join(str(call.get("tool_id") or "") for call in calls)
            )
            final_state = state
            trace["warnings"].append("Limite de ciclos do agente atingido antes da resposta final.")
            break

        cycle_results: list[dict[str, Any]] = []
        external_safe_mode = bool(task.get("external_safe_mode"))
        read_only_channel_mode = bool(external_safe_mode or _codex_task_whatsapp_query_only(task))
        external_allowed_tools = {
            str(item.get("id") or "")
            for item in _codex_agent_tool_catalog(
                task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                read_only_only=True,
            )
        } if read_only_channel_mode else set()
        for call in calls[:max_calls]:
            if time.monotonic() - started_monotonic >= deadline_seconds:
                trace["deadline_exceeded"] = True
                trace["warnings"].append(f"Limite seguro de {deadline_seconds} segundos atingido durante as consultas.")
                break
            tool_id = str(call.get("tool_id") or "").strip()
            args = dict(call.get("args") or {}) if isinstance(call.get("args"), dict) else {}
            if tool_id in set(source_policy.get("required_tools") or []):
                args["force_refresh"] = bool(source_policy.get("force_refresh", True))
                if tool_id == "mercado_livre_listing" and source_policy.get("include_listing_details") is True:
                    args["incluir_detalhes"] = True
            args, call_complete_ml_report = _codex_whatsapp_prepare_agent_tool_call(
                task,
                tool_id,
                args,
                previous_results + cycle_results,
            )
            label = _codex_agent_tool_status(tool_id)
            trace_call = {
                "cycle": cycle,
                "tool_id": tool_id,
                "args": args,
                "reason": call.get("reason") or "",
                "at": _codex_now(),
            }
            trace["tool_calls"].append(trace_call)
            trace["agent_steps"].append({"cycle": cycle, "status": f"solicitando ferramenta: {label}", "tool_id": tool_id, "at": _codex_now()})
            _codex_agent_update_trace(
                task_id,
                trace,
                live_status=f"solicitando ferramenta: {label}",
                live_answer="",
                wait_reason="api_query",
            )
            try:
                from backend.services import codex_assistant

                if tool_id in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_listing"}:
                    if call_complete_ml_report and tool_id == "mercado_livre_orders":
                        api_query_deadline = time.monotonic() + 300
                    elif api_query_deadline is None:
                        request_text = _codex_texto_sem_acentos(_codex_whatsapp_user_request_text(task))
                        latest_direct = bool(
                            tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
                            and re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", request_text)
                        )
                        api_query_deadline = time.monotonic() + (25 if latest_direct else 60)
                if read_only_channel_mode and tool_id not in external_allowed_tools:
                    result = {
                        "success": False,
                        "tool_id": tool_id,
                        "error": "Ferramenta bloqueada para tarefa originada fora do aplicativo.",
                        "records": 0,
                        "warnings": ["Somente consultas read-only sao permitidas no canal WhatsApp."],
                        "generated_at": _codex_now(),
                    }
                elif _codex_agent_source_policy_error(tool_id, source_policy, previous_results + cycle_results):
                    policy_error = _codex_agent_source_policy_error(tool_id, source_policy, previous_results + cycle_results)
                    result = {
                        "success": False,
                        "tool_id": tool_id,
                        "error": policy_error,
                        "records": 0,
                        "warnings": [policy_error],
                        "generated_at": _codex_now(),
                    }
                else:
                    result = codex_assistant.codex_assistant_execute_tool_call(
                        client_id=str(task.get("client_id") or ""),
                        tool_id=tool_id,
                        args=args,
                        screen_context=screen_context if isinstance(screen_context, dict) else {},
                        previous_results=previous_results + cycle_results,
                        permissions=task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                        audit_user=str(task.get("username") or task.get("created_by") or ""),
                        query_deadline=api_query_deadline,
                    )
            except Exception as exc:
                result = {
                    "success": False,
                    "tool_id": tool_id,
                    "error": str(exc)[:600],
                    "records": 0,
                    "warnings": [str(exc)[:600]],
                    "generated_at": _codex_now(),
                }
            raw_sources = list(result.get("sources_raw") or [])[:8]
            human_sources = list(result.get("sources_human") or result.get("sources") or [])[:8]
            source_label = str(result.get("source_label") or ((human_sources[:1] or raw_sources[:1] or [""])[0]) or "")
            next_fallbacks_raw = (
                list((result.get("tool_validation") or {}).get("proximas_fontes") or result.get("next_fallbacks") or [])[:8]
                if isinstance(result.get("tool_validation"), dict)
                else list(result.get("next_fallbacks") or [])[:8]
            )
            next_fallbacks_human = (
                list((result.get("tool_validation") or {}).get("proximas_fontes_humanas") or result.get("next_fallbacks_human") or [])[:8]
                if isinstance(result.get("tool_validation"), dict)
                else list(result.get("next_fallbacks_human") or [])[:8]
            )
            result_paging: dict[str, Any] = {}
            for summary_item in result.get("summary") if isinstance(result.get("summary"), list) else []:
                if not isinstance(summary_item, dict):
                    continue
                summary_payload = summary_item.get("summary") if isinstance(summary_item.get("summary"), dict) else {}
                paging = summary_payload.get("paging") if isinstance(summary_payload.get("paging"), dict) else {}
                if paging:
                    result_paging = dict(paging)
                    break
            result_summary = {
                "cycle": cycle,
                "tool_id": result.get("tool_id") or tool_id,
                "tool_label": result.get("tool_label") or label,
                "module": result.get("module") or "",
                "records": int(result.get("records") or 0),
                "success": bool(result.get("success")),
                "sources": human_sources or raw_sources,
                "sources_raw": raw_sources,
                "sources_human": human_sources,
                "source": source_label,
                "source_label": source_label,
                "warnings": list(result.get("warnings") or [])[:6],
                "empty_reason": str(result.get("empty_reason") or result.get("error") or "")[:900],
                "tool_validation": result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {},
                "confidence": str((result.get("tool_validation") or {}).get("confidence") or "") if isinstance(result.get("tool_validation"), dict) else "",
                "next_fallbacks": next_fallbacks_human or next_fallbacks_raw,
                "next_fallbacks_raw": next_fallbacks_raw,
                "next_fallbacks_human": next_fallbacks_human,
                "failures": [
                    item
                    for item in [str(result.get("empty_reason") or result.get("error") or "")[:600]]
                    + [str(warning or "")[:300] for warning in list(result.get("warnings") or [])[:4]]
                    if str(item or "").strip()
                ],
                "paging": result_paging,
                "generated_at": result.get("generated_at") or _codex_now(),
            }
            trace["tool_results_summary"].append(result_summary)
            _codex_agent_unique_extend(trace["sources"], human_sources or raw_sources)
            _codex_agent_unique_extend(trace["warnings"], list(result.get("warnings") or []))
            if result.get("empty_reason"):
                _codex_agent_unique_extend(trace["warnings"], [str(result.get("empty_reason"))])
            cycle_results.append(result)
            previous_results.append(result)
            validation = result_summary.get("tool_validation") if isinstance(result_summary.get("tool_validation"), dict) else {}
            if validation:
                confidence = str(validation.get("confidence") or "").strip()
                records = int(result_summary.get("records") or 0)
                status_validation = (
                    f"validando dados: suficientes | {records} registros | confianca {confidence or '-'}"
                    if validation.get("dados_suficientes")
                    else f"validando dados: tentando fallback | {records} registros | confianca {confidence or '-'}"
                )
                trace["agent_steps"].append(
                    {
                        "cycle": cycle,
                        "status": status_validation,
                        "tool_id": tool_id,
                        "next_fallbacks": next_fallbacks_human or list(validation.get("proximas_fontes") or [])[:8],
                        "at": _codex_now(),
                    }
                )
                _codex_agent_update_trace(task_id, trace, live_status=status_validation)
            else:
                _codex_agent_update_trace(task_id, trace, live_status=f"{label} concluido")

        if trace.get("deadline_exceeded"):
            confirmed = [item for item in trace.get("tool_results_summary") or [] if item.get("success")]
            final_response = (
                "A consulta atingiu o limite seguro de tempo. "
                f"Foram preservados {len(confirmed)} resultado(s) confirmado(s), mas ainda faltam dados para uma conclusao completa."
            )
            final_state = state
            break

        if str(task.get("reasoning_policy") or "") == "adaptive":
            needs_more_reasoning = any(
                isinstance(item.get("tool_validation"), dict)
                and item.get("tool_validation", {}).get("dados_suficientes") is False
                for item in trace.get("tool_results_summary") or []
                if isinstance(item, dict)
            )
            if needs_more_reasoning:
                levels = ("low", "medium", "high", "xhigh")
                current_level = str((_codex_load_task(task_id) or task).get("reasoning_level") or task.get("reasoning_effort") or "medium")
                maximum = str(task.get("reasoning_max") or "xhigh")
                try:
                    next_index = min(levels.index(current_level) + 1, levels.index(maximum))
                except ValueError:
                    next_index = levels.index("high")
                next_level = levels[next_index]
                if next_level != current_level:
                    run_kwargs["effort"] = _codex_reasoning_effort_enum(next_level)
                    _codex_update_task(
                        task_id,
                        reasoning_level=next_level,
                        live_status=f"Aprofundando a analise para validar dados ainda insuficientes ({next_level}).",
                        wait_reason="data_validation",
                    )

        current_prompt = _codex_agent_results_prompt(cycle, cycle_results)
        final_state = state
        trace["agent_steps"].append({"cycle": cycle, "status": "gerando proximo passo", "at": _codex_now()})
        _codex_agent_update_trace(task_id, trace, live_status="gerando proximo passo", live_answer="")

    if not final_response:
        final_response = "Nao consegui gerar uma resposta final nesta execucao do agente."
    trace["token_usage"] = final_state.get("token_usage") if isinstance(final_state.get("token_usage"), dict) else trace.get("token_usage") or {}
    # Gere os graficos antes de descartar ``previous_results``. O texto final
    # nunca e usado como fonte numerica e uma falha visual nao afeta a tarefa.
    _codex_generate_whatsapp_chart_artifacts(task_id, task, previous_results)
    return final_response, final_state, trace


def _codex_model_dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", by_alias=False)
        except TypeError:
            return value.model_dump()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_codex_model_dump(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _codex_model_dump(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _codex_token_usage_dict(value: Any) -> dict[str, Any]:
    dumped = _codex_model_dump(value)
    return dumped if isinstance(dumped, dict) else {}


def _codex_final_response_from_items(items: list[Any], fallback: str = "") -> str:
    last_text = ""
    for item in reversed(items):
        root = getattr(item, "root", item)
        text = str(getattr(root, "text", "") or "").strip()
        if not text:
            continue
        phase = getattr(root, "phase", None)
        phase_value = str(getattr(phase, "value", phase) or "")
        if phase_value == "final_answer":
            return text
        if not last_text:
            last_text = text
    return last_text or str(fallback or "").strip()


def _codex_item_label(item: Any) -> str:
    root = getattr(item, "root", item)
    item_type = str(getattr(root, "type", "") or root.__class__.__name__)
    labels = {
        "agent_message": "Gerando resposta",
        "reasoning": "Raciocinando",
        "command_execution": "Executando comando",
        "file_change": "Preparando alteracao de arquivo",
        "plan": "Atualizando plano",
        "mcp_tool_call": "Usando ferramenta",
        "dynamic_tool_call": "Usando ferramenta",
        "web_search": "Pesquisando",
        "collab_agent_tool_call": "Usando agente auxiliar",
    }
    return labels.get(item_type, item_type or "Etapa")


def _codex_plan_text(plan: Any, explanation: Optional[str] = None) -> str:
    parts: list[str] = []
    if explanation:
        parts.append(str(explanation).strip())
    if isinstance(plan, list):
        for idx, step in enumerate(plan[:12], 1):
            dumped = _codex_model_dump(step)
            if isinstance(dumped, dict):
                text = dumped.get("step") or dumped.get("text") or dumped.get("title") or dumped.get("description") or str(dumped)
                status = dumped.get("status") or ""
                parts.append(f"{idx}. {text}" + (f" [{status}]" if status else ""))
            else:
                parts.append(f"{idx}. {dumped}")
    return "\n".join(part for part in parts if str(part).strip())[:4000]


def _codex_update_live(task_id: str, **updates: Any) -> dict[str, Any]:
    with CODEX_TASKS_LOCK:
        task = CODEX_TASKS.get(task_id)
        if not task:
            raise KeyError(task_id)
        task.update(updates)
        _codex_persist_task(task)
        return task


def _codex_register_active_turn(task_id: str, turn: Any) -> None:
    turn_id = str(getattr(turn, "id", "") or "").strip()
    with CODEX_ACTIVE_TURNS_LOCK:
        CODEX_ACTIVE_TURNS[task_id] = turn
    _codex_update_live(task_id, turn_id=turn_id, active_turn_id=turn_id, can_steer=bool(turn_id))


def _codex_unregister_active_turn(task_id: str, turn: Any = None) -> None:
    with CODEX_ACTIVE_TURNS_LOCK:
        current = CODEX_ACTIVE_TURNS.get(task_id)
        if turn is None or current is turn:
            CODEX_ACTIVE_TURNS.pop(task_id, None)
    try:
        _codex_update_live(task_id, active_turn_id="", can_steer=False)
    except KeyError:
        pass


def _codex_interrupt_active_turn(task_id: str) -> bool:
    with CODEX_ACTIVE_TURNS_LOCK:
        turn = CODEX_ACTIVE_TURNS.get(str(task_id or ""))
    if turn is None:
        return False
    try:
        turn.interrupt()
        return True
    except Exception:
        return False


def _codex_process_stream_event(
    task_id: str,
    task: dict[str, Any],
    event: Any,
    state: dict[str, Any],
) -> None:
    method = str(getattr(event, "method", "") or "")
    payload = getattr(event, "payload", None)
    now = time.time()

    def maybe_update(force: bool = False, **updates: Any) -> None:
        last = float(state.get("last_update_at") or 0)
        if not force and now - last < 0.75:
            state.setdefault("pending_updates", {}).update(updates)
            return
        pending = state.pop("pending_updates", {})
        pending.update(updates)
        if pending:
            _codex_update_live(task_id, **pending)
        state["last_update_at"] = now

    if method == "turn/started":
        _codex_log(task, "Turno iniciado no Codex.", "event")
        maybe_update(True, live_status="Turno iniciado no Codex.", wait_reason="codex_turn")
        return

    if method == "item/started":
        label = _codex_item_label(getattr(payload, "item", None))
        _codex_log(task, f"{label}...", "event")
        maybe_update(True, live_status=f"{label}...")
        return

    if method == "item/completed":
        item = getattr(payload, "item", None)
        if item is not None:
            state.setdefault("items", []).append(item)
        label = _codex_item_label(item)
        _codex_log(task, f"{label} concluido.", "event")
        maybe_update(live_status=f"{label} concluido.")
        return

    if method == "item/agentMessage/delta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["live_answer"] = (state.get("live_answer") or "") + delta
            maybe_update(live_status="Gerando resposta...", live_answer=state["live_answer"][-8000:])
        return

    if method == "item/reasoning/summaryPartAdded":
        _codex_log(task, "Codex iniciou um resumo de raciocinio.", "reasoning")
        maybe_update(live_status="Raciocinando com resumo disponivel...", wait_reason="data_analysis")
        return

    if method == "item/reasoning/summaryTextDelta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["reasoning_summary"] = (state.get("reasoning_summary") or "") + delta
            maybe_update(live_status="Raciocinando...", wait_reason="data_analysis", reasoning_summary=state["reasoning_summary"][-8000:])
        return

    if method == "item/reasoning/textDelta":
        maybe_update(live_status="Raciocinando...")
        return

    if method == "item/plan/delta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["live_plan"] = (state.get("live_plan") or "") + delta
            maybe_update(live_status="Atualizando plano...", live_plan=state["live_plan"][-6000:])
        return

    if method == "turn/plan/updated":
        plan_text = _codex_plan_text(getattr(payload, "plan", None), getattr(payload, "explanation", None))
        if plan_text:
            _codex_log(task, "Plano atualizado.", "plan")
            maybe_update(True, live_status="Plano atualizado.", live_plan=plan_text)
        return

    if method in {"item/commandExecution/outputDelta", "command/exec/outputDelta"}:
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            text = delta.replace("\r", "").strip()
            if text:
                _codex_log(task, "Saida de comando: " + text[:300], "command")
        maybe_update(live_status="Executando comando...")
        return

    if method == "item/fileChange/patchUpdated":
        changes = getattr(payload, "changes", None) or []
        _codex_log(task, f"Patch atualizado ({len(changes)} alteracao/alteracoes).", "file")
        maybe_update(True, live_status="Preparando alteracao de arquivo...")
        return

    if method == "item/mcpToolCall/progress":
        message = str(getattr(payload, "message", "") or "").strip()
        if message:
            _codex_log(task, message, "tool")
            maybe_update(live_status=message[:240], wait_reason="api_query")
        return

    if method == "model/rerouted":
        from_model = str(getattr(payload, "from_model", "") or "")
        to_model = str(getattr(payload, "to_model", "") or "")
        message = f"Modelo redirecionado: {from_model} -> {to_model}".strip()
        _codex_log(task, message, "event")
        maybe_update(True, live_status=message)
        return

    if method == "thread/tokenUsage/updated":
        usage = _codex_token_usage_dict(getattr(payload, "token_usage", None))
        if usage:
            state["token_usage"] = usage
            maybe_update(token_usage=usage)
        return

    if method == "turn/completed":
        turn = getattr(payload, "turn", None)
        state["completed_turn"] = turn
        usage = _codex_token_usage_dict(getattr(payload, "token_usage", None))
        if usage:
            state["token_usage"] = usage
        maybe_update(True, live_status="Codex concluiu o turno.")
        return

    if method == "error":
        message = str(getattr(payload, "message", "") or getattr(payload, "error", "") or "Erro no stream do Codex.")
        _codex_log(task, message, "error")
        maybe_update(True, live_status=message[:240])
        return

    if method == "warning":
        message = str(getattr(payload, "message", "") or "Aviso do Codex.")
        _codex_log(task, message, "warning")
        maybe_update(live_status=message[:240])


def _codex_normalizar_model(value: Optional[str]) -> str:
    model = str(value or os.getenv("JK_CODEX_MODEL") or CODEX_DEFAULT_MODEL).strip()
    if not model:
        return CODEX_DEFAULT_MODEL
    if len(model) > 80 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", model):
        raise HTTPException(status_code=400, detail="Modelo Codex invalido.")
    return model


def _codex_normalizar_reasoning_effort(value: Optional[str]) -> str:
    raw = str(value or os.getenv("JK_CODEX_REASONING_EFFORT") or "xhigh").strip().lower()
    aliases = {
        "nenhum": "none",
        "none": "none",
        "minimo": "minimal",
        "minimal": "minimal",
        "baixa": "low",
        "baixo": "low",
        "low": "low",
        "media": "medium",
        "medio": "medium",
        "medium": "medium",
        "alta": "high",
        "alto": "high",
        "high": "high",
        "altissimo": "xhigh",
        "altissima": "xhigh",
        "xhigh": "xhigh",
    }
    effort = aliases.get(raw, raw)
    if effort not in CODEX_REASONING_EFFORTS:
        raise HTTPException(status_code=400, detail="Nivel de raciocinio Codex invalido.")
    return effort


def _codex_reasoning_effort_enum(value: Optional[str]):
    effort = _codex_normalizar_reasoning_effort(value)
    from openai_codex.generated.v2_all import ReasoningEffort

    return getattr(ReasoningEffort, effort)


def _codex_sdk_response_compat(value: Any) -> Any:
    """Keep the beta Python SDK compatible with newer Codex protocol values."""
    if isinstance(value, dict):
        return {
            key: (
                "xhigh"
                if key == "reasoningEffort" and item in {"max", "ultra"}
                else _codex_sdk_response_compat(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_codex_sdk_response_compat(item) for item in value]
    return value


def _codex_apply_sdk_protocol_compat() -> None:
    from openai_codex.client import CodexClient
    from openai_codex.errors import CodexError

    if getattr(CodexClient.request, "_jk_protocol_compat", False):
        return

    def request_compat(self, method, params, *, response_model):
        result = self._request_raw(method, params)
        if not isinstance(result, dict):
            raise CodexError(f"{method} response must be a JSON object")
        return response_model.model_validate(_codex_sdk_response_compat(result))

    request_compat._jk_protocol_compat = True
    CodexClient.request = request_compat


def _codex_normalizar_speed(value: Optional[str]) -> str:
    raw = str(value or os.getenv("JK_CODEX_SPEED") or "standard").strip().lower()
    aliases = {
        "padrao": "standard",
        "standard": "standard",
        "default": "standard",
        "rapido": "fast",
        "fast": "fast",
    }
    speed = aliases.get(raw, raw)
    if speed not in CODEX_SPEEDS:
        raise HTTPException(status_code=400, detail="Velocidade Codex invalida.")
    return speed


def _codex_normalizar_service_tier(value: Optional[str], speed: str) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if raw in {"", "standard", "default", "none"}:
        # O catalogo do app-server identifica o modo Fast pelo tier interno
        # ``priority`` (nome exibido: Fast). A flag ``features.fast_mode`` e
        # habilitada separadamente no runtime que executa a tarefa.
        return "priority" if speed == "fast" else None
    if raw in {"priority", "auto", "flex"}:
        return raw
    raise HTTPException(status_code=400, detail="Service tier Codex invalido.")


def _codex_normalizar_approval_profile(value: Optional[str], sandbox: str) -> str:
    raw = str(value or "").strip().lower()
    if sandbox == "full_access":
        return "full_access"
    if sandbox == "read_only":
        return "read_only"
    aliases = {
        "request": "request",
        "ask": "request",
        "solicitar": "request",
        "solicitar_aprovacao": "request",
        "auto": "auto",
        "auto_review": "auto",
        "aprovar_por_mim": "auto",
        "full": "full_access",
        "full_access": "full_access",
        "acesso_completo": "full_access",
        "deny_all": "read_only",
        "read_only": "read_only",
    }
    return aliases.get(raw, "request")


def _codex_approval_mode_enum(approval_profile: str, sandbox: str):
    from openai_codex import ApprovalMode

    if sandbox == "read_only" or approval_profile == "read_only":
        return ApprovalMode.deny_all
    return ApprovalMode.auto_review


def _codex_resolver_paths(raw_paths: Optional[list[str]], allow_external_for_task: bool = False) -> list[str]:
    if not raw_paths:
        return []
    base = _codex_base_dir()
    allow_external = allow_external_for_task or _codex_bool_env("JK_CODEX_ALLOW_EXTERNAL_PATHS", False)
    paths: list[str] = []
    for raw in raw_paths[:CODEX_PATHS_MAX_COUNT]:
        text = str(raw or "").strip().strip('"').strip("'")
        if not text:
            continue
        candidate = text
        if not os.path.isabs(candidate):
            candidate = os.path.join(base, candidate)
        candidate = os.path.abspath(os.path.expanduser(candidate))
        if not allow_external:
            try:
                common = os.path.commonpath([base, candidate])
            except Exception:
                common = ""
            if common != base:
                raise HTTPException(status_code=400, detail="Arquivo ou pasta fora do workspace nao permitido.")
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _codex_resolver_paths_for_session(
    raw_paths: Optional[list[str]],
    sessao: dict[str, Any],
    conversation_id: str,
    *,
    allow_external_for_admin: bool = False,
) -> list[str]:
    if bool(sessao.get("is_full")):
        return _codex_resolver_paths(raw_paths, allow_external_for_admin)
    if not raw_paths:
        return []
    raise HTTPException(
        status_code=403,
        detail="Anexos e caminhos do sistema exigem permissao de administrador full.",
    )


def _codex_readonly_cwd_for_session(sessao: dict[str, Any], conversation_id: str) -> str:
    # Fica fora do codigo e dos dados do app para que a descoberta de projeto do
    # Codex nunca transforme o repositorio inteiro em workspace legivel.
    local_root = str(os.getenv("LOCALAPPDATA") or "").strip()
    root = (
        Path(local_root) / "JK Sistema Cliente" / "black_jhon_readonly"
        if local_root
        else Path.home() / ".jk-sistema" / "black_jhon_readonly"
    )
    path = (
        root
        / _codex_safe_id(str(sessao.get("client_id") or "default"))
        / _codex_safe_id(str(sessao.get("username") or "user"), "user")
        / _codex_safe_id(conversation_id)
    )
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def _codex_resolver_cwd(raw: Optional[str]) -> str:
    base = _codex_base_dir()
    if not raw or not _codex_bool_env("JK_CODEX_ALLOW_CUSTOM_CWD", False):
        return base
    candidate = os.path.abspath(os.path.expanduser(str(raw)))
    if not _codex_bool_env("JK_CODEX_ALLOW_EXTERNAL_CWD", False):
        try:
            common = os.path.commonpath([base, candidate])
        except Exception:
            common = ""
        if common != base:
            raise HTTPException(status_code=400, detail="cwd fora do workspace nao permitido.")
    return candidate


def _codex_normalizar_modulo_scope(value: Any) -> str:
    text = str(value or "").strip().lower().replace("\\", "/")
    text = text.replace("/static/", "/").strip("/")
    if text.endswith(".html"):
        text = text[:-5]
    text = text.replace("/", "_")
    text = _codex_texto_sem_acentos(text)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _codex_scope_modules_from_screen(screen_context: Any) -> list[str]:
    if not isinstance(screen_context, dict):
        return []
    raw = (
        screen_context.get("modulo_atual")
        or screen_context.get("pathname")
        or screen_context.get("url")
        or screen_context.get("title")
        or ""
    )
    module = _codex_normalizar_modulo_scope(raw)
    aliases = {
        "anunciosml": ["mercadolivre", "mercado_livre", "anunciosml"],
        "anunciosml_campanha": ["mercadolivre", "mercado_livre", "promocoes", "anunciosml_campanha"],
        "dashboard": ["dashboard"],
        "devolucoes": ["vendas", "devolucoes"],
        "devolucoes_sku": ["vendas", "devolucoes", "devolucoes_sku"],
        "frontend_promo": ["promocoes", "frontend_promo", "promo"],
        "importacoes_lista": ["importacoes", "cadastro", "importacoes_lista"],
        "importacoes_sku": ["importacoes", "cadastro", "importacoes_sku"],
        "perguntas_pos_venda": ["perguntas_pos_venda", "mercadolivre", "mercado_livre"],
        "pesquisa_ml": ["mercadolivre", "mercado_livre", "pesquisa_ml"],
        "produtos_sem_venda": ["vendas", "estoque", "produtos_sem_venda"],
        "promo": ["promocoes", "frontend_promo", "promo"],
        "simulador": ["favoritos", "simulador"],
        "vendas_sku": ["vendas", "vendas_sku"],
    }
    modules = aliases.get(module, [module] if module and module not in {"inicio", "frontend_index"} else [])
    result: list[str] = []
    for item in modules:
        normalized = _codex_normalizar_modulo_scope(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _codex_prompt_pede_escopo_amplo(prompt: str) -> bool:
    text = _codex_texto_sem_acentos(prompt)
    patterns = (
        r"\b(todo o app|app inteiro|programa inteiro|sistema inteiro|projeto inteiro)\b",
        r"\b(varredura|validacao completa|validar tudo|verifique todo|verificar todo)\b",
        r"\b(release|publicar|build|installer|instalador|empacotar|deploy)\b",
        r"\b(modularizar backend|backend_api|arquitetura geral|infraestrutura)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _codex_relpath(path: str, base: Optional[str] = None) -> str:
    root = os.path.abspath(base or _codex_base_dir())
    candidate = os.path.abspath(os.path.expanduser(str(path or "")))
    try:
        rel = os.path.relpath(candidate, root)
    except Exception:
        rel = candidate
    return rel.replace("\\", "/").lstrip("./")


def _codex_scope_entries_for_module(module: str) -> tuple[list[str], list[str]]:
    module = _codex_normalizar_modulo_scope(module)
    if not module:
        return [], []
    exact = [
        f"{module}.html",
        f"{module}.py",
        f"backend/routers/{module}.py",
        f"backend/schemas/{module}.py",
        f"backend/services/{module}.py",
        f"static/{module}.html",
    ]
    prefixes = [
        f"backend/schemas/{module}_",
        f"backend/services/{module}_",
        f"static/{module}/",
        f"static/{module}_",
        f"design_previews/{module}",
    ]
    return exact, prefixes


def _codex_build_scope(
    *,
    prompt: str,
    sandbox: str,
    paths: list[str],
    screen_context: Any,
    cwd: str,
) -> dict[str, Any]:
    modules = _codex_scope_modules_from_screen(screen_context)
    broad = _codex_prompt_pede_escopo_amplo(prompt)
    explicit_exact: list[str] = []
    explicit_prefixes: list[str] = []
    for path in paths:
        candidate = os.path.abspath(os.path.expanduser(str(path or "")))
        rel = _codex_relpath(candidate, cwd)
        if os.path.isdir(candidate):
            prefix = rel.rstrip("/") + "/"
            if prefix not in explicit_prefixes:
                explicit_prefixes.append(prefix)
        elif rel and rel not in explicit_exact:
            explicit_exact.append(rel)

    allowed_exact: list[str] = []
    allowed_prefixes: list[str] = []
    if not paths:
        for module in modules:
            exact, prefixes = _codex_scope_entries_for_module(module)
            for item in exact:
                if item not in allowed_exact:
                    allowed_exact.append(item)
            for item in prefixes:
                if item not in allowed_prefixes:
                    allowed_prefixes.append(item)

    for item in explicit_exact:
        if item not in allowed_exact:
            allowed_exact.append(item)
    for item in explicit_prefixes:
        if item not in allowed_prefixes:
            allowed_prefixes.append(item)

    enforced = bool(
        sandbox == "workspace_write"
        and not broad
        and (allowed_exact or allowed_prefixes)
    )
    if paths:
        enforced = sandbox in {"workspace_write", "full_access"} and bool(allowed_exact or allowed_prefixes)

    reason = "explicit_paths" if paths else "screen_module"
    if broad:
        reason = "broad_request"
    if sandbox == "read_only":
        reason = "read_only"
    return {
        "enforced": enforced,
        "reason": reason,
        "broad_request": broad,
        "modules": modules,
        "explicit_paths": explicit_exact + explicit_prefixes,
        "allowed_exact": sorted(allowed_exact),
        "allowed_prefixes": sorted(allowed_prefixes),
        "ignored_prefixes": list(CODEX_SCOPE_RUNTIME_PREFIXES),
    }


def _codex_scope_allows_path(rel_path: str, scope: Any) -> bool:
    rel = str(rel_path or "").replace("\\", "/").lstrip("./")
    rel_lower = rel.lower()
    if not rel:
        return True
    if any(rel_lower.startswith(prefix.lower()) for prefix in CODEX_SCOPE_RUNTIME_PREFIXES):
        return True
    if not isinstance(scope, dict) or not scope.get("enforced"):
        return True
    allowed_exact = {str(item or "").replace("\\", "/").lstrip("./").lower() for item in scope.get("allowed_exact") or []}
    allowed_prefixes = [
        str(item or "").replace("\\", "/").lstrip("./").lower()
        for item in scope.get("allowed_prefixes") or []
        if str(item or "").strip()
    ]
    if rel_lower in allowed_exact:
        return True
    if any(rel_lower.startswith(prefix.rstrip("/") + "/") or rel_lower.startswith(prefix) for prefix in allowed_prefixes):
        return True
    filename = os.path.basename(rel_lower)
    for module in scope.get("modules") or []:
        mod = _codex_normalizar_modulo_scope(module)
        if rel_lower.startswith("tests/") and mod and mod in filename:
            return True
    return False


def _codex_scope_instruction(scope: Any) -> str:
    if not isinstance(scope, dict) or not scope.get("enforced"):
        if isinstance(scope, dict) and scope.get("broad_request"):
            return "Escopo de escrita: pedido amplo detectado; ainda assim evite tocar modulos nao relacionados sem explicar."
        return "Escopo de escrita: mantenha qualquer alteracao no pedido atual e explique arquivos tocados."
    lines = [
        "Escopo de escrita obrigatorio desta tarefa:",
        "Voce so pode criar, editar ou remover arquivos que estejam nesta lista exata ou dentro destes prefixos.",
    ]
    if scope.get("modules"):
        lines.append("Modulos detectados: " + ", ".join(str(item) for item in scope.get("modules") or []))
    if scope.get("allowed_exact"):
        lines.append("Arquivos exatos permitidos:\n" + "\n".join(f"- {item}" for item in scope.get("allowed_exact") or []))
    if scope.get("allowed_prefixes"):
        lines.append("Pastas/prefixos permitidos:\n" + "\n".join(f"- {item}" for item in scope.get("allowed_prefixes") or []))
    lines.append(
        "Se a correcao exigir arquivo fora desse escopo, pare e responda pedindo ampliacao de escopo. "
        "Nao faca refatoracao oportunista, formatacao global ou ajuste em outro modulo."
    )
    return "\n".join(lines)


def _codex_git_root(base: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", base, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return os.path.abspath(proc.stdout.strip())
    except Exception:
        pass
    return os.path.abspath(base)


def _codex_git_status_map(root: str) -> dict[str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    status: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            _, path = path.rsplit(" -> ", 1)
        path = path.strip('"').replace("\\", "/")
        if path:
            status[path] = code
    return status


def _codex_git_tracked_files(root: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    files: list[str] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace").replace("\\", "/")
        ext = os.path.splitext(rel)[1].lower()
        if ext in CODEX_SCOPE_TEXT_EXTENSIONS and not any(rel.lower().startswith(prefix) for prefix in CODEX_SCOPE_RUNTIME_PREFIXES):
            files.append(rel)
    return files


def _codex_scan_text_files(root: str, limit: int = 12000) -> list[str]:
    files: list[str] = []
    root = os.path.abspath(root)
    for current, dirs, names in os.walk(root):
        dirs[:] = [
            dirname
            for dirname in dirs
            if dirname not in CODEX_SCOPE_SNAPSHOT_SKIP_DIRS
            and not dirname.startswith(".cache")
            and not dirname.startswith("tmp_")
        ]
        rel_dir = os.path.relpath(current, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        if any(rel_dir.lower().startswith(prefix.lower().rstrip("/")) for prefix in CODEX_SCOPE_RUNTIME_PREFIXES):
            dirs[:] = []
            continue
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext not in CODEX_SCOPE_TEXT_EXTENSIONS:
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            rel = rel.replace("\\", "/")
            if any(rel.lower().startswith(prefix.lower()) for prefix in CODEX_SCOPE_RUNTIME_PREFIXES):
                continue
            files.append(rel)
            if len(files) >= limit:
                return files
    return files


def _codex_file_sha1(path: str) -> str:
    if not os.path.exists(path):
        return "__missing__"
    if not os.path.isfile(path):
        return "__not_file__"
    try:
        digest = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return "__unreadable__"


def _codex_workspace_snapshot(base: str) -> dict[str, Any]:
    root = _codex_git_root(base)
    tracked_files = _codex_git_tracked_files(root)
    if not tracked_files:
        tracked_files = _codex_scan_text_files(root)
    tracked = {
        rel: _codex_file_sha1(os.path.join(root, rel.replace("/", os.sep)))
        for rel in tracked_files
    }
    return {
        "root": root,
        "tracked": tracked,
        "status": _codex_git_status_map(root),
    }


def _codex_workspace_changed_files(before: Any, after: Any) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    changed: set[str] = set()
    before_status = before.get("status") if isinstance(before.get("status"), dict) else {}
    after_status = after.get("status") if isinstance(after.get("status"), dict) else {}
    for rel, code in after_status.items():
        if before_status.get(rel) != code:
            changed.add(str(rel).replace("\\", "/"))
    before_tracked = before.get("tracked") if isinstance(before.get("tracked"), dict) else {}
    after_tracked = after.get("tracked") if isinstance(after.get("tracked"), dict) else {}
    for rel, digest in after_tracked.items():
        if before_tracked.get(rel) != digest:
            changed.add(str(rel).replace("\\", "/"))
    for rel in before_tracked:
        if rel not in after_tracked:
            changed.add(str(rel).replace("\\", "/"))
    return sorted(changed)


def _codex_scope_violations(scope: Any, changed_files: list[str]) -> list[str]:
    return [rel for rel in changed_files if not _codex_scope_allows_path(rel, scope)]


def _codex_sandbox_enum(sandbox: str):
    from openai_codex import Sandbox

    return {
        "read_only": Sandbox.read_only,
        "workspace_write": Sandbox.workspace_write,
        "full_access": Sandbox.full_access,
    }[sandbox]


def _codex_sdk_env() -> dict[str, str]:
    if _codex_bool_env("JK_CODEX_ALLOW_API_KEY_AUTH", False):
        return {}
    return {key: "" for key in CODEX_API_AUTH_ENV_KEYS}


def _codex_config_file_path() -> Path:
    codex_home = str(os.getenv("CODEX_HOME") or "").strip()
    root = Path(os.path.expanduser(codex_home)) if codex_home else Path.home() / ".codex"
    return root / "config.toml"


def _codex_configured_mcp_server_names() -> list[str]:
    names: set[str] = {"node_repl", "openaiDeveloperDocs"}
    path = _codex_config_file_path()
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        text = ""
    if text:
        try:
            parsed = tomllib.loads(text)
            servers = parsed.get("mcp_servers") if isinstance(parsed, dict) else {}
            if isinstance(servers, dict):
                names.update(str(name).strip() for name in servers if str(name).strip())
        except Exception:
            for match in re.finditer(r"(?m)^\s*\[mcp_servers\.([^\]]+)\]\s*$", text):
                raw = str(match.group(1) or "").strip()
                name = raw.split(".", 1)[0].strip().strip('"').strip("'")
                if name:
                    names.add(name)
    return sorted(names)


def _codex_nonfull_config_overrides(*, fast_mode: bool = False) -> tuple[str, ...]:
    overrides = [
        'default_permissions="jk_black_jhon_readonly"',
        'permissions.jk_black_jhon_readonly.filesystem={":root"="deny",":minimal"="read",":workspace_roots"={"."="read"}}',
        "permissions.jk_black_jhon_readonly.network={enabled=false}",
        "features.shell_tool=false",
        "features.shell_snapshot=false",
        "features.unified_exec=false",
        "features.apps=false",
        "features.auth_elicitation=false",
        "features.browser_use=false",
        "features.browser_use_external=false",
        "features.browser_use_full_cdp_access=false",
        "features.computer_use=false",
        f"features.fast_mode={'true' if fast_mode else 'false'}",
        "features.guardian_approval=false",
        "features.image_generation=false",
        "features.in_app_browser=false",
        "features.plugins=false",
        "features.plugin_sharing=false",
        "features.multi_agent=false",
        "features.memories=false",
        "features.goals=false",
        "features.hooks=false",
        "features.remote_plugin=false",
        "features.skill_mcp_dependency_install=false",
        "features.network_proxy=false",
        "features.tool_call_mcp_elicitation=false",
        "features.tool_suggest=false",
        "features.workspace_dependencies=false",
        "apps._default.enabled=false",
        "tools.web_search=false",
        "tools.view_image=false",
        'web_search="disabled"',
        'history.persistence="none"',
        'shell_environment_policy.inherit="none"',
        "notify=[]",
    ]
    for name in _codex_configured_mcp_server_names():
        if re.fullmatch(r"[A-Za-z0-9_-]+", name):
            key = name
        else:
            key = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
        overrides.append(f"mcp_servers.{key}.enabled=false")
    return tuple(overrides)


def _codex_external_readonly_config_overrides(*, fast_mode: bool = False) -> tuple[str, ...]:
    overrides = [
        item
        for item in _codex_nonfull_config_overrides(fast_mode=fast_mode)
        if item != "tools.view_image=false"
    ]
    overrides.append("tools.view_image=true")
    return tuple(overrides)


def _codex_web_readonly_config_overrides(*, fast_mode: bool = False) -> tuple[str, ...]:
    disabled = {"tools.view_image=false", "tools.web_search=false", 'web_search="disabled"'}
    overrides = [
        item
        for item in _codex_nonfull_config_overrides(fast_mode=fast_mode)
        if item not in disabled
    ]
    overrides.extend(("tools.view_image=true", "tools.web_search=true", 'web_search="live"'))
    return tuple(overrides)


def _codex_dual_worker_web_search_enabled(
    task: dict[str, Any],
    *,
    read_only_channel_mode: bool,
    sandbox: str,
) -> bool:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    profile = str(
        metadata.get("orchestration_profile") or task.get("orchestration_profile") or ""
    ).strip()
    role = str(metadata.get("agent_role") or task.get("agent_role") or "").strip().lower()
    lane = str(metadata.get("agent_lane") or task.get("agent_lane") or "").strip().lower()
    return bool(
        read_only_channel_mode
        and sandbox == "read_only"
        and str(task.get("origin") or "").strip().lower() == "whatsapp"
        and profile == "whatsapp_dual_codex_worker"
        and role == "task"
        and lane == "worker"
        and metadata.get("allow_web_search") is True
    )


def _codex_is_whatsapp_dual_worker(task: dict[str, Any]) -> bool:
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    return bool(
        str(task.get("origin") or "").strip().lower() == "whatsapp"
        and str(metadata.get("orchestration_profile") or task.get("orchestration_profile") or "").strip()
        == "whatsapp_dual_codex_worker"
        and str(metadata.get("agent_role") or task.get("agent_role") or "").strip().lower() == "task"
        and str(metadata.get("agent_lane") or task.get("agent_lane") or "").strip().lower() == "worker"
    )


def _codex_configure_dual_sol_limit(limit: int, per_conversation_limit: Optional[int] = None) -> int:
    return CODEX_DUAL_SOL_GATE.configure(limit, per_conversation_limit)


def _codex_dual_sol_diagnostics() -> dict[str, Any]:
    return CODEX_DUAL_SOL_GATE.diagnostics()


def _codex_task_conversation_gate_key(task: dict[str, Any]) -> str:
    identity = "|".join(
        (
            str(task.get("client_id") or "default").strip().lower(),
            str(task.get("created_by") or "user").strip().lower(),
            _codex_task_channel(task),
            _codex_task_stored_conversation_id(task),
            str(int(task.get("conversation_generation") or 1)),
        )
    )
    return hashlib.sha256(identity.encode("utf-8", "ignore")).hexdigest()


def _codex_task_queue_key(task: dict[str, Any]) -> str:
    base_key = _codex_task_conversation_gate_key(task)
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    group_id = str(metadata.get("job_group_id") or task.get("job_group_id") or "").strip()
    subtask_id = str(metadata.get("subtask_id") or task.get("subtask_id") or "").strip()
    if _codex_is_whatsapp_dual_worker(task) and group_id and subtask_id:
        return hashlib.sha256(
            f"{base_key}|{group_id}|{subtask_id}".encode("utf-8", "ignore")
        ).hexdigest()
    return base_key


def _codex_task_queue_position(task: dict[str, Any]) -> int:
    status = str(task.get("status") or "")
    if status != "queued":
        return 0
    key = _codex_task_queue_key(task)
    with CODEX_TASKS_LOCK:
        queued = [
            item
            for item in CODEX_TASKS.values()
            if isinstance(item, dict)
            and str(item.get("status") or "") == "queued"
            and _codex_task_queue_key(item) == key
        ]
    queued.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")))
    task_id = str(task.get("task_id") or "")
    for index, item in enumerate(queued, start=1):
        if str(item.get("task_id") or "") == task_id:
            return index
    return 0


def _codex_next_queued_task_id(queue_key: str) -> str:
    with CODEX_TASKS_LOCK:
        queued = [
            item
            for item in CODEX_TASKS.values()
            if isinstance(item, dict)
            and str(item.get("status") or "") == "queued"
            and _codex_task_queue_key(item) == queue_key
        ]
    queued.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")))
    return str(queued[0].get("task_id") or "") if queued else ""


def _codex_run_conversation_queue(queue_key: str) -> None:
    try:
        while True:
            task_id = _codex_next_queued_task_id(queue_key)
            if not task_id:
                return
            task = _codex_load_task(task_id)
            dual_worker = isinstance(task, dict) and _codex_is_whatsapp_dual_worker(task)
            acquired_dual_slot = False
            dual_gate_key = _codex_task_conversation_gate_key(task) if isinstance(task, dict) else ""
            if dual_worker:
                _codex_update_task(task_id, sol_queue_wait_started_at=_codex_now(), wait_reason="global_sol_capacity")
                acquired_dual_slot = CODEX_DUAL_SOL_GATE.acquire(task_id, dual_gate_key)
                if not acquired_dual_slot:
                    continue
                _codex_update_task(task_id, sol_started_at=_codex_now(), wait_reason="")
            try:
                _codex_run_worker(task_id)
            finally:
                if acquired_dual_slot:
                    CODEX_DUAL_SOL_GATE.release(dual_gate_key)
    finally:
        with CODEX_QUEUE_LOCK:
            CODEX_ACTIVE_QUEUES.discard(queue_key)
        # Fecha a corrida entre a ultima leitura vazia e a remocao da fila ativa.
        next_task_id = _codex_next_queued_task_id(queue_key)
        if next_task_id:
            _codex_start_thread(next_task_id)


def _codex_thread_resume_failure(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and re.search(
            r"(thread).*(not found|expired|invalid|does not exist|nao existe|expirad|inval)",
            text,
        )
    )


def _codex_transient_runtime_failure(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and re.search(
            r"(app[- ]server|broken pipe|connection (?:closed|refused|reset)|temporar(?:y|ily) unavailable|"
            r"timed? out|timeout|rate limit|too many requests|runtime.*(?:indispon|unavailable)|"
            r"codex.*(?:indispon|unavailable|not ready)|process.*(?:closed|exited))",
            text,
        )
    )


def _codex_run_worker(task_id: str) -> None:
    task = _codex_load_task(task_id)
    if not task:
        return
    sandbox = str(task.get("sandbox") or "read_only")
    whatsapp_query_only = _codex_task_whatsapp_query_only(task)
    if whatsapp_query_only:
        sandbox = "read_only"
    acquired_full_lock = False
    thread_id = ""
    try:
        if sandbox == "full_access":
            acquired_full_lock = CODEX_FULL_ACCESS_LOCK.acquire(blocking=False)
            if not acquired_full_lock:
                raise RuntimeError("Ja existe uma tarefa Codex com acesso total em execucao.")

        _codex_update_task(task_id, status="running", started_at=_codex_now(), error="")
        _codex_transition_task_plan(task_id, "planejando", current_step="entender", step_status="completed")
        _codex_transition_task_plan(task_id, "consultando", current_step="consultar", step_status="in_progress")
        task = _codex_load_task(task_id) or task
        _codex_log(task, f"Iniciando Codex em {sandbox}.")

        _codex_apply_sdk_protocol_compat()
        from openai_codex import Codex, CodexConfig
        from openai_codex.generated.v2_all import ReasoningSummary

        model = _codex_normalizar_model(task.get("model"))
        reasoning_effort = _codex_reasoning_effort_enum(task.get("reasoning_effort"))
        speed = _codex_normalizar_speed(task.get("speed"))
        service_tier = _codex_normalizar_service_tier(task.get("service_tier"), speed)
        approval_profile = _codex_normalizar_approval_profile(task.get("approval_mode"), sandbox)
        approval_mode = _codex_approval_mode_enum(approval_profile, sandbox)
        sandbox_enum = _codex_sandbox_enum(sandbox)
        task_permissions = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
        is_full_task = task_permissions.get("full") is True
        trusted_model_config = bool(
            str(task.get("origin") or "").strip().lower() == "whatsapp"
            and task.get("trusted_model_config") is True
        )
        external_safe_mode = bool(task.get("external_safe_mode"))
        whatsapp_full_access = bool(task.get("whatsapp_full_access"))
        read_only_channel_mode = bool(external_safe_mode or whatsapp_query_only)
        prompt = str(task.get("prompt") or "").strip()
        cwd = str(task.get("cwd") or _codex_base_dir())
        conversation_state = _codex_conversation_state_for_task(task)
        thread_id = (
            str(conversation_state.get("latest_thread_id") or "").strip()
            if is_full_task
            else ""
        )
        if thread_id != str(task.get("thread_id") or "").strip():
            _codex_update_task(task_id, thread_id=thread_id)
        goal = _codex_clean_text(task.get("goal"), 1200)
        paths = list(task.get("paths") or [])
        if not is_full_task:
            # Recalcula a fronteira no worker; uma tarefa persistida nunca pode
            # elevar cwd, modelo, tier ou recursos alterando seu JSON.
            sandbox = "read_only"
            if not trusted_model_config:
                model = _codex_normalizar_model(None)
                reasoning_effort = _codex_reasoning_effort_enum(None)
                speed = _codex_normalizar_speed(None)
                service_tier = _codex_normalizar_service_tier(None, speed)
            approval_profile = "read_only"
            approval_mode = _codex_approval_mode_enum(approval_profile, sandbox)
            cwd = _codex_readonly_cwd_for_session(
                {
                    "client_id": str(task.get("client_id") or "default"),
                    "username": str(task.get("created_by") or "user"),
                },
                _codex_conversation_id(
                    str(task.get("conversation_id") or ""),
                    str(task.get("task_id") or ""),
                ),
            )
            thread_id = ""
            goal = ""
            paths = []
        screen_context = task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {}
        scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
        if not is_full_task or not scope:
            scope = _codex_build_scope(
                prompt=prompt,
                sandbox=sandbox,
                paths=paths,
                screen_context=screen_context,
                cwd=cwd,
            )
            _codex_update_task(task_id, scope=scope)
        workspace_before = _codex_workspace_snapshot(cwd) if sandbox != "read_only" else {}
        agent_mode = _codex_agent_mode_enabled()
        native_mcp = bool(agent_mode and read_only_channel_mode and _codex_native_mcp_enabled(task))
        app_data_context: dict[str, Any] = {}
        conversation_context: dict[str, Any] = {}
        if agent_mode:
            _codex_update_live(task_id, agent_mode=True, live_status="interpretando pergunta")
            conversation_context = _codex_prepare_conversation_context(task)
            run_prompt = _codex_agent_initial_prompt(
                prompt,
                screen_context,
                conversation_context,
                str(task.get("client_id") or "default"),
                task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                sandbox,
                model,
                _codex_normalizar_reasoning_effort(task.get("reasoning_effort")),
                speed,
                approval_profile,
                read_only_channel_mode,
                whatsapp_full_access,
                native_mcp,
            )
            context_stats = _codex_context_stats_from_prompt(
                prompt,
                run_prompt,
                screen_context,
                {"enabled": True, "tool_results_count": 0},
                conversation_context,
            )
            _codex_update_live(
                task_id,
                context_stats=context_stats,
                conversation_summary={
                    "conversation_id": conversation_context.get("conversation_id") or "",
                    "summary_chars": len(str(conversation_context.get("summary") or "")),
                    "recent_messages": len(conversation_context.get("recent_messages") or []),
                    "compacted": bool(conversation_context.get("compacted")),
                },
                conversation_compaction=conversation_context.get("compaction") if isinstance(conversation_context.get("compaction"), dict) else {},
                app_data_context={
                    "enabled": True,
                    "agent_mode": True,
                    "tool_results_count": 0,
                    "catalog_tools_count": len(
                        _codex_agent_tool_catalog(
                            task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                            read_only_only=read_only_channel_mode,
                        )
                    ),
                    "error": "",
                },
                live_status="interpretando pergunta",
            )
            _codex_log(task, "Modo agente ativo: contexto bruto desativado; usando catalogo de ferramentas sob demanda.", "status")
            if conversation_context.get("recent_messages") or conversation_context.get("summary"):
                _codex_log(task, "Historico da conversa anexado em formato compacto.", "status")
        else:
            _codex_update_live(task_id, agent_mode=False, live_status="Interpretando pedido e consultando dados internos.")
            app_data_context = _codex_app_data_context(
                prompt,
                screen_context,
                str(task.get("client_id") or ""),
                task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
            )
            run_prompt = _codex_prompt_com_contexto_tela(prompt, screen_context, app_data_context)
            context_stats = _codex_context_stats(prompt, screen_context, app_data_context)
            status_steps = app_data_context.get("status_steps") if isinstance(app_data_context, dict) and isinstance(app_data_context.get("status_steps"), list) else []
            status_final = str(status_steps[-1] if status_steps else "Gerando resposta com dados internos.")
            _codex_update_live(
                task_id,
                context_stats=context_stats,
                app_data_context={
                    "enabled": bool(app_data_context.get("enabled")) if isinstance(app_data_context, dict) else False,
                    "agent_mode": False,
                    "tool_results_count": int(app_data_context.get("tool_results_count") or 0) if isinstance(app_data_context, dict) else 0,
                    "error": str(app_data_context.get("error") or "")[:600] if isinstance(app_data_context, dict) else "",
                },
                live_status=status_final,
            )
            _codex_log(task, "Interpretando pedido e preparando Codex Data Tools.", "status")

        extra_instructions: list[str] = []
        channel_metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
        dual_worker_web_search = _codex_dual_worker_web_search_enabled(
            task,
            read_only_channel_mode=read_only_channel_mode,
            sandbox=sandbox,
        )
        phone_ai_behavior = _codex_clean_text(channel_metadata.get("phone_ai_behavior"), 2000)
        if str(task.get("origin") or "").strip().lower() == "whatsapp" and phone_ai_behavior:
            extra_instructions.append(
                "Instrucao administrativa especifica para este numero de WhatsApp:\n"
                + phone_ai_behavior
                + "\nAplique esta instrucao ao tom, formato e forma de atendimento. "
                "Ela nunca amplia permissoes, libera mutacoes, altera o escopo de lojas ou substitui regras de seguranca e fontes."
            )
        if goal:
            extra_instructions.append(f"Meta definida pelo usuario: {goal}")
        if task.get("planning_mode"):
            extra_instructions.append("Modo planejamento ativo: comece com um plano curto antes de executar alteracoes.")
        if paths:
            extra_instructions.append("Arquivos e pastas adicionados ao contexto:\n" + "\n".join(f"- {path}" for path in paths))
        extra_instructions.append(_codex_scope_instruction(scope))
        if screen_context:
            extra_instructions.append(
                "Contexto da tela atual do JK Sistema enviado pela sidebar. "
                "Trate isto como a tela onde o usuario esta agora; use os textos, filtros, cards, tabelas e controles visiveis para entender o pedido. "
                "Se precisar de algo que nao esteja no snapshot, diga exatamente o que falta."
            )
            _codex_log(task, "Contexto da tela anexado ao prompt do Codex.")
        if agent_mode:
            extra_instructions.append(
                "Modo agente ativo: nao ha contexto bruto anexado. "
                "Use o protocolo jk_tool_calls para solicitar ferramentas read-only quando precisar de dados reais."
            )
            if dual_worker_web_search:
                extra_instructions.append(
                    "Para perguntas gerais que dependam de informacao atual, use a pesquisa web read-only. "
                    "Consulte fontes adequadas, informe as fontes no resultado estruturado e nunca execute acoes externas."
                )
        elif isinstance(app_data_context, dict) and int(app_data_context.get("tool_results_count") or 0) > 0:
            extra_instructions.append(
                "Consultas internas read-only do JK Sistema foram anexadas ao prompt. "
                "Use esses resultados como fonte principal para responder perguntas de dados e montar relatorios."
            )
            _codex_log(task, f"Consultas internas anexadas: {int(app_data_context.get('tool_results_count') or 0)} resultado(s).")
        elif isinstance(app_data_context, dict) and app_data_context.get("error"):
            _codex_log(task, "Falha ao preparar consultas internas: " + str(app_data_context.get("error"))[:300], "warning")
        extra_instructions.append(
            f"Configuracao Codex: modelo={model}, raciocinio={_codex_normalizar_reasoning_effort(task.get('reasoning_effort'))}, "
            f"velocidade={speed}, aprovacao={approval_profile}, sandbox={sandbox}."
        )
        if whatsapp_full_access:
            extra_instructions.append(
                "No WhatsApp, fale de forma natural, cordial e descontraida, como um colega prestativo. "
                "Va direto ao ponto, varie a abertura, nao coloque titulo em respostas simples, nao repita o nome Black Jhon e nao assine ao final. "
                "Nao use emojis. Quando o usuario pedir relatorio pelo WhatsApp, use Markdown simples e estas secoes: "
                "# Relatorio, ## Dados principais, ## Mais vendidos (se houver), ## Analise e ## Fontes e cobertura. "
                "Nao use tabelas. Limite o resumo a 8 indicadores e o ranking a 5 itens, salvo pedido expresso por mais. "
                "Separe cada produto do ranking em seu proprio bloco, com SKU, nome curto, quantidade e valor. "
                "Nas fontes, informe em linguagem simples o periodo, a conta ou loja, a quantidade de registros e qualquer lacuna. "
                "Se os dados forem insuficientes, declare a lacuna em vez de completar por suposicao."
            )
        else:
            extra_instructions.append(
                "Quando o usuario pedir relatorio, gere um relatorio em Markdown com titulo, periodo/filtros usados, dados principais, analise e proximas acoes. "
                "Se os dados internos anexados forem insuficientes, declare a lacuna em vez de completar por suposicao."
            )
        if read_only_channel_mode:
            access_instruction = (
                "Esta tarefa veio do WhatsApp e foi vinculada a um usuario do JK Sistema. "
                "Durante a execucao, use apenas consultas read-only do catalogo. "
                "Nao crie propostas operacionais, nao execute acoes de negocio e nao tente aprovar a propria tarefa. "
            )
        elif whatsapp_full_access:
            access_instruction = (
                "Esta tarefa veio do WhatsApp de um usuario administrativo full. "
                "Consultas podem usar todo o catalogo permitido ao usuario. "
                "Se a tarefa for mutavel, ela ja foi confirmada por codigo unico no mesmo numero antes desta execucao. "
                "Execute somente o pedido confirmado, preserve a auditoria e nunca exponha credenciais ou segredos. "
            )
        elif is_full_task:
            access_instruction = (
                "Voce esta dentro do JK Sistema em modo interno administrativo. "
                "Quando alterar arquivos, mantenha o escopo no pedido atual. "
            )
        else:
            access_instruction = (
                "Voce atende um usuario autenticado sem acesso administrativo full. "
                "A tarefa e estritamente read-only e limitada aos modulos/ferramentas autorizados no catalogo deste turno. "
                "Nao leia, enumere ou descreva arquivos, fontes, modulos ou capacidades omitidos do catalogo. "
            )
        developer_instructions = (
            access_instruction
            + "Respeite o pedido do usuario, nao exponha segredos e explique limites de acesso com clareza.\n\n"
            + "\n".join(extra_instructions)
        )

        fast_mode = speed == "fast"
        if dual_worker_web_search:
            config_overrides = _codex_web_readonly_config_overrides(fast_mode=fast_mode)
        elif read_only_channel_mode and sandbox == "read_only":
            config_overrides = _codex_external_readonly_config_overrides(fast_mode=fast_mode)
        else:
            config_overrides = () if is_full_task else _codex_nonfull_config_overrides(fast_mode=fast_mode)
        if str((_codex_load_task(task_id) or {}).get("status") or "") == "cancel_requested":
            _codex_update_task(
                task_id,
                status="canceled",
                completed_at=_codex_now(),
                live_status="Tarefa cancelada.",
                wait_reason="",
                active_turn_id="",
                can_steer=False,
            )
            return
        runtime_bin = _codex_runtime_require_ready()
        with Codex(
            CodexConfig(
                codex_bin=runtime_bin,
                env=_codex_sdk_env(),
                cwd=cwd,
                config_overrides=config_overrides,
            )
        ) as codex:
            thread_kwargs = {
                "cwd": cwd,
                "model": model,
                "sandbox": sandbox_enum,
                "approval_mode": approval_mode,
                "developer_instructions": developer_instructions,
            }
            if native_mcp:
                thread_kwargs["config"] = _codex_native_mcp_thread_config(task, screen_context)
                _codex_update_task(
                    task_id,
                    tool_protocol="mcp_v1",
                    mcp_migration={
                        "target": "jk_system_mcp",
                        "native_enabled": True,
                        "native_active": True,
                        "legacy_parser_fallback": True,
                    },
                )
            if not is_full_task:
                # O perfil de permissions e a fronteira de leitura. Passar
                # sandbox aqui substituiria partes desse perfil.
                thread_kwargs.pop("sandbox", None)
                thread_kwargs["ephemeral"] = True
            if service_tier:
                thread_kwargs["service_tier"] = service_tier
            try:
                if thread_id:
                    try:
                        thread = codex.thread_resume(thread_id, **thread_kwargs)
                    except Exception as exc:
                        _codex_log(task, f"Thread tecnica anterior indisponivel; contexto logico preservado: {exc}", "warning")
                        _codex_save_conversation_state(task, latest_thread_id="")
                        _codex_update_task(task_id, thread_id="")
                        thread_id = ""
                        thread = codex.thread_start(**thread_kwargs)
                else:
                    thread = codex.thread_start(**thread_kwargs)
            except Exception as exc:
                if not native_mcp:
                    raise
                _codex_log(task, f"MCP local indisponivel; ativando parser tipado de rollback: {exc}", "warning")
                thread_kwargs.pop("config", None)
                native_mcp = False
                _codex_update_task(
                    task_id,
                    tool_protocol="typed_catalog_text_v1",
                    mcp_migration={
                        "target": "jk_system_mcp",
                        "native_enabled": True,
                        "native_active": False,
                        "fallback_used": True,
                        "fallback_error": str(exc)[:500],
                        "legacy_parser_fallback": True,
                    },
                )
                thread = codex.thread_start(**thread_kwargs)
            run_kwargs = {
                "cwd": cwd,
                "sandbox": sandbox_enum,
                "model": model,
                "approval_mode": approval_mode,
                "effort": reasoning_effort,
                "summary": ReasoningSummary.model_validate("auto"),
            }
            if not is_full_task:
                run_kwargs.pop("sandbox", None)
            if service_tier:
                run_kwargs["service_tier"] = service_tier
            if agent_mode:
                final_response, state, agent_trace = _codex_agent_run_loop(
                    task_id,
                    task,
                    thread,
                    run_kwargs,
                    run_prompt,
                    screen_context,
                    _codex_agent_is_report_request(prompt),
                )
            else:
                turn = thread.turn(run_prompt, **run_kwargs)
                state = {"items": [], "live_answer": "", "reasoning_summary": "", "live_plan": ""}
                agent_trace = {}
                _codex_update_live(task_id, live_status="Codex iniciou o processamento.", wait_reason="codex_turn")
                _codex_register_active_turn(task_id, turn)
                try:
                    for event in turn.stream():
                        _codex_process_stream_event(task_id, task, event, state)
                finally:
                    _codex_unregister_active_turn(task_id, turn)

        if not agent_mode:
            completed_turn = state.get("completed_turn")
            if completed_turn is not None:
                status_value = str(getattr(getattr(completed_turn, "status", None), "value", getattr(completed_turn, "status", "")) or "")
                if status_value == "failed":
                    error = getattr(completed_turn, "error", None)
                    message = str(getattr(error, "message", "") or "turn failed with status failed")
                    raise RuntimeError(message)

            final_response = _codex_final_response_from_items(
                list(state.get("items") or []),
                fallback=str(state.get("live_answer") or ""),
            )
        result_thread_id = str(getattr(thread, "id", "") or thread_id or "").strip()
        if not result_thread_id:
            try:
                read = thread.read()
                result_thread_id = str(getattr(read, "id", "") or getattr(read, "thread_id", "") or "").strip()
            except Exception:
                result_thread_id = thread_id
        current_task = _codex_load_task(task_id) or {}
        if str(current_task.get("status") or "") == "cancel_requested":
            _codex_update_task(
                task_id,
                status="canceled",
                completed_at=_codex_now(),
                live_status="Tarefa cancelada.",
                wait_reason="",
                active_turn_id="",
                can_steer=False,
                error=str(current_task.get("error") or "")[:1000],
            )
            return
        conversation_id = _codex_task_conversation_id(task)
        scope_changed_files: list[str] = []
        scope_violations: list[str] = []
        if sandbox != "read_only" and workspace_before:
            workspace_after = _codex_workspace_snapshot(cwd)
            scope_changed_files = _codex_workspace_changed_files(workspace_before, workspace_after)
            scope_violations = _codex_scope_violations(scope, scope_changed_files)
            if scope_violations:
                preview = "\n".join(f"- {item}" for item in scope_violations[:30])
                message = (
                    "Bloqueio de escopo: a tarefa alterou arquivo(s) fora do modulo permitido.\n"
                    f"{preview}"
                    + ("\n- ..." if len(scope_violations) > 30 else "")
                    + "\n\nNenhuma nova tarefa deve ser aprovada com esse resultado. "
                    "Revise as alteracoes fora do escopo ou rode novamente ampliando explicitamente o escopo."
                )
                _codex_log(task, message, "scope")
                _codex_update_task(
                    task_id,
                    status="failed",
                    completed_at=_codex_now(),
                    final_response=message,
                    thread_id=result_thread_id,
                    conversation_id=conversation_id,
                    live_status="Codex bloqueado por escopo.",
                    error=message,
                    scope=scope,
                    scope_changed_files=scope_changed_files,
                    scope_violations=scope_violations,
                    warnings=(list(task.get("warnings") or []) + ["Alteracoes fora do escopo detectadas."])[-80:],
                )
                _codex_transition_task_plan(
                    task_id,
                    "falhou",
                    current_step="verificar",
                    step_status="failed",
                    verification={"status": "failed", "confirmed": False, "reason": "scope_violation"},
                )
                return

        _codex_transition_task_plan(task_id, "validando", current_step="consultar", step_status="completed")
        _codex_transition_task_plan(task_id, "validando", current_step="validar", step_status="in_progress")
        read_verification = {
            "status": "confirmed",
            "confirmed": True,
            "method": "tool_validation" if agent_mode else "codex_result",
            "verified_at": _codex_now(),
        }
        if isinstance(agent_trace, dict) and agent_trace.get("tool_results_summary"):
            last_result = list(agent_trace.get("tool_results_summary") or [])[-1]
            validation = last_result.get("tool_validation") if isinstance(last_result, dict) and isinstance(last_result.get("tool_validation"), dict) else {}
            if validation.get("dados_suficientes") is False:
                read_verification.update(
                    {
                        "status": "partial",
                        "confirmed": False,
                        "reason": str(validation.get("motivo") or "dados_insuficientes")[:1000],
                    }
                )
        terminal_status = "partial" if isinstance(agent_trace, dict) and agent_trace.get("deadline_exceeded") else "completed"
        if terminal_status == "partial":
            read_verification.update(
                {
                    "status": "partial",
                    "confirmed": False,
                    "reason": "deadline_exceeded",
                }
            )
        _codex_update_task(
            task_id,
            status=terminal_status,
            completed_at=_codex_now(),
            final_response=final_response or "Codex concluiu sem resposta final.",
            thread_id=result_thread_id,
            conversation_id=conversation_id,
            live_status="Codex concluiu.",
            wait_reason="",
            active_turn_id="",
            can_steer=False,
            live_answer=str(state.get("live_answer") or "")[-8000:],
            reasoning_summary=str(state.get("reasoning_summary") or "")[-8000:],
            live_plan=str(state.get("live_plan") or "")[-6000:],
            token_usage=state.get("token_usage") if isinstance(state.get("token_usage"), dict) else {},
            agent_mode=agent_mode,
            agent_steps=list(agent_trace.get("agent_steps") or []) if isinstance(agent_trace, dict) else [],
            tool_calls=list(agent_trace.get("tool_calls") or []) if isinstance(agent_trace, dict) else [],
            tool_results_summary=list(agent_trace.get("tool_results_summary") or []) if isinstance(agent_trace, dict) else [],
            sources=list(agent_trace.get("sources") or []) if isinstance(agent_trace, dict) else [],
            warnings=list(agent_trace.get("warnings") or []) if isinstance(agent_trace, dict) else [],
            scope=scope,
            scope_changed_files=scope_changed_files,
            scope_violations=scope_violations,
            verification=read_verification,
            error="",
        )
        _codex_transition_task_plan(
            task_id,
            "concluido" if read_verification.get("confirmed") else "parcial",
            current_step="responder",
            step_status="completed",
            verification=read_verification,
        )
        if result_thread_id and is_full_task:
            _codex_save_conversation_state(task, latest_thread_id=result_thread_id)
        task_permissions = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
        if task_permissions.get("full") is True:
            try:
                operational_memory_result = codex_operational_memory.remember_from_interaction(
                    client_id=str(task.get("client_id") or "default"),
                    prompt=str(prompt or ""),
                    final_answer=final_response or "",
                    trace=agent_trace if isinstance(agent_trace, dict) else {},
                    task={**task, "task_id": task_id},
                )
                if operational_memory_result.get("added_count"):
                    _codex_update_task(task_id, operational_memory=operational_memory_result)
            except Exception as exc:
                _codex_update_task(
                    task_id,
                    warnings=(list(task.get("warnings") or []) + [f"Falha ao atualizar memoria operacional: {exc}"])[-80:],
                )
        if agent_mode:
            memory_payload = _codex_update_conversation_memory(task_id)
            if memory_payload:
                _codex_update_task(
                    task_id,
                    conversation_summary={
                        "conversation_id": memory_payload.get("conversation_id") or conversation_id,
                        "summary_chars": len(str(memory_payload.get("summary") or "")),
                        "recent_messages": len(memory_payload.get("recent_messages") or []),
                        "compacted": bool(memory_payload.get("summary")),
                    },
                    conversation_compaction={
                        "compacted_until": memory_payload.get("compacted_until") or "",
                        "summary_updated_at": memory_payload.get("summary_updated_at") or "",
                        "estimated_tokens_before": memory_payload.get("estimated_tokens_before") or 0,
                        "estimated_tokens_after": memory_payload.get("estimated_tokens_after") or 0,
                    },
                )
    except Exception as exc:
        current_task = _codex_load_task(task_id) or {}
        if str(current_task.get("status") or "") == "cancel_requested":
            _codex_update_task(
                task_id,
                status="canceled",
                completed_at=_codex_now(),
                live_status="Tarefa cancelada.",
                wait_reason="",
                active_turn_id="",
                can_steer=False,
                error=str(current_task.get("error") or "")[:1000],
            )
            return
        if thread_id and not bool(task.get("thread_resume_retried")) and _codex_thread_resume_failure(exc):
            _codex_save_conversation_state(task, latest_thread_id="")
            _codex_update_task(
                task_id,
                status="queued",
                started_at="",
                completed_at="",
                thread_id="",
                thread_resume_retried=True,
                live_status="Renovando a thread tecnica sem perder o contexto.",
                error="",
            )
            return
        retry_count = int(task.get("runtime_retry_count") or 0)
        if (
            str(task.get("origin") or "") == "whatsapp"
            and not bool(task.get("mutable_intent"))
            and retry_count < 3
            and _codex_transient_runtime_failure(exc)
        ):
            delay = (2, 5, 10)[retry_count]
            _codex_update_task(
                task_id,
                status="queued",
                started_at="",
                completed_at="",
                runtime_retry_count=retry_count + 1,
                runtime_retry_after_seconds=delay,
                live_status=f"Codex indisponivel; nova tentativa {retry_count + 1}/3 em {delay} segundos.",
                wait_reason="retry_backoff",
                error="",
            )
            time.sleep(delay)
            return
        _codex_update_task(
            task_id,
            status="failed",
            completed_at=_codex_now(),
            live_status="Codex falhou.",
            error=str(exc),
        )
        _codex_transition_task_plan(
            task_id,
            "falhou",
            current_step=str((task or {}).get("current_step") or "consultar"),
            step_status="failed",
            verification={"status": "failed", "confirmed": False, "error": str(exc)[:1000]},
        )
    finally:
        _codex_native_mcp_cleanup(task_id)
        if acquired_full_lock:
            CODEX_FULL_ACCESS_LOCK.release()


def _codex_start_thread(task_id: str) -> None:
    task = _codex_load_task(task_id)
    if not task or str(task.get("status") or "") != "queued":
        return
    queue_key = _codex_task_queue_key(task)
    with CODEX_QUEUE_LOCK:
        if queue_key in CODEX_ACTIVE_QUEUES:
            return
        CODEX_ACTIVE_QUEUES.add(queue_key)
    worker = threading.Thread(target=_codex_run_conversation_queue, args=(queue_key,), daemon=True)
    worker.start()


def codex_console_recuperar_fila_background() -> dict[str, Any]:
    queued_ids: list[str] = []
    interrupted_ids: list[str] = []
    try:
        paths = sorted(Path(_codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime)
    except Exception:
        paths = []
    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if not task_id:
            continue
        status = str(task.get("status") or "")
        if status in {"running", "cancel_requested"}:
            resumable_dual_worker = bool(
                status == "running"
                and _codex_is_whatsapp_dual_worker(task)
                and str(task.get("sandbox") or "read_only") == "read_only"
            )
            resumable_whatsapp_read = bool(
                status == "running"
                and str(task.get("origin") or "") == "whatsapp"
                and task.get("external_safe_mode") is True
                and (
                    resumable_dual_worker
                    or (not task.get("mutable_intent") and not task.get("proposal"))
                )
            )
            if resumable_whatsapp_read:
                task.update(
                    {
                        "status": "queued",
                        "started_at": "",
                        "completed_at": "",
                        "active_turn_id": "",
                        "can_steer": False,
                        "wait_reason": "restart_recovery",
                        "live_status": "Retomando a consulta apos a reinicializacao, sem reutilizar resposta incompleta.",
                        "error": "",
                        "restart_recovery_count": int(task.get("restart_recovery_count") or 0) + 1,
                    }
                )
                queued_ids.append(task_id)
            else:
                task.update(
                    {
                        "status": "failed",
                        "completed_at": _codex_now(),
                        "active_turn_id": "",
                        "can_steer": False,
                        "live_status": "Execucao interrompida pelo reinicio do aplicativo.",
                        "error": "Execucao interrompida pelo reinicio; nenhuma resposta foi adicionada ao contexto.",
                    }
                )
                interrupted_ids.append(task_id)
        elif status == "queued":
            queued_ids.append(task_id)
        else:
            continue
        with CODEX_TASKS_LOCK:
            CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    for task_id in queued_ids:
        _codex_start_thread(task_id)
    return {
        "success": True,
        "queued": len(queued_ids),
        "interrupted": len(interrupted_ids),
        "queued_task_ids": queued_ids,
        "interrupted_task_ids": interrupted_ids,
    }


def codex_status(request: Request, authorization: Optional[str] = Header(default=None)):
    sessao = _codex_require_authenticated(request, authorization)
    return _codex_status_for_session(sessao)


async def codex_upload_attachments(
    request: Request,
    files: list[UploadFile] = File(...),
    conversation_id: str = Form(default=""),
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    uploads = list(files or [])
    if not uploads:
        raise HTTPException(status_code=400, detail="Envie ao menos um arquivo.")
    if len(uploads) > CODEX_ATTACHMENT_MAX_COUNT:
        raise HTTPException(status_code=400, detail=f"Limite de {CODEX_ATTACHMENT_MAX_COUNT} arquivos por envio.")

    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "user")
    # O campo continua aceito para clientes antigos, mas anexos sempre pertencem
    # a conversa app canonica do usuario autenticado.
    del conversation_id
    state = _codex_load_or_create_conversation_state(client_id, username, channel="app")
    conv_id = str(state.get("conversation_id") or _codex_universal_conversation_id(client_id, username))
    saved: list[dict[str, Any]] = []
    saved_paths: list[Path] = []
    total_bytes = 0
    try:
        _codex_cleanup_old_attachments()
        target_dir = _codex_attachment_dir(client_id, username, conv_id)
        for upload in uploads:
            original_name = _codex_safe_filename(upload.filename or "arquivo")
            content = await upload.read()
            size = len(content or b"")
            if size <= 0:
                raise HTTPException(status_code=400, detail=f"Arquivo vazio: {original_name}")
            if size > CODEX_ATTACHMENT_MAX_BYTES:
                raise HTTPException(status_code=413, detail=f"Arquivo acima de 25 MB: {original_name}")
            total_bytes += size
            if total_bytes > CODEX_ATTACHMENT_TOTAL_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Limite total de 100 MB por envio excedido.")

            file_id = uuid.uuid4().hex
            target = (target_dir / f"{file_id}_{original_name}").resolve()
            base = _codex_attachments_base_dir().resolve()
            try:
                common = os.path.commonpath([str(base), str(target)])
            except Exception:
                common = ""
            if common != str(base):
                raise HTTPException(status_code=400, detail="Nome de arquivo invalido.")
            target.write_bytes(content)
            saved_paths.append(target)
            saved.append(
                _codex_attachment_public_payload(
                    target,
                    original_name,
                    upload.content_type or "application/octet-stream",
                    size,
                )
            )
    except HTTPException:
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise
    except Exception as exc:
        for path in saved_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Falha ao salvar anexo: {exc}") from exc

    return {
        "success": True,
        "attachments": saved,
        "conversation_id": conv_id,
        "conversation_generation": int(state.get("generation") or 1),
    }


def codex_criar_tarefa(
    payload: CodexTaskRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_criar_tarefa_para_sessao(payload, sessao)


def codex_criar_tarefa_para_sessao(
    payload: CodexTaskRequest,
    sessao: dict[str, Any],
    *,
    origin: str = "app",
    channel_metadata: Optional[dict[str, Any]] = None,
    trusted_model_config: bool = False,
):
    _codex_cleanup_old_attachments()
    if not _codex_enabled():
        raise HTTPException(status_code=503, detail="Codex Console desabilitado. Defina JK_CODEX_CONSOLE_ENABLED=true.")
    if not _codex_sdk_installed():
        raise HTTPException(status_code=503, detail="Dependencia openai-codex nao instalada no runtime Python.")

    prompt = str(payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Informe uma mensagem para o Codex.")

    is_full = bool(sessao.get("is_full"))
    origin = "whatsapp" if str(origin or "").strip().lower() == "whatsapp" else "app"
    channel_metadata = dict(channel_metadata or {}) if isinstance(channel_metadata, dict) else {}
    trusted_model_config = bool(
        origin == "whatsapp"
        and (trusted_model_config or channel_metadata.get("admin_configured_ai") is True)
    )
    query_policy = _codex_whatsapp_query_policy(origin, channel_metadata)
    whatsapp_query_only = bool(query_policy)
    whatsapp_full_access = bool(
        origin == "whatsapp"
        and is_full
        and channel_metadata.get("mobile_full_access") is True
    )
    external_safe_mode = bool(origin == "whatsapp" and not whatsapp_full_access)
    sandbox = _codex_normalizar_sandbox(payload.sandbox)
    model = _codex_normalizar_model(payload.model)
    reasoning_effort = _codex_normalizar_reasoning_effort(payload.reasoning_effort)
    speed = _codex_normalizar_speed(payload.speed)
    service_tier = _codex_normalizar_service_tier(payload.service_tier, speed)
    approval_profile = _codex_normalizar_approval_profile(payload.approval_mode, sandbox)
    if approval_profile == "full_access":
        sandbox = "full_access"
    elif approval_profile == "read_only":
        sandbox = "read_only"
    if whatsapp_query_only:
        # Defesa em profundidade: mesmo que um chamador tente elevar sandbox ou
        # approval_mode, vendas/anuncios originados do WhatsApp permanecem leitura.
        sandbox = "read_only"
        approval_profile = "read_only"
    if not is_full:
        # O cliente nunca escolhe elevar permissao: o servidor rebaixa a tarefa.
        sandbox = "read_only"
        approval_profile = "read_only"
        if not trusted_model_config:
            model = _codex_normalizar_model(None)
            reasoning_effort = _codex_normalizar_reasoning_effort(None)
            speed = _codex_normalizar_speed(None)
            service_tier = _codex_normalizar_service_tier(None, speed)
    conversation_id = _codex_resolve_new_conversation_id(
        sessao,
        payload.conversation_id,
        origin=origin,
        channel_metadata=channel_metadata,
    )
    conversation_state = _codex_load_or_create_conversation_state(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
        channel=origin,
        phone=channel_metadata.get("wa_id") if origin == "whatsapp" else "",
        lane=channel_metadata.get("agent_lane") or channel_metadata.get("agent_role"),
    )
    conversation_generation = int(conversation_state.get("generation") or 1)
    cwd = (
        _codex_resolver_cwd(payload.cwd)
        if is_full
        else _codex_readonly_cwd_for_session(sessao, conversation_id)
    )
    paths = _codex_resolver_paths_for_session(
        payload.paths,
        sessao,
        conversation_id,
        allow_external_for_admin=sandbox == "full_access",
    )
    goal = _codex_clean_text(payload.goal, 1200) if is_full else ""
    screen_context = _codex_normalizar_screen_context(payload.screen_context)
    context_stats = _codex_context_stats(prompt, screen_context)
    # Compatibilidade de entrada: `history` ainda e aceito, mas o contexto
    # confiavel e reconstruido somente com tarefas persistidas no servidor.
    history: list[dict[str, str]] = []
    mutable_intent = _codex_prompt_pede_alteracao(prompt)
    if origin == "whatsapp" and mutable_intent:
        # O WhatsApp prepara a proposta, mas toda mutacao operacional do agente
        # geral precisa ser confirmada no aplicativo. Perguntas e Pos-venda
        # usam seu fluxo especializado, versionado e isolado deste endpoint.
        whatsapp_full_access = False
        external_safe_mode = True
        channel_metadata["requires_app_confirmation"] = True
    guidance_context = _codex_agent_guidance_context(prompt, screen_context)
    guidance_applied = codex_agent_runtime.resolve_guidance(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        context=guidance_context,
    )
    if whatsapp_query_only:
        # A politica de consulta prevalece sobre qualquer verbo mutavel que o
        # usuario tenha incluído na frase. O agente pode explicar ou preparar,
        # mas esse job nunca recebe sandbox de escrita.
        sandbox = "read_only"
        approval_profile = "read_only"
    elif external_safe_mode:
        if not is_full:
            sandbox = "read_only"
            approval_profile = "read_only"
        elif mutable_intent:
            sandbox = "workspace_write"
            approval_profile = "request"
        else:
            sandbox = "read_only"
            approval_profile = "read_only"
    if approval_profile == "request" and sandbox == "workspace_write" and not mutable_intent:
        sandbox = "read_only"
    scope = _codex_build_scope(
        prompt=prompt,
        sandbox=sandbox,
        paths=paths,
        screen_context=screen_context,
        cwd=cwd,
    )
    request_id = str(
        payload.request_id
        or channel_metadata.get("message_id")
        or channel_metadata.get("request_id")
        or ""
    ).strip()
    idempotency_key = codex_agent_runtime.make_idempotency_key(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or "user"),
        channel=origin,
        conversation_id=conversation_id,
        conversation_generation=conversation_generation,
        request_id=request_id,
        message=prompt,
    )
    existing_plan = codex_assistant_storage.codex_assistant_agent_plan_get(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        idempotency_key=idempotency_key,
    )
    if isinstance(existing_plan, dict) and existing_plan.get("task_id"):
        existing_task = _codex_load_task(str(existing_plan.get("task_id") or ""))
        if (
            isinstance(existing_task, dict)
            and str(existing_task.get("client_id") or "") == str(sessao.get("client_id") or "")
            and str(existing_task.get("created_by") or "").strip().lower() == str(sessao.get("username") or "").strip().lower()
        ):
            return {"success": True, "task": _codex_public_task(existing_task), "idempotent_replay": True}

    task_id = uuid.uuid4().hex
    plan = codex_agent_runtime.create_plan(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        task_id=task_id,
        conversation_id=conversation_id,
        conversation_generation=conversation_generation,
        username=str(sessao.get("username") or ""),
        channel=origin,
        message=prompt,
        mutable=bool(mutable_intent),
        idempotency_key=idempotency_key,
        guidance_applied=guidance_applied,
    )
    approval_required = bool(
        not whatsapp_query_only
        and (
            (external_safe_mode and is_full and mutable_intent)
            or (
                is_full
                and (sandbox == "full_access" or (approval_profile == "request" and mutable_intent))
            )
        )
    )
    operational_action: dict[str, Any] = {}
    if is_full and mutable_intent and not paths and not whatsapp_query_only:
        operational_action = codex_actions.create_proposal(
            client_id=str(sessao.get("client_id") or "default"),
            username=str(sessao.get("username") or ""),
            message=prompt,
            screen_context=screen_context,
            history=[],
            conversation_id=conversation_id,
            conversation_generation=conversation_generation,
            plan_id=str(plan.get("plan_id") or ""),
            task_id=task_id,
            channel=origin,
            wa_id_hash=hashlib.sha256(str(channel_metadata.get("wa_id") or "").encode("utf-8")).hexdigest() if origin == "whatsapp" else "",
            idempotency_key=idempotency_key,
        )
        if operational_action.get("matched") is True:
            approval_required = not bool(operational_action.get("needs_input"))
        elif not paths:
            # Uma intencao operacional sem contrato nunca recebe acesso de
            # escrita ao workspace. O agente ainda pode analisar e orientar.
            sandbox = "read_only"
            approval_profile = "read_only"
            approval_required = False

    task_status = "awaiting_approval" if approval_required else "queued"
    required_input: list[Any] = []
    proposal: dict[str, Any] = {}
    initial_response = ""
    if operational_action.get("matched") is True:
        required_input = list(operational_action.get("missing_params") or [])
        proposal = operational_action.get("proposal") if isinstance(operational_action.get("proposal"), dict) else {}
        if required_input:
            task_status = "awaiting_input"
            initial_response = str(operational_action.get("message") or "Preciso de mais dados antes de preparar a acao.")
        elif proposal:
            task_status = "awaiting_approval"
            initial_response = str(proposal.get("summary") or "Revise e confirme a acao proposta.")
    if required_input:
        plan = codex_agent_runtime.transition_plan(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            str(plan.get("plan_id") or ""),
            "aguardando_dados",
            current_step="preparar",
            required_input=required_input,
            details={"missing_params": required_input},
        )
    elif proposal:
        refreshed_plan = codex_assistant_storage.codex_assistant_agent_plan_get(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            str(plan.get("plan_id") or ""),
        )
        if isinstance(refreshed_plan, dict):
            plan = refreshed_plan
    default_deadline_seconds = 600 if _codex_agent_is_report_request(prompt) else 180
    try:
        deadline_seconds = int(channel_metadata.get("deadline_seconds") or default_deadline_seconds)
    except (TypeError, ValueError):
        deadline_seconds = default_deadline_seconds
    deadline_seconds = max(30, min(deadline_seconds, 600))
    task = {
        "task_id": task_id,
        "status": task_status,
        "sandbox": sandbox,
        "cwd": cwd,
        "thread_id": str(conversation_state.get("latest_thread_id") or "").strip() if is_full else "",
        "conversation_id": conversation_id,
        "conversation_generation": conversation_generation,
        "prompt": prompt,
        "model": model,
        "approval_mode": approval_profile,
        "reasoning_effort": reasoning_effort,
        "reasoning_level": str(channel_metadata.get("reasoning_level") or reasoning_effort),
        "reasoning_policy": str(channel_metadata.get("reasoning_policy") or "fixed")[:40],
        "reasoning_max": str(channel_metadata.get("reasoning_max") or reasoning_effort)[:20],
        "orchestration_profile": str(channel_metadata.get("orchestration_profile") or "default")[:80],
        "agent_role": str(channel_metadata.get("agent_role") or "")[:40],
        "agent_lane": str(channel_metadata.get("agent_lane") or channel_metadata.get("agent_role") or "")[:40],
        "parent_job_id": str(channel_metadata.get("parent_job_id") or "")[:100],
        "job_group_id": str(channel_metadata.get("job_group_id") or "")[:100],
        "subtask_id": str(channel_metadata.get("subtask_id") or "")[:100],
        "logical_subtask_id": str(channel_metadata.get("logical_subtask_id") or channel_metadata.get("subtask_id") or "")[:100],
        "current_attempt": max(1, int(channel_metadata.get("attempt") or 1)),
        "attempt_task_ids": [],
        "retry_count": 0,
        "retry_reason": "",
        "next_retry_at_epoch": 0,
        "handoff_status": str(channel_metadata.get("handoff_status") or "")[:60],
        "last_conversation_tick_at": str(channel_metadata.get("last_conversation_tick_at") or "")[:40],
        "delivery_state": str(channel_metadata.get("delivery_state") or "pending")[:40],
        "tool_protocol": "mcp_v1" if _codex_bool_env("JK_CODEX_NATIVE_MCP_ENABLED", True) and origin == "whatsapp" else "typed_catalog_text_v1",
        "mcp_migration": {
            "target": "jk_system_mcp",
            "native_enabled": _codex_bool_env("JK_CODEX_NATIVE_MCP_ENABLED", True) and origin == "whatsapp",
            "native_active": False,
            "legacy_parser_fallback": True,
        },
        "speed": speed,
        "service_tier": service_tier or "",
        "goal": goal,
        "planning_mode": bool(payload.planning_mode) if is_full else False,
        "paths": paths,
        "scope": scope,
        "scope_violations": [],
        "screen_context": screen_context,
        "history": history,
        "context_stats": context_stats,
        "conversation_summary": {},
        "conversation_compaction": {},
        "agent_mode": _codex_agent_mode_enabled(),
        "plan_id": str(plan.get("plan_id") or ""),
        "agent_state": str(plan.get("agent_state") or ("aguardando_dados" if required_input else "aguardando_aprovacao" if proposal else "entendendo")),
        "steps": list(plan.get("steps") or []),
        "current_step": str(plan.get("current_step") or ("aprovar" if proposal else "preparar" if required_input else "entender")),
        "required_input": required_input,
        "proposal": proposal,
        "guidance_applied": guidance_applied,
        "verification": {},
        "idempotency_key": idempotency_key,
        "agent_steps": [],
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": [],
        "warnings": [],
        "live_status": "Tarefa criada.",
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
        "token_usage": {},
        "turn_id": "",
        "active_turn_id": "",
        "can_steer": False,
        "wait_reason": "queue" if task_status == "queued" else task_status,
        "progress_events": [],
        "last_progress_at": "",
        "deadline_seconds": deadline_seconds,
        "deadline_at": _codex_deadline_at(deadline_seconds),
        "steer_events": [],
        "mutable_intent": mutable_intent,
        "final_response": initial_response,
        "error": "",
        "message_kind": "",
        "memory_excluded": False,
        "logs": [],
        "created_at": _codex_now(),
        "started_at": "",
        "completed_at": "",
        "created_by": sessao["username"],
        "client_id": sessao["client_id"],
        "origin": origin,
        "channel_message_id": str(channel_metadata.get("message_id") or "")[:200],
        "channel_metadata": channel_metadata,
        "external_safe_mode": external_safe_mode,
        "whatsapp_full_access": whatsapp_full_access,
        "whatsapp_query_only": whatsapp_query_only,
        "query_policy": query_policy,
        "trusted_model_config": trusted_model_config,
        "access_mode": "query_only" if whatsapp_query_only else ("full" if is_full else "read_only"),
        "permissions": {
            str(key): value is True
            for key, value in (sessao.get("permissions") or {}).items()
            if str(key or "").strip()
        },
        "approval_required": approval_required,
        "approved": not bool(approval_required or required_input or proposal),
    }
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    _codex_log(task, "Tarefa criada.")
    if not is_full:
        _codex_log(task, "Acesso do usuario limitado pelo servidor a leitura e aos modulos autorizados.")
    if whatsapp_query_only:
        _codex_log(task, "Politica WhatsApp query_only aplicada a vendas/anuncios; aprovacao e mutacoes desabilitadas.")
    if approval_profile == "request" and not mutable_intent:
        _codex_log(task, "Modo solicitar aprovacao executado em leitura porque a tarefa nao pediu alteracao de arquivos.")
    if approval_required:
        _codex_log(task, "Aguardando confirmacao para executar com permissao mutavel.")
        if external_safe_mode:
            _codex_log(task, "Aprovacao pelo WhatsApp e proibida; informe modulo ou caminhos no aplicativo.")
        elif whatsapp_full_access:
            _codex_log(task, "Aprovacao movel habilitada para o mesmo numero e usuario full que originaram a tarefa.")
    else:
        _codex_start_thread(task_id)
    return {"success": True, "task": _codex_public_task(task)}


def codex_listar_tarefas(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
    summary: bool = False,
    channel: str = "",
):
    sessao = _codex_require_authenticated(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    max_items = max(1, min(100, int(limit or 20)))
    channel_filter = str(channel or "").strip().lower()
    if channel_filter not in {"", "app", "whatsapp"}:
        raise HTTPException(status_code=400, detail="Canal de conversa invalido.")
    if bool(sessao.get("is_full")):
        _codex_backfill_assistant_report_tasks(
            client_id,
            str(sessao.get("username") or ""),
            max_items,
        )
    tasks: list[dict[str, Any]] = []
    try:
        paths = sorted(
            Path(_codex_info_dir()).glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        paths = []

    deleted_ids = _codex_deleted_conversation_ids(client_id, username)
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                task = json.load(fh)
            if isinstance(task, dict):
                if str(task.get("client_id") or "default") != client_id:
                    continue
                if _codex_task_conversation_keys(task) & deleted_ids:
                    continue
                created_by = str(task.get("created_by") or "").strip().lower()
                if not created_by or created_by != username:
                    continue
                if channel_filter and _codex_task_channel(task) != channel_filter:
                    continue
                tasks.append(_codex_task_summary(task) if summary else _codex_public_task(task))
                if len(tasks) >= max_items:
                    break
        except Exception:
            continue
    return {"success": True, "tasks": tasks}


def codex_registrar_interacao_whatsapp_externa(
    *,
    client_id: str,
    username: str,
    phone: str,
    prompt: str,
    response: str,
    model: str = "black-jhon-voice",
    source: str = "whatsapp_call",
    call_id: str = "",
    duration_seconds: int = 0,
    sources: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """Persiste um par concluido produzido fora do runner sem inventar uma thread.

    O uso inicial e o sideband de voz. O registro entra na mesma memoria logica
    do telefone, mas nunca carrega audio bruto, eventos do Realtime ou segredos.
    """
    phone_norm = _codex_normalize_phone(phone)
    prompt_text = str(prompt or "").replace("\x00", "").strip()[:12000]
    response_text = str(response or "").replace("\x00", "").strip()[:30000]
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"
    if not phone_norm or not username_norm or not prompt_text or not response_text:
        raise ValueError("external_whatsapp_exchange_invalid")
    state = _codex_load_or_create_conversation_state(
        client_norm,
        username_norm,
        channel="whatsapp",
        phone=phone_norm,
    )
    conversation_id = str(state.get("conversation_id") or "")
    task_id = f"wa_voice_{uuid.uuid4().hex}"
    now = _codex_now()
    metadata = {
        "wa_id": phone_norm,
        "phone": phone_norm,
        "message_type": "voice_call",
        "interaction_type": "call",
        "source": str(source or "whatsapp_call")[:60],
        "call_id": str(call_id or "")[:200],
        "duration_seconds": max(0, int(duration_seconds or 0)),
        "safe_read_only": True,
    }
    task: dict[str, Any] = {
        "task_id": task_id,
        "status": "completed",
        "sandbox": "read_only",
        "cwd": _codex_base_dir(),
        "thread_id": "",
        "conversation_id": conversation_id,
        "conversation_generation": int(state.get("generation") or 1),
        "conversation_state": str(state.get("state") or "active"),
        "prompt": prompt_text,
        "model": str(model or "black-jhon-voice")[:100],
        "approval_mode": "read_only",
        "reasoning_effort": "",
        "paths": [],
        "screen_context": {"channel": "whatsapp", "interaction_type": "call"},
        "history": [],
        "context_stats": _codex_context_stats(prompt_text, {}),
        "conversation_summary": {},
        "conversation_compaction": {},
        "agent_mode": True,
        "agent_state": "completed",
        "agent_steps": [],
        "tool_calls": [],
        "tool_results_summary": [],
        "sources": [str(item or "")[:1000] for item in list(sources or [])[:30] if str(item or "").strip()],
        "warnings": [],
        "live_status": "",
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
        "token_usage": {},
        "turn_id": "",
        "active_turn_id": "",
        "can_steer": False,
        "wait_reason": "",
        "progress_events": [],
        "required_input": [],
        "proposal": {},
        "verification": {"status": "confirmed", "source": "voice_sideband"},
        "mutable_intent": False,
        "final_response": response_text,
        "error": "",
        "message_kind": "conversation",
        "memory_excluded": False,
        "logs": [],
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "created_by": username_norm,
        "client_id": client_norm,
        "origin": "whatsapp",
        "channel_message_id": str(call_id or task_id)[:200],
        "channel_metadata": metadata,
        "external_safe_mode": True,
        "whatsapp_full_access": False,
        "whatsapp_query_only": True,
        "query_policy": {"safe_read_only": True},
        "trusted_model_config": True,
        "access_mode": "query_only",
        "permissions": {},
        "approval_required": False,
        "approved": True,
    }
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    try:
        _codex_update_conversation_memory(task_id)
    except Exception:
        pass
    return _codex_public_task(task)


def codex_listar_conversas_whatsapp(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 50,
    offset: int = 0,
):
    sessao = _codex_require_authenticated(request, authorization)
    max_items = max(1, min(100, int(limit or 50)))
    page_offset = max(0, int(offset or 0))
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    grouped: dict[str, dict[str, Any]] = {}

    for record in _codex_whatsapp_history_records(sessao):
        task = record["task"]
        conversation_id = str(record.get("conversation_id") or "")
        phone = str(record.get("phone") or "")
        group = grouped.get(conversation_id)
        if group is None:
            state = _codex_load_conversation_summary(client_id, username, conversation_id)
            group = {
                "conversation_id": conversation_id,
                "channel": "whatsapp",
                "phone": phone,
                "phone_display": _codex_whatsapp_phone_display(phone),
                "generation": int(state.get("generation") or task.get("conversation_generation") or 1),
                "state": str(state.get("state") or "active"),
                "updated_at": str(record.get("activity_at") or ""),
                "exchange_count": 0,
                "active_count": 0,
                "failed_count": 0,
                "last_prompt_preview": "",
                "last_response_preview": "",
                "registered": False,
                "label": "",
                "last_inbound_at": "",
            }
            grouped[conversation_id] = group
        status = str(task.get("status") or "")
        if status in {"queued", "running", "awaiting_approval", "cancel_requested"}:
            group["active_count"] += 1
        elif status in {"failed", "cancelled"}:
            group["failed_count"] += 1
        if _codex_whatsapp_completed_exchange(task):
            group["exchange_count"] += 1
            if not group["last_prompt_preview"]:
                group["last_prompt_preview"] = str(task.get("prompt") or "").strip()[:180]
                group["last_response_preview"] = str(task.get("final_response") or "").strip()[:240]

    for binding in _codex_registered_whatsapp_bindings(sessao):
        conversation_id = str(binding.get("conversation_id") or "")
        if not conversation_id:
            continue
        registered_at = str(binding.get("registered_at") or "")
        last_inbound_at = str(binding.get("last_inbound_at") or "")
        activity_at = last_inbound_at or registered_at
        group = grouped.get(conversation_id)
        if group is None:
            phone = str(binding.get("phone") or "")
            state = _codex_load_conversation_summary(client_id, username, conversation_id)
            group = {
                "conversation_id": conversation_id,
                "channel": "whatsapp",
                "phone": phone,
                "phone_display": _codex_whatsapp_phone_display(phone),
                "generation": int(state.get("generation") or 1),
                "state": str(state.get("state") or "active"),
                "updated_at": activity_at,
                "exchange_count": 0,
                "active_count": 0,
                "failed_count": 0,
                "last_prompt_preview": "",
                "last_response_preview": "",
                "registered": True,
                "label": str(binding.get("label") or ""),
                "last_inbound_at": last_inbound_at,
            }
            grouped[conversation_id] = group
        else:
            # O numero do cadastro e preferido na apresentacao; a identidade
            # canonica ja reuniu a variante Meta com ou sem o nono digito.
            phone = str(binding.get("phone") or group.get("phone") or "")
            group["phone"] = phone
            group["phone_display"] = _codex_whatsapp_phone_display(phone)
            group["registered"] = True
            group["label"] = str(binding.get("label") or "")
            group["last_inbound_at"] = last_inbound_at
            if activity_at > str(group.get("updated_at") or ""):
                group["updated_at"] = activity_at

    conversations = sorted(
        grouped.values(),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )
    page = conversations[page_offset : page_offset + max_items]
    return {
        "success": True,
        "channel": "whatsapp",
        "conversations": page,
        "total": len(conversations),
        "limit": max_items,
        "offset": page_offset,
        "has_more": page_offset + len(page) < len(conversations),
    }


def codex_listar_mensagens_conversa_whatsapp(
    conversation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
    offset: int = 0,
):
    sessao = _codex_require_authenticated(request, authorization)
    requested_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not requested_id or not requested_id.startswith("wa_"):
        raise HTTPException(status_code=404, detail="Conversa do WhatsApp nao encontrada.")
    matching = [
        record
        for record in _codex_whatsapp_history_records(sessao)
        if str(record.get("conversation_id") or "") == requested_id
    ]
    registered = {
        str(item.get("conversation_id") or ""): item
        for item in _codex_registered_whatsapp_bindings(sessao)
    }
    binding = registered.get(requested_id)
    if not matching and not binding:
        raise HTTPException(status_code=404, detail="Conversa do WhatsApp nao encontrada para este usuario.")

    completed = [record for record in matching if _codex_whatsapp_completed_exchange(record["task"])]
    max_items = max(1, min(50, int(limit or 20)))
    page_offset = max(0, int(offset or 0))
    page = completed[page_offset : page_offset + max_items]
    phone = str((binding or {}).get("phone") or (matching[0].get("phone") if matching else "") or "")
    return {
        "success": True,
        "conversation": {
            "conversation_id": requested_id,
            "channel": "whatsapp",
            "phone": phone,
            "phone_display": _codex_whatsapp_phone_display(phone),
            "registered": bool(binding),
            "label": str((binding or {}).get("label") or ""),
            "last_inbound_at": str((binding or {}).get("last_inbound_at") or ""),
        },
        "messages": [_codex_whatsapp_history_message_preview(record["task"]) for record in page],
        "total": len(completed),
        "limit": max_items,
        "offset": page_offset,
        "has_more": page_offset + len(page) < len(completed),
    }


def codex_obter_mensagem_conversa_whatsapp(
    conversation_id: str,
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    requested_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    requested_task_id = str(task_id or "").strip()
    if not requested_id or not requested_task_id:
        raise HTTPException(status_code=404, detail="Mensagem do WhatsApp nao encontrada.")
    for record in _codex_whatsapp_history_records(sessao):
        task = record["task"]
        if str(record.get("conversation_id") or "") != requested_id:
            continue
        if str(task.get("task_id") or "") != requested_task_id:
            continue
        if not _codex_whatsapp_completed_exchange(task):
            raise HTTPException(status_code=409, detail="Esta conversa ainda nao possui uma resposta concluida.")
        return {
            "success": True,
            "message": {
                "task_id": requested_task_id,
                "created_at": str(task.get("created_at") or ""),
                "completed_at": str(task.get("completed_at") or ""),
                "prompt": str(task.get("prompt") or "").strip(),
                "response": str(task.get("final_response") or "").strip(),
            },
        }
    raise HTTPException(status_code=404, detail="Mensagem do WhatsApp nao encontrada para este usuario.")


def codex_obter_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    return {"success": True, "task": _codex_public_task(task)}


def codex_complementar_tarefa_para_sessao(
    task_id: str,
    message: str,
    sessao: dict[str, Any],
    *,
    request_id: str = "",
    subject_id: str = "",
    wa_id: str = "",
) -> dict[str, Any]:
    task = _codex_require_owned_task(task_id, sessao)
    text = str(message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Informe o complemento da tarefa.")
    if len(text) > 12000:
        raise HTTPException(status_code=400, detail="O complemento excede 12 mil caracteres.")
    status = str(task.get("status") or "")
    if status not in {"queued", "running"}:
        return {"success": False, "accepted": False, "reason": "task_not_steerable", "task": _codex_public_task(task)}
    if task.get("mutable_intent") or task.get("proposal"):
        return {"success": False, "accepted": False, "reason": "proposal_or_mutation_locked", "task": _codex_public_task(task)}
    agent_state = str(task.get("agent_state") or "").strip().lower()
    if agent_state in {"aguardando_aprovacao", "executando", "verificando", "concluido", "parcial", "falhou", "cancelado"}:
        return {"success": False, "accepted": False, "reason": "agent_state_locked", "task": _codex_public_task(task)}
    metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    if str(task.get("origin") or "") == "whatsapp":
        expected_subject = str(metadata.get("subject_id") or "").strip()
        expected_phone = _codex_normalize_phone(metadata.get("wa_id"))
        supplied_phone = _codex_normalize_phone(wa_id)
        if expected_subject and expected_subject != str(subject_id or "").strip():
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
        if expected_phone and expected_phone != supplied_phone:
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este telefone.")
    event_id = str(request_id or uuid.uuid4().hex).strip()[:200]
    events = [item for item in (task.get("steer_events") or []) if isinstance(item, dict)]
    if any(str(item.get("request_id") or "") == event_id for item in events):
        return {"success": True, "accepted": True, "idempotent_replay": True, "task": _codex_public_task(task)}
    event = {"request_id": event_id, "message_preview": text[:500], "created_at": _codex_now(), "mode": "queued"}
    if status == "queued":
        with CODEX_TASKS_LOCK:
            current = CODEX_TASKS.get(task_id) or task
            current["prompt"] = (str(current.get("prompt") or "").rstrip() + "\n\n[Complemento do usuario]\n" + text).strip()
            current["steer_events"] = (events + [event])[-20:]
            current["live_status"] = "Complemento incorporado antes do inicio da tarefa."
            _codex_persist_task(current)
        return {"success": True, "accepted": True, "mode": "queued_prompt", "task": _codex_public_task(current)}
    with CODEX_ACTIVE_TURNS_LOCK:
        turn = CODEX_ACTIVE_TURNS.get(task_id)
    if turn is None or not str(getattr(turn, "id", "") or "").strip():
        return {"success": False, "accepted": False, "reason": "active_turn_unavailable", "task": _codex_public_task(task)}
    try:
        turn.steer(text)
    except Exception as exc:
        _codex_log(task, f"Nao foi possivel incorporar o complemento no turno ativo: {exc}", "warning")
        return {"success": False, "accepted": False, "reason": "steer_failed", "error": str(exc)[:500], "task": _codex_public_task(task)}
    event["mode"] = "turn_steer"
    _codex_update_task(
        task_id,
        steer_events=(events + [event])[-20:],
        live_status="Complemento do usuario incorporado ao turno ativo.",
        wait_reason="codex_turn",
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Complemento autenticado incorporado ao turno ativo do Codex.")
    return {"success": True, "accepted": True, "mode": "turn_steer", "task": _codex_public_task(task)}


def codex_complementar_tarefa(
    task_id: str,
    payload: CodexTaskSteerRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_complementar_tarefa_para_sessao(
        task_id,
        payload.message,
        sessao,
        request_id=str(payload.request_id or ""),
    )


def codex_deletar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    if str(task.get("status") or "") in {"queued", "running", "awaiting_approval", "cancel_requested"}:
        raise HTTPException(status_code=409, detail="Cancele ou aguarde a tarefa terminar antes de excluir.")
    public = _codex_public_task(task)
    with CODEX_TASKS_LOCK:
        CODEX_TASKS.pop(task_id, None)
    try:
        path = _codex_task_path(task_id)
        if os.path.exists(path):
            os.remove(path)
        _codex_delete_conversation_memory_if_unused(task)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Nao foi possivel excluir a conversa: {exc}") from exc
    return {"success": True, "deleted": True, "task": public}


def codex_deletar_conversa(
    conversation_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not conv_id:
        raise HTTPException(status_code=400, detail="Informe a conversa para excluir.")
    try:
        state_paths = list(_codex_conversation_dir(client_id, username).glob("*.json"))
    except Exception:
        state_paths = []
    for state_path in state_paths:
        if state_path.name.startswith("_"):
            continue
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                state_payload = json.load(fh)
        except Exception:
            continue
        if (
            isinstance(state_payload, dict)
            and str(state_payload.get("conversation_id") or "") == conv_id
            and str(state_payload.get("state") or "active") == "active"
            and int(state_payload.get("generation") or 0) >= 1
        ):
            raise HTTPException(
                status_code=409,
                detail="A conversa ativa do Black Jhon nao pode ser excluida. Use Reiniciar memoria.",
            )

    matches: list[dict[str, Any]] = []
    blocked: list[str] = []
    try:
        paths = list(Path(_codex_info_dir()).glob("*.json"))
    except Exception:
        paths = []

    for path in paths:
        try:
            with path.open("r", encoding="utf-8") as fh:
                task = json.load(fh)
        except Exception:
            continue
        if not isinstance(task, dict):
            continue
        if str(task.get("client_id") or "default") != client_id:
            continue
        created_by = str(task.get("created_by") or "").strip().lower()
        if not created_by or created_by != username:
            continue
        task_id = str(task.get("task_id") or path.stem).strip()
        if conv_id not in _codex_task_conversation_keys(task):
            continue
        status = str(task.get("status") or "").strip()
        if status in {"queued", "running", "awaiting_approval", "cancel_requested"}:
            blocked.append(task_id)
            continue
        matches.append({"task": task, "path": path, "task_id": task_id})

    if blocked:
        raise HTTPException(
            status_code=409,
            detail="Cancele ou aguarde as tarefas em andamento antes de excluir a conversa.",
        )

    deleted_ids: list[str] = []
    with CODEX_TASKS_LOCK:
        for item in matches:
            task_id = str(item.get("task_id") or "").strip()
            if task_id:
                CODEX_TASKS.pop(task_id, None)
                deleted_ids.append(task_id)
    for item in matches:
        path = item.get("path")
        try:
            if isinstance(path, Path) and path.exists():
                path.unlink()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Nao foi possivel excluir a conversa: {exc}") from exc

    try:
        summary_path = _codex_conversation_path(client_id, username, conv_id)
        if summary_path.exists():
            summary_path.unlink()
    except Exception:
        pass
    _codex_mark_conversation_deleted(client_id, conv_id, username, deleted_ids)
    return {
        "success": True,
        "deleted": True,
        "conversation_id": conv_id,
        "deleted_count": len(deleted_ids),
        "task_ids": deleted_ids,
    }


def codex_reset_current_conversation(
    payload: CodexConversationResetRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="Confirme o reinicio da memoria do Black Jhon.")
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    with CODEX_CONVERSATION_LOCK:
        state = _codex_load_or_create_conversation_state(client_id, username, channel="app")
        conversation_id = str(state.get("conversation_id") or "")
        generation = int(state.get("generation") or 1)
        active_statuses = {"queued", "running", "awaiting_approval", "cancel_requested"}
        busy = [
            task
            for task in _codex_owned_persisted_tasks(client_id, username)
            if _codex_task_stored_conversation_id(task) == conversation_id
            and int(task.get("conversation_generation") or 1) == generation
            and str(task.get("status") or "") in active_statuses
        ]
        if busy:
            raise HTTPException(
                status_code=409,
                detail="Aguarde ou cancele as tarefas ativas antes de reiniciar a memoria.",
            )
        audit = list(state.get("reset_audit") or [])
        audit.append(
            {
                "at": _codex_now(),
                "by": username,
                "archived_generation": generation,
            }
        )
        state.update(
            {
                "generation": generation + 1,
                "summary": "",
                "recent_messages": [],
                "legacy_conversation_ids": [],
                "latest_thread_id": "",
                "compacted_until": "",
                "summary_updated_at": "",
                "estimated_tokens_before": 0,
                "estimated_tokens_after": 0,
                "reset_audit": audit[-50:],
                "updated_at": _codex_now(),
            }
        )
        _codex_save_conversation_summary(client_id, username, conversation_id, state)
    return {
        "success": True,
        "conversation": {
            "conversation_id": conversation_id,
            "channel": "app",
            "generation": int(state.get("generation") or generation + 1),
            "state": "active",
            "can_reset": True,
            "queue": {"running": 0, "pending": 0},
        },
    }


def codex_aprovar_tarefa_para_sessao(
    task_id: str,
    sessao: dict[str, Any],
    payload: Optional[CodexTaskApprovalRequest] = None,
    *,
    approval_source: str = "app",
    subject_id: str = "",
):
    if not bool(sessao.get("is_full")) or (sessao.get("permissions") or {}).get("full") is not True:
        raise HTTPException(status_code=403, detail="A aprovacao exige um usuario full ativo.")
    task = _codex_require_owned_task(task_id, sessao)
    requested_source = "whatsapp" if str(approval_source or "").strip().lower() == "whatsapp" else "app"
    if requested_source == "whatsapp":
        raise HTTPException(
            status_code=403,
            detail="Acoes operacionais preparadas pelo WhatsApp devem ser confirmadas no aplicativo JK Sistema.",
        )
    task_proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    if task_proposal.get("proposal_id"):
        source = requested_source
        metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
        if source == "whatsapp":
            expected_subject = str(metadata.get("subject_id") or "").strip()
            if not expected_subject or expected_subject != str(subject_id or "").strip():
                raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
        approved_action = codex_actions.approve_proposal(
            str(task_proposal.get("proposal_id") or ""),
            username=str(sessao.get("username") or ""),
            client_id=str(sessao.get("client_id") or "default"),
            authorization=None,
            source=source,
            wa_id=str(metadata.get("wa_id") or ""),
            proposal_version=int(task_proposal.get("version") or 1),
            proposal_hash=str(task_proposal.get("proposal_hash") or ""),
        )
        _codex_update_task(
            task_id,
            status="running",
            approved=True,
            approval_source=source,
            approved_at=_codex_now(),
            approved_by=str(sessao.get("username") or ""),
            proposal=approved_action.get("proposal") if isinstance(approved_action.get("proposal"), dict) else task_proposal,
            action_run=approved_action.get("run") if isinstance(approved_action.get("run"), dict) else {},
            agent_state="executando",
            current_step="executar",
        )
        return {"success": True, "task": _codex_public_task(_codex_load_task(task_id) or task), **approved_action}
    if _codex_task_whatsapp_query_only(task):
        raise HTTPException(
            status_code=403,
            detail="Consultas de vendas e anuncios originadas do WhatsApp usam politica query_only e nao podem ser aprovadas para execucao mutavel.",
        )
    if task.get("status") != "awaiting_approval":
        return {"success": True, "task": _codex_public_task(task)}
    source = str(approval_source or "app").strip().lower()
    if source not in {"app", "whatsapp"}:
        raise HTTPException(status_code=400, detail="Origem de aprovacao invalida.")
    channel_metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
    mobile_approval = bool(
        source == "whatsapp"
        and str(task.get("origin") or "") == "whatsapp"
        and task.get("whatsapp_full_access") is True
    )
    if source == "whatsapp" and not mobile_approval:
        raise HTTPException(status_code=403, detail="Esta tarefa nao permite aprovacao pelo WhatsApp.")
    if mobile_approval:
        expected_subject = str(channel_metadata.get("subject_id") or "").strip()
        actual_subject = str(subject_id or "").strip()
        if not expected_subject or not actual_subject or expected_subject != actual_subject:
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
        approval = payload or CodexTaskApprovalRequest()
        screen_context = _codex_normalizar_screen_context(approval.screen_context)
        scope = _codex_build_scope(
            prompt=str(task.get("prompt") or ""),
            sandbox="full_access",
            paths=[],
            screen_context=screen_context,
            cwd=str(task.get("cwd") or _codex_base_dir()),
        )
        scope.update(
            {
                "enforced": False,
                "reason": "whatsapp_full_user_approval",
                "broad_request": True,
                "approved_subject_fingerprint": hashlib.sha256(actual_subject.encode("utf-8")).hexdigest()[:16],
            }
        )
        _codex_update_task(
            task_id,
            screen_context=screen_context or task.get("screen_context") or {},
            scope=scope,
            sandbox="full_access",
            approval_mode="full_access",
        )
    elif str(task.get("origin") or "") == "whatsapp":
        approval = payload or CodexTaskApprovalRequest()
        screen_context = _codex_normalizar_screen_context(approval.screen_context)
        conversation_id = _codex_task_conversation_id(task)
        paths = _codex_resolver_paths_for_session(
            approval.paths,
            sessao,
            conversation_id,
            allow_external_for_admin=False,
        )
        attachment_root = _codex_attachments_base_dir().resolve()
        scoped_paths: list[str] = []
        for path in paths:
            try:
                if os.path.commonpath([str(attachment_root), str(Path(path).resolve())]) == str(attachment_root):
                    continue
            except Exception:
                continue
            scoped_paths.append(path)
        modules = _codex_scope_modules_from_screen(screen_context)
        if not scoped_paths and not modules:
            raise HTTPException(
                status_code=400,
                detail="Tarefa do WhatsApp exige modulo da tela ou caminho explicito antes da aprovacao.",
            )
        scope = _codex_build_scope(
            prompt=str(task.get("prompt") or ""),
            sandbox="workspace_write",
            paths=scoped_paths,
            screen_context=screen_context,
            cwd=str(task.get("cwd") or _codex_base_dir()),
        )
        if not scope.get("allowed_exact") and not scope.get("allowed_prefixes"):
            raise HTTPException(status_code=400, detail="O escopo informado nao gerou nenhum caminho permitido.")
        scope.update(
            {
                "enforced": True,
                "reason": "whatsapp_app_approval",
                "broad_request": False,
            }
        )
        merged_paths = list(task.get("paths") or [])
        for path in scoped_paths:
            if path not in merged_paths:
                merged_paths.append(path)
        _codex_update_task(
            task_id,
            paths=merged_paths,
            screen_context=screen_context,
            scope=scope,
            sandbox="workspace_write",
            approval_mode="request",
        )
    _codex_update_task(
        task_id,
        status="queued",
        approved=True,
        approval_source=source,
        approved_at=_codex_now(),
        approved_by=str(sessao.get("username") or ""),
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Execucao aprovada pelo WhatsApp vinculado." if mobile_approval else "Execucao aprovada pelo administrador.")
    _codex_start_thread(task_id)
    return {"success": True, "task": _codex_public_task(task)}


def codex_aprovar_tarefa(
    task_id: str,
    request: Request,
    payload: Optional[CodexTaskApprovalRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_aprovar_tarefa_para_sessao(task_id, sessao, payload, approval_source="app")


def codex_cancelar_tarefa_para_sessao(
    task_id: str,
    sessao: dict[str, Any],
    *,
    cancel_source: str = "app",
    subject_id: str = "",
):
    task = _codex_require_owned_task(task_id, sessao)
    source = str(cancel_source or "app").strip().lower()
    if source == "whatsapp":
        metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
        expected_subject = str(metadata.get("subject_id") or "").strip()
        if task.get("whatsapp_full_access") is not True or not expected_subject or expected_subject != str(subject_id or "").strip():
            raise HTTPException(status_code=404, detail="Tarefa Codex nao encontrada para este numero.")
    if task.get("status") in {"completed", "partial", "failed", "canceled"}:
        return {"success": True, "task": _codex_public_task(task)}
    task_proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    if task_proposal.get("proposal_id") and task.get("status") == "awaiting_approval":
        codex_actions.reject_proposal(
            str(task_proposal.get("proposal_id") or ""),
            username=str(sessao.get("username") or ""),
            client_id=str(sessao.get("client_id") or "default"),
            source=source,
        )
    running = task.get("status") == "running"
    interrupted = _codex_interrupt_active_turn(task_id) if running else False
    status = "cancel_requested" if running else "canceled"
    _codex_update_task(
        task_id,
        status=status,
        completed_at=_codex_now(),
        cancel_source=source,
        interrupt_requested=bool(interrupted),
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Cancelamento solicitado pelo WhatsApp vinculado." if source == "whatsapp" else "Cancelamento solicitado.")
    return {"success": True, "task": _codex_public_task(task)}


def codex_cancelar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    return codex_cancelar_tarefa_para_sessao(task_id, sessao, cancel_source="app")


def codex_actions_listar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    return codex_actions.list_actions()


def codex_program_functions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    category: str = "",
    limit: int = 300,
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        limit_safe = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit_safe = 300
    return codex_capabilities.list_capabilities(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        category=str(category or ""),
        limit=limit_safe,
    )


def codex_capabilities_listar(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    category: str = "",
    limit: int = 300,
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        limit_safe = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit_safe = 300
    return codex_capabilities.list_capabilities(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        category=str(category or ""),
        limit=limit_safe,
    )


def codex_capabilities_modules(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_capabilities.list_modules(client_id=str(sessao.get("client_id") or "default"))


def codex_capabilities_resolver(
    payload: CodexCapabilityResolveRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_capabilities.resolve_capability(
        client_id=str(sessao.get("client_id") or "default"),
        message=str(payload.message or ""),
        capability_id=str(payload.capability_id or ""),
        module=str(payload.module or ""),
        category=str(payload.category or ""),
        params=payload.params if isinstance(payload.params, dict) else {},
        limit=payload.limit,
    )


def codex_actions_criar_proposta(
    payload: CodexActionProposalRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    message = str(payload.message or "").strip()
    if not message and not payload.action_id and not payload.capability_id:
        raise HTTPException(status_code=400, detail="Informe uma mensagem, action_id ou capability_id.")
    conversation_state = _codex_load_or_create_conversation_state(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or ""),
        channel="app",
    )
    return codex_actions.create_proposal(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        message=message,
        action_id=str(payload.action_id or ""),
        capability_id=str(payload.capability_id or ""),
        params=payload.params if isinstance(payload.params, dict) else {},
        screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
        history=payload.history if isinstance(payload.history, list) else [],
        conversation_id=str(conversation_state.get("conversation_id") or ""),
        conversation_generation=int(conversation_state.get("generation") or 1),
    )


def codex_actions_listar_propostas(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    status: str = "awaiting_approval",
    limit: int = 100,
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.list_proposals(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        status=str(status or ""),
        limit=max(1, min(int(limit or 100), 500)),
    )
def codex_actions_aprovar_proposta(
    proposal_id: str,
    request: Request,
    payload: Optional[CodexActionApprovalRequest] = None,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.approve_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        authorization=authorization,
        source="app",
        proposal_version=payload.proposal_version if payload else None,
        proposal_hash=str(payload.proposal_hash or "") if payload else "",
    )


def codex_actions_revisar_proposta(
    proposal_id: str,
    payload: CodexActionRevisionRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.revise_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        params=payload.params if isinstance(payload.params, dict) else {},
        message=str(payload.message or ""),
        source="app",
    )


def codex_actions_rejeitar_proposta(
    proposal_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.reject_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        source="app",
    )


def codex_actions_obter_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.get_run(run_id, client_id=str(sessao.get("client_id") or "default"))


def codex_actions_cancelar_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.cancel_run(run_id, client_id=str(sessao.get("client_id") or "default"))


def codex_agent_settings_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    guidance = codex_assistant_storage.codex_assistant_agent_guidance_list(
        _codex_base_info_dir(),
        client_id,
        active_only=False,
        latest_only=True,
    )
    return {
        "success": True,
        "states": list(codex_agent_runtime.AGENT_STATES),
        "guidance_scope_types": sorted(codex_agent_runtime.GUIDANCE_SCOPE_TYPES),
        "guidance": guidance,
        "policies": {
            "read_only_autonomous": True,
            "mutations_require_confirmation": True,
            "generic_route_execution": False,
            "whatsapp_non_destructive_only": True,
            "sensitive_actions_app_only": True,
        },
    }


def codex_agent_guidance_put(
    payload: CodexAgentGuidanceRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    try:
        saved = codex_agent_runtime.save_guidance(
            _codex_base_info_dir(),
            str(sessao.get("client_id") or "default"),
            _codex_model_dump(payload),
            updated_by=str(sessao.get("username") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "guidance": saved}


def codex_agent_guidance_simulate(
    payload: CodexAgentGuidanceSimulationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    context = _codex_model_dump(payload)
    resolved = codex_agent_runtime.resolve_guidance(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        context=context,
    )
    return {
        "success": True,
        "context": context,
        "guidance_applied": resolved,
        "prompt_preview": codex_agent_runtime.guidance_prompt(resolved),
    }


def codex_agent_capability_coverage(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    from backend.services import codex_assistant

    actions_payload = codex_actions.list_actions()
    tools = codex_assistant._assistant_tools_public({"full": True})
    return codex_agent_runtime.capability_coverage(
        actions=list(actions_payload.get("actions") or []),
        data_tools=list(tools or []),
    )


def codex_agent_plan_get(
    plan_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    plan = codex_assistant_storage.codex_assistant_agent_plan_get(
        _codex_base_info_dir(),
        str(sessao.get("client_id") or "default"),
        plan_id,
    )
    if not isinstance(plan, dict) or str(plan.get("created_by") or "").strip().lower() != str(sessao.get("username") or "").strip().lower():
        raise HTTPException(status_code=404, detail="Plano do Black Jhon nao encontrado.")
    return {"success": True, "plan": plan}


configure_codex_console_runtime()


__all__ = [
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
]
