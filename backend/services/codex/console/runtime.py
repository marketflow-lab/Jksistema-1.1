"""Codex console runtime component."""

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

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)
from . import bindings


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
    # Cutover definitivo: o modo legado anexava contexto comercial amplo antes
    # de o seletor decidir quais fontes eram realmente necessarias.
    return True





def _codex_base_dir() -> str:
    return bindings.current().base_dir


def _codex_base_info_dir() -> str:
    base_info = bindings.current().info_dir
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
    if not isinstance(task, dict):
        return False
    client_id = _codex_safe_id(str(task.get("client_id") or "default"))
    policy_path = Path(_codex_base_info_dir()) / client_id / "codex_ai" / "mcp_rollout.sqlite3"
    policy = codex_mcp_rollout.MCPRolloutPolicyStore(policy_path).get()
    mode = str(policy.get("mode") or "off")
    if mode in {"off", "shadow"}:
        return False
    origin = str(task.get("origin") or "app").strip().lower()
    if mode == "pilot" and origin != "app":
        return False
    if mode.startswith("whatsapp_") and origin != "whatsapp":
        return False
    subject = f"{task.get('client_id')}:{task.get('conversation_id')}"
    secret = str(os.getenv("JK_CODEX_MCP_ROLLOUT_SECRET") or "")
    if not codex_mcp_rollout.in_cohort(policy, subject=subject, secret=secret):
        return False
    selected = set(_codex_agent_data_selection_tool_ids(_codex_agent_data_selection_from_task(task)))
    allowed = set(policy.get("allowed_tools") or [])
    return bool(selected) and (not allowed or selected.issubset(allowed))





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
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
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
    client_id = str(task.get("client_id") or "").strip()
    from backend.services import jk_codex_mcp_server

    authorized_stores: list[str] = []
    stable_store_refs: list[dict[str, str]] = []
    store_scope_valid = False
    if client_id:
        try:
            from backend.services import integracoes

            configured_stores = integracoes.carregar_lojas(client_id)
            for item in list(configured_stores or [])[:50]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("nome") or item.get("name") or "").strip()[:180]
                integrations = item.get("integracoes") if isinstance(item.get("integracoes"), dict) else {}
                ml = integrations.get("mercadolivre") if isinstance(integrations.get("mercadolivre"), dict) else {}
                seller_id = str(ml.get("user_id") or "").strip()
                site_id = str(ml.get("site_id") or "MLB").strip().upper()
                if name:
                    authorized_stores.append(name)
                if name and seller_id and site_id:
                    stable_store_refs.append(
                        {
                            "store_id": integracoes._integracoes_store_id(client_id, item),
                            "name": name,
                            "seller_id": seller_id,
                            "site_id": site_id,
                        }
                    )
            store_scope_valid = bool(stable_store_refs)
        except Exception:
            authorized_stores = []
            stable_store_refs = []
    selection = _codex_agent_data_selection_from_task(task)
    calls: list[dict[str, Any]] = []
    for raw in list(selection.get("tool_calls") or [])[:8]:
        if not isinstance(raw, dict):
            continue
        arguments = _codex_agent_planned_arguments(raw.get("arguments"))
        tool_id = str(raw.get("tool_id") or "").strip()
        if not tool_id or arguments is None:
            continue
        calls.append(
            {
                "tool_id": tool_id,
                "arguments": arguments,
                "depends_on": list(raw.get("depends_on") or []),
                "required": raw.get("required") is not False,
            }
        )
    if not calls or not stable_store_refs:
        raise RuntimeError("mcp_plan_materialization_incomplete")
    source_policy = _codex_agent_source_policy_from_screen(screen_context)
    client_safe = _codex_safe_id(client_id)
    rollout_path = Path(_codex_base_info_dir()) / client_safe / "codex_ai" / "mcp_rollout.sqlite3"
    rollout = codex_mcp_rollout.MCPRolloutPolicyStore(rollout_path).get()
    idempotency_path = Path(_codex_base_info_dir()) / client_safe / "codex_ai" / "mcp_idempotency.sqlite3"
    issued_at_epoch = int(time.time())
    payload = {
        "version": 2,
        "protocol": "mcp_v2",
        "task_id": str(task.get("task_id") or ""),
        "execution_id": uuid.uuid4().hex,
        "conversation_id": str(task.get("conversation_id") or ""),
        "client_id": client_id,
        "username": str(task.get("created_by") or "whatsapp"),
        "wa_id_hash": _codex_hmac_identifier(metadata.get("wa_id"), namespace="whatsapp_phone"),
        "permissions": task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
        "authorized_stores": authorized_stores,
        "store_scope_valid": store_scope_valid,
        "allowed_tools": sorted({call["tool_id"] for call in calls}),
        "source_policy": source_policy,
        "screen_context": _codex_agent_screen_summary(screen_context),
        "rollout": rollout,
        "rollout_policy_db_path": str(rollout_path.resolve()),
        "idempotency_db_path": str(idempotency_path.resolve()),
        "result_path": str(_codex_native_mcp_result_path(task.get("task_id")).resolve()),
        # O contexto assinado expira por seguranca, mas nao representa um
        # prazo total da tarefa. Cada ferramenta recebe seu proprio timeout.
        "deadline_at_epoch": 0,
        "tool_timeout_seconds": 60,
        "issued_at": issued_at_epoch,
        "expires_at": issued_at_epoch + 15 * 60,
    }
    payload["plan"] = jk_codex_mcp_server.build_plan_v2(
        payload,
        calls=calls,
        stores=stable_store_refs,
    )
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    ).decode("ascii").rstrip("=")
    secret = secrets.token_urlsafe(32)
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    server_path = str(Path(jk_codex_mcp_server.__file__).resolve())
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
    state = CONSOLE_STATE
    with state.runtime_bin_lock:
        version = ".".join(str(part) for part in state.runtime_selected_version_cache)
        return {
            "path": state.runtime_selected_path_cache,
            "version": version,
            "config_ok": not bool(state.runtime_config_error_cache),
            "config_error": state.runtime_config_error_cache,
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
    state = CONSOLE_STATE
    with state.runtime_bin_lock:
        if state.runtime_bin_cache is not None:
            return state.runtime_bin_cache or None

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
            state.runtime_selected_path_cache = str(selected_path)
            state.runtime_selected_version_cache = selected_version
            state.runtime_config_error_cache = ""
            state.runtime_bin_cache = "" if selected_is_bundled else str(selected_path)
        elif ordered:
            fallback_path, fallback_version, fallback_is_bundled = ordered[0]
            state.runtime_selected_path_cache = str(fallback_path)
            state.runtime_selected_version_cache = fallback_version
            state.runtime_config_error_cache = " | ".join(failures)[-1800:]
            state.runtime_bin_cache = "" if fallback_is_bundled else str(fallback_path)
        else:
            state.runtime_selected_path_cache = ""
            state.runtime_selected_version_cache = bundled_version
            state.runtime_config_error_cache = ""
            state.runtime_bin_cache = ""
        return state.runtime_bin_cache or None


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
    try:
        return bindings.current().session_loader(authorization)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Runtime de autenticacao ainda nao configurado.",
        ) from exc


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
    permissoes = bindings.current().permissions_loader(username, client_id)
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
        "execution_plane": CODEX_EXECUTION_PLANE,
        "development_write_enabled": CODEX_DEVELOPMENT_WRITE_ENABLED,
        "development_error_code": CODEX_DEVELOPMENT_ERROR_CODE,
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
            "approval_mode": "read_only",
            "sandbox": "read_only",
            "agent_mode": _codex_agent_mode_enabled(),
        },
        "message": message,
    }





