"""Internal Codex console endpoints and task runner."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import tomllib
import unicodedata
import uuid
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import codex_actions, codex_assistant_storage, codex_capabilities, codex_operational_memory
from backend.services.runtime_bridge import bind_runtime_globals


CODEX_SANDBOXES = {"read_only", "workspace_write", "full_access"}
CODEX_TASKS: dict[str, dict[str, Any]] = {}
CODEX_TASKS_LOCK = threading.RLock()
CODEX_FULL_ACCESS_LOCK = threading.Lock()
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


class CodexActionProposalRequest(BaseModel):
    message: str
    action_id: Optional[str] = None
    capability_id: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    conversation_id: Optional[str] = None
    screen_context: Optional[dict[str, Any]] = None
    history: Optional[list[dict[str, Any]]] = None


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
    if not codex_bin:
        return False, ""
    return True, codex_bin


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
    cli_ok, cli_path = _codex_cli_version()
    sdk_ok = _codex_sdk_installed()
    enabled = _codex_enabled()
    auth_file = _codex_auth_file_path()
    auth_file_exists = _codex_auth_detected()
    ready = bool(enabled and sdk_ok)
    message = f"{BLACK_JHON_DISPLAY_NAME} pronto com Codex como IA principal."
    if not enabled:
        message = f"{BLACK_JHON_DISPLAY_NAME} esta com o Codex desabilitado pela configuracao local."
    elif not sdk_ok:
        message = "Instale a dependencia openai-codex no runtime Python."
    elif not auth_file_exists:
        message = "SDK instalado. Se nao houver credencial no keyring, rode codex login nesta maquina."

    return {
        "success": True,
        "enabled": enabled,
        "ready": ready,
        "sdk_installed": sdk_ok,
        "cli_available": cli_ok,
        "cli_path": cli_path,
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


def _codex_public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "sandbox": task.get("sandbox"),
        "cwd": task.get("cwd"),
        "thread_id": task.get("thread_id"),
        "conversation_id": task.get("conversation_id") or task.get("thread_id") or task.get("task_id"),
        "prompt": task.get("prompt"),
        "mutable_intent": bool(task.get("mutable_intent")),
        "model": task.get("model"),
        "approval_mode": task.get("approval_mode"),
        "reasoning_effort": task.get("reasoning_effort"),
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
        "final_response": task.get("final_response") or "",
        "error": task.get("error") or "",
        "message_kind": task.get("message_kind") or "",
        "report_id": task.get("report_id") or "",
        "report_formats": list(task.get("report_formats") or []),
        "logs": list(task.get("logs") or [])[-80:],
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "created_by": task.get("created_by"),
        "client_id": task.get("client_id"),
        "access_mode": task.get("access_mode") or ("full" if task.get("sandbox") != "read_only" else "read_only"),
        "approval_required": bool(task.get("approval_required")),
        "approved": bool(task.get("approved")),
    }


def _codex_task_summary(task: dict[str, Any]) -> dict[str, Any]:
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
        "conversation_id": task.get("conversation_id") or task.get("thread_id") or task.get("task_id"),
        "thread_id": task.get("thread_id") or "",
        "prompt_preview": prompt[:240],
        "response_preview": response[:600],
        "message_kind": task.get("message_kind") or "",
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
        "thread_id": str(thread_id or "").strip(),
        "conversation_id": _codex_safe_id(str(conversation_id or thread_id or report_id).strip()),
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
        "report_id": report_id,
        "report_formats": formats,
        "logs": [{"at": now, "text": f"Relatorio {BLACK_JHON_DISPLAY_NAME} gerado e persistido no historico.", "kind": "report"}],
        "created_at": created_at,
        "started_at": created_at,
        "completed_at": created_at,
        "created_by": str(username or ""),
        "client_id": str(client_id or "default"),
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


def _codex_universal_conversation_id(client_id: str, username: str) -> str:
    raw = f"{str(client_id or 'default').strip().lower()}:{str(username or 'user').strip().lower()}"
    digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]
    return f"universal_{digest}"


def _codex_resolve_new_conversation_id(sessao: dict[str, Any], requested: Any = "") -> str:
    explicit = _codex_safe_id(str(requested or "").strip(), "")
    if explicit:
        return explicit
    return _codex_universal_conversation_id(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
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
    messages: list[dict[str, str]] = []
    prompt = str(task.get("prompt") or "").strip()
    if prompt:
        messages.append({"role": "user", "text": prompt[:2400], "task_id": str(task.get("task_id") or "")})
    final = str(task.get("final_response") or "").strip()
    if not final and task.get("error"):
        final = "Erro: " + str(task.get("error") or "")
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
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    username_norm = str(username or "").strip().lower()
    if _codex_is_conversation_deleted(client_id, username_norm, conversation_id):
        return []
    try:
        paths = sorted(Path(_codex_info_dir()).glob("*.json"), key=lambda item: item.stat().st_mtime)
    except Exception:
        paths = []
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
        if _codex_safe_id(conversation_id) not in _codex_task_conversation_keys(task):
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
    browser_history = _codex_normalizar_history(task.get("history"))
    persisted = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        60,
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
    messages = _codex_recent_persisted_messages(
        client_id,
        username,
        conversation_id,
        str(task.get("task_id") or ""),
        160,
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
        with open(_codex_task_path(task.get("task_id")), "w", encoding="utf-8") as fh:
            json.dump(_codex_public_task(task), fh, ensure_ascii=False, indent=2)
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
            history=[],
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


def _codex_agent_tool_catalog(permissions: Any = None) -> list[dict[str, Any]]:
    try:
        from backend.services import codex_assistant

        tools = codex_assistant._assistant_tools_public(permissions)
    except Exception:
        tools = []
    compact: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
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


def _codex_agent_capability_catalog(client_id: str = "", permissions: Any = None) -> dict[str, Any]:
    permissions = permissions if isinstance(permissions, dict) else {}
    if permissions.get("full") is not True:
        return {
            "version": "permission-filtered",
            "total_capabilities": 0,
            "modules": [],
            "capabilities": [],
        }
    try:
        return codex_capabilities.compact_capability_catalog(client_id=str(client_id or ""), limit=80)
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
) -> str:
    catalog = _codex_agent_tool_catalog(permissions)
    capabilities = _codex_agent_capability_catalog(client_id, permissions)
    screen_summary = _codex_agent_screen_summary(screen_context)
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
    return (
        f"Voce e o {BLACK_JHON_DISPLAY_NAME}, assistente interno unificado do JK Sistema. "
        "O Codex e sua IA principal de raciocinio e execucao.\n"
        "Trabalhe em modo agente: primeiro entenda a pergunta, depois solicite somente as ferramentas read-only necessarias. "
        "Nao invente dados e nao dependa da tela atual quando houver ferramenta de dados mais apropriada.\n\n"
        "Regras de seguranca:\n"
        "- Consultas internas e externas read-only podem ser solicitadas pelo protocolo abaixo.\n"
        "- Acao mutavel nao pode ser executada por ferramenta: editar arquivo, comando, sincronizar, publicar, responder pergunta, alterar banco, Bling ou Mercado Livre exige aprovacao explicita.\n"
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
        "Protocolo de ferramenta:\n"
        "Quando precisar consultar dados, responda somente com um bloco JSON valido neste formato:\n"
        "<jk_tool_calls>\n"
        "[{\"tool_id\":\"sales_ranking\",\"args\":{\"message\":\"pedido original\",\"data_inicio\":\"YYYY-MM-DD\",\"data_fim\":\"YYYY-MM-DD\",\"loja\":\"JK Pecas\",\"limite\":50},\"reason\":\"por que precisa\"}]\n"
        "</jk_tool_calls>\n"
        f"Use no maximo {max_calls} ferramentas por ciclo e no maximo {max_cycles} ciclos. "
        "Depois que receber resultados suficientes, responda normalmente sem o bloco jk_tool_calls.\n\n"
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


def _codex_agent_results_prompt(cycle: int, results: list[dict[str, Any]]) -> str:
    payload = {
        "cycle": cycle,
        "tool_results": results,
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

    for cycle in range(1, max_cycles + 1):
        status = "interpretando pergunta" if cycle == 1 else f"analisando resultados do ciclo {cycle - 1}"
        trace["agent_steps"].append({"cycle": cycle, "status": status, "at": _codex_now()})
        _codex_agent_update_trace(task_id, trace, live_status=status, live_answer="")
        turn = thread.turn(current_prompt, **run_kwargs)
        state: dict[str, Any] = {"items": [], "live_answer": "", "reasoning_summary": "", "live_plan": ""}
        _codex_update_live(task_id, live_status=status, turn_id=turn.id, live_answer="")
        for event in turn.stream():
            _codex_process_stream_event(task_id, task, event, state)

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

        if not calls:
            final_response = response
            final_state = state
            break

        if cycle >= max_cycles:
            final_response = (
                "Nao consegui concluir a resposta dentro do limite de ciclos do agente. "
                "Ferramentas solicitadas: "
                + ", ".join(str(call.get("tool_id") or "") for call in calls)
            )
            final_state = state
            trace["warnings"].append("Limite de ciclos do agente atingido antes da resposta final.")
            break

        cycle_results: list[dict[str, Any]] = []
        for call in calls[:max_calls]:
            tool_id = str(call.get("tool_id") or "").strip()
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
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
            _codex_agent_update_trace(task_id, trace, live_status=f"solicitando ferramenta: {label}", live_answer="")
            try:
                from backend.services import codex_assistant

                result = codex_assistant.codex_assistant_execute_tool_call(
                    client_id=str(task.get("client_id") or ""),
                    tool_id=tool_id,
                    args=args,
                    screen_context=screen_context if isinstance(screen_context, dict) else {},
                    previous_results=previous_results + cycle_results,
                    permissions=task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
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

        current_prompt = _codex_agent_results_prompt(cycle, cycle_results)
        final_state = state
        trace["agent_steps"].append({"cycle": cycle, "status": "gerando proximo passo", "at": _codex_now()})
        _codex_agent_update_trace(task_id, trace, live_status="gerando proximo passo", live_answer="")

    if not final_response:
        final_response = "Nao consegui gerar uma resposta final nesta execucao do agente."
    trace["token_usage"] = final_state.get("token_usage") if isinstance(final_state.get("token_usage"), dict) else trace.get("token_usage") or {}
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
        maybe_update(True, live_status="Turno iniciado no Codex.")
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
        maybe_update(live_status="Raciocinando com resumo disponivel...")
        return

    if method == "item/reasoning/summaryTextDelta":
        delta = str(getattr(payload, "delta", "") or "")
        if delta:
            state["reasoning_summary"] = (state.get("reasoning_summary") or "") + delta
            maybe_update(live_status="Raciocinando...", reasoning_summary=state["reasoning_summary"][-8000:])
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
            maybe_update(live_status=message[:240])
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


def _codex_nonfull_config_overrides() -> tuple[str, ...]:
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
        "features.fast_mode=false",
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


def _codex_run_worker(task_id: str) -> None:
    task = _codex_load_task(task_id)
    if not task:
        return
    sandbox = str(task.get("sandbox") or "read_only")
    acquired_full_lock = False
    try:
        if sandbox == "full_access":
            acquired_full_lock = CODEX_FULL_ACCESS_LOCK.acquire(blocking=False)
            if not acquired_full_lock:
                raise RuntimeError("Ja existe uma tarefa Codex com acesso total em execucao.")

        _codex_update_task(task_id, status="running", started_at=_codex_now(), error="")
        task = _codex_load_task(task_id) or task
        _codex_log(task, f"Iniciando Codex em {sandbox}.")

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
        prompt = str(task.get("prompt") or "").strip()
        cwd = str(task.get("cwd") or _codex_base_dir())
        thread_id = str(task.get("thread_id") or "").strip() if is_full_task else ""
        goal = _codex_clean_text(task.get("goal"), 1200)
        paths = list(task.get("paths") or [])
        if not is_full_task:
            # Recalcula a fronteira no worker; uma tarefa persistida nunca pode
            # elevar cwd, modelo, tier ou recursos alterando seu JSON.
            sandbox = "read_only"
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
                            task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
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
        extra_instructions.append(
            "Quando o usuario pedir relatorio, gere um relatorio em Markdown com titulo, periodo/filtros usados, dados principais, analise e proximas acoes. "
            "Se os dados internos anexados forem insuficientes, declare a lacuna em vez de completar por suposicao."
        )
        if is_full_task:
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

        config_overrides = () if is_full_task else _codex_nonfull_config_overrides()
        with Codex(CodexConfig(env=_codex_sdk_env(), cwd=cwd, config_overrides=config_overrides)) as codex:
            thread_kwargs = {
                "cwd": cwd,
                "model": model,
                "sandbox": sandbox_enum,
                "approval_mode": approval_mode,
                "developer_instructions": developer_instructions,
            }
            if not is_full_task:
                # O perfil de permissions e a fronteira de leitura. Passar
                # sandbox aqui substituiria partes desse perfil.
                thread_kwargs.pop("sandbox", None)
                thread_kwargs["ephemeral"] = True
            if service_tier:
                thread_kwargs["service_tier"] = service_tier
            if thread_id:
                thread = codex.thread_resume(thread_id, **thread_kwargs)
            else:
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
                _codex_update_live(task_id, live_status="Codex iniciou o processamento.", turn_id=turn.id)
                for event in turn.stream():
                    _codex_process_stream_event(task_id, task, event, state)

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
                return

        _codex_update_task(
            task_id,
            status="completed",
            completed_at=_codex_now(),
            final_response=final_response or "Codex concluiu sem resposta final.",
            thread_id=result_thread_id,
            conversation_id=conversation_id,
            live_status="Codex concluiu.",
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
            error="",
        )
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
        _codex_update_task(
            task_id,
            status="failed",
            completed_at=_codex_now(),
            live_status="Codex falhou.",
            error=str(exc),
        )
    finally:
        if acquired_full_lock:
            CODEX_FULL_ACCESS_LOCK.release()


def _codex_start_thread(task_id: str) -> None:
    worker = threading.Thread(target=_codex_run_worker, args=(task_id,), daemon=True)
    worker.start()


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
    conv_id = _codex_safe_id(str(conversation_id or "").strip(), "")
    if not conv_id:
        conv_id = _codex_universal_conversation_id(client_id, username)
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

    return {"success": True, "attachments": saved}


def codex_criar_tarefa(
    payload: CodexTaskRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    _codex_cleanup_old_attachments()
    if not _codex_enabled():
        raise HTTPException(status_code=503, detail="Codex Console desabilitado. Defina JK_CODEX_CONSOLE_ENABLED=true.")
    if not _codex_sdk_installed():
        raise HTTPException(status_code=503, detail="Dependencia openai-codex nao instalada no runtime Python.")

    prompt = str(payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Informe uma mensagem para o Codex.")

    is_full = bool(sessao.get("is_full"))
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
    if not is_full:
        # O cliente nunca escolhe elevar permissao: o servidor rebaixa a tarefa.
        sandbox = "read_only"
        approval_profile = "read_only"
        model = _codex_normalizar_model(None)
        reasoning_effort = _codex_normalizar_reasoning_effort(None)
        speed = _codex_normalizar_speed(None)
        service_tier = _codex_normalizar_service_tier(None, speed)
    conversation_id = _codex_resolve_new_conversation_id(sessao, payload.conversation_id)
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
    history = _codex_normalizar_history(payload.history)
    mutable_intent = _codex_prompt_pede_alteracao(prompt)
    if approval_profile == "request" and sandbox == "workspace_write" and not mutable_intent:
        sandbox = "read_only"
    scope = _codex_build_scope(
        prompt=prompt,
        sandbox=sandbox,
        paths=paths,
        screen_context=screen_context,
        cwd=cwd,
    )
    task_id = uuid.uuid4().hex
    approval_required = bool(
        is_full
        and (sandbox == "full_access" or (approval_profile == "request" and mutable_intent))
    )
    task = {
        "task_id": task_id,
        "status": "awaiting_approval" if approval_required else "queued",
        "sandbox": sandbox,
        "cwd": cwd,
        "thread_id": str(payload.thread_id or "").strip() if is_full else "",
        "conversation_id": conversation_id,
        "prompt": prompt,
        "model": model,
        "approval_mode": approval_profile,
        "reasoning_effort": reasoning_effort,
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
        "mutable_intent": mutable_intent,
        "final_response": "",
        "error": "",
        "logs": [],
        "created_at": _codex_now(),
        "started_at": "",
        "completed_at": "",
        "created_by": sessao["username"],
        "client_id": sessao["client_id"],
        "access_mode": "full" if is_full else "read_only",
        "permissions": {
            str(key): value is True
            for key, value in (sessao.get("permissions") or {}).items()
            if str(key or "").strip()
        },
        "approval_required": approval_required,
        "approved": not approval_required,
    }
    with CODEX_TASKS_LOCK:
        CODEX_TASKS[task_id] = task
        _codex_persist_task(task)
    _codex_log(task, "Tarefa criada.")
    if not is_full:
        _codex_log(task, "Acesso do usuario limitado pelo servidor a leitura e aos modulos autorizados.")
    if approval_profile == "request" and not mutable_intent:
        _codex_log(task, "Modo solicitar aprovacao executado em leitura porque a tarefa nao pediu alteracao de arquivos.")
    if approval_required:
        _codex_log(task, "Aguardando confirmacao para executar com permissao mutavel.")
    else:
        _codex_start_thread(task_id)
    return {"success": True, "task": _codex_public_task(task)}


def codex_listar_tarefas(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    limit: int = 20,
    summary: bool = False,
):
    sessao = _codex_require_authenticated(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    username = str(sessao.get("username") or "").strip().lower()
    max_items = max(1, min(100, int(limit or 20)))
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
                tasks.append(_codex_task_summary(task) if summary else _codex_public_task(task))
                if len(tasks) >= max_items:
                    break
        except Exception:
            continue
    return {"success": True, "tasks": tasks}


def codex_obter_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    return {"success": True, "task": _codex_public_task(task)}


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


def codex_aprovar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    if task.get("status") != "awaiting_approval":
        return {"success": True, "task": _codex_public_task(task)}
    _codex_update_task(task_id, status="queued", approved=True)
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Execucao aprovada pelo administrador.")
    _codex_start_thread(task_id)
    return {"success": True, "task": _codex_public_task(task)}


def codex_cancelar_tarefa(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_authenticated(request, authorization)
    task = _codex_require_owned_task(task_id, sessao)
    if task.get("status") in {"completed", "failed", "canceled"}:
        return {"success": True, "task": _codex_public_task(task)}
    status = "canceled" if task.get("status") != "running" else "cancel_requested"
    _codex_update_task(task_id, status=status, completed_at=_codex_now())
    task = _codex_load_task(task_id) or task
    _codex_log(task, "Cancelamento solicitado.")
    return {"success": True, "task": _codex_public_task(task)}


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
    return codex_actions.create_proposal(
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        message=message,
        action_id=str(payload.action_id or ""),
        capability_id=str(payload.capability_id or ""),
        params=payload.params if isinstance(payload.params, dict) else {},
        screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
        history=payload.history if isinstance(payload.history, list) else [],
    )


def codex_actions_aprovar_proposta(
    proposal_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _codex_require_full_admin(request, authorization)
    return codex_actions.approve_proposal(
        proposal_id,
        username=str(sessao.get("username") or ""),
        client_id=str(sessao.get("client_id") or "default"),
        authorization=authorization,
    )


def codex_actions_obter_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    return codex_actions.get_run(run_id)


def codex_actions_cancelar_execucao(
    run_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _codex_require_full_admin(request, authorization)
    return codex_actions.cancel_run(run_id)


configure_codex_console_runtime()


__all__ = [
    "CodexTaskRequest",
    "CodexActionProposalRequest",
    "CodexCapabilityResolveRequest",
    "configure_codex_console_runtime",
    "codex_status",
    "codex_upload_attachments",
    "codex_criar_tarefa",
    "codex_listar_tarefas",
    "codex_obter_tarefa",
    "codex_deletar_tarefa",
    "codex_aprovar_tarefa",
    "codex_cancelar_tarefa",
    "codex_program_functions",
    "codex_capabilities_listar",
    "codex_capabilities_modules",
    "codex_capabilities_resolver",
    "codex_actions_listar",
    "codex_actions_criar_proposta",
    "codex_actions_aprovar_proposta",
    "codex_actions_obter_execucao",
    "codex_actions_cancelar_execucao",
]
