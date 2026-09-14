from __future__ import annotations

import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import backend_api  # noqa: F401
from backend.services.codex.console import auth_status as console_auth
from backend.services.codex.console import runtime as console_runtime
from backend.services.codex.console import state as console_state
from backend.services.codex.console import telemetry as console_telemetry


@pytest.fixture(autouse=True)
def reset_auth_status_cache():
    os_environ = {
        "JK_CODEX_ALLOW_API_KEY_AUTH": "0",
        "JK_CODEX_AUTH_STATUS_TIMEOUT_SECONDS": "4",
        "JK_CODEX_AUTH_STATUS_TTL_SECONDS": "45",
    }
    previous = {key: os.environ.get(key) for key in os_environ}
    os.environ.update(os_environ)
    state = console_state.CONSOLE_STATE
    if state.auth_status_inflight:
        state.auth_status_event.wait(6)
    with state.auth_status_lock:
        state.auth_status_cache = None
        state.auth_status_cache_key = ""
        state.auth_status_cache_at = 0.0
        state.auth_status_inflight = False
        state.auth_status_generation = 0
        state.auth_status_event.set()
    yield
    if state.auth_status_inflight:
        state.auth_status_event.wait(6)
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _runtime_file(tmp_path: Path) -> Path:
    runtime = tmp_path / "codex.exe"
    runtime.write_bytes(b"test")
    return runtime


def test_login_status_recognizes_keyring_without_auth_file(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    auth_file = tmp_path / "missing-auth.json"
    observed = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "Logged in using ChatGPT", "")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-probe")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-reach-probe")
    monkeypatch.setattr(console_auth.subprocess, "run", fake_run)

    result = console_auth._codex_auth_status(
        str(runtime), auth_file_path=str(auth_file)
    )

    assert result == {
        "state": "authenticated",
        "authenticated": True,
        "auth_file_detected": False,
        "check_pending": False,
    }
    assert observed["argv"] == [str(runtime), "login", "status"]
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["text"] is True
    assert observed["kwargs"]["check"] is False
    assert observed["kwargs"]["timeout"] == 4
    assert observed["kwargs"]["env"]["OPENAI_API_KEY"] == ""
    assert observed["kwargs"]["env"]["CODEX_ACCESS_TOKEN"] == "must-not-reach-probe"


def test_auth_file_is_a_hint_and_logout_result_is_authoritative(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(
        console_auth.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "Not logged in"),
    )

    result = console_auth._codex_auth_status(
        str(runtime), auth_file_path=str(auth_file)
    )

    assert result["state"] == "unauthenticated"
    assert result["authenticated"] is False
    assert result["auth_file_detected"] is True


def test_login_status_timeout_is_unknown_and_still_allows_execution(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    monkeypatch.setattr(console_runtime, "_codex_runtime_diagnostics", lambda: {
        "path": str(runtime),
        "version": "test",
        "config_ok": True,
        "config_error": "",
    })
    monkeypatch.setattr(console_runtime, "_codex_auth_file_path", lambda: str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        console_auth.subprocess,
        "run",
        lambda argv, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 4)),
    )

    result = console_auth._codex_auth_status(
        str(runtime), auth_file_path=str(tmp_path / "missing.json")
    )

    assert result["state"] == "unknown"
    assert result["authenticated"] is None
    assert console_runtime._codex_auth_detected() is False
    assert console_runtime._codex_auth_allows_attempt() is True


def test_missing_runtime_returns_unknown_without_starting_process(monkeypatch, tmp_path):
    monkeypatch.setattr(
        console_auth.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run"),
    )

    result = console_auth._codex_auth_status(
        str(tmp_path / "missing-codex.exe"),
        auth_file_path=str(tmp_path / "missing-auth.json"),
    )

    assert result["state"] == "unknown"
    assert result["authenticated"] is None


