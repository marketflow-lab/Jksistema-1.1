from __future__ import annotations

from pathlib import Path

import pytest

import backend_api  # noqa: F401
from backend.services import codex_console
from backend.services.codex.console import runtime as console_runtime
from backend.services.codex.console import state as console_state


@pytest.fixture(autouse=True)
def reset_runtime_selection_cache():
    state = console_state.CONSOLE_STATE
    state.runtime_bin_cache = None
    state.runtime_selected_path_cache = ""
    state.runtime_selected_version_cache = ()
    state.runtime_config_error_cache = ""
    yield
    state.runtime_bin_cache = None
    state.runtime_selected_path_cache = ""
    state.runtime_selected_version_cache = ()
    state.runtime_config_error_cache = ""


def _prepare_runtime_candidates(monkeypatch, tmp_path: Path):
    local_app_data = tmp_path / "local-app-data"
    desktop_root = local_app_data / "OpenAI" / "Codex" / "bin"
    desktop_older = desktop_root / "old" / "codex.exe"
    desktop_newer = desktop_root / "new" / "codex.exe"
    bundled = tmp_path / "python-sdk" / "codex.exe"
    for candidate in (desktop_older, desktop_newer, bundled):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"test")

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("JK_CODEX_BIN", raising=False)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(console_runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(console_runtime.Path, "home", classmethod(lambda cls: tmp_path / "home"))

    import codex_cli_bin

    monkeypatch.setattr(codex_cli_bin, "bundled_codex_path", lambda: str(bundled))
    versions = {
        str(desktop_older.resolve()).lower(): (0, 199, 0, 0),
        str(desktop_newer.resolve()).lower(): (0, 200, 0, 0),
        str(bundled.resolve()).lower(): (0, 137, 0, 4),
    }
    monkeypatch.setattr(console_runtime, "_codex_bin_version",
        lambda path: versions.get(str(Path(path).resolve()).lower(), ()),
    )
    return desktop_older.resolve(), desktop_newer.resolve(), bundled.resolve()


def test_desktop_runtime_discovery_includes_versioned_installations(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local-app-data"
    direct = local_app_data / "OpenAI" / "Codex" / "bin" / "codex.exe"
    versioned = local_app_data / "OpenAI" / "Codex" / "bin" / "build-id" / "codex.exe"
    for candidate in (direct, versioned):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"test")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    discovered = {path.resolve() for path in console_runtime._codex_desktop_runtime_candidates()}

    assert direct.resolve() in discovered
    assert versioned.resolve() in discovered


def test_runtime_selection_prefers_newest_compatible_desktop_codex(monkeypatch, tmp_path):
    _, desktop_newer, _ = _prepare_runtime_candidates(monkeypatch, tmp_path)
    monkeypatch.setattr(console_runtime, "_codex_bin_config_preflight", lambda path: (True, ""))

    selected = console_runtime._codex_runtime_bin()
    diagnostics = console_runtime._codex_runtime_diagnostics()

    assert selected == str(desktop_newer)
    assert diagnostics["path"] == str(desktop_newer)
    assert diagnostics["version"] == "0.200.0.0"
    assert diagnostics["config_ok"] is True


def test_runtime_selection_skips_newest_when_it_cannot_load_node_repl(monkeypatch, tmp_path):
    desktop_older, desktop_newer, _ = _prepare_runtime_candidates(monkeypatch, tmp_path)

    def preflight(path):
        if Path(path).resolve() == desktop_newer:
            return False, "invalid transport in mcp_servers.node_repl"
        return True, ""

    monkeypatch.setattr(console_runtime, "_codex_bin_config_preflight", preflight)

    selected = console_runtime._codex_runtime_bin()

    assert selected == str(desktop_older)
    assert console_runtime._codex_runtime_diagnostics()["config_ok"] is True


def test_runtime_preflight_returns_actionable_error_when_all_candidates_fail(monkeypatch, tmp_path):
    _prepare_runtime_candidates(monkeypatch, tmp_path)
    monkeypatch.setattr(console_runtime, "_codex_bin_config_preflight",
        lambda path: (False, "invalid transport in mcp_servers.node_repl"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        console_runtime._codex_runtime_require_ready()

    message = str(exc_info.value)
    assert "login continua valido" in message
    assert "mcp_servers.node_repl" in message
    assert "invalid transport" in message


def test_codex_status_uses_detected_desktop_runtime_and_reports_config_failure(monkeypatch):
    monkeypatch.setattr(console_runtime, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_enabled", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_auth_detected", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_auth_file_path", lambda: "auth.json")
    monkeypatch.setattr(console_runtime, "_codex_cli_version", lambda: (False, ""))
    monkeypatch.setattr(console_runtime, "_codex_runtime_diagnostics",
        lambda: {
            "path": "C:/OpenAI/Codex/bin/new/codex.exe",
            "version": "0.200.0.0",
            "config_ok": False,
            "config_error": "invalid transport in mcp_servers.node_repl",
        },
    )

    status = console_runtime._codex_status_payload()

    assert status["cli_available"] is True
    assert status["cli_path"].endswith("codex.exe")
    assert status["runtime_version"] == "0.200.0.0"
    assert status["runtime_config_ok"] is False
    assert status["ready"] is False
    assert "login nao foi perdido" in status["message"]
