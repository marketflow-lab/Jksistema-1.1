"""Cached, single-flight detection of the local Codex login."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .state import CONSOLE_STATE


def _codex_auth_default_file_path() -> str:
    codex_home = os.getenv("CODEX_HOME")
    if codex_home:
        return os.path.join(os.path.expanduser(codex_home), "auth.json")
    return os.path.join(str(Path.home()), ".codex", "auth.json")


def _codex_auth_runtime_path() -> str:
    runtime = _codex_runtime_diagnostics()
    candidate = str(runtime.get("path") or "").strip()
    if candidate:
        return candidate
    cli_ok, cli_path = _codex_cli_version()
    return str(cli_path or "").strip() if cli_ok else ""


def _codex_auth_probe(runtime_path: str, auth_file_exists: bool) -> dict[str, Any]:
    candidate = str(runtime_path or "").strip()
    if not candidate or not os.path.isfile(candidate):
        return {
            "state": "unknown",
            "authenticated": None,
            "auth_file_detected": auth_file_exists,
        }

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    probe_env = os.environ.copy()
    probe_env.update(_codex_sdk_env())
    try:
        proc = subprocess.run(
            [candidate, "login", "status"],
            capture_output=True,
            text=True,
            timeout=_codex_int_env("JK_CODEX_AUTH_STATUS_TIMEOUT_SECONDS", 4, 1, 15),
            check=False,
            creationflags=creation_flags,
            env=probe_env,
        )
    except Exception:
        return {
            "state": "unknown",
            "authenticated": None,
            "auth_file_detected": auth_file_exists,
        }
    if proc.returncode == 0:
        authenticated: bool | None = True
    else:
        detail = f"{proc.stdout or ''}\n{proc.stderr or ''}".casefold()
        logged_out_markers = ("not logged in", "not authenticated", "no active login")
        authenticated = False if any(marker in detail for marker in logged_out_markers) else None
    return {
        "state": (
            "authenticated"
            if authenticated is True
            else "unauthenticated"
            if authenticated is False
            else "unknown"
        ),
        "authenticated": authenticated,
        "auth_file_detected": auth_file_exists,
    }


def _codex_auth_checking(auth_file_exists: bool) -> dict[str, Any]:
    return {
        "state": "checking",
        "authenticated": None,
        "auth_file_detected": auth_file_exists,
        "check_pending": True,
    }


def _codex_auth_pending_result(result: dict[str, Any]) -> dict[str, Any]:
    pending = dict(result)
    pending["check_pending"] = True
    return pending


def _codex_auth_refresh(
    cache_key: str,
    runtime_path: str,
    auth_file_exists: bool,
    generation: int,
) -> None:
    try:
        result = _codex_auth_probe(runtime_path, auth_file_exists)
    except Exception:
        result = {
            "state": "unknown",
            "authenticated": None,
            "auth_file_detected": auth_file_exists,
        }
    state = CONSOLE_STATE
    with state.auth_status_lock:
        if state.auth_status_generation == generation:
            state.auth_status_cache = dict(result)
            state.auth_status_cache_key = cache_key
            state.auth_status_cache_at = time.monotonic()
            state.auth_status_inflight = False
        state.auth_status_event.set()


def _codex_auth_status(
    runtime_path: str = "",
    *,
    auth_file_path: str = "",
    background: bool = False,
) -> dict[str, Any]:
    auth_file = str(auth_file_path or "").strip() or _codex_auth_default_file_path()
    auth_file_exists = os.path.exists(auth_file)
    candidate = str(runtime_path or "").strip() or _codex_auth_runtime_path()
    if not candidate or not os.path.isfile(candidate):
        return {
            "state": "unknown",
            "authenticated": None,
            "auth_file_detected": auth_file_exists,
        }

    cache_key = "|".join(
        (os.path.normcase(candidate), os.path.normcase(auth_file), str(int(auth_file_exists)))
    )
    ttl_seconds = _codex_int_env("JK_CODEX_AUTH_STATUS_TTL_SECONDS", 45, 1, 300)
    state = CONSOLE_STATE
    while True:
        with state.auth_status_lock:
            cached = state.auth_status_cache
            cached_state = str(cached.get("state") or "") if cached else ""
            cache_ttl = 5 if cached_state in {"unknown", "unauthenticated"} else ttl_seconds
            same_cache_key = cached is not None and state.auth_status_cache_key == cache_key
            if (
                same_cache_key
                and time.monotonic() - state.auth_status_cache_at < cache_ttl
            ):
                fresh = dict(cached)
                fresh["check_pending"] = False
                return fresh
            if state.auth_status_inflight:
                if background:
                    return (
                        _codex_auth_pending_result(cached)
                        if same_cache_key
                        else _codex_auth_checking(auth_file_exists)
                    )
                wait_event = state.auth_status_event
                should_start = False
                generation = state.auth_status_generation
            else:
                state.auth_status_inflight = True
                state.auth_status_generation += 1
                generation = state.auth_status_generation
                state.auth_status_event.clear()
                wait_event = state.auth_status_event
                should_start = True

        if should_start:
            if background:
                worker = threading.Thread(
                    target=_codex_auth_refresh,
                    args=(cache_key, candidate, auth_file_exists, generation),
                    name="jk-codex-auth-status",
                    daemon=True,
                )
                try:
                    worker.start()
                except Exception:
                    with state.auth_status_lock:
                        if state.auth_status_generation == generation:
                            state.auth_status_inflight = False
                        state.auth_status_event.set()
                    return {
                        "state": "unknown",
                        "authenticated": None,
                        "auth_file_detected": auth_file_exists,
                        "check_pending": False,
                    }
                return (
                    _codex_auth_pending_result(cached)
                    if same_cache_key
                    else _codex_auth_checking(auth_file_exists)
                )
            _codex_auth_refresh(cache_key, candidate, auth_file_exists, generation)
            with state.auth_status_lock:
                refreshed = dict(state.auth_status_cache or _codex_auth_checking(auth_file_exists))
                refreshed["check_pending"] = False
                return refreshed

        timeout = _codex_int_env("JK_CODEX_AUTH_STATUS_TIMEOUT_SECONDS", 4, 1, 15) + 1
        if not wait_event.wait(timeout):
            return {
                "state": "unknown",
                "authenticated": None,
                "auth_file_detected": auth_file_exists,
            }


__codex_dependencies__ = [
    "_codex_bool_env",
    "_codex_cli_version",
    "_codex_int_env",
    "_codex_runtime_diagnostics",
    "_codex_sdk_env",
]

__codex_exports__ = ["_codex_auth_status"]