def test_auth_status_cache_expires_after_ttl(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    calls = 0

    def fake_probe(_runtime_path, auth_file_exists):
        nonlocal calls
        calls += 1
        return {
            "state": "authenticated",
            "authenticated": True,
            "auth_file_detected": auth_file_exists,
        }

    monkeypatch.setattr(console_auth, "_codex_auth_probe", fake_probe)
    kwargs = {"auth_file_path": str(tmp_path / "missing-auth.json")}

    assert console_auth._codex_auth_status(str(runtime), **kwargs)["authenticated"] is True
    assert console_auth._codex_auth_status(str(runtime), **kwargs)["authenticated"] is True
    assert calls == 1
    console_state.CONSOLE_STATE.auth_status_cache_at = time.monotonic() - 60
    assert console_auth._codex_auth_status(str(runtime), **kwargs)["authenticated"] is True
    assert calls == 2


def test_expired_positive_cache_stays_ready_while_background_refreshes(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    auth_file = str(tmp_path / "missing-auth.json")
    release = threading.Event()
    calls = 0

    def fake_probe(_runtime_path, auth_file_exists):
        nonlocal calls
        calls += 1
        if calls > 1:
            assert release.wait(2)
        return {
            "state": "authenticated",
            "authenticated": True,
            "auth_file_detected": auth_file_exists,
        }

    monkeypatch.setattr(console_auth, "_codex_auth_probe", fake_probe)
    assert console_auth._codex_auth_status(
        str(runtime), auth_file_path=auth_file
    )["authenticated"] is True
    console_state.CONSOLE_STATE.auth_status_cache_at = time.monotonic() - 60

    stale = console_auth._codex_auth_status(
        str(runtime), auth_file_path=auth_file, background=True
    )

    assert stale["state"] == "authenticated"
    assert stale["authenticated"] is True
    assert stale["check_pending"] is True
    release.set()
    assert console_state.CONSOLE_STATE.auth_status_event.wait(2)


def test_unexpected_nonzero_login_status_is_unknown(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    monkeypatch.setattr(
        console_auth.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 2, "", "credential store temporarily unavailable"
        ),
    )

    result = console_auth._codex_auth_status(
        str(runtime), auth_file_path=str(tmp_path / "missing-auth.json")
    )

    assert result["state"] == "unknown"
    assert result["authenticated"] is None


def test_concurrent_checks_share_one_login_status_process(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    calls = 0
    calls_lock = threading.Lock()
    callers_ready = threading.Barrier(8)
    release_probe = threading.Event()

    def fake_probe(_runtime_path, auth_file_exists):
        nonlocal calls
        with calls_lock:
            calls += 1
        assert release_probe.wait(2)
        return {
            "state": "authenticated",
            "authenticated": True,
            "auth_file_detected": auth_file_exists,
        }

    monkeypatch.setattr(console_auth, "_codex_auth_probe", fake_probe)
    auth_file = str(tmp_path / "missing-auth.json")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                lambda: (
                    callers_ready.wait(),
                    console_auth._codex_auth_status(str(runtime), auth_file_path=auth_file),
                )[1]
            )
            for _index in range(8)
        ]
        time.sleep(0.05)
        release_probe.set()
        results = [future.result() for future in futures]

    assert calls == 1
    assert all(result["authenticated"] is True for result in results)


def test_background_check_is_nonblocking_and_single_flight(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    release = threading.Event()
    started = threading.Event()
    calls = 0

    def fake_probe(_runtime_path, auth_file_exists):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return {
            "state": "authenticated",
            "authenticated": True,
            "auth_file_detected": auth_file_exists,
        }

    monkeypatch.setattr(console_auth, "_codex_auth_probe", fake_probe)
    auth_file = str(tmp_path / "missing-auth.json")

    first = console_auth._codex_auth_status(
        str(runtime), auth_file_path=auth_file, background=True
    )
    assert started.wait(1)
    second = console_auth._codex_auth_status(
        str(runtime), auth_file_path=auth_file, background=True
    )

    assert first["state"] == "checking"
    assert second["state"] == "checking"
    assert calls == 1
    release.set()
    assert console_state.CONSOLE_STATE.auth_status_event.wait(2)
    final = console_auth._codex_auth_status(
        str(runtime), auth_file_path=auth_file, background=True
    )
    assert final["state"] == "authenticated"


def test_background_probe_exception_releases_single_flight(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)
    monkeypatch.setattr(
        console_auth,
        "_codex_auth_probe",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )

    result = console_auth._codex_auth_status(
        str(runtime),
        auth_file_path=str(tmp_path / "missing-auth.json"),
        background=True,
    )

    assert result["state"] == "checking"
    assert console_state.CONSOLE_STATE.auth_status_event.wait(2)
    assert console_state.CONSOLE_STATE.auth_status_inflight is False
    assert console_state.CONSOLE_STATE.auth_status_cache["state"] == "unknown"


def test_background_thread_start_failure_releases_single_flight(monkeypatch, tmp_path):
    runtime = _runtime_file(tmp_path)

    class FailingThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("synthetic start failure")

    monkeypatch.setattr(console_auth.threading, "Thread", FailingThread)

    result = console_auth._codex_auth_status(
        str(runtime),
        auth_file_path=str(tmp_path / "missing-auth.json"),
        background=True,
    )

    assert result["state"] == "unknown"
    assert console_state.CONSOLE_STATE.auth_status_inflight is False
    assert console_state.CONSOLE_STATE.auth_status_event.is_set()


def test_cache_key_changes_with_runtime_and_auth_file_presence(monkeypatch, tmp_path):
    first_runtime = _runtime_file(tmp_path)
    second_runtime = tmp_path / "newer-codex.exe"
    second_runtime.write_bytes(b"test")
    auth_file = tmp_path / "auth.json"
    calls = []

    def fake_probe(runtime_path, auth_file_exists):
        calls.append((runtime_path, auth_file_exists))
        return {
            "state": "authenticated",
            "authenticated": True,
            "auth_file_detected": auth_file_exists,
        }

    monkeypatch.setattr(console_auth, "_codex_auth_probe", fake_probe)

    console_auth._codex_auth_status(str(first_runtime), auth_file_path=str(auth_file))
    console_auth._codex_auth_status(str(second_runtime), auth_file_path=str(auth_file))
    auth_file.write_text("synthetic", encoding="utf-8")
    console_auth._codex_auth_status(str(second_runtime), auth_file_path=str(auth_file))

    assert calls == [
        (str(first_runtime), False),
        (str(second_runtime), False),
        (str(second_runtime), True),
    ]


def test_status_for_session_requests_nonblocking_authentication(monkeypatch):
    observed = {}

    def fake_status_payload(*, background_auth=False):
        observed["background_auth"] = background_auth
        return {"success": True, "defaults": {}}

    monkeypatch.setattr(console_runtime, "_codex_status_payload", fake_status_payload)

    console_runtime._codex_status_for_session({"is_full": False})

    assert observed["background_auth"] is True


def test_telemetry_status_requests_nonblocking_authentication(monkeypatch):
    observed = {}

    def fake_status_payload(*, background_auth=False):
        observed["background_auth"] = background_auth
        return {"success": True}

    monkeypatch.setattr(console_telemetry, "_codex_status_payload", fake_status_payload)

    assert console_telemetry.status_payload()["success"] is True
    assert observed["background_auth"] is True


@pytest.mark.parametrize(
    ("auth_state", "authenticated", "runtime_status", "ready", "required"),
    [
        ("authenticated", True, "ready", True, False),
        ("unauthenticated", False, "authentication_pending", False, True),
        ("unknown", None, "authentication_unknown", False, False),
        ("checking", None, "authentication_checking", False, False),
    ],
)
def test_public_status_maps_authentication_state_without_exposing_probe_output(
    monkeypatch,
    auth_state,
    authenticated,
    runtime_status,
    ready,
    required,
):
    monkeypatch.setattr(console_runtime, "_codex_sdk_installed", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_enabled", lambda: True)
    monkeypatch.setattr(console_runtime, "_codex_cli_version", lambda: (True, "C:/codex.exe"))
    monkeypatch.setattr(console_runtime, "_codex_auth_file_path", lambda: "C:/secret/auth.json")
    monkeypatch.setattr(console_runtime, "_codex_runtime_diagnostics", lambda: {
        "path": "C:/codex.exe",
        "version": "test",
        "config_ok": True,
        "config_error": "",
    })
    monkeypatch.setattr(console_runtime, "_codex_auth_status", lambda *args, **kwargs: {
        "state": auth_state,
        "authenticated": authenticated,
        "auth_file_detected": False,
        "check_pending": auth_state == "checking",
        "stdout": "sensitive output must not escape",
    })

    payload = console_runtime._codex_status_for_session({"is_full": False})

    assert payload["runtime_status"] == runtime_status
    assert payload["ready"] is ready
    assert payload["authentication_required"] is required
    assert payload["authentication_state"] == auth_state
    assert payload["authentication_check_pending"] is (auth_state == "checking")
    assert payload["authenticated"] is authenticated
    assert "stdout" not in payload
    assert "cli_path" not in payload
    assert "auth_file_path" not in payload
