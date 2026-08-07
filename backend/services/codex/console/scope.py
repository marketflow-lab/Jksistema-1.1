"""Codex console scope component."""

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
        rel = raw.decode("utf-8", errors="strict").replace("\\", "/")
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

def _codex_authorized_reference_roots(
    sessao: dict[str, Any],
    conversation_id: str,
) -> list[Path]:
    roots = [
        _codex_attachment_conversation_dir(
            str(sessao.get("client_id") or "default"),
            str(sessao.get("username") or "user"),
            conversation_id,
        ).resolve(),
    ]
    client_safe = _codex_safe_id(str(sessao.get("client_id") or "default"))
    configured_paths: list[str] = []
    configured_json = str(os.getenv("JK_CODEX_REFERENCE_ROOTS_JSON") or "").strip()
    if configured_json:
        try:
            mapping = json.loads(configured_json)
            values = mapping.get(client_safe) if isinstance(mapping, dict) else []
            if isinstance(values, list):
                configured_paths.extend(str(item or "") for item in values)
        except (TypeError, ValueError, json.JSONDecodeError):
            configured_paths = []
    legacy_roots = str(os.getenv("JK_CODEX_REFERENCE_ROOTS") or "").strip()
    for raw_root in legacy_roots.split(os.pathsep) if legacy_roots else []:
        configured_paths.append(str(Path(raw_root).expanduser() / client_safe))
    for raw in configured_paths:
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser().resolve()
        if candidate.exists() and candidate not in roots:
            roots.append(candidate)
    return roots



def _codex_development_requires_desktop_detail() -> dict[str, Any]:
    return _codex_public_error(
        CODEX_DEVELOPMENT_ERROR_CODE,
        "Alteracoes de codigo devem ser feitas no Codex Desktop, em um Worktree, e aplicadas ao checkout Local somente apos revisao.",
    )



def _codex_path_within_roots(candidate: Path, roots: list[Path]) -> bool:
    resolved = candidate.resolve()
    for root in roots:
        try:
            if os.path.commonpath([str(root), str(resolved)]) == str(root):
                return True
        except (OSError, ValueError):
            continue
    return False



def _codex_resolve_attachment_ids(
    attachment_ids: Optional[list[str]],
    sessao: dict[str, Any],
    conversation_id: str,
) -> tuple[list[str], list[str]]:
    if not attachment_ids:
        return [], []
    root = _codex_attachment_conversation_dir(
        str(sessao.get("client_id") or "default"),
        str(sessao.get("username") or "user"),
        conversation_id,
    ).resolve()
    ids: list[str] = []
    paths: list[str] = []
    for raw in attachment_ids[:CODEX_ATTACHMENT_MAX_COUNT]:
        attachment_id = re.sub(r"[^A-Za-z0-9_-]+", "", str(raw or ""))[:80]
        if not attachment_id:
            continue
        matches = [
            item.resolve()
            for item in root.rglob(f"{attachment_id}_*")
            if item.is_file() and _codex_path_within_roots(item, [root])
        ] if root.exists() else []
        if len(matches) != 1:
            raise HTTPException(
                status_code=404,
                detail=_codex_public_error("ATTACHMENT_NOT_FOUND", "Anexo nao encontrado ou expirado."),
            )
        if attachment_id not in ids:
            ids.append(attachment_id)
            paths.append(str(matches[0]))
    return ids, paths



def _codex_resolve_readonly_references(
    raw_paths: Optional[list[str]],
    sessao: dict[str, Any],
    conversation_id: str,
) -> list[str]:
    if not raw_paths:
        return []
    roots = _codex_authorized_reference_roots(sessao, conversation_id)
    resolved: list[str] = []
    for raw in raw_paths[:CODEX_PATHS_MAX_COUNT]:
        text = str(raw or "").strip().strip('"').strip("'")
        if not text:
            continue
        candidates = [Path(text).expanduser()] if os.path.isabs(text) else [
            Path(_codex_base_dir()) / text,
            *(root / text for root in roots),
        ]
        candidate = next(
            (item.resolve() for item in candidates if item.exists() and _codex_path_within_roots(item, roots)),
            None,
        )
        if candidate is None:
            raise HTTPException(
                status_code=403,
                detail=_codex_public_error(
                    "REFERENCE_PATH_NOT_ALLOWED",
                    "A referencia deve estar em uma raiz de leitura autorizada pelo servidor.",
                ),
            )
        value = str(candidate)
        if value not in resolved:
            resolved.append(value)
    return resolved
__codex_dependencies__ = ['CODEX_API_AUTH_ENV_KEYS', 'CODEX_ATTACHMENT_MAX_COUNT', 'CODEX_DEVELOPMENT_ERROR_CODE', 'CODEX_DUAL_SOL_GATE', 'CODEX_PATHS_MAX_COUNT', 'CODEX_SCOPE_RUNTIME_PREFIXES', 'CODEX_SCOPE_SNAPSHOT_SKIP_DIRS', 'CODEX_SCOPE_TEXT_EXTENSIONS', '_codex_attachment_conversation_dir', '_codex_base_dir', '_codex_bool_env', '_codex_public_error', '_codex_safe_id', '_codex_texto_sem_acentos']

__codex_exports__ = ['_codex_resolver_paths', '_codex_resolver_paths_for_session', '_codex_readonly_cwd_for_session', '_codex_resolver_cwd', '_codex_normalizar_modulo_scope', '_codex_scope_modules_from_screen', '_codex_prompt_pede_escopo_amplo', '_codex_relpath', '_codex_scope_entries_for_module', '_codex_build_scope', '_codex_scope_allows_path', '_codex_scope_instruction', '_codex_git_root', '_codex_git_status_map', '_codex_git_tracked_files', '_codex_scan_text_files', '_codex_file_sha1', '_codex_workspace_snapshot', '_codex_workspace_changed_files', '_codex_scope_violations', '_codex_sandbox_enum', '_codex_sdk_env', '_codex_config_file_path', '_codex_configured_mcp_server_names', '_codex_nonfull_config_overrides', '_codex_external_readonly_config_overrides', '_codex_web_readonly_config_overrides', '_codex_dual_worker_web_search_enabled', '_codex_is_whatsapp_dual_worker', '_codex_configure_dual_sol_limit', '_codex_dual_sol_diagnostics', '_codex_authorized_reference_roots', '_codex_development_requires_desktop_detail', '_codex_path_within_roots', '_codex_resolve_attachment_ids', '_codex_resolve_readonly_references']