def _codex_status_for_session(sessao: dict[str, Any]) -> dict[str, Any]:
    payload = _codex_status_payload()
    for sensitive_path_field in ("cli_path", "auth_file_path", "cwd"):
        payload.pop(sensitive_path_field, None)
    is_full = bool(sessao.get("is_full"))
    payload["access"] = {
        "mode": "full" if is_full else "read_only",
        "can_mutate": is_full,
        "can_approve": is_full,
        "can_upload": is_full,
        "development_write_enabled": False,
        "typed_commercial_actions_only": True,
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
        shared = _codex_shared_continuity_for_session(sessao)
        state = (
            _codex_load_or_create_shared_conversation_state(client_id, username)
            if shared
            else _codex_load_or_create_conversation_state(client_id, username, channel="app")
        )
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
            "channel": "shared" if shared else "app",
            "generation": generation,
            "state": "active",
            "queue": {
                "running": sum(1 for task in active_tasks if str(task.get("status") or "") in {"running", "cancel_requested"}),
                "pending": sum(1 for task in active_tasks if str(task.get("status") or "") in {"queued", "awaiting_approval"}),
            },
            "can_reset": not active_tasks,
        }
    return payload
__codex_dependencies__ = ['BLACK_JHON_DISPLAY_NAME', 'CODEX_DEFAULT_MODEL', 'CODEX_DEVELOPMENT_ERROR_CODE', 'CODEX_DEVELOPMENT_WRITE_ENABLED', 'CODEX_EXECUTION_PLANE', 'CONSOLE_STATE', '_codex_agent_data_selection_from_task', '_codex_agent_data_selection_tool_ids', '_codex_agent_planned_arguments', '_codex_agent_screen_summary', '_codex_agent_source_policy_from_screen', '_codex_config_file_path', '_codex_hmac_identifier', '_codex_load_or_create_conversation_state', '_codex_load_or_create_shared_conversation_state', '_codex_owned_persisted_tasks', '_codex_safe_id', '_codex_shared_continuity_for_session', '_codex_task_stored_conversation_id']

__codex_exports__ = ['_codex_bool_env', '_codex_int_env', '_codex_enabled', '_codex_agent_mode_enabled', '_codex_base_dir', '_codex_base_info_dir', '_codex_info_dir', '_codex_task_path', '_codex_now', '_codex_deadline_at', '_codex_deadline_epoch', '_codex_native_mcp_enabled', '_codex_native_mcp_result_path', '_codex_native_mcp_read_results', '_codex_native_mcp_cleanup', '_codex_native_mcp_thread_config', '_codex_sdk_installed', '_codex_auth_file_path', '_codex_auth_detected', '_codex_cli_version', '_codex_bin_version', '_codex_desktop_runtime_candidates', '_codex_bin_config_preflight', '_codex_runtime_diagnostics', '_codex_runtime_require_ready', '_codex_runtime_bin', '_codex_local_request_allowed', '_codex_payload_sessao', '_codex_require_authenticated', '_codex_require_full_admin', '_codex_status_payload', '_codex_status_for_session']
